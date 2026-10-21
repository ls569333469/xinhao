/** SIM-01: 模拟篮子纸面账户与事件账本（研究/模拟，非实盘）。 */
(function () {
  'use strict';
  function q(sel, root) { return (root || document).querySelector(sel); }
  function qa(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }
  function esc(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function toast(msg) {
    if (typeof window.showToast === 'function') return window.showToast(msg);
    var el = q('#simStatus');
    if (el) el.textContent = msg;
    else console.log('[模拟]', msg);
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


  function findVersion(id) {
    if (!id) return null;
    for (var i = 0; i < state.versions.length; i++) {
      if (state.versions[i].version_id === id) return state.versions[i];
    }
    return null;
  }

  function renderFrozenNote(v) {
    var el = q('#simFrozenNote');
    if (!el) return;
    if (!v) {
      el.textContent = '自动填仓必须选已启用冻结版本；参数只读冻结 rules.params，页面不发明缺失止盈止损。';
      return;
    }
    var p = versionParams(v);
    var mode = executionModeFromParams(p);
    var bits = ['执行模式 ' + mode];
    if (p.side) bits.push('方向 ' + p.side);
    if (p.notional_u != null) bits.push('名义 ' + p.notional_u + 'U');
    if (p.entry_rule) bits.push('入场 ' + p.entry_rule);
    if (mode === 'ema99_close_trail') {
      bits.push('初始止损 ' + (p.initial_stop || 'cross_bar_ema99'));
      bits.push('追踪 ' + (p.trail_activation || 'first_close_above_entry'));
      bits.push('无固定tp/sl；缺确认/预热由引擎拒绝');
    } else {
      if (p.tp_pct != null) bits.push('止盈 ' + p.tp_pct);
      if (p.sl_pct != null) bits.push('止损 ' + p.sl_pct);
      if (p.max_hold_days != null) bits.push('持有 ' + p.max_hold_days + '天');
    }
    el.textContent = (v.title || v.strategy_id) + ' @' + (v.version || '')
      + ' · 指纹 ' + shortHash(v.content_hash)
      + (v.enabled ? ' · 已启用' : ' · 未启用（自动填仓会拒）')
      + ' · ' + bits.join(' · ')
      + ' · 只服从冻结，不跟手填覆盖';
  }

  var state = { accounts: [], events: [], versions: [], accountId: 'acc-shared' };

  function api(path, opts) {
    opts = opts || {};
    var init = {
      method: opts.method || 'GET',
      headers: { 'Accept': 'application/json' },
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
    var section = q('[data-view="basket"]');
    if (!section || q('#simRuntimePanel')) return;
    var panel = document.createElement('div');
    panel.className = 'panel';
    panel.id = 'simRuntimePanel';
    panel.innerHTML =
      '<div class="panel-head"><div><h2>纸面账户与事件账本（SIM-01）</h2>' +
      '<small>研究/模拟专用 · 不会向交易所下单 · EVAL-01 前向观察首刀见「验证与复盘」</small></div>' +
      '<span class="pill gray">纸面 · 非实盘</span></div>' +
      '<p id="simStatus" class="muted">加载中…</p>' +
      '<div class="stats" id="simStats"></div>' +
      '<div class="row" style="margin-top:12px;gap:8px;flex-wrap:wrap">' +
      '<label>账户 <select id="simAccount"></select></label>' +
      '<label>合约 <input id="simSymbol" placeholder="如 BTCUSDT" style="width:120px"></label>' +
      '<label>方向 <select id="simSide"><option value="long">多</option><option value="short">空</option></select></label>' +
      '<label>名义U <input id="simNotional" type="number" min="1" value="100" style="width:90px"></label>' +
      '<label>杠杆 <input id="simLev" type="number" min="1" max="2" step="0.1" value="1" style="width:70px"></label>' +
      '<label>冻结版本 <select id="simVersion"><option value="">（请选择已启用冻结版）</option></select></label>' +
      '</div>' +
      '<p id="simFrozenNote" class="muted" style="margin-top:8px">自动填仓必须选已启用冻结版本；参数只读冻结 rules.params，手填名义/方向不会覆盖。</p>' +
      '<div class="command-bar" style="margin-top:12px">' +
      '<span id="simPolicyNote"></span>' +
      '<div class="row">' +
      '<button type="button" data-sim="refresh">刷新</button>' +
      '<button type="button" class="primary" data-sim="open">模拟开仓</button>' +
      '<button type="button" data-sim="preview-af">预览自动填仓</button>' +
      '<button type="button" data-sim="autofill">确认自动填仓</button>' +
      '<button type="button" data-sim="close">模拟平仓</button>' +
      '</div></div>' +
      '<div class="grid2" style="margin-top:16px">' +
      '<div><h3>持仓</h3><div class="tablewrap"><table><thead><tr><th>合约</th><th>方向</th><th>数量</th><th>开仓价</th><th>保证金</th><th>版本</th></tr></thead>' +
      '<tbody id="simPositions"><tr><td colspan="6">空仓</td></tr></tbody></table></div></div>' +
      '<div><h3>最近事件</h3><div class="tablewrap"><table><thead><tr><th>时间</th><th>类型</th><th>合约</th><th>摘要</th><th>事件ID</th></tr></thead>' +
      '<tbody id="simEvents"><tr><td colspan="5">暂无</td></tr></tbody></table></div></div>' +
      '</div>' +
      '<div class="note">同币冲突策略：共享篮子拒绝第二次开仓。独立对照账户互不占用。无触发可空仓。资金守恒：cash + margin + fees − 毛盈亏 = 初始本金。</div>';

    // Insert after lead/stats or at end of section
    var ledger = null;
    qa('.panel', section).forEach(function (p) {
      var h = p.querySelector('h2');
      if (h && h.textContent.indexOf('持仓与事件账本') >= 0) ledger = p;
    });
    if (ledger) section.insertBefore(panel, ledger);
    else section.appendChild(panel);

    // Soften demo empty copy
    if (ledger) {
      var empty = ledger.querySelector('.empty');
      if (empty) {
        empty.innerHTML = '<strong>下方「纸面账户」已接入 SIM-01 首刀</strong><p>正式自动撮合/前向成绩仍属 EVAL-01；此处可手动模拟开平仓验证账本与守恒。</p>';
      }
    }
  }

  function currentAccount() {
    var id = (q('#simAccount') && q('#simAccount').value) || state.accountId || 'acc-shared';
    for (var i = 0; i < state.accounts.length; i++) {
      if (state.accounts[i].account_id === id) return state.accounts[i];
    }
    return state.accounts[0] || null;
  }

  function render() {
    var acc = currentAccount();
    var stats = q('#simStats');
    if (stats && acc) {
      stats.innerHTML =
        '<div class="stat"><label>权益（账面）</label><strong>' + fmt(acc.equity_book, 2) + ' U</strong><small>现金+占用保证金</small></div>' +
        '<div class="stat"><label>可用现金</label><strong>' + fmt(acc.cash, 2) + ' U</strong><small>开仓预算</small></div>' +
        '<div class="stat"><label>占用保证金</label><strong>' + fmt(acc.margin_locked, 2) + ' U</strong><small>持仓 ' + acc.position_count + '</small></div>' +
        '<div class="stat"><label>已付费用 / 毛盈亏</label><strong>' + fmt(acc.fees_paid, 4) + ' / ' + fmt(acc.realized_pnl_gross, 2) + '</strong><small>守恒 ' + (acc.conservation_ok ? '通过' : '失败') + '</small></div>';
    }
    var sel = q('#simAccount');
    if (sel) {
      var cur = sel.value || state.accountId;
      sel.innerHTML = state.accounts.map(function (a) {
        return '<option value="' + esc(a.account_id) + '">' + esc(a.label || a.account_id) + ' · ' + fmt(a.cash, 1) + 'U</option>';
      }).join('');
      if (cur) sel.value = cur;
    }
    var posBody = q('#simPositions');
    if (posBody) {
      var positions = (acc && acc.positions) || [];
      if (!positions.length) {
        posBody.innerHTML = '<tr><td colspan="6">空仓（允许）</td></tr>';
      } else {
        posBody.innerHTML = positions.map(function (p) {
          return '<tr><td>' + esc(p.symbol) + '</td><td>' + esc(p.side) + '</td><td>' + fmt(p.qty, 6) +
            '</td><td>' + fmt(p.entry_price, 6) + '</td><td>' + fmt(p.margin, 2) + '</td><td>' +
            esc(p.strategy_version_id || '—') + '</td></tr>';
        }).join('');
      }
    }
    var evBody = q('#simEvents');
    if (evBody) {
      if (!state.events.length) {
        evBody.innerHTML = '<tr><td colspan="5">暂无事件</td></tr>';
      } else {
        evBody.innerHTML = state.events.slice(0, 30).map(function (e) {
          var summary = e.type === 'open'
            ? (esc(e.side) + ' @' + fmt(e.price, 4) + ' fee ' + fmt(e.fee, 4))
            : (e.type === 'close'
              ? ('平 @' + fmt(e.price, 4) + ' 毛盈亏 ' + fmt(e.realized_pnl_gross, 2))
              : esc(e.note || e.status || ''));
          return '<tr><td>' + esc((e.created_at || '').replace('T', ' ').slice(0, 19)) +
            '</td><td>' + esc(e.type) + '</td><td>' + esc(e.symbol || '—') +
            '</td><td>' + summary + '</td><td><code>' + esc(e.event_id) + '</code></td></tr>';
        }).join('');
      }
    }
    var note = q('#simPolicyNote');
    if (note && state.policyNote) note.textContent = state.policyNote;
    var cnt = q('#basketStrategyCount');
    if (cnt) {
      var n = (state.versions || []).filter(function (v) { return v.enabled; }).length;
      cnt.textContent = String(n);
      var small = cnt.parentNode && cnt.parentNode.querySelector('small');
      if (small) small.textContent = n ? '已启用冻结版本（纸面可引用）' : '尚无已启用冻结版本';
    }
  }

  function refresh() {
    ensurePanel();
    return Promise.all([
      api('/api/sim/accounts'),
      api('/api/sim/events?limit=40'),
      api('/api/strategies').catch(function () { return { versions: [] }; }),
      api('/api/strategies/bindings').catch(function () { return null; }),
    ]).then(function (pair) {
      var accData = pair[0];
      var evData = pair[1];
      var strat = pair[2] || {};
      state.bindings = pair[3];
      state.accounts = accData.accounts || [];
      state.events = evData.events || [];
      state.policyNote = (accData.config && accData.config.same_symbol_policy_note) || '';
      state.versions = strat.versions || [];
      state.bindings = state.bindings || null;
      var vsel = q('#simVersion');
      if (vsel) {
        var keep = vsel.value;
        var basketRef = state.bindings && state.bindings.surfaces && state.bindings.surfaces.basket && state.bindings.surfaces.basket.ref;
        if (!keep && basketRef && basketRef.version_id) keep = basketRef.version_id;
        vsel.innerHTML = '<option value="">（请选择已启用冻结版）</option>' + state.versions.map(function (v) {
          var tag = v.enabled ? '启用' : '未启用';
          return '<option value="' + esc(v.version_id) + '">' + (((/(?:^|[-_\s])(smoke|probe|fixture|test)(?:[-_\s]|$)|测试/i.test((v.title||'')+' '+(v.strategy_id||'')))?'[测试版本] ':'')+esc((v.title || v.strategy_id) + ' @' + (v.version || '') + ' · ' + tag)) + '</option>';
        }).join('');
        if (keep) vsel.value = keep;
        renderFrozenNote(findVersion(vsel.value));
      }
      q('#simStatus').textContent = '已加载 ' + state.accounts.length + ' 个账户 · ' +
        (accData.event_count || 0) + ' 条账本事件 · ' + (accData.conservation_ok ? '守恒通过' : '守恒异常');
      render();
    }).catch(function (e) {
      ensurePanel();
      q('#simStatus').textContent = '加载失败: ' + (e.message || e);
    });
  }

  
  function doAutofillPreview() {
    var symbol = ((q('#simSymbol') || {}).value || '').trim().toUpperCase();
    if (!symbol) return toast('请填写合约');
    var versionId = (q('#simVersion') || {}).value || '';
    if (!versionId) return toast('自动填仓必须选择已启用冻结版本');
    var body = {
      symbol: symbol,
      side: (q('#simSide') || {}).value || 'long',
      strategy_version_id: versionId,
    };
    api('/api/sim/autofill/preview', { method: 'POST', body: body }).then(function (plan) {
      toast('预览名义 ' + plan.notional_u + 'U · 需现金 ' + plan.need_cash + 'U · 未写账本'
        + (plan.same_symbol_blocked ? ' · 同币将拒绝' : ''));
      var el = q('#simStatus');
      if (el) el.textContent = JSON.stringify({
        dry_run: plan.dry_run, notional_u: plan.notional_u, fee: plan.fee,
        size_rule: plan.size_rule, block: plan.block_reason || null,
      });
    }).catch(function (err) { toast('预览失败：' + err.message); });
  }
  function doAutofill() {
    var symbol = ((q('#simSymbol') || {}).value || '').trim().toUpperCase();
    if (!symbol) return toast('请填写合约');
    var versionId = (q('#simVersion') || {}).value || '';
    if (!versionId) return toast('自动填仓必须选择已启用冻结版本');
    var ver = findVersion(versionId);
    var hint = ver ? ('将只按冻结参数记账：' + (ver.title || ver.strategy_id) + ' @' + (ver.version || '')) : versionId;
    if (!window.confirm('确认纸面自动填仓？不会向交易所下单。\n' + hint)) return;
    var body = {
      symbol: symbol,
      side: (q('#simSide') || {}).value || 'long',
      strategy_version_id: versionId,
      confirm: true,
    };
    api('/api/sim/autofill', { method: 'POST', body: body }).then(function (out) {
      toast('已填仓 ' + out.event.symbol + ' · 名义 ' + out.event.notional + 'U（研究/模拟）');
      return refresh();
    }).catch(function (err) { toast('填仓失败：' + err.message); });
  }

  function doOpen() {
    var symbol = (q('#simSymbol').value || '').trim().toUpperCase();
    if (!symbol) return toast('请填写合约代码');
    var notional = Number(q('#simNotional').value);
    var lev = Number(q('#simLev').value);
    var side = q('#simSide').value;
    var versionId = q('#simVersion').value || undefined;
    var accountId = q('#simAccount').value || 'acc-shared';
    var body = {
      symbol: symbol,
      side: side,
      notional_u: notional,
      leverage: lev,
      account_id: accountId,
      strategy_version_id: versionId,
    };
    api('/api/sim/events/open', { method: 'POST', body: body }).then(function () {
      toast('模拟开仓已记账');
      return refresh();
    }).catch(function (e) { toast('开仓拒绝: ' + (e.message || e)); });
  }

  function doClose() {
    var symbol = (q('#simSymbol').value || '').trim().toUpperCase();
    var acc = currentAccount();
    if (!symbol && acc && acc.positions && acc.positions[0]) symbol = acc.positions[0].symbol;
    if (!symbol) return toast('请填写要平仓的合约');
    var body = {
      symbol: symbol,
      account_id: (q('#simAccount').value || 'acc-shared'),
    };
    api('/api/sim/events/close', { method: 'POST', body: body }).then(function () {
      toast('模拟平仓已记账');
      return refresh();
    }).catch(function (e) { toast('平仓拒绝: ' + (e.message || e)); });
  }

  function bind() {
    ensurePanel();
    var section = q('[data-view="basket"]');
    if (!section || section.getAttribute('data-sim-bound')) return;
    section.setAttribute('data-sim-bound', '1');
    section.addEventListener('click', function (ev) {
      var btn = ev.target.closest('[data-sim]');
      if (!btn) return;
      var act = btn.getAttribute('data-sim');
      if (act === 'refresh') refresh();
      if (act === 'open') doOpen();
      if (act === 'preview-af') doAutofillPreview();
      if (act === 'autofill') doAutofill();
      if (act === 'close') doClose();
    });
    section.addEventListener('change', function (ev) {
      if (ev.target && ev.target.id === 'simAccount') {
        state.accountId = ev.target.value;
        render();
      }
      if (ev.target && ev.target.id === 'simVersion') {
        renderFrozenNote(findVersion(ev.target.value));
      }
    });
  }

  function boot() {
    if (!q('[data-view="basket"]')) return;
    bind();
    refresh();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
  window.SimStoreUI = { refresh: refresh };
})();
