import sqlite3

from openpyxl import Workbook

from store_scenario_inspiration.catalog.pilot import (
    build_fts,
    child_main_map,
    filter_country,
    fuse_rankings,
    keyword_search,
    load_country_stock,
    load_inventory_context,
)


def test_bilingual_fts_rrf_and_country_filter_keep_global_audit() -> None:
    products = {
        "A": {"main_sku": "A", "standard_name_cn": "露营折叠椅", "standard_name_en": "Camping Chair"},
        "B": {"main_sku": "B", "standard_name_cn": "户外折叠桌", "standard_name_en": "Outdoor Folding Table"},
    }
    connection = build_fts(products)
    try:
        assert keyword_search(connection, "cn", "露营椅", 5)[0][0] == "A"
        assert keyword_search(connection, "en", "folding table", 5)[0][0] == "B"
    finally:
        connection.close()

    global_candidates = fuse_rankings(
        products,
        [("cn_vector", "露营", [("A", 0.9), ("B", 0.8)]),
         ("en_keyword", "folding table", [("B", 1.0)])],
        10,
    )
    country_candidates = filter_country(global_candidates, {"B": 3.0}, 10)
    assert {row["main_sku"] for row in global_candidates} == {"A", "B"}
    assert [row["main_sku"] for row in country_candidates] == ["B"]
    assert next(row for row in global_candidates if row["main_sku"] == "A")["country_available"] is False

    weighted = fuse_rankings(
        products,
        [("cn_vector", "主词", [("A", 0.9)], 1.0),
         ("cn_vector", "扩写", [("B", 0.9)], 0.7)],
        10,
    )
    assert [row["main_sku"] for row in weighted] == ["A", "B"]


def test_country_stock_only_uses_asset_child_mapping(tmp_path) -> None:
    products = {
        "A": {"inventory_metadata_json": '{"child_skus":["A-1"]}'},
        "B": {"inventory_metadata_json": '{"child_skus":["B-1"]}'},
    }
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "汇总表格"
    sheet.append(["子SKU", "主SKU", "国家", "库存中心库存", "公共池库存"])
    sheet.append(["A-1", "A", "菲律宾", 2, 3])
    sheet.append(["A-X", "A", "菲律宾", 100, 100])
    sheet.append(["B-1", "B", "泰国", 7, 8])
    path = tmp_path / "stock.xlsx"
    workbook.save(path)

    allowed = child_main_map(products)
    assert load_country_stock(path, "PH", allowed) == {"A": 5.0}
    assert load_inventory_context(path, "PH", allowed) == ("PH", "available", {"A": 5.0})


def test_uncovered_country_keeps_inventory_unknown_without_reading_stock(tmp_path) -> None:
    country, coverage, stock = load_inventory_context(tmp_path / "missing.xlsx", "BR", {})
    assert (country, coverage, stock) == ("BR", "unavailable", None)

    candidates = [{"main_sku": "A"}]
    assert filter_country(candidates, stock, 10) == []
    assert candidates == [{
        "main_sku": "A", "country_available": None, "country_available_quantity": None,
    }]
