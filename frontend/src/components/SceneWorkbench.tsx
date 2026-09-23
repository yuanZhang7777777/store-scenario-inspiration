import { useEffect, useMemo, useRef, useState } from "react";
import { api, type Retrieval, type RetrievalProduct, type Scene } from "../api";
import { countryName } from "../countries";
import { changeSelection, isSelected, normalizedSelections, readSelection, selectionKey, type SelectionState } from "../selection";

type Mode = "on" | "off" | "invert";
type Filter = "all" | "in_stock" | "picked";

function loadSelection(storeId: string): SelectionState {
  try {
    const saved = localStorage.getItem(`ssi.selection.v2.${storeId}`);
    if (saved) {
      const value = JSON.parse(saved);
      if (typeof value.defaultSelected === "boolean" && value.overrides && typeof value.overrides === "object") {
        return { defaultSelected: value.defaultSelected, overrides: Object.fromEntries(
          Object.entries(value.overrides).filter(([key, on]) => readSelection(key) && typeof on === "boolean")) as Record<string, boolean> };
      }
    }
    // Preserve existing choices, including an intentionally empty selection.
    const legacy = localStorage.getItem(`ssi.picked.${storeId}`);
    if (legacy !== null) return { defaultSelected: false, overrides: Object.fromEntries(
      [...normalizedSelections(JSON.parse(legacy))].map((key) => [key, true])) };
  } catch { /* Unreadable storage uses the documented default. */ }
  return { defaultSelected: true, overrides: {} };
}

function TriCheck({ total, on, label, onChange }: { total: number; on: number; label: string; onChange: () => void }) {
  const box = useRef<HTMLInputElement>(null);
  useEffect(() => { if (box.current) box.current.indeterminate = on > 0 && on < total; }, [on, total]);
  return <input ref={box} className="tricheck" type="checkbox" aria-label={label} disabled={!total} checked={total > 0 && on === total} onChange={onChange} />;
}

async function copyText(text: string): Promise<boolean> {
  try { await navigator.clipboard.writeText(text); return true; } catch { /* Older browser fallback. */ }
  const box = document.createElement("textarea");
  try {
    box.value = text; box.style.position = "fixed"; box.style.opacity = "0";
    document.body.appendChild(box); box.select();
    return document.execCommand("copy");
  } catch { return false; } finally { box.remove(); }
}

interface Props { storeId: string; scenes: Scene[]; retrieval: Retrieval | null; exportBlocked: string }

