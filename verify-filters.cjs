// Execute actual current filters, without fixed snapshot-count expectations or network.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const read=n=>fs.readFileSync(__dirname+'/'+n,'utf8');
const html=read('index.html'),source=html.split('<script>')[1].split('</script>')[0];
new vm.Script(source);
const inputs={},get=id=>inputs[id]??={value:'',checked:false,validity:{valid:true}};
const context=vm.createContext({document:{querySelector:s=>get(s.slice(1))}});context.window=context;
vm.runInContext(read('design/strategy-registry.js'),context);
vm.runInContext(read('design/market-evidence.js'),context);
vm.runInContext(source.slice(source.indexOf("'use strict'"),source.indexOf('const cell='))+'\nglobalThis.api={defaults,flags,thresholdStatus,combineStatus,resultStatus,scopeStatus,compareRows,validation,syncEmaOptions};',context);
const api=context.api;
const reset=()=>{for(const [id,v]of Object.entries(api.defaults)){const el=get(id);typeof v==='boolean'?el.checked=v:el.value=v;}get('poolViewMode').value='six';['Weak','Medium','Strong'].forEach(g=>get('ignition'+g).checked=false);};
const row=o=>({symbol:'TESTUSDT',asset:'TEST',market:'USDⓈ-M',quote:'USDT',capUsd:2e7,capTrusted:true,oiCapPct:40,accountRatio:0.5,fundingAprPct:-100,fundingIntervalStatus:'verified',closed4h:110,ema4h:[90,100,80],ignitionGrade:'weak',belowAllEma:false,volumeRatio:4,priceChangeWindowPct:1,...o});
reset();
for(let mask=0;mask<3**6;mask++){let n=mask;const values=Array.from({length:6},()=>{const v=[true,false,null][n%3];n=Math.floor(n/3);return v;});const u=values.filter(v=>v===null).length,h=values.filter(v=>v===true).length;for(let t=1;t<=6;t++)assert.equal(api.thresholdStatus(values,t),h>=t?true:h+u<t?false:null);}
get('indicator4').checked=true;get('ignitionWeak').checked=true;
assert.equal(api.resultStatus(row()),true);assert.equal(api.resultStatus(row({ignitionGrade:'medium'})),false);
assert.equal(api.resultStatus(row({ignitionGrade:'none',belowAllEma:true})),false);
assert.equal(api.resultStatus(row({ignitionGrade:'unknown',ema4h:null,closed4h:null})),null);
get('indicator1').checked=true;get('combineMode').value='any';assert.equal(api.resultStatus(row({ignitionGrade:'none'})),true);
get('combineMode').value='all';assert.equal(api.resultStatus(row({ignitionGrade:'none'})),false);
get('combineMode').value='atleast';get('minimumHits').value='1';assert.equal(api.resultStatus(row({ignitionGrade:'none'})),true);
get('minimumHits').value='2';assert.equal(api.resultStatus(row({ignitionGrade:'none'})),false);
get('minimumHits').value='3';assert.ok(api.validation());
get('indicator4').checked=false;api.syncEmaOptions();assert.equal(get('ignitionWeak').checked,false);assert.equal(get('ignitionWeak').disabled,true);
reset();get('indicator5').checked=true;assert.equal(api.resultStatus(row()),null);
get('indicator1').checked=true;assert.equal(api.resultStatus(row({oiCapPct:1})),false);
reset();get('capMin').value='1000';get('capMax').value='5000';assert.equal(api.scopeStatus(row({capUsd:1e7})),true);assert.equal(api.scopeStatus(row({capUsd:5e7})),false);assert.equal(api.scopeStatus(row({capUsd:null})),null);get('capMin').value='6000';assert.ok(api.validation());
reset();get('sort').value='cap';for(const d of ['asc','desc']){get('sortDirection').value=d;const sorted=[row({capUsd:3}),row({capUsd:null}),row({capUsd:1}),row({capUsd:2})].sort(api.compareRows);assert.deepEqual(sorted.map(r=>r.capUsd),d==='asc'?[1,2,3,null]:[3,2,1,null]);}
const event={event_id:'e1',candidate_cycle_id:'c1',level:'strong'};
const history=row({ignitionGrade:'none',emaSqueezeStage:{candidateStatus:'active',candidateCycleId:'c1',confirmedEvents:[event]}});
reset();get('indicator4').checked=true;assert.equal(api.resultStatus(history),false);get('poolViewMode').value='pool';assert.equal(api.resultStatus(history),true);get('poolViewMode').value='confirm';assert.equal(api.resultStatus(history),true);
// Execute the actual override installed by the professional page.
const pi={},pg=id=>pi[id]??={value:'',checked:false,setAttribute(k,v){this[k]=v;}},pro=vm.createContext({});pro.window=pro;
vm.runInContext(read('design/strategy-registry.js'),pro);vm.runInContext(read('design/market-evidence.js'),pro);
pro.$=s=>s==='#indicators input[value="3"]'?pg('indicator4'):pg(s.slice(1));
pro.$$=()=>[0,1,2,3,4,5].filter(i=>pg('indicator'+(i+1)).checked).map(i=>({value:String(i)}));
const ps=read('design/pro-table-runtime.js'),prototype=read('design/prototype.html');
const threshold=prototype.match(/function threshold\(v,n\)\{.*?\}function hitCount/)[0].replace(/function hitCount$/,'');
const sf=ps.slice(ps.indexOf('  function signalFlags('),ps.indexOf('  function realHitCount('));
vm.runInContext(threshold+'\nfunction flags(r){return MarketEvidence.marketSignals(r,StrategyRegistry.signals(r));}\nfunction combined(){}\n'+sf+'\n'+ps.slice(ps.indexOf('  function poolViewMode()'),ps.indexOf('  // 注入外壳 DOM'))+'\nglobalThis.sync=syncEmaOptions;',pro);
pg('poolViewMode').value='six';pg('combine').value='all';pg('n').value='1';pg('indicator4').checked=true;pg('ignitionWeak').checked=true;
assert.equal(pro.combined(row()),true);assert.equal(pro.combined(row({ignitionGrade:'medium'})),false);
pg('indicator1').checked=true;pg('combine').value='any';assert.equal(pro.combined(row({ignitionGrade:'none'})),true);
pg('combine').value='all';assert.equal(pro.combined(row({ignitionGrade:'none'})),false);
pg('combine').value='atleast';pg('n').value='1';assert.equal(pro.combined(row({ignitionGrade:'none'})),true);pg('n').value='2';assert.equal(pro.combined(row({ignitionGrade:'none'})),false);
pg('indicator4').checked=false;pro.sync();assert.equal(pg('ignitionWeak').checked,false);
pg('poolViewMode').value='pool';assert.equal(pro.combined(history),true);pg('poolViewMode').value='confirm';assert.equal(pro.combined(history),true);
console.log('PASS actual filters on both pages: 4,374 tri-state thresholds; EMA parent/levels; AND/OR/N; pool/history isolation; unknowns; cap boundaries; null-last sorting.');
