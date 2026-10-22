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

  /** 取语言包文本（阶段 6 交接口径：页面文案统一走 I18N，缺 key 时返回 key 本身便于排查）。
   * @param {string} key 键名 / i18n key
   * @param {Object} vars 占位符变量表
   * @returns {*} 处理结果
   */
  function t(key, vars) {
    return window.I18N ? window.I18N.t(key, vars) : key;
  }

  // 各视图容器（由 index.html 提供）
  var VIEW_IDS = {
    sectors: "page-sectors",
    stock: "page-stock",
    "sector-val": "page-sector-val",
    news: "page-news",
    fund: "page-fund",
    charting: "page-charting",
    portfolio: "page-portfolio",
    connectors: "page-connectors",
    dashboard: "page-dashboard",
    yjb: "page-yjb",
    settings: "page-settings"
  };

  // 图表实例登记表：路由切换时统一 dispose，窗口 resize 时统一 resize
  var charts = [];

  // 页面会话状态：市场/口径/标的，用于 Tab 切换时保留上下文
  var state = {
    sectors: { market: "cn", kind: "industry" },
    stock: { symbol: "688012", market: "a", tab: "kline", finTab: "summary" },
    sectorVal: { board: "", market: "cn" },
    // 图表分析页状态：标的选择 + 周期/复权/指标开关 + K线/分时 Tab + 画线开关
    charting: { symbol: "688012", market: "a", period: "日k", adjust: "qfq", tab: "kline", draw: false },
    // 设置页状态：AI 配置表单（api_key 留空表示保留原值，与后端契约一致）
    settings: { saving: false }
  };

  // =============================================================
  // 一、通用工具
  // =============================================================
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

  /** 数字格式化：空值返回「无数据」，其余保留指定小数并加千分位（统一走 L10N.num）。
   * @param {*} value 待格式化或处理的值
   * @param {number} digits 小数位数
   * @returns {*} 处理结果
   */
  function fmt(value, digits) {
    if (window.L10N) {
      return window.L10N.num(value, digits);
    }
    if (value === null || value === undefined || (typeof value === "number" && isNaN(value))) {
      return "无数据";
    }
    var num = Number(value);
    if (isNaN(num)) {
      return "无数据";
    }
    var fixed = num.toFixed(digits === undefined ? 2 : digits);
    var parts = fixed.split(".");
    parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    return parts.join(".");
  }

  /** 百分比格式化：带正负号与 %，空值「无数据」（统一走 L10N.pct）。
   * @param {*} value 待格式化或处理的值
   * @returns {*} 处理结果
   */
  function fmtPct(value) {
    if (window.L10N) {
      return window.L10N.pct(value);
    }
    if (value === null || value === undefined) {
      return "无数据";
    }
    var num = Number(value);
    if (isNaN(num)) {
      return "无数据";
    }
    return (num > 0 ? "+" : "") + num.toFixed(2) + "%";
  }

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

  /** 相对时间：把 YYYY-MM-DD HH:MM:SS 转成「x 分钟前 / x 小时前 / 日期」。
   * @param {string} text 文本内容
   * @returns {*} 处理结果
   */
  function relativeTime(text) {
    if (!text) {
      return "";
    }
    var stamp = new Date(String(text).replace(/-/g, "/"));
    if (isNaN(stamp.getTime())) {
      return String(text).slice(0, 16);
    }
    var diff = (Date.now() - stamp.getTime()) / 1000;
    if (diff < 60) {
      return "刚刚";
    }
    if (diff < 3600) {
      return Math.floor(diff / 60) + " 分钟前";
    }
    if (diff < 86400) {
      return Math.floor(diff / 3600) + " 小时前";
    }
    return String(text).slice(0, 10);
  }

  /** 统一请求：返回 data；失败抛出带中文文案的错误。
   * @param {string} path 接口路径
   * @param {Object} params 查询参数对象
   * @returns {*} 处理结果
   */
  function api(path, params) {
    var query = new URLSearchParams();
    Object.keys(params || {}).forEach(function (key) {
      if (params[key] !== undefined && params[key] !== null && params[key] !== "") {
        query.append(key, params[key]);
      }
    });
    var url = path + (query.toString() ? "?" + query.toString() : "");
    return fetch(url, { cache: "no-store" })
      // 网络层失败（断网/请求被拦截）统一转中文，避免把 "Failed to fetch" 暴露到界面
      .catch(function () {
        throw new Error("网络连接失败，请检查网络后重试。");
      })
      .then(function (response) {
        return response.json().catch(function () {
          throw new Error("数据格式异常，请稍后重试。");
        });
      })
      .then(function (body) {
        if (body && body.error) {
          // 错误码只进控制台，界面不暴露内部标记
          console.warn("[api]", url, body.error_code);
          var err = new Error(body.error);
          err.code = body.error_code;
          throw err;
        }
        return body.data || {};
      });
  }

  /** 轻提示（复用页面底部 #toast 元素）。
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
    box.appendChild(el("div", "pg-state-title", message || "暂无数据，点击刷新"));
    var button = el("button", "pg-btn", "刷新");
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
    var title = el("div", "pg-state-title", "加载失败");
    var hint = el("div", "pg-state-hint", message || "请检查网络后点击重试");
    var button = el("button", "pg-btn", "重试");
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
    var card = el("section", "pg-card " + gridClass);
    card.id = id;
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
   * 渲染表格。
   * columns: [{title, key, cls, render}]
   * rows: 数据数组；onRowClick(row) 提供整行点击（跳转闭环）。
   * @param {HTMLElement} body 区块容器（.pg-body，四态 data-state 挂在此节点）
   * @param {Array} columns 列定义（字段名数组）
   * @param {Array} rows 记录行列表
   * @param {*} onRowClick 行点击回调
   * @param {*} maxHeightClass 最大高度类名
   */
  function renderTable(body, columns, rows, onRowClick, maxHeightClass) {
    body.innerHTML = "";
    if (!rows || rows.length === 0) {
      setEmpty(body, "暂无数据，点击刷新");
      return;
    }
    var wrap = el("div", "pg-table-wrap" + (maxHeightClass ? " " + maxHeightClass : ""));
    var table = el("table", "pg-table");
    var thead = el("thead");
    var headRow = el("tr");
    columns.forEach(function (column) {
      headRow.appendChild(el("th", column.title === undefined ? "" : "", column.title));
    });
    thead.appendChild(headRow);
    table.appendChild(thead);

    var tbody = el("tbody");
    rows.forEach(function (row) {
      var tr = el("tr");
      columns.forEach(function (column) {
        var td = el("td", column.cls || "");
        var value = column.render ? column.render(row) : row[column.key];
        if (value instanceof Node) {
          td.appendChild(value);
        } else {
          td.textContent = value === undefined || value === null ? "无数据" : String(value);
        }
        tr.appendChild(td);
      });
      if (onRowClick) {
        tr.addEventListener("click", function () {
          onRowClick(row);
        });
      }
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    body.appendChild(wrap);
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
    var title = {
      sectors: "板块分析",
      stock: "个股详情 · FinScope",
      "sector-val": "单板块估值",
      news: "新闻资讯",
      fund: "场外基金",
      charting: "图表分析",
      portfolio: "持仓管理",
      connectors: "数据连接器",
      dashboard: window.I18N ? window.I18N.t("page.dashboard") : "仪表板",
      yjb: "养基宝绑定",
      settings: "设置"
    }[name];
    var titleNode = document.getElementById("page-title");
    if (titleNode && title) {
      titleNode.textContent = title;
    }
    // 同步左侧菜单选中态
    document.querySelectorAll("#main-nav .nav-item").forEach(function (item) {
      item.classList.toggle("active", item.getAttribute("data-view") === (name === "home" ? "home" : name));
    });

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
    } else if (name === "portfolio") {
      renderPortfolio();
    } else if (name === "connectors") {
      renderConnectors();
    } else if (name === "dashboard") {
      if (window.renderDashboard) {
        window.renderDashboard();
      }
    } else if (name === "yjb") {
      renderYjb();
    } else if (name === "settings") {
      renderSettings();
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
    [["cn", "国内"], ["hk", "港股"], ["us", "美股"]].forEach(function (item) {
      var button = el("button", "pg-tab", item[1]);
      button.type = "button";
      button.setAttribute("data-market", item[0]);
      marketTabs.appendChild(button);
    });
    toolbar.appendChild(marketTabs);

    // S-02 行业/概念切换
    var kindTabs = el("div", "pg-tabs");
    kindTabs.id = "S-02";
    [["industry", "行业"], ["concept", "概念"]].forEach(function (item) {
      var button = el("button", "pg-tab", item[1]);
      button.type = "button";
      button.setAttribute("data-kind", item[0]);
      kindTabs.appendChild(button);
    });
    toolbar.appendChild(kindTabs);
    toolbar.appendChild(el("span", "pg-spacer"));

    var refresh = el("button", "pg-btn", "刷新本页");
    refresh.type = "button";
    refresh.id = "sectors-refresh";
    toolbar.appendChild(refresh);
    container.appendChild(toolbar);

    var grid = el("div", "pg-grid");
    grid.id = "sectors-grid";

    // 左主栏 S-03 热力图
    var heat = buildCard("S-03", "板块热力图", "g-7", "面积=abs(涨跌幅)，颜色与标签为原值");
    var heatBody = heat.body;
    heatBody.classList.add("tall");
    heatBody.id = "s03-body";
    grid.appendChild(heat.card);

    // 右辅栏：S-04/S-05 排行 + S-06 摘要
    var rightStack = el("div", "g-5");
    rightStack.style.display = "flex";
    rightStack.style.flexDirection = "column";
    rightStack.style.gap = "16px";

    var rank = buildCard("S-04", "行业板块排行", "", "");
    rank.card.setAttribute("data-block", "rank"); // 稳定锚点：id 会在 S-04/S-05 间切换
    rank.body.id = "s04-body";
    var rankBody = rank.body;
    rankBody.classList.add("short");
    rightStack.appendChild(rank.card);

    var summary = buildCard("S-06", "板块摘要", "", "");
    summary.body.id = "s06-body";
    rightStack.appendChild(summary.card);
    grid.appendChild(rightStack);

    // S-07 资金流向通栏
    var flow = buildCard("S-07", "板块资金流向", "g-12", "主力净额/流入/流出；超大单、大单源不提供");
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

  /** 渲染板块页。
   * @param {Object} params 查询参数对象
   */
  function renderSectors(params) {
    var container = document.getElementById(VIEW_IDS.sectors);
    if (params.market) {
      state.sectors.market = params.market;
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
      // 排行卡 id 会在 S-04 / S-05 间切换，故用稳定锚点定位
      s04: document.querySelector('[data-block="rank"]'),
      s06: document.getElementById("S-06"),
      s07: document.getElementById("S-07")
    };
    var bodies = {
      s03: document.getElementById("s03-body"),
      s04: document.getElementById("s04-body"),
      s06: document.getElementById("s06-body"),
      s07: document.getElementById("s07-body")
    };
    if (!bodies.s03) {
      return;
    }
    // 排行卡标题随口径变化（S-04 行业 / S-05 概念同源切换）
    var rankTitle = cards.s04.querySelector(".pg-card-title");
    if (rankTitle) {
      rankTitle.textContent = kind === "concept" ? "概念板块排行" : "行业板块排行";
    }
    cards.s04.id = kind === "concept" ? "S-05" : "S-04";

    if (force) {
      sectorsCache = {};
    }
    if (sectorsCache[cacheKey]) {
      paintSectors(sectorsCache[cacheKey]);
      return;
    }
    setLoading(bodies.s03);
    setLoading(bodies.s04);
    setLoading(bodies.s06);
    setLoading(bodies.s07);

    var listPath = kind === "concept" ? "/api/sector/concept" : "/api/sector/industry";
    // 资金流仅 A股有免费通道；港美股不发请求，避免整页因单块 4xx 业务错误被判为失败
    var flowTask = market === "cn" && kind === "industry"
      ? api("/api/sector/fundflow", { market: market })
      : Promise.resolve(null);
    Promise.all([
      api(listPath, { market: market }),
      api("/api/sector/heatmap", { market: market, kind: kind }),
      api("/api/sector/summary", { market: market }),
      flowTask
    ])
      .then(function (result) {
        var payload = {
          market: market,
          kind: kind,
          list: result[0],
          heat: result[1],
          summary: result[2],
          flow: result[3]
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
      s06: document.getElementById("s06-body"),
      s07: document.getElementById("s07-body")
    };
    var cards = {
      s03: document.getElementById("S-03"),
      s04: document.getElementById("S-05") || document.getElementById("S-04"),
      s06: document.getElementById("S-06"),
      s07: document.getElementById("S-07")
    };
    if (!bodies.s03) {
      return;
    }
    var degrade = payload.list.source && payload.list.source !== "同花顺" && market === "cn";

    // ---- S-03 热力图 ----
    setBadges(cards.s03, payload.heat.source, payload.heat.caliber, degrade);
    bodies.s03.innerHTML = "";
    var nodes = (payload.heat.nodes || []).filter(function (node) {
      return node.value !== null && node.value !== undefined;
    });
    if (nodes.length === 0) {
      setEmpty(bodies.s03, "暂无数据，点击刷新", function () {
        loadSectors(true);
      });
    } else {
      var chartBox = el("div", "pg-chart");
      chartBox.id = "s03-chart";
      bodies.s03.appendChild(chartBox);
      var chart = mountChart(chartBox);
      if (chart) {
        var data = nodes.map(function (node) {
          var pct = node.pct;
          var color = pct > 0 ? COLOR_UP : pct < 0 ? COLOR_DOWN : COLOR_MUTED;
          return {
            name: node.name,
            value: node.value,
            pct: pct,
            amount: node.amount,
            lead: node.lead_stock,
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
                "<b>" + info.name + "</b><br/>涨跌幅：" + fmtPct(source.pct) +
                "<br/>成交额：" + (source.amount === null || source.amount === undefined ? "无数据" : fmt(source.amount) + " 亿元") +
                "<br/>领涨股：" + (source.lead || "无数据")
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
                overflow: "truncate",
                lineHeight: 12,
                /** 热力图块内标签：板块名换行 + 涨跌幅百分比（缺值显示「无数据」）。
                 * @param {*} info 信息对象
                 * @returns {*} 处理结果
                 */
                formatter: function (info) {
                  var pctText = info.data.pct === null ? "无数据" : Number(info.data.pct).toFixed(2) + "%";
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

    // ---- S-04 / S-05 排行表 ----
    setBadges(cards.s04, payload.list.source, payload.list.caliber, degrade);
    var rows = payload.list.items || [];
    renderTable(
      bodies.s04,
      [
        { title: "板块名", key: "name", cls: "name" },
        { title: "涨跌幅%", cls: "num", render: function (row) { return pctNode(row.pct); } },
        { title: "成交额(亿元)", cls: "num", render: function (row) { return fmt(row.amount); } },
        { title: "领涨股", key: "lead_stock" },
        { title: "领涨幅%", cls: "num", render: function (row) { return pctNode(row.lead_pct); } }
      ],
      rows,
      function (row) {
        jumpToSectorVal(row.name, market);
      },
      "short"
    );

    // ---- S-06 摘要 ----
    setBadges(cards.s06, payload.summary.source, payload.summary.caliber, degrade);
    paintSummary(bodies.s06, payload.summary);

    // ---- S-07 资金流向 ----
    if (market !== "cn") {
      setBadges(cards.s07, "", "", false);
      setEmpty(bodies.s07, "境外简化建模：港美股板块资金流暂无免费通道", function () {
        loadSectors(true);
      });
    } else if (payload.kind === "concept") {
      setBadges(cards.s07, "", "", false);
      setEmpty(bodies.s07, "概念板块资金流暂无免费通道，可切换到「行业」查看", function () {
        state.sectors.kind = "industry";
        syncSectorTabs();
        loadSectors();
      });
    } else if (payload.flow) {
      setBadges(cards.s07, payload.flow.source, payload.flow.caliber, degrade);
      var flowRows = (payload.flow.items || []).slice(0, 40);
      renderTable(
        bodies.s07,
        [
          { title: "板块名", key: "name", cls: "name" },
          { title: "主力净流入(亿元)", cls: "num", render: function (row) { return pctNode(row.net_inflow); } },
          { title: "流入(亿元)", cls: "num", render: function (row) { return fmt(row.inflow); } },
          { title: "流出(亿元)", cls: "num", render: function (row) { return fmt(row.outflow); } },
          // 源不提供分项：显示「无数据」而不是补 0（§9）
 // @param {*} row 单行记录
          { title: "超大单(亿元)", cls: "num", render: function (row) { return fmt(row.super_big); } },
          { title: "大单(亿元)", cls: "num", render: function (row) { return fmt(row.big); } }
        ],
        flowRows,
        null,
        null
      );
    }
  }

  /** 摘要卡：领涨/领跌/净流入 Top3 + 集中度。
   * @param {HTMLElement} body 区块容器（.pg-body，四态 data-state 挂在此节点）
   * @param {*} summary 摘要数据
   */
  function paintSummary(body, summary) {
    body.innerHTML = "";
    if (!summary || (!summary.gainers.length && !summary.losers.length)) {
      setEmpty(body, "暂无数据，点击刷新", function () {
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
    group("领涨 Top3", summary.gainers || [], "pct");
    group("领跌 Top3", summary.losers || [], "pct");
    if (summary.inflow && summary.inflow.length) {
      group("主力净流入 Top3", summary.inflow, "net_inflow");
    }
    var conc = el("div", "pg-card-sub");
    conc.textContent =
      "成交额集中度（Top10 板块占比）：" + (summary.concentration === null ? "无数据" : summary.concentration + "%");
    body.appendChild(conc);
    body.setAttribute("data-state", "default");
  }

  /** 跳转 #1/#9：板块 → 单板块估值。
   * @param {*} board 板块名称
   * @param {*} market 市场标识：a/hk/us
   */
  function jumpToSectorVal(board, market) {
    window.location.hash =
      "#/sector-val?board=" + encodeURIComponent(board) + "&market=" + (market || "cn");
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
    var label = el("span", "pg-card-sub", "证券代码");
    toolbar.appendChild(label);
    var input = document.createElement("input");
    input.id = "stock-symbol";
    input.value = state.stock.symbol;
    input.setAttribute("aria-label", "证券代码");
    input.style.cssText =
      "width:120px;background:var(--bg-panel);border:1px solid var(--border);color:var(--text);" +
      "border-radius:5px;padding:4px 8px;font-family:var(--font-mono)";
    toolbar.appendChild(input);
    var marketSelect = document.createElement("select");
    marketSelect.id = "stock-market";
    marketSelect.style.cssText =
      "background:var(--bg-panel);border:1px solid var(--border);color:var(--text);border-radius:5px;padding:4px 8px";
    [["a", "A股"], ["hk", "港股"], ["us", "美股"]].forEach(function (item) {
      var option = document.createElement("option");
      option.value = item[0];
      option.textContent = item[1];
      marketSelect.appendChild(option);
    });
    marketSelect.value = state.stock.market;
    toolbar.appendChild(marketSelect);
    var go = el("button", "pg-btn", "查看");
    go.type = "button";
    go.id = "stock-go";
    toolbar.appendChild(go);
    toolbar.appendChild(el("span", "pg-spacer"));
    var refresh = el("button", "pg-btn", "刷新本页");
    refresh.type = "button";
    refresh.id = "stock-refresh";
    toolbar.appendChild(refresh);
    container.appendChild(toolbar);

    var grid = el("div", "pg-grid");
    grid.id = "stock-grid";

    // ST-01 报价大卡（通栏）
    var quote = buildCard("ST-01", "报价", "g-12", "");
    quote.body.id = "st01-body";
    grid.appendChild(quote.card);

    // 左主栏：ST-02/ST-03 互斥 Tab + ST-04 + ST-05
    var left = el("div", "g-7");
    left.style.display = "flex";
    left.style.flexDirection = "column";
    left.style.gap = "16px";

    var chartCard = buildCard("ST-02", "价格走势", "", "");
    chartCard.card.setAttribute("data-block", "chart"); // 稳定锚点：id 在 ST-02/ST-03 间切换
    chartCard.body.id = "st02-body";
    var tabRow = el("div", "pg-tabs");
    tabRow.style.marginBottom = "8px";
    [["kline", "K 线"], ["minute", "分时"]].forEach(function (item) {
      var button = el("button", "pg-tab", item[1]);
      button.type = "button";
      button.setAttribute("data-tab", item[0]);
      tabRow.appendChild(button);
    });
    chartCard.card.insertBefore(tabRow, chartCard.body);
    left.appendChild(chartCard.card);

    var fundCard = buildCard("ST-04", "资金分布", "", "超大单/大单/中单/小单四档");
    fundCard.body.id = "st04-body";
    left.appendChild(fundCard.card);

    var newsCard = buildCard("ST-05", "相关新闻", "", "标题点击跳转新闻页");
    newsCard.body.id = "st05-body";
    left.appendChild(newsCard.card);
    grid.appendChild(left);

    // 右辅栏：ST-07 舆情 + ST-06 机构预测
    var right = el("div", "g-5");
    right.style.display = "flex";
    right.style.flexDirection = "column";
    right.style.gap = "16px";
    var sentiCard = buildCard("ST-07", "舆情", "", "本地词典三档");
    sentiCard.body.id = "st07-body";
    right.appendChild(sentiCard.card);
    var forecastCard = buildCard("ST-06", "机构预测", "", "");
    forecastCard.body.id = "st06-body";
    right.appendChild(forecastCard.card);
    grid.appendChild(right);

    // 通栏：ST-08 / ST-09 / ST-10
    var profileCard = buildCard("ST-08", "公司概况", "g-12", "");
    profileCard.body.id = "st08-body";
    grid.appendChild(profileCard.card);

    var finCard = buildCard("ST-09", "财务", "g-12", "");
    finCard.body.id = "st09-body";
    grid.appendChild(finCard.card);

    var holderCard = buildCard("ST-10", "十大股东", "g-12", "");
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
  }

  /** 读取输入框并跳转（保持 URL 参数与页面状态一致）。 */
  function applyStockInput() {
    var input = document.getElementById("stock-symbol");
    var code = (input && input.value ? input.value : "").trim();
    if (!code) {
      toast("请输入证券代码");
      return;
    }
    window.location.hash =
      "#/stock?symbol=" + encodeURIComponent(code) + "&market=" + state.stock.market;
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
      title.textContent = state.stock.tab === "minute" ? "分时走势（上下午两段，午休断裂）" : "K 线（日线，前复权）";
    }
  }

  var stockCache = { key: "", panel: null };

  /** 渲染个股页。
   * @param {Object} params 查询参数对象
   */
  function renderStock(params) {
    var container = document.getElementById(VIEW_IDS.stock);
    state.stock.symbol = params.symbol || state.stock.symbol;
    state.stock.market = params.market || state.stock.market;
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
    syncStockTabs();
    loadStock();
  }

  /** 拉取并渲染个股页（面板 + 概况 + 股东 + 财务摘要/趋势）。
   * @param {boolean} force 是否强制刷新（true 时跳过本地缓存）
   * @returns {*} 处理结果
   */
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
      setEmpty(body, "该标的暂无行情数据", function () {
        loadStock(true);
      });
      return;
    }
    setBadges(card, quote.source, quote.caliber, false);
    body.innerHTML = "";
    var main = el("div", "pg-quote-main");
    main.appendChild(el("span", "pg-quote-name", quote.name || "无数据"));
    main.appendChild(el("span", "pg-quote-price " + pctClass(quote.change), fmt(quote.price, 2)));
    main.appendChild(el("span", pctClass(quote.change), fmt(quote.change, 2) + " / " + fmtPct(quote.pct)));
    main.appendChild(el("span", "pg-card-sub", quote.quote_time || ""));
    body.appendChild(main);
    var meta = el("div", "pg-quote-meta");
    [
      ["今开", fmt(quote.open, 2)],
      ["最高", fmt(quote.high, 2)],
      ["最低", fmt(quote.low, 2)],
      ["昨收", fmt(quote.prev_close, 2)],
      ["换手率", quote.turnover === null ? "无数据" : fmt(quote.turnover, 2) + "%"],
      ["总市值", quote.total_market_cap === null ? "无数据" : fmt(yi(quote.total_market_cap), 2) + " 亿"],
      ["PE(TTM)", fmt(quote.pe_ttm, 2)],
      ["市场状态", quote.market_status || "无数据"]
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
        setEmpty(body, "该标的暂无分时数据", function () {
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
        categories.splice(noonIndex, 0, "午休");
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
          { name: "价格", type: "line", data: prices, showSymbol: false, lineStyle: { width: 1.4 }, color: COLOR_UP },
          { name: "均价", type: "line", data: avg, showSymbol: false, lineStyle: { width: 1, type: "dashed" }, color: "#e8a33d" }
        ]
      });
      body.setAttribute("data-state", "default");
      return;
    }

    var kline = panel.kline || [];
    if (kline.length === 0) {
      setEmpty(body, "该标的暂无 K 线数据", function () {
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
    chart.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      grid: [
        { left: 56, right: 16, top: 16, height: "62%" },
        { left: 56, right: 16, top: "76%", height: "16%" }
      ],
      xAxis: [
        { type: "category", data: dates, axisLabel: { color: theme.text }, axisLine: { lineStyle: { color: theme.border } } },
        { type: "category", gridIndex: 1, data: dates, axisLabel: { show: false }, axisLine: { lineStyle: { color: theme.border } } }
      ],
      yAxis: [
        { scale: true, axisLabel: { color: theme.text }, splitLine: { lineStyle: { color: theme.border, opacity: 0.4 } } },
        { gridIndex: 1, scale: true, axisLabel: { show: false }, splitLine: { show: false } }
      ],
      series: [
        {
          name: "日K",
          type: "candlestick",
          data: ohlc,
          itemStyle: { color: COLOR_UP, color0: COLOR_DOWN, borderColor: COLOR_UP, borderColor0: COLOR_DOWN }
        },
        { name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: volumes }
      ]
    });
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
      setEmpty(body, "资金数据暂无，点击刷新", function () {
        loadStock(true);
      });
      return;
    }
    setBadges(card, "新浪", fund.caliber_note || "四档资金流", false);
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
            return item.name + "<br/>净额：" + fmt(item.net, 4) + " 亿元<br/>占比：" + fmt(item.ratio, 2) + "%";
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
                var ratioText = item.ratio === null || item.ratio === undefined ? "无数据" : fmt(item.ratio, 1) + "%";
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
      "主力净流入：" + fmt(fund.main_net, 4) + " 亿元（占成交 " + fmt(fund.main_ratio, 2) + "%）";
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
      setEmpty(body, "该股暂无相关新闻", function () {
        loadStock(true);
      });
      return;
    }
    body.innerHTML = "";
    var list = el("ul", "pg-news-list");
    news.forEach(function (item) {
      var li = el("li", "pg-news-item");
      var title = el("span", "pg-news-title", item.title);
      // 跳转 #5：标题 → #/news?symbol=…（新闻页 5.2 落地前给轻提示，不报错）
      title.addEventListener("click", function () {
        window.location.hash = "#/news?symbol=" + encodeURIComponent(state.stock.symbol);
        toast("新闻页 5.2 阶段开放，已带参数跳转。");
      });
      var chipClass = item.sentiment === "利好" ? "chip up" : item.sentiment === "利空" ? "chip down" : "chip";
      li.appendChild(title);
      li.appendChild(el("span", chipClass, item.sentiment || "中性"));
      li.appendChild(el("span", "pg-news-time", relativeTime(item.time)));
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
      setEmpty(body, "暂无可分析的资讯", function () {
        loadStock(true);
      });
      return;
    }
    var positive = news.filter(function (item) { return item.sentiment === "利好"; }).length;
    var negative = news.filter(function (item) { return item.sentiment === "利空"; }).length;
    var neutral = news.length - positive - negative;
    var score = news.reduce(function (acc, item) { return acc + (item.sentiment_score || 0); }, 0);
    var level = score > 1 ? "偏多" : score < -1 ? "偏空" : "中性";
    body.innerHTML = "";
    var rows = [
      ["利好条数", positive, "up"],
      ["利空条数", negative, "down"],
      ["中性条数", neutral, "flat"],
      ["情绪分", score, score > 0 ? "up" : score < 0 ? "down" : "flat"],
      ["情绪档位", level, "flat"]
    ];
    var grid = el("div", "pg-metric-grid");
    rows.forEach(function (item) {
      var cell = el("div", "pg-metric-cell");
      cell.appendChild(el("div", "pg-metric-label", item[0]));
      cell.appendChild(el("div", "pg-metric-value " + item[2], item[1]));
      grid.appendChild(cell);
    });
    body.appendChild(grid);
    var note = el("div", "pg-card-sub", "由 " + news.length + " 条新闻的本地词典标签聚合，非大模型判读");
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
        ? "无数据"
        : fmt(row["最小值"], 2) + " ~ " + fmt(row["最大值"], 2);
      tr.appendChild(el("td", "", range));
      tr.appendChild(el("td", "", profit["均值"] === null || profit["均值"] === undefined
        ? "无数据" : fmt(profit["均值"], 2)));
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
    var trough = null;
    (series || []).forEach(function (item) {
      if (item && item.drawdown !== null && item.drawdown !== undefined) {
        if (!trough || item.drawdown < trough.drawdown) {
          trough = item;
        }
      }
    });
    if (!trough) {
      return undefined;
    }
    return {
      symbol: "circle",
      symbolSize: 7,
      itemStyle: { color: COLOR_DOWN },
      label: {
        show: true,
        position: "bottom",
        fontSize: 10,
        color: COLOR_DOWN,
        formatter: "最大回撤 " + trough.date + " " + Number(trough.drawdown).toFixed(2) + "%"
      },
      data: [{ coord: [trough.date, trough.drawdown], value: trough.drawdown }]
    };
  }

  /** 渲染机构预测（ST-06）：评级分布 + 目标价 + 分年度一致预期。
   * @param {Object} panel 数据面板（报价/新闻/舆情等聚合块）
   * @param {*} estimates 一致预期数据（eps/profit/institutions/detail）
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
        setBadges(card, "同花顺", estimates.caliber_note || "一致预期", false);
        body.innerHTML = "";
        paintEstimates(body, estimates);
        body.setAttribute("data-state", "default");
        return;
      }
      setEmpty(body, "机构预测暂无", function () {
        loadStock(true);
      });
      return;
    }
    setBadges(card, "同花顺", forecast.caliber_note || "评级分布+目标价", false);
    body.innerHTML = "";
    var hasTarget = forecast.target_low !== null && forecast.target_low !== undefined;
    var targetText;
    if (hasTarget) {
      targetText = fmt(forecast.target_low, 2) + " ~ " + fmt(forecast.target_high, 2) +
        " 元（均值 " + fmt(forecast.target_avg, 2) + "，样本 " + (forecast.target_samples || 0) + " 篇）";
    } else {
      // 明确空态（P1-11）：说清为什么没有、评级覆盖多少、可以去哪里找替代信息
      targetText = "目标价暂无（" + (forecast.caliber_note || "该通道不提供目标价") + "）" +
        "；评级仍覆盖 " + (forecast.org_count || 0) + " 家，可参考下方一致预期 EPS / 净利";
    }
    var lines = [
      ["目标价区间", targetText],
      ["覆盖机构", (forecast.org_count || 0) + " 家"],
      ["买入/增持", (forecast.ratings["买入"] || 0) + " / " + (forecast.ratings["增持"] || 0)],
      ["中性/减持/卖出", (forecast.ratings["中性"] || 0) + " / " + (forecast.ratings["减持"] || 0) + " / " + (forecast.ratings["卖出"] || 0)]
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
      setEmpty(body, "暂无公司概况数据", function () {
        loadStock(true);
      });
      return;
    }
    setBadges(card, "东财", profile.caliber_note || "F10 基本资料", false);
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
      var byKey = {};
      summary.metrics.forEach(function (item) {
        byKey[item.key] = item.values[0];
      });
      finGroup = [
        ["报告期", latest],
        ["营业总收入", byKey["营业总收入"] === null ? "无数据" : fmt(byKey["营业总收入"] / 1e8, 2) + " 亿元"],
        ["归母净利润", byKey["归母净利润"] === null ? "无数据" : fmt(byKey["归母净利润"] / 1e8, 2) + " 亿元"],
        ["EPS", fmt(byKey["基本每股收益"], 2) + " 元"],
        ["毛利率", fmt(byKey["毛利率"], 2) + "%"],
        ["ROE", fmt(byKey["净资产收益率(ROE)"], 2) + "%"]
      ];
    }

    var groups = [
      ["基本信息", [
        ["公司名称", profile.full_name || profile.name],
        ["所属行业", profile.industry],
        ["上市日期", profile.listing_date],
        ["上市板块", profile.board],
        ["注册地", profile.province]
      ]],
      ["股本市值", [
        ["总股本", profile.total_share === null ? "无数据" : fmt(profile.total_share / 1e8, 2) + " 亿股"],
        ["流通股", profile.float_share === null ? "无数据" : fmt(profile.float_share / 1e8, 2) + " 亿股"],
        ["总市值", profile.total_mv === null ? "无数据" : fmt(yi(profile.total_mv), 2) + " 亿元"],
        ["流通市值", profile.float_mv === null ? "无数据" : fmt(yi(profile.float_mv), 2) + " 亿元"],
        ["PE(TTM)", fmt(profile.pe_ttm, 2)]
      ]],
      ["机构与基金持有", [
        ["十大流通股东中机构家数", orgCount ? orgCount + " 家" : "无数据"],
        ["十大流通股东合计", freeRatio ? fmt(freeRatio, 2) + "%" : "无数据"],
        ["前三大流通股东", topThree || "无数据"],
        ["数据口径", (holders && holders.free_top10_period) ? "报告期 " + holders.free_top10_period : "无数据"]
      ]],
      ["最新财报摘要", finGroup.length ? finGroup : [["财报", "暂无数据"]]]
    ];

    groups.forEach(function (group) {
      body.appendChild(el("div", "pg-card-sub", group[0]));
      var grid = el("div", "pg-quote-meta");
      group[1].forEach(function (item) {
        var cell = el("span", "", item[0] + "：");
        cell.appendChild(el("b", "", item[1] === null || item[1] === undefined || item[1] === "" ? "无数据" : item[1]));
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
      setEmpty(body, "该市场暂无可用的中文财报通道", function () {
        loadStock(true);
      });
      return;
    }
    setBadges(card, fin.summary.source, fin.summary.caliber, false);
    body.innerHTML = "";
    var tabs = el("div", "pg-fin-tabs");
    [["summary", "关键指标"], ["trend", "财务趋势"], ["report", "三大报表"], ["indicator", "指标明细"]].forEach(function (item) {
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
      var columns = [{ title: "指标", key: "name", cls: "name" }].concat(
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
                return "无数据";
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
        setEmpty(pane, "暂无趋势数据", function () {
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
            { type: "value", name: "亿元", nameTextStyle: { color: theme.text }, axisLabel: { color: theme.text }, splitLine: { lineStyle: { color: theme.border, opacity: 0.4 } } },
            { type: "value", name: "%", nameTextStyle: { color: theme.text }, axisLabel: { color: theme.text }, splitLine: { show: false } }
          ],
          series: [
            { name: "营业总收入(亿)", type: "bar", data: revenue.map(function (v) { return v === null ? null : +(v / 1e8).toFixed(2); }), color: "#b47da0" },
            { name: "净利润(亿)", type: "bar", data: profit.map(function (v) { return v === null ? null : +(v / 1e8).toFixed(2); }), color: "#4b9eff" },
            { name: "营收同比(%)", type: "line", yAxisIndex: 1, data: revenueYoy, color: COLOR_UP, showSymbol: true },
            { name: "净利同比(%)", type: "line", yAxisIndex: 1, data: profitYoy, color: COLOR_DOWN, showSymbol: true }
          ]
        });
      }
      return;
    }

    if (tab === "report" || tab === "indicator") {
      // 报表/指标数据按需加载，避免首屏 5 个财务请求
      pane.innerHTML = "";
      var loadingBox = el("div", "pg-state");
      loadingBox.textContent = "加载中…";
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
            ["资产负债表", "利润表", "现金流量表"].forEach(function (name) {
              var button = el("button", "pg-tab" + (name === "利润表" ? " active" : ""), name);
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
    var columns = [{ title: "项目", key: "name", cls: "name" }].concat(
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
            return value === null || value === undefined ? "无数据" : fmt(value / 1e8, 2) + " 亿";
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
      setEmpty(body, "暂无股东数据", function () {
        loadStock(true);
      });
      return;
    }
    setBadges(card, "东财", "F10 股东（报告期 " + (holders.top10_period || "—") + "）", false);
    renderTable(
      body,
      [
        { title: "排名", key: "rank", cls: "num" },
        { title: "股东名称", key: "name", cls: "name" },
        { title: "持股比例%", cls: "num", render: function (row) { return fmt(row.ratio); } },
        {
          title: "本期变动",
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
    var info = el("span", "pg-card-sub", "板块");
    info.id = "val-board-label";
    toolbar.appendChild(info);
    toolbar.appendChild(el("span", "pg-spacer"));
    var refresh = el("button", "pg-btn", "刷新本页");
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

    var kline = buildCard("V-01", "板块 K 线", "", "板块指数日线");
    kline.body.id = "v01-body";
    kline.body.classList.add("tall");
    left.appendChild(kline.card);

    var metrics = buildCard("V-02", "板块估值指标", "", "");
    metrics.body.id = "v02-body";
    left.appendChild(metrics.card);

    var cons = buildCard("V-03", "成分股", "", "整行可点进入个股");
    cons.body.id = "v03-body";
    left.appendChild(cons.card);
    grid.appendChild(left);

    var right = el("div", "g-5");
    var info4 = buildCard("V-04", "板块精准资讯", "", "5.2 阶段落地，本批占位");
    info4.body.id = "v04-body";
    // 占位区块固定渲染，不参与数据加载/错误态，避免上游失败时把占位卡也标成「加载失败」
    var placeholder = el("div", "pg-state");
    placeholder.appendChild(el("div", "pg-state-title", "板块精准资讯（AI 初评）5.2 阶段落地"));
    var hint = el("div", "pg-state-hint", "本批仅占位并预留 board / market 参数，不做 AI 判读");
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
    state.sectorVal.market = params.market || state.sectorVal.market;
    if (!container.querySelector("#val-grid")) {
      buildValShell(container);
    }
    var label = document.getElementById("val-board-label");
    if (label) {
      label.textContent = "板块：" + (state.sectorVal.board || "未选择") + "（" + (state.sectorVal.market === "cn" ? "国内" : state.sectorVal.market === "hk" ? "港股" : "美股") + "）";
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
          setEmpty(body, "请从板块分析页点击板块进入", null);
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
        setEmpty(v01, "该板块暂无 K 线数据", function () {
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
        setEmpty(v02, "暂无数据，点击刷新", function () {
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
          ["最新点位", fmt(last.close, 2)],
          ["当日涨跌", fmtPct(pct)],
          ["成交额", last.amount === null ? "无数据" : fmt(yi(last.amount), 2) + " 亿元"],
          ["近一年价格分位", percentile === null ? "无数据" : fmt(percentile, 1) + "%"],
          ["领涨股", leader ? leader.name + " " + fmtPct(leader.pct) : "无数据"],
          ["领跌股", laggard ? laggard.name + " " + fmtPct(laggard.pct) : "无数据"]
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
        setEmpty(v03, "该板块暂无成分数据", function () {
          loadSectorVal(true);
        });
      } else {
        setBadges(document.getElementById("V-03"), cons.source, cons.caliber, false);
        renderTable(
          v03,
          [
            { title: "代码", key: "code", cls: "num" },
            { title: "名称", key: "name", cls: "name" },
            { title: "涨跌幅%", cls: "num", render: function (row) { return pctNode(row.pct); } },
            { title: "权重%", cls: "num", render: function (row) { return fmt(row.weight); } },
            { title: "最新价", cls: "num", render: function (row) { return fmt(row.price, 2); } }
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
        "已预留参数：board=" + (board || "未选择") + "，market=" + market + "；本批不做 AI 判读";
    }
  }

  // =============================================================
  // 八、#/news 新闻与利好利空（批次 5.2）
  // 结构：N-01 三级板块树 / N-02 资讯流 / N-03 情绪温度 / N-04 AI 复核（内嵌于卡片）
  // =============================================================
  var newsState = {
    mode: "feed", // feed=多源资讯流，stock=个股新闻
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
  function buildNewsShell(container) {
    container.innerHTML = "";
    var toolbar = el("div", "page-toolbar");

    // 模式切换：资讯流 / 个股新闻（个股模式需带 symbol）
    var modeTabs = el("div", "pg-tabs");
    modeTabs.id = "N-mode";
    [["feed", "多源资讯流"], ["stock", "个股新闻"]].forEach(function (item) {
      var button = el("button", "pg-tab", item[1]);
      button.type = "button";
      button.setAttribute("data-mode", item[0]);
      modeTabs.appendChild(button);
    });
    toolbar.appendChild(modeTabs);

    // 排序切换
    var sortTabs = el("div", "pg-tabs");
    sortTabs.id = "N-sort";
    [["relevance", "相关度"], ["time", "时间"], ["impact", "影响度"]].forEach(function (item) {
      var button = el("button", "pg-tab", item[1]);
      button.type = "button";
      button.setAttribute("data-sort", item[0]);
      sortTabs.appendChild(button);
    });
    toolbar.appendChild(sortTabs);

    // 区域切换（仅资讯流模式生效）
    var regionTabs = el("div", "pg-tabs");
    regionTabs.id = "N-region";
    [["global", "全球"], ["cn", "国内"]].forEach(function (item) {
      var button = el("button", "pg-tab", item[1]);
      button.type = "button";
      button.setAttribute("data-region", item[0]);
      regionTabs.appendChild(button);
    });
    toolbar.appendChild(regionTabs);
    toolbar.appendChild(el("span", "pg-spacer"));

    var refresh = el("button", "pg-btn", "刷新本页");
    refresh.type = "button";
    refresh.id = "news-refresh";
    toolbar.appendChild(refresh);
    container.appendChild(toolbar);

    var grid = el("div", "pg-grid");
    grid.id = "news-grid";

    // 左栏：板块树 + 情绪温度
    var left = el("div", "g-4");
    left.style.display = "flex";
    left.style.flexDirection = "column";
    left.style.gap = "16px";
    var treeCard = buildCard("N-01", "板块树", "", "三级：市场 / 方向 / 细分");
    treeCard.body.id = "n01-body";
    treeCard.body.classList.add("short");
    left.appendChild(treeCard.card);
    var sentiCard = buildCard("N-03", "情绪温度", "", "本地规则引擎");
    sentiCard.body.id = "n03-body";
    left.appendChild(sentiCard.card);
    grid.appendChild(left);

    // 右栏：资讯流
    var feedCard = buildCard("N-02", "资讯流", "g-8", "本地确定性初评 · 非 AI 判读");
    feedCard.body.id = "n02-body";
    grid.appendChild(feedCard.card);
    container.appendChild(grid);

    modeTabs.addEventListener("click", function (event) {
      var button = event.target.closest(".pg-tab");
      if (!button) {
        return;
      }
      var mode = button.getAttribute("data-mode");
      if (mode === "stock" && !newsState.symbol) {
        toast("个股新闻需从个股详情页「相关新闻」进入，或先在地址栏带 symbol 参数");
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
    if (modeBox) {
      modeBox.querySelectorAll(".pg-tab").forEach(function (button) {
        button.classList.toggle("active", button.getAttribute("data-mode") === newsState.mode);
      });
    }
    document.querySelectorAll("#N-sort .pg-tab").forEach(function (button) {
      button.classList.toggle("active", button.getAttribute("data-sort") === newsState.sort);
    });
    if (regionBox) {
      regionBox.hidden = newsState.mode !== "feed";
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
    var feedTask =
      newsState.mode === "stock"
        ? api("/api/news/stock", { symbol: newsState.symbol, limit: 30 })
        : api("/api/news/ai-feed", {
            boards: newsState.board,
            sort: newsState.sort,
            limit: 40,
            region: newsState.region
          });
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
      setEmpty(body, "板块树暂无数据", function () {
        loadNews(true);
      });
      return;
    }
    body.innerHTML = "";
    var meta = el("div", "pg-card-sub",
      "版本 " + tree.version + " · 一级 " + tree.counts.level1 + " / 二级 " + tree.counts.level2 + " / 末级 " + tree.counts.leaf);
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
      setEmpty(body, "当前板块暂无相关资讯，可换一个板块或稍后刷新", function () {
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
      var chipClass = sentiment.label === "利好" ? "chip up" : sentiment.label === "利空" ? "chip down" : "chip";
      head.appendChild(el("span", chipClass, sentiment.label || "中性"));
      head.appendChild(el("span", "pg-feed-score", "情绪分 " + fmt(sentiment.score, 4) + " · " + (sentiment.strength || "弱")));
      head.appendChild(el("span", "pg-spacer"));
      head.appendChild(el("span", "pg-news-time", relativeTime(item.time)));
      box.appendChild(head);

      var title = el("div", "pg-feed-title", item.title || "无标题");
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
      foot.appendChild(el("span", "pg-card-sub", item.source || "来源未知"));
      (item.channels || []).forEach(function (channel) {
        foot.appendChild(el("span", "pg-news-tag", channel));
      });
      (item.boards || []).forEach(function (board) {
        foot.appendChild(el("span", "pg-news-tag board", board));
      });
      if ((sentiment.events || []).length) {
        foot.appendChild(el("span", "pg-news-tag",
          "事件：" + sentiment.events.map(function (event) { return event.name; }).join("、")));
      }
      var reviewButton = el("button", "pg-btn", "AI 复核");
      reviewButton.type = "button";
      var reviewBox = el("div", "pg-review-box");
      reviewButton.addEventListener("click", function () {
        reviewButton.disabled = true;
        reviewButton.textContent = "复核中…";
        apiPost("/api/news/ai-review", {
          title: item.title,
          content: item.text,
          board: (item.boards || [])[0] || newsState.board,
          path: newsState.mode === "stock" ? "stock" : "feed",
          sentiment: sentiment.label,
          strength: sentiment.strength
        })
          .then(function (data) {
            reviewButton.textContent = "AI 复核";
            reviewButton.disabled = false;
            reviewBox.textContent = "AI（" + (data.model || "已配置模型") + "）：" + (data.review && data.review.raw ? data.review.raw.slice(0, 200) : "已返回");
            reviewBox.className = "pg-review-box ok";
          })
          .catch(function (error) {
            reviewButton.textContent = "AI 复核";
            reviewButton.disabled = false;
            // AI 未配置属预期契约：给出明确引导，不报错、不冒充结果
            reviewBox.textContent =
              error.code === "NOT_CONFIGURED"
                ? "AI 复核不可用：未配置模型接口，请到「设置 → AI 设置」填写 base_url / api_key / model"
                : "AI 复核失败：" + error.message;
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
      setEmpty(body, (payload && payload.error) || "情绪数据暂无", function () {
        loadNews(true);
      });
      return;
    }
    body.innerHTML = "";
    var grid = el("div", "pg-metric-grid");
    [
      ["温度", payload.temperature, payload.temperature >= 65 ? "up" : payload.temperature <= 35 ? "down" : "flat"],
      ["档位", payload.level, "flat"],
      ["利好", payload.positive, "up"],
      ["利空", payload.negative, "down"],
      ["样本", payload.total, "flat"]
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
      body.appendChild(el("div", "pg-card-sub", "驱动事件：" + events));
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
      "口径：" + (payload.caliber || "本地规则引擎") + "；温度 = 50 + 均分×500，裁剪 0–100"));
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
        throw new Error("网络连接失败，请检查网络后重试。");
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
    toolbar.appendChild(el("span", "pg-card-sub", "基金代码"));
    var input = document.createElement("input");
    input.id = "fund-code";
    input.value = fundState.code;
    input.setAttribute("aria-label", "基金代码");
    input.style.cssText =
      "width:120px;background:var(--bg-panel);border:1px solid var(--border);color:var(--text);" +
      "border-radius:5px;padding:4px 8px;font-family:var(--font-mono)";
    toolbar.appendChild(input);
    var go = el("button", "pg-btn", "查看");
    go.type = "button";
    go.id = "fund-go";
    toolbar.appendChild(go);
    toolbar.appendChild(el("span", "pg-spacer"));
    var refresh = el("button", "pg-btn", "刷新本页");
    refresh.type = "button";
    refresh.id = "fund-refresh";
    toolbar.appendChild(refresh);
    container.appendChild(toolbar);

    var grid = el("div", "pg-grid");
    grid.id = "fund-grid";

    var head = buildCard("F-01", "基金概览", "g-12", "场外基金 · T+1 公布净值");
    head.body.id = "f01-body";
    grid.appendChild(head.card);

    var left = el("div", "g-7");
    left.style.display = "flex";
    left.style.flexDirection = "column";
    left.style.gap = "16px";
    var navCard = buildCard("F-02", "净值与回撤", "", "净值走势 + 回撤序列（净值自算）");
    navCard.body.id = "f02-body";
    left.appendChild(navCard.card);
    var perfCard = buildCard("F-03", "阶段业绩", "", "涨幅与同类排名（蛋卷）");
    perfCard.body.id = "f03-body";
    left.appendChild(perfCard.card);
    grid.appendChild(left);

    var right = el("div", "g-5");
    right.style.display = "flex";
    right.style.flexDirection = "column";
    right.style.gap = "16px";
    var infoCard = buildCard("F-05", "档案与分红", "", "蛋卷档案 + 东财分红");
    infoCard.body.id = "f05-body";
    right.appendChild(infoCard.card);
    var posCard = buildCard("F-06", "持仓分布", "", "需登录通道已禁用");
    posCard.body.id = "f06-body";
    right.appendChild(posCard.card);
    grid.appendChild(right);

    var holdCard = buildCard("F-04", "重仓股", "g-12", "东财基金季报（占比/市值）");
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
      toast("请输入基金代码");
      return;
    }
    window.location.hash = "#/fund?code=" + encodeURIComponent(code);
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
          (realtime && realtime.caliber) || "T+1 公布净值", false);
        head.innerHTML = "";
        var main = el("div", "pg-quote-main");
        main.appendChild(el("span", "pg-quote-name", info.name || "无数据"));
        main.appendChild(el("span", "pg-card-sub", info.code || ""));
        main.appendChild(el("span", "pg-quote-price " + pctClass(realtime.day_growth), fmt(realtime.nav, 4)));
        main.appendChild(el("span", pctClass(realtime.day_growth), fmtPct(realtime.day_growth)));
        main.appendChild(el("span", "pg-card-sub", (realtime.nav_date || "") + " 净值"));
        head.appendChild(main);
        var meta = el("div", "pg-quote-meta");
        [
          ["基金全称", info.full_name],
          ["基金经理", info.manager],
          ["成立日期", info.found_date],
          ["最新规模", info.scale],
          ["风险等级", info.risk_level ? "R" + info.risk_level : "无数据"],
          ["申购状态", info.subscribe_status === "0" ? "开放申购" : "限制/暂停"],
          ["赎回状态", info.withdraw_status === "1" ? "开放赎回" : "暂停赎回"]
        ].forEach(function (item) {
          var cell = el("span", "", item[0] + "：");
          cell.appendChild(el("b", "", item[1] || "无数据"));
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
        setEmpty(navBody, (payload.nav && payload.nav.error) || "净值数据暂无", function () { loadFund(); });
      } else {
        setBadges(document.getElementById("F-02"), (payload.nav && payload.nav.source) || "蛋卷",
          (payload.nav && payload.nav.caliber) || "净值历史", false);
        var ordered = navItems.slice().reverse();
        var box = el("div", "pg-chart");
        navBody.innerHTML = "";
        navBody.appendChild(box);
        var chart = mountChart(box);
        var theme = chartTheme();
        if (chart) {
          chart.setOption({
            backgroundColor: "transparent",
            tooltip: { trigger: "axis" },
            legend: { textStyle: { color: theme.text }, top: 0 },
            grid: [
              // 主图与回撤子图留出足够间距，避免回撤纵轴标签压住净值横轴日期（P1-10）
              { left: 62, right: 18, top: 34, height: "40%" },
              { left: 62, right: 18, top: "72%", height: "16%" }
            ],
            xAxis: [
              { type: "category", data: ordered.map(function (i) { return i.date; }),
                axisLabel: { color: theme.text, fontSize: 10, margin: 8,
                  interval: Math.max(1, Math.floor(ordered.length / 6)) },
                axisLine: { lineStyle: { color: theme.border } } },
              { type: "category", gridIndex: 1, data: ddSeries.map(function (i) { return i.date; }),
                axisLabel: { color: theme.text, fontSize: 10, margin: 6,
                  interval: Math.max(1, Math.floor(ddSeries.length / 5)) },
                axisLine: { lineStyle: { color: theme.border } } }
            ],
            yAxis: [
              { scale: true, axisLabel: { color: theme.text, fontSize: 10 },
                splitLine: { lineStyle: { color: theme.border, opacity: 0.4 } } },
              { gridIndex: 1, axisLabel: { color: theme.text, fontSize: 10, margin: 8, formatter: "{value}%" },
                splitLine: { show: false } }
            ],
            series: [
              { name: "单位净值", type: "line", data: ordered.map(function (i) { return i.nav; }),
                showSymbol: false, color: "#b47da0", lineStyle: { width: 1.6 } },
              { name: "回撤%", type: "line", xAxisIndex: 1, yAxisIndex: 1,
                data: ddSeries.map(function (i) { return i.drawdown; }),
                showSymbol: false, areaStyle: { opacity: 0.25 }, color: COLOR_DOWN,
                // 最大回撤点直接标在绘图窗内（与坐标对齐），替代图外文字标注
                markPoint: troughPoint(ddSeries) }
            ]
          });
        }
        var dd = payload.drawdown || {};
        navBody.appendChild(el("div", "pg-card-sub",
          "最大回撤 " + fmt(dd.max_drawdown, 2) + "%（" + (dd.peak_date || "—") + " → " + (dd.trough_date || "—") +
          "），当前回撤 " + fmt(dd.current_drawdown, 2) + "%"));
        navBody.setAttribute("data-state", "default");
      }
    }

    // ---- F-03 阶段业绩 ----
    var perfBody = document.getElementById("f03-body");
    if (perfBody) {
      var perf = payload.performance || {};
      if (perf.error || !(perf.periods || []).length) {
        setEmpty(perfBody, perf.error || "阶段业绩暂无", function () { loadFund(); });
      } else {
        setBadges(document.getElementById("F-03"), perf.source || "蛋卷", perf.caliber || "阶段涨幅", false);
        renderTable(
          perfBody,
          [
            { title: "区间", key: "period", cls: "name" },
            { title: "涨幅%", cls: "num", render: function (row) { return pctNode(row.growth); } },
            { title: "同类排名", key: "rank" }
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
        setEmpty(holdBody, holding.error || "重仓数据暂无", function () { loadFund(); });
      } else {
        setBadges(document.getElementById("F-04"), holding.source || "东财季报",
          (holding.caliber || "") + "；报告期 " + (holding.quarter || "—"), false);
        renderTable(
          holdBody,
          [
            { title: "代码", key: "code", cls: "num" },
            { title: "名称", key: "name", cls: "name" },
            { title: "占净值%", cls: "num", render: function (row) { return fmt(row.ratio, 2); } },
            { title: "持股数(万股)", cls: "num", render: function (row) { return fmt(row.shares, 2); } },
            { title: "持仓市值(万元)", cls: "num", render: function (row) { return fmt(row.value, 2); } }
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
        ["托管银行", info.trustee],
        ["基金公司", info.keeper],
        ["雪球规模", info.xq && info.xq["最新规模"]]
      ].forEach(function (item) {
        var cell = el("span", "", item[0] + "：");
        cell.appendChild(el("b", "", item[1] || "无数据"));
        group.appendChild(cell);
      });
      infoBody.appendChild(group);
      infoBody.appendChild(el("div", "pg-card-sub", "分红记录：" + (div.caliber || "")));
      if ((div.items || []).length) {
        var list = el("div", "pg-news-list");
        div.items.slice(0, 6).forEach(function (row) {
          var line = el("div", "pg-news-item");
          line.appendChild(el("span", "pg-news-title", (row.record_date || "") + " 每10份派 " + (row.per_10 || "—") + " 元"));
          line.appendChild(el("span", "pg-news-time", row.pay_date || ""));
          list.appendChild(line);
        });
        infoBody.appendChild(list);
      } else {
        infoBody.appendChild(el("div", "pg-card-sub", "该基金暂无分红记录"));
      }
      infoBody.setAttribute("data-state", "default");
    }

    // ---- F-06 持仓分布（禁用说明） ----
    var posBody = document.getElementById("f06-body");
    if (posBody) {
      posBody.innerHTML = "";
      var boxState = el("div", "pg-state");
      boxState.appendChild(el("div", "pg-state-title", "持仓分布暂不可用"));
      boxState.appendChild(el("div", "pg-state-hint",
        "蛋卷持仓分布端点需登录（rc=300001），依规范已禁用；可用上方「重仓股」查看季报前十大持仓。"));
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
    form.appendChild(el("span", "pg-card-sub", "新增持仓"));
    var market = document.createElement("select");
    market.id = "pf-market";
    market.style.cssText =
      "background:var(--bg-panel);border:1px solid var(--border);color:var(--text);border-radius:5px;padding:4px 8px";
    [["a", "A股"], ["hk", "港股"], ["us", "美股"], ["fund", "场外基金"]].forEach(function (item) {
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

    form.appendChild(field("pf-symbol", "代码", 100));
    form.appendChild(field("pf-name", "名称（可选）", 140));
    form.appendChild(field("pf-shares", "份额/股数", 100));
    form.appendChild(field("pf-cost", "成本价", 90));
    var add = el("button", "pg-btn", "添加");
    add.type = "button";
    add.id = "pf-add";
    form.appendChild(add);
    form.appendChild(el("span", "pg-spacer"));
    var refresh = el("button", "pg-btn", "刷新");
    refresh.type = "button";
    form.appendChild(refresh);
    container.appendChild(form);

    var grid = el("div", "pg-grid");
    grid.id = "portfolio-grid";
    var summary = buildCard("P-01", "持仓汇总", "g-12", "人民币口径（港美按实时汇率折算）");
    summary.body.id = "p01-body";
    grid.appendChild(summary.card);
    var dist = buildCard("P-04", "市场分布", "g-5", "按人民币市值占比");
    dist.body.id = "p04-body";
    grid.appendChild(dist.card);
    var table = buildCard("P-03", "持仓明细", "g-12", "盈亏按本币与人民币双口径");
    table.body.id = "p03-body";
    grid.appendChild(table.card);
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
        toast("请填写代码与份额/股数");
        return;
      }
      apiSend("POST", "/api/portfolio", payload)
        .then(function () {
          toast("已添加持仓");
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

  /** 渲染持仓页。 */
  function renderPortfolio() {
    var container = document.getElementById(VIEW_IDS.portfolio);
    if (!container.querySelector("#portfolio-grid")) {
      buildPortfolioShell(container);
    }
    loadPortfolio();
  }

  /** 拉取持仓与分析。
   * @returns {*} 处理结果
   */
  function loadPortfolio() {
    var bodies = ["p01-body", "p03-body", "p04-body"].map(function (id) {
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
        setEmpty(p01, "暂无持仓，请在上方添加", function () { loadPortfolio(); });
      } else {
        setBadges(document.getElementById("P-01"), analysis.source || "本地持仓", analysis.caliber || "", false);
        var totals = analysis.totals || {};
        p01.innerHTML = "";
        var grid = el("div", "pg-metric-grid");
        [
          ["市值(人民币)", fmt(totals.cny_market_value, 2), "flat"],
          ["成本(人民币)", fmt(totals.cny_cost, 2), "flat"],
          ["盈亏(人民币)", fmt(totals.cny_pnl, 2), pctClass(totals.cny_pnl)],
          ["持仓条数", (analysis.count || 0) + " 条", "flat"],
          ["汇率", analysis.fx && analysis.fx.USD ? ("USD " + fmt(analysis.fx.USD, 4) + " / HKD " + fmt(analysis.fx.HKD, 4)) : "无数据", "flat"]
        ].forEach(function (item) {
          var cell = el("div", "pg-metric-cell");
          cell.appendChild(el("div", "pg-metric-label", item[0]));
          cell.appendChild(el("div", "pg-metric-value " + item[2], item[1]));
          grid.appendChild(cell);
        });
        p01.appendChild(grid);
        if (analysis.fx_missing) {
          p01.appendChild(el("div", "pg-card-sub", "部分外币持仓缺少汇率，折算值显示为无数据"));
        }
        p01.setAttribute("data-state", "default");
      }
    }

    // ---- P-04 市场分布 ----
    var p04 = document.getElementById("p04-body");
    if (p04) {
      var dist = (analysis.distribution || []).filter(function (item) { return item.value_cny; });
      if (!dist.length) {
        setEmpty(p04, "暂无分布数据", function () { loadPortfolio(); });
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
        setEmpty(p03, "暂无持仓明细", function () { loadPortfolio(); });
      } else {
        var wrap = el("div", "");
        p03.innerHTML = "";
        p03.appendChild(wrap);
        renderTable(
          wrap,
          [
            { title: "市场", key: "market_label" },
            { title: "代码", key: "symbol", cls: "num" },
            { title: "名称", key: "name", cls: "name" },
            { title: "份额/股数", cls: "num", render: function (row) { return fmt(row.shares, 2); } },
            { title: "成本", cls: "num", render: function (row) { return fmt(row.cost, 4); } },
            { title: "现价", cls: "num", render: function (row) { return fmt(row.price, 4); } },
            { title: "市值(本币)", cls: "num", render: function (row) { return fmt(row.market_value, 2); } },
            { title: "盈亏(本币)", cls: "num", render: function (row) { return pctNode(row.pnl); } },
            { title: "盈亏%", cls: "num", render: function (row) { return pctNode(row.pnl_pct); } },
            {
              title: "操作",
              /** 持仓表「操作」列：渲染删除按钮，点击后调用 DELETE 并重载。
               * @param {*} row 单行记录
               * @returns {*} 处理结果
               */
              render: function (row) {
                var button = el("button", "pg-btn", "删除");
                button.type = "button";
                button.addEventListener("click", function (event) {
                  event.stopPropagation();
                  apiSend("DELETE", "/api/portfolio/" + encodeURIComponent(row.id))
                    .then(function () {
                      toast("已删除");
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
  function renderConnectors() {
    var container = document.getElementById(VIEW_IDS.connectors);
    container.innerHTML = "";
    var grid = el("div", "pg-grid");
    var card = buildCard("C-01", "数据源与能力矩阵", "g-12", "按实测能力矩阵，不可用项如实标注");
    card.body.id = "c01-body";
    grid.appendChild(card.card);
    container.appendChild(grid);
    var body = document.getElementById("c01-body");
    setLoading(body);

    api("/api/connectors/status", {})
      .then(function (data) {
        setBadges(card.card, data.source, data.caliber, false);
        body.innerHTML = "";

        var sourceTitle = el("div", "pg-card-sub", "数据源开关");
        body.appendChild(sourceTitle);
        var sourceGrid = el("div", "pg-metric-grid");
        (data.datasources || []).forEach(function (item) {
          var cell = el("div", "pg-metric-cell");
          cell.appendChild(el("div", "pg-metric-label", item.label));
          var state = item.enabled ? (item.active ? "启用" : (item.stage === "installed" ? "已装待接" : "待接入")) : "未启用";
          cell.appendChild(el("div", "pg-metric-value " + (item.enabled ? "flat" : "down"), state));
          sourceGrid.appendChild(cell);
        });
        body.appendChild(sourceGrid);

        var tableTitle = el("div", "pg-card-sub", "三市场能力矩阵（✓ 可用 / — 不可用）");
        body.appendChild(tableTitle);
        var wrap = el("div", "");
        body.appendChild(wrap);
        var features = [
          ["quote", "报价"], ["kline", "K 线"], ["minute", "分时"],
          ["index", "指数"], ["stock_news", "个股新闻"], ["sector", "板块专项"]
        ];
        var rows = features.map(function (pair) {
          var row = { feature: pair[1] };
          ["a", "hk", "us"].forEach(function (market) {
            var cell = ((data.markets || {})[market] || {})[pair[0]] || {};
            row[market] = cell.available ? "✓ " + (cell.source || "") : "— 暂不可用";
          });
          return row;
        });
        renderTable(wrap, [
          { title: "能力", key: "feature", cls: "name" },
          { title: "A股", key: "a" },
          { title: "港股", key: "hk" },
          { title: "美股", key: "us" }
        ], rows, null, null);

        if ((data.gaps || []).length) {
          body.appendChild(el("div", "pg-card-sub", "已确认缺口（D3 决策：不编造数据）"));
          var list = el("ul", "pg-news-list");
          data.gaps.forEach(function (gap) {
            var li = el("li", "pg-news-item");
            li.appendChild(el("span", "pg-news-title", gap.market_label + " · " + gap.feature_label));
            li.appendChild(el("span", "chip down", "暂不可用"));
            li.appendChild(el("span", "pg-news-time", gap.reason));
            list.appendChild(li);
          });
          body.appendChild(list);
        }
        body.setAttribute("data-state", "default");
      })
      .catch(function (error) {
        setError(body, error.message, function () {
          renderConnectors();
        });
      });
  }

  // =============================================================
  // 十二、#/yjb 养基宝绑定（阶段 7）
  // 合规告知书 → 扫码绑定（2s 轮询）→ 增强开关 → 解绑；token 前端不可见
  // =============================================================
  var yjbTimer = null;

  /** 渲染养基宝页。 */
  function renderYjb() {
    var container = document.getElementById(VIEW_IDS.yjb);
    container.innerHTML = "";
    var grid = el("div", "pg-grid");
    var g1 = buildCard("Y-01", "合规告知书", "g-4", "首次启用前必须确认");
    g1.body.id = "y01-body";
    var g2 = buildCard("Y-02", "连接状态", "g-4", "token 仅后端加密存储");
    g2.body.id = "y02-body";
    var g3 = buildCard("Y-03", "扫码绑定", "g-4", "二维码 2 秒轮询");
    g3.body.id = "y03-body";
    var g4 = buildCard("Y-04", "盘中估值", "g-12", "非官方·拟合估算·仅供参考·不构成投资建议");
    g4.body.id = "y04-body";
    [g1, g2, g3, g4].forEach(function (item) { grid.appendChild(item.card); });
    container.appendChild(grid);
    var body = document.getElementById("y04-body");
    body.innerHTML = "";
    var toolbar = el("div", "page-toolbar");
    toolbar.appendChild(el("span", "pg-card-sub", "基金代码"));
    var input = document.createElement("input");
    input.id = "yjb-code";
    input.value = "017811";
    input.style.cssText = "width:110px;background:var(--bg-panel);border:1px solid var(--border);color:var(--text);border-radius:5px;padding:4px 8px;font-family:var(--font-mono)";
    toolbar.appendChild(input);
    var query = el("button", "pg-btn", "查询估值");
    query.type = "button";
    query.addEventListener("click", loadYjbEstimate);
    toolbar.appendChild(query);
    toolbar.appendChild(el("span", "pg-spacer"));
    toolbar.appendChild(el("span", "badge-src state-degraded", "非官方通道"));
    body.appendChild(toolbar);
    body.appendChild(el("div", "pg-card-sub", "本区域为拟合估算，不构成投资建议；可随时在左侧关闭增强通道回退 T+1 净值。"));
    var result = el("div", "");
    result.id = "yjb-result";
    body.appendChild(result);
    loadYjbStatus();
  }

  /** 加载连接器状态与告知书。 */
  function loadYjbStatus() {
    api("/api/fund/yjb/status", {})
      .then(function (data) {
        paintYjbConsent(data);
        paintYjbStatus(data);
        paintYjbBind(data);
      })
      .catch(function (error) {
        setError(document.getElementById("y02-body"), error.message, renderYjb);
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
    var tip = el("span", "chip " + (accepted ? "" : "down"), accepted ? "已确认" : "未确认");
    row.appendChild(tip);
    if (!accepted) {
      var agree = el("button", "pg-btn primary", "我已阅读并同意");
      agree.type = "button";
      agree.addEventListener("click", function () {
        apiSend("POST", "/api/fund/yjb/consent", { accepted: true }).then(function () {
          toast("已记录确认");
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
    [["绑定", data.bound ? "已绑定" : "未绑定"], ["增强通道", data.enabled ? "已开启" : "已关闭"],
     ["签名密钥", data.secret_configured ? "已配置" : "未配置"],
     ["安全存储", data.secure_storage ? "DPAPI" : "不可用"]].forEach(function (pair) {
      var cell = el("div", "pg-metric-cell");
      cell.appendChild(el("div", "pg-metric-label", pair[0]));
      cell.appendChild(el("div", "pg-metric-value", pair[1]));
      grid.appendChild(cell);
    });
    body.appendChild(grid);
    var row = el("div", "page-toolbar");
    var toggle = el("button", "pg-btn", data.enabled ? "一键关闭增强通道" : "开启增强通道");
    toggle.type = "button";
    toggle.addEventListener("click", function () {
      apiSend("POST", "/api/fund/yjb/toggle", { enabled: !data.enabled })
        .then(function () { toast(data.enabled ? "已关闭，回退 T+1 净值" : "已开启"); loadYjbStatus(); })
        .catch(function (error) { toast(error.message); });
    });
    row.appendChild(toggle);
    if (data.bound) {
      var unbind = el("button", "pg-btn", "解绑");
      unbind.type = "button";
      unbind.addEventListener("click", function () {
        apiSend("POST", "/api/fund/yjb/unbind", {}).then(function () {
          toast("已解绑"); loadYjbStatus();
        });
      });
      row.appendChild(unbind);
    }
    body.appendChild(row);
    body.appendChild(el("div", "pg-card-sub", data.notice || ""));
    body.setAttribute("data-state", "default");
  }

  /** 二维码与轮询。
   * @param {Object} data 数据主体（接口 data 段）
   */
  function paintYjbBind(data) {
    var body = document.getElementById("y03-body");
    body.innerHTML = "";
    if (!data.secret_configured) {
      var hint = el("div", "pg-state");
      hint.appendChild(el("div", "pg-state-title", "扫码绑定不可用"));
      hint.appendChild(el("div", "pg-state-hint",
        "未配置养基宝签名密钥（YJB_SECRET_NEW/OLD），按规范不内置签名盐；可继续使用拟合估算与 T+1 净值。"));
      body.appendChild(hint);
      body.setAttribute("data-state", "empty");
      return;
    }
    var button = el("button", "pg-btn", "获取二维码");
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
        var state = el("div", "pg-card-sub", "等待扫码…");
        state.id = "yjb-poll-state";
        box.appendChild(state);
        var attempts = 0;
        window.clearInterval(yjbTimer);
        yjbTimer = window.setInterval(function () {
          attempts += 1;
          api("/api/fund/yjb/poll", { session_id: session.session_id }).then(function (status) {
            state.textContent = status.message || status.state;
            if (status.state === "success") {
              window.clearInterval(yjbTimer);
              apiSend("POST", "/api/fund/yjb/bind", { session_id: session.session_id })
                .then(function () { toast("绑定成功"); loadYjbStatus(); })
                .catch(function (error) { state.textContent = "绑定失败：" + error.message; });
            } else if (status.state === "expired" || status.state === "timeout") {
              window.clearInterval(yjbTimer);
            }
          });
          if (attempts > 60) {
            window.clearInterval(yjbTimer);
          }
        }, 2000);
      }).catch(function (error) {
        box.textContent = error.message;
      });
    });
    body.appendChild(button);
    body.appendChild(box);
    body.setAttribute("data-state", "default");
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
        [["最新估值", fmt(data.nav, 4)], ["日涨幅", fmtPct(data.day_growth)],
         ["数据源", data.source || t("value.none")], ["覆盖度", data.coverage ? fmt(data.coverage, 2) + "%" : "无数据"]]
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
    if (!container.querySelector("#ch-tabs")) {
      buildChartingShell(container);
    }
    document.getElementById("ch-symbol").value = state.charting.symbol;
    document.getElementById("ch-market").value = state.charting.market;
    document.getElementById("ch-period").value = state.charting.period;
    document.getElementById("ch-adjust").value = state.charting.adjust;
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
    setLoading(quoteBody);
    setLoading(chartBody);
    Promise.all([
      api("/api/market/kline", { symbol: symbol, market: market, period: state.charting.period, adjust: state.charting.adjust }),
      api("/api/market/minute", { symbol: symbol, market: market, period: "1min" }),
      api("/api/market/quote", { symbol: symbol, market: market })
    ])
      .then(function (result) {
        // /api/market/quote 的 data 为 {quote:{...}} 嵌套，先解包再入缓存
        var quoteData = (result[2] && result[2].quote) || {};
        chartingCache = { kline: result[0], minute: result[1], quote: quoteData, lines: [] };
        paintChartingQuote(quoteData);
        paintChartingChart(result[0], result[1]);
      })
      .catch(function (error) {
        setError(quoteBody, error.message, loadCharting);
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
    main.appendChild(el("span", "pg-card-sub", quote.quote_time || ""));
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
    chart.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      legend: { textStyle: { color: theme.text }, top: 0 },
      grid: [
        { left: 56, right: 16, top: 28, height: "42%" },
        { left: 56, right: 16, top: "74%", height: "7%" },
        { left: 56, right: 16, top: "83%", height: "8%" },
        { left: 56, right: 16, top: "93%", height: "6%" }
      ],
      xAxis: [
        { type: "category", data: dates, axisLabel: { color: theme.text }, axisLine: { lineStyle: { color: theme.border } } },
        { type: "category", gridIndex: 1, data: dates, axisLabel: { show: false }, axisLine: { lineStyle: { color: theme.border } } },
        { type: "category", gridIndex: 2, data: dates, axisLabel: { show: false }, axisLine: { lineStyle: { color: theme.border } } },
        { type: "category", gridIndex: 3, data: dates, axisLabel: { show: false }, axisLine: { lineStyle: { color: theme.border } } }
      ],
      yAxis: [
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
    });
    // 画线交互：开启后点击主图，按像素坐标换算价格，添加水平参考线（本地标注不写回数据）
    chart.off("click");
    if (state.charting.draw) {
      chart.on("click", function (params) {
        var point = chart.convertFromPixel({ yAxisIndex: 0 }, [params.event.offsetX, params.event.offsetY]);
        if (point && typeof point[1] === "number") {
          chartingCache.lines.push(Number(point[1].toFixed(2)));
          paintChartingChart(chartingCache.kline, chartingCache.minute);
          toast(t("chart.draw.added", { price: Number(point[1].toFixed(2)) }));
        }
      });
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

    // —— AI 设置卡：base_url / api_key / model，保存走 POST /api/ai/config ——
    var aiCard = buildCard("ST-AI", t("set.card.ai"), "g-12", t("set.card.aiSub"));
    aiCard.body.id = "set-ai-body";
    grid.appendChild(aiCard.card);

    // —— 主题与缓存卡：主题切换开关 + 缓存清理入口（唯一定位在连接器页） ——
    var themeCard = buildCard("ST-TH", t("set.card.theme"), "g-6", t("set.card.themeSub"));
    themeCard.body.id = "set-theme-body";
    grid.appendChild(themeCard.card);

    // —— 关于卡：版本/构建标识/数据口径声明 ——
    var aboutCard = buildCard("ST-AB", t("set.card.about"), "g-6", t("set.card.aboutSub"));
    aboutCard.body.id = "set-about-body";
    grid.appendChild(aboutCard.card);

    container.appendChild(grid);
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

  /** 渲染 AI 设置卡：回显当前配置（掩码）+ 保存。 */
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
        body.appendChild(form);
        // 回显：base_url/model 明文（非敏感），api_key 只显示掩码状态
        document.getElementById("set-base-url").value = data.base_url || "";
        document.getElementById("set-model").value = data.model || "";
        var status = el("div", "pg-card-sub");
        status.id = "set-ai-status";
        status.textContent = data.configured
          ? t("set.ai.configured", { model: data.model || t("set.ai.modelEmpty"), key: data.api_key || t("set.ai.keyMasked") })
          : t("set.ai.notConfigured");
        body.appendChild(status);
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
      })
      .catch(function (error) {
        setError(body, error.message, paintSettingsAI);
      });
  }

  /** 渲染主题与缓存卡：主题切换 + 缓存清理引导（唯一定位在连接器页）。 */
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

    var cacheRow = el("div", "page-toolbar");
    cacheRow.appendChild(el("span", "pg-toolbar-label", t("set.cache.label")));
    var jump = el("button", "pg-btn", t("set.cache.jump"));
    jump.type = "button";
    jump.addEventListener("click", function () {
      window.location.hash = "#/connectors";
    });
    cacheRow.appendChild(jump);
    body.appendChild(cacheRow);
    body.setAttribute("data-state", "default");
  }

  /** 渲染关于卡：版本/构建标识（/api/health）+ 合规声明。 */
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
        body.appendChild(el("div", "pg-card-sub", t("set.about.disclaimer")));
        body.setAttribute("data-state", "default");
      })
      .catch(function (error) {
        setError(body, error.message, paintSettingsAbout);
      });
  }

  /** 渲染置页入口：骨架 + 三卡数据。 */
  function renderSettings() {
    var container = document.getElementById(VIEW_IDS.settings);
    if (!container.querySelector("#set-ai-body")) {
      buildSettingsShell(container);
    }
    paintSettingsAI();
    paintSettingsTheme();
    paintSettingsAbout();
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

  // 供工具栏「刷新当前视图」调用：按当前 hash 重新走一遍路由（P2-03）
  window.Pages = { reload: route };

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
