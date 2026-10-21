'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const root=process.argv[2]||__dirname,C=require(path.join(root,'design/unified-core.js'));
const BAR=1792483199999,step=14400000;
const kinds=['ema7_cross_25','ema7_cross_99','ema25_cross_99'];
function row(prev,now,volume=2){return {market:'USDⓈ-M',symbol:'TESTUSDT',quote:'USDT',candleAsOf:BAR,closed4h:now[0]+1,ema4h:now,barVolumeRatio7:volume,ignitionGrade:'none',legacySixStates:[true,true,true,true,null,true],emaObservation:{timeframe:'4h',bar_time:BAR,quality:'valid',valid_bars:1499,weak_new_cross:false,long_crosses:Object.fromEntries(kinds.map((k,i)=>{const [a,b]=[[0,1],[0,2],[1,2]][i];return [k,prev[a]<=prev[b]&&now[a]>now[b]];}))}};}
const thresholds={tOi:30,tLsr:.8,tApr:-50,tVol:3,tQuiet:2};
const state=()=>({signals:new Set(['i4']),mode:'and',minimum:1,market:'all',search:'',cap:'all',minCap:'',maxCap:'',trusted:false,listing:'current',history:'all',context:'latest',aStage:'grade_all',bStage:'cur_event',cStage:'confirm',result:'pass',sort:'grade',desc:true});
let checks=0;function test(name,fn){fn();checks++;console.log('PASS '+name);}
for(const [name,prev,now,ratio,grade]of [
 ['7/25 below99',[1,2,3],[2.5,2,3],2,'weak'],
 ['7/25 touches99',[1,2,3],[3,2,3],2,'weak'],
 ['7/25 above99 not stacked',[1,2,3],[4,2,3],2,'medium'],
 ['7/25 creates stack low volume',[3,4,2],[5,4,2],1.99,'medium'],
 ['7/25 creates stack at2',[3,4,2],[5,4,2],2,'strong'],
 ['7/99 ahead of25',[1,4,2],[3,4,2],100,'medium'],
 ['25/99 with7 below25',[1,1.5,2],[1,3,2],100,'medium'],
 ['25/99 stack low volume',[4,1,2],[4,3,2],0,'medium'],
 ['25/99 stack at2',[4,1,2],[4,3,2],2,'strong'],
 ['three crosses same root',[1,2,3],[5,4,3],2,'strong'],
 ['persistent stack with higher volume',[5,4,3],[6,5,4],100,'none'],
 ['downward crosses excluded',[5,4,3],[1,2,3],100,'none'],
 ['previous equality new strict cross',[2,2,3],[2.5,2,3],2,'weak'],
 ['current equality not cross',[1,2,3],[2,2,3],2,'none']])test(name,()=>assert.equal(C.emaSignal(row(prev,now,ratio)).grade,grade));
