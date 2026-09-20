import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from run_store_pilot import resolve_outputs
from serve_pilot_dashboard import _project_retrieval


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "batch_store_pipeline.py"
DEEPSEEK_BATCH_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_deepseek_batches.py"
ASSEMBLE = Path(__file__).resolve().parents[1] / "scripts" / "assemble_store_pilot.py"


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def observation(clue: str, role: str) -> dict:
    return {"clue": clue, "role": role, "confidence": 0.9, "evidence": "依据"}


def occurrence(image: str, role: str) -> dict:
    return {"image": image, "role": role, "confidence": 0.9, "evidence": "依据"}


def test_batch_resumes_at_api_free_assembly(tmp_path) -> None:
    base = tmp_path / "stores" / "row-1"
    write(tmp_path / "manifest.json", {"models": ["gpt55"], "stores": [{"id": "row-1"}]})
    write(base / "sample_store.json", {
        "store": {"country": "PH"}, "observed_product_clues": [{"text": "chair"}],
    })
    analysis = {
        "model": "gpt-5.5", "manager_summary": {}, "store_profile": {}, "audiences": [],
        "current_product_structure": {}, "future_product_structure": {},
        "operation_strategy": [], "scenes": [{"scene_name": "休息", "product_needs": []}],
    }
    write(base / "gpt55_analysis.json", analysis)
    write(base / "gpt55_expansions.json", {"model": "gpt-5.5", "scenes": []})
    write(base / "retrieval" / "gpt55_retrieval.json", {
        "model": "gpt-5.5", "country": "PH", "scenes": [{"scene_name": "休息", "products": []}],
    })
    write(base / "gpt55_rerank.json", {
        "model": "deepseek-flash", "scenes": [{"scene_name": "休息", "products": []}],
    })

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path / "manifest.json"), "--run-local"],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )

    assert json.loads(result.stdout)["runs"][0]["stages"]["final"] == "ready"
    assert json.loads((base / "gpt55_final.json").read_text(encoding="utf-8"))["recommended_main_skus"] == []


def test_pipeline_applies_the_direction_gate_before_analysis(tmp_path) -> None:
    base = tmp_path / "stores" / "row-1"
    write(tmp_path / "manifest.json", {"models": ["deepseek"], "stores": [{"id": "row-1"}]})
    write(base / "sample_store.json", {
        "store": {"country": "PH"},
        "observed_product_clues": [
            {**observation("遮阳棚替换布", "商品卡片主图"),
             "occurrences": [occurrence("a.png", "商品卡片主图")]},
            {**observation("Micro SD/CCTV 存储卡", "场景中偶然出现"),
             "occurrences": [occurrence("a.png", "场景中偶然出现")]},
        ],
    })

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path / "manifest.json")],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )
    run = json.loads(result.stdout)["runs"][0]

    assert run["stages"]["direction"] == "ready"
    assert run["stages"]["analysis"] == "waiting_model"
    assert "direction_hint" not in run
    assert "analysis_input.json" in run["analysis_command"]
    payload = json.loads((base / "analysis_input.json").read_text(encoding="utf-8"))
    assert payload["direction_confirmed"] is True
    assert [item["clue"] for item in payload["observed_product_clues"]] == ["遮阳棚替换布"]
    assert [item["clue"] for item in payload["excluded_product_clues"]] == ["Micro SD/CCTV 存储卡"]


def test_pipeline_still_runs_when_no_direction_is_confirmed(tmp_path) -> None:
    """A store nobody has sorted must not stall. It gets a hint, not a block."""
    base = tmp_path / "stores" / "row-1"
    write(tmp_path / "manifest.json", {"models": ["deepseek"], "stores": [{"id": "row-1"}]})
    write(base / "sample_store.json", {
        "store": {"country": "PH"},
        "observed_product_clues": [
            {**observation("焊机、电钻等电动工具", "不确定"),
             "occurrences": [occurrence("a.png", "不确定")]},
        ],
    })

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path / "manifest.json")],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )
    run = json.loads(result.stdout)["runs"][0]

    assert run["stages"]["analysis"] == "waiting_model"
    assert "可以试试" in run["direction_hint"]
    payload = json.loads((base / "analysis_input.json").read_text(encoding="utf-8"))
    assert payload["direction_confirmed"] is False
    assert [item["clue"] for item in payload["observed_product_clues"]] == ["焊机、电钻等电动工具"]


