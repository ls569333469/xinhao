/** Explicitly reviewed research methods; no model/network calls outside local APIs. */
(function () {
  const q = s => document.querySelector(s);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let entries = [], editing = null, report = null, feedbackId = null;
  const selected = new Map();
  async function api(path, method, body) {
    const r = await fetch(path, {method: method || 'GET', headers: {'Content-Type':'application/json'}, credentials:'same-origin', body: body ? JSON.stringify(body) : undefined});
    const d = await r.json(); if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status)); return d;
  }
  function status(s) { q('#experienceStatus').textContent = s; }
  function changed() { window.dispatchEvent(new Event('workbench-experience-selection')); }
  function render() {
    const labels = {candidate:'候选', reviewed:'已审核', adopted:'已采用', disabled:'已停用'};
    q('#experienceList').innerHTML = entries.length ? entries.map(e => `<div class="panel" style="padding:12px;margin:8px 0">
      <strong>${esc(e.title)}</strong> · ${labels[e.status] || esc(e.status)} · v${e.version}
      <p class="muted">适用：${esc(e.trigger)}<br>动作：${esc(e.action)}<br>边界：${esc(e.boundary)}</p>
      <button type="button" data-exp-edit="${e.id}">查看 / 修改 / 审核</button>
      <label><input type="checkbox" data-exp-select="${e.id}" ${selected.has(e.id)?'checked':''} ${e.status==='adopted'?'':'disabled'}> 本次投研使用</label>
      ${e.status==='adopted'?`<label style="display:block;margin-top:8px">选择依据 <input data-exp-reason="${e.id}" maxlength="500" value="${esc(selected.get(e.id)?.selection_reason || '')}" placeholder="为何适用于本次对象"></label>`:''}
    </div>`).join('') : '<p class="muted">暂无经验。可从报告反馈创建候选，经审核后采用。</p>';
    q('#experienceCount').textContent = '本次已选 ' + selected.size + '/5 条';
  }
  async function load() {
    const data = await api('/api/research/experiences'); entries = data.entries;
    let invalid = false;
    for (const [id, ref] of selected) {
      const e = entries.find(x => x.id === id);
      if (!e || e.status !== 'adopted' || e.version !== ref.version || e.content_hash !== ref.content_hash) { selected.delete(id); invalid = true; }
    }
    if (invalid) { changed(); status('已移除失效经验，请重新预览'); }
    q('#experienceFeedbackList').textContent = data.feedback.slice(0,10).map(f=>`${f.report_id}：${f.note}`).join('\n') || '暂无反馈';
    render();
  }
  function fields() {
    const out = {};
    for (const k of ['title','trigger','action','boundary','counterexample']) out[k] = q('#exp_' + k).value;
    out.sources = q('#exp_sources').value.split('\n').map(s=>s.trim()).filter(Boolean);
    return out;
  }
  function edit(e) {
    editing = e || null;
    for (const k of ['title','trigger','action','boundary','counterexample']) q('#exp_' + k).value = e?.[k] || '';
    q('#exp_sources').value = (e?.sources || []).join('\n');
    q('#exp_review_note').value = '';
    q('#exp_methods_ack').checked = false;
    q('#experienceEditor').hidden = false;
    q('#experienceEditing').textContent = e ? `${e.id} · v${e.version} · ${e.status}` : '新候选';
    q('#experienceHistory').textContent = '';
    q('#exp_review').disabled = !e || !['candidate','reviewed'].includes(e.status);
    q('#exp_adopt').disabled = !e || e.status !== 'reviewed';
    q('#exp_disable').disabled = !e;
  }
  async function update(operation) {
    if (!editing) throw new Error('请先保存候选');
    const e = await api('/api/research/experiences/' + editing.id, 'PUT', {operation, expected_version:editing.version,
      review_note:q('#exp_review_note').value, acknowledge_methods_only:q('#exp_methods_ack').checked});
    edit(e); await load(); status('已保存新版本 v' + e.version + '；旧版本保留');
  }
  function run(fn) { return () => Promise.resolve().then(fn).catch(e=>status(e.message || String(e))); }
  function setup() {
    const host = q('[data-view="reports"]'); if (!host) return;
    const panel = document.createElement('details'); panel.id = 'researchExperience'; panel.className = 'panel'; panel.style.marginTop = '24px';
    panel.innerHTML = `<summary><strong>投研反馈与经验</strong> <span id="experienceCount" class="muted">本次已选 0/5 条</span></summary>
      <p class="muted">只复用研究方法。历史案例和数字须在新任务中重新核验；仅勾选已采用条目才会应用。</p>
      <p id="experienceStatus" role="status"></p>
      <p id="experienceReport">打开报告后可提交反馈。</p>
      <label>报告反馈<textarea id="experienceFeedback" rows="3" maxlength="4000" placeholder="哪些判断有用、错误或遗漏？请勿填凭证。"></textarea></label>
      <button type="button" id="experienceSaveFeedback">保存反馈</button>
      <button type="button" id="experienceNew">新建候选经验</button>
      <button type="button" id="experienceRefresh">刷新经验</button>
      <details><summary>近期反馈</summary><pre id="experienceFeedbackList" style="white-space:pre-wrap"></pre></details>
      <div id="experienceList"></div>
      <section id="experienceEditor" hidden style="margin-top:20px">
        <strong id="experienceEditing"></strong>
        ${[['title','名称'],['trigger','适用条件'],['action','核验动作'],['boundary','适用边界'],['counterexample','反例 / 失效条件'],['sources','来源链接（每行一条）']].map(([k,t])=>`<label style="display:block;margin:10px 0">${t}<textarea id="exp_${k}" rows="2" maxlength="4000"></textarea></label>`).join('')}
        <button type="button" id="exp_save">保存候选 / 修改</button>
        <label style="display:block;margin:12px 0">审核说明 / 停用原因<textarea id="exp_review_note" rows="2" maxlength="4000"></textarea></label>
        <label><input type="checkbox" id="exp_methods_ack"> 已审阅；只复用方法，历史事实仍需重新检索</label>
        <div style="margin:12px 0"><button type="button" id="exp_review">审核通过</button> <button type="button" id="exp_adopt">采用</button> <button type="button" id="exp_disable">停用</button></div>
        <pre id="experienceHistory" style="white-space:pre-wrap"></pre>
      </section>`;
    host.appendChild(panel);
    q('#experienceRefresh').onclick = run(load);
    q('#experienceNew').onclick = () => edit(null);
    q('#experienceSaveFeedback').onclick = run(async()=>{
      if (!report?.report_id || report.error) throw new Error('请先打开一份有效报告');
      const f = await api('/api/research/feedback','POST',{report_id:report.report_id,note:q('#experienceFeedback').value});
      feedbackId = f.id; await load(); status('反馈已保存；新候选将关联这份反馈');
    });
    q('#exp_save').onclick = run(async()=>{
      const payload = fields();
      if (editing) { payload.expected_version = editing.version; payload.operation = 'edit'; }
      else payload.feedback_id = feedbackId;
      const e = await api('/api/research/experiences' + (editing?'/'+editing.id:''),editing?'PUT':'POST',payload);
      edit(e); await load(); status('候选已保存，审核后才能采用');
    });
    for (const op of ['review','adopt','disable']) q('#exp_' + op).onclick = run(()=>update(op));
    q('#experienceList').addEventListener('click', e => {
      const id = e.target.dataset.expEdit; if (!id) return;
      run(async()=>{ const d = await api('/api/research/experiences/'+id); edit(d.entry);
        q('#experienceHistory').textContent = d.versions.map(v=>`v${v.version} · ${v.status} · ${v.updated_at}\n${v.review_note || ''}`).join('\n'); })();
    });
    q('#experienceList').addEventListener('change',e=>{
      const id = e.target.dataset.expSelect;
      if (!id) return;
      if (e.target.checked) {
        if (selected.size >= 5) { e.target.checked = false; status('每次最多选择5条'); return; }
        const item = entries.find(x=>x.id===id);
        selected.set(id,{id,version:item.version,content_hash:item.content_hash,selection_reason:''});
      } else selected.delete(id);
      changed(); render();
    });
    q('#experienceList').addEventListener('input',e=>{
      const id = e.target.dataset.expReason; if (!selected.has(id)) return;
      selected.get(id).selection_reason = e.target.value; changed();
    });
    window.addEventListener('workbench-report-opened',e=>{
      report = e.detail; feedbackId = null;
      q('#experienceReport').textContent = report?.error ? '报告读取失败，暂不可关联反馈' : '反馈对象：' + (report?.report_id || '未选择');
    });
    window.ExperienceRuntime = {selected:()=>[...selected.values()].sort((a,b)=>a.id.localeCompare(b.id)), load};
    load().catch(e=>status('经验库读取失败：'+e.message));
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded',setup); else setup();
})();
