import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  api,
  type Analysis,
  type Clues,
  type CustomProduct,
  type Job,
  type Judgement,
  type Params,
  type Retrieval,
  type StageProgress,
  type StoreDetail,
} from "../api";
import { countryName } from "../countries";
import { paramsEqual, validateParams, stageNotice, mergeStages } from "../operatorUx";
import ParamsPanel from "../components/ParamsPanel";
import PhotoViewer from "../components/PhotoViewer";
import SceneWorkbench from "../components/SceneWorkbench";
import StageRail, { type RailStage } from "../components/StageRail";

function isLive(job: Job | null): boolean {
  return job?.status === "pending" || job?.status === "running";
}

/** "12.3 秒" / "1 分 05 秒" — the operator asked where the time goes. */
function took(seconds: number | null | undefined): string {
  if (seconds == null) return "";
  if (seconds < 60) return `${seconds.toFixed(1)} 秒`;
  const whole = Math.round(seconds);
  return `${Math.floor(whole / 60)} 分 ${String(whole % 60).padStart(2, "0")} 秒`;
}

const ROLE_LABEL = "识别到的商品";

/** The pipeline, in the order the job manager runs it, and where each step's
 *  output can be read. Mirrors ``STAGE_LABELS`` in ``app/jobs.py``. */
const PIPELINE: { name: string; label: string; target: string; flag: keyof StageProgress }[] = [
  { name: "recognize", label: "识别截图里的商品", target: "clues", flag: "recognized" },
  { name: "clues", label: "去掉你排除的商品", target: "clues", flag: "clues" },
  { name: "synthesis", label: "写店铺结论和人群策略", target: "analysis-summary", flag: "synthesis" },
  { name: "scenes", label: "生成场景", target: "analysis-scenes", flag: "scenes" },
  { name: "products", label: "列出每个场景要用的商品", target: "sku-recommendations", flag: "products" },
  { name: "expand", label: "补充搜索词", target: "", flag: "expansions" },
  { name: "retrieval", label: "找商品", target: "sku-recommendations", flag: "retrieval" },
  { name: "rerank", label: "帮你复核一遍", target: "sku-recommendations", flag: "rerank" },
];

/** Which saved result each stage replaces, so a finished stage redraws only
 *  what it changed instead of pulling the whole store again. */
const ARTIFACTS: Record<string, string> = {
  recognize: "clues",
  clues: "clues",
  synthesis: "synthesis",
  retrieval: "retrieval",
  rerank: "retrieval",
};

function Judged({ section }: { section: Judgement }) {
  return (
    <div className="judged">
      <p className="verdict">{section.judgement}</p>
    </div>
  );
}

function Cards({ items }: { items: { name: string; description: string }[] }) {
  return (
    <div className="people-grid">
      {items.map((item) => (
        <article className="person" key={item.name}>
          <h3>{item.name}</h3>
          <p>{item.description}</p>
        </article>
      ))}
    </div>
  );
}

