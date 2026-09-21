import { useEffect, useState } from "react";

import { api, type ParamField, type Params } from "../api";

/**
 * Which knobs can be applied without paying for another model call.
 *
 * These re-read what is already on disk: the product library for the two recall
 * knobs, and the candidate lists for the three verdict knobs. Re-deciding which
 * verdicts to act on is free even on DeepSeek, because the answer is cached by
 * the question that was asked — the cutoff and the drop mode are not part of it.
 * Only switching the model to DeepSeek makes it answer for the first time, and
 * that is the one case the tag below says so.
 */
const LOCAL: Partial<Record<keyof Params, string[]>> = {
  recall_limit: ["clues", "retrieval"],
  stock_filter: ["clues", "retrieval"],
  rerank: ["rerank"],
  rerank_cutoff: ["rerank"],
  rerank_provider: ["rerank"],
};

/**
 * The knob that steers how the writing reads, rather than what it looks for.
 *
 * There were three of them. 重复词压制 and 已用词回避 are gone: DeepSeek marks both
 * "deprecated — 传入该参数将不会产生任何效果", so they were sliders that could not
 * change anything. What is left stays its own group rather than folding back into
 * the eight above, because it still behaves unlike them — every other number here
 * changes what gets recalled, this one changes the sentence.
 */
const STYLE: (keyof Params)[] = ["temperature"];

/** The tile titles say what the number does, not what it is called internally. */
const SHORT: Record<keyof Params, string> = {
  scene_count: "写几个场景",
  products_per_scene: "每个场景列几样商品",
  expansion_terms: "每个商品扩几个近义词",
  recall_limit: "每个商品找几个 SKU",
  stock_filter: "没货的要不要留着",
  rerank: "不相关的产品要不要排除",
  rerank_provider: "召回产品谁来判相关性",
  rerank_cutoff: "多确定才敢标不相关",
  temperature: "天马行空程度",
};

/** What applying this knob costs, in the words the operator reads on it. */
function cost(name: keyof Params, billed: boolean): { text: string; free: boolean } {
  if (name === "rerank_provider") {
    return billed ? { text: "改用后会调用 DeepSeek", free: false } : { text: "免费重跑", free: true };
  }
  return LOCAL[name] ? { text: "免费重跑", free: true } : { text: "需重新生成", free: false };
}

interface Props {
  storeId: string;
  params: Params;
  onSaved: (params: Params) => void;
  onRun: (stages: string[], params: Params) => void;
  busy: boolean;
  canRerunLocally: boolean;
}

export default function ParamsPanel({ storeId, params, onSaved, onRun, busy, canRerunLocally }: Props) {
  const [fields, setFields] = useState<ParamField[]>([]);
  const [draft, setDraft] = useState<Params>(params);
  const [note, setNote] = useState("");

  useEffect(() => setDraft(params), [params]);
  useEffect(() => {
    api
      .paramSchema()
      .then((schema) => setFields(schema.fields))
      .catch(() => setFields([]));
  }, []);

  const changed = fields.filter((field) => draft[field.name] !== params[field.name]);
  const dirty = changed.length > 0;
  const paidChanges = changed.filter((field) => !LOCAL[field.name]);
  const localChanged = changed.filter((field) => LOCAL[field.name]);
  // Whatever the changed knobs need, in pipeline order and each stage once.
  // With nothing changed the button is the plain recall, which is what an
  // operator reaching for it usually wants.
  const rerunStages =
    localChanged.length === 0
      ? ["clues", "retrieval"]
      : ["clues", "retrieval", "rerank"].filter((stage) =>
          localChanged.some((field) => LOCAL[field.name]?.includes(stage)),
        );
  const rerunLabel = rerunStages.includes("retrieval") ? "重跑召回" : "重新标一遍相关性";
  // Rerunning the verdicts is free only when it asks the same question again,
  // because the answer is cached by exactly what was asked — scene, its product
  // names and every candidate recalled for it. Two ways to change the question,
  // and both cost: switching the provider to DeepSeek (it has never answered
  // this one), and rerunning the recall first (the candidate list is now
  // different). The second is easy to miss: recall_limit and the cutoff are each
  // free on their own, so moving them together looks free and quietly bills a
  // call per scene.
  const billed =
    rerunStages.includes("rerank") &&
    draft.rerank_provider === "deepseek" &&
    (rerunStages.includes("retrieval") || draft.rerank_provider !== params.rerank_provider);

  function tile(field: ParamField) {
    const { text, free } = cost(field.name, billed);
    const touched = draft[field.name] !== params[field.name];
    return (
      <label className="knob" key={field.name} data-changed={touched}>
        <span className="knob-head">
          <strong>{SHORT[field.name]}</strong>
          <em className={free ? "tag free" : "tag paid"}>{text}</em>
        </span>
        {field.options ? (
          <select
            value={draft[field.name]}
            onChange={(e) => setDraft({ ...draft, [field.name]: e.target.value })}
          >
            {field.options.map((option) => (
              <option key={option} value={option}>
                {field.labels?.[option] ?? option}
              </option>
            ))}
          </select>
        ) : (
          <input
            type="number"
            min={field.minimum ?? undefined}
            max={field.maximum ?? undefined}
            step={field.step}
            value={draft[field.name]}
            onChange={(e) => setDraft({ ...draft, [field.name]: Number(e.target.value) })}
          />
        )}
        <small className="hint">{field.description}</small>
        <small className="muted">
          {field.options
            ? `默认 ${field.labels?.[String(field.default)] ?? field.default}`
            : `可填 ${field.minimum}–${field.maximum}，默认 ${field.default}`}
        </small>
      </label>
    );
  }

  async function save() {
    try {
      const saved = await api.setParams(storeId, draft);
      onSaved(saved);
      setNote(paidChanges.length ? "已保存。要生效还需要重新生成场景。" : "已保存。");
    } catch (e) {
      setNote((e as Error).message);
    }
  }

  return (
    <section className="card">
      <h2>检索参数</h2>

      <div className="knobs">
        {fields.filter((field) => !STYLE.includes(field.name)).map(tile)}
      </div>

      <h3 className="knobs-heading">写作风格</h3>
      <p className="knobs-note">
        下面三个只管「场景怎么写」，不管「去库里找什么」。改了要重新生成场景才看得出效果。
      </p>
      <div className="knobs">
        {fields.filter((field) => STYLE.includes(field.name)).map(tile)}
      </div>

      <div className="knob-actions">
        <button className="btn small" disabled={busy || !dirty} onClick={save}>
          保存参数
        </button>
        <button
          className="btn ghost small"
          disabled={busy || !canRerunLocally || rerunStages.length === 0}
          title={
            billed
              ? "重新标一遍相关/不相关。这一档会调用 DeepSeek，一个场景一次，按量付费"
              : "只重读已有的数据，不重新生成场景，不花钱"
          }
          onClick={() => onRun(rerunStages, draft)}
        >
          {rerunLabel}
          {billed ? "（会用 DeepSeek，计费）" : "（免费）"}
        </button>
        {paidChanges.length > 0 && (
          <span className="muted">
            改过的 {paidChanges.map((field) => SHORT[field.name]).join("、")} 要重新生成场景
          </span>
        )}
        {note && <span className="muted">{note}</span>}
      </div>
    </section>
  );
}
