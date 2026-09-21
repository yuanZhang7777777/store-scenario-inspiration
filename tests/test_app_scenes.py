import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from store_scenario_inspiration.app.scenes import annotate


def scene(name: str, products: list[str]) -> dict:
    return {"scene_name": name, "audience": "住户", "user_need": "需要",
            "evidence": "证据", "product_needs": [
                {"product_cn": product, "product_en": "", "purpose": "用途"}
                for product in products
            ]}


def test_a_scene_nobody_excluded_gets_an_empty_marker() -> None:
    scenes = annotate({"scenes": [scene("庭院遮阳", ["遮阳棚替换布", "风扇"])]}, [])

    assert scenes[0]["excluded"] == []
    assert len(scenes[0]["product_needs"]) == 2


def test_the_operators_exclusion_is_attached_to_the_scene_that_carries_it() -> None:
    """A product missing from the screenshots says nothing about whether we can
    offer it. The only product-level signal worth carrying into the report is
    the operator's own exclusion, because a scene that quietly re-adds a
    ruled-out product is ignoring an instruction rather than observing."""
    scenes = annotate(
        {"scenes": [scene("庭院遮阳", ["遮阳棚替换布", "风扇"])]},
        ["风扇"],
    )

    assert scenes[0]["excluded"] == ["风扇"]


def test_a_compounded_name_still_matches_the_excluded_clue() -> None:
    scenes = annotate({"scenes": [scene("家庭安防", ["监控存储卡"])]}, ["Micro SD/CCTV 存储卡"])

    assert scenes[0]["excluded"] == ["监控存储卡"]


def test_scenes_the_model_left_out_are_untouched() -> None:
    assert annotate({}, ["风扇"]) == []
    assert annotate({"scenes": None}, ["风扇"]) == []


def test_annotation_keeps_every_other_scene_field() -> None:
    scenes = annotate({"scenes": [scene("庭院遮阳", ["风扇"])]}, ["风扇"])

    assert scenes[0]["scene_name"] == "庭院遮阳"
    assert scenes[0]["user_need"] == "需要"
    assert scenes[0]["product_needs"][0]["purpose"] == "用途"
