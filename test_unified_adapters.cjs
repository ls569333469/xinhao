'use strict';
// Pure function checks from the shipped runtime; no browser or network access.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const C=require('./design/unified-core.js');
const source=fs.readFileSync(__dirname+'/design/unified-workbench.js','utf8');
async function main(){
let exported=null,notices=[],reloads=0,renders=0;
const state={signals:new Set(),mode:'and',minimum:1,market:'all',search:'',cap:'all',minCap:'',maxCap:'',trusted:false,listing:'current',history:'all',context:'latest',aStage:'strongmedium',bStage:'complete',cStage:'confirm',result:'pass',sort:'symbol',desc:false};
const thresholds={tOi:30,tLsr:.8,tApr:-50,tVol:3,tQuiet:2};
const row=C.adapt({market:'USDⓈ-M',symbol:'TESTUSDT',legacySixStates:[null,null,null,null,null,null]});
const box={core:C,state,thresholds,payload:{generation:'stable',snapshot:{}},visible:[row],loadedContext:'latest',download:(text,name,type)=>{exported={text,name,type};},notice:x=>notices.push(x),serialize:()=>C.snapshot(state,thresholds),render:()=>renders++,reload:()=>{reloads++;return Promise.resolve();}};
vm.createContext(box);
box.hydrateFull=box.hydrateContract=async()=>{};box.rows=[row];
vm.runInContext(source.match(/^async function prepareExport\(.*$/m)[0],box);
vm.runInContext(source.match(/^async function exportReport\(\).*$/m)[0],box);
await vm.runInContext('exportReport()',box);
assert.equal(exported.type,'application/json');
const data=JSON.parse(exported.text);
assert.equal(data.generation,'stable');assert.equal(data.contract_count,1);assert.equal(data.records[0].B_event_id,null);
box.loadedContext='snapshot';exported=null;await vm.runInContext('exportReport()',box);assert.equal(exported,null);assert.match(notices.at(-1),/上下文/);
box.loadedContext='latest';box.hydrateContract=async()=>{throw Error('generation changed');};await vm.runInContext('exportReport()',box);assert.equal(exported,null);assert.match(notices.at(-1),/generation/);
vm.runInContext(source.match(/^function change\(\).*$/m)[0],box);
box.loadedContext='snapshot';
vm.runInContext('change()',box);assert.equal(reloads,1);assert.equal(renders,1);
box.aOptions=vm.runInNewContext('('+source.match(/const aOptions=(\{[^\n]+\});/)[1]+')');
const restore=source.slice(source.indexOf('function restoreHash()'),source.indexOf("$('reloadSaved').onclick"));
const fake=()=>({});Object.assign(box,{location:{hash:'#'+new URLSearchParams({filters:JSON.stringify({...C.snapshot(state,thresholds),trusted:true})}),search:''},URLSearchParams,startupSavedQuery:null,defaults:thresholds,lookup:()=>null,rows:[],showEmptyDetail:()=>{throw Error('Must not substitute a missing contract');},openDetail:()=>{throw Error('No event requested');}});
box.loadedContext='latest';vm.runInContext(restore,box);vm.runInContext('restoreHash()',box);assert.equal(state.trusted,true);
console.log('shipped adapters: JSON report, generation gate, context reload, trusted-scope restore passed');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
