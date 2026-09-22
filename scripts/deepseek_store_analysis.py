"""Generate one store analysis or expansion pass, without persisting credentials.

The prompts and the validation live in
``store_scenario_inspiration.pipeline.analysis``; this is the command line that
drives them over one input file.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path

from store_scenario_inspiration.pipeline.analysis import (
    analyze_expansions,
    analyze_scene_products,
    analyze_scenes,
    analyze_synthesis,
    assemble,
)


def run_analysis(source: dict, key: str, *, scene_count: int, products_per_scene: int) -> tuple[dict, dict]:
    """The same three calls the app makes, so both paths pay for the same thing.

    The conclusions come first, because the scenes are written from them. One
    answer per scene keeps each call small, so no single answer can lose the
    whole store.
    """
    synthesis, receipt = analyze_synthesis(source, key)
    scenes, scene_receipt = analyze_scenes(
        source, key, scene_count=scene_count, conclusion=synthesis)
    frames = []
    for scene in scenes["scenes"]:
        result, product_receipt = analyze_scene_products(
            source, scene, key, products_per_scene=products_per_scene)
        frames.append({"scene_name": scene["scene_name"], "products": result["products"]})
        receipt = _add_usage(receipt, product_receipt)
    return assemble(scenes, frames, synthesis), _add_usage(receipt, scene_receipt)


def _add_usage(receipt: dict, other: dict) -> dict:
    """One receipt for the whole run, so the printed spend is the run's spend."""
    total = dict(receipt)
    usage = dict(total.get("usage") or {})
    for field, value in (other.get("usage") or {}).items():
        usage[field] = usage.get(field, 0) + value if isinstance(value, int) else value
    total["usage"] = usage
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expand", action="store_true")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scene-count", type=int, default=8)
    parser.add_argument("--products-per-scene", type=int, default=10)
    parser.add_argument("--expansion-terms", type=int, default=6)
    args = parser.parse_args()
    source = json.loads(args.input.read_text(encoding="utf-8"))
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip() or getpass.getpass("DeepSeek API key: ").strip()
    if args.expand:
        result, receipt = analyze_expansions(source, key, expansion_terms=args.expansion_terms)
    else:
        result, receipt = run_analysis(
            source, key, scene_count=args.scene_count,
            products_per_scene=args.products_per_scene,
        )
    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(output), "scenes": len(result["scenes"]), "usage": receipt["usage"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
