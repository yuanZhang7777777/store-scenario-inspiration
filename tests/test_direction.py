import json
from pathlib import Path

import pytest


from direction import (
    BUCKET_EXCLUDED,
    BUCKET_MAIN,
    BUCKET_UNSURE,
    apply_overrides,
    bucket_clue,
    bucket_clues,
    load_overrides,
    save_overrides,
    selected_clues,
)


def clue(name: str, roles: list[str], confidence: float = 0.9, images: list[str] | None = None) -> dict:
    filenames = images or [f"img{index}.png" for index in range(len(roles))]
    return {
        "clue": name,
        "confidence": confidence,
        "source_image": filenames,
        "occurrences": [
            {"image": filename, "role": role, "confidence": confidence, "evidence": "依据"}
            for filename, role in zip(filenames, roles, strict=True)
        ],
    }


def test_card_image_goes_to_main() -> None:
    entry = bucket_clue(clue("折叠露营椅", ["商品卡片主图", "商品卡片主图"]))

    assert entry["bucket"] == BUCKET_MAIN
    assert entry["card_images"] == 2
    assert "商品主图" in entry["reason"]


def test_scenery_only_clue_is_excluded() -> None:
    entry = bucket_clue(clue("Micro SD/CCTV 存储卡", ["场景中偶然出现"]))

    assert entry["bucket"] == BUCKET_EXCLUDED
    assert entry["card_images"] == 0
    assert entry["scenery_images"] == 1
    assert "从未作为商品主图展示" in entry["reason"]


def test_low_confidence_card_is_unsure_not_main() -> None:
    entry = bucket_clue(clue("折叠露营椅", ["商品卡片主图"], confidence=0.4))

    assert entry["bucket"] == BUCKET_UNSURE
    assert "需要人工确认" in entry["reason"]


def test_unjudgeable_clue_is_unsure() -> None:
    entry = bucket_clue(clue("某物", ["不确定"]))

    assert entry["bucket"] == BUCKET_UNSURE
    assert "识别把握不足" in entry["reason"]


def test_one_card_image_is_enough_to_reach_main() -> None:
    """A product the store presents as a listing card is in the catalogue,
    even if the other screenshot happens to show it as scenery."""
    entry = bucket_clue(clue("折叠露营椅", ["商品卡片主图", "场景中偶然出现"]))

    assert entry["bucket"] == BUCKET_MAIN
    assert entry["card_images"] == 1
    assert entry["scenery_images"] == 1


def test_selected_clues_defaults_to_main_only() -> None:
    review = bucket_clues([
        clue("折叠露营椅", ["商品卡片主图"]),
        clue("Micro SD/CCTV 存储卡", ["场景中偶然出现"]),
        clue("某物", ["不确定"]),
    ])

    assert selected_clues(review) == ["折叠露营椅"]
    assert selected_clues(review, (BUCKET_MAIN, BUCKET_UNSURE)) == ["折叠露营椅", "某物"]
    assert review["counts"] == {BUCKET_MAIN: 1, BUCKET_UNSURE: 1, BUCKET_EXCLUDED: 1}


def test_operator_override_beats_the_deterministic_bucket() -> None:
    review = bucket_clues([
        clue("折叠露营椅", ["商品卡片主图"]),
        clue("Micro SD/CCTV 存储卡", ["场景中偶然出现"]),
    ])

    updated = apply_overrides(review, {"Micro SD/CCTV 存储卡": BUCKET_MAIN})

    assert selected_clues(updated) == ["折叠露营椅", "Micro SD/CCTV 存储卡"]
    promoted = next(e for e in updated["entries"] if e["clue"] == "Micro SD/CCTV 存储卡")
    assert promoted["reason"] == "运营手动指定"
    assert updated["counts"][BUCKET_EXCLUDED] == 0


def test_apply_overrides_with_empty_mapping_is_a_noop() -> None:
    review = bucket_clues([clue("折叠露营椅", ["商品卡片主图"])])

    assert apply_overrides(review, {}) is review


def test_overrides_reject_unknown_clue_and_unknown_bucket() -> None:
    review = bucket_clues([clue("折叠露营椅", ["商品卡片主图"])])

    with pytest.raises(ValueError, match="unknown clue"):
        apply_overrides(review, {"不存在": BUCKET_MAIN})
    with pytest.raises(ValueError, match="invalid bucket"):
        apply_overrides(review, {"折叠露营椅": "垃圾"})

    # A rejected override must not leave the review half-mutated.
    assert review["entries"][0]["bucket"] == BUCKET_MAIN
    assert review["entries"][0]["reason"] != "运营手动指定"


def test_role_tag_health_flags_unreliable_recognition() -> None:
    solid = bucket_clues([clue("折叠露营椅", ["商品卡片主图"], confidence=0.95)])
    shaky = bucket_clues([
        clue("折叠露营椅", ["商品卡片主图"], confidence=0.3),
        clue("遮阳伞", ["商品卡片主图"], confidence=0.4),
    ])

    assert solid["role_tag_health"] == {
        "low_confidence_count": 0, "total": 1, "share": 0.0, "needs_review": False,
    }
    assert shaky["role_tag_health"]["share"] == 1.0
    assert shaky["role_tag_health"]["needs_review"] is True


def test_overrides_round_trip_through_disk(tmp_path) -> None:
    path = tmp_path / "direction_overrides.json"

    assert load_overrides(path) == {}

    save_overrides(path, {"Micro SD/CCTV 存储卡": BUCKET_EXCLUDED})
    assert load_overrides(path) == {"Micro SD/CCTV 存储卡": BUCKET_EXCLUDED}
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == "direction-overrides-v1"


def test_invalid_override_file_is_rejected(tmp_path) -> None:
    path = tmp_path / "direction_overrides.json"
    path.write_text(json.dumps({"schema": "wrong", "overrides": {}}), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid direction overrides"):
        load_overrides(path)


def test_real_store_clues_split_the_outdoor_shop_from_its_incidentals() -> None:
    """Shape taken from Shopee-13021PH, the store that motivated the gate:
    an outdoor listing that also carried an SD card and a merged blob."""
    review = bucket_clues([
        clue("遮阳棚替换布、遮阳网", ["商品卡片主图", "商品卡片主图"]),
        clue("便携 BBQ 烤架/不锈钢折叠烤炉桌", ["商品卡片主图", "商品卡片主图"]),
        clue("Micro SD/CCTV 存储卡", ["场景中偶然出现"]),
        clue("焊机、电钻等电动工具", ["不确定"], confidence=0.5),
    ])

    assert selected_clues(review) == ["遮阳棚替换布、遮阳网", "便携 BBQ 烤架/不锈钢折叠烤炉桌"]
    assert review["counts"][BUCKET_EXCLUDED] == 1
    assert review["counts"][BUCKET_UNSURE] == 1
