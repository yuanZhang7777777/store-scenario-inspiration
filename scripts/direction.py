"""Turn recognized product roles into the three store-direction buckets.

The vision pass tags every recognized product with how it appeared in the
screenshot. This module applies a fixed policy on top of those tags and
produces the buckets an operator confirms before anything else runs:

  main      截图里以商品卡片主图出现，是店铺实际在推的商品
  unsure    说是主图但把握不足，或本来就判断不了
  excluded  只作为生活场景的陪衬出现，或运营手动排除

Bucketing stays local and deterministic on purpose. Whether a product belongs
to the store's *direction* is a cross-image judgement that a per-image vision
call cannot make, and it is cheap to re-run whenever the thresholds change.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import tempfile


BUCKET_MAIN = "main"
BUCKET_UNSURE = "unsure"
BUCKET_EXCLUDED = "excluded"
BUCKET_ORDER = (BUCKET_MAIN, BUCKET_UNSURE, BUCKET_EXCLUDED)

ROLE_CARD = "商品卡片主图"
ROLE_SCENERY = "场景中偶然出现"

DEFAULT_MIN_CONFIDENCE = 0.6
LOW_CONFIDENCE_THRESHOLD = 0.6
LOW_CONFIDENCE_SHARE_WARNING = 0.4
SCHEMA = "store-direction-v1"
SCHEMA_ANALYSIS_INPUT = "store-analysis-input-v1"

DIRECTION_NOTE = (
    "observed_product_clues 是本店已确认的主营方向，场景只能围绕它们展开。"
    "excluded_product_clues 是不属于本店方向的商品：不得作为 current_product_structure 的支柱，"
    "也不得出现在任何场景的 product_needs 里。"
)

UNCONFIRMED_NOTE = (
    "direction_confirmed 为 false：还没有任何商品被确认属于本店方向，"
    "observed_product_clues 只是截图中可见的全部商品，不代表本店主营。"
    "照常生成场景，但不要假设它们都是本店方向。"
    "excluded_product_clues 为空表示没有商品被排除，不代表全部商品都被采纳。"
)

_LATIN = re.compile(r"[0-9a-z]{2,}")
_HAN = re.compile(r"[一-鿿]{2,}")


def bucket_clue(clue: dict, *, min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> dict:
    """Assign one recognized clue to a bucket and explain why in plain Chinese."""
    occurrences = clue.get("occurrences") or []
    card_images = sum(1 for item in occurrences if item.get("role") == ROLE_CARD)
    scenery_images = sum(1 for item in occurrences if item.get("role") == ROLE_SCENERY)
    confidence = float(clue.get("confidence") or 0.0)

    if card_images and confidence >= min_confidence:
        bucket = BUCKET_MAIN
        reason = f"在 {card_images} 张截图中作为商品主图出现，识别把握 {confidence:.2f}"
    elif card_images:
        bucket = BUCKET_UNSURE
        reason = (
            f"在 {card_images} 张截图中作为商品主图出现，但识别把握只有 {confidence:.2f}，"
            "需要人工确认"
        )
    elif scenery_images:
        bucket = BUCKET_EXCLUDED
        reason = f"只在 {scenery_images} 张截图中作为场景陪衬出现，从未作为商品主图展示"
    else:
        bucket = BUCKET_UNSURE
        reason = "识别把握不足，无法判断是否为店铺在推的商品"

    return {
        "clue": clue.get("clue"),
        "bucket": bucket,
        "reason": reason,
        "card_images": card_images,
        "scenery_images": scenery_images,
        "confidence": confidence,
        "source_image": list(clue.get("source_image") or []),
        "merged_from": list(clue.get("merged_from") or []),
    }


def summarize(entries: list[dict], *, min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> dict:
    """Group bucketed entries and report how much the role tags can be trusted."""
    buckets = {name: [entry for entry in entries if entry["bucket"] == name] for name in BUCKET_ORDER}
    low = sum(1 for entry in entries if entry["confidence"] < LOW_CONFIDENCE_THRESHOLD)
    share = low / len(entries) if entries else 0.0
    return {
        "schema": SCHEMA,
        "min_confidence": min_confidence,
        "counts": {name: len(items) for name, items in buckets.items()},
        "role_tag_health": {
            "low_confidence_count": low,
            "total": len(entries),
            "share": round(share, 4),
            "needs_review": share > LOW_CONFIDENCE_SHARE_WARNING,
        },
        "entries": entries,
        "buckets": buckets,
    }


def bucket_clues(clues: list[dict], *, min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> dict:
    return summarize(
        [bucket_clue(clue, min_confidence=min_confidence) for clue in clues],
        min_confidence=min_confidence,
    )


def apply_overrides(review: dict, overrides: dict[str, str]) -> dict:
    """Move clues between buckets per operator edits. Operator always wins."""
    if not overrides:
        return review
    by_name = {entry["clue"]: entry for entry in review["entries"]}
    unknown = sorted(set(overrides) - set(by_name))
    if unknown:
        raise ValueError("override targets unknown clue: " + "、".join(unknown))
    for name, bucket in overrides.items():
        if bucket not in BUCKET_ORDER:
            raise ValueError(f"invalid bucket for {name!r}: {bucket!r}")
        by_name[name]["bucket"] = bucket
        by_name[name]["reason"] = "运营手动指定"
    return summarize(review["entries"], min_confidence=review["min_confidence"])


def selected_clues(review: dict, buckets: tuple[str, ...] = (BUCKET_MAIN,)) -> list[str]:
    """Clue names allowed to drive downstream scene generation."""
    return [
        entry["clue"]
        for entry in review["entries"]
        if entry["bucket"] in buckets and entry["clue"]
    ]


def build_analysis_input(sample: dict, review: dict, *, direction: str | None = None) -> dict:
    """Project a store sample down to the direction it should be read through.

    The scene generator used to receive every product the screenshots happened
    to contain, so an off-direction item could become a scene of its own. Feed
    it the confirmed direction instead, and name what was ruled out so the model
    cannot quietly bring it back.

    Confirming a direction stays optional. When nothing is confirmed — a fresh
    store, or one whose screenshots are a general-merchandise grid — the default
    path still has to produce scenes for an operator to judge, so pass
    everything through and say the direction is unconfirmed rather than handing
    the model an empty store.
    """
    by_name = {clue.get("clue"): clue for clue in sample.get("observed_product_clues") or []}
    confirmed = [entry for entry in review["entries"] if entry["bucket"] == BUCKET_MAIN]
    kept = confirmed or review["entries"]
    return {
        "schema": SCHEMA_ANALYSIS_INPUT,
        "store": sample.get("store") or {},
        "store_direction": direction,
        "direction_confirmed": bool(confirmed),
        "direction_note": DIRECTION_NOTE if confirmed else UNCONFIRMED_NOTE,
        "observed_product_clues": [
            {
                "clue": by_name[entry["clue"]].get("clue"),
                "role": by_name[entry["clue"]].get("role"),
                "confidence": by_name[entry["clue"]].get("confidence"),
                "evidence": by_name[entry["clue"]].get("evidence"),
            }
            for entry in kept
        ],
        "excluded_product_clues": [
            {"clue": entry["clue"], "reason": entry["reason"]}
            for entry in review["entries"]
            if entry["bucket"] == BUCKET_EXCLUDED and confirmed
        ],
        "limitations": list(sample.get("limitations") or []),
    }


def find_purity_violations(analysis: dict, excluded: list[str]) -> list[dict]:
    """Report product needs that mention a clue the direction gate excluded.

    A soft signal for the operator, never a reason to throw away a result that
    has already been paid for. Matching is heuristic — a latin run of the
    excluded name has to reappear (``CCTV``), a Chinese run has to be contained
    in one the model wrote (``存储卡`` inside ``监控存储卡``) — so an alias the
    model invented can still slip through, and a neighbouring accessory may be
    flagged for a glance.
    """
    violations = []
    for scene in analysis.get("scenes") or []:
        for product in scene.get("product_needs") or []:
            text = " ".join(str(product.get(field, "")) for field in ("product_cn", "product_en"))
            violations.extend(
                {
                    "scene_name": scene.get("scene_name"),
                    "product_cn": product.get("product_cn"),
                    "excluded_clue": name,
                }
                for name in excluded
                if _mentions(text, name)
            )
    return violations


def _mentions(text: str, name: str) -> bool:
    text, name = str(text).lower(), str(name).lower()
    if set(_LATIN.findall(name)) & set(_LATIN.findall(text)):
        return True
    runs = _HAN.findall(text)
    return any(chunk in run for chunk in _HAN.findall(name) for run in runs)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        temporary = Path(handle.name)
    temporary.replace(path)


def load_overrides(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != "direction-overrides-v1":
        raise ValueError(f"invalid direction overrides: {path}")
    overrides = value.get("overrides")
    if not isinstance(overrides, dict):
        raise ValueError(f"invalid direction overrides: {path}")
    return {str(name): str(bucket) for name, bucket in overrides.items()}


def save_overrides(path: Path, overrides: dict[str, str]) -> None:
    write_json(path, {
        "schema": "direction-overrides-v1",
        "overrides": dict(sorted(overrides.items())),
    })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("store_dir", type=Path, help="Directory holding sample_store.json")
    parser.add_argument("--overrides", type=Path, help="Defaults to <store_dir>/direction_overrides.json")
    parser.add_argument("--direction", help="Operator's name for the store's direction")
    parser.add_argument("--min-confidence", type=float, default=DEFAULT_MIN_CONFIDENCE)
    args = parser.parse_args()

    store_dir = args.store_dir.resolve()
    sample = json.loads((store_dir / "sample_store.json").read_text(encoding="utf-8"))
    review = bucket_clues(
        sample.get("observed_product_clues") or [], min_confidence=args.min_confidence
    )
    overrides_path = args.overrides or store_dir / "direction_overrides.json"
    review = apply_overrides(review, load_overrides(overrides_path))

    direction_path = store_dir / "direction.json"
    input_path = store_dir / "analysis_input.json"
    write_json(direction_path, review)
    write_json(input_path, build_analysis_input(sample, review, direction=args.direction))
    report = {
        "store_dir": str(store_dir),
        "direction": str(direction_path),
        "analysis_input": str(input_path),
        "counts": review["counts"],
        "role_tag_health": review["role_tag_health"],
    }
    if not review["counts"][BUCKET_MAIN]:
        report["hint"] = (
            "还没有商品被确认属于本店方向，场景会照常生成，但都只是「可以试试」的建议。"
            "识别结果缺少 role 标注，或店里本来什么都卖，都会走到这里。"
        )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
