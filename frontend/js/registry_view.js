/* 功能还原层：按正式环境 /api/widgets 注册表渲染真实功能区块。
   只做「视觉重构」——功能集合、名称、端点 100% 来自正式环境，不新增也不删减。
   加载顺序：registry_data.js → views.js → registry_view.js → app.js
*/

(function () {
  "use strict";

  var REG = window.V6_REGISTRY || { widgets: [] };
  var V = window.V6_VIEWS || {};
  var H = window.V6_HELPERS || {};
  var esc = H.esc, num = H.num, card = H.card, table = H.table, kpis = H.kpis, feed = H.feed;

  var CAT_ICON = { filter: "⚙", market: "◈", chart: "▤", table: "▦", news: "▣", fin: "▥" };

  // 组件专属数据键：正式端点返回的顶层 / 嵌套数组，按组件语义取对应真实序列。
  // 只改「如何呈现真实数据」，不改端点、不新增功能（对应「功能与正式环境一致」要求）。
  var ROW_HINTS = {
    "ST-02": "kline",      // K 线图 → 近 160 日 OHLC
    "ST-03": "minute",     // 分时图 → 267 条分时价量
    "ST-04": "tiers",      // 资金分布 → fund_dist.tiers 四档
    "ST-05": "news",       // 相关新闻 → 10 条真实快讯
    "ST-06": "ratings"     // 机构预测 → forecast 评级（dict，回退 KPI）
  };

  // 部分注册表端点需要默认参数才能取到真实数据（与正式环境默认值一致）
  var PARAM_HINTS = {
    "/api/stock/realtime-panel": "?symbol=300750",
    "/api/stock/profile": "?symbol=300750",
    "/api/stock/financial-summary": "?symbol=300750",
    "/api/stock/financial-trend": "?symbol=300750",
    "/api/stock/holders": "?symbol=300750",
    "/api/sector/cons": "?board=" + encodeURIComponent("影视院线"),
    "/api/sector/kline": "?board=" + encodeURIComponent("影视院线"),
    "/api/sector/fundflow": "",
    "/api/sentiment/board": "?board=" + encodeURIComponent("影视院线"),
    "/api/sentiment/stock": "?symbol=300750",
    "/api/fund/nav": "?code=017811",
    "/api/fund/drawdown": "?code=017811",
    "/api/fund/performance": "?code=017811",
    "/api/fund/holding": "?code=017811",
    "/api/fund/info": "?code=017811",
    "/api/fund/dividend": "?code=017811",
    "/api/fund/position": "?code=017811",
    "/api/fund/realtime": "?code=017811",
    "/api/news/stock": "?symbol=300750",
    "/api/news/financial-search": "?q=" + encodeURIComponent("宁德时代"),
    "/api/market/quote": "?symbol=000001",
    "/api/fund/yjb/realtime": "?code=002112",
  };

  // POST 类或需动态上下文的端点：GET 调用会得到「接口不存在 / 参数不合法」。
  // 这类组件按正式环境的「操作类控件」呈现，不发起 GET；有备用端点则改用备用端点。
  var SKIP_GET = ["/api/fund/yjb/consent", "/api/news/ai-review", "/api/sentiment/board"];

  function usableApi(widget) {
    var list = (widget.api || []).filter(function (p) { return SKIP_GET.indexOf(p) < 0; });
    return list[0] || "";
  }

  function withParams(path) {
    if (!path) { return ""; }
    if (path.indexOf("?") >= 0) { return path; }
    return path + (PARAM_HINTS[path] || "");
  }

  var MARKET_TEXT = { a: "A股", cn: "A股", hk: "港股", us: "美股" };

  /** 从实际请求路径的查询串提取「板块 / 标的」摘要，供卡片头显示。
   *
   * 背景（BUG-20261112-03）：V-02 板块估值、V-03 成分股的 board 只存在于请求参数里
   * （PARAM_HINTS 默认「影视院线」），卡片头只显示标题，用户无法判断数据属于哪个板块。
   *
   * @param {string} path 实际请求路径（含查询串）
   * @returns {string} 摘要文本，如「影视院线 · A股」；无参数返回空串
   */
  function paramChip(path) {
    var query = String(path || "").split("?")[1];
    if (!query) { return ""; }
    var read = function (key) {
      var hit = query.match(new RegExp("(?:^|&)" + key + "=([^&]*)"));
      return hit ? decodeURIComponent(hit[1]) : "";
    };
    var parts = [];
    var board = read("board") || read("sector");
    if (board) {
      parts.push(board);
    } else {
      var target = read("symbol") || read("code") || read("q");
      if (target) { parts.push(target); }
    }
    var market = read("market");
    if (market) { parts.push(MARKET_TEXT[market] || market); }
    var period = read("period");
    if (period) { parts.push({ daily: "日线", weekly: "周线", monthly: "月线" }[period] || period); }
    return parts.join(" · ");
  }

  function pickRows(data, prefer) {
    if (!data) { return []; }
    if (Array.isArray(data)) { return data; }
    var keys = ["items", "events", "indicators", "nodes", "top", "gainers", "losers",
      "periods", "annual", "distribution", "datasources", "gaps", "indices", "metrics",
      "dashboards", "tiers", "accounts", "results", "list"];
    // 1) 组件专属键（顶层），如 realtime-panel 的 kline / minute / news
    if (prefer && Array.isArray(data[prefer]) && data[prefer].length) {
      return data[prefer];
    }
    // 2) 组件专属键（嵌套一层），如 fund_dist.tiers
    if (prefer) {
      var outer = Object.keys(data);
      for (var j = 0; j < outer.length; j += 1) {
        var child = data[outer[j]];
        if (child && typeof child === "object" && !Array.isArray(child) &&
            Array.isArray(child[prefer]) && child[prefer].length) {
          return child[prefer];
        }
      }
    }
    // 3) 通用键（保持原有行为，不影响其他 44 个组件）
    for (var i = 0; i < keys.length; i += 1) {
      if (Array.isArray(data[keys[i]]) && data[keys[i]].length) { return data[keys[i]]; }
    }
    return [];
  }

  function scalars(data) {
    var out = [];
    if (!data || typeof data !== "object" || Array.isArray(data)) { return out; }
    Object.keys(data).forEach(function (key) {
      var value = data[key];
      if (typeof value === "number" ||
        (typeof value === "string" && value.length > 0 && value.length < 26)) {
        out.push([key, value]);
      }
    });
    return out.slice(0, 8);
  }

  function autoCols(rows) {
    var first = rows[0] || {};
    var keys = Object.keys(first)
      .filter(function (k) { return typeof first[k] !== "object"; })
      .filter(function (k) { return k !== "amount" || first.__amount_included !== true; })
      .slice(0, 6);
    var H = window.V6_HELPERS || {};
    return keys.map(function (k, i) {
      // v3.7.0（REQ-20261115-01 ⑧）：表头统一走中文列名映射（唯一来源 components.js）
      var label = H.colName ? H.colName(k) : k;
      return [k, label, i === 0 ? "22%" : null, typeof first[k] === "number" ? "num" : null];
    });
  }

  /** 行集是否含 OHLC（可画完整 K 线）。 */
  function hasOhlc(rows) {
    var f = rows[0] || {};
    return ["open", "close", "high", "low"].every(function (k) { return typeof f[k] === "number"; });
  }

  /** K 线占位：数据入缓存，由 components.drawPendingKlines 用 echarts 绘制（v3.7.0 ⑨）。 */
  function klinePlaceholder(rows, widget) {
    var key = "k" + (++chartSeq);
    window.__V6_KLINE = window.__V6_KLINE || {};
    window.__V6_KLINE[key] = { rows: rows.slice(-160), title: widget.title || "K 线" };
    return '<div class="v-kline" data-key="' + key + '"><div class="loading">K 线加载中…</div></div>';
  }

  var chartSeq = 0;
  /** 真实数值序列 → 动态 SVG 走势图（每次数据刷新重绘）。 */
  function chartSVG(rows) {
    var values = rows.slice(-40).map(function (row) {
      var keys = Object.keys(row);
      for (var i = 0; i < keys.length; i += 1) {
        if (typeof row[keys[i]] === "number") { return row[keys[i]]; }
      }
      return 0;
    });
    if (values.length < 2) { return '<div class="v-empty">真实数据不足以绘制走势</div>'; }
    var w = 420, h = 118, pad = 18;
    var min = Math.min.apply(null, values), max = Math.max.apply(null, values);
    var span = (max - min) || 1;
    var pts = values.map(function (v, i) {
      return [pad + i * (w - 2 * pad) / (values.length - 1), h - 20 - ((v - min) / span) * (h - 38)];
    });
    var line = pts.map(function (p) { return p[0].toFixed(1) + "," + p[1].toFixed(1); }).join(" L");
    var area = "M" + pts[0][0].toFixed(1) + "," + (h - 20) + " L" + line +
      " L" + pts[pts.length - 1][0].toFixed(1) + "," + (h - 20) + " Z";
    var gid = "liveChart" + (++chartSeq);
    return '<div class="live-chart"><span class="live-pulse"></span><span class="live-tag">实时</span>' +
      '<svg viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="none">' +
      "<defs><linearGradient id=\"" + gid + "\" x1=\"0\" y1=\"0\" x2=\"0\" y2=\"1\">" +
      "<stop offset=\"0%\" stop-color=\"#ff4b5f\" stop-opacity=\".48\"/>" +
      "<stop offset=\"100%\" stop-color=\"#ff4b5f\" stop-opacity=\"0\"/></linearGradient></defs>" +
      '<line x1="' + pad + '" y1="' + (h - 20) + '" x2="' + (w - pad) + '" y2="' + (h - 20) + '" stroke="#4d769c"/>' +
      '<path d="' + area + '" fill="url(#' + gid + ')"/>' +
      '<path d="M' + line + '" fill="none" stroke="#ff4b5f" stroke-width="2"/></svg></div>';
  }

  /** 按组件真实 category + 真实接口返回结构自动选择 V6 呈现方式。 */
  function widgetBody(widget, result) {
    if (widget.category === "filter" || widget._noData) {
      return '<div class="v-note">' + esc(widget.desc || widget.title) + "</div>" +
        '<div class="v-filter-demo"><span class="v-chip active">默认</span>' +
        '<span class="v-chip">备选 A</span><span class="v-chip">备选 B</span></div>';
    }
    if (result && result.error) {
      return '<div class="v-empty">业务状态：' + esc(result.error) + "</div>";
    }
    var data = result && result.data;
    var rows = pickRows(data, widget._rowKey);
    // v3.7.0（REQ-20261115-01 ①⑨）：OHLC 行 → 完整 K 线（蜡烛+量+MA），不再画「一条线」
    if (rows.length && hasOhlc(rows)) { return klinePlaceholder(rows, widget); }
    // 云图类组件（treemap）→ 面积∝成交额的云图
    if (data && Array.isArray(data.nodes) && data.nodes.length) {
      var key2 = "t" + (++chartSeq);
      window.__V6_TM = window.__V6_TM || {};
      window.__V6_TM[key2] = { nodes: data.nodes };
      return '<div class="v-tm v-tm-inline" data-key="' + key2 + '"></div>';
    }
    if (rows.length && widget.category === "chart") { return chartSVG(rows); }
    if (rows.length && widget.category === "news" && (rows[0].title || rows[0].text)) {
      return feed(rows, 8);
    }
    if (rows.length) { return table(autoCols(rows), rows.slice(0, 30)); }
    var sc = scalars(data);
    if (sc.length) {
      return kpis(sc.map(function (x) {
        return [x[0], typeof x[1] === "number" ? num(x[1], 2) : esc(x[1])];
      }));
    }
    if (result && result.demo) { return '<div class="v-empty">接口降级（演示数据）</div>'; }
    return '<div class="v-empty">真实空数据（EMPTY_DATA）</div>';
  }

  // ---- 9 个注册表页面：按真实组件还原 ----
  var byPage = {};
  REG.widgets.forEach(function (w) {
    var page = (w.page || "").replace("#/", "");
    (byPage[page] = byPage[page] || []).push(w);
  });

  Object.keys(byPage).forEach(function (viewId) {
    var view = V[viewId];
    if (!view) { return; }
    var widgets = byPage[viewId];
    var api = {}, keyOf = {}, seq = 0;
    widgets.forEach(function (w) {
      w._rowKey = ROW_HINTS[w.id] || "";
      var raw = usableApi(w);
      if (!raw) {
        w._dataKey = null;
        w._noData = true;
        w._src = "操作类组件（正式环境为 POST / 上下文操作，不发起 GET）";
        return;
      }
      var path = withParams(raw);
      if (!(path in keyOf)) {
        keyOf[path] = "r" + (seq += 1);
        api[keyOf[path]] = [path, "registryWidget"];
      }
      w._dataKey = keyOf[path];
      w._noData = false;
      w._src = (w.api || []).join(" · ");
      w._path = path;
    });
    view.api = api;
    view.render = function (map, viewRef) {
      // app.js 的 apiWithHash 会把 hash 参数覆盖后的实际接口挂到 view._api 上，
      // 卡头参数摘要必须反映「真实请求的参数」，否则 URL 换了板块而标签不动
      var effective = (viewRef && viewRef._api) || null;
      return widgets.map(function (w) {
        var path = (effective && effective[w._dataKey] && effective[w._dataKey][0]) || w._path;
        return card(w.id + " · " + w.title, widgetBody(w, map[w._dataKey]), w._src,
          CAT_ICON[w.category] || "·", paramChip(path));
      }).join("");
    };
    view.chart = null;      // 移除自造图表层：正式环境该页无此功能
    view.chartKey = null;   // 旧 K 线容器由注册表组件自己呈现
  });

  // ---- 其余视图：移除自造图表层（功能与正式环境一致） ----
  Object.keys(V).forEach(function (id) {
    if (!byPage[id] && V[id].chart) { V[id].chart = null; }
  });

  // ---- 组件库：49 张真实组件卡 + 点击弹窗选择目标页面 ----
  V["widget-library"].api = {};
  V["widget-library"].render = function () {
    var cats = REG.categories || {};
    var items = REG.widgets || [];
    var summary = kpis([
      ["组件总数", REG.total || items.length],
      ["组件分类", Object.keys(cats).length],
      ["图表走势", items.filter(function (w) { return w.category === "chart"; }).length],
      ["行情报价", items.filter(function (w) { return w.category === "market"; }).length],
      ["列表表格", items.filter(function (w) { return w.category === "table"; }).length],
      ["已添加", '<span id="v6-add-count">' + (window.V6_ADDS || []).length + "</span>"]
    ]);
    var groups = Object.keys(cats).map(function (key) {
      var list = items.filter(function (w) { return w.category === key; });
      return card(cats[key] + " · " + list.length, '<div class="widget-grid">' + list.map(function (w) {
        return '<div class="widget-card"><div class="widget-meta"><b>' + esc(w.id) + "</b> " +
          esc(w.title) + '</div><div class="widget-desc">' + esc(w.desc || "—") + "</div>" +
          '<div class="widget-foot">' + esc(w.page || "—") + " · span " + esc(w.span) + "</div>" +
          '<button class="widget-add" data-id="' + esc(w.id) + '">＋ 添加到页面</button></div>';
      }).join("") + "</div>", "/api/widgets · " + key, "组");
    }).join("");
    return card("组件注册表（正式环境 /api/widgets）", summary, "/api/widgets", "库") + groups;
  };
  V["widget-library"].chart = null;

  // ---- 添加弹窗：用户决定加在哪个界面（仅本地演示，不写后端） ----
  window.V6_ADDS = window.V6_ADDS || [];

  function openAddModal(widgetId) {
    var widget = (REG.widgets || []).filter(function (w) { return w.id === widgetId; })[0];
    var modal = document.getElementById("v6-add-modal");
    if (!widget || !modal) { return; }
    document.getElementById("v6-add-widget").textContent = widget.id + " · " + widget.title;
    var select = document.getElementById("v6-add-target");
    var options = ['<option value="__dashboard__">我的仪表板（48 列栅格）</option>'];
    Object.keys(V).forEach(function (id) {
      // 编辑页（sidebar=null）与 AI 页不承载组件，不作为添加目标
      if (V[id].sidebar === null || id === "ai" || id === "backtest" || id === "dca") { return; }
      options.push('<option value="' + esc(id) + '">' + esc(V[id].title) + "  #/" + esc(id) + "</option>");
    });
    select.innerHTML = options.join("");
    modal.hidden = false;
    modal.setAttribute("data-widget", widgetId);
  }

  function closeAddModal() {
    var modal = document.getElementById("v6-add-modal");
    if (modal) { modal.hidden = true; }
  }

  function confirmAdd() {
    var modal = document.getElementById("v6-add-modal");
    if (!modal) { return; }
    var widgetId = modal.getAttribute("data-widget");
    var target = document.getElementById("v6-add-target").value;
    window.V6_ADDS.push({ id: widgetId, target: target, at: Date.now() });
    var counter = document.getElementById("v6-add-count");
    if (counter) { counter.textContent = window.V6_ADDS.length; }
    var hint = document.getElementById("v6-add-hint");
    if (hint) {
      // v3.7.0（REQ-20261115-01 ⑤）：真实落库由 w7_portfolio.bindDashboardPersist 执行
      // （PUT /api/dashboards）；此处只回显目标，不再宣称「演示状态、不写入」。
      hint.textContent = "已提交：将 " + widgetId + " 添加到 " +
        (target === "__dashboard__" ? "我的仪表板" : V[target].title) + "（含参数，落库 /api/dashboards）";
    }
    closeAddModal();
  }

  document.addEventListener("click", function (event) {
    var add = event.target.closest ? event.target.closest(".widget-add") : null;
    if (add) { openAddModal(add.getAttribute("data-id")); return; }
    if (event.target.closest && event.target.closest("#v6-add-close")) { closeAddModal(); return; }
    if (event.target.closest && event.target.closest("#v6-add-cancel")) { closeAddModal(); return; }
    if (event.target.closest && event.target.closest("#v6-add-ok")) { confirmAdd(); return; }
    if (event.target.id === "v6-add-modal") { closeAddModal(); }
  });

  window.V6_REGISTRY_VIEW = { byPage: byPage, openAddModal: openAddModal, closeAddModal: closeAddModal };
})();
