import pytest

from store_scenario_inspiration.catalog.normalize import (
    clean_optional_text,
    keyword_tokens,
    make_vector_text,
    normalize_compare,
    prepare_vector_names,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        ("  Ａ\tＢ\nＣ  ", "A B C"),
        (" 40L防水袋 ", "40L防水袋"),
    ],
)
def test_normalize_compare_nfkc_trims_and_collapses_whitespace(
    value: object, expected: str
) -> None:
    assert normalize_compare(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        0,
        1,
        "0",
        " １ ",
        "无",
        "n-a",
        "N/A",
        "na",
        "none",
        "NULL",
        "unknown",
        "未知",
    ],
)
def test_clean_optional_text_removes_every_whole_cell_placeholder(value: object) -> None:
    assert clean_optional_text(value) is None


def test_clean_optional_text_preserves_digits_and_valid_product_names() -> None:
    assert clean_optional_text("40L防水袋") == "40L防水袋"
    assert clean_optional_text("16寸风扇") == "16寸风扇"
    assert clean_optional_text("N/A防水袋") == "N/A防水袋"
    assert clean_optional_text("1号收纳箱") == "1号收纳箱"


def test_vector_names_truncate_only_trailing_operational_text() -> None:
    names = [
        "露营折叠桌 链接拍红色款",
        "链接拍专用防水袋",
        "40L防水袋采购看供应商",
    ]

    assert prepare_vector_names(names) == (
        "露营折叠桌",
        "40L防水袋",
        "链接拍专用防水袋",
    )


def test_vector_names_are_stable_bounded_and_deduplicated() -> None:
    names = ["蓝色40L防水袋", " 蓝色40L防水袋 ", "8L防水袋"] * 5
    prepared = prepare_vector_names(names)

    assert prepared == ("8L防水袋", "蓝色40L防水袋")
    assert make_vector_text(names) == "商品名称：8L防水袋；蓝色40L防水袋"
    assert all(len(name) <= 120 for name in prepared)
    assert len(prepared) <= 8


def test_vector_names_limit_individual_names_before_sorting_and_keep_eight() -> None:
    names = ["甲" * 121, *[f"样品{i}" for i in range(10)]]

    assert prepare_vector_names(names) == tuple(f"样品{i}" for i in range(8))


def test_vector_names_truncate_a_single_121_character_name_to_120_characters() -> None:
    assert prepare_vector_names(["甲" * 121]) == ("甲" * 120,)


def test_vector_functions_accept_only_chinese_name_field_values_without_charset_filtering() -> None:
    names_from_chinese_product_name_field = ["40L防水袋", "XK-40型号收纳箱"]

    assert prepare_vector_names(names_from_chinese_product_name_field) == (
        "40L防水袋",
        "XK-40型号收纳箱",
    )
    assert (
        make_vector_text(names_from_chinese_product_name_field)
        == "商品名称：40L防水袋；XK-40型号收纳箱"
    )


def test_make_vector_text_is_empty_without_valid_names() -> None:
    assert make_vector_text(["N/A", " 1 "]) == ""


def test_keyword_tokens_generate_chinese_bigrams_and_preserve_single_char_and_alphanumerics() -> None:
    assert keyword_tokens(" 户外灯 40L-A7 防水 ") == (
        "户外",
        "外灯",
        "40L",
        "A7",
        "防水",
    )
    assert keyword_tokens("灯 1") == ("灯", "1")


def test_keyword_tokens_are_deduplicated_in_first_appearance_order() -> None:
    assert keyword_tokens("防水袋 防水袋 40L 40L") == ("防水", "水袋", "40L")
