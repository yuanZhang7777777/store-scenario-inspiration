from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Sequence
import warnings

import numpy as np
import pytest

from store_scenario_inspiration.catalog.models import ProductFamilyDocument
from store_scenario_inspiration.catalog.vectors import ExactVectorIndex, build_vector_matrix


class RecordingProvider:
    model_id = "test-embedding-v1"
    dimension = 3

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        self.calls.append(tuple(texts))
        return np.asarray(
            [[len(text), text.count("户外"), 1.0] for text in texts], dtype=np.float32
        )


@pytest.fixture
def documents() -> tuple[ProductFamilyDocument, ...]:
    def document(main_sku: str, text: str, *, searchable: bool = True) -> ProductFamilyDocument:
        return ProductFamilyDocument(
            doc_id=f"main:{main_sku}", main_sku=main_sku, searchable=searchable,
            cn_names=(), en_aliases=(), leaf_categories=(), category_paths=(),
            vector_text_v1=text, children=(), quality_flags=(),
        )

    return (
        document("B-2", "商品名称：户外灯"),
        document("A-1", "商品名称：露营灯"),
        document("HIDDEN", "商品名称：不应调用", searchable=False),
    )


def test_unchanged_vector_text_reuses_embedding_and_writes_deterministic_artifact(
    tmp_path: Path, documents: tuple[ProductFamilyDocument, ...]
) -> None:
    provider = RecordingProvider()

    first = build_vector_matrix(documents, provider, tmp_path / "cache")
    second = build_vector_matrix(tuple(reversed(documents)), provider, tmp_path / "cache")

    assert provider.calls == [("商品名称：露营灯", "商品名称：户外灯")]
    assert first.main_skus == ("A-1", "B-2")
    assert first.rows == ("A-1", "B-2")
    np.testing.assert_array_equal(first.matrix, second.matrix)
    assert first.matrix.dtype == np.float32
    assert first.matrix_path.exists() and first.rows_path.exists()
    assert json.loads(first.rows_path.read_text(encoding="utf-8")) == ["A-1", "B-2"]


def test_same_text_in_distinct_documents_embeds_once_and_changed_model_or_text_misses(
    tmp_path: Path, documents: tuple[ProductFamilyDocument, ...]
) -> None:
    provider = RecordingProvider()
    duplicated_text = replace(documents[1], main_sku="C-3", doc_id="main:C-3", vector_text_v1=documents[0].vector_text_v1)

    build_vector_matrix((documents[0], duplicated_text), provider, tmp_path / "cache")
    build_vector_matrix((replace(documents[0], vector_text_v1="商品名称：新户外灯"),), provider, tmp_path / "cache")
    other_model = RecordingProvider()
    other_model.model_id = "test-embedding-v2"
    build_vector_matrix((documents[0],), other_model, tmp_path / "cache")

    assert provider.calls == [("商品名称：户外灯",), ("商品名称：新户外灯",)]
    assert other_model.calls == [("商品名称：户外灯",)]


def test_bad_or_wrong_dimension_cache_is_a_miss_not_a_result(
    tmp_path: Path, documents: tuple[ProductFamilyDocument, ...]
) -> None:
    provider = RecordingProvider()
    cache = tmp_path / "cache"
    build_vector_matrix((documents[0],), provider, cache)
    cached = next((cache / "embeddings").glob("*.npy"))
    cached.write_bytes(b"truncated")

    build_vector_matrix((documents[0],), provider, cache)
    assert provider.calls == [("商品名称：户外灯",), ("商品名称：户外灯",)]

    cached = next((cache / "embeddings").glob("*.npy"))
    np.save(cached, np.ones((4,), dtype=np.float32))
    build_vector_matrix((documents[0],), provider, cache)
    assert len(provider.calls) == 3


@pytest.mark.parametrize(
    "bad_cache",
    [
        np.ones((3,), dtype=np.float64),
        np.ones((3,), dtype=np.int32),
        np.asarray([1, np.inf, 1], dtype=np.float32),
    ],
)
def test_noncanonical_or_nonfinite_cache_is_recomputed_and_overwritten_as_float32(
    tmp_path: Path, documents: tuple[ProductFamilyDocument, ...], bad_cache: np.ndarray
) -> None:
    provider = RecordingProvider()
    cache = tmp_path / "cache"
    build_vector_matrix((documents[0],), provider, cache)
    cached = next((cache / "embeddings").glob("*.npy"))
    np.save(cached, bad_cache)

    build_vector_matrix((documents[0],), provider, cache)

    assert provider.calls == [("商品名称：户外灯",), ("商品名称：户外灯",)]
    reloaded = np.load(cached, allow_pickle=False)
    assert reloaded.dtype == np.dtype(np.float32)
    assert np.isfinite(reloaded).all()


def test_normalized_blank_vector_text_is_not_embedded(
    tmp_path: Path, documents: tuple[ProductFamilyDocument, ...]
) -> None:
    provider = RecordingProvider()
    blank = replace(documents[0], vector_text_v1=" \t\n ")

    artifact = build_vector_matrix((blank,), provider, tmp_path / "cache")

    assert provider.calls == []
    assert artifact.matrix.shape == (0, provider.dimension)


