from __future__ import annotations

import json
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

import numpy as np
import pytest

from store_scenario_inspiration.catalog.models import ProductFamilyDocument
from store_scenario_inspiration.catalog import qdrant as qdrant_module
from store_scenario_inspiration.catalog.qdrant import QdrantVectorIndex


BASE_URL = "http://127.0.0.1:6333"


def _request(method: str, path: str, payload: object | None = None) -> object:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"{BASE_URL}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


def _server_available() -> bool:
    try:
        _request("GET", "/")
    except (OSError, URLError):
        return False
    return True


def _document(main_sku: str) -> ProductFamilyDocument:
    return ProductFamilyDocument(
        doc_id=f"main:{main_sku}",
        main_sku=main_sku,
        searchable=True,
        cn_names=(),
        en_aliases=(),
        leaf_categories=(),
        category_paths=(),
        vector_text_v1=f"商品名称：{main_sku}",
        children=(),
        quality_flags=(),
    )


class _FakeResponse:
    def __init__(self, result: object) -> None:
        self._result = result

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps({"status": "ok", "result": self._result}).encode("utf-8")


class _FakeQdrant:
    def __init__(self, *, exists: bool = True) -> None:
        self.requests: list[tuple[str, str, object | None]] = []
        self.exists = exists

    def __call__(self, request: Request, timeout: int) -> _FakeResponse:
        payload = None
        if request.data is not None:
            payload = json.loads(request.data.decode("utf-8"))
        path = urlsplit(request.full_url).path
        if urlsplit(request.full_url).query:
            path = f"{path}?{urlsplit(request.full_url).query}"
        method = request.get_method()
        self.requests.append((method, path, payload))
        if path.endswith("/exists"):
            return _FakeResponse({"exists": self.exists})
        if method == "GET" and path.startswith("/collections/"):
            return _FakeResponse(
                {"config": {"params": {"vectors": {"size": 2, "distance": "Cosine"}}}}
            )
        return _FakeResponse({})


def test_qdrant_upsert_reuses_create_point_ids_without_creating_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeQdrant(exists=False)
    monkeypatch.setattr(qdrant_module, "urlopen", fake)
    index = QdrantVectorIndex(BASE_URL, "catalog", 2)

    index.create((_document("MAIN-A"),), np.array([[1.0, 0.0]], dtype=np.float32))
    index.upsert((_document("MAIN-A"),), np.array([[0.0, 1.0]], dtype=np.float32))

    point_writes = [
        payload
        for method, path, payload in fake.requests
        if method == "PUT" and path == "/collections/catalog/points?wait=true"
    ]
    collection_creates = [
        payload
        for method, path, payload in fake.requests
        if method == "PUT" and path == "/collections/catalog"
    ]

    assert len(collection_creates) == 1
    assert len(point_writes) == 2
    assert point_writes[0]["points"][0]["id"] == point_writes[1]["points"][0]["id"]
    assert point_writes[1]["points"] == [
        {
            "id": point_writes[0]["points"][0]["id"],
            "vector": [0.0, 1.0],
            "payload": {"main_sku": "MAIN-A"},
        }
    ]


def test_qdrant_upsert_rejects_bad_vectors_before_writing_points(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeQdrant()
    monkeypatch.setattr(qdrant_module, "urlopen", fake)
    index = QdrantVectorIndex(BASE_URL, "catalog", 2)

    with pytest.raises(ValueError, match="non-zero norm"):
        index.upsert((_document("MAIN-A"),), np.array([[0.0, 0.0]], dtype=np.float32))
    with pytest.raises(ValueError, match=r"shape \(1, 2\)"):
        index.upsert((_document("MAIN-A"),), np.array([[1.0, 0.0, 0.0]], dtype=np.float32))

    assert [
        path for method, path, payload in fake.requests if path == "/collections/catalog/points?wait=true"
    ] == []


def test_qdrant_delete_sends_stable_point_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeQdrant(exists=False)
    monkeypatch.setattr(qdrant_module, "urlopen", fake)
    index = QdrantVectorIndex(BASE_URL, "catalog", 2)

    index.create((_document("MAIN-A"),), np.array([[1.0, 0.0]], dtype=np.float32))
    created_id = next(
        payload["points"][0]["id"]
        for method, path, payload in fake.requests
        if method == "PUT" and path == "/collections/catalog/points?wait=true"
    )
    fake.requests.clear()

    index.delete(("MAIN-A",))

    assert fake.requests == [
        (
            "POST",
            "/collections/catalog/points/delete?wait=true",
            {"points": [created_id]},
        )
    ]


@pytest.mark.skipif(not _server_available(), reason="Qdrant is not running on 127.0.0.1:6333")
def test_qdrant_vector_index_creates_and_filters_before_top_k() -> None:
    collection = f"test_store_catalog_qdrant_{uuid4().hex}"
    assert collection.startswith("test_store_catalog_qdrant_")
    index = QdrantVectorIndex(BASE_URL, collection, 2)
    try:
        index.create(
            (_document("MAIN-A"), _document("MAIN-B")),
            np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
        )

        unfiltered = index.search(np.array([1.0, 0.0], dtype=np.float32), 2)
        filtered = index.search(
            np.array([1.0, 0.0], dtype=np.float32), 1, main_skus=("MAIN-B",)
        )
        empty = index.search(np.array([1.0, 0.0], dtype=np.float32), 1, main_skus=())

        assert [(hit.main_sku, hit.score, hit.sources) for hit in unfiltered] == [
            ("MAIN-A", pytest.approx(1.0), ("vector",)),
            ("MAIN-B", pytest.approx(0.5), ("vector",)),
        ]
        assert [(hit.main_sku, hit.score, hit.sources) for hit in filtered] == [
            ("MAIN-B", pytest.approx(0.5), ("vector",))
        ]
        assert empty == ()
        with pytest.raises(ValueError, match="already exists"):
            index.create((_document("MAIN-C"),), np.ones((1, 2)))
    finally:
        if collection.startswith("test_store_catalog_qdrant_"):
            try:
                _request("DELETE", f"/collections/{collection}")
            except (OSError, URLError):
                pass
