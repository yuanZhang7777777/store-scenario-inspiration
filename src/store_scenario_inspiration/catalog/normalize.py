"""Deterministic normalization for catalog text fields."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable


_PLACEHOLDERS = frozenset(
    {"0", "1", "无", "n-a", "n/a", "na", "none", "null", "unknown", "未知"}
)
_OPERATIONAL_MARKERS = (
    "链接随便拍",
    "链接拍",
    "采购看",
    "仓库看",
    "下单备注",
    "供应商按照",
    "供应商打包",
    "控制在",
    "备货量",
    "市场分析",
    "头程费用",
    "本地上传",
)
_CHINESE_SEGMENT = re.compile(r"[\u4e00-\u9fff]+")
_ALPHANUMERIC_WORD = re.compile(r"[A-Za-z0-9]+")


def normalize_compare(value: object) -> str:
    """Convert a raw cell to normalized text suitable for comparisons."""

    if value is None:
        return ""
    normalized = unicodedata.normalize("NFKC", str(value))
    return " ".join(normalized.split())


def clean_optional_text(value: object) -> str | None:
    """Return usable normalized text, or ``None`` for a whole-cell placeholder."""

    normalized = normalize_compare(value)
    if not normalized or normalized.lower() in _PLACEHOLDERS:
        return None
    return normalized


def _truncate_operational_text(name: str) -> str:
    marker_positions = (name.find(marker) for marker in _OPERATIONAL_MARKERS)
    position = min((index for index in marker_positions if index > 0), default=-1)
    if position == -1:
        return name

    prefix = clean_optional_text(name[:position])
    return prefix if prefix is not None else name


def prepare_vector_names(names: Iterable[str]) -> tuple[str, ...]:
    """Clean, bound, deduplicate, and deterministically order Chinese names."""

    prepared: set[str] = set()
    for name in names:
        cleaned = clean_optional_text(name)
        if cleaned is None:
            continue
        prepared.add(_truncate_operational_text(cleaned)[:120])
    return tuple(sorted(prepared, key=lambda name: (len(name), name))[:8])


def make_vector_text(names: Iterable[str]) -> str:
    """Build the v1 vector-text payload from cleaned product names only."""

    prepared = prepare_vector_names(names)
    if not prepared:
        return ""
    return f"商品名称：{'；'.join(prepared)}"


def keyword_tokens(value: str) -> tuple[str, ...]:
    """Produce deterministic FTS-ready Chinese bigrams and alphanumeric tokens."""

    normalized = normalize_compare(value)
    matches = [
        (match.start(), True, match.group())
        for match in _CHINESE_SEGMENT.finditer(normalized)
    ] + [
        (match.start(), False, match.group())
        for match in _ALPHANUMERIC_WORD.finditer(normalized)
    ]
    tokens: list[str] = []
    seen: set[str] = set()
    for _, is_chinese, segment in sorted(matches):
        candidates = (
            (segment,)
            if not is_chinese or len(segment) == 1
            else (segment[index : index + 2] for index in range(len(segment) - 1))
        )
        for token in candidates:
            if token not in seen:
                seen.add(token)
                tokens.append(token)
    return tuple(tokens)
