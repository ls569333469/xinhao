/* v3.3 r5: read-only unified UI; no collection, AI, simulation or trade requests. */
 (async function(){'use strict';
 const panel=document.querySelector('[data-view="market"]');if(!panel)return;
 let host=panel.querySelector('#unifiedWorkbench');if(!host){const old=document.createElement('div');old.className='legacy-market';while(panel.firstChild)old.append(panel.firstChild);panel.append(old);host=document.createElement('div');host.id='unifiedWorkbench';panel.append(host);const response=await fetch('/unified-workbench-fragment.html',{cache:'no-store'});if(!response.ok)throw Error('统一界面未完整发布');host.innerHTML=await response.text();}
 const binding=document.getElementById('ruleMarketFilterBanner');if(binding)host.querySelector('.topfilters').before(binding);
 const $=id=>document.getElementById('u-'+id)||host.querySelector('[id="'+id+'"]'),$$=s=>[...host.querySelectorAll(s)];
 let rows=[],payload=null;const oldRender=window.renderMarket;
 let startupSavedQuery=null;try{startupSavedQuery=sessionStorage.getItem('unified-last-query-v33r5');}catch{}
 const state={signals:new Set(),mode:'and',minimum:1,search:'',market:'all',cap:'all',minCap:'',maxCap:'',trusted:false,history:'all',context:'latest',listing:'current',aStage:'grade_all',bStage:'cur_event',bFrom:'',bTo:'',cStage:'confirm',reserveMode:'covered',reserveThreshold:1,result:'pass',sort:'symbol',desc:true,selected:selectedTokens,quick:'all'};
const defaults={...COMBO_GUIDES.find(c=>c.id==='none').thresholds};
const thresholds={...defaults};
const catalog=window.WorkbenchSignals;
const lookup=id=>catalog.find(s=>s.id===id);
const triAnd=v=>v.includes(false)?false:v.includes(null)?null:true;
const triOr=v=>v.includes(true)?true:v.includes(null)?null:false;
const fmt=(v,d=2)=>v==null?'未取得':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:d});
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=v=>v==null?'市值未知':v>=1e8?fmt(v/1e8)+' 亿美元':fmt(v/1e4)+' 万美元';
const bLabel={complete:'放量价稳 · 已确认',pending:'放量价稳 · 等待下一小时确认',unknown:'数据待补齐，暂不能确认',fail:'已放量；资格未通过（非完整确认）',confirmation_failed:'下一小时未通过确认',corrected:'信号已更正',legacy:'旧记录待复核',unmonitored:'本轮未检查',insufficient:'历史数据不足或有缺失',none:'本轮未出现放量价稳信号'};
const aLabel={active:'活跃候选',confirmed:'周期已确认',none:'不在观察池',exited:'已退出候选'};
const cLabel={confirm:'已确认',waiting:'等待确认',none:'无候选',expired:'已到期'};
const aOptions={grade_all:'全部',grade_watch:'弱观察',grade_weak:'弱',grade_medium:'中',grade_strong:'强',grade_mediumstrong:'中＋强'};
const legacyAOptions={"all_signals":"旧口径：技术交叉或原A等级","cross_any":"本根任一向上技术交叉","cross_7_25":"本根EMA7上穿EMA25","cross_7_99":"本根EMA7上穿EMA99","cross_25_99":"本根EMA25上穿EMA99","long_stack":"完整EMA7＞EMA25＞EMA99排列","strongmedium":"原A点火等级：中＋强","strong":"原A点火等级：强","medium":"原A点火等级：中","weak":"原A弱观察","active":"原A活跃候选（含已确认）","confirm":"原A周期已确认","any":"原A候选或点火等级","mediumweak":"原A点火等级：弱＋中","strongweak":"原A点火等级：弱＋强","allgrades":"原A全部点火等级"};
function aStageLabel(stage){return aOptions[stage]||(Object.hasOwn(legacyAOptions,stage)?"兼容条件 · "+legacyAOptions[stage]:"无效条件，请重新选择");}
const bOptions={cur_event:'本轮：放量记录（含资格未通过）',cur_trigger:'本轮：已放量（含条件未通过）',cur_complete:'本轮：吸筹背离已确认',cur_pending:'本轮：等待下一小时确认',cur_unknown:'本轮：数据不足',cur_fail:'本轮：条件或确认未通过',cur_unmonitored:'本轮：未检查',cur_none:'本轮：无新信号',complete:'历史：最近记录已确认',pending:'历史：最近记录待确认',unknown:'历史：最近记录数据不足',event:'历史：有信号记录',fail:'历史：条件未满足 / 确认失败 / 已更正',prior:'历史：曾有已确认信号',unmonitored:'历史：未记录 / 数据不足'};
const cOptions={confirm:'已确认',waiting:'等待确认',candidate:'候选或已确认'};
let visible=[],resultRows=[],lastFocus=null,detail=null,toastTimer,editing=null;
function badge(text,color='gray'){return '<span class="badge '+color+'"><i></i>'+esc(text)+'</span>';}
const resultKey=v=>v===true?'pass':v===false?'fail':'unknown';
function profile(id){if(id==='i1')return 'OI / 市值 > '+thresholds.tOi+'%';if(id==='i2')return '账户比 < '+thresholds.tLsr;if(id==='i3')return '年化 < '+thresholds.tApr+'%';if(id==='i4')return aStageLabel(state.aStage);if(id==='i5')return '净流入 > 0';if(id==='i6')return '量 > '+thresholds.tVol+'× · 价变 < '+thresholds.tQuiet+'%';if(id==='b')return bOptions[state.bStage];if(id==='c')return cOptions[state.cStage];return id==='reserve'?'近期已核验覆盖':'观察标签样例';}
const expandedSignals=new Set();
const basicAGrades=Object.entries(aOptions);
function aGradeOptions(scope){
 const choices=basicAGrades.slice();
 if(Object.hasOwn(legacyAOptions,state.aStage))choices.unshift([state.aStage,aStageLabel(state.aStage)+"（保留原条件）"]);
 
 return choices.map(([key,label])=>{const n=payload?scope.filter(r=>core.signals(r,{...state,aStage:key},thresholds).i4===true).length:null;return '<option value="'+key+'" '+(key===state.aStage?'selected':'')+'>'+esc(label)+(n===null?'':'（'+n+'）')+'</option>';}).join('');
}
function signalCount(item,scope){
 if(item.planning)return '未接入';
 if(!payload)return '读取中';
 if(payload.projection==='scan-only-v1'&&['b','c'].includes(item.id))return '待读取';
 const values=scope.map(r=>signalValue(r,item.id));
 const pass=values.filter(v=>v===true).length,unknown=values.filter(v=>v===null).length;
 if(values.length&&unknown===values.length)return '待核验';
 return unknown?pass+' · 待核'+unknown:pass;
}
function pickerHtml(item,scope){
 const chosen=state.signals.has(item.id),open=expandedSignals.has(item.id);
 const count=signalCount(item,scope);
 const group=(field,choices)=>'<fieldset class="inline-choices"><legend>'+esc(item.name)+'条件</legend>'+choices.map(([value,label])=>'<label><input type="radio" name="signal-'+field+'" data-inline-stage="'+field+'" value="'+value+'" '+(state[field]===value?'checked':'')+'>'+esc(label)+'</label>').join('')+'</fieldset>';
 let controls='';
 if(item.id==='i4')controls=group('aStage',basicAGrades)+(Object.hasOwn(legacyAOptions,state.aStage)?'<p class="settings-note">当前保留：'+esc(aStageLabel(state.aStage))+'。尚未选择新版技术等级；选择上方六项之一才会替换此条件。</p>':'');
 else if(item.id==='b')controls=group('bStage',Object.entries(bOptions).filter(([k])=>k.startsWith('cur_')))+group('bStage',Object.entries(bOptions).filter(([k])=>!k.startsWith('cur_')));
 else if(item.id==='c')controls=group('cStage',Object.entries(cOptions))+'<p>按1015独立资格与T+1至T+10确认；当前筛选只采用追踪启用、非历史且数据边界一致的完整记录。旧观察可在ABC持续追踪中查看。</p>';
 else if(item.id==='reserve')controls=group('reserveMode',Object.entries({covered:'近期已核验覆盖',day:'日余额减少',week:'7日余额减少',streak:'连续减少至少3天'}))+'<label>减少幅度至少（%）<input type="number" min="0" max="100" data-inline-value="reserveThreshold" value="'+state.reserveThreshold+'"></label>';
 else {const fields={i1:[['tOi','OI / 市值大于（%）']],i2:[['tLsr','账户比小于']],i3:[['tApr','简单年化小于（%）']],i6:[['tVol','24H量倍数大于'],['tQuiet','同窗绝对价变小于（%）']]};controls=(fields[item.id]||[]).map(([key,label])=>'<label>'+esc(label)+'<input type="number" step="any" data-inline-threshold="'+key+'" value="'+thresholds[key]+'" '+(key==='tApr'?'':'min="0"')+'></label>').join('')||'<p>'+esc(profile(item.id))+'</p>';}
 return '<div class="signal-item '+(chosen?'selected':'')+'"><div class="signal-line"><input type="checkbox" data-signal="'+item.id+'" aria-label="选择'+esc(item.name)+'" '+(chosen?'checked':'')+'><button class="signal-name" data-expand-signal="'+item.id+'" aria-expanded="'+open+'" aria-controls="signal-options-'+item.id+'">'+esc(item.num+' '+item.name)+' <span aria-hidden="true">'+(open?'▾':'▸')+'</span></button><span class="count">'+count+'</span></div><div class="signal-desc">'+esc(profile(item.id))+'</div><div class="signal-options" id="signal-options-'+item.id+'" data-owner-signal="'+item.id+'" '+(open?'':'hidden')+'>'+controls+'<p class="settings-note">选择等级或修改阈值会启用本项筛选；原始策略判定不变。</p></div></div>';
}
function activeFilterSummary(scope){
 const counts=[...state.signals].map(id=>{const item=lookup(id),values=scope.map(r=>signalValue(r,id));return item.num+' '+item.name+'：'+values.filter(v=>v===true).length+'命中、'+values.filter(v=>v===null).length+'待核验';});
 return counts.length?' 当前启用条件（各项独立统计）：'+counts.join('；')+'。'+(state.mode==='and'&&counts.length>1?'全部命中要求同时满足；可取消不需要的信号。':''):'';
}
function emptyResultMessage(scope){
 if(!scope.length)return '当前公共范围内没有可得合约，请检查顶部搜索、市场和市值条件。';
 if(state.signals.size===1&&state.signals.has('b')&&state.bStage==='cur_complete'&&state.result==='pass')return '本轮尚无完整确认的吸筹背离信号。可查看本轮放量记录，或切换历史信号。'+activeFilterSummary(scope);
 if(state.result==='pass'&&state.signals.has('i4')){
  const counts=['weak','medium','strong'].map(g=>scope.filter(r=>r.signalGrade===g).length);
  const crossCounts=['ema7_cross_25','ema7_cross_99','ema25_cross_99'].map(k=>scope.filter(r=>r.raw.emaObservation?.long_crosses?.[k]===true).length);
  return '本根技术交叉：7/25 '+crossCounts[0]+'、7/99 '+crossCounts[1]+'、25/99 '+crossCounts[2]+'；技术等级：弱 '+counts[0]+'、中 '+counts[1]+'、强 '+counts[2]+'。可调整左侧等级，或点上方“EMA金叉”单独查看。'+activeFilterSummary(scope);
 }
 return '此组合当前没有结果。0命中不代表没有采集，缺失与未接入仍保持未知。'+activeFilterSummary(scope);
}
function chips(){const out=[];const put=(text,k)=>out.push(`<span class="chip">${esc(text)}<button data-clear="${k}" aria-label="清除${esc(text)}">×</button></span>`);if(state.search)put('搜索 '+state.search,'search');if(state.market!=='all')put('市场 '+state.market.toUpperCase(),'market');if(state.cap!=='all')put('市值 '+$('cap').selectedOptions[0].text,'cap');if(state.trusted)put('仅已核对项目','trusted');if(state.history!=='all')put(state.history==='forward'?'开始监测后记录':'历史补算','history');if(state.context!=='latest')put('还原当时数据','context');if(state.listing!=='current')put('含历史合约','listing');for(const id of state.signals)put(lookup(id).num+' '+lookup(id).name+' · '+profile(id),'signal:'+id);$('chips').innerHTML=(state.signals.size?'<span class="muted">'+(state.mode==='and'?'全部命中':state.mode==='or'?'任一命中':'至少'+state.minimum+'项')+'：</span>':'')+(out.length?out.join(''):'<span class="muted">直接勾选左侧信号开始筛选；不勾选时显示范围内全部合约。</span>');}
function notice(text){clearTimeout(toastTimer);$('toast').textContent=text;$('toast').hidden=false;toastTimer=setTimeout(()=>$('toast').hidden=true,3200);}
function quick(k){if(k==='a')state.aStage='grade_all';if(k==='b'){state.bStage='cur_event';state.context='latest';state.history='all';}state.signals=new Set(k==='all'?[]:[k==='a'?'i4':k]);state.mode='and';state.result='pass';state.quick=k;$('comboPreset').value='custom';change();}
function reset(){Object.assign(state,{signals:new Set(),mode:'and',minimum:1,search:'',market:'all',cap:'all',minCap:'',maxCap:'',trusted:false,history:'all',context:'latest',listing:'current',aStage:'grade_all',bStage:'cur_event',bFrom:'',bTo:'',cStage:'confirm',reserveMode:'covered',reserveThreshold:1,result:'pass',sort:'symbol',desc:true,quick:'all'});Object.assign(thresholds,defaults);$('comboPreset').value='custom';$('moreSignals').open=false;history.replaceState(null,'',location.pathname);change();}
function settingsOpen(id){editing=id;const item=lookup(id);$('settingsTitle').textContent=item.num+' '+item.name;const options=id==='i4'?aOptions:id==='b'?bOptions:cOptions;let html='';if(['i4','b','c'].includes(id)){const field=id==='i4'?'aStage':id==='b'?'bStage':'cStage';html='<label class="field">查看哪个阶段<select id="configStage">'+(id==='i4'?aGradeOptions(rows.filter(r=>publicState(r)===true)):Object.entries(options).map(([k,v])=>`<option value="${k}" ${state[field]===k?'selected':''}>${v}</option>`).join(''))+'</select></label>';}
 const config={i1:[['tOi','OI / 市值大于（%）']],i2:[['tLsr','账户比小于']],i3:[['tApr','简单年化小于（%）']],i6:[['tVol','24H量倍数大于'],['tQuiet','同窗绝对价变小于（%）']]};
 if(config[id])html='<div class="field">'+config[id].map(([k,label])=>`<label>${label}<input type="number" id="config-${k}" data-threshold="${k}" value="${thresholds[k]}" step="any" ${k!=='tApr'?'min="0"':''}></label>`).join('')+'</div>';
 if(id==='i5')html='<p>使用已核验的聪明钱4H净流入 > 0。未覆盖仍按未知判断。</p>';
 if(['vol','spot','oi','warning'].includes(id))html='<p>当前使用合成观察标签演示筛选；正式阈值与数据源需在P6独立冻结，不在这里编造。</p>';
 if(id==='reserve')html='<label class="field">储备观察条件<select id="configReserveMode">'+Object.entries({covered:'近期已核验覆盖',day:'日余额减少',week:'7日余额减少',streak:'连续减少至少3天'}).map(([k,v])=>'<option value="'+k+'" '+(state.reserveMode===k?'selected':'')+'>'+v+'</option>').join('')+'</select></label><label class="field">日 / 7日减少幅度至少（%）<input type="number" id="configReserveThreshold" min="0" max="100" step="any" value="'+state.reserveThreshold+'"></label><p>余额变化是独立证据，不代表逐笔净流或聪明钱流入。</p>';
 html+='<p class="settings-note">应用后会启用本项筛选，保留其他已勾选信号；原始策略判定不变。'+(item.planning?' 本项为规划演示。':'')+'</p><p id="configError" class="field-error" role="alert"></p>';
 $('settingsBody').innerHTML=html;$('defaultSettings').disabled=['i5','vol','spot','oi','warning'].includes(id);$('signalSettings').showModal();}