def test_retrieval_outputs_pair_by_input_order_and_reject_overlap(tmp_path) -> None:
    inputs = [
        (Path("one.json"), {"model": "gpt-5.5"}),
        (Path("two.json"), {"model": "deepseek-flash"}),
    ]
    outputs = [tmp_path / "one.json", tmp_path / "two.json"]
    assert resolve_outputs(inputs, outputs, tmp_path) == outputs

    try:
        resolve_outputs(inputs, outputs[:1], tmp_path)
    except ValueError as error:
        assert "count" in str(error)
    else:
        raise AssertionError("mismatched output count was accepted")

    try:
        resolve_outputs(inputs, [outputs[0], outputs[0]], tmp_path)
    except ValueError as error:
        assert "unique" in str(error)
    else:
        raise AssertionError("duplicate outputs were accepted")


def test_deepseek_batch_rejects_country_outside_inventory_scope(tmp_path) -> None:
    manifest = tmp_path / "manifest.json"
    write(manifest, {"models": ["deepseek"], "stores": [{"id": "store-br", "country": "BR"}]})

    result = subprocess.run(
        [sys.executable, str(DEEPSEEK_BATCH_SCRIPT), str(manifest)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "DEEPSEEK_API_KEY": "test-key"},
    )

    assert result.returncode != 0
    assert "unsupported country" in result.stderr


def test_assembly_uses_semantic_candidates_when_inventory_is_uncovered(tmp_path) -> None:
    analysis = {
        "model": "gpt-5.5", "scenes": [],
    }
    candidates = [
        {"main_sku": "A", "country_available": None, "country_available_quantity": None},
        {"main_sku": "B", "country_available": None, "country_available_quantity": None},
    ]
    retrieval = {
        "model": "gpt-5.5", "country": "BR", "inventory_coverage": "unavailable",
        "scenes": [{"scene_name": "场景", "products": [{
            "product_cn": "产品", "product_en": "Product",
            "canonical_cn": "产品", "canonical_en": "Product",
            "expanded_cn": [], "expanded_en": [],
            "global_candidates": candidates, "country_candidates": [],
        }]}],
    }
    rerank = {
        "model": "deepseek-flash", "scenes": [{"scene_name": "场景", "products": [{
            "product_cn": "产品", "product_en": "Product",
            "ranked_candidates": [
                {"main_sku": "B", "relevance": 3, "reason": "同类"},
                {"main_sku": "A", "relevance": 2, "reason": "相关"},
            ],
        }]}],
    }
    paths = {name: tmp_path / f"{name}.json" for name in ("analysis", "retrieval", "rerank")}
    for name, value in (("analysis", analysis), ("retrieval", retrieval), ("rerank", rerank)):
        write(paths[name], value)
    output = tmp_path / "final.json"

    subprocess.run([
        sys.executable, str(ASSEMBLE), "--analysis", str(paths["analysis"]),
        "--retrieval", str(paths["retrieval"]), "--rerank", str(paths["rerank"]),
        "--output", str(output),
    ], check=True, capture_output=True, text=True, encoding="utf-8")

    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["inventory_coverage"] == "unavailable"
    assert [row["main_sku"] for row in result["recommended_main_skus"]] == ["B", "A"]
    assert result["scenes"][0]["products"][0]["country_recommendations"] == []
    assert all(row["country_available"] is None for row in result["recommended_main_skus"])
    assert _project_retrieval(result, is_final=True)["inventory_coverage"] == "unavailable"


