// Pure Node isolation: synthetic DOM and fetch; never connects to a browser or service.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('design/unified-workbench.js','utf8');
const hydration=source.slice(source.indexOf('async function hydrateContract('),source.indexOf('function renderDetail('));
const close=source.slice(source.indexOf('function closeDetail(){'),source.indexOf('async function shareDetail('));
let tests=0;
function setup(){
 const els={};const get=id=>els[id]??=( {hidden:true,textContent:'',prepend(x){this.load=x;},querySelector(){return this.load||null;},focus(){this.focused=true;},append(){}} );
 const row=s=>({key:'m|'+s,symbol:s,price:1,raw:{market:'m',symbol:s}});
 const pending=[],timers=new Map();let timerId=0;
 const ctx={AbortController,DOMException,console,encodeURIComponent,document:{activeElement:{isConnected:true,focus(){}},createElement(){return {dataset:{},setAttribute(){},append(){}};}},
  window:{scrollY:12,scrollTo(){}},state:{context:'latest',history:'all'},loadedContext:'latest',
  payload:{projection:'summary-v1',generation:'g1',rows:[{market:'m',symbol:'A'},{market:'m',symbol:'B'}]},rows:[row('A'),row('B')],
  core:{adapt:r=>({key:r.market+'|'+r.symbol})},$:get,host:{addEventListener(){}},detailRequest:0,detail:null,lastFocus:null,detailsScroll:0,
  fmt:String,notice(){},showEmptyDetail(){},render(){},renderDetail(k,tab,exact,loading){get('detailBody').textContent=k;},
  fetch(url,options){return new Promise((resolve,reject)=>pending.push({url,options,resolve,reject}));},
  setTimeout(fn){const id=++timerId;timers.set(id,fn);return id;},clearTimeout(id){timers.delete(id);}};
 vm.createContext(ctx);vm.runInContext(hydration+'\n'+close,ctx);
 const respond=(i,s,g='g1')=>pending[i].resolve({ok:true,json:async()=>({generation:g,rows:[{market:'m',symbol:s,proof:s}]})});
 return {ctx,pending,timers,get,respond};
}
async function test(name,fn){await fn();tests++;console.log('PASS '+name);}
(async()=>{
 await test('drawer opens before full response',async()=>{const h=setup(),p=h.ctx.openDetail('m|A');assert.equal(h.get('drawer').hidden,false);assert.equal(h.pending.length,1);assert.equal(h.get('detailTitle').textContent,'A');h.respond(0,'A');await p;assert.equal(h.ctx.payload.rows[0].proof,'A');assert.equal(h.timers.size,0);});
 await test('close cancels and a late response cannot reopen or mutate',async()=>{const h=setup(),p=h.ctx.openDetail('m|A');h.ctx.closeDetail();assert(h.pending[0].options.signal.aborted);h.respond(0,'A');await p;assert(h.get('drawer').hidden);assert(!h.ctx.payload.rows[0].proof);});
 await test('switch token fences stale response even if transport ignores abort',async()=>{const h=setup(),a=h.ctx.openDetail('m|A'),b=h.ctx.openDetail('m|B');h.respond(0,'A');await a;assert(!h.ctx.payload.rows[0].proof);h.respond(1,'B');await b;assert.equal(h.get('detailTitle').textContent,'B');assert.equal(h.ctx.payload.rows[1].proof,'B');});
 await test('10 second timer aborts and exposes retry without clearing summary',async()=>{const h=setup(),p=h.ctx.openDetail('m|A');h.timers.values().next().value();assert(h.pending[0].options.signal.aborted);h.pending[0].reject(new DOMException('aborted','AbortError'));await p;assert.match(h.get('detailBody').load.textContent,/超时/);assert.equal(h.get('drawer').hidden,false);assert.equal(h.get('detailBody').textContent,'m|A');});
 await test('503 ends loading and preserves the selected token',async()=>{const h=setup(),p=h.ctx.openDetail('m|A');h.pending[0].resolve({ok:false,status:503});await p;assert.match(h.get('detailBody').load.textContent,/正在发布/);assert.equal(h.get('detailTitle').textContent,'A');assert.equal(h.timers.size,0);});
 await test('generation change cannot splice evidence',async()=>{const h=setup(),p=h.ctx.openDetail('m|A');h.ctx.payload.generation='g2';h.respond(0,'A');await p;assert(!h.ctx.payload.rows[0].proof);assert.match(h.get('detailBody').load.textContent,/数据已更新/);});
 await test('wrong contract cannot replace target',async()=>{const h=setup(),p=h.ctx.openDetail('m|A');h.respond(0,'B');await p;assert(!h.ctx.payload.rows[0].proof);assert.match(h.get('detailBody').load.textContent,/未返回目标合约/);});
 await test('summary-render failure still opens drawer with fallback',async()=>{const h=setup();h.ctx.console={error(){}};h.ctx.renderDetail=()=>{throw Error('injected rendering failure');};const p=h.ctx.openDetail('m|A');assert.equal(h.get('drawer').hidden,false);assert.match(h.get('detailBody').textContent,/已有摘要/);h.pending[0].resolve({ok:false,status:503});await p;});
 await test('new cross filters retain unknown and old grade semantics',async()=>{
  const c={};vm.createContext(c);vm.runInContext(fs.readFileSync('design/unified-core.js','utf8'),c);const core=c.UnifiedCore||c.WorkbenchCore||c.UnifiedSignalsCore;
  assert(core,'core export');const raw={market:'m',symbol:'A',ignitionGrade:'none',emaObservation:{long_crosses:{ema7_cross_25:false,ema7_cross_99:true,ema25_cross_99:false}}};const r=core.adapt(raw),s={aStage:'cross_7_99',bStage:'cur_event',cStage:'confirm'},t={};assert.equal(core.signals(r,s,t).i4,true);assert.equal(core.signals(r,{...s,aStage:'strong'},t).i4,false);assert.equal(core.signals(core.adapt({...raw,emaObservation:null}),s,t).i4,null);
 });
 await test('actual detail renderer builds every tab and shows independent cross and B qualification',async()=>{
  const h=setup(),c=h.ctx;
  vm.runInContext(fs.readFileSync('design/unified-core.js','utf8'),c);c.core=c.window.UnifiedCore;c.UnifiedCore=c.core;Object.assign(c.state,{signals:new Set(),mode:"and",aStage:"all_signals",bStage:"cur_event",cStage:"confirm"});
  vm.runInContext(fs.readFileSync('design/market-evidence.js','utf8')+'\nglobalThis.EvidenceForTest=MarketEvidence;',c);c.MarketEvidence=c.EvidenceForTest;
  const raw={market:'m',symbol:'A',quote:'USDT',candleAsOf:1792425599999,ema4h:[3,4,2],closed4h:4,ignitionGrade:'none',emaObservation:{timeframe:'4h',bar_time:1792425599999,quality:'valid',valid_bars:1499,weak_new_cross:false,long_crosses:{ema7_cross_25:false,ema7_cross_99:true,ema25_cross_99:false},long_cross_events:[{kind:'ema7_cross_99'}],recent_long_cross_events:[]}};
  const event={event_id:'e',schema_version:2,technical_event:'pass',technical_confirmation:'pending',structure_gate:'fail',structure_h:{oi_cap_pct:8.2477,lsr:.9904,state:'fail'}};
  const row=c.core.adapt({...raw,bEvents:[event]});row.roundB={state:'fail',event};c.rows=[row];c.thresholds={};c.payload.snapshot={};
  Object.assign(c,{bViewHistory:()=>false,fieldContext:()=>({}),esc:String,rawValue:v=>v??'未知',date:String,hourWindow:String,jsonBlock:()=>'',dataCell:()=>'',cDetailsHtml:()=>'',repairSummary:()=>'',eventListHtml:()=>'',bLabel:{fail:'非完整确认'}});
  vm.runInContext(source.slice(source.indexOf('function bQualificationHtml('),source.indexOf('function jsonBlock(')),c);
  vm.runInContext(source.slice(source.indexOf('function renderDetail('),source.indexOf('function showEmptyDetail(')),c);
  for(const tab of ['all','a','b','c']){c.renderDetail('m|A',tab,null,false);assert(h.get('detailBody').innerHTML.length>100);if(tab==='a')assert.match(h.get('detailBody').innerHTML,/EMA7上穿EMA99/);if(tab==='b'){assert.match(h.get('detailBody').innerHTML,/8.2477/);assert.match(h.get('detailBody').innerHTML,/须＞30%/);assert.match(h.get('detailBody').innerHTML,/非完整确认/);}}
 });
 console.log(tests+' synthetic behavior checks passed; no browser validation claimed');
})().catch(e=>{console.error(e);process.exitCode=1;});
