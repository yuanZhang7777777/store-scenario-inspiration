"""The spreadsheet the operator hands on.

One sheet reads like a short report: the store and its conclusions across the
top row, then scene by scene, which products were picked and what they are. A
last sheet says how that list was made, because the numbers in it are not all of
one kind — what our own run decided is frozen, but it was decided against a
stock snapshot that carries a date, and that date is the only thing that says
how old the reasoning is.
"""

from __future__ import annotations

from datetime import datetime
from itertools import groupby
import io
import math
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from .params import CHOICES


# Named for someone who has never heard the words this project uses internally:
# a scene, the products it needs, the catalogue SKUs recalled for them, and the
# name and stock each of those SKUs carries. The last three columns are read
# down the same line — SKU *n* is the name on line *n* and the stock on line *n*.
COLUMNS = ("场景", "包含产品", "召回的主SKU", "主SKU产品名称", "目标国家有货数量")

# The table's own columns: a scene name, a product role, a SKU, its name and
# its stock, and nothing else on the sheet.
WIDTHS = {"A": 22, "B": 32, "C": 24, "D": 32, "E": 18}

# The conclusions, read across one row instead of down eight. They get their
# own sheet, and therefore their own widths, because a field that runs to seven
# hundred characters needs a column wide enough to hold it: sharing columns
# with the table would either clip it or make every SKU cell half a screen wide.
SUMMARY_WIDTHS = {"A": 20, "B": 10, "C": 50, "D": 50, "E": 64, "F": 64, "G": 26, "H": 46}

# The buy-list, and its own widths: no scene column, and room for the places.
DEDUP_COLUMNS = ("主SKU", "主SKU产品名称", "目标国家有货数量", "用在哪里")
DEDUP_WIDTHS = {"A": 24, "B": 32, "C": 18, "D": 46}

# The form's own tile titles, word for word: a number the operator set on the
# page and a number they read back in the sheet have to be the same phrase.
PARAM_LABELS = (
    ("scene_count", "写几个场景"),
    ("products_per_scene", "每个场景列几样商品"),
    ("expansion_terms", "每个商品扩几个近义词"),
    ("recall_limit", "每个商品找几个 SKU"),
    ("stock_filter", "没货的要不要留着"),
    ("temperature", "天马行空程度"),
)


def picked_skus(analysis: dict, retrieval: dict, picks: list[dict]):
    """Every pick that survived, as ``(scene, role, sku, name, stock)``.

    The picks arrive from the browser and are used as a filter, never as data: a
    SKU the retrieval never returned is dropped rather than written out under a
    name the browser claimed for it. Order is the store's: scene by scene, role
    by role, best-ranked first, which is the order every sheet keeps.
    """
    wanted = {(item.get("scene_name"), item.get("product_cn"), item.get("main_sku"))
              for item in picks}
    recalled = _recalled(retrieval)
    for scene in analysis.get("scenes") or []:
        scene_name = scene.get("scene_name")
        for product in scene.get("product_needs") or []:
            role = product.get("product_cn")
            found = recalled.get((scene_name, role)) or {}
            for sku in sorted((sku for sku in found if (scene_name, role, sku) in wanted),
                              key=lambda sku: found[sku][0]):
                rank, name, stock = found[sku]
                yield scene_name, role, sku, name, stock


def report_rows(analysis: dict, retrieval: dict, picks: list[dict]) -> list[list[str]]:
    """One row per scene and product role, in the order the store was written.

    The picked SKUs for that role go in one cell and their names in the next,
    one per line, so line *n* of one is line *n* of the other. A role nobody
    picked from does not appear at all.

    A role whose list outgrows one Excel row carries on down the next, scene and
    role left blank the way a sheet shows a group continuing.
    """
    rows = []
    for (scene_name, role), group in groupby(picked_skus(analysis, retrieval, picks),
                                             key=lambda row: row[:2]):
        for index, chunk in enumerate(_groups_of(list(group))):
            head = [scene_name, role] if index == 0 else ["", ""]
            rows.append([*head, *_aligned(chunk)])
    return rows


