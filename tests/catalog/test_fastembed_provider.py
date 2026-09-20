from __future__ import annotations

import sys
from types import SimpleNamespace

import numpy as np

from store_scenario_inspiration.catalog.models import ProductFamilyDocument
from store_scenario_inspiration.catalog.vectors import FastEmbedEmbeddingProvider, build_vector_matrix


def test_fastembed_provider_separates_document_and_query_embeddings(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, ...]] = []

    class FakeTextEmbedding:
        def __init__(self, *, model_name: str, cache_dir: str) -> None:
            assert model_name == "BAAI/bge-small-zh-v1.5"
            assert cache_dir == str(tmp_path / "model-cache")

        def embed(self, texts):
            calls.append(tuple(texts))
            return iter(np.ones(512, dtype=np.float32) for _ in calls[-1])

    monkeypatch.setitem(sys.modules, "fastembed", SimpleNamespace(TextEmbedding=FakeTextEmbedding))
    provider = FastEmbedEmbeddingProvider(tmp_path / "model-cache")

    document = ProductFamilyDocument(
        doc_id="main:A", main_sku="A", searchable=True,
        cn_names=(), en_aliases=(), leaf_categories=(), category_paths=(),
        vector_text_v1="商品名称：户外太阳能灯笼", children=(), quality_flags=(),
    )
    build_vector_matrix((document,), provider, tmp_path / "cache")
    assert provider.embed_queries(("露营椅",)).shape == (1, 512)
    assert calls == [("商品名称：户外太阳能灯笼",), ("为这个句子生成表示以用于检索相关文章：露营椅",)]
