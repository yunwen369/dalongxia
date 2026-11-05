/* =============================================================
 * OpenBB 中文投研工作台 · pages/stock_a.js
 * 个股页骨架与交互（ST-01 外壳/自选/K线 Tab）
 * 由 tools/split_pages.py 从 pages.js 机械切分生成（R17），逻辑零改动；
 * 重新生成请改 pages.js 后重跑脚本，勿直接编辑本文件的结构性段落。
 * ============================================================= */
(function (P) {
  "use strict";

  // ---- 跨模块依赖（core/兄弟模块导出，运行时解析） ----
  var api = P.api;
  var el = P.el;
  var state = P.state;
  var t = P.t;
  var toast = P.toast;

  // =============================================================
  // 六、#/stock 个股详情（ST-01~ST-10）
  // =============================================================
  /** 构建个股页骨架。
   * @param {HTMLElement} container DOM 容器节点
   */
  function buildStockShell(container) {
    container.innerHTML = "";
    var toolbar = el("div", "page-toolbar");
    var label = el("span", "pg-card-sub", t("stock.kv.codeLabel"));
    toolbar.appendChild(label);
    var input = document.createElement("input");
    input.id = "stock-symbol";
    input.value = state.stock.symbol;
    input.setAttribute("aria-label", t("stock.kv.codeLabel"));
    input.style.cssText =
      "width:120px;background:var(--bg-panel);border:1px solid var(--border);color:var(--text);" +
      "border-radius:5px;padding:4px 8px;font-family:var(--font-mono)";
    toolbar.appendChild(input);
    var marketSelect = document.createElement("select");
    marketSelect.id = "stock-market";
    marketSelect.style.cssText =
      "background:var(--bg-panel);border:1px solid var(--border);color:var(--text);border-radius:5px;padding:4px 8px";
    [["a", t("stock.market.a")], ["hk", t("sectors.market.hk")], ["us", t("sectors.market.us")]].forEach(function (item) {
      var option = document.createElement("option");
      option.value = item[0];
      option.textContent = item[1];
      marketSelect.appendChild(option);
    });
    marketSelect.value = state.stock.market;
    toolbar.appendChild(marketSelect);
    var go = el("button", "pg-btn", t("action.view"));
    go.type = "button";
    go.id = "stock-go";
    toolbar.appendChild(go);
    toolbar.appendChild(el("span", "pg-spacer"));
    var refresh = el("button", "pg-btn", t("action.reload"));
    refresh.type = "button";
    refresh.id = "stock-refresh";
    toolbar.appendChild(refresh);
    container.appendChild(toolbar);

    var grid = el("div", "pg-grid");
    grid.id = "stock-grid";

    // ST-01 报价大卡（通栏）
    var quote = P.buildCard("ST-01", t("stock.quoteCard"), "g-12", "");
    quote.body.id = "st01-body";
    grid.appendChild(quote.card);

    // 左主栏：ST-02/ST-03 互斥 Tab + ST-04 + ST-05
    var left = el("div", "g-7");
    left.style.display = "flex";
    left.style.flexDirection = "column";
    left.style.gap = "16px";

    var chartCard = P.buildCard("ST-02", t("stock.chartCard"), "", "");
    chartCard.card.setAttribute("data-block", "chart"); // 稳定锚点：id 在 ST-02/ST-03 间切换
    chartCard.body.id = "st02-body";
    var tabRow = el("div", "pg-tabs");
    tabRow.style.marginBottom = "8px";
    [["kline", t("stock.klineTab")], ["minute", t("stock.minuteTab")]].forEach(function (item) {
      var button = el("button", "pg-tab", item[1]);
      button.type = "button";
      button.setAttribute("data-tab", item[0]);
      tabRow.appendChild(button);
    });
    chartCard.card.insertBefore(tabRow, chartCard.body);
    left.appendChild(chartCard.card);

    var fundCard = P.buildCard("ST-04", t("stock.fundCard"), "", t("stock.fundSub"));
    fundCard.body.id = "st04-body";
    left.appendChild(fundCard.card);

    var newsCard = P.buildCard("ST-05", t("stock.newsCard"), "", t("stock.newsSub"));
    newsCard.body.id = "st05-body";
    left.appendChild(newsCard.card);
    grid.appendChild(left);

    // 右辅栏：ST-07 舆情 + ST-06 机构预测
    var right = el("div", "g-5");
    right.style.display = "flex";
    right.style.flexDirection = "column";
    right.style.gap = "16px";
    var sentiCard = P.buildCard("ST-07", t("stock.sentiCard"), "", t("stock.sentiSub"));
    sentiCard.body.id = "st07-body";
    right.appendChild(sentiCard.card);
    var forecastCard = P.buildCard("ST-06", t("stock.forecastCard"), "", "");
    forecastCard.body.id = "st06-body";
    right.appendChild(forecastCard.card);
    grid.appendChild(right);

    // 通栏：ST-08 / ST-09 / ST-10
    var profileCard = P.buildCard("ST-08", t("stock.profileCard"), "g-12", "");
    profileCard.body.id = "st08-body";
    grid.appendChild(profileCard.card);

    var finCard = P.buildCard("ST-09", t("stock.finCard"), "g-12", "");
    finCard.body.id = "st09-body";
    grid.appendChild(finCard.card);

    // 第七轮 M7：FCF 与利润背离检查（对应八条原则第 8 条）
    var fcfCard = P.buildCard("ST-11", t("stock.fcfCard"), "g-12", t("stock.fcfCardSub"));
    fcfCard.body.id = "st11-body";
    grid.appendChild(fcfCard.card);

    var holderCard = P.buildCard("ST-10", t("stock.holderCard"), "g-12", "");
    holderCard.body.id = "st10-body";
    grid.appendChild(holderCard.card);
    container.appendChild(grid);

    tabRow.addEventListener("click", function (event) {
      var button = event.target.closest(".pg-tab");
      if (!button) {
        return;
      }
      state.stock.tab = button.getAttribute("data-tab");
      syncStockTabs();
      P.paintStockChart();
    });
    go.addEventListener("click", function () {
      applyStockInput();
    });
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        applyStockInput();
      }
    });
    marketSelect.addEventListener("change", function () {
      state.stock.market = marketSelect.value;
      applyStockInput();
    });
    refresh.addEventListener("click", function () {
      P.loadStock(true);
    });
    // W-03：自选星标（写入/取消），与 #/watchlist 形成闭环
    var star = el("button", "pg-btn", t("watchlist.starAdd"));
    star.type = "button";
    star.id = "stock-star";
    toolbar.appendChild(star);
    star.addEventListener("click", function () {
      toggleWatchlist(star);
    });
    refreshStar();
  }

  /** 刷新个股页星标状态（是否已在自选）。
   * @returns {void}
   */
  function refreshStar() {
    var star = document.getElementById("stock-star");
    if (!star) {
      return;
    }
    api("/api/watchlist").then(function (data) {
      var exists = (data.items || []).some(function (item) {
        return item.symbol === state.stock.symbol && item.market === state.stock.market;
      });
      star.textContent = exists ? t("watchlist.starRemove") : t("watchlist.starAdd");
      star.setAttribute("data-in-list", exists ? "1" : "0");
    }).catch(function () {
      star.textContent = t("watchlist.starAdd");
    });
  }

  /** 切换当前个股的自选状态并提示。
   * @param {HTMLElement} star 星标按钮
   * @returns {void}
   */
  function toggleWatchlist(star) {
    var symbol = state.stock.symbol;
    var market = state.stock.market;
    var inList = star && star.getAttribute("data-in-list") === "1";
    var request = inList
      ? P.sendJson("DELETE", "/api/watchlist/" + encodeURIComponent(symbol) + "?market=" + encodeURIComponent(market))
      : P.sendJson("POST", "/api/watchlist", { symbol: symbol, market: market, name: "" });
    request.then(function () {
      toast(inList ? t("watchlist.removed") : t("watchlist.added"));
      refreshStar();
    }).catch(function (error) {
      toast(error.message || t("state.errorHint"));
    });
  }

  /** 读取输入框并跳转（保持 URL 参数与页面状态一致）。 */
  function applyStockInput() {
    var input = document.getElementById("stock-symbol");
    var code = (input && input.value ? input.value : "").trim();
    if (!code) {
      toast(t("stock.codeRequired"));
      return;
    }
    // R3-28：市场以 select 当前值为准（程序化改值不触发 change，读 state 会拿旧值）
    var marketSelect = document.getElementById("stock-market");
    var market = (marketSelect && marketSelect.value) || state.stock.market;
    state.stock.market = market;
    // 先写全局共享 store，再改 hash——确保其它页面无参进入时带回本次标的
    if (window.SymbolStore) {
      window.SymbolStore.set(code, market);
    }
    window.location.hash =
      "#/stock?symbol=" + encodeURIComponent(code) + "&market=" + market;
  }

  /** 同步 K 线/分时 Tab 选中态与标题。 */
  function syncStockTabs() {
    // 卡片 id 在 ST-02 / ST-03 之间切换，故用 data-block 锚点定位
    var card = document.querySelector('[data-block="chart"]');
    if (!card) {
      return;
    }
    card.querySelectorAll(".pg-tab").forEach(function (button) {
      button.classList.toggle("active", button.getAttribute("data-tab") === state.stock.tab);
    });
    card.id = state.stock.tab === "minute" ? "ST-03" : "ST-02";
    var title = card.querySelector(".pg-card-title");
    if (title) {
      title.textContent = state.stock.tab === "minute" ? t("stock.minuteTitle") : t("stock.klineTitle");
    }
  }

  var stockCache = { key: "", panel: null };

  /** 渲染基金组合页（第七轮 M5）：诊断（读台账）与评估（输入代码）两个 Tab。
   *
   * **合规边界**：只做筛选与结构诊断，不输出预期收益、不给买入评级；
   * 页面固定展示「非投资建议」徽章，禁用「推荐/最优」措辞。
   *
   * @returns {void} 无返回值
   */

  // ---- 导出到 window.Pages（供兄弟模块与 init 使用） ----
  P.applyStockInput = applyStockInput;
  P.buildStockShell = buildStockShell;
  P.refreshStar = refreshStar;
  P.stockCache = stockCache;
  P.syncStockTabs = syncStockTabs;
  P.toggleWatchlist = toggleWatchlist;
})(window.Pages = window.Pages || {});
