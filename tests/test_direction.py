import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "direction.py"
sys.path.insert(0, str(SCRIPT.parent))

from direction import (
    BUCKET_EXCLUDED,
    BUCKET_MAIN,
    BUCKET_UNSURE,
    apply_overrides,
    bucket_clue,
    bucket_clues,
    build_analysis_input,
    find_purity_violations,
    load_overrides,
    save_overrides,
    selected_clues,
)


def clue(name: str, roles: list[str], confidence: float = 0.9, images: list[str] | None = None) -> dict:
    filenames = images or [f"img{index}.png" for index in range(len(roles))]
    occurrences = [
        {"image": filename, "role": role, "confidence": confidence, "evidence": "依据"}
        for filename, role in zip(filenames, roles, strict=True)
    ]
    return {
        "clue": name,
        "role": max(roles, key=lambda role: {"商品卡片主图": 2, "不确定": 1, "场景中偶然出现": 0}[role]),
        "confidence": confidence,
        "evidence": "依据",
        "source_image": filenames,
        "occurrences": occurrences,
    }


def sample(clues: list[dict]) -> dict:
    return {"store": {"country": "PH", "store_name": "Shopee-13021PH"},
            "observed_product_clues": clues,
            "limitations": ["商品线索仅来自本次截图中可见内容。"]}


def analysis(scenes: dict[str, list[str]]) -> dict:
    return {"scenes": [
        {"scene_name": name, "product_needs": [
            {"product_cn": product, "product_en": "", "purpose": "用途"} for product in products
        ]}
        for name, products in scenes.items()
    ]}


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


def test_analysis_input_keeps_only_the_confirmed_direction() -> None:
    store = sample([
        clue("遮阳棚替换布、遮阳网", ["商品卡片主图", "商品卡片主图"]),
        clue("Micro SD/CCTV 存储卡", ["场景中偶然出现"]),
    ])

    payload = build_analysis_input(store, bucket_clues(store["observed_product_clues"]),
                                   direction="户外庭院")

    assert [item["clue"] for item in payload["observed_product_clues"]] == ["遮阳棚替换布、遮阳网"]
    assert payload["observed_product_clues"][0]["evidence"] == "依据"
    assert payload["store_direction"] == "户外庭院"
    assert payload["direction_confirmed"] is True
    assert "不得出现在任何场景" in payload["direction_note"]
    assert payload["schema"] == "store-analysis-input-v1"


def test_unconfirmed_direction_passes_everything_through_with_a_warning() -> None:
    """Confirming a direction is optional. A store nobody has sorted yet — or a
    general-merchandise grid that cannot be sorted from pixels — still has to
    produce scenes for an operator to judge, so the default path keeps every
    clue and says out loud that none of it is confirmed."""
    store = sample([clue("遮阳棚替换布、遮阳网", ["不确定"], confidence=0.4)])
    review = bucket_clues(store["observed_product_clues"])
    payload = build_analysis_input(store, review)

    assert review["counts"][BUCKET_MAIN] == 0
    assert payload["direction_confirmed"] is False
    assert [item["clue"] for item in payload["observed_product_clues"]] == ["遮阳棚替换布、遮阳网"]
    assert payload["excluded_product_clues"] == []
    assert "不代表本店主营" in payload["direction_note"]


def test_excluded_clue_reaches_the_model_with_its_reason() -> None:
    store = sample([
        clue("遮阳棚替换布、遮阳网", ["商品卡片主图"]),
        clue("Micro SD/CCTV 存储卡", ["场景中偶然出现"]),
    ])

    payload = build_analysis_input(store, bucket_clues(store["observed_product_clues"]))

    assert payload["store_direction"] is None
    assert [item["clue"] for item in payload["excluded_product_clues"]] == ["Micro SD/CCTV 存储卡"]
    assert "从未作为商品主图展示" in payload["excluded_product_clues"][0]["reason"]
    assert payload["limitations"] == ["商品线索仅来自本次截图中可见内容。"]


def test_operator_promotion_moves_a_clue_into_the_direction() -> None:
    store = sample([
        clue("遮阳棚替换布、遮阳网", ["商品卡片主图"]),
        clue("Micro SD/CCTV 存储卡", ["场景中偶然出现"]),
    ])
    review = apply_overrides(
        bucket_clues(store["observed_product_clues"]), {"Micro SD/CCTV 存储卡": BUCKET_MAIN}
    )

    payload = build_analysis_input(store, review)

    assert [item["clue"] for item in payload["observed_product_clues"]] == [
        "遮阳棚替换布、遮阳网", "Micro SD/CCTV 存储卡",
    ]
    assert payload["excluded_product_clues"] == []


