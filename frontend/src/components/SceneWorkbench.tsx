import { useEffect, useMemo, useState } from "react";

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
  related: "模型认为这个商品和这个场景搭，卖得掉",
  unrelated: "模型认为这个商品和这个场景不搭。只是它的判断，不一定对——默认仍然留在列表里，"
    + "用不用你说了算；只有把「不相关的产品要不要排除」调成排除，这类才会被去掉",
};

/** Separates the three parts of a selection without colliding with real names. */
function selKey(scene: string, product: string, sku: string): string {
  return `${scene}::${product}::${sku}`;
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
  if (candidate.country_available == null) return { text: "未读到库存表", yes: false };
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
    return new Set(Array.isArray(raw) ? raw.filter((v) => typeof v === "string") : []);
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

/** Undo `selKey`, so the ticked rows can be sent back as the names they are. */
function parseSelKey(key: string): Pick {
  const [scene_name, product_cn, main_sku] = key.split("::");
  return { scene_name, product_cn, main_sku };
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
    localStorage.setItem(storageKey(storeId), JSON.stringify([...picked]));
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
      const rows = item.candidates ?? [];
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
  const inventoryNote = retrieval && !stocked ? "这次没有读到库存表，下面列出的是纯语义召回结果。" : null;
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
      for (const row of item.candidates ?? []) {
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
            模型逐个场景看了一遍：{rerank.asked} 件商品（同一个商品在几个商品角色下出现只算一次），
            答上来 {rerank.answered} 件，其中 {doubted} 条标成不相关
            {rerank.dropped > 0 ? `，去掉 ${rerank.dropped} 条。` : "，都留在列表里。"}
          </p>
        )}
        {rerank && rerank.notes.length > 0 && (
          <p className="notice warn">
            模型这一步没跑成，原因：{rerank.notes[0]}
            {rerank.answered === 0 &&
              " 这一步整体没有生效，下面的列表和「重跑召回」出来的完全一样，没有漏掉也没有多删。"}
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
                          {item ? `${count} 个候选` : "还没检索"}
                        </span>
                        <span className="chev">{expanded ? "收起" : "展开"}</span>
                      </button>

                      {expanded && item && (
                        <div className="candidates">
                          <p className="queries">
                            <span className="lbl">检索词</span>
                            {[...item.queries.cn, ...item.queries.en].join("、")}
                          </p>
                          {count === 0 ? (
                            <p className="muted">这个商品在产品库里没搜到候选。</p>
                          ) : (
                            <ul className="candidate-list">
                              {rows.map((candidate) => {
                                const on = picked.has(
                                  selKey(scene.scene_name, need.product_cn, candidate.main_sku),
                                );
                                const stock = stockLabel(candidate, country);
                                return (
                                  <li className="pick" data-on={on} key={candidate.main_sku}>
                                    <input
                                      type="checkbox"
                                      checked={on}
                                      onChange={() =>
                                        toggle(scene.scene_name, need.product_cn, candidate.main_sku)
                                      }
                                    />
                                    <span className="names">
                                      <strong>{candidate.standard_name_cn}</strong>
                                      <small>{candidate.standard_name_en}</small>
                                    </span>
                                    <span className="meta">
                                      <span className="rank">{candidate.rank}</span>
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
                                          title="目标国家当前可发的量，跟着库存快照走"
                                        >
                                          {stock.text}
                                        </em>
                                      ) : null}
                                    </span>
                                    <button
                                      className="above"
                                      title="这一行及更相关的全部选中"
                                      onClick={() => selectAbove(item, candidate.rank)}
                                    >
                                      以上全选
                                    </button>
                                  </li>
                                );
                              })}
                            </ul>
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
          最终采纳 <span className="count">{adopted.length} 个 SKU</span>
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
              <button className="btn ghost small" onClick={() => setPicked(new Set())}>
                全部取消
              </button>
              <label
                className="muted dedupe-toggle"
                title="勾上之后，表格里会多一个「去重商品清单」：每个 SKU 一行，它被哪些场景用来干什么合并写在一起。"
              >
                <input
                  type="checkbox"
                  checked={dedupe}
                  onChange={(e) => setDedupe(e.target.checked)}
                />
                表格里同一 SKU 只列一次
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
