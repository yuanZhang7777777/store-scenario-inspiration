"""Cached embedding artifacts and deterministic exact cosine retrieval."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
import os
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Protocol

import numpy as np

from .models import ProductFamilyDocument, SearchHit
from .normalize import normalize_compare


class EmbeddingProvider(Protocol):
    """Replaceable local or remote embedding implementation."""

    model_id: str
    dimension: int

    def embed(self, texts: Sequence[str]) -> np.ndarray: ...


class FastEmbedEmbeddingProvider:
    """Local BGE embeddings, with the BGE query instruction kept out of documents."""

    model_id = "BAAI/bge-small-zh-v1.5"
    dimension = 512
    _QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："

    def __init__(self, cache_dir: Path) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=self.model_id, cache_dir=str(Path(cache_dir)))

    def _embed(self, texts: Sequence[str]) -> np.ndarray:
        values = tuple(texts)
        if not values:
            return np.empty((0, self.dimension), dtype=np.float32)
        return np.asarray(list(self._model.embed(values)), dtype=np.float32)

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        return self._embed(texts)

    def embed_queries(self, texts: Sequence[str]) -> np.ndarray:
        return self._embed(tuple(self._QUERY_PREFIX + text for text in texts))

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """Compatibility with the original document-only provider protocol."""
        return self.embed_documents(texts)


class EnglishFastEmbedEmbeddingProvider(FastEmbedEmbeddingProvider):
    """Approved 1024-dimensional English model; legacy Chinese provider is unchanged."""

    model_id = "BAAI/bge-large-en-v1.5"
    dimension = 1024
    _QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

    def __init__(self, cache_dir: Path) -> None:
        from fastembed import TextEmbedding

        # ponytail: 2 CPU threads / batch 4 bound this laptop's memory; tune after measurement.
        self._model = TextEmbedding(model_name=self.model_id, cache_dir=str(cache_dir),
                                    threads=2, providers=["CPUExecutionProvider"], local_files_only=True)

    def _embed(self, texts: Sequence[str]) -> np.ndarray:
        values = tuple(texts)
        if not values:
            return np.empty((0, self.dimension), dtype=np.float32)
        vectors = []
        for count, vector in enumerate(self._model.embed(values, batch_size=4), start=1):
            vectors.append(vector)
            if len(values) >= 100 and (count % 128 == 0 or count == len(values)):
                print(f"english_embeddings={count}/{len(values)}", file=sys.stderr, flush=True)
        return np.asarray(vectors, dtype=np.float32)


@dataclass(frozen=True)
class VectorArtifact:
    """A complete matrix plus its durable, stable row mapping."""

    matrix: np.ndarray
    matrix_path: Path
    rows_path: Path
    rows: tuple[str, ...]
    main_skus: tuple[str, ...]


def _cache_key(model_id: str, vector_text: str) -> str:
    return sha256((model_id + "\0" + vector_text).encode("utf-8")).hexdigest()


def _atomic_npy(path: Path, value: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
        ) as temporary:
            temporary_name = temporary.name
            np.save(temporary, value, allow_pickle=False)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
        ) as temporary:
            temporary_name = temporary.name
            json.dump(value, temporary, ensure_ascii=False, separators=(",", ":"))
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _cached_embedding(path: Path, dimension: int) -> np.ndarray | None:
    candidate: object | None = None
    try:
        candidate = np.load(path, allow_pickle=False)
        if not isinstance(candidate, np.ndarray):
            return None
        if candidate.dtype != np.dtype(np.float32):
            return None
        if candidate.shape != (dimension,) or not np.isfinite(candidate).all():
            return None
        return candidate
    except Exception:
        return None
    finally:
        close = getattr(candidate, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def _validate_provider_vectors(result: object, misses: int, dimension: int) -> np.ndarray:
    try:
        vectors = np.asarray(result, dtype=np.float32)
    except (TypeError, ValueError) as error:
        raise ValueError("provider embeddings must be convertible to float32") from error
    if vectors.shape != (misses, dimension):
        raise ValueError(
            f"provider embeddings must have shape ({misses}, {dimension}), got {vectors.shape}"
        )
    if not np.isfinite(vectors).all():
        raise ValueError("provider embeddings must contain only finite values")
    return vectors


def _ordered_documents(
    documents: Iterable[ProductFamilyDocument],
) -> tuple[ProductFamilyDocument, ...]:
    materialized = tuple(documents)
    seen_skus: set[str] = set()
    seen_document_ids: set[str] = set()
    for document in materialized:
        main_sku = normalize_compare(document.main_sku)
        document_id = normalize_compare(document.doc_id)
        if not main_sku:
            raise ValueError("document main_sku must contain text")
        if not document_id:
            raise ValueError("document identity must contain text")
        if main_sku in seen_skus:
            raise ValueError(f"duplicate main_sku: {main_sku}")
        if document_id in seen_document_ids:
            raise ValueError(f"duplicate document identity: {document_id}")
        seen_skus.add(main_sku)
        seen_document_ids.add(document_id)
    return tuple(
        sorted(
            (
                document
                for document in materialized
                if document.searchable and normalize_compare(document.vector_text_v1)
            ),
            key=lambda document: (normalize_compare(document.main_sku), document.main_sku),
        )
    )


def build_vector_matrix(
    documents: Iterable[ProductFamilyDocument],
    provider: EmbeddingProvider,
    cache_dir: Path,
) -> VectorArtifact:
    """Embed eligible documents once and publish a self-contained matrix artifact."""

    if not isinstance(provider.dimension, int) or isinstance(provider.dimension, bool) or provider.dimension <= 0:
        raise ValueError("provider dimension must be a positive integer")
    if not isinstance(provider.model_id, str) or not provider.model_id:
        raise ValueError("provider model_id must contain text")
    root = Path(cache_dir)
    eligible = _ordered_documents(documents)
    embedding_dir = root / "embeddings"
    vectors_by_key: dict[str, np.ndarray] = {}
    text_by_key: dict[str, str] = {}
    for document in eligible:
        key = _cache_key(provider.model_id, document.vector_text_v1)
        text_by_key.setdefault(key, document.vector_text_v1)

    misses: list[str] = []
    for key, text in text_by_key.items():
        cached = _cached_embedding(embedding_dir / f"{key}.npy", provider.dimension)
        if cached is None:
            misses.append(key)
        else:
            vectors_by_key[key] = cached
    if misses:
        embed_documents = getattr(provider, "embed_documents", None)
        if not callable(embed_documents):
            embed_documents = provider.embed
        embedded = _validate_provider_vectors(
            embed_documents(tuple(text_by_key[key] for key in misses)), len(misses), provider.dimension
        )
        for key, vector in zip(misses, embedded, strict=True):
            _atomic_npy(embedding_dir / f"{key}.npy", vector)
            vectors_by_key[key] = vector

    matrix = np.asarray(
        [vectors_by_key[_cache_key(provider.model_id, document.vector_text_v1)] for document in eligible],
        dtype=np.float32,
    )
    if not eligible:
        matrix = np.empty((0, provider.dimension), dtype=np.float32)
    matrix_path = root / "vectors.npy"
    rows_path = root / "vector-rows.json"
    rows = tuple(document.main_sku for document in eligible)
    _atomic_npy(matrix_path, matrix)
    _atomic_json(rows_path, list(rows))
    return VectorArtifact(matrix, matrix_path, rows_path, rows, rows)


class ExactVectorIndex:
    """In-memory exact cosine index loaded from a validated vector artifact."""

    def __init__(self, normalized_matrix: np.ndarray, rows: tuple[str, ...]) -> None:
        self._normalized_matrix = normalized_matrix
        self._rows = rows
        self.dimension = normalized_matrix.shape[1]

    @classmethod
    def load(cls, matrix_path: Path, rows_path: Path) -> "ExactVectorIndex":
        loaded: object | None = None
        try:
            loaded = np.load(Path(matrix_path), allow_pickle=False)
        except (OSError, ValueError, EOFError) as error:
            raise ValueError("vector matrix artifact is invalid") from error
        try:
            if not isinstance(loaded, np.ndarray):
                raise ValueError("vector matrix artifact must be a NumPy array")
            if loaded.dtype != np.dtype(np.float32):
                raise ValueError("vector matrix artifact dtype must be float32")
            matrix = loaded
        finally:
            close = getattr(loaded, "close", None)
            if callable(close):
                close()
        try:
            payload = json.loads(Path(rows_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("vector rows artifact is invalid") from error
        if matrix.ndim != 2:
            raise ValueError("vector matrix must be 2D")
        if matrix.shape[1] <= 0:
            raise ValueError("vector matrix dimension must be positive")
        if not np.isfinite(matrix).all():
            raise ValueError("vector matrix must contain only finite values")
        if not isinstance(payload, list) or not all(isinstance(row, str) and row for row in payload):
            raise ValueError("vector rows must be a JSON array of non-empty strings")
        rows = tuple(payload)
        if matrix.shape[0] != len(rows):
            raise ValueError("vector matrix row count must match vector rows")
        if len(set(rows)) != len(rows):
            raise ValueError("vector rows contain duplicate IDs")
        matrix64 = matrix.astype(np.float64)
        norms = np.linalg.norm(matrix64, axis=1, keepdims=True)
        normalized = np.divide(
            matrix64, norms, out=np.zeros_like(matrix64), where=norms != 0
        ).astype(np.float32)
        return cls(normalized, rows)

    def search(self, query_vector: np.ndarray, limit: int, *, main_skus: tuple[str, ...] | None = None) -> tuple[SearchHit, ...]:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        try:
            query = np.asarray(query_vector, dtype=np.float32)
        except (TypeError, ValueError) as error:
            raise ValueError("query vector must be convertible to float32") from error
        if query.ndim != 1:
            raise ValueError("query vector must be one-dimensional")
        if query.shape[0] != self.dimension:
            raise ValueError(f"query vector dimension must be {self.dimension}")
        if not np.isfinite(query).all():
            raise ValueError("query vector must contain only finite values")
        query64 = query.astype(np.float64)
        query_norm = float(np.linalg.norm(query64))
        if query_norm == 0:
            raise ValueError("query vector must have a non-zero norm")
        normalized_query = (query64 / query_norm).astype(np.float32)
        similarities = self._normalized_matrix @ normalized_query
        allowed = None if main_skus is None else frozenset(main_skus)
        candidates = (index for index, sku in enumerate(self._rows) if allowed is None or sku in allowed)
        order = sorted(candidates, key=lambda index: (-float(similarities[index]), self._rows[index]))
        return tuple(
            SearchHit(
                main_sku=self._rows[index],
                score=float(np.clip((float(similarities[index]) + 1.0) / 2.0, 0.0, 1.0)),
                sources=("vector",),
            )
            for index in order[:limit]
        )