test('same root coalesces all types and has stable identity',()=>{const raw=row([1,2,3],[5,4,3]),a=C.emaSignal(raw),b=C.emaSignal(JSON.parse(JSON.stringify(raw)));assert.equal(a.kinds.length,3);assert.equal(a.event_id,b.event_id);assert(!a.event_id.includes('undefined'));});
test('missing or invalid volume never becomes strong, zero is valid',()=>{for(const ratio of [null,undefined,NaN,Infinity,-1]){const raw=row([4,1,2],[4,3,2]);raw.barVolumeRatio7=ratio;const s=C.emaSignal(raw);assert.equal(s.grade,'medium');assert.equal(s.possible_strong,true);assert.equal(C.emaGradeMatch(s,'grade_strong'),null);assert.equal(C.emaGradeMatch(s,'grade_medium'),true);}const s=C.emaSignal(row([4,1,2],[4,3,2],0));assert.equal(s.possible_strong,false);assert.equal(C.emaGradeMatch(s,'grade_strong'),false);});
test('24h volume cannot substitute same-bar4h volume',()=>{const raw=row([4,1,2],[4,3,2],null);raw.volumeRatio=100;assert.equal(C.emaSignal(raw).grade,'medium');assert.equal(C.emaSignal(raw).volume_status,'unknown');});
test('price crossing is weak observation and never renamed7/99',()=>{const raw=row([1,2,3],[1,2,3]);raw.emaObservation.weak_new_cross=true;raw.closed4h=4;const s=C.emaSignal(raw);assert.equal(s.grade,'weak');assert.equal(s.reason,'price_breakout');assert.deepEqual(s.kinds,['price_cross_99']);});
test('missing evidence is unknown, original A grade cannot fill it',()=>{for(const mutate of [r=>delete r.emaObservation,r=>r.emaObservation.quality='insufficient_or_gap',r=>r.emaObservation.valid_bars=120,r=>r.emaObservation.bar_time-=step,r=>r.emaObservation.long_crosses.ema7_cross_99=null,r=>r.ema4h=null]){const raw=row([1,2,3],[5,4,3]);raw.ignitionGrade='strong';mutate(raw);assert.equal(C.emaSignal(raw).grade,'unknown');assert.equal(C.emaGradeMatch(C.emaSignal(raw),'grade_all'),null);}});
test('proof conflicting with current EMA is not accepted',()=>{const raw=row([5,4,3],[1,2,3]);raw.emaObservation.long_crosses.ema7_cross_99=true;assert.equal(C.emaSignal(raw).grade,'unknown');});
test('historical grades use historical EMA and volume and merge same bar',()=>{
 const raw=row([1,4,2],[3,4,2],0);raw.emaObservation.recent_long_cross_events=kinds.map(kind=>({kind,bar_time:BAR-step,ema:[5,4,3],previous_ema:[1,2,3],volume_ratio:2}));
 const list=C.emaHistory(raw);assert.equal(list.length,1);assert.equal(list[0].grade,'strong');assert.equal(list[0].kinds.length,3);assert.equal(list[0].historical,true);assert.equal(C.emaSignal(raw).grade,'medium');
 raw.emaObservation.recent_long_cross_events[1].volume_ratio=1;assert.equal(C.emaHistory(raw)[0].grade,'unknown');
});
test('same new grades drive filter counts and sorting while old A stays intact',()=>{
 const raws=[row([1,2,3],[2.5,2,3]),row([1,4,2],[3,4,2]),row([4,1,2],[4,3,2])];raws.forEach((r,i)=>r.symbol='T'+i);const rows=raws.map(r=>C.adapt(r)),s=state();
 assert.equal(rows.filter(r=>C.evaluate(r,s,thresholds)===true).length,3);
 for(const grade of ['weak','medium','strong']){s.aStage='grade_'+grade;assert.equal(rows.filter(r=>C.evaluate(r,s,thresholds)===true).length,1);}
 s.aStage='grade_all';assert.deepEqual(C.sort(rows,s,thresholds).map(r=>r.signalGrade),['strong','medium','weak']);assert(rows.every(r=>r.grade==='none'));
 const ev=C.evidence(rows[2],s,thresholds,{generation:'g',snapshot:{}});assert.equal(ev.EMA_technical_signal.grade,'strong');assert.equal(ev.A_current_grade,'none');assert.equal(ev.query.ema_grade_version,C.EMA_GRADE_VERSION);const csv=C.csv([ev]);assert(csv.includes('EMA_technical_signal'));assert(csv.includes('ema-technical-grade-v1'));
});
test('real runtime offers all six grade choices',()=>{
 const source=fs.readFileSync(path.join(root,'design/unified-workbench.js'),'utf8');const options=vm.runInNewContext('('+source.match(/const aOptions=(\{[^\n]+\});/)[1]+')');assert.deepEqual(Object.keys(options),['grade_all','grade_watch','grade_weak','grade_medium','grade_strong','grade_mediumstrong']);assert(!source.includes("controls=group('aStage',[...basicAGrades"));
});
test('list renderer shows grade types actual4h window and volume waiting',()=>{
 const ctx={UnifiedCore:C,window:{}};vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(root,'design/market-evidence.js'),'utf8')+'\nglobalThis.E=MarketEvidence;',ctx);
 const raw=row([1,4,2],[3,4,2]);const html=ctx.E.emaSummaryHtml(raw);assert.match(html,/中信号/);assert.match(html,/EMA7上穿EMA99/);assert.match(html,/4H｜/);assert.match(html,/已收盘/);assert.match(html,/10\/20/);assert.equal(ctx.E.marketSignals(raw,raw.legacySixStates)[3],true);
 const wait=ctx.E.emaSummaryHtml(row([4,1,2],[4,3,2],null));assert.match(wait,/中信号 · 量能待核/);assert(!wait.includes('强信号'));
 const weak=row([1,2,3],[1,2,3]);weak.emaObservation.weak_new_cross=true;weak.closed4h=4;assert.match(ctx.E.emaSummaryHtml(weak),/弱观察/);assert.match(ctx.E.emaSummaryHtml(weak),/非均线交叉/);
});
test('weak observation and weak signal are separate, medium+strong is their exact union',()=>{
 const watch=row([1,2,3],[1,2,3]);watch.emaObservation.weak_new_cross=true;watch.closed4h=4;
 const weak=row([1,2,3],[2.5,2,3]),medium=row([1,4,2],[3,4,2]),strong=row([4,1,2],[4,3,2]);
 const signals=[watch,weak,medium,strong].map(C.emaSignal);
 assert.deepEqual(signals.map(x=>C.emaGradeMatch(x,'grade_watch')),[true,false,false,false]);
 assert.deepEqual(signals.map(x=>C.emaGradeMatch(x,'grade_weak')),[false,true,false,false]);
 assert.deepEqual(signals.map(x=>C.emaGradeMatch(x,'grade_mediumstrong')),[false,false,true,true]);
 assert(signals.every(x=>C.emaGradeMatch(x,'grade_all')===true));
 const wait=C.emaSignal(row([4,1,2],[4,3,2],null));assert.equal(C.emaGradeMatch(wait,'grade_mediumstrong'),true);assert.equal(C.emaGradeMatch(wait,'grade_strong'),null);
 for(const stage of ['grade_watch','grade_weak','grade_mediumstrong']){const s=state();s.aStage=stage;assert.equal(C.validate(s,thresholds),'');assert.equal(C.emaGradeMatch({grade:'unknown'},stage),null);}
 const source=fs.readFileSync(path.join(root,'design/unified-workbench.js'),'utf8');
 const ctx={state:state(),payload:{},core:C,thresholds,esc:String};vm.createContext(ctx);
 vm.runInContext(source.slice(source.indexOf('const aOptions='),source.indexOf('const bOptions='))+'\nglobalThis.aOptionsForTest=aOptions;',ctx);
 vm.runInContext('const basicAGrades=Object.entries(aOptions);'+source.slice(source.indexOf('function aGradeOptions('),source.indexOf('function signalCount(')),ctx);
 const options=ctx.aGradeOptions([watch,weak,medium,strong].map(x=>C.adapt(x)));
 assert.equal((options.match(/<option /g)||[]).length,6);assert.match(options,/弱观察（1）/);assert.match(options,/中＋强（2）/);assert.match(options,/>弱（1）/);
});
console.log(JSON.stringify({passed:checks,kind:'versioned grading and synthetic interaction; no real browser'}));
