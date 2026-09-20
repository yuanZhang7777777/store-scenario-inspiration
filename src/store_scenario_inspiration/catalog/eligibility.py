"""Confirmed family and exact child-SKU exclusions; raw data remains auditable."""

import json
from pathlib import Path

from .normalize import normalize_compare

# 2026-09-09: user confirmed with colleagues that ALL children of 1A0000 are excluded.
EXCLUDED_MAIN_SKUS = frozenset({"1A0000"})


def _load_excluded_skus() -> frozenset[str]:
    path = Path(__file__).with_name("confirmed_excluded_skus.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        skus = payload["child_skus"]
        if not isinstance(skus, list) or not all(
            isinstance(sku, str) and normalize_compare(sku) for sku in skus
        ):
            raise ValueError("child_skus must be a list of non-empty strings")
        return frozenset(normalize_compare(sku) for sku in skus)
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as error:
        raise ValueError(f"confirmed SKU exclusions are invalid: {path}") from error


# Exact identifiers, not name rules or prefixes; case remains significant.
EXCLUDED_CHILD_SKUS = _load_excluded_skus()


def is_excluded_main_sku(main_sku: str) -> bool:
    return normalize_compare(main_sku).upper() in EXCLUDED_MAIN_SKUS


def is_excluded_sku(sku: str) -> bool:
    return normalize_compare(sku) in EXCLUDED_CHILD_SKUS


def is_excluded_product(main_sku: str, sku: str) -> bool:
    return is_excluded_main_sku(main_sku) or is_excluded_sku(sku)
