from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from store_scenario_inspiration.catalog.models import (
    BuildManifest,
    ChildVariant,
    ProductFamilyDocument,
)
from store_scenario_inspiration.catalog.storage import CatalogStore


def manifest(document_count: int = 2) -> BuildManifest:
    return BuildManifest(
        version_id="20260831T100000Z",
        source_sha256="a" * 64,
        schema_version="1",
        cleaning_rules_version="1",
        embedding_model_id="test-model",
        built_at="2026-08-31T10:00:00Z",
        document_count=document_count,
        vector_status="pending",
    )


@pytest.fixture
def documents() -> tuple[ProductFamilyDocument, ...]:
    return (
        ProductFamilyDocument(
            doc_id="main:ZXOD3713",
            main_sku="ZXOD3713",
            searchable=True,
            cn_names=("户外太阳能灯笼40L",),
            en_aliases=("solar lantern",),
            leaf_categories=("户外灯",),
            category_paths=(("运动及娱乐", "野营及徒步旅行", "户外灯"),),
            vector_text_v1="商品名称：户外太阳能灯笼",
            children=(
                ChildVariant(
                    sku="ZXOD3713-BLUE",
                    display_name="户外太阳能灯笼 蓝色",
                    sales_status_raw="Shopee 禁售",
                    status_flags=("shopee_banned",),
                ),
            ),
            quality_flags=("reviewed",),
        ),
        ProductFamilyDocument(
            doc_id="main:EN-ONLY",
            main_sku="EN-ONLY",
            searchable=True,
            cn_names=("露营灯",),
            en_aliases=("lantern",),
            leaf_categories=("营地装备",),
            category_paths=(("运动", "营地装备"),),
            vector_text_v1="商品名称：露营灯",
            children=(),
            quality_flags=(),
        ),
    )


def test_keyword_search_prefers_chinese_name_over_english_alias(
    tmp_path, documents
) -> None:
    store = CatalogStore.create(tmp_path / "catalog.sqlite3", documents, manifest())
    try:
        hits = store.keyword_search("户外太阳能灯笼", limit=5)
    finally:
        store.close()

    assert hits[0].main_sku == "ZXOD3713"
    assert hits[0].sources == ("keyword",)
    assert hits[0].score > 0


def test_keyword_search_breaks_equal_bm25_scores_by_main_sku(tmp_path, documents) -> None:
    same_evidence = replace(documents[0], doc_id="main:AAA", main_sku="AAA")

    with CatalogStore.create(
        tmp_path / "catalog.sqlite3", (documents[0], same_evidence), manifest()
    ) as store:
        hits = store.keyword_search("户外太阳能灯笼", limit=5)

    assert [hit.main_sku for hit in hits] == ["AAA", "ZXOD3713"]
    assert hits[0].score == hits[1].score


def test_storage_round_trips_documents_manifest_and_children_losslessly(
    tmp_path, documents
) -> None:
    path = tmp_path / "catalog.sqlite3"
    store = CatalogStore.create(path, documents, manifest())
    store.close()

    with CatalogStore.open_readonly(path) as reopened:
        assert reopened.get_document("  ZXOD3713  ") == documents[0]
        assert reopened.get_document("missing") is None
        assert reopened.manifest == manifest()


def test_fts_indexes_only_searchable_keyword_fields_and_expands_alias_once(
    tmp_path, documents
) -> None:
    unsearchable = ProductFamilyDocument(
        doc_id="main:HIDDEN",
        main_sku="HIDDEN",
        searchable=False,
        cn_names=("不可检索商品",),
        en_aliases=("hidden",),
        leaf_categories=("隐藏类",),
        category_paths=(),
        vector_text_v1="",
        children=(ChildVariant("CHILD-SECRET", "不可检索商品", "停售", ()),),
        quality_flags=("unsearchable",),
    )
    path = tmp_path / "catalog.sqlite3"
    duplicated_alias = replace(
        documents[0], en_aliases=("solar lantern", "solar lantern")
    )
    store = CatalogStore.create(
        path, (duplicated_alias, documents[1], unsearchable), manifest(document_count=3)
    )
    store.close()

    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            "SELECT main_sku, cn_names, categories, en_aliases FROM product_fts ORDER BY main_sku"
        ).fetchall()
    finally:
        connection.close()

    assert [row[0] for row in rows] == ["EN-ONLY", "ZXOD3713"]
    solar_alias_text = rows[1][3]
    assert solar_alias_text == "solar lantern"
    assert rows[1][1].count("40L") == 1
    assert "ZXOD3713" not in " ".join(rows[1][1:])
    assert "ZXOD3713-BLUE" not in " ".join(rows[1][1:])
    assert "Shopee 禁售" not in " ".join(rows[1][1:])

    with CatalogStore.open_readonly(path) as readonly:
        assert readonly.keyword_search("ZXOD3713", limit=5) == ()
        assert readonly.keyword_search("CHILD-SECRET", limit=5) == ()
        assert readonly.keyword_search("禁售", limit=5) == ()
        assert readonly.keyword_search("hidden", limit=5) == ()


