from __future__ import annotations

import pytest

from store_scenario_inspiration.catalog import eligibility
from store_scenario_inspiration.catalog.build import build_documents
from store_scenario_inspiration.catalog.models import SourceRow


def _row(
    sku: str,
    main_sku: str,
    product_name: str,
    english_name: str = "",
    english_keywords: str = "",
    sales_status_raw: str = "",
    product_catalog: str = "运动及娱乐",
    category_level_1: str = "户外",
    category_level_2: str = "露营",
    category_level_3: str = "收纳",
    category_level_4: str = "防水袋",
) -> SourceRow:
    return SourceRow(
        sku=sku,
        main_sku=main_sku,
        product_name=product_name,
        english_name=english_name,
        english_keywords=english_keywords,
        sales_status_raw=sales_status_raw,
        product_catalog=product_catalog,
        category_level_1=category_level_1,
        category_level_2=category_level_2,
        category_level_3=category_level_3,
        category_level_4=category_level_4,
    )


def test_confirmed_family_exclusion_keeps_children_for_audit_but_disables_indexing() -> None:
    documents = {d.main_sku: d for d in build_documents((
        _row("1A7777", "1A0000", "拆单符SKU"),
        _row("FUTURE-CHILD", "1A0000", "后来加入同族的商品"),
        _row("KEEP", "1A00001", "普通商品"),
    ))}
    assert documents["1A0000"].searchable is False
    assert "excluded_main_sku" in documents["1A0000"].quality_flags
    assert {c.sku for c in documents["1A0000"].children} == {"1A7777", "FUTURE-CHILD"}
    assert documents["1A00001"].searchable is True


def test_confirmed_child_exclusions_leave_siblings_searchable_without_excluded_text(monkeypatch) -> None:
    monkeypatch.setattr(eligibility, "EXCLUDED_CHILD_SKUS", frozenset({"EXCLUDED", "ALL-OUT"}), raising=False)
    documents = {d.main_sku: d for d in build_documents((
        _row(" ＥＸＣＬＵＤＥＤ ", "MIXED", "售后杯", "replacement cup", category_level_4="配件"),
        _row("SAFE", "MIXED", "榨汁机", "juicer", category_level_4="厨房"),
        _row("ALL-OUT", "REMOVED", "纸箱"),
        _row("excluded", "CASE-SENSITIVE", "独立商品"),
    ))}
    mixed = documents["MIXED"]
    assert mixed.searchable
    assert mixed.cn_names == ("榨汁机",)
    assert mixed.en_aliases == ("juicer",)
    assert mixed.leaf_categories == ("厨房",)
    assert mixed.category_paths == (("户外", "露营", "收纳", "厨房"),)
    assert mixed.vector_text_v1 == "商品名称：榨汁机"
    assert mixed.children[0].display_name == "售后杯"
    assert mixed.children[0].status_flags == ("excluded_sku",)
    assert len(mixed.children) == 2
    assert not documents["REMOVED"].searchable
    assert documents["CASE-SENSITIVE"].searchable


def test_invalid_confirmed_exclusion_file_is_not_silently_ignored(monkeypatch) -> None:
    monkeypatch.setattr(eligibility.Path, "read_text", lambda *args, **kwargs: '{"child_skus": [null]}')
    with pytest.raises(ValueError, match="confirmed SKU exclusions are invalid"):
        eligibility._load_excluded_skus()


