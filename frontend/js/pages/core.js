/* =============================================================
 * OpenBB 中文投研工作台 · pages/core.js
 * 常量 · 通用工具 · 四态与徽章 · 表格渲染 · 路由
 * 由 tools/split_pages.py 从 pages.js 机械切分生成（R17），逻辑零改动；
 * 重新生成请改 pages.js 后重跑脚本，勿直接编辑本文件的结构性段落。
 * ============================================================= */
(function (P) {
  "use strict";

  // ---------- 常量 ----------
  var COLOR_UP = "#ff4b4b";
  var COLOR_DOWN = "#00b061";
  var COLOR_MUTED = "#7a7a82";
  var COLOR_WARN = "#b77f3d";
  var COLOR_ERR = "#ff5a5a";

  // P1-13：共享工具别名（t/el/fmt/fmtPct/api/toast/renderTable 原本各 IIFE 重复定义于此）
  var t = window.Utils.t;
  var el = window.Utils.el;
  var fmt = window.Utils.fmt;
  var fmtPct = window.Utils.fmtPct;
  var api = window.Utils.api;
  var toast = window.Utils.toast;

  // 后端固定中文口径字段 → 前端语义键（R2-08 / R2-15：集中收口，渲染处禁止散写中文字段名）
  // 评级：stock.py::_RATING_NAMES 固定为 买入/增持/中性/减持/卖出
  var RATING_SOURCE_KEYS = { buy: "买入", add: "增持", neutral: "中性", reduce: "减持", sell: "卖出" };
  // 财务摘要：/api/stock/financial-summary 的 metrics[].key 为新浪原始中文口径
  var FIN_METRIC_FIELDS = {
    revenue: "营业总收入",
    netProfit: "归母净利润",
    eps: "基本每股收益",
    grossMargin: "毛利率",
    roe: "净资产收益率(ROE)",
    debtRatio: "资产负债率"
  };

  // 各视图容器（由 index.html 提供）
  var VIEW_IDS = {
    sectors: "page-sectors",
    stock: "page-stock",
    "sector-val": "page-sector-val",
    news: "page-news",
    fund: "page-fund",
    charting: "page-charting",
    watchlist: "page-watchlist",
    lhb: "page-lhb",
    portfolio: "page-portfolio",
    connectors: "page-connectors",
    dashboard: "page-dashboard",
    // DS-01/DS-02：仪表板管理与组件库独立路由（URL 可直达，四态齐全）
    "dashboard-manage": "page-dashboard-manage",
    "widget-library": "page-widget-library",
    yjb: "page-yjb",
    settings: "page-settings",
    // 第七轮（M1/M2）：宏观与周期、估值分位（均挂在「市场」下的二级页）
    macro: "page-macro",
    valuation: "page-valuation",
    crowding: "page-crowding",
    "fund-combo": "page-fund-combo",
    // 第七轮 M9/M6：筛选器与提醒中心
    screener: "page-screener",
    alerts: "page-alerts",
    // 第十轮 P1 新 IA：7 个新页（其余 12 页复用既有 page-view）
    today: "page-today",
    market: "page-market",
    fin: "page-fin",
    ann: "page-ann",
    mgr: "page-mgr",
    tools: "page-tools",
    cand: "page-cand"
  };

  // 图表实例登记表：路由切换时统一 dispose，窗口 resize 时统一 resize
  P.charts = [];

  // 页面会话状态：市场/口径/标的，用于 Tab 切换时保留上下文
  var state = {
    sectors: { market: "a", kind: "industry" },
    stock: { symbol: "688012", market: "a", tab: "kline", finTab: "summary" },
    sectorVal: { board: "", market: "a" },
    // 图表分析页状态：标的选择 + 周期/复权/指标开关 + K线/分时 Tab + 画线开关
    charting: { symbol: "688012", market: "a", period: "日k", adjust: "qfq", tab: "kline", draw: false },
    // 设置页状态：AI 配置表单（api_key 留空表示保留原值，与后端契约一致）
    settings: { saving: false },
    // 第七轮 M5：基金组合页状态（Tab / 上次输入的代码 / 风险档位）
    fundCombo: { tab: "diagnose", codes: "", risk: "balanced" },
    // 第七轮 M9：筛选器页状态（条件行由 DOM 持有，这里存快照与结果缓存）
    screener: { conditions: [], lastResult: null },
    // 第七轮 M6：提醒中心页状态（规则表单草稿）
    alerts: { draftType: "price", unreadOnly: false },
    // 第七轮 M12 补齐：拥挤度排行口径（industry/concept）
    crowdingKind: "industry"
  };

  // =============================================================
  // 一、通用工具
  // =============================================================
  /** 涨跌样式类：正红、负绿、零灰。
   * @param {*} value 待格式化或处理的值
   * @returns {*} 处理结果
   */
  function pctClass(value) {
    if (value === null || value === undefined || Number(value) === 0) {
      return "flat";
    }
    return Number(value) > 0 ? "up" : "down";
  }

  /** 亿/万元换算：接口返回元时转亿元展示。
   * @param {*} value 待格式化或处理的值
   * @returns {*} 处理结果
   */
  function yi(value) {
    if (value === null || value === undefined) {
      return null;
    }
    return Number(value) / 1e8;
  }

  /** 成交额格式化：元 → 亿元/万元（与首页行情口径一致）。
   * @param {*} value 元为单位的数值
   * @returns {string} 带单位的文本
   */
  function fmtAmount(value) {
    if (value === null || value === undefined) {
      return t("value.none");
    }
    var num = Number(value);
    if (!isFinite(num)) {
      return t("value.none");
    }
    if (Math.abs(num) >= 1e8) {
      return fmt(num / 1e8, 2) + " 亿";
    }
    if (Math.abs(num) >= 1e4) {
      return fmt(num / 1e4, 2) + " 万";
    }
    return fmt(num, 2);
  }

  /** 发送 JSON 请求（POST/PATCH/DELETE），统一解包信封并抛中文错误。
   * @param {string} method HTTP 方法
   * @param {string} path 接口路径（可含 query）
   * @param {*} body 请求体（GET/DELETE 可不传）
   * @returns {Promise} data 段
   */
  function sendJson(method, path, body) {
    var init = { method: method, cache: "no-store", headers: {} };
    if (body !== undefined && body !== null) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    return fetch(path, init).then(function (response) {
      return response.json();
    }).then(function (envelope) {
      if (envelope && envelope.error) {
        var error = new Error(envelope.error);
        error.code = envelope.error_code;
        throw error;
      }
      return envelope ? envelope.data : null;
    }).catch(function (error) {
      if (error instanceof Error && error.message) {
        throw error;
      }
      throw new Error("网络连接失败，请检查网络后重试。");
    });
  }

  /** 相对时间：把 YYYY-MM-DD HH:MM:SS 转成「x 分钟前 / x 小时前 / 日期」。
   * @param {string} text 文本内容
   * @returns {*} 处理结果
   */
  function relativeTime(text) {
    if (!text) {
      return "";
    }
    // TZ-07：后端时间是北京裸串，统一按 UTC+8 解析，避免非 UTC+8 浏览器算错距今秒差
    var stamp = window.I18N && window.I18N.bjMs ? window.I18N.bjMs(text) : NaN;
    if (isNaN(stamp)) {
      return String(text).slice(0, 16);
    }
    var diff = (Date.now() - stamp) / 1000;
    if (diff < 60) {
      return t("stock.time.justNow");
    }
    if (diff < 3600) {
      return Math.floor(diff / 60) + t("stock.time.minAgo");
    }
    if (diff < 86400) {
      return Math.floor(diff / 3600) + t("stock.time.hourAgo");
    }
    return String(text).slice(0, 10);
  }

  /** 时区标注标签：在时间旁统一挂「北京」后缀，悬浮显示完整口径（TZ-01）。
   * @param {string} tip 附加换算说明（如美股收盘对应北京时间），可为空串
   * @returns {*} 处理结果
   */
  function bjTag(tip) {
    var full = t("common.time.beijing") + (tip ? "｜" + tip : "");
    var node = el("span", "tz-tag", t("common.time.bj"));
    node.title = full;  // 悬浮提示：完整口径 + 跨市场换算，不占用列表宽度
    return node;
  }

  /** 按市场给出收盘时间的北京时间换算说明。
   * @param {string} market 市场标识（a/hk/us）
   * @returns {string} 提示文案；A 股与未知市场返回空串（本身即北京时间，无需额外说明）
   */
  function marketTzTip(market) {
    var key = String(market || "").toLowerCase();
    if (key === "us") {
      return t("common.time.usTip");
    }
    if (key === "hk") {
      return t("common.time.hkTip");
    }
    return "";
  }

  /** 资讯时间：相对时间 + 北京时间标签（悬浮显示绝对北京时间）。
   * @param {string} text 后端返回的北京 naive 时间串
   * @returns {*} 处理结果
   */
  function newsTime(text) {
    var wrap = el("span", "pg-news-time", relativeTime(text) + " ");
    var tag = bjTag("");
    // 资讯流只给相对时间，悬浮补绝对北京时间，便于核对跨市场发布时点
    tag.title = t("common.time.beijing") + "｜" + String(text || t("value.none"));
    wrap.appendChild(tag);
    return wrap;
  }

  /** 时间文本 + 北京时间标签的组合节点。
   * @param {string} text 时间文本（后端北京裸串或相对时间）
   * @param {string} tip 附加换算说明，可为空串
   * @param {string} cls 外层类名
   * @returns {*} 处理结果
   */
  function timeWithBj(text, tip, cls) {
    var wrap = el("span", cls || "pg-card-sub", (text || "") + " ");
    wrap.appendChild(bjTag(tip));
    return wrap;
  }

  // api / toast 已提取到 utils.js（P1-13），此处不再重复定义。

  // =============================================================
  // 二、四态与徽章
  // =============================================================
  /** 加载态：组件级骨架屏，区块内禁点。
   * @param {HTMLElement} body 区块容器（.pg-body，四态 data-state 挂在此节点）
   */
  function setLoading(body) {
    body.innerHTML = "";
    var box = el("div", "pg-skeleton");
    for (var i = 0; i < 5; i += 1) {
      box.appendChild(el("span"));
    }
    body.appendChild(box);
    body.setAttribute("data-state", "loading");
  }

  /** 空态：就地替换内容区，提供刷新按钮（§7 空态规范）。
   * @param {HTMLElement} body 区块容器（.pg-body，四态 data-state 挂在此节点）
   * @param {string} message 提示文案（中文）
   * @param {*} onRetry 重试回调
   */
  function setEmpty(body, message, onRetry) {
    body.innerHTML = "";
    var box = el("div", "pg-state");
    box.appendChild(el("div", "pg-state-title", message || t("state.empty")));
    var button = el("button", "pg-btn", t("action.refresh"));
    button.type = "button";
    button.addEventListener("click", function () {
      if (onRetry) {
        onRetry();
      }
    });
    box.appendChild(button);
    body.appendChild(box);
    body.setAttribute("data-state", "empty");
  }

  /** 错误态：中文原因 + 重试；与空态严格区分（§7）。
   * @param {HTMLElement} body 区块容器（.pg-body，四态 data-state 挂在此节点）
   * @param {string} message 提示文案（中文）
   * @param {*} onRetry 重试回调
   */
  function setError(body, message, onRetry) {
    body.innerHTML = "";
    var box = el("div", "pg-state");
    var title = el("div", "pg-state-title", t("state.errorTitle"));
    var hint = el("div", "pg-state-hint", message || t("state.errorHint"));
    var button = el("button", "pg-btn", t("action.retry"));
    button.type = "button";
    button.addEventListener("click", function () {
      if (onRetry) {
        onRetry();
      }
    });
    box.appendChild(title);
    box.appendChild(hint);
    box.appendChild(button);
    body.appendChild(box);
    body.setAttribute("data-state", "error");
  }

  /** 双徽章：数据源 + 口径；降级命中时徽章显示实际源（§8）。
   * @param {HTMLElement} card 卡片 DOM 节点
   * @param {*} source 数据源名称
   * @param {*} caliber 口径说明文案（展示在口径徽章）
   * @param {*} degrade 是否降级命中（决定徽章样式）
   */
  function setBadges(card, source, caliber, degrade) {
    var head = card.querySelector(".pg-card-head");
    if (!head) {
      return;
    }
    var old = head.querySelectorAll(".badge-src, .badge-caliber");
    old.forEach(function (node) {
      node.remove();
    });
    if (source) {
      var src = el("span", "badge-src", source);
      if (degrade) {
        src.classList.add("state-degraded");
      }
      head.appendChild(src);
    }
    if (caliber) {
      head.appendChild(el("span", "badge-caliber", caliber));
    }
  }

  /** 统一占位卡结构：标题 + 双徽章位 + 内容区。
   * @param {string} id 元素 id / 组件 id
   * @param {*} title 标题
   * @param {*} gridClass 栅格类名
   * @param {*} extra 附加配置项
   * @returns {Object} 处理结果
   */
  function buildCard(id, title, gridClass, extra) {
    // R6-01：委托到唯一实现 window.Utils.buildCard，dashboard.js 亦复用同一函数，
    // 避免「IIFE 私有定义 + 跨文件裸调用」导致的 ReferenceError。
    return window.Utils.buildCard(id, title, gridClass, extra);
  }

  /** 释放并重建图表实例（避免路由切换后残留旧图）。
   * @param {HTMLElement} node DOM 节点
   * @returns {*} 处理结果
   */
  function mountChart(node) {
    if (!window.echarts || !node) {
      return null;
    }
    var instance = window.echarts.init(node, null, { renderer: "canvas" });
    P.charts.push(instance);
    return instance;
  }

  /** 统一图表配色（跟随主题变量）。
   * @returns {Object} 处理结果
   */
  function chartTheme() {
    var styles = window.getComputedStyle(document.documentElement);
    return {
      text: styles.getPropertyValue("--text-dim").trim() || "#a2a2a2",
      border: styles.getPropertyValue("--border").trim() || "#2a2a2a",
      panel: styles.getPropertyValue("--bg-widget").trim() || "#151518"
    };
  }

  // =============================================================
  // 三、表格通用渲染
  // =============================================================
  /**
   * 渲染表格（P1-13 委托 window.Utils.renderTable）。
   * columns: [{title, key, cls, render}]
   * rows: 数据数组；onRowClick(row) 提供整行点击（跳转闭环）。
   * @param {HTMLElement} body 区块容器（.pg-body，四态 data-state 挂在此节点）
   * @param {Array} columns 列定义（字段名数组）
   * @param {Array} rows 记录行列表
   * @param {*} onRowClick 行点击回调
   * @param {*} maxHeightClass 最大高度类名
   */
  function renderTable(body, columns, rows, onRowClick, maxHeightClass, selectable) {
    if (!rows || rows.length === 0) {
      setEmpty(body, t("state.empty"));
      return;
    }
    window.Utils.renderTable(body, columns, rows, {
      tableClass: "pg-table",
      wrapClass: "pg-table-wrap",
      maxHeightClass: maxHeightClass,
      onRowClick: onRowClick,
      nullText: t("value.none"),
      // R6-09：可选行（自选股批量操作）；不传时与旧行为完全一致
      selectable: selectable
    });
    body.setAttribute("data-state", "default");
  }

  /** 生成带涨跌色的文本节点。
   * @param {*} value 待格式化或处理的值
   * @returns {*} 处理结果
   */
  function pctNode(value) {
    var span = el("span", pctClass(value), fmtPct(value));
    return span;
  }

  // =============================================================
  // 四、路由
  // =============================================================
  /** 解析 #/view?k=v 形式的 hash。
   * @returns {Object} 处理结果
   */
  function parseHash() {
    var raw = window.location.hash || "#/home";
    var question = raw.indexOf("?");
    var path = question >= 0 ? raw.slice(0, question) : raw;
    var query = question >= 0 ? raw.slice(question + 1) : "";
    var params = {};
    new URLSearchParams(query).forEach(function (value, key) {
      params[key] = value;
    });
    return { path: path.replace(/^#\/?/, ""), params: params };
  }

  /** 切换视图显隐（只显示目标视图，其余隐藏，避免旧数据残留）。
   * @param {string} name 名称
   */
  function showView(name) {
    Object.keys(VIEW_IDS).forEach(function (key) {
      var node = document.getElementById(VIEW_IDS[key]);
      if (!node) {
        return;
      }
      node.hidden = key !== name;
    });
    // home 视图由 app.js 管理，这里只在非 home 时隐藏
    var viewport = document.getElementById("viewport");
    if (viewport) {
      viewport.hidden = name !== "home";
    }
  }

  /** 清理图表实例。 */
  function disposeCharts() {
    P.charts.forEach(function (instance) {
      try {
        instance.dispose();
      } catch (error) {
        /* 已释放则忽略 */
      }
    });
    P.charts = [];
  }

  /** 主路由分发。 */
  function route() {
    var parsed = parseHash();
    var name = parsed.path.split("/")[0] || "home";
    // 标题统一取 app.js 的 VIEW_TITLES（单一实现）：
    // 这里原先自维护一份只含 5 个键的映射表，导致 macro/valuation/crowding 等
    // 视图切过去后标题仍停留在上一页（第七轮补产品图时发现）。
    var title = window.App && typeof window.App.viewTitle === "function"
      ? window.App.viewTitle(name)
      : "";
    var titleNode = document.getElementById("page-title");
    if (titleNode && title) {
      titleNode.textContent = title;
    }
    // 同步左侧菜单选中态
    // R6-13：顶栏 8 个一级项 —— 按视图所属分组高亮（同组多个二级视图共用同一高亮），
    // 不再用 data-view 全等匹配（否则 dashboard-manage / widget-library 等二级页会全部灭灯）。
    // 映射表唯一实现放在 app.js 并导出为 window.App.syncNavActive。
    if (window.App && typeof window.App.syncNavActive === "function") {
      window.App.syncNavActive(name);
    }

    disposeCharts();
    if (!VIEW_IDS[name]) {
      showView("home");
      return;
    }
    showView(name);
    if (name === "sectors") {
      P.renderSectors(parsed.params);
    } else if (name === "stock") {
      P.renderStock(parsed.params);
    } else if (name === "sector-val") {
      // P2：板块详情（SD-01~07）；旧估值页实现保留在 sectorval.js，作为单板块估值的内嵌能力
      if (typeof P.renderSectorDetail === "function") {
        P.renderSectorDetail(parsed.params);
      } else {
        P.renderSectorVal(parsed.params);
      }
    } else if (name === "news") {
      P.renderNews(parsed.params);
    } else if (name === "fund") {
      P.renderFund(parsed.params);
    } else if (name === "charting") {
      P.renderCharting(parsed.params);
    } else if (name === "watchlist") {
      P.renderWatchlist(parsed.params);
    } else if (name === "lhb") {
      P.renderLhb();
    } else if (name === "portfolio") {
      P.renderPortfolio();
    } else if (name === "connectors") {
      P.renderConnectors();
    } else if (name === "dashboard") {
      if (window.renderDashboard) {
        window.renderDashboard();
      }
    } else if (name === "dashboard-manage") {
      if (window.Dashboard && window.Dashboard.renderManage) {
        window.Dashboard.renderManage(document.getElementById(VIEW_IDS["dashboard-manage"]));
      }
    } else if (name === "widget-library") {
      if (window.Dashboard && window.Dashboard.renderLibraryPage) {
        window.Dashboard.renderLibraryPage(document.getElementById(VIEW_IDS["widget-library"]));
      }
    } else if (name === "yjb") {
      P.renderYjb();
    } else if (name === "settings") {
      P.renderSettings();
    } else if (name === "macro") {
      P.renderMacro();
    } else if (name === "fund-combo") {
      P.renderFundCombo();
    } else if (name === "crowding") {
      P.renderCrowding();
    } else if (name === "valuation") {
      P.renderValuation();
    } else if (name === "screener") {
      P.renderScreener();
    } else if (name === "alerts") {
      P.renderAlerts();
    } else if (name === "today") {
      P.renderToday(parsed.params);
    } else if (name === "market") {
      P.renderMarketOverview(parsed.params);
    } else if (name === "fin") {
      P.renderFinSummary(parsed.params);
    } else if (name === "ann") {
      P.renderAnnLib(parsed.params);
    } else if (name === "mgr") {
      P.renderManager(parsed.params);
    } else if (name === "tools") {
      P.renderTools(parsed.params);
    } else if (name === "cand") {
      P.renderCandidates(parsed.params);
    }
  }

  /** 拦截主菜单点击：VIEW_IDS 内视图走 hash 路由，其余（home）保持 app.js 原行为。 */
  function bindNav() {
    document.addEventListener(
      "click",
      function (event) {
        var button = event.target.closest ? event.target.closest("#main-nav .nav-item") : null;
        if (!button) {
          return;
        }
        var view = button.getAttribute("data-view");
        if (!VIEW_IDS[view]) {
          return; // 交给 app.js 处理（其他视图仍是占位）
        }
        event.stopPropagation();
        event.preventDefault();
        var target = "#/" + view;
        if (view === "stock" && !/symbol=/.test(window.location.hash)) {
          target += "?symbol=" + state.stock.symbol + "&market=" + state.stock.market;
        }
        if (window.location.hash === target) {
          route();
        } else {
          window.location.hash = target;
        }
      },
      true // 捕获阶段先于 app.js 的冒泡监听，避免弹出「后续阶段接入」
    );
  }

  // ---- 导出到 window.Pages（供兄弟模块与 init 使用） ----
  P.COLOR_DOWN = COLOR_DOWN;
  P.COLOR_ERR = COLOR_ERR;
  P.COLOR_MUTED = COLOR_MUTED;
  P.COLOR_UP = COLOR_UP;
  P.COLOR_WARN = COLOR_WARN;
  P.FIN_METRIC_FIELDS = FIN_METRIC_FIELDS;
  P.RATING_SOURCE_KEYS = RATING_SOURCE_KEYS;
  P.VIEW_IDS = VIEW_IDS;
  P.api = api;
  P.bindNav = bindNav;
  P.bjTag = bjTag;
  P.buildCard = buildCard;
  P.chartTheme = chartTheme;
  P.disposeCharts = disposeCharts;
  P.el = el;
  P.fmt = fmt;
  P.fmtAmount = fmtAmount;
  P.fmtPct = fmtPct;
  P.marketTzTip = marketTzTip;
  P.mountChart = mountChart;
  P.newsTime = newsTime;
  P.parseHash = parseHash;
  P.pctClass = pctClass;
  P.pctNode = pctNode;
  P.relativeTime = relativeTime;
  P.renderTable = renderTable;
  P.route = route;
  P.sendJson = sendJson;
  P.setBadges = setBadges;
  P.setEmpty = setEmpty;
  P.setError = setError;
  P.setLoading = setLoading;
  P.showView = showView;
  P.state = state;
  P.t = t;
  P.timeWithBj = timeWithBj;
  P.toast = toast;
  P.yi = yi;
})(window.Pages = window.Pages || {});
