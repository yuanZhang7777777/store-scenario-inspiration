"""Run the store pipeline in the background and report what it is doing.

Eight stages, several of which call DeepSeek and take tens of seconds each, so
they cannot run inside a request. Progress is plain polled state rather than a
stream: with this few stages a two-second poll reads the same to the operator
and keeps the client free of reconnect logic.

The store reading is three stages rather than one — scenes, then one call per
scene for its products, then the conclusions — because a single answer holding
every scene and every product made any one bad character lose the whole store,
and left the writing thinner the further into it the model got.

The local stages sit after the paid ones on purpose. Excluding a product or
turning the recall count up changes nothing the model was asked, so the operator
can tune the result as often as they like without paying twice.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import partial
import threading
import time
import uuid

from ..pipeline.analysis import (
    analyze_expansions,
    analyze_scene_products,
    analyze_scenes,
    analyze_synthesis,
    assemble,
)
from ..pipeline.artifacts import write_json
from ..pipeline.clues import build_analysis_input, load_exclusions, review_clues
from ..pipeline.recognize import build_sample, recognize

from .params import (
    RERANK_DEEPSEEK,
    RERANK_JEV,
    RERANK_MARK_ONLY,
    RERANK_OFF,
    STOCK_IN_ONLY,
    SearchParams,
    load_params,
    save_params,
)
from .rerank import Ask, rerank_store, strip_verdicts
from .rerank_providers import ask_deepseek, ask_typesafe
from .retrieval import flatten_expansions, retrieve_store
from .stores import Workspace, read_json
from .confirmation import (ANALYSIS_STAGES, choose_stages, authorize_start, require_confirmation)

from store_scenario_inspiration.reliability import StoreLease, json_digest, atomic_json


PENDING = "pending"
RUNNING = "running"
READY = "ready"
FAILED = "failed"
CANCELLED = "cancelled"

RECOGNIZE = "recognize"
CLUES = "clues"
SCENES = "scenes"
PRODUCTS = "products"
SYNTHESIS = "synthesis"
EXPAND = "expand"
RETRIEVAL = "retrieval"
RERANK = "rerank"
STAGE_ORDER = (RECOGNIZE, CLUES, SCENES, PRODUCTS, SYNTHESIS, EXPAND, RETRIEVAL, RERANK)
STAGE_LABELS = {
    RECOGNIZE: "识别截图里的商品",
    CLUES: "应用排除名单",
    SCENES: "生成使用场景",
    PRODUCTS: "为每个场景列商品",
    SYNTHESIS: "写店铺结论与人群策略",
    EXPAND: "扩写商品检索词",
    RETRIEVAL: "召回候选 SKU",
    RERANK: "给候选标相关 / 不相关",
}
# The steps that cost money by calling DeepSeek. Rerank is not here because
# whether it costs anything depends on which model answers: Jev charges only for
# input, at 4.2e-8 per token, which rounds to nothing, and DeepSeek charges.
PAID_STAGES = frozenset({RECOGNIZE, SCENES, PRODUCTS, SYNTHESIS, EXPAND})
RECEIPTS = {
    RECOGNIZE: "deepseek_vision.json",
    SCENES: "receipt_scenes.json",
    PRODUCTS: "receipt_products.json",
    SYNTHESIS: "receipt_analysis.json",
    EXPAND: "receipt_expansion.json",
    RERANK: "rerank.json",
}
# How many scenes ask for their products at once. Each call is independent and
# the store background is the same prefix every time, so the provider caches it
# and the calls overlap instead of queueing one behind another.
PRODUCT_WORKERS = 4


def _is_paid(stage_name: str, provider: str) -> bool:
    if stage_name == RERANK:
        return provider == RERANK_DEEPSEEK
    return stage_name in PAID_STAGES


@dataclass
class Stage:
    name: str
    status: str = PENDING
    detail: str = ""
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    seconds: float | None = None


@dataclass
class Job:
    id: str
    store_id: str
    stages: list[Stage] = field(default_factory=list)
    status: str = PENDING
    log: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    created_at: str = ''
    finished_at: str | None = None
    cancelled: bool = False
    provider: str = RERANK_JEV
    started: float = 0.0
    seconds: float | None = None
    params_snapshot: SearchParams | None = None
    lease: object | None = field(default=None, repr=False)
    review_version: str | None = None


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobManager:
    def __init__(self, workspace: Workspace, settings, *, workers: int = 2):
        self.workspace = workspace
        self.settings = settings
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=workers)

    def start(self, store_id: str, stages: tuple[str, ...] | None = None,
              params: SearchParams | None = None) -> dict:
        """Run the whole pipeline, or only the stages the caller asked to redo.

        Recognition is by far the most expensive stage, and an exclusion list or
        a recall count only affects what comes after it, so redoing either must
        not pay for vision again. It is also the one stage that runs without a
        confirmation: everything in ANALYSIS_STAGES spends money on the operator's
        behalf, and that only happens once they have approved the facts it reads.
        """
        self.workspace.entry(store_id)  # Refuse a missing or invalid store.
        unknown = sorted(set(stages or ()) - set(STAGE_ORDER))
        if unknown:
            raise ValueError('unknown stage: ' + '、'.join(unknown))
        chosen = choose_stages(stages, STAGE_ORDER)
        with self._lock:
            stored = params or load_params(self.workspace.path(store_id, 'params.json'))
            for active in self._jobs.values():
                if active.store_id == store_id and active.status in {PENDING, RUNNING}:
                    same_stages = tuple(stage.name for stage in active.stages) == chosen
                    if not active.cancelled and same_stages and active.params_snapshot == stored:
                        return self.snapshot(active.id)
                    raise ValueError('该店铺已有任务，参数未覆盖。请等待完成或停止后再试。')
            lease = StoreLease(self.workspace.path(store_id, '.pipeline.lock'))
            try:
                review_version = authorize_start(self.workspace.dir(store_id), chosen)
                frozen = stored.model_copy(deep=True)
                if params is not None:
                    save_params(self.workspace.path(store_id, 'params.json'), frozen)
                job = Job(id=uuid.uuid4().hex[:12], store_id=store_id, created_at=now(),
                          stages=[Stage(name) for name in chosen], started=time.perf_counter(),
                          provider=frozen.rerank_provider, params_snapshot=frozen, lease=lease,
                          review_version=review_version)
                self._jobs[job.id] = job
                future = self._pool.submit(self._run, job)
            except Exception:
                lease.close()
                if 'job' in locals():
                    self._jobs.pop(job.id, None)
                raise

            def completed(task):
                try:
                    if task.cancelled():
                        with self._lock:
                            job.cancelled, job.status = True, CANCELLED
                            job.finished_at = now()
                            job.seconds = time.perf_counter() - job.started
                finally:
                    lease.close()  # Also releases a task cancelled while still queued.
            future.add_done_callback(completed)
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
                "seconds": round(self._elapsed(job), 1),
                "usage": dict(job.usage),
                "log": list(job.log),
                "stages": [
                    {"name": stage.name, "label": STAGE_LABELS[stage.name],
                     "status": stage.status, "detail": stage.detail, "error": stage.error,
                     "paid": _is_paid(stage.name, job.provider),
                     "started_at": stage.started_at, "finished_at": stage.finished_at,
                     "seconds": stage.seconds}
                    for stage in job.stages
                ],
            }

    @staticmethod
    def _elapsed(job: Job) -> float:
        """How long the operator waited, so far or in total.

        A running job reports live, because the answer to "where is the time
        going" is most useful while it is still going.
        """
        if job.seconds is not None:
            return job.seconds
        if job.started and job.status in {PENDING, RUNNING}:
            return time.perf_counter() - job.started
        return 0.0

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
        return self.snapshot(max(matching, key=lambda job: job.started).id) if matching else None

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _run(self, job: Job) -> None:
        unexpected = False
        with self._lock:
            job.status = RUNNING
        try:
            params = (job.params_snapshot if job.params_snapshot is not None
                      else load_params(self.workspace.path(job.store_id, 'params.json')).model_copy(deep=True))
            for stage in job.stages:
                if job.cancelled:
                    break
                self._run_stage(job, stage, params)
                if stage.status == FAILED:
                    if stage.name == SYNTHESIS:
                        # run_synthesis has already published an explicitly partial,
                        # shape-safe analysis. run_expand reads products, not prose.
                        with self._lock:
                            job.log.append({'at': now(), 'stage': SYNTHESIS,
                                            'message': '补充分析未完成，继续匹配商品；可单独重试补充分析。'})
                        continue
                    break
        except Exception as exc:
            unexpected = True
            with self._lock:
                job.log.append({'at': now(), 'stage': 'job',
                                'message': '任务未正常完成：' + type(exc).__name__})
        finally:
            with self._lock:
                critical_failed = any(stage.status == FAILED and stage.name != SYNTHESIS for stage in job.stages)
                # A synthesis-only retry has no successful core result in this job.
                only_failed = bool(job.stages) and not any(stage.status == READY for stage in job.stages)
                job.status = (CANCELLED if job.cancelled else FAILED if unexpected or critical_failed or only_failed else READY)
                job.finished_at = now()
                job.seconds = time.perf_counter() - job.started
            if job.lease is not None:
                job.lease.close()

    def _run_stage(self, job: Job, stage: Stage, params: SearchParams) -> None:
        with self._lock:
            stage.status = RUNNING
            stage.started_at = now()
            job.log.append({"at": stage.started_at, "stage": stage.name,
                            "message": f"开始{STAGE_LABELS[stage.name]}"})
        started = time.perf_counter()
        try:
            if stage.name in ANALYSIS_STAGES:
                require_confirmation(self.workspace.dir(job.store_id), job.review_version)
            detail = STAGE_RUNNERS[stage.name](self.workspace, self.settings, job.store_id, params)
        except Exception as error:  # noqa: BLE001 — reported to the operator, not swallowed
            with self._lock:
                stage.status = FAILED
                stage.error = f"{type(error).__name__}: {error}"
                stage.finished_at = now()
                stage.seconds = round(time.perf_counter() - started, 1)
                job.log.append({"at": stage.finished_at, "stage": stage.name,
                                "message": f"失败：{stage.error}"})
            return
        with self._lock:
            stage.status = READY
            stage.detail = detail
            stage.finished_at = now()
            stage.seconds = round(time.perf_counter() - started, 1)
            job.log.append({"at": stage.finished_at, "stage": stage.name,
                            "message": f"{detail}（{stage.seconds} 秒）"})
            self._add_usage(job, stage.name)

    def _add_usage(self, job: Job, stage_name: str) -> None:
        """Fold this stage's token spend into the job total."""
        receipt_name = RECEIPTS.get(stage_name)
        path = self.workspace.path(job.store_id, receipt_name or "")
        if not receipt_name or not path.is_file():
            return
        usage = read_json(path).get("usage") or {}
        for key, value in usage.items():
            if isinstance(value, int) and isinstance(job.usage.get(key), int):
                job.usage[key] = job.usage[key] + value
            else:
                job.usage[key] = value


