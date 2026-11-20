/* V6 视觉稿渲染逻辑（独立评审稿）。
   数据全部来自同源 /api/* 真实接口；接口不可达时降级为 data/demoData.js，
   并统一显示「演示数据·非真实行情」角标。依赖：本地 vendor/echarts.min.js。 */

(function () {
  "use strict";

  var PERIOD = encodeURIComponent("日k");
  var INDICATOR = "MA";          // MA / MACD / KDJ（v3.3.0 恢复旧版技术指标切换，内部数据键）
  var lastIndexRef = null;       // 最近一次渲染用到的指数，供周期/指标切换复用
  var API = {
    index: "/api/market/index?market=a",
    indexKline: function (code) {
      return "/api/market/index-kline?code=" + encodeURIComponent(code) + "&period=" + PERIOD;
    },
    sentiment: "/api/sentiment/market",
    industry: "/api/sector/industry",
    summary: "/api/sector/summary",
    // v3.7.0（②）：limit=120 取全量扫描结果 —— 后端 level_counts 按全部扫描计，
    // 若仍用默认 limit=50 会「计数 20 个、名单只有 8 个」，明细与计数对不上。
    crowding: "/api/sector/crowding/rankings?limit=120",
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
  /* BUG-20261119-01：持有用户输入/会话状态的交互型视图，禁止 12 秒定时静默重渲染
     （host.innerHTML 重建会清空聊天、输入框、表单与计算结果）。 */
  var INTERACTIVE_VIEWS = {
    "ai": 1,                    // 智能助手：聊天会话 + 输入框
    "backtest": 1,              // 组合回测：参数表单 + 结果
    "dca": 1,                   // 定投计算器：参数表单 + 结果
    "screener": 1,              // 智能选股：筛选表单 + 结果
    "settings": 1,              // 系统设置：配置表单
    "connectors": 1,            // 连接器：开关与选择
    "dashboard-manage": 1,      // 仪表板管理：管理操作
    "watchlist-edit": 1,        // 编辑自选：表单
    "alerts-edit": 1,           // 编辑提醒：表单
    "portfolio-edit": 1         // 编辑持仓：表单
  };
  var SIDEBAR_GROUPS = [
    ["cloud", "大盘云图"], ["overview", "总览"], ["market", "市场"], ["research", "投研"],
    ["my", "我的"], ["sys", "系统"]
  ];
  var VIEW_IDS = Object.keys(VIEWS);
  var APP_VER = "";        // 由 /api/health 回填（侧栏页脚展示真实版本，不再写死）
  // 用户手动展开/折叠的分组；未登记的组只随当前视图自动展开。
  // v3.6.1 修复 D-3：V6 重写后只留内存态、丢掉了落盘（旧 v6 tree.js 有持久化，
  // OB.config.KEYS.SB_GROUPS 亦登记为「侧栏分组折叠记忆」契约键）→ 启动读取 + 变更写回。
  var pinnedGroups = OB.store.getJSON(OB.config.KEYS.SB_GROUPS, {}) || {};

  /** 拉取真实接口，失败时按 key 降级为演示数据。（阶段2：fetch 骨架收编 OB.http.getJSON） */
  async function getJSON(path, demoKey) {
    try {
      var payload = await OB.http.getJSON(path);
      if (!payload || payload.data == null) {
        // 业务态（未绑定 / 无权限 / EMPTY_DATA）不是通道故障：保留真实文案，不降级演示数据。
        return { data: {}, error: (payload && payload.error) || "暂无数据", demo: false };
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
    $("as-of").textContent = "数据时间：" + asOf;
    var sparks = host.querySelectorAll(".spark");
    // v3.6.1（D-2 修复）：迷你走势改为**不阻塞主流程**。
    // 原实现 `await Promise.all(...)` 会等 4 个 index-kline 逐个真实取数完成
    // （受 V8 串行 + 上游限流，实测 8–18 秒），把 main() 第二波渲染
    // （风险等级 / 行业排行 / K 线 / AI 要点）一并拖到同一时长。
    // 现在立即返回，走势在后台补齐；失败仍照旧给「走势无数据」并标记降级来源。
    Promise.all(Array.prototype.map.call(sparks, async function (node, i) {
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
    })).catch(function () { /* 迷你走势非关键路径，失败不影响主渲染 */ });
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

  /* v3.7.0（②）：拥挤度/风控「计数 + 具体名单」公共件。
     用户诉求：原来只有「高位 3 个 / 偏高 17 个」这种计数，看不到是谁。
     名单条数以 data-total 标注真实总数（展示上限内截断时仍可核对，B9 判据用）。 */
  var CROWD_LEVEL_CLS = { "高位": "hot", "偏高": "warn", "正常": "ok" };
  var CROWD_LEVEL_MAX = 12;

  function crowdChips(level, items) {
    var list = items || [];
    if (!list.length) { return ""; }
    var shown = list.slice(0, CROWD_LEVEL_MAX);
    return '<div class="bd-line" data-bd-level="' + esc(level) + '" data-total="' + list.length + '">' +
      '<span class="bd-tag ' + (CROWD_LEVEL_CLS[level] || "") + '">' + esc(level) + "</span>" +
      shown.map(function (x) {
        return '<button class="bd-chip" type="button" data-crowd-board="' + esc(x.board) +
          '" title="' + esc(x.board) + " 拥挤度 " + num(x.score, 1) +
          "（点击查看该板块六维明细）" + '">' + esc(x.board) +
          '<i>' + num(x.score, 1) + "</i></button>";
      }).join("") +
      (list.length > shown.length
        ? '<span class="bd-more" title="其余 ' + (list.length - shown.length) + ' 个见板块行情页">+' +
          (list.length - shown.length) + " 个</span>" : "") +
      "</div>";
  }

  /** 拥挤度分档进度条 + 高位/偏高板块名单。 */
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
    var html = rows.map(function (row) {
      // 平方根缩放：保留档位相对关系，避免小值条形不可见；数值本身仍为真实值。
      var width = Math.max(6, Math.sqrt(row.value / max) * 100);
      return '<div class="bar-row"><span class="bar-label">' + row.label + "</span>" +
        '<span class="bar-track"><span class="bar-fill" style="width:' + width.toFixed(1) +
        "%;background:" + row.color + '"></span></span>' +
        '<span class="bar-num">' + row.value + "</span></div>";
    }).join("");
    // 名单：按 level 分组（items 已按分数降序，同名次即分数序）
    var byLevel = { "高位": [], "偏高": [] };
    ((result.data && result.data.items) || []).forEach(function (x) {
      if (byLevel[x.level]) { byLevel[x.level].push(x); }
    });
    var detail = crowdChips("高位", byLevel["高位"]) + crowdChips("偏高", byLevel["偏高"]);
    html += detail ? '<div class="bar-detail">' + detail + "</div>" : "";
    $("bar-list").innerHTML = html;
    tagSource("src-bar", result, API.crowding + " · 明细名单与计数同源");
    return { data: result.data, demo: result.demo };
  }

  /** 风控概览（只展示真实可得字段，不编造指标）+ 风险名单 / 未读提醒明细。 */
  async function renderRisk(crowding) {
    var result = await getJSON(API.alerts, "alerts");
    var counts = (crowding.data && crowding.data.level_counts) || {};
    var high = ((crowding.data && crowding.data.items) || []).filter(function (x) { return x.level === "高位"; });
    var events = (result.data && result.data.events) || [];
    var unreadEvents = events.filter(function (e) { return !e.read; });
    var unread = (result.data && result.data.unread);
    if (typeof unread !== "number") { unread = unreadEvents.length; }
    // 风险状态着色：高位红 / 偏高橙 / 无未读绿，让问题一眼可见。
    var rows = [
      ["高位板块", (counts["高位"] || 0) + " 个", "up"],
      ["偏高板块", (counts["偏高"] || 0) + " 个", "warn"],
      ["未读提醒", unread + " 条", unread ? "down" : ""],
      ["扫描 / 失败", (crowding.data && crowding.data.scanned || 0) + " / " +
        (((crowding.data && crowding.data.failed) || []).length), ""]
    ];
    var html = rows.map(function (row) {
      return '<div class="stat-row"><span>' + row[0] + '</span><b class="' +
        row[2] + '">' + esc(row[1]) + "</b></div>";
    }).join("");
    // 明细一：高位板块名单（风险名单，点击→板块页六维明细）
    if (high.length) {
      html += '<div class="risk-detail" data-risk-list="high">' +
        crowdChips("高位", high) + "</div>";
    }
    // 明细二：未读提醒逐条（规则名 + 标的 + 时间，非仅数字）
    if (unread) {
      var shown = unreadEvents.slice(0, 4);
      html += '<div class="risk-events" data-risk-list="alerts" data-total="' + unread + '">' +
        '<span class="bd-tag warn">未读提醒</span>' +
        shown.map(function (e) {
          return '<div class="re-item" title="' + esc(e.message || "") + '">' +
            '<b>' + esc(e.type_label || e.type || "规则") + "</b>" +
            '<span class="re-target">' + esc(e.target || "—") + "</span>" +
            '<span class="re-time">' + esc(String(e.time || "").slice(5, 16)) + "</span></div>";
        }).join("") +
        (unread > shown.length ? '<div class="bd-more">+' + (unread - shown.length) + " 条（见提醒页）</div>" : "") +
        "</div>";
    }
    $("risk-list").innerHTML = html;
    tagSource("src-risk", result, API.crowding + " + /api/alerts/events · 名单与计数同源");
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
    lastIndexRef = firstIndex || lastIndexRef;
    var code = (lastIndexRef && lastIndexRef.symbol) || "000001";
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
      ["#f5a623", "5日均线", lastMa(5)],
      ["#3fa0c0", "10日均线", lastMa(10)],
      ["#c07ae8", "20日均线", lastMa(20)]
    ].map(function (item) {
      return '<span class="ma-item"><i style="background:' + item[0] + '"></i>' +
        '<span style="color:' + item[0] + '">' + item[1] + ":" + item[2] + "</span></span>";
    }).join("");
    /** EMA 序列（MACD 用）。 */
    var ema = function (n) {
      var k = 2 / (n + 1), out = [], prev = null;
      rows.forEach(function (r) {
        prev = prev == null ? r.close : r.close * k + prev * (1 - k);
        out.push(+prev.toFixed(3));
      });
      return out;
    };
    var series = [
      { name: "K线", type: "candlestick", data: candles,
        itemStyle: { color: "#ff4b5f", color0: "#00d68f", borderColor: "#ff4b5f", borderColor0: "#00d68f" } }
    ];
    if (INDICATOR === "MA") {
      series.push({ name: "5日均线", type: "line", data: ma(5), smooth: true, symbol: "none", lineStyle: { width: 1.5, color: "#f5a623" } });
      series.push({ name: "10日均线", type: "line", data: ma(10), smooth: true, symbol: "none", lineStyle: { width: 1.5, color: "#3fa0c0" } });
      series.push({ name: "20日均线", type: "line", data: ma(20), smooth: true, symbol: "none", lineStyle: { width: 1.5, color: "#c07ae8" } });
      series.push({ name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: volumes, barWidth: "55%" });
    } else if (INDICATOR === "MACD") {
      var e12 = ema(12), e26 = ema(26);
      var dif = e12.map(function (v, i) { return +(v - e26[i]).toFixed(3); });
      var dea = [], prevDea = null;
      dif.forEach(function (v) {
        prevDea = prevDea == null ? v : v * 0.2 + prevDea * 0.8;
        dea.push(+prevDea.toFixed(3));
      });
      var hist = dif.map(function (v, i) { return +(2 * (v - dea[i])).toFixed(3); });
      series.push({ name: "差离值", type: "line", xAxisIndex: 1, yAxisIndex: 1, data: dif, symbol: "none", lineStyle: { width: 1.2, color: "#f5a623" } });
      series.push({ name: "信号线", type: "line", xAxisIndex: 1, yAxisIndex: 1, data: dea, symbol: "none", lineStyle: { width: 1.2, color: "#3fa0c0" } });
      series.push({ name: "柱状图", type: "bar", xAxisIndex: 1, yAxisIndex: 1, barWidth: "55%", data: hist.map(function (v) {
        return { value: v, itemStyle: { color: v >= 0 ? "#ff4b5f" : "#00d68f" } };
      }) });
    } else {
      var kn = [], dn = [], jn = [], kPrev = 50, dPrev = 50;
      rows.forEach(function (r) {
        var win = rows.slice(Math.max(0, rows.indexOf(r) - 8), rows.indexOf(r) + 1);
        var hi = Math.max.apply(null, win.map(function (x) { return x.high; }));
        var lo = Math.min.apply(null, win.map(function (x) { return x.low; }));
        var rsv = hi === lo ? 50 : (r.close - lo) / (hi - lo) * 100;
        kPrev = (2 * kPrev + rsv) / 3;
        dPrev = (2 * dPrev + kPrev) / 3;
        kn.push(+kPrev.toFixed(2)); dn.push(+dPrev.toFixed(2)); jn.push(+(3 * kPrev - 2 * dPrev).toFixed(2));
      });
      series.push({ name: "K值", type: "line", xAxisIndex: 1, yAxisIndex: 1, data: kn, symbol: "none", lineStyle: { width: 1.2, color: "#f5a623" } });
      series.push({ name: "D值", type: "line", xAxisIndex: 1, yAxisIndex: 1, data: dn, symbol: "none", lineStyle: { width: 1.2, color: "#3fa0c0" } });
      series.push({ name: "J值", type: "line", xAxisIndex: 1, yAxisIndex: 1, data: jn, symbol: "none", lineStyle: { width: 1.2, color: "#c07ae8" } });
    }

    var chart = OB.chart.init($("kline-chart"), { renderer: "canvas" });
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
      series: series
    });
    window.addEventListener("resize", function () { chart.resize(); });
    window.__v6Chart = chart;
  }

  /** 供功能层调用：切换 K 线周期或技术指标并重绘。 */
  window.V6_APP = {
    setPeriod: function (periodText) {
      PERIOD = encodeURIComponent(periodText);
      if (lastIndexRef) { renderKline(lastIndexRef); }
    },
    setIndicator: function (name) {
      INDICATOR = name;
      if (lastIndexRef) { renderKline(lastIndexRef); }
    },
    getPeriod: function () { return decodeURIComponent(PERIOD); },
    getIndicator: function () { return INDICATOR; }
  };

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
        // 落盘（v3.6.1 D-3）：跨会话保住用户手动展开/折叠的分组
        OB.store.setJSON(OB.config.KEYS.SB_GROUPS, pinnedGroups);
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

  /** v3.7.0（②）：首页拥挤度/风控名单里的板块 chip → 板块行情页「拥挤度单查」并自动算一次。
      app.js 先于 w7/*.js 加载，故走惰性桥（运行时才读 window.W7）。 */
  function bindBoardChipJump() {
    document.addEventListener("click", function (event) {
      var chip = event.target.closest ? event.target.closest("[data-crowd-board]") : null;
      if (!chip) { return; }
      var board = chip.getAttribute("data-crowd-board");
      if (!board) { return; }
      event.preventDefault();
      if (String(location.hash || "").indexOf("#/sectors") !== 0) { location.hash = "#/sectors"; }
      var tries = 0;
      var timer = setInterval(function () {
        tries += 1;
        if (tries > 40) { clearInterval(timer); return; }   // 6s 内等不到工具卡就放弃（不报错）
        var tab = document.querySelector('[data-sec-tab="拥挤度单查"]');
        var input = document.getElementById("v7-sec-board");
        if (!tab || !input) { return; }
        clearInterval(timer);
        if (!tab.classList.contains("on")) { tab.click(); }   // 切页签会重渲染，故延后再填值
        setTimeout(function () {
          var box = document.getElementById("v7-sec-board");
          if (box) { box.value = board; }
          if (window.W7 && W7.data && W7.data.runCrowd) { W7.data.runCrowd(); }
        }, 80);
      }, 150);
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
        /* BUG-20261119-01：交互型视图（对话/表单/编辑）持有用户输入状态，
           12 秒定时重渲染会执行 host.innerHTML 整体重建，导致智能助手聊天内容
           与输入框每 12 秒被清空（输入到一半一闪消失、回复不到两秒消失）。
           此类视图不参与静默定时重渲染，仅首次进入时渲染一次。 */
        if (id === "home") {
          var payload = await OB.http.getJSON("/api/market/index?market=a").catch(function () { return null; });
          var items = (payload && payload.data && payload.data.items) || [];
          document.querySelectorAll(".index-value").forEach(function (node, i) {
            if (!items[i]) { return; }
            var next = Number(items[i].price).toFixed(2);
            if (node.textContent !== next) {
              node.textContent = next;
              node.classList.remove("live-flash");
              void node.offsetWidth;
              node.classList.add("live-flash");
            }
            /* 2026-11-17 涨红跌绿：原逻辑只更新数字不更新方向 class，
               定时刷新后颜色会停留在上一次的涨跌方向，此处同步修正。 */
            var dirCls = signClass(Number(items[i].pct) || 0);
            if (!node.classList.contains(dirCls)) {
              node.classList.remove("up", "down");
              node.classList.add(dirCls);
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

  /** 当前 hash 视图（kebab-case，对齐 §7.3.1(2)）。
   *
   * BUG-20261112-05：hash 带查询串时（#/watchlist-edit?id=…、#/stock?symbol=…），
   * 原实现拿整串去查 VIEWS 必然落空并回落到 home，导致所有带参入口点开即跳回首页。
   * 这里先剥离 `?query` 再匹配。
   */
  function currentViewId() {
    var raw = (location.hash || "").replace(/^#\/?/, "").trim();
    var id = raw.split("?")[0];
    return Object.prototype.hasOwnProperty.call(VIEWS, id) ? id : "home";
  }

  /** 取当前 hash 的查询参数（如 `#/stock?symbol=600519&market=a`）。
   *
   * @returns {Object} 参数键值表；无查询串返回空对象
   */
  function hashParams() {
    var raw = String(location.hash || "").split("?")[1] || "";
    var out = {};
    raw.split("&").forEach(function (pair) {
      if (!pair) {
        return;
      }
      var index = pair.indexOf("=");
      if (index < 0) {
        return;
      }
      var key = decodeURIComponent(pair.slice(0, index));
      var value = decodeURIComponent(pair.slice(index + 1));
      if (key && value) {
        out[key] = value;
      }
    });
    return out;
  }

  /** 用 hash 参数覆盖视图接口里的同名参数（标的 / 板块 / 代码切换）。
   *
   * BUG-20261112-06：视图 api 的 symbol/code/board 是写死的（个股固定 300750），
   * 带参进入时页面仍拉旧标的，与卡片标题、注入面板显示的标的互相矛盾。
   * 只替换接口里**已存在**的同名键，不给不支持的端点追加参数。
   *
   * @param {Object} api 视图接口表 {key: [path, dataKey]}
   * @returns {Object} 覆盖后的接口表（不改原对象）
   */
  function apiWithHash(api) {
    var params = hashParams();
    var names = Object.keys(params);
    if (!names.length) {
      return api;
    }
    var out = {};
    Object.keys(api).forEach(function (key) {
      var path = api[key][0];
      var next = path;
      names.forEach(function (name) {
        var pattern = new RegExp("([?&]" + name + "=)[^&]*");
        if (pattern.test(next)) {
          next = next.replace(pattern, "$1" + encodeURIComponent(params[name]));
        }
      });
      out[key] = [next, api[key][1]];
    });
    return out;
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
    html += '<div class="side-foot">视图模块同源 · ' + VIEW_IDS.length +
      ' 视图<br>本地真实数据<br><span class="mono" id="side-version">' + esc(APP_VER || "版本读取中…") + "</span></div>";
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
    var chart = OB.chart.init(host, { renderer: "canvas" });
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
    var api = apiWithHash(view.api || {});
    // 覆盖结果挂到视图上，供 render 侧展示「实际请求参数」（如卡片头参数摘要）
    view._api = api;
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
    // v3.7.0：视图渲染完成信号 —— 注入类卡片（板块研判 / 仪表板管理工具栏）必须在此之后挂载，
    // 否则会被这次 host.innerHTML = html 覆盖掉（且静默无异常，表现为「切路由偶发不出现」）。
    try {
      window.dispatchEvent(new CustomEvent("view:rendered", { detail: { id: id, quiet: !!quiet } }));
    } catch (ignored) { /* 老浏览器无 CustomEvent 构造器时忽略 */ }
  }

  /* ---------- 大盘云图（2026-11-18 集成） ----------
     fullbleed 视图：折叠工作台侧栏 + iframe 全幅 + 按视图配置下发 PRD §4.14 命令。
     iframe 在页面加载时即开始加载，onReady 之后下发的指令才有效，故就绪后补发一次。 */
  var cloudReady = false;

  /** 折叠/展开工作台侧栏：云图页进入即折叠，离开恢复。 */
  function setSideCollapsed(on) {
    document.body.classList.toggle("v7-side-collapsed", !!on);
  }

  /** 向云图下发命令；返回是否真正发出（未就绪时为 false，便于调用方判断）。 */
  function postCloud(cmd) {
    if (!cmd || !cloudReady) { return false; }
    var frame = $("cloud-frame");
    if (!frame || !frame.contentWindow) { return false; }
    try {
      frame.contentWindow.postMessage(
        { source: "workbench", type: "command", name: cmd.name, payload: cmd.payload || {} },
        "*"
      );
      return true;
    } catch (ignored) {
      return false;
    }
  }

  /** 对外契约门面：宿主其它模块可编程调用 PRD §4.14 的 7 个方法。 */
  function exposeCloudApi() {
    var call = function (name, payload) {
      return postCloud({ name: name, payload: payload });
    };
    window.WorkbenchCloud = {
      setMarket: function (market) { return call("setMarket", { market: market }); },
      setAreaMetric: function (metric) { return call("setAreaMetric", { metric: metric }); },
      setFilters: function (filters) { return call("setFilters", { filters: filters }); },
      focusGroup: function (name) { return call("focusGroup", { name: name }); },
      resetView: function () { return call("resetView"); },
      exportImage: function () { return call("exportImage"); },
      setTheme: function (theme) { return call("setTheme", { theme: theme }); },
      /** 打开云图页（默认总览）。 */
      open: function (viewId) { location.hash = "#/" + (viewId || "cloud"); },
      /** 最近一次命令回执 / 最近一次点选，供宿主诊断与联动使用。 */
      lastAck: function () { return window.__cloudAck || null; },
      lastPick: function () { return window.__cloudLastPick || null; }
    };
  }

  /** 显示云图全幅页。 */
  function showCloud(id, view) {
    $("home-main").hidden = true;
    $("view-generic").hidden = true;
    $("view-cloud").hidden = false;
    setSideCollapsed(true);
    postCloud(view.cloudCmd);
  }

  /** 接云图上报事件（onReady / onItemClick / onItemDoubleClick / onCommandAck）。 */
  function bindCloudBridge() {
    window.addEventListener("message", function (event) {
      var data = event.data || {};
      if (data.source !== "market-cloud") { return; }
      if (data.name === "onReady") {
        cloudReady = true;
        postCloud((VIEWS[currentViewId()] || {}).cloudCmd);
        return;
      }
      if (data.name === "onCommandAck") {
        // 命令回执：记录最近一次结果，失败时留下可诊断痕迹（不弹窗打扰用户）
        window.__cloudAck = data.payload || null;
        if (data.payload && data.payload.ok === false) {
          console.warn("[market-cloud] 命令未执行：", data.payload);
        }
        return;
      }
      if (data.name === "onItemClick") {
        // 单击仅记录最近一次点选，云图内部检视器自行处理；不改宿主状态
        window.__cloudLastPick = data.payload || null;
        return;
      }
      if (data.name === "onItemDoubleClick") {
        // 双击：按 PRD §4.14 语义打开宿主行情页（带 symbol 参数），离开云图前记录点选
        var pick = data.payload || {};
        window.__cloudLastPick = pick;
        if (pick.code) {
          location.hash = "#/stock?symbol=" + encodeURIComponent(pick.code);
        }
      }
    });
    var collapseBtn = $("v7-collapse");
    if (collapseBtn) {
      collapseBtn.addEventListener("click", function () {
        document.body.classList.toggle("v7-side-collapsed");
      });
    }
  }

  /** 路由：home 显示原首页，fullbleed 显示云图全幅页，其余显示通用视图外壳。 */
  function route() {
    var id = currentViewId();
    var view = VIEWS[id] || VIEWS.home;
    renderSidebar(id);
    setTopActive(view.group);
    if (view.fullbleed) {
      showCloud(id, view);
      return;
    }
    setSideCollapsed(false);
    $("view-cloud").hidden = true;
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
    bindBoardChipJump();
    bindCloudBridge();
    exposeCloudApi();
    startLiveRefresh();
    // 2026-11-12 修复：原先 route() 与 hashchange 监听放在全部首页数据 await 之后，
    // 导致首屏最长 30s 无侧栏/顶栏，且期间的 hash 跳转被丢弃。改为启动即渲染导航。
    route();
    window.addEventListener("hashchange", route);
    OB.http.getJSON("/api/health")
      .then(function (j) {
        var app = (j && j.data && j.data.app) || {};
        if (app.version) { APP_VER = "v" + app.version; renderSidebar(currentViewId()); }
      })
      .catch(function () { /* 版本非关键，静默 */ });
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
