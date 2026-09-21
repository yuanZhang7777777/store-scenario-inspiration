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

    One answer per scene keeps each call small, and the conclusions are written
    last because they need every scene at once.
    """
    scenes, _ = analyze_scenes(source, key, scene_count=scene_count)
    frames = []
    for scene in scenes["scenes"]:
        result, _ = analyze_scene_products(
            source, scene, key, products_per_scene=products_per_scene)
        frames.append({"scene_name": scene["scene_name"], "products": result["products"]})
    synthesis, receipt = analyze_synthesis(
        source,
        [{**scene, "product_needs": frame["products"]}
         for scene, frame in zip(scenes["scenes"], frames)],
        key,
    )
    return assemble(scenes, frames, synthesis), receipt


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
