import { useEffect, useMemo, useRef, useState } from "react";
import { selectionKey, normalizedSelections } from "../selection";

import {
  api,
  type Candidate,
  type Pick,
  type Retrieval,
  type RetrievalProduct,
  type Scene,
} from "../api";
import { countryName } from "../countries";

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

/** How one tick moves across a whole level — a scene, or a role. */
type Mode = "on" | "off" | "invert";

/** Which rows the list shows. Filtering the view never changes what is picked. */
type Filter = "all" | "in_stock" | "picked";

/** One ticked row, kept with everything the right-hand list needs to draw it. */
interface ChosenRow {
  key: string;
  pick: Pick;
  sku: string;
  name: string;
  role: string;
  candidate: Candidate;
}
interface ChosenGroup {
  scene: string;
  rows: ChosenRow[];
}

/** Separates the three parts of a selection without colliding with real names. */
function selKey(scene: string, product: string, sku: string): string {
  return selectionKey(scene, product, sku);
}

/** Everything a role can offer: the ranked rows, plus any the setting took away. */
function rowsOf(item: RetrievalProduct | undefined): Candidate[] {
  return [...(item?.candidates ?? []), ...(item?.dropped ?? [])];
}

/** The id a jump from the chosen list lands on. */
function roleId(scene: string, product: string): string {
  return `role::${scene}::${product}`;
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
 * as "泰国 可发 6" must not print there as something else, and both name the
 * country rather than printing the code the catalogue is keyed by.
 */
function stockLabel(candidate: Candidate, country: string): { text: string; yes: boolean } {
  const name = countryName(country);
  if (candidate.country_available == null) return { text: "库存没查到", yes: false };
  if (!candidate.country_available) return { text: `${name} 没货`, yes: false };
  const quantity = candidate.country_available_quantity;
  return typeof quantity === "number"
    ? { text: `${name} 可发 ${quantity}`, yes: true }
    : { text: `${name} 有货`, yes: true };
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

/** The box above a scene or a role. Three states, because two would make "some
    of this is picked" read the same as "none of it is". */
function TriCheck({ total, on, label, onChange }: {
  total: number;
  on: number;
  label: string;
  onChange: () => void;
}) {
  const box = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (box.current) box.current.indeterminate = on > 0 && on < total;
  }, [on, total]);
  return (
    <input
      ref={box}
      className="tricheck"
      type="checkbox"
      aria-label={label}
      disabled={total === 0}
      checked={total > 0 && on === total}
      onChange={onChange}
    />
  );
}

/** The moves the old screen was missing: with only one tick at a time, a wrong
    batch could be neither un-picked nor flipped.

    One button for both ends. "全选" and "全不选" are the same switch seen from
    either side, so the label follows the group: nothing ticked reads 全选, all of
    it ticked reads 全不选, and either way pressing it settles the whole group. */
function Bulk({ total, on, keys, setKeys }: {
  total: number;
  on: number;
  keys: string[];
  setKeys: (keys: string[], mode: Mode) => void;
}) {
  const all = total > 0 && on === total;
  return (
    <span className="bulk">
      <button
        type="button"
        disabled={keys.length === 0}
        onClick={() => setKeys(keys, all ? "off" : "on")}
      >
        {all ? "全不选" : "全选"}
      </button>
      <button type="button" disabled={keys.length === 0} onClick={() => setKeys(keys, "invert")}>反选</button>
    </span>
  );
}

interface Props {
  storeId: string;
  scenes: Scene[];
  retrieval: Retrieval | null;
}