def test_npz_disguised_as_cache_entry_is_recomputed_and_atomically_replaced(
    tmp_path: Path, documents: tuple[ProductFamilyDocument, ...]
) -> None:
    provider = RecordingProvider()
    cache = tmp_path / "cache"
    build_vector_matrix((documents[0],), provider, cache)
    cached = next((cache / "embeddings").glob("*.npy"))
    with cached.open("wb") as stream:
        np.savez(stream, embedding=np.ones((3,), dtype=np.float32))

    build_vector_matrix((documents[0],), provider, cache)

    assert provider.calls == [("商品名称：户外灯",), ("商品名称：户外灯",)]
    reloaded = np.load(cached, allow_pickle=False)
    assert isinstance(reloaded, np.ndarray)
    assert reloaded.dtype == np.dtype(np.float32)


def test_vector_build_rejects_duplicate_identities_and_invalid_provider_outputs(
    tmp_path: Path, documents: tuple[ProductFamilyDocument, ...]
) -> None:
    provider = RecordingProvider()
    with pytest.raises(ValueError, match="duplicate main_sku"):
        build_vector_matrix((documents[0], replace(documents[0], doc_id="other")), provider, tmp_path / "cache")
    with pytest.raises(ValueError, match="duplicate document identity"):
        build_vector_matrix((documents[0], replace(documents[1], doc_id=documents[0].doc_id)), provider, tmp_path / "cache")

    class BadProvider(RecordingProvider):
        def embed(self, texts: Sequence[str]) -> np.ndarray:
            return np.asarray([1, 2, 3], dtype=np.float32)

    with pytest.raises(ValueError, match="shape"):
        build_vector_matrix((documents[0],), BadProvider(), tmp_path / "bad")


def test_empty_searchable_set_never_calls_provider(tmp_path: Path, documents: tuple[ProductFamilyDocument, ...]) -> None:
    provider = RecordingProvider()
    artifact = build_vector_matrix((documents[2],), provider, tmp_path / "cache")

    assert provider.calls == []
    assert artifact.matrix.shape == (0, 3)
    assert artifact.main_skus == ()


def test_exact_vector_search_normalizes_once_and_orders_ties_by_main_sku(tmp_path: Path) -> None:
    matrix_path = tmp_path / "vectors.npy"
    rows_path = tmp_path / "rows.json"
    np.save(matrix_path, np.asarray([[1, 0], [0, 0], [1, 0], [-1, 0]], dtype=np.float32))
    rows_path.write_text(json.dumps(["B", "ZERO", "A", "NEG"]), encoding="utf-8")

    index = ExactVectorIndex.load(matrix_path, rows_path)
    hits = index.search(np.asarray([4, 0], dtype=np.float64), limit=4)

    assert [(hit.main_sku, hit.score, hit.sources) for hit in hits] == [
        ("A", 1.0, ("vector",)), ("B", 1.0, ("vector",)),
        ("ZERO", 0.5, ("vector",)), ("NEG", 0.0, ("vector",)),
    ]


def test_exact_vector_search_handles_float32_max_without_overflow_or_nan(tmp_path: Path) -> None:
    matrix_path = tmp_path / "vectors.npy"
    rows_path = tmp_path / "rows.json"
    fmax = np.finfo(np.float32).max
    np.save(
        matrix_path,
        np.asarray([[fmax, fmax], [fmax, fmax], [-fmax, -fmax], [fmax, -fmax]], dtype=np.float32),
    )
    rows_path.write_text(json.dumps(["B", "A", "NEG", "ORTH"]), encoding="utf-8")

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        index = ExactVectorIndex.load(matrix_path, rows_path)
        hits = index.search(np.asarray([fmax, fmax], dtype=np.float32), limit=4)

    assert index._normalized_matrix.dtype == np.dtype(np.float32)
    assert np.isfinite(index._normalized_matrix).all()
    assert [hit.main_sku for hit in hits] == ["A", "B", "ORTH", "NEG"]
    assert hits[0].score == pytest.approx(1.0)
    assert hits[2].score == pytest.approx(0.5)
    assert hits[3].score == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize("query", [np.asarray([[1, 0]], dtype=np.float32), np.asarray([1, np.nan]), np.asarray([0, 0]), np.asarray([1, 2, 3])])
def test_exact_vector_search_rejects_invalid_queries(tmp_path: Path, query: np.ndarray) -> None:
    matrix_path = tmp_path / "vectors.npy"
    rows_path = tmp_path / "rows.json"
    np.save(matrix_path, np.eye(2, dtype=np.float32))
    rows_path.write_text('["A", "B"]', encoding="utf-8")
    index = ExactVectorIndex.load(matrix_path, rows_path)

    with pytest.raises(ValueError):
        index.search(query, limit=1)
    with pytest.raises(ValueError, match="positive integer"):
        index.search(np.asarray([1, 0]), limit=0)


@pytest.mark.parametrize(
    ("matrix", "rows", "message"),
    [
        (np.ones((2, 2), dtype=np.float32), ["A"], "row count"),
        (np.ones((2,), dtype=np.float32), ["A", "B"], "2D"),
        (np.ones((2, 2), dtype=np.float64), ["A", "B"], "float32"),
        (np.ones((2, 2), dtype=np.int32), ["A", "B"], "float32"),
        (np.asarray([[1, np.inf]], dtype=np.float32), ["A"], "finite"),
        (np.ones((2, 2), dtype=np.float32), ["A", "A"], "duplicate"),
    ],
)
def test_exact_vector_load_validates_artifact(tmp_path: Path, matrix: np.ndarray, rows: list[str], message: str) -> None:
    matrix_path = tmp_path / "vectors.npy"
    rows_path = tmp_path / "rows.json"
    np.save(matrix_path, matrix)
    rows_path.write_text(json.dumps(rows), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        ExactVectorIndex.load(matrix_path, rows_path)
