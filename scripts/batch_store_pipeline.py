"""Plan, validate, and resume the per-store analysis pipeline.

Directory contract (paths are relative to the manifest directory):

  manifest.json
  stores/<store-id>/sample_store.json
  stores/<store-id>/direction.json          (local, always regenerated)
  stores/<store-id>/analysis_input.json     (local, direction-filtered)
  stores/<store-id>/direction_overrides.json (optional operator corrections)
  stores/<store-id>/<model>_analysis.json
  stores/<store-id>/<model>_expansions.json
  stores/<store-id>/retrieval/<model>_retrieval.json
  stores/<store-id>/<model>_rerank.json
  stores/<store-id>/<model>_final.json

The model writes analysis/expansion/rerank JSON into those slots. This script
validates them and can run the API-free retrieval/assembly stages. Existing
outputs are checkpoints and are skipped unless --force is used.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys


SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from direction import BUCKET_MAIN  # noqa: E402


SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
NO_DIRECTION_HINT = (
    "没有任何商品被确认为主营方向，无法生成场景。"
    "先重跑识别以拿到 role 标注，或在 direction_overrides.json 里手动指定。"
)
ANALYSIS_FIELDS = {
    "model", "manager_summary", "store_profile", "audiences",
    "current_product_structure", "future_product_structure",
    "operation_strategy", "scenes",
}


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def validate_store(path: Path) -> dict:
    value = read_json(path)
    store = value.get("store")
    if not isinstance(store, dict) or not str(store.get("country", "")).strip():
        raise ValueError(f"store.country required: {path}")
    clues = value.get("observed_product_clues")
    if not isinstance(clues, list):
        raise ValueError(f"observed_product_clues list required: {path}")
    return value


def validate_analysis(path: Path) -> dict:
    value = read_json(path)
    if not ANALYSIS_FIELDS <= set(value) or not isinstance(value.get("scenes"), list):
        raise ValueError(f"invalid analysis: {path}")
    for scene in value["scenes"]:
        if not isinstance(scene, dict) or not isinstance(scene.get("product_needs"), list):
            raise ValueError(f"invalid analysis scene: {path}")
    return value


def validate_expansions(path: Path) -> dict:
    value = read_json(path)
    if not isinstance(value.get("model"), str) or not isinstance(value.get("scenes"), list):
        raise ValueError(f"invalid expansions: {path}")
    required = {"product_cn", "product_en", "canonical_cn", "canonical_en",
                "expanded_cn", "expanded_en"}
    for scene in value["scenes"]:
        products = scene.get("products", scene.get("product_needs"))
        if not isinstance(products, list):
            raise ValueError(f"invalid expansion scene: {path}")
        for product in products:
            if not required <= set(product):
                raise ValueError(f"invalid expanded product: {path}")
            if any(not isinstance(product[field], list) or len(product[field]) > 4
                   for field in ("expanded_cn", "expanded_en")):
                raise ValueError(f"invalid expansion terms: {path}")
    return value


def validate_rerank(path: Path) -> dict:
    value = read_json(path)
    if not isinstance(value.get("scenes"), list):
        raise ValueError(f"invalid rerank: {path}")
    return value


def command_text(command: list[str]) -> str:
    return subprocess.list2cmdline(command)


def run(command: list[str], enabled: bool) -> None:
    if enabled:
        subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")


def local_command(script: str, *args: object) -> list[str]:
    return [sys.executable, str(SCRIPTS / script), *map(str, args)]


def artifact_paths(root: Path, store_id: str, model: str) -> dict[str, Path]:
    base = root / "stores" / store_id
    return {
        "store": base / "sample_store.json",
        "direction": base / "direction.json",
        "analysis_input": base / "analysis_input.json",
        "analysis": base / f"{model}_analysis.json",
        "expansions": base / f"{model}_expansions.json",
        "retrieval": base / "retrieval" / f"{model}_retrieval.json",
        "rerank": base / f"{model}_rerank.json",
        "final": base / f"{model}_final.json",
        "cache": base / "cache" / model,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--model", action="append", help="Run only this manifest model alias")
    parser.add_argument("--run-local", action="store_true",
                        help="Execute retrieval/assembly; API stages remain planned only")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--asset-db", type=Path)
    parser.add_argument("--stock", type=Path)
    parser.add_argument("--qdrant")
    parser.add_argument("--collection")
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 10:
        parser.error("--limit must be 1..10")

    manifest_path = args.manifest.resolve()
    root = manifest_path.parent
    manifest = read_json(manifest_path)
    stores = manifest.get("stores")
    models = args.model or manifest.get("models", [])
    if not isinstance(stores, list) or not stores or not isinstance(models, list) or not models:
        raise ValueError("manifest requires non-empty stores and models lists")
    if len(set(models)) != len(models) or any(not SAFE_ID.fullmatch(str(model)) for model in models):
        raise ValueError("model aliases must be unique safe IDs")

    report = []
    seen_stores = set()
    for entry in stores[:args.limit]:
        store_id = str(entry.get("id", "")) if isinstance(entry, dict) else ""
        if not SAFE_ID.fullmatch(store_id) or store_id in seen_stores:
            raise ValueError(f"invalid or duplicate store id: {store_id!r}")
        seen_stores.add(store_id)
        for model in models:
            paths = artifact_paths(root, store_id, model)
            store = validate_store(paths["store"])
            country = str(entry.get("country") or store["store"]["country"]).strip()
            status = {"store": store_id, "model": model, "country": country, "stages": {}}

            direction_command = local_command("direction.py", paths["store"].parent)
            status["direction_command"] = command_text(direction_command)
            if not args.validate_only:
                run(direction_command, enabled=True)
            status["stages"]["direction"] = "ready" if paths["direction"].exists() else "failed"
            confirmed = (read_json(paths["direction"]).get("counts", {}).get(BUCKET_MAIN, 0)
                         if paths["direction"].exists() else 0)

            if paths["analysis"].exists():
                validate_analysis(paths["analysis"])
                status["stages"]["analysis"] = "ready"
            elif not confirmed:
                status["stages"]["analysis"] = "blocked_by_direction"
                status["analysis_hint"] = NO_DIRECTION_HINT
            elif paths["analysis_input"].exists():
                status["stages"]["analysis"] = "waiting_model"
                if model == "deepseek":
                    status["analysis_command"] = command_text(local_command(
                        "deepseek_store_analysis.py", "--input", paths["analysis_input"],
                        "--output", paths["analysis"],
                    ))
            else:
                status["stages"]["analysis"] = "blocked_by_direction"

            if paths["expansions"].exists():
                validate_expansions(paths["expansions"])
                status["stages"]["expansions"] = "ready"
            elif paths["analysis"].exists():
                status["stages"]["expansions"] = "waiting_model"
                if model == "deepseek":
                    status["expansion_command"] = command_text(local_command(
                        "deepseek_store_analysis.py", "--expand", "--input", paths["analysis"],
                        "--output", paths["expansions"],
                    ))
            else:
                status["stages"]["expansions"] = "blocked_by_analysis"

            retrieval_ready = paths["retrieval"].exists() and not args.force
            if retrieval_ready:
                read_json(paths["retrieval"])
                status["stages"]["retrieval"] = "ready"
            elif paths["expansions"].exists():
                command = local_command(
                    "run_store_pilot.py", "--expansions", paths["expansions"],
                    "--country", country, "--output-dir", paths["retrieval"].parent,
                    "--output", paths["retrieval"],
                )
                for flag, value in (("--asset-db", args.asset_db), ("--stock", args.stock),
                                    ("--qdrant", args.qdrant), ("--collection", args.collection)):
                    if value is not None:
                        command.extend((flag, str(value)))
                status["retrieval_command"] = command_text(command)
                if not args.validate_only:
                    run(command, args.run_local)
                status["stages"]["retrieval"] = "ready" if paths["retrieval"].exists() else "planned"
            else:
                status["stages"]["retrieval"] = "blocked_by_expansions"

            if paths["rerank"].exists():
                validate_rerank(paths["rerank"])
                status["stages"]["rerank"] = "ready"
            elif paths["retrieval"].exists():
                status["stages"]["rerank"] = "waiting_model"
                status["rerank_command"] = command_text(local_command(
                    "deepseek_rerank.py", "--source", paths["retrieval"],
                    "--output", paths["rerank"], "--cache-dir", paths["cache"],
                ))
            else:
                status["stages"]["rerank"] = "blocked_by_retrieval"

            final_ready = paths["final"].exists() and not args.force
            if final_ready:
                read_json(paths["final"])
                status["stages"]["final"] = "ready"
            elif all(paths[name].exists() for name in ("analysis", "retrieval", "rerank")):
                command = local_command(
                    "assemble_store_pilot.py", "--analysis", paths["analysis"],
                    "--retrieval", paths["retrieval"], "--rerank", paths["rerank"],
                    "--output", paths["final"],
                )
                status["assemble_command"] = command_text(command)
                if not args.validate_only:
                    run(command, args.run_local)
                status["stages"]["final"] = "ready" if paths["final"].exists() else "planned"
            else:
                status["stages"]["final"] = "blocked"
            report.append(status)

    print(json.dumps({"root": str(root), "stores": len(seen_stores), "runs": report},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
