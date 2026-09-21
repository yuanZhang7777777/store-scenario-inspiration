import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  api,
  type Analysis,
  type Clues,
  type Job,
  type Judgement,
  type Params,
  type Retrieval,
  type StageProgress,
  type StoreDetail,
} from "../api";
import ParamsPanel from "../components/ParamsPanel";
import BusinessEvidence from "../components/BusinessEvidence";
import ConfirmationPanel from "../components/ConfirmationPanel";
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
  { name: "clues", label: "应用排除名单", target: "clues", flag: "clues" },
  { name: "scenes", label: "生成使用场景", target: "sku-recommendations", flag: "scenes" },
  { name: "products", label: "为每个场景列商品", target: "sku-recommendations", flag: "products" },
  { name: "synthesis", label: "写店铺结论与人群策略", target: "analysis-summary", flag: "synthesis" },
  { name: "expand", label: "扩写商品检索词", target: "", flag: "expansions" },
  { name: "retrieval", label: "召回候选 SKU", target: "sku-recommendations", flag: "retrieval" },
  { name: "rerank", label: "给候选标相关 / 不相关", target: "sku-recommendations", flag: "rerank" },
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
      <p className="evi">
        <span className="lbl">依据</span>
        {section.evidence}
      </p>
    </div>
  );
}

