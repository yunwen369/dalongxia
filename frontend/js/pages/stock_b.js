/* =============================================================
 * OpenBB 中文投研工作台 · pages/stock_b.js
 * 个股数据渲染（报价/K线/资金分布/新闻/舆情/一致预期）
 * 由 tools/split_pages.py 从 pages.js 机械切分生成（R17），逻辑零改动；
 * 重新生成请改 pages.js 后重跑脚本，勿直接编辑本文件的结构性段落。
 * ============================================================= */
(function (P) {
  "use strict";

  // ---- 跨模块依赖（core/兄弟模块导出，运行时解析） ----
  var COLOR_DOWN = P.COLOR_DOWN;
  var COLOR_MUTED = P.COLOR_MUTED;
  var COLOR_UP = P.COLOR_UP;
  var VIEW_IDS = P.VIEW_IDS;
  var api = P.api;
  var el = P.el;
  var fmt = P.fmt;
  var fmtPct = P.fmtPct;
  var state = P.state;
  var stockCache = P.stockCache;
  var t = P.t;
  var toast = P.toast;

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
      P.buildStockShell(container);
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
    P.syncStockTabs();
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
      P.setEmpty(body, t("stock.fcfNeedSymbol"));
      return;
    }
    P.setLoading(body);
    api("/api/stock/fcf-health", { symbol: symbol }).then(function (data) {
      body.innerHTML = "";
      var card = document.getElementById("ST-11");
      P.setBadges(card, "新浪三表", data.caliber);
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
      P.setError(body, error.message || t("state.errorHint"), loadStockFcf);
    });
  }

  /** 单请求失败折算为 null，避免 Promise.all 一处失败拖垮整页（2026-11-10）。
   * @param {Promise} task 接口 Promise
   * @returns {Promise} 永不 reject 的 Promise
   */
  function soft(task) {
    return task.then(function (value) {
      return value;
    }).catch(function () {
      return null;
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
        P.setLoading(body);
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

    Promise.all(tasks.map(soft))
      .then(function (result) {
        // 全部失败才视为真故障；单块失败已由后端返回空态（available=false），逐卡渲染。
        if (result.every(function (item) { return item === null; })) {
          throw new Error(t("state.errorHint"));
        }
        stockCache = { key: key, panel: result[0], estimates: result[2] };
        P.paintStock({
          panel: result[0] || {},
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
            P.setError(body, error.message, function () {
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
      P.setEmpty(body, t("stock.empty.quote"), function () {
        loadStock(true);
      });
      return;
    }
    P.setBadges(card, quote.source, quote.caliber, false);
    body.innerHTML = "";
    var main = el("div", "pg-quote-main");
    main.appendChild(el("span", "pg-quote-name", quote.name || t("value.none")));
    main.appendChild(el("span", "pg-quote-price " + P.pctClass(quote.change), fmt(quote.price, 2)));
    main.appendChild(el("span", P.pctClass(quote.change), fmt(quote.change, 2) + " / " + fmtPct(quote.pct)));
    // TZ-01：报价时间统一标注北京时区；美股/港股在悬浮里给收盘换算说明
    main.appendChild(P.timeWithBj(quote.quote_time, P.marketTzTip(state.stock.market), "pg-card-sub"));
    body.appendChild(main);
    var meta = el("div", "pg-quote-meta");
    [
      [t("stock.kv.open"), fmt(quote.open, 2)],
      [t("stock.kv.high"), fmt(quote.high, 2)],
      [t("stock.kv.low"), fmt(quote.low, 2)],
      [t("stock.kv.prevClose"), fmt(quote.prev_close, 2)],
      [t("stock.kv.turnover"), quote.turnover === null ? t("value.none") : fmt(quote.turnover, 2) + "%"],
      [t("stock.kv.totalMarketCap"), quote.total_market_cap === null ? t("value.none") : fmt(P.yi(quote.total_market_cap), 2) + " 亿"],
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
    var chart = P.mountChart(box);
    if (!chart) {
      return;
    }
    var theme = P.chartTheme();

    if (state.stock.tab === "minute") {
      var minute = panel.minute || [];
      if (minute.length === 0) {
        P.setEmpty(body, t("stock.empty.minute"), function () {
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
      P.setEmpty(body, t("stock.empty.kline"), function () {
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
      P.setEmpty(body, t("stock.empty.fund"), function () {
        loadStock(true);
      });
      return;
    }
    P.setBadges(card, "新浪", fund.caliber_note || t("stock.fundCaliber"), false);
    body.innerHTML = "";
    var box = el("div", "pg-chart small");
    body.appendChild(box);
    var chart = P.mountChart(box);
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
        legend: { bottom: 0, textStyle: { color: P.chartTheme().text } },
        series: [
          {
            type: "pie",
            radius: ["45%", "68%"],
            center: ["50%", "45%"],
            avoidLabelOverlap: true,
            // 标签显示「档位 + 净额占比%」，金额进 tooltip，避免 0.0151 亿这类超长小数
            label: {
              color: P.chartTheme().text,
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
      P.setEmpty(body, t("stock.empty.news"), function () {
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
      li.appendChild(P.newsTime(item.time));
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
      P.setEmpty(body, t("stock.empty.senti"), function () {
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

  // ---- 导出到 window.Pages（供兄弟模块与 init 使用） ----
  P.loadStock = loadStock;
  P.loadStockFcf = loadStockFcf;
  P.paintEstimates = paintEstimates;
  P.paintFundDist = paintFundDist;
  P.paintNews = paintNews;
  P.paintQuote = paintQuote;
  P.paintSentiment = paintSentiment;
  P.paintStockChart = paintStockChart;
  P.renderStock = renderStock;
})(window.Pages = window.Pages || {});