def deduped_rows(analysis: dict, retrieval: dict, picks: list[dict]) -> list[list[str]]:
    """One row per distinct SKU, with everywhere it was picked listed beside it.

    The report above repeats a SKU once for every role it answers, which is what
    that table is for — a role and its SKUs belong on one line. A list to buy or
    upload from wants the opposite, and one SKU answering nine roles in a scene
    is worth seeing rather than dissolving: it is carrying that scene.

    Order is first appearance, which is the report's order, so the two sheets
    read top to bottom the same way.
    """
    rows: dict[str, list] = {}
    for scene_name, role, sku, name, stock in picked_skus(analysis, retrieval, picks):
        rows.setdefault(sku, [name, stock, []])[2].append(f"{scene_name} · {role}")
    return [[sku, name, stock, "\n".join(where)] for sku, (name, stock, where) in rows.items()]


# Excel closes a row at this height and draws nothing past it.
MAX_ROW_POINTS = 409.0


def roles_exported(rows: list[list[str]]) -> int:
    """How many product roles the table covers.

    Not the same as the number of rows: a role too tall for one row runs on
    down the next, and counting those would report more roles than the
    operator picked.
    """
    return sum(1 for row in rows if row[0])


def _groups_of(picks: list[tuple]):
    """The picked SKUs of one role, cut into groups that fit inside one Excel row.

    The operator can pick all thirty candidates a role carries, and Excel will
    not draw a row tall enough to show thirty lines — it cuts the cell off at
    the row's edge instead, so a sheet that looks like it ends cleanly comes
    back missing the tail of the list.
    """
    group: list[tuple] = []
    for row in picks:
        group.append(row)
        if len(group) > 1 and _aligned_height(_aligned(group)) > MAX_ROW_POINTS:
            group.pop()
            yield group
            group = [row]
    if group:
        yield group


def _aligned(group: list[tuple]) -> list[str]:
    """The three cells that are read across, line by line."""
    return ["\n".join(row[2] for row in group),
            "\n".join(row[3] for row in group),
            "\n".join(row[4] for row in group)]


def _recalled(retrieval: dict) -> dict[tuple, dict[str, tuple[int, str, str]]]:
    """What the run put in front of the operator, keyed by scene and role."""
    found: dict[tuple, dict[str, tuple[int, str, str]]] = {}
    for scene in retrieval.get("scenes") or []:
        rows = found.setdefault((scene.get("scene_name"), scene.get("product_cn")), {})
        for candidate in _candidates(scene):
            rows[candidate["main_sku"]] = (candidate.get("rank") or 0,
                                           candidate.get("standard_name_cn") or "",
                                           _stock(candidate))
    return found


def _stock(candidate: dict) -> str:
    """"没货" is a fact about the country, not about the catalogue row."""
    available = candidate.get("country_available")
    if available is None:
        return "未读到库存表"
    if not available:
        return "没货"
    quantity = candidate.get("country_available_quantity")
    if not isinstance(quantity, (int, float)):
        return "有货"
    # The counts arrive as floats, so 6 comes back as 6.0 and 1.0 must not be
    # rounded away into a bare "有货" — the number is the point of the column.
    return f"{quantity:g}"


def _candidates(scene: dict):
    """Every candidate the role carried, dropped ones included.

    A SKU the verdict pass dropped is not gone: it sits in ``dropped`` until the
    operator puts it back, and a pick can be exactly that row.
    """
    return [*(scene.get("candidates") or []), *(scene.get("dropped") or [])]


