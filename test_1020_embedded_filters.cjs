'use strict';
// Test the actual entry page scripts, not only their standalone counterparts.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const root=path.resolve(process.argv[2]||__dirname);
const html=fs.readFileSync(process.argv[3]||path.join(root,'design/prototype.html'),'utf8');
const assets=new Map([...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)].flatMap(m=>{
 const a=m[1].match(/data-startup-asset="([^"]+)"/);return a?[[a[1],m[2]]]:[];
}));
const work=assets.get('unified-workbench.js');
const coreCtx={};vm.createContext(coreCtx);vm.runInContext(assets.get('unified-core.js'),coreCtx);
const C=coreCtx.UnifiedCore;
const state=()=>vm.runInNewContext('('+work.match(/const state=(\{[^\n]+\});/)[1]+')',{selectedTokens:new Set()});
const thresholds={tOi:30,tLsr:.8,tApr:-50,tVol:3,tQuiet:2};
let checks=0,failed=0;
async function check(name,fn){try{await fn();checks++;console.log('PASS '+name);}catch(e){failed++;console.error('FAIL '+name+': '+e.message);}}
function raw(symbol,hit){return {market:'USDⓈ-M',symbol,quote:'USDT',ignitionGrade:'none',legacySixStates:[true,true,true,true,null,true],emaObservation:{long_crosses:{ema7_cross_25:false,ema7_cross_99:hit,ema25_cross_99:false},long_stack:false}};}
function harness(){
 const nodes=new Map();
 const node=id=>{if(!nodes.has(id))nodes.set(id,{value:'',dataset:{},classList:{toggle(){}},setAttribute(){},blur(){},append(){},querySelector(){return node(id+':child');},querySelectorAll(){return [];},closest(){return node(id+':parent');},insertAdjacentHTML(){}});return nodes.get(id);};
 const ctx={core:C,state:state(),thresholds,rows:[],visible:[],payload:{context:'latest',generation:'old',rows:[raw('BRUSDT',true),raw('BTCUSDT',false)]},loadedContext:'latest',loadError:null,loadTask:null,refreshSequence:0,LOAD_TIMEOUT_MS:30000,
  document:{activeElement:null,createElement:()=>node('created')},host:{contains:()=>false},$:node,$$:()=>[],catalog:[],rawCols:[],rawDefs:[],resultRows:[],currentRows:[],pageRows:[],filteredForReports:[],
  publicState:r=>C.scope(r,ctx.state),resultKey:v=>v===true?'pass':v===false?'fail':'unknown',bViewHistory:()=>false,
  refreshCombos(){},pickerHtml(){return '';},rowHtml:r=>'<tr>'+r.symbol+'</tr>',esc:String,emptyResultMessage:()=> '没有匹配项',
  syncScope(){},chips(){},renderBView(){},updateComboHelp(){},serialize:()=>C.snapshot(ctx.state,thresholds),sessionStorage:{setItem(){}},
  window:{dispatchEvent(){}},CustomEvent:function(){},loadMessage:()=>ctx.loadError?.message||'读取中',renderBArchive(){},notice(){},
  performance,AbortController,queueMicrotask,console:{info(){},error(){}},setTimeout:()=>1,clearTimeout(){},fetch(){throw Error('fetch_unconfigured');}};
 vm.createContext(ctx);
 const cut=(start,end)=>work.slice(work.indexOf(start),work.indexOf(end,work.indexOf(start)));
 vm.runInContext(cut('function evaluation(r)','const triLabels='),ctx);
 vm.runInContext(cut('function render(){','function updateComboHelp()'),ctx);
 vm.runInContext(cut('function reload({force=false}={})','async function prepareExport('),ctx);
 ctx.render();return {ctx,nodes};
}
(async()=>{
 await check('all five embedded assets match standalone source',()=>{
  const normalize=s=>s.replace(/\r\n/g,'\n').trim();
  assert.equal(assets.size,5);for(const [name,body]of assets)assert.equal(normalize(body),normalize(fs.readFileSync(path.join(root,'design',name),'utf8')),name);
 });
 await check('all inline JS parses',()=>{for(const [i,m]of [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)].entries())if(!/\btype="(?:application\/json|importmap)"/.test(m[1]))new vm.Script(m[2],{filename:'inline-'+i});});
 await check('every displayed stage is accepted by embedded core',()=>{
  for(const [name,key]of [['aOptions','aStage'],['bOptions','bStage'],['cOptions','cStage']]){
   const options=vm.runInNewContext('('+work.match(new RegExp('const '+name+'=(\\{[^\\n]+\\});'))[1]+')');
   for(const value of Object.keys(options)){const s=state();s[key]=value;assert.equal(C.validate(s,thresholds),'',key+'='+value);}
  }
 });
 await check('invalid stage remains rejected',()=>{const s=state();s.aStage='invalid_future_stage';assert.match(C.validate(s,thresholds),/阶段条件版本无效/);});
 await check('7/99 uses true false unknown and preserves original A grades',()=>{
  const s=state();s.aStage='cross_7_99';for(const v of [true,false,null])assert.equal(C.signals(C.adapt(raw('BRUSDT',v)),s,thresholds).i4,v);
  s.aStage='strong';assert.equal(C.signals(C.adapt({...raw('BRUSDT',false),ignitionGrade:'strong'}),s,thresholds).i4,true);
 });
 await check('initial list search clear search and new cross filter keep correct rows',()=>{
  const {ctx:c,nodes}=harness();assert.equal(c.visible.length,2);c.state.search='BR';c.change();assert.equal(c.visible.length,1);assert.equal(c.visible[0].symbol,'BRUSDT');
  c.state.search='';c.change();assert.equal(c.visible.length,2);c.state.signals.add('i4');c.state.aStage='cross_7_99';c.change();assert.equal(c.visible.length,1);assert.equal(c.visible[0].symbol,'BRUSDT');assert.equal(nodes.get('queryError').hidden,true);
 });
 await check('same context refresh pending and 503 retain searchable previous rows',async()=>{
  const {ctx:c}=harness();let reject;c.fetch=()=>new Promise((_,r)=>reject=r);const p=c.reload();c.state.search='BR';c.change();assert.equal(c.visible.length,1);assert.equal(c.payload.generation,'old');
  reject(Error('服务返回HTTP 503'));await assert.rejects(p,/503/);assert.equal(c.visible.length,1);assert.equal(c.payload.generation,'old');assert.equal(c.loadedContext,'latest');
 });
 await check('successful same context refresh replaces payload after full body',async()=>{
  const {ctx:c}=harness();let resolve;c.fetch=()=>new Promise(r=>resolve=r);const p=c.reload();assert.equal(c.visible.length,2);
  resolve({ok:true,status:200,text:async()=>JSON.stringify({context:'latest',generation:'new',rows:[raw('ETHUSDT',false)]})});await p;assert.equal(c.visible.length,1);assert.equal(c.visible[0].symbol,'ETHUSDT');assert.equal(c.payload.generation,'new');
 });
 await check('different context never presents stale latest rows',async()=>{
  const {ctx:c}=harness();c.state.context='snapshot';let reject;c.fetch=()=>new Promise((_,r)=>reject=r);const p=c.reload();c.render();assert.equal(c.visible.length,0);reject(Error('offline'));await assert.rejects(p);assert.equal(c.visible.length,0);
 });
 await check('incomplete or mismatched response preserves last complete generation',async()=>{
  for(const bad of [{context:'snapshot',generation:'bad',rows:[]},{context:'latest',rows:[]},{context:'latest',generation:'bad',rows:null}]){
   const {ctx:c}=harness();c.fetch=async()=>({ok:true,status:200,text:async()=>JSON.stringify(bad)});await assert.rejects(c.reload(),/格式不完整/);assert.equal(c.payload.generation,'old');assert.equal(c.visible.length,2);
  }
 });
 console.log(JSON.stringify({passed:checks,failed,kind:'synthetic DOM and actual embedded scripts; not a real browser'}));process.exitCode=failed?1:0;
})();
