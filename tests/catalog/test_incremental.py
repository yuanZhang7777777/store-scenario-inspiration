from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from openpyxl import Workbook

from store_scenario_inspiration.catalog.incremental import (
    apply_name_reviews,
    catalog_revision,
    import_products,
    initialize_daily_catalog,
    pending_name_reviews,
)
from store_scenario_inspiration.catalog.models import BuildManifest, SourceRow
from store_scenario_inspiration.catalog.build import build_documents
from store_scenario_inspiration.catalog.storage import CatalogStore


HEADERS = (
    "sku",
    "主SKU",
    "商品名称",
    "英文名称",
    "英文关键字",
    "销售状态",
    "商品目录",
    "商品一级目录",
    "商品二级目录",
    "商品三级目录",
    "商品四级目录",
)


def _row(sku: str, main_sku: str, name: str, en: str = "camp lamp") -> SourceRow:
    return SourceRow(
        sku=sku,
        main_sku=main_sku,
        product_name=name,
        english_name=en,
        english_keywords=en,
        sales_status_raw="正常",
        product_catalog="目录",
        category_level_1="户外",
        category_level_2="露营",
        category_level_3="照明",
        category_level_4="营地灯",
    )


def _manifest(count: int) -> BuildManifest:
    return BuildManifest(
        version_id="baseline-v1",
        source_sha256="a" * 64,
        schema_version="1",
        cleaning_rules_version="1",
        embedding_model_id="test-model",
        built_at="2026-09-16T00:00:00Z",
        document_count=count,
        vector_status="pending",
    )


def _catalog(path: Path) -> None:
    documents = build_documents(
        (
            _row("a1", "A", "旧中文名一", "old camp lamp"),
            _row("a2", "A", "旧中文名二", "old camp lamp"),
            _row("x1", "1A0000", "排除商品", "excluded"),
        )
    )
    with CatalogStore.create(path, documents, _manifest(len(documents))):
        pass


def _workbook(path: Path, rows: tuple[SourceRow, ...]) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "产品列表"
    worksheet.append(HEADERS)
    for row in rows:
        worksheet.append(
            (
                row.sku,
                row.main_sku,
                row.product_name,
                row.english_name,
                row.english_keywords,
                row.sales_status_raw,
                row.product_catalog,
                row.category_level_1,
                row.category_level_2,
                row.category_level_3,
                row.category_level_4,
            )
        )
    workbook.save(path)


def _document(path: Path, main_sku: str) -> dict:
    connection = sqlite3.connect(path)
    try:
        (payload,) = connection.execute(
            "SELECT document_json FROM documents WHERE main_sku = ?", (main_sku,)
        ).fetchone()
    finally:
        connection.close()
    import json

    return json.loads(payload)


def test_incremental_import_preserves_baseline_names_retains_absent_children_and_reviews(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "baseline.sqlite3"
    daily = tmp_path / "daily.sqlite3"
    first = tmp_path / "first.xlsx"
    conflict = tmp_path / "conflict.xlsx"
    _catalog(baseline)
    _workbook(first, (_row("a1", "A", "新中文名一", "new camp lamp"), _row("b1", "B", "新增商品", "new b")))

    initialize_daily_catalog(baseline, daily)
    report = import_products(daily, first, apply=True)

    assert report["revision"] == 1
    assert report["existing_children"] == 1
    assert report["new_children"] == 1
    assert report["retained_children"] == 1
    assert report["pending_name_mains"] == 2
    a_doc = _document(daily, "A")
    assert a_doc["keyword_fields"]["cn_names"] == ["旧中文名一", "旧中文名二"]
    assert [child["sku"] for child in a_doc["children"]] == ["a1", "a2"]
    assert a_doc["children"][0]["display_name"] == "新中文名一"
    assert a_doc["children"][1]["display_name"] == "旧中文名二"

    replay = import_products(daily, first, apply=True)
    assert replay["revision"] == 1
    assert replay["pending_name_mains"] == 2

    pending = pending_name_reviews(daily)
    assert [item["main_sku"] for item in pending] == ["A", "B"]
    applied = apply_name_reviews(
        daily,
        [
            {
                "main_sku": "A",
                "evidence_hash": pending[0]["evidence_hash"],
                "cn_name": "审核中文名",
                "en_name": "reviewed camp lamp",
                "aliases": ["reviewed lantern"],
                "reason": "manual review",
            },
            {
                "main_sku": "B",
                "evidence_hash": pending[1]["evidence_hash"],
                "cn_name": "新增审核名",
                "en_name": "reviewed new product",
                "aliases": [],
                "reason": "manual review",
            },
        ],
    )
    assert applied["revision"] == 2

    unchanged = import_products(daily, first, apply=True)
    assert unchanged["revision"] == 2
    assert pending_name_reviews(daily) == []
    assert _document(daily, "A")["keyword_fields"]["cn_names"] == ["审核中文名"]

    _workbook(conflict, (_row("a1", "B", "冲突归属", "bad"),))
    with pytest.raises(ValueError, match="reparent"):
        import_products(daily, conflict, apply=True)
    assert catalog_revision(daily) == 2

    # Later source evidence can repair a previously unnamed family.
    empty_baseline, empty_daily = tmp_path / "empty.sqlite3", tmp_path / "repaired.sqlite3"
    with CatalogStore.create(empty_baseline, build_documents((_row("c1", "C", "", ""),)), _manifest(1)):
        pass
    initialize_daily_catalog(empty_baseline, empty_daily)
    _workbook(first, (_row("c2", "C", "露营灯", "Camping lantern"),))
    import_products(empty_daily, first, apply=True)
    repair = pending_name_reviews(empty_daily)[0]
    apply_name_reviews(empty_daily, [{"main_sku": "C", "evidence_hash": repair["evidence_hash"],
                                     "cn_name": "露营灯", "en_name": "Camping lantern", "aliases": []}])
    assert _document(empty_daily, "C")["searchable"] is True
    with CatalogStore.open_readonly(empty_daily) as store:
        assert store.keyword_search("Camping lantern", 10, english_only=True)[0].main_sku == "C"
