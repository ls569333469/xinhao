(function(root){
 'use strict';
 const data=JSON.parse('__REGISTRY_JSON__'),hash='__REGISTRY_HASH__';
 // Display formulas derive from the actual rule thresholds, not duplicate defaults.
 data.indicators.forEach(i=>{
  if(i.id==='I1')i.formula='OI / 流通市值 > '+i.rule.value+'%';
  if(i.id==='I2')i.formula='全体账户多空比 < '+i.rule.value;
  if(i.id==='I3')i.formula='按核验周期简单年化 < '+i.rule.value+'%';
  if(i.id==='I6')i.formula='量倍数 > '+i.rule.args[0].value+'× 且同窗 |价变| < '+i.rule.args[1].value+'%';
 });
 const known=x=>typeof x==='number'&&Number.isFinite(x);
 const all=a=>a.includes(false)?false:a.includes(null)?null:true;
 function evaluate(rule,row){
  const op=rule.op;
  if(op==='unknown')return null;
  if(op==='smart_inflow'){const e=row.smartMoneyEvidence||{},v=row.smartMoneyNetInflowUsd;return row.smartMoneyStatus==='covered'&&e.schema===rule.schema&&e.period===rule.period&&e.tagType===2&&known(v)&&e.netInflowUsd===v?v>rule.value:null;}
  if(op==='all')return all(rule.args.map(r=>evaluate(r,row)));
  if(op==='signals'){const f=signals(row);return all(rule.indices.map(i=>f[i]));}
  if(op==='below_ema'||op==='trend'){
   const c=row.closed4h,e=row.ema4h;
   if(!known(c)||!Array.isArray(e)||e.length!==3||!e.every(known))return null;
   return op==='below_ema'?e.every(v=>c<v):e[0]>e[1]&&e[1]>e[2]&&e.every(v=>c>v);
  }
  if(op==='is_true'){
   const value=row[rule.field];
   if(value==null)return null;
   return value===true;
  }
  if(op==='ignition_grade'){
   const grade=row[rule.field||'ignitionGrade'];
   if(!['strong','medium','weak','none'].includes(grade))return null;
   const wanted=rule.value;
   return Array.isArray(wanted)?wanted.includes(grade):grade===wanted;
  }
  const value=row[rule.field];if(!known(value))return null;
  if(rule.field==='fundingAprPct'&&['estimated','unknown'].includes(row.fundingIntervalStatus))return null;
  if(op==='gt')return value>rule.value;
  if(op==='lt')return value<rule.value;
  if(op==='abs_lt')return Math.abs(value)<rule.value;
  throw Error('Unsupported rule: '+op);
 }
 function signals(row,overrides){return data.indicators.map(i=>{
  let rule=i.rule;
  if(overrides&&Object.hasOwn(overrides,i.id))rule={...rule,value:overrides[i.id]};
  if(overrides&&i.id==='I6')rule={op:'all',args:[{...rule.args[0],value:overrides.volume},{...rule.args[1],value:overrides.quiet}]};
  return evaluate(rule,row);
 });}
 function candidateText(s){return s.indices?s.indices.map(i=>String.fromCharCode(0x2460+i)+data.indicators[i].formula).join(' ＋ '):s.id==='T8'?'已多头排列且收盘在三线上方（趋势延续，≠I4b从下方点火）；可选量>1.5×且同窗上涨':s.id==='T4'?'连续独立量价与买盘；⑥仅单次线索':'结构规则缺数据，不能判定完整候选';}
  const presentation={T2:{risk:'④按弱／中／强筛选当前技术状态；三线下方仅用于持续候选池入池。技术分级、历史确认与执行状态分别核验。'}};
  const strategies=data.strategies.map(s=>({...s,...(presentation[s.id]||{}),version:data.version,registryHash:hash,candidateText:candidateText(s),why:'独立判定本策略，不要求先通过其他策略。'}));
 const presets=Object.fromEntries([...strategies.filter(s=>s.preset).map(s=>[s.preset,{indices:s.indices,name:s.indices.map(i=>String.fromCharCode(0x2460+i)).join('')+' '+s.title,help:s.risk,strategyId:s.id,version:data.version}]),...data.combinations.map(c=>[c.id,{...c,help:c.help?.replace('④为入池筛选','三线下方为历史入池条件；页面④按弱／中／强筛选')}])]);
 root.StrategyRegistry=Object.freeze({data,hash,strategies,presets,signals,evaluate,all});
 if(typeof module!=='undefined')module.exports=root.StrategyRegistry;
})(typeof window!=='undefined'?window:globalThis);
