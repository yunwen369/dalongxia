/* =============================================================
 * OpenBB 中文投研工作台 · pages/valuation.js
 * 估值分位 M2
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
    var idx = P.buildCard("VL-01", t("valuation.card.index"), "g-12", t("valuation.card.indexSub"));
    idx.body.id = "vl01-body";
    grid.appendChild(idx.card);
    var market = P.buildCard("VL-02", t("valuation.card.market"), "g-5", t("valuation.card.marketSub"));
    market.body.id = "vl02-body";
    grid.appendChild(market.card);
    var industry = P.buildCard("VL-03", t("valuation.card.industry"), "g-7", t("valuation.card.industrySub"));
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
    P.setLoading(body1);
    P.setLoading(body2);
    P.setLoading(body3);
    api("/api/valuation/percentile", { force: force ? 1 : "" }).then(function (data) {
      var card = document.getElementById("VL-01");
      P.setBadges(card, data.source, data.caliber);

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
        P.renderTable(tableHost, [
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
      P.setError(body1, error.message || t("state.errorHint"), function () { loadValuation(true); });
      P.setError(body2, error.message || t("state.errorHint"), function () { loadValuation(true); });
      P.setError(body3, error.message || t("state.errorHint"), function () { loadValuation(true); });
    });
  }

  // ---- 导出到 window.Pages（供兄弟模块与 init 使用） ----
  P.loadValuation = loadValuation;
  P.renderValuation = renderValuation;
})(window.Pages = window.Pages || {});
