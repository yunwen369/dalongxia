/* =============================================================
 * OpenBB 中文投研工作台 · pages/macro.js
 * 宏观与周期 M1
 * 由 tools/split_pages.py 从 pages.js 机械切分生成（R17），逻辑零改动；
 * 重新生成请改 pages.js 后重跑脚本，勿直接编辑本文件的结构性段落。
 * ============================================================= */
(function (P) {
  "use strict";

  // ---- 跨模块依赖（core/兄弟模块导出，运行时解析） ----
  var VIEW_IDS = P.VIEW_IDS;
  var api = P.api;
  var el = P.el;
  var fmt = P.fmt;
  var state = P.state;
  var t = P.t;

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
    var kpi = P.buildCard("MC-01", t("macro.card.kpi"), "g-12", t("macro.card.kpiSub"));
    kpi.body.id = "mc01-body";
    grid.appendChild(kpi.card);
    var quad = P.buildCard("MC-02", t("macro.card.quad"), "g-5", t("macro.card.quadSub"));
    quad.body.id = "mc02-body";
    grid.appendChild(quad.card);
    var chart = P.buildCard("MC-03", t("macro.card.chart"), "g-7", t("macro.card.chartSub"));
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
    P.setLoading(body);
    P.setLoading(quadBody);
    P.setLoading(chartBody);
    api("/api/macro/overview", { force: force ? 1 : "" }).then(function (data) {
      var card = document.getElementById("MC-01");
      P.setBadges(card, data.source, data.caliber);

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
      var chart = P.mountChart(box);
      var theme = P.chartTheme();
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
      P.setError(body, error.message || t("state.errorHint"), function () { loadMacro(true); });
      P.setError(quadBody, error.message || t("state.errorHint"), function () { loadMacro(true); });
      P.setError(chartBody, error.message || t("state.errorHint"), function () { loadMacro(true); });
    });
  }

  // ---- 导出到 window.Pages（供兄弟模块与 init 使用） ----
  P.loadMacro = loadMacro;
  P.renderMacro = renderMacro;
})(window.Pages = window.Pages || {});
