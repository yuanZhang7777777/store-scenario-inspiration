import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from store_scenario_inspiration.app.scenes import (
    BAND_BACKED,
    BAND_STRETCH,
    BAND_UNSURE,
    rank_scenes,
)


def clue(name: str, role: str, confidence: float = 0.9) -> dict:
    return {"clue": name, "role": role, "confidence": confidence, "evidence": "依据"}


def scene(name: str, products: list[str]) -> dict:
    return {"scene_name": name, "audience": "住户", "user_need": "需要",
            "evidence": "证据", "product_needs": [
                {"product_cn": product, "product_en": "", "purpose": "用途"}
                for product in products
            ]}


SHELF = [
    clue("遮阳棚替换布", "商品卡片主图"),
    clue("便携 BBQ 烤架", "商品卡片主图"),
    clue("风扇", "商品卡片主图"),
]


def test_scene_resting_on_shelf_products_is_backed() -> None:
    ranked = rank_scenes({"scenes": [scene("庭院遮阳", ["遮阳棚替换布", "风扇"])]}, SHELF)

    assert ranked[0]["fit"]["band"] == BAND_BACKED
    assert ranked[0]["fit"]["backed"] == ["遮阳棚替换布", "风扇"]
    assert ranked[0]["fit"]["unsupported"] == []


def test_scene_the_model_invented_is_marked_as_a_stretch() -> None:
    ranked = rank_scenes({"scenes": [scene("亲子露营", ["儿童帐篷", "野餐垫"])]}, SHELF)

    assert ranked[0]["fit"]["band"] == BAND_STRETCH
    assert ranked[0]["fit"]["backed_count"] == 0
    assert ranked[0]["fit"]["unsupported"] == ["儿童帐篷", "野餐垫"]


def test_shaky_recognition_is_flagged_over_being_backed() -> None:
    clues = [clue("遮阳棚替换布", "商品卡片主图", confidence=0.3),
             clue("风扇", "商品卡片主图", confidence=0.3)]
    ranked = rank_scenes({"scenes": [scene("庭院遮阳", ["遮阳棚替换布", "风扇"])]}, clues)

    assert ranked[0]["fit"]["band"] == BAND_UNSURE


def test_a_scene_shown_only_as_scenery_does_not_count_as_backed() -> None:
    clues = [clue("遮阳棚替换布", "场景中偶然出现"), clue("风扇", "商品卡片主图")]
    ranked = rank_scenes({"scenes": [scene("庭院遮阳", ["遮阳棚替换布", "风扇"])]}, clues)

    assert ranked[0]["fit"]["band"] == BAND_STRETCH
    assert ranked[0]["fit"]["backed"] == ["风扇"]
    assert ranked[0]["fit"]["weak"] == ["遮阳棚替换布"]


def test_best_supported_scenes_come_first() -> None:
    ranked = rank_scenes({"scenes": [
        scene("亲子露营", ["儿童帐篷", "野餐垫"]),
        scene("庭院遮阳", ["遮阳棚替换布", "风扇"]),
        scene("户外烧烤", ["便携 BBQ 烤架", "遮阳棚替换布"]),
    ]}, SHELF)

    assert [item["scene_name"] for item in ranked] == ["庭院遮阳", "户外烧烤", "亲子露营"]


def test_scene_without_any_shelf_product_is_not_pretended_to_be_backed() -> None:
    ranked = rank_scenes({"scenes": [scene("空场景", [])]}, SHELF)

    assert ranked[0]["fit"]["band"] == BAND_STRETCH
    assert ranked[0]["fit"]["product_count"] == 0
