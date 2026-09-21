import io
from pathlib import Path
import sys

from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

# Importing the fixture itself is what registers it here; the fakes it installs
# are the same ones the rest of the API tests run against.
from test_app_api import client, run_job, upload  # noqa: E402, F401

from store_scenario_inspiration.app.export import (  # noqa: E402
    COLUMNS,
    DEDUP_COLUMNS,
    MAX_ROW_POINTS,
    deduped_rows,
    notes_for,
    report_rows,
    roles_exported,
    summaries,
    workbook,
)


def candidate(sku: str, *, rank: int, name: str | None = None,
              available: bool | None = True, quantity: int | None = 12) -> dict:
    return {"rank": rank, "main_sku": sku, "standard_name_cn": name or f"{sku}中文",
            "standard_name_en": f"{sku}EN", "rrf_score": 0.5, "channels": ["cn_keyword"],
            "matched_queries": [sku], "country_available": available,
            "country_available_quantity": quantity, "rerank": "related"}


def retrieval(*roles: dict) -> dict:
    return {"schema": "store-retrieval-v1", "country": "TH", "inventory": "available",
            "scenes": list(roles)}


def role(scene_name: str, product: str, *candidates: dict, dropped: list[dict] | None = None) -> dict:
    return {"scene_name": scene_name, "product_cn": product, "product_en": "EN",
            "queries": {"cn": [product], "en": []}, "candidates": list(candidates),
            "dropped": dropped or []}


def analysis(*scenes: tuple[str, list[str]]) -> dict:
    return {
        "model": "deepseek-flash",
        "manager_summary": {"executive_conclusion": "结论", "business_opportunity": "机会",
                            "recommended_actions": ["一", "二", "三"], "decision_boundary": "边界"},
        "store_profile": {"judgement": "店铺画像判断", "evidence": "画像依据"},
        "current_product_structure": {"judgement": "当前结构判断", "evidence": "当前依据"},
        "future_product_structure": {"judgement": "未来结构判断", "evidence": "未来依据",
                                     "priority_order": ["甲", "乙", "丙"]},
        "audiences": [{"audience_name": "租房上班族", "description": "25-35 岁", "evidence": "依据"}],
        "operation_strategy": [{"strategy_name": "打包卖", "description": "桌布配桌垫", "evidence": "依据"}],
        "scenes": [{"scene_name": name, "audience": "人群", "user_need": "需求", "evidence": "依据",
                    "product_needs": [{"product_cn": product, "product_en": "EN", "purpose": "用途"}
                                      for product in products]}
                   for name, products in scenes],
    }


def pick(scene_name: str, product: str, sku: str) -> dict:
    return {"scene_name": scene_name, "product_cn": product, "main_sku": sku}


# ---------- the table under the report ----------

def test_each_role_lists_its_skus_one_per_line_with_the_names_beside_them() -> None:
    rows = report_rows(
        analysis(("小户型客厅", ["茶几桌布"])),
        retrieval(role("小户型客厅", "茶几桌布",
                       candidate("XX4", rank=2), candidate("XX119", rank=1))),
        [pick("小户型客厅", "茶几桌布", "XX119"), pick("小户型客厅", "茶几桌布", "XX4")],
    )

    assert rows == [["小户型客厅", "茶几桌布", "XX119\nXX4", "XX119中文\nXX4中文", "12\n12"]]


def test_the_lines_of_the_three_columns_stay_in_step() -> None:
    """Read across a line and it is one SKU: its name and its stock on the same
    line. That only holds if the lists are built in one order."""
    rows = report_rows(
        analysis(("客厅", ["桌布"])),
        retrieval(role("客厅", "桌布",
                       candidate("B", rank=2, name="乙名", quantity=3),
                       candidate("A", rank=1, name="甲名", quantity=9))),
        [pick("客厅", "桌布", "A"), pick("客厅", "桌布", "B")],
    )

    scene, product, skus, names, stock = rows[0]
    assert skus.split("\n") == ["A", "B"]
    assert names.split("\n") == ["甲名", "乙名"]
    assert stock.split("\n") == ["9", "3"]


