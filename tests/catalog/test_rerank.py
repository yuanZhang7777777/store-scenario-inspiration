from __future__ import annotations

import pytest

from store_scenario_inspiration.catalog.models import SearchHit
from store_scenario_inspiration.catalog.rerank import RerankJudgment, soft_rerank


def test_soft_rerank_keeps_every_candidate_and_uses_original_rank_inside_each_level() -> None:
    hits = tuple(SearchHit(sku, score, ("vector",)) for sku, score in (("A", 0.9), ("B", 0.8), ("C", 0.7), ("D", 0.6)))
    judgments = (
        RerankJudgment("A", 0, "仅词面相关"),
        RerankJudgment("B", 3, "直接满足需求"),
        RerankJudgment("C", 2, "可作为配套商品"),
        RerankJudgment("D", 2, "可作为替代商品"),
    )

    reranked = soft_rerank(hits, judgments)

    assert [(item.hit.main_sku, item.original_rank, item.rerank_rank, item.relevance_level) for item in reranked] == [
        ("B", 2, 1, 3),
        ("C", 3, 2, 2),
        ("D", 4, 3, 2),
        ("A", 1, 4, 0),
    ]


def test_soft_rerank_rejects_incomplete_codex_judgments_instead_of_dropping_candidates() -> None:
    hits = (SearchHit("A", 0.9, ("keyword",)), SearchHit("B", 0.8, ("vector",)))

    with pytest.raises(ValueError, match="exactly one judgment"):
        soft_rerank(hits, (RerankJudgment("A", 3, "直接满足需求"),))
