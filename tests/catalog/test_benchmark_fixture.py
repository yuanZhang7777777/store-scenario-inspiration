from __future__ import annotations

import json
from pathlib import Path

import pytest

from store_scenario_inspiration.catalog.benchmark import (
    BenchmarkItem,
    load_benchmark,
    run_benchmark,
)
from store_scenario_inspiration.catalog.models import SearchHit


BENCHMARK_PATH = Path(__file__).parents[1] / "fixtures" / "retrieval-benchmark-v1.json"


def test_benchmark_has_unique_confirmable_queries() -> None:
    items = load_benchmark(BENCHMARK_PATH)

    assert 20 <= len(items) <= 30
    assert len({item.query_id for item in items}) == len(items)
    assert all(item.expected_status in {"has_match", "no_reliable_match"} for item in items)
    assert all(item.label_status in {"provisional", "confirmed"} for item in items)
    assert all(item.relevant_main_skus for item in items if item.expected_status == "has_match")
    assert all(item.label_status == "provisional" for item in items)
    assert isinstance(items, tuple)


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({}, "top-level"),
        ([{"query_id": "q1"}], "fields"),
        ([{"query_id": "q1", "query": "query", "relevant_main_skus": [], "expected_status": "has_match", "label_status": "confirmed", "notes": "note"}], "relevant_main_skus"),
        ([{"query_id": "q1", "query": "query", "relevant_main_skus": ["SKU"], "expected_status": "no_reliable_match", "label_status": "confirmed", "notes": "note"}], "relevant_main_skus"),
    ],
)
def test_load_benchmark_rejects_invalid_contracts(
    tmp_path: Path, payload: object, error: str
) -> None:
    path = tmp_path / "benchmark.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=error):
        load_benchmark(path)


def test_load_benchmark_rejects_duplicate_ids_and_immutable_items(tmp_path: Path) -> None:
    payload = [
        {"query_id": "q1", "query": "one", "relevant_main_skus": ["A"], "expected_status": "has_match", "label_status": "confirmed", "notes": "note"},
        {"query_id": "q1", "query": "two", "relevant_main_skus": ["B"], "expected_status": "has_match", "label_status": "confirmed", "notes": "note"},
    ]
    path = tmp_path / "duplicate.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate"):
        load_benchmark(path)

    with pytest.raises((AttributeError, TypeError)):
        BenchmarkItem("q", "query", ("SKU",), "has_match", "confirmed", "note").query = "other"  # type: ignore[misc]


def test_run_benchmark_records_returned_ids_and_only_scores_has_match() -> None:
    items = (
        BenchmarkItem("hit", "hit query", ("MATCH",), "has_match", "confirmed", "note"),
        BenchmarkItem("miss", "miss query", ("MISSED",), "has_match", "confirmed", "note"),
        BenchmarkItem("negative", "negative query", (), "no_reliable_match", "confirmed", "note"),
    )

    def search(query: str, top_k: int) -> tuple[SearchHit, ...]:
        assert top_k == 5
        return {
            "hit query": (SearchHit("MATCH", 1.0, ("keyword",)),),
            "miss query": (SearchHit("OTHER", 0.8, ("keyword",)),),
            "negative query": (SearchHit("UNEXPECTED", 0.7, ("keyword",)),),
        }[query]

    report = run_benchmark(search, items)

    assert report.hit_at_5 == 0.5
    assert report.enforced is True
    assert report.passes_threshold is False
    assert [(result.query_id, result.returned_main_skus) for result in report.results] == [
        ("hit", ("MATCH",)),
        ("miss", ("OTHER",)),
        ("negative", ("UNEXPECTED",)),
    ]
    assert [result.query_id for result in report.misses] == ["miss", "negative"]


def test_run_benchmark_does_not_enforce_provisional_labels() -> None:
    item = BenchmarkItem("q1", "query", ("MATCH",), "has_match", "provisional", "note")

    report = run_benchmark(lambda query, top_k: (), (item,))

    assert report.hit_at_5 == 0.0
    assert report.enforced is False
    assert report.passes_threshold is None