function activateSignal(id){
 if(id&&lookup(id))state.signals.add(id);
 state.quick='custom';$('comboPreset').value='custom';
}
function activateInline(input){activateSignal(input.closest('[data-owner-signal]')?.dataset.ownerSignal);}
function applySettings(){const pending={};for(const input of $$('[data-threshold]')){const v=Number(input.value);if(!input.value||!Number.isFinite(v)||(input.dataset.threshold!=='tApr'&&v<0)){$('configError').textContent='请输入有效数值；除费率外不可小于0。';return;}pending[input.dataset.threshold]=v;}
 if(editing==='reserve'){const value=Number($('configReserveThreshold').value);if($('configReserveThreshold').value===''||!Number.isFinite(value)||value<0||value>100){$('configError').textContent='储备减少阈值须为0—100的有效百分比。';return;}state.reserveMode=$('configReserveMode').value;state.reserveThreshold=value;}
 Object.assign(thresholds,pending);if($('configStage'))state[editing==='i4'?'aStage':editing==='b'?'bStage':'cStage']=$('configStage').value;$('signalSettings').close();activateSignal(editing);change();}
function download(text,name,type='text/plain'){const url=URL.createObjectURL(new Blob(['\ufeff'+text],{type:type+';charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
for(const id of ['search','market','cap','minCap','maxCap','history','context','listing'])$(id).addEventListener(['search','minCap','maxCap'].includes(id)?'input':'change',e=>{state[id]=e.target.value;change();});
$('trusted').onchange=e=>{state.trusted=e.target.checked;change();};
$('minimum').oninput=e=>{state.minimum=Number(e.target.value);$('comboPreset').value='custom';change();};
$('comboPreset').onchange=e=>applyCombo(e.target.value);
host.addEventListener('change',e=>{const d=e.target.dataset;if(d.inlineStage){state[d.inlineStage]=e.target.value;activateInline(e.target);change();return;}if(d.inlineThreshold||d.inlineValue){const key=d.inlineThreshold||d.inlineValue,v=Number(e.target.value);if(e.target.value===''||!Number.isFinite(v)||(key!=='tApr'&&v<0)||(key==='reserveThreshold'&&v>100)){e.target.setCustomValidity('请输入有效范围内的数值');e.target.reportValidity();return;}e.target.setCustomValidity('');if(d.inlineThreshold)thresholds[key]=v;else state[key]=v;activateInline(e.target);change();return;}if(d.scope){state[d.scope]=d.scope==='trusted'?e.target.value==='true':e.target.value;change();return;}if(d.stage==='aStage'){state.aStage=e.target.value;activateSignal('i4');change();return;}if(d.signal){e.target.checked?state.signals.add(d.signal):state.signals.delete(d.signal);state.quick='custom';$('comboPreset').value='custom';change();}if(d.select){e.target.checked?state.selected.add(d.select):state.selected.delete(d.select);render();}});
host.addEventListener('click',e=>{const radio=e.target.closest('input[data-inline-stage]');if(radio&&radio.checked&&!state.signals.has(radio.closest('[data-owner-signal]')?.dataset.ownerSignal)){state[radio.dataset.inlineStage]=radio.value;activateInline(radio);change();return;}const el=e.target.closest('button');if(!el)return;const d=el.dataset;if(d.mode){state.mode=d.mode;$('comboPreset').value='custom';change();}else if(d.expandSignal){const id=d.expandSignal;expandedSignals.has(id)?expandedSignals.delete(id):expandedSignals.add(id);render();}else if(d.setting)settingsOpen(d.setting);else if(d.quick)quick(d.quick);else if(d.result){state.result=d.result;render();}else if(d.detail)openDetail(d.detail,d.tab||'all',d.event?{event:d.event,revision:d.revision}:null);else if(d.detailTab&&detailTarget)openDetail(detailTarget.k,d.detailTab,detailTarget.exact);else if(d.clear){if(d.clear.startsWith('signal:'))state.signals.delete(d.clear.slice(7));else if(d.clear==='trusted')state.trusted=false;else state[d.clear]=d.clear==='search'?'':d.clear==='context'?'latest':d.clear==='listing'?'current':'all';state.quick='custom';$('comboPreset').value='custom';change();}});
$('resetAll').onclick=reset;$('clearSignals').onclick=()=>{state.signals.clear();state.quick='all';$('comboPreset').value='custom';change();};
$('sort').onchange=e=>{state.sort=e.target.value;change();};$('direction').onclick=()=>{state.desc=!state.desc;change();};
$('selectAllResults').onchange=e=>{resultRows.forEach(r=>e.target.checked?state.selected.add(r.key):state.selected.delete(r.key));render();};$('clearSelected').onclick=()=>{state.selected.clear();render();};
$('export').onclick=()=>csvExport(visible,'统一信号-当前筛选.csv');$('exportSelected').onclick=()=>csvExport(rows.filter(r=>state.selected.has(r.key)),'统一信号-全部已选.csv');
$('mobileFilters').onclick=()=>{const open=$('rail').classList.toggle('open');$('mobileFilters').setAttribute('aria-expanded',String(open));$('mobileFilters').textContent=open?'收起信号筛选':'展开信号筛选';};
$('closeDetail').onclick=closeDetail;$('veil').onclick=closeDetail;$('closeSettings').onclick=()=>$('signalSettings').close();$('applySettings').onclick=applySettings;
$('defaultSettings').onclick=()=>{if(editing==='reserve'){$('configReserveMode').value='covered';$('configReserveThreshold').value=1;return;}const k=editing==='i4'?'aStage':editing==='b'?'bStage':'cStage';if(['i4','b','c'].includes(editing))$('configStage').value=editing==='i4'?'grade_all':editing==='b'?'cur_event':'confirm';$$('[data-threshold]').forEach(input=>input.value=defaults[input.dataset.threshold]);};
$('shareDetail').onclick=shareDetail;
$('makeReport').onclick=exportReport;
host.addEventListener('keydown',e=>{if(e.key==='Escape'){const adv=$('scopeAdv');if(adv&&adv.open){adv.open=false;const s=adv.querySelector('summary');if(s)s.focus();return;}}if($('drawer').hidden)return;if(e.key==='Escape')closeDetail();if(e.key==='Tab'){const focus=[...$('drawer').querySelectorAll('button,a[href],input,select,textarea,[tabindex="0"]')].filter(x=>!x.disabled&&!x.hidden&&x.getClientRects().length),first=focus[0],last=focus.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}});
window.addEventListener('hashchange',restoreHash);

const CUSTOM_COMBO_KEY='unified-workbench-combos-v33r5';
let customCombos=[];
try{const data=JSON.parse(localStorage.getItem(CUSTOM_COMBO_KEY)||'[]');if(Array.isArray(data))customCombos=data.filter(x=>x.version===COMBO_VERSION&&typeof x.name==='string'&&Array.isArray(x.signals)&&x.signals.every(id=>lookup(id))&&['and','or','n'].includes(x.mode)&&(Object.hasOwn(aOptions,x.aStage)||Object.hasOwn(legacyAOptions,x.aStage))&&bOptions[x.bStage]&&cOptions[x.cStage]&&Number.isInteger(x.minimum)&&x.minimum>=1&&Object.keys(defaults).every(k=>Number.isFinite(x.thresholds?.[k]))).slice(0,50);}catch{}
function combinationSnapshot(){return {schema:UnifiedCore.VERSION,rule_versions:{ui:'v3.3-r5',a:'v4-D10-D13',market_ema:UnifiedCore.EMA_GRADE_VERSION,b:'v3.2-r2-1h',frozen_registry_hash:payload?.registry?.frozen_hash},version:COMBO_VERSION,signals:[...state.signals],mode:state.mode,minimum:state.minimum,aStage:state.aStage,bStage:state.bStage,cStage:state.cStage,reserveMode:state.reserveMode,reserveThreshold:state.reserveThreshold,thresholds:{...thresholds}};}
function comboCounts(c){
 if(!payload||loadedContext!==state.context)return null;
 const query={...state,...c,signals:new Set(c.signals)},t={...defaults,...c.thresholds};
 if(core.validate(query,t))return {invalid:true};
 let pass=0,unknown=0;for(const row of rows){const v=core.evaluate(row,query,t);if(v===true)pass++;else if(v===null)unknown++;}
 return {pass,unknown};
}
function comboOption(c){
 const n=comboCounts(c),planning=c.signals.length&&c.signals.every(id=>lookup(id)?.planning);
 const suffix=planning?' · 未接入':!n?' · 读取中':n.invalid?' · 范围无效':' · 命中'+n.pass+(n.unknown?' / 待核'+n.unknown:'');
 const historicB=c.signals.includes('b')&&!String(c.bStage).startsWith('cur_');
 const label=historicB?(c.signals.length===1?'历史 · '+c.name:c.name+'（B按历史记录）'):c.name;
 return '<option value="'+esc(c.id)+'">'+esc(label)+suffix+'</option>';
}
function refreshCombos(selected='custom'){
 $('comboPreset').innerHTML='<option value="custom">自定义 / 不限</option>'+['推荐','更多观察','规划与未接入'].map(group=>'<optgroup label="'+group+'">'+COMBO_GUIDES.filter(c=>c.group===group&&c.available).map(comboOption).join('')+'</optgroup>').join('')+(customCombos.length?'<optgroup label="我的组合">'+customCombos.map(comboOption).join('')+'</optgroup>':'');$('comboPreset').value=selected;
}
function applyCombo(id){const c=COMBO_GUIDES.find(c=>c.id===id&&c.available)||customCombos.find(c=>c.id===id);if(!c){state.signals.clear();state.mode='and';}else{state.signals=new Set(c.signals);for(const k of ['mode','minimum','aStage','bStage','cStage'])state[k]=c[k];state.reserveMode=c.reserveMode||'covered';state.reserveThreshold=c.reserveThreshold??1;Object.assign(thresholds,defaults,c.thresholds);}state.result='pass';state.quick='custom';change();}
$('saveCombo')&&($('saveCombo').onclick=()=>{if(!state.signals.size){notice('请先勾选信号');return;}const current=customCombos.find(x=>x.id===$('comboPreset').value);$('comboName').value=current?current.name+' 副本':'';$('comboNotes').value='';$('saveComboError').textContent='';$('saveComboSummary').textContent=(state.mode==='and'?'全部命中':state.mode==='or'?'任一命中':'至少'+state.minimum+'项')+'：'+([...state.signals].map(id=>lookup(id).name+'（'+profile(id)+'）').join('＋')||'不限制信号');$('saveComboDialog').showModal();});
$('cancelSaveCombo').onclick=()=>$('saveComboDialog').close();
$('confirmSaveCombo').onclick=()=>{const validation=core.validate(state,thresholds);if(validation){$('saveComboError').textContent=validation;return;}const name=$('comboName').value.trim(),notes=$('comboNotes').value.trim();if(!name){$('saveComboError').textContent='请填写组合名称。';return;}if(customCombos.some(c=>c.name===name)||COMBO_GUIDES.some(c=>c.name===name)){$('saveComboError').textContent='名称已存在，请换一个名称；原组合不会被覆盖。';return;}if(customCombos.length>=50){$('saveComboError').textContent='当前最多保存50个组合。';return;}const c={...combinationSnapshot(),id:'my_'+Date.now()+'_'+Math.random().toString(36).slice(2,7),name,notes,created_at:new Date().toISOString()};try{localStorage.setItem(CUSTOM_COMBO_KEY,JSON.stringify([...customCombos,c]));}catch{$('saveComboError').textContent='浏览器无法保存，请导出筛选报告保留条件。';return;}customCombos.push(c);refreshCombos(c.id);updateComboHelp();$('saveComboDialog').close();notice('已保存，策略说明中同步显示用法');};


const core=UnifiedCore;
let refreshSequence=0,loadedContext=null,detailsScroll=0;
let loadTask=null,loadError=null;
const LOAD_TIMEOUT_MS=30000;
function loadMessage(){if(payload?.context===state.context&&loadError?.context===state.context)return payload.projection==='scan-only-v1'?'完整追踪读取暂不可用：正在显示已保存基础行情，B/C与A候选资格暂未核验。请稍后点“读取本地最新”。':'本次刷新失败，保留上次完整数据（'+date(payload.snapshot?.finishedAt)+'）。请点“读取本地最新”重试。';return loadError?.context===state.context?'保存数据读取失败：'+loadError.message+'。请点“重新读取”，不会重新扫描。':'正在读取已保存数据…';}

async function bootstrapSavedScan(){
 if(state.context!=='latest'||payload)return;
 const node=document.getElementById('snapshot-data');if(!node)return;
 const saved=JSON.parse(node.textContent);
 if(!saved.scan_id||!Array.isArray(saved.rows)||!saved.rows.length)return;
 const baseRows=saved.rows.map(row=>{
  const raw={...row};
  // The basic scan does not carry a complete, pinned tracking read view.
  for(const key of ['bEvents','bMonitoringEvidence','bMonitoringGap','trackingSummary','cCheck','emaSqueezeStage','confirmedEvents','crossConfirm','latestConfirm','reserveEvidence'])delete raw[key];
  raw.bMonitoring='unmonitored';raw.trackingSummary=[];raw.cCheck=null;raw.trackingEnabled=true;raw.trackingReadUnavailable=true;
  raw.legacySixStates=StrategyRegistry.signals(raw);
  raw.unifiedListing=raw.contractStatus&&raw.contractStatus!=='TRADING'?'history':'current';
  return raw;
 });
 const bytes=new TextEncoder().encode(JSON.stringify({scan_id:saved.scan_id,revision:saved.revision,rows:baseRows}));
 const digest=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(x=>x.toString(16).padStart(2,'0')).join('');
 payload={schema:core.VERSION,ui_version:'v3.3-r5',context:'latest',projection:'scan-only-v1',generation:'scan-only:'+digest,rows:baseRows,contract_count:baseRows.length,event_count:null,last_round:null,snapshot:{scan_id:saved.scan_id,revision:saved.revision,startedAt:saved.startedAt,finishedAt:saved.finishedAt,available_at:saved.available_at,registryHash:saved.registryHash},registry:{frozen_hash:StrategyRegistry.hash},read_status:'basic_scan_only',tracking:{enabled:true,state:'temporarily_unavailable'},simulation_enabled:false};
 loadedContext='latest';
 loadError={context:'latest',message:'完整追踪正在读取'};
}

const rawDefs=[["cap", "流通市值", "cap"], ["hits", "已选组合 / 六项统计", "hits"], ["oiUsd", "本合约 OI", "oiUsd"], ["oi", "① OI / 市值", "oiCapPct"], ["ratio", "② 全体账户比", "accountRatio"], ["funding", "③ 资金费率 / 结算周期", "fundingPct"], ["ema", "④ EMA金叉 · 4H", "grade"], ["flow", "⑤ 聪明钱4H流入", "smartMoneyNetInflowUsd"], ["vol", "⑥ 24H量倍数 / 同窗价变", "volumeRatio"], ["change", "24h涨跌", "change"], ["volume", "24h成交额", "volume24Usd"], ["b", "⑦ 吸筹背离 · 1小时", "btime"], ["c", "⑧ C超卖反弹 · 4H", null], ["tracking", "ABC持续观察", null], ["aux", "新增辅助观察 · 未接入", null], ["mark", "标记价格", "markPrice"], ["index", "指数价格", "indexPrice"], ["ema7", "EMA7 · 4H", "ema7"], ["ema25", "EMA25 · 4H", "ema25"], ["ema99", "EMA99 · 4H", "ema99"], ["closed", "已收盘4H价格", "closed4h"], ["assetOi", "同资产币安总OI", "binanceAssetOiUsd"], ["time", "观测时间", "asOf"], ["reserveDay", "币安余额日变", "reserveDayPct"], ["reserveWeek", "币安余额7日变", "reserveWeekPct"], ["reserveStreak", "币安余额连降天数", "reserveStreakDays"], ["histFlow", "历史净流样本（非当前⑤）", "histFlowUsd"], ["barVolumeRatio7", "4H前7根量比", "barVolumeRatio7"], ["priceChangeWindowPct", "24H同窗价变 %", "priceChangeWindowPct"], ["quality", "来源、质量与缺口", null]];
const defaultColumns=['cap','hits','oiUsd','oi','ratio','funding','ema','flow','vol','change','volume','b','c','tracking'];
const COLUMN_KEY='unified-columns-v33r5-integrity-v1';
const columnAliases={oiCapPct:'oi',accountRatio:'ratio',fundingAprPct:'funding',smartMoneyNetInflowUsd:'flow',volumeRatio:'vol',ema4h:'ema',asOf:'time'};
function cleanColumns(value){return [...new Set((value||[]).map(k=>columnAliases[k]||k).filter(k=>rawDefs.some(d=>d[0]===k)))];}
let rawCols=defaultColumns.slice();try{const saved=JSON.parse(localStorage.getItem(COLUMN_KEY)||'null');if(Array.isArray(saved))rawCols=cleanColumns(saved);else{const old=JSON.parse(localStorage.getItem('workbench-a-pro-columns-v4')||'null');if(Array.isArray(old)&&old.length)rawCols=cleanColumns([...old,'b']);}}catch{}
function saveColumns(){try{localStorage.setItem(COLUMN_KEY,JSON.stringify(rawCols));}catch{}}
function fieldContext(r){return {reserve:r.raw.reserveEvidence||null,history:typeof historicalFlowsByAsset!=='undefined'?historicalFlowsByAsset.get(r.asset)||null:null,exportContext:'unified-generation:'+payload?.generation};}
function dataCell(r,k,cells){const raw=r.raw,reserve=raw.reserveEvidence;
 if(k==='c')return cCell(r);
 if(k==='tracking'){
  if(raw.trackingReadUnavailable)return '<span class="muted">追踪更新中</span>';
  const names={observing:'观察中',paused_awaiting_data:'待补',ended:'已结束',confirmed:'已确认',waiting:'等待确认',expired:'已到期',invalidated:'周期失效',unknown:'待核验'};
  const items=raw.trackingSummary||[];
  return '<a href="/#tracking&market='+encodeURIComponent(raw.market)+'&symbol='+encodeURIComponent(raw.symbol)+'">'+(items.length?items.map(x=>esc(x.strategy+' '+(names[x.observation?.status]||names[x.original_confirmation?.technical_status]||'待核验')+(x.historical_import?' · 历史':''))).join('<br>'):state.context==='snapshot'?'快照未绑定追踪':raw.trackingEnabled?'尚无可得记录':'持续追踪未启用')+'</a>';
 }
 if(k==='cap')return cells.cap;
 if(k==='hits'){const f=core.signals(r,state,thresholds),screen=MarketEvidence.marketSignals(r.raw,r.e),chosen=[...state.signals];return (chosen.length?'<strong>'+chosen.filter(id=>f[id]===true).length+' / '+chosen.length+' 项</strong>':'<span class="muted">未限制组合信号</span>')+'<span class="secondary">六项：命中 '+screen.filter(x=>x===true).length+' / 可算 '+screen.filter(x=>x!==null).length+' / 未知 '+screen.filter(x=>x===null).length+'</span>';}
 const mapped={oi:'oi',ratio:'ratio',funding:'funding',ema:'ema',flow:'flow',vol:'volume',assetOi:'assetOi',histFlow:'flowHistory',quality:'risk',time:'sourceTimes'};
 if(mapped[k])return cells[mapped[k]]+(k==='time'?cells.times:'');
 if(k==='oiUsd'||k==='volume')return esc(money(raw[k==='oiUsd'?'oiUsd':'volume24Usd'])).replace('市值未知','未知');
 if(k==='change')return esc(rawValue(raw.priceChange24Pct))+'%';
 if(k==='reserveDay'||k==='reserveWeek')return reserve?esc(rawValue(reserve[k==='reserveDay'?'dayChangePct':'weekChangePct']))+'%<span class="secondary">'+esc(date(reserve.asOf*1000))+' · 独立余额证据</span>':'未核验覆盖';
 if(k==='reserveStreak')return reserve?esc(rawValue(reserve.consecutiveDecreaseDays))+' 天':'未知';
 if(['ema7','ema25','ema99'].includes(k))return esc(rawValue(raw.ema4h?.[{ema7:0,ema25:1,ema99:2}[k]]));
 if(k==='mark'||k==='index'||k==='closed')return esc(rawValue(raw[{mark:'markPrice',index:'indexPrice',closed:'closed4h'}[k]]));
 if(k==='b')return bCell(r);
 if(k==='aux')return '<button class="statusbtn" data-detail="'+esc(r.key)+'" data-tab="c">'+badge('规划／未接入','gray')+'</button>';
 return esc(rawValue(raw[k]));
}
function flags(r){return Object.values(core.signals(r,state,thresholds)).slice(0,6);}
function bState(r){return r.event&&state.history!=='all'&&(state.history==='history'?!r.event.historical:r.event.historical)?'outscope':r.b;}
function bViewHistory(){return state.signals.has('b')&&!state.bStage.startsWith('cur_');}
function bCell(r){
 if(r.raw.trackingReadUnavailable)return '<button class="statusbtn" data-detail="'+esc(r.key)+'" data-tab="b">'+badge('B追踪更新中','orange')+'</button>';
 const round=r.roundB,historyView=bViewHistory(),e=historyView?r.event:round.event;
 const code=historyView?r.b:round.state;
 const labels={complete:'吸筹背离已确认',pending:'放量价稳，等待确认',fail:'已放量；资格未通过（非完整确认）',confirmation_failed:'未通过后续确认',unknown:'数据不足，暂不能确认',insufficient:'数据不足，暂不能判断',unmonitored:'本轮未检查',none:'无新信号'};
 const color=code==='complete'?'green':code==='pending'?'blue':['fail','confirmation_failed','corrected'].includes(code)?'red':['unknown','insufficient','unmonitored','legacy'].includes(code)?'orange':'gray';
 const label=(historyView?'历史 · ':'')+(labels[code]||bLabel[code]||'无记录');
 let body=badge(label,color);
 if(e&&['complete','pending','fail','confirmation_failed','unknown','corrected','legacy'].includes(code))body+='<span class="secondary">触发：'+esc(hourWindow(e.trigger_open_time))+'</span>';
 if(code==='complete'&&e)body+='<span class="secondary">确认收盘：'+esc(shortDate(e.trigger_open_time+7200000))+'</span>';
 if(code==='pending'&&e)body+='<span class="secondary">等待 '+esc(shortDate(e.trigger_open_time+7200000))+' 收盘确认</span>';
 if(code==='fail'&&round.triggered&&!historyView)body+='<span class="secondary">已放量 '+esc(fmt(r.raw.bMonitoringEvidence?.event_ratio))+'倍；未通过完整条件</span>';
 if(['unknown','insufficient'].includes(code)&&e?.evidence_repair?.labels?.length)body+='<span class="secondary">'+esc(e.evidence_repair.labels.join('、'))+' · '+esc(({queued:'已排队补采',running:'正在补采',retry:'等待重试',blocked:'来源受限，将复核',resolved:'已处理'})[e.evidence_repair.state]||'待核对')+'</span>';
 if(historyView&&e)body+='<span class="secondary">'+(e.historical?'历史补算':'开始监测后记录')+'</span>';
 const last=round.lastConfirmed;
 if(!historyView&&last&&last.event_id!==e?.event_id)body+='<span class="secondary">上次信号（已确认）：'+esc(shortDate(last.trigger_open_time))+'</span>';
 return '<button class="statusbtn" data-detail="'+esc(r.key)+'" data-tab="b"'+(e?' data-event="'+esc(e.event_id)+'"':'')+'>'+body+'</button>';
}

// C uses the frozen C0/C1 stages; strong/medium/weak belong to A only.
function cPresentation(raw,context){
 const check=raw.cCheck,event=(raw.trackingSummary||[]).find(x=>x.strategy==='C');
 const boundary=Number.isFinite(raw.candleAsOf)?raw.candleAsOf+1:null;
 const same=boundary!==null&&check?.target_close_ms===boundary;
 const current=raw.trackingEnabled===true&&context!=='snapshot';
 const liveEvent=current&&boundary!==null&&event&&!event.historical_import&&event.decision_close_ms===boundary;
 const status=event?.original_confirmation?.technical_status,q=event?.qualification?.status;
 let label='本轮未检查',color='orange';
 if(context==='snapshot')label='快照未绑定C检查';
 else if(raw.trackingReadUnavailable)label='C追踪更新中';
 else if(raw.trackingEnabled!==true)label='C追踪未启用';
 else if(liveEvent){
  label=({confirmed:'C1 · 反弹已确认',waiting:'C0 · 超卖候选，等待反弹',expired:'C周期已到期',invalidated:'C周期已失效'})[status]||'C数据待核验';
  color=status==='confirmed'&&q==='pass'?'green':status==='waiting'&&q==='pass'?'blue':['expired','invalidated'].includes(status)?'gray':'orange';
  if(q!=='pass')label+=q==='fail'?' · 资格未通过':' · 资格待补齐';
 }else if(current&&same&&check.has_cycle===false){
  label=check.checks?.c0==='fail'?'无新信号':check.checks?.c0==='pass'?'C0条件满足 · 周期待核验':'数据不足，暂不能判断';
  color=check.checks?.c0==='fail'?'gray':'orange';
 }else if(current&&(check||event))label='本轮C状态待更新';
 const quality=same?check?.source_quality:null;
 const missing=Object.entries(quality||{}).filter(([,v])=>v?.first_gap_ms!=null).map(([k])=>({oi:'同期OI',lsr:'同期账户比',cap:'同期可信市值',candles_4h:'4H K线'})[k]||k);
 const checks=same&&check?.has_cycle===false?check.checks:null;
 const conditions=[['rsi','RSI14 < 25'],['below_all_ema','收盘价低于EMA7／25／99'],['oi_cap','同期OI／可信市值 > 30%'],['lsr','同期账户多空比 < 1.5'],['new_lows14','14根严格新低次数 ≤ 2']];
 const failed=conditions.filter(([k])=>checks?.[k]==='fail').map(([,label])=>label);
 const unknown=checks?conditions.filter(([k])=>!['pass','fail'].includes(checks[k])).map(([,label])=>label):[];
 return {label,color,boundary,same,liveEvent,event,check,checks,conditions,failed,unknown,missing};
}
function cCell(r){
 const p=cPresentation(r.raw,state.context);
 let body=badge(p.label,p.color);
 if(p.missing.length)body+='<span class="secondary">待补：'+esc(p.missing.join('、'))+'</span>';
 if(p.same)body+='<span class="secondary">4H收盘：'+esc(shortDate(p.boundary))+'</span>';
 if(p.event&&!p.liveEvent)body+='<span class="secondary">另有'+(p.event.historical_import?'历史':'以往')+'C记录 · '+esc(({confirmed:'已确认',waiting:'等待确认',expired:'已到期',invalidated:'已失效'})[p.event.original_confirmation?.technical_status]||'待核验')+'</span>';
 return '<button class="statusbtn" data-detail="'+esc(r.key)+'" data-tab="c">'+body+'</button>';
}
function cDetailsHtml(r){
 const p=cPresentation(r.raw,state.context),labels={pass:'满足',fail:'不满足',unknown:'缺数据／未核验'};
 let html='<p>'+badge(p.label,p.color)+'</p><p>C0为超卖候选；C1须在随后第1至第10根4H内出现阳线，且成交额严格大于前7根均值的3倍，并通过同期资格与失效检查。</p>';
 if(p.same)html+='<p>本次检查的4H收盘：'+esc(date(p.boundary))+'。</p>';
 else html+='<p>未取得与当前行情同一根4H的C检查，不能当作无信号。</p>';
 if(p.checks){
  html+='<table><thead><tr><th>C0条件</th><th>本根结果</th></tr></thead><tbody>'+p.conditions.map(([k,n])=>'<tr><td>'+esc(n)+'</td><td>'+esc(labels[p.checks[k]]||labels.unknown)+'</td></tr>').join('')+'</tbody></table>';
  if(p.failed.length)html+='<p>未触发原因：'+esc(p.failed.join('；'))+'不满足。</p>';
  if(p.unknown.length)html+='<p>尚未核验：'+esc(p.unknown.join('；'))+'。未触发不代表数据已齐全。</p>';
 }
 if(p.missing.length)html+='<p>缺少合格的同期来源：'+esc(p.missing.join('、'))+'。扫描时的最新值不能替代过去4H收盘时的证据。</p>';
 if(p.event){
  const e=p.event,o=e.observation||{};
  html+='<p>'+(p.liveEvent?'本根周期':'以往记录')+'：'+esc(({confirmed:'C1反弹已确认',waiting:'C0候选等待反弹',expired:'周期到期',invalidated:'周期失效'})[e.original_confirmation?.technical_status]||'待核验')+'；完整资格：'+esc(labels[e.qualification?.status]||labels.unknown)+'；后续观察：'+esc(({observing:'观察中',paused_awaiting_data:'等待补数',ended:'已结束'})[o.status]||'尚无观察')+'。</p><p>历史记录不作为本轮新信号，原确认与后续观察分别保存。</p>';
 }
 return html+jsonBlock('C已保存的检查、周期与来源依据',{check:p.check,event:p.event});
}

function hourWindow(v){if(v==null)return '未知';const d=new Date(v+3600000);return shortDate(v)+'—'+d.toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false});}
function renderBView(){
 const historyView=bViewHistory();
 const range=$('bHistoryDates');if(range)range.hidden=!historyView;for(const key of ['bFrom','bTo']){const el=$(key);if(el)el.value=state[key];}
 $$('[data-b-view]').forEach(el=>{const active=(el.dataset.bView==='history')===historyView;el.classList.toggle('active',active);el.setAttribute('aria-pressed',String(active));});
 const t=payload?.last_round?.target_close_boundary;
 const hint=$('bViewHint');if(hint)hint.textContent=historyView?'历史查询：按触发日期查看过去记录，不代表本轮新信号。':t?'最近扫描检查：'+hourWindow(t-3600000)+' · 北京时间'+(Date.now()>t+3600000?' · 尚未更新至当前小时':''):'未取得本轮检查时间';
}
function repairSummary(e){const r=e?.evidence_repair;if(!r)return '<p>补采：暂无记录</p>';const label=({queued:'已排队补采',running:'正在补采',retry:'等待重试',blocked:'数据来源受限，将定期复核',resolved:'已处理',not_queued:'等待登记补采'})[r.state]||'等待核对';return '<p>数据补齐：'+esc(label)+(r.labels?.length?'；'+esc(r.labels.join('、')):'')+(r.next_retry_at?'；下次重试或复核 '+esc(date(r.next_retry_at)):'')+'</p>';}
function time(r){return date(r.btime);}
function shortDate(v){const d=new Date(v);return Number.isFinite(d.valueOf())?d.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):'时间未知';}
function date(v){if(v==null)return '未取得';const d=new Date(v);return Number.isFinite(d.valueOf())?d.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})+' 北京时间':'时间未核验';}
function publicState(r){return core.scope(r,state);}
function signalValue(r,id){return core.signals(r,state,thresholds)[id]??null;}
function combine(v){return core.combine(v,state.mode,state.minimum);}
function evaluation(r){return core.evaluate(r,state,thresholds);}
function filtered(){return rows.filter(r=>publicState(r)!==false&&(state.result==='selected'?state.selected.has(r.key):resultKey(evaluation(r))===state.result));}
function sortData(list){return core.sort(list,state,thresholds);}
function change(){render();if(state.context!==loadedContext)reload().catch(e=>notice(e.message));}
const triLabels={pass:'通过',fail:'不通过',unknown:'待核验',pending:'待确认',none:'无记录',active:'活跃',confirmed:'已确认',exited:'已退出',strong:'强',medium:'中',weak:'弱'};
function rawValue(v){if(typeof v==='string'&&triLabels[v])return triLabels[v];return v==null?'未知':typeof v==='number'?fmt(v,6):typeof v==='object'?JSON.stringify(v):String(v);}
function rowHtml(r){const cells=MarketEvidence.cells(r.raw,MarketEvidence.marketSignals(r.raw,r.e),{...fieldContext(r),compact:true});return '<tr data-contract-key="'+esc(r.key)+'"><td><input type="checkbox" data-select="'+esc(r.key)+'" '+(state.selected.has(r.key)?'checked':'')+' aria-label="选择'+esc(r.raw.market+' '+r.symbol)+'"></td><td><button class="link contract" data-detail="'+esc(r.key)+'">'+esc(r.symbol)+'</button><span class="secondary">'+esc(rawValue(r.price))+(r.raw.quote?' '+esc(r.raw.quote):'')+(r.listing==='history'?' · 历史合约':'')+'</span></td>'+rawCols.map(k=>'<td data-raw-cell="'+k+'"'+(k==='ema'?' class="ema-cell"':'')+'>'+dataCell(r,k,cells)+'</td>').join('')+'</tr>';}
function render(){
 const focused=document.activeElement;
 const focusData=focused&&host.contains(focused)?{...focused.dataset,value:focused.value}:null;
 if(payload)rows=payload.rows.map(r=>core.adapt(r,state.history,state.context==='latest'?payload?.last_round?.target_close_boundary:null,bViewHistory()?{from:state.bFrom,to:state.bTo}:{}));
 for(const id of ['search','market','cap','minCap','maxCap','trusted','history','context','listing'])$(id).value=state[id];
 $('trusted').checked=state.trusted;$('customCap').hidden=state.cap!=='custom';$('sort').value=state.sort;$('direction').textContent=state.desc?'↓ 降序':'↑ 升序';
 const error=core.validate(state,thresholds),contextPending=loadedContext!==state.context;
 let errorNode=$('queryError');if(!errorNode){errorNode=document.createElement('p');errorNode.id='u-queryError';errorNode.className='u-error';$('topFilters').append(errorNode);}const refreshIssue=loadError?.context===state.context;errorNode.hidden=!error&&!contextPending&&!refreshIssue;errorNode.textContent=error||((contextPending||refreshIssue)?loadMessage():'');
 const scope=rows.filter(r=>publicState(r)===true),unknownScope=rows.filter(r=>publicState(r)===null).length;
 refreshCombos($('comboPreset').value||'custom');
 $('signalList').innerHTML=catalog.slice(0,8).map(x=>pickerHtml(x,scope)).join('');$('extraSignalList').innerHTML=catalog.slice(8).map(x=>pickerHtml(x,scope)).join('');
 const n=state.signals.size;$('signalSelectionCount').textContent=n?'已勾选 '+n+' 个信号':'未选择 · 不限制信号';const nWrapEl=$('nWrap');const nOn=state.mode==='n';nWrapEl.hidden=false;nWrapEl.classList.toggle('is-slot-hidden',!nOn);nWrapEl.setAttribute('aria-hidden',nOn?'false':'true');const minEl=$('minimum');if(minEl){minEl.tabIndex=nOn?0:-1;if(!nOn)minEl.blur();}$('minimum').value=state.minimum;$('minimum').max=n||1;$('minimum').disabled=!n;
 $('modeDescription').textContent=!n?'不勾选时，不限制信号。':state.mode==='and'?'同时命中这 '+n+' 个信号。':state.mode==='or'?'命中其中任何一个信号即可。':'至少命中 '+state.minimum+' 项。';
 $$('[data-mode]').forEach(e=>e.classList.toggle('active',state.mode===e.dataset.mode));$$('[data-result]').forEach(e=>e.classList.toggle('active',state.result===e.dataset.result));$$('[data-quick]').forEach(e=>e.classList.toggle('active',state.quick===e.dataset.quick));
 const outcomes=rows.filter(r=>publicState(r)!==false).map(evaluation);
 $('scopeCount').textContent=payload?scope.length:'—';$('scopeCount').title='另有'+unknownScope+'份公共范围待核验';$('passCount').textContent=error||contextPending?'—':outcomes.filter(x=>x===true).length;$('unknownCount').textContent=error||contextPending?'—':outcomes.filter(x=>x===null).length;$('failCount').textContent=error||contextPending?'—':outcomes.filter(x=>x===false).length;
 $('scopeCount').closest('.stat').querySelector('.label').textContent=n?'范围内合约':'显示范围内全部合约';
 $('passCount').closest('.stat').hidden=!n;$('failCount').closest('.stat').hidden=!n;
 visible=error||contextPending?[]:sortData(filtered());resultRows=visible;
 $('rows').innerHTML=visible.length?visible.map(rowHtml).join(''):'<tr><td colspan="'+(2+rawCols.length)+'" class="empty">'+esc(error||(contextPending?loadMessage():emptyResultMessage(scope)))+(contextPending&&loadError?.context===state.context?' <button data-load-retry>重新读取</button>':'')+'</td></tr>';
 // All export/report consumers use this exact generation and current full query result.
 currentRows=visible.map(r=>r.raw);pageRows=currentRows.slice();filteredForReports=currentRows.slice();
 // Evidence is created only for explicit detail/export/research, not every hidden row redraw.
 $('resultSummary').textContent='当前'+({pass:'命中',unknown:'待核验',fail:'未命中',selected:'已选'}[state.result])+' '+visible.length+' 份合约 · 全部显示，无分页';
 $('selectAllResults').checked=!!visible.length&&visible.every(r=>state.selected.has(r.key));$('selectAllResults').indeterminate=visible.some(r=>state.selected.has(r.key))&&!$('selectAllResults').checked;
 $('selectedBadge').textContent=state.selected.size;const keys=new Set(visible.map(r=>r.key)),inside=[...state.selected].filter(k=>keys.has(k)).length;$('selectedCount').textContent='总已选 '+state.selected.size+' 份 · 当前结果内 '+inside+' · 结果外 '+(state.selected.size-inside);
  /* 1006: snapshot time moved to #u-scopeSummary via syncScope */
 $('coverage').textContent=payload?.projection==='scan-only-v1'?'已显示 '+rows.length+' 份基础行情 · 完整追踪更新中':'已保存 '+(payload?.event_count??'—')+' 条吸筹背离记录 · '+rows.length+' 份合约';$('clock').textContent='时间均为北京时间 · 显示已保存数据';
 $('rawColumns').innerHTML=rawDefs.map(([k,n])=>'<label><input type="checkbox" data-raw-column="'+k+'" '+(rawCols.includes(k)?'checked':'')+'>'+n+'</label>').join('');
 const head=$('rows').closest('table').querySelector('thead tr');head.querySelectorAll('[data-raw-head]').forEach(e=>e.remove());for(const k of rawCols){const def=rawDefs.find(x=>x[0]===k);head.insertAdjacentHTML('beforeend','<th data-raw-head="'+k+'">'+(def[2]?'<button data-sort-field="'+def[2]+'">'+def[1]+' ↕</button>':def[1])+'</th>');}
 syncScope();chips();renderBView();updateComboHelp();try{sessionStorage.setItem('unified-last-query-v33r5',JSON.stringify(serialize()));}catch{}
 if(focusData&&!focused.isConnected){const entries=Object.entries(focusData).filter(([key])=>key!=='value');const replacement=[...host.querySelectorAll('input,button')].find(el=>entries.length&&entries.every(([k,v])=>el.dataset[k]===v)&&(!('inlineStage' in focusData)||el.value===focusData.value));replacement?.focus({preventScroll:true});}
 if(typeof updateReportSelection==='function'){try{updateReportSelection();}catch(e){console.warn('研究选币同步失败；保存行情仍可独立加载',e);}}
 window.dispatchEvent(new CustomEvent('workbench-selection-changed'));
}
function updateComboHelp(){const id=$('comboPreset').value,url='/unified-strategies.html?filters='+encodeURIComponent(JSON.stringify(serialize()));const help=$('comboHelp');if(help)help.href=url+(id&&id!=='custom'?'#'+encodeURIComponent(id):'');const nav=$('strategiesNav');if(nav)nav.href=url;}
function serialize(){return core.snapshot(state,thresholds);}
function reload({force=false}={}){
 const context=state.context;
 // Snapshot and B notifications can arrive together during startup. Never abort
 // a same-context body transfer: coalesce notifications and refresh once after it.
 if(loadTask?.context===context){if(force)loadTask.refreshPending=true;return loadTask.promise;}
 if(loadTask)loadTask.controller.abort();
 const seq=++refreshSequence,controller=new AbortController();
 loadedContext=payload?.context===context?context:null;loadError=payload?.projection==='scan-only-v1'?{context,message:'完整追踪正在读取'}:null;
 const task={context,controller,promise:null,refreshPending:false,completed:false};loadTask=task;
 task.promise=(async()=>{
  let timer;
  const timeout=new Promise((_,reject)=>{timer=setTimeout(()=>{reject(Error('超过30秒仍未取得完整响应'));controller.abort();},LOAD_TIMEOUT_MS);});
  try{
   const started=performance.now();const trace=stage=>console.info('[unified-load]',stage,Math.round(performance.now()-started)+'ms');trace('fetch-start');
   // Include body transfer and JSON decoding in the deadline, not only response headers.
   const read=(async()=>{
    const response=await fetch('/api/unified/signals?projection=summary-v1&context='+encodeURIComponent(context)+'&read='+Date.now()+'-'+seq,{cache:'no-store',signal:controller.signal});
    if(!response.ok)throw Error('服务返回HTTP '+response.status);
    trace('headers '+response.status);
    const text=await response.text();trace('body '+text.length);
    const next=JSON.parse(text);trace('json');
    if(next.context!==context||!Array.isArray(next.rows)||!next.generation)throw Error('返回的数据上下文或格式不完整');
    return next;
   })();
   const next=await Promise.race([read,timeout]);
   if(seq!==refreshSequence)return;
   payload=next;loadedContext=context;loadError=null;
   trace('render-start');render();trace('render-end');renderBArchive();trace('archive-end');task.completed=true;
  }catch(e){
   if(seq!==refreshSequence)return;
   loadedContext=payload?.context===context?context:null;loadError={context,message:e.message||'连接中断'};
   try{render();}catch(renderError){console.error(renderError);const target=$('queryError');if(target){target.hidden=false;target.textContent=loadMessage();}}
   throw e;
  }finally{
   clearTimeout(timer);
   if(seq===refreshSequence){loadTask=null;if(task.refreshPending)queueMicrotask(()=>{if(!loadTask&&state.context===context)reload().catch(e=>notice(e.message));});}
  }
 })();
 return task.promise;
}
async function prepareExport(data){if(payload?.projection==='scan-only-v1')throw Error('当前仅有基础行情；完整追踪更新中，请稍后读取本地最新再查看完整依据或导出');if(core.validate(state,thresholds)||loadedContext!==state.context||!payload)throw Error('条件无效或上下文正在切换，未导出');if(!data.length)return [];const query=JSON.stringify(serialize()),generation=payload.generation,keys=data.map(r=>r.key);if(keys.length<=8){for(const key of keys)await hydrateContract(key);}else await hydrateFull();if(query!==JSON.stringify(serialize())||payload?.generation!==generation||loadedContext!==state.context)throw Error('导出期间筛选条件或数据已变化，请按当前结果重新导出');const current=keys.map(key=>rows.find(r=>r.key===key));if(current.some(r=>!r))throw Error('导出合约已变化，请重新读取后重试');return current;}
async function csvExport(data,name){try{data=await prepareExport(data);if(!data.length)return notice('当前没有合约');download(core.csv(data.map(r=>core.evidence({...r,raw:{...r.raw,historicalFlowEvidence:fieldContext(r).history}},state,thresholds,payload))),name,'text/csv');notice('已导出 '+data.length+' 份合约及同批证据');}catch(e){notice(e.message);}}
async function exportReport(){try{const current=await prepareExport(visible);if(!current.length)return notice('当前没有结果');const data=current.map(r=>core.evidence(r,state,thresholds,payload));download(JSON.stringify({title:'统一信号工作台数据报告',schema:core.VERSION,generation:payload.generation,query:serialize(),contract_count:data.length,event_count:data.filter(r=>r.B_event_id).length,records:data},null,2),'统一信号-筛选数据报告.json','application/json');}catch(e){notice(e.message);}}
function bQualificationHtml(e){
 if(!e)return '';
 return '<p>强度：按原B规则仅完整确认构成有效信号，尚未定义弱／中／强。</p>'+[['触发小时',e.structure_h],['确认小时',e.structure_confirmation]].map(([name,x])=>'<p>'+name+'资格：OI / 市值 '+fmt(x?.oi_cap_pct)+'%（须＞30%）；账户多空比 '+fmt(x?.lsr,4)+'（须＜0.80）；结果 '+esc(({pass:'通过',fail:'未通过',unknown:'缺同期证据'})[x?.state]||'待核验')+'。</p>').join('');
}
function jsonBlock(label,value){return '<details><summary>'+esc(label)+'</summary><pre class="raw-json">'+esc(JSON.stringify(value??null,null,2))+'</pre></details>';}
let detailRequest=0,hydration=null;
async function hydrateFull(){if(payload?.projection==='scan-only-v1')throw Error('当前仅有基础行情；完整追踪更新中，请稍后读取本地最新再查看完整依据或导出');if(!payload||loadedContext!==state.context)throw Error('请先完成当前数据读取');if(payload.projection==='full')return;const generation=payload.generation,context=state.context;if(hydration?.generation===generation)return hydration.promise;const promise=(async()=>{notice('正在读取完整事件证据…');const response=await fetch('/api/unified/signals?context='+encodeURIComponent(context),{cache:'no-store'});if(!response.ok)throw Error('完整证据读取失败，请重试');const full=await response.json();if(full.generation!==generation||payload?.generation!==generation||state.context!==context)throw Error('数据代次已变化，请重新读取后重试');payload=full;render();})();hydration={generation,promise};try{await promise;}finally{if(hydration?.promise===promise)hydration=null;}}
async function hydrateContract(key,{signal=null,valid=()=>true}={}){
 if(payload?.projection==='scan-only-v1')throw Error('当前仅有基础行情；完整追踪更新中，请稍后重试');
 if(!payload||loadedContext!==state.context)throw Error('请先完成当前数据读取');
 const row=payload.rows.find(x=>core.adapt(x,state.history).key===key);if(!row)throw Error('目标精确合约不存在');
 if(row.full_evidence_loaded)return;
 const generation=payload.generation,context=state.context,controller=new AbortController();
 let timedOut=false;const cancel=()=>controller.abort();
 if(signal?.aborted)cancel();else signal?.addEventListener('abort',cancel,{once:true});
 const timer=setTimeout(()=>{timedOut=true;controller.abort();},10000);
 try{
  const response=await fetch('/api/unified/signals?context='+encodeURIComponent(context)+'&market='+encodeURIComponent(row.market)+'&symbol='+encodeURIComponent(row.symbol),{cache:'no-store',signal:controller.signal});
  if(!response.ok)throw Error(response.status===503?'证据正在发布，请重试':'合约证据读取失败（'+response.status+'），请重试');
  const full=await response.json();
  if(controller.signal.aborted||!valid())throw new DOMException('详情请求已取消','AbortError');
  if(full.generation!==generation||payload?.generation!==generation||state.context!==context)throw Error('数据已更新，请读取本地最新后重试；现有摘要已保留');
  const exact=full.rows.find(x=>x.market===row.market&&x.symbol===row.symbol);if(!exact)throw Error('未返回目标合约，已保留原合约摘要');
  payload.rows=payload.rows.map(x=>x===row?{...exact,full_evidence_loaded:true}:x);render();
 }catch(e){if(timedOut)throw Error('完整详情读取超时，请重试');throw e;}
 finally{clearTimeout(timer);signal?.removeEventListener('abort',cancel);}
}
let detailController=null,detailTarget=null;
function detailStatus(message,retry=false){
 let el=$('drawer').querySelector('[data-detail-load]');if(!el){el=document.createElement('p');el.dataset.detailLoad='';el.setAttribute('role','status');$('detailBody').prepend(el);}
 el.textContent=message;if(retry){const button=document.createElement('button');button.dataset.detailRetry='';button.textContent='重试完整详情';el.append(' ',button);}
}
async function openDetail(k,tab='all',exact=null){
 const request=++detailRequest;detailController?.abort();const controller=detailController=new AbortController();
 detailTarget={k,tab,exact};const initial=rows.find(x=>x.key===k);if(!initial){showEmptyDetail('目标精确合约不存在');return;}
 if($('drawer').hidden){lastFocus=document.activeElement;detailsScroll=window.scrollY;}
 $('drawer').hidden=false;$('veil').hidden=false;$('detailTitle').textContent=initial.symbol;
 $('detailIdentity').textContent=initial.raw.market+' · 北京时间';
 try{renderDetail(k,tab,exact,true);}catch(e){$('detailBody').textContent='已有摘要：'+initial.symbol+'；价格 '+fmt(initial.price,8);console.error(e);}
 detailStatus('正在读取完整证据；当前显示已保存摘要，资格与完整记录以读取结果为准。');
 $('closeDetail').focus({preventScroll:true});
 try{
  await hydrateContract(k,{signal:controller.signal,valid:()=>request===detailRequest});
  if(request!==detailRequest||controller.signal.aborted)return;
  renderDetail(k,tab,exact,false);detailStatus('完整证据已读取。');
 }catch(e){if(request===detailRequest&&!controller.signal.aborted)detailStatus(e.message,true);}
}
host.addEventListener('click',e=>{if(e.target.closest('[data-detail-retry]')&&detailTarget){const {k,tab,exact}=detailTarget;openDetail(k,tab,exact);}});
function renderDetail(k,tab='all',exact=null,loading=false){
 const r=rows.find(x=>x.key===k);if(!r)return;
 let e=bViewHistory()?r.event:r.roundB.event;if(exact&&!loading){const current=(r.raw.bEvents||[]).find(x=>x.event_id===exact.event);if(!current){showEmptyDetail('该事件不存在或在此上下文尚不可得');return;}e=current;if(exact.revision&&Number(exact.revision)!==Number(e.event_revision)){e=(current.revisions||[]).map(x=>x.snapshot).find(x=>Number(x.event_revision)===Number(exact.revision));if(!e){showEmptyDetail('请求的事件修订不存在');return;}}}
 detail={key:k,tab,event:e?.event_id??null,revision:e?.event_revision??null};
 $('drawer').hidden=false;$('veil').hidden=false;$('detailTitle').textContent=r.symbol;$('detailIdentity').textContent=r.raw.market+' · '+(r.raw.quote||'报价币未知')+' · 北京时间';
 const data=core.evidence({...r,event:e,b:e?core.bstatus(e):r.b},state,thresholds,payload),st=r.raw.emaSqueezeStage;
 const cells=MarketEvidence.cells(r.raw,MarketEvidence.marketSignals(r.raw,r.e),fieldContext(r));
 const defs=[['oi','① 杠杆密度'],['ratio','② 全体账户比'],['funding','③ 资金费率与结算周期'],['ema','④ EMA金叉'],['flow','⑤ 聪明钱4H流入'],['volume','⑥ 独立24H量增价平']];
 $('detailBody').innerHTML='<p class="muted">行情保存时间：'+esc(date(r.raw.asOf))+'。历史信号另标触发时间，需与本轮状态分别查看。</p><div class="detailtabs">'+[['all','全部依据'],['a','EMA三线'],['b','放量价稳（吸筹背离）'],['c','C超卖反弹']].map(([id,n])=>'<button data-detail-tab="'+id+'" class="'+(tab===id?'active':'')+'">'+n+'</button>').join('')+'</div>'+
 (tab==='all'?'<section class="detailpanel"><header>六项原始依据</header><table>'+defs.map(([id,n])=>'<tr><td>'+n+'</td><td>'+cells[id]+'</td></tr>').join('')+'</table><p class="detailnotice">来源 '+esc(r.raw.source||'未知')+'；质量 '+esc(r.raw.quality||r.raw.capQualityStatus||'逐字段核验')+'；缺口 '+esc((r.raw.missing||[]).join('；')||JSON.stringify(r.raw.errors||{}))+'</p>'+jsonBlock('源时间、取得／可得时间与完整原值',r.raw)+'</section>':'')+
 (tab==='a'?'<section class="detailpanel"><header>EMA金叉 · 当前4H技术等级；原A资格与确认单独查看</header><div style="padding:12px">'+MarketEvidence.emaSummaryHtml(r.raw)+MarketEvidence.observationDetailsHtml(r.raw)+MarketEvidence.confirmEventDetailsHtml(r.raw)+'<p>原A策略等级（非本页技术分级） '+esc(rawValue(r.grade))+'；候选状态 '+esc(rawValue(r.a))+'。</p><p>该合约持续观察：'+dataCell(r,'tracking',cells)+'</p>'+jsonBlock('A候选、周期确认、等待放量及事件身份',st)+jsonBlock('EMA／量比／连续根与前后根观察',{ema:r.raw.ema4h,close:r.raw.closed4h,volume7:r.raw.barVolumeRatio7,observation:r.raw.emaObservation,quality:r.raw.emaQuality,candleAsOf:r.raw.candleAsOf})+'</div></section>':'')+
 (tab==='b'?'<section class="detailpanel"><header>放量价稳（吸筹背离）· 1小时</header><div style="padding:12px"><p>某一小时成交额达到前168小时平均的8倍或以上，价格涨跌幅绝对值小于3%；紧接的一小时成交额仍达到其前168小时平均的2倍或以上。两个小时均需满足4小时EMA7高于EMA25、OI/市值大于30%、全体账户多空比小于0.80，才显示已确认。这是量价与持仓条件信号，不代表已证实庄家买入。</p><p>本轮结果：'+esc(({none:'无新信号',complete:'吸筹背离已确认',pending:'放量价稳，等待确认',fail:'已放量；资格未通过（非完整确认）',confirmation_failed:'未通过后续确认',unknown:'数据不足，暂不能确认',insufficient:'数据不足，暂不能判断',unmonitored:'本轮未检查'})[r.roundB.state]||'未知')+'</p><p>所选记录：'+esc(e?bLabel[core.bstatus(e)]||'未核验':({none:'本轮无新信号',unmonitored:'本轮未检查',insufficient:'本轮数据不足'})[r.roundB.state]||'本轮无可得信号')+'</p><p>量价与趋势确认 '+esc(rawValue(e?.technical_confirmation))+'；当时持仓与多空比条件 '+esc(rawValue(e?.structure_gate))+'；风险检查（参考价格／发现价格） '+esc(rawValue(e?.risk_theory)+' / '+rawValue(e?.risk_observed))+'</p><p>事件 '+esc(e?.event_id||'无')+' · 记录版本 '+esc(e?.event_revision??'未知')+'</p><p>触发小时 '+esc(e?hourWindow(e.trigger_open_time):'无')+(e?('；确认小时 '+hourWindow(e.trigger_open_time+3600000)):'')+'；首次发现 '+esc(date(e?.first_seen_at))+'；系统首次发现技术确认 '+esc(date(e?.confirmed_first_seen_at))+'</p><p>理论开盘 '+esc(rawValue(e?.P_theory))+'；发现价格 '+esc(rawValue(e?.P_observed))+'；止损参考价 '+esc(rawValue(e?.S0))+'。仅观察，不产生模拟或真实成交。</p>'+bQualificationHtml(e)+jsonBlock('触发小时对应的4小时趋势数据',e?.bg4h)+jsonBlock('确认小时对应的4小时趋势数据',e?.bg4h_h1)+repairSummary(e)+jsonBlock('本轮触发与确认的完整记录',r.roundB)+jsonBlock('补采技术记录',e?.evidence_repair)+jsonBlock('选定事件完整证据',e)+eventListHtml(r)+jsonBlock('同合约全部事件（最新优先）',r.raw.bEvents||[])+'</div></section>':'')+
 (['all','c','aux'].includes(tab)?'<section class="detailpanel"><header>C与新增辅助</header><div style="padding:12px">'+cDetailsHtml(r)+'<p>Vol/OI、合约/现货、OI背离与出货预警：规划／未接入，保持未知。</p>'+jsonBlock('独立储备证据',r.raw.reserveEvidence)+'</div></section>':'')+jsonBlock('当前组合、分母与版本',data);
 $('closeDetail').focus({preventScroll:true});
}
function showEmptyDetail(message){detail=null;$('drawer').hidden=false;$('veil').hidden=false;$('detailTitle').textContent='定位结果为空';$('detailIdentity').textContent='未切换到其他合约';$('detailBody').textContent=message;}
function closeDetail(){detailRequest++;detailController?.abort();detailController=null;detailTarget=null;detail=null;$('drawer').hidden=true;$('veil').hidden=true;if(lastFocus?.isConnected)lastFocus.focus({preventScroll:true});window.scrollTo(0,detailsScroll);}
async function shareDetail(){if(!detail)return notice('没有可分享的事件');const r=rows.find(x=>x.key===detail.key);const u=new URL(location.href);u.hash=new URLSearchParams({market:r.raw.market,symbol:r.symbol,tab:detail.tab,event:detail.event||'',revision:detail.revision||'',filters:JSON.stringify(serialize())}).toString();try{await navigator.clipboard.writeText(u.href);notice('已复制精确定位链接');}catch{history.replaceState(null,'',u);notice('地址已更新，可复制地址');}}
function restoreHash(){try{const p=new URLSearchParams(location.hash.slice(1));const stored=p.get('filters')||(!location.hash?startupSavedQuery:null);startupSavedQuery=null;if(stored){
  const d=JSON.parse(stored);if(!d||d.schema!==core.VERSION){notice('旧版组合待复核，请重新选择；未自动映射');return;}
  // Validate the whole query before committing; rejected links must not widen filters.
  const next={...state,signals:new Set(state.signals)},nextThresholds={...thresholds};
  if(Object.hasOwn(d,'signals')){if(!Array.isArray(d.signals)||d.signals.some(id=>!lookup(id)))throw Error('invalid signals');next.signals=new Set(d.signals);}
  for(const k of ['search','market','cap','minCap','maxCap','trusted','history','context','listing','aStage','bStage','bFrom','bTo','cStage','mode','result','sort','desc','minimum','reserveMode','reserveThreshold'])if(Object.hasOwn(d,k)){if(typeof d[k]!==typeof state[k])throw Error('invalid query type');next[k]=d[k];}
  if(Object.hasOwn(d,'thresholds')){if(!d.thresholds||typeof d.thresholds!=='object')throw Error('invalid thresholds');for(const k in defaults)if(Object.hasOwn(d.thresholds,k)){if(!Number.isFinite(d.thresholds[k]))throw Error('invalid threshold');nextThresholds[k]=d.thresholds[k];}}
  if(core.validate(next,nextThresholds))throw Error('invalid query');
  Object.assign(state,next);Object.assign(thresholds,nextThresholds);
 }
 const locate=()=>{const q=new URLSearchParams(location.search);const market=p.get('market')||q.get('market'),symbol=p.get('symbol')||q.get('symbol'),event=p.get('event')||q.get('event_id'),revision=p.get('revision')||q.get('revision');if(market&&symbol){const r=rows.find(r=>r.raw.market===market&&r.symbol===symbol);if(!r){showEmptyDetail('目标精确合约不存在');return;}openDetail(r.key,p.get('tab')||'b',event?{event,revision}:null);}else if(event){const r=rows.find(r=>(r.raw.bEvents||[]).some(e=>e.event_id===event));if(r)openDetail(r.key,'b',{event,revision});else showEmptyDetail('目标事件不存在');}else if(symbol||market)showEmptyDetail('缺少完整market+symbol身份，未猜测目标');};
 if(state.context!==loadedContext)reload().then(locate).catch(e=>notice(e.message));else{render();locate();}
 }catch{notice('链接条件无效，未应用旧版或损坏条件');}}
