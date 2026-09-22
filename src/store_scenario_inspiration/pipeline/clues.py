"""Decide which recognized products the scenes are allowed to be built from.

Recognition tags every product it sees with how it appeared in the screenshot,
but a per-image call cannot tell whether a product is what the store is *about*
— and in practice it tags nearly everything as a product card at the same
confidence, so there is no threshold that separates the store's direction from
an item that merely happened to be photographed. Rather than dress a coin flip
up as policy, the operator gets exactly one lever: exclude what does not belong.

Everything not excluded passes through, and the excluded names are handed to the
scene generator as a list it must not reintroduce.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

from .business import business_for_analysis
from .artifacts import write_json


ROLE_CARD = "商品卡片主图"
ROLE_SCENERY = "场景中偶然出现"

SCHEMA_CLUES = "store-clues-v1"
SCHEMA_EXCLUSIONS = "store-exclusions-v1"
SCHEMA_ANALYSIS_INPUT = "store-analysis-input-v1"
SCHEMA_CUSTOM_PRODUCTS = "store-custom-products-v1"

# Marks a clue the operator typed in themselves. Recognition only sees what is
# in the screenshots, and a store sells things it never photographed; a clue
# with no image behind it has to be readable as such wherever it is listed.
MANUAL_ROLE = "人工添加"

DIRECTION_NOTE = (
    "observed_product_clues 是这家店截图里可见的商品，运营排除只是分析范围，不等于已验证主营或畅销方向。"
    "它们决定哪些场景值得做，但不是场景清单的上限。"
    "每个场景的 product_needs 要从场景本身出发写全，可以包含店里没有在卖的商品。"
    "excluded_product_clues 是运营明确排除的商品：不得作为 current_product_structure 的支柱，"
    "也不得出现在任何场景的 product_needs 里。"
)

_LATIN = re.compile(r"[0-9a-z]{2,}")
_HAN = re.compile(r"[一-鿿]{2,}")


def review_clues(clues: list[dict], excluded: set[str],
                 custom: list[dict] | None = None) -> dict:
    """Attach the operator's exclusions to the recognition output.

    Nothing here decides anything: a clue is excluded because the operator said
    so, and every other clue is kept. The per-image counts are carried along as
    context for that judgement, not as a verdict.

    Products the operator added by hand join the same list — they are things the
    store sells that no screenshot happened to show, and the scenes are meant to
    be built from what the store sells. One already visible in a screenshot is
    left to the screenshot, which is where its evidence is.
    """
    entries = []
    for clue in clues:
        name = clue.get("clue")
        occurrences = clue.get("occurrences") or []
        entries.append({
            "clue": name,
            "excluded": name in excluded,
            # The screenshot's own wording, when the name above is the Chinese
            # the vision pass translated it into. What the operator recognises.
            "original": clue.get("original") or "",
            "role": clue.get("role"),
            "confidence": float(clue.get("confidence") or 0.0),
            "evidence": clue.get("evidence") or "",
            "image_count": len({item.get("image") for item in occurrences}),
            "card_images": sum(1 for item in occurrences if item.get("role") == ROLE_CARD),
            "scenery_images": sum(1 for item in occurrences if item.get("role") == ROLE_SCENERY),
            "merged_from": list(clue.get("merged_from") or []),
            "manual": False,
        })
    seen = {entry["clue"] for entry in entries}
    for item in custom or []:
        name = str(item.get("name_cn") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        english = str(item.get("name_en") or "").strip()
        entries.append({
            "clue": name,
            "excluded": name in excluded,
            "original": english,
            "role": MANUAL_ROLE,
            # No screenshot to be confident about, and saying 0 reads as a
            # measurement. The evidence sentence is what carries the fact.
            "confidence": 0.0,
            "evidence": f"运营手动添加，没有截图依据；英文名：{english}" if english
                        else "运营手动添加，没有截图依据。",
            "image_count": 0,
            "card_images": 0,
            "scenery_images": 0,
            "merged_from": [],
            "manual": True,
        })
    return {
        "schema": SCHEMA_CLUES,
        "counts": {
            "kept": sum(1 for entry in entries if not entry["excluded"]),
            "excluded": sum(1 for entry in entries if entry["excluded"]),
        },
        "entries": entries,
    }


def kept_clues(review: dict) -> list[str]:
    """Clue names the scenes may be built from."""
    return [entry["clue"] for entry in review["entries"] if not entry["excluded"] and entry["clue"]]


def excluded_clues(review: dict) -> list[str]:
    return [entry["clue"] for entry in review["entries"] if entry["excluded"] and entry["clue"]]


def build_analysis_input(sample: dict, review: dict, *, direction: str | None = None) -> dict:
    """Project a store sample down to the products the operator kept.

    The scene generator used to receive every product the screenshots happened
    to contain, so an off-direction item could become a scene of its own. It now
    gets the kept list, plus the names of what was ruled out so it cannot quietly
    bring them back.
    """
    kept = [entry for entry in review["entries"] if not entry["excluded"]]
    return {
        "schema": SCHEMA_ANALYSIS_INPUT,
        "store": sample.get("store") or {},
        "store_direction": direction,
        "direction_note": DIRECTION_NOTE,
        "observed_product_clues": [
            {
                "clue": entry["clue"],
                "role": entry["role"],
                "confidence": entry["confidence"],
                "evidence": entry["evidence"],
            }
            for entry in kept
        ],
        "excluded_product_clues": [
            {"clue": entry["clue"], "reason": "运营手动排除"}
            for entry in review["entries"]
            if entry["excluded"]
        ],
        "limitations": list(sample.get("limitations") or []),
        "business_context": business_for_analysis(sample.get("business_context"), excluded_clues(review)),
    }


def find_purity_violations(analysis: dict, excluded: list[str]) -> list[dict]:
    """Report product needs that mention a clue the operator excluded.

    A soft signal, never a reason to throw away a result that has already been
    paid for. Matching is heuristic, so an alias the model invented can still
    slip through, and a neighbouring accessory may be flagged for a glance.
    """
    violations = []
    for scene in analysis.get("scenes") or []:
        for product in scene.get("product_needs") or []:
            text = product_text(product)
            violations.extend(
                {
                    "scene_name": scene.get("scene_name"),
                    "product_cn": product.get("product_cn"),
                    "excluded_clue": name,
                }
                for name in excluded
                if mentions(text, name)
            )
    return violations


def product_text(product: dict) -> str:
    return " ".join(str(product.get(field, "")) for field in ("product_cn", "product_en"))


def mentions(text: str, name: str) -> bool:
    """Whether a product description refers to a clue, without needing an exact name.

    A latin run of the clue has to reappear (``CCTV``), while a Chinese run only
    has to be contained in one the model wrote (``存储卡`` inside ``监控存储卡``),
    because Chinese names get compounded rather than repeated verbatim.
    """
    text, name = str(text).lower(), str(name).lower()
    if set(_LATIN.findall(name)) & set(_LATIN.findall(text)):
        return True
    runs = _HAN.findall(text)
    return any(chunk in run for chunk in _HAN.findall(name) for run in runs)


def load_exclusions(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != SCHEMA_EXCLUSIONS:
        raise ValueError(f"invalid exclusions: {path}")
    excluded = value.get("excluded")
    if not isinstance(excluded, list):
        raise ValueError(f"invalid exclusions: {path}")
    return {str(name) for name in excluded}


def save_exclusions(path: Path, names: list[str]) -> None:
    write_json(path, {"schema": SCHEMA_EXCLUSIONS, "excluded": sorted(set(names))})


def load_custom_products(path: Path) -> list[dict]:
    """The products the operator added by hand, as they were written down."""
    if not path.is_file():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != SCHEMA_CUSTOM_PRODUCTS:
        raise ValueError(f"invalid custom products: {path}")
    products = value.get("products")
    if not isinstance(products, list):
        raise ValueError(f"invalid custom products: {path}")
    return [item for item in products if isinstance(item, dict)]


def normalize_custom_products(products: list[dict]) -> list[dict]:
    """One entry per name, in the order the operator added them.

    Shared with the route that saves them, so the comparison that decides
    whether anything actually changed is made against the same shape that
    reaches the file — otherwise a stray space would look like an edit and
    invalidate a confirmation for nothing.
    """
    seen: dict[str, dict] = {}
    for item in products:
        name = str(item.get("name_cn") or "").strip()
        if not name or name in seen:
            continue
        seen[name] = {"name_cn": name, "name_en": str(item.get("name_en") or "").strip()}
    return list(seen.values())


def save_custom_products(path: Path, products: list[dict]) -> None:
    write_json(path, {"schema": SCHEMA_CUSTOM_PRODUCTS,
                      "products": normalize_custom_products(products)})
