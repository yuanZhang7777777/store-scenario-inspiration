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
import BusinessEvidence from "../components/BusinessEvidence";
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
        if (shown.current !== storeId) return;
        setJob(next);
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
        if (stagesToRun.includes("recognize")) {
          throw new Error("截图有新内容，请先重读之后再更新建议。");
        }
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
    // Reading comes first: everything below it is written from what it found.
    if (reading.length) {
      return {
        label: "识别新增截图",
        stages: reading,
        title: "仅识别新增截图，会调用模型。",
      };
    }
    if (!detail.stages.synthesis) {
      return {
        label: "生成经营建议",
        stages: ["synthesis", "scenes", "products", "expand", "retrieval", "rerank"],
        title: "按当前商品名单生成场景和商品匹配。会调用模型，费用以当前服务配置为准。",
      };
    }
    if (writing.length || recall.length) {
      const stages = [...writing, ...recall];
      return {
        label: writing.length ? "更新经营建议" : "更新推荐商品",
        stages,
        title: stageNotice(stages, detail.params),
      };
    }
    return null;
  }, [detail]);

  // Starting or updating a recommendation is always an explicit action.
  // Retrieval may include paid reranking, so saving a setting must not run it.

  if (!detail) {
    return (
      <section className="card">
        <p className="muted">{error || "正在读取店铺…"}</p>
      </section>
    );
  }

  const images = detail.store.images;
  const scored = retrieval?.inventory === "available";
  const summary = running
    ? `正在处理${job?.seconds ? ` · 已用 ${took(job.seconds)}` : ""}`
    : job?.status === "failed" || job?.status === "cancelled"
      ? "部分步骤未完成，可继续处理"
      : detail.outdated?.length ? "有结果需要更新" : analysis ? "经营建议已生成" : "等待生成经营建议";

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
        <div className="shot-strip">
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
        </div>
      </section>

      {error && (
        <p className="notice error" style={{ marginBottom: 16 }}>
          {error}
        </p>
      )}

      {hint && <p className="notice warn" role="status">{hint}</p>}
      {analysis && (running || Boolean(detail.outdated?.length)) && (
        <p className="notice warn" role="status">{running ? "正在更新建议，页面中的结果可能尚未全部更新。" : "以下为上次分析结果。资料或设置已有变化，更新后再作为当前建议使用。"}</p>
      )}

      <div className="workbench">
        <main className="workbench-main">
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
                <h3 className="sub">建议先做这几件事</h3>
                <ol className="actions">
                  {analysis.manager_summary.recommended_actions.map((action) => (
                    <li key={action}>{action}</li>
                  ))}
                </ol>
                {analysis.manager_summary.decision_boundary && (
                  <details className="operator-scope"><summary>建议的适用范围</summary><p className="muted">{analysis.manager_summary.decision_boundary}</p></details>
                )}
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
                    经营建议已生成，商品匹配尚未完成。已有建议可以先查看。
                  </p>
                </section>
              )}
            </>
          )}

          {scored === false && retrieval && (
            <p className="muted">{countryName(retrieval.country)} 库存暂不可用，请在上架前核实。</p>
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
