"""Run the catalog retrieval benchmark once a search dependency is supplied."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from store_scenario_inspiration.catalog.benchmark import load_benchmark, run_benchmark
from store_scenario_inspiration.catalog.models import SearchHit


SearchFunction = Callable[[str, int], Sequence[SearchHit]]
DEFAULT_BENCHMARK_PATH = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "retrieval-benchmark-v1.json"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK_PATH)
    parser.add_argument("--index-root", type=Path)
    return parser


def main(argv: Sequence[str] | None = None, search_fn: SearchFunction | None = None) -> int:
    """Print a benchmark report and return its command-line status code.

    An injected search function retains the Task 5 testing boundary. Otherwise,
    ``--index-root`` opens the active SQLite version for a keyword-only baseline.
    """

    args = _parser().parse_args(argv)
    if search_fn is None and args.index_root is None:
        print("search dependency is not wired; supply a search_fn programmatically", file=sys.stderr)
        return 2

    try:
        items = load_benchmark(args.benchmark)
        if search_fn is not None:
            report = run_benchmark(search_fn, items)
        else:
            from store_scenario_inspiration.catalog.retrieval import (
                HybridRetriever,
                RetrievalQuery,
            )
            from store_scenario_inspiration.catalog.storage import CatalogStore
            from store_scenario_inspiration.catalog.versioning import CatalogIndexManager

            manager = CatalogIndexManager(args.index_root)
            store_path = manager.active_store_path()
            if store_path is None:
                print("active catalog index is not available", file=sys.stderr)
                return 2
            with CatalogStore.open_readonly(store_path) as store:
                retriever = HybridRetriever(store)

                def active_search(query: str, top_k: int) -> Sequence[SearchHit]:
                    return retriever.search(
                        RetrievalQuery(text=query), query_vector=None, limit=top_k
                    )

                report = run_benchmark(active_search, items)
    except Exception as error:
        print(f"benchmark error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 1 if report.enforced and report.passes_threshold is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
