"""Build immutable main-SKU retrieval documents from ERP source rows."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from .models import ChildVariant, ProductFamilyDocument, SourceRow
from .normalize import (
    _truncate_operational_text,
    clean_optional_text,
    make_vector_text,
    normalize_compare,
)


def _sorted_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values), key=lambda value: (value.casefold(), value)))


def _unique_in_order(values: Iterable[tuple[str, ...]]) -> tuple[tuple[str, ...], ...]:
    return tuple(dict.fromkeys(values))


def _category_path(row: SourceRow) -> tuple[str, ...]:
    return tuple(
        value
        for value in (
            clean_optional_text(row.category_level_1),
            clean_optional_text(row.category_level_2),
            clean_optional_text(row.category_level_3),
            clean_optional_text(row.category_level_4),
        )
        if value is not None
    )


def _leaf_category(row: SourceRow) -> str | None:
    for value in (
        row.category_level_4,
        row.category_level_3,
        row.category_level_2,
        row.category_level_1,
        row.product_catalog,
    ):
        cleaned = clean_optional_text(value)
        if cleaned is not None:
            return cleaned
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


def _build_document(main_sku: str, rows: tuple[SourceRow, ...]) -> ProductFamilyDocument:
    names = tuple(
        name
        for row in rows
        if (name := clean_optional_text(row.product_name)) is not None
    )
    cn_names = _sorted_unique(names)
    english_aliases = _sorted_unique(
        alias
        for row in rows
        for value in (row.english_name, row.english_keywords)
        if (alias := clean_optional_text(value)) is not None
    )
    leaf_categories = _sorted_unique(
        leaf for row in rows if (leaf := _leaf_category(row)) is not None
    )
    category_paths = _unique_in_order(
        path for row in rows if (path := _category_path(row))
    )
    vector_text = make_vector_text(
        clean_optional_text(row.product_name) or "" for row in rows
    )
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
            sku=normalize_compare(row.sku),
            display_name=normalize_compare(row.product_name),
            sales_status_raw=normalize_compare(row.sales_status_raw),
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
