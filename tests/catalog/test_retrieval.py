from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest

from store_scenario_inspiration.catalog.models import ChildVariant, ProductFamilyDocument, SearchHit
from store_scenario_inspiration.catalog.retrieval import HybridRetriever, RetrievalQuery


def document(main_sku: str, children: tuple[ChildVariant, ...] = ()) -> ProductFamilyDocument:
    return ProductFamilyDocument(
        doc_id=f"main:{main_sku}", main_sku=main_sku, searchable=True,
        cn_names=(main_sku,), en_aliases=(), leaf_categories=(), category_paths=(),
        vector_text_v1=f"商品名称：{main_sku}", children=children, quality_flags=(),
    )


@dataclass
class FakeStore:
    keyword_hits: tuple[SearchHit, ...]
    documents: dict[str, ProductFamilyDocument]

    def __post_init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def keyword_search(self, query: str, limit: int) -> tuple[SearchHit, ...]:
        self.calls.append((query, limit))
        return self.keyword_hits[:limit]

    def get_document(self, main_sku: str) -> ProductFamilyDocument | None:
        return self.documents.get(main_sku)


@dataclass
class FakeIndex:
    hits: tuple[SearchHit, ...]

    def __post_init__(self) -> None:
        self.calls: list[tuple[np.ndarray, int]] = []

    def search(self, query_vector: np.ndarray, limit: int) -> tuple[SearchHit, ...]:
        self.calls.append((query_vector, limit))
        return self.hits[:limit]


def test_hybrid_search_uses_rrf_deduplicates_and_tracks_stable_sources() -> None:
    store = FakeStore(
        (SearchHit("A", 1.0, ("keyword",)), SearchHit("B", 0.5, ("keyword",)), SearchHit("A", 0.1, ("keyword",))),
        {sku: document(sku) for sku in ("A", "B", "C")},
    )
    index = FakeIndex((SearchHit("C", 1.0, ("vector",)), SearchHit("A", 0.8, ("vector",))))

    hits = HybridRetriever(store, index).search(RetrievalQuery("  camp  "), np.array([1.0]), limit=3)

    assert [(hit.main_sku, hit.score, hit.sources) for hit in hits] == [
        ("A", pytest.approx(1 / 61 + 1 / 62), ("keyword", "vector")),
        ("C", pytest.approx(1 / 61), ("vector",)),
        ("B", pytest.approx(1 / 62), ("keyword",)),
    ]
    assert [hit.main_sku for hit in hits] == ["A", "C", "B"]
    assert store.calls == [("camp", 50)]
    assert index.calls[0][1] == 50


def test_hybrid_search_resolves_equal_scores_by_main_sku_and_degrades_to_keyword_only() -> None:
    store = FakeStore(
        (SearchHit("B", 1.0, ("keyword",)), SearchHit("A", 1.0, ("keyword",))),
        {sku: document(sku) for sku in ("A", "B")},
    )

    hits = HybridRetriever(store).search(RetrievalQuery("lamp"), None, limit=2)

    assert [(hit.main_sku, hit.sources) for hit in hits] == [("B", ("keyword",)), ("A", ("keyword",))]
    assert store.calls == [("lamp", 50)]


def test_query_normalizes_fields_and_rejects_blank_text_and_bad_limits() -> None:
    query = RetrievalQuery("  fullwidth\u3000text  ", "  Ｓｈｏｐｅｅ  ", "  CN  ")
    assert (query.text, query.platform, query.country) == ("fullwidth text", "Shopee", "CN")
    with pytest.raises(ValueError, match="text"):
        RetrievalQuery(" \t ")
    with pytest.raises(ValueError, match="positive"):
        HybridRetriever(FakeStore((), {})).search(RetrievalQuery("lamp"), None, limit=0)


def test_vector_without_an_index_is_explicitly_rejected() -> None:
    with pytest.raises(ValueError, match="vector index"):
        HybridRetriever(FakeStore((), {})).search(RetrievalQuery("lamp"), np.array([1.0]))


