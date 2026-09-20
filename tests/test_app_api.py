import json
from pathlib import Path
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from store_scenario_inspiration.app import jobs as jobs_module
from store_scenario_inspiration.app.config import Settings
from store_scenario_inspiration.app.main import create_app


PRODUCTS = [
    {"name": "遮阳棚替换布", "role": "商品卡片主图", "confidence": 0.9, "evidence": "主图"},
    {"name": "风扇", "role": "商品卡片主图", "confidence": 0.9, "evidence": "主图"},
]
SCENES = [
    {"scene_name": "庭院遮阳", "audience": "住户", "user_need": "遮阳", "evidence": "证据",
     "product_needs": [{"product_cn": "遮阳棚替换布", "product_en": "Sunshade", "purpose": "遮阳"},
                       {"product_cn": "风扇", "product_en": "Fan", "purpose": "通风"}]},
    {"scene_name": "亲子露营", "audience": "家庭", "user_need": "露营", "evidence": "证据",
     "product_needs": [{"product_cn": "儿童帐篷", "product_en": "Kids tent", "purpose": "露营"},
                       {"product_cn": "野餐垫", "product_en": "Picnic mat", "purpose": "铺垫"}]},
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    def fake_recognize(entry, key):
        assert key == "test-key"
        return ({"images": [{"filename": image["filename"], "products": PRODUCTS}
                            for image in entry["images"]]},
                {"input_filenames": [image["filename"] for image in entry["images"]],
                 "images": [{"filename": image["filename"], "products": PRODUCTS}
                            for image in entry["images"]],
                 "response_id": "resp-1", "response_model": "deepseek-flash",
                 "usage": {"total_tokens": 42}})

    def fake_analyze(source, key, *, expand=False):
        assert key == "test-key"
        assert source["direction_confirmed"] is True
        assert [item["clue"] for item in source["observed_product_clues"]] == ["遮阳棚替换布", "风扇"]
        return ({"model": "deepseek-flash", "scenes": SCENES, "manager_summary": {"x": 1},
                 "audiences": [], "store_profile": {}, "current_product_structure": {},
                 "future_product_structure": {}, "operation_strategy": []},
                {"response_id": "resp-2", "response_model": "deepseek-flash",
                 "usage": {"total_tokens": 99}})

    monkeypatch.setattr(jobs_module, "recognize", fake_recognize)
    monkeypatch.setattr(jobs_module, "analyze", fake_analyze)
    settings = Settings(data_dir=tmp_path, deepseek_api_key="test-key")
    app = create_app(settings)
    with TestClient(app) as client:
        yield client
    app.state.jobs.shutdown()


def upload(client, store_name="Shopee-13021PH", country="PH", count=2):
    files = [("files", (f"row_017_image_{index}.png", b"png-bytes", "image/png"))
             for index in range(1, count + 1)]
    return client.post("/api/stores", data={"store_name": store_name, "country": country},
                       files=files)


def run_job(client, store_id: str) -> dict:
    """Wait for the background job, which runs off the request thread."""
    job_id = client.post(f"/api/stores/{store_id}/jobs").json()["id"]
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"ready", "failed", "cancelled"}:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not settle: {job}")


def test_upload_keeps_every_screenshot_and_writes_a_store_dir(client, tmp_path) -> None:
    created = upload(client).json()

    assert created["id"] == "shopee-13021ph"
    assert created["country"] == "PH"
    images = Path(created["images"][0]["local_path"]).parent
    assert sorted(item.name for item in images.iterdir()) == [
        "row_017_image_1.png", "row_017_image_2.png",
    ]
    assert client.get("/api/stores").json()["stores"][0]["id"] == "shopee-13021ph"


def test_upload_rejects_a_country_the_inventory_does_not_cover(client) -> None:
    response = upload(client, country="BR")

    assert response.status_code == 400
    assert "unsupported country" in response.json()["detail"]


