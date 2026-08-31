from __future__ import annotations

from dataclasses import replace

import pytest

from store_scenario_inspiration.catalog.build import build_documents
from store_scenario_inspiration.catalog.models import ProductFamilyDocument, SourceRow
from store_scenario_inspiration.catalog.quality import (
    QualityGateError,
    QualityReport,
    compare_quality,
    validate_build,
)


def _row(
    sku: str,
    main_sku: str,
    product_name: str,
    *,
    category_level_4: str = "户外灯",
) -> SourceRow:
    return SourceRow(
        sku=sku,
        main_sku=main_sku,
        product_name=product_name,
        english_name="",
        english_keywords="",
        sales_status_raw="",
        product_catalog="运动及娱乐",
        category_level_1="户外",
        category_level_2="露营",
        category_level_3="灯具",
        category_level_4=category_level_4,
    )


@pytest.fixture
def rows() -> tuple[SourceRow, ...]:
    return (
        _row("ZXOD3713-A", "ZXOD3713", "户外太阳能灯笼"),
        _row("ZXOD3713-B", "ZXOD3713", "户外太阳能灯笼"),
        _row("ZXOD2149-40", "ZXOD2149", "蓝色40L防水袋", category_level_4="防水袋"),
    )


@pytest.fixture
def documents(rows: tuple[SourceRow, ...]) -> tuple[ProductFamilyDocument, ...]:
    return build_documents(rows)


def test_quality_gate_reconciles_every_child_once(
    rows: tuple[SourceRow, ...], documents: tuple[ProductFamilyDocument, ...]
) -> None:
    report = validate_build(rows, documents)

    assert report.source_row_count == len(rows)
    assert report.child_count == len(rows)
    assert report.document_count == len({row.main_sku for row in rows})
    assert report.errors == ()


def test_duplicate_child_sku_blocks_activation(
    rows: tuple[SourceRow, ...], documents: tuple[ProductFamilyDocument, ...]
) -> None:
    duplicated = (*rows, rows[0])

    with pytest.raises(QualityGateError, match="duplicate child sku"):
        validate_build(duplicated, documents)


def test_duplicate_sku_comparison_normalizes_but_keeps_traceable_raw_values() -> None:
    rows = (
        _row(" ＳＫＵ-1 ", "MAIN-1", "露营灯"),
        _row("SKU-1", "MAIN-1", "露营灯"),
    )

    with pytest.raises(QualityGateError) as raised:
        validate_build(rows, build_documents(rows))

    assert any("duplicate child sku 'SKU-1'" in error for error in raised.value.errors)
    assert " ＳＫＵ-1 " in raised.value.errors[0]
    assert "SKU-1" in raised.value.errors[0]


def test_quality_gate_collects_all_blocking_failures() -> None:
    rows = (_row("SKU-1", "MAIN-1", "灯"), _row("SKU-1", "MAIN-2", "灯"))
    bad_vector_document = ProductFamilyDocument(
        doc_id="main:MAIN-1",
        main_sku="MAIN-1",
        searchable=True,
        cn_names=("灯",),
        en_aliases=(),
        leaf_categories=("户外灯",),
        category_paths=(),
        vector_text_v1="商品名称：MAIN-1 SKU-1",
        children=(),
        quality_flags=(),
    )

    with pytest.raises(QualityGateError) as raised:
        validate_build(rows, (bad_vector_document,))

    assert len(raised.value.errors) >= 3
    assert any("duplicate child sku" in error for error in raised.value.errors)
    assert any("missing child" in error for error in raised.value.errors)
    assert any("vector text contains main sku" in error for error in raised.value.errors)


def test_vector_identifier_check_uses_whole_sku_tokens_not_substrings() -> None:
    rows = (_row("SKU-1", "MAIN-1", "露营灯"),)
    document = ProductFamilyDocument(
        doc_id="main:MAIN-1",
        main_sku="MAIN-1",
        searchable=True,
        cn_names=("露营灯",),
        en_aliases=(),
        leaf_categories=("户外灯",),
        category_paths=(),
        vector_text_v1="商品名称：MAIN-10 SKU-10露营灯",
        children=build_documents(rows)[0].children,
        quality_flags=(),
    )

    assert validate_build(rows, (document,)).errors == ()


def test_unsearchable_documents_are_excluded_from_embedding_requests() -> None:
    rows = (_row("SKU-1", "MAIN-1", "无"), _row("SKU-2", "MAIN-2", "露营灯"))
    report = validate_build(rows, build_documents(rows))

    assert report.searchable_document_count == 1
    assert report.embedding_document_ids == ("main:MAIN-2",)


