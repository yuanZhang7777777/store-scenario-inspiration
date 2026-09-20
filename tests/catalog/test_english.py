from store_scenario_inspiration.catalog.english import english_document
from store_scenario_inspiration.catalog.models import ChildVariant, ProductFamilyDocument


def document(
    en_aliases: tuple[str, ...],
    quality_flags: tuple[str, ...] = ("existing",),
    main_sku: str = "SKU-1",
) -> ProductFamilyDocument:
    return ProductFamilyDocument(
        doc_id=f"main:{main_sku}",
        main_sku=main_sku,
        searchable=False,
        cn_names=("露营灯",),
        en_aliases=en_aliases,
        leaf_categories=("户外灯",),
        category_paths=(("户外", "灯"),),
        vector_text_v1="商品名称：露营灯",
        children=(ChildVariant("SKU-1-A", "露营灯 黑色", "在售", ()),),
        quality_flags=quality_flags,
    )


def test_english_document_builds_vector_text_from_english_aliases_only() -> None:
    result = english_document(
        document(
            (
                "camping lantern",
                "Camping Lantern",
                "AB12",
                "123456",
                "SKU-1",
                "SKU-1 Optimized flying saucer Silver-Orange",
            )
        )
    )

    assert result.searchable is True
    assert result.vector_text_v1 == (
        "Product name: AB12; Camping Lantern; "
        "Optimized flying saucer Silver-Orange"
    )
    assert result.en_aliases == (
        "AB12",
        "Camping Lantern",
        "Optimized flying saucer Silver-Orange",
    )
    assert result.cn_names == ("露营灯",)
    assert result.children[0].display_name == "露营灯 黑色"
    assert "missing_english" not in result.quality_flags


def test_english_document_removes_chinese_noise_from_english_source_values() -> None:
    result = english_document(document(("Solar 露营 Lamp 12V", "露营灯", "LED-户外")))

    assert result.vector_text_v1 == "Product name: LED; Solar Lamp 12V"
    assert result.en_aliases == ("LED", "Solar Lamp 12V")
    assert "露营" not in result.vector_text_v1
    assert "户外" not in result.vector_text_v1


def test_english_document_marks_missing_english_without_dropping_source_data() -> None:
    source = document(("露营灯", "123456", "n/a"))

    result = english_document(source)

    assert result.searchable is False
    assert result.vector_text_v1 == ""
    assert result.en_aliases == ()
    assert result.cn_names == source.cn_names
    assert result.quality_flags == ("existing", "missing_english")


def test_english_document_keeps_excluded_main_sku_unsearchable() -> None:
    result = english_document(
        document(("Camping Lantern",), quality_flags=("excluded_main_sku",))
    )

    assert result.searchable is False
    assert result.vector_text_v1 == "Product name: Camping Lantern"


def test_english_document_uses_sourced_fallbacks_only_when_source_english_is_missing() -> None:
    lace = english_document(document((), main_sku="SH-CW-2339"))
    pendant = english_document(document(("1005009609633212",), main_sku="WATOY320"))
    sourced = english_document(document(("ERP Source Name",), main_sku="SH-CW-2339"))

    assert lace.searchable is True
    assert lace.en_aliases == ("Lace shorts",)
    assert lace.vector_text_v1 == "Product name: Lace shorts"
    assert "missing_english" not in lace.quality_flags
    assert pendant.searchable is True
    assert pendant.en_aliases == ("Miniature ancient weapon model pendant",)
    assert sourced.en_aliases == ("ERP Source Name",)
