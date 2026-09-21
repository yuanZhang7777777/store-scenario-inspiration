import { useEffect, useState } from "react";
import { api, type ParamField, type Params } from "../api";

const TITLES: Record<keyof Params, string> = {
  scene_count: "场景数量", products_per_scene: "每个场景的商品数量", expansion_terms: "扩写词数量",
  recall_limit: "每项商品的候选数量", stock_filter: "库存范围", rerank: "无关候选处理",
  rerank_provider: "相关性复核", rerank_cutoff: "排除阈值", temperature: "生成随机性",
};
const OPTIONS: Record<string, string> = {
  all: "保留全部，标注库存", in_stock: "仅目标国家有货", mark_only: "保留并标记", drop: "排除明确无关项",
  jev: "Jev", deepseek: "DeepSeek", off: "不启用",
};
const HINTS: Partial<Record<keyof Params, string>> = {
  rerank_cutoff: "数值越高，自动排除越谨慎。", temperature: "通常无需调整。",
  stock_filter: "库存未知不等于无货。", products_per_scene: "保留现有默认设置即可。",
};
interface Props {
  storeId: string; params: Params; onSaved: (params: Params) => void;
  onRun: (stages: string[], params: Params) => Promise<boolean>;
  busy: boolean; canRerunLocally: boolean;
}

export default function ParamsPanel({ params, onSaved, onRun, busy, canRerunLocally }: Props) {
  const [fields, setFields] = useState<ParamField[]>([]);
  const [draft, setDraft] = useState<Params>(params);
  const [note, setNote] = useState("");
  const [applying, setApplying] = useState(false);
  useEffect(() => { setDraft(params); }, [params]);
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
    const full = ["clues", "scenes", "products", "synthesis", "expand", "retrieval", "rerank"];
    const needsAnalysis = changed.some((f) => ["scene_count", "products_per_scene", "expansion_terms", "temperature"].includes(f.name));
    const needsRetrieval = changed.some((f) => ["recall_limit", "stock_filter"].includes(f.name));
    const stages = needsAnalysis || !canRerunLocally ? full : needsRetrieval ? ["retrieval", "rerank"] : ["rerank"];
    setApplying(true); setNote("");
    try {
      if (await onRun(stages, draft)) { onSaved(draft); setNote("设置已应用，正在更新受影响的结果。"); }
      else setNote("更新未启动，修改尚未应用。请检查页面提示。");
    } catch { setNote("更新未启动，请稍后重试。"); }
    finally { setApplying(false); }
  }
  return (
    <details className="card advanced-settings">
      <summary>高级设置 <span className="muted">通常无需调整</span></summary>
      <p className="muted">保留现有检索默认值。修改后，由系统更新相关步骤。</p>
      <div className="business-fields">{fields.map((field) => {
        const disabled = busy || applying || (field.name === "rerank_cutoff" && (draft.rerank !== "drop" || draft.rerank_provider === "off"));
        return <label className="field" key={field.name}><span>{TITLES[field.name]}</span>
          {field.options ? <select disabled={disabled} value={draft[field.name]} onChange={(e) => setDraft({ ...draft, [field.name]: e.target.value })}>{field.options.map((option) => <option key={option} value={option}>{OPTIONS[option] || option}</option>)}</select> :
            <input disabled={disabled} type="number" min={field.minimum ?? undefined} max={field.maximum ?? undefined} step={field.step} value={draft[field.name]} onChange={(e) => { if (e.target.value !== "") setDraft({ ...draft, [field.name]: Number(e.target.value) }); }} />}
          <small className="muted">{HINTS[field.name] || (field.options ? "" : `${field.minimum}–${field.maximum}`)}</small>
        </label>;
      })}</div>
      <div className="form-actions"><button className="btn small" disabled={busy || applying || !changed.length} onClick={apply}>应用并更新</button><button className="btn ghost small" disabled={busy || applying || !changed.length} onClick={() => { setDraft(params); setNote(""); }}>撤销修改</button><span role="status" className="muted">{note}</span></div>
    </details>
  );
}
