import json
from pathlib import Path
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from store_scenario_inspiration.app import jobs as jobs_module
from store_scenario_inspiration.app import main as main_module
from store_scenario_inspiration.app.config import Settings
from store_scenario_inspiration.app.main import create_app
from store_scenario_inspiration.app.params import load_params


PRODUCTS = [
    {"name_cn": "遮阳棚替换布", "name_en": "Awning Replacement Fabric",
     "role": "商品卡片主图", "confidence": 0.9, "evidence": "主图"},
    {"name_cn": "风扇", "name_en": "Fan", "role": "商品卡片主图", "confidence": 0.9, "evidence": "主图"},
]
SCENES = [
    {"scene_name": "庭院遮阳", "audience": "住户", "user_need": "遮阳", "evidence": "证据",
     "product_needs": [{"product_cn": "遮阳棚替换布", "product_en": "Sunshade", "purpose": "遮阳"},
                       {"product_cn": "风扇", "product_en": "Fan", "purpose": "通风"}]},
    {"scene_name": "亲子露营", "audience": "家庭", "user_need": "露营", "evidence": "证据",
     "product_needs": [{"product_cn": "儿童帐篷", "product_en": "Kids tent", "purpose": "露营"},
                       {"product_cn": "野餐垫", "product_en": "Picnic mat", "purpose": "铺垫"}]},
]
EXPANSIONS = [
    {"scene_name": "庭院遮阳", "products": [
        {"product_cn": "遮阳棚替换布", "product_en": "Sunshade", "canonical_cn": "遮阳棚替换布",
         "canonical_en": "Sun Shade Cloth", "expanded_cn": ["遮阳网"], "expanded_en": ["Sunshade Net"]},
        {"product_cn": "风扇", "product_en": "Fan", "canonical_cn": "风扇",
         "canonical_en": "Electric Fan", "expanded_cn": ["落地扇"], "expanded_en": ["Standing Fan"]},
    ]},
]


def install_fakes(monkeypatch) -> None:
    """Stand in for every stage that would otherwise reach the network."""
    def fake_recognize(entry, key):
        assert key == "test-key"
        frames = [{"filename": image["filename"], "products": PRODUCTS}
                  for image in entry["images"]]
        # The receipt carries the same frames as the result: it is the copy that
        # stays on disk, and the one a later screenshot gets folded into.
        return ({"images": frames},
                {"input_filenames": [image["filename"] for image in entry["images"]],
                 "images": frames, "response_id": "resp-1", "response_model": "deepseek-flash",
                 "usage": {"total_tokens": 42}})

    def sampling_ok(temperature):
        assert temperature == 0.2

    def fake_scenes(source, key, *, scene_count=8, conclusion=None, temperature=0.2):
        """The scene call answers with skeletons: four fields, no products, and
        written from the conclusion the step before it reached."""
        sampling_ok(temperature)
        assert key == "test-key"
        assert conclusion["manager_summary"] == {"x": 1}
        assert set(source["observed_product_clues"][0]) == {
            "clue", "role", "confidence", "evidence"}
        return ({"model": "deepseek-flash",
                 "scenes": [{k: v for k, v in scene.items() if k != "product_needs"}
                            for scene in SCENES]},
                {"response_id": "resp-2", "response_model": "deepseek-flash",
                 "usage": {"total_tokens": 99}})

    def fake_products(source, scene, key, *, products_per_scene=10, temperature=0.2):
        """One call per scene, and each one is told about only its own scene."""
        sampling_ok(temperature)
        assert key == "test-key"
        assert set(scene) == {"scene_name", "audience", "user_need", "evidence"}
        return ({"model": "deepseek-flash",
                 "products": next(item["product_needs"] for item in SCENES
                                  if item["scene_name"] == scene["scene_name"])},
                {"response_id": "resp-products", "response_model": "deepseek-flash",
                 "usage": {"total_tokens": 30}})

    def fake_synthesis(source, key, *, temperature=0.2):
        """Conclusions are written once, before there is any scene to summarise."""
        sampling_ok(temperature)
        assert key == "test-key"
        assert "scenes" not in source
        assert source["observed_product_clues"]
        return ({"model": "deepseek-flash", "manager_summary": {"x": 1}, "audiences": [],
                 "store_profile": {}, "current_product_structure": {},
                 "future_product_structure": {}, "operation_strategy": []},
                {"response_id": "resp-4", "response_model": "deepseek-flash",
                 "usage": {"total_tokens": 5}})

    def fake_expand(source, key, *, expansion_terms=6, temperature=0.2):
        sampling_ok(temperature)
        assert key == "test-key"
        assert source["scenes"][0]["products"][0] == {
            "product_cn": "遮阳棚替换布", "product_en": "Sunshade"}
        return ({"model": "deepseek-flash", "scenes": EXPANSIONS},
                {"response_id": "resp-3", "response_model": "deepseek-flash",
                 "usage": {"total_tokens": 7}})

    def fake_retrieve_store(*, products, **kwargs):
        """The real pass loads a 76MB vector index and two torch models."""
        return ({
            "schema": "store-retrieval-v1", "country": "PH", "inventory": "unavailable",
            "scenes": [{
                "scene_name": product["scene_name"],
                "product_cn": product["product_cn"],
                "product_en": product["product_en"],
                "queries": {"cn": [product["product_cn"]], "en": [product["product_en"]]},
                "candidates": [{"rank": 1, "main_sku": "SKU-1", "standard_name_cn": "遮阳网",
                                "standard_name_en": "Shade Net", "rrf_score": 0.0164,
                                "channels": ["cn_keyword"], "matched_queries": ["遮阳棚替换布"],
                                "country_available": None, "country_available_quantity": None}],
            } for product in products],
        }, {"schema": "store-stock-children-v1", "country": "PH", "available": False,
            "quantities": {}})

    def fake_load_children(asset_db, main_skus):
        """The real one opens the 8k-row product catalogue; the export only asks
        which children hang off the 货号 the operator picked."""
        return {sku: [(f"{sku}-1", f"{sku}子款")] for sku in main_skus}

    def fake_rerank_store(payload, **kwargs):
        """The real pass asks a model about every candidate. Here every candidate
        keeps its place, so the assertions about the recall still hold."""
        payload["rerank"] = {"schema": "store-rerank-v1", "asked": 4, "answered": 4,
                             "dropped": 0, "failed": 0, "notes": [], "mode": "drop",
                             "cutoff": 70}
        return payload

    monkeypatch.setattr(jobs_module, "recognize", fake_recognize)
    monkeypatch.setattr(jobs_module, "analyze_scenes", fake_scenes)
    monkeypatch.setattr(jobs_module, "analyze_scene_products", fake_products)
    monkeypatch.setattr(jobs_module, "analyze_synthesis", fake_synthesis)
    monkeypatch.setattr(jobs_module, "analyze_expansions", fake_expand)
    monkeypatch.setattr(jobs_module, "retrieve_store", fake_retrieve_store)
    monkeypatch.setattr(jobs_module, "rerank_store", fake_rerank_store)
    monkeypatch.setattr(main_module, "load_children", fake_load_children)


