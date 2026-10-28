/* =============================================================
 * OpenBB 中文投研工作台 · 前端共享工具模块（P1-13 / P1-14）
 * 职责：
 *   1) 提取各 IIFE 内重复的工具函数到公共模块（el / t / toast /
 *      fmt / fmtPct / trendClass / api / renderTable），统一口径；
 *   2) 提供 K 线 ECharts option 脚手架构造函数（P1-14），供
 *      paintStockChart 与 paintChartingChart 复用。
 * 约束：不改变任何对外行为 —— 所有函数签名与返回值语义与原 IIFE 保持一致；
 *       仅在必要处统一空值显示为「无数据」（t("value.none")），
 *       统一 toast 超时为 2600ms（3/4 文件已一致），
 *       统一 api 的 URL 构造 + response.ok 检查 + 网络/解析错误中文化。
 * 加载顺序：必须在 i18n.js 之后、其它 IIFE 之前（defer 顺序已保证）。
 * ============================================================= */

(function () {
  "use strict";

  // ---------- i18n ----------

  /** 取翻译文本，支持 {name} 占位符。
   * @param {string} key 键名 / i18n key
   * @param {Object} vars 占位符变量表
   * @returns {*} 处理结果
   */
  function t(key, vars) {
    return window.I18N ? window.I18N.t(key, vars) : key;
  }

  // ---------- DOM ----------

  /** 创建元素：cls 为类名，text 为文本（一律用 textContent，避免注入）。
   * @param {string} tag HTML 标签名
   * @param {string} cls CSS 类名
   * @param {string} text 文本内容
   * @returns {*} 处理结果
   */
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

  // ---------- Toast ----------

  /** 轻提示：复用页面底部 #toast，2600ms 自动消失。
   * @param {string} message 提示文案（中文）
   */
  function toast(message) {
    var node = document.getElementById("toast");
    if (!node) {
      return;
    }
    node.textContent = message;
    node.classList.add("show");
    window.clearTimeout(toast._timer);
    toast._timer = window.setTimeout(function () {
      node.classList.remove("show");
    }, 2600);
  }

  // ---------- 数字格式化 ----------

  /** 数值格式化：空值返回「无数据」，千分位 + 指定位数（统一走 L10N）。
   * @param {*} value 待格式化或处理的值
   * @param {number} digits 小数位数
   * @returns {*} 处理结果
   */
  function fmt(value, digits) {
    if (window.L10N) {
      return window.L10N.num(value, digits);
    }
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
      return t("value.none");
    }
    var n = Number(value);
    if (isNaN(n)) {
      return t("value.none");
    }
    var fixed = n.toFixed(digits === undefined ? 2 : digits);
    var parts = fixed.split(".");
    parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    return parts.join(".");
  }

  /** 百分比格式化：带正负号，空值「无数据」（统一走 L10N）。
   * @param {*} value 待格式化或处理的值
   * @returns {*} 处理结果
   */
  function fmtPct(value) {
    if (window.L10N) {
      return window.L10N.pct(value);
    }
    if (value === null || value === undefined) {
      return t("value.none");
    }
    var n = Number(value);
    if (isNaN(n)) {
      return t("value.none");
    }
    return (n > 0 ? "+" : "") + n.toFixed(2) + "%";
  }

  /** 涨跌样式类：正红、负绿、零灰、NaN 灰。
   * @param {*} value 待格式化或处理的值
   * @returns {string} 处理结果
   */
  function trendClass(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "flat";
    if (Number(value) > 0) return "up";
    if (Number(value) < 0) return "down";
    return "flat";
  }

  // ---------- API ----------

  /** 统一请求：返回 data；失败抛出带中文文案的错误。
   * 合并 market.js（URL 构造 + response.ok 检查）与 pages.js
   * （网络错误/解析错误中文化 + data||{} 回退）的特性。
   * @param {string} path 接口路径
   * @param {Object} params 查询参数对象
   * @returns {*} 处理结果
   */
  async function api(path, params) {
    var url = new URL(path, window.location.origin);
    Object.keys(params || {}).forEach(function (key) {
      var value = params[key];
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, value);
      }
    });
    var response;
    try {
      response = await fetch(url.toString(), { cache: "no-store" });
    } catch (e) {
      throw new Error("网络连接失败，请检查网络后重试。");
    }
    if (!response.ok) {
      throw new Error("请求失败（HTTP " + response.status + "）");
    }
    var body;
    try {
      body = await response.json();
    } catch (e) {
      throw new Error("数据格式异常，请稍后重试。");
    }
    if (body && body.error) {
      var error = new Error(body.error);
      error.code = body.error_code;
      throw error;
    }
    return body.data || {};
  }

  // ---------- 表格 ----------

  /** 渲染数据表：统一表头/表体构建，支持列渲染、点击回调、空态。
   * 合并 market.js / pages.js 两版 renderTable，dashboard.js 因
   * 含排序+CSV 导出逻辑保持本地。
   * columns: [{key, title|label, render, num, trend, cls}]
   * @param {HTMLElement|string} container DOM 容器或其 id
   * @param {Array} columns 列定义
   * @param {Array} rows 记录行列表
   * @param {Object} options 可选项：{tableClass, wrapClass, maxHeightClass, onRowClick, nullText, emptyText,
   *   selectable: {key, selected, onChange}} —— 传入 selectable 时首列渲染勾选框，
   *   表头勾选框为全选/反选；onChange 回传当前选中的 key 数组（R6-09 / F6-09）。
   * @returns {*} 处理结果
   */
  function renderTable(container, columns, rows, options) {
    if (typeof container === "string") {
      container = document.getElementById(container);
    }
    if (!container) {
      return;
    }
    container.innerHTML = "";
    var opts = options || {};

    if (!rows || !rows.length) {
      if (opts.emptyText !== undefined) {
        container.textContent = opts.emptyText;
      }
      return;
    }

    var wrapClass = opts.wrapClass;
    var maxHeightClass = opts.maxHeightClass;
    var wrap = wrapClass
      ? el("div", wrapClass + (maxHeightClass ? " " + maxHeightClass : ""))
      : null;
    var target = wrap || container;

    // R6-09：可选行（仅自选股等需要批量操作的表格启用，未传时行为与旧版完全一致）
    var sel = opts.selectable || null;
    var selectedKeys = [];
    var rowKey = null;
    if (sel) {
      rowKey = typeof sel.key === "function"
        ? sel.key
        : function (row) { return String(row[sel.key]); };
      (sel.selected || []).forEach(function (item) { selectedKeys.push(String(item)); });
    }

    /** 触发选中集变化回调。
     * @returns {void} 无返回值
     */
    function emitSelection() {
      if (sel && typeof sel.onChange === "function") {
        sel.onChange(selectedKeys.slice());
      }
    }

    var table = el("table", opts.tableClass || "pg-table");
    var thead = el("thead");
    var headRow = el("tr");
    var headBox = null;
    if (sel) {
      var headCell = el("th", "col-check");
      headBox = document.createElement("input");
      headBox.type = "checkbox";
      headBox.setAttribute("aria-label", t("common.selectAll"));
      headBox.addEventListener("change", function () {
        selectedKeys = headBox.checked
          ? rows.map(function (row) { return String(rowKey(row)); })
          : [];
        tbody.querySelectorAll("input[data-row-key]").forEach(function (node) {
          node.checked = headBox.checked;
        });
        emitSelection();
      });
      headCell.appendChild(headBox);
      headRow.appendChild(headCell);
    }
    columns.forEach(function (column) {
      var label = column.title !== undefined
        ? column.title
        : (column.label !== undefined ? column.label : "");
      headRow.appendChild(el("th", column.num ? "num" : "", label));
    });
    thead.appendChild(headRow);
    table.appendChild(thead);

    var tbody = el("tbody");
    rows.forEach(function (row) {
      var tr = el("tr");
      if (opts.onRowClick) {
        tr.addEventListener("click", function () {
          opts.onRowClick(row);
        });
      }
      if (sel) {
        var key = String(rowKey(row));
        var checkCell = el("td", "col-check");
        var box = document.createElement("input");
        box.type = "checkbox";
        box.setAttribute("data-row-key", key);
        box.checked = selectedKeys.indexOf(key) >= 0;
        // 勾选不得触发整行跳转
        box.addEventListener("click", function (event) { event.stopPropagation(); });
        box.addEventListener("change", function () {
          if (box.checked) {
            if (selectedKeys.indexOf(key) < 0) {
              selectedKeys.push(key);
            }
          } else {
            selectedKeys = selectedKeys.filter(function (item) { return item !== key; });
          }
          if (headBox) {
            headBox.checked = selectedKeys.length === rows.length && rows.length > 0;
          }
          emitSelection();
        });
        checkCell.appendChild(box);
        tr.appendChild(checkCell);
        if (selectedKeys.indexOf(key) >= 0) {
          tr.classList.add("row-selected");
        }
      }
      columns.forEach(function (column) {
        var cls = column.cls || "";
        if (column.num) cls = (cls ? cls + " " : "") + "num";
        var td = el("td", cls || "");
        var value = column.render ? column.render(row) : row[column.key];
        if (value instanceof Node) {
          td.appendChild(value);
        } else {
          td.textContent = value === undefined || value === null
            ? (opts.nullText !== undefined ? opts.nullText : t("value.none"))
            : String(value);
        }
        if (column.trend) {
          td.classList.add(trendClass(row[column.trend]));
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    target.appendChild(table);
    if (wrap) {
      container.appendChild(wrap);
    }
    if (headBox) {
      headBox.checked = selectedKeys.length === rows.length && rows.length > 0;
    }
  }

  // ---------- ECharts K 线 option 脚手架 (P1-14) ----------

  var DEFAULT_UP = "#ff4b4b";
  var DEFAULT_DOWN = "#00b061";

  /** 构建 K 线 ECharts option 脚手架。
   * 提供透明背景 + 十字光标 + 轴关联链接 + 默认双 pane（K 线 + 成交量）
   * 网格、坐标轴与系列。调用方可通过 override 传递自定义 grids/xAxes/yAxes/series/legend/markLine。
   * @param {Object} theme 图表配色（chartTheme 返回的对象）
   * @param {Array} dates X 轴日期字符串
   * @param {Array} ohlc OHLC 数据 [[open,close,low,high], ...]
   * @param {Array} volumes 成交量数据 [{value, itemStyle}, ...]
   * @param {Object} override 覆盖/扩展字段：{candleName, upColor, downColor, grids, xAxes, yAxes, series, legend, markLine}
   * @returns {Object} ECharts option 对象
   */
  function buildCandlestickOption(theme, dates, ohlc, volumes, override) {
    var opt = override || {};
    var up = opt.upColor !== undefined ? opt.upColor : DEFAULT_UP;
    var down = opt.downColor !== undefined ? opt.downColor : DEFAULT_DOWN;
    var candleName = opt.candleName || "K线";

    var option = {
      backgroundColor: "transparent",
      tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
      axisPointer: { link: [{ xAxisIndex: "all" }] }
    };

    if (opt.legend) {
      option.legend = opt.legend;
    }

    option.grid = opt.grids || [
      { left: 56, right: 16, top: 16, height: "62%" },
      { left: 56, right: 16, top: "76%", height: "16%" }
    ];

    if (opt.xAxes) {
      option.xAxis = opt.xAxes;
    } else {
      option.xAxis = [
        { type: "category", data: dates, axisLabel: { color: theme.text }, axisLine: { lineStyle: { color: theme.border } } },
        { type: "category", gridIndex: 1, data: dates, axisLabel: { show: false }, axisLine: { lineStyle: { color: theme.border } } }
      ];
    }

    if (opt.yAxes) {
      option.yAxis = opt.yAxes;
    } else {
      option.yAxis = [
        { scale: true, axisLabel: { color: theme.text }, splitLine: { lineStyle: { color: theme.border, opacity: 0.4 } } },
        { gridIndex: 1, scale: true, axisLabel: { show: false }, splitLine: { show: false } }
      ];
    }

    var candle = {
      name: candleName,
      type: "candlestick",
      data: ohlc,
      itemStyle: { color: up, color0: down, borderColor: up, borderColor0: down }
    };
    if (opt.markLine) {
      candle.markLine = opt.markLine;
    }

    if (opt.series) {
      option.series = opt.series;
    } else {
      option.series = [
        candle,
        { name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: volumes }
      ];
    }

    return option;
  }

  /** 构建标准区块卡（标题行 + spacer + 副标题 + body）。
   *
   * 唯一实现：pages.js 与 dashboard.js 共用（R6-01 修复 —— 原先 pages.js 在 IIFE 内
   * 私有定义、dashboard.js 裸调用导致 `ReferenceError: buildCard is not defined`，
   * 仪表板管理页因此整页空白）。
   *
   * @param {string} id 区块编号（同时作为卡片 DOM id）
   * @param {string} title 卡片标题
   * @param {string} gridClass 栅格类名（如 g-12）
   * @param {string} extra 副标题文本（可为空）
   * @returns {{card: HTMLElement, body: HTMLElement}} 卡片节点与内容容器
   */
  function buildCard(id, title, gridClass, extra) {
    var card = el("section", "pg-card " + (gridClass || ""));
    if (id) {
      card.id = id;
    }
    var head = el("div", "pg-card-head");
    head.appendChild(el("span", "pg-card-title", title));
    if (extra) {
      head.appendChild(el("span", "pg-card-sub", extra));
    }
    head.appendChild(el("span", "pg-spacer"));
    card.appendChild(head);
    var body = el("div", "pg-body");
    card.appendChild(body);
    return { card: card, body: body };
  }

  window.Utils = {
    el: el,
    t: t,
    toast: toast,
    fmt: fmt,
    fmtPct: fmtPct,
    trendClass: trendClass,
    api: api,
    renderTable: renderTable,
    buildCandlestickOption: buildCandlestickOption,
    buildCard: buildCard
  };
})();
