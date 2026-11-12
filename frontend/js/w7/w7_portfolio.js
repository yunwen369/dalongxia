/* W7.portfolio——切割自 v7_app.js（RF-20261112-01 阶段4）。代码体原样保留。 */
(function () {
  "use strict";
  var W7 = window.W7 = window.W7 || {};
  var C = W7.core;
  var $ = C.$, toast = C.toast, apiJson = C.apiJson, queryParam = C.queryParam;
  var bindImport = function () { return W7.forms.bindImport.apply(null, arguments); };
  var currentId = function () { return W7.detail.currentId.apply(null, arguments); };
  var esc = function () { return W7.forms.esc.apply(null, arguments); };
  var injectAboutCard = function () { return W7.forms.injectAboutCard.apply(null, arguments); };
  var injectAiTestPanel = function () { return W7.ai.injectAiTestPanel.apply(null, arguments); };
  var injectAlertsToolbar = function () { return W7.misc.injectAlertsToolbar.apply(null, arguments); };
  var injectComboPanel = function () { return W7.fund.injectComboPanel.apply(null, arguments); };
  var injectConnectorsTest = function () { return W7.misc.injectConnectorsTest.apply(null, arguments); };
  var injectConnectorsToolbar = function () { return W7.forms.injectConnectorsToolbar.apply(null, arguments); };
  var injectFundPanel = function () { return W7.fund.injectFundPanel.apply(null, arguments); };
  var injectKlineTools = function () { return W7.detail.injectKlineTools.apply(null, arguments); };
  var injectMoneyPanel = function () { return W7.data.injectMoneyPanel.apply(null, arguments); };
  var injectNewsPanel = function () { return W7.data.injectNewsPanel.apply(null, arguments); };
  var injectPortfolioToolbar = function () { return W7.forms.injectPortfolioToolbar.apply(null, arguments); };
  var injectQuoteChart = function () { return W7.detail.injectQuoteChart.apply(null, arguments); };
  var injectScreenerPanel = function () { return W7.data.injectScreenerPanel.apply(null, arguments); };
  var injectSectorPanel = function () { return W7.data.injectSectorPanel.apply(null, arguments); };
  var injectSettingsPrefs = function () { return W7.forms.injectSettingsPrefs.apply(null, arguments); };
  var injectStockPanel = function () { return W7.detail.injectStockPanel.apply(null, arguments); };
  var injectSymbolBar = function () { return W7.detail.injectSymbolBar.apply(null, arguments); };
  var injectSystemPanel = function () { return W7.misc.injectSystemPanel.apply(null, arguments); };
  var injectTableTools = function () { return W7.misc.injectTableTools.apply(null, arguments); };
  var injectWatchToolbar = function () { return W7.misc.injectWatchToolbar.apply(null, arguments); };
  var injectYjbPanel = function () { return W7.fund.injectYjbPanel.apply(null, arguments); };
  var preview = function () { return W7.shell.preview.apply(null, arguments); };
  var secFixed = function () { return W7.data.secFixed.apply(null, arguments); };
  var secSigned = function () { return W7.data.secSigned.apply(null, arguments); };
  var secSignedText = function () { return W7.data.secSignedText.apply(null, arguments); };

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

  /* ---------- 21. 仪表板栅格拖拽排序（恢复旧版 drag 能力，落库） ---------- */
  /** 宽度档位 → 分数标签（与 views.js 演示卡同口径）。 */
  var V7_SPAN_LABEL = { 12: "1/4", 16: "1/3", 20: "5/12", 24: "1/2", 28: "7/12", 32: "2/3", 48: "整行" };

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
    // 预览卡「移除」：与画布一致——删组件并落库（BUG-20261112-04 恢复该能力）
    grid.addEventListener("click", function (e) {
      var btn = e.target.closest(".dw-remove");
      if (!btn) { return; }
      e.preventDefault();
      e.stopPropagation();
      var id = btn.getAttribute("data-remove");
      var before = (target.widgets || []).length;
      target.widgets = (target.widgets || []).filter(function (w) { return w.id !== id; });
      if (target.widgets.length === before) { return; }
      apiJson("/api/dashboards", { method: "PUT", body: JSON.stringify({
        dashboards: list, activeId: data.activeId, groups: data.groups || [] }) })
        .then(function (r) {
          var ok = r.ok && r.body && !r.body.error;
          toast(ok ? "组件已移除" : ((r.body && r.body.error) || "移除失败"));
          if (!ok) { return; }
          var node = btn.closest(".demo-widget");
          if (node) { node.remove(); }
          if (!grid.querySelector(".demo-widget")) {
            grid.innerHTML = '<div class="v-empty">仪表板为空，从组件库添加</div>';
          }
        });
    });
  }

  /** 把 /api/dashboards 的真实布局注入「48 栅格」预览卡。
   *
   * BUG-20261112-04：视图是异步渲染的，afterRoute 首次调用时栅格容器往往还没挂载，
   * 原实现直接 return 且无重试，导致同一页面在「切路由进入」时才注入、刷新进入时不注入，
   * 表现为时好时坏。此处改为退避重试（最多 12 次 / 约 7 秒）。
   *
   * @param {number} [attempt] 当前重试次数（内部递归用）
   * @returns {void} 无返回值
   */
  function injectDashboardDrag(attempt) {
    if (currentId() !== "dashboard") { return; }
    var grid = document.querySelector(".demo-dash-grid");
    if (!grid) {
      if ((attempt || 0) < 12) {
        setTimeout(function () { injectDashboardDrag((attempt || 0) + 1); }, 600);
      }
      return;
    }
    if (grid.getAttribute("data-v7-bound") === "1") { return; }
    Promise.all([
      fetch("/api/dashboards").then(function (r) { return r.json(); }),
      fetch("/api/widgets").then(function (r) { return r.json(); })
    ]).then(function (res) {
      var data = (res[0] && res[0].data) || {};
      var list = data.dashboards || [];
      var target = list.filter(function (x) { return x.id === data.activeId; })[0] || list[0];
      if (!target) { return; }
      var meta = {};
      // /api/widgets 的真实字段名是 items（不是 widgets），旧写法取不到标题与分类
      var reg = (res[1] && res[1].data) || {};
      (reg.items || reg.widgets || []).forEach(function (x) { meta[x.id] = x; });
      var ws = target.widgets || [];
      // BUG-20261112-04：此前覆写丢失 grid-column: span N，全部卡片退化成 1/48 列宽，
      // 被压成一条竖排文字。此处按 widget.span 还原列宽，并补齐标题/宽度/移除三段结构。
      grid.innerHTML = ws.length ? ws.map(function (x) {
        var m = meta[x.id] || {};
        var span = Number(x.span) || 48;
        return '<div class="demo-widget cat-' + esc(m.category || "table") + '" draggable="true" data-id="' + esc(x.id) +
          '" data-span="' + span + '" style="grid-column: span ' + span + '">' +
          '<div class="dw-head"><b>' + esc(x.id) + "</b><span>" + esc(m.title || x.id) + "</span></div>" +
          '<div class="dw-meta">span ' + span + " · " + (V7_SPAN_LABEL[span] || "—") + " / 48 列</div>" +
          '<button class="dw-remove" type="button" draggable="false" data-remove="' + esc(x.id) + '">移除</button>' +
          "</div>";
      }).join("") : '<div class="v-empty">仪表板为空，从组件库添加</div>';
      grid.setAttribute("data-v7-bound", "1");
      bindGridDrag(grid, target, list, data);
      var note = grid.closest(".card") && grid.closest(".card").querySelector(".card-note");
      if (note) { note.textContent = "拖拽卡片可调整顺序，松手即保存到 /api/dashboards"; }
    }).catch(function () { /* 静默：预览卡属辅助视图，失败不影响主画布 */ });
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
        var chart = OB.chart.init($("bt-chart"));
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
          var chart = OB.chart.init($("bt-chart"));
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


  /* 跨域导出（W7.portfolio） */
  W7.portfolio = W7.portfolio || {};
  W7.portfolio.bindDashboardPersist = bindDashboardPersist;
  W7.portfolio.injectDashboardDrag = injectDashboardDrag;
  W7.portfolio.injectPortfolioAttrPanel = injectPortfolioAttrPanel;
  W7.portfolio.loadPortfolioAttr = loadPortfolioAttr;
  W7.portfolio.restoreCalcResults = restoreCalcResults;
  W7.portfolio.runBacktest = runBacktest;
  W7.portfolio.runDca = runDca;
  W7.portfolio.statKpis = statKpis;
  W7.portfolio.watchViewRender = watchViewRender;
})();
