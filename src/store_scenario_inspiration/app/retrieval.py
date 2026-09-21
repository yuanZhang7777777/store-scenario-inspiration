"""Turn each scene product into a ranked list of catalogue SKUs.

Four channels run per product — Chinese and English, each in a keyword and a
vector flavour — and their rankings are fused with reciprocal rank fusion. The
fused score is the whole answer: there is no model rerank pass, because ranking
a few hundred candidates with an LLM costs money and minutes to reorder a list
the operator is about to read top-down anyway.

The operator picks from this list, so recall matters more than precision: the
point is to put the right SKU somewhere in the visible range, not to be sure
about the first one.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

from store_scenario_inspiration.catalog.bilingual_vectors import (
    LANGUAGES,
    load_encoder,
    load_products,
    load_vector_index,
)
from store_scenario_inspiration.catalog.pilot import (
    build_fts,
    fuse_rankings,
    keyword_search,
    load_inventory_context,
)

from .params import STOCK_IN_ONLY, SearchParams


SCHEMA_RETRIEVAL = "store-retrieval-v1"

CHANNEL_LIMIT = 100
GLOBAL_LIMIT = 400


def flatten_expansions(expansions: dict) -> list[dict]:
    """One dict per product role, each carrying the scene it came from."""
    products = []
    for scene in expansions.get("scenes") or []:
        for product in scene.get("products") or []:
            products.append({**product, "scene_name": scene.get("scene_name")})
    return products


def _queries(product: dict) -> dict[str, list[str]]:
    """The terms one product contributes, per language, without duplicates."""
    fields = {
        "cn": ("canonical_cn", "product_cn", "expanded_cn"),
        "en": ("canonical_en", "product_en", "expanded_en"),
    }
    queries = {}
    for language, names in fields.items():
        seen: list[str] = []
        for name in names:
            value = product.get(name)
            values = value if isinstance(value, list) else [value]
            for item in values:
                text = str(item or "").strip()
                if text and text not in seen:
                    seen.append(text)
        queries[language] = seen
    return queries


def _rankings(queries: dict[str, list[str]], fts, index, encoder) -> list[tuple]:
    """One ranking per channel per query term, ready for fusion."""
    rankings: list[tuple] = []
    for language in LANGUAGES:
        terms = queries[language]
        if not terms:
            continue
        for term in terms:
            hits = keyword_search(fts, language, term, CHANNEL_LIMIT)
            if hits:
                rankings.append((f"{language}_keyword", term, hits))
        vectors = encoder.encode(index.model_ids[language], terms)
        for term, vector in zip(terms, vectors, strict=True):
            hits = index.search(language, vector, CHANNEL_LIMIT)
            if hits:
                rankings.append((f"{language}_vector", term, hits))
    return rankings


@contextmanager
def _keyword_index(asset_db: Path) -> Iterator[sqlite3.Connection]:
    connection = build_fts(load_products(str(asset_db)))
    try:
        yield connection
    finally:
        connection.close()


def _child_to_main(catalogue: dict[str, dict]) -> dict[str, str]:
    """The child-to-main SKU map the stock workbook is keyed by."""
    mapping: dict[str, str] = {}
    for main_sku, product in catalogue.items():
        metadata = product.get("inventory_metadata_json") or "{}"
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        for child in metadata.get("child_skus") or []:
            mapping[str(child).strip()] = main_sku
    return mapping


def _inventory(stock_path: Path, country: str, catalogue: dict[str, dict]):
    """Country stock, or an explicit "not checked" when there is nothing to read."""
    if not Path(stock_path).is_file():
        return country.strip().upper(), "unavailable", None
    return load_inventory_context(Path(stock_path), country, _child_to_main(catalogue))


def _annotate(fused: list[dict], stock: dict[str, float] | None) -> None:
    """Write each candidate's country availability in place, dropping nothing.

    Filtering here would silently delete the head of the list whenever the
    semantically closest SKUs happen to be out of stock locally — the operator
    then sees a short page full of near-misses and no way to tell why.
    """
    for candidate in fused:
        quantity = 0.0 if stock is None else stock.get(str(candidate["main_sku"]), 0.0)
        candidate["country_available_quantity"] = round(quantity, 4) if quantity > 0 else None
        candidate["country_available"] = None if stock is None else quantity > 0


def retrieve_store(
    *,
    asset_db: Path,
    vector_cache: Path,
    model_cache: Path,
    stock_path: Path,
    country: str,
    products: list[dict],
    params: SearchParams,
) -> dict:
    """Rank catalogue SKUs for every product role in the store's scenes."""
    catalogue = load_products(str(asset_db))
    index = load_vector_index(str(vector_cache))
    encoder = load_encoder(str(model_cache))
    country_code, coverage, stock = _inventory(stock_path, country, catalogue)

    scenes = []
    with _keyword_index(asset_db) as fts:
        for product in products:
            queries = _queries(product)
            fused = fuse_rankings(catalogue, _rankings(queries, fts, index, encoder), GLOBAL_LIMIT)
            _annotate(fused, stock)
            if stock is not None and params.stock_filter == STOCK_IN_ONLY:
                fused = [candidate for candidate in fused if candidate["country_available"]]
            kept = fused[: params.recall_limit]
            # Ranks are renumbered over what is actually shown, so the list the
            # operator reads never starts at 15 or skips numbers.
            for rank, candidate in enumerate(kept, start=1):
                candidate["rank"] = rank
                candidate.pop("evidence", None)
            scenes.append({
                "scene_name": product.get("scene_name"),
                "product_cn": product.get("product_cn"),
                "product_en": product.get("product_en"),
                "queries": queries,
                "candidates": kept,
            })
    return {
        "schema": SCHEMA_RETRIEVAL,
        "country": country_code,
        "inventory": coverage,
        "scenes": scenes,
    }
