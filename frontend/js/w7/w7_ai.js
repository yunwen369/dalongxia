/* W7.ai——切割自 v7_app.js（RF-20261112-01 阶段4）。代码体原样保留。 */
(function () {
  "use strict";
  var W7 = window.W7 = window.W7 || {};
  var C = W7.core;
  var $ = C.$, toast = C.toast, apiJson = C.apiJson, queryParam = C.queryParam;
  var currentId = function () { return W7.detail.currentId.apply(null, arguments); };
  var esc = function () { return W7.forms.esc.apply(null, arguments); };
  var newsVal = function () { return W7.data.newsVal.apply(null, arguments); };
  var preview = function () { return W7.shell.preview.apply(null, arguments); };
  var statKpis = function () { return W7.portfolio.statKpis.apply(null, arguments); };

  /* ---------- 8. AI 视图输入 ---------- */
  /* 2026-11-18 修复：维护本机会话历史，以 messages 数组发送，
     启用后端 /api/ai/chat 已支持的多轮上下文（保留最近 10 轮）。 */
  var chatHistory = [];

  /* 指令性 pill：点击后直接发送，不再仅填入输入框（否则用户点完无任何反馈）。 */
  var DIRECT_SEND = { "解读大盘": 1, "诊断持仓": 1, "同业对比": 1, "生成复盘": 1 };

  function bindAi() {
    document.addEventListener("keydown", function (event) {
      var input = $("v7-ai-input");
      if (!input || document.activeElement !== input) { return; }
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey || !event.shiftKey)) {
        event.preventDefault();
        sendAi(input);
      }
    });
    document.addEventListener("click", function (event) {
      if (event.target.id === "v7-ai-send") {
        var input = $("v7-ai-input");
        if (input) { sendAi(input); }
      }
      var pill = event.target.closest(".v7-pill");
      if (pill) {
        var txt = pill.textContent.trim();
        if (txt === "翻译" && $("v7-ai-input")) {
          translateText(($("v7-ai-input").value || "").trim());
          $("v7-ai-input").value = "";
          return;
        }
        if (txt === "清空会话") {
          clearAiHistory();
          return;
        }
        /* 2026-11-18 修复：指令性问题（诊断持仓等）点击即发送，带输入框补充内容；
           发送后界面立即出现「思考中…」，保证点击必有反馈。 */
        if (DIRECT_SEND[txt]) {
          var inp = $("v7-ai-input");
          var extra = ((inp && inp.value) || "").trim();
          sendAiDirect(txt + (extra ? "（补充：" + extra + "）" : ""));
          return;
        }
        if ($("v7-ai-input")) { $("v7-ai-input").value = txt; $("v7-ai-input").focus(); }
      }
    });
  }

  function appendMsg(kind, text, extraHtml) {
    var host = document.querySelector(".v7-chat");
    if (!host) { return null; }
    var el = document.createElement("div");
    el.className = "v7-msg " + kind;
    el.innerHTML = '<div class="v7-ai-txt">' + esc(text) + "</div>" + (extraHtml || "");
    host.appendChild(el);
    host.scrollTop = host.scrollHeight;
    return el;
  }

  /* 2026-11-18 修复：等待兜底。推理模型单轮可达 15–70 秒，30 秒后追加进度提示，
     避免「思考中…」长时间无变化被用户误认为没反应。 */
  function startWaitingTip(loading) {
    if (!loading) { return null; }
    return setTimeout(function () {
      if (!loading || !loading.parentNode) { return; }
      var tip = document.createElement("div");
      tip.className = "v7-wait-tip";
      tip.textContent = "模型思考中，推理模型响应较慢，请稍候…";
      loading.appendChild(tip);
      host_scroll(loading);
    }, 30000);
  }

  function host_scroll(el) {
    var host = document.querySelector(".v7-chat");
    if (host && el) { host.scrollTop = host.scrollHeight; }
  }

  /** 指令性问题直接发送（不依赖输入框内容）。 */
  function sendAiDirect(text) {
    var input = $("v7-ai-input");
    if (input) { input.value = ""; }
    sendAiText(text);
  }

  function sendAi(input) {
    var text = (input.value || "").trim();
    if (!text) { return; }
    sendAiText(text);
  }

  function sendAiText(text) {
    appendMsg("me", text);
    var loading = appendMsg("ai", "思考中…");
    var waitTip = startWaitingTip(loading);
    var history = chatHistory.slice();  // 完整历史，由后端按上下文 KB 预算裁剪
    history.push({ role: "user", content: text });
    apiJson("/api/ai/chat", { method: "POST", body: JSON.stringify({
      messages: history, page: currentId(),
      page_params: { symbol: localStorage.getItem("openbb-symbol") || "" } }) })
      .then(function (r) {
        var data = (r.body && r.body.data) || {};
        var reply = data.reply || data.content || data.answer ||
          (data.choices && data.choices[0] && data.choices[0].message && data.choices[0].message.content) ||
          (r.body && r.body.error) || "无返回";
        if (loading) { loading.remove(); }
        if (waitTip) { clearTimeout(waitTip); waitTip = null; }
        /* 2026-11-18 修复：同步后端上下文信息（context_used + 实际携带轮数），
           输入输出与上下文不再只在后端处理、前端不可见。 */
        var ctxHtml = "";
        if (!(r.body && r.body.error) && data.context_used) {
          var n = (data.messages || history).length;
          ctxHtml = '<div class="v7-ctx-note">已注入当前页面真实数值上下文 · 携带 ' + n + " 轮会话</div>";
        } else if (!(r.body && r.body.error) && (data.messages || []).length) {
          ctxHtml = '<div class="v7-ctx-note">携带 ' + data.messages.length + " 轮会话</div>";
        }
        appendMsg("ai", reply, ctxHtml + '<div class="v7-warn">⚠️ 非投资建议 · 数据以真实接口为准</div>');
        /* 仅成功时落历史；失败回滚，避免空回复进入上下文 */
        if (!(r.body && r.body.error) && reply && reply !== "无返回") {
          chatHistory.push({ role: "assistant", content: reply });
        }
      })
      .catch(function () {
        if (loading) { loading.remove(); }
        if (waitTip) { clearTimeout(waitTip); waitTip = null; }
        appendMsg("ai", "请求失败，请检查智能配置（设置页）");
      });
  }

  /** 清空本机会话历史（多轮上下文复位，重新提问不再携带旧上下文）。 */
  function clearAiHistory() {
    chatHistory = [];
    var host = document.querySelector(".v7-chat");
    if (host) { host.innerHTML = ""; }
    toast("已清空会话，重新提问");
  }

  function translateText(text) {
    if (!text) { return; }
    appendMsg("me", "翻译：" + text);
    var loading = appendMsg("ai", "翻译中…");
    apiJson("/api/ai/translate", { method: "POST", body: JSON.stringify({ text: text, target: "zh" }) })
      .then(function (r) {
        var data = (r.body && r.body.data) || {};
        if (loading) { loading.remove(); }
        /* 2026-11-18 修复：后端 /api/ai/translate 成功字段是 translated，
           原解析链（text/translation/result）三者均不匹配，翻译界面必然显示「无返回」 */
        appendMsg("ai", data.translated || data.text || data.translation || data.result || (r.body && r.body.error) || "无返回");
      });
  }

  function saveEdit(kind, hint) {
    function fail(msg) { if (hint) { hint.textContent = msg; hint.className = "v7-hint bad"; } }
    function done(msg, back) {
      if (hint) { hint.textContent = msg; hint.className = "v7-hint ok"; }
      toast(msg); setTimeout(function () { location.hash = back; }, 650);
    }
    if (kind === "watchlist") {
      var code = (($("v7-f-code") || {}).value || "").trim();
      if (!code) { fail("标的不能为空"); return; }
      apiJson("/api/watchlist", { method: "POST", body: JSON.stringify({ symbol: code, market: "a" }) })
        .then(function (r) {
          if (r.ok && r.body && !r.body.error) { done("已加入自选", "#/watchlist"); }
          else { fail((r.body && r.body.error) || "保存失败"); }
        });
      return;
    }
    if (kind === "alerts") {
      var map = { "价格 ≥": ["price","gte"], "价格 ≤": ["price","lte"],
        "涨跌幅 ≥": ["pct_change","gte"], "涨跌幅 ≤": ["pct_change","lte"],
        "估值分位 ≥": ["valuation","gte"], "拥挤度 ≥": ["crowding","gte"] };
      var pair = map[(($("v7-f-cond") || {}).value || "")] || ["price","gte"];
      var target = (($("v7-f-symbol") || {}).value || "").trim();
      if (!target) { fail("标的不能为空"); return; }
      var val = parseFloat((($("v7-f-threshold") || {}).value || "").trim());
      if (!isFinite(val)) { fail("阈值必须是数字"); return; }
      apiJson("/api/alerts", { method: "POST", body: JSON.stringify({ type: pair[0], target: target,
        op: pair[1], value: val, note: (($("v7-f-name") || {}).value || "").trim() }) })
        .then(function (r) {
          if (r.ok && r.body && !r.body.error) { done("提醒已保存", "#/alerts"); }
          else { fail((r.body && r.body.error) || "保存失败"); }
        });
      return;
    }
    if (kind === "portfolio") {
      var sym = (($("v7-f-symbol") || {}).value || "").trim();
      var shares = parseFloat((($("v7-f-qty") || {}).value || "").trim());
      var cost = parseFloat((($("v7-f-cost") || {}).value || "").trim());
      if (!sym) { fail("标的不能为空"); return; }
      if (!isFinite(shares) || shares <= 0) { fail("数量必须大于 0"); return; }
      if (!isFinite(cost) || cost < 0) { fail("成本必须为非负数"); return; }
      var mktSel = (($("v7-f-market") || {}).value || "A股");
      var mkt = mktSel === "港股" ? "hk" : mktSel === "美股" ? "us" : mktSel === "基金" ? "fund" : "a";
      apiJson("/api/portfolio", { method: "POST", body: JSON.stringify({ market: mkt, symbol: sym,
        shares: shares, cost: cost }) })
        .then(function (r) {
          if (r.ok && r.body && !r.body.error) { done("持仓已保存", "#/portfolio"); }
          else { fail((r.body && r.body.error) || "保存失败"); }
        });
      return;
    }
  }

  /* ---------- 33b. AI 配置卡（2026-11-18：思考强度/上下文/输出配置） ---------- */
  /* 设置页「智能配置」唯一入口：密钥 / 引用地址 / 模型 / 思考强度 / 上下文输入 / 输出配置，
     保存走 /api/ai/config，读取走 /api/ai/config 回显。连接测试卡复用本卡字段。 */
  function injectAiConfigCard() {
    if (currentId() !== "settings") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-ai-cfg")) { return; }
    var card = document.createElement("div");
    card.className = "card v7-ai-cfg";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">✦</span>智能配置</span>' +
        '<span class="card-note">密钥不回显；思考强度与上下文/输出配置保存后立即应用于对话</span></div>' +
        '<div class="v-body">' +
          '<div class="v7-form">' +
            '<div><label>密钥（api_key）</label><input id="v7-ai-key" type="password" placeholder="留空则保留已保存密钥"></div>' +
            '<div><label>引用地址（base_url）</label><input id="v7-ai-baseurl" placeholder="https://api.xxx.com/v1"></div>' +
            '<div><label>模型名称（model）</label><input id="v7-ai-model" placeholder="如 GLM-5.3-Flash"></div>' +
            '<div><label>思考强度（reasoning_effort）</label><select id="v7-ai-effort">' +
              ['', "弱", "中", "高", "强", "极致"].map(function (x) {
                return '<option value="' + esc(x) + '">' + (x || "不指定（上游默认）") + "</option>";
              }).join("") +
            "</select></div>" +
            '<div><label>上下文输入（KB，0=上游默认）</label><input id="v7-ai-ctxkb" type="number" min="0" max="512" placeholder="如 8，留空=上游默认"></div>' +
            '<div><label>输出配置（token，0=上游默认）</label><input id="v7-ai-tokens" type="number" min="0" max="8192" placeholder="0=不传，按模型默认"></div>' +
            '<div><button class="v7-btn primary" data-act="ai-cfg-save" type="button">保存配置</button>' +
            '<span class="v7-hint" id="v7-ai-cfg-hint"></span></div>' +
          "</div>" +
        "</div>";
    body.insertBefore(card, body.firstChild);
    /* 回显已保存配置（密钥仅掩码，不接受明文回显） */
    apiJson("/api/ai/config").then(function (r) {
      var d = (r.body && r.body.data) || {};
      var k = $("v7-ai-key"), b = $("v7-ai-baseurl"), m = $("v7-ai-model");
      var e = $("v7-ai-effort"), c = $("v7-ai-ctxkb"), t = $("v7-ai-tokens");
      if (k) { k.value = ""; }  // 密钥不回显、不接受明文，留空保留原值
      if (b && d.base_url) { b.value = d.base_url; }
      if (m && d.model) { m.value = d.model; }
      if (e) { e.value = d.reasoning_effort_label || ""; }
      if (c && d.context_kb != null) { c.value = String(d.context_kb); }
      if (t && d.max_tokens != null) { t.value = String(d.max_tokens); }
    });
    /* 保存：复用连接测试卡与对话链路读取的同一字段来源 */
    card.addEventListener("click", function (ev) {
      var btn = ev.target.closest('[data-act="ai-cfg-save"]');
      if (!btn) { return; }
      saveAiConfig(btn);
    });
  }

  /** 保存智能配置到 /api/ai/config，返回中文结果提示。 */
  function saveAiConfig(btn) {
    var hint = $("v7-ai-cfg-hint");
    if (!hint) { return; }
    var key = ((($("v7-ai-key") || {}).value) || "").trim();
    var base = ((($("v7-ai-baseurl") || {}).value) || "").trim();
    var model = ((($("v7-ai-model") || {}).value) || "").trim();
    var effort = ((($("v7-ai-effort") || {}).value) || "").trim();
    var ctxKb = ((($("v7-ai-ctxkb") || {}).value) || "").trim();
    var tokens = ((($("v7-ai-tokens") || {}).value) || "").trim();
    if (!base) { hint.textContent = "请填写引用地址（base_url）"; hint.className = "v7-hint bad"; return; }
    if (!model) { hint.textContent = "请填写模型名称（model）"; hint.className = "v7-hint bad"; return; }
    if (key && !/^sk-/.test(key) && !/^[A-Za-z0-9_\-]{16,}$/.test(key)) {
      hint.textContent = "密钥格式异常，请检查后重试"; hint.className = "v7-hint bad"; return;
    }
    var body = { base_url: base, model: model };
    if (key) { body.api_key = key; }
    if (effort) { body.reasoning_effort = effort; }
    // 2026-11-19：上下文/输出随表单提交：留空或 0 = 不裁剪/不传，按上游默认
    if (ctxKb) { body.context_kb = parseInt(ctxKb, 10); }
    if (tokens) { body.max_tokens = parseInt(tokens, 10); }
    btn.disabled = true;
    hint.textContent = "保存中…"; hint.className = "v7-hint";
    apiJson("/api/ai/config", { method: "POST", body: JSON.stringify(body) })
      .then(function (r) {
        btn.disabled = false;
        if (r.body && r.body.error) {
          hint.textContent = r.body.error;
          hint.className = "v7-hint bad";
          return;
        }
        hint.textContent = "已保存，配置立即生效";
        hint.className = "v7-hint ok";
        toast("AI 配置已保存");
      })
      .catch(function () {
        btn.disabled = false;
        hint.textContent = "保存失败，请重试";
        hint.className = "v7-hint bad";
      });
  }

  /* ---------- 33. AI 连接测试（v3.4.0：恢复 ai/test） ---------- */
  /** 设置页注入 AI 连通性测试卡（复用智能配置卡字段，避免双源不一致）。 */
  function injectAiTestPanel() {
    if (currentId() !== "settings") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-ai-test")) { return; }
    var card = document.createElement("div");
    card.className = "card v7-ai-test";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">✦</span>AI 连接测试</span>' +
        '<span class="card-note">最小请求验证鉴权与模型（120s 超时，推理模型响应较慢）</span></div>' +
        '<div class="v-body">' +
          '<div class="v7-form" style="margin-bottom:8px">' +
            '<div><button class="v7-btn primary" data-act="ai-test" type="button">测试连接</button></div>' +
          "</div>" +
          '<div id="v7-ai-test-body"><div class="v7-hint">密钥沿用「智能配置」中已保存配置（此页不回显、不接受明文输入）</div></div>' +
        "</div>";
    body.insertBefore(card, body.firstChild);
    apiJson("/api/ai/config").then(function (r) {
      var d = (r.body && r.body.data) || {};
      var tip = $("v7-ai-test-body");
      if (tip && !d.configured) {
        tip.innerHTML = '<div class="v7-sec-miss">未配置智能服务：请先在上方「智能配置」保存 接口地址 / 密钥 / 模型，再回来测试。</div>';
      }
    });
  }

  /** 执行 AI 连通性测试（ai/test），读取智能配置卡字段。 */
  function runAiTest() {
    var box = $("v7-ai-test-body");
    if (!box) { return; }
    var base = ((($("v7-ai-baseurl") || {}).value) || "").trim();
    var model = ((($("v7-ai-model") || {}).value) || "").trim();
    if (!base) { box.innerHTML = '<div class="v-empty">请先在「智能配置」填写 Base URL（以 http(s):// 开头）</div>'; return; }
    if (!model) { box.innerHTML = '<div class="v-empty">请先在「智能配置」填写模型名称</div>'; return; }
    box.innerHTML = '<div class="loading">测试中（最长 120 秒，推理模型响应较慢）…</div>';
    apiJson("/api/ai/test", { method: "POST", body: JSON.stringify({ base_url: base, model: model }) })
      .then(function (r) {
        var target = $("v7-ai-test-body");
        if (!target) { return; }
        var d = (r.body && r.body.data) || {};
        if (r.body && r.body.error) {
          target.innerHTML = '<div class="v-empty">' + esc(r.body.error) + "</div>";
          return;
        }
        target.innerHTML = statKpis([
          ["结果", d.ok ? "连通" : "不通"],
          ["耗时", (d.latency_ms == null ? "—" : String(d.latency_ms)) + " 毫秒"],
          ["说明", d.detail || "—"]
        ]);
      });
  }

  /* ---------- 34. AI 财报摘要（v3.4.0：恢复 ai/summary；v3.6.0：R14 三市场） ---------- */
  /** AI 摘要三段式渲染主体（key_table/points/risks），资讯页与 fin/ann 注入卡共用。 */
  function aiSummaryHtml(d, mkt) {
    if (d.status === "no_content" || !d.summary) {
      return '<div class="v-empty">' + esc(d.caliber || "正文不可得，未生成摘要（不造数）") + "</div>";
    }
    var s = d.summary || {};
    var html = '<div class="v7-hint" style="margin-bottom:6px">' + esc(d.title || "") +
      " · 市场 " + esc(String(d.market || mkt).toUpperCase()) +
      " · 原文 " + esc(String(d.source_text_chars || 0)) + " 字" +
      (d.truncated ? "（已截断）" : "") + " · 模型 " + esc(d.model || "—") +
      (d.cached ? " · 命中缓存" : "") + "</div>";
    if ((s.key_table || []).length) {
      html += '<div class="v7-preview-table"><table><tr><th>关键指标</th><th>数值</th><th>同比</th></tr>' +
        s.key_table.map(function (x) {
          return "<tr><td>" + esc(x.metric) + "</td><td>" + esc(x.value) + "</td><td>" + esc(x.yoy || "—") + "</td></tr>";
        }).join("") + "</table></div>";
    }
    if ((s.points || []).length) {
      html += '<div class="v7-hint" style="margin:8px 0 2px">要点</div><ul class="v7-ai-ul">' +
        s.points.map(function (p) { return "<li>" + esc(p) + "</li>"; }).join("") + "</ul>";
    }
    if ((s.risks || []).length) {
      html += '<div class="v7-hint" style="margin:8px 0 2px">风险与不确定事项</div><ul class="v7-ai-ul">' +
        s.risks.map(function (p) { return "<li>" + esc(p) + "</li>"; }).join("") + "</ul>";
    }
    if (d.caliber) { html += '<div class="v7-sec-note">口径：' + esc(d.caliber) + "</div>"; }
    return html;
  }

  /** 资讯页「AI 摘要」页签的渲染主体。 */
  function loadAiSummary() {
    var box = $("v7-news-body");
    if (!box) { return; }
    var code = newsVal("v7-news-code", W7.st.newsInput.code);
    var title = newsVal("v7-ai-title", W7.st.newsInput.title);
    if (!code && !title) {
      box.innerHTML = '<div class="v-empty">请填写公告代码（或公告标题）后再生成摘要</div>';
      return;
    }
    /* R14：市场取自资讯工具下拉（默认 a，与 v3.5.0 前行为一致）。 */
    var mkt = (W7.st && W7.st.newsInput && W7.st.newsInput.market) || "a";
    box.innerHTML = '<div class="loading">智能摘要生成中（读取公告正文 + 结构化，约 10–40 秒）…</div>';
    apiJson("/api/ai/summary", {
      method: "POST",
      body: JSON.stringify({ market: mkt, code: code, title: title, pub_time: "", doc_url: "" })
    }).then(function (r) {
      var target = $("v7-news-body");
      if (!target) { return; }
      if (r.body && r.body.error) {
        target.innerHTML = '<div class="v-empty">' + esc(r.body.error) + "</div>";
        return;
      }
      target.innerHTML = aiSummaryHtml((r.body && r.body.data) || {}, mkt);
    }).catch(function () {   // F14：失败态，避免停留「生成中…」
      var b3 = $("v7-news-body");
      if (b3) { b3.innerHTML = '<div class="v-empty">AI 摘要请求失败（/api/ai/summary）</div>'; }
    });
  }

  /* ---------- 34b. R14：fin / ann 视图注入 AI 摘要卡（走 w7 注入层，不改 VIEWS 定义） ---------- */
  /** 财报摘要 / 公告库视图注入卡：市场 + 代码 + 标题，与资讯工具共用 W7.st.newsInput。 */
  function injectFinSummaryCard() {
    var id = currentId();
    if (id !== "fin" && id !== "ann") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-fin-ai")) { return; }
    if (!W7.st.newsInput) { W7.st.newsInput = { sector: "", code: "", title: "", market: "a" }; }
    var st = W7.st.newsInput;
    var card = document.createElement("div");
    card.className = "card v7-fin-ai";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">✦</span>AI 财报摘要</span>' +
        '<span class="card-note">三市场（A股 / 港股 / 美股）· 公告正文结构化</span></div>' +
        '<div class="v-body">' +
          '<div class="v7-form" style="margin-bottom:8px">' +
            '<div><label>市场</label><select id="v7-finai-market">' +
              [["a", "A股"], ["hk", "港股"], ["us", "美股"]].map(function (x) {
                return '<option value="' + x[0] + '"' + (x[0] === (st.market || "a") ? " selected" : "") + ">" + x[1] + "</option>";
              }).join("") +
            "</select></div>" +
            '<div><label>公告代码</label><input id="v7-finai-code" value="' + esc(st.code || "") + '" placeholder="A股 600519 / 港股 00700 / 美股 AAPL"></div>' +
            '<div><label>公告标题（可空）</label><input id="v7-finai-title" value="' + esc(st.title || "") + '" placeholder="如 2026年第三季度报告"></div>' +
            '<div><button class="v7-btn primary" data-act="fin-ai-run" type="button">生成摘要</button></div>' +
          "</div>" +
          '<div id="v7-finai-body"><div class="v7-hint">填写公告代码（或公告标题）后点「生成摘要」；约 10–40 秒。</div></div>' +
        "</div>";
    body.insertBefore(card, body.firstChild);
    card.addEventListener("input", function (ev) {
      var t = ev.target;
      if (!t || !t.id) { return; }
      if (t.id === "v7-finai-market") { W7.st.newsInput.market = t.value; }
      else if (t.id === "v7-finai-code") { W7.st.newsInput.code = t.value; }
      else if (t.id === "v7-finai-title") { W7.st.newsInput.title = t.value; }
    });
    card.addEventListener("change", function (ev) {
      var t = ev.target;
      if (t && t.id === "v7-finai-market" && t.value) { W7.st.newsInput.market = t.value; }
    });
  }

  /** fin/ann 摘要卡的执行主体（data-act="fin-ai-run"）。 */
  function runFinAiSummary() {
    var box = $("v7-finai-body");
    if (!box) { return; }
    var codeEl = $("v7-finai-code"), titleEl = $("v7-finai-title"), mktEl = $("v7-finai-market");
    var code = ((codeEl && codeEl.value) || "").trim();
    var title = ((titleEl && titleEl.value) || "").trim();
    var mkt = (mktEl && mktEl.value) || (W7.st.newsInput && W7.st.newsInput.market) || "a";
    if (W7.st.newsInput) {
      W7.st.newsInput.market = mkt;
      if (code) { W7.st.newsInput.code = code; }
      if (title) { W7.st.newsInput.title = title; }
    }
    if (!code && !title) {
      box.innerHTML = '<div class="v-empty">请填写公告代码（或公告标题）后再生成摘要</div>';
      return;
    }
    box.innerHTML = '<div class="loading">智能摘要生成中（读取公告正文 + 结构化，约 10–40 秒）…</div>';
    apiJson("/api/ai/summary", {
      method: "POST",
      body: JSON.stringify({ market: mkt, code: code, title: title, pub_time: "", doc_url: "" })
    }).then(function (r) {
      var target = $("v7-finai-body");
      if (!target) { return; }
      if (r.body && r.body.error) {
        target.innerHTML = '<div class="v-empty">' + esc(r.body.error) + "</div>";
        return;
      }
      target.innerHTML = aiSummaryHtml((r.body && r.body.data) || {}, mkt);
    }).catch(function () {
      var b3 = $("v7-finai-body");
      if (b3) { b3.innerHTML = '<div class="v-empty">AI 摘要请求失败（/api/ai/summary）</div>'; }
    });
  }


  /* 跨域导出（W7.ai） */
  W7.ai = W7.ai || {};
  W7.ai.bindAi = bindAi;
  W7.ai.clearAiHistory = clearAiHistory;
  W7.ai.injectAiConfigCard = injectAiConfigCard;
  W7.ai.saveAiConfig = saveAiConfig;
  W7.ai.injectAiTestPanel = injectAiTestPanel;
  W7.ai.injectFinSummaryCard = injectFinSummaryCard;
  W7.ai.loadAiSummary = loadAiSummary;
  W7.ai.runAiTest = runAiTest;
  W7.ai.runFinAiSummary = runFinAiSummary;
  W7.ai.saveEdit = saveEdit;
})();
