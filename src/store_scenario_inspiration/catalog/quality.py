"""Deterministic catalog build quality checks and version comparisons."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
import re

from .models import ProductFamilyDocument, SourceRow
from .normalize import clean_optional_text, normalize_compare


_METRIC_NAMES = (
    "source_row_count",
    "child_count",
    "document_count",
    "searchable_document_count",
    "multi_variant_group_count",
    "missing_product_name_count",
    "placeholder_product_name_count",
    "mixed_category_document_count",
    "exact_document_collision_count",
)


@dataclass(frozen=True)
class QualityReport:
    """A serializable accounting of one complete catalog document build."""

    source_row_count: int
    child_count: int
    document_count: int
    searchable_document_count: int
    multi_variant_group_count: int
    missing_product_name_count: int
    placeholder_product_name_count: int
    mixed_category_document_count: int
    exact_document_collision_count: int
    canonical_document_sha256: str
    embedding_document_ids: tuple[str, ...]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]

    @classmethod
    def empty(cls) -> "QualityReport":
        return cls(
            source_row_count=0,
            child_count=0,
            document_count=0,
            searchable_document_count=0,
            multi_variant_group_count=0,
            missing_product_name_count=0,
            placeholder_product_name_count=0,
            mixed_category_document_count=0,
            exact_document_collision_count=0,
            canonical_document_sha256=_canonical_hash(()),
            embedding_document_ids=(),
            warnings=(),
            errors=(),
        )


class QualityGateError(ValueError):
    """Raised when one or more blocking catalog invariants fail."""

    def __init__(self, report: QualityReport) -> None:
        self.report = report
        self.errors = report.errors
        super().__init__("; ".join(report.errors))


@dataclass(frozen=True)
class QualityMetricDelta:
    """One absolute and percentage comparison against a prior build."""

    absolute_delta: int | None
    percentage_delta: float | None


@dataclass(frozen=True)
class QualityDelta:
    """Non-blocking metrics comparison between two catalog builds."""

    previous: QualityReport | None
    current: QualityReport
    metrics: dict[str, QualityMetricDelta]


def _canonical_json(documents: Sequence[ProductFamilyDocument]) -> bytes:
    payload = [document.to_index_dict() for document in documents]
    payload.sort(key=lambda value: (str(value["main_sku"]), str(value["doc_id"])))
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _canonical_hash(documents: Sequence[ProductFamilyDocument]) -> str:
    return sha256(_canonical_json(documents)).hexdigest()


def _is_missing(value: str) -> bool:
    return not normalize_compare(value)


def _is_placeholder(value: str) -> bool:
    return bool(normalize_compare(value)) and clean_optional_text(value) is None


def _identifier_in_vector_text(identifier: str, vector_text: str) -> bool:
    """Match an entire ASCII SKU token, avoiding ``SKU-1`` in ``SKU-10``."""

    normalized_identifier = normalize_compare(identifier)
    normalized_text = normalize_compare(vector_text)
    if not normalized_identifier:
        return False
    pattern = rf"(?<![A-Za-z0-9_-]){re.escape(normalized_identifier)}(?![A-Za-z0-9_-])"
    return re.search(pattern, normalized_text) is not None


def _duplicate_errors(
    label: str, values: Sequence[tuple[str, str]]
) -> list[str]:
    raw_by_normalized: dict[str, list[str]] = defaultdict(list)
    for normalized, raw in values:
        raw_by_normalized[normalized].append(raw)
    return [
        f"duplicate {label} '{normalized}': raw values {raw_values!r}"
        for normalized, raw_values in sorted(raw_by_normalized.items())
        if len(raw_values) > 1
    ]


def validate_build(
    rows: Sequence[SourceRow], documents: Sequence[ProductFamilyDocument]
) -> QualityReport:
    """Check all blocking catalog invariants and return deterministic metrics.

    ``embedding_document_ids`` is deliberately only a request candidate list.
    The embedding subsystem consumes it later; this keeps the quality gate free
    from an embedding provider while making unsearchable exclusion explicit.
    """

    source_children = [
        (normalize_compare(row.main_sku), normalize_compare(row.sku), row.sku)
        for row in rows
    ]
    document_children = [
        (normalize_compare(document.main_sku), normalize_compare(child.sku), child.sku)
        for document in documents
        for child in document.children
    ]
    errors = _duplicate_errors(
        "child sku", [(sku, raw) for _, sku, raw in source_children]
    )
    errors.extend(
        _duplicate_errors(
            "document main sku",
            [(normalize_compare(document.main_sku), document.main_sku) for document in documents],
        )
    )
    errors.extend(
        _duplicate_errors(
            "document child sku", [(sku, raw) for _, sku, raw in document_children]
        )
    )

    source_counter = Counter((main_sku, sku) for main_sku, sku, _ in source_children)
    document_counter = Counter(
        (main_sku, sku) for main_sku, sku, _ in document_children
    )
    for main_sku, sku in sorted(source_counter.keys() - document_counter.keys()):
        errors.append(f"missing child sku '{sku}' for main sku '{main_sku}'")
    for main_sku, sku in sorted(document_counter.keys() - source_counter.keys()):
        errors.append(f"extra child sku '{sku}' for main sku '{main_sku}'")
    for key in sorted(source_counter.keys() & document_counter.keys()):
        if source_counter[key] != document_counter[key]:
            main_sku, sku = key
            errors.append(
                f"child sku '{sku}' for main sku '{main_sku}' occurs "
                f"{source_counter[key]} times in source and {document_counter[key]} times in documents"
            )

    source_main_skus = {main_sku for main_sku, _, _ in source_children}
    document_main_skus = {normalize_compare(document.main_sku) for document in documents}
    for main_sku in sorted(source_main_skus - document_main_skus):
        errors.append(f"missing document for main sku '{main_sku}'")
    for main_sku in sorted(document_main_skus - source_main_skus):
        errors.append(f"extra document for main sku '{main_sku}'")

    for document in documents:
        if not document.searchable:
            continue
        if _identifier_in_vector_text(document.main_sku, document.vector_text_v1):
            errors.append(
                f"vector text contains main sku '{normalize_compare(document.main_sku)}' "
                f"for document '{document.doc_id}'"
            )
        for child in document.children:
            if _identifier_in_vector_text(child.sku, document.vector_text_v1):
                errors.append(
                    f"vector text contains child sku '{normalize_compare(child.sku)}' "
                    f"for document '{document.doc_id}'"
                )

    canonical_payloads = [
        json.dumps(
            document.to_index_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        for document in documents
    ]
    collision_count = sum(count - 1 for count in Counter(canonical_payloads).values() if count > 1)
    mixed_category_count = sum(
        "mixed_leaf_category" in document.quality_flags for document in documents
    )
    missing_name_count = sum(_is_missing(row.product_name) for row in rows)
    placeholder_name_count = sum(_is_placeholder(row.product_name) for row in rows)
    warnings: list[str] = []
    if missing_name_count:
        warnings.append(f"missing product names: {missing_name_count}")
    if placeholder_name_count:
        warnings.append(f"placeholder product names: {placeholder_name_count}")
    if mixed_category_count:
        warnings.append(f"mixed-category documents: {mixed_category_count}")
    if collision_count:
        warnings.append(f"exact document collisions: {collision_count}")

    report = QualityReport(
        source_row_count=len(rows),
        child_count=len(document_children),
        document_count=len(documents),
        searchable_document_count=sum(document.searchable for document in documents),
        multi_variant_group_count=sum(len(document.children) > 1 for document in documents),
        missing_product_name_count=missing_name_count,
        placeholder_product_name_count=placeholder_name_count,
        mixed_category_document_count=mixed_category_count,
        exact_document_collision_count=collision_count,
        canonical_document_sha256=_canonical_hash(documents),
        embedding_document_ids=tuple(
            document.doc_id for document in documents if document.searchable
        ),
        warnings=tuple(warnings),
        errors=tuple(errors),
    )
    if report.errors:
        raise QualityGateError(report)
    return report


def compare_quality(
    previous: QualityReport | None, current: QualityReport
) -> QualityDelta:
    """Return non-blocking count deltas; zero baselines have no percentage."""

    metrics: dict[str, QualityMetricDelta] = {}
    for name in _METRIC_NAMES:
        current_value = getattr(current, name)
        if previous is None:
            metrics[name] = QualityMetricDelta(None, None)
            continue
        previous_value = getattr(previous, name)
        absolute_delta = current_value - previous_value
        percentage_delta = (
            None if previous_value == 0 else absolute_delta / previous_value * 100
        )
        metrics[name] = QualityMetricDelta(absolute_delta, percentage_delta)
    return QualityDelta(previous=previous, current=current, metrics=metrics)