def run_recognize(workspace: Workspace, settings, store_id: str, params: SearchParams) -> str:
    entry = workspace.entry(store_id)
    result, receipt = recognize(entry, settings.api_key)
    base = workspace.dir(store_id)
    write_json(base / "deepseek_vision.json", receipt)
    write_json(base / "sample_store.json",
               build_sample(entry, result, workspace.path(store_id, "store.json")))
    return f"识别出 {sum(len(image['products']) for image in result['images'])} 个商品"


def run_clues(workspace: Workspace, settings, store_id: str, params: SearchParams) -> str:
    base = workspace.dir(store_id)
    sample = read_json(base / "sample_store.json")
    review = review_clues(sample.get("observed_product_clues") or [],
                          load_exclusions(base / "exclusions.json"))
    write_json(base / "clues.json", review)
    write_json(base / "analysis_input.json", build_analysis_input(sample, review))
    counts = review["counts"]
    return f"保留 {counts['kept']} 条，排除 {counts['excluded']} 条"


def sampling(params: SearchParams) -> dict:
    """How the operator asked the writing model to pick its words.

    Judging and recognising deliberately keep their own settings: a verdict that
    changes with a slider cannot be reproduced, and the rerank cache is keyed on
    the question alone, so a knob it does not honour would hand back an answer
    that no longer matches the setting. These apply to the one call whose job is
    to be imaginative.
    """
    return {"temperature": params.temperature}


