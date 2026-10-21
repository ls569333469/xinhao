'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const root=process.argv[2]||__dirname,C=require(path.join(root,'design/unified-core.js'));
const source=fs.readFileSync(path.join(root,'design/unified-workbench.js'),'utf8');
const defaults={tOi:30,tLsr:.8,tApr:-50,tVol:3,tQuiet:2};
function state(){return {signals:new Set(['i4']),mode:'and',minimum:1,search:'',market:'all',cap:'all',minCap:'',maxCap:'',trusted:false,history:'all',context:'latest',listing:'current',aStage:'grade_mediumstrong',bStage:'cur_event',bFrom:'',bTo:'',cStage:'confirm',reserveMode:'covered',reserveThreshold:1,result:'pass',sort:'symbol',desc:true};}
let checks=0;function test(name,fn){fn();checks++;console.log('PASS '+name);}
function harness(){
 const nodes=new Map(),node=id=>{if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',close(){this.closed=true;}});return nodes.get(id);};
 const c={core:C,state:state(),thresholds:{...defaults},defaults:{...defaults},payload:null,esc:String,editing:'i4',expandedSignals:new Set(),signalCount:()=>0,
  $:node,$$:()=>[],activateSignal:()=>{},change:()=>{},location:{hash:'',search:''},startupSavedQuery:null,URLSearchParams,
  loadedContext:'latest',lookup:id=>['i1','i2','i3','i4','i5','i6','b','c'].includes(id)?{id}:null,rows:[],notices:[],render(){},reload(){throw Error('Unexpected context reload');},notice(x){this.notices.push(x);}};
 c.notice=x=>c.notices.push(x);vm.createContext(c);
 vm.runInContext(source.slice(source.indexOf('const aOptions='),source.indexOf('const bOptions=')),c);
 vm.runInContext(source.match(/^function profile\(.*$/m)[0],c);
 vm.runInContext('const basicAGrades=Object.entries(aOptions);'+source.slice(source.indexOf('function aGradeOptions('),source.indexOf('function signalCount(')),c);
 vm.runInContext(source.slice(source.indexOf('function pickerHtml('),source.indexOf('function activeFilterSummary(')),c);
 vm.runInContext(source.slice(source.indexOf('function applySettings('),source.indexOf('function download(')),c);
 vm.runInContext(source.slice(source.indexOf('function restoreHash()'),source.indexOf("$('reloadSaved').onclick")),c);
 return {c,node};
}
const saved=c=>C.snapshot(c.state,c.thresholds);
const restore=(c,query,session=false)=>{if(session){c.location.hash='';c.startupSavedQuery=JSON.stringify(query);}else c.location.hash='#'+new URLSearchParams({filters:JSON.stringify(query)});c.restoreHash();};
test('old preset survives session restore with exact original grade semantics',()=>{
 const {c}=harness();const q={...saved(c),aStage:'strongmedium',signals:['i1','i2','i4']};restore(c,q,true);
 assert.equal(c.state.aStage,'strongmedium');assert.deepEqual([...c.state.signals],q.signals);
 const raw={market:'USDⓈ-M',symbol:'TESTUSDT',ignitionGrade:'strong',legacySixStates:[true,true,true,true,null,true]};
 assert.equal(C.signals(C.adapt(raw),c.state,c.thresholds).i4,true);
 assert.equal(C.signals(C.adapt(raw),{...c.state,aStage:'grade_mediumstrong'},c.thresholds).i4,null);
});
test('every supported compatibility stage survives shared URL restore',()=>{
 const {c}=harness();const keys=vm.runInContext('Object.keys(legacyAOptions)',c);for(const key of keys){restore(c,{...saved(c),aStage:key});assert.equal(c.state.aStage,key);}
});
test('new six stages survive restore unchanged and show exactly six normal options',()=>{
 const {c}=harness();for(const key of vm.runInContext('Object.keys(aOptions)',c)){restore(c,{...saved(c),aStage:key});assert.equal(c.state.aStage,key);const html=c.aGradeOptions([]);assert.equal((html.match(/<option /g)||[]).length,6);assert.match(html,new RegExp('value="'+key+'" selected'));}
});
test('opening compatibility settings and applying unchanged cannot fall back to all',()=>{
 const {c,node}=harness();c.state.aStage='strongmedium';const html=c.aGradeOptions([]);const selected=html.match(/<option value="([^"]+)" selected/);assert(selected);assert.equal(selected[1],'strongmedium');
 node('configStage').value=selected[1];c.applySettings();assert.equal(c.state.aStage,'strongmedium');assert.equal(node('signalSettings').closed,true);
 node('configStage').value='grade_mediumstrong';c.applySettings();assert.equal(c.state.aStage,'grade_mediumstrong');
});
test('sidebar makes original compatibility condition visible while retaining six new choices',()=>{
 const {c}=harness();c.state.aStage='strongmedium';const html=c.pickerHtml({id:'i4',name:'EMA金叉',num:'④'},[]);
 assert.match(html,/原A点火等级：中＋强/);assert.match(html,/尚未选择新版技术等级/);assert.equal((html.match(/type="radio"/g)||[]).length,6);
});
test('bad stage rejects entire query and thresholds without widening active filter',()=>{
 const {c}=harness(),before=JSON.stringify(saved(c));restore(c,{...saved(c),signals:[],search:'other',aStage:'retired_unknown',thresholds:{...defaults,tOi:999}});
 assert.equal(JSON.stringify(saved(c)),before);assert.match(c.notices.at(-1),/无效/);
});
test('bad scope type unknown signal invalid threshold and cap range are atomic rejections',()=>{
 for(const delta of [{trusted:'true'},{signals:['i4','removed']},{thresholds:{...defaults,tVol:'8'}},{market:'removed'},{cap:'custom',minCap:'10',maxCap:'1'}]){
  const {c}=harness(),before=JSON.stringify(saved(c));restore(c,{...saved(c),search:'changed',...delta});assert.equal(JSON.stringify(saved(c)),before);assert.match(c.notices.at(-1),/无效/);
 }
});
test('strategy guide identifies every new and compatibility stage and preserves return filters',()=>{
 const {c}=harness(),nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,{});return nodes.get(id);};
 const query=JSON.stringify({...saved(c),aStage:'grade_watch'});
 const box={document:{getElementById:get,addEventListener(){}},window:{addEventListener(){}},location:{search:'?'+new URLSearchParams({filters:query}),hash:''},URLSearchParams,localStorage:{getItem(){return '[]';}},history:{replaceState(){}}};vm.createContext(box);
 vm.runInContext(fs.readFileSync(path.join(root,'design/unified-combos.js'),'utf8'),box);vm.runInContext(fs.readFileSync(path.join(root,'design/unified-guide.js'),'utf8'),box);
 for(const stage of vm.runInContext('[...Object.keys(aOptions),...Object.keys(legacyAOptions)]',c)){
  const label=box.signalCondition({aStage:stage,thresholds:defaults},'i4');assert(!/无效|待复核/.test(label));assert(stage.startsWith('grade_')?label.includes('4H技术等级'):label.includes('兼容条件'));
 }
 assert.equal(new URLSearchParams(get('backWorkbench').href.split('#')[1]).get('filters'),query);
 assert.match(get('guideContent').innerHTML,/新版六档按4H技术等级筛选/);
});
test('legacy storage inputs are disabled but backup button and actual config stay active',()=>{
 const inputs=[{disabled:false},{disabled:false}],backup={disabled:false};const advanced={id:'configRuntimePanel',classList:{contains:k=>k==='panel'||k==='advanced-config'}};
 const storage={children:[],append(n){this.children.push(n);n.parentNode=this;}};
 const base={id:'',classList:{contains:k=>k==='panel'},parentNode:storage,querySelectorAll:s=>s==='input,select,textarea'?inputs:[],prepend(n){this.note=n;}};storage.children=[base,advanced];
 const document={readyState:'complete',querySelector:s=>s==='[data-setting-view="storage"]'?storage:null,querySelectorAll:()=>[],addEventListener(){},createElement(){return {append(n){n.parentNode=this;},className:'',innerHTML:''};}};
 const box={document};vm.createContext(box);vm.runInContext(fs.readFileSync(path.join(root,'design/workbench-integrity.js'),'utf8'),box);
 assert(inputs.every(x=>x.disabled));assert.match(base.note.textContent,/只读预览，不保存、不生效/);assert.equal(backup.disabled,false);assert(!advanced.disabled);
});
test('one query serializer and no dead legacy guide navigation remain',()=>{
 assert.equal((source.match(/function serialize\(/g)||[]).length,1);assert(!source.includes('previewUpdateComboHelp'));
 assert(source.includes("url='/unified-strategies.html?filters='"));assert(!/data-setting=/.test(fs.readFileSync(path.join(root,'design/unified-workbench-fragment.html'),'utf8')));
});
console.log(JSON.stringify({passed:checks,kind:'actual runtime functions and synthetic DOM; no real browser'}));
