/** REVIEW-01: 数据复盘 / 手动AI门禁 / 持久计划 / 暂停新入场（研究/模拟，非实盘）。 */
(function () {
  'use strict';
  function q(sel, root) { return (root || document).querySelector(sel); }
  function esc(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function toast(msg) {
    if (typeof window.showToast === 'function') return window.showToast(msg);
    var el = q('#reviewStatus');
    if (el) el.textContent = msg;
    else console.log('[复盘]', msg);
  }
  function fmt(n, d) {
    if (n == null || !isFinite(Number(n))) return '—';
    return Number(n).toLocaleString('zh-CN', { maximumFractionDigits: d == null ? 4 : d });
  }

  var state = { status: null, reviews: [], plans: [] };

  function api(path, opts) {
    opts = opts || {};
    var init = {
      method: opts.method || 'GET',
      headers: { Accept: 'application/json' },
      credentials: 'same-origin',
    };
    if (opts.body != null) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opts.body);
    }
    return fetch(path, init).then(function (res) {
      return res.json().then(function (data) {
        if (!res.ok) {
          var err = new Error((data && data.error) || ('HTTP ' + res.status));
          err.status = res.status;
          err.payload = data;
          throw err;
        }
        return data;
      });
    });
  }

  function ensurePanel() {
    var section = q('[data-view="validation"]');
    if (!section || q('#reviewRuntimePanel')) return;
    var panel = document.createElement('div');
    panel.className = 'panel';
    panel.id = 'reviewRuntimePanel';
    panel.innerHTML =
      '<div class="panel-head"><div><h2>数据复盘与计划（REVIEW-01 首刀）</h2>' +
      '<small>数值复盘不调用模型 · 手动 AI 须已配置 Key · 重合计划/周期去重 · 暂停新入场不停止原仓跟踪</small></div>' +
      '<span class="pill gray">服务端生效</span></div>' +
      '<p id="reviewStatus" class="muted">加载中…</p>' +
      '<div class="stats" id="reviewEffect"></div>' +
      '<div class="command-bar" style="margin-top:12px">' +
      '<span id="reviewPauseNote" class="muted"></span>' +
      '<div class="row">' +
      '<button type="button" data-review="refresh">刷新</button>' +
      '<label>冻结版本(可选) <select id="reviewVersion"><option value="">（全库汇总）</option></select></label>' +
      '<button type="button" class="primary" data-review="data">创建数据复盘</button>' +
      '<button type="button" data-review="ai">手动 AI 复盘</button>' +
      '<button type="button" data-review="toggle-pause" id="reviewPauseBtn">暂停新入场</button>' +
      '</div></div>' +
      '<div class="row" style="margin-top:12px;gap:8px;flex-wrap:wrap">' +
      '<label>计划类型 <select id="reviewPlanKind"><option value="daily">每日</option><option value="weekly">每周</option></select></label>' +
      '<label>时刻 <input id="reviewPlanTime" value="21:00" style="width:70px" title="北京时间 HH:MM"></label>' +
      '<label id="reviewWeekdayWrap" hidden>星期 <select id="reviewPlanWeekday">' +
      '<option value="1">一</option><option value="2">二</option><option value="3">三</option>' +
      '<option value="4">四</option><option value="5">五</option><option value="6">六</option><option value="7">日</option>' +
      '</select></label>' +
      '<button type="button" class="primary" data-review="save-plan">保存计划</button>' +
      '</div>' +
      '<div class="grid2" style="margin-top:16px">' +
      '<div><h3>复盘记录</h3><div class="tablewrap"><table><thead><tr>' +
      '<th>时间</th><th>类型</th><th>成绩摘要</th><th>AI</th></tr></thead>' +
      '<tbody id="reviewList"><tr><td colspan="4">暂无</td></tr></tbody></table></div></div>' +
      '<div><h3>持久计划</h3><div class="tablewrap"><table><thead><tr>' +
      '<th>类型</th><th>时刻</th><th>启用</th><th>下次</th><th>操作</th></tr></thead>' +
      '<tbody id="reviewPlans"><tr><td colspan="5">暂无</td></tr></tbody></table></div></div>' +
      '</div>' +
      '<div style="margin-top:16px"><h3>最近结算（联调只读）</h3>'
      + '<small class="muted">来自 EVAL 结算记录；不可核/断档不计假赢；复盘数值据此汇总</small>'
      + '<div class="tablewrap"><table><thead><tr>'
      + '<th>时间</th><th>合约</th><th>结果</th><th>原因</th><th>盈亏</th></tr></thead>'
      + '<tbody id="reviewSettlements"><tr><td colspan="5">加载中…</td></tr></tbody></table></div></div>'
      + '<div class="note">服务端生效状态以上方统计为准。数值更新不调用 AI；重合周期复盘去重；暂停新入场只拦新开，不关闭已有观察/仓位。自动按计划触发调度以服务端绑定为准。付费 AI 叙述本刀未接通；解锁时间未在官网上核对的不在此假装已核。</div>';

    var evalPanel = q('#evalRuntimePanel');
    if (evalPanel && evalPanel.parentNode) {
      evalPanel.parentNode.insertBefore(panel, evalPanel.nextSibling);
    } else {
      var firstPanel = section.querySelector('.panel');
      if (firstPanel) section.insertBefore(panel, firstPanel.nextSibling);
      else section.appendChild(panel);
    }
  }

  function renderEffect() {
    var box = q('#reviewEffect');
    var st = state.status || {};
    var eff = st.server_effect || {};
    if (!box) return;
    var g = state.grades || {};
    box.innerHTML =
      '<div class="stat"><label>暂停新入场</label><strong>' +
      (st.pause_new_entries ? '是（已生效）' : '否') +
      '</strong><small>不停止原仓跟踪</small></div>' +
      '<div class="stat"><label>启用计划数</label><strong>' +
      fmt(eff.enabled_plan_count || 0, 0) +
      '</strong><small>' + esc((eff.enabled_plan_ids || []).join(', ') || '无') + '</small></div>' +
      '<div class="stat"><label>可核价样本 / 不可核</label><strong>' +
      fmt(g.sample_count || 0, 0) + ' / ' + fmt(g.unverifiable_count || 0, 0) +
      '</strong><small>胜率 ' + esc(g.win_rate_label || '无样本') + '</small></div>' +
      '<div class="stat"><label>调度绑定</label><strong>' +
      (eff.scheduler_bound ? '已绑定' : '未绑定') +
      '</strong><small>复盘 ' + fmt(st.review_count || 0, 0) + ' 条</small></div>';
    var note = q('#reviewPauseNote');
    if (note) note.textContent = st.pause_note || '暂停新入场不影响原仓跟踪与结算';
    var btn = q('#reviewPauseBtn');
    if (btn) btn.textContent = st.pause_new_entries ? '恢复新入场' : '暂停新入场';
  }

  function render() {
    renderEffect();
    var kindSel = q('#reviewPlanKind');
    var wrap = q('#reviewWeekdayWrap');
    if (kindSel && wrap) wrap.hidden = kindSel.value !== 'weekly';

    var list = q('#reviewList');
    if (list) {
      if (!state.reviews.length) {
        list.innerHTML = '<tr><td colspan="4">暂无复盘（重启后仍可从磁盘回放）</td></tr>';
      } else {
        list.innerHTML = state.reviews.slice(0, 40).map(function (r) {
          var sum = r.numerical_summary || {};
          var kindLabel = r.kind === 'data' ? '数据' : (r.kind === 'ai_manual' ? '手动AI' : r.kind);
          var ai = r.ai_called ? '已调用' : (r.ai_status || '未调用');
          return '<tr><td>' + esc((r.created_at || '').replace('T', ' ').slice(0, 19)) +
            '</td><td>' + esc(kindLabel) +
            '</td><td>胜率 ' + esc(sum.win_rate_label || '—') +
            ' · 结算 ' + esc(sum.settlement_count == null ? '—' : sum.settlement_count) +
            ' · 进行中 ' + esc(sum.open_observation_count == null ? '—' : sum.open_observation_count) +
            '</td><td>' + esc(ai) + '</td></tr>';
        }).join('');
      }
    }

    var plans = q('#reviewPlans');
    if (plans) {
      if (!state.plans.length) {
        plans.innerHTML = '<tr><td colspan="5">暂无计划</td></tr>';
      } else {
        plans.innerHTML = state.plans.map(function (p) {
          var kindLabel = p.kind === 'daily' ? '每日' : '每周';
          var when = p.time_of_day || '';
          if (p.kind === 'weekly' && p.weekday) when = '周' + p.weekday + ' ' + when;
          var next = p.next_run_at ? String(p.next_run_at).replace('T', ' ').slice(0, 19) + '（源时区）' : '—';
          var act = p.enabled
            ? '<button type="button" data-review="pause-plan" data-pid="' + esc(p.plan_id) + '">停用</button>'
            : '<button type="button" data-review="enable-plan" data-pid="' + esc(p.plan_id) + '">启用</button>';
          return '<tr><td>' + esc(kindLabel) + '</td><td>' + esc(when) +
            '</td><td>' + (p.enabled ? '启用' : '停用') +
            '</td><td>' + esc(next) + '</td><td>' + act + '</td></tr>';
        }).join('');
      }
    }
    var vsel = q('#reviewVersion');
    if (vsel) {
      var keep = vsel.value;
      vsel.innerHTML = '<option value="">（全库汇总）</option>' + (state.versions || []).map(function (v) {
        return '<option value="' + esc(v.version_id) + '">' + esc((v.title || v.strategy_id) + ' @' + (v.version || '')) + '</option>';
      }).join('');
      if (keep) vsel.value = keep;
    }
    var setBody = q('#reviewSettlements');
    if (setBody) {
      var rows = state.settlements || [];
      if (!rows.length) {
        setBody.innerHTML = '<tr><td colspan="5">暂无结算；先在上方前向观察里规则结算</td></tr>';
      } else {
        setBody.innerHTML = rows.slice(0, 20).map(function (s) {
          var outcome = s.outcome || '';
          var reason = s.exit_reason || '';
          var flag = (outcome === 'unverifiable' || reason === 'unverifiable' || reason === 'data_gap' || reason === 'pending_backfill') ? ' · 不计假赢' : '';
          var pnl = s.realized_pnl_gross == null ? '—' : fmt(s.realized_pnl_gross, 2);
          return '<tr><td>' + esc((s.created_at || '').replace('T', ' ').slice(0, 19)) +
            '</td><td>' + esc(s.symbol) + '</td><td>' + esc(outcome) + flag +
            '</td><td>' + esc(reason) + '</td><td>' + pnl + '</td></tr>';
        }).join('');
      }
    }
  }

  function refresh() {
    ensurePanel();
    return Promise.all([
      api('/api/review/status'),
      api('/api/review/reviews?limit=40'),
      api('/api/review/plans'),
      api('/api/eval/settlements?limit=20').catch(function () { return { settlements: [] }; }),
      api('/api/eval/grades').catch(function () { return null; }),
      api('/api/strategies').catch(function () { return { versions: [] }; }),
    ]).then(function (pair) {
      state.status = pair[0];
      state.reviews = pair[1].reviews || [];
      state.plans = pair[2].plans || [];
      state.settlements = pair[3].settlements || [];
      state.grades = pair[4];
      state.versions = (pair[5].versions || []).filter(function (v) { return v.enabled; });
      var eff = (state.status && state.status.server_effect) || {};
      var g = state.grades || {};
      q('#reviewStatus').textContent =
        '暂停=' + (state.status.pause_new_entries ? '是' : '否') +
        ' · 启用计划 ' + (eff.enabled_plan_count || 0) +
        ' · 复盘 ' + (state.status.review_count || 0) +
        ' · 结算样本 ' + (g.sample_count || 0) +
        ' · 不可核 ' + (g.unverifiable_count || 0) +
        ' · ' + (eff.scheduler_bound ? '调度已绑定' : '调度未绑定');
      render();
    }).catch(function (e) {
      ensurePanel();
      q('#reviewStatus').textContent = '加载失败: ' + (e.message || e);
    });
  }

  function doDataReview() {
    var body = {};
    var vid = (q('#reviewVersion') || {}).value;
    if (vid) body.strategy_version_id = vid;
    api('/api/review/data', { method: 'POST', body: body }).then(function (rec) {
      var sum = (rec && rec.numerical_summary) || {};
      toast('数据复盘已保存: ' + (rec.review_id || '') + ' · 胜率 ' + (sum.win_rate_label || '无样本') + '（不调模型）');
      return refresh();
    }).catch(function (e) { toast('拒绝: ' + (e.message || e)); });
  }

  function doAiReview() {
    if (!window.confirm('手动 AI 复盘：本刀只做 Key 门禁与数值快照，不会发起付费模型调用。继续？')) return;
    var body = {};
    var vid = (q('#reviewVersion') || {}).value;
    if (vid) body.strategy_version_id = vid;
    api('/api/review/ai', { method: 'POST', body: body }).then(function (rec) {
      toast('手动 AI 门禁通过（未调用模型）: ' + (rec.review_id || ''));
      return refresh();
    }).catch(function (e) { toast('拒绝: ' + (e.message || e)); });
  }

  function doTogglePause() {
    var next = !(state.status && state.status.pause_new_entries);
    api('/api/review/pause-new-entries', { method: 'PUT', body: { pause_new_entries: next } })
      .then(function () {
        toast(next ? '已暂停新入场（原仓继续跟踪）' : '已恢复新入场');
        return refresh();
      })
      .catch(function (e) { toast('拒绝: ' + (e.message || e)); });
  }

  function doSavePlan() {
    var kind = q('#reviewPlanKind').value;
    var body = {
      kind: kind,
      time_of_day: (q('#reviewPlanTime').value || '21:00').trim(),
      timezone: 'Asia/Shanghai',
      enabled: false,
    };
    if (kind === 'weekly') body.weekday = Number(q('#reviewPlanWeekday').value) || 1;
    api('/api/review/plans', { method: 'PUT', body: body }).then(function (res) {
      toast('计划已保存: ' + ((res.plan && res.plan.plan_id) || ''));
      return refresh();
    }).catch(function (e) { toast('拒绝: ' + (e.message || e)); });
  }

  function setPlan(pid, enabled) {
    api('/api/review/plans/' + encodeURIComponent(pid) + '/enable', {
      method: 'POST',
      body: { enabled: enabled },
    }).then(function () {
      toast(enabled ? '计划已启用（服务端生效标记已更新）' : '计划已停用');
      return refresh();
    }).catch(function (e) { toast('拒绝: ' + (e.message || e)); });
  }

  function bind() {
    ensurePanel();
    var section = q('[data-view="validation"]');
    if (!section || section.getAttribute('data-review-bound')) return;
    section.setAttribute('data-review-bound', '1');
    section.addEventListener('click', function (ev) {
      var btn = ev.target.closest('[data-review]');
      if (!btn) return;
      var act = btn.getAttribute('data-review');
      if (act === 'refresh') refresh();
      if (act === 'data') doDataReview();
      if (act === 'ai') doAiReview();
      if (act === 'toggle-pause') doTogglePause();
      if (act === 'save-plan') doSavePlan();
      if (act === 'enable-plan') setPlan(btn.getAttribute('data-pid'), true);
      if (act === 'pause-plan') setPlan(btn.getAttribute('data-pid'), false);
    });
    section.addEventListener('change', function (ev) {
      if (ev.target && ev.target.id === 'reviewPlanKind') render();
    });
  }

  function boot() {
    if (!q('[data-view="validation"]')) return;
    bind();
    refresh();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
  window.ReviewStoreUI = { refresh: refresh };
})();
