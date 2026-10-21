// BEGIN RESERVE RUNTIME
const reservesData={records:[]};
function syncReserveSnapshot(){
 const next=snapshot.reserveEvidence||{records:[],status:'not_collected',freshAssets:0,contractReferences:0};
 Object.keys(reservesData).forEach(k=>delete reservesData[k]);Object.assign(reservesData,next);
 reservesByAsset.clear();(reservesData.records||[]).forEach(r=>reservesByAsset.set(r.asset,r));
}
const reservesByAsset=new Map();syncReserveSnapshot();
const reserveFresh=r=>r&&r.freshAtRetrieval&&Date.now()/1000>=r.asOf&&(Date.now()/1000-r.asOf)/3600<=36;
const reserveDate=t=>!Number.isFinite(t)?'未知':new Date(t*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
function reserveCheck(row){const rule=$('#reserveFilter').value;if(rule==='none')return true;const r=reservesByAsset.get(row.asset);if(!reserveFresh(r))return null;if(rule==='covered')return true;if(rule==='day')return r.dayChangePct<0&&r.dayChangePct<=-Number($('#reserveThreshold').value);if(rule==='week')return r.weekChangePct<0&&r.weekChangePct<=-Number($('#reserveThreshold').value);return r.consecutiveDecreaseDays>=3;}
const originalReserveScope=scope;
scope=function(row){return threshold([originalReserveScope(row),reserveCheck(row)],2)};
const originalReserveValidation=filterValidation;
filterValidation=function(){const old=originalReserveValidation();if(old)return old;const e=$('#reserveThreshold');if(['day','week'].includes($('#reserveFilter').value)&&(!e.validity.valid||!present(Number(e.value))||e.value===''||Number(e.value)<0||Number(e.value)>100))return '储备减少阈值须为0—100的有效百分比。';return '';};
snapshot.rows.forEach(r=>{const p=reservesByAsset.get(r.asset);r.reserveDayPct=reserveFresh(p)?p.dayChangePct:null;r.reserveWeekPct=reserveFresh(p)?p.weekChangePct:null;});
function reserveEvidence(row){const r=reservesByAsset.get(row.asset);return r?{...r,retrievedAt:reservesData.retrievedAt,updateStatus:reservesData.status,updateError:reservesData.updateError,freshNow:!!reserveFresh(r),source:reservesData.source,sourceEndpoint:reservesData.endpoint,methodology:reservesData.methodology,exactNetflow:false,derivativesSnapshotAt:snapshot.finishedAt,combinedSignal:false}:null;}
function reserveText(row){const r=reservesByAsset.get(row.asset);return !r?'储备辅助证据未知：未核验本资产网络映射。':`${reserveFresh(r)?'近期':'已过期'}局部储备：${r.chain}；日变化${fmt(r.dayChangePct,3)}%，7日${fmt(r.weekChangePct,3)}%，连续减少${r.consecutiveDecreaseDays}天。截止${reserveDate(r.asOf)}（北京时间）。采集${reservesData.retrievedAt||'未知'}。${reservesData.updateError?'本轮更新失败，保留上次证据。':''}属于余额变化，非逐笔净流出；与合约快照异步，不能确认为同步组合或吸筹。`;}
const originalReserveRender=renderMarket;
renderMarket=function(){syncReserveSnapshot();renderReserveTable();snapshot.rows.forEach(r=>{const p=reservesByAsset.get(r.asset);r.reserveDayPct=reserveFresh(p)?p.dayChangePct:null;r.reserveWeekPct=reserveFresh(p)?p.weekChangePct:null;});originalReserveRender();const mode=$('#reserveFilter').value;$('#reserveThresholdLabel').hidden=!['day','week'].includes(mode);const fresh=reservesData.records.filter(reserveFresh);$('#reserveStatus').textContent=(snapshot.registryHash!==StrategyRegistry.hash?'旧规则快照，请重新扫描；':'聪明钱4h榜单；')+fresh.length+'资产储备另列辅助';if(!filterValidation()){$$('#marketRows tr').forEach((tr,i)=>{const r=pageRows[i];if(!r)return;const cell=tr.querySelectorAll('td')[3];if(cell){const small=document.createElement('small');small.textContent=reserveFresh(reservesByAsset.get(r.asset))?'储备日变化 '+fmt(r.reserveDayPct,2)+'%':'储备辅助未知/过期';cell.append(small);}})}};
['reserveFilter','reserveThreshold'].forEach(id=>$('#'+id).addEventListener('input',()=>{marketPage=1;renderMarket()}));
const originalReserveReset=$('#reset').onclick;$('#reset').onclick=()=>{$('#reserveFilter').value='none';$('#reserveThreshold').value='1';originalReserveReset()};
const originalReserveRequest=requestPayload;
requestPayload=function(){const request=originalReserveRequest();request.supplementalEvidence={type:'labelled_reserve_change_proxy',snapshotAsOf:reservesData.retrievedAt,currentExactNetflowAvailable:false,independentTimestamps:true,filter:{mode:$('#reserveFilter').value,thresholdPct:Number($('#reserveThreshold').value)},limits:reservesData.limits};request.evidence=request.evidence.map(r=>({...r,reserveEvidence:reserveEvidence(r)}));return request;};
const originalReserveReport=$('#dataReport').onclick;
$('#dataReport').onclick=()=>{originalReserveReport();$$('#generatedReport .report-item').forEach((article,i)=>{const row=analysisRows()[i];const evidence=document.createElement('div');evidence.className='note';evidence.textContent='⑤扩展·储备变化：'+reserveText(row);article.append(evidence);});};
const originalReserveDetail=openDetail;
openDetail=function(index,trigger){originalReserveDetail(index,trigger);const n=document.createElement('div');n.className='note';n.textContent=reserveText(snapshot.rows[index]);$('#detailBody').append(n);};
$('#reserveCoverage').textContent=`${reservesData.freshAssets}资产 · ${reservesData.contractReferences}合约关联（采集时）`;
$('#reserveDataTime').textContent='截至'+reserveDate(reservesData.records[0]?.asOf)+'；只用完整24h日边界，不把今日部分区间当作一天。';
function renderReserveTable(){
 const fresh=reservesData.records.filter(reserveFresh);
 $('#reserveCoverage').textContent=`${fresh.length}资产 · ${snapshot.rows.filter(r=>reserveFresh(reservesByAsset.get(r.asset))).length}合约关联`;
 const state=reservesData.updateError?'更新失败，保留上次证据':reservesData.status==='not_collected'?'本轮尚未采集':reservesData.cacheError?'证据已更新，缓存保存失败':fresh.length?'最新可用完整日证据':'暂无有效证据';
 $('#reserveDataTime').textContent=state+' · 采集：'+(reservesData.retrievedAt||'未取得')+' · 检查：'+(reservesData.checkedAt||'未检查')+'；各资产截止时间独立，超过36小时不参与当前筛选。';
 const mode=$('#reserveTableMode').value;const rows=reservesData.records.filter(r=>mode==='all'||reserveFresh(r)&&((mode==='day'&&r.dayChangePct<=-1)||(mode==='week'&&r.weekChangePct<=-3)||(mode==='streak'&&r.consecutiveDecreaseDays>=3))).sort((a,b)=>a.dayChangePct-b.dayChangePct);$('#reserveTableCount').textContent=rows.length+'个资产；按日变化升序';$('#reserveTableRows').innerHTML=rows.map(r=>`<tr><td>${safe(r.asset)}<small>${safe(r.chain)} · ${reserveFresh(r)?'近期':'已过期'}</small></td><td>${fmt(r.reserveTokens,2)}</td><td>${fmt(r.dayChangePct,3)}%</td><td>${fmt(r.weekChangePct,3)}%</td><td>${r.consecutiveDecreaseDays}天 / ${r.decreasingDaysInWeek}天</td><td><button data-reserve-report="${safe(r.asset)}">带入投研</button></td></tr>`).join('')||'<tr><td colspan="6">此条件没有样本，不强行挑选。</td></tr>';}
$('#reserveTableMode').onchange=renderReserveTable;renderReserveTable();
document.addEventListener('click',e=>{const b=e.target.closest('[data-reserve-report]');if(!b)return;const matches=snapshot.rows.filter(r=>r.asset===b.dataset.reserveReport&&r.market!=='COIN-M'&&r.quote==='USDT');if(matches.length!==1){toast('没有唯一USDT合约，请在市场按资产选择。');return}selectedTokens.add(key(matches[0]));setReportScope('selected');page('reports');toast('已带入合约与独立时点的储备证据；请生成报告核验。');});
$('#reserveStrategy').onclick=()=>{$('#guidedDraft').hidden=false;$('#guidedDraftTitle').textContent='储备减少＋价格修复 · 观察草案';$('#editorHeading').textContent='局部储备变化辅助策略';$('#guidedDraftBody').textContent='候选：已核验原生链映射、8个连续日余额、数据不超过36小时；日减少≥1%，或7日减少≥3%，或连续3日减少。\n核验：先检查钱包标签新增/删除、托管/储备金移动、代币迁移；排除币安储备本身异常。需要同期合约与现货证据，当前旧扫描不能作为同期确认。\n触发：后续完整K线收复EMA99并回踩，或突破预先固定日线箱体，等待现货量/主动买盘证据。\n失效：标签或数量口径变化、价格继续新低、结构失败；入场前冻结结构止损和总篮子风险1%。\n状态：真实辅助数据可用于筛选与研究；完整价格数据、标签审计、策略冻结、模拟账本未齐，不运行、不报胜率。';page('strategies');setTab('strategy','editor');$$('#strategySteps [data-step]').forEach(b=>b.hidden=true);$$('[data-step-view]').forEach(v=>v.hidden=true);};
// END RESERVE RUNTIME
