"""Human-reviewable retrieval benchmark contracts and evaluation."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .models import SearchHit


EXPECTED_STATUSES = frozenset({"has_match", "no_reliable_match"})
LABEL_STATUSES = frozenset({"provisional", "confirmed"})
HIT_AT_5_THRESHOLD = 0.75
HIT_AT_5_LIMIT = 5
_ITEM_FIELDS = frozenset(
    {
        "query_id",
        "query",
        "relevant_main_skus",
        "expected_status",
        "label_status",
        "notes",
    }
)


@dataclass(frozen=True)
class BenchmarkItem:
    """One labeled query and its expected catalog-family outcome."""

    query_id: str
    query: str
    relevant_main_skus: tuple[str, ...]
    expected_status: str
    label_status: str
    notes: str


@dataclass(frozen=True)
class BenchmarkResult:
    """One query's returned IDs and whether its expected outcome was met."""

    query_id: str
    expected_status: str
    returned_main_skus: tuple[str, ...]
    matched_relevant_main_skus: tuple[str, ...]
    is_hit: bool


@dataclass(frozen=True)
class BenchmarkReport:
    """Aggregate benchmark outcome, including individual misses."""

    hit_at_5: float
    results: tuple[BenchmarkResult, ...]
    misses: tuple[BenchmarkResult, ...]
    enforced: bool
    passes_threshold: bool | None
    threshold: float

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready report for command-line consumers."""

        return {
            "hit_at_5": self.hit_at_5,
            "threshold": self.threshold,
            "enforced": self.enforced,
            "passes_threshold": self.passes_threshold,
            "results": [
                {
                    "query_id": result.query_id,
                    "expected_status": result.expected_status,
                    "returned_main_skus": list(result.returned_main_skus),
                    "matched_relevant_main_skus": list(result.matched_relevant_main_skus),
                    "is_hit": result.is_hit,
                }
                for result in self.results
            ],
            "misses": [
                {
                    "query_id": result.query_id,
                    "expected_status": result.expected_status,
                    "returned_main_skus": list(result.returned_main_skus),
                    "matched_relevant_main_skus": list(result.matched_relevant_main_skus),
                    "is_hit": result.is_hit,
                }
                for result in self.misses
            ],
        }


def _required_string(value: object, field: str, index: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"benchmark item {index} field {field!r} must be a non-empty string")
    return value


def _load_item(payload: object, index: int) -> BenchmarkItem:
    if not isinstance(payload, dict):
        raise ValueError(f"benchmark item {index} must be an object")
    fields = frozenset(payload)
    if fields != _ITEM_FIELDS:
        missing = sorted(_ITEM_FIELDS.difference(fields))
        extra = sorted(fields.difference(_ITEM_FIELDS))
        raise ValueError(
            f"benchmark item {index} fields must exactly match the schema "
            f"(missing: {missing or 'none'}; extra: {extra or 'none'})"
        )

    query_id = _required_string(payload["query_id"], "query_id", index)
    query = _required_string(payload["query"], "query", index)
    notes = _required_string(payload["notes"], "notes", index)
    expected_status = payload["expected_status"]
    label_status = payload["label_status"]
    if not isinstance(expected_status, str) or expected_status not in EXPECTED_STATUSES:
        raise ValueError(f"benchmark item {index} has invalid expected_status")
    if not isinstance(label_status, str) or label_status not in LABEL_STATUSES:
        raise ValueError(f"benchmark item {index} has invalid label_status")

    relevant_main_skus = payload["relevant_main_skus"]
    if not isinstance(relevant_main_skus, list) or any(
        not isinstance(sku, str) or not sku.strip() for sku in relevant_main_skus
    ):
        raise ValueError(
            f"benchmark item {index} relevant_main_skus must be a list of non-empty strings"
        )
    if len(set(relevant_main_skus)) != len(relevant_main_skus):
        raise ValueError(f"benchmark item {index} relevant_main_skus must not contain duplicates")
    if expected_status == "has_match" and not relevant_main_skus:
        raise ValueError(
            f"benchmark item {index} relevant_main_skus is required for has_match"
        )
    if expected_status == "no_reliable_match" and relevant_main_skus:
        raise ValueError(
            f"benchmark item {index} relevant_main_skus must be empty for no_reliable_match"
        )

    return BenchmarkItem(
        query_id=query_id,
        query=query,
        relevant_main_skus=tuple(relevant_main_skus),
        expected_status=expected_status,
        label_status=label_status,
        notes=notes,
    )


def load_benchmark(path: Path) -> tuple[BenchmarkItem, ...]:
    """Load and strictly validate a versioned benchmark JSON array."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid benchmark JSON: {path}") from error
    if not isinstance(payload, list):
        raise ValueError("benchmark top-level value must be a JSON array")

    items = tuple(_load_item(item, index) for index, item in enumerate(payload))
    query_ids = tuple(item.query_id for item in items)
    if len(set(query_ids)) != len(query_ids):
        raise ValueError("benchmark contains duplicate query_id values")
    return items


def run_benchmark(
    search_fn: Callable[[str, int], Sequence[SearchHit]],
    items: Sequence[BenchmarkItem],
    top_k: int = 5,
) -> BenchmarkReport:
    """Evaluate a search function without imposing process-level exit behavior."""

    if type(top_k) is not int or top_k != HIT_AT_5_LIMIT:
        raise ValueError("top_k must be fixed at 5 for Hit@5")

    results: list[BenchmarkResult] = []
    for item in items:
        returned_main_skus: list[str] = []
        seen_main_skus: set[str] = set()
        for hit in search_fn(item.query, top_k)[:HIT_AT_5_LIMIT]:
            if hit.main_sku not in seen_main_skus:
                seen_main_skus.add(hit.main_sku)
                returned_main_skus.append(hit.main_sku)
        immutable_returned_main_skus = tuple(returned_main_skus)
        relevant_skus = set(item.relevant_main_skus)
        matched_relevant_main_skus = tuple(
            main_sku
            for main_sku in immutable_returned_main_skus
            if main_sku in relevant_skus
        )
        is_hit = (
            bool(matched_relevant_main_skus)
            if item.expected_status == "has_match"
            else not immutable_returned_main_skus
        )
        results.append(
            BenchmarkResult(
                query_id=item.query_id,
                expected_status=item.expected_status,
                returned_main_skus=immutable_returned_main_skus,
                matched_relevant_main_skus=matched_relevant_main_skus,
                is_hit=is_hit,
            )
        )

    has_match_results = [
        result for result in results if result.expected_status == "has_match"
    ]
    hit_at_5 = (
        sum(result.is_hit for result in has_match_results) / len(has_match_results)
        if has_match_results
        else 0.0
    )
    enforced = all(item.label_status == "confirmed" for item in items)
    passes_threshold = hit_at_5 >= HIT_AT_5_THRESHOLD if enforced else None
    immutable_results = tuple(results)
    return BenchmarkReport(
        hit_at_5=hit_at_5,
        results=immutable_results,
        misses=tuple(result for result in immutable_results if not result.is_hit),
        enforced=enforced,
        passes_threshold=passes_threshold,
        threshold=HIT_AT_5_THRESHOLD,
    )