@pytest.fixture
def client(tmp_path, monkeypatch):
    install_fakes(monkeypatch)
    app = create_app(Settings(data_dir=tmp_path, deepseek_api_key="test-key",
                              typesafe_api_key="tf-key"))
    with TestClient(app) as client:
        yield client
    app.state.jobs.shutdown()


@pytest.fixture
def keyless_client(tmp_path, monkeypatch):
    """No TypeSafe key: the rerank step has to skip itself, not fail.

    Said out loud rather than left out, because the machine's own .env would
    otherwise supply one and quietly turn this into the keyed case.
    """
    install_fakes(monkeypatch)
    app = create_app(Settings(data_dir=tmp_path, deepseek_api_key="test-key",
                              typesafe_api_key=""))
    with TestClient(app) as client:
        yield client
    app.state.jobs.shutdown()


def upload(client, store_name="Shopee-13021PH", country="PH", count=2):
    files = [("files", (f"row_017_image_{index}.png", b"png-bytes", "image/png"))
             for index in range(1, count + 1)]
    return client.post("/api/stores", data={"store_name": store_name, "country": country},
                       files=files)


def post_job(client, store_id: str, stages: list[str] | None = None,
             params: dict | None = None) -> dict:
    body = {key: value for key, value in
            (("stages", stages), ("params", params)) if value is not None} or None
    response = client.post(f"/api/stores/{store_id}/jobs", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def settle(client, job_id: str) -> dict:
    """Wait for the background job, which runs off the request thread."""
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"ready", "failed", "cancelled"}:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not settle: {job}")


def run_job(client, store_id: str, stages: list[str] | None = None,
            params: dict | None = None) -> dict:
    """One press, which is what the upload page sends.

    With no stage list the backend runs the whole pipeline: the reading and
    everything written from it land in one job, and the operator edits the
    result afterwards rather than approving the product list first.
    """
    return settle(client, post_job(client, store_id, stages, params)["id"])


def read_analysis_input(client, store_id: str) -> dict:
    """What the exclusion list actually fed the scene generator."""
    path = client.app.state.settings.data_dir / "stores" / store_id / "analysis_input.json"
    return json.loads(path.read_text(encoding="utf-8"))


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


def test_a_store_with_no_screenshots_runs_on_the_products_that_were_typed(client) -> None:
    """Some shops are described by what the operator types and nothing else. The
    pipeline runs for them the same way, and the pictures it never had cost
    nothing."""
    created = client.post("/api/stores", data={"store_name": "手填店", "country": "PH"})

    assert created.status_code == 200, created.text
    store_id = created.json()["id"]
    assert created.json()["images"] == []

    typed = client.put(f"/api/stores/{store_id}/products", json={
        "products": [{"name_cn": "遮阳棚替换布", "name_en": "Awning Fabric"}]})
    assert typed.status_code == 200, typed.text
    assert [entry["clue"] for entry in typed.json()["entries"]] == ["遮阳棚替换布"]
    # The list exists before the store has ever been read: this is what the
    # operator edits, and it is the same list a recognised product lands in.
    assert typed.json()["entries"][0]["manual"] is True

    job = run_job(client, store_id)

    assert job["status"] == "ready"
    assert job["stages"][0]["detail"] == "没有截图，商品名单从手工填写开始"
    assert [stage["status"] for stage in job["stages"]] == ["ready"] * 8
    source = read_analysis_input(client, store_id)
    assert [item["clue"] for item in source["observed_product_clues"]] == ["遮阳棚替换布"]
    assert [item["role"] for item in source["observed_product_clues"]] == ["人工添加"]
    assert client.get(f"/api/stores/{store_id}/retrieval").status_code == 200


