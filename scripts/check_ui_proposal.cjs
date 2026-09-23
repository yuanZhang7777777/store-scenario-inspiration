// Browser-free checks: node scripts/check_ui_proposal.cjs
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const {createRequire}=require('node:module');
const root=path.resolve(__dirname,'..'),file=path.join(root,'docs/2026-09-22-export-first-prototype.html');
const html=fs.readFileSync(file,'utf8'),script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(script,{filename:file});
createRequire(path.join(root,'frontend/package.json'))('postcss').parse(html.match(/<style>([\s\S]*?)<\/style>/)[1]);
const markup=html.replace(/<(style|script)>[\s\S]*?<\/\1>/g,'');
const stack=[],parents={},voidTags=new Set(['input','br','hr','img']);
for(const [token,closing,tag] of markup.matchAll(/<(\/?)([a-z][a-z0-9-]*)\b[^>]*>/gi)){
  const id=token.match(/\bid="([^"]+)"/)?.[1];
  if(id)parents[id]=stack.map(node=>node.id).filter(Boolean);
  if(voidTags.has(tag))continue;
  if(closing)assert.equal(stack.pop().tag,tag,'unbalanced HTML');else stack.push({tag,id});
}
assert.deepEqual(stack,[]);
const ids=[...markup.matchAll(/\bid="([^"]+)"/g)].map(match=>match[1]);
assert.equal(new Set(ids).size,ids.length,'duplicate IDs');
for(const [,id] of script.matchAll(/q\('#([^']+)'\)/g))assert.ok(ids.includes(id),'missing #'+id);
for(const [,id] of markup.matchAll(/\bfor="([^"]+)"/g))assert.ok(ids.includes(id),'missing label target');
assert.doesNotMatch(html,/<html|<!doctype|<iframe|\bfetch\s*\(|XMLHttpRequest|WebSocket|https?:\/\//i);
assert.doesNotMatch(markup,/<canvas|role="tab"/,'no large animation board or result tabs');
assert.deepEqual([...markup.matchAll(/data-page="([^"]+)"/g)].map(match=>match[1]),['home','work']);
assert.ok(parents['mx-history'].includes('mx-home'),'history belongs to the homepage');
assert.ok(parents['mx-new-store'].includes('mx-home'),'new store entry belongs to the homepage');
for(const id of ['mx-import','mx-current','mx-export','mx-back']){
  assert.ok(parents[id].includes('mx-work'),id+' belongs to the store workspace');
  assert.ok(!parents[id].includes('mx-home'));
}
assert.ok(Buffer.byteLength(html)<1_000_000);
assert.match(html,/prefers-reduced-motion:reduce/);
assert.match(markup,/固定示例数据/);

const context=vm.createContext({});
vm.runInContext(script.match(/\/\/ MODEL START[^\n]*\n([\s\S]*?)\/\/ MODEL END/)[1]+
  '\nthis.model={mxCatalog,mxPhase,mxRows,mxCounts,mxCanExport,mxSelect,mxAdvance,mxNewStore,mxNavigate};',context);
const {mxCatalog,mxPhase,mxRows,mxCounts,mxCanExport,mxSelect,mxAdvance,mxNewStore,mxNavigate}=context.model;
const plain=value=>JSON.parse(JSON.stringify(value));
const ready=mxNewStore('outdoor','户外','菲律宾','outdoor','ready',24);
assert.deepEqual(plain(mxCounts(mxRows(ready))),{scenes:3,perScene:9,unique:8});
assert.equal(mxCanExport(ready),true,'default export needs no manual picks');
const stock={...ready,scope:'stock'};
assert.equal(mxRows(stock).length,6);
assert.ok(mxRows(stock).every(row=>row.stock>0));
assert.ok(mxRows(ready).some(row=>row.stock===null));
assert.ok(mxRows(ready).some(row=>row.review==='不相关'));
const custom={...ready,scope:'custom',picked:[0,2,5]};
assert.deepEqual(plain(mxCounts(mxRows(custom))),{scenes:2,perScene:3,unique:2});
mxRows({...custom,scope:'stock'});
assert.deepEqual(custom.picked,[0,2,5],'changing scope must not overwrite custom picks');
assert.equal(mxCanExport({...custom,picked:[]}),false);
for(const override of [{status:'running'},{status:'failed'},{status:'cancelled'},{snapshot:false},{stale:true},{elapsed:20}])
  assert.equal(mxCanExport({...ready,...override}),false,'incomplete/stale exports must stay disabled');
assert.deepEqual(plain(mxSelect([0,4],[0,1,2])),[0,4,1,2]);
assert.deepEqual(plain(mxSelect([0,1,2,4],[0,1,2])),[4]);
assert.deepEqual(plain(mxSelect(mxSelect([0,4],[0,1,2],true),[0,1,2],true)).sort(),[0,4]);

const running=mxNewStore('new','新店','菲律宾');
for(const [seconds,key] of [[7,'analysis'],[10,'scenes'],[13,'needs'],[20,'catalog'],[24,'done']]){
  assert.equal(mxPhase(seconds-0.01)[key],false);
  assert.equal(mxPhase(seconds)[key],true);
}
mxAdvance(running,8);
assert.equal(mxPhase(running.elapsed).analysis,true);
running.status='failed';mxAdvance(running,100);
assert.equal(running.elapsed,8,'failure must never animate to success');
running.status='cancelled';mxAdvance(running,100);
assert.equal(running.elapsed,8);
running.status='running';mxAdvance(running,16);
assert.equal(mxCanExport(running),true,'resume preserves partial progress and completes snapshot');
const other=mxNewStore('desk','收纳','马来西亚','desk','failed',17);
const state={page:'home',drafting:false,selected:'outdoor',stores:[ready,other],draft:{country:'泰国',name:'还没有提交'}};
const elements={};let contentRenders=0;
context.state=state;context.q=selector=>(elements[selector]??={});context.current=()=>state.stores.find(store=>store.id===state.selected);
context.history=()=>{};context.renderContent=()=>contentRenders++;
vm.runInContext('this.render=function(){'+script.match(/function render\(\)\{([\s\S]*?)\n  \}\n  function rememberOpen/)[1]+'}',context);
context.render();
assert.equal(elements['#mx-home'].hidden,false);
assert.equal(elements['#mx-work'].hidden,true);
assert.equal(contentRenders,0,'homepage must not render a store report');
assert.equal(mxNavigate(state,'new'),true);context.render();
assert.equal(elements['#mx-home'].hidden,true);
assert.equal(elements['#mx-import'].hidden,false);
assert.equal(elements['#mx-current'].hidden,true,'new store must not show the previously opened store');
assert.equal(mxNavigate(state,'home'),true);
assert.equal(mxNavigate(state,'new'),true);
assert.deepEqual(state.draft,{country:'泰国',name:'还没有提交'},'returning home must preserve draft input');
ready.picked=[2];
ready.open=['report'];
assert.equal(mxNavigate(state,'store','desk'),true);context.render();
assert.equal(elements['#mx-import'].hidden,true);
assert.equal(elements['#mx-current'].hidden,false);
assert.equal(state.page,'work');
assert.equal(state.drafting,false);
assert.notEqual(mxCatalog[ready.kind].conclusion,mxCatalog[other.kind].conclusion);
assert.equal(mxNavigate(state,'store','missing'),false);
assert.equal(state.selected,'desk');
assert.deepEqual(state.draft,{country:'泰国',name:'还没有提交'});
assert.equal(mxNavigate(state,'store','outdoor'),true);
assert.deepEqual(ready.picked,[2],'history selection preserves each store selection');
assert.deepEqual(ready.open,['report']);
assert.equal(ready.status,'ready');assert.equal(ready.elapsed,24,'opening completed work never restarts it');
mxNavigate(state,'home');
other.status='running';mxAdvance(other,7);
assert.equal(other.status,'ready','unselected tasks continue');
assert.equal(state.selected,'outdoor','background completion cannot select another store');
assert.equal(state.page,'home','background completion cannot leave the homepage');

const timer=script.match(/const timer=setInterval\(\(\)=>\{([\s\S]*?)\},1000\)/)[1];
assert.doesNotMatch(timer,/scrollIntoView|focusPage|mxNavigate|\.(?:selected|page)\s*=(?!=)/);
assert.match(script,/if\(!role\.open\)return/,'closed product roles do not render SKU rows');
assert.match(script,/if\(contentStoreId!==store\.id\)/,'polling must not rebuild the current report');
console.log('PASS: HTML/JS/CSS, two-page ownership/visibility/navigation, draft/history preservation, background progress, export gates/scopes/counts, retry, lazy SKU rendering, reduced motion.');
console.log('No browser rendering, real API jobs, or XLSX download was performed.');
