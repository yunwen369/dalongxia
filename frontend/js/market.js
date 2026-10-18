/* =============================================================
 * 阶段 4 · 真实行情验证前端
 * 依赖接口：/api/market/index、index-kline、quote、kline、minute、search、profile
 * 数据原则：页面不写死任何行情数字；加载失败显示错误，不保留旧值冒充新值。
 * 四态：加载（“加载中”徽章）、成功（数据+双徽章）、空（“—”）、错误（红色提示）。
 * ============================================================= */

(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  /** 数字格式化：空值统一显示“—”，不补 0。 */
  function fmt(value, digits) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
      return "—";
    }
    return Number(value).toLocaleString("zh-CN", {
      minimumFractionDigits: digits === undefined ? 2 : digits,
      maximumFractionDigits: digits === undefined ? 2 : digits
    });
  }

  /** 涨跌幅格式化：显式带正负号。 */
  function fmtPct(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
      return "—";
    }
    const number = Number(value);
    return (number > 0 ? "+" : "") + number.toFixed(2) + "%";
  }

  /** 金额格式化：元 → 万/亿/万亿。 */
  function fmtAmount(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
      return "—";
    }
    const number = Number(value);
    if (Math.abs(number) >= 1e12) return (number / 1e12).toFixed(2) + " 万亿";
    if (Math.abs(number) >= 1e8) return (number / 1e8).toFixed(2) + " 亿";
    if (Math.abs(number) >= 1e4) return (number / 1e4).toFixed(2) + " 万";
    return number.toFixed(2);
  }

  /** 成交量格式化：手 → 万手。 */
  function fmtVolume(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
      return "—";
    }
    const number = Number(value);
    if (Math.abs(number) >= 1e4) return (number / 1e4).toFixed(2) + " 万手";
    return number.toFixed(0) + " 手";
  }

  /** 根据数值返回涨跌样式类。 */
  function trendClass(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "flat";
    if (Number(value) > 0) return "up";
    if (Number(value) < 0) return "down";
    return "flat";
  }

  /** 设置徽章文案与状态。 */
  function setBadge(id, text, state) {
    const el = $(id);
    if (!el) return;
    el.textContent = text;
    el.classList.remove("ok", "error", "accent");
    if (state) el.classList.add(state);
  }

  /** 设置全局数据源/口径/状态/时间徽章。 */
  function setGlobalBadges(data) {
    if (!data) return;
    setBadge("mk-source", "数据源：" + (data.source || "—"), "accent");
    setBadge("mk-caliber", "口径：" + (data.caliber || "—"));
    const realtime = Boolean(data.is_realtime);
    setBadge(
      "mk-status",
      "状态：" + (data.market_status || "—") + (realtime ? " · 实时" : " · 非实时"),
      realtime ? "ok" : ""
    );
    setBadge("mk-asof", "时间：" + (data.as_of || "—"));
  }

  /** 统一 fetch：把 {data,error} 转成 data 或抛出中文错误。 */
  async function api(path, params) {
    const url = new URL(path, window.location.origin);
    Object.entries(params || {}).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, value);
      }
    });
    const response = await fetch(url.toString(), { cache: "no-store" });
    if (!response.ok) {
      throw new Error("请求失败（HTTP " + response.status + "）");
    }
    const body = await response.json();
    if (body.error) {
      const error = new Error(body.error);
      error.code = body.error_code;
      throw error;
    }
    return body.data;
  }

  /** 渲染普通表格。 */
  function renderTable(containerId, columns, rows) {
    const container = $(containerId);
    if (!container) return;
    container.textContent = "";
    if (!rows || rows.length === 0) {
      container.textContent = "无数据";
      return;
    }
    const table = document.createElement("table");
    table.className = "data-table";
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    columns.forEach((column) => {
      const th = document.createElement("th");
      th.textContent = column.label;
      if (column.num) th.className = "num";
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    rows.forEach((row) => {
      const tr = document.createElement("tr");
      columns.forEach((column) => {
        const td = document.createElement("td");
        const value = column.render ? column.render(row) : row[column.key];
        td.textContent = value === null || value === undefined ? "—" : String(value);
        if (column.num) td.classList.add("num");
        if (column.trend) td.classList.add(trendClass(row[column.trend]));
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    container.appendChild(table);
  }

  /** 渲染 key-value 表（报价/概况）。 */
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
      value.textContent = pair[1] === null || pair[1] === undefined ? "—" : String(pair[1]);
      if (pair[2]) value.classList.add(pair[2]);
      if (pair[3]) value.classList.add("num");
      tr.appendChild(key);
      tr.appendChild(value);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    container.appendChild(table);
  }

  /** 获取当前主题色。 */
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

  /** 获取或初始化图表实例。 */
  function chartOf(id) {
    const element = $(id);
    if (!element || typeof echarts === "undefined") return null;
    const existing = echarts.getInstanceByDom(element);
    return existing || echarts.init(element);
  }

  /** 渲染折线图（指数/分时共用）。 */
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
          splitLine: { lineStyle: { color: c.border, type: "dashed" } }
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

  /** 渲染 K 线图。 */
  function renderKlineChart(items) {
    const chart = chartOf("mk-kline-chart");
    if (!chart) return;
    const c = themeColors();
    const labels = items.map((item) => item.date);
    const values = items.map((item) => [item.open, item.close, item.low, item.high]);
    chart.setOption(
      {
        animation: false,
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "cross" },
          backgroundColor: c.panel,
          borderColor: c.border,
          textStyle: { color: c.text }
        },
        grid: { left: 56, right: 16, top: 18, bottom: 28 },
        xAxis: {
          type: "category",
          data: labels,
          axisLine: { lineStyle: { color: c.border } },
          axisLabel: { color: c.dim, fontSize: 11 }
        },
        yAxis: {
          scale: true,
          axisLabel: { color: c.dim, fontSize: 11 },
          splitLine: { lineStyle: { color: c.border, type: "dashed" } }
        },
        dataZoom: [{ type: "inside", start: 55, end: 100 }],
        series: [
          {
            type: "candlestick",
            data: values,
            itemStyle: {
              color: c.up,
              color0: c.down,
              borderColor: c.up,
              borderColor0: c.down
            }
          }
        ]
      },
      true
    );
    chart.resize();
  }

  /** 渲染指数表。 */
  function renderIndexTable(items) {
    renderTable(
      "mk-index-table",
      [
        { key: "name", label: "名称" },
        { key: "code", label: "代码" },
        { key: "price", label: "最新价", num: true, render: (row) => fmt(row.price) },
        { key: "change", label: "涨跌额", num: true, trend: "change", render: (row) => fmt(row.change) },
        { key: "pct", label: "涨跌幅", num: true, trend: "pct", render: (row) => fmtPct(row.pct) },
        { key: "market_status", label: "状态" }
      ],
      items
    );
  }

  /** 渲染报价详情。 */
  function renderQuote(quote) {
    renderKV("mk-quote-table", [
      ["名称 / 代码", (quote.name || "—") + " / " + (quote.symbol || "—")],
      ["最新价", fmt(quote.price), trendClass(quote.change), true],
      ["涨跌额 / 涨跌幅", fmt(quote.change) + " / " + fmtPct(quote.pct), trendClass(quote.pct), true],
      ["今开 / 昨收", fmt(quote.open) + " / " + fmt(quote.prev_close), "", true],
      ["最高 / 最低", fmt(quote.high) + " / " + fmt(quote.low), "", true],
      ["成交量", fmtVolume(quote.volume), "", true],
      ["成交额", fmtAmount(quote.amount), "", true],
      ["换手率", quote.turnover === null || quote.turnover === undefined ? "—" : fmt(quote.turnover) + "%", "", true],
      ["市盈率(TTM) / 市净率", fmt(quote.pe_ttm) + " / " + fmt(quote.pb), "", true],
      ["总市值", fmtAmount(quote.total_market_cap), "", true],
      ["流通市值", fmtAmount(quote.float_market_cap), "", true],
      ["买一 / 卖一", (quote.bids && quote.bids[0] ? fmt(quote.bids[0].price) : "—") + " / " + (quote.asks && quote.asks[0] ? fmt(quote.asks[0].price) : "—"), "", true],
      ["数据时间", quote.quote_time || "—"],
      ["市场状态", quote.market_status || "—"]
    ]);
  }

  /** 渲染个股概况。 */
  function renderProfile(profile) {
    renderKV("mk-profile-table", [
      ["公司名称", profile.company_name || profile.name || "—"],
      ["英文名称", profile.english_name || "—"],
      ["所属行业", profile.industry || "—"],
      ["所属市场 / 板块", profile.market_board || "—"],
      ["上市日期", profile.list_date || "—"],
      ["法人代表", profile.legal_representative || "—"],
      ["注册资本", profile.registered_capital ? fmt(profile.registered_capital, 4) + " 万元" : "—"],
      ["总股本", profile.total_shares ? fmt(profile.total_shares / 1e8, 2) + " 亿股" : "—"],
      ["流通股本", profile.float_shares ? fmt(profile.float_shares / 1e8, 2) + " 亿股" : "—"],
      ["总市值", fmtAmount(profile.total_market_cap)],
      ["流通市值", fmtAmount(profile.float_market_cap)],
      ["市盈率(TTM) / 市净率", fmt(profile.pe_ttm) + " / " + fmt(profile.pb)],
      ["公司网站", profile.website || "—"],
      ["主营业务", profile.main_business || "—"]
    ]);
  }

  /** 渲染搜索结果。 */
  function renderSearch(items) {
    const container = $("mk-search-list");
    if (!container) return;
    container.textContent = "";
    if (!items || items.length === 0) {
      container.textContent = "无匹配结果";
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
    setBadge("mk-index-badge", "加载中");
    setBadge("mk-index-kline-badge", "加载中");
    try {
      const data = await api("/api/market/index", { market: "all" });
      renderIndexTable(data.items);
      setGlobalBadges(data);
      setBadge("mk-index-badge", data.source + " · " + data.market_status, data.is_realtime ? "ok" : "");
    } catch (error) {
      $("mk-index-table").textContent = "指数加载失败：" + error.message;
      setBadge("mk-index-badge", "失败", "error");
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
      setBadge("mk-index-kline-badge", "失败", "error");
    }
  }

  /** 加载当前标的的报价、K线、分时、概况。 */
  async function loadSymbol() {
    const market = $("mk-market").value;
    const symbol = $("mk-symbol").value.trim();
    const period = $("mk-period").value;
    const adjust = $("mk-adjust").value;
    if (!symbol) {
      $("mk-notice").textContent = "请输入股票代码。";
      $("mk-notice").classList.add("error");
      return;
    }
    $("mk-notice").classList.remove("error");
    $("mk-notice").textContent = "正在请求真实接口：" + symbol + "（" + market + "）...";
    ["mk-quote-badge", "mk-kline-badge", "mk-minute-badge", "mk-profile-badge"].forEach((id) =>
      setBadge(id, "加载中")
    );

    const [quoteResult, klineResult, minuteResult, profileResult] = await Promise.allSettled([
      api("/api/market/quote", { symbol, market }),
      api("/api/market/kline", { symbol, market, period, adjust }),
      api("/api/market/minute", { symbol, market, period: "1min" }),
      api("/api/market/profile", { symbol, market })
    ]);

    if (quoteResult.status === "fulfilled") {
      const data = quoteResult.value;
      renderQuote(data.quote);
      setGlobalBadges(data);
      setBadge("mk-quote-badge", data.source + " · " + data.market_status, data.is_realtime ? "ok" : "");
    } else {
      $("mk-quote-table").textContent = "报价加载失败：" + quoteResult.reason.message;
      setBadge("mk-quote-badge", "失败", "error");
    }

    if (klineResult.status === "fulfilled") {
      const data = klineResult.value;
      renderKlineChart(data.items);
      setBadge("mk-kline-badge", data.source + " · " + data.caliber, "");
    } else {
      setBadge("mk-kline-badge", "失败", "error");
    }

    if (minuteResult.status === "fulfilled") {
      const data = minuteResult.value;
      renderLineChart(
        "mk-minute-chart",
        data.items.map((item) => item.time),
        data.items.map((item) => item.price),
        themeColors().accent
      );
      setBadge("mk-minute-badge", data.source + " · " + data.caliber, "");
    } else {
      setBadge("mk-minute-badge", "失败", "error");
    }

    if (profileResult.status === "fulfilled") {
      const data = profileResult.value;
      renderProfile(data.profile);
      setBadge("mk-profile-badge", data.source + " · " + data.caliber, "");
    } else {
      $("mk-profile-table").textContent = "概况加载失败：" + profileResult.reason.message;
      setBadge("mk-profile-badge", "失败", "error");
    }

    const failures = [quoteResult, klineResult, minuteResult, profileResult].filter(
      (item) => item.status === "rejected"
    );
    if (failures.length === 0) {
      $("mk-notice").textContent =
        "真实数据已加载：报价 " + symbol + "；数据源/口径以各卡片徽章为准；休市显示收盘快照，不伪装实时。";
    } else {
      $("mk-notice").textContent = "部分接口失败：" + failures.map((item) => item.reason.message).join("；");
      $("mk-notice").classList.add("error");
    }
  }

  /** 搜索。 */
  async function doSearch() {
    const keyword = $("mk-search-input").value.trim();
    if (!keyword) {
      $("mk-search-list").textContent = "请输入关键词";
      return;
    }
    setBadge("mk-search-badge", "搜索中");
    try {
      const data = await api("/api/market/search", { q: keyword, limit: 10 });
      renderSearch(data.items);
      setBadge("mk-search-badge", data.source + " · " + data.caliber, "");
    } catch (error) {
      $("mk-search-list").textContent = "搜索失败：" + error.message;
      setBadge("mk-search-badge", "失败", "error");
    }
  }

  /** 刷新全部数据（先清缓存，保证看到源站最新结果）。 */
  async function refreshAll() {
    try {
      await fetch("/api/cache/clear", { method: "POST" });
    } catch (error) {
      /* 清缓存失败不阻断刷新，页面仍会重新请求 */
    }
    await Promise.all([loadIndex(), loadSymbol()]);
  }

  /** 初始化控件。 */
  function init() {
    const loadButton = $("mk-load");
    if (!loadButton) return; // 非行情页
    loadButton.addEventListener("click", loadSymbol);
    $("mk-refresh").addEventListener("click", refreshAll);
    $("mk-search-button").addEventListener("click", doSearch);
    $("mk-search-input").addEventListener("keydown", (event) => {
      if (event.key === "Enter") doSearch();
    });
    $("mk-symbol").addEventListener("keydown", (event) => {
      if (event.key === "Enter") loadSymbol();
    });
    window.addEventListener("resize", () => {
      ["mk-index-chart", "mk-kline-chart", "mk-minute-chart"].forEach((id) => {
        const chart = chartOf(id);
        if (chart) chart.resize();
      });
    });
    loadIndex();
    loadSymbol();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