def test_a_store_with_neither_screenshots_nor_products_is_refused(client) -> None:
    """The one mistake worth catching before it costs a whole run: there is
    nothing to read a store from."""
    store_id = client.post("/api/stores",
                           data={"store_name": "空店", "country": "PH"}).json()["id"]

    refused = client.post(f"/api/stores/{store_id}/jobs")

    assert refused.status_code == 409
    assert "还没有商品" in refused.json()["detail"]


def test_running_the_pipeline_produces_scenes_expansion_and_candidates(client) -> None:
    store_id = upload(client).json()["id"]

    # One press, from the screenshots to the candidates: the operator edits the
    # product list afterwards, from the results, rather than approving it first.
    job = run_job(client, store_id)

    assert job["status"] == "ready"
    assert [stage["name"] for stage in job["stages"]] == [
        "recognize", "clues", "synthesis", "scenes", "products", "expand", "retrieval", "rerank",
    ]
    assert [stage["status"] for stage in job["stages"]] == ["ready"] * 8
    # Every stage reports how long the operator waited for it.
    assert all(isinstance(stage["seconds"], float) for stage in job["stages"])
    assert job["seconds"] >= 0
    # Every paid stage's spend lands in one running total, including the one call
    # per scene in the products stage.
    assert job["usage"]["total_tokens"] == 42 + 99 + 30 * 2 + 5 + 7

    source = read_analysis_input(client, store_id)
    assert [item["clue"] for item in source["observed_product_clues"]] == ["遮阳棚替换布", "风扇"]

    scenes = client.get(f"/api/stores/{store_id}/analysis").json()
    assert [item["scene_name"] for item in scenes["scenes"]] == ["庭院遮阳", "亲子露营"]
    assert scenes["scenes"][0]["excluded"] == []

    retrieval = client.get(f"/api/stores/{store_id}/retrieval").json()
    assert retrieval["scenes"][0]["product_cn"] == "遮阳棚替换布"
    assert retrieval["scenes"][0]["candidates"][0]["main_sku"] == "SKU-1"


def test_the_paid_stages_are_marked_so_the_operator_knows_what_costs_money(client) -> None:
    store_id = upload(client).json()["id"]

    job = run_job(client, store_id)

    # The reading half: the screenshots cost money, applying the exclusion list
    # does not.
    assert [stage["paid"] for stage in job["stages"][:2]] == [True, False]
    # Judging the candidates is free on Jev, which is what a store starts on, and
    # charged by DeepSeek, so the tag follows whichever model the store is set to.
    assert [stage["paid"] for stage in job["stages"][2:]] == [
        True, True, True, True, False, False,
    ]

    billed = run_job(client, store_id, stages=["rerank"],
                     params={"rerank_provider": "deepseek"})
    assert billed["stages"][-1]["paid"] is True


def test_redoing_the_local_stage_never_pays_for_anything_again(client, monkeypatch) -> None:
    """Tuning the recall count answers "what does the catalogue have", which is a
    question the operator asks over and over while they tune it. Re-asking it
    recounts the same recalled candidates and must not re-run one paid stage."""
    store_id = upload(client).json()["id"]
    run_job(client, store_id)

    def refuse(*args, **kwargs):
        raise AssertionError("a paid stage ran again")

    monkeypatch.setattr(jobs_module, "recognize", refuse)
    monkeypatch.setattr(jobs_module, "analyze_scenes", refuse)
    monkeypatch.setattr(jobs_module, "analyze_scene_products", refuse)
    monkeypatch.setattr(jobs_module, "analyze_synthesis", refuse)
    monkeypatch.setattr(jobs_module, "analyze_expansions", refuse)

    job = run_job(client, store_id, stages=["retrieval"], params={"recall_limit": 5})

    assert job["status"] == "ready"
    assert job["usage"] == {}
    assert client.get(f"/api/stores/{store_id}/retrieval").status_code == 200


def test_excluding_a_product_leaves_the_results_behind_without_deleting_them(client) -> None:
    """Ruling a product out is the edit the operator makes most, and it is local:
    the exclusion is applied at once, everything written from the old list is
    marked behind so the one button offers to redo it, and the run they already
    paid for stays readable."""
    store_id = upload(client).json()["id"]
    run_job(client, store_id)

    client.put(f"/api/stores/{store_id}/clues", json={"excluded": ["风扇"]})

    # The list the next run would be written from is the new one, already.
    assert [item["clue"] for item in read_analysis_input(client, store_id)["observed_product_clues"]] == [
        "遮阳棚替换布"
    ]
    # The reading itself is untouched — the pictures did not change — and
    # everything written from the list is.
    assert client.get(f"/api/stores/{store_id}").json()["outdated"] == [
        "synthesis", "scenes", "products", "expand", "retrieval", "rerank"]
    # And the results of the previous run are still readable.
    assert client.get(f"/api/stores/{store_id}/retrieval").status_code == 200


