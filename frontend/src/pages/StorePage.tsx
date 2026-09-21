import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  api,
  type Analysis,
  type Clues,
  type Job,
  type Judgement,
  type Params,
  type Retrieval,
  type StoreDetail,
} from "../api";
import ParamsPanel from "../components/ParamsPanel";
import BusinessEvidence from "../components/BusinessEvidence";
import ConfirmationPanel from "../components/ConfirmationPanel";
import SceneWorkbench from "../components/SceneWorkbench";

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

  const load = useCallback(async () => {
    const data = await api.store(storeId);
    if (shown.current !== storeId) return;
    const [nextClues, nextAnalysis, nextRetrieval] = await Promise.all([
      data.stages.clues ? api.clues(storeId) : Promise.resolve(null),
      data.stages.synthesis ? api.analysis(storeId) : Promise.resolve(null),
      data.stages.retrieval ? api.retrieval(storeId) : Promise.resolve(null),
    ]);
    if (shown.current !== storeId) return;
    setDetail(data);
    setJob(data.job);
    setClues(nextClues);
    setAnalysis(nextAnalysis);
    setRetrieval(nextRetrieval);
    setError("");
  }, [storeId]);

  useEffect(() => {
    shown.current = storeId;
    setDetail(null); setAnalysis(null); setRetrieval(null); setClues(null); setJob(null); setError("");
    load().catch((e: Error) => { if (shown.current === storeId) setError(e.message); });
  }, [load, storeId]);

  const running = isLive(job);
  useEffect(() => {
    if (!running || !job) return;
    const timer = setInterval(async () => {
      try {
        const next = await api.job(job.id);
        setJob(next);
        if (!isLive(next) && shown.current === storeId) await load();
      } catch (e) {
        setError((e as Error).message);
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [running, job, storeId, load]);

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

  if (!detail) {
    return (
      <section className="card">
        <p className="muted">{error || "正在读取店铺…"}</p>
      </section>
    );
  }

  const images = detail.store.images;
  const scored = retrieval?.inventory === "available";

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
          {running ? (
            <button className="btn ghost" onClick={cancel}>
              停止
            </button>
          ) : (
            <>
              {detail.confirmation?.confirmed && <button className="btn" disabled={busy} onClick={() => run()}>
                {detail.stages.synthesis ? "更新分析" : "开始分析"}
              </button>}

            </>
          )}
        </div>
      </section>

      {error && (
        <p className="notice error" style={{ marginBottom: 16 }}>
          {error}
        </p>
      )}

      {job && (
        <details className="card" open={running}>
          <summary>
            进度
            <span className="count">
              {job.status === "ready"
                ? "上次已完成"
                : job.status === "failed"
                  ? "上次失败"
                  : job.status === "cancelled"
                    ? "已停止"
                    : "进行中"}
              {job.seconds ? ` · 全程 ${took(job.seconds)}` : ""}
            </span>
          </summary>
          <div className="stage-list">
            {job.stages.map((stage) => (
              <div className="stage" key={stage.name} data-status={stage.status}>
                <span className="dot">{stage.status === "running" ? <i className="spinner" /> : ""}</span>
                <div>
                  <strong>
                    {stage.label}
                  </strong>
                  <small>{stage.error || stage.detail || "等待中"}</small>
                </div>
                <span className="state">
                  {stage.status === "ready"
                    ? `完成${stage.seconds != null ? ` · ${took(stage.seconds)}` : ""}`
                    : stage.status === "running"
                      ? `进行中${stage.seconds != null ? ` · ${took(stage.seconds)}` : ""}`
                      : stage.status === "failed"
                        ? `失败${stage.seconds != null ? ` · ${took(stage.seconds)}` : ""}`
                        : "等待"}
                </span>
              </div>
            ))}
          </div>

        </details>
      )}

      {clues && (
        <details className="card" open={!detail.confirmation?.confirmed}>
          <summary>
            确认识别到的商品 <span className="count">{clues.entries.length} 条</span>
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

      {detail.stages.recognized && detail.confirmation?.confirmed && (
        <ParamsPanel
          storeId={storeId}
          params={detail.params}
          busy={running || busy}
          canRerunLocally={detail.stages.expansions}
          onSaved={(params) => setDetail({ ...detail, params })}
          onRun={run}
        />
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
    </>
  );
}