def test_vector_only_results_and_large_limits_use_the_channel_candidate_limit() -> None:
    store = FakeStore((), {"VECTOR": document("VECTOR")})
    index = FakeIndex((SearchHit("VECTOR", 1.0, ("vector",)),))

    hits = HybridRetriever(store, index).search(RetrievalQuery("lamp"), np.array([1.0]), limit=6)

    assert [(hit.main_sku, hit.sources) for hit in hits] == [("VECTOR", ("vector",))]
    assert store.calls == [("lamp", 60)]
    assert index.calls[0][1] == 60


def test_platform_filter_keeps_eligible_children_and_warns_when_platform_missing() -> None:
    shopee_child = ChildVariant("YNFBA997-S", "Shopee child", " Shopee违禁品 ", ())
    safe_child = ChildVariant("YNFBA997-OK", "Safe child", "", ())
    family = document("YNFBA997", (shopee_child, safe_child))
    store = FakeStore((SearchHit("YNFBA997", 1.0, ("keyword",)),), {"YNFBA997": family})
    retriever = HybridRetriever(store)

    shopee = retriever.search(RetrievalQuery("lamp", platform=" shopee "), None)
    neutral = retriever.search(RetrievalQuery("lamp"), None)

    assert shopee[0].eligible_child_skus == ("YNFBA997-OK",)
    assert shopee[0].warnings == ()
    assert neutral[0].eligible_child_skus == ("YNFBA997-S", "YNFBA997-OK")
    assert neutral[0].warnings == ("YNFBA997-S:  Shopee违禁品 ",)


@pytest.mark.parametrize(
    ("platform", "status"),
    [
        ("shopee", "ＳＨＯＰＥＥ违禁品"),
        ("Shopee", "【shopee】 prohibited"),
        ("抖音", "抖音违禁品"),
        ("抖音", "【抖音】 prohibited"),
    ],
)
def test_platform_bans_match_normalized_complete_platform_labels(platform: str, status: str) -> None:
    family = document("BANNED", (ChildVariant("BANNED-1", "No", status, ()), ChildVariant("SAFE", "Safe", "", ())))
    store = FakeStore((SearchHit("BANNED", 1.0, ("keyword",)),), {"BANNED": family})

    hits = HybridRetriever(store).search(RetrievalQuery("lamp", platform=platform), None)

    assert hits[0].eligible_child_skus == ("SAFE",)


@pytest.mark.parametrize(
    ("platform", "status"),
    [
        ("shopee", "notshopee prohibited"),
        ("shopee", "shopeeTW prohibited"),
        ("ali", "aliExpress prohibited"),
        ("抖音", "快手抖音 prohibited"),
    ],
)
def test_platform_bans_do_not_match_platform_substrings(platform: str, status: str) -> None:
    family = document("NOT-BANNED", (ChildVariant("NOT-BANNED-1", "No", status, ()),))
    store = FakeStore((SearchHit("NOT-BANNED", 1.0, ("keyword",)),), {"NOT-BANNED": family})

    hits = HybridRetriever(store).search(RetrievalQuery("lamp", platform=platform), None)

    assert hits[0].eligible_child_skus == ("NOT-BANNED-1",)


def test_filtered_child_preserves_mixed_ban_and_risk_warning_when_sibling_remains() -> None:
    family = document(
        "MIXED",
        (
            ChildVariant("MIXED-BANNED", "Risk", "Shopee 违禁品 高退款/侵权/质量", ()),
            ChildVariant("MIXED-SAFE", "Safe", "", ()),
        ),
    )
    store = FakeStore((SearchHit("MIXED", 1.0, ("keyword",)),), {"MIXED": family})

    hits = HybridRetriever(store).search(RetrievalQuery("lamp", platform="Shopee"), None)

    assert hits[0].eligible_child_skus == ("MIXED-SAFE",)
    assert hits[0].warnings == ("MIXED-BANNED: Shopee 违禁品 高退款/侵权/质量",)