def run_scenes(workspace: Workspace, settings, store_id: str, params: SearchParams) -> str:
    base = workspace.dir(store_id)
    result, receipt = analyze_scenes(
        read_json(base / "analysis_input.json"), settings.api_key,
        scene_count=params.scene_count, **sampling(params),
    )
    write_json(base / "deepseek_scenes.json", result)
    write_json(base / "receipt_scenes.json", receipt)
    return f"生成 {len(result['scenes'])} 个场景"


def run_products(workspace: Workspace, settings, store_id: str, params: SearchParams) -> str:
    """Stage in a batch directory; publish only a complete manifest; resume failed scenes."""
    base = workspace.dir(store_id)
    source = read_json(base / 'analysis_input.json')
    skeleton = read_json(base / 'deepseek_scenes.json')
    scenes = skeleton.get('scenes')
    if not isinstance(scenes, list) or not scenes:
        raise ValueError('没有有效场景，未生成商品列表。')
    names = [scene['scene_name'] for scene in scenes]
    if len(set(names)) != len(names):
        raise ValueError('场景名称重复，未混合商品结果。')
    digest = _products_source_digest(base)
    key = json_digest({'source': digest, 'products_per_scene': params.products_per_scene,
                       'sampling': sampling(params)})
    manifest_path = base / 'products' / 'manifest.json'
    previous = {}
    if manifest_path.is_file():
        try:
            previous = read_json(manifest_path)
        except (ValueError, TypeError, OSError):
            pass
    reused = {}
    batch_id = uuid.uuid4().hex
    if previous.get('status') in ('building', 'failed') and previous.get('request_digest') == key:
        for entry in previous.get('files') or []:
            try:
                index = entry['index']
                if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(scenes):
                    continue
                path = (base / 'products' / entry['path']).resolve()
                if not path.is_relative_to((base / 'products' / 'runs').resolve()):
                    continue
                frame = read_json(path)
                if frame['scene_name'] == names[index] and json_digest(frame) == entry['sha256']:
                    reused[index] = entry
            except (ValueError, TypeError, KeyError, OSError):
                continue
    manifest = {'schema': 'store-products-manifest-v1', 'status': 'building', 'batch_id': batch_id,
                'source_digest': digest, 'request_digest': key, 'files': list(reused.values())}
    atomic_json(manifest_path, manifest)
    receipts, failed, entries = [], [], dict(reused)

    def one(index):
        scene = scenes[index]
        try:
            result, receipt = analyze_scene_products(source, scene, settings.api_key,
                                                    products_per_scene=params.products_per_scene, **sampling(params))
            frame = {'scene_name': scene['scene_name'], 'products': result['products']}
            path = f'runs/{batch_id}/{index:02d}.json'
            atomic_json(base / 'products' / path, frame)
            return index, {'index': index, 'path': path, 'scene_name': scene['scene_name'],
                           'sha256': json_digest(frame)}, receipt, None
        except Exception as exc:
            return index, None, {}, type(exc).__name__

    pending = [index for index in range(len(scenes)) if index not in reused]
    if pending:
        with ThreadPoolExecutor(max_workers=min(PRODUCT_WORKERS, len(pending))) as pool:
            for index, entry, receipt, error in pool.map(one, pending):
                if error:
                    failed.append({'index': index, 'scene_name': names[index], 'error_type': error})
                else:
                    entries[index] = entry
                    receipts.append(receipt)
                manifest['files'] = [entries[i] for i in sorted(entries)]
                atomic_json(manifest_path, manifest)
    manifest['failed'] = failed
    manifest['status'] = 'failed' if failed else 'ready'
    if _products_source_digest(base) != digest:
        manifest['status'] = 'failed'
        manifest['failed'].append({'error_type': 'SourceChanged'})
    atomic_json(manifest_path, manifest)
    receipt = _merge_receipts(receipts, len(scenes))
    receipt['reused_scenes'] = len(reused)
    write_json(base / 'receipt_products.json', receipt)
    if manifest['status'] != 'ready':
        raise RuntimeError('部分场景商品未生成完成；已保存成功结果，重试时只处理未完成的场景。')
    frames = _product_frames(base)
    _publish_partial_analysis(base, skeleton, frames)
    return f"{len(scenes)} 个场景共列出 {sum(len(frame['products']) for frame in frames)} 个商品角色"


