/* =============================================================
 * 阶段 4 · 真实行情验证前端
 * 依赖接口：/api/market/index、index-kline、quote、kline、minute、search、profile
 * 数据原则：页面不写死任何行情数字；加载失败显示错误，不保留旧值冒充新值。
 * 四态：加载（“加载中”徽章）、成功（数据+双徽章）、空（“—”）、错误（红色提示）。
 * ============================================================= */

(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  // P1-13：共享工具别名（fmt/fmtPct/trendClass/api/t/renderTable 原本各 IIFE 重复定义于此）
  var fmt = window.Utils.fmt;
  var fmtPct = window.Utils.fmtPct;
  var trendClass = window.Utils.trendClass;
  var api = window.Utils.api;
  var t = window.Utils.t;

  /** 金额格式化：元 → 万/亿/万亿。
   * @param {*} value 待格式化或处理的值
   * @returns {*} 处理结果
   */
  function fmtAmount(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
      return t("value.none"); // P2-09：统一空值为「无数据」
    }
    const number = Number(value);
    if (Math.abs(number) >= 1e12) return (number / 1e12).toFixed(2) + " 万亿";
    if (Math.abs(number) >= 1e8) return (number / 1e8).toFixed(2) + " 亿";
    if (Math.abs(number) >= 1e4) return (number / 1e4).toFixed(2) + " 万";
    return number.toFixed(2);
  }

  /** 成交量格式化：手 → 万手。
   * @param {*} value 待格式化或处理的值
   * @returns {*} 处理结果
   */
  function fmtVolume(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
      return t("value.none"); // P2-09：统一空值为「无数据」
    }
    const number = Number(value);
    if (Math.abs(number) >= 1e4) return (number / 1e4).toFixed(2) + " 万手";
    return number.toFixed(0) + " 手";
  }

  /** 设置徽章文案与状态。
   * @param {string} id 元素 id / 组件 id
   * @param {string} text 文本内容
   * @param {Object} state 状态对象
   */
  function setBadge(id, text, state) {
    const el = $(id);
    if (!el) return;
    el.textContent = text;
    el.classList.remove("ok", "error", "accent");
    if (state) el.classList.add(state);
  }

  /** 设置全局数据源徽章 + 「口径 ℹ」浮层（AR-11：三徽章合并，title 动态含口径/状态/时间）。
   * @param {Object} data 数据主体（接口 data 段）
   */
  function setGlobalBadges(data) {
    if (!data) return;
    setBadge("mk-source", t("mk.sourceBadge") + (data.source || t("value.dash")), "accent");
    // AR-11：三行详情收进「口径 ℹ」按钮 title 浮层，主行只保留数据源
    const info = document.getElementById("mk-caliber-info");
    if (info) {
      const realtime = Boolean(data.is_realtime);
      info.title = [
        t("mk.caliberBadge") + (data.caliber || t("value.dash")),
        t("mk.statusBadge") + (data.market_status || t("value.dash")) + (realtime ? t("mk.realtime") : t("mk.notRealtime")),
        t("mk.timeBadge") + (data.as_of || t("value.dash")),
      ].join("\n");
    }
  }

  /** 渲染普通表格（P1-13 委托 window.Utils.renderTable）。
   * @param {*} containerId 容器元素 id
   * @param {Array} columns 列定义（字段名数组）
   * @param {Array} rows 记录行列表
   */
  function renderTable(containerId, columns, rows) {
    window.Utils.renderTable(containerId, columns, rows, {
      tableClass: "data-table",
      emptyText: t("value.none"),
      nullText: t("value.none")
    });
  }

  /** 渲染 key-value 表（报价/概况）。
   * @param {*} containerId 容器元素 id
   * @param {*} pairs 键值对列表
   */
  function renderKV(containerId, pairs) {
    const container = $(containerId);
    if (!container) return;
    container.textContent = "";
    const table = document.createElement("table");
    table.className = "data-table";
    const tbody = document.createElement("tbody");
    pairs.forEach((pair) => {
      const tr = document.createElement("tr");
      const key = document.createElement("th");
      key.textContent = pair[0];
      const value = document.createElement("td");
      value.textContent = pair[1] === null || pair[1] === undefined ? t("value.none") : String(pair[1]); // P2-09：统一空值显示
      if (pair[2]) value.classList.add(pair[2]);
      if (pair[3]) value.classList.add("num");
      tr.appendChild(key);
      tr.appendChild(value);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    container.appendChild(table);
  }

  /** 获取当前主题色。
   * @returns {Object} 处理结果
   */
  function themeColors() {
    const styles = getComputedStyle(document.documentElement);
    return {
      up: styles.getPropertyValue("--up").trim() || "#FF4B4B",
      down: styles.getPropertyValue("--down").trim() || "#00B061",
      accent: styles.getPropertyValue("--accent").trim() || "#B47DA0",
      text: styles.getPropertyValue("--text").trim() || "#F6F6F6",
      dim: styles.getPropertyValue("--text-dim").trim() || "#A2A2A2",
      border: styles.getPropertyValue("--border").trim() || "#2A2A2A",
      panel: styles.getPropertyValue("--bg-widget").trim() || "#151518"
    };
  }

  /** 获取或初始化图表实例。
   * @param {string} id 元素 id / 组件 id
   * @returns {*} 处理结果
   */
  function chartOf(id) {
    const element = $(id);
    if (!element || typeof echarts === "undefined") return null;
    const existing = echarts.getInstanceByDom(element);
    return existing || echarts.init(element);
  }

  /** 渲染折线图（指数/分时共用）。
   * @param {string} id 元素 id / 组件 id
   * @param {*} labels 标签列表
   * @param {Array} values 数值列表
   * @param {*} color 颜色值（CSS 色值）
   */
  function renderLineChart(id, labels, values, color) {
    const chart = chartOf(id);
    if (!chart) return;
    const c = themeColors();
    chart.setOption(
      {
        animation: false,
        tooltip: {
          trigger: "axis",
          backgroundColor: c.panel,
          borderColor: c.border,
          textStyle: { color: c.text }
        },
        grid: { left: 52, right: 16, top: 18, bottom: 28 },
        xAxis: {
          type: "category",
          data: labels,
          axisLine: { lineStyle: { color: c.border } },
          axisLabel: { color: c.dim, fontSize: 11 }
        },
        yAxis: {
          type: "value",
          scale: true,
          axisLabel: { color: c.dim, fontSize: 11 },
          splitLine: { lineStyle: { color: c.border, type: "dashed" } },
          // REQ-20261111-01 V6 皮肤：交替亮带（splitArea）让图表区不再整片纯黑。
          // 参考图 K 线卡 p90=94，纯黑底细线只有 37-45；交替带是主要亮度来源。
          // 只影响绘制观感，不改变坐标范围、数值与曲线。
          splitArea: {
            show: document.documentElement.getAttribute("data-theme") === "v6",
            areaStyle: { color: ["rgba(46, 82, 118, .28)", "rgba(46, 82, 118, .06)"] }
          }
        },
        series: [
          {
            type: "line",
            data: values,
            showSymbol: false,
            lineStyle: { color: color || c.accent, width: 1.6 },
            areaStyle: { color: color || c.accent, opacity: 0.12 }
          }
        ]
      },
      true
    );
    chart.resize();
  }

  /** 渲染指数表。
   * @param {Array} items 记录列表
   */
  function renderIndexTable(items) {
    renderTable(
      "mk-index-table",
      [
        { key: "name", label: t("th.name") },
        { key: "code", label: t("th.code") },
        { key: "price", label: t("mk.col.price"), num: true, render: (row) => fmt(row.price) },
        { key: "change", label: t("mk.col.change"), num: true, trend: "change", render: (row) => fmt(row.change) },
        { key: "pct", label: t("mk.col.pct"), num: true, trend: "pct", render: (row) => fmtPct(row.pct) },
        { key: "market_status", label: t("mk.col.status") }
      ],
      items
    ); // P2-09：表头走 i18n
  }

  /** 渲染报价详情。
   * @param {*} quote 报价数据
   */
  function renderQuote(quote) {
    renderKV("mk-quote-table", [
      [t("mk.kv.nameCode"), (quote.name || t("value.dash")) + " / " + (quote.symbol || t("value.dash"))],
      [t("mk.kv.price"), fmt(quote.price), trendClass(quote.change), true],
      [t("mk.kv.changePct"), fmt(quote.change) + " / " + fmtPct(quote.pct), trendClass(quote.pct), true],
      [t("mk.kv.openPre"), fmt(quote.open) + " / " + fmt(quote.prev_close), "", true],
      [t("mk.kv.highLow"), fmt(quote.high) + " / " + fmt(quote.low), "", true],
      [t("mk.kv.volume"), fmtVolume(quote.volume), "", true],
      [t("mk.kv.amount"), fmtAmount(quote.amount), "", true],
      [t("mk.kv.turnover"), quote.turnover === null || quote.turnover === undefined ? t("value.none") : fmt(quote.turnover) + "%", "", true],
      [t("mk.kv.pePb"), fmt(quote.pe_ttm) + " / " + fmt(quote.pb), "", true],
      [t("mk.kv.totalMarketCap"), fmtAmount(quote.total_market_cap), "", true],
      [t("mk.kv.floatMarketCap"), fmtAmount(quote.float_market_cap), "", true],
      [t("mk.kv.bidAsk"), (quote.bids && quote.bids[0] ? fmt(quote.bids[0].price) : t("value.dash")) + " / " + (quote.asks && quote.asks[0] ? fmt(quote.asks[0].price) : t("value.dash")), "", true],
      [t("mk.kv.dataTime"), quote.quote_time || t("value.dash")],
      [t("mk.kv.marketStatus"), quote.market_status || t("value.dash")]
    ]); // P2-09：KV 标签走 i18n，空值统一「无数据」
  }

  /** 渲染个股概况。
   * @param {*} profile 公司概况数据
   */
  function renderProfile(profile) {
    renderKV("mk-profile-table", [
      [t("mk.kv.companyName"), profile.company_name || profile.name || t("value.dash")],
      [t("mk.kv.engName"), profile.english_name || t("value.dash")],
      [t("mk.kv.industry"), profile.industry || t("value.dash")],
      [t("mk.kv.marketBoard"), profile.market_board || t("value.dash")],
      [t("mk.kv.listDate"), profile.list_date || t("value.dash")],
      [t("mk.kv.legalRep"), profile.legal_representative || t("value.dash")],
      [t("mk.kv.registeredCap"), profile.registered_capital ? fmt(profile.registered_capital, 4) + " 万元" : t("value.dash")],
      [t("mk.kv.totalShares"), profile.total_shares ? fmt(profile.total_shares / 1e8, 2) + " 亿股" : t("value.dash")],
      [t("mk.kv.floatShares"), profile.float_shares ? fmt(profile.float_shares / 1e8, 2) + " 亿股" : t("value.dash")],
      [t("mk.kv.totalMarketCap"), fmtAmount(profile.total_market_cap)],
      [t("mk.kv.floatMarketCap"), fmtAmount(profile.float_market_cap)],
      [t("mk.kv.pePb"), fmt(profile.pe_ttm) + " / " + fmt(profile.pb)],
      [t("mk.kv.companyWeb"), profile.website || t("value.dash")],
      [t("mk.kv.mainBusiness"), profile.main_business || t("value.dash")]
    ]); // P2-09：KV 标签走 i18n
  }

  /** 渲染搜索结果。
   * @param {Array} items 记录列表
   */
  function renderSearch(items) {
    const container = $("mk-search-list");
    if (!container) return;
    container.textContent = "";
    if (!items || items.length === 0) {
      container.textContent = t("mk.noMatch");
      return;
    }
    items.forEach((item) => {
      const row = document.createElement("div");
      row.className = "search-item";
      const name = document.createElement("span");
      name.textContent = item.name + "（" + item.symbol + "）";
      const meta = document.createElement("span");
      meta.textContent = item.market + " · " + (item.type || "");
      row.appendChild(name);
      row.appendChild(meta);
      row.addEventListener("click", () => {
        $("mk-symbol").value = item.symbol;
        $("mk-market").value = item.market;
        loadSymbol();
      });
      container.appendChild(row);
    });
  }

  /** 加载核心指数与指数走势。 */
  async function loadIndex() {
    setBadge("mk-index-badge", t("mk.loading"));
    setBadge("mk-index-kline-badge", t("mk.loading"));
    try {
      const data = await api("/api/market/index", { market: "all" });
      renderIndexTable(data.items);
      setGlobalBadges(data);
      setBadge("mk-index-badge", data.source + " · " + data.market_status, data.is_realtime ? "ok" : "");
    } catch (error) {
      $("mk-index-table").textContent = t("mk.indexFailed", { error: error.message });
      setBadge("mk-index-badge", t("mk.failed"), "error");
    }

    try {
      const data = await api("/api/market/index-kline", { code: "000001", period: "日k" });
      renderLineChart(
        "mk-index-chart",
        data.items.map((item) => item.date),
        data.items.map((item) => item.close)
      );
      setBadge("mk-index-kline-badge", data.source + " · " + data.caliber, "");
    } catch (error) {
      setBadge("mk-index-kline-badge", t("mk.failed"), "error");
    }
  }

  /** 加载当前标的的报价与概况（K线/分时统一由图表页负责，P2-02 功能唯一）。 */
  async function loadSymbol() {
    const market = $("mk-market").value;
    const symbol = $("mk-symbol").value.trim();
    // R3-28：同步到全局共享 store，跨页带代码
    if (window.SymbolStore && symbol) {
      window.SymbolStore.set(symbol, market);
    }
    if (!symbol) {
      $("mk-notice").textContent = t("mk.symbolRequired");
      $("mk-notice").classList.add("error");
      return;
    }
    $("mk-notice").classList.remove("error");
    $("mk-notice").textContent = t("mk.requesting", { symbol: symbol, market: market });
    ["mk-quote-badge", "mk-profile-badge"].forEach((id) => setBadge(id, t("mk.loading")));

    const [quoteResult, profileResult] = await Promise.allSettled([
      api("/api/market/quote", { symbol, market }),
      api("/api/market/profile", { symbol, market })
    ]);

    if (quoteResult.status === "fulfilled") {
      const data = quoteResult.value;
      renderQuote(data.quote);
      setGlobalBadges(data);
      setBadge("mk-quote-badge", data.source + " · " + data.market_status, data.is_realtime ? "ok" : "");
    } else {
      $("mk-quote-table").textContent = t("mk.quoteFailed", { error: quoteResult.reason.message });
      setBadge("mk-quote-badge", t("mk.failed"), "error");
    }

    if (profileResult.status === "fulfilled") {
      const data = profileResult.value;
      renderProfile(data.profile);
      setBadge("mk-profile-badge", data.source + " · " + data.caliber, "");
    } else {
      $("mk-profile-table").textContent = t("mk.profileFailed", { error: profileResult.reason.message });
      setBadge("mk-profile-badge", t("mk.failed"), "error");
    }

    const failures = [quoteResult, profileResult].filter((item) => item.status === "rejected");
    if (failures.length === 0) {
      $("mk-notice").textContent =
        t("mk.dataLoaded", { symbol: symbol });
    } else {
      $("mk-notice").textContent = t("mk.partialFail", { errors: failures.map((item) => item.reason.message).join("；") });
      $("mk-notice").classList.add("error");
    }
  }

  /** 搜索。 */
  async function doSearch() {
    const keyword = $("mk-search-input").value.trim();
    if (!keyword) {
      $("mk-search-list").textContent = t("mk.searchKeywordRequired");
      return;
    }
    setBadge("mk-search-badge", t("mk.searching"));
    try {
      const data = await api("/api/market/search", { q: keyword, limit: 10 });
      renderSearch(data.items);
      setBadge("mk-search-badge", data.source + " · " + data.caliber, "");
    } catch (error) {
      $("mk-search-list").textContent = t("mk.searchFailPrefix") + error.message;
      setBadge("mk-search-badge", t("mk.failed"), "error");
    }
  }

  /** 刷新全部数据（先清缓存，保证看到源站最新结果）。 */
  async function refreshAll() {
    try {
      await fetch("/api/cache/clear", { method: "POST" });
    } catch (error) {
      /* 清缓存失败不阻断刷新，页面仍会重新请求 */
    }
    await Promise.all([loadIndex(), loadSymbol(), loadSentiment()]);
  }

  /** 拉取并渲染首页「市场情绪 · 涨跌家数」卡（FN-01）。
   *
   * 数据来源：/api/sentiment/market（与仪表板 N-03 同源，复用同一接口，不另造）；
   * 涨跌家数为该接口附带的 breadth 字段（东财 push2 沪深两市合计），
   * 不可用时显示空态说明，绝不以 0 代替（红线：不补零）。
   */
  async function loadSentiment() {
    const body = $("mk-senti-body");
    const badge = $("mk-senti-badge");
    if (!body) {
      return;
    }
    const el = window.Utils.el;
    try {
      const data = await api("/api/sentiment/market", { region: "cn", limit: 60 });
      const breadth = data.breadth || {};
      body.textContent = "";

      // 第一行：情绪温度 + 等级 + 进度条（0–100）
      const tempRow = el("div", "mk-senti-row");
      tempRow.appendChild(el("span", "mk-senti-label", t("market.sentiTemp")));
      tempRow.appendChild(el("span", "mk-senti-value", fmt(data.temperature, 1)));
      tempRow.appendChild(el("span", "mk-senti-level", data.level || t("value.none")));
      const bar = el("div", "mk-senti-bar");
      const fill = el("div", "mk-senti-fill");
      fill.style.width = Math.max(0, Math.min(100, Number(data.temperature) || 0)) + "%";
      bar.appendChild(fill);
      tempRow.appendChild(bar);
      body.appendChild(tempRow);

      // 第二行：涨跌家数（红涨绿跌）；不可用时只给中文说明
      const countRow = el("div", "mk-senti-row mk-senti-counts");
      if (breadth.available) {
        countRow.appendChild(el("span", "up", t("market.sentiUp") + " " + fmt(breadth.up, 0)));
        countRow.appendChild(el("span", "down", t("market.sentiDown") + " " + fmt(breadth.down, 0)));
        countRow.appendChild(el("span", "flat", t("market.sentiFlat") + " " + fmt(breadth.flat, 0)));
      } else {
        // r9 修复：不可用时这里只给短标签，详细原因（含「不以 0 代替」）留到下一行的口径说明，
        // 否则同一句话会连着出现两次（首版实测截图就是这样）
        countRow.appendChild(el("span", "flat", t("market.sentiUnavailable")));
      }
      body.appendChild(countRow);

      // 第三行：口径 + 北京时间（红线：时间一律北京时间并显式标注）
      body.appendChild(el("div", "mk-senti-note",
        (breadth.caliber || t("market.sentiCaliber")) + " · " + t("common.bjShort") + " " + (breadth.as_of || "—")));
      body.setAttribute("data-state", "default");
      if (badge) {
        badge.textContent = breadth.available ? t("market.sentiSource") : t("state.errorShort");
        // r9 修复：不可用时不能挂 ok（绿框「异常」自相矛盾）
        badge.className = breadth.available ? "badge ok" : "badge error";
      }
    } catch (error) {
      body.textContent = "";
      const box = el("div", "pg-state");
      box.appendChild(el("div", "pg-state-title", t("state.errorTitle")));
      box.appendChild(el("div", "pg-state-hint", (error && error.message) || t("state.errorHint")));
      body.appendChild(box);
      body.setAttribute("data-state", "error");
      if (badge) {
        badge.textContent = t("state.errorShort");
        badge.className = "badge";
      }
    }
  }

  /** 跳转图表分析页（携带当前市场/标的，功能唯一，P2-02）。 */
  function gotoCharting() {
    const market = $("mk-market").value;
    const symbol = $("mk-symbol").value.trim() || "600519";
    // R3-28：跳转前同步共享 store，图表页及其它页面无参进入时带回本次标的
    if (window.SymbolStore) {
      window.SymbolStore.set(symbol, market);
    }
    window.location.hash = "#/charting?symbol=" + encodeURIComponent(symbol) + "&market=" + encodeURIComponent(market);
  }

  /** 初始化控件。 */
  function init() {
    if (!$("mk-symbol")) return; // 非行情页
    // R3-28/R3-30：若本页输入为空，从共享 store 带出最近一次标的
    if (!$("mk-symbol").value && window.SymbolStore) {
      var shared = window.SymbolStore.get();
      $("mk-symbol").value = shared.symbol || $("mk-symbol").value;
      $("mk-market").value = shared.market || $("mk-market").value;
    }
    // 订阅共享 store：切页带代码自动同步到本页输入框
    if (window.SymbolStore && window.SymbolStore.subscribe) {
      window.SymbolStore.subscribe(function (symbol, market) {
        if ($("mk-symbol")) {
          $("mk-symbol").value = symbol;
          $("mk-market").value = market;
        }
      });
    }
    $("mk-refresh").addEventListener("click", refreshAll);
    const chartLink = $("mk-chart-link");
    if (chartLink) chartLink.addEventListener("click", gotoCharting);
    const chartHintLink = $("mk-chart-hint-link");
    if (chartHintLink) chartHintLink.addEventListener("click", gotoCharting);
    $("mk-search-button").addEventListener("click", doSearch);
    $("mk-search-input").addEventListener("keydown", (event) => {
      if (event.key === "Enter") doSearch();
    });
    $("mk-symbol").addEventListener("keydown", (event) => {
      if (event.key === "Enter") loadSymbol();
    });
    // 输入框变更时同步回共享 store（保留各页可独立改代码）
    $("mk-symbol").addEventListener("change", function () {
      if (window.SymbolStore) {
        window.SymbolStore.set($("mk-symbol").value, $("mk-market").value);
      }
    });
    $("mk-market").addEventListener("change", function () {
      if (window.SymbolStore) {
        window.SymbolStore.set($("mk-symbol").value, $("mk-market").value);
      }
    });
    window.addEventListener("resize", () => {
      ["mk-index-chart"].forEach((id) => {
        const chart = chartOf(id);
        if (chart) chart.resize();
      });
    });
    loadIndex();
    loadSymbol();
    loadSentiment();  // FN-01：首页市场情绪 + 涨跌家数
  }

  // 供工具栏「刷新当前视图」调用（P2-03）
  window.Market = { reload: refreshAll };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
