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
      if (!btn) { handleTabs(event); return; }
      var act = btn.getAttribute("data-act");
      var id = btn.getAttribute("data-id");

      if (act === "est-toggle") {
        apiJson("/api/fund/estimation").then(function (r0) {
          var cur = ((r0.body && r0.body.data) || {}).enabled;
          apiJson("/api/fund/estimation/toggle", { method: "POST", body: JSON.stringify({ enabled: !cur }) })
            .then(function (r) {
              toast(r.ok && r.body && !r.body.error ? "盘中估值已" + (!cur ? "启用" : "关闭") : ((r.body && r.body.error) || "失败"));
              loadEstimation();
            });
        });
        return;
      }
      if (act === "yjb-toggle") {
        var en = ($("yjb-enabled") || {}).value === "1";
        apiJson("/api/fund/yjb/toggle", { method: "POST", body: JSON.stringify({ enabled: en }) })
          .then(function (r) { toast(r.ok && r.body && !r.body.error ? "开关已保存" : ((r.body && r.body.error) || "失败")); refreshYjb(); });
        return;
      }
      if (act === "yjb-consent") {
        var acc = ($("yjb-consent") || {}).value === "1";
        apiJson("/api/fund/yjb/consent", { method: "POST", body: JSON.stringify({ accepted: acc }) })
          .then(function (r) { toast(r.ok && r.body && !r.body.error ? "授权已提交" : ((r.body && r.body.error) || "失败")); refreshYjb(); });
        return;
      }
      if (act === "yjb-qr") { yjbQr(); return; }
      if (act === "yjb-poll") {
        var sid1 = btn.getAttribute("data-sid");
        apiJson("/api/fund/yjb/poll?session_id=" + encodeURIComponent(sid1)).then(function (r) {
          var d = (r.body && r.body.data) || {};
          toast("轮询结果：" + (d.state || d.status || (r.body && r.body.error) || "等待扫码"));
        });
        return;
      }
      if (act === "yjb-bind") {
        var sid2 = btn.getAttribute("data-sid");
        apiJson("/api/fund/yjb/bind", { method: "POST", body: JSON.stringify({ session_id: sid2 }) })
          .then(function (r) {
            toast(r.ok && r.body && !r.body.error ? "绑定成功" : ((r.body && r.body.error) || "绑定失败"));
            refreshYjb();
          });
        return;
      }
      if (act === "yjb-collect") {
        apiJson("/api/fund/yjb/collect").then(function (r) {
          var d = (r.body && r.body.data) || {};
          toast(r.ok && !(r.body && r.body.error)
            ? "采集完成：" + (d.count != null ? d.count + " 条" : "已更新")
            : ((r.body && r.body.error) || "采集失败"));
          refreshYjb();
        });
        return;
      }
      if (act === "yjb-unbind") {
        if (btn.getAttribute("data-armed") !== "1") {
          btn.setAttribute("data-armed", "1");
          btn.textContent = "确认解绑？";
          return;
        }
        apiJson("/api/fund/yjb/unbind", { method: "POST", body: JSON.stringify({ confirm: true }) })
          .then(function (r) { toast(r.ok && r.body && !r.body.error ? "已解绑" : ((r.body && r.body.error) || "解绑失败")); refreshYjb(); });
        return;
      }
      if (act === "bt-run") { runBacktest(); return; }
      if (act === "dca-run") { runDca(); return; }
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
      if (act === "scr-run") {
        runScreener();
        return;
      }
      if (act === "scr-refresh") {
        apiJson("/api/screener/refresh", { method: "POST" }).then(function (r) {
          var d = (r.body && r.body.data) || {};
          toast(r.ok && r.body && !r.body.error
            ? (d.started ? "快照构建已启动（异步，进度见状态）" : (d.message || "已有构建任务在进行"))
            : "刷新失败");
          loadScreenerStatus();
        });
        return;
      }
      if (act === "ai-test") {
        runAiTest();
        return;
      }
      if (act === "sys-cache-clear") {
        apiJson("/api/cache/clear", { method: "POST" }).then(function (r) {
          var d = (r.body && r.body.data) || {};
          toast(r.ok && r.body && !r.body.error ? ("已清空缓存（" + (d.removed || 0) + " 个文件）") : "清空失败");
          loadSystemPanel();
        });
        return;
      }
      if (act === "alerts-read-all") {
        var hint = $("v7-alerts-hint");
        if (hint) { hint.textContent = "处理中…"; }
        apiJson("/api/alerts/events/read", { method: "POST" }).then(function (r) {
          var d = (r.body && r.body.data) || {};
          if (r.ok && r.body && !r.body.error) {
            var un = $("v7-alerts-unread");
            if (un) { un.textContent = String(d.unread == null ? 0 : d.unread); }
            alertsHintText = "已标记 " + (d.marked || 0) + " 条为已读";
            if (hint) { hint.textContent = alertsHintText; }
            toast(alertsHintText);
            setTimeout(function () { window.dispatchEvent(new HashChangeEvent("hashchange")); }, 700);
            setTimeout(function () { alertsHintText = ""; }, 8000);
          } else if (hint) {
            hint.textContent = (r.body && r.body.error) || "标记失败";
          }
        });
        return;
      }
      if (act === "cn-test-all") {
        runConnectorTests();
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
      injectYjbPanel();
      injectStockPanel();
      injectQuoteChart();
      injectMoneyPanel();
      injectFundPanel();
      injectComboPanel();
      injectNewsPanel();
      injectSectorPanel();
      injectPortfolioAttrPanel();
      injectSystemPanel();
      injectAlertsToolbar();
      injectConnectorsTest();
      injectScreenerPanel();
      injectAiTestPanel();
      restoreCalcResults();
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

  /* ---------- 22. 组合回测 / 定投计算器（v3.4.0 恢复） ---------- */
  var btCache = null;   // {html, dates, curve}：12s 动态刷新会重渲染视图，需回填
  var dcaCache = null;  // {html}

  /** 拉取基金全量净值（自动翻页），返回按日期升序的 [{date, nav}]。 */
  function fetchNav(code) {
    function page(n) {
      return apiJson("/api/fund/nav?code=" + encodeURIComponent(code) + "&size=500&page=" + n)
        .then(function (r) {
          var data = (r.body && r.body.data) || {};
          return { items: data.items || [], total: data.total_pages || 1, err: r.body && r.body.error };
        });
    }
    return page(1).then(function (first) {
      if (first.err) { throw new Error(first.err); }
      var pages = [first.items];
      var chain = Promise.resolve();
      for (var i = 2; i <= Math.min(first.total, 4); i += 1) {
        chain = chain.then((function (n) {
          return function () { return page(n).then(function (x) { pages.push(x.items); }); };
        })(i));
      }
      return chain.then(function () {
        var all = [];
        pages.forEach(function (list) { all = all.concat(list); });
        var byDate = {};
        all.forEach(function (x) { if (x && x.date) { byDate[x.date] = Number(x.nav); } });
        return Object.keys(byDate).sort().map(function (d) { return { date: d, nav: byDate[d] }; })
          .filter(function (x) { return isFinite(x.nav) && x.nav > 0; });
      });
    });
  }

  function statKpis(items) {
    return '<div class="v-kpi-grid">' + items.map(function (kv) {
      return '<div class="v-kpi"><span class="k-label">' + esc(kv[0]) + '</span>' +
        '<span class="k-value ' + (kv[2] || "") + '">' + esc(kv[1]) + '</span></div>';
    }).join("") + "</div>";
  }

  function runBacktest() {
    var box = $("bt-result");
    var hint = $("bt-hint");
    if (!box) { return; }
    var codes = (($("bt-codes") || {}).value || "").split(/[,，\s]+/).filter(Boolean);
    var weightsRaw = (($("bt-weights") || {}).value || "").split(/[,，\s]+/).filter(Boolean).map(Number);
    var cash = parseFloat((($("bt-cash") || {}).value || "100000"));
    var range = parseInt((($("bt-range") || {}).value || "250"), 10);
    if (!codes.length) { if (hint) { hint.textContent = "请填写至少一个基金代码"; } return; }
    var weights = codes.map(function (_, i) {
      return isFinite(weightsRaw[i]) && weightsRaw[i] > 0 ? weightsRaw[i] : 0;
    });
    var sumW = weights.reduce(function (a, b) { return a + b; }, 0);
    weights = sumW > 0 ? weights.map(function (w) { return w / sumW; })
      : codes.map(function () { return 1 / codes.length; });
    box.innerHTML = '<div class="loading">回测计算中…</div>';
    Promise.all(codes.map(fetchNav)).then(function (sets) {
      var maps = sets.map(function (rows) {
        var m = {};
        rows.forEach(function (r) { m[r.date] = r.nav; });
        return m;
      });
      // 取各基金日期交集
      var dates = Object.keys(maps[0]).filter(function (d) {
        return maps.every(function (m) { return m[d] != null; });
      }).sort();
      if (dates.length < 20) { throw new Error("可用共同交易日不足（" + dates.length + " 天）"); }
      dates = dates.slice(-range);
      var base = dates.map(function (d) { return maps.map(function (m) { return m[d]; }); });
      var curve = base.map(function (row) {
        var acc = 0;
        row.forEach(function (v, i) { acc += (v / base[0][i]) * weights[i]; });
        return acc;
      });
      var equity = curve.map(function (v) { return +((v - 1) * cash).toFixed(2); });
      var totalRet = curve[curve.length - 1] - 1;
      var days = dates.length;
      var annual = Math.pow(curve[curve.length - 1], 250 / days) - 1;
      var peak = curve[0], mdd = 0;
      curve.forEach(function (v) { peak = Math.max(peak, v); mdd = Math.min(mdd, v / peak - 1); });
      var rets = [];
      for (var i = 1; i < curve.length; i += 1) { rets.push(curve[i] / curve[i - 1] - 1); }
      var mean = rets.reduce(function (a, b) { return a + b; }, 0) / (rets.length || 1);
      var sd = Math.sqrt(rets.reduce(function (a, b) { return a + (b - mean) * (b - mean); }, 0) / (rets.length || 1));
      var sharpe = sd > 0 ? (mean / sd) * Math.sqrt(250) : 0;
      var pct = function (v) { return (v >= 0 ? "+" : "") + (v * 100).toFixed(2) + "%"; };
      var btHtml = statKpis([
        ["区间收益", pct(totalRet), totalRet >= 0 ? "up" : "down"],
        ["年化收益", pct(annual), annual >= 0 ? "up" : "down"],
        ["最大回撤", pct(mdd), "down"],
        ["夏普比率", sharpe.toFixed(2)],
        ["交易日", days + " 天"],
        ["期末权益", equity[equity.length - 1].toLocaleString() + " 元"]
      ]) + '<div id="bt-chart" style="height:230px;margin-top:8px"></div>';
      btCache = { html: btHtml, dates: dates, curve: curve };
      box.innerHTML = btHtml;
      if (window.echarts) {
        var chart = window.echarts.init($("bt-chart"));
        chart.setOption({
          animation: false, grid: { left: 8, right: 12, top: 12, bottom: 8, containLabel: true },
          xAxis: { type: "category", data: dates, axisLabel: { fontSize: 9 } },
          yAxis: { type: "value", scale: true, axisLabel: { fontSize: 9 } },
          series: [{ name: "组合净值", type: "line", showSymbol: false, data: curve,
            lineStyle: { width: 1.6, color: "#4a9bff" },
            areaStyle: { color: "rgba(74,155,255,.14)" } }]
        });
        window.__btChart = chart;
      }
      if (hint) { hint.textContent = "已按 " + codes.length + " 只基金、" + days + " 个共同交易日计算"; }
    }).catch(function (e) {
      box.innerHTML = '<div class="v-empty">' + esc(e.message || "回测失败") + "</div>";
    });
  }

  function runDca() {
    var box = $("dca-result");
    if (!box) { return; }
    var code = (($("dca-code") || {}).value || "").trim();
    var amount = parseFloat((($("dca-amount") || {}).value || "1000"));
    var freq = (($("dca-freq") || {}).value || "month");
    var range = parseInt((($("dca-range") || {}).value || "250"), 10);
    if (!code || !(amount > 0)) { box.innerHTML = '<div class="v-empty">请填写基金代码与定投金额</div>'; return; }
    box.innerHTML = '<div class="loading">定投模拟中…</div>';
    fetchNav(code).then(function (rows) {
      rows = rows.slice(-range);
      if (rows.length < 10) { throw new Error("净值序列不足"); }
      var picks = [];
      if (freq === "week") {
        picks = rows.filter(function (_, i) { return i % 5 === 0; });
      } else {
        var seen = {};
        picks = rows.filter(function (r) {
          var k = r.date.slice(0, 7);
          if (seen[k]) { return false; }
          seen[k] = 1; return true;
        });
      }
      var shares = 0, invested = 0;
      picks.forEach(function (r) { shares += amount / r.nav; invested += amount; });
      var last = rows[rows.length - 1];
      var value = shares * last.nav;
      var profit = value - invested;
      var ret = invested > 0 ? profit / invested : 0;
      var years = rows.length / 250;
      var annual = years > 0 ? Math.pow(value / invested, 1 / years) - 1 : 0;
      var pct = function (v) { return (v >= 0 ? "+" : "") + (v * 100).toFixed(2) + "%"; };
      dcaCache = { html: statKpis([
        ["定投次数", picks.length + " 次"],
        ["累计投入", invested.toLocaleString() + " 元"],
        ["累计份额", shares.toFixed(2)],
        ["期末市值", value.toFixed(2) + " 元"],
        ["累计收益", profit.toFixed(2) + " 元", profit >= 0 ? "up" : "down"],
        ["收益率", pct(ret), ret >= 0 ? "up" : "down"],
        ["年化（近似）", pct(annual), annual >= 0 ? "up" : "down"],
        ["区间", rows[0].date + " ~ " + last.date]
      ]) + '<div class="v-hint" style="margin-top:6px">口径：按选定频率定期定额买入，份额=当日单位净值；年化为现金流近似折算，非严格 XIRR。</div>' };
      box.innerHTML = dcaCache.html;
    }).catch(function (e) {
      box.innerHTML = '<div class="v-empty">' + esc(e.message || "定投计算失败") + "</div>";
    });
  }

  function watchViewRender() {
    var body = $("view-body");
    if (!body || body.getAttribute("data-v7-watch") === "1") { return; }
    body.setAttribute("data-v7-watch", "1");
    var timer = null;
    new MutationObserver(function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        injectSymbolBar();
        injectWatchToolbar();
        injectTableTools();
        injectPortfolioToolbar();
        injectConnectorsToolbar();
        injectSettingsPrefs();
        injectKlineTools();
        injectAboutCard();
        injectDashboardDrag();
        injectYjbPanel();
        injectStockPanel();
        injectQuoteChart();
        injectMoneyPanel();
        injectFundPanel();
        injectComboPanel();
        injectNewsPanel();
        injectSectorPanel();
        injectPortfolioAttrPanel();
        injectSystemPanel();
        injectAlertsToolbar();
        injectConnectorsTest();
        injectScreenerPanel();
        injectAiTestPanel();
        bindImport();
        restoreCalcResults();
      }, 150);
    }).observe(body, { childList: true });
  }

  /** 动态刷新重渲染后回填回测/定投结果。 */
  function restoreCalcResults() {
    var id = currentId();
    if (id === "backtest" && btCache) {
      var box = $("bt-result");
      if (box && !box.querySelector(".v-kpi")) {
        box.innerHTML = btCache.html;
        if (window.echarts && $("bt-chart")) {
          var chart = window.echarts.init($("bt-chart"));
          chart.setOption({
            animation: false, grid: { left: 8, right: 12, top: 12, bottom: 8, containLabel: true },
            xAxis: { type: "category", data: btCache.dates, axisLabel: { fontSize: 9 } },
            yAxis: { type: "value", scale: true, axisLabel: { fontSize: 9 } },
            series: [{ name: "组合净值", type: "line", showSymbol: false, data: btCache.curve,
              lineStyle: { width: 1.6, color: "#4a9bff" },
              areaStyle: { color: "rgba(74,155,255,.14)" } }]
          });
        }
      }
    }
    if (id === "dca" && dcaCache) {
      var dbox = $("dca-result");
      if (dbox && !dbox.querySelector(".v-kpi")) { dbox.innerHTML = dcaCache.html; }
    }
  }

  /* ---------- 23. 养基宝连接面板（v3.4.0：恢复 8 个未接端点） ---------- */
  function injectYjbPanel() {
    if (currentId() !== "yjb") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-yjb")) { return; }
    var card = document.createElement("div");
    card.className = "card v7-yjb";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">⛨</span>养基宝连接</span>' +
        '<span class="card-note">扫码授权 · 采集 · 解绑 · 增强通道</span></div>' +
        '<div class="v-body">' +
          '<div class="v-kpi-grid" id="yjb-kpis"><div class="loading">读取状态…</div></div>' +
          '<div class="v7-form" style="margin-top:10px">' +
            '<div><label>增强通道</label><select id="yjb-enabled">' +
              '<option value="1">启用</option><option value="0">停用</option></select></div>' +
            '<div><label>授权告知</label><select id="yjb-consent">' +
              '<option value="1">已阅读并同意</option><option value="0">不同意</option></select></div>' +
          '</div>' +
          '<div class="v7-form-foot">' +
            '<button class="v7-btn" data-act="yjb-toggle" type="button">保存开关</button>' +
            '<button class="v7-btn" data-act="yjb-consent" type="button">提交授权</button>' +
            '<button class="v7-btn primary" data-act="yjb-qr" type="button">生成扫码链接</button>' +
            '<button class="v7-btn" data-act="yjb-collect" type="button">立即采集</button>' +
            '<button class="v7-btn danger" data-act="yjb-unbind" type="button">解除绑定</button>' +
          '</div>' +
          '<div id="yjb-qr-box" class="v7-preview-table" style="margin-top:8px"></div>' +
          '<div class="v7-hint" id="yjb-hint"></div>' +
        '</div>';
    body.appendChild(card);
    refreshYjb();
  }

  function refreshYjb() {
    var box = $("yjb-kpis");
    if (!box) { return; }
    apiJson("/api/fund/yjb/status").then(function (r) {
      var d = (r.body && r.body.data) || {};
      var rows = [
        ["绑定状态", d.bound ? "已绑定" : "未绑定", d.bound ? "up" : "down"],
        ["增强通道", d.enabled ? "启用" : "停用"],
        ["授权", d.consent ? "已同意" : "未同意"],
        ["账户数", d.accounts != null ? String(d.accounts) : "—"]
      ];
      box.innerHTML = rows.map(function (kv) {
        return '<div class="v-kpi"><span class="k-label">' + esc(kv[0]) + '</span>' +
          '<span class="k-value ' + (kv[2] || "") + '">' + esc(kv[1]) + '</span></div>';
      }).join("");
    }).catch(function () { box.innerHTML = '<div class="v-empty">状态读取失败</div>'; });
  }

  function yjbQr() {
    var box0 = $("yjb-qr-box");
    if (box0) { box0.innerHTML = '<div class="loading">生成扫码会话…</div>'; }
    apiJson("/api/fund/yjb/qrcode").then(function (r) {
      var box = $("yjb-qr-box");            // 回调内重取：面板可能已被重渲染替换
      var hint = $("yjb-hint");
      if (!box) { return; }
      var d = (r.body && r.body.data) || {};
      if (r.body && r.body.error) { box.innerHTML = '<div class="v-empty">' + esc(r.body.error) + "</div>"; return; }
      if (d.available === false) {
        box.innerHTML = '<div class="v-empty">当前不可扫码：' + esc(d.reason || "环境不支持") + "</div>";
        return;
      }
      var sid = d.session_id || d.id || "";
      var url = d.url || d.qr_url || "";
      var img = d.image_base64 ? '<img alt="养基宝登录二维码" style="width:160px;height:160px;background:#fff;padding:6px;border-radius:6px" src="data:image/png;base64,' + d.image_base64 + '">' : "";
      box.innerHTML = '<div style="display:flex;gap:14px;align-items:flex-start;flex-wrap:wrap">' + img +
        '<div style="min-width:220px">' +
          '<div class="v7-hint">会话：' + esc(sid) + "</div>" +
          (url ? '<div class="v7-hint" style="word-break:break-all">链接：' + esc(url) + "</div>" : "") +
          '<div class="v7-form-foot" style="justify-content:flex-start;margin-top:6px">' +
            '<button class="v7-btn" data-act="yjb-poll" data-sid="' + esc(sid) + '" type="button">轮询状态</button>' +
            '<button class="v7-btn primary" data-act="yjb-bind" data-sid="' + esc(sid) + '" type="button">确认已扫码</button>' +
          "</div></div></div>";
      if (hint) { hint.textContent = "用养基宝 App 扫码后点「确认已扫码」；会话仅存内存，重启需重扫"; }
    }).catch(function () {
      var box = $("yjb-qr-box");
      if (box) { box.innerHTML = '<div class="v-empty">生成失败</div>'; }
    });
  }

  /* ---------- 24. 个股深度 + 行情图（v3.4.0：恢复 7 个未接端点） ---------- */
  var stockSymbol = "";
  var stockTabState = "概况";

  function injectStockPanel() {
    if (currentId() !== "stock") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-stock")) { return; }
    var view = (window.V6_VIEWS || {}).stock || {};
    var arr = (view.api && view.api.p) || [];
    var m = String(arr[0] || "").match(/symbol=([^&]+)/);
    stockSymbol = m ? decodeURIComponent(m[1]) : (localStorage.getItem("openbb-symbol") || "300750");
    var card = document.createElement("div");
    card.className = "card v7-stock";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">ST</span>个股深度 · ' + esc(stockSymbol) + "</span>" +
        '<span class="card-note">概况 / 财务指标 / 三大报表 / 一致预期 / FCF 健康度</span></div>' +
        '<div class="v-body">' +
          '<div class="v7-kline-tools" style="margin:0 0 8px">' +
            ["概况", "财务指标", "三大报表", "一致预期", "FCF"].map(function (t, i) {
              return '<button class="v7-tab' + (t === stockTabState ? " on" : "") + '" data-stock-tab="' + t + '" type="button">' + t + "</button>";
            }).join("") +
          "</div>" +
          '<div id="v7-stock-body"><div class="loading">加载中…</div></div>' +
        "</div>";
    body.insertBefore(card, body.firstChild);
    loadStockTab(stockTabState);
  }

  function stockApi(path) { return apiJson(path); }

  var stockReqSeq = 0;

  /** 横表渲染：[{name, values[]}] + periods → 表格（行=指标，列=期次）。 */
  function metricTable(periods, metrics, maxRows) {
    if (!metrics || !metrics.length) { return ""; }
    var cols = (periods || []).slice(0, 6);
    var rows = metrics.slice(0, maxRows || 12);
    return '<div class="v7-preview-table"><table><tr><th>指标</th>' +
      cols.map(function (p) { return "<th>" + esc(p) + "</th>"; }).join("") + "</tr>" +
      rows.map(function (m) {
        return "<tr><td>" + esc(m.name || m.key) + "</td>" +
          cols.map(function (_, i) {
            var v = (m.values || [])[i];
            return "<td>" + esc(v == null ? "—" : (typeof v === "number" ? (Math.abs(v) >= 1e6 ? (v / 1e8).toFixed(2) + "亿" : v.toFixed(2)) : v)) + "</td>";
          }).join("") + "</tr>";
      }).join("") + "</table></div>";
  }

  function loadStockTab(tab) {
    stockTabState = tab;
    var box = $("v7-stock-body");
    if (!box) { return; }
    var seq = ++stockReqSeq;
    box.innerHTML = '<div class="loading">加载中…</div>';
    var sym = encodeURIComponent(stockSymbol);
    var stale = function () { return seq !== stockReqSeq; };

    if (tab === "概况") {
      apiJson("/api/market/profile?symbol=" + sym + "&market=a").then(function (r) {
        if (stale()) { return; }
        var b = $("v7-stock-body"); if (!b) { return; }
        var d = (r.body && r.body.data) || {};
        if (r.body && r.body.error) { b.innerHTML = '<div class="v-empty">' + esc(r.body.error) + "</div>"; return; }
        var info = d.profile || d.info || d;
        var labels = { name: "名称", industry: "行业", main_business: "主营业务", list_date: "上市日期",
          region: "地区", website: "公司网站", total_mv: "总市值", pe: "市盈率", pb: "市净率" };
        var rows = Object.keys(labels).filter(function (k) { return info[k] != null && info[k] !== ""; })
          .map(function (k) { return [labels[k], String(info[k])]; });
        b.innerHTML = rows.length ? statKpis(rows.slice(0, 8)) : '<div class="v-empty">概况接口无字段</div>';
      });
      return;
    }

    if (tab === "财务指标") {
      apiJson("/api/stock/financial-indicator?symbol=" + sym + "&market=a&limit=8").then(function (r) {
        if (stale()) { return; }
        var b = $("v7-stock-body"); if (!b) { return; }
        var d = (r.body && r.body.data) || {};
        if (r.body && r.body.error) { b.innerHTML = '<div class="v-empty">' + esc(r.body.error) + "</div>"; return; }
        var html = metricTable(d.periods, d.metrics, 12);
        b.innerHTML = html || '<div class="v-empty">无财务指标数据</div>';
      });
      return;
    }

    if (tab === "三大报表") {
      box.innerHTML = '<div class="v7-kline-tools" style="margin:0 0 8px">' +
        ["利润表", "资产负债表", "现金流量表"].map(function (t, i) {
          return '<button class="v7-tab' + (i === 0 ? " on" : "") + '" data-stock-stmt="' + t + '" type="button">' + t + "</button>";
        }).join("") + '</div><div id="v7-stmt-body"><div class="loading">加载中…</div></div>';
      loadStatement("利润表");
      return;
    }

    if (tab === "一致预期") {
      apiJson("/api/stock/estimates?symbol=" + sym + "&market=a").then(function (r) {
        if (stale()) { return; }
        var b = $("v7-stock-body"); if (!b) { return; }
        var d = (r.body && r.body.data) || {};
        if (r.body && r.body.error) { b.innerHTML = '<div class="v-empty">' + esc(r.body.error) + "</div>"; return; }
        var eps = d.eps || [];
        if (!eps.length) { b.innerHTML = '<div class="v-empty">暂无一致预期（' + esc(d.caliber_note || "接口返回空") + "）</div>"; return; }
        var keys = ["年度", "预测机构数", "最小值", "均值", "最大值", "行业平均数"];
        b.innerHTML = '<div class="v7-preview-table"><table><tr>' +
          keys.map(function (k) { return "<th>" + esc(k) + "</th>"; }).join("") + "</tr>" +
          eps.map(function (it) {
            return "<tr>" + keys.map(function (k) { return "<td>" + esc(it[k] == null ? "—" : it[k]) + "</td>"; }).join("") + "</tr>";
          }).join("") + "</table></div>" +
          '<div class="v-hint" style="margin-top:6px">口径：' + esc(d.caliber_note || "机构一致预期，非本系统预测") + "</div>";
      });
      return;
    }

    if (tab === "FCF") {
      apiJson("/api/stock/fcf-health?symbol=" + sym).then(function (r) {
        if (stale()) { return; }
        var b = $("v7-stock-body"); if (!b) { return; }
        var d = (r.body && r.body.data) || {};
        if (r.body && r.body.error) { b.innerHTML = '<div class="v-empty">' + esc(r.body.error) + "</div>"; return; }
        var checks = d.checks || [];
        if (!checks.length) { b.innerHTML = '<div class="v-empty">' + esc(d.missing && d.missing.length ? "数据不足：" + d.missing.join("、") : "无 FCF 数据") + "</div>"; return; }
        var bad = checks.filter(function (c) { return c.status && c.status !== "ok"; });
        b.innerHTML = statKpis([
          ["判定", d.verdict || "—", bad.length ? "down" : "up"],
          ["异常项", bad.length + " / " + checks.length, bad.length ? "down" : ""],
          ["报告期", d.period || "—"],
          ["对比期", d.compare_period || "—"]
        ]) + '<div class="v7-preview-table" style="margin-top:8px"><table><tr><th>检查项</th><th>本期</th><th>上期</th><th>状态</th><th>说明</th></tr>' +
          checks.map(function (c) {
            return "<tr><td>" + esc(c.label) + "</td><td>" + esc(c.value == null ? "—" : c.value + (c.unit || "")) +
              "</td><td>" + esc(c.prev == null ? "—" : c.prev + (c.unit || "")) +
              '</td><td class="' + (c.status === "ok" ? "ok" : "bad") + '">' + esc(c.status || "—") +
              "</td><td>" + esc(c.note || "") + "</td></tr>";
          }).join("") + "</table></div>";
      });
      return;
    }
  }

  function loadStatement(name) {
    var box = $("v7-stmt-body");
    if (!box) { return; }
    box.innerHTML = '<div class="loading">加载中…</div>';
    apiJson("/api/stock/financial-report?symbol=" + encodeURIComponent(stockSymbol) +
      "&statement=" + encodeURIComponent(name) + "&limit=6").then(function (r) {
      var b = $("v7-stmt-body"); if (!b) { return; }
      var d = (r.body && r.body.data) || {};
      if (r.body && r.body.error) { b.innerHTML = '<div class="v-empty">' + esc(r.body.error) + "</div>"; return; }
      var html = metricTable(d.periods, d.metrics, 14);
      b.innerHTML = html || '<div class="v-empty">无报表数据</div>';
    });
  }

  function injectQuoteChart() {
    if (currentId() !== "stock") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-quote")) { return; }
    var card = document.createElement("div");
    card.className = "card v7-quote";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">〽</span>行情图 · ' + esc(stockSymbol) + "</span>" +
        '<span class="v7-kline-tools">' +
          ["日k", "周k", "月k"].map(function (p, i) {
            return '<button class="v7-tab' + (i === 0 ? " on" : "") + '" data-quote-period="' + p + '">' + p.replace("k", "K") + "</button>";
          }).join("") +
          ["qfq", "hfq", "不复权"].map(function (a, i) {
            return '<button class="v7-tab' + (i === 0 ? " on" : "") + '" data-quote-adjust="' + a + '">' +
              ({ qfq: "前复权", hfq: "后复权", "不复权": "不复权" }[a]) + "</button>";
          }).join("") +
          '<button class="v7-tab" data-quote-minute="1" type="button">分时</button>' +
        "</span></div>" +
        '<div class="v-body"><div id="v7-quote-chart" style="height:240px"></div>' +
        '<div class="v-hint" id="v7-quote-hint">数据源：/api/market/kline · /api/market/minute</div></div>';
    body.insertBefore(card, body.firstChild);
    loadQuote(quoteState.period, quoteState.adjust);
  }

  var quoteState = { period: "日k", adjust: "qfq" };

  function loadQuote(period, adjust) {
    quoteState = { period: period, adjust: adjust };
    var box = $("v7-quote-chart");
    var hint = $("v7-quote-hint");
    if (!box) { return; }
    box.innerHTML = '<div class="loading">行情加载中…</div>';
    if (period === "分时") {
      apiJson("/api/market/minute?symbol=" + encodeURIComponent(stockSymbol) + "&market=a&period=1").then(function (r) {
        var d = (r.body && r.body.data) || {};
        var items = d.items || [];
        if (!items.length) { var b2 = $("v7-quote-chart"); if (b2) { b2.innerHTML = '<div class="v-empty">无分时数据</div>'; } return; }
        var xs = items.map(function (x) { return x.time || x.date || ""; });
        var ys = items.map(function (x) { return x.price != null ? x.price : x.close; });
        drawQuote(xs, ys, "分时", "line");
        if (hint) { hint.textContent = "分时 · " + (d.source || "") + " · " + (d.as_of || ""); }
      });
      return;
    }
    apiJson("/api/market/kline?symbol=" + encodeURIComponent(stockSymbol) + "&market=a&period=" +
      encodeURIComponent(period) + "&adjust=" + encodeURIComponent(adjust)).then(function (r) {
      var d = (r.body && r.body.data) || {};
      var items = (d.items || []).slice(-120);
      var b2 = $("v7-quote-chart");
      if (!items.length) { if (b2) { b2.innerHTML = '<div class="v-empty">无 K 线数据</div>'; } return; }
      var xs = items.map(function (x) { return x.date; });
      var cs = items.map(function (x) { return [x.open, x.close, x.low, x.high]; });
      drawQuote(xs, cs, "K线", "candle");
      if (hint) { hint.textContent = period + " · 复权：" + adjust + " · " + (d.source || "") + " · " + (d.as_of || ""); }
    });
  }

  function drawQuote(xs, data, name, kind) {
    var box = $("v7-quote-chart");
    if (!box || !window.echarts) { return; }
    box.innerHTML = "";
    var chart = window.echarts.init(box);
    chart.setOption({
      animation: false,
      grid: { left: 8, right: 12, top: 12, bottom: 8, containLabel: true },
      xAxis: { type: "category", data: xs, axisLabel: { fontSize: 9 } },
      yAxis: { type: "value", scale: true, axisLabel: { fontSize: 9 } },
      series: [kind === "candle"
        ? { name: name, type: "candlestick", data: data,
            itemStyle: { color: "#ff3b4e", color0: "#00c087", borderColor: "#ff3b4e", borderColor0: "#00c087" } }
        : { name: name, type: "line", showSymbol: false, data: data,
            lineStyle: { width: 1.4, color: "#4a9bff" },
            areaStyle: { color: "rgba(74,155,255,.14)" } }]
    });
    window.__v7QuoteChart = chart;
  }

  /** 页签类点击（无 data-act，单独分派）。 */
  function handleTabs(event) {
    var nt = event.target.closest ? event.target.closest("[data-news-tab]") : null;
    if (nt) {
      Array.prototype.forEach.call(document.querySelectorAll("[data-news-tab]"), function (b) { b.classList.toggle("on", b === nt); });
      loadNews(nt.getAttribute("data-news-tab"));
      return true;
    }
    var ns = event.target.closest ? event.target.closest("[data-news-sort]") : null;
    if (ns) {
      Array.prototype.forEach.call(document.querySelectorAll("[data-news-sort]"), function (b) { b.classList.toggle("on", b === ns); });
      newsSort = ns.getAttribute("data-news-sort");
      loadNews("AI 资讯流");
      return true;
    }
    var nr = event.target.closest ? event.target.closest("[data-news-region]") : null;
    if (nr) {
      Array.prototype.forEach.call(document.querySelectorAll("[data-news-region]"), function (b) { b.classList.toggle("on", b === nr); });
      newsRegion = nr.getAttribute("data-news-region");
      loadNews("AI 资讯流");
      return true;
    }
    var nsearch = event.target.closest ? event.target.closest("[data-news-search]") : null;
    if (nsearch) {
      var sec = $("v7-news-sector");
      if (sec) { sec.value = nsearch.getAttribute("data-news-search"); }
      Array.prototype.forEach.call(document.querySelectorAll("[data-news-tab]"), function (b) {
        b.classList.toggle("on", b.getAttribute("data-news-tab") === "板块资讯");
      });
      loadNews("板块资讯");
      return true;
    }
    var ct = event.target.closest ? event.target.closest("[data-combo-tab]") : null;
    if (ct) {
      Array.prototype.forEach.call(document.querySelectorAll("[data-combo-tab]"), function (b) {
        b.classList.toggle("on", b === ct);
      });
      loadCombo(ct.getAttribute("data-combo-tab"));
      return true;
    }
    var cr = event.target.closest ? event.target.closest("[data-combo-risk]") : null;
    if (cr) {
      Array.prototype.forEach.call(document.querySelectorAll("[data-combo-risk]"), function (b) {
        b.classList.toggle("on", b === cr);
      });
      comboRisk = cr.getAttribute("data-combo-risk");
      loadCombo(comboState);
      return true;
    }
    var mt = event.target.closest ? event.target.closest("[data-money-tab]") : null;
    if (mt) {
      Array.prototype.forEach.call(document.querySelectorAll("[data-money-tab]"), function (b) {
        b.classList.toggle("on", b === mt);
      });
      loadMoney(mt.getAttribute("data-money-tab"));
      return true;
    }
    var mm = event.target.closest ? event.target.closest("[data-money-metric]") : null;
    if (mm) {
      Array.prototype.forEach.call(document.querySelectorAll("[data-money-metric]"), function (b) {
        b.classList.toggle("on", b === mm);
      });
      northMetric = mm.getAttribute("data-money-metric");
      loadMoney("北向成交");
      return true;
    }
      var stab = event.target.closest ? event.target.closest("[data-stock-tab]") : null;
      if (stab) {
        Array.prototype.forEach.call(document.querySelectorAll("[data-stock-tab]"), function (b) {
          b.classList.toggle("on", b === stab);
        });
        loadStockTab(stab.getAttribute("data-stock-tab"));
        return;
      }
      var stmt = event.target.closest ? event.target.closest("[data-stock-stmt]") : null;
      if (stmt) {
        Array.prototype.forEach.call(document.querySelectorAll("[data-stock-stmt]"), function (b) {
          b.classList.toggle("on", b === stmt);
        });
        loadStatement(stmt.getAttribute("data-stock-stmt"));
        return;
      }
      var qp = event.target.closest ? event.target.closest("[data-quote-period]") : null;
      if (qp) {
        Array.prototype.forEach.call(document.querySelectorAll("[data-quote-period]"), function (b) {
          b.classList.toggle("on", b === qp);
        });
        loadQuote(qp.getAttribute("data-quote-period"), quoteState.adjust === "不复权" ? "不复权" : quoteState.adjust);
        return;
      }
      var qa = event.target.closest ? event.target.closest("[data-quote-adjust]") : null;
      if (qa) {
        Array.prototype.forEach.call(document.querySelectorAll("[data-quote-adjust]"), function (b) {
          b.classList.toggle("on", b === qa);
        });
        loadQuote(quoteState.period, qa.getAttribute("data-quote-adjust"));
        return;
      }
      var qm = event.target.closest ? event.target.closest("[data-quote-minute]") : null;
      if (qm) {
        Array.prototype.forEach.call(document.querySelectorAll("[data-quote-period]"), function (b) { b.classList.remove("on"); });
        loadQuote("分时", quoteState.adjust);
        return;
      }
      var stb = event.target.closest ? event.target.closest("[data-sec-tab]") : null;
      if (stb) {
        loadSector(stb.getAttribute("data-sec-tab"));
        return;
      }
      var smk = event.target.closest ? event.target.closest("[data-sec-market]") : null;
      if (smk) {
        sectorMarket = smk.getAttribute("data-sec-market");
        loadSector(sectorTabState);
        return;
      }
      var skd = event.target.closest ? event.target.closest("[data-sec-kind]") : null;
      if (skd) {
        sectorKind = skd.getAttribute("data-sec-kind");
        loadSector("板块热力");
        return;
      }
      var scw = event.target.closest ? event.target.closest("[data-sec-crowd]") : null;
      if (scw) {
        runCrowd();
        return;
      }
      var pat = event.target.closest ? event.target.closest("[data-pa-tab]") : null;
      if (pat) {
        Array.prototype.forEach.call(document.querySelectorAll("[data-pa-tab]"), function (b) {
          b.classList.toggle("on", b === pat);
        });
        loadPortfolioAttr(pat.getAttribute("data-pa-tab"));
        return;
      }
      return false;
  }

  /* ---------- 25. 资金明细（v3.4.0：恢复 lhb-broker / lhb-institution / north-top / northbound） ---------- */
  var moneyTabState = "北向成交";
  var northMetric = "turnover";

  /** 对象数组 → 表格（cols = [[key, label, 格式化?], ...]）。 */
  function objTable(rows, cols, emptyNote) {
    if (!rows || !rows.length) { return '<div class="v-empty">' + esc(emptyNote || "接口返回空数据") + "</div>"; }
    return '<div class="v7-preview-table"><table><tr>' +
      cols.map(function (c) { return "<th>" + esc(c[1]) + "</th>"; }).join("") + "</tr>" +
      rows.map(function (r) {
        return "<tr>" + cols.map(function (c) {
          var v = r[c[0]];
          var text = c[2] ? c[2](v) : (v == null ? "—" : String(v));
          return "<td>" + esc(text) + "</td>";
        }).join("") + "</tr>";
      }).join("") + "</table></div>";
  }

  function injectMoneyPanel() {
    if (currentId() !== "lhb") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-money")) { return; }
    var card = document.createElement("div");
    card.className = "card v7-money";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">◎</span>资金明细</span>' +
        '<span class="card-note">北向成交 / 前十 · 龙虎榜机构 / 营业部</span></div>' +
        '<div class="v-body">' +
          '<div class="v7-kline-tools" style="margin:0 0 8px">' +
            ["北向成交", "北向前十", "龙虎榜机构", "龙虎榜营业部"].map(function (t) {
              return '<button class="v7-tab' + (t === moneyTabState ? " on" : "") + '" data-money-tab="' + t + '" type="button">' + t + "</button>";
            }).join("") +
            '<span data-money-metric-wrap style="margin-left:8px">' +
              '<button class="v7-tab on" data-money-metric="turnover" type="button">成交总额</button>' +
              '<button class="v7-tab" data-money-metric="legacy_net" type="button">历史净买额</button>' +
            "</span>" +
          "</div>" +
          '<div id="v7-money-body"><div class="loading">加载中…</div></div>' +
        "</div>";
    body.insertBefore(card, body.firstChild);
    loadMoney(moneyTabState);
  }

  function loadMoney(tab) {
    moneyTabState = tab;
    var box = $("v7-money-body");
    if (!box) { return; }
    box.innerHTML = '<div class="loading">加载中…</div>';
    var put = function (html) { var b = $("v7-money-body"); if (b) { b.innerHTML = html; } };

    if (tab === "北向成交") {
      apiJson("/api/moneyflow/northbound?days=20&metric=" + northMetric).then(function (r) {
        var d = (r.body && r.body.data) || {};
        if (r.body && r.body.error) { put('<div class="v-empty">' + esc(r.body.error) + "</div>"); return; }
        var items = (d.items || []).slice().reverse();
        var html = '<div class="v7-preview-table"><table><tr><th>日期</th><th>合计(亿)</th><th>沪(亿)</th><th>深(亿)</th><th>笔数</th></tr>' +
          items.map(function (x) {
            return "<tr><td>" + esc(x.date) + "</td><td>" + esc(x.total == null ? "—" : x.total) +
              "</td><td>" + esc(x.hu == null ? "—" : x.hu) + "</td><td>" + esc(x.shen == null ? "—" : x.shen) +
              "</td><td>" + esc(x.deals == null ? "—" : x.deals) + "</td></tr>";
          }).join("") + "</table></div>" +
          '<div class="v-hint" style="margin-top:6px">' + esc(d.caliber || "") + "</div>";
        put(html);
      });
      return;
    }
    if (tab === "北向前十") {
      apiJson("/api/moneyflow/north-top?limit=10").then(function (r) {
        var d = (r.body && r.body.data) || {};
        var items = d.items || [];
        put(objTable(items, [["name", "名称"], ["code", "代码"], ["net", "净买额(亿)"],
          ["buy", "买入(亿)"], ["sell", "卖出(亿)"], ["pct", "涨跌幅"]],
          d.note || "当前无北向前十数据（可能未到披露时点）"));
      });
      return;
    }
    if (tab === "龙虎榜机构") {
      apiJson("/api/moneyflow/lhb-institution?limit=50").then(function (r) {
        var d = (r.body && r.body.data) || {};
        put(objTable(d.items, [["date", "日期"], ["code", "代码"], ["name", "名称"], ["close", "收盘"],
          ["pct", "涨跌幅"], ["buy_orgs", "买方机构"], ["sell_orgs", "卖方机构"],
          ["net_buy", "净买(亿)"]], "当日无机构席位上榜") +
          '<div class="v-hint" style="margin-top:6px">披露日：' + esc(d.date || "—") + " · " + esc(d.source || "") + "</div>");
      });
      return;
    }
    if (tab === "龙虎榜营业部") {
      apiJson("/api/moneyflow/lhb-broker?limit=50").then(function (r) {
        var d = (r.body && r.body.data) || {};
        put(objTable(d.items, [["date", "日期"], ["name", "营业部"], ["buy_count", "买入次数"],
          ["sell_count", "卖出次数"], ["buy_amount", "买入(亿)"], ["sell_amount", "卖出(亿)"],
          ["net_buy", "净买(亿)"]], "当日无营业部上榜"));
      });
      return;
    }
  }

  /* ---------- 26. 基金：经理画像 + 盘中估值（v3.4.0：fund/manager、fund/estimation） ---------- */
  function injectFundPanel() {
    if (currentId() !== "fund") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-fund")) { return; }
    var view = (window.V6_VIEWS || {}).fund || {};
    var arr = (view.api && view.api.f) || [];
    var m = String(arr[0] || "").match(/code=([^&]+)/);
    var code = m ? decodeURIComponent(m[1]) : "017811";
    var card = document.createElement("div");
    card.className = "card v7-fund";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">F</span>基金经理与估值开关 · ' + esc(code) + "</span>" +
        '<span class="card-note">/api/fund/manager · /api/fund/estimation</span></div>' +
        '<div class="v-body"><div class="v-kpi-grid" id="v7-mgr-kpis"><div class="loading">加载中…</div></div>' +
        '<div class="v7-form-foot" style="margin-top:8px">' +
          '<span id="v7-est-state" class="v7-hint">盘中估值：—</span>' +
          '<button class="v7-btn" data-act="est-toggle" type="button">切换盘中估值</button>' +
        "</div>" +
        '<div class="v-hint" id="v7-est-note"></div></div>';
    body.insertBefore(card, body.firstChild);
    loadManager(code);
    loadEstimation();
  }

  function loadManager(code) {
    apiJson("/api/fund/manager?code=" + encodeURIComponent(code)).then(function (r) {
      var box = $("v7-mgr-kpis");
      if (!box) { return; }
      var d = (r.body && r.body.data) || {};
      if (r.body && r.body.error) { box.innerHTML = '<div class="v-empty">' + esc(r.body.error) + "</div>"; return; }
      if (!d.found) { box.innerHTML = '<div class="v-empty">未找到该基金经理</div>'; return; }
      var g = d.manager || {};
      box.innerHTML = statKpis([
        ["经理", g.name || "—"],
        ["公司", g.company || "—"],
        ["任职", g.tenure_years != null ? g.tenure_years + " 年" : (g.tenure_days != null ? g.tenure_days + " 天" : "—")],
        ["在管规模", g.scale_aum != null ? g.scale_aum + " 亿" : "—"],
        ["最佳回报", g.best_return != null ? g.best_return + "%" : "—"]
      ]);
    });
  }

  function loadEstimation() {
    apiJson("/api/fund/estimation").then(function (r) {
      var st = $("v7-est-state"), note = $("v7-est-note");
      var d = (r.body && r.body.data) || {};
      if (st) { st.textContent = "盘中估值：" + (d.enabled ? "已启用" : "已关闭") + " · " + (d.source || ""); }
      if (note) { note.textContent = d.caliber || ""; }
    });
  }

  /* ---------- 27. 基金组合：诊断 / 权重 / 评估 / 候选池（v3.4.0） ---------- */
  var comboRisk = "balanced";
  var comboState = "组合诊断";

  function injectComboPanel() {
    if (currentId() !== "fund-combo") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-combo")) { return; }
    var card = document.createElement("div");
    card.className = "card v7-combo";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">◫</span>组合工具</span>' +
        '<span class="card-note">诊断 / 权重建议 / 组合评估 / 候选池</span></div>' +
        '<div class="v-body">' +
          '<div class="v7-kline-tools" style="margin:0 0 8px">' +
            ["组合诊断", "权重建议", "组合评估", "候选池"].map(function (t) {
              return '<button class="v7-tab' + (t === comboState ? " on" : "") + '" data-combo-tab="' + t + '" type="button">' + t + "</button>";
            }).join("") +
            ["conservative", "balanced", "aggressive"].map(function (x) {
              return '<button class="v7-tab' + (x === comboRisk ? " on" : "") + '" data-combo-risk="' + x + '" type="button">' +
                ({ conservative: "保守", balanced: "均衡", aggressive: "进取" }[x]) + "</button>";
            }).join("") +
          "</div>" +
          '<div class="v7-form" style="margin-bottom:8px">' +
            '<div class="full"><label>基金代码（逗号分隔，用于权重/评估）</label>' +
              '<input id="v7-combo-codes" value="017811" placeholder="如 017811,025500"></div>' +
          "</div>" +
          '<div id="v7-combo-body"><div class="loading">加载中…</div></div>' +
        "</div>";
    body.insertBefore(card, body.firstChild);
    loadCombo(comboState);
  }

  function loadCombo(tab) {
    comboState = tab;
    var box = $("v7-combo-body");
    if (!box) { return; }
    var put = function (h) { var b = $("v7-combo-body"); if (b) { b.innerHTML = h; } };
    box.innerHTML = '<div class="loading">加载中…</div>';
    var codes = (($("v7-combo-codes") || {}).value || "").trim();

    if (tab === "组合诊断") {
      apiJson("/api/fund/combo/diagnose?risk=" + comboRisk).then(function (r) {
        var d = (r.body && r.body.data) || {};
        if (r.body && r.body.error) { put('<div class="v-empty">' + esc(r.body.error) + "</div>"); return; }
        if (d.missing && d.missing.length && !(d.holdings || []).length) {
          put('<div class="v-empty">' + esc(d.missing.join("；")) + "</div>");
          return;
        }
        put(objTable((d.holdings || []).map(function (h, i) {
          return { name: h.name || h.code || ("#" + (i + 1)), weight: h.weight, target: (d.target || [])[i],
            deviation: (d.deviation || [])[i] };
        }), [["name", "持仓"], ["weight", "当前权重"], ["target", "目标权重"], ["deviation", "偏离"]],
          "组合诊断无持仓数据") + '<div class="v-hint" style="margin-top:6px">' + esc(d.caliber || "") + "</div>");
      });
      return;
    }
    if (tab === "权重建议") {
      if (!codes) { put('<div class="v-empty">请填写基金代码</div>'); return; }
      box.innerHTML = '<div class="loading">计算中…（首次约 20–30 秒，含拥挤度降权）</div>';
      apiJson("/api/fund/combo/weight?codes=" + encodeURIComponent(codes) + "&risk=" + comboRisk + "&crowd=true").then(function (r) {
        var d = (r.body && r.body.data) || {};
        if (r.body && r.body.error) { put('<div class="v-empty">' + esc(r.body.error) + "</div>"); return; }
        var items = d.items || [];
        var tot = d.totals || {};
        put(statKpis([
          ["风险档", comboRisk === "conservative" ? "保守" : comboRisk === "aggressive" ? "进取" : "均衡"],
          ["基础权重合计", tot.base != null ? tot.base + "%" : "—"],
          ["最终权重合计", tot.final != null ? tot.final + "%" : "—", "up"],
          ["现金保留", tot.cash != null ? tot.cash + "%" : "—"]
        ]) + objTable(items, [["code", "代码"], ["name", "名称"],
          ["base_weight", "基础%"], ["crowding", "拥挤度"],
          ["discount", "折扣", function (v) { return v == null ? "—" : String(v); }],
          ["final_weight", "最终%"], ["discount_label", "说明"]], "无权重建议"));
      });
      return;
    }
    if (tab === "组合评估") {
      if (!codes) { put('<div class="v-empty">请填写基金代码</div>'); return; }
      apiJson("/api/fund/combo/evaluate?codes=" + encodeURIComponent(codes) + "&risk=" + comboRisk).then(function (r) {
        var d = (r.body && r.body.data) || {};
        if (r.body && r.body.error) { put('<div class="v-empty">' + esc(r.body.error) + "</div>"); return; }
        var items = d.items || [];
        if (!items.length) {
          put('<div class="v-empty">' + esc((d.missing || []).join("；") || "无评估结果") + "</div>");
          return;
        }
        var rows = items.map(function (it) {
          var st = it.stats || {};
          return {
            code: it.code, name: it.name || it.code,
            annual: st.annual_return != null ? st.annual_return + "%" : "—",
            vol: st.volatility != null ? st.volatility + "%" : "—",
            mdd: st.max_drawdown != null ? st.max_drawdown + "%" : "—",
            sharpe: st.sharpe != null ? st.sharpe : "—",
            days: st.sample_days != null ? st.sample_days : "—",
            weight: (function () {
              var w = (d.weights || []).filter(function (x) { return x.code === it.code; })[0];
              return w && w.weight != null ? w.weight + "%" : "—";
            })()
          };
        });
        put(objTable(rows, [["code", "代码"], ["name", "名称"], ["weight", "建议权重"],
          ["annual", "年化"], ["vol", "波动率"], ["mdd", "最大回撤"], ["sharpe", "夏普"], ["days", "样本天数"]],
          "无评估结果") + '<div class="v-hint" style="margin-top:6px">' + esc(d.caliber || "") + "</div>");
      });
      return;
    }
    if (tab === "候选池") {
      apiJson("/api/fund/candidates?limit=60&template=fund_friendly").then(function (r) {
        var d = (r.body && r.body.data) || {};
        if (r.body && r.body.error) { put('<div class="v-empty">' + esc(r.body.error) + "</div>"); return; }
        var items = d.items || [];
        var tpl = d.template || {};
        put('<div class="v-hint">模板：' + esc(tpl.label || "基金友好") + " · 命中 " + esc(String(d.total_matched || items.length)) +
          (d.truncated ? "（已截断显示）" : "") + "</div>" +
          objTable(items, [["code", "代码"], ["name", "名称"], ["board", "行业"], ["close", "收盘"],
            ["chg", "涨跌%", function (v) { return v == null ? "—" : Number(v).toFixed(2); }],
            ["pe", "PE", function (v) { return v == null ? "—" : Number(v).toFixed(1); }],
            ["pb", "PB", function (v) { return v == null ? "—" : Number(v).toFixed(2); }]], "候选池为空"));
      });
      return;
    }
  }

  /* ---------- 28. 资讯工具（v3.4.0：ai-feed / board-tree / sector-search / financial-detail） ---------- */
  var newsTabState = "AI 资讯流";
  var newsInput = { sector: "半导体", code: "", title: "" };
  /** 输入框优先、状态兜底（12 秒刷新会重建卡片，输入值必须持久）。 */
  function newsVal(id, fallback) {
    var el = $(id);
    var v = el && el.value != null ? String(el.value) : "";
    return v.trim() ? v.trim() : (fallback || "");
  }
  var newsSort = "relevance";
  var newsRegion = "global";

  function injectNewsPanel() {
    if (currentId() !== "news") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-news")) { return; }
    var card = document.createElement("div");
    card.className = "card v7-news";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">▦</span>资讯工具</span>' +
        '<span class="card-note">AI 资讯流 / 板块树 / 板块资讯 / 财报正文</span></div>' +
        '<div class="v-body">' +
          '<div class="v7-kline-tools" style="margin:0 0 8px">' +
            ["AI 资讯流", "板块树", "板块资讯", "财报正文", "AI 摘要"].map(function (t) {
              return '<button class="v7-tab' + (t === newsTabState ? " on" : "") + '" data-news-tab="' + t + '" type="button">' + t + "</button>";
            }).join("") +
            ["relevance:相关", "time:最新", "impact:影响"].map(function (x) {
              var k = x.split(":")[0];
              return '<button class="v7-tab' + (k === newsSort ? " on" : "") + '" data-news-sort="' + k + '" type="button">' + x.split(":")[1] + "</button>";
            }).join("") +
            ["global:全球", "cn:国内"].map(function (x) {
              var k = x.split(":")[0];
              return '<button class="v7-tab' + (k === newsRegion ? " on" : "") + '" data-news-region="' + k + '" type="button">' + x.split(":")[1] + "</button>";
            }).join("") +
          "</div>" +
          '<div class="v7-form" style="margin-bottom:8px">' +
            '<div><label>板块（板块资讯用）</label><input id="v7-news-sector" value="' + esc(newsInput.sector) + '"></div>' +
            '<div><label>公告代码（财报正文 / AI 摘要用）</label><input id="v7-news-code" value="' + esc(newsInput.code) + '" placeholder="如 600519"></div>' +
            '<div><label>公告标题（AI 摘要用，可空）</label><input id="v7-ai-title" value="' + esc(newsInput.title) + '" placeholder="如 2026年第三季度报告"></div>' +
          "</div>" +
          '<div id="v7-news-body"><div class="loading">加载中…</div></div>' +
        "</div>";
    body.insertBefore(card, body.firstChild);
    card.addEventListener("input", function (ev) {
      var t = ev.target;
      if (!t || !t.id) { return; }
      if (t.id === "v7-news-sector") { newsInput.sector = t.value; }
      else if (t.id === "v7-news-code") { newsInput.code = t.value; }
      else if (t.id === "v7-ai-title") { newsInput.title = t.value; }
    });
    loadNews(newsTabState);
  }

  function newsItemHtml(x) {
    return '<div class="v7-news-item"><b>' + esc(x.title || "—") + "</b>" +
      '<div class="v7-hint">' + esc((x.text || x.summary || "").slice(0, 160)) + "</div>" +
      '<div class="v7-hint" style="opacity:.6">' + esc(x.time || x.publish_time || x.source || "") + "</div></div>";
  }

  function loadNews(tab) {
    newsTabState = tab;
    var box = $("v7-news-body");
    if (!box) { return; }
    var put = function (h) { var b = $("v7-news-body"); if (b) { b.innerHTML = h; } };
    box.innerHTML = '<div class="loading">加载中…</div>';

    if (tab === "AI 资讯流") {
      apiJson("/api/news/ai-feed?limit=20&sort=" + newsSort + "&region=" + newsRegion + "&market=all").then(function (r) {
        var d = (r.body && r.body.data) || {};
        if (r.body && r.body.error) { put('<div class="v-empty">' + esc(r.body.error) + "</div>"); return; }
        var items = d.items || [];
        put(items.length ? '<div class="v7-hint" style="margin-bottom:6px">共 ' + esc(String(d.count || items.length)) +
          " 条 · 去重前 " + esc(String(d.before_dedup || "—")) + " · 源：" + esc(d.source || "") + "</div>" +
          items.map(newsItemHtml).join("") : '<div class="v-empty">无资讯</div>');
      });
      return;
    }
    if (tab === "板块树") {
      apiJson("/api/news/board-tree").then(function (r) {
        var d = (r.body && r.body.data) || {};
        if (r.body && r.body.error) { put('<div class="v-empty">' + esc(r.body.error) + "</div>"); return; }
        var tree = d.tree || [];
        var cnt = d.counts || {};
        var html = '<div class="v7-hint" style="margin-bottom:6px">版本 ' + esc(d.version || "—") +
          " · 一级 " + esc(String(cnt.level1 || tree.length)) + " 二级 " + esc(String(cnt.level2 || "—")) +
          " 三级 " + esc(String(cnt.level3 || "—")) + "</div>";
        tree.forEach(function (lv1) {
          html += '<div style="margin-bottom:6px"><b>' + esc(lv1.name) + "</b>";
          (lv1.children || []).forEach(function (lv2) {
            html += '<div style="margin-left:12px">▸ ' + esc(lv2.name);
            (lv2.children || []).forEach(function (lv3) {
              html += '<button class="v7-tab" data-news-search="' + esc(lv3.name) + '" style="margin-left:6px">' + esc(lv3.name) + "</button>";
            });
            html += "</div>";
          });
          html += "</div>";
        });
        put(html || '<div class="v-empty">板块树为空</div>');
      });
      return;
    }
    if (tab === "板块资讯") {
      var sector = newsVal("v7-news-sector", newsInput.sector);
      if (!sector) { put('<div class="v-empty">请填写板块名</div>'); return; }
      apiJson("/api/news/sector-search?sector=" + encodeURIComponent(sector) + "&sort=time&limit=30").then(function (r) {
        var d = (r.body && r.body.data) || {};
        if (r.body && r.body.error) { put('<div class="v-empty">' + esc(r.body.error) + "</div>"); return; }
        var items = d.items || [];
        put(items.length ? '<div class="v7-hint" style="margin-bottom:6px">「' + esc(d.sector || sector) + "」命中 " +
          esc(String(d.count || items.length)) + " 条 · 层级 " + esc(d.level || "—") + "</div>" +
          items.map(newsItemHtml).join("") : '<div class="v-empty">该板块无资讯</div>');
      });
      return;
    }
    if (tab === "财报正文") {
      var code = newsVal("v7-news-code", newsInput.code);
      if (!code) { put('<div class="v-empty">请填写公告代码</div>'); return; }
      apiJson("/api/news/financial-detail?market=a&code=" + encodeURIComponent(code) + "&title=").then(function (r) {
        var d = (r.body && r.body.data) || {};
        if (r.body && r.body.error) { put('<div class="v-empty">' + esc(r.body.error) + "</div>"); return; }
        var text = d.content || d.text || d.body || "";
        put(text ? '<div class="v7-hint">' + esc(d.title || "") + '</div><div class="v7-pre">' +
          esc(String(text).slice(0, 4000)) + "</div>" : '<div class="v-empty">未取到正文（' + esc(d.note || "需从公告列表行传入标题/链接") + "）</div>");
      });
      return;
    }
    if (tab === "AI 摘要") {
      loadAiSummary();
      return;
    }
  }

  /* ---------- 29. 板块工具（v3.4.0：恢复 sector/concept · sector/heatmap · sector/crowding） ---------- */
  var SECTOR_TABS = ["概念板块", "板块热力", "拥挤度单查"];
  var sectorTabState = "概念板块";
  var sectorMarket = "a";
  var sectorKind = "industry";
  var sectorBoard = "半导体";

  /** 数值格式化（非数值返回占位符，不把 NaN 打到界面上）。 */
  function secFixed(v, digits) {
    var n = parseFloat(v);
    if (!isFinite(n)) { return "—"; }
    return n.toFixed(digits == null ? 2 : digits);
  }

  /** 带符号数值 + 涨跌配色（红涨绿跌，A 股习惯；0 不着色）。 */
  function secSigned(v, digits, suffix) {
    var n = parseFloat(v);
    if (!isFinite(n)) { return '<span style="opacity:.55">—</span>'; }
    var color = n > 0 ? "#ff6b78" : (n < 0 ? "#2fd6a4" : "");
    return '<span' + (color ? ' style="color:' + color + '"' : "") + ">" +
      (n > 0 ? "+" : "") + n.toFixed(digits == null ? 2 : digits) + (suffix || "") + "</span>";
  }

  /** 纯文本版带符号数值（statKpis 内部会 esc，不能传 HTML）。 */
  function secSignedText(v, digits, suffix) {
    var n = parseFloat(v);
    if (!isFinite(n)) { return "—"; }
    return (n > 0 ? "+" : "") + n.toFixed(digits == null ? 2 : digits) + (suffix || "");
  }

  /** 板块工具页签 + 市场/口径切换条。 */
  function sectorToolsHtml() {
    var html = '<div class="v7-kline-tools" style="margin:0 0 8px">' +
      SECTOR_TABS.map(function (t) {
        return '<button class="v7-tab' + (t === sectorTabState ? " on" : "") +
          '" data-sec-tab="' + t + '" type="button">' + t + "</button>";
      }).join("");
    if (sectorTabState !== "拥挤度单查") {
      html += '<span style="flex:1"></span>' +
        [["a", "A股"], ["hk", "港股"], ["us", "美股"]].map(function (m) {
          return '<button class="v7-tab' + (m[0] === sectorMarket ? " on" : "") +
            '" data-sec-market="' + m[0] + '" type="button">' + m[1] + "</button>";
        }).join("");
    }
    html += "</div>";
    if (sectorTabState === "板块热力") {
      html += '<div class="v7-kline-tools" style="margin:0 0 8px">' +
        [["industry", "行业"], ["concept", "概念"]].map(function (k) {
          return '<button class="v7-tab' + (k[0] === sectorKind ? " on" : "") +
            '" data-sec-kind="' + k[0] + '" type="button">' + k[1] + "</button>";
        }).join("") + "</div>";
    }
    return html;
  }

  /** 板块行情页注入板块工具卡（概念/热力/单板块拥挤度）。 */
  function injectSectorPanel() {
    if (currentId() !== "sectors") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-sector")) { return; }
    var card = document.createElement("div");
    card.className = "card v7-sector";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">▤</span>板块工具</span>' +
        '<span class="card-note">概念板块 / 板块热力 / 单板块拥挤度</span></div>' +
        '<div class="v-body">' +
          '<div id="v7-sec-tools"></div>' +
          '<div id="v7-sec-body"><div class="loading">加载中…</div></div>' +
        "</div>";
    body.insertBefore(card, body.firstChild);
    loadSector(sectorTabState);
  }

  /** 切换页签后重新加载对应子面板。 */
  function loadSector(tab) {
    sectorTabState = tab || sectorTabState;
    var tools = $("v7-sec-tools");
    if (tools) { tools.innerHTML = sectorToolsHtml(); }
    var box = $("v7-sec-body");
    if (!box) { return; }
    box.innerHTML = '<div class="loading">加载中…</div>';
    if (sectorTabState === "概念板块") { loadConcept(); return; }
    if (sectorTabState === "板块热力") { loadHeatmap(); return; }
    renderCrowdForm();
  }

  /** 概念板块列表（sector/concept）。 */
  function loadConcept() {
    apiJson("/api/sector/concept?market=" + encodeURIComponent(sectorMarket)).then(function (r) {
      var box = $("v7-sec-body");
      if (!box) { return; }
      if (r.body && r.body.error) { box.innerHTML = '<div class="v-empty">' + esc(r.body.error) + "</div>"; return; }
      var d = (r.body && r.body.data) || {};
      var items = (d.items || []).slice().sort(function (a, b) { return (b.pct || 0) - (a.pct || 0); });
      if (!items.length) { box.innerHTML = '<div class="v-empty">该市场无概念板块数据</div>'; return; }
      var html = '<div class="v7-hint" style="margin-bottom:6px">共 ' + items.length + " 个概念 · 上涨 " +
        items.filter(function (x) { return (x.pct || 0) > 0; }).length + " 个 · 源 " +
        esc(d.source || "—") + " · " + esc(d.as_of || "") + "</div>";
      html += '<div class="v7-preview-table"><table><tr><th>概念</th><th>涨跌幅</th><th>5日</th><th>20日</th>' +
        "<th>成交额(亿)</th><th>净流入(亿)</th><th>换手%</th><th>量比</th><th>领涨股</th></tr>" +
        items.map(function (x) {
          return "<tr><td>" + esc(x.name) + "</td><td>" + secSigned(x.pct, 2, "%") + "</td>" +
            "<td>" + secSigned(x.pct_5d, 2, "%") + "</td><td>" + secSigned(x.pct_20d, 2, "%") + "</td>" +
            "<td>" + secFixed(x.amount, 2) + "</td><td>" + secSigned(x.net_inflow, 2) + "</td>" +
            "<td>" + secFixed(x.turnover_rate, 2) + "</td><td>" + secFixed(x.volume_ratio, 2) + "</td>" +
            "<td>" + esc(x.lead_stock || "—") + (x.lead_pct == null ? "" : " " + secSigned(x.lead_pct, 2, "%")) + "</td></tr>";
        }).join("") + "</table></div>";
      if (d.caliber) { html += '<div class="v7-sec-note">口径：' + esc(d.caliber) + "</div>"; }
      box.innerHTML = html;
    });
  }

  /** 板块热力图（sector/heatmap）：色块 + 明细表。 */
  function loadHeatmap() {
    apiJson("/api/sector/heatmap?market=" + encodeURIComponent(sectorMarket) + "&kind=" + encodeURIComponent(sectorKind))
      .then(function (r) {
        var box = $("v7-sec-body");
        if (!box) { return; }
        if (r.body && r.body.error) { box.innerHTML = '<div class="v-empty">' + esc(r.body.error) + "</div>"; return; }
        var d = (r.body && r.body.data) || {};
        var nodes = (d.nodes || []).slice().sort(function (a, b) { return (b.pct || 0) - (a.pct || 0); });
        if (!nodes.length) { box.innerHTML = '<div class="v-empty">该市场无热力数据</div>'; return; }
        var up = nodes.filter(function (x) { return (x.pct || 0) > 0; }).length;
        var html = '<div class="v7-hint" style="margin-bottom:6px">共 ' + nodes.length + " 个" +
          (sectorKind === "industry" ? "行业" : "概念") + " · 上涨 " + up + " / 下跌 " +
          (nodes.length - up) + " · 源 " + esc(d.source || "—") + " · " + esc(d.as_of || "") + "</div>";
        html += '<div class="v7-heat">' + nodes.map(function (n) {
          var pct = parseFloat(n.pct) || 0;
          var cls = pct > 0 ? "up" : (pct < 0 ? "down" : "flat");
          var op = (0.55 + 0.45 * Math.min(1, Math.abs(pct) / 4)).toFixed(2);
          var tip = n.name + " · 成交额 " + secFixed(n.amount, 2) + " 亿 · 领涨 " + (n.lead_stock || "—");
          return '<div class="v7-heat-cell ' + cls + '" style="opacity:' + op + '" title="' + esc(tip) + '">' +
            "<span>" + esc(n.name) + "</span><b>" + (pct > 0 ? "+" : "") + pct.toFixed(2) + "%</b>" +
            '<span class="sub">' + (n.up == null ? "" : "↑" + n.up) + (n.down == null ? "" : " ↓" + n.down) + "</span></div>";
        }).join("") + "</div>";
        html += '<div class="v7-hint" style="margin:10px 0 4px">成交额 Top15 明细</div>' +
          '<div class="v7-preview-table"><table><tr><th>板块</th><th>涨跌幅</th><th>成交额(亿)</th><th>领涨股</th><th>上涨/下跌</th></tr>' +
          nodes.slice().sort(function (a, b) { return (b.amount || 0) - (a.amount || 0); }).slice(0, 15).map(function (n) {
            return "<tr><td>" + esc(n.name) + "</td><td>" + secSigned(n.pct, 2, "%") + "</td><td>" +
              secFixed(n.amount, 2) + "</td><td>" + esc(n.lead_stock || "—") + "</td><td>" +
              esc((n.up == null ? "—" : n.up) + " / " + (n.down == null ? "—" : n.down)) + "</td></tr>";
          }).join("") + "</table></div>";
        if (d.caliber) { html += '<div class="v7-sec-note">口径：' + esc(d.caliber) + "</div>"; }
        box.innerHTML = html;
      });
  }

  /** 单板块拥挤度查询表单（渲染后自动跑一次当前板块）。 */
  function renderCrowdForm() {
    var box = $("v7-sec-body");
    if (!box) { return; }
    box.innerHTML =
      '<div class="v7-form" style="margin-bottom:8px">' +
        '<div><label>板块名</label><input id="v7-sec-board" value="' + esc(sectorBoard) + '" placeholder="如 半导体"></div>' +
        '<div><label>类型</label><select id="v7-sec-ctype">' +
          '<option value="industry"' + (sectorKind === "industry" ? " selected" : "") + ">行业</option>" +
          '<option value="concept"' + (sectorKind === "concept" ? " selected" : "") + ">概念</option></select></div>" +
        '<div><button class="v7-btn primary" data-sec-crowd="1" type="button">查询拥挤度</button></div>' +
      "</div>" +
      '<div id="v7-sec-crowd-body"><div class="loading">计算中…</div></div>';
    runCrowd();
  }

  /** 执行单板块拥挤度查询（sector/crowding）。 */
  function runCrowd() {
    var boardEl = $("v7-sec-board");
    var kindEl = $("v7-sec-ctype");
    var board = ((boardEl && boardEl.value) || "").trim();
    var kind = (kindEl && kindEl.value) || "industry";
    var box = $("v7-sec-crowd-body");
    if (!box) { return; }
    if (!board) { box.innerHTML = '<div class="v-empty">请填写板块名</div>'; return; }
    sectorBoard = board;
    box.innerHTML = '<div class="loading">计算中（首次约 10–40 秒）…</div>';
    apiJson("/api/sector/crowding?board=" + encodeURIComponent(board) + "&kind=" + encodeURIComponent(kind))
      .then(function (r) {
        var target = $("v7-sec-crowd-body");
        if (!target) { return; }
        if (r.body && r.body.error) { target.innerHTML = '<div class="v-empty">' + esc(r.body.error) + "</div>"; return; }
        var d = (r.body && r.body.data) || {};
        if (d.score == null) {
          target.innerHTML = '<div class="v-empty">该板块暂无拥挤度数据</div>' +
            ((d.missing || []).length ? '<div class="v7-sec-miss">' + d.missing.map(esc).join("；") + "</div>" : "");
          return;
        }
        var levelText = d.level || "—";
        var html = statKpis([
          ["板块", (d.board || board) + "（" + (d.kind === "concept" ? "概念" : "行业") + "）"],
          ["拥挤度评分", secFixed(d.score, 1) + " / 100"],
          ["档位", levelText],
          ["样本天数", String(d.sample_days == null ? "—" : d.sample_days)]
        ]);
        html += '<div class="v7-hint" style="margin:8px 0 4px">' + esc(d.advice || "") +
          " · 源 " + esc(d.source || "—") + " · " + esc(d.as_of || "") + " · " + esc(d.market_status || "") + "</div>";
        html += '<div class="v7-preview-table"><table><tr><th>维度</th><th>原始值</th><th>历史分位</th><th>权重</th></tr>' +
          (d.parts || []).map(function (p) {
            return "<tr><td>" + esc(p.label) + "</td><td>" + secFixed(p.value, 2) + esc(p.unit || "") +
              "</td><td>" + secFixed(p.pct, 1) + "</td><td>" + secFixed((p.weight || 0) * 100, 0) + "%</td></tr>";
          }).join("") + "</table></div>";
        var ex = d.extra || {};
        if (ex.constituent_count != null) {
          html += '<div class="v7-hint" style="margin-top:6px">成分股 ' + esc(String(ex.constituent_count)) + " 只</div>";
        }
        if (ex.correlation && ex.correlation.caliber) {
          html += '<div class="v7-sec-note">成分股相关性：' + esc(ex.correlation.caliber) + "</div>";
        }
        if (ex.turnover && ex.turnover.caliber) {
          html += '<div class="v7-sec-note">成分股换手率：' + esc(ex.turnover.caliber) + "</div>";
        }
        if (d.caliber) { html += '<div class="v7-sec-note">口径：' + esc(d.caliber) + "</div>"; }
        if ((d.missing || []).length) {
          html += '<div class="v7-sec-miss">缺失维度：' + (d.missing || []).map(esc).join("；") + "</div>";
        }
        target.innerHTML = html;
      });
  }

  /* ---------- 30. 组合归因与风险（v3.4.0：恢复 portfolio/attribution · portfolio/risk） ---------- */
  var PA_TABS = ["归因", "风险"];
  var paTabState = "归因";

  /** 持仓页注入组合归因/风险卡。 */
  function injectPortfolioAttrPanel() {
    if (currentId() !== "portfolio") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-pa")) { return; }
    var card = document.createElement("div");
    card.className = "card v7-pa";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">◱</span>组合归因与风险</span>' +
        '<span class="v7-pa-tools">' + PA_TABS.map(function (t) {
          return '<button class="v7-tab' + (t === paTabState ? " on" : "") +
            '" data-pa-tab="' + t + '" type="button">' + t + "</button>";
        }).join("") + "</span></div>" +
        '<div class="v-body" id="v7-pa-body"><div class="loading">加载中…</div></div>';
    body.insertBefore(card, body.firstChild);
    loadPortfolioAttr(paTabState);
  }

  /** 切换归因/风险页签。 */
  function loadPortfolioAttr(tab) {
    paTabState = tab || paTabState;
    var box = $("v7-pa-body");
    if (!box) { return; }
    box.innerHTML = '<div class="loading">加载中…</div>';
    if (paTabState === "归因") { loadAttribution(); return; }
    loadPortfolioRisk();
  }

  /** 收益归因（portfolio/attribution）。 */
  function loadAttribution() {
    apiJson("/api/portfolio/attribution").then(function (r) {
      var box = $("v7-pa-body");
      if (!box) { return; }
      if (r.body && r.body.error) { box.innerHTML = '<div class="v-empty">' + esc(r.body.error) + "</div>"; return; }
      var d = (r.body && r.body.data) || {};
      var t = d.totals || {};
      if (!t.count) {
        box.innerHTML = '<div class="v-empty">' + esc(d.caliber || "台账为空，无法归因") + "</div>" +
          ((d.missing || []).length ? '<div class="v7-sec-miss">' + d.missing.map(esc).join("；") + "</div>" : "");
        return;
      }
      var html = statKpis([
        ["持仓数", String(t.count)],
        ["市值(元)", secFixed(t.value, 2)],
        ["成本(元)", secFixed(t.cost, 2)],
        ["浮动盈亏(元)", secSignedText(t.pnl, 2)],
        ["盈亏率", t.pnl_pct == null ? "—" : secSignedText(t.pnl_pct, 2, "%")]
      ]);
      html += '<div class="v7-hint" style="margin:10px 0 4px">收益贡献（贡献=权重×盈亏率，单位百分点）</div>' +
        '<div class="v7-preview-table"><table><tr><th>标的</th><th>市场</th><th>行业</th>' +
        "<th>市值</th><th>权重%</th><th>盈亏%</th><th>贡献</th><th>取价源</th></tr>" +
        (d.items || []).map(function (x) {
          return "<tr><td>" + esc(x.name) + ' <span style="opacity:.6">' + esc(x.symbol) + "</span></td><td>" +
            esc(x.market_label) + "</td><td>" + esc(x.industry || "—") + "</td><td>" + secFixed(x.value, 2) +
            "</td><td>" + secFixed(x.weight, 2) + "</td><td>" + secSigned(x.pnl_pct, 2, "%") + "</td><td>" +
            secSigned(x.contribution, 2) + "</td><td>" + esc(x.price_source || "—") + "</td></tr>";
        }).join("") + "</table></div>";
      if ((d.by_industry || []).length) {
        html += '<div class="v7-hint" style="margin:10px 0 4px">行业暴露</div>' +
          '<div class="v7-preview-table"><table><tr><th>行业</th><th>市值</th><th>权重%</th><th>只数</th></tr>' +
          d.by_industry.map(function (x) {
            return "<tr><td>" + esc(x.name) + "</td><td>" + secFixed(x.value, 2) + "</td><td>" +
              secFixed(x.weight, 2) + "</td><td>" + esc(String(x.count)) + "</td></tr>";
          }).join("") + "</table></div>";
      }
      var c = d.concentration || {};
      html += '<div class="v7-hint" style="margin:10px 0 4px">集中度：HHI ' + secFixed(c.hhi, 4) +
        "（" + esc(c.level || "—") + "，≥0.25 偏集中）</div>";
      if ((c.top5 || []).length) {
        html += '<div class="v7-preview-table"><table><tr><th>前五大</th><th>代码</th><th>权重%</th></tr>' +
          c.top5.map(function (x) {
            return "<tr><td>" + esc(x.name) + "</td><td>" + esc(x.symbol) + "</td><td>" + secFixed(x.weight, 2) + "</td></tr>";
          }).join("") + "</table></div>";
      }
      if ((d.scenarios || []).length) {
        html += '<div class="v7-hint" style="margin:10px 0 4px">情景（单市场 −10% 线性近似，非压力测试）</div>' +
          '<div class="v7-preview-table"><table><tr><th>情景</th><th>影响额(元)</th><th>影响比例%</th></tr>' +
          d.scenarios.map(function (x) {
            return "<tr><td>" + esc(x.label) + "</td><td>" + secSigned(x.impact, 2) + "</td><td>" +
              (x.impact_pct == null ? "—" : secSigned(x.impact_pct, 2)) + "</td></tr>";
          }).join("") + "</table></div>";
      }
      if (d.caliber) { html += '<div class="v7-sec-note">口径：' + esc(d.caliber) + "</div>"; }
      if ((d.missing || []).length) { html += '<div class="v7-sec-miss">缺价/缺汇率：' + d.missing.map(esc).join("；") + "</div>"; }
      box.innerHTML = html;
    });
  }

  /** 组合风险指标（portfolio/risk）。 */
  function loadPortfolioRisk() {
    apiJson("/api/portfolio/risk").then(function (r) {
      var box = $("v7-pa-body");
      if (!box) { return; }
      if (r.body && r.body.error) { box.innerHTML = '<div class="v-empty">' + esc(r.body.error) + "</div>"; return; }
      var d = (r.body && r.body.data) || {};
      if (!d.available) {
        box.innerHTML = '<div class="v-empty">' + esc(d.caliber || "组合风险暂不可用") + "</div>" +
          ((d.missing || []).length ? '<div class="v7-sec-miss">' + d.missing.map(esc).join("；") + "</div>" : "");
        return;
      }
      var m = d.metrics || {};
      var html = statKpis([
        ["年化收益", secSignedText(m.annual_return, 2, "%")],
        ["年化波动", secFixed(m.annual_vol, 2) + "%"],
        ["最大回撤", secFixed(m.max_drawdown, 2) + "%"],
        ["回撤天数", m.max_drawdown_days == null ? "—" : String(m.max_drawdown_days)],
        ["夏普", secFixed(m.sharpe, 2)],
        ["β", secFixed(m.beta, 2)],
        ["α", secSignedText(m.alpha, 2, "%")],
        ["R²", secFixed(m.r2, 2)],
        ["样本天数", String(d.sample_days || 0)]
      ]);
      if (!d.reliable) {
        html += '<div class="v7-sec-miss">样本仅 ' + esc(String(d.sample_days || 0)) + " 个交易日，低于可靠性阈值：仅给数值、不给结论标签</div>";
      }
      if ((d.weights || []).length) {
        html += '<div class="v7-hint" style="margin:10px 0 4px">纳入序列的权重（现金与场外基金不参与）</div>' +
          '<div class="v7-preview-table"><table><tr><th>标的</th><th>代码</th><th>权重%</th></tr>' +
          d.weights.map(function (x) {
            return "<tr><td>" + esc(x.name) + "</td><td>" + esc(x.symbol) + "</td><td>" + secFixed(x.weight, 2) + "</td></tr>";
          }).join("") + "</table></div>";
      }
      if ((d.correlation || []).length) {
        html += '<div class="v7-hint" style="margin:10px 0 4px">两两相关性（>0.8 标红：同涨同跌，分散失效）</div>' +
          '<div class="v7-preview-table"><table><tr><th>标的 A</th><th>标的 B</th><th>相关系数</th><th>提示</th></tr>' +
          d.correlation.map(function (x) {
            var v = x.value == null ? '<span style="opacity:.55">—</span>'
              : '<span' + (x.high ? ' class="v7-flag-hi"' : "") + ">" + secFixed(x.value, 3) + "</span>";
            return "<tr><td>" + esc(x.left) + "</td><td>" + esc(x.right) + "</td><td>" + v + "</td><td>" +
              (x.high ? '<span class="v7-flag-hi">高相关</span>' : "—") + "</td></tr>";
          }).join("") + "</table></div>";
      }
      if (d.caliber) { html += '<div class="v7-sec-note">口径：' + esc(d.caliber) + "</div>"; }
      if ((d.missing || []).length) { html += '<div class="v7-sec-miss">未纳入：' + d.missing.map(esc).join("；") + "</div>"; }
      box.innerHTML = html;
    });
  }

  /* ---------- 31. 系统运维 / 提醒已读 / 连接器测速（v3.4.0） ---------- */
  var alertsHintText = "";
  /** 设置页注入「缓存与访问」运维卡（cache/stats · cache/clear · system/access）。 */
  function injectSystemPanel() {
    if (currentId() !== "settings") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-sys")) { return; }
    var card = document.createElement("div");
    card.className = "card v7-sys";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">⚙</span>缓存与访问</span>' +
        '<span class="card-note">缓存统计 / 清空缓存 / 访问方式</span></div>' +
        '<div class="v-body"><div id="v7-sys-body"><div class="loading">加载中…</div></div></div>';
    body.insertBefore(card, body.firstChild);
    loadSystemPanel();
  }

  /** 读取缓存统计与访问方式并渲染。 */
  function loadSystemPanel() {
    var box = $("v7-sys-body");
    if (!box) { return; }
    Promise.all([apiJson("/api/cache/stats"), apiJson("/api/system/access")]).then(function (res) {
      var target = $("v7-sys-body");
      if (!target) { return; }
      var cs = (res[0].body && res[0].body.data) || {};
      var ac = (res[1].body && res[1].body.data) || {};
      var html = statKpis([
        ["内存缓存", secFixed(cs.memory_mb, 2) + " MB"],
        ["磁盘缓存", secFixed(cs.disk_mb, 2) + " MB"],
        ["内存条目", String(cs.memory == null ? "—" : cs.memory)],
        ["磁盘条目", String(cs.disk == null ? "—" : cs.disk)],
        ["布局占用", secFixed(cs.layout_kb, 1) + " KB"]
      ]);
      html += '<div class="v7-kline-tools" style="margin:8px 0">' +
        '<button class="v7-btn" data-act="sys-cache-clear" type="button">清空缓存</button>' +
        '<span class="v7-hint" style="margin-left:8px">清空后下一次请求会重新取数（不影响持仓/自选/密钥）</span></div>';
      if ((cs.ttl_map || []).length) {
        html += '<div class="v7-hint" style="margin:8px 0 4px">缓存 TTL 口径</div>' +
          '<div class="v7-preview-table"><table><tr><th>缓存键</th><th>TTL(秒)</th><th>说明</th></tr>' +
          cs.ttl_map.map(function (x) {
            return "<tr><td>" + esc(x.key) + "</td><td>" + esc(String(x.ttl)) + "</td><td>" + esc(x.note || "") + "</td></tr>";
          }).join("") + "</table></div>";
      }
      html += '<div class="v7-hint" style="margin:10px 0 4px">访问入口（当前访问：' + esc(location.origin) +
        "；口令：" + (ac.password_required ? "已启用" : "未启用") +
        "；局域网：" + (ac.lan_visible ? "已放开" : "仅本机") + "）</div>";
      var points = ac.points || [];
      html += points.length
        ? '<div class="v7-preview-table"><table><tr><th>范围</th><th>入口</th><th>二维码</th></tr>' +
          points.map(function (p) {
            var qr = p.qr ? '<img src="' + p.qr + '" alt="QR" style="width:84px;height:84px;image-rendering:pixelated">' : "—";
            return "<tr><td>" + esc(p.label || p.scope) + "</td><td>" + esc(p.url || "") + "</td><td>" + qr + "</td></tr>";
          }).join("") + "</table></div>"
        : '<div class="v-empty">无可用入口</div>';
      if (ac.public && ac.public.url) {
        html += '<div class="v7-sec-note">公网临时地址：' + esc(ac.public.url) + "（重启会变）</div>";
      } else if (ac.public && ac.public.error) {
        html += '<div class="v7-sec-miss">公网：' + esc(ac.public.error) + "</div>";
      }
      if (ac.caliber) { html += '<div class="v7-sec-note">口径：' + esc(ac.caliber) + "</div>"; }
      if (cs.caliber) { html += '<div class="v7-sec-note">缓存口径：' + esc(cs.caliber) + "</div>"; }
      target.innerHTML = html;
    });
  }

  /** 提醒中心注入「全部标为已读」工具条（alerts/events/read）。 */
  function injectAlertsToolbar() {
    if (currentId() !== "alerts") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-alerts-bar")) { return; }
    var bar = document.createElement("div");
    bar.className = "v7-toolbar v7-alerts-bar";
    bar.innerHTML = '<span>未读提醒</span><b id="v7-alerts-unread">—</b>' +
      '<button class="v7-btn" data-act="alerts-read-all" type="button">全部标为已读</button>' +
      '<span class="v7-hint" id="v7-alerts-hint">' + esc(alertsHintText) + "</span>";
    body.insertBefore(bar, body.firstChild);
    apiJson("/api/alerts/events?limit=1").then(function (r) {
      var d = (r.body && r.body.data) || {};
      var el = $("v7-alerts-unread");
      if (el) { el.textContent = String(d.unread == null ? "—" : d.unread); }
    });
  }

  /** 连接器页「逐个测速」结果区（connectors/test）。 */
  function injectConnectorsTest() {
    if (currentId() !== "connectors") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-cn-test")) { return; }
    var card = document.createElement("div");
    card.className = "card v7-cn-test";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">◎</span>连通性自检</span>' +
        '<button class="v7-btn" data-act="cn-test-all" type="button">逐个测速</button></div>' +
        '<div class="v-body"><div id="v7-cn-test"><div class="v7-hint">点「逐个测速」对各数据源跑一次真实探测（不读缓存、不改配置）</div></div></div>';
    body.insertBefore(card, body.firstChild);
  }

  /** 逐个跑 connectors/test，逐行追加结果（仅对后端提供探测实现的源）。 */
  function runConnectorTests() {
    var box = $("v7-cn-test");
    if (!box) { return; }
    box.innerHTML = '<div class="loading">读取数据源清单…</div>';
    apiJson("/api/connectors/status").then(function (r) {
      var list = ((r.body && r.body.data && r.body.data.datasources) || [])
        .filter(function (x) { return x.enabled !== false; });
      var target = $("v7-cn-test");
      if (!target) { return; }
      if (!list.length) { target.innerHTML = '<div class="v-empty">没有启用的数据源</div>'; return; }
      target.innerHTML = '<div class="v7-hint" style="margin-bottom:4px">共 ' + list.length +
        ' 个源，逐个真实探测（每源约 1–3 秒；后端仅对部分源提供探测实现，未实现的标「未提供探测」）…</div>' +
        '<div class="v7-preview-table"><table id="v7-cn-test-tbl">' +
        '<tr><th>数据源</th><th>结果</th><th>说明</th><th>探测时间</th></tr></table></div>';
      var idx = 0;
      function step() {
        var tbl = $("v7-cn-test-tbl");
        if (!tbl || idx >= list.length) {
          if (tbl && !$("v7-cn-sum")) {
            tbl.insertAdjacentHTML("afterend", '<div class="v7-hint" id="v7-cn-sum"></div>');
          }
          var okN = 0, probedN = 0;
          if (tbl) {
            Array.prototype.forEach.call(tbl.querySelectorAll("tr"), function (tr) {
              if (tr.getAttribute("data-probed") === "1") {
                probedN += 1;
                if (tr.getAttribute("data-ok") === "1") { okN += 1; }
              }
            });
          }
          var sum = $("v7-cn-sum");
          if (sum) {
            sum.textContent = "完成：可用 " + okN + " / 已探测 " + probedN + "（共 " + list.length +
              " 源，未提供探测 " + (list.length - probedN) + " 源）";
          }
          return;
        }
        var item = list[idx];
        var key = item.key || item.source || item.label || "";
        var label = item.label || key;
        idx += 1;
        apiJson("/api/connectors/test?source=" + encodeURIComponent(key), { method: "POST" }).then(function (tr) {
          var d = (tr.body && tr.body.data) || {};
          var err = (tr.body && tr.body.error) || "";
          var cell, detailText, probed;
          if (err) {
            cell = '<span style="opacity:.6">未提供探测</span>';
            detailText = err;
            probed = "0";
          } else if (d.ok) {
            cell = '<span style="color:#2fd6a4">可用</span>';
            detailText = d.detail || "—";
            probed = "1";
          } else {
            cell = '<span style="color:#ff6b78">不可用</span>';
            detailText = d.detail || "—";
            probed = "1";
          }
          var tbl2 = $("v7-cn-test-tbl");
          if (tbl2) {
            tbl2.insertAdjacentHTML("beforeend",
              '<tr data-ok="' + (d.ok ? "1" : "0") + '" data-probed="' + probed + '"><td>' + esc(label) +
              "</td><td>" + cell + "</td><td>" + esc(detailText) + "</td><td>" + esc(d.probed_at || "—") + "</td></tr>");
          }
          step();
        });
      }
      step();
    });
  }

  /* ---------- 32. 选股器（v3.4.0：恢复 screener/refresh · screener/run） ---------- */
  var SCREENER_FACTORS = [
    ["pe", "PE-TTM", "倍"], ["pb", "PB-MRQ", "倍"], ["ps", "PS-TTM", "倍"],
    ["pcf", "经营现金流市值比", "倍"], ["chg", "涨跌幅", "%"], ["mktcap", "总市值", "亿元"],
    ["roe_min", "ROE（近3年年报均）", "%"], ["gross", "销售毛利率", "%"],
    ["rev_yoy", "营收同比", "%"], ["np_yoy", "净利同比", "%"],
    ["debt_ratio", "资产负债率", "%"], ["ocf", "经营现金流净额", "元"],
    ["dividend_yield", "股息率", "%"]
  ];

  /** 条件行 HTML（4 行固定槽位，留空=忽略）。 */
  function screenerRowsHtml() {
    var opts = SCREENER_FACTORS.map(function (f) {
      return '<option value="' + f[0] + '">' + esc(f[1]) + "</option>";
    }).join("");
    var rows = "";
    for (var i = 0; i < 4; i += 1) {
      rows += '<div class="v7-form" style="margin-bottom:6px">' +
        '<div><label>因子 ' + (i + 1) + '</label><select class="scr-factor">' + opts + "</select></div>" +
        '<div><label>关系</label><select class="scr-op"><option value="lte">≤</option><option value="gte">≥</option></select></div>' +
        '<div><label>阈值</label><input class="scr-value" placeholder="留空=不用该条件"></div>' +
        "</div>";
    }
    return rows;
  }

  /** 选股页注入条件筛选卡。 */
  function injectScreenerPanel() {
    if (currentId() !== "screener") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-screener")) { return; }
    var card = document.createElement("div");
    card.className = "card v7-screener";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">⌕</span>条件选股</span>' +
        '<span class="card-note">查本地快照（&lt;1s），不触源站</span></div>' +
        '<div class="v-body">' +
          '<div id="v7-scr-status"><div class="loading">读取快照状态…</div></div>' +
          '<div style="margin:8px 0 4px" class="v7-hint">筛选条件（AND，最多 4 条；留空自动忽略）</div>' +
          screenerRowsHtml() +
          '<div class="v7-form" style="margin-bottom:8px">' +
            '<div><label>返回上限</label><input id="v7-scr-limit" value="50"></div>' +
            '<div><button class="v7-btn primary" data-act="scr-run" type="button">执行筛选</button></div>' +
            '<div><button class="v7-btn" data-act="scr-refresh" type="button">刷新快照</button></div>' +
          "</div>" +
          '<div id="v7-scr-body"><div class="v7-hint">设置条件后点「执行筛选」</div></div>' +
        "</div>";
    body.insertBefore(card, body.firstChild);
    loadScreenerStatus();
  }

  /** 快照状态（screener/status）。 */
  function loadScreenerStatus() {
    apiJson("/api/screener/status").then(function (r) {
      var box = $("v7-scr-status");
      if (!box) { return; }
      if (r.body && r.body.error) { box.innerHTML = '<div class="v-empty">' + esc(r.body.error) + "</div>"; return; }
      var d = (r.body && r.body.data) || {};
      var html = statKpis([
        ["快照版本", d.version || "—"],
        ["交易日", d.trade_date || "—"],
        ["覆盖个股", String(d.row_count == null ? "—" : d.row_count)],
        ["股息率覆盖", secFixed(d.dividend_coverage, 1) + "%"],
        ["构建状态", d.building ? "构建中" : "空闲"]
      ]);
      if ((d.progress || {}).total) {
        html += '<div class="v7-hint">进度：' + esc(String(d.progress.done || 0)) + " / " +
          esc(String(d.progress.total)) + "（" + esc(d.progress.step || "—") + "）</div>";
      }
      if (d.last_error) { html += '<div class="v7-sec-miss">上次构建错误：' + esc(d.last_error) + "</div>"; }
      box.innerHTML = html;
    });
  }

  /** 执行筛选（screener/run）。 */
  function runScreener() {
    var box = $("v7-scr-body");
    if (!box) { return; }
    var factors = document.querySelectorAll(".scr-factor");
    var ops = document.querySelectorAll(".scr-op");
    var values = document.querySelectorAll(".scr-value");
    var conds = [];
    for (var i = 0; i < factors.length; i += 1) {
      var raw = ((values[i] || {}).value || "").trim();
      if (!raw) { continue; }
      var num = parseFloat(raw);
      if (!isFinite(num)) { box.innerHTML = '<div class="v-empty">第 ' + (i + 1) + " 条阈值不是数字</div>"; return; }
      conds.push({ factor: factors[i].value, op: (ops[i] || {}).value || "lte", value: num });
    }
    var limit = parseInt((($("v7-scr-limit") || {}).value || "50"), 10);
    if (!isFinite(limit) || limit < 1) { limit = 50; }
    box.innerHTML = '<div class="loading">筛选计算中…</div>';
    apiJson("/api/screener/run", { method: "POST", body: JSON.stringify({ conditions: conds, limit: limit }) })
      .then(function (r) {
        var target = $("v7-scr-body");
        if (!target) { return; }
        if (r.body && r.body.error) {
          target.innerHTML = '<div class="v-empty">' + esc(r.body.error) + "</div>";
          return;
        }
        var d = (r.body && r.body.data) || {};
        var items = d.items || [];
        if (!items.length) {
          target.innerHTML = '<div class="v-empty">无符合条件的个股（条件：' +
            (conds.length ? conds.map(function (c) { return c.factor + (c.op === "lte" ? "≤" : "≥") + c.value; }).join(" 且 ")
              : "未设置，返回快照前 " + limit + " 只") + "）</div>";
          return;
        }
        var html = '<div class="v7-hint" style="margin-bottom:4px">命中 ' + esc(String(d.total_matched)) +
          " 只，返回 " + esc(String(d.returned)) + " 只" + (d.truncated ? "（已截断）" : "") +
          " · 快照 " + esc((d.snapshot || {}).trade_date || "—") + " · " + esc(d.caliber || "") + "</div>";
        html += '<div class="v7-preview-table"><table><tr><th>代码</th><th>名称</th><th>板块</th><th>收盘</th>' +
          "<th>涨跌幅</th><th>PE</th><th>PB</th><th>ROE(3年最低)</th><th>资产负债率</th><th>股息率</th></tr>" +
          items.map(function (x) {
            return "<tr><td>" + esc(x.code) + "</td><td>" + esc(x.name) + "</td><td>" + esc(x.board || "—") + "</td><td>" +
              secFixed(x.close, 2) + "</td><td>" + secSigned(x.chg, 2, "%") + "</td><td>" + secFixed(x.pe, 2) +
              "</td><td>" + secFixed(x.pb, 2) + "</td><td>" + secFixed(x.roe3y_min, 2) + "</td><td>" +
              secFixed(x.debt_ratio, 2) + "</td><td>" + secFixed(x.dividend_yield, 2) + "</td></tr>";
          }).join("") + "</table></div>";
        target.innerHTML = html;
      });
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
    var code = newsVal("v7-news-code", newsInput.code);
    var title = newsVal("v7-ai-title", newsInput.title);
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

  /* ---------- 35. 移动端抽屉（v3.4.0 修 R-01：≤768px 侧栏改为抽屉） ---------- */
  /** 绑定汉堡按钮 / 遮罩 / Esc / 点条目自动收起。 */
  function bindMobileNav() {
    var btn = $("menu-toggle");
    var side = $("side-nav");
    if (!btn || !side) { return; }
    var backdrop = $("sb-backdrop");
    if (!backdrop) {
      backdrop = document.createElement("div");
      backdrop.className = "sb-backdrop";
      backdrop.id = "sb-backdrop";
      backdrop.hidden = true;
      document.body.appendChild(backdrop);
    }

    function setOpen(open) {
      side.classList.toggle("open", open);
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      btn.setAttribute("aria-label", open ? "关闭导航" : "打开导航");
      backdrop.hidden = !open;
    }
    window.V7_SET_SIDEBAR = setOpen;

    btn.addEventListener("click", function (ev) {
      ev.stopPropagation();
      setOpen(!side.classList.contains("open"));
    });
    backdrop.addEventListener("click", function () { setOpen(false); });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") { setOpen(false); }
    });
    // 点侧栏条目后自动收起（移动端点完就走）
    side.addEventListener("click", function (ev) {
      var item = ev.target.closest ? ev.target.closest(".side-item") : null;
      if (item && window.matchMedia("(max-width: 768px)").matches) { setOpen(false); }
    });
    window.addEventListener("hashchange", function () { setOpen(false); });
    window.addEventListener("resize", function () {
      if (!window.matchMedia("(max-width: 768px)").matches) { setOpen(false); }
    });
  }

  /* ---------- 初始化 ---------- */
  function init() {
    bindLogo();
    bindCollapse();
    bindMobileNav();
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
    watchViewRender();
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
