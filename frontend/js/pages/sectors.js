/* =============================================================
 * OpenBB 中文投研工作台 · pages/sectors.js
 * 板块分析 S-01~S-07
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
  var t = P.t;

  // =============================================================
  // 五、#/sectors 板块分析（S-01~S-07）
  // =============================================================
  var sectorsCache = {}; // key=market|kind → 数据，避免 Tab 来回切换重复请求

  /** 构建板块页骨架（只建一次，之后仅更新内容）。
   * @param {HTMLElement} container DOM 容器节点
   */
  function buildSectorsShell(container) {
    container.innerHTML = "";
    var toolbar = el("div", "page-toolbar");

    // S-01 市场切换
    var marketTabs = el("div", "pg-tabs");
    marketTabs.id = "S-01";
    [["a", t("sectors.market.cn")], ["hk", t("sectors.market.hk")], ["us", t("sectors.market.us")]].forEach(function (item) {
      var button = el("button", "pg-tab", item[1]);
      button.type = "button";
      button.setAttribute("data-market", item[0]);
      marketTabs.appendChild(button);
    });
    toolbar.appendChild(marketTabs);

    // S-02 行业/概念切换
    var kindTabs = el("div", "pg-tabs");
    kindTabs.id = "S-02";
    [["industry", t("sectors.kind.industry")], ["concept", t("sectors.kind.concept")]].forEach(function (item) {
      var button = el("button", "pg-tab", item[1]);
      button.type = "button";
      button.setAttribute("data-kind", item[0]);
      kindTabs.appendChild(button);
    });
    toolbar.appendChild(kindTabs);
    toolbar.appendChild(el("span", "pg-spacer"));

    var refresh = el("button", "pg-btn", t("action.reload"));
    refresh.type = "button";
    refresh.id = "sectors-refresh";
    toolbar.appendChild(refresh);
    container.appendChild(toolbar);

    var grid = el("div", "pg-grid");
    grid.id = "sectors-grid";

    // 左主栏 S-03 热力图
    var heat = P.buildCard("S-03", t("sectors.heatTitle"), "g-7", t("sectors.heatSub"));
    var heatBody = heat.body;
    heatBody.classList.add("tall");
    heatBody.id = "s03-body";
    grid.appendChild(heat.card);

    // 右辅栏：S-04/S-05 排行 + S-06 摘要
    var rightStack = el("div", "g-5");
    rightStack.style.display = "flex";
    rightStack.style.flexDirection = "column";
    rightStack.style.gap = "16px";

    var rank = P.buildCard("S-04", t("sectors.rankIndustry"), "", "");
    rank.card.setAttribute("data-block", "rank"); // 行业排行（常驻，不再与 S-05 互换身份）
    rank.body.id = "s04-body";
    var rankBody = rank.body;
    rankBody.classList.add("short");
    rightStack.appendChild(rank.card);

    var summary = P.buildCard("S-06", t("sectors.summaryTitle"), "", "");
    summary.body.id = "s06-body";
    rightStack.appendChild(summary.card);
    grid.appendChild(rightStack);

    // S-05 概念排行：DS-07 整改——独立第二表（基准 §3.1：S-05 概念排行 .g-12 或 .g-7），
    // 此前与 S-04 共用同一张卡的 identity（id 在 S-04/S-05 间切换），概念口径要看就得切走行业口径。
    var concept = P.buildCard("S-05", t("sectors.rankConcept"), "g-12", t("sectors.rankConceptSub"));
    concept.body.id = "s05-body";
    concept.body.classList.add("short");
    grid.appendChild(concept.card);

    // S-07 资金流向通栏
    var flow = P.buildCard("S-07", t("sectors.flowTitle"), "g-12", t("sectors.flowSub"));
    flow.body.id = "s07-body";
    grid.appendChild(flow.card);

    container.appendChild(grid);

    marketTabs.addEventListener("click", function (event) {
      var button = event.target.closest(".pg-tab");
      if (!button) {
        return;
      }
      state.sectors.market = button.getAttribute("data-market");
      loadSectors();
    });
    kindTabs.addEventListener("click", function (event) {
      var button = event.target.closest(".pg-tab");
      if (!button) {
        return;
      }
      state.sectors.kind = button.getAttribute("data-kind");
      loadSectors();
    });
    refresh.addEventListener("click", function () {
      sectorsCache = {};
      loadSectors(true);
    });
  }

  /** 同步 Tab 选中态。 */
  function syncSectorTabs() {
    document.querySelectorAll("#S-01 .pg-tab").forEach(function (button) {
      button.classList.toggle("active", button.getAttribute("data-market") === state.sectors.market);
    });
    document.querySelectorAll("#S-02 .pg-tab").forEach(function (button) {
      button.classList.toggle("active", button.getAttribute("data-kind") === state.sectors.kind);
    });
  }

  /** 板块市场枚举归一（RT-03/RT-05）：行情/个股侧用 a/hk/us，板块侧用 cn/hk/us。
   * 跨页带 market=a 跳板块时统一映射为 cn，避免后端 INVALID_PARAM 与页头误显示「美股」。
   * @param {*} market 市场标识（a/cn/hk/us 或空）
   * @returns {string} 板块侧枚举：cn / hk / us
   */
  function normSectorMarket(market) {
    var value = String(market === null || market === undefined ? "" : market).toLowerCase();
    if (value === "hk") {
      return "hk";
    }
    if (value === "us") {
      return "us";
    }
    return "a";  // a / cn / 空值 一律视为 A 股（后端已统一 a 口径，此处仅做兜底）
  }

  /** 渲染板块页。
   * @param {Object} params 查询参数对象
   */
  function renderSectors(params) {
    var container = document.getElementById(VIEW_IDS.sectors);
    if (params.market) {
      state.sectors.market = normSectorMarket(params.market);
    }
    if (params.kind) {
      state.sectors.kind = params.kind;
    }
    if (!container.querySelector("#sectors-grid")) {
      buildSectorsShell(container);
    }
    syncSectorTabs();
    loadSectors();
  }

  /** 拉取并渲染板块页全部区块。
   * @param {boolean} force 是否强制刷新（true 时跳过本地缓存）
   */
  function loadSectors(force) {
    var market = state.sectors.market;
    var kind = state.sectors.kind;
    var cacheKey = market + "|" + kind;
    var cards = {
      s03: document.getElementById("S-03"),
      s04: document.getElementById("S-04"),
      s05: document.getElementById("S-05"),
      s06: document.getElementById("S-06"),
      s07: document.getElementById("S-07")
    };
    var bodies = {
      s03: document.getElementById("s03-body"),
      s04: document.getElementById("s04-body"),
      s05: document.getElementById("s05-body"),
      s06: document.getElementById("s06-body"),
      s07: document.getElementById("s07-body")
    };
    if (!bodies.s03) {
      return;
    }

    if (force) {
      sectorsCache = {};
    }
    if (sectorsCache[cacheKey]) {
      paintSectors(sectorsCache[cacheKey]);
      return;
    }
    P.setLoading(bodies.s03);
    P.setLoading(bodies.s04);
    P.setLoading(bodies.s05);
    P.setLoading(bodies.s06);
    P.setLoading(bodies.s07);

    // DS-07：行业与概念两张排行表各自独立取数、同时常驻，互不替代；
    // S-02 的行业/概念切换只决定 S-03 热力图口径（基准 §3.4 两表分别绑定 industry/concept 接口）。
    var conceptTask = market === "a"
      ? api("/api/sector/concept", { market: "a" })
      : Promise.resolve(null);
    // 资金流仅 A股有免费通道（同花顺行业资金流）；港美股不发请求，避免整页因单块 4xx 被判失败
    var flowTask = market === "a"
      ? api("/api/sector/fundflow", { market: market })
      : Promise.resolve(null);
    Promise.all([
      api("/api/sector/industry", { market: market }),
      conceptTask,
      api("/api/sector/heatmap", { market: market, kind: kind }),
      api("/api/sector/summary", { market: market }),
      flowTask
    ])
      .then(function (result) {
        var payload = {
          market: market,
          kind: kind,
          list: result[0],
          concept: result[1],
          heat: result[2],
          summary: result[3],
          flow: result[4]
        };
        sectorsCache[cacheKey] = payload;
        paintSectors(payload);
      })
      .catch(function (error) {
        var retry = function () {
          loadSectors(true);
        };
        P.setError(bodies.s03, error.message, retry);
        P.setError(bodies.s04, error.message, retry);
        P.setError(bodies.s05, error.message, retry);
        P.setError(bodies.s06, error.message, retry);
        P.setError(bodies.s07, error.message, retry);
      });
  }

  /** 渲染板块页（默认态）。
   * @param {*} payload 渲染载荷（聚合后的数据）
   * @returns {*} 处理结果
   */
  function paintSectors(payload) {
    var market = payload.market;
    var bodies = {
      s03: document.getElementById("s03-body"),
      s04: document.getElementById("s04-body"),
      s05: document.getElementById("s05-body"),
      s06: document.getElementById("s06-body"),
      s07: document.getElementById("s07-body")
    };
    var cards = {
      s03: document.getElementById("S-03"),
      s04: document.getElementById("S-04"),
      s05: document.getElementById("S-05"),
      s06: document.getElementById("S-06"),
      s07: document.getElementById("S-07")
    };
    if (!bodies.s03) {
      return;
    }
    var degrade = payload.list.source && payload.list.source !== "同花顺" && market === "a";

    // ---- S-03 热力图 ----
    P.setBadges(cards.s03, payload.heat.source, payload.heat.caliber, degrade);
    bodies.s03.innerHTML = "";
    var nodes = (payload.heat.nodes || []).filter(function (node) {
      return node.value !== null && node.value !== undefined;
    });
    if (nodes.length === 0) {
      P.setEmpty(bodies.s03, t("state.empty"), function () {
        loadSectors(true);
      });
    } else {
      var chartBox = el("div", "pg-chart");
      chartBox.id = "s03-chart";
      bodies.s03.appendChild(chartBox);
      var chart = P.mountChart(chartBox);
      if (chart) {
        // UI-03 整改：按面积占比分档控制标签，小面积块不再压字
        // （基准 §3.3「最小字号 10px，溢出 2 行截断不压字」）
        var totalValue = nodes.reduce(function (sum, node) {
          return sum + (Number(node.value) || 0);
        }, 0) || 1;
        var data = nodes.map(function (node) {
          var pct = node.pct;
          var color = pct > 0 ? COLOR_UP : pct < 0 ? COLOR_DOWN : COLOR_MUTED;
          var share = (Number(node.value) || 0) / totalValue;
          // 三档：大块两行（板块名 + 涨跌幅）；中块一行（取整百分比，窄块也放得下）；
          // 更小面积块**不显示标签、仅着色**——避免 ECharts 把「-1.64%」截成「-1.6…」这种压字残句，
          // 精确值仍可经悬浮提示与点击进入板块页查看（基准 §3.3：最小 10px + 溢出截断不压字）。
          var labelCfg;
          if (share >= 0.02) {
            labelCfg = { show: true, fontSize: 10 };
          } else if (share >= 0.012) {
            labelCfg = { show: true, fontSize: 10, formatter: function () {
              return pct === null || pct === undefined ? t("value.none") : Math.round(pct) + "%";
            } };
          } else {
            labelCfg = { show: false };
          }
          return {
            name: node.name,
            value: node.value,
            pct: pct,
            amount: node.amount,
            lead: node.lead_stock,
            label: labelCfg,
            itemStyle: { color: color, borderColor: "#0b0b0c", borderWidth: 1 }
          };
        });
        chart.setOption({
          backgroundColor: "transparent",
          tooltip: {
            /** 热力图悬浮说明：名称 + 涨跌幅 + 成交额（亿元）+ 领涨股。
             * @param {*} info 信息对象
             * @returns {*} 处理结果
             */
            formatter: function (info) {
              var source = info.data || {};
              return (
                "<b>" + info.name + "</b><br/>" + t("sectors.tooltip.change") + fmtPct(source.pct) +
                "<br/>" + t("sectors.tooltip.amount") + (source.amount === null || source.amount === undefined ? t("value.none") : fmt(source.amount) + " 亿元") +
                "<br/>" + t("sectors.tooltip.lead") + (source.lead || t("value.none"))
              );
            }
          },
          series: [
            {
              type: "treemap",
              roam: false,
              nodeClick: false, // 点击由 chart.on('click') 统一处理（评审 §3 #9）
              breadcrumb: { show: false },
              width: "100%",
              height: "100%",
              itemStyle: { gapWidth: 1 },
              label: {
                show: true,
                fontSize: 10,
                color: "#fff",
                // 两行上限 + 截断省略，避免长板块名在小块里挤成两三行压字
                overflow: "truncate",
                lineOverflow: "truncate",
                ellipsis: "…",
                lineHeight: 12,
                /** 热力图块内标签：板块名换行 + 涨跌幅百分比（缺值显示「无数据」）。
                 * @param {*} info 信息对象
                 * @returns {*} 处理结果
                 */
                formatter: function (info) {
                  var pctText = info.data.pct === null ? t("value.none") : Number(info.data.pct).toFixed(2) + "%";
                  return info.name + "\n" + pctText;
                }
              },
              data: data
            }
          ]
        });
        chart.on("click", function (info) {
          if (info && info.name) {
            jumpToSectorVal(info.name, market);
          }
        });
      }
      // 图表渲染完成即视为默认态，避免四态巡检一直停在 loading
      bodies.s03.setAttribute("data-state", "default");
    }

    // ---- S-04 行业排行表 ----
    paintRankTable(bodies.s04, cards.s04, payload.list, market, degrade);
    // ---- S-05 概念排行表（DS-07：独立第二表，不再与 S-04 互换） ----
    if (payload.concept) {
      paintRankTable(bodies.s05, cards.s05, payload.concept, market,
        payload.concept.source && payload.concept.source !== "同花顺" && market === "a");
    } else {
      P.setBadges(cards.s05, "", "", false);
      P.setEmpty(bodies.s05, t("sectors.conceptOverseas"), function () {
        loadSectors(true);
      });
    }

    // ---- S-06 摘要 ----
    P.setBadges(cards.s06, payload.summary.source, payload.summary.caliber, degrade);
    paintSummary(bodies.s06, payload.summary);

    // ---- S-07 资金流向（行业口径常驻：概念板块无免费资金流口径，不在两张排行表之间切换） ----
    if (market !== "a") {
      P.setBadges(cards.s07, "", "", false);
      P.setEmpty(bodies.s07, t("sectors.overseasFlow"), function () {
        loadSectors(true);
      });
    } else if (payload.flow) {
      P.setBadges(cards.s07, payload.flow.source, payload.flow.caliber, degrade);
      var flowRows = (payload.flow.items || []).slice(0, 40);
      P.renderTable(
        bodies.s07,
        [
          { title: t("sectors.col.board"), key: "name", cls: "name" },
          { title: t("sectors.col.netInflow"), cls: "num", render: function (row) { return P.pctNode(row.net_inflow); } },
          { title: t("sectors.col.inflow"), cls: "num", render: function (row) { return fmt(row.inflow); } },
          { title: t("sectors.col.outflow"), cls: "num", render: function (row) { return fmt(row.outflow); } },
          // 源不提供分项：显示「无数据」而不是补 0（§9）
 // @param {*} row 单行记录
          { title: t("sectors.col.superBig"), cls: "num", render: function (row) { return fmt(row.super_big); } },
          { title: t("sectors.col.big"), cls: "num", render: function (row) { return fmt(row.big); } }
        ],
        flowRows,
        null,
        null
      );
    }
  }

  /** 渲染一张板块排行表（S-04 行业 / S-05 概念共用同一渲染契约）。
   * @param {HTMLElement} body 区块容器（.pg-body，四态 data-state 挂在此节点）
   * @param {HTMLElement} card 卡片根节点（右上角双徽章挂这里）
   * @param {Object} list 接口 data 段（source/caliber/items）
   * @param {string} market 市场标识（cn/hk/us），跳转时带上
   * @param {boolean} degrade 是否降级（源非首选）
   * @returns {void} 无返回值
   */
  function paintRankTable(body, card, list, market, degrade) {
    if (!body || !list) {
      return;
    }
    P.setBadges(card, list.source, list.caliber, degrade);
    if (list.error) {
      P.setError(body, list.error, function () { loadSectors(true); });
      return;
    }
    var rows = list.items || [];
    if (!rows.length) {
      P.setEmpty(body, t("state.empty"), function () { loadSectors(true); });
      return;
    }
    // 概念口径 800+ 行，排行表只渲染涨幅前 60（同源同序，不改变统计口径）；
    // 行业仅 90 行，全量渲染不做截断。
    var displayRows = list.kind === "concept" ? rows.slice(0, 60) : rows;
    P.renderTable(
      body,
      [
        { title: t("sectors.col.board"), key: "name", cls: "name" },
        { title: t("sectors.pctCol"), cls: "num", render: function (row) { return P.pctNode(row.pct); } },
        { title: t("sectors.amountCol"), cls: "num", render: function (row) { return fmt(row.amount); } },
        { title: t("sectors.col.lead"), key: "lead_stock" },
        { title: t("sectors.col.leadPct"), cls: "num", render: function (row) { return P.pctNode(row.lead_pct); } }
      ],
      displayRows,
      function (row) {
        jumpToSectorVal(row.name, market);
      },
      "short"
    );
  }

  /** 摘要卡：领涨/领跌/净流入 Top3 + 集中度。
   * @param {HTMLElement} body 区块容器（.pg-body，四态 data-state 挂在此节点）
   * @param {*} summary 摘要数据
   */
  function paintSummary(body, summary) {
    body.innerHTML = "";
    if (!summary || (!summary.gainers.length && !summary.losers.length)) {
      P.setEmpty(body, t("state.empty"), function () {
        loadSectors(true);
      });
      return;
    }
    /** 板块摘要分组：按 title 输出小标题，并逐条渲染 items 的 valueKey 指标。
     * @param {*} title 标题
     * @param {Array} items 记录列表
     * @param {*} valueKey 取值字段名
     */
    function group(title, items, valueKey) {
      var wrap = el("div", "pg-summary-row");
      var label = el("div", "pg-card-sub", title);
      body.appendChild(label);
      items.forEach(function (item) {
        var box = el("div", "pg-summary-item");
        box.appendChild(el("div", "pg-summary-label", item.name));
        var value = el("div", "pg-summary-value " + P.pctClass(item[valueKey]), fmt(item[valueKey]) + (valueKey === "pct" ? "%" : " 亿"));
        box.appendChild(value);
        wrap.appendChild(box);
      });
      body.appendChild(wrap);
    }
    group(t("sectors.summary.leaders"), summary.gainers || [], "pct");
    group(t("sectors.summary.losers"), summary.losers || [], "pct");
    if (summary.inflow && summary.inflow.length) {
      group(t("sectors.summary.inflow"), summary.inflow, "net_inflow");
    }
    var conc = el("div", "pg-card-sub");
    conc.textContent =
      t("val.kv.concentration") + (summary.concentration === null ? t("value.none") : summary.concentration + "%");
    body.appendChild(conc);
    body.setAttribute("data-state", "default");
  }

  /** 跳转 #1/#9：板块 → 单板块估值。
   * @param {*} board 板块名称
   * @param {*} market 市场标识：a/hk/us
   */
  function jumpToSectorVal(board, market) {
    window.location.hash =
      "#/sector-val?board=" + encodeURIComponent(board) + "&market=" + normSectorMarket(market);
  }

  // ---- 导出到 window.Pages（供兄弟模块与 init 使用） ----
  P.buildSectorsShell = buildSectorsShell;
  P.jumpToSectorVal = jumpToSectorVal;
  P.loadSectors = loadSectors;
  P.normSectorMarket = normSectorMarket;
  P.paintRankTable = paintRankTable;
  P.paintSectors = paintSectors;
  P.paintSummary = paintSummary;
  P.renderSectors = renderSectors;
  P.sectorsCache = sectorsCache;
  P.syncSectorTabs = syncSectorTabs;
})(window.Pages = window.Pages || {});