def test_choosing_a_model_without_a_key_skips_the_rerank_step(keyless_client) -> None:
    """The account has no key yet, so the step says so and stays out of the way
    rather than failing the run."""
    store_id = upload(keyless_client).json()["id"]

    job = run_job(keyless_client, store_id, params={"rerank_provider": "jev"})

    assert job["status"] == "ready"
    assert job["stages"][-1]["status"] == "ready"
    assert "TypeSafe 的 Jev" in job["stages"][-1]["detail"]
    # The list the operator came for is untouched.
    assert keyless_client.get(
        f"/api/stores/{store_id}/retrieval").json()["scenes"][0]["candidates"][0]["main_sku"] == "SKU-1"


def test_turning_the_verdict_off_puts_the_dropped_candidates_back(client) -> None:
    """A previous pass removed rows. Leaving those rows gone while calling the
    step "off" would show a list nobody chose."""
    store_id = upload(client).json()["id"]
    run_job(client, store_id)
    base = client.app.state.settings.data_dir / "stores" / store_id
    retrieval = json.loads((base / "retrieval.json").read_text(encoding="utf-8"))
    kept = retrieval["scenes"][0]["candidates"][0]
    kept["recall_rank"] = 2
    retrieval["scenes"][0]["dropped"] = [
        {**kept, "main_sku": "SKU-9", "recall_rank": 1, "rerank": "unrelated"}]
    (base / "retrieval.json").write_text(json.dumps(retrieval), encoding="utf-8")

    job = run_job(client, store_id, stages=["rerank"], params={"rerank_provider": "off"})

    assert job["status"] == "ready"
    assert "回到了列表" in job["stages"][-1]["detail"]
    back = client.get(f"/api/stores/{store_id}/retrieval").json()
    assert [row["main_sku"] for row in back["scenes"][0]["candidates"]] == ["SKU-9", "SKU-1"]
    assert "rerank" not in back
    assert not (base / "rerank.json").exists()


def test_a_model_that_is_down_costs_the_verdicts_not_the_recall(client, monkeypatch) -> None:
    """The recall is what the operator came for. A second opinion that cannot be
    obtained is a missing second opinion, not a failed run."""
    def refuse(payload, **kwargs):
        raise RuntimeError("TypeSafe 403: customer_verification_required")

    monkeypatch.setattr(jobs_module, "rerank_store", refuse)
    store_id = upload(client).json()["id"]

    job = run_job(client, store_id)

    assert job["status"] == "ready"
    assert job["stages"][-1]["detail"].startswith("模型暂时用不了")
    assert "403" in job["stages"][-1]["detail"]
    # Nothing was removed and nothing was written as if the pass had run.
    assert not (client.app.state.settings.data_dir / "stores" / store_id / "rerank.json").exists()
    assert client.get(
        f"/api/stores/{store_id}/retrieval").json()["scenes"][0]["candidates"][0]["main_sku"] == "SKU-1"


def test_a_stage_the_pipeline_does_not_have_is_rejected(client) -> None:
    store_id = upload(client).json()["id"]

    response = client.post(f"/api/stores/{store_id}/jobs", json={"stages": ["adoption"]})

    assert response.status_code == 400
    assert response.json()["detail"] == "未知的处理步骤：adoption"


def test_operator_can_rule_a_product_out_and_the_next_run_avoids_it(client) -> None:
    store_id = upload(client).json()["id"]
    run_job(client, store_id)

    before = client.get(f"/api/stores/{store_id}/clues").json()
    assert before["counts"] == {"kept": 2, "excluded": 0}

    updated = client.put(f"/api/stores/{store_id}/clues", json={"excluded": ["风扇"]}).json()

    assert updated["counts"] == {"kept": 1, "excluded": 1}
    assert updated["excluded"] == ["风扇"]
    payload = client.get(f"/api/stores/{store_id}").json()
    assert payload["kept_clues"] == ["遮阳棚替换布"]
    assert payload["excluded_clues"] == ["风扇"]


def test_the_exclusion_list_survives_a_restart(client, tmp_path) -> None:
    store_id = upload(client).json()["id"]
    run_job(client, store_id)
    client.put(f"/api/stores/{store_id}/clues", json={"excluded": ["风扇"]})

    stored = json.loads((tmp_path / "stores" / store_id / "exclusions.json")
                        .read_text(encoding="utf-8"))

    assert stored == {"schema": "store-exclusions-v1", "excluded": ["风扇"]}


