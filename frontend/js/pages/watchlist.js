/* =============================================================
 * OpenBB 中文投研工作台 · pages/watchlist.js
 * 自选股 W-01/W-02 与龙虎榜·北向 L-01~L-05
 * 由 tools/split_pages.py 从 pages.js 机械切分生成（R17），逻辑零改动；
 * 重新生成请改 pages.js 后重跑脚本，勿直接编辑本文件的结构性段落。
 * ============================================================= */
(function (P) {
  "use strict";

  // ---- 跨模块依赖（core/兄弟模块导出，运行时解析） ----
  var COLOR_DOWN = P.COLOR_DOWN;
  var COLOR_UP = P.COLOR_UP;
  var api = P.api;
  var el = P.el;
  var fmt = P.fmt;
  var fmtPct = P.fmtPct;
  var state = P.state;
  var t = P.t;
  var toast = P.toast;

  // =============================================================
  // 二十一、自选股（W-01/W-02）与龙虎榜·北向（L-01~L-03）
  // 设计基准：新增功能设计基准_20261022.md；数据全部真实接口，无演示数据
  // =============================================================
  var MARKET_LABELS = { a: "A股", hk: "港股", us: "美股", fund: "场外基金" };

  /** 市场中文标签。
   * @param {string} market 市场标识
   * @returns {string} 中文标签
   */
  function marketLabel(market) {
    return MARKET_LABELS[market] || market;
  }

  /** 渲染自选股页（W-01 概览 + W-02 行情表）。
   */
  /** 当前自选分组过滤（W-02 分组 chips / W-03 侧栏树共用，空=全部）。
   * @type {string}
   */
  var watchlistGroup = "";

  /** 渲染自选股页（W-01 概览 + W-02 分组筛选 + 行情表）。
   * @param {Object} params 查询参数（支持 group=a/hk/us/fund 预过滤，来自侧栏树）
   * @returns {void} 无返回值
   */
  function renderWatchlist(params) {
    var page = document.getElementById("page-watchlist");
    if (!page) {
      return;
    }
    watchlistGroup = (params && params.group) || "";
    page.innerHTML = "";
    var grid = el("div", "pg-grid");
    var w1 = P.buildCard("W-01", t("watchlist.overview"), "g-12", t("watchlist.overviewSub"));
    w1.body.id = "w01-body";
    grid.appendChild(w1.card);
    var w2 = P.buildCard("W-02", t("watchlist.tableTitle"), "g-12", t("watchlist.tableSub"));
    w2.body.id = "w02-body";
    grid.appendChild(w2.card);
    page.appendChild(grid);
    loadWatchlist();
  }

  /** 拉取并渲染自选股（force=true 时绕过前端无缓存直接刷新）。
   * @param {boolean} force 是否强制刷新
   * @returns {void} 无返回值（异步渲染）
   */
  // ---------- R6-09 / F6-09：自选股批量操作状态 ----------
  /** 当前自选行缓存（批量操作按 symbol@market 反查行对象）。 */
  var watchRows = [];
  /** 当前选中的行键（symbol@market）。 */
  var watchSelected = [];
  /** 批量栏宿主（表格上方，随选中集重绘）。 */
  var watchBulkHost = null;

  /** 渲染自选股批量操作栏（选中 ≥1 行时显示）。
   *
   * 规格（设计基准 F6-09）：`已选 3 · [批量删除] [批量加图表] [导出CSV] [取消]`；
   * 批量删除二次确认并列出标的，逐条删除、部分失败逐条提示。
   *
   * @returns {void} 无返回值
   */
  function paintWatchBulkBar() {
    if (!watchBulkHost) {
      return;
    }
    watchBulkHost.innerHTML = "";
    if (!watchSelected.length) {
      watchBulkHost.hidden = true;
      return;
    }
    watchBulkHost.hidden = false;
    watchBulkHost.appendChild(el("span", "wl-bulk-count",
      t("watchlist.bulk.selected", { n: watchSelected.length })));

    /** 取当前选中的行对象。
     * @returns {Array<Object>} 选中行列表
     */
    function pickedRows() {
      return watchRows.filter(function (row) {
        return watchSelected.indexOf(row.symbol + "@" + row.market) >= 0;
      });
    }

    var del = el("button", "pg-btn", t("watchlist.bulk.delete"));
    del.type = "button";
    del.addEventListener("click", function () {
      var targets = pickedRows();
      if (!targets.length) {
        return;
      }
      var names = targets.map(function (row) { return row.name || row.symbol; }).join("、");
      if (!window.confirm(t("watchlist.bulk.deleteConfirm", { names: names, n: targets.length }))) {
        return;
      }
      var failed = [];
      var done = 0;
      /** 串行删除：逐条反馈，避免并发触发写锁。
       * @param {number} index 当前下标
       * @returns {void} 无返回值
       */
      function step(index) {
        if (index >= targets.length) {
          watchSelected = [];
          if (failed.length) {
            toast(t("watchlist.bulk.deletePartial", { ok: done, fail: failed.join("、") }));
          } else {
            toast(t("watchlist.bulk.deleteDone", { n: done }));
          }
          loadWatchlist(true);
          return;
        }
        var row = targets[index];
        P.sendJson("DELETE", "/api/watchlist/" + encodeURIComponent(row.symbol)
          + "?market=" + encodeURIComponent(row.market)).then(function () {
          done += 1;
          step(index + 1);
        }).catch(function () {
          failed.push(row.name || row.symbol);
          step(index + 1);
        });
      }
      step(0);
    });
    watchBulkHost.appendChild(del);

    var toBoard = el("button", "pg-btn", t("watchlist.bulk.toBoard"));
    toBoard.type = "button";
    toBoard.addEventListener("click", function () {
      var targets = pickedRows();
      if (!targets.length || !window.Dashboard || !window.Dashboard.addWidgetById) {
        return;
      }
      var added = 0;
      targets.forEach(function (row) {
        if (row.market === "fund") {
          return;
        }
        window.Dashboard.addWidgetById("ST-01", { symbol: row.symbol, market: row.market });
        added += 1;
      });
      toast(t("watchlist.bulk.toBoardDone", { n: added }));
      watchSelected = [];
      paintWatchBulkBar();
    });
    watchBulkHost.appendChild(toBoard);

    var csv = el("button", "pg-btn", t("watchlist.bulk.export"));
    csv.type = "button";
    csv.addEventListener("click", function () {
      var targets = pickedRows();
      if (!targets.length) {
        return;
      }
      var head = ["代码", "名称", "市场", "最新价", "涨跌", "涨幅%", "成交额", "PE"];
      var lines = [head.join(",")];
      targets.forEach(function (row) {
        var quote = row.quote || {};
        lines.push([row.symbol, row.name || "", row.market || "",
          quote.price === null || quote.price === undefined ? "" : quote.price,
          quote.change === null || quote.change === undefined ? "" : quote.change,
          quote.pct === null || quote.pct === undefined ? "" : quote.pct,
          quote.amount === null || quote.amount === undefined ? "" : quote.amount,
          quote.pe_ttm === null || quote.pe_ttm === undefined ? "" : quote.pe_ttm].join(","));
      });
      var blob = new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
      var link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = "watchlist_" + new Date().toISOString().slice(0, 10) + ".csv";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(link.href);
    });
    watchBulkHost.appendChild(csv);

    var cancel = el("button", "pg-btn ghost", t("watchlist.bulk.cancel"));
    cancel.type = "button";
    cancel.addEventListener("click", function () {
      watchSelected = [];
      loadWatchlist(true);
    });
    watchBulkHost.appendChild(cancel);
  }

  /** 渲染自选股空态（含「搜索代码直接加自选」）。
   *
   * 规格（设计基准 F6-09 空态）：0 条时给「从个股详情页点 ★ 添加」之外，
   * 额外提供搜索框，可直接按代码/名称检索并加入自选，避免必须先去个股页。
   *
   * @param {HTMLElement} body 内容容器
   * @param {string} hint 后端下发的空态说明（可为空）
   * @returns {void} 无返回值
   */
  function paintWatchlistEmpty(body, hint) {
    body.innerHTML = "";
    var box = el("div", "pg-state");
    box.setAttribute("data-state", "empty");
    box.appendChild(el("div", "pg-state-title", t("watchlist.emptyTitle")));
    box.appendChild(el("div", "pg-state-hint", hint || t("watchlist.empty")));

    var bar = el("div", "wl-addbar");
    var input = document.createElement("input");
    input.type = "search";
    input.className = "wl-addbar-input";
    input.placeholder = t("watchlist.addPlaceholder");
    input.setAttribute("aria-label", t("watchlist.addPlaceholder"));
    bar.appendChild(input);
    var searchBtn = el("button", "pg-btn primary", t("watchlist.addSearch"));
    searchBtn.type = "button";
    bar.appendChild(searchBtn);
    box.appendChild(bar);

    var resultHost = el("div", "wl-addresult");
    box.appendChild(resultHost);

    /** 执行检索并渲染「加入自选」候选。
     * @returns {void} 无返回值
     */
    function doSearch() {
      var keyword = (input.value || "").trim();
      if (!keyword) {
        toast(t("watchlist.addNeedKeyword"));
        return;
      }
      resultHost.innerHTML = "";
      resultHost.appendChild(el("div", "pg-state", t("state.loading")));
      api("/api/market/search", { q: keyword }).then(function (data) {
        var items = data.items || [];
        resultHost.innerHTML = "";
        if (!items.length) {
          resultHost.appendChild(el("div", "pg-state", t("watchlist.addNoMatch")));
          return;
        }
        items.slice(0, 8).forEach(function (item) {
          var row = el("div", "wl-addrow");
          row.appendChild(el("b", "", item.symbol));
          row.appendChild(el("span", "wl-addrow-name", item.name || ""));
          row.appendChild(el("span", "pg-spacer"));
          var add = el("button", "pg-btn", t("watchlist.addAction"));
          add.type = "button";
          add.addEventListener("click", function () {
            add.disabled = true;
            P.sendJson("POST", "/api/watchlist", {
              symbol: item.symbol,
              market: item.market || "a",
              name: item.name || ""
            }).then(function () {
              toast(t("watchlist.added"));
              loadWatchlist(true);
            }).catch(function (error) {
              add.disabled = false;
              toast(error.message || t("state.errorHint"));
            });
          });
          row.appendChild(add);
          resultHost.appendChild(row);
        });
      }).catch(function (error) {
        resultHost.innerHTML = "";
        resultHost.appendChild(el("div", "pg-state", error.message || t("state.errorHint")));
      });
    }

    searchBtn.addEventListener("click", doSearch);
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        doSearch();
      }
    });
    body.appendChild(box);
    body.setAttribute("data-state", "empty");
  }

  /** W-03：渲染侧栏「自选股」分组树（我的自选 + 市场分组 + 数量徽章）。
   * 数据源与 W-02 chips 相同（counts.by_market），点击分组进入自选页并预过滤。
   * @param {Object} byMarket 市场 → 数量 映射
   * @param {number} total 自选总数
   * @returns {void} 无返回值
   */
  function renderWatchlistTree(byMarket, total) {
    var host = document.getElementById("sidebar-watchlist-groups");
    if (!host) {
      return;
    }
    host.innerHTML = "";
    if (!total) {
      return; // 空清单不渲染分组（避免 0 徽章噪音）
    }
    [
      ["a", t("watchlist.group.a")],
      ["hk", t("watchlist.group.hk")],
      ["us", t("watchlist.group.us")],
      ["fund", t("watchlist.group.fund")]
    ].forEach(function (pair) {
      var count = (byMarket || {})[pair[0]] || 0;
      if (!count) {
        return; // 无成员的组不展示
      }
      var li = el("li", "sidebar-item sidebar-sub");
      li.setAttribute("data-action", "view");
      li.setAttribute("data-view", "watchlist");
      // app.js bindSidebar 按 data-view-target 拼 hash，缺失会导致点击无响应
      li.setAttribute("data-view-target", "watchlist");
      li.dataset.group = pair[0];
      var label = el("span", "", pair[1]);
      li.appendChild(label);
      li.appendChild(el("span", "sidebar-count", String(count)));
      host.appendChild(li);
    });
  }

  function loadWatchlist(force) {
    var body1 = document.getElementById("w01-body");
    var body2 = document.getElementById("w02-body");
    if (!body1 || !body2) {
      return;
    }
    P.setLoading(body1);
    P.setLoading(body2);
    api("/api/watchlist").then(function (data) {
      var counts = data.counts || {};
      var card1 = document.getElementById("W-01");
      P.setBadges(card1, data.source, data.caliber);
      body1.innerHTML = "";
      var grid = el("div", "pg-metric-grid");
      [
        [t("watchlist.count"), counts.total || 0, "flat"],
        [t("watchlist.up"), counts.up || 0, "up"],
        [t("watchlist.down"), counts.down || 0, "down"],
        [t("watchlist.flat"), counts.flat || 0, "flat"],
        [t("watchlist.asOf"), String(data.as_of || "").slice(11, 19), "flat"]
      ].forEach(function (item) {
        var cell = el("div", "pg-metric-cell");
        cell.appendChild(el("div", "pg-metric-label", item[0]));
        cell.appendChild(el("div", "pg-metric-value " + item[2], String(item[1])));
        grid.appendChild(cell);
      });
      body1.appendChild(grid);
      body1.setAttribute("data-state", "default");

      var rows = data.items || [];
      var byMarket = (counts.by_market) || {};
      // W-03：侧栏自选分组树（当前市场分布 + 数量徽章），与 W-02 chips 同源
      renderWatchlistTree(byMarket, counts.total || 0);
      if (watchlistGroup) {
        rows = rows.filter(function (row) { return row.market === watchlistGroup; });
      }
      if (!rows.length) {
        if (watchlistGroup) {
          P.setEmpty(body2, t("watchlist.groupEmpty"), function () {
            watchlistGroup = "";
            loadWatchlist(true);
          });
          return;
        }
        // R6-09 / F6-09：空态给「搜索框直接加自选」，不只让用户去个股页
        paintWatchlistEmpty(body2, data.empty_note || t("watchlist.empty"));
        return;
      }
      P.renderTable(body2, [
        {
          title: t("watchlist.col.name"), cls: "l",
          /** 名称列：代码加粗 + 名称。
           * @param {Object} row 自选行
           * @returns {HTMLElement} 单元格节点
           */
          render: function (row) {
            var box = el("span", "");
            box.appendChild(el("b", "", row.symbol));
            box.appendChild(document.createTextNode(" " + (row.name || "")));
            return box;
          }
        },
        { title: t("watchlist.col.market"), cls: "l", render: function (row) { return marketLabel(row.market); } },
        { title: t("watchlist.col.price"), num: true, render: function (row) { return fmt((row.quote || {}).price, 2); } },
        { title: t("watchlist.col.change"), num: true, render: function (row) { var q = row.quote || {}; return el("span", P.pctClass(q.change), fmt(q.change, 2)); } },
        { title: t("watchlist.col.pct"), num: true, render: function (row) { var q = row.quote || {}; return el("span", P.pctClass(q.pct), fmtPct(q.pct)); } },
        { title: t("watchlist.col.amount"), num: true, render: function (row) { return P.fmtAmount((row.quote || {}).amount); } },
        { title: t("watchlist.col.pe"), num: true, render: function (row) { return fmt((row.quote || {}).pe_ttm, 2); } },
        {
          title: "", cls: "l",
          /** 操作列：移除按钮（二次确认后 DELETE）。
           * @param {Object} row 自选行
           * @returns {HTMLElement} 单元格节点
           */
          render: function (row) {
            var btn = el("button", "pg-btn", t("watchlist.remove"));
            btn.type = "button";
            btn.addEventListener("click", function (event) {
              event.stopPropagation();
              if (!window.confirm(t("watchlist.removeConfirm"))) {
                return;
              }
              P.sendJson("DELETE", "/api/watchlist/" + encodeURIComponent(row.symbol)
                + "?market=" + encodeURIComponent(row.market)).then(function () {
                toast(t("watchlist.removed"));
                loadWatchlist(true);
              }).catch(function (error) {
                toast(error.message || t("state.errorHint"));
              });
            });
            return btn;
          }
        }
      ], rows, function (row) {
        if (row.market === "fund") {
          window.location.hash = "#/fund?code=" + row.symbol;
        } else {
          window.location.hash = "#/stock?symbol=" + row.symbol + "&market=" + row.market;
        }
      }, null, {
        // R6-09 / F6-09：勾选列 + 表头全选，变化后重绘批量栏
        key: function (row) { return row.symbol + "@" + row.market; },
        selected: watchSelected,
        onChange: function (keys) {
          watchSelected = keys;
          paintWatchBulkBar();
        }
      });
      // R6-09：批量栏插到表格上方（renderTable 会清空容器，故在其后 insertBefore）
      watchRows = rows;
      watchBulkHost = el("div", "wl-bulk");
      watchBulkHost.hidden = true;
      body2.insertBefore(watchBulkHost, body2.firstChild);
      paintWatchBulkBar();
      // W-02：分组筛选 chips 必须在 renderTable 之后追加——renderTable 会清空容器
      var chips = el("div", "pg-tabs");
      [
        ["", t("watchlist.group.all"), counts.total || 0],
        ["a", t("watchlist.group.a"), byMarket.a || 0],
        ["hk", t("watchlist.group.hk"), byMarket.hk || 0],
        ["us", t("watchlist.group.us"), byMarket.us || 0],
        ["fund", t("watchlist.group.fund"), byMarket.fund || 0]
      ].forEach(function (chip) {
        var btn = el("button", "pg-tab" + (watchlistGroup === chip[0] ? " active" : ""),
          chip[1] + "（" + chip[2] + "）");
        btn.type = "button";
        btn.addEventListener("click", function () {
          watchlistGroup = chip[0];
          loadWatchlist(true);
        });
        chips.appendChild(btn);
      });
      body2.appendChild(chips);
    }).catch(function (error) {
      P.setError(body1, error.message || t("state.errorHint"), function () { loadWatchlist(true); });
      P.setError(body2, error.message || t("state.errorHint"), function () { loadWatchlist(true); });
    });
  }

  /** 渲染龙虎榜·北向页（L-01 图 + L-02 Top10 + L-03 明细 + L-04 机构席位 + L-05 营业部）。
   */
  function renderLhb() {
    var page = document.getElementById("page-lhb");
    if (!page) {
      return;
    }
    page.innerHTML = "";
    var grid = el("div", "pg-grid");
    var l1 = P.buildCard("L-01", t("lhb.northTitle"), "g-7", t("lhb.northSub"));
    l1.body.id = "l01-body";
    grid.appendChild(l1.card);
    var l2 = P.buildCard("L-02", t("lhb.topTitle"), "g-5", t("lhb.topSub"));
    l2.body.id = "l02-body";
    grid.appendChild(l2.card);
    var l3 = P.buildCard("L-03", t("lhb.detailTitle"), "g-12", t("lhb.detailSub"));
    l3.body.id = "l03-body";
    grid.appendChild(l3.card);
    // R3-18：机构席位统计（stock_lhb_jgmmtj_em）与活跃营业部（stock_lhb_hyyyb_em）
    var l4 = P.buildCard("L-04", t("lhb.instTitle"), "g-7", t("lhb.instSub"));
    l4.body.id = "l04-body";
    grid.appendChild(l4.card);
    var l5 = P.buildCard("L-05", t("lhb.brokerTitle"), "g-5", t("lhb.brokerSub"));
    l5.body.id = "l05-body";
    grid.appendChild(l5.card);
    page.appendChild(grid);
    loadNorthbound();
    loadNorthTop();
    loadLhb();
    loadLhbInstitution();
    loadLhbBroker();
  }

  /** L-04：龙虎榜机构席位统计（按票汇总买方/卖方机构数与净买额）。
   * @returns {void} 无返回值（异步渲染）
   */
  function loadLhbInstitution() {
    var body = document.getElementById("l04-body");
    if (!body) {
      return;
    }
    P.setLoading(body);
    api("/api/moneyflow/lhb-institution", { limit: 50 }).then(function (data) {
      var card = document.getElementById("L-04");
      // 双徽章：徽章1=源（含 akshare 函数名），徽章2=口径（含统计区间）
      P.setBadges(card, data.source, data.caliber);
      var items = data.items || [];
      if (!items.length) {
        P.setEmpty(body, data.note || t("lhb.instEmpty"), function () { loadLhbInstitution(); });
        return;
      }
      P.renderTable(body, [
        {
          title: t("watchlist.col.name"), cls: "l",
          /** 名称列：代码加粗 + 名称。
           * @param {Object} row 数据行
           * @returns {HTMLElement} 单元格节点
           */
          render: function (row) {
            var box = el("span", "");
            box.appendChild(el("b", "", row.code));
            box.appendChild(document.createTextNode(" " + (row.name || "")));
            return box;
          }
        },
        { title: t("lhb.col.pct"), num: true, render: function (row) { return el("span", P.pctClass(row.pct), fmtPct(row.pct)); } },
        { title: t("lhb.col.buyOrgs"), num: true, render: function (row) { return fmt(row.buy_orgs, 0); } },
        { title: t("lhb.col.sellOrgs"), num: true, render: function (row) { return fmt(row.sell_orgs, 0); } },
        { title: t("lhb.col.netBuy"), num: true, render: function (row) { return el("span", P.pctClass(row.net_buy), fmt(row.net_buy, 2)); } },
        { title: t("lhb.col.ratio"), num: true, render: function (row) { return row.ratio === null || row.ratio === undefined ? t("value.none") : fmt(row.ratio, 2) + "%"; } },
        {
          title: t("lhb.col.reason"), cls: "l",
          /** 上榜原因：过长时截断由 CSS 处理，title 保完整。
           * @param {Object} row 数据行
           * @returns {HTMLElement} 单元格节点
           */
          render: function (row) {
            var text = row.reason || t("value.none");
            var node = el("span", "", text);
            node.title = text;
            return node;
          }
        }
      ], items, function (row) {
        window.location.hash = "#/stock?symbol=" + row.code + "&market=a";
      });
    }).catch(function (error) {
      P.setError(body, error.message || t("state.errorHint"), function () { loadLhbInstitution(); });
    });
  }

  /** L-05：龙虎榜活跃营业部（按营业部汇总买卖金额与个股数）。
   * @returns {void} 无返回值（异步渲染）
   */
  function loadLhbBroker() {
    var body = document.getElementById("l05-body");
    if (!body) {
      return;
    }
    P.setLoading(body);
    api("/api/moneyflow/lhb-broker", { limit: 50 }).then(function (data) {
      var card = document.getElementById("L-05");
      P.setBadges(card, data.source, data.caliber);
      var items = data.items || [];
      if (!items.length) {
        P.setEmpty(body, data.note || t("lhb.brokerEmpty"), function () { loadLhbBroker(); });
        return;
      }
      P.renderTable(body, [
        {
          title: t("lhb.col.broker"), cls: "l",
          /** 营业部名：过长截断，悬停看全称。
           * @param {Object} row 数据行
           * @returns {HTMLElement} 单元格节点
           */
          render: function (row) {
            var node = el("span", "", row.name || t("value.none"));
            node.title = row.name || "";
            return node;
          }
        },
        { title: t("lhb.col.buyCount"), num: true, render: function (row) { return fmt(row.buy_count, 0); } },
        { title: t("lhb.col.sellCount"), num: true, render: function (row) { return fmt(row.sell_count, 0); } },
        { title: t("lhb.col.buy"), num: true, render: function (row) { return fmt(row.buy_amount, 2); } },
        { title: t("lhb.col.sell"), num: true, render: function (row) { return fmt(row.sell_amount, 2); } },
        { title: t("lhb.col.netBuy"), num: true, render: function (row) { return el("span", P.pctClass(row.net_buy), fmt(row.net_buy, 2)); } }
      ], items, null);
    }).catch(function (error) {
      P.setError(body, error.message || t("state.errorHint"), function () { loadLhbBroker(); });
    });
  }

  /** L-01：北向资金近 20 个有披露交易日净流入柱状图。
   * @returns {void} 无返回值（异步渲染）
   */
  function loadNorthbound() {
    var body = document.getElementById("l01-body");
    if (!body) {
      return;
    }
    P.setLoading(body);
    api("/api/moneyflow/northbound", { days: 20 }).then(function (data) {
      var card = document.getElementById("L-01");
      P.setBadges(card, data.source, data.caliber);
      var items = data.items || [];
      if (!items.length) {
        P.setEmpty(body, data.note || t("state.empty"), function () { loadNorthbound(); });
        return;
      }
      body.innerHTML = "";
      if (data.note) {
        body.appendChild(el("div", "pg-card-sub", data.note));
      }
      // R3：新披露口径（成交总额）与历史净买额共用一张卡，按 metric 切换画法与图例
      var isTurnover = data.metric === "turnover";
      if (isTurnover) {
        var head = el("div", "north-head");
        var kv = data.turnover || {};
        head.appendChild(el("div", "north-kv",
          "今日成交总额 " + (kv.total === null || kv.total === undefined ? "无数据" : fmt(kv.total, 2) + " 亿") +
          "（沪 " + (kv.hu === null || kv.hu === undefined ? "—" : fmt(kv.hu, 2)) +
          " / 深 " + (kv.shen === null || kv.shen === undefined ? "—" : fmt(kv.shen, 2)) + "）"));
        head.appendChild(el("span", "pill ai", data.latest_label || "沪深股通成交总额 · 交易所 T+0 收市后披露"));
        body.appendChild(head);
      }
      var node = el("div", "pg-chart");
      node.style.height = "300px";
      body.appendChild(node);
      var chart = P.mountChart(node);
      if (chart) {
        var theme = P.chartTheme();
        chart.setOption({
          grid: { left: 48, right: 16, top: 24, bottom: 32 },
          tooltip: { trigger: "axis" },
          xAxis: {
            type: "category",
            data: items.map(function (item) { return String(item.date).slice(5); }),
            axisLabel: { color: theme.text }
          },
          yAxis: {
            type: "value",
            name: isTurnover ? "亿元" : "亿元",
            nameTextStyle: { color: theme.text },
            axisLabel: { color: theme.text }, splitLine: { lineStyle: { color: theme.border } }
          },
          series: [{
            type: "bar",
            name: isTurnover ? "成交总额" : "净买额",
            data: items.map(function (item) {
              if (isTurnover) {
                return { value: item.total, itemStyle: { color: theme.accent || COLOR_UP } };
              }
              return { value: item.net, itemStyle: { color: item.net >= 0 ? COLOR_UP : COLOR_DOWN } };
            })
          }]
        });
      }
      if (isTurnover) {
        body.appendChild(el("p", "pg-hint",
          "口径：2024-08-19 起交易所不再披露北向净买额，改为收市后披露成交总额与笔数；本图右侧为当前披露口径，历史净买额序列见 metric=legacy_net。"));
        var top = data.top || [];
        if (top.length) {
          body.appendChild(el("div", "mg-sub", "前十活跃证券（按成交额）"));
          var table = el("table", "pg-table");
          var headRow = el("tr");
          ["#", "证券", "成交额(亿)", "涨跌幅"].forEach(function (text) { headRow.appendChild(el("th", "", text)); });
          table.appendChild(headRow);
          top.forEach(function (row) {
            var tr = el("tr");
            tr.setAttribute("data-ctx", "stock");
            tr.setAttribute("data-symbol", row.code || "");
            tr.setAttribute("data-name", row.name || "");
            tr.appendChild(el("td", "", String(row.rank || "")));
            tr.appendChild(el("td", "", (row.name || "") + " " + (row.code || "")));
            tr.appendChild(el("td", "", row.deal_amt === null || row.deal_amt === undefined ? "无数据" : fmt(row.deal_amt, 2)));
            tr.appendChild(el("td", row.pct > 0 ? "up" : (row.pct < 0 ? "down" : ""), row.pct === null || row.pct === undefined ? "无数据" : (row.pct > 0 ? "+" : "") + fmt(row.pct, 2) + "%"));
            table.appendChild(tr);
          });
          body.appendChild(table);
        }
      }
      body.setAttribute("data-state", "default");
    }).catch(function (error) {
      P.setError(body, error.message || t("state.errorHint"), function () { loadNorthbound(); });
    });
  }

  /** L-02：北向个股净买入 Top10（无当日数据时空态 + 说明）。
   * @returns {void} 无返回值（异步渲染）
   */
  function loadNorthTop() {
    var body = document.getElementById("l02-body");
    if (!body) {
      return;
    }
    P.setLoading(body);
    api("/api/moneyflow/north-top", { limit: 10 }).then(function (data) {
      var card = document.getElementById("L-02");
      P.setBadges(card, data.source, data.caliber);
      var items = data.items || [];
      if (!items.length) {
        P.setEmpty(body, data.note || t("state.empty"), function () { loadNorthTop(); });
        return;
      }
      P.renderTable(body, [
        {
          title: t("watchlist.col.name"), cls: "l",
          /** 名称列：代码加粗 + 名称（北向/龙虎榜通用）。
           * @param {Object} row 数据行
           * @returns {HTMLElement} 单元格节点
           */
          render: function (row) {
            var box = el("span", "");
            box.appendChild(el("b", "", row.code));
            box.appendChild(document.createTextNode(" " + (row.name || "")));
            return box;
          }
        },
        { title: t("lhb.col.netBuy"), num: true, render: function (row) { return el("span", P.pctClass(row.net), fmt(row.net, 2)); } }
      ], items, function (row) {
        window.location.hash = "#/stock?symbol=" + row.code + "&market=a";
      });
    }).catch(function (error) {
      P.setError(body, error.message || t("state.errorHint"), function () { loadNorthTop(); });
    });
  }

  /** L-03：龙虎榜当日明细（默认最近披露日）。
   * @returns {void} 无返回值（异步渲染）
   */
  function loadLhb() {
    var body = document.getElementById("l03-body");
    if (!body) {
      return;
    }
    P.setLoading(body);
    api("/api/moneyflow/lhb", { limit: 100 }).then(function (data) {
      var card = document.getElementById("L-03");
      P.setBadges(card, data.source, data.caliber);
      var items = data.items || [];
      if (!items.length) {
        P.setEmpty(body, data.note || t("lhb.empty"), function () { loadLhb(); });
        return;
      }
      P.renderTable(body, [
        { title: t("lhb.col.date"), render: function (row) { return row.date; } },
        {
          title: t("watchlist.col.name"), cls: "l",
          /** 名称列：代码加粗 + 名称（北向/龙虎榜通用）。
           * @param {Object} row 数据行
           * @returns {HTMLElement} 单元格节点
           */
          render: function (row) {
            var box = el("span", "");
            box.appendChild(el("b", "", row.code));
            box.appendChild(document.createTextNode(" " + (row.name || "")));
            return box;
          }
        },
        { title: t("lhb.col.reason"), cls: "l", render: function (row) { return row.reason || t("value.none"); } },
        { title: t("lhb.col.pct"), num: true, render: function (row) { return el("span", P.pctClass(row.pct), fmtPct(row.pct)); } },
        { title: t("lhb.col.netBuy"), num: true, render: function (row) { return el("span", P.pctClass(row.net_buy), fmt(row.net_buy, 2)); } },
        { title: t("lhb.col.buy"), num: true, render: function (row) { return fmt(row.buy, 2); } },
        { title: t("lhb.col.sell"), num: true, render: function (row) { return fmt(row.sell, 2); } }
      ], items, function (row) {
        window.location.hash = "#/stock?symbol=" + row.code + "&market=a";
      });
    }).catch(function (error) {
      P.setError(body, error.message || t("state.errorHint"), function () { loadLhb(); });
    });
  }

  // 供工具栏「刷新当前视图」调用：按当前 hash 重新走一遍路由（P2-03）
  P.reload = P.route;  // 原 window.Pages={reload:route}：改为追加，避免覆盖已注册符号

  // ---- 导出到 window.Pages（供兄弟模块与 init 使用） ----
  P.MARKET_LABELS = MARKET_LABELS;
  P.loadLhb = loadLhb;
  P.loadLhbBroker = loadLhbBroker;
  P.loadLhbInstitution = loadLhbInstitution;
  P.loadNorthTop = loadNorthTop;
  P.loadNorthbound = loadNorthbound;
  P.loadWatchlist = loadWatchlist;
  P.marketLabel = marketLabel;
  P.paintWatchBulkBar = paintWatchBulkBar;
  P.paintWatchlistEmpty = paintWatchlistEmpty;
  P.renderLhb = renderLhb;
  P.renderWatchlist = renderWatchlist;
  P.renderWatchlistTree = renderWatchlistTree;
  P.watchBulkHost = watchBulkHost;
  P.watchRows = watchRows;
  P.watchSelected = watchSelected;
  P.watchlistGroup = watchlistGroup;
})(window.Pages = window.Pages || {});
