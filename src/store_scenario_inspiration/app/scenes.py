"""Mark which of a scene's products the operator has ruled out.

Screenshots show what a store has *listed*, not what the catalogue can supply,
so a product missing from them says nothing about whether we can offer it. The
only product-level signal worth carrying into the report is the operator's own
exclusion, because a scene that quietly re-adds a ruled-out product is ignoring
an instruction rather than making an observation.
"""

from __future__ import annotations

from ..pipeline.clues import mentions, product_text


def annotate(analysis: dict, excluded: list[str]) -> list[dict]:
    """Attach the ruled-out product names to each scene."""
    scenes = []
    for scene in analysis.get("scenes") or []:
        hits = [
            product.get("product_cn")
            for product in scene.get("product_needs") or []
            if any(mentions(product_text(product), name) for name in excluded)
        ]
        scenes.append({**scene, "excluded": hits})
    return scenes