def test_a_role_the_operator_skipped_is_left_out_of_the_table() -> None:
    rows = report_rows(
        analysis(("客厅", ["桌布", "茶几垫"])),
        retrieval(role("客厅", "桌布", candidate("A", rank=1)),
                  role("客厅", "茶几垫", candidate("B", rank=1))),
        [pick("客厅", "桌布", "A")],
    )

    assert [row[1] for row in rows] == ["桌布"]


def test_a_sku_that_was_never_recalled_is_dropped_rather_than_trusted() -> None:
    """The picks arrive from the browser. Names and stock are read off disk, so a
    row the run never produced cannot be written out at all."""
    rows = report_rows(
        analysis(("客厅", ["桌布"])),
        retrieval(role("客厅", "桌布", candidate("A", rank=1))),
        [pick("客厅", "桌布", "A"), pick("客厅", "桌布", "不存在的SKU")],
    )

    assert rows[0][2] == "A"


def test_a_sku_the_verdict_pass_dropped_is_still_exported_when_it_was_picked() -> None:
    """A pick can be a row the operator fished back out of the dropped pile."""
    rows = report_rows(
        analysis(("客厅", ["驱蚊液"])),
        retrieval(role("客厅", "驱蚊液", candidate("A", rank=1),
                       dropped=[candidate("Z", rank=31, name="捕虫器")])),
        [pick("客厅", "驱蚊液", "Z")],
    )

    assert rows[0][2] == "Z"
    assert rows[0][3] == "捕虫器"


def test_a_list_too_tall_for_one_excel_row_carries_on_to_the_next() -> None:
    """Excel refuses to draw a row past 409 points, so a role carrying the
    operator's whole thirty-item shortlist would lose its tail behind the row's
    edge — on a sheet that looked complete. It continues below instead."""
    picks = [pick("客厅", "桌布", f"SKU-{index:02d}") for index in range(40)]
    rows = report_rows(
        analysis(("客厅", ["桌布"])),
        retrieval(role("客厅", "桌布",
                       *(candidate(f"SKU-{index:02d}", rank=index) for index in range(40)))),
        picks,
    )

    assert len(rows) > 1
    # Every pick survives, in rank order, split across the rows in step.
    listed = "\n".join(row[2] for row in rows).split("\n")
    assert listed == [f"SKU-{index:02d}" for index in range(40)]
    # Only the first row names the role; the rest read as the same group going on.
    assert rows[0][:2] == ["客厅", "桌布"]
    assert all(row[:2] == ["", ""] for row in rows[1:])


def test_a_split_role_still_counts_as_one_role() -> None:
    """The sheet reports how many roles it carries, and a role that ran onto a
    second row is one role — counting rows would overstate what was picked."""
    picks = [pick("客厅", "桌布", f"SKU-{index:02d}") for index in range(40)]
    rows = report_rows(
        analysis(("客厅", ["桌布"])),
        retrieval(role("客厅", "桌布",
                       *(candidate(f"SKU-{index:02d}", rank=index) for index in range(40)))),
        picks,
    )

    assert len(rows) > 1
    assert roles_exported(rows) == 1


def test_no_row_is_ever_asked_for_more_height_than_excel_will_draw() -> None:
    """The split is only as good as the height it is measured against."""
    picks = [pick("客厅", "桌布", f"SKU-{index:02d}") for index in range(60)]
    payload = workbook(
        report_rows(
            analysis(("客厅", ["桌布"])),
            retrieval(role("客厅", "桌布",
                           *(candidate(f"SKU-{index:02d}", rank=index, name="很长很长的中文名称"
                                       * 3) for index in range(60)))),
            picks,
        ),
        [], [],
    )

    # Rows left on auto height are Excel's to size; the ones we set are ours.
    heights = [dim.height for dim in
               load_workbook(io.BytesIO(payload))["店铺报告"].row_dimensions.values()
               if dim.height is not None]
    assert heights and max(heights) <= MAX_ROW_POINTS


# ---------- the buy-list ----------

def test_a_sku_picked_in_several_places_is_one_line_that_says_where() -> None:
    """The table above repeats a SKU once per role, which is what it is for. A
    list to buy or upload from wants the opposite, and one SKU carrying nine
    roles in a scene is worth seeing rather than dissolving."""
    rows = deduped_rows(
        analysis(("客厅", ["桌布"]), ("卧室", ["床旗"])),
        retrieval(role("客厅", "桌布", candidate("A", rank=1)),
                  role("卧室", "床旗", candidate("A", rank=1))),
        [pick("客厅", "桌布", "A"), pick("卧室", "床旗", "A")],
    )

    assert rows == [["A", "A中文", "12", "客厅 · 桌布\n卧室 · 床旗"]]


