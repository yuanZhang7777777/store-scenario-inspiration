"""Minimal Qdrant REST vector index for catalog documents."""

from __future__ import annotations

import json
from collections.abc import Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
from uuid import NAMESPACE_URL, uuid5

import numpy as np

from .models import ProductFamilyDocument, SearchHit


_TIMEOUT_SECONDS = 10


def _collection_path(collection: str) -> str:
    if not isinstance(collection, str) or not collection:
        raise ValueError("collection must contain text")
    if any(character in collection for character in "/?#"):
        raise ValueError("collection must be a single path segment")
    return quote(collection, safe="")


def _base_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc or parts.query or parts.fragment:
        raise ValueError("base_url must be an http(s) URL without query or fragment")
    return value.rstrip("/")


def _vector(value: np.ndarray, dimension: int) -> np.ndarray:
    try:
        vector = np.asarray(value, dtype=np.float32)
    except (TypeError, ValueError) as error:
        raise ValueError("vector must be convertible to float32") from error
    if vector.ndim != 1:
        raise ValueError("vector must be one-dimensional")
    if vector.shape[0] != dimension:
        raise ValueError(f"vector dimension must be {dimension}")
    if not np.isfinite(vector).all():
        raise ValueError("vector must contain only finite values")
    if float(np.linalg.norm(vector.astype(np.float64))) == 0.0:
        raise ValueError("vector must have a non-zero norm")
    return vector


def _point_id(main_sku: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"store-scenario-inspiration/catalog/{main_sku}"))


def _document_vectors(
    documents: Sequence[ProductFamilyDocument],
    matrix: np.ndarray,
    dimension: int,
) -> tuple[tuple[str, ...], np.ndarray]:
    rows = tuple(documents)
    try:
        vectors = np.asarray(matrix, dtype=np.float32)
    except (TypeError, ValueError) as error:
        raise ValueError("matrix must be convertible to float32") from error
    if vectors.shape != (len(rows), dimension):
        raise ValueError(f"matrix must have shape ({len(rows)}, {dimension})")
    if not np.isfinite(vectors).all():
        raise ValueError("matrix must contain only finite values")
    if len(rows) and (np.linalg.norm(vectors.astype(np.float64), axis=1) == 0.0).any():
        raise ValueError("matrix rows must have a non-zero norm")
    main_skus = tuple(document.main_sku for document in rows)
    _validate_main_skus(main_skus, "document main_sku")
    return main_skus, vectors


def _validate_main_skus(main_skus: Sequence[str], label: str) -> None:
    if any(not isinstance(main_sku, str) or not main_sku for main_sku in main_skus):
        raise ValueError(f"{label} must contain text")
    if len(set(main_skus)) != len(main_skus):
        raise ValueError(f"{label} values must be unique")


