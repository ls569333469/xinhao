/** EXT-01：来源槽位（默认未授权）+ 精确⑤拒冒充；USDC/COIN-M 引擎未就绪。 */
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
    var el = q('#extStatus');
    if (el) el.textContent = msg;
  }
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
          throw err;
        }
        return data;
      });
    });
  }
  var state = { catalog: null, sources: null };

  function ensurePanel() {
    var section = q('[data-view="settings"]') || q('[data-view="market"]');
    if (!section || q('#extRuntimePanel')) return;
    var panel = document.createElement('div');
    panel.className = 'panel';
    panel.id = 'extRuntimePanel';
    panel.innerHTML =
      '<div class="panel-head"><div><h2>扩展能力（EXT-01 加深）</h2>' +
      '<small>来源槽位默认未授权/未核验 · 历史交易所净流独立保留；⑤采用聪明钱 · 解锁时间用「第三方解锁网站上的时间」或「未在官网上核对」</small></div>' +
      '<span class="pill gray" id="extReadyPill">未就绪</span></div>' +
      '<p id="extStatus" class="muted">加载中…</p>' +
      '<div class="command-bar"><button type="button" data-ext="refresh">刷新</button></div>' +
      '<h3>能力目录</h3>' +
      '<div class="tablewrap"><table><thead><tr><th>能力</th><th>状态</th><th>说明</th></tr></thead>' +
      '<tbody id="extBody"></tbody></table></div>' +
      '<h3 style="margin-top:12px">授权来源槽位</h3>' +
      '<div class="tablewrap"><table><thead><tr><th>槽位</th><th>status</th><th>as_of</th><th>口径</th><th>url / hash</th><th>claim_ready</th></tr></thead>' +
      '<tbody id="extSlots"></tbody></table></div>' +
      '<p class="muted">USDC/COIN-M 引擎仅目录展示、不可 claim 就绪；授权须本地核验后写入 url/hash/as_of/口径，不自动发明真源。</p>';
    (document.querySelector('[data-setting-view="sources"]')||section).appendChild(panel);
    panel.addEventListener('click', function (ev) {
      var btn = ev.target.closest('[data-ext]');
      if (btn && btn.getAttribute('data-ext') === 'refresh') refresh();
    });
  }

  function statusPill(ready, status) {
    if (ready) return '<span class="pill">就绪</span>';
    return '<span class="pill gray">' + esc(status || '未就绪') + '</span>';
  }

  function refresh() {
    ensurePanel();
    Promise.all([
      api('/api/ext/catalog'),
      api('/api/ext/sources'),
    ]).then(function (pair) {
      state.catalog = pair[0];
      state.sources = pair[1];
      var cat = pair[0];
      var body = q('#extBody');
      if (body) {
        body.innerHTML = (cat.items || []).map(function (it) {
          return '<tr><td>' + esc(it.title) + '</td><td>' +
            statusPill(it.ready, it.status) +
            '</td><td>' + esc(it.note) + '</td></tr>';
        }).join('');
      }
      var slots = q('#extSlots');
      if (slots) {
        var list = (pair[1].slots) || cat.source_slots || [];
        slots.innerHTML = list.map(function (s) {
          return '<tr><td><code>' + esc(s.slot_id) + '</code><small>' + esc(s.title || '') + '</small></td>' +
            '<td>' + esc(s.status) + '</td>' +
            '<td>' + esc(s.as_of || '—') + '</td>' +
            '<td>' + esc(s['口径'] || '—') + '</td>' +
            '<td><small>' + esc(s.url || '—') + '<br>' + esc((s.content_hash || '').slice(0, 16) || '—') + '</small></td>' +
            '<td>' + (s.claim_ready ? '是' : '否') + '</td></tr>';
        }).join('');
      }
      var pill = q('#extReadyPill');
      if (pill) pill.textContent = cat.any_ready ? '部分槽位可声明' : '均未就绪';
      toast('扩展目录已加载 · any_ready=' + !!cat.any_ready);
    }).catch(function (err) {
      toast('加载失败：' + err.message);
    });
  }

  function boot() { ensurePanel(); refresh(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
  window.ExtStoreUI = { refresh: refresh };
})();
