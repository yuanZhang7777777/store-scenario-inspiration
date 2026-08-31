from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from store_scenario_inspiration.catalog.workbook import (
    CatalogSchemaError,
    iter_source_rows,
)
import store_scenario_inspiration.catalog.workbook as workbook_module


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


def test_iter_source_rows_preserves_raw_cell_strings_for_display_and_traceability(
    tmp_path: Path,
) -> None:
    source = tmp_path / "raw-values.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "产品列表"
    worksheet.append(REQUIRED_HEADERS)
    worksheet.append(
        (
            " ＳＫＵ-001 ",
            " ＭＡＩＮ-001 ",
            "　全角　 名称  ",
            " English  Name ",
            " Keyword ",
            "　状态  原样 ",
            "目录",
            "一级",
            "二级",
            "三级",
            "四级",
        )
    )
    workbook.save(source)

    row = next(iter_source_rows(source))

    assert row.sku == " ＳＫＵ-001 "
    assert row.main_sku == " ＭＡＩＮ-001 "
    assert row.product_name == "　全角　 名称  "
    assert row.sales_status_raw == "　状态  原样 "


def test_iter_source_rows_accepts_an_explicit_valid_sheet(tmp_path: Path) -> None:
    source = tmp_path / "explicit-sheet.xlsx"
    _write_workbook(source, REQUIRED_HEADERS)

    rows = tuple(iter_source_rows(source, sheet_name="产品列表"))

    assert [row.sku for row in rows] == ["000123"]


def test_iter_source_rows_names_a_missing_explicit_sheet(tmp_path: Path) -> None:
    source = tmp_path / "unknown-sheet.xlsx"
    _write_workbook(source, REQUIRED_HEADERS)

    with pytest.raises(CatalogSchemaError, match="不存在"):
        tuple(iter_source_rows(source, sheet_name="不存在"))


def test_iter_source_rows_lists_sheets_when_auto_discovery_finds_none(tmp_path: Path) -> None:
    source = tmp_path / "no-schema-sheet.xlsx"
    workbook = Workbook()
    workbook.active.title = "无效"
    workbook.active.append(("sku", "商品名称"))
    workbook.save(source)

    with pytest.raises(CatalogSchemaError, match="无效"):
        tuple(iter_source_rows(source))


def test_iter_source_rows_lists_matching_sheets_when_auto_discovery_is_ambiguous(
    tmp_path: Path,
) -> None:
    source = tmp_path / "ambiguous-schema-sheet.xlsx"
    workbook = Workbook()
    first = workbook.active
    first.title = "候选一"
    first.append(REQUIRED_HEADERS)
    second = workbook.create_sheet("候选二")
    second.append(REQUIRED_HEADERS)
    workbook.save(source)

    with pytest.raises(CatalogSchemaError, match="候选一.*候选二"):
        tuple(iter_source_rows(source))


def test_iter_source_rows_closes_the_workbook_when_the_generator_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeWorksheet:
        title = "产品列表"

        def iter_rows(self, *, min_row: int, max_row: int | None = None, values_only: bool):
            rows = (
                REQUIRED_HEADERS,
                (
                    "sku-1",
                    "main-1",
                    "露营灯",
                    "",
                    "",
                    "",
                    "目录",
                    "一级",
                    "二级",
                    "三级",
                    "四级",
                ),
            )
            stop = max_row or len(rows)
            yield from rows[min_row - 1 : stop]

    class FakeWorkbook:
        def __init__(self) -> None:
            self.worksheet = FakeWorksheet()
            self.worksheets = (self.worksheet,)
            self.closed = False

        def __getitem__(self, sheet_name: str) -> FakeWorksheet:
            if sheet_name != "产品列表":
                raise KeyError(sheet_name)
            return self.worksheet

        def close(self) -> None:
            self.closed = True

    workbook = FakeWorkbook()
    monkeypatch.setattr(workbook_module, "load_workbook", lambda *args, **kwargs: workbook)

    rows = iter_source_rows(Path("unused.xlsx"))
    next(rows)
    rows.close()

    assert workbook.closed is True
