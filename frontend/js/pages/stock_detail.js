/* =============================================================
 * OpenBB 中文投研工作台 · pages/stock_detail.js
 * 个股「行情明细」22 字段网格（ST-02）—— 第十轮 P2
 *
 * 设计依据：DS-R10-v2.1 §5 页 10；原型说明书 ST-02「行情明细 22 字段网格」。
 *
 * 口径约束：
 * - 全部字段来自 `/api/stock/realtime-panel`（quote/profile）、`/api/stock/financial-summary`
 *   与 `/api/market/kline`，不新造字段、不臆造数值；
 * - 派生字段在前端计算并标注算法（振幅=最高-最低/昨收；近 250 日高低取日线尾部）；
 * - 缺失字段显示「无数据」，不补 0（§八 边界）；
 * - 红涨绿跌走 CSS 变量，数字千分位走 Utils.fmt。
 * ============================================================= */
(function (P) {
  "use strict";

  var buildCard = P.buildCard;
  var el = P.el;
  var fmt = P.fmt;
  var pctClass = P.pctClass;
  var setBadges = P.setBadges;

  /** 空值安全：null/undefined/NaN 一律「无数据」。 */
  function num(value, digits) {
    if (value === null || value === undefined || value === "" || isNaN(Number(value))) {
      return "无数据";
    }
    return fmt(Number(value), digits === undefined ? 2 : digits);
  }

  /** 元 → 亿元（接口给原始元；小于 1e6 视为已经是亿，避免二次除）。 */
  function toYi(value) {
    if (value === null || value === undefined || value === "" || isNaN(Number(value))) {
      return null;
    }
    var n = Number(value);
    return Math.abs(n) >= 1e6 ? n / 1e8 : n;
  }

  /** 股 → 亿股。 */
  function toYiShare(value) {
    if (value === null || value === undefined || value === "" || isNaN(Number(value))) {
      return null;
    }
    var n = Number(value);
    return Math.abs(n) >= 1e6 ? n / 1e8 : n;
  }

  /** 带符号百分比。 */
  function pct(value, digits) {
    if (value === null || value === undefined || value === "" || isNaN(Number(value))) {
      return "无数据";
    }
    var v = Number(value);
    return (v > 0 ? "+" : "") + fmt(v, digits === undefined ? 2 : digits) + "%";
  }

  /** 取财务指标最新一期（metrics[].values[0]）。 */
  function latestMetric(summary, keyword) {
    var metrics = (summary && summary.metrics) || [];
    for (var i = 0; i < metrics.length; i++) {
      var name = metrics[i].key || metrics[i].name || "";
      if (name.indexOf(keyword) >= 0) {
        var values = metrics[i].values || [];
        return values.length ? values[0] : null;
      }
    }
    return null;
  }

  /** 在个股页插入「行情明细」卡（放在报价卡之后，只建一次）。 */
  function ensureCard() {
    var existing = document.getElementById("ST-12");
    if (existing) {
      return {
        card: existing,
        body: existing.querySelector(".pg-body")
      };
    }
    var page = document.getElementById("page-stock");
    if (!page) {
      return null;
    }
    var built = buildCard("ST-12", "行情明细", "g-12", "22 字段 · realtime-panel + financial-summary + kline");
    var quoteCard = page.querySelector("#ST-01") || page.firstElementChild;
    if (quoteCard && quoteCard.parentNode) {
      quoteCard.parentNode.insertBefore(built.card, quoteCard.nextSibling);
    } else {
      page.insertBefore(built.card, page.firstChild);
    }
    return built;
  }

  /** 渲染 22 字段网格。 */
  function paintStockDetail(payload) {
    var holder = ensureCard();
    if (!holder || !holder.body) {
      return;
    }
    var body = holder.body;
    var quote = (payload && payload.panel && payload.panel.quote) || {};
    var profile = (payload && payload.profile) || {};
    var summary = (payload && payload.summary) || {};
    var kline = (payload && payload.panel && payload.panel.kline) || [];

    var prevClose = Number(quote.prev_close);
    var high = Number(quote.high);
    var low = Number(quote.low);
    var amplitude = prevClose && !isNaN(high) && !isNaN(low) ? (high - low) / prevClose * 100 : null;

    var tail = (kline || []).slice(-250);
    var highs = tail.map(function (x) { return Number(x.high); }).filter(function (v) { return !isNaN(v); });
    var lows = tail.map(function (x) { return Number(x.low); }).filter(function (v) { return !isNaN(v); });

    var fields = [
      ["现价", num(quote.price, 2), "v"],
      ["涨跌额", (Number(quote.change) > 0 ? "+" : "") + num(quote.change, 2), pctClass(quote.change)],
      ["涨跌幅", pct(quote.pct, 2), pctClass(quote.pct)],
      ["今开", num(quote.open, 2), ""],
      ["昨收", num(quote.prev_close, 2), ""],
      ["最高", num(quote.high, 2), ""],
      ["最低", num(quote.low, 2), ""],
      ["振幅", amplitude === null ? "无数据" : num(amplitude, 2) + "%", ""],
      ["成交量", quote.volume === undefined ? "无数据" : num(quote.volume, 0) + " 手", ""],
      ["成交额", toYi(quote.amount) === null ? "无数据" : num(toYi(quote.amount), 2) + " 亿", ""],
      ["换手率", quote.turnover === undefined || quote.turnover === null ? "无数据" : num(quote.turnover, 2) + "%", ""],
      ["市盈率 TTM", num(quote.pe_ttm || profile.pe_ttm, 2), ""],
      ["市净率", num(quote.pb || profile.pb, 2), ""],
      ["总市值", toYi(quote.total_market_cap || profile.total_mv) === null ? "无数据" : num(toYi(quote.total_market_cap || profile.total_mv), 2) + " 亿", ""],
      ["流通市值", toYi(quote.float_market_cap || profile.float_mv) === null ? "无数据" : num(toYi(quote.float_market_cap || profile.float_mv), 2) + " 亿", ""],
      ["近250日高", highs.length ? num(Math.max.apply(null, highs), 2) : "无数据", ""],
      ["近250日低", lows.length ? num(Math.min.apply(null, lows), 2) : "无数据", ""],
      ["每股收益 EPS", num(latestMetric(summary, "每股收益"), 2), ""],
      ["毛利率", num(latestMetric(summary, "毛利率"), 2) + "%", ""],
      ["ROE", num(latestMetric(summary, "净资产收益率"), 2) + "%", ""],
      ["总股本", toYiShare(profile.total_share) === null ? "无数据" : num(toYiShare(profile.total_share), 2) + " 亿股", ""],
      ["流通股本", toYiShare(profile.float_share) === null ? "无数据" : num(toYiShare(profile.float_share), 2) + " 亿股", ""]
    ];

    body.innerHTML = "";
    var grid = el("div", "st-detail-grid");
    fields.forEach(function (item) {
      var cell = el("div", "st-detail-cell");
      cell.appendChild(el("div", "st-detail-k", item[0]));
      cell.appendChild(el("div", "st-detail-v " + item[2], String(item[1])));
      grid.appendChild(cell);
    });
    body.appendChild(grid);

    var hint = el("p", "pg-hint",
      "数据源 " + (quote.source || "—") + " · " + (quote.caliber || "盘口快照") +
      "；振幅=最高−最低/昨收；近 250 日高低取日线尾部；财务四项取最近报告期（" +
      ((summary.periods && summary.periods[0]) || "无数据") + "）。");
    body.appendChild(hint);
    setBadges(holder.card, quote.source || "腾讯/东财", quote.market_status || "盘口快照", false);
    body.setAttribute("data-state", "default");
  }

  P.paintStockDetail = paintStockDetail;
})(window.Pages = window.Pages || {});
