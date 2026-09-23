"""The spreadsheet the operator hands on.

One sheet reads like a short report: the store and its conclusions across the
top row, then scene by scene, which products were picked and what they are. A
last sheet says how that list was made, because the numbers in it are not all of
one kind — what our own run decided is frozen, but it was decided against a
stock snapshot that carries a date, and that date is the only thing that says
how old the reasoning is.

The look is part of the deliverable rather than a coat of paint on it. Every
sheet is opened by someone who did not build it, often in a hurry, and often
after being forwarded; three things carry it — a header row that looks like one,
a border around every cell so a row can be followed across, and a gutter so two
scenes do not read as one long table. Nothing below changes a value except the
counts, which are written as numbers so the column the list is ordered by can be
re-sorted and filtered in Excel.
"""

from __future__ import annotations

from datetime import datetime
import io
import math
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .params import CHOICES


# The country as the manager names it. The two-letter code is what the catalogue
# and the stock workbook are keyed by, and it tells the person reading the sheet
# nothing about which warehouse the goods sit in.
COUNTRY_NAMES = {"PH": "菲律宾", "TH": "泰国", "VN": "越南", "MY": "马来西亚"}


def country_name(country: str | None) -> str:
    code = str(country or "").strip().upper()
    return COUNTRY_NAMES.get(code, code)


# --------------------------------------------------------------------------
# The look, in one place, so a header reads as a header on every sheet.
# --------------------------------------------------------------------------

NAVY = "1F3B57"
SECTION = "E7EEF5"
LABEL = "F2F6FA"
STRIPE = "F7FAFC"
LINE = "D5DEE7"

HEADER_FONT = Font(bold=True, color="FFFFFF")
TITLE_FONT = Font(bold=True, color="FFFFFF", size=12)
LABEL_FONT = Font(bold=True, color=NAVY)
MUTED_FONT = Font(color="62748A", size=9)

BODY = Alignment(vertical="top", wrap_text=True)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)

FILL_TITLE = PatternFill("solid", fgColor=NAVY)
FILL_LABEL = PatternFill("solid", fgColor=LABEL)
FILL_SECTION = PatternFill("solid", fgColor=SECTION)
FILL_STRIPE = PatternFill("solid", fgColor=STRIPE)

_EDGE = Side(style="thin", color=LINE)
BORDER = Border(left=_EDGE, right=_EDGE, top=_EDGE, bottom=_EDGE)

# Counts live in the grid as numbers, not as text: the sheet is ordered by stock,
# and a manager who wants to re-sort it or filter it to the ones with goods should
# not have to convert a text column first. "没货" and "未核验" stay words — they are
# facts about the country that Excel has no number for.
QUANTITY_FORMAT = "#,##0.###"

# One scene's block: what the SKU is, what it is called, and how many of it this
# country can ship. The stock header carries the country's name, so a block read
# on its own still says which warehouse the number came from.
BLOCK = ("主SKU", "名称")
BLOCK_WIDTHS = (16, 32, 16)

# The blank column left between two scenes' blocks. Without it the last column of
# one scene sits flush against the first column of the next and the two read as
# one very wide table with a stray header in the middle.
BLOCK_GUTTER = 3

# The sub-SKU sheet, one row per child. It exists to answer one question — which
# child the goods are actually in — so the quantity column is the reason the
# sheet is there and sits next to the child rather than off at the end.
CHILD_WIDTHS = {"A": 16, "B": 32, "C": 22, "D": 40, "E": 16, "F": 42}

# The conclusions, read across one row instead of down eight. They get their
# own sheet, and therefore their own widths, because a field that runs to seven
# hundred characters needs a column wide enough to hold it: sharing columns
# with the table would either clip it or make every SKU cell half a screen wide.
SUMMARY_WIDTHS = {"A": 20, "B": 10, "C": 50, "D": 50, "E": 64, "F": 64, "G": 26, "H": 46}

# The buy-list, and its own widths: no scene column, and room for the places.
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