$('reloadSaved').onclick=()=>reload().catch(e=>notice(e.message));
host.addEventListener('click',e=>{if(e.target.closest('[data-load-retry]'))reload().catch(e=>notice(e.message));});
host.addEventListener('change',e=>{if(e.target.dataset.rawColumn){rawCols=[...$('rawColumns').querySelectorAll(':checked')].map(x=>x.dataset.rawColumn);saveColumns();render();}});
host.addEventListener('click',e=>{const b=e.target.closest('[data-sort-field]');if(b){state.desc=state.sort===b.dataset.sortField?!state.desc:false;state.sort=b.dataset.sortField;if(![...$('sort').options].some(x=>x.value===state.sort))$('sort').add(new Option(rawDefs.find(x=>x[2]===state.sort)?.[1]||state.sort,state.sort));render();}});
window.addEventListener('workbench-snapshot',()=>reload({force:true}).catch(e=>notice(e.message)));
window.addEventListener('workbench-b-observe',()=>reload({force:true}).catch(e=>notice(e.message)));
// Existing research requests retain their explicit analysis limit, but receive the exact
// canonical selection and query generation rather than re-resolving a stale B event.
const legacyReportRows=window.reportRows;
window.reportRows=function(){if(!payload)return legacyReportRows();const wanted=reportScope==='custom'?customTokens:reportScope==='watch'?watchTokens:state.selected;const source=reportScope==='filtered'?visible:reportScope==='all'?rows:rows.filter(r=>wanted.has(r.key));return source.map(r=>({...r.raw,unifiedEvidence:core.evidence(r,state,thresholds,payload)}));};
const researchButton=document.createElement('button');researchButton.id='u-sendResearch';researchButton.textContent='已选带入投研';$('clearSelected').insertAdjacentElement('afterend',researchButton);researchButton.onclick=async()=>{try{await hydrateFull();setReportScope('selected');page('reports');}catch(e){notice(e.message);}};
// Saved tasks can still refresh their hidden legacy panel without exposing a second filter UI.
window.renderMarket=function(){if(payload)render();};
const nav=document.querySelector('.sidebar [data-page="market"]');if(nav)nav.innerHTML='统一信号工作台';
const strat=document.querySelector('.sidebar [data-page="strategies"]');if(strat)strat.textContent='指标与策略';
const strategyPanel=document.querySelector('[data-view="strategies"]');if(strategyPanel){const note=document.createElement('p');note.className='note';note.innerHTML='当前组合以 <a href="/unified-strategies.html">v3.3 r5 组合使用说明</a> 为准。下方保留历史注册表、草案与冻结版本；旧④三线下方只用于历史／候选口径，不等于当前金叉分级。';strategyPanel.prepend(note);}
// Source catalogue remains authoritative for conditions, help and threshold defaults.
profile=function(id){if(id==='i1')return 'OI / 市值 > '+thresholds.tOi+'%';if(id==='i2')return '账户比 < '+thresholds.tLsr;if(id==='i3')return '年化 < '+thresholds.tApr+'%';if(id==='i4')return aStageLabel(state.aStage);if(id==='i5')return '已核验净流入 > 0';if(id==='i6')return '24H量 > '+thresholds.tVol+'× · |价变| < '+thresholds.tQuiet+'%';if(id==='b')return bOptions[state.bStage];if(id==='c')return cOptions[state.cStage]+' · 同期资格';return id==='reserve'?({covered:'独立储备覆盖，缺证据未知',day:'日余额减少 ≥ '+state.reserveThreshold+'%',week:'7日余额减少 ≥ '+state.reserveThreshold+'%',streak:'连续减少至少3天'}[state.reserveMode]):'规划／未接入';};
const originalSettings=settingsOpen;settingsOpen=function(id){originalSettings(id);$('settingsBody').innerHTML=$('settingsBody').innerHTML.replaceAll('合成观察标签演示筛选','规划／未接入，当前结果未知').replaceAll('规划演示','规划／未接入');};