export default function SceneWorkbench({ storeId, scenes, retrieval }: Props) {
  // Both levels start folded: a scene card is a line of text, and a role's fifty
  // candidate rows are drawn only once somebody asks for that one role. Opening
  // every role of every scene at once is fifteen thousand rows of table for a
  // screen that shows twenty, which is what made the page die.
  const [openScenes, setOpenScenes] = useState<Set<string>>(() => new Set());
  const [openRoles, setOpenRoles] = useState<Set<string>>(() => new Set());
  const [picked, setPicked] = useState<Set<string>>(() => loadSet(storageKey(storeId)));
  const [filter, setFilter] = useState<Filter>("all");
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

  const stocked = retrieval?.inventory === "available";
  const country = retrieval?.country ?? "";

  const byScene = useMemo(() => {
    const map = new Map<string, RetrievalProduct>();
    for (const item of retrieval?.scenes ?? []) {
      map.set(selKey(item.scene_name, item.product_cn, ""), item);
    }
    return map;
  }, [retrieval]);

  /** Every key a scene offers, and the same for each of its roles. */
  const keysOf = useMemo(() => {
    const boxes = new Map<string, { keys: string[]; total: number; on: number }>();
    const count = (key: string) => boxes.get(key) ?? { keys: [], total: 0, on: 0 };
    for (const scene of scenes) {
      for (const need of scene.product_needs) {
        const roleKey = selKey(scene.scene_name, need.product_cn, "");
        const item = byScene.get(roleKey);
        const sceneBox = count(scene.scene_name);
        const roleBox = count(roleKey);
        for (const candidate of rowsOf(item)) {
          const key = selKey(scene.scene_name, need.product_cn, candidate.main_sku);
          const on = picked.has(key);
          sceneBox.keys.push(key);
          roleBox.keys.push(key);
          sceneBox.total += 1;
          roleBox.total += 1;
          if (on) { sceneBox.on += 1; roleBox.on += 1; }
        }
        boxes.set(scene.scene_name, sceneBox);
        boxes.set(roleKey, roleBox);
      }
    }
    return boxes;
  }, [scenes, byScene, picked]);

  /**
   * What the operator has ticked, grouped the way the report reads it.
   *
   * Built by walking the same scenes the left-hand list draws, so the panel can
   * never show a row the list has no home for — and the sheet is written from
   * this list rather than from the raw ticks, which is why the two agree.
   */
  const chosen = useMemo(() => {
    const groups: ChosenGroup[] = [];
    for (const scene of scenes) {
      const rows: ChosenRow[] = [];
      for (const need of scene.product_needs) {
        const item = byScene.get(selKey(scene.scene_name, need.product_cn, ""));
        for (const candidate of rowsOf(item)) {
          const key = selKey(scene.scene_name, need.product_cn, candidate.main_sku);
          if (!picked.has(key)) continue;
          rows.push({
            key,
            pick: {
              scene_name: scene.scene_name,
              product_cn: need.product_cn,
              main_sku: candidate.main_sku,
            },
            sku: candidate.main_sku,
            name: candidate.standard_name_cn,
            role: need.product_cn,
            candidate,
          });
        }
      }
      if (rows.length > 0) groups.push({ scene: scene.scene_name, rows });
    }
    return groups;
  }, [scenes, byScene, picked]);

  const chosenCount = chosen.reduce((total, group) => total + group.rows.length, 0);

  function setKeys(keys: string[], mode: Mode) {
    setPicked((current) => {
      const next = new Set(current);
      for (const key of keys) {
        if (mode === "on") next.add(key);
        else if (mode === "off") next.delete(key);
        else if (next.has(key)) next.delete(key);
        else next.add(key);
      }
      return next;
    });
  }

  function toggle(scene: string, product: string, sku: string) {
    setKeys([selKey(scene, product, sku)], "invert");
  }

  /** "Select this row and everything above it" — above means at least as relevant. */
  function selectAbove(item: RetrievalProduct, upTo: number) {
    const rows = item.candidates ?? [];
    setKeys(
      rows
        .filter((candidate) => candidate.rank <= upTo)
        .map((candidate) => selKey(item.scene_name, item.product_cn, candidate.main_sku)),
      "on",
    );
  }

  /**
   * Go back to where a ticked row lives: open the scene, open that one role,
   * then bring the row into view. The two opens have to paint first, which is
   * what the timeout is for. This is the page's only deliberate scroll — the
   * operator asked for it, so it is the one place that is allowed to move.
   */
  function jump(row: ChosenRow, scene: string) {
    if (filter === "in_stock" && row.candidate.country_available !== true) setFilter("all");
    setOpenScenes((current) => new Set(current).add(scene));
    setOpenRoles((current) => new Set(current).add(selKey(scene, row.role, "")));
    const id = roleId(scene, row.role);
    window.setTimeout(() => {
      document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 60);
  }

  function shows(candidate: Candidate, scene: string, product: string): boolean {
    if (filter === "in_stock") return candidate.country_available === true;
    if (filter === "picked") return picked.has(selKey(scene, product, candidate.main_sku));
    return true;
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
          <span className="sku" title="货号">{candidate.main_sku}</span>
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
            title="把本行和它上面的都勾上"
            onClick={() => selectAbove(item, candidate.rank)}
          >
            以上全选
          </button>
        )}
      </li>
    );
  }

  async function copyList() {
    const skus = [...new Set(chosen.flatMap((group) => group.rows.map((row) => row.sku)))];
    setCopied(await copyText(skus.join("\n")));
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
        chosen.flatMap((group) => group.rows.map((row) => row.pick)),
        `${storeId}-店铺场景报告.xlsx`,
        dedupe,
      );
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const inventoryNote = retrieval && !stocked
    ? "这次没查到库存，下面是商品匹配的结果，用之前请先确认有没有货。"
    : null;
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
    <div className="scene-workbench">
      <section className="card scene-pane">
        <h2>
          场景与可选商品 <span className="count">{scenes.length} 个场景</span>
        </h2>
        {inventoryNote && <p className="notice warn">{inventoryNote}</p>}
        {rerank && rerank.notes.length === 0 && (
          <p className="muted">
            帮你复核了一遍：{rerank.answered} 项已看完，其中 {doubted} 项模型觉得对不上
            {rerank.dropped > 0 ? `，去掉 ${rerank.dropped} 条。` : "，都留在列表里。"}
          </p>
        )}
        {rerank && rerank.notes.length > 0 && (
          <p className="notice warn">
            有 {rerank.answered} 项没复核完，可选商品都留着。详情：{rerank.notes[0]}
            {rerank.answered === 0 && " 没有拿到有效结果，请人工判断。"}
          </p>
        )}

        <div className="scene-filters" role="group" aria-label="筛选可选商品">
          <button className="chip" data-on={filter === "all"} onClick={() => setFilter("all")}>
            全部
          </button>
          <button
            className="chip"
            data-on={filter === "in_stock"}
            disabled={!stocked}
            title={stocked ? "只看这次查到有货的" : "这次没查到库存，用不了这个筛选"}
            onClick={() => setFilter("in_stock")}
          >
            只看有货的
          </button>
          <button
            className="chip"
            data-on={filter === "picked"}
            onClick={() => setFilter("picked")}
          >
            只看已选的
          </button>
        </div>

        <div className="scene-list">
          {scenes.map((scene) => {
            const open = openScenes.has(scene.scene_name);
            const box = keysOf.get(scene.scene_name) ?? { keys: [], total: 0, on: 0 };
            return (
              <article className="scene-card" data-open={open} key={scene.scene_name}>
                <div className="scene-line">
                  <TriCheck
                    total={box.total}
                    on={box.on}
                    label={`全选「${scene.scene_name}」`}
                    onChange={() => setKeys(box.keys, box.on === box.total ? "off" : "on")}
                  />
                  <button
                    className="scene-head"
                    data-open={open}
                    onClick={() =>
                      setOpenScenes((current) => {
                        const next = new Set(current);
                        if (next.has(scene.scene_name)) next.delete(scene.scene_name);
                        else next.add(scene.scene_name);
                        return next;
                      })
                    }
                  >
                    <span className="scene-name">{scene.scene_name}</span>
                    <span className="pick-count">已选 {box.on} / 共 {box.total}</span>
                    <span className="chev">{open ? "收起" : "展开"}</span>
                  </button>
                  <Bulk total={box.total} on={box.on} keys={box.keys} setKeys={setKeys} />
                </div>

                {open && (
                  <div className="scene-body">
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
                        const roleKey = selKey(scene.scene_name, need.product_cn, "");
                        const item = byScene.get(roleKey);
                        const roleOpen = openRoles.has(roleKey);
                        const excluded = scene.excluded.includes(need.product_cn);
                        const rows = (item?.candidates ?? []).filter((c) =>
                          shows(c, scene.scene_name, need.product_cn));
                        // Empty unless the operator switched the setting to 排除: the
                        // default keeps every doubted row in the list above.
                        const taken = (item?.dropped ?? [])
                          .filter((c) => shows(c, scene.scene_name, need.product_cn))
                          .sort((a, b) => byRecall(a) - byRecall(b));
                        const roleBox = keysOf.get(roleKey) ?? { keys: [], total: 0, on: 0 };
                        return (
                          <div className="role" id={roleId(scene.scene_name, need.product_cn)} key={need.product_cn}>
                            <div className="role-line">
                              <TriCheck
                                total={roleBox.total}
                                on={roleBox.on}
                                label={`全选「${need.product_cn}」`}
                                onChange={() =>
                                  setKeys(roleBox.keys, roleBox.on === roleBox.total ? "off" : "on")}
                              />
                              <button
                                className="role-head"
                                data-open={roleOpen}
                                disabled={!item}
                                onClick={() =>
                                  setOpenRoles((current) => {
                                    const next = new Set(current);
                                    if (next.has(roleKey)) next.delete(roleKey);
                                    else next.add(roleKey);
                                    return next;
                                  })
                                }
                              >
                                <span className={excluded ? "role-name excluded" : "role-name"}>
                                  {need.product_cn}
                                </span>
                                {excluded && <em className="tag">你已排除</em>}
                                <span className="pick-count">
                                  {item ? `已选 ${roleBox.on} / 共 ${roleBox.total}` : "还没找商品"}
                                </span>
                                <span className="chev">{!item ? "" : roleOpen ? "收起" : "展开"}</span>
                              </button>
                              <Bulk
                                total={roleBox.total}
                                on={roleBox.on}
                                keys={roleBox.keys}
                                setKeys={setKeys}
                              />
                            </div>

                            {roleOpen && item && (
                              <div className="candidates">
                                <details className="queries"><summary>看用了哪些搜索词</summary>
                                  <span className="lbl">匹配依据</span>
                                  {[...item.queries.cn, ...item.queries.en].join("、")}
                                </details>
                                {rows.length === 0 && taken.length === 0 ? (
                                  <p className="muted">
                                    {filter === "all"
                                      ? "这次没找到可选商品，不代表整个产品库没有对应商品。"
                                      : "这条筛选下没有可选商品，换成「全部」看看。"}
                                  </p>
                                ) : (
                                  <ul className="candidate-list">
                                    {rows.map((candidate) => row(item, candidate, false))}
                                  </ul>
                                )}
                                {taken.length > 0 && (
                                  <>
                                    <p className="taken-head">
                                      模型觉得对不上、按你的设置收起来的 {taken.length} 条。
                                      越靠前越是搜索当时觉得接近的，想用哪条直接勾上。
                                    </p>
                                    <ul className="candidate-list taken">
                                      {taken.map((candidate) => row(item, candidate, true))}
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
                  </div>
                )}
              </article>
            );
          })}
        </div>
      </section>

      <aside className="chosen-rail" aria-label="已选清单">
        <section className="card chosen-card">
          <h2>
            已选清单 <span className="count">{chosenCount} 条</span>
          </h2>
          <p className="muted chosen-hint">勾选记录存在这个浏览器里，换电脑不会有。</p>
          <div className="chosen-actions">
            <button className="btn small" disabled={chosenCount === 0 || busy} onClick={exportSheet}>
              {busy ? "正在导出…" : "导出 Excel"}
            </button>
            <button className="btn ghost small" disabled={chosenCount === 0} onClick={copyList}>
              {copied ? "已复制" : "复制货号"}
            </button>
            <button
              className="btn ghost small"
              disabled={chosenCount === 0}
              onClick={() => { if (window.confirm("清空已选商品？")) setPicked(new Set()); }}
            >
              清空
            </button>
            <label
              className="muted dedupe-toggle"
              title="新增一张每个货号一行的去重清单，原场景报告保留。"
            >
              <input
                type="checkbox"
                checked={dedupe}
                onChange={(e) => setDedupe(e.target.checked)}
              />
              附带去重清单
            </label>
            {note && <span className="muted">{note}</span>}
          </div>

          {chosenCount === 0 ? (
            <p className="muted">还没选。展开左边的场景，勾你要用的商品。</p>
          ) : (
            <div className="chosen-groups">
              {chosen.map((group) => (
                <div className="chosen-group" key={group.scene}>
                  <p className="chosen-scene">
                    {group.scene} <span className="muted">{group.rows.length} 条</span>
                  </p>
                  <ul>
                    {group.rows.map((item) => (
                      <li key={item.key}>
                        <button
                          className="chosen-jump"
                          title="跳到左边这一行"
                          onClick={() => jump(item, group.scene)}
                        >
                          <span className="sku">{item.sku}</span>
                          <strong>{item.name}</strong>
                          <small>{item.role}</small>
                        </button>
                        <button
                          className="chosen-drop"
                          title="取消这一条"
                          aria-label={`取消 ${item.name}`}
                          onClick={() => setKeys([item.key], "off")}
                        >
                          ×
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </section>
      </aside>
    </div>
  );
}
