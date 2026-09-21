import { useEffect, useMemo, useState } from "react";
import { selectionKey, readSelection, normalizedSelections } from "../selection";

import { api, type Candidate, type Pick, type Retrieval, type RetrievalProduct, type Scene } from "../api";

const CHANNEL: Record<string, string> = {
  cn_keyword: "中·词",
  cn_vector: "中·向量",
  en_keyword: "英·词",
  en_vector: "英·向量",
};

/* One question, two answers: is this product something the scene can sell. The
   model is not asked to grade how close it is — "same" only ever meant "more
   related than related", and the operator never acted on that difference. What
   they do act on is the no, and it is a suspicion rather than a finding, so the
   label says so without a second tier to explain. */
const VERDICT: Record<string, string> = { related: "相关", unrelated: "不相关" };
const VERDICT_TITLE: Record<string, string> = {
  related: "模型认为该商品适用于此场景，不代表销售效果或上架条件已核验。",
  unrelated: "模型认为该商品与场景不相关，可人工复核后选择。",
};

/** Separates the three parts of a selection without colliding with real names. */
function selKey(scene: string, product: string, sku: string): string {
  return selectionKey(scene, product, sku);
}

/**
 * How much of this row the target country can actually ship.
 *
 * The number is the point: at the top of a ranked list the candidates are all
 * about as relevant, and what separates them is whether there are 2 of them or
 * 200. It used to sit in a hover tooltip, where nobody picking forty rows would
 * ever read it.
 *
 * The words match the sheet's stock column row for row — a candidate read here
 * as "TH 可发 6" must not print there as something else.
 */
function stockLabel(candidate: Candidate, country: string): { text: string; yes: boolean } {
  if (candidate.country_available == null) return { text: "库存未核验", yes: false };
  if (!candidate.country_available) return { text: `${country} 无货`, yes: false };
  const quantity = candidate.country_available_quantity;
  return typeof quantity === "number"
    ? { text: `${country} 可发 ${quantity}`, yes: true }
    : { text: `${country} 有货`, yes: true };
}

function storageKey(storeId: string): string {
  return `ssi.picked.${storeId}`;
}

function loadSet(key: string): Set<string> {
  try {
    const raw = JSON.parse(localStorage.getItem(key) ?? "[]");
    const values = normalizedSelections(raw);
    try { if (!localStorage.getItem(key + ".backup-v1")) localStorage.setItem(key + ".backup-v1", JSON.stringify(raw)); } catch { /* Original remains available if persistence is blocked. */ }
    return values;
  } catch {
    return new Set();
  }
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    /* Older or locked-down browsers refuse the async clipboard; the textarea
       trick still works there, and the operator needs the copy either way. */
  }
  try {
    const box = document.createElement("textarea");
    box.value = text;
    box.style.position = "fixed";
    box.style.opacity = "0";
    document.body.appendChild(box);
    box.select();
    const ok = document.execCommand("copy");
    box.remove();
    return ok;
  } catch {
    return false;
  }
}

/**
 * Where the search itself had a taken-away row.
 *
 * Everything in that group carries the same verdict — one question per scene,
 * one "not related" for all of them — so the label cannot order it, and the only
 * thing left to read the list by is how close the recall thought each row was.
 */
function byRecall(candidate: Candidate): number {
  return candidate.recall_rank ?? candidate.rank;
}

/** Undo `selKey`, so the ticked rows can be sent back as the names they are. */
function parseSelKey(key: string): Pick {
  const value = readSelection(key);
  if (!value) throw new Error("选择记录已失效，请重新勾选商品。");
  return value;
}

interface Props {
  storeId: string;
  scenes: Scene[];
  retrieval: Retrieval | null;
}