def run_synthesis(workspace: Workspace, settings, store_id: str, params: SearchParams) -> str:
    base = workspace.dir(store_id)
    scenes, frames = read_json(base / 'deepseek_scenes.json'), _product_frames(base)
    _publish_partial_analysis(base, scenes, frames)
    synthesis, receipt = analyze_synthesis(
        read_json(base / 'analysis_input.json'),
        [{**scene, 'product_needs': frame['products']} for scene, frame in zip(scenes['scenes'], frames, strict=True)],
        settings.api_key, **sampling(params))
    value = assemble(scenes, frames, synthesis)
    value['analysis_status'] = 'complete'
    write_json(base / 'deepseek_analysis.json', value)
    write_json(base / 'receipt_analysis.json', receipt)
    return '已完成店铺补充分析'


def _slug(name: str) -> str:
    """The readable half of a scene's filename; the index prefix is what is unique."""
    safe = "".join(character for character in name if character.isalnum())
    return f"{safe[:40] or 'scene'}"


def _product_frames(base) -> list[dict]:
    """Read only the currently committed batch, not every JSON left in the directory."""
    manifest_path = base / 'products' / 'manifest.json'
    skeleton = read_json(base / 'deepseek_scenes.json')
    scenes = skeleton['scenes']
    if manifest_path.is_file():
        manifest = read_json(manifest_path)
        if manifest.get('status') != 'ready' or manifest.get('source_digest') != _products_source_digest(base):
            raise RuntimeError('场景商品结果未完成或已过期，请先更新场景商品。')
        entries = manifest.get('files')
        if not isinstance(entries, list) or len(entries) != len(scenes):
            raise RuntimeError('场景商品清单不完整，请重新生成。')
        frames = []
        for index, (scene, entry) in enumerate(zip(scenes, entries, strict=True)):
            if entry.get('index') != index or entry.get('scene_name') != scene['scene_name']:
                raise RuntimeError('场景商品顺序不一致，请重新生成。')
            path = (base / 'products' / entry['path']).resolve()
            if not path.is_relative_to((base / 'products' / 'runs').resolve()):
                raise RuntimeError('场景商品路径无效。')
            frame = read_json(path)
            if frame.get('scene_name') != scene['scene_name'] or json_digest(frame) != entry.get('sha256'):
                raise RuntimeError('场景商品内容已变化，请重新生成。')
            frames.append(frame)
        return frames
    # Compatibility with original artifacts: exact expected index + name only.
    # Ambiguous/missing legacy files require regeneration rather than guessing.
    frames = []
    for index, scene in enumerate(scenes):
        path = base / 'products' / f"{index:02d}-{_slug(scene['scene_name'])}.json"
        if not path.is_file():
            raise RuntimeError('历史场景商品不完整，请重新生成场景商品。')
        frame = read_json(path)
        if frame.get('scene_name') != scene['scene_name']:
            raise RuntimeError('历史场景商品与当前场景不一致，请重新生成。')
        frames.append(frame)
    return frames


