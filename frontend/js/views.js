/* 全站 29 视图配置与页面模板（独立评审稿）。
   视图清单 / group / sidebar / crumb 逐条对齐 frontend/js/app.js 的 NAV_TREE，
   保证顶栏高亮、侧栏选中、面包屑、页面标题四处同源。
   所有数据经 window.V6.fetch() 取自同源 /api 真实接口；不编造行情。 */

(function () {
  "use strict";

  var esc = function (value) {
    return String(value == null ? "—" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  };
  var num = function (v, d) {
    return typeof v === "number" && isFinite(v) ? v.toFixed(d == null ? 2 : d) : "—";
  };
  var cls = function (v) { return v >= 0 ? "up" : "down"; };
  var pct = function (v) { return (v >= 0 ? "+" : "") + num(v, 2) + "%"; };
  var d = function (map, key) { return (map[key] && map[key].data) || {}; };

  /** 通用卡片容器。 */
  function card(title, body, note, extra) {
    return '<div class="card v-card">' +
      '<div class="card-head"><span class="card-title"><span class="badge-ico">' +
      (extra || "·") + "</span>" + esc(title) + "</span>" +
      (note ? '<span class="card-note">' + esc(note) + "</span>" : "") + "</div>" +
      '<div class="v-body">' + body + "</div></div>";
  }

  /** 通用表格：cols = [[key, label, width, cssClass], ...]。 */
  function table(cols, rows, emptyNote) {
    if (!rows || !rows.length) {
      return '<div class="v-empty">' + esc(emptyNote || "该接口当前返回空数据") + "</div>";
    }
    var head = cols.map(function (c) {
      return "<th" + (c[2] ? ' style="width:' + c[2] + '"' : "") +
        (c[3] ? ' class="' + c[3] + '"' : "") + ">" + esc(c[1]) + "</th>";
    }).join("");
    var body = rows.map(function (row, i) {
      return "<tr>" + cols.map(function (c) {
        return "<td" + (c[3] ? ' class="' + c[3] + '"' : "") + ">" + String(c[4] ? c[4](row[c[0]], row, i) : esc(row[c[0]])) + "</td>";
      }).join("") + "</tr>";
    }).join("");
    return '<div class="v-table-wrap"><table class="v-table"><thead><tr>' +
      head + "</tr></thead><tbody>" + body + "</tbody></table></div>";
  }

  /** 指标卡网格。 */
  function kpis(items) {
    return '<div class="v-kpi-grid">' + items.map(function (item) {
      return '<div class="v-kpi"><span class="k-label">' + esc(item[0]) + "</span>" +
        '<span class="k-value ' + (item[2] || "") + '">' + String(item[1]) + "</span></div>";
    }).join("") + "</div>";
  }

  /** 资讯/文本流。 */
  function feed(items, limit) {
    if (!items || !items.length) { return '<div class="v-empty">暂无资讯</div>'; }
    return '<div class="v-feed">' + items.slice(0, limit || 8).map(function (item) {
      return '<div class="v-feed-row"><span class="f-time mono">' + esc(item.time || "—") +
        '</span><span class="f-title">' + esc(item.title || item.text || "—") +
        '</span><span class="f-src">' + esc(item.source || "—") + "</span></div>";
    }).join("") + "</div>";
  }

  var rankCell = function (v, row, i) {
    return '<span class="rank-badge r' + (i + 1) + '">' + (i + 1) + "</span>";
  };
  var pctCell = function (v) {
    return '<span class="' + cls(v) + ' mono">' + pct(v) + "</span>";
  };
  var plainNum = function (v) { return '<span class="mono">' + num(v, 2) + "</span>"; };
  var levelCell = function (v) {
    if (!v) { return "—"; }
    var c = v === "高位" ? "high" : (v === "偏高" ? "mid" : "low");
    return '<span class="level-pill ' + c + '">' + esc(v) + "</span>";
  };

  var V = {};

  V.home = { title: "总览仪表盘", group: "dashboard", sidebar: "overview", template: "home", crumb: ["市场总览"] };

  V.today = {
    title: "今日看点", group: "dashboard", sidebar: "overview", template: "dashboard", crumb: ["市场总览", "今日看点"],
    api: { idx: ["/api/market/index?market=a", "index"], sen: ["/api/sentiment/market", "sentiment"], news: ["/api/news/flash", "newsFlash"] },
    render: function (m) {
      var indices = (d(m, "idx").items || []).map(function (x) {
        return [esc(x.name), '<span class="mono">' + num(x.price) + '</span> <span class="' + cls(x.pct) + '">' + pct(x.pct) + "</span>"];
      });
      var sen = d(m, "sen");
      return card("指数快照", kpis(indices), "/api/market/index", "指") +
        card("市场情绪", kpis([["温度", num(sen.temperature, 1)], ["判定", esc(sen.level)],
          ["正面", sen.positive], ["负面", sen.negative]]), "/api/sentiment/market", "情") +
        card("今日快讯", feed(d(m, "news").items, 8), "/api/news/flash", "讯");
    }
  };

  V.market = {
    title: "市场总览", group: "market", sidebar: "market", template: "dashboard", crumb: ["市场总览"],
    api: { idx: ["/api/market/index?market=a", "index"], sum: ["/api/sector/summary", "summary"], ind: ["/api/sector/industry", "industry"] },
    render: function (m) {
      var indices = (d(m, "idx").items || []).map(function (x) {
        return [esc(x.name), '<span class="mono">' + num(x.price) + '</span> <span class="' + cls(x.pct) + '">' + pct(x.pct) + "</span>"];
      });
      var sum = d(m, "sum");
      var top = (d(m, "ind").items || []).slice().sort(function (a, b) { return b.pct - a.pct; }).slice(0, 8);
      return card("核心指数", kpis(indices), "/api/market/index", "指") +
        card("板块概览", kpis([["板块数", sum.board_count], ["总成交额(亿)", num(sum.total_amount)],
          ["集中度", num(sum.concentration) + "%"], ["领涨", esc((sum.gainers || [])[0] && sum.gainers[0].name)]]),
          "/api/sector/summary", "板") +
        card("强度前列", table([["name", "板块", "30%"], ["pct", "涨跌幅", "20%", "num", pctCell],
          ["amount", "成交额(亿)", "25%", "num", plainNum], ["lead_stock", "领涨股", "25%"]], top),
          "/api/sector/industry", "榜");
    }
  };

  V.dashboard = {
    title: "我的仪表板", group: "dashboard", sidebar: "overview", template: "dashboard", crumb: ["仪表板", "我的仪表板"],
    api: { dash: ["/api/dashboards", "dashboards"], idx: ["/api/market/index?market=a", "index"],
      reg: ["/api/widgets", "widgetsReg"] },
    render: function (m) {
      var list = d(m, "dash").dashboards || [];
      demoInit(d(m, "reg"), d(m, "dash"));
      return card("仪表板清单", table([["name", "名称", "40%"], ["id", "ID", "30%"],
        ["group", "分组", "30%"]], list), "/api/dashboards", "板") +
        card("48 列栅格 · " + esc(demo.target),
          demoGridHTML(), "/api/dashboards · widgets.span + /api/widgets · grid（本地演示，不写后端）", "栅") +
        card("多组件分配规则与逐行占用", demoRuleHTML(), "/api/widgets · grid（.g-3→12 … .g-12→48）", "则") +
        card("指数快照", kpis((d(m, "idx").items || []).map(function (x) {
          return [esc(x.name), '<span class="mono">' + num(x.price) + "</span>"];
        })), "/api/market/index", "指");
    }
  };

  V["dashboard-manage"] = {
    title: "仪表板管理", group: "dashboard", sidebar: "overview", template: "list", crumb: ["仪表板", "仪表板管理"],
    api: { dash: ["/api/dashboards", "dashboards"] },
    render: function (m) {
      return card("仪表板配置", table([["name", "名称", "35%"], ["id", "ID", "30%"],
        ["group", "分组", "20%"], ["widgets", "组件数", "15%", "num", function (v) { return Array.isArray(v) ? v.length : 0; }]],
        d(m, "dash").dashboards, "暂无仪表板配置"), "/api/dashboards", "管");
    }
  };

  V["widget-library"] = {
    title: "组件库", group: "dashboard", sidebar: "overview", template: "dashboard", crumb: ["仪表板", "组件库"],
    api: { dash: ["/api/dashboards", "dashboards"], conn: ["/api/connectors/status", "connectors"] },
    render: function (m) {
      var widgets = ((d(m, "dash").dashboards || [])[0] || {}).widgets || [];
      var sources = d(m, "conn").datasources || [];
      return card("已配置组件", kpis((widgets.length ? widgets : [{ name: "暂无" }]).map(function (w) {
        return [typeof w === "string" ? w : (w.name || w.id || "widget"), "可用"];
      })), "/api/dashboards", "组") +
        card("数据源能力", table([["label", "数据源", "35%"], ["status", "状态", "25%"],
          ["stage", "阶段", "20%"], ["ttl", "缓存(秒)", "20%", "num"]], sources),
          "/api/connectors/status", "源");
    }
  };

  V.watchlist = {
    title: "我的自选", group: "watchlist", sidebar: "my", template: "list", crumb: ["自选股", "我的自选"],
    api: { wl: ["/api/watchlist", "watchlist"] },
    render: function (m) {
      var data = d(m, "wl");
      return card("自选股列表", table([["code", "代码", "25%"], ["name", "名称", "35%"],
        ["price", "现价", "20%", "num", plainNum], ["pct", "涨跌幅", "20%", "num", pctCell]],
        data.items, data.empty_note || "自选股为空（真实空数据）"), "/api/watchlist", "自选");
    }
  };

  V.news = {
    title: "新闻资讯", group: "news", sidebar: "research", template: "list", crumb: ["投研资讯", "新闻资讯"],
    api: { g: ["/api/news/global", "newsGlobal"], f: ["/api/news/flash", "newsFlash"] },
    render: function (m) {
      return card("全球资讯", table([["time", "时间", "18%"], ["title", "标题", "58%"],
        ["source", "来源", "14%"], ["sentiment", "情绪", "10%"]], (d(m, "g").items || []).slice(0, 14)),
        "/api/news/global（81 条取前 14）", "闻") +
        card("实时快讯", feed(d(m, "f").items, 8), "/api/news/flash", "讯");
    }
  };

  V.sectors = {
    title: "板块行情", group: "market", sidebar: "market", template: "list", crumb: ["市场", "板块行情"],
    api: { ind: ["/api/sector/industry", "industry"] },
    render: function (m) {
      return card("行业板块（90）", table([["name", "板块", "22%"], ["code", "代码", "14%"],
        ["pct", "涨跌幅", "14%", "num", pctCell], ["amount", "成交额(亿)", "16%", "num", plainNum],
        ["net_inflow", "净流入(亿)", "16%", "num", plainNum], ["lead_stock", "领涨股", "18%"]],
        d(m, "ind").items), "/api/sector/industry", "板");
    }
  };

  V.stock = {
    title: "个股详情", group: "market", sidebar: "research", template: "detail", crumb: ["市场", "个股详情"],
    api: { p: ["/api/stock/realtime-panel?symbol=300750", "stockPanel"] },
    chartKey: "p",
    render: function (m) {
      var p = d(m, "p");
      var q = p.quote || {};
      return card("关键指标 · " + esc(q.name || "300750"), kpis([
        ["现价", num(q.price)], ["涨跌幅", pct(q.pct || 0), cls(q.pct || 0)],
        ["今开", num(q.open)], ["最高", num(q.high)], ["最低", num(q.low)], ["成交量", num(q.volume, 0)]
      ]), "/api/stock/realtime-panel", "股") +
        '<div class="card v-card"><div class="card-head"><span class="card-title"><span class="badge-ico">K</span>日 K 线</span>' +
        '<span class="card-note">本地 ECharts</span></div><div id="v-chart" class="v-chart"></div></div>' +
        card("个股资讯", feed(p.news, 6), "/api/stock/realtime-panel · news", "讯");
    }
  };

  V.fin = {
    title: "财报摘要", group: "market", sidebar: "research", template: "list", crumb: ["市场", "财报摘要"],
    api: { f: ["/api/stock/financial-summary?symbol=300750", "finSummary"] },
    render: function (m) {
      var data = d(m, "f");
      var cols = [["name", "指标", "34%"]];
      (data.periods || []).slice(0, 6).forEach(function (p, i) {
        cols.push(["v" + i, p, null, "num", function (v, row) { return num(row.values && row.values[i], 2); }]);
      });
      return card("财务指标（近 6 期）", table(cols, data.metrics),
        "/api/stock/financial-summary", "财");
    }
  };

  V.ann = {
    title: "公告库", group: "market", sidebar: "research", template: "list", crumb: ["市场", "公告库"],
    api: { f: ["/api/news/flash", "newsFlash"] },
    render: function (m) {
      return card("资讯流（公告库专用端点本稿未接入，以下为真实快讯流）",
        feed(d(m, "f").items, 14), "/api/news/flash · 口径已标注", "告");
    }
  };

  V.tools = {
    title: "投研工具", group: "market", sidebar: "research", template: "dashboard", crumb: ["市场", "投研工具"],
    api: { c: ["/api/connectors/status", "connectors"], s: ["/api/screener/status", "screener"] },
    render: function (m) {
      var s = d(m, "s");
      return card("工具状态", kpis([["筛选器", s.ready ? "就绪" : "构建中"], ["数据行数", s.row_count],
        ["版本", esc(s.version)], ["构建时间", esc(s.built_at)]]), "/api/screener/status", "工") +
        card("数据源能力", table([["label", "数据源", "34%"], ["status", "状态", "22%"],
          ["stage", "阶段", "22%"], ["fallback", "备源", "22%"]], d(m, "c").datasources),
          "/api/connectors/status", "源");
    }
  };

  V.cand = {
    title: "候选池", group: "market", sidebar: "research", template: "list", crumb: ["市场", "候选池"],
    api: { c: ["/api/sector/cons?board=" + encodeURIComponent("影视院线"), "sectorCons"] },
    render: function (m) {
      return card("板块成分股（影视院线）", table([["code", "代码", "18%"], ["name", "名称", "24%"],
        ["price", "现价", "16%", "num", plainNum], ["pct", "涨跌幅", "16%", "num", pctCell],
        ["amount", "成交额(亿)", "14%", "num", plainNum], ["weight", "权重", "12%", "num", plainNum]],
        d(m, "c").items), "/api/sector/cons", "选");
    }
  };

  V.charting = {
    title: "图表分析", group: "market", sidebar: "research", template: "detail", crumb: ["市场", "图表分析"],
    api: { p: ["/api/stock/realtime-panel?symbol=300750", "stockPanel"] },
    chartKey: "p",
    render: function (m) {
      var q = d(m, "p").quote || {};
      return card("标的", kpis([["名称", esc(q.name || "—")], ["现价", num(q.price)],
        ["涨跌幅", pct(q.pct || 0), cls(q.pct || 0)], ["K线根数", (d(m, "p").kline || []).length]]),
        "/api/stock/realtime-panel", "图") +
        '<div class="card v-card"><div class="card-head"><span class="card-title"><span class="badge-ico">K</span>蜡烛图 + 成交量</span>' +
        '<span class="card-note">本地 ECharts</span></div><div id="v-chart" class="v-chart"></div></div>';
    }
  };

  V["sector-val"] = {
    title: "板块估值", group: "market", sidebar: "market", template: "list", crumb: ["市场", "板块估值"],
    api: { v: ["/api/valuation/percentile", "valuation"] },
    render: function (m) {
      return card("指数估值分位", table([["name", "指数", "26%"], ["pe", "PE", "14%", "num", plainNum],
        ["pct3y", "3年分位", "20%", "num", plainNum], ["pct5y", "5年分位", "20%", "num", plainNum],
        ["pct10y", "10年分位", "20%", "num", plainNum]], d(m, "v").indices),
        "/api/valuation/percentile", "估") +
        card("口径说明", '<div class="v-note">' + esc(d(m, "v").disclaimer || "—") + "</div>",
          "/api/valuation/percentile · disclaimer", "注");
    }
  };

  V.macro = {
    title: "宏观概览", group: "market", sidebar: "market", template: "detail", crumb: ["市场", "宏观概览"],
    api: { m: ["/api/macro/overview", "macro"] },
    render: function (m) {
      var data = d(m, "m");
      var q = data.quadrant || {};
      return card("周期象限", kpis([["阶段", esc(q.stage)], ["增长", esc(q.growth)],
        ["通胀", esc(q.inflation)], ["窗口", q.window + " 期"], ["可用指标", data.available_count + "/" + data.total_count]]),
        "/api/macro/overview", "宏") +
        card("结论", '<div class="v-note">' + esc(q.conclusion || "—") + "</div>",
          "/api/macro/overview · quadrant", "结") +
        card("宏观指标", table([["name", "指标", "40%"], ["value", "最新值", "30%"],
          ["date", "日期", "30%"]], data.indicators), "/api/macro/overview · indicators", "标");
    }
  };

  V.valuation = {
    title: "估值分位", group: "market", sidebar: "market", template: "detail", crumb: ["市场", "估值分位"],
    api: { v: ["/api/valuation/percentile", "valuation"] },
    render: function (m) {
      var data = d(m, "v");
      return card("估值概览", kpis([["10年期国债", num(data.bond_10y) + "%"],
        ["指数数", (data.indices || []).length], ["行业数据", (data.industries || []).length ? "有" : "无"],
        ["市场层", data.market && data.market.available ? "可用" : "不可用"]]),
        "/api/valuation/percentile", "估") +
        card("分位明细", table([["name", "指数", "26%"], ["pe", "PE", "14%", "num", plainNum],
          ["pct3y", "3年", "20%", "num", plainNum], ["pct5y", "5年", "20%", "num", plainNum],
          ["erp", "ERP", "20%", "num", plainNum]], data.indices), "/api/valuation/percentile", "位");
    }
  };

  V.crowding = {
    title: "拥挤度", group: "market", sidebar: "market", template: "list", crumb: ["市场", "拥挤度"],
    api: { c: ["/api/sector/crowding/rankings", "crowding"] },
    render: function (m) {
      var data = d(m, "c");
      return card("拥挤度分档", kpis([["扫描", data.scanned], ["总数", data.total],
        ["高位", (data.level_counts || {})["高位"] || 0], ["偏高", (data.level_counts || {})["偏高"] || 0]]),
        "/api/sector/crowding/rankings", "拥") +
        card("板块排名（50）", table([["board", "板块", "26%"], ["score", "评分", "16%", "num", plainNum],
          ["level", "档位", "18%", "", levelCell], ["kind", "类型", "20%"],
          ["parts", "维度数", "20%", "num", function (v, row) { return (row.parts || []).length; }]],
          data.items), "/api/sector/crowding/rankings", "榜");
    }
  };

  V.screener = {
    title: "智能选股", group: "market", sidebar: "market", template: "list", crumb: ["市场", "智能选股"],
    api: { s: ["/api/screener/status", "screener"], ind: ["/api/sector/industry", "industry"] },
    render: function (m) {
      var s = d(m, "s");
      var rows = (d(m, "ind").items || []).slice().sort(function (a, b) { return b.pct - a.pct; }).slice(0, 16);
      return card("筛选器状态", kpis([["就绪", s.ready ? "是" : "否"], ["行数", s.row_count],
        ["版本", esc(s.version)], ["股息覆盖", num(s.dividend_coverage, 1) + "%"]]),
        "/api/screener/status", "筛") +
        card("强势板块（真实接口派生）", table([["name", "板块", "26%"], ["pct", "涨跌幅", "20%", "num", pctCell],
          ["amount", "成交额(亿)", "20%", "num", plainNum], ["net_inflow", "净流入(亿)", "18%", "num", plainNum],
          ["lead_stock", "领涨股", "16%"]], rows), "/api/sector/industry · 涨幅降序", "选");
    }
  };

  V.lhb = {
    title: "龙虎榜", group: "money", sidebar: "market", template: "list", crumb: ["资金", "龙虎榜"],
    api: { l: ["/api/moneyflow/lhb", "lhb"] },
    render: function (m) {
      return card("龙虎榜 · " + esc(d(m, "l").date || "—"), table([["code", "代码", "14%"],
        ["name", "名称", "16%"], ["reason", "上榜原因", "30%"], ["close", "收盘", "12%", "num", plainNum],
        ["pct", "涨跌幅", "14%", "num", pctCell], ["net_buy", "净买入(亿)", "14%", "num", plainNum]],
        d(m, "l").items), "/api/moneyflow/lhb", "榜");
    }
  };

  V["fund-combo"] = {
    title: "基金组合", group: "portfolio", sidebar: "market", template: "detail", crumb: ["组合", "基金组合"],
    api: { f: ["/api/fund/performance?code=017811", "fundPerf"] },
    render: function (m) {
      var f = d(m, "f");
      return card("基金业绩 · " + esc(f.name || "017811"), kpis([["单位净值", num(f.unit_nav, 4)],
        ["累计净值", num(f.unit_acc_nav, 4)], ["日涨跌", pct(f.day_growth || 0), cls(f.day_growth || 0)],
        ["定投1年", num(f.aip && f.aip["1y"], 2) + "%"]]), "/api/fund/performance", "基") +
        card("区间业绩", table([["period", "区间", "34%"], ["growth", "涨幅", "33%", "num", plainNum],
          ["rank", "排名", "33%", "num"]], f.periods), "/api/fund/performance · periods", "区");
    }
  };

  V.portfolio = {
    title: "我的持仓", group: "portfolio", sidebar: "my", template: "detail", crumb: ["组合", "我的持仓"],
    api: { p: ["/api/portfolio", "portfolio"], a: ["/api/portfolio/analysis", "portfolioAnalysis"] },
    render: function (m) {
      var a = d(m, "a");
      return card("持仓概览", kpis([["持仓数", (d(m, "p").items || []).length],
        ["人民币市值", num(a.totals && a.totals.cny_market_value)],
        ["人民币成本", num(a.totals && a.totals.cny_cost)], ["USD 汇率", num(a.fx && a.fx.USD, 4)]]),
        "/api/portfolio/analysis", "仓") +
        card("市场分布", table([["label", "市场", "40%"], ["value_cny", "市值(元)", "30%", "num", plainNum],
          ["ratio", "占比", "30%", "num", plainNum]], a.distribution), "/api/portfolio/analysis · distribution", "布") +
        card("持仓明细", table([["code", "代码", "30%"], ["name", "名称", "40%"],
          ["market_value", "市值", "30%", "num", plainNum]], d(m, "p").items, "持仓为空（真实空数据）"),
          "/api/portfolio", "细");
    }
  };

  V.fund = {
    title: "基金详情", group: "portfolio", sidebar: "market", template: "detail", crumb: ["组合", "基金详情"],
    api: { s: ["/api/fund/search?q=" + encodeURIComponent("易方达"), "fundSearch"],
      f: ["/api/fund/performance?code=017811", "fundPerf"] },
    render: function (m) {
      var f = d(m, "f");
      return card("基金业绩 · " + esc(f.name || "—"), kpis([["单位净值", num(f.unit_nav, 4)],
        ["累计净值", num(f.unit_acc_nav, 4)], ["日涨跌", pct(f.day_growth || 0), cls(f.day_growth || 0)],
        ["净值日期", esc(f.end_date)]]), "/api/fund/performance", "基") +
        card("基金检索（易方达）", table([["code", "代码", "24%"], ["name", "名称", "48%"],
          ["type", "类型", "28%"]], (d(m, "s").items || []).slice(0, 14)), "/api/fund/search", "索");
    }
  };

  V.yjb = {
    title: "养基宝持仓", group: "yjb", sidebar: "my", template: "list", crumb: ["养基宝", "养基宝持仓"],
    api: { y: ["/api/fund/yjb/holdings", "yjbHoldings"] },
    render: function (m) {
      var y = d(m, "y");
      return card("账户分布", kpis([["持仓数", y.total], ["账户数", (y.accounts || []).length],
        ["估值", y.estimates ? "已开启" : "未开启"], ["数据日期", esc(y.as_of)]]),
        "/api/fund/yjb/holdings", "养") +
        card("持仓明细（15 行）", table([["code", "代码", "14%"], ["name", "名称", "30%"],
          ["account_title", "账户", "12%"], ["share", "份额", "14%", "num", plainNum],
          ["money", "市值", "15%", "num", plainNum], ["pct", "涨跌", "15%", "num", pctCell]],
          y.items), "/api/fund/yjb/holdings", "持");
    }
  };

  V.mgr = {
    title: "经理画像", group: "portfolio", sidebar: "my", template: "detail", crumb: ["组合", "经理画像"],
    api: { f: ["/api/fund/performance?code=017811", "fundPerf"] },
    render: function (m) {
      var f = d(m, "f");
      return card("业绩代理（经理画像专用端点本稿未接入，以下为真实基金业绩）",
        kpis([["基金", esc(f.name || "—")], ["单位净值", num(f.unit_nav, 4)],
          ["日涨跌", pct(f.day_growth || 0), cls(f.day_growth || 0)], ["净值日期", esc(f.end_date)]]),
        "/api/fund/performance · 口径已标注", "理") +
        card("年度业绩", table([["period", "区间", "34%"], ["nav", "净值", "33%", "num", plainNum],
          ["rank", "排名", "33%", "num"]], f.annual), "/api/fund/performance · annual", "年");
    }
  };

  V.alerts = {
    title: "提醒中心", group: "system", sidebar: "sys", template: "list", crumb: ["提醒中心"],
    api: { a: ["/api/alerts/events", "alerts"] },
    render: function (m) {
      var a = d(m, "a");
      return card("提醒概览", kpis([["事件数", (a.events || []).length], ["未读", a.unread],
        ["数据时间", esc(a.as_of)]]), "/api/alerts/events", "警") +
        card("事件列表", table([["type", "类型", "30%"], ["title", "内容", "50%"],
          ["created_at", "时间", "20%"]], a.events, "当前无提醒事件（真实空数据）"),
          "/api/alerts/events", "事");
    }
  };

  V.connectors = {
    title: "连接器", group: "system", sidebar: "sys", template: "form", crumb: ["系统", "连接器"],
    api: { c: ["/api/connectors/status", "connectors"] },
    render: function (m) {
      var c = d(m, "c");
      return card("数据源状态（只读）", table([["label", "数据源", "30%"],
        ["enabled", "启用", "14%", "", function (v) { return v ? '<span class="up">是</span>' : '<span class="dim">否</span>'; }],
        ["active", "活动", "14%"], ["status", "状态", "20%"], ["detail", "说明", "22%"]],
        c.datasources), "/api/connectors/status", "连") +
        card("能力缺口", table([["market_label", "市场", "24%"], ["feature_label", "能力", "30%"],
          ["reason", "原因", "30%"], ["decision", "处置", "16%"]], c.gaps),
          "/api/connectors/status · gaps", "缺") +
        card("设置项", '<div class="v-form"><label>主题皮肤</label><div class="v-form-value">V6 / 现行 / 砚</div>' +
          "<label>数据刷新</label><div class=\"v-form-value\">按各端点 TTL</div>" +
          "<label>操作模式</label><div class=\"v-form-value\">只读评审，不写配置</div></div>",
          "视觉稿只读", "设");
    }
  };

  V.settings = {
    title: "系统设置", group: "system", sidebar: "sys", template: "form", crumb: ["系统", "系统设置"],
    api: { a: ["/api/ai/config", "aiConfig"], p: ["/api/system/desktop-prefs", "desktopPrefs"] },
    render: function (m) {
      var a = d(m, "a");
      var p = d(m, "p");
      return card("AI 配置（只读）", '<div class="v-form"><label>已配置</label><div class="v-form-value">' +
        (a.configured ? "是" : "否") + "</div><label>模型</label><div class=\"v-form-value\">" + esc(a.model || "未配置") +
        "</div><label>Base URL</label><div class=\"v-form-value\">" + esc(a.base_url || "未配置") + "</div></div>",
        "/api/ai/config", "AI") +
        card("桌面偏好（只读）", '<div class="v-form"><label>关闭动作</label><div class="v-form-value">' +
          esc(p.close_action) + "</div><label>可选项</label><div class=\"v-form-value\">" +
          esc((p.options || []).join(" / ")) + "</div></div>", "/api/system/desktop-prefs", "桌") +
        card("皮肤说明", '<div class="v-note">V6 为新增第三套皮肤；原深色基准与砚浅色保持不变。' +
          "本视觉稿不写入任何设置。</div>", "视觉稿说明", "肤");
    }
  };

  /* ---------- 通用走势图：全部由真实接口数据派生，纯 SVG / CSS ---------- */
  function barSVG(series) {
    var w = 420, h = 132, pad = 22;
    var rows = series.slice(0, 14);
    var max = Math.max.apply(null, rows.map(function (x) { return Math.abs(Number(x.value)) || 0; })) || 1;
    var bw = (w - 2 * pad) / (rows.length || 1);
    var bars = rows.map(function (item, i) {
      var v = Number(item.value) || 0;
      var bh = Math.max(2, (Math.abs(v) / max) * (h - 48));
      var y = v >= 0 ? (h - 26 - bh) : (h - 26);
      var color = v >= 0 ? "#ff4b5f" : "#00d68f";
      var x = pad + i * bw + bw * 0.18;
      return '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + (bw * 0.64).toFixed(1) +
        '" height="' + bh.toFixed(1) + '" rx="2" fill="' + color + '" opacity=".9"><title>' +
        esc(item.label) + ": " + num(v, 2) + "</title></rect>" +
        '<text x="' + (x + bw * 0.32).toFixed(1) + '" y="' + (h - 8) +
        '" text-anchor="middle" font-size="8" fill="#96a6b6">' +
        esc(String(item.label).slice(0, 3)) + "</text>";
    }).join("");
    return '<svg class="v-chart-svg" viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="none">' +
      '<line x1="' + pad + '" y1="' + (h - 26) + '" x2="' + (w - pad) + '" y2="' + (h - 26) + '" stroke="#1e364c"/>' +
      '<line x1="' + pad + '" y1="' + ((h - 26) / 2).toFixed(1) + '" x2="' + (w - pad) + '" y2="' +
      ((h - 26) / 2).toFixed(1) + '" stroke="#1c3247" stroke-dasharray="3 3" stroke-width=".6"/>' + bars + "</svg>";
  }

  function lineSVG(series) {
    var w = 420, h = 132, pad = 22;
    var rows = series.slice(-40);
    var vals = rows.map(function (x) { return Number(x.value) || 0; });
    if (!vals.length) { return ""; }
    var min = Math.min.apply(null, vals);
    var max = Math.max.apply(null, vals);
    var span = (max - min) || 1;
    var pts = vals.map(function (v, i) {
      return [pad + (i * (w - 2 * pad)) / (vals.length - 1 || 1), h - 26 - ((v - min) / span) * (h - 52)];
    });
    var line = pts.map(function (p) { return p[0].toFixed(1) + "," + p[1].toFixed(1); }).join(" L");
    var area = "M" + pts[0][0].toFixed(1) + "," + (h - 26) + " L" + line +
      " L" + pts[pts.length - 1][0].toFixed(1) + "," + (h - 26) + " Z";
    return '<svg class="v-chart-svg" viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="none">' +
      "<defs><linearGradient id=\"vlineG\" x1=\"0\" y1=\"0\" x2=\"0\" y2=\"1\">" +
      "<stop offset=\"0%\" stop-color=\"#ff4b5f\" stop-opacity=\".5\"/>" +
      "<stop offset=\"100%\" stop-color=\"#ff4b5f\" stop-opacity=\"0\"/></linearGradient></defs>" +
      '<line x1="' + pad + '" y1="' + (h - 26) + '" x2="' + (w - pad) + '" y2="' + (h - 26) + '" stroke="#1e364c"/>' +
      '<path d="' + area + '" fill="url(#vlineG)"/>' +
      '<path d="M' + line + '" fill="none" stroke="#ff4b5f" stroke-width="2"/>' +
      '<text x="' + pad + '" y="12" font-size="9" fill="#96a6b6">' + esc(rows[0].label) + " → " +
      esc(rows[rows.length - 1].label) + "</text></svg>";
  }

  function donutSVG(series) {
    var colors = ["#3a72a8", "#00d68f", "#ffab3d", "#ff5b6b", "#b06bff", "#3fa0c0"];
    var rows = series.slice(0, 6);
    var total = rows.reduce(function (s, x) { return s + Math.abs(Number(x.value) || 0); }, 0) || 1;
    var r = 42, cx = 70, cy = 66, circ = 2 * Math.PI * r, acc = 0;
    var arcs = rows.map(function (x, i) {
      var frac = Math.abs(Number(x.value) || 0) / total;
      var el = '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="none" stroke="' +
        colors[i] + '" stroke-width="16" stroke-dasharray="' + (frac * circ).toFixed(2) + " " +
        circ.toFixed(2) + '" stroke-dashoffset="' + (-acc * circ).toFixed(2) +
        '" transform="rotate(-90 ' + cx + " " + cy + ')"/>';
      acc += frac;
      return el;
    }).join("");
    var legend = rows.map(function (x, i) {
      return '<div class="v-legend-row"><i style="background:' + colors[i] + '"></i><span>' +
        esc(x.label) + "</span><b>" + num(x.value, 2) + "</b></div>";
    }).join("");
    return '<div class="v-donut"><svg viewBox="0 0 140 132">' +
      '<circle cx="70" cy="66" r="42" fill="none" stroke="#1a2836" stroke-width="16"/>' + arcs +
      '<text x="70" y="71" text-anchor="middle" font-size="13" fill="#edf1f5" font-family="monospace">' +
      rows.length + " 类</text></svg><div class=\"v-legend\">" + legend + "</div></div>";
  }

  function heatHTML(series) {
    return '<div class="v-heat">' + series.slice(0, 48).map(function (x) {
      var v = Number(x.value) || 0;
      var alpha = Math.min(0.85, 0.18 + Math.abs(v) / 12);
      var color = v >= 0 ? "rgba(255,75,95," + alpha.toFixed(2) + ")" : "rgba(0,214,143," + alpha.toFixed(2) + ")";
      return '<div class="v-heat-cell" style="background:' + color + '" title="' +
        esc(x.label) + " " + num(v, 2) + '%"><span>' + esc(String(x.label).slice(0, 4)) +
        "</span><b>" + num(v, 1) + "</b></div>";
    }).join("") + "</div>";
  }

  /** 真实空数据的图表骨架：保留图形位，但明确标注不编造数据。 */
  function emptyChartSVG() {
    return '<svg class="v-chart-svg" viewBox="0 0 420 132" preserveAspectRatio="none">' +
      '<line x1="22" y1="106" x2="398" y2="106" stroke="#1e364c"/>' +
      '<line x1="22" y1="53" x2="398" y2="53" stroke="#1c3247" stroke-dasharray="3 3" stroke-width=".6"/>' +
      '<polyline points="22,90 90,84 158,88 226,70 294,76 362,58 398,64" fill="none" ' +
      'stroke="#1e364c" stroke-width="1.5" opacity=".45"/></svg>' +
      '<div class="v-empty">该接口当前无可用走势数据（真实空数据）</div>';
  }

  /** 生成一张走势图卡片；无数据时保留图表位并显示真实空态。 */
  function chartCard(spec, series) {
    if (!series || !series.length) {
      return card(spec.title || "走势", emptyChartSVG(), spec.src || "真实接口", "图");
    }
    var body = spec.type === "line" ? lineSVG(series)
      : spec.type === "donut" ? donutSVG(series)
        : spec.type === "heat" ? heatHTML(series) : barSVG(series);
    return card(spec.title + " · " + series.length + " 项", body, spec.src || "真实接口派生", "图");
  }

  /* ---------- 组件库：/api/widgets 真实注册表（49 个组件） ---------- */
  /* ---------- 48 列仪表板演示（本地内存，绝不写后端 dashboards.json） ---------- */
  var SPAN_LABEL = { 12: "1/4", 16: "1/3", 20: "5/12", 24: "1/2", 28: "7/12", 32: "2/3", 48: "整行" };
  var demo = { widgets: [], registry: {}, target: "我的仪表板", loaded: false };
  window.V6_DEMO = demo;

  function demoInit(registryData, dashData) {
    (registryData.items || []).forEach(function (w) { demo.registry[w.id] = w; });
    if (demo.loaded) { return; }
    var dash = (dashData.dashboards || [])[0];
    if (dash) {
      demo.target = dash.name;
      (dash.widgets || []).forEach(function (x) {
        var meta = demo.registry[x.id] || {};
        demo.widgets.push({
          id: x.id, span: x.span || meta.span || 24,
          title: meta.title || x.id, category: meta.category || "table"
        });
      });
    }
    demo.loaded = true;
  }

  function demoRows() {
    var rows = [], cur = [], acc = 0;
    demo.widgets.forEach(function (w) {
      if (acc + w.span > 48) { rows.push({ items: cur, used: acc }); cur = []; acc = 0; }
      cur.push(w);
      acc += w.span;
    });
    if (cur.length) { rows.push({ items: cur, used: acc }); }
    return rows;
  }

  function demoGridHTML() {
    if (!demo.widgets.length) {
      return '<div class="demo-dash-grid"><div class="v-empty">演示仪表板为空，从组件库点「添加」</div></div>';
    }
    return '<div class="demo-dash-grid">' + demo.widgets.map(function (w) {
      return '<div class="demo-widget cat-' + esc(w.category) + '" style="grid-column: span ' + w.span + '">' +
        '<div class="dw-head"><b>' + esc(w.id) + "</b><span>" + esc(w.title) + "</span></div>" +
        '<div class="dw-meta">span ' + w.span + " · " + (SPAN_LABEL[w.span] || "—") + " / 48 列</div>" +
        '<button class="dw-remove" data-remove="' + esc(w.id) + '">移除</button></div>';
    }).join("") + "</div>";
  }

  function demoSummaryHTML() {
    return demoRows().map(function (r, i) {
      return '<div class="demo-row"><span class="mono">第 ' + (i + 1) + " 行</span>" +
        "<span>" + r.items.map(function (w) { return esc(w.id) + "(" + w.span + ")"; }).join(" + ") + "</span>" +
        '<b class="' + (r.used === 48 ? "up" : "warn-text") + '">' + r.used + " / 48 列</b></div>";
    }).join("");
  }

  function demoRuleHTML() {
    return '<div class="v-note">48 列 CSS Grid：组件按数组顺序依次放入，累计 span 超过 48 自动换行。' +
      '合法宽度 <span class="mono">12 / 16 / 20 / 24 / 28 / 32 / 48</span>' +
      '（1/4、1/3、5/12、1/2、7/12、2/3、整行）；默认宽度来自 /api/widgets 的 grid 字段。</div>' +
      '<div class="demo-rows demo-row-summary">' + demoSummaryHTML() + "</div>";
  }

  function refreshDemo() {
    document.querySelectorAll(".demo-dash-grid").forEach(function (node) {
      node.outerHTML = demoGridHTML();
    });
    document.querySelectorAll(".demo-row-summary").forEach(function (node) {
      node.innerHTML = demoSummaryHTML();
    });
    document.querySelectorAll(".widget-add").forEach(function (btn) {
      var added = demo.widgets.some(function (w) { return w.id === btn.getAttribute("data-id"); });
      btn.textContent = added ? "✓ 已添加（点击移除）" : "＋ 添加到演示仪表板";
      btn.classList.toggle("added", added);
    });
    document.querySelectorAll(".demo-count").forEach(function (node) {
      node.textContent = demo.widgets.length;
    });
  }

  window.V6_DEMO_API = {
    init: demoInit,
    add: function (id) {
      var meta = demo.registry[id];
      if (!meta) { return "unknown"; }
      if (demo.widgets.some(function (w) { return w.id === id; })) { return "duplicate"; }
      demo.widgets.push({ id: id, span: meta.span || 24, title: meta.title, category: meta.category });
      refreshDemo();
      return "added";
    },
    remove: function (id) {
      demo.widgets = demo.widgets.filter(function (w) { return w.id !== id; });
      refreshDemo();
      return "removed";
    },
    grid: demoGridHTML,
    rule: demoRuleHTML,
    rows: demoRows
  };

  function widgetThumb(category) {
    var maps = {
      chart: '<polyline points="6,30 16,22 26,26 36,12 46,18" fill="none" stroke="#3a72a8" stroke-width="2"/><rect x="6" y="35" width="40" height="3" fill="#1c3247"/>',
      table: '<rect x="6" y="10" width="40" height="5" fill="#1e364c"/><rect x="6" y="18" width="40" height="3" fill="#1c3247"/><rect x="6" y="24" width="40" height="3" fill="#1c3247"/><rect x="6" y="30" width="40" height="3" fill="#1c3247"/>',
      market: '<text x="7" y="28" font-size="15" fill="#ff4b5f" font-family="monospace">3,078</text><polyline points="6,36 20,32 34,34 46,26" fill="none" stroke="#ff4b5f" stroke-width="1.5"/>',
      news: '<rect x="6" y="10" width="34" height="3" fill="#1e364c"/><rect x="6" y="17" width="40" height="3" fill="#1c3247"/><rect x="6" y="24" width="30" height="3" fill="#1c3247"/><rect x="6" y="31" width="38" height="3" fill="#1c3247"/>',
      fin: '<rect x="8" y="24" width="6" height="14" fill="#3a72a8"/><rect x="18" y="16" width="6" height="22" fill="#00d68f"/><rect x="28" y="20" width="6" height="18" fill="#ffab3d"/><rect x="38" y="10" width="6" height="28" fill="#ff5b6b"/>',
      filter: '<rect x="6" y="14" width="40" height="4" rx="2" fill="#1c3247"/><circle cx="20" cy="16" r="5" fill="#3a72a8"/><rect x="6" y="26" width="40" height="4" rx="2" fill="#1c3247"/><circle cx="34" cy="28" r="5" fill="#00d68f"/>'
    };
    return '<svg class="widget-thumb-svg" viewBox="0 0 52 44">' + (maps[category] || maps.table) + "</svg>";
  }

  function widgetGrid(list) {
    return '<div class="widget-grid">' + list.map(function (w) {
      var apis = (w.api || []).join(", ") || "无专用端点";
      var added = demo.widgets.some(function (x) { return x.id === w.id; });
      return '<div class="widget-card" data-page="' + esc(w.page || "") + '">' +
        '<div class="widget-thumb">' + widgetThumb(w.category) + "</div>" +
        '<div class="widget-meta"><b>' + esc(w.id) + "</b> " + esc(w.title) + "</div>" +
        '<div class="widget-desc">' + esc(w.desc || "—") + "</div>" +
        '<div class="widget-foot">' + esc(w.page || "—") + " · span " + esc(w.span) + " · " +
        esc(apis.slice(0, 42)) + "</div>" +
        '<button class="widget-add' + (added ? " added" : "") + '" data-id="' + esc(w.id) + '">' +
        (added ? "✓ 已添加（点击移除）" : "＋ 添加到演示仪表板") + "</button></div>";
    }).join("") + "</div>";
  }

  V["widget-library"] = {
    title: "组件库", group: "dashboard", sidebar: "overview", template: "dashboard",
    crumb: ["仪表板", "组件库"],
    api: { reg: ["/api/widgets", "widgetsReg"], conn: ["/api/connectors/status", "connectors"],
      dash: ["/api/dashboards", "dashboards"] },
    render: function (m) {
      var reg = d(m, "reg");
      demoInit(reg, d(m, "dash"));
      var items = reg.items || [];
      var cats = reg.categories || {};
      var keys = Object.keys(cats);
      var count = function (key) {
        return items.filter(function (w) { return w.category === key; }).length;
      };
      var summary = kpis([
        ["组件总数", reg.total || items.length], ["组件分类", keys.length],
        ["图表走势", count("chart")], ["行情报价", count("market")],
        ["列表表格", count("table")], ["数据源", (d(m, "conn").datasources || []).length],
        ["已添加", '<span class="demo-count">' + demo.widgets.length + "</span>"]
      ]);
      var groups = keys.map(function (key) {
        return card(cats[key] + " · " + count(key), widgetGrid(items.filter(function (w) {
          return w.category === key;
        })), "/api/widgets · " + key, "组");
      }).join("");
      return card("组件注册表（真实 /api/widgets）", summary, "/api/widgets", "库") +
        groups +
        card("添加到：" + esc(demo.target) + " · 48 列栅格",
          demoGridHTML(), "本地演示状态，不调用 PUT /api/dashboards、不写入 data/dashboards.json", "板") +
        card("多组件分配规则与逐行占用", demoRuleHTML(), "/api/widgets · grid / /api/dashboards · widgets.span", "则") +
        card("数据源能力", table([["label", "数据源", "35%"], ["status", "状态", "22%"],
          ["stage", "阶段", "21%"], ["ttl", "缓存(秒)", "22%", "num"]],
          d(m, "conn").datasources), "/api/connectors/status", "源");
    },
    chart: {
      type: "donut", key: "reg", title: "组件分类分布", src: "/api/widgets · categories",
      pick: function (x) {
        var cats = x.categories || {}, items = x.items || [];
        return Object.keys(cats).map(function (k) {
          return { label: cats[k], value: items.filter(function (w) { return w.category === k; }).length };
        });
      }
    }
  };

  /* ---------- 各视图走势图配置：统一从已有真实接口数据派生，不新增端点 ---------- */
  var topN = function (key, field, n, label) {
    return function (x) {
      return (x[key] || []).slice(0, n || 12).map(function (i) {
        return { label: i[label || "name"], value: i[field] };
      });
    };
  };
  var CHART_SPECS = {
    today: { type: "bar", key: "idx", title: "核心指数涨跌幅", src: "/api/market/index", pick: topN("items", "pct", 8, "name") },
    market: { type: "heat", key: "ind", title: "板块热力", src: "/api/sector/industry（90 板块取前 48）", pick: topN("items", "pct", 48, "name") },
    dashboard: { type: "bar", key: "idx", title: "指数涨跌幅", src: "/api/market/index", pick: topN("items", "pct", 8, "name") },
    "dashboard-manage": { type: "bar", key: "dash", title: "仪表板组件数", src: "/api/dashboards", pick: function (x) { return (x.dashboards || []).map(function (i) { return { label: i.name, value: (i.widgets || []).length }; }); } },
    watchlist: { type: "bar", key: "wl", title: "自选股涨跌", src: "/api/watchlist", pick: topN("items", "pct", 12, "name") },
    news: { type: "bar", key: "g", title: "资讯时段分布", src: "/api/news/global（按小时聚合）", pick: function (x) {
      var buckets = {};
      (x.items || []).forEach(function (i) { var h = String(i.time || "").slice(11, 13) || "--"; buckets[h] = (buckets[h] || 0) + 1; });
      return Object.keys(buckets).sort().map(function (k) { return { label: k + "时", value: buckets[k] }; });
    } },
    sectors: { type: "bar", key: "ind", title: "板块涨幅 Top12", src: "/api/sector/industry", pick: topN("items", "pct", 12, "name") },
    stock: { type: "line", key: "p", title: "分时走势", src: "/api/stock/realtime-panel · minute", pick: function (x) {
      return (x.minute || []).filter(function (_, i) { return i % 6 === 0; }).map(function (i) { return { label: i.time, value: i.price }; });
    } },
    fin: { type: "line", key: "f", title: "财务指标趋势", src: "/api/stock/financial-summary", pick: function (x) {
      var first = (x.metrics || [])[0];
      if (!first) { return []; }
      return (x.periods || []).map(function (p, i) { return { label: p, value: (first.values || [])[i] }; });
    } },
    ann: { type: "bar", key: "f", title: "快讯时段分布", src: "/api/news/flash（按小时聚合）", pick: function (x) {
      var buckets = {};
      (x.items || []).forEach(function (i) { var h = String(i.time || "").slice(11, 13) || "--"; buckets[h] = (buckets[h] || 0) + 1; });
      return Object.keys(buckets).sort().map(function (k) { return { label: k + "时", value: buckets[k] }; });
    } },
    tools: { type: "bar", key: "c", title: "数据源缓存 TTL", src: "/api/connectors/status", pick: topN("datasources", "ttl", 8, "label") },
    cand: { type: "bar", key: "c", title: "成分股涨跌幅", src: "/api/sector/cons", pick: topN("items", "pct", 12, "name") },
    charting: { type: "line", key: "p", title: "分时走势", src: "/api/stock/realtime-panel · minute", pick: function (x) {
      return (x.minute || []).filter(function (_, i) { return i % 6 === 0; }).map(function (i) { return { label: i.time, value: i.price }; });
    } },
    "sector-val": { type: "bar", key: "v", title: "指数 5 年估值分位", src: "/api/valuation/percentile", pick: topN("indices", "pct5y", 8, "name") },
    macro: { type: "bar", key: "m", title: "宏观指标最新值", src: "/api/macro/overview · indicators", pick: topN("indicators", "value", 9, "name") },
    valuation: { type: "bar", key: "v", title: "指数 PE", src: "/api/valuation/percentile", pick: topN("indices", "pe", 8, "name") },
    crowding: { type: "bar", key: "c", title: "拥挤度评分 Top12", src: "/api/sector/crowding/rankings", pick: function (x) {
      return (x.items || []).slice(0, 12).map(function (i) { return { label: i.board, value: i.score }; });
    } },
    screener: { type: "bar", key: "ind", title: "强势板块涨幅", src: "/api/sector/industry", pick: topN("items", "pct", 12, "name") },
    lhb: { type: "bar", key: "l", title: "龙虎榜净买入 Top12", src: "/api/moneyflow/lhb", pick: topN("items", "net_buy", 12, "name") },
    "fund-combo": { type: "bar", key: "f", title: "区间涨幅", src: "/api/fund/performance · periods", pick: topN("periods", "growth", 8, "period") },
    portfolio: { type: "donut", key: "a", title: "市场分布", src: "/api/portfolio/analysis · distribution", pick: function (x) {
      return (x.distribution || []).map(function (i) { return { label: i.label, value: i.value_cny }; });
    } },
    fund: { type: "bar", key: "f", title: "区间涨幅", src: "/api/fund/performance · periods", pick: topN("periods", "growth", 8, "period") },
    yjb: { type: "bar", key: "y", title: "持仓市值分布", src: "/api/fund/yjb/holdings", pick: topN("items", "money", 12, "name") },
    mgr: { type: "line", key: "f", title: "年度净值", src: "/api/fund/performance · annual", pick: topN("annual", "nav", 8, "period") },
    alerts: { type: "bar", key: "a", title: "提醒事件", src: "/api/alerts/events", pick: function (x) {
      return (x.events || []).map(function (i) { return { label: i.type || "event", value: 1 }; });
    } },
    connectors: { type: "bar", key: "c", title: "数据源缓存 TTL", src: "/api/connectors/status", pick: topN("datasources", "ttl", 8, "label") },
    settings: { type: "donut", key: "p", title: "偏好选项", src: "/api/system/desktop-prefs", pick: function (x) {
      return (x.options || []).map(function (o) { return { label: o, value: 1 }; });
    } }
  };
  Object.keys(CHART_SPECS).forEach(function (id) {
    if (V[id]) { V[id].chart = CHART_SPECS[id]; }
  });

  window.V6_VIEWS = V;
  window.V6_HELPERS = { esc: esc, num: num, cls: cls, pct: pct, card: card, table: table, kpis: kpis, feed: feed, chartCard: chartCard };
})();
