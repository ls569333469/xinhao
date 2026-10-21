/* Main-site tracking. Scoped DOM/state; no collection/model/trading requests. */
(()=>{'use strict';
 const panel=document.querySelector('[data-view="tracking"]');if(!panel)return;
 let root,ready=false,opening=null,active=false,epoch=0,version=null,view=null,offset=0,next=null,total=0;
 let tab='tracking',targetEvent=null,pendingDetail=null,listKey=null,sortDirection='desc',trackingData=null,trackingFilterKey=null;
 const savedTabs={tracking:{},history:{status:'all'}};
 let detail=null,detailOffset=0,detailNext=null,focusBefore=null,loadTimer=null,retryTimer=null;
 const requests=new Map(),labels={observing:'追踪中',paused_awaiting_data:'待补数据',ended:'已结束',pass:'通过',fail:'未通过',unknown:'待核',pending:'等待确认',confirmed:'已确认',blocked:'资格待核验',none:'未进入追踪',technical_only:'仅技术事件',confirm_failed:'确认未通过',expired:'原周期到期',waiting:'等待确认',invalidated:'周期失效',structure_invalid:'结构破坏',human_stop:'人工结束',source_revision_superseded:'来源更正，旧观察保留',session_started:'开始追踪'};
 const $=id=>root.querySelector('#tw-'+id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const time=ms=>ms==null?'—':new Date(ms).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
 const label=v=>labels[v]||v||'—',num=v=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:8}):'—';
 const pct=v=>typeof v==='number'&&Number.isFinite(v)?`<span class="${v>=0?'tw-up':'tw-down'}">${v>0?'+':''}${v.toFixed(2)}%</span>`:'—';
 function signalName(e){return String(e.signal_label||e.strategy||'').replace(/EMA(7|25|99)↑EMA(7|25|99)/g,'EMA$1上穿EMA$2');}
 function gradeName(e){return ({strong:'强信号',medium:'中信号',weak:'弱信号'})[e.technical_grade]||'等级待核';}

 // Each query/page gets its own baseline. Quotes and returns never create alerts.
 const updateScopes=new Map();let currentScope=null,currentRows=[];
 function gradeHtml(e){return `<span class="tw-grade" data-grade="${['weak','medium','strong'].includes(e.technical_grade)?e.technical_grade:'unknown'}">${esc(gradeName(e))}</span>`;}
 function rowId(row,historyView){return JSON.stringify(historyView?[row.market,row.symbol,row.event_id]:[row.market,row.symbol]);}
 function rowFact(row,historyView){
  const e=historyView?row:row.representative,o=e.observation||{};
  return {id:e.event_id,trigger:e.trigger_close_ms||0,count:historyView?1:row.event_count,
   evidence:JSON.stringify([e.signal_label,e.technical_grade,e.source_revision,e.rule_version]),
   state:JSON.stringify([e.original_confirmation,e.qualification,e.result_state,o.status,o.end_reason,historyView?null:[row.active_count,row.paused_count,row.ended_count]]),
   checked:o.last_evaluated_bar_close_ms||0};
 }
 function noticeFor(prev,next){
  if(next.count>prev.count||next.id!==prev.id&&next.trigger>prev.trigger)return '新增事件';
  if(next.id!==prev.id)return '展示事件变化';
  if(next.evidence!==prev.evidence)return '信号修订';
  if(next.state!==prev.state)return '状态更新';
  if(next.checked>prev.checked)return '检查推进';
  if(next.checked<prev.checked)return '检查水位回退';
  return null;
 }
 function observeUpdates(data,scope){
  currentScope=scope;currentRows=data.rows.map(r=>rowId(r,data.tab==='history'));
  let cache=updateScopes.get(scope);const first=!cache;
  if(!cache){cache=new Map();updateScopes.set(scope,cache);while(updateScopes.size>20)updateScopes.delete(updateScopes.keys().next().value);}
  for(const row of data.rows){
   const id=rowId(row,data.tab==='history'),fact=rowFact(row,data.tab==='history'),prev=cache.get(id);
   const notice=prev?(noticeFor(prev.fact,fact)||prev.notice):(first?null:'新进入列表');
   cache.set(id,{fact,notice});
  }
 }
 function rowNotice(id){return updateScopes.get(currentScope)?.get(id)?.notice||'';}
 function noticeHtml(id){const text=rowNotice(id);return `<small class="tw-update" ${text?'':'hidden'}>${esc(text)}</small>`;}
 function syncNotices(){
  const count=currentRows.filter(id=>rowNotice(id)).length;
  $('updates').hidden=!count;$('updatecount').textContent=`本页 ${count} 条新动态`;
  for(const row of $('rows').querySelectorAll('[data-tw-row]')){
   const text=rowNotice(row.dataset.twRow);row.classList.toggle('tw-updated',!!text);
   const badge=row.querySelector('.tw-update');if(badge){badge.textContent=text;badge.hidden=!text;}
  }
 }
 function acknowledge(market,symbol,event_id){
  // Read receipts are local presentation state, never writes to business ledgers.
  for(const cache of updateScopes.values())for(const [id,entry] of cache){const key=JSON.parse(id);if(key[0]===market&&key[1]===symbol&&(key.length===2||key[2]===event_id))entry.notice=null;}
  syncNotices();
 }
 function acknowledgePage(){const cache=updateScopes.get(currentScope);for(const id of currentRows)if(cache?.has(id))cache.get(id).notice=null;syncNotices();}


 const sortLabels={symbol:'代币 / 市场',price:'当前价格',change:'24H涨跌',volume:'24H成交额',grade:'信号等级',trigger:'触发时间',performance:'触发后表现',trend:'当前4H走势',state:'追踪状态',checked:'检查截至'};
 const compactMoney=v=>typeof v==='number'&&Number.isFinite(v)?(v>=1e8?v/1e8:v/1e4).toLocaleString('zh-CN',{maximumFractionDigits:2})+(v>=1e8?' 亿美元':' 万美元'):'未取得';
 function headCell(text,key){
  if(!key)return `<th scope="col">${esc(text)}</th>`;
  const selected=$('sort').value===key,dir=sortDirection==='asc'?'升序':'降序';
  return `<th scope="col" aria-sort="${selected?(sortDirection==='asc'?'ascending':'descending'):'none'}"><button type="button" class="tw-sort" data-tracking-sort="${key}" title="按${esc(sortLabels[key])}排序，点击切换升降序">${esc(text)} <span aria-hidden="true">${selected?(sortDirection==='asc'?'↑':'↓'):'↕'}</span><span class="tw-sr">${selected?'，当前'+dir:'，可排序'}</span></button></th>`;
 }
 function trackingQueryKey(){const f=filters();delete f.sort;delete f.direction;return JSON.stringify(f);}
 function sortValue(g,key){
  const q=g.quote_data||{},e=g.representative,o=e.observation||{};
  if(key==='symbol')return g.symbol;
  if(key==='price')return q.price;if(key==='change')return q.priceChange24Pct;if(key==='volume')return q.volume24Usd;
  if(key==='grade')return ({weak:1,medium:2,strong:3})[e.technical_grade];
  if(key==='trigger')return e.trigger_close_ms;if(key==='performance')return g.return_pct;
  if(key==='checked')return o.last_evaluated_bar_close_ms;
  if(key==='trend'){const a=q.ema4h;return Array.isArray(a)&&a.length===3&&a.every(Number.isFinite)?(a[0]>a[1]&&a[1]>a[2]?2:a[0]>a[1]?1:0):null;}
  if(key==='state')return g.active_count?(g.paused_count===g.active_count?0:g.paused_count?1:2):o.status==='ended'?-1:null;
  return null;
 }
 function sortTracking(rows){
  const key=$('sort').value,dir=sortDirection==='asc'?1:-1;
  return [...rows].sort((a,b)=>{
   const av=sortValue(a,key),bv=sortValue(b,key),am=av==null||typeof av==='number'&&!Number.isFinite(av),bm=bv==null||typeof bv==='number'&&!Number.isFinite(bv);
   if(am!==bm)return am?1:-1;
   const diff=am?0:typeof av==='string'?av.localeCompare(bv,'en',{numeric:true}):av-bv;
   return diff?diff*dir:JSON.stringify([a.symbol,a.market,a.representative.event_id]).localeCompare(JSON.stringify([b.symbol,b.market,b.representative.event_id]));
  });
 }
 function trackingPage(data,pageOffset){const rows=sortTracking(data.rows);return {...data,rows:rows.slice(pageOffset,pageOffset+50),total:rows.length,next_offset:pageOffset+50<rows.length?pageOffset+50:null};}
 async function readTracking(q,own){
  const first=await get('list','/api/tracking/workbench/list?'+q),rows=[...first.rows];let nextPage=first.next_offset;
  if(first.total>10000)throw Error('追踪列表超过读取上限');
  while(nextPage!=null){
   if(own!==epoch||!active)return null;
   const more=new URLSearchParams(q);more.set('view',first.view);more.set('offset',String(nextPage));
   const page=await get('list','/api/tracking/workbench/list?'+more);
   if(page.view!==first.view||page.version!==first.version||page.next_offset!=null&&page.next_offset<=nextPage)throw Error('追踪分页版本不一致');
   rows.push(...page.rows);if(rows.length>10000)throw Error('追踪列表超过读取上限');nextPage=page.next_offset;
  }
  if(rows.length!==first.total)throw Error('追踪分页记录不完整');
  return {...first,rows,next_offset:null};
 }
 function chooseSort(key){
  if(!Object.hasOwn(sortLabels,key)||tab==='history'&&!['symbol','trigger'].includes(key))return;
  sortDirection=$('sort').value===key?(sortDirection==='asc'?'desc':'asc'):'asc';$('sort').value=key;targetEvent=null;save();load({frozen:true});
 }

 function message(s){$('statusline').textContent=s;}
 function abort(scope){requests.get(scope)?.abort();requests.delete(scope);}
 async function get(scope,path){
  abort(scope);const controller=new AbortController();requests.set(scope,controller);let timedOut=false;
  const timeout=setTimeout(()=>{timedOut=true;controller.abort();},scope==='watch'?65000:scope==='list'?30000:15000);
  try{const response=await fetch(path,{cache:'no-store',signal:controller.signal});const data=await response.json();
   if(!response.ok||data.ok===false){const err=new Error((data.errors||[]).map(x=>x.code).join('、')||'读取失败');err.status=response.status;throw err;}
   return data;
  }catch(error){if(timedOut)throw new Error('读取超时');throw error;}
  finally{clearTimeout(timeout);if(requests.get(scope)===controller)requests.delete(scope);}
 }
 function filters(){return {tab,search:$('search').value.trim(),status:$('status').value,strategy:$('strategy').value,market:$('market').value,sort:$('sort').value,from:$('from').value,to:$('to').value,direction:sortDirection};}
 function save(){savedTabs[tab]=filters();try{localStorage.setItem('tracking-workbench-tabs-v2',JSON.stringify({tab,queries:savedTabs}));}catch{}}
 function applyTab(next,{loadNow=true,keep=true}={}){
  if(keep)save();tab=next;targetEvent=null;pendingDetail=null;closeDetail();
  const options=tab==='history'?[['all','全部历史记录'],['confirmed','已确认'],['pending','待确认'],['fail','未通过 / 失效'],['unknown','数据不足 / 资格待核']]:[['active','追踪中＋待补数据'],['paused','待补数据'],['ended','已结束'],['all','全部追踪记录']];
  $('status').innerHTML=options.map(([k,v])=>`<option value="${k}">${v}</option>`).join('');
  $('statuslabel').textContent=tab==='history'?'记录结果':'追踪范围';
  $('sort').innerHTML=Object.entries(sortLabels).filter(([k])=>tab==='tracking'||['trigger','symbol'].includes(k)).map(([k,v])=>`<option value="${k}">${v}</option>`).join('');
  const defaults={search:'',status:tab==='history'?'all':'active',strategy:'all',market:'all',sort:'trigger',from:'',to:''};
  for(const [key,value] of Object.entries({...defaults,...savedTabs[tab]}))if(['search','status','strategy','market','sort','from','to'].includes(key)){
   $(key).value=value;if($(key).tagName==='SELECT'&&$(key).selectedIndex<0)$(key).value=defaults[key];
  }
  sortDirection=savedTabs[tab].direction==='asc'?'asc':savedTabs[tab].direction==='desc'?'desc':$('sort').value==='symbol'?'asc':'desc';
  root.querySelectorAll('[data-tracking-tab]').forEach(b=>{const on=b.dataset.trackingTab===tab;b.setAttribute('aria-selected',String(on));b.tabIndex=on?0:-1;});
  $('content').setAttribute('aria-labelledby','tw-tab-'+tab);if(loadNow)load();
 }
 function resultName(e){return {confirmed:'已确认',pending:'待确认',fail:'未通过 / 失效',unknown:'数据不足 / 资格待核'}[e.result_state]||'待核';}
 function eventKey(e){return JSON.stringify([e.market,e.symbol,e.event_id]);}
 function renderHistory(data){
  $('heads').innerHTML='<tr>'+['代币 / 市场','触发策略 / 信号等级','触发K线','当时记录结果','追踪状态','记录来源','查看'].map((x,i)=>headCell(x,({0:'symbol',2:'trigger'})[i])).join('')+'</tr>';
  $('rows').innerHTML=data.rows.map(e=>{const key=esc(eventKey(e)),o=e.observation;const rid=rowId(e,true);return `<tr data-tw-row="${esc(rid)}" class="${targetEvent===e.event_id?'tw-selected':''} ${rowNotice(rid)?'tw-updated':''}"><td><button class="tw-coin" data-tracking-open="${key}">${esc(e.symbol)}</button><small>${esc(e.market==='USDT-M'?'USDⓈ-M':e.market)}</small>${noticeHtml(rid)}</td><td>${esc(signalName(e))}<small>${e.technical_grade||e.strategy==='A'?gradeHtml(e):'B/C沿用原确认结果'}</small></td><td>${esc(time(e.trigger_close_ms))}<small>${esc(e.timeframe)} · 收盘时点</small></td><td>${esc(resultName(e))}<small>技术：${esc(label(e.original_confirmation.technical_status))} / 资格：${esc(label(e.qualification.status))}</small></td><td>${esc(label(o.status||'none'))}<small>${e.session_id?'检查至 '+esc(time(o.last_evaluated_bar_close_ms)):'尚无追踪会话'}</small></td><td>${e.historical_import?'历史补登记':'开始监测后记录'}<small>${esc(e.source==='b_event_archive'?'B原始事件账本 · r'+(e.source_revision||'?'):'ABC追踪账本')}</small></td><td><button class="tw-link" data-tracking-open="${key}">信号详情</button>${e.session_id?`<small><button class="tw-link" data-tracking-jump="tracking" data-tracking-key="${key}">查看追踪 →</button></small>`:''}</td></tr>`;}).join('')||'<tr><td colspan="7">当前范围没有历史记录；可调整日期或重置筛选。</td></tr>';
 }
 function jump(next,key){const [market,symbol,event_id]=JSON.parse(key);applyTab(next,{loadNow:false});$('search').value=symbol;$('market').value=market;$('status').value='all';$('strategy').value='all';$('from').value='';$('to').value='';targetEvent=event_id;pendingDetail=key;load();}
 function trend(q){const e=q.ema4h;if(!Array.isArray(e)||e.length!==3||!e.every(Number.isFinite))return '待核';return e[0]>e[1]&&e[1]>e[2]?'多头排列持续':e[0]>e[1]?'EMA7＞EMA25':'EMA7≤EMA25';}
 function trendHtml(q){const text=trend(q);return text==='多头排列持续'?'<strong class="tw-long-stack">'+text+'</strong>':esc(text);}
 function render(data,scope=JSON.stringify([data.tab,filters(),offset])){
  observeUpdates(data,scope);
  const historyView=data.tab==='history';
  if(historyView)renderHistory(data);else {
  $('heads').innerHTML='<tr>'+['代币 / 市场','当前价格','24H涨跌','24H成交额','触发信号 / 信号等级','触发K线','触发后表现','当前4H走势','追踪状态','检查截至'].map((x,i)=>headCell(x,['symbol','price','change','volume','grade','trigger','performance','trend','state','checked'][i])).join('')+'</tr>';
  const html=data.rows.map(g=>{const e=g.representative,q=g.quote_data,o=e.observation;const key=JSON.stringify([g.market,g.symbol,e.event_id]);
   const state=g.active_count?(g.paused_count===g.active_count?'待补数据':g.paused_count?'追踪中 · 部分待补':'追踪中'):o.status==='ended'?'已结束':'技术事件';
   const rid=rowId(g,false);return `<tr data-tw-row="${esc(rid)}" class="${targetEvent===e.event_id?'tw-selected':''} ${rowNotice(rid)?'tw-updated':''}"><td><button type="button" class="tw-coin" data-tracking-open="${esc(key)}">${esc(g.symbol)}</button><small>${esc(g.market==='USDT-M'?'USDⓈ-M':g.market)}${q.quote?' · '+esc(q.quote)+'计价':''}</small>${noticeHtml(rid)}</td><td>${num(q.price)}<small>行情 ${esc(time(q.asOf))}</small></td><td>${pct(q.priceChange24Pct)}</td><td>${compactMoney(q.volume24Usd)}</td><td>${esc(signalName(e))}<small>${e.technical_grade||e.strategy==='A'?gradeHtml(e):esc('原确认：'+label(e.original_confirmation.technical_status))}</small></td><td>${esc(time(e.trigger_close_ms))}<small>${esc(e.timeframe||'周期待核')} · 已收盘</small></td><td>${pct(g.return_pct)}<small>${e.trigger_price?'触发闭合价 '+num(e.trigger_price):o.status==='ended'?'缺结束价，不计算结束收益':'缺触发价，暂不计算'}</small></td><td>${trendHtml(q)}<small>4H截至 ${esc(time(q.candleAsOf==null?null:q.candleAsOf+1))}</small></td><td><span class="tw-pill">${esc(state)}</span><small>${g.active_count}条进行中 · ${g.event_count}次事件</small><small><button class="tw-link" data-tracking-jump="history" data-tracking-key="${esc(key)}">原始历史信号</button></small></td><td>${esc(time(o.last_evaluated_bar_close_ms))}<small>${o.last_evaluated_bar_close_ms&&q.candleAsOf!=null&&o.last_evaluated_bar_close_ms<q.candleAsOf+1&&g.active_count?'落后于行情，等待续接或补齐':o.status==='ended'?esc(label(o.end_reason)):'逐根检查'}</small></td></tr>`;
  }).join('');
  $('rows').innerHTML=html||'<tr><td colspan="10">当前范围没有已登记的追踪记录，可切换“全部追踪记录”或其他筛选。</td></tr>';
  }
  syncNotices();
  total=data.total;next=data.next_offset;$('count').textContent=historyView?`共 ${total} 次事件 · ${data.contract_count} 份合约 · 本页 ${data.rows.length} 条`:`共 ${total} 个合约 · 本页 ${data.rows.length} 个`;
  $('prev').disabled=offset===0;$('next').disabled=next==null;
  $('explain').textContent=historyView?'历史按每次事件展示，默认包含全部结果；同一合约可以有多条记录。原始结果与后续观察分开显示，尚无会话的记录不会自动变成追踪。':'已有记录不会因本轮没有新金叉而消失。信号等级沿用触发时证据，名称与主站一致；行情时间与追踪检查时间分别显示。结束记录仍保留，缺结束价不计算结束收益。同一币种的不同计价合约分别追踪。';
  const queue=data.queue||{};
  $('warning').textContent=(queue.pending_rows?`追踪发布仍有 ${queue.pending_rows} 条待处理，检查截至可能落后。`:'')+(queue.errors?.length?' 存在来源或处理阻塞，详情见数据与任务。':'')+' '+(data.coverage_note||'');
 }
 async function load({pageOffset=0,frozen=false,retried=false}={}){
  if(!active||!ready)return;
  const f=filters(),tracking=tab==='tracking',filterKey=trackingQueryKey();
  const requestKey=JSON.stringify([f,pageOffset,frozen?view:null]);
  const q=new URLSearchParams({...f,sort:tracking?'trigger':f.sort,direction:tracking?'desc':f.direction,limit:tracking?'100':'50',offset:tracking?'0':String(pageOffset)});if(frozen&&view)q.set('view',view);
  // Re-entering the foreground must not cancel an identical first read.
  if(requests.has('list')&&listKey===requestKey)return;
  clearTimeout(retryTimer);abort('watch');abort('list');abort('status');listKey=requestKey;
  const own=++epoch;message(view?'正在更新，保留上次列表…':'正在读取已保存的追踪记录…');
  if(!view)$('rows').innerHTML='<tr><td colspan="10">正在读取已保存记录，请稍候…</td></tr>';
  const slowTimer=setTimeout(()=>{if(own===epoch&&active)message(view?'本次更新较慢，继续显示上次列表…':'首次读取较慢，正在等待本地服务；超时会提示重试。');},5000);
  try{
   let data;
   if(tracking){
    const all=frozen&&trackingData&&trackingFilterKey===filterKey?trackingData:await readTracking(q,own);
    if(own!==epoch||!active||!all)return;
    trackingData=all;trackingFilterKey=filterKey;data=trackingPage(all,pageOffset);
   }else data=await get('list','/api/tracking/workbench/list?'+q);
   if(own!==epoch||!active)return;
   view=data.view;version=data.version;offset=pageOffset;render(data,JSON.stringify([f,pageOffset]));save();message('已读取保存数据 · 随采集和补齐结果更新 · 北京时间');
   if(pendingDetail){const key=pendingDetail;pendingDetail=null;openDetail(key,null);}
   else if(detail)await loadDetail({reset:true,retried});
  }catch(error){
   if(error.name==='AbortError'||own!==epoch)return;
   if([409,410].includes(error.status)&&!retried){return load({retried:true});}
   const reason=error.status?'HTTP '+error.status:error.message==='读取超时'?'读取超时':'连接暂不可用';
   const initialRetry=!version&&!retried;
   message((view?'读取失败，已保留上次数据。':'暂时未能读取记录。')+reason+'；'+(initialRetry?'2秒后自动重试一次。':'可点击“刷新已保存数据”重试。'));
   if(initialRetry)retryTimer=setTimeout(()=>{if(active&&!document.hidden&&own===epoch)load({retried:true});},2000);
  }finally{clearTimeout(slowTimer);if(own===epoch){listKey=null;if(active)watch();}}
 }
 async function watch(){
  if(!active||document.hidden||!version||requests.has('watch')||requests.has('list'))return;
  const own=epoch;
  try{
   const data=await get('watch','/api/tracking/workbench/watch?'+new URLSearchParams({version}));
   if(!active||document.hidden||own!==epoch)return;
   if(data.changed){scheduleLoad();}else {message('已读取保存数据 · 随采集和补齐结果更新 · 北京时间');retryTimer=setTimeout(watch,1000);}
  }catch(error){
   if(error.name==='AbortError'||!active||document.hidden||own!==epoch)return;
   // A failed notification connection still checks the saved version on its existing retry cycle.
   try{const latest=await get('status','/api/tracking/workbench/status');
    if(!active||document.hidden||own!==epoch)return;
    if(latest.version!==version){scheduleLoad();return;}
    message('已保留最新列表 · 更新通知重连中');
   }catch(statusError){if(statusError.name==='AbortError'||!active||document.hidden||own!==epoch)return;message('自动更新连接暂不可用，已保留数据；可手动刷新。');}
   retryTimer=setTimeout(watch,60000);
  }
 }
 function scheduleLoad(){if(!active||!ready||document.hidden)return;clearTimeout(loadTimer);loadTimer=setTimeout(()=>load(),700);}
 function closeDetail(){
  abort('detail');detail=null;detailOffset=0;detailNext=null;
  if(root){$('drawer').hidden=true;$('veil').hidden=true;focusBefore?.focus();}
 }
 async function loadDetail({reset=false,retried=false}={}){
  if(!detail)return;const chosen=JSON.stringify(detail),own=epoch;
  $('detailstatus').textContent='正在读取事件详情…';
  const q=new URLSearchParams({...detail,view,offset:String(reset?0:detailOffset),limit:'20'});
  try{
   const data=await get('detail','/api/tracking/workbench/detail?'+q);
   if(own!==epoch||!detail||chosen!==JSON.stringify(detail)||!active)return;
   $('detailtitle').textContent=`${data.symbol} · ${tab==='history'?'历史信号':'持续追踪'}`;
   const buttons=data.events.map(e=>`<button type="button" data-tracking-event="${esc(e.event_id)}" aria-pressed="${e.event_id===data.selected?.event_id}">${esc(e.strategy+' · '+time(e.trigger_close_ms)+' · '+label(e.observation.status||'none'))}</button>`).join('');
   if(reset)$('events').innerHTML=buttons;else $('events').insertAdjacentHTML('beforeend',buttons);
   detailNext=data.next_offset;$('moreevents').hidden=detailNext==null;
   const e=data.selected,o=e?.observation||{};
   if(e){
    const timeline=(data.transitions||[]).map(t=>`<p>${esc(time(t.at_bar_close_ms))} · ${esc(label(t.to_state))} · ${esc(label(t.reason))}</p>`).join('');
    $('eventdetail').innerHTML=`<section class="tw-evidence"><h3>${esc(signalName(e))}</h3>${e.technical_grade?'<p>触发时等级：'+gradeHtml(e)+'</p>':''}<p>触发K线：${esc(time(e.trigger_close_ms))} · ${esc(e.timeframe)}</p><p>原确认：${esc(label(e.original_confirmation.technical_status))}；资格：${esc(label(e.qualification.status))}</p><p>后续观察：${esc(label(o.status||'none'))}；检查截至：${esc(time(o.last_evaluated_bar_close_ms))}</p><p>结束原因：${esc(label(o.end_reason))}</p><p>发现时间：${esc(time(e.first_seen_at))}${e.historical_import?' · 历史补登记':''}</p></section><section class="tw-evidence"><h3>后续进展</h3>${timeline||'<p>尚无后续状态变化，连续检查时间见上方。</p>'}${data.has_more_transitions?'<p>此处显示最近100条状态变化，完整记录仍保留。</p>':''}</section><details class="tw-evidence"><summary>数据缺口与证据</summary><pre>${esc(JSON.stringify({event_id:e.event_id,profile:o.profile_id,rule_version:e.rule_version,quality:e.data_quality,gaps:data.gaps},null,2))}</pre></details><button data-tracking-jump="${tab==='history'?'tracking':'history'}" data-tracking-key="${esc(eventKey(e))}" ${tab==='history'&&!e.session_id?'disabled':''}>${tab==='history'?(e.session_id?'查看对应持续追踪':'此记录尚无追踪会话'):'查看原始历史信号'}</button>`;
   }
   if(e)acknowledge(data.market,data.symbol,e.event_id);
   $('detailstatus').textContent=`共 ${data.event_count} 次事件 · 当前展示不会覆盖原始信号`;
  }catch(error){
   if(error.name==='AbortError'||!detail||chosen!==JSON.stringify(detail))return;
   if([409,410].includes(error.status)&&!retried){
    // Retry once with a complete fresh list; never loop on a busy ledger.
    if(active)return load({retried:true});
   }
   $('detailstatus').textContent='详情读取失败，保留已读内容。请点击事件或刷新重试。';
  }
 }
 function openDetail(key,button){
  const [market,symbol,event_id]=JSON.parse(key);detail={market,symbol,event_id};detailOffset=0;focusBefore=button;
  $('drawer').hidden=false;$('veil').hidden=false;$('drawer').focus();loadDetail({reset:true});
 }
 async function init(){
  if(ready)return;if(opening)return opening;
  opening=(async()=>{
   root=panel.querySelector('#trackingWorkbench');
   if(!root){
    panel.innerHTML='<p role="status">正在加载持续追踪页面，请稍候…</p>';
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
    try{const response=await fetch('/tracking-workbench.html',{cache:'no-store',signal:controller.signal});if(!response.ok)throw Error('页面资源未就绪');panel.innerHTML=await response.text();}
    finally{clearTimeout(timer);}
    root=panel.querySelector('#trackingWorkbench');
    if(!root)throw Error('页面资源不完整');
   }
   try{const saved=JSON.parse(localStorage.getItem('tracking-workbench-tabs-v2')||'null');if(saved?.queries){for(const t of ['tracking','history'])if(saved.queries[t]&&typeof saved.queries[t]==='object')savedTabs[t]=saved.queries[t];if(['tracking','history'].includes(saved.tab))tab=saved.tab;}}catch{}
   applyTab(tab,{loadNow:false,keep:false});
   for(const k of ['status','strategy','market','from','to'])$(k).addEventListener('change',()=>{targetEvent=null;scheduleLoad();});
   $('sort').addEventListener('change',()=>{sortDirection=$('sort').value==='symbol'?'asc':'desc';targetEvent=null;save();load({frozen:true});});
   $('heads').addEventListener('click',event=>{const button=event.target.closest('[data-tracking-sort]');if(button)chooseSort(button.dataset.trackingSort);});
   $('search').addEventListener('input',()=>{targetEvent=null;scheduleLoad();});$('refresh').onclick=()=>load();$('readupdates').onclick=acknowledgePage;
   $('reset').onclick=()=>{savedTabs[tab]={};applyTab(tab,{keep:false});};
   root.querySelectorAll('[data-tracking-tab]').forEach(b=>b.onclick=()=>applyTab(b.dataset.trackingTab));
   root.querySelector('.tw-tabs').addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();applyTab(event.key==='Home'?'tracking':event.key==='End'?'history':tab==='tracking'?'history':'tracking');$('tab-'+tab).focus();}});
   root.addEventListener('click',event=>{const button=event.target.closest('[data-tracking-jump]');if(button&&!button.disabled)jump(button.dataset.trackingJump,button.dataset.trackingKey);});
   $('prev').onclick=()=>load({pageOffset:Math.max(0,offset-50),frozen:true});$('next').onclick=()=>load({pageOffset:next,frozen:true});
   $('rows').addEventListener('click',event=>{const button=event.target.closest('[data-tracking-open]');if(button)openDetail(button.dataset.trackingOpen,button);});
   $('events').addEventListener('click',event=>{const button=event.target.closest('[data-tracking-event]');if(button&&detail){detail.event_id=button.dataset.trackingEvent;detailOffset=0;loadDetail({reset:true});}});
   $('moreevents').onclick=()=>{detailOffset=detailNext;loadDetail();};$('close').onclick=closeDetail;$('veil').onclick=closeDetail;
   root.addEventListener('keydown',event=>{
    if($('drawer').hidden)return;
    if(event.key==='Escape'){event.stopPropagation();closeDetail();}
    if(event.key==='Tab'){
     const nodes=[...$('drawer').querySelectorAll('button:not([hidden]),a,input,select,summary')].filter(n=>!n.disabled&&n.getClientRects().length),first=nodes[0],last=nodes.at(-1);
     if(event.shiftKey&&(document.activeElement===first||document.activeElement===$('drawer'))){event.preventDefault();last?.focus();}
     else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
    }
   });ready=true;
  })();try{await opening;}finally{opening=null;}
 }
 async function activate(){
  if(panel.hidden){active=false;++epoch;requests.forEach(c=>c.abort());requests.clear();clearTimeout(loadTimer);clearTimeout(retryTimer);if(ready)closeDetail();return;}
  if(active)return;active=true;
  try{await init();const hash=new URLSearchParams(location.hash.slice(1).replace(/^tracking&?/,''));
   if(hash.get('symbol'))$('search').value=hash.get('symbol');if(hash.get('market'))$('market').value=hash.get('market');
   if(active)load();
  }catch(error){
   active=false;ready=false;
   panel.innerHTML='<p role="alert">持续追踪页面加载失败，已保存的数据仍然保留。</p><button type="button" data-tracking-retry>重新加载追踪页面</button>';
   panel.querySelector('[data-tracking-retry]').addEventListener('click',activate);
  }
 }
 new MutationObserver(activate).observe(panel,{attributes:true,attributeFilter:['hidden']});
 const nav=document.querySelector('.sidebar [data-page="tracking"]');
 nav?.addEventListener('click',()=>{if(!location.hash.startsWith('#tracking'))location.hash='tracking';});
 document.querySelector('.sidebar nav')?.addEventListener('click',event=>{const button=event.target.closest('[data-page]');if(button&&button.dataset.page!=='tracking'&&location.hash.startsWith('#tracking'))history.replaceState(null,'',location.pathname+location.search);});
 window.addEventListener('hashchange',()=>{if(location.hash.startsWith('#tracking')){nav?.click();activate();}else if(!panel.hidden)document.querySelector('.sidebar [data-page="market"]')?.click();});
 document.addEventListener('visibilitychange',()=>{if(document.hidden){abort('watch');abort('status');clearTimeout(retryTimer);}else if(active&&!requests.has('list'))load();});
 let taskSignature='';window.addEventListener('workbench-scan-state',event=>{
  const s=event.detail||{},task=s.task||s.lastTask||s.operation;const signature=task?JSON.stringify([task.task_id||task.id,task.status]):'';
  if(signature!==taskSignature){taskSignature=signature;if(active&&task&&['success','partial_success'].includes(task.status))scheduleLoad();}
 });
 if(location.hash.startsWith('#tracking'))nav?.click();activate();
})();
