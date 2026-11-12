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
        if (pill.textContent.trim() === "翻译" && $("v7-ai-input")) {
          translateText(($("v7-ai-input").value || "").trim());
          $("v7-ai-input").value = "";
          return;
        }
        if ($("v7-ai-input")) { $("v7-ai-input").value = pill.textContent; $("v7-ai-input").focus(); }
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

  function sendAi(input) {
    var text = (input.value || "").trim();
    if (!text) { return; }
    appendMsg("me", text);
    input.value = "";
    var loading = appendMsg("ai", "思考中…");
    apiJson("/api/ai/chat", { method: "POST", body: JSON.stringify({
      message: text, page: currentId(),
      page_params: { symbol: localStorage.getItem("openbb-symbol") || "" } }) })
      .then(function (r) {
        var data = (r.body && r.body.data) || {};
        var reply = data.reply || data.content || data.answer ||
          (data.choices && data.choices[0] && data.choices[0].message && data.choices[0].message.content) ||
          (r.body && r.body.error) || "无返回";
        if (loading) { loading.remove(); }
        appendMsg("ai", reply, '<div class="v7-warn">⚠️ 非投资建议 · 数据以真实接口为准</div>');
      })
      .catch(function () {
        if (loading) { loading.remove(); }
        appendMsg("ai", "请求失败，请检查 AI 配置（设置页）");
      });
  }

  function translateText(text) {
    if (!text) { return; }
    appendMsg("me", "翻译：" + text);
    var loading = appendMsg("ai", "翻译中…");
    apiJson("/api/ai/translate", { method: "POST", body: JSON.stringify({ text: text, target: "zh" }) })
      .then(function (r) {
        var data = (r.body && r.body.data) || {};
        if (loading) { loading.remove(); }
        appendMsg("ai", data.text || data.translation || data.result || (r.body && r.body.error) || "无返回");
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

  /* ---------- 33. AI 连接测试（v3.4.0：恢复 ai/test） ---------- */
  /** 设置页注入 AI 连通性测试卡。 */
  function injectAiTestPanel() {
    if (currentId() !== "settings") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-ai-test")) { return; }
    var card = document.createElement("div");
    card.className = "card v7-ai-test";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">✦</span>AI 连接测试</span>' +
        '<span class="card-note">最小请求验证鉴权与模型（15s 超时）</span></div>' +
        '<div class="v-body">' +
          '<div class="v7-form" style="margin-bottom:8px">' +
            '<div><label>Base URL</label><input id="v7-ai-base" placeholder="https://api.xxx.com/v1"></div>' +
            '<div><label>模型</label><input id="v7-ai-model" placeholder="如 deepseek-chat"></div>' +
            '<div><button class="v7-btn primary" data-act="ai-test" type="button">测试连接</button></div>' +
          "</div>" +
          '<div id="v7-ai-test-body"><div class="v7-hint">密钥沿用已保存配置（此页不回显、不接受明文输入）</div></div>' +
        "</div>";
    body.insertBefore(card, body.firstChild);
    apiJson("/api/ai/config").then(function (r) {
      var d = (r.body && r.body.data) || {};
      var b = $("v7-ai-base"), m = $("v7-ai-model");
      if (b && d.base_url) { b.value = d.base_url; }
      if (m && d.model) { m.value = d.model; }
      var tip = $("v7-ai-test-body");
      if (tip && !d.configured) {
        tip.innerHTML = '<div class="v7-sec-miss">未配置 AI：请先在上方「AI 配置」保存 base_url / api_key / model，再回来测试。</div>';
      }
    });
  }

  /** 执行 AI 连通性测试（ai/test）。 */
  function runAiTest() {
    var box = $("v7-ai-test-body");
    if (!box) { return; }
    var base = ((($("v7-ai-base") || {}).value) || "").trim();
    var model = ((($("v7-ai-model") || {}).value) || "").trim();
    if (!base) { box.innerHTML = '<div class="v-empty">请填写 Base URL（以 http(s):// 开头）</div>'; return; }
    box.innerHTML = '<div class="loading">测试中（最长 15 秒）…</div>';
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
          ["耗时", (d.latency_ms == null ? "—" : String(d.latency_ms)) + " ms"],
          ["说明", d.detail || "—"]
        ]);
      });
  }

  /* ---------- 34. AI 财报摘要（v3.4.0：恢复 ai/summary） ---------- */
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
    box.innerHTML = '<div class="loading">AI 摘要生成中（读取公告正文 + 结构化，约 10–40 秒）…</div>';
    apiJson("/api/ai/summary", {
      method: "POST",
      body: JSON.stringify({ market: "a", code: code, title: title, pub_time: "", doc_url: "" })
    }).then(function (r) {
      var target = $("v7-news-body");
      if (!target) { return; }
      var d = (r.body && r.body.data) || {};
      if (r.body && r.body.error) {
        target.innerHTML = '<div class="v-empty">' + esc(r.body.error) + "</div>";
        return;
      }
      if (d.status === "no_content" || !d.summary) {
        target.innerHTML = '<div class="v-empty">' + esc(d.caliber || "正文不可得，未生成摘要（不造数）") + "</div>";
        return;
      }
      var s = d.summary || {};
      var html = '<div class="v7-hint" style="margin-bottom:6px">' + esc(d.title || "") +
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
      target.innerHTML = html;
    });
  }


  /* 跨域导出（W7.ai） */
  W7.ai = W7.ai || {};
  W7.ai.bindAi = bindAi;
  W7.ai.injectAiTestPanel = injectAiTestPanel;
  W7.ai.loadAiSummary = loadAiSummary;
  W7.ai.runAiTest = runAiTest;
  W7.ai.saveEdit = saveEdit;
})();
