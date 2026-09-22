// Offline checks for the design prototype; does not launch a browser or call APIs.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '..');
const file = path.join(root, 'docs/2026-09-22-export-first-prototype.html');
const html = fs.readFileSync(file, 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(script, { filename: file });
const requireFrontend = createRequire(path.join(root, 'frontend/package.json'));
requireFrontend('postcss').parse(html.match(/<style>([\s\S]*?)<\/style>/)[1]);
const markup=html.replace(/<(style|script)>[\s\S]*?<\/\1>/g,'');
const stack=[], voidTags=new Set(['input','br','hr','img','meta','link','area','base','col','embed','param','source','track','wbr']);
for (const [,closing,tag] of markup.matchAll(/<(\/?)([a-z][a-z0-9-]*)\b[^>]*>/gi)) {
  if(voidTags.has(tag))continue;
  if(closing)assert.equal(stack.pop(),tag,`unbalanced closing tag ${tag}`);
  else stack.push(tag);
}
assert.deepEqual(stack,[],'unclosed markup');

const context = vm.createContext({});
vm.runInContext(script.match(/\/\/ MODEL START[^\n]*\n([\s\S]*?)\/\/ MODEL END/)[1] +
  '\nthis.model={mxItems,mxChoose,mxCounts,mxPhase,mxFlowPaths};', context);
const { mxItems, mxChoose, mxCounts, mxPhase, mxFlowPaths } = context.model;
const plain = value => JSON.parse(JSON.stringify(value));
assert.deepEqual(plain(mxCounts(mxChoose('all', []))), { scenes: 3, perScene: 9, unique: 8 });
assert.equal(mxChoose('stock', []).length, 6);
assert.ok(mxChoose('stock', []).every(item => item.stock > 0));
assert.ok(mxChoose('all', []).some(item => item.review === '不相关'));
assert.ok(mxChoose('all', []).some(item => item.stock === null));
assert.equal(mxChoose('custom', []).length, 0);
const picked = [0, 2, 5];
assert.deepEqual(plain(mxCounts(mxChoose('custom', picked))), { scenes: 2, perScene: 3, unique: 2 });
mxChoose('stock', picked);
assert.deepEqual(picked, [0, 2, 5]);
assert.deepEqual(plain(mxCounts([...mxItems, mxItems[0]])), { scenes: 3, perScene: 9, unique: 8 });

assert.deepEqual(plain(mxPhase(0)), { stage:0, analysisReady:false, done:false });
assert.deepEqual(plain(mxPhase(7.99)), { stage:0, analysisReady:false, done:false });
assert.deepEqual(plain(mxPhase(8)), { stage:1, analysisReady:true, done:false });
assert.equal(mxPhase(15).stage, 2);
assert.equal(mxPhase(22).stage, 3);
assert.deepEqual(plain(mxPhase(39.99)), { stage:3, analysisReady:true, done:false });
assert.deepEqual(plain(mxPhase(40)), { stage:4, analysisReady:true, done:true });
for (const [width,height,compact,narrow] of [[480,257,false,false],[264,192,false,true],[320,90,true,false],[90,94,true,true]]) {
  for (let stage=0;stage<4;stage++) {
    const paths=mxFlowPaths(stage,width,height,compact,narrow);
    assert.equal(paths.length, stage===3?32:3);
    for (const route of paths) {
      const coords=route.match(/-?\d+(?:\.\d+)?/g).map(Number);
      assert.equal(coords.length, 8);
      assert.ok(coords.every((n,i)=>n>=0&&n<=(i%2?height:width)), 'flow escaped its canvas');
    }
  }
}

const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
assert.equal(new Set(ids).size, ids.length, 'duplicate element ID');
for (const [, id] of script.matchAll(/q\('#([^']+)'\)/g)) assert.ok(ids.includes(id), `missing #${id}`);
for (const [, id] of html.matchAll(/\bfor="([^"]+)"/g)) assert.ok(ids.includes(id), `missing label target #${id}`);
assert.doesNotMatch(html, /<html|<!doctype|<iframe|\bfetch\s*\(|XMLHttpRequest|WebSocket|https?:\/\//i);
assert.ok(Buffer.byteLength(html) < 1_000_000);
assert.match(html, /prefers-reduced-motion:reduce/);
assert.match(html, /getTotalLength\(\)/);
assert.match(html, /模拟导出/);
assert.doesNotMatch(html, /data-lucide|lucide\.createIcons|data-page="running"|data-page="result"/);
assert.equal([...html.matchAll(/data-report-panel="\d"/g)].length, 6);
const progress = script.match(/function renderProgress\(\)\{([\s\S]*?)\n  function render\(/)[1];
assert.doesNotMatch(progress, /state\.(?:view|content|report)\s*=(?!=)/, 'progress must not navigate away from reading');
assert.match(progress, /\.inert=!phase.analysisReady/);
assert.match(script, /disabled=!done\|\|!rows.length/);
assert.match(script, /!reduced\.matches/, 'motion must stay behind a reduced-motion guard');
// A store list that answers "did this shop finish?", on the same clock as the workbench.
assert.match(markup, /data-page="stores"/);
assert.match(script, /function renderStores\(\)/);
assert.match(script, /renderResult\(\);renderStores\(\)/);
assert.match(progress, /mxPhase\(state\.elapsed\)/, 'the store list follows the live phase');
const tick = script.match(/function tick\(now\)\{([\s\S]*?)\n  \}/)[1];
assert.match(script, /const state=\{version:2,view:'workbench'/, 'the demo must open where the animation is visible');
assert.match(tick, /state\.view==='workbench'\)\{[\s\S]*?lights\.forEach[\s\S]*?tails\.forEach/, 'motion runs only where the canvas is on screen');
assert.doesNotMatch(tick, /!=='input'/, 'the clock must not run behind the store list or the input form');
// The scene picker and the recalled SKUs are two bands, not one list.
assert.equal([...markup.matchAll(/class="mx-band-scene"/g)].length, 2, '两个预览面板各有场景板块');
assert.equal([...markup.matchAll(/class="mx-band-recall"/g)].length, 1);
assert.ok(markup.indexOf('mx-band-scene') < markup.indexOf('mx-band-recall'), '场景在上，召回在下');
assert.match(markup, /<h3>使用场景<\/h3>[\s\S]*?<h3>召回商品<\/h3>/, '两块各有自己的标题');
assert.match(html, /--mx-accent:#0a8fd4/);
assert.doesNotMatch(html, /#3679d4/i, 'the old flat blue must not survive the recolor');
// The accent has to read as blue, not as the cyan/teal it replaced. Hue, not the literal:
// a future tweak to a bluer or lighter value should still pass, a swing back to teal should not.
const accent = html.match(/--mx-accent:#([0-9a-f]{6})/)[1];
const [ar, ag, ab] = [0, 2, 4].map(i => parseInt(accent.slice(i, i + 2), 16) / 255);
const accentHue = 240 + 60 * (ar - ag) / (Math.max(ar, ag, ab) - Math.min(ar, ag, ab));
assert.ok(accentHue > 195 && accentHue < 230, `the accent reads blue, got hue ${accentHue.toFixed(0)}`);
assert.doesNotMatch(html, /8 个 SKU|8 个主 SKU|3 个场景/, 'counts are derived, not written in');
console.log('PASS: script/CSS syntax, balanced markup, export scopes, deduplication, selection preservation, automatic same-page stages, early analysis, six report sections, flow geometry, store status page, scene/recall band split, derived counts and reduced-motion guards.');
console.log('Browser layout and rendered interaction have not been tested.');