export default function StorePage() {
  const { storeId = "" } = useParams();
  const [detail, setDetail] = useState<StoreDetail | null>(null);
  const [clues, setClues] = useState<Clues | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [retrieval, setRetrieval] = useState<Retrieval | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  /** The knobs as the form currently shows them, saved or not, so a run started
   *  from the header applies what the operator just typed rather than what was
   *  last written to disk. */
  const [draft, setDraft] = useState<Params | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [hint, setHint] = useState("");
  const [newProduct, setNewProduct] = useState({ name_cn: "", name_en: "" });
  /** Which screenshot is open full size, or none. */
  const [shot, setShot] = useState<number | null>(null);
  const shown = useRef("");
  const requesting = useRef(false);
  /** Which stages of the running job were already ready, so each one is drawn
   *  as it lands rather than the whole report appearing at the end. */
  const drawn = useRef<{ id: string; done: Set<string> }>({ id: "", done: new Set() });

  /** Re-read the saved results, or only the named ones.
   *
   * ``undefined`` means "leave that panel alone" — a recall payload runs to
   * megabytes, so the poll only pulls back what the stage that just finished
   * actually rewrote. */
  const refresh = useCallback(async (names: Set<string> | null) => {
    const data = await api.store(storeId);
    if (shown.current !== storeId) return null;
    setDetail(data);
    // Start following an active job even if one artifact request needs a retry.
    setJob((current) => current ?? data.job);
    const wanted = (name: string) => names === null || names.has(name);
    const [nextClues, nextAnalysis, nextRetrieval] = await Promise.all([
      wanted("clues") && data.stages.clues ? api.clues(storeId) : Promise.resolve(undefined),
      wanted("synthesis") && data.stages.synthesis ? api.analysis(storeId) : Promise.resolve(undefined),
      wanted("retrieval") && data.stages.retrieval ? api.retrieval(storeId) : Promise.resolve(undefined),
    ]);
    if (shown.current !== storeId) return null;
    if (nextClues !== undefined) setClues(nextClues);
    if (nextAnalysis !== undefined) setAnalysis(nextAnalysis);
    if (nextRetrieval !== undefined) setRetrieval(nextRetrieval);
    setError("");
    return data;
  }, [storeId]);

  const load = useCallback(async () => {
    const data = await refresh(null);
    if (data) setJob(data.job);
  }, [refresh]);

  useEffect(() => {
    shown.current = storeId;
    setDetail(null); setAnalysis(null); setRetrieval(null); setClues(null); setJob(null); setError("");
    setDraft(null); setShot(null);
    drawn.current = { id: "", done: new Set() };
    load().catch((e: Error) => { if (shown.current === storeId) setError(e.message); });
    return () => { shown.current = ""; };
  }, [load, storeId]);

  const running = isLive(job);
  const jobId = job?.id ?? "";
  useEffect(() => {
    if (!running || !jobId) return;
    let pending = false;
    let active = true;
    const timer = setInterval(async () => {
      if (pending) return;
      pending = true;
      try {
        const next = await api.job(jobId);
        if (!active || shown.current !== storeId) return;
        if (drawn.current.id !== next.id) drawn.current = { id: next.id, done: new Set() };
        const landed = next.stages.filter((stage) => stage.status === "ready" && !drawn.current.done.has(stage.name));
        if (landed.length) {
          const artifacts = new Set(landed.map((stage) => ARTIFACTS[stage.name]).filter(Boolean));
          await refresh(artifacts.size ? artifacts : null);
          landed.forEach((stage) => drawn.current.done.add(stage.name));
        }
        if (!isLive(next)) await load();
        else if (active) setJob(next);
      } catch (e) {
        if (active && (e as Error).message === "no such job") {
          try { await load(); return; } catch { /* The visible retry below retains the error. */ }
        }
        if (active) setError(`进度暂时无法更新，正在重试。${(e as Error).message}`);
      } finally { pending = false; }
    }, 2000);
    return () => { active = false; clearInterval(timer); };
  }, [running, jobId, storeId, refresh, load]);

  // Every run uses the settings as the form shows them, saved or not, and saving
  // settings does not itself launch a job.
  async function saveDraft(value: Params | null = draft): Promise<StoreDetail | null> {
    if (!detail || !value || paramsEqual(value, detail.params)) return detail;
    const schema = await api.paramSchema();
    const invalid = validateParams(value, schema.fields);
    if (invalid) throw new Error(invalid);
    await api.setParams(storeId, value);
    const updated = await api.store(storeId);
    if (shown.current === storeId) setDetail(updated);
    return updated;
  }

  async function run(stages?: string[], params?: Params): Promise<boolean> {
    if (requesting.current || isLive(job) || busy) return false;
    const recognitionOnly = Boolean(stages?.length) && stages!.every((stage) => ["recognize", "clues"].includes(stage));
    requesting.current = true;
    setBusy(true);
    setError("");
    setHint("");
    const nextStages = stages ?? (detail?.stages.recognized
      ? ["clues", "synthesis", "scenes", "products", "expand", "retrieval", "rerank"]
      : undefined);
    try {
      const chosenParams = params ?? draft ?? detail?.params;
      let stagesToRun = nextStages;
      if (!recognitionOnly && chosenParams && detail && !paramsEqual(chosenParams, detail.params)) {
        const updated = await saveDraft(chosenParams);
        // A changed scene count must also refresh the scenes, even when the
        // button was originally rendered for a retrieval-only update.
        stagesToRun = mergeStages(nextStages ?? [], updated?.outdated ?? []);
      }
      const nextJob = await api.startJob(storeId, stagesToRun, recognitionOnly ? undefined : chosenParams);
      if (shown.current !== storeId) return false;
      setJob(nextJob);
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "分析未能启动，请稍后重试。");
      return false;
    } finally {
      requesting.current = false;
      setBusy(false);
    }
  }

  /** Add screenshots, then read what was added.
   *
   * The upload and the reading are two calls on purpose: the pictures belong to
   * the store whether or not the reading succeeds, and a reading that fails is
   * something the operator can see and start again rather than lose. */
  async function addShots(event: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (!files.length || busy || running) return;
    setBusy(true);
    setError("");
    setHint("");
    let added: string[] = [];
    try {
      const result = await api.addImages(storeId, files);
      added = result.added;
      setDetail(result.store);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
    if (added.length) {
      setHint(`已添加 ${added.length} 张截图，正在重读新增的这几张。`);
      await run(["recognize", "clues"]);
    }
  }

  /** Drop one screenshot, and with it whatever only that screenshot showed. */
  async function dropShot(filename: string) {
    if (busy || running) return;
    const warning = `删除「${filename}」？\n仅在这张图中识别到的商品也会移除，之后需要重新生成建议。`;
    if (!window.confirm(warning)) return;
    setBusy(true);
    setError("");
    setHint("");
    try {
      setDetail(await api.removeImage(storeId, filename));
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    if (!job) return;
    try {
      setJob(await api.cancelJob(job.id));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  /** The one product-level lever: everything recognition saw is kept unless
   *  the operator says otherwise. */
  async function setExcluded(name: string, excluded: boolean) {
    if (!clues) return;
    const next = excluded
      ? [...clues.excluded, name]
      : clues.excluded.filter((item) => item !== name);
    setBusy(true);
    setError("");
    try {
      setClues(await api.setExcluded(storeId, next));
      setDetail(await api.store(storeId));
      if (detail?.stages.synthesis) {
        setHint("商品范围已更新，重新生成经营建议后才会用上新名单。");
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  /** Send the whole hand-added list, the way the exclusion list is sent: what
   *  the operator is looking at is what gets saved. */
  async function setProducts(next: CustomProduct[]): Promise<boolean> {
    if (!clues || busy || running) return false;
    setBusy(true);
    setError("");
    try {
      setClues(await api.setProducts(storeId, next));
      setDetail(await api.store(storeId));
      if (detail?.stages.synthesis) {
        setHint("商品名单已更新，重新生成经营建议后才会用上新名单。");
      }
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  }

  const railStages: RailStage[] = useMemo(() => {
    const live = new Map((job?.stages ?? []).map((stage) => [stage.name, stage]));
    return PIPELINE.map((step) => {
      const stage = live.get(step.name);
      const stale = !isLive(job) && detail?.outdated?.includes(step.name);
      if (stage && (!stale || stage.status === "failed")) {
        return {
          name: step.name,
          label: stage.label,
          status: job?.status === "cancelled" && stage.status === "running" ? "pending" : stage.status,
          note: stage.error || stage.detail || "等待中",
          target: stage.status === "ready" ? step.target : "",
        };
      }
      const saved = Boolean(detail?.stages[step.flag]) && !stale;
      return {
        name: step.name,
        label: step.label,
        status: saved ? "ready" : "pending",
        note: saved ? "已有结果，可跳到结果查看" : "等待中",
        target: saved ? step.target : "",
      };
    });
  }, [job, detail]);

  /** The one thing to do next, named for what it will actually run.
   *
   * The page used to offer three buttons whose difference was a fact about the
   * pipeline — which steps touch a model and which do not — so choosing one
   * meant knowing how the pipeline is built. This picks the work instead: the
   * steps behind are the steps that run, and the label says whether that costs
   * anything. Nothing behind means nothing to press, because re-rolling a
   * reading that is already current is not something the page should invite.
   */
  const next = useMemo(() => {
    if (!detail) return null;
    // A backend left running from before this field existed sends nothing here.
    // Reading that as "nothing is behind" costs a button; reading it as an error
    // costs the whole page, which is how the operator ends up on a blank screen
    // with nothing to act on.
    const interrupted = job?.status === "failed" || job?.status === "cancelled" || job?.stages.some((stage) => stage.status === "failed");
    const stages = mergeStages(detail.outdated ?? [], interrupted
      ? (job?.stages ?? []).filter((stage) => stage.status !== "ready").map((stage) => stage.name) : []);
    if (stages.length) return { label: interrupted ? "继续生成" : detail.stages.synthesis ? "更新结果" : "开始生成", stages, title: stageNotice(stages, detail.params) };
    if (draft && !paramsEqual(draft, detail.params)) return { label: "应用设置并更新", stages: [], title: "按当前设置更新结果" };
    return null;
  }, [detail, job, draft]);

  // Starting or updating a recommendation is always an explicit action.
  // Retrieval may include paid reranking, so saving a setting must not run it.

  if (!detail) {
    return (
      <section className="card">
        <p className="muted" role="status">{error || "正在读取店铺…"}</p>
        {error && <button className="btn ghost" onClick={() => load().catch((e: Error) => setError(e.message))}>重新加载</button>}
        <Link className="text-link" to="/">返回首页</Link>
      </section>
    );
  }

  const images = detail.store.images;
  const exportBlocked = running || busy ? "商品仍在生成或更新，完成后即可导出。"
    : draft && !paramsEqual(draft, detail.params) ? "生成设置已修改，请应用设置并更新结果后导出。"
    : detail.export_blocked_reason ?? (detail.outdated?.length ? "资料或设置已变化，请更新结果后导出。" : "");
  const summary = running
    ? `正在生成${job?.seconds ? ` · 已用 ${took(job.seconds)}` : ""}${analysis ? " · 可先阅读店铺分析" : ""}`
    : job?.status === "failed" || job?.status === "cancelled"
      ? "部分步骤未完成，可继续处理"
      : detail.outdated?.length ? "资料或设置已变化，等待更新" : retrieval ? "生成完成 · 查看商品后即可导出" : "等待开始生成";

  return (
    <>
      <p className="crumb">
        <Link to="/">← 所有店铺</Link>
      </p>

      <section className="card store-head">
        <div className="store-head-top">
          <div>
            <h1>{detail.store.store_name}</h1>
            <p className="muted">
              {countryName(detail.store.country)} · {images.length} 张截图 · 已选 {detail.kept_clues.length} 类商品 ·
              已排除 {detail.excluded_clues.length} 类
            </p>
          </div>
          <div className="run-actions">
            {!running && next && (
              <button className="btn" disabled={busy} onClick={() => run(next.stages)}
                title={next.title}>
                {next.label}
              </button>
            )}
          </div>
        </div>
        {/* The screenshots the whole page was built from, kept beside the store
            name rather than at the bottom of it: the分析 is read against them,
            and a run of them at the foot of a long page is no use for that.
            They can also be changed here, because a picture that should have
            been in the first upload is the ordinary case, not a reason to start
            over — and changing them here is what tells the page to reread. */}
        <details className="store-materials"><summary>店铺截图 <span className="muted">{images.length} 张 · 查看或补充</span></summary><div className="shot-strip">
          {images.map((image, index) => (
            <div className="shot" key={image.filename}>
              <button className="shot-open" onClick={() => setShot(index)} title="点击放大查看">
                <img src={api.imageUrl(storeId, image.filename)} alt={image.filename} />
              </button>
              <span>{image.filename}</span>
              <button className="shot-drop" disabled={busy || running} title="删除这张截图" aria-label={`删除 ${image.filename}`}
                onClick={() => dropShot(image.filename)}>×</button>
            </div>
          ))}
          <label className="shot shot-add" title="加一张截图。只会读新加的这张，已有的不会重读。">
            <input type="file" accept="image/*" multiple hidden
              onChange={addShots} disabled={busy || running} />
            <span>＋ 补充截图</span>
          </label>
        </div></details>
      </section>

      {error && (
        <p className="notice error" role="alert" style={{ marginBottom: 16 }}>
          {error}
          <button className="btn ghost small" onClick={() => load().catch((e: Error) => setError(e.message))}>重新加载</button>
        </p>
      )}

      {hint && <p className="notice warn" role="status">{hint}</p>}
      {(job?.status === "failed" || job?.status === "cancelled") && <p className="notice warn" role="status">{job.status === "cancelled" ? "生成已停止" : "生成未完成"}，已完成的内容会保留。点击「继续生成」完成剩余步骤。</p>}
      {/* Only when nothing is running: while a run is in flight the rail above
          already says so, and "please update" would be asking for the update
          that is already happening. */}
      {analysis && !running && Boolean(detail.outdated?.length) && (
        <p className="notice warn" role="status">以下为上次分析结果。资料或设置已有变化，请更新后再使用。</p>
      )}

      <div className="workbench">
        <main className="workbench-main">
          {!analysis && <section className="card analysis-wait" id="analysis-summary" data-running={running}><p className="eyebrow">店铺分析</p><h2>{running ? "正在整理这家店的经营方向" : "准备生成店铺分析"}</h2><p className="muted">分析完成后将在这里展开；场景与商品匹配会接着进行，无需再次操作。</p></section>}
          <details className="workspace-settings"><summary>商品资料与生成设置 <span className="muted">需要调整时展开</span></summary>
          {clues && (
            <details className="card" id="clues">
              <summary>
                {images.length ? ROLE_LABEL : "这家店的商品"} <span className="count">{clues.entries.length} 条</span>
              </summary>
              <div className="clue-list">
                {clues.entries.map((entry) => (
                  <label className="clue pick-clue" data-off={entry.excluded} key={entry.clue}>
                    <input
                      type="checkbox"
                      checked={!entry.excluded}
                      disabled={busy || running}
                      onChange={(e) => setExcluded(entry.clue, !e.target.checked)}
                    />
                    <span>
                      <strong>{entry.clue}</strong>
                      {/* The screenshot's own words, so a translated name can
                          still be matched back to the picture it came from. */}
                      {entry.original && <small className="original">{entry.original}</small>}
                      <small className="meta">
                        <em className={entry.manual ? "role-tag manual" : "role-tag"}>
                          {entry.role || "未标注"}
                        </em>
                        {entry.manual ? "手工填写" : `${entry.image_count} 张截图出现`}
                      </small>
                    </span>
                    <span className="clue-tags">
                      {/* Unticking rules it out of the analysis; this takes it
                          off the list, which is what a typo needs. */}
                      {entry.manual && clues && (
                        <button
                          className="btn ghost small"
                          disabled={busy || running}
                          title="从名单里删掉这条"
                          onClick={(e) => {
                            e.preventDefault();
                            void setProducts(
                              clues.custom.filter((item) => item.name_cn !== entry.clue));
                          }}
                        >
                          删掉
                        </button>
                      )}
                    </span>
                  </label>
                ))}
              </div>

              {/* Recognition only sees the screenshots, and a store sells things
                  nobody photographed. What the operator types here reaches the
                  same list a recognised product does. */}
              <div className="add-product">
                <span className="lbl">补充漏掉的商品</span>
                <div className="add-product-row">
                  <input
                    value={newProduct.name_cn}
                    placeholder="商品中文名（必填）"
                    disabled={busy || running}
                    onChange={(e) => setNewProduct({ ...newProduct, name_cn: e.target.value })}
                  />
                  <input
                    value={newProduct.name_en}
                    placeholder="英文名（选填）"
                    disabled={busy || running}
                    onChange={(e) => setNewProduct({ ...newProduct, name_en: e.target.value })}
                  />
                  <button
                    className="btn ghost small"
                    disabled={busy || running || !newProduct.name_cn.trim()}
                    onClick={async () => {
                      if (!clues) return;
                      const next = [...clues.custom, { name_cn: newProduct.name_cn.trim(), name_en: newProduct.name_en.trim() }];
                      if (await setProducts(next)) setNewProduct({ name_cn: "", name_en: "" });
                    }}
                  >
                    加入名单
                  </button>
                </div>
              </div>

            </details>
          )}

          <ParamsPanel
            storeId={storeId}
            params={detail.params}
            busy={running || busy}
            // Reading the store back is what answers "what did that change",
            // and the answer is the server's to give: `outdated` is derived
            // from the run record, so a locally patched copy of `detail` would
            // show a button that never appears.
            onSaved={async () => { await load(); }}
            onDraft={setDraft}
            onBusyChange={setBusy}
          />
          </details>

          {analysis && (
            <>
              <section className="card" id="analysis-summary">
                <p className="eyebrow">店铺分析 / 01</p><h2>执行结论</h2>
                {analysis.reintroduced.length > 0 && (
                  <p className="notice warn">
                    上一轮结果里有 {analysis.reintroduced.length} 处把已被你排除的商品又写了回来（
                    {analysis.reintroduced.map((item) => item.product_cn).join("、")}）。这些项需要复核，更新后仍需确认是否已排除。
                  </p>
                )}
                <p className="verdict">{analysis.manager_summary.executive_conclusion}</p>
                <h3 className="sub">从哪里切入</h3>
                <p className="evi">{analysis.manager_summary.business_opportunity}</p>
                <h3 className="sub">建议先做这几件事</h3>
                <ol className="actions">
                  {(analysis.manager_summary.recommended_actions ?? []).map((action) => (
                    <li key={action}>{action}</li>
                  ))}
                </ol>
                {/* The picture of the shop sits inside the conclusion rather
                    than beside it: it is what the conclusion was drawn from,
                    and read on its own it is a description with nothing to act
                    on. */}
                <div className="card-part">
                  <h3 className="sub">店铺画像</h3>
                  <Judged section={analysis.store_profile} />
                </div>
              </section>

              <section className="card" id="analysis-current">
                <p className="eyebrow">店铺分析 / 02</p><h2>当前产品结构</h2>
                <Judged section={analysis.current_product_structure} />
              </section>

              {/* One card, two lists: who the shop is talking to, and what those
                  people are doing. They are read against each other — a scene
                  with no audience under it is a guess — but they stay separate
                  headings, because "who" and "when" are different answers. */}
              <section className="card" id="analysis-audience">
                <p className="eyebrow">店铺分析 / 03</p><h2>人群与场景</h2>
                <h3 className="sub">目标人群</h3>
                <Cards items={analysis.audiences.map((item) => ({ name: item.audience_name, description: item.description }))} />
                <div className="card-part" id="analysis-scenes">
                  <h3 className="sub">场景方向</h3>
                  {analysis.scenes.length ? <Cards items={analysis.scenes.map((scene) => ({ name: scene.scene_name, description: `${scene.audience} · ${scene.user_need}` }))} /> : <p className="muted">场景仍在生成，完成后会自动显示。</p>}
                </div>
              </section>

              {/* Where the shop is going, and what to do about it this week. The
                  structure keeps its own body and its推进顺序; the strategy is
                  read right after it because it is the same decision. */}
              <section className="card" id="analysis-future">
                <p className="eyebrow">店铺分析 / 04</p><h2>未来产品结构与运营策略</h2>
                <Judged section={analysis.future_product_structure} />
                <h4 className="sub">推进顺序</h4>
                <ol className="actions">
                  {(analysis.future_product_structure.priority_order ?? []).map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ol>
                <div className="card-part" id="analysis-strategy">
                  <h3 className="sub">运营策略</h3>
                  <Cards
                    items={analysis.operation_strategy.map((strategy) => ({
                      name: strategy.strategy_name,
                      description: strategy.description,
                    }))}
                  />
                </div>
              </section>

              <SceneWorkbench key={storeId} storeId={storeId} scenes={analysis.scenes} retrieval={retrieval} exportBlocked={exportBlocked} />
            </>
          )}

        </main>

        {/* Where the run has got to, and what there is on the page to read: the
            two questions the operator asks while waiting, in the one column
            that is not moving under them. */}
        <aside className="workspace-side">
          <StageRail stages={railStages} summary={summary} running={running} onCancel={cancel} />
          <nav className="workspace-nav" aria-label="本页导航"><p className="eyebrow">本页内容</p>{[
            ["analysis-summary", "执行结论"], ["analysis-current", "当前产品结构"], ["analysis-audience", "人群与场景"], ["analysis-future", "未来产品结构与运营策略"], ["sku-recommendations", "场景与商品"], ["export-section", "导出 Excel"],
          ].map(([id, label], index) => analysis ? <a key={id} href={`#${id}`} className={id === "export-section" ? "nav-export" : ""}><span>{String(index + 1).padStart(2, "0")}</span>{label}{id === "export-section" && <span aria-hidden="true">↓</span>}</a> : <span className="nav-pending" key={id}>{label}</span>)}<p className="muted">{analysis ? "点击章节，快速定位" : "分析生成后即可查看"}</p></nav>
        </aside>
      </div>

      <PhotoViewer
        photos={images.map((image) => ({
          src: api.imageUrl(storeId, image.filename),
          label: image.filename,
        }))}
        index={shot}
        onIndex={setShot}
        onClose={() => setShot(null)}
      />
    </>
  );
}
