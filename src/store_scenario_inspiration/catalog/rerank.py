"""Stable soft reranking of high-recall candidates using Codex judgments."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .models import SearchHit


@dataclass(frozen=True)
class RerankJudgment:
    main_sku: str
    relevance_level: int
    reason: str

    def __post_init__(self) -> None:
        if not self.main_sku.strip() or not self.reason.strip():
            raise ValueError("rerank judgment requires main_sku and reason")
        if isinstance(self.relevance_level, bool) or self.relevance_level not in range(4):
            raise ValueError("relevance_level must be an integer from 0 to 3")


@dataclass(frozen=True)
class RerankedHit:
    hit: SearchHit
    original_rank: int
    rerank_rank: int
    relevance_level: int
    relevance_reason: str


def soft_rerank(
    hits: Sequence[SearchHit], judgments: Iterable[RerankJudgment]
) -> tuple[RerankedHit, ...]:
    """Keep every hit, sorting by Codex relevance level then original RRF rank."""

    candidates = tuple(hits)
    by_sku: dict[str, RerankJudgment] = {}
    for judgment in judgments:
        if judgment.main_sku in by_sku:
            raise ValueError(f"duplicate rerank judgment: {judgment.main_sku}")
        by_sku[judgment.main_sku] = judgment
    candidate_skus = [hit.main_sku for hit in candidates]
    if len(by_sku) != len(candidates) or set(by_sku) != set(candidate_skus):
        raise ValueError("reranking requires exactly one judgment per candidate")

    ordered = sorted(
        enumerate(candidates, start=1),
        key=lambda item: (-by_sku[item[1].main_sku].relevance_level, item[0]),
    )
    return tuple(
        RerankedHit(
            hit=hit,
            original_rank=original_rank,
            rerank_rank=rerank_rank,
            relevance_level=by_sku[hit.main_sku].relevance_level,
            relevance_reason=by_sku[hit.main_sku].reason,
        )
        for rerank_rank, (original_rank, hit) in enumerate(ordered, start=1)
    )
