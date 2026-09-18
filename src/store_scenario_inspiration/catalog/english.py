"""English-only catalog document projection."""

from __future__ import annotations

from dataclasses import replace
import os
import re
import sqlite3
import tempfile
from pathlib import Path

from .build import _remove_identifier_tokens
from .models import ProductFamilyDocument
from .normalize import clean_optional_text, normalize_compare
from .storage import CatalogStore, _fts_text


_CHINESE_TEXT = re.compile(r"[\u3400-\u9fff]+")
_ENGLISH_LETTER = re.compile(r"[A-Za-z]")
_SOURCED_ENGLISH_FALLBACKS = {
    # Full ERP SQLite evidence: cn_names=["黑色镂空蕾丝短裤 L/M/S/XL"], en_aliases=[].
    "SH-CW-2339": ("Lace shorts",),
    # Full ERP SQLite evidence: cn_names=["迷你古代兵器模型挂件"], en_aliases=["1005009609633212"].
    "WATOY320": ("Miniature ancient weapon model pendant",),
}
_ENGLISH_KEYWORD_SCHEMA = """
CREATE VIRTUAL TABLE english_fts USING fts5(
    main_sku UNINDEXED,
    en_aliases
);
"""


def _english_alias(value: str, identifiers: tuple[str, ...]) -> str | None:
    cleaned = clean_optional_text(value)
    if cleaned is None:
        return None
    cleaned = _remove_identifier_tokens(cleaned, identifiers)
    cleaned = _CHINESE_TEXT.sub(" ", cleaned)
    cleaned = clean_optional_text(cleaned)
    if cleaned is None or _ENGLISH_LETTER.search(cleaned) is None:
        return None
    cleaned = cleaned.strip(" ,.;:，。；：、/\\|()[]{}（）【】<>《》\"'“”-_")
    return cleaned


def _english_aliases(document: ProductFamilyDocument) -> tuple[str, ...]:
    identifiers = tuple(
        normalize_compare(value)
        for value in (document.main_sku, *(child.sku for child in document.children))
        if normalize_compare(value)
    )
    aliases: dict[str, str] = {}
    for value in document.en_aliases:
        alias = _english_alias(value, identifiers)
        if alias is None:
            continue
        key = normalize_compare(alias).casefold()
        current = aliases.get(key)
        if current is None or alias < current:
            aliases[key] = alias
    return tuple(aliases[key] for key in sorted(aliases))


def english_document(document: ProductFamilyDocument) -> ProductFamilyDocument:
    aliases = _english_aliases(document)
    if not aliases:
        aliases = _SOURCED_ENGLISH_FALLBACKS.get(document.main_sku, ())
    flags = set(document.quality_flags)
    flags.discard("missing_english")
    if not aliases:
        flags.add("missing_english")
        return replace(
            document,
            searchable=False,
            en_aliases=aliases,
            vector_text_v1="",
            quality_flags=tuple(sorted(flags)),
        )
    return replace(
        document,
        searchable="excluded_main_sku" not in flags,
        en_aliases=aliases,
        vector_text_v1=f"Product name: {'; '.join(aliases)}",
        quality_flags=tuple(sorted(flags)),
    )


def build_english_keyword_index(
    store: CatalogStore,
    target: Path,
    stock_documents: tuple[ProductFamilyDocument, ...],
) -> None:
    """Build an English-only FTS overlay without mutating the source catalog."""

    destination = Path(target)
    if destination.exists():
        raise FileExistsError(f"english keyword index already exists: {destination}")
    if not destination.parent.exists():
        raise FileNotFoundError(f"catalog directory does not exist: {destination.parent}")

    temporary_name: str | None = None
    connection: sqlite3.Connection | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent, delete=False
        ) as temporary:
            temporary_name = temporary.name
        connection = sqlite3.connect(temporary_name)
        with connection:
            connection.executescript(_ENGLISH_KEYWORD_SCHEMA)
            connection.executemany(
                "INSERT INTO english_fts(main_sku, en_aliases) VALUES (?, ?)",
                store.connection.execute("SELECT main_sku, en_aliases FROM product_fts"),
            )
            for document in stock_documents:
                main_sku = normalize_compare(document.main_sku)
                if not main_sku:
                    raise ValueError("document main_sku must contain text")
                connection.execute("DELETE FROM english_fts WHERE main_sku = ?", (main_sku,))
                if document.searchable and document.en_aliases:
                    connection.execute(
                        "INSERT INTO english_fts(main_sku, en_aliases) VALUES (?, ?)",
                        (main_sku, _fts_text(document.en_aliases)),
                    )
        connection.close()
        connection = None
        os.link(temporary_name, destination)
        Path(temporary_name).unlink()
        temporary_name = None
    except Exception:
        if connection is not None:
            connection.close()
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
        raise
