/* Real local scan controls. Polls local state only; no AI requests. */
(() => {
 'use strict';
 const q = s => document.querySelector(s);
 const rich = !!q('[data-view="tasks"]');
 if(!rich){const link=document.createElement('a');link.href='/b-observe.html';link.textContent='庄家吸筹背离 · 1H';link.className='pill';(q('header')||document.body).prepend(link);}
 const escape = value => String(value ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const names = {queued:'排队',running:'运行中',success:'成功',partial_success:'部分成功',failed:'失败',cancelled:'已取消',skipped:'未运行／已跳过'};
 let config = null, actualConfig = null, busy = false, timer = null, rows = [], connected = false, scanTimezone = 'Asia/Shanghai', seenOutput = null, operation = null, seenBOutput = null;
 const time = value => {if(!value)return '—';const d=new Date(value);return Number.isNaN(d.getTime())?String(value):d.toLocaleString('zh-CN',{timeZone:scanTimezone,hour12:false});};
 const formatDuration = ms => {if(!Number.isFinite(ms)||ms<0)return '—';const s=Math.floor(ms/1000);const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),sec=s%60;if(h)return h+'小时'+m+'分'+sec+'秒';if(m)return m+'分'+sec+'秒';return sec+'秒';};
 const progressText=t=>{const p=t.progress||{},r=t.result||{},stats=r.round_stats;if(t.kind==='b_observe_1h'&&!p.unit&&['success','partial_success','failed','cancelled'].includes(t.status))return stats?'监测 '+(stats.monitored_symbols??'未知')+' / '+(stats.universe??r.symbols??'未知')+' 标的':'历史记录：评估 '+(r.evaluated??p.completed??'未知')+'（旧字段单位未核验），目录 '+(r.symbols??p.total??'未知')+' 标的';return (p.completed??0)+' / '+(p.total??'—')+' '+(p.unit==='symbols'?'标的':'项');};
 const progressLabel = t => {
  if(!t)return '';
  const done=Number(t.progress?.completed)||0, total=Number(t.progress?.total)||0;
  const started=t.started_at?new Date(t.started_at):null;
  const elapsed=started&&!Number.isNaN(started.getTime())?(Date.now()-started.getTime()):null;
  let eta='';
  if(elapsed!=null&&done>0&&total>done){const remain=elapsed*(total-done)/done;eta=' · 预计剩余 '+formatDuration(remain);}
  const clock=started?(' · 已用 '+formatDuration(elapsed)+' · 开始 '+time(t.started_at)):'';
  const kind=t.kind==='scan_and_research'?'扫描并投研':t.kind==='ai_research'?'AI投研':t.kind==='candle_range'?'区间补查':t.kind==='b_observe_1h'?'庄家吸筹1H':'全市场扫描';
  return kind+' '+(names[t.status]||t.status)+' · '+(t.progress?.phase||'准备')+' · '+progressText(t)+clock+eta;
 };
 async function api(path, method='GET', body) {
  const controller=method==='GET'?new AbortController():null;
  const timeout=controller?setTimeout(()=>controller.abort(),path==='/api/tasks'?30000:10000):null;
  try{
   const response = await fetch(path,{method,cache:'no-store',...(controller?{signal:controller.signal}:{}),headers:method==='GET'?{}:{'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})});
   const data = await response.json();
   if(!response.ok)throw new Error(data.error || `HTTP ${response.status}`);
   return data;
  }finally{if(timeout)clearTimeout(timeout);}
 }
 function operationTasks(){return operation?.tasks?.filter(Boolean)||[];}
 function currentScanTask(){return operationTasks().find(t=>t.id===operation?.current_task_id)||rows.find(t=>['scan','b_observe_1h'].includes(t.kind)&&t.status==='running')||rows.find(t=>['scan','b_observe_1h'].includes(t.kind)&&t.status==='queued')||null;}
 function operationStatus(){
  if(!operation)return '';
  const parts=operationTasks().map(t=>(t.kind==='scan'?'常规4H':'B观察1H')+'：'+(names[t.status]||t.status));
  const target=operation.targets?.find(t=>t.task_id===operation.current_task_id);
  const actual=target?.actual_target!=null?' · K线截至 '+time(target.candle_close_boundary??target.actual_target)+(target.lag_ms>0?'（复用旧边界，距本次请求 '+formatDuration(target.lag_ms)+'）':'') : '';
  const current=currentScanTask();
  return (operation.status==='success'?'本次扫描完成':operation.status==='partial_success'?(operation.coverage_pending?'复用任务已结束，新边界仍待补采':'本次扫描部分完成，请查看任务缺口'):operation.status==='failed'?'本次扫描未完成，请查看任务错误':'本次扫描进行中')+' · '+parts.join('；')+actual+(current?' · '+(current.progress?.phase||'等待执行'):'');
 }
 function scanState(){return {connected,busy,operation,label:q('#startLiveScan').textContent,status:q('#liveScanStatus').textContent,disabled:q('#startLiveScan').disabled,task:currentScanTask(),lastTask:rows.find(t=>t.kind==='scan')||null};}
 function notifyScan(){window.dispatchEvent(new CustomEvent('workbench-scan-state',{detail:scanState()}));}
 function message(text) {q('#liveScanStatus').textContent=text;const marketStatus=q('#u-scanStatus');if(marketStatus)marketStatus.textContent=text;notifyScan();}
 const intervalLabel = hours => Number(hours)===0.25?'15分钟':hours+'小时';
 function scheduleErrors(value){
  return [value.scheduler_error,...Object.entries(value.subsystem_errors||{}).filter(([,v])=>v).map(([k,v])=>String(v).includes('RECOVERY_business_root_not_evaluated')?'历史数据补齐尚未完成，正在等待对应周期重新评估':k+': '+v)].filter(Boolean);
 }
 function renderActualSettings(value){
  const summary=q('#liveScheduleSummary');
  if(summary){
   const last=kind=>{const t=rows.find(t=>t.kind===kind&&t.finished_at);return t?'最近 '+(names[t.status]||t.status)+' '+time(t.finished_at):'暂无完成记录';};
   summary.textContent='定时扫描 · 1H：'+(value.observe?.enabled?'开启，下次 '+time(value.observe_next_due):'关闭')+' · '+last('b_observe_1h')+'；4H采集：'+(value.scan.enabled?'开启，每'+intervalLabel(value.scan.intervalHours)+'，下次 '+time(value.next_due):'关闭')+' · '+last('scan')+' · 调度'+(value.scheduler_running?'运行中':'停止');
   if(value.scheduler_heartbeat)summary.textContent+=' · 心跳 '+time(value.scheduler_heartbeat);
   const errors=scheduleErrors(value);
   if(errors.length)summary.textContent+=' · 异常：'+errors.join('；');
   summary.title=JSON.stringify(value.subsystem_errors||{});
  }
  if(!rich)return;
  q('#scheduleState').textContent=`实际自动扫描：${value.scan.enabled?'已开启，每'+intervalLabel(value.scan.intervalHours):'关闭'} · 自动补齐：${value.collection?.recoveryEnabled?'已开启':'关闭'} · 下次执行：${time(value.next_due)} · ${value.scan.timezone} · 服务${value.scheduler_running?'运行中':'未运行'} · B观察：${(value.observe&&value.observe.enabled)?'已开启':'关闭'} · 收盘偏移${(value.timing&&value.timing.closeOffsetSec)!=null?value.timing.closeOffsetSec:120}秒 · B下次执行：${time(value.observe_next_due)}`;
  if(value.scheduler_error)q('#scheduleState').textContent+=' · 调度异常：'+value.scheduler_error;
 }
 const controls = document.createElement('div');controls.className=rich?'row':'controls';controls.id='liveScanControls';
 controls.innerHTML='<button id="startLiveScan" class="primary" disabled>开始全市场扫描</button>'+(rich?'<button id="readSavedScan" disabled>读取最新快照</button>':'')+'<span id="liveScanStatus" role="status" aria-live="polite">正在连接本地扫描服务…</span><div id="liveScanProgressWrap" hidden style="flex:1;min-width:220px;max-width:420px"><div style="display:flex;justify-content:space-between;gap:8px;font-size:12px;margin-bottom:4px"><strong id="liveScanProgressText">—</strong><span id="liveScanProgressTime" class="muted">—</span></div><div style="height:8px;background:var(--line,#e5e7eb);border-radius:999px;overflow:hidden"><div id="liveScanProgressBar" style="height:100%;width:0%;background:var(--accent,#0f766e);transition:width .3s"></div></div></div>';
 (rich?(q('[data-view="market"]')||q('.topline')):q('header')).append(controls);
 // Progress belongs to the market page from its first render, independently of tracking reads.
 const progressHost=q('#u-introStatus');if(rich&&progressHost){progressHost.append(q('#liveScanProgressWrap'));controls.hidden=true;}
 if(rich&&progressHost){const label=document.createElement('small');label.id='collectionRecoveryStatus';label.setAttribute('role','status');progressHost.append(label);}
 if(progressHost){const summary=document.createElement('small');summary.id='liveScheduleSummary';summary.style.display='block';progressHost.append(summary);}
 if(rich){
  q('[data-task-view="jobs"] .panel').innerHTML='<div class="panel-head"><div><h2>数据任务</h2><small>读取快照只访问本地文件；开始扫描才访问公开行情源。行情任务不自动调用AI或操作模拟账本。</small></div><button id="refreshLiveTasks">刷新任务状态</button></div><p id="liveTaskSummary" class="muted"></p><div class="tablewrap"><table><thead><tr><th>任务 / 来源</th><th>状态 / 阶段</th><th>进度</th><th>开始 / 完成</th><th>输出 / 错误</th><th>操作</th></tr></thead><tbody id="liveTaskRows"></tbody></table></div><div class="note">任务和计划保存在本机SQLite。暂停计划后不再发起该计划的下一次HTTP请求；已取得数据保留，目标待续接。服务重启后原中断任务保留失败身份，已授权目标另建续接任务；连续缺口合并补采。设备需运行本地服务、联网且未休眠。</div>';
  const schedule=q('[data-setting-view="schedule"]');
  q('#scheduleToggle').parentElement.lastChild.textContent='启用自动扫描（保存后生效）';
  q('#scanInterval').innerHTML='<option value="0.25">每15分钟（测试）</option><option value="4">每4小时</option><option value="1">每1小时</option><option value="8">每8小时</option><option value="24">每24小时</option>';
  const selects=schedule.querySelectorAll('select');selects[1].id='scanTimezone';selects[1].innerHTML=['Asia/Shanghai','America/Los_Angeles','UTC'].map(v=>`<option value="${v}">${v}</option>`).join('');
  const checks=schedule.querySelectorAll('input[type="checkbox"]');
  checks[1].disabled=true;checks[1].checked=false;checks[1].parentElement.lastChild.textContent='持仓自动补查和资金结算（账本未接入；手动行情补查见数据与任务）';
  checks[2].id='recoverMissedScan';checks[2].parentElement.lastChild.textContent='服务重启后合并补齐错过的数据，不重复逐轮全市场扫描';
  schedule.querySelector('.panel').insertAdjacentHTML('beforeend','<div class="row" style="gap:16px;flex-wrap:wrap;margin:8px 0"><label><input type="checkbox" id="observeEnabled"> 1H采集＋B策略（与4H计划分开）</label><label>收盘偏移秒 <input type="number" id="closeOffsetSec" min="0" max="600" value="120" style="width:4.5em"></label><span class="muted">手动一次扫描含4H与1H；15分钟选项调整市场采集频率；B仍按闭合1H运行，策略仍使用闭合1H/4H数据。</span></div>');
  schedule.querySelector('.panel').insertAdjacentHTML('beforeend','<div class="command-bar"><span id="liveSettingsVersion">尚未读取配置</span><div class="row"><button id="reloadScanSettings">重新读取扫描设置</button><button id="saveScanSettings" class="primary" disabled>保存扫描设置</button></div></div>');
  q('#scheduleToggle').onchange=()=>{q('#liveSettingsVersion').textContent='有未保存修改；当前实际状态以服务端计划为准。';};
  ['scanInterval','scanTimezone','recoverMissedScan','observeEnabled','closeOffsetSec'].forEach(id=>{const el=q('#'+id);if(el)el.addEventListener('input',q('#scheduleToggle').onchange);});
  schedule.querySelector('.panel').insertAdjacentHTML('beforeend','<div class="row" style="gap:16px;margin:8px 0"><button id="dualCyclePreset">设置1H＋4H定时方案</button><label><input type="checkbox" id="autoRecoverSources"> 自动补齐来源缺口（有界预算）</label><span class="muted">预设：1H每小时、4H每4小时；点击保存后才生效。</span></div>');
  q('#dualCyclePreset').onclick=()=>{q('#scheduleToggle').checked=true;q('#scanInterval').value='4';q('#observeEnabled').checked=true;q('#closeOffsetSec').value='120';q('#autoRecoverSources').checked=true;q('#scheduleToggle').onchange();};
  q('#autoRecoverSources').onchange=q('#scheduleToggle').onchange;
  q('#saveScanSettings').onclick=saveSettings;q('#reloadScanSettings').onclick=()=>loadSettings().catch(showError);
  q('#refreshLiveTasks').onclick=()=>poll().catch(showError);
  q('#liveTaskRows').onclick=async e=>{const b=e.target.closest('[data-cancel-task]');if(!b)return;b.disabled=true;try{await api('/api/tasks/'+b.dataset.cancelTask+'/cancel','POST',{});await poll();}catch(error){showError(error);b.disabled=false;}};
  q('#readSavedScan').onclick=()=>readSnapshot(true).catch(showError);
  q('[data-task-view="history"] .panel').innerHTML='<h2>网页扫描任务历史</h2><div id="liveScanHistory" class="muted">正在读取…</div><div class="note">列出最近50个网页任务中的成功发布记录。旧版样本与CLI归档不会伪造成网页任务；当前保存快照见页面顶部。磁盘 revision 浏览与只读价格索引见「区间行情补查」页签（DATA-01）；手动行情补查也在该页签。</div><h3 style="margin-top:18px">磁盘快照 revision（只读）</h3><div id="diskSnapshotList" class="muted">正在读取…</div>';
 }
 function updateProgressMeter(task){
  const wrap=q('#liveScanProgressWrap');
  if(!wrap)return;
  if(!task||!['queued','running'].includes(task.status)){wrap.hidden=true;return;}
  wrap.hidden=false;
  const done=Number(task.progress?.completed)||0, total=Number(task.progress?.total)||0;
  const pct=total>0?Math.min(100,Math.round(done*100/total)):(task.status==='queued'?0:5);
  q('#liveScanProgressBar').style.width=pct+'%';
  q('#liveScanProgressText').textContent=(task.kind==='b_observe_1h'?'B观察1H · ':'常规4H · ')+done+' / '+(total||'—')+(total>0?' · '+pct+'%':' · 等待目录');
  const started=task.started_at?new Date(task.started_at):null;
  const elapsed=started&&!Number.isNaN(started.getTime())?(Date.now()-started.getTime()):null;
  let timeText=task.status==='queued'?'排队等待中':('已用 '+formatDuration(elapsed));
  if(elapsed!=null&&done>0&&total>done)timeText+=' · 预计剩余 '+formatDuration(elapsed*(total-done)/done);
  if(started)timeText+=' · 开始 '+time(task.started_at);
  q('#liveScanProgressTime').textContent=timeText;
 }
 function showError(error){message('操作失败：'+error.message);if(rich)toast(error.message);}
 function renderTasks(){
  const active=currentScanTask();
  q('#startLiveScan').disabled=!connected||busy;q('#startLiveScan').textContent=active?'查看在途扫描':'开始全市场扫描';notifyScan();
  if(rich){
   q('#liveTaskSummary').textContent=`最近${rows.length}个任务 · 时间显示 ${scanTimezone} · 本地状态查询不调用AI`;
   q('#liveTaskRows').innerHTML=rows.map(t=>`<tr><td>${escape(t.kind==='ai_research'?'AI投研报告':t.kind==='candle_range'?'区间行情补查':t.kind==='b_observe_1h'?'B观察1H':t.kind==='b_evidence_repair'?'B证据缺口补采':t.kind==='b_cap_refresh'?'B边界前市值归档':'全市场扫描')}<small>${escape(t.id)}</small><small>${escape({evidence_gap_recovery:'缺项自动补采',manual:'手动',scheduled:'定时',recovery:'重启补扫',manual_b_observe:'手动B观察',manual_unified:'手动统一扫描',scheduled_b_observe:'自动B观察',boundary_resume:'已登记边界续接',test_countdown:'15分钟测试'}[t.reason]||t.reason)} · 设置v${escape(t.settings_version)}</small></td><td>${escape(names[t.status]||t.status)}<small>${escape(t.progress?.phase)}</small></td><td>${escape(progressText(t))}</td><td>${escape(time(t.started_at||t.created_at))}<small>${escape(time(t.finished_at))}</small></td><td>${t.result?(['b_evidence_repair','b_cap_refresh'].includes(t.kind)?`补采请求 ${escape(t.result.requests??0)}<small>处理 ${escape(t.result.processed??t.result.recorded??0)} · 保留缺口 ${escape(JSON.stringify(t.result.queue?.counts||{}))}</small>`:t.kind==='b_observe_1h'?`目标 ${escape(time(t.result.target_close_boundary))}<small>${escape(progressText(t))} · 采集错误/缺项 ${escape((t.result.errors||[]).length+(t.result.missing||[]).length)}</small><small>${escape(t.result.coverage_status==='recorded'?'覆盖水位已记录，完整资格须另核对':t.result.coverage_status==='incomplete'?'归档或判定仍有未解决范围':'历史轮次：完整覆盖尚未核验')}</small>`:t.kind==='candle_range'?`${escape(t.result.range_id)}<small>已收盘K线 ${escape(t.result.known)}/${escape(t.result.expected)}</small>${escape(t.error||'')}`:`${escape(t.result.scan_id)} / r${escape(t.result.revision)}<small>取得${escape(t.result.received)}/${escape(t.result.total)}</small>${t.result.capCoverage?`<small>市值 ${escape(t.result.capCoverage.required_trusted_value)}/${escape(t.result.capCoverage.required_contracts)}</small>`:''}`):escape(t.error||'尚无输出')}</td><td>${t.status==='queued'?`<button data-cancel-task="${escape(t.id)}">取消排队</button>`:'—'}</td></tr>`).join('')||'<tr><td colspan="6">暂无任务。加载页面不会自动扫描；请使用顶部“开始全市场扫描”。</td></tr>';
   q('#liveScanHistory').innerHTML=rows.filter(t=>t.kind==='scan'&&t.result).map(t=>`<p>${escape(time(t.finished_at))} · ${escape(names[t.status])} · ${escape(t.result.scan_id)} / r${escape(t.result.revision)} · ${escape(t.result.received)}/${escape(t.result.total)}</p>`).join('')||'暂无网页采集历史；当前仍可浏览已保存样本。';
  }
  window.dispatchEvent(new CustomEvent('workbench-tasks',{detail:rows}));
  return rows.find(t=>['queued','running'].includes(t.status));
 }
 async function poll(){
  clearTimeout(timer);
  try{
   const data=await api('/api/tasks');rows=data.tasks;operation=data.scan_operation||null;
   api('/api/settings').then(value=>{actualConfig=value;renderActualSettings(value);}).catch(()=>{});
   api('/api/collection/status').then(value=>{
    const label=q('#collectionRecoveryStatus');if(!label)return;
    const pending=(value.b_replay_counts||[]).filter(r=>r.state==='pending').reduce((n,r)=>n+r.count,0);
    const incomplete=(value.b_replay_counts||[]).filter(r=>r.state==='evaluated_incomplete').reduce((n,r)=>n+r.count,0);
     const effective=value.effective?.status==='ready'?value.effective:null;
     const open=(effective?.source_counts||value.source_counts||[]).filter(r=>!['evaluated','resolved','complete','cancelled'].includes(r.state));
     const needs=open.reduce((n,r)=>n+r.count,0),blocked=open.filter(r=>r.state.startsWith('blocked')).reduce((n,r)=>n+r.count,0);
     const bState=(value.b_replay_counts||[]).length?'B重算待处理 '+pending+'，证据仍未齐 '+incomplete:'B重算尚无恢复批次';
     const effectWait=value.last?.effects_pending?(' · 业务重算待补数据 '+value.last.effects_pending+' 项（本次处理）'):'';
      const reasonNames={cap_history_provider_not_configured:'缺可信历史市值来源',unsupported_quote_conversion:'报价币换算不支持',contract_identity_unverified:'来源身份未核验',contract_metadata_outside_history:'历史身份时间范围不足',coin_multiplier_unknown:'历史合约乘数未核验'};
      const reasons=(effective?.mapping_blockers||[]).slice(0,3).map(r=>(reasonNames[r.reason]||r.reason)+' '+r.count+'项').join('；');
      const archived=open.filter(r=>r.state==='archived_awaiting_effect').reduce((n,r)=>n+r.count,0);
      const outside=(effective?.outside_automatic_window||[]).reduce((n,r)=>n+r.count,0);
      label.textContent='自动补齐'+(value.policy.recoveryEnabled?'已开启':'关闭')+' · '+(effective?'去重来源需求 ':'原始登记（去重统计准备中） ')+needs+' 项；阻塞 '+blocked+'，已归档待业务生效 '+archived+(effective?' · 受影响 '+effective.affected_contracts+' 份合约；历史登记已接续 '+effective.legacy_bridged+' 项':'')+(outside?' · 超自动'+effective.automatic_window_hours+'小时窗口 '+outside+' 项，保留待历史修复':'')+(reasons?' · '+reasons:'')+(value.local_archived_pages?' · 本地归档复用 '+value.local_archived_pages+' 批（待业务生效另计）':'')+' · '+bState+effectWait+(effective?' · 来源统计 '+time(effective.as_of_ms):'')+(value.last?' · 最近处理 '+time(value.last.at_ms):'');
   }).catch(()=>{const label=q('#collectionRecoveryStatus');if(label)label.textContent='补齐状态暂不可读；当前行情与采集进度保留';});
   const state=actualConfig||config;if(state?.scan){scanTimezone=state.scan.timezone;renderActualSettings(state);}
   connected=true;renderTasks();
   const scanActive=rows.find(t=>t.kind==='scan'&&['queued','running'].includes(t.status));
   const otherActive=rows.find(t=>t.kind!=='scan'&&['queued','running'].includes(t.status));
   const anyActive=scanActive||otherActive||rows.find(t=>['queued','running'].includes(t.status));
   updateProgressMeter(currentScanTask());
   if(operation&&(!currentScanTask()||operationTasks().some(t=>t.id===currentScanTask().id)))message(operationStatus());
   else if(scanActive&&otherActive)message(progressLabel(scanActive)+'；另有：'+progressLabel(otherActive));
   else if(anyActive)message(progressLabel(anyActive));
   else if(rows.length)message(`最近数据任务：${names[rows[0].status]} · ${time(rows[0].finished_at)}`);
   else message('扫描服务已连接 · 尚无任务 · 自动扫描'+(state?.scan?.enabled?'已开启':'关闭'));
   if(state?.scheduler_error)message('调度异常：'+state.scheduler_error);
   const completed=rows.find(t=>t.kind==='scan'&&t.result);
   if(completed&&completed.id!==seenOutput&&!snapshotRead&&Date.now()-snapshotAttemptAt>15000){
    snapshotAttemptAt=Date.now();readSnapshot().then(()=>{seenOutput=completed.id;}).catch(error=>{const meta=q('#scanMetaLine');if(meta)meta.textContent='保存快照读取失败，保留当前行情：'+error.message;});
   }
   const bCompleted=rows.find(t=>t.kind==='b_observe_1h'&&t.result);
   if(bCompleted&&bCompleted.id!==seenBOutput){seenBOutput=bCompleted.id;window.dispatchEvent(new Event('workbench-b-observe'));}
   timer=setTimeout(()=>poll().catch(showError),anyActive?2000:30000);
  }catch(error){connected=false;renderTasks();message('任务状态读取暂不可用，保留上次进度与行情；正在重试。');timer=setTimeout(()=>poll().catch(showError),currentScanTask()?3000:30000);}
 }
 async function startScan(options={}){
  const stay=options.stayOnPage===true;
  busy=true;renderTasks();
  try{let requestKey=sessionStorage.getItem('pending-unified-scan');if(!requestKey){requestKey=crypto.randomUUID();sessionStorage.setItem('pending-unified-scan',requestKey);}const response=await fetch('/api/scans',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':requestKey},body:'{}'});const result=await response.json();if(!response.ok)throw new Error(result.error||'提交扫描失败');sessionStorage.removeItem('pending-unified-scan');operation=result.operation||null;message((result.reused?'复用在途任务：':'已创建真实采集任务：')+result.task.id);if(rich&&!stay){page('tasks');setTab('task','jobs');}await poll();}catch(error){showError(error);}finally{busy=false;renderTasks();}
 }
 async function loadSettings(){
  config=await api('/api/settings');actualConfig=config;scanTimezone=config.scan.timezone;
  if(rich){
   q('#scheduleToggle').checked=config.scan.enabled;q('#scanInterval').value=String(config.scan.intervalHours);q('#scanTimezone').value=config.scan.timezone;if(q('#observeEnabled'))q('#observeEnabled').checked=!!(config.observe&&config.observe.enabled);if(q('#closeOffsetSec'))q('#closeOffsetSec').value=String((config.timing&&config.timing.closeOffsetSec)!=null?config.timing.closeOffsetSec:120);q('#recoverMissedScan').checked=config.scan.recoverMissed;
   if(q('#autoRecoverSources'))q('#autoRecoverSources').checked=!!config.collection?.recoveryEnabled;
   q('#saveScanSettings').disabled=false;q('#readSavedScan').disabled=false;
   q('#liveSettingsVersion').textContent=`扫描配置v${config.version} · 已保存 · ${time(config.updated_at)}`;
   renderActualSettings(config);
  }
 }
 async function saveSettings(){
  if(!config)return;q('#saveScanSettings').disabled=true;
  try{
   const offset=q('#closeOffsetSec');if(offset&&(!/^\d+$/.test(offset.value)||!Number.isInteger(Number(offset.value))||Number(offset.value)>600))throw Error('收盘偏移须为0～600的整数秒；输入已保留');
   const payload={version:config.version,scan:{enabled:q('#scheduleToggle').checked,intervalHours:Number(q('#scanInterval').value),timezone:q('#scanTimezone').value,recoverMissed:q('#recoverMissedScan').checked}};if(q('#observeEnabled')||q('#closeOffsetSec')){payload.timing={closeOffsetSec:Number((q('#closeOffsetSec')||{value:120}).value),schema_version:1};payload.observe=Object.assign({schema_version:1,enabled:false,strategy_id:'B_ACCUMULATION_1H',rule_version:'b-accum-1h-v1',baseline_bars:168,event_ratio:8.0,confirm_ratio:2.0,max_abs_return:0.03,evidence_profile:'binance-1h-oi-lsr-v1',oi_lsr_period:'1h',oi_lsr_max_lag_ms:300000},config.observe||{},{enabled:q('#observeEnabled')?q('#observeEnabled').checked:!!(config.observe&&config.observe.enabled)});}payload.collection=Object.assign({},config.collection||{schema_version:1,recoveryEnabled:false,maxHttpAttempts:6000,deadlineSeconds:1800,recoveryWindowHours:48,recoveryAttempts:16,sampleCaptureEnabled:true,maxSampleBytes:134217728},{recoveryEnabled:!!q('#autoRecoverSources')?.checked});await api('/api/settings','PUT',payload);
   await loadSettings();toast('扫描配置已保存并生效；AI与模拟配置有各自保存入口。');
  }catch(error){q('#liveSettingsVersion').textContent='保存未成功：'+error.message+'；输入保留，实际配置未改变。';showError(error);}finally{q('#saveScanSettings').disabled=false;}
 }
 let snapshotRead=null,snapshotAttemptAt=0;
 function readSnapshot(notify=false){
  if(snapshotRead)return snapshotRead;
  snapshotRead=readSnapshotNow(notify).finally(()=>{snapshotRead=null;});return snapshotRead;
 }
 async function readSnapshotNow(notify=false){
  if(!rich){const reload=q('#reload');if(typeof reload?.onclick!=='function')throw new Error('扫描页尚未就绪');return reload.onclick();}
  const latest=await api('/api/scan');
  if(!Array.isArray(latest.rows)||!latest.rows.length)throw new Error('保存快照为空，保留当前视图');
  if(latest.registryHash&&latest.registryHash!==StrategyRegistry.hash)latest.ruleVersionNotice='保存快照使用旧注册表版本；各字段仍保留原采样时间及证据。';
  closeDrawer();
  Object.keys(snapshot).forEach(k=>delete snapshot[k]);Object.assign(snapshot,latest);
  if(typeof syncReserveSnapshot==='function')syncReserveSnapshot();
  if(typeof renderReserveTable==='function')renderReserveTable();
  const keys=new Set(snapshot.rows.map(key));(window.UnifiedWorkbench?.getEvidence()?.rows||[]).forEach(r=>keys.add(key(r)));[selectedTokens,customTokens,watchTokens].forEach(set=>{for(const k of set)if(!keys.has(k))set.delete(k);});
  snapshot.rows.forEach(r=>{r.ema99DistancePct=present(r.closed4h)&&present(r.ema4h?.[2])&&r.ema4h[2]>0?(r.closed4h/r.ema4h[2]-1)*100:null;});
  generatedSignature=null;previewSignature=null;q('#requestDetails').hidden=true;q('#guidedDraft').hidden=true;
  q('#generatedReport').className='empty';q('#generatedReport').innerHTML='<strong>已读取保存快照</strong><p>请重新生成报告，避免沿用旧证据。</p>';
  marketPage=1;renderMarket();renderStrategyTemplates();updateReportSelection();searchTokens();
  const stats=q('[data-view="market"] .legacy-market .stats');stats.querySelector('.stat strong').textContent=`${snapshot.received??snapshot.rows.length} / ${snapshot.total??snapshot.rows.length}`;
  stats.querySelector('.stat small').textContent=Object.entries(snapshot.markets||{}).map(([m,n])=>`${n} ${m}`).join(' · ');
  stats.querySelectorAll('.stat strong')[3].textContent=`当前 ${snapshot.rows.filter(r=>r.smartMoneyStatus==='covered'&&Number.isFinite(r.smartMoneyNetInflowUsd)).length} / ${snapshot.rows.length}`;
  q('#reportLimit').max=String(snapshot.rows.length);
  const caps=snapshot.capCoverage;
  const capText=caps?` · 市值 ${caps.required_trusted_value}/${caps.required_contracts} · 排除${caps.excluded_contracts?.length||0}份`:'';
  q('.topline small').textContent=`保存快照：${time(snapshot.finishedAt)} · ${snapshot.received??snapshot.rows.length}份合约${capText} · ${snapshot.scan_id||'旧样本'} / r${snapshot.revision??'旧版'} · 非连续实时行情${snapshot.ruleVersionNotice?' · '+snapshot.ruleVersionNotice:''}`;
  window.dispatchEvent(new Event('workbench-snapshot'));
  if(notify)toast('已读取最新本地快照，没有启动新采集。');
 }
 async function loadDiskSnapshots(){
  if(!rich||!q('#diskSnapshotList'))return;
  try{
   const data=await api('/api/snapshots?limit=30');
   const names={success:'成功',partial_success:'部分成功',failed:'失败',legacy_import:'旧样本导入'};
   q('#diskSnapshotList').innerHTML=data.snapshots.map(s=>`<p>${escape(time(s.available_at))} · ${escape(s.scan_id||'无scan_id')} / r${escape(s.revision??'—')} · ${escape(names[s.status]||s.status||'—')} · ${escape(s.received)}/${escape(s.total)} · K线文件${escape(s.candle_files)} · <span class="pill">${escape(s.availability||'')}</span></p>`).join('')||'尚无已发布 revision；不伪造历史。';
  }catch(error){q('#diskSnapshotList').textContent='读取失败：'+error.message;}
 }
 async function loadCallHistory(){
  const panel=q('[data-task-view="usage"]');if(!panel)return;
  panel.innerHTML='<div class="panel"><h2>AI调用与成本</h2><p role="status">正在读取本系统保存的调用记录…</p></div>';
  try{
   const [data,ai]=await Promise.all([api('/api/ai/calls'),api('/api/ai/config')]);
   const calls=data.calls||[];
   panel.innerHTML='<div class="panel"><div class="panel-head"><h2>AI调用与成本</h2><button id="refreshAiCalls">刷新记录</button></div><p>当前模型：'+escape(ai.modelId||'未配置')+' · 最近 '+calls.length+' 条调用。仅查询本地记录，不调用模型。</p><div class="tablewrap"><table><thead><tr><th>时间 / 报告</th><th>模型 / 阶段</th><th>状态</th><th>Token</th><th>耗时</th></tr></thead><tbody>'+calls.map(c=>'<tr><td>'+escape(time(c.created_at||c.at||c.timestamp))+'<small>'+escape(c.report_id||c.task_id||'')+'</small></td><td>'+escape(c.modelId||c.model||'未记录')+'<small>'+escape(c.phase||c.stage||'成文')+'</small></td><td>'+escape(names[c.status]||c.status||'未记录')+'</td><td>'+escape(c.usage?.total_tokens??'未知')+'</td><td>'+escape(c.duration_ms==null?'未记录':formatDuration(c.duration_ms))+'</td></tr>').join('')+(calls.length?'':'<tr><td colspan="5">暂无本系统调用记录</td></tr>')+'</tbody></table></div><p class="note">Token仅按服务商实际返回展示，缺失不填0。费用尚无完整可核算价格和工具账单，未估造金额；聊天中的分析不计作本地API调用。</p></div>';
   q('#refreshAiCalls').onclick=loadCallHistory;
  }catch(e){panel.innerHTML='<div class="panel"><h2>AI调用与成本</h2><p role="alert">读取失败：'+escape(e.message)+'</p><button id="refreshAiCalls">重试</button></div>';q('#refreshAiCalls').onclick=loadCallHistory;}
 }
 if(rich)document.addEventListener('click',e=>{if(e.target.closest('[data-task="usage"]'))loadCallHistory();});
 window.addEventListener('workbench-task-created',()=>poll().catch(showError));
 window.WorkbenchScan=Object.freeze({start:()=>startScan({stayOnPage:true}),state:scanState});
 q('#startLiveScan').onclick=()=>startScan();
 if(rich)q('.topline .pill.gray').textContent='本地工作台已连接';
 // Auxiliary read failures must not relabel an ongoing scan as an operation failure.
 Promise.allSettled([
  loadSettings().catch(error=>{const field=q('#liveSettingsVersion');if(field)field.textContent='扫描设置读取暂不可用：'+error.message;}),
  poll().catch(showError),
  ...(rich?[readSnapshot().catch(error=>{const field=q('#scanMetaLine');if(field)field.textContent='保存快照暂不可读，保留已显示行情：'+error.message;}),loadDiskSnapshots()]:[])
 ]).then(results=>{for(const result of results)if(result.status==='rejected')console.warn('辅助读取失败，保留市场数据与任务进度',result.reason);});
})();
