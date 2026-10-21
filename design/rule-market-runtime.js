/** RULE-01: market filter read-only consume + report export fingerprint (V2.8). */
(function () {
  'use strict';

  var state = {
    market: null,
    exportGate: null,
    lockedKey: null,
    loadedAt: null,
  };

  function q(sel, root) { return (root || document).querySelector(sel); }

  function toast(msg) {
    var el = q('#ruleMarketToast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'ruleMarketToast';
      el.setAttribute('role', 'status');
      el.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:9999;background:#1b2430;color:#e8eef7;padding:10px 14px;border-radius:8px;max-width:360px;font:13px/1.4 system-ui;box-shadow:0 8px 24px rgba(0,0,0,.35)';
      document.body.appendChild(el);
    }
    el.textContent = String(msg || '');
    el.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { el.hidden = true; }, 4200);
  }

  function api(path, method, body) {
    var opt = { method: method || 'GET', headers: { 'Accept': 'application/json' }, cache: 'no-store' };
    if (body !== undefined) {
      opt.headers['Content-Type'] = 'application/json';
      opt.body = JSON.stringify(body);
    }
    return fetch(path, opt).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok) {
          var err = new Error((j && (j.error || j.message)) || ('HTTP ' + r.status));
          err.status = r.status;
          err.body = j;
          throw err;
        }
        return j;
      });
    });
  }

  function shortHash(h) { return String(h || '').slice(0, 12); }

  function escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }

  function bannerHtml(m) {
    if (!m) return '<p class="muted">策略指纹加载中…</p>';
    if (!m.bound || !m.filter) {
      return '<p class="muted"><strong>策略指纹未绑定</strong> — ' + escapeHtml(m.note || '市场页尚未绑定冻结版本') +
        '。筛选仅作观察层；导出/保存报告将被拒绝。</p>';
    }
    var f = m.filter;
    return '<div class="rule-fp-lock">' +
      '<strong>策略指纹只读（强制）</strong> ' +
      '<span class="pill">' + escapeHtml(f.strategy_id) + ' @' + escapeHtml(String(f.version)) + '</span> ' +
      '<code title="' + escapeHtml(f.content_hash) + '">' + escapeHtml(shortHash(f.content_hash)) + '…</code>' +
      '<div class="muted" style="margin-top:4px">unified_key 已锁定；观察层阈值可调，但不得假装另一策略。禁止手改 hash。</div>' +
      '<div class="muted" style="font-size:12px;word-break:break-all">key: ' + escapeHtml(f.unified_key) + '</div>' +
      '</div>';
  }

  function ensureBanner() {
    var host = q('#ruleMarketFilterBanner');
    if (host) return host;
    host = document.createElement('div');
    host.id = 'ruleMarketFilterBanner';
    host.className = 'notice';
    host.style.cssText = 'margin:10px 0;padding:10px 12px;border:1px solid var(--line,#334);border-radius:8px;background:rgba(80,120,180,.08)';
    var anchor =
      q('#market .header') ||
      q('#market') ||
      q('[data-view="market"]') ||
      q('header .controls') ||
      q('main') ||
      document.body;
    if (anchor && anchor.parentNode && anchor.classList && anchor.classList.contains('header')) {
      anchor.parentNode.insertBefore(host, anchor.nextSibling);
    } else if (anchor && anchor.firstChild) {
      anchor.insertBefore(host, anchor.firstChild);
    } else {
      document.body.insertBefore(host, document.body.firstChild);
    }
    return host;
  }

  function renderBanner() {
    ensureBanner().innerHTML = bannerHtml(state.market);
  }

  function lockStrategyPick() {
    var m = state.market;
    if (!m || !m.bound || !m.filter) {
      state.lockedKey = null;
      window.RuleMarketFilter = {
        bound: false,
        readOnly: true,
        filter: null,
        refresh: refreshMarket,
        exportGate: fetchExportGate,
        assertNoDrift: assertNoDrift,
        exportMarkdown: exportMarkdown,
        exportCsv: exportCsv,
      };
      return;
    }
    var key = m.filter.unified_key;
    if (state.lockedKey && state.lockedKey !== key) {
      toast('绑定指纹已变更，已重新锁定到新版本');
    }
    state.lockedKey = key;
    // Freeze visible strategy identity fields if present
    var pick = q('#ruleStrategyPick');
    if (!pick) {
      pick = document.createElement('input');
      pick.type = 'text';
      pick.id = 'ruleStrategyPick';
      pick.readOnly = true;
      pick.setAttribute('aria-readonly', 'true');
      pick.style.cssText = 'width:min(100%,520px);margin-top:6px';
      var banner = q('#ruleMarketFilterBanner');
      if (banner) banner.appendChild(pick);
    }
    pick.value = m.filter.strategy_id + ' @' + m.filter.version + ' · ' + shortHash(m.filter.content_hash);
    pick.readOnly = true;
    window.RuleMarketFilter = {
      bound: true,
      readOnly: true,
      filter: m.filter,
      lockedKey: key,
      refresh: refreshMarket,
      exportGate: fetchExportGate,
      assertNoDrift: assertNoDrift,
      exportMarkdown: exportMarkdown,
      exportCsv: exportCsv,
    };
  }

  function assertNoDrift() {
    var m = state.market;
    if (!m || !m.bound || !m.filter) return { ok: false, reason: '未绑定' };
    if (state.lockedKey && state.lockedKey !== m.filter.unified_key) {
      return { ok: false, reason: '指纹漂移：页面锁定与服务器绑定不一致' };
    }
    return { ok: true, filter: m.filter };
  }

  function refreshMarket() {
    return api('/api/strategies/bindings/market/filter').then(function (m) {
      state.market = m;
      state.loadedAt = Date.now();
      renderBanner();
      lockStrategyPick();
      return m;
    }).catch(function (err) {
      state.market = { bound: false, read_only: true, note: '指纹接口不可用：' + (err.message || err) };
      renderBanner();
      lockStrategyPick();
      return state.market;
    });
  }

  function fetchExportGate() {
    return api('/api/strategies/bindings/export-fingerprint').then(function (g) {
      state.exportGate = g;
      return g;
    });
  }

  function downloadBlob(text, name, type) {
    var a = document.createElement('a');
    var url = URL.createObjectURL(new Blob([text], { type: type }));
    a.href = url;
    a.download = name;
    a.click();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  function exportMarkdown() {
    return fetchExportGate().then(function (g) {
      if (!g.bound) throw new Error(g.note || '未绑定冻结版本，禁止下载报告');
      var drift = assertNoDrift();
      if (state.market && state.market.bound && !drift.ok) throw new Error(drift.reason);
      var ta = q('#reportText');
      var text = (ta && ta.value) || '';
      if (!text || text.indexOf('选择合约或当前筛选') === 0) throw new Error('没有可下载的报告正文');
      if (text.indexOf('strategy_fingerprint') < 0) text = (g.header_md || '') + '\n' + text;
      downloadBlob(text, 'perpetual-report.md', 'text/markdown;charset=utf-8');
      toast('已写入策略指纹后下载');
      return true;
    });
  }

  function exportCsv() {
    return fetchExportGate().then(function (g) {
      if (!g.bound) throw new Error(g.note || '未绑定冻结版本，禁止导出 CSV');
      var drift = assertNoDrift();
      if (state.market && state.market.bound && !drift.ok) throw new Error(drift.reason);
      var body = '';
      if (typeof window.__ruleExportCsv === 'function') {
        body = window.__ruleExportCsv() || '';
      } else {
        throw new Error('页面尚未提供筛选导出钩子');
      }
      var header = g.header_csv || '';
      downloadBlob(header + '\n' + body, 'perpetual-full-evidence.csv', 'text/csv;charset=utf-8');
      toast('CSV 已带策略指纹');
      return true;
    });
  }

  function wrapMarketExports() {
    var dl = q('#downloadReport');
    var ex = q('#export');
    if (dl && !dl.getAttribute('data-rule-fp')) {
      dl.setAttribute('data-rule-fp', '1');
      dl.addEventListener('click', function (ev) {
        ev.preventDefault();
        ev.stopImmediatePropagation();
        exportMarkdown().catch(function (err) {
          toast((err.body && err.body.error) || err.message || '导出硬闸拒绝');
        });
      }, true);
    }
    if (ex && !ex.getAttribute('data-rule-fp')) {
      ex.setAttribute('data-rule-fp', '1');
      ex.addEventListener('click', function (ev) {
        ev.preventDefault();
        ev.stopImmediatePropagation();
        exportCsv().catch(function (err) {
          toast((err.body && err.body.error) || err.message || '导出硬闸拒绝');
        });
      }, true);
    }
  }

  function patchReportRuntime() {
    var rr = window.ReportRuntime;
    if (!rr || rr.__ruleFpPatched) return;
    var orig = rr.saveLocalFromDom;
    if (typeof orig !== 'function') return;
    rr.saveLocalFromDom = async function (meta) {
      try {
        await fetchExportGate();
      } catch (e) {
        toast((e.body && e.body.error) || e.message || '导出硬闸拒绝');
        throw e;
      }
      if (state.exportGate && !state.exportGate.bound) {
        var msg = state.exportGate.note || '未绑定冻结版本，禁止保存报告';
        toast(msg);
        throw new Error(msg);
      }
      var merged = Object.assign({}, meta || {});
      if (state.exportGate && state.exportGate.fingerprint) {
        merged.strategy_fingerprint = state.exportGate.fingerprint;
        merged.unified_key = state.exportGate.fingerprint.unified_key;
      }
      return orig.call(rr, merged);
    };
    rr.__ruleFpPatched = true;
  }

  function boot() {
    ensureBanner();
    refreshMarket().then(function () {
      wrapMarketExports();
      patchReportRuntime();
    });
    setTimeout(patchReportRuntime, 800);
    setTimeout(wrapMarketExports, 500);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();

  window.RuleMarketRuntime = {
    refresh: refreshMarket,
    state: function () { return state; },
    fetchExportGate: fetchExportGate,
    exportMarkdown: exportMarkdown,
    exportCsv: exportCsv,
  };
})();
