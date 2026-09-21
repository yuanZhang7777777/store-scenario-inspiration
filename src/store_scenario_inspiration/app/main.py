"""HTTP surface for the store-scenario workspace.

Small on purpose: upload screenshots, run the pipeline, read back scenes and
the operator's corrections. Everything it returns is read straight off the store
directory, so the command line and the browser never disagree.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import io
import json
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from ..pipeline.clues import (
    excluded_clues,
    find_purity_violations,
    kept_clues,
    load_exclusions,
    save_exclusions,
)

from ..pipeline.business import normalize_metrics
from .confirmation import (ANALYSIS_STAGES, ConfirmationRequired, approve_review,
                           invalidate_review, review_details, review_status)
from ..reliability import StoreLease
from contextlib import closing
from .config import Settings
from .export import (
    deduped_rows,
    notes_for,
    report_rows,
    roles_exported,
    summaries,
    workbook,
)
from .jobs import STAGE_ORDER, JobManager, run_clues
from .params import SearchParams, load_params, save_params
from .scenes import annotate
from .stores import Workspace, read_json


class ReviewApproval(BaseModel):
    version: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class Exclusions(BaseModel):
    """The whole exclusion list, not a diff: the operator sends what they see."""

    excluded: list[str] = Field(default_factory=list)


class RunRequest(BaseModel):
    """Which stages to run and with which knobs. Omitted means everything, as before."""

    stages: list[str] | None = None
    params: SearchParams | None = None


class Pick(BaseModel):
    """One row the operator ticked, as the three names that identify it."""

    scene_name: str
    product_cn: str
    main_sku: str


class AdoptionPicks(BaseModel):
    """What to write out, and whether to collapse each SKU onto one line."""

    picks: list[Pick] = Field(default_factory=list)
    dedupe: bool = False


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

    # A store id in the URL that was never issued, or that no longer resolves to
    # a directory, is the caller's mistake rather than a fault on this side.
    # Without these two the raised ValueError and FileNotFoundError come back as
    # a bare "500 Internal Server Error", which the browser shows verbatim and
    # which says nothing about what to do next.
    @app.exception_handler(ValueError)
    async def _rejected(_request: Request, error: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(error)})

    @app.exception_handler(FileNotFoundError)
    async def _missing(_request: Request, error: FileNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "没有这个店铺"})

    @app.get("/api/health")
    def health() -> dict:
        return {
            "data_dir": str(settings.data_dir),
            "countries": list(settings.countries),
            "api_key_configured": bool(settings.api_key),
            "max_upload_bytes": settings.max_upload_bytes,
        }

    @app.post("/api/stores")
    async def create_store(
        store_name: str = Form(...),
        country: str = Form(...),
        files: list[UploadFile] = File(...),
        business_metrics: str = Form("{}"),
    ) -> dict:
        if country.strip().upper() not in settings.countries:
            raise HTTPException(400, f"unsupported country: {country!r}")
        try:
            metrics = normalize_metrics(json.loads(business_metrics))
        except (ValueError, TypeError) as error:
            raise HTTPException(400, str(error)) from error
        if len(files) > 20:
            raise HTTPException(413, "一次最多上传 20 张图片")
        uploads = []
        total_bytes = 0
        for upload in files:
            payload = await upload.read(settings.max_upload_bytes + 1)
            total_bytes += len(payload)
            if total_bytes > min(200 * 1024 * 1024, settings.max_upload_bytes * 20):
                raise HTTPException(413, "本次上传图片总量过大，请减少图片后重试")
            if not payload:
                raise HTTPException(400, f"empty upload: {upload.filename}")
            if len(payload) > settings.max_upload_bytes:
                raise HTTPException(413, f"too large: {upload.filename}")
            if not (upload.content_type or "").startswith("image/"):
                raise HTTPException(400, f"not an image: {upload.filename}")
            uploads.append((upload.filename or "screenshot.png", io.BytesIO(payload)))
        try:
            return workspace.create(store_name, country, uploads, business_metrics=metrics)
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

    @app.get("/api/stores/{store_id}/review")
    def get_review(store_id: str) -> dict:
        workspace.entry(store_id)
        return review_details(workspace.dir(store_id))

    @app.post("/api/stores/{store_id}/review/confirm")
    def confirm_review(store_id: str, body: ReviewApproval) -> dict:
        workspace.entry(store_id)
        try:
            with closing(StoreLease(workspace.path(store_id, ".pipeline.lock"))):
                approve_review(workspace.dir(store_id), body.version)
            # start() reacquires the lease and revalidates the same facts.
            return jobs.start(store_id, ANALYSIS_STAGES)
        except (ValueError, RuntimeError) as error:
            raise HTTPException(409, str(error)) from error

    @app.get("/api/stores/{store_id}/clues")
    def get_clues(store_id: str) -> dict:
        """Every recognised product, with the operator's exclusions applied.

        Recognition tags nearly everything as a product card at the same
        confidence, so there is no threshold worth showing. The only lever is
        what the operator excludes by hand, and that is all this returns.
        """
        review = _require(workspace, store_id, "clues.json")
        path = workspace.path(store_id, "exclusions.json")
        review["excluded"] = sorted(load_exclusions(path))
        review["exclusions_saved"] = path.is_file()
        return review

    @app.put("/api/stores/{store_id}/clues")
    def put_clues(store_id: str, body: Exclusions) -> dict:
        """Record the operator's exclusions, then rebuild the filtered input.

        Rebuilding is local and free, so the next scene generation sees the
        correction without a second DeepSeek call.
        """
        review = _require(workspace, store_id, "clues.json")
        known = {entry["clue"] for entry in review["entries"]}
        unknown = sorted(set(body.excluded) - known)
        if unknown:
            raise HTTPException(400, "排除了一个不存在的商品：" + "、".join(unknown))
        try:
            with closing(StoreLease(workspace.path(store_id, ".pipeline.lock"))):
                previous = load_exclusions(workspace.path(store_id, "exclusions.json"))
                if set(body.excluded) != previous:
                    invalidate_review(workspace.dir(store_id))
                save_exclusions(workspace.path(store_id, "exclusions.json"), body.excluded)
                run_clues(workspace, settings, store_id, load_params(workspace.path(store_id, "params.json")))
        except (ValueError, RuntimeError) as error:
            raise HTTPException(409, str(error)) from error
        return get_clues(store_id)

    @app.get("/api/params/schema")
    def params_schema() -> dict:
        """The knob list the settings form renders, with its Chinese explanations."""
        return SearchParams.schema_for_ui()

    @app.get("/api/stores/{store_id}/params")
    def get_params(store_id: str) -> dict:
        workspace.dir(store_id)
        return load_params(workspace.path(store_id, "params.json")).model_dump()

    @app.put("/api/stores/{store_id}/params")
    def put_params(store_id: str, body: SearchParams) -> dict:
        workspace.dir(store_id)
        save_params(workspace.path(store_id, "params.json"), body)
        return body.model_dump()

    @app.post("/api/stores/{store_id}/jobs")
    def start_job(store_id: str, body: RunRequest | None = None) -> dict:
        requested = tuple(body.stages) if body and body.stages else ()
        unknown = sorted(set(requested) - set(STAGE_ORDER))
        if unknown:
            raise HTTPException(400, "unknown stage: " + "、".join(unknown))
        try:
            return jobs.start(store_id, requested or None, body.params if body else None)
        except ConfirmationRequired as error:
            raise HTTPException(409, str(error)) from error
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

    @app.get("/api/stores/{store_id}/analysis")
    def get_analysis(store_id: str) -> dict:
        """The whole store reading, in the order a manager reads it."""
        analysis = _require(workspace, store_id, "deepseek_analysis.json")
        sample = _require(workspace, store_id, "sample_store.json")
        excluded = sorted(load_exclusions(workspace.path(store_id, "exclusions.json")))
        return {
            "store": sample.get("store") or {},
            "business_context": sample.get("business_context") or None,
            "manager_summary": analysis.get("manager_summary") or {},
            "store_profile": analysis.get("store_profile") or {},
            "audiences": analysis.get("audiences") or [],
            "current_product_structure": analysis.get("current_product_structure") or {},
            "scenes": annotate(analysis, excluded),
            "future_product_structure": analysis.get("future_product_structure") or {},
            "operation_strategy": analysis.get("operation_strategy") or [],
            "reintroduced": find_purity_violations(analysis, excluded),
        }

    @app.get("/api/stores/{store_id}/retrieval")
    def get_retrieval(store_id: str) -> dict:
        """Scene → product → ranked catalogue SKUs, in the order the operator reads it."""
        return _require(workspace, store_id, "retrieval.json")

    @app.post("/api/stores/{store_id}/adoption/export")
    def export_adoption(store_id: str, body: AdoptionPicks) -> Response:
        """The ticked rows as a spreadsheet, joined against what the run recorded.

        The picks come back over the wire, so they are used as a filter and
        nothing else: names, ranks and stock all come off disk.

        The same SKU picked under several roles is one product, and the operator
        decides which list they want: the report repeats it once per role, the
        buy-list keeps it once and says where it was used.
        """
        retrieval = _require(workspace, store_id, "retrieval.json")
        analysis = _require(workspace, store_id, "deepseek_analysis.json")
        entry = workspace.entry(store_id)
        picks = [pick.model_dump() for pick in body.picks]
        rows = report_rows(analysis, retrieval, picks)
        notes = notes_for(
            retrieval=retrieval,
            params=load_params(workspace.path(store_id, "params.json")).model_dump(),
            store=entry, store_id=store_id,
            stock_path=settings.stock, exported=roles_exported(rows),
        )
        name = f"{entry.get('store_name') or store_id}-店铺场景报告.xlsx"
        return Response(
            content=workbook(rows, summaries(analysis, entry, retrieval.get("country") or ""),
                             notes,
                             deduped_rows(analysis, retrieval, picks) if body.dedupe else None),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
        )

    return app


def _require(workspace: Workspace, store_id: str, name: str) -> dict:
    path = workspace.path(store_id, name)
    if not path.is_file():
        raise HTTPException(409, f"这个阶段还没跑：{name}")
    return read_json(path)


def _store_payload(workspace: Workspace, jobs: JobManager, store_id: str) -> dict:
    entry = workspace.entry(store_id)
    clues_path = workspace.path(store_id, "clues.json")
    return {
        "id": store_id,
        "store": entry,
        "stages": workspace.stages(store_id),
        "params": load_params(workspace.path(store_id, "params.json")).model_dump(),
        "kept_clues": kept_clues(read_json(clues_path)) if clues_path.is_file() else [],
        "excluded_clues": excluded_clues(read_json(clues_path)) if clues_path.is_file() else [],
        "job": jobs.newest(store_id),
        "confirmation": review_status(workspace.dir(store_id)),
    }
