import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "deepseek_prepare_store_batch.py"
sys.path.insert(0, str(ROOT / "scripts"))

from deepseek_prepare_store_batch import (
    build_sample,
    recognize,
    split_clue_names,
    store_id,
    validate_result,
)


def product(name: str, role: str, confidence: float) -> dict:
    return {"name": name, "role": role, "confidence": confidence, "evidence": f"{name} 的判断依据"}


def one_image(products: list[dict], filename: str = "one.png") -> dict:
    return {"images": [{"filename": filename, "products": products}]}


def test_recognition_result_becomes_product_clues(tmp_path) -> None:
    image = tmp_path / "one.png"
    image.write_bytes(b"not read by this test")
    entry = {
        "source_row": 7, "store_name": "Shopee-20005PH", "country": "PH",
        "images": [{"filename": "one.png", "local_path": str(image)}],
    }
    result = validate_result(
        one_image([
            product("折叠椅", "商品卡片主图", 0.9),
            product("折叠椅", "商品卡片主图", 0.9),
            product("遮阳伞", "场景中偶然出现", 0.5),
        ]),
        ["one.png"],
    )
    sample = build_sample(entry, result, tmp_path / "input" / "stores.json")

    assert store_id(entry) == "row007-shopee-20005ph"
    clues = sample["observed_product_clues"]
    assert [item["clue"] for item in clues] == ["折叠椅", "遮阳伞"]
    assert [item["role"] for item in clues] == ["商品卡片主图", "场景中偶然出现"]
    assert [item["confidence"] for item in clues] == [0.9, 0.5]


def test_merged_clue_is_split_into_atomic_products(tmp_path) -> None:
    image = tmp_path / "one.png"
    image.write_bytes(b"not read by this test")
    entry = {
        "source_row": 7, "store_name": "Shopee-20005PH", "country": "PH",
        "images": [{"filename": "one.png", "local_path": str(image)}],
    }
    merged = "鱼箱、麦克风、马克杯礼盒"
    result = validate_result(one_image([product(merged, "商品卡片主图", 0.8)]), ["one.png"])

    clues = build_sample(entry, result, tmp_path / "stores.json")["observed_product_clues"]

    assert [item["clue"] for item in clues] == ["鱼箱", "麦克风", "马克杯礼盒"]
    assert all(item["merged_from"] == [merged] for item in clues)
    assert all(item["role"] == "商品卡片主图" for item in clues)


def test_slash_aliases_are_kept_as_one_product() -> None:
    assert split_clue_names("Micro SD/CCTV 存储卡") == ("Micro SD/CCTV 存储卡",)
    assert split_clue_names("折叠露营椅") == ("折叠露营椅",)


def test_strongest_role_wins_across_images(tmp_path) -> None:
    images = []
    for filename in ("one.png", "two.png"):
        path = tmp_path / filename
        path.write_bytes(b"not read by this test")
        images.append({"filename": filename, "local_path": str(path)})
    entry = {"source_row": 7, "store_name": "Shopee-20005PH", "country": "PH", "images": images}
    result = validate_result(
        {"images": [
            {"filename": "one.png", "products": [product("折叠椅", "场景中偶然出现", 0.4)]},
            {"filename": "two.png", "products": [product("折叠椅", "商品卡片主图", 0.6)]},
        ]},
        ["one.png", "two.png"],
    )

    clue = build_sample(entry, result, tmp_path / "stores.json")["observed_product_clues"][0]

    assert clue["role"] == "商品卡片主图"
    assert clue["confidence"] == 0.6
    assert clue["source_image"] == ["one.png", "two.png"]
    assert [item["role"] for item in clue["occurrences"]] == ["场景中偶然出现", "商品卡片主图"]


def test_unknown_role_is_rejected() -> None:
    with pytest.raises(ValueError, match="invalid product role"):
        validate_result(one_image([product("折叠椅", "背景道具", 0.5)]), ["one.png"])


def test_out_of_range_confidence_is_rejected() -> None:
    with pytest.raises(ValueError, match="confidence"):
        validate_result(one_image([product("折叠椅", "商品卡片主图", 1.5)]), ["one.png"])


def test_cli_resumes_existing_and_filters_unsupported_country(tmp_path) -> None:
    stores = [
        {"source_row": 3, "store_name": "Shopee-1TH", "country": "TH",
         "images": [{"filename": "one.png", "local_path": str(tmp_path / "one.png")}]},
        {"source_row": 4, "store_name": "Shopee-2BR", "country": "BR",
         "images": [{"filename": "two.png", "local_path": str(tmp_path / "two.png")}]},
    ]
    (tmp_path / "input").mkdir()
    (tmp_path / "input" / "stores.json").write_text(
        json.dumps(stores, ensure_ascii=False), encoding="utf-8"
    )
    sample = tmp_path / "stores" / "row003-shopee-1th" / "sample_store.json"
    sample.parent.mkdir(parents=True)
    sample.write_text(json.dumps({
        "store": {"country": "TH"}, "observed_product_clues": []
    }), encoding="utf-8")
    sample.with_name("deepseek_vision.json").write_text(json.dumps({
        "input_filenames": ["one.png"],
        "images": [{"filename": "one.png", "products": [product("折叠椅", "商品卡片主图", 0.9)]}],
        "response_id": "response-id", "response_model": "deepseek-flash", "usage": {},
    }, ensure_ascii=False), encoding="utf-8")

    run = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path)], check=True,
        capture_output=True, text=True, encoding="utf-8",
    )
    report = json.loads(run.stdout)
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))

    assert report == {"root": str(tmp_path), "stores": 1, "recognized": 0,
                      "resumed": 1, "skipped_country": 1}
    assert manifest["models"] == ["deepseek"]
    assert manifest["stores"] == [{"id": "row003-shopee-1th", "country": "TH"}]


def test_recognition_receipt_keeps_usage_without_request_secrets(tmp_path, monkeypatch) -> None:
    image = tmp_path / "one.png"
    image.write_bytes(b"image bytes")
    entry = {
        "source_row": 7, "store_name": "Shopee-20005PH", "country": "PH",
        "images": [{"filename": "one.png", "local_path": str(image)}],
    }
    body = {
        "id": "chat-123", "model": "deepseek-flash",
        "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
        "choices": [{"message": {"content": json.dumps(
            one_image([product("折叠椅", "商品卡片主图", 0.9)]), ensure_ascii=False
        )}}],
    }

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps(body, ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: Response())
    result, receipt = recognize(entry, "secret-key")
    serialized = json.dumps(receipt, ensure_ascii=False)

    assert result["images"][0]["products"][0]["name"] == "折叠椅"
    assert result["images"][0]["products"][0]["role"] == "商品卡片主图"
    assert receipt["usage"]["total_tokens"] == 15
    assert receipt["response_id"] == "chat-123"
    assert "secret-key" not in serialized
    assert "Authorization" not in serialized
    assert "base64" not in serialized