function eventListHtml(r){return '<h3>同合约事件（点击查看独立事件与修订）</h3><div class="event-list">'+(r.raw.bEvents||[]).map(e=>'<button data-detail="'+esc(r.key)+'" data-tab="b" data-event="'+esc(e.event_id)+'" data-revision="'+esc(e.event_revision)+'">'+esc(date(e.trigger_open_time))+' · '+esc(bLabel[core.bstatus(e)]||'待核验')+' · r'+esc(e.event_revision)+'</button>').join('')+'</div>';}
function syncScan(value){
 const d=value||window.WorkbenchScan?.state();if(!d)return;
 $('startScan').disabled=d.disabled;$('startScan').textContent=d.label;
 const task=d.task,p=task?.progress||{},done=Number(p.completed)||0,total=Number(p.total)||0;
 let status=d.status;const errorStatus=/^(操作失败|扫描服务不可用|调度异常)/.test(status||'');
 if(d.connected&&!errorStatus&&d.operation&&(!task||d.operation.tasks.some(t=>t?.id===task.id))){
  status=d.status;
 }else if(d.connected&&!errorStatus&&task){
  const phase=total>0&&done>=total&&p.phase==='全量查询OI、账户比、已收盘4hK线'?'行情采集结束，正在归档':(p.phase||'准备采集');
  status='全市场扫描 · '+(task.status==='queued'?'排队中':'运行中')+' · '+phase;
 }else if(d.connected&&!errorStatus&&d.lastTask){
  const last=d.lastTask,labels={success:'扫描完成',partial_success:'扫描部分完成，缺口见数据与任务',failed:'扫描失败，保留上次快照',cancelled:'扫描已取消'};
  status=(labels[last.status]||'最近扫描')+(last.finished_at?' · '+date(last.finished_at):'');
 }
 {const el=$('scanStatus');if(el){el.title=status||'';el.textContent=status||'';el.classList.toggle('ok',/完成(?!，)|成功/.test(status||'')&&!/失败|异常|不可用/.test(status||''));el.classList.toggle('partial',/部分完成|排队|运行中|采集/.test(status||''));el.classList.toggle('err',/失败|异常|不可用/.test(status||''));}}
 // The original task runtime owns the single counter, timer and progress bar.
 const meter=document.getElementById('liveScanProgressWrap');
 if(meter&&$('scanStatus')&&meter.parentElement!==$('scanStatus').parentElement){
  $('scanStatus').insertAdjacentElement('afterend',meter);
  meter.style.flex='1 1 180px';meter.style.width='auto';meter.style.minWidth='160px';meter.style.maxWidth='320px';meter.style.margin='0';
 }
}
$('startScan').onclick=()=>window.WorkbenchScan?window.WorkbenchScan.start():notice('扫描服务尚未连接，请稍后重试');
$('viewTasks').onclick=()=>{page('tasks');setTab('task','jobs');};
window.addEventListener('workbench-scan-state',e=>syncScan(e.detail));syncScan();
host.addEventListener('click',ev=>{const button=ev.target.closest('[data-b-view]');if(!button)return;const historical=button.dataset.bView==='history';state.bStage=historical?'complete':'cur_event';state.signals=new Set(['b']);state.context='latest';state.history='all';state.result='pass';state.quick='custom';$('comboPreset').value='custom';change();});
for(const key of ['bFrom','bTo'])$(key).addEventListener('change',e=>{state[key]=e.target.value;change();});
$('defaultColumns').onclick=()=>{rawCols=defaultColumns.slice();saveColumns();render();};
$('allColumns').onclick=()=>{rawCols=rawDefs.map(d=>d[0]);saveColumns();render();};
function renderBArchive(){const r=payload?.last_round,s=r?.round_stats||{};$('bRoundStatus').textContent=r?'最近一轮 '+(r.status||'未知')+' · 目标收盘 '+date(r.target_close_boundary)+' · 目录 '+(s.universe??r.symbols??'未知')+' · 已监测 '+(s.monitored_symbols??'未知')+' · 可评估 '+(s.evaluated_symbols??'未知')+' · 缺口 '+(s.warmup_or_gap_symbols??'未知')+' · 未监测 '+(s.unmonitored??'未知')+' · 错误 '+(s.errors??'未知'):'尚无可得B轮次；未监测不等于未触发。';$('bEventArchive').innerHTML=rows.filter(r=>r.raw.bEvents?.length).map(eventListHtml).join('')||'<p class="muted">当前上下文没有可得事件。</p>';}
$('openMaintenance').onclick=()=>{page('settings');setTab('setting','storage');document.getElementById('unifiedRetentionPanel').hidden=false;const retentionVisible=new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)){retentionVisible.disconnect();loadRetention().catch(e=>{document.getElementById('unifiedRetentionStatus').textContent=e.message;});}});retentionVisible.observe(maintenance);};
const maintenance=document.createElement('section');maintenance.className='panel';maintenance.id='unifiedRetentionPanel';maintenance.hidden=false;maintenance.innerHTML='<h2>历史清理设置</h2><p id="unifiedRetentionStatus" role="status"></p><label><input id="unifiedRetentionEnabled" type="checkbox"> 开启普通历史定期清理</label><div id="unifiedRetentionFields" class="row"></div><button id="unifiedRetentionSave">保存清理设置</button><p class="muted">当前快照、最近三轮可用快照、事件与研究引用受保护；40GB为预算。读取设置不会执行清理。</p>';document.querySelector('[data-setting-view="storage"]').append(maintenance);
async function loadRetention(){const response=await fetch('/api/cache/retention',{cache:'no-store'});if(!response.ok)throw Error('读取清理设置失败');const d=await response.json();document.getElementById('unifiedRetentionEnabled').checked=d.settings.enabled;const labels={temp_hours:'临时文件（小时）',debug_days:'调试明细（天）',log_days:'普通日志（天）',raw_api_days:'原始响应（天）',full_snapshot_days:'完整快照（天）',oi_lsr_days:'普通OI/LSR（天）','1h_days':'普通1H K线（天）',backup_days:'备份（天）',backup_min_success:'成功恢复点（至少3）'};document.getElementById('unifiedRetentionFields').innerHTML=Object.entries(d.policy).map(([k,v])=>'<label>'+esc(labels[k]||k)+'<input type="number" min="'+(k==='backup_min_success'?3:1)+'" max="3650" data-retention="'+esc(k)+'" value="'+esc(v)+'" style="width:85px"></label>').join('');document.getElementById('unifiedRetentionStatus').textContent='当前占用 '+(d.capacity.total_bytes/1024**3).toFixed(2)+' GB / 40 GB';}
document.getElementById('unifiedRetentionSave').onclick=async()=>{const b=document.getElementById('unifiedRetentionSave');b.disabled=true;try{const r=await fetch('/api/cache/retention',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:document.getElementById('unifiedRetentionEnabled').checked,retention:Object.fromEntries([...maintenance.querySelectorAll('[data-retention]')].map(i=>[i.dataset.retention,Number(i.value)]))})});if(!r.ok)throw Error('保存失败');document.getElementById('unifiedRetentionStatus').textContent='清理设置已保存';}catch(e){document.getElementById('unifiedRetentionStatus').textContent=e.message;}finally{b.disabled=false;}};
// Event isolation and the filter shell must be live before waiting for any data API.
for(const type of ['click','change','input','keydown'])host.addEventListener(type,e=>e.stopPropagation());

