"""Immutable contracts for source rows, catalog documents, and search results."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceRow:
    """One ERP product row, preserved as text for deterministic processing."""

    sku: str
    main_sku: str
    product_name: str
    english_name: str
    english_keywords: str
    sales_status_raw: str
    product_catalog: str
    category_level_1: str
    category_level_2: str
    category_level_3: str
    category_level_4: str


@dataclass(frozen=True)
class ChildVariant:
    """A purchasable child SKU retained as document metadata."""

    sku: str
    display_name: str
    sales_status_raw: str
    status_flags: tuple[str, ...]


@dataclass(frozen=True)
class ProductFamilyDocument:
    """A main-SKU product family with separately stored retrieval evidence."""

    doc_id: str
    main_sku: str
    searchable: bool
    cn_names: tuple[str, ...]
    en_aliases: tuple[str, ...]
    leaf_categories: tuple[str, ...]
    category_paths: tuple[tuple[str, ...], ...]
    vector_text_v1: str
    children: tuple[ChildVariant, ...]
    quality_flags: tuple[str, ...]

    def to_index_dict(self) -> dict[str, object]:
        """Return a JSON-ready document with identifiers restricted to metadata."""

        return {
            "doc_id": self.doc_id,
            "main_sku": self.main_sku,
            "searchable": self.searchable,
            "keyword_fields": {
                "cn_names": list(self.cn_names),
                "en_aliases": list(self.en_aliases),
                "leaf_categories": list(self.leaf_categories),
                "category_paths": [list(path) for path in self.category_paths],
            },
            "vector_text_v1": self.vector_text_v1,
            "children": [
                {
                    "sku": child.sku,
                    "display_name": child.display_name,
                    "sales_status_raw": child.sales_status_raw,
                    "status_flags": list(child.status_flags),
                }
                for child in self.children
            ],
            "quality": {
                "group_size": len(self.children),
                "flags": list(self.quality_flags),
            },
        }


@dataclass(frozen=True)
class BuildManifest:
    """Metadata describing one immutable catalog build."""

    version_id: str
    source_sha256: str
    schema_version: str
    cleaning_rules_version: str
    embedding_model_id: str
    built_at: str
    document_count: int
    vector_status: str
    source_sheet_name: str | None = None
    source_modified_at: str = ""


@dataclass(frozen=True)
class SearchHit:
    """A product-family search result and its retrieval evidence."""

    main_sku: str
    score: float
    sources: tuple[str, ...]
    eligible_child_skus: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
