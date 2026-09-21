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

from zipfile import BadZipFile
from store_scenario_inspiration.reliability import file_signature, stock_snapshot, DataChangedError


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
def _keyword_index(asset_db: Path, catalogue: dict | None = None) -> Iterator[sqlite3.Connection]:
    # Build keywords from the same in-memory catalogue used for fusion.
    connection = build_fts(catalogue if catalogue is not None else load_products(str(asset_db)))
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


def retrieve_store(*, asset_db: Path, vector_cache: Path, model_cache: Path,
                   stock_path: Path, country: str, products: list[dict], params: SearchParams) -> dict:
    """Preserve the four-channel policy, adding source consistency and explicit fallback."""
    if not products:
        raise ValueError('没有可匹配的商品需求，请先完成场景商品分析。')
    asset_revision, vector_revision = file_signature(asset_db), file_signature(vector_cache)
    catalogue = load_products(str(asset_db))
    index = load_vector_index(str(vector_cache))
    encoder = load_encoder(str(model_cache))
    stock_revision = file_signature(stock_path)
    warnings, snapshot = [], None
    try:
        snapshot = stock_snapshot(stock_path)
        country_code, coverage, stock = _inventory(stock_path, country, catalogue)
    except DataChangedError:
        raise
    except (OSError, ValueError, KeyError, TypeError, BadZipFile) as exc:
        country_code, coverage, stock = country.strip().upper(), 'unavailable', None
        warnings.append('库存未能核验，候选保留为待确认；错误类型：' + type(exc).__name__)
    if file_signature(stock_path) != stock_revision:
        raise DataChangedError('库存读取过程中发生更新，请在更新完成后重试。')
    if stock is None and params.stock_filter == STOCK_IN_ONLY:
        raise RuntimeError('当前无法核验目标国家库存，未将未知库存商品当作有货推荐。请更新库存后重试。')
    scenes = []
    with _keyword_index(asset_db, catalogue) as fts:
        for product in products:
            queries = _queries(product)
            if not any(queries.values()):
                raise ValueError('商品检索词为空，本次未发布不完整结果。')
            fused = fuse_rankings(catalogue, _rankings(queries, fts, index, encoder), GLOBAL_LIMIT)
            _annotate(fused, stock)
            before_stock = len(fused)
            if stock is not None and params.stock_filter == STOCK_IN_ONLY:
                fused = [candidate for candidate in fused if candidate['country_available']]
            kept = fused[:params.recall_limit]
            for rank, candidate in enumerate(kept, start=1):
                candidate['rank'] = rank
                candidate.pop('evidence', None)
            status = ('matched' if kept else 'no_stock_in_recalled_candidates' if before_stock
                      else 'no_candidates_in_recall_window')
            scenes.append({'scene_name': product.get('scene_name'), 'product_cn': product.get('product_cn'),
                           'product_en': product.get('product_en'), 'queries': queries, 'candidates': kept,
                           'match_status': status, 'candidate_count_before_stock': before_stock})
    if (file_signature(asset_db) != asset_revision or file_signature(vector_cache) != vector_revision
            or file_signature(stock_path) != stock_revision):
        raise DataChangedError('分析期间商品或库存数据已更新，本次未发布混合版本结果，请重新匹配。')
    return {'schema': SCHEMA_RETRIEVAL, 'country': country_code, 'inventory': coverage,
            'inventory_snapshot': snapshot if stock is not None else None,
            'source_revisions': {'catalogue': asset_revision, 'vectors': vector_revision},
            'warnings': warnings, 'scenes': scenes}
