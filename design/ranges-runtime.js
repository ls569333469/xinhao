/* Manual interval evidence tasks. No AI, account credentials or trades. */
(() => {
 'use strict';
 const q=s=>document.querySelector(s);
 if(!q('[data-view="tasks"]'))return;
 const esc=v=>String(v??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const chosen=new Set();let prepared=null,busy=false,lastFinished=null,lastScanFinished=null;
 const tab=document.createElement('button');tab.dataset.task='ranges';tab.textContent='区间行情补查';
 q('[data-view="tasks"] .subtabs').insertBefore(tab,q('[data-task="history"]'));
 const section=document.createElement('div');section.dataset.taskView='ranges';section.hidden=true;
 section.innerHTML=`<div class="panel"><div class="panel-head"><div><h2>区间行情补查</h2><small>公开K线 · 不调用AI · 不修改扫描快照 · 每任务1–20份合约、最长366天</small></div><button id="refreshRangeHistory">刷新归档</button></div>
 <div class="grid2"><div><label class="field"><span>查找合约（当前保存快照）</span><input id="rangeSearch" placeholder="例如 BTCUSDT 或 ETH"></label><label class="field"><span id="rangeSelectedCount">已选0份 · 按Ctrl/⌘可多选</span><select id="rangeContracts" multiple size="8" style="width:100%;min-height:190px" aria-label="补查合约，多选"></select></label><div class="row"><button id="useMarketRangeSelection">使用市场已选</button><button id="clearRangeSelection">清空</button></div><p id="rangeSelectedNames" class="muted"></p></div>
 <div><div class="grid3"><label class="field"><span>K线周期</span><select id="rangeInterval"><option value="1h">1小时</option><option value="4h">4小时</option><option value="1d">日线（UTC）</option></select></label><label class="field"><span>开始时间（UTC，含）</span><input id="rangeStart" type="datetime-local"></label><label class="field"><span>结束时间（UTC，不含）</span><input id="rangeEnd" type="datetime-local"></label></div><div class="note">仅纳入完整落在区间内的已收盘K线。日线以UTC零点为边界。上市前无行情、源错误和断档均保留为缺口。先检查本地覆盖，再明确提交公开行情请求；新任务重新查询选定区间，旧归档保留。</div><div class="command-bar"><span id="rangeStatus" role="status" aria-live="polite">手动补查；没有自动任务。</span><div class="row"><button id="rangePreflight">检查本地覆盖</button><button id="rangeSubmit" class="primary">提交区间补查</button></div></div></div></div>
 <div id="rangeCoverage" class="note" hidden></div></div>
 <div class="panel"><h2>区间归档</h2><p class="muted">最近50个已完成归档；未完成任务见“任务记录”。覆盖仅代表价格K线，不证明历史OI、市值、链上流或代币全市场ATH。</p><div class="tablewrap"><table><thead><tr><th>区间 / 周期</th><th>合约与覆盖</th><th>状态 / 缺口</th><th>实际可得时间（UTC）</th></tr></thead><tbody id="rangeHistoryRows"></tbody></table></div></div>
 <div class="panel"><div class="panel-head"><div><h2>只读价格索引（DATA-01）</h2><small>合并扫描窗口K线与区间补查 · 当前可得 ≠ 历史当时可得 · 不宣称六历史指标齐全</small></div><button id="refreshPriceIndex">刷新索引</button></div>
 <p id="priceIndexSummary" class="muted">正在读取本地价格目录…</p>
 <div class="note" id="priceIndexLabels"></div>
 <div class="tablewrap"><table><thead><tr><th>合约 / 周期</th><th>当前状态</th><th>扫描窗口来源</th><th>区间补查来源</th><th>约计K线</th></tr></thead><tbody id="priceIndexRows"></tbody></table></div></div>
 <div class="panel"><div class="panel-head"><div><h2>快照 revision 浏览</h2><small>只列磁盘上已有的 scan revision；不补造缺失历史</small></div><button id="refreshSnapshots">刷新快照列表</button></div>
 <p id="snapshotSummary" class="muted">正在读取…</p>
 <div class="tablewrap"><table><thead><tr><th>scan_id / revision</th><th>状态 / 质量</th><th>取得 / 总数</th><th>K线文件</th><th>实际可得时间</th></tr></thead><tbody id="snapshotRows"></tbody></table></div></div>`;
 q('[data-view="tasks"]').append(section);
 const end=new Date(Math.floor(Date.now()/14400000)*14400000);
 q('#rangeEnd').value=end.toISOString().slice(0,16);q('#rangeStart').value=new Date(end.getTime()-30*86400000).toISOString().slice(0,16);
 const key=c=>c.market+'|'+c.symbol;
 function status(t){q('#rangeStatus').textContent=t;}
 function buttons(){['rangePreflight','rangeSubmit','rangeSearch','rangeContracts','rangeInterval','rangeStart','rangeEnd','useMarketRangeSelection','clearRangeSelection'].forEach(id=>q('#'+id).disabled=busy);}
 function invalidate(){prepared=null;q('#rangeCoverage').hidden=true;status('条件已修改，请重新检查覆盖。');}
 function renderContracts(){
  const search=q('#rangeSearch').value.trim().toUpperCase();
  q('#rangeContracts').innerHTML=snapshot.rows.filter(c=>chosen.has(key(c))||!search||c.symbol.includes(search)||c.asset?.includes(search)).map(c=>`<option value="${esc(key(c))}" ${chosen.has(key(c))?'selected':''}>${esc(c.symbol)} · ${esc(c.market)}</option>`).join('');
  q('#rangeSelectedCount').textContent=`已选${chosen.size}/20份 · 按Ctrl/⌘可多选`;
  q('#rangeSelectedNames').textContent=[...chosen].join('、')||'尚未选择合约';
 }
 function payload(){
  if(chosen.size<1||chosen.size>20)throw new Error('请选择1–20份合约；更多合约请分批补查。');
  if(!q('#rangeStart').value||!q('#rangeEnd').value)throw new Error('请填写完整的UTC时间范围。');
  return {contracts:[...chosen].sort().map(k=>{const [market,symbol]=k.split('|');return {market,symbol};}),interval:q('#rangeInterval').value,start:q('#rangeStart').value+':00Z',end:q('#rangeEnd').value+':00Z'};
 }
 async function api(path,method='GET',body){
  const r=await fetch(path,{method,cache:'no-store',headers:method==='GET'?{}:{'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})});
  const data=await r.json();if(!r.ok)throw new Error(data.error||`HTTP ${r.status}`);return data;
 }
 function showCoverage(data){
  q('#rangeCoverage').hidden=false;
  q('#rangeCoverage').innerHTML=`<strong>本地覆盖检查 · ${data.complete?'所选价格区间已齐':'仍有价格缺口'}</strong><p>有效完整区间：${esc(data.request.effective_start)} → ${esc(data.request.effective_end)}（不含） · ${esc(data.request.interval)}。<strong>当前可得</strong>是现在磁盘合并结果；各来源 <strong>历史当时可得</strong> 见 available_at，后补价格不能回填成当时已得。</p><div class="tablewrap"><table><thead><tr><th>合约</th><th>取得 / 应有</th><th>覆盖</th><th>连续性</th><th>缺口 / 冲突 / 错误</th></tr></thead><tbody>${data.series.map(r=>{const c=r.continuity||{};return `<tr><td>${esc(r.symbol)}<small>${esc(r.market)}</small><small>${esc(r.label_current||'当前可得')}</small></td><td>${r.known}/${r.expected}</td><td>${r.coveragePct}%</td><td>${c.continuous?'逐周期连续':'有断档'}<small>末段连续${c.trailing_continuous??'—'}根 · 最长${c.longest_continuous??'—'} · 预热提示${c.warmup_ready_hint??'—'}</small></td><td>${r.missing}根缺失 · ${r.conflicts}冲突 · ${(r.errors||[]).length}归档错误${(r.gaps||[]).slice(0,3).map(g=>`<small>${esc(g.start)} → ${esc(g.end)}，${g.bars}根</small>`).join('')}${(r.gap_count||0)>3?`<small>共${r.gap_count}段缺口</small>`:''}</td></tr>`;}).join('')}</tbody></table></div><p>${esc(data.scope)}</p>`;
 }
 async function preflight(){const body=payload();const data=await api('/api/candles/preflight','POST',body);showCoverage(data);prepared=JSON.stringify(body);return body;}
 async function history(){
  const {ranges}=await api('/api/candles/history');
  const names={success:'成功',partial_success:'部分成功',failed:'失败'};
  q('#rangeHistoryRows').innerHTML=ranges.map(r=>`<tr><td>${esc(r.request.effective_start)}<small>至 ${esc(r.request.effective_end)}（不含） · ${esc(r.request.interval)}</small><small>${esc(r.range_id)}</small></td><td>${r.series.map(s=>`${esc(s.symbol)} ${esc(s.market)} · ${s.known}/${s.expected} (${s.coveragePct}%)`).join('<br>')}</td><td>${esc(names[r.status]||r.status)}${r.series.map(s=>`<small>${esc(s.symbol)}：${s.missing}根缺失 / ${s.gap_count}段${s.errors.map(e=>'；'+esc(e.error)).join('')}</small>`).join('')}</td><td>${esc(r.available_at)}<small>原始响应及SHA256已归档</small></td></tr>`).join('')||'<tr><td colspan="4">暂无区间补查归档；扫描窗口K线已纳入上方价格索引与覆盖检查。</td></tr>';
 }
 q('#rangeSearch').oninput=renderContracts;
 q('#rangeContracts').onchange=()=>{[...q('#rangeContracts').options].forEach(o=>o.selected?chosen.add(o.value):chosen.delete(o.value));invalidate();renderContracts();};
 ['rangeInterval','rangeStart','rangeEnd'].forEach(id=>q('#'+id).addEventListener('change',invalidate));
 q('#useMarketRangeSelection').onclick=()=>{if(selectedTokens.size>20){status('市场已选超过20份，请缩小选择或分批补查。');return;}chosen.clear();selectedTokens.forEach(k=>chosen.add(k));invalidate();renderContracts();};
 q('#clearRangeSelection').onclick=()=>{chosen.clear();invalidate();renderContracts();};
 q('#rangePreflight').onclick=async()=>{busy=true;buttons();try{await preflight();status('本地覆盖检查完成；未访问外部行情源。');}catch(e){status('检查失败：'+e.message);}finally{busy=false;buttons();}};
 q('#rangeSubmit').onclick=async()=>{busy=true;buttons();try{let body=payload();if(prepared!==JSON.stringify(body))body=await preflight();const result=await api('/api/candles/backfills','POST',body);status((result.reused?'复用在途补查：':'补查任务已排队：')+result.task.id);window.dispatchEvent(new Event('workbench-task-created'));toast('区间补查已提交，任务记录可查看进度；不调用AI。');}catch(e){status('提交失败：'+e.message);}finally{busy=false;buttons();}};
 q('#refreshRangeHistory').onclick=()=>history().catch(e=>status('读取失败：'+e.message));
 let priceIndexRead=null;
 async function loadPriceIndex(){
  if(!section.getClientRects().length)return;
  if(priceIndexRead)return priceIndexRead;
  priceIndexRead=readPriceIndex();try{return await priceIndexRead;}finally{priceIndexRead=null;}
 }
 async function readPriceIndex(){
  const data=await api('/api/price-index?limit=80');
  const arch=data.archives||{};
  q('#priceIndexSummary').textContent=`共${data.total_series}个合约周期 · 本页${data.returned}行${data.truncated?'（已截断）':''} · 扫描窗口归档${arch.scan_windows||0}个 · 区间补查归档${arch.candle_ranges||0}个 · ${data.checked_at||''}`;
  q('#priceIndexLabels').innerHTML=`<strong>口径</strong>：${esc(data.labels?.current_available||'当前可得')}；${esc(data.labels?.historical_available_at||'历史当时可得')}；${esc(data.labels?.missing_history||'缺失不标已覆盖')}。<br>${esc(data.scope)}`;
  q('#priceIndexRows').innerHTML=data.series.map(r=>`<tr><td>${esc(r.symbol)}<small>${esc(r.market)} · ${esc(r.interval)}</small></td><td>${esc(r.label_current)}</td><td>${(r.scan_sources||[]).length}个${(r.scan_sources||[]).slice(0,2).map(s=>`<small>${esc(s.scan_id)} · ${esc(s.available_at)}</small>`).join('')}</td><td>${(r.range_sources||[]).length}个${(r.range_sources||[]).slice(0,2).map(s=>`<small>${esc(s.range_id)} · 知${s.known}/${s.expected??'—'} · ${esc(s.available_at)}</small>`).join('')}</td><td>扫描约${r.approx_bars_scan} · 补查约${r.approx_bars_range_known}<small>${esc(r.merge_note)}</small></td></tr>`).join('')||'<tr><td colspan="5">尚无价格归档；跑过扫描或区间补查后才会出现。缺失历史不会标成已覆盖。</td></tr>';
 }
 async function loadSnapshots(){
  const data=await api('/api/snapshots?limit=50');
  q('#snapshotSummary').textContent=`磁盘上找到${data.total_found}个 revision · 本页${data.snapshots.length}行。${data.scope||''}`;
  const names={success:'成功',partial_success:'部分成功',failed:'失败',legacy_import:'旧样本导入'};
  q('#snapshotRows').innerHTML=data.snapshots.map(s=>`<tr><td>${esc(s.scan_id||'（无scan_id）')}<small>r${esc(s.revision??'—')} · ${esc(s.provenance)}</small></td><td>${esc(names[s.status]||s.status||'—')}<small>${s.quality?`行覆盖${esc(s.quality.ratios?.rows)}`:(s.error||'')}</small></td><td>${esc(s.received)} / ${esc(s.total)}</td><td>${esc(s.candle_files)}</td><td>${esc(s.available_at)}<small>${esc(s.availability)}</small></td></tr>`).join('')||'<tr><td colspan="5">尚无已发布 revision；不伪造历史快照。</td></tr>';
 }
 q('#refreshPriceIndex').onclick=()=>loadPriceIndex().catch(e=>{q('#priceIndexSummary').textContent='读取失败：'+e.message;});
 q('#refreshSnapshots').onclick=()=>loadSnapshots().catch(e=>{q('#snapshotSummary').textContent='读取失败：'+e.message;});
 window.addEventListener('workbench-snapshot',()=>{const pool=new Set(snapshot.rows.map(key));for(const k of chosen)if(!pool.has(k))chosen.delete(k);invalidate();renderContracts();loadPriceIndex().catch(()=>{});loadSnapshots().catch(()=>{});});
 window.addEventListener('workbench-tasks',e=>{const task=e.detail.find(t=>t.kind==='candle_range'&&t.finished_at);if(task&&task.id!==lastFinished){lastFinished=task.id;history().catch(err=>status('读取失败：'+err.message));prepared=null;loadPriceIndex().catch(()=>{});}const scanDone=e.detail.find(t=>t.kind==='scan'&&t.result);if(scanDone&&scanDone.id!==lastScanFinished){lastScanFinished=scanDone.id;loadSnapshots().catch(()=>{});loadPriceIndex().catch(()=>{});}});
 renderContracts();history().catch(e=>status('读取失败：'+e.message));
 const visibleIndex=new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)){loadPriceIndex().catch(e=>{q('#priceIndexSummary').textContent='读取失败：'+e.message;});}});visibleIndex.observe(section);
 loadSnapshots().catch(e=>{q('#snapshotSummary').textContent='读取失败：'+e.message;});
})();
