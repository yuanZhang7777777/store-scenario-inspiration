"""Local stock-aware batch retrieval using full ERP FTS and the Qdrant server."""

import argparse
from dataclasses import asdict
from datetime import UTC, datetime
import json
import os
import re
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from store_scenario_inspiration.catalog.qdrant import QdrantVectorIndex
from store_scenario_inspiration.catalog.english import build_english_keyword_index, english_document
from store_scenario_inspiration.catalog.retrieval import HybridRetriever, RetrievalQuery
from store_scenario_inspiration.catalog.stock import COUNTRIES, read_stock_snapshot
from store_scenario_inspiration.catalog.storage import CatalogStore
from store_scenario_inspiration.catalog.vectors import (
    EnglishFastEmbedEmbeddingProvider, _atomic_json, build_vector_matrix,
)
from store_scenario_inspiration.catalog.versioning import CatalogIndexManager
from store_scenario_inspiration.catalog.incremental import (
    initialize_daily_catalog, catalog_revision, import_products,
    pending_name_reviews, apply_name_reviews,
)

BASE = Path("E:/Project/store-assortment-copilot/var")
MODEL_CACHE = BASE / "models/fastembed"
TEXT_POLICY = "erp-english-aliases-v2-sourced-fallbacks"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preview-products", "update-products", "name-review",
                                          "apply-names", "update-stock", "index-stock", "search"))
    parser.add_argument("--index-root", type=Path, default=BASE / "catalog-full")
    parser.add_argument("--runtime-root", type=Path, default=BASE / "catalog-stock-en")
    parser.add_argument("--model-cache", type=Path, default=MODEL_CACHE)
    parser.add_argument("--daily-root", type=Path, default=BASE / "catalog-daily")
    parser.add_argument("--source", type=Path, help="Daily partial ERP export; absent rows are retained")
    parser.add_argument("--names", type=Path, help="Reviewed main-SKU names JSON")
    parser.add_argument("--stock", type=Path)
    parser.add_argument("--country")
    parser.add_argument("--requests", type=Path, help="JSON list: scene, query, expanded_queries")
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--top-k", type=int, default=50)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.top_k <= 50:
        parser.error("top-k must be between 1 and 50 per retrieval branch")
    # ponytail: one local reader/writer at a time; replace with service snapshots
    # when concurrent operators become an actual requirement.
    with CatalogIndexManager(args.runtime_root)._writer_lock():
        run(args, parser)


