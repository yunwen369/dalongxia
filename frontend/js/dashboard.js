/* =============================================================
 * OpenBB 中文投研工作台 · 组件化仪表板（阶段 6）
 * 能力：48 列网格 / 组件库（按 /api/widgets 注册表）/ 新建仪表板 / 模板库 /
 *       拖拽排序 / 宽度调整（吸附到 48 列栅格）/ 保存布局（localStorage 持久化）/
 *       表格排序与 CSV 导出（UTF-8 BOM）/ 指标卡 / 资讯流 / 情绪面板 / 图表。
 * 约束：配色只取 theme.css（--up/--down），不新增配色；无演示数据，全部走真实接口。
 * ============================================================= */

(function () {
  "use strict";

  // P1-13：共享工具别名（t / el / toast 原本各 IIFE 重复定义于此）
  // 注意：必须先声明再使用 —— SPAN_LABEL 初始化即调用 t()，若别名在其后声明会因
  // 变量提升得到 undefined，整个 IIFE 抛 TypeError，仪表板将整页空白。
  var t = window.Utils.t;
  var el = window.Utils.el;
  var toast = window.Utils.toast;
  // 本 IIFE 内需独立引用格式化/涨跌色工具（pages.js 的 fmt/pctClass 不在本作用域）
  var fmt = window.Utils.fmt;
  var fmtPct = window.Utils.fmtPct;
  var pctClass = window.Utils.trendClass;

  var STORAGE_KEY = "openbb-dashboards-v1";
  var SPANS = [12, 16, 20, 24, 28, 32, 48];
  var SPAN_LABEL = { 12: "1/4", 16: "1/3", 20: "5/12", 24: "1/2", 28: "7/12", 32: "2/3", 48: t("dashboard.span.full") };

  var state = { dashboards: [], activeId: "", registry: null };

  /** 本地化格式。
   * @returns {*} 处理结果
   */
  function L() {
    return window.L10N;
  }

  /** 读取本地布局；损坏或缺失时返回默认仪表板。 */
  /** 读取布局：先取本地缓存秒开，再异步对齐后端 data/dashboards.json。
   * 后端有数据时以后端为准（多标签页/换浏览器后仍能恢复），本地仅作兜底缓存。
   */
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
        }
      } catch (error) {
        // 损坏则重建，不影响使用
      }
    }
    // 只有「从未初始化」（本地无缓存键）才自动建默认板；
    // 用户把仪表板删光时 localStorage 会存成空数组，此时必须停在「无仪表板」引导态，
    // 否则一刷新就自动补回默认板，「无仪表板」状态永远不可达（四态验收要求）。
    var hadCache = false;
    try {
      hadCache = localStorage.getItem(STORAGE_KEY) !== null;
    } catch (error) {
      hadCache = false;
    }
    if (!state.dashboards.length && !hadCache) {
      state.dashboards = [buildDefaultDashboard()];
      state.activeId = state.dashboards[0].id;
    }
    // R3-02：异步拉取后端配置（失败时保留本地缓存，不阻断渲染）
    fetchJson("/api/dashboards", {}).then(function (data) {
      if (data && Array.isArray(data.dashboards) && data.dashboards.length) {
        state.dashboards = data.dashboards;
        state.activeId = data.activeId || data.dashboards[0].id;
        try {
          localStorage.setItem(STORAGE_KEY, JSON.stringify({
            dashboards: state.dashboards,
            activeId: state.activeId
          }));
        } catch (error) {
          // 本地缓存写失败不影响已渲染内容
        }
        if (typeof window.renderDashboard === "function") {
          window.renderDashboard();
        }
      }
    }).catch(function () {
      // 后端不可用时静默降级到本地缓存（离线/只读场景）
    });
  }

  /** 保存布局：先写本地缓存，再异步落盘后端 data/dashboards.json。
   * @param {*} silent 是否静默（true 时不弹提示）
   * @returns {void} 无返回值（异步落盘，失败以 toast 提示）
   */
  function saveState(silent) {
    var payload = { dashboards: state.dashboards, activeId: state.activeId };
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    } catch (error) {
      // 本地缓存失败仍尝试后端落盘
    }
    fetch("/api/dashboards", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (response) {
      return response.json();
    }).then(function (body) {
      if (body.error) {
        toast(t("dashboard.saveFailed"));
      } else if (!silent) {
        toast(t("dashboard.saved"));
      }
    }).catch(function () {
      // 后端不可用时本地缓存已生效，提示降级原因
      if (!silent) {
        toast(t("dashboard.saveLocalOnly"));
      }
    });
  }

  /** 重命名当前仪表板（R3-02）。
   * @returns {void}
   */
  function renameDashboard() {
    var dash = activeDashboard();
    if (!dash || !window.UI) {
      return;
    }
    window.UI.modal({
      title: t("dashboard.rename"),
      input: true,
      defaultValue: dash.name,
      /** 确认重命名：空名保持原名，成功后落盘并重绘。
       * @param {*} value 输入框内容
       */
      onConfirm: function (value) {
        var name = String(value || "").trim();
        if (!name) {
          return;
        }
        dash.name = name;
        saveState(true);
        render();
      }
    });
  }

  /** 复制当前仪表板（含全部组件，名称追加「副本」）（R3-02）。
   * @returns {void}
   */
  function duplicateDashboard() {
    var dash = activeDashboard();
    if (!dash) {
      return;
    }
    var copy = {
      id: "dash-" + Date.now(),
      name: dash.name + t("dashboard.copySuffix"),
      group: dash.group || "",
      widgets: dash.widgets.map(function (item) {
        // 复制时必须带上 span/h/params，否则副本丢失尺寸与改参数结果
        var copyItem = { id: item.id, span: item.span };
        if (item.h) { copyItem.h = item.h; }
        if (item.params) { copyItem.params = Object.assign({}, item.params); }
        return copyItem;
      })
    };
    state.dashboards.push(copy);
    state.activeId = copy.id;
    saveState(true);
    render();
  }

  /** 把当前仪表板移入/新建分组（R3-02）。
   * @returns {void}
   */
  function groupDashboard() {
    var dash = activeDashboard();
    if (!dash || !window.UI) {
      return;
    }
    window.UI.modal({
      title: t("dashboard.groupTitle"),
      message: t("dashboard.groupHint"),
      input: true,
      defaultValue: dash.group || "",
      /** 确认分组：留空即移出分组。
       * @param {*} value 分组名
       */
      onConfirm: function (value) {
        dash.group = String(value || "").trim();
        saveState(true);
        render();
      }
    });
  }

  /** 删除当前仪表板（含组件时二次确认）（R3-02）。
   * @returns {void}
   */
  function deleteDashboard() {
    var dash = activeDashboard();
    if (!dash || !window.UI) {
      return;
    }
    var isLast = state.dashboards.length <= 1;
    window.UI.modal({
      title: t("dashboard.remove"),
      message: isLast
        ? (dash.widgets.length
            ? t("dashboard.deleteLastWarnWidgets", { name: dash.name, count: dash.widgets.length })
            : t("dashboard.deleteLastWarn", { name: dash.name }))
        : (dash.widgets.length
            ? t("dashboard.deleteWarnWidgets", { name: dash.name, count: dash.widgets.length })
            : t("dashboard.deleteWarn", { name: dash.name })),
      confirmText: t("action.delete"),
      /** 确认删除：移除后切到第一个仪表板并落盘。
       * @returns {void} 无返回值
       */
      onConfirm: function () {
        state.dashboards = state.dashboards.filter(function (item) {
          return item.id !== dash.id;
        });
        // 删光时不能取 dashboards[0].id（会 TypeError），置空走「无仪表板」引导态
        state.activeId = state.dashboards.length ? state.dashboards[0].id : "";
        saveState(true);
        render();
      }
    });
  }

  /** 调整仪表板顺序（R3-02 排序）。
   * @param {number} delta 位移量（-1 前移 / +1 后移）
   * @returns {void}
   */
  function moveDashboard(delta) {
    var index = -1;
    for (var i = 0; i < state.dashboards.length; i += 1) {
      if (state.dashboards[i].id === state.activeId) {
        index = i;
        break;
      }
    }
    var target = index + delta;
    if (index < 0 || target < 0 || target >= state.dashboards.length) {
      return;
    }
    var moved = state.dashboards.splice(index, 1)[0];
    state.dashboards.splice(target, 0, moved);
    saveState(true);
    render();
  }

  /** 默认仪表板（全部使用真实接口，无演示数据）。
   * P2-01：不再硬编码 688012 等个股标的；默认为市场总览类组件，
   * 用户可从「组件库」按需添加个股组件（自动带入当前标的）。
   * @returns {Object} 处理结果
   */
  function buildDefaultDashboard() {
    return {
      id: "dash-" + Date.now(),
      name: t("dashboard.defaultName"),
      widgets: [
        { id: "S-03", span: 28 },
        { id: "S-06", span: 20 },
        { id: "S-04", span: 28 },
        { id: "N-03", span: 20 },
        { id: "N-02", span: 48 }
      ]
    };
  }

  /** 当前仪表板。
   * @returns {*} 处理结果
   */
  function activeDashboard() {
    for (var i = 0; i < state.dashboards.length; i += 1) {
      if (state.dashboards[i].id === state.activeId) {
        return state.dashboards[i];
      }
    }
    state.activeId = state.dashboards[0].id;
    return state.dashboards[0];
  }

  /** 拉取注册表（35 个组件元数据）。
   * @returns {*} 处理结果
   */
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
    // P2-01：ST 系列不再硬编码 symbol，改为由 addWidgetById 透传当前标的（个股页/Ctrl+K）
    "ST-01": { kind: "panel", path: "/api/stock/realtime-panel", params: {}, pick: "quote" },
    "ST-02": { kind: "chart", path: "/api/stock/realtime-panel", params: {}, pick: "kline", x: "date", y: "close" },
    "ST-04": { kind: "metrics", path: "/api/stock/realtime-panel", params: {}, pick: "fund_dist" },
    "S-03": { kind: "chart", path: "/api/sector/heatmap", params: { market: "a", kind: "industry" }, pick: "nodes", x: "name", y: "pct", bar: true },
    "S-04": { kind: "table", path: "/api/sector/industry", params: { market: "a" }, cols: ["name", "pct", "amount", "lead_stock"] },
    "S-05": { kind: "table", path: "/api/sector/concept", params: { market: "a" }, cols: ["name", "pct", "amount", "lead_stock"] },
    "S-06": { kind: "metrics", path: "/api/sector/summary", params: { market: "a" }, pick: "gainers" },
    "S-07": { kind: "table", path: "/api/sector/fundflow", params: { market: "a" }, cols: ["name", "net_inflow", "inflow", "outflow"] },
    "N-02": { kind: "news", path: "/api/news/ai-feed", params: { limit: 12, sort: "relevance" } },
    "N-03": { kind: "metrics", path: "/api/sentiment/market", params: { region: "cn" }, pick: "self" },
    "F-03": { kind: "table", path: "/api/fund/performance", params: { code: "017811" }, cols: ["period", "growth", "rank"] },
    "F-04": { kind: "table", path: "/api/fund/holding", params: { code: "017811" }, cols: ["code", "name", "ratio", "value"] },
    "P-01": { kind: "metrics", path: "/api/portfolio/analysis", params: {}, pick: "totals" },
    "P-03": { kind: "table", path: "/api/portfolio/analysis", params: {}, cols: ["market_label", "symbol", "name", "market_value", "pnl", "pnl_pct"] },
    "V-01": { kind: "chart", path: "/api/sector/kline", params: { board: t("dashboard.defaultBoard"), market: "a" }, pick: "items", x: "date", y: "close" },
    // 养基宝（需已绑定；未绑定时后端返回 UPSTREAM_ERROR，卡片走错误态）
    "Y-05": { kind: "yjbCollect", path: "/api/fund/yjb/collect", params: {} },
    "Y-06": { kind: "table", path: "/api/fund/yjb/holdings", params: { account_id: "all" },
              cols: ["code", "name", "pct", "money", "hold_earn", "account_title"] }
  };

  /** 表头本地化映射（字段名 → i18n key）。 */
  var HEADER_KEYS = {
    name: "th.name", code: "th.code", symbol: "th.code", pct: "th.pct", amount: "th.amount",
    lead_stock: "th.leadStock", lead_pct: "th.leadPct", net_inflow: "th.netInflow",
    inflow: "th.inflow", outflow: "th.outflow", super_big: "th.superBig", big: "th.big",
    period: "th.period", growth: "th.growth", rank: "th.rank", ratio: "th.weight",
    value: "th.value", price: "th.price", market_value: "th.value", pnl: "th.pnl",
    pnl_pct: "th.pnlPct", market_label: "th.market", time: "th.time", title: t("th.title")
  };

  /** 取单元格显示值（数值走本地化）。
   * @param {*} value 待格式化或处理的值
   * @param {string} field 字段名
   * @returns {*} 处理结果
   */
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

  /** 带涨跌色的单元格。
   * @param {*} value 待格式化或处理的值
   * @param {string} field 字段名
   * @returns {*} 处理结果
   */
  function cellNode(value, field) {
    var node = el("td", "num", cellText(value, field));
    if (typeof value === "number" && (field.indexOf("pct") >= 0 || field === "pnl" || field === "growth" || field === "net_inflow")) {
      node.className = "num " + (value > 0 ? "up" : value < 0 ? "down" : "flat");
    }
    return node;
  }

  /** 统一请求（中文错误）。
   * @param {string} path 接口路径
   * @param {Object} params 查询参数对象
   * @returns {*} 处理结果
   */
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

  /** 渲染表格（可排序 + 可导出 CSV）。
   * @param {HTMLElement} container DOM 容器节点
   * @param {Array} rows 记录行列表
   * @param {*} cols 列定义（字段名数组）
   * @param {Object} widget 组件元数据（id/title/span）
   * @returns {*} 处理结果
   */
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

    /** 生成一行 tr：按列顺序取值并交给 cellNode 做类型化渲染。
     * @param {*} row 单行记录
     * @param {Array} columns 列定义（字段名数组）
     * @returns {*} 处理结果
     */
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

  /** 指标键 → i18n 键（R2-07）。
   * renderMetrics 不得把接口对象键名直接当标签，否则 scope/total/mean_score
   * 等英文键会裸渲染到界面；未登记且非中文的键一律不展示。
   */
  var METRIC_LABELS = {
    total: { key: "news.senti.sample", kind: "int" },
    positive: { key: "stock.sentiment.up", kind: "int" },
    negative: { key: "stock.sentiment.down", kind: "int" },
    neutral: { key: "stock.sentiment.neutral", kind: "int" },
    mean_score: { key: "news.senti.meanScore", kind: "num" },
    temperature: { key: "news.senti.temperature", kind: "num" },
    level: { key: "news.senti.level", kind: "text" },
    count: { key: "portfolio.kv.count", kind: "int" },
    cny_market_value: { key: "portfolio.kv.valueCny", kind: "num" },
    cny_cost: { key: "portfolio.kv.costCny", kind: "num" },
    cny_pnl: { key: "portfolio.kv.pnlCny", kind: "num" },
    fx: { key: "portfolio.kv.fx", kind: "text" }
  };

  /** 渲染指标卡。
   * @param {HTMLElement} container DOM 容器节点
   * @param {Object} data 数据主体（接口 data 段）
   * @param {Object} widget 组件元数据（id/title/span）
   */
  function renderMetrics(container, data, widget) {
    var entries = [];
    if (Array.isArray(data)) {
      data.slice(0, 6).forEach(function (item, index) {
        entries.push([item.name || t("dashboard.metricDefault") + (index + 1), item.pct !== undefined ? item.pct : item.net_inflow, item.pct !== undefined ? "pct" : "num"]);
      });
    } else if (data && typeof data === "object") {
      // 对象型指标（N-03 情绪温度、P-01 持仓汇总）：键名走 i18n 映射；
      // 未登记的英文键直接跳过，中文键（如板块名）原样展示
      Object.keys(data).slice(0, 8).forEach(function (key) {
        var value = data[key];
        if (!(typeof value === "number" || typeof value === "string")) {
          return;
        }
        var rule = METRIC_LABELS[key];
        var label = rule ? t(rule.key) : (/[\u4e00-\u9fff]/.test(key) ? key : "");
        if (!label) {
          return;
        }
        entries.push([label, value, rule ? rule.kind : (typeof value === "number" ? "num" : "text")]);
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
      var text = entry[2] === "pct" ? L().pct(entry[1])
        : (entry[2] === "int" ? L().num(entry[1], 0)
          : (entry[2] === "num" ? L().num(entry[1], 2) : entry[1]));
      var cls = entry[2] === "pct" && typeof entry[1] === "number" ? (entry[1] > 0 ? "up" : entry[1] < 0 ? "down" : "flat") : "";
      cell.appendChild(el("div", "pg-metric-value " + cls, text));
      grid.appendChild(cell);
    });
    container.appendChild(grid);
  }

  /** 渲染折线/柱状图（ECharts 本地库）。
   * @param {HTMLElement} container DOM 容器节点
   * @param {Array} items 记录列表
   * @param {Object} recipe 渲染配方（kind/path/cols）
   * @returns {Object} 处理结果
   */
  function renderChart(container, items, recipe) {
    if (!items || !items.length) {
      container.appendChild(el("div", "pg-state", t("state.empty")));
      return;
    }
    var box = el("div", "pg-chart");
    container.appendChild(box);
    var chart = window.echarts ? window.echarts.init(box) : null;
    if (!chart) {
      container.appendChild(el("div", "pg-state", t("dashboard.chartNotFound")));
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

  /** 渲染资讯流（真实新闻 + 本地情绪标签）。
   * @param {HTMLElement} container DOM 容器节点
   * @param {Array} items 记录列表
   */
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
      var label = sentiment.label || "";
      var chipClass = label === t("dashboard.sentiment.up") ? "chip up"
        : label === t("dashboard.sentiment.down") ? "chip down" : "chip";
      head.appendChild(el("span", chipClass, label || t("dashboard.sentiment.neutral")));
      head.appendChild(el("span", "pg-feed-score", L().num(sentiment.score, 4)));
      head.appendChild(el("span", "pg-spacer"));
      head.appendChild(el("span", "pg-news-time", L().datetime(item.time)));
      row.appendChild(head);
      row.appendChild(el("div", "pg-feed-title", item.title || ""));
      list.appendChild(row);
    });
    container.appendChild(list);
  }

  /** 分组渲染：表格 / 图表 / 指标 / 资讯 / 面板。
   * @param {HTMLElement} body 区块容器（.pg-body，四态 data-state 挂在此节点）
   * @param {Object} data 数据主体（接口 data 段）
   * @param {Object} recipe 渲染配方（kind/path/cols）
   * @param {Object} widget 组件元数据（id/title/span）
   */
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
    if (recipe.kind === "yjbCollect") {
      // 养基宝账户概览：KPI 数字 + 账户小卡（与 Y-05 区块同构）
      var accounts = data.accounts || [];
      if (!data.total_assets && !accounts.length) {
        body.appendChild(el("div", "pg-state", t("yjb.notBoundHint")));
        body.setAttribute("data-state", "empty");
        return;
      }
      var kpis = el("div", "pg-kpis");
      [[t("yjb.kpi.totalAssets"), fmt(data.total_assets, 2), "flat"],
       [t("yjb.kpi.todayIncome"), (data.today_income > 0 ? "+" : "") + fmt(data.today_income, 2), pctClass(data.today_income)],
       [t("yjb.kpi.todayRate"), fmtPct(data.today_income_rate), pctClass(data.today_income_rate)],
       [t("yjb.kpi.holdIncome"), (data.hold_income > 0 ? "+" : "") + fmt(data.hold_income, 2), pctClass(data.hold_income)]]
        .forEach(function (row) {
          var box = el("div", "pg-kpi");
          box.appendChild(el("div", "pg-kpi-k", row[0]));
          box.appendChild(el("div", "pg-kpi-v " + row[2], row[1]));
          kpis.appendChild(box);
        });
      body.appendChild(kpis);
      if (accounts.length) {
        var list = el("div", "pg-kpi-accounts");
        accounts.forEach(function (acc) {
          var box = el("div", "pg-kpi-acc");
          var head = el("div", "pg-kpi-acc-head");
          head.appendChild(el("span", "pg-kpi-acc-name", acc.title || acc.id));
          head.appendChild(el("span", "pg-kpi-acc-id", acc.up + "↑/" + acc.down + "↓"));
          box.appendChild(head);
          var line = el("div", "pg-kpi-acc-row");
          line.appendChild(el("span", "", t("yjb.kpi.todayIncome")));
          line.appendChild(el("b", pctClass(acc.today_income),
            (acc.today_income > 0 ? "+" : "") + fmt(acc.today_income, 2)));
          box.appendChild(line);
          var line2 = el("div", "pg-kpi-acc-row");
          line2.appendChild(el("span", "", t("yjb.kpi.todayRate")));
          line2.appendChild(el("b", pctClass(acc.today_income_rate), fmtPct(acc.today_income_rate)));
          box.appendChild(line2);
          list.appendChild(box);
        });
        body.appendChild(list);
      }
      body.setAttribute("data-state", "default");
      return;
    }
    if (recipe.kind === "panel") {
      var quote = data[recipe.pick] || {};
      var grid = el("div", "pg-metric-grid");
      [[t("dashboard.panel.name"), quote.name], [t("dashboard.panel.price"), L().price(quote.price, "a")], [t("dashboard.panel.pct"), L().pct(quote.pct)],
       [t("dashboard.panel.open"), L().price(quote.open, "a")], [t("dashboard.panel.high"), L().price(quote.high, "a")], [t("dashboard.panel.low"), L().price(quote.low, "a")]]
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

  /** 卡片「改参数」弹窗（R3-11）：编辑标的/周期等配方参数并持久化。
   * @param {Object} entry 组件条目（含 params）
   * @param {Array} keys 需要编辑的参数键
   * @returns {void} 无返回值
   */
  function editWidgetParams(entry, keys) {
    var meta = (state.registry && state.registry[entry.id]) || { id: entry.id, title: entry.id };
    var current = entry.params || {};
    var recipeDef = RECIPES[entry.id] || {};
    var merged = Object.assign({}, recipeDef.params || {}, current);
    var labels = {
      symbol: t("dashboard.param.symbol"),
      market: t("dashboard.param.market"),
      period: t("dashboard.param.period"),
      code: t("dashboard.param.code"),
      board: t("dashboard.param.board"),
      market_type: t("dashboard.param.marketType"),
      kind: t("dashboard.param.kind"),
      sort: t("dashboard.param.sort"),
      limit: t("dashboard.param.limit")
    };
    window.UI.modal({
      title: t("dashboard.editParams") + " · " + (meta.title || entry.id),
      message: t("dashboard.editParamsHint"),
      fields: keys.map(function (key) {
        return { key: key, label: labels[key] || key, value: merged[key], mono: true };
      }),
      /** 确认改参数：空值表示删除该参数（回落到配方默认）。
       * @param {Object} values 字段键值
       */
      onConfirm: function (values) {
        var next = Object.assign({}, current);
        Object.keys(values).forEach(function (key) {
          if (values[key] === "") {
            delete next[key];
          } else {
            next[key] = values[key];
          }
        });
        entry.params = Object.keys(next).length ? next : undefined;
        saveState(true);
        render();
      }
    });
  }

  /** 构建一张组件卡（含拖拽、宽度调整、移除、CSV）。
   * @param {*} entry 列表项 / 组件条目
   * @param {number} index 序号（从 0 开始）
   * @returns {*} 处理结果
   */
  function buildWidgetCard(entry, index) {
    var meta = (state.registry && state.registry[entry.id]) || { id: entry.id, title: entry.id, desc: "" };
    var card = el("section", "pg-card dash-widget");
    card.style.gridColumn = "span " + entry.span;
    // R3-11：自定义高度（拖拽下边缘后持久化）
    if (entry.h) {
      card.style.minHeight = entry.h + "px";
    }
    card.setAttribute("draggable", "true");
    card.dataset.index = String(index);
    card.dataset.widgetId = entry.id;
    // UI-02：区块编号只作 DOM 定位用（加 `dash-widget-` 前缀，避免与各页面里的 S-03/ST-01 同 id 冲突）
    card.id = "dash-widget-" + entry.id;

    var head = el("div", "pg-card-head");
    head.appendChild(el("span", "pg-card-title", meta.title));
    // UI-02：区块编号（S-03/N-03 等）只作 HTML id，不再作为副标题渲染到界面
    head.appendChild(el("span", "pg-spacer"));

    // 改参数（R3-11）：按配方声明的参数键生成弹窗（标的 symbol / 周期 period 等）
    var recipeDef = RECIPES[entry.id];
    var paramKeys = recipeDef ? Object.keys(recipeDef.params || {}).concat(Object.keys(entry.params || {})) : [];
    var uniqueKeys = paramKeys.filter(function (key, position) { return paramKeys.indexOf(key) === position; });
    if (uniqueKeys.length) {
      var tune = el("button", "pg-btn dash-btn", t("dashboard.editParams"));
      tune.type = "button";
      tune.title = t("dashboard.editParamsHint");
      tune.addEventListener("click", function (event) {
        event.stopPropagation();
        editWidgetParams(entry, uniqueKeys);
      });
      head.appendChild(tune);
    }

    var remove = el("button", "pg-btn dash-btn", t("action.close"));
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

    // R3-11：右下角拖拽手柄 —— 自由拉伸宽度（吸附 48 列，8~48 约束）
    var widthHandle = el("div", "dash-resize-handle");
    widthHandle.title = t("dashboard.dragResize");
    // D-11：手柄是无指针设备的键盘入口（左右方向键 ±4 列，Home 回到 48 列）
    widthHandle.setAttribute("tabindex", "0");
    widthHandle.setAttribute("role", "slider");
    widthHandle.setAttribute("aria-label", t("dashboard.dragResize"));
    widthHandle.addEventListener("keydown", function (event) {
      var step = 0;
      if (event.key === "ArrowLeft") {
        step = -4;
      } else if (event.key === "ArrowRight") {
        step = 4;
      } else if (event.key === "Home") {
        step = 48 - entry.span;
      }
      if (!step) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      entry.span = Math.max(8, Math.min(48, entry.span + step));
      card.style.gridColumn = "span " + entry.span;
      saveState(true);
    });
    widthHandle.addEventListener("pointerdown", function (event) {
      event.preventDefault();
      event.stopPropagation();
      var grid = card.parentElement;
      if (!grid) {
        return;
      }
      var colWidth = grid.getBoundingClientRect().width / 48;
      var startX = event.clientX;
      var startSpan = entry.span;
      widthHandle.setPointerCapture(event.pointerId);
      /** 拖动中实时改宽度：按 48 列栅格取整，限制 8~48 列。
       * @param {PointerEvent} moveEvent 指针移动事件
       * @returns {void} 无返回值
       */
      function onMove(moveEvent) {
        var delta = Math.round((moveEvent.clientX - startX) / colWidth);
        var next = Math.max(8, Math.min(48, startSpan + delta));
        entry.span = next;
        card.style.gridColumn = "span " + next;
      }
      /** 松手后落盘并解除监听。 */
      function onUp() {
        widthHandle.removeEventListener("pointermove", onMove);
        widthHandle.removeEventListener("pointerup", onUp);
        saveState(true);
      }
      widthHandle.addEventListener("pointermove", onMove);
      widthHandle.addEventListener("pointerup", onUp);
    });
    card.appendChild(widthHandle);

    // R3-11：下边缘手柄 —— 调整卡片高度（120~900px）
    var heightHandle = el("div", "dash-height-handle");
    heightHandle.title = t("dashboard.dragHeight");
    heightHandle.addEventListener("pointerdown", function (event) {
      event.preventDefault();
      event.stopPropagation();
      var startY = event.clientY;
      var startHeight = card.getBoundingClientRect().height;
      heightHandle.setPointerCapture(event.pointerId);
      /** 拖动中实时改高度。
       * @param {PointerEvent} moveEvent 指针移动事件
       * @returns {void} 无返回值
       */
      function onMove(moveEvent) {
        var next = Math.max(120, Math.min(900, Math.round(startHeight + (moveEvent.clientY - startY))));
        entry.h = next;
        card.style.minHeight = next + "px";
      }
      /** 松手后落盘。 */
      function onUp() {
        heightHandle.removeEventListener("pointermove", onMove);
        heightHandle.removeEventListener("pointerup", onUp);
        saveState(true);
      }
      heightHandle.addEventListener("pointermove", onMove);
      heightHandle.addEventListener("pointerup", onUp);
    });
    card.appendChild(heightHandle);

    return card;
  }

  /** 加载单个组件数据（失败就地显示错误 + 重试）。
   * @param {HTMLElement} body 区块容器（.pg-body，四态 data-state 挂在此节点）
   * @param {HTMLElement} card 卡片 DOM 节点
   * @param {*} entry 列表项 / 组件条目
   * @param {Object} meta 元数据对象
   */
  function loadWidget(body, card, entry, meta) {
    body.innerHTML = "";
    // 四态规范（§7.9）：显式标记 loading，巡检与 wait_blocks 才能等到收口
    body.setAttribute("data-state", "loading");
    body.appendChild(el("div", "pg-state", t("state.loading")));
    var recipe = RECIPES[entry.id];
    if (!recipe) {
      body.innerHTML = "";
      var hint = el("div", "pg-state");
      hint.appendChild(el("div", "pg-state-title", meta.desc || meta.title));
      if (meta.api && meta.api.length) {
        hint.appendChild(el("div", "pg-state-hint", t("dashboard.apiPrefix") + meta.api.join("、") + "（" + t("dashboard.noRenderer") + "）"));
      }
      body.appendChild(hint);
      // 无内置渲染器的组件属「如实说明」而非加载中，收口为默认态
      body.setAttribute("data-state", "default");
      return;
    }
    // P2-01：合并配方默认参数与用户传入参数（ST 系列不再硬编码 symbol）
    var params = Object.assign({}, recipe.params || {}, entry.params || {});
    // 股票类组件需 symbol 参数，否则进入空态引导（由命令面板或组件库带入当前标的）
    if (recipe.path.indexOf("/api/stock/realtime-panel") === 0 && !params.symbol) {
      body.innerHTML = "";
      var hint = el("div", "pg-state");
      hint.appendChild(el("div", "pg-state-title", t("dashboard.stockEmptyTitle")));
      hint.appendChild(el("div", "pg-state-hint", t("dashboard.stockEmptyHint")));
      var libBtn = el("button", "pg-btn", t("dashboard.library"));
      libBtn.type = "button";
      libBtn.addEventListener("click", function () { openLibrary(); });
      hint.appendChild(libBtn);
      body.appendChild(hint);
      // 无数据非加载中，收口为默认态
      body.setAttribute("data-state", "default");
      return;
    }
    fetchJson(recipe.path, params)
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
  /** 「无仪表板」引导态（四态规范：无 / 空 / 加载 / 错误）。
   * @param {HTMLElement} container 页面容器
   * @returns {void} 无返回值
   */
  function renderNoDashboard(container) {
    var box = el("div", "pg-state");
    box.setAttribute("data-state", "empty");
    box.appendChild(el("div", "pg-state-title", t("dashboard.noneTitle")));
    box.appendChild(el("div", "pg-state-hint", t("dashboard.noneHint")));
    var row = el("div", "page-toolbar");
    var create = el("button", "pg-btn primary", "+ " + t("dashboard.new"));
    create.type = "button";
    create.addEventListener("click", openCreateDashboard);
    row.appendChild(create);
    box.appendChild(row);
    container.appendChild(box);
  }

  /** 渲染仪表板编辑画布：工具栏（新建/管理/组件库/模板/保存）+ 48 列组件网格。
   * @returns {void} 无返回值（异步渲染，registry 就绪后挂载 DOM）
   */
  function render() {
    var container = document.getElementById("page-dashboard");
    if (!container) {
      return;
    }
    loadRegistry().then(function () {
      // R3-02：侧栏分组树与页内内容同步刷新
      renderSidebar();
      container.innerHTML = "";
      // 四态之一：一个仪表板都没有（用户删光或首次空仓）→ 引导态，不自动造假数据
      if (!state.dashboards.length) {
        renderNoDashboard(container);
        return;
      }
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
      addDash.addEventListener("click", openCreateDashboard);
      toolbar.appendChild(addDash);

      // R3-02：当前仪表板的重命名/复制/分组/删除/排序（原位管理，不离开当前页）
      var manage = [
        { label: t("dashboard.rename"), run: renameDashboard },
        { label: t("dashboard.duplicate"), run: duplicateDashboard },
        { label: t("dashboard.groupTitle"), run: groupDashboard },
        { label: t("dashboard.moveLeft"), run: function () { moveDashboard(-1); } },
        { label: t("dashboard.moveRight"), run: function () { moveDashboard(1); } },
        { label: t("dashboard.remove"), run: deleteDashboard }
      ];
      manage.forEach(function (action) {
        var button = el("button", "pg-btn", action.label);
        button.type = "button";
        button.addEventListener("click", action.run);
        toolbar.appendChild(button);
      });

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
      // R3-12：接收组件库拖入的组件（自定义 MIME，与卡片换位拖拽互不干扰）
      bindCanvasDrop(grid);
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

  /** 打开「新建仪表板」输入弹窗：工具栏按钮与侧栏入口共用同一实现。
   * @returns {void}
   */
  function openCreateDashboard() {
    if (!window.UI) {
      return;
    }
    window.UI.modal({
      title: t("dashboard.new"),
      input: true,
      defaultValue: t("dashboard.defaultName"),
      /** 新建仪表板确认：空名回落默认名，创建后切为当前并重绘。
       * @param {*} value 待格式化或处理的值
       */
      onConfirm: function (value) {
        var name = value || t("dashboard.defaultName");
        var dash = { id: "dash-" + Date.now(), name: name, widgets: [] };
        state.dashboards.push(dash);
        state.activeId = dash.id;
        saveState(true);
        render();
      }
    });
  }

  /** 组件库抽屉。 */
  /** 组件库分组标题：把注册表里的路由（/#sectors）翻成中文视图名。
   * 缺键或未知路由时退回原始路由，避免出现英文/键名泄漏。
   * @param {string} page 注册表里的页面标识（形如 /#sectors）
   * @returns {string} 分组标题
   */
  function libraryGroupLabel(page) {
    var map = {
      sectors: "nav.sectors",
      stock: "nav.stock",
      news: "nav.news",
      fund: "nav.fund",
      portfolio: "nav.portfolio",
      "sector-val": "nav.sectorVal",
      dashboard: "nav.dashboard",
      home: "nav.home",
      yjb: "nav.yjb"
    };
    var name = String(page || "").replace(/^\/?#\/?/, "").split("/")[0];
    var key = map[name];
    var text = key ? t(key) : "";
    return text && text !== key ? text : String(page || "");
  }

  /** 组件库分类标签（6 类能力分类；未知分类回退到页面名，再回退到编号）。
   * @param {Object} item 注册表条目（含 category / page）
   * @returns {string} 中文分类名
   */
  function libraryCategoryLabel(item) {
    var cat = (item && item.category) || "";
    if (cat) {
      var key = "dashboard.libCat." + cat;
      var text = t(key);
      if (text && text !== key) {
        return text;
      }
    }
    return libraryGroupLabel(item && item.page);
  }

  /** 组件缩略预览：按配方渲染类型生成迷你示意（R3-12 L-02）。
   * @param {string} widgetId 组件编号
   * @returns {HTMLElement} 缩略预览节点（高 44px 的迷你图形）
   */
  function libraryThumb(widgetId) {
    var box = el("div", "dash-lib-thumb");
    var recipe = RECIPES[widgetId] || {};
    var kind = recipe.kind || "panel";
    if (kind === "chart") {
      // 迷你折线 + 成交量柱
      var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("viewBox", "0 0 120 44");
      svg.setAttribute("preserveAspectRatio", "none");
      svg.classList.add("dash-lib-thumb-svg");
      var line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
      line.setAttribute("points", "0,30 15,22 30,26 45,14 60,18 75,8 90,12 105,5 120,9");
      line.setAttribute("fill", "none");
      line.setAttribute("stroke", "var(--accent)");
      line.setAttribute("stroke-width", "1.6");
      svg.appendChild(line);
      [6, 18, 30, 42, 54, 66, 78, 90, 102, 114].forEach(function (x, position) {
        var bar = document.createElementNS("http://www.w3.org/2000/svg", "rect");
        var height = 4 + ((position * 7) % 11);
        bar.setAttribute("x", String(x));
        bar.setAttribute("y", String(44 - height));
        bar.setAttribute("width", "5");
        bar.setAttribute("height", String(height));
        bar.setAttribute("fill", "var(--border)");
        svg.appendChild(bar);
      });
      box.appendChild(svg);
    } else if (kind === "table") {
      // 迷你表格：三行四列
      for (var row = 0; row < 3; row += 1) {
        var lineBox = el("div", "dash-lib-thumb-row");
        for (var col = 0; col < 4; col += 1) {
          lineBox.appendChild(el("span", "dash-lib-thumb-cell" + (col === 0 ? " wide" : "")));
        }
        box.appendChild(lineBox);
      }
    } else if (kind === "metrics" || kind === "yjbCollect") {
      // 迷你指标块：2×2 数字块
      for (var i = 0; i < 4; i += 1) {
        var cellBox = el("div", "dash-lib-thumb-metric");
        cellBox.appendChild(el("span", "dash-lib-thumb-num"));
        cellBox.appendChild(el("span", "dash-lib-thumb-bar"));
        box.appendChild(cellBox);
      }
    } else if (kind === "news") {
      // 迷你资讯：三条文字行
      for (var n = 0; n < 3; n += 1) {
        box.appendChild(el("div", "dash-lib-thumb-line" + (n === 2 ? " short" : "")));
      }
    } else {
      // 面板/未知类型：文本块示意
      box.appendChild(el("div", "dash-lib-thumb-block"));
      box.appendChild(el("div", "dash-lib-thumb-line short"));
    }
    return box;
  }

  /** L-03 组件说明表：全量注册表（编号/组件名/分类/默认跨列/绑定接口/能力说明）。
   * @returns {void} 无返回值（弹窗挂载 DOM）
   */
  function openLibraryTable() {
    var registry = state.registry || {};
    var ids = Object.keys(registry).sort();
    if (!window.UI) {
      return;
    }
    var overlay = el("div", "ui-overlay");
    var box = el("div", "ui-modal wide");
    box.appendChild(el("div", "ui-modal-title", t("dashboard.libTableTitle")));
    box.appendChild(el("div", "ui-modal-sub",
      t("dashboard.libTableCount", { n: ids.length })));
    var table = el("table", "pg-table");
    var head = document.createElement("tr");
    [t("dashboard.libTable.col.id"), t("dashboard.libTable.col.name"),
     t("dashboard.libTable.col.category"), t("dashboard.libTable.col.span"),
     t("dashboard.libTable.col.api"), t("dashboard.libTable.col.desc")]
      .forEach(function (label) {
        var th = document.createElement("th");
        th.textContent = label;
        head.appendChild(th);
      });
    table.appendChild(head);
    ids.forEach(function (id) {
      var item = registry[id] || {};
      var recipe = RECIPES[id] || {};
      var tr = document.createElement("tr");
      var cells = [
        { text: id, cls: "mono" },
        { text: item.title || id },
        { text: libraryCategoryLabel(item) },
        { text: String(item.span || "—") + t("dashboard.libTable.spanUnit") },
        { text: recipe.path || (item.api && item.api[0]) || "—", cls: "mono" },
        { text: item.desc || "—" }
      ];
      cells.forEach(function (cell) {
        var td = document.createElement("td");
        td.textContent = cell.text;
        if (cell.cls) {
          td.className = cell.cls;
        }
        tr.appendChild(td);
      });
      table.appendChild(tr);
    });
    box.appendChild(table);
    var closeRow = el("div", "page-toolbar");
    var closeBtn = el("button", "pg-btn primary", t("action.close"));
    closeBtn.type = "button";
    closeBtn.addEventListener("click", function () { overlay.remove(); });
    closeRow.appendChild(closeBtn);
    box.appendChild(closeRow);
    overlay.appendChild(box);
    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) {
        overlay.remove();
      }
    });
    document.body.appendChild(overlay);
  }

  /** 渲染组件库主体：搜索 + 分类 chips + 预览卡网格（DS-02）。
   *
   * 抽屉 `openLibrary` 与独立页 `#/widget-library` 共用本函数，
   * 保证两处组件清单、搜索、分类与拖拽行为完全一致（单一实现）。
   * @param {HTMLElement} host 宿主容器
   * @param {Object} opts 选项：{onAdded:Function(添加成功回调)}
   * @returns {void} 无返回值
   */
  function renderLibraryGrid(host, opts) {
    var options = opts || {};
    var registry = state.registry || {};
    // r9：按**能力分类**分组（设计基准 §5.4 L-01 的 6 类），不再按页面路由分组——
    // 页面分组会把 "#/watchlist" "#/lhb" 这类路由串直接漏成 chips 文案，且与冻结原型不符。
    var buckets = {};
    Object.keys(registry).forEach(function (id) {
      var item = registry[id];
      var cat = item.category || "table";
      buckets[cat] = buckets[cat] || [];
      buckets[cat].push(item);
    });
    var catOrder = ["market", "chart", "table", "news", "fin", "filter"];
    var catKeys = catOrder.filter(function (cat) { return (buckets[cat] || []).length > 0; });
    var activeCat = "__all__";

    // 搜索框
    var searchRow = el("div", "page-toolbar");
    var search = document.createElement("input");
    search.type = "search";
    search.placeholder = t("dashboard.librarySearch");
    search.setAttribute("aria-label", t("dashboard.librarySearch"));
    search.style.cssText =
      "flex:1;min-width:160px;background:var(--bg-panel);border:1px solid var(--border);" +
      "color:var(--text);border-radius:5px;padding:4px 8px";
    searchRow.appendChild(search);
    host.appendChild(searchRow);

    var filterRow = el("div", "page-toolbar dash-lib-filters");
    host.appendChild(filterRow);

    var listHost = el("div", "dash-lib-grid");
    listHost.id = "dash-lib-grid";
    host.appendChild(listHost);
    host.appendChild(el("div", "pg-card-sub", t("dashboard.libDragHint")));

    /** 按关键词与分类过滤后重绘预览卡网格（关键词匹配 id/标题/说明）。
     * @returns {void} 无返回值
     */
    function renderList() {
      var keyword = (search.value || "").trim().toLowerCase();
      listHost.innerHTML = "";
      var total = 0;
      catKeys.forEach(function (cat) {
        if (activeCat !== "__all__" && cat !== activeCat) {
          return;
        }
        var items = buckets[cat].filter(function (item) {
          if (!keyword) {
            return true;
          }
          var hay = (item.id + " " + item.title + " " + (item.desc || "")).toLowerCase();
          return hay.indexOf(keyword) >= 0;
        });
        if (!items.length) {
          return;
        }
        total += items.length;
        items.forEach(function (item) {
          // L-02 预览卡：缩略预览 + 编号 + 标题 + 能力说明 + 默认跨列 + 添加按钮，整卡可拖拽
          var card = el("div", "dash-lib-card");
          card.setAttribute("draggable", "true");
          card.dataset.widgetId = item.id;
          card.appendChild(libraryThumb(item.id));
          var info = el("div", "dash-lib-info");
          var titleRow = el("div", "dash-lib-title-row");
          titleRow.appendChild(el("span", "dash-lib-id", item.id));
          titleRow.appendChild(el("span", "dash-lib-title", item.title));
          info.appendChild(titleRow);
          if (item.desc) {
            info.appendChild(el("div", "dash-lib-desc", item.desc));
          }
          info.appendChild(el("div", "dash-lib-meta",
            t("dashboard.libSpan", { n: item.span || "—" })));
          var actions = el("div", "dash-lib-actions");
          var add = el("button", "pg-btn primary", t("action.add"));
          add.type = "button";
          add.addEventListener("click", function (event) {
            event.stopPropagation();
            addWidgetById(item.id);
            if (options.onAdded) {
              options.onAdded(item);
            }
            toast(t("dashboard.widgetAdded", { name: item.title || item.id }));
          });
          actions.appendChild(add);
          info.appendChild(actions);
          card.appendChild(info);

          // 拖拽入位：拖动到仪表板画布即完成添加（与画布 drop 监听配对）
          card.addEventListener("dragstart", function (event) {
            event.dataTransfer.setData("application/x-openbb-widget", item.id);
            event.dataTransfer.setData("text/plain", item.id);
            event.dataTransfer.effectAllowed = "copy";
            card.classList.add("dragging");
          });
          card.addEventListener("dragend", function () {
            card.classList.remove("dragging");
          });
          listHost.appendChild(card);
        });
      });
      if (!total) {
        listHost.appendChild(el("div", "pg-state", t("dashboard.emptyLibrary")));
      }
    }

    // 分类 chips：全部 + 6 个能力分类（设计基准 §5.4 L-01 → 共 7 个 chips）
    var allChip = el("button", "pg-tab active", t("dashboard.libraryAll"));
    allChip.type = "button";
    allChip.addEventListener("click", function () {
      activeCat = "__all__";
      filterRow.querySelectorAll(".pg-tab").forEach(function (node) {
        node.classList.toggle("active", node === allChip);
      });
      renderList();
    });
    filterRow.appendChild(allChip);
    catKeys.forEach(function (cat) {
      var chip = el("button", "pg-tab", libraryCategoryLabel({ category: cat }));
      chip.type = "button";
      chip.setAttribute("data-cat", cat);
      chip.addEventListener("click", function () {
        activeCat = cat;
        filterRow.querySelectorAll(".pg-tab").forEach(function (node) {
          node.classList.toggle("active", node === chip);
        });
        renderList();
      });
      filterRow.appendChild(chip);
    });

    search.addEventListener("input", renderList);
    renderList();
  }

  /** 组件库抽屉（保留原入口：仪表板页工具栏「组件库」按钮）。
   * @returns {void} 无返回值（直接挂载 DOM）
   */
  function openLibrary() {
    var overlay = el("div", "ui-overlay");
    var panel = el("aside", "dash-library");
    var head = el("div", "pg-card-head");
    head.appendChild(el("span", "pg-card-title", t("dashboard.library")));
    head.appendChild(el("span", "pg-spacer"));
    // L-03：全量组件说明表（与 /api/widgets 注册表实时一致）
    var tableBtn = el("button", "pg-btn", t("dashboard.libTable"));
    tableBtn.type = "button";
    tableBtn.addEventListener("click", openLibraryTable);
    head.appendChild(tableBtn);
    var close = el("button", "pg-btn", t("action.close"));
    close.type = "button";
    close.addEventListener("click", function () { overlay.remove(); });
    head.appendChild(close);
    panel.appendChild(head);

    // DS-02：与独立页共用同一渲染主体
    renderLibraryGrid(panel, { onAdded: function () { overlay.remove(); } });

    overlay.appendChild(panel);
    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) {
        overlay.remove();
      }
    });
    document.body.appendChild(overlay);
  }

  /** 独立路由 #/widget-library：组件库整页（DS-02，与抽屉同一实现）。
   * @param {HTMLElement} container 页面容器（#page-widget-library）
   */
  function renderLibraryPage(container) {
    loadState();
    container.innerHTML = "";
    var toolbar = el("div", "page-toolbar");
    toolbar.appendChild(el("span", "pg-card-title", t("dashboard.library")));
    toolbar.appendChild(el("span", "pg-spacer"));
    var tableBtn = el("button", "pg-btn", t("dashboard.libTable"));
    tableBtn.type = "button";
    tableBtn.addEventListener("click", openLibraryTable);
    toolbar.appendChild(tableBtn);
    var back = el("button", "pg-btn", t("dashboard.backToBoard"));
    back.type = "button";
    back.addEventListener("click", function () {
      location.hash = "#/dashboard";
    });
    toolbar.appendChild(back);
    container.appendChild(toolbar);

    var host = el("div", "dash-lib-page");
    container.appendChild(host);
    // DS-02 缺陷修复（r9）：此前只有仪表板画布 `render()` 会调 `loadRegistry()`，
    // 直接打开 `#/widget-library`（URL 直达/命令面板/侧栏）时 `state.registry` 仍是 null，
    // 整页显示「没有可添加的组件」——路由可达但功能为空。现显式 ensure 注册表，
    // 并按四态给「加载中 / 默认 / 错误」三态（空态由 renderLibraryGrid 内部处理）。
    container.setAttribute("data-state", "loading");
    host.appendChild(el("div", "pg-card-sub", t("state.loadingShort")));
    loadRegistry().then(function () {
      host.innerHTML = "";
      renderLibraryGrid(host, {});
      container.setAttribute("data-state", "default");
    }).catch(function () {
      host.innerHTML = "";
      var box = el("div", "pg-state");
      box.appendChild(el("div", "pg-state-title", t("dashboard.registryFailed")));
      var retry = el("button", "pg-btn", t("action.refresh"));
      retry.type = "button";
      retry.addEventListener("click", function () {
        renderLibraryPage(container);
      });
      box.appendChild(retry);
      host.appendChild(box);
      container.setAttribute("data-state", "error");
    });
  }

  /** 独立路由 #/dashboard-manage：仪表板管理整页（DS-01，7 操作 + 分组树）。
   * @param {HTMLElement} container 页面容器（#page-dashboard-manage）
   */
  function renderDashboardManage(container) {
    loadState();
    container.innerHTML = "";

    var toolbar = el("div", "page-toolbar");
    toolbar.appendChild(el("span", "pg-card-title", t("dashboard.manage")));
    toolbar.appendChild(el("span", "pg-spacer"));
    var addBtn = el("button", "pg-btn primary", "+ " + t("dashboard.new"));
    addBtn.type = "button";
    addBtn.addEventListener("click", openCreateDashboard);
    toolbar.appendChild(addBtn);
    var libBtn = el("button", "pg-btn", t("dashboard.library"));
    libBtn.type = "button";
    libBtn.addEventListener("click", function () {
      location.hash = "#/widget-library";
    });
    toolbar.appendChild(libBtn);
    container.appendChild(toolbar);

    // 四态之一：无仪表板
    if (!state.dashboards.length) {
      var empty = el("div", "pg-state");
      empty.appendChild(el("div", "pg-state-title", t("dashboard.emptyList")));
      empty.appendChild(el("div", "pg-state-hint", t("dashboard.emptyListHint")));
      var create = el("button", "pg-btn primary", "+ " + t("dashboard.new"));
      create.type = "button";
      create.addEventListener("click", openCreateDashboard);
      empty.appendChild(create);
      container.appendChild(empty);
      container.setAttribute("data-state", "empty");
      return;
    }

    var grid = el("div", "pg-grid");

    // 左栏：分组树（按 group 聚合；点击切换当前仪表板）
    var tree = buildCard("DM-01", t("dashboard.groups"), "g-4", "");
    var groups = {};
    state.dashboards.forEach(function (dash) {
      var group = dash.group || t("dashboard.ungrouped");
      groups[group] = groups[group] || [];
      groups[group].push(dash);
    });
    Object.keys(groups).forEach(function (group) {
      tree.body.appendChild(el("div", "sidebar-heading", group));
      groups[group].forEach(function (dash) {
        var item = el("div", "sidebar-item" + (dash.id === state.activeId ? " active" : ""), dash.name);
        item.addEventListener("click", function () {
          state.activeId = dash.id;
          saveState(true);
          renderDashboardManage(container);
        });
        tree.body.appendChild(item);
      });
    });
    tree.body.setAttribute("data-state", "default");
    grid.appendChild(tree.card);

    // 右栏：当前仪表板 + 7 操作 + 列表
    var main = buildCard("DM-02", t("dashboard.currentBoard"), "g-8",
      state.dashboards.length + " " + t("dashboard.countUnit"));
    var dash = activeDashboard();
    if (dash) {
      var ops = el("div", "page-toolbar");
      [
        { label: t("dashboard.rename"), run: renameDashboard },
        { label: t("dashboard.duplicate"), run: duplicateDashboard },
        { label: t("dashboard.groupTitle"), run: groupDashboard },
        { label: t("dashboard.moveLeft"), run: function () { moveDashboard(-1); } },
        { label: t("dashboard.moveRight"), run: function () { moveDashboard(1); } },
        { label: t("dashboard.remove"), run: deleteDashboard }
      ].forEach(function (action) {
        var button = el("button", "pg-btn", action.label);
        button.type = "button";
        button.addEventListener("click", function () {
          action.run();
          // 操作后在管理页内即时重绘，避免停留在旧快照
          window.setTimeout(function () {
            if (document.getElementById("page-dashboard-manage") &&
                !document.getElementById("page-dashboard-manage").hidden) {
              renderDashboardManage(container);
            }
          }, 120);
        });
        ops.appendChild(button);
      });
      main.body.appendChild(el("div", "pg-card-sub", t("dashboard.currentBoardHint")));
      main.body.appendChild(ops);
    }

    var columns = [
      { title: t("dashboard.colName"), render: function (row) { return row.name; } },
      { title: t("dashboard.colGroup"), render: function (row) { return row.group || t("dashboard.ungrouped"); } },
      { title: t("dashboard.colCount"), num: true, render: function (row) { return (row.widgets || []).length; } },
      {
        title: t("dashboard.colActive"),
        render: function (row) {
          return row.id === state.activeId ? t("dashboard.isActive") : "—";
        }
      }
    ];
    window.Utils.renderTable(main.body, columns, state.dashboards, {
      tableClass: "pg-table",
      wrapClass: "pg-table-wrap",
      onRowClick: function (row) {
        state.activeId = row.id;
        saveState(true);
        location.hash = "#/dashboard";
      }
    });
    main.body.setAttribute("data-state", "default");
    grid.appendChild(main.card);

    container.appendChild(grid);
    container.setAttribute("data-state", "default");
  }

  /** 绑定画布拖拽入位：接收组件库拖入的组件 id（R3-12 L-02）。
   * @param {HTMLElement} grid 仪表板画布网格节点
   * @returns {void} 无返回值
   */
  function bindCanvasDrop(grid) {
    grid.addEventListener("dragover", function (event) {
      // 只响应组件库拖入（自定义 MIME），避免与卡片换位拖拽互相干扰
      var types = event.dataTransfer ? Array.prototype.slice.call(event.dataTransfer.types) : [];
      if (types.indexOf("application/x-openbb-widget") < 0) {
        return;
      }
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
      grid.classList.add("dash-drop-active");
    });
    grid.addEventListener("dragleave", function () {
      grid.classList.remove("dash-drop-active");
    });
    grid.addEventListener("drop", function (event) {
      grid.classList.remove("dash-drop-active");
      var widgetId = event.dataTransfer ? event.dataTransfer.getData("application/x-openbb-widget") : "";
      if (!widgetId) {
        return;
      }
      event.preventDefault();
      addWidgetById(widgetId);
      var meta = (state.registry || {})[widgetId] || {};
      toast(t("dashboard.widgetAdded", { name: meta.title || widgetId }));
    });
  }

  /** 模板库：三个预设布局（全部真实接口组件）。
   * @returns {Object} 处理结果
   */
  function openTemplates() {
    if (!window.UI) {
      return;
    }
    var overlay = el("div", "ui-overlay");
    var box = el("div", "ui-modal");
    box.appendChild(el("div", "ui-modal-title", t("dashboard.templates")));
    var presets = [
      { name: t("dashboard.template.overview"), widgets: ["S-05", "S-03", "S-06", "S-04", "S-07"] },
      { name: t("dashboard.template.news"), widgets: ["N-02", "N-03", "S-03", "S-06"] },
      { name: t("dashboard.template.portfolio"), widgets: ["P-01", "P-03", "F-04", "F-03"] }
    ];
    presets.forEach(function (preset) {
      var button = el("button", "copilot-suggest", preset.name + t("dashboard.widgetCount", { n: preset.widgets.length }));
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

  /** 按 id 添加组件（供命令面板与组件库调用）。
   * @param {*} widgetId 组件 id
   * @param {Object} params 用户传入参数（如 symbol/market，P2-01 透传当前标的）
   */
  function addWidgetById(widgetId, params) {
    // 命令面板可能在未进入仪表板前调用，此时 state 尚未装载 → 先补 loadState，
    // 否则 activeDashboard() 取空数组会抛 TypeError（P2-01 相关）
    if (!state.dashboards.length) {
      loadState();
    }
    var dash = activeDashboard();
    if (dash.widgets.some(function (entry) { return entry.id === widgetId; })) {
      toast(t("dashboard.duplicateWidget"));
      return;
    }
    var meta = (state.registry && state.registry[widgetId]) || {};
    dash.widgets.push({ id: widgetId, span: meta.span || 24, params: params || {} });
    saveState(true);
    if (location.hash === "#/dashboard") {
      render();
    }
  }

  /** 渲染侧栏分组树（R3-02）：按 group 归组，未分组排最后。
   * 分组树与页内 tab 同步，任何增删改后都会刷新。
   * @returns {void} 无返回值
   */
  function renderSidebar() {
    var host = document.getElementById("sidebar-dashboards");
    if (!host) {
      return;
    }
    host.innerHTML = "";
    var groups = [];
    state.dashboards.forEach(function (dash) {
      var name = dash.group || "";
      if (groups.indexOf(name) < 0) {
        groups.push(name);
      }
    });
    // 具名分组在前（保持出现顺序），未分组固定排最后
    groups.sort(function (a, b) {
      if (a === b) {
        return 0;
      }
      if (!a) {
        return 1;
      }
      if (!b) {
        return -1;
      }
      return 0;
    });
    groups.forEach(function (group) {
      var heading = el("li", "sidebar-heading", group || t("sidebar.noGroup"));
      host.appendChild(heading);
      state.dashboards.forEach(function (dash) {
        if ((dash.group || "") !== group) {
          return;
        }
        var item = el("li", "sidebar-item" + (dash.id === state.activeId ? " active" : ""),
          dash.name + (dash.id === state.activeId ? t("sidebar.current") : ""));
        item.setAttribute("data-action", "dashboard");
        item.setAttribute("data-dash-id", dash.id);
        host.appendChild(item);
      });
    });
  }

  /** 切换当前仪表板（侧栏分组树点击时调用）。
   * @param {string} dashId 目标仪表板 id
   * @returns {void} 无返回值
   */
  function activate(dashId) {
    var exists = state.dashboards.some(function (dash) {
      return dash.id === dashId;
    });
    if (!exists) {
      return;
    }
    state.activeId = dashId;
    saveState(true);
    render();
  }

  window.Dashboard = {
    render: render,
    addWidgetById: addWidgetById,
    // 侧栏「新建仪表板 / 组件库」入口依赖这两个方法（P1-07），不导出会退化成只跳页不弹层
    createDashboard: openCreateDashboard,
    openLibrary: openLibrary,
    // DS-01/DS-02：独立路由视图（#/dashboard-manage、#/widget-library）由 pages.js 路由调用
    renderManage: renderDashboardManage,
    renderLibraryPage: renderLibraryPage,
    // R3-02：侧栏分组树点击切换 + 管理操作
    activate: activate,
    rename: renameDashboard,
    duplicate: duplicateDashboard,
    remove: deleteDashboard,
    /** 重载仪表板（重新读配置并重绘）。
     * @returns {void} 无返回值
     */
    reload: function () {
      loadState(); render();
    }
  };

  // 暴露初始化：pages.js 路由到 #/dashboard 时调用
  window.renderDashboard = function () {
    loadState();
    render();
  };

  // 时序兜底：pages.js 先于本文件执行，首次进入 #/dashboard 时 renderDashboard 尚未定义，
  // 故本文件加载完成后按当前 hash 补渲染一次（否则首屏空白，刷新后才出现）。
  // R3-02：侧栏分组树不依赖当前路由，初始化后立即渲染一次。
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      loadState();
      renderSidebar();
      if ((location.hash || "").indexOf("#/dashboard") === 0) {
        window.renderDashboard();
      }
    });
  } else {
    loadState();
    renderSidebar();
    if ((location.hash || "").indexOf("#/dashboard") === 0) {
      window.renderDashboard();
    }
  }
})();
