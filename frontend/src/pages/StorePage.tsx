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
import ParamsPanel from "../components/ParamsPanel";
import BusinessEvidence from "../components/BusinessEvidence";
import ConfirmationPanel from "../components/ConfirmationPanel";
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
  { name: "scenes", label: "生成使用场景", target: "sku-recommendations", flag: "scenes" },
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
      ? ["clues", "synthesis", "scenes", "products", "expand", "retrieval", "rerank"]
      : undefined);
    try {
      const nextJob = await api.startJob(storeId, nextStages, params ?? draft ?? undefined);
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
      setHint(`已加 ${added.length} 张截图，正在读新加的（会调用模型）。读完请重新确认店铺信息。`);
      await run(["recognize", "clues"]);
    }
  }

  /** Drop one screenshot, and with it whatever only that screenshot showed. */
  async function dropShot(filename: string) {
    if (busy || running) return;
    const warning = `删掉「${filename}」？\n只有这张图里出现过的商品会跟着消失，别的图里也有的会留下。\n这一步在本机完成，不花钱。`;
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
        setHint("排除名单已更新。上面的场景还是按旧名单生成的，重新生成场景才会用上新的。");
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  /** Send the whole hand-added list, the way the exclusion list is sent: what
   *  the operator is looking at is what gets saved. */
  async function setProducts(next: CustomProduct[]) {
    if (!clues || busy) return;
    setBusy(true);
    setError("");
    try {
      setClues(await api.setProducts(storeId, next));
      setDetail(await api.store(storeId));
      if (detail?.stages.synthesis) {
        setHint("商品名单已更新。上面的场景还是按旧名单生成的，重新生成场景才会用上新的。");
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
    const behind = detail.outdated ?? [];
    const reading = behind.filter((name) => name === "recognize" || name === "clues");
    const writing = behind.filter((name) =>
      ["synthesis", "scenes", "products", "expand"].includes(name));
    const recall = behind.filter((name) => name === "retrieval" || name === "rerank");
    // Reading comes first and needs no confirmation: the operator cannot agree
    // to facts the store has not been read for yet.
    if (reading.length) {
      return {
        label: "读取截图",
        stages: reading,
        title: "把还没有读过的截图读一遍。会调用视觉模型，只读新加的那些，读过的不会重读。",
      };
    }
    if (!detail.confirmation?.confirmed) return null;
    if (!detail.stages.synthesis) {
      return {
        label: "开始分析",
        stages: ["synthesis", "scenes", "products", "expand", "retrieval", "rerank"],
        title: "按已确认的商品名单生成场景和商品匹配。这一步会调用模型。",
      };
    }
    if (writing.length || recall.length) {
      const stages = [...writing, ...recall];
      return {
        label: writing.length ? "更新分析" : "重算商品",
        stages,
        title: writing.length
          ? "按改动重新写店铺结论、场景和商品，并重新匹配。这一步会调用模型。"
          : "按改动重新匹配商品。这一步在本机完成，不花钱。",
      };
    }
    return null;
  }, [detail]);

  /** The search is local and free, so a setting of its own that has changed is
   *  applied without being asked about. Once per store per shape of the change:
   *  a run that fails has to be looked at, not restarted on every render. */
  const rebuilt = useRef<{ store: string; signature: string }>({ store: "", signature: "" });
  useEffect(() => {
    if (!detail || running || busy) return;
    const behind = detail.outdated ?? [];
    if (!behind.length || !behind.every((name) => name === "retrieval" || name === "rerank")) return;
    if (!detail.confirmation?.confirmed) return;
    const signature = behind.join(",");
    if (rebuilt.current.store === storeId && rebuilt.current.signature === signature) return;
    rebuilt.current = { store: storeId, signature };
    setHint("设置改过了，正在按新设置重算商品（这一步不花钱）。");
    run(["retrieval", "rerank"]);
  }, [detail, running, busy, storeId]);  // eslint-disable-line react-hooks/exhaustive-deps

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
        <div className="store-head-top">
          <div>
            <h2>{detail.store.store_name}</h2>
            <p className="muted">
              {countryName(detail.store.country)} · {images.length} 张截图 · 进入场景生成 {detail.kept_clues.length} 条 ·
              已排除 {detail.excluded_clues.length} 条
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
        <div className="shot-strip">
          {images.map((image, index) => (
            <div className="shot" key={image.filename}>
              <button className="shot-open" onClick={() => setShot(index)} title="点击放大查看">
                <img src={api.imageUrl(storeId, image.filename)} alt={image.filename} />
              </button>
              <span>{image.filename}</span>
              <button className="shot-drop" disabled={busy} title="删掉这张截图"
                onClick={() => dropShot(image.filename)}>×</button>
            </div>
          ))}
          <label className="shot shot-add" title="加一张截图。只会读新加的这张，已有的不会重读。">
            <input type="file" accept="image/*" multiple hidden
              onChange={addShots} disabled={busy} />
            <span>＋ 加图</span>
          </label>
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
                      {/* The screenshot's own words, so a translated name can
                          still be matched back to the picture it came from. */}
                      {entry.original && <small className="original">{entry.original}</small>}
                      <small className="meta">
                        <em className={entry.manual ? "role-tag manual" : "role-tag"}>
                          {entry.role || "未标注"}
                        </em>
                        {entry.image_count} 张截图出现
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
                <span className="lbl">新增店内商品</span>
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
                    onClick={() => {
                      if (!clues) return;
                      const next = [...clues.custom, newProduct];
                      setNewProduct({ name_cn: "", name_en: "" });
                      void setProducts(next);
                    }}
                  >
                    加入名单
                  </button>
                </div>
              </div>

              {hint && (
                <p className="notice warn" style={{ marginTop: 14 }}>
                  {hint}
                </p>
              )}
            </details>
          )}

          {detail.stages.recognized && (
            <ParamsPanel
              storeId={storeId}
              params={detail.params}
              busy={running || busy}
              confirmed={Boolean(detail.confirmation?.confirmed)}
              // Reading the store back is what answers "what did that change",
              // and the answer is the server's to give: `outdated` is derived
              // from the run record, so a locally patched copy of `detail` would
              // show a button that never appears.
              onSaved={() => { void load().catch((e: Error) => setError(e.message)); }}
              onDraft={setDraft}
            />
          )}

          {detail.stages.recognized && !running && (
            <ConfirmationPanel storeId={storeId} status={detail.confirmation} busy={busy}
              onStarted={(nextJob) => { setJob(nextJob); void load().catch((e: Error) => setError(e.message)); }} />
          )}

          {analysis && (
            <>
              <nav className="analysis-nav" aria-label="报告导航">
                <a href="#analysis-summary">核心结论</a><a href="#analysis-structure">店铺与产品结构</a><a href="#analysis-strategy">人群与策略</a><a href="#sku-recommendations">场景与可选商品</a>
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
                  }))}
                />
                <h3 className="sub">运营策略</h3>
                <Cards
                  items={analysis.operation_strategy.map((strategy) => ({
                    name: strategy.strategy_name,
                    description: strategy.description,
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

          {scored === false && retrieval && (
            <p className="muted">这次没查到 {countryName(retrieval.country)} 的库存，不能据此判断商品有货或无货。</p>
          )}
        </main>

        <StageRail stages={railStages} summary={summary} running={running} onCancel={cancel} />
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
