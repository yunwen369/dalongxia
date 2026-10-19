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

  // 各视图容器（由 index.html 提供）
  var VIEW_IDS = {
    sectors: "page-sectors",
    stock: "page-stock",
    "sector-val": "page-sector-val",
    news: "page-news"
  };

  // 图表实例登记表：路由切换时统一 dispose，窗口 resize 时统一 resize
  var charts = [];

  // 页面会话状态：市场/口径/标的，用于 Tab 切换时保留上下文
  var state = {
    sectors: { market: "cn", kind: "industry" },
    stock: { symbol: "688012", market: "a", tab: "kline", finTab: "summary" },
    sectorVal: { board: "", market: "cn" }
  };

  // =============================================================
  // 一、通用工具
  // =============================================================
  /** 创建元素：cls 为类名，text 为文本（一律用 textContent，避免注入）。 */
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

  /** 数字格式化：空值返回「无数据」，其余保留指定小数并加千分位。 */
  function fmt(value, digits) {
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

  /** 百分比格式化：带正负号与 %，空值「无数据」。 */
  function fmtPct(value) {
    if (value === null || value === undefined) {
      return "无数据";
    }
    var num = Number(value);
    if (isNaN(num)) {
      return "无数据";
    }
    return (num > 0 ? "+" : "") + num.toFixed(2) + "%";
  }

  /** 涨跌样式类：正红、负绿、零灰。 */
  function pctClass(value) {
    if (value === null || value === undefined || Number(value) === 0) {
      return "flat";
    }
    return Number(value) > 0 ? "up" : "down";
  }

  /** 亿/万元换算：接口返回元时转亿元展示。 */
  function yi(value) {
    if (value === null || value === undefined) {
      return null;
    }
    return Number(value) / 1e8;
  }

  /** 相对时间：把 YYYY-MM-DD HH:MM:SS 转成「x 分钟前 / x 小时前 / 日期」。 */
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

  /** 统一请求：返回 data；失败抛出带中文文案的错误。 */
  function api(path, params) {
    var query = new URLSearchParams();
    Object.keys(params || {}).forEach(function (key) {
      if (params[key] !== undefined && params[key] !== null && params[key] !== "") {
        query.append(key, params[key]);
      }
    });
    var url = path + (query.toString() ? "?" + query.toString() : "");
    return fetch(url, { cache: "no-store" })
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

  /** 轻提示（复用页面底部 #toast 元素）。 */
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
  /** 加载态：组件级骨架屏，区块内禁点。 */
  function setLoading(body) {
    body.innerHTML = "";
    var box = el("div", "pg-skeleton");
    for (var i = 0; i < 5; i += 1) {
      box.appendChild(el("span"));
    }
    body.appendChild(box);
    body.setAttribute("data-state", "loading");
  }

  /** 空态：就地替换内容区，提供刷新按钮（§7 空态规范）。 */
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

  /** 错误态：中文原因 + 重试；与空态严格区分（§7）。 */
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

  /** 双徽章：数据源 + 口径；降级命中时徽章显示实际源（§8）。 */
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

  /** 统一占位卡结构：标题 + 双徽章位 + 内容区。 */
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

  /** 释放并重建图表实例（避免路由切换后残留旧图）。 */
  function mountChart(node) {
    if (!window.echarts || !node) {
      return null;
    }
    var instance = window.echarts.init(node, null, { renderer: "canvas" });
    charts.push(instance);
    return instance;
  }

  /** 统一图表配色（跟随主题变量）。 */
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

  /** 生成带涨跌色的文本节点。 */
  function pctNode(value) {
    var span = el("span", pctClass(value), fmtPct(value));
    return span;
  }

  // =============================================================
  // 四、路由
  // =============================================================
  /** 解析 #/view?k=v 形式的 hash。 */
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

  /** 切换视图显隐（只显示目标视图，其余隐藏，避免旧数据残留）。 */
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
      news: "新闻资讯"
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
      renderNewsPlaceholder(parsed.params);
    }
  }

  /** 拦截主菜单点击：三视图走 hash 路由，其余视图保持 app.js 原行为。 */
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

  /** 构建板块页骨架（只建一次，之后仅更新内容）。 */
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

  /** 渲染板块页。 */
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

  /** 拉取并渲染板块页全部区块。 */
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

  /** 渲染板块页（默认态）。 */
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
          { title: "超大单(亿元)", cls: "num", render: function (row) { return fmt(row.super_big); } },
          { title: "大单(亿元)", cls: "num", render: function (row) { return fmt(row.big); } }
        ],
        flowRows,
        null,
        null
      );
    }
  }

  /** 摘要卡：领涨/领跌/净流入 Top3 + 集中度。 */
  function paintSummary(body, summary) {
    body.innerHTML = "";
    if (!summary || (!summary.gainers.length && !summary.losers.length)) {
      setEmpty(body, "暂无数据，点击刷新", function () {
        loadSectors(true);
      });
      return;
    }
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

  /** 跳转 #1/#9：板块 → 单板块估值。 */
  function jumpToSectorVal(board, market) {
    window.location.hash =
      "#/sector-val?board=" + encodeURIComponent(board) + "&market=" + (market || "cn");
  }

  // =============================================================
  // 六、#/stock 个股详情（ST-01~ST-10）
  // =============================================================
  /** 构建个股页骨架。 */
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

  /** 渲染个股页。 */
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

  /** 拉取并渲染个股页（面板 + 概况 + 股东 + 财务摘要/趋势）。 */
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
      api("/api/stock/profile", { symbol: symbol, market: market })
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
        stockCache = { key: key, panel: result[0] };
        paintStock({
          panel: result[0],
          profile: result[1],
          holders: result[2],
          summary: result[3],
          trend: result[4]
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

  /** 渲染报价大卡（ST-01）。 */
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

  /** 渲染 K 线或分时（ST-02 / ST-03 互斥）。 */
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

  /** 渲染资金分布环形图（ST-04）。 */
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

  /** 渲染相关新闻（ST-05）。 */
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

  /** 渲染舆情面板（ST-07）：由新闻情绪标签聚合，确定性规则非 AI。 */
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

  /** 渲染机构预测（ST-06）。 */
  function paintForecast(panel) {
    var body = document.getElementById("st06-body");
    var card = document.getElementById("ST-06");
    if (!body) {
      return;
    }
    var forecast = panel.forecast;
    if (!forecast) {
      setEmpty(body, "机构预测暂无", function () {
        loadStock(true);
      });
      return;
    }
    setBadges(card, "同花顺", forecast.caliber_note || "评级分布+目标价", false);
    body.innerHTML = "";
    var targetText =
      forecast.target_low === null
        ? "无数据"
        : fmt(forecast.target_low, 2) + " ~ " + fmt(forecast.target_high, 2) + " 元（均值 " + fmt(forecast.target_avg, 2) + "）";
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
    body.setAttribute("data-state", "default");
  }

  /** 渲染公司概况（ST-08）：基本信息 / 股本市值 / 机构与基金持有 / 最新财报摘要 四组。 */
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

  /** 渲染财务页签区（ST-09）：关键指标 / 趋势 / 三表 / 指标明细。 */
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

  /** 渲染财务页签内容。 */
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

  /** 渲染财务表（期数横向）。 */
  function renderFinTable(pane, data) {
    var holder = el("div", "");
    var columns = [{ title: "项目", key: "name", cls: "name" }].concat(
      (data.periods || []).map(function (period, index) {
        return {
          title: period,
          cls: "num",
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

  /** 渲染十大股东（ST-10）。 */
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

  /** 渲染整页个股数据。 */
  function paintStock(payload) {
    paintQuote(payload.panel);
    syncStockTabs();
    paintStockChart();
    paintFundDist(payload.panel);
    paintNews(payload.panel);
    paintSentiment(payload.panel);
    paintForecast(payload.panel);
    paintProfile(payload.profile, payload.holders, payload.summary);
    paintFinancial({ summary: payload.summary, trend: payload.trend });
    paintHolders(payload.holders);
  }

  // =============================================================
  // 七、#/sector-val 单板块估值（V-01~V-04）
  // =============================================================
  /** 构建估值页骨架。 */
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

  /** 渲染估值页。 */
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

  /** 拉取并渲染估值页。 */
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

  /** 渲染估值页数据。 */
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

  /** 更新 V-04 占位卡的参数提示（不做任何数据请求）。 */
  function updateValPlaceholder(board, market) {
    var hint = document.getElementById("v04-hint");
    if (hint) {
      hint.textContent =
        "已预留参数：board=" + (board || "未选择") + "，market=" + market + "；本批不做 AI 判读";
    }
  }

  // =============================================================
  // 八、#/news 占位（5.2 落地，本批仅承接跳转参数）
  // =============================================================
  /** 新闻页占位：显示跳转带来的 symbol 参数，不报错、不白屏。 */
  function renderNewsPlaceholder(params) {
    var container = document.getElementById(VIEW_IDS.news);
    container.innerHTML = "";
    var grid = el("div", "pg-grid");
    var card = buildCard("N-01", "新闻资讯", "g-12", "5.2 阶段落地");
    var box = el("div", "pg-state");
    box.appendChild(el("div", "pg-state-title", "新闻页将在批次 5.2 上线"));
    box.appendChild(
      el("div", "pg-state-hint", "已接收参数：" + (params.symbol ? "symbol=" + params.symbol : "无") + "；当前不发起任何新闻请求")
    );
    card.body.appendChild(box);
    grid.appendChild(card.card);
    container.appendChild(grid);
  }

  // =============================================================
  // 九、初始化
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