def test_the_buy_list_only_names_the_places_that_were_actually_picked() -> None:
    rows = deduped_rows(
        analysis(("客厅", ["桌布", "茶几垫"])),
        retrieval(role("客厅", "桌布", candidate("A", rank=1)),
                  role("客厅", "茶几垫", candidate("A", rank=1))),
        [pick("客厅", "桌布", "A")],
    )

    assert rows == [["A", "A中文", "12", "客厅 · 桌布"]]


def test_the_buy_list_keeps_the_order_the_table_uses() -> None:
    """Both sheets read top to bottom the same way, so the operator does not have
    to hunt for the row they were just looking at."""
    rows = deduped_rows(
        analysis(("客厅", ["桌布"])),
        retrieval(role("客厅", "桌布", candidate("B", rank=2), candidate("A", rank=1))),
        [pick("客厅", "桌布", "A"), pick("客厅", "桌布", "B")],
    )

    assert [row[0] for row in rows] == ["A", "B"]


def test_the_buy_list_counts_a_dropped_row_that_was_picked_the_same_way() -> None:
    rows = deduped_rows(
        analysis(("客厅", ["驱蚊液"])),
        retrieval(role("客厅", "驱蚊液", candidate("A", rank=1, name="驱蚊液"),
                       dropped=[candidate("Z", rank=31, name="捕虫器")])),
        [pick("客厅", "驱蚊液", "Z")],
    )

    assert rows == [["Z", "捕虫器", "12", "客厅 · 驱蚊液"]]


# ---------- the stock column ----------

def test_out_of_stock_says_so_rather_than_showing_a_zero() -> None:
    """"0" reads as a number somebody could top up; "没货" reads as a fact about
    the country."""
    rows = report_rows(
        analysis(("客厅", ["桌布"])),
        retrieval(role("客厅", "桌布",
                       candidate("A", rank=1, available=False, quantity=0),
                       candidate("B", rank=2, available=True, quantity=40))),
        [pick("客厅", "桌布", "A"), pick("客厅", "桌布", "B")],
    )

    assert rows[0][4].split("\n") == ["没货", "40"]


def test_a_count_that_came_back_as_a_float_is_still_a_count() -> None:
    """The stock reader hands whole numbers over as floats, so 1.0 used to fall
    through to a bare "有货" and the column lost the number it exists for."""
    rows = report_rows(
        analysis(("客厅", ["桌布"])),
        retrieval(role("客厅", "桌布", candidate("A", rank=1, quantity=1.0),
                       candidate("B", rank=2, quantity=6.0),
                       candidate("C", rank=3, quantity=2.5))),
        [pick("客厅", "桌布", sku) for sku in ("A", "B", "C")],
    )

    assert rows[0][4].split("\n") == ["1", "6", "2.5"]


def test_a_store_whose_stock_was_never_read_is_not_called_out_of_stock() -> None:
    """No answer to "有没有货" is a different thing from "没有货", and printing
    the second when we mean the first is a lie about the country."""
    rows = report_rows(
        analysis(("客厅", ["桌布"])),
        retrieval(role("客厅", "桌布", candidate("A", rank=1, available=None, quantity=None))),
        [pick("客厅", "桌布", "A")],
    )

    assert rows[0][4] == "未读到库存表"


# ---------- the report above the table ----------

def test_the_report_starts_with_the_store_and_its_conclusions() -> None:
    found = dict(summaries(analysis(("小户型客厅", ["茶几桌布"])),
                           {"store_name": "Shopee-15005TH"}, "TH"))

    assert list(found)[:2] == ["店铺名称", "国家"]
    assert found["店铺名称"] == "Shopee-15005TH"
    assert found["国家"] == "TH"
    assert found["店铺画像"] == "店铺画像判断\n画像依据"
    assert found["当前产品结构"] == "当前结构判断\n当前依据"
    assert found["未来产品结构"] == "未来结构判断\n优先顺序：甲、乙、丙\n未来依据"
    assert found["人群"] == "租房上班族：25-35 岁"
    assert found["场景"] == "小户型客厅"
    assert found["未来运营策略"] == "打包卖：桌布配桌垫"