def run(args, parser):
    started = perf_counter()
    manager = CatalogIndexManager(args.index_root)
    path = manager.active_store_path()
    if path is None:
        parser.error("build the full ERP keyword catalog first")
    daily_path = args.daily_root / "catalog.sqlite3"
    if args.command in {"preview-products", "update-products", "name-review", "apply-names"}:
        args.daily_root.mkdir(parents=True, exist_ok=True)
        initialize_daily_catalog(path, daily_path)
        if args.command in {"preview-products", "update-products"}:
            if args.source is None:
                parser.error("product import requires --source")
            result = import_products(daily_path, args.source, apply=args.command == "update-products")
        elif args.command == "name-review":
            result = pending_name_reviews(daily_path)
        else:
            if args.names is None:
                parser.error("apply-names requires --names")
            result = apply_name_reviews(daily_path, json.loads(args.names.read_text(encoding="utf-8")))
        if args.output:
            _atomic_json(args.output, result)
            print(str(args.output))
        else:
            print(json.dumps(result, ensure_ascii=False))
        return
    if daily_path.exists():
        initialize_daily_catalog(path, daily_path)  # Verify it still belongs to this baseline.
        path = daily_path
    revision = catalog_revision(path)
    state_path = args.runtime_root / "stock-index.json"
    pending_path = args.runtime_root / "index-update-pending.json"
    with CatalogStore.open_readonly(path) as store:
        if args.command in {"index-stock", "update-stock"}:
            if args.stock is None:
                parser.error("inventory update requires --stock")
            sku_to_main = dict(store.connection.execute("SELECT sku, main_sku FROM children"))
            stock = read_stock_snapshot(args.stock, sku_to_main, allow_unmatched=True)
            del sku_to_main
            previous = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else None
            if previous and set(previous["stock"]["country_main_skus"]) != set(stock["country_main_skus"]):
                raise ValueError("snapshot country coverage changed; old stock remains active, inspect export first")
            mains = sorted({sku for values in stock["country_main_skus"].values() for sku in values})
            compatible = bool(previous and previous["catalog_version"] == store.manifest.version_id
                              and previous["embedding_model"] == EnglishFastEmbedEmbeddingProvider.model_id
                              and previous["dimension"] == EnglishFastEmbedEmbeddingProvider.dimension
                              and previous["vector_text_policy"] == TEXT_POLICY)
            old_documents = (json.loads(Path(previous["documents_file"]).read_text(encoding="utf-8"))
                             if compatible else [])
            old_text = {document["main_sku"]: document["vector_text"] for document in old_documents}
            # Zero stock is not a semantic deletion. Keep known vectors available
            # to the global branch; the current country snapshot controls stock.
            indexed_mains = sorted(set(mains) | set(old_text))
            documents = [store.get_document(sku) for sku in indexed_mains]
            if any(document is None for document in documents):
                raise ValueError("an available main SKU lacks an ERP document")
            documents = [english_document(document) for document in documents]
            missing_english = [document.main_sku for document in documents
                               if document.main_sku in mains and not document.searchable]
            ordered = [document for document in documents if document.searchable]
            changed = [document for document in ordered if old_text.get(document.main_sku) != document.vector_text_v1]
            removed = sorted(set(old_text) - {document.main_sku for document in ordered})
            interrupted = json.loads(pending_path.read_text(encoding="utf-8")) if pending_path.exists() else None
            if compatible and interrupted and interrupted["collection"] == previous["collection"]:
                touched = interrupted.get("mutated_main_skus")
                if not isinstance(touched, list) or any(not isinstance(sku, str) or not sku for sku in touched):
                    raise ValueError("interrupted update lacks touched IDs; preserve journal for explicit recovery")
                # Inputs can change between retries. Restore every desired vector
                # (from cache), plus remove unpublished IDs from the failed run.
                changed = ordered
                removed = sorted((set(removed) | set(touched)) - {doc.main_sku for doc in ordered})
            if args.command == "update-stock" and (
                not compatible or changed or removed or previous.get("catalog_revision", 0) != revision
                or pending_path.exists()
            ):
                raise ValueError("catalog/vector changes pending; run index-stock once, then update-stock can refresh inventory alone")
            snapshot_id = "stock_en_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S_") + uuid4().hex[:6]
            collection = previous["collection"] if compatible else snapshot_id
            refresh_keywords = (not compatible or bool(changed or removed)
                                or previous.get("catalog_revision", 0) != revision)
            keywords_path = (args.runtime_root / (snapshot_id + ".keywords.sqlite3") if refresh_keywords
                             else Path(previous["keywords_file"]))
            if refresh_keywords:
                build_english_keyword_index(store, keywords_path, documents)
            index = QdrantVectorIndex("http://127.0.0.1:6333", collection,
                                      EnglishFastEmbedEmbeddingProvider.dimension)
            vectors = None
            if changed or not compatible:
                os.environ["HF_HUB_OFFLINE"] = "1"
                provider = EnglishFastEmbedEmbeddingProvider(args.model_cache)
                vectors = build_vector_matrix(changed, provider, args.runtime_root / "embedding-cache")
                by_main = {document.main_sku: document for document in changed}
                changed = [by_main[sku] for sku in vectors.main_skus]
            if changed or removed or not compatible:
                # An interrupted in-place upsert blocks search until an idempotent
                # retry finishes. Never serve mixed keyword/vector revisions.
                _atomic_json(pending_path, {"collection": collection, "catalog_revision": revision,
                                           "mutated_main_skus": sorted({doc.main_sku for doc in changed} | set(removed))})
                if not compatible:
                    index.create(changed, vectors.matrix)
                elif changed:
                    index.upsert(changed, vectors.matrix)
                if removed:
                    index.delete(removed)
            documents_path = args.runtime_root / (snapshot_id + ".documents.json")
            _atomic_json(documents_path, [{"main_sku": document.main_sku,
                                          "vector_text": document.vector_text_v1,
                                          "english_names": list(document.en_aliases)} for document in ordered])
            state = {
                "catalog_version": store.manifest.version_id,
                "catalog_revision": revision,
                "collection": collection,
                "embedding_model": EnglishFastEmbedEmbeddingProvider.model_id,
                "dimension": EnglishFastEmbedEmbeddingProvider.dimension,
                "vector_text_policy": TEXT_POLICY,
                "main_sku_count": len(mains),
                "vector_count": len(ordered),
                "vector_upsert_count": len(changed),
                "vector_reused_count": len(ordered) - len(changed),
                "vector_deleted_count": len(removed),
                "missing_english_main_skus": missing_english,
                "documents_file": str(documents_path.resolve()),
                "keywords_file": str(keywords_path.resolve()),
                "stock": stock,
                "elapsed_seconds": round(perf_counter() - started, 3),
            }
            if previous:
                _atomic_json(args.runtime_root / (snapshot_id + ".previous-state.json"), previous)
            _atomic_json(state_path, state)
            pending_path.unlink(missing_ok=True)
            if args.output:
                _atomic_json(args.output, state)
            print(json.dumps({key: value for key, value in state.items() if key != "stock"}, ensure_ascii=False))
            print(json.dumps({country: len(skus) for country, skus in stock["country_main_skus"].items()}))
            if stock.get("unmatched_positive_main_skus"):
                print(json.dumps({"warning": "inventory activated with unmatched ERP records retained separately",
                                  "unmatched_positive_main_skus": stock["unmatched_positive_main_skus"]}, ensure_ascii=False))
            return

        if not state_path.exists():
            parser.error("run index-stock first")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if pending_path.exists():
            raise ValueError("an index update was interrupted; rerun index-stock before searching")
        if state.get("catalog_revision", 0) != revision:
            raise ValueError("daily catalog changed; run index-stock before searching")
        if state["catalog_version"] != store.manifest.version_id:
            raise ValueError("ERP version changed; run index-stock before searching")
        if (state["embedding_model"] != EnglishFastEmbedEmbeddingProvider.model_id
                or state["dimension"] != EnglishFastEmbedEmbeddingProvider.dimension
                or state["vector_text_policy"] != TEXT_POLICY):
            raise ValueError("query embedding model does not match the stored vectors")
        country = COUNTRIES.get(args.country, args.country)
        if country not in state["stock"]["country_main_skus"]:
            parser.error("select a country covered by the stock snapshot")
        requests = json.loads(args.requests.read_text(encoding="utf-8")) if args.requests else [
            {"scene": "", "query": query, "expanded_queries": []} for query in args.query
        ]
        if not isinstance(requests, list) or not requests:
            parser.error("provide --query or a nonempty --requests JSON list")
        for request in requests:
            if not isinstance(request, dict) or not isinstance(request.get("query"), str) or not request["query"].strip():
                parser.error("each request needs a nonempty query string")
            if not isinstance(request.get("expanded_queries", []), list) or any(
                not isinstance(value, str) or not value.strip() for value in request.get("expanded_queries", [])
            ):
                parser.error("expanded_queries must be a list of nonempty strings")
            request["query"] = RetrievalQuery(request["query"]).text
            request["expanded_queries"] = [RetrievalQuery(value).text for value in request.get("expanded_queries", [])]
            for value in (request["query"], *request["expanded_queries"]):
                if re.search(r"[\u3400-\u9fff]", value) or not re.search(r"[A-Za-z]", value):
                    parser.error("use English query/expanded_queries; keep Chinese labels in scene/product_label")
        texts = list(dict.fromkeys(text for request in requests for text in (
            request["query"], *request.get("expanded_queries", []),
        )))
        os.environ["HF_HUB_OFFLINE"] = "1"
        provider = EnglishFastEmbedEmbeddingProvider(args.model_cache)
        embedding_started = perf_counter()
        vectors = dict(zip(texts, provider.embed_queries(texts), strict=True))
        embedding_ms = round((perf_counter() - embedding_started) * 1000, 2)
        index = QdrantVectorIndex("http://127.0.0.1:6333", state["collection"], state["dimension"])
        store.use_english_keyword_index(state["keywords_file"])
        retriever = HybridRetriever(store, index, english_only=True)
        allowed = tuple(state["stock"]["country_main_skus"][country])
        allowed_set = set(allowed)
        rows, details = [], []
        for request in requests:
            query_started = perf_counter()
            query = RetrievalQuery(request["query"], country=country)
            expanded = tuple(request.get("expanded_queries", []))
            options = dict(expanded_queries=expanded, expanded_query_vectors=tuple(vectors[text] for text in expanded))
            all_hits = retriever.search(query, vectors[query.text], args.top_k, **options)
            stock_hits = retriever.search(query, vectors[query.text], args.top_k, main_skus=allowed, **options)
            main_skus = list(dict.fromkeys(hit.main_sku for hit in (*all_hits, *stock_hits)))
            stocked = list(dict.fromkeys([hit.main_sku for hit in stock_hits] + [sku for sku in main_skus if sku in allowed_set]))
            rows.append({"场景": request.get("scene", ""), "相关产品关键词": request.get("product_label", query.text),
                         "ERP主SKU": main_skus, "目标国家有货主SKU": stocked})
            details.append({"query": query.text, "retrieval_ms": round((perf_counter() - query_started) * 1000, 2),
                            "country_hits": [dict(asdict(hit), product_names=list(store.get_document(hit.main_sku).cn_names),
                                                  english_names=list(english_document(store.get_document(hit.main_sku)).en_aliases)) for hit in stock_hits]})
        result = {"result_stage": "high_recall_candidates", "reranked": False,
                  "country": country, "catalog_version": state["catalog_version"],
                  "catalog_revision": revision,
                  "embedding_model": state["embedding_model"], "dimension": state["dimension"],
                  "vector_text_policy": state["vector_text_policy"],
                  "stock_source_sha256": state["stock"]["source_sha256"],
                  "stock_source_modified_at": state["stock"]["source_modified_at"],
                  "stock_basis": state["stock"]["stock_basis"],
                  "stock_erp_mapping_complete": state["stock"].get("erp_mapping_complete", True),
                  "stock_unmatched_positive_rows": state["stock"].get("unmatched_positive_rows", []),
                  "collection": state["collection"], "embedding_batch_ms": embedding_ms,
                  "total_seconds": round(perf_counter() - started, 3), "rows": rows, "debug": details}
        if args.output:
            _atomic_json(args.output, result)
            print(str(args.output))
        else:
            print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
