/** EVAL-01: 前向观察 / 撮合结算 / 成绩与记录回放（研究/模拟，非实盘）。 */
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
    var el = q('#evalStatus');
    if (el) el.textContent = msg;
    else console.log('[验证]', msg);
  }
  function fmt(n, d) {
    if (n == null || !isFinite(Number(n))) return '—';
    return Number(n).toLocaleString('zh-CN', { maximumFractionDigits: d == null ? 4 : d });
  }


  function shortHash(h) { return String(h || '').slice(0, 12); }
  function versionParams(v) {
    var rules = (v && v.rules) || {};
    var params = rules.params;
    return (params && typeof params === 'object') ? params : {};
  }
  function executionModeFromParams(p) {
    if (!p) return 'pct_touch';
    var m = p.execution_mode || p.exit_mode || p.mode;
    if (!m) return 'pct_touch';
    m = String(m).toLowerCase();
    if (m === 'ema99' || m === 'ema99_trail' || m === 'ema99_close' || m === 'close_trail' || m === 'ema99_close_trail') return 'ema99_close_trail';
    return m;
  }
  function syncEvalModeFields() {
    var ver = findVersion(q('#evalVersion') && q('#evalVersion').value);
    var mode = executionModeFromParams(versionParams(ver));
    var box = q('#evalEmaFields');
    if (box) box.hidden = mode !== 'ema99_close_trail';
  }

  function findVersion(id) {
    if (!id) return null;
    for (var i = 0; i < (state.versions || []).length; i++) {
      if (state.versions[i].version_id === id) return state.versions[i];
    }
    return null;
  }
  function renderFrozenNote(v) {
    var el = q('#evalFrozenNote');
    if (!el) return;
    syncEvalModeFields();
    if (!v) {
      el.textContent = '开观察只服从已启用冻结版参数；EMA99 模式不要求也不发明固定止盈/止损/持仓天。';
      return;
    }
    var p = versionParams(v);
    var mode = executionModeFromParams(p);
    var bits = ['执行模式 ' + mode];
    if (p.side) bits.push('方向 ' + p.side);
    if (p.entry_rule) bits.push('入场 ' + p.entry_rule);
    if (mode === 'ema99_close_trail') {
      bits.push('初始止损 ' + (p.initial_stop || 'cross_bar_ema99'));
      bits.push('追踪 ' + (p.trail_activation || 'first_close_above_entry'));
      bits.push('盘中触价 ' + (p.intrabar_stop === true ? '是' : '否'));
      bits.push('候选期 ' + (p.candidate_max_days != null ? p.candidate_max_days : 30) + '天(≠持仓)');
      bits.push('不使用固定tp/sl/持仓天');
    } else {
      if (p.tp_pct != null) bits.push('止盈 ' + p.tp_pct);
      if (p.sl_pct != null) bits.push('止损 ' + p.sl_pct);
      if (p.max_hold_days != null) bits.push('持仓 ' + p.max_hold_days + '天');
    }
    el.textContent = (v.title || v.strategy_id) + ' @' + (v.version || '')
      + ' · 指纹 ' + shortHash(v.content_hash)
      + ' · ' + bits.join(' · ')
      + ' · 只服从冻结；未声明参数不由页面补默认';
  }

  var state = { versions: [], open: [], settlements: [], grades: null, links: [] };

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
    if (!section || q('#evalRuntimePanel')) return;
    var panel = document.createElement('div');
    panel.className = 'panel';
    panel.id = 'evalRuntimePanel';
    panel.innerHTML =
      '<div class="panel-head"><div><h2>前向观察与结算（EVAL-01 首刀）</h2>' +
      '<small>观察期与持仓期限分离 · 同根/跳空/断档固定规则 · 无样本不填胜率 · 研究/模拟非实盘</small></div>' +
      '<span class="pill gray">纸面结算</span></div>' +
      '<p id="evalStatus" class="muted">加载中…</p>' +
      '<div class="stats" id="evalGrades"></div>' +
      '<div class="row" style="margin-top:12px;gap:8px;flex-wrap:wrap">' +
      '<label>启用冻结版本 <select id="evalVersion"></select></label>' +
      '<label>合约 <input id="evalSymbol" placeholder="如 BTCUSDT" style="width:120px"></label>' +
      '<label title="若冻结版声明了 side，开观察用冻结方向">方向(可被冻结覆盖) <select id="evalSide"><option value="long">多</option><option value="short">空</option></select></label>' +
      '<label>观察期(天) <input id="evalHorizon" type="number" min="1" max="3650" value="30" style="width:70px" title="停止新入场的观察跨度；不覆盖冻结持仓期限"></label>' +
      '</div>' +
      '<p id="evalFrozenNote" class="muted" style="margin-top:8px">开观察只服从已启用冻结版参数；模式决定是否需要固定止盈止损。</p>' +
      '<div id="evalEmaFields" hidden class="row" style="margin-top:8px;gap:8px;flex-wrap:wrap">' +
      '<label>确认事件ID <input id="evalConfirmId" placeholder="confirm event / trigger" style="width:180px"></label>' +
      '<label>金叉根EMA99 <input id="evalCrossEma99" type="number" step="any" placeholder="初始止损" style="width:120px"></label>' +
      '<span class="muted">EMA99模式必填；缺则引擎409，页面不发明3%止损</span></div>' +
      '<div class="command-bar" style="margin-top:12px">' +
      '<span id="evalRulesNote" class="muted"></span>' +
      '<div class="row">' +
      '<button type="button" data-eval="refresh">刷新</button>' +
      '<button type="button" class="primary" data-eval="start">开始前向观察</button>' +
      '</div></div>' +
      '<div class="grid2" style="margin-top:16px">' +
      '<div><h3>进行中的观察</h3><div class="tablewrap"><table><thead><tr>' +
      '<th>合约</th><th>模式</th><th>止损/追踪</th><th>入场</th><th>操作</th></tr></thead>' +
      '<tbody id="evalOpen"><tr><td colspan="5">暂无</td></tr></tbody></table></div></div>' +
      '<div><h3>结算记录（可回放）</h3><div class="tablewrap"><table><thead><tr>' +
      '<th>时间</th><th>合约</th><th>结果</th><th>原因</th><th>盈亏</th></tr></thead>' +
      '<tbody id="evalSettlements"><tr><td colspan="5">暂无</td></tr></tbody></table></div></div>' +
      '</div>' +
      '<div style="margin-top:16px"><h3>补查联动状态</h3>' +
      '<small class="muted">pending_backfill / unverifiable / data_gap → 联动记录；仅计划 auto_enqueue_backfill=true 时入队 candle_range</small>' +
      '<div class="tablewrap"><table><thead><tr>' +
      '<th>时间</th><th>合约</th><th>原因</th><th>状态</th><th>任务</th></tr></thead>' +
      '<tbody id="evalBackfillLinks"><tr><td colspan="5">暂无</td></tr></tbody></table></div></div>' +
      '<div class="note">歧义规则：同根止盈止损 → 对交易者不利（按止损）；跳空穿过 → 以开盘价成交；断档 → data_gap，不计假赢。历史回测属 BACKTEST-01；解锁时间未在官网上核对的，不在本页假装已核。</div>';

    var firstPanel = section.querySelector('.panel');
    if (firstPanel) section.insertBefore(panel, firstPanel.nextSibling);
    else section.appendChild(panel);
  }

  function renderGrades() {
    var box = q('#evalGrades');
    var g = state.grades || {};
    if (!box) return;
    var wr = g.win_rate_label || '无样本';
    var wrStrong = g.sample_count > 0 ? (fmt((g.win_rate || 0) * 100, 2) + '%') : '无样本';
    box.innerHTML =
      '<div class="stat"><label>可核价样本</label><strong>' + fmt(g.sample_count || 0, 0) + '</strong><small>win/loss/flat</small></div>' +
      '<div class="stat"><label>胜率</label><strong>' + esc(wrStrong) + '</strong><small>' + esc(g.sample_count > 0 ? ('胜 ' + g.wins + ' / 负 ' + g.losses) : '不填 0%') + '</small></div>' +
      '<div class="stat"><label>断档 / 不可核</label><strong>' + fmt(g.data_gap_count || 0, 0) + ' / ' + fmt(g.unverifiable_count || 0, 0) + '</strong><small>不计胜率分母</small></div>' +
      '<div class="stat"><label>结算总数</label><strong>' + fmt(g.settlement_count || 0, 0) + '</strong><small>磁盘可回放</small></div>';
  }

  function render() {
    renderGrades();
    var vsel = q('#evalVersion');
    if (vsel) {
      var keep = vsel.value;
      var valRef = state.bindings && state.bindings.surfaces && state.bindings.surfaces.validation && state.bindings.surfaces.validation.ref;
      if (!keep && valRef && valRef.version_id) keep = valRef.version_id;
      if (!state.versions.length) {
        vsel.innerHTML = '<option value="">（无已启用冻结版本）</option>';
      } else {
        vsel.innerHTML = state.versions.map(function (v) {
          return '<option value="' + esc(v.version_id) + '">' + (((/(?:^|[-_\s])(smoke|probe|fixture|test)(?:[-_\s]|$)|测试/i.test((v.title||'')+' '+(v.strategy_id||'')))?'[测试版本] ':'')+esc((v.title || v.strategy_id) + ' @' + (v.version || ''))) + '</option>';
        }).join('');
        if (keep) vsel.value = keep;
        renderFrozenNote(findVersion(vsel.value));
      }
    }
    var openBody = q('#evalOpen');
    if (openBody) {
      if (!state.open.length) {
        openBody.innerHTML = '<tr><td colspan="5">暂无进行中的观察</td></tr>';
      } else {
        openBody.innerHTML = state.open.map(function (o) {
          var mode = o.execution_mode || (o.frozen_exec && o.frozen_exec.execution_mode) || 'pct_touch';
          var stopBits = [];
          if (o.initial_stop != null) stopBits.push('初 ' + fmt(o.initial_stop, 6));
          if (o.current_stop != null) stopBits.push('现 ' + fmt(o.current_stop, 6));
          else if (o.sl_price != null) stopBits.push('止损价 ' + fmt(o.sl_price, 6));
          if (o.trailing_activated) stopBits.push('追踪已开');
          if (o.pending_exit) stopBits.push('待下开盘出·' + (o.pending_exit_reason || o.pending_reason || ''));
          if (mode === 'pct_touch' && o.tp_price != null) stopBits.push('止盈价 ' + fmt(o.tp_price, 6));
          return '<tr><td>' + esc(o.symbol) + '<div class="muted">' + esc(o.observation_horizon_days) + '天观察'
            + (o.max_hold_days != null ? (' / ' + esc(o.max_hold_days) + '天持仓') : ' / 无固定持仓天') + '</div></td><td>'
            + esc(mode) + '<div class="muted"><code>' + esc(o.strategy_version_id) + '</code></div></td><td>'
            + esc(stopBits.join(' · ') || '—') + '</td><td>' + fmt(o.entry_price, 4) + '</td><td>'
            + '<button type="button" data-eval="settle-rules" data-oid="' + esc(o.observation_id) + '">规则结算</button> '
            + '<button type="button" data-eval="settle-manual" data-oid="' + esc(o.observation_id) + '">手动平</button></td></tr>';
        }).join('');
      }
    }
    var setBody = q('#evalSettlements');
    if (setBody) {
      if (!state.settlements.length) {
        setBody.innerHTML = '<tr><td colspan="5">暂无结算（重启后仍可从磁盘回放）</td></tr>';
      } else {
        setBody.innerHTML = state.settlements.slice(0, 40).map(function (s) {
          var pnl = s.realized_pnl_gross == null ? '—' : fmt(s.realized_pnl_gross, 2);
          return '<tr><td>' + esc((s.created_at || '').replace('T', ' ').slice(0, 19)) +
            '</td><td>' + esc(s.symbol) + '</td><td>' + esc(s.outcome) +
            '</td><td>' + esc(s.exit_reason) + (s.fill_rule ? (' · ' + esc(s.fill_rule)) : '') +
            '</td><td>' + pnl + '</td></tr>';
        }).join('');
      }
    }
    var linkBody = q('#evalBackfillLinks');
    if (linkBody) {
      var links = state.links || [];
      if (!links.length) {
        linkBody.innerHTML = '<tr><td colspan="5">暂无补查联动</td></tr>';
      } else {
        linkBody.innerHTML = links.slice(0, 30).map(function (L) {
          var st = L.status || L.enqueue_status || 'linked';
          var tid = L.task_id ? ('<code>' + esc(L.task_id) + '</code>') : '—';
          if (L.reused) tid += ' · reused';
          if (L.enqueue_error) tid += ' · ' + esc(String(L.enqueue_error).slice(0, 40));
          return '<tr><td>' + esc((L.created_at || '').replace('T', ' ').slice(0, 19)) +
            '</td><td>' + esc(L.symbol) + '</td><td>' + esc(L.reason) +
            '</td><td>' + esc(st) + '</td><td>' + tid + '</td></tr>';
        }).join('');
      }
    }
  }

  function refresh() {
    ensurePanel();
    return Promise.all([
      api('/api/eval/observations?status=open&limit=50'),
      api('/api/eval/settlements?limit=40'),
      api('/api/eval/grades'),
      api('/api/strategies').catch(function () { return { versions: [] }; }),
      api('/api/eval/rules').catch(function () { return {}; }),
      api('/api/eval/backfill-links?limit=30').catch(function () { return { links: [] }; }),
      api('/api/eval/status').catch(function () { return {}; }),
      api('/api/strategies/bindings').catch(function () { return null; }),
    ]).then(function (pair) {
      state.open = pair[0].observations || [];
      state.settlements = pair[1].settlements || [];
      state.grades = pair[2];
      state.versions = (pair[3].versions || []).filter(function (v) { return v.enabled; });
      state.links = pair[5].links || [];
      state.status = pair[6] || {};
      state.bindings = pair[7];
      var rules = pair[4] || {};
      var note = q('#evalRulesNote');
      if (note) {
        note.textContent = '同根=' + (rules.same_bar_tp_sl || 'worse_for_trader') +
          ' · 跳空=' + (rules.gap_through_level || 'fill_at_open') +
          ' · 断档=' + (rules.missing_bars || 'data_gap');
      }
      var se = state.status.server_effect || {};
      q('#evalStatus').textContent =
        '进行中 ' + state.open.length + ' · 结算 ' + (state.grades.settlement_count || 0) +
        ' · 胜率 ' + (state.grades.win_rate_label || '无样本') +
        ' · 补查联动 ' + state.links.length +
        ' · 调度计划 ' + (se.enabled_plan_count || 0) +
        (se.pause_new_entries_active ? ' · 已暂停新入场' : '');
      render();
    }).catch(function (e) {
      ensurePanel();
      q('#evalStatus').textContent = '加载失败: ' + (e.message || e);
    });
  }

  function doStart() {
    var versionId = q('#evalVersion').value;
    var symbol = (q('#evalSymbol').value || '').trim().toUpperCase();
    if (!versionId) return toast('请选择已启用的冻结策略版本');
    if (!symbol) return toast('请填写合约代码');
    var ver = findVersion(versionId);
    var p = versionParams(ver);
    var mode = executionModeFromParams(p);
    if (mode === 'pct_touch') {
      if (p.tp_pct == null || p.sl_pct == null || p.max_hold_days == null) {
        return toast('pct_touch 冻结版缺少止盈/止损/持仓天数，开观察会被拒绝');
      }
    } else if (mode === 'ema99_close_trail') {
      // 不要求也不补 tp/sl/持仓天；确认事件与金叉根 EMA99 由引擎强校验
      var cid = (q('#evalConfirmId') && q('#evalConfirmId').value || '').trim();
      var c99 = q('#evalCrossEma99') && q('#evalCrossEma99').value;
      if (!cid) return toast('EMA99 模式请填写确认事件ID（引擎也会拒绝缺证据）');
      if (c99 === '' || c99 == null) return toast('EMA99 模式请填写金叉根 EMA99（初始止损）；不发明3%');
    } else {
      return toast('未知执行模式 ' + mode + '；拒绝开观察');
    }
    var body = {
      strategy_version_id: versionId,
      symbol: symbol,
      observation_horizon_days: Number(q('#evalHorizon').value) || 30,
    };
    if (p.side) body.side = p.side;
    else if (q('#evalSide') && q('#evalSide').value) body.side = q('#evalSide').value;
    if (mode === 'ema99_close_trail') {
      body.confirm_event_id = (q('#evalConfirmId').value || '').trim();
      body.cross_ema99 = Number(q('#evalCrossEma99').value);
      // 不向 body 塞 tp_pct/sl_pct/max_hold_days，避免与冻结冲突或发明默认
    }
    api('/api/eval/observations/start', { method: 'POST', body: body }).then(function () {
      toast('前向观察已开始 · ' + mode);
      return refresh();
    }).catch(function (e) { toast('拒绝: ' + (e.message || e)); });
  }

  function doSettle(oid, mode) {
    var body = { mode: mode };
    if (mode === 'manual') {
      var mark = window.prompt('手动平仓标记价（留空则用 scan.json）', '');
      if (mark === null) return;
      if (mark !== '') body.exit_price = Number(mark);
      body.exit_reason = 'manual';
    }
    api('/api/eval/observations/' + encodeURIComponent(oid) + '/settle', { method: 'POST', body: body })
      .then(function (res) {
        var s = res.settlement || {};
        toast('已结算: ' + (s.outcome || '') + ' / ' + (s.exit_reason || ''));
        return refresh();
      })
      .catch(function (e) { toast('结算拒绝: ' + (e.message || e)); });
  }

  function bind() {
    ensurePanel();
    var section = q('[data-view="validation"]');
    if (!section || section.getAttribute('data-eval-bound')) return;
    section.setAttribute('data-eval-bound', '1');
    section.addEventListener('click', function (ev) {
      var btn = ev.target.closest('[data-eval]');
      if (!btn) return;
      var act = btn.getAttribute('data-eval');
      if (act === 'refresh') refresh();
      if (act === 'start') doStart();
      if (act === 'settle-rules') doSettle(btn.getAttribute('data-oid'), 'rules');
      if (act === 'settle-manual') doSettle(btn.getAttribute('data-oid'), 'manual');
    });
    section.addEventListener('change', function (ev) {
      if (ev.target && ev.target.id === 'evalVersion') {
        renderFrozenNote(findVersion(ev.target.value));
      }
    });
  }

  function boot() {
    if (!q('[data-view="validation"]')) return;
    bind();
    refresh();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
  window.EvalStoreUI = { refresh: refresh };
})();