def _merge_receipts(receipts: list[dict], scenes: int) -> dict:
    """One receipt for the whole stage, with each call's spend kept underneath."""
    total: dict = {}
    for receipt in receipts:
        for key, value in (receipt.get("usage") or {}).items():
            if isinstance(value, int) and isinstance(total.get(key), int):
                total[key] = total[key] + value
            elif isinstance(value, int):
                total[key] = value
    return {"calls": len(receipts), "scenes": scenes, "usage": total, "per_call": receipts}


def run_expand(workspace: Workspace, settings, store_id: str, params: SearchParams) -> str:
    base = workspace.dir(store_id)
    if (base / 'deepseek_scenes.json').is_file():
        frames = _product_frames(base)
        source = {'scenes': [{'scene_name': frame['scene_name'], 'products': [
            {'product_cn': product.get('product_cn'), 'product_en': product.get('product_en')}
            for product in frame['products']]} for frame in frames]}
    else:
        # Previously assembled, pre-split results remain usable without rewriting them.
        analysis = read_json(base / 'deepseek_analysis.json')
        source = {'scenes': [{'scene_name': scene.get('scene_name'), 'products': [
            {'product_cn': product.get('product_cn'), 'product_en': product.get('product_en')}
            for product in scene.get('product_needs') or []]} for scene in analysis.get('scenes') or []]}
    result, receipt = analyze_expansions(source, settings.api_key,
                                        expansion_terms=params.expansion_terms, **sampling(params))
    write_json(base / 'expansions.json', result)
    write_json(base / 'receipt_expansion.json', receipt)
    count = sum(len(scene['products']) for scene in result['scenes'])
    fallback = receipt.get('expansion_fallback', {}).get('count', 0)
    tail = f'；其中 {fallback} 个使用原商品词继续匹配' if fallback else ''
    return f'已准备 {count} 个商品的匹配词{tail}'


