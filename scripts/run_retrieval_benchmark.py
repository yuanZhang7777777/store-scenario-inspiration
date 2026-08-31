"""Run the catalog retrieval benchmark once a search dependency is supplied."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from store_scenario_inspiration.catalog.benchmark import load_benchmark, run_benchmark
from store_scenario_inspiration.catalog.models import SearchHit


SearchFunction = Callable[[str, int], Sequence[SearchHit]]
DEFAULT_BENCHMARK_PATH = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "retrieval-benchmark-v1.json"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK_PATH)
    parser.add_argument("--top-k", type=int, default=5)
    return parser


def main(argv: Sequence[str] | None = None, search_fn: SearchFunction | None = None) -> int:
    """Print a benchmark report and return its command-line status code.

    Task 6 wires a catalog store into this injection boundary.  Until then, the
    script remains runnable and explicitly reports the missing dependency.
    """

    args = _parser().parse_args(argv)
    if search_fn is None:
        print("search dependency is not wired; supply a search_fn programmatically", file=sys.stderr)
        return 2

    report = run_benchmark(search_fn, load_benchmark(args.benchmark), top_k=args.top_k)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 1 if report.enforced and report.passes_threshold is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