export default function SceneWorkbench({ storeId, scenes, retrieval, exportBlocked }: Props) {
  const [openScenes, setOpenScenes] = useState<Set<string>>(() => new Set());
  const [openRoles, setOpenRoles] = useState<Set<string>>(() => new Set());
  const [selection, setSelection] = useState<SelectionState>(() => loadSelection(storeId));
  const [filter, setFilter] = useState<Filter>("all");
  const [scope, setScope] = useState<"all" | "in_stock">("all");
  const [relatedOnly, setRelatedOnly] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [storageNote, setStorageNote] = useState("");
  const [copied, setCopied] = useState(false);
  const [foldSpot, setFoldSpot] = useState({ scene: "", role: "" });
  const exporting = useRef(false);
  const rootRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    try { localStorage.setItem(`ssi.selection.v2.${storeId}`, JSON.stringify(selection)); }
    catch { setStorageNote("勾选记录未能保存在此浏览器，离开前请先导出。"); }
  }, [selection, storeId]);

  // What deserves a travelling fold control, and only when its own one has
  // scrolled out of sight: an expanded scene or product type whose header has
  // gone past the top while its rows are still on screen. Read here rather
  // than in the markup because the answer changes with every wheel notch, and
  // only against what is actually drawn — a closed header looks the same as an
  // open one and must not be offered.
  useEffect(() => {
    let queued = false;
    const measure = () => {
      queued = false;
      const middle = window.innerHeight / 2;
      let scene = "";
      let role = "";
      for (const card of rootRef.current?.querySelectorAll<HTMLElement>("[data-scene-key]") ?? []) {
        const cardBox = card.getBoundingClientRect();
        if (cardBox.bottom < 0 || cardBox.top > window.innerHeight) continue;
        const sceneKey = card.dataset.sceneKey ?? "";
        const head = card.querySelector<HTMLElement>(".scene-head");
        if (openScenes.has(sceneKey) && head && head.getBoundingClientRect().bottom < 0 && cardBox.bottom > middle) {
          scene = sceneKey;
        }
        for (const item of card.querySelectorAll<HTMLElement>("[data-role-key]")) {
          const roleKey = item.dataset.roleKey ?? "";
          const roleHead = item.querySelector<HTMLElement>(".role-head");
          const box = item.getBoundingClientRect();
          if (openRoles.has(roleKey) && roleHead && roleHead.getBoundingClientRect().bottom < 0 && box.bottom > middle) {
            role = roleKey;
          }
        }
      }
      setFoldSpot((current) => (current.scene === scene && current.role === role ? current : { scene, role }));
    };
    const onScroll = () => { if (!queued) { queued = true; requestAnimationFrame(measure); } };
    measure();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => { window.removeEventListener("scroll", onScroll); window.removeEventListener("resize", onScroll); };
  }, [scenes, openScenes, openRoles]);

  const stocked = retrieval?.inventory === "available";
  const country = countryName(retrieval?.country ?? "");
  const byRole = useMemo(() => new Map((retrieval?.scenes ?? []).map((item) => [selectionKey(item.scene_name, item.product_cn, ""), item])), [retrieval]);
  const selected = (key: string) => isSelected(selection, key);
  const setKeys = (keys: string[], mode: Mode) => setSelection((current) => changeSelection(current, keys, mode));

  // Dropped rows remain in audit data; only retained candidates are selectable.
  const groups = useMemo(() => scenes.map((scene) => ({ scene, roles: scene.product_needs.map((need) => {
    const key = selectionKey(scene.scene_name, need.product_cn, "");
    const item = byRole.get(key);
    const rows = (item?.candidates ?? []).map((candidate) => {
      const rowKey = selectionKey(scene.scene_name, need.product_cn, candidate.main_sku);
      return { key: rowKey, candidate, on: isSelected(selection, rowKey), pick: {
        scene_name: scene.scene_name, product_cn: need.product_cn, main_sku: candidate.main_sku,
      } };
    });
    return { key, need, item, rows, on: rows.filter((row) => row.on).length };
  }) })), [scenes, byRole, selection]);
  const chosen = groups.flatMap((group) => group.roles.flatMap((role) => role.rows.filter((row) => row.on)));
  // The verdict is the model's doubt, not its ruling, so it changes only what
  // leaves in the file — never what the operator is allowed to tick above.
  const doubted = chosen.filter((row) => row.candidate.rerank === "unrelated").length;
  const exportRows = chosen
    .filter((row) => scope === "all" || row.candidate.country_available === true)
    .filter((row) => !relatedOnly || row.candidate.rerank !== "unrelated");
  const uniqueSkus = new Set(exportRows.map((row) => row.candidate.main_sku));
  const exportScenes = new Map<string, Set<string>>();
  for (const row of exportRows) {
    if (!exportScenes.has(row.pick.scene_name)) exportScenes.set(row.pick.scene_name, new Set());
    exportScenes.get(row.pick.scene_name)!.add(row.pick.main_sku);
  }
  const blocked = exportBlocked || (!retrieval ? "商品匹配完成后即可导出。" : "")
    || (scope === "in_stock" && !stocked ? "库存未核验，请选择「全部已选商品」，或更新商品匹配后重试。" : "")
    || (relatedOnly && !exportRows.length && chosen.length
        ? "勾上「只看相关的」后没有商品剩下，取消勾选即可导出。" : "")
    || (!exportRows.length ? "当前范围没有商品，请勾选场景或调整导出范围。" : "");

  async function exportSheet() {
    if (blocked || exporting.current) return;
    exporting.current = true; setBusy(true); setNote("");
    try {
      const latest = await api.store(storeId);
      if (latest.export_blocked_reason) throw new Error(latest.export_blocked_reason);
      await api.exportAdoption(storeId, exportRows.map((row) => row.pick),
        `${storeId}-店铺场景报告.xlsx`, relatedOnly);
      setNote("Excel 已生成，请在浏览器下载列表中查看。");
    } catch (e) { setNote(e instanceof Error ? e.message : "导出未完成，请重试。"); }
    finally { exporting.current = false; setBusy(false); }
  }
  async function copyList() {
    const ok = await copyText([...uniqueSkus].join("\n"));
    setCopied(ok); if (!ok) setNote("未能复制，请重试或导出 Excel。");
    window.setTimeout(() => setCopied(false), 1600);
  }
  function toggleOpen(set: React.Dispatch<React.SetStateAction<Set<string>>>, key: string) {
    set((current) => { const next = new Set(current); if (next.has(key)) next.delete(key); else next.add(key); return next; });
  }
  // Folding is not ticking: opening a scene shows what is in it, and closing it
  // again leaves every choice where it was. The scene's own 收起 keeps the roles
  // as the operator left them, so coming back lands where they were; 收起全部
  // also forgets the roles, which is the way back to the short page.
  const sceneKeys = groups.map((group) => selectionKey(group.scene.scene_name, "", ""));
  const roleKeys = groups.flatMap((group) => group.roles.filter((role) => role.item).map((role) => role.key));
  const allFolded = !sceneKeys.some((key) => openScenes.has(key)) && !roleKeys.some((key) => openRoles.has(key));
  const allOpen = Boolean(sceneKeys.length) && sceneKeys.every((key) => openScenes.has(key))
    && roleKeys.every((key) => openRoles.has(key));
  // One key, one name, whichever level it came from — the fold rail names the
  // scene and the product type with the same lookup.
  const labels = new Map<string, string>([
    ...groups.map((group) => [selectionKey(group.scene.scene_name, "", ""), group.scene.scene_name] as const),
    ...groups.flatMap((group) => group.roles.map((role) => [role.key, role.need.product_cn] as const)),
  ]);
  function openEverything() { setOpenScenes(new Set(sceneKeys)); setOpenRoles(new Set(roleKeys)); }
  function closeEverything() { setOpenScenes(new Set()); setOpenRoles(new Set()); }
  function selectAbove(item: RetrievalProduct, rank: number) {
    setKeys(item.candidates.filter((row) => row.rank <= rank).map((row) => selectionKey(item.scene_name, item.product_cn, row.main_sku)), "on");
  }

  return <div className="scene-workbench" ref={rootRef}>
    <section className="card scene-pane" id="sku-recommendations">
      <div className="section-heading"><div><p className="eyebrow">查看与调整</p><h2>场景与商品 <span className="count">{scenes.length} 个场景</span></h2><p className="muted">默认选中全部场景。可取消整个场景，或展开后调整商品；「展开全部」一次看齐明细。展开后列表左边会跟着一个收起按钮，滑到哪儿都能收起，不用滚回顶部——收起不会改变已勾选的商品。</p></div></div>
      {storageNote && <p className="notice warn" role="status">{storageNote}</p>}
      {retrieval && !stocked && <p className="notice warn">本次库存未核验。仍可导出全部已选商品，有货范围暂不可用。</p>}
      {retrieval?.rerank?.failed ? <p className="notice warn">部分商品相关性尚未复核，请结合场景检查后再使用。</p> : null}
      <div className="scene-toolbar"><div className="scene-filters" role="group" aria-label="筛选商品，仅改变显示">
        <button className="chip" aria-pressed={filter === "all"} data-on={filter === "all"} onClick={() => setFilter("all")}>全部</button>
        <button className="chip" aria-pressed={filter === "in_stock"} data-on={filter === "in_stock"} disabled={!stocked} onClick={() => setFilter("in_stock")}>只看有货</button>
        <button className="chip" aria-pressed={filter === "picked"} data-on={filter === "picked"} onClick={() => setFilter("picked")}>只看已选</button>
      </div><div className="toolbar-actions">
        <div className="bulk" role="group" aria-label="批量勾选"><button onClick={() => setSelection({ defaultSelected: true, overrides: {} })}>全选场景</button><button onClick={() => setSelection({ defaultSelected: false, overrides: {} })}>取消全选</button></div>
        <div className="bulk fold" role="group" aria-label="展开与收起"><button onClick={openEverything} disabled={allOpen || !sceneKeys.length}>展开全部</button><button onClick={closeEverything} disabled={allFolded}>收起全部</button></div>
      </div></div>
      {!scenes.length && <p className="muted">场景生成后会显示在这里。</p>}
      <div className="scene-list">{groups.map(({ scene, roles }, index) => {
        const key = selectionKey(scene.scene_name, "", "");
        const open = openScenes.has(key);
        const total = roles.reduce((n, role) => n + role.rows.length, 0);
        const on = roles.reduce((n, role) => n + role.on, 0);
        const count = total || 1;
        const checked = total ? on : selected(key) ? 1 : 0;
        return <article className="scene-card" data-open={open} key={key} id={`scene-${index}`} data-scene-key={key}>
          <div className="scene-line">
            <TriCheck total={count} on={checked} label={`选择场景：${scene.scene_name}`} onChange={() => setKeys([key], checked === count ? "off" : "on")} />
            <button className="scene-head" aria-expanded={open} aria-controls={`scene-body-${index}`} onClick={() => toggleOpen(setOpenScenes, key)}>
              <span className="scene-name">{scene.scene_name}</span><span className="pick-count">{total ? `已选 ${on} / ${total} 项` : retrieval ? "暂无匹配商品" : `${roles.length} 类商品 · 待匹配`}</span><span className="chev">{open ? "收起" : "展开"}</span>
            </button>
          </div>
          {open && <div className="scene-body" id={`scene-body-${index}`}>
            <p><span className="lbl">人群</span>{scene.audience}</p><p><span className="lbl">需求</span>{scene.user_need}</p>
            <div className="roles-open">{roles.map(({ key: roleKey, need, item, rows, on: roleOn }, roleIndex) => {
              const roleOpen = openRoles.has(roleKey);
              const visible = rows.filter((row) => filter === "picked" ? row.on : filter === "in_stock" ? row.candidate.country_available === true : true);
              const roleTotal = rows.length || 1;
              const roleChecked = rows.length ? roleOn : selected(roleKey) ? 1 : 0;
              return <div className="role" key={roleKey} data-role-key={roleKey}>
                <div className="role-line"><TriCheck total={roleTotal} on={roleChecked} label={`选择商品类型：${need.product_cn}`} onChange={() => setKeys([roleKey], roleChecked === roleTotal ? "off" : "on")} />
                  <button className="role-head" aria-expanded={roleOpen} aria-controls={`role-${index}-${roleIndex}`} disabled={!item} onClick={() => toggleOpen(setOpenRoles, roleKey)}>
                    <span className="role-name">{need.product_cn}</span>{scene.excluded.includes(need.product_cn) && <em className="tag">需复核</em>}<span className="pick-count">{item ? `已选 ${roleOn} / ${rows.length} 项` : "等待匹配"}</span><span className="chev">{item ? roleOpen ? "收起" : "展开" : ""}</span>
                  </button>
                  {item && <span className="bulk"><button onClick={() => setKeys(rows.map((row) => row.key), "invert")}>反选</button></span>}
                </div>
                {roleOpen && item && <div className="candidates" id={`role-${index}-${roleIndex}`}>
                  {!visible.length ? <p className="muted">{filter === "all" ? "暂未匹配到商品，可调整资料或生成设置后重试。" : "当前筛选下没有商品，可切换为「全部」。"}</p> : <ul className="candidate-list">{visible.map(({ key: rowKey, candidate, on: rowOn }) => <li className="pick" data-on={rowOn} key={rowKey}>
                    <input type="checkbox" checked={rowOn} aria-label={`选择 ${candidate.standard_name_cn}（${candidate.main_sku}）`} onChange={() => setKeys([rowKey], "invert")} />
                    <span className="names"><strong>{candidate.standard_name_cn}</strong><small>{candidate.standard_name_en}</small></span>
                    <span className="meta"><span className="rank">{candidate.rank}</span>{candidate.rerank && <em className={`verdict ${candidate.rerank}`}>{candidate.rerank === "unrelated" ? "相关性待复核" : "相关"}</em>}<span className="sku">{candidate.main_sku}</span><em className={`stock ${candidate.country_available ? "yes" : "no"}`} title="匹配时的库存快照，非实时库存">{candidate.country_available == null ? "库存未核验" : candidate.country_available ? `${country} 有货${candidate.country_available_quantity == null ? "" : ` ${candidate.country_available_quantity}`}` : `${country} 无货`}</em></span>
                    <button className="above" title="选中本行及排序在前的商品" onClick={() => selectAbove(item, candidate.rank)}>以上全选</button>
                  </li>)}</ul>}
                </div>}
              </div>;
            })}</div>
          </div>}
        </article>;
      })}</div>
      {/* One button per level that has scrolled its own control away, deepest
          first: the product type you are inside, then its scene, then the lot.
          It hangs beside the rows rather than in a corner so it reads as part
          of the list. Each one says what it does and then which one it does it
          to, in the opening two words of the name — the full name does not fit
          beside the list and is not what identifies the row anyway: the button
          is for the depth you just scrolled past, and the first words are
          enough to tell which one that was. The full name is in the tooltip. */}
      {!allFolded && <div className="fold-rail" role="group" aria-label="收起已展开的内容">
        {[["role", foldSpot.role], ["scene", foldSpot.scene]].map(([level, key]) => key && <button
          className="fold-chip" key={key} title={`收起「${labels.get(key) ?? ""}」`}
          aria-label={`收起${level === "role" ? "商品" : "场景"}：${labels.get(key) ?? ""}`}
          onClick={() => toggleOpen(level === "role" ? setOpenRoles : setOpenScenes, key)}>
          <span className="lead">收起{level === "role" ? "商品" : "场景"}</span>{(labels.get(key) ?? "").slice(0, 2)}</button>)}
        <button className="fold-chip quiet" onClick={closeEverything} title="收起全部展开的场景与商品" aria-label="收起全部">
          <span className="lead">收起</span>全部</button>
      </div>}
    </section>

    <section className="card export-card" id="export-section" aria-labelledby="export-title">
      <p className="eyebrow">准备带走结果</p><h2 id="export-title">导出 Excel</h2>
      <p className="export-total"><strong>{exportScenes.size}</strong> 个场景 <span>·</span> <strong>{uniqueSkus.size}</strong> 个主 SKU</p>
      <p className="muted">导出以当前勾选为准。同一主 SKU 可出现在多个场景，以上数量已去重。</p>
      <fieldset className="export-options"><legend>导出范围</legend>
        <label><input type="radio" name="export-scope" checked={scope === "all"} onChange={() => setScope("all")} /><span><strong>全部已选商品</strong><small>保留有货、无货及库存未核验的已选商品</small></span></label>
        <label><input type="radio" name="export-scope" checked={scope === "in_stock"} disabled={!stocked} onChange={() => setScope("in_stock")} /><span><strong>仅目标国家有货</strong><small>{stocked ? `在已选商品中，仅导出${country}有货的商品` : "本次库存未核验，暂不可用"}</small></span></label>
      </fieldset>
      {doubted > 0 && <label className="dedupe-toggle"><input type="checkbox" checked={relatedOnly} disabled={Boolean(exportBlocked)} onChange={(e) => setRelatedOnly(e.target.checked)} />只看相关的 <span className="muted">不导出模型标为「相关性待复核」的 {doubted} 项</span></label>}
      <div className="export-contents"><span>文件包含</span><p>主 SKU 清单 · 子 SKU 清单 · 店铺概况 · 去重商品清单 · 口径说明</p></div>
      {exportScenes.size > 0 && <div className="export-scene-summary" aria-label="将导出的场景">{[...exportScenes].map(([name, skus]) => <a href={`#scene-${scenes.findIndex((scene) => scene.scene_name === name)}`} key={name}>{name}<span>{skus.size} 个主 SKU</span></a>)}</div>}
      {blocked && <p className="notice warn" role="status">{blocked}</p>}
      <div className="form-actions"><button className="btn" disabled={Boolean(blocked) || busy} onClick={exportSheet}>{busy ? "正在生成文件…" : "导出 Excel"}</button><button className="btn ghost" disabled={!uniqueSkus.size || busy} onClick={copyList}>{copied ? "已复制" : "复制主 SKU"}</button><a className="text-link" href="#sku-recommendations">返回调整商品 ↑</a></div>
      {note && <p className="notice" role="status">{note}</p>}
      <p className="muted">勾选记录保存在当前浏览器；筛选商品列表不会改变导出范围。</p>
    </section>
  </div>;
}