def test_the_store_line_reads_across_rather_than_down() -> None:
    """Stacked one per row, eight conclusions are a screenful of tall rows and
    the table the sheet exists for is somewhere below them."""
    payload = workbook([], summaries(analysis(("客厅", ["桌布"])),
                                     {"store_name": "店"}, "TH"), [])
    sheet = load_workbook(io.BytesIO(payload))["店铺概况"]

    labels = [cell.value for cell in sheet[1]]
    values = [cell.value for cell in sheet[2]]
    assert (labels[0], labels[-1]) == ("店铺名称", "未来运营策略")
    assert (values[0], values[1]) == ("店", "TH")
    # One row of values, so nothing runs on below it.
    assert sheet["A3"].value is None


def test_a_long_conclusion_still_fits_inside_one_excel_row() -> None:
    """The columns are wide on purpose. A seven-hundred-character conclusion in
    a narrow one is a row Excel will not draw past 409 points, and the tail of
    it would simply be missing — which is what the operator saw."""
    payload = workbook([], [("店铺名称", "店"), ("国家", "TH"), ("店铺画像", ""),
                            ("当前产品结构", ""), ("未来产品结构", "很长的结论" * 140),
                            ("人群", ""), ("场景", ""), ("未来运营策略", "")], [])
    sheet = load_workbook(io.BytesIO(payload))["店铺概况"]

    assert sheet.row_dimensions[2].height < MAX_ROW_POINTS


def test_a_report_with_no_audiences_or_strategies_leaves_those_cells_empty() -> None:
    bare = {**analysis(), "audiences": [], "operation_strategy": [], "scenes": []}

    found = dict(summaries(bare, {}, "PH"))

    assert found["人群"] == ""
    assert found["未来运营策略"] == ""
    assert found["场景"] == ""


# ---------- how the sheet says it was made ----------

def snapshot(filename: str) -> dict:
    return {"filename": filename, "sha256": "a" * 64, "captured_at": "2026-09-20 10:00",
            "file_modified_at": "2026-09-19 09:30 (+08:00)"}


def test_the_notes_name_the_snapshot_this_result_was_built_from(tmp_path) -> None:
    stock = tmp_path / "库存明细.xlsx"
    stock.write_bytes(b"x")

    notes = dict(notes_for(
        retrieval={**retrieval(), "inventory_snapshot": snapshot("库存明细.xlsx")},
        params={"scene_count": 6}, store={"store_name": "店"},
        store_id="store-1", stock_path=stock, exported=3,
    ))

    assert notes["库存快照文件"] == "库存明细.xlsx"
    assert notes["库存快照时间"] == "2026-09-19 09:30 (+08:00)"
    assert notes["库存快照 SHA256"] == "a" * 64
    assert notes["导出条数"] == "3 个产品"
    assert notes["这次读到了吗"] == "读到了"
    assert notes["库存数量"].startswith("仅对应本次结果绑定的库存快照")


def test_a_file_that_appeared_later_never_stands_in_for_the_one_that_was_read(tmp_path) -> None:
    """The export is handed whatever stock file is configured *now*. That file is
    not the evidence for these rows, so borrowing its timestamp would date the
    result to a snapshot it was never built from."""
    stock = tmp_path / "后来放进去的.xlsx"
    stock.write_bytes(b"x")

    notes = dict(notes_for(
        retrieval={**retrieval(), "inventory": "unavailable"}, params={}, store={},
        store_id="store-1", stock_path=stock, exported=0,
    ))

    assert notes["库存快照时间"] == "历史结果未记录，请重新匹配后核验"
    assert notes["库存快照文件"] == "未记录"
    assert notes["这次读到了吗"] == "未核验，本次候选不提供库存承诺"


def test_the_snapshot_the_results_used_survives_the_file_being_gone(tmp_path) -> None:
    notes = dict(notes_for(
        retrieval={**retrieval(), "inventory_snapshot": snapshot("已删除的库存.xlsx")},
        params={}, store={}, store_id="store-1",
        stock_path=tmp_path / "没有这个文件.xlsx", exported=0,
    ))

    assert notes["库存快照文件"] == "已删除的库存.xlsx"
    assert notes["库存快照时间"] == "2026-09-19 09:30 (+08:00)"


