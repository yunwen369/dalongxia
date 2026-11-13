/* W7.detail——切割自 v7_app.js（RF-20261112-01 阶段4）。代码体原样保留。 */
(function () {
  "use strict";
  var W7 = window.W7 = window.W7 || {};
  var C = W7.core;
  var $ = C.$, toast = C.toast, apiJson = C.apiJson, queryParam = C.queryParam;
  var bindImport = function () { return W7.forms.bindImport.apply(null, arguments); };
  var esc = function () { return W7.forms.esc.apply(null, arguments); };
  var injectAboutCard = function () { return W7.forms.injectAboutCard.apply(null, arguments); };
  var injectAiTestPanel = function () { return W7.ai.injectAiTestPanel.apply(null, arguments); };
  var injectAlertsToolbar = function () { return W7.misc.injectAlertsToolbar.apply(null, arguments); };
  var injectComboPanel = function () { return W7.fund.injectComboPanel.apply(null, arguments); };
  var injectConnectorsTest = function () { return W7.misc.injectConnectorsTest.apply(null, arguments); };
  var injectConnectorsToolbar = function () { return W7.forms.injectConnectorsToolbar.apply(null, arguments); };
  var injectDashboardDrag = function () { return W7.portfolio.injectDashboardDrag.apply(null, arguments); };
  var injectFundPanel = function () { return W7.fund.injectFundPanel.apply(null, arguments); };
  var injectMoneyPanel = function () { return W7.data.injectMoneyPanel.apply(null, arguments); };
  var injectNewsPanel = function () { return W7.data.injectNewsPanel.apply(null, arguments); };
  var injectPortfolioAttrPanel = function () { return W7.portfolio.injectPortfolioAttrPanel.apply(null, arguments); };
  var injectPortfolioToolbar = function () { return W7.forms.injectPortfolioToolbar.apply(null, arguments); };
  var injectScreenerPanel = function () { return W7.data.injectScreenerPanel.apply(null, arguments); };
  var injectSectorPanel = function () { return W7.data.injectSectorPanel.apply(null, arguments); };
  var injectSettingsPrefs = function () { return W7.forms.injectSettingsPrefs.apply(null, arguments); };
  var injectSystemPanel = function () { return W7.misc.injectSystemPanel.apply(null, arguments); };
  var injectTableTools = function () { return W7.misc.injectTableTools.apply(null, arguments); };
  var injectWatchToolbar = function () { return W7.misc.injectWatchToolbar.apply(null, arguments); };
  var injectYjbPanel = function () { return W7.fund.injectYjbPanel.apply(null, arguments); };
  var loadCombo = function () { return W7.fund.loadCombo.apply(null, arguments); };
  var loadMoney = function () { return W7.data.loadMoney.apply(null, arguments); };
  var loadNews = function () { return W7.data.loadNews.apply(null, arguments); };
  var loadPortfolioAttr = function () { return W7.portfolio.loadPortfolioAttr.apply(null, arguments); };
  var loadSector = function () { return W7.data.loadSector.apply(null, arguments); };
  var preview = function () { return W7.shell.preview.apply(null, arguments); };
  var restoreCalcResults = function () { return W7.portfolio.restoreCalcResults.apply(null, arguments); };
  var runCrowd = function () { return W7.data.runCrowd.apply(null, arguments); };
  var statKpis = function () { return W7.portfolio.statKpis.apply(null, arguments); };

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
    // BUG-20261112-06：hash 里的 ?symbol= 优先于视图配置与本地记忆，
    // 否则「关键指标」卡（按 hash 取数）与「个股深度」卡（按配置取数）会显示两个标的
    stockSymbol = queryParam("symbol") || (m ? decodeURIComponent(m[1]) : (localStorage.getItem("openbb-symbol") || "300750"));
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
      }).catch(function () {   // F14：失败态，避免停留「行情加载中…」
        var b3 = $("v7-quote-chart");
        if (b3) { b3.innerHTML = '<div class="v-empty">分时数据加载失败</div>'; }
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
    }).catch(function () {   // F14
      var b4 = $("v7-quote-chart");
      if (b4) { b4.innerHTML = '<div class="v-empty">K 线数据加载失败</div>'; }
    });
  }

  function drawQuote(xs, data, name, kind) {
    var box = $("v7-quote-chart");
    if (!box || !window.echarts) { return; }
    box.innerHTML = "";
    var chart = OB.chart.init(box);
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
      W7.st.newsSort = ns.getAttribute("data-news-sort");
      loadNews("AI 资讯流");
      return true;
    }
    var nr = event.target.closest ? event.target.closest("[data-news-region]") : null;
    if (nr) {
      Array.prototype.forEach.call(document.querySelectorAll("[data-news-region]"), function (b) { b.classList.toggle("on", b === nr); });
      W7.st.newsRegion = nr.getAttribute("data-news-region");
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
      W7.st.comboRisk = cr.getAttribute("data-combo-risk");
      loadCombo(W7.st.comboState);
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
      W7.st.northMetric = mm.getAttribute("data-money-metric");
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
        W7.st.sectorMarket = smk.getAttribute("data-sec-market");
        loadSector(W7.st.sectorTabState);
        return;
      }
      var skd = event.target.closest ? event.target.closest("[data-sec-kind]") : null;
      if (skd) {
        W7.st.sectorKind = skd.getAttribute("data-sec-kind");
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
    var nvr = event.target.closest ? event.target.closest("[data-nav-range]") : null;
    if (nvr) {
      Array.prototype.forEach.call(document.querySelectorAll("[data-nav-range]"), function (b) {
        b.classList.toggle("on", b === nvr);
      });
      W7.fund.loadNavRange(nvr.getAttribute("data-nav-range"));
      return true;
    }
      return false;
  }


  /* 跨域导出（W7.detail） */
  W7.detail = W7.detail || {};
  W7.detail.bindKlineTools = bindKlineTools;
  W7.detail.currentId = currentId;
  W7.detail.handleTabs = handleTabs;
  W7.detail.injectKlineTools = injectKlineTools;
  W7.detail.injectQuoteChart = injectQuoteChart;
  W7.detail.injectStockPanel = injectStockPanel;
  W7.detail.injectSymbolBar = injectSymbolBar;
  W7.detail.markIndicator = markIndicator;
  W7.detail.markPeriod = markPeriod;
  W7.detail.watchRoute = watchRoute;
})();
