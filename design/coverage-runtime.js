/** DATA-01 加深：覆盖缺口 / 补查提示 / 指纹；快照 expected vs received。 */
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
    var el = q('#covStatus');
    if (el) el.textContent = msg;
  }
  function api(path) {
    return fetch(path, { headers: { Accept: 'application/json' }, credentials: 'same-origin' })
      .then(function (res) {
        return res.json().then(function (data) {
          if (!res.ok) throw new Error((data && data.error) || ('HTTP ' + res.status));
          return data;
        });
      });
  }
  function statusPill(st) {
    if (st === 'covered') return '<span class="pill">covered</span>';
    if (st === 'partial') return '<span class="pill amber">partial</span>';
    return '<span class="pill gray">unknown</span>';
  }

  function ensurePanel() {
    var section = q('[data-view="tasks"]');
    if (!section || q('#coverageGatePanel')) return;
    var panel = document.createElement('div');
    panel.className = 'panel';
    panel.id = 'coverageGatePanel';
    panel.innerHTML =
      '<div class="panel-head"><div><h2>多字段覆盖闸门（DATA-01 加深）</h2>' +
      '<small>expected / received / known · 缺口合约样本 · 补查提示 · 覆盖指纹 · 不重建宇宙</small></div>' +
      '<span class="pill gray">硬闸</span></div>' +
      '<p id="covStatus" class="muted">加载中…</p>' +
      '<div class="stats" id="covAgg"></div>' +
      '<p class="muted" id="covFp"></p>' +
      '<div class="command-bar"><button type="button" id="covRefresh">刷新覆盖</button></div>' +
      '<div class="tablewrap" style="margin-top:8px"><table><thead><tr>' +
      '<th>字段</th><th>状态</th><th>expected</th><th>received</th><th>known</th><th>missing</th><th>覆盖%</th><th>说明</th>' +
      '</tr></thead><tbody id="covBody"></tbody></table></div>' +
      '<h3 style="margin-top:12px">缺口与补查提示</h3>' +
      '<div class="tablewrap"><table><thead><tr><th>字段</th><th>状态</th><th>缺口样本</th><th>补查提示</th></tr></thead>' +
      '<tbody id="covGaps"></tbody></table></div>' +
      '<h3 style="margin-top:12px">快照浏览（expected vs received）</h3>' +
      '<div class="tablewrap"><table><thead><tr><th>scan/rev</th><th>expected</th><th>received</th><th>ratio</th><th>fingerprint</th><th>状态</th></tr></thead>' +
      '<tbody id="covSnaps"></tbody></table></div>' +
      '<p class="muted" id="covClaims"></p>';
    (document.querySelector('[data-task-view="coverage"]')||section).appendChild(panel);
    q('#covRefresh').addEventListener('click', refresh);
  }

  function render(rep, snaps) {
    var agg = rep.aggregate || {};
    var box = q('#covAgg');
    if (box) {
      box.innerHTML =
        '<div class="stat"><label>聚合</label><strong>' + esc(agg.status || '—') + '</strong><small>' + esc(agg.note || '') + '</small></div>' +
        '<div class="stat"><label>covered</label><strong>' + esc(agg.covered_fields) + '</strong></div>' +
        '<div class="stat"><label>partial</label><strong>' + esc(agg.partial_fields) + '</strong></div>' +
        '<div class="stat"><label>unknown</label><strong>' + esc(agg.unknown_fields) + '</strong></div>' +
        '<div class="stat"><label>gaps</label><strong>' + esc(rep.gap_count != null ? rep.gap_count : '—') + '</strong></div>';
    }
    var fp = q('#covFp');
    if (fp) {
      var f = rep.fingerprint || {};
      fp.textContent = 'coverage_fingerprint=' + String(f.hex || '').slice(0, 16) + (f.hex ? '…' : '—') +
        ' · aggregate=' + (f.aggregate_status || agg.status || '—');
    }
    var body = q('#covBody');
    if (body) {
      body.innerHTML = (rep.fields || []).map(function (f) {
        return '<tr><td>' + esc(f.title || f.id) + '</td><td>' + statusPill(f.status) +
          '</td><td>' + esc(f.expected) + '</td><td>' + esc(f.received) + '</td><td>' + esc(f.known) +
          '</td><td>' + esc(f.missing) + '</td><td>' + esc(f.coverage_pct) + '</td><td>' + esc(f.note || '') +
          (f.completeness_claim === false ? ' <em>（禁止历史齐全宣称）</em>' : '') +
          '</td></tr>';
      }).join('');
    }
    var gaps = q('#covGaps');
    if (gaps) {
      var list = rep.gaps || [];
      gaps.innerHTML = list.length ? list.map(function (g) {
        return '<tr><td>' + esc(g.title || g.field_id) + '</td><td>' + statusPill(g.status) +
          '</td><td><small>' + esc((g.gap_symbols_sample || []).join(', ') || '—') + '</small></td>' +
          '<td><small>' + esc(g.backfill_hint || '') + '</small></td></tr>';
      }).join('') : '<tr><td colspan="4" class="muted">无缺口或尚未加载</td></tr>';
    }
    var snapBody = q('#covSnaps');
    if (snapBody) {
      var snapsList = (snaps && snaps.snapshots) || [];
      snapBody.innerHTML = snapsList.slice(0, 12).map(function (s) {
        var ev = s.expected_vs_received || {};
        return '<tr><td><code>' + esc(s.scan_id || '—') + '</code> / r' + esc(s.revision) +
          '</td><td>' + esc(ev.expected != null ? ev.expected : s.expected != null ? s.expected : s.total) +
          '</td><td>' + esc(ev.received != null ? ev.received : s.received) +
          '</td><td>' + esc(ev.row_ratio != null ? ev.row_ratio : '—') +
          '</td><td><small>' + esc(String(s.coverage_fingerprint || '').slice(0, 12) || '—') + '</small></td>' +
          '<td>' + esc(s.status || '') + '</td></tr>';
      }).join('') || '<tr><td colspan="6" class="muted">尚无快照</td></tr>';
    }
    var claims = q('#covClaims');
    if (claims) {
      var c = rep.claims || {};
      claims.textContent =
        '硬性声明：oi_history_complete=' + c.oi_history_complete +
        ' · market_cap_history_complete=' + c.market_cap_history_complete +
        ' · account_history_complete=' + c.account_history_complete +
        ' · ' + (c.note || '');
    }
    toast('覆盖闸门已刷新 · 聚合 ' + (agg.status || '') + ' · gaps=' + (rep.gap_count || 0));
  }

  function refresh() {
    ensurePanel();
    Promise.all([
      api('/api/coverage?candle_limit=40'),
      api('/api/snapshots?limit=12').catch(function () { return { snapshots: [] }; }),
    ]).then(function (pair) {
      render(pair[0], pair[1]);
    }).catch(function (err) {
      toast('加载失败：' + err.message);
    });
  }

  function boot() {
    if (!q('[data-view="tasks"]')) return;
    ensurePanel();
    var panel=q('#coverageGatePanel');
    var visible=new IntersectionObserver(function(entries){if(entries.some(function(e){return e.isIntersecting;})){visible.disconnect();refresh();}});visible.observe(panel);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