def test_clearing_the_exclusion_list_puts_the_product_back(client) -> None:
    store_id = upload(client).json()["id"]
    run_job(client, store_id)
    client.put(f"/api/stores/{store_id}/clues", json={"excluded": ["风扇"]})

    client.put(f"/api/stores/{store_id}/clues", json={"excluded": []})

    assert client.get(f"/api/stores/{store_id}/clues").json()["counts"]["excluded"] == 0


def test_a_product_the_operator_adds_joins_the_list_the_scenes_are_built_from(
    client, tmp_path,
) -> None:
    """Recognition only sees the screenshots, and a store sells things nobody
    photographed. The product the operator types in has to reach the same place
    a recognised one does, or the button is decorative."""
    store_id = upload(client).json()["id"]
    run_job(client, store_id)

    updated = client.put(f"/api/stores/{store_id}/products",
                         json={"products": [{"name_cn": "折叠衣架", "name_en": "Folding Hanger"}]}).json()

    assert updated["counts"] == {"kept": 3, "excluded": 0}
    assert updated["custom"] == [{"name_cn": "折叠衣架", "name_en": "Folding Hanger"}]
    added = next(entry for entry in updated["entries"] if entry["clue"] == "折叠衣架")
    assert added["manual"] is True
    assert added["image_count"] == 0
    assert "运营手动添加" in added["evidence"]
    # The scene generator is what the scenes are actually written from.
    source = json.loads((tmp_path / "stores" / store_id / "analysis_input.json")
                        .read_text(encoding="utf-8"))
    assert [clue["clue"] for clue in source["observed_product_clues"]] == [
        "遮阳棚替换布", "风扇", "折叠衣架"]


def test_something_the_operator_added_is_not_excluded_by_default(client) -> None:
    """It was added on purpose. Reading an absent exclusion as a decision to
    exclude would rule out the very product the operator just asked for."""
    store_id = upload(client).json()["id"]
    run_job(client, store_id)

    client.put(f"/api/stores/{store_id}/products",
               json={"products": [{"name_cn": "折叠衣架"}]})

    payload = client.get(f"/api/stores/{store_id}").json()
    assert payload["kept_clues"] == ["遮阳棚替换布", "风扇", "折叠衣架"]
    assert payload["excluded_clues"] == []


def test_a_product_added_twice_is_kept_once(client) -> None:
    store_id = upload(client).json()["id"]
    run_job(client, store_id)

    client.put(f"/api/stores/{store_id}/products", json={"products": [
        {"name_cn": "折叠衣架", "name_en": "Folding Hanger"},
        {"name_cn": " 折叠衣架 ", "name_en": "Hanger, Foldable"},
    ]})

    entries = client.get(f"/api/stores/{store_id}/clues").json()["entries"]
    assert [entry["clue"] for entry in entries].count("折叠衣架") == 1


def test_a_product_recognition_already_saw_is_left_to_the_screenshot(client) -> None:
    """It is already in the list with the evidence behind it. A second copy with
    no images attached would only make the same product look like two."""
    store_id = upload(client).json()["id"]
    run_job(client, store_id)

    client.put(f"/api/stores/{store_id}/products",
               json={"products": [{"name_cn": "风扇", "name_en": "Fan"}]})

    entries = client.get(f"/api/stores/{store_id}/clues").json()["entries"]
    assert [entry["clue"] for entry in entries].count("风扇") == 1
    assert next(entry for entry in entries if entry["clue"] == "风扇")["manual"] is False


def test_removing_a_product_the_operator_added_takes_it_back_out(client) -> None:
    store_id = upload(client).json()["id"]
    run_job(client, store_id)
    client.put(f"/api/stores/{store_id}/products",
               json={"products": [{"name_cn": "折叠衣架"}]})

    client.put(f"/api/stores/{store_id}/products", json={"products": []})

    assert client.get(f"/api/stores/{store_id}/clues").json()["counts"]["kept"] == 2


def test_a_product_added_by_hand_can_be_excluded_like_any_other(client) -> None:
    """It goes through the same list, so the same lever has to reach it."""
    store_id = upload(client).json()["id"]
    run_job(client, store_id)
    client.put(f"/api/stores/{store_id}/products",
               json={"products": [{"name_cn": "折叠衣架"}]})

    updated = client.put(f"/api/stores/{store_id}/clues", json={"excluded": ["折叠衣架"]}).json()

    assert updated["counts"] == {"kept": 2, "excluded": 1}


def test_a_product_added_with_no_chinese_name_is_rejected(client) -> None:
    store_id = upload(client).json()["id"]
    run_job(client, store_id)

    response = client.put(f"/api/stores/{store_id}/products",
                          json={"products": [{"name_cn": "  ", "name_en": "Hanger"}]})

    assert response.status_code == 400
    assert "中文名" in response.json()["detail"]


def test_excluding_something_recognition_never_saw_is_rejected(client) -> None:
    store_id = upload(client).json()["id"]
    run_job(client, store_id)

    response = client.put(f"/api/stores/{store_id}/clues", json={"excluded": ["不存在的东西"]})

    assert response.status_code == 400
    assert "不存在的东西" in response.json()["detail"]


