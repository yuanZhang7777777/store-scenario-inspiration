"""Run the store pipeline in the background and report what it is doing.

Two of the three stages call DeepSeek and take tens of seconds each, so they
cannot run inside a request. Progress is plain polled state rather than a
stream: with this few stages a two-second poll reads the same to the operator
and keeps the client free of reconnect logic.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
import uuid

from deepseek_prepare_store_batch import build_sample, recognize
from deepseek_store_analysis import analyze
from direction import (
    apply_overrides,
    bucket_clues,
    build_analysis_input,
    load_overrides,
    write_json,
)

from .stores import Workspace, read_json


PENDING = "pending"
RUNNING = "running"
READY = "ready"
FAILED = "failed"
CANCELLED = "cancelled"

RECOGNIZE = "recognize"
DIRECTION = "direction"
ANALYSIS = "analysis"
STAGE_ORDER = (RECOGNIZE, DIRECTION, ANALYSIS)
STAGE_LABELS = {
    RECOGNIZE: "识别截图里的商品",
    DIRECTION: "整理店铺方向",
    ANALYSIS: "生成使用场景",
}


@dataclass
class Stage:
    name: str
    status: str = PENDING
    detail: str = ""
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


@dataclass
class Job:
    id: str
    store_id: str
    stages: list[Stage] = field(default_factory=list)
    status: str = PENDING
    log: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    created_at: str = ""
    finished_at: str | None = None
    cancelled: bool = False


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobManager:
    def __init__(self, workspace: Workspace, settings, *, workers: int = 2):
        self.workspace = workspace
        self.settings = settings
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=workers)

    def start(self, store_id: str) -> dict:
        self.workspace.dir(store_id)
        job = Job(id=uuid.uuid4().hex[:12], store_id=store_id, created_at=now(),
                  stages=[Stage(name) for name in STAGE_ORDER])
        with self._lock:
            self._jobs[job.id] = job
        self._pool.submit(self._run, job)
        return self.snapshot(job.id)

    def snapshot(self, job_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            return {
                "id": job.id,
                "store_id": job.store_id,
                "status": job.status,
                "created_at": job.created_at,
                "finished_at": job.finished_at,
                "usage": dict(job.usage),
                "log": list(job.log),
                "stages": [
                    {"name": stage.name, "label": STAGE_LABELS[stage.name],
                     "status": stage.status, "detail": stage.detail, "error": stage.error,
                     "started_at": stage.started_at, "finished_at": stage.finished_at}
                    for stage in job.stages
                ],
            }

    def cancel(self, job_id: str) -> dict:
        """Stop before the next stage. A stage already talking to DeepSeek runs on."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            job.cancelled = True
        return self.snapshot(job_id)

    def newest(self, store_id: str) -> dict | None:
        with self._lock:
            matching = [job for job in self._jobs.values() if job.store_id == store_id]
        if not matching:
            return None
        return self.snapshot(max(matching, key=lambda job: job.created_at).id)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _run(self, job: Job) -> None:
        with self._lock:
            job.status = RUNNING
        for stage in job.stages:
            if job.cancelled:
                break
            self._run_stage(job, stage)
            if stage.status == FAILED:
                break
        with self._lock:
            if job.cancelled:
                job.status = CANCELLED
            elif any(stage.status == FAILED for stage in job.stages):
                job.status = FAILED
            else:
                job.status = READY
            job.finished_at = now()

    def _run_stage(self, job: Job, stage: Stage) -> None:
        with self._lock:
            stage.status = RUNNING
            stage.started_at = now()
            job.log.append({"at": stage.started_at, "stage": stage.name,
                            "message": f"开始{STAGE_LABELS[stage.name]}"})
        try:
            stage.detail = STAGE_RUNNERS[stage.name](self.workspace, self.settings, job.store_id)
        except Exception as error:  # noqa: BLE001 — reported to the operator, not swallowed
            with self._lock:
                stage.status = FAILED
                stage.error = f"{type(error).__name__}: {error}"
                stage.finished_at = now()
                job.log.append({"at": stage.finished_at, "stage": stage.name,
                                "message": f"失败：{stage.error}"})
            return
        with self._lock:
            stage.status = READY
            stage.finished_at = now()
            job.log.append({"at": stage.finished_at, "stage": stage.name,
                            "message": stage.detail})
            receipt = self.workspace.path(job.store_id, "receipt_analysis.json")
            if stage.name == ANALYSIS and receipt.is_file():
                job.usage = read_json(receipt).get("usage", {})


def run_recognize(workspace: Workspace, settings, store_id: str) -> str:
    entry = workspace.entry(store_id)
    result, receipt = recognize(entry, settings.api_key)
    base = workspace.dir(store_id)
    write_json(base / "deepseek_vision.json", receipt)
    write_json(base / "sample_store.json",
               build_sample(entry, result, workspace.path(store_id, "store.json")))
    return f"识别出 {sum(len(image['products']) for image in result['images'])} 个商品"


def run_direction(workspace: Workspace, settings, store_id: str) -> str:
    base = workspace.dir(store_id)
    sample = read_json(base / "sample_store.json")
    review = apply_overrides(
        bucket_clues(sample.get("observed_product_clues") or []),
        load_overrides(base / "direction_overrides.json"),
    )
    write_json(base / "direction.json", review)
    write_json(base / "analysis_input.json", build_analysis_input(sample, review))
    counts = review["counts"]
    return f"主营 {counts['main']} 条，存疑 {counts['unsure']} 条，建议排除 {counts['excluded']} 条"


def run_analysis(workspace: Workspace, settings, store_id: str) -> str:
    base = workspace.dir(store_id)
    result, receipt = analyze(read_json(base / "analysis_input.json"), settings.api_key)
    write_json(base / "deepseek_analysis.json", result)
    write_json(base / "receipt_analysis.json", receipt)
    return f"生成 {len(result['scenes'])} 个场景"


STAGE_RUNNERS = {
    RECOGNIZE: run_recognize,
    DIRECTION: run_direction,
    ANALYSIS: run_analysis,
}
