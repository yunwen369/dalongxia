/* =============================================================
 * OpenBB 中文投研工作台 · pages/fund.js
 * 场外基金详情
 * 由 tools/split_pages.py 从 pages.js 机械切分生成（R17），逻辑零改动；
 * 重新生成请改 pages.js 后重跑脚本，勿直接编辑本文件的结构性段落。
 * ============================================================= */
(function (P) {
  "use strict";

  // ---- 跨模块依赖（core/兄弟模块导出，运行时解析） ----
  var COLOR_DOWN = P.COLOR_DOWN;
  var VIEW_IDS = P.VIEW_IDS;
  var api = P.api;
  var el = P.el;
  var fmt = P.fmt;
  var fmtPct = P.fmtPct;
  var state = P.state;
  var t = P.t;
  var toast = P.toast;

  // =============================================================
  // 九、#/fund 场外基金详情（批次 5.3）
  // 结构：F-01 基金大卡 / F-02 净值走势 / F-03 阶段业绩 / F-04 重仓股
  //       F-05 档案与分红 / F-06 持仓分布（东财 F10 行业配置）
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

    var head = P.buildCard("F-01", t("fund.overviewCard"), "g-12", t("fund.overviewSub"));
    head.body.id = "f01-body";
    grid.appendChild(head.card);

    var left = el("div", "g-7");
    left.style.display = "flex";
    left.style.flexDirection = "column";
    left.style.gap = "16px";
    var navCard = P.buildCard("F-02", t("fund.navCard"), "", t("fund.navSub"));
    navCard.body.id = "f02-body";
    left.appendChild(navCard.card);
    var perfCard = P.buildCard("F-03", t("fund.perfCard"), "", t("fund.perfSub"));
    perfCard.body.id = "f03-body";
    left.appendChild(perfCard.card);
    grid.appendChild(left);

    var right = el("div", "g-5");
    right.style.display = "flex";
    right.style.flexDirection = "column";
    right.style.gap = "16px";
    var infoCard = P.buildCard("F-05", t("fund.infoCard"), "", t("fund.infoSub"));
    infoCard.body.id = "f05-body";
    right.appendChild(infoCard.card);
    var posCard = P.buildCard("F-06", t("fund.holdCard"), "", t("fund.holdSub"));
    posCard.body.id = "f06-body";
    right.appendChild(posCard.card);
    grid.appendChild(right);

    var holdCard = P.buildCard("F-04", t("fund.holdingCard"), "g-12", t("fund.holdingSub"));
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
        P.setLoading(body);
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
        P.setError(head, info.error, function () { loadFund(); });
      } else {
        P.setBadges(headCard, (realtime && realtime.source) || "蛋卷",
          (realtime && realtime.caliber) || t("fund.caliber.navFallback"), false);
        head.innerHTML = "";
        var main = el("div", "pg-quote-main");
        main.appendChild(el("span", "pg-quote-name", info.name || t("value.none")));
        main.appendChild(el("span", "pg-card-sub", info.code || ""));
        main.appendChild(el("span", "pg-quote-price " + P.pctClass(realtime.day_growth), fmt(realtime.nav, 4)));
        main.appendChild(el("span", P.pctClass(realtime.day_growth), fmtPct(realtime.day_growth)));
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
        P.setEmpty(navBody, (payload.nav && payload.nav.error) || t("fund.empty.nav"), function () { loadFund(); });
      } else {
        P.setBadges(document.getElementById("F-02"), (payload.nav && payload.nav.source) || "蛋卷",
          (payload.nav && payload.nav.caliber) || t("fund.caliber.nav"), false);
        var ordered = navItems.slice().reverse();
        var box = el("div", "pg-chart");
        navBody.innerHTML = "";
        navBody.appendChild(box);
        var chart = P.mountChart(box);
        var theme = P.chartTheme();
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
                markPoint: P.troughPoint(ddSeries) }
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
        P.setEmpty(perfBody, perf.error || t("fund.empty.perf"), function () { loadFund(); });
      } else {
        P.setBadges(document.getElementById("F-03"), perf.source || "蛋卷", perf.caliber || t("fund.caliber.perf"), false);
        P.renderTable(
          perfBody,
          [
            { title: t("fund.perf.col.period"), key: "period", cls: "name" },
            { title: t("fund.perf.col.growth"), cls: "num", render: function (row) { return P.pctNode(row.growth); } },
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
        P.setEmpty(holdBody, holding.error || t("fund.empty.holding"), function () { loadFund(); });
      } else {
        P.setBadges(document.getElementById("F-04"), holding.source || "东财季报",
          (holding.caliber || "") + t("stock.profile.dataCaliber") + (holding.quarter || t("value.dash")), false);
        P.renderTable(
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
    if (typeof P.paintFundExtras === "function") {
      P.paintFundExtras(payload);  // FD-01 四分位 / FD-02 回撤分析 / FD-04 经理入口
    }

  }

  // ---- 导出到 window.Pages（供兄弟模块与 init 使用） ----
  P.apiSend = apiSend;
  P.applyFundInput = applyFundInput;
  P.bindFundSearch = bindFundSearch;
  P.buildFundShell = buildFundShell;
  P.fundState = fundState;
  P.loadFund = loadFund;
  P.paintFund = paintFund;
  P.renderFund = renderFund;
})(window.Pages = window.Pages || {});