def test_empty_and_unknown_child_statuses_do_not_produce_warnings() -> None:
    family = document("UNKNOWN", (ChildVariant("EMPTY", "Empty", "", ()), ChildVariant("UNKNOWN", "Unknown", "unknown", ())))
    store = FakeStore((SearchHit("UNKNOWN", 1.0, ("keyword",)),), {"UNKNOWN": family})

    hits = HybridRetriever(store).search(RetrievalQuery("lamp", platform="Shopee"), None)

    assert hits[0].eligible_child_skus == ("EMPTY", "UNKNOWN")
    assert hits[0].warnings == ()


def test_equal_rrf_scores_break_ties_by_main_sku() -> None:
    store = FakeStore((SearchHit("B", 1.0, ("keyword",)),), {"A": document("A"), "B": document("B")})
    index = FakeIndex((SearchHit("A", 1.0, ("vector",)),))

    hits = HybridRetriever(store, index).search(RetrievalQuery("lamp"), np.array([1.0]), limit=2)

    assert [(hit.main_sku, hit.score) for hit in hits] == [("A", pytest.approx(1 / 61)), ("B", pytest.approx(1 / 61))]


def test_store_and_vector_errors_propagate_without_conversion() -> None:
    class ClosedStore(FakeStore):
        def keyword_search(self, query: str, limit: int) -> tuple[SearchHit, ...]:
            raise RuntimeError("catalog store is closed")

    class BrokenIndex(FakeIndex):
        def search(self, query_vector: np.ndarray, limit: int) -> tuple[SearchHit, ...]:
            raise ValueError("query vector must have a non-zero norm")

    with pytest.raises(RuntimeError, match="catalog store is closed"):
        HybridRetriever(ClosedStore((), {})).search(RetrievalQuery("lamp"), None)
    with pytest.raises(ValueError, match="non-zero norm"):
        HybridRetriever(FakeStore((), {}), BrokenIndex(())).search(RetrievalQuery("lamp"), np.array([1.0]))


def test_child_risk_is_not_a_family_ban_and_all_banned_family_is_backfilled() -> None:
    ali_risk = document("ZXMO528", (ChildVariant("ZXMO528-R", "Risk", "Ali-高退款(其他)", ()), ChildVariant("ZXMO528-OK", "Safe", "", ())))
    banned = document("BANNED", (ChildVariant("BANNED-1", "No", "SHOPEE 违禁品", ()),))
    replacement = document("REPLACEMENT")
    store = FakeStore(
        (SearchHit("BANNED", 1.0, ("keyword",)), SearchHit("ZXMO528", 0.8, ("keyword",)), SearchHit("REPLACEMENT", 0.5, ("keyword",))),
        {"BANNED": banned, "ZXMO528": ali_risk, "REPLACEMENT": replacement},
    )

    hits = HybridRetriever(store).search(RetrievalQuery("lamp", platform="shopee"), None, limit=2)

    assert [hit.main_sku for hit in hits] == ["ZXMO528", "REPLACEMENT"]
    assert hits[0].eligible_child_skus == ("ZXMO528-R", "ZXMO528-OK")
    assert hits[0].warnings == ("ZXMO528-R: Ali-高退款(其他)",)


def test_other_platform_does_not_filter_explicit_shopee_ban_and_missing_document_errors() -> None:
    family = document("YNFBA997", (ChildVariant("YNFBA997-S", "Shopee child", "Shopee违禁品", ()),))
    store = FakeStore((SearchHit("YNFBA997", 1.0, ("keyword",)),), {"YNFBA997": family})
    hits = HybridRetriever(store).search(RetrievalQuery("lamp", platform="Ali"), None)
    assert hits[0].eligible_child_skus == ("YNFBA997-S",)
    assert hits[0].warnings == ("YNFBA997-S: Shopee违禁品",)

    with pytest.raises(RuntimeError, match="missing catalog document"):
        HybridRetriever(FakeStore((SearchHit("GONE", 1.0, ("keyword",)),), {})).search(RetrievalQuery("lamp"), None)
