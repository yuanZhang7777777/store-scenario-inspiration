const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");
const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "src/operatorUx.ts"), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
});
const moduleObject = { exports: {} };
vm.runInNewContext(compiled.outputText, { exports: moduleObject.exports, module: moduleObject });
const { paramsEqual, validateParams, stageNotice, groupStatus, defaultStoreName, mergeStages } = moduleObject.exports;
const defaults = { scene_count: 6, products_per_scene: 16, expansion_terms: 0, recall_limit: 30, stock_filter: "all", rerank: "mark_only", rerank_provider: "jev", rerank_cutoff: 50, temperature: 0.2 };
const number = (name, minimum, maximum, step = 1) => ({ name, minimum, maximum, step, options: null });
const fields = [number("scene_count", 3, 8), number("rerank_cutoff", 50, 95), number("temperature", 0, 1, 0.1), { name: "stock_filter", options: ["all", "in_stock"] }];

test("a refresh with the same values is not a changed draft", () => assert.equal(paramsEqual(defaults, { ...defaults }), true));
test("a changed scene count is detected", () => assert.equal(paramsEqual(defaults, { ...defaults, scene_count: 7 }), false));
test("validation accepts the current backend defaults", () => assert.equal(validateParams(defaults, fields), null));
test("an empty numeric input is not silently converted to zero", () => assert.match(validateParams({ ...defaults, scene_count: NaN }, fields), /请填写/));
test("infinite input is rejected", () => assert.match(validateParams({ ...defaults, scene_count: Infinity }, fields), /请填写/));
test("integer fields reject fractions", () => assert.match(validateParams({ ...defaults, scene_count: 3.5 }, fields), /整数/));
test("the backend minimum is respected", () => assert.match(validateParams({ ...defaults, scene_count: 2 }, fields), /3–8/));
test("the backend maximum is respected", () => assert.match(validateParams({ ...defaults, rerank_cutoff: 96 }, fields), /50–95/));
test("decimal temperature values remain accepted", () => assert.equal(validateParams({ ...defaults, temperature: 0.5 }, fields), null));
test("unknown select options are rejected", () => assert.match(validateParams({ ...defaults, stock_filter: "unknown" }, fields), /重新选择/));
test("a missing schema cannot be used to save settings", () => assert.match(validateParams(defaults, []), /尚未加载/));
test("Jev reranking is not described as free", () => assert.match(stageNotice(["retrieval", "rerank"], defaults), /费用/));
test("DeepSeek reranking is not described as free", () => assert.match(stageNotice(["rerank"], { ...defaults, rerank_provider: "deepseek" }), /费用/));
test("disabled reranking does not advertise a model call", () => assert.doesNotMatch(stageNotice(["rerank"], { ...defaults, rerank_provider: "off" }), /会调用模型/));
test("zero-term expansion does not advertise a model call", () => assert.doesNotMatch(stageNotice(["expand"], defaults), /会调用模型/));
test("nonzero expansion reports model usage", () => assert.match(stageNotice(["expand"], { ...defaults, expansion_terms: 2 }), /会调用模型/));
test("empty and partially completed groups remain pending", () => { assert.equal(groupStatus([]), "pending"); assert.equal(groupStatus([{ status: "ready" }, { status: "pending" }]), "pending"); });
test("all complete groups are ready", () => assert.equal(groupStatus([{ status: "ready" }, { status: "ready" }]), "ready"));
test("failed steps are not labelled complete", () => assert.equal(groupStatus([{ status: "ready" }, { status: "failed" }]), "failed"));
test("running steps are visible", () => assert.equal(groupStatus([{ status: "pending" }, { status: "running" }]), "running"));
test("unnamed stores get a country-and-time label", () => assert.equal(defaultStoreName("泰国", new Date(2026, 8, 22, 9, 5)), "泰国店铺 09-22 09:05"));
test("saving an unsaved scene count extends a retrieval-only run", () => assert.deepEqual(Array.from(mergeStages(["retrieval", "rerank"], ["scenes", "products", "expand", "retrieval", "rerank"])), ["scenes", "products", "expand", "retrieval", "rerank"]));
test("stage updates are deduplicated and sorted", () => assert.deepEqual(Array.from(mergeStages(["rerank", "retrieval"], ["synthesis", "retrieval"])), ["synthesis", "retrieval", "rerank"]));
test("new backend stages are not silently lost", () => assert.equal(mergeStages([], ["future_stage"]).includes("future_stage"), true));

// These are source-level regression guards, not browser integration tests.
test("the confirmation flow awaits draft persistence before confirming", () => {
  const text = fs.readFileSync(path.join(root, "src/components/ConfirmationPanel.tsx"), "utf8");
  assert.ok(text.indexOf("await beforeConfirm?.()") < text.indexOf("await api.confirmReview("));
  assert.match(text, /api\.confirmReview\(storeId, reviewedVersion\)/);
});
test("settings changes no longer auto-run retrieval and reranking", () => {
  const text = fs.readFileSync(path.join(root, "src/pages/StorePage.tsx"), "utf8");
  assert.doesNotMatch(text, /const rebuilt = useRef/);
  assert.doesNotMatch(text, /不花钱|免费重跑/);
  assert.match(text, /beforeConfirm=\{async \(\) => \{ await saveDraft\(\); \}\}/);
});
test("store naming is optional and the market is not preselected", () => {
  const text = fs.readFileSync(path.join(root, "src/pages/StoresPage.tsx"), "utf8");
  assert.match(text, /const \[country, setCountry\] = useState\(""\)/);
  assert.match(text, /name\.trim\(\) \|\| defaultStoreName/);
  assert.doesNotMatch(text, /\|\| !name\.trim\(\)/);
});
