"""Static SQLite catalog storage with a safe, tokenized FTS5 keyword index.

SQLite's ``bm25`` returns lower (normally negative) values for better matches.
``keyword_search`` negates that value, so its public scores are stable values
where larger is better. Equal scores are ordered by normalized main SKU.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import asdict
from pathlib import Path

from .models import BuildManifest, ChildVariant, ProductFamilyDocument, SearchHit
from .normalize import keyword_tokens, normalize_compare


_SCHEMA = """
CREATE TABLE documents (
    main_sku TEXT PRIMARY KEY,
    document_json TEXT NOT NULL
);
CREATE TABLE children (
    main_sku TEXT NOT NULL REFERENCES documents(main_sku),
    sku TEXT NOT NULL,
    display_name TEXT NOT NULL,
    sales_status_raw TEXT NOT NULL,
    status_flags_json TEXT NOT NULL,
    PRIMARY KEY (main_sku, sku)
);
CREATE TABLE build_meta (
    meta_key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);
CREATE VIRTUAL TABLE product_fts USING fts5(
    main_sku UNINDEXED,
    cn_names,
    categories,
    en_aliases
);
"""
_CJK_SEGMENT = re.compile(r"[\u4e00-\u9fff]+")
_CJK_ADJACENT_ALPHANUMERIC = re.compile(
    r"(?<=[\u4e00-\u9fff])([A-Za-z0-9]+)|([A-Za-z0-9]+)(?=[\u4e00-\u9fff])"
)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _document_from_json(value: str) -> ProductFamilyDocument:
    payload = json.loads(value)
    fields = payload["keyword_fields"]
    return ProductFamilyDocument(
        doc_id=payload["doc_id"],
        main_sku=payload["main_sku"],
        searchable=payload["searchable"],
        cn_names=tuple(fields["cn_names"]),
        en_aliases=tuple(fields["en_aliases"]),
        leaf_categories=tuple(fields["leaf_categories"]),
        category_paths=tuple(tuple(path) for path in fields["category_paths"]),
        vector_text_v1=payload["vector_text_v1"],
        children=tuple(
            ChildVariant(
                sku=child["sku"],
                display_name=child["display_name"],
                sales_status_raw=child["sales_status_raw"],
                status_flags=tuple(child["status_flags"]),
            )
            for child in payload["children"]
        ),
        quality_flags=tuple(payload["quality"]["flags"]),
    )


def _manifest_from_json(value: str) -> BuildManifest:
    return BuildManifest(**json.loads(value))


def _index_tokens(values: Iterable[str]) -> tuple[str, ...]:
    """Add CJK help plus alphanumerics fused directly to CJK text."""

    native_terms = {
        segment for value in values for segment in _CJK_SEGMENT.findall(value)
    }
    tokens: list[str] = []
    seen: set[str] = set()
    for value in values:
        for segment in _CJK_SEGMENT.findall(value):
            for token in (*segment, *(segment[index : index + 2] for index in range(len(segment) - 1))):
                if token not in seen and token not in native_terms:
                    seen.add(token)
                    tokens.append(token)
        for match in _CJK_ADJACENT_ALPHANUMERIC.finditer(value):
            token = match.group(1) or match.group(2)
            if token not in seen:
                seen.add(token)
                tokens.append(token)
    return tuple(tokens)


def _fts_text(values: Iterable[str]) -> str:
    """Store normalized originals plus CJK-only index assistance.

    Unicode61 already tokenizes English and alphanumeric text, so duplicating
    those terms would distort FTS term frequency. Chinese needs explicit
    unigrams and bigrams for the query-side ``keyword_tokens`` contract.
    """

    originals: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_compare(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            originals.append(normalized)
    return " ".join((*originals, *_index_tokens(originals)))


class CatalogStore:
    """Own one SQLite connection; callers must close it or use a context manager."""

    def __init__(self, connection: sqlite3.Connection, manifest: BuildManifest) -> None:
        self._connection: sqlite3.Connection | None = connection
        self.manifest = manifest

    @property
    def connection(self) -> sqlite3.Connection:
        """Expose the live connection for low-level diagnostics only."""

        return self._require_connection()

    def __enter__(self) -> CatalogStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Release the owned SQLite connection; repeated close calls are harmless."""

        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("catalog store is closed")
        return self._connection

    @classmethod
    def create(
        cls,
        path: Path,
        documents: Sequence[ProductFamilyDocument],
        manifest: BuildManifest,
    ) -> CatalogStore:
        """Create a complete new catalog atomically without replacing an existing file."""

        target = Path(path)
        if manifest.document_count != len(documents):
            raise ValueError("manifest document_count must equal the documents length")
        if target.exists():
            raise FileExistsError(f"catalog already exists: {target}")
        if not target.parent.exists():
            raise FileNotFoundError(f"catalog directory does not exist: {target.parent}")

        temporary_name: str | None = None
        connection: sqlite3.Connection | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=f".{target.name}.", suffix=".tmp", dir=target.parent, delete=False
            ) as temporary:
                temporary_name = temporary.name
            connection = sqlite3.connect(temporary_name)
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                connection.executescript(_SCHEMA)
                connection.execute(
                    "INSERT INTO build_meta(meta_key, value_json) VALUES (?, ?)",
                    ("manifest", _json(asdict(manifest))),
                )
                for document in documents:
                    main_sku = normalize_compare(document.main_sku)
                    if not main_sku:
                        raise ValueError("document main_sku must contain text")
                    connection.execute(
                        "INSERT INTO documents(main_sku, document_json) VALUES (?, ?)",
                        (main_sku, _json(document.to_index_dict())),
                    )
                    connection.executemany(
                        """INSERT INTO children(
                            main_sku, sku, display_name, sales_status_raw, status_flags_json
                        ) VALUES (?, ?, ?, ?, ?)""",
                        (
                            (
                                main_sku,
                                child.sku,
                                child.display_name,
                                child.sales_status_raw,
                                _json(list(child.status_flags)),
                            )
                            for child in document.children
                        ),
                    )
                    if document.searchable:
                        categories = (
                            *document.leaf_categories,
                            *(part for path in document.category_paths for part in path),
                        )
                        connection.execute(
                            """INSERT INTO product_fts(
                                main_sku, cn_names, categories, en_aliases
                            ) VALUES (?, ?, ?, ?)""",
                            (
                                main_sku,
                                _fts_text(document.cn_names),
                                _fts_text(categories),
                                _fts_text(document.en_aliases),
                            ),
                        )
            connection.close()
            connection = None
            # Link the complete file into place with an exclusive target name.
            # Unlike replace(), this cannot clobber a catalog another writer
            # created between the initial existence check and publication.
            os.link(temporary_name, target)
            Path(temporary_name).unlink()
            temporary_name = None
            return cls.open_readonly(target)
        except Exception:
            if connection is not None:
                connection.close()
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)
            raise

    @classmethod
    def open_readonly(cls, path: Path) -> CatalogStore:
        """Open a catalog through SQLite's real read-only URI mode."""

        target = Path(path).resolve()
        connection = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)
        row = connection.execute(
            "SELECT value_json FROM build_meta WHERE meta_key = ?", ("manifest",)
        ).fetchone()
        if row is None:
            connection.close()
            raise ValueError("catalog is missing its build manifest")
        return cls(connection, _manifest_from_json(row[0]))

    def get_document(self, main_sku: str) -> ProductFamilyDocument | None:
        """Fetch one family using the same normalized identity as construction."""

        identity = normalize_compare(main_sku)
        if not identity:
            return None
        row = self._require_connection().execute(
            "SELECT document_json FROM documents WHERE main_sku = ?", (identity,)
        ).fetchone()
        return None if row is None else _document_from_json(row[0])

    def keyword_search(self, query: str, limit: int) -> tuple[SearchHit, ...]:
        """Search sanitized tokens only; raw user text is never FTS syntax."""

        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        tokens = keyword_tokens(query)
        if not tokens:
            raise ValueError("query must contain at least one searchable token")
        # Every token is separately quoted. OR keeps partial query evidence useful
        # while eliminating FTS operators, phrases, columns, and punctuation syntax.
        match_query = " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)
        rows = self._require_connection().execute(
            """SELECT main_sku, -bm25(product_fts, 0.0, 8.0, 4.0, 1.0) AS score
               FROM product_fts
               WHERE product_fts MATCH ?
               ORDER BY score DESC, main_sku ASC
               LIMIT ?""",
            (match_query, limit),
        ).fetchall()
        return tuple(
            SearchHit(main_sku=row[0], score=float(row[1]), sources=("keyword",))
            for row in rows
        )