export default function SceneWorkbench({ storeId, scenes, retrieval }: Props) {
  const [open, setOpen] = useState("");
  const [picked, setPicked] = useState<Set<string>>(() => loadSet(storageKey(storeId)));
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  // On by default, because the same SKU picked under several roles is one
  // product and the sheet wrote it out once per role. Turning it off leaves the
  // report alone, which is what someone reading role by role wants.
  const [dedupe, setDedupe] = useState(true);

  useEffect(() => {
    setPicked(loadSet(storageKey(storeId)));
  }, [storeId]);

  useEffect(() => {
    try { localStorage.setItem(storageKey(storeId), JSON.stringify([...picked])); }
    catch { setNote("浏览器未能保存勾选记录，请在离开前导出清单。"); }
  }, [picked, storeId]);

  const byScene = useMemo(() => {
    const map = new Map<string, RetrievalProduct>();
    for (const item of retrieval?.scenes ?? []) {
      map.set(selKey(item.scene_name, item.product_cn, ""), item);
    }
    return map;
  }, [retrieval]);

  const adopted = useMemo(() => {
    const bySku = new Map<string, { row: Candidate; where: string[] }>();
    for (const item of retrieval?.scenes ?? []) {
      // Taken-away rows are pickable too — the operator's call, not the model's.
      const rows = [...(item.candidates ?? []), ...(item.dropped ?? [])];
      for (const candidate of rows) {
        if (!picked.has(selKey(item.scene_name, item.product_cn, candidate.main_sku))) continue;
        const entry = bySku.get(candidate.main_sku) ?? { row: candidate, where: [] };
        entry.where.push(`${item.scene_name} · ${item.product_cn}`);
        bySku.set(candidate.main_sku, entry);
      }
    }
    return [...bySku.entries()].map(([sku, entry]) => ({ sku, ...entry }));
  }, [retrieval, picked]);

  function toggle(scene: string, product: string, sku: string) {
    const key = selKey(scene, product, sku);
    setPicked((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  /** "Select this row and everything above it" — above means at least as relevant. */
  function selectAbove(item: RetrievalProduct, upTo: number) {
    const rows = item.candidates ?? [];
    setPicked((current) => {
      const next = new Set(current);
      for (const candidate of rows) {
        if (candidate.rank <= upTo) {
          next.add(selKey(item.scene_name, item.product_cn, candidate.main_sku));
        }
      }
      return next;
    });
  }

  /**
   * One candidate row, the same in both groups.
   *
   * A row the model took away is drawn exactly like one it left: same name,
   * same stock, same tick box landing in the same list. The only difference is
   * that the group it sits in already says why it is not above.
   */
  function row(item: RetrievalProduct, candidate: Candidate, taken: boolean) {
    const on = picked.has(selKey(item.scene_name, item.product_cn, candidate.main_sku));
    const stock = stockLabel(candidate, country);
    return (
      <li className="pick" data-on={on} key={candidate.main_sku}>
        <input
          type="checkbox"
          checked={on}
          onChange={() => toggle(item.scene_name, item.product_cn, candidate.main_sku)}
        />
        <span className="names">
          <strong>{candidate.standard_name_cn}</strong>
          <small>{candidate.standard_name_en}</small>
        </span>
        <span className="meta">
          <span className="rank">{taken ? byRecall(candidate) : candidate.rank}</span>
          {candidate.rerank ? (
            <em
              className={`verdict ${candidate.rerank}`}
              title={VERDICT_TITLE[candidate.rerank] ?? ""}
            >
              {VERDICT[candidate.rerank] ?? candidate.rerank}
            </em>
          ) : null}
          <span className="sku">{candidate.main_sku}</span>
          <span className="channels">
            {candidate.channels.map((channel) => (
              <em key={channel}>{CHANNEL[channel] ?? channel}</em>
            ))}
          </span>
          {stocked ? (
            <em
              className={`stock ${stock.yes ? "yes" : "no"}`}
              title="库存快照中的目标国家可发数量，并非实时库存"
            >
              {stock.text}
            </em>
          ) : null}
        </span>
        {taken ? null : (
          <button
            className="above"
            title="选择本行及上方候选"
            onClick={() => selectAbove(item, candidate.rank)}
          >
            以上全选
          </button>
        )}
      </li>
    );
  }

  async function copyList() {
    setCopied(await copyText(adopted.map((item) => item.sku).join("\n")));
    window.setTimeout(() => setCopied(false), 1600);
  }

  /**
   * The server writes the sheet, not the browser: it is the one holding the
   * scores, the verdicts and the date on the stock snapshot, and a sheet that
   * said nothing about where its numbers came from would be read as if they
   * were all equally current.
   */
  async function exportSheet() {
    setBusy(true);
    setNote("");
    try {
      await api.exportAdoption(
        storeId,
        [...picked].map(parseSelKey),
        `${storeId}-店铺场景报告.xlsx`,
        dedupe,
      );
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const stocked = retrieval?.inventory === "available";
  const country = retrieval?.country ?? "";
  const inventoryNote = retrieval && !stocked ? "库存暂未核验。以下为商品匹配结果，请确认库存后再使用。" : null;
  const rerank = retrieval?.rerank;
  // Counted off the rows rather than read from the summary: the summary only
  // counts what was removed, and in the default mode nothing is removed — "剔除
  // 0 条" said nothing about the ninety rows the model had actually doubted.
  //
  // Counted per scene and SKU, the way the sentence's two neighbours are: one
  // verdict marked on five roles is one judgement, and adding the five rows up
  // would print more doubts than the model was ever asked about.
  const doubted = useMemo(() => {
    const judged = new Set<string>();
    for (const item of retrieval?.scenes ?? []) {
      // Both piles: in 排除 mode the doubted rows are exactly the ones that
      // left the list, so counting only what stayed would understate what the
      // model was asked and answered.
      for (const row of [...(item.candidates ?? []), ...(item.dropped ?? [])]) {
        if (row.rerank === "unrelated") judged.add(`${item.scene_name}::${row.main_sku}`);
      }
    }
    return judged.size;
  }, [retrieval]);

  return (
    <>
      <section className="card">
        <h2>
          场景与候选 SKU <span className="count">{scenes.length} 个场景</span>
        </h2>
        {inventoryNote && <p className="notice warn">{inventoryNote}</p>}
        {rerank && rerank.notes.length === 0 && (
          <p className="muted">
            相关性复核：{rerank.answered} 项已完成，其中 {doubted} 项需人工留意
            {rerank.dropped > 0 ? `，去掉 ${rerank.dropped} 条。` : "，都留在列表里。"}
          </p>
        )}
        {rerank && rerank.notes.length > 0 && (
          <p className="notice warn">
            部分商品未完成复核，候选已保留。详情：{rerank.notes[0]}
            {rerank.answered === 0 &&
              " 未获得有效复核结果，保留原始候选供人工判断。"}
          </p>
        )}

        <div className="scene-map">
          {scenes.map((scene) => (
            <article className="scene-card" key={scene.scene_name}>
              <h3>{scene.scene_name}</h3>
              <p>
                <span className="lbl">人群</span>
                {scene.audience}
              </p>
              <p>
                <span className="lbl">需求</span>
                {scene.user_need}
              </p>

              <div className="roles-open">
                {scene.product_needs.map((need) => {
                  const item = byScene.get(selKey(scene.scene_name, need.product_cn, ""));
                  const key = selKey(scene.scene_name, need.product_cn, "");
                  const expanded = open === key;
                  const excluded = scene.excluded.includes(need.product_cn);
                  const rows = item?.candidates ?? [];
                  // Empty unless the operator switched the setting to 排除: the
                  // default keeps every doubted row in the list above.
                  const taken = item?.dropped ?? [];
                  const count = rows.length;
                  return (
                    <div className="role" key={need.product_cn}>
                      <button
                        className="role-head"
                        data-open={expanded}
                        onClick={() => setOpen(expanded ? "" : key)}
                      >
                        <span className={excluded ? "role-name excluded" : "role-name"}>
                          {need.product_cn}
                        </span>
                        {excluded && <em className="tag">你已排除</em>}
                        <span className="role-count">
                          {!item
                            ? "待匹配"
                            : taken.length > 0
                              ? `${count} 个候选，另 ${taken.length} 条已排除`
                              : `${count} 个候选`}
                        </span>
                        <span className="chev">{expanded ? "收起" : "展开"}</span>
                      </button>

                      {expanded && item && (
                        <div className="candidates">
                          <details className="queries"><summary>查看检索词</summary>
                            <span className="lbl">匹配依据</span>
                            {[...item.queries.cn, ...item.queries.en].join("、")}
                          </details>
                          {count === 0 && taken.length === 0 ? (
                            <p className="muted">本次匹配范围内未找到候选，不代表整个产品库没有对应商品。</p>
                          ) : (
                            <ul className="candidate-list">
                              {rows.map((candidate) => row(item, candidate, false))}
                            </ul>
                          )}
                          {taken.length > 0 && (
                            <>
                              <p className="taken-head">
                                模型判成不相关、按你的设置排除掉的 {taken.length} 条。
                                越靠前越是搜索当时觉得接近的，想用哪条直接勾上。
                              </p>
                              <ul className="candidate-list taken">
                                {[...taken]
                                  .sort((a, b) => byRecall(a) - byRecall(b))
                                  .map((candidate) => row(item, candidate, true))}
                              </ul>
                            </>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              <p className="muted">{scene.evidence}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="card adoption">
        <h2>
          已选商品 <span className="count">{adopted.length} 个 SKU</span>
        </h2>
        {adopted.length === 0 ? (
          <p className="muted">还没有选中任何 SKU。展开上面的商品，勾选你要采用的候选。</p>
        ) : (
          <>
            <div className="adopt-actions">
              <button className="btn small" onClick={copyList}>
                {copied ? "已复制" : "复制 SKU 清单"}
              </button>
              <button className="btn ghost small" disabled={busy} onClick={exportSheet}>
                {busy ? "正在导出…" : "导出 Excel"}
              </button>
              <button className="btn ghost small" onClick={() => { if (window.confirm("清空已选商品？")) setPicked(new Set()); }}>
                清空已选
              </button>
              <label
                className="muted dedupe-toggle"
                title="新增一张每个 SKU 一行的去重清单，原场景报告保留。"
              >
                <input
                  type="checkbox"
                  checked={dedupe}
                  onChange={(e) => setDedupe(e.target.checked)}
                />
                附带去重 SKU 清单
              </label>
              {note && <span className="muted">{note}</span>}
            </div>
            <ul className="adopted">
              {adopted.map((item) => {
                const stock = stockLabel(item.row, country);
                return (
                  <li key={item.sku}>
                    <span className="sku">{item.sku}</span>
                    <strong>{item.row.standard_name_cn}</strong>
                    <span className="muted">{item.where.join("；")}</span>
                    {stocked && (
                      <em className={`stock ${stock.yes ? "yes" : "no"}`}>{stock.text}</em>
                    )}
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </section>
    </>
  );
}
