"""Prepare store-analysis workbook exports as JSON."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path

from python_calamine import CalamineWorkbook


COUNTRIES = {"菲律宾": "PH", "泰国": "TH", "越南": "VN", "马来西亚": "MY"}
STOCK_FIELDS = (
    "单销", "本地海外仓在途", "本地海外仓库存", "海外仓在途", "库存中心库存", "公共池库存"
)
MODEL_LABELS = {"gpt55": "GPT-5.5", "deepseek": "DeepSeek Flash"}


def text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def iter_rows(path: Path):
    workbook = CalamineWorkbook.from_path(str(path))
    try:
        for sheet_name in workbook.sheet_names:
            rows = workbook.get_sheet_by_name(sheet_name).iter_rows()
            for row_number, row in enumerate(rows, start=1):
                headers = [text(value) for value in row]
                if "主SKU" not in headers:
                    if row_number < 30:
                        continue
                    break
                columns = {header: index for index, header in enumerate(headers) if header}
                for current, data in enumerate(rows, start=row_number + 1):
                    if any(text(value) for value in data):
                        yield sheet_name, current, columns, data
                break
    finally:
        workbook.close()


def get(row: list[object], columns: dict[str, int], field: str) -> object:
    index = columns.get(field)
    return row[index] if index is not None and index < len(row) else None


def load_stock(path: Path, wanted: set[str]) -> tuple[dict[tuple[str, str], dict], set[str]]:
    totals: dict[tuple[str, str], dict] = defaultdict(lambda: defaultdict(float))
    covered = set()
    for _, _, columns, row in iter_rows(path):
        raw_country = text(get(row, columns, "国家"))
        country = COUNTRIES.get(raw_country, raw_country.upper())
        if country:
            covered.add(country)
        main_sku = text(get(row, columns, "主SKU"))
        if main_sku not in wanted:
            continue
        item = totals[(country, main_sku)]
        for field in STOCK_FIELDS:
            item[field] += number(get(row, columns, field))
    result = {}
    for key, item in totals.items():
        available = item["库存中心库存"] + item["公共池库存"]
        total = sum(item[field] for field in (
            "本地海外仓在途", "本地海外仓库存", "海外仓在途", "库存中心库存", "公共池库存"
        ))
        result[key] = {
            "available_quantity": round(available, 4),
            "single_sales": round(item["单销"], 4),
            "total_stock": round(total, 4),
        }
    return result, covered


def load_review_times(path: Path, wanted: set[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for _, _, columns, row in iter_rows(path):
        main_sku = text(get(row, columns, "主SKU"))
        if main_sku not in wanted:
            continue
        value = text(get(row, columns, "审核时间"))
        if value and value > result.get(main_sku, ""):
            result[main_sku] = value
    return result


def flattened(value: object, separator: str = "、") -> str:
    if isinstance(value, list):
        return separator.join(item for item in (flattened(entry, separator) for entry in value) if item)
    if isinstance(value, dict):
        return separator.join(item for item in (flattened(entry, separator) for entry in value.values()) if item)
    return text(value)


def labeled_fields(value: object, fields: tuple[tuple[str, str], ...]) -> str:
    if not isinstance(value, dict):
        return text(value)
    output = []
    seen = set()
    for key, label in fields:
        rendered = flattened(value.get(key))
        if rendered and rendered not in seen:
            seen.add(rendered)
            output.append(f"{label}：{rendered}")
    return "\n".join(output)


def current_structure_text(value: object) -> str:
    return labeled_fields(value, (
        ("judgement", "当前判断"), ("judgment", "当前判断"),
        ("observed_core", "主要产品"), ("observed_lines", "当前产品线"),
        ("primary_visible_categories", "主营产品"),
        ("secondary_visible_categories", "补充产品"),
        ("strong_evidence_lines", "主要产品"), ("limited_evidence_lines", "少量产品"),
        ("structure_summary", "结构特点"), ("structure_note", "结构特点"),
        ("evidence", "判断依据"),
        ("actionable_implication", "经营含义"),
    ))


def store_profile_text(value: object) -> str:
    return labeled_fields(value, (
        ("judgement", "核心定位"), ("judgment", "核心定位"),
        ("visible_positioning", "核心定位"), ("observed_positioning", "核心定位"),
        ("evidence", "判断依据"),
        ("actionable_implication", "经营特点"),
    ))


def audience_text(value: object, scenes: list[dict]) -> str:
    if isinstance(value, list):
        lines = []
        for item in value:
            if not isinstance(item, dict):
                continue
            name, description = text(item.get("audience_name")), text(item.get("description"))
            rendered = "：".join(part for part in (name, description) if part)
            if rendered:
                lines.append(rendered)
        if lines:
            return "\n".join(dict.fromkeys(lines))
    return "\n".join(dict.fromkeys(
        value for value in (text(scene.get("audience")) for scene in scenes) if value
    ))


def scene_columns(scenes: list[dict]) -> dict[str, str]:
    columns = {key: [] for key in (
        "core_audiences", "demand_scenes", "pain_points", "user_goals", "product_directions"
    )}
    for scene in scenes:
        needs = scene.get("product_needs", [])
        columns["core_audiences"].append(text(scene.get("audience")))
        columns["demand_scenes"].append(text(scene.get("scene_name")))
        columns["pain_points"].append(text(scene.get("user_need")))
        columns["user_goals"].append("；".join(
            value for value in (text(item.get("purpose")) for item in needs) if value
        ))
        columns["product_directions"].append("、".join(dict.fromkeys(
            value for value in (text(item.get("product_cn")) for item in needs) if value
        )))
    return {key: "\n".join(values) for key, values in columns.items()}


def future_fields(analysis: dict, manager: dict, scenes: list[dict]) -> dict[str, str]:
    future = analysis.get("future_product_structure")
    future = future if isinstance(future, dict) else {}
    direction_parts = []
    for value in (
        manager.get("business_opportunity"), future.get("judgement"), future.get("judgment"),
        future.get("expansion_logic"), future.get("principle"), future.get("evidence"),
        future.get("actionable_implication"),
    ):
        rendered = flattened(value)
        if rendered and rendered not in direction_parts:
            direction_parts.append(rendered)
    future_needs = list(dict.fromkeys(
        value for value in (text(scene.get("user_need")) for scene in scenes) if value
    ))
    suggested = []
    for key in ("suggested_groups", "adjacent_expansion", "priority_scenes"):
        value = future.get(key)
        if isinstance(value, list):
            suggested.extend(text(item) for item in value if text(item))
        elif text(value):
            suggested.append(text(value))
    if not suggested:
        suggested = list(dict.fromkeys(
            text(item.get("product_cn"))
            for scene in scenes for item in scene.get("product_needs", [])
            if text(item.get("product_cn"))
        ))
    return {
        "future_direction": "\n".join(direction_parts),
        "future_need": "\n".join(future_needs),
        "suggested_product_lines": "\n".join(dict.fromkeys(suggested)),
        "priority": "\n".join(
            text(item) for item in future.get("priority_order", []) if text(item)
        ),
    }


def operation_advice(analysis: dict, manager: dict) -> str:
    output = []
    strategy = analysis.get("operation_strategy")
    if isinstance(strategy, list):
        for item in strategy:
            if not isinstance(item, dict):
                continue
            rendered = "：".join(part for part in (
                text(item.get("strategy_name") or item.get("strategy")),
                text(item.get("description") or item.get("detail")),
            ) if part)
            if rendered:
                output.append(rendered)
    elif isinstance(strategy, dict):
        output.extend(text(value) for key, value in strategy.items()
                      if key not in {"evidence", "evidence_boundary"} and text(value))
    elif text(strategy):
        output.append(text(strategy))
    actions = manager.get("recommended_actions")
    if isinstance(actions, list):
        output.extend(text(item) for item in actions if text(item))
    return "\n".join(dict.fromkeys(output))


def keyword_lines(scenes: list[dict]) -> str:
    output = []
    for scene in scenes:
        for product in scene.get("products", []):
            cn = text(product.get("canonical_cn") or product.get("product_cn"))
            if cn:
                output.append(cn)
    return "\n".join(dict.fromkeys(output))


def export_data(batch_label: str, root: Path, model: str, source_url: str,
                stock: dict, covered: set[str], review_times: dict[str, str]) -> dict:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    stores = []
    for sequence, entry in enumerate(manifest["stores"], start=1):
        store_id = entry["id"]
        base = root / "stores" / store_id
        sample = json.loads((base / "sample_store.json").read_text(encoding="utf-8"))
        final = json.loads((base / f"{model}_final.json").read_text(encoding="utf-8"))
        analysis = final["analysis"]
        store = sample["store"]
        source = sample.get("source", {})
        country = text(final.get("country") or store.get("country"))
        recommendations = final.get("recommended_main_skus", [])
        enriched = []
        for item in recommendations:
            main_sku = text(item.get("main_sku"))
            metrics = stock.get((country, main_sku))
            if country not in covered:
                metrics = None
            enriched.append({
                "main_sku": main_sku,
                "name_cn": text(item.get("standard_name_cn")),
                "inventory_exists": "" if country not in covered else ("是" if metrics is not None else "否"),
                "available_quantity": None if metrics is None else metrics["available_quantity"],
                "single_sales": None if metrics is None else metrics["single_sales"],
                "total_stock": None if metrics is None else metrics["total_stock"],
                "review_time": review_times.get(main_sku, ""),
            })
        manager = analysis.get("manager_summary", {})
        analysis_scenes = analysis.get("scenes", [])
        result_scenes = final.get("scenes", [])
        scene_data = scene_columns(analysis_scenes)
        scene_data["core_audiences"] = audience_text(analysis.get("audiences"), analysis_scenes)
        future = future_fields(analysis, manager, analysis_scenes)
        stores.append({
            "sequence": sequence,
            "store_name": text(store.get("store_name")),
            "ado": store.get("ado"),
            "adg": store.get("adg"),
            "real_store_name": text(store.get("real_store_name")),
            "country": country,
            "store_attribute": text(source.get("store_attribute")),
            "owner": text(store.get("owner")),
            "department": text(store.get("department")),
            "platform": text(store.get("platform")),
            "inventory_status": f"已覆盖（{country}）" if country in covered else f"未覆盖（{country}）",
            "current_product_structure": current_structure_text(analysis.get("current_product_structure")),
            "store_profile": store_profile_text(analysis.get("store_profile")),
            **scene_data,
            **future,
            "operation_advice": operation_advice(analysis, manager),
            "keywords": keyword_lines(result_scenes),
            "recommendations": enriched,
        })
    model_label = MODEL_LABELS[model]
    return {
        "batch_label": batch_label,
        "model": model,
        "model_label": model_label,
        "source_url": source_url,
        "filename": f"{batch_label}_{model_label}_前10家店铺分析.xlsx",
        "stores": stores,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stock", type=Path, required=True)
    parser.add_argument("--product-list", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch", action="append", nargs=3, metavar=("LABEL", "ROOT", "URL"), required=True)
    parser.add_argument("--model", action="append", choices=tuple(MODEL_LABELS),
                        help="Export only selected models; repeat to include more than one")
    args = parser.parse_args()
    selected_models = args.model or list(MODEL_LABELS)
    configs = [(label, Path(root), url) for label, root, url in args.batch]
    wanted = set()
    for _, root, _ in configs:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        for entry in manifest["stores"]:
            base = root / "stores" / entry["id"]
            for model in selected_models:
                final = json.loads((base / f"{model}_final.json").read_text(encoding="utf-8"))
                wanted.update(item["main_sku"] for item in final.get("recommended_main_skus", []))
    stock, covered = load_stock(args.stock, wanted)
    review_times = load_review_times(args.product_list, wanted)
    exports = [
        export_data(label, root, model, url, stock, covered, review_times)
        for label, root, url in configs for model in selected_models
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"exports": exports}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "exports": len(exports),
                      "wanted_skus": len(wanted), "stock_rows": len(stock),
                      "review_times": len(review_times)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
