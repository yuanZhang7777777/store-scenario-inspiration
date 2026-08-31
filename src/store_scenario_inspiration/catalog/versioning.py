"""Versioned, staged catalog builds with atomic activation and rollback."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import asdict, fields
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from .build import build_documents
from .models import BuildManifest
from .quality import QualityDelta, QualityReport, compare_quality, validate_build
from .storage import CatalogStore
from .vectors import EmbeddingProvider, ExactVectorIndex, build_vector_matrix
from .workbook import iter_source_rows


DOCUMENT_SCHEMA_VERSION = "1"
CLEANING_RULES_VERSION = "1"
NO_EMBEDDING_MODEL_ID = "none"

_VERSION_ID = re.compile(r"\A\d{8}T\d{6}Z-[0-9a-f]{12}\Z")
_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")
_MANIFEST_FIELDS = frozenset(field.name for field in fields(BuildManifest))


class CatalogIndexError(ValueError):
    """Raised when catalog version state is missing, malformed, or unsafe."""


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_identity(
    source_sha256: str,
    schema_version: str,
    cleaning_rules_version: str,
    embedding_model_id: str,
) -> str:
    payload = {
        "cleaning_rules_version": cleaning_rules_version,
        "embedding_model_id": embedding_model_id,
        "schema_version": schema_version,
        "source_sha256": source_sha256,
    }
    encoded = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _write_json_fsync(path: Path, payload: object) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _fsync_file(path: Path) -> None:
    # Windows rejects ``fsync`` on a read-only descriptor even though no bytes
    # are changed, so request a writable handle solely for durable flushing.
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())


def _quality_payload(report: QualityReport) -> dict[str, object]:
    return asdict(report)


def _delta_payload(delta: QualityDelta) -> dict[str, object]:
    return {
        "metrics": {
            name: asdict(metric) for name, metric in sorted(delta.metrics.items())
        }
    }


def _quality_from_payload(payload: object, *, path: Path) -> QualityReport:
    if not isinstance(payload, dict):
        raise CatalogIndexError(f"quality report must be a JSON object: {path}")
    expected = frozenset(field.name for field in fields(QualityReport))
    if frozenset(payload) != expected:
        raise CatalogIndexError(f"quality report has invalid fields: {path}")
    converted = dict(payload)
    for name in ("embedding_document_ids", "warnings", "errors"):
        value = converted[name]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise CatalogIndexError(f"quality report field {name!r} is invalid: {path}")
        converted[name] = tuple(value)
    try:
        return QualityReport(**converted)
    except TypeError as error:
        raise CatalogIndexError(f"quality report is invalid: {path}") from error


class CatalogIndexManager:
    """Build and activate immutable catalog versions under one index root."""

    def __init__(self, index_root: Path) -> None:
        self.index_root = Path(index_root)
        self.last_rebuild_skipped = False

    @property
    def versions_dir(self) -> Path:
        return self.index_root / "versions"

    def _pointer_path(self, name: str) -> Path:
        return self.index_root / f"{name}.json"

    def _version_path(self, version_id: str) -> Path:
        if not isinstance(version_id, str) or _VERSION_ID.fullmatch(version_id) is None:
            raise CatalogIndexError(f"invalid catalog version ID: {version_id!r}")
        return self.versions_dir / version_id

    def _read_pointer(self, name: str, *, required: bool) -> str | None:
        path = self._pointer_path(name)
        if not path.exists():
            if required:
                raise CatalogIndexError(f"catalog {name} pointer does not exist")
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise CatalogIndexError(f"catalog {name} pointer is invalid: {path}") from error
        if not isinstance(payload, dict) or frozenset(payload) != {"version_id"}:
            raise CatalogIndexError(f"catalog {name} pointer has invalid fields: {path}")
        version_id = payload["version_id"]
        self._version_path(version_id)
        return version_id

    def _read_manifest(self, version_id: str) -> BuildManifest:
        path = self._version_path(version_id) / "manifest.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise CatalogIndexError(f"catalog manifest does not exist: {path}") from error
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise CatalogIndexError(f"catalog manifest is invalid: {path}") from error
        if not isinstance(payload, dict) or frozenset(payload) != _MANIFEST_FIELDS:
            raise CatalogIndexError(f"catalog manifest has invalid fields: {path}")
        try:
            manifest = BuildManifest(**payload)
        except TypeError as error:
            raise CatalogIndexError(f"catalog manifest is invalid: {path}") from error
        string_fields = (
            "version_id",
            "source_sha256",
            "schema_version",
            "cleaning_rules_version",
            "embedding_model_id",
            "built_at",
            "vector_status",
        )
        if any(not isinstance(getattr(manifest, name), str) for name in string_fields):
            raise CatalogIndexError(f"catalog manifest contains invalid values: {path}")
        if manifest.version_id != version_id or _SHA256.fullmatch(manifest.source_sha256) is None:
            raise CatalogIndexError(f"catalog manifest identity is invalid: {path}")
        if not all(
            (manifest.schema_version, manifest.cleaning_rules_version, manifest.embedding_model_id)
        ):
            raise CatalogIndexError(f"catalog manifest identity is incomplete: {path}")
        if (
            not isinstance(manifest.document_count, int)
            or isinstance(manifest.document_count, bool)
            or manifest.document_count < 0
            or manifest.vector_status not in {"absent", "present"}
        ):
            raise CatalogIndexError(f"catalog manifest contains invalid values: {path}")
        identity = _build_identity(
            manifest.source_sha256,
            manifest.schema_version,
            manifest.cleaning_rules_version,
            manifest.embedding_model_id,
        )
        if not manifest.version_id.endswith(f"-{identity[:12]}"):
            raise CatalogIndexError(f"catalog manifest build identity is invalid: {path}")
        version_path = self._version_path(version_id)
        matrix_exists = (version_path / "vectors.npy").is_file()
        rows_exist = (version_path / "vector-rows.json").is_file()
        artifacts_match = (
            not matrix_exists and not rows_exist
            if manifest.vector_status == "absent"
            else matrix_exists and rows_exist
        )
        if not artifacts_match:
            raise CatalogIndexError(
                f"catalog vector artifacts do not match manifest status: {path}"
            )
        return manifest

    def _read_quality(self, version_id: str) -> QualityReport:
        path = self._version_path(version_id) / "quality.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise CatalogIndexError(f"catalog quality report does not exist: {path}") from error
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise CatalogIndexError(f"catalog quality report is invalid: {path}") from error
        return _quality_from_payload(payload, path=path)

    def _write_pointer(self, name: str, version_id: str) -> None:
        self._version_path(version_id)
        self.index_root.mkdir(parents=True, exist_ok=True)
        target = self._pointer_path(name)
        temporary = self.index_root / f"{name}.json.tmp"
        try:
            _write_json_fsync(temporary, {"version_id": version_id})
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def active_manifest(self) -> BuildManifest | None:
        """Return the strictly validated active manifest, or ``None`` if unset."""

        version_id = self._read_pointer("active", required=False)
        return None if version_id is None else self._read_manifest(version_id)

    def active_store_path(self) -> Path | None:
        """Return the active SQLite path after validating pointer and manifest."""

        manifest = self.active_manifest()
        if manifest is None:
            return None
        path = self._version_path(manifest.version_id) / "catalog.sqlite3"
        if not path.is_file():
            raise CatalogIndexError(f"active catalog store does not exist: {path}")
        return path

    def active_quality(self) -> QualityReport | None:
        """Return the active version's quality report for operator output."""

        manifest = self.active_manifest()
        return None if manifest is None else self._read_quality(manifest.version_id)

    def _model_id(self, provider: EmbeddingProvider | None) -> str:
        if provider is None:
            return NO_EMBEDDING_MODEL_ID
        model_id = provider.model_id
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("provider model_id must contain text")
        if model_id != model_id.strip():
            raise ValueError("provider model_id must not contain surrounding whitespace")
        if model_id.casefold() == NO_EMBEDDING_MODEL_ID:
            raise ValueError("provider model_id must not use reserved value 'none'")
        return model_id

    def _next_version(self, identity: str) -> tuple[str, datetime]:
        moment = _utc_now()
        while True:
            version_id = f"{moment.strftime('%Y%m%dT%H%M%SZ')}-{identity[:12]}"
            if not self._version_path(version_id).exists():
                return version_id, moment
            moment += timedelta(seconds=1)

    @staticmethod
    def _same_build(
        manifest: BuildManifest,
        *,
        source_sha256: str,
        schema_version: str,
        cleaning_rules_version: str,
        embedding_model_id: str,
    ) -> bool:
        return (
            manifest.source_sha256 == source_sha256
            and manifest.schema_version == schema_version
            and manifest.cleaning_rules_version == cleaning_rules_version
            and manifest.embedding_model_id == embedding_model_id
        )

    def rebuild(
        self,
        source: Path,
        sheet_name: str | None,
        provider: EmbeddingProvider | None,
    ) -> BuildManifest:
        """Build completely in staging and activate only after every check passes."""

        source_path = Path(source)
        model_id = self._model_id(provider)
        source_sha256 = _sha256_file(source_path)
        active = self.active_manifest()
        if active is not None and self._same_build(
            active,
            source_sha256=source_sha256,
            schema_version=DOCUMENT_SCHEMA_VERSION,
            cleaning_rules_version=CLEANING_RULES_VERSION,
            embedding_model_id=model_id,
        ):
            self.last_rebuild_skipped = True
            return active

        self.last_rebuild_skipped = False
        identity = _build_identity(
            source_sha256,
            DOCUMENT_SCHEMA_VERSION,
            CLEANING_RULES_VERSION,
            model_id,
        )
        version_id, built_at = self._next_version(identity)
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        staging = self.versions_dir / f".staging-{uuid4()}"
        staging.mkdir()
        renamed = False
        try:
            rows = tuple(iter_source_rows(source_path, sheet_name))
            documents = build_documents(rows)
            quality = validate_build(rows, documents)
            previous_quality = None if active is None else self._read_quality(active.version_id)
            delta = compare_quality(previous_quality, quality)
            manifest = BuildManifest(
                version_id=version_id,
                source_sha256=source_sha256,
                schema_version=DOCUMENT_SCHEMA_VERSION,
                cleaning_rules_version=CLEANING_RULES_VERSION,
                embedding_model_id=model_id,
                built_at=built_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                document_count=len(documents),
                vector_status="absent" if provider is None else "present",
            )

            with CatalogStore.create(staging / "catalog.sqlite3", documents, manifest):
                pass
            _fsync_file(staging / "catalog.sqlite3")

            if provider is not None:
                artifact = build_vector_matrix(
                    documents, provider, self.index_root / "embedding-cache"
                )
                matrix_path = staging / "vectors.npy"
                rows_path = staging / "vector-rows.json"
                shutil.copyfile(artifact.matrix_path, matrix_path)
                shutil.copyfile(artifact.rows_path, rows_path)
                _fsync_file(matrix_path)
                _fsync_file(rows_path)
                ExactVectorIndex.load(matrix_path, rows_path)

            _write_json_fsync(staging / "manifest.json", asdict(manifest))
            _write_json_fsync(staging / "quality.json", _quality_payload(quality))
            _write_json_fsync(staging / "delta.json", _delta_payload(delta))

            if _sha256_file(source_path) != source_sha256:
                raise CatalogIndexError("source workbook changed during catalog rebuild")

            final = self._version_path(version_id)
            staging.rename(final)
            renamed = True
            if active is not None:
                self._write_pointer("previous", active.version_id)
            self._write_pointer("active", version_id)
            return manifest
        finally:
            if not renamed:
                shutil.rmtree(staging, ignore_errors=True)

    def rollback(self) -> BuildManifest:
        """Atomically swap active and previous version pointers."""

        current_id = self._read_pointer("active", required=True)
        previous_id = self._read_pointer("previous", required=True)
        assert current_id is not None and previous_id is not None
        if current_id == previous_id:
            raise CatalogIndexError("catalog active and previous versions are identical")
        current = self._read_manifest(current_id)
        previous = self._read_manifest(previous_id)
        self._write_pointer("previous", current.version_id)
        try:
            self._write_pointer("active", previous.version_id)
        except Exception:
            try:
                self._write_pointer("previous", previous.version_id)
            except Exception:
                pass
            raise
        return previous
