'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const core=require('./design/unified-core.js');
const source=fs.readFileSync(__dirname+'/design/unified-workbench.js','utf8');
function fixture(){
 const a=core.adapt({market:'USDⓈ-M',symbol:'AGTUSDT',legacySixStates:Array(6).fill(null)}),b=core.adapt({market:'USDⓈ-M',symbol:'BTCUSDT',legacySixStates:Array(6).fill(null)});
 const state={signals:new Set(),mode:'and',minimum:1,search:'AGT',market:'all',cap:'all',minCap:'',maxCap:'',trusted:false,history:'all',context:'latest',listing:'current',result:'pass',selected:new Set(),aStage:'allgrades',bStage:'complete',cStage:'confirm',reserveMode:'covered',reserveThreshold:1};
 const thresholds={tOi:30,tLsr:.8,tApr:-50,tVol:3,tQuiet:2};
 const notices=[],downloads=[],reads=[];
 const box=vm.createContext({core,state,thresholds,rows:[a,b],visible:[a],payload:{generation:'stable',snapshot:{}},loadedContext:'latest',serialize:()=>core.snapshot(state,thresholds),notice:x=>notices.push(x),fieldContext:()=>({history:null}),download:(text,name,type)=>downloads.push({text,name,type}),hydrateContract:async key=>reads.push(key),hydrateFull:async()=>reads.push('full')});
 for(const name of ['prepareExport','csvExport','exportReport'])vm.runInContext(source.match(new RegExp('^async function '+name+'\\(.*$','m'))[0],box);
 return {box,state,a,b,notices,downloads,reads};
}
(async()=>{
 for(const action of ["csvExport(visible,'test.csv')",'exportReport()']){
  const f=fixture();await vm.runInContext(action,f.box);
  assert.deepEqual(f.reads,[f.a.key]);assert.equal(f.downloads.length,1);
  assert.match(f.downloads[0].text,/AGTUSDT/);assert.doesNotMatch(f.downloads[0].text,/BTCUSDT/);
  for(const change of ['query','threshold','generation','context','missing']){
   const g=fixture();let release;g.box.hydrateContract=()=>new Promise(r=>release=r);
   const pending=vm.runInContext(action,g.box);
   if(change==='query'){g.state.search='BTC';g.box.visible=[g.b];}
   if(change==='threshold')g.box.thresholds.tOi=50;
   if(change==='generation')g.box.payload={generation:'new',snapshot:{}};
   if(change==='context')g.state.context='snapshot';
   if(change==='missing')g.box.rows=[];
   release();await pending;assert.equal(g.downloads.length,0,change+' must not export mismatched evidence');assert.match(g.notices.at(-1),/变化|切换/);
  }
 }
 const bulk=fixture();bulk.box.visible=Array.from({length:9},(_,i)=>core.adapt({market:'COIN-M',symbol:'T'+i}));bulk.box.rows=bulk.box.visible;
 await vm.runInContext('exportReport()',bulk.box);assert.deepEqual(bulk.reads,['full']);assert.equal(JSON.parse(bulk.downloads[0].text).records.length,9);
 const failed=fixture();failed.box.hydrateContract=async()=>{throw Error('read failed');};await vm.runInContext('exportReport()',failed.box);assert.equal(failed.downloads.length,0);assert.match(failed.notices.at(-1),/read failed/);
 console.log('PASS: CSV/report exact hydration, full bulk export, filter/threshold/context/generation race rejection, missing rows and failed reads');
})().catch(e=>{console.error(e);process.exitCode=1;});
