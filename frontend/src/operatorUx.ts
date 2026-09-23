import type { ParamField, Params, StageStatus } from "./api";

export const PARAM_TITLES: Record<keyof Params, string> = {
  scene_count: "推荐场景数",
  products_per_scene: "每个场景至少推荐几类商品",
  stock_filter: "库存范围",
  expansion_terms: "补充搜索词数",
  recall_limit: "每类商品的候选数",
  rerank_provider: "复核模型",
  temperature: "创意程度",
};

export function paramsEqual(a: Params, b: Params): boolean {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  return [...keys].every((key) => Object.is(a[key as keyof Params], b[key as keyof Params]));
}

/** Use the backend's rules; do not duplicate its limits or defaults in the UI. */
export function validateParams(params: Params, fields: ParamField[]): string | null {
  if (!fields.length) return "设置规则尚未加载，请稍后重试。";
  for (const field of fields) {
    const value = params[field.name];
    const title = PARAM_TITLES[field.name] || field.name;
    if (field.options) {
      if (!field.options.includes(String(value))) return `请重新选择“${title}”。`;
      continue;
    }
    if (typeof value !== "number" || !Number.isFinite(value)) return `请填写“${title}”。`;
    if (field.step === 1 && !Number.isInteger(value)) return `“${title}”需要填写整数。`;
    if ((field.minimum != null && value < field.minimum) ||
        (field.maximum != null && value > field.maximum)) {
      return `“${title}”的范围为 ${field.minimum ?? "不限"}–${field.maximum ?? "不限"}。`;
    }
  }
  return null;
}

/** No pricing promise: a rerank job can use either configured model provider. */
export function stageNotice(stages: string[], params: Params): string {
  const callsModel = stages.some((stage) =>
    ["recognize", "synthesis", "scenes", "products"].includes(stage) ||
    (stage === "expand" && params.expansion_terms > 0) ||
    (stage === "rerank" && params.rerank_provider !== "off"));
  return callsModel
    ? "会调用模型，费用以当前服务配置为准。"
    : "按已保存的结果更新商品匹配。";
}

export function groupStatus(stages: { status: StageStatus }[]): StageStatus {
  if (!stages.length) return "pending";
  if (stages.some((stage) => stage.status === "running")) return "running";
  if (stages.some((stage) => stage.status === "failed")) return "failed";
  return stages.every((stage) => stage.status === "ready") ? "ready" : "pending";
}

export function defaultStoreName(country: string, now = new Date()): string {
  const two = (value: number) => String(value).padStart(2, "0");
  return `${country}店铺 ${two(now.getMonth() + 1)}-${two(now.getDate())} ${two(now.getHours())}:${two(now.getMinutes())}`;
}

/** Let the backend's outdated stages extend an action after saving a draft. */
export function mergeStages(requested: string[], outdated: string[]): string[] {
  const needed = new Set([...requested, ...outdated]);
  const order = ["recognize", "clues", "synthesis", "scenes", "products", "expand", "retrieval", "rerank"];
  // Preserve unknown stages so the backend can reject a version mismatch rather
  // than silently dropping work introduced by a newer server.
  return [...order.filter((stage) => needed.has(stage)), ...[...needed].filter((stage) => !order.includes(stage))];
}