def test_an_excluded_product_is_marked_in_the_scene_that_still_carries_it(client) -> None:
    """Excluding is not a rewrite: the analysis already paid for keeps the
    product, and the report says plainly which parts the operator ruled out."""
    store_id = upload(client).json()["id"]
    run_job(client, store_id)

    client.put(f"/api/stores/{store_id}/clues", json={"excluded": ["风扇"]})

    scenes = client.get(f"/api/stores/{store_id}/analysis").json()["scenes"]
    assert scenes[0]["excluded"] == ["风扇"]
    assert scenes[1]["excluded"] == []


def test_analysis_before_the_stage_has_run_says_which_stage_is_missing(client) -> None:
    store_id = upload(client).json()["id"]

    response = client.get(f"/api/stores/{store_id}/analysis")

    assert response.status_code == 409
    assert "deepseek_analysis.json" in response.json()["detail"]


def test_retrieval_before_the_stage_has_run_says_which_stage_is_missing(client) -> None:
    store_id = upload(client).json()["id"]

    response = client.get(f"/api/stores/{store_id}/retrieval")

    assert response.status_code == 409
    assert "retrieval.json" in response.json()["detail"]


def test_a_store_that_was_never_created_is_not_a_server_error(client) -> None:
    """The browser prints whatever comes back, and "500" tells the operator nothing."""
    response = client.get("/api/stores/no-such-store")

    assert response.status_code == 404
    assert response.json()["detail"] == "没有这个店铺"


def test_a_store_id_the_workspace_could_not_have_issued_is_rejected(client) -> None:
    """Ids are generated from store names, so anything else never named a store."""
    assert client.get("/api/stores/Bad_Id").status_code == 400


def test_the_parameter_schema_carries_a_chinese_explanation_per_knob(client) -> None:
    fields = client.get("/api/params/schema").json()["fields"]

    assert [field["name"] for field in fields] == [
        "scene_count", "products_per_scene", "expansion_terms", "recall_limit",
        "stock_filter", "rerank", "rerank_provider", "rerank_cutoff", "temperature",
    ]
    assert all(field["description"].strip() for field in fields)
    for field in fields:
        if field["options"]:
            continue
        assert field["minimum"] <= field["default"] <= field["maximum"]
        # Every number knob has to be turnable up from where it starts. The
        # penalties sit at their floor because zero means "off", but a default
        # pinned to its ceiling — which products_per_scene was — leaves the
        # operator with a control that only moves one way.
        assert field["default"] < field["maximum"], field["name"]


def test_the_choice_knob_hands_the_form_its_options_and_labels(client) -> None:
    """The stock knob is a choice, not a number, so the form needs both the
    values to send and the sentences to show instead of them."""
    field = next(f for f in client.get("/api/params/schema").json()["fields"]
                 if f["name"] == "stock_filter")

    assert field["options"] == ["all", "in_stock"]
    assert set(field["labels"]) == {"all", "in_stock"}
    assert all(label.strip() for label in field["labels"].values())


def test_a_store_starts_from_the_default_parameters(client) -> None:
    store_id = upload(client).json()["id"]

    params = client.get(f"/api/stores/{store_id}/params").json()

    assert params == {"scene_count": 6, "products_per_scene": 16, "expansion_terms": 0,
                      "recall_limit": 30, "stock_filter": "all", "rerank": "mark_only",
                      "rerank_provider": "jev", "rerank_cutoff": 50,
                      "temperature": 0.2}


def test_parameters_are_saved_per_store_and_survive_a_restart(client, tmp_path) -> None:
    store_id = upload(client).json()["id"]

    client.put(f"/api/stores/{store_id}/params",
               json={"scene_count": 8, "products_per_scene": 30,
                     "expansion_terms": 12, "recall_limit": 200})

    assert json.loads((tmp_path / "stores" / store_id / "params.json")
                      .read_text(encoding="utf-8"))["schema"] == "store-params-v1"
    assert client.get(f"/api/stores/{store_id}/params").json()["scene_count"] == 8


def test_parameters_outside_their_range_are_rejected(client) -> None:
    store_id = upload(client).json()["id"]

    response = client.put(f"/api/stores/{store_id}/params",
                          json={"scene_count": 99, "products_per_scene": 10,
                                "expansion_terms": 6, "recall_limit": 30})

    assert response.status_code == 422


def test_the_pair_that_used_to_overflow_one_answer_is_now_allowed(client) -> None:
    """8 scenes × 40 products was refused while every scene came back in a single
    response. Each scene is its own call now, so the two numbers no longer have to
    fit together and the pair is just two knobs."""
    store_id = upload(client).json()["id"]

    response = client.put(f"/api/stores/{store_id}/params",
                          json={"scene_count": 8, "products_per_scene": 40,
                                "expansion_terms": 6, "recall_limit": 30})

    assert response.status_code == 200