def picked_candidates(analysis: dict, retrieval: dict, picks: list[dict]):
    """Every pick that survived, as ``(scene, role, candidate)``.

    The picks arrive from the browser and are used as a filter, never as data: a
    SKU the retrieval never returned is dropped rather than written out under a
    name the browser claimed for it. Order is the store's: scene by scene, role
    by role, best-ranked first.
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
                              key=lambda sku: found[sku].get("rank") or 0):
                yield scene_name, role, found[sku]


def picked_by_sku(analysis: dict, retrieval: dict, picks: list[dict]) -> dict[str, dict]:
    """Every picked SKU once, with everywhere it was picked listed beside it.

    The report repeats a SKU once for every role it answers, which is what that
    sheet is for — a role and its SKUs belong together. A list to buy or upload
    from wants the opposite, and one SKU answering nine roles in a scene is
    worth seeing rather than dissolving: it is carrying that scene.
    """
    found: dict[str, dict] = {}
    for scene_name, role, candidate in picked_candidates(analysis, retrieval, picks):
        entry = found.setdefault(str(candidate.get("main_sku")),
                                 {"candidate": candidate, "used": []})
        entry["used"].append(f"{scene_name} · {role}")
    return found


def scene_blocks(analysis: dict, retrieval: dict, picks: list[dict]) -> list[tuple[str, list[list]]]:
    """One block per scene that has picks, holding its main SKUs one to a row.

    A scene used to be a row and its SKUs one cell full of line breaks, which
    meant the manager could not copy a column into the ERP and could not read a
    quantity down the list. Turned on their side — the scene is the column and
    the SKU is the row — both work, and the stock sort becomes visible instead of
    being frozen into the order the lines happen to sit in.

    Scenes nobody picked from are left out rather than drawn as three empty
    columns; the 店铺概况 sheet already lists every scene that was written.
    """
    order: list[str] = []
    by_scene: dict[str, dict[str, dict]] = {}
    for scene_name, _role, candidate in picked_candidates(analysis, retrieval, picks):
        if scene_name not in by_scene:
            by_scene[scene_name] = {}
            order.append(scene_name)
        # One scene, one line per SKU: the same SKU reached by two roles is one
        # product, and the row keeps the best rank it earned across them.
        rows = by_scene[scene_name]
        sku = str(candidate.get("main_sku"))
        previous = rows.get(sku)
        if previous is None or (candidate.get("rank") or 0) < (previous.get("rank") or 0):
            rows[sku] = candidate
    return [(str(name or ""), [[str(item.get("main_sku")), _name(item), _stock(item)]
                               for item in sorted(by_scene[name].values(), key=_by_stock)])
            for name in order]


def deduped_rows(picked: dict[str, dict]) -> list[list]:
    """One row per distinct SKU, with everywhere it was picked listed beside it.

    Order is first appearance, which is the report's order, so the two sheets
    read top to bottom the same way.
    """
    return [[sku, _name(entry["candidate"]), _stock(entry["candidate"]), "\n".join(entry["used"])]
            for sku, entry in picked.items()]


def child_rows(picked: dict[str, dict], children: dict[str, list[tuple[str, str]]],
               stock: dict[str, float] | None) -> list[list]:
    """One row per child of every picked SKU, grouped by that SKU.

    Every child the product has is listed, including the ones this country
    cannot ship, because the question the sheet answers is which child the goods
    are actually in — a list pre-filtered to the good news answers nothing, and
    the operator can sort the zeros away in Excel faster than we can guess which
    of them they meant to see.

    A product the catalogue holds no child for still gets a line saying so. The
    sheet is meant to be exhaustive, and a gap where a货号 should be reads as an
    omission rather than as a fact about the catalogue.
    """
    rows: list[list] = []
    for sku, entry in picked.items():
        candidate = entry["candidate"]
        used = "\n".join(entry["used"])
        found = children.get(sku) or []
        if not found:
            rows.append([sku, _name(candidate), "—", "产品库中未登记子款",
                         _child_quantity(stock, None), used])
            continue
        for child_sku, child_name in sorted(found, key=lambda item: -(stock or {}).get(item[0], 0.0)):
            rows.append([sku, _name(candidate), child_sku, child_name,
                         _child_quantity(stock, child_sku), used])
    return rows


def _name(candidate: dict) -> str:
    return str(candidate.get("standard_name_cn") or "")


def _by_stock(candidate: dict) -> tuple:
    """Most stock first, the recall's own best rank breaking the ties.

    When the stock workbook could not be read every quantity is None, and the
    order falls back to the ranking the four channels produced — which is the
    only ordering there is any basis for at that point.
    """
    return (-float(candidate.get("country_available_quantity") or 0),
            candidate.get("rank") or 0, str(candidate.get("main_sku") or ""))


def _child_quantity(stock: dict[str, float] | None, child: str | None) -> str | float:
    """A child this country cannot ship prints 0. A country we could not read
    prints that instead, because 0 would be an answer we never had."""
    if stock is None:
        return "未核验"
    return float(stock.get(child or "", 0.0))


# Excel closes a row at this height and draws nothing past it.
MAX_ROW_POINTS = 409.0


def _recalled(retrieval: dict) -> dict[tuple, dict[str, dict]]:
    """What the run put in front of the operator, keyed by scene and role."""
    found: dict[tuple, dict[str, dict]] = {}
    for scene in retrieval.get("scenes") or []:
        rows = found.setdefault((scene.get("scene_name"), scene.get("product_cn")), {})
        for candidate in _candidates(scene):
            rows[candidate["main_sku"]] = candidate
    return found


def _stock(candidate: dict) -> str | float:
    """"没货" is a fact about the country, not about the catalogue row."""
    available = candidate.get("country_available")
    if available is None:
        return "未读到库存表"
    if not available:
        return "没货"
    quantity = candidate.get("country_available_quantity")
    if not isinstance(quantity, (int, float)):
        return "有货"
    # The counts arrive as floats, so 1.0 must not be rounded away into a bare
    # "有货" — the number is the point of the column.
    return float(quantity)


def _candidates(scene: dict):
    """Every candidate the role carried, dropped ones included.

    A SKU the verdict pass dropped is not gone: it sits in ``dropped`` until the
    operator puts it back, and a pick can be exactly that row.
    """
    return [*(scene.get("candidates") or []), *(scene.get("dropped") or [])]


def summaries(analysis: dict, store: dict, country: str) -> list[tuple[str, str]]:
    """The store and its conclusions, in the order a manager reads them.

    The product structure leads, because what the store makes its money on is
    what the person opening this sheet came for; the profile is the sentence
    that puts the rest in context, and it reads better after the numbers it
    explains than before them.
    """
    return [
        ("店铺名称", str(store.get("store_name") or "")),
        ("国家", country_name(country)),
        ("当前产品结构", _section(analysis.get("current_product_structure"))),
        ("未来产品结构", _section(analysis.get("future_product_structure"))),
        ("店铺画像", _section(analysis.get("store_profile"))),
        ("人群", _lines((item.get("audience_name"), item.get("description"))
                        for item in analysis.get("audiences") or [])),
        ("场景", _lines((scene.get("scene_name"), "") for scene in analysis.get("scenes") or [])),
        ("未来运营策略", _lines((item.get("strategy_name"), item.get("description"))
                               for item in analysis.get("operation_strategy") or [])),
    ]


def _section(value: dict | None) -> str:
    """The conclusion, and the order to work through it in.

    The support it was built on is not repeated here. The report is read for what
    it makes of the numbers; the numbers themselves are already on the first
    sheet, and a sentence that recites them back reads as if it had decided
    nothing.
    """
    value = value or {}
    parts = [str(value.get("judgement") or "")]
    if value.get("priority_order"):
        parts.append("优先顺序：" + "、".join(value["priority_order"]))
    return "\n".join(part for part in parts if part)


def _lines(pairs) -> str:
    return "\n".join(f"{name}：{text}" if text else str(name) for name, text in pairs if name)


def _param_label(name: str, value) -> str:
    """The form's own words for a setting chosen from a list.

    Every parameter is read back by the person who set it, and a raw value like
    ``all`` says nothing about what the run did; the sheet uses the words the
    dropdown used. Numbers and free text are passed through untouched.
    """
    choice = CHOICES.get(name)
    if choice and str(value) in choice[1]:
        return choice[1][str(value)]
    return str(value)


def _related_label(related_only: bool) -> str:
    """What the export did with the model's doubts, in the operator's words."""
    return ("只看相关的：模型标为待复核的候选没有导出" if related_only
            else "全部导出：模型标为待复核的候选也导出了")


# The models by the name the operator picks them by, not by the setting value.
MODEL_NAMES = {"jev": "Jev", "deepseek": "DeepSeek"}


def _model_name(provider) -> str:
    """The model that answered, or the word for the run where none did."""
    if not provider:
        return "没跑"
    return MODEL_NAMES.get(str(provider), str(provider))


def notes_for(*, retrieval: dict, params: dict, store: dict, store_id: str,
              stock_path: Path, exported: int, related_only: bool = False) -> list[tuple[str, str]]:
    """Describe the snapshot actually used, never the file present at export time."""
    rerank = retrieval.get('rerank') or {}
    snapshot = retrieval.get('inventory_snapshot') or {}
    read = retrieval.get('inventory') == 'available'
    written = snapshot.get('file_modified_at') or '历史结果未记录，请重新匹配后核验'
    notes = [
        ('店铺', str(store.get('store_name') or '')), ('店铺 ID', store_id),
        ('国家', country_name(retrieval.get('country'))),
        ('导出时间', datetime.now().strftime('%Y-%m-%d %H:%M')),
        ('导出条数', f'{exported} 个产品'), ('', ''), ('本次运行的参数', ''),
    ]
    notes += [(label, _param_label(name, params.get(name))) for name, label in PARAM_LABELS]
    notes += [
        ('', ''), ('库存口径', ''),
        ('库存快照文件', str(snapshot.get('filename') or '未记录')),
        ('库存快照时间', str(written)),
        ('库存时间说明', '以上为原文件修改时间（带时区），不是库存业务发生时间，也不是实时库存。'),
        ('库存快照 SHA256', str(snapshot.get('sha256') or '未记录')),
        ('库存读取时间', str(snapshot.get('captured_at') or '未记录')),
        ('这次读到了吗', '读到了' if read else '未核验，本次候选不提供库存承诺'),
        ('', ''), ('相关性复核', ''),
        ('模型怎么处理的', '只标相关 / 待复核，没有删'),
        ('实际用的模型', _model_name(rerank.get('provider'))),
        ('标注时用的把握阈值', str(rerank.get('cutoff') or '')),
        ('导出时怎么处理', _related_label(related_only)),
        ('问了/答了/剔除/失败', '{}/{}/{}/{}'.format(rerank.get('asked', 0), rerank.get('answered', 0),
                                              rerank.get('dropped', 0), rerank.get('failed', 0))),
        ('', ''), ('结果使用说明', ''),
        ('结论和商品', '来自本次已保存结果，主SKU仍需按现有ERP流程选择具体子款。'),
        ('库存数量', '仅对应本次结果绑定的库存快照。替换库存文件不会自动更新已经保存的结果。'),
        ('候选为空', '仅说明本轮召回范围内未找到满足条件的候选，不代表整个商品库没有该商品。'),
    ]
    if retrieval.get('warnings'):
        notes.append(('待确认事项', '\n'.join(str(item) for item in retrieval['warnings'])))
    return notes


def workbook(blocks: list[tuple[str, list[list]]], summary: list[tuple[str, str]],
             notes: list[tuple[str, str]], *, country: str,
             children: list[list], deduped: list[list]) -> bytes:
    """The scene board, the sub-SKU list, the store line, the buy-list and the notes.

    Both the board and the buy-list are always in the file. A board that repeats
    a SKU under every scene it answers is the right shape for reading scene by
    scene, but it is not a list anyone can hand to purchasing, so the one-row-
    per-SKU version rides along rather than being something to ask for.
    """
    book = Workbook()
    _scene_board(book.active, blocks, country)
    _sub_sku_list(book, children, country)
    _store_line(book, summary)
    _buy_list(book, deduped, country)
    _legend(book, notes)
    headline = _headline(summary, country)
    for sheet in book.worksheets:
        _printable(sheet, headline)

    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue()


def _headline(summary: list[tuple[str, str]], country: str) -> str:
    """Which store, which country, which day — for the sheet that gets printed or
    pasted somewhere the file name does not travel with it."""
    store = dict(summary).get("店铺名称") or ""
    return " · ".join(part for part in
                      (store, country_name(country), datetime.now().strftime("%Y-%m-%d")) if part)


def _printable(sheet, headline: str) -> None:
    sheet.oddHeader.left.text = headline
    sheet.oddHeader.left.font = "微软雅黑,Regular"
    sheet.oddHeader.left.size = 10
    sheet.oddHeader.left.color = "62748A"
    sheet.oddFooter.right.text = "第 &P 页 / 共 &N 页"
    sheet.oddFooter.right.size = 9
    sheet.oddFooter.right.color = "62748A"


def _scene_board(sheet, blocks: list[tuple[str, list[list]]], country: str) -> None:
    """One block of columns per scene, so a scene's SKUs copy out in one go."""
    sheet.title = "主SKU清单"
    sheet.sheet_properties.tabColor = NAVY
    if not blocks:
        sheet.append(["本次没有勾选任何商品"])
        sheet.cell(row=1, column=1).font = LABEL_FONT
        return
    headers = (*BLOCK, f"{country_name(country)}有货")
    step = len(headers) + 1
    for index, (scene_name, rows) in enumerate(blocks):
        start = index * step + 1
        title = sheet.cell(row=1, column=start, value=scene_name)
        sheet.merge_cells(start_row=1, start_column=start, end_row=1,
                          end_column=start + len(headers) - 1)
        title.fill = FILL_TITLE
        title.font = TITLE_FONT
        title.alignment = CENTER
        for offset, value in enumerate(headers):
            cell = sheet.cell(row=2, column=start + offset, value=value)
            cell.fill = FILL_LABEL
            cell.font = LABEL_FONT
            cell.alignment = CENTER
            cell.border = BORDER
        for offset, values in enumerate(rows):
            for column, value in enumerate(values):
                cell = sheet.cell(row=offset + 3, column=start + column, value=value)
                cell.alignment = BODY
                cell.border = BORDER
                if isinstance(value, (int, float)):
                    cell.number_format = QUANTITY_FORMAT
        for offset, width in enumerate(BLOCK_WIDTHS):
            sheet.column_dimensions[get_column_letter(start + offset)].width = width
        sheet.column_dimensions[get_column_letter(start + len(headers))].width = BLOCK_GUTTER
    # The header block, the scene title and one blank column read across the whole
    # sheet, so every cell in them is bordered whether a scene wrote there or not.
    for row in (1, 2):
        for column in range(1, len(blocks) * step + 1):
            sheet.cell(row=row, column=column).border = BORDER
    sheet.row_dimensions[1].height = 22
    sheet.row_dimensions[2].height = 20
    for row_index in range(3, sheet.max_row + 1):
        sheet.row_dimensions[row_index].height = min(MAX_ROW_POINTS, max(
            _height(str(sheet.cell(row=row_index, column=index * step + 2).value or ""),
                    BLOCK_WIDTHS[1])
            for index in range(len(blocks))))
    sheet.freeze_panes = sheet.cell(row=3, column=1).coordinate


