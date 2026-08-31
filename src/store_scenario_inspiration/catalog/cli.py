"""Operator CLI for versioned catalog rebuilds and keyword retrieval."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
from pathlib import Path
import sys

from .models import BuildManifest
from .retrieval import HybridRetriever, RetrievalQuery
from .storage import CatalogStore
from .versioning import CatalogIndexError, CatalogIndexManager


DEFAULT_INDEX_ROOT = Path("var/catalog")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="store-catalog", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    rebuild = commands.add_parser("rebuild", help="build and activate a complete catalog")
    rebuild.add_argument("--source", type=Path, required=True)
    rebuild.add_argument("--sheet")
    rebuild.add_argument("--index-root", type=Path, default=DEFAULT_INDEX_ROOT)

    status = commands.add_parser("status", help="show the active catalog version")
    status.add_argument("--index-root", type=Path, default=DEFAULT_INDEX_ROOT)

    search = commands.add_parser("search", help="search the active keyword index")
    search.add_argument("--query", required=True)
    search.add_argument("--platform")
    search.add_argument("--top-k", type=int, default=5)
    search.add_argument("--index-root", type=Path, default=DEFAULT_INDEX_ROOT)

    rollback = commands.add_parser("rollback", help="swap active and previous versions")
    rollback.add_argument("--index-root", type=Path, default=DEFAULT_INDEX_ROOT)
    return parser


def _value(value: object) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _print_pairs(pairs: Sequence[tuple[str, object]]) -> None:
    for key, value in pairs:
        print(f"{key}={_value(value)}")


def _delta_metric(manager: CatalogIndexManager, manifest: BuildManifest, name: str) -> int | None:
    path = manager.index_root / "versions" / manifest.version_id / "delta.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        value = payload["metrics"][name]["absolute_delta"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise CatalogIndexError(f"catalog delta report is invalid: {path}") from error
    if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
        raise CatalogIndexError(f"catalog delta metric {name!r} is invalid: {path}")
    return value


def _print_active(
    manager: CatalogIndexManager,
    manifest: BuildManifest,
    *,
    skipped: bool | None = None,
    rolled_back: bool | None = None,
) -> None:
    quality = manager.active_quality()
    if quality is None:
        raise CatalogIndexError("active catalog quality report is unavailable")
    pairs: list[tuple[str, object]] = [
        ("active_version", manifest.version_id),
        ("source_sha256", manifest.source_sha256),
        ("source_rows", quality.source_row_count),
        ("documents", manifest.document_count),
        ("children", quality.child_count),
        ("document_delta", _delta_metric(manager, manifest, "document_count")),
        ("child_delta", _delta_metric(manager, manifest, "child_count")),
        ("quality_errors", len(quality.errors)),
        ("quality_warnings", len(quality.warnings)),
        (
            "warnings",
            json.dumps(quality.warnings, ensure_ascii=False, separators=(",", ":")),
        ),
        ("vector_status", manifest.vector_status),
    ]
    if skipped is not None:
        pairs.append(("skipped", skipped))
    if rolled_back is not None:
        pairs.append(("rolled_back", rolled_back))
    _print_pairs(pairs)


def _rebuild(args: argparse.Namespace) -> int:
    manager = CatalogIndexManager(args.index_root)
    manifest = manager.rebuild(args.source, sheet_name=args.sheet, provider=None)
    _print_active(manager, manifest, skipped=manager.last_rebuild_skipped)
    return 0


def _status(args: argparse.Namespace) -> int:
    manager = CatalogIndexManager(args.index_root)
    manifest = manager.active_manifest()
    if manifest is None:
        _print_pairs((("active_version", "none"),))
        return 0
    _print_active(manager, manifest)
    return 0


def _search(args: argparse.Namespace) -> int:
    if args.top_k <= 0:
        raise ValueError("top-k must be a positive integer")
    query = RetrievalQuery(text=args.query, platform=args.platform)
    manager = CatalogIndexManager(args.index_root)
    store_path = manager.active_store_path()
    if store_path is None:
        raise CatalogIndexError("no active catalog index")
    with CatalogStore.open_readonly(store_path) as store:
        hits = HybridRetriever(store).search(query, query_vector=None, limit=args.top_k)
    payload = {
        "query": query.text,
        "platform": query.platform,
        "top_k": args.top_k,
        "results": [
            {
                "main_sku": hit.main_sku,
                "score": hit.score,
                "sources": list(hit.sources),
                "eligible_child_skus": list(hit.eligible_child_skus),
                "warnings": list(hit.warnings),
            }
            for hit in hits
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


def _rollback(args: argparse.Namespace) -> int:
    manager = CatalogIndexManager(args.index_root)
    manifest = manager.rollback()
    _print_active(manager, manifest, rolled_back=True)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command, returning a process status without exposing tracebacks."""

    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return int(error.code)
    command = args.command
    try:
        if command == "rebuild":
            return _rebuild(args)
        if command == "status":
            return _status(args)
        if command == "search":
            return _search(args)
        if command == "rollback":
            return _rollback(args)
        raise ValueError(f"unknown command: {command}")
    except Exception as error:
        print(f"error={error}", file=sys.stderr)
        if command == "rebuild":
            print("previous index remains active", file=sys.stderr)
        return 1