def test_duplicate_upload_names_stay_apart(client) -> None:
    created = client.post("/api/stores", data={"store_name": "Dup", "country": "PH"},
                          files=[("files", ("a.png", b"one", "image/png")),
                                 ("files", ("a.png", b"two", "image/png"))]).json()

    names = sorted(Path(item["local_path"]).name for item in created["images"])
    assert names == ["a-2.png", "a.png"]


def test_running_the_pipeline_produces_ranked_scenes(client) -> None:
    store_id = upload(client).json()["id"]

    job = run_job(client, store_id)

    assert job["status"] == "ready"
    assert [stage["status"] for stage in job["stages"]] == ["ready", "ready", "ready"]
    assert job["usage"]["total_tokens"] == 99

    scenes = client.get(f"/api/stores/{store_id}/scenes").json()
    assert [item["scene_name"] for item in scenes["scenes"]] == ["庭院遮阳", "亲子露营"]
    assert scenes["scenes"][0]["fit"]["band"] == "贴合"
    assert scenes["scenes"][1]["fit"]["band"] == "发散"
    assert scenes["scenes"][1]["fit"]["unsupported"] == ["儿童帐篷", "野餐垫"]


def test_operator_can_rule_a_product_out_and_the_next_run_avoids_it(client) -> None:
    store_id = upload(client).json()["id"]
    run_job(client, store_id)

    before = client.get(f"/api/stores/{store_id}/direction").json()
    assert before["counts"]["main"] == 2

    updated = client.put(f"/api/stores/{store_id}/direction",
                         json={"overrides": [{"clue": "风扇", "bucket": "excluded"}]}).json()

    assert updated["counts"] == {"main": 1, "unsure": 0, "excluded": 1}
    assert updated["entries"][1]["reason"] == "运营手动指定"
    assert client.get(f"/api/stores/{store_id}").json()["kept_clues"] == ["遮阳棚替换布"]


def test_direction_edit_survives_a_restart(client, tmp_path) -> None:
    store_id = upload(client).json()["id"]
    run_job(client, store_id)
    client.put(f"/api/stores/{store_id}/direction",
               json={"overrides": [{"clue": "风扇", "bucket": "excluded"}]})

    stored = json.loads((tmp_path / "stores" / store_id / "direction_overrides.json")
                        .read_text(encoding="utf-8"))

    assert stored == {"schema": "direction-overrides-v1", "overrides": {"风扇": "excluded"}}


def test_setting_an_override_back_clears_the_earlier_one(client, tmp_path) -> None:
    store_id = upload(client).json()["id"]
    run_job(client, store_id)
    client.put(f"/api/stores/{store_id}/direction",
               json={"overrides": [{"clue": "风扇", "bucket": "excluded"}]})

    client.put(f"/api/stores/{store_id}/direction", json={"overrides": []})

    assert client.get(f"/api/stores/{store_id}/direction").json()["counts"]["excluded"] == 0


def test_override_for_an_unknown_clue_is_rejected(client) -> None:
    store_id = upload(client).json()["id"]
    run_job(client, store_id)

    response = client.put(f"/api/stores/{store_id}/direction",
                          json={"overrides": [{"clue": "不存在的东西", "bucket": "main"}]})

    assert response.status_code == 400
    assert "unknown clue" in response.json()["detail"]


def test_scenes_before_the_analysis_stage_say_which_stage_is_missing(client) -> None:
    store_id = upload(client).json()["id"]

    response = client.get(f"/api/stores/{store_id}/scenes")

    assert response.status_code == 409
    assert "deepseek_analysis.json" in response.json()["detail"]


def test_screenshots_are_served_back_for_the_operator_to_check(client) -> None:
    store_id = upload(client).json()["id"]

    response = client.get(f"/api/stores/{store_id}/images/row_017_image_1.png")

    assert response.status_code == 200
    assert response.content == b"png-bytes"