def test_purity_check_flags_a_scene_that_brought_back_an_excluded_product() -> None:
    scene = analysis({
        "家庭安防与存储扩展": ["监控存储卡", "遮阳伞"],
        "庭院遮阳": ["遮阳伞"],
    })

    violations = find_purity_violations(scene, ["Micro SD/CCTV 存储卡"])

    assert violations == [{
        "scene_name": "家庭安防与存储扩展",
        "product_cn": "监控存储卡",
        "excluded_clue": "Micro SD/CCTV 存储卡",
    }]


def test_purity_check_stays_quiet_when_only_direction_products_appear() -> None:
    scene = analysis({"庭院遮阳": ["遮阳伞", "折叠露营椅"], "户外烧烤": ["便携 BBQ 烤架"]})

    assert find_purity_violations(scene, ["Micro SD/CCTV 存储卡"]) == []


def test_direction_cli_writes_both_artifacts(tmp_path) -> None:
    store_dir = tmp_path / "row017-shopee-13021ph"
    store_dir.mkdir()
    (store_dir / "sample_store.json").write_text(json.dumps(sample([
        clue("遮阳棚替换布、遮阳网", ["商品卡片主图", "商品卡片主图"]),
        clue("Micro SD/CCTV 存储卡", ["场景中偶然出现"]),
        clue("焊机、电钻等电动工具", ["不确定"], confidence=0.5),
    ]), ensure_ascii=False), encoding="utf-8")

    run = subprocess.run(
        [sys.executable, str(SCRIPT), str(store_dir), "--direction", "户外庭院"],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )
    report = json.loads(run.stdout)
    direction = json.loads((store_dir / "direction.json").read_text(encoding="utf-8"))
    payload = json.loads((store_dir / "analysis_input.json").read_text(encoding="utf-8"))

    assert report["counts"] == {BUCKET_MAIN: 1, BUCKET_UNSURE: 1, BUCKET_EXCLUDED: 1}
    assert direction["schema"] == "store-direction-v1"
    assert [item["clue"] for item in payload["observed_product_clues"]] == ["遮阳棚替换布、遮阳网"]
    assert [item["clue"] for item in payload["excluded_product_clues"]] == ["Micro SD/CCTV 存储卡"]


def test_direction_cli_applies_an_operator_override_file(tmp_path) -> None:
    store_dir = tmp_path / "row017-shopee-13021ph"
    store_dir.mkdir()
    (store_dir / "sample_store.json").write_text(json.dumps(sample([
        clue("遮阳棚替换布、遮阳网", ["商品卡片主图"]),
        clue("Micro SD/CCTV 存储卡", ["场景中偶然出现"]),
    ]), ensure_ascii=False), encoding="utf-8")
    save_overrides(store_dir / "direction_overrides.json", {"Micro SD/CCTV 存储卡": BUCKET_MAIN})

    subprocess.run([sys.executable, str(SCRIPT), str(store_dir)],
                   check=True, capture_output=True, text=True, encoding="utf-8")
    payload = json.loads((store_dir / "analysis_input.json").read_text(encoding="utf-8"))

    assert [item["clue"] for item in payload["observed_product_clues"]] == [
        "遮阳棚替换布、遮阳网", "Micro SD/CCTV 存储卡",
    ]
    assert payload["excluded_product_clues"] == []


def test_real_store_needs_the_operator_because_the_role_tags_stay_neutral() -> None:
    """Shape taken from Shopee-13021PH, the store that motivated the gate.

    Both of its screenshots are product grids, so its SD card and welding tools
    sit on listing cards exactly like the sunshade does. The role tags cannot
    tell that this shop's direction is outdoor — nothing about the pixels says
    so. Only the operator can, which is why the override file exists and why the
    gate must never pretend the tags settled the question.
    """
    store = sample([
        clue("遮阳棚替换布、遮阳网", ["商品卡片主图", "商品卡片主图"]),
        clue("Micro SD/CCTV 存储卡", ["商品卡片主图"]),
        clue("焊机、电钻等电动工具", ["商品卡片主图", "商品卡片主图"]),
    ])

    review = bucket_clues(store["observed_product_clues"])

    assert review["counts"][BUCKET_MAIN] == 3
    assert selected_clues(review) == [
        "遮阳棚替换布、遮阳网", "Micro SD/CCTV 存储卡", "焊机、电钻等电动工具",
    ]

    review = apply_overrides(review, {
        "Micro SD/CCTV 存储卡": BUCKET_EXCLUDED,
        "焊机、电钻等电动工具": BUCKET_EXCLUDED,
    })

    assert [item["clue"] for item in
            build_analysis_input(store, review)["observed_product_clues"]] == ["遮阳棚替换布、遮阳网"]
