import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "clues.py"

from store_scenario_inspiration.pipeline.clues import (
    build_analysis_input,
    excluded_clues,
    find_purity_violations,
    kept_clues,
    load_exclusions,
    mentions,
    product_text,
    review_clues,
    save_exclusions,
)


def clue(name: str, roles: list[str], confidence: float = 0.95,
         images: list[str] | None = None) -> dict:
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


def test_review_carries_the_per_image_counts_as_context() -> None:
    review = review_clues([clue("折叠露营椅", ["商品卡片主图", "场景中偶然出现"])], set())
    entry = review["entries"][0]

    assert entry["role"] == "商品卡片主图"
    assert entry["confidence"] == 0.95
    assert entry["image_count"] == 2
    assert entry["card_images"] == 1
    assert entry["scenery_images"] == 1
    assert entry["excluded"] is False


def test_nothing_is_excluded_until_the_operator_says_so() -> None:
    """Recognition tags nearly everything as a product card at the same
    confidence, so no threshold here separates the store's direction from an
    item that merely happened to be photographed. Rather than dress a coin flip
    up as policy, the review keeps everything and waits."""
    review = review_clues([
        clue("遮阳棚替换布、遮阳网", ["商品卡片主图"]),
        clue("Micro SD/CCTV 存储卡", ["商品卡片主图"]),
        clue("焊机、电钻等电动工具", ["商品卡片主图"]),
    ], set())

    assert kept_clues(review) == [
        "遮阳棚替换布、遮阳网", "Micro SD/CCTV 存储卡", "焊机、电钻等电动工具",
    ]
    assert excluded_clues(review) == []
    assert review["counts"] == {"kept": 3, "excluded": 0}


def test_operator_exclusion_is_the_only_thing_that_narrows_the_direction() -> None:
    store = sample([
        clue("遮阳棚替换布、遮阳网", ["商品卡片主图", "商品卡片主图"]),
        clue("Micro SD/CCTV 存储卡", ["商品卡片主图"]),
        clue("焊机、电钻等电动工具", ["商品卡片主图", "商品卡片主图"]),
    ])

    review = review_clues(store["observed_product_clues"], {"Micro SD/CCTV 存储卡"})
    payload = build_analysis_input(store, review)

    assert kept_clues(review) == ["遮阳棚替换布、遮阳网", "焊机、电钻等电动工具"]
    assert excluded_clues(review) == ["Micro SD/CCTV 存储卡"]
    assert [item["clue"] for item in payload["observed_product_clues"]] == [
        "遮阳棚替换布、遮阳网", "焊机、电钻等电动工具",
    ]


def test_analysis_input_keeps_the_evidence_and_drops_the_rest() -> None:
    store = sample([clue("折叠露营椅", ["商品卡片主图"])])

    payload = build_analysis_input(store, review_clues(store["observed_product_clues"], set()),
                                   direction="户外庭院")

    assert payload["schema"] == "store-analysis-input-v1"
    assert payload["store_direction"] == "户外庭院"
    assert payload["observed_product_clues"] == [
        {"clue": "折叠露营椅", "role": "商品卡片主图", "confidence": 0.95, "evidence": "依据"}
    ]
    assert "不得出现在任何场景" in payload["direction_note"]
    assert payload["limitations"] == ["商品线索仅来自本次截图中可见内容。"]


def test_excluded_clue_reaches_the_model_as_a_named_ban() -> None:
    store = sample([
        clue("遮阳棚替换布、遮阳网", ["商品卡片主图"]),
        clue("Micro SD/CCTV 存储卡", ["商品卡片主图"]),
    ])

    payload = build_analysis_input(
        store, review_clues(store["observed_product_clues"], {"Micro SD/CCTV 存储卡"}))

    assert [item["clue"] for item in payload["excluded_product_clues"]] == ["Micro SD/CCTV 存储卡"]
    assert payload["excluded_product_clues"][0]["reason"] == "运营手动排除"


