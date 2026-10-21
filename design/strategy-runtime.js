/** RULE-01: 策略草案持久化 / 冻结版本 / 依赖闸门（无 SIM/EVAL 执行）。 */
(function () {
  'use strict';
  function q(sel, root) { return (root || document).querySelector(sel); }
  function qa(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }
  function toast(msg) {
    if (typeof window.showToast === 'function') return window.showToast(msg);
    var el = q('#strategyStoreStatus');
    if (el) el.textContent = msg;
    else console.log('[策略]', msg);
  }
  function esc(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function shortHash(h) { return h ? String(h).slice(0, 12) : '—'; }


  // —— UI02: 模板身份 + EMA99 结构化参数（避免指导页标题是 T2 却保存 T1 三字段）——
  var LEGACY_T1_PARAM_KEYS = ['oiCapPct', 'accountRatio', 'fundingAprPct'];
  var T2_INAPPLICABLE_LABELS = [
    '候选有效期（小时）',
    '本根量 / 前20根均量大于',
    '止盈（R倍数，未验证）',
    '时间退出（小时）'
  ];

  function defaultEma99Params() {
    return {
      execution_mode: 'ema99_close_trail',
      entry_rule: 'ema_squeeze_confirmed',
      candidate_max_days: 30,
      oi_exit_ratio: 0.70,
      vol_confirm_ratio: 2.0,
      volume_confirm_window: 'next_bar_only',
      initial_stop: 'cross_bar_ema99',
      trail_activation: 'first_close_above_entry',
      intrabar_stop: false,
      side: 'long',
      params_schema_version: 'ema99-close-trail-v1',
      user_rules_version: 'ema-squeeze-v4'
    };
  }

  function editorMode() {
    var el = q('#strategyEditorMode');
    return (el && el.value) || 't1_generic';
  }

  function ensureIdentityFields() {
    var panel = q('[data-strategy-view="editor"]');
    if (!panel) return;
    function need(id, val) {
      if (q('#' + id, panel)) return;
      var inp = document.createElement('input');
      inp.type = 'hidden';
      inp.id = id;
      inp.value = val || '';
      panel.appendChild(inp);
    }
    need('strategyEditorMode', 't1_generic');
    need('strategyTemplateId', '');
    need('strategyRegistryVersion', '');
    need('strategyRegistryHash', '');
    need('strategyExecutableFlag', 'false');
  }

  function ensureEmaModeForm() {
    ensureIdentityFields();
    if (q('#emaModeForm')) return q('#emaModeForm');
    var guided = q('#guidedDraft');
    if (!guided) return null;
    var box = document.createElement('div');
    box.id = 'emaModeForm';
    box.hidden = true;
    box.innerHTML = ''
      + '<div class="note" style="margin:10px 0">EMA三线金叉参数：候选约30天、入池OI基数、金叉后下一根放量确认；未启用前不可当作已验收入场。</div>'
      + '<div class="grid2">'
      + '<label class="field"><span>模板身份</span><input id="emaFieldTemplateId" readonly></label>'
      + '<label class="field"><span>注册表版本</span><input id="emaFieldRegistryVer" readonly></label>'
      + '<label class="field"><span>执行模式 execution_mode</span>'
      + '<select id="emaFieldExecMode"><option value="ema99_close_trail">ema99_close_trail</option></select></label>'
      + '<label class="field"><span>入场规则 entry_rule</span>'
      + '<select id="emaFieldEntryRule">'
      + '<option value="ema_squeeze_confirmed">ema_squeeze_confirmed</option>'
      + '<option value="ema_squeeze_full_cross_vol">ema_squeeze_full_cross_vol</option>'
      + '<option value="candidate_cross_vol">candidate_cross_vol</option>'
      + '</select></label>'
      + '<label class="field"><span>候选有效期（天）candidate_max_days</span><input id="emaFieldCandDays" type="number" value="30" min="1" max="30"></label>'
      + '<label class="field"><span>OI 出池比例 oi_exit_ratio</span><input id="emaFieldOiExit" type="number" value="0.70" min="0.1" max="1" step="0.01"></label>'
      + '<label class="field"><span>放量确认倍数 vol_confirm_ratio</span><input id="emaFieldVol" type="number" value="2" min="1" step="0.1"></label>'
      + '<label class="field"><span>量能等待 volume_confirm_window</span>'
      + '<select id="emaFieldVolWait"><option value="next_bar_only">next_bar_only（仅紧接下一根）</option></select></label>'
      + '<label class="field"><span>初始止损 initial_stop</span>'
      + '<select id="emaFieldInitStop"><option value="cross_bar_ema99">cross_bar_ema99（金叉根）</option></select></label>'
      + '<label class="field"><span>追踪激活 trail_activation</span>'
      + '<select id="emaFieldTrail"><option value="first_close_above_entry">first_close_above_entry</option></select></label>'
      + '<label class="field"><span>盘中触价止损 intrabar_stop</span>'
      + '<select id="emaFieldIntrabar"><option value="false">false（仅收盘）</option></select></label>'
      + '<label class="field"><span>方向 side</span><select id="emaFieldSide"><option value="long">long</option></select></label>'
      + '<label class="field"><span>规则版本 user_rules_version</span><input id="emaFieldRulesVer" value="ema-squeeze-v4" readonly></label>'
      + '<label class="field"><span>参数schema params_schema_version</span><input id="emaFieldSchemaVer" value="ema99-close-trail-v1" readonly></label>'
      + '</div>'
      + '<label class="field"><span>策略想法 / 来源报告</span><textarea id="emaFieldIdea" rows="4" placeholder="记录假设；不会把纯文本当成已可执行规则。"></textarea></label>'
      + '<div id="emaUnfinishedBox" class="note" style="margin-top:8px"></div>'
      + '<p class="muted">冻结后不可原地改写；要改规则请从冻结版「开新草案」。缺依赖或未冻结前保持未启用。</p>';
    guided.insertAdjacentElement('afterend', box);
    return box;
  }

  function setLegacyFieldsDisabled(disabled) {
    var panel = q('[data-strategy-view="editor"]');
    if (!panel) return;
    T2_INAPPLICABLE_LABELS.forEach(function (label) {
      var fields = qa('.field', panel);
      for (var i = 0; i < fields.length; i++) {
        var span = fields[i].querySelector('span');
        if (!span || span.textContent.trim() !== label) continue;
        var inp = fields[i].querySelector('input, select, textarea');
        if (!inp) continue;
        inp.disabled = !!disabled;
        fields[i].style.opacity = disabled ? '0.45' : '';
        var tip = fields[i].querySelector('.ema-disabled-tip');
        if (disabled && !tip) {
          tip = document.createElement('small');
          tip.className = 'ema-disabled-tip muted';
          tip.textContent = '（EMA99 模式不适用，已禁用）';
          fields[i].appendChild(tip);
        } else if (!disabled && tip) {
          tip.remove();
        }
      }
    });
  }

  function fillEmaForm(params, meta) {
    ensureEmaModeForm();
    params = params || defaultEma99Params();
    meta = meta || {};
    var map = {
      emaFieldTemplateId: meta.templateId || 'T2',
      emaFieldRegistryVer: meta.registryVersion || '',
      emaFieldExecMode: params.execution_mode || 'ema99_close_trail',
      emaFieldEntryRule: params.entry_rule || 'ema_squeeze_confirmed',
      emaFieldCandDays: params.candidate_max_days != null ? params.candidate_max_days : 30,
      emaFieldOiExit: params.oi_exit_ratio != null ? params.oi_exit_ratio : 0.70,
      emaFieldVol: params.vol_confirm_ratio != null ? params.vol_confirm_ratio : 2.0,
      emaFieldVolWait: params.volume_confirm_window || 'next_bar_only',
      emaFieldInitStop: params.initial_stop || 'cross_bar_ema99',
      emaFieldTrail: params.trail_activation || 'first_close_above_entry',
      emaFieldIntrabar: String(params.intrabar_stop === true ? 'true' : 'false'),
      emaFieldSide: params.side || 'long',
      emaFieldRulesVer: params.user_rules_version || 'ema-squeeze-v4',
      emaFieldSchemaVer: params.params_schema_version || 'ema99-close-trail-v1'
    };
    Object.keys(map).forEach(function (id) {
      var el = q('#' + id);
      if (el) el.value = map[id];
    });
    var idea = q('#emaFieldIdea');
    if (idea && meta.idea != null) idea.value = meta.idea;
    var box = q('#emaUnfinishedBox');
    if (box) {
      var miss = meta.missing || [];
      box.innerHTML = '<strong>未启用原因</strong>：'
        + (miss.length ? esc(miss.join('、')) : '缺依赖（待核验）')
        + '；完整K线/确认事件齐备并冻结前不启用。';
    }
  }

  function readEma99ParamsFromForm() {
    ensureEmaModeForm();
    var num = function (id, fallback) {
      var el = q('#' + id);
      if (!el || el.value === '') return fallback;
      var n = Number(el.value);
      return Number.isFinite(n) ? n : fallback;
    };
    var val = function (id, fallback) {
      var el = q('#' + id);
      return el && el.value !== '' ? el.value : fallback;
    };
    return {
      execution_mode: val('emaFieldExecMode', 'ema99_close_trail'),
      entry_rule: val('emaFieldEntryRule', 'ema_squeeze_confirmed'),
      candidate_max_days: num('emaFieldCandDays', 30),
      oi_exit_ratio: num('emaFieldOiExit', 0.70),
      vol_confirm_ratio: num('emaFieldVol', 2.0),
      volume_confirm_window: val('emaFieldVolWait', 'next_bar_only'),
      initial_stop: val('emaFieldInitStop', 'cross_bar_ema99'),
      trail_activation: val('emaFieldTrail', 'first_close_above_entry'),
      intrabar_stop: val('emaFieldIntrabar', 'false') === 'true',
      side: val('emaFieldSide', 'long'),
      params_schema_version: val('emaFieldSchemaVer', 'ema99-close-trail-v1'),
      user_rules_version: val('emaFieldRulesVer', 'ema-squeeze-v4')
    };
  }

  function setEditorMode(mode, meta) {
    ensureEmaModeForm();
    meta = meta || {};
    var modeEl = q('#strategyEditorMode');
    if (modeEl) modeEl.value = mode;
    if (q('#strategyTemplateId')) q('#strategyTemplateId').value = meta.templateId || (mode === 't2_ema99' ? 'T2' : '');
    if (q('#strategyRegistryVersion')) q('#strategyRegistryVersion').value = meta.registryVersion || '';
    if (q('#strategyRegistryHash')) q('#strategyRegistryHash').value = meta.registryHash || '';
    if (q('#strategyExecutableFlag')) q('#strategyExecutableFlag').value = 'false';

    var emaForm = q('#emaModeForm');
    var guided = q('#guidedDraft');
    var steps = q('#strategySteps');
    var stepViews = qa('[data-step-view]');

    if (mode === 't2_ema99') {
      if (guided) guided.hidden = false;
      if (emaForm) emaForm.hidden = false;
      if (steps) steps.hidden = true;
      stepViews.forEach(function (v) { v.hidden = true; });
      setLegacyFieldsDisabled(true);
      fillEmaForm(meta.params || defaultEma99Params(), meta);
      // 同步可见标题字段（步骤1里的策略名称），避免界面仍写着空头拥挤
      var fields = qa('.field', q('[data-strategy-view="editor"]'));
      for (var i = 0; i < fields.length; i++) {
        var span = fields[i].querySelector('span');
        if (span && span.textContent.trim() === '策略名称') {
          var inp = fields[i].querySelector('input');
          if (inp && meta.title) inp.value = meta.title;
        }
      }
      if (q('#editorHeading') && meta.title) {
        q('#editorHeading').textContent = meta.title + ' · 指导草案';
      }
    } else {
      if (emaForm) emaForm.hidden = true;
      setLegacyFieldsDisabled(false);
      if (meta.showSteps !== false) {
        if (steps) steps.hidden = false;
      }
    }
  }

  function applyTemplate(strategy) {
    if (!strategy || !strategy.id) return;
    var reg = (typeof StrategyRegistry !== 'undefined' && StrategyRegistry) || null;
    var ver = (reg && reg.data && reg.data.version) || strategy.version || '';
    var hash = (reg && reg.hash) || strategy.registryHash || '';
    var title = strategy.title || strategy.id;
    if (q('#strategyIdInput') && !q('#strategyIdInput').value) {
      q('#strategyIdInput').value = 'S-' + strategy.id;
    }
    if (q('#strategyDraftId')) q('#strategyDraftId').value = '';

    if (strategy.id === 'T2') {
      var idea = [
        '模板 ' + strategy.id + ' · ' + title,
        '注册表 ' + ver + (hash ? (' / ' + hash) : ''),
        '候选：' + (strategy.candidateText || ''),
        '触发：' + (strategy.trigger || ''),
        '退出：' + (strategy.exit || ''),
        '风险：' + (strategy.risk || ''),
        '缺口：' + ((strategy.missing || []).join('、') || '—')
      ].join('\n');
      if (q('#guidedDraftTitle')) q('#guidedDraftTitle').textContent = strategy.id + ' ' + title + ' · ' + ver;
      if (q('#guidedDraftBody')) q('#guidedDraftBody').textContent = idea;
      setEditorMode('t2_ema99', {
        templateId: 'T2',
        title: title,
        registryVersion: ver,
        registryHash: hash,
        idea: idea,
        missing: strategy.missing || [],
        params: defaultEma99Params()
      });
    } else {
      // 旧模板：保留通用表单参数，但写入真实模板身份，禁止默默挂 T1
      setEditorMode('t1_generic', {
        templateId: strategy.id,
        title: title,
        registryVersion: ver,
        registryHash: hash,
        showSteps: false
      });
      if (q('#guidedDraft')) q('#guidedDraft').hidden = false;
      if (q('#strategySteps')) q('#strategySteps').hidden = true;
      qa('[data-step-view]').forEach(function (v) { v.hidden = true; });
    }
  }


  

  function downloadJson(filename, obj) {
    var blob = new Blob([JSON.stringify(obj, null, 2)], { type: 'application/json;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = filename || 'strategy-export.json';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  function exportVersion(versionId) {
    return api('/api/strategies/versions/' + versionId + '/export', { method: 'POST', body: {} }).then(function (pkg) {
      var ver = (pkg && pkg.version) || {};
      var name = (ver.strategy_id || 'strategy') + '@' + (ver.version || versionId) + '-export.json';
      downloadJson(name, pkg);
      toast('已导出 ' + name + ' · joint ' + shortHash(pkg.joint_hash || (pkg.joint_fingerprint && pkg.joint_fingerprint.joint_hash)));
      return pkg;
    }).catch(function (err) {
      toast('导出失败：' + err.message);
    });
  }

  function importVersionPackage(pkg) {
    if (!pkg || typeof pkg !== 'object') return toast('导入包无效');
    return api('/api/strategies/import', { method: 'POST', body: pkg }).then(function (v) {
      toast('已导入 ' + (v.strategy_id || '') + '@' + (v.version || '') + ' · 指纹 ' + shortHash(v.content_hash));
      return refresh();
    }).catch(function (err) {
      var detail = err.message;
      if (err.status === 409 && err.payload) {
        detail = err.payload.error || err.payload.message || detail;
      }
      toast('导入拒绝：' + detail);
    });
  }

  function readImportText() {
    var ta = q('#strategyImportJson');
    return ta ? String(ta.value || '').trim() : '';
  }

  function doImportFromTextarea() {
    var raw = readImportText();
    if (!raw) return toast('先粘贴导出 JSON，或选本地文件');
    var pkg;
    try { pkg = JSON.parse(raw); }
    catch (e) { return toast('JSON 解析失败：' + e.message); }
    return importVersionPackage(pkg);
  }

  function doImportFromFile(file) {
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function () {
      var raw = String(reader.result || '');
      var ta = q('#strategyImportJson');
      if (ta) ta.value = raw;
      try {
        importVersionPackage(JSON.parse(raw));
      } catch (e) {
        toast('文件不是合法 JSON：' + e.message);
      }
    };
    reader.onerror = function () { toast('读文件失败'); };
    reader.readAsText(file, 'utf-8');
  }

  function loadExportFingerprint() {
    var target=q('#strategyExportFp');if(target&&!target.getClientRects().length)return Promise.resolve();
    return api('/api/strategies/bindings/export-fingerprint').then(function (fp) {
      var el = q('#strategyExportFp');
      if (!el) return fp;
      el.textContent = '导出指纹 schema ' + String(fp.schema || fp.fingerprint_schema || '—')
        + ' · joint ' + shortHash(fp.joint_hash || (fp.joint_fingerprint && fp.joint_fingerprint.joint_hash))
        + ' · content ' + shortHash(fp.content_hash)
        + (fp.ok === false ? (' · 不可用：' + String(fp.reason || fp.error || '')) : ' · 可读');
      return fp;
    }).catch(function (err) {
      var el = q('#strategyExportFp');
      if (el) el.textContent = '导出指纹加载失败：' + err.message;
    });
  }
var state = { drafts: [], versions: [], registry: null, selectedDraft: null };

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
          var err = new Error((data && (data.error || data.enable_block_reason)) || ('HTTP ' + res.status));
          err.status = res.status;
          err.payload = data;
          throw err;
        }
        return data;
      });
    });
  }

  function readEditorForm() {
    ensureIdentityFields();
    ensureEmaModeForm();
    var panel = q('[data-strategy-view="editor"]');
    if (!panel) return null;
    var fields = qa('.field', panel);
    function fieldByLabel(label) {
      for (var i = 0; i < fields.length; i++) {
        var span = fields[i].querySelector('span');
        if (span && span.textContent.trim() === label) {
          return fields[i].querySelector('input, textarea, select');
        }
      }
      return null;
    }
    var titleEl = fieldByLabel('策略名称');
    var ideaEl = fieldByLabel('策略想法 / 来源报告');
    var oiEl = fieldByLabel('OI / 市值大于（%）');
    var ratioEl = fieldByLabel('账户多空比小于');
    var fundingEl = fieldByLabel('费率年化小于（%，可设加分项）');
    var entryEl = fieldByLabel('入场触发');
    var exitEl = fieldByLabel('初始止损');
    var versionEl = fieldByLabel('新版本名称');
    var changeEl = fieldByLabel('变更说明');
    var wlTrig = q('#strategyWlTrigger');
    var wlExit = q('#strategyWlExit');
    var depsSel = q('#strategyDeps');
    var deps = [];
    if (depsSel) {
      qa('option:checked', depsSel).forEach(function (o) { deps.push(o.value); });
    }
    var mode = editorMode();
    var templateId = (q('#strategyTemplateId') && q('#strategyTemplateId').value) || '';
    var title = (titleEl && titleEl.value.trim()) || '未命名策略';
    if (mode === 't2_ema99') {
      title = (titleEl && titleEl.value.trim()) || 'EMA三线金叉';
      if (q('#emaFieldIdea') && q('#emaFieldIdea').value) {
        ideaEl = q('#emaFieldIdea');
      }
      if (!deps.length) {
        deps = ['price_4h', 'snapshot', 'oi', 'account_ratio', 'ema'];
      }
    } else if (!deps.length) {
      deps = ['price_4h', 'snapshot'];
    }

    var params;
    var entryText;
    var exitText;
    var idea = (ideaEl && ideaEl.value) || '';
    if (mode === 't2_ema99') {
      params = readEma99ParamsFromForm();
      entryText = 'entry_rule=' + params.entry_rule + '；候选' + params.candidate_max_days + '天；放量×' + params.vol_confirm_ratio;
      exitText = 'initial_stop=' + params.initial_stop + '；trail=' + params.trail_activation + '；intrabar_stop=' + params.intrabar_stop;
      // 禁止把通用表单三旧字段塞进 T2
      LEGACY_T1_PARAM_KEYS.forEach(function (k) { if (k in params) delete params[k]; });
    } else {
      params = {
        oiCapPct: oiEl ? Number(oiEl.value) : null,
        accountRatio: ratioEl ? Number(ratioEl.value) : null,
        fundingAprPct: fundingEl ? Number(fundingEl.value) : null,
      };
      entryText = (entryEl && (entryEl.value || entryEl.options[entryEl.selectedIndex].text)) || '';
      exitText = (exitEl && (exitEl.value || exitEl.options[exitEl.selectedIndex].text)) || '';
    }

    return {
      title: title,
      versionLabel: (versionEl && versionEl.value.trim()) || '',
      changeNote: (changeEl && changeEl.value.trim()) || '',
      rules: {
        idea: idea,
        entry_text: entryText,
        exit_text: exitText,
        base_template_id: templateId || (mode === 't2_ema99' ? 'T2' : undefined),
        registry_version: (q('#strategyRegistryVersion') && q('#strategyRegistryVersion').value) || undefined,
        registry_hash: (q('#strategyRegistryHash') && q('#strategyRegistryHash').value) || undefined,
        editor_mode: mode,
        implementation_status: {
          executable: false,
          requires: ['冻结策略版本', '行情依赖检查', '执行模式检查'],
        },
        params: params,
        whitelist_trigger: {
          mode: (wlTrig && q('select', wlTrig) && q('select', wlTrig).value) || 'unset',
          symbols: ((wlTrig && q('input', wlTrig) && q('input', wlTrig).value) || '')
            .split(/[,，\s]+/).map(function (s) { return s.trim(); }).filter(Boolean),
        },
        whitelist_exit: {
          mode: (wlExit && q('select', wlExit) && q('select', wlExit).value) || 'unset',
          symbols: ((wlExit && q('input', wlExit) && q('input', wlExit).value) || '')
            .split(/[,，\s]+/).map(function (s) { return s.trim(); }).filter(Boolean),
        },
      },
      data_dependencies: deps,
      strategy_id: (q('#strategyIdInput') && q('#strategyIdInput').value.trim()) || undefined,
      draft_id: (q('#strategyDraftId') && q('#strategyDraftId').value) || null,
    };
  }

  function renderList() {
    var box = q('#strategyStoreList');
    if (!box) return;
    var reg = state.registry;
    var regLine = reg
      ? ('共用注册表 ' + esc(reg.version) + ' · hash ' + esc(shortHash(reg.hash)))
      : '注册表未加载';
    var draftsHtml = state.drafts.length ? state.drafts.map(function (d) {
      return '<article class="card" data-draft="' + esc(d.draft_id) + '">'
        + '<span class="eyebrow">草案 · ' + esc(d.strategy_id) + '</span>'
        + '<h3>' + esc(d.title) + '</h3>'
        + '<p>内容指纹 ' + esc(shortHash(d.content_hash)) + ' · 更新 ' + esc(d.updated_at || '') + '</p>'
        + '<span class="pill gray">未冻结可改</span>'
        + '<div class="row" style="margin-top:8px">'
        + '<button type="button" data-act="load-draft" data-id="' + esc(d.draft_id) + '">载入编辑</button>'
        + '<button type="button" class="primary" data-act="freeze" data-id="' + esc(d.draft_id) + '">冻结成版本</button>'
        + '</div></article>';
    }).join('') : '<p class="muted">还没有已存草案。可在「策略编辑」填写后点保存。</p>';

    var versHtml = state.versions.length ? state.versions.map(function (v) {
      var en = v.enabled
        ? '<span class="pill">已启用（仅可引用，无自动下单）</span>'
        : '<span class="pill amber">未启用</span>';
      var miss = (v.missing_dependencies && v.missing_dependencies.length)
        ? '<p class="muted">缺依赖：' + esc(v.missing_dependencies.join('、')) + '</p>' : '';
      return '<article class="card" data-version="' + esc(v.version_id) + '">'
        + '<span class="eyebrow">' + esc(v.strategy_id) + ' @ ' + esc(v.version) + '</span>'
        + '<h3>' + esc(v.title) + '</h3>'
        + '<p>指纹 ' + esc(shortHash(v.content_hash)) + ' · ' + esc(v.id_version_hash) + '</p>'
        + '<p class="muted">冻结于 ' + esc(v.frozen_at || '') + '</p>'
        + en + miss
        + '<div class="row" style="margin-top:8px">'
        + '<button type="button" data-act="enable" data-id="' + esc(v.version_id) + '" data-on="' + (v.enabled ? '0' : '1') + '">'
        + (v.enabled ? '停用' : '尝试启用') + '</button>'
        + '<button type="button" data-act="export" data-id="' + esc(v.version_id) + '">导出包</button>'
        + '<button type="button" data-act="fork" data-id="' + esc(v.version_id) + '">改规则→新草案</button>'
        + '</div></article>';
    }).join('') : '<p class="muted">尚无冻结版本。冻结后 ID/version/hash 可供市场、报告、篮子、实验引用。</p>';

    box.innerHTML = '<div class="note">' + regLine
      + ' · 草案可改；冻结后不可改写；导出带联合指纹；导入会重算校验。启用仅引用，不会自动下单。</div>'
      + '<div class="grid2" style="margin-top:12px"><div><h3>草案</h3><div class="grid1">' + draftsHtml
      + '</div></div><div><h3>冻结版本</h3><div class="grid1">' + versHtml + '</div></div></div>'
      + '<div class="panel" style="margin-top:12px" id="strategyImportPanel">'
      + '<div class="panel-head"><div><h3>导入冻结包</h3><small>粘贴或选择此前「导出包」JSON；篡改 rules 保留旧指纹会被拒绝（409）</small></div></div>'
      + '<p id="strategyExportFp" class="muted">导出指纹未加载</p>'
      + '<textarea id="strategyImportJson" rows="6" style="width:100%;font-family:ui-monospace,monospace;font-size:12px" placeholder="粘贴 strategy-export-v1 导出 JSON"></textarea>'
      + '<div class="row" style="margin-top:8px;gap:8px;flex-wrap:wrap">'
      + '<button type="button" class="primary" data-act="import-text">导入 JSON</button>'
      + '<label class="buttonish" style="display:inline-flex;align-items:center;gap:6px;cursor:pointer">选择文件'
      + '<input type="file" id="strategyImportFile" accept="application/json,.json" style="display:none"></label>'
      + '<button type="button" data-act="load-export-fp">刷新导出指纹</button>'
      + '<button type="button" data-act="refresh">刷新列表</button>'
      + '</div></div>';
    var tests = document.createElement('details');
    tests.className='panel test-versions';
    tests.innerHTML='<summary>测试 / 验收草案与冻结版本（保留所有账本及实验引用）</summary><div class="grid2"></div>';
    var testCount=0;
    [['[data-draft]',state.drafts,'draft_id','draft'],['[data-version]',state.versions,'version_id','version']].forEach(function(spec){
      var target=document.createElement('div');target.className='grid1';
      box.querySelectorAll(spec[0]).forEach(function(card){var item=spec[1].find(function(x){return x[spec[2]]===card.dataset[spec[3]];});if(item&&/^S-(smoke|probe)/i.test(item.strategy_id||'')){target.appendChild(card);testCount++;}});
      tests.querySelector('.grid2').appendChild(target);
    });
    if(testCount){tests.querySelector('summary').append(' · '+testCount+'份');q('#strategyImportPanel',box).before(tests);}
    box.querySelectorAll('.grid1').forEach(function(group){if(!group.children.length)group.innerHTML='<p class="muted">暂无正式条目；测试版本见下方折叠区。</p>';});
    var fileInput = q('#strategyImportFile');
    if (fileInput && !fileInput._wired) {
      fileInput._wired = true;
      fileInput.addEventListener('change', function () {
        var f = fileInput.files && fileInput.files[0];
        if (f) doImportFromFile(f);
        fileInput.value = '';
      });
    }
    loadExportFingerprint();
  }

  
  // --- RULE-01 bindings deepen ---
  var bindState = { bindings: null, versions: [] };
  function ensureBindingsPanel() {
    var host = q('#strategyRuntimePanel') || q('[data-view="strategies"]');
    if (!host || q('#ruleBindingsPanel')) return;
    var panel = document.createElement('div');
    panel.className = 'panel';
    panel.id = 'ruleBindingsPanel';
    panel.innerHTML =
      '<div class="panel-head"><div><h2>跨页统一引用（RULE-01 加深）</h2>' +
      '<small>市场 / 报告 / 篮子 / 实验 / 验证 · 同一 strategy_id + version + content_hash + registry_hash</small></div>' +
      '<span class="pill gray">仅引用</span></div>' +
      '<p id="bindStatus" class="muted">加载绑定…</p>' +
      '<div class="row" style="gap:8px;flex-wrap:wrap">' +
      '<label>冻结版本 <select id="bindVersion"></select></label>' +
      '<button type="button" class="primary" data-bind="bind-all">五页对齐到此版本</button>' +
      '<button type="button" data-bind="refresh">刷新一致性</button>' +
      '</div>' +
      '<div class="tablewrap" style="margin-top:8px"><table><thead><tr>' +
      '<th>页面</th><th>状态</th><th>策略</th><th>指纹</th></tr></thead>' +
      '<tbody id="bindBody"></tbody></table></div>' +
      '<p class="muted">指纹不一致会拒绝绑定；对齐后各页应读同一 unified_key。不自动下单。</p>';
    host.insertBefore(panel, host.firstChild);
    panel.addEventListener('click', function (ev) {
      var btn = ev.target.closest('[data-bind]');
      if (!btn) return;
      var act = btn.getAttribute('data-bind');
      if (act === 'refresh') refreshBindings();
      if (act === 'bind-all') bindAllSurfaces();
    });
  }
  function shortH(h) { return String(h || '').slice(0, 12); }
  function renderBindings() {
    var b = bindState.bindings;
    var body = q('#bindBody');
    var st = q('#bindStatus');
    if (st && b) {
      st.textContent = (b.unified ? '已全部对齐 · ' : '') + (b.note || '') +
        (b.active_unified_key ? (' · key ' + shortH(b.active_unified_key.split('|')[2] || b.active_unified_key)) : '');
    }
    var sel = q('#bindVersion');
    if (sel) {
      var keep = sel.value;
      var vers = bindState.versions || [];
      if (!vers.length) sel.innerHTML = '<option value="">（无冻结版本）</option>';
      else {
        sel.innerHTML = vers.map(function (v) {
          return '<option value="' + esc(v.version_id) + '">' + esc((v.title || v.strategy_id) + ' @' + v.version + (v.enabled ? ' ·启用' : '')) + '</option>';
        }).join('');
        if (keep) sel.value = keep;
      }
    }
    if (!body || !b) return;
    var order = b.surface_order || Object.keys(b.surfaces || {});
    body.innerHTML = order.map(function (sid) {
      var s = (b.surfaces || {})[sid] || {};
      var ref = s.ref || {};
      var status = s.broken ? '失效' : (s.bound ? (s.drift_fields && s.drift_fields.length ? '漂移' : '已绑定') : '未绑定');
      return '<tr><td>' + esc(s.title || sid) + '</td><td>' + esc(status) + '</td><td>' +
        esc(ref.strategy_id ? (ref.strategy_id + '@' + ref.version) : '—') + '</td><td>' +
        esc(ref.content_hash ? shortH(ref.content_hash) : '—') + '</td></tr>';
    }).join('');
  }
  
  function loadCoverageGateNote(versionId) {
    if (!versionId) return;
    api('/api/strategies/coverage-gate?version_id=' + encodeURIComponent(versionId)).then(function (g) {
      var el = q('#bindStatus');
      if (!el) return;
      el.textContent = (g.ok ? '覆盖硬闸：通过' : ('覆盖硬闸：拒绝 ' + (g.missing_dependencies || []).join(',')))
        + ' · 不宣称ATH/六指标齐全';
    }).catch(function () {});
  }

  function refreshBindings() {
    ensureBindingsPanel();
    return Promise.all([
      api('/api/strategies/bindings'),
      api('/api/strategies'),
    ]).then(function (pair) {
      bindState.bindings = pair[0];
      bindState.versions = (pair[1].versions || []);
      renderBindings();
      var vid = (q("#bindVersion") || {}).value;
      if (vid) loadCoverageGateNote(vid);
    }).catch(function (err) {
      var st = q('#bindStatus');
      if (st) st.textContent = '绑定加载失败：' + err.message;
    });
  }
  function bindAllSurfaces() {
    var vid = (q('#bindVersion') || {}).value;
    if (!vid) { toast('请先选冻结版本'); return; }
    api('/api/strategies/bindings/bind-all', { method: 'POST', body: { version_id: vid } })
      .then(function (b) {
        bindState.bindings = b;
        renderBindings();
        toast(b.unified ? '五页已对齐同一指纹' : (b.note || '已提交绑定'));
      })
      .catch(function (err) { toast('对齐失败：' + err.message); });
  }

  function refresh() {
    return api('/api/strategies').then(function (data) {
      state.drafts = data.drafts || [];
      state.versions = data.versions || [];
      state.registry = data.registry || null;
      renderList();
      var st = q('#strategyStoreStatus');
      if (st) st.textContent = '已刷新 · 草案 ' + state.drafts.length + ' · 版本 ' + state.versions.length;
    }).catch(function (err) {
      toast('读策略库失败：' + err.message);
    });
  }

  function saveDraft() {
    var form = readEditorForm();
    if (!form) return toast('找不到策略编辑区');
    var payload = {
      title: form.title,
      strategy_id: form.strategy_id,
      rules: form.rules,
      data_dependencies: form.data_dependencies,
    };
    var p;
    if (form.draft_id) {
      p = api('/api/strategies/drafts/' + form.draft_id, { method: 'PUT', body: payload });
    } else {
      p = api('/api/strategies/drafts', { method: 'POST', body: payload });
    }
    return p.then(function (d) {
      if (q('#strategyDraftId')) q('#strategyDraftId').value = d.draft_id;
      if (q('#strategyIdInput')) q('#strategyIdInput').value = d.strategy_id;
      state.selectedDraft = d.draft_id;
      toast('草案已保存 · ' + d.draft_id + ' · 指纹 ' + shortHash(d.content_hash));
      return refresh();
    }).catch(function (err) {
      toast('保存失败：' + err.message);
    });
  }

  function freezeDraft(draftId) {
    var form = readEditorForm() || {};
    var body = {};
    if (form.versionLabel) body.version = form.versionLabel;
    if (form.changeNote) body.change_note = form.changeNote;
    return api('/api/strategies/drafts/' + draftId + '/freeze', { method: 'POST', body: body }).then(function (v) {
      toast('已冻结 ' + v.strategy_id + '@' + v.version + ' · ' + shortHash(v.content_hash));
      return refresh();
    }).catch(function (err) {
      toast('冻结失败：' + err.message);
    });
  }

  function enableVersion(versionId, on) {
    return api('/api/strategies/versions/' + versionId + '/enable', {
      method: 'POST',
      body: { enabled: !!on },
    }).then(function (v) {
      toast(v.enabled ? '已启用（仅引用，无自动交易）' : '已停用');
      return refresh();
    }).catch(function (err) {
      if (err.status === 409 && err.payload) {
        var miss = (err.payload.missing_dependencies || []).join('、') || err.message;
        toast('覆盖硬闸禁止启用：' + miss);
        return refresh();
      }
      toast('启停失败：' + err.message);
    });
  }

  function forkVersion(versionId) {
    return api('/api/strategies/versions/' + versionId + '/fork', { method: 'POST', body: {} }).then(function (d) {
      if (q('#strategyDraftId')) q('#strategyDraftId').value = d.draft_id;
      if (q('#strategyIdInput')) q('#strategyIdInput').value = d.strategy_id;
      toast('已从冻结版开出新草案 ' + d.draft_id + '（不会改旧版）');
      // switch to editor tab if available
      var btn = q('[data-st="editor"]');
      if (btn) btn.click();
      return refresh();
    }).catch(function (err) {
      toast('开新草案失败：' + err.message);
    });
  }

  function loadDraft(draftId) {
    return api('/api/strategies/drafts/' + draftId).then(function (d) {
      if (q('#strategyDraftId')) q('#strategyDraftId').value = d.draft_id;
      if (q('#strategyIdInput')) q('#strategyIdInput').value = d.strategy_id;
      var rules = d.rules || {};
      var params = rules.params || {};
      var isEma = rules.base_template_id === 'T2'
        || rules.editor_mode === 't2_ema99'
        || params.execution_mode === 'ema99_close_trail'
        || params.execution_mode === 'ema99';

      var panel = q('[data-strategy-view="editor"]');
      var fields = panel ? qa('.field', panel) : [];
      function setByLabel(label, val) {
        for (var i = 0; i < fields.length; i++) {
          var span = fields[i].querySelector('span');
          if (span && span.textContent.trim() === label) {
            var inp = fields[i].querySelector('input, textarea, select');
            if (inp && val != null) inp.value = val;
            return;
          }
        }
      }

      if (isEma) {
        setEditorMode('t2_ema99', {
          templateId: rules.base_template_id || 'T2',
          title: d.title || 'EMA三线金叉',
          registryVersion: rules.registry_version || '',
          registryHash: rules.registry_hash || '',
          idea: rules.idea || '',
          missing: (rules.implementation_status && rules.implementation_status.unfinished) || [],
          params: Object.assign(defaultEma99Params(), params)
        });
        if (q('#guidedDraft')) q('#guidedDraft').hidden = false;
        if (q('#guidedDraftTitle')) q('#guidedDraftTitle').textContent = (rules.base_template_id || 'T2') + ' ' + (d.title || 'EMA三线金叉');
        if (q('#guidedDraftBody')) q('#guidedDraftBody').textContent = rules.idea || '';
      } else {
        setEditorMode('t1_generic', {
          templateId: rules.base_template_id || '',
          title: d.title,
          registryVersion: rules.registry_version || '',
          registryHash: rules.registry_hash || '',
          showSteps: true
        });
        if (q('#guidedDraft')) q('#guidedDraft').hidden = true;
        if (q('#strategySteps')) {
          q('#strategySteps').hidden = false;
          qa('#strategySteps [data-step]').forEach(function (b) {
            b.hidden = false;
            b.classList.toggle('active', b.getAttribute('data-step') === '1');
          });
        }
        qa('[data-step-view]').forEach(function (v) {
          v.hidden = v.getAttribute('data-step-view') !== '1';
        });
        setByLabel('策略名称', d.title);
        setByLabel('策略想法 / 来源报告', rules.idea);
        if (params.oiCapPct != null) setByLabel('OI / 市值大于（%）', params.oiCapPct);
        if (params.accountRatio != null) setByLabel('账户多空比小于', params.accountRatio);
        if (params.fundingAprPct != null) setByLabel('费率年化小于（%，可设加分项）', params.fundingAprPct);
      }

      // deps
      var depsSel = q('#strategyDeps');
      if (depsSel && Array.isArray(d.data_dependencies)) {
        qa('option', depsSel).forEach(function (o) {
          o.selected = d.data_dependencies.indexOf(o.value) >= 0;
        });
      }

      var btn = q('[data-st="editor"]');
      if (btn) btn.click();
      toast('已载入草案 ' + d.draft_id + (isEma ? ' · EMA99 结构化参数' : ' · 通用参数'));
    }).catch(function (err) {
      toast('载入失败：' + err.message);
    });
  }

  function bind() {
    var root = q('[data-view="strategies"]') || q('#strategyStorePanel');
    if (!root) return;
    root.addEventListener('click', function (ev) {
      var btn = ev.target.closest('[data-act]');
      if (!btn) return;
      var act = btn.getAttribute('data-act');
      var id = btn.getAttribute('data-id');
      if (act === 'refresh') refresh();
      if (act === 'save') saveDraft();
      if (act === 'freeze' && id) freezeDraft(id);
      if (act === 'freeze-current') {
        var did = q('#strategyDraftId') && q('#strategyDraftId').value;
        if (!did) return toast('请先保存草案再冻结');
        freezeDraft(did);
      }
      if (act === 'enable' && id) enableVersion(id, btn.getAttribute('data-on') === '1');
      if (act === 'export' && id) exportVersion(id);
      if (act === 'fork' && id) forkVersion(id);
      if (act === 'load-draft' && id) loadDraft(id);
      if (act === 'import-text') doImportFromTextarea();
      if (act === 'load-export-fp') loadExportFingerprint();
    });

    // Wire legacy demo save button if present
    qa('button').forEach(function (b) {
      if (b.getAttribute('data-demo') && b.textContent.indexOf('保存草案') >= 0) {
        b.removeAttribute('data-demo');
        b.setAttribute('data-act', 'save');
        b.setAttribute('type', 'button');
        b.textContent = '保存草案';
      }
    });
    var saveRow = q('[data-strategy-view="editor"] .row');
    if (saveRow && !q('#strategySaveReal', saveRow)) {
      var freezeBtn = document.createElement('button');
      freezeBtn.className = 'primary';
      freezeBtn.type = 'button';
      freezeBtn.id = 'strategySaveReal';
      freezeBtn.setAttribute('data-act', 'freeze-current');
      freezeBtn.textContent = '冻结当前草案';
      saveRow.insertBefore(freezeBtn, saveRow.firstChild);
      var saveBtn = document.createElement('button');
      saveBtn.type = 'button';
      saveBtn.setAttribute('data-act', 'save');
      saveBtn.id = 'strategyDraftSaveReal';
      saveBtn.textContent = '保存草案到本地';
      saveRow.insertBefore(saveBtn, freezeBtn);
      qa('[data-strategy-view="editor"] button').forEach(function(b){if(b.id!=='strategyDraftSaveReal'&&b.id!=='strategySaveReal'&&(b.getAttribute('data-act')==='save'||(b.hasAttribute('data-demo')&&/冻结/.test(b.textContent)))){b.hidden=true;b.disabled=true;}});
    }
  }

  function boot() {
    if (!q('[data-view="strategies"]')) return;
    bind();
    ensureIdentityFields();
    ensureEmaModeForm();
    refresh();
    var fpVisible=new IntersectionObserver(function(entries){if(entries.some(function(e){return e.isIntersecting;})){fpVisible.disconnect();loadExportFingerprint();}});fpVisible.observe(q('[data-view="strategies"]'));
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
  window.StrategyStoreUI = { refresh: refresh, saveDraft: saveDraft, loadDraft: loadDraft, applyTemplate: applyTemplate, setEditorMode: setEditorMode, defaultEma99Params: defaultEma99Params, readEditorForm: readEditorForm, exportVersion: exportVersion, importVersionPackage: importVersionPackage };
})();
