"""Rank generated scenes by how much the store's own screenshots back them.

The default path asks the operator nothing, so it has to be honest instead of
confident. Every scene reports whether it rests on products the store actually
fronts, or on products the model extrapolated from them — and says so on the
card, because a scene built on nothing the store sells should look different
from one built on its shelves.
"""

from __future__ import annotations

from direction import ROLE_CARD, LOW_CONFIDENCE_THRESHOLD, mentions, product_text


BAND_BACKED = "贴合"
BAND_UNSURE = "把握不足"
BAND_STRETCH = "发散"
BAND_ORDER = (BAND_BACKED, BAND_UNSURE, BAND_STRETCH)

MIN_BACKED_PRODUCTS = 2


def rank_scenes(
    analysis: dict,
    clues: list[dict],
    *,
    min_confidence: float = LOW_CONFIDENCE_THRESHOLD,
) -> list[dict]:
    """Score every scene and put the best-supported ones first."""
    scenes = [_score(scene, clues, min_confidence) for scene in analysis.get("scenes") or []]
    scenes.sort(key=lambda item: (
        BAND_ORDER.index(item["fit"]["band"]),
        -item["fit"]["backed_count"],
        str(item["scene_name"]),
    ))
    return scenes


def _score(scene: dict, clues: list[dict], min_confidence: float) -> dict:
    backed: list[str] = []
    weak: list[str] = []
    unsupported: list[str] = []
    shaky = False
    for need in scene.get("product_needs") or []:
        name = need.get("product_cn")
        matched = [clue for clue in clues if mentions(product_text(need), clue.get("clue") or "")]
        if not matched:
            unsupported.append(name)
            continue
        if any(clue.get("role") == ROLE_CARD for clue in matched):
            backed.append(name)
        else:
            weak.append(name)
        if min(float(clue.get("confidence") or 0.0) for clue in matched) < min_confidence:
            shaky = True

    total = len(scene.get("product_needs") or [])
    majority = len(backed) * 2 >= total
    if not backed and not weak:
        band = BAND_STRETCH
    elif shaky:
        band = BAND_UNSURE
    elif len(backed) >= MIN_BACKED_PRODUCTS and majority:
        band = BAND_BACKED
    else:
        band = BAND_STRETCH

    return {
        **scene,
        "fit": {
            "band": band,
            "backed_count": len(backed),
            "product_count": len(scene.get("product_needs") or []),
            "backed": backed,
            "weak": weak,
            "unsupported": unsupported,
        },
    }