class QdrantVectorIndex:
    """REST-backed Qdrant vector index with the ExactVectorIndex search contract."""

    def __init__(self, base_url: str, collection: str, dimension: int) -> None:
        if not isinstance(dimension, int) or isinstance(dimension, bool) or dimension <= 0:
            raise ValueError("dimension must be a positive integer")
        self.base_url = _base_url(base_url)
        self.collection = collection
        self.dimension = dimension
        self._collection_path = _collection_path(collection)

    def _request(self, method: str, path: str, payload: object | None = None) -> object:
        data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Qdrant {method} {path} failed with HTTP {error.code}: {body}") from error
        except (OSError, URLError, TimeoutError) as error:
            raise RuntimeError(f"Qdrant {method} {path} failed: {error}") from error
        except json.JSONDecodeError as error:
            raise RuntimeError(f"Qdrant {method} {path} returned invalid JSON") from error
        if not isinstance(result, dict) or result.get("status") != "ok":
            raise RuntimeError(f"Qdrant {method} {path} returned unexpected response: {result!r}")
        return result.get("result")

    def _exists(self) -> bool:
        result = self._request("GET", f"/collections/{self._collection_path}/exists")
        if not isinstance(result, dict) or not isinstance(result.get("exists"), bool):
            raise RuntimeError("Qdrant collection existence response is invalid")
        return result["exists"]

    def _require_collection(self) -> None:
        result = self._request("GET", f"/collections/{self._collection_path}")
        if not isinstance(result, dict):
            raise RuntimeError("Qdrant collection response is invalid")
        config = result.get("config")
        if not isinstance(config, dict):
            raise RuntimeError("Qdrant collection config response is invalid")
        params = config.get("params")
        if not isinstance(params, dict):
            raise RuntimeError("Qdrant collection params response is invalid")
        vectors = params.get("vectors")
        if not isinstance(vectors, dict) or vectors.get("size") != self.dimension or vectors.get("distance") != "Cosine":
            raise ValueError(
                f"Qdrant collection {self.collection!r} must use unnamed Cosine vectors of size {self.dimension}"
            )

    def _put_points(self, main_skus: Sequence[str], vectors: np.ndarray) -> None:
        self._request(
            "PUT",
            f"/collections/{self._collection_path}/points?wait=true",
            {
                "points": [
                    {
                        "id": _point_id(main_sku),
                        "vector": vector.tolist(),
                        "payload": {"main_sku": main_sku},
                    }
                    for main_sku, vector in zip(main_skus, vectors, strict=True)
                ]
            },
        )

    def create(self, documents: Sequence[ProductFamilyDocument], matrix: np.ndarray) -> None:
        main_skus, vectors = _document_vectors(documents, matrix, self.dimension)
        if self._exists():
            raise ValueError(f"Qdrant collection {self.collection!r} already exists")

        self._request(
            "PUT",
            f"/collections/{self._collection_path}",
            {"vectors": {"size": self.dimension, "distance": "Cosine"}},
        )
        if main_skus:
            self._put_points(main_skus, vectors)
        # ponytail: unindexed payload filter scans small eligible corpus; add index on
        # verified supported deployment when scale demands. Win Qdrant 1.19.1 returned
        # gridstore "path not found" while creating this keyword index.

    def upsert(self, documents: Sequence[ProductFamilyDocument], matrix: np.ndarray) -> None:
        main_skus, vectors = _document_vectors(documents, matrix, self.dimension)
        if not main_skus:
            return
        self._require_collection()
        self._put_points(main_skus, vectors)

    def delete(self, main_skus: Sequence[str]) -> None:
        rows = tuple(main_skus)
        _validate_main_skus(rows, "main_skus")
        if rows:
            self._request(
                "POST",
                f"/collections/{self._collection_path}/points/delete?wait=true",
                {"points": [_point_id(main_sku) for main_sku in rows]},
            )

    def search(
        self,
        query_vector: np.ndarray,
        limit: int,
        *,
        main_skus: tuple[str, ...] | None = None,
    ) -> tuple[SearchHit, ...]:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        query = _vector(query_vector, self.dimension)
        if main_skus == ():
            return ()
        if main_skus is not None and any(
            not isinstance(main_sku, str) or not main_sku for main_sku in main_skus
        ):
            raise ValueError("main_skus must contain non-empty strings")

        payload: dict[str, object] = {
            "query": query.tolist(),
            "limit": limit,
            "with_payload": ["main_sku"],
        }
        if main_skus is not None:
            payload["filter"] = {
                "must": [{"key": "main_sku", "match": {"any": list(main_skus)}}]
            }
        result = self._request(
            "POST",
            f"/collections/{self._collection_path}/points/query",
            payload,
        )
        if not isinstance(result, dict) or not isinstance(result.get("points"), list):
            raise RuntimeError("Qdrant query response is invalid")
        hits = []
        for point in result["points"]:
            if not isinstance(point, dict):
                raise RuntimeError("Qdrant query point response is invalid")
            point_payload = point.get("payload")
            if not isinstance(point_payload, dict) or not isinstance(point_payload.get("main_sku"), str):
                raise RuntimeError("Qdrant query point payload is invalid")
            score = float(np.clip((float(point["score"]) + 1.0) / 2.0, 0.0, 1.0))
            hits.append(SearchHit(point_payload["main_sku"], score, ("vector",)))
        return tuple(hits)
