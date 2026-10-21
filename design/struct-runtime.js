/** STRUCT-01 首刀：ATR/ADX/日线箱体/区间高点（非可信ATH）/弱吸筹证据。 */
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
    var el = q('#structStatus');
    if (el) el.textContent = msg;
  }
  function fmt(n, d) {
    if (n == null || !isFinite(Number(n))) return '—';
    return Number(n).toLocaleString('zh-CN', { maximumFractionDigits: d == null ? 4 : d });
  }
  function statusPill(st) {
    if (st === 'ok') return '<span class="pill">可用</span>';
    if (st === 'insufficient_history') return '<span class="pill amber">预热不足</span>';
    if (st === 'wrong_interval') return '<span class="pill gray">周期不符</span>';
    if (st === 'no_signal') return '<span class="pill gray">无信号</span>';
    if (st === 'non_finite') return '<span class="pill amber">非有限数已拒</span>';
    return '<span class="pill gray">' + esc(st || '未知') + '</span>';
  }

  var state = { catalog: null, list: null, last: null };

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
    var section = q('[data-view="strategies"]') || q('[data-view="market"]');
    if (!section || q('#structRuntimePanel')) return;
    var panel = document.createElement('div');
    panel.className = 'panel';
    panel.id = 'structRuntimePanel';
    panel.innerHTML =
      '<div class="panel-head"><div><h2>结构指标（STRUCT-01 首刀）</h2>' +
      '<small>ATR/ADX · 日线箱体 · <strong>区间高点</strong>（非可信ATH）· 弱吸筹证据 · 预热不足不给伪数</small></div>' +
      '<span class="pill gray">参数冻结 ' + esc('struct-params-v1') + '</span></div>' +
      '<p id="structStatus" class="muted">加载目录…</p>' +
      '<div class="row" style="gap:8px;flex-wrap:wrap;margin:8px 0">' +
      '<label>市场 <input id="structMarket" value="USDⓈ-M" style="width:7rem"></label>' +
      '<label>合约 <input id="structSymbol" placeholder="如 AAVEUSDT" style="width:10rem"></label>' +
      '<label>周期 <select id="structInterval"><option value="1d" selected>1d</option><option value="4h">4h</option></select></label>' +
      '<button type="button" id="structComputeBtn">计算结构指标</button>' +
      '<button type="button" id="structRefreshListBtn" class="ghost">刷新可得列表</button>' +
      '</div>' +
      '<div id="structCatalog" class="muted"></div>' +
      '<div class="tablewrap" style="margin-top:8px"><table><thead><tr>' +
      '<th>指标</th><th>状态</th><th>数值</th><th>周期/预热</th><th>覆盖说明</th></tr></thead>' +
      '<tbody id="structMetricsBody"><tr><td colspan="5" class="muted">选择合约后计算</td></tr></tbody></table></div>' +
      '<div id="structListWrap" style="margin-top:12px"></div>' +
      '<p class="muted" id="structFootnote">BACKTEST-01 未启动。无全历史核验源时只标区间高点。</p>';
    var anchor = q('[data-strategy-view="library"]', section) || section;
    anchor.insertBefore(panel, anchor.firstChild);
    q('#structComputeBtn').addEventListener('click', compute);
    q('#structRefreshListBtn').addEventListener('click', loadList);
  }

  function renderCatalog(cat) {
    state.catalog = cat;
    var el = q('#structCatalog');
    if (!el || !cat) return;
    var formulas = (cat.formulas || []).map(function (f) {
      return esc(f.formula_id) + '（' + esc(f.title) + '）';
    }).join(' · ');
    el.innerHTML =
      '<div>公式：' + formulas + '</div>' +
      '<div>ATH 政策：' + esc((cat.ath_policy && cat.ath_policy.note) || '') + '</div>' +
      '<div>冻结参数：ATR=' + esc(cat.params.atr_period) +
      ' ADX=' + esc(cat.params.adx_period) +
      ' 箱体lookback=' + esc(cat.params.day_box_lookback) + '</div>';
  }

  function metricRow(title, m) {
    if (!m) return '';
    var val = m.value;
    if (m.formula_id === 'DAY_BOX_LOOKBACK_v1' && m.status === 'ok') {
      val = '上' + fmt(m.high) + ' / 下' + fmt(m.low) + ' / 宽' + fmt(m.width_pct, 2) + '%';
    } else if (m.formula_id === 'RANGE_HIGH_WINDOW_v1') {
      val = fmt(m.value) + '（' + esc(m.label || '区间高点') + '）';
    } else if (m.formula_id === 'ABSORPTION_VOL_PRICE_WEAK_v1') {
      val = m.status === 'ok'
        ? ('连续' + fmt(m.trailing_streak, 0) + '根 · ' + esc(m.evidence_strength))
        : ('— · ' + esc(m.evidence_strength || '弱证据'));
    } else if (m.formula_id === 'ADX_WILDER_v1' && m.status === 'ok') {
      val = fmt(m.value, 2) + '（+DI ' + fmt(m.plus_di, 2) + ' / −DI ' + fmt(m.minus_di, 2) + '）';
    } else {
      val = fmt(val, 6);
    }
    var warm = esc(String(m.warmup_used != null ? m.warmup_used : '—')) +
      ' / 需 ' + esc(String(m.warmup_required != null ? m.warmup_required : '—'));
    var note = esc(m.note || m.coverage_note || m.note_extra || '');
    if (m.status === 'insufficient_history') {
      note = '预热不足：' + note;
    }
    return '<tr><td>' + esc(title) + '<div class="muted">' + esc(m.formula_id || '') + '</div></td>' +
      '<td>' + statusPill(m.status) + '</td>' +
      '<td>' + val + '</td>' +
      '<td>' + warm + '</td>' +
      '<td>' + note + '</td></tr>';
  }

  function renderResult(data) {
    state.last = data;
    var body = q('#structMetricsBody');
    if (!body) return;
    var m = data.metrics || {};
    var cov = data.coverage || {};
    body.innerHTML =
      metricRow('ATR', m.atr) +
      metricRow('ADX', m.adx) +
      metricRow('区间高点', m.range_high) +
      metricRow('日线箱体', m.day_box) +
      metricRow('量增价平（弱）', m.absorption);
    var st = q('#structStatus');
    if (st) {
      st.textContent =
        data.symbol + ' · 已知K线 ' + (data.bars_used || 0) +
        ' · 覆盖 ' + fmt(cov.coveragePct, 1) + '%' +
        (cov.continuous === false ? ' · 有缺口' : '') +
        ' · 参数 ' + (data.params_version || '');
    }
  }

  function renderList(data) {
    state.list = data;
    var wrap = q('#structListWrap');
    if (!wrap) return;
    var rows = (data.series || []).slice(0, 15).map(function (r) {
      return '<tr><td><button type="button" class="ghost struct-pick" data-market="' +
        esc(r.market) + '" data-symbol="' + esc(r.symbol) + '">' + esc(r.symbol) +
        '</button></td><td>' + esc(r.approx_bars) + '</td><td>' +
        (r.atr_warmup_ok ? 'ATR✓' : 'ATR预热不足') + ' · ' +
        (r.adx_warmup_ok ? 'ADX✓' : 'ADX预热不足') + ' · ' +
        (r.day_box_warmup_ok ? '箱体✓' : '箱体预热不足') +
        '</td><td>禁止ATH</td></tr>';
    }).join('');
    wrap.innerHTML =
      '<div class="panel-head"><div><h3>可得合约（目录级）</h3>' +
      '<small>近似根数；精确以计算为准</small></div></div>' +
      '<div class="tablewrap"><table><thead><tr><th>合约</th><th>约计K线</th><th>预热提示</th><th>ATH</th></tr></thead>' +
      '<tbody>' + (rows || '<tr><td colspan="4" class="muted">暂无</td></tr>') + '</tbody></table></div>';
    wrap.querySelectorAll('.struct-pick').forEach(function (btn) {
      btn.addEventListener('click', function () {
        q('#structMarket').value = btn.getAttribute('data-market') || 'USDⓈ-M';
        q('#structSymbol').value = btn.getAttribute('data-symbol') || '';
        compute();
      });
    });
  }

  function compute() {
    var market = (q('#structMarket').value || '').trim();
    var symbol = (q('#structSymbol').value || '').trim().toUpperCase();
    var interval = q('#structInterval').value || '1d';
    if (!market || !symbol) {
      toast('请填写市场与合约');
      return;
    }
    q('#structStatus').textContent = '计算中…';
    var url = '/api/struct/compute?market=' + encodeURIComponent(market) +
      '&symbol=' + encodeURIComponent(symbol) +
      '&interval=' + encodeURIComponent(interval);
    api(url).then(function (data) {
      renderResult(data);
      toast('已计算 ' + symbol);
    }).catch(function (err) {
      q('#structStatus').textContent = '失败：' + (err.message || err);
      toast('计算失败');
    });
  }

  function loadList() {
    var interval = (q('#structInterval') && q('#structInterval').value) || '1d';
    return api('/api/struct/list?interval=' + encodeURIComponent(interval) + '&limit=40')
      .then(renderList)
      .catch(function (err) {
        toast('列表失败：' + (err.message || err));
      });
  }

  function boot() {
    ensurePanel();
    api('/api/struct/catalog').then(function (cat) {
      renderCatalog(cat);
      q('#structStatus').textContent = '目录已加载 · ' + (cat.params_version || '');
      return loadList();
    }).catch(function (err) {
      if (q('#structStatus')) q('#structStatus').textContent = '结构接口不可用：' + (err.message || err);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
