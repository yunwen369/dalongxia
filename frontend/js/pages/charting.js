/* =============================================================
 * OpenBB 中文投研工作台 · pages/charting.js
 * 图表分析
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
  var charts = P.charts;
  var el = P.el;
  var fmt = P.fmt;
  var fmtPct = P.fmtPct;
  var state = P.state;
  var t = P.t;
  var toast = P.toast;

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
    var quoteCard = P.buildCard("CH-01", t("chart.card.quote"), "g-12", t("chart.card.quoteSub"));
    quoteCard.body.id = "ch01-body";
    grid.appendChild(quoteCard.card);
    var chartCard = P.buildCard("CH-02", t("chart.card.chart"), "g-12", t("chart.card.chartSub"));
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
      P.setEmpty(quoteBody, t("chart.empty.input"));
      P.setEmpty(chartBody, t("chart.empty.input"));
      return;
    }
    // R3-32：CH-01（报价）与 CH-02（K线/分时）**解耦**。
    // 此前三请求合成一个 Promise.all，最慢的 K 线未回时，报价区块即使数据早已到手也一直挂骨架，
    // 慢源下表现为「两块 loading 并挂 8-10s」。现改为各自落地：任一源慢只影响自己那张卡，
    // 页面上最多只剩 1 个真正的等待骨架。
    chartingRequestToken += 1;
    var token = chartingRequestToken;
    P.setLoading(quoteBody);
    P.setLoading(chartBody);

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
        P.setError(quoteBody, error.message, loadCharting);
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
        P.setError(chartBody, error.message, loadCharting);
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
      P.setEmpty(body, t("chart.empty.quote"), loadCharting);
      return;
    }
    P.setBadges(card, quote.source, quote.caliber, false);
    body.innerHTML = "";
    var main = el("div", "pg-quote-main");
    main.appendChild(el("span", "pg-quote-name", quote.name || state.charting.symbol));
    main.appendChild(el("span", "pg-quote-price " + P.pctClass(quote.change), fmt(quote.price, 2)));
    main.appendChild(el("span", P.pctClass(quote.change), fmt(quote.change, 2) + " / " + fmtPct(quote.pct)));
    // TZ-01：同上，图表页与个股页口径一致
    main.appendChild(P.timeWithBj(quote.quote_time, P.marketTzTip(state.charting.market), "pg-card-sub"));
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
      P.setEmpty(body, t("chart.empty.kline"), loadCharting);
      return;
    }
    P.setBadges(card, kline.source, kline.caliber, false);
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
    var chart = P.mountChart(box);
    if (!chart) {
      return;
    }
    var theme = P.chartTheme();
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
      P.setEmpty(body, t("chart.empty.minute"), loadCharting);
      return;
    }
    P.setBadges(card, minute.source, minute.caliber, false);
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
    var chart = P.mountChart(box);
    if (!chart) {
      return;
    }
    var theme = P.chartTheme();
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

  // ---- 导出到 window.Pages（供兄弟模块与 init 使用） ----
  P.bindChartingControls = bindChartingControls;
  P.buildChartingShell = buildChartingShell;
  P.chartingCache = chartingCache;
  P.chartingRequestToken = chartingRequestToken;
  P.computeKDJ = computeKDJ;
  P.computeMACD = computeMACD;
  P.exponentialMovingAverage = exponentialMovingAverage;
  P.loadCharting = loadCharting;
  P.movingAverage = movingAverage;
  P.paintChartingChart = paintChartingChart;
  P.paintChartingMinute = paintChartingMinute;
  P.paintChartingQuote = paintChartingQuote;
  P.renderCharting = renderCharting;
  P.syncChartingTabs = syncChartingTabs;
})(window.Pages = window.Pages || {});