def summaries(analysis: dict, store: dict, country: str) -> list[tuple[str, str]]:
    """The store and its conclusions, in the order a manager reads them."""
    return [
        ("店铺名称", str(store.get("store_name") or "")),
        ("国家", country),
        ("店铺画像", _section(analysis.get("store_profile"))),
        ("当前产品结构", _section(analysis.get("current_product_structure"))),
        ("未来产品结构", _section(analysis.get("future_product_structure"))),
        ("人群", _lines((item.get("audience_name"), item.get("description"))
                        for item in analysis.get("audiences") or [])),
        ("场景", _lines((scene.get("scene_name"), "") for scene in analysis.get("scenes") or [])),
        ("未来运营策略", _lines((item.get("strategy_name"), item.get("description"))
                               for item in analysis.get("operation_strategy") or [])),
    ]


def _section(value: dict | None) -> str:
    """A conclusion and what it rests on, one under the other."""
    value = value or {}
    parts = [str(value.get("judgement") or "")]
    if value.get("priority_order"):
        parts.append("优先顺序：" + "、".join(value["priority_order"]))
    if value.get("evidence"):
        parts.append(str(value["evidence"]))
    return "\n".join(part for part in parts if part)


def _lines(pairs) -> str:
    return "\n".join(f"{name}：{text}" if text else str(name) for name, text in pairs if name)


def _mode_label(mode) -> str:
    """The dropdown's own words, so the sheet never shows a raw setting name."""
    if not mode:
        return "没跑"
    return CHOICES["rerank"][1].get(str(mode), str(mode))


# The models by the name the operator picks them by, not by the setting value.
MODEL_NAMES = {"jev": "Jev", "deepseek": "DeepSeek"}


def _model_name(provider) -> str:
    """The model that answered, or the word for the run where none did."""
    if not provider:
        return "没跑"
    return MODEL_NAMES.get(str(provider), str(provider))