function Cards({ items }: { items: { name: string; description: string; evidence: string }[] }) {
  return (
    <div className="people-grid">
      {items.map((item) => (
        <article className="person" key={item.name}>
          <h3>{item.name}</h3>
          <p>{item.description}</p>
          <p className="muted">{item.evidence}</p>
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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [hint, setHint] = useState("");
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
    drawn.current = { id: "", done: new Set() };
    load().catch((e: Error) => { if (shown.current === storeId) setError(e.message); });
  }, [load, storeId]);

  const running = isLive(job);
  const jobId = job?.id ?? "";
  useEffect(() => {
    if (!running || !jobId) return;
    const timer = setInterval(async () => {
      try {
        const next = await api.job(jobId);
        setJob(next);
        if (shown.current !== storeId) return;
        if (drawn.current.id !== next.id) drawn.current = { id: next.id, done: new Set() };
        const landed = next.stages.filter((stage) => stage.status === "ready" && !drawn.current.done.has(stage.name));
        if (landed.length) {
          landed.forEach((stage) => drawn.current.done.add(stage.name));
          const artifacts = new Set(landed.map((stage) => ARTIFACTS[stage.name]).filter(Boolean));
          await refresh(artifacts.size ? artifacts : null);
        }
        if (!isLive(next)) await load();
      } catch (e) {
        setError((e as Error).message);
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [running, jobId, storeId, refresh, load]);

  async function run(stages?: string[], params?: Params): Promise<boolean> {
    if (requesting.current || isLive(job) || busy) return false;
    const recognitionOnly = Boolean(stages?.length) && stages!.every((stage) => ["recognize", "clues"].includes(stage));
    if (!detail?.confirmation?.confirmed && !recognitionOnly) { setError("请先确认店铺信息。"); return false; }
    requesting.current = true;
    setBusy(true);
    setError("");
    setHint("");
    const nextStages = stages ?? (detail?.stages.recognized
      ? ["clues", "scenes", "products", "synthesis", "expand", "retrieval", "rerank"]
      : undefined);
    try {
      const nextJob = await api.startJob(storeId, nextStages, params);
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
        setHint("排除名单已更新。上面的场景还是按旧名单生成的，重新生成场景才会用上新的。");
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const railStages: RailStage[] = useMemo(() => {
    const live = new Map((job?.stages ?? []).map((stage) => [stage.name, stage]));
    return PIPELINE.map((step) => {
      const stage = live.get(step.name);
      if (stage) {
        return {
          name: step.name,
          label: stage.label,
          status: stage.status,
          note: stage.error || stage.detail || "等待中",
          target: stage.status === "ready" ? step.target : "",
        };
      }
      const saved = Boolean(detail?.stages[step.flag]);
      return {
        name: step.name,
        label: step.label,
        status: saved ? "ready" : "pending",
        note: saved ? "已有结果，可跳到结果查看" : "等待中",
        target: saved ? step.target : "",
      };
    });
  }, [job, detail]);

  if (!detail) {
    return (
      <section className="card">
        <p className="muted">{error || "正在读取店铺…"}</p>
      </section>
    );
  }

  const images = detail.store.images;
  const scored = retrieval?.inventory === "available";
  const summary = job
    ? `${job.status === "ready" ? "上次已完成" : job.status === "failed" ? "上次失败"
      : job.status === "cancelled" ? "已停止" : "进行中"}${job.seconds ? ` · 全程 ${took(job.seconds)}` : ""}`
    : detail.confirmation?.confirmed ? "分析尚未开始" : "确认店铺信息后开始分析";

  return (
    <>
      <p className="crumb">
        <Link to="/">← 所有店铺</Link>
      </p>

      <section className="card store-head">
        <div>
          <h2>{detail.store.store_name}</h2>
          <p className="muted">
            {detail.store.country} · {images.length} 张截图 · 进入场景生成 {detail.kept_clues.length} 条 ·
            已排除 {detail.excluded_clues.length} 条
          </p>
        </div>
        <div className="run-actions">
          {!running && detail.confirmation?.confirmed && (
            <button className="btn" disabled={busy} onClick={() => run()}>
              {detail.stages.synthesis ? "更新分析" : "开始分析"}
            </button>
          )}
        </div>
      </section>

      {error && (
        <p className="notice error" style={{ marginBottom: 16 }}>
          {error}
        </p>
      )}

      <div className="workbench">
        <main className="workbench-main">
          {clues && (
            <details className="card" id="clues" open={!detail.confirmation?.confirmed}>
              <summary>
                {ROLE_LABEL} <span className="count">{clues.entries.length} 条</span>
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
                      <small>
                        {entry.image_count} 张截图出现 · 把握 {entry.confidence.toFixed(2)}
                      </small>
                    </span>
                    <em className="role-tag">{entry.role || "未标注"}</em>
                  </label>
                ))}
              </div>

              {hint && (
                <p className="notice warn" style={{ marginTop: 14 }}>
                  {hint}
                </p>
              )}
            </details>
          )}

          {!running && detail.confirmation?.state === "not_ready" && (
            <section className="card"><p>资料识别尚未完成。</p>
              <button className="btn" disabled={busy} onClick={() => run(["recognize", "clues"])}>重新识别</button>
            </section>
          )}

          {detail.stages.recognized && (
            <ParamsPanel
              storeId={storeId}
              params={detail.params}
              busy={running || busy}
              confirmed={Boolean(detail.confirmation?.confirmed)}
              canRerunLocally={detail.stages.expansions}
              onSaved={(params) => setDetail({ ...detail, params })}
              onRun={run}
            />
          )}

          {detail.stages.recognized && !running && (
            <ConfirmationPanel storeId={storeId} status={detail.confirmation} busy={busy}
              onStarted={(nextJob) => { setJob(nextJob); void load().catch((e: Error) => setError(e.message)); }} />
          )}

          {analysis && (!detail.confirmation?.confirmed || running) && (
            <p className="notice warn">以下保留了已有结果；本次资料尚未完成确认或分析，请以本轮完成后的结果为准。</p>
          )}

          {analysis && (
            <>
              <nav className="analysis-nav" aria-label="报告导航">
                <a href="#analysis-summary">核心结论</a><a href="#analysis-structure">店铺与产品结构</a><a href="#analysis-strategy">人群与策略</a><a href="#sku-recommendations">场景与商品</a>
              </nav>
              <section className="card" id="analysis-summary">
                <h2>核心结论</h2>
                {analysis.reintroduced.length > 0 && (
                  <p className="notice warn">
                    上一轮结果里有 {analysis.reintroduced.length} 处把已被你排除的商品又写了回来（
                    {analysis.reintroduced.map((item) => item.product_cn).join("、")}）。这些项需要复核，更新后仍需确认是否已排除。
                  </p>
                )}
                <p className="verdict">{analysis.manager_summary.executive_conclusion}</p>
                <h3 className="sub">从哪里切入</h3>
                <p className="evi">{analysis.manager_summary.business_opportunity}</p>
                <h3 className="sub">建议动作</h3>
                <ol className="actions">
                  {analysis.manager_summary.recommended_actions.map((action) => (
                    <li key={action}>{action}</li>
                  ))}
                </ol>
                <p className="boundary">{analysis.manager_summary.decision_boundary}</p>
              </section>

              <BusinessEvidence context={analysis.business_context} />
              <section className="card" id="analysis-structure">
                <h2>店铺与产品结构</h2>
                <h3 className="sub">店铺画像</h3>
                <Judged section={analysis.store_profile} />
                <h3 className="sub">当前产品结构</h3>
                <Judged section={analysis.current_product_structure} />
                <h3 className="sub">未来产品结构</h3>
                <Judged section={analysis.future_product_structure} />
                <h4 className="sub">推进顺序</h4>
                <ol className="actions">
                  {analysis.future_product_structure.priority_order.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ol>
              </section>

              <section className="card" id="analysis-strategy">
                <h2>
                  人群与运营策略
                  <span className="count">
                    {analysis.audiences.length} 个人群 · {analysis.operation_strategy.length} 条策略
                  </span>
                </h2>
                <h3 className="sub">人群</h3>
                <Cards
                  items={analysis.audiences.map((audience) => ({
                    name: audience.audience_name,
                    description: audience.description,
                    evidence: audience.evidence,
                  }))}
                />
                <h3 className="sub">运营策略</h3>
                <Cards
                  items={analysis.operation_strategy.map((strategy) => ({
                    name: strategy.strategy_name,
                    description: strategy.description,
                    evidence: strategy.evidence,
                  }))}
                />
              </section>

              <div id="sku-recommendations"><SceneWorkbench key={storeId} storeId={storeId} scenes={analysis.scenes} retrieval={retrieval} /></div>

              {!retrieval && (
                <section className="card">
                  <p className="notice warn">
                    场景分析已完成，商品匹配尚未完成。可更新分析继续处理；已有结果会保留。
                  </p>
                </section>
              )}
            </>
          )}

          <section className="card">
            <h2>店铺截图</h2>
            <div className="thumbs">
              {images.map((image) => (
                <div className="thumb" key={image.filename}>
                  <img src={api.imageUrl(storeId, image.filename)} alt={image.filename} />
                  <span>{image.filename}</span>
                </div>
              ))}
            </div>
          </section>

          {scored === false && retrieval && (
            <p className="muted">本次未完成 {retrieval.country} 的库存核验，不能据此判断商品有货或无货。</p>
          )}
        </main>

        <StageRail stages={railStages} summary={summary} running={running} onCancel={cancel} />
      </div>
    </>
  );
}
