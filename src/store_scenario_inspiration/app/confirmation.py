"""One persistent review gate between recognition and store analysis.

This module never calls a model or changes retrieval policy. Confirmation covers
exactly the current stored facts. Missing, corrupt or stale confirmations fail
closed; existing historical results remain on disk and are labelled separately.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile

INITIAL_STAGES = ("recognize", "clues")
ANALYSIS_STAGES = ("synthesis", "scenes", "products", "expand", "retrieval", "rerank")
SOURCE_FILES = ("store.json", "sample_store.json", "clues.json", "analysis_input.json", "exclusions.json")
CONFIRMATION_FILE = "review_confirmation.json"
SCHEMA = "store-review-confirmation-v1"


class ConfirmationRequired(ValueError):
    """A request would run analysis with unreviewed or changed information."""


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("店铺资料格式无效，请重新识别。")
    return value


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         delete=False, suffix=".tmp") as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, allow_nan=False)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _sources(base: Path) -> dict:
    required = SOURCE_FILES[:-1]
    if any(not (base / name).is_file() for name in required):
        raise ConfirmationRequired("请先完成店铺资料识别。")
    return {name: _read(base / name) if (base / name).is_file() else None
            for name in SOURCE_FILES}


def review_version(base: Path) -> str:
    # API writes also use the same store lease. The second read catches common
    # external changes; this is not a distributed/multi-file database transaction.
    first, second = _sources(base), _sources(base)
    if _digest(first) != _digest(second):
        raise ConfirmationRequired("资料正在更新，请稍后重新查看。")
    return _digest(second)


def review_status(base: Path) -> dict:
    try:
        version = review_version(base)
    except (OSError, ValueError, TypeError):
        return {"state": "not_ready", "confirmed": False, "version": None}
    try:
        saved = _read(base / CONFIRMATION_FILE)
    except (OSError, ValueError, TypeError):
        saved = {}
    confirmed = (saved.get("schema") == SCHEMA and saved.get("version") == version
                 and isinstance(saved.get("confirmed_at"), str)
                 and bool(saved.get("confirmed_at")))
    return {"state": "confirmed" if confirmed else "awaiting_confirmation",
            "confirmed": confirmed, "version": version,
            "confirmed_at": saved.get("confirmed_at") if confirmed else None}


def review_details(base: Path) -> dict:
    status = review_status(base)
    if status["version"] is None:
        return {**status, "business_context": None, "products": []}
    sample = _read(base / "sample_store.json")
    clues = _read(base / "clues.json")
    result = {**status, "business_context": sample.get("business_context"),
              "products": clues.get("entries") or []}
    if review_version(base) != status["version"]:
        raise ConfirmationRequired("资料已更新，请刷新后确认。")
    return result


def approve_review(base: Path, expected_version: str) -> dict:
    """Called under the SAME store lease as jobs and source-edit endpoints."""
    if not isinstance(expected_version, str) or not expected_version:
        raise ConfirmationRequired("缺少资料版本，请刷新后确认。")
    version = review_version(base)
    if version != expected_version:
        raise ConfirmationRequired("资料已变化，请查看最新信息后再确认。")
    source = _read(base / "analysis_input.json")
    if not source.get("observed_product_clues"):
        raise ConfirmationRequired("请至少保留一件参与分析的商品。")
    status = review_status(base)
    if status["confirmed"]:
        return status  # repeated confirmation of the same facts is idempotent
    _atomic(base / CONFIRMATION_FILE,
            {"schema": SCHEMA, "version": version,
             "confirmed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return review_status(base)


def invalidate_review(base: Path) -> None:
    (base / CONFIRMATION_FILE).unlink(missing_ok=True)


def require_confirmation(base: Path, expected_version: str | None = None) -> str:
    state = review_status(base)
    if not state["confirmed"]:
        raise ConfirmationRequired("请先确认店铺商品与经营数据，再开始分析。")
    if expected_version is not None and state["version"] != expected_version:
        raise ConfirmationRequired("任务使用的资料已变化，请重新确认后分析。")
    return state["version"]


def choose_stages(stages: tuple[str, ...] | None, order: tuple[str, ...]) -> tuple[str, ...]:
    requested = INITIAL_STAGES if not stages else tuple(stages)
    if set(requested) - set(order):
        raise ValueError("存在未知处理步骤。")
    if "recognize" in requested and set(requested) & set(ANALYSIS_STAGES):
        raise ConfirmationRequired("识别与分析之间必须确认，不能一次启动全部步骤。")
    return tuple(stage for stage in order if stage in requested)


def authorize_start(base: Path, chosen: tuple[str, ...]) -> str | None:
    """Called while holding the job lease, before changing stored params."""
    if set(chosen) & set(ANALYSIS_STAGES):
        return require_confirmation(base)
    if "recognize" in chosen:
        invalidate_review(base)  # even a failed recognition requires review again
    return None
