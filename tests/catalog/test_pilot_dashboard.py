import json

from scripts.serve_pilot_dashboard import build_payload, discover_stores


def test_discovers_batch_and_allows_one_available_model(tmp_path):
    for store_id in ("first", "second"):
        directory = tmp_path / "stores" / store_id
        directory.mkdir(parents=True)
        (directory / "sample_store.json").write_text(json.dumps({
            "store": {"store_name": store_id, "country": "PH"},
        }), encoding="utf-8")
        (directory / "gpt55_analysis.json").write_text(json.dumps({
            "model": "gpt-5.5",
            "scenes": [],
        }), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({
        "stores": ["second", "first"],
        "default_store_id": "second",
    }), encoding="utf-8")

    stores, default_store_id = discover_stores(tmp_path)

    assert [store["id"] for store in stores] == ["second", "first"]
    assert default_store_id == "second"
    assert list(build_payload(stores[0]["path"])["models"]) == ["gpt55"]
