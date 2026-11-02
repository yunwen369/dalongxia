/* =============================================================
 * OpenBB 中文投研工作台 · 阶段 5.1 页面脚本
 * 覆盖三个视图：#/sectors（S-01~S-07）、#/stock（ST-01~ST-10）、
 * #/sector-val（V-01~V-04），并落地跳转 #1/#2/#5/#9 与四态渲染。
 *
 * 设计约束（《设计基准说明书_阶段5.1》）：
 * - 48 列栅格：主栏 .g-7（28 列=62%）、辅栏 .g-5（20 列=38%），1024 断点上下堆叠；
 * - 每区块右上角常驻「数据源 + 口径」双徽章，降级后显示实际命中源；
 * - 四态就地渲染：加载骨架 / 空态（点刷新）/ 错误态（中文原因+重试），空错不保留旧数据；
 * - 错误码只进 console，界面只显示中文原因（P1-13 口径）；
 * - 红涨绿跌、空值统一「无数据」、日期 YYYY-MM-DD、数字千分位；
 * - 本文件不改动 app.js / market.js 的既有行为，只在 hash 路由命中三视图时接管渲染。
 * ============================================================= */

(function () {
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
    alerts: "page-alerts"
  };

  // 图表实例登记表：路由切换时统一 dispose，窗口 resize 时统一 resize
  var charts = [];

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
    charts.push(instance);
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
    charts.forEach(function (instance) {
      try {
        instance.dispose();
      } catch (error) {
        /* 已释放则忽略 */
      }
    });
    charts = [];
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
      renderSectors(parsed.params);
    } else if (name === "stock") {
      renderStock(parsed.params);
    } else if (name === "sector-val") {
      renderSectorVal(parsed.params);
    } else if (name === "news") {
      renderNews(parsed.params);
    } else if (name === "fund") {
      renderFund(parsed.params);
    } else if (name === "charting") {
      renderCharting(parsed.params);
    } else if (name === "watchlist") {
      renderWatchlist(parsed.params);
    } else if (name === "lhb") {
      renderLhb();
    } else if (name === "portfolio") {
      renderPortfolio();
    } else if (name === "connectors") {
      renderConnectors();
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
      renderYjb();
    } else if (name === "settings") {
      renderSettings();
    } else if (name === "macro") {
      renderMacro();
    } else if (name === "fund-combo") {
      renderFundCombo();
    } else if (name === "crowding") {
      renderCrowding();
    } else if (name === "valuation") {
      renderValuation();
    } else if (name === "screener") {
      renderScreener();
    } else if (name === "alerts") {
      renderAlerts();
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

  // =============================================================
  // 五、#/sectors 板块分析（S-01~S-07）
  // =============================================================
  var sectorsCache = {}; // key=market|kind → 数据，避免 Tab 来回切换重复请求

  /** 构建板块页骨架（只建一次，之后仅更新内容）。
   * @param {HTMLElement} container DOM 容器节点
   */
  function buildSectorsShell(container) {
    container.innerHTML = "";
    var toolbar = el("div", "page-toolbar");

    // S-01 市场切换
    var marketTabs = el("div", "pg-tabs");
    marketTabs.id = "S-01";
    [["a", t("sectors.market.cn")], ["hk", t("sectors.market.hk")], ["us", t("sectors.market.us")]].forEach(function (item) {
      var button = el("button", "pg-tab", item[1]);
      button.type = "button";
      button.setAttribute("data-market", item[0]);
      marketTabs.appendChild(button);
    });
    toolbar.appendChild(marketTabs);

    // S-02 行业/概念切换
    var kindTabs = el("div", "pg-tabs");
    kindTabs.id = "S-02";
    [["industry", t("sectors.kind.industry")], ["concept", t("sectors.kind.concept")]].forEach(function (item) {
      var button = el("button", "pg-tab", item[1]);
      button.type = "button";
      button.setAttribute("data-kind", item[0]);
      kindTabs.appendChild(button);
    });
    toolbar.appendChild(kindTabs);
    toolbar.appendChild(el("span", "pg-spacer"));

    var refresh = el("button", "pg-btn", t("action.reload"));
    refresh.type = "button";
    refresh.id = "sectors-refresh";
    toolbar.appendChild(refresh);
    container.appendChild(toolbar);

    var grid = el("div", "pg-grid");
    grid.id = "sectors-grid";

    // 左主栏 S-03 热力图
    var heat = buildCard("S-03", t("sectors.heatTitle"), "g-7", t("sectors.heatSub"));
    var heatBody = heat.body;
    heatBody.classList.add("tall");
    heatBody.id = "s03-body";
    grid.appendChild(heat.card);

    // 右辅栏：S-04/S-05 排行 + S-06 摘要
    var rightStack = el("div", "g-5");
    rightStack.style.display = "flex";
    rightStack.style.flexDirection = "column";
    rightStack.style.gap = "16px";

    var rank = buildCard("S-04", t("sectors.rankIndustry"), "", "");
    rank.card.setAttribute("data-block", "rank"); // 行业排行（常驻，不再与 S-05 互换身份）
    rank.body.id = "s04-body";
    var rankBody = rank.body;
    rankBody.classList.add("short");
    rightStack.appendChild(rank.card);

    var summary = buildCard("S-06", t("sectors.summaryTitle"), "", "");
    summary.body.id = "s06-body";
    rightStack.appendChild(summary.card);
    grid.appendChild(rightStack);

    // S-05 概念排行：DS-07 整改——独立第二表（基准 §3.1：S-05 概念排行 .g-12 或 .g-7），
    // 此前与 S-04 共用同一张卡的 identity（id 在 S-04/S-05 间切换），概念口径要看就得切走行业口径。
    var concept = buildCard("S-05", t("sectors.rankConcept"), "g-12", t("sectors.rankConceptSub"));
    concept.body.id = "s05-body";
    concept.body.classList.add("short");
    grid.appendChild(concept.card);

    // S-07 资金流向通栏
    var flow = buildCard("S-07", t("sectors.flowTitle"), "g-12", t("sectors.flowSub"));
    flow.body.id = "s07-body";
    grid.appendChild(flow.card);

    container.appendChild(grid);

    marketTabs.addEventListener("click", function (event) {
      var button = event.target.closest(".pg-tab");
      if (!button) {
        return;
      }
      state.sectors.market = button.getAttribute("data-market");
      loadSectors();
    });
    kindTabs.addEventListener("click", function (event) {
      var button = event.target.closest(".pg-tab");
      if (!button) {
        return;
      }
      state.sectors.kind = button.getAttribute("data-kind");
      loadSectors();
    });
    refresh.addEventListener("click", function () {
      sectorsCache = {};
      loadSectors(true);
    });
  }

  /** 同步 Tab 选中态。 */
  function syncSectorTabs() {
    document.querySelectorAll("#S-01 .pg-tab").forEach(function (button) {
      button.classList.toggle("active", button.getAttribute("data-market") === state.sectors.market);
    });
    document.querySelectorAll("#S-02 .pg-tab").forEach(function (button) {
      button.classList.toggle("active", button.getAttribute("data-kind") === state.sectors.kind);
    });
  }

  /** 板块市场枚举归一（RT-03/RT-05）：行情/个股侧用 a/hk/us，板块侧用 cn/hk/us。
   * 跨页带 market=a 跳板块时统一映射为 cn，避免后端 INVALID_PARAM 与页头误显示「美股」。
   * @param {*} market 市场标识（a/cn/hk/us 或空）
   * @returns {string} 板块侧枚举：cn / hk / us
   */
  function normSectorMarket(market) {
    var value = String(market === null || market === undefined ? "" : market).toLowerCase();
    if (value === "hk") {
      return "hk";
    }
    if (value === "us") {
      return "us";
    }
    return "a";  // a / cn / 空值 一律视为 A 股（后端已统一 a 口径，此处仅做兜底）
  }

  /** 渲染板块页。
   * @param {Object} params 查询参数对象
   */
  function renderSectors(params) {
    var container = document.getElementById(VIEW_IDS.sectors);
    if (params.market) {
      state.sectors.market = normSectorMarket(params.market);
    }
    if (params.kind) {
      state.sectors.kind = params.kind;
    }
    if (!container.querySelector("#sectors-grid")) {
      buildSectorsShell(container);
    }
    syncSectorTabs();
    loadSectors();
  }

  /** 拉取并渲染板块页全部区块。
   * @param {boolean} force 是否强制刷新（true 时跳过本地缓存）
   */
  function loadSectors(force) {
    var market = state.sectors.market;
    var kind = state.sectors.kind;
    var cacheKey = market + "|" + kind;
    var cards = {
      s03: document.getElementById("S-03"),
      s04: document.getElementById("S-04"),
      s05: document.getElementById("S-05"),
      s06: document.getElementById("S-06"),
      s07: document.getElementById("S-07")
    };
    var bodies = {
      s03: document.getElementById("s03-body"),
      s04: document.getElementById("s04-body"),
      s05: document.getElementById("s05-body"),
      s06: document.getElementById("s06-body"),
      s07: document.getElementById("s07-body")
    };
    if (!bodies.s03) {
      return;
    }

    if (force) {
      sectorsCache = {};
    }
    if (sectorsCache[cacheKey]) {
      paintSectors(sectorsCache[cacheKey]);
      return;
    }
    setLoading(bodies.s03);
    setLoading(bodies.s04);
    setLoading(bodies.s05);
    setLoading(bodies.s06);
    setLoading(bodies.s07);

    // DS-07：行业与概念两张排行表各自独立取数、同时常驻，互不替代；
    // S-02 的行业/概念切换只决定 S-03 热力图口径（基准 §3.4 两表分别绑定 industry/concept 接口）。
    var conceptTask = market === "a"
      ? api("/api/sector/concept", { market: "a" })
      : Promise.resolve(null);
    // 资金流仅 A股有免费通道（同花顺行业资金流）；港美股不发请求，避免整页因单块 4xx 被判失败
    var flowTask = market === "a"
      ? api("/api/sector/fundflow", { market: market })
      : Promise.resolve(null);
    Promise.all([
      api("/api/sector/industry", { market: market }),
      conceptTask,
      api("/api/sector/heatmap", { market: market, kind: kind }),
      api("/api/sector/summary", { market: market }),
      flowTask
    ])
      .then(function (result) {
        var payload = {
          market: market,
          kind: kind,
          list: result[0],
          concept: result[1],
          heat: result[2],
          summary: result[3],
          flow: result[4]
        };
        sectorsCache[cacheKey] = payload;
        paintSectors(payload);
      })
      .catch(function (error) {
        var retry = function () {
          loadSectors(true);
        };
        setError(bodies.s03, error.message, retry);
        setError(bodies.s04, error.message, retry);
        setError(bodies.s05, error.message, retry);
        setError(bodies.s06, error.message, retry);
        setError(bodies.s07, error.message, retry);
      });
  }

  /** 渲染板块页（默认态）。
   * @param {*} payload 渲染载荷（聚合后的数据）
   * @returns {*} 处理结果
   */
  function paintSectors(payload) {
    var market = payload.market;
    var bodies = {
      s03: document.getElementById("s03-body"),
      s04: document.getElementById("s04-body"),
      s05: document.getElementById("s05-body"),
      s06: document.getElementById("s06-body"),
      s07: document.getElementById("s07-body")
    };
    var cards = {
      s03: document.getElementById("S-03"),
      s04: document.getElementById("S-04"),
      s05: document.getElementById("S-05"),
      s06: document.getElementById("S-06"),
      s07: document.getElementById("S-07")
    };
    if (!bodies.s03) {
      return;
    }
    var degrade = payload.list.source && payload.list.source !== "同花顺" && market === "a";

    // ---- S-03 热力图 ----
    setBadges(cards.s03, payload.heat.source, payload.heat.caliber, degrade);
    bodies.s03.innerHTML = "";
    var nodes = (payload.heat.nodes || []).filter(function (node) {
      return node.value !== null && node.value !== undefined;
    });
    if (nodes.length === 0) {
      setEmpty(bodies.s03, t("state.empty"), function () {
        loadSectors(true);
      });
    } else {
      var chartBox = el("div", "pg-chart");
      chartBox.id = "s03-chart";
      bodies.s03.appendChild(chartBox);
      var chart = mountChart(chartBox);
      if (chart) {
        // UI-03 整改：按面积占比分档控制标签，小面积块不再压字
        // （基准 §3.3「最小字号 10px，溢出 2 行截断不压字」）
        var totalValue = nodes.reduce(function (sum, node) {
          return sum + (Number(node.value) || 0);
        }, 0) || 1;
        var data = nodes.map(function (node) {
          var pct = node.pct;
          var color = pct > 0 ? COLOR_UP : pct < 0 ? COLOR_DOWN : COLOR_MUTED;
          var share = (Number(node.value) || 0) / totalValue;
          // 三档：大块两行（板块名 + 涨跌幅）；中块一行（取整百分比，窄块也放得下）；
          // 更小面积块**不显示标签、仅着色**——避免 ECharts 把「-1.64%」截成「-1.6…」这种压字残句，
          // 精确值仍可经悬浮提示与点击进入板块页查看（基准 §3.3：最小 10px + 溢出截断不压字）。
          var labelCfg;
          if (share >= 0.02) {
            labelCfg = { show: true, fontSize: 10 };
          } else if (share >= 0.012) {
            labelCfg = { show: true, fontSize: 10, formatter: function () {
              return pct === null || pct === undefined ? t("value.none") : Math.round(pct) + "%";
            } };
          } else {
            labelCfg = { show: false };
          }
          return {
            name: node.name,
            value: node.value,
            pct: pct,
            amount: node.amount,
            lead: node.lead_stock,
            label: labelCfg,
            itemStyle: { color: color, borderColor: "#0b0b0c", borderWidth: 1 }
          };
        });
        chart.setOption({
          backgroundColor: "transparent",
          tooltip: {
            /** 热力图悬浮说明：名称 + 涨跌幅 + 成交额（亿元）+ 领涨股。
             * @param {*} info 信息对象
             * @returns {*} 处理结果
             */
            formatter: function (info) {
              var source = info.data || {};
              return (
                "<b>" + info.name + "</b><br/>" + t("sectors.tooltip.change") + fmtPct(source.pct) +
                "<br/>" + t("sectors.tooltip.amount") + (source.amount === null || source.amount === undefined ? t("value.none") : fmt(source.amount) + " 亿元") +
                "<br/>" + t("sectors.tooltip.lead") + (source.lead || t("value.none"))
              );
            }
          },
          series: [
            {
              type: "treemap",
              roam: false,
              nodeClick: false, // 点击由 chart.on('click') 统一处理（评审 §3 #9）
              breadcrumb: { show: false },
              width: "100%",
              height: "100%",
              itemStyle: { gapWidth: 1 },
              label: {
                show: true,
                fontSize: 10,
                color: "#fff",
                // 两行上限 + 截断省略，避免长板块名在小块里挤成两三行压字
                overflow: "truncate",
                lineOverflow: "truncate",
                ellipsis: "…",
                lineHeight: 12,
                /** 热力图块内标签：板块名换行 + 涨跌幅百分比（缺值显示「无数据」）。
                 * @param {*} info 信息对象
                 * @returns {*} 处理结果
                 */
                formatter: function (info) {
                  var pctText = info.data.pct === null ? t("value.none") : Number(info.data.pct).toFixed(2) + "%";
                  return info.name + "\n" + pctText;
                }
              },
              data: data
            }
          ]
        });
        chart.on("click", function (info) {
          if (info && info.name) {
            jumpToSectorVal(info.name, market);
          }
        });
      }
      // 图表渲染完成即视为默认态，避免四态巡检一直停在 loading
      bodies.s03.setAttribute("data-state", "default");
    }

    // ---- S-04 行业排行表 ----
    paintRankTable(bodies.s04, cards.s04, payload.list, market, degrade);
    // ---- S-05 概念排行表（DS-07：独立第二表，不再与 S-04 互换） ----
    if (payload.concept) {
      paintRankTable(bodies.s05, cards.s05, payload.concept, market,
        payload.concept.source && payload.concept.source !== "同花顺" && market === "a");
    } else {
      setBadges(cards.s05, "", "", false);
      setEmpty(bodies.s05, t("sectors.conceptOverseas"), function () {
        loadSectors(true);
      });
    }

    // ---- S-06 摘要 ----
    setBadges(cards.s06, payload.summary.source, payload.summary.caliber, degrade);
    paintSummary(bodies.s06, payload.summary);

    // ---- S-07 资金流向（行业口径常驻：概念板块无免费资金流口径，不在两张排行表之间切换） ----
    if (market !== "a") {
      setBadges(cards.s07, "", "", false);
      setEmpty(bodies.s07, t("sectors.overseasFlow"), function () {
        loadSectors(true);
      });
    } else if (payload.flow) {
      setBadges(cards.s07, payload.flow.source, payload.flow.caliber, degrade);
      var flowRows = (payload.flow.items || []).slice(0, 40);
      renderTable(
        bodies.s07,
        [
          { title: t("sectors.col.board"), key: "name", cls: "name" },
          { title: t("sectors.col.netInflow"), cls: "num", render: function (row) { return pctNode(row.net_inflow); } },
          { title: t("sectors.col.inflow"), cls: "num", render: function (row) { return fmt(row.inflow); } },
          { title: t("sectors.col.outflow"), cls: "num", render: function (row) { return fmt(row.outflow); } },
          // 源不提供分项：显示「无数据」而不是补 0（§9）
 // @param {*} row 单行记录
          { title: t("sectors.col.superBig"), cls: "num", render: function (row) { return fmt(row.super_big); } },
          { title: t("sectors.col.big"), cls: "num", render: function (row) { return fmt(row.big); } }
        ],
        flowRows,
        null,
        null
      );
    }
  }

  /** 渲染一张板块排行表（S-04 行业 / S-05 概念共用同一渲染契约）。
   * @param {HTMLElement} body 区块容器（.pg-body，四态 data-state 挂在此节点）
   * @param {HTMLElement} card 卡片根节点（右上角双徽章挂这里）
   * @param {Object} list 接口 data 段（source/caliber/items）
   * @param {string} market 市场标识（cn/hk/us），跳转时带上
   * @param {boolean} degrade 是否降级（源非首选）
   * @returns {void} 无返回值
   */
  function paintRankTable(body, card, list, market, degrade) {
    if (!body || !list) {
      return;
    }
    setBadges(card, list.source, list.caliber, degrade);
    if (list.error) {
      setError(body, list.error, function () { loadSectors(true); });
      return;
    }
    var rows = list.items || [];
    if (!rows.length) {
      setEmpty(body, t("state.empty"), function () { loadSectors(true); });
      return;
    }
    // 概念口径 800+ 行，排行表只渲染涨幅前 60（同源同序，不改变统计口径）；
    // 行业仅 90 行，全量渲染不做截断。
    var displayRows = list.kind === "concept" ? rows.slice(0, 60) : rows;
    renderTable(
      body,
      [
        { title: t("sectors.col.board"), key: "name", cls: "name" },
        { title: t("sectors.pctCol"), cls: "num", render: function (row) { return pctNode(row.pct); } },
        { title: t("sectors.amountCol"), cls: "num", render: function (row) { return fmt(row.amount); } },
        { title: t("sectors.col.lead"), key: "lead_stock" },
        { title: t("sectors.col.leadPct"), cls: "num", render: function (row) { return pctNode(row.lead_pct); } }
      ],
      displayRows,
      function (row) {
        jumpToSectorVal(row.name, market);
      },
      "short"
    );
  }

  /** 摘要卡：领涨/领跌/净流入 Top3 + 集中度。
   * @param {HTMLElement} body 区块容器（.pg-body，四态 data-state 挂在此节点）
   * @param {*} summary 摘要数据
   */
  function paintSummary(body, summary) {
    body.innerHTML = "";
    if (!summary || (!summary.gainers.length && !summary.losers.length)) {
      setEmpty(body, t("state.empty"), function () {
        loadSectors(true);
      });
      return;
    }
    /** 板块摘要分组：按 title 输出小标题，并逐条渲染 items 的 valueKey 指标。
     * @param {*} title 标题
     * @param {Array} items 记录列表
     * @param {*} valueKey 取值字段名
     */
    function group(title, items, valueKey) {
      var wrap = el("div", "pg-summary-row");
      var label = el("div", "pg-card-sub", title);
      body.appendChild(label);
      items.forEach(function (item) {
        var box = el("div", "pg-summary-item");
        box.appendChild(el("div", "pg-summary-label", item.name));
        var value = el("div", "pg-summary-value " + pctClass(item[valueKey]), fmt(item[valueKey]) + (valueKey === "pct" ? "%" : " 亿"));
        box.appendChild(value);
        wrap.appendChild(box);
      });
      body.appendChild(wrap);
    }
    group(t("sectors.summary.leaders"), summary.gainers || [], "pct");
    group(t("sectors.summary.losers"), summary.losers || [], "pct");
    if (summary.inflow && summary.inflow.length) {
      group(t("sectors.summary.inflow"), summary.inflow, "net_inflow");
    }
    var conc = el("div", "pg-card-sub");
    conc.textContent =
      t("val.kv.concentration") + (summary.concentration === null ? t("value.none") : summary.concentration + "%");
    body.appendChild(conc);
    body.setAttribute("data-state", "default");
  }

  /** 跳转 #1/#9：板块 → 单板块估值。
   * @param {*} board 板块名称
   * @param {*} market 市场标识：a/hk/us
   */
  function jumpToSectorVal(board, market) {
    window.location.hash =
      "#/sector-val?board=" + encodeURIComponent(board) + "&market=" + normSectorMarket(market);
  }

  // =============================================================
  // 六、#/stock 个股详情（ST-01~ST-10）
  // =============================================================
  /** 构建个股页骨架。
   * @param {HTMLElement} container DOM 容器节点
   */
  function buildStockShell(container) {
    container.innerHTML = "";
    var toolbar = el("div", "page-toolbar");
    var label = el("span", "pg-card-sub", t("stock.kv.codeLabel"));
    toolbar.appendChild(label);
    var input = document.createElement("input");
    input.id = "stock-symbol";
    input.value = state.stock.symbol;
    input.setAttribute("aria-label", t("stock.kv.codeLabel"));
    input.style.cssText =
      "width:120px;background:var(--bg-panel);border:1px solid var(--border);color:var(--text);" +
      "border-radius:5px;padding:4px 8px;font-family:var(--font-mono)";
    toolbar.appendChild(input);
    var marketSelect = document.createElement("select");
    marketSelect.id = "stock-market";
    marketSelect.style.cssText =
      "background:var(--bg-panel);border:1px solid var(--border);color:var(--text);border-radius:5px;padding:4px 8px";
    [["a", t("stock.market.a")], ["hk", t("sectors.market.hk")], ["us", t("sectors.market.us")]].forEach(function (item) {
      var option = document.createElement("option");
      option.value = item[0];
      option.textContent = item[1];
      marketSelect.appendChild(option);
    });
    marketSelect.value = state.stock.market;
    toolbar.appendChild(marketSelect);
    var go = el("button", "pg-btn", t("action.view"));
    go.type = "button";
    go.id = "stock-go";
    toolbar.appendChild(go);
    toolbar.appendChild(el("span", "pg-spacer"));
    var refresh = el("button", "pg-btn", t("action.reload"));
    refresh.type = "button";
    refresh.id = "stock-refresh";
    toolbar.appendChild(refresh);
    container.appendChild(toolbar);

    var grid = el("div", "pg-grid");
    grid.id = "stock-grid";

    // ST-01 报价大卡（通栏）
    var quote = buildCard("ST-01", t("stock.quoteCard"), "g-12", "");
    quote.body.id = "st01-body";
    grid.appendChild(quote.card);

    // 左主栏：ST-02/ST-03 互斥 Tab + ST-04 + ST-05
    var left = el("div", "g-7");
    left.style.display = "flex";
    left.style.flexDirection = "column";
    left.style.gap = "16px";

    var chartCard = buildCard("ST-02", t("stock.chartCard"), "", "");
    chartCard.card.setAttribute("data-block", "chart"); // 稳定锚点：id 在 ST-02/ST-03 间切换
    chartCard.body.id = "st02-body";
    var tabRow = el("div", "pg-tabs");
    tabRow.style.marginBottom = "8px";
    [["kline", t("stock.klineTab")], ["minute", t("stock.minuteTab")]].forEach(function (item) {
      var button = el("button", "pg-tab", item[1]);
      button.type = "button";
      button.setAttribute("data-tab", item[0]);
      tabRow.appendChild(button);
    });
    chartCard.card.insertBefore(tabRow, chartCard.body);
    left.appendChild(chartCard.card);

    var fundCard = buildCard("ST-04", t("stock.fundCard"), "", t("stock.fundSub"));
    fundCard.body.id = "st04-body";
    left.appendChild(fundCard.card);

    var newsCard = buildCard("ST-05", t("stock.newsCard"), "", t("stock.newsSub"));
    newsCard.body.id = "st05-body";
    left.appendChild(newsCard.card);
    grid.appendChild(left);

    // 右辅栏：ST-07 舆情 + ST-06 机构预测
    var right = el("div", "g-5");
    right.style.display = "flex";
    right.style.flexDirection = "column";
    right.style.gap = "16px";
    var sentiCard = buildCard("ST-07", t("stock.sentiCard"), "", t("stock.sentiSub"));
    sentiCard.body.id = "st07-body";
    right.appendChild(sentiCard.card);
    var forecastCard = buildCard("ST-06", t("stock.forecastCard"), "", "");
    forecastCard.body.id = "st06-body";
    right.appendChild(forecastCard.card);
    grid.appendChild(right);

    // 通栏：ST-08 / ST-09 / ST-10
    var profileCard = buildCard("ST-08", t("stock.profileCard"), "g-12", "");
    profileCard.body.id = "st08-body";
    grid.appendChild(profileCard.card);

    var finCard = buildCard("ST-09", t("stock.finCard"), "g-12", "");
    finCard.body.id = "st09-body";
    grid.appendChild(finCard.card);

    // 第七轮 M7：FCF 与利润背离检查（对应八条原则第 8 条）
    var fcfCard = buildCard("ST-11", t("stock.fcfCard"), "g-12", t("stock.fcfCardSub"));
    fcfCard.body.id = "st11-body";
    grid.appendChild(fcfCard.card);

    var holderCard = buildCard("ST-10", t("stock.holderCard"), "g-12", "");
    holderCard.body.id = "st10-body";
    grid.appendChild(holderCard.card);
    container.appendChild(grid);

    tabRow.addEventListener("click", function (event) {
      var button = event.target.closest(".pg-tab");
      if (!button) {
        return;
      }
      state.stock.tab = button.getAttribute("data-tab");
      syncStockTabs();
      paintStockChart();
    });
    go.addEventListener("click", function () {
      applyStockInput();
    });
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        applyStockInput();
      }
    });
    marketSelect.addEventListener("change", function () {
      state.stock.market = marketSelect.value;
      applyStockInput();
    });
    refresh.addEventListener("click", function () {
      loadStock(true);
    });
    // W-03：自选星标（写入/取消），与 #/watchlist 形成闭环
    var star = el("button", "pg-btn", t("watchlist.starAdd"));
    star.type = "button";
    star.id = "stock-star";
    toolbar.appendChild(star);
    star.addEventListener("click", function () {
      toggleWatchlist(star);
    });
    refreshStar();
  }

  /** 刷新个股页星标状态（是否已在自选）。
   * @returns {void}
   */
  function refreshStar() {
    var star = document.getElementById("stock-star");
    if (!star) {
      return;
    }
    api("/api/watchlist").then(function (data) {
      var exists = (data.items || []).some(function (item) {
        return item.symbol === state.stock.symbol && item.market === state.stock.market;
      });
      star.textContent = exists ? t("watchlist.starRemove") : t("watchlist.starAdd");
      star.setAttribute("data-in-list", exists ? "1" : "0");
    }).catch(function () {
      star.textContent = t("watchlist.starAdd");
    });
  }

  /** 切换当前个股的自选状态并提示。
   * @param {HTMLElement} star 星标按钮
   * @returns {void}
   */
  function toggleWatchlist(star) {
    var symbol = state.stock.symbol;
    var market = state.stock.market;
    var inList = star && star.getAttribute("data-in-list") === "1";
    var request = inList
      ? sendJson("DELETE", "/api/watchlist/" + encodeURIComponent(symbol) + "?market=" + encodeURIComponent(market))
      : sendJson("POST", "/api/watchlist", { symbol: symbol, market: market, name: "" });
    request.then(function () {
      toast(inList ? t("watchlist.removed") : t("watchlist.added"));
      refreshStar();
    }).catch(function (error) {
      toast(error.message || t("state.errorHint"));
    });
  }

  /** 读取输入框并跳转（保持 URL 参数与页面状态一致）。 */
  function applyStockInput() {
    var input = document.getElementById("stock-symbol");
    var code = (input && input.value ? input.value : "").trim();
    if (!code) {
      toast(t("stock.codeRequired"));
      return;
    }
    // R3-28：市场以 select 当前值为准（程序化改值不触发 change，读 state 会拿旧值）
    var marketSelect = document.getElementById("stock-market");
    var market = (marketSelect && marketSelect.value) || state.stock.market;
    state.stock.market = market;
    // 先写全局共享 store，再改 hash——确保其它页面无参进入时带回本次标的
    if (window.SymbolStore) {
      window.SymbolStore.set(code, market);
    }
    window.location.hash =
      "#/stock?symbol=" + encodeURIComponent(code) + "&market=" + market;
  }

  /** 同步 K 线/分时 Tab 选中态与标题。 */
  function syncStockTabs() {
    // 卡片 id 在 ST-02 / ST-03 之间切换，故用 data-block 锚点定位
    var card = document.querySelector('[data-block="chart"]');
    if (!card) {
      return;
    }
    card.querySelectorAll(".pg-tab").forEach(function (button) {
      button.classList.toggle("active", button.getAttribute("data-tab") === state.stock.tab);
    });
    card.id = state.stock.tab === "minute" ? "ST-03" : "ST-02";
    var title = card.querySelector(".pg-card-title");
    if (title) {
      title.textContent = state.stock.tab === "minute" ? t("stock.minuteTitle") : t("stock.klineTitle");
    }
  }

  var stockCache = { key: "", panel: null };

  /** 渲染基金组合页（第七轮 M5）：诊断（读台账）与评估（输入代码）两个 Tab。
   *
   * **合规边界**：只做筛选与结构诊断，不输出预期收益、不给买入评级；
   * 页面固定展示「非投资建议」徽章，禁用「推荐/最优」措辞。
   *
   * @returns {void} 无返回值
   */
  function renderFundCombo() {
    var page = document.getElementById(VIEW_IDS["fund-combo"]);
    if (!page) {
      return;
    }
    page.innerHTML = "";
    var tab = state.fundCombo && state.fundCombo.tab ? state.fundCombo.tab : "diagnose";
    var bar = el("div", "page-toolbar");
    [["diagnose", t("fundCombo.tab.diagnose")], ["evaluate", t("fundCombo.tab.evaluate")], ["candidates", t("combo.candidates.tab")]].forEach(function (pair) {
      var btn = el("button", "pg-tab" + (tab === pair[0] ? " active" : ""), pair[1]);
      btn.type = "button";
      btn.addEventListener("click", function () {
        state.fundCombo = state.fundCombo || {};
        state.fundCombo.tab = pair[0];
        renderFundCombo();
      });
      bar.appendChild(btn);
    });
    bar.appendChild(el("span", "pg-spacer"));
    bar.appendChild(el("span", "badge legal", t("fundCombo.disclaimerBadge")));
    page.appendChild(bar);

    var host = el("div", "");
    page.appendChild(host);
    if (tab === "evaluate") {
      paintFundComboEvaluate(host);
    } else if (tab === "candidates") {
      paintFundComboCandidates(host);
    } else {
      paintFundComboDiagnose(host);
    }
    page.setAttribute("data-state", "default");
  }

  /** 诊断 Tab：读台账里的基金持仓，给实际 vs 目标权重、相关性、偏离。
   *
   * @param {HTMLElement} host 容器
   * @returns {void} 无返回值（异步渲染）
   */
  function paintFundComboDiagnose(host) {
    host.innerHTML = "";
    host.appendChild(el("div", "pg-state", t("state.loading")));
    api("/api/fund/combo/diagnose", {}).then(function (data) {
      host.innerHTML = "";
      var holdings = data.holdings || [];
      if (!holdings.length) {
        host.appendChild(el("div", "pg-state", (data.missing || []).join("；") || t("fundCombo.empty")));
        host.setAttribute("data-state", "empty");
        return;
      }

      // KPI：只放客观量，不放"预期收益"
      var kpis = el("div", "pg-kpis");
      var totalActual = holdings.reduce(function (sum, row) { return sum + (row.actual_weight || 0); }, 0);
      var avgScore = holdings.reduce(function (sum, row) { return sum + (row.score || 0); }, 0) / holdings.length;
      [
        [t("fundCombo.kpi.count"), String(holdings.length), "flat", t("fundCombo.kpi.countTag")],
        [t("fundCombo.kpi.actual"), fmt(totalActual, 2) + "%", "flat", t("fundCombo.kpi.actualTag")],
        [t("fundCombo.kpi.avgScore"), fmt(avgScore, 1), "flat", t("fundCombo.kpi.scoreTag")],
        [t("fundCombo.kpi.deviation"), String((data.deviation || []).length), "flat", t("fundCombo.kpi.devTag")]
      ].forEach(function (row) {
        var box = el("div", "pg-kpi");
        box.appendChild(el("div", "pg-kpi-k", row[0]));
        box.appendChild(el("div", "pg-kpi-v " + row[2], row[1]));
        if (row[3]) {
          box.appendChild(el("div", "pg-kpi-x", row[3]));
        }
        kpis.appendChild(box);
      });
      host.appendChild(kpis);

      // 权重对比
      var targetMap = {};
      (data.target || []).forEach(function (row) { targetMap[row.code] = row.weight; });
      var weightCard = el("div", "pg-card");
      weightCard.appendChild(el("div", "pg-card-title", t("fundCombo.card.weight")));
      holdings.forEach(function (row) {
        var line = el("div", "wt");
        line.appendChild(el("span", "n", row.code + (row.name ? " " + row.name : "")));
        var barBox = el("span", "b");
        var bar = el("div", "bar");
        var fill = el("i", "");
        var width = Math.min(100, row.actual_weight || 0);
        fill.style.width = width + "%";
        if ((row.actual_weight || 0) > (data.constraints.single_cap * 100)) {
          fill.classList.add("over");
        }
        bar.appendChild(fill);
        barBox.appendChild(bar);
        line.appendChild(barBox);
        var target = targetMap[row.code];
        // 权重不是涨跌幅，不能用 fmtPct（会给正数加「+」号）
        line.appendChild(el("span", "v",
          fmt(row.actual_weight, 2) + "%" + (target !== undefined ? " / " + fmt(target, 2) + "%" : "")));
        var gap = target !== undefined ? (row.actual_weight || 0) - target : null;
        var gapNode = el("span", "d" + (gap === null ? "" : (gap > 0 ? " up" : gap < 0 ? " down" : "")),
          gap === null ? "—" : (gap > 0 ? "+" : "") + fmt(gap, 1) + "pt");
        line.appendChild(gapNode);
        weightCard.appendChild(line);
      });
      weightCard.appendChild(el("div", "pg-card-sub",
        t("fundCombo.weightNote", { single: fmt(data.constraints.single_cap * 100, 0), cash: fmt(data.constraints.cash_floor * 100, 0) })));
      host.appendChild(weightCard);

      // 相关性
      var corr = (data.correlation || []).filter(function (row) { return row.value !== null; });
      if (corr.length) {
        var corrCard = el("div", "pg-card");
        corrCard.appendChild(el("div", "pg-card-title", t("fundCombo.card.corr")));
        corr.forEach(function (row) {
          var line = el("div", "portfolio-scen");
          line.appendChild(el("span", "", row.left + " ↔ " + row.right));
          line.appendChild(el("b", row.value > 0.90 ? "down" : "", fmt(row.value, 3)
            + (row.value > 0.90 ? t("fundCombo.corrHigh") : "")));
          corrCard.appendChild(line);
        });
        host.appendChild(corrCard);
      }

      // 偏离提示
      var dev = data.deviation || [];
      if (dev.length) {
        var devCard = el("div", "pg-card");
        devCard.appendChild(el("div", "pg-card-title", t("fundCombo.card.dev")));
        dev.forEach(function (row) {
          var line = el("div", "alert-row");
          line.appendChild(el("i", "dot-w"));
          line.appendChild(el("span", "",
            row.code + "：" + t("fundCombo.devLine", { actual: fmt(row.actual, 2), target: fmt(row.target, 2), gap: fmt(row.gap, 1) })));
          devCard.appendChild(line);
        });
        host.appendChild(devCard);
      }

      if (data.cash_note) {
        host.appendChild(el("div", "note warn", data.cash_note));
      }
      (data.missing || []).forEach(function (note) {
        host.appendChild(el("div", "pg-card-sub", note));
      });
      host.appendChild(el("div", "pg-card-sub", data.caliber || ""));
      host.appendChild(el("div", "note legal", data.disclaimer || ""));
      host.setAttribute("data-state", "default");
      // 第七轮 M14：权重明细（基础权重 × 拥挤度折扣）
      paintComboWeight(host, holdings.map(function (row) { return row.code; }));
    }).catch(function (error) {
      host.innerHTML = "";
      host.appendChild(el("div", "pg-state", error.message || t("state.errorHint")));
      host.setAttribute("data-state", "error");
    });
  }

  // =============================================================
  // 第七轮 M14：权重明细（基础权重 × 拥挤度折扣因子）
  // 拥挤度代理 = 该基金第一大重仓股所属行业的同花顺板块分数（调 M3 同一函数）
  // =============================================================
  /** 渲染权重明细表（附在诊断 Tab 下方）。
   *
   * @param {HTMLElement} host 容器
   * @param {Array<string>} codes 基金代码列表
   * @returns {void} 无返回值（异步渲染）
   */
  function paintComboWeight(host, codes) {
    if (!codes || codes.length < 2) {
      return;
    }
    var wrap = el("div", "pg-card");
    wrap.appendChild(el("div", "pg-card-title", t("combo.weight.title")));
    wrap.appendChild(el("div", "pg-card-sub", t("combo.weight.loading")));
    host.appendChild(wrap);
    api("/api/fund/combo/weight", { codes: codes.join(","), risk: "balanced", crowd: "true" }).then(function (data) {
      wrap.innerHTML = "";
      wrap.appendChild(el("div", "pg-card-title", t("combo.weight.title")));
      var totals = data.totals || {};
      var scroll = el("div", "table-scroll");
      renderTable(scroll, [
        { key: "code", title: t("combo.weight.col.code"), render: function (row) { return el("b", "", row.code); } },
        { key: "base_weight", title: t("combo.weight.col.base"), num: true, render: function (row) { return fmt(row.base_weight, 2) + "%"; } },
        { key: "board", title: t("combo.weight.col.board"), render: function (row) { return row.board || t("value.none"); } },
        { key: "crowding", title: t("combo.weight.col.crowd"), num: true, render: function (row) { return row.crowding === null ? t("value.none") : fmt(row.crowding, 1); } },
        { key: "crowding_source", title: t("combo.weight.col.crowdsrc"), render: function (row) { return row.crowding_source || t("value.none"); } },
        { key: "discount", title: t("combo.weight.col.discount"), num: true, render: function (row) {
            return el("span", row.discount < 0.999 ? "down" : "flat", "×" + fmt(row.discount, 2));
          } },
        { key: "final_weight", title: t("combo.weight.col.final"), num: true, render: function (row) { return el("b", "", fmt(row.final_weight, 2) + "%"); } },
        { key: "delta", title: t("combo.weight.col.delta"), num: true, render: function (row) {
            return el("span", row.delta < 0 ? "down" : (row.delta > 0 ? "up" : "flat"),
              (row.delta > 0 ? "+" : "") + fmt(row.delta, 2) + "pt");
          } }
      ], data.items || [], {});
      wrap.appendChild(scroll);
      wrap.appendChild(el("div", "pg-card-sub",
        t("combo.weight.totals", {
          base: fmt(totals.base, 2),
          final: fmt(totals.final, 2),
          cash: fmt(totals.cash, 2)
        })));
      if ((data.missing || []).length) {
        wrap.appendChild(el("div", "pg-card-sub", data.missing.join("；")));
      }
      wrap.appendChild(el("div", "pg-card-sub", data.caliber || ""));
      wrap.appendChild(el("div", "note legal", data.disclaimer || ""));
    }).catch(function (error) {
      wrap.innerHTML = "";
      wrap.appendChild(el("div", "pg-card-title", t("combo.weight.title")));
      wrap.appendChild(el("div", "pg-card-sub", error.message || t("state.errorHint")));
    });
  }

  // =============================================================
  // 第七轮 M13：全市场候选池（复用 M9 快照 + 基金友好模板）
  // =============================================================
  /** 渲染候选池 Tab：模板条件 + 候选结果表（只筛选，不排序打分）。
   *
   * @param {HTMLElement} host 容器
   * @returns {void} 无返回值（异步渲染）
   */
  function paintFundComboCandidates(host) {
    host.innerHTML = "";
    host.appendChild(el("div", "pg-state", t("combo.candidates.loading")));
    api("/api/fund/candidates", { limit: 200 }).then(function (data) {
      host.innerHTML = "";
      var tpl = data.template || {};
      var condCard = el("div", "pg-card");
      condCard.appendChild(el("div", "pg-card-title", t("combo.candidates.condTitle")));
      var condScroll = el("div", "table-scroll");
      renderTable(condScroll, [
        { key: "factor", title: t("combo.candidates.col.factor"), render: function (row) { return row.label || row.factor; } },
        { key: "op", title: t("combo.candidates.col.op"), render: function (row) { return row.op === "gte" ? "≥" : "≤"; } },
        { key: "value", title: t("combo.candidates.col.value"), num: true, render: function (row) { return fmt(row.value, 0); } },
        { key: "note", title: t("combo.candidates.col.note") }
      ], tpl.rules || [], {});
      condCard.appendChild(condScroll);
      condCard.appendChild(el("div", "pg-card-sub", t("combo.candidates.tplNote")));
      host.appendChild(condCard);

      var items = data.items || [];
      var resultCard = el("div", "pg-card");
      resultCard.appendChild(el("div", "pg-card-title",
        t("combo.candidates.resultTitle", { count: data.total_matched || 0 })));
      if (!items.length) {
        resultCard.appendChild(el("div", "pg-state", t("combo.candidates.empty")));
        host.appendChild(resultCard);
        host.setAttribute("data-state", "empty");
        return;
      }
      var scroll = el("div", "table-scroll");
      renderTable(scroll, [
        { key: "code", title: t("scr.col.code"), render: function (row) {
            var box = el("span", "");
            box.appendChild(el("b", "", row.code));
            box.appendChild(document.createTextNode(" " + (row.name || "")));
            return box;
          } },
        { key: "board", title: t("scr.col.board") },
        { key: "pe", title: "PE", num: true, render: function (row) { return fmt(row.pe, 2); } },
        { key: "roe3y_min", title: "ROE3年最弱", num: true, render: function (row) { return fmt(row.roe3y_min, 2); } },
        { key: "gross", title: t("scr.col.gross"), num: true, render: function (row) { return fmt(row.gross, 2); } },
        { key: "debt_ratio", title: t("scr.col.debt"), num: true, render: function (row) { return fmt(row.debt_ratio, 2); } },
        { key: "mktcap", title: t("scr.col.mktcap"), num: true, render: function (row) { return fmt(row.mktcap, 0); } },
        { key: "dividend_yield", title: t("scr.col.dividend"), num: true, render: function (row) {
            return row.dividend_yield === null || row.dividend_yield === undefined ? t("value.none")
              : fmt(row.dividend_yield, 2) + "%";
          } },
        { key: "exempt", title: t("scr.col.exempt"), render: function (row) {
            return (row.exempt && row.exempt.length) ? t("scr.exemptYes") : "";
          } }
      ], items, {});
      resultCard.appendChild(scroll);
      (data.missing || []).forEach(function (note) {
        resultCard.appendChild(el("div", "pg-card-sub", note));
      });
      resultCard.appendChild(el("div", "pg-card-sub", data.caliber || ""));
      host.appendChild(resultCard);
      host.setAttribute("data-state", "default");
    }).catch(function (error) {
      host.innerHTML = "";
      host.appendChild(el("div", "pg-state", error.message || t("state.errorHint")));
      host.setAttribute("data-state", "error");
    });
  }

  /** 评估 Tab：输入基金代码 → 六维打分 + 相关性 + 目标权重。
   *
   * @param {HTMLElement} host 容器
   * @returns {void} 无返回值
   */
  function paintFundComboEvaluate(host) {
    host.innerHTML = "";
    var bar = el("div", "page-toolbar");
    var input = document.createElement("input");
    input.type = "text";
    input.className = "wl-addbar-input";
    input.id = "fc-codes";
    input.placeholder = t("fundCombo.evalPlaceholder");
    input.value = (state.fundCombo && state.fundCombo.codes) || "";
    bar.appendChild(input);
    var select = document.createElement("select");
    select.className = "fs-select";
    select.id = "fc-risk";
    [["balanced", t("fundCombo.risk.balanced")], ["conservative", t("fundCombo.risk.conservative")], ["aggressive", t("fundCombo.risk.aggressive")]].forEach(function (pair) {
      var option = document.createElement("option");
      option.value = pair[0];
      option.textContent = pair[1];
      select.appendChild(option);
    });
    select.value = (state.fundCombo && state.fundCombo.risk) || "balanced";
    bar.appendChild(select);
    var btn = el("button", "pg-btn primary", t("fundCombo.evalRun"));
    btn.type = "button";
    bar.appendChild(btn);
    host.appendChild(bar);

    var result = el("div", "");
    host.appendChild(result);

    /** 执行评估。
     * @returns {void} 无返回值
     */
    function run() {
      var codes = (input.value || "").trim();
      if (!codes) {
        toast(t("fundCombo.needCodes"));
        return;
      }
      state.fundCombo = state.fundCombo || {};
      state.fundCombo.codes = codes;
      state.fundCombo.risk = select.value;
      result.innerHTML = "";
      result.appendChild(el("div", "pg-state", t("fundCombo.evaling")));
      api("/api/fund/combo/evaluate", { codes: codes, risk: select.value }).then(function (data) {
        result.innerHTML = "";
        var weightMap = {};
        (data.weights || []).forEach(function (row) { weightMap[row.code] = row; });
        renderTable(result, [
          { title: t("fundCombo.col.code"), cls: "l",
            render: function (row) { return el("b", "", row.code); } },
          { title: t("fundCombo.col.score"), num: true,
            render: function (row) { return row.score === null ? "—" : fmt(row.score, 1); } },
          { title: t("fundCombo.col.annual"), num: true,
            render: function (row) {
              var value = row.stats.annual_return;
              return el("span", pctClass(value), fmtPct(value));
            } },
          { title: t("fundCombo.col.dd"), num: true,
            render: function (row) { return el("span", "down", fmtPct(row.stats.max_drawdown)); } },
          { title: t("fundCombo.col.sharpe"), num: true,
            render: function (row) { return row.stats.sharpe === null ? "—" : fmt(row.stats.sharpe, 2); } },
          { title: t("fundCombo.col.rank"), num: true,
            render: function (row) { return row.rank_pct === null ? "—" : fmt(row.rank_pct, 1) + "%"; } },
          { title: t("fundCombo.col.target"), num: true,
            render: function (row) {
              var hit = weightMap[row.code];
              if (!hit) {
                return "—";
              }
              var node = el("span", "", fmt(hit.weight, 2) + "%");
              if (hit.capped_by) {
                node.title = hit.capped_by;
              }
              return node;
            } }
        ], data.items || [], null);

        (data.correlation || []).forEach(function (row) {
          if (row.value !== null && row.value > 0.90) {
            result.appendChild(el("div", "pg-card-sub",
              t("fundCombo.highCorrLine", { left: row.left, right: row.right, value: fmt(row.value, 3) })));
          }
        });
        result.appendChild(el("div", "pg-card-sub", data.caliber || ""));
        (data.missing || []).forEach(function (note) {
          result.appendChild(el("div", "pg-card-sub", note));
        });
        result.appendChild(el("div", "note legal", data.disclaimer || ""));
      }).catch(function (error) {
        result.innerHTML = "";
        result.appendChild(el("div", "pg-state", error.message || t("state.errorHint")));
      });
    }

    btn.addEventListener("click", run);
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        run();
      }
    });
  }

  /** 渲染板块拥挤度独立页（第七轮 M3 目录重排后从板块页提升）。
   *
   * 阈值按用户口径：>60 提示、>80 建议复核。只陈述交易结构位置，不给买卖指令。
   * 缺失维度在接口 missing 里逐条说明，界面原样呈现（不隐藏降级事实）。
   *
   * @returns {void} 无返回值
   */
  function renderCrowding() {
    var page = document.getElementById(VIEW_IDS.crowding);
    if (!page) {
      return;
    }
    page.innerHTML = "";
    var grid = el("div", "pg-grid");
    var card = buildCard("M3-01", t("sectors.crowd.title"), "g-12", t("sectors.crowd.sub"));
    card.body.id = "m301-body";
    grid.appendChild(card.card);
    // 第七轮 M12：全板块排行（与 M3-01 同一打分函数，全市场同口径）
    var rankCard = buildCard("M12-01", t("crowd.rank.title"), "g-12", t("crowd.rank.sub"));
    rankCard.body.id = "m1201-body";
    // 行业/概念口径切换（补齐概念排行，不降级）
    var kindBar = el("div", "page-toolbar");
    [["industry", t("sectors.kind.industry")], ["concept", t("sectors.kind.concept")]].forEach(function (item) {
      var btn = el("button", "nav-item" + (state.crowdingKind === item[0] ? " active" : ""));
      btn.textContent = item[1];
      btn.addEventListener("click", function () {
        if (state.crowdingKind === item[0]) {
          return;
        }
        state.crowdingKind = item[0];
        paintCrowdingRankings();
      });
      kindBar.appendChild(btn);
    });
    rankCard.card.insertBefore(kindBar, rankCard.body);
    grid.appendChild(rankCard.card);
    page.appendChild(grid);
    paintCrowding();
    paintCrowdingRankings();
  }

  // =============================================================
  // 第七轮 M12：拥挤度全板块排行（复用 M3 同一函数，首次扫描约 1 分钟）
  // =============================================================
  /** 渲染全板块排行（排行榜 + 等级分布 + 热力条）。
   *
   * @returns {void} 无返回值（异步渲染）
   */
  function paintCrowdingRankings() {
    var body = document.getElementById("m1201-body");
    if (!body) {
      return;
    }
    body.innerHTML = "";
    body.appendChild(el("div", "pg-state", t("crowd.rank.loading")));
    body.setAttribute("data-state", "loading");
    api("/api/sector/crowding/rankings", { kind: state.crowdingKind, limit: 30 }).then(function (data) {
      body.innerHTML = "";
      // 后台计算中：不冒充结果，显示计算中态并稍后自动重试
      if (data.computing) {
        body.appendChild(el("div", "pg-state", data.caliber || t("crowd.rank.loading")));
        body.setAttribute("data-state", "loading");
        setTimeout(paintCrowdingRankings, 8000);
        return;
      }
      var counts = data.level_counts || {};
      var kpis = el("div", "pg-kpis");
      [
        [t("crowd.rank.scanned"), (data.scanned || 0) + " / " + (data.total || 0), "flat"],
        [t("crowd.rank.high"), String(counts["高位"] || 0), (counts["高位"] ? "up" : "flat")],
        [t("crowd.rank.warn"), String(counts["偏高"] || 0), "flat"],
        [t("crowd.rank.normal"), String(counts["正常"] || 0), "down"]
      ].forEach(function (row) {
        var box = el("div", "pg-kpi");
        box.appendChild(el("div", "pg-kpi-k", row[0]));
        box.appendChild(el("div", "pg-kpi-v " + row[2], row[1]));
        kpis.appendChild(box);
      });
      body.appendChild(kpis);

      var items = data.items || [];
      if (!items.length) {
        body.appendChild(el("div", "pg-state", t("crowd.rank.empty")));
        body.setAttribute("data-state", "empty");
        return;
      }
      var scroll = el("div", "table-scroll");
      renderTable(scroll, [
        { key: "rank", title: "#", num: true, render: function (row) { return String(items.indexOf(row) + 1); } },
        { key: "board", title: t("crowd.rank.col.board"), render: function (row) { return el("b", "", row.board); } },
        { key: "score", title: t("crowd.rank.col.score"), num: true, render: function (row) {
            var cls = row.score >= 80 ? "up" : (row.score >= 60 ? "flat" : "down");
            return el("span", cls, fmt(row.score, 1));
          } },
        { key: "level", title: t("crowd.rank.col.level"), render: function (row) { return row.level; } },
        { key: "parts", title: t("crowd.rank.col.parts"), render: function (row) {
            return (row.parts || []).map(function (part) {
              return part.label + " " + fmt(part.pct, 0) + "%";
            }).join(" · ");
          } }
      ], items, {});
      body.appendChild(scroll);

      // 热力条：颜色越深越拥挤（橙→红，非红涨绿跌语义）
      var heat = el("div", "crowd-heat");
      items.slice(0, 30).forEach(function (row) {
        var cell = el("span", "crowd-heat-cell", row.board);
        var ratio = Math.min(100, Math.max(0, row.score)) / 100;
        cell.style.background = "rgba(224,90,62," + (0.12 + ratio * 0.75).toFixed(2) + ")";
        cell.title = row.board + " " + fmt(row.score, 1) + "（" + row.level + "）";
        heat.appendChild(cell);
      });
      body.appendChild(heat);

      if ((data.failed || []).length) {
        body.appendChild(el("div", "pg-card-sub", t("crowd.rank.failed", { list: data.failed.join("、") })));
      }
      body.appendChild(el("div", "pg-card-sub", data.caliber || ""));
      body.setAttribute("data-state", "default");
    }).catch(function (error) {
      setError(body, error.message || t("state.errorHint"), paintCrowdingRankings);
      body.setAttribute("data-state", "error");
    });
  }

  /** 渲染拥挤度查询区（输入 + 结果 + 缺失说明）。
   *
   * @returns {void} 无返回值
   */
  function paintCrowding() {
    var body = document.getElementById("m301-body");
    if (!body) {
      return;
    }
    body.innerHTML = "";
    var bar = el("div", "page-toolbar");
    var input = document.createElement("input");
    input.type = "text";
    input.className = "wl-addbar-input";
    input.id = "crowd-board";
    input.placeholder = t("sectors.crowd.placeholder");
    input.value = state.sectors.crowdBoard || "";
    bar.appendChild(input);
    var btn = el("button", "pg-btn primary", t("sectors.crowd.query"));
    btn.type = "button";
    bar.appendChild(btn);
    bar.appendChild(el("span", "pg-card-sub", t("sectors.crowd.hint")));
    body.appendChild(bar);

    var result = el("div", "");
    body.appendChild(result);
    body.setAttribute("data-state", "default");

    /** 查询指定板块。
     * @returns {void} 无返回值
     */
    function run() {
      var board = (input.value || "").trim();
      if (!board) {
        toast(t("sectors.crowd.needBoard"));
        return;
      }
      state.sectors.crowdBoard = board;
      result.innerHTML = "";
      result.appendChild(el("div", "pg-state", t("state.loading")));
      api("/api/sector/crowding", { board: board }).then(function (data) {
        var card = document.getElementById("M3-01");
        setBadges(card, "同花顺行业日线", data.caliber);
        result.innerHTML = "";
        var head = el("div", "pg-kpis");
        var main = el("div", "pg-kpi");
        main.appendChild(el("div", "pg-kpi-k", t("sectors.crowd.score")));
        main.appendChild(el("div", "pg-kpi-v " + (data.score >= 80 ? "up" : data.score >= 60 ? "flat" : "down"),
          fmt(data.score, 1)));
        main.appendChild(el("div", "pg-kpi-x", data.level + " · " + data.advice));
        head.appendChild(main);
        result.appendChild(head);

        var list = el("div", "crowd-parts");
        (data.parts || []).forEach(function (part) {
          var line = el("div", "crowd-part");
          line.appendChild(el("span", "crowd-part-name", part.label));
          var track = el("div", "crowd-part-bar");
          var fill = el("i", "");
          fill.style.width = (part.pct === null || part.pct === undefined ? 0 : part.pct) + "%";
          track.appendChild(fill);
          line.appendChild(track);
          line.appendChild(el("span", "crowd-part-val",
            t("sectors.crowd.rankLabel") + fmt(part.pct, 1) + "%"));
          line.appendChild(el("span", "crowd-part-raw", fmt(part.value, 2) + part.unit));
          list.appendChild(line);
        });
        result.appendChild(list);

        (data.missing || []).forEach(function (note) {
          result.appendChild(el("div", "pg-card-sub", note));
        });
        body.setAttribute("data-state", "default");
      }).catch(function (error) {
        result.innerHTML = "";
        result.appendChild(el("div", "pg-state", error.message || t("state.errorHint")));
        body.setAttribute("data-state", "error");
      });
    }

    btn.addEventListener("click", run);
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        run();
      }
    });
  }

  /** 渲染个股页。
   * @param {Object} params 查询参数对象
   */
  function renderStock(params) {
    var container = document.getElementById(VIEW_IDS.stock);
    state.stock.symbol = params.symbol || state.stock.symbol;
    state.stock.market = params.market || state.stock.market;
    // R3-28/R3-30：若无 URL 参数，自共享 store 带出最近标的
    if (!params.symbol && !params.market && window.SymbolStore) {
      var shared = window.SymbolStore.get();
      state.stock.symbol = shared.symbol || state.stock.symbol;
      state.stock.market = shared.market || state.stock.market;
    }
    if (!container.querySelector("#stock-grid")) {
      buildStockShell(container);
    }
    var input = document.getElementById("stock-symbol");
    var select = document.getElementById("stock-market");
    if (input) {
      input.value = state.stock.symbol;
    }
    if (select) {
      select.value = state.stock.market;
    }
    // R3-30：订阅共享 store——切页带代码自动同步到本页输入框
    if (window.SymbolStore && window.SymbolStore.subscribe && !input.dataset.symbolStoreSubscribed) {
      input.dataset.symbolStoreSubscribed = "1";
      window.SymbolStore.subscribe(function (symbol, market) {
        var inp = document.getElementById("stock-symbol");
        var sel = document.getElementById("stock-market");
        if (inp) inp.value = symbol;
        if (sel) sel.value = market;
        state.stock.symbol = symbol;
        state.stock.market = market;
      });
    }
    syncStockTabs();
    loadStock();
    loadStockFcf();
  }

  /** 拉取并渲染个股页（面板 + 概况 + 股东 + 财务摘要/趋势）。
   * @param {boolean} force 是否强制刷新（true 时跳过本地缓存）
   * @returns {*} 处理结果
   */
  /** 渲染 FCF 与利润背离检查（第七轮 M7）。
   *
   * 六项检查带状态色（ok/warn/bad/unknown），金融业返回「不适用」并说明原因，
   * 不把银行的存款波动当成经营现金流硬算。
   *
   * @returns {void} 无返回值（异步渲染）
   */
  function loadStockFcf() {
    var body = document.getElementById("st11-body");
    if (!body) {
      return;
    }
    var symbol = state.stock.symbol;
    if (!symbol) {
      setEmpty(body, t("stock.fcfNeedSymbol"));
      return;
    }
    setLoading(body);
    api("/api/stock/fcf-health", { symbol: symbol }).then(function (data) {
      body.innerHTML = "";
      var card = document.getElementById("ST-11");
      setBadges(card, "新浪三表", data.caliber);
      if (data.applicable === false) {
        body.appendChild(el("div", "note warn", data.reason || t("stock.fcfNotApplicable")));
        body.appendChild(el("div", "pg-card-sub", data.caliber || ""));
        body.setAttribute("data-state", "empty");
        return;
      }
      var counters = data.counters || {};
      var head = el("div", "pg-kpis");
      [
        [t("stock.fcfPeriod"), data.period || "—", "flat", "vs " + (data.compare_period || "—")],
        [t("stock.fcfBad"), String(counters.bad || 0), counters.bad ? "up" : "flat", ""],
        [t("stock.fcfWarn"), String(counters.warn || 0), counters.warn ? "flat" : "flat", ""],
        [t("stock.fcfVerdict"), data.verdict || "—", "flat", ""]
      ].forEach(function (row) {
        var box = el("div", "pg-kpi");
        box.appendChild(el("div", "pg-kpi-k", row[0]));
        box.appendChild(el("div", "pg-kpi-v " + row[2], row[1]));
        if (row[3]) {
          box.appendChild(el("div", "pg-kpi-x", row[3]));
        }
        head.appendChild(box);
      });
      body.appendChild(head);

      var list = el("div", "fcf-checks");
      (data.checks || []).forEach(function (item) {
        var row = el("div", "fcf-row is-" + item.status);
        row.appendChild(el("span", "fcf-label", item.label));
        // 小数位按量级自适应：五粮液 OCF/净利 = 0.005，两位会显示成 0.00 像缺值
        var digits = item.value === null || item.value === undefined
          ? 2 : (Math.abs(item.value) < 0.1 ? 3 : 2);
        row.appendChild(el("span", "fcf-value",
          item.value === null || item.value === undefined
            ? "—" : fmt(item.value, digits) + item.unit));
        row.appendChild(el("span", "fcf-note", item.note || ""));
        list.appendChild(row);
      });
      body.appendChild(list);

      (data.missing || []).forEach(function (note) {
        body.appendChild(el("div", "pg-card-sub", note));
      });
      body.setAttribute("data-state", "default");
    }).catch(function (error) {
      setError(body, error.message || t("state.errorHint"), loadStockFcf);
    });
  }

  function loadStock(force) {
    var symbol = state.stock.symbol;
    var market = state.stock.market;
    var key = symbol + "|" + market;
    var bodies = [
      "st01-body", "st02-body", "st04-body", "st05-body",
      "st06-body", "st07-body", "st08-body", "st09-body", "st10-body"
    ].map(function (id) {
      return document.getElementById(id);
    });
    bodies.forEach(function (body) {
      if (body) {
        setLoading(body);
      }
    });

    // 港股美股无 F10/财报通道，按 D3 如实空态，不发无效请求
    var tasks = [
      api("/api/stock/realtime-panel", { symbol: symbol, market: market }),
      api("/api/stock/profile", { symbol: symbol, market: market }),
      // 一致预期（F-027）：港美股由后端返回 available=false 空态，前端统一渲染
      api("/api/stock/estimates", { symbol: symbol, market: market })
    ];
    if (market === "a") {
      tasks.push(api("/api/stock/holders", { symbol: symbol }));
      tasks.push(api("/api/stock/financial-summary", { symbol: symbol }));
      tasks.push(api("/api/stock/financial-trend", { symbol: symbol, years: 5 }));
    } else {
      tasks.push(Promise.resolve(null));
      tasks.push(Promise.resolve(null));
      tasks.push(Promise.resolve(null));
    }

    Promise.all(tasks)
      .then(function (result) {
        stockCache = { key: key, panel: result[0], estimates: result[2] };
        paintStock({
          panel: result[0],
          profile: result[1],
          estimates: result[2],
          holders: result[3],
          summary: result[4],
          trend: result[5]
        });
      })
      .catch(function (error) {
        bodies.forEach(function (body) {
          if (body) {
            setError(body, error.message, function () {
              loadStock(true);
            });
          }
        });
      });
  }

  /** 渲染报价大卡（ST-01）。
   * @param {Object} panel 数据面板（报价/新闻/舆情等聚合块）
   */
  function paintQuote(panel) {
    var body = document.getElementById("st01-body");
    var card = document.getElementById("ST-01");
    if (!body) {
      return;
    }
    var quote = panel.quote;
    if (!quote) {
      setEmpty(body, t("stock.empty.quote"), function () {
        loadStock(true);
      });
      return;
    }
    setBadges(card, quote.source, quote.caliber, false);
    body.innerHTML = "";
    var main = el("div", "pg-quote-main");
    main.appendChild(el("span", "pg-quote-name", quote.name || t("value.none")));
    main.appendChild(el("span", "pg-quote-price " + pctClass(quote.change), fmt(quote.price, 2)));
    main.appendChild(el("span", pctClass(quote.change), fmt(quote.change, 2) + " / " + fmtPct(quote.pct)));
    // TZ-01：报价时间统一标注北京时区；美股/港股在悬浮里给收盘换算说明
    main.appendChild(timeWithBj(quote.quote_time, marketTzTip(state.stock.market), "pg-card-sub"));
    body.appendChild(main);
    var meta = el("div", "pg-quote-meta");
    [
      [t("stock.kv.open"), fmt(quote.open, 2)],
      [t("stock.kv.high"), fmt(quote.high, 2)],
      [t("stock.kv.low"), fmt(quote.low, 2)],
      [t("stock.kv.prevClose"), fmt(quote.prev_close, 2)],
      [t("stock.kv.turnover"), quote.turnover === null ? t("value.none") : fmt(quote.turnover, 2) + "%"],
      [t("stock.kv.totalMarketCap"), quote.total_market_cap === null ? t("value.none") : fmt(yi(quote.total_market_cap), 2) + " 亿"],
      [t("stock.kv.peTtm"), fmt(quote.pe_ttm, 2)],
      [t("stock.kv.marketStatus"), quote.market_status || t("value.none")]
    ].forEach(function (item) {
      var box = el("span", "", item[0] + "：");
      var value = el("b", "", item[1]);
      box.appendChild(value);
      meta.appendChild(box);
    });
    body.appendChild(meta);
    body.setAttribute("data-state", "default");
  }

  /** 渲染 K 线或分时（ST-02 / ST-03 互斥）。
   * @returns {*} 处理结果
   */
  function paintStockChart() {
    var body = document.getElementById("st02-body");
    if (!body || !stockCache.panel) {
      return;
    }
    var panel = stockCache.panel;
    body.innerHTML = "";
    var box = el("div", "pg-chart");
    body.appendChild(box);
    var chart = mountChart(box);
    if (!chart) {
      return;
    }
    var theme = chartTheme();

    if (state.stock.tab === "minute") {
      var minute = panel.minute || [];
      if (minute.length === 0) {
        setEmpty(body, t("stock.empty.minute"), function () {
          loadStock(true);
        });
        return;
      }
      // 午休断裂：在 11:30 与 13:00 之间插入空点，时间轴可见断口（P0-06）
      var categories = [];
      var prices = [];
      var avg = [];
      var sumPrice = 0;
      var sumVolume = 0;
      minute.forEach(function (item) {
        if (item.time > "11:30" && item.time < "13:00") {
          return;
        }
        categories.push(item.time);
        prices.push(item.price);
        sumPrice += item.price * (item.volume || 0);
        sumVolume += item.volume || 0;
        avg.push(sumVolume > 0 ? sumPrice / sumVolume : item.price);
      });
      var noonIndex = categories.findIndex(function (value) {
        return value >= "13:00";
      });
      if (noonIndex > 0) {
        categories.splice(noonIndex, 0, t("stock.chart.noon"));
        prices.splice(noonIndex, 0, null);
        avg.splice(noonIndex, 0, null);
      }
      chart.setOption({
        backgroundColor: "transparent",
        tooltip: { trigger: "axis" },
        legend: { textStyle: { color: theme.text }, top: 0 },
        grid: { left: 48, right: 16, top: 32, bottom: 24 },
        xAxis: {
          type: "category",
          data: categories,
          axisLabel: { color: theme.text, interval: Math.max(0, Math.floor(categories.length / 8)) },
          axisLine: { lineStyle: { color: theme.border } }
        },
        yAxis: {
          type: "value",
          scale: true,
          axisLabel: { color: theme.text },
          splitLine: { lineStyle: { color: theme.border, opacity: 0.4 } }
        },
        series: [
          { name: t("stock.chart.price"), type: "line", data: prices, showSymbol: false, lineStyle: { width: 1.4 }, color: COLOR_UP },
          { name: t("stock.chart.avg"), type: "line", data: avg, showSymbol: false, lineStyle: { width: 1, type: "dashed" }, color: "#e8a33d" }
        ]
      });
      body.setAttribute("data-state", "default");
      return;
    }

    var kline = panel.kline || [];
    if (kline.length === 0) {
      setEmpty(body, t("stock.empty.kline"), function () {
        loadStock(true);
      });
      return;
    }
    var dates = kline.map(function (item) {
      return item.date;
    });
    var ohlc = kline.map(function (item) {
      return [item.open, item.close, item.low, item.high];
    });
    var volumes = kline.map(function (item, index) {
      return { value: item.volume, itemStyle: { color: item.close >= item.open ? COLOR_UP : COLOR_DOWN } };
    });
    // P1-14：K线 option 脚手架委托 window.Utils.buildCandlestickOption
    chart.setOption(window.Utils.buildCandlestickOption(theme, dates, ohlc, volumes, {
      candleName: t("stock.kline.name")
    }));
    body.setAttribute("data-state", "default");
  }

  /** 渲染资金分布环形图（ST-04）。
   * @param {Object} panel 数据面板（报价/新闻/舆情等聚合块）
   * @returns {*} 处理结果
   */
  function paintFundDist(panel) {
    var body = document.getElementById("st04-body");
    var card = document.getElementById("ST-04");
    if (!body) {
      return;
    }
    var fund = panel.fund_dist;
    if (!fund) {
      setEmpty(body, t("stock.empty.fund"), function () {
        loadStock(true);
      });
      return;
    }
    setBadges(card, "新浪", fund.caliber_note || t("stock.fundCaliber"), false);
    body.innerHTML = "";
    var box = el("div", "pg-chart small");
    body.appendChild(box);
    var chart = mountChart(box);
    var tiers = fund.tiers || [];
    var total = tiers.reduce(function (acc, item) {
      return acc + Math.abs(item.net || 0);
    }, 0);
    if (chart) {
      chart.setOption({
        backgroundColor: "transparent",
        tooltip: {
          /** 四档资金流悬浮说明：档位 + 净额（亿元，4 位小数）+ 占比%。
           * @param {*} info 信息对象
           * @returns {*} 处理结果
           */
          formatter: function (info) {
            var item = tiers[info.dataIndex] || {};
            return item.name + "<br/>" + t("stock.fundDist.net") + "：" + fmt(item.net, 4) + " 亿元<br/>" + t("stock.fundDist.ratio") + "：" + fmt(item.ratio, 2) + "%";
          }
        },
        legend: { bottom: 0, textStyle: { color: chartTheme().text } },
        series: [
          {
            type: "pie",
            radius: ["45%", "68%"],
            center: ["50%", "45%"],
            avoidLabelOverlap: true,
            // 标签显示「档位 + 净额占比%」，金额进 tooltip，避免 0.0151 亿这类超长小数
            label: {
              color: chartTheme().text,
              fontSize: 11,
              /** 四档柱状图标签：档位换行 + 占比%（金额只进 tooltip）。
               * @param {*} info 信息对象
               * @returns {*} 处理结果
               */
              formatter: function (info) {
                var item = tiers[info.dataIndex] || {};
                var ratioText = item.ratio === null || item.ratio === undefined ? t("value.none") : fmt(item.ratio, 1) + "%";
                return info.name + "\n" + ratioText;
              }
            },
            data: tiers.map(function (item) {
              return {
                name: item.name,
                value: Math.abs(item.net || 0),
                ratio: item.ratio,
                itemStyle: {
                  color: (item.net || 0) >= 0 ? (total ? COLOR_UP : COLOR_MUTED) : COLOR_DOWN
                }
              };
            })
          }
        ]
      });
    }
    var foot = el("div", "pg-card-sub");
    foot.textContent =
      t("stock.fundDist.netFlow") + fmt(fund.main_net, 4) + " 亿元（占成交 " + fmt(fund.main_ratio, 2) + "%）";
    body.appendChild(foot);
    body.setAttribute("data-state", "default");
  }

  /** 渲染相关新闻（ST-05）。
   * @param {Object} panel 数据面板（报价/新闻/舆情等聚合块）
   */
  function paintNews(panel) {
    var body = document.getElementById("st05-body");
    if (!body) {
      return;
    }
    var news = panel.news || [];
    if (news.length === 0) {
      setEmpty(body, t("stock.empty.news"), function () {
        loadStock(true);
      });
      return;
    }
    body.innerHTML = "";
    var list = el("ul", "pg-news-list");
    news.forEach(function (item) {
      var li = el("li", "pg-news-item");
      var title = el("span", "pg-news-title", item.title);
      // 跳转 #5：标题 → #/news?symbol=…（带当前标的路由参数）
      title.addEventListener("click", function () {
        window.location.hash = "#/news?symbol=" + encodeURIComponent(state.stock.symbol);
        toast(t("news.pageRedirect"));
      });
      var chipClass = item.sentiment === t("stock.sentiment.up") ? "chip up" : item.sentiment === t("stock.sentiment.down") ? "chip down" : "chip";
      li.appendChild(title);
      li.appendChild(el("span", chipClass, item.sentiment || t("stock.sentiment.neutral")));
      li.appendChild(newsTime(item.time));
      list.appendChild(li);
    });
    body.appendChild(list);
    body.setAttribute("data-state", "default");
  }

  /** 渲染舆情面板（ST-07）：由新闻情绪标签聚合，确定性规则非 AI。
   * @param {Object} panel 数据面板（报价/新闻/舆情等聚合块）
   */
  function paintSentiment(panel) {
    var body = document.getElementById("st07-body");
    if (!body) {
      return;
    }
    var news = panel.news || [];
    if (news.length === 0) {
      setEmpty(body, t("stock.empty.senti"), function () {
        loadStock(true);
      });
      return;
    }
    var positive = news.filter(function (item) { return item.sentiment === t("stock.sentiment.up"); }).length;
    var negative = news.filter(function (item) { return item.sentiment === t("stock.sentiment.down"); }).length;
    var neutral = news.length - positive - negative;
    var score = news.reduce(function (acc, item) { return acc + (item.sentiment_score || 0); }, 0);
    var level = score > 1 ? t("stock.senti.level.bullish") : score < -1 ? t("stock.senti.level.bearish") : t("stock.sentiment.neutral");
    body.innerHTML = "";
    var rows = [
      [t("stock.senti.upCount"), positive, "up"],
      [t("stock.senti.downCount"), negative, "down"],
      [t("stock.senti.neutralCount"), neutral, "flat"],
      [t("stock.senti.score"), score, score > 0 ? "up" : score < 0 ? "down" : "flat"],
      [t("news.senti.level"), level, "flat"]
    ];
    var grid = el("div", "pg-metric-grid");
    rows.forEach(function (item) {
      var cell = el("div", "pg-metric-cell");
      cell.appendChild(el("div", "pg-metric-label", item[0]));
      cell.appendChild(el("div", "pg-metric-value " + item[2], item[1]));
      grid.appendChild(cell);
    });
    body.appendChild(grid);
    var note = el("div", "pg-card-sub", t("stock.senti.note", { count: news.length }));
    body.appendChild(note);
    body.setAttribute("data-state", "default");
  }

  /** 渲染分年度一致预期表（F-027：年度/机构数/EPS 均值与区间/净利均值/行业平均）。
   * @param {HTMLElement} body 区块容器（.pg-body，四态 data-state 挂在此节点）
   * @param {*} estimates 一致预期数据（eps/profit/institutions/detail）
   */
  function paintEstimates(body, estimates) {
    var block = el("div", "pg-est-block");
    block.appendChild(el("div", "pg-card-sub", t("stock.est.title") + " · " + t("stock.est.sub")));
    var eps = (estimates && estimates.eps) || [];
    if (!estimates || !estimates.available || eps.length === 0) {
      // 空态如实展示原因（港美股无通道 / 该标的无机构覆盖），不编造数值
      var hint = el("div", "pg-state-hint", (estimates && estimates.note) || t("stock.est.empty"));
      block.appendChild(hint);
      body.appendChild(block);
      return;
    }
    var profitByYear = {};
    (estimates.profit || []).forEach(function (row) {
      profitByYear[String(row["年度"])] = row;
    });
    var table = el("table", "pg-table pg-est-table");
    var thead = el("thead");
    var headRow = el("tr");
    [t("stock.est.year"), t("stock.est.orgs"), t("stock.est.epsAvg"),
     t("stock.est.epsRange"), t("stock.est.profitAvg"), t("stock.est.industry")].forEach(function (text) {
      headRow.appendChild(el("th", "", text));
    });
    thead.appendChild(headRow);
    table.appendChild(thead);
    var tbody = el("tbody");
    eps.forEach(function (row) {
      var tr = el("tr");
      var year = String(row["年度"] || "");
      var profit = profitByYear[year] || {};
      tr.appendChild(el("td", "", year));
      tr.appendChild(el("td", "", String(row["预测机构数"] || 0)));
      tr.appendChild(el("td", "", fmt(row["均值"], 2)));
      var range = (row["最小值"] === null || row["最大值"] === null)
        ? t("value.none")
        : fmt(row["最小值"], 2) + " ~ " + fmt(row["最大值"], 2);
      tr.appendChild(el("td", "", range));
      tr.appendChild(el("td", "", profit["均值"] === null || profit["均值"] === undefined
        ? t("value.none") : fmt(profit["均值"], 2)));
      tr.appendChild(el("td", "", fmt(row["行业平均数"], 2)));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    block.appendChild(table);
    body.appendChild(block);
  }

  /** 计算回撤序列的最低点，供 ECharts markPoint 在绘图窗内标注（P1-10）。
   * @param {Array} series 回撤序列 [{date, drawdown}]
   * @returns {Object} markPoint 配置；无数据时返回 undefined
   */
  function troughPoint(series) {
    var rows = series || [];
    var trough = null;
    var index = -1;
    rows.forEach(function (item, position) {
      if (item && item.drawdown !== null && item.drawdown !== undefined) {
        if (!trough || item.drawdown < trough.drawdown) {
          trough = item;
          index = position;
        }
      }
    });
    if (!trough) {
      return undefined;
    }
    // 标注必须落在绘图窗内：末段点位文字向左展开、首段向右展开，其余置于点上方，
    // 否则贴边点位的气泡文字会被画布裁掉（P1-10 人眼验收项）。
    var labelPosition = "top";
    if (index >= rows.length - 2) {
      labelPosition = "left";
    } else if (index <= 1) {
      labelPosition = "right";
    }
    return {
      symbol: "circle",
      symbolSize: 7,
      itemStyle: { color: COLOR_DOWN },
      label: {
        show: true,
        position: labelPosition,
        distance: 6,
        fontSize: 10,
        color: COLOR_DOWN,
        backgroundColor: "rgba(0,0,0,0.35)",
        padding: [2, 4],
        borderRadius: 3,
        formatter: t("stock.kdj.troughPrefix") + trough.date + " " + Number(trough.drawdown).toFixed(2) + "%"
      },
      data: [{ coord: [trough.date, trough.drawdown], value: trough.drawdown }]
    };
  }

  /** 渲染机构预测（ST-06）：评级分布 + 目标价 + 分年度一致预期。
   * @param {Object} panel 数据面板（报价/新闻/舆情等聚合块）
   * @param {*} estimates 一致预期数据（eps/profit/institutions/detail）
   * @returns {void} 无返回值（直接写 DOM）
   */
  function paintForecast(panel, estimates) {
    var body = document.getElementById("st06-body");
    var card = document.getElementById("ST-06");
    if (!body) {
      return;
    }
    var forecast = panel && panel.forecast;
    if (!forecast) {
      // 评级通道不可用时，仍尝试展示一致预期（两者数据源不同表，互不阻塞）
      if (estimates && estimates.available) {
        setBadges(card, "同花顺", estimates.caliber_note || t("stock.estimates.caliber"), false);
        body.innerHTML = "";
        paintEstimates(body, estimates);
        body.setAttribute("data-state", "default");
        return;
      }
      setEmpty(body, t("stock.empty.forecast"), function () {
        loadStock(true);
      });
      return;
    }
    setBadges(card, "同花顺", forecast.caliber_note || t("stock.forecast.caliber"), false);
    body.innerHTML = "";
    var hasTarget = forecast.target_low !== null && forecast.target_low !== undefined;
    var targetText;
    if (hasTarget) {
      targetText = fmt(forecast.target_low, 2) + " ~ " + fmt(forecast.target_high, 2) +
        " 元（均值 " + fmt(forecast.target_avg, 2) + "，样本 " + (forecast.target_samples || 0) + " 篇）";
    } else {
      // 明确空态（P1-11）：说清为什么没有、评级覆盖多少、可以去哪里找替代信息
      targetText = t("stock.forecast.targetNone", {
        note: t("stock.forecast.channelNoTarget"),
        count: forecast.org_count || 0
      });
    }
    // R2-08：评级字典的键是后端固定中文口径（stock._RATING_NAMES），
    // 统一经 RATING_SOURCE_KEYS 取值，禁止再用 t() 译文当 key、也不在渲染处散写中文
    var ratings = forecast.ratings || {};
    var ratingOf = function (name) {
      return ratings[RATING_SOURCE_KEYS[name]] || 0;
    };
    var lines = [
      [t("stock.forecast.targetRange"), targetText],
      [t("stock.forecast.orgs"), (forecast.org_count || 0) + " 家"],
      [t("stock.forecast.buyHold"), ratingOf("buy") + " / " + ratingOf("add")],
      [t("stock.forecast.neutralSell"), ratingOf("neutral") + " / " + ratingOf("reduce") + " / " + ratingOf("sell")]
    ];
    lines.forEach(function (item) {
      var row = el("div", "pg-quote-meta");
      var label = el("span", "", item[0] + "：");
      var value = el("b", "", item[1]);
      label.appendChild(value);
      row.appendChild(label);
      body.appendChild(row);
    });
    paintEstimates(body, estimates);
    body.setAttribute("data-state", "default");
  }

  /** 渲染公司概况（ST-08）：基本信息 / 股本市值 / 机构与基金持有 / 最新财报摘要 四组。
   * @param {*} profile 公司概况数据
   * @param {*} holders 股东数据（十大股东/十大流通股东）
   * @param {*} summary 摘要数据
   * @returns {*} 处理结果
   */
  function paintProfile(profile, holders, summary) {
    var body = document.getElementById("st08-body");
    var card = document.getElementById("ST-08");
    if (!body) {
      return;
    }
    if (!profile || !profile.name) {
      setEmpty(body, t("stock.empty.profile"), function () {
        loadStock(true);
      });
      return;
    }
    setBadges(card, "东财", profile.caliber_note || t("stock.profile.caliber"), false);
    body.innerHTML = "";

    // 第三组：机构与基金持有（由十大流通股东派生，确定性统计，不做推断）
    var freeList = (holders && holders.free_top10) || [];
    var orgKeywords = ["基金", "投资", "证券", "保险", "资产", "信托", "银行", "社保", "养老金"];
    var orgCount = freeList.filter(function (item) {
      return orgKeywords.some(function (word) {
        return String(item.name || "").indexOf(word) >= 0;
      });
    }).length;
    var freeRatio = freeList.reduce(function (acc, item) {
      return acc + (item.ratio || 0);
    }, 0);
    var topThree = freeList.slice(0, 3).map(function (item) {
      return item.name + "（" + fmt(item.ratio, 2) + "%）";
    }).join("；");

    // 第四组：最新财报摘要（取财务摘要首期）
    var finGroup = [];
    if (summary && summary.metrics && summary.metrics.length) {
      var latest = summary.periods[0];
      // key 与 name 双写入：后端 metrics 两种口径（英文 key + 中文 name / 纯中文 key）都能命中
      var byKey = {};
      summary.metrics.forEach(function (item) {
        byKey[item.key] = item.values[0];
        if (item.name) {
          byKey[item.name] = item.values[0];
        }
      });
      var finValue = function (field) { return byKey[FIN_METRIC_FIELDS[field]]; };
      var revenue = finValue("revenue");
      var profit = finValue("netProfit");
      finGroup = [
        [t("stock.fin.reportPeriod"), latest],
        [t("stock.fin.totalRevenue"), revenue === null || revenue === undefined ? t("value.none") : fmt(revenue / 1e8, 2) + " 亿元"],
        [t("stock.fin.netProfit"), profit === null || profit === undefined ? t("value.none") : fmt(profit / 1e8, 2) + " 亿元"],
        [t("stock.fin.totalRevenueEps"), fmt(finValue("eps"), 2) + " 元"],
        [t("stock.fin.grossMargin"), fmt(finValue("grossMargin"), 2) + "%"],
        [t("stock.fin.roe"), fmt(finValue("roe"), 2) + "%"]
      ];
    }

    var groups = [
      [t("stock.profile.group.basic"), [
        [t("stock.profile.companyName"), profile.full_name || profile.name],
        [t("stock.profile.industry"), profile.industry],
        [t("stock.profile.listDate"), profile.listing_date],
        [t("stock.profile.board"), profile.board],
        [t("stock.profile.province"), profile.province]
      ]],
      [t("stock.profile.group.capital"), [
        [t("stock.profile.totalShare"), profile.total_share === null ? t("value.none") : fmt(profile.total_share / 1e8, 2) + " 亿股"],
        [t("stock.profile.floatShare"), profile.float_share === null ? t("value.none") : fmt(profile.float_share / 1e8, 2) + " 亿股"],
        [t("stock.profile.totalMv"), profile.total_mv === null ? t("value.none") : fmt(yi(profile.total_mv), 2) + " 亿元"],
        [t("stock.profile.floatMv"), profile.float_mv === null ? t("value.none") : fmt(yi(profile.float_mv), 2) + " 亿元"],
        [t("stock.profile.peTtm"), fmt(profile.pe_ttm, 2)]
      ]],
      [t("stock.profile.group.holding"), [
        [t("stock.profile.orgCount"), orgCount ? orgCount + " 家" : t("value.none")],
        [t("stock.profile.freeRatio"), freeRatio ? fmt(freeRatio, 2) + "%" : t("value.none")],
        [t("stock.profile.topThree"), topThree || t("value.none")],
        [t("stock.profile.dataCaliber"), (holders && holders.free_top10_period) ? t("stock.fin.reportPeriod") + holders.free_top10_period : t("value.none")]
      ]],
      [t("stock.profile.group.finSummary"), finGroup.length ? finGroup : [[t("stock.profile.financialReport"), t("state.noData")]]]
    ];

    groups.forEach(function (group) {
      body.appendChild(el("div", "pg-card-sub", group[0]));
      var grid = el("div", "pg-quote-meta");
      group[1].forEach(function (item) {
        var cell = el("span", "", item[0] + "：");
        cell.appendChild(el("b", "", item[1] === null || item[1] === undefined || item[1] === "" ? t("value.none") : item[1]));
        grid.appendChild(cell);
      });
      body.appendChild(grid);
    });
    body.setAttribute("data-state", "default");
  }

  /** 渲染财务页签区（ST-09）：关键指标 / 趋势 / 三表 / 指标明细。
   * @param {*} fin 财务数据块（摘要 + 趋势）
   */
  function paintFinancial(fin) {
    var body = document.getElementById("st09-body");
    var card = document.getElementById("ST-09");
    if (!body) {
      return;
    }
    if (!fin.summary) {
      setEmpty(body, t("stock.empty.fin"), function () {
        loadStock(true);
      });
      return;
    }
    setBadges(card, fin.summary.source, fin.summary.caliber, false);
    body.innerHTML = "";
    var tabs = el("div", "pg-fin-tabs");
    [["summary", t("stock.fin.tabs").split(",")[0]], ["trend", t("stock.fin.tabs").split(",")[1]], ["report", t("stock.fin.tabs").split(",")[2]], ["indicator", t("stock.fin.tabs").split(",")[3]]].forEach(function (item) {
      var button = el("button", "pg-tab", item[1]);
      button.type = "button";
      button.setAttribute("data-fin", item[0]);
      if (state.stock.finTab === item[0]) {
        button.classList.add("active");
      }
      tabs.appendChild(button);
    });
    body.appendChild(tabs);
    var pane = el("div", "");
    pane.id = "st09-pane";
    body.appendChild(pane);

    tabs.addEventListener("click", function (event) {
      var button = event.target.closest(".pg-tab");
      if (!button) {
        return;
      }
      state.stock.finTab = button.getAttribute("data-fin");
      tabs.querySelectorAll(".pg-tab").forEach(function (node) {
        node.classList.toggle("active", node === button);
      });
      paintFinPane(fin);
    });
    paintFinPane(fin);
    body.setAttribute("data-state", "default");
  }

  /** 渲染财务页签内容。
   * @param {*} fin 财务数据块（摘要 + 趋势）
   * @returns {*} 处理结果
   */
  function paintFinPane(fin) {
    var pane = document.getElementById("st09-pane");
    if (!pane) {
      return;
    }
    var tab = state.stock.finTab;
    pane.innerHTML = "";

    if (tab === "summary") {
      var metrics = fin.summary.metrics || [];
      var columns = [{ title: t("stock.fin.item"), key: "name", cls: "name" }].concat(
        (fin.summary.periods || []).slice(0, 4).map(function (period, index) {
          return {
            title: period,
            cls: "num",
            /** 财务摘要单元格：按报告期列取值，缺失显示「无数据」（单位随接口口径）。
             * @param {*} row 单行记录
             * @returns {*} 处理结果
             */
            render: function (row) {
              var value = row.values[index];
              if (value === null || value === undefined) {
                return t("value.none");
              }
              // 金额类指标（原始单位=元）折算为亿元，比例类保持原值
              if (row.key === "营业总收入" || row.key === "归母净利润") {
                return fmt(value / 1e8, 2) + " 亿";
              }
              return fmt(value, 2);
            }
          };
        })
      );
      renderTable(pane, columns, metrics, null, "short");
      return;
    }

    if (tab === "trend") {
      if (!fin.trend || !fin.trend.series) {
        setEmpty(pane, t("stock.empty.trend"), function () {
          loadStock(true);
        });
        return;
      }
      var box = el("div", "pg-chart");
      pane.appendChild(box);
      var chart = mountChart(box);
      var theme = chartTheme();
      var periods = fin.trend.periods || [];
      var revenue = (fin.trend.series.find(function (s) { return s.key === "revenue"; }) || {}).values || [];
      var profit = (fin.trend.series.find(function (s) { return s.key === "profit"; }) || {}).values || [];
      var revenueYoy = (fin.trend.series.find(function (s) { return s.key === "revenue_yoy"; }) || {}).values || [];
      var profitYoy = (fin.trend.series.find(function (s) { return s.key === "profit_yoy"; }) || {}).values || [];
      if (chart) {
        chart.setOption({
          backgroundColor: "transparent",
          tooltip: { trigger: "axis" },
          legend: { textStyle: { color: theme.text }, top: 0 },
          grid: { left: 60, right: 48, top: 36, bottom: 28 },
          xAxis: { type: "category", data: periods, axisLabel: { color: theme.text }, axisLine: { lineStyle: { color: theme.border } } },
          // 左轴金额（亿元），右轴增速（%），双序列共图但各自量纲
          yAxis: [
            { type: "value", name: t("stock.unit.billions"), nameTextStyle: { color: theme.text }, axisLabel: { color: theme.text }, splitLine: { lineStyle: { color: theme.border, opacity: 0.4 } } },
            { type: "value", name: "%", nameTextStyle: { color: theme.text }, axisLabel: { color: theme.text }, splitLine: { show: false } }
          ],
          series: [
            { name: t("stock.chart.revenue"), type: "bar", data: revenue.map(function (v) { return v === null ? null : +(v / 1e8).toFixed(2); }), color: "#b47da0" },
            { name: t("stock.chart.profit"), type: "bar", data: profit.map(function (v) { return v === null ? null : +(v / 1e8).toFixed(2); }), color: "#4b9eff" },
            { name: t("stock.chart.revenueYoy"), type: "line", yAxisIndex: 1, data: revenueYoy, color: COLOR_UP, showSymbol: true },
            { name: t("stock.chart.profitYoy"), type: "line", yAxisIndex: 1, data: profitYoy, color: COLOR_DOWN, showSymbol: true }
          ]
        });
      }
      return;
    }

    if (tab === "report" || tab === "indicator") {
      // 报表/指标数据按需加载，避免首屏 5 个财务请求
      pane.innerHTML = "";
      var loadingBox = el("div", "pg-state");
      loadingBox.textContent = t("state.loading");
      pane.appendChild(loadingBox);
      var request =
        tab === "report"
          ? api("/api/stock/financial-report", { symbol: state.stock.symbol, statement: "利润表", limit: 6 })
          : api("/api/stock/financial-indicator", { symbol: state.stock.symbol, limit: 8 });
      request
        .then(function (data) {
          pane.innerHTML = "";
          if (tab === "report") {
            var selectRow = el("div", "pg-fin-tabs");
            var reports = t("stock.fin.reportTabs").split(",");
            reports.forEach(function (name, idx) {
              var button = el("button", "pg-tab" + (idx === 1 ? " active" : ""), name);
              button.type = "button";
              button.addEventListener("click", function () {
                selectRow.querySelectorAll(".pg-tab").forEach(function (node) {
                  node.classList.toggle("active", node === button);
                });
                api("/api/stock/financial-report", { symbol: state.stock.symbol, statement: name, limit: 6 })
                  .then(function (payload) {
                    renderFinTable(pane, payload);
                  })
                  .catch(function (error) {
                    setError(pane, error.message, function () {
                      paintFinPane(fin);
                    });
                  });
              });
              selectRow.appendChild(button);
            });
            pane.appendChild(selectRow);
          }
          renderFinTable(pane, data);
        })
        .catch(function (error) {
          setError(pane, error.message, function () {
            paintFinPane(fin);
          });
        });
    }
  }

  /** 渲染财务表（期数横向）。
   * @param {*} pane 指标面板索引
   * @param {Object} data 数据主体（接口 data 段）
   * @returns {*} 处理结果
   */
  function renderFinTable(pane, data) {
    var holder = el("div", "");
    var columns = [{ title: t("stock.fin.item"), key: "name", cls: "name" }].concat(
      (data.periods || []).map(function (period, index) {
        return {
          title: period,
          cls: "num",
          /** 财务趋势单元格：元 → 亿元（÷1e8，两位小数），缺失显示「无数据」。
           * @param {*} row 单行记录
           * @returns {*} 处理结果
           */
          render: function (row) {
            var value = row.values[index];
            return value === null || value === undefined ? t("value.none") : fmt(value / 1e8, 2) + " 亿";
          }
        };
      })
    );
    pane.appendChild(holder);
    renderTable(holder, columns, data.metrics || [], null, "short");
  }

  /** 渲染十大股东（ST-10）。
   * @param {*} holders 股东数据（十大股东/十大流通股东）
   * @returns {*} 处理结果
   */
  function paintHolders(holders) {
    var body = document.getElementById("st10-body");
    var card = document.getElementById("ST-10");
    if (!body) {
      return;
    }
    if (!holders || !holders.top10 || holders.top10.length === 0) {
      setEmpty(body, t("stock.empty.holder"), function () {
        loadStock(true);
      });
      return;
    }
    setBadges(card, "东财", t("stock.holder.caliberPrefix") + (holders.top10_period || t("value.dash")) + t("stock.holder.caliberSuffix"), false);
    renderTable(
      body,
      [
        { title: t("stock.holder.rank"), key: "rank", cls: "num" },
        { title: t("stock.holder.name"), key: "name", cls: "name" },
        { title: t("stock.holder.ratio"), cls: "num", render: function (row) { return fmt(row.ratio); } },
        {
          title: t("stock.holder.change"),
          cls: "num",
          /** 股东表「本期变动」：优先用源站文本，缺省回落到数值格式化。
           * @param {*} row 单行记录
           * @returns {*} 处理结果
           */
          render: function (row) {
            if (row.change_text) {
              return row.change_text;
            }
            return fmt(row.change, 0);
          }
        }
      ],
      holders.top10,
      null,
      "short"
    );
  }

  /** 渲染整页个股数据。
   * @param {*} payload 渲染载荷（聚合后的数据）
   */
  function paintStock(payload) {
    paintQuote(payload.panel);
    syncStockTabs();
    paintStockChart();
    paintFundDist(payload.panel);
    paintNews(payload.panel);
    paintSentiment(payload.panel);
    paintForecast(payload.panel, payload.estimates);
    paintProfile(payload.profile, payload.holders, payload.summary);
    paintFinancial({ summary: payload.summary, trend: payload.trend });
    paintHolders(payload.holders);
  }

  // =============================================================
  // 七、#/sector-val 单板块估值（V-01~V-04）
  // =============================================================
  /** 构建估值页骨架。
   * @param {HTMLElement} container DOM 容器节点
   */
  function buildValShell(container) {
    container.innerHTML = "";
    var toolbar = el("div", "page-toolbar");
    var info = el("span", "pg-card-sub", t("val.boardLabel"));
    info.id = "val-board-label";
    toolbar.appendChild(info);
    toolbar.appendChild(el("span", "pg-spacer"));
    var refresh = el("button", "pg-btn", t("action.reload"));
    refresh.type = "button";
    refresh.addEventListener("click", function () {
      loadSectorVal(true);
    });
    toolbar.appendChild(refresh);
    container.appendChild(toolbar);

    var grid = el("div", "pg-grid");
    grid.id = "val-grid";
    var left = el("div", "g-7");
    left.style.display = "flex";
    left.style.flexDirection = "column";
    left.style.gap = "16px";

    var kline = buildCard("V-01", t("stock.kv.chartBoard"), "", t("val.chartTitle"));
    kline.body.id = "v01-body";
    kline.body.classList.add("tall");
    left.appendChild(kline.card);

    var metrics = buildCard("V-02", t("val.metricsTitle"), "", "");
    metrics.body.id = "v02-body";
    left.appendChild(metrics.card);

    var cons = buildCard("V-03", t("val.components"), "", t("val.componentsSub"));
    cons.body.id = "v03-body";
    left.appendChild(cons.card);
    grid.appendChild(left);

    var right = el("div", "g-5");
    var info4 = buildCard("V-04", t("val.newsTitle"), "", t("val.newsSub"));
    info4.body.id = "v04-body";
    // 占位区块固定渲染，不参与数据加载/错误态，避免上游失败时把占位卡也标成「加载失败」
    var placeholder = el("div", "pg-state");
    placeholder.appendChild(el("div", "pg-state-title", t("val.chartPlaceholder")));
    var hint = el("div", "pg-state-hint", t("val.chartHint"));
    hint.id = "v04-hint";
    placeholder.appendChild(hint);
    info4.body.appendChild(placeholder);
    info4.body.setAttribute("data-state", "empty");
    right.appendChild(info4.card);
    grid.appendChild(right);
    container.appendChild(grid);
  }

  /** 渲染估值页。
   * @param {Object} params 查询参数对象
   */
  function renderSectorVal(params) {
    var container = document.getElementById(VIEW_IDS["sector-val"]);
    state.sectorVal.board = params.board || state.sectorVal.board;
    state.sectorVal.market = normSectorMarket(params.market || state.sectorVal.market);  // RT-05：a→cn 归一
    if (!container.querySelector("#val-grid")) {
      buildValShell(container);
    }
    var label = document.getElementById("val-board-label");
    if (label) {
      label.textContent = t("val.boardLabel") + (state.sectorVal.board || t("val.placeholder.unselected")) + "（" + (state.sectorVal.market === "a" ? t("sectors.market.cn") : state.sectorVal.market === "hk" ? t("sectors.market.hk") : t("sectors.market.us")) + "）";
    }
    loadSectorVal();
  }

  /** 拉取并渲染估值页。
   * @param {boolean} force 是否强制刷新（true 时跳过本地缓存）
   * @returns {*} 处理结果
   */
  function loadSectorVal(force) {
    var board = state.sectorVal.board;
    var market = state.sectorVal.market;
    // V-04 为静态占位，不进入加载/错误循环
    var bodies = ["v01-body", "v02-body", "v03-body"].map(function (id) {
      return document.getElementById(id);
    });
    updateValPlaceholder(board, market);
    if (!board) {
      bodies.forEach(function (body) {
        if (body) {
          setEmpty(body, t("val.enterFromSectors"), null);
        }
      });
      return;
    }
    bodies.forEach(function (body) {
      if (body) {
        setLoading(body);
      }
    });

    Promise.all([
      api("/api/sector/kline", { board: board, period: "daily", market: market }),
      api("/api/sector/cons", { board: board, market: market }).catch(function (error) {
        return { error: error.message };
      })
    ])
      .then(function (result) {
        paintSectorVal({ kline: result[0], cons: result[1] });
      })
      .catch(function (error) {
        bodies.forEach(function (body) {
          if (body) {
            setError(body, error.message, function () {
              loadSectorVal(true);
            });
          }
        });
      });
  }

  /** 渲染估值页数据。
   * @param {*} payload 渲染载荷（聚合后的数据）
   */
  function paintSectorVal(payload) {
    var kline = payload.kline;
    var cons = payload.cons;
    var board = state.sectorVal.board;
    var market = state.sectorVal.market;

    // ---- V-01 板块 K 线 ----
    var v01 = document.getElementById("v01-body");
    var v01Card = document.getElementById("V-01");
    if (v01) {
      setBadges(v01Card, kline.source, kline.caliber, false);
      var items = kline.items || [];
      v01.innerHTML = "";
      if (items.length === 0) {
        setEmpty(v01, t("stock.empty.boardKline"), function () {
          loadSectorVal(true);
        });
      } else {
        var box = el("div", "pg-chart");
        v01.appendChild(box);
        var chart = mountChart(box);
        var theme = chartTheme();
        if (chart) {
          chart.setOption({
            backgroundColor: "transparent",
            tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
            grid: { left: 60, right: 16, top: 24, bottom: 28 },
            xAxis: {
              type: "category",
              data: items.map(function (item) { return item.date; }),
              axisLabel: { color: theme.text },
              axisLine: { lineStyle: { color: theme.border } }
            },
            yAxis: { scale: true, axisLabel: { color: theme.text }, splitLine: { lineStyle: { color: theme.border, opacity: 0.4 } } },
            series: [
              {
                type: "candlestick",
                data: items.map(function (item) { return [item.open, item.close, item.low, item.high]; }),
                itemStyle: { color: COLOR_UP, color0: COLOR_DOWN, borderColor: COLOR_UP, borderColor0: COLOR_DOWN }
              }
            ]
          });
        }
        v01.setAttribute("data-state", "default");
      }
    }

    // ---- V-02 估值指标 ----
    var v02 = document.getElementById("v02-body");
    if (v02) {
      var list = kline.items || [];
      if (list.length === 0) {
        setEmpty(v02, t("state.empty"), function () {
          loadSectorVal(true);
        });
      } else {
        var last = list[list.length - 1];
        var prev = list.length > 1 ? list[list.length - 2] : null;
        var closes = list.slice(-250).map(function (item) { return item.close; });
        var below = closes.filter(function (value) { return value <= last.close; }).length;
        var percentile = closes.length ? (below / closes.length) * 100 : null;
        var pct = prev && prev.close ? ((last.close - prev.close) / prev.close) * 100 : null;
        var consItems = (cons && cons.items) || [];
        var leader = consItems.length ? consItems[0] : null;
        var laggard = consItems.length ? consItems[consItems.length - 1] : null;
        v02.innerHTML = "";
        var grid = el("div", "pg-quote-meta");
        [
          [t("val.kv.latest"), fmt(last.close, 2)],
          [t("val.kv.change"), fmtPct(pct)],
          [t("val.kv.amount"), last.amount === null ? t("value.none") : fmt(yi(last.amount), 2) + " 亿元"],
          [t("val.kv.percentile"), percentile === null ? t("value.none") : fmt(percentile, 1) + "%"],
          [t("val.kv.lead"), leader ? leader.name + " " + fmtPct(leader.pct) : t("value.none")],
          [t("val.kv.laggard"), laggard ? laggard.name + " " + fmtPct(laggard.pct) : t("value.none")]
        ].forEach(function (item) {
          var cell = el("span", "", item[0] + "：");
          cell.appendChild(el("b", "", item[1]));
          grid.appendChild(cell);
        });
        v02.appendChild(grid);
        v02.setAttribute("data-state", "default");
      }
    }

    // ---- V-03 成分股 ----
    var v03 = document.getElementById("v03-body");
    if (v03) {
      if (cons && cons.error) {
        setError(v03, cons.error, function () {
          loadSectorVal(true);
        });
      } else if (!cons || (cons.items || []).length === 0) {
        setEmpty(v03, t("val.empty.components"), function () {
          loadSectorVal(true);
        });
      } else {
        setBadges(document.getElementById("V-03"), cons.source, cons.caliber, false);
        renderTable(
          v03,
          [
            { title: t("th.code"), key: "code", cls: "num" },
            { title: t("th.name"), key: "name", cls: "name" },
            { title: t("sectors.pctCol"), cls: "num", render: function (row) { return pctNode(row.pct); } },
            { title: t("th.weight"), cls: "num", render: function (row) { return fmt(row.weight); } },
            { title: t("mk.kv.price"), cls: "num", render: function (row) { return fmt(row.price, 2); } }
          ],
          cons.items,
          function (row) {
            // 跳转 #2：成分股 → 个股详情
            window.location.hash = "#/stock?symbol=" + encodeURIComponent(row.code) + "&market=a";
          },
          "short"
        );
      }
    }

    // ---- V-04 占位说明随参数更新（区块本体在骨架里固定渲染） ----
    updateValPlaceholder(board, market);
  }

  /** 更新 V-04 占位卡的参数提示（不做任何数据请求）。
   * @param {*} board 板块名称
   * @param {*} market 市场标识：a/hk/us
   */
  function updateValPlaceholder(board, market) {
    var hint = document.getElementById("v04-hint");
    if (hint) {
      hint.textContent =
        t("val.placeholder.hint", { board: board || t("val.placeholder.unselected"), market: market });
    }
  }

  // =============================================================
  // 八、#/news 新闻与利好利空（批次 5.2）
  // 结构：N-01 三级板块树 / N-02 资讯流 / N-03 情绪温度 / N-04 AI 复核（内嵌于卡片）
  // =============================================================
  var newsState = {
    mode: "feed", // feed=多源资讯流，stock=个股新闻
    src: "feed", // 资讯源：feed=综合资讯流 / flash=7×24 快讯 / global=全球三源 / sector=板块命中
    symbol: "",
    board: "科技成长",
    sort: "relevance",
    region: "global",
    tree: null,
    expanded: {}
  };

  /** POST 请求：用于 AI 复核（返回 data，失败抛中文错误）。
   * @param {string} path 接口路径
   * @param {HTMLElement} body 区块容器（.pg-body，四态 data-state 挂在此节点）
   * @returns {*} 处理结果
   */
  function apiPost(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    })
      .then(function (response) {
        return response.json();
      })
      .then(function (payload) {
        if (payload && payload.error) {
          var error = new Error(payload.error);
          error.code = payload.error_code;
          error.data = payload.data;
          throw error;
        }
        return payload.data || {};
      });
  }

  /** 构建新闻页骨架。
   * @param {HTMLElement} container DOM 容器节点
   */
  // =============================================================
  // 新增功能 ⑥：财报 / 公告搜索（#/news?fs=1，FS-01~FS-09）
  // 唯一实现页；入口 = 新闻页 N-05 入口卡 + 侧栏同名项（两处同跳）
  // 设计依据：验收方《设计基准_财报搜索_20261025.md》§5.3；
  //          样式复用现网 pages.css 的 FS-* 段，无第二套配色/栅格。
  // =============================================================
  var fsState = {
    market: "all",   // all / a / hk / us
    type: "全部",     // 财报类型 chips
    since: "all",    // 全部时间 / 近 N 天
    keyword: "",     // 当前生效关键词
    page: 1,         // 当前页码（「加载更多」递增）
    seq: 0,          // 请求序号：过期响应直接丢弃（防抖 + 竞态保护）
    timer: null,     // 输入防抖计时器（300ms）
    rows: [],        // 已累积结果（「加载更多」追加）
    total: 0,        // 源站总命中数
    loading: false,  // 加载中禁止重复提交
    expanded: false  // R6-10：新闻页内联财报区是否展开（跨路由重绘后保持）
  };

  var FS_MARKETS = [["all", "fs.market.all"], ["a", "fs.market.a"], ["hk", "fs.market.hk"], ["us", "fs.market.us"]];
  var FS_TIMES = [["all", "fs.time.all"], ["7", "fs.time.7"], ["30", "fs.time.30"], ["90", "fs.time.90"], ["365", "fs.time.365"]];
  var FS_TYPES = ["全部", "年报", "半年报", "一季报", "三季报", "业绩预告", "招股书", "10-K", "10-Q", "8-K"];

  /** 市场标签样式类：A 股绿描边 / 港股橙描边 / 美股蓝描边（设计基准 §5.3）。
   * @param {string} market a/hk/us
   * @returns {string} 样式类名
   */
  function fsMarketClass(market) {
    return { a: "mkt-a", hk: "mkt-hk", us: "mkt-us" }[market] || "";
  }

  /** 市场中文名。
   * @param {string} market a/hk/us
   * @returns {string} 中文名
   */
  function fsMarketLabel(market) {
    // 显式映射：避免动态拼接键（i18n 静态检查与翻译抽取都依赖字面量键）
    var labels = { a: "fs.market.a", hk: "fs.market.hk", us: "fs.market.us" };
    return t(labels[market] || "fs.market.all");
  }

  /** 定期报告类走「财报」色，其余公告走「公告」色。
   * @param {string} type 类型
   * @returns {boolean} 是否定期报告
   */
  function fsIsReport(type) {
    return ["年报", "半年报", "一季报", "三季报", "招股书", "10-K", "10-Q"].indexOf(type) >= 0;
  }

  /** HTML 转义：标题高亮走 innerHTML，必须先转义防注入。
   * @param {*} text 原文
   * @returns {string} 转义后的文本
   */
  function fsEscape(text) {
    return String(text === null || text === undefined ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  /** 标题关键词高亮（只高亮命中片段，其余原样转义）。
   * @param {*} text 标题
   * @param {string} keyword 关键词
   * @returns {string} 含 mark 标签的 HTML
   */
  function fsHighlight(text, keyword) {
    var safe = fsEscape(text);
    if (!keyword) {
      return safe;
    }
    var needle = fsEscape(keyword).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return safe.replace(new RegExp("(" + needle + ")", "gi"), "<mark>$1</mark>");
  }

  /** 渲染财报/公告搜索页（FS 唯一实现页，对齐原型 fs 视图）。
   * @param {HTMLElement} container 页面容器（#page-news）
   */
  function renderFinancialSearch(container, inline) {
    var embedded = inline === true;
    container.innerHTML = "";
    container.setAttribute("data-fs", "1");  // 标记当前容器是 FS 视图，返回时据此重建新闻壳

    var toolbar = el("div", "page-toolbar");
    // R6-10 / F6-11：内联模式下按钮语义为「收起」，深链模式下为「返回」
    var back = el("button", "fs-back", embedded ? t("fs.collapse") : t("fs.back"));
    back.type = "button";
    back.addEventListener("click", function () {
      if (embedded) {
        container.innerHTML = "";
        container.removeAttribute("data-fs");
        container.hidden = true;
        fsState.expanded = false;
        return;
      }
      window.location.hash = "#/news";
    });
    toolbar.appendChild(back);
    toolbar.appendChild(el("span", "pg-spacer"));
    var refresh = el("button", "pg-btn", t("action.reload"));
    refresh.type = "button";
    refresh.id = "fs-refresh";
    refresh.addEventListener("click", function () {
      loadFinancialSearch(true);
    });
    toolbar.appendChild(refresh);
    container.appendChild(toolbar);

    var grid = el("div", "pg-grid");
    grid.id = "fs-grid";

    // 顶行：市场 Tab（FS-01a）
    var topCard = buildCard("FS-00", t("fs.title"), "g-12", "");
    setBadges(topCard.card, "巨潮资讯 · 东财 · HKEX披露易 · SEC EDGAR", t("fs.entry.sub"), false);
    var marketTabs = el("div", "pg-tabs");
    marketTabs.id = "fs-market";
    FS_MARKETS.forEach(function (item) {
      var button = el("button", "pg-tab", t(item[1]));
      button.type = "button";
      button.setAttribute("data-market", item[0]);
      marketTabs.appendChild(button);
    });
    topCard.body.appendChild(marketTabs);
    topCard.body.setAttribute("data-state", "default");
    grid.appendChild(topCard.card);

    // FS-01：搜索条件区（输入框 + 时间下拉 + 搜索按钮 + 类型 chips）
    var cond = buildCard("FS-01", t("fs.condTitle"), "g-12", "");
    var bar = el("div", "fs-searchbar");
    var input = el("input", "fs-search-input");
    input.id = "fs-input";
    input.type = "search";
    input.placeholder = t("fs.placeholder");
    input.value = fsState.keyword;
    bar.appendChild(input);
    var select = el("select", "fs-select");
    select.id = "fs-time";
    FS_TIMES.forEach(function (item) {
      var option = document.createElement("option");
      option.value = item[0];
      option.textContent = t(item[1]);
      select.appendChild(option);
    });
    select.value = fsState.since;
    bar.appendChild(select);
    var searchBtn = el("button", "pg-btn primary", t("fs.search"));
    searchBtn.type = "button";
    searchBtn.id = "fs-search-btn";
    bar.appendChild(searchBtn);
    cond.body.appendChild(bar);

    var chips = el("div", "fs-chips");
    chips.id = "fs-type";
    FS_TYPES.forEach(function (name) {
      var chip = el("button", "fs-chip" + (fsState.type === name ? " on" : ""), name);
      chip.type = "button";
      chip.setAttribute("data-type", name);
      chips.appendChild(chip);
    });
    cond.body.appendChild(chips);
    cond.body.setAttribute("data-state", "default");
    grid.appendChild(cond.card);

    // FS-02：结果列表（四态容器）
    var result = buildCard("FS-02", t("fs.resultTitle"), "g-12", "");
    var count = el("span", "fs-count", "");
    count.id = "fs-count";
    result.card.querySelector(".pg-card-head").insertBefore(count, result.card.querySelector(".pg-spacer"));
    result.body.id = "fs-body";
    grid.appendChild(result.card);

    container.appendChild(grid);

    // ---------- 交互绑定 ----------
    marketTabs.addEventListener("click", function (event) {
      var button = event.target.closest(".pg-tab");
      if (!button) {
        return;
      }
      fsState.market = button.getAttribute("data-market");
      fsSyncTabs();
      loadFinancialSearch(true);
    });
    chips.addEventListener("click", function (event) {
      var chip = event.target.closest(".fs-chip");
      if (!chip) {
        return;
      }
      fsState.type = chip.getAttribute("data-type");
      fsSyncTabs();
      loadFinancialSearch(true);
    });
    select.addEventListener("change", function () {
      fsState.since = select.value;
      loadFinancialSearch(true);
    });
    searchBtn.addEventListener("click", function () {
      fsState.keyword = input.value.trim();
      loadFinancialSearch(true);
    });
    input.addEventListener("input", function () {
      // 防抖 300ms：连续输入只在停顿后发一次请求
      window.clearTimeout(fsState.timer);
      fsState.timer = window.setTimeout(function () {
        fsState.keyword = input.value.trim();
        loadFinancialSearch(true);
      }, 300);
    });
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        window.clearTimeout(fsState.timer);
        fsState.keyword = input.value.trim();
        loadFinancialSearch(true);
      }
    });

    fsSyncTabs();
    loadFinancialSearch(true);
  }

  /** 同步市场 Tab 与类型 chips 的选中态。*/
  function fsSyncTabs() {
    var marketBox = document.getElementById("fs-market");
    if (marketBox) {
      marketBox.querySelectorAll(".pg-tab").forEach(function (button) {
        button.classList.toggle("active", button.getAttribute("data-market") === fsState.market);
      });
    }
    var chipBox = document.getElementById("fs-type");
    if (chipBox) {
      chipBox.querySelectorAll(".fs-chip").forEach(function (chip) {
        chip.classList.toggle("on", chip.getAttribute("data-type") === fsState.type);
      });
    }
  }

  /** 设置结果区四态（default/loading/empty/error）。
   * @param {string} state 状态名
   * @param {string} message 空/错态说明文案
   * @param {Function} onRetry 重试回调
   */
  function fsSetState(state, message, onRetry) {
    var body = document.getElementById("fs-body");
    if (!body) {
      return;
    }
    body.innerHTML = "";
    body.setAttribute("data-state", state);
    if (state === "loading") {
      var skeleton = el("div", "pg-skeleton");
      for (var i = 0; i < 5; i += 1) {
        var row = el("div", "sk-row");
        row.appendChild(el("span", i % 2 ? "w2" : "w1", ""));
        skeleton.appendChild(row);
      }
      body.appendChild(skeleton);
      fsSetCount(t("fs.searching"));
      return;
    }
    var box = el("div", "pg-state");
    if (state === "empty") {
      box.appendChild(el("div", "pg-state-title", t("fs.emptyTitle")));
      box.appendChild(el("div", "pg-state-hint", message || t("fs.emptyHint")));
      var clear = el("button", "pg-btn", t("fs.clearRetry"));
      clear.type = "button";
      clear.addEventListener("click", function () {
        fsState.market = "all";
        fsState.type = "全部";
        fsState.since = "all";
        fsState.keyword = "";
        renderFinancialSearch(document.getElementById(VIEW_IDS.news));
      });
      box.appendChild(clear);
      fsSetCount(t("fs.countZero"));
    } else {
      box.appendChild(el("div", "pg-state-title", t("fs.errorTitle")));
      box.appendChild(el("div", "pg-state-hint", message || t("fs.errorHint")));
      var retry = el("button", "pg-btn primary", t("action.retry"));
      retry.type = "button";
      retry.addEventListener("click", function () {
        if (onRetry) {
          onRetry();
        }
      });
      box.appendChild(retry);
      fsSetCount("—");
    }
    body.appendChild(box);
  }

  /** 更新结果计数文案。
   * @param {string} text 计数文本
   */
  function fsSetCount(text) {
    var node = document.getElementById("fs-count");
    if (node) {
      node.textContent = text;
    }
  }

  /** 渲染结果行（FS-02 九字段 + 标题高亮）。
   * @param {Array} rows 记录列表
   */
  function fsRenderRows(rows) {
    var body = document.getElementById("fs-body");
    if (!body) {
      return;
    }
    body.innerHTML = "";
    body.setAttribute("data-state", "default");
    var list = el("div", "fs-list");
    rows.forEach(function (item) {
      var row = el("div", "fs-item");
      var title = el("div", "fs-item-title");
      title.innerHTML = fsHighlight(item.title, fsState.keyword);  // 已转义，仅插入 mark
      row.appendChild(title);

      var sub = el("div", "fs-item-sub");
      sub.appendChild(el("span", "co", item.company || t("value.none")));
      sub.appendChild(el("span", "code", item.code || "—"));
      sub.appendChild(el("span", "fs-tag " + fsMarketClass(item.market), fsMarketLabel(item.market)));
      sub.appendChild(el("span", "fs-tag " + (fsIsReport(item.type) ? "fr" : "ann"), item.type || "其他"));
      sub.appendChild(el("span", "", "来源：" + (item.source || "—")));
      var time = el("span", "fs-time");
      // TZ-01：财报跨 A股/港股/美股三个市场，时间一律标注北京口径，悬浮给完整说明
      time.innerHTML = fsEscape(item.pubTime || "—") + ' <span class="tz" title="'
        + fsEscape(t("fs.tzFull")) + '">' + fsEscape(t("fs.tz")) + "</span>";
      sub.appendChild(time);
      row.appendChild(sub);

      row.addEventListener("click", function () {
        fsOpenDetail(item);
      });
      list.appendChild(row);
    });
    body.appendChild(list);

    var more = el("div", "fs-more");
    more.appendChild(el("span", "fs-count", t("fs.shown", { n: rows.length })));
    var moreBtn = el("button", "pg-btn sm", t("fs.loadMore"));
    moreBtn.type = "button";
    moreBtn.addEventListener("click", function () {
      loadFinancialSearch(false);
    });
    more.appendChild(moreBtn);
    body.appendChild(more);
    fsSetCount(t("fs.count", { n: rows.length }));
  }

  /** FS-08 详情弹窗：标题/元信息/摘要/原文链接。
   * @param {Object} item 单条记录
   */
  function fsOpenDetail(item) {
    var mask = el("div", "fs-modal-mask");
    var modal = el("div", "fs-modal");
    modal.appendChild(el("h3", "", item.title || t("value.none")));

    var meta = el("div", "m-meta");
    meta.appendChild(el("span", "fs-tag " + fsMarketClass(item.market), fsMarketLabel(item.market)));
    meta.appendChild(el("span", "fs-tag " + (fsIsReport(item.type) ? "fr" : "ann"), item.type || "其他"));
    meta.appendChild(el("span", "fs-tag", (item.company || "") + " · " + (item.code || "")));
    meta.appendChild(el("span", "fs-time", (item.pubTime || "—") + " " + t("fs.tzFull")));
    modal.appendChild(meta);

    modal.appendChild(el("div", "m-sec", t("fs.summary")));
    // 源站不提供摘要时不编造：如实说明「未提供」并引导看原文
    var bodyNode = el("div", "m-body", item.summary || t("fs.summaryNone"));
    modal.appendChild(bodyNode);

    // 阶段 12：A 股（巨潮/东财）、港股（HKEX）、美股（EDGAR）均在打开弹窗时按需拉取正文
    //（A 股：东财直链或巨潮↔东财交叉验证；港股：HKEX↔东财港股库校验；美股：SEC 官方原文）
    fsLoadCnDetail(modal, bodyNode, item);

    modal.appendChild(el("div", "m-sec", t("fs.docLink", { source: item.source || "—" })));
    var link = el("a", "m-link", item.docUrl || t("value.none"));
    if (item.docUrl) {
      link.href = item.docUrl;
      link.target = "_blank";
      link.rel = "noopener";
    }
    modal.appendChild(link);

    var ops = el("div", "m-ops");
    var close = el("button", "pg-btn", t("fs.close"));
    close.type = "button";
    close.addEventListener("click", function () {
      mask.remove();
    });
    ops.appendChild(close);
    var open = el("button", "pg-btn primary", t("fs.openDoc"));
    open.type = "button";
    open.addEventListener("click", function () {
      if (item.docUrl) {
        window.open(item.docUrl, "_blank", "noopener");
      }
    });
    ops.appendChild(open);
    modal.appendChild(ops);

    mask.appendChild(modal);
    mask.addEventListener("click", function (event) {
      if (event.target === mask) {
        mask.remove();
      }
    });
    document.body.appendChild(mask);
  }

  /** 详情正文按需拉取（弹窗打开后异步加载，四态齐备；三市场共用）。
   * @param {HTMLElement} modal 弹窗容器（用于挂口径行与判断弹窗是否已关闭）
   * @param {HTMLElement} bodyNode 摘要/正文体节点
   * @param {Object} item 单条记录（market/title/code/pubTime/docUrl 原样回传后端）
   */
  function fsLoadCnDetail(modal, bodyNode, item) {
    bodyNode.textContent = t("fs.detailLoading");
    bodyNode.classList.add("m-loading");
    window.Utils.api("/api/news/financial-detail", {
      market: item.market || "a",
      title: item.title || "",
      code: item.code || "",
      pub_time: item.pubTime || "",
      doc_url: item.docUrl || ""
    }).then(function (data) {
      if (!document.body.contains(modal)) {
        return;  // 弹窗已关闭，过期响应丢弃
      }
      bodyNode.classList.remove("m-loading");
      if (data && data.content) {
        bodyNode.textContent = data.content;
        var caliber = el("div", "m-caliber",
          (data.verified ? t("fs.verified") : t("fs.unverified"))
          + " · " + (data.caliber || ""));
        modal.insertBefore(caliber, bodyNode.nextSibling);
      } else {
        bodyNode.textContent = (data && data.caliber) || t("fs.summaryNone");
      }
    }).catch(function () {
      if (!document.body.contains(modal)) {
        return;
      }
      bodyNode.classList.remove("m-loading");
      bodyNode.textContent = t("fs.detailFailed");
    });
  }

  /** 拉取财报/公告结果（reset=true 重新检索，false 追加下一页）。
   * @param {boolean} reset 是否重置分页与已累积结果
   */
  function loadFinancialSearch(reset) {
    var body = document.getElementById("fs-body");
    if (!body) {
      return;
    }
    if (fsState.loading) {
      return;  // 加载中禁止重复提交
    }
    if (reset) {
      fsState.page = 1;
      fsState.rows = [];
    }
    var seq = ++fsState.seq;
    fsState.loading = true;
    if (reset) {
      fsSetState("loading");
    }
    window.Utils.api("/api/news/financial-search", {
      market: fsState.market,
      q: fsState.keyword,
      type: fsState.type,
      since: fsState.since,
      page: fsState.page
    }).then(function (data) {
      if (seq !== fsState.seq) {
        return;  // 过期响应丢弃
      }
      fsState.loading = false;
      var rows = (data && data.list) || [];
      fsState.total = (data && data.total) || 0;
      fsState.rows = fsState.rows.concat(rows);
      var card = document.getElementById("FS-02");
      if (card) {
        setBadges(card, (data && data.source) || "—", (data && data.caliber) || "—", false);
      }
      if (!rows.length && !fsState.rows.length) {
        fsState.loading = false;
        fsSetState("empty", (data && data.caliber) || "");
        return;
      }
      fsRenderRows(fsState.rows);
      fsState.page += 1;
    }).catch(function (error) {
      if (seq !== fsState.seq) {
        return;
      }
      fsState.loading = false;
      fsSetState("error", (error && error.message) || "", function () {
        loadFinancialSearch(true);
      });
    });
  }

  function buildNewsShell(container) {
    container.innerHTML = "";
    var toolbar = el("div", "page-toolbar");

    // 模式切换：资讯流 / 个股新闻（个股模式需带 symbol）
    var modeTabs = el("div", "pg-tabs");
    modeTabs.id = "N-mode";
    [["feed", t("news.tab.feed")], ["stock", t("news.tab.stock")]].forEach(function (item) {
      var button = el("button", "pg-tab", item[1]);
      button.type = "button";
      button.setAttribute("data-mode", item[0]);
      modeTabs.appendChild(button);
    });
    toolbar.appendChild(modeTabs);

    // 资讯源切换（FN-03 整改）：把此前无界面入口的三个端点接上——
    // 综合=/api/news/ai-feed、快讯=/api/news/flash、全球=/api/news/global、板块=/api/news/sector-search
    var srcTabs = el("div", "pg-tabs");
    srcTabs.id = "N-src";
    [["feed", t("news.src.feed")], ["flash", t("news.src.flash")],
     ["global", t("news.src.global")], ["sector", t("news.src.sector")]].forEach(function (item) {
      var button = el("button", "pg-tab", item[1]);
      button.type = "button";
      button.setAttribute("data-src", item[0]);
      srcTabs.appendChild(button);
    });
    toolbar.appendChild(srcTabs);

    // 排序切换
    var sortTabs = el("div", "pg-tabs");
    sortTabs.id = "N-sort";
    [["relevance", t("news.sort.relevance")], ["time", t("th.time")], ["impact", t("news.sort.impact")]].forEach(function (item) {
      var button = el("button", "pg-tab", item[1]);
      button.type = "button";
      button.setAttribute("data-sort", item[0]);
      sortTabs.appendChild(button);
    });
    toolbar.appendChild(sortTabs);

    // 区域切换（仅资讯流模式生效）
    var regionTabs = el("div", "pg-tabs");
    regionTabs.id = "N-region";
    [["global", t("news.region.global")], ["cn", t("sectors.market.cn")]].forEach(function (item) {
      var button = el("button", "pg-tab", item[1]);
      button.type = "button";
      button.setAttribute("data-region", item[0]);
      regionTabs.appendChild(button);
    });
    toolbar.appendChild(regionTabs);
    toolbar.appendChild(el("span", "pg-spacer"));

    var refresh = el("button", "pg-btn", t("action.reload"));
    refresh.type = "button";
    refresh.id = "news-refresh";
    toolbar.appendChild(refresh);
    container.appendChild(toolbar);

    var grid = el("div", "pg-grid");
    grid.id = "news-grid";

    // N-05：财报/公告搜索入口卡（新增功能 ⑥，48 列通栏，置于资讯流上方）
    var fsEntryCard = buildCard("N-05", t("fs.entry.title"), "g-12", "");
    setBadges(fsEntryCard.card, "巨潮/东财/HKEX/EDGAR", t("fs.entry.sub"), false);
    var entryBox = el("div", "fs-entry");
    entryBox.id = "fs-entry-btn";
    entryBox.title = t("fs.entry.tip");
    entryBox.appendChild(el("div", "ic", "📑"));
    var entryText = el("div");
    entryText.appendChild(el("div", "t", t("fs.entry.title")));
    entryText.appendChild(el("div", "d", t("fs.entry.desc")));
    entryBox.appendChild(entryText);
    entryBox.appendChild(el("div", "go", t("fs.entry.go")));
    // R6-10 / F6-11：默认就地展开（原实现跳转 #/news?fs=1 会整页替换、资讯流消失）。
    // 「在新页面打开」保留深链语义，供分享/书签使用。
    entryBox.addEventListener("click", function () {
      var host = document.getElementById("fs-inline");
      if (!host) {
        return;
      }
      if (fsState.expanded) {
        host.innerHTML = "";
        host.hidden = true;
        fsState.expanded = false;
        return;
      }
      host.hidden = false;
      fsState.expanded = true;
      renderFinancialSearch(host, true);
      host.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    // 深链按钮：单独一个小按钮，点了才跳整页
    var openPage = el("button", "pg-btn dash-btn", t("fs.entry.openPage"));
    openPage.type = "button";
    openPage.addEventListener("click", function (event) {
      event.stopPropagation();
      window.location.hash = "#/news?fs=1";
    });
    entryBox.appendChild(openPage);
    fsEntryCard.body.appendChild(entryBox);
    // 内联结果宿主：默认隐藏，点入口卡后就地展开
    var inlineHost = el("div", "fs-inline");
    inlineHost.id = "fs-inline";
    inlineHost.hidden = !fsState.expanded;
    fsEntryCard.body.appendChild(inlineHost);
    if (fsState.expanded) {
      renderFinancialSearch(inlineHost, true);
    }
    fsEntryCard.body.setAttribute("data-state", "default");
    grid.appendChild(fsEntryCard.card);

    // 左栏：板块树 + 情绪温度
    var left = el("div", "g-4");
    left.style.display = "flex";
    left.style.flexDirection = "column";
    left.style.gap = "16px";
    var treeCard = buildCard("N-01", t("news.treeTitle"), "", t("news.treeSub"));
    treeCard.body.id = "n01-body";
    treeCard.body.classList.add("short");
    left.appendChild(treeCard.card);
    var sentiCard = buildCard("N-03", t("news.sentiTitle"), "", t("news.sentiSub"));
    sentiCard.body.id = "n03-body";
    left.appendChild(sentiCard.card);
    grid.appendChild(left);

    // 右栏：资讯流
    var feedCard = buildCard("N-02", t("news.feedTitle"), "g-8", t("news.feedSub"));
    feedCard.body.id = "n02-body";
    grid.appendChild(feedCard.card);
    container.appendChild(grid);

    srcTabs.addEventListener("click", function (event) {
      var button = event.target.closest(".pg-tab");
      if (!button) {
        return;
      }
      newsState.src = button.getAttribute("data-src") || "feed";
      newsState.mode = "feed"; // 任意资讯源都属于资讯流模式，个股模式需重新带 symbol
      syncNewsTabs();
      loadNews(true);
    });
    modeTabs.addEventListener("click", function (event) {
      var button = event.target.closest(".pg-tab");
      if (!button) {
        return;
      }
      var mode = button.getAttribute("data-mode");
      if (mode === "stock" && !newsState.symbol) {
        toast(t("news.stockRedirectToast"));
        return;
      }
      newsState.mode = mode;
      syncNewsTabs();
      loadNews();
    });
    sortTabs.addEventListener("click", function (event) {
      var button = event.target.closest(".pg-tab");
      if (!button) {
        return;
      }
      newsState.sort = button.getAttribute("data-sort");
      syncNewsTabs();
      loadNews(true);
    });
    regionTabs.addEventListener("click", function (event) {
      var button = event.target.closest(".pg-tab");
      if (!button) {
        return;
      }
      newsState.region = button.getAttribute("data-region");
      syncNewsTabs();
      loadNews(true);
    });
    refresh.addEventListener("click", function () {
      newsState.tree = null;
      loadNews(true);
    });
  }

  /** 同步 Tab 选中态与可见性。 */
  function syncNewsTabs() {
    var modeBox = document.getElementById("N-mode");
    var regionBox = document.getElementById("N-region");
    var srcBox = document.getElementById("N-src");
    if (modeBox) {
      modeBox.querySelectorAll(".pg-tab").forEach(function (button) {
        button.classList.toggle("active", button.getAttribute("data-mode") === newsState.mode);
      });
    }
    if (srcBox) {
      // 资讯源只在资讯流模式下有意义（个股新闻固定一个源）
      srcBox.hidden = newsState.mode !== "feed";
      srcBox.querySelectorAll(".pg-tab").forEach(function (button) {
        button.classList.toggle("active", button.getAttribute("data-src") === newsState.src);
      });
    }
    document.querySelectorAll("#N-sort .pg-tab").forEach(function (button) {
      button.classList.toggle("active", button.getAttribute("data-sort") === newsState.sort);
    });
    if (regionBox) {
      // 区域只影响「综合资讯流」这一源，切到快讯/全球/板块时隐藏，避免无效控件
      regionBox.hidden = newsState.mode !== "feed" || newsState.src !== "feed";
      regionBox.querySelectorAll(".pg-tab").forEach(function (button) {
        button.classList.toggle("active", button.getAttribute("data-region") === newsState.region);
      });
    }
  }

  /** 渲染新闻页。
   * @param {Object} params 查询参数对象
   */
  function renderNews(params) {
    var container = document.getElementById(VIEW_IDS.news);
    // 财报/公告搜索：唯一实现页 #/news?fs=1（新增功能 ⑥），不另起第二套外壳
    if (String(params.fs || "") === "1") {
      renderFinancialSearch(container);
      return;
    }
    if (container.getAttribute("data-fs") === "1") {
      // 从财报搜索返回：容器内是 FS 视图，先清空并摘掉标记，再走常规新闻页渲染
      container.innerHTML = "";
      container.removeAttribute("data-fs");
    }
    if (params.symbol) {
      newsState.symbol = params.symbol;
      newsState.mode = "stock";
    }
    if (!container.querySelector("#news-grid")) {
      buildNewsShell(container);
    }
    syncNewsTabs();
    loadNews();
  }

  /** 拉取并渲染：板块树 + 资讯流 + 情绪温度。
   * @param {boolean} force 是否强制刷新（true 时跳过本地缓存）
   * @returns {Object} 处理结果
   */
  function loadNews(force) {
    var body = document.getElementById("n02-body");
    var sentiBody = document.getElementById("n03-body");
    var treeBody = document.getElementById("n01-body");
    if (!body) {
      return;
    }
    if (force) {
      newsState.tree = null;
    }
    setLoading(body);
    setLoading(sentiBody);
    if (!newsState.tree) {
      setLoading(treeBody);
    }

    var treeTask = newsState.tree
      ? Promise.resolve(newsState.tree)
      : api("/api/news/board-tree", {});
    var feedTask;
    if (newsState.mode === "stock") {
      feedTask = api("/api/news/stock", { symbol: newsState.symbol, limit: 30 });
    } else if (newsState.src === "flash") {
      // FN-03：7×24 快讯（财联社，不可用回退东财/新浪）
      feedTask = api("/api/news/flash", { limit: 40, sort: "time" });
    } else if (newsState.src === "global") {
      // FN-03：全球快讯三源合并去重
      feedTask = api("/api/news/global", { limit: 60 });
    } else if (newsState.src === "sector") {
      // FN-03：板块命中检索（当前板块树选中项）
      feedTask = api("/api/news/sector-search", {
        sector: newsState.board, sort: newsState.sort, limit: 40
      });
    } else {
      feedTask = api("/api/news/ai-feed", {
        boards: newsState.board,
        sort: newsState.sort,
        limit: 40,
        region: newsState.region
      });
    }
    var sentiTask =
      newsState.mode === "stock"
        ? api("/api/sentiment/stock", { symbol: newsState.symbol })
        : api("/api/sentiment/board", { board: newsState.board }).catch(function (error) {
            return { error: error.message };
          });

    Promise.all([treeTask, feedTask, sentiTask])
      .then(function (result) {
        newsState.tree = result[0];
        paintBoardTree(treeBody, result[0]);
        paintNewsFeed(body, result[1]);
        paintNewsSentiment(sentiBody, result[2]);
      })
      .catch(function (error) {
        setError(body, error.message, function () {
          loadNews(true);
        });
        setError(sentiBody, error.message, function () {
          loadNews(true);
        });
      });
  }

  /** 渲染三级板块树（一级/二级可折叠，末级可选中）。
   * @param {HTMLElement} body 区块容器（.pg-body，四态 data-state 挂在此节点）
   * @param {*} tree 树形结构（板块树）
   */
  function paintBoardTree(body, tree) {
    if (!body) {
      return;
    }
    var levels = (tree && tree.tree) || [];
    if (!levels.length) {
      setEmpty(body, t("news.empty.board"), function () {
        loadNews(true);
      });
      return;
    }
    body.innerHTML = "";
    var meta = el("div", "pg-card-sub",
      t("sectors.versionFormat", { version: tree.version, l1: tree.counts.level1, l2: tree.counts.level2, leaf: tree.counts.leaf }));
    body.appendChild(meta);

    levels.forEach(function (level1) {
      var open1 = newsState.expanded[level1.id] !== false;
      var row1 = el("div", "pg-tree-row level1" + (level1.name === newsState.board ? " active" : ""));
      row1.appendChild(el("span", "pg-tree-toggle", open1 ? "▾" : "▸"));
      row1.appendChild(el("span", "pg-tree-name", level1.name));
      row1.addEventListener("click", function () {
        // 一级节点同样可作过滤条件（展开为其全部末级子孙）
        newsState.expanded[level1.id] = !open1;
        if (newsState.board !== level1.name) {
          newsState.board = level1.name;
          newsState.mode = "feed";
          syncNewsTabs();
          loadNews();
        } else {
          paintBoardTree(body, tree);
        }
      });
      body.appendChild(row1);
      if (!open1) {
        return;
      }
      level1.children.forEach(function (level2) {
        var open2 = newsState.expanded[level2.id] === true;
        var row2 = el("div", "pg-tree-row level2" + (level2.name === newsState.board ? " active" : ""));
        row2.appendChild(el("span", "pg-tree-toggle", open2 ? "▾" : "▸"));
        row2.appendChild(el("span", "pg-tree-name", level2.name));
        row2.addEventListener("click", function () {
          // 二级节点同时承担「展开」和「作为过滤板块」两个职责
          newsState.expanded[level2.id] = !open2;
          if (newsState.board !== level2.name) {
            newsState.board = level2.name;
            newsState.mode = "feed";
            syncNewsTabs();
            loadNews();
          } else {
            paintBoardTree(body, tree);
          }
        });
        body.appendChild(row2);
        if (!open2) {
          return;
        }
        level2.children.forEach(function (leaf) {
          var row3 = el("div", "pg-tree-row level3" + (leaf.name === newsState.board ? " active" : ""));
          row3.appendChild(el("span", "pg-tree-dot", "·"));
          row3.appendChild(el("span", "pg-tree-name", leaf.name));
          row3.addEventListener("click", function () {
            newsState.board = leaf.name;
            newsState.mode = "feed";
            syncNewsTabs();
            loadNews();
          });
          body.appendChild(row3);
        });
      });
    });
    body.setAttribute("data-state", "default");
  }

  /** 渲染资讯流：每条含情绪标签、事件、板块、AI 复核按钮。
   * @param {HTMLElement} body 区块容器（.pg-body，四态 data-state 挂在此节点）
   * @param {*} payload 渲染载荷（聚合后的数据）
   */
  function paintNewsFeed(body, payload) {
    var card = document.getElementById("N-02");
    if (!body) {
      return;
    }
    setBadges(card, payload.source, payload.caliber, false);
    var items = payload.items || [];
    if (!items.length) {
      setEmpty(body, t("news.empty.feed"), function () {
        loadNews(true);
      });
      return;
    }
    body.innerHTML = "";
    var list = el("div", "pg-feed-list");
    items.forEach(function (item) {
      var sentiment = item.sentiment || {};
      var box = el("article", "pg-feed-item");

      var head = el("div", "pg-feed-head");
      var chipClass = sentiment.label === t("stock.sentiment.up") ? "chip up" : sentiment.label === t("stock.sentiment.down") ? "chip down" : "chip";
      head.appendChild(el("span", chipClass, sentiment.label || t("stock.sentiment.neutral")));
      head.appendChild(el("span", "pg-feed-score", t("news.scoreLabel") + fmt(sentiment.score, 4) + " · " + (sentiment.strength || t("news.weak"))));
      head.appendChild(el("span", "pg-spacer"));
      head.appendChild(newsTime(item.time));
      box.appendChild(head);

      var title = el("div", "pg-feed-title", item.title || t("news.titleMissing"));
      if (item.url) {
        title.style.cursor = "pointer";
        title.addEventListener("click", function () {
          window.open(item.url, "_blank", "noopener");
        });
      }
      box.appendChild(title);

      if (item.text) {
        box.appendChild(el("div", "pg-feed-text", item.text.slice(0, 180) + (item.text.length > 180 ? "…" : "")));
      }

      var foot = el("div", "pg-feed-foot");
      foot.appendChild(el("span", "pg-card-sub", item.source || t("news.sourceUnknown")));
      (item.channels || []).forEach(function (channel) {
        foot.appendChild(el("span", "pg-news-tag", channel));
      });
      (item.boards || []).forEach(function (board) {
        foot.appendChild(el("span", "pg-news-tag board", board));
      });
      if ((sentiment.events || []).length) {
        foot.appendChild(el("span", "pg-news-tag",
          t("news.eventLabel") + sentiment.events.map(function (event) { return event.name; }).join("、")));
      }
      var reviewButton = el("button", "pg-btn", t("news.button.review"));
      reviewButton.type = "button";
      var reviewBox = el("div", "pg-review-box");
      reviewButton.addEventListener("click", function () {
        reviewButton.disabled = true;
        reviewButton.textContent = t("news.button.reviewing");
        apiPost("/api/news/ai-review", {
          title: item.title,
          content: item.text,
          board: (item.boards || [])[0] || newsState.board,
          path: newsState.mode === "stock" ? "stock" : "feed",
          sentiment: sentiment.label,
          strength: sentiment.strength
        })
          .then(function (data) {
            reviewButton.textContent = t("news.button.review");
            reviewButton.disabled = false;
            reviewBox.textContent = "AI（" + (data.model || t("news.reviewConfigured")) + "）：" + (data.review && data.review.raw ? data.review.raw.slice(0, 200) : t("news.reviewReturned"));
            reviewBox.className = "pg-review-box ok";
          })
          .catch(function (error) {
            reviewButton.textContent = t("news.button.review");
            reviewButton.disabled = false;
            // AI 未配置属预期契约：给出明确引导，不报错、不冒充结果
            reviewBox.textContent =
              error.code === "NOT_CONFIGURED"
                ? t("copilot.aiNotConfigured")
                : t("news.reviewFailed") + error.message;
            reviewBox.className = "pg-review-box warn";
          });
      });
      foot.appendChild(reviewButton);
      box.appendChild(foot);
      box.appendChild(reviewBox);
      list.appendChild(box);
    });
    body.appendChild(list);
    body.setAttribute("data-state", "default");
  }

  /** 渲染情绪温度卡（N-03）。
   * @param {HTMLElement} body 区块容器（.pg-body，四态 data-state 挂在此节点）
   * @param {*} payload 渲染载荷（聚合后的数据）
   * @returns {*} 处理结果
   */
  function paintNewsSentiment(body, payload) {
    if (!body) {
      return;
    }
    if (!payload || payload.error || payload.temperature === undefined) {
      setEmpty(body, (payload && payload.error) || t("senti.empty"), function () {
        loadNews(true);
      });
      return;
    }
    body.innerHTML = "";
    var grid = el("div", "pg-metric-grid");
    [
      [t("news.senti.temperature"), payload.temperature, payload.temperature >= 65 ? "up" : payload.temperature <= 35 ? "down" : "flat"],
      [t("news.senti.level"), payload.level, "flat"],
      [t("stock.sentiment.up"), payload.positive, "up"],
      [t("stock.sentiment.down"), payload.negative, "down"],
      [t("news.senti.sample"), payload.total, "flat"]
    ].forEach(function (item) {
      var cell = el("div", "pg-metric-cell");
      cell.appendChild(el("div", "pg-metric-label", item[0]));
      cell.appendChild(el("div", "pg-metric-value " + item[2], item[1]));
      grid.appendChild(cell);
    });
    body.appendChild(grid);

    if ((payload.top_events || []).length) {
      var events = payload.top_events.map(function (event) {
        return event.name + "×" + event.count;
      }).join("、");
      body.appendChild(el("div", "pg-card-sub", t("news.senti.driving") + events));
    }
    if ((payload.samples || []).length) {
      var list = el("ul", "pg-news-list");
      payload.samples.slice(0, 3).forEach(function (sample) {
        var li = el("li", "pg-news-item");
        li.appendChild(el("span", "pg-news-title", sample.title || ""));
        li.appendChild(el("span", "chip", fmt(sample.score, 4)));
        list.appendChild(li);
      });
      body.appendChild(list);
    }
    body.appendChild(el("div", "pg-card-sub",
      t("news.senti.caliber") + (payload.caliber || t("news.senti.engine")) + t("news.senti.equation")));
    body.setAttribute("data-state", "default");
  }

  // =============================================================
  // 九、#/fund 场外基金详情（批次 5.3）
  // 结构：F-01 基金大卡 / F-02 净值走势 / F-03 阶段业绩 / F-04 重仓股
  //       F-05 档案与分红 / F-06 持仓分布（需登录，禁用说明）
  // =============================================================
  var fundState = { code: "017811" };

  /** 通用写请求：method = PUT/DELETE/POST，统一中文异常。
   * @param {*} method 事件名或 HTTP 方法
   * @param {string} path 接口路径
   * @param {HTMLElement} body 区块容器（.pg-body，四态 data-state 挂在此节点）
   * @returns {*} 处理结果
   */
  function apiSend(method, path, body) {
    var options = { method: method, headers: { "Content-Type": "application/json" } };
    if (body !== undefined) {
      options.body = JSON.stringify(body);
    }
    return fetch(path, options)
      .catch(function () {
        throw new Error(t("error.network"));
      })
      .then(function (response) {
        return response.json();
      })
      .then(function (payload) {
        if (payload && payload.error) {
          var error = new Error(payload.error);
          error.code = payload.error_code;
          error.data = payload.data;
          throw error;
        }
        return payload.data || {};
      });
  }

  /** 构建基金页骨架。
   * @param {HTMLElement} container DOM 容器节点
   */
  function buildFundShell(container) {
    container.innerHTML = "";
    var toolbar = el("div", "page-toolbar");
    toolbar.appendChild(el("span", "pg-card-sub", t("fund.codeLabel")));
    var input = document.createElement("input");
    input.id = "fund-code";
    input.value = fundState.code;
    input.setAttribute("aria-label", t("fund.codeLabel"));
    input.style.cssText =
      "width:120px;background:var(--bg-panel);border:1px solid var(--border);color:var(--text);" +
      "border-radius:5px;padding:4px 8px;font-family:var(--font-mono)";
    toolbar.appendChild(input);
    var go = el("button", "pg-btn", t("action.view"));
    go.type = "button";
    go.id = "fund-go";
    toolbar.appendChild(go);

    // FN-02：基金独立搜索（OB-F044 P0）—— 支持代码/简称/拼音，选中直接跳详情，
    // 不再只能靠自选或养基宝带 code 进入；接口复用既有 GET /api/fund/search。
    var searchWrap = el("div", "fund-search-wrap");
    var searchInput = document.createElement("input");
    searchInput.id = "fund-search";
    searchInput.type = "search";
    searchInput.placeholder = t("fund.searchPlaceholder");
    searchInput.setAttribute("aria-label", t("fund.searchPlaceholder"));
    searchInput.style.cssText =
      "width:200px;background:var(--bg-panel);border:1px solid var(--border);color:var(--text);" +
      "border-radius:5px;padding:4px 8px";
    var searchList = el("div", "fund-search-list");
    searchList.id = "fund-search-list";
    searchList.hidden = true;
    searchWrap.appendChild(searchInput);
    searchWrap.appendChild(searchList);
    toolbar.appendChild(searchWrap);
    toolbar.appendChild(el("span", "pg-spacer"));
    var refresh = el("button", "pg-btn", t("action.reload"));
    refresh.type = "button";
    refresh.id = "fund-refresh";
    toolbar.appendChild(refresh);
    container.appendChild(toolbar);

    var grid = el("div", "pg-grid");
    grid.id = "fund-grid";

    var head = buildCard("F-01", t("fund.overviewCard"), "g-12", t("fund.overviewSub"));
    head.body.id = "f01-body";
    grid.appendChild(head.card);

    var left = el("div", "g-7");
    left.style.display = "flex";
    left.style.flexDirection = "column";
    left.style.gap = "16px";
    var navCard = buildCard("F-02", t("fund.navCard"), "", t("fund.navSub"));
    navCard.body.id = "f02-body";
    left.appendChild(navCard.card);
    var perfCard = buildCard("F-03", t("fund.perfCard"), "", t("fund.perfSub"));
    perfCard.body.id = "f03-body";
    left.appendChild(perfCard.card);
    grid.appendChild(left);

    var right = el("div", "g-5");
    right.style.display = "flex";
    right.style.flexDirection = "column";
    right.style.gap = "16px";
    var infoCard = buildCard("F-05", t("fund.infoCard"), "", t("fund.infoSub"));
    infoCard.body.id = "f05-body";
    right.appendChild(infoCard.card);
    var posCard = buildCard("F-06", t("fund.holdCard"), "", t("fund.holdSub"));
    posCard.body.id = "f06-body";
    right.appendChild(posCard.card);
    grid.appendChild(right);

    var holdCard = buildCard("F-04", t("fund.holdingCard"), "g-12", t("fund.holdingSub"));
    holdCard.body.id = "f04-body";
    grid.appendChild(holdCard.card);

    container.appendChild(grid);

    go.addEventListener("click", applyFundInput);
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        applyFundInput();
      }
    });
    refresh.addEventListener("click", function () {
      loadFund();
    });
  }

  /** 读取输入并跳转（保持 URL 参数与状态一致）。 */
  function applyFundInput() {
    var input = document.getElementById("fund-code");
    var code = (input && input.value ? input.value : "").trim();
    if (!code) {
      toast(t("fund.codeRequired"));
      return;
    }
    window.location.hash = "#/fund?code=" + encodeURIComponent(code);
  }

  /** 渲染基金页。
   * @param {Object} params 查询参数对象
   */
  /** FN-02：基金搜索联想（代码/简称/拼音），点击结果直接跳详情。
   *
   * 说明：基金页此前只能靠自选/养基宝带 code 进入（OB-F044 P0 缺口）；
   * 本函数复用既有 GET /api/fund/search（后端已存在），不新增接口。
   * @param {HTMLInputElement} input 搜索输入框
   */
  function bindFundSearch(input) {
    var list = document.getElementById("fund-search-list");
    var timer = null;
    var seq = 0;

    /** 收起下拉并清空内容。*/
    function hide() {
      if (list) {
        list.hidden = true;
        list.innerHTML = "";
      }
    }

    /** 渲染联想结果（空结果给中文提示，不编造）。
     * @param {Array} items 结果列表
     */
    function render(items) {
      if (!list) {
        return;
      }
      list.innerHTML = "";
      if (!items.length) {
        list.appendChild(el("div", "fund-search-item", t("fund.searchNone")));
      } else {
        items.forEach(function (item) {
          var row = el("div", "fund-search-item");
          row.appendChild(el("span", "code", item.code || ""));
          row.appendChild(el("span", "name", item.name || ""));
          row.addEventListener("click", function () {
            fundState.code = item.code;
            hide();
            input.value = item.name || item.code;
            window.location.hash = "#/fund?code=" + encodeURIComponent(item.code);
          });
          list.appendChild(row);
        });
      }
      list.hidden = false;
    }

    // 防抖 300ms + 请求序号过期丢弃（与财报搜索同一交互口径）
    input.addEventListener("input", function () {
      var keyword = input.value.trim();
      window.clearTimeout(timer);
      if (!keyword) {
        hide();
        return;
      }
      timer = window.setTimeout(function () {
        var mine = ++seq;
        api("/api/fund/search", { q: keyword, limit: 10 }).then(function (data) {
          if (mine !== seq) {
            return;
          }
          render((data && data.items) || []);
        }).catch(function () {
          if (mine !== seq) {
            return;
          }
          render([]);
        });
      }, 300);
    });
    input.addEventListener("blur", function () {
      window.setTimeout(hide, 180);  // 延迟收起，保证点击结果能先触发
    });
  }

  /** 渲染基金页。
   * @param {Object} params 查询参数对象
   */
  function renderFund(params) {
    var container = document.getElementById(VIEW_IDS.fund);
    fundState.code = params.code || fundState.code;
    if (!container.querySelector("#fund-grid")) {
      buildFundShell(container);
    }
    var input = document.getElementById("fund-code");
    if (input) {
      input.value = fundState.code;
    }
    // FN-02：绑定「查看」按钮与基金搜索联想（只绑一次，路由重复进入不叠加监听器）
    var goBtn = document.getElementById("fund-go");
    if (goBtn && !goBtn.getAttribute("data-bound")) {
      goBtn.setAttribute("data-bound", "1");
      goBtn.addEventListener("click", function () {
        var box = document.getElementById("fund-code");
        var next = box ? box.value.trim() : "";
        if (next) {
          fundState.code = next;
          window.location.hash = "#/fund?code=" + encodeURIComponent(next);
        }
      });
    }
    var searchBox = document.getElementById("fund-search");
    if (searchBox && !searchBox.getAttribute("data-bound")) {
      searchBox.setAttribute("data-bound", "1");
      bindFundSearch(searchBox);
    }
    loadFund();
  }

  /** 并发拉取基金六块数据，单块失败只影响本块。 */
  function loadFund() {
    var code = fundState.code;
    var bodies = ["f01-body", "f02-body", "f03-body", "f04-body", "f05-body", "f06-body"]
      .map(function (id) { return document.getElementById(id); });
    bodies.forEach(function (body) {
      if (body) {
        setLoading(body);
      }
    });
    var params = { code: code };
    Promise.all([
      api("/api/fund/info", params).catch(function (e) { return { error: e.message }; }),
      api("/api/fund/realtime", params).catch(function (e) { return { error: e.message }; }),
      api("/api/fund/nav", { code: code, page: 1, size: 200 }).catch(function (e) { return { error: e.message }; }),
      api("/api/fund/performance", params).catch(function (e) { return { error: e.message }; }),
      api("/api/fund/drawdown", params).catch(function (e) { return { error: e.message }; }),
      api("/api/fund/holding", params).catch(function (e) { return { error: e.message }; }),
      api("/api/fund/dividend", params).catch(function (e) { return { error: e.message }; }),
      api("/api/fund/position", params).catch(function (e) { return { error: e.message, disabled: true }; })
    ]).then(function (result) {
      paintFund({
        info: result[0], realtime: result[1], nav: result[2], performance: result[3],
        drawdown: result[4], holding: result[5], dividend: result[6], position: result[7]
      });
    });
  }

  /** 渲染基金页各区块。
   * @param {*} payload 渲染载荷（聚合后的数据）
   */
  function paintFund(payload) {
    var info = payload.info;
    var realtime = payload.realtime;

    // ---- F-01 概览大卡 ----
    var head = document.getElementById("f01-body");
    var headCard = document.getElementById("F-01");
    if (head) {
      if (info && info.error) {
        setError(head, info.error, function () { loadFund(); });
      } else {
        setBadges(headCard, (realtime && realtime.source) || "蛋卷",
          (realtime && realtime.caliber) || t("fund.caliber.navFallback"), false);
        head.innerHTML = "";
        var main = el("div", "pg-quote-main");
        main.appendChild(el("span", "pg-quote-name", info.name || t("value.none")));
        main.appendChild(el("span", "pg-card-sub", info.code || ""));
        main.appendChild(el("span", "pg-quote-price " + pctClass(realtime.day_growth), fmt(realtime.nav, 4)));
        main.appendChild(el("span", pctClass(realtime.day_growth), fmtPct(realtime.day_growth)));
        main.appendChild(el("span", "pg-card-sub", (realtime.nav_date || "") + " 净值"));
        head.appendChild(main);
        var meta = el("div", "pg-quote-meta");
        [
          [t("fund.kv.fullName"), info.full_name],
          [t("fund.kv.manager"), info.manager],
          [t("fund.kv.foundDate"), info.found_date],
          [t("fund.kv.scale"), info.scale],
          [t("fund.kv.riskLevel"), info.risk_level ? "R" + info.risk_level : t("value.none")],
          [t("fund.kv.subscribe"), info.subscribe_status === "0" ? t("fund.kv.subscribeOpen") : t("fund.kv.subscribeLimited")],
          [t("fund.kv.withdraw"), info.withdraw_status === "1" ? t("fund.kv.withdrawOpen") : t("fund.kv.withdrawPause")]
        ].forEach(function (item) {
          var cell = el("span", "", item[0] + "：");
          cell.appendChild(el("b", "", item[1] || t("value.none")));
          meta.appendChild(cell);
        });
        head.appendChild(meta);
        head.setAttribute("data-state", "default");
      }
    }

    // ---- F-02 净值与回撤 ----
    var navBody = document.getElementById("f02-body");
    if (navBody) {
      var navItems = (payload.nav && payload.nav.items) || [];
      var ddSeries = (payload.drawdown && payload.drawdown.series) || [];
      if (!navItems.length) {
        setEmpty(navBody, (payload.nav && payload.nav.error) || t("fund.empty.nav"), function () { loadFund(); });
      } else {
        setBadges(document.getElementById("F-02"), (payload.nav && payload.nav.source) || "蛋卷",
          (payload.nav && payload.nav.caliber) || t("fund.caliber.nav"), false);
        var ordered = navItems.slice().reverse();
        var box = el("div", "pg-chart");
        navBody.innerHTML = "";
        navBody.appendChild(box);
        var chart = mountChart(box);
        var theme = chartTheme();
        // 回撤子图纵轴固定阶梯：>50% 用 30% 步长，其余 25%，保证最多 3~4 档标签，
        // 窄高（1024 下 260px）也不会互相挤压（P1-10 人眼验收项）
        var ddValues = ddSeries.map(function (i) { return Number(i.drawdown) || 0; });
        var ddWorst = ddValues.length ? Math.abs(Math.min.apply(null, ddValues)) : 0;
        var ddStep = ddWorst > 50 ? 30 : 25;
        var ddMin = -Math.ceil((ddWorst + 5) / ddStep) * ddStep;
        if (chart) {
          chart.setOption({
            backgroundColor: "transparent",
            tooltip: { trigger: "axis" },
            legend: { textStyle: { color: theme.text }, top: 0 },
            // 用纯百分比切分：主图 44%，子图 22%，中间与底部各留 14% / 12% 给日期标签，
            // 避免 px 与 % 混算在 260px 窄高下溢出（P1-10）
            grid: [
              { left: 62, right: 18, top: "8%", height: "44%" },
              { left: 62, right: 18, top: "66%", height: "22%" }
            ],
            xAxis: [
              { type: "category", data: ordered.map(function (i) { return i.date; }),
                axisLabel: { color: theme.text, fontSize: 10, margin: 8,
                  interval: Math.max(1, Math.floor(ordered.length / 6)) },
                axisLine: { lineStyle: { color: theme.border } } },
              { type: "category", gridIndex: 1, data: ddSeries.map(function (i) { return i.date; }),
                axisLabel: { color: theme.text, fontSize: 10, margin: 6, hideOverlap: true,
                  interval: Math.max(1, Math.floor(ddSeries.length / 5)) },
                axisLine: { lineStyle: { color: theme.border } } }
            ],
            yAxis: [
              { scale: true, axisLabel: { color: theme.text, fontSize: 10 },
                splitLine: { lineStyle: { color: theme.border, opacity: 0.4 } } },
              { gridIndex: 1, min: ddMin, max: 0, interval: ddStep,
                axisLabel: { color: theme.text, fontSize: 10, margin: 8, formatter: "{value}%" },
                splitLine: { show: false } }
            ],
            series: [
              { name: t("fund.nav.nav"), type: "line", data: ordered.map(function (i) { return i.nav; }),
                showSymbol: false, color: "#b47da0", lineStyle: { width: 1.6 } },
              { name: t("fund.nav.drawdown"), type: "line", xAxisIndex: 1, yAxisIndex: 1,
                data: ddSeries.map(function (i) { return i.drawdown; }),
                showSymbol: false, areaStyle: { opacity: 0.25 }, color: COLOR_DOWN,
                // 最大回撤点直接标在绘图窗内（与坐标对齐），替代图外文字标注
                markPoint: troughPoint(ddSeries) }
            ]
          });
        }
        var dd = payload.drawdown || {};
        navBody.appendChild(el("div", "pg-card-sub",
          t("stock.kdj.troughPrefix") + fmt(dd.max_drawdown, 2) + "%（" + (dd.peak_date || t("value.dash")) + " → " + (dd.trough_date || t("value.dash")) +
          t("stock.kdj.currentSuffix") + fmt(dd.current_drawdown, 2) + "%"));
        navBody.setAttribute("data-state", "default");
      }
    }

    // ---- F-03 阶段业绩 ----
    var perfBody = document.getElementById("f03-body");
    if (perfBody) {
      var perf = payload.performance || {};
      if (perf.error || !(perf.periods || []).length) {
        setEmpty(perfBody, perf.error || t("fund.empty.perf"), function () { loadFund(); });
      } else {
        setBadges(document.getElementById("F-03"), perf.source || "蛋卷", perf.caliber || t("fund.caliber.perf"), false);
        renderTable(
          perfBody,
          [
            { title: t("fund.perf.col.period"), key: "period", cls: "name" },
            { title: t("fund.perf.col.growth"), cls: "num", render: function (row) { return pctNode(row.growth); } },
            { title: t("fund.perf.col.rank"), key: "rank" }
          ],
          perf.periods,
          null,
          "short"
        );
      }
    }

    // ---- F-04 重仓股 ----
    var holdBody = document.getElementById("f04-body");
    if (holdBody) {
      var holding = payload.holding || {};
      if (holding.error || !(holding.items || []).length) {
        setEmpty(holdBody, holding.error || t("fund.empty.holding"), function () { loadFund(); });
      } else {
        setBadges(document.getElementById("F-04"), holding.source || "东财季报",
          (holding.caliber || "") + t("stock.profile.dataCaliber") + (holding.quarter || t("value.dash")), false);
        renderTable(
          holdBody,
          [
            { title: t("th.code"), key: "code", cls: "num" },
            { title: t("th.name"), key: "name", cls: "name" },
            { title: t("fund.holding.col.ratio"), cls: "num", render: function (row) { return fmt(row.ratio, 2); } },
            { title: t("fund.holding.col.shares"), cls: "num", render: function (row) { return fmt(row.shares, 2); } },
            { title: t("fund.holding.col.value"), cls: "num", render: function (row) { return fmt(row.value, 2); } }
          ],
          holding.items,
          function (row) {
            window.location.hash = "#/stock?symbol=" + encodeURIComponent(row.code) + "&market=a";
          },
          "short"
        );
      }
    }

    // ---- F-05 档案与分红 ----
    var infoBody = document.getElementById("f05-body");
    if (infoBody) {
      var div = payload.dividend || {};
      infoBody.innerHTML = "";
      var group = el("div", "pg-quote-meta");
      [
        [t("fund.profile.bank"), info.trustee],
        [t("fund.profile.company"), info.keeper],
        // R2-04：雪球档案键名与 i18n 统一为「基金规模」；若接口为旧缓存，
        // 回退到蛋卷归一字段 info.scale，两处兜底避免再显示"无数据"
        [t("fund.profile.size"), (info.xq && info.xq[t("fund.kv.scale")]) || info.scale]
      ].forEach(function (item) {
        var cell = el("span", "", item[0] + "：");
        cell.appendChild(el("b", "", item[1] || t("value.none")));
        group.appendChild(cell);
      });
      infoBody.appendChild(group);
      infoBody.appendChild(el("div", "pg-card-sub", t("fund.profile.dividend") + (div.caliber || "")));
      if ((div.items || []).length) {
        var list = el("div", "pg-news-list");
        div.items.slice(0, 6).forEach(function (row) {
          var line = el("div", "pg-news-item");
          line.appendChild(el("span", "pg-news-title", (row.record_date || "") + " 每10份派 " + (row.per_10 || t("value.dash")) + " 元"));
          line.appendChild(el("span", "pg-news-time", row.pay_date || ""));
          list.appendChild(line);
        });
        infoBody.appendChild(list);
      } else {
        infoBody.appendChild(el("div", "pg-card-sub", t("fund.profile.noDividend")));
      }
      infoBody.setAttribute("data-state", "default");
    }

    // ---- F-06 持仓分布（禁用说明） ----
    var posBody = document.getElementById("f06-body");
    if (posBody) {
      posBody.innerHTML = "";
      var boxState = el("div", "pg-state");
      boxState.appendChild(el("div", "pg-state-title", t("fund.holdUnavailable")));
      boxState.appendChild(el("div", "pg-state-hint",
        t("fund.holdDisabled")));
      posBody.appendChild(boxState);
      posBody.setAttribute("data-state", "empty");
    }
  }

  // =============================================================
  // 十、#/portfolio 持仓管理（批次 5.3）
  // 结构：P-01 汇总 / P-02 新增表单 / P-03 持仓表 / P-04 市场分布
  // =============================================================
  /** 构建持仓页骨架。
   * @param {HTMLElement} container DOM 容器节点
   * @returns {*} 处理结果
   */
  function buildPortfolioShell(container) {
    container.innerHTML = "";
    var form = el("div", "page-toolbar");
    form.appendChild(el("span", "pg-card-sub", t("portfolio.addPosition")));
    var market = document.createElement("select");
    market.id = "pf-market";
    market.style.cssText =
      "background:var(--bg-panel);border:1px solid var(--border);color:var(--text);border-radius:5px;padding:4px 8px";
    [["a", t("stock.market.a")], ["hk", t("sectors.market.hk")], ["us", t("sectors.market.us")], ["fund", t("page.fund")]].forEach(function (item) {
      var option = document.createElement("option");
      option.value = item[0];
      option.textContent = item[1];
      market.appendChild(option);
    });
    form.appendChild(market);

    /** 构造一个带 id/占位文案/宽度的输入框（估值页查询表单用）。
     * @param {string} id 元素 id / 组件 id
     * @param {*} placeholder 占位文案
     * @param {*} width 宽度（栅格列数）
     * @returns {*} 处理结果
     */
    function field(id, placeholder, width) {
      var input = document.createElement("input");
      input.id = id;
      input.placeholder = placeholder;
      input.style.cssText =
        "width:" + (width || 110) + "px;background:var(--bg-panel);border:1px solid var(--border);" +
        "color:var(--text);border-radius:5px;padding:4px 8px";
      return input;
    }

    form.appendChild(field("pf-symbol", t("th.code"), 100));
    form.appendChild(field("pf-name", t("portfolio.namePlaceholder"), 140));
    form.appendChild(field("pf-shares", t("portfolio.col.shares"), 100));
    form.appendChild(field("pf-cost", t("portfolio.costPlaceholder"), 90));
    var add = el("button", "pg-btn", t("portfolio.addBtn"));
    add.type = "button";
    add.id = "pf-add";
    form.appendChild(add);
    form.appendChild(el("span", "pg-spacer"));
    var refresh = el("button", "pg-btn", t("action.refresh"));
    refresh.type = "button";
    form.appendChild(refresh);
    container.appendChild(form);

    var grid = el("div", "pg-grid");
    grid.id = "portfolio-grid";
    var summary = buildCard("P-01", t("portfolio.summaryTitle"), "g-12", t("portfolio.summarySub"));
    summary.body.id = "p01-body";
    grid.appendChild(summary.card);
    // 第七轮 M4：归因（赚亏从哪来 / 风险堆在哪），与 P-01 同用一份台账
    var attr = buildCard("P-05", t("portfolio.attr.title"), "g-12", t("portfolio.attr.sub"));
    attr.body.id = "p05-body";
    grid.appendChild(attr.card);
    var dist = buildCard("P-04", t("portfolio.distTitle"), "g-5", t("portfolio.distSub"));
    dist.body.id = "p04-body";
    grid.appendChild(dist.card);
    var table = buildCard("P-03", t("portfolio.tableTitle"), "g-12", t("portfolio.tableSub"));
    table.body.id = "p03-body";
    grid.appendChild(table.card);
    // 第七轮 M10：组合风险指标（波动/回撤/夏普/α/β/相关性），与归因同用一份台账
    var riskCard = buildCard("P-06", t("risk.title"), "g-12", t("risk.sub"));
    riskCard.body.id = "p06-body";
    grid.appendChild(riskCard.card);
    // 第七轮 M11：xlsx 一次性导入（只读导入，覆盖当前台账）
    var xlsxCard = buildCard("P-07", t("xlsx.title"), "g-12", t("xlsx.sub"));
    xlsxCard.body.id = "p07-body";
    grid.appendChild(xlsxCard.card);
    container.appendChild(grid);

    add.addEventListener("click", function () {
      var payload = {
        market: market.value,
        symbol: (document.getElementById("pf-symbol").value || "").trim(),
        name: (document.getElementById("pf-name").value || "").trim(),
        shares: document.getElementById("pf-shares").value,
        cost: document.getElementById("pf-cost").value
      };
      if (!payload.symbol || !payload.shares) {
        toast(t("portfolio.inputRequired"));
        return;
      }
      apiSend("POST", "/api/portfolio", payload)
        .then(function () {
          toast(t("portfolio.added"));
          ["pf-symbol", "pf-name", "pf-shares", "pf-cost"].forEach(function (id) {
            var node = document.getElementById(id);
            if (node) {
              node.value = "";
            }
          });
          loadPortfolio();
        })
        .catch(function (error) {
          toast(error.message);
        });
    });
    refresh.addEventListener("click", function () {
      loadPortfolio();
    });
  }

  /** 渲染组合归因（第七轮 M4）：权重/贡献、行业暴露、集中度、情景。
   *
   * 与 P-01 的分工：P-01 回答「值多少」，本卡回答「赚亏从哪来、风险堆在哪」。
   * 共用同一份台账（/api/portfolio），不另存数据。
   *
   * @returns {void} 无返回值（异步渲染）
   */
  function loadPortfolioAttribution() {
    var body = document.getElementById("p05-body");
    if (!body) {
      return;
    }
    setLoading(body);
    api("/api/portfolio/attribution").then(function (data) {
      var totals = data.totals || {};
      var card = document.getElementById("P-05");
      setBadges(card, "本机台账 data/portfolio.json", data.caliber);
      body.innerHTML = "";
      if (!totals.count) {
        setEmpty(body, t("portfolio.attr.empty"), loadPortfolioAttribution);
        return;
      }

      // —— 头部 KPI ——
      var kpis = el("div", "pg-kpis");
      var conc = data.concentration || {};
      [
        [t("portfolio.attr.value"), fmt(totals.value, 2), "flat", t("yjb.kpi.yuan")],
        [t("portfolio.attr.pnl"), (totals.pnl > 0 ? "+" : "") + fmt(totals.pnl, 2),
          pctClass(totals.pnl), fmtPct(totals.pnl_pct)],
        [t("portfolio.attr.hhi"), fmt(conc.hhi, 4), "flat", conc.level || ""],
        [t("portfolio.attr.count"), String(totals.count), "flat", t("portfolio.attr.countTag")]
      ].forEach(function (row) {
        var box = el("div", "pg-kpi");
        box.appendChild(el("div", "pg-kpi-k", row[0]));
        box.appendChild(el("div", "pg-kpi-v " + row[2], row[1]));
        if (row[3]) {
          box.appendChild(el("div", "pg-kpi-x", row[3]));
        }
        kpis.appendChild(box);
      });
      body.appendChild(kpis);

      // —— 权重与贡献表 ——
      var tableHost = el("div", "");
      body.appendChild(tableHost);
      renderTable(tableHost, [
        { title: t("portfolio.col.name"), cls: "l",
          /** 标的：代码 + 名称 + 行业。
           * @param {Object} row 归因行
           * @returns {HTMLElement} 单元格节点
           */
          render: function (row) {
            var box = el("span", "");
            box.appendChild(el("b", "", row.symbol));
            box.appendChild(document.createTextNode(" " + (row.name || "")));
            if (row.industry) {
              // 东财 F10 的 industry 是「食品饮料-饮料-白酒」这类全路径，
              // 卡片列宽有限，展示末级行业名，完整路径放 title 供悬浮查看
              var shortIndustry = String(row.industry).split(/[-—/]/).pop();
              var tag = el("span", "pg-card-sub", " · " + shortIndustry);
              tag.title = row.industry;
              box.appendChild(tag);
            }
            return box;
          } },
        { title: t("portfolio.attr.weight"), num: true,
          render: function (row) { return fmtPct(row.weight); } },
        { title: t("portfolio.attr.pnlPct"), num: true,
          render: function (row) { return el("span", pctClass(row.pnl_pct), fmtPct(row.pnl_pct)); } },
        { title: t("portfolio.attr.contribution"), num: true,
          render: function (row) {
            return el("span", pctClass(row.contribution),
              (row.contribution > 0 ? "+" : "") + fmt(row.contribution, 2) + "pt");
          } },
        { title: t("portfolio.attr.market"), cls: "l",
          render: function (row) { return row.market_label || row.market; } }
      ], data.items || [], null);

      // —— 行业暴露 + 情景 ——
      var bottom = el("div", "pg-grid");
      var ind = el("div", "card g-7");
      ind.appendChild(el("div", "card-title", t("portfolio.attr.byIndustry")));
      (data.by_industry || []).slice(0, 8).forEach(function (item) {
        var line = el("div", "portfolio-expo");
        line.appendChild(el("span", "portfolio-expo-name", item.name));
        var bar = el("div", "portfolio-expo-bar");
        var fill = el("i", "");
        fill.style.width = Math.min(100, item.weight || 0) + "%";
        bar.appendChild(fill);
        line.appendChild(bar);
        line.appendChild(el("span", "portfolio-expo-val", fmtPct(item.weight)));
        ind.appendChild(line);
      });
      bottom.appendChild(ind);

      var scen = el("div", "card g-5");
      scen.appendChild(el("div", "card-title", t("portfolio.attr.scenario")));
      (data.scenarios || []).forEach(function (item) {
        var line = el("div", "portfolio-scen");
        line.appendChild(el("span", "", item.label));
        line.appendChild(el("b", "down", fmt(item.impact, 0) + " 元"));
        line.appendChild(el("span", "pg-card-sub", "（" + fmtPct(item.impact_pct) + "）"));
        scen.appendChild(line);
      });
      scen.appendChild(el("div", "pg-card-sub", t("portfolio.attr.scenarioNote")));
      bottom.appendChild(scen);
      body.appendChild(bottom);

      if ((data.missing || []).length) {
        body.appendChild(el("div", "pg-card-sub", t("portfolio.attr.missing", { list: data.missing.join("；") })));
      }
      body.appendChild(el("div", "pg-card-sub", data.caliber || ""));
      body.setAttribute("data-state", "default");
    }).catch(function (error) {
      setError(body, error.message || t("state.errorHint"), loadPortfolioAttribution);
    });
  }

  /** 渲染持仓页。 */
  function renderPortfolio() {
    var container = document.getElementById(VIEW_IDS.portfolio);
    if (!container.querySelector("#portfolio-grid")) {
      buildPortfolioShell(container);
    }
    loadPortfolio();
    loadPortfolioAttribution();
    loadPortfolioRisk();
    paintXlsxImport();
  }

  // =============================================================
  // 第七轮 M10：组合风险指标（波动 / 回撤 / 夏普 / α / β / 相关性）
  // 口径：按当前持仓权重回溯（非真实逐日净值）；场外基金不纳入；样本<30 日只给数不给结论
  // =============================================================
  /** 加载并渲染组合风险指标。
   *
   * @returns {void} 无返回值（异步渲染）
   */
  function loadPortfolioRisk() {
    var body = document.getElementById("p06-body");
    if (!body) {
      return;
    }
    setLoading(body);
    api("/api/portfolio/risk", {}).then(function (data) {
      body.innerHTML = "";
      if (!data.available) {
        body.appendChild(el("div", "pg-state", data.caliber || t("risk.empty")));
        (data.missing || []).forEach(function (note) {
          body.appendChild(el("div", "pg-card-sub", note));
        });
        body.setAttribute("data-state", "empty");
        return;
      }
      var m = data.metrics || {};
      var table = el("table", "pg-table");
      var head = el("thead");
      var headRow = el("tr");
      [t("risk.col.metric"), t("risk.col.value"), t("risk.col.note")].forEach(function (title, index) {
        headRow.appendChild(el("th", index > 0 ? "num" : "", title));
      });
      head.appendChild(headRow);
      table.appendChild(head);
      var tbody = el("tbody");
      [
        [t("risk.annualVol"), fmt(m.annual_vol, 2) + "%", t("risk.annualVolNote", { bench: fmt(m.benchmark_annual, 2) })],
        [t("risk.maxDrawdown"), (m.max_drawdown === null ? t("value.none") : fmt(m.max_drawdown, 2) + "%"),
          m.max_drawdown_days ? t("risk.maxDrawdownNote", { days: m.max_drawdown_days }) : ""],
        [t("risk.sharpe"), fmt(m.sharpe, 2), t("risk.sharpeNote")],
        [t("risk.alpha"), (m.alpha === null ? t("value.none") : fmtPct(m.alpha)), "R²=" + fmt(m.r2, 2)],
        [t("risk.beta"), fmt(m.beta, 2), t("risk.betaNote")],
        [t("risk.annualReturn"), fmtPct(m.annual_return), t("risk.riskFree", { rate: fmt(m.risk_free, 2), source: m.risk_free_source || "" })]
      ].forEach(function (row) {
        var tr = el("tr");
        tr.appendChild(el("td", "", row[0]));
        tr.appendChild(el("td", "num", row[1]));
        tr.appendChild(el("td", "", row[2] || "—"));
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      body.appendChild(table);
      body.appendChild(el("div", "pg-card-sub", data.caliber || ""));

      var pairs = (data.correlation || []).filter(function (item) { return item.value !== null; });
      if (pairs.length) {
        body.appendChild(el("div", "pg-card-sub", t("risk.corrTitle")));
        var corrBox = el("div", "risk-corr");
        pairs.forEach(function (item) {
          var line = el("div", "risk-corr-row");
          line.appendChild(el("span", "", item.left + " ↔ " + item.right));
          line.appendChild(el("b", item.high ? "down" : "", fmt(item.value, 3)
            + (item.high ? t("risk.corrHigh") : "")));
          corrBox.appendChild(line);
        });
        body.appendChild(corrBox);
      }
      if (!data.reliable) {
        body.appendChild(el("div", "pg-card-sub", t("risk.notReliable", { days: data.sample_days })));
      }
      body.setAttribute("data-state", "default");
    }).catch(function (error) {
      setError(body, error.message || t("state.errorHint"), loadPortfolioRisk);
      body.setAttribute("data-state", "error");
    });
  }

  // =============================================================
  // 第七轮 M11：xlsx 一次性导入（base64 提交；预览 → 二次确认覆盖）
  // =============================================================
  /** 渲染 xlsx 导入区（文件选择 + 预览 + 确认）。
   *
   * @returns {void} 无返回值
   */
  function paintXlsxImport() {
    var body = document.getElementById("p07-body");
    if (!body) {
      return;
    }
    body.innerHTML = "";
    var bar = el("div", "page-toolbar");
    var input = document.createElement("input");
    input.type = "file";
    input.accept = ".xlsx,.xls";
    input.id = "p07-file";
    var previewBtn = el("button", "pg-btn primary", t("xlsx.preview"));
    previewBtn.type = "button";
    bar.appendChild(el("span", "pg-card-sub", t("xlsx.hint")));
    bar.appendChild(input);
    bar.appendChild(previewBtn);
    body.appendChild(bar);
    var result = el("div", "");
    body.appendChild(result);
    body.setAttribute("data-state", "default");

    var cached = null; // {filename, content_base64}

    /** 读取文件为 base64 并请求预览。
     *
     * @returns {void} 无返回值
     */
    function doPreview() {
      var file = input.files && input.files[0];
      if (!file) {
        toast(t("xlsx.needFile"));
        return;
      }
      var reader = new FileReader();
      reader.onload = function () {
        var dataUrl = String(reader.result || "");
        var base64 = dataUrl.indexOf(",") >= 0 ? dataUrl.split(",")[1] : dataUrl;
        cached = { filename: file.name, content_base64: base64 };
        result.innerHTML = "";
        result.appendChild(el("div", "pg-state", t("xlsx.parsing")));
        apiSend("POST", "/api/portfolio/import-xlsx", {
          filename: cached.filename, content_base64: cached.content_base64, confirm: false
        }).then(function (data) {
          renderPreview(data);
        }).catch(function (error) {
          result.innerHTML = "";
          result.appendChild(el("div", "pg-state", error.message || t("state.errorHint")));
          body.setAttribute("data-state", "error");
        });
      };
      reader.readAsDataURL(file);
    }

    /** 渲染预览表（标红不可导入行 + 确认按钮）。
     *
     * @param {Object} data 预览结果
     * @returns {void} 无返回值
     */
    function renderPreview(data) {
      result.innerHTML = "";
      var summary = data.summary || {};
      var kpis = el("div", "pg-kpis");
      [
        [t("xlsx.kpi.total"), String(summary.total || 0)],
        [t("xlsx.kpi.importable"), String(summary.importable || 0)],
        [t("xlsx.kpi.rejected"), String(summary.rejected || 0)]
      ].forEach(function (pair) {
        var box = el("div", "pg-kpi");
        box.appendChild(el("div", "pg-kpi-k", pair[0]));
        box.appendChild(el("div", "pg-kpi-v flat", pair[1]));
        kpis.appendChild(box);
      });
      result.appendChild(kpis);

      var table = el("div", "table-scroll");
      renderTable(table, [
        { key: "line", title: "#", num: true },
        { key: "symbol", title: t("xlsx.col.symbol"), render: function (row) {
            return el("b", row.ok ? "" : "down", row.symbol);
          } },
        { key: "name", title: t("xlsx.col.name") },
        { key: "market", title: t("xlsx.col.market") },
        { key: "shares", title: t("xlsx.col.shares"), num: true, render: function (row) { return fmt(row.shares, 2); } },
        { key: "cost", title: t("xlsx.col.cost"), num: true, render: function (row) { return fmt(row.cost, 2); } },
        { key: "price", title: t("xlsx.col.price"), num: true, render: function (row) { return fmt(row.price, 2); } },
        { key: "value", title: t("xlsx.col.value"), num: true, render: function (row) { return fmt(row.value, 0); } },
        { key: "status", title: t("xlsx.col.status"), render: function (row) {
            if (row.ok) {
              return el("span", "down", t("xlsx.ok"));
            }
            return el("span", "up", (row.errors || []).join("；"));
          } }
      ], data.rows || [], {});
      result.appendChild(table);
      (data.warnings || []).forEach(function (note) {
        result.appendChild(el("div", "pg-card-sub", note));
      });

      var bar2 = el("div", "page-toolbar");
      var confirmBtn = el("button", "pg-btn primary", t("xlsx.confirm"));
      confirmBtn.type = "button";
      confirmBtn.disabled = !(summary.importable > 0);
      confirmBtn.addEventListener("click", function () {
        if (!cached) {
          return;
        }
        confirmBtn.disabled = true;
        apiSend("POST", "/api/portfolio/import-xlsx", {
          filename: cached.filename, content_base64: cached.content_base64, confirm: true
        }).then(function (payload) {
          var done = payload.result || {};
          toast(t("xlsx.importedToast", { count: done.imported || 0 }));
          result.innerHTML = "";
          result.appendChild(el("div", "pg-state",
            t("xlsx.importedNote", { count: done.imported || 0, skipped: done.skipped || 0 })));
          loadPortfolio();
          loadPortfolioRisk();
        }).catch(function (error) {
          toast(error.message || t("state.errorHint"));
          confirmBtn.disabled = false;
        });
      });
      bar2.appendChild(confirmBtn);
      bar2.appendChild(el("span", "pg-card-sub", t("xlsx.overwriteNote")));
      result.appendChild(bar2);
      body.setAttribute("data-state", "default");
    }

    previewBtn.addEventListener("click", doPreview);
  }

  /** 拉取持仓与分析。
   * @returns {*} 处理结果
   */
  function loadPortfolio() {
    var bodies = ["p01-body", "p03-body", "p04-body", "p05-body"].map(function (id) {
      return document.getElementById(id);
    });
    bodies.forEach(function (body) {
      if (body) {
        setLoading(body);
      }
    });
    Promise.all([
      api("/api/portfolio", {}).catch(function (e) { return { error: e.message }; }),
      api("/api/portfolio/analysis", {}).catch(function (e) { return { error: e.message }; })
    ]).then(function (result) {
      paintPortfolio({ listing: result[0], analysis: result[1] });
    });
  }

  /** 渲染持仓汇总 / 明细 / 分布。
   * @param {*} payload 渲染载荷（聚合后的数据）
   * @returns {*} 处理结果
   */
  function paintPortfolio(payload) {
    var listing = payload.listing || {};
    var analysis = payload.analysis || {};

    // ---- P-01 汇总 ----
    var p01 = document.getElementById("p01-body");
    if (p01) {
      if (listing.error || analysis.error) {
        setError(p01, listing.error || analysis.error, function () { loadPortfolio(); });
      } else if (!(listing.items || []).length) {
        setEmpty(p01, t("portfolio.empty.listing"), function () { loadPortfolio(); });
      } else {
        setBadges(document.getElementById("P-01"), analysis.source || t("portfolio.source.local"), analysis.caliber || "", false);
        var totals = analysis.totals || {};
        p01.innerHTML = "";
        var grid = el("div", "pg-metric-grid");
        [
          [t("portfolio.kv.valueCny"), fmt(totals.cny_market_value, 2), "flat"],
          [t("portfolio.kv.costCny"), fmt(totals.cny_cost, 2), "flat"],
          [t("portfolio.kv.pnlCny"), fmt(totals.cny_pnl, 2), pctClass(totals.cny_pnl)],
          [t("portfolio.kv.count"), (analysis.count || 0) + " 条", "flat"],
          [t("portfolio.kv.fx"), analysis.fx && analysis.fx.USD ? ("USD " + fmt(analysis.fx.USD, 4) + " / HKD " + fmt(analysis.fx.HKD, 4)) : t("value.none"), "flat"]
        ].forEach(function (item) {
          var cell = el("div", "pg-metric-cell");
          cell.appendChild(el("div", "pg-metric-label", item[0]));
          cell.appendChild(el("div", "pg-metric-value " + item[2], item[1]));
          grid.appendChild(cell);
        });
        p01.appendChild(grid);
        if (analysis.fx_missing) {
          p01.appendChild(el("div", "pg-card-sub", t("portfolio.fxMissing")));
        }
        p01.setAttribute("data-state", "default");
      }
    }

    // ---- P-04 市场分布 ----
    var p04 = document.getElementById("p04-body");
    if (p04) {
      var dist = (analysis.distribution || []).filter(function (item) { return item.value_cny; });
      if (!dist.length) {
        setEmpty(p04, t("portfolio.empty.distribution"), function () { loadPortfolio(); });
      } else {
        p04.innerHTML = "";
        var box = el("div", "pg-chart small");
        p04.appendChild(box);
        var chart = mountChart(box);
        if (chart) {
          chart.setOption({
            backgroundColor: "transparent",
            tooltip: {
              /** 持仓分布饼图悬浮说明：名称 + 市值（元）+ 占比%。
               * @param {*} info 信息对象
               * @returns {*} 处理结果
               */
              formatter: function (info) {
                return info.name + "<br/>" + fmt(info.value, 2) + " 元（" + fmt(info.percent, 2) + "%）";
              }
            },
            legend: { bottom: 0, textStyle: { color: chartTheme().text } },
            series: [
              {
                type: "pie",
                radius: ["45%", "68%"],
                center: ["50%", "45%"],
                label: { color: chartTheme().text, fontSize: 11, formatter: "{b}\n{d}%" },
                data: dist.map(function (item) {
                  return { name: item.label, value: item.value_cny };
                })
              }
            ]
          });
        }
        p04.setAttribute("data-state", "default");
      }
    }

    // ---- P-03 持仓明细 ----
    var p03 = document.getElementById("p03-body");
    if (p03) {
      var rows = (analysis.items || []).filter(function (item) { return item.id; });
      if (!rows.length) {
        setEmpty(p03, t("portfolio.empty.detail"), function () { loadPortfolio(); });
      } else {
        var wrap = el("div", "");
        p03.innerHTML = "";
        p03.appendChild(wrap);
        renderTable(
          wrap,
          [
            { title: t("portfolio.col.market"), key: "market_label" },
            { title: t("th.code"), key: "symbol", cls: "num" },
            { title: t("th.name"), key: "name", cls: "name" },
            { title: t("portfolio.col.shares"), cls: "num", render: function (row) { return fmt(row.shares, 2); } },
            { title: t("portfolio.col.cost"), cls: "num", render: function (row) { return fmt(row.cost, 4); } },
            { title: t("portfolio.col.price"), cls: "num", render: function (row) { return fmt(row.price, 4); } },
            { title: t("portfolio.col.value"), cls: "num", render: function (row) { return fmt(row.market_value, 2); } },
            { title: t("portfolio.col.pnl"), cls: "num", render: function (row) { return pctNode(row.pnl); } },
            { title: t("portfolio.col.pnlPct"), cls: "num", render: function (row) { return pctNode(row.pnl_pct); } },
            {
              title: t("portfolio.col.action"),
              /** 持仓表「操作」列：渲染删除按钮，点击后调用 DELETE 并重载。
               * @param {*} row 单行记录
               * @returns {*} 处理结果
               */
              render: function (row) {
                var button = el("button", "pg-btn", t("portfolio.col.delete"));
                button.type = "button";
                button.addEventListener("click", function (event) {
                  event.stopPropagation();
                  apiSend("DELETE", "/api/portfolio/" + encodeURIComponent(row.id))
                    .then(function () {
                      toast(t("portfolio.deleted"));
                      loadPortfolio();
                    })
                    .catch(function (error) {
                      toast(error.message);
                    });
                });
                return button;
              }
            }
          ],
          rows,
          null,
          null
        );
        // 有持仓时表格渲染完成必须显式收口，否则四态巡检一直停在 loading
        p03.setAttribute("data-state", "default");
      }
    }
  }

  // =============================================================
  // 十一、#/connectors 数据连接器（批次 5.4）
  // 展示：数据源开关 + 三市场能力矩阵 + D3 缺口（如实标注不可用）
  // =============================================================
  /** 渲染连接器页。
   * @returns {*} 处理结果
   */
  /** 源状态中文文案（与后端 capabilities 同口径，缺失时按启用/探测推导）。
   * @param {Object} item 数据源条目
   * @param {boolean} userDisabled 用户是否停用
   * @returns {string} 中文状态
   */
  function connectorStateText(item, userDisabled) {
    if (userDisabled) {
      return t("connectors.state.userDisabled");
    }
    if (item.status) {
      return String(item.status);
    }
    if (item.active) {
      return t("connectors.state.enabled");
    }
    if (item.enabled) {
      return item.stage === "installed" ? t("connectors.state.installed") : t("connectors.state.pending");
    }
    return t("connectors.state.disabled");
  }

  /** 源状态色阶：active=正常 / enabled 未探测=安装待接 / 停用=down / optin=warn。
   * @param {Object} item 数据源条目
   * @param {boolean} userDisabled 用户是否停用
   * @returns {string} CSS 类名
   */
  function connectorTone(item, userDisabled) {
    if (userDisabled) {
      return "down";
    }
    if (item.active) {
      return "flat";
    }
    if (item.stage === "optin") {
      return "warn";
    }
    return "down";
  }

  /** 渲染连接器页：C-01 总览 + C-02 源表格 + C-03 降级链 + C-04 四市场矩阵（R3-03/R3-13）。
   * @returns {void} 无返回值（异步渲染）
   */
  function renderConnectors() {
    var container = document.getElementById(VIEW_IDS.connectors);
    container.innerHTML = "";
    var grid = el("div", "pg-grid");

    var card = buildCard("C-01", t("connectors.cardTitle"), "g-12", t("connectors.cardSub"));
    card.body.id = "c01-body";
    grid.appendChild(card.card);
    // LG-01 整改：C-02 明细表独立成卡（基准 §五5.6：C-01 g-12 / C-02 g-12 / C-03 g-6 / C-04 g-6），
    // 此前折叠在 C-01 body 内导致"统计+缓存+源表"挤在一卡，网格结构偏离设计基准。
    var tableCard = buildCard("C-02", t("connectors.tableTitle"), "g-12", t("connectors.tableSub"));
    tableCard.body.id = "c02-body";
    grid.appendChild(tableCard.card);
    var chainCard = buildCard("C-03", t("connectors.chainTitle"), "g-6", t("connectors.chainSub"));
    chainCard.body.id = "c03-body";
    grid.appendChild(chainCard.card);
    var matrixCard = buildCard("C-04", t("connectors.matrixTitle"), "g-6", t("connectors.matrixSub"));
    matrixCard.body.id = "c04-body";
    grid.appendChild(matrixCard.card);
    container.appendChild(grid);

    var body = document.getElementById("c01-body");
    setLoading(body);
    setLoading(document.getElementById("c02-body"));
    document.getElementById("c03-body").setAttribute("data-state", "default");
    document.getElementById("c03-body").appendChild(el("div", "pg-card-sub", t("connectors.chainNote")));
    document.getElementById("c04-body").setAttribute("data-state", "default");

    api("/api/connectors/status", {})
      .then(function (data) {
        setBadges(card.card, data.source, data.caliber, false);
        body.innerHTML = "";

        var disabledSet = {};
        (data.disabled || []).forEach(function (name) {
          disabledSet[name] = true;
        });
        var sources = data.datasources || [];
        var enabledCount = sources.filter(function (item) {
          return item.enabled && !disabledSet[item.label];
        }).length;
        var activeCount = sources.filter(function (item) { return item.active; }).length;
        var userDisabledCount = sources.filter(function (item) {
          return disabledSet[item.label];
        }).length;
        var degradedCount = Math.max(0, sources.length - activeCount - userDisabledCount);

        // C-01 四统计 + 缓存管理
        var statGrid = el("div", "pg-metric-grid");
        [
          [t("connectors.stat.enabled"), enabledCount + " / " + sources.length, "flat"],
          [t("connectors.stat.active"), String(activeCount), "flat"],
          [t("connectors.stat.degraded"), String(degradedCount), "warn"],
          [t("connectors.stat.disabled"), String(userDisabledCount), "down"]
        ].forEach(function (item) {
          var cell = el("div", "pg-metric-cell");
          cell.appendChild(el("div", "pg-metric-label", item[0]));
          cell.appendChild(el("div", "pg-metric-value " + item[2], item[1]));
          statGrid.appendChild(cell);
        });
        body.appendChild(statGrid);

        var cacheRow = el("div", "page-toolbar");
        cacheRow.appendChild(el("span", "pg-toolbar-label", t("connectors.cacheLabel")));
        var clearBtn = el("button", "pg-btn", t("set.cache.clear"));
        clearBtn.type = "button";
        clearBtn.addEventListener("click", function () {
          clearBtn.disabled = true;
          clearBtn.textContent = t("set.cache.clearing");
          fetch("/api/cache/clear", { method: "POST" })
            .then(function (response) { return response.json(); })
            .then(function (result) {
              toast(result.error ? t("toast.cacheFailed") : t("toast.cacheCleared"));
            })
            .catch(function () { toast(t("toast.cacheFailed")); })
            .then(function () {
              clearBtn.disabled = false;
              clearBtn.textContent = t("set.cache.clear");
            });
        });
        cacheRow.appendChild(clearBtn);
        body.appendChild(cacheRow);

        // C-02：源表格（状态 pill / 启停 / 密钥 / 测试 / 限流 / TTL / 降级链）
        var tableBody = document.getElementById("c02-body");
        setBadges(tableCard.card, data.source, data.caliber, false);
        tableBody.innerHTML = "";
        var tableHost = el("div", "");
        tableBody.appendChild(tableHost);
        var rows = sources.map(function (item) {
          var userDisabled = !!disabledSet[item.label];
          return {
            label: item.label,
            state: connectorStateText(item, userDisabled),
            tone: connectorTone(item, userDisabled),
            enabled: item.enabled && !userDisabled,
            key: item.label.indexOf("ai") >= 0 || item.label === "akshare（东财/新浪等）"
              ? t("connectors.key.public")
              : t("connectors.key.public"),
            rate: item.rate_limit || "未登记",
            ttl: item.ttl || "—",
            fallback: item.fallback || "—",
            detail: item.detail || ""
          };
        });
        renderTable(tableHost, [
          {
            title: t("connectors.col.source"), cls: "l",
            /** 源名列：中文标签 + 探测说明。
             * @param {Object} row 数据行
             * @returns {HTMLElement} 单元格节点
             */
            render: function (row) {
              var box = el("div", "");
              box.appendChild(el("b", "", row.label));
              if (row.detail) {
                box.appendChild(el("div", "pg-card-sub", row.detail));
              }
              return box;
            }
          },
          {
            title: t("connectors.col.state"),
            /** 状态 pill：按 tone 上色。
             * @param {Object} row 数据行
             * @returns {HTMLElement} 单元格节点
             */
            render: function (row) {
              return el("span", "chip " + (row.tone === "flat" ? "up" : row.tone === "warn" ? "warn" : "down"), row.state);
            }
          },
          {
            title: t("connectors.col.key"),
            /** 密钥列：公开接口如实标注；AI 服务商密钥在设置页管理。
             * @param {Object} row 数据行
             * @returns {HTMLElement} 单元格节点
             */
            render: function (row) {
              return el("span", "pg-card-sub", row.key);
            }
          },
          {
            title: t("connectors.col.rate"),
            /** 限流策略列。
             * @param {Object} row 数据行
             * @returns {HTMLElement} 单元格节点
             */
            render: function (row) {
              return el("span", "pg-card-sub", row.rate);
            }
          },
          { title: t("connectors.col.ttl"), key: "ttl", cls: "mono" },
          {
            title: t("connectors.col.fallback"),
            /** 降级链列。
             * @param {Object} row 数据行
             * @returns {HTMLElement} 单元格节点
             */
            render: function (row) {
              return el("span", "pg-card-sub", row.fallback);
            }
          },
          {
            title: t("connectors.col.actions"),
            /** 操作列：测试连接 + 启用/停用。
             * @param {Object} row 数据行
             * @returns {HTMLElement} 单元格节点
             */
            render: function (row) {
              var box = el("div", "page-toolbar");
              var testBtn = el("button", "pg-btn", t("connectors.testConn"));
              testBtn.type = "button";
              testBtn.addEventListener("click", function (event) {
                event.stopPropagation();
                testBtn.disabled = true;
                testBtn.textContent = t("connectors.testing");
                fetch("/api/connectors/test?source=" + encodeURIComponent(row.label), { method: "POST" })
                  .then(function (response) { return response.json(); })
                  .then(function (result) {
                    var payload = result.data || {};
                    testBtn.textContent = payload.ok ? t("connectors.testOk") : t("connectors.testFail");
                    toast(row.label + " · " + (payload.ok ? t("connectors.testOk") : t("connectors.testFail"))
                      + (payload.detail ? "（" + payload.detail + "）" : ""));
                  })
                  .catch(function () {
                    testBtn.textContent = t("connectors.testFail");
                    toast(t("connectors.testFail"));
                  })
                  .then(function () { testBtn.disabled = false; });
              });
              box.appendChild(testBtn);
              var toggleBtn = el("button", "pg-btn", row.enabled ? t("connectors.disable") : t("connectors.enable"));
              toggleBtn.type = "button";
              toggleBtn.addEventListener("click", function (event) {
                event.stopPropagation();
                fetch("/api/connectors/toggle", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ source: row.label, enabled: !row.enabled })
                }).then(function (response) { return response.json(); })
                  .then(function (result) {
                    if (result.error) {
                      toast(result.error);
                      return;
                    }
                    toast(result.data && result.data.enabled ? t("connectors.enabledToast") : t("connectors.disabledToast"));
                    renderConnectors();
                  })
                  .catch(function () { toast(t("connectors.toggleFail")); });
              });
              box.appendChild(toggleBtn);
              return box;
            }
          }
        ], rows, null, "tall");
        tableBody.appendChild(el("div", "pg-card-sub", t("connectors.keyNote")));
        tableBody.setAttribute("data-state", "default");

        // C-03：三级降级链说明
        var chainBody = document.getElementById("c03-body");
        chainBody.innerHTML = "";
        var chains = [
          t("connectors.chain.quote"),
          t("connectors.chain.fund"),
          t("connectors.chain.news"),
          t("connectors.chain.sector")
        ];
        var list = el("ul", "pg-news-list");
        chains.forEach(function (text) {
          var li = el("li", "pg-news-item");
          li.appendChild(el("span", "pg-news-title", text));
          list.appendChild(li);
        });
        chainBody.appendChild(list);
        chainBody.setAttribute("data-state", "default");

        // C-04：四市场能力矩阵（含场外基金列；缺口如实标注）
        var matrixBody = document.getElementById("c04-body");
        matrixBody.innerHTML = "";
        var features = [
          ["quote", t("connectors.feature.quote")],
          ["kline", t("connectors.feature.kline")],
          ["minute", t("connectors.feature.minute")],
          ["index", t("connectors.feature.index")],
          ["stock_news", t("connectors.feature.stockNews")],
          ["sector", t("connectors.feature.sector")]
        ];
        var matrixRows = features.map(function (pair) {
          var row = { feature: pair[1] };
          ["a", "hk", "us", "fund"].forEach(function (market) {
            var cell = ((data.markets || {})[market] || {})[pair[0]] || {};
            row[market] = cell.available
              ? "✓ " + (cell.source || "")
              : t("connectors.unavailableShort");
          });
          return row;
        });
        var matrixHost = el("div", "");
        matrixBody.appendChild(matrixHost);
        renderTable(matrixHost, [
          { title: t("connectors.featureLabel"), key: "feature", cls: "name" },
          { title: t("stock.market.a"), key: "a" },
          { title: t("sectors.market.hk"), key: "hk" },
          { title: t("sectors.market.us"), key: "us" },
          { title: t("nav.fund"), key: "fund" }
        ], matrixRows, null, null);

        if ((data.gaps || []).length) {
          matrixBody.appendChild(el("div", "pg-card-sub", t("connectors.gapTitle")));
          var gapList = el("ul", "pg-news-list");
          data.gaps.forEach(function (gap) {
            var li = el("li", "pg-news-item");
            li.appendChild(el("span", "pg-news-title", gap.market_label + " · " + gap.feature_label));
            li.appendChild(el("span", "chip down", t("connectors.unavailable")));
            li.appendChild(el("span", "pg-news-time", gap.reason));
            gapList.appendChild(li);
          });
          matrixBody.appendChild(gapList);
        }
        matrixBody.setAttribute("data-state", "default");
        body.setAttribute("data-state", "default");
      })
      .catch(function (error) {
        setError(body, error.message, function () {
          renderConnectors();
        });
        var tableBody = document.getElementById("c02-body");
        if (tableBody) {
          setError(tableBody, error.message, function () {
            renderConnectors();
          });
        }
      });
  }

  // =============================================================
  // 十二、#/yjb 养基宝绑定（阶段 7）
  // 合规告知书 → 扫码绑定（2s 轮询）→ 增强开关 → 解绑；token 前端不可见
  // =============================================================
  var yjbTimer = null;
  // 估算展示偏好（Y-05/Y-06 角标「一键关闭」）：true=盘中估算口径，false=回退 T+1 官方净值。
  // 只影响展示口径，不关闭数据通道；持久化在本机 localStorage，用户可随时恢复。
  var yjbPrefs = { estimates: true };
  // R6-11 / F6-12：账户维度单一状态源 —— 账户概览与持仓估值两张卡共用，
  // 切换一次两卡同时变（原缺陷：概览卡没有切换控件，三账户永远合并显示）。
  var yjbAccount = "all";
  /** 账户概览最近一次接口数据（切换账户时本地重绘，不重复请求养基宝）。 */
  var yjbCollectCache = null;
  try {
    yjbPrefs.estimates = window.localStorage.getItem("openbb-yjb-estimates") !== "0";
  } catch (error) {
    yjbPrefs.estimates = true; // 隐私模式/禁用存储时退回默认（开启估算）
  }

  /** 写入估算展示偏好。
   * @param {boolean} enabled 是否启用盘中估算口径
   * @returns {void} 无返回值
   */
  function setYjbEstimates(enabled) {
    yjbPrefs.estimates = !!enabled;
    try {
      window.localStorage.setItem("openbb-yjb-estimates", yjbPrefs.estimates ? "1" : "0");
    } catch (error) {
      // 存储不可用不影响本次会话的展示口径
    }
  }

  /** 估算口径角标（常驻）：非官方·拟合估算·仅供参考 + 一键切换 T+1。
   * 关闭后 Y-05 今日收益、Y-06 涨跌幅列一律留空（不补 0），并标注 T+1 官方净值为准。
   * @returns {HTMLElement} 角标节点
   */
  function yjbEstimateBadge() {
    var wrap = el("span", "yjb-est-badge");
    var text = yjbPrefs.estimates ? t("yjb.estimates.badge") : t("yjb.estimates.offBadge");
    wrap.appendChild(el("span", "badge-src " + (yjbPrefs.estimates ? "state-degraded" : ""), text));
    var button = el("button", "yjb-est-close", yjbPrefs.estimates ? t("yjb.estimates.close") : t("yjb.estimates.restore"));
    button.type = "button";
    button.title = yjbPrefs.estimates ? t("yjb.estimates.closeTip") : t("yjb.estimates.restoreTip");
    button.addEventListener("click", function () {
      setYjbEstimates(!yjbPrefs.estimates);
      toast(yjbPrefs.estimates ? t("yjb.estimates.onToast") : t("yjb.estimates.offToast"));
      loadYjbCollect();
      loadYjbHoldings(yjbAccount);
    });
    wrap.appendChild(button);
    return wrap;
  }

  /** 首启向导（Y-00）：冻结包 data 缺 token/签名盐时显式引导，禁止静默空态。
   * 「不得显示已绑定」由 status.bound 决定；本卡只在 setup_required 时出现。
   * @param {Object} data /api/fund/yjb/status 的 data 段
   * @returns {void} 无返回值
   */
  function paintYjbWizard(data) {
    var card = document.getElementById("Y-00");
    var body = document.getElementById("y00-body");
    if (!card || !body) {
      return;
    }
    if (!data || !data.setup_required) {
      card.hidden = true;
      return;
    }
    card.hidden = false;
    body.innerHTML = "";
    var missing = data.setup_missing || [];
    var missText = missing.map(function (key) {
      return key === "signing_salt" ? t("yjb.wizard.missSecret") : t("yjb.wizard.missToken");
    }).join(t("yjb.wizard.and"));
    body.appendChild(el("div", "pg-state-title", t("yjb.wizard.title") + " " + missText));
    body.appendChild(el("div", "pg-state-hint", t("yjb.wizard.desc")));
    var list = el("ol", "yjb-wizard-steps");
    (data.setup_steps || []).forEach(function (step) {
      list.appendChild(el("li", "", step));
    });
    body.appendChild(list);
    var row = el("div", "page-toolbar");
    // 第一步：注入签名盐（走设置页，DPAPI 加密，掩码输入）
    var toSettings = el("button", "pg-btn primary", t("yjb.wizard.toSettings"));
    toSettings.type = "button";
    toSettings.addEventListener("click", function () {
      window.__yjbFocusSecret = true; // 设置页渲染后自动滚动到「养基宝连接」并聚焦密码框
      window.location.hash = "#/settings";
    });
    row.appendChild(toSettings);
    // 第二步：扫码授权（签名盐已配置才可用，避免点了必然失败）
    var toScan = el("button", "pg-btn", t("yjb.wizard.toScan"));
    toScan.type = "button";
    if (!data.secret_configured) {
      toScan.disabled = true;
      toScan.title = t("yjb.wizard.needSecretFirst");
    }
    toScan.addEventListener("click", function () {
      var target = document.getElementById("Y-03");
      if (target && target.scrollIntoView) {
        target.scrollIntoView({ block: "start" });
      }
      var qr = document.querySelector("#y03-body .pg-btn");
      if (qr && !qr.disabled) {
        qr.click();
      }
    });
    row.appendChild(toScan);
    row.appendChild(el("span", "pg-spacer"));
    if (data.data_dir) {
      row.appendChild(el("span", "pg-card-sub", t("yjb.wizard.dataDir") + data.data_dir));
    }
    body.appendChild(row);
    body.setAttribute("data-state", "empty");
  }

  /** 渲染养基宝页。 */
  function renderYjb() {
    var container = document.getElementById(VIEW_IDS.yjb);
    container.innerHTML = "";
    var grid = el("div", "pg-grid");
    // Y-00 首启向导：仅当凭据未齐（缺 token 或缺签名盐）时显示，默认隐藏
    var g0 = buildCard("Y-00", t("yjb.wizard.cardTitle"), "g-12", t("yjb.wizard.cardSub"));
    g0.body.id = "y00-body";
    g0.card.hidden = true;
    grid.appendChild(g0.card);
    var g1 = buildCard("Y-01", t("yjb.consentTitle"), "g-4", t("yjb.consentSub"));
    g1.body.id = "y01-body";
    var g2 = buildCard("Y-02", t("yjb.statusTitle"), "g-4", t("yjb.statusSub"));
    g2.body.id = "y02-body";
    var g3 = buildCard("Y-03", t("yjb.bindTitle"), "g-4", t("yjb.bindSub"));
    g3.body.id = "y03-body";
    var g4 = buildCard("Y-04", t("yjb.estimateTitle"), "g-12", t("yjb.estimateSub"));
    g4.body.id = "y04-body";
    // Y-05 / Y-06：养基宝账户概览与持仓估值（数据来自 account_collect / fund_hold）
    var g5 = buildCard("Y-05", t("yjb.collectTitle"), "g-12", t("yjb.collectSub"));
    g5.body.id = "y05-body";
    var g6 = buildCard("Y-06", t("yjb.holdTitle"), "g-12", t("yjb.holdSub"));
    g6.body.id = "y06-body";
    [g1, g2, g3, g4, g5, g6].forEach(function (item) { grid.appendChild(item.card); });
    container.appendChild(grid);
    var body = document.getElementById("y04-body");
    body.innerHTML = "";
    var toolbar = el("div", "page-toolbar");
    toolbar.appendChild(el("span", "pg-card-sub", t("fund.codeLabel")));
    var input = document.createElement("input");
    input.id = "yjb-code";
    input.value = "017811";
    input.style.cssText = "width:110px;background:var(--bg-panel);border:1px solid var(--border);color:var(--text);border-radius:5px;padding:4px 8px;font-family:var(--font-mono)";
    toolbar.appendChild(input);
    var query = el("button", "pg-btn", t("yjb.queryBtn"));
    query.type = "button";
    query.addEventListener("click", loadYjbEstimate);
    toolbar.appendChild(query);
    toolbar.appendChild(el("span", "pg-spacer"));
    toolbar.appendChild(el("span", "badge-src state-degraded", t("yjb.unofficial")));
    body.appendChild(toolbar);
    body.appendChild(el("div", "pg-card-sub", t("yjb.disclaimer")));
    var result = el("div", "");
    result.id = "yjb-result";
    body.appendChild(result);
    // 该区块是本地表单，不发起加载请求，显式收口为默认态（四态巡检要求）
    body.setAttribute("data-state", "default");
    loadYjbStatus();
    loadYjbCollect();
    loadYjbHoldings(yjbAccount);
  }

  /** 加载连接器状态与告知书。 */
  function loadYjbStatus() {
    api("/api/fund/yjb/status", {})
      .then(function (data) {
        // 首启向导先出：凭据未齐时用户第一眼就能看到"该做什么"，而不是空白区块
        paintYjbWizard(data);
        if (document.getElementById("y01-body")) {
          paintYjbConsent(data);
        }
        if (document.getElementById("y02-body")) {
          paintYjbStatus(data);
        }
        if (document.getElementById("y03-body")) {
          paintYjbBind(data);
        }
      })
      .catch(function (error) {
        var target = document.getElementById("y02-body") || document.getElementById("set-yjb-body");
        if (target) {
          setError(target, error.message, loadYjbStatus);
        }
      });
  }

  /** 告知书 + 确认按钮。
   * @param {Object} data 数据主体（接口 data 段）
   */
  function paintYjbConsent(data) {
    var body = document.getElementById("y01-body");
    body.innerHTML = "";
    var accepted = (data.consent || {}).accepted === true;
    var text = el("div", "pg-card-sub", data.consent_text || "");
    text.style.whiteSpace = "pre-wrap";
    text.style.maxHeight = "220px";
    text.style.overflow = "auto";
    body.appendChild(text);
    var row = el("div", "page-toolbar");
    var tip = el("span", "chip " + (accepted ? "" : "down"), accepted ? t("yjb.accepted") : t("yjb.unconfirmed"));
    row.appendChild(tip);
    if (!accepted) {
      var agree = el("button", "pg-btn primary", t("yjb.agree"));
      agree.type = "button";
      agree.addEventListener("click", function () {
        apiSend("POST", "/api/fund/yjb/consent", { accepted: true }).then(function () {
          toast(t("yjb.consentRecorded"));
          loadYjbStatus();
        });
      });
      row.appendChild(agree);
    }
    body.appendChild(row);
    body.setAttribute("data-state", "default");
  }

  /** 状态 + 增强开关 + 解绑。
   * @param {Object} data 数据主体（接口 data 段）
   */
  function paintYjbStatus(data) {
    var body = document.getElementById("y02-body");
    body.innerHTML = "";
    var grid = el("div", "pg-metric-grid");
    [[t("yjb.kv.binding"), data.bound ? t("yjb.bound") : t("yjb.unbound")], [t("yjb.kv.enhanced"), data.enabled ? t("yjb.enabled") : t("yjb.disabled")],
     [t("yjb.kv.secret"), data.secret_configured ? t("yjb.secretConfigured") : t("yjb.secretUnconfigured")],
     [t("yjb.kv.security"), data.secure_storage ? t("yjb.secure.dpapi") : t("yjb.unavailable")]].forEach(function (pair) {
      var cell = el("div", "pg-metric-cell");
      cell.appendChild(el("div", "pg-metric-label", pair[0]));
      cell.appendChild(el("div", "pg-metric-value", pair[1]));
      grid.appendChild(cell);
    });
    body.appendChild(grid);
    var row = el("div", "page-toolbar");
    var toggle = el("button", "pg-btn", data.enabled ? t("yjb.toggle.offBtn") : t("yjb.toggle.onBtn"));
    toggle.type = "button";
    toggle.addEventListener("click", function () {
      apiSend("POST", "/api/fund/yjb/toggle", { enabled: !data.enabled })
        .then(function () { toast(data.enabled ? t("yjb.toggle.onToast") : t("yjb.toggle.offToast")); loadYjbStatus(); })
        .catch(function (error) { toast(error.message); });
    });
    row.appendChild(toggle);
    if (data.bound) {
      var unbind = el("button", "pg-btn", t("yjb.unbind"));
      unbind.type = "button";
      unbind.addEventListener("click", function () {
        // RT-01 整改：解绑不可逆，必须先二次确认弹窗，再带 confirm=true 提交
        if (!window.UI) {
          return;
        }
        window.UI.modal({
          title: t("yjb.unbind"),
          message: t("yjb.unbindWarn"),
          confirmText: t("yjb.unbindConfirm"),
          danger: true,
          /** 确认解绑：显式提交 confirm=true（后端无此标识一律拒绝执行）。
           * @returns {void} 无返回值
           */
          onConfirm: function () {
            apiSend("POST", "/api/fund/yjb/unbind", { confirm: true }).then(function () {
              toast(t("yjb.unbindToast")); loadYjbStatus();
            }).catch(function (error) { toast(error.message); });
          }
        });
      });
      row.appendChild(unbind);
    }
    body.appendChild(row);
    body.appendChild(el("div", "pg-card-sub", data.notice || ""));
    body.setAttribute("data-state", "default");
  }

  /** 签名盐配置区（R1）：仅在未配置或用户点「更新密钥」时展开。
   * 密码框输入，提交后走 POST /api/fund/yjb/secret（后端 DPAPI 加密，不回显）。
   * RT-01 整改：已配置时追加「清除签名盐」危险按钮（二次确认 + confirm=true）。
   * @param {HTMLElement} host 容器节点
   * @param {boolean} [secretConfigured] 当前是否已配置签名盐（决定是否显示清除按钮）
   * @param {Function} [onCleared] 清除成功后的回调（默认刷新养基宝页状态）
   * @param {Function} [onSaved] 保存成功后的回调（默认刷新养基宝页状态）
   * @returns {void} 无返回值
   */
  function buildYjbSecretForm(host, secretConfigured, onCleared, onSaved) {
    host.innerHTML = "";
    var row = el("div", "page-toolbar");
    var newInput = document.createElement("input");
    newInput.type = "password";
    newInput.placeholder = t("yjb.secret.newPlaceholder");
    newInput.autocomplete = "off";
    newInput.style.cssText = "flex:1;min-width:120px;background:var(--bg-panel);" +
      "border:1px solid var(--border);color:var(--text);border-radius:5px;padding:4px 8px";
    var oldInput = document.createElement("input");
    oldInput.type = "password";
    oldInput.placeholder = t("yjb.secret.oldPlaceholder");
    oldInput.autocomplete = "off";
    oldInput.style.cssText = newInput.style.cssText;
    row.appendChild(newInput);
    row.appendChild(oldInput);
    var save = el("button", "pg-btn primary", t("yjb.secret.save"));
    save.type = "button";
    save.addEventListener("click", function () {
      var payload = { new: newInput.value, old: oldInput.value };
      if (!payload.new && !payload.old) {
        toast(t("yjb.secret.empty"));
        return;
      }
      save.disabled = true;
      apiSend("POST", "/api/fund/yjb/secret", payload).then(function () {
        // 立即清空输入框，避免明文停留在 DOM
        newInput.value = "";
        oldInput.value = "";
        toast(t("yjb.secret.saved"));
        if (typeof onSaved === "function") {
          onSaved();
        } else {
          loadYjbStatus();
        }
      }).catch(function (error) {
        save.disabled = false;
        toast(t("yjb.secret.saveFailed") + (error.message || ""));
      });
    });
    row.appendChild(save);
    host.appendChild(row);
    // RT-01 整改：清除签名盐属不可逆操作，独立危险按钮 + 二次确认弹窗；
    // 仅当已配置时才显示（未配置时无需清除）
    if (secretConfigured) {
      var clearRow = el("div", "page-toolbar");
      var clearBtn = el("button", "pg-btn danger", t("yjb.secret.clear"));
      clearBtn.type = "button";
      clearBtn.addEventListener("click", function () {
        if (!window.UI) {
          return;
        }
        window.UI.modal({
          title: t("yjb.secret.clear"),
          message: t("yjb.secret.clearWarn"),
          confirmText: t("yjb.secret.clear"),
          danger: true,
          /** 确认清除：仅提交 confirm=true（不带 new/old，后端据此判定清除意图）。
           * @returns {void} 无返回值
           */
          onConfirm: function () {
            apiSend("POST", "/api/fund/yjb/secret", { confirm: true }).then(function () {
              toast(t("yjb.secret.clearToast"));
              if (typeof onCleared === "function") { onCleared(); } else { loadYjbStatus(); }
            }).catch(function (error) { toast(t("yjb.secret.clearFailed") + (error.message || "")); });
          }
        });
      });
      clearRow.appendChild(clearBtn);
      host.appendChild(clearRow);
    }
    host.appendChild(el("div", "pg-card-sub", t("yjb.secret.note")));
  }

  /** 二维码与轮询。
   * @param {Object} data 数据主体（接口 data 段）
   */
  function paintYjbBind(data) {
    var body = document.getElementById("y03-body");
    body.innerHTML = "";
    if (!data.secret_configured) {
      var hint = el("div", "pg-state");
      hint.appendChild(el("div", "pg-state-title", t("yjb.qrUnavailable")));
      hint.appendChild(el("div", "pg-state-hint", t("yjb.qrHint")));
      body.appendChild(hint);
      // R1：提供加密输入框（DPAPI 落盘），未提供真实盐前仍保持「待配置」态
      var formHost = el("div", "");
      buildYjbSecretForm(formHost, false);
      body.appendChild(formHost);
      body.setAttribute("data-state", "empty");
      return;
    }
    var button = el("button", "pg-btn", t("yjb.qrButton"));
    button.type = "button";
    var box = el("div", "");
    button.addEventListener("click", function () {
      api("/api/fund/yjb/qrcode", {}).then(function (session) {
        box.innerHTML = "";
        if (session.image_base64) {
          var img = document.createElement("img");
          img.src = "data:image/png;base64," + session.image_base64;
          img.style.cssText = "width:200px;height:200px;background:#fff;border-radius:8px";
          box.appendChild(img);
        }
        var state = el("div", "pg-card-sub", t("yjb.qrWaiting"));
        state.id = "yjb-poll-state";
        box.appendChild(state);
        // R9：轮询次数与间隔由后端 expires_in 推导，不再硬编码 60 次
        var pollMs = 2000;
        var ttlSec = Number(session.expires_in) > 0 ? Number(session.expires_in) : 120;
        var maxAttempts = Math.max(1, Math.ceil((ttlSec * 1000) / pollMs));
        var attempts = 0;
        window.clearInterval(yjbTimer);
        yjbTimer = window.setInterval(function () {
          attempts += 1;
          if (attempts > maxAttempts) {
            window.clearInterval(yjbTimer);
            state.textContent = t("yjb.qrTimeout");
            return;
          }
          api("/api/fund/yjb/poll", { session_id: session.session_id }).then(function (status) {
            state.textContent = status.message || status.state;
            if (status.state === "success") {
              window.clearInterval(yjbTimer);
              apiSend("POST", "/api/fund/yjb/bind", { session_id: session.session_id })
                .then(function () { toast(t("yjb.bindSuccess")); loadYjbStatus(); })
                .catch(function (error) { state.textContent = t("yjb.bindFailed") + error.message; });
            } else if (status.state === "expired" || status.state === "timeout" || status.state === "error") {
              window.clearInterval(yjbTimer);
            }
          });
        }, pollMs);
      }).catch(function (error) {
        box.textContent = error.message;
      });
    });
    body.appendChild(button);
    body.appendChild(box);
    // 已配置时提供「更新密钥」入口：社区逆向盐随 App 版本漂移，需可随时替换
    var update = el("button", "pg-btn", t("yjb.secret.update"));
    update.type = "button";
    var updateHost = el("div", "");
    update.addEventListener("click", function () {
      if (updateHost.childNodes.length) {
        updateHost.innerHTML = "";
        return;
      }
      // 已配置态：显示清除按钮；清除成功后整体刷新回到「待配置」引导
      buildYjbSecretForm(updateHost, true, function () { loadYjbStatus(); });
    });
    body.appendChild(update);
    body.appendChild(updateHost);
    body.setAttribute("data-state", "default");
  }

  /** Y-05：养基宝账户概览（总资产 / 今日估算收益 / 各账户明细）。
   * @returns {void} 无返回值（异步渲染）
   */
  function loadYjbCollect() {
    var body = document.getElementById("y05-body");
    if (!body) {
      return;
    }
    setLoading(body);
    api("/api/fund/yjb/collect", { estimates: yjbPrefs.estimates ? 1 : 0 }).then(function (data) {
      yjbCollectCache = data;
      paintYjbCollect(body, data);
    }).catch(function (error) {
      setError(body, error.message || t("state.errorHint"), function () { loadYjbCollect(); });
    });
  }

  /** 渲染养基宝账户概览（全部账户合并 或 单账户视图）。
   *
   * R6-11 / F6-12：头部 chips 与「持仓与估值」卡共用 `yjbAccount`；
   * 单账户视图下四张指标卡改用该账户数值，账户小卡高亮当前项。
   *
   * @param {HTMLElement} body 内容容器
   * @param {Object} data `/api/fund/yjb/collect` 的 data 段
   * @returns {void} 无返回值
   */
  function paintYjbCollect(body, data) {
    var card = document.getElementById("Y-05");
    setBadges(card, data.source, data.caliber);
    var accounts = data.accounts || [];
    if (!data.total_assets && !accounts.length) {
      setEmpty(body, t("yjb.notBoundHint"), function () { loadYjbCollect(); });
      return;
    }
    body.innerHTML = "";
    var bar = el("div", "page-toolbar");
    bar.appendChild(yjbEstimateBadge());
    bar.appendChild(el("span", "pg-spacer"));
    // 账户维度 chips：全部 + 各账户（与持仓卡同款，数量来自 accounts）
    if (accounts.length > 1) {
      var chips = el("div", "pg-tabs");
      var options = [{ id: "all", title: t("yjb.hold.all"), n: accounts.length }];
      accounts.forEach(function (acc) {
        options.push({ id: acc.id, title: acc.title || acc.id, n: null });
      });
      options.forEach(function (opt) {
        var current = yjbAccount === opt.id || (yjbAccount === "all" && opt.id === "all");
        var btn = el("button", "pg-tab" + (current ? " active" : ""),
          opt.title + (opt.id === "all" ? "（" + accounts.length + "）" : ""));
        btn.type = "button";
        btn.addEventListener("click", function () {
          yjbAccount = opt.id;
          // 概览用本地缓存重绘（养基宝接口 5~9s，切换不该再等一次）；
          // 持仓卡按账户重新拉取（后端按 account_id 过滤）。
          if (yjbCollectCache) {
            paintYjbCollect(body, yjbCollectCache);
          }
          loadYjbHoldings(yjbAccount);
        });
        chips.appendChild(btn);
      });
      bar.appendChild(chips);
    }
    bar.appendChild(timeWithBj(t("yjb.asOfBJ") + (data.as_of || t("value.none")), "", "pg-card-sub"));
    body.appendChild(bar);

    var active = null;
    if (yjbAccount !== "all") {
      active = accounts.filter(function (acc) { return acc.id === yjbAccount; })[0] || null;
    }
    var kpis = el("div", "pg-kpis");
    var items;
    if (active) {
      // 单账户视图：账户资产（成本+累计收益，后端已下发 assets）+ 今日收益/收益率 + 累计收益 + 涨跌家数
      var assets = active.assets;
      if (assets === null || assets === undefined) {
        assets = (Number(active.hold_cost) || 0) + (Number(active.hold_income) || 0);
      }
      items = [
        [t("yjb.acc.assets"), fmt(assets, 2), "flat",
          t("yjb.kpi.yuan") + " ｜ " + t("yjb.kpi.approxWan") + fmt(assets / 10000, 2) + t("yjb.kpi.wanUnit")],
        [t("yjb.kpi.todayIncome"),
          active.today_income === null || active.today_income === undefined
            ? t("value.none")
            : (active.today_income > 0 ? "+" : "") + fmt(active.today_income, 2),
          active.today_income === null || active.today_income === undefined ? "flat" : pctClass(active.today_income),
          yjbPrefs.estimates ? t("yjb.kpi.estimateTag") : t("yjb.kpi.estimateOffTag")],
        [t("yjb.kpi.todayRate"), fmtPct(active.today_income_rate), pctClass(active.today_income_rate), ""],
        [t("yjb.kpi.holdIncome"), (active.hold_income > 0 ? "+" : "") + fmt(active.hold_income, 2),
          pctClass(active.hold_income), t("yjb.kpi.yuan")],
        [t("yjb.kpi.upDown"), active.up + " / " + active.down, "flat", ""]
      ];
    } else {
      // 全部账户：总资产（元为主体，附「≈万元」便于快速读数）
      var totalAssets = data.total_assets;
      var wanText = (totalAssets === null || totalAssets === undefined)
        ? "" : t("yjb.kpi.approxWan") + fmt(totalAssets / 10000, 2) + t("yjb.kpi.wanUnit");
      items = [
        [t("yjb.kpi.totalAssets"), fmt(totalAssets, 2), "flat", t("yjb.kpi.yuan") + (wanText ? " ｜ " + wanText : "")],
        [t("yjb.kpi.todayIncome"),
          data.today_income === null || data.today_income === undefined
            ? t("value.none")
            : (data.today_income > 0 ? "+" : "") + fmt(data.today_income, 2),
          data.today_income === null || data.today_income === undefined ? "flat" : pctClass(data.today_income),
          yjbPrefs.estimates ? t("yjb.kpi.estimateTag") : t("yjb.kpi.estimateOffTag")],
        [t("yjb.kpi.todayRate"), fmtPct(data.today_income_rate), pctClass(data.today_income_rate), ""],
        [t("yjb.kpi.holdIncome"), (data.hold_income > 0 ? "+" : "") + fmt(data.hold_income, 2),
          pctClass(data.hold_income), t("yjb.kpi.yuan")],
        [t("yjb.kpi.accounts"), String(accounts.length), "flat", t("yjb.kpi.countTag")]
      ];
    }
    items.forEach(function (row) {
      var box = el("div", "pg-kpi");
      box.appendChild(el("div", "pg-kpi-k", row[0]));
      box.appendChild(el("div", "pg-kpi-v " + row[2], row[1]));
      if (row[3]) {
        box.appendChild(el("div", "pg-kpi-x", row[3]));
      }
      kpis.appendChild(box);
    });
    body.appendChild(kpis);

    if (accounts.length) {
      var list = el("div", "pg-kpi-accounts");
      accounts.forEach(function (acc) {
        var box = el("div", "pg-kpi-acc" + (active && active.id === acc.id ? " active" : ""));
        var head = el("div", "pg-kpi-acc-head");
        head.appendChild(el("span", "pg-kpi-acc-name", acc.title || acc.id));
        head.appendChild(el("span", "pg-kpi-acc-id", acc.id));
        box.appendChild(head);
        var accAssets = acc.assets;
        if (accAssets === null || accAssets === undefined) {
          accAssets = (Number(acc.hold_cost) || 0) + (Number(acc.hold_income) || 0);
        }
        var rows = [
          [t("yjb.acc.assets"), fmt(accAssets, 2), "flat"],
          [t("yjb.kpi.todayIncome"), (acc.today_income > 0 ? "+" : "") + fmt(acc.today_income, 2), pctClass(acc.today_income)],
          [t("yjb.kpi.todayRate"), fmtPct(acc.today_income_rate), pctClass(acc.today_income_rate)],
          [t("yjb.kpi.holdIncome"), (acc.hold_income > 0 ? "+" : "") + fmt(acc.hold_income, 2), pctClass(acc.hold_income)],
          [t("yjb.kpi.upDown"), acc.up + " / " + acc.down, "flat"]
        ];
        rows.forEach(function (r) {
          var line = el("div", "pg-kpi-acc-row");
          line.appendChild(el("span", "", r[0]));
          line.appendChild(el("b", r[2], r[1]));
          box.appendChild(line);
        });
        // 点账户小卡等于切 chips（同一 state，单一实现）
        box.addEventListener("click", function () {
          yjbAccount = acc.id;
          if (yjbCollectCache) {
            paintYjbCollect(body, yjbCollectCache);
          }
          loadYjbHoldings(yjbAccount);
        });
        list.appendChild(box);
      });
      body.appendChild(list);
    }
    body.appendChild(el("div", "pg-card-sub", t("yjb.collectNote")));
    body.setAttribute("data-state", "default");
  }

  /** Y-06：养基宝持仓与估值（支持账户 Tab 切换）。
   * @param {string} accountId 账户 id；"all" 表示全部账户
   * @returns {void} 无返回值（异步渲染）
   */
  function loadYjbHoldings(accountId) {
    var body = document.getElementById("y06-body");
    if (!body) {
      return;
    }
    setLoading(body);
    api("/api/fund/yjb/holdings", {
      account_id: accountId || "all",
      estimates: yjbPrefs.estimates ? 1 : 0
    }).then(function (data) {
      var card = document.getElementById("Y-06");
      setBadges(card, data.source, data.caliber);
      var items = data.items || [];
      body.innerHTML = "";
      var bar = el("div", "page-toolbar");
      bar.appendChild(yjbEstimateBadge());
      var tabs = [{ id: "all", title: t("yjb.hold.all"), n: data.total }];
      (data.accounts || []).forEach(function (acc) {
        var n = items.filter(function (x) { return x.account_id === acc.id; }).length;
        tabs.push({ id: acc.id, title: acc.title || acc.id, n: n });
      });
      var current = data.account_id || "all";
      tabs.forEach(function (tab) {
        var btn = el("button", "pg-btn" + (tab.id === current ? " primary" : ""),
          tab.title + (current === "all" ? "（" + tab.n + "）" : ""));
        btn.type = "button";
        btn.addEventListener("click", function () { loadYjbHoldings(tab.id); });
        bar.appendChild(btn);
      });
      bar.appendChild(el("span", "pg-spacer"));
      bar.appendChild(el("span", "badge-src state-degraded", t("yjb.unofficial")));
      body.appendChild(bar);

      if (!items.length) {
        setEmpty(body, t("yjb.hold.empty"), function () { loadYjbHoldings(current); });
        return;
      }
      // 表格必须渲染到子容器：renderTable 会清空目标容器 innerHTML，
      // 若直接传 body，上面的工具栏（账户 Tab / 角标 / 一键关闭）会被一并清掉。
      var tableHost = el("div", "");
      body.appendChild(tableHost);
      renderTable(tableHost, [
        {
          title: t("yjb.hold.col.fund"), cls: "l",
          /** 基金名称列：代码 + 简称。
           * @param {Object} row 数据行
           * @returns {HTMLElement} 单元格节点
           */
          render: function (row) {
            var box = el("span", "pg-sym");
            box.appendChild(el("b", "", row.code));
            box.appendChild(document.createTextNode(" " + (row.name || "")));
            return box;
          }
        },
        {
          title: yjbPrefs.estimates ? t("yjb.hold.col.pct") : t("yjb.hold.col.pctT1"),
          num: true,
          render: function (row) {
            // 关闭估算：估算涨跌幅一律留空（不用 T+1 净值涨幅冒充盘中估算）
            if (row.pct === null || row.pct === undefined) {
              return el("span", "flat", t("value.none"));
            }
            return el("span", pctClass(row.pct), fmtPct(row.pct));
          }
        },
        { title: t("yjb.hold.col.nav"), num: true, render: function (row) { return row.nav === null ? t("value.none") : fmt(row.nav, 4); } },
        { title: t("yjb.hold.col.share"), num: true, render: function (row) { return row.share === null ? t("value.none") : fmt(row.share, 2); } },
        { title: t("yjb.hold.col.cost"), num: true, render: function (row) { return row.cost === null ? t("value.none") : fmt(row.cost, 4); } },
        { title: t("yjb.hold.col.money"), num: true, render: function (row) { return row.money === null ? t("value.none") : fmt(row.money, 2); } },
        { title: t("yjb.hold.col.earn"), num: true, render: function (row) { return row.hold_earn === null ? t("value.none") : el("span", pctClass(row.hold_earn), fmt(row.hold_earn, 2)); } },
        { title: t("yjb.hold.col.account"), render: function (row) { return el("span", "pg-card-sub", row.account_title || row.account_id); } },
        { title: t("yjb.hold.col.asOf"), render: function (row) { return el("span", "pg-card-sub", row.as_of || t("value.none")); } }
      ], items, function (row) {
        window.location.hash = "#/fund?code=" + row.code;
      });
      if (!yjbPrefs.estimates) {
        // 关闭估算后明确告知口径回退，避免用户误以为"数据丢了"
        // 注意：必须放在 renderTable 之后（renderTable 会清空容器 innerHTML）
        body.appendChild(el("div", "pg-card-sub", t("yjb.hold.t1Hint")));
      }
      body.setAttribute("data-state", "default");
    }).catch(function (error) {
      setError(body, error.message || t("state.errorHint"), function () { loadYjbHoldings(accountId); });
    });
  }

  /** 查询估值（显示命中源与口径）。 */
  function loadYjbEstimate() {
    var code = (document.getElementById("yjb-code").value || "").trim();
    var box = document.getElementById("yjb-result");
    setLoading(box);
    api("/api/fund/yjb/realtime", { code: code })
      .then(function (data) {
        box.innerHTML = "";
        var grid = el("div", "pg-metric-grid");
        [[t("fund.estimateTitle"), fmt(data.nav, 4)], [t("fund.estimateGrowth"), fmtPct(data.day_growth)],
         [t("fund.estimateSource"), data.source || t("value.none")], [t("fund.estimateCoverage"), data.coverage ? fmt(data.coverage, 2) + "%" : t("value.none")]]
          .forEach(function (pair) {
            var cell = el("div", "pg-metric-cell");
            cell.appendChild(el("div", "pg-metric-label", pair[0]));
            cell.appendChild(el("div", "pg-metric-value", pair[1]));
            grid.appendChild(cell);
          });
        box.appendChild(grid);
        box.appendChild(el("div", "pg-card-sub", data.caliber || ""));
        box.setAttribute("data-state", "default");
      })
      .catch(function (error) { setError(box, error.message, loadYjbEstimate); });
  }

  // =============================================================
  // 十二之二、#/charting 图表分析（阶段 8 补齐，OB-V02/F006-F010）
  // =============================================================
  /** 计算 N 日简单移动平均（数据不足处补 null，前端本地计算不造假）。
   * @param {Array} values 数值列表
   * @param {*} window 时间窗口（如 30 日）
   * @returns {*} 处理结果
   */
  function movingAverage(values, window) {
    var out = [];
    for (var i = 0; i < values.length; i += 1) {
      if (i + 1 < window) {
        out.push(null);
        continue;
      }
      var sum = 0;
      for (var j = i + 1 - window; j <= i; j += 1) {
        sum += values[j];
      }
      out.push(Number((sum / window).toFixed(4)));
    }
    return out;
  }

  /** 计算指数移动平均序列（MACD 基础）。
   * @param {Array} values 数值列表
   * @param {*} window 时间窗口（如 30 日）
   * @returns {*} 处理结果
   */
  function exponentialMovingAverage(values, window) {
    var out = [];
    var k = 2 / (window + 1);
    var prev = values[0];
    for (var i = 0; i < values.length; i += 1) {
      if (i === 0) {
        out.push(values[0]);
        continue;
      }
      prev = values[i] * k + prev * (1 - k);
      out.push(Number(prev.toFixed(4)));
    }
    return out;
  }

  /** 计算 MACD（12,26,9）：返回 {dif, dea, macd} 三组同长序列。
   * @param {*} closes 收盘价序列
   * @returns {*} 处理结果
   */
  function computeMACD(closes) {
    var ema12 = exponentialMovingAverage(closes, 12);
    var ema26 = exponentialMovingAverage(closes, 26);
    var dif = ema12.map(function (value, index) {
      return Number((value - ema26[index]).toFixed(4));
    });
    var dea = exponentialMovingAverage(dif, 9);
    var macd = dif.map(function (value, index) {
      return Number(((value - dea[index]) * 2).toFixed(4));
    });
    return { dif: dif, dea: dea, macd: macd };
  }

  /** 计算 KDJ（9,3,3）：RSV→K→D→J，数据不足处补 null。
   * @param {Array} items 记录列表
   * @returns {Object} 处理结果
   */
  function computeKDJ(items) {
    var k = [];
    var d = [];
    var j = [];
    var prevK = 50;
    var prevD = 50;
    for (var i = 0; i < items.length; i += 1) {
      var start = Math.max(0, i - 8);
      var lows = [];
      var highs = [];
      for (var m = start; m <= i; m += 1) {
        lows.push(items[m].low);
        highs.push(items[m].high);
      }
      var llv = Math.min.apply(null, lows);
      var hhv = Math.max.apply(null, highs);
      var rsv = hhv > llv ? ((items[i].close - llv) / (hhv - llv)) * 100 : 50;
      var kValue = (2 / 3) * prevK + (1 / 3) * rsv;
      var dValue = (2 / 3) * prevD + (1 / 3) * kValue;
      prevK = kValue;
      prevD = dValue;
      k.push(Number(kValue.toFixed(2)));
      d.push(Number(dValue.toFixed(2)));
      j.push(Number((3 * kValue - 2 * dValue).toFixed(2)));
    }
    return { k: k, d: d, j: j };
  }

  /** 构建图表分析页骨架（工具栏 + K线/分时 Tab + 四区块）。
   * @param {HTMLElement} container DOM 容器节点
   */
  function buildChartingShell(container) {
    container.innerHTML = "";
    var toolbar = el("div", "page-toolbar");
    // 市场切换：A股/港股/美股（与个股页一致的口径）
    var market = document.createElement("select");
    market.id = "ch-market";
    [["a", t("chart.market.a")], ["hk", t("chart.market.hk")], ["us", t("chart.market.us")]].forEach(function (item) {
      var option = document.createElement("option");
      option.value = item[0];
      option.textContent = item[1];
      market.appendChild(option);
    });
    toolbar.appendChild(market);
    toolbar.appendChild(el("span", "pg-toolbar-label", t("chart.label.code")));
    var symbol = document.createElement("input");
    symbol.id = "ch-symbol";
    symbol.placeholder = t("chart.placeholder.symbol");
    symbol.style.cssText = "width:150px;background:var(--bg-panel);border:1px solid var(--border);"
      + "color:var(--text);border-radius:5px;padding:4px 8px";
    toolbar.appendChild(symbol);
    // 周期：日k/周k/月k（后端 Query 口径）
    var period = document.createElement("select");
    period.id = "ch-period";
    [["日k", t("chart.period.day")], ["周k", t("chart.period.week")], ["月k", t("chart.period.month")]].forEach(function (item) {
      var option = document.createElement("option");
      option.value = item[0];
      option.textContent = item[1];
      period.appendChild(option);
    });
    toolbar.appendChild(period);
    // 复权：前复权/后复权/不复权
    var adjust = document.createElement("select");
    adjust.id = "ch-adjust";
    [["qfq", t("chart.adjust.qfq")], ["hfq", t("chart.adjust.hfq")], ["none", t("chart.adjust.none")]].forEach(function (item) {
      var option = document.createElement("option");
      option.value = item[0];
      option.textContent = item[1];
      adjust.appendChild(option);
    });
    toolbar.appendChild(adjust);
    var query = el("button", "pg-btn primary", t("chart.action.query"));
    query.type = "button";
    query.id = "ch-query";
    toolbar.appendChild(query);
    toolbar.appendChild(el("span", "pg-spacer"));
    // 画线开关：点击后在主图添加水平参考线（本地标注，不写回数据）
    var draw = el("button", "pg-btn", t("chart.draw.off"));
    draw.type = "button";
    draw.id = "ch-draw";
    toolbar.appendChild(draw);
    var clearDraw = el("button", "pg-btn", t("chart.action.clearDraw"));
    clearDraw.type = "button";
    clearDraw.id = "ch-draw-clear";
    toolbar.appendChild(clearDraw);
    container.appendChild(toolbar);

    // K线/分时 Tab
    var tabs = el("div", "pg-tabs");
    tabs.id = "ch-tabs";
    [["kline", t("chart.tab.kline")], ["minute", t("chart.tab.minute")]].forEach(function (item) {
      var button = el("button", "pg-tab", item[1]);
      button.type = "button";
      button.setAttribute("data-tab", item[0]);
      tabs.appendChild(button);
    });
    container.appendChild(tabs);

    var grid = el("div", "pg-grid");
    var quoteCard = buildCard("CH-01", t("chart.card.quote"), "g-12", t("chart.card.quoteSub"));
    quoteCard.body.id = "ch01-body";
    grid.appendChild(quoteCard.card);
    var chartCard = buildCard("CH-02", t("chart.card.chart"), "g-12", t("chart.card.chartSub"));
    chartCard.body.id = "ch02-body";
    grid.appendChild(chartCard.card);
    container.appendChild(grid);

    bindChartingControls();
  }

  /** 绑定图表页控件事件（查询/Tab/画线）。 */
  function bindChartingControls() {
    var query = document.getElementById("ch-query");
    if (query) {
      query.addEventListener("click", function () {
        state.charting.symbol = (document.getElementById("ch-symbol").value || "").trim();
        state.charting.market = document.getElementById("ch-market").value;
        state.charting.period = document.getElementById("ch-period").value;
        state.charting.adjust = document.getElementById("ch-adjust").value;
        // R3-28：同步共享 store，行情页也能带上图表页最近一次标的
        if (window.SymbolStore) {
          window.SymbolStore.set(state.charting.symbol, state.charting.market);
        }
        loadCharting();
      });
    }
    var symbol = document.getElementById("ch-symbol");
    if (symbol) {
      symbol.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
          document.getElementById("ch-query").click();
        }
      });
    }
    document.querySelectorAll("#ch-tabs .pg-tab").forEach(function (button) {
      button.addEventListener("click", function () {
        state.charting.tab = button.getAttribute("data-tab");
        syncChartingTabs();
        paintChartingChart(chartingCache.kline, chartingCache.minute);
      });
    });
    var draw = document.getElementById("ch-draw");
    if (draw) {
      draw.addEventListener("click", function () {
        state.charting.draw = !state.charting.draw;
        draw.textContent = t(state.charting.draw ? "chart.draw.on" : "chart.draw.off");
        toast(t(state.charting.draw ? "chart.draw.opened" : "chart.draw.closed"));
        // R6-05：开关必须重绘一次 —— 点击监听是在 paintChartingChart 内按
        // state.charting.draw 决定是否绑定的，不重绘则监听永远不会挂上（原缺陷：
        // 按钮显示"画线：开"但点主图无任何反应）。
        if (chartingCache && (chartingCache.kline || chartingCache.minute)) {
          paintChartingChart(chartingCache.kline, chartingCache.minute);
        }
      });
    }
    var clear = document.getElementById("ch-draw-clear");
    if (clear) {
      clear.addEventListener("click", function () {
        chartingCache.lines = [];
        paintChartingChart(chartingCache.kline, chartingCache.minute);
        toast(t("chart.draw.cleared"));
      });
    }
  }

  /** 同步图表页 Tab 选中态。 */
  function syncChartingTabs() {
    document.querySelectorAll("#ch-tabs .pg-tab").forEach(function (button) {
      button.classList.toggle("active", button.getAttribute("data-tab") === state.charting.tab);
    });
  }

  var chartingCache = { kline: null, minute: null, quote: null, lines: [] };
  // R3-32：请求令牌 —— 切换标的/周期后旧响应不得覆盖新标的的渲染结果
  var chartingRequestToken = 0;

  /** 渲染图表分析页（入口：同步表单 → 拉数）。
   * @param {Object} params 查询参数对象
   */
  function renderCharting(params) {
    var container = document.getElementById(VIEW_IDS.charting);
    if (params && params.symbol) {
      state.charting.symbol = params.symbol;
    }
    if (params && params.market) {
      state.charting.market = params.market;
    }
    // R3-28/R3-30：若无 URL 参数，自共享 store 带出最近标的
    if ((!params || (!params.symbol && !params.market)) && window.SymbolStore) {
      var shared = window.SymbolStore.get();
      state.charting.symbol = shared.symbol || state.charting.symbol;
      state.charting.market = shared.market || state.charting.market;
    }
    if (!container.querySelector("#ch-tabs")) {
      buildChartingShell(container);
    }
    document.getElementById("ch-symbol").value = state.charting.symbol;
    document.getElementById("ch-market").value = state.charting.market;
    document.getElementById("ch-period").value = state.charting.period;
    document.getElementById("ch-adjust").value = state.charting.adjust;
    // R3-30：订阅共享 store——切页带代码自动同步到本页输入框
    if (window.SymbolStore && window.SymbolStore.subscribe && !container.dataset.symbolStoreSubscribed) {
      container.dataset.symbolStoreSubscribed = "1";
      window.SymbolStore.subscribe(function (symbol, market) {
        var inp = document.getElementById("ch-symbol");
        var sel = document.getElementById("ch-market");
        if (inp) inp.value = symbol;
        if (sel) sel.value = market;
        state.charting.symbol = symbol;
        state.charting.market = market;
      });
    }
    syncChartingTabs();
    loadCharting();
  }

  /** 拉取图表页数据（K线 + 分时 + 报价并行；任一失败进错误态）。 */
  function loadCharting() {
    var symbol = state.charting.symbol;
    var market = state.charting.market;
    var quoteBody = document.getElementById("ch01-body");
    var chartBody = document.getElementById("ch02-body");
    if (!quoteBody || !chartBody) {
      return;
    }
    if (!symbol) {
      setEmpty(quoteBody, t("chart.empty.input"));
      setEmpty(chartBody, t("chart.empty.input"));
      return;
    }
    // R3-32：CH-01（报价）与 CH-02（K线/分时）**解耦**。
    // 此前三请求合成一个 Promise.all，最慢的 K 线未回时，报价区块即使数据早已到手也一直挂骨架，
    // 慢源下表现为「两块 loading 并挂 8-10s」。现改为各自落地：任一源慢只影响自己那张卡，
    // 页面上最多只剩 1 个真正的等待骨架。
    chartingRequestToken += 1;
    var token = chartingRequestToken;
    setLoading(quoteBody);
    setLoading(chartBody);

    api("/api/market/quote", { symbol: symbol, market: market })
      .then(function (result) {
        if (token !== chartingRequestToken) {
          return;  // 标的已切换，丢弃过期响应
        }
        // /api/market/quote 的 data 为 {quote:{...}} 嵌套，先解包再入缓存
        chartingCache.quote = (result && result.quote) || {};
        paintChartingQuote(chartingCache.quote);
      })
      .catch(function (error) {
        if (token !== chartingRequestToken) {
          return;
        }
        setError(quoteBody, error.message, loadCharting);
      });

    // K 线与分时同图共存（Tab 切换用的是同一份缓存），仍需一起到位后才能落图
    Promise.all([
      api("/api/market/kline", { symbol: symbol, market: market, period: state.charting.period, adjust: state.charting.adjust }),
      api("/api/market/minute", { symbol: symbol, market: market, period: "1min" })
    ])
      .then(function (result) {
        if (token !== chartingRequestToken) {
          return;
        }
        chartingCache.kline = result[0];
        chartingCache.minute = result[1];
        chartingCache.lines = [];
        paintChartingChart(result[0], result[1]);
      })
      .catch(function (error) {
        if (token !== chartingRequestToken) {
          return;
        }
        setError(chartBody, error.message, loadCharting);
      });
  }

  /** 渲染报价概览卡（CH-01）：价格/涨跌/最高/最低/成交额 + 双徽章。
   * @param {*} quote 报价数据
   */
  function paintChartingQuote(quote) {
    var body = document.getElementById("ch01-body");
    var card = document.getElementById("CH-01");
    if (!body) {
      return;
    }
    if (!quote) {
      setEmpty(body, t("chart.empty.quote"), loadCharting);
      return;
    }
    setBadges(card, quote.source, quote.caliber, false);
    body.innerHTML = "";
    var main = el("div", "pg-quote-main");
    main.appendChild(el("span", "pg-quote-name", quote.name || state.charting.symbol));
    main.appendChild(el("span", "pg-quote-price " + pctClass(quote.change), fmt(quote.price, 2)));
    main.appendChild(el("span", pctClass(quote.change), fmt(quote.change, 2) + " / " + fmtPct(quote.pct)));
    // TZ-01：同上，图表页与个股页口径一致
    main.appendChild(timeWithBj(quote.quote_time, marketTzTip(state.charting.market), "pg-card-sub"));
    body.appendChild(main);
    var meta = el("div", "pg-quote-meta");
    [
      [t("chart.meta.high"), fmt(quote.high, 2)], [t("chart.meta.low"), fmt(quote.low, 2)],
      [t("chart.meta.open"), fmt(quote.open, 2)], [t("chart.meta.prevClose"), fmt(quote.prev_close, 2)],
      [t("chart.meta.volume"), fmt(quote.volume, 0)], [t("chart.meta.amount"), fmt(quote.amount, 0)]
    ].forEach(function (pair) {
      var cell = el("div", "pg-quote-meta-cell");
      cell.appendChild(el("span", "pg-metric-label", pair[0]));
      cell.appendChild(el("span", "pg-metric-value", pair[1]));
      meta.appendChild(cell);
    });
    body.appendChild(meta);
    body.setAttribute("data-state", "default");
  }

  /** 渲染主图（CH-02）：K线四 pane（K+MA/VOL/MACD/KDJ）或分时（午休断裂）。
   * @param {*} kline K 线数据列表
   * @param {*} minute 分时数据列表
   * @returns {*} 处理结果
   */
  function paintChartingChart(kline, minute) {
    var body = document.getElementById("ch02-body");
    var card = document.getElementById("CH-02");
    if (!body) {
      return;
    }
    if (state.charting.tab === "minute") {
      paintChartingMinute(body, card, minute);
      return;
    }
    var items = (kline && kline.items) || [];
    if (items.length === 0) {
      setEmpty(body, t("chart.empty.kline"), loadCharting);
      return;
    }
    setBadges(card, kline.source, kline.caliber, false);
    // 重绘前释放本卡内的旧图表实例，避免路由内反复重取导致实例累积
    charts.slice().forEach(function (instance) {
      var dom = instance.getDom ? instance.getDom() : null;
      if (dom && body.contains(dom)) {
        instance.dispose();
        charts.splice(charts.indexOf(instance), 1);
      }
    });
    body.innerHTML = "";
    var box = el("div", "pg-chart tall");
    body.appendChild(box);
    var chart = mountChart(box);
    if (!chart) {
      return;
    }
    var theme = chartTheme();
    var dates = items.map(function (item) { return item.date; });
    var closes = items.map(function (item) { return item.close; });
    var ohlc = items.map(function (item) {
      return [item.open, item.close, item.low, item.high];
    });
    // 指标前端计算：MA5/MA10/MA20、MACD(12,26,9)、KDJ(9,3,3)
    var ma5 = movingAverage(closes, 5);
    var ma10 = movingAverage(closes, 10);
    var ma20 = movingAverage(closes, 20);
    var macd = computeMACD(closes);
    var kdj = computeKDJ(items);
    var volumes = items.map(function (item, index) {
      return { value: item.volume, itemStyle: { color: closes[index] >= items[index].open ? COLOR_UP : COLOR_DOWN } };
    });
    // 画线：用户点击主图后记录价格，转成主序列水平参考线
    var markLines = chartingCache.lines.map(function (price) {
      return { yAxis: price };
    });
    // P1-14：K线 option 脚手架委托 window.Utils.buildCandlestickOption
    chart.setOption(window.Utils.buildCandlestickOption(theme, dates, ohlc, volumes, {
      candleName: t("chart.series.candle"),
      legend: { textStyle: { color: theme.text }, top: 0 },
      grids: [
        { left: 56, right: 16, top: 28, height: "42%" },
        { left: 56, right: 16, top: "74%", height: "7%" },
        { left: 56, right: 16, top: "83%", height: "8%" },
        { left: 56, right: 16, top: "93%", height: "6%" }
      ],
      xAxes: [
        { type: "category", data: dates, axisLabel: { color: theme.text }, axisLine: { lineStyle: { color: theme.border } } },
        { type: "category", gridIndex: 1, data: dates, axisLabel: { show: false }, axisLine: { lineStyle: { color: theme.border } } },
        { type: "category", gridIndex: 2, data: dates, axisLabel: { show: false }, axisLine: { lineStyle: { color: theme.border } } },
        { type: "category", gridIndex: 3, data: dates, axisLabel: { show: false }, axisLine: { lineStyle: { color: theme.border } } }
      ],
      yAxes: [
        { scale: true, axisLabel: { color: theme.text }, splitLine: { lineStyle: { color: theme.border, opacity: 0.4 } } },
        { gridIndex: 1, scale: true, axisLabel: { show: false }, splitLine: { show: false } },
        { gridIndex: 2, scale: true, axisLabel: { show: false }, splitLine: { show: false } },
        { gridIndex: 3, scale: true, axisLabel: { show: false }, splitLine: { show: false } }
      ],
      series: [
        {
          name: t("chart.series.candle"), type: "candlestick", data: ohlc,
          itemStyle: { color: COLOR_UP, color0: COLOR_DOWN, borderColor: COLOR_UP, borderColor0: COLOR_DOWN },
          markLine: {
            symbol: "none", silent: true,
            data: markLines,
            lineStyle: { color: "#e8a33d", type: "dashed" }
          }
        },
        { name: "MA5", type: "line", data: ma5, showSymbol: false, smooth: true, lineStyle: { width: 1 }, color: "#e8a33d" },
        { name: "MA10", type: "line", data: ma10, showSymbol: false, smooth: true, lineStyle: { width: 1 }, color: "#4c8df6" },
        { name: "MA20", type: "line", data: ma20, showSymbol: false, smooth: true, lineStyle: { width: 1 }, color: "#b07cf0" },
        { name: t("chart.series.volume"), type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: volumes },
        { name: "MACD", type: "bar", xAxisIndex: 2, yAxisIndex: 2, data: macd.macd.map(function (value) {
            return { value: value, itemStyle: { color: value >= 0 ? COLOR_UP : COLOR_DOWN } };
          }) },
        { name: "DIF", type: "line", xAxisIndex: 2, yAxisIndex: 2, data: macd.dif, showSymbol: false, lineStyle: { width: 1 }, color: "#e8a33d" },
        { name: "DEA", type: "line", xAxisIndex: 2, yAxisIndex: 2, data: macd.dea, showSymbol: false, lineStyle: { width: 1 }, color: "#4c8df6" },
        { name: "K", type: "line", xAxisIndex: 3, yAxisIndex: 3, data: kdj.k, showSymbol: false, lineStyle: { width: 1 }, color: "#e8a33d" },
        { name: "D", type: "line", xAxisIndex: 3, yAxisIndex: 3, data: kdj.d, showSymbol: false, lineStyle: { width: 1 }, color: "#4c8df6" },
        { name: "J", type: "line", xAxisIndex: 3, yAxisIndex: 3, data: kdj.j, showSymbol: false, lineStyle: { width: 1 }, color: "#b07cf0" }
      ]
    }));
    // 画线交互：开启后点击主图，按像素坐标换算价格，添加水平参考线（本地标注不写回数据）
    //
    // R6-05 修复：原先用 `params.event.offsetX` —— ECharts 的 click 回调里 params.event 是
    // zrender 包装事件，**不保证携带 offsetX/offsetY**，取到 undefined 后 convertFromPixel
    // 返回空、整段逻辑静默跳过，表现为"点了没反应"。改挂 zrender 原始事件（offsetX 为标准字段），
    // 并对换算结果做区间校验 + 失败提示，禁止静默失败。
    chart.off("click");
    var zr = chart.getZr ? chart.getZr() : null;
    if (zr) {
      zr.off("click");
      if (state.charting.draw) {
        zr.on("click", function (event) {
          // convertFromPixel 的 finder 必须能定位到一个完整坐标系：只给 {yAxisIndex} 时
          // ECharts 5 拿不到 x 轴，返回 undefined（这正是"点了没反应"的第二层原因）。
          // 优先用 gridIndex:0（主图），失败再退回双轴组合。
          var px = [event.offsetX, event.offsetY];
          var point = chart.convertFromPixel({ gridIndex: 0 }, px);
          if (!point) {
            point = chart.convertFromPixel({ xAxisIndex: 0, yAxisIndex: 0 }, px);
          }
          var price = point && typeof point[1] === "number" ? Number(point[1]) : null;
          if (price === null || !isFinite(price)) {
            toast(t("chart.draw.failed"));
            return;
          }
          if (price <= 0) {
            toast(t("chart.draw.outOfRange"));
            return;
          }
          if (chartingCache.lines.length >= 20) {
            toast(t("chart.draw.limit"));
            return;
          }
          var rounded = Number(price.toFixed(2));
          chartingCache.lines.push(rounded);
          paintChartingChart(chartingCache.kline, chartingCache.minute);
          toast(t("chart.draw.added", { price: rounded }));
        });
      }
    }
    body.setAttribute("data-state", "default");
  }

  /** 渲染分时图（午休断裂 + 均价线 + 昨收参考线）。
   * @param {HTMLElement} body 区块容器（.pg-body，四态 data-state 挂在此节点）
   * @param {HTMLElement} card 卡片 DOM 节点
   * @param {*} minute 分时数据列表
   * @returns {*} 处理结果
   */
  function paintChartingMinute(body, card, minute) {
    var items = (minute && minute.items) || [];
    if (items.length === 0) {
      setEmpty(body, t("chart.empty.minute"), loadCharting);
      return;
    }
    setBadges(card, minute.source, minute.caliber, false);
    // 重绘前释放本卡内的旧图表实例，避免 Tab 反复切换导致实例累积
    charts.slice().forEach(function (instance) {
      var dom = instance.getDom ? instance.getDom() : null;
      if (dom && body.contains(dom)) {
        instance.dispose();
        charts.splice(charts.indexOf(instance), 1);
      }
    });
    body.innerHTML = "";
    var box = el("div", "pg-chart tall");
    body.appendChild(box);
    var chart = mountChart(box);
    if (!chart) {
      return;
    }
    var theme = chartTheme();
    var quote = chartingCache.quote || {};
    // 午休断裂：剔除 11:30–13:00 之间数据并插入可见断点
    var categories = [];
    var prices = [];
    var avg = [];
    var sumPrice = 0;
    var sumVolume = 0;
    items.forEach(function (item) {
      if (item.time > "11:30" && item.time < "13:00") {
        return;
      }
      categories.push(item.time);
      prices.push(item.price);
      sumPrice += item.price * (item.volume || 0);
      sumVolume += item.volume || 0;
      avg.push(sumVolume > 0 ? sumPrice / sumVolume : item.price);
    });
    var noonIndex = categories.findIndex(function (value) {
      return value >= "13:00";
    });
    if (noonIndex > 0) {
      categories.splice(noonIndex, 0, t("chart.series.noon"));
      prices.splice(noonIndex, 0, null);
      avg.splice(noonIndex, 0, null);
    }
    // 昨收参考线：分时涨跌的基准（红涨绿跌）
    var prevClose = quote.prev_close;
    var markLines = prevClose ? [{ yAxis: prevClose }] : [];
    chart.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis" },
      legend: { textStyle: { color: theme.text }, top: 0 },
      grid: { left: 48, right: 16, top: 32, bottom: 24 },
      xAxis: {
        type: "category", data: categories,
        axisLabel: { color: theme.text, interval: Math.max(0, Math.floor(categories.length / 8)) },
        axisLine: { lineStyle: { color: theme.border } }
      },
      yAxis: {
        type: "value", scale: true,
        axisLabel: { color: theme.text },
        splitLine: { lineStyle: { color: theme.border, opacity: 0.4 } }
      },
      series: [
        {
          name: t("chart.series.price"), type: "line", data: prices, showSymbol: false, lineStyle: { width: 1.4 },
          color: COLOR_UP,
          markLine: { symbol: "none", silent: true, data: markLines, lineStyle: { color: "#8a8a8a", type: "dashed" } }
        },
        { name: t("chart.series.avg"), type: "line", data: avg, showSymbol: false, lineStyle: { width: 1, type: "dashed" }, color: "#e8a33d" }
      ]
    });
    body.setAttribute("data-state", "default");
  }

  // =============================================================
  // 十二之三、#/settings 设置（阶段 8 补齐，OB-V11/F069-F072）
  // =============================================================
  /** 构建设置页骨架（AI 配置 / 主题与缓存 / 关于）。
   * @param {HTMLElement} container DOM 容器节点
   */
  function buildSettingsShell(container) {
    container.innerHTML = "";
    var grid = el("div", "pg-grid");

    // —— S-01 AI 设置卡：服务商预设 / base_url / api_key / model + 测试连接 ——
    var aiCard = buildCard("ST-AI", t("set.card.ai"), "g-12", t("set.card.aiSub"));
    aiCard.body.id = "set-ai-body";
    grid.appendChild(aiCard.card);

    // —— S-02 外观卡：主题切换 + 字号档位（CSS 变量缩放，即时生效） ——
    var themeCard = buildCard("ST-TH", t("set.card.theme"), "g-6", t("set.card.themeSub"));
    themeCard.body.id = "set-theme-body";
    grid.appendChild(themeCard.card);

    // —— S-03 缓存卡：内存/磁盘/布局占用 + TTL 口径 + 清理（R3-13） ——
    var cacheCard = buildCard("ST-CA", t("set.card.cache"), "g-6", t("set.card.cacheSub"));
    cacheCard.body.id = "set-cache-body";
    grid.appendChild(cacheCard.card);

    // —— ST-CP 数据与合规卡（R3-13）：盘中估值 + 养基宝增强通道两个 opt-in 开关 ——
    var complianceCard = buildCard("ST-CP", t("set.card.compliance"), "g-12", t("set.card.complianceSub"));
    complianceCard.body.id = "set-compliance-body";
    grid.appendChild(complianceCard.card);

    // —— ST-YJ 养基宝连接卡（首启向导兜底）：注入签名盐（DPAPI/掩码）+ 绑定状态 + 去扫码 ——
    var yjbCard = buildCard("ST-YJ", t("set.card.yjb"), "g-12", t("set.card.yjbSub"));
    yjbCard.body.id = "set-yjb-body";
    grid.appendChild(yjbCard.card);

    // —— ST-CL 启动与关闭卡（R6-12 / F6-14）：点关闭按钮时「最小化到托盘 / 直接退出」——
    var closeCard = buildCard("ST-CL", t("set.card.close"), "g-12", t("set.card.closeSub"));
    closeCard.body.id = "set-close-body";
    grid.appendChild(closeCard.card);

    // —— S-05 关于卡：版本/许可证/数据源数 + 日志导出 + 恢复出厂 ——
    var aboutCard = buildCard("ST-AB", t("set.card.about"), "g-12", t("set.card.aboutSub"));
    aboutCard.body.id = "set-about-body";
    grid.appendChild(aboutCard.card);

    container.appendChild(grid);
  }

  /** 渲染「数据与合规」卡：盘中估值 + 养基宝增强通道两个 opt-in 开关（R3-13 S-04）。
   * 两个开关默认均为关；关闭后相关通道立即回退 T+1 官方净值口径。
   * @returns {void} 无返回值（异步渲染）
   */
  function paintSettingsCompliance() {
    var body = document.getElementById("set-compliance-body");
    if (!body) {
      return;
    }
    setLoading(body);
    api("/api/fund/estimation", {})
      .then(function (data) {
        body.innerHTML = "";
        var row = el("div", "page-toolbar");
        row.appendChild(el("span", "pg-toolbar-label", t("set.estimation.label")));
        var toggle = el("button", "pg-btn" + (data.enabled ? " primary" : ""),
          data.enabled ? t("set.estimation.on") : t("set.estimation.off"));
        toggle.type = "button";
        toggle.addEventListener("click", function () {
          fetch("/api/fund/estimation", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled: !data.enabled })
          }).then(function (response) { return response.json(); })
            .then(function (res) {
              if (res.error) {
                toast(res.error);
                return;
              }
              toast(t("set.estimation.toggled"));
              paintSettingsCompliance();
            })
            .catch(function () { toast(t("set.estimation.fail")); });
        });
        row.appendChild(toggle);
        body.appendChild(row);
        // 常驻口径角标：估算非官方，关闭即回退 T+1
        body.appendChild(el("div", "pg-card-sub", t("set.estimation.note")));

        // 养基宝增强通道开关（Y-04 一级估值通道；未绑定时引导去扫码）
        return api("/api/fund/yjb/status", {}).then(function (yjb) {
          var yjbRow = el("div", "page-toolbar");
          yjbRow.appendChild(el("span", "pg-toolbar-label", t("set.yjb.label")));
          if (!yjb.bound) {
            yjbRow.appendChild(el("span", "pg-card-sub", t("set.yjb.unbound")));
            body.appendChild(yjbRow);
            body.setAttribute("data-state", "default");
            return;
          }
          var yjbToggle = el("button", "pg-btn" + (yjb.enabled ? " primary" : ""),
            yjb.enabled ? t("set.yjb.on") : t("set.yjb.off"));
          yjbToggle.type = "button";
          yjbToggle.addEventListener("click", function () {
            apiPost("/api/fund/yjb/toggle", { enabled: !yjb.enabled })
              .then(function () {
                toast(t("set.yjb.toggled"));
                paintSettingsCompliance();
              })
              .catch(function (error) { toast(error.message); });
          });
          yjbRow.appendChild(yjbToggle);
          body.appendChild(yjbRow);
          body.appendChild(el("div", "pg-card-sub", t("set.yjb.note")));
          body.setAttribute("data-state", "default");
        });
      })
      .catch(function (error) {
        setError(body, error.message, function () { paintSettingsCompliance(); });
      });
  }

  /** AI 配置表单字段构造（输入框样式与持仓页表单一致）。
   * @param {string} id 元素 id / 组件 id
   * @param {*} placeholder 占位文案
   * @param {*} width 宽度（栅格列数）
   * @param {*} secret 签名密钥（敏感信息）
   * @returns {*} 处理结果
   */
  function settingsField(id, placeholder, width, secret) {
    var input = document.createElement("input");
    input.id = id;
    input.placeholder = placeholder;
    if (secret) {
      input.type = "password";
    }
    input.autocomplete = "off";
    input.style.cssText = "width:" + (width || 220) + "px;background:var(--bg-panel);"
      + "border:1px solid var(--border);color:var(--text);border-radius:5px;padding:4px 8px";
    return input;
  }

  /** 渲染 AI 设置卡：服务商预设 + 配置回显（掩码）+ 保存 + 测试连接。 */
  function paintSettingsAI() {
    var body = document.getElementById("set-ai-body");
    if (!body) {
      return;
    }
    setLoading(body);
    api("/api/ai/config", {})
      .then(function (data) {
        body.innerHTML = "";
        var form = el("div", "page-toolbar");
        form.appendChild(el("span", "pg-toolbar-label", t("set.label.provider")));
        var provider = document.createElement("select");
        provider.id = "set-provider";
        provider.style.cssText = "background:var(--bg-panel);border:1px solid var(--border);"
          + "color:var(--text);border-radius:5px;padding:4px 8px";
        [
          { value: "deepseek", label: "DeepSeek", base: "https://api.deepseek.com/v1", model: "deepseek-chat" },
          { value: "qwen", label: "通义千问", base: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus" },
          { value: "custom", label: t("set.provider.custom"), base: "", model: "" }
        ].forEach(function (preset) {
          var option = document.createElement("option");
          option.value = preset.value;
          option.textContent = preset.label;
          option.dataset.base = preset.base;
          option.dataset.model = preset.model;
          provider.appendChild(option);
        });
        form.appendChild(provider);
        form.appendChild(el("span", "pg-toolbar-label", t("set.label.baseUrl")));
        form.appendChild(settingsField("set-base-url", t("set.placeholder.baseUrl"), 240));
        form.appendChild(el("span", "pg-toolbar-label", t("set.label.apiKey")));
        form.appendChild(settingsField("set-api-key", data.configured ? t("set.placeholder.apiKeySaved") : t("set.placeholder.apiKeyNew"), 240, true));
        form.appendChild(el("span", "pg-toolbar-label", t("set.label.model")));
        form.appendChild(settingsField("set-model", t("set.placeholder.model"), 180));
        form.appendChild(el("span", "pg-spacer"));
        var save = el("button", "pg-btn primary", t("set.action.save"));
        save.type = "button";
        save.id = "set-ai-save";
        form.appendChild(save);
        // 测试连接：用当前表单值直连上游探测（api_key 留空时用已保存密钥）
        var testBtn = el("button", "pg-btn", t("set.action.test"));
        testBtn.type = "button";
        testBtn.id = "set-ai-test";
        form.appendChild(testBtn);
        body.appendChild(form);
        // 回显：base_url/model 明文（非敏感），api_key 只显示掩码状态
        document.getElementById("set-base-url").value = data.base_url || "";
        document.getElementById("set-model").value = data.model || "";
        // 按已保存的 base_url 反选服务商预设（匹配不到回落 custom）
        var matched = "custom";
        Array.prototype.slice.call(provider.options).forEach(function (option) {
          if (option.dataset.base && option.dataset.base === (data.base_url || "")) {
            matched = option.value;
          }
        });
        provider.value = matched;
        // 选择预设时自动填充 base_url/model（custom 不动已填内容）
        provider.addEventListener("change", function () {
          var option = provider.options[provider.selectedIndex];
          if (!option || !option.dataset || !option.dataset.base) {
            return;
          }
          document.getElementById("set-base-url").value = option.dataset.base;
          document.getElementById("set-model").value = option.dataset.model;
        });
        var status = el("div", "pg-card-sub");
        status.id = "set-ai-status";
        status.textContent = data.configured
          ? t("set.ai.configured", { model: data.model || t("set.ai.modelEmpty"), key: data.api_key || t("set.ai.keyMasked") })
          : t("set.ai.notConfigured");
        body.appendChild(status);
        body.appendChild(el("div", "pg-card-sub", t("set.ai.securityNote")));
        body.setAttribute("data-state", "default");

        save.addEventListener("click", function () {
          if (state.settings.saving) {
            return; // 防重复提交
          }
          state.settings.saving = true;
          save.disabled = true;
          save.textContent = t("set.action.saving");
          apiPost("/api/ai/config", {
            base_url: (document.getElementById("set-base-url").value || "").trim(),
            api_key: (document.getElementById("set-api-key").value || "").trim(),
            model: (document.getElementById("set-model").value || "").trim()
          })
            .then(function () {
              toast(t("set.toast.aiSaved"));
              state.settings.saving = false;
              save.disabled = false;
              save.textContent = t("set.action.save");
              paintSettingsAI(); // 回显最新状态（密钥掩码）
            })
            .catch(function (error) {
              toast(t("set.toast.aiFailed", { message: error.message }));
              state.settings.saving = false;
              save.disabled = false;
              save.textContent = t("set.action.save");
            });
        });

        // 测试连接：加载态 + 结果 toast（R3-13）
        testBtn.addEventListener("click", function () {
          if (state.settings.testing) {
            return;
          }
          state.settings.testing = true;
          testBtn.disabled = true;
          testBtn.textContent = t("set.action.testing");
          apiPost("/api/ai/test", {
            base_url: (document.getElementById("set-base-url").value || "").trim(),
            api_key: (document.getElementById("set-api-key").value || "").trim(),
            model: (document.getElementById("set-model").value || "").trim()
          })
            .then(function (detail) {
              // apiPost 成功即代表探测通过：data 透传 {ok, latency_ms, detail}
              toast(t("set.toast.testOk", { ms: (detail && detail.latency_ms) || 0 }));
            })
            .catch(function (error) {
              // 业务失败（未配置/鉴权/超时/上游异常）走 reject，error.message 已是中文
              toast(t("set.toast.testFail", { message: error.message }));
            })
            .then(function () {
              state.settings.testing = false;
              testBtn.disabled = false;
              testBtn.textContent = t("set.action.test");
            });
        });
      })
      .catch(function (error) {
        setError(body, error.message, paintSettingsAI);
      });
  }

  /** 渲染外观卡：主题切换 + 字号档位（R3-13 S-02）。
   * 字号通过 html 的 data-font 属性 + CSS 变量缩放，立即生效并持久化。
   */
  function paintSettingsTheme() {
    var body = document.getElementById("set-theme-body");
    if (!body) {
      return;
    }
    body.innerHTML = "";
    var light = t("set.theme.light");
    var dark = t("set.theme.dark");
    var current = document.documentElement.getAttribute("data-theme") === "light" ? light : dark;
    var row = el("div", "page-toolbar");
    row.appendChild(el("span", "pg-toolbar-label", t("set.theme.current", { theme: current })));
    var toggle = el("button", "pg-btn", t(current === light ? "set.theme.toDark" : "set.theme.toLight"));
    toggle.type = "button";
    toggle.id = "set-theme-toggle";
    row.appendChild(toggle);
    body.appendChild(row);
    // 主题切换与 app.js 的 applyTheme 同口径：写 data-theme + localStorage["openbb-theme"]
    toggle.addEventListener("click", function () {
      var next = document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light";
      document.documentElement.setAttribute("data-theme", next);
      try {
        window.localStorage.setItem("openbb-theme", next);
      } catch (error) {
        /* 隐私模式下忽略持久化，主题仍生效 */
      }
      paintSettingsTheme(); // 刷新按钮文案
      charts.forEach(function (instance) {
        try {
          instance.resize();
        } catch (error) {
          /* 已释放则忽略 */
        }
      });
      toast(t("set.theme.toggled"));
    });

    // 字号档位：小/中/大 → html[data-font] → CSS 变量缩放（R3-13 S-02）
    var fontRow = el("div", "page-toolbar");
    fontRow.appendChild(el("span", "pg-toolbar-label", t("set.font.label")));
    var currentFont = document.documentElement.getAttribute("data-font") || "md";
    [
      { value: "sm", label: t("set.font.sm") },
      { value: "md", label: t("set.font.md") },
      { value: "lg", label: t("set.font.lg") }
    ].forEach(function (item) {
      var button = el("button", "pg-btn" + (currentFont === item.value ? " primary" : ""), item.label);
      button.type = "button";
      button.addEventListener("click", function () {
        document.documentElement.setAttribute("data-font", item.value);
        try {
          window.localStorage.setItem("openbb-font", item.value);
        } catch (error) {
          /* 隐私模式下忽略持久化，字号仍生效 */
        }
        charts.forEach(function (instance) {
          try {
            instance.resize();
          } catch (error) {
            /* 已释放则忽略 */
          }
        });
        toast(t("set.font.toggled", { label: item.label }));
        paintSettingsTheme();
      });
      fontRow.appendChild(button);
    });
    body.appendChild(fontRow);
    body.appendChild(el("div", "pg-card-sub", t("set.font.note")));
    body.setAttribute("data-state", "default");
  }

  /** 渲染缓存卡：占用统计 + TTL 口径表 + 清理按钮（R3-13 S-03）。 */
  function paintSettingsCache() {
    var body = document.getElementById("set-cache-body");
    if (!body) {
      return;
    }
    setLoading(body);
    api("/api/cache/stats", {})
      .then(function (data) {
        body.innerHTML = "";
        var grid = el("div", "pg-metric-grid");
        [
          [t("set.cache.memory"), data.memory + " " + t("set.cache.entries") + " · " + (data.memory_mb || 0) + " MB", "flat"],
          [t("set.cache.disk"), data.disk + " " + t("set.cache.files") + " · " + (data.disk_mb || 0) + " MB", "flat"],
          [t("set.cache.layout"), (data.layout_kb || 0) + " KB", "flat"]
        ].forEach(function (item) {
          var cell = el("div", "pg-metric-cell");
          cell.appendChild(el("div", "pg-metric-label", item[0]));
          cell.appendChild(el("div", "pg-metric-value " + item[2], String(item[1])));
          grid.appendChild(cell);
        });
        body.appendChild(grid);

        var row = el("div", "page-toolbar");
        var clearBtn = el("button", "pg-btn", t("set.cache.clear"));
        clearBtn.type = "button";
        clearBtn.addEventListener("click", function () {
          clearBtn.disabled = true;
          clearBtn.textContent = t("set.cache.clearing");
          fetch("/api/cache/clear", { method: "POST" })
            .then(function (response) { return response.json(); })
            .then(function (result) {
              toast(result.error ? t("toast.cacheFailed") : t("toast.cacheCleared"));
              paintSettingsCache();
            })
            .catch(function () { toast(t("toast.cacheFailed")); })
            .then(function () {
              clearBtn.disabled = false;
              clearBtn.textContent = t("set.cache.clear");
            });
        });
        row.appendChild(clearBtn);
        row.appendChild(el("span", "pg-spacer"));
        row.appendChild(el("span", "pg-card-sub", t("set.cache.scope")));
        body.appendChild(row);

        // TTL 口径表（与 07_数据 数据字典 §9 同源）
        var table = el("table", "pg-table");
        var head = document.createElement("tr");
        [t("set.cache.ttlKey"), t("set.cache.ttlValue"), t("set.cache.ttlNote")].forEach(function (label) {
          var th = document.createElement("th");
          th.textContent = label;
          head.appendChild(th);
        });
        table.appendChild(head);
        (data.ttl_map || []).forEach(function (item) {
          var tr = document.createElement("tr");
          [item.key, String(item.ttl), item.note || "—"].forEach(function (text, index) {
            var td = document.createElement("td");
            td.textContent = text;
            if (index === 0) {
              td.className = "mono";
            }
            tr.appendChild(td);
          });
          table.appendChild(tr);
        });
        body.appendChild(table);
        body.setAttribute("data-state", "default");
      })
      .catch(function (error) {
        setError(body, error.message, paintSettingsCache);
      });
  }

  /** 渲染关于卡：版本/许可证/数据源数 + 日志导出 + 恢复出厂（R3-13 S-05）。 */
  function paintSettingsAbout() {
    var body = document.getElementById("set-about-body");
    if (!body) {
      return;
    }
    setLoading(body);
    api("/api/health", {})
      .then(function (data) {
        body.innerHTML = "";
        var app = data.app || {};
        var rows = [
          [t("set.about.software"), app.name || t("set.about.default")],
          [t("set.about.version"), app.version || "—"],
          [t("set.about.build"), data.build || "—"],
          [t("set.about.python"), data.python || "—"],
          [t("set.about.datasources"), t("set.about.datasourcesValue", { count: Object.keys(data.datasources || {}).length })],
          [t("set.about.license"), t("set.about.licenseValue")],
          [t("set.about.serverTime"), data.server_time || "—"]
        ];
        var table = el("table", "pg-table");
        rows.forEach(function (pair) {
          var tr = document.createElement("tr");
          var th = document.createElement("th");
          th.textContent = pair[0];
          var td = document.createElement("td");
          td.textContent = pair[1];
          tr.appendChild(th);
          tr.appendChild(td);
          table.appendChild(tr);
        });
        body.appendChild(table);

        // 日志导出：GET /api/logs/export 直接下载 zip
        var row = el("div", "page-toolbar");
        var exportBtn = el("button", "pg-btn", t("set.about.exportLogs"));
        exportBtn.type = "button";
        exportBtn.addEventListener("click", function () {
          exportBtn.disabled = true;
          window.open("/api/logs/export", "_blank");
          toast(t("set.about.exportStarted"));
          exportBtn.disabled = false;
        });
        row.appendChild(exportBtn);

        // 恢复出厂：二次确认弹窗（R3-13）；只清应用偏好，不动持仓/自选/密钥
        var resetBtn = el("button", "pg-btn danger", t("set.about.factoryReset"));
        resetBtn.type = "button";
        resetBtn.addEventListener("click", function () {
          if (!window.UI) {
            return;
          }
          window.UI.modal({
            title: t("set.about.factoryReset"),
            message: t("set.about.factoryWarn"),
            confirmText: t("set.about.factoryConfirm"),
            danger: true,
            /** 确认恢复出厂：显式提交 confirm + 全部偏好 scope。
             * @returns {void} 无返回值
             */
            onConfirm: function () {
              apiPost("/api/factory-reset", {
                confirm: true,
                scopes: ["prefs", "connectors", "dashboards", "ai"]
              })
                .then(function (result) {
                  var removed = (result && result.removed) || [];
                  toast(removed.length
                    ? t("set.about.factoryDone", { n: removed.length })
                    : t("set.about.factoryNothing"));
                  paintSettingsAbout();
                })
                .catch(function (error) { toast(error.message); });
            }
          });
        });
        row.appendChild(resetBtn);
        body.appendChild(row);
        body.appendChild(el("div", "pg-card-sub", t("set.about.factoryNote")));
        body.appendChild(el("div", "pg-card-sub", t("set.about.disclaimer")));
        body.setAttribute("data-state", "default");
      })
      .catch(function (error) {
        setError(body, error.message, paintSettingsAbout);
      });
  }

  /** 渲染置页入口：骨架 + 五卡数据（AI/外观/缓存/合规/关于）。 */
  // =============================================================
  // 第七轮 M1：宏观与周期（#/macro）
  // =============================================================
  /** 宏观页外壳（指标卡 + 周期象限 + 双轴走势），进入即取数。 */
  function renderMacro() {
    var page = document.getElementById(VIEW_IDS.macro);
    if (!page) {
      return;
    }
    page.innerHTML = "";
    var toolbar = el("div", "page-toolbar");
    var refresh = el("button", "pg-btn", t("macro.refresh"));
    refresh.type = "button";
    refresh.id = "macro-refresh";
    refresh.addEventListener("click", function () { loadMacro(true); });
    toolbar.appendChild(refresh);
    toolbar.appendChild(el("span", "pg-spacer"));
    toolbar.appendChild(el("span", "pg-card-sub", t("macro.toolbarHint")));
    page.appendChild(toolbar);

    var grid = el("div", "pg-grid");
    var kpi = buildCard("MC-01", t("macro.card.kpi"), "g-12", t("macro.card.kpiSub"));
    kpi.body.id = "mc01-body";
    grid.appendChild(kpi.card);
    var quad = buildCard("MC-02", t("macro.card.quad"), "g-5", t("macro.card.quadSub"));
    quad.body.id = "mc02-body";
    grid.appendChild(quad.card);
    var chart = buildCard("MC-03", t("macro.card.chart"), "g-7", t("macro.card.chartSub"));
    chart.body.id = "mc03-body";
    grid.appendChild(chart.card);
    page.appendChild(grid);
    loadMacro(false);
  }

  /** 拉取并渲染宏观总览。
   * @param {boolean} force 是否绕过缓存强制重取
   * @returns {void} 无返回值（异步渲染）
   */
  function loadMacro(force) {
    var body = document.getElementById("mc01-body");
    var quadBody = document.getElementById("mc02-body");
    var chartBody = document.getElementById("mc03-body");
    if (!body || !quadBody || !chartBody) {
      return;
    }
    setLoading(body);
    setLoading(quadBody);
    setLoading(chartBody);
    api("/api/macro/overview", { force: force ? 1 : "" }).then(function (data) {
      var card = document.getElementById("MC-01");
      setBadges(card, data.source, data.caliber);

      // ---- 指标卡 ----
      body.innerHTML = "";
      var grid = el("div", "macro-kpis");
      (data.indicators || []).forEach(function (item) {
        var box = el("div", "macro-kpi" + (item.available ? "" : " is-missing"));
        box.appendChild(el("div", "macro-kpi-name", item.name));
        if (item.available) {
          // 宏观指标不套用涨跌红绿：A 股语境里红=涨/好、绿=跌/坏，
          // 但 CPI/PPI 上行未必是好事（通胀压制估值），M1−M2 收窄也未必是坏事。
          // 用中性文字 + ↑↓ 箭头表达「方向」，避免把宏观数据误读成利好利空。
          var arrow = item.direction === "up" ? "↑ " : (item.direction === "down" ? "↓ " : "");
          var valueText = item.digits === 0 ? fmt(item.value, 0) : fmt(item.value, item.digits);
          box.appendChild(el("div", "macro-kpi-value", valueText + (item.unit || "")));
          var deltaText = item.delta === null || item.delta === undefined
            ? t("macro.noPrev")
            : (item.delta > 0 ? "+" : "") + fmt(item.delta, item.digits);
          box.appendChild(el("div", "macro-kpi-delta", arrow + t("macro.vsPrev", { delta: deltaText })));
          box.appendChild(el("div", "macro-kpi-period", item.period || ""));
        } else {
          box.appendChild(el("div", "macro-kpi-value flat", t("macro.unavailable")));
          box.appendChild(el("div", "macro-kpi-reason", item.reason || ""));
        }
        grid.appendChild(box);
      });
      body.appendChild(grid);
      body.setAttribute("data-state", "default");

      // ---- 周期象限 ----
      var quadrant = data.quadrant || {};
      quadBody.innerHTML = "";
      var stage = el("div", "macro-stage");
      stage.appendChild(el("span", "macro-stage-name", quadrant.stage || t("value.none")));
      stage.appendChild(el("span", "macro-stage-axis",
        t("macro.axisSummary", {
          growth: quadrant.growth === "up" ? t("macro.axis.up") : (quadrant.growth === "down" ? t("macro.axis.down") : t("macro.axis.flat")),
          inflation: quadrant.inflation === "up" ? t("macro.axis.up") : (quadrant.inflation === "down" ? t("macro.axis.down") : t("macro.axis.flat"))
        })));
      quadBody.appendChild(stage);

      var quadBox = el("div", "macro-quad");
      quadBox.appendChild(el("i", "mq-axis-v"));
      quadBox.appendChild(el("i", "mq-axis-h"));
      quadBox.appendChild(el("span", "mq-zone mq-lt", t("macro.quad.stagflation")));
      quadBox.appendChild(el("span", "mq-zone mq-rt", t("macro.quad.overheat")));
      quadBox.appendChild(el("span", "mq-zone mq-lb", t("macro.quad.recession")));
      quadBox.appendChild(el("span", "mq-zone mq-rb", t("macro.quad.recovery")));
      var dot = el("span", "mq-dot");
      var growthRight = quadrant.growth === "up";
      var inflationUp = quadrant.inflation === "up";
      dot.style.left = growthRight ? "72%" : "26%";
      dot.style.top = inflationUp ? "26%" : "72%";
      quadBox.appendChild(dot);
      quadBody.appendChild(quadBox);
      quadBody.appendChild(el("div", "macro-conclusion", quadrant.conclusion || ""));
      quadBody.appendChild(el("div", "pg-card-sub", quadrant.disclaimer || ""));
      quadBody.setAttribute("data-state", "default");

      // ---- 双轴走势 ----
      chartBody.innerHTML = "";
      var pmiSeries = (data.series || {}).pmi || [];
      var ppiSeries = (data.series || {}).ppi || [];
      if (!pmiSeries.length && !ppiSeries.length) {
        chartBody.appendChild(el("div", "pg-state", t("macro.chartEmpty")));
        chartBody.setAttribute("data-state", "empty");
        return;
      }
      var box = el("div", "pg-chart");
      chartBody.appendChild(box);
      var chart = mountChart(box);
      var theme = chartTheme();
      if (chart) {
        var periods = pmiSeries.map(function (item) { return item.period; });
        var ppiMap = {};
        ppiSeries.forEach(function (item) { ppiMap[item.period] = item.value; });
        chart.setOption({
          backgroundColor: "transparent",
          tooltip: { trigger: "axis" },
          legend: { textStyle: { color: theme.text }, top: 0,
            data: [t("macro.series.pmi"), t("macro.series.ppi")] },
          grid: { left: 52, right: 52, top: 32, bottom: 34 },
          xAxis: {
            type: "category", data: periods,
            axisLabel: { color: theme.text, fontSize: 10,
              interval: Math.max(1, Math.floor(periods.length / 8)) },
            axisLine: { lineStyle: { color: theme.border } }
          },
          yAxis: [
            { type: "value", scale: true, name: "PMI",
              nameTextStyle: { color: theme.text, fontSize: 10 },
              axisLabel: { color: theme.text, fontSize: 10 },
              splitLine: { lineStyle: { color: theme.border, opacity: 0.4 } } },
            { type: "value", scale: true, name: "PPI%",
              nameTextStyle: { color: theme.text, fontSize: 10 },
              axisLabel: { color: theme.text, fontSize: 10 },
              splitLine: { show: false } }
          ],
          series: [
            { name: t("macro.series.pmi"), type: "line", smooth: true, showSymbol: false,
              data: pmiSeries.map(function (item) { return item.value; }),
              color: "#b47da0", lineStyle: { width: 1.8 } },
            { name: t("macro.series.ppi"), type: "line", smooth: true, showSymbol: false, yAxisIndex: 1,
              data: periods.map(function (period) { return ppiMap[period]; }),
              color: "#4a9eff", lineStyle: { width: 1.8 } }
          ]
        });
      }
      chartBody.setAttribute("data-state", "default");
    }).catch(function (error) {
      setError(body, error.message || t("state.errorHint"), function () { loadMacro(true); });
      setError(quadBody, error.message || t("state.errorHint"), function () { loadMacro(true); });
      setError(chartBody, error.message || t("state.errorHint"), function () { loadMacro(true); });
    });
  }

  // =============================================================
  // 第七轮 M2：估值分位（#/valuation）
  // =============================================================
  /** 估值分位页外壳（指数表 + 全 A + 行业横截面），进入即取数。 */
  function renderValuation() {
    var page = document.getElementById(VIEW_IDS.valuation);
    if (!page) {
      return;
    }
    page.innerHTML = "";
    var toolbar = el("div", "page-toolbar");
    var refresh = el("button", "pg-btn", t("valuation.refresh"));
    refresh.type = "button";
    refresh.id = "valuation-refresh";
    refresh.addEventListener("click", function () { loadValuation(true); });
    toolbar.appendChild(refresh);
    toolbar.appendChild(el("span", "pg-spacer"));
    toolbar.appendChild(el("span", "pg-card-sub", t("valuation.toolbarHint")));
    page.appendChild(toolbar);

    var grid = el("div", "pg-grid");
    var idx = buildCard("VL-01", t("valuation.card.index"), "g-12", t("valuation.card.indexSub"));
    idx.body.id = "vl01-body";
    grid.appendChild(idx.card);
    var market = buildCard("VL-02", t("valuation.card.market"), "g-5", t("valuation.card.marketSub"));
    market.body.id = "vl02-body";
    grid.appendChild(market.card);
    var industry = buildCard("VL-03", t("valuation.card.industry"), "g-7", t("valuation.card.industrySub"));
    industry.body.id = "vl03-body";
    grid.appendChild(industry.card);
    page.appendChild(grid);
    loadValuation(false);
  }

  /** 拉取并渲染估值分位。
   * @param {boolean} force 是否绕过缓存强制重取
   * @returns {void} 无返回值（异步渲染）
   */
  function loadValuation(force) {
    var body1 = document.getElementById("vl01-body");
    var body2 = document.getElementById("vl02-body");
    var body3 = document.getElementById("vl03-body");
    if (!body1 || !body2 || !body3) {
      return;
    }
    setLoading(body1);
    setLoading(body2);
    setLoading(body3);
    api("/api/valuation/percentile", { force: force ? 1 : "" }).then(function (data) {
      var card = document.getElementById("VL-01");
      setBadges(card, data.source, data.caliber);

      /** 分位色阶：低=跌色（绿，估值低）、高=涨色（红，估值高），沿用全站涨跌两色。
       * @param {number} pct 分位值
       * @returns {string} CSS 类名
       */
      function pctClassOf(pct) {
        if (pct === null || pct === undefined) {
          return "flat";
        }
        if (pct <= 20) {
          return "down";
        }
        if (pct >= 80) {
          return "up";
        }
        return "flat";
      }

      /** 格式化分位。
       * @param {number} value 分位值
       * @returns {string} 文案
       */
      function fmtPctile(value) {
        return value === null || value === undefined ? t("value.none") : fmt(value, 1) + "%";
      }

      // ---- 指数表 ----
      body1.innerHTML = "";
      var rows = (data.indices || []).filter(function (item) { return item.available; });
      if (!rows.length) {
        body1.appendChild(el("div", "pg-state", t("valuation.indexEmpty")));
        body1.setAttribute("data-state", "empty");
      } else {
        var tableHost = el("div", "");
        body1.appendChild(tableHost);
        renderTable(tableHost, [
          { title: t("valuation.col.name"), cls: "l", render: function (row) { return row.name; } },
          { title: t("valuation.col.pe"), num: true, render: function (row) { return fmt(row.pe, 2); } },
          { title: t("valuation.col.pct3y"), num: true, render: function (row) {
            return el("span", pctClassOf(row.pct3y), fmtPctile(row.pct3y)); } },
          { title: t("valuation.col.pct5y"), num: true, render: function (row) {
            return el("span", pctClassOf(row.pct5y), fmtPctile(row.pct5y)); } },
          { title: t("valuation.col.pct10y"), num: true, render: function (row) {
            return el("span", pctClassOf(row.pct10y), fmtPctile(row.pct10y)); } },
          { title: t("valuation.col.erp"), num: true, render: function (row) {
            return row.erp === null || row.erp === undefined
              ? t("value.none") : el("span", row.erp > 0 ? "up" : "down", fmt(row.erp, 2) + "%"); } },
          { title: t("valuation.col.period"), cls: "l", render: function (row) { return row.period || ""; } }
        ], rows);
        body1.setAttribute("data-state", "default");
      }
      var missing = (data.indices || []).filter(function (item) { return !item.available; });
      if (missing.length) {
        body1.appendChild(el("div", "pg-card-sub",
          t("valuation.indexMissing", { names: missing.map(function (item) { return item.name + "（" + (item.reason || "") + "）"; }).join("；") })));
      }
      body1.appendChild(el("div", "pg-card-sub", data.disclaimer || ""));

      // ---- 全 A ----
      body2.innerHTML = "";
      var market = data.market || {};
      if (!market.available) {
        body2.appendChild(el("div", "pg-state", market.reason || t("state.empty")));
        body2.setAttribute("data-state", "empty");
      } else {
        var grid = el("div", "pg-kpis");
        [
          [t("valuation.col.pe"), fmt(market.pe, 2), "flat", market.period || ""],
          [t("valuation.col.pct3y"), fmtPctile(market.pct3y), pctClassOf(market.pct3y), ""],
          [t("valuation.col.pct5y"), fmtPctile(market.pct5y), pctClassOf(market.pct5y), ""],
          [t("valuation.col.pct10y"), fmtPctile(market.pct10y), pctClassOf(market.pct10y), ""]
        ].forEach(function (row) {
          var box = el("div", "pg-kpi");
          box.appendChild(el("div", "pg-kpi-k", row[0]));
          box.appendChild(el("div", "pg-kpi-v " + row[2], row[1]));
          if (row[3]) {
            box.appendChild(el("div", "pg-kpi-x", row[3]));
          }
          grid.appendChild(box);
        });
        body2.appendChild(grid);
        body2.appendChild(el("div", "pg-card-sub", t("valuation.marketNote")));
        body2.setAttribute("data-state", "default");
      }

      // ---- 行业横截面 ----
      body3.innerHTML = "";
      // 取横截面分位的两端各 8 个（最便宜 + 最贵），而不是 PE 最高的 16 个：
      // 只取高 PE 会让色阶全红、失去信息量；两端取样能一眼看出「贵 vs 便宜」的结构。
      var allIndustries = (data.industries || []).slice().sort(function (a, b) {
        return (a.cross_pct || 0) - (b.cross_pct || 0);
      });
      var industries = allIndustries.length <= 16
        ? allIndustries
        : allIndustries.slice(0, 8).concat(allIndustries.slice(-8));
      if (!industries.length) {
        body3.appendChild(el("div", "pg-state", data.industry_note || t("valuation.industryEmpty")));
        body3.setAttribute("data-state", "empty");
      } else {
        var heat = el("div", "macro-heat");
        industries.forEach(function (item) {
          var tone = item.cross_pct >= 80 ? "#ff4b4b" : (item.cross_pct <= 20 ? "#00b061" : "#5a6a8a");
          var cell = el("div", "heat-cell");
          cell.style.background = tone;
          cell.appendChild(el("b", "", item.name));
          cell.appendChild(el("span", "", "PE " + fmt(item.pe, 1) + " · " + fmt(item.cross_pct, 0) + "%"));
          heat.appendChild(cell);
        });
        body3.appendChild(heat);
        var snapshot = (data.industries[0] || {}).snapshot_date || "";
        body3.appendChild(el("div", "pg-card-sub",
          t("valuation.industryNote", { date: snapshot, n: (data.industries || []).length })));
        body3.setAttribute("data-state", "default");
      }
    }).catch(function (error) {
      setError(body1, error.message || t("state.errorHint"), function () { loadValuation(true); });
      setError(body2, error.message || t("state.errorHint"), function () { loadValuation(true); });
      setError(body3, error.message || t("state.errorHint"), function () { loadValuation(true); });
    });
  }

  function renderSettings() {
    var container = document.getElementById(VIEW_IDS.settings);
    if (!container.querySelector("#set-ai-body")) {
      buildSettingsShell(container);
    }
    paintSettingsAI();
    paintSettingsTheme();
    paintSettingsCache();
    paintSettingsAbout();
    paintSettingsCompliance();
    paintSettingsYjb();
    paintSettingsClose();
  }

  /** 渲染「启动与关闭」卡（R6-12 / F6-14）。
   *
   * 两个单选项写入 data/user_prefs.json 的 close_action；桌面壳在点关闭按钮时
   * 读取该值决定「隐藏到托盘」还是「直接退出」。默认 tray。
   *
   * @returns {void} 无返回值（异步渲染）
   */
  function paintSettingsClose() {
    var body = document.getElementById("set-close-body");
    if (!body) {
      return;
    }
    setLoading(body);
    api("/api/system/desktop-prefs", {}).then(function (data) {
      body.innerHTML = "";
      var current = data.close_action === "quit" ? "quit" : "tray";
      var list = el("div", "set-close-list");
      [
        ["tray", t("set.close.tray"), t("set.close.trayHint")],
        ["quit", t("set.close.quit"), t("set.close.quitHint")]
      ].forEach(function (option) {
        var row = el("label", "set-close-row" + (current === option[0] ? " active" : ""));
        var radio = document.createElement("input");
        radio.type = "radio";
        radio.name = "close-action";
        radio.value = option[0];
        radio.checked = current === option[0];
        radio.addEventListener("change", function () {
          if (!radio.checked) {
            return;
          }
          // Utils.api 只支持 GET，写偏好走原生 fetch（PUT + JSON）
          fetch("/api/system/desktop-prefs", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ close_action: option[0] })
          }).then(function (response) {
            return response.json();
          }).then(function (payload) {
            if (payload && payload.error) {
              throw new Error(payload.error);
            }
            current = option[0];
            body.querySelectorAll(".set-close-row").forEach(function (node) {
              node.classList.toggle("active", node.contains(radio));
            });
            toast(t("set.close.saved"));
          }).catch(function (error) {
            toast(error.message || t("state.errorHint"));
          });
        });
        row.appendChild(radio);
        var text = el("div", "set-close-text");
        text.appendChild(el("b", "", option[1]));
        text.appendChild(el("span", "", option[2]));
        row.appendChild(text);
        list.appendChild(row);
      });
      body.appendChild(list);
      body.appendChild(el("div", "pg-card-sub", t("set.close.note")));
      body.setAttribute("data-state", "default");
    }).catch(function (error) {
      setError(body, error.message || t("state.errorHint"), paintSettingsClose);
    });
  }

  /** 渲染「养基宝连接」卡：绑定/盐/安全存储三态 + 掩码注入签名盐 + 去扫码。
   * 首启向导（Y-00）的第二步落到这里：冻结包 data 缺盐时用户可在设置页补盐，
   * 全程 DPAPI 加密落盘，输入框 type=password 且提交后立即清空，不回显明文。
   * @returns {void} 无返回值（异步渲染）
   */
  function paintSettingsYjb() {
    var body = document.getElementById("set-yjb-body");
    if (!body) {
      return;
    }
    setLoading(body);
    api("/api/fund/yjb/status", {}).then(function (data) {
      body.innerHTML = "";
      var chips = el("div", "page-toolbar");
      [
        [t("set.yjb.chip.bound"), data.bound],
        [t("set.yjb.chip.secret"), data.secret_configured],
        [t("set.yjb.chip.secure"), data.secure_storage]
      ].forEach(function (pair) {
        chips.appendChild(el("span", "chip " + (pair[1] ? "" : "down"),
          pair[0] + "：" + (pair[1] ? t("set.yjb.ok") : t("set.yjb.missing"))));
      });
      chips.appendChild(el("span", "pg-spacer"));
      var goFund = el("button", "pg-btn", t("set.yjb.goScan"));
      goFund.type = "button";
      goFund.addEventListener("click", function () { window.location.hash = "#/yjb"; });
      chips.appendChild(goFund);
      body.appendChild(chips);

      if (data.setup_required) {
        body.appendChild(el("div", "pg-card-sub", t("set.yjb.setupHint")));
      }
      var formHost = el("div", "");
      formHost.id = "set-yjb-secret";
      buildYjbSecretForm(formHost, data.secret_configured, null, function () {
        // 保存/清除成功后只刷新本卡，不依赖养基宝页 DOM 是否在场
        paintSettingsYjb();
        if (document.getElementById("y00-body")) {
          loadYjbStatus();
        }
      });
      body.appendChild(formHost);
      body.appendChild(el("div", "pg-card-sub", t("set.yjb.maskNote")));
      if (data.is_frozen && data.data_dir) {
        body.appendChild(el("div", "pg-card-sub", t("set.yjb.dataDir") + data.data_dir));
      }
      body.setAttribute("data-state", "default");
      // 从首启向导跳来：滚动到本卡并聚焦签名盐输入框
      if (window.__yjbFocusSecret) {
        window.__yjbFocusSecret = false;
        if (body.scrollIntoView) {
          body.scrollIntoView({ block: "center" });
        }
        var input = document.querySelector("#set-yjb-secret input[type=password]");
        if (input) {
          input.focus();
        }
      }
    }).catch(function (error) {
      setError(body, error.message || t("state.errorHint"), paintSettingsYjb);
    });
  }

  // =============================================================
  // 十三、初始化
  // =============================================================
  var resizeTimer = null;

  /** 窗口尺寸变化时重算所有图表尺寸（1024 断点不空白，P0-05）。 */
  function bindResize() {
    window.addEventListener("resize", function () {
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(function () {
        charts.forEach(function (instance) {
          try {
            instance.resize();
          } catch (error) {
            /* 已释放则忽略 */
          }
        });
      }, 120);
    });
  }


  // =============================================================
  // 二十一、自选股（W-01/W-02）与龙虎榜·北向（L-01~L-03）
  // 设计基准：新增功能设计基准_20261022.md；数据全部真实接口，无演示数据
  // =============================================================
  var MARKET_LABELS = { a: "A股", hk: "港股", us: "美股", fund: "场外基金" };

  /** 市场中文标签。
   * @param {string} market 市场标识
   * @returns {string} 中文标签
   */
  function marketLabel(market) {
    return MARKET_LABELS[market] || market;
  }

  /** 渲染自选股页（W-01 概览 + W-02 行情表）。
   */
  /** 当前自选分组过滤（W-02 分组 chips / W-03 侧栏树共用，空=全部）。
   * @type {string}
   */
  var watchlistGroup = "";

  /** 渲染自选股页（W-01 概览 + W-02 分组筛选 + 行情表）。
   * @param {Object} params 查询参数（支持 group=a/hk/us/fund 预过滤，来自侧栏树）
   * @returns {void} 无返回值
   */
  function renderWatchlist(params) {
    var page = document.getElementById("page-watchlist");
    if (!page) {
      return;
    }
    watchlistGroup = (params && params.group) || "";
    page.innerHTML = "";
    var grid = el("div", "pg-grid");
    var w1 = buildCard("W-01", t("watchlist.overview"), "g-12", t("watchlist.overviewSub"));
    w1.body.id = "w01-body";
    grid.appendChild(w1.card);
    var w2 = buildCard("W-02", t("watchlist.tableTitle"), "g-12", t("watchlist.tableSub"));
    w2.body.id = "w02-body";
    grid.appendChild(w2.card);
    page.appendChild(grid);
    loadWatchlist();
  }

  /** 拉取并渲染自选股（force=true 时绕过前端无缓存直接刷新）。
   * @param {boolean} force 是否强制刷新
   * @returns {void} 无返回值（异步渲染）
   */
  // ---------- R6-09 / F6-09：自选股批量操作状态 ----------
  /** 当前自选行缓存（批量操作按 symbol@market 反查行对象）。 */
  var watchRows = [];
  /** 当前选中的行键（symbol@market）。 */
  var watchSelected = [];
  /** 批量栏宿主（表格上方，随选中集重绘）。 */
  var watchBulkHost = null;

  /** 渲染自选股批量操作栏（选中 ≥1 行时显示）。
   *
   * 规格（设计基准 F6-09）：`已选 3 · [批量删除] [批量加图表] [导出CSV] [取消]`；
   * 批量删除二次确认并列出标的，逐条删除、部分失败逐条提示。
   *
   * @returns {void} 无返回值
   */
  function paintWatchBulkBar() {
    if (!watchBulkHost) {
      return;
    }
    watchBulkHost.innerHTML = "";
    if (!watchSelected.length) {
      watchBulkHost.hidden = true;
      return;
    }
    watchBulkHost.hidden = false;
    watchBulkHost.appendChild(el("span", "wl-bulk-count",
      t("watchlist.bulk.selected", { n: watchSelected.length })));

    /** 取当前选中的行对象。
     * @returns {Array<Object>} 选中行列表
     */
    function pickedRows() {
      return watchRows.filter(function (row) {
        return watchSelected.indexOf(row.symbol + "@" + row.market) >= 0;
      });
    }

    var del = el("button", "pg-btn", t("watchlist.bulk.delete"));
    del.type = "button";
    del.addEventListener("click", function () {
      var targets = pickedRows();
      if (!targets.length) {
        return;
      }
      var names = targets.map(function (row) { return row.name || row.symbol; }).join("、");
      if (!window.confirm(t("watchlist.bulk.deleteConfirm", { names: names, n: targets.length }))) {
        return;
      }
      var failed = [];
      var done = 0;
      /** 串行删除：逐条反馈，避免并发触发写锁。
       * @param {number} index 当前下标
       * @returns {void} 无返回值
       */
      function step(index) {
        if (index >= targets.length) {
          watchSelected = [];
          if (failed.length) {
            toast(t("watchlist.bulk.deletePartial", { ok: done, fail: failed.join("、") }));
          } else {
            toast(t("watchlist.bulk.deleteDone", { n: done }));
          }
          loadWatchlist(true);
          return;
        }
        var row = targets[index];
        sendJson("DELETE", "/api/watchlist/" + encodeURIComponent(row.symbol)
          + "?market=" + encodeURIComponent(row.market)).then(function () {
          done += 1;
          step(index + 1);
        }).catch(function () {
          failed.push(row.name || row.symbol);
          step(index + 1);
        });
      }
      step(0);
    });
    watchBulkHost.appendChild(del);

    var toBoard = el("button", "pg-btn", t("watchlist.bulk.toBoard"));
    toBoard.type = "button";
    toBoard.addEventListener("click", function () {
      var targets = pickedRows();
      if (!targets.length || !window.Dashboard || !window.Dashboard.addWidgetById) {
        return;
      }
      var added = 0;
      targets.forEach(function (row) {
        if (row.market === "fund") {
          return;
        }
        window.Dashboard.addWidgetById("ST-01", { symbol: row.symbol, market: row.market });
        added += 1;
      });
      toast(t("watchlist.bulk.toBoardDone", { n: added }));
      watchSelected = [];
      paintWatchBulkBar();
    });
    watchBulkHost.appendChild(toBoard);

    var csv = el("button", "pg-btn", t("watchlist.bulk.export"));
    csv.type = "button";
    csv.addEventListener("click", function () {
      var targets = pickedRows();
      if (!targets.length) {
        return;
      }
      var head = ["代码", "名称", "市场", "最新价", "涨跌", "涨幅%", "成交额", "PE"];
      var lines = [head.join(",")];
      targets.forEach(function (row) {
        var quote = row.quote || {};
        lines.push([row.symbol, row.name || "", row.market || "",
          quote.price === null || quote.price === undefined ? "" : quote.price,
          quote.change === null || quote.change === undefined ? "" : quote.change,
          quote.pct === null || quote.pct === undefined ? "" : quote.pct,
          quote.amount === null || quote.amount === undefined ? "" : quote.amount,
          quote.pe_ttm === null || quote.pe_ttm === undefined ? "" : quote.pe_ttm].join(","));
      });
      var blob = new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
      var link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = "watchlist_" + new Date().toISOString().slice(0, 10) + ".csv";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(link.href);
    });
    watchBulkHost.appendChild(csv);

    var cancel = el("button", "pg-btn ghost", t("watchlist.bulk.cancel"));
    cancel.type = "button";
    cancel.addEventListener("click", function () {
      watchSelected = [];
      loadWatchlist(true);
    });
    watchBulkHost.appendChild(cancel);
  }

  /** 渲染自选股空态（含「搜索代码直接加自选」）。
   *
   * 规格（设计基准 F6-09 空态）：0 条时给「从个股详情页点 ★ 添加」之外，
   * 额外提供搜索框，可直接按代码/名称检索并加入自选，避免必须先去个股页。
   *
   * @param {HTMLElement} body 内容容器
   * @param {string} hint 后端下发的空态说明（可为空）
   * @returns {void} 无返回值
   */
  function paintWatchlistEmpty(body, hint) {
    body.innerHTML = "";
    var box = el("div", "pg-state");
    box.setAttribute("data-state", "empty");
    box.appendChild(el("div", "pg-state-title", t("watchlist.emptyTitle")));
    box.appendChild(el("div", "pg-state-hint", hint || t("watchlist.empty")));

    var bar = el("div", "wl-addbar");
    var input = document.createElement("input");
    input.type = "search";
    input.className = "wl-addbar-input";
    input.placeholder = t("watchlist.addPlaceholder");
    input.setAttribute("aria-label", t("watchlist.addPlaceholder"));
    bar.appendChild(input);
    var searchBtn = el("button", "pg-btn primary", t("watchlist.addSearch"));
    searchBtn.type = "button";
    bar.appendChild(searchBtn);
    box.appendChild(bar);

    var resultHost = el("div", "wl-addresult");
    box.appendChild(resultHost);

    /** 执行检索并渲染「加入自选」候选。
     * @returns {void} 无返回值
     */
    function doSearch() {
      var keyword = (input.value || "").trim();
      if (!keyword) {
        toast(t("watchlist.addNeedKeyword"));
        return;
      }
      resultHost.innerHTML = "";
      resultHost.appendChild(el("div", "pg-state", t("state.loading")));
      api("/api/market/search", { q: keyword }).then(function (data) {
        var items = data.items || [];
        resultHost.innerHTML = "";
        if (!items.length) {
          resultHost.appendChild(el("div", "pg-state", t("watchlist.addNoMatch")));
          return;
        }
        items.slice(0, 8).forEach(function (item) {
          var row = el("div", "wl-addrow");
          row.appendChild(el("b", "", item.symbol));
          row.appendChild(el("span", "wl-addrow-name", item.name || ""));
          row.appendChild(el("span", "pg-spacer"));
          var add = el("button", "pg-btn", t("watchlist.addAction"));
          add.type = "button";
          add.addEventListener("click", function () {
            add.disabled = true;
            sendJson("POST", "/api/watchlist", {
              symbol: item.symbol,
              market: item.market || "a",
              name: item.name || ""
            }).then(function () {
              toast(t("watchlist.added"));
              loadWatchlist(true);
            }).catch(function (error) {
              add.disabled = false;
              toast(error.message || t("state.errorHint"));
            });
          });
          row.appendChild(add);
          resultHost.appendChild(row);
        });
      }).catch(function (error) {
        resultHost.innerHTML = "";
        resultHost.appendChild(el("div", "pg-state", error.message || t("state.errorHint")));
      });
    }

    searchBtn.addEventListener("click", doSearch);
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        doSearch();
      }
    });
    body.appendChild(box);
    body.setAttribute("data-state", "empty");
  }

  /** W-03：渲染侧栏「自选股」分组树（我的自选 + 市场分组 + 数量徽章）。
   * 数据源与 W-02 chips 相同（counts.by_market），点击分组进入自选页并预过滤。
   * @param {Object} byMarket 市场 → 数量 映射
   * @param {number} total 自选总数
   * @returns {void} 无返回值
   */
  function renderWatchlistTree(byMarket, total) {
    var host = document.getElementById("sidebar-watchlist-groups");
    if (!host) {
      return;
    }
    host.innerHTML = "";
    if (!total) {
      return; // 空清单不渲染分组（避免 0 徽章噪音）
    }
    [
      ["a", t("watchlist.group.a")],
      ["hk", t("watchlist.group.hk")],
      ["us", t("watchlist.group.us")],
      ["fund", t("watchlist.group.fund")]
    ].forEach(function (pair) {
      var count = (byMarket || {})[pair[0]] || 0;
      if (!count) {
        return; // 无成员的组不展示
      }
      var li = el("li", "sidebar-item sidebar-sub");
      li.setAttribute("data-action", "view");
      li.setAttribute("data-view", "watchlist");
      // app.js bindSidebar 按 data-view-target 拼 hash，缺失会导致点击无响应
      li.setAttribute("data-view-target", "watchlist");
      li.dataset.group = pair[0];
      var label = el("span", "", pair[1]);
      li.appendChild(label);
      li.appendChild(el("span", "sidebar-count", String(count)));
      host.appendChild(li);
    });
  }

  function loadWatchlist(force) {
    var body1 = document.getElementById("w01-body");
    var body2 = document.getElementById("w02-body");
    if (!body1 || !body2) {
      return;
    }
    setLoading(body1);
    setLoading(body2);
    api("/api/watchlist").then(function (data) {
      var counts = data.counts || {};
      var card1 = document.getElementById("W-01");
      setBadges(card1, data.source, data.caliber);
      body1.innerHTML = "";
      var grid = el("div", "pg-metric-grid");
      [
        [t("watchlist.count"), counts.total || 0, "flat"],
        [t("watchlist.up"), counts.up || 0, "up"],
        [t("watchlist.down"), counts.down || 0, "down"],
        [t("watchlist.flat"), counts.flat || 0, "flat"],
        [t("watchlist.asOf"), String(data.as_of || "").slice(11, 19), "flat"]
      ].forEach(function (item) {
        var cell = el("div", "pg-metric-cell");
        cell.appendChild(el("div", "pg-metric-label", item[0]));
        cell.appendChild(el("div", "pg-metric-value " + item[2], String(item[1])));
        grid.appendChild(cell);
      });
      body1.appendChild(grid);
      body1.setAttribute("data-state", "default");

      var rows = data.items || [];
      var byMarket = (counts.by_market) || {};
      // W-03：侧栏自选分组树（当前市场分布 + 数量徽章），与 W-02 chips 同源
      renderWatchlistTree(byMarket, counts.total || 0);
      if (watchlistGroup) {
        rows = rows.filter(function (row) { return row.market === watchlistGroup; });
      }
      if (!rows.length) {
        if (watchlistGroup) {
          setEmpty(body2, t("watchlist.groupEmpty"), function () {
            watchlistGroup = "";
            loadWatchlist(true);
          });
          return;
        }
        // R6-09 / F6-09：空态给「搜索框直接加自选」，不只让用户去个股页
        paintWatchlistEmpty(body2, data.empty_note || t("watchlist.empty"));
        return;
      }
      renderTable(body2, [
        {
          title: t("watchlist.col.name"), cls: "l",
          /** 名称列：代码加粗 + 名称。
           * @param {Object} row 自选行
           * @returns {HTMLElement} 单元格节点
           */
          render: function (row) {
            var box = el("span", "");
            box.appendChild(el("b", "", row.symbol));
            box.appendChild(document.createTextNode(" " + (row.name || "")));
            return box;
          }
        },
        { title: t("watchlist.col.market"), cls: "l", render: function (row) { return marketLabel(row.market); } },
        { title: t("watchlist.col.price"), num: true, render: function (row) { return fmt((row.quote || {}).price, 2); } },
        { title: t("watchlist.col.change"), num: true, render: function (row) { var q = row.quote || {}; return el("span", pctClass(q.change), fmt(q.change, 2)); } },
        { title: t("watchlist.col.pct"), num: true, render: function (row) { var q = row.quote || {}; return el("span", pctClass(q.pct), fmtPct(q.pct)); } },
        { title: t("watchlist.col.amount"), num: true, render: function (row) { return fmtAmount((row.quote || {}).amount); } },
        { title: t("watchlist.col.pe"), num: true, render: function (row) { return fmt((row.quote || {}).pe_ttm, 2); } },
        {
          title: "", cls: "l",
          /** 操作列：移除按钮（二次确认后 DELETE）。
           * @param {Object} row 自选行
           * @returns {HTMLElement} 单元格节点
           */
          render: function (row) {
            var btn = el("button", "pg-btn", t("watchlist.remove"));
            btn.type = "button";
            btn.addEventListener("click", function (event) {
              event.stopPropagation();
              if (!window.confirm(t("watchlist.removeConfirm"))) {
                return;
              }
              sendJson("DELETE", "/api/watchlist/" + encodeURIComponent(row.symbol)
                + "?market=" + encodeURIComponent(row.market)).then(function () {
                toast(t("watchlist.removed"));
                loadWatchlist(true);
              }).catch(function (error) {
                toast(error.message || t("state.errorHint"));
              });
            });
            return btn;
          }
        }
      ], rows, function (row) {
        if (row.market === "fund") {
          window.location.hash = "#/fund?code=" + row.symbol;
        } else {
          window.location.hash = "#/stock?symbol=" + row.symbol + "&market=" + row.market;
        }
      }, null, {
        // R6-09 / F6-09：勾选列 + 表头全选，变化后重绘批量栏
        key: function (row) { return row.symbol + "@" + row.market; },
        selected: watchSelected,
        onChange: function (keys) {
          watchSelected = keys;
          paintWatchBulkBar();
        }
      });
      // R6-09：批量栏插到表格上方（renderTable 会清空容器，故在其后 insertBefore）
      watchRows = rows;
      watchBulkHost = el("div", "wl-bulk");
      watchBulkHost.hidden = true;
      body2.insertBefore(watchBulkHost, body2.firstChild);
      paintWatchBulkBar();
      // W-02：分组筛选 chips 必须在 renderTable 之后追加——renderTable 会清空容器
      var chips = el("div", "pg-tabs");
      [
        ["", t("watchlist.group.all"), counts.total || 0],
        ["a", t("watchlist.group.a"), byMarket.a || 0],
        ["hk", t("watchlist.group.hk"), byMarket.hk || 0],
        ["us", t("watchlist.group.us"), byMarket.us || 0],
        ["fund", t("watchlist.group.fund"), byMarket.fund || 0]
      ].forEach(function (chip) {
        var btn = el("button", "pg-tab" + (watchlistGroup === chip[0] ? " active" : ""),
          chip[1] + "（" + chip[2] + "）");
        btn.type = "button";
        btn.addEventListener("click", function () {
          watchlistGroup = chip[0];
          loadWatchlist(true);
        });
        chips.appendChild(btn);
      });
      body2.appendChild(chips);
    }).catch(function (error) {
      setError(body1, error.message || t("state.errorHint"), function () { loadWatchlist(true); });
      setError(body2, error.message || t("state.errorHint"), function () { loadWatchlist(true); });
    });
  }

  /** 渲染龙虎榜·北向页（L-01 图 + L-02 Top10 + L-03 明细 + L-04 机构席位 + L-05 营业部）。
   */
  function renderLhb() {
    var page = document.getElementById("page-lhb");
    if (!page) {
      return;
    }
    page.innerHTML = "";
    var grid = el("div", "pg-grid");
    var l1 = buildCard("L-01", t("lhb.northTitle"), "g-7", t("lhb.northSub"));
    l1.body.id = "l01-body";
    grid.appendChild(l1.card);
    var l2 = buildCard("L-02", t("lhb.topTitle"), "g-5", t("lhb.topSub"));
    l2.body.id = "l02-body";
    grid.appendChild(l2.card);
    var l3 = buildCard("L-03", t("lhb.detailTitle"), "g-12", t("lhb.detailSub"));
    l3.body.id = "l03-body";
    grid.appendChild(l3.card);
    // R3-18：机构席位统计（stock_lhb_jgmmtj_em）与活跃营业部（stock_lhb_hyyyb_em）
    var l4 = buildCard("L-04", t("lhb.instTitle"), "g-7", t("lhb.instSub"));
    l4.body.id = "l04-body";
    grid.appendChild(l4.card);
    var l5 = buildCard("L-05", t("lhb.brokerTitle"), "g-5", t("lhb.brokerSub"));
    l5.body.id = "l05-body";
    grid.appendChild(l5.card);
    page.appendChild(grid);
    loadNorthbound();
    loadNorthTop();
    loadLhb();
    loadLhbInstitution();
    loadLhbBroker();
  }

  /** L-04：龙虎榜机构席位统计（按票汇总买方/卖方机构数与净买额）。
   * @returns {void} 无返回值（异步渲染）
   */
  function loadLhbInstitution() {
    var body = document.getElementById("l04-body");
    if (!body) {
      return;
    }
    setLoading(body);
    api("/api/moneyflow/lhb-institution", { limit: 50 }).then(function (data) {
      var card = document.getElementById("L-04");
      // 双徽章：徽章1=源（含 akshare 函数名），徽章2=口径（含统计区间）
      setBadges(card, data.source, data.caliber);
      var items = data.items || [];
      if (!items.length) {
        setEmpty(body, data.note || t("lhb.instEmpty"), function () { loadLhbInstitution(); });
        return;
      }
      renderTable(body, [
        {
          title: t("watchlist.col.name"), cls: "l",
          /** 名称列：代码加粗 + 名称。
           * @param {Object} row 数据行
           * @returns {HTMLElement} 单元格节点
           */
          render: function (row) {
            var box = el("span", "");
            box.appendChild(el("b", "", row.code));
            box.appendChild(document.createTextNode(" " + (row.name || "")));
            return box;
          }
        },
        { title: t("lhb.col.pct"), num: true, render: function (row) { return el("span", pctClass(row.pct), fmtPct(row.pct)); } },
        { title: t("lhb.col.buyOrgs"), num: true, render: function (row) { return fmt(row.buy_orgs, 0); } },
        { title: t("lhb.col.sellOrgs"), num: true, render: function (row) { return fmt(row.sell_orgs, 0); } },
        { title: t("lhb.col.netBuy"), num: true, render: function (row) { return el("span", pctClass(row.net_buy), fmt(row.net_buy, 2)); } },
        { title: t("lhb.col.ratio"), num: true, render: function (row) { return row.ratio === null || row.ratio === undefined ? t("value.none") : fmt(row.ratio, 2) + "%"; } },
        {
          title: t("lhb.col.reason"), cls: "l",
          /** 上榜原因：过长时截断由 CSS 处理，title 保完整。
           * @param {Object} row 数据行
           * @returns {HTMLElement} 单元格节点
           */
          render: function (row) {
            var text = row.reason || t("value.none");
            var node = el("span", "", text);
            node.title = text;
            return node;
          }
        }
      ], items, function (row) {
        window.location.hash = "#/stock?symbol=" + row.code + "&market=a";
      });
    }).catch(function (error) {
      setError(body, error.message || t("state.errorHint"), function () { loadLhbInstitution(); });
    });
  }

  /** L-05：龙虎榜活跃营业部（按营业部汇总买卖金额与个股数）。
   * @returns {void} 无返回值（异步渲染）
   */
  function loadLhbBroker() {
    var body = document.getElementById("l05-body");
    if (!body) {
      return;
    }
    setLoading(body);
    api("/api/moneyflow/lhb-broker", { limit: 50 }).then(function (data) {
      var card = document.getElementById("L-05");
      setBadges(card, data.source, data.caliber);
      var items = data.items || [];
      if (!items.length) {
        setEmpty(body, data.note || t("lhb.brokerEmpty"), function () { loadLhbBroker(); });
        return;
      }
      renderTable(body, [
        {
          title: t("lhb.col.broker"), cls: "l",
          /** 营业部名：过长截断，悬停看全称。
           * @param {Object} row 数据行
           * @returns {HTMLElement} 单元格节点
           */
          render: function (row) {
            var node = el("span", "", row.name || t("value.none"));
            node.title = row.name || "";
            return node;
          }
        },
        { title: t("lhb.col.buyCount"), num: true, render: function (row) { return fmt(row.buy_count, 0); } },
        { title: t("lhb.col.sellCount"), num: true, render: function (row) { return fmt(row.sell_count, 0); } },
        { title: t("lhb.col.buy"), num: true, render: function (row) { return fmt(row.buy_amount, 2); } },
        { title: t("lhb.col.sell"), num: true, render: function (row) { return fmt(row.sell_amount, 2); } },
        { title: t("lhb.col.netBuy"), num: true, render: function (row) { return el("span", pctClass(row.net_buy), fmt(row.net_buy, 2)); } }
      ], items, null);
    }).catch(function (error) {
      setError(body, error.message || t("state.errorHint"), function () { loadLhbBroker(); });
    });
  }

  /** L-01：北向资金近 20 个有披露交易日净流入柱状图。
   * @returns {void} 无返回值（异步渲染）
   */
  function loadNorthbound() {
    var body = document.getElementById("l01-body");
    if (!body) {
      return;
    }
    setLoading(body);
    api("/api/moneyflow/northbound", { days: 20 }).then(function (data) {
      var card = document.getElementById("L-01");
      setBadges(card, data.source, data.caliber);
      var items = data.items || [];
      if (!items.length) {
        setEmpty(body, data.note || t("state.empty"), function () { loadNorthbound(); });
        return;
      }
      body.innerHTML = "";
      if (data.note) {
        body.appendChild(el("div", "pg-card-sub", data.note));
      }
      var node = el("div", "pg-chart");
      node.style.height = "300px";
      body.appendChild(node);
      var chart = mountChart(node);
      if (chart) {
        var theme = chartTheme();
        chart.setOption({
          grid: { left: 48, right: 16, top: 24, bottom: 32 },
          tooltip: { trigger: "axis" },
          xAxis: {
            type: "category",
            data: items.map(function (item) { return String(item.date).slice(5); }),
            axisLabel: { color: theme.text }
          },
          yAxis: {
            type: "value",
            axisLabel: { color: theme.text }, splitLine: { lineStyle: { color: theme.border } }
          },
          series: [{
            type: "bar",
            data: items.map(function (item) {
              return { value: item.net, itemStyle: { color: item.net >= 0 ? COLOR_UP : COLOR_DOWN } };
            })
          }]
        });
      }
      body.setAttribute("data-state", "default");
    }).catch(function (error) {
      setError(body, error.message || t("state.errorHint"), function () { loadNorthbound(); });
    });
  }

  /** L-02：北向个股净买入 Top10（无当日数据时空态 + 说明）。
   * @returns {void} 无返回值（异步渲染）
   */
  function loadNorthTop() {
    var body = document.getElementById("l02-body");
    if (!body) {
      return;
    }
    setLoading(body);
    api("/api/moneyflow/north-top", { limit: 10 }).then(function (data) {
      var card = document.getElementById("L-02");
      setBadges(card, data.source, data.caliber);
      var items = data.items || [];
      if (!items.length) {
        setEmpty(body, data.note || t("state.empty"), function () { loadNorthTop(); });
        return;
      }
      renderTable(body, [
        {
          title: t("watchlist.col.name"), cls: "l",
          /** 名称列：代码加粗 + 名称（北向/龙虎榜通用）。
           * @param {Object} row 数据行
           * @returns {HTMLElement} 单元格节点
           */
          render: function (row) {
            var box = el("span", "");
            box.appendChild(el("b", "", row.code));
            box.appendChild(document.createTextNode(" " + (row.name || "")));
            return box;
          }
        },
        { title: t("lhb.col.netBuy"), num: true, render: function (row) { return el("span", pctClass(row.net), fmt(row.net, 2)); } }
      ], items, function (row) {
        window.location.hash = "#/stock?symbol=" + row.code + "&market=a";
      });
    }).catch(function (error) {
      setError(body, error.message || t("state.errorHint"), function () { loadNorthTop(); });
    });
  }

  /** L-03：龙虎榜当日明细（默认最近披露日）。
   * @returns {void} 无返回值（异步渲染）
   */
  function loadLhb() {
    var body = document.getElementById("l03-body");
    if (!body) {
      return;
    }
    setLoading(body);
    api("/api/moneyflow/lhb", { limit: 100 }).then(function (data) {
      var card = document.getElementById("L-03");
      setBadges(card, data.source, data.caliber);
      var items = data.items || [];
      if (!items.length) {
        setEmpty(body, data.note || t("lhb.empty"), function () { loadLhb(); });
        return;
      }
      renderTable(body, [
        { title: t("lhb.col.date"), render: function (row) { return row.date; } },
        {
          title: t("watchlist.col.name"), cls: "l",
          /** 名称列：代码加粗 + 名称（北向/龙虎榜通用）。
           * @param {Object} row 数据行
           * @returns {HTMLElement} 单元格节点
           */
          render: function (row) {
            var box = el("span", "");
            box.appendChild(el("b", "", row.code));
            box.appendChild(document.createTextNode(" " + (row.name || "")));
            return box;
          }
        },
        { title: t("lhb.col.reason"), cls: "l", render: function (row) { return row.reason || t("value.none"); } },
        { title: t("lhb.col.pct"), num: true, render: function (row) { return el("span", pctClass(row.pct), fmtPct(row.pct)); } },
        { title: t("lhb.col.netBuy"), num: true, render: function (row) { return el("span", pctClass(row.net_buy), fmt(row.net_buy, 2)); } },
        { title: t("lhb.col.buy"), num: true, render: function (row) { return fmt(row.buy, 2); } },
        { title: t("lhb.col.sell"), num: true, render: function (row) { return fmt(row.sell, 2); } }
      ], items, function (row) {
        window.location.hash = "#/stock?symbol=" + row.code + "&market=a";
      });
    }).catch(function (error) {
      setError(body, error.message || t("state.errorHint"), function () { loadLhb(); });
    });
  }

  // 供工具栏「刷新当前视图」调用：按当前 hash 重新走一遍路由（P2-03）
  window.Pages = { reload: route };

  // =============================================================
  // 七、#/screener 全市场筛选器（第七轮 M9）
  // 契约：GET /api/screener/status（快照元信息+构建进度）
  //       POST /api/screener/run（条件查询，本地快照 <1s）
  //       POST /api/screener/refresh（异步构建，进度轮询 status）
  // =============================================================
  // 因子下拉数据（与后端 FACTOR_META 同源；label/unit 手工同步）
  var SCREENER_FACTORS = [
    ["pe", "PE-TTM", "倍", "quote"],
    ["pb", "PB-MRQ", "倍", "quote"],
    ["ps", "PS-TTM", "倍", "quote"],
    ["pcf", "经营现金流市值比", "倍", "quote"],
    ["chg", "涨跌幅", "%", "quote"],
    ["mktcap", "总市值", "亿元", "quote"],
    ["roe_min", "ROE（近3年年报均≥）", "%", "finance"],
    ["gross", "销售毛利率", "%", "finance"],
    ["rev_yoy", "营收同比", "%", "finance"],
    ["np_yoy", "净利同比", "%", "finance"],
    ["debt_ratio", "资产负债率", "%", "finance"],
    ["ocf_yi", "经营现金流净额", "亿元", "finance"]
  ];
  // 结果表列（后端 items 字段 → 列定义；红涨绿跌走 pctClass）
  var SCREENER_COLS = [
    { key: "code", title: "代码", render: function (row) {
        var box = el("span", "");
        box.appendChild(el("b", "", row.code));
        box.appendChild(document.createTextNode(" " + (row.name || "")));
        return box;
      } },
    { key: "board", title: "板块" },
    { key: "close", title: "现价", num: true, render: function (row) { return fmt(row.close, 2); } },
    { key: "chg", title: "涨跌幅", num: true, render: function (row) { return el("span", pctClass(row.chg), fmtPct(row.chg)); } },
    { key: "pe", title: "PE", num: true, render: function (row) { return fmt(row.pe, 2); } },
    { key: "pb", title: "PB", num: true, render: function (row) { return fmt(row.pb, 2); } },
    { key: "mktcap", title: "总市值(亿)", num: true, render: function (row) { return fmt(row.mktcap, 0); } },
    { key: "roe3y_min", title: "ROE3年最弱", num: true, render: function (row) { return fmt(row.roe3y_min, 2); } },
    { key: "gross", title: "毛利率", num: true, render: function (row) { return fmt(row.gross, 2); } },
    { key: "rev_yoy", title: "营收同比", num: true, render: function (row) { return fmt(row.rev_yoy, 2); } },
    { key: "np_yoy", title: "净利同比", num: true, render: function (row) { return fmt(row.np_yoy, 2); } },
    { key: "debt_ratio", title: "资产负债率", num: true, render: function (row) { return fmt(row.debt_ratio, 2); } },
    { key: "ocf_yi", title: "经营现金流(亿)", num: true, render: function (row) { return fmt(row.ocf_yi, 2); } },
    { key: "exempt", title: "豁免", render: function (row) {
        return (row.exempt && row.exempt.length) ? t("scr.exemptYes") : "";
      } }
  ];

  /** 渲染筛选器页：快照状态卡 + 条件构建器 + 结果表。
   *
   * @returns {void} 无返回值（异步渲染）
   */
  function renderScreener() {
    var page = document.getElementById(VIEW_IDS.screener);
    if (!page) {
      return;
    }
    page.innerHTML = "";
    var grid = el("div", "pg-grid");

    // M9-01 快照状态卡
    var snapCard = buildCard("M9-01", t("scr.snapTitle"), "g-5", t("scr.snapSub"));
    snapCard.body.id = "m901-body";
    grid.appendChild(snapCard.card);

    // M9-02 条件构建器
    var condCard = buildCard("M9-02", t("scr.condTitle"), "g-7", t("scr.condSub"));
    condCard.body.id = "m902-body";
    grid.appendChild(condCard.card);

    page.appendChild(grid);

    // M9-03 结果表（全宽卡，栅格外）
    var resultCard = buildCard("M9-03", t("scr.resultTitle"), "", t("scr.resultSub"));
    resultCard.card.classList.add("wide");
    resultCard.body.id = "m903-body";
    page.appendChild(resultCard.card);

    paintScreenerStatus();
    paintScreenerConditions();
    paintScreenerResult(state.screener.lastResult); // 有缓存直接回填
    page.setAttribute("data-state", "default");
  }

  /** 快照状态卡：ready/版本/覆盖数/构建进度/自动构建窗口。 */
  function paintScreenerStatus() {
    var body = document.getElementById("m901-body");
    if (!body) {
      return;
    }
    body.innerHTML = "";
    body.appendChild(el("div", "pg-state", t("state.loading")));
    api("/api/screener/status", {}).then(function (data) {
      body.innerHTML = "";
      var card = document.getElementById("M9-01");
      if (card) {
        setBadges(card, t("scr.snapSource"), data.caliber || "");
      }
      var kpis = el("div", "pg-kpis");
      var main = el("div", "pg-kpi");
      main.appendChild(el("div", "pg-kpi-k", t("scr.snapState")));
      var readyText = data.ready ? t("scr.snapReady") : (data.building ? t("scr.snapBuilding") : t("scr.snapNotReady"));
      main.appendChild(el("div", "pg-kpi-v " + (data.ready ? "down" : "flat"), readyText));
      main.appendChild(el("div", "pg-kpi-x", (data.building ? t("scr.buildingHint") : t("scr.autoHint"))));
      kpis.appendChild(main);
      var rows = el("div", "pg-kpi");
      rows.appendChild(el("div", "pg-kpi-k", t("scr.snapRows")));
      rows.appendChild(el("div", "pg-kpi-v flat", data.ready ? fmt(data.row_count, 0) : "—"));
      rows.appendChild(el("div", "pg-kpi-x", data.trade_date ? (t("scr.tradeDate") + data.trade_date) : ""));
      kpis.appendChild(rows);
      body.appendChild(kpis);
      if (data.version) {
        body.appendChild(el("div", "pg-card-sub", t("scr.versionLabel") + data.version
          + (data.built_at ? " · " + data.built_at : "")));
      }
      if (data.last_error) {
        body.appendChild(el("div", "pg-card-sub", t("scr.lastError") + data.last_error));
      }

      var btnRow = el("div", "page-toolbar");
      var btn = el("button", "pg-btn primary", data.building ? t("scr.buildingBtn") : t("scr.refreshBtn"));
      btn.type = "button";
      btn.disabled = !!data.building;
      btn.addEventListener("click", function () {
        api("/api/screener/refresh", {}).then(function () {
          toast(t("scr.startedToast"));
          paintScreenerStatus(); // 回到 status 轮询进度
        }).catch(function (error) {
          toast(error.message || t("state.errorHint"));
        });
      });
      btnRow.appendChild(btn);
      btnRow.appendChild(el("span", "pg-card-sub", t("scr.refreshNote")));
      body.appendChild(btnRow);

      // 构建中：8s 轮询进度直到完成
      if (data.building) {
        window.setTimeout(function () {
          if (document.getElementById("page-screener") && !document.getElementById("page-screener").hidden) {
            paintScreenerStatus();
          }
        }, 8000);
      }
      body.setAttribute("data-state", "default");
    }).catch(function (error) {
      setError(body, error.message || t("state.errorHint"), paintScreenerStatus);
      body.setAttribute("data-state", "error");
    });
  }

  /** 条件构建器：最多 8 行 [因子 ▾][≥/≤ ▾][阈值][×]，AND 连接；「执行筛选」走 run。 */
  function paintScreenerConditions() {
    var body = document.getElementById("m902-body");
    if (!body) {
      return;
    }
    body.innerHTML = "";
    var list = el("div", "scr-cond-list");
    body.appendChild(list);
    var saved = (state.screener.conditions || []).slice();

    /** 生成一行条件编辑器。
     * @param {Object} preset 预填条件 {factor, op, value}
     * @returns {void} 无返回值
     */
    function addRow(preset) {
      if (list.children.length >= 8) {
        toast(t("scr.maxConds"));
        return;
      }
      var row = el("div", "scr-cond");
      var factor = document.createElement("select");
      factor.className = "scr-cond-factor";
      SCREENER_FACTORS.forEach(function (item) {
        var option = document.createElement("option");
        option.value = item[0];
        option.textContent = item[1] + "（" + item[2] + "）";
        factor.appendChild(option);
      });
      factor.value = (preset && preset.factor) || "pe";
      var op = document.createElement("select");
      op.className = "scr-cond-op";
      [["gte", "≥"], ["lte", "≤"]].forEach(function (pair) {
        var option = document.createElement("option");
        option.value = pair[0];
        option.textContent = pair[1];
        op.appendChild(option);
      });
      op.value = (preset && preset.op) || "gte";
      var value = document.createElement("input");
      value.type = "number";
      value.step = "any";
      value.className = "scr-cond-value";
      value.placeholder = t("scr.valuePlaceholder");
      if (preset && preset.value !== undefined && preset.value !== null) {
        value.value = String(preset.value);
      }
      var remove = el("button", "pg-btn", "×");
      remove.type = "button";
      remove.title = t("scr.removeCond");
      remove.addEventListener("click", function () {
        row.remove();
        syncDraft();
      });
      [factor, op, value].forEach(function (node) {
        node.addEventListener("change", syncDraft);
        node.addEventListener("input", syncDraft);
      });
      row.appendChild(factor);
      row.appendChild(op);
      row.appendChild(value);
      row.appendChild(remove);
      list.appendChild(row);
    }

    /** 从 DOM 收集当前条件（丢弃缺值行）。 */
    function collect() {
      var conditions = [];
      list.querySelectorAll(".scr-cond").forEach(function (row) {
        var factor = row.querySelector(".scr-cond-factor").value;
        var op = row.querySelector(".scr-cond-op").value;
        var raw = row.querySelector(".scr-cond-value").value;
        if (raw === "" || raw === null || isNaN(Number(raw))) {
          return; // 未填阈值的行不参与
        }
        conditions.push({ factor: factor, op: op, value: Number(raw) });
      });
      return conditions;
    }

    /** 条件变化时同步草稿（Tab 切回保留）。 */
    function syncDraft() {
      state.screener.conditions = collect();
    }

    (saved.length ? saved : [{ factor: "pe", op: "lte", value: 30 }]).forEach(addRow);

    var bar = el("div", "page-toolbar");
    var addBtn = el("button", "pg-btn", t("scr.addCond"));
    addBtn.type = "button";
    addBtn.addEventListener("click", function () { addRow(); });
    var runBtn = el("button", "pg-btn primary", t("scr.runBtn"));
    runBtn.type = "button";
    var result = document.getElementById("m903-body");

    runBtn.addEventListener("click", function () {
      var conditions = collect();
      state.screener.conditions = conditions;
      if (!conditions.length) {
        toast(t("scr.needCond"));
        return;
      }
      if (!result) {
        return;
      }
      result.innerHTML = "";
      result.appendChild(el("div", "pg-state", t("scr.running")));
      result.setAttribute("data-state", "loading");
      runScreenerPost(conditions).then(function (data) {
        state.screener.lastResult = data;
        paintScreenerResult(data);
      }).catch(function (error) {
        result.innerHTML = "";
        result.appendChild(el("div", "pg-state", error.message || t("state.errorHint")));
        result.setAttribute("data-state", "error");
      });
    });

    bar.appendChild(addBtn);
    bar.appendChild(runBtn);
    bar.appendChild(el("span", "pg-card-sub", t("scr.andNote")));
    body.appendChild(bar);
    body.setAttribute("data-state", "default");
  }

  /** POST /api/screener/run（utils.api 只发 GET，这里封装 JSON POST）。 */
  function runScreenerPost(conditions) {
    return window.fetch("/api/screener/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conditions: conditions, limit: 200 })
    }).then(function (response) {
      if (!response.ok) {
        throw new Error("请求失败（HTTP " + response.status + "）");
      }
      return response.json();
    }).then(function (body) {
      if (body && body.error) {
        var error = new Error(body.error);
        error.code = body.error_code;
        throw error;
      }
      return body.data || {};
    });
  }

  /** 结果表渲染：命中数 + 截断说明 + 快照口径 + 豁免标注；空结果走空态。 */
  function paintScreenerResult(data) {
    var body = document.getElementById("m903-body");
    if (!body) {
      return;
    }
    if (!data) {
      body.innerHTML = "";
      body.appendChild(el("div", "pg-state", t("scr.resultIdle")));
      body.setAttribute("data-state", "default");
      return;
    }
    body.innerHTML = "";
    var card = document.getElementById("M9-03");
    if (card && data.snapshot) {
      setBadges(card, t("scr.snapSource"),
        t("scr.resultCaliber", { count: fmt(data.snapshot.row_count, 0), date: data.snapshot.trade_date || "" }));
    }
    var summary = el("div", "pg-kpis");
    var kpi = el("div", "pg-kpi");
    kpi.appendChild(el("div", "pg-kpi-k", t("scr.matched")));
    kpi.appendChild(el("div", "pg-kpi-v " + (data.total_matched ? "up" : "flat"), fmt(data.total_matched, 0)));
    kpi.appendChild(el("div", "pg-kpi-x",
      (data.exempt_count ? t("scr.exemptCount", { count: data.exempt_count }) : "")));
    summary.appendChild(kpi);
    body.appendChild(summary);

    var items = data.items || [];
    if (!items.length) {
      body.appendChild(el("div", "pg-state", t("scr.emptyResult")));
      body.appendChild(el("div", "pg-card-sub", t("scr.emptyHint")));
      body.setAttribute("data-state", "empty");
      return;
    }
    var table = el("div", "table-scroll");
    renderTable(table, SCREENER_COLS, items, {
      onRowClick: function (row) {
        window.location.hash = "#/stock?symbol=" + row.code + "&market=a";
      }
    });
    body.appendChild(table);
    if (data.truncated) {
      body.appendChild(el("div", "pg-card-sub", t("scr.truncated")));
    }
    body.setAttribute("data-state", "default");
  }

  // =============================================================
  // 八、#/alerts 提醒中心（第七轮 M6）
  // 契约：GET/POST /api/alerts（规则列表/新增）
  //       PATCH/DELETE /api/alerts/{id}
  //       GET /api/alerts/events、POST /api/alerts/events/read
  //       POST /api/alerts/evaluate?force=
  // 规则六类：price/pct_change/valuation/crowding/announcement/report_date
  // =============================================================
  // 需要数值阈值的规则类型（announcement/report_date 只要 target）
  var ALERT_VALUE_TYPES = { price: 1, pct_change: 1, valuation: 1, crowding: 1 };

  /** 渲染提醒中心：事件列表 + 规则管理（新增表单/启停/删除）+ 手动评估。 */
  function renderAlerts() {
    var page = document.getElementById(VIEW_IDS.alerts);
    if (!page) {
      return;
    }
    page.innerHTML = "";
    var grid = el("div", "pg-grid");

    var eventCard = buildCard("M6-01", t("alr.eventTitle"), "g-7", t("alr.eventSub"));
    eventCard.body.id = "m601-body";
    grid.appendChild(eventCard.card);

    var ruleCard = buildCard("M6-02", t("alr.ruleTitle"), "g-5", t("alr.ruleSub"));
    ruleCard.body.id = "m602-body";
    grid.appendChild(ruleCard.card);

    page.appendChild(grid);
    paintAlertsEvents();
    paintAlertsRules();
    page.setAttribute("data-state", "default");
  }

  /** 事件列表：未读徽标 + 全部已读 + 单条已读 + 只看未读开关。 */
  function paintAlertsEvents() {
    var body = document.getElementById("m601-body");
    if (!body) {
      return;
    }
    body.innerHTML = "";
    body.appendChild(el("div", "pg-state", t("state.loading")));
    api("/api/alerts/events", { unread_only: state.alerts.unreadOnly ? "true" : "" }).then(function (data) {
      body.innerHTML = "";
      updateBell(data.unread || 0);
      var card = document.getElementById("M6-01");
      if (card) {
        setBadges(card, t("alr.eventSource"), t("alr.dedupNote"));
      }
      var bar = el("div", "page-toolbar");
      var unreadBtn = el("button", "pg-btn" + (state.alerts.unreadOnly ? " primary" : ""),
        t("alr.unreadOnlyBtn") + "（" + fmt(data.unread || 0, 0) + "）");
      unreadBtn.type = "button";
      unreadBtn.addEventListener("click", function () {
        state.alerts.unreadOnly = !state.alerts.unreadOnly;
        paintAlertsEvents();
      });
      var readAllBtn = el("button", "pg-btn", t("alr.readAllBtn"));
      readAllBtn.type = "button";
      readAllBtn.disabled = !(data.unread > 0);
      readAllBtn.addEventListener("click", function () {
        alertMarkRead("").then(function (result) {
          toast(t("alr.markedToast", { count: result.marked || 0 }));
          paintAlertsEvents();
        }).catch(function (error) {
          toast(error.message || t("state.errorHint"));
        });
      });
      bar.appendChild(unreadBtn);
      bar.appendChild(readAllBtn);
      body.appendChild(bar);

      var events = data.events || [];
      if (!events.length) {
        body.appendChild(el("div", "pg-state", t("alr.emptyEvents")));
        body.setAttribute("data-state", "empty");
        return;
      }
      var list = el("div", "alr-list");
      events.forEach(function (item) {
        var line = el("div", "alr-item" + (item.read ? "" : " unread"));
        line.appendChild(el("i", item.read ? "dot" : "dot-w"));
        var main = el("div", "alr-main");
        main.appendChild(el("span", "alr-type", item.type_label || ""));
        main.appendChild(el("span", "alr-target", item.target || ""));
        main.appendChild(el("div", "alr-msg", item.message || ""));
        line.appendChild(main);
        line.appendChild(el("span", "alr-time", item.time || ""));
        if (!item.read) {
          var readBtn = el("button", "pg-btn", t("alr.readBtn"));
          readBtn.type = "button";
          readBtn.addEventListener("click", function (event) {
            event.stopPropagation();
            alertMarkRead(item.id).then(function () {
              paintAlertsEvents();
            }).catch(function (error) {
              toast(error.message || t("state.errorHint"));
            });
          });
          line.appendChild(readBtn);
        }
        list.appendChild(line);
      });
      body.appendChild(list);
      body.setAttribute("data-state", "default");
    }).catch(function (error) {
      setError(body, error.message || t("state.errorHint"), paintAlertsEvents);
      body.setAttribute("data-state", "error");
    });
  }

  /** POST /api/alerts/events/read（event_id 空串=全部已读）。 */
  function alertMarkRead(eventId) {
    return window.fetch(
      "/api/alerts/events/read" + (eventId ? "?event_id=" + encodeURIComponent(eventId) : ""),
      { method: "POST" }
    ).then(function (response) {
      if (!response.ok) {
        throw new Error("请求失败（HTTP " + response.status + "）");
      }
      return response.json();
    }).then(function (body) {
      if (body && body.error) {
        throw new Error(body.error);
      }
      return body.data || {};
    });
  }

  /** 规则管理：新增表单（六类规则）+ 规则表（启停/删除/立即评估）。 */
  function paintAlertsRules() {
    var body = document.getElementById("m602-body");
    if (!body) {
      return;
    }
    body.innerHTML = "";
    api("/api/alerts", {}).then(function (data) {
      body.innerHTML = "";
      updateBell(data.unread || 0);
      var labels = data.type_labels || {};

      // —— 新增表单 ——
      var form = el("div", "alr-form");
      var type = document.createElement("select");
      type.className = "alr-input";
      Object.keys(labels).forEach(function (key) {
        var option = document.createElement("option");
        option.value = key;
        option.textContent = labels[key];
        type.appendChild(option);
      });
      type.value = state.alerts.draftType || "price";
      var target = document.createElement("input");
      target.type = "text";
      target.className = "alr-input";
      target.placeholder = t("alr.targetPlaceholder");
      var op = document.createElement("select");
      op.className = "alr-input";
      [["gte", "≥"], ["lte", "≤"]].forEach(function (pair) {
        var option = document.createElement("option");
        option.value = pair[0];
        option.textContent = pair[1];
        op.appendChild(option);
      });
      var value = document.createElement("input");
      value.type = "number";
      value.step = "any";
      value.className = "alr-input";
      value.placeholder = t("scr.valuePlaceholder");
      var note = document.createElement("input");
      note.type = "text";
      note.className = "alr-input";
      note.placeholder = t("alr.notePlaceholder");
      var addBtn = el("button", "pg-btn primary", t("alr.addBtn"));
      addBtn.type = "button";

      /** 比较类规则才显示 op/value（announcement/report_date 只要目标）。 */
      function syncFormVisibility() {
        var needValue = !!ALERT_VALUE_TYPES[type.value];
        op.style.display = needValue ? "" : "none";
        value.style.display = needValue ? "" : "none";
      }
      type.addEventListener("change", function () {
        state.alerts.draftType = type.value;
        syncFormVisibility();
      });
      addBtn.addEventListener("click", function () {
        var payload = {
          type: type.value,
          target: (target.value || "").trim(),
          note: note.value || ""
        };
        if (!payload.target) {
          toast(t("alr.needTarget"));
          return;
        }
        if (ALERT_VALUE_TYPES[type.value]) {
          payload.op = op.value;
          if (value.value === "" || isNaN(Number(value.value))) {
            toast(t("alr.needValue"));
            return;
          }
          payload.value = Number(value.value);
        }
        alertAddRule(payload).then(function () {
          toast(t("alr.addedToast"));
          target.value = "";
          value.value = "";
          note.value = "";
          paintAlertsRules();
        }).catch(function (error) {
          toast(error.message || t("state.errorHint"));
        });
      });
      [type, target, op, value, note].forEach(function (node) { form.appendChild(node); });
      form.appendChild(addBtn);
      body.appendChild(form);
      syncFormVisibility();

      // —— 规则列表 ——
      var rules = data.rules || [];
      if (!rules.length) {
        body.appendChild(el("div", "pg-state", t("alr.emptyRules")));
        body.setAttribute("data-state", "empty");
        return;
      }
      var list = el("div", "alr-list");
      rules.forEach(function (rule) {
        var line = el("div", "alr-item rule" + (rule.enabled ? "" : " disabled"));
        line.appendChild(el("i", rule.enabled ? "dot" : "dot"));
        var main = el("div", "alr-main");
        main.appendChild(el("span", "alr-type", rule.type_label || labels[rule.type] || rule.type));
        main.appendChild(el("span", "alr-target", rule.target || ""));
        var condText = (rule.op === "gte" ? "≥ " : rule.op === "lte" ? "≤ " : "")
          + (rule.value !== undefined && rule.value !== null ? fmt(rule.value, 2) : "");
        main.appendChild(el("div", "alr-msg", condText + (rule.note ? " · " + rule.note : "")));
        line.appendChild(main);
        var toggle = el("button", "pg-btn", rule.enabled ? t("alr.disableBtn") : t("alr.enableBtn"));
        toggle.type = "button";
        toggle.addEventListener("click", function () {
          alertPatchRule(rule.id, { enabled: !rule.enabled }).then(function () {
            paintAlertsRules();
          }).catch(function (error) {
            toast(error.message || t("state.errorHint"));
          });
        });
        var remove = el("button", "pg-btn", t("alr.deleteBtn"));
        remove.type = "button";
        remove.addEventListener("click", function () {
          alertDeleteRule(rule.id).then(function () {
            toast(t("alr.deletedToast"));
            paintAlertsRules();
          }).catch(function (error) {
            toast(error.message || t("state.errorHint"));
          });
        });
        line.appendChild(toggle);
        line.appendChild(remove);
        list.appendChild(line);
      });
      body.appendChild(list);

      // —— 评估说明 + 手动立即评估 ——
      var footBar = el("div", "page-toolbar");
      var evalBtn = el("button", "pg-btn", t("alr.evalNowBtn"));
      evalBtn.type = "button";
      evalBtn.addEventListener("click", function () {
        evalBtn.disabled = true;
        alertEvaluate(true).then(function (result) {
          toast(t("alr.evalDoneToast", { count: result.new_events || 0 }));
          if (result.new_events) {
            paintAlertsEvents();
          }
        }).catch(function (error) {
          toast(error.message || t("state.errorHint"));
        }).finally(function () {
          evalBtn.disabled = false;
        });
      });
      footBar.appendChild(evalBtn);
      footBar.appendChild(el("span", "pg-card-sub", t("alr.throttleNote")));
      body.appendChild(footBar);
      body.setAttribute("data-state", "default");
    }).catch(function (error) {
      setError(body, error.message || t("state.errorHint"), paintAlertsRules);
      body.setAttribute("data-state", "error");
    });
  }

  /** POST /api/alerts（新增规则）。 */
  function alertAddRule(payload) {
    return window.fetch("/api/alerts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (response) {
      if (!response.ok) {
        throw new Error("请求失败（HTTP " + response.status + "）");
      }
      return response.json();
    }).then(function (body) {
      if (body && body.error) {
        throw new Error(body.error);
      }
      return body.data || {};
    });
  }

  /** PATCH /api/alerts/{id}。 */
  function alertPatchRule(ruleId, fields) {
    return window.fetch("/api/alerts/" + encodeURIComponent(ruleId), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fields)
    }).then(function (response) {
      if (!response.ok) {
        throw new Error("请求失败（HTTP " + response.status + "）");
      }
      return response.json();
    }).then(function (body) {
      if (body && body.error) {
        throw new Error(body.error);
      }
      return body.data || {};
    });
  }

  /** DELETE /api/alerts/{id}。 */
  function alertDeleteRule(ruleId) {
    return window.fetch("/api/alerts/" + encodeURIComponent(ruleId), { method: "DELETE" })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("请求失败（HTTP " + response.status + "）");
        }
        return response.json();
      })
      .then(function (body) {
        if (body && body.error) {
          throw new Error(body.error);
        }
        return body.data || {};
      });
  }

  /** POST /api/alerts/evaluate?force=true（手动立即评估）。 */
  function alertEvaluate(force) {
    return window.fetch("/api/alerts/evaluate?force=" + (force ? "true" : "false"), { method: "POST" })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("请求失败（HTTP " + response.status + "）");
        }
        return response.json();
      })
      .then(function (body) {
        if (body && body.error) {
          throw new Error(body.error);
        }
        return body.data || {};
      });
  }

  /** 顶栏铃铛未读徽标更新（其他页面轮询也复用）。 */
  function updateBell(unread) {
    var badge = document.getElementById("alerts-unread");
    if (!badge) {
      return;
    }
    var count = Number(unread) || 0;
    badge.textContent = count > 99 ? "99+" : String(count);
    badge.hidden = count === 0;
  }

  /** 铃铛轮询：120s 拉未读数 + 触发一次后端评估（后端 60s/10min/6h 节流兜底）。
   * 仅页面可见时轮询；异常静默（不打扰用户）。
   */
  function startBellPolling() {
    var tick = function () {
      if (document.hidden) {
        return;
      }
      alertEvaluate(false).then(function (result) {
        updateBell(result.unread || 0);
        // 新事件：toast 提醒 + 徽标刷新（在提醒页时由页面自身刷新列表）
        if (result.new_events && result.events && result.events.length) {
          toast(t("alr.newEventToast", { first: result.events[0].message || "" }));
        }
      }).catch(function () {
        /* 后台轮询失败静默 */
      });
    };
    tick();
    window.setInterval(tick, 120000);
  }

  startBellPolling();

  /** 入口：绑定路由与尺寸监听。 */
  function init() {
    bindNav();
    bindResize();
    window.addEventListener("hashchange", route);
    route();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