def test_build_documents_groups_children_and_keeps_only_product_names_in_vector_text() -> None:
    rows = (
        _row(
            "CHILD-SKU-SENTINEL",
            "MAIN-SKU-SENTINEL",
            "中文帽子40L型号ABC",
            "ENGLISH-NAME-SENTINEL",
            "ENGLISH-KEYWORD-SENTINEL",
            "平台类额外值-SENTINEL",
            "商品目录-SENTINEL",
            "一级目录-SENTINEL",
            "二级目录-SENTINEL",
            "三级目录-SENTINEL",
            "四级目录-SENTINEL",
        ),
        _row(
            "ZXOD3713-A",
            "ZXOD3713",
            "户外太阳能灯笼",
            "solar lantern",
            "solar lantern",
            "正常销售",
        ),
        _row(
            "ZXOD3713-B",
            "ZXOD3713",
            "户外太阳能灯笼",
            "solar lantern",
            "solar lantern",
            "Shopee违禁品",
        ),
        _row("ZXOD2149-40", "ZXOD2149", "蓝色40L防水袋"),
        _row("ZXOD2149-70", "ZXOD2149", "蓝色70L防水袋"),
        _row("ZXOD2149-8", "ZXOD2149", "蓝色8L防水袋"),
        _row("LSLFBA579A-A", "LSLFBA579A", "折叠露营椅", "1", "1"),
        _row(
            "ZXNXK0726-N2-A",
            "ZXNXK0726-N2",
            "防晒渔夫帽",
            "underwear briefs",
            "underwear briefs",
        ),
        _row(
            "test1123-A",
            "test1123",
            "1",
            "1",
            "1",
            product_catalog="",
            category_level_1="",
            category_level_2="",
            category_level_3="",
            category_level_4="",
        ),
    )

    documents = {document.main_sku: document for document in build_documents(rows)}

    assert set(documents) == {
        "MAIN-SKU-SENTINEL",
        "ZXOD3713",
        "ZXOD2149",
        "LSLFBA579A",
        "ZXNXK0726-N2",
        "test1123",
    }
    assert [child.sku for child in documents["ZXOD3713"].children] == [
        "ZXOD3713-A",
        "ZXOD3713-B",
    ]
    assert documents["ZXOD3713"].en_aliases == ("solar lantern",)
    assert len(documents["ZXOD2149"].children) == 3
    assert documents["ZXOD2149"].vector_text_v1 == (
        "商品名称：蓝色8L防水袋；蓝色40L防水袋；蓝色70L防水袋"
    )
    assert documents["LSLFBA579A"].en_aliases == ()
    assert documents["LSLFBA579A"].vector_text_v1 == "商品名称：折叠露营椅"
    assert documents["ZXNXK0726-N2"].vector_text_v1 == "商品名称：防晒渔夫帽"
    assert documents["test1123"].searchable is False
    assert "unsearchable" in documents["test1123"].quality_flags

    vector_text = documents["MAIN-SKU-SENTINEL"].vector_text_v1
    assert vector_text == "商品名称：中文帽子40L型号ABC"
    for non_product_name_value in (
        "MAIN-SKU-SENTINEL",
        "CHILD-SKU-SENTINEL",
        "ENGLISH-NAME-SENTINEL",
        "ENGLISH-KEYWORD-SENTINEL",
        "平台类额外值-SENTINEL",
        "商品目录-SENTINEL",
        "一级目录-SENTINEL",
        "二级目录-SENTINEL",
        "三级目录-SENTINEL",
        "四级目录-SENTINEL",
    ):
        assert non_product_name_value not in vector_text


def test_build_documents_uses_deepest_category_and_marks_only_actual_flags() -> None:
    document = build_documents(
        (
            _row(
                "child-1",
                "MAIN-1",
                "露营灯链接拍采购看",
                "1",
                "1",
                category_level_4="",
                category_level_3="灯具",
            ),
            _row(
                "child-2",
                "MAIN-1",
                "露营灯",
                "camp light",
                "camp light",
                category_level_4="营地灯",
            ),
        )
    )[0]

    assert document.leaf_categories == ("灯具", "营地灯")
    assert document.category_paths == (("户外", "露营", "灯具"), ("户外", "露营", "收纳", "营地灯"))
    assert set(document.quality_flags) == {
        "mixed_leaf_category",
        "placeholder_english",
        "operational_text",
    }
    assert document.vector_text_v1 == "商品名称：露营灯"


def test_build_documents_retains_raw_children_while_normalizing_group_and_dedup_keys() -> None:
    documents = build_documents(
        (
            _row(
                " ＳＫＵ-1 ",
                " ＭＡＩＮ-1 ",
                "　露营　灯　",
                sales_status_raw="　状态  一　",
            ),
            _row(
                "SKU-2",
                "MAIN-1",
                "露营 灯",
                sales_status_raw=" status two ",
            ),
        )
    )

    assert len(documents) == 1
    document = documents[0]
    assert document.main_sku == "MAIN-1"
    assert document.doc_id == "main:MAIN-1"
    assert document.cn_names == ("　露营　灯　",)
    assert document.vector_text_v1 == "商品名称：露营 灯"
    assert [(child.sku, child.display_name, child.sales_status_raw) for child in document.children] == [
        (" ＳＫＵ-1 ", "　露营　灯　", "　状态  一　"),
        ("SKU-2", "露营 灯", " status two "),
    ]


def test_build_documents_removes_known_sku_tokens_from_indexed_product_names(monkeypatch) -> None:
    # Isolate identifier normalization from the separately tested business exclusions.
    monkeypatch.setattr(eligibility, "EXCLUDED_CHILD_SKUS", frozenset())
    document = build_documents(
        (
            _row(
                "BCFBA378",
                "ZQFBA086",
                "纸箱，绑定ZQFBA086、ZQFBA086A",
            ),
            _row(
                "ZQFBA086",
                "ZQFBA086",
                "热巧克力礼品；仓库BCFBA378打包绑定ZQFBA086",
            ),
            _row("ZQFBA086A", "ZQFBA086", "热巧克力礼品补充款"),
        )
    )[0]

    indexed_text = " ".join((*document.cn_names, document.vector_text_v1))
    assert "纸箱" in indexed_text
    assert "热巧克力礼品" in indexed_text
    for identifier in ("BCFBA378", "ZQFBA086", "ZQFBA086A"):
        assert identifier not in indexed_text
    assert any("ZQFBA086" in child.display_name for child in document.children)


@pytest.mark.parametrize("sku, main_sku", [("", "MAIN-1"), ("child-1", "")])
def test_build_documents_rejects_rows_without_required_sku_identifiers(
    sku: str, main_sku: str
) -> None:
    with pytest.raises(ValueError, match="sku|main_sku"):
        build_documents((_row(sku, main_sku, "露营灯"),))
