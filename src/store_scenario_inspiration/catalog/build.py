"""Build immutable main-SKU retrieval documents from ERP source rows."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
import re

from .models import ChildVariant, ProductFamilyDocument, SourceRow
from .normalize import (
    _truncate_operational_text,
    clean_optional_text,
    make_vector_text,
    normalize_compare,
)


def _sorted_unique_display(values: Iterable[str]) -> tuple[str, ...]:
    displays: dict[str, str] = {}
    for value in values:
        key = clean_optional_text(value)
        if key is None:
            continue
        current = displays.get(key)
        if current is None or value < current:
            displays[key] = value
    return tuple(
        displays[key]
        for key in sorted(displays, key=lambda value: (value.casefold(), value))
    )


def _unique_paths_in_order(
    values: Iterable[tuple[str, ...]],
) -> tuple[tuple[str, ...], ...]:
    displays: dict[tuple[str, ...], tuple[str, ...]] = {}
    for value in values:
        key = tuple(normalize_compare(part) for part in value)
        current = displays.get(key)
        if current is None or value < current:
            displays[key] = value
    return tuple(displays.values())


def _category_path(row: SourceRow) -> tuple[str, ...]:
    return tuple(
        value
        for value in (
            row.category_level_1,
            row.category_level_2,
            row.category_level_3,
            row.category_level_4,
        )
        if clean_optional_text(value) is not None
    )


def _leaf_category(row: SourceRow) -> str | None:
    for value in (
        row.category_level_4,
        row.category_level_3,
        row.category_level_2,
        row.category_level_1,
        row.product_catalog,
    ):
        if clean_optional_text(value) is not None:
            return value
    return None


def _has_placeholder_english(row: SourceRow) -> bool:
    return any(
        normalize_compare(value) and clean_optional_text(value) is None
        for value in (row.english_name, row.english_keywords)
    )


def _has_operational_text(row: SourceRow) -> bool:
    name = clean_optional_text(row.product_name)
    return name is not None and _truncate_operational_text(name) != name


def _validate_row(row: SourceRow) -> tuple[str, str]:
    sku = normalize_compare(row.sku)
    main_sku = normalize_compare(row.main_sku)
    if not sku or not main_sku:
        raise ValueError("each source row requires non-empty sku and main_sku")
    return sku, main_sku


def _remove_identifier_tokens(value: str, identifiers: tuple[str, ...]) -> str:
    """Remove this family's known metadata IDs from an indexed source name.

    The untouched raw value remains on ``ChildVariant.display_name``.  Limiting
    removal to exact known IDs avoids guessing that every model-like token is a
    SKU while still enforcing the metadata/semantic boundary.
    """

    cleaned = clean_optional_text(value)
    if cleaned is None:
        return value
    sanitized = cleaned
    for identifier in sorted(set(identifiers), key=lambda item: (-len(item), item)):
        pattern = rf"(?<![A-Za-z0-9_-]){re.escape(identifier)}(?![A-Za-z0-9_-])"
        sanitized = re.sub(pattern, " ", sanitized, flags=re.IGNORECASE)
    if sanitized == cleaned:
        return value
    sanitized = " ".join(sanitized.split()).strip(
        " ,.;:，。；：、/\\|()[]{}（）【】<>《》\"'“”-_"
    )
    return sanitized


def _build_document(main_sku: str, rows: tuple[SourceRow, ...]) -> ProductFamilyDocument:
    identifiers = tuple(
        sorted(
            {
                main_sku,
                *(normalize_compare(row.sku) for row in rows),
            }
        )
    )
    indexed_names = tuple(
        _remove_identifier_tokens(row.product_name, identifiers) for row in rows
    )
    cn_names = _sorted_unique_display(indexed_names)
    english_aliases = _sorted_unique_display(
        value
        for row in rows
        for value in (row.english_name, row.english_keywords)
    )
    leaf_categories = _sorted_unique_display(
        leaf for row in rows if (leaf := _leaf_category(row)) is not None
    )
    category_paths = _unique_paths_in_order(
        path for row in rows if (path := _category_path(row))
    )
    vector_text = make_vector_text(indexed_names)
    flags: set[str] = set()
    if len(leaf_categories) > 1:
        flags.add("mixed_leaf_category")
    if any(_has_placeholder_english(row) for row in rows):
        flags.add("placeholder_english")
    if any(_has_operational_text(row) for row in rows):
        flags.add("operational_text")
    if not vector_text:
        flags.add("unsearchable")

    children = tuple(
        ChildVariant(
            sku=row.sku,
            display_name=row.product_name,
            sales_status_raw=row.sales_status_raw,
            status_flags=(),
        )
        for row in rows
    )
    return ProductFamilyDocument(
        doc_id=f"main:{main_sku}",
        main_sku=main_sku,
        searchable=bool(vector_text),
        cn_names=cn_names,
        en_aliases=english_aliases,
        leaf_categories=leaf_categories,
        category_paths=category_paths,
        vector_text_v1=vector_text,
        children=children,
        quality_flags=tuple(sorted(flags)),
    )


def build_documents(rows: Iterable[SourceRow]) -> tuple[ProductFamilyDocument, ...]:
    """Group validated source rows into one deterministic document per main SKU."""

    grouped: dict[str, list[SourceRow]] = defaultdict(list)
    for row in rows:
        _, main_sku = _validate_row(row)
        grouped[main_sku].append(row)
    return tuple(
        _build_document(main_sku, tuple(grouped[main_sku]))
        for main_sku in sorted(grouped)
    )
