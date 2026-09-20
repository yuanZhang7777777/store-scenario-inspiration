"""Run resumable DeepSeek store-analysis batches with shared local retrieval."""

from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
import json
import os
from pathlib import Path
import subprocess
import sys


SCRIPTS = Path(__file__).resolve().parent
COUNTRIES = {"PH", "TH", "VN", "MY"}


def run(command: list[str]) -> None:
    subprocess.run(command, check=True, env=os.environ.copy())


def store_paths(root: Path, store_id: str) -> dict[str, Path]:
    base = root / "stores" / store_id
    return {
        "sample": base / "sample_store.json",
        "analysis": base / "deepseek_analysis.json",
        "expansions": base / "deepseek_expansions.json",
        "retrieval": base / "retrieval" / "deepseek_retrieval.json",
        "rerank": base / "deepseek_rerank.json",
        "final": base / "deepseek_final.json",
        "cache": base / "cache" / "deepseek",
    }


def model_stages(paths: dict[str, Path], *, refresh: bool = False) -> None:
    if refresh or not paths["analysis"].exists():
        run([sys.executable, str(SCRIPTS / "deepseek_store_analysis.py"),
             "--input", str(paths["sample"]), "--output", str(paths["analysis"])])
    if refresh or not paths["expansions"].exists():
        run([sys.executable, str(SCRIPTS / "deepseek_store_analysis.py"), "--expand",
             "--input", str(paths["analysis"]), "--output", str(paths["expansions"])])


def rerank_and_assemble(paths: dict[str, Path], *, refresh: bool = False) -> None:
    if refresh or not paths["rerank"].exists():
        run([sys.executable, str(SCRIPTS / "deepseek_rerank.py"),
             "--source", str(paths["retrieval"]), "--output", str(paths["rerank"]),
             "--cache-dir", str(paths["cache"])])
    if refresh or not paths["final"].exists():
        run([sys.executable, str(SCRIPTS / "assemble_store_pilot.py"),
             "--analysis", str(paths["analysis"]), "--retrieval", str(paths["retrieval"]),
             "--rerank", str(paths["rerank"]), "--output", str(paths["final"])])


def parallel(items: list[tuple[str, dict[str, Path]]], fn, workers: int) -> None:
    stage_name = getattr(fn, "__name__", fn.func.__name__)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, paths): store_id for store_id, paths in items}
        for future in as_completed(futures):
            store_id = futures[future]
            future.result()
            print(json.dumps({"store": store_id, "stage": stage_name, "status": "ready"},
                             ensure_ascii=False), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, nargs="+")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--refresh", action="store_true",
                        help="Regenerate DeepSeek analysis, retrieval, rerank and final files")
    parser.add_argument("--refresh-model", action="store_true")
    parser.add_argument("--refresh-retrieval", action="store_true")
    parser.add_argument("--refresh-rerank", action="store_true")
    args = parser.parse_args()
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        parser.error("DEEPSEEK_API_KEY is required")

    batches: list[tuple[Path, list[tuple[str, str, dict[str, Path]]]]] = []
    for manifest_path in args.manifest:
        manifest_path = manifest_path.resolve()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        rows = []
        for entry in manifest["stores"]:
            store_id = entry["id"]
            country = str(entry.get("country", "")).strip().upper()
            if country not in COUNTRIES:
                raise ValueError(
                    f"unsupported country in {manifest_path}: {country or '<blank>'}; "
                    "expected PH/TH/VN/MY"
                )
            rows.append((store_id, country, store_paths(manifest_path.parent, store_id)))
        batches.append((manifest_path.parent, rows))

    refresh_model = args.refresh or args.refresh_model
    refresh_retrieval = args.refresh or args.refresh_retrieval
    refresh_rerank = args.refresh or args.refresh_rerank
    all_items = [(store_id, paths) for _, rows in batches for store_id, _, paths in rows]
    parallel(all_items, partial(model_stages, refresh=refresh_model), args.workers)

    # One retrieval process per batch and country loads the 1024-d models once.
    for root, rows in batches:
        by_country: dict[str, list[tuple[str, dict[str, Path]]]] = defaultdict(list)
        for store_id, country, paths in rows:
            if refresh_retrieval or not paths["retrieval"].exists():
                by_country[country].append((store_id, paths))
        for country, country_rows in by_country.items():
            output_dir = root / "retrieval-cache" / "deepseek" / country.lower()
            command = [sys.executable, str(SCRIPTS / "run_store_pilot.py"),
                       "--country", country, "--output-dir", str(output_dir)]
            for _, paths in country_rows:
                command.extend(("--expansions", str(paths["expansions"])))
            for _, paths in country_rows:
                command.extend(("--output", str(paths["retrieval"])))
            run(command)
            print(json.dumps({"batch": root.name, "country": country,
                              "stage": "retrieval", "stores": len(country_rows)},
                             ensure_ascii=False), flush=True)

    parallel(all_items, partial(
        rerank_and_assemble, refresh=refresh_rerank or refresh_retrieval or refresh_model
    ), args.workers)


if __name__ == "__main__":
    main()