def test_assembly_keeps_related_skus_without_country_stock_and_balances_products(tmp_path) -> None:
    analysis = {"model": "deepseek-flash", "scenes": []}
    products = []
    reranked = []
    for label, rows in (
        ("杯子", [("CUP-A", True), ("CUP-B", False), ("CUP-C", False)]),
        ("风扇", [("FAN-A", True), ("FAN-B", False), ("FAN-C", False)]),
    ):
        candidates = [
            {"main_sku": sku, "country_available": available,
             "country_available_quantity": 1 if available else None}
            for sku, available in rows
        ]
        products.append({
            "product_cn": label, "product_en": label,
            "canonical_cn": label, "canonical_en": label,
            "expanded_cn": [], "expanded_en": [],
            "global_candidates": candidates, "country_candidates": candidates[:1],
        })
        reranked.append({
            "product_cn": label, "product_en": label,
            "ranked_candidates": [
                {"main_sku": sku, "relevance": 3, "reason": "同类"} for sku, _ in rows
            ],
        })
    retrieval = {
        "model": "deepseek-flash", "country": "PH", "inventory_coverage": "available",
        "scenes": [{"scene_name": "日常", "products": products}],
    }
    rerank = {
        "model": "deepseek-flash",
        "scenes": [{"scene_name": "日常", "products": reranked}],
    }
    paths = {name: tmp_path / f"{name}.json" for name in ("analysis", "retrieval", "rerank")}
    for name, value in (("analysis", analysis), ("retrieval", retrieval), ("rerank", rerank)):
        write(paths[name], value)
    output = tmp_path / "final.json"

    subprocess.run([
        sys.executable, str(ASSEMBLE), "--analysis", str(paths["analysis"]),
        "--retrieval", str(paths["retrieval"]), "--rerank", str(paths["rerank"]),
        "--output", str(output), "--max-recommendations", "4",
    ], check=True, capture_output=True, text=True, encoding="utf-8")

    result = json.loads(output.read_text(encoding="utf-8"))
    assert [row["main_sku"] for row in result["recommended_main_skus"]] == [
        "CUP-A", "FAN-A", "CUP-B", "FAN-B",
    ]
    assert result["scenes"][0]["products"][0]["country_recommendations"][0]["main_sku"] == "CUP-A"
    assert result["recommended_main_skus"][2]["country_available"] is False


def test_assembly_excludes_scene_only_adjacency_by_default(tmp_path) -> None:
    analysis = {"model": "deepseek-flash", "scenes": []}
    candidates = [
        {"main_sku": "CUP", "country_available": True, "country_available_quantity": 1},
        {"main_sku": "COASTER", "country_available": True, "country_available_quantity": 1},
    ]
    retrieval = {
        "model": "deepseek-flash", "country": "PH", "inventory_coverage": "available",
        "scenes": [{"scene_name": "饮水", "products": [{
            "product_cn": "杯子", "product_en": "Cup",
            "canonical_cn": "杯子", "canonical_en": "Cup",
            "expanded_cn": [], "expanded_en": [],
            "global_candidates": candidates, "country_candidates": candidates,
        }]}],
    }
    rerank = {
        "model": "deepseek-flash", "scenes": [{"scene_name": "饮水", "products": [{
            "product_cn": "杯子", "product_en": "Cup",
            "ranked_candidates": [
                {"main_sku": "CUP", "relevance": 3, "reason": "同类"},
                {"main_sku": "COASTER", "relevance": 1, "reason": "搭配品"},
            ],
        }]}],
    }
    paths = {name: tmp_path / f"{name}.json" for name in ("analysis", "retrieval", "rerank")}
    for name, value in (("analysis", analysis), ("retrieval", retrieval), ("rerank", rerank)):
        write(paths[name], value)
    output = tmp_path / "final.json"

    subprocess.run([
        sys.executable, str(ASSEMBLE), "--analysis", str(paths["analysis"]),
        "--retrieval", str(paths["retrieval"]), "--rerank", str(paths["rerank"]),
        "--output", str(output),
    ], check=True, capture_output=True, text=True, encoding="utf-8")

    result = json.loads(output.read_text(encoding="utf-8"))
    assert [row["main_sku"] for row in result["recommended_main_skus"]] == ["CUP"]
    assert result["audit"]["minimum_relevance"] == 2
