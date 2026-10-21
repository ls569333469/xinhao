/* Backend DTO is authoritative; no indicator calculation or trading actions. */
(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const state = {view:null, cursor:null, selected:null, epoch:0, requests:new Map(), pages:{},latencies:[]};
  const labels = {pending:"等待确认",confirmed:"已确认",void:"原确认失效",blocked:"资格待核验",technical_only:"仅技术事件",pass:"通过",fail:"未满足",unknown:"未知",observing:"持续观察",ended:"已结束",paused_awaiting_data:"待补数据",ok:"完整",incomplete:"有缺口",source_revision_superseded:"来源更正，旧观察保留",waiting:"等待确认",expired:"原周期已到期",invalidated:"原周期失效",confirm_failed:"后续确认未满足",structure_invalid:"结构破坏",human_stop:"人工结束",session_started:"开始观察",missing_candles:"缺少K线",backfilled:"数据已补齐"};
  const label = x => labels[x] || x || "—";
  Object.assign(labels,{pending_authorization:'等待恢复授权',blocked_permission:'来源权限受限',blocked_configuration:'来源配置待核验',unavailable_history:'历史来源不可得',backoff:'退避待重试',running:'正在恢复来源',archived_awaiting_effect:'已归档，等待逐根评价',evaluated:'已评价',not_required:'该消费者无需补证',awaiting_effect:'待消费者评价',no_registered_gap:'未登记来源缺口',not_initialized:'尚无恢复记录'});
  const time = ms => ms == null ? "—" : new Date(ms).toLocaleString("zh-CN", {timeZone:"Asia/Shanghai",hour12:false});
  const text = (id,value) => {$(id).textContent=value;};
  function clearSelection() {
    state.selected=null;state.pages={};
    ["detail-header","detail-evidence","detail-transitions","detail-revisions","detail-gaps"].forEach(id=>text(id,"正在读取…"));
    ["btn-next-tr","btn-next-rev","btn-next-gap"].forEach(id=>$(id).disabled=true);
    $("detail").classList.add("hidden");$("detail-empty").classList.remove("hidden");
    ["csv","json","report"].forEach(x => $("btn-export-"+x).disabled=true);
    text("export-preview","尚未导出");state.exportCursor=null;$("btn-export-next").disabled=true;
  }
  async function api(scope, method, url, body) {
    state.requests.get(scope)?.abort();
    const control=new AbortController();state.requests.set(scope,control);
    const epoch=state.epoch,selected=state.selected;
    const began=performance.now();
    try {
      const response=await fetch(url,{method,signal:control.signal,headers:body ? {"Content-Type":"application/json"}:{},body:body ? JSON.stringify(body):undefined});
      const data=await response.json();
      if(epoch!==state.epoch || control!==state.requests.get(scope) || (scope!=="list" && scope!=="view" && selected!==state.selected)) return null;
      if(!data.ok) throw new Error(response.status===409 ? "记录已更新，请重新载入。" : (data.errors || []).map(x=>x.code).join("、"));
      return data;
    } catch(error) {
      if(error.name!=="AbortError") text("view-meta",error.message || "读取失败，请重试。");
      return null;
    } finally {
      state.latencies.push(performance.now()-began);
      if(state.latencies.length>200)state.latencies.shift();
    }
  }
  async function openView() {
    state.epoch++;state.requests.forEach(x=>x.abort());clearSelection();state.cursor=null;
    const filters={};for(const [field,id] of Object.entries({strategy:'filter-strategy',observation_status:'filter-observation',history:'filter-history',symbol:'filter-symbol',market:'filter-market'})){const v=$(id).value.trim();if(v)filters[field]=v;}
    if(filters.symbol)filters.symbol=filters.symbol.toUpperCase();
    const data=await api("view","POST","/api/tracking/read_view",{filters});if(!data)return;
    state.view=data.read_view;$("btn-reload").disabled=false;
    text("view-meta","固定记录视图 · 数据不自动更新 · 扫描与来源时间见各记录");await list();
  }
  async function list() {
    if(!state.view)return;clearSelection();
    const q=new URLSearchParams({read_view:state.view,limit:"5",...(state.cursor || {})});
    const data=await api("list","GET","/api/tracking/events?"+q);if(!data)return;
    const tbody=$("events-table").querySelector("tbody");tbody.replaceChildren();
    for(const item of data.items) {
      const row=document.createElement("tr");row.tabIndex=0;row.dataset.eventId=item.event_id;
      const cells=[item.strategy+" · "+item.market+" "+item.symbol,time(item.decision_close_ms),label(item.original_confirmation?.technical_status),label(item.qualification?.status),label(item.observation?.status),label(item.data_quality?.summary),item.historical_import?"历史形成":"前向记录"];
      for(const value of cells){const td=document.createElement("td");td.textContent=value;row.append(td);}
      row.addEventListener("click",()=>select(item.event_id,row));row.addEventListener("keydown",e=>{if(e.key==="Enter")select(item.event_id,row);});tbody.append(row);
    }
    state.next=data.next_cursor;$("btn-next").disabled=!state.next;
    text("page-info","本页 "+data.items.length+" 条"+(state.next?" · 可继续翻页":" · 末页"));
  }
  async function select(id,row) {
    clearSelection();state.selected=id;state.pages={};
    $("events-table").querySelectorAll("tr").forEach(x=>x.classList.remove("selected"));row.classList.add("selected");
    $("detail-empty").classList.add("hidden");$("detail").classList.remove("hidden");
    const q=new URLSearchParams({event_id:id,read_view:state.view});
    const data=await api("header","GET","/api/tracking/event?"+q);if(!data)return;
    const h=data.header;
    text("detail-header",`${h.strategy} · ${h.market} ${h.symbol}\n原确认：${label(h.original_confirmation?.technical_status)}\n完整资格：${label(h.qualification?.status)}\n后续观察：${label(h.observation?.status)}\n检查截至：${time(h.observation?.last_evaluated_bar_close_ms)}\n结束原因：${label(h.observation?.end_reason)}\n数据质量：${label(h.data_quality?.summary)}\n来源：${h.historical_import?"历史形成，不作为实时机会":"前向记录"}`);
    const recovery=h.data_quality?.recovery||{};
    const qualityLines=['来源恢复：'+label(recovery.status)+' · '+(recovery.enabled?'允许按授权恢复':'恢复已关闭'),
      '质量记录时间：'+time(recovery.as_of_ms)+' · 下次重试：'+time(recovery.next_retry_ms),
      ...(recovery.source_counts||[]).map(x=>x.source+' · '+(x.period_ms?x.period_ms/60000+'分钟':'待核验周期')+' · '+label(x.state)+'：'+x.n+' 条获取需求'),
      ...(recovery.consumer_counts||[]).map(x=>x.consumer+' · '+label(x.state)+'：'+x.n+' 条消费者依赖'),
      '需求条数不等于缺失样本数；原任务完成状态、完整资格和技术观察分别保留。'];
    $('detail-header').textContent+='\n'+qualityLines.join('\n');
    const ctx=h.context||{},c=ctx.cycle||{},b=ctx.prepared_t0||{},st=b.structure||{};
    const value=x=>x==null?'未知':String(x);
    const sourceLine=(label,s)=>label+'：'+(s?value(s.value)+' '+value(s.unit)+' · 数据时点 '+time(s.effective_at_ms)+' · 首次可得 '+time(s.available_at_ms):'同期证据缺失');
    text('detail-evidence',h.strategy==='C' ? [
      'T0：'+time(b.open_ms)+' · 确认截止 T+10',
      'T0 RSI14：'+value(b.rsi14)+'（要求 <25）',
      'T0 收盘：'+value(b.c)+' · EMA7/25/99：'+[b.ema7,b.ema25,b.ema99].map(value).join(' / '),
      '14根严格新低：'+value(ctx.t0_new_lows14)+'（≤2；相等不计）',
      'T0 OI/可信市值：'+value(b.oi_cap_pct)+'%（要求 >30%） · LSR：'+value(b.lsr)+'（要求 <1.5）',
      c.status==='confirmed'
        ? '确认根：T+'+value(c.terminal_offset)+' · 相对前7根量比：'+value(c.confirm_vol_ratio)+'（要求 >3 且阳线）'
        : '周期状态：'+label(c.status)+(c.terminal_offset==null?'':' · 截至 T+'+value(c.terminal_offset))+' · 尚无有效确认根',
      ...(ctx.expired_followup?.latest ? ['到期后走势：'+time(ctx.expired_followup.latest.bar_close_ms)+' · 收盘 '+value(ctx.expired_followup.latest.close)+' · 相对T0 '+value(ctx.expired_followup.latest.return_from_t0_pct)+'%',
        '窗口外首次放量阳线：'+time(ctx.expired_followup.first_rebound?.bar_close_ms)+'（只记进展；原周期仍到期）'] : []),
      '固定观察低点：'+value(ctx.structure_reference?.value)+' · 收盘严格跌破才结束',
      sourceLine('T0 OI',st.oi_sample),sourceLine('T0 LSR',st.lsr_sample),sourceLine('T0 市值',st.cap_sample)
    ].join('\n') : '原确认与资格见本记录；信号修订和走势时间线各自保留，后续走势不改写原确认。');
    ["csv","json","report"].forEach(x=>$("btn-export-"+x).disabled=false);
    await Promise.all([page("transitions",true),page("revisions",true),page("gaps",true)]);
  }
  const panels={transitions:["detail-transitions","btn-next-tr"],revisions:["detail-revisions","btn-next-rev"],gaps:["detail-gaps","btn-next-gap"]};
  async function page(kind,reset) {
    if(!state.selected)return;
    if(reset)state.pages[kind]=null;
    const q=new URLSearchParams({event_id:state.selected,read_view:state.view,limit:"3",...(state.pages[kind] || {})});
    if(kind==="transitions")q.set("view","effective");
    const data=await api(kind,"GET","/api/tracking/"+kind+"?"+q);if(!data)return;
    const lines=data.items.map(x=>kind==="transitions" ? `${time(x.at_bar_close_ms)} · ${label(x.to_state)} · ${label(x.reason)}` : kind==="revisions" ? `修订 ${x.revision} · ${label(x.technical_status)} · 资格${label(x.qualification_status)}` : `${x.scope} · ${x.status} · ${label(x.detail?.reason)}`);
    text(panels[kind][0],lines.join("\n") || "本页无记录");state.pages[kind]=data.next_cursor;$(panels[kind][1]).disabled=!data.next_cursor;
  }
  async function doExport(fmt,next=false) {
    if(!state.selected)return;
    const data=await api("export","POST","/api/tracking/exports",{event_id:state.selected,read_view:state.view,fmt,...(next?state.exportCursor||{}:{})});if(!data)return;
    text("export-preview",fmt==="csv" ? data.csv : JSON.stringify(data.rows||data.body,null,2));state.exportFmt=fmt;state.exportCursor=data.next_cursor||data.body?.next_cursor;$("btn-export-next").disabled=!state.exportCursor;
    const link=document.createElement("a");link.href="/api/tracking/export?export_id="+encodeURIComponent(data.export_id);link.download="tracking."+(fmt==="report"?"html":fmt);link.textContent="下载 "+(fmt==="report"?"报告 HTML":fmt.toUpperCase());$("export-preview").append(document.createTextNode("\n"),link);
  }
  $("btn-export-next").addEventListener("click",()=>doExport(state.exportFmt,true));
  let noticeCursor=null,noticeEpoch=0;
  async function loadNotices(next=false){
    const epoch=++noticeEpoch;const q=new URLSearchParams(next?noticeCursor||{}:{});
    try{
      const response=await fetch('/api/tracking/notifications?'+q);const result=await response.json();
      if(epoch!==noticeEpoch)return;if(!result.ok)throw Error('状态提醒暂不可读');
      const box=$('notices');box.replaceChildren();
      for(const n of result.items){
        const line=document.createElement('div');line.className='notice';
        const title=document.createElement('p');
        const kind={observation_state:'走势状态变化',c_state_change:'C 周期变化',c_delayed_correction:'C 历史更正',b_original_state_change:'B 原确认更新',b_observation_state:'B 观察变化',b_s0_risk_only:'B S0风险提示',a_followup_volume:'A 后续量能'}[n.kind]||'信号状态变化';
        const identity=(n.event_id||'').split('|');
        title.textContent=kind+' · '+(identity.length>=4?[identity[0],identity[2],identity[3]].join(' '):(n.event_id||''))+' · '+label(n.state||n.original_status||n.reason)+' · '+n.created_at_utc+(n.delayed?' · 延迟确认':'');line.append(title);
        const button=document.createElement('button');button.textContent=n.read_at_utc?'已读':'标为已读';button.disabled=Boolean(n.read_at_utc);
        button.addEventListener('click',async()=>{button.disabled=true;try{const r=await fetch('/api/tracking/notifications/read',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids:[n.outbox_id]})});const d=await r.json();if(!d.ok)throw Error();button.textContent='已读';}catch{button.disabled=false;button.textContent='重试标为已读';}});line.append(button);box.append(line);
      }
      if(!result.items.length)box.textContent='本页无提醒';
      noticeCursor=result.next_cursor;$('btn-notices-next').disabled=!noticeCursor;
      const ids=result.items.filter(n=>!n.delivered_at_utc).map(n=>n.outbox_id);
      if(ids.length){const r=await fetch('/api/tracking/notifications/delivered',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids})});const d=await r.json();if(!d.ok)throw Error('送达记录未保存，可重新载入重试');}
    }catch(error){if(epoch===noticeEpoch)$('notices').textContent=error.message||'状态提醒读取失败';}
  }
  $('btn-notices').addEventListener('click',()=>loadNotices(false));$('btn-notices-next').addEventListener('click',()=>loadNotices(true));
  $('btn-runtime').addEventListener('click',async()=>{
    const button=$('btn-runtime');button.disabled=true;
    try{
      const response=await fetch('/api/tracking/runtime');const data=await response.json();
      if(!response.ok||!data.ok)throw Error('运行信息暂不可读');
      const control=data.control||{},metrics=data.metrics||{},last=data.last_tick||{};
      const latencies=state.latencies.slice().sort((a,b)=>a-b);
      const pct=q=>latencies.length?Math.round(latencies[Math.max(0,Math.ceil(latencies.length*q)-1)])+' ms':'—';
      const lines=['持续处理：'+(control.enabled?'已启用':'已关闭'),
        '来源获取：'+(control.network_enabled?'仅按有效授权和预算获取':'已关闭'),
        '最近运行：'+time(last.at_ms)+' · 耗时 '+(last.duration_ms==null?'—':Math.round(last.duration_ms)+' ms'),
        '待处理需求最老更新时间距今：'+(data.oldest_registered_pending_age_ms==null?'—':Math.round(data.oldest_registered_pending_age_ms/60000)+' 分钟'),
        '最近 '+metrics.samples+' 次处理耗时：P50 '+(metrics.p50_ms==null?'—':Math.round(metrics.p50_ms)+' ms')+' / P95 '+(metrics.p95_ms==null?'—':Math.round(metrics.p95_ms)+' ms'),
        '处理耗时不代表浏览器延迟；未运行时不显示虚构性能值。',
        '当前页面请求耗时：'+latencies.length+' 次 · P50 '+pct(.5)+' / P95 '+pct(.95)+'（含取消或失败的尝试）',
        ...(data.recovery_counts||[]).map(x=>x.source+' · '+label(x.state)+'：'+x.n+' 条获取需求'),
        '来源获取条数不等于缺失样本数，任务完成不表示覆盖完整。',
        '真实 A 发布确认样本：本页 '+(data.samples?.items||[]).length+' 条；资格由原规则和复核决定。'];
      $('runtime-status').textContent=lines.join('\n');
    }catch(error){$('runtime-status').textContent=error.message||'读取失败';}
    finally{button.disabled=false;}
  });
  const initial=new URLSearchParams(location.search);if(['A','B','C'].includes(initial.get('strategy')))$('filter-strategy').value=initial.get('strategy');if(initial.get('symbol'))$('filter-symbol').value=initial.get('symbol');if(initial.get('market'))$('filter-market').value=initial.get('market')==='USDⓈ-M'?'USDT-M':initial.get('market');
  $("btn-open-view").addEventListener("click",openView);$("btn-reload").addEventListener("click",openView);
  $("btn-next").addEventListener("click",()=>{state.cursor=state.next;list();});
  Object.entries(panels).forEach(([kind,ids])=>$(ids[1]).addEventListener("click",()=>page(kind,false)));
  ["csv","json","report"].forEach(fmt=>$("btn-export-"+fmt).addEventListener("click",()=>doExport(fmt)));
})();
