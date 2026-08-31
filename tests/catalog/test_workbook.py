from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from store_scenario_inspiration.catalog.workbook import (
    CatalogSchemaError,
    iter_source_rows,
)


REQUIRED_HEADERS = (
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


def _write_workbook(path: Path, headers: tuple[str, ...]) -> None:
    workbook = Workbook()
    ignored = workbook.active
    ignored.title = "忽略"
    ignored.append(("not", "the", "source"))
    source = workbook.create_sheet("产品列表")
    source.append((*headers, "额外列"))
    source.append(
        (
            "000123",
            "主-001",
            "蓝色40L防水袋",
            "waterproof bag",
            "dry bag",
            "正常销售",
            "运动及娱乐",
            "户外",
            "露营",
            "收纳",
            "防水袋",
            "ignored",
        )
    )
    workbook.save(path)


def test_iter_source_rows_autodiscovers_schema_sheet_and_keeps_skus_as_strings(
    tmp_path: Path,
) -> None:
    source = tmp_path / "catalog.xlsx"
    _write_workbook(source, REQUIRED_HEADERS)

    rows = tuple(iter_source_rows(source))

    assert len(rows) == 1
    assert rows[0].sku == "000123"
    assert rows[0].main_sku == "主-001"
    assert rows[0].product_name == "蓝色40L防水袋"


def test_iter_source_rows_names_the_exact_missing_chinese_header(tmp_path: Path) -> None:
    source = tmp_path / "missing-main-sku.xlsx"
    headers = tuple(header for header in REQUIRED_HEADERS if header != "主SKU")
    _write_workbook(source, headers)

    with pytest.raises(CatalogSchemaError, match="主SKU"):
        tuple(iter_source_rows(source, sheet_name="产品列表"))
