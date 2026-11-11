/* V6 视觉稿渲染逻辑（独立评审稿）。
   数据全部来自同源 /api/* 真实接口；接口不可达时降级为 data/demoData.js，
   并统一显示「演示数据·非真实行情」角标。依赖：本地 vendor/echarts.min.js。 */

(function () {
  "use strict";

  var PERIOD = encodeURIComponent("日k");
  var API = {
    index: "/api/market/index?market=a",
    indexKline: function (code) {
      return "/api/market/index-kline?code=" + encodeURIComponent(code) + "&period=" + PERIOD;
    },
    sentiment: "/api/sentiment/market",
    industry: "/api/sector/industry",
    summary: "/api/sector/summary",
    crowding: "/api/sector/crowding/rankings",
    alerts: "/api/alerts/events",
    macro: "/api/macro/overview"
  };

  var $ = function (id) { return document.getElementById(id); };
  var esc = function (value) {
    return String(value == null ? "—" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  };
  var num = function (value, digits) {
    return typeof value === "number" && isFinite(value)
      ? value.toFixed(digits == null ? 2 : digits)
      : "—";
  };
  var signClass = function (value) { return value >= 0 ? "up" : "down"; };
  var signText = function (value, digits) {
    return (value >= 0 ? "+" : "") + num(value, digits == null ? 2 : digits) + "%";
  };
  var sparkSeq = 0;
  var VIEWS = window.V6_VIEWS || {};
  var SIDEBAR_GROUPS = [
    ["overview", "总览"], ["market", "市场"], ["research", "投研"],
    ["my", "我的"], ["sys", "系统"]
  ];
  var VIEW_IDS = Object.keys(VIEWS);
  var pinnedGroups = {};     // 用户手动展开/折叠的分组；未登记的组只随当前视图自动展开

  /** 拉取真实接口，失败时按 key 降级为演示数据。 */
  async function getJSON(path, demoKey) {
    try {
      var response = await fetch(path, { cache: "no-store" });
      if (!response.ok) { throw new Error("HTTP " + response.status); }
      var payload = await response.json();
      if (!payload || payload.data == null) {
        // 业务态（未绑定 / 无权限 / EMPTY_DATA）不是通道故障：保留真实文案，不降级演示数据。
        return { data: {}, error: (payload && payload.error) || "empty data", demo: false };
      }
      return { data: payload.data, demo: false };
    } catch (error) {
      var fallback = window.DEMO_DATA && window.DEMO_DATA[demoKey];
      if (fallback) {
        return { data: fallback, demo: true, error: String(error) };
      }
      return { data: null, demo: true, error: String(error) };
    }
  }

  /** 标记数据来源角标：真实接口路径 / 演示数据告警。 */
  function tagSource(id, result, path) {
    var node = $(id);
    if (!node) { return; }
    node.classList.toggle("demo", !!result.demo);
    var shown = path;
    try { shown = decodeURIComponent(path); } catch (ignored) { shown = path; }
    node.textContent = result.demo
      ? "演示数据·非真实行情（" + (result.error || "接口不可达") + "）"
      : "真实接口 " + shown;
  }

  /** 迷你面积折线（纯 SVG，无依赖）。 */
  function sparkSVG(values, positive) {
    if (!values || values.length < 2) { return ""; }
    var w = 120;
    var h = 34;
    var pad = 2;
    var min = Math.min.apply(null, values);
    var max = Math.max.apply(null, values);
    var span = (max - min) || 1;
    var points = values.map(function (value, i) {
      return [
        pad + (i * (w - 2 * pad)) / (values.length - 1),
        h - pad - ((value - min) / span) * (h - 2 * pad)
      ];
    });
    var line = points.map(function (p) { return p[0].toFixed(1) + "," + p[1].toFixed(1); }).join(" L");
    var area = "M" + points[0][0].toFixed(1) + "," + h + " L" + line +
      " L" + points[points.length - 1][0].toFixed(1) + "," + h + " Z";
    var color = positive ? "#ff3b4e" : "#00c087";
    var gid = "spark" + (++sparkSeq);
    return '<svg viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none">' +
      "<defs><linearGradient id=\"" + gid + "\" x1=\"0\" y1=\"0\" x2=\"0\" y2=\"1\">" +
      "<stop offset=\"0%\" stop-color=\"" + color + "\" stop-opacity=\"0.62\"/>" +
      "<stop offset=\"100%\" stop-color=\"" + color + "\" stop-opacity=\"0.02\"/>" +
      "</linearGradient></defs>" +
      "<path d=\"" + area + "\" fill=\"url(#" + gid + ")\"/>" +
      "<path d=\"M" + line + "\" fill=\"none\" stroke=\"" + color +
      "\" stroke-width=\"5\" stroke-opacity=\"0.18\"/>" +
      "<path d=\"M" + line + "\" fill=\"none\" stroke=\"" + color + "\" stroke-width=\"2\"/>" +
      "</svg>";
  }

  /** 第一行：四个指数卡 + 迷你折线。 */
  async function renderIndexCards() {
    var result = await getJSON(API.index, "index");
    var items = (result.data && result.data.items) || [];
    var host = $("index-row");
    var cards = host.querySelectorAll(".card");
    for (var i = 0; i < 4; i += 1) {
      var card = cards[i];
      var item = items[i] || { name: "指数 " + (i + 1), price: null, pct: 0, symbol: "000001" };
      card.innerHTML =
        '<div class="index-name">' + esc(item.name) + "</div>" +
        '<div><span class="index-value ' + signClass(item.pct || 0) + '">' + esc(num(item.price, 2)) + "</span>" +
        '<span class="index-pct ' + signClass(item.pct || 0) + '">' + signText(item.pct || 0) + "</span></div>" +
        '<div class="spark" data-symbol="' + esc(item.symbol) + '"><span class="loading">走势加载中…</span></div>' +
        '<div class="src-tag" id="src-index-' + i + '">' + (result.demo ? "演示数据·非真实行情" : "/api/market/index") + "</div>";
      if (result.demo) { card.querySelector(".src-tag").classList.add("demo"); }
    }
    var asOf = (result.data && result.data.as_of) || "—";
    $("as-of").textContent = "as_of: " + asOf;
    var sparks = host.querySelectorAll(".spark");
    await Promise.all(Array.prototype.map.call(sparks, async function (node, i) {
      var code = items[i] ? items[i].symbol : "000001";
      var kline = await getJSON(API.indexKline(code), "indexKline");
      var rows = (kline.data && kline.data.items) || [];
      var values = rows.slice(-30).map(function (row) { return row.close; });
      var positive = (items[i] ? items[i].pct : 0) >= 0;
      node.innerHTML = values.length ? sparkSVG(values, positive) : '<span class="err">走势无数据</span>';
      if (kline.demo) {
        var tag = $("src-index-" + i);
        if (tag) { tag.classList.add("demo"); tag.textContent = "演示数据·非真实行情（走势）"; }
      }
    }));
    return { items: items, demo: result.demo, data: result.data };
  }

  /** 半环仪表盘（绿→黄→红，pathLength=100 便于按温度取弧长）。 */
  function renderGauge(temperature) {
    var value = Math.max(0, Math.min(100, Number(temperature) || 0));
    $("gauge-svg").innerHTML =
      "<defs><linearGradient id=\"gaugeGrad\" x1=\"0\" y1=\"0\" x2=\"1\" y2=\"0\">" +
      "<stop offset=\"0%\" stop-color=\"#00c087\"/><stop offset=\"55%\" stop-color=\"#f5a623\"/>" +
      "<stop offset=\"100%\" stop-color=\"#ff3b4e\"/></linearGradient></defs>" +
      '<path d="M10 60 A50 50 0 0 1 110 60" fill="none" stroke="#1a2836" stroke-width="13" stroke-linecap="round"/>' +
      '<path d="M10 60 A50 50 0 0 1 110 60" fill="none" stroke="url(#gaugeGrad)" stroke-width="13" ' +
      'stroke-linecap="round" pathLength="100" stroke-dasharray="' + value.toFixed(1) + ' 100"/>' +
      '<text x="60" y="56" text-anchor="middle" font-family="monospace" font-size="26" font-weight="600" ' +
      'style="fill: #edf1f5">' + value.toFixed(0) + "</text>";
  }

  /** 情绪卡与环境判断卡。 */
  async function renderSentiment() {
    var result = await getJSON(API.sentiment, "sentiment");
    var data = result.data || {};
    var breadth = data.breadth || {};
    renderGauge(data.temperature);
    var temp = Number(data.temperature) || 0;
    $("gauge-num").innerHTML = '<span class="' + (temp >= 50 ? "up" : "down") + '">' +
      esc(data.level || "—") + (temp >= 50 ? " ↑" : " ↓") + "</span>";
    $("gauge-level").textContent = "温度 " + num(data.temperature, 1);
    $("gauge-breadth").textContent = "涨 " + (breadth.up != null ? breadth.up : "—") +
      " / 跌 " + (breadth.down != null ? breadth.down : "—");
    tagSource("src-sentiment", result, API.sentiment);

    var macro = await getJSON(API.macro, "macro");
    var quadrant = (macro.data && macro.data.quadrant) || {};
    var levelText = data.level || "—";
    $("env-level").textContent = levelText === "中性" ? "震荡中性" : levelText;
    var upCount = breadth.up || 0;
    var downCount = breadth.down || 0;
    $("env-desc").textContent = "涨跌家数 " + upCount + "/" + downCount +
      "，宏观象限「" + (quadrant.stage || "—") + "」，关注拥挤度。";
    var envResult = {
      demo: !!(macro.demo || result.demo),
      error: macro.error || result.error
    };
    tagSource("src-env", envResult, "/api/sentiment/market + /api/macro/overview");
    return { sentiment: data, macro: macro.data || {}, demo: result.demo || macro.demo };
  }

  /** 综合选股信号（由真实 90 板块涨跌分档派生，口径标注在卡片上）。 */
  async function renderSignals() {
    var result = await getJSON(API.industry, "industry");
    var items = (result.data && result.data.items) || [];
    var strong = items.filter(function (x) { return x.pct >= 3; }).length;
    var mild = items.filter(function (x) { return x.pct > 0 && x.pct < 3; }).length;
    var weak = items.filter(function (x) { return x.pct < 0; }).length;
    var boxes = [
      { label: "今日候选", value: strong + mild, cls: "c1" },
      { label: "重点关注", value: strong, cls: "c2" },
      { label: "可研究", value: mild, cls: "c3" },
      { label: "风险限制", value: weak, cls: "c4" }
    ];
    $("signal-grid").innerHTML = boxes.map(function (box) {
      return '<div class="signal-box ' + box.cls + '">' +
        '<span class="label">' + box.label + "</span>" +
        '<span class="value">' + box.value + '<span class="unit">只</span></span></div>';
    }).join("");
    tagSource("src-signal", result, API.industry + " · 按涨跌幅分档派生");
    return { items: items, demo: result.demo };
  }

  /** 拥挤度分档进度条。 */
  async function renderBars() {
    var result = await getJSON(API.crowding, "crowding");
    var counts = (result.data && result.data.level_counts) || {};
    var rows = [
      { label: "高位", value: counts["高位"] || 0, color: "linear-gradient(90deg,#ff5b6b,#c81f3a)" },
      { label: "偏高", value: counts["偏高"] || 0, color: "linear-gradient(90deg,#ffab3d,#e07a08)" },
      { label: "正常", value: counts["正常"] || 0, color: "linear-gradient(90deg,#00d68f,#0a8f60)" },
      { label: "已扫描", value: (result.data && result.data.scanned) || 0, color: "linear-gradient(90deg,#3a72a8,#1c4f86)" }
    ];
    var max = Math.max.apply(null, rows.map(function (r) { return r.value; })) || 1;
    $("bar-list").innerHTML = rows.map(function (row) {
      // 平方根缩放：保留档位相对关系，避免小值条形不可见；数值本身仍为真实值。
      var width = Math.max(6, Math.sqrt(row.value / max) * 100);
      return '<div class="bar-row"><span class="bar-label">' + row.label + "</span>" +
        '<span class="bar-track"><span class="bar-fill" style="width:' + width.toFixed(1) +
        "%;background:" + row.color + '"></span></span>' +
        '<span class="bar-num">' + row.value + "</span></div>";
    }).join("");
    tagSource("src-bar", result, API.crowding);
    return { data: result.data, demo: result.demo };
  }

  /** 风控概览（只展示真实可得字段，不编造指标）。 */
  async function renderRisk(crowding) {
    var result = await getJSON(API.alerts, "alerts");
    var counts = (crowding.data && crowding.data.level_counts) || {};
    // 风险状态着色：高位红 / 偏高橙 / 无未读绿，让问题一眼可见。
    var rows = [
      ["高位板块", (counts["高位"] || 0) + " 个", "up"],
      ["偏高板块", (counts["偏高"] || 0) + " 个", "warn"],
      ["未读提醒", ((result.data && result.data.unread) || 0) + " 条", "down"],
      ["扫描 / 失败", (crowding.data && crowding.data.scanned || 0) + " / " +
        (((crowding.data && crowding.data.failed) || []).length), ""]
    ];
    $("risk-list").innerHTML = rows.map(function (row) {
      return '<div class="stat-row"><span>' + row[0] + '</span><b class="' +
        row[2] + '">' + esc(row[1]) + "</b></div>";
    }).join("");
    tagSource("src-risk", result, "/api/sector/crowding/rankings + /api/alerts/events");
  }

  /** 今日领涨 / 领跌。 */
  async function renderPerformance() {
    var result = await getJSON(API.summary, "summary");
    var data = result.data || {};
    var rows = (data.gainers || []).slice(0, 2).map(function (x) {
      return ["涨 " + esc(x.name), signText(x.pct), signClass(x.pct)];
    }).concat((data.losers || []).slice(0, 2).map(function (x) {
      return ["跌 " + esc(x.name), signText(x.pct), signClass(x.pct)];
    }));
    rows.push(["板块集中度", num(data.concentration, 2) + "%", ""]);
    $("perf-list").innerHTML = rows.map(function (row) {
      return '<div class="stat-row"><span>' + row[0] + '</span><b class="' + row[2] + '">' + row[1] + "</b></div>";
    }).join("");
    tagSource("src-perf", result, API.summary);
  }

  /** 板块强度排行榜（真实 90 板块取前 10，拥挤度列来自另一真实端点）。 */
  async function renderRanking(industry, crowding) {
    var items = (industry.items || []).slice().sort(function (a, b) { return b.pct - a.pct; }).slice(0, 10);
    var scoreMap = {};
    ((crowding.data && crowding.data.items) || []).forEach(function (row) {
      scoreMap[row.board] = row.score;
    });
    $("rank-body").innerHTML = items.map(function (item, i) {
      var score = scoreMap[item.name];
      var scoreCell = typeof score === "number"
        ? '<span class="score-pill">' + num(score, 1) + "</span>" : "—";
      return "<tr><td><span class=\"rank-badge r" + (i + 1) + "\">" + (i + 1) + "</span></td>" +
        "<td>" + esc(item.name) + "</td><td class=\"mono dim\">" + esc(item.code) + "</td>" +
        '<td class="num ' + signClass(item.pct) + '">' + signText(item.pct) + "</td>" +
        '<td class="num hot">' + num(item.amount, 2) + "</td>" +
        '<td class="num ' + signClass(item.net_inflow) + '">' + num(item.net_inflow, 2) + "</td>" +
        '<td><span class="signal-ico">⌁</span>' + esc(item.lead_stock) + "</td>" +
        "<td>" + scoreCell + "</td>" +
        '<td><span class="study-btn">研究</span></td></tr>';
    }).join("") || '<tr><td colspan="9" class="err">榜单无数据</td></tr>';
    $("table-note").textContent = industry.demo ? "演示数据" : "真实接口 · 90 板块取前 10";
    tagSource("src-rank", industry, API.industry + " 取前 10");
  }

  /** 主 K 线：本地 ECharts 蜡烛图 + 成交量 + MA5/MA10/MA20。 */
  async function renderKline(firstIndex) {
    var code = (firstIndex && firstIndex.symbol) || "000001";
    var result = await getJSON(API.indexKline(code), "indexKline");
    var rows = ((result.data && result.data.items) || []).slice(-60);
    $("kline-name").textContent = (firstIndex && firstIndex.name) || "上证指数";
    $("kline-price").textContent = num(firstIndex && firstIndex.price, 2);
    $("kline-pct").textContent = signText((firstIndex && firstIndex.pct) || 0);
    $("kline-pct").className = "mono " + signClass((firstIndex && firstIndex.pct) || 0);
    tagSource("src-kline", result, API.indexKline(code));
    if (!rows.length || typeof window.echarts === "undefined") {
      $("kline-chart").innerHTML = '<span class="err">K 线无数据或图表库未加载</span>';
      return;
    }
    var dates = rows.map(function (r) { return String(r.date).slice(5); });
    var candles = rows.map(function (r) { return [r.open, r.close, r.low, r.high]; });
    var volumes = rows.map(function (r) {
      return { value: r.volume, itemStyle: { color: r.close >= r.open ? "#ff3b4e" : "#00c087" } };
    });
    var ma = function (n) {
      return rows.map(function (_, i) {
        if (i < n - 1) { return "-"; }
        var sum = 0;
        for (var k = i - n + 1; k <= i; k += 1) { sum += rows[k].close; }
        return (sum / n).toFixed(2);
      });
    };
    var lastMa = function (n) {
      if (rows.length < n) { return "—"; }
      var sum = 0;
      for (var i = rows.length - n; i < rows.length; i += 1) { sum += rows[i].close; }
      return (sum / n).toFixed(2);
    };
    $("kline-code").textContent = code;
    $("ma-legend").innerHTML = [
      ["#f5a623", "MA5", lastMa(5)],
      ["#3fa0c0", "MA10", lastMa(10)],
      ["#c07ae8", "MA20", lastMa(20)]
    ].map(function (item) {
      return '<span class="ma-item"><i style="background:' + item[0] + '"></i>' +
        '<span style="color:' + item[0] + '">' + item[1] + ":" + item[2] + "</span></span>";
    }).join("");
    var chart = window.echarts.init($("kline-chart"), null, { renderer: "canvas" });
    chart.setOption({
      animation: false,
      backgroundColor: "transparent",
      grid: [
        { left: 8, right: 44, top: 8, height: "64%" },
        { left: 8, right: 44, top: "78%", height: "14%" }
      ],
      xAxis: [
        { type: "category", data: dates, gridIndex: 0, axisLine: { lineStyle: { color: "#1e364c" } },
          axisLabel: { color: "#9aa8b6", fontSize: 9 }, splitLine: { show: false } },
        { type: "category", data: dates, gridIndex: 1, axisLine: { lineStyle: { color: "#1e364c" } },
          axisLabel: { show: false }, splitLine: { show: false } }
      ],
      yAxis: [
        { scale: true, gridIndex: 0, position: "right", axisLine: { show: false },
          axisLabel: { color: "#c2ccd6", fontSize: 9 },
          splitLine: { lineStyle: { color: "rgba(130,160,190,.14)", type: "dashed" } } },
        { scale: true, gridIndex: 1, axisLine: { show: false }, axisLabel: { show: false }, splitLine: { show: false } }
      ],
      series: [
        { name: "K线", type: "candlestick", data: candles,
          itemStyle: { color: "#ff4b5f", color0: "#00d68f", borderColor: "#ff4b5f", borderColor0: "#00d68f" } },
        { name: "MA5", type: "line", data: ma(5), smooth: true, symbol: "none", lineStyle: { width: 1.5, color: "#f5a623" } },
        { name: "MA10", type: "line", data: ma(10), smooth: true, symbol: "none", lineStyle: { width: 1.5, color: "#3fa0c0" } },
        { name: "MA20", type: "line", data: ma(20), smooth: true, symbol: "none", lineStyle: { width: 1.5, color: "#c07ae8" } },
        { name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: volumes, barWidth: "55%" }
      ]
    });
    window.addEventListener("resize", function () { chart.resize(); });
    window.__v6Chart = chart;
  }

  /** AI 信号解释：只拼接真实接口返回的结论文本，不生成投资建议。 */
  function renderAiPoints(sentiment, macro) {
    var breadth = sentiment.breadth || {};
    var quadrant = macro.quadrant || {};
    var cut = function (text, limit) {
      var value = String(text || "—");
      return value.length > limit ? value.slice(0, limit) + "…" : value;
    };
    var points = [
      "<b>市场情绪</b>：" + esc(sentiment.level || "—") + " · 温度 " + num(sentiment.temperature, 1),
      "<b>市场宽度</b>：涨 " + (breadth.up != null ? breadth.up : "—") +
        " / 跌 " + (breadth.down != null ? breadth.down : "—"),
      "<b>宏观象限</b>：" + esc(quadrant.stage || "—") + " · " + esc(cut(quadrant.conclusion, 28)),
      "<b>风险提示</b>：" + esc(cut(quadrant.disclaimer, 24))
    ];
    $("ai-points").innerHTML = points.map(function (text) {
      return '<div class="ai-point"><span class="tick">✓</span><span>' + text + "</span></div>";
    }).join("");
  }

  /** 皮肤切换：V6 / 现行深色 / 砚，验证三套并存且基准不变。 */
  function bindSkinSwitch() {
    document.querySelectorAll(".skin-switch button").forEach(function (button) {
      button.addEventListener("click", function () {
        document.querySelectorAll(".skin-switch button").forEach(function (b) { b.classList.remove("active"); });
        button.classList.add("active");
        var skin = button.getAttribute("data-skin");
        if (skin === "dark") { document.body.removeAttribute("data-theme"); }
        else { document.body.setAttribute("data-theme", skin); }
        if (window.__v6Chart) { window.__v6Chart.resize(); }
        if (window.__v6ViewChart) { window.__v6ViewChart.resize(); }
      });
    });
  }

  /** 导航：侧栏事件委托 + 顶栏按 group 跳代表视图 + tabs 点击态。 */
  function bindNav() {
    document.querySelectorAll(".tabs button").forEach(function (button) {
      button.addEventListener("click", function () {
        document.querySelectorAll(".tabs button").forEach(function (b) { b.classList.remove("active"); });
        button.classList.add("active");
      });
    });
    $("side-nav").addEventListener("click", function (event) {
      var item = event.target.closest ? event.target.closest(".side-item") : null;
      var id = item && item.getAttribute("data-view");
      if (id) { location.hash = "#/" + id; return; }
      var group = event.target.closest ? event.target.closest(".side-group") : null;
      if (group) {
        var key = group.getAttribute("data-group");
        var currentGroup = (VIEWS[currentViewId()] || {}).sidebar;
        pinnedGroups[key] = !groupOpen(key, currentGroup);
        renderSidebar(currentViewId());
      }
    });
    document.querySelectorAll(".topnav button").forEach(function (button) {
      button.addEventListener("click", function () {
        var group = button.getAttribute("data-group");
        var first = VIEW_IDS.filter(function (id) { return VIEWS[id].group === group; })[0];
        if (first) { location.hash = "#/" + first; }
      });
    });
  }

  /** 仪表板 48 列演示网格的移除按钮；组件库「添加」由 registry_view.js 的弹窗处理。 */
  function bindDemoActions() {
    document.addEventListener("click", function (event) {
      var api = window.V6_DEMO_API;
      if (!api) { return; }
      var remove = event.target.closest ? event.target.closest(".dw-remove") : null;
      if (remove) { api.remove(remove.getAttribute("data-remove")); }
    });
  }

  /** 动态数据：定时重拉真实接口，走势图重绘 + 数字闪动，不是静态快照。 */
  function startLiveRefresh() {
    var live = { enabled: true, intervalMs: 12000, ticks: 0, busy: false };
    window.V6_LIVE = live;
    live.tick = async function () {
      if (live.busy) { return live.ticks; }
      live.busy = true;
      try {
        live.ticks += 1;
        var id = currentViewId();
        if (id !== "home") {
          await renderGeneric(id, true);
        } else {
          var response = await fetch("/api/market/index?market=a", { cache: "no-store" });
          var payload = await response.json();
          var items = (payload.data && payload.data.items) || [];
          document.querySelectorAll(".index-value").forEach(function (node, i) {
            if (!items[i]) { return; }
            var next = Number(items[i].price).toFixed(2);
            if (node.textContent !== next) {
              node.textContent = next;
              node.classList.remove("live-flash");
              void node.offsetWidth;
              node.classList.add("live-flash");
            }
          });
        }
      } catch (ignored) {
        // 网络抖动留到下一轮重试，不中断定时刷新。
      } finally {
        live.busy = false;
      }
      return live.ticks;
    };
    setInterval(live.tick, live.intervalMs);
  }

  /** 当前 hash 视图（kebab-case，对齐 §7.3.1(2)）。 */
  function currentViewId() {
    var id = (location.hash || "").replace(/^#\/?/, "").trim();
    return Object.prototype.hasOwnProperty.call(VIEWS, id) ? id : "home";
  }

  /** 分组是否展开：用户手动固定过的听用户的，否则只自动展开当前视图所属组。 */
  function groupOpen(key, currentGroup) {
    if (Object.prototype.hasOwnProperty.call(pinnedGroups, key)) { return pinnedGroups[key]; }
    return key === currentGroup;
  }

  /** 侧栏：按 NAV_TREE 的 sidebar 分组，可折叠；切组自动展开、离开自动收起。 */
  function renderSidebar(current) {
    var currentGroup = (VIEWS[current] || {}).sidebar;
    var html = "";
    SIDEBAR_GROUPS.forEach(function (group) {
      var ids = VIEW_IDS.filter(function (id) { return VIEWS[id].sidebar === group[0]; });
      if (!ids.length) { return; }
      var open = groupOpen(group[0], currentGroup);
      html += '<div class="side-group" data-group="' + group[0] + '"><span>' + group[1] +
        '</span><span class="grp-caret">' + (open ? "▾" : "▸") + "</span></div>";
      if (!open) { return; }
      ids.forEach(function (id) {
        html += '<div class="side-item' + (id === current ? " active" : "") +
          '" data-view="' + id + '"><i class="side-ico"></i>' + esc(VIEWS[id].title) + "</div>";
      });
    });
    html += '<svg class="side-art" viewBox="0 0 120 92" aria-hidden="true">' +
      '<defs><linearGradient id="artGrad" x1="0" y1="0" x2="1" y2="1">' +
      '<stop offset="0%" stop-color="#3a72a8" stop-opacity=".95"/>' +
      '<stop offset="100%" stop-color="#00d68f" stop-opacity=".35"/></linearGradient></defs>' +
      '<path d="M18 30 C4 22 5 46 22 50 M102 30 C116 22 115 46 98 50" ' +
      'fill="none" stroke="url(#artGrad)" stroke-width="2.4"/>' +
      '<path d="M22 48 Q60 32 98 48 L88 78 Q60 92 32 78 Z" ' +
      'fill="rgba(30,110,220,.10)" stroke="url(#artGrad)" stroke-width="1.8"/>' +
      '<path d="M42 60 L52 64 M78 60 L68 64" stroke="#3f86c0" stroke-width="2"/>' +
      '<path d="M8 84 Q60 70 112 84" fill="none" stroke="#2f6cae" stroke-width="1" stroke-opacity=".5"/>' +
      "</svg>";
    html += '<div class="side-foot">NAV_TREE 同源 · ' + VIEW_IDS.length +
      ' 视图<br>皮肤评审稿 · 只读<br><span class="mono">v2.2.0 / visual-draft</span></div>';
    $("side-nav").innerHTML = html;
  }

  /** 顶栏高亮：group 与 NAV_TREE 同源。 */
  function setTopActive(group) {
    document.querySelectorAll(".topnav button").forEach(function (button) {
      button.classList.toggle("active", button.getAttribute("data-group") === group);
    });
  }

  /** 面包屑 + 标题 + 元信息。 */
  function renderHead(view, id) {
    var chain = view.crumb || [view.title];
    $("view-crumb").innerHTML = chain.map(function (text, i) {
      return (i ? '<span class="sep">/</span>' : "") +
        (i === chain.length - 1 ? '<span class="cur">' + esc(text) + "</span>" : esc(text));
    }).join("");
    $("view-title").textContent = view.title;
    $("view-meta").textContent = "#/" + id + " · " + view.group + " / " + view.sidebar + " · " + view.template;
  }

  /** 通用视图蜡烛图（stock / charting）。 */
  function initViewChart(map, key) {
    var host = $("v-chart");
    if (!host || typeof window.echarts === "undefined") { return; }
    var rows = (((map[key] || {}).data || {}).kline || []).slice(-60);
    if (!rows.length) { host.innerHTML = '<div class="v-empty">K 线无数据</div>'; return; }
    var dates = rows.map(function (r) { return String(r.date).slice(5); });
    var candles = rows.map(function (r) { return [r.open, r.close, r.low, r.high]; });
    var volumes = rows.map(function (r) {
      return { value: r.volume, itemStyle: { color: r.close >= r.open ? "#ff3b4e" : "#00c087" } };
    });
    var chart = window.echarts.init(host, null, { renderer: "canvas" });
    chart.setOption({
      animation: false, backgroundColor: "transparent",
      grid: [{ left: 52, right: 16, top: 12, height: "58%" }, { left: 52, right: 16, top: "76%", height: "16%" }],
      xAxis: [
        { type: "category", data: dates, gridIndex: 0, axisLine: { lineStyle: { color: "#1c3247" } }, axisLabel: { color: "#6c7b8b", fontSize: 10 } },
        { type: "category", data: dates, gridIndex: 1, axisLabel: { show: false }, axisLine: { lineStyle: { color: "#1c3247" } } }
      ],
      yAxis: [
        { scale: true, gridIndex: 0, axisLabel: { color: "#6c7b8b", fontSize: 10 }, splitLine: { lineStyle: { color: "rgba(70,130,210,.12)" } } },
        { scale: true, gridIndex: 1, axisLabel: { show: false }, splitLine: { show: false } }
      ],
      series: [
        { type: "candlestick", data: candles, itemStyle: { color: "#ff3b4e", color0: "#00c087", borderColor: "#ff3b4e", borderColor0: "#00c087" } },
        { type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: volumes, barWidth: "55%" }
      ]
    });
    window.__v6ViewChart = chart;
  }

  /** 通用视图：按配置并发取真实接口，失败逐项降级。 */
  async function renderGeneric(id, quiet) {
    var view = VIEWS[id];
    var host = $("view-body");
    var generic = $("view-generic");
    var keepBody = quiet ? host.scrollTop : 0;
    var keepGeneric = quiet ? generic.scrollTop : 0;
    $("home-main").hidden = true;
    generic.hidden = false;
    renderHead(view, id);
    if (!quiet) { host.innerHTML = '<div class="loading">视图加载中…</div>'; }
    var api = view.api || {};
    var keys = Object.keys(api);
    var results = await Promise.all(keys.map(function (key) {
      return getJSON(api[key][0], api[key][1]);
    }));
    var map = {};
    keys.forEach(function (key, i) { map[key] = results[i]; });
    try {
      var html = view.render(map, view);
      if (view.chart && window.V6_HELPERS && window.V6_HELPERS.chartCard) {
        var source = map[view.chart.key];
        var series = [];
        try {
          series = view.chart.pick((source && source.data) || {});
        } catch (ignored) {
          series = [];
        }
        html += window.V6_HELPERS.chartCard(view.chart, series);
      }
      host.innerHTML = html;
      if (quiet) { host.scrollTop = keepBody; generic.scrollTop = keepGeneric; }
      if (view.chartKey) { initViewChart(map, view.chartKey); }
    } catch (error) {
      host.innerHTML = '<div class="err">视图渲染异常：' + esc(String(error)) + "</div>";
    }
    var demoKeys = keys.filter(function (key, i) { return results[i].demo; });
    var src = $("view-src");
    src.classList.toggle("demo", demoKeys.length > 0);
    src.textContent = demoKeys.length
      ? "演示数据·非真实行情（降级端点：" + demoKeys.join(", ") + "）"
      : "真实接口：" + keys.map(function (key) { return decodeURIComponent(api[key][0]); }).join("  |  ");
  }

  /** 路由：home 显示原首页，其余视图显示通用视图外壳。 */
  function route() {
    var id = currentViewId();
    var view = VIEWS[id] || VIEWS.home;
    renderSidebar(id);
    setTopActive(view.group);
    if (id === "home") {
      $("home-main").hidden = false;
      $("view-generic").hidden = true;
      return;
    }
    renderGeneric(id);
  }

  /** 状态条日期。 */
  function renderDate() {
    var now = new Date();
    var week = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"][now.getDay()];
    var pad = function (n) { return String(n).padStart(2, "0"); };
    $("today-text").textContent = now.getFullYear() + "-" + pad(now.getMonth() + 1) + "-" +
      pad(now.getDate()) + " " + pad(now.getHours()) + ":" + pad(now.getMinutes()) +
      " (" + week + ")";
  }

  /** 主流程：并行拉取，失败逐项降级，不阻塞其它卡片。 */
  async function main() {
    renderDate();
    bindSkinSwitch();
    bindNav();
    bindDemoActions();
    startLiveRefresh();
    // 2026-11-12 修复：原先 route() 与 hashchange 监听放在全部首页数据 await 之后，
    // 导致首屏最长 30s 无侧栏/顶栏，且期间的 hash 跳转被丢弃。改为启动即渲染导航。
    route();
    window.addEventListener("hashchange", route);
    try {
      var settled = await Promise.all([
        renderIndexCards(),
        renderSentiment(),
        renderSignals(),
        renderBars(),
        renderPerformance()
      ]);
      var indexResult = settled[0];
      var sentimentResult = settled[1];
      var industryResult = settled[2];
      var crowdingResult = settled[3];
      var firstIndex = (indexResult.items || [])[0];
      await Promise.all([
        renderRisk(crowdingResult),
        renderRanking(industryResult, crowdingResult),
        renderKline(firstIndex),
        renderAiPoints(sentimentResult.sentiment, sentimentResult.macro)
      ]);
      var anyDemo = settled.some(function (r) { return r && r.demo; });
      $("src-name").textContent = anyDemo ? "部分降级" : "本地真实接口";
      $("conn-state").textContent = anyDemo ? "部分降级" : "已连接";
    } catch (error) {
      $("conn-state").textContent = "渲染异常";
      $("src-name").textContent = String(error);
    }
    // 路由在 main() 开头已渲染并监听，此处不再重复 route()（避免覆盖用户在加载期间的跳转）。
  }

  document.addEventListener("DOMContentLoaded", main);
})();
