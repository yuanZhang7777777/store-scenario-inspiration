"""Local query-side vectors for the bilingual catalogue.

The vectors on disk were built with BAAI/bge-large-{zh,en}-v1.5, so a query has
to be encoded by those same models or the cosine scores mean nothing. Both are
already in the offline model cache; the Chinese one takes about half a minute to
come off disk, which is far too slow to pay per query and perfectly fine to pay
once, so models and matrices are memoised per process.

This replaces Qdrant for retrieval. Qdrant answered vector queries over the same
8,126 points, but it is a network service that has to be up, and a brute-force
cosine over an 8,126 by 1,024 matrix takes milliseconds.
"""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
import sqlite3

import numpy as np


LANGUAGES = ("cn", "en")


def _row_text(value: str) -> str:
    """BGE models read a bare product name best without an instruction prefix."""
    return " ".join(str(value).split())


class BilingualEncoder:
    """Turns a query string into the vector the catalogue was indexed with."""

    def __init__(self, hub_cache: Path) -> None:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        self._hub_cache = hub_cache
        self._models: dict[str, tuple[object, object]] = {}

    def _model(self, model_id: str):
        if model_id not in self._models:
            import torch
            from transformers import AutoModel, AutoTokenizer

            tokenizer = AutoTokenizer.from_pretrained(model_id, cache_dir=self._hub_cache)
            model = AutoModel.from_pretrained(
                model_id, cache_dir=self._hub_cache, dtype=torch.float32
            )
            model.eval()
            self._models[model_id] = (tokenizer, model)
        return self._models[model_id]

    def encode(self, model_id: str, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        import torch

        tokenizer, model = self._model(model_id)
        batch = tokenizer([_row_text(text) for text in texts], padding=True,
                          truncation=True, max_length=512, return_tensors="pt")
        with torch.no_grad():
            hidden = model(**batch).last_hidden_state[:, 0]
        return torch.nn.functional.normalize(hidden, p=2, dim=1).numpy()


class BilingualVectorIndex:
    """The on-disk catalogue vectors, searched by brute-force cosine."""

    def __init__(self, matrix: np.ndarray, skus: tuple[str, ...],
                 model_ids: dict[str, str]) -> None:
        self.matrix = matrix
        self.skus = skus
        self.model_ids = model_ids

    @classmethod
    def load(cls, cache_path: Path) -> BilingualVectorIndex:
        connection = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)
        try:
            model_ids = {}
            for language in LANGUAGES:
                keys = [row[0] for row in connection.execute(
                    "SELECT DISTINCT model_key FROM embeddings WHERE language = ?", (language,))]
                if len(keys) != 1:
                    raise ValueError(
                        f"{cache_path}: expected one model for {language}, found {len(keys)}")
                model_ids[language] = keys[0].split("@")[0]
            rows = {}
            for language in LANGUAGES:
                rows[language] = {
                    main_sku: vector
                    for main_sku, vector in connection.execute(
                        "SELECT main_sku, vector FROM embeddings WHERE language = ?", (language,))
                }
        finally:
            connection.close()

        skus = tuple(sorted(set(rows["cn"]) & set(rows["en"])))
        if not skus:
            raise ValueError(f"{cache_path}: no main SKU has vectors in both languages")
        matrix = np.stack([
            np.frombuffer(rows[language][sku], dtype=np.float32)
            for language in LANGUAGES
            for sku in skus
        ])
        return cls(matrix, skus, model_ids)

    def search(self, language: str, vector: np.ndarray, limit: int) -> list[tuple[str, float]]:
        """Rank catalogue main SKUs against one query vector."""
        if language not in LANGUAGES:
            raise ValueError("language must be cn or en")
        offset = LANGUAGES.index(language) * len(self.skus)
        scores = self.matrix[offset:offset + len(self.skus)] @ np.asarray(
            vector, dtype=np.float32)
        if limit >= len(self.skus):
            top = np.argsort(-scores)
        else:
            top = np.argpartition(-scores, limit)[:limit]
            top = top[np.argsort(-scores[top])]
        return [(self.skus[int(index)], float(scores[int(index)])) for index in top]


@lru_cache(maxsize=1)
def load_vector_index(cache_path: str) -> BilingualVectorIndex:
    return BilingualVectorIndex.load(Path(cache_path))


@lru_cache(maxsize=1)
def load_encoder(hub_cache: str) -> BilingualEncoder:
    return BilingualEncoder(Path(hub_cache))


@lru_cache(maxsize=1)
def load_products(asset_db: str) -> dict[str, dict[str, object]]:
    from .pilot import load_products as read_products

    return read_products(Path(asset_db))
