"""Apply one store's exclusion list and write the scene generator's input.

The rules are in ``store_scenario_inspiration.pipeline.clues``; this is the
command line over one store directory.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from store_scenario_inspiration.pipeline.artifacts import read_json, write_json
from store_scenario_inspiration.pipeline.clues import (
    build_analysis_input,
    load_exclusions,
    review_clues,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("store_dir", type=Path, help="Directory holding sample_store.json")
    parser.add_argument("--exclusions", type=Path,
                        help="Defaults to <store_dir>/exclusions.json")
    parser.add_argument("--direction", help="Operator's name for the store's direction")
    args = parser.parse_args()

    store_dir = args.store_dir.resolve()
    sample = read_json(store_dir / "sample_store.json")
    exclusions_path = args.exclusions or store_dir / "exclusions.json"
    review = review_clues(sample.get("observed_product_clues") or [],
                          load_exclusions(exclusions_path))

    clues_path = store_dir / "clues.json"
    input_path = store_dir / "analysis_input.json"
    write_json(clues_path, review)
    write_json(input_path, build_analysis_input(sample, review, direction=args.direction))
    print(json.dumps({
        "store_dir": str(store_dir),
        "clues": str(clues_path),
        "analysis_input": str(input_path),
        "counts": review["counts"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
