"""Join analysis, raw retrieval, soft rerank and country availability."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def balanced_unique(groups: list[list[dict]], limit: int) -> list[dict]:
    """Round-robin related candidates so every product need is represented."""
    selected, seen = [], set()
    for rank in range(max((len(group) for group in groups), default=0)):
        for group in groups:
            if rank >= len(group):
                continue
            row = group[rank]
            if row["main_sku"] in seen:
                continue
            seen.add(row["main_sku"])
            selected.append(row)
            if len(selected) == limit:
                return selected
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--retrieval", type=Path, required=True)
    parser.add_argument("--rerank", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-recommendations", type=int, default=300)
    parser.add_argument("--min-relevance", type=int, choices=(1, 2, 3), default=2)
    args = parser.parse_args()
    if args.max_recommendations < 1:
        parser.error("--max-recommendations must be positive")
    analysis, retrieval, rerank = read(args.analysis), read(args.retrieval), read(args.rerank)
    inventory_coverage = retrieval.get("inventory_coverage", "available")
    reranked = {
        (scene["scene_name"], product["product_cn"], product["product_en"]): product["ranked_candidates"]
        for scene in rerank["scenes"] for product in scene["products"]
    }
    related_groups = []
    scenes = []
    for scene in retrieval["scenes"]:
        output_scene = {"scene_name": scene["scene_name"], "products": []}
        for product in scene["products"]:
            key = (scene["scene_name"], product["product_cn"], product["product_en"])
            ranking = reranked.get(key, [])
            by_sku = {row["main_sku"]: row for row in product["global_candidates"]}
            related, country = [], []
            for judgment in ranking:
                if judgment["relevance"] < args.min_relevance:
                    continue
                row = by_sku.get(judgment["main_sku"])
                if row is None:
                    continue
                merged = {**row, "relevance": judgment["relevance"],
                          "rerank_reason": judgment.get("reason", "")}
                related.append(merged)
                if merged.get("country_available"):
                    country.append(merged)
            related_groups.append(related)
            output_scene["products"].append({
                "product_cn": product["product_cn"],
                "product_en": product["product_en"],
                "canonical_cn": product["canonical_cn"],
                "canonical_en": product["canonical_en"],
                "expanded_cn": product["expanded_cn"],
                "expanded_en": product["expanded_en"],
                "related_candidates": related,
                "country_recommendations": country,
                "raw_global_candidates": product["global_candidates"],
            })
        scenes.append(output_scene)
    result = {
        "model": analysis["model"],
        "country": retrieval["country"],
        "inventory_coverage": inventory_coverage,
        "analysis": analysis,
        "scenes": scenes,
        "recommended_main_skus": balanced_unique(related_groups, args.max_recommendations),
        "audit": {
            "raw_retrieval_preserved": True,
            "recommendation_rule": "semantic relevance after rerank; balanced across product needs",
            "minimum_relevance": args.min_relevance,
            "country_rule": "country inventory is annotated and does not remove semantic recommendations",
            "inventory_coverage": inventory_coverage,
            "reranker": rerank["model"],
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
