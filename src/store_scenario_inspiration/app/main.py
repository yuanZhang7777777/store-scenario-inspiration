"""HTTP surface for the store-scenario workspace.

Small on purpose: upload screenshots, run the pipeline, read back scenes and
the operator's corrections. Everything it returns is read straight off the store
directory, so the command line and the browser never disagree.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import io
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from direction import (
    BUCKET_EXCLUDED,
    BUCKET_ORDER,
    find_purity_violations,
    load_overrides,
    save_overrides,
    selected_clues,
)

from .config import Settings
from .jobs import JobManager, run_direction
from .scenes import rank_scenes
from .stores import Workspace, read_json


class Override(BaseModel):
    clue: str
    bucket: str


class Overrides(BaseModel):
    overrides: list[Override] = Field(default_factory=list)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    workspace = Workspace(settings.data_dir)
    jobs = JobManager(workspace, settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        jobs.shutdown()

    app = FastAPI(title="店铺场景灵感助手", lifespan=lifespan)
    app.state.settings = settings
    app.state.workspace = workspace
    app.state.jobs = jobs

    @app.get("/api/health")
    def health() -> dict:
        return {
            "data_dir": str(settings.data_dir),
            "countries": list(settings.countries),
            "api_key_configured": bool(settings.api_key),
        }

    @app.post("/api/stores")
    async def create_store(
        store_name: str = Form(...),
        country: str = Form(...),
        files: list[UploadFile] = File(...),
    ) -> dict:
        if country.strip().upper() not in settings.countries:
            raise HTTPException(400, f"unsupported country: {country!r}")
        uploads = []
        for upload in files:
            payload = await upload.read()
            if not payload:
                raise HTTPException(400, f"empty upload: {upload.filename}")
            if len(payload) > settings.max_upload_bytes:
                raise HTTPException(413, f"too large: {upload.filename}")
            if not (upload.content_type or "").startswith("image/"):
                raise HTTPException(400, f"not an image: {upload.filename}")
            uploads.append((upload.filename or "screenshot.png", io.BytesIO(payload)))
        try:
            return workspace.create(store_name, country, uploads)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.get("/api/stores")
    def list_stores() -> dict:
        return {"stores": workspace.listing()}

    @app.get("/api/stores/{store_id}")
    def get_store(store_id: str) -> dict:
        return _store_payload(workspace, jobs, store_id)

    @app.get("/api/stores/{store_id}/images/{filename}")
    def get_image(store_id: str, filename: str) -> FileResponse:
        path = workspace.images_dir(store_id) / Path(filename).name
        if not path.is_file():
            raise HTTPException(404, "no such screenshot")
        return FileResponse(path)

    @app.get("/api/stores/{store_id}/direction")
    def get_direction(store_id: str) -> dict:
        review = _require(workspace, store_id, "direction.json")
        review["overrides"] = load_overrides(workspace.path(store_id, "direction_overrides.json"))
        return review

    @app.put("/api/stores/{store_id}/direction")
    def put_direction(store_id: str, body: Overrides) -> dict:
        """Record the operator's corrections, then rebuild the filtered input.

        Rebuilding is local and free, so the next scene generation sees the
        correction without a second DeepSeek call.
        """
        review = _require(workspace, store_id, "direction.json")
        known = {entry["clue"] for entry in review["entries"]}
        unknown = sorted({item.clue for item in body.overrides} - known)
        if unknown:
            raise HTTPException(400, "override targets unknown clue: " + "、".join(unknown))
        for override in body.overrides:
            if override.bucket not in BUCKET_ORDER:
                raise HTTPException(400, f"invalid bucket: {override.bucket!r}")
        save_overrides(workspace.path(store_id, "direction_overrides.json"),
                       {item.clue: item.bucket for item in body.overrides})
        run_direction(workspace, settings, store_id)
        return get_direction(store_id)

    @app.post("/api/stores/{store_id}/jobs")
    def start_job(store_id: str) -> dict:
        try:
            return jobs.start(store_id)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        try:
            return jobs.snapshot(job_id)
        except KeyError as error:
            raise HTTPException(404, "no such job") from error

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict:
        try:
            return jobs.cancel(job_id)
        except KeyError as error:
            raise HTTPException(404, "no such job") from error

    @app.get("/api/stores/{store_id}/scenes")
    def get_scenes(store_id: str) -> dict:
        analysis = _require(workspace, store_id, "deepseek_analysis.json")
        sample = _require(workspace, store_id, "sample_store.json")
        clues = sample.get("observed_product_clues") or []
        return {
            "store": sample.get("store") or {},
            "scenes": rank_scenes(analysis, clues),
            "manager_summary": analysis.get("manager_summary") or {},
            "audiences": analysis.get("audiences") or [],
            "reintroduced": _reintroduced(workspace, store_id, analysis),
        }

    return app


def _require(workspace: Workspace, store_id: str, name: str) -> dict:
    path = workspace.path(store_id, name)
    if not path.is_file():
        raise HTTPException(409, f"该阶段还没跑：{name}")
    return read_json(path)


def _reintroduced(workspace: Workspace, store_id: str, analysis: dict) -> list[dict]:
    """Products the model wrote back in that the operator had ruled out."""
    overrides = load_overrides(workspace.path(store_id, "direction_overrides.json"))
    return find_purity_violations(
        analysis, [clue for clue, bucket in overrides.items() if bucket == BUCKET_EXCLUDED]
    )


def _store_payload(workspace: Workspace, jobs: JobManager, store_id: str) -> dict:
    entry = workspace.entry(store_id)
    return {
        "id": store_id,
        "store": entry,
        "stages": workspace.stages(store_id),
        "kept_clues": _kept_clues(workspace, store_id),
        "job": jobs.newest(store_id),
    }


def _kept_clues(workspace: Workspace, store_id: str) -> list[str]:
    path = workspace.path(store_id, "direction.json")
    if not path.is_file():
        return []
    return selected_clues(read_json(path))
