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

  const load = useCallback(async () => {
    const data = await api.store(storeId);
    setDetail(data);
    setJob(data.job);
    setClues(data.stages.clues ? await api.clues(storeId) : null);
    // The assembled reading is what there is to show. The per-stage files
    // behind it belong to the job runner — a store read before the analysis
    // was split still has a complete answer, and hiding it because a file the
    // runner uses is missing would hide work the operator paid for.
    setAnalysis(data.stages.synthesis ? await api.analysis(storeId) : null);
    setRetrieval(data.stages.retrieval ? await api.retrieval(storeId) : null);
    setError("");
  }, [storeId]);

  useEffect(() => {
    shown.current = storeId;
    load().catch((e: Error) => setError(e.message));
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

  async function run(stages?: string[], params?: Params) {
    setError("");
    setHint("");
    try {
      setJob(await api.startJob(storeId, stages, params));
    } catch (e) {
      setError((e as Error).message);
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
              <button className="btn" onClick={() => run()}>
                {detail.stages.synthesis ? "整体重跑" : "开始生成"}
              </button>
              {detail.stages.recognized && (
                <button
                  className="btn ghost"
                  onClick={() =>
                    run(["clues", "scenes", "products", "synthesis", "expand", "retrieval", "rerank"])
                  }
                  title="用改动后的排除名单重新生成场景，不重新识别截图，省掉最贵的那一步"
                >
                  {detail.stages.scenes ? "重新生成场景" : "生成场景"}
                </button>
              )}
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
        <section className="card">
          <h2>
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
          </h2>
          <div className="stage-list">
            {job.stages.map((stage) => (
              <div className="stage" key={stage.name} data-status={stage.status}>
                <span className="dot">{stage.status === "running" ? <i className="spinner" /> : ""}</span>
                <div>
                  <strong>
                    {stage.label}
                    {stage.paid && <em className="tag paid">花钱</em>}
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
          {job.usage.total_tokens ? (
            <p className="muted" style={{ marginTop: 10 }}>
              本次共消耗 {job.usage.total_tokens} tokens
            </p>
          ) : null}
        </section>
      )}

      {clues && (
        <section className="card">
          <h2>
            截图里识别到的商品 <span className="count">{clues.entries.length} 条</span>
          </h2>
          <div className="clue-list">
            {clues.entries.map((entry) => (
              <label className="clue pick-clue" data-off={entry.excluded} key={entry.clue}>
                <input
                  type="checkbox"
                  checked={!entry.excluded}
                  disabled={busy}
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
        </section>
      )}

      {detail.stages.recognized && (
        <ParamsPanel
          storeId={storeId}
          params={detail.params}
          busy={running}
          canRerunLocally={detail.stages.expansions}
          onSaved={(params) => setDetail({ ...detail, params })}
          onRun={run}
        />
      )}

      {analysis && (
        <>
          <section className="card">
            <h2>执行结论</h2>
            {analysis.reintroduced.length > 0 && (
              <p className="notice warn">
                上一轮结果里有 {analysis.reintroduced.length} 处把已被你排除的商品又写了回来（
                {analysis.reintroduced.map((item) => item.product_cn).join("、")}）。下一轮会避免。
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

          <section className="card">
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

          <section className="card">
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

          <SceneWorkbench storeId={storeId} scenes={analysis.scenes} retrieval={retrieval} />

          {!retrieval && (
            <section className="card">
              <p className="notice warn">
                场景已经生成，但还没有检索产品库。点上面的「重新生成场景」，或在参数里点「重跑召回」。
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
        <p className="muted">本次召回未按 {retrieval.country} 的可发库存过滤。</p>
      )}
    </>
  );
}