@pytest.mark.parametrize(
    ("query", "expected_sku"),
    [
        ('"户外"', "ZXOD3713"),
        ("solar-lantern", "ZXOD3713"),
        ("OR 户外", "ZXOD3713"),
    ],
)
def test_keyword_search_treats_fts_syntax_as_plain_token_input(
    tmp_path, documents, query, expected_sku
) -> None:
    with CatalogStore.create(tmp_path / "catalog.sqlite3", documents, manifest()) as store:
        hits = store.keyword_search(query, limit=5)

    assert hits[0].main_sku == expected_sku


def test_keyword_search_matches_a_single_chinese_character_in_a_two_character_name(
    tmp_path, documents
) -> None:
    lamp_only = replace(
        documents[1],
        cn_names=("台灯",),
        en_aliases=(),
        leaf_categories=(),
        category_paths=(),
    )

    with CatalogStore.create(tmp_path / "catalog.sqlite3", (lamp_only,), manifest(1)) as store:
        hits = store.keyword_search("灯", limit=1)

    assert [hit.main_sku for hit in hits] == ["EN-ONLY"]


@pytest.mark.parametrize("query", ["", " () ", "---", '""'])
def test_keyword_search_rejects_queries_without_tokens(tmp_path, documents, query) -> None:
    with CatalogStore.create(tmp_path / "catalog.sqlite3", documents, manifest()) as store:
        with pytest.raises(ValueError, match="at least one searchable token"):
            store.keyword_search(query, limit=1)


@pytest.mark.parametrize("limit", [0, -1])
def test_keyword_search_rejects_non_positive_limits(tmp_path, documents, limit) -> None:
    with CatalogStore.create(tmp_path / "catalog.sqlite3", documents, manifest()) as store:
        with pytest.raises(ValueError, match="positive"):
            store.keyword_search("灯笼", limit=limit)


def test_readonly_store_allows_queries_but_rejects_writes(tmp_path, documents) -> None:
    path = tmp_path / "catalog.sqlite3"
    with CatalogStore.create(path, documents, manifest()):
        pass

    with CatalogStore.open_readonly(path) as store:
        assert store.keyword_search("灯笼", limit=1)[0].main_sku == "ZXOD3713"
        with pytest.raises(sqlite3.OperationalError):
            store.connection.execute("DELETE FROM documents")


def test_create_does_not_overwrite_an_existing_database(tmp_path, documents) -> None:
    path = tmp_path / "catalog.sqlite3"
    with CatalogStore.create(path, documents, manifest()):
        pass

    with pytest.raises(FileExistsError):
        CatalogStore.create(path, documents, manifest())


def test_create_failure_leaves_no_target_or_temporary_database(tmp_path, documents) -> None:
    path = tmp_path / "catalog.sqlite3"
    duplicate_identity = replace(documents[1], main_sku=" ZXOD3713 ")

    with pytest.raises(sqlite3.IntegrityError):
        CatalogStore.create(path, (documents[0], duplicate_identity), manifest())

    assert not path.exists()
    assert list(tmp_path.glob(".catalog.sqlite3.*.tmp")) == []


def test_create_rejects_manifest_document_count_mismatch_without_artifacts(
    tmp_path, documents
) -> None:
    path = tmp_path / "catalog.sqlite3"

    with pytest.raises(ValueError, match="document_count"):
        CatalogStore.create(path, documents, manifest(document_count=3))

    assert not path.exists()
    assert list(tmp_path.glob(".catalog.sqlite3.*.tmp")) == []


def test_create_persists_expected_document_child_and_fts_row_counts(tmp_path, documents) -> None:
    unsearchable = replace(documents[1], main_sku="HIDDEN", searchable=False)
    path = tmp_path / "catalog.sqlite3"
    store = CatalogStore.create(path, (*documents, unsearchable), manifest(document_count=3))
    store.close()

    connection = sqlite3.connect(path)
    try:
        counts = tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("documents", "children", "product_fts")
        )
    finally:
        connection.close()

    assert counts == (3, 1, 2)


def test_closed_store_raises_a_clear_error_from_public_query_apis(tmp_path, documents) -> None:
    store = CatalogStore.create(tmp_path / "catalog.sqlite3", documents, manifest())
    store.close()
    store.close()

    with pytest.raises(RuntimeError, match="catalog store is closed"):
        store.get_document("ZXOD3713")
    with pytest.raises(RuntimeError, match="catalog store is closed"):
        store.keyword_search("灯笼", limit=1)
