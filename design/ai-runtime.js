/** Wire settings → AI API panel to real /api/ai/* endpoints. */
(function () {
  function q(sel, root) { return (root || document).querySelector(sel); }
  function toast(msg) {
    if (typeof window.toast === "function") return window.toast(msg);
    console.log(msg);
  }
  async function api(path, method, body) {
    const opts = { method: method || "GET", headers: { Accept: "application/json" }, credentials: "same-origin" };
    if (method && method !== "GET") {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body == null ? {} : body);
    }
    const res = await fetch(path, opts);
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_) { data = { error: text }; }
    if (!res.ok) throw new Error((data && data.error) || ("HTTP " + res.status));
    return data;
  }
  function statusLabel(cap) {
    const map = { success: "成功", failed: "失败", unsupported: "不支持", unverified: "未验证" };
    if (!cap) return "未验证";
    const t = cap.tested_at ? (" · " + cap.tested_at) : "";
    return (map[cap.status] || cap.status) + t;
  }
  function ensureModelSelect() {
    const modelInput = q("#modelId");
    if (!modelInput || q("#modelSelect")) return;
    const select = document.createElement("select");
    select.id = "modelSelect";
    select.style.marginBottom = "8px";
    select.innerHTML = "<option value=\"\">选择已知模型版本…</option>";
    modelInput.parentNode.insertBefore(select, modelInput);
    select.onchange = () => {
      if (select.value) modelInput.value = select.value;
    };
    modelInput.placeholder = "也可手填准确模型ID，例如 gpt-6.1-sol";
  }
  function fillModels(models, selected) {
    ensureModelSelect();
    const select = q("#modelSelect");
    if (!select) return;
    const cur = selected || (q("#modelId") && q("#modelId").value) || "";
    select.innerHTML = "<option value=\"\">选择已知模型版本…</option>" +
      (models || []).map(m => {
        const mark = m.recommended ? " · 推荐" : (m.source === "provider" ? " · 服务商" : "");
        return `<option value="${m.id}">${m.label || m.id}${mark}</option>`;
      }).join("");
    if (cur) {
      select.value = cur;
      if (select.value !== cur) {
        const opt = document.createElement("option");
        opt.value = cur; opt.textContent = cur + " · 当前";
        select.appendChild(opt);
        select.value = cur;
      }
    }
  }
  function render(config) {
    ensureModelSelect();
    const pill = q('[data-setting-view="ai"] .pill');
    if (pill) {
      pill.textContent = config.keyConfigured ? "已配置Key" : "未配置";
      pill.className = "pill " + (config.keyConfigured ? "" : "gray");
    }
    if (q("#provider")) q("#provider").value = config.provider || "apikey";
    if (q("#protocol")) {
      const preferred = config.protocol || (config.provider === "apikey" ? "responses" : "chat");
      q("#protocol").value = preferred;
      [...q("#protocol").options].forEach(opt => {
        const implemented = (config.implementedProtocols || ["chat", "responses"]).includes(opt.value);
        opt.disabled = !implemented;
        if (!implemented && !/未实现/.test(opt.textContent)) opt.textContent += "（未实现）";
      });
    }
    if (q("#baseUrl")) q("#baseUrl").value = config.baseUrl || "";
    if (q("#modelId")) q("#modelId").value = config.modelId || "";
    fillModels(config.knownModels || [], config.modelId || "");
    if (q("#apiKey")) {
      q("#apiKey").value = "";
      q("#apiKey").placeholder = config.keyConfigured ? "已保存（留空保留；输入则替换）" : "保存后由本地后端DPAPI保护，不回显";
    }
    const status = q("#apiStatus");
    if (status) {
      const c = config.capabilities || {};
      status.textContent = [
        "配置ID：" + (config.configId || "—"),
        "版本：v" + config.version + (config.updated_at ? " · " + config.updated_at : ""),
        "鉴权：" + statusLabel(c.auth),
        "模型：" + statusLabel(c.model),
        "结构化：" + statusLabel(c.schema),
        "检索：" + statusLabel(c.retrieval),
        "APIKEY.FAN常用模型ID：gpt-6.1-sol / gpt-6-astra / gpt-6-sol（对应界面 6.1 Sol / 6 Astra / 6 Sol）",
        "建议协议：Responses；保存与测试分开，失败不会标已接通。"
      ].join("\n");
    }
    if (q("#aiTimeout") && config.timeoutSec != null) q("#aiTimeout").value = String(config.timeoutSec);
    if (q("#aiRetries") && config.maxRetries != null) q("#aiRetries").value = String(config.maxRetries);
    const tasks = config.tasks || {};
    if (q("#aiTaskResearch") && typeof tasks.research === "boolean") q("#aiTaskResearch").checked = tasks.research;
    if (q("#aiTaskDraft") && typeof tasks.draft === "boolean") q("#aiTaskDraft").checked = tasks.draft;
    if (q("#aiTaskReview") && typeof tasks.review === "boolean") q("#aiTaskReview").checked = tasks.review;
    if (typeof window.updateApiPaths === "function") window.updateApiPaths();
    const headerAI = q('.topline .row .pill');
    if (headerAI) headerAI.textContent = config.keyConfigured ? 'AI已配置 · ' + (config.modelId || '未选模型') : 'AI未配置';
    window.__aiConfig = config;
  }
  async function load() {
    const config = await api("/api/ai/config");
    render(config);
    await loadResearchPrompts();
    return config;
  }
  let promptConfigHash = null;
  let savedPromptBase = null;
  let researchBackendReady = false;
  async function loadResearchPrompts() {
    researchBackendReady = false;
    const data = await api("/api/research/prompts");
    const catalog = data.catalog;
    promptConfigHash = data.meta && data.meta.content_hash;
    savedPromptBase = catalog.version;
    const origin = q("#depthPromptLabel");
    if (origin) origin.textContent = "标准投研模板（基于 " + (catalog.profiles.standard.base_version || catalog.version) + "）";
    if (q("#depthPrompt")) {
      q("#depthPrompt").value = catalog.profiles.standard.instruction;
      q("#depthPrompt").dispatchEvent(new Event("input", { bubbles: true }));
    }
    if (q("#commonPrompt")) q("#commonPrompt").value = catalog.common;
    const save = q("#savePrompt");
    if (save) save.onclick = async function () {
      if (!promptConfigHash) { toast("提示词配置尚未加载，无法保存"); return; }
      save.disabled = true;
      try {
        const out = await api("/api/config/prompt-edits", "PUT", {overrides: {standard: q("#depthPrompt").value},
          base_version: savedPromptBase, expected_content_hash: promptConfigHash});
        promptConfigHash = out.meta.content_hash;
        invalidateResearchPreview();
        toast("标准提示词已保存；后续投研使用该版本");
      } catch (e) { toast(String(e.message || e) + "；未覆盖，请刷新后核对"); }
      finally { save.disabled = false; }
    };
    const cap = await api("/api/research/capability");
    researchBackendReady = cap.adapter === "model_native_web_search";
    const label = q("#researchRetrievalStatus");
    if (label) label.textContent = cap.configured ? " · 沿用当前模型联网检索；来源随报告保存，接口兼容性待验" : " · 未配置模型或Key，暂不可提交联网投研";
  }
  function invalidateResearchPreview() {
    pendingResearch = null;
    setConfirmEnabled(false);
    const status = q('#aiResearchStatus');
    if (status) status.textContent = '输入已变更，请重新预览后确认';
  }
  async function loadModels() {
    const data = await api("/api/ai/models");
    fillModels(data.models || [], q("#modelId") && q("#modelId").value);
    if (data.error) toast("已显示预设模型；拉取服务商列表失败：" + data.error);
    else toast("已更新模型列表（含预设与服务商返回）");
    return data;
  }
  function currentPayload(keyAction) {
    const cfg = window.__aiConfig;
    if (!cfg) throw new Error("尚未读取AI配置（请确认已重启服务）");
    const apiKey = (q("#apiKey") && q("#apiKey").value) || "";
    let action = keyAction;
    if (!action) action = apiKey.trim() ? "replace" : "keep";
    const modelFromSelect = q("#modelSelect") && q("#modelSelect").value;
    const modelId = ((q("#modelId") && q("#modelId").value) || modelFromSelect || "").trim();
    return {
      version: cfg.version,
      keyAction: action,
      apiKey: action === "replace" ? apiKey.trim() : undefined,
      config: {
        provider: q("#provider").value,
        protocol: q("#protocol").value,
        baseUrl: q("#baseUrl").value.trim(),
        modelId,
        timeoutSec: Number((q("#aiTimeout") && q("#aiTimeout").value) || 90),
        maxRetries: Number((q("#aiRetries") && q("#aiRetries").value) || 2),
        tasks: {
          research: !!(q("#aiTaskResearch") ? q("#aiTaskResearch").checked : true),
          draft: !!(q("#aiTaskDraft") ? q("#aiTaskDraft").checked : true),
          review: !!(q("#aiTaskReview") ? q("#aiTaskReview").checked : true)
        }
      }
    };
  }
  async function save() {
    const cfg = window.__aiConfig;
    const typed = (q("#apiKey") && q("#apiKey").value || "").trim();
    if (!cfg) throw new Error("尚未读取AI配置，请刷新页面");
    if (!cfg.keyConfigured && !typed) throw new Error("还没有保存过API Key，请先填写Key再点保存");
    if (cfg.keyConfigured && !typed) {
      // keep existing key
    }
    const payload = currentPayload(typed ? "replace" : "keep");
    if (payload.provider === "apikey" || (payload.config && payload.config.provider === "apikey")) {
      // prefer Responses for APIKEY.FAN unless user chose chat
    }
    if (!payload.config.modelId) throw new Error("请先选择或填写模型ID，例如 gpt-6.1-sol");
    if (payload.config.provider === "apikey" && payload.config.protocol === "chat") {
      // keep user choice; hint only
    }
    const saved = await api("/api/ai/config", "PUT", payload);
    render(saved);
    toast(saved.keyConfigured ? "AI配置已保存（Key已保护存储）" : "配置已保存，但Key仍未配置");
  }
  async function clearKey() {
    const payload = currentPayload("clear");
    delete payload.apiKey;
    const saved = await api("/api/ai/config", "PUT", payload);
    render(saved);
    toast("已清除本地API Key");
  }
  async function testConnection() {
    toast("将发送一次可能计费的小模型请求…");
    const result = await api("/api/ai/test/connection", "POST", {});
    render(await api("/api/ai/config"));
    toast(result.ok ? "连接测试成功" : ("连接测试未通过：" + (result.detail || result.status)));
  }
  async function testCapabilities() {
    const result = await api("/api/ai/test/capabilities", "POST", { checks: ["model", "schema", "retrieval"] });
    render(await api("/api/ai/config"));
    toast("能力验证完成");
    return result;
  }
  function bind() {
    const panel = q('[data-setting-view="ai"]');
    if (!panel || panel.dataset.aiBound === "1") return;
    panel.dataset.aiBound = "1";
    ensureModelSelect();
    const buttons = [...panel.querySelectorAll("button")];
    const saveBtn = q("#saveAiConfig") || buttons.find(b => /保存配置/.test(b.textContent));
    const testBtn = q("#testApi") || buttons.find(b => /测试/.test(b.textContent));
    const clearBtn = q("#clearKey") || buttons.find(b => /清空|清除/.test(b.textContent));
    if (saveBtn) saveBtn.onclick = () => save().catch(e => toast(String(e.message || e)));
    if (testBtn) testBtn.onclick = () => testConnection().catch(e => toast(String(e.message || e)));
    if (clearBtn) clearBtn.onclick = () => clearKey().catch(e => toast(String(e.message || e)));
    const loadModelsBtn = q("#loadProviderModels");
    if (loadModelsBtn) {
      loadModelsBtn.textContent = "获取可用模型";
      loadModelsBtn.onclick = () => loadModels().catch(e => toast(String(e.message || e)));
    }
    const researchBtn = q("#runAiResearch");
    if (researchBtn) researchBtn.onclick = () => previewResearchFlow({ mode: "snapshot" }).catch(e => toast(String(e.message || e)));
    const scanResearchBtn = q("#runScanAndResearch");
    if (scanResearchBtn) scanResearchBtn.onclick = () => previewResearchFlow({ mode: "scan_and_research" }).catch(e => toast(String(e.message || e)));
    const previewBtn = q("#previewRequest");
    if (previewBtn) {
      const prevClick = previewBtn.onclick;
      previewBtn.onclick = () => {
        if (typeof prevClick === "function") {
          try { prevClick(); } catch (_) {}
        }
        previewResearchFlow({ mode: "snapshot", localPreviewOnly: false }).catch(e => toast(String(e.message || e)));
      };
    }
    const confirmBtn = q("#confirmAiResearch");
    if (confirmBtn) confirmBtn.onclick = () => confirmPendingResearch().catch(e => toast(String(e.message || e)));
    const cancelBtn = q("#cancelAiResearch");
    if (cancelBtn) cancelBtn.onclick = () => cancelActiveResearch().catch(e => toast(String(e.message || e)));
    setConfirmEnabled(false);
    ["#depthPrompt", "#promptFocus", "#useSearch"].forEach(sel => {
      const el = q(sel); if (el) el.addEventListener("input", invalidateResearchPreview);
    });
    const reset = q("#resetPrompt");
    if (reset) reset.addEventListener("click", invalidateResearchPreview);
    refreshResearchStatus();
    if (q("#provider")) {
      const prev = q("#provider").onchange;
      q("#provider").onchange = function () {
        if (typeof prev === "function") prev.apply(this, arguments);
        const v = q("#provider").value;
        if (v === "apikey") {
          q("#baseUrl").value = "https://api.apikey.fan/v1";
          q("#protocol").value = "responses";
        }
        load().catch(() => {});
      };
    }
  }
  async function refreshResearchStatus() {
    const el = q('#aiResearchStatus');
    if (!el) return;
    try {
      const config = window.__aiConfig || await api('/api/ai/config');
      if (!config.keyConfigured) el.textContent = 'AI未配置Key · 本地数据报告仍可用';
      else if (!config.modelId) el.textContent = '已配置Key，请选择模型后再投研';
      else el.textContent = 'AI已配置 · ' + config.modelId + ' · ' + (config.protocol || '');
      if(q('[data-view="reports"]')?.getClientRects().length)fetchCoverageTip().then(paintCoverageTip).catch(function () {});
    } catch (e) { el.textContent = 'AI接口不可用（需重启服务）'; }
  }
  window.addEventListener("workbench-experience-selection", invalidateResearchPreview);
  let pendingResearch = null;
  let activeResearchTaskId = null;
  let researchBusy = false;

  function setConfirmEnabled(on) {
    const confirmBtn = q("#confirmAiResearch");
    const cancelBtn = q("#cancelAiResearch");
    if (confirmBtn) confirmBtn.disabled = !on;
    if (cancelBtn) cancelBtn.disabled = !(on || !!activeResearchTaskId);
  }

  function researchSignatureOf(body) {
    return JSON.stringify({
      mode: body.mode,
      unified_context: body.unified_context,
      depth: body.depth,
      scope: body.scope,
      contracts: (body.contracts || []).map(function (c) {
        return [c.market, c.symbol || c.pair, c.asset];
      }),
      depth_template: body.depth_template || "",
      user_focus: body.user_focus || "",
      requested_search: !!body.requested_search,
      experiences: body.experiences || [],
      prompt_version: body.prompt_version || ""
    });
  }

  function collectResearchContracts(opts) {
    opts = opts || {};
    if (Array.isArray(opts.contracts) && opts.contracts.length) {
      return opts.contracts.map(function (c) {
        return { market: c.market, symbol: c.symbol || c.pair, asset: c.asset };
      });
    }
    if (typeof window.selectedResearchContracts === "function") {
      const rows = window.selectedResearchContracts() || [];
      if (rows.length) {
        return rows.map(function (c) {
          return { market: c.market, symbol: c.symbol || c.pair, asset: c.asset };
        });
      }
    }
    // 仅当用户明确选择「当前筛选」且允许顶 N 时才回退；默认禁止静默顶 20
    if (opts.allowFilteredTop && typeof window.analysisRows === "function") {
      return analysisRows().map(function (r) {
        return { market: r.market, symbol: r.symbol || r.pair, asset: r.asset };
      });
    }
    return [];
  }

  function buildResearchBody(opts) {
    if (!researchBackendReady) throw new Error("服务尚未加载本轮投研功能，请重新加载服务后刷新；本次未建任务");
    opts = opts || {};
    const mode = opts.mode || "snapshot";
    const depth = "standard";
    const scopeEl = document.querySelector("#reportScope button.active");
    const scope = opts.scope || (scopeEl && scopeEl.getAttribute("data-scope")) || "selected";
    const contracts = collectResearchContracts(opts);
    if (!contracts.length) {
      throw new Error("请先勾选或指定合约；不会再静默用筛选前 20 份顶替");
    }
    const depthPromptEl = document.querySelector("#depthPrompt");
    const focusEl = document.querySelector("#promptFocus");
    const searchEl = document.querySelector("#useSearch");
    const promptVerEl = document.querySelector("#promptVersion");
    const prompt_version = (typeof window.promptVersion === "function")
      ? window.promptVersion()
      : ((promptVerEl && (promptVerEl.textContent || promptVerEl.value)) || "").trim();
    return {
      mode: mode,
      unified_context: mode === "snapshot" && window.UnifiedWorkbench ? window.UnifiedWorkbench.researchContext() : undefined,
      depth: depth,
      scope: scope,
      contracts: contracts,
      depth_template: depthPromptEl ? String(depthPromptEl.value || "") : "",
      user_focus: focusEl ? String(focusEl.value || "") : "",
      requested_search: !!(searchEl && searchEl.checked),
      experiences: window.ExperienceRuntime ? window.ExperienceRuntime.selected() : [],
      prompt_version: prompt_version
    };
  }


  async function fetchCoverageTip() {
    try {
      const rep = await api("/api/coverage?candle_limit=40");
      const agg = (rep && rep.aggregate) || {};
      const gaps = (rep && rep.gaps) || [];
      const claims = (rep && rep.claims) || {};
      const gapTitles = gaps.slice(0, 3).map(function (g) { return g.title || g.field_id; }).filter(Boolean);
      let tip = "覆盖：" + (agg.status || "未知");
      if (rep.gap_count != null) tip += " · 缺口字段 " + rep.gap_count;
      if (gapTitles.length) tip += "（" + gapTitles.join("、") + (gaps.length > 3 ? "…" : "") + "）";
      tip += " · 不宣称历史齐全";
      if (claims.oi_history_complete === false || claims.market_cap_history_complete === false) {
        tip += " · OI/市值历史未齐";
      }
      return { tip: tip, aggregate: agg.status, gap_count: rep.gap_count || 0 };
    } catch (e) {
      return { tip: "覆盖提示暂不可用（" + (e.message || e) + "）", aggregate: null, gap_count: null };
    }
  }

  function paintCoverageTip(tipObj) {
    let el = q("#researchCoverageTip");
    if (!el) {
      const host = q("#aiResearchStatus") || q("#researchSummary");
      if (!host || !host.parentNode) return;
      el = document.createElement("p");
      el.id = "researchCoverageTip";
      el.className = "muted";
      el.style.marginTop = "6px";
      host.parentNode.insertBefore(el, host.nextSibling);
    }
    el.textContent = (tipObj && tipObj.tip) ? tipObj.tip : "";
  }

  function renderPreviewStatus(preview, body, covTip) {
    const el = q("#aiResearchStatus") || q("#researchSummary");
    if (!el) return;
    const sid = preview.submission_id ? String(preview.submission_id) : "";
    const ih = preview.intent_hash ? String(preview.intent_hash) : "";
    const tmpl = preview.body_template === "B" || (preview.schema_version && String(preview.schema_version).indexOf("template-b") >= 0)
      ? " · 模板B八节"
      : "";
    const migr = preview.prompt_migration && preview.prompt_migration.action
      ? (" · 提示词:" + preview.prompt_migration.action)
      : "";
    el.textContent = "预览待确认：" + (preview.selected_count || body.contracts.length) + "份 · "
      + (preview.modelId || "") + " · " + (preview.depth || body.depth)
      + (body.mode === "scan_and_research" ? " · 先扫描再投研" : " · 当前快照")
      + tmpl + migr
      + " · 已选经验 " + ((preview.experiences || []).length) + " 条"
      + (preview.cache_hit ? (" · 将复用缓存 " + (preview.cached_report_id || "")) : " · 将发起新调用")
      + (preview.missing_symbols && preview.missing_symbols.length ? (" · 快照缺" + preview.missing_symbols.length + "个") : "")
      + (sid ? (" · sub " + sid.slice(0, 12) + "…") : "")
      + (ih ? (" · intent " + ih.slice(0, 10) + "…") : "")
      + " · 计划模型请求 " + (preview.planned_model_calls == null ? "未知" : preview.planned_model_calls) + " 次（含联网检索；工具内部搜索次数/费用另按服务商）"
      + " · 点「确认提交」才会真正调用";
    if (covTip) paintCoverageTip(covTip);
  }

  async function previewResearchFlow(opts) {
    opts = opts || {};
    if (researchBusy) {
      toast("已有投研在进行，先取消或等完成");
      return null;
    }
    const body = buildResearchBody(opts);
    if (body.mode === "scan_and_research") {
      toast("预览：确认后会先完整扫描再投研；扫描失败不会改用旧快照");
    }
    const preview = await api("/api/reports/preview", "POST", body);
    const covTip = await fetchCoverageTip();
    pendingResearch = {
      body: body,
      preview: preview,
      submission_id: preview.submission_id || null,
      intent_hash: preview.intent_hash || null,
      signature: researchSignatureOf(body),
      at: Date.now(),
      coverage: covTip
    };
    renderPreviewStatus(preview, body, covTip);
    setConfirmEnabled(preview.can_confirm !== false);
    toast("已生成正式预览，请核对覆盖提示后点「确认提交」；未确认不会计费调用");
    return preview;
  }

  async function confirmPendingResearch() {
    if (!pendingResearch || !pendingResearch.body) {
      throw new Error("没有待确认的预览，请先点投研或预览");
    }
    // 按页面现选对象/深度重算；变了就作废预览
    let fresh;
    try {
      fresh = buildResearchBody({ mode: pendingResearch.body.mode });
    } catch (e) {
      pendingResearch = null;
      setConfirmEnabled(false);
      throw e;
    }
    if (researchSignatureOf(fresh) !== pendingResearch.signature) {
      pendingResearch = null;
      setConfirmEnabled(false);
      throw new Error("预览后对象/深度/提示词/关注点/检索选项已变，请重新预览");
    }
    pendingResearch.body = fresh;
    if (researchBusy) throw new Error("投研进行中，请勿重复提交");
    researchBusy = true;
    setConfirmEnabled(false);
    const body = Object.assign({}, pendingResearch.body);
    const preview = pendingResearch.preview || {};
    if (preview.can_confirm === false) { researchBusy = false; throw new Error("当前检索渠道不可用，请重新预览"); }
    // R1 residual / R2a: preview → confirm must round-trip submission_id + intent_hash
    if (preview.submission_id) body.submission_id = preview.submission_id;
    if (preview.intent_hash) {
      body.preview_intent_hash = preview.intent_hash;
      body.intent_hash = preview.intent_hash;
    }
    try {
      if (body.mode === "scan_and_research") {
        toast("已确认：提交扫描并投研（最多" + body.contracts.length + "份）…");
      } else {
        toast(preview.cache_hit ? "已确认：将复用已有报告缓存" : ("已确认：提交投研（最多" + body.contracts.length + "份）…"));
      }
      const result = await api("/api/reports", "POST", body);
      pendingResearch = null;
      toast(result.reused ? "复用在途任务" : ("任务已创建 " + (result.task && result.task.id)));
      if (result.task) {
        activeResearchTaskId = result.task.id;
        setConfirmEnabled(false);
        const cancelBtn = q("#cancelAiResearch");
        if (cancelBtn) cancelBtn.disabled = false;
        // Submission is brief; long running work belongs to its own board row.
        const taskId = result.task.id;
        if (window.ReportRuntime) window.ReportRuntime.trackTask(result.task, '投研任务');
        pollReport(taskId, { mode: body.mode }).catch(e => toast('任务状态读取失败，可刷新任务记录：' + e.message));
      }
      return result;
    } finally {
      researchBusy = false;
      const cancelBtn = q("#cancelAiResearch");
      if (cancelBtn) cancelBtn.disabled = !activeResearchTaskId;
    }
  }

  async function cancelActiveResearch() {
    if (pendingResearch) {
      pendingResearch = null;
      setConfirmEnabled(false);
      const el = q("#aiResearchStatus");
      if (el) el.textContent = "已取消待确认预览";
      toast("已取消预览，未提交");
      return { cancelled: "preview" };
    }
    if (!activeResearchTaskId) throw new Error("没有可取消的投研任务");
    const taskId = activeResearchTaskId;
    const out = await api("/api/tasks/" + taskId + "/cancel", "POST", {});
    toast("已请求取消 " + taskId);
    const el = q("#aiResearchStatus");
    if (el) el.textContent = "已请求取消 " + taskId;
    return out;
  }

  // 兼容旧调用：默认只预览；opts.confirm===true 时预览并立即确认（自动化用）
  async function runResearch(opts) {
    opts = opts || {};
    const preview = await previewResearchFlow(opts);
    if (opts.confirm === true) {
      return confirmPendingResearch();
    }
    return preview;
  }


  function retrievalBriefFromReport(rep) {
    if (!rep || !rep.retrieval || typeof rep.retrieval !== "object") return "";
    const ret = rep.retrieval;
    const req = rep.request || {};
    const requested = (rep.requested_search != null) ? !!rep.requested_search : !!req.requested_search;
    const map = {
      success: "成功", partial_success: "部分成功", failed: "失败",
      disabled: "未启用", not_configured: "未配置", unsupported: "不支持", unverified: "未验证"
    };
    const st = map[ret.status] || ret.status || "—";
    const ids = Array.isArray(ret.allowed_evidence_ids) ? ret.allowed_evidence_ids : [];
    const cov = ret.coverage || {};
    const n = (cov.item_count != null) ? Number(cov.item_count) : ids.length;
    const gaps = (cov.gap_count != null) ? Number(cov.gap_count) : (Array.isArray(ret.gaps) ? ret.gaps.length : 0);
    const online = !!(ret.online_verified || (rep.claims && rep.claims.retrieval_online_verified));
    let s = "检索：" + st;
    s += " · 请求联网：" + (requested ? "是" : "否");
    s += " · 证据 " + n + " 条";
    if (gaps) s += " · 缺口 " + gaps;
    s += online ? " · 已在线核验" : " · 未在线核验";
    if (ret.attempt_id) s += " · 尝试 " + String(ret.attempt_id).slice(0, 24) + (String(ret.attempt_id).length > 24 ? "…" : "");
    return s;
  }

  async function pollReport(taskId, opts) {

    opts = opts || {};
    const maxRounds = opts.mode === 'scan_and_research' ? 600 : 120;
    for (let i = 0; i < maxRounds; i++) {
      await new Promise(r => setTimeout(r, 1500));
      const task = await api('/api/tasks/' + taskId);
      const el = !pendingResearch && activeResearchTaskId === taskId ? q('#aiResearchStatus') : null;
      const label = (task.kind === 'scan_and_research') ? '扫描并投研' : '投研任务';
      if (window.ReportRuntime && window.ReportRuntime.trackTask) window.ReportRuntime.trackTask(task, label);
      if (el) el.textContent = label + ' ' + task.status + (task.progress && task.progress.phase ? (' · ' + task.progress.phase) : '');
      if (['success', 'partial_success', 'failed', 'cancelled'].includes(task.status)) {
        if (task.status === 'failed') {
          toast((task.kind === 'scan_and_research' ? '扫描并投研失败：' : '投研失败：') + (task.error || ''));
          if (window.ReportRuntime && window.ReportRuntime.loadList) await window.ReportRuntime.loadList();
        }
        else {
          toast(task.kind === 'scan_and_research' ? '扫描并投研完成' : '投研完成');
          if (task.result && task.result.report_id) {
            const rep = await api('/api/reports/' + task.result.report_id);
            const brief = retrievalBriefFromReport(rep);
            if (el && brief) el.textContent = label + ' ' + task.status + ' · ' + brief;
            if (window.ReportRuntime && window.ReportRuntime.notifyReady) {
              // CON-06: do not switch away from a report the user is reading
              await window.ReportRuntime.notifyReady(task.result.report_id, rep.title || label);
            } else if (window.ReportRuntime && window.ReportRuntime.loadList) {
              await window.ReportRuntime.loadList(task.result.report_id);
            } else {
              const out = q('#generatedReport');
              if (out) {
                out.className = 'report-body';
                out.textContent = typeof rep.content === 'string' ? rep.content : JSON.stringify(rep, null, 2);
              }
            }
          }
        }
        if (activeResearchTaskId === taskId) { activeResearchTaskId = null; if (!pendingResearch) setConfirmEnabled(false); }
        return task;
      }
    }
    toast('任务仍在进行，可稍后在任务记录查看');
  }
  const researchVisible=new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)){researchVisible.disconnect();refreshResearchStatus();}});if(q('[data-view="reports"]'))researchVisible.observe(q('[data-view="reports"]'));
  window.AiRuntime = { load, save, testConnection, testCapabilities, loadModels, render, bind, runResearch, previewResearchFlow, confirmPendingResearch, cancelActiveResearch, refreshResearchStatus, previewResearch: async function(body){ return api("/api/reports/preview","POST",body||{}); } };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => { bind(); load().catch(e => toast("AI配置未接通：" + e.message + "（请重启 start-pages）")); });
  else { bind(); load().catch(e => toast("AI配置未接通：" + e.message + "（请重启 start-pages）")); }
})();