def test_canonical_document_hash_is_identical_for_same_build(
    rows: tuple[SourceRow, ...]
) -> None:
    first = validate_build(rows, build_documents(rows))
    second = validate_build(rows, build_documents(rows))

    assert first.canonical_document_sha256 == second.canonical_document_sha256
    assert len(first.canonical_document_sha256) == 64


def test_canonical_hash_and_embedding_candidates_ignore_unordered_collection_order() -> None:
    rows = (
        _row("A-2", "A", "露营灯"),
        _row("B-1", "B", "防水袋", category_level_4="防水袋"),
        _row("A-1", "A", "露营灯"),
    )
    built = build_documents(rows)
    document_a, document_b = built
    first_documents = (
        replace(
            document_a,
            cn_names=("乙", "甲"),
            en_aliases=("zulu", "alpha"),
            leaf_categories=("乙类", "甲类"),
            category_paths=(("乙", "二"), ("甲", "一")),
            children=tuple(reversed(document_a.children)),
            quality_flags=("placeholder_english", "mixed_leaf_category"),
        ),
        replace(
            document_b,
            quality_flags=("unsearchable",),
        ),
    )
    reordered_documents = (
        replace(
            first_documents[1],
            quality_flags=tuple(reversed(first_documents[1].quality_flags)),
        ),
        replace(
            first_documents[0],
            cn_names=tuple(reversed(first_documents[0].cn_names)),
            en_aliases=tuple(reversed(first_documents[0].en_aliases)),
            leaf_categories=tuple(reversed(first_documents[0].leaf_categories)),
            category_paths=tuple(reversed(first_documents[0].category_paths)),
            children=tuple(reversed(first_documents[0].children)),
            quality_flags=tuple(reversed(first_documents[0].quality_flags)),
        ),
    )

    first = validate_build(rows, first_documents)
    reordered = validate_build(tuple(reversed(rows)), reordered_documents)

    assert first.canonical_document_sha256 == reordered.canonical_document_sha256
    assert first.embedding_document_ids == reordered.embedding_document_ids == (
        "main:A",
        "main:B",
    )


def test_canonical_hash_changes_when_a_real_document_value_changes() -> None:
    rows = (_row("A-1", "A", "露营灯"),)
    document = build_documents(rows)[0]

    original = validate_build(rows, (document,))
    changed = validate_build(rows, (replace(document, cn_names=("不同商品名",)),))

    assert original.canonical_document_sha256 != changed.canonical_document_sha256


def test_quality_report_includes_required_metrics() -> None:
    rows = (
        _row("SKU-1", "MAIN-1", ""),
        _row("SKU-2", "MAIN-2", "无", category_level_4="收纳"),
        _row("SKU-3", "MAIN-2", "无", category_level_4="户外灯"),
    )
    report = validate_build(rows, build_documents(rows))

    assert report.multi_variant_group_count == 1
    assert report.missing_product_name_count == 1
    assert report.placeholder_product_name_count == 2
    assert report.mixed_category_document_count == 1
    assert report.exact_document_collision_count == 0
    assert report.warnings


def test_compare_quality_handles_no_previous_and_zero_percentage_denominator() -> None:
    empty = QualityReport.empty()
    current = validate_build((_row("SKU-1", "MAIN-1", "露营灯"),), build_documents((_row("SKU-1", "MAIN-1", "露营灯"),)))

    first = compare_quality(None, current)
    assert first.previous is None
    assert first.metrics["document_count"].absolute_delta is None
    assert first.metrics["document_count"].percentage_delta is None

    delta = compare_quality(empty, current)
    assert delta.metrics["document_count"].absolute_delta == 1
    assert delta.metrics["document_count"].percentage_delta is None


def test_large_catalog_delta_is_reported_not_blocked() -> None:
    previous = validate_build((_row("SKU-1", "MAIN-1", "露营灯"),), build_documents((_row("SKU-1", "MAIN-1", "露营灯"),)))
    rows = tuple(_row(f"SKU-{index}", f"MAIN-{index}", "露营灯") for index in range(100))
    current = validate_build(rows, build_documents(rows))

    delta = compare_quality(previous, current)
    assert delta.metrics["document_count"].absolute_delta == 99
    assert delta.metrics["document_count"].percentage_delta == 9900.0
