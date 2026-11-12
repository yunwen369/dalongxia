/* v7.0 功能补齐 · 壳层与交互增强（高保真轮）
   覆盖：Logo 回首页 / 侧栏整栏折叠 / 顶栏标的搜索 / 提醒铃铛 / 健康·版本徽章 /
         布局三档（紧凑·舒适·自定义）/ W9 自定义面板 / AI 视图输入。
   说明：原型稿允许演示数据；能连到真实 /api 时优先用真实值。 */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var LS_LAYOUT = "openbb-layout";
  var LS_CUSTOM = "openbb-layout-custom";
  var DEMO_SEARCH = [
    { symbol: "600519", name: "贵州茅台", market: "A股" },
    { symbol: "300750", name: "宁德时代", market: "A股" },
    { symbol: "000001", name: "平安银行", market: "A股" },
    { symbol: "017811", name: "易方达蓝筹精选", market: "基金" }
  ];

  /** 右下角轻提示。 */
  function toast(text) {
    var el = document.createElement("div");
    el.className = "v7-toast";
    el.textContent = text;
    document.body.appendChild(el);
    setTimeout(function () { el.remove(); }, 2200);
  }

  /* ---------- 1. Logo 回首页 ---------- */
  function bindLogo() {
    var brand = document.querySelector(".brand");
    if (!brand) { return; }
    brand.style.cursor = "pointer";
    brand.title = "回到首页";
    brand.addEventListener("click", function () { location.hash = "#/home"; });
  }

  /* ---------- 2. 侧栏整栏折叠 ---------- */
  function bindCollapse() {
    var btn = $("v7-collapse");
    if (!btn) { return; }
    btn.addEventListener("click", function () {
      document.body.classList.toggle("v7-side-collapsed");
      btn.title = document.body.classList.contains("v7-side-collapsed") ? "展开侧栏" : "收起侧栏";
    });
  }

  /* ---------- 3. 布局三档 + W9 自定义 ---------- */
  function currentLayout() {
    try { return localStorage.getItem(LS_LAYOUT) || "comfort"; } catch (e) { return "comfort"; }
  }

  function customOpts() {
    var def = { font: 13, side: 176, gap: 10, kline: 300, row: 34, density: "标准" };
    try {
      var raw = localStorage.getItem(LS_CUSTOM);
      return raw ? Object.assign(def, JSON.parse(raw)) : def;
    } catch (e) { return def; }
  }

  /** 应用布局（写 body class 与 CSS 变量）。 */
  function applyLayout(mode, opts) {
    document.body.classList.remove("v7-compact", "v7-custom");
    if (mode === "compact") { document.body.classList.add("v7-compact"); }
    if (mode === "custom") {
      var o = opts || customOpts();
      document.body.classList.add("v7-custom");
      document.body.style.setProperty("--v7-font", o.font + "px");
      document.body.style.setProperty("--v7-side-width", o.side + "px");
      document.body.style.setProperty("--v7-gap", o.gap + "px");
      document.body.style.setProperty("--v7-kline-h", o.kline + "px");
      document.body.style.setProperty("--v7-row-h", o.row + "px");
    }
    var label = { compact: "紧凑", comfort: "舒适", custom: "自定义" }[mode] || "舒适";
    var btn = $("v7-layout-btn");
    if (btn) { btn.textContent = "⇔ 布局：" + label; }
    try { localStorage.setItem(LS_LAYOUT, mode); } catch (e) { /* 隐私模式忽略 */ }
  }

  function bindLayout() {
    var btn = $("v7-layout-btn");
    var menu = $("v7-layout-menu");
    if (!btn || !menu) { return; }
    btn.addEventListener("click", function (event) {
      event.stopPropagation();
      menu.hidden = !menu.hidden;
    });
    document.addEventListener("click", function () { menu.hidden = true; });
    menu.addEventListener("click", function (event) {
      var item = event.target.closest("button[data-layout]");
      if (!item) { return; }
      var mode = item.getAttribute("data-layout");
      if (mode === "reset") {
        try { localStorage.removeItem(LS_CUSTOM); } catch (e) { /* noop */ }
        applyLayout("comfort");
        toast("已重置为「舒适」默认布局");
      } else if (mode === "custom") {
        applyLayout("custom");
        openPanel();
      } else {
        applyLayout(mode);
      }
      menu.hidden = true;
    });
  }

  /* ---------- 4. W9 自定义面板 ---------- */
  var panel = null;

  function buildPanel() {
    panel = document.createElement("div");
    panel.className = "v7-panel-mask";
    panel.id = "v7-panel";
    panel.hidden = true;
    panel.innerHTML =
      '<div class="v7-panel">' +
        '<div class="v7-panel-head"><span>自定义布局</span>' +
          '<button class="v7-btn" id="v7-panel-x">×</button></div>' +
        '<div class="v7-panel-body">' +
          '<div>' +
            '<div class="v7-field"><span>密度</span><select id="v7-o-density">' +
              '<option>紧凑</option><option selected>标准</option><option>宽松</option></select></div>' +
            '<div class="v7-field"><span>基准字号</span><select id="v7-o-font">' +
              '<option>12</option><option selected>13</option><option>14</option><option>15</option></select></div>' +
            '<div class="v7-field"><span>侧栏宽度</span><select id="v7-o-side">' +
              '<option>160</option><option selected>176</option><option>200</option><option>220</option></select></div>' +
            '<div class="v7-field"><span>卡片间距</span><select id="v7-o-gap">' +
              '<option>6</option><option>10</option><option>14</option></select></div>' +
            '<div class="v7-field"><span>K 线高度</span><select id="v7-o-kline">' +
              '<option>240</option><option selected>300</option><option>360</option></select></div>' +
            '<div class="v7-field"><span>表格行高</span><select id="v7-o-row">' +
              '<option>28</option><option selected>34</option><option>40</option></select></div>' +
          '</div>' +
          '<div class="v7-preview">' +
            '<div class="pv-top"></div>' +
            '<div class="pv-row"><div></div><div></div></div>' +
            '<div class="pv-kline" id="v7-pv-kline" style="height:56px"></div>' +
            '<div style="margin-top:4px;opacity:.7">实时预览</div>' +
          '</div>' +
        '</div>' +
        '<div class="v7-panel-foot">' +
          '<button class="v7-btn" id="v7-panel-reset">重置为默认</button>' +
          '<button class="v7-btn" id="v7-panel-cancel">取消</button>' +
          '<button class="v7-btn primary" id="v7-panel-save">保存为我的布局</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(panel);

    var opt = customOpts();
    $("v7-o-font").value = String(opt.font);
    $("v7-o-side").value = String(opt.side);
    $("v7-o-gap").value = String(opt.gap);
    $("v7-o-kline").value = String(opt.kline);
    $("v7-o-row").value = String(opt.row);

    ["density", "font", "side", "gap", "kline", "row"].forEach(function (k) {
      $("v7-o-" + k).addEventListener("change", preview);
    });
    $("v7-panel-x").addEventListener("click", closePanel);
    $("v7-panel-cancel").addEventListener("click", closePanel);
    $("v7-panel-reset").addEventListener("click", function () {
      try { localStorage.removeItem(LS_CUSTOM); } catch (e) { /* noop */ }
      applyLayout("comfort");
      closePanel();
      toast("已重置为默认布局");
    });
    $("v7-panel-save").addEventListener("click", function () {
      var data = readPanel();
      try { localStorage.setItem(LS_CUSTOM, JSON.stringify(data)); } catch (e) { /* noop */ }
      applyLayout("custom", data);
      closePanel();
      toast("已保存为我的布局（本机生效）");
    });
  }

  function readPanel() {
    return {
      density: $("v7-o-density").value,
      font: parseInt($("v7-o-font").value, 10),
      side: parseInt($("v7-o-side").value, 10),
      gap: parseInt($("v7-o-gap").value, 10),
      kline: parseInt($("v7-o-kline").value, 10),
      row: parseInt($("v7-o-row").value, 10)
    };
  }

  function preview() {
    var o = readPanel();
    document.body.style.setProperty("--v7-font", o.font + "px");
    document.body.style.setProperty("--v7-side-width", o.side + "px");
    document.body.style.setProperty("--v7-gap", o.gap + "px");
    document.body.style.setProperty("--v7-kline-h", o.kline + "px");
    document.body.style.setProperty("--v7-row-h", o.row + "px");
    var pv = $("v7-pv-kline");
    if (pv) { pv.style.height = Math.round(o.kline * 0.19) + "px"; }
  }

  function openPanel() {
    if (!panel) { buildPanel(); }
    panel.hidden = false;
    preview();
  }

  function closePanel() {
    if (!panel) { return; }
    panel.hidden = true;
    var mode = currentLayout();
    applyLayout(mode === "custom" ? "custom" : mode);
  }

  /* ---------- 5. 顶栏标的搜索 ---------- */
  function bindSearch() {
    var input = $("v7-search");
    var list = $("v7-search-list");
    if (!input || !list) { return; }
    var timer = null;

    function render(items) {
      list.innerHTML = items.length
        ? items.map(function (x) {
            return '<div class="v7-search-item" data-symbol="' + x.symbol + '" data-name="' + x.name + '">' +
              "<b>" + x.symbol + "</b><span>" + x.name + " · " + (x.market || "") + "</span></div>";
          }).join("")
        : '<div class="v7-search-item"><b>无匹配</b><span>换个代码或名称试试</span></div>';
      list.hidden = false;
    }

    input.addEventListener("input", function () {
      var q = input.value.trim();
      if (!q) { list.hidden = true; return; }
      clearTimeout(timer);
      timer = setTimeout(function () {
        fetch("/api/market/search?q=" + encodeURIComponent(q), { cache: "no-store" })
          .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
          .then(function (j) {
            var items = (j && j.data && (j.data.items || j.data.results)) || [];
            render(items.length ? items.slice(0, 8) : DEMO_SEARCH.filter(function (x) {
              return (x.symbol + x.name).indexOf(q) >= 0;
            }));
          })
          .catch(function () {
            render(DEMO_SEARCH.filter(function (x) {
              return (x.symbol + x.name).toLowerCase().indexOf(q.toLowerCase()) >= 0;
            }));
          });
      }, 220);
    });

    list.addEventListener("click", function (event) {
      var item = event.target.closest(".v7-search-item");
      if (!item || !item.getAttribute("data-symbol")) { return; }
      var symbol = item.getAttribute("data-symbol");
      var name = item.getAttribute("data-name");
      list.hidden = true;
      input.value = symbol;
      try { localStorage.setItem("openbb-symbol", symbol); } catch (e) { /* noop */ }
      location.hash = "#/stock";
      toast("已切换标的：" + symbol + " " + name);
    });

    document.addEventListener("click", function (e) {
      if (!e.target.closest(".v7-search")) { list.hidden = true; }
    });
  }

  /* ---------- 6. 提醒铃铛 ---------- */
  function bindBell() {
    var bell = $("v7-bell");
    var dot = $("v7-bell-dot");
    if (!bell) { return; }
    bell.addEventListener("click", function () { location.hash = "#/alerts"; });
    fetch("/api/alerts/events", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (j) {
        var events = (j && j.data && (j.data.events || j.data.items)) || [];
        var unread = events.filter(function (e) { return !e.read; }).length;
        if (unread > 0) { dot.hidden = false; dot.textContent = unread > 99 ? "99+" : String(unread); }
      })
      .catch(function () {
        dot.hidden = false;
        dot.textContent = "3";   // 演示稿：真实接口不可达时给固定角标
      });
  }

  /* ---------- 7. 健康 / 版本徽章 ---------- */
  function bindBadges() {
    var health = $("v7-health");
    var build = $("v7-build");
    fetch("/api/health", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (j) {
        var data = (j && j.data) || {};
        var ok = data.status === "ok";
        health.className = "v7-badge" + (ok ? "" : " warn");
        health.innerHTML = '<i class="dot"></i>' + (ok ? "数据正常" : "降级中");
        if (data.build) { build.textContent = data.build; build.title = "构建标识（/api/health）"; }
      })
      .catch(function () {
        health.className = "v7-badge warn";
        health.innerHTML = '<i class="dot"></i>演示稿';
        build.textContent = "v3.2.0";
      });
  }

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

  /* ---------- 统一请求（401 跳登录，D-19） ---------- */
  function apiJson(path, opts) {
    var conf = Object.assign({ headers: { "Content-Type": "application/json" } }, opts || {});
    return fetch(path, conf).then(function (resp) {
      if (resp.status === 401) { location.href = "/auth"; return { ok: false, body: null, status: 401 }; }
      return resp.json().then(function (body) { return { ok: resp.ok, body: body, status: resp.status }; })
        .catch(function () { return { ok: resp.ok, body: null, status: resp.status }; });
    });
  }

  function queryParam(name) {
    var m = String(location.hash || "").split("?")[1] || "";
    var hit = new RegExp("(?:^|&)" + name + "=([^&]*)").exec(m);
    return hit ? decodeURIComponent(hit[1]) : "";
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

  /* ---------- 9. 列表与表单写操作（真实接口） ---------- */
  function bindActions() {
    document.addEventListener("click", function (event) {
      var btn = event.target.closest("[data-act]");
      if (!btn) { return; }
      var act = btn.getAttribute("data-act");
      var id = btn.getAttribute("data-id");

      if (act === "pf-add") { location.hash = "#/portfolio-edit"; return; }
      if (act === "pf-del") {
        var pfSel = $("v7-pf-select");
        if (!pfSel || !pfSel.value) { toast("请先选择持仓"); return; }
        if (btn.getAttribute("data-armed") === "1") {
          apiJson("/api/portfolio/" + encodeURIComponent(pfSel.value), { method: "DELETE" })
            .then(function (r) {
              if (r.ok && r.body && !r.body.error) {
                toast("持仓已删除");
                window.dispatchEvent(new HashChangeEvent("hashchange"));
              } else { toast((r.body && r.body.error) || "删除失败"); }
            });
          return;
        }
        btn.setAttribute("data-armed", "1");
        btn.textContent = "确认删除？";
        return;
      }
      if (act === "cn-on" || act === "cn-off") {
        var cnSel = $("v7-cn-select");
        if (!cnSel || !cnSel.value) { toast("请先选择数据源"); return; }
        apiJson("/api/connectors/toggle", { method: "POST", body: JSON.stringify({
          source: cnSel.value, enabled: act === "cn-on" }) })
          .then(function (r) {
            var ok = r.ok && r.body && !r.body.error;
            toast(ok ? (act === "cn-on" ? "已启用 " : "已停用 ") + cnSel.value
              : ((r.body && r.body.error) || "操作失败"));
          });
        return;
      }
      if (act === "about-log") {
        window.open("/api/logs/export", "_blank");
        toast("日志导出已开始下载");
        return;
      }
      if (act === "about-reset") {
        if (btn.getAttribute("data-armed") !== "1") {
          btn.setAttribute("data-armed", "1");
          btn.textContent = "确认恢复出厂？";
          return;
        }
        apiJson("/api/factory-reset", { method: "POST" }).then(function (r) {
          var ok = r.ok && r.body && !r.body.error;
          toast(ok ? "已恢复出厂设置" : ((r.body && r.body.error) || "失败"));
        });
        return;
      }
      if (act === "pref-save") {
        var pref = $("v7-pref-close");
        apiJson("/api/system/desktop-prefs", { method: "PUT", body: JSON.stringify({
          close_action: pref ? pref.value : "tray" }) })
          .then(function (r) {
            var ok = r.ok && r.body && !r.body.error;
            toast(ok ? "偏好已保存" : ((r.body && r.body.error) || "保存失败"));
          });
        return;
      }

      if (act === "watch-add") { location.hash = "#/watchlist-edit"; return; }
      if (act === "watch-edit-sel") {
        var sel = $("v7-wl-select");
        if (!sel || !sel.value) { toast("请先选择标的"); return; }
        location.hash = "#/watchlist-edit?id=" + encodeURIComponent(sel.value);
        return;
      }
      if (act === "watch-del-sel") {
        var sel2 = $("v7-wl-select");
        if (!sel2 || !sel2.value) { toast("请先选择标的"); return; }
        if (btn.getAttribute("data-armed") === "1") {
          apiJson("/api/watchlist/" + encodeURIComponent(sel2.value) + "?market=a", { method: "DELETE" })
            .then(function (r) {
              if (r.ok && r.body && !r.body.error) {
                toast("已删除 " + sel2.value);
                window.dispatchEvent(new HashChangeEvent("hashchange"));
              } else { toast((r.body && r.body.error) || "删除失败"); }
            });
          return;
        }
        btn.setAttribute("data-armed", "1");
        btn.textContent = "确认删除？";
        return;
      }
      if (act === "watch-edit") { location.hash = "#/watchlist-edit?id=" + encodeURIComponent(id); return; }
      if (act === "watch-del") {
        if (btn.getAttribute("data-armed") === "1") {
          toast("已删除 " + id + "（原型演示，未写后端）");
          var tr = btn.closest("tr");
          if (tr) { tr.style.opacity = ".35"; }
          return;
        }
        btn.setAttribute("data-armed", "1");
        btn.textContent = "确认删除？";
        toast("再次点击确认删除 " + id);
        return;
      }

      if (act === "edit-save") {
        saveEdit(btn.getAttribute("data-kind"), $("v7-form-hint"));
        return;
      }
      if (act === "edit-cancel") {
        if (history.length > 1) { history.back(); } else { location.hash = "#/home"; }
        return;
      }
      if (act === "edit-test") {
        apiJson("/api/alerts/evaluate?force=true", { method: "POST" }).then(function (r) {
          toast(r.ok ? "已执行一轮规则评估" : "评估失败");
        });
        return;
      }
      if (act === "edit-delete") {
        var ruleId = queryParam("id");
        if (!ruleId) { toast("缺少规则 ID"); return; }
        apiJson("/api/alerts/" + encodeURIComponent(ruleId), { method: "DELETE" }).then(function (r) {
          if (r.ok && r.body && !r.body.error) { toast("规则已删除"); setTimeout(function () { location.hash = "#/alerts"; }, 600); }
          else { toast((r.body && r.body.error) || "删除失败"); }
        });
        return;
      }
      if (act === "watch-add") { location.hash = "#/watchlist-edit"; return; }
      if (act === "watch-edit-sel") {
        var sel = $("v7-wl-select");
        if (!sel || !sel.value) { toast("请先选择标的"); return; }
        location.hash = "#/watchlist-edit?id=" + encodeURIComponent(sel.value);
        return;
      }
      if (act === "watch-del-sel") {
        var sel2 = $("v7-wl-select");
        if (!sel2 || !sel2.value) { toast("请先选择标的"); return; }
        if (btn.getAttribute("data-armed") === "1") {
          apiJson("/api/watchlist/" + encodeURIComponent(sel2.value) + "?market=a", { method: "DELETE" })
            .then(function (r) {
              if (r.ok && r.body && !r.body.error) {
                toast("已删除 " + sel2.value);
                window.dispatchEvent(new HashChangeEvent("hashchange"));
              } else { toast((r.body && r.body.error) || "删除失败"); }
            });
          return;
        }
        btn.setAttribute("data-armed", "1");
        btn.textContent = "确认删除？";
        return;
      }
      if (act === "watch-edit") { location.hash = "#/watchlist-edit?id=" + encodeURIComponent(id); return; }
      if (act === "watch-del") {
        if (btn.getAttribute("data-armed") === "1") {
          toast("已删除 " + id + "（原型演示，未写后端）");
          var tr = btn.closest("tr");
          if (tr) { tr.style.opacity = ".35"; }
          return;
        }
        btn.setAttribute("data-armed", "1");
        btn.textContent = "确认删除？";
        toast("再次点击确认删除 " + id);
        return;
      }

      if (act === "edit-save") {
        saveEdit(btn.getAttribute("data-kind"), $("v7-form-hint"));
        return;
      }
      if (act === "edit-cancel") {
        if (history.length > 1) { history.back(); } else { location.hash = "#/home"; }
        return;
      }
      if (act === "edit-test") { toast("测试提醒已发送到提醒中心（原型演示）"); return; }
      if (act === "edit-delete") { toast("规则已删除（原型演示，未写后端）"); return; }

      if (act === "import-check") {
        if (!importRows || !importRows.length) { toast("请先选择 .csv / .xlsx 文件"); return; }
        renderImport(importRows, importFileName);
        return;
      }
      if (act === "import-confirm") {
        var okRows = (importRows || []).filter(function (r) { return r.ok; });
        if (!okRows.length) { toast("没有可导入的行"); return; }
        if (importBase64) {
          apiJson("/api/portfolio/import-xlsx", { method: "POST", body: JSON.stringify({
            filename: importFileName, content_base64: importBase64, confirm: true }) })
            .then(function (r) {
              if (r.ok && r.body && !r.body.error) { toast("已导入台账"); setTimeout(function () { location.hash = "#/portfolio"; }, 700); }
              else { toast((r.body && r.body.error) || "导入失败"); }
            });
          return;
        }
        var okN = 0, badN = 0;
        okRows.forEach(function (row) {
          apiJson("/api/portfolio", { method: "POST", body: JSON.stringify({
            market: /^\d{6}$/.test(row.code) ? "a" : "fund",
            symbol: row.code, shares: parseFloat(row.qty), cost: parseFloat(row.cost) }) })
            .then(function (r) {
              if (r.ok && r.body && !r.body.error) { okN += 1; } else { badN += 1; }
              if (okN + badN === okRows.length) {
                toast("导入完成：" + okN + " 成功 / " + badN + " 失败");
                if (okN) { setTimeout(function () { location.hash = "#/portfolio"; }, 800); }
              }
            });
        });
        return;
      }
      if (act === "watch-add") { location.hash = "#/watchlist-edit"; return; }
      if (act === "watch-edit-sel") {
        var sel = $("v7-wl-select");
        if (!sel || !sel.value) { toast("请先选择标的"); return; }
        location.hash = "#/watchlist-edit?id=" + encodeURIComponent(sel.value);
        return;
      }
      if (act === "watch-del-sel") {
        var sel2 = $("v7-wl-select");
        if (!sel2 || !sel2.value) { toast("请先选择标的"); return; }
        if (btn.getAttribute("data-armed") === "1") {
          apiJson("/api/watchlist/" + encodeURIComponent(sel2.value) + "?market=a", { method: "DELETE" })
            .then(function (r) {
              if (r.ok && r.body && !r.body.error) {
                toast("已删除 " + sel2.value);
                window.dispatchEvent(new HashChangeEvent("hashchange"));
              } else { toast((r.body && r.body.error) || "删除失败"); }
            });
          return;
        }
        btn.setAttribute("data-armed", "1");
        btn.textContent = "确认删除？";
        return;
      }
      if (act === "watch-edit") { location.hash = "#/watchlist-edit?id=" + encodeURIComponent(id); return; }
      if (act === "watch-del") {
        if (btn.getAttribute("data-armed") === "1") {
          toast("已删除 " + id + "（原型演示，未写后端）");
          var tr = btn.closest("tr");
          if (tr) { tr.style.opacity = ".35"; }
          return;
        }
        btn.setAttribute("data-armed", "1");
        btn.textContent = "确认删除？";
        toast("再次点击确认删除 " + id);
        return;
      }

      if (act === "edit-save") {
        saveEdit(btn.getAttribute("data-kind"), $("v7-form-hint"));
        return;
      }
      if (act === "edit-cancel") {
        if (history.length > 1) { history.back(); } else { location.hash = "#/home"; }
        return;
      }
      if (act === "edit-test") {
        apiJson("/api/alerts/evaluate?force=true", { method: "POST" }).then(function (r) {
          toast(r.ok ? "已执行一轮规则评估" : "评估失败");
        });
        return;
      }
      if (act === "edit-delete") {
        var ruleId = queryParam("id");
        if (!ruleId) { toast("缺少规则 ID"); return; }
        apiJson("/api/alerts/" + encodeURIComponent(ruleId), { method: "DELETE" }).then(function (r) {
          if (r.ok && r.body && !r.body.error) { toast("规则已删除"); setTimeout(function () { location.hash = "#/alerts"; }, 600); }
          else { toast((r.body && r.body.error) || "删除失败"); }
        });
        return;
      }
      if (act === "watch-add") { location.hash = "#/watchlist-edit"; return; }
      if (act === "watch-edit-sel") {
        var sel = $("v7-wl-select");
        if (!sel || !sel.value) { toast("请先选择标的"); return; }
        location.hash = "#/watchlist-edit?id=" + encodeURIComponent(sel.value);
        return;
      }
      if (act === "watch-del-sel") {
        var sel2 = $("v7-wl-select");
        if (!sel2 || !sel2.value) { toast("请先选择标的"); return; }
        if (btn.getAttribute("data-armed") === "1") {
          apiJson("/api/watchlist/" + encodeURIComponent(sel2.value) + "?market=a", { method: "DELETE" })
            .then(function (r) {
              if (r.ok && r.body && !r.body.error) {
                toast("已删除 " + sel2.value);
                window.dispatchEvent(new HashChangeEvent("hashchange"));
              } else { toast((r.body && r.body.error) || "删除失败"); }
            });
          return;
        }
        btn.setAttribute("data-armed", "1");
        btn.textContent = "确认删除？";
        return;
      }
      if (act === "watch-edit") { location.hash = "#/watchlist-edit?id=" + encodeURIComponent(id); return; }
      if (act === "watch-del") {
        if (btn.getAttribute("data-armed") === "1") {
          toast("已删除 " + id + "（原型演示，未写后端）");
          var tr = btn.closest("tr");
          if (tr) { tr.style.opacity = ".35"; }
          return;
        }
        btn.setAttribute("data-armed", "1");
        btn.textContent = "确认删除？";
        toast("再次点击确认删除 " + id);
        return;
      }

      if (act === "edit-save") {
        saveEdit(btn.getAttribute("data-kind"), $("v7-form-hint"));
        return;
      }
      if (act === "edit-cancel") {
        if (history.length > 1) { history.back(); } else { location.hash = "#/home"; }
        return;
      }
      if (act === "edit-test") { toast("测试提醒已发送到提醒中心（原型演示）"); return; }
      if (act === "edit-delete") { toast("规则已删除（原型演示，未写后端）"); return; }

      if (act === "import-check") {
        if (!importRows || !importRows.length) { toast("请先选择 .csv / .xlsx 文件"); return; }
        renderImport(importRows, importFileName);
        return;
      }
      if (act === "import-confirm") {
        var okN = (importRows || []).filter(function (r) { return r.ok; }).length;
        if (!okN) { toast("没有可导入的行"); return; }
        toast("已导入 " + okN + " 条（原型演示，未写后端）");
        return;
      }
    });
  }

  /* ---------- 10. 详情页内标的切换（W7） ---------- */
  var SYM_MAP = {
    stock: { key: "symbol", api: ["p"] },
    fin: { key: "symbol", api: ["f"] },
    charting: { key: "symbol", api: ["p", "v"] },
    fund: { key: "code", api: ["f"] },
    mgr: { key: "code", api: ["f"] }
  };

  function currentId() {
    return (location.hash || "").replace(/^#\//, "").split("?")[0] || "home";
  }

  /** 在 view.api 的所有路径中查找并替换 symbol/code 参数（registry 会重写 api 的键名）。 */
  function apiPaths(view, key) {
    var out = [];
    if (!view || !view.api) { return out; }
    Object.keys(view.api).forEach(function (k) {
      var pair = view.api[k];
      if (pair && typeof pair[0] === "string" && pair[0].indexOf(key + "=") >= 0) {
        out.push(pair);
      }
    });
    return out;
  }

  function injectSymbolBar() {
    var id = currentId();
    var conf = SYM_MAP[id];
    if (!conf) { return; }
    var row = document.querySelector(".view-title-row");
    if (!row || row.querySelector(".v7-sym")) { return; }
    var view = (window.V6_VIEWS || {})[id] || {};
    var paths = apiPaths(view, conf.key);
    var m = paths.length ? String(paths[0][0]).match(new RegExp(conf.key + "=([^&]+)")) : null;
    var cur = m ? decodeURIComponent(m[1]) : "";
    var span = document.createElement("span");
    span.className = "v7-sym";
    span.innerHTML = '<input id="v7-sym-input" value="' + cur + '" aria-label="切换标的">' +
      '<button class="v7-btn" id="v7-sym-go" type="button">切换</button>';
    row.appendChild(span);
    $("v7-sym-go").addEventListener("click", function () {
      var val = ($("v7-sym-input").value || "").trim();
      if (!val) { return; }
      paths.forEach(function (pair) {
        pair[0] = pair[0].replace(new RegExp(conf.key + "=[^&]*"), conf.key + "=" + encodeURIComponent(val));
      });
      toast("已切换至 " + val);
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
  }

  function watchRoute() {
    function afterRoute() {
      injectSymbolBar();
      injectWatchToolbar();
      bindImport();
      injectTableTools();
      injectPortfolioToolbar();
      injectConnectorsToolbar();
      injectSettingsPrefs();
      injectKlineTools();
      injectAboutCard();
      injectDashboardDrag();
    }
    window.addEventListener("hashchange", function () { setTimeout(afterRoute, 460); });
    setTimeout(afterRoute, 1300);
  }

  /* ---------- 11. 组件参数（W8） ---------- */
  function bindWidgetParams() {
    var modal = $("v6-add-modal");
    if (!modal) { return; }
    new MutationObserver(function () {
      if (modal.hidden) { return; }
      var body = modal.querySelector(".v6-modal-body");
      if (!body || body.querySelector(".v7-params")) { return; }
      var box = document.createElement("div");
      box.className = "v7-params";
      box.innerHTML =
        '<div class="v7-field"><span>标的（可选）</span>' +
          '<input id="v7p-symbol" placeholder="如 600519" style="width:120px"></div>' +
        '<div class="v7-field"><span>周期</span><select id="v7p-period">' +
          '<option>近 5 日</option><option>近 20 日</option><option>近 60 日</option></select></div>' +
        '<div class="v7-field"><span>板块（可选）</span><select id="v7p-board">' +
          '<option>不限</option><option>半导体</option><option>新能源</option></select></div>';
      body.appendChild(box);
      var note = modal.querySelector(".v6-modal-note");
      if (note) {
        note.textContent = "参数随组件一并保存到仪表板（PUT /api/dashboards）；原型演示不写后端。";
      }
    }).observe(modal, { attributes: true, attributeFilter: ["hidden"] });
  }

  /* ---------- 12. 自选管理工具栏（保留双卡） ---------- */
  var DEMO_WL = [
    { code: "600519", name: "贵州茅台" }, { code: "300750", name: "宁德时代" },
    { code: "017811", name: "易方达蓝筹精选" }
  ];

  function fillWatchOptions() {
    var sel = $("v7-wl-select");
    if (!sel) { return; }
    function render(items) {
      sel.innerHTML = '<option value="">选择标的…</option>' + items.map(function (x) {
        return '<option value="' + x.code + '">' + x.code + " " + x.name + "</option>";
      }).join("");
    }
    fetch("/api/watchlist", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (j) {
        var items = (j && j.data && j.data.items) || [];
        render(items.length ? items : DEMO_WL);
      })
      .catch(function () { render(DEMO_WL); });
  }

  function injectWatchToolbar() {
    if (currentId() !== "watchlist") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-toolbar")) { return; }
    var bar = document.createElement("div");
    bar.className = "v7-toolbar";
    bar.innerHTML =
      '<button class="v7-btn primary" data-act="watch-add" type="button">＋ 添加自选</button>' +
      '<select id="v7-wl-select"><option value="">选择标的…</option></select>' +
      '<button class="v7-btn" data-act="watch-edit-sel" type="button">编辑</button>' +
      '<button class="v7-btn danger" data-act="watch-del-sel" type="button">删除</button>' +
      '<span class="v7-hint">W-01 / W-02 双卡保持不变，管理操作集中在工具栏</span>';
    body.insertBefore(bar, body.firstChild);
    fillWatchOptions();
  }

  /* ---------- 13. 真实文件导入（CSV 解析；xlsx 需服务端） ---------- */
  var importRows = null;
  var importFileName = "";

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function parseCsv(text) {
    var lines = String(text || "").replace(/\r/g, "").split("\n").filter(function (l) { return l.trim(); });
    if (lines.length < 2) { return []; }
    var head = lines[0].split(/[,\t]/).map(function (s) { return s.trim(); });
    var idx = { code: -1, name: -1, qty: -1, cost: -1 };
    head.forEach(function (h, i) {
      if (/代码|code/i.test(h)) { idx.code = i; }
      else if (/名称|name/i.test(h)) { idx.name = i; }
      else if (/数量|qty|shares/i.test(h)) { idx.qty = i; }
      else if (/成本|cost|price/i.test(h)) { idx.cost = i; }
    });
    if (idx.code < 0) { return []; }
    return lines.slice(1).map(function (line) {
      var c = line.split(/[,\t]/);
      var qty = (c[idx.qty] || "").trim();
      var cost = (c[idx.cost] || "").trim();
      var okQty = /^\d+(\.\d+)?$/.test(qty);
      var okCost = /^\d+(\.\d+)?$/.test(cost);
      return {
        code: (c[idx.code] || "").trim(),
        name: idx.name >= 0 ? (c[idx.name] || "").trim() : "",
        qty: qty, cost: cost,
        ok: okQty && okCost,
        note: !okQty ? "数量非法" : (!okCost ? "成本非法" : "可导入")
      };
    });
  }

  function renderImport(rows, fileName) {
    var box = $("v7-import-preview");
    if (!box) { return; }
    var okN = rows.filter(function (r) { return r.ok; }).length;
    box.hidden = false;
    box.innerHTML = "<table><tr><th>代码</th><th>名称</th><th>数量</th><th>成本</th><th>状态</th></tr>" +
      rows.map(function (r) {
        return "<tr><td>" + esc(r.code) + "</td><td>" + esc(r.name) + "</td><td>" + esc(r.qty) +
          "</td><td>" + esc(r.cost) + '</td><td class="' + (r.ok ? "ok" : "bad") + '">' + r.note + "</td></tr>";
      }).join("") + "</table>";
    var cf = document.querySelector('[data-act="import-confirm"]');
    if (cf) { cf.disabled = okN === 0; }
    var ih = $("v7-import-hint");
    if (ih) { ih.textContent = rows.length + " 行：" + okN + " 可导入 / " + (rows.length - okN) + " 错误" + (fileName ? " · " + fileName : ""); }
  }

  var importBase64 = "";

  function handleFile(file) {
    if (!file) { return; }
    importFileName = file.name;
    importBase64 = "";
    var ext = (file.name.split(".").pop() || "").toLowerCase();
    var info = $("v7-file-info");
    if (info) { info.textContent = "已接收：" + file.name + "（" + (file.size / 1024).toFixed(1) + " KB）"; }
    if (ext === "csv" || ext === "tsv") {
      var reader = new FileReader();
      reader.onload = function () {
        importRows = parseCsv(String(reader.result || ""));
        if (importRows.length) {
          renderImport(importRows, file.name);
        } else {
          var ih = $("v7-import-hint");
          if (ih) { ih.textContent = "未识别到「代码」列或数据为空，请检查表头"; }
        }
      };
      reader.readAsText(file, "utf-8");
      return;
    }
    var fr = new FileReader();
    fr.onload = function () {
      var dataUrl = String(fr.result || "");
      importBase64 = dataUrl.indexOf(",") >= 0 ? dataUrl.split(",")[1] : "";
      apiJson("/api/portfolio/import-xlsx", { method: "POST", body: JSON.stringify({
        filename: file.name, content_base64: importBase64, confirm: false }) })
        .then(function (r) {
          var body = (r.body && r.body.data) || {};
          var rows = body.rows || [];
          var errs = body.errors || [];
          importRows = rows.map(function (x) {
            return { code: x.symbol || x.code || "—", name: x.name || "—",
              qty: x.shares != null ? x.shares : (x.qty != null ? x.qty : "—"),
              cost: x.cost != null ? x.cost : "—", ok: true, note: "可导入" };
          });
          errs.forEach(function (e) {
            importRows.push({ code: e.symbol || "—", name: e.name || "—", qty: "—", cost: "—",
              ok: false, note: e.reason || e.message || "校验失败" });
          });
          if (importRows.length) { renderImport(importRows, file.name); }
          else {
            var ih = $("v7-import-hint");
            if (ih) { ih.textContent = (r.body && r.body.error) || "未解析到数据行"; }
          }
        });
    };
    fr.readAsDataURL(file);
  }

  function bindImport() {
    var drop = $("v7-drop");
    var input = $("v7-file");
    if (!drop || !input || drop.getAttribute("data-bound") === "1") { return; }
    drop.setAttribute("data-bound", "1");
    drop.addEventListener("click", function () { input.click(); });
    input.addEventListener("change", function () { handleFile(input.files && input.files[0]); });
    ["dragenter", "dragover"].forEach(function (name) {
      drop.addEventListener(name, function (e) { e.preventDefault(); drop.classList.add("drag"); });
    });
    ["dragleave", "drop"].forEach(function (name) {
      drop.addEventListener(name, function (e) { e.preventDefault(); drop.classList.remove("drag"); });
    });
    drop.addEventListener("drop", function (e) {
      var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) { handleFile(f); }
    });
  }

  /* ---------- 14. 表格工具：导出 / 排序 / 分页 / 分享（D-08 / D-18） ---------- */
  function injectTableTools() {
    var row = document.querySelector(".view-title-row");
    if (!row) { return; }
    if (!row.querySelector(".v7-share")) {
      var share = document.createElement("button");
      share.className = "v7-btn v7-share";
      share.type = "button";
      share.textContent = "复制链接";
      share.addEventListener("click", function () {
        var url = location.href;
        if (navigator.clipboard) { navigator.clipboard.writeText(url); }
        toast("链接已复制：" + url.slice(0, 64));
      });
      row.appendChild(share);
    }
    var table = document.querySelector("#view-body table");
    if (!table || row.querySelector(".v7-export")) { return; }
    var exp = document.createElement("button");
    exp.className = "v7-btn v7-export";
    exp.type = "button";
    exp.textContent = "导出 CSV";
    exp.addEventListener("click", function () {
      var lines = [];
      Array.prototype.forEach.call(table.querySelectorAll("tr"), function (tr) {
        var cells = [];
        Array.prototype.forEach.call(tr.querySelectorAll("th,td"), function (cell) {
          cells.push('"' + String(cell.innerText || "").replace(/"/g, '""').trim() + '"');
        });
        if (cells.length) { lines.push(cells.join(",")); }
      });
      if (!lines.length) { toast("表格为空"); return; }
      var blob = new Blob(["\ufeff" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = (currentId() || "export") + ".csv";
      a.click();
      URL.revokeObjectURL(a.href);
      toast("已导出 CSV（" + (lines.length - 1) + " 行）");
    });
    row.appendChild(exp);

    // 排序：点击表头
    Array.prototype.forEach.call(table.querySelectorAll("thead th"), function (th, idx) {
      th.style.cursor = "pointer";
      th.title = "点击排序";
      th.addEventListener("click", function () {
        var tbody = table.querySelector("tbody");
        var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr"));
        var asc = th.getAttribute("data-asc") !== "1";
        th.setAttribute("data-asc", asc ? "1" : "0");
        rows.sort(function (a, b) {
          var av = (a.children[idx] || {}).innerText || "";
          var bv = (b.children[idx] || {}).innerText || "";
          var an = parseFloat(String(av).replace(/[^\d.\-]/g, ""));
          var bn = parseFloat(String(bv).replace(/[^\d.\-]/g, ""));
          if (isFinite(an) && isFinite(bn)) { return asc ? an - bn : bn - an; }
          return asc ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av));
        });
        rows.forEach(function (tr) { tbody.appendChild(tr); });
      });
    });
  }

  /* ---------- 15. 持仓删除入口（D-12） ---------- */
  function injectPortfolioToolbar() {
    if (currentId() !== "portfolio") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-toolbar")) { return; }
    var bar = document.createElement("div");
    bar.className = "v7-toolbar";
    bar.innerHTML = '<select id="v7-pf-select"><option value="">选择持仓…</option></select>' +
      '<button class="v7-btn danger" data-act="pf-del" type="button">删除持仓</button>' +
      '<button class="v7-btn primary" data-act="pf-add" type="button">＋ 添加持仓</button>' +
      '<span class="v7-hint">删除会真实写入 data/portfolio.json</span>';
    body.insertBefore(bar, body.firstChild);
    fetch("/api/portfolio", { headers: { "Content-Type": "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        var items = ((j && j.data && j.data.items) || []);
        var sel = $("v7-pf-select");
        if (sel) {
          sel.innerHTML = '<option value="">选择持仓…</option>' + items.map(function (x) {
            return '<option value="' + x.id + '">' + (x.symbol || "") + " " + (x.name || "") + "</option>";
          }).join("");
        }
      }).catch(function () { /* 静默 */ });
  }

  /* ---------- 16. 连接器开关 / 设置保存（D-01 剩余） ---------- */
  function injectConnectorsToolbar() {
    if (currentId() !== "connectors") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-toolbar")) { return; }
    var bar = document.createElement("div");
    bar.className = "v7-toolbar";
    bar.innerHTML = '<select id="v7-cn-select"><option value="">选择数据源…</option></select>' +
      '<button class="v7-btn" data-act="cn-on" type="button">启用</button>' +
      '<button class="v7-btn danger" data-act="cn-off" type="button">停用</button>';
    body.insertBefore(bar, body.firstChild);
    fetch("/api/connectors/status", { headers: { "Content-Type": "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        var list = ((j && j.data && j.data.datasources) || []);
        var sel = $("v7-cn-select");
        if (sel) {
          sel.innerHTML = '<option value="">选择数据源…</option>' + list.map(function (x) {
            return '<option value="' + esc(x.label || x.source || "") + '">' + esc(x.label || x.source || "") + "</option>";
          }).join("");
        }
      }).catch(function () { /* 静默 */ });
  }

  function injectSettingsPrefs() {
    if (currentId() !== "settings") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-toolbar")) { return; }
    var bar = document.createElement("div");
    bar.className = "v7-toolbar";
    bar.innerHTML = '<span>关闭窗口行为</span>' +
      '<select id="v7-pref-close"><option value="tray">最小化到托盘</option><option value="quit">直接退出</option></select>' +
      '<button class="v7-btn primary" data-act="pref-save" type="button">保存</button>';
    body.insertBefore(bar, body.firstChild);
    fetch("/api/system/desktop-prefs", { headers: { "Content-Type": "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        var v = (j && j.data && j.data.close_action) || "tray";
        var sel = $("v7-pref-close");
        if (sel) { sel.value = v; }
      }).catch(function () { /* 静默 */ });
  }

  /* ---------- 17. 仪表板持久化（D-03 / D-07 参数） ---------- */
  var pendingWidgetId = "";

  function bindDashboardPersist() {
    document.addEventListener("click", function (event) {
      var addBtn = event.target.closest ? event.target.closest(".widget-add") : null;
      if (addBtn && addBtn.getAttribute("data-id")) { pendingWidgetId = addBtn.getAttribute("data-id"); return; }
      if (event.target && event.target.id === "v6-add-ok" && pendingWidgetId) {
        var wid = pendingWidgetId;
        pendingWidgetId = "";
        var psym = $("v7p-symbol"), pper = $("v7p-period"), pbrd = $("v7p-board");
        var params = { symbol: psym ? (psym.value || "").trim() : "",
          period: pper ? pper.value : "", board: pbrd ? pbrd.value : "" };
        fetch("/api/dashboards", { headers: { "Content-Type": "application/json" } })
          .then(function (r) { return r.json(); })
          .then(function (j) {
            var data = (j && j.data) || {};
            var list = data.dashboards || [];
            var target = list.filter(function (x) { return x.id === data.activeId; })[0] || list[0];
            if (!target) { toast("没有可用的仪表板配置"); return; }
            target.widgets = target.widgets || [];
            if (!target.widgets.some(function (w) { return w.id === wid; })) {
              target.widgets.push({ id: wid, span: 48, params: params });
            }
            apiJson("/api/dashboards", { method: "PUT", body: JSON.stringify({
              dashboards: list, activeId: data.activeId, groups: data.groups || [] }) })
              .then(function (res) {
                var ok = res.ok && res.body && !res.body.error;
                toast(ok ? "组件已保存到仪表板（刷新不丢）" : ((res.body && res.body.error) || "仪表板保存失败"));
              });
          });
      }
    }, true);
  }

  /* ---------- 18. K 线周期 + 技术指标（恢复旧版能力） ---------- */
  var PERIODS = ["日k", "周k", "月k"];
  var INDICS = ["MA", "MACD", "KDJ"];

  function injectKlineTools() {
    var host = document.getElementById("kline-chart");
    if (!host || document.querySelector(".v7-kline-tools")) { return; }
    var card = host.closest(".card") || host.parentNode;
    var head = card ? card.querySelector(".card-head") : null;
    if (!head) { return; }
    var bar = document.createElement("span");
    bar.className = "v7-kline-tools";
    bar.innerHTML =
      PERIODS.map(function (x) {
        return '<button class="v7-tab" data-period="' + x + '" type="button">' +
          ({ "日k": "日K", "周k": "周K", "月k": "月K" }[x]) + "</button>";
      }).join("") +
      INDICS.map(function (x) {
        return '<button class="v7-tab" data-indicator="' + x + '" type="button">' + x + "</button>";
      }).join("");
    head.appendChild(bar);
    var cur = (window.V6_APP && window.V6_APP.getPeriod && window.V6_APP.getPeriod()) || "日k";
    var ind = (window.V6_APP && window.V6_APP.getIndicator && window.V6_APP.getIndicator()) || "MA";
    markPeriod(cur); markIndicator(ind);
  }

  function markPeriod(p) {
    Array.prototype.forEach.call(document.querySelectorAll('[data-period]'), function (b) {
      b.classList.toggle("on", b.getAttribute("data-period") === p);
    });
  }

  function markIndicator(i) {
    Array.prototype.forEach.call(document.querySelectorAll('[data-indicator]'), function (b) {
      b.classList.toggle("on", b.getAttribute("data-indicator") === i);
    });
  }

  function bindKlineTools() {
    document.addEventListener("click", function (event) {
      var pb = event.target.closest ? event.target.closest("[data-period]") : null;
      if (pb && window.V6_APP) {
        window.V6_APP.setPeriod(pb.getAttribute("data-period"));
        markPeriod(pb.getAttribute("data-period"));
        toast("已切换周期：" + pb.textContent);
        return;
      }
      var ib = event.target.closest ? event.target.closest("[data-indicator]") : null;
      if (ib && window.V6_APP) {
        window.V6_APP.setIndicator(ib.getAttribute("data-indicator"));
        markIndicator(ib.getAttribute("data-indicator"));
        toast("已切换指标：" + ib.textContent);
      }
    }, true);
  }

  /* ---------- 19. 右键菜单（恢复旧版 ctxmenu） ---------- */
  var ctxMenu = null;

  function closeCtx() {
    if (ctxMenu) { ctxMenu.remove(); ctxMenu = null; }
  }

  function openCtx(x, y, items) {
    closeCtx();
    ctxMenu = document.createElement("div");
    ctxMenu.className = "v7-ctx";
    ctxMenu.innerHTML = items.map(function (it, i) {
      return it === "-" ? '<hr>' : '<button type="button" data-i="' + i + '">' + esc(it.label) + "</button>";
    }).join("");
    document.body.appendChild(ctxMenu);
    var w = ctxMenu.offsetWidth, h = ctxMenu.offsetHeight;
    ctxMenu.style.left = Math.min(x, window.innerWidth - w - 6) + "px";
    ctxMenu.style.top = Math.min(y, window.innerHeight - h - 6) + "px";
    ctxMenu.addEventListener("click", function (event) {
      var btn = event.target.closest("button[data-i]");
      if (!btn) { return; }
      var item = items[+btn.getAttribute("data-i")];
      closeCtx();
      if (item && item.run) { item.run(); }
    });
  }

  function bindContextMenu() {
    document.addEventListener("contextmenu", function (event) {
      var tr = event.target.closest ? event.target.closest("#view-body table tbody tr") : null;
      if (tr) {
        event.preventDefault();
        var code = (tr.children[0] || {}).innerText || "";
        var name = (tr.children[1] || {}).innerText || "";
        openCtx(event.clientX, event.clientY, [
          { label: "查看详情", run: function () {
            localStorage.setItem("openbb-symbol", code.trim());
            location.hash = "#/stock";
          } },
          { label: "加入自选", run: function () {
            apiJson("/api/watchlist", { method: "POST", body: JSON.stringify({ symbol: code.trim(), market: "a", name: name.trim() }) })
              .then(function (r) { toast(r.ok && r.body && !r.body.error ? "已加入自选" : ((r.body && r.body.error) || "失败")); });
          } },
          { label: "设为提醒", run: function () { location.hash = "#/alerts-edit"; } },
          "-",
          { label: "复制代码", run: function () {
            if (navigator.clipboard) { navigator.clipboard.writeText(code.trim()); }
            toast("已复制：" + code.trim());
          } },
          { label: "复制整行", run: function () {
            var line = Array.prototype.map.call(tr.children, function (c) { return (c.innerText || "").trim(); }).join("\t");
            if (navigator.clipboard) { navigator.clipboard.writeText(line); }
            toast("已复制整行");
          } },
          { label: "导出该表", run: function () {
            var btn = document.querySelector(".v7-export");
            if (btn) { btn.click(); }
          } }
        ]);
        return;
      }
      var kl = event.target.closest ? event.target.closest("#kline-chart") : null;
      if (kl) {
        event.preventDefault();
        openCtx(event.clientX, event.clientY, [
          { label: "周期：日K", run: function () { window.V6_APP && window.V6_APP.setPeriod("日k"); markPeriod("日k"); } },
          { label: "周期：周K", run: function () { window.V6_APP && window.V6_APP.setPeriod("周k"); markPeriod("周k"); } },
          { label: "周期：月K", run: function () { window.V6_APP && window.V6_APP.setPeriod("月k"); markPeriod("月k"); } },
          "-",
          { label: "指标：MA", run: function () { window.V6_APP && window.V6_APP.setIndicator("MA"); markIndicator("MA"); } },
          { label: "指标：MACD", run: function () { window.V6_APP && window.V6_APP.setIndicator("MACD"); markIndicator("MACD"); } },
          { label: "指标：KDJ", run: function () { window.V6_APP && window.V6_APP.setIndicator("KDJ"); markIndicator("KDJ"); } },
          "-",
          { label: "复制当前链接", run: function () {
            if (navigator.clipboard) { navigator.clipboard.writeText(location.href); }
            toast("已复制链接");
          } }
        ]);
      }
    });
    document.addEventListener("click", closeCtx);
    document.addEventListener("scroll", closeCtx, true);
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") { closeCtx(); } });
  }

  /* ---------- 20. 关于卡（恢复旧版 settings S-05） ---------- */
  function injectAboutCard() {
    if (currentId() !== "settings") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-about")) { return; }
    var card = document.createElement("div");
    card.className = "card v7-about";
    card.innerHTML = '<div class="card-head"><span class="card-title"><span class="badge-ico">◈</span>关于</span>' +
      '<span class="card-note">版本 / 数据源 / 日志 / 恢复出厂</span></div>' +
      '<div class="v-body"><div class="v-kpi-grid" id="v7-about-kpis"></div>' +
      '<div class="v7-form-foot">' +
      '<button class="v7-btn" data-act="about-log" type="button">导出日志</button>' +
      '<button class="v7-btn danger" data-act="about-reset" type="button">恢复出厂设置</button>' +
      '</div></div>';
    body.appendChild(card);
    Promise.all([
      fetch("/api/health").then(function (r) { return r.json(); }).catch(function () { return {}; }),
      fetch("/api/connectors/status").then(function (r) { return r.json(); }).catch(function () { return {}; })
    ]).then(function (res) {
      var h = (res[0] && res[0].data) || {};
      var c = (res[1] && res[1].data) || {};
      var srcs = (c.datasources || []).length;
      var box = $("v7-about-kpis");
      if (box) {
        box.innerHTML = [
          ["版本", (h.app && h.app.version) || "—"],
          ["构建", h.build || "—"],
          ["数据源", srcs + " 个"],
          ["口径", h.caliber || "—"]
        ].map(function (kv) {
          return '<div class="v-kpi"><span class="k-label">' + kv[0] + '</span><span class="k-value">' + esc(kv[1]) + "</span></div>";
        }).join("");
      }
    });
  }

  /* ---------- 21. 仪表板栅格拖拽排序（恢复旧版 drag 能力，落库） ---------- */
  function bindGridDrag(grid, target, list, data) {
    var dragId = "";
    grid.addEventListener("dragstart", function (e) {
      var item = e.target.closest(".demo-widget");
      if (!item) { return; }
      dragId = item.getAttribute("data-id");
      item.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
    });
    grid.addEventListener("dragend", function () {
      Array.prototype.forEach.call(grid.querySelectorAll(".demo-widget"), function (x) {
        x.classList.remove("dragging");
      });
    });
    grid.addEventListener("dragover", function (e) {
      e.preventDefault();
      var over = e.target.closest(".demo-widget");
      if (!over || over.getAttribute("data-id") === dragId) { return; }
      var dragging = grid.querySelector('.demo-widget[data-id="' + dragId + '"]');
      if (!dragging || over === dragging) { return; }
      var rect = over.getBoundingClientRect();
      var after = (e.clientY - rect.top) > rect.height / 2;
      grid.insertBefore(dragging, after ? over.nextSibling : over);
    });
    grid.addEventListener("drop", function (e) {
      e.preventDefault();
      var order = Array.prototype.map.call(grid.querySelectorAll(".demo-widget"), function (x) {
        return x.getAttribute("data-id");
      });
      var byId = {};
      (target.widgets || []).forEach(function (w) { byId[w.id] = w; });
      target.widgets = order.map(function (id) { return byId[id] || { id: id, span: 48, params: {} }; });
      apiJson("/api/dashboards", { method: "PUT", body: JSON.stringify({
        dashboards: list, activeId: data.activeId, groups: data.groups || [] }) })
        .then(function (r) {
          var ok = r.ok && r.body && !r.body.error;
          toast(ok ? "布局顺序已保存" : ((r.body && r.body.error) || "保存失败"));
        });
    });
  }

  function injectDashboardDrag() {
    if (currentId() !== "dashboard") { return; }
    var grid = document.querySelector(".demo-dash-grid");
    if (!grid || grid.getAttribute("data-v7-bound") === "1") { return; }
    Promise.all([
      fetch("/api/dashboards").then(function (r) { return r.json(); }),
      fetch("/api/widgets").then(function (r) { return r.json(); })
    ]).then(function (res) {
      var data = (res[0] && res[0].data) || {};
      var list = data.dashboards || [];
      var target = list.filter(function (x) { return x.id === data.activeId; })[0] || list[0];
      if (!target) { return; }
      var meta = {};
      ((res[1] && res[1].data && res[1].data.widgets) || []).forEach(function (x) { meta[x.id] = x; });
      var ws = target.widgets || [];
      grid.innerHTML = ws.length ? ws.map(function (x) {
        var m = meta[x.id] || {};
        return '<div class="demo-widget" draggable="true" data-id="' + esc(x.id) + '" data-span="' + (x.span || 48) + '">' +
          "<b>" + esc(m.title || x.id) + '</b><span class="mono">' + esc(x.id) + " · span " + (x.span || 48) + "</span></div>";
      }).join("") : '<div class="v-empty">仪表板为空，从组件库添加</div>';
      grid.setAttribute("data-v7-bound", "1");
      bindGridDrag(grid, target, list, data);
      var note = grid.closest(".card") && grid.closest(".card").querySelector(".card-note");
      if (note) { note.textContent = "拖拽卡片可调整顺序，松手即保存到 /api/dashboards"; }
    }).catch(function () { /* 静默 */ });
  }

  /* ---------- 初始化 ---------- */
  function init() {
    bindLogo();
    bindCollapse();
    bindLayout();
    bindSearch();
    bindBell();
    bindBadges();
    bindAi();
    bindActions();
    watchRoute();
    bindWidgetParams();
    bindDashboardPersist();
    bindKlineTools();
    bindContextMenu();
    var mode = currentLayout();
    applyLayout(mode);
    if (mode === "custom") { applyLayout("custom", customOpts()); }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.V7 = { applyLayout: applyLayout, toast: toast, openPanel: openPanel };
})();
