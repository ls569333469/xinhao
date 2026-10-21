/* One query contract for UI, detail, data report and CSV. No market collection. */
(function(root){'use strict';
const VERSION='unified-signals-v33r5';
const finite=v=>typeof v==='number'&&Number.isFinite(v);
const and=v=>v.includes(false)?false:v.includes(null)?null:true;
const or=v=>v.includes(true)?true:v.includes(null)?null:false;
const identity=r=>String(r.market)+'|'+String(r.symbol);
const market=r=>r.market==='COIN-M'?'coin':r.quote==='USDC'?'usdc':'usdt';
const bstatus=e=>!e?'unmonitored':e.presentation?.version==='b-state-v1'?e.presentation.code:e.migration_status==='legacy_unverified'||(e.schema_version||0)<2?'legacy':e.technical_event==='fail'?'corrected':e.structure_gate==='fail'?'fail':e.technical_event!=='pass'?'unknown':e.technical_confirmation==='pending'?'pending':e.technical_confirmation==='fail'?'confirmation_failed':e.technical_confirmation!=='pass'?'unknown':e.structure_gate==='pass'?'complete':e.structure_gate==='fail'?'fail':'unknown';
function cycleEvents(raw){const st=raw.emaSqueezeStage||{},seen=new Set();return [...(st.confirmedEvents||[]),st.crossConfirm,st.latestConfirm].filter(e=>e&&e.event_id&&!e.void&&!e.blocked&&!e.duplicate&&(!st.candidateCycleId||e.candidate_cycle_id===st.candidateCycleId)&&!seen.has(e.event_id)&&seen.add(e.event_id));}

// Presentation only: latest inspected hour plus its immediately preceding confirmation.
function bRound(raw,target){
 const events=raw.bEvents||[],o=raw.bMonitoringEvidence;
 const targetOk=finite(target)&&target>0;
 const lastConfirmed=events.filter(e=>bstatus(e)==='complete'&&(!targetOk||e.trigger_open_time+7200000<=target)).sort((a,b)=>b.trigger_open_time-a.trigger_open_time)[0]||null;
 const result=(state,event=null,triggered=false)=>({state,event,triggered,target:targetOk?target:null,lastConfirmed,currentEvent:targetOk?events.find(e=>e.trigger_open_time===target-3600000)||null:null,confirmationEvent:targetOk?events.find(e=>e.trigger_open_time===target-7200000)||null:null});
 if(!targetOk)return result('unmonitored');
 const exact=o&&o.target_close_boundary===target&&o.open_time===target-3600000;
 const current=events.find(e=>e.trigger_open_time===target-3600000)||null;
 const prior=events.find(e=>e.trigger_open_time===target-7200000)||null;
 // A later scan's confirmation must never leak back into an earlier target.
 const atTarget=e=>e&&finite(e.target_close_boundary)&&e.target_close_boundary===target;
 const priorValid=atTarget(prior)&&prior.technical_event==='pass';
 const triggered=!!(exact&&o.technical==='pass');
 if(priorValid&&prior.technical_confirmation==='pass'&&prior.structure_gate==='pass')return result('complete',prior,triggered);
 if(triggered){
  if(!current||!atTarget(current)||current.technical_event!=='pass')return result('insufficient',current,true);
  if(o.structure==='fail'||current.structure_h?.state==='fail')return result('fail',current,true);
  if(o.structure!=='pass'&&current.structure_h?.state!=='pass')return result('unknown',current,true);
  return result('pending',current,true);
 }
 if(priorValid&&prior.technical_confirmation==='fail')return result('confirmation_failed',prior);
 if(priorValid&&prior.structure_gate==='fail')return result('fail',prior);
 if(priorValid&&prior.technical_confirmation!=='pending'&&(prior.technical_confirmation!=='pass'||prior.structure_gate!=='pass'))return result('unknown',prior);
 if(!exact)return result('unmonitored');
 return result(o.technical==='fail'?'none':'insufficient');
}

// One read-only technical grade for the market list, filters, details and exports.
const EMA_GRADE_VERSION='ema-technical-grade-v1',EMA_BAR_MS=14400000;
const emaKinds=['ema7_cross_25','ema7_cross_99','ema25_cross_99'];
const emaNames={ema7_cross_25:'EMA7上穿EMA25',ema7_cross_99:'EMA7上穿EMA99',ema25_cross_99:'EMA25上穿EMA99',price_cross_99:'价格上穿EMA99（非均线交叉）'};
const emaTime=v=>finite(v)?v:typeof v==='string'&&/[TZ]|[+-]\d\d:\d\d$/.test(v)?Date.parse(v):NaN;
function emaWindow(bar){
 if(!finite(bar))return '4H｜K线时间待核';
 const fmt=v=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(v).replace(' ',' ');
 return '4H｜'+fmt(bar+1-EMA_BAR_MS)+'—'+fmt(bar+1)+'｜已收盘';
}
function emaSignal(raw){
 const o=raw?.emaObservation,bar=emaTime(o?.bar_time),saved=emaTime(raw?.candleAsOf),e=raw?.ema4h;
 const trustedTime=finite(bar)&&bar===saved&&(bar+1)%EMA_BAR_MS===0;
 const result={version:EMA_GRADE_VERSION,grade:'unknown',kinds:[],bar_time:trustedTime?bar:null,open_time:trustedTime?bar+1-EMA_BAR_MS:null,close_boundary:trustedTime?bar+1:null,timeframe:'4h',volume_ratio:null,volume_status:'unknown',possible_strong:false,reason:'evidence_missing',long_stack:null,event_id:null,original_a_grade:raw?.ignitionGrade??null};
 const done=(grade,reason,kinds=[])=>{
  result.grade=grade;result.reason=reason;result.kinds=kinds;
  if(kinds.length)result.event_id=[raw.market,raw.symbol,'4h',EMA_GRADE_VERSION,bar].join('|');
  return result;
 };
 // Do not promote an old A grade, real-time price, a wrong bar, or incomplete warmup.
 if(!o||o.quality!=='valid'||o.timeframe!=='4h'||!finite(o.valid_bars)||o.valid_bars<121||!finite(bar)||bar!==saved||(bar+1)%EMA_BAR_MS!==0||!Array.isArray(e)||e.length!==3||!e.every(v=>finite(v)&&v>0))return result;
 const c=o.long_crosses;if(!c||emaKinds.some(k=>typeof c[k]!=='boolean'))return done('unknown','cross_evidence_missing');
 const kinds=emaKinds.filter(k=>c[k]);
 // Recorded crossings must agree with the current same-bar EMA relationship.
 if(kinds.some(k=>{const [a,b]=({ema7_cross_25:[0,1],ema7_cross_99:[0,2],ema25_cross_99:[1,2]})[k];return e[a]<=e[b];}))return done('unknown','cross_structure_conflict');
 result.long_stack=e[0]>e[1]&&e[1]>e[2];
 const ratio=raw.barVolumeRatio7;result.volume_ratio=finite(ratio)&&ratio>=0?ratio:null;
 result.volume_status=result.volume_ratio===null?'unknown':ratio>=2?'pass':'fail';
 if(kinds.length){
  if(result.long_stack){
   result.possible_strong=result.volume_status==='unknown';
   return done(result.volume_status==='pass'?'strong':'medium',result.volume_status==='pass'?'stack_volume_pass':result.volume_status==='unknown'?'stack_volume_unknown':'stack_volume_low',kinds);
  }
  return done(c.ema7_cross_99||c.ema25_cross_99||e[0]>e[2]?'medium':'weak',c.ema7_cross_99||c.ema25_cross_99||e[0]>e[2]?'cross_above_99':'early_7_25',kinds);
 }
 if(o.weak_new_cross===true){
  if(!finite(raw.closed4h)||raw.closed4h<=e[2])return done('unknown','price_cross_evidence_conflict');
  return done('weak','price_breakout',['price_cross_99']);
 }
 if(o.weak_new_cross!==false)return done('unknown','price_cross_evidence_missing');
 return done('none',result.long_stack?'stack_continues':'no_new_cross');
}
function emaGradeMatch(signal,stage){
 if(signal.grade==='unknown')return null;
 if(stage==='grade_all')return ['weak','medium','strong'].includes(signal.grade);
 if(stage==='grade_watch')return signal.reason==='price_breakout';
 if(stage==='grade_weak')return signal.grade==='weak'&&signal.reason!=='price_breakout';
 if(stage==='grade_mediumstrong')return ['medium','strong'].includes(signal.grade);
 if(stage==='grade_strong'&&signal.possible_strong)return null;
 return signal.grade===stage.slice(6);
}
function emaHistory(raw){
 const o=raw?.emaObservation,groups=new Map();
 for(const x of o?.recent_long_cross_events||[]){
  if(!emaKinds.includes(x.kind)||!finite(x.bar_time)||x.bar_time>emaTime(o.bar_time)||!Array.isArray(x.ema)||!Array.isArray(x.previous_ema)||x.ema.length!==3||x.previous_ema.length!==3||![...x.ema,...x.previous_ema].every(v=>finite(v)&&v>0))continue;
  const old=groups.get(x.bar_time);
  if(old){if(JSON.stringify([old.ema,old.previous_ema,old.volume_ratio])!==JSON.stringify([x.ema,x.previous_ema,x.volume_ratio]))old.conflict=true;continue;}
  groups.set(x.bar_time,{...x});
 }
 return [...groups.values()].sort((a,b)=>b.bar_time-a.bar_time).map(x=>{
  const crosses=Object.fromEntries(emaKinds.map((k,i)=>{const [a,b]=[[0,1],[0,2],[1,2]][i];return [k,x.previous_ema[a]<=x.previous_ema[b]&&x.ema[a]>x.ema[b]];}));
  const pseudo={market:raw.market,symbol:raw.symbol,candleAsOf:x.bar_time,ema4h:x.ema,barVolumeRatio7:x.volume_ratio,emaObservation:{timeframe:'4h',quality:x.conflict?'conflict':'valid',valid_bars:121,bar_time:x.bar_time,long_crosses:crosses,weak_new_cross:false}};
  const signal=emaSignal(pseudo);return {...signal,historical:x.bar_time!==emaTime(o.bar_time)};
 });
}

function adapt(raw,history='all',target=null,range={}){const all=raw.bEvents||[],es=all.filter(e=>(history==='all'||(history==='history'?e.historical===true:e.historical===false))&&(!range.from||e.trigger_open_time>=Date.parse(range.from+'T00:00:00+08:00'))&&(!range.to||e.trigger_open_time<Date.parse(range.to+'T00:00:00+08:00')+86400000)),e=es[0]||null,st=raw.emaSqueezeStage;return {key:identity(raw),symbol:raw.symbol,asset:raw.asset||'',market:market(raw),listing:raw.unifiedListing||'current',price:raw.price??null,cap:raw.capUsd??null,change:raw.priceChange24Pct??null,trusted:raw.capTrusted===true,grade:raw.ignitionGrade||'unknown',emaSignal:emaSignal(raw),signalGrade:emaSignal(raw).grade,a:st?.candidateStatus==='active'?(cycleEvents(raw).length?'confirmed':'active'):st?.candidateStatus||'unknown',b:e?bstatus(e):all.length&&(history!=='all'||range.from||range.to)?'outscope':raw.bMonitoring||'unmonitored',btime:e?.trigger_open_time??null,historical:e?.historical??null,prior:es.slice(1).some(e=>bstatus(e)==='complete'),c:(raw.trackingSummary||[]).find(x=>x.strategy==='C')?.technical_status||'unknown',aux:[],e:raw.legacySixStates||Array(6).fill(null),metrics:{oi:raw.oiCapPct??null,lsr:raw.accountRatio??null,apr:raw.fundingAprPct??null,vol:raw.volumeRatio??null,quiet:finite(raw.priceChangeWindowPct)?Math.abs(raw.priceChangeWindowPct):null},raw,event:e,roundB:bRound(raw,target)};}
function validate(s,t){const n=s.signals.size;
 if(!['all','usdt','usdc','coin'].includes(s.market)||!['all','micro','small','mid','large','custom'].includes(s.cap)||!['all','forward','history'].includes(s.history)||!['latest','snapshot'].includes(s.context)||!['current','all'].includes(s.listing)||!['pass','unknown','fail','selected'].includes(s.result))return '范围条件版本无效，请重新选择';
 if(!['grade_all','grade_watch','grade_weak','grade_medium','grade_strong','grade_mediumstrong','all_signals','cross_any','cross_7_25','cross_7_99','cross_25_99','long_stack','strongmedium','strong','medium','weak','active','confirm','any','mediumweak','strongweak','allgrades'].includes(s.aStage)||!['complete','pending','unknown','event','fail','prior','unmonitored','cur_event','cur_trigger','cur_complete','cur_pending','cur_unknown','cur_fail','cur_unmonitored','cur_none'].includes(s.bStage)||!['confirm','waiting','candidate'].includes(s.cStage))return '阶段条件版本无效，请重新选择';
 if((s.bFrom&&!/^\d{4}-\d{2}-\d{2}$/.test(s.bFrom))||(s.bTo&&!/^\d{4}-\d{2}-\d{2}$/.test(s.bTo))||(s.bFrom&&s.bTo&&s.bFrom>s.bTo))return '历史信号起止日期无效';
 if(s.reserveMode!==undefined&&!['covered','day','week','streak'].includes(s.reserveMode)||s.reserveThreshold!==undefined&&(!finite(s.reserveThreshold)||s.reserveThreshold<0||s.reserveThreshold>100))return '储备条件无效';
 if(!['and','or','n'].includes(s.mode))return '组合方式无效';
 if(s.mode==='n'&&n&&(!Number.isInteger(s.minimum)||s.minimum<1||s.minimum>n))return 'N须为1至已选信号数之间的整数';
 for(const k of ['minCap','maxCap'])if(s[k]!==''&&(!Number.isFinite(Number(s[k]))||Number(s[k])<0))return '请输入有效非负市值（万美元）';
 if(s.cap==='custom'&&s.minCap!==''&&s.maxCap!==''&&Number(s.minCap)>=Number(s.maxCap))return '市值下限须小于上限';
 if(Object.values(t).some(v=>!finite(v))||['tOi','tLsr','tVol','tQuiet'].some(k=>t[k]<0))return '信号阈值无效';return '';
}
function combine(v,mode,n){if(!v.length)return true;if(mode==='and')return and(v);if(mode==='or')return or(v);const p=v.filter(x=>x===true).length,u=v.filter(x=>x===null).length;return p>=n?true:p+u<n?false:null;}
function scope(r,s){const v=[s.listing==='all'||r.listing==='current'?true:r.listing==='unknown'?null:false,!s.search||[r.symbol,r.asset].some(x=>x.toUpperCase().includes(s.search.toUpperCase())),s.market==='all'||r.market===s.market,!s.trusted||r.trusted];
 if(s.cap!=='all'){if(!finite(r.cap))v.push(null);else{const ranges={micro:[0,1e7],small:[1e7,5e7],mid:[5e7,1e8],large:[1e8,Infinity]};const a=s.cap==='custom'?[s.minCap===''?0:Number(s.minCap)*1e4,s.maxCap===''?Infinity:Number(s.maxCap)*1e4]:ranges[s.cap];v.push(a?r.cap>=a[0]&&r.cap<a[1]:false);}}return and(v);}
function signals(r,s,t){const raw=r.raw,m=r.metrics;
 // Unknown quality is not promoted by a threshold edit. Known false can be re-evaluated.
 const safe=(i,v)=>r.e[i]===null?null:v;
 let a=null;
 const crosses=raw.emaObservation?.long_crosses;
 if(s.aStage.startsWith('grade_'))a=emaGradeMatch(r.emaSignal||emaSignal(raw),s.aStage);
 else if(s.aStage==='all_signals')a=or([crosses?or(Object.values(crosses)):null,r.grade==='unknown'?null:['strong','medium','weak'].includes(r.grade)]);
 else if(s.aStage==='cross_any')a=crosses?or(Object.values(crosses)):null;
 else if(s.aStage==='long_stack')a=raw.emaObservation?.long_stack??null;
 else if(s.aStage.startsWith('cross_'))a=crosses?.[{'cross_7_25':'ema7_cross_25','cross_7_99':'ema7_cross_99','cross_25_99':'ema25_cross_99'}[s.aStage]]??null;
 else if(s.aStage==='active')a=r.a==='unknown'?null:['active','confirmed'].includes(r.a);
 else if(s.aStage==='confirm')a=r.a==='unknown'?null:r.a==='confirmed';
 else if(s.aStage==='any')a=['active','confirmed'].includes(r.a)||['strong','medium','weak'].includes(r.grade)?true:r.grade==='unknown'||r.a==='unknown'?null:false;
 else if(['mediumweak','strongweak','allgrades'].includes(s.aStage))a=r.grade==='unknown'?null:({mediumweak:['medium','weak'],strongweak:['strong','weak'],allgrades:['strong','medium','weak']}[s.aStage]).includes(r.grade);
 else if(r.grade!=='unknown')a=s.aStage==='strongmedium'?['strong','medium'].includes(r.grade):r.grade===s.aStage;
 let b=null;const e=r.event,bs=r.b;
 const inScope=e&&(s.history==='all'||(s.history==='history'?e.historical===true:e.historical===false));
 if(s.bStage.startsWith('cur_')){
  const round=r.roundB||bRound(raw,null),code=round.state,choice=s.bStage.slice(4);
  if(choice==='trigger')b=code==='unmonitored'||code==='insufficient'?null:round.triggered;
  else if(choice==='unknown')b=['unknown','insufficient'].includes(code);
  else if(choice==='unmonitored')b=code==='unmonitored';
  else if(code==='unmonitored'||code==='insufficient'||code==='unknown')b=null;
  else if(choice==='event')b=['complete','pending','fail','confirmation_failed'].includes(code);
  else if(choice==='fail')b=['fail','confirmation_failed'].includes(code);
  else b=code===choice;
 }
 else if(e&&!inScope)b=false;
 else if(s.bStage==='unmonitored')b=['unmonitored','insufficient'].includes(bs);
 else if(s.bStage==='unknown')b=['unknown','legacy','insufficient'].includes(bs);
 else if(bs==='legacy'||bs==='unmonitored'||bs==='insufficient')b=null;
 else if(s.bStage==='prior')b=r.prior||bs==='complete';
 else if(s.bStage==='event')b=!!e;
 else if(s.bStage==='fail')b=['fail','confirmation_failed','corrected'].includes(bs);
 else b=bs===s.bStage;
 const reserve=raw.reserveEvidence;let reserveOk=null;if(reserve?.freshNow===true){const mode=s.reserveMode||'covered',threshold=s.reserveThreshold??1,v=mode==='day'?reserve.dayChangePct:mode==='week'?reserve.weekChangePct:reserve.consecutiveDecreaseDays;reserveOk=mode==='covered'?true:finite(v)?mode==='streak'?v>=3:v<0&&v<=-threshold:null;}
 let c=null;const check=raw.cCheck;const ch=(raw.trackingSummary||[]).find(x=>x.strategy==='C');
 if(raw.trackingEnabled===true&&ch&&!ch.historical_import&&ch.decision_close_ms===raw.candleAsOf+1){
   const status=ch.original_confirmation?.technical_status;
   c=ch.qualification?.status==='unknown'?null:ch.qualification?.status==='fail'?false:s.cStage==='confirm'?status==='confirmed':s.cStage==='waiting'?status==='waiting':['waiting','confirmed'].includes(status);
 }
 // A known negative C0 is not a missing event. Never infer a positive cycle.
 if(!ch&&raw.trackingEnabled===true&&check?.has_cycle===false&&check.target_close_ms===raw.candleAsOf+1&&check.checks?.c0==='fail')c=false;
 return {i1:finite(m.oi)?safe(0,m.oi>t.tOi):null,i2:finite(m.lsr)?safe(1,m.lsr<t.tLsr):null,i3:finite(m.apr)&&finite(raw.fundingHours)&&!['estimated','unknown',undefined].includes(raw.fundingIntervalStatus)?safe(2,m.apr<t.tApr):null,i4:a,i5:r.e[4]??null,i6:finite(m.vol)&&finite(m.quiet)?safe(5,m.vol>t.tVol&&m.quiet<t.tQuiet):null,b,c,vol:null,spot:null,oi:null,warning:null,reserve:reserveOk};
}
function evaluate(r,s,t){const f=signals(r,s,t);return and([scope(r,s),combine([...s.signals].map(id=>f[id]??null),s.mode,s.minimum)]);}
function sort(rows,s,t){const value=r=>['ema7','ema25','ema99'].includes(s.sort)?r.raw.ema4h?.[{ema7:0,ema25:1,ema99:2}[s.sort]]:s.sort==='grade'?({strong:3,medium:2,weak:1,none:0}[r.signalGrade]??null):s.sort==='reserveDayPct'?r.raw.reserveEvidence?.dayChangePct:s.sort==='reserveWeekPct'?r.raw.reserveEvidence?.weekChangePct:s.sort==='reserveStreakDays'?r.raw.reserveEvidence?.consecutiveDecreaseDays:s.sort==='hits'?[...s.signals].filter(id=>signals(r,s,t)[id]===true).length:s.sort==='btime'?r.btime:r[s.sort]??r.raw[s.sort];return rows.slice().sort((a,b)=>{const av=value(a),bv=value(b),miss=v=>v==null||typeof v==='number'&&!finite(v);if(miss(av)&&miss(bv))return a.key.localeCompare(b.key);if(miss(av))return 1;if(miss(bv))return -1;const c=typeof av==='string'?av.localeCompare(bv):av-bv;return c*(s.desc?-1:1)||a.key.localeCompare(b.key);});}
function snapshot(s,t){const out={...s,signals:[...s.signals],selected:undefined,thresholds:{...t},schema:VERSION,ema_grade_version:EMA_GRADE_VERSION};return out;}
function evidence(r,s,t,p){const f=signals(r,s,t);const {unifiedEvidence,...raw}=r.raw;raw.B_round=r.roundB||null;return {schema:VERSION,generation:p.generation,snapshot:p.snapshot,query:snapshot(s,t),market:r.raw.market,symbol:r.symbol,selected_signal_hits:[...s.signals].filter(id=>f[id]===true).length,selected_signal_count:s.signals.size,selected_states:Object.fromEntries([...s.signals].map(id=>[id,f[id]])),query_state:evaluate(r,s,t),legacy_six_states:r.e,legacy_registry_hash:r.raw.registryHash||p.snapshot.registryHash,EMA_technical_signal:r.emaSignal||emaSignal(r.raw),A_current_grade:r.grade,A_state:r.raw.emaSqueezeStage||null,B_state:r.b,B_event_id:r.event?.event_id??null,B_revision:r.event?.event_revision??null,B_event:r.event,B_round:r.roundB||null,C_state:(r.raw.trackingSummary||[]).find(x=>x.strategy==='C')||{status:r.raw.trackingEnabled?'no_verified_event':'connected_disabled',check:r.raw.cCheck||null},auxiliary_state:'not_connected',raw};}
const triText=v=>v===true?'满足':v===false?'不满足':'未知';
function legacyTrend(r){const e=r.ema4h;return Array.isArray(e)&&e.length===3&&e.every(finite)&&finite(r.closed4h)?e[0]>e[1]&&e[1]>e[2]&&r.closed4h>e[0]:null;}
function legacyTrendVolume(r){const t=legacyTrend(r);return t===false||finite(r.barVolumeRatio)&&r.barVolumeRatio<=1.5||finite(r.priceChangeWindowPct)&&r.priceChangeWindowPct<=0?false:t===true&&finite(r.barVolumeRatio)&&finite(r.priceChangeWindowPct)?true:null;}
function reserveStatus(r){return !r?'未核验覆盖':r.freshNow===true?'36h内':'时点／新鲜度未核验';}
function legacyCsv(rows,flags,context){const keys=['candleHistoryStatus','emaWarmupRemaining','emaWarmupExpectedAt','contractOnboardAt','smartMoneyNetInflowUsd','smartMoneyTraders','smartMoneyStatus','smartMoneyStatusNote','smartMoneyEvidence','symbol','asset','market','quote','price','priceChange24Pct','markPrice','indexPrice','high24','low24','capUsd','capId','capName','capTrusted','capStatus','capSource','capPriceGapPct','oiUsd','oiCapPct','binanceAssetOiUsd','binanceAssetOiCapPct','accountRatio','topAccountRatio','topPositionRatio','fundingPct','fundingHours','fundingDailyPct','fundingAprPct','closed4h','ema7','ema25','ema99','crossEma99','barVolumeRatio','volumeRatio','priceChangeWindowPct','volume24Usd','volumeWindowUsd','volumeBaselineUsd','hitCount','knownCount','screeningSchema','poolEntryBelowEma','indicator1','indicator2','indicator3','indicator4','indicator5','indicator6','trendCandidate','trendVolumeCandidate','asOf','candleAsOf','oiAsOf','accountAsOf','capAsOf','riskFlags','errors','reserveDayPct','reserveWeekPct','reserveStreak','reserveAsOf','reserveStatus','reserveExactNetflow','historicalNetTokens','historicalFlowFrom','historicalFlowTo','exportContext','scan_id','revision','available_at','fieldAvailableAt','registryHash','registryVersion','fundingIntervalStatus','assetOiCoverage','binanceAssetOiPartialUsd','fullCross','mediumCrossEvent','barVolumeRatio7','newLowCount10','amplitudeRatio20','amplitudeOk','ignitionGrade','emaSqueezeStage','candidateEnteredAt','candidateExpiresAt','entryOiUsd','pendingCross','crossConfirm','exitReason','exitTime','rules_version','emaObservation','confirmedEvents','pendingCrossEvidence','csvSchema'];const q=v=>'"'+String(v??'').replaceAll('"','""')+'"';return '\uFEFF'+keys.join(',')+'\n'+rows.map(r=>{const f=flags(r),ctx=context(r),p=ctx.reserve,h=ctx.history;const x={...r,screeningSchema:'market-ema-grade-v1',poolEntryBelowEma:r.belowAllEma,ema7:r.ema4h?.[0],ema25:r.ema4h?.[1],ema99:r.ema4h?.[2],hitCount:f.filter(v=>v===true).length,knownCount:f.filter(v=>v!==null).length,trendCandidate:triText(legacyTrend(r)),trendVolumeCandidate:triText(legacyTrendVolume(r)),riskFlags:JSON.stringify(r.riskFlags||[]),errors:JSON.stringify(r.errors||{}),reserveDayPct:p?.dayChangePct,reserveWeekPct:p?.weekChangePct,reserveStreak:p?.consecutiveDecreaseDays,reserveAsOf:p?.asOf,reserveStatus:reserveStatus(p),reserveExactNetflow:false,historicalNetTokens:h?.days?.reduce((s,d)=>s+d.netInflowTokens,0),historicalFlowFrom:h?.days?.[0]?.from,historicalFlowTo:h?.days?.at(-1)?.to,exportContext:ctx.exportContext,registryHash:r.registryHash,registryVersion:r.registryVersion||'',fieldAvailableAt:JSON.stringify(r.fieldAvailableAt??null),assetOiCoverage:JSON.stringify(r.assetOiCoverage??null)};f.forEach((v,i)=>x['indicator'+(i+1)]=triText(v));x.smartMoneyEvidence=JSON.stringify(r.smartMoneyEvidence??null);const st=r.emaSqueezeStage||{};x.csvSchema='market-evidence-csv-v5-smart-money';x.emaObservation=JSON.stringify(r.emaObservation??null);x.confirmedEvents=JSON.stringify(cycleEvents(r));x.pendingCrossEvidence=JSON.stringify(st.pendingCross??null);x.fullCross=r.fullCross??(st.fullCross??'');x.mediumCrossEvent=typeof (r.mediumCrossEvent??st.mediumCrossEvent)==='object'?JSON.stringify(r.mediumCrossEvent??st.mediumCrossEvent):(r.mediumCrossEvent??st.mediumCrossEvent??'');x.barVolumeRatio7=r.barVolumeRatio7;x.newLowCount10=r.newLowCount10;x.amplitudeRatio20=r.amplitudeRatio20;x.amplitudeOk=r.amplitudeOk;x.ignitionGrade=r.ignitionGrade;x.emaSqueezeStage=st&&typeof st==='object'?JSON.stringify(st):'';x.candidateEnteredAt=st.candidateEnteredAt||'';x.candidateExpiresAt=st.candidateExpiresAt||'';x.entryOiUsd=st.entryOiUsd;x.pendingCross=st.pendingCross==null?'':!!st.pendingCross;x.crossConfirm=st.crossConfirm?JSON.stringify(st.crossConfirm):'';x.exitReason=st.exitReason||st.exit_reason||r.exitReason||'';x.exitTime=st.exitTime||st.exitedAt||r.exitTime||'';x.rules_version=(st.crossConfirm&&st.crossConfirm.rules_version)||r.rules_version||(r.registryVersion||'');return keys.map(k=>q(x[k])).join(',');}).join('\n');}
function csv(data){const cols=['generation','selected_signal_hits','selected_signal_count','query_state','selected_states','legacy_six_states','legacy_registry_hash','EMA_technical_signal','A_current_grade','A_state','B_state','B_event_id','B_revision','B_event','C_state','auxiliary_state','snapshot','query','raw'];const q=v=>'"'+(v==null?'':typeof v==='object'?JSON.stringify(v):String(v)).replace(/"/g,'""')+'"';let header=null;const lines=data.map(d=>{const raw=d.raw,f=[...(raw.legacySixStates||d.legacy_six_states||Array(6).fill(null))];f[3]=emaGradeMatch(emaSignal(raw),'grade_all');const text=legacyCsv([raw],()=>f,()=>({reserve:raw.reserveEvidence||null,history:raw.historicalFlowEvidence||null,exportContext:'unified-generation:'+d.generation}));const split=text.indexOf('\n');header=text.slice(0,split).replace(/^\uFEFF/,'')+','+cols.join(',');return text.slice(split+1)+','+cols.map(k=>q(d[k])).join(',');});if(!header){const empty=legacyCsv([],()=>[],()=>({}));header=empty.slice(0,empty.indexOf('\n')).replace(/^\uFEFF/,'')+','+cols.join(',');}return header+'\r\n'+lines.join('\r\n');}

root.UnifiedCore=Object.freeze({VERSION,EMA_GRADE_VERSION,emaSignal,emaGradeMatch,emaHistory,emaWindow,emaNames,finite,and,or,identity,adapt,bstatus,bRound,validate,combine,scope,signals,evaluate,sort,snapshot,evidence,csv});if(typeof module!=='undefined')module.exports=root.UnifiedCore;
})(typeof window!=='undefined'?window:globalThis);
