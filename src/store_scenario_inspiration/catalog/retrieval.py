"""Hybrid catalog retrieval with deterministic RRF fusion and child-level risks."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

import numpy as np

from .models import ChildVariant, SearchHit
from .storage import CatalogStore
from .vectors import ExactVectorIndex

_RRF_OFFSET = 60
_SOURCE_ORDER = ("keyword", "vector")
_RISK_TOKENS = ("违禁", "禁售", "侵权", "高退款", "质量", "banned", "prohibited")
_BAN_TOKENS = ("违禁", "禁售", "banned", "prohibited")
_EXTRA_RISK_TOKENS = ("侵权", "高退款", "质量")
_CJK_PLATFORM = re.compile(r"[\u3400-\u9fff]+\Z")
_CJK_DELIMITERS = frozenset("-_/\\|,:;，、；：()[]{}（）【】<>《》\"'“”")


def _normalize_text(value: str, *, field: str, optional: bool = False) -> str | None:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if normalized:
        return normalized
    if optional:
        return None
    raise ValueError(f"{field} must contain text")


@dataclass(frozen=True)
class RetrievalQuery:
    """A normalized retrieval request; country is retained as contextual metadata."""

    text: str
    platform: str | None = None
    country: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _normalize_text(self.text, field="text"))
        for field in ("platform", "country"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _normalize_text(value, field=field, optional=True))


class HybridRetriever:
    """Fuse keyword and optional vector candidates, then filter explicit child bans."""

    def __init__(self, store: CatalogStore, vector_index: ExactVectorIndex | None = None) -> None:
        self._store = store
        self._vector_index = vector_index

    def search(
        self,
        query: RetrievalQuery,
        query_vector: np.ndarray | None,
        limit: int = 5,
    ) -> tuple[SearchHit, ...]:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        if query_vector is not None and self._vector_index is None:
            raise ValueError("query vector requires a vector index")

        candidate_limit = max(50, limit * 10)
        channels: list[tuple[str, tuple[SearchHit, ...]]] = [
            ("keyword", self._store.keyword_search(query.text, candidate_limit))
        ]
        if query_vector is not None and self._vector_index is not None:
            channels.append(("vector", self._vector_index.search(query_vector, candidate_limit)))

        fused: dict[str, dict[str, object]] = {}
        for source, candidates in channels:
            seen_in_source: set[str] = set()
            for position, candidate in enumerate(candidates, start=1):
                if candidate.main_sku in seen_in_source:
                    continue
                seen_in_source.add(candidate.main_sku)
                evidence = fused.setdefault(candidate.main_sku, {"score": 0.0, "sources": set()})
                evidence["score"] = float(evidence["score"]) + 1 / (_RRF_OFFSET + position)
                cast_sources = evidence["sources"]
                assert isinstance(cast_sources, set)
                cast_sources.add(source)

        ranked = sorted(fused.items(), key=lambda item: (-float(item[1]["score"]), item[0]))
        results: list[SearchHit] = []
        for main_sku, evidence in ranked:
            document = self._store.get_document(main_sku)
            if document is None:
                raise RuntimeError(f"missing catalog document for main_sku: {main_sku}")
            eligible, warnings = _child_availability(document.children, query.platform)
            if document.children and not eligible:
                continue
            evidence_sources = evidence["sources"]
            assert isinstance(evidence_sources, set)
            results.append(
                SearchHit(
                    main_sku=document.main_sku,
                    score=float(evidence["score"]),
                    sources=tuple(source for source in _SOURCE_ORDER if source in evidence_sources),
                    eligible_child_skus=eligible,
                    warnings=warnings,
                )
            )
            if len(results) == limit:
                break
        return tuple(results)


def _child_availability(
    children: tuple[ChildVariant, ...], platform: str | None
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    platform_key = _platform_key(platform)
    eligible: list[str] = []
    warnings: list[str] = []
    warning_seen: set[str] = set()
    for child in children:
        status = child.sales_status_raw
        normalized_status = _status_key(status)
        is_banned = normalized_status and _is_explicit_platform_ban(normalized_status, platform_key)
        if normalized_status and _is_risk_status(normalized_status) and (
            not is_banned or platform_key is None or _has_extra_risk(normalized_status)
        ):
            warning = f"{child.sku}: {status}"
            if warning not in warning_seen:
                warning_seen.add(warning)
                warnings.append(warning)
        if is_banned:
            continue
        eligible.append(child.sku)
    return tuple(eligible), tuple(warnings)


def _platform_key(platform: str | None) -> str | None:
    return None if platform is None else unicodedata.normalize("NFKC", platform).casefold()


def _status_key(status: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", status).split()).casefold()


def _is_explicit_platform_ban(status: str, platform: str | None) -> bool:
    return (
        platform is not None
        and _matches_platform_label(status, platform)
        and any(token in status for token in _BAN_TOKENS)
    )


def _matches_platform_label(status: str, platform: str) -> bool:
    if any(character.isascii() and character.isalnum() for character in platform):
        return re.search(
            rf"(?<![A-Za-z0-9]){re.escape(platform)}(?![A-Za-z0-9])", status
        ) is not None
    if _CJK_PLATFORM.fullmatch(platform) is None:
        return False
    start = status.find(platform)
    while start != -1:
        if start == 0 or status[start - 1].isspace() or status[start - 1] in _CJK_DELIMITERS:
            return True
        start = status.find(platform, start + 1)
    return False


def _is_risk_status(status: str) -> bool:
    return any(token in status for token in _RISK_TOKENS)


def _has_extra_risk(status: str) -> bool:
    return any(token in status for token in _EXTRA_RISK_TOKENS)
