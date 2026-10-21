/** BACKTEST-01 首刀：单策略版本×单合约×本地合并K线历史回测（研究/模拟，非实盘）。 */
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
    var el = q('#btStatus');
    if (el) el.textContent = msg;
  }
  function fmt(n, d) {
    if (n == null || !isFinite(Number(n))) return '—';
    return Number(n).toLocaleString('zh-CN', { maximumFractionDigits: d == null ? 4 : d });
  }
  function versionParams(v) {
    var rules = (v && v.rules) || {};
    var params = rules.params;
    return (params && typeof params === 'object') ? params : {};
  }
  function findVersion(id) {
    if (!id) return null;
    var list = state.versions || [];
    for (var i = 0; i < list.length; i++) {
      if (list[i].version_id === id) return list[i];
    }
    return null;
  }
  function executionModeFromParams(p) {
    if (!p) return 'pct_touch';
    var m = p.execution_mode || p.exit_mode || p.mode;
    if (!m) return 'pct_touch';
    m = String(m).toLowerCase();
    if (m === 'ema99' || m === 'ema99_trail' || m === 'ema99_close' || m === 'close_trail' || m === 'ema99_close_trail') return 'ema99_close_trail';
    return m;
  }
  function renderFrozenNote() {
    var el = q('#btFrozenNote');
    var fields = q('#btLegacyFields');
    var scopeBox = q('#btScopeBox');
    var ver = findVersion(q('#btVersion') && q('#btVersion').value);
    if (!el) return;
    if (!ver) {
      el.textContent = '请选择冻结版本；回测参数只读冻结 rules.params，页面不发明 5%/3%/42 或 close_above_prev_high。';
      if (fields) fields.hidden = true;
      if (scopeBox) scopeBox.hidden = true;
      return;
    }
    var p = versionParams(ver);
    var mode = executionModeFromParams(p);
    var bits = [
      (ver.title || ver.strategy_id || '') + ' @' + (ver.version || ''),
      'version_id=' + ver.version_id,
      '指纹 ' + String(ver.content_hash || '').slice(0, 10),
      '执行模式 ' + mode,
    ];
    if (p.entry_rule) bits.push('入场 ' + p.entry_rule);
    if (p.side) bits.push('方向 ' + p.side);
    if (mode === 'ema99_close_trail') {
      bits.push('初始止损 ' + (p.initial_stop || 'cross_bar_ema99'));
      bits.push('追踪 ' + (p.trail_activation || 'first_close_above_entry'));
      bits.push('不提交旧突破/5%/3%/42');
      if (fields) fields.hidden = true;
      if (scopeBox) scopeBox.hidden = false;
    } else {
      if (p.tp_pct != null) bits.push('止盈 ' + p.tp_pct);
      if (p.sl_pct != null) bits.push('止损 ' + p.sl_pct);
      if (p.max_hold_bars != null) bits.push('持仓 ' + p.max_hold_bars + '根');
      else if (p.max_hold_days != null) bits.push('持仓 ' + p.max_hold_days + '天');
      if (fields) fields.hidden = true; // 亦不手填覆盖；只展示冻结
      if (scopeBox) scopeBox.hidden = true;
    }
    var rules = ver.rules || {};
    if (rules.entry_text) bits.push('entry_text=' + String(rules.entry_text).slice(0, 40));
    if (rules.exit_text) bits.push('exit_text=' + String(rules.exit_text).slice(0, 40));
    el.textContent = bits.join(' · ') + ' · 请求体不覆盖冻结值';
  }
  function buildRunBodyFromFrozen(svid, extra) {
    var ver = findVersion(svid);
    var p = versionParams(ver);
    var mode = executionModeFromParams(p);
    var body = {
      strategy_version_id: svid,
      market: ((q('#btMarket') || {}).value || 'USDⓈ-M').trim(),
      interval: (q('#btInterval') || {}).value || '4h',
      split: !!(q('#btSplit') || {}).checked,
      start: localToIso((q('#btStart') || {}).value),
      end: localToIso((q('#btEnd') || {}).value),
      // 明确不发明：不传 allow_generic_price_rule / 不塞默认 entry/tp/sl/hold
    };
    if (extra) {
      Object.keys(extra).forEach(function (k) { body[k] = extra[k]; });
    }
    if (mode === 'ema99_close_trail') {
      body.execution_mode = 'ema99_close_trail';
      var scopeEl = q('#btScope');
      var scope = (scopeEl && scopeEl.value) || 'full_strategy';
      body.backtest_scope = scope;
      if (scope === 'ema_tech_slice') {
        body.experiment_kind = 'ema_tech_slice';
      }
      // 不传 entry_rule/tp_pct/sl_pct/max_hold_bars
    } else {
      // pct_touch：只声明与冻结一致的字段（若冻结有）；绝不发明 5/3/42
      if (p.entry_rule) body.entry_rule = p.entry_rule;
      if (p.tp_pct != null) body.tp_pct = Number(p.tp_pct);
      if (p.sl_pct != null) body.sl_pct = Number(p.sl_pct);
      if (p.max_hold_bars != null) body.max_hold_bars = Number(p.max_hold_bars);
    }
    if ((q('#btStructFilter') || {}).checked) {
      body.struct_filter = { mode: 'required', require: ['atr', 'adx'] };
    }
    return body;
  }

  var state = { catalog: null, versions: [], runs: [], compares: [], last: null, lastCompare: null };

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
    var section = q('[data-view="validation"]') || q('[data-view="strategies"]');
    if (!section || q('#backtestRuntimePanel')) return;
    var panel = document.createElement('div');
    panel.className = 'panel';
    panel.id = 'backtestRuntimePanel';
    panel.innerHTML =
      '<div class="panel-head"><div><h2>历史回测（BACKTEST-01 对照刀）</h2>' +
      '<small>单跑或公平对照批次 · 仅本地合并K线 · 绝对时间训/验/测 · EVAL歧义 · SIM费用 · 不重建历史币池</small></div>' +
      '<span class="pill gray">非宇宙重建</span></div>' +
      '<p id="btStatus" class="muted">加载中…</p>' +
      '<div id="btWarnings" class="notice warning" style="margin:8px 0"></div>' +
      '<div class="row" style="gap:8px;flex-wrap:wrap;margin:8px 0">' +
      '<label>单跑版本 <select id="btVersion"></select></label>' +
      '<label>对照版本(多选) <select id="btVersions" multiple size="3" style="min-width:12rem"></select></label>' +
      '<label>市场 <input id="btMarket" value="USDⓈ-M" style="width:7rem"></label>' +
      '<label>合约(单跑) <input id="btSymbol" placeholder="如 BTCUSDT" style="width:9rem"></label>' +
      '<label>对照合约(逗号) <input id="btSymbols" placeholder="BTCUSDT,ETHUSDT" style="width:14rem" title="多合约公平对照"></label>' +
      '<label>周期 <select id="btInterval"><option value="4h">4h</option><option value="1d">1d</option></select></label>' +
      '</div>' +
      '<p id="btFrozenNote" class="muted" style="margin:8px 0">回测只服从冻结版本 params；不发明 close_above_prev_high / 5% / 3% / 42。</p>' +
      '<div id="btScopeBox" hidden class="row" style="gap:8px;flex-wrap:wrap;margin:4px 0">' +
      '<label>回测范围 <select id="btScope">' +
      '<option value="full_strategy">完整策略（需历史OI/市值/账户证据）</option>' +
      '<option value="ema_tech_slice">EMA技术切片（单独命名；非完整策略收益验收）</option>' +
      '</select></label>' +
      '<span class="muted">缺证据时完整策略将不足/拒绝；切片不得当作全策略 PnL</span></div>' +
      '<div id="btLegacyFields" hidden class="row" style="gap:8px;flex-wrap:wrap">' +
      '<span class="muted">旧手填入场/止盈止损已禁用（防覆盖冻结）</span>' +
      '<label>开始 <input id="btStart" type="datetime-local" style="width:11rem"></label>' +
      '<label>结束 <input id="btEnd" type="datetime-local" style="width:11rem"></label>' +
      '<label class="check"><input id="btSplit" type="checkbox" checked> 训/验/测切分</label>' +
      '<label class="check"><input id="btStructFilter" type="checkbox"> STRUCT过滤(ATR+ADX必过)</label>' +
      '</div>' +
      '<div class="command-bar" style="margin-top:12px">' +
      '<span class="muted" id="btCatalogNote"></span>' +
      '<div class="row">' +
      '<button type="button" data-bt="refresh">刷新列表</button>' +
      '<button type="button" class="primary" data-bt="run">启动回测</button>' +
      '<button type="button" data-bt="compare">公平对照批次</button>' +
      '</div></div>' +
      '<div class="stats" id="btHeadline" style="margin-top:12px"></div>' +
      '<div class="tablewrap" style="margin-top:12px"><table><thead><tr>' +
      '<th>时间</th><th>合约</th><th>状态</th><th>笔数</th><th>胜率</th><th>净盈亏</th><th>操作</th></tr></thead>' +
      '<tbody id="btRunsBody"><tr><td colspan="7" class="muted">暂无回测记录</td></tr></tbody></table></div>' +
      '<pre id="btDetail" class="muted" style="white-space:pre-wrap;max-height:240px;overflow:auto;margin-top:8px"></pre>' +
      '<div style="margin-top:16px"><h3>公平对照批次</h3>' +
      '<div class="tablewrap"><table><thead><tr>' +
      '<th>时间</th><th>合约数</th><th>版本数</th><th>完成/不足/拒绝</th><th>窗口</th><th>操作</th></tr></thead>' +
      '<tbody id="btCompareBody"><tr><td colspan="6" class="muted">暂无对照批次</td></tr></tbody></table></div>' +
      '<div class="tablewrap" style="margin-top:8px"><table id="btCompareMatrix"><thead id="btCompareMatrixHead"></thead>' +
      '<tbody id="btCompareMatrixBody"></tbody></table></div></div>' +
      '<p class="muted">诚实边界：生存者偏差未消；仅本地可得K线；不回填当今市值；不重建历史上市宇宙；对照缺口格显示 insufficient/拒绝；EMA完整策略缺历史OI/市值/账户证据→明确不足；技术切片单独命名。</p>';

    var firstPanel = section.querySelector('.panel');
    if (firstPanel) section.insertBefore(panel, firstPanel.nextSibling);
    else section.appendChild(panel);

    panel.addEventListener('click', function (ev) {
      var btn = ev.target.closest('[data-bt]');
      if (!btn) return;
      var act = btn.getAttribute('data-bt');
      if (act === 'refresh') refresh();
      if (act === 'run') startRun();
      if (act === 'compare') startCompare();
      if (act === 'open') openRun(btn.getAttribute('data-id'));
      if (act === 'open-compare') {
        api('/api/backtest/compares/' + encodeURIComponent(btn.getAttribute('data-cid')))
          .then(function (rec) { state.lastCompare = rec; renderCompareMatrix(rec); })
          .catch(function (err) { toast('打开对照失败：' + err.message); });
      }
    });
    panel.addEventListener('change', function (ev) {
      if (ev.target && (ev.target.id === 'btVersion' || ev.target.id === 'btScope')) renderFrozenNote();
    });
  }

  function localToIso(v) {
    if (!v) return null;
    var d = new Date(v);
    if (isNaN(d.getTime())) return null;
    return d.toISOString();
  }

  function renderWarnings(cat) {
    var box = q('#btWarnings');
    if (!box) return;
    var warns = (cat && cat.coverage_warnings) || [];
    if (!warns.length) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    box.innerHTML = warns.map(function (w) {
      return '<div><strong>' + esc(w.title) + '</strong>：' + esc(w.note) + '</div>';
    }).join('');
  }

  function renderVersions() {
    var sel = q('#btVersion');
    var multi = q('#btVersions');
    var opts = '';
    if (!state.versions.length) {
      opts = '<option value="">（无冻结版本；请先在策略页冻结）</option>';
      if (sel) sel.innerHTML = opts;
      if (multi) multi.innerHTML = opts;
      return;
    }
    opts = state.versions.map(function (v) {
      var label = (v.title || v.strategy_id || '') + ' · ' + v.version_id + (v.enabled ? '（启用）' : '');
      return '<option value="' + esc(v.version_id) + '">' + (((/(?:^|[-_\s])(smoke|probe|fixture|test)(?:[-_\s]|$)|测试/i.test((v.title||'')+' '+(v.strategy_id||'')))?'[测试版本] ':'')+esc(label)) + '</option>';
    }).join('');
    if (sel) {
      var keep = sel.value;
      sel.innerHTML = opts;
      if (keep) sel.value = keep;
    }
    if (multi) multi.innerHTML = opts;
  }

  function renderRuns() {
    var body = q('#btRunsBody');
    if (!body) return;
    if (!state.runs.length) {
      body.innerHTML = '<tr><td colspan="7" class="muted">暂无回测记录</td></tr>';
      return;
    }
    body.innerHTML = state.runs.map(function (r) {
      return '<tr>' +
        '<td>' + esc((r.created_at || '').replace('T', ' ').slice(0, 19)) + '</td>' +
        '<td>' + esc(r.symbol) + '</td>' +
        '<td>' + esc(r.status) + '</td>' +
        '<td>' + fmt(r.trades, 0) + '</td>' +
        '<td>' + esc(r.win_rate_label || '无样本') + '</td>' +
        '<td>' + fmt(r.realized_pnl_net, 4) + '</td>' +
        '<td><button type="button" data-bt="open" data-id="' + esc(r.run_id) + '">详情</button></td>' +
        '</tr>';
    }).join('');
  }

  function renderCompares() {
    var body = q('#btCompareBody');
    if (!body) return;
    var rows = state.compares || [];
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="6" class="muted">暂无对照批次</td></tr>';
      return;
    }
    body.innerHTML = rows.map(function (c) {
      return '<tr><td>' + esc((c.created_at || '').replace('T', ' ').slice(0, 19)) +
        '</td><td>' + esc((c.symbols || []).length) +
        '</td><td>' + esc((c.strategy_version_ids || []).length) +
        '</td><td>' + esc(c.completed || 0) + '/' + esc(c.insufficient || 0) + '/' + esc(c.rejected || 0) +
        '</td><td>' + esc(c.window_mode || '—') +
        '</td><td><button type="button" data-bt="open-compare" data-cid="' + esc(c.compare_id) + '">矩阵</button></td></tr>';
    }).join('');
  }

  function renderCompareMatrix(rec) {
    var head = q('#btCompareMatrixHead');
    var body = q('#btCompareMatrixBody');
    if (!head || !body || !rec || !rec.table) return;
    var syms = rec.table.symbols || [];
    var vers = rec.table.versions || [];
    var grid = rec.table.grid || {};
    head.innerHTML = '<tr><th>版本 \\ 合约</th>' + syms.map(function (s) {
      return '<th>' + esc(s) + '</th>';
    }).join('') + '</tr>';
    body.innerHTML = vers.map(function (v) {
      var cells = syms.map(function (s) {
        var cell = grid[v + '|' + s] || {};
        var st = cell.status || '—';
        var wr = cell.win_rate_label || '无样本';
        var excl = cell.exclusions || {};
        var tip = st + ' · 笔数 ' + (cell.trades == null ? '—' : cell.trades) +
          ' · 胜率 ' + wr +
          (excl.insufficient ? ' · insufficient' : '') +
          (cell.reject_reason ? (' · ' + String(cell.reject_reason).slice(0, 40)) : '');
        return '<td title="' + esc(tip) + '"><div><code>' + esc(st) + '</code></div>' +
          '<div class="muted">' + esc(wr) + '</div>' +
          '<div class="muted">净 ' + (cell.realized_pnl_net == null ? '—' : fmt(cell.realized_pnl_net, 2)) + '</div></td>';
      }).join('');
      return '<tr><th><code>' + esc(v) + '</code></th>' + cells + '</tr>';
    }).join('');
    var gaps = (rec.coverage_gaps || []).map(function (g) { return g.title; }).join('；');
    q('#btDetail').textContent = JSON.stringify({
      compare_id: rec.compare_id,
      summary: rec.summary,
      window_mode: rec.window_mode,
      absolute_cuts: rec.absolute_cuts,
      coverage_gaps: gaps,
      symbol_ranges: rec.symbol_ranges,
    }, null, 2);
  }

  function renderHeadline(rec) {
    var box = q('#btHeadline');
    if (!box) return;
    if (!rec) {
      box.innerHTML = '';
      return;
    }
    var m = rec.headline_metrics || rec.metrics || {};
    var wr = m.sample_count > 0 ? (fmt((m.win_rate || 0) * 100, 2) + '%') : '无样本';
    box.innerHTML =
      '<div class="stat"><label>状态</label><strong>' + esc(rec.status) + '</strong><small>' + esc(rec.status_note || '') + '</small></div>' +
      '<div class="stat"><label>K线根数</label><strong>' + fmt((rec.data_range || {}).bars_count, 0) + '</strong><small>冲突排除 ' + fmt(((rec.exclusions || {}).conflicted_bars), 0) + '</small></div>' +
      '<div class="stat"><label>成交笔数</label><strong>' + fmt(m.trades, 0) + '</strong><small>断档 ' + fmt(m.data_gap_count, 0) + '</small></div>' +
      '<div class="stat"><label>胜率</label><strong>' + esc(wr) + '</strong><small>样本 ' + fmt(m.sample_count, 0) + '</small></div>' +
      '<div class="stat"><label>净盈亏(U)</label><strong>' + fmt(m.realized_pnl_net, 4) + '</strong><small>费用 ' + fmt(m.fees_paid, 4) + '</small></div>';
  }

  function openRun(id) {
    if (!id) return;
    api('/api/backtest/runs/' + encodeURIComponent(id)).then(function (rec) {
      state.last = rec;
      renderHeadline(rec);
      var detail = q('#btDetail');
      if (detail) {
        detail.textContent = JSON.stringify({
          run_id: rec.run_id,
          status: rec.status,
          status_note: rec.status_note,
          backtest_scope: rec.backtest_scope,
          execution_mode: rec.execution_mode,
          frozen_exec: rec.frozen_exec,
          not_full_strategy_pnl: rec.not_full_strategy_pnl,
          params_hash: rec.params_hash,
          exclusions: rec.exclusions,
          segments: Object.keys(rec.segments || {}),
          coverage_warnings: (rec.coverage_warnings || []).map(function (w) { return w.title; }),
          disclaimer: rec.disclaimer,
        }, null, 2);
      }
      toast('已打开 ' + id);
    }).catch(function (err) {
      toast('打开失败：' + err.message);
    });
  }

  function startRun() {
    var svid = (q('#btVersion') || {}).value;
    var symbol = ((q('#btSymbol') || {}).value || '').trim().toUpperCase();
    if (!svid) return toast('请先选择冻结策略版本');
    if (!symbol) return toast('请填写合约');
    var ver = findVersion(svid);
    var mode = executionModeFromParams(versionParams(ver));
    var body = buildRunBodyFromFrozen(svid, { symbol: symbol });
    toast('回测启动中 · ' + mode + ' · 只服从冻结…');
    api('/api/backtest/run', { method: 'POST', body: body }).then(function (rec) {
      state.last = rec;
      renderHeadline(rec);
      var detail = q('#btDetail');
      if (detail) {
        detail.textContent = JSON.stringify({
          run_id: rec.run_id,
          status: rec.status,
          status_note: rec.status_note,
          backtest_scope: rec.backtest_scope || body.backtest_scope,
          execution_mode: rec.execution_mode || mode,
          frozen_exec: rec.frozen_exec || null,
          params_hash: rec.params_hash,
          disclaimer: rec.disclaimer,
          not_full_strategy_pnl: rec.not_full_strategy_pnl || body.backtest_scope === 'ema_tech_slice',
        }, null, 2);
      }
      var note = rec.status_note || '';
      if (rec.status === 'insufficient' || rec.status === 'rejected' || rec.status === 'unimplemented') {
        toast((rec.status || '不足') + '：' + note);
      } else {
        toast('完成：笔数 ' + ((rec.metrics || {}).trades || 0) + '，胜率 ' + ((rec.metrics || {}).win_rate_label || '无样本'));
      }
      return refreshRuns();
    }).catch(function (err) {
      toast('回测失败：' + err.message);
    });
  }

  function selectedVersions() {
    var multi = q('#btVersions');
    if (multi && multi.selectedOptions && multi.selectedOptions.length) {
      return Array.prototype.map.call(multi.selectedOptions, function (o) { return o.value; }).filter(Boolean);
    }
    var one = (q('#btVersion') || {}).value;
    return one ? [one] : [];
  }

  function startCompare() {
    var vers = selectedVersions();
    var raw = ((q('#btSymbols') || {}).value || '').trim();
    var symbols = raw ? raw.split(/[,\s]+/).map(function (s) { return s.trim().toUpperCase(); }).filter(Boolean) : [];
    if (!symbols.length) {
      var one = ((q('#btSymbol') || {}).value || '').trim().toUpperCase();
      if (one) symbols = [one];
    }
    if (!vers.length) return toast('请选择至少一个冻结版本（可多选对照版本）');
    if (!symbols.length) return toast('请填写对照合约（逗号分隔）或单跑合约');
    // 对照批次：共享窗口/费用；每格执行参数由各冻结版本决定，禁止统一塞旧 5/3/42
    var body = {
      strategy_version_ids: vers,
      symbols: symbols,
      market: ((q('#btMarket') || {}).value || 'USDⓈ-M').trim(),
      interval: (q('#btInterval') || {}).value || '4h',
      split: !!(q('#btSplit') || {}).checked,
      start: localToIso((q('#btStart') || {}).value),
      end: localToIso((q('#btEnd') || {}).value),
    };
    var scopeEl = q('#btScope');
    if (scopeEl && scopeEl.value) {
      body.backtest_scope = scopeEl.value;
      if (scopeEl.value === 'ema_tech_slice') body.experiment_kind = 'ema_tech_slice';
    }
    if ((q('#btStructFilter') || {}).checked) {
      body.struct_filter = { mode: 'required', require: ['atr', 'adx'] };
    }
    toast('公平对照批次启动中（按各版本冻结模式，不覆盖）…');
    api('/api/backtest/compare', { method: 'POST', body: body }).then(function (rec) {
      state.lastCompare = rec;
      renderCompareMatrix(rec);
      var s = rec.summary || {};
      toast('对照完成：完成 ' + (s.completed || 0) + ' / 不足 ' + (s.insufficient || 0) + ' / 拒绝 ' + (s.rejected || 0));
      return refreshCompares();
    }).catch(function (err) {
      toast('对照失败：' + err.message);
    });
  }

  function refreshRuns() {
    return api('/api/backtest/runs?limit=30').then(function (data) {
      state.runs = data.runs || [];
      renderRuns();
      if (!state.catalog) renderWarnings(data);
      return data;
    });
  }

  function refreshCompares() {
    return api('/api/backtest/compares?limit=20').then(function (data) {
      state.compares = data.compares || [];
      renderCompares();
      return data;
    }).catch(function () {
      state.compares = [];
      renderCompares();
    });
  }

  function refresh() {
    ensurePanel();
    Promise.all([
      api('/api/backtest/catalog'),
      api('/api/strategies').catch(function () { return { versions: [] }; }),
      refreshRuns(),
      refreshCompares(),
    ]).then(function (all) {
      state.catalog = all[0];
      renderWarnings(all[0]);
      var note = q('#btCatalogNote');
      if (note) {
        var fc = (all[0].fair_compare || {});
        note.textContent = '歧义=' + ((all[0].ambiguity_rules || {}).same_bar_tp_sl || '') +
          ' · 对照=' + (fc.ready ? '就绪' : '未接') +
          ' · 结构钩子未接 · ' + ((all[0].disclaimer || '').slice(0, 36));
      }
      var vers = all[1].versions || all[1].items || all[1] || [];
      if (!Array.isArray(vers)) vers = [];
      // Prefer enabled, but keep all frozen
      state.versions = vers.slice().sort(function (a, b) {
        return (b.enabled ? 1 : 0) - (a.enabled ? 1 : 0);
      });
      renderVersions();
      renderFrozenNote();
      toast('回测面板已刷新');
    }).catch(function (err) {
      toast('加载失败：' + err.message);
    });
  }

  function boot() {
    ensurePanel();
    refresh();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
