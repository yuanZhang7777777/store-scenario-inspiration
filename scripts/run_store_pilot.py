"""Run model expansions through the same bilingual hybrid retrieval pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter

import numpy as np

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE / "src"))

from store_scenario_inspiration.catalog.pilot import (  # noqa: E402
    DEFAULT_ASSET_DB,
    DEFAULT_COLLECTION,
    DEFAULT_QDRANT,
    DEFAULT_STOCK,
    build_fts,
    child_main_map,
    embed_queries,
    filter_country,
    fuse_rankings,
    keyword_search,
    load_inventory_context,
    load_products,
    qdrant_search,
)


def load_expansions(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value.get("model"), str) or not isinstance(value.get("scenes"), list):
        raise ValueError(f"invalid expansion file: {path}")
    for scene in value["scenes"]:
        products = scene.get("products", scene.get("product_needs"))
        if not isinstance(scene.get("scene_name"), str) or not isinstance(products, list):
            raise ValueError(f"invalid scene: {path}")
        scene["products"] = products
        for product in products:
            for key in ("product_cn", "product_en", "canonical_cn", "canonical_en"):
                if not isinstance(product.get(key), str) or not product[key].strip():
                    raise ValueError(f"missing {key}: {path}")
            for key in ("expanded_cn", "expanded_en"):
                if not isinstance(product.get(key), list) or len(product[key]) > 6:
                    raise ValueError(f"invalid {key}: {path}")
    return value


def terms(product: dict, language: str) -> list[tuple[str, float]]:
    canonical = product[f"canonical_{language}"].strip()
    expansions = [str(term).strip() for term in product[f"expanded_{language}"]]
    return [(canonical, 1.0), *[(term, 0.7) for term in dict.fromkeys(expansions) if term and term != canonical]]


def query_vectors(asset_db: Path, queries: dict[str, list[str]], output_dir: Path):
    signature = hashlib.sha256(json.dumps(
        {"schema": "bge-large-v1.5-1024", "queries": queries},
        ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")).hexdigest()[:16]
    cache = output_dir / f"query-vectors-{signature}.npz"
    if cache.exists():
        saved = np.load(cache, allow_pickle=False)
        vectors = saved["vectors"]
        keys = list(zip(saved["languages"].tolist(), saved["texts"].tolist(), strict=True))
        if vectors.shape != (len(keys), 1024):
            raise ValueError(f"invalid query vector cache: {cache}")
        return dict(zip(keys, vectors, strict=True))
    vectors = embed_queries(asset_db, queries)
    keys = list(vectors)
    np.savez_compressed(
        cache,
        languages=np.array([key[0] for key in keys]),
        texts=np.array([key[1] for key in keys]),
        vectors=np.stack([vectors[key] for key in keys]),
    )
    return vectors


def resolve_outputs(inputs: list[tuple[Path, dict]], outputs: list[Path] | None,
                    output_dir: Path) -> list[Path]:
    paths = outputs or [
        output_dir / f"{value['model'].replace('.', '').replace('-', '_')}_retrieval.json"
        for _, value in inputs
    ]
    if len(paths) != len(inputs):
        raise ValueError("--output count must equal --expansions count")
    if len({path.resolve() for path in paths}) != len(paths):
        raise ValueError("retrieval output paths must be unique")
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expansions", type=Path, action="append", required=True)
    parser.add_argument("--country", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, action="append",
                        help="Explicit output path paired by order with each --expansions")
    parser.add_argument("--asset-db", type=Path, default=DEFAULT_ASSET_DB)
    parser.add_argument("--stock", type=Path, default=DEFAULT_STOCK)
    parser.add_argument("--qdrant", default=DEFAULT_QDRANT)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--channel-limit", type=int, default=100)
    parser.add_argument("--global-limit", type=int, default=80)
    parser.add_argument("--country-limit", type=int, default=40)
    parser.add_argument("--prepare-vectors-only", action="store_true")
    args = parser.parse_args()

    started = perf_counter()
    inputs = [(path, load_expansions(path)) for path in args.expansions]
    try:
        outputs = resolve_outputs(inputs, args.output, args.output_dir)
    except ValueError as error:
        parser.error(str(error))
    query_texts = {"cn": [], "en": []}
    for _, value in inputs:
        for scene in value["scenes"]:
            for product in scene["products"]:
                for language in ("cn", "en"):
                    query_texts[language].extend(text for text, _ in terms(product, language))
    query_texts = {language: list(dict.fromkeys(values)) for language, values in query_texts.items()}

    args.output_dir.mkdir(parents=True, exist_ok=True)
    products = load_products(args.asset_db)
    vectors = query_vectors(args.asset_db, query_texts, args.output_dir)
    if args.prepare_vectors_only:
        print(json.dumps({"cached_query_vectors": len(vectors)}, ensure_ascii=False))
        return
    country, inventory_coverage, stock = load_inventory_context(
        args.stock, args.country, child_main_map(products)
    )
    fts = build_fts(products)
    try:
        for (source_path, value), output in zip(inputs, outputs, strict=True):
            result = {
                "model": value["model"], "country": country,
                "inventory_coverage": inventory_coverage,
                "source": str(source_path), "scenes": [],
            }
            for scene in value["scenes"]:
                scene_result = {"scene_name": scene["scene_name"], "products": []}
                for product in scene["products"]:
                    rankings = []
                    for language in ("cn", "en"):
                        for query, weight in terms(product, language):
                            rankings.append((f"{language}_keyword", query,
                                             keyword_search(fts, language, query, args.channel_limit), weight))
                            rankings.append((f"{language}_vector", query,
                                             qdrant_search(args.qdrant, args.collection, language,
                                                            vectors[(language, query)], args.channel_limit), weight))
                    global_candidates = fuse_rankings(products, rankings, args.global_limit)
                    country_candidates = filter_country(global_candidates, stock, args.country_limit)
                    scene_result["products"].append({
                        **product,
                        "global_candidates": global_candidates,
                        "country_candidates": country_candidates,
                    })
                result["scenes"].append(scene_result)
            result["elapsed_total_seconds"] = round(perf_counter() - started, 3)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(output.resolve())
    finally:
        fts.close()


if __name__ == "__main__":
    main()