# ---------- the download ----------

def sheet_of(response) -> dict:
    book = load_workbook(io.BytesIO(response.content))
    return {name: [[cell.value for cell in row] for row in book[name].iter_rows()]
            for name in book.sheetnames}


def head_row(rows: list[list]) -> int:
    return next(index for index, row in enumerate(rows) if list(row[:5]) == list(COLUMNS))


def test_the_export_arrives_as_a_named_workbook(client) -> None:
    store_id = upload(client).json()["id"]
    run_job(client, store_id)

    response = client.post(f"/api/stores/{store_id}/adoption/export",
                           json={"picks": [pick("庭院遮阳", "遮阳棚替换布", "SKU-1")]})

    assert response.status_code == 200
    assert "spreadsheetml" in response.headers["content-type"]
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    sheets = sheet_of(response)
    assert list(sheets) == ["店铺报告", "店铺概况", "口径说明"]
    report = sheets["店铺报告"]
    head = head_row(report)
    assert report[head][:5] == list(COLUMNS)
    assert report[head + 1][:3] == ["庭院遮阳", "遮阳棚替换布", "SKU-1"]
    # The table starts at the top of its own sheet — nothing above it to scroll
    # past — and the store's line sits beside it on the sheet next door.
    assert head == 0
    line = sheets["店铺概况"]
    assert line[0][0] == "店铺名称"
    assert line[1][0] == "Shopee-13021PH"
    assert line[0][6] == "场景"
    assert line[1][6] == "庭院遮阳\n亲子露营"


def test_asking_for_dedupe_adds_the_buy_list_beside_the_report(client) -> None:
    """The operator chooses. Left alone the sheet is the report it always was;
    asked for, a second sheet keeps each SKU once and says where it was used."""
    store_id = upload(client).json()["id"]
    run_job(client, store_id)

    # The fake catalogue answers every role with the same SKU, which is exactly
    # the case worth collapsing: one product carrying two roles in one scene.
    response = client.post(f"/api/stores/{store_id}/adoption/export", json={
        "picks": [pick("庭院遮阳", "遮阳棚替换布", "SKU-1"),
                  pick("庭院遮阳", "风扇", "SKU-1")],
        "dedupe": True,
    })

    sheets = sheet_of(response)
    assert list(sheets) == ["店铺报告", "店铺概况", "去重商品清单", "口径说明"]
    report = sheets["店铺报告"]
    head = head_row(report)
    # The report still names the SKU twice, once under each role it answers.
    assert [row[2] for row in report[head + 1:]] == ["SKU-1", "SKU-1"]

    buy = sheets["去重商品清单"]
    assert buy[0] == list(DEDUP_COLUMNS)
    assert buy[1] == ["SKU-1", "遮阳网", "未读到库存表",
                      "庭院遮阳 · 遮阳棚替换布\n庭院遮阳 · 风扇"]


def test_exporting_before_anything_was_picked_has_an_empty_table(client) -> None:
    store_id = upload(client).json()["id"]
    run_job(client, store_id)

    response = client.post(f"/api/stores/{store_id}/adoption/export", json={"picks": []})

    assert response.status_code == 200
    report = sheet_of(response)["店铺报告"]
    assert len(report) == head_row(report) + 1


def test_exporting_a_store_whose_recall_never_ran_says_which_stage_is_missing(client) -> None:
    store_id = upload(client).json()["id"]

    response = client.post(f"/api/stores/{store_id}/adoption/export", json={"picks": []})

    assert response.status_code == 409
    assert "retrieval.json" in response.json()["detail"]


def test_the_notes_sheet_names_the_parameters_this_run_used(client) -> None:
    store_id = upload(client).json()["id"]
    run_job(client, store_id, params={"scene_count": 6, "products_per_scene": 8,
                                      "expansion_terms": 2, "recall_limit": 5})

    response = client.post(f"/api/stores/{store_id}/adoption/export", json={"picks": []})
    notes = dict((row[0], row[1]) for row in sheet_of(response)["口径说明"])

    assert notes["写几个场景"] == "6"
    assert notes["每个商品找几个 SKU"] == "5"
    assert notes["店铺 ID"] == store_id
    assert notes["目标国家"] == "PH"