def notes_for(*, retrieval: dict, params: dict, store: dict, store_id: str,
              stock_path: Path, exported: int) -> list[tuple[str, str]]:
    """How this particular sheet was made, and what in it can move."""
    rerank = retrieval.get("rerank") or {}
    stock = Path(stock_path)
    written = (
        datetime.fromtimestamp(stock.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        if stock.is_file() else "文件不存在"
    )
    read = retrieval.get("inventory") == "available"
    notes = [
        ("店铺", str(store.get("store_name") or "")),
        ("店铺 ID", store_id),
        ("目标国家", str(retrieval.get("country") or "")),
        ("导出时间", datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("导出条数", f"{exported} 个产品"),
        ("", ""),
        ("本次运行的参数", ""),
    ]
    notes += [(label, str(params.get(name))) for name, label in PARAM_LABELS]
    notes += [
        ("", ""),
        ("库存口径", ""),
        ("库存快照文件", stock.name),
        ("库存快照时间", written),
        ("这次读到了吗", "读到了" if read else "没读到，本次候选没有库存信息"),
        ("", ""),
        ("这次判断实际是怎么跑的", ""),
        ("实际用的模式", _mode_label(rerank.get("mode"))),
        ("实际用的模型", _model_name(rerank.get("provider"))),
        ("实际用的把握阈值", str(rerank.get("cutoff") or "")),
        ("问了/答了/剔除/失败", "{}/{}/{}/{}".format(
            rerank.get("asked", 0), rerank.get("answered", 0),
            rerank.get("dropped", 0), rerank.get("failed", 0))),
        ("", ""),
        ("这份表哪里会变", ""),
        ("结论和商品这几列", "不会变。场景、包含产品、召回的主SKU、主SKU产品名称都是本次运行定下来的，"
                            "任何时候打开都是同一份。"),
        ("有货数量会变", "跟着库存快照走。它说的是那份快照当时的情况，不是现在；"
                        "快照换了、再跑一次召回，这一列才会变。"),
        ("库存是怎么进来的", "库存决定的是候选能不能被召回（看上面的「没货的要不要留着」那一档）。"
                            "标「没货」是指这个 SKU 在目标国家当前没有可发量。"),
    ]
    return notes


def workbook(rows: list[list], summary: list[tuple[str, str]],
             notes: list[tuple[str, str]], deduped: list[list] | None = None) -> bytes:
    """The table, the store line, the buy-list and the notes, as bytes to hand back.

    The buy-list is optional because it is the operator's call: a report that
    repeats a SKU under every role it answers is what the first sheet is for,
    and someone reading role by role does not need the same list again.
    """
    book = Workbook()
    sheet = book.active
    sheet.title = "店铺报告"
    for column, width in WIDTHS.items():
        sheet.column_dimensions[column].width = width

    sheet.append(list(COLUMNS))
    head = sheet.max_row
    for cell in sheet[head]:
        cell.font = Font(bold=True)
    for values in rows:
        sheet.append(values)
        sheet.row_dimensions[sheet.max_row].height = min(
            MAX_ROW_POINTS, _aligned_height(values[2:5]))
    for row in sheet.iter_rows(min_row=head + 1, min_col=3, max_col=5):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.freeze_panes = sheet.cell(row=head + 1, column=1).coordinate

    _store_line(book, summary)
    if deduped is not None:
        _buy_list(book, deduped)

    legend = book.create_sheet("口径说明")
    for label, value in notes:
        legend.append([label, value])
        legend.cell(row=legend.max_row, column=1).font = Font(bold=True)
        legend.cell(row=legend.max_row, column=2).alignment = Alignment(
            wrap_text=True, vertical="top")
        legend.row_dimensions[legend.max_row].height = _height(value, 90)
    legend.column_dimensions["A"].width = 22
    legend.column_dimensions["B"].width = 90

    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue()


def _store_line(book: Workbook, summary: list[tuple[str, str]]) -> None:
    """The store and its conclusions, across one row rather than down eight.

    Written one under the other they are eight tall rows, and the table — the
    thing the workbook is for — ends up below a screenful of them. Read across,
    the whole line is one row.
    """
    sheet = book.create_sheet("店铺概况")
    for column, width in SUMMARY_WIDTHS.items():
        sheet.column_dimensions[column].width = width
    sheet.append([label for label, _ in summary])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.append([value for _, value in summary])
    if summary:
        sheet.row_dimensions[2].height = min(MAX_ROW_POINTS, max(
            _height(value, SUMMARY_WIDTHS[letter])
            for (_, value), letter in zip(summary, SUMMARY_WIDTHS)))
    for cell in sheet[2]:
        cell.alignment = Alignment(wrap_text=True, vertical="top")


def _buy_list(book: Workbook, rows: list[list]) -> None:
    """One line per SKU, with every scene and role it answers listed beside it."""
    sheet = book.create_sheet("去重商品清单")
    for column, width in DEDUP_WIDTHS.items():
        sheet.column_dimensions[column].width = width
    sheet.append(list(DEDUP_COLUMNS))
    head = sheet.max_row
    for cell in sheet[head]:
        cell.font = Font(bold=True)
    for values in rows:
        sheet.append(values)
        sheet.row_dimensions[sheet.max_row].height = min(
            MAX_ROW_POINTS, _height(values[3], DEDUP_WIDTHS["D"]))
    for row in sheet.iter_rows(min_row=head + 1, min_col=2, max_col=4):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.freeze_panes = sheet.cell(row=head + 1, column=1).coordinate


def _aligned_height(cells: list[str]) -> float:
    """Tall enough for the longest of the three lists that share a row."""
    return max(_height(cell, WIDTHS[letter])
               for cell, letter in zip(cells, ("C", "D", "E"))) + 4


def _height(value: str, width: int) -> float:
    """Tall enough for the wrapped text, since Excel will not size a merged cell."""
    lines = sum(_line_height(line, width) for line in str(value).split("\n"))
    return max(16.0, lines * 15.0 + 3)


def _line_height(line: str, width: int) -> int:
    visual = sum(2 if _cjk(character) else 1 for character in line)
    return max(1, math.ceil(visual / max(1, width)))


def _cjk(character: str) -> bool:
    return "一" <= character <= "鿿"