def run_retrieval(workspace: Workspace, settings, store_id: str, params: SearchParams) -> str:
    base = workspace.dir(store_id)
    entry = workspace.entry(store_id)
    result = retrieve_store(
        asset_db=settings.asset_db,
        vector_cache=settings.vector_cache,
        model_cache=settings.model_cache,
        stock_path=settings.stock,
        country=str(entry.get("country") or ""),
        products=flatten_expansions(read_json(base / "expansions.json")),
        params=params,
    )
    write_json(base / "retrieval.json", result)
    # A fresh recall invalidates the old verdicts; leaving the marker would claim
    # the new list had been judged when it had not.
    (base / "rerank.json").unlink(missing_ok=True)
    counts = [len(scene["candidates"]) for scene in result["scenes"]]
    if result["inventory"] != "available":
        where = "未做库存过滤"
    elif params.stock_filter == STOCK_IN_ONLY:
        where = "只留目标国家有货的"
    else:
        where = "保留全部语义候选，逐条标注了有没有货"
    return f"{len(counts)} 个商品角色各召回 {min(counts, default=0)}–{max(counts, default=0)} 个 SKU（{where}）"


def _rerank_asker(settings, provider: str) -> Ask:
    """Who answers, and where an answer may be reused instead of paid for again.

    Each model gets its own cache directory. They answer the same question in
    different words, so a file has to name the model that wrote it — that is what
    makes "which model said this" answerable from the disk rather than from memory.
    """
    cache = settings.data_dir / "cache" / "rerank"
    if provider == RERANK_JEV:
        return partial(ask_typesafe, api_key=settings.typesafe_key,
                       url=settings.typesafe_url, cache_dir=cache / "jev")
    return partial(ask_deepseek, api_key=settings.api_key, cache_dir=cache)


