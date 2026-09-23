const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const storage = new Map();
const cache = new Map();
function load(file) {
  file = path.resolve(__dirname, "../src", file);
  if (!path.extname(file)) file += fs.existsSync(file + ".ts") ? ".ts" : ".tsx";
  if (cache.has(file)) return cache.get(file);
  const module = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  vm.runInNewContext(code, {
    module, exports: module.exports,
    require: (name) => name.startsWith(".") ? load(path.resolve(path.dirname(file), name)) : require(name),
    localStorage: { getItem: (key) => storage.get(key) ?? null },
  }, { filename: file });
  cache.set(file, module.exports);
  return module.exports;
}
const { selectionKey: key, isSelected, changeSelection } = load("selection.ts");
const fresh = () => ({ defaultSelected: true, overrides: {} });

test("scene exclusions cover late arrivals; partial choices and reselect survive serialization", () => {
  const scene = key("客厅::收纳", "", "");
  const role = key("客厅::收纳", "盒子", "");
  const first = key("客厅::收纳", "盒子", "SKU-1");
  const later = key("客厅::收纳", "盒子", "SKU-2");
  assert.equal(isSelected(fresh(), first), true);
  let state = changeSelection(fresh(), [scene], "off");
  assert.equal(isSelected(state, later), false);
  state = changeSelection(state, [first], "on");
  state = JSON.parse(JSON.stringify(state));
  assert.equal(isSelected(state, first), true);
  assert.equal(isSelected(state, later), false);
  state = changeSelection(state, [role], "on");
  assert.equal(isSelected(state, later), true);
  state = changeSelection(state, [scene], "off");
  assert.equal(isSelected(state, first), false);
  assert.equal(isSelected(state, later), false);
  state = changeSelection(state, [scene], "on");
  assert.equal(isSelected(state, first), true);
  assert.equal(isSelected(state, key("另一场景", "盒子", "SKU-3")), true);
});

const Workbench = load("components/SceneWorkbench.tsx").default;
const candidate = (sku) => ({ main_sku: sku, rank: 1, standard_name_cn: "测试商品", standard_name_en: "Test", country_available: true, country_available_quantity: 3 });
const scenes = [{ scene_name: "客厅", audience: "家庭", user_need: "收纳", product_needs: [{ product_cn: "盒子" }, { product_cn: "篮子" }], excluded: [] }];
const retrieval = { country: "TH", inventory: "available", scenes: [
  { scene_name: "客厅", product_cn: "盒子", candidates: [candidate("A"), candidate("B")], dropped: [candidate("DROP")] },
  { scene_name: "客厅", product_cn: "篮子", candidates: [candidate("A")] },
] };
function renderResult() { return renderToStaticMarkup(React.createElement(Workbench, { storeId: "test", scenes, retrieval, exportBlocked: "" })); }

test("default export counts unique retained SKUs, keeps options visible, and defers candidate DOM", () => {
  storage.clear();
  const html = renderResult();
  assert.match(html, /<strong>1<\/strong> 个场景/);
  assert.match(html, /<strong>2<\/strong> 个主 SKU/);
  assert.match(html, /id="export-section"/);
  assert.match(html, /type="radio"/);
  assert.doesNotMatch(html, /<details|class="candidate-list"|DROP|候选明细/);
  assert.match(html, /选择场景：客厅[^>]*checked=""/);
});

test("saved scene exclusions and legacy empty choices are never reset to all", () => {
  storage.clear();
  storage.set("ssi.selection.v2.test", JSON.stringify(changeSelection(fresh(), [key("客厅", "", "")], "off")));
  assert.match(renderResult(), /<strong>0<\/strong> 个主 SKU/);
  storage.clear(); storage.set("ssi.picked.test", "[]");
  assert.match(renderResult(), /<strong>0<\/strong> 个主 SKU/);
  storage.clear();
});

test("compact progress uses real stage status and has no processing-details panel", () => {
  const Rail = load("components/StageRail.tsx").default;
  const stages = ["recognize", "clues", "synthesis", "scenes", "products", "expand", "retrieval", "rerank"].map((name, i) => ({ name, status: i < 3 ? "ready" : i === 3 ? "running" : "pending", target: "", label: name, note: "technical" }));
  const html = renderToStaticMarkup(React.createElement(Rail, { stages, running: true, summary: "正在生成", onCancel() {} }));
  assert.equal((html.match(/<li /g) ?? []).length, 4);
  assert.match(html, /aria-current="step"/);
  assert.match(html, /店铺分析/);
  assert.doesNotMatch(html, /处理详情|technical|<details/);
});