function syncScope(){const defaults={history:'all',context:'latest',listing:'current',trusted:false};for(const el of $$('[data-scope]'))el.checked=String(state[el.dataset.scope])===el.value;const changed=['history','context','listing','trusted'].some(k=>state[k]!==defaults[k]);const summary=$('scopeSummary');if(summary){const ctx=state.context==='latest'?'最新保存行情与信号':'还原当时数据';const t=date(payload?.snapshot?.finishedAt);const bits=[];if(changed)bits.push('已改范围');bits.push(ctx+(t?' · '+t:''));summary.textContent=bits.join(' · ');}}
/* 1005: details#u-scopeAdv replaces toggle/overlay */
$('scopeReset').onclick=()=>{Object.assign(state,{history:'all',context:'latest',listing:'current',trusted:false});change();};
const addAction=(id,label,parent,action)=>{const button=document.createElement('button');button.id='u-'+id;button.textContent=label;parent.append(button);button.onclick=action;};
addAction('watchSelected','加入观察池',$('clearSelected').parentElement,()=>{for(const key of state.selected)watchTokens.add(key);if(typeof updateReportSelection==='function')updateReportSelection();notice('已加入原有观察池 '+state.selected.size+' 份合约');});
addAction('researchFiltered','研究当前筛选',host.querySelector('.tabletools'),async()=>{try{await hydrateFull();setReportScope('filtered');page('reports');}catch(e){notice(e.message);}});
addAction('watchDetail','加入观察池',host.querySelector('.drawerfoot'),()=>{if(!detail)return;watchTokens.add(detail.key);if(typeof updateReportSelection==='function')updateReportSelection();notice('已加入原有观察池');});
addAction('researchDetail','研究此合约',host.querySelector('.drawerfoot'),async()=>{if(!detail)return;try{await hydrateFull();customTokens.clear();customTokens.add(detail.key);setReportScope('custom');closeDetail();page('reports');}catch(e){notice(e.message);}});
loadRetention().catch(e=>{document.getElementById('unifiedRetentionStatus').textContent=e.message;});
const railHint=host.querySelector('.railbottom');if(railHint)railHint.innerHTML='点名称只展开条件；选择等级或修改阈值会启用该信号，复选框可独立取消。<br>高级范围条件对所有信号统一生效。';
try{refreshCombos();render();}catch(e){console.warn('首屏控件同步异常，继续读取保存数据',e);}
 window.UnifiedWorkbench={applyPreset(id){const target=COMBO_GUIDES.find(c=>c.id===id&&c.available)||customCombos.find(c=>c.id===id);if(!target){notice('此为旧版或未开放组合，请在当前组合中重新选择；未替换历史口径');return false;}$('comboPreset').value=id;applyCombo(id);return true;},selection(){return {selected:rows.filter(r=>state.selected.has(r.key)).map(r=>r.raw),visibleKeys:new Set(visible.map(r=>r.key))};},version:'v3.3-r5',reload,hydrateFull,serialize,getEvidence:()=>payload,researchContext:()=>({schema:UnifiedCore.VERSION,generation:payload?.generation??null,context:state.context,query:serialize()})};

