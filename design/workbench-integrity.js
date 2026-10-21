/* Presentation integration. All trading, collection and stored rule decisions remain in their original services. */
function integrateWorkbench() {
 'use strict';
 const q=(s,root=document)=>root.querySelector(s);
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 function fold(parent,nodes,title,id){
  if(!parent||q('#'+id))return;
  const d=document.createElement('details');d.id=id;d.className='panel legacy-reference';
  d.innerHTML='<summary>'+esc(title)+'</summary><p class="muted">保留原有参数和历史说明供研究；当前记录与操作以上方真实服务面板为准。</p>';
  for(const node of nodes)if(node&&node.parentNode===parent)d.append(node);
  parent.append(d);return d;
 }
 function comboCard(c){return '<article class="card current-combo"><h3>'+esc(c.name)+'</h3><p>'+esc(c.purpose)+'</p><ol>'+c.steps.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ol><details><summary>风险与规则来源</summary><p>'+esc(c.risk)+'</p><p class="muted">'+esc(c.source)+'</p></details>'+(c.available?'<button data-current-combo="'+esc(c.id)+'">按此组合筛选</button>':'<span class="pill gray">历史 / 尚未开放</span>')+'</article>';}
 const lib=q('[data-strategy-view="library"]');
 if(lib){
  const legacy=[...lib.children].filter(n=>n.id!=='structRuntimePanel');
  fold(lib,legacy,'旧指标目录与历史研究分类（仅供参考）','legacyIndicatorDirectory');
  const current=document.createElement('div');current.className='panel';current.id='currentSignalDirectory';
  current.innerHTML='<h2>当前信号目录</h2><p>与统一信号台共用目录。已接入表示有读取 / 判定能力，数据缺失仍显示未知；运行验收和收益验证分别记录。</p><div class="tablewrap"><table><thead><tr><th>信号</th><th>周期</th><th>实现状态</th><th>当前用法与边界</th></tr></thead><tbody>'+WorkbenchSignals.map(s=>'<tr data-signal-entry="'+esc(s.id)+'"><td>'+esc(s.num+' '+s.name)+'</td><td>'+esc(s.period)+'</td><td>'+esc(s.status)+'</td><td>'+esc(s.description)+'</td></tr>').join('')+'</tbody></table></div><p class="note">ATR / ADX / RSI 等结构指标可按已有OHLCV现场计算，实际可算范围见结构面板；这不等于完整T4～T7策略、ATH或历史资格已接入。</p>';
  lib.prepend(current);
 }
 const templates=q('[data-strategy-view="templates"]'),cards=q('#templateCards');
 if(templates&&cards){
  fold(templates,[cards],'历史 T1～T8 研究模板（保留原ID与编辑能力）','legacyStrategyTemplates');
  const current=document.createElement('div');current.id='currentStrategyCombos';current.className='panel';
  current.innerHTML='<h2>当前组合与使用办法</h2><p>以最新A金叉、独立1H B和当前⑤聪明钱口径为主。组合仅改变筛选，不触发交易或新增任务。旧胜率数字已撤回。</p>'+['推荐','更多观察','规划与未接入','历史对照'].map(g=>{const list=COMBO_GUIDES.filter(c=>c.group===g&&c.id!=='none');return list.length?'<h3>'+g+'</h3><div class="grid2">'+list.map(comboCard).join('')+'</div>':'';}).join('')+'<div class="note">旧T1 / T3候选意图分别对应①②③、③⑥；T2当前观察请使用A组合。T4持续吸筹不等于B；T4～T7完整候选条件尚未实现。T8趋势延续有独立研究用途，不改名为A、不删除。旧固定百分比包与撤回收益数字不作为当前操作指导。</div>';
  templates.prepend(current);
 }
 const recommendations=q('#recommendedCards');
 if(recommendations)recommendations.innerHTML=['a_confirm_v32','leverage','b_confirmed_v32'].map(id=>comboCard(COMBO_GUIDES.find(c=>c.id===id))).join('');
 const basket=q('[data-view="basket"]');
 if(basket){const real=q('#simRuntimePanel',basket);if(real)basket.prepend(real);const recommendations=q('.recommendation-panel',basket);if(recommendations){recommendations.innerHTML='<p>组合使用办法集中在 <a href="/unified-strategies.html">指标与策略说明</a>；此处是纸面账户和历史账本，不代表当前A/B自动模拟。</p>';basket.append(recommendations);}}
 if(basket)fold(basket,[...basket.children].filter(n=>!n.matches('.lead,.recommendation-panel,#simRuntimePanel')),'旧模板资金分配与展示预览（不代表真实账本）','basketLegacyPreview');
 const validation=q('[data-view="validation"]');
 if(validation){const anchors=document.createElement('nav');anchors.className='panel validation-jumps';anchors.setAttribute('aria-label','验证页内定位');anchors.innerHTML='<a href="#backtestRuntimePanel">历史回测</a> · <a href="#evalRuntimePanel">前向观察</a> · <a href="#reviewRuntimePanel">复盘计划</a>';validation.prepend(anchors);}
 if(validation)fold(validation,[...validation.children].filter(n=>!n.matches('.lead,.validation-jumps,#backtestRuntimePanel,#evalRuntimePanel,#reviewRuntimePanel')),'旧验证参数与排期预览（实际实验 / 计划以上方为准）','validationLegacyPreview');
 // Preserve report form state while switching into the real saved-report list.
 const reports=q('[data-view="reports"]'),output=q('#reportOutput');
 if(reports&&output){
  const tabs=q('.subtabs',reports)||reports.querySelector('.subtabs');
  const buttons=tabs?[...tabs.querySelectorAll('button')]:[];
  const form=[...reports.children].filter(n=>n!==output&&!n.matches('.lead,.subtabs'));
  function mode(history){form.forEach(n=>n.hidden=history);buttons.forEach((b,i)=>b.classList.toggle('active',history?i===1:i===0));if(history){output.hidden=false;output.scrollIntoView({block:'start',behavior:'smooth'});}}
  buttons.forEach((b,i)=>{b.removeAttribute('data-demo');b.onclick=()=>mode(i===1);});
 }
 document.addEventListener('click',e=>{
  const b=e.target.closest('[data-current-combo]');if(!b)return;
  if(!window.UnifiedWorkbench){toast('统一信号台正在连接数据，请稍后重试');return;}
  if(window.UnifiedWorkbench.applyPreset(b.dataset.currentCombo)){page('market');window.scrollTo({top:0});}
 });
 // Blank legacy storage form is a preview; genuine backup and cleanup controls remain below it.
 const advancedButton=[...document.querySelectorAll('[data-view="settings"] button[data-demo]')].find(b=>b.textContent==='其他设置预览');
 if(advancedButton){advancedButton.removeAttribute('data-demo');advancedButton.textContent='高级配置与备份';advancedButton.onclick=()=>{page('settings');setTab('setting','storage');const d=q('.advanced-config');if(d){d.open=true;d.scrollIntoView({block:'start'});}};const hint=advancedButton.parentElement.querySelector('small');if(hint)hint.textContent='高级双向配置与全量备份集中在「存储与展示」；AI密钥在AI面板单独管理。';}
 for(const section of document.querySelectorAll('[data-setting-view="budget"],[data-setting-view="simulation"]')){for(const input of section.querySelectorAll('input,select,textarea'))input.disabled=true;const note=document.createElement('p');note.className='note';note.textContent='旧表单为只读预览，不保存、不生效。实际模拟费用与预算请打开存储页的实际SIM配置；投研数量以新建报告页明确输入为准。';section.prepend(note);const button=document.createElement('button');button.textContent='打开实际配置';button.onclick=()=>{page('settings');setTab('setting','storage');const panel=q('.advanced-config');if(panel){panel.open=true;panel.scrollIntoView({block:'start'});}};section.prepend(button);}
 const storage=q('[data-setting-view="storage"]');
 const sourcePreview=q('[data-setting-view="sources"]>.panel');
 if(sourcePreview&&sourcePreview.id!=='extRuntimePanel'){for(const input of sourcePreview.querySelectorAll('input,select,textarea'))input.disabled=true;const note=document.createElement('p');note.className='note';note.textContent='此为旧来源配置预览，只读且不生效。实际公开行情由采集器读取；投研检索方式在AI API与报告核对流程中确认。来源授权状态见下方真实扩展目录。';sourcePreview.prepend(note);}
 if(storage){const base=[...storage.children].find(n=>n.classList.contains('panel')&&!n.id&&!n.classList.contains('advanced-config'));if(base){for(const input of base.querySelectorAll('input,select,textarea'))input.disabled=true;const note=document.createElement('p');note.className='note';note.textContent='旧存储与主题表单为只读预览，不保存、不生效。实际配置与全量备份请使用本页高级配置，历史清理使用独立清理设置。';base.prepend(note);const d=fold(storage,[base],'旧存储与主题表单预览（真实备份见高级配置）','legacyStoragePreview');if(d)storage.append(d);}}
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',integrateWorkbench,{once:true});else integrateWorkbench();
