/** Report list + detail UI (local data reports vs AI reports). */
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
  function escape(value) {
    return String(value ?? "—").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function time(value) {
    if (!value) return "—";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return d.toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
  }
  function symbolsOf(item) {
    if (!item) return [];
    if (Array.isArray(item.symbols) && item.symbols.length) return item.symbols.filter(Boolean);
    const contracts = item.contracts || (item.request && item.request.contracts) || [];
    return contracts.map(c => (typeof c === "string" ? c : (c && (c.symbol || c.pair)))).filter(Boolean);
  }
  function displayTitle(item) {
    const src = (item && item.source) || (item && item.modelId ? "ai" : "local");
    const kind = src === "ai" ? "投研报告" : "数据报告";
    const syms = symbolsOf(item);
    if (!syms.length) return kind;
    if (syms.length === 1) return syms[0] + kind;
    return syms[0] + "等" + syms.length + "份" + kind;
  }
  function fmtVal(v) {
    if (v == null || v === "") return "—";
    if (typeof v === "number") {
      if (!Number.isFinite(v)) return "—";
      const abs = Math.abs(v);
      if (abs >= 1000) return v.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
      if (abs >= 1) return String(Math.round(v * 1e4) / 1e4);
      return String(Math.round(v * 1e6) / 1e6);
    }
    if (typeof v === "boolean") return v ? "是" : "否";
    if (typeof v === "object") {
      try { return JSON.stringify(v); } catch (_) { return String(v); }
    }
    return String(v);
  }
  function listHtml(items, mapFn) {
    const arr = (items || []).map(mapFn).filter(Boolean);
    if (!arr.length) return '<p class="muted">无</p>';
    return "<ul style='margin:6px 0 0;padding-left:1.2em'>" + arr.map(x => "<li style='margin:4px 0'>" + x + "</li>").join("") + "</ul>";
  }
  function section(title, body) {
    if (!body) return "";
    return '<section style="margin:0 0 16px;padding:12px 14px;border:1px solid var(--line,#e5e7eb);border-radius:10px;background:#fff">'
      + "<h3 style='margin:0 0 8px;font-size:15px'>" + escape(title) + "</h3>" + body + "</section>";
  }
  // R2a / RES-13: template B eight sections (UI + export consistency)
  const TEMPLATE_B_SECTIONS = ["核心催化剂", "价格与市场", "风险与机会", "技术面", "合约结构", "基本面", "支撑压力", "综合判断"];
  function isTemplateBReport(report, research) {
    const sv = (report && report.schema_version) || (research && research.schema_version) || "";
    if ((report && report.body_template) === "B" || (research && research.body_template) === "B") return true;
    if (String(sv).indexOf("template-b") >= 0 || String(sv).indexOf("research-report-v2") === 0) return true;
    if (research && research.sections && typeof research.sections === "object") return true;
    return false;
  }
  function sectionBodyFromValue(v) {
    if (v == null || v === "") return "";
    if (typeof v === "string") return '<div class="report-md">' + renderMarkdownLite(v) + "</div>";
    if (Array.isArray(v)) return listHtml(v, x => (typeof x === "string" ? escape(x) : escape(fmtVal(x))));
    if (typeof v === "object") {
      if (v.markdown || v.text || v.body) return sectionBodyFromValue(v.markdown || v.text || v.body);
      return factBody(v);
    }
    return escape(fmtVal(v));
  }
  function renderTemplateBSections(sectionsObj) {
    if (!sectionsObj || typeof sectionsObj !== "object") return "";
    return TEMPLATE_B_SECTIONS.map(title => {
      const raw = sectionsObj[title];
      const body = sectionBodyFromValue(raw);
      if (!body) return section(title, '<p class="muted">本节无内容</p>');
      return section(title, body);
    }).join("");
  }
  function renderConstraintAttachments(obj) {
    if (!obj || typeof obj !== "object") return "";
    const bits = [];
    const six = obj.six_indicators || obj.six_indicators_detail || (obj.attachments && obj.attachments.six_indicators_detail);
    if (six) {
      bits.push(section("六指标（满足/不满足/未知）", sectionBodyFromValue(six)));
    }
    const states = obj.constraint_states || (obj.attachments && obj.attachments.constraint_states);
    if (states) bits.push(section("约束三态", sectionBodyFromValue(states)));
    const sources = obj.sources;
    if (Array.isArray(sources) && sources.length) {
      bits.push(section("来源与运行记录", listHtml(sources, s => {
        if (!s) return "";
        const id = s.evidence_id || s.id || "";
        const url = s.source_url || s.url || "";
        const times = [s.published_at, s.retrieved_at, s.available_at].filter(Boolean).join(" / ");
        return "<strong>" + escape(fmtVal(id)) + "</strong>"
          + (url ? " · " + escape(url) : "")
          + (times ? "<div class='muted' style='font-size:12px'>" + escape(times) + "</div>" : "");
      })));
    }
    return bits.join("");
  }
  function splitMarkdownBSections(text) {
    const src = String(text || "").replace(/\r\n/g, "\n");
    const re = /^(?:#{1,3}\s*)?(?:\d+\.\s*)?(核心催化剂|价格与市场|风险与机会|技术面|合约结构|基本面|支撑压力|综合判断)\s*$/gm;
    const hits = [];
    let m;
    while ((m = re.exec(src))) hits.push({ title: m[1], index: m.index, end: m.index + m[0].length });
    if (hits.length < 5) return null;
    const map = {};
    for (let i = 0; i < hits.length; i++) {
      const start = hits[i].end;
      const stop = i + 1 < hits.length ? hits[i + 1].index : src.length;
      map[hits[i].title] = src.slice(start, stop).trim();
    }
    return map;
  }
  function renderMarkdownAsB(text) {
    const map = splitMarkdownBSections(text);
    if (!map) return '<div class="report-md">' + renderMarkdownLite(text) + "</div>";
    // Keep leading title/price block above sections
    const firstIdx = String(text).search(/^(?:#{1,3}\s*)?(?:\d+\.\s*)?(?:核心催化剂)/m);
    const head = firstIdx > 0 ? String(text).slice(0, firstIdx).trim() : "";
    return (head ? '<div class="report-md" style="margin-bottom:12px">' + renderMarkdownLite(head) + "</div>" : "")
      + renderTemplateBSections(map);
  }
  function factBody(f) {
    if (f == null) return "";
    if (typeof f !== "object") return escape(fmtVal(f));
    if (Array.isArray(f)) return escape(f.map(fmtVal).join("、"));
    const keys = Object.keys(f);
    if (!keys.length) return "";
    return "<div style='display:grid;grid-template-columns:auto 1fr;gap:4px 12px;font-size:13px'>"
      + keys.map(k => "<span class='muted'>" + escape(k) + "</span><span>" + escape(fmtVal(f[k])) + "</span>").join("")
      + "</div>";
  }
  function renderContractReadable(c) {
    if (!c || typeof c !== "object") return "<div class='muted'>无合约正文</div>";
    const parts = [];
    if (c.sections && typeof c.sections === "object") {
      parts.push(renderTemplateBSections(c.sections));
      parts.push(renderConstraintAttachments(c));
      // Still keep strategy / state if present (RES-13 attachments)
      if (c.state) {
        parts.push(section("结论状态", (
          "<p><strong>" + escape(c.state.label || "—") + "</strong>"
          + (c.state.confirmed ? " · 已确认" : " · 未确认")
          + "</p><p>" + escape(c.state.basis || "") + "</p>"
        )));
      }
      const stratB = c.strategy_suggestions_with_dependency_ids || c.strategy_suggestions || [];
      if (stratB.length) {
        parts.push(section("策略建议", stratB.map(s => (
          "<div style='margin:0 0 12px;padding:10px;background:#f8fafc;border-radius:8px'>"
          + "<div><strong>" + escape(s.id || "建议") + "</strong> · " + escape(s.type || "") + "</div>"
          + "<p>" + escape(s.suggestion || "") + "</p></div>"
        )).join("")));
      }
      return parts.filter(Boolean).join("") || "<div class='muted'>这份合约没有可展示字段</div>";
    }
    parts.push(section("结论状态", c.state ? (
      "<p><strong>" + escape(c.state.label || "—") + "</strong>"
      + (c.state.confirmed ? " · 已确认" : " · 未确认")
      + "</p><p>" + escape(c.state.basis || "") + "</p>"
      + (c.state.invalidated != null ? "<p class='muted'>失效：" + escape(fmtVal(c.state.invalidated)) + "</p>" : "")
    ) : null));

    const facts = c.facts_with_evidence_ids || [];
    if (facts.length) {
      parts.push(section("关键事实", facts.map(f => {
        let body = "";
        if (f.fact != null) body += factBody(f.fact);
        if (Array.isArray(f.items) && f.items.length) {
          body += "<div style='margin-top:6px'>" + f.items.map(it => (
            "<div style='margin:4px 0;padding:6px 8px;background:#f8fafc;border-radius:6px'>"
            + "<strong>#" + escape(fmtVal(it.index)) + "</strong> · " + escape(it.status || "—")
            + (it.fact ? " — " + escape(fmtVal(it.fact)) : "")
            + (it.definition_and_threshold ? "<div class='muted' style='font-size:12px'>口径：" + escape(it.definition_and_threshold) + "</div>" : "")
            + (it.counterevidence_or_limit ? "<div class='muted' style='font-size:12px'>限制：" + escape(it.counterevidence_or_limit) + "</div>" : "")
            + "</div>"
          )).join("") + "</div>";
        }
        return "<div style='margin:0 0 10px'><div><strong>" + escape(f.topic || "事实") + "</strong></div>"
          + body
          + (f.scope ? "<p class='muted' style='margin:6px 0 0;font-size:12px'>" + escape(f.scope) + "</p>" : "")
          + "</div>";
      }).join("")));
    }

    parts.push(section("假设", listHtml(c.hypotheses, h => {
      if (!h) return "";
      let s = escape(h.hypothesis || h.claim || "");
      if (h.required_test) s += "<div class='muted' style='font-size:12px'>待检验：" + escape(h.required_test) + "</div>";
      if (Array.isArray(h.competing_explanations) && h.competing_explanations.length) {
        s += "<div class='muted' style='font-size:12px'>其他解释：" + escape(h.competing_explanations.join("；")) + "</div>";
      }
      if (h.status) s += "<div class='muted' style='font-size:12px'>状态：" + escape(h.status) + "</div>";
      return s;
    })));

    parts.push(section("反证 / 限制", listHtml(c.counterevidence, x => escape((x && (x.finding || x)) || ""))));

    parts.push(section("未知项", listHtml(c.unknowns, u => {
      if (!u) return "";
      const fields = Array.isArray(u.fields) ? u.fields.join("、") : (u.fields || "");
      const src = Array.isArray(u.suggested_sources) ? u.suggested_sources.join("、") : (u.suggested_sources || "");
      return escape(fields) + (src ? "<div class='muted' style='font-size:12px'>可查：" + escape(src) + "</div>" : "");
    })));

    parts.push(section("确认条件", listHtml(c.confirmation_conditions, x => {
      if (!x) return "";
      return escape(x.condition || x) + (x.role ? "<div class='muted' style='font-size:12px'>" + escape(x.role) + "</div>" : "");
    })));

    parts.push(section("失效条件", listHtml(c.invalidation_conditions, x => escape((x && (x.condition || x)) || ""))));

    const strat = c.strategy_suggestions_with_dependency_ids || c.strategy_suggestions || [];
    if (strat.length) {
      parts.push(section("策略建议", strat.map(s => (
        "<div style='margin:0 0 12px;padding:10px;background:#f8fafc;border-radius:8px'>"
        + "<div><strong>" + escape(s.id || "建议") + "</strong> · " + escape(s.type || "") + "</div>"
        + "<p>" + escape(s.suggestion || "") + "</p>"
        + (s.candidate_basis ? "<p class='muted' style='font-size:12px'>依据：" + escape(s.candidate_basis) + "</p>" : "")
        + (s.subsequent_entry_trigger ? "<p class='muted' style='font-size:12px'>后续触发：" + escape(s.subsequent_entry_trigger) + "</p>" : "")
        + (Array.isArray(s.confirmation) && s.confirmation.length ? "<p style='font-size:13px'>确认：" + escape(s.confirmation.join("；")) + "</p>" : "")
        + (s.exit ? "<p style='font-size:13px'>退出：" + escape(s.exit) + "</p>" : "")
        + (Array.isArray(s.risks) && s.risks.length ? "<p class='muted' style='font-size:12px'>风险：" + escape(s.risks.join("；")) + "</p>" : "")
        + (s.validity ? "<p class='muted' style='font-size:12px'>有效期：" + escape(s.validity) + "</p>" : "")
        + "</div>"
      )).join("")));
    }

    return parts.filter(Boolean).join("") || "<div class='muted'>这份合约没有可展示字段</div>";
  }
  function renderBasket(basket) {
    if (!basket || typeof basket !== "object") return "";
    const bits = [];
    if (basket.asset_overlap && basket.asset_overlap.finding) {
      bits.push("<p>" + escape(basket.asset_overlap.finding) + "</p>");
    }
    if (Array.isArray(basket.shared_risks) && basket.shared_risks.length) {
      bits.push("<p><strong>共同风险</strong></p>" + listHtml(basket.shared_risks, r => escape(r.finding || r)));
    }
    if (Array.isArray(basket.research_priority) && basket.research_priority.length) {
      bits.push("<p><strong>研究优先级</strong></p>" + basket.research_priority.map(p => (
        "<div style='margin:6px 0'><strong>" + escape(p.symbol || "") + "</strong> · " + escape(p.priority || "")
        + (Array.isArray(p.sequence) ? listHtml(p.sequence, s => escape(s)) : "")
        + "</div>"
      )).join(""));
    }
    return bits.length ? section("篮子小结", bits.join("")) : "";
  }
  function renderMarkdownLite(text) { return ReportFormat.markdown(text); }
  function tryParseResearch(content) {
    if (typeof content !== "string") return null;
    const t = content.trim();
    if (!(t.startsWith("{") && t.endsWith("}"))) return null;
    try {
      const obj = JSON.parse(t);
      if (obj && (obj.schema_version || obj.contracts || obj.sections || obj.body_template)) return obj;
    } catch (_) {}
    return null;
  }
  function looksMarkdown(text) { return ReportFormat.looksMarkdown(text); }

  let reports = [];
  let currentId = null;
  let currentReport = null;
  let activeSymbol = null;

  function ensureShell() {
    const panel = q("#reportOutput");
    if (!panel || panel.dataset.reportShell === "1") return panel;
    panel.dataset.reportShell = "1";
    const head = q(".panel-head", panel);
    if (head) {
      const small = q("small", head);
      if (small) small.textContent = "左侧选报告，右侧看详情。";
    }
    const old = q("#generatedReport");
    const wrap = document.createElement("div");
    wrap.className = "report-shell";
    wrap.style.cssText = "display:grid;grid-template-columns:minmax(220px,280px) 1fr;gap:16px;align-items:start;";
    wrap.innerHTML = [
      '<div class="report-list-panel">',
      '<div id="reportTaskBoard" class="report-task-board" style="display:none;margin-bottom:10px"></div>',
      '<div class="row" style="justify-content:space-between;margin-bottom:8px"><strong>报告列表</strong>',
      '<button type="button" id="refreshReportList">刷新</button></div>',
      '<div id="reportList" class="report-list" style="display:flex;flex-direction:column;gap:8px;max-height:70vh;overflow:auto"></div></div>',
      '<div class="report-detail-panel">',
      '<div id="reportReadyNotice" class="note" style="display:none;margin-bottom:8px"></div>',
      '<div id="reportMeta" class="note" style="margin-bottom:10px">尚未选择报告</div>',
      '<div id="reportContractTabs" class="row" style="flex-wrap:wrap;gap:6px;margin-bottom:10px"></div>',
      '<div id="generatedReport" class="empty"><strong>先选择或生成报告</strong><p>生成数据报告或完成AI投研后，会出现在左侧列表。</p></div>',
      '</div>'
    ].join("");
    if (old) old.replaceWith(wrap);
    else panel.appendChild(wrap);
    q("#refreshReportList").onclick = () => loadList().catch(e => toast(String(e.message || e)));
    return panel;
  }

  function renderList() {
    ensureShell();
    const box = q("#reportList");
    if (!box) return;
    if (!reports.length) {
      box.innerHTML = '<div class="muted">暂无已保存报告</div>';
      return;
    }
    box.innerHTML = reports.map(item => {
      const active = item.report_id === currentId;
      // §11.8：列表只显示「代币名+报告类型」与时间，不堆模型/深度/ID pill
      return '<button type="button" data-report-id="' + escape(item.report_id) + '" style="text-align:left;padding:10px 12px;border:1px solid ' + (active ? "var(--accent,#0f766e)" : "var(--line,#e5e7eb)") + ";border-radius:10px;background:" + (active ? "rgba(15,118,110,.08)" : "#fff") + ';cursor:pointer">'
        + "<strong style='font-size:14px;display:block'>" + escape(displayTitle(item)) + "</strong>"
        + '<div class="muted" style="margin-top:4px;font-size:12px">' + escape(time(item.created_at)) + "</div>"
        + (item.validation_failed ? '<div style="margin-top:4px;color:#b42318;font-size:12px">校验未通过 · 原文保留</div>' : '')
        + (item.error ? ('<div class="muted" style="margin-top:4px;color:#b42318;font-size:12px">失败：' + escape(String(item.error).slice(0, 80)) + "</div>") : "")
        + "</button>";
    }).join("") + "</details>";
    box.onclick = (e) => {
      const btn = e.target.closest("[data-report-id]");
      if (!btn) return;
      userPinned = true; // CON-06: user chose what to read; background completions must not steal focus
      openReport(btn.dataset.reportId).catch(err => toast(String(err.message || err)));
    };
  }

  function isBlankReportContent(content) {
    if (content == null) return true;
    const s = String(content).trim();
    if (!s) return true;
    // 去掉标签与空白后仍空，视为无正文（避免空壳 HTML 装成有内容）
    const text = s.replace(/<[^>]+>/g, " ").replace(/&nbsp;/gi, " ").replace(/\s+/g, "").trim();
    return !text;
  }

  function buildParts(report) {
    const content = report.content || "";
    const format = report.content_format || (report.source === "local" ? "html" : "text");
    const research = (format !== "html") ? tryParseResearch(content) : null;
    if (research) {
      // REV-08: v1 structured still uses renderContractReadable; B uses eight sections
      const attach = renderConstraintAttachments(research);
      if (isTemplateBReport(report, research) && research.sections && typeof research.sections === "object") {
        const contracts = Array.isArray(research.contracts) ? research.contracts : [];
        const head = renderTemplateBSections(research.sections) + attach + renderBasket(research.basket);
        if (!contracts.length) return [{ symbol: "报告正文", html: head }];
        return contracts.map((c, i) => ({
          symbol: (c && (c.symbol || c.pair)) || ("合约" + (i + 1)),
          html: (c && c.sections ? renderContractReadable(c) : (i === 0 ? head : renderContractReadable(c))) + (i === 0 && c && c.sections ? attach + renderBasket(research.basket) : "")
        }));
      }
      const contracts = Array.isArray(research.contracts) ? research.contracts : [];
      if (!contracts.length) {
        // Do not force-parse as B; show raw for unknown shapes
        return [{ symbol: "报告正文", html: "<pre class='pre' style='white-space:pre-wrap'>" + escape(content) + "</pre>" }];
      }
      return contracts.map((c, i) => ({
        symbol: (c && (c.symbol || c.pair)) || ("合约" + (i + 1)),
        html: renderContractReadable(c) + (i === 0 ? renderBasket(research.basket) + attach : "")
      }));
    }
    // Markdown template B → consistent eight section cards
    if (format !== "html" && typeof content === "string" && splitMarkdownBSections(content)) {
      const contracts = report.contracts || (report.request && report.request.contracts) || [];
      const symbols = contracts.map(c => (typeof c === "string" ? c : (c && (c.symbol || c.pair)))).filter(Boolean);
      return [{ symbol: symbols[0] || "报告正文", html: renderMarkdownAsB(content) }];
    }
    const contracts = report.contracts || (report.request && report.request.contracts) || [];
    const symbols = contracts.map(c => (typeof c === "string" ? c : (c.symbol || c.pair))).filter(Boolean);
    if (format === "html" && typeof content === "string" && content.includes("report-item")) {
      const tmp = document.createElement("div");
      tmp.innerHTML = content;
      const items = [...tmp.querySelectorAll(".report-item")];
      if (items.length) {
        return items.map((el, i) => ({
          symbol: symbols[i] || ((el.querySelector("h3,strong,header") || {}).textContent || "").trim() || ("合约" + (i + 1)),
          html: el.outerHTML
        }));
      }
    }
    if (format === "html") {
      if (isBlankReportContent(content)) return [];
      return [{ symbol: symbols[0] || "报告正文", html: content }];
    }
    if (looksMarkdown(content) || symbols.length <= 1) {
      return [{ symbol: symbols[0] || "报告正文", html: '<div class="report-md">' + renderMarkdownLite(content) + "</div>" }];
    }
    const parts = [];
    for (let i = 0; i < symbols.length; i++) {
      const sym = symbols[i];
      const next = symbols[i + 1];
      const esc = sym.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const startRe = new RegExp("(^|\\n)#{1,3}\\s*" + esc + "|\\*\\*" + esc + "\\*\\*");
      const m = content.match(startRe);
      if (!m) {
        parts.push({ symbol: sym, html: i === 0 ? ('<div class="report-md">' + renderMarkdownLite(content) + "</div>") : "" });
        continue;
      }
      let chunk = content.slice(m.index);
      if (next) {
        const esc2 = next.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        const nextRe = new RegExp("(^|\\n)#{1,3}\\s*" + esc2 + "|\\*\\*" + esc2 + "\\*\\*");
        const n = chunk.slice(1).match(nextRe);
        if (n) chunk = chunk.slice(0, n.index + 1);
      }
      parts.push({ symbol: sym, html: '<div class="report-md">' + renderMarkdownLite(chunk.trim()) + "</div>" });
    }
    if (parts.every(p => !p.html) && content) return [{ symbol: "全部", html: '<div class="report-md">' + renderMarkdownLite(content) + "</div>" }];
    return parts.filter(p => p.html);
  }


  // R2b: read-only retrieval / evidence status (never invent online_verified)
  function retrievalStatusLabel(status) {
    const map = {
      success: "成功",
      partial_success: "部分成功",
      failed: "失败",
      disabled: "未启用",
      not_configured: "未配置",
      unsupported: "不支持",
      unverified: "未验证",
      not_run_in_r1: "未运行"
    };
    if (status == null || status === "") return "—";
    return map[status] || String(status);
  }
  function gapSummaryText(gaps) {
    const arr = Array.isArray(gaps) ? gaps : [];
    if (!arr.length) return "";
    const bits = arr.slice(0, 4).map(function (g) {
      if (g == null) return "";
      if (typeof g === "string") return g;
      return g.reason || g.symbol || g.title || g.field_id || g.fetch_status || "";
    }).filter(Boolean);
    if (!bits.length) return "共 " + arr.length + " 项";
    return bits.join("；") + (arr.length > 4 ? "…" : "");
  }
  function formatRetrievalPanel(report) {
    if (!report || typeof report !== "object") return "";
    const ret = report.retrieval;
    if (!ret || typeof ret !== "object") return "";
    const req = report.request || {};
    const requested = (report.requested_search != null)
      ? !!report.requested_search
      : !!req.requested_search;
    const claims = report.claims || {};
    const online = !!(ret.online_verified || claims.retrieval_online_verified);
    const ids = Array.isArray(ret.allowed_evidence_ids) ? ret.allowed_evidence_ids : [];
    const cov = ret.coverage || {};
    const itemCount = (cov.item_count != null) ? Number(cov.item_count) : ids.length;
    const gapCount = (cov.gap_count != null) ? Number(cov.gap_count) : (Array.isArray(ret.gaps) ? ret.gaps.length : 0);
    const gapText = gapSummaryText(ret.gaps);
    const attemptId = ret.attempt_id || "";
    const rows = [];
    rows.push("<div><span class='muted'>是否请求联网检索</span>：" + (requested ? "是" : "否") + "</div>");
    rows.push("<div><span class='muted'>检索状态</span>：" + escape(retrievalStatusLabel(ret.status)) + "</div>");
    rows.push("<div><span class='muted'>外部证据</span>：" + escape(String(itemCount)) + " 条"
      + (gapCount ? (" · 缺口 " + escape(String(gapCount)) + " 项") : "") + "</div>");
    if (gapText) {
      rows.push("<div><span class='muted'>缺口摘要</span>：" + escape(gapText) + "</div>");
    }
    if (online) {
      rows.push("<div><span class='muted'>在线核验</span>：<strong style='color:#067647'>已在线核验</strong></div>");
    } else {
      rows.push("<div><span class='muted'>在线核验</span>：未在线核验</div>");
    }
    if (attemptId) {
      rows.push("<div><span class='muted'>检索尝试编号</span>：<code style='font-size:12px'>" + escape(attemptId) + "</code></div>");
    }
    if (ret.actual_search_performed) rows.push("<div>实际联网检索：已取得工具记录；来源内容待核验</div>");
    const sources = (ret.items || []).filter(item => {
      try { const url = new URL(item.source_url); return ["https:", "http:"].includes(url.protocol) && !url.username; }
      catch (_) { return false; }
    });
    if (sources.length) rows.push("<details><summary>本轮来源（" + sources.length + "）</summary><ul>" + sources.map(item =>
      "<li><a target='_blank' rel='noopener noreferrer' href='" + escape(item.source_url) + "'>" + escape(item.title || item.source_url)
      + "</a> · " + escape(item.content_level || "未知层级") + " · 检索于 " + escape(time(item.retrieved_at)) + "</li>"
    ).join("") + "</ul></details>");
    if (ret.detail) {
      rows.push("<div class='muted' style='margin-top:4px'>说明：" + escape(String(ret.detail)) + "</div>");
    }
    return '<div id="reportRetrievalStatus" class="note" style="margin:10px 0 12px;padding:10px 12px;border:1px solid var(--line,#e5e7eb);border-radius:8px;background:#fafafa">'
      + "<strong style='display:block;margin-bottom:6px'>外部检索与证据</strong>"
      + "<div style='display:grid;gap:4px;font-size:13px;line-height:1.5'>" + rows.join("") + "</div>"
      + "</div>";
  }

  function renderDetail() {
    ensureShell();
    const meta = q("#reportMeta");
    const tabs = q("#reportContractTabs");
    const out = q("#generatedReport");
    if (!currentReport) {
      if (meta) meta.textContent = "尚未选择报告";
      if (tabs) tabs.innerHTML = "";
      if (out) {
        out.className = "empty";
        out.innerHTML = "<strong>先选择或生成报告</strong><p>生成数据报告或完成AI投研后，会出现在左侧列表。</p>";
      }
      return;
    }
    if (meta) {
      // §11.8：详情标题与列表一致——代币名+报告类型，下一行仅时间；模型/协议等不进主标题
      meta.innerHTML = "<div style='display:flex;justify-content:space-between;gap:10px;align-items:flex-start;flex-wrap:wrap'>"
        + "<div><strong style='font-size:16px'>" + escape(displayTitle(currentReport)) + "</strong>"
        + '<div class="muted" style="margin-top:4px">' + escape(time(currentReport.created_at)) + "</div>"
        + (currentReport.error ? ('<div class="muted" style="margin-top:4px;color:#b42318">失败：' + escape(currentReport.error) + "</div>") : "")
        + "</div>"
        + '<div class="row" style="gap:6px">'
        + '<button type="button" id="exportReportMd">导出正文</button>'
        + '<button type="button" id="exportReportJson">导出JSON</button>'
        + "</div></div>";
      const mdBtn = q("#exportReportMd");
      const jsBtn = q("#exportReportJson");
      if (mdBtn) mdBtn.onclick = () => exportCurrent("text");
      if (jsBtn) jsBtn.onclick = () => exportCurrent("json");
      // R2b: append read-only retrieval status under title row
      const retPanel = formatRetrievalPanel(currentReport);
      if (retPanel) meta.insertAdjacentHTML("beforeend", "<details><summary>外部检索与证据明细</summary>"+retPanel+"</details>");
    }
    const parts = buildParts(currentReport);
    const partsBlank = !parts || !parts.length || parts.every(p => isBlankReportContent(p && p.html));
    if (partsBlank && !currentReport.error) {
      if (tabs) tabs.innerHTML = "";
      if (out) {
        const retOnly = formatRetrievalPanel(currentReport);
        if (retOnly) {
          out.className = "report-body";
          out.innerHTML = retOnly + "<div class='empty' style='margin-top:8px'><strong>报告暂无正文</strong><p>请查看任务结果；失败或空返回不会用旧报告顶替。</p></div>";
        } else {
          out.className = "empty";
          out.innerHTML = "<strong>报告暂无正文</strong><p>请查看任务结果；失败或空返回不会用旧报告顶替。</p>";
        }
      }
      return;
    }
    if (!activeSymbol || !parts.some(p => p.symbol === activeSymbol)) activeSymbol = parts[0] && parts[0].symbol;
    if (tabs) {
      if (parts.length <= 1) tabs.innerHTML = "";
      else {
        tabs.innerHTML = parts.map(p => {
          const on = p.symbol === activeSymbol;
          return '<button type="button" data-sym="' + escape(p.symbol) + '" class="' + (on ? "primary" : "") + '" style="padding:6px 10px">' + escape(p.symbol) + "</button>";
        }).join("");
        tabs.onclick = (e) => {
          const b = e.target.closest("[data-sym]");
          if (!b) return;
          activeSymbol = b.dataset.sym;
          renderDetail();
        };
      }
    }
    const part = parts.find(p => p.symbol === activeSymbol) || parts[0];
    if (!out) return;
    out.className = "report-body";
    out.style.cssText = "line-height:1.55;max-width:920px";
    if (currentReport.error) {
      out.className = "note";
      out.innerHTML = "<strong>报告生成失败</strong><p>" + escape(currentReport.error) + "</p>";
    } else if (part && part.html) {
      out.innerHTML = part.html;
    } else if (!currentReport.content) {
      out.className = "empty";
      out.innerHTML = "<strong>正文缺失</strong><p>列表能打开但没有 content；请核对任务是否 partial/failed，或重新生成。</p>";
    } else {
      out.innerHTML = "<div class='muted'>这份报告没有可显示正文（格式无法解析）</div>";
    }
  }

  let detailRequestSequence = 0;
  async function loadList(selectId) {
    ensureShell();
    const requestSequence = detailRequestSequence;
    const data = await api("/api/reports");
    reports = data.reports || [];
    if (requestSequence !== detailRequestSequence) { renderList(); return reports; }
    if (selectId) currentId = selectId;
    else if (currentId && !reports.some(r => r.report_id === currentId)) currentId = null;
    if (!currentId && reports[0]) currentId = reports[0].report_id;
    renderList();
    if (currentId) await openReport(currentId);
    else { currentReport = null; renderDetail(); }
    return reports;
  }

  async function openReport(reportId) {
    currentId = reportId;
    const sequence = ++detailRequestSequence;
    const body=q("#generatedReport");if(body){body.className="empty";body.textContent="正在读取所选报告…";}
    const tabs=q("#reportContractTabs");if(tabs)tabs.innerHTML="";
    const meta=q("#reportMeta");if(meta)meta.textContent="报告加载中…";
    try {
      const report = await api("/api/reports/" + reportId);
      if (sequence !== detailRequestSequence) return null;
      currentReport = report;
    } catch (e) {
      if (sequence !== detailRequestSequence) return null;
      currentReport = {
        report_id: reportId,
        source: "unknown",
        title: "读取失败",
        created_at: null,
        content: "",
        error: String(e.message || e)
      };
      toast("打开报告失败：" + String(e.message || e));
    }
    activeSymbol = null;
    renderList();
    renderDetail();
    renderReviewSummary();
    window.dispatchEvent(new CustomEvent("workbench-report-opened", {detail: currentReport}));
    return currentReport;
  }

  // R6: REV-07 / REV-09 / RES-16 — show cutoffs, automatic vs human checks, revision version separately
  function renderReviewSummary() {
    const meta = q("#reportMeta");
    const r = currentReport;
    if (!meta || !r) return;
    const old = q("#reportReviewSummary");
    if (old) old.remove();
    const lines = [];
    const dc = r.data_cutoffs;
    if (dc) {
      const st = { fresh: "未过期", stale: "已过期", unknown: "未知", as_retrieved: "按检索时间" };
      lines.push(escape(dc.label || "本次知识时点研究"));
      lines.push("行情截至 " + escape(time(dc.market && dc.market.cutoff) || "未知") + "（" + escape(st[dc.market && dc.market.status] || "—") + "）"
        + " · 技术面截至 " + escape(time(dc.technical && dc.technical.cutoff) || "未知") + "（" + escape(st[dc.technical && dc.technical.status] || "—") + "）"
        + " · 新闻检索于 " + escape(time(dc.news && dc.news.retrieved_at) || "未知"));
    }
    const ck = r.checks;
    if (ck) {
      const a = ck.automatic || {};
      const bad = [];
      const it = a.items || {};
      if (it.citations_exist && !it.citations_exist.ok) bad.push("无效引用 " + (it.citations_exist.invalid || []).join("、"));
      if (it.contracts_covered && !it.contracts_covered.ok) bad.push("未覆盖 " + (it.contracts_covered.missing || []).join("、"));
      if (it.strategy_dependencies && !it.strategy_dependencies.ok) bad.push("策略依赖问题 " + (it.strategy_dependencies.issues || []).length + " 处");
      lines.push("自动校验：" + (a.status === "passed" ? "通过" : "未通过") + (bad.length ? "（" + escape(bad.join("；")) + "）" : "")
        + " · 内容核验：待办 · " + escape((ck.human && ck.human.label) || "未人工核验"));
    }
    const review = r.current_rule_review;
    const quality = (review && review.quality) || r.quality;
    if (quality) lines.push("原始版当前规则复核：" + (quality.status === "accepted" ? "程序检查通过（内容待核验）" : "未通过；不能转策略草案")
      + (quality.reasons && quality.reasons.length ? " · " + escape(quality.reasons.join("；")) : ""));
    if (r.usage_total) lines.push("本份报告检索+成文 token：" + (r.usage_total.total_tokens == null ? "未知/部分未知" : escape(r.usage_total.total_tokens)));
    if (r.current_version) lines.push("当前为修订版 " + r.current_version + "（原始模型输出保留；修订见 revisions）");
    if (!lines.length) return;
    const div = document.createElement("div");
    div.id = "reportReviewSummary";
    div.className = "muted";
    div.style.cssText = "font-size:12px;margin-top:6px;line-height:1.6";
    div.innerHTML = "<details><summary>数据时点、质量检查与修订信息</summary>" + lines.join("<br>") + "</details>";
    meta.appendChild(div);
  }

  async function saveLocalFromDom(meta) { /* rule-fp */
    // RULE-01: server stamps fingerprint; unbound → 409. Client may pre-attach via RuleMarketRuntime.
    ensureShell();
    const out = q("#generatedReport");
    const content = (meta && meta.content) || (out && out.innerHTML) || "";
    if (!content || (out && out.classList.contains("empty"))) throw new Error("没有可保存的数据报告内容");
    const body = Object.assign({
      title: "",
      content: content,
      content_format: "html",
      contracts: []
    }, meta || {});
    const saved = await api("/api/reports/local", "POST", body);
    toast("本地数据报告已保存到列表");
    await loadList(saved.report_id);
    return saved;
  }


  function exportCurrent(kind) {
    if (!currentReport) { toast("请先选择报告"); return; }
    const id = currentReport.report_id || "report";
    let blob;
    let name;
    if (kind === "json") {
      const payload = Object.assign({}, currentReport);
      if (!payload.schema_version && payload.body_template === "B") payload.schema_version = "research-report-v2-template-b";
      blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
      name = id + ".json";
    } else {
      let text = typeof currentReport.content === "string" ? currentReport.content : JSON.stringify(currentReport.content, null, 2);
      // Normalize B markdown export to eight ## headings (RES-13)
      const map = (typeof text === "string") ? splitMarkdownBSections(text) : null;
      if (map) {
        const research = tryParseResearch(text);
        // keep original if already well-formed; else rebuild
        text = TEMPLATE_B_SECTIONS.map(t => "## " + t + "\n\n" + (map[t] || "")).join("\n\n");
      } else {
        const research = tryParseResearch(text);
        if (research && research.sections) {
          text = TEMPLATE_B_SECTIONS.map(t => {
            const v = research.sections[t];
            const body = (v && typeof v === "object") ? (v.markdown || v.text || JSON.stringify(v, null, 2)) : (v || "");
            return "## " + t + "\n\n" + body;
          }).join("\n\n");
          if (Array.isArray(research.sources) && research.sources.length) {
            text += "\n\n## 来源与运行记录\n\n" + research.sources.map(s => "- " + (s.evidence_id || "") + " " + (s.source_url || "")).join("\n");
          }
        }
      }
      blob = new Blob([text || ""], { type: "text/plain;charset=utf-8" });
      name = id + (currentReport.content_format === "html" ? ".html" : ".md");
    }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    toast("已导出 " + name);
  }

  // ---------- R5 / CON-06: several running/finished research tasks shown separately ----------
  let userPinned = false;
  const taskBoard = {}; // taskId -> {task, label, updatedAt}
  const TASK_STATUS_CN = { queued: "排队", running: "进行中", success: "完成", partial_success: "部分完成", failed: "失败", cancelled: "已取消" };

  function taskSymbols(task) {
    const req = task && task.request;
    const cs = (req && Array.isArray(req.contracts)) ? req.contracts : [];
    return cs.map(c => (c && typeof c === "object") ? (c.symbol || c.pair || "") : String(c || "")).filter(Boolean);
  }

  function renderTaskBoard() {
    ensureShell();
    const box = q("#reportTaskBoard");
    if (!box) return;
    const ids = Object.keys(taskBoard).sort((a, b) => (taskBoard[b].createdAt || "").localeCompare(taskBoard[a].createdAt || ""));
    if (!ids.length) { box.style.display = "none"; box.innerHTML = ""; return; }
    box.style.display = "";
    const expanded=box.querySelector("details")?.open;
    const active=ids.filter(id=>["queued","running"].includes(taskBoard[id].task?.status)).length;
    box.innerHTML = "<details"+(expanded||active?" open":"")+"><summary>投研任务 · 进行中 "+active+" / 记录 "+ids.length+"（历史错误独立于当前报告）</summary>" + ids.map(id => {
      const ent = taskBoard[id];
      const t = ent.task || {};
      const syms = taskSymbols(t);
      const res = t.result || {};
      const bs = res.batch_status;
      let line = (syms.length ? syms.slice(0, 4).join("、") + (syms.length > 4 ? " 等" + syms.length + "个" : "") : (ent.label || "投研"))
        + " · " + (TASK_STATUS_CN[t.status] || t.status || "—")
        + ((t.progress && t.progress.phase && !["success", "failed", "cancelled"].includes(t.status)) ? " · " + t.progress.phase : "");
      if (bs) line += " · 成功 " + bs.success + "/" + bs.total + " 组" + (bs.failed_symbols && bs.failed_symbols.length ? "，失败：" + bs.failed_symbols.join("、") : "");
      let btns = "";
      const rid = res.report_id;
      (res.rejected_report_ids || []).forEach(rejectedId => {
        btns += " <button type='button' data-open-report='" + escape(rejectedId) + "'>查看未通过报告</button>";
      });
      if (rid) btns += " <button type='button' data-open-report='" + escape(rid) + "'>查看</button>";
      if (res.batch_id && bs && bs.retryable_symbols && bs.retryable_symbols.length && t.status !== "running" && t.status !== "queued") {
        btns += " <button type='button' data-retry-batch='" + escape(res.batch_id) + "'>只重试失败币</button>";
      }
      if (t.status === "failed" && t.error) line += " · " + String(t.error).slice(0, 60);
      return "<div class='muted' data-task-id='" + escape(id) + "' style='font-size:12px;margin-top:4px'>" + escape(line) + btns + "</div>";
    }).join("") + "</details>";
    box.onclick = (e) => {
      const o = e.target.closest("[data-open-report]");
      if (o) { userPinned = true; openReport(o.dataset.openReport).then(() => renderList()).catch(err => toast(String(err.message || err))); return; }
      const r = e.target.closest("[data-retry-batch]");
      if (r) {
        r.disabled = true;
        api("/api/reports/batches/" + r.dataset.retryBatch + "/retry-failed", "POST", {})
          .then(out => { toast("已排队：只重试失败组"); if (out && out.task) trackTask(out.task, "失败币重试"); })
          .catch(err => { r.disabled = false; toast(String(err.message || err)); });
      }
    };
  }

  function trackTask(task, label) {
    if (!task || !task.id) return;
    const prev = taskBoard[task.id];
    taskBoard[task.id] = { task: task, label: label || (prev && prev.label), createdAt: task.created_at || (prev && prev.createdAt) || "" };
    renderTaskBoard();
  }

  // Background completion: never replace what the user is reading; show a notice instead.
  async function notifyReady(reportId, label) {
    if (!reportId) return;
    const reading = userPinned && currentId && currentId !== reportId;
    if (!reading) { await loadList(reportId); return; }
    const data = await api("/api/reports");
    reports = data.reports || [];
    renderList();
    const n = q("#reportReadyNotice");
    if (n) {
      n.style.display = "";
      n.innerHTML = "新报告已完成：" + escape(label || reportId) + " <button type='button' data-ready-open='" + escape(reportId) + "'>查看</button> <button type='button' data-ready-close='1'>关闭</button>";
      n.onclick = (e) => {
        if (e.target.closest("[data-ready-close]")) { n.style.display = "none"; return; }
        const b = e.target.closest("[data-ready-open]");
        if (b) { n.style.display = "none"; openReport(b.dataset.readyOpen).catch(err => toast(String(err.message || err))); }
      };
    }
  }

  let boardRefreshing = false;
  async function refreshTaskBoard() {
    if (boardRefreshing) return;
    boardRefreshing = true;
    try {
      const data = await api('/api/tasks');
      for (const t of (data.tasks || [])) {
        if (!['ai_research','scan_and_research','ai_research_batch_retry'].includes(t.kind)) continue;
        trackTask(t);
      }
    } catch (_) { /* Leave last observed task statuses in place. */ }
    finally { boardRefreshing = false; }
  }
  setInterval(refreshTaskBoard, 2500);
  refreshTaskBoard();
  window.ReportRuntime = { loadList, openReport, saveLocalFromDom, ensureShell, renderList, displayTitle, exportCurrent, trackTask, notifyReady, _taskBoard: taskBoard };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => { ensureShell(); loadList().catch(() => {}); });
  else { ensureShell(); loadList().catch(() => {}); }
})();
