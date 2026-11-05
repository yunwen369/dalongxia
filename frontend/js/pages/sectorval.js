/* =============================================================
 * OpenBB 中文投研工作台 · pages/sectorval.js
 * 单板块估值 V-01~V-04
 * 由 tools/split_pages.py 从 pages.js 机械切分生成（R17），逻辑零改动；
 * 重新生成请改 pages.js 后重跑脚本，勿直接编辑本文件的结构性段落。
 * ============================================================= */
(function (P) {
  "use strict";

  // ---- 跨模块依赖（core/兄弟模块导出，运行时解析） ----
  var COLOR_DOWN = P.COLOR_DOWN;
  var COLOR_UP = P.COLOR_UP;
  var VIEW_IDS = P.VIEW_IDS;
  var api = P.api;
  var el = P.el;
  var fmt = P.fmt;
  var fmtPct = P.fmtPct;
  var state = P.state;
  var t = P.t;

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

    var kline = P.buildCard("V-01", t("stock.kv.chartBoard"), "", t("val.chartTitle"));
    kline.body.id = "v01-body";
    kline.body.classList.add("tall");
    left.appendChild(kline.card);

    var metrics = P.buildCard("V-02", t("val.metricsTitle"), "", "");
    metrics.body.id = "v02-body";
    left.appendChild(metrics.card);

    var cons = P.buildCard("V-03", t("val.components"), "", t("val.componentsSub"));
    cons.body.id = "v03-body";
    left.appendChild(cons.card);
    grid.appendChild(left);

    var right = el("div", "g-5");
    var info4 = P.buildCard("V-04", t("val.newsTitle"), "", t("val.newsSub"));
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
    state.sectorVal.market = P.normSectorMarket(params.market || state.sectorVal.market);  // RT-05：a→cn 归一
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
          P.setEmpty(body, t("val.enterFromSectors"), null);
        }
      });
      return;
    }
    bodies.forEach(function (body) {
      if (body) {
        P.setLoading(body);
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
            P.setError(body, error.message, function () {
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
      P.setBadges(v01Card, kline.source, kline.caliber, false);
      var items = kline.items || [];
      v01.innerHTML = "";
      if (items.length === 0) {
        P.setEmpty(v01, t("stock.empty.boardKline"), function () {
          loadSectorVal(true);
        });
      } else {
        var box = el("div", "pg-chart");
        v01.appendChild(box);
        var chart = P.mountChart(box);
        var theme = P.chartTheme();
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
        P.setEmpty(v02, t("state.empty"), function () {
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
          [t("val.kv.amount"), last.amount === null ? t("value.none") : fmt(P.yi(last.amount), 2) + " 亿元"],
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
        P.setError(v03, cons.error, function () {
          loadSectorVal(true);
        });
      } else if (!cons || (cons.items || []).length === 0) {
        P.setEmpty(v03, t("val.empty.components"), function () {
          loadSectorVal(true);
        });
      } else {
        P.setBadges(document.getElementById("V-03"), cons.source, cons.caliber, false);
        P.renderTable(
          v03,
          [
            { title: t("th.code"), key: "code", cls: "num" },
            { title: t("th.name"), key: "name", cls: "name" },
            { title: t("sectors.pctCol"), cls: "num", render: function (row) { return P.pctNode(row.pct); } },
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

  // ---- 导出到 window.Pages（供兄弟模块与 init 使用） ----
  P.buildValShell = buildValShell;
  P.loadSectorVal = loadSectorVal;
  P.paintSectorVal = paintSectorVal;
  P.renderSectorVal = renderSectorVal;
  P.updateValPlaceholder = updateValPlaceholder;
})(window.Pages = window.Pages || {});
