"""Build a DeepSeek-ready store batch from local screenshot exports.

The vision call and the sample it produces are in
``store_scenario_inspiration.pipeline.recognize``; this drives them over a batch
root and skips the stores already recognized.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import getpass
import json
import os
from pathlib import Path

from store_scenario_inspiration.pipeline.artifacts import read_json, write_json
from store_scenario_inspiration.pipeline.recognize import (
    COUNTRIES,
    build_sample,
    recognize,
    store_id,
    validate_existing,
    validate_receipt,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Batch root containing input/stores.json")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")

    root = args.root.resolve()
    input_path = root / "input" / "stores.json"
    raw = read_json(input_path)
    if not isinstance(raw, list):
        raise ValueError("input/stores.json must contain a list")

    entries = []
    skipped_country = 0
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("every store must be an object")
        country = str(entry.get("country", "")).upper().strip()
        if country not in COUNTRIES:
            skipped_country += 1
            continue
        entry = {**entry, "country": country}
        if not isinstance(entry.get("images"), list) or not entry["images"]:
            continue
        entries.append(entry)
    ids = [store_id(entry) for entry in entries]
    if not ids:
        raise ValueError("no PH/TH/VN/MY stores with local images")
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate stable store id")

    pending = []
    for entry, identifier in zip(entries, ids):
        output = root / "stores" / identifier / "sample_store.json"
        receipt = output.with_name("deepseek_vision.json")
        filenames = [str(image.get("filename") or Path(str(image.get("local_path", ""))).name)
                     for image in entry["images"]]
        if output.exists() and receipt.exists() and not args.refresh:
            validate_existing(output, entry["country"])
            validate_receipt(receipt, filenames)
        else:
            pending.append((entry, identifier, output, receipt))

    if pending:
        key = os.environ.get("DEEPSEEK_API_KEY", "").strip() or getpass.getpass("DeepSeek API key: ").strip()
        if not key or "\n" in key or "\r" in key:
            raise ValueError("missing API key")

        def process(item: tuple[dict, str, Path, Path]) -> str:
            entry, identifier, output, receipt_path = item
            result, receipt = recognize(entry, key)
            write_json(receipt_path, receipt)
            write_json(output, build_sample(entry, result, input_path))
            return identifier

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(process, pending))

    manifest = {
        "models": ["deepseek"],
        "stores": [{"id": identifier, "country": entry["country"]}
                   for entry, identifier in zip(entries, ids)],
        "default_store_id": ids[0],
    }
    write_json(root / "manifest.json", manifest)
    print(json.dumps({
        "root": str(root), "stores": len(ids), "recognized": len(pending),
        "resumed": len(ids) - len(pending), "skipped_country": skipped_country,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
