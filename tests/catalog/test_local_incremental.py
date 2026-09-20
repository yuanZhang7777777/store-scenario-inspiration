"""One acceptance check for inventory-only refresh without embedding work."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import numpy as np

from store_scenario_inspiration.catalog.english import english_document
from store_scenario_inspiration.catalog.models import BuildManifest, ChildVariant, ProductFamilyDocument
from store_scenario_inspiration.catalog.storage import CatalogStore


def test_stock_only_reuses_vectors_and_blocks_pending_updates(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("local_catalog", Path(__file__).parents[2] / "scripts/local_catalog.py")
    local = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(local)
    document = ProductFamilyDocument("main:A", "A", True, ("露营灯",), ("Camping lantern",),
                                     (), (), "商品名称：露营灯", (ChildVariant("a1", "露营灯", "", ()),), ())
    database = tmp_path / "catalog.sqlite3"
    manifest = BuildManifest("baseline", "hash", "2", "4", "none", "2026-09-16", 1, "absent")
    with CatalogStore.create(database, (document,), manifest):
        pass
    old_docs = tmp_path / "old.documents.json"
    old_docs.write_text(json.dumps([{"main_sku": "A", "vector_text": english_document(document).vector_text_v1}]), encoding="utf-8")
    state_path = tmp_path / "stock-index.json"
    initial = {"catalog_version": "baseline", "collection": "keep_me", "dimension": 1024,
               "embedding_model": "BAAI/bge-large-en-v1.5", "vector_text_policy": local.TEXT_POLICY,
               "documents_file": str(old_docs), "keywords_file": "unused.sqlite3",
               "stock": {"country_main_skus": {"PH": ["A"]}}}
    state_path.write_text(json.dumps(initial), encoding="utf-8")
    monkeypatch.setattr(local, "CatalogIndexManager", lambda _: SimpleNamespace(active_store_path=lambda: database))
    monkeypatch.setattr(local, "read_stock_snapshot", lambda *_, **__: {"country_main_skus": {"PH": []}, "source_sha256": "new"})
    monkeypatch.setattr(local.EnglishFastEmbedEmbeddingProvider, "__init__", lambda *_: pytest.fail("inventory update loaded model"))
    monkeypatch.setattr(local, "build_english_keyword_index", lambda *_: pytest.fail("inventory update rebuilt keyword index"))
    args = SimpleNamespace(command="update-stock", index_root=tmp_path, runtime_root=tmp_path,
                           daily_root=tmp_path / "no-daily", stock=Path("stock.xlsx"), output=None)
    local.run(args, None)
    updated = json.loads(state_path.read_text(encoding="utf-8"))
    assert updated["collection"] == "keep_me"
    assert updated["vector_upsert_count"] == 0 and updated["vector_reused_count"] == 1
    assert updated["stock"]["country_main_skus"] == {"PH": []}
    assert updated["vector_count"] == 1  # zero stock does not delete product vectors
    pending = tmp_path / "index-update-pending.json"
    pending.write_text('{}', encoding="utf-8")
    args.command = "search"
    with pytest.raises(ValueError, match="interrupted"):
        local.run(args, None)
    # A failed prior update touched A and added B. Even if inputs reverted,
    # retry restores A and removes unpublished B before clearing the journal.
    pending.write_text(json.dumps({"collection": "keep_me", "mutated_main_skus": ["A", "B"]}), encoding="utf-8")
    args.command = "index-stock"
    args.model_cache = tmp_path
    calls = []
    monkeypatch.setattr(local.EnglishFastEmbedEmbeddingProvider, "__init__", lambda *_: None)
    monkeypatch.setattr(local, "build_english_keyword_index", lambda *_: None)
    monkeypatch.setattr(local, "build_vector_matrix", lambda docs, *_: SimpleNamespace(
        main_skus=tuple(doc.main_sku for doc in docs), matrix=np.ones((len(docs), 1024), dtype=np.float32)))
    monkeypatch.setattr(local.QdrantVectorIndex, "upsert", lambda _, docs, matrix: calls.append(("upsert", [doc.main_sku for doc in docs])))
    monkeypatch.setattr(local.QdrantVectorIndex, "delete", lambda _, skus: calls.append(("delete", skus)))
    local.run(args, None)
    assert calls == [("upsert", ["A"]), ("delete", ["B"])]
    assert not pending.exists()
