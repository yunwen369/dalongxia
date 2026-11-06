/* =============================================================
 * OpenBB 中文投研工作台 · pages/screener.js
 * 全市场筛选器 M9
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
  var fmtPct = P.fmtPct;
  var state = P.state;
  var t = P.t;
  var toast = P.toast;

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
    ["ocf_yi", "经营现金流净额", "亿元", "finance"],
    ["dividend_yield", "股息率（近一年）", "%", "dividend"]
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
    { key: "chg", title: "涨跌幅", num: true, render: function (row) { return el("span", P.pctClass(row.chg), fmtPct(row.chg)); } },
    { key: "pe", title: "PE", num: true, render: function (row) { return fmt(row.pe, 2); } },
    { key: "pb", title: "PB", num: true, render: function (row) { return fmt(row.pb, 2); } },
    { key: "mktcap", title: "总市值(亿)", num: true, render: function (row) { return fmt(row.mktcap, 0); } },
    { key: "roe3y_min", title: "ROE3年最弱", num: true, render: function (row) { return fmt(row.roe3y_min, 2); } },
    { key: "gross", title: "毛利率", num: true, render: function (row) { return fmt(row.gross, 2); } },
    { key: "rev_yoy", title: "营收同比", num: true, render: function (row) { return fmt(row.rev_yoy, 2); } },
    { key: "np_yoy", title: "净利同比", num: true, render: function (row) { return fmt(row.np_yoy, 2); } },
    { key: "debt_ratio", title: "资产负债率", num: true, render: function (row) { return fmt(row.debt_ratio, 2); } },
    { key: "ocf_yi", title: "经营现金流(亿)", num: true, render: function (row) { return fmt(row.ocf_yi, 2); } },
    // R9：股息率=快照列。0.00%=近一年真实未派息；—=分红通道缺失（语义区分，不冒充）
    { key: "dividend_yield", title: t("scr.col.dividend"), num: true, render: function (row) {
        return row.dividend_yield === null || row.dividend_yield === undefined
          ? "—" : fmt(row.dividend_yield, 2) + "%";
      } },
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
    var snapCard = P.buildCard("M9-01", t("scr.snapTitle"), "g-5", t("scr.snapSub"));
    snapCard.body.id = "m901-body";
    grid.appendChild(snapCard.card);

    // M9-02 条件构建器
    var condCard = P.buildCard("M9-02", t("scr.condTitle"), "g-7", t("scr.condSub"));
    condCard.body.id = "m902-body";
    grid.appendChild(condCard.card);

    page.appendChild(grid);

    // M9-03 结果表（全宽卡，栅格外）
    var resultCard = P.buildCard("M9-03", t("scr.resultTitle"), "", t("scr.resultSub"));
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
        P.setBadges(card, t("scr.snapSource"), data.caliber || "");
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
      P.setError(body, error.message || t("state.errorHint"), paintScreenerStatus);
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
      P.setBadges(card, t("scr.snapSource"),
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
    P.renderTable(table, SCREENER_COLS, items, {
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

  // ---- 导出到 window.Pages（供兄弟模块与 init 使用） ----
  P.SCREENER_COLS = SCREENER_COLS;
  P.SCREENER_FACTORS = SCREENER_FACTORS;
  P.paintScreenerConditions = paintScreenerConditions;
  P.paintScreenerResult = paintScreenerResult;
  P.paintScreenerStatus = paintScreenerStatus;
  P.renderScreener = renderScreener;
  P.runScreenerPost = runScreenerPost;
})(window.Pages = window.Pages || {});