def _sub_sku_list(book: Workbook, rows: list[list], country: str) -> None:
    """One line per child SKU, grouped by the货号 it belongs to."""
    sheet = book.create_sheet("子SKU清单")
    sheet.sheet_properties.tabColor = SECTION
    for column, width in CHILD_WIDTHS.items():
        sheet.column_dimensions[column].width = width
    sheet.append(["主SKU", "主SKU名称", "子SKU", "子SKU名称",
                  f"{country_name(country)}有货数量", "用在哪个场景"])
    head = sheet.max_row
    _head_row(sheet, head)
    for index, values in enumerate(rows):
        sheet.append(values)
        _data_row(sheet, sheet.max_row, values, stripe=bool(index % 2))
        sheet.row_dimensions[sheet.max_row].height = min(
            MAX_ROW_POINTS, max(_height(str(values[column]), CHILD_WIDTHS[letter])
                                for column, letter in ((1, "B"), (3, "D"), (5, "F"))))
    _table_tail(sheet, head, len(CHILD_WIDTHS))


def _store_line(book: Workbook, summary: list[tuple[str, str]]) -> None:
    """The store and its conclusions, across one row rather than down eight.

    Written one under the other they are eight tall rows, and the table — the
    thing the workbook is for — ends up below a screenful of them. Read across,
    the whole line is one row.
    """
    sheet = book.create_sheet("店铺概况")
    sheet.sheet_properties.tabColor = NAVY
    # A page of prose, not a table: the grid lines would only cut through the
    # sentences the sheet exists to carry.
    sheet.sheet_view.showGridLines = False
    for column, width in SUMMARY_WIDTHS.items():
        sheet.column_dimensions[column].width = width
    sheet.append([label for label, _ in summary])
    _head_row(sheet, 1)
    sheet.row_dimensions[1].height = 24
    sheet.append([value for _, value in summary])
    for cell in sheet[2]:
        cell.alignment = BODY
        cell.border = BORDER
    if summary:
        sheet.row_dimensions[2].height = min(MAX_ROW_POINTS, max(
            _height(value, SUMMARY_WIDTHS[letter])
            for (_, value), letter in zip(summary, SUMMARY_WIDTHS)))


