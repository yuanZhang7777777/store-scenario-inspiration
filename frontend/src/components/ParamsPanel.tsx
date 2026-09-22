import { useEffect, useState } from "react";
import { api, type ParamField, type Params } from "../api";

const TITLES: Record<keyof Params, string> = {
  scene_count: "场景数量", products_per_scene: "每个场景的商品数量", expansion_terms: "补充搜索词数量",
  recall_limit: "每个商品留几个可选", stock_filter: "要不要只看有货的", rerank: "复核出的无关商品怎么处理",
  rerank_provider: "谁来帮你复核", rerank_cutoff: "多确定才排除", temperature: "文字的发挥程度",
};
const OPTIONS: Record<string, string> = {
  all: "都留着，标出有没有货", in_stock: "只看有货的", mark_only: "留着，标一下", drop: "把明确无关的去掉",
  jev: "Jev", deepseek: "DeepSeek", off: "不复核",
};
const HINTS: Partial<Record<keyof Params, string>> = {
  rerank_cutoff: "数越大越谨慎，越不容易被排除。", temperature: "通常无需调整。",
  stock_filter: "库存未知不等于无货。", products_per_scene: "保留现有默认设置即可。",
};
interface Props {
  storeId: string; params: Params; onSaved: (params: Params) => void;
  busy: boolean;
  /** Before the operator confirms the store, the knobs can still be saved —
   *  they are read from disk when the run starts, so saving first works. */
  confirmed: boolean;
  /** The knobs as the form shows them, saved or not. The buttons outside this
   *  panel read params off disk, so a knob moved and not saved would otherwise
   *  be silently dropped from the run it was meant for. */
  onDraft: (params: Params) => void;
}

export default function ParamsPanel({ storeId, params, onSaved, busy, confirmed, onDraft }: Props) {
  const [fields, setFields] = useState<ParamField[]>([]);
  const [draft, setDraft] = useState<Params>(params);
  const [note, setNote] = useState("");
  const [applying, setApplying] = useState(false);
  useEffect(() => { setDraft(params); }, [params]);
  useEffect(() => { onDraft(draft); }, [draft, onDraft]);
  useEffect(() => {
    let active = true;
    api.paramSchema().then((schema) => { if (active) setFields(schema.fields); }).catch(() => { if (active) setNote("设置暂时无法加载，请刷新重试。"); });
    return () => { active = false; };
  }, []);
  const changed = fields.filter((field) => draft[field.name] !== params[field.name]);

  async function apply() {
    if (busy || applying || !changed.length) return;
    const invalid = fields.find((field) => !field.options && (
      typeof draft[field.name] !== "number" || !Number.isFinite(draft[field.name]) ||
      (field.minimum != null && Number(draft[field.name]) < field.minimum) ||
      (field.maximum != null && Number(draft[field.name]) > field.maximum) ||
      (field.step === 1 && !Number.isInteger(draft[field.name]))));
    if (invalid) { setNote(`请检查“${TITLES[invalid.name]}”的取值范围。`); return; }
    // Saving is the whole job here. Which steps a change undoes is a fact about
    // the store, not about this form, so the page works it out from what the
    // last run recorded and shows exactly one button for it. A form that also
    // decided the stages would be a second opinion that can drift from the first.
    setApplying(true); setNote("");
    try {
      onSaved(await api.setParams(storeId, draft));
      setNote(confirmed
        ? "设置已保存。不花钱的部分会自动重算；要重新调用模型的，按页面上的按钮开始。"
        : "设置已保存，确认店铺信息后按这套设置开始分析。");
    } catch { setNote("未能保存设置，请稍后重试。"); }
    finally { setApplying(false); }
  }
  return (
    <details className="card advanced-settings">
      <summary>高级设置 <span className="muted">通常无需调整</span></summary>
      <div className="business-fields">{fields.map((field) => {
        const disabled = busy || applying || (field.name === "rerank_cutoff" && (draft.rerank !== "drop" || draft.rerank_provider === "off"));
        return <label className="field" key={field.name}><span>{TITLES[field.name]}</span>
          {field.options ? <select disabled={disabled} value={draft[field.name]} onChange={(e) => setDraft({ ...draft, [field.name]: e.target.value })}>{field.options.map((option) => <option key={option} value={option}>{OPTIONS[option] || option}</option>)}</select> :
            <input disabled={disabled} type="number" min={field.minimum ?? undefined} max={field.maximum ?? undefined} step={field.step} value={draft[field.name]} onChange={(e) => { if (e.target.value !== "") setDraft({ ...draft, [field.name]: Number(e.target.value) }); }} />}
          <small className="muted">{HINTS[field.name] || (field.options ? "" : `${field.minimum}–${field.maximum}`)}</small>
        </label>;
      })}</div>
      <div className="form-actions"><button className="btn small" disabled={busy || applying || !changed.length} onClick={apply}>保存设置</button><button className="btn ghost small" disabled={busy || applying || !changed.length} onClick={() => { setDraft(params); setNote(""); }}>撤销修改</button><span role="status" className="muted">{note}</span></div>
    </details>
  );
}