def test_exclusions_round_trip_through_disk(tmp_path) -> None:
    path = tmp_path / "exclusions.json"

    assert load_exclusions(path) == set()

    save_exclusions(path, ["Micro SD/CCTV 存储卡", "Micro SD/CCTV 存储卡"])
    assert load_exclusions(path) == {"Micro SD/CCTV 存储卡"}
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema": "store-exclusions-v1", "excluded": ["Micro SD/CCTV 存储卡"],
    }


def test_invalid_exclusions_file_is_rejected(tmp_path) -> None:
    path = tmp_path / "exclusions.json"
    path.write_text(json.dumps({"schema": "wrong", "excluded": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid exclusions"):
        load_exclusions(path)

    path.write_text(json.dumps({"schema": "store-exclusions-v1"}), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid exclusions"):
        load_exclusions(path)


def test_latin_clues_only_match_on_a_whole_word_run() -> None:
    """CCTV has to reappear as a run, not as a coincidence of letters."""
    assert mentions("CCTV 存储卡", "CCTV")
    assert not mentions("监控摄像头", "CCTV")


def test_chinese_clues_match_when_compounded() -> None:
    """Chinese names get compounded rather than repeated verbatim."""
    assert mentions("监控存储卡", "存储卡")
    assert mentions("可折叠户外露营椅", "露营椅")
    assert not mentions("遮阳伞", "存储卡")


def test_product_text_reads_both_languages() -> None:
    assert product_text({"product_cn": "遮阳伞", "product_en": "Sun Shade"}) == "遮阳伞 Sun Shade"


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


def test_purity_check_stays_quiet_when_only_kept_products_appear() -> None:
    scene = analysis({"庭院遮阳": ["遮阳伞", "折叠露营椅"], "户外烧烤": ["便携 BBQ 烤架"]})

    assert find_purity_violations(scene, ["Micro SD/CCTV 存储卡"]) == []


def test_cli_writes_both_artifacts_and_honours_the_exclusion_file(tmp_path) -> None:
    store_dir = tmp_path / "row017-shopee-13021ph"
    store_dir.mkdir()
    (store_dir / "sample_store.json").write_text(json.dumps(sample([
        clue("遮阳棚替换布、遮阳网", ["商品卡片主图", "商品卡片主图"]),
        clue("Micro SD/CCTV 存储卡", ["商品卡片主图"]),
        clue("焊机、电钻等电动工具", ["商品卡片主图"]),
    ]), ensure_ascii=False), encoding="utf-8")
    save_exclusions(store_dir / "exclusions.json", ["Micro SD/CCTV 存储卡", "焊机、电钻等电动工具"])

    run = subprocess.run(
        [sys.executable, str(SCRIPT), str(store_dir), "--direction", "户外庭院"],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )
    report = json.loads(run.stdout)
    review = json.loads((store_dir / "clues.json").read_text(encoding="utf-8"))
    payload = json.loads((store_dir / "analysis_input.json").read_text(encoding="utf-8"))

    assert report["counts"] == {"kept": 1, "excluded": 2}
    assert review["schema"] == "store-clues-v1"
    assert [item["clue"] for item in payload["observed_product_clues"]] == ["遮阳棚替换布、遮阳网"]
    assert [item["clue"] for item in payload["excluded_product_clues"]] == [
        "Micro SD/CCTV 存储卡", "焊机、电钻等电动工具",
    ]


def test_cli_without_an_exclusion_file_keeps_everything(tmp_path) -> None:
    store_dir = tmp_path / "row017-shopee-13021ph"
    store_dir.mkdir()
    (store_dir / "sample_store.json").write_text(json.dumps(sample([
        clue("遮阳棚替换布、遮阳网", ["商品卡片主图", "商品卡片主图"]),
        clue("Micro SD/CCTV 存储卡", ["商品卡片主图"]),
    ]), ensure_ascii=False), encoding="utf-8")

    subprocess.run([sys.executable, str(SCRIPT), str(store_dir)],
                   check=True, capture_output=True, text=True, encoding="utf-8")
    payload = json.loads((store_dir / "analysis_input.json").read_text(encoding="utf-8"))

    assert len(payload["observed_product_clues"]) == 2
    assert payload["excluded_product_clues"] == []
    assert payload["store_direction"] is None
