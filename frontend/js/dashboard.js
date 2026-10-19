/* =============================================================
 * OpenBB 中文投研工作台 · 组件化仪表板（阶段 6）
 * 能力：48 列网格 / 组件库（按 /api/widgets 注册表）/ 新建仪表板 / 模板库 /
 *       拖拽排序 / 宽度调整（吸附到 48 列栅格）/ 保存布局（localStorage 持久化）/
 *       表格排序与 CSV 导出（UTF-8 BOM）/ 指标卡 / 资讯流 / 情绪面板 / 图表。
 * 约束：配色只取 theme.css（--up/--down），不新增配色；无演示数据，全部走真实接口。
 * ============================================================= */

(function () {
  "use strict";

  var STORAGE_KEY = "openbb-dashboards-v1";
  var SPANS = [12, 16, 20, 24, 28, 32, 48];
  var SPAN_LABEL = { 12: "1/4", 16: "1/3", 20: "5/12", 24: "1/2", 28: "7/12", 32: "2/3", 48: "整行" };

  var state = { dashboards: [], activeId: "", registry: null };

  /** i18n 取词（未加载时回退）。 */
  function t(key, vars) {
    return window.I18N ? window.I18N.t(key, vars) : key;
  }

  /** 本地化格式。 */
  function L() {
    return window.L10N;
  }

  /** 创建元素。 */
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) {
      node.className = cls;
    }
    if (text !== undefined && text !== null) {
      node.textContent = String(text);
    }
    return node;
  }

  /** 轻提示。 */
  function toast(message) {
    if (window.UI) {
      window.UI.toast(message);
    }
  }

  /** 读取本地布局；损坏或缺失时返回默认仪表板。 */
  function loadState() {
    var raw = null;
    try {
      raw = localStorage.getItem(STORAGE_KEY);
    } catch (error) {
      raw = null;
    }
    if (raw) {
      try {
        var parsed = JSON.parse(raw);
        if (parsed && Array.isArray(parsed.dashboards) && parsed.dashboards.length) {
          state.dashboards = parsed.dashboards;
          state.activeId = parsed.activeId || parsed.dashboards[0].id;
          return;
        }
      } catch (error) {
        // 损坏则重建，不影响使用
      }
    }
    state.dashboards = [buildDefaultDashboard()];
    state.activeId = state.dashboards[0].id;
  }

  /** 保存布局到 localStorage（刷新后仍生效）。 */
  function saveState(silent) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        dashboards: state.dashboards,
        activeId: state.activeId
      }));
      if (!silent) {
        toast(t("dashboard.saved"));
      }
    } catch (error) {
      toast("保存失败：浏览器存储不可用");
    }
  }

  /** 默认仪表板（全部使用真实接口，无演示数据）。 */
  function buildDefaultDashboard() {
    return {
      id: "dash-" + Date.now(),
      name: t("dashboard.defaultName"),
      widgets: [
        { id: "ST-01", span: 48 },
        { id: "S-03", span: 28 },
        { id: "S-06", span: 20 },
        { id: "S-04", span: 28 },
        { id: "N-03", span: 20 },
        { id: "N-02", span: 48 }
      ]
    };
  }

  /** 当前仪表板。 */
  function activeDashboard() {
    for (var i = 0; i < state.dashboards.length; i += 1) {
      if (state.dashboards[i].id === state.activeId) {
        return state.dashboards[i];
      }
    }
    state.activeId = state.dashboards[0].id;
    return state.dashboards[0];
  }

  /** 拉取注册表（35 个组件元数据）。 */
  function loadRegistry() {
    if (state.registry) {
      return Promise.resolve(state.registry);
    }
    return fetch("/api/widgets", { cache: "no-store" })
      .then(function (response) { return response.json(); })
      .then(function (body) {
        state.registry = {};
        ((body.data && body.data.items) || []).forEach(function (item) {
          state.registry[item.id] = item;
        });
        return state.registry;
      });
  }

  // =============================================================
  // 组件取数与渲染配方（按组件 id 映射到真实接口）
  // =============================================================
  var RECIPES = {
    "ST-01": { kind: "panel", path: "/api/stock/realtime-panel", params: { symbol: "688012", market: "a" }, pick: "quote" },
    "ST-02": { kind: "chart", path: "/api/stock/realtime-panel", params: { symbol: "688012", market: "a" }, pick: "kline", x: "date", y: "close" },
    "ST-04": { kind: "metrics", path: "/api/stock/realtime-panel", params: { symbol: "688012", market: "a" }, pick: "fund_dist" },
    "S-03": { kind: "chart", path: "/api/sector/heatmap", params: { market: "cn", kind: "industry" }, pick: "nodes", x: "name", y: "pct", bar: true },
    "S-04": { kind: "table", path: "/api/sector/industry", params: { market: "cn" }, cols: ["name", "pct", "amount", "lead_stock"] },
    "S-05": { kind: "table", path: "/api/sector/concept", params: { market: "cn" }, cols: ["name", "pct", "amount", "lead_stock"] },
    "S-06": { kind: "metrics", path: "/api/sector/summary", params: { market: "cn" }, pick: "gainers" },
    "S-07": { kind: "table", path: "/api/sector/fundflow", params: { market: "cn" }, cols: ["name", "net_inflow", "inflow", "outflow"] },
    "N-02": { kind: "news", path: "/api/news/ai-feed", params: { limit: 12, sort: "relevance" } },
    "N-03": { kind: "metrics", path: "/api/sentiment/market", params: { region: "cn" }, pick: "self" },
    "F-03": { kind: "table", path: "/api/fund/performance", params: { code: "017811" }, cols: ["period", "growth", "rank"] },
    "F-04": { kind: "table", path: "/api/fund/holding", params: { code: "017811" }, cols: ["code", "name", "ratio", "value"] },
    "P-01": { kind: "metrics", path: "/api/portfolio/analysis", params: {}, pick: "totals" },
    "P-03": { kind: "table", path: "/api/portfolio/analysis", params: {}, cols: ["market_label", "symbol", "name", "market_value", "pnl", "pnl_pct"] },
    "V-01": { kind: "chart", path: "/api/sector/kline", params: { board: "半导体", market: "cn" }, pick: "items", x: "date", y: "close" }
  };

  /** 表头本地化映射（字段名 → i18n key）。 */
  var HEADER_KEYS = {
    name: "th.name", code: "th.code", symbol: "th.code", pct: "th.pct", amount: "th.amount",
    lead_stock: "th.leadStock", lead_pct: "th.leadPct", net_inflow: "th.netInflow",
    inflow: "th.inflow", outflow: "th.outflow", super_big: "th.superBig", big: "th.big",
    period: "th.period", growth: "th.growth", rank: "th.rank", ratio: "th.weight",
    value: "th.value", price: "th.price", market_value: "th.value", pnl: "th.pnl",
    pnl_pct: "th.pnlPct", market_label: "th.market", time: "th.time", title: "新闻标题"
  };

  /** 取单元格显示值（数值走本地化）。 */
  function cellText(value, field) {
    if (value === null || value === undefined) {
      return t("value.none");
    }
    if (typeof value === "number") {
      if (field && (field.indexOf("pct") >= 0 || field === "growth" || field === "ratio")) {
        return L().pct(value);
      }
      return L().num(value, 2);
    }
    return String(value);
  }

  /** 带涨跌色的单元格。 */
  function cellNode(value, field) {
    var node = el("td", "num", cellText(value, field));
    if (typeof value === "number" && (field.indexOf("pct") >= 0 || field === "pnl" || field === "growth" || field === "net_inflow")) {
      node.className = "num " + (value > 0 ? "up" : value < 0 ? "down" : "flat");
    }
    return node;
  }

  /** 统一请求（中文错误）。 */
  function fetchJson(path, params) {
    var query = new URLSearchParams();
    Object.keys(params || {}).forEach(function (key) {
      query.append(key, params[key]);
    });
    var url = path + (query.toString() ? "?" + query.toString() : "");
    return fetch(url, { cache: "no-store" })
      .catch(function () {
        throw new Error(t("error.network"));
      })
      .then(function (response) { return response.json(); })
      .then(function (body) {
        if (body && body.error) {
          var error = new Error(body.error);
          error.code = body.error_code;
          throw error;
        }
        return body.data || {};
      });
  }

  /** 渲染表格（可排序 + 可导出 CSV）。 */
  function renderTable(container, rows, cols, widget) {
    if (!rows.length) {
      container.appendChild(el("div", "pg-state", t("state.empty")));
      return;
    }
    var wrap = el("div", "pg-table-wrap");
    var table = el("table", "pg-table");
    var thead = el("thead");
    var headRow = el("tr");
    var sortState = { field: null, asc: true };
    cols.forEach(function (field) {
      var th = el("th", field === "name" ? "" : "num", HEADER_KEYS[field] ? t(HEADER_KEYS[field]) : field);
      th.style.cursor = "pointer";
      th.addEventListener("click", function () {
        sortState.asc = sortState.field === field ? !sortState.asc : true;
        sortState.field = field;
        var sorted = rows.slice().sort(function (a, b) {
          var va = a[field];
          var vb = b[field];
          if (typeof va === "number" && typeof vb === "number") {
            return sortState.asc ? va - vb : vb - va;
          }
          return sortState.asc
            ? String(va === null || va === undefined ? "" : va).localeCompare(String(vb === null || vb === undefined ? "" : vb), "zh-CN")
            : String(vb === null || vb === undefined ? "" : vb).localeCompare(String(va === null || va === undefined ? "" : va), "zh-CN");
        });
        tbody.innerHTML = "";
        sorted.forEach(function (row) {
          tbody.appendChild(buildRow(row, cols));
        });
      });
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);
    var tbody = el("tbody");

    function buildRow(row, columns) {
      var tr = el("tr");
      columns.forEach(function (field) {
        tr.appendChild(cellNode(row[field], field));
      });
      return tr;
    }

    rows.forEach(function (row) {
      tbody.appendChild(buildRow(row, cols));
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    container.appendChild(wrap);

    // CSV 导出（UTF-8 BOM，Excel 直接打开不乱码）
    var exportButton = el("button", "pg-btn", t("action.export"));
    exportButton.type = "button";
    exportButton.addEventListener("click", function () {
      if (!rows.length) {
        toast(t("error.exportEmpty"));
        return;
      }
      var headers = cols.map(function (field) { return HEADER_KEYS[field] ? t(HEADER_KEYS[field]) : field; });
      var body = rows.map(function (row) {
        return cols.map(function (field) { return row[field]; });
      });
      var blob = new Blob([L().csvText(headers, body)], { type: "text/csv;charset=utf-8" });
      var link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = L().csvFilename(widget.title || widget.id);
      document.body.appendChild(link);
      link.click();
      link.remove();
      toast(t("toast.copied"));
    });
    container.appendChild(exportButton);
  }

  /** 渲染指标卡。 */
  function renderMetrics(container, data, widget) {
    var entries = [];
    if (Array.isArray(data)) {
      data.slice(0, 6).forEach(function (item, index) {
        entries.push([item.name || "指标" + (index + 1), item.pct !== undefined ? item.pct : item.net_inflow, item.pct !== undefined ? "pct" : "num"]);
      });
    } else if (data && typeof data === "object") {
      Object.keys(data).slice(0, 8).forEach(function (key) {
        var value = data[key];
        if (typeof value === "number" || typeof value === "string") {
          entries.push([key, value, typeof value === "number" ? "num" : "text"]);
        }
      });
    }
    if (!entries.length) {
      container.appendChild(el("div", "pg-state", t("state.empty")));
      return;
    }
    var grid = el("div", "pg-metric-grid");
    entries.forEach(function (entry) {
      var cell = el("div", "pg-metric-cell");
      cell.appendChild(el("div", "pg-metric-label", entry[0]));
      var text = entry[2] === "pct" ? L().pct(entry[1]) : (entry[2] === "num" ? L().num(entry[1], 2) : entry[1]);
      var cls = entry[2] === "pct" && typeof entry[1] === "number" ? (entry[1] > 0 ? "up" : entry[1] < 0 ? "down" : "flat") : "";
      cell.appendChild(el("div", "pg-metric-value " + cls, text));
      grid.appendChild(cell);
    });
    container.appendChild(grid);
  }

  /** 渲染折线/柱状图（ECharts 本地库）。 */
  function renderChart(container, items, recipe) {
    if (!items || !items.length) {
      container.appendChild(el("div", "pg-state", t("state.empty")));
      return;
    }
    var box = el("div", "pg-chart");
    container.appendChild(box);
    var chart = window.echarts ? window.echarts.init(box) : null;
    if (!chart) {
      container.appendChild(el("div", "pg-state", "图表库未加载"));
      return;
    }
    var styles = getComputedStyle(document.documentElement);
    var textColor = styles.getPropertyValue("--text-dim").trim() || "#a2a2a2";
    var borderColor = styles.getPropertyValue("--border").trim() || "#2a2a2a";
    var up = styles.getPropertyValue("--up").trim() || "#ff4b4b";
    var down = styles.getPropertyValue("--down").trim() || "#00b061";
    var labels = items.map(function (item) { return item[recipe.x]; });
    var values = items.map(function (item) { return item[recipe.y]; });
    chart.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis" },
      grid: { left: 56, right: 16, top: 20, bottom: 28 },
      xAxis: {
        type: "category", data: labels,
        axisLabel: { color: textColor, interval: Math.max(0, Math.floor(labels.length / 8)) },
        axisLine: { lineStyle: { color: borderColor } }
      },
      yAxis: {
        type: "value", scale: true,
        axisLabel: { color: textColor },
        splitLine: { lineStyle: { color: borderColor, opacity: 0.4 } }
      },
      series: [{
        type: recipe.bar ? "bar" : "line",
        data: recipe.bar ? values.map(function (value) {
          return { value: value, itemStyle: { color: value > 0 ? up : down } };
        }) : values,
        showSymbol: false,
        lineStyle: { width: 1.6 }
      }]
    });
    window.addEventListener("resize", function () { chart.resize(); });
  }

  /** 渲染资讯流（真实新闻 + 本地情绪标签）。 */
  function renderNews(container, items) {
    if (!items || !items.length) {
      container.appendChild(el("div", "pg-state", t("state.empty")));
      return;
    }
    var list = el("div", "pg-feed-list");
    items.slice(0, 10).forEach(function (item) {
      var sentiment = item.sentiment || {};
      var row = el("article", "pg-feed-item");
      var head = el("div", "pg-feed-head");
      head.appendChild(el("span", sentiment.label === "利好" ? "chip up" : sentiment.label === "利空" ? "chip down" : "chip",
        sentiment.label || "中性"));
      head.appendChild(el("span", "pg-feed-score", L().num(sentiment.score, 4)));
      head.appendChild(el("span", "pg-spacer"));
      head.appendChild(el("span", "pg-news-time", L().datetime(item.time)));
      row.appendChild(head);
      row.appendChild(el("div", "pg-feed-title", item.title || ""));
      list.appendChild(row);
    });
    container.appendChild(list);
  }

  /** 分组渲染：表格 / 图表 / 指标 / 资讯 / 面板。 */
  function renderWidgetBody(body, data, recipe, widget) {
    body.innerHTML = "";
    if (recipe.kind === "table") {
      var rows = (data.items || data.nodes || []).filter(function (item) { return item; });
      renderTable(body, rows, recipe.cols, widget);
      return;
    }
    if (recipe.kind === "chart") {
      renderChart(body, data[recipe.pick] || [], recipe);
      return;
    }
    if (recipe.kind === "news") {
      renderNews(body, data.items || []);
      return;
    }
    if (recipe.kind === "metrics") {
      var payload = recipe.pick === "self" ? data : data[recipe.pick];
      renderMetrics(body, payload, widget);
      return;
    }
    if (recipe.kind === "panel") {
      var quote = data[recipe.pick] || {};
      var grid = el("div", "pg-metric-grid");
      [["名称", quote.name], ["最新价", L().price(quote.price, "a")], ["涨跌幅", L().pct(quote.pct)],
       ["今开", L().price(quote.open, "a")], ["最高", L().price(quote.high, "a")], ["最低", L().price(quote.low, "a")]]
        .forEach(function (pair) {
          var cell = el("div", "pg-metric-cell");
          cell.appendChild(el("div", "pg-metric-label", pair[0]));
          cell.appendChild(el("div", "pg-metric-value", pair[1]));
          grid.appendChild(cell);
        });
      body.appendChild(grid);
      body.setAttribute("data-state", "default");
    }
  }

  // =============================================================
  // 组件卡片
  // =============================================================
  var draggedIndex = null;

  /** 构建一张组件卡（含拖拽、宽度调整、移除、CSV）。 */
  function buildWidgetCard(entry, index) {
    var meta = (state.registry && state.registry[entry.id]) || { id: entry.id, title: entry.id, desc: "" };
    var card = el("section", "pg-card dash-widget");
    card.style.gridColumn = "span " + entry.span;
    card.setAttribute("draggable", "true");
    card.dataset.index = String(index);
    card.dataset.widgetId = entry.id;

    var head = el("div", "pg-card-head");
    head.appendChild(el("span", "pg-card-title", meta.title));
    head.appendChild(el("span", "pg-card-sub", meta.id));
    head.appendChild(el("span", "pg-spacer"));

    // 宽度调整（吸附到 48 列栅格）
    var resize = el("button", "pg-btn dash-btn", SPAN_LABEL[entry.span] || "宽度");
    resize.type = "button";
    resize.title = t("dashboard.resize");
    resize.addEventListener("click", function (event) {
      event.stopPropagation();
      var position = SPANS.indexOf(entry.span);
      entry.span = SPANS[(position + 1) % SPANS.length];
      card.style.gridColumn = "span " + entry.span;
      resize.textContent = SPAN_LABEL[entry.span];
      saveState(true);
    });
    head.appendChild(resize);

    var remove = el("button", "pg-btn dash-btn", "×");
    remove.type = "button";
    remove.title = t("dashboard.removeWidget");
    remove.addEventListener("click", function (event) {
      event.stopPropagation();
      var dash = activeDashboard();
      dash.widgets.splice(index, 1);
      saveState(true);
      render();
    });
    head.appendChild(remove);
    card.appendChild(head);

    var body = el("div", "pg-body");
    card.appendChild(body);
    // 注册表与接口均为异步：直接进入加载态，避免先闪一次空态
    loadWidget(body, card, entry, meta);

    // 拖拽排序
    card.addEventListener("dragstart", function (event) {
      draggedIndex = index;
      event.dataTransfer.effectAllowed = "move";
    });
    card.addEventListener("dragover", function (event) {
      event.preventDefault();
    });
    card.addEventListener("drop", function (event) {
      event.preventDefault();
      if (draggedIndex === null || draggedIndex === index) {
        return;
      }
      var dash = activeDashboard();
      var moved = dash.widgets.splice(draggedIndex, 1)[0];
      dash.widgets.splice(index, 0, moved);
      draggedIndex = null;
      saveState(true);
      render();
    });
    return card;
  }

  /** 加载单个组件数据（失败就地显示错误 + 重试）。 */
  function loadWidget(body, card, entry, meta) {
    body.innerHTML = "";
    body.appendChild(el("div", "pg-state", t("state.loading")));
    var recipe = RECIPES[entry.id];
    if (!recipe) {
      body.innerHTML = "";
      var hint = el("div", "pg-state");
      hint.appendChild(el("div", "pg-state-title", meta.desc || meta.title));
      if (meta.api && meta.api.length) {
        hint.appendChild(el("div", "pg-state-hint", "接口：" + meta.api.join("、") + "（该组件暂无内置渲染器）"));
      }
      body.appendChild(hint);
      return;
    }
    fetchJson(recipe.path, recipe.params)
      .then(function (data) {
        body.innerHTML = "";
        renderWidgetBody(body, data, recipe, meta);
        // 重新加载时先清掉旧徽章，避免重复叠加
        card.querySelectorAll(".dash-badges").forEach(function (node) { node.remove(); });
        var badges = el("div", "dash-badges");
        if (data.source) {
          badges.appendChild(el("span", "badge-src", data.source));
        }
        if (data.caliber) {
          badges.appendChild(el("span", "badge-caliber", data.caliber));
        }
        if (badges.children.length) {
          card.querySelector(".pg-card-head").appendChild(badges);
        }
        body.setAttribute("data-state", "default");
      })
      .catch(function (error) {
        body.innerHTML = "";
        var box = el("div", "pg-state");
        box.appendChild(el("div", "pg-state-title", t("state.errorTitle")));
        box.appendChild(el("div", "pg-state-hint", error.message));
        var retry = el("button", "pg-btn", t("action.retry"));
        retry.type = "button";
        retry.addEventListener("click", function () { loadWidget(body, card, entry, meta); });
        box.appendChild(retry);
        body.appendChild(box);
        body.setAttribute("data-state", "error");
      });
  }

  // =============================================================
  // 布局与工具栏
  // =============================================================
  /** 渲染仪表板页。 */
  function render() {
    var container = document.getElementById("page-dashboard");
    if (!container) {
      return;
    }
    loadRegistry().then(function () {
      container.innerHTML = "";
      var toolbar = el("div", "page-toolbar");
      var tabs = el("div", "pg-tabs");
      state.dashboards.forEach(function (dash) {
        var button = el("button", "pg-tab" + (dash.id === state.activeId ? " active" : ""), dash.name);
        button.type = "button";
        button.addEventListener("click", function () {
          state.activeId = dash.id;
          saveState(true);
          render();
        });
        tabs.appendChild(button);
      });
      toolbar.appendChild(tabs);

      var addDash = el("button", "pg-btn", "+ " + t("dashboard.new"));
      addDash.type = "button";
      addDash.addEventListener("click", function () {
        if (!window.UI) {
          return;
        }
        window.UI.modal({
          title: t("dashboard.new"),
          input: true,
          defaultValue: t("dashboard.defaultName"),
          onConfirm: function (value) {
            var name = value || t("dashboard.defaultName");
            var dash = { id: "dash-" + Date.now(), name: name, widgets: [] };
            state.dashboards.push(dash);
            state.activeId = dash.id;
            saveState(true);
            render();
          }
        });
      });
      toolbar.appendChild(addDash);

      var library = el("button", "pg-btn", t("dashboard.library"));
      library.type = "button";
      library.addEventListener("click", openLibrary);
      toolbar.appendChild(library);

      var templates = el("button", "pg-btn", t("dashboard.templates"));
      templates.type = "button";
      templates.addEventListener("click", openTemplates);
      toolbar.appendChild(templates);

      toolbar.appendChild(el("span", "pg-spacer"));
      var save = el("button", "pg-btn", t("dashboard.save"));
      save.type = "button";
      save.addEventListener("click", function () { saveState(false); });
      toolbar.appendChild(save);
      container.appendChild(toolbar);
      container.appendChild(el("div", "pg-card-sub", t("dashboard.dragHint")));

      var grid = el("div", "pg-grid dash-grid");
      grid.id = "dash-grid";
      var dash = activeDashboard();
      if (!dash.widgets.length) {
        var empty = el("div", "pg-state dash-empty");
        empty.style.gridColumn = "span 48";
        empty.appendChild(el("div", "pg-state-title", t("dashboard.empty")));
        var libraryButton = el("button", "pg-btn", t("dashboard.library"));
        libraryButton.type = "button";
        libraryButton.addEventListener("click", openLibrary);
        empty.appendChild(libraryButton);
        grid.appendChild(empty);
      } else {
        dash.widgets.forEach(function (entry, index) {
          grid.appendChild(buildWidgetCard(entry, index));
        });
      }
      container.appendChild(grid);
    });
  }

  /** 组件库抽屉。 */
  function openLibrary() {
    var registry = state.registry || {};
    var overlay = el("div", "ui-overlay");
    var panel = el("aside", "dash-library");
    var head = el("div", "pg-card-head");
    head.appendChild(el("span", "pg-card-title", t("dashboard.library")));
    head.appendChild(el("span", "pg-spacer"));
    var close = el("button", "pg-btn", t("action.close"));
    close.type = "button";
    close.addEventListener("click", function () { overlay.remove(); });
    head.appendChild(close);
    panel.appendChild(head);

    var pages = {};
    Object.keys(registry).forEach(function (id) {
      var item = registry[id];
      pages[item.page] = pages[item.page] || [];
      pages[item.page].push(item);
    });
    Object.keys(pages).forEach(function (page) {
      panel.appendChild(el("div", "pg-card-sub", page));
      pages[page].forEach(function (item) {
        var row = el("div", "dash-lib-row");
        row.appendChild(el("span", "dash-lib-id", item.id));
        row.appendChild(el("span", "dash-lib-title", item.title));
        var add = el("button", "pg-btn", t("action.add"));
        add.type = "button";
        add.addEventListener("click", function () {
          addWidgetById(item.id);
        });
        row.appendChild(add);
        panel.appendChild(row);
      });
    });
    overlay.appendChild(panel);
    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) {
        overlay.remove();
      }
    });
    document.body.appendChild(overlay);
  }

  /** 模板库：三个预设布局（全部真实接口组件）。 */
  function openTemplates() {
    if (!window.UI) {
      return;
    }
    var overlay = el("div", "ui-overlay");
    var box = el("div", "ui-modal");
    box.appendChild(el("div", "ui-modal-title", t("dashboard.templates")));
    var presets = [
      { name: t("dashboard.template.overview"), widgets: ["ST-01", "S-03", "S-06", "S-04", "S-07"] },
      { name: t("dashboard.template.news"), widgets: ["N-02", "N-03", "S-03", "S-06"] },
      { name: t("dashboard.template.portfolio"), widgets: ["P-01", "P-03", "F-04", "F-03"] }
    ];
    presets.forEach(function (preset) {
      var button = el("button", "copilot-suggest", preset.name + "（" + preset.widgets.length + " 个组件）");
      button.type = "button";
      button.addEventListener("click", function () {
        var dash = activeDashboard();
        dash.widgets = preset.widgets.map(function (id) {
          var meta = state.registry[id] || {};
          return { id: id, span: meta.span || 24 };
        });
        saveState(true);
        overlay.remove();
        render();
        toast(t("dashboard.saved"));
      });
      box.appendChild(button);
    });
    overlay.appendChild(box);
    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) {
        overlay.remove();
      }
    });
    document.body.appendChild(overlay);
  }

  /** 按 id 添加组件（供命令面板与组件库调用）。 */
  function addWidgetById(widgetId) {
    var dash = activeDashboard();
    if (dash.widgets.some(function (entry) { return entry.id === widgetId; })) {
      toast("该组件已在仪表板中");
      return;
    }
    var meta = (state.registry && state.registry[widgetId]) || {};
    dash.widgets.push({ id: widgetId, span: meta.span || 24 });
    saveState(true);
    if (location.hash === "#/dashboard") {
      render();
    }
  }

  window.Dashboard = {
    render: render,
    addWidgetById: addWidgetById,
    reload: function () { loadState(); render(); }
  };

  // 暴露初始化：pages.js 路由到 #/dashboard 时调用
  window.renderDashboard = function () {
    loadState();
    render();
  };

  // 时序兜底：pages.js 先于本文件执行，首次进入 #/dashboard 时 renderDashboard 尚未定义，
  // 故本文件加载完成后按当前 hash 补渲染一次（否则首屏空白，刷新后才出现）。
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      if ((location.hash || "").indexOf("#/dashboard") === 0) {
        window.renderDashboard();
      }
    });
  } else if ((location.hash || "").indexOf("#/dashboard") === 0) {
    window.renderDashboard();
  }
})();