def _forget_verdicts(base, payload: dict) -> int:
    """Put back everything the last pass removed, so a skipped step is not a stale one."""
    restored = strip_verdicts(payload)
    write_json(base / "retrieval.json", payload)
    (base / "rerank.json").unlink(missing_ok=True)
    return restored


def run_rerank(workspace: Workspace, settings, store_id: str, params: SearchParams) -> str:
    base = workspace.dir(store_id)
    payload = read_json(base / 'retrieval.json')
    provider = params.rerank_provider
    # Judge the recall itself, not what the last pass left of it: a row dropped on
    # the previous run has to stay in the audit rather than fall off it. This is
    # also written to disk BEFORE another service is contacted, so a failure
    # leaves disk, interface and export agreeing that these rows are unjudged.
    restored = _forget_verdicts(base, payload)
    if provider == RERANK_OFF:
        if not restored:
            return '这一步已关闭，候选列表就是纯召回结果'
        return f'这一步已关闭，上一步剔除的 {restored} 条候选回到了列表'
    key = settings.typesafe_key if provider == RERANK_JEV else settings.api_key
    if not key:
        where = 'TypeSafe 的 Jev' if provider == RERANK_JEV else 'DeepSeek'
        tail = (f'，上一步剔除的 {restored} 条候选回到了列表' if restored
                else '，候选列表保持纯召回结果')
        return f'没有配置 {where} 密钥，跳过了这一步{tail}'
    try:
        rerank_store(payload, ask=_rerank_asker(settings, provider), cut=params.rerank_cutoff,
                     mode=params.rerank, provider=provider)
    except (RuntimeError, ValueError, TypeError, KeyError, OSError) as error:
        # The recall is what the operator came for; the model's own words are the
        # only version of the failure they can act on.
        _forget_verdicts(base, payload)
        return f'模型暂时用不了，候选列表保持纯召回结果（{error}）'
    write_json(base / 'retrieval.json', payload)
    write_json(base / 'rerank.json', payload['rerank'])
    summary = payload['rerank']
    if summary['failed']:
        what = summary['notes'][0] if summary['notes'] else '未获得复核答案'
        return (f"已复核 {summary['answered']} 件商品，但 {summary['failed']} 个场景没问上"
                f"（{what}），候选已保留")
    if params.rerank == RERANK_MARK_ONLY:
        return f"已给 {summary['answered']} 件商品标上相关 / 不相关，都留着没有删"
    return f"已去掉 {summary['dropped']} 条不相关的候选"


STAGE_RUNNERS = {
    RECOGNIZE: run_recognize,
    CLUES: run_clues,
    SCENES: run_scenes,
    PRODUCTS: run_products,
    SYNTHESIS: run_synthesis,
    EXPAND: run_expand,
    RETRIEVAL: run_retrieval,
    RERANK: run_rerank,
}


def _products_source_digest(base) -> str:
    return json_digest({'scenes': read_json(base / 'deepseek_scenes.json'),
                        'source': read_json(base / 'analysis_input.json')})


def _publish_partial_analysis(base, scenes: dict, frames: list[dict]) -> None:
    # Fallback text describes a processing state, not a fabricated business opinion.
    notice = '店铺补充分析尚未完成；商品与场景匹配可继续使用。'
    skeleton = {
        'manager_summary': {'executive_conclusion': notice, 'business_opportunity': '',
                            'recommended_actions': [], 'decision_boundary': '请结合匹配结果核验规格、库存和具体子款。'},
        'store_profile': {'judgement': '待完成补充分析', 'evidence': ''},
        'current_product_structure': {'judgement': '待完成补充分析', 'evidence': ''},
        'future_product_structure': {'judgement': '待完成补充分析', 'evidence': '', 'priority_order': []},
        'audiences': [], 'operation_strategy': [],
    }
    value = assemble(scenes, frames, skeleton)
    value['analysis_status'] = 'partial'
    write_json(base / 'deepseek_analysis.json', value)
