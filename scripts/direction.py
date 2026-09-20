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

import json
from pathlib import Path
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
    payload = {"schema": "direction-overrides-v1", "overrides": dict(sorted(overrides.items()))}
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        temporary = Path(handle.name)
    temporary.replace(path)