def _buy_list(book: Workbook, rows: list[list], country: str) -> None:
    """One line per SKU, with every scene and role it answers listed beside it."""
    sheet = book.create_sheet("去重商品清单")
    sheet.sheet_properties.tabColor = SECTION
    for column, width in DEDUP_WIDTHS.items():
        sheet.column_dimensions[column].width = width
    sheet.append(["主SKU", "主SKU产品名称", f"{country_name(country)}有货数量", "用在哪里"])
    head = sheet.max_row
    _head_row(sheet, head)
    for index, values in enumerate(rows):
        sheet.append(values)
        _data_row(sheet, sheet.max_row, values, stripe=bool(index % 2))
        sheet.row_dimensions[sheet.max_row].height = min(
            MAX_ROW_POINTS, _height(str(values[3]), DEDUP_WIDTHS["D"]))
    _table_tail(sheet, head, len(DEDUP_WIDTHS))


def _head_row(sheet, row: int) -> None:
    for cell in sheet[row]:
        cell.font = HEADER_FONT
        cell.fill = FILL_TITLE
        cell.alignment = CENTER
        cell.border = BORDER
    sheet.row_dimensions[row].height = 20


def _data_row(sheet, row: int, values: list, *, stripe: bool) -> None:
    for cell, value in zip(sheet[row], values):
        cell.alignment = BODY
        cell.border = BORDER
        if isinstance(value, (int, float)):
            cell.number_format = QUANTITY_FORMAT
        if stripe:
            cell.fill = FILL_STRIPE


