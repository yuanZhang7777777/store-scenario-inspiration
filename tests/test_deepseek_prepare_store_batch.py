import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "deepseek_prepare_store_batch.py"
sys.path.insert(0, str(ROOT / "scripts"))

from deepseek_prepare_store_batch import build_sample, recognize, store_id, validate_result


def test_recognition_result_becomes_product_clues(tmp_path) -> None:
    image = tmp_path / "one.png"
    image.write_bytes(b"not read by this test")
    entry = {
        "source_row": 7, "store_name": "Shopee-20005PH", "country": "PH",
        "images": [{"filename": "one.png", "local_path": str(image)}],
    }
    result = validate_result(
        {"images": [{"filename": "one.png", "products": ["折叠椅", "折叠椅", "遮阳伞"]}]},
        ["one.png"],
    )
    sample = build_sample(entry, result, tmp_path / "input" / "stores.json")

    assert store_id(entry) == "row007-shopee-20005ph"
    assert [item["clue"] for item in sample["observed_product_clues"]] == ["折叠椅", "遮阳伞"]


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
        "images": [{"filename": "one.png", "products": ["折叠椅"]}],
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
        "choices": [{"message": {"content": json.dumps({
            "images": [{"filename": "one.png", "products": ["折叠椅"]}]
        }, ensure_ascii=False)}}],
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

    assert result["images"][0]["products"] == ["折叠椅"]
    assert receipt["usage"]["total_tokens"] == 15
    assert receipt["response_id"] == "chat-123"
    assert "secret-key" not in serialized
    assert "Authorization" not in serialized
    assert "base64" not in serialized
