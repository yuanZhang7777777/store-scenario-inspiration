from store_scenario_inspiration.catalog.models import ProductFamilyDocument


def test_index_dict_never_places_ids_in_vector_text() -> None:
    doc = ProductFamilyDocument(
        doc_id="main:ZXOD3713",
        main_sku="ZXOD3713",
        searchable=True,
        cn_names=("户外太阳能灯笼",),
        en_aliases=("solar lantern",),
        leaf_categories=("户外灯",),
        category_paths=(("运动及娱乐", "野营及徒步旅行", "户外灯"),),
        vector_text_v1="商品名称：户外太阳能灯笼",
        children=(),
        quality_flags=(),
    )

    payload = doc.to_index_dict()

    assert payload["main_sku"] == "ZXOD3713"
    assert "ZXOD3713" not in payload["vector_text_v1"]
