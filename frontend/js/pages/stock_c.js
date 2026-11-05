/* =============================================================
 * OpenBB 中文投研工作台 · pages/stock_c.js
 * 个股深度渲染（机构预测/概况/财务/股东）
 * 由 tools/split_pages.py 从 pages.js 机械切分生成（R17），逻辑零改动；
 * 重新生成请改 pages.js 后重跑脚本，勿直接编辑本文件的结构性段落。
 * ============================================================= */
(function (P) {
  "use strict";

  // ---- 跨模块依赖（core/兄弟模块导出，运行时解析） ----
  var COLOR_DOWN = P.COLOR_DOWN;
  var COLOR_UP = P.COLOR_UP;
  var FIN_METRIC_FIELDS = P.FIN_METRIC_FIELDS;
  var RATING_SOURCE_KEYS = P.RATING_SOURCE_KEYS;
  var api = P.api;
  var el = P.el;
  var fmt = P.fmt;
  var state = P.state;
  var t = P.t;

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
        P.setBadges(card, "同花顺", estimates.caliber_note || t("stock.estimates.caliber"), false);
        body.innerHTML = "";
        P.paintEstimates(body, estimates);
        body.setAttribute("data-state", "default");
        return;
      }
      P.setEmpty(body, t("stock.empty.forecast"), function () {
        P.loadStock(true);
      });
      return;
    }
    P.setBadges(card, "同花顺", forecast.caliber_note || t("stock.forecast.caliber"), false);
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
    P.paintEstimates(body, estimates);
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
      P.setEmpty(body, t("stock.empty.profile"), function () {
        P.loadStock(true);
      });
      return;
    }
    P.setBadges(card, "东财", profile.caliber_note || t("stock.profile.caliber"), false);
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
        [t("stock.profile.totalMv"), profile.total_mv === null ? t("value.none") : fmt(P.yi(profile.total_mv), 2) + " 亿元"],
        [t("stock.profile.floatMv"), profile.float_mv === null ? t("value.none") : fmt(P.yi(profile.float_mv), 2) + " 亿元"],
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
      P.setEmpty(body, t("stock.empty.fin"), function () {
        P.loadStock(true);
      });
      return;
    }
    P.setBadges(card, fin.summary.source, fin.summary.caliber, false);
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
      P.renderTable(pane, columns, metrics, null, "short");
      return;
    }

    if (tab === "trend") {
      if (!fin.trend || !fin.trend.series) {
        P.setEmpty(pane, t("stock.empty.trend"), function () {
          P.loadStock(true);
        });
        return;
      }
      var box = el("div", "pg-chart");
      pane.appendChild(box);
      var chart = P.mountChart(box);
      var theme = P.chartTheme();
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
                    P.setError(pane, error.message, function () {
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
          P.setError(pane, error.message, function () {
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
    P.renderTable(holder, columns, data.metrics || [], null, "short");
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
      P.setEmpty(body, t("stock.empty.holder"), function () {
        P.loadStock(true);
      });
      return;
    }
    P.setBadges(card, "东财", t("stock.holder.caliberPrefix") + (holders.top10_period || t("value.dash")) + t("stock.holder.caliberSuffix"), false);
    P.renderTable(
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
    P.paintQuote(payload.panel);
    P.syncStockTabs();
    P.paintStockChart();
    P.paintFundDist(payload.panel);
    P.paintNews(payload.panel);
    P.paintSentiment(payload.panel);
    paintForecast(payload.panel, payload.estimates);
    paintProfile(payload.profile, payload.holders, payload.summary);
    paintFinancial({ summary: payload.summary, trend: payload.trend });
    paintHolders(payload.holders);
  }

  // ---- 导出到 window.Pages（供兄弟模块与 init 使用） ----
  P.paintFinPane = paintFinPane;
  P.paintFinancial = paintFinancial;
  P.paintForecast = paintForecast;
  P.paintHolders = paintHolders;
  P.paintProfile = paintProfile;
  P.paintStock = paintStock;
  P.renderFinTable = renderFinTable;
  P.troughPoint = troughPoint;
})(window.Pages = window.Pages || {});
