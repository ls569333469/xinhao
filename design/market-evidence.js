/* Shared, read-only presentation of saved evidence. No data collection or AI calls. */
'use strict';
const MarketEvidence=(()=>{
 const ok=v=>typeof v==='number'&&Number.isFinite(v);
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const number=(v,n=2)=>ok(v)?v.toLocaleString('en-US',{maximumFractionDigits:n}):'未知';
 const unit=(v,u,n=2)=>ok(v)?number(v,n)+u:'未知';
 const money=v=>ok(v)?'$'+(Math.abs(v)>=1e8?number(v/1e8,2)+'亿':Math.abs(v)>=1e4?number(v/1e4,2)+'万':number(v,2)):'未知';
 const quotePrice=(v,q)=>ok(v)?(['USD','USDT','USDC'].includes(q||'USDT')?'$'+number(v,6):number(v,8)+' '+esc(q)):'未知';
 const date=v=>{if(v==null)return'未取得';const d=new Date(v);return Number.isFinite(d.valueOf())?d.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})+' 北京时间':'未取得';};
 const state=v=>v===true?'满足':v===false?'不满足':'未知';
 const badge=(v,text=state(v))=>'<span class="evidence-state '+(v===true?'yes':v===false?'no':'unknown')+'">'+esc(text)+'</span>';
 const sub=text=>'<small>'+esc(text)+'</small>';
 const circles=['①','②','③','④','⑤','⑥'];
 const mark=(flags,i)=>badge(flags[i],circles[i]+' '+state(flags[i]));
 function trend(r){const e=r.ema4h;return Array.isArray(e)&&e.length===3&&e.every(ok)&&ok(r.closed4h)?e[0]>e[1]&&e[1]>e[2]&&r.closed4h>e[0]:null;}
 function trendVolume(r){const t=trend(r);return t===false||ok(r.barVolumeRatio)&&r.barVolumeRatio<=1.5||ok(r.priceChangeWindowPct)&&r.priceChangeWindowPct<=0?false:t===true&&ok(r.barVolumeRatio)&&ok(r.priceChangeWindowPct)?true:null;}
 function reserve(r){if(!r)return'未核验覆盖';const age=(Date.now()/1000-r.asOf)/3600;return age<0?'时点异常':age>36?'已过期':'36h内';}
 const groupDefs=[['trend','趋势与点火'],['positions','大户与结算'],['reserves','储备辅助'],['history','历史净流'],['quality','来源与质量']];
 const style=`.evidence-table{min-width:1600px}.evidence-table th,.evidence-table td{min-width:118px;max-width:260px}.evidence-table .evidence-symbol{min-width:160px;position:sticky;left:0;background:#fff;z-index:1;box-shadow:1px 0 #dce5e9}.evidence-table thead .evidence-symbol{z-index:2}.evidence-table tbody tr:hover .evidence-symbol{background:#f5faf8}.evidence-table small{display:block;font-size:11px;line-height:1.65;color:#6d8290;margin-top:3px}.evidence-state{display:inline-block;border-radius:4px;padding:1px 5px;margin:2px 3px 2px 0;font-size:11px;white-space:nowrap}.evidence-state.yes{background:#e3f4ec;color:#167a72}.evidence-state.no{background:#f0f3f5;color:#657985}.evidence-state.unknown{background:#fff4df;color:#96652b}.hit-total{font-size:20px;font-weight:600;color:#203742}.hit-list{max-width:240px}.evidence-columns{margin:10px 0 16px;padding:10px 13px;border:1px solid #dce5e9;border-radius:8px;background:#fafcfc}.evidence-columns summary{cursor:pointer;font-size:13px;color:#167a72}.evidence-columns .column-options{display:flex;gap:16px;flex-wrap:wrap;margin-top:12px;align-items:center}.evidence-columns label{display:flex;align-items:center;gap:5px;font-size:13px}.evidence-columns button{padding:5px 9px;font-size:12px}.evidence-histogram{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0;font-size:12px}.evidence-histogram span{padding:3px 8px;border-radius:5px;background:#eef3f5;color:#536c79}.evidence-detail{margin-top:18px}.evidence-detail h3{margin:14px 0 7px}.evidence-detail table{font-size:12px}.evidence-detail td{overflow-wrap:anywhere}.evidence-detail a{overflow-wrap:anywhere}`;
 const responsiveStyle='.layout{grid-template-columns:216px minmax(0,1fr)}main,.panel,.results-panel,.query-controls .field,.tablewrap,.table-wrap{min-width:0}.tablewrap,.table-wrap{max-width:100%}.evidence-table th:first-child:has(input),.evidence-table td:first-child:has(input){min-width:36px;width:36px}.evidence-table [data-evidence-col="hits"]{min-width:180px}.evidence-columns small{display:block;color:#6d8290;font-size:12px;margin-top:8px}@media(max-width:1150px){.layout{grid-template-columns:175px minmax(0,1fr)}}@media(max-width:750px){.layout{display:block}}';
 function controls(id){return `<details class="evidence-columns" id="${id}"><summary>显示数据：六指标完整展示 · 可展开更多数据列</summary><div class="column-options">${groupDefs.map(([k,t])=>`<label><input type="checkbox" data-evidence-group="${k}">${t}</label>`).join('')}<button type="button" data-evidence-mode="all">全部数据列</button><button type="button" data-evidence-mode="core">仅核心六指标</button></div><small>扩展列独立于六项命中；可横向滚动。缺失为未知，历史净流与储备保留各自时点。</small></details><div class="evidence-histogram" id="${id}Histogram"></div>`;}
 function distribution(rows,flags){const counts=Array(7).fill(0);let unknown=0;for(const r of rows){const f=flags(r);counts[f.filter(v=>v===true).length]++;if(f.some(v=>v===null))unknown++;}return '<span>范围内命中分布（按合约）</span>'+counts.map((n,i)=>'<span>'+i+'项：'+n+'</span>').join('')+'<span>含未知：'+unknown+'</span>';}
 function columns(groups=[]){const core=[['symbol','合约 / 价格'],['cap','流通市值'],['hits','命中项数'],['oi','① OI / 市值'],['ratio','② 全体账户比'],['funding','③ 资金费率 / 结算周期'],['ema','④ EMA三线金叉'],['flow','⑤ 聪明钱流入'],['volume','⑥ 量价 / 成交额']];const extras={trend:[['priceRange','24h价格范围'],['trend','趋势候选（简化）'],['cross','EMA99上穿 / 本根量'],['volumeWindows','对齐成交窗口']],positions:[['assetOi','同资产币安OI'],['whales','大户账户 / 持仓比'],['settlements','实际费率结算']],reserves:[['reserveChange','储备日 / 7日变化'],['reserveStreak','储备减少持续性'],['reserveScope','储备数量 / 口径']],history:[['flowHistory','历史净流（独立时点）']],quality:[['times','行情 / K线时间'],['sourceTimes','OI / 市值源时间'],['mapping','资产映射 / 价格差'],['risk','风险 / 数据错误']]};return core.concat(groups.flatMap(g=>extras[g]||[]));}
  // Market-screen I4 is a technical grade; registry I4 remains the frozen pool-entry rule.
  function emaGradeStatus(r, levels=['weak','medium','strong']){
   if(typeof UnifiedCore!=='undefined')return UnifiedCore.emaGradeMatch(UnifiedCore.emaSignal(r),'grade_all');
   const grade=r&&r.ignitionGrade;
   return ['weak','medium','strong','none'].includes(grade)?levels.includes(grade):null;
  }
  function marketSignals(r, base){const values=base.slice();values[3]=emaGradeStatus(r);return values;}
 function cells(r,f,ctx={}){const p=ctx.reserve||null,h=ctx.history||null;const e=r.ema4h||[];
 const yes=f.map((v,i)=>v===true?circles[i]:'').join('、').replace(/、+/g,'、').replace(/^、|、$/g,'');
 const missing=f.map((v,i)=>v===null?circles[i]:'').filter(Boolean).join('、');
 let history='未取得该资产历史净流';if(h?.days?.length){const sum=h.days.reduce((s,d)=>s+d.netInflowTokens,0);let streak=0;for(const d of [...h.days].reverse()){if(d.netInflowTokens>=0)break;streak++;}history=unit(sum,' '+r.asset,4)+sub('负值=净流出；期末连续净流出 '+streak+'日')+sub(h.days[0].from+' 至 '+h.days.at(-1).to+'（结束不含，UTC）')+sub('Santiment历史；不计当前⑤');}
 return {
 symbol:esc(r.symbol)+sub(quotePrice(r.price,r.quote)),
 cap:money(r.capUsd)+sub(r.capTrusted?'项目已核对':'项目匹配待核对')+sub(r.capName||r.capId||'项目身份未明确核验'),
 hits:'<strong class="hit-total">'+f.filter(v=>v===true).length+'/6</strong>'+sub('已知 '+f.filter(v=>v!==null).length+'/6 · 未知 '+f.filter(v=>v===null).length+'项')+sub('命中 '+(yes||'无'))+sub('未知 '+(missing||'无'))+'<div class="hit-list">'+f.map((v,i)=>badge(v,circles[i]+' '+state(v))).join('')+'</div>',
 oi:unit(r.oiCapPct,'%')+(ctx.compact?'':sub('单合约名义OI '+money(r.oiUsd)))+mark(f,0),
 ratio:'<span title="多头账户数 / 空头账户数，不代表持仓金额比">'+number(r.accountRatio,3)+'</span>'+(ctx.compact?'':sub('多头账户数 / 空头账户数'))+mark(f,1),
 funding:unit(r.fundingPct,'%',5)+sub(ok(r.fundingHours)&&r.fundingHours>0?'每'+number(r.fundingHours,2)+'小时结算':'结算周期未知'),
 ema:emaSummaryHtml(r),
 flow:(ctx.compact?'<span title="'+esc(r.smartMoneyStatusNote||'暂无可核验数据，不代表净流入为零')+'">'+(f[4]===null?'暂无可核验数据':money(r.smartMoneyNetInflowUsd))+'</span>':(f[4]===null?'暂无可核验数据':money(r.smartMoneyNetInflowUsd))+sub(r.smartMoneyStatusNote||'暂无可核验数据'))+(ctx.compact?'':mark(f,4))+sub(r.smartMoneyEvidence?'4小时 · '+(r.smartMoneyTraders??'未知')+'个地址'+(ctx.compact?'':' · 获取 '+date(r.smartMoneyEvidence.retrieved_at)):ctx.compact?'':'数据来源或匹配详情见说明'),
 volume:unit(r.volumeRatio,'×')+sub('同窗24h价变 '+unit(r.priceChangeWindowPct,'%'))+(ctx.compact?'':sub('滚动24h成交 '+money(r.volume24Usd)))+mark(f,5),
 priceRange:sub('高 '+quotePrice(r.high24,r.quote))+sub('低 '+quotePrice(r.low24,r.quote))+sub('标记 '+quotePrice(r.markPrice,r.quote)) +sub('指数 '+quotePrice(r.indexPrice,r.quote)),
 trend:badge(trend(r),'EMA多头且收盘在线上：'+state(trend(r)))+badge(trendVolume(r),'加放量及24h上涨：'+state(trendVolume(r)))+sub('候选条件；非回踩/入场确认'),
 cross:(()=>{const g=r.ignitionGrade;const ok=g==='strong'||g==='medium'?true:g==='weak'?false:null;const label=g==='strong'?'强🟢':g==='medium'?'中🟡':g==='weak'?'弱🔴勿入场':g==='none'?'无':'未记录';const evs=cycleConfirmEvents(r);const latest=evs.length?evs[evs.length-1]:null;const confLabel=latest?('周期确认 '+(latest.level||'')+' · '+String(latest.confirm_time||'').slice(0,10)):('周期确认 无');return badge(ok,'本根 '+label)+sub(confLabel+(latest?(' · '+esc(latest.event_id||'').slice(0,24)):''))+sub('前7根量 '+unit(r.barVolumeRatio7,'×')+'（本根）')+sub('旧20根量 '+unit(r.barVolumeRatio,'×')+'；旧上穿标记 '+state(r.crossEma99))+sub('多头栈 '+state(r.emaStack)+' · 刚形成='+(r.fullCross===true?'满足':(r.emaFirstCross25over99===true&&r.emaStack===true?'满足':(r.fullCross===false||r.emaFirstCross25over99===false?'不满足':'未知(需首次上穿字段)'))));})(),
 volumeWindows:sub('已结束24h '+money(r.volumeWindowUsd))+sub('前7独立24h窗均量 '+money(r.volumeBaselineUsd)),
 assetOi:money(r.binanceAssetOiUsd)+sub('同资产币安汇总OI/市值 '+unit(r.binanceAssetOiCapPct,'%'))+sub(r.assetOiCoverage?`OI覆盖 ${r.assetOiCoverage.known}/${r.assetOiCoverage.expected} · ${r.assetOiCoverage.complete?'完整':'不完整'}；部分和 ${money(r.binanceAssetOiPartialUsd)}`:'旧样本未记录OI汇总覆盖')+sub('不同合约合计；不是全市场'),
 whales:sub('大户账户 '+number(r.topAccountRatio,3))+sub('大户持仓 '+number(r.topPositionRatio,3))+sub('缺少时不以全体账户比替代'),
 settlements:r.settledFunding?.length?sub('已存 '+r.settledFunding.length+'次结算，负值 '+number(r.negativeSettlements,0)+'次')+sub('观测周期 '+unit(r.observedFundingHours,'h')):'尚未补充实际结算历史',
 reserveChange:p?unit(p.dayChangePct,'%')+' / '+unit(p.weekChangePct,'%')+sub('日 / 7日；'+reserve(p))+sub('截止 '+date(p.asOf*1000)):'未核验覆盖',
 reserveStreak:p?sub('连续减少 '+p.consecutiveDecreaseDays+'日')+sub('7日减少 '+p.decreasingDaysInWeek+'日'):'未知',
 reserveScope:p?unit(p.reserveTokens,' '+r.asset,4)+sub(p.chain+' · 原生链局部储备')+sub('余额变化；非逐笔净流、非吸筹确认'):'未核验原生链映射',
 flowHistory:history,
 times:sub('行情 '+date(r.asOf))+sub('已收盘4h '+date(r.candleAsOf)),
 sourceTimes:sub('OI '+date(r.oiAsOf))+sub('市值 '+date(r.capAsOf))+sub('账户 '+date(r.accountAsOf)),
 mapping:sub('项目ID '+(r.capId||'未知'))+sub('市值价格与合约差 '+unit(r.capPriceGapPct,'%'))+sub(r.capStatus||'未核验') ,
 risk:sub((r.riskFlags||[]).join('；')||'未存风险标记')+sub(Object.entries(r.errors||{}).map(([k,v])=>k+': '+v).join('；')||'未记录接口错误；不代表数据完整')
 };}
 function tableCells(r,f,groups,ctx,link){const c=cells(r,f,ctx);return columns(groups).map(([k])=>'<td class="'+(k==='symbol'?'evidence-symbol':'')+'" data-evidence-col="'+k+'">'+(k==='symbol'?link+sub(quotePrice(r.price,r.quote)):c[k])+'</td>').join('');}
 function headers(groups){return columns(groups).map(([k,t])=>'<th class="'+(k==='symbol'?'evidence-symbol':'')+'" data-evidence-col="'+k+'">'+t+'</th>').join('');}
 function detail(r,f,ctx,extrasOnly=false){const c=cells(r,f,ctx);const all=columns(groupDefs.map(x=>x[0])).slice(extrasOnly?9:0);return '<section class="evidence-detail"><h3>'+(extrasOnly?'扩展数据与辅助证据':'完整快照与辅助证据')+'</h3><p>命中按原六项统计；数据源各自标时。EMA观察数据以匹配的已收盘K线为准；箱体与全历史ATH另查覆盖。</p><div class="tablewrap table-wrap"><table><tbody>'+all.filter(([k])=>k!=='symbol').map(([k,t])=>'<tr><td>'+esc(t)+'</td><td>'+c[k]+'</td></tr>').join('')+'<tr><td>来源</td><td>'+esc(r.source||'Binance public REST')+sub(r.url||'')+sub('市值源 '+(r.capSource||'未知'))+sub(r.emaMethod||'EMA数据未知')+'</td></tr></tbody></table></div></section>'+observationDetailsHtml(r)+confirmEventDetailsHtml(r);}
 function csv(rows,flags,context){const keys=['candleHistoryStatus','emaWarmupRemaining','emaWarmupExpectedAt','contractOnboardAt','smartMoneyNetInflowUsd','smartMoneyTraders','smartMoneyStatus','smartMoneyStatusNote','smartMoneyEvidence','symbol','asset','market','quote','price','priceChange24Pct','markPrice','indexPrice','high24','low24','capUsd','capId','capName','capTrusted','capStatus','capSource','capPriceGapPct','oiUsd','oiCapPct','binanceAssetOiUsd','binanceAssetOiCapPct','accountRatio','topAccountRatio','topPositionRatio','fundingPct','fundingHours','fundingDailyPct','fundingAprPct','closed4h','ema7','ema25','ema99','crossEma99','barVolumeRatio','volumeRatio','priceChangeWindowPct','volume24Usd','volumeWindowUsd','volumeBaselineUsd','hitCount','knownCount','screeningSchema','poolEntryBelowEma','indicator1','indicator2','indicator3','indicator4','indicator5','indicator6','trendCandidate','trendVolumeCandidate','asOf','candleAsOf','oiAsOf','accountAsOf','capAsOf','riskFlags','errors','reserveDayPct','reserveWeekPct','reserveStreak','reserveAsOf','reserveStatus','reserveExactNetflow','historicalNetTokens','historicalFlowFrom','historicalFlowTo','exportContext','scan_id','revision','available_at','fieldAvailableAt','registryHash','registryVersion','fundingIntervalStatus','assetOiCoverage','binanceAssetOiPartialUsd','fullCross','mediumCrossEvent','barVolumeRatio7','newLowCount10','amplitudeRatio20','amplitudeOk','ignitionGrade','emaSqueezeStage','candidateEnteredAt','candidateExpiresAt','entryOiUsd','pendingCross','crossConfirm','exitReason','exitTime','rules_version','emaObservation','confirmedEvents','pendingCrossEvidence','csvSchema'];const q=v=>'"'+String(v??'').replaceAll('"','""')+'"';return '\uFEFF'+keys.join(',')+'\n'+rows.map(r=>{const f=flags(r),ctx=context(r),p=ctx.reserve,h=ctx.history;const x={...r,screeningSchema:'market-ema-grade-v1',poolEntryBelowEma:r.belowAllEma,ema7:r.ema4h?.[0],ema25:r.ema4h?.[1],ema99:r.ema4h?.[2],hitCount:f.filter(v=>v===true).length,knownCount:f.filter(v=>v!==null).length,trendCandidate:state(trend(r)),trendVolumeCandidate:state(trendVolume(r)),riskFlags:JSON.stringify(r.riskFlags||[]),errors:JSON.stringify(r.errors||{}),reserveDayPct:p?.dayChangePct,reserveWeekPct:p?.weekChangePct,reserveStreak:p?.consecutiveDecreaseDays,reserveAsOf:p?.asOf,reserveStatus:reserve(p),reserveExactNetflow:false,historicalNetTokens:h?.days?.reduce((s,d)=>s+d.netInflowTokens,0),historicalFlowFrom:h?.days?.[0]?.from,historicalFlowTo:h?.days?.at(-1)?.to,exportContext:ctx.exportContext,registryHash:typeof StrategyRegistry!=='undefined'?StrategyRegistry.hash:r.registryHash,registryVersion:typeof StrategyRegistry!=='undefined'?StrategyRegistry.data.version:'',fieldAvailableAt:JSON.stringify(r.fieldAvailableAt??null),assetOiCoverage:JSON.stringify(r.assetOiCoverage??null)};f.forEach((v,i)=>x['indicator'+(i+1)]=state(v));x.smartMoneyEvidence=JSON.stringify(r.smartMoneyEvidence??null);const st=r.emaSqueezeStage||{};x.csvSchema='market-evidence-csv-v5-smart-money';x.emaObservation=JSON.stringify(r.emaObservation??null);x.confirmedEvents=JSON.stringify(cycleConfirmEvents(r));x.pendingCrossEvidence=JSON.stringify(st.pendingCross??null);x.fullCross=r.fullCross??(st.fullCross??'');x.mediumCrossEvent=typeof (r.mediumCrossEvent??st.mediumCrossEvent)==='object'?JSON.stringify(r.mediumCrossEvent??st.mediumCrossEvent):(r.mediumCrossEvent??st.mediumCrossEvent??'');x.barVolumeRatio7=r.barVolumeRatio7;x.newLowCount10=r.newLowCount10;x.amplitudeRatio20=r.amplitudeRatio20;x.amplitudeOk=r.amplitudeOk;x.ignitionGrade=r.ignitionGrade;x.emaSqueezeStage=st&&typeof st==='object'?JSON.stringify(st):'';x.candidateEnteredAt=st.candidateEnteredAt||'';x.candidateExpiresAt=st.candidateExpiresAt||'';x.entryOiUsd=st.entryOiUsd;x.pendingCross=st.pendingCross==null?'':!!st.pendingCross;x.crossConfirm=st.crossConfirm?JSON.stringify(st.crossConfirm):'';x.exitReason=st.exitReason||st.exit_reason||r.exitReason||'';x.exitTime=st.exitTime||st.exitedAt||r.exitTime||'';x.rules_version=(st.crossConfirm&&st.crossConfirm.rules_version)||r.rules_version||(typeof StrategyRegistry!=='undefined'?StrategyRegistry.data.version:'');return keys.map(k=>q(x[k])).join(',');}).join('\n');}
 function selectedGroups(root){return [...root.querySelectorAll('[data-evidence-group]:checked')].map(e=>e.dataset.evidenceGroup);}
 function bindControls(root,refresh,storageKey){try{const saved=JSON.parse(localStorage.getItem(storageKey)||'[]');root.querySelectorAll('[data-evidence-group]').forEach(e=>e.checked=Array.isArray(saved)&&saved.includes(e.dataset.evidenceGroup));}catch{}const save=()=>{try{localStorage.setItem(storageKey,JSON.stringify(selectedGroups(root)));}catch{}refresh();};root.addEventListener('change',save);root.addEventListener('click',e=>{const b=e.target.closest('[data-evidence-mode]');if(!b)return;root.querySelectorAll('[data-evidence-group]').forEach(e=>e.checked=b.dataset.evidenceMode==='all');save();});}

 function cycleConfirmEvents(r){
  const st=r&&r.emaSqueezeStage;const out=[];const seen=new Set();
  if(!st||typeof st!=='object')return out;
  const push=ev=>{
   if(!ev||typeof ev!=='object')return;
   const eid=ev.event_id;
   if(!eid||ev.void||ev.duplicate||ev.blocked||(st.candidateCycleId&&ev.candidate_cycle_id!==st.candidateCycleId))return;
   if(seen.has(eid))return;seen.add(eid);out.push(ev);
  };
  (st.confirmedEvents||[]).forEach(push);push(st.crossConfirm);push(st.latestConfirm);
  return out;
 }
 function isActivePoolRow(r){return (r&&r.emaSqueezeStage&&r.emaSqueezeStage.candidateStatus)==='active';}
 function hasCycleConfirm(r){return cycleConfirmEvents(r).length>0;}
 function legacyEmaSummaryHtml(r){
  // B方案：摘要只呈现本根等级；新穿/持续、历史确认与候选阶段见详情。
  const grade=r.ignitionGrade;
  const titles={strong:'三线金叉已形成',medium:'EMA7 上穿 EMA25',weak:'价格站上 EMA99',none:'原A本根无等级信号'};
  const labels={strong:'强信号',medium:'中信号',weak:'弱信号'};
  const colors={strong:'#008b73',medium:'#b98000',weak:'#cf6854'};
  const crosses=r.emaObservation?.long_cross_events||[];
  const names={ema7_cross_25:'EMA7上穿EMA25',ema7_cross_99:'EMA7上穿EMA99',ema25_cross_99:'EMA25上穿EMA99'};
  const title=crosses.length?crosses.map(x=>names[x.kind]||x.kind).join(' / '):titles[grade]||'4h 数据不足';
  const note=crosses.length&&(!labels[grade])?'技术交叉 · 未单独分级；资格另查':grade==='none'?(r.emaObservation?.long_crosses&&Object.values(r.emaObservation.long_crosses).every(v=>v===false)?'本根无新上穿':'独立交叉尚未核验，请查看详情'):labels[grade]
   ?'<span style="color:'+colors[grade]+';font-weight:600">'+labels[grade]+'</span><span style="color:#8b9eac"> · '+(grade==='weak'?'7/25 未金叉':'放量确认')+'</span>'
   :'暂不显示信号等级';
  return '<div class="ema-summary" style="text-align:center"><span style="font-size:14px;font-weight:600">'+esc(title)+'</span>'+(note?'<div class="coin-sub coin-sub-critical" style="font-weight:400;text-align:center;color:#8b9eac">'+note+'</div>':'')+'</div>';
 }
 function emaSummaryHtml(r){
  const C=typeof UnifiedCore!=='undefined'?UnifiedCore:typeof window!=='undefined'?window.UnifiedCore:null;
  if(!C)return legacyEmaSummaryHtml(r);
  const s=C.emaSignal(r),labels={strong:'强信号',medium:'中信号',weak:s.reason==='price_breakout'?'弱观察':'弱信号',none:s.reason==='stack_continues'?'多头排列持续':'无新信号',unknown:'数据待核'},colors={strong:'#008b73',medium:'#b98000',weak:'#b65b4b',none:s.reason==='stack_continues'?'#008b73':'#6d8290',unknown:'#96652b'};
  const title=s.kinds.map(k=>C.emaNames[k]).join(' / ')||({stack_continues:'EMA7＞EMA25＞EMA99',no_new_cross:'本根无新向上交叉'}[s.reason]||'缺少同根交叉证据');
  const volume=s.grade==='medium'&&s.volume_status==='unknown'?' · 量能待核':'';
  return '<div class="ema-summary" data-ema-grade="'+esc(s.grade)+'" style="text-align:center"><strong style="color:'+colors[s.grade]+'">'+labels[s.grade]+volume+'</strong><div class="coin-sub coin-sub-critical" style="font-weight:500;color:#334b5a">'+esc(title)+'</div><div class="coin-sub" style="color:#6d8290">'+esc(C.emaWindow(s.bar_time))+'</div></div>';
 }

 function observationDetailsHtml(r){
  const o=r.emaObservation,h=o?.history60;
  if(!o)return '<section class="ema-observation-detail"><h3>EMA观察证据</h3><p>旧快照未记录，且无匹配的已归档K线。持续根数与历史状态未知。</p></section>';
  const rows=[['证据时间',date(o.bar_time)],['K线窗口',number(o.valid_bars,0)+'根 · '+(o.quality==='valid'?'满足120根预热':'数据不足或存在缺口')],
   ['EMA7上穿EMA25 / EMA99',state(o.long_crosses?.ema7_cross_25)+' / '+state(o.long_crosses?.ema7_cross_99)],
   ['EMA25上穿EMA99 / 完整多头排列',state(o.long_crosses?.ema25_cross_99)+' / '+state(o.long_stack)],
   ['价格新穿EMA99',state(o.weak_new_cross)],['连续收盘高于各自EMA99',ok(o.above99_count)?(o.above99_count_exact?'':'至少')+number(o.above99_count,0)+'根':'未知'],
   ['之前60根完整排列',h?.stack_seen===true?'出现过':h?.stack_seen===false?'未观察到':'历史不足'],
   ['历史覆盖',number(h?.coverage,0)+'/60根'],['最近可信完整排列',h?.latest_stack_time?date(h.latest_stack_time):'未知'],
   ['较之前60根最高收盘回撤',unit(o.drawdown60_pct,'%')],['距EMA99 / EMA7',unit(o.distance_ema99_pct,'%')+' / '+unit(o.distance_ema7_pct,'%')],
   ['单根 / 5期涨幅',unit(o.candle_return_pct,'%')+' / '+unit(o.return5_pct,'%')],['ATR14（Wilder）',number(o.atr14,8)],
   ['距EMA99 / EMA7（ATR倍数）',unit(o.distance_ema99_atr,' ATR')+' / '+unit(o.distance_ema7_atr,' ATR')],
   ['阳线 / 上影实体比',state(o.candle_shape?.bullish)+' / '+unit(o.candle_shape?.upper_body_ratio,'×')],
   ['前7根单根量倍数',unit(r.barVolumeRatio7,'×')+' · '+(o.volume_unit==='usd_contract_notional'?'合约美元名义额':'报价成交额')],
   ['当前技术分级',typeof UnifiedCore!=='undefined'?({strong:'强',medium:'中',weak:'弱',none:'无新信号',unknown:'待核'})[UnifiedCore.emaSignal(r).grade]:'待核']];
  return '<section class="ema-observation-detail"><h3>EMA金叉 · 4H分级依据</h3><p>形态、距离及历史排列仅作观察；时效规则未启用。</p><table><tbody>'+rows.map(([a,b])=>'<tr><td>'+esc(a)+'</td><td>'+esc(b)+'</td></tr>').join('')+'</tbody></table><p>本页技术分级按1021：99下7穿25为弱，突破99为中，新交叉形成完整排列且4H量比≥2为强；缺量不提升强。技术等级不等于原A候选资格或下一根周期确认。</p><details><summary>最近60根归档内的向上交叉（区分本根与历史）</summary><ul>'+((typeof UnifiedCore!=='undefined'?UnifiedCore.emaHistory(r):[]).map(x=>'<li>'+(x.historical?'历史 · ':'本根 · ')+esc(({strong:'强信号',medium:'中信号',weak:'弱信号',none:'无新信号',unknown:'待核'})[x.grade])+' · '+esc(x.kinds.map(k=>UnifiedCore.emaNames[k]).join(' / '))+' · '+esc(UnifiedCore.emaWindow(x.bar_time))+(x.grade==='medium'&&x.volume_status==='unknown'?' · 量能待核':'')+'<small> · '+esc(x.event_id||'证据冲突')+'</small></li>').join('')||'<li>此归档窗口内暂无可证实记录；不代表全部历史从未出现。</li>')+'</ul></details><details><summary>来源与计算档案</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">'+esc(JSON.stringify(o,null,2))+'</pre></details></section>';
 }
 function confirmEventDetailsHtml(r){
  const evs=cycleConfirmEvents(r);
  if(!evs.length)return '<p class="muted">本候选周期尚无确认点火事件（本根分级≠周期确认）。</p>';
  return evs.map((ev,i)=>{
   const bar=ev.confirm_bar_time,ref=ev.next_open_reference;
   const age=ok(bar)&&ok(r.candleAsOf)&&r.candleAsOf>=bar?Math.floor((r.candleAsOf-bar)/14400000):null;
   const deviation=ok(ref?.price)&&ref.price>0&&ok(r.price)?(r.price/ref.price-1)*100:null;
   const rows=[['金叉根收盘',ev.cross_bar_time?date(ev.cross_bar_time):'未知（保留旧记录）'],['确认根收盘',bar?date(bar):'未知（旧confirm_time不是确认根）'],
    ['首次发现',ev.first_seen_at?date(ev.first_seen_at):'旧记录未知'],['系统确认',date(ev.confirmed_at||ev.confirm_time)],
    ['发现类型',ev.historical?'历史确认／延迟发现，不补过去成交':'扫描发现'],['确认后经过',unit(age,'根4h（截至快照K线）',0)],
    ['下一开盘参考',ref?number(ref.price,8)+' · '+date(ref.open_time):'未取得（不猜测成交价）'],
    ['快照价格偏离参考',unit(deviation,'%')+' · 价格截至 '+date(r.asOf)],['执行时效','时效规则未启用'],['金叉根EMA99',number(ev.cross_ema99,8)],['确认量倍数',unit(ev.confirm_vol_ratio,'×')]];
   return '<details '+(i===evs.length-1?'open ':'')+'class="ema-confirm-event"><summary>'+esc(({strong:'强',medium:'中'}[ev.level]||'未知')+'级周期确认 · '+(bar?date(bar):'确认根时间未知'))+'</summary><table><tbody>'+rows.map(([k,v])=>'<tr><td>'+esc(k)+'</td><td>'+esc(v)+'</td></tr>').join('')+'</tbody></table><details><summary>事件档案</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">'+esc(JSON.stringify(ev,null,2))+'</pre></details></details>';
  }).join('');
 }

 return {emaSummaryHtml,observationDetailsHtml,emaGradeStatus,marketSignals,columns,headers,tableCells,cells,controls,distribution,detail,csv,bindControls,selectedGroups,style:style+responsiveStyle,trend,trendVolume,cycleConfirmEvents,isActivePoolRow,hasCycleConfirm,confirmEventDetailsHtml};
})();

window.MarketEvidence = MarketEvidence;
