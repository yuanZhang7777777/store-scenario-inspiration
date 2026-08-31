"""Versioned, staged catalog builds with atomic activation and rollback."""

from __future__ import annotations

import json
import os
import re
import shutil
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
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
_QUALITY_COUNT_FIELDS = (
    "source_row_count",
    "child_count",
    "document_count",
    "searchable_document_count",
    "multi_variant_group_count",
    "missing_product_name_count",
    "placeholder_product_name_count",
    "mixed_category_document_count",
    "exact_document_collision_count",
)


class CatalogIndexError(ValueError):
    """Raised when catalog version state is missing, malformed, or unsafe."""


@dataclass(frozen=True)
class _ActiveState:
    version_id: str
    previous_version_id: str | None


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

    def _read_active_state(self, *, required: bool) -> _ActiveState | None:
        path = self._pointer_path("active")
        if not path.exists():
            if required:
                raise CatalogIndexError("catalog active pointer does not exist")
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise CatalogIndexError(f"catalog active pointer is invalid: {path}") from error
        if not isinstance(payload, dict) or frozenset(payload) != {
            "version_id",
            "previous_version_id",
        }:
            raise CatalogIndexError(f"catalog active pointer has invalid fields: {path}")
        version_id = payload["version_id"]
        self._version_path(version_id)
        previous_version_id = payload["previous_version_id"]
        if previous_version_id is not None:
            self._version_path(previous_version_id)
        return _ActiveState(version_id, previous_version_id)

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
        return manifest

    def _read_quality(self, version_id: str) -> QualityReport:
        return self._read_quality_at(self._version_path(version_id))

    @staticmethod
    def _read_quality_at(directory: Path) -> QualityReport:
        path = directory / "quality.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise CatalogIndexError(f"catalog quality report does not exist: {path}") from error
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise CatalogIndexError(f"catalog quality report is invalid: {path}") from error
        return _quality_from_payload(payload, path=path)

    @staticmethod
    def _artifact_names(manifest: BuildManifest) -> frozenset[str]:
        names = {"catalog.sqlite3", "manifest.json", "quality.json", "delta.json"}
        if manifest.vector_status == "present":
            names.update(("vectors.npy", "vector-rows.json"))
        return frozenset(names)

    def _write_artifacts(self, directory: Path, manifest: BuildManifest) -> None:
        names = self._artifact_names(manifest)
        _write_json_fsync(
            directory / "artifacts.json",
            {name: _sha256_file(directory / name) for name in sorted(names)},
        )

    @staticmethod
    def _validate_delta(directory: Path) -> None:
        path = directory / "delta.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise CatalogIndexError(f"catalog delta report is invalid: {path}") from error
        if not isinstance(payload, dict) or frozenset(payload) != {"metrics"}:
            raise CatalogIndexError(f"catalog delta report is invalid: {path}")
        metrics = payload["metrics"]
        if not isinstance(metrics, dict) or frozenset(metrics) != frozenset(_QUALITY_COUNT_FIELDS):
            raise CatalogIndexError(f"catalog delta report is invalid: {path}")
        for metric in metrics.values():
            if not isinstance(metric, dict) or frozenset(metric) != {
                "absolute_delta",
                "percentage_delta",
            }:
                raise CatalogIndexError(f"catalog delta report is invalid: {path}")
            absolute = metric["absolute_delta"]
            percentage = metric["percentage_delta"]
            if absolute is not None and (
                not isinstance(absolute, int) or isinstance(absolute, bool)
            ):
                raise CatalogIndexError(f"catalog delta report is invalid: {path}")
            if percentage is not None and (
                not isinstance(percentage, (int, float))
                or isinstance(percentage, bool)
                or not float("-inf") < float(percentage) < float("inf")
            ):
                raise CatalogIndexError(f"catalog delta report is invalid: {path}")

    def _validate_artifacts(self, directory: Path, manifest: BuildManifest) -> QualityReport:
        artifact_path = directory / "artifacts.json"
        expected = self._artifact_names(manifest)
        try:
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise CatalogIndexError(f"catalog artifacts do not exist: {artifact_path}") from error
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise CatalogIndexError(f"catalog artifacts are invalid: {artifact_path}") from error
        if not isinstance(payload, dict) or frozenset(payload) != expected:
            raise CatalogIndexError(f"catalog artifacts have invalid fields: {artifact_path}")
        if any(not isinstance(value, str) or _SHA256.fullmatch(value) is None for value in payload.values()):
            raise CatalogIndexError(f"catalog artifacts contain invalid hashes: {artifact_path}")
        expected_files = set(expected) | {"artifacts.json"}
        actual_files = {path.name for path in directory.iterdir() if path.is_file()}
        if (
            {"vectors.npy", "vector-rows.json"} & (actual_files - expected_files)
            or (
                manifest.vector_status == "present"
                and not {"vectors.npy", "vector-rows.json"} <= actual_files
            )
        ):
            raise CatalogIndexError(
                f"catalog vector artifacts do not match manifest status: {directory}"
            )
        if actual_files != expected_files:
            raise CatalogIndexError(f"catalog version files are incomplete or unexpected: {directory}")
        for name in expected:
            if _sha256_file(directory / name) != payload[name]:
                raise CatalogIndexError(f"catalog artifact hash mismatch: {directory / name}")

        self._validate_delta(directory)
        quality = self._read_quality_at(directory)
        if any(
            not isinstance(getattr(quality, name), int)
            or isinstance(getattr(quality, name), bool)
            or getattr(quality, name) < 0
            for name in _QUALITY_COUNT_FIELDS
        ) or _SHA256.fullmatch(quality.canonical_document_sha256) is None:
            raise CatalogIndexError(f"catalog quality report contains invalid values: {directory / 'quality.json'}")
        if quality.errors:
            raise CatalogIndexError(f"catalog quality report contains errors: {directory / 'quality.json'}")
        if manifest.document_count != quality.document_count:
            raise CatalogIndexError("catalog manifest document count does not match quality report")

        try:
            with CatalogStore.open_readonly(directory / "catalog.sqlite3") as store:
                if store.manifest != manifest:
                    raise CatalogIndexError("catalog SQLite manifest does not match external manifest")
                document_count = store.connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
                child_count = store.connection.execute("SELECT COUNT(*) FROM children").fetchone()[0]
                fts_count = store.connection.execute("SELECT COUNT(*) FROM product_fts").fetchone()[0]
        except CatalogIndexError:
            raise
        except Exception as error:
            raise CatalogIndexError(f"catalog SQLite validation failed: {directory / 'catalog.sqlite3'}") from error
        if document_count != manifest.document_count or document_count != quality.document_count:
            raise CatalogIndexError("catalog document count does not match manifest or quality report")
        if child_count != quality.child_count:
            raise CatalogIndexError("catalog child count does not match quality report")
        if fts_count != quality.searchable_document_count:
            raise CatalogIndexError("catalog FTS count does not match quality report")

        if manifest.vector_status == "present":
            try:
                index = ExactVectorIndex.load(directory / "vectors.npy", directory / "vector-rows.json")
            except Exception as error:
                raise CatalogIndexError(f"catalog vector artifacts are invalid: {directory}") from error
            if tuple(f"main:{row}" for row in index._rows) != quality.embedding_document_ids:
                raise CatalogIndexError("catalog vector rows do not match quality embedding IDs")
        return quality

    def _validate_version(self, version_id: str) -> tuple[BuildManifest, QualityReport]:
        manifest = self._read_manifest(version_id)
        quality = self._validate_artifacts(self._version_path(version_id), manifest)
        return manifest, quality

    @contextmanager
    def _writer_lock(self):
        """Acquire the root-local cross-process writer lock without blocking readers."""

        self.index_root.mkdir(parents=True, exist_ok=True)
        path = self.index_root / ".writer.lock"
        stream = path.open("a+b")
        try:
            if path.stat().st_size == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            try:
                stream.close()
            except OSError:
                pass
            raise CatalogIndexError(f"catalog writer lock is already held: {path}") from error
        try:
            yield
        finally:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            finally:
                try:
                    stream.close()
                except OSError:
                    pass

    def _write_derived_previous(self, version_id: str | None) -> None:
        """Publish the compatibility pointer before authoritative state."""

        target = self._pointer_path("previous")
        if version_id is None:
            target.unlink(missing_ok=True)
            return
        self._version_path(version_id)
        self.index_root.mkdir(parents=True, exist_ok=True)
        temporary = self.index_root / f".previous.json.{uuid4()}.tmp"
        try:
            _write_json_fsync(temporary, {"version_id": version_id})
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def _write_active_state(self, state: _ActiveState) -> None:
        self._version_path(state.version_id)
        if state.previous_version_id is not None:
            self._version_path(state.previous_version_id)
        self.index_root.mkdir(parents=True, exist_ok=True)
        target = self._pointer_path("active")
        temporary = self.index_root / f".active.json.{uuid4()}.tmp"
        try:
            _write_json_fsync(
                temporary,
                {
                    "version_id": state.version_id,
                    "previous_version_id": state.previous_version_id,
                },
            )
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def _commit_state(self, state: _ActiveState) -> None:
        self._write_derived_previous(state.previous_version_id)
        self._write_active_state(state)

    def active_manifest(self) -> BuildManifest | None:
        """Return the strictly validated active manifest, or ``None`` if unset."""

        state = self._read_active_state(required=False)
        return None if state is None else self._validate_version(state.version_id)[0]

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

        state = self._read_active_state(required=False)
        return None if state is None else self._validate_version(state.version_id)[1]

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

        self.last_rebuild_skipped = False
        model_id = self._model_id(provider)
        with self._writer_lock():
            return self._rebuild_locked(source, sheet_name, provider, model_id)

    def _rebuild_locked(
        self,
        source: Path,
        sheet_name: str | None,
        provider: EmbeddingProvider | None,
        model_id: str,
    ) -> BuildManifest:

        source_path = Path(source)
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

            self._write_artifacts(staging, manifest)
            final = self._version_path(version_id)
            self._validate_artifacts(staging, manifest)
            staging.rename(final)
            renamed = True
            self._commit_state(
                _ActiveState(
                    version_id=version_id,
                    previous_version_id=None if active is None else active.version_id,
                )
            )
            return manifest
        finally:
            if not renamed:
                shutil.rmtree(staging, ignore_errors=True)

    def rollback(self) -> BuildManifest:
        """Atomically swap active and previous version pointers."""

        with self._writer_lock():
            return self._rollback_locked()

    def _rollback_locked(self) -> BuildManifest:

        state = self._read_active_state(required=True)
        assert state is not None
        current_id = state.version_id
        previous_id = state.previous_version_id
        if previous_id is None:
            raise CatalogIndexError("catalog previous pointer does not exist")
        if current_id == previous_id:
            raise CatalogIndexError("catalog active and previous versions are identical")
        current, _ = self._validate_version(current_id)
        previous, _ = self._validate_version(previous_id)
        self._commit_state(
            _ActiveState(
                version_id=previous.version_id,
                previous_version_id=current.version_id,
            )
        )
        return previous