try{await bootstrapSavedScan();render();await reload();restoreHash();}catch(e){notice('保存数据读取失败：'+e.message+'；请使用重新读取');}
const applyId=new URLSearchParams(location.search).get('combo');
if(applyId){const target=COMBO_GUIDES.find(c=>c.id===applyId&&c.available)||customCombos.find(c=>c.id===applyId);if(target){$('comboPreset').value=applyId;applyCombo(applyId);}else notice('组合不存在或尚未开放筛选，未自动替换');}

// Keep one page scrollbar; a sidebar taller than the screen returns to normal flow.
function syncRailFit(){const rail=$('rail');rail.classList.toggle('rail-tall',rail.getBoundingClientRect().height>window.innerHeight-76);}
const railFitObserver=new ResizeObserver(syncRailFit);railFitObserver.observe($('rail'));window.addEventListener('resize',syncRailFit);syncRailFit();

// Prevent the older document-wide table handlers from interpreting new contract IDs as indices.
 for(const type of ['click','change','input','keydown'])host.addEventListener(type,e=>e.stopPropagation());
 window.dispatchEvent(new CustomEvent('workbench-selection-changed'));
 })().catch(e=>{console.error(e);const p=document.querySelector('[data-view="market"]');if(p){const n=document.createElement('p');n.className='u-error';n.textContent='统一工作台加载失败：'+e.message;p.prepend(n);}});
 