def test_params_saved_under_an_older_range_still_open(tmp_path) -> None:
    """A store tuned when 12 scenes and 20 products were allowed must not become
    unopenable — its page could not render the form that would let the operator
    fix it. Every number comes back inside the range now in force."""
    path = tmp_path / "params.json"
    path.write_text(json.dumps({
        "schema": "store-params-v1", "scene_count": 12, "products_per_scene": 20,
        "expansion_terms": 12, "recall_limit": 200, "stock_filter": "all",
        "rerank": "drop", "rerank_provider": "deepseek", "rerank_cutoff": 70,
    }), encoding="utf-8")

    params = load_params(path)

    assert params.scene_count == 8
    assert params.products_per_scene == 20
    assert params.recall_limit == 200


def test_a_temperature_above_the_new_ceiling_is_pulled_down_on_open(tmp_path) -> None:
    """The ceiling moved after a store had already been saved above it. Floats were
    skipped by the clamp, so that store would have failed validation on open —
    the very thing the clamp is there to prevent.

    The file also still carries the two penalty knobs that have since been taken
    off the form, because that is exactly what a store saved before the change
    looks like on disk. It has to keep opening."""
    path = tmp_path / "params.json"
    path.write_text(json.dumps({
        "schema": "store-params-v1", "scene_count": 8, "products_per_scene": 30,
        "expansion_terms": 10, "recall_limit": 50, "stock_filter": "all",
        "rerank": "mark_only", "rerank_provider": "deepseek", "rerank_cutoff": 70,
        "temperature": 2.0, "frequency_penalty": 1.0, "presence_penalty": 1.0,
    }), encoding="utf-8")

    params = load_params(path)

    assert params.temperature == 1.0
    # The retired knobs are dropped rather than carried on the object, so nothing
    # downstream can read a setting the form no longer offers.
    assert "frequency_penalty" not in params.model_dump()


def test_parameters_can_be_set_on_the_run_that_uses_them(client) -> None:
    store_id = upload(client).json()["id"]

    run_job(client, store_id, params={"scene_count": 6, "products_per_scene": 8,
                                      "expansion_terms": 2, "recall_limit": 5})

    assert client.get(f"/api/stores/{store_id}/params").json()["recall_limit"] == 5


def test_screenshots_are_served_back_for_the_operator_to_check(client) -> None:
    store_id = upload(client).json()["id"]

    response = client.get(f"/api/stores/{store_id}/images/row_017_image_1.png")

    assert response.status_code == 200
    assert response.content == b"png-bytes"


# --- 截图增删 ------------------------------------------------------------------
#
# A store is read once and then worked on for a while, so the screenshots are not
# fixed at upload time. What matters is what each edit costs: adding a picture is
# a picture's worth of reading, and taking one away is free.


def reads_by_screenshot(products_for: dict[str, list[str]], reads: list[list[str]]):
    """A vision pass that reports a different product list per screenshot."""

    def fake(entry, key):
        assert key == "test-key"
        reads.append([image["filename"] for image in entry["images"]])
        frames = [{"filename": image["filename"],
                   "products": [{"name_cn": name, "name_en": f"{name} (listing)",
                                 "role": "商品卡片主图", "confidence": 0.9,
                                 "evidence": "测试"} for name in products_for[image["filename"]]]}
                  for image in entry["images"]]
        return ({"images": frames},
                {"input_filenames": [image["filename"] for image in entry["images"]],
                 "images": frames, "response_id": "resp-1", "response_model": "deepseek-flash",
                 "usage": {"total_tokens": 42}})

    return fake


def post_images(client, store_id: str, names: list[str]):
    files = [("files", (name, b"png-bytes", "image/png")) for name in names]
    return client.post(f"/api/stores/{store_id}/images", files=files)


def clue_names(client, store_id: str) -> list[str]:
    return [entry["clue"] for entry in client.get(f"/api/stores/{store_id}/clues").json()["entries"]]


def test_a_screenshot_added_later_is_the_only_one_read_again(client, monkeypatch) -> None:
    store_id = upload(client).json()["id"]
    settle(client, post_job(client, store_id, ["recognize", "clues"])["id"])

    reads: list[list[str]] = []
    monkeypatch.setattr(jobs_module, "recognize", reads_by_screenshot(
        {"row_017_image_3.png": ["折叠椅"]}, reads))

    added = post_images(client, store_id, ["row_017_image_3.png"])
    assert added.status_code == 200, added.text
    assert added.json()["added"] == ["row_017_image_3.png"]
    settle(client, post_job(client, store_id, ["recognize", "clues"])["id"])

    # One picture read, and it is the new one: the two that were already read
    # are not sent again.
    assert reads == [["row_017_image_3.png"]]
    assert clue_names(client, store_id) == ["遮阳棚替换布", "风扇", "折叠椅"]


def test_a_picture_added_later_does_not_disturb_what_was_already_read(client) -> None:
    store_id = upload(client).json()["id"]
    settle(client, post_job(client, store_id, ["recognize", "clues"])["id"])
    excluded = client.put(f"/api/stores/{store_id}/clues", json={"excluded": ["风扇"]}).json()
    assert [entry["clue"] for entry in excluded["entries"] if entry["excluded"]] == ["风扇"]

    assert post_images(client, store_id, ["late.png"]).status_code == 200

    # Still excluded, because the reading the exclusion was made against is the
    # one still on disk for that picture.
    review = client.get(f"/api/stores/{store_id}/clues").json()
    assert review["excluded"] == ["风扇"]


