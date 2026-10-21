/** CONFIG-01：设置页全字段双向绑定 + 备份向导（永不回显 AI Key）。 */
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
    var el = q('#cfgStatus');
    if (el) el.textContent = msg;
  }
  function conflictMsg(err) {
    if (err && err.status === 409) {
      return '冲突 409（拒绝静默覆盖）：' + (err.message || '');
    }
    return (err && err.message) || String(err);
  }


  function shortH(h) { return String(h || '').slice(0, 12); }
  function integritySummary(pkg) {
    if (!pkg || typeof pkg !== 'object') return { note: '无包' };
    var man = pkg.manifest || {};
    var listed = man.sections || [];
    var hashes = listed.map(function (sec) {
      var ph = sec && sec.payload_hash;
      return { id: (sec && sec.id) || '?', payload_hash: ph ? shortH(ph) : '（缺）', item_count: sec && sec.item_count };
    });
    // 若只有 sections 数据、无 manifest，标缺 hash，方便发现坏包
    if (!hashes.length && pkg.sections) {
      Object.keys(pkg.sections).forEach(function (id) {
        hashes.push({ id: id, payload_hash: '（缺 manifest）' });
      });
    }
    var phFull = man.package_hash || pkg.package_hash || null;
    return {
      schema: pkg.schema,
      integrity: man.integrity || pkg.integrity || 'manifest+payload_hash（期望）',
      package_hash: phFull ? shortH(phFull) : '—',
      package_hash_full: phFull,
      credentials_included: !!pkg.credentials_included,
      backup_file: pkg.backup_file || null,
      section_count: hashes.length,
      sections: hashes,
      note: '导入会逐段核验 payload_hash；缺 hash 或篡改 → 409，不静默覆盖',
    };
  }

  var state = {
    bind: null,
    catalog: null,
    config: null,
    scan: null,
    ai: null,
    sim: null,
    evalPlans: [],
    reviewPlans: [],
    wizard: { step: 'idle', package: null, review: null },
  };

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
    var section = q('[data-view="settings"]') || q('[data-view="tasks"]');
    if (!section || q('#configRuntimePanel')) return;
    var panel = document.createElement('div');
    panel.className = 'panel';
    panel.id = 'configRuntimePanel';
    panel.innerHTML =
      '<div class="panel-head"><div><h2>设置双向绑定（CONFIG-01）</h2>' +
      '<small>打开即从服务端加载 · 保存带 version/hash · 409 冲突明示 · 永不回显 AI Key / DPAPI</small></div>' +
      '<span class="pill gray" id="cfgBindPill">settings-bind</span></div>' +
      '<p id="cfgStatus" class="muted">加载中…</p>' +

      '<h3>观察池 / 提示词</h3>' +
      '<div class="row" style="gap:8px;flex-wrap:wrap">' +
      '<label style="flex:1">观察池合约（逗号分隔）<input id="cfgPool" style="width:100%" placeholder="BTCUSDT, ETHUSDT"></label>' +
      '</div>' +
      '<div class="row" style="gap:8px;flex-wrap:wrap;margin-top:8px">' +
      '<label style="flex:1">提示词覆写 common<textarea id="cfgPrompt" rows="3" style="width:100%"></textarea></label>' +
      '</div>' +
      '<div class="command-bar" style="margin-top:8px">' +
      '<span class="muted" id="cfgMeta"></span>' +
      '<div class="row">' +
      '<button type="button" data-cfg="refresh">重新加载全部</button>' +
      '<button type="button" class="primary" data-cfg="save-core">保存池/提示词</button>' +
      '</div></div>' +

      '<h3 style="margin-top:16px">扫描调度（只读摘要）</h3><p>统一在「设置 → 自动任务」保存常规扫描、B观察和偏移；此处不另存第二份。</p>' +
      '<div class="row" style="gap:12px;flex-wrap:wrap;align-items:center">' +
      '<label><input type="checkbox" id="cfgScanEnabled" disabled> 自动扫描</label>' +
      '<label>扫描间隔 <select id="cfgScanInterval" disabled><option value="0.25">15分钟（测试）</option><option value="1">1</option><option value="4" selected>4</option><option value="8">8</option><option value="24">24</option></select></label>' +
      '<label>时区 <select id="cfgScanTz" disabled><option>Asia/Shanghai</option><option>America/Los_Angeles</option><option>UTC</option></select></label>' +
      '<label><input type="checkbox" id="cfgScanRecover" checked disabled> 重启补扫</label>' +
      '<span class="muted" id="cfgScanMeta"></span>' +
      '<button type="button" class="primary" data-cfg="save-scan" hidden disabled>保存扫描</button>' +
      '</div>' +

      '<h3 style="margin-top:16px">AI（非密钥只读摘要）</h3>' +
      '<p class="muted" id="cfgAiSummary">—</p>' +
      '<p class="muted">Key 仅显示是否已配置；保存/替换/清除请用 AI 设置面板（DPAPI，不进 localStorage）。</p>' +

      '<h3 style="margin-top:16px">SIM 费用 / 分档预算</h3>' +
      '<div class="row" style="gap:8px;flex-wrap:wrap">' +
      '<label>fee_rate <input id="cfgSimFee" type="number" step="0.0001" style="width:7em"></label>' +
      '<label>slip_rate <input id="cfgSimSlip" type="number" step="0.0001" style="width:7em"></label>' +
      '<label>budget_t1_u <input id="cfgSimT1" type="number" step="1" style="width:7em"></label>' +
      '<label>budget_t2_u <input id="cfgSimT2" type="number" step="1" style="width:7em"></label>' +
      '<label>budget_t3_u <input id="cfgSimT3" type="number" step="1" style="width:7em"></label>' +
      '<span class="muted" id="cfgSimMeta"></span>' +
      '<button type="button" class="primary" data-cfg="save-sim">保存 SIM</button>' +
      '</div>' +

      '<h3 style="margin-top:16px">EVAL 计划开关</h3>' +
      '<div class="tablewrap"><table><thead><tr><th>计划</th><th>启用</th><th>auto_enqueue_backfill</th><th>版本/合约</th></tr></thead>' +
      '<tbody id="cfgEvalPlans"><tr><td colspan="4" class="muted">加载中…</td></tr></tbody></table></div>' +

      '<h3 style="margin-top:16px">REVIEW 计划开关</h3>' +
      '<div class="tablewrap"><table><thead><tr><th>计划</th><th>启用</th><th>标签</th></tr></thead>' +
      '<tbody id="cfgReviewPlans"><tr><td colspan="3" class="muted">加载中…</td></tr></tbody></table></div>' +

      '<hr style="margin:16px 0;opacity:.3">' +
      '<div class="panel-head"><div><h3>全量备份 / 恢复向导</h3>' +
      '<small>凭证永不进入包 · SIM 账本默认排除</small></div>' +
      '<span class="pill" id="cfgWizStep">idle</span></div>' +
      '<div class="row" style="gap:12px;flex-wrap:wrap;align-items:center;margin:8px 0">' +
      '<label><input type="checkbox" id="cfgIncLedger"> 导出含 SIM 账本</label>' +
      '<label><input type="checkbox" id="cfgImpLedger"> 导入恢复账本</label>' +
      '<label><input type="checkbox" id="cfgForce"> force 覆盖冲突</label>' +
      '</div>' +
      '<div class="command-bar"><div class="row" style="flex-wrap:wrap;gap:6px">' +
      '<button type="button" data-cfg="wiz-manifest">① 清单</button>' +
      '<button type="button" class="primary" data-cfg="wiz-export">② 导出</button>' +
      '<button type="button" data-cfg="wiz-review">③ 审阅</button>' +
      '<button type="button" data-cfg="wiz-import">④ 导入</button>' +
      '<button type="button" data-cfg="wiz-download">下载 JSON</button>' +
      '<label style="display:inline-flex;align-items:center;gap:6px;cursor:pointer">选文件导入'
      + '<input type="file" id="cfgWizFile" accept="application/json,.json" style="display:none"></label>' +
      '</div></div>' +
      '<textarea id="cfgWizPackage" rows="5" style="width:100%;margin-top:8px;font-family:monospace;font-size:12px"></textarea>' +
      '<pre id="cfgWizReport" class="muted" style="white-space:pre-wrap;max-height:180px;overflow:auto;margin-top:8px"></pre>' +
      '<p class="muted">冲突拒绝静默覆盖；导入逐段核验 payload_hash / package_hash；缺 hash 或篡改 → 409。凭证永不进包。普通缓存清理不应删 runtime/config/。</p>';

    var advanced=document.createElement('details');advanced.className='panel advanced-config';advanced.innerHTML='<summary>高级配置与全量备份（保留所有原设置）</summary>';advanced.appendChild(panel);(document.querySelector('[data-setting-view="storage"]')||section).appendChild(advanced);
    panel.addEventListener('click', onClick);
    panel.addEventListener('change', onChange);
    var file = q('#cfgWizFile', panel);
    if (file && !file._wired) {
      file._wired = true;
      file.addEventListener('change', function () {
        var f = file.files && file.files[0];
        if (!f) return;
        var reader = new FileReader();
        reader.onload = function () {
          try {
            var pkg = JSON.parse(String(reader.result || ''));
            putPackage(pkg);
            report(integritySummary(pkg));
            setWizStep('loaded');
            toast('已载入备份文件 · 可点③审阅或④导入');
          } catch (e) {
            toast('文件不是合法 JSON：' + e.message);
          }
          file.value = '';
        };
        reader.readAsText(f, 'utf-8');
      });
    }
  }

  function onClick(ev) {
    var btn = ev.target.closest('[data-cfg]');
    if (!btn) return;
    var act = btn.getAttribute('data-cfg');
    if (act === 'refresh') refresh();
    if (act === 'save-core') saveCore();
    if (act === 'save-scan') saveScan();
    if (act === 'save-sim') saveSim();
    if (act === 'wiz-manifest') wizManifest();
    if (act === 'wiz-export') wizExport();
    if (act === 'wiz-review') wizReview();
    if (act === 'wiz-import') wizImport();
    if (act === 'wiz-download') wizDownload();
  }

  function onChange(ev) {
    var t = ev.target;
    if (!t) return;
    if (t.getAttribute('data-eval-en')) {
      toggleEvalEnabled(t.getAttribute('data-eval-en'), t.checked);
    }
    if (t.getAttribute('data-eval-enq')) {
      toggleEvalEnqueue(t.getAttribute('data-eval-enq'), t.checked);
    }
    if (t.getAttribute('data-review-en')) {
      toggleReviewEnabled(t.getAttribute('data-review-en'), t.checked);
    }
  }

  function renderCore() {
    var cfg = state.config || {};
    var pool = (cfg.observation_pool || {}).symbols || [];
    var inp = q('#cfgPool');
    if (inp) inp.value = pool.map(function (s) { return s.symbol || s; }).join(', ');
    var pe = (cfg.prompt_edits || {}).overrides || {};
    var ta = q('#cfgPrompt');
    if (ta) ta.value = pe.common || '';
    var meta = q('#cfgMeta');
    if (meta) {
      meta.textContent = 'revision=' + ((cfg.meta || {}).revision || '—') +
        ' · hash=' + String((cfg.meta || {}).content_hash || '').slice(0, 12);
    }
  }

  function renderScan() {
    var sc = (state.scan && state.scan.scan) || {};
    var en = q('#cfgScanEnabled');
    if (en) en.checked = !!sc.enabled;
    var iv = q('#cfgScanInterval');
    if (iv && sc.intervalHours) iv.value = String(sc.intervalHours);
    var tz = q('#cfgScanTz');
    if (tz && sc.timezone) tz.value = sc.timezone;
    var rc = q('#cfgScanRecover');
    if (rc) rc.checked = sc.recoverMissed !== false;
    var meta = q('#cfgScanMeta');
    if (meta) {
      meta.textContent = 'version=' + ((state.scan && state.scan.version) || '—') +
        ' · next=' + ((state.scan && state.scan.next_due) || '—') + ' · B观察=' + ((state.scan && state.scan.observe && state.scan.observe.enabled) ? '开' : '关') + ' · 偏移=' + ((state.scan && state.scan.timing && state.scan.timing.closeOffsetSec) != null ? state.scan.timing.closeOffsetSec : 120) + 's';
    }
  }

  function renderAi() {
    var ai = state.ai || {};
    var el = q('#cfgAiSummary');
    if (!el) return;
    // Never show raw key fields even if somehow present
    var leak = ai.api_key || ai.apiKey || ai.key || ai.secret;
    if (leak) {
      el.textContent = '异常：响应含密钥字段，已屏蔽显示';
      return;
    }
    el.textContent =
      (ai.keyConfigured ? 'Key：已配置（DPAPI）' : 'Key：未配置') +
      ' · provider=' + (ai.provider || '—') +
      ' · protocol=' + (ai.protocol || '—') +
      ' · model=' + (ai.modelId || '—') +
      ' · baseUrl=' + (ai.baseUrl || '—') +
      ' · configId=' + (ai.configId || '—');
  }

  function renderSim() {
    var sim = state.sim || {};
    var map = {
      cfgSimFee: 'fee_rate', cfgSimSlip: 'slip_rate',
      cfgSimT1: 'budget_t1_u', cfgSimT2: 'budget_t2_u', cfgSimT3: 'budget_t3_u',
    };
    Object.keys(map).forEach(function (id) {
      var el = q('#' + id);
      if (el && sim[map[id]] != null) el.value = sim[map[id]];
    });
    var meta = q('#cfgSimMeta');
    if (meta) meta.textContent = 'rev=' + (sim.revision || '—') + ' · updated_at=' + (sim.updated_at || '—') + ' · mode=' + (sim.mode || '—');
  }

  function renderEvalPlans() {
    var body = q('#cfgEvalPlans');
    if (!body) return;
    var plans = state.evalPlans || [];
    if (!plans.length) {
      body.innerHTML = '<tr><td colspan="4" class="muted">暂无 EVAL 计划</td></tr>';
      return;
    }
    body.innerHTML = plans.map(function (p) {
      var pid = p.plan_id || '';
      return '<tr><td><code>' + esc(pid) + '</code><small>' + esc(p.label || '') + '</small></td>' +
        '<td><input type="checkbox" data-eval-en="' + esc(pid) + '"' + (p.enabled ? ' checked' : '') + '></td>' +
        '<td><input type="checkbox" data-eval-enq="' + esc(pid) + '"' + (p.auto_enqueue_backfill ? ' checked' : '') + '></td>' +
        '<td><small>' + esc(p.strategy_version_id || '') + ' · ' + esc((p.symbols || []).join(',')) + '</small></td></tr>';
    }).join('');
  }

  function renderReviewPlans() {
    var body = q('#cfgReviewPlans');
    if (!body) return;
    var plans = state.reviewPlans || [];
    if (!plans.length) {
      body.innerHTML = '<tr><td colspan="3" class="muted">暂无 REVIEW 计划</td></tr>';
      return;
    }
    body.innerHTML = plans.map(function (p) {
      var pid = p.plan_id || '';
      return '<tr><td><code>' + esc(pid) + '</code></td>' +
        '<td><input type="checkbox" data-review-en="' + esc(pid) + '"' + (p.enabled ? ' checked' : '') + '></td>' +
        '<td>' + esc(p.label || p.title || '') + '</td></tr>';
    }).join('');
  }

  function renderAll() {
    renderCore();
    renderScan();
    renderAi();
    renderSim();
    renderEvalPlans();
    renderReviewPlans();
    var pill = q('#cfgBindPill');
    if (pill && state.bind && state.bind.bind) {
      pill.textContent = state.bind.bind.ready ? 'bind ready' : 'bind';
    }
  }

  function applyBindPayload(bind) {
    state.bind = bind;
    state.config = bind.config || state.config;
    state.scan = bind.scan || null;
    state.ai = bind.ai || null;
    state.sim = bind.sim || null;
    state.evalPlans = ((bind.eval_plans || {}).plans) || [];
    state.reviewPlans = ((bind.review_plans || {}).plans) || [];
  }

  function refresh() {
    ensurePanel();
    Promise.all([
      api('/api/config/settings-bind'),
      api('/api/config/catalog').catch(function () { return null; }),
    ]).then(function (pair) {
      applyBindPayload(pair[0]);
      state.catalog = pair[1];
      renderAll();
      toast('设置已从服务端加载（无凭证）');
    }).catch(function (err) {
      // fallback: parallel old endpoints
      return Promise.all([
        api('/api/config'),
        api('/api/settings').catch(function () { return null; }),
        api('/api/ai/config').catch(function () { return null; }),
        api('/api/sim/config').catch(function () { return null; }),
        api('/api/eval/plans').catch(function () { return { plans: [] }; }),
        api('/api/review/plans').catch(function () { return { plans: [] }; }),
      ]).then(function (xs) {
        state.config = xs[0];
        state.scan = xs[1];
        state.ai = xs[2];
        state.sim = xs[3];
        state.evalPlans = (xs[4] && xs[4].plans) || [];
        state.reviewPlans = (xs[5] && xs[5].plans) || [];
        renderAll();
        toast('设置已加载（兼容路径）· ' + conflictMsg(err));
      }).catch(function (e2) {
        toast('加载失败：' + conflictMsg(e2));
      });
    });
  }

  function saveCore() {
    var raw = ((q('#cfgPool') || {}).value || '');
    var symbols = raw.split(/[,，\s]+/).map(function (s) { return s.trim(); }).filter(Boolean);
    var common = ((q('#cfgPrompt') || {}).value || '');
    Promise.resolve()
      .then(function () {
        return api('/api/config/observation-pool', { method: 'PUT', body: { symbols: symbols } });
      })
      .then(function () {
        return api('/api/config').then(function (cfg) {
          state.config = cfg;
          var body = { overrides: {}, expected_content_hash: cfg.meta.content_hash };
          if (common) body.overrides.common = common;
          else body.delete_keys = ['common'];
          return api('/api/config/prompt-edits', { method: 'PUT', body: body });
        });
      })
      .then(function (st) {
        state.config = st;
        renderCore();
        toast('观察池/提示词已保存');
      })
      .catch(function (err) { toast('保存失败：' + conflictMsg(err)); });
  }

  function saveScan() {
    if (!state.scan || state.scan.version == null) {
      toast('请先加载扫描设置');
      return;
    }
    var body = {
      version: state.scan.version,
      scan: {
        enabled: !!(q('#cfgScanEnabled') && q('#cfgScanEnabled').checked),
        intervalHours: Number((q('#cfgScanInterval') || {}).value || 4),
        timezone: (q('#cfgScanTz') || {}).value || 'Asia/Shanghai',
        recoverMissed: !!(q('#cfgScanRecover') && q('#cfgScanRecover').checked),
      },
    };
    api('/api/settings', { method: 'PUT', body: body }).then(function (st) {
      state.scan = st;
      renderScan();
      toast('扫描设置已保存 · version=' + st.version);
    }).catch(function (err) { toast('扫描保存失败：' + conflictMsg(err)); });
  }

  function saveSim() {
    var body = {
      fee_rate: Number((q('#cfgSimFee') || {}).value),
      slip_rate: Number((q('#cfgSimSlip') || {}).value),
      budget_t1_u: Number((q('#cfgSimT1') || {}).value),
      budget_t2_u: Number((q('#cfgSimT2') || {}).value),
      budget_t3_u: Number((q('#cfgSimT3') || {}).value),
    };
    if (state.sim && state.sim.revision != null) {
      body.expected_revision = state.sim.revision;
    } else if (state.sim && state.sim.updated_at) {
      body.expected_updated_at = state.sim.updated_at;
    }
    api('/api/sim/config', { method: 'PUT', body: body }).then(function (cfg) {
      state.sim = cfg;
      renderSim();
      toast('SIM 费用/预算已保存');
    }).catch(function (err) { toast('SIM 保存失败：' + conflictMsg(err)); });
  }

  function toggleEvalEnabled(pid, enabled) {
    api('/api/eval/plans/' + encodeURIComponent(pid) + '/enable', {
      method: 'POST', body: { enabled: !!enabled },
    }).then(function () {
      toast('EVAL ' + pid + ' enabled=' + enabled);
      return refresh();
    }).catch(function (err) {
      toast('EVAL 启用失败：' + conflictMsg(err));
      refresh();
    });
  }

  function toggleEvalEnqueue(pid, enq) {
    var plan = (state.evalPlans || []).find(function (p) { return p.plan_id === pid; });
    if (!plan) { toast('找不到计划 ' + pid); return; }
    var body = {
      plan_id: pid,
      strategy_version_id: plan.strategy_version_id,
      symbols: plan.symbols,
      market: plan.market,
      side: plan.side,
      interval: plan.interval,
      observation_horizon_days: plan.observation_horizon_days,
      max_hold_days: plan.max_hold_days,
      check_interval_minutes: plan.check_interval_minutes,
      auto_backfill_on_gap: plan.auto_backfill_on_gap !== false,
      auto_enqueue_backfill: !!enq,
      enabled: !!plan.enabled,
      label: plan.label,
      tp_pct: plan.tp_pct,
      sl_pct: plan.sl_pct,
      notional_u: plan.notional_u,
    };
    api('/api/eval/plans', { method: 'PUT', body: body }).then(function () {
      toast('EVAL auto_enqueue_backfill=' + enq);
      return refresh();
    }).catch(function (err) {
      toast('EVAL 更新失败：' + conflictMsg(err));
      refresh();
    });
  }

  function toggleReviewEnabled(pid, enabled) {
    api('/api/review/plans/' + encodeURIComponent(pid) + '/enable', {
      method: 'POST', body: { enabled: !!enabled },
    }).then(function () {
      toast('REVIEW ' + pid + ' enabled=' + enabled);
      return refresh();
    }).catch(function (err) {
      toast('REVIEW 启用失败：' + conflictMsg(err));
      refresh();
    });
  }

  /* ---- backup wizard (kept) ---- */
  function setWizStep(step) {
    state.wizard.step = step;
    var el = q('#cfgWizStep');
    if (el) el.textContent = step;
  }
  function report(obj) {
    var el = q('#cfgWizReport');
    if (el) el.textContent = typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2);
  }
  function packageOpts() {
    return { include_sim_ledger: !!(q('#cfgIncLedger') && q('#cfgIncLedger').checked) };
  }
  function packageFromTextarea() {
    var raw = ((q('#cfgWizPackage') || {}).value || '').trim();
    if (!raw) return null;
    return JSON.parse(raw);
  }
  function putPackage(pkg) {
    state.wizard.package = pkg;
    var el = q('#cfgWizPackage');
    if (el && pkg) el.value = JSON.stringify(pkg, null, 2);
  }
  function wizManifest() {
    setWizStep('manifest');
    api('/api/config/backup/manifest', { method: 'POST', body: packageOpts() })
      .then(function (m) { report(m); toast('清单已生成'); })
      .catch(function (err) { toast(conflictMsg(err)); });
  }
  function wizExport() {
    setWizStep('export');
    api('/api/config/backup/export', { method: 'POST', body: packageOpts() })
      .then(function (pkg) {
        putPackage(pkg);
        report(integritySummary(pkg));
        toast('全量包已导出 · package_hash ' + shortH((pkg.manifest && pkg.manifest.package_hash) || pkg.package_hash));
      }).catch(function (err) { toast(conflictMsg(err)); });
  }
  function wizReview() {
    var pkg;
    try { pkg = state.wizard.package || packageFromTextarea(); } catch (e) { toast(e.message); return; }
    if (!pkg) { toast('请先导出或粘贴包'); return; }
    setWizStep('review');
    api('/api/config/backup/review', { method: 'POST', body: pkg })
      .then(function (rev) {
        state.wizard.review = rev;
        var base = integritySummary(pkg);
        report({ integrity: base, review: rev });
        var miss = (rev.missing_payload_hash || rev.payload_hash_failures || []).length;
        toast('审阅 · 冲突=' + (rev.conflict_count || 0) + (miss ? (' · hash问题 ' + miss) : ' · payload_hash 已核'));
      })
      .catch(function (err) { toast(conflictMsg(err)); });
  }
  function wizImport() {
    var pkg;
    try { pkg = state.wizard.package || packageFromTextarea(); } catch (e) { toast(e.message); return; }
    if (!pkg) { toast('请先导出或粘贴包'); return; }
    setWizStep('import');
    var body = Object.assign({}, pkg, {
      force: !!(q('#cfgForce') && q('#cfgForce').checked),
      import_sim_ledger: !!(q('#cfgImpLedger') && q('#cfgImpLedger').checked),
    });
    api('/api/config/backup/import', { method: 'POST', body: body })
      .then(function (out) { report(out); toast('导入完成'); refresh(); })
      .catch(function (err) { toast('导入失败：' + conflictMsg(err)); report({ error: err.message, status: err.status }); });
  }
  function wizDownload() {
    var pkg = state.wizard.package;
    try { if (!pkg) pkg = packageFromTextarea(); } catch (e) { toast(e.message); return; }
    if (!pkg) { toast('无包可下载'); return; }
    var blob = new Blob([JSON.stringify(pkg, null, 2)], { type: 'application/json' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = (pkg.backup_file || 'workbench-backup').split('/').pop() || 'workbench-backup.json';
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
  }

  function boot() {
    ensurePanel();
    refresh();
    var gotoBtn = q('#storageGotoBackup');
    if (gotoBtn && !gotoBtn._wired) {
      gotoBtn._wired = true;
      gotoBtn.addEventListener('click', function () {
        var setBtn = q('[data-set="storage"]') || q('[data-page="settings"]');
        if (setBtn) setBtn.click();
        ensurePanel();
        var panel = q('#configRuntimePanel');
        if(panel&&panel.closest('details'))panel.closest('details').open=true;
        if (panel && panel.scrollIntoView) panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
        toast('全量备份向导在设置页下方');
      });
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
  window.ConfigSettingsBind = { refresh: refresh };
})();
