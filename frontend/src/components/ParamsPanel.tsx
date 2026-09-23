import { useEffect, useRef, useState } from "react";
import { api, type ParamField, type Params } from "../api";
import { PARAM_TITLES, paramsEqual, validateParams } from "../operatorUx";

// One list, in the order an operator set them: what the analysis is for, then
// how wide it searches, then the writing. The form no longer splits them into
// "ordinary" and "technical" — a knob nobody recognises is still a knob they
// have to decide whether to touch.
const ORDER: (keyof Params)[] = [
  "scene_count", "products_per_scene", "stock_filter",
  "expansion_terms", "recall_limit", "rerank_provider", "temperature",
];
/** The knobs that read as a continuum rather than a count. */
const SLIDERS = new Set<keyof Params>(["temperature"]);
const OPTIONS: Record<string, string> = {
  all: "全部商品（显示库存状态）", in_stock: "仅目标国家有货",
  jev: "Jev", deepseek: "DeepSeek", off: "不复核",
};
const HINTS: Record<keyof Params, string> = {
  scene_count: "从几个使用场景寻找经营机会。",
  products_per_scene: "商品类别数，不是最终上架数量。",
  stock_filter: "库存未知不代表无货。",
  expansion_terms: "0 表示不扩写搜索词。",
  recall_limit: "从产品库为每类商品保留的候选数量。",
  rerank_provider: "使用已配置的模型服务，可能产生费用。",
  temperature: "往右更发散，往左更贴资料。",
};
/** What the two ends of a slider mean, so the number is not the only clue. */
const ENDS: Partial<Record<keyof Params, [string, string]>> = {
  temperature: ["更稳、更贴资料", "更发散、更多可能"],
};
interface Props {
  /** Absent before the store exists: on the upload page there is nothing to save
   *  to yet, and the values ride along with the run that creates it. */
  storeId?: string;
  params: Params;
  onSaved?: (params: Params) => void | Promise<void>;
  busy: boolean;
  onDraft: (params: Params) => void;
  onBusyChange?: (busy: boolean) => void;
}

export default function ParamsPanel({ storeId, params, onSaved, busy, onDraft, onBusyChange }: Props) {
  const [fields, setFields] = useState<ParamField[]>([]);
  const [draft, setDraft] = useState<Params>(params);
  const [note, setNote] = useState("");
  const [applying, setApplying] = useState(false);
  const [schemaError, setSchemaError] = useState(false);
  const [reload, setReload] = useState(0);
  const saved = useRef(params);
  const submitting = useRef(false);

  // A background refresh creates a new object even when values are unchanged.
  // Do not silently discard the operator's unsaved edits in that case.
  useEffect(() => {
    if (!paramsEqual(saved.current, params)) setDraft(params);
    saved.current = params;
  }, [params]);
  useEffect(() => { onDraft(draft); }, [draft, onDraft]);
  useEffect(() => {
    let active = true;
    setSchemaError(false);
    api.paramSchema().then((schema) => { if (active) setFields(schema.fields); })
      .catch(() => { if (active) setSchemaError(true); });
    return () => { active = false; };
  }, [reload]);
  const changed = !paramsEqual(draft, params);

  async function apply() {
    if (!storeId || busy || submitting.current || !changed) return;
    const invalid = validateParams(draft, fields);
    if (invalid) { setNote(invalid); return; }
    submitting.current = true;
    setApplying(true);
    onBusyChange?.(true);
    setNote("");
    let persisted = false;
    try {
      const next = await api.setParams(storeId, draft);
      persisted = true;
      await onSaved?.(next);
      setNote("设置已保存。后续分析使用新设置。");
    } catch {
      setNote(persisted ? "设置已保存，但页面未能刷新。请刷新后继续。" : "设置未保存，请重试。");
    } finally {
      submitting.current = false;
      setApplying(false);
      onBusyChange?.(false);
    }
  }

  function renderField(field: ParamField) {
    const disabled = busy || applying;
    const value = draft[field.name];
    const number = typeof value === "number" && Number.isFinite(value) ? value : "";
    if (field.options) return <label className="field" key={field.name}>
      <span>{PARAM_TITLES[field.name]}</span>
      <select disabled={disabled} value={value} onChange={(event) => {
        setNote(""); setDraft({ ...draft, [field.name]: event.target.value });
      }}>{field.options.map((option) => <option key={option} value={option}>{OPTIONS[option] || option}</option>)}</select>
      <small className="muted">{HINTS[field.name]}</small>
    </label>;
    if (SLIDERS.has(field.name)) {
      const [left, right] = ENDS[field.name] ?? ["", ""];
      return <label className="field slider-field" key={field.name}>
        <span>{PARAM_TITLES[field.name]} <output>{number}</output></span>
        <input disabled={disabled} type="range" min={field.minimum ?? undefined}
          max={field.maximum ?? undefined} step={field.step}
          value={typeof number === "number" ? number : (field.default as number)}
          onChange={(event) => { setNote(""); setDraft({ ...draft, [field.name]: event.target.valueAsNumber }); }} />
        <span className="slider-ends"><small className="muted">{left}</small><small className="muted">{right}</small></span>
        <small className="muted">{HINTS[field.name]}</small>
      </label>;
    }
    return <label className="field" key={field.name}>
      <span>{PARAM_TITLES[field.name]}</span>
      <input disabled={disabled} type="number" min={field.minimum ?? undefined}
        max={field.maximum ?? undefined} step={field.step} value={number}
        onChange={(event) => { setNote(""); setDraft({ ...draft, [field.name]: event.target.valueAsNumber }); }} />
      <small className="muted">{HINTS[field.name]}</small>
    </label>;
  }

  return <details className="card advanced-settings operator-settings">
    <summary>生成设置 <span className="muted">选填，默认设置即可开始</span>{changed && <span className="count">{storeId ? "有未保存修改" : "已修改默认设置"}</span>}</summary>
    {schemaError ? <p className="notice warn" role="alert">设置暂时无法加载。<button className="btn ghost small" onClick={() => setReload((value) => value + 1)}>重试</button></p> :
      fields.length === 0 ? <p className="muted" role="status">正在加载设置…</p> :
        <div className="business-fields operator-fields">{ORDER.map((name) =>
          fields.find((field) => field.name === name)).filter(Boolean).map((field) =>
          renderField(field as ParamField))}</div>}
    <div className="form-actions">
      {storeId && <button className="btn small" disabled={busy || applying || !changed || !fields.length} onClick={apply}>{applying ? "保存中…" : "保存设置"}</button>}
      <button className="btn ghost small" disabled={busy || applying || !changed} onClick={() => { setDraft(params); setNote(""); }}>{storeId ? "撤销修改" : "恢复默认"}</button>
      <span role="status" className="muted">{note}</span>
    </div>
  </details>;
}
