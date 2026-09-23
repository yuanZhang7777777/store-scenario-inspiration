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
test("the upload page opens the store, writes the products, then runs everything", () => {
  const text = fs.readFileSync(path.join(root, "src/pages/StoresPage.tsx"), "utf8");
  // The order matters: the products have to be on the store before the run that
  // reads them. No stage list is sent, which is what asks for the whole pipeline.
  assert.ok(text.indexOf("await api.createStore(") < text.indexOf("await api.setProducts("));
  assert.ok(text.indexOf("await api.setProducts(") < text.indexOf("await api.startJob("));
  assert.match(text, /api\.startJob\(id, undefined, chosen\)/);
});
test("settings changes no longer auto-run retrieval and reranking", () => {
  const text = fs.readFileSync(path.join(root, "src/pages/StorePage.tsx"), "utf8");
  assert.doesNotMatch(text, /const rebuilt = useRef/);
  assert.doesNotMatch(text, /不花钱|免费重跑/);
  // Nothing gates the run any more: the store is read, written and recalled in
  // one press, and the operator edits the result afterwards.
  assert.doesNotMatch(text, /confirmation|confirmReview|确认商品/);
  const api = fs.readFileSync(path.join(root, "src/api.ts"), "utf8");
  assert.doesNotMatch(api, /confirmReview|ReviewStatus/);
});
test("store naming is optional and the market is not preselected", () => {
  const text = fs.readFileSync(path.join(root, "src/pages/StoresPage.tsx"), "utf8");
  assert.match(text, /const \[country, setCountry\] = useState\(""\)/);
  assert.match(text, /name\.trim\(\) \|\| defaultStoreName/);
  assert.doesNotMatch(text, /\|\| !name\.trim\(\)/);
});
test("the product list folds back in one press without touching the ticks", () => {
  const text = fs.readFileSync(path.join(root, "src/components/SceneWorkbench.tsx"), "utf8");
  assert.match(text, /收起全部/);
  assert.match(text, /展开全部/);
  // Collapsing clears the two open sets and nothing else: the selection is a
  // separate state, so folding the list back up must not uncheck anything.
  assert.match(text, /function closeEverything\(\) \{ setOpenScenes\(new Set\(\)\); setOpenRoles\(new Set\(\)\); \}/);
});
test("the fold controls follow the screen instead of scrolling away", () => {
  const css = fs.readFileSync(path.join(root, "src/styles/workbench.css"), "utf8");
  const bar = css.slice(css.indexOf(".scene-toolbar {")).slice(0, 320);
  assert.match(bar, /position: sticky/);
  // The list outruns the screen, so the buttons that fold it are pinned to the
  // window, level with the rows rather than in a corner, in the margin to the
  // left of the list.
  const rail = css.slice(css.indexOf(".fold-rail {")).slice(0, 240);
  assert.match(rail, /position: fixed/);
  assert.match(rail, /top: 50%/);
  assert.match(rail, /left: max\(/);
  // The width is what holds them off the card: laid out right-aligned inside
  // it, the widest button stops short of the content.
  assert.match(rail, /width: 150px/);
  assert.match(rail, /align-items: flex-end/);
  const text = fs.readFileSync(path.join(root, "src/components/SceneWorkbench.tsx"), "utf8");
  assert.match(text, /\{!allFolded && <div className="fold-rail"/);
  // The chip says what it does before which one it does it to, and the name
  // stops at two words: the whole scene name does not fit in the margin, and
  // the button is about the level just scrolled past, not about the row.
  assert.ok(text.includes('<span className="lead">收起{level === "role" ? "商品" : "场景"}</span>'));
  assert.ok(text.includes('(labels.get(key) ?? "").slice(0, 2)'));
  // A control appears only for a level whose own one has scrolled away: the
  // scene header and the product-type header are measured against the top of
  // the screen, and only while their rows are still the thing being read.
  assert.match(text, /openScenes\.has\(sceneKey\) && head && head\.getBoundingClientRect\(\)\.bottom < 0 && cardBox\.bottom > middle/);
  assert.match(text, /openRoles\.has\(roleKey\) && roleHead && roleHead\.getBoundingClientRect\(\)\.bottom < 0/);
  // Except on a phone, where the section strip is already stuck to the top and
  // a second bar under it would be most of the screen.
  const mobile = css.slice(css.indexOf("@media (max-width: 760px)"));
  assert.match(mobile, /\.scene-toolbar \{\s*position: static/);
});