def test_a_product_only_the_deleted_screenshot_showed_goes_with_it(client, monkeypatch) -> None:
    store_id = upload(client).json()["id"]
    monkeypatch.setattr(jobs_module, "recognize", reads_by_screenshot({
        "row_017_image_1.png": ["遮阳棚替换布", "风扇"],
        "row_017_image_2.png": ["风扇", "儿童帐篷"],
    }, []))
    settle(client, post_job(client, store_id, ["recognize", "clues"])["id"])
    assert clue_names(client, store_id) == ["遮阳棚替换布", "风扇", "儿童帐篷"]

    def refuse(entry, key):
        raise AssertionError("删除截图不该再调用识别")

    monkeypatch.setattr(jobs_module, "recognize", refuse)
    removed = client.delete(f"/api/stores/{store_id}/images/row_017_image_1.png")

    assert removed.status_code == 200, removed.text
    # 遮阳棚替换布 was only in the deleted picture; 风扇 was in both.
    assert clue_names(client, store_id) == ["风扇", "儿童帐篷"]
    assert len(removed.json()["store"]["images"]) == 1


def test_deleting_a_screenshot_leaves_the_analysis_behind(client) -> None:
    """A picture that is gone was part of what the reading was made of, so the
    reading is redone and everything written from it is marked behind."""
    store_id = upload(client).json()["id"]
    run_job(client, store_id)
    assert client.get(f"/api/stores/{store_id}").json()["outdated"] == []

    assert client.delete(f"/api/stores/{store_id}/images/row_017_image_1.png").status_code == 200

    assert client.get(f"/api/stores/{store_id}").json()["outdated"] == [
        "synthesis", "scenes", "products", "expand", "retrieval", "rerank"]


def test_the_last_screenshot_can_be_deleted(client) -> None:
    """A store described by typed products alone is still a store, so the
    pictures can all go. The reading is redone from nothing — free — and what
    was written from the old one is left behind, as any edit leaves it."""
    store_id = upload(client, count=1).json()["id"]
    run_job(client, store_id)

    response = client.delete(f"/api/stores/{store_id}/images/row_017_image_1.png")

    assert response.status_code == 200, response.text
    assert response.json()["store"]["images"] == []
    assert client.get(f"/api/stores/{store_id}").json()["outdated"] == [
        "synthesis", "scenes", "products", "expand", "retrieval", "rerank"]


def test_deleting_a_screenshot_that_is_not_there_is_rejected(client) -> None:
    store_id = upload(client).json()["id"]

    response = client.delete(f"/api/stores/{store_id}/images/nope.png")

    assert response.status_code == 409
    assert "没有这张截图" in response.json()["detail"]


def test_a_screenshot_added_after_the_analysis_leaves_that_analysis_behind(client) -> None:
    store_id = upload(client).json()["id"]
    run_job(client, store_id)
    assert client.get(f"/api/stores/{store_id}").json()["outdated"] == []

    assert post_images(client, store_id, ["late.png"]).status_code == 200

    # The reading first, and then everything built on it: none of it describes a
    # store that now holds a picture nobody has looked at.
    assert client.get(f"/api/stores/{store_id}").json()["outdated"][:2] == ["recognize", "clues"]


def test_an_edited_setting_puts_behind_only_the_step_that_reads_it(client) -> None:
    store_id = upload(client).json()["id"]
    run_job(client, store_id)

    saved = client.get(f"/api/stores/{store_id}").json()["params"]
    saved["recall_limit"] = saved["recall_limit"] + 5
    assert client.put(f"/api/stores/{store_id}/params", json=saved).status_code == 200

    # Recall count is the search's own setting; the writing above it did not
    # read it and is not asked to run again.
    assert client.get(f"/api/stores/{store_id}").json()["outdated"] == ["retrieval", "rerank"]


def test_a_writing_setting_puts_the_whole_chain_behind(client) -> None:
    store_id = upload(client).json()["id"]
    run_job(client, store_id)

    saved = client.get(f"/api/stores/{store_id}").json()["params"]
    saved["scene_count"] = saved["scene_count"] + 1
    assert client.put(f"/api/stores/{store_id}/params", json=saved).status_code == 200

    assert client.get(f"/api/stores/{store_id}").json()["outdated"] == [
        "scenes", "products", "expand", "retrieval", "rerank"]


def test_a_store_the_app_never_ran_is_not_called_behind_by_a_setting(client) -> None:
    store_id = upload(client).json()["id"]
    run_job(client, store_id)
    base = client.app.state.settings.data_dir / "stores" / store_id
    # What a store produced by the command line looks like: the same artifacts,
    # and nothing saying what they were run with.
    (base / "run_state.json").unlink()

    saved = client.get(f"/api/stores/{store_id}").json()["params"]
    saved["scene_count"] = saved["scene_count"] + 1
    assert client.put(f"/api/stores/{store_id}/params", json=saved).status_code == 200

    assert client.get(f"/api/stores/{store_id}").json()["outdated"] == []