def _table_tail(sheet, head: int, columns: int) -> None:
    """Freeze the header, filter by it, and repeat it on every printed page."""
    sheet.freeze_panes = sheet.cell(row=head + 1, column=1).coordinate
    width = get_column_letter(columns)
    sheet.auto_filter.ref = f"A{head}:{width}{max(head, sheet.max_row)}"
    sheet.print_title_rows = f"{head}:{head}"


def _legend(book: Workbook, notes: list[tuple[str, str]]) -> None:
    """What the numbers above are, and what they are not.

    It runs to thirty-odd lines in four groups, so a group is a filled bar rather
    than one more line of the same weight as the rest.
    """
    sheet = book.create_sheet("口径说明")
    sheet.sheet_properties.tabColor = LINE
    sheet.sheet_view.showGridLines = False
    for label, value in notes:
        sheet.append([label, value])
        row = sheet.max_row
        name, text = sheet.cell(row=row, column=1), sheet.cell(row=row, column=2)
        name.font = LABEL_FONT
        if value == "" and label:
            name.fill = FILL_SECTION
            text.fill = FILL_SECTION
            name.alignment = Alignment(vertical="center")
            sheet.row_dimensions[row].height = 20
            continue
        text.alignment = BODY
        sheet.row_dimensions[row].height = _height(value, 90)
    sheet.column_dimensions["A"].width = 22
    sheet.column_dimensions["B"].width = 90


def _height(value: str, width: int) -> float:
    """Tall enough for the wrapped text, since Excel will not size a merged cell."""
    lines = sum(_line_height(line, width) for line in str(value).split("\n"))
    return max(16.0, lines * 15.0 + 3)


def _line_height(line: str, width: int) -> int:
    visual = sum(2 if _cjk(character) else 1 for character in line)
    return max(1, math.ceil(visual / max(1, width)))


def _cjk(character: str) -> bool:
    return "一" <= character <= "鿿"
