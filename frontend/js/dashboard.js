/* =============================================================
 * OpenBB 中文投研工作台 · 组件化仪表板（阶段 6）
 * 能力：48 列网格 / 组件库（按 /api/widgets 注册表）/ 新建仪表板 / 模板库 /
 *       拖拽排序 / 宽度调整（吸附到 48 列栅格）/ 保存布局（localStorage 持久化）/
 *       表格排序与 CSV 导出（UTF-8 BOM）/ 指标卡 / 资讯流 / 情绪面板 / 图表。
 * 约束：配色只取 theme.css（--up/--down），不新增配色；无演示数据，全部走真实接口。
 * ============================================================= */

(function () {
  "use strict";

  // P1-13：共享工具别名（t / el / toast 原本各 IIFE 重复定义于此）
  // 注意：必须先声明再使用 —— SPAN_LABEL 初始化即调用 t()，若别名在其后声明会因
  // 变量提升得到 undefined，整个 IIFE 抛 TypeError，仪表板将整页空白。
  var t = window.Utils.t;
  var el = window.Utils.el;
  var toast = window.Utils.toast;
  // R6-01：buildCard 唯一实现挂在 Utils（原先裸调用未导出的函数 → 管理页 ReferenceError 整页空白）
  var buildCard = window.Utils.buildCard;
  // 本 IIFE 内需独立引用格式化/涨跌色工具（pages.js 的 fmt/pctClass 不在本作用域）
  var fmt = window.Utils.fmt;
  var fmtPct = window.Utils.fmtPct;
  var pctClass = window.Utils.trendClass;

  var STORAGE_KEY = "openbb-dashboards-v1";
  var SPANS = [12, 16, 20, 24, 28, 32, 48];
  var SPAN_LABEL = { 12: "1/4", 16: "1/3", 20: "5/12", 24: "1/2", 28: "7/12", 32: "2/3", 48: t("dashboard.span.full") };

  var state = { dashboards: [], activeId: "", registry: null };

  // R6-03：图表实例生命周期集中管理
  // 原缺陷：每次 renderChart 都 echarts.init 且从不 dispose，并在渲染函数内
  // window.addEventListener("resize") —— 实测 6 轮刷新 JS 堆 260MB→2394MB、
  // canvas 1→0（图表消失）。此处统一「重绘前释放 + 全局单一 resize」。
  var resizeListenerBound = false;

  /** 释放 root 子树内所有 ECharts 实例（重复 init 前必须调用）。
   * @param {HTMLElement} root 待清理的 DOM 子树（可为 null）
   * @returns {void} 无返回值
   */
  function disposeChartsIn(root) {
    if (!root || !window.echarts || typeof root.querySelectorAll !== "function") {
      return;
    }
    root.querySelectorAll(".pg-chart").forEach(function (node) {
      var instance = window.echarts.getInstanceByDom(node);
      if (instance) {
        instance.dispose();
      }
    });
  }

  /** 绑定全局唯一的 resize 监听器：遍历仪表板内的图表实例统一 resize。
   * 严禁在渲染函数里逐次 addEventListener（监听器只增不减 → 抖动 + 泄漏）。
   * @returns {void} 无返回值
   */
  function bindGlobalChartResize() {
    if (resizeListenerBound) {
      return;
    }
    resizeListenerBound = true;
    window.addEventListener("resize", function () {
      if (!window.echarts) {
        return;
      }
      document.querySelectorAll("#dash-grid .pg-chart").forEach(function (node) {
        var instance = window.echarts.getInstanceByDom(node);
        if (instance) {
          instance.resize();
        }
      });
    });
  }


  /** 本地化格式。
   * @returns {*} 处理结果
   */
  function L() {
    return window.L10N;
  }

  /** 读取布局：先取本地缓存秒开，再异步对齐后端 data/dashboards.json。
   * 后端有数据时以后端为准（多标签页/换浏览器后仍能恢复），本地仅作兜底缓存。
   *
   * R6-02 / R6-08 修复：
   *  1) 加 `state._serverLoaded` 一次性标记 —— 原先每次调用都发请求并在回调里无条件
   *     `renderDashboard()`，与「点刷新」叠加成一次点击 3 次全量渲染（实测 renderCalls 1→3）；
   *  2) 回调里先比较数据是否真的变了，未变则不重绘；
   *  3) 后端为空而本地有时，主动回写一次（首次同步），否则管理页永远读到 0 个仪表板。
   *
   * @param {Object} opts 选项：`{force:true}` 强制重新拉取，`{render:false}` 不触发重绘
   * @returns {void} 无返回值（异步对齐，失败保留本地缓存）
   */
  function loadState(opts) {
    var options = opts || {};
    var raw = null;
    try {
      raw = localStorage.getItem(STORAGE_KEY);
    } catch (error) {
      raw = null;
    }
    if (raw) {
      try {
        var parsed = JSON.parse(raw);
        if (parsed && Array.isArray(parsed.dashboards) && parsed.dashboards.length) {
          state.dashboards = parsed.dashboards;
          state.activeId = parsed.activeId || parsed.dashboards[0].id;
        }
      } catch (error) {
        // 损坏则重建，不影响使用
      }
    }
    // 只有「从未初始化」（本地无缓存键）才自动建默认板；
    // 用户把仪表板删光时 localStorage 会存成空数组，此时必须停在「无仪表板」引导态，
    // 否则一刷新就自动补回默认板，「无仪表板」状态永远不可达（四态验收要求）。
    var hadCache = false;
    try {
      hadCache = localStorage.getItem(STORAGE_KEY) !== null;
    } catch (error) {
      hadCache = false;
    }
    if (!state.dashboards.length && !hadCache) {
      state.dashboards = [buildDefaultDashboard()];
      state.activeId = state.dashboards[0].id;
    }
    // 同一会话只对齐一次后端；需要强制刷新时传 {force:true}
    if (state._serverLoaded && !options.force) {
      return;
    }
    state._serverLoaded = true;
    var shouldRender = options.render !== false;
    // R3-02：异步拉取后端配置（失败时保留本地缓存，不阻断渲染）
    fetchJson("/api/dashboards", {}).then(function (data) {
      var serverList = data && Array.isArray(data.dashboards) ? data.dashboards : [];
      if (serverList.length) {
        var changed = JSON.stringify(serverList) !== JSON.stringify(state.dashboards);
        state.dashboards = serverList;
        state.activeId = data.activeId || serverList[0].id;
        try {
          localStorage.setItem(STORAGE_KEY, JSON.stringify({
            dashboards: state.dashboards,
            activeId: state.activeId
          }));
        } catch (error) {
          // 本地缓存写失败不影响已渲染内容
        }
        if (changed && shouldRender && typeof window.renderDashboard === "function") {
          window.renderDashboard();
        }
      } else if (state.dashboards.length) {
        // 首次同步：后端空、本地有默认板 → 静默回写，保证两端一致（管理页可见）
        saveState(true);
      }
    }).catch(function () {
      // 后端不可用时静默降级到本地缓存；允许下次再试
      state._serverLoaded = false;
    });
  }

  /** 保存布局：先写本地缓存，再异步落盘后端 data/dashboards.json。
   * @param {*} silent 是否静默（true 时不弹提示）
   * @returns {void} 无返回值（异步落盘，失败以 toast 提示）
   */
  function saveState(silent) {
    var payload = { dashboards: state.dashboards, activeId: state.activeId };
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    } catch (error) {
      // 本地缓存失败仍尝试后端落盘
    }
    fetch("/api/dashboards", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (response) {
      return response.json();
    }).then(function (body) {
      if (body.error) {
        toast(t("dashboard.saveFailed"));
      } else if (!silent) {
        toast(t("dashboard.saved"));
      }
    }).catch(function () {
      // 后端不可用时本地缓存已生效，提示降级原因
      if (!silent) {
        toast(t("dashboard.saveLocalOnly"));
      }
    });
  }

  /** 重命名当前仪表板（R3-02）。
   * @returns {void}
   */
  function renameDashboard() {
    var dash = activeDashboard();
    if (!dash || !window.UI) {
      return;
    }
    window.UI.modal({
      title: t("dashboard.rename"),
      input: true,
      defaultValue: dash.name,
      /** 确认重命名：空名保持原名，成功后落盘并重绘。
       * @param {*} value 输入框内容
       */
      onConfirm: function (value) {
        var name = String(value || "").trim();
        if (!name) {
          return;
        }
        dash.name = name;
        saveState(true);
        render();
      }
    });
  }

  /** 复制当前仪表板（含全部组件，名称追加「副本」）（R3-02）。
   * @returns {void}
   */
  function duplicateDashboard() {
    var dash = activeDashboard();
    if (!dash) {
      return;
    }
    var copy = {
      id: "dash-" + Date.now(),
      name: dash.name + t("dashboard.copySuffix"),
      group: dash.group || "",
      widgets: dash.widgets.map(function (item) {
        // 复制时必须带上 span/h/params，否则副本丢失尺寸与改参数结果
        var copyItem = { id: item.id, span: item.span };
        if (item.h) { copyItem.h = item.h; }
        if (item.params) { copyItem.params = Object.assign({}, item.params); }
        return copyItem;
      })
    };
    state.dashboards.push(copy);
    state.activeId = copy.id;
    saveState(true);
    render();
  }

  /** 把当前仪表板移入/新建分组（R3-02）。
   * @returns {void}
   */
  function groupDashboard() {
    var dash = activeDashboard();
    if (!dash || !window.UI) {
      return;
    }
    window.UI.modal({
      title: t("dashboard.groupTitle"),
      message: t("dashboard.groupHint"),
      input: true,
      defaultValue: dash.group || "",
      /** 确认分组：留空即移出分组。
       * @param {*} value 分组名
       */
      onConfirm: function (value) {
        dash.group = String(value || "").trim();
        saveState(true);
        render();
      }
    });
  }

  /** 删除当前仪表板（含组件时二次确认）（R3-02）。
   * @returns {void}
   */
  function deleteDashboard() {
    var dash = activeDashboard();
    if (!dash || !window.UI) {
      return;
    }
    var isLast = state.dashboards.length <= 1;
    window.UI.modal({
      title: t("dashboard.remove"),
      message: isLast
        ? (dash.widgets.length
            ? t("dashboard.deleteLastWarnWidgets", { name: dash.name, count: dash.widgets.length })
            : t("dashboard.deleteLastWarn", { name: dash.name }))
        : (dash.widgets.length
            ? t("dashboard.deleteWarnWidgets", { name: dash.name, count: dash.widgets.length })
            : t("dashboard.deleteWarn", { name: dash.name })),
      confirmText: t("action.delete"),
      /** 确认删除：移除后切到第一个仪表板并落盘。
       * @returns {void} 无返回值
       */
      onConfirm: function () {
        state.dashboards = state.dashboards.filter(function (item) {
          return item.id !== dash.id;
        });
        // 删光时不能取 dashboards[0].id（会 TypeError），置空走「无仪表板」引导态
        state.activeId = state.dashboards.length ? state.dashboards[0].id : "";
        saveState(true);
        render();
      }
    });
  }

  /** 调整仪表板顺序（R3-02 排序）。
   * @param {number} delta 位移量（-1 前移 / +1 后移）
   * @returns {void}
   */
  function moveDashboard(delta) {
    var index = -1;
    for (var i = 0; i < state.dashboards.length; i += 1) {
      if (state.dashboards[i].id === state.activeId) {
        index = i;
        break;
      }
    }
    var target = index + delta;
    if (index < 0 || target < 0 || target >= state.dashboards.length) {
      return;
    }
    var moved = state.dashboards.splice(index, 1)[0];
    state.dashboards.splice(target, 0, moved);
    saveState(true);
    render();
  }

  /** 默认仪表板（全部使用真实接口，无演示数据）。
   * P2-01：不再硬编码 688012 等个股标的；默认为市场总览类组件，
   * 用户可从「组件库」按需添加个股组件（自动带入当前标的）。
   * @returns {Object} 处理结果
   */
  function buildDefaultDashboard() {
    return {
      id: "dash-" + Date.now(),
      name: t("dashboard.defaultName"),
      widgets: [
        { id: "S-03", span: 28 },
        { id: "S-06", span: 20 },
        { id: "S-04", span: 28 },
        { id: "N-03", span: 20 },
        { id: "N-02", span: 48 }
      ]
    };
  }

  /** 当前仪表板。
   * @returns {*} 处理结果
   */
  function activeDashboard() {
    for (var i = 0; i < state.dashboards.length; i += 1) {
      if (state.dashboards[i].id === state.activeId) {
        return state.dashboards[i];
      }
    }
    state.activeId = state.dashboards[0].id;
    return state.dashboards[0];
  }

  /** 拉取注册表（35 个组件元数据）。
   * @returns {*} 处理结果
   */
  function loadRegistry() {
    if (state.registry) {
      return Promise.resolve(state.registry);
    }
    return fetch("/api/widgets", { cache: "no-store" })
      .then(function (response) { return response.json(); })
      .then(function (body) {
        state.registry = {};
        ((body.data && body.data.items) || []).forEach(function (item) {
          state.registry[item.id] = item;
        });
        return state.registry;
      });
  }

  // =============================================================
  // 组件取数与渲染配方（按组件 id 映射到真实接口）
  // =============================================================
  var RECIPES = {
    // P2-01：ST 系列不再硬编码 symbol，改为由 addWidgetById 透传当前标的（个股页/Ctrl+K）
    "ST-01": { kind: "panel", path: "/api/stock/realtime-panel", params: {}, pick: "quote" },
    "ST-02": { kind: "chart", path: "/api/stock/realtime-panel", params: {}, pick: "kline", x: "date", y: "close" },
    "ST-04": { kind: "metrics", path: "/api/stock/realtime-panel", params: {}, pick: "fund_dist" },
    "S-03": { kind: "chart", path: "/api/sector/heatmap", params: { market: "a", kind: "industry" }, pick: "nodes", x: "name", y: "pct", bar: true },
    "S-04": { kind: "table", path: "/api/sector/industry", params: { market: "a" }, cols: ["name", "pct", "amount", "lead_stock"] },
    "S-05": { kind: "table", path: "/api/sector/concept", params: { market: "a" }, cols: ["name", "pct", "amount", "lead_stock"] },
    "S-06": { kind: "metrics", path: "/api/sector/summary", params: { market: "a" }, pick: "gainers" },
    "S-07": { kind: "table", path: "/api/sector/fundflow", params: { market: "a" }, cols: ["name", "net_inflow", "inflow", "outflow"] },
    "N-02": { kind: "news", path: "/api/news/ai-feed", params: { limit: 12, sort: "relevance" } },
    "N-03": { kind: "metrics", path: "/api/sentiment/market", params: { region: "cn" }, pick: "self" },
    "F-03": { kind: "table", path: "/api/fund/performance", params: { code: "017811" }, cols: ["period", "growth", "rank"] },
    "F-04": { kind: "table", path: "/api/fund/holding", params: { code: "017811" }, cols: ["code", "name", "ratio", "value"] },
    "P-01": { kind: "metrics", path: "/api/portfolio/analysis", params: {}, pick: "totals" },
    "P-03": { kind: "table", path: "/api/portfolio/analysis", params: {}, cols: ["market_label", "symbol", "name", "market_value", "pnl", "pnl_pct"] },
    "V-01": { kind: "chart", path: "/api/sector/kline", params: { board: t("dashboard.defaultBoard"), market: "a" }, pick: "items", x: "date", y: "close" },
    // 养基宝（需已绑定；未绑定时后端返回 UPSTREAM_ERROR，卡片走错误态）
    "Y-05": { kind: "yjbCollect", path: "/api/fund/yjb/collect", params: {} },
    "Y-06": { kind: "table", path: "/api/fund/yjb/holdings", params: { account_id: "all" },
              cols: ["code", "name", "pct", "money", "hold_earn", "account_title"] },

    // ===== R6-04：补齐 23 个组件渲染器（原覆盖率 17/49 = 35%，其余组件添加后只显示"无渲染器"） =====
    // 行情类
    "ST-03": { kind: "chart", path: "/api/stock/realtime-panel", params: {}, pick: "minute", x: "time", y: "price", labels: false },
    "ST-05": { kind: "news", path: "/api/stock/realtime-panel", params: {}, pick: "news" },
    "ST-06": { kind: "profile", path: "/api/stock/realtime-panel", params: {}, pick: "forecast",
               fields: [["org_count", "机构数", "int"], ["target_low", "目标价下限", "num"],
                        ["target_avg", "目标价均值", "num"], ["target_high", "目标价上限", "num"],
                        ["target_samples", "样本数", "int"], ["caliber_note", "口径", "text"]] },
    "ST-07": { kind: "news", path: "/api/stock/realtime-panel", params: {}, pick: "news" },
    "ST-08": { kind: "profile", path: "/api/stock/profile", params: {},
               fields: [["name", "名称", "text"], ["industry", "行业", "text"], ["board", "板块", "text"],
                        ["listing_date", "上市日", "text"], ["reg_capital", "注册资本(万)", "num"],
                        ["total_mv", "总市值(元)", "num0"], ["pe_ttm", "PE(TTM)", "num"],
                        ["pb", "PB", "num"], ["employees", "员工数", "int"], ["province", "省份", "text"]] },
    "ST-09": { kind: "finTable", path: "/api/stock/financial-summary", params: {} },
    "ST-10": { kind: "table", path: "/api/stock/holders", params: {}, pick: "top10",
               cols: ["rank", "name", "ratio", "hold_num", "change_text"] },
    // 板块估值
    "V-02": { kind: "table", path: "/api/sector/kline", params: { board: t("dashboard.defaultBoard"), market: "a" },
              pick: "items", cols: ["date", "close", "open", "high", "low", "volume"] },
    "V-03": { kind: "table", path: "/api/sector/cons", params: { board: t("dashboard.defaultBoard"), market: "a" },
              pick: "items", cols: ["code", "name", "pct", "price", "weight"] },
    "V-04": { kind: "news", path: "/api/news/sector-search", params: { sector: t("dashboard.defaultBoard") }, pick: "items" },
    // 资讯
    "N-05": { kind: "table", path: "/api/news/financial-search", params: { market: "a", q: "茅台" }, pick: "list",
              cols: ["company", "title", "type", "pubTime", "source"] },
    // 基金
    "F-01": { kind: "profile", path: "/api/fund/info", params: { code: "017811" },
              fields: [["name", "基金简称", "text"], ["full_name", "基金全称", "text"],
                       ["keeper", "基金公司", "text"], ["manager", "基金经理", "text"],
                       ["found_date", "成立日", "text"], ["scale", "规模", "text"],
                       ["risk_level", "风险等级", "text"], ["trustee", "托管银行", "text"]] },
    "F-02": { kind: "chart", path: "/api/fund/nav", params: { code: "017811", size: 120 },
              pick: "items", x: "date", y: "nav", labels: false },
    "F-05": { kind: "table", path: "/api/fund/dividend", params: { code: "017811" }, pick: "items",
              cols: ["date", "dividend", "type"] },
    // 组合 / 自选
    "P-04": { kind: "table", path: "/api/portfolio/analysis", params: {}, pick: "distribution",
              cols: ["label", "count", "value_cny", "ratio"] },
    "W-01": { kind: "profile", path: "/api/watchlist", params: {}, pick: "counts",
              fields: [["total", "自选总数", "int"], ["up", "上涨", "int"], ["down", "下跌", "int"], ["flat", "平盘", "int"]] },
    "W-02": { kind: "table", path: "/api/watchlist", params: {}, pick: "items",
              cols: ["symbol", "name", "market"] },
    // 资金
    "L-01": { kind: "chart", path: "/api/moneyflow/northbound", params: {}, pick: "items",
              x: "date", y: "net", labels: false },
    "L-02": { kind: "table", path: "/api/moneyflow/north-top", params: {}, pick: "items",
              cols: ["name", "net_buy", "date"] },
    "L-03": { kind: "table", path: "/api/moneyflow/lhb", params: {}, pick: "items",
              cols: ["date", "code", "name", "close", "pct", "net_buy"] },
    "L-04": { kind: "table", path: "/api/moneyflow/lhb-institution", params: {}, pick: "items",
              cols: ["date", "code", "name", "buy_orgs", "sell_orgs", "net_buy"] },
    "L-05": { kind: "table", path: "/api/moneyflow/lhb-broker", params: {}, pick: "items",
              cols: ["date", "name", "buy_amount", "sell_amount", "net_buy"] },
    // 养基宝实时估值
    "Y-04": { kind: "profile", path: "/api/fund/yjb/realtime", params: { code: "017811" },
              fields: [["code", "基金代码", "text"], ["nav", "估算净值", "num4"],
                       ["nav_date", "净值日期", "text"], ["day_growth", "估算涨跌", "pct"],
                       ["source", "数据源", "text"]] }
  };

  /** 不可作为自动刷新组件的条目（R6-04：如实登记，不造假渲染）。
   *
   * 两类：
   *  1) `control: true` —— 页面控件（筛选器/表单/告知书），放进仪表板无意义；
   *  2) `control: false` —— 接口真实不可用（需登录/需手动 POST），标注原因并禁用「添加」。
   */
  var UNAVAILABLE = {
    "S-01": { control: true, reason: "市场切换是页面控件，请用组件的「改参数 → market」切换" },
    "S-02": { control: true, reason: "行业/概念切换是页面控件，请用组件的「选行业」控件" },
    "N-01": { control: true, reason: "三级板块树是页面筛选器，不单独成卡" },
    "P-02": { control: true, reason: "新增持仓为表单控件，请在持仓页操作" },
    "Y-01": { control: true, reason: "合规告知书为一次性确认控件，请在养基宝页阅读" },
    "Y-02": { control: true, reason: "绑定状态为连接器状态控件，请在养基宝页查看" },
    "Y-03": { control: true, reason: "扫码绑定需交互，无法作为自动刷新组件" },
    "N-04": { control: false, reason: "AI 复核需手动触发（POST 接口），不作为自动刷新组件" },
    "F-06": { control: false, reason: "基金持仓分布需登录蛋卷账号，依规范已禁用该通道；可改用「F-04 重仓股」" }
  };

  /** 表头本地化映射（字段名 → i18n key）。 */
  var HEADER_KEYS = {
    name: "th.name", code: "th.code", symbol: "th.code", pct: "th.pct", amount: "th.amount",
    lead_stock: "th.leadStock", lead_pct: "th.leadPct", net_inflow: "th.netInflow",
    inflow: "th.inflow", outflow: "th.outflow", super_big: "th.superBig", big: "th.big",
    period: "th.period", growth: "th.growth", rank: "th.rank", ratio: "th.weight",
    value: "th.value", price: "th.price", market_value: "th.value", pnl: "th.pnl",
    pnl_pct: "th.pnlPct", market_label: "th.market", time: "th.time", title: t("th.title")
  };

  /** 取单元格显示值（数值走本地化）。
   * @param {*} value 待格式化或处理的值
   * @param {string} field 字段名
   * @returns {*} 处理结果
   */
  function cellText(value, field) {
    if (value === null || value === undefined) {
      return t("value.none");
    }
    if (typeof value === "number") {
      if (field && (field.indexOf("pct") >= 0 || field === "growth" || field === "ratio")) {
        return L().pct(value);
      }
      return L().num(value, 2);
    }
    return String(value);
  }

  /** 带涨跌色的单元格。
   * @param {*} value 待格式化或处理的值
   * @param {string} field 字段名
   * @returns {*} 处理结果
   */
  function cellNode(value, field) {
    var node = el("td", "num", cellText(value, field));
    if (typeof value === "number" && (field.indexOf("pct") >= 0 || field === "pnl" || field === "growth" || field === "net_inflow")) {
      node.className = "num " + (value > 0 ? "up" : value < 0 ? "down" : "flat");
    }
    return node;
  }

  /** 单请求超时（毫秒）。超过即中断并按错误态收口，避免整块卡在"加载中"。 */
  var FETCH_TIMEOUT_MS = 12000;

  /** 同时在飞的请求上限（HTTP/1.1 同域连接数上限，超出只会排队等待）。 */
  var MAX_CONCURRENT = 6;
  var activeRequests = 0;
  var requestQueue = [];

  /** 驱动请求队列：空出名额就取下一个任务执行。
   * @returns {void} 无返回值
   */
  function pumpQueue() {
    while (activeRequests < MAX_CONCURRENT && requestQueue.length) {
      var job = requestQueue.shift();
      activeRequests += 1;
      job.run().then(job.resolve, job.reject).then(function () {
        activeRequests -= 1;
        pumpQueue();
      });
    }
  }

  /** 把任务放进受限并发队列。
   * @param {Function} run 返回 Promise 的任务函数
   * @returns {Promise} 任务结果
   */
  function enqueueRequest(run) {
    return new Promise(function (resolve, reject) {
      requestQueue.push({ run: run, resolve: resolve, reject: reject });
      pumpQueue();
    });
  }

  /** 统一请求（中文错误；12s 超时 + 并发上限 6）。
   * @param {string} path 接口路径
   * @param {Object} params 查询参数对象
   * @returns {Promise} data 段
   */
  function fetchJson(path, params) {
    var query = new URLSearchParams();
    Object.keys(params || {}).forEach(function (key) {
      query.append(key, params[key]);
    });
    var url = path + (query.toString() ? "?" + query.toString() : "");
    return enqueueRequest(function () {
      var controller = typeof AbortController === "function" ? new AbortController() : null;
      var timer = controller
        ? window.setTimeout(function () { controller.abort(); }, FETCH_TIMEOUT_MS)
        : null;
      return fetch(url, controller ? { cache: "no-store", signal: controller.signal }
        : { cache: "no-store" })
        .catch(function (error) {
          throw new Error(error && error.name === "AbortError"
            ? t("dashboard.timeout")
            : t("error.network"));
        })
        .then(function (response) { return response.json(); })
        .then(function (body) {
          if (body && body.error) {
            var error = new Error(body.error);
            error.code = body.error_code;
            throw error;
          }
          return body.data || {};
        })
        .then(function (data) {
          if (timer) {
            window.clearTimeout(timer);
          }
          return data;
        }, function (error) {
          if (timer) {
            window.clearTimeout(timer);
          }
          throw error;
        });
    });
  }

  /** 渲染表格（可排序 + 可导出 CSV）。
   * @param {HTMLElement} container DOM 容器节点
   * @param {Array} rows 记录行列表
   * @param {*} cols 列定义（字段名数组）
   * @param {Object} widget 组件元数据（id/title/span）
   * @returns {*} 处理结果
   */
  function renderTable(container, rows, cols, widget) {
    if (!rows.length) {
      container.appendChild(el("div", "pg-state", t("state.empty")));
      return;
    }
    var wrap = el("div", "pg-table-wrap");
    var table = el("table", "pg-table");
    var thead = el("thead");
    var headRow = el("tr");
    var sortState = { field: null, asc: true };
    cols.forEach(function (field) {
      var th = el("th", field === "name" ? "" : "num", HEADER_KEYS[field] ? t(HEADER_KEYS[field]) : field);
      th.style.cursor = "pointer";
      th.addEventListener("click", function () {
        sortState.asc = sortState.field === field ? !sortState.asc : true;
        sortState.field = field;
        var sorted = rows.slice().sort(function (a, b) {
          var va = a[field];
          var vb = b[field];
          if (typeof va === "number" && typeof vb === "number") {
            return sortState.asc ? va - vb : vb - va;
          }
          return sortState.asc
            ? String(va === null || va === undefined ? "" : va).localeCompare(String(vb === null || vb === undefined ? "" : vb), "zh-CN")
            : String(vb === null || vb === undefined ? "" : vb).localeCompare(String(va === null || va === undefined ? "" : va), "zh-CN");
        });
        tbody.innerHTML = "";
        sorted.forEach(function (row) {
          tbody.appendChild(buildRow(row, cols));
        });
      });
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);
    var tbody = el("tbody");

    /** 生成一行 tr：按列顺序取值并交给 cellNode 做类型化渲染。
     * @param {*} row 单行记录
     * @param {Array} columns 列定义（字段名数组）
     * @returns {*} 处理结果
     */
    function buildRow(row, columns) {
      var tr = el("tr");
      columns.forEach(function (field) {
        tr.appendChild(cellNode(row[field], field));
      });
      return tr;
    }

    rows.forEach(function (row) {
      tbody.appendChild(buildRow(row, cols));
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    container.appendChild(wrap);

    // CSV 导出（UTF-8 BOM，Excel 直接打开不乱码）
    var exportButton = el("button", "pg-btn", t("action.export"));
    exportButton.type = "button";
    exportButton.addEventListener("click", function () {
      if (!rows.length) {
        toast(t("error.exportEmpty"));
        return;
      }
      var headers = cols.map(function (field) { return HEADER_KEYS[field] ? t(HEADER_KEYS[field]) : field; });
      var body = rows.map(function (row) {
        return cols.map(function (field) { return row[field]; });
      });
      var blob = new Blob([L().csvText(headers, body)], { type: "text/csv;charset=utf-8" });
      var link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = L().csvFilename(widget.title || widget.id);
      document.body.appendChild(link);
      link.click();
      link.remove();
      toast(t("toast.copied"));
    });
    container.appendChild(exportButton);
  }

  /** 指标键 → i18n 键（R2-07）。
   * renderMetrics 不得把接口对象键名直接当标签，否则 scope/total/mean_score
   * 等英文键会裸渲染到界面；未登记且非中文的键一律不展示。
   */
  var METRIC_LABELS = {
    total: { key: "news.senti.sample", kind: "int" },
    positive: { key: "stock.sentiment.up", kind: "int" },
    negative: { key: "stock.sentiment.down", kind: "int" },
    neutral: { key: "stock.sentiment.neutral", kind: "int" },
    mean_score: { key: "news.senti.meanScore", kind: "num" },
    temperature: { key: "news.senti.temperature", kind: "num" },
    level: { key: "news.senti.level", kind: "text" },
    count: { key: "portfolio.kv.count", kind: "int" },
    cny_market_value: { key: "portfolio.kv.valueCny", kind: "num" },
    cny_cost: { key: "portfolio.kv.costCny", kind: "num" },
    cny_pnl: { key: "portfolio.kv.pnlCny", kind: "num" },
    fx: { key: "portfolio.kv.fx", kind: "text" }
  };

  /** 渲染指标卡。
   * @param {HTMLElement} container DOM 容器节点
   * @param {Object} data 数据主体（接口 data 段）
   * @param {Object} widget 组件元数据（id/title/span）
   */
  function renderMetrics(container, data, widget) {
    var entries = [];
    if (Array.isArray(data)) {
      data.slice(0, 6).forEach(function (item, index) {
        entries.push([item.name || t("dashboard.metricDefault") + (index + 1), item.pct !== undefined ? item.pct : item.net_inflow, item.pct !== undefined ? "pct" : "num"]);
      });
    } else if (data && typeof data === "object") {
      // 对象型指标（N-03 情绪温度、P-01 持仓汇总）：键名走 i18n 映射；
      // 未登记的英文键直接跳过，中文键（如板块名）原样展示
      Object.keys(data).slice(0, 8).forEach(function (key) {
        var value = data[key];
        if (!(typeof value === "number" || typeof value === "string")) {
          return;
        }
        var rule = METRIC_LABELS[key];
        var label = rule ? t(rule.key) : (/[\u4e00-\u9fff]/.test(key) ? key : "");
        if (!label) {
          return;
        }
        entries.push([label, value, rule ? rule.kind : (typeof value === "number" ? "num" : "text")]);
      });
    }
    if (!entries.length) {
      container.appendChild(el("div", "pg-state", t("state.empty")));
      return;
    }
    var grid = el("div", "pg-metric-grid");
    entries.forEach(function (entry) {
      var cell = el("div", "pg-metric-cell");
      cell.appendChild(el("div", "pg-metric-label", entry[0]));
      var text = entry[2] === "pct" ? L().pct(entry[1])
        : (entry[2] === "int" ? L().num(entry[1], 0)
          : (entry[2] === "num" ? L().num(entry[1], 2) : entry[1]));
      var cls = entry[2] === "pct" && typeof entry[1] === "number" ? (entry[1] > 0 ? "up" : entry[1] < 0 ? "down" : "flat") : "";
      cell.appendChild(el("div", "pg-metric-value " + cls, text));
      grid.appendChild(cell);
    });
    container.appendChild(grid);
  }

  /** 渲染折线/柱状图（ECharts 本地库）。
   * @param {HTMLElement} container DOM 容器节点
   * @param {Array} items 记录列表
   * @param {Object} recipe 渲染配方（kind/path/cols）
   * @returns {Object} 处理结果
   */
  function renderChart(container, items, recipe) {
    if (!items || !items.length) {
      container.appendChild(el("div", "pg-state", t("state.empty")));
      return;
    }
    // R6-03：同一容器禁止重复 init（旧实例先释放），否则新 canvas 不会挂载
    disposeChartsIn(container);
    container.innerHTML = "";
    var box = el("div", "pg-chart");
    container.appendChild(box);
    var chart = window.echarts ? window.echarts.init(box) : null;
    if (!chart) {
      container.appendChild(el("div", "pg-state", t("dashboard.chartNotFound")));
      return;
    }
    var styles = getComputedStyle(document.documentElement);
    var textColor = styles.getPropertyValue("--text-dim").trim() || "#a2a2a2";
    var borderColor = styles.getPropertyValue("--border").trim() || "#2a2a2a";
    var up = styles.getPropertyValue("--up").trim() || "#ff4b4b";
    var down = styles.getPropertyValue("--down").trim() || "#00b061";
    var labels = items.map(function (item) { return item[recipe.x]; });
    var values = items.map(function (item) { return item[recipe.y]; });
    // R6-07：名称常显（用户要求"展示出来的数据都要写明什么行业/个股/基金"）。
    // 柱状图默认带名称+数值标签；折线图点数多，保持悬停 + 图例，不铺标签。
    var showLabels = recipe.labels === true || (recipe.bar === true && recipe.labels !== false);
    var rotate = labels.length > 8 && recipe.rotate !== false ? 38 : 0;
    // R6-08 降级链第一级：标签多时先缩字号（10 → 9 → 8），再交给截断与 hideOverlap
    var labelFont = labels.length > 24 ? 8 : (labels.length > 14 ? 9 : 10);
    chart.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis" },
      grid: { left: 56, right: 16, top: showLabels ? 28 : 20, bottom: rotate ? 64 : 28 },
      xAxis: {
        type: "category", data: labels,
        axisLabel: {
          color: textColor,
          interval: 0,
          rotate: rotate,
          fontSize: labelFont,
          hideOverlap: !rotate,
          formatter: function (value) {
            var text = String(value == null ? "" : value);
            return text.length > 6 ? text.slice(0, 5) + "…" : text;
          }
        },
        axisLine: { lineStyle: { color: borderColor } }
      },
      yAxis: {
        type: "value", scale: true,
        axisLabel: { color: textColor },
        splitLine: { lineStyle: { color: borderColor, opacity: 0.4 } }
      },
      series: [{
        type: recipe.bar ? "bar" : "line",
        data: recipe.bar ? values.map(function (value) {
          return { value: value, itemStyle: { color: value > 0 ? up : down } };
        }) : values,
        showSymbol: false,
        lineStyle: { width: 1.6 },
        label: showLabels ? {
          show: true,
          position: "top",
          // 降级链第二级：柱子多时标签同步缩一号，避免"全画但全挤在一起"
          fontSize: Math.max(8, labelFont - 1),
          color: textColor,
          formatter: recipe.labelUnit === "pct"
            ? function (param) { return (param.value > 0 ? "+" : "") + param.value + "%"; }
            : undefined
        } : { show: false }
      }]
    });
  }

  /** 渲染资讯流（真实新闻 + 本地情绪标签）。
   * @param {HTMLElement} container DOM 容器节点
   * @param {Array} items 记录列表
   */
  function renderNews(container, items) {
    if (!items || !items.length) {
      container.appendChild(el("div", "pg-state", t("state.empty")));
      return;
    }
    var list = el("div", "pg-feed-list");
    items.slice(0, 10).forEach(function (item) {
      var sentiment = item.sentiment || {};
      var row = el("article", "pg-feed-item");
      var head = el("div", "pg-feed-head");
      var label = sentiment.label || "";
      var chipClass = label === t("dashboard.sentiment.up") ? "chip up"
        : label === t("dashboard.sentiment.down") ? "chip down" : "chip";
      head.appendChild(el("span", chipClass, label || t("dashboard.sentiment.neutral")));
      head.appendChild(el("span", "pg-feed-score", L().num(sentiment.score, 4)));
      head.appendChild(el("span", "pg-spacer"));
      head.appendChild(el("span", "pg-news-time", L().datetime(item.time)));
      row.appendChild(head);
      row.appendChild(el("div", "pg-feed-title", item.title || ""));
      list.appendChild(row);
    });
    container.appendChild(list);
  }

  /** 分组渲染：表格 / 图表 / 指标 / 资讯 / 面板。
   * @param {HTMLElement} body 区块容器（.pg-body，四态 data-state 挂在此节点）
   * @param {Object} data 数据主体（接口 data 段）
   * @param {Object} recipe 渲染配方（kind/path/cols）
   * @param {Object} widget 组件元数据（id/title/span）
   */
  function renderWidgetBody(body, data, recipe, widget) {
    body.innerHTML = "";
    // R6-04：统一取列表 —— recipe.pick 优先，否则 items / nodes / list
    var picked = recipe.pick ? data[recipe.pick] : null;
    var rows = (recipe.pick && Array.isArray(picked) ? picked
      : (data.items || data.nodes || data.list || [])).filter(function (item) { return item; });

    if (recipe.kind === "table") {
      if (!rows.length) {
        appendExplanation(body, data);
        return;
      }
      // 行数上限：长表（板块 K 线 261 行等）只展示最近 N 行并注明，避免一次性铺满卡片
      var maxRows = recipe.maxRows || 200;
      var shown = rows;
      if (rows.length > maxRows) {
        shown = rows.slice(-maxRows);
      }
      renderTable(body, shown, recipe.cols, widget);
      if (shown.length !== rows.length) {
        body.appendChild(el("div", "pg-card-sub",
          t("dashboard.tableTrimmed", { shown: shown.length, total: rows.length })));
      }
      return;
    }
    if (recipe.kind === "chart") {
      if (!rows.length) {
        appendExplanation(body, data);
        return;
      }
      renderChart(body, rows, recipe);
      return;
    }
    if (recipe.kind === "news") {
      if (!rows.length) {
        appendExplanation(body, data);
        return;
      }
      renderNews(body, rows);
      return;
    }
    if (recipe.kind === "metrics") {
      var payload = recipe.pick === "self" ? data : data[recipe.pick];
      renderMetrics(body, payload, widget);
      return;
    }
    if (recipe.kind === "profile") {
      renderProfile(body, data, recipe);
      return;
    }
    if (recipe.kind === "finTable") {
      renderFinTable(body, data);
      return;
    }
    if (recipe.kind === "yjbCollect") {
      // 养基宝账户概览：KPI 数字 + 账户小卡（与 Y-05 区块同构）
      var accounts = data.accounts || [];
      if (!data.total_assets && !accounts.length) {
        body.appendChild(el("div", "pg-state", t("yjb.notBoundHint")));
        body.setAttribute("data-state", "empty");
        return;
      }
      var kpis = el("div", "pg-kpis");
      [[t("yjb.kpi.totalAssets"), fmt(data.total_assets, 2), "flat"],
       [t("yjb.kpi.todayIncome"), (data.today_income > 0 ? "+" : "") + fmt(data.today_income, 2), pctClass(data.today_income)],
       [t("yjb.kpi.todayRate"), fmtPct(data.today_income_rate), pctClass(data.today_income_rate)],
       [t("yjb.kpi.holdIncome"), (data.hold_income > 0 ? "+" : "") + fmt(data.hold_income, 2), pctClass(data.hold_income)]]
        .forEach(function (row) {
          var box = el("div", "pg-kpi");
          box.appendChild(el("div", "pg-kpi-k", row[0]));
          box.appendChild(el("div", "pg-kpi-v " + row[2], row[1]));
          kpis.appendChild(box);
        });
      body.appendChild(kpis);
      if (accounts.length) {
        var list = el("div", "pg-kpi-accounts");
        accounts.forEach(function (acc) {
          var box = el("div", "pg-kpi-acc");
          var head = el("div", "pg-kpi-acc-head");
          head.appendChild(el("span", "pg-kpi-acc-name", acc.title || acc.id));
          head.appendChild(el("span", "pg-kpi-acc-id", acc.up + "↑/" + acc.down + "↓"));
          box.appendChild(head);
          // R6-11：账户资产 = hold_cost + hold_income（后端若已下发 assets 则直接用）
          var assets = acc.assets;
          if (assets === undefined || assets === null) {
            assets = (Number(acc.hold_cost) || 0) + (Number(acc.hold_income) || 0);
          }
          var lineAssets = el("div", "pg-kpi-acc-row");
          lineAssets.appendChild(el("span", "", t("yjb.acc.assets")));
          lineAssets.appendChild(el("b", "", fmt(assets, 2)));
          box.appendChild(lineAssets);
          var line = el("div", "pg-kpi-acc-row");
          line.appendChild(el("span", "", t("yjb.kpi.todayIncome")));
          line.appendChild(el("b", pctClass(acc.today_income),
            (acc.today_income > 0 ? "+" : "") + fmt(acc.today_income, 2)));
          box.appendChild(line);
          var line2 = el("div", "pg-kpi-acc-row");
          line2.appendChild(el("span", "", t("yjb.kpi.todayRate")));
          line2.appendChild(el("b", pctClass(acc.today_income_rate), fmtPct(acc.today_income_rate)));
          box.appendChild(line2);
          list.appendChild(box);
        });
        body.appendChild(list);
      }
      body.setAttribute("data-state", "default");
      return;
    }
    if (recipe.kind === "panel") {
      var quote = data[recipe.pick] || {};
      var grid = el("div", "pg-metric-grid");
      [[t("dashboard.panel.name"), quote.name], [t("dashboard.panel.price"), L().price(quote.price, "a")], [t("dashboard.panel.pct"), L().pct(quote.pct)],
       [t("dashboard.panel.open"), L().price(quote.open, "a")], [t("dashboard.panel.high"), L().price(quote.high, "a")], [t("dashboard.panel.low"), L().price(quote.low, "a")]]
        .forEach(function (pair) {
          var cell = el("div", "pg-metric-cell");
          cell.appendChild(el("div", "pg-metric-label", pair[0]));
          cell.appendChild(el("div", "pg-metric-value", pair[1]));
          grid.appendChild(cell);
        });
      body.appendChild(grid);
      body.setAttribute("data-state", "default");
    }
  }

  /** 空数据时的说明块（R6-15：空错分离 —— 空结果必须带来源/口径说明，禁止静默空白）。
   * @param {HTMLElement} body 内容容器
   * @param {Object} data 接口 data 段
   * @returns {void} 无返回值
   */
  function appendExplanation(body, data) {
    var box = el("div", "pg-state");
    box.appendChild(el("div", "pg-state-title", t("state.empty")));
    var hint = data.note || data.empty_note || data.caliber || "";
    if (hint) {
      box.appendChild(el("div", "pg-state-hint", hint));
    }
    body.appendChild(box);
    body.setAttribute("data-state", "empty");
  }

  /** 渲染键值档案（按 recipe.fields 声明的字段顺序，缺值显示「无数据」占位）。
   * @param {HTMLElement} container DOM 容器
   * @param {Object} data 接口 data 段
   * @param {Object} recipe 渲染配方（含 pick / fields）
   * @returns {void} 无返回值
   */
  function renderProfile(container, data, recipe) {
    var source = recipe.pick && recipe.pick !== "self" ? (data[recipe.pick] || {}) : data;
    var fields = recipe.fields || [];
    var grid = el("div", "pg-metric-grid");
    var shown = 0;
    fields.forEach(function (field) {
      var key = field[0];
      var label = field[1];
      var kind = field[2] || "text";
      var value = source[key];
      if (value === undefined || value === null || value === "") {
        value = t("value.none");
        kind = "text";
      } else if (kind === "num0") {
        value = L().num(value, 0);
      } else if (kind === "num4") {
        value = L().num(value, 4);
      } else if (kind === "num") {
        value = L().num(value, 2);
      } else if (kind === "int") {
        value = L().num(value, 0);
      } else if (kind === "pct") {
        value = L().pct(value);
      } else {
        value = String(value);
      }
      var cell = el("div", "pg-metric-cell");
      cell.appendChild(el("div", "pg-metric-label", label));
      var cls = kind === "pct" && typeof source[key] === "number"
        ? (source[key] > 0 ? "up" : source[key] < 0 ? "down" : "flat") : "";
      cell.appendChild(el("div", "pg-metric-value " + cls, value));
      grid.appendChild(cell);
      shown += 1;
    });
    if (!shown) {
      appendExplanation(container, data);
      return;
    }
    container.appendChild(grid);
  }

  /** 渲染财务摘要矩阵（行=指标，列=报告期）。
   * @param {HTMLElement} container DOM 容器
   * @param {Object} data `/api/stock/financial-summary` 的 data 段
   * @returns {void} 无返回值
   */
  function renderFinTable(container, data) {
    var periods = data.periods || [];
    var metrics = data.metrics || [];
    if (!periods.length || !metrics.length) {
      appendExplanation(container, data);
      return;
    }
    /** 报告期 20260630 → 2026-06-30（保持可读且不引入歧义）。
     * @param {string} value 期次字符串
     * @returns {string} 格式化后的日期
     */
    function fmtPeriod(value) {
      var text = String(value || "");
      if (text.length !== 8) {
        return text;
      }
      return text.slice(0, 4) + "-" + text.slice(4, 6) + "-" + text.slice(6, 8);
    }
    var wrap = el("div", "pg-table-wrap");
    var table = el("table", "pg-table");
    var thead = el("thead");
    var headRow = el("tr");
    headRow.appendChild(el("th", "", t("dashboard.fin.metric")));
    periods.forEach(function (period) {
      headRow.appendChild(el("th", "num", fmtPeriod(period)));
    });
    thead.appendChild(headRow);
    table.appendChild(thead);
    var tbody = el("tbody");
    metrics.forEach(function (metric) {
      var tr = el("tr");
      tr.appendChild(el("td", "", metric.name || metric.key || ""));
      (metric.values || []).forEach(function (value) {
        tr.appendChild(el("td", "num", value === null || value === undefined ? t("value.none") : L().num(value, 2)));
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    container.appendChild(wrap);
  }

  // =============================================================
  // 组件卡片
  // =============================================================
  var draggedIndex = null;

  /** 卡片「改参数」弹窗（R3-11）：编辑标的/周期等配方参数并持久化。
   * @param {Object} entry 组件条目（含 params）
   * @param {Array} keys 需要编辑的参数键
   * @returns {void} 无返回值
   */
  function editWidgetParams(entry, keys) {
    var meta = (state.registry && state.registry[entry.id]) || { id: entry.id, title: entry.id };
    var current = entry.params || {};
    var recipeDef = RECIPES[entry.id] || {};
    var merged = Object.assign({}, recipeDef.params || {}, current);
    var labels = {
      symbol: t("dashboard.param.symbol"),
      market: t("dashboard.param.market"),
      period: t("dashboard.param.period"),
      code: t("dashboard.param.code"),
      board: t("dashboard.param.board"),
      market_type: t("dashboard.param.marketType"),
      kind: t("dashboard.param.kind"),
      sort: t("dashboard.param.sort"),
      limit: t("dashboard.param.limit")
    };
    window.UI.modal({
      title: t("dashboard.editParams") + " · " + (meta.title || entry.id),
      message: t("dashboard.editParamsHint"),
      fields: keys.map(function (key) {
        return { key: key, label: labels[key] || key, value: merged[key], mono: true };
      }),
      /** 确认改参数：空值表示删除该参数（回落到配方默认）。
       * @param {Object} values 字段键值
       */
      onConfirm: function (values) {
        var next = Object.assign({}, current);
        Object.keys(values).forEach(function (key) {
          if (values[key] === "") {
            delete next[key];
          } else {
            next[key] = values[key];
          }
        });
        entry.params = Object.keys(next).length ? next : undefined;
        saveState(true);
        render();
      }
    });
  }

  // =============================================================
  // R6-07 / F6-07：板块类组件的内容选择器（默认展示 20，可选 10/20/30/50/全部）
  // =============================================================

  /** 需要「选行业 / 选概念」控件的组件及其默认展示数量。
   * 与设计基准 F6-07 一致：热点图、行业排行、概念排行、板块资金流共用同一控件与同一份参数。
   */
  var SECTOR_PICKERS = {
    "S-03": { labelKey: "dashboard.pick.sector", listKey: "nodes", defaultLimit: 20, mode: "industry" },
    "S-04": { labelKey: "dashboard.pick.sector", listKey: "items", defaultLimit: 20, mode: "industry" },
    "S-05": { labelKey: "dashboard.pick.concept", listKey: "items", defaultLimit: 20, mode: "concept" },
    "S-07": { labelKey: "dashboard.pick.sector", listKey: "items", defaultLimit: 20, mode: "industry" }
  };

  /** 展示数量档位（0 表示全部）。 */
  var SECTOR_COUNTS = [10, 20, 30, 50, 0];

  /** 取该组件在当前参数下应展示的板块名集合。
   * @param {Object} params 组件参数（含 boards / limit）
   * @param {Object} picker 选择器配置
   * @returns {{boards: string[], limit: number}} 已选板块与展示数量
   */
  function pickerSelection(params, picker) {
    var raw = String((params && params.boards) || "").trim();
    var boards = raw ? raw.split(",").map(function (item) { return item.trim(); }).filter(Boolean) : [];
    var limit = params && params.limit !== undefined && params.limit !== ""
      ? Number(params.limit) : picker.defaultLimit;
    if (!isFinite(limit) || limit < 0) {
      limit = picker.defaultLimit;
    }
    return { boards: boards, limit: limit };
  }

  /** 按参数裁剪列表：先按已选板块过滤，再按展示数量截断（保持接口原有排序）。
   * @param {Object} data 接口 data 段
   * @param {Object} picker 选择器配置
   * @param {Object} params 组件参数
   * @returns {Object} 新的 data 段（不改原对象）
   */
  function applySectorFilter(data, picker, params) {
    var listKey = picker.listKey;
    var list = data[listKey];
    if (!Array.isArray(list) || !list.length) {
      return data;
    }
    var selection = pickerSelection(params, picker);
    var rows = list;
    if (selection.boards.length) {
      rows = rows.filter(function (item) {
        return selection.boards.indexOf(String(item.name || "")) >= 0;
      });
    }
    if (selection.limit > 0) {
      rows = rows.slice(0, selection.limit);
    }
    var copy = Object.assign({}, data);
    copy[listKey] = rows;
    if (listKey === "nodes") {
      copy.count = rows.length;
    }
    return copy;
  }

  /** 板块选择上限（超出必然标签重叠，与设计基准 F6-07 一致）。 */
  var SECTOR_MAX_PICK = 30;

  /** 打开板块选择浮层：搜索 + 排序 + 复选 + 展示数量 + 应用。
   *
   * 规格（设计基准 F6-07）：锚定在「选行业」按钮下方弹出（420px 宽，最大高 60vh）；
   * 默认展示 20；可在 10/20/30/50/全部 之间切换；最多勾选 30 个，超出禁用「应用」；
   * 勾选为空表示"全部板块参与排序后取前 N"。
   *
   * @param {Object} entry 组件条目（含 id / params）
   * @param {Object} meta 组件元数据（标题）
   * @param {HTMLElement} anchor 触发按钮（用于定位；缺省居中）
   * @returns {void} 无返回值（异步拉取列表后挂载浮层）
   */
  function openSectorPicker(entry, meta, anchor) {
    var picker = SECTOR_PICKERS[entry.id];
    if (!picker) {
      return;
    }
    var recipe = RECIPES[entry.id] || {};
    var overlay = el("div", "dash-picker-mask");
    overlay.classList.add("is-anchored");
    var panel = el("div", "dash-picker");
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", t(picker.labelKey));

    var head = el("div", "dash-picker-head");
    head.appendChild(el("span", "dash-picker-title", t(picker.labelKey) + " · " + (meta.title || entry.id)));
    var closeBtn = el("button", "pg-btn dash-btn", t("action.close"));
    closeBtn.type = "button";
    head.appendChild(closeBtn);
    panel.appendChild(head);

    var searchRow = el("div", "dash-picker-row");
    var search = document.createElement("input");
    search.type = "search";
    search.className = "dash-picker-search";
    search.placeholder = t("dashboard.pick.search");
    search.setAttribute("aria-label", t("dashboard.pick.search"));
    searchRow.appendChild(search);
    panel.appendChild(searchRow);

    var countRow = el("div", "dash-picker-row dash-picker-counts");
    countRow.appendChild(el("span", "dash-picker-label", t("dashboard.pick.showCount")));
    var selection = pickerSelection(entry.params, picker);
    var currentLimit = selection.limit;
    var countButtons = [];
    SECTOR_COUNTS.forEach(function (value) {
      var label = value === 0 ? t("dashboard.pick.countAll") : String(value);
      var chip = el("button", "pg-tab" + (value === currentLimit ? " active" : ""), label);
      chip.type = "button";
      chip.addEventListener("click", function () {
        currentLimit = value;
        countButtons.forEach(function (node) { node.classList.remove("active"); });
        chip.classList.add("active");
      });
      countButtons.push(chip);
      countRow.appendChild(chip);
    });
    panel.appendChild(countRow);

    // R6-07：排序切换（涨跌幅 / 成交额 / 名称），与展示数量同排
    var sortRow = el("div", "dash-picker-row dash-picker-sort");
    sortRow.appendChild(el("span", "dash-picker-label", t("dashboard.pick.sortBy")));
    var sortButtons = [];
    var sortKey = "pct";
    [
      ["pct", t("dashboard.pick.sortPct")],
      ["amount", t("dashboard.pick.sortAmount")],
      ["name", t("dashboard.pick.sortName")]
    ].forEach(function (option) {
      var chip = el("button", "pg-tab" + (option[0] === sortKey ? " active" : ""), option[1]);
      chip.type = "button";
      chip.addEventListener("click", function () {
        sortKey = option[0];
        sortButtons.forEach(function (node) { node.classList.remove("active"); });
        chip.classList.add("active");
        renderList();
      });
      sortButtons.push(chip);
      sortRow.appendChild(chip);
    });
    panel.appendChild(sortRow);

    var listHost = el("div", "dash-picker-list");
    listHost.appendChild(el("div", "pg-state", t("state.loading")));
    panel.appendChild(listHost);

    var foot = el("div", "dash-picker-foot");
    var counter = el("span", "dash-picker-counter", "");
    foot.appendChild(counter);
    var selectAll = el("button", "pg-btn", t("dashboard.pick.all"));
    selectAll.type = "button";
    var clearAll = el("button", "pg-btn", t("dashboard.pick.clear"));
    clearAll.type = "button";
    var apply = el("button", "pg-btn primary", t("dashboard.pick.apply"));
    apply.type = "button";
    foot.appendChild(selectAll);
    foot.appendChild(clearAll);
    foot.appendChild(apply);
    panel.appendChild(foot);

    overlay.appendChild(panel);
    document.body.appendChild(overlay);

    // R6-07：锚定在触发按钮下方弹出；空间不足时自动翻转，始终留在视口内
    if (anchor && anchor.getBoundingClientRect) {
      var anchorRect = anchor.getBoundingClientRect();
      var panelWidth = panel.offsetWidth || 420;
      var panelHeight = panel.offsetHeight || 420;
      var left = Math.max(8, Math.min(anchorRect.left, window.innerWidth - panelWidth - 12));
      var top = anchorRect.bottom + 6;
      if (top + panelHeight > window.innerHeight - 8) {
        top = Math.max(8, anchorRect.top - panelHeight - 6);
      }
      panel.style.position = "fixed";
      panel.style.left = left + "px";
      panel.style.top = top + "px";
      panel.style.maxHeight = Math.min(420, Math.round(window.innerHeight * 0.6)) + "px";
    }

    var chosen = selection.boards.slice();
    var allItems = [];

    /** 关闭浮层并移除节点。
     * @returns {void} 无返回值
     */
    function close() {
      if (overlay.parentNode) {
        overlay.parentNode.removeChild(overlay);
      }
      document.removeEventListener("keydown", onEsc);
    }

    /** Esc 关闭。
     * @param {KeyboardEvent} event 键盘事件
     * @returns {void} 无返回值
     */
    function onEsc(event) {
      if (event.key === "Escape") {
        close();
      }
    }
    document.addEventListener("keydown", onEsc);
    closeBtn.addEventListener("click", close);
    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) {
        close();
      }
    });

    /** 重绘计数文案、超限提示与「应用」可用态。
     * @returns {void} 无返回值
     */
    function refreshCounter() {
      counter.textContent = t("dashboard.pick.selected", { n: chosen.length, total: allItems.length })
        + (currentLimit > 0 ? " · " + t("dashboard.pick.showCount") + " " + currentLimit : "");
      var over = chosen.length > SECTOR_MAX_PICK;
      counter.classList.toggle("over-limit", over);
      apply.disabled = over;
      if (over) {
        counter.textContent = t("dashboard.pick.overLimit", { n: SECTOR_MAX_PICK })
          + " · " + t("dashboard.pick.selected", { n: chosen.length, total: allItems.length });
      }
    }

    /** 按搜索词、排序键与选中态重绘列表。
     * @returns {void} 无返回值
     */
    function renderList() {
      var keyword = (search.value || "").trim().toLowerCase();
      var sorted = allItems.slice().sort(function (a, b) {
        if (sortKey === "name") {
          return String(a.name).localeCompare(String(b.name), "zh-CN");
        }
        var left = Number(a[sortKey]);
        var right = Number(b[sortKey]);
        if (!isFinite(left)) {
          left = -Infinity;
        }
        if (!isFinite(right)) {
          right = -Infinity;
        }
        return right - left;
      });
      listHost.innerHTML = "";
      var shown = 0;
      sorted.forEach(function (item) {
        var name = String(item.name || "");
        if (!name) {
          return;
        }
        if (keyword && name.toLowerCase().indexOf(keyword) < 0) {
          return;
        }
        shown += 1;
        var row = el("label", "dash-picker-item");
        var box = document.createElement("input");
        box.type = "checkbox";
        box.checked = chosen.indexOf(name) >= 0;
        box.addEventListener("change", function () {
          if (box.checked) {
            if (chosen.indexOf(name) < 0) {
              chosen.push(name);
            }
          } else {
            chosen = chosen.filter(function (entryName) { return entryName !== name; });
          }
          refreshCounter();
        });
        row.appendChild(box);
        row.appendChild(el("span", "dash-picker-name", name));
        // 名称常显：列表里直接给出涨跌幅，避免"勾选前不知道是什么"
        if (typeof item.pct === "number") {
          var pctNode = el("span", "dash-picker-pct " + (item.pct > 0 ? "up" : item.pct < 0 ? "down" : "flat"),
            (item.pct > 0 ? "+" : "") + item.pct + "%");
          row.appendChild(pctNode);
        }
        listHost.appendChild(row);
      });
      if (!shown) {
        listHost.appendChild(el("div", "pg-state", t("state.empty")));
      }
      refreshCounter();
    }

    search.addEventListener("input", renderList);
    selectAll.addEventListener("click", function () {
      // 全选受上限约束：只取前 30 个（避免"全选后应用被禁用"的困惑）
      chosen = allItems.slice(0, SECTOR_MAX_PICK).map(function (item) { return String(item.name || ""); })
        .filter(Boolean);
      if (allItems.length > SECTOR_MAX_PICK) {
        toast(t("dashboard.pick.limit", { n: SECTOR_MAX_PICK }));
      }
      renderList();
    });
    clearAll.addEventListener("click", function () {
      chosen = [];
      renderList();
    });
    apply.addEventListener("click", function () {
      if (chosen.length > SECTOR_MAX_PICK) {
        toast(t("dashboard.pick.overLimit", { n: SECTOR_MAX_PICK }));
        return;
      }
      var params = Object.assign({}, entry.params || {});
      if (chosen.length) {
        params.boards = chosen.join(",");
      } else {
        delete params.boards;
      }
      if (currentLimit > 0) {
        params.limit = currentLimit;
      } else {
        params.limit = 0;
      }
      entry.params = params;
      saveState(true);
      close();
      render();
    });

    // 拉取板块列表（与卡片同一接口，保证"看到的可选"与"能展示的"一致）
    fetchJson(recipe.path, recipe.params || {}).then(function (data) {
      var list = data[picker.listKey] || [];
      allItems = list.filter(function (item) { return String(item.name || ""); });
      if (!allItems.length) {
        listHost.innerHTML = "";
        listHost.appendChild(el("div", "pg-state",
          (data.caliber || t("state.empty"))));
        return;
      }
      renderList();
    }).catch(function (error) {
      listHost.innerHTML = "";
      var box = el("div", "pg-state");
      box.appendChild(el("div", "pg-state-title", t("state.errorTitle")));
      box.appendChild(el("div", "pg-state-hint", error.message));
      listHost.appendChild(box);
    });
  }

  /** 构建一张组件卡（含拖拽、宽度调整、移除、CSV）。
   * @param {*} entry 列表项 / 组件条目
   * @param {number} index 序号（从 0 开始）
   * @returns {*} 处理结果
   */
  function buildWidgetCard(entry, index) {
    var meta = (state.registry && state.registry[entry.id]) || { id: entry.id, title: entry.id, desc: "" };
    var card = el("section", "pg-card dash-widget");
    card.style.gridColumn = "span " + entry.span;
    // R3-11：自定义高度（拖拽下边缘后持久化）
    if (entry.h) {
      card.style.minHeight = entry.h + "px";
    }
    card.setAttribute("draggable", "true");
    card.dataset.index = String(index);
    card.dataset.widgetId = entry.id;
    // UI-02：区块编号只作 DOM 定位用（加 `dash-widget-` 前缀，避免与各页面里的 S-03/ST-01 同 id 冲突）
    card.id = "dash-widget-" + entry.id;

    var head = el("div", "pg-card-head");
    head.appendChild(el("span", "pg-card-title", meta.title));
    // UI-02：区块编号（S-03/N-03 等）只作 HTML id，不再作为副标题渲染到界面
    head.appendChild(el("span", "pg-spacer"));

    // R6-07：板块类组件提供「选行业 / 选概念」控件（默认展示 20，可调 10/20/30/50/全部）
    var pickerDef = SECTOR_PICKERS[entry.id];
    if (pickerDef) {
      var pick = el("button", "pg-btn dash-btn", t(pickerDef.labelKey));
      pick.type = "button";
      var applied = pickerSelection(entry.params, pickerDef);
      pick.title = applied.boards.length
        ? t("dashboard.pick.selected", { n: applied.boards.length, total: applied.boards.length })
        : t(pickerDef.labelKey);
      pick.addEventListener("click", function (event) {
        event.stopPropagation();
        openSectorPicker(entry, meta, pick);
      });
      head.appendChild(pick);
      var summary = el("span", "dash-pick-summary",
        t("dashboard.pick.summary", {
          mode: t(pickerDef.labelKey),
          n: applied.limit > 0 ? applied.limit : "\u221E",
          total: applied.boards.length ? applied.boards.length : "\u2014"
        }));
      head.appendChild(summary);
    }

    // 改参数（R3-11）：按配方声明的参数键生成弹窗（标的 symbol / 周期 period 等）
    var recipeDef = RECIPES[entry.id];
    var paramKeys = recipeDef ? Object.keys(recipeDef.params || {}).concat(Object.keys(entry.params || {})) : [];
    var uniqueKeys = paramKeys.filter(function (key, position) { return paramKeys.indexOf(key) === position; });
    if (uniqueKeys.length) {
      var tune = el("button", "pg-btn dash-btn", t("dashboard.editParams"));
      tune.type = "button";
      tune.title = t("dashboard.editParamsHint");
      tune.addEventListener("click", function (event) {
        event.stopPropagation();
        editWidgetParams(entry, uniqueKeys);
      });
      head.appendChild(tune);
    }

    var remove = el("button", "pg-btn dash-btn", t("action.close"));
    remove.type = "button";
    remove.title = t("dashboard.removeWidget");
    remove.addEventListener("click", function (event) {
      event.stopPropagation();
      // R6-01：移除组件加二次确认（原实现点一下即删，误触代价高且无撤销）
      if (!window.confirm(t("dashboard.removeWidgetConfirm", { name: meta.title || entry.id }))) {
        return;
      }
      var dash = activeDashboard();
      if (!dash) {
        return;
      }
      dash.widgets.splice(index, 1);
      saveState(true);
      render();
    });
    head.appendChild(remove);
    card.appendChild(head);

    var body = el("div", "pg-body");
    card.appendChild(body);
    // 注册表与接口均为异步：直接进入加载态，避免先闪一次空态
    loadWidget(body, card, entry, meta);

    // 拖拽排序
    card.addEventListener("dragstart", function (event) {
      draggedIndex = index;
      event.dataTransfer.effectAllowed = "move";
    });
    // R6-01：卡片右键菜单（改参数 / 复位拉伸 / 移除），不冒泡到画布菜单
    card.addEventListener("contextmenu", function (event) {
      event.preventDefault();
      event.stopPropagation();
      openWidgetMenu(entry, index, meta, event.clientX, event.clientY);
    });
    card.addEventListener("dragover", function (event) {
      event.preventDefault();
    });
    card.addEventListener("drop", function (event) {
      event.preventDefault();
      if (draggedIndex === null || draggedIndex === index) {
        return;
      }
      var dash = activeDashboard();
      var moved = dash.widgets.splice(draggedIndex, 1)[0];
      dash.widgets.splice(index, 0, moved);
      draggedIndex = null;
      saveState(true);
      render();
    });

    // R3-11：右下角拖拽手柄 —— 自由拉伸宽度（吸附 48 列，8~48 约束）
    var widthHandle = el("div", "dash-resize-handle");
    widthHandle.title = t("dashboard.dragResize");
    // D-11：手柄是无指针设备的键盘入口（左右方向键 ±4 列，Home 回到 48 列）
    widthHandle.setAttribute("tabindex", "0");
    widthHandle.setAttribute("role", "slider");
    widthHandle.setAttribute("aria-label", t("dashboard.dragResize"));
    widthHandle.addEventListener("keydown", function (event) {
      // R6-04：键盘完整路径 —— ←/→ 调宽 ±4 列、↑/↓ 调高 ±40px、
      // Home 满宽、Delete 复位到 1/2 宽 + 默认高度
      var spanStep = 0;
      var heightStep = 0;
      if (event.key === "ArrowLeft") {
        spanStep = -4;
      } else if (event.key === "ArrowRight") {
        spanStep = 4;
      } else if (event.key === "ArrowUp") {
        heightStep = -40;
      } else if (event.key === "ArrowDown") {
        heightStep = 40;
      } else if (event.key === "Home") {
        spanStep = 48 - entry.span;
      } else if (event.key === "Delete") {
        entry.span = 24;
        delete entry.h;
        card.style.gridColumn = "span 24";
        card.style.minHeight = "";
        saveState(true);
        return;
      }
      if (!spanStep && !heightStep) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      if (spanStep) {
        entry.span = Math.max(8, Math.min(48, entry.span + spanStep));
        card.style.gridColumn = "span " + entry.span;
      }
      if (heightStep) {
        var current = entry.h || Math.round(card.getBoundingClientRect().height);
        entry.h = Math.max(120, Math.min(900, Math.round((current + heightStep) / 8) * 8));
        card.style.minHeight = entry.h + "px";
      }
      saveState(true);
    });
    widthHandle.addEventListener("pointerdown", function (event) {
      event.preventDefault();
      event.stopPropagation();
      var grid = card.parentElement;
      if (!grid) {
        return;
      }
      var colWidth = grid.getBoundingClientRect().width / 48;
      var startX = event.clientX;
      var startY = event.clientY;
      var startSpan = entry.span;
      var startHeight = Math.round(card.getBoundingClientRect().height);
      widthHandle.setPointerCapture(event.pointerId);
      /** 拖动中实时改宽高：右下角手柄双向（宽吸附 48 列 8~48，高 120~900 步进 8px）。
       * @param {PointerEvent} moveEvent 指针移动事件
       * @returns {void} 无返回值
       */
      function onMove(moveEvent) {
        var deltaX = Math.round((moveEvent.clientX - startX) / colWidth);
        var next = Math.max(8, Math.min(48, startSpan + deltaX));
        entry.span = next;
        card.style.gridColumn = "span " + next;
        var deltaY = moveEvent.clientY - startY;
        if (Math.abs(deltaY) >= 4) {
          var height = Math.max(120, Math.min(900, Math.round((startHeight + deltaY) / 8) * 8));
          entry.h = height;
          card.style.minHeight = height + "px";
        }
      }
      /** 松手后落盘并解除监听。 */
      function onUp() {
        widthHandle.removeEventListener("pointermove", onMove);
        widthHandle.removeEventListener("pointerup", onUp);
        saveState(true);
      }
      widthHandle.addEventListener("pointermove", onMove);
      widthHandle.addEventListener("pointerup", onUp);
    });
    card.appendChild(widthHandle);

    // R3-11：下边缘手柄 —— 调整卡片高度（120~900px）
    var heightHandle = el("div", "dash-height-handle");
    heightHandle.title = t("dashboard.dragHeight");
    heightHandle.addEventListener("pointerdown", function (event) {
      event.preventDefault();
      event.stopPropagation();
      var startY = event.clientY;
      var startHeight = card.getBoundingClientRect().height;
      heightHandle.setPointerCapture(event.pointerId);
      /** 拖动中实时改高度。
       * @param {PointerEvent} moveEvent 指针移动事件
       * @returns {void} 无返回值
       */
      function onMove(moveEvent) {
        var next = Math.max(120, Math.min(900, Math.round(startHeight + (moveEvent.clientY - startY))));
        entry.h = next;
        card.style.minHeight = next + "px";
      }
      /** 松手后落盘。 */
      function onUp() {
        heightHandle.removeEventListener("pointermove", onMove);
        heightHandle.removeEventListener("pointerup", onUp);
        saveState(true);
      }
      heightHandle.addEventListener("pointermove", onMove);
      heightHandle.addEventListener("pointerup", onUp);
    });
    card.appendChild(heightHandle);

    return card;
  }

  /** 加载单个组件数据（失败就地显示错误 + 重试）。
   * @param {HTMLElement} body 区块容器（.pg-body，四态 data-state 挂在此节点）
   * @param {HTMLElement} card 卡片 DOM 节点
   * @param {*} entry 列表项 / 组件条目
   * @param {Object} meta 元数据对象
   */
  function loadWidget(body, card, entry, meta) {
    // R6-03：重绘前释放本卡旧图表实例，避免实例与 canvas 累积
    disposeChartsIn(body);
    body.innerHTML = "";
    // 四态规范（§7.9）：显式标记 loading，巡检与 wait_blocks 才能等到收口
    body.setAttribute("data-state", "loading");
    // R6-05：固定 3 行骨架占位（不再用单行文字），卡片高度不因加载态跳变
    var skeleton = el("div", "pg-skeleton");
    for (var skeletonIndex = 0; skeletonIndex < 3; skeletonIndex += 1) {
      skeleton.appendChild(el("div", "pg-skeleton-row"));
    }
    body.appendChild(skeleton);
    var recipe = RECIPES[entry.id];
    if (!recipe) {
      body.innerHTML = "";
      var hint = el("div", "pg-state");
      hint.appendChild(el("div", "pg-state-title", meta.desc || meta.title));
      if (meta.api && meta.api.length) {
        hint.appendChild(el("div", "pg-state-hint", t("dashboard.apiPrefix") + meta.api.join("、") + "（" + t("dashboard.noRenderer") + "）"));
      }
      body.appendChild(hint);
      // 无内置渲染器的组件属「如实说明」而非加载中，收口为默认态
      body.setAttribute("data-state", "default");
      return;
    }
    // P2-01：合并配方默认参数与用户传入参数（ST 系列不再硬编码 symbol）
    var params = Object.assign({}, recipe.params || {}, entry.params || {});
    // 个股类组件必须带 symbol：覆盖全部 /api/stock/* 接口（原先只判 realtime-panel，
    // 导致 ST-08/ST-09/ST-10 直接打后端拿 400 并以「请求参数不合法：symbol」错误态收场）。
    if (recipe.path.indexOf("/api/stock/") === 0 && !params.symbol) {
      body.innerHTML = "";
      var hint = el("div", "pg-state");
      hint.appendChild(el("div", "pg-state-title", t("dashboard.stockEmptyTitle")));
      hint.appendChild(el("div", "pg-state-hint", t("dashboard.stockEmptyHint")));
      var libBtn = el("button", "pg-btn", t("dashboard.library"));
      libBtn.type = "button";
      libBtn.addEventListener("click", function () { openLibrary(); });
      hint.appendChild(libBtn);
      body.appendChild(hint);
      // 无数据非加载中，收口为默认态
      body.setAttribute("data-state", "default");
      return;
    }
    fetchJson(recipe.path, params)
      .then(function (data) {
        disposeChartsIn(body);
        body.innerHTML = "";
        // R6-07：板块类组件按「已选行业 + 展示数量」裁剪后渲染（默认 20，可调）
        var pickerDef = SECTOR_PICKERS[entry.id];
        var payload = pickerDef ? applySectorFilter(data, pickerDef, entry.params) : data;
        var pickedList = pickerDef ? (payload[pickerDef.listKey] || []) : [];
        if (pickerDef && Array.isArray(data[pickerDef.listKey]) && data[pickerDef.listKey].length
            && !pickedList.length) {
          // 选了行业但这批数据里一个都不匹配 → 明确空态，不显示空白
          body.appendChild(el("div", "pg-state",
            t("dashboard.pick.empty", { label: t(pickerDef.labelKey) })));
          body.setAttribute("data-state", "empty");
          return;
        }
        renderWidgetBody(body, payload, recipe, meta);
        // 重新加载时先清掉旧徽章，避免重复叠加
        card.querySelectorAll(".dash-badges").forEach(function (node) { node.remove(); });
        var badges = el("div", "dash-badges");
        if (data.source) {
          badges.appendChild(el("span", "badge-src", data.source));
        }
        if (data.caliber) {
          badges.appendChild(el("span", "badge-caliber", data.caliber));
        }
        if (badges.children.length) {
          card.querySelector(".pg-card-head").appendChild(badges);
        }
        body.setAttribute("data-state", "default");
      })
      .catch(function (error) {
        body.innerHTML = "";
        var box = el("div", "pg-state");
        box.appendChild(el("div", "pg-state-title", t("state.errorTitle")));
        box.appendChild(el("div", "pg-state-hint", error.message));
        var retry = el("button", "pg-btn", t("action.retry"));
        retry.type = "button";
        retry.addEventListener("click", function () { loadWidget(body, card, entry, meta); });
        box.appendChild(retry);
        body.appendChild(box);
        body.setAttribute("data-state", "error");
      });
  }

  // =============================================================
  // 布局与工具栏
  // =============================================================
  /** 渲染仪表板页。 */
  /** 「无仪表板」引导态（四态规范：无 / 空 / 加载 / 错误）。
   * @param {HTMLElement} container 页面容器
   * @returns {void} 无返回值
   */
  function renderNoDashboard(container) {
    var box = el("div", "pg-state");
    box.setAttribute("data-state", "empty");
    box.appendChild(el("div", "pg-state-title", t("dashboard.noneTitle")));
    box.appendChild(el("div", "pg-state-hint", t("dashboard.noneHint")));
    var row = el("div", "page-toolbar");
    var create = el("button", "pg-btn primary", "+ " + t("dashboard.new"));
    create.type = "button";
    create.addEventListener("click", openCreateDashboard);
    row.appendChild(create);
    box.appendChild(row);
    container.appendChild(box);
  }

  /** 渲染仪表板编辑画布：工具栏（新建/管理/组件库/模板/保存）+ 48 列组件网格。
   * @returns {void} 无返回值（异步渲染，registry 就绪后挂载 DOM）
   */
  function render() {
    var container = document.getElementById("page-dashboard");
    if (!container) {
      return;
    }
    loadRegistry().then(function () {
      // R3-02：侧栏分组树与页内内容同步刷新
      renderSidebar();
      // R6-03：全量重建前释放旧图表实例 + 确保全局 resize 监听器只注册一次
      disposeChartsIn(container);
      bindGlobalChartResize();
      container.innerHTML = "";
      // 四态之一：一个仪表板都没有（用户删光或首次空仓）→ 引导态，不自动造假数据
      if (!state.dashboards.length) {
        renderNoDashboard(container);
        return;
      }
      var toolbar = el("div", "page-toolbar");
      var tabs = el("div", "pg-tabs");
      state.dashboards.forEach(function (dash) {
        var button = el("button", "pg-tab" + (dash.id === state.activeId ? " active" : ""), dash.name);
        button.type = "button";
        button.addEventListener("click", function () {
          state.activeId = dash.id;
          saveState(true);
          render();
        });
        tabs.appendChild(button);
      });
      toolbar.appendChild(tabs);

      var addDash = el("button", "pg-btn primary", "+ " + t("dashboard.new"));
      addDash.type = "button";
      addDash.addEventListener("click", openCreateDashboard);
      toolbar.appendChild(addDash);

      // R6-08 / F6-01：8 个操作按钮收进「⋯ 更多」，与画布右键菜单共用同一份定义。
      // 原来 11 个控件平铺整行，且与侧栏「新建仪表板 / 管理 / 组件库」三处重复。
      var more = el("button", "pg-btn", "⋯ " + t("dashboard.more"));
      more.type = "button";
      more.id = "dash-more-btn";
      more.setAttribute("aria-haspopup", "menu");
      more.addEventListener("click", function (event) {
        event.stopPropagation();
        var rect = more.getBoundingClientRect();
        openCanvasMenu(rect.left, rect.bottom + 4);
      });
      toolbar.appendChild(more);

      toolbar.appendChild(el("span", "pg-spacer"));
      // R6-05：刷新进度（"刷新中 3/6"），全部收口后自动隐藏
      var progress = el("span", "dash-progress", "");
      progress.id = "dash-progress";
      progress.hidden = true;
      toolbar.appendChild(progress);
      var save = el("button", "pg-btn", t("dashboard.save"));
      save.type = "button";
      save.addEventListener("click", function () { saveState(false); });
      toolbar.appendChild(save);
      container.appendChild(toolbar);
      container.appendChild(el("div", "pg-card-sub", t("dashboard.dragHint")));

      var grid = el("div", "pg-grid dash-grid");
      grid.id = "dash-grid";
      // R6-08：画布空白处右键唤出同一份操作菜单
      bindCanvasMenu(grid);
      // R3-12：接收组件库拖入的组件（自定义 MIME，与卡片换位拖拽互不干扰）
      bindCanvasDrop(grid);
      var dash = activeDashboard();
      if (!dash.widgets.length) {
        var empty = el("div", "pg-state dash-empty");
        empty.style.gridColumn = "span 48";
        empty.appendChild(el("div", "pg-state-title", t("dashboard.empty")));
        var libraryButton = el("button", "pg-btn", t("dashboard.library"));
        libraryButton.type = "button";
        libraryButton.addEventListener("click", openLibrary);
        empty.appendChild(libraryButton);
        grid.appendChild(empty);
      } else {
        dash.widgets.forEach(function (entry, index) {
          grid.appendChild(buildWidgetCard(entry, index));
        });
      }
      container.appendChild(grid);
      watchProgress();
    });
  }

  /** 刷新进度监视器的定时器句柄（同一时刻只保留一个）。 */
  var progressTimer = null;

  /** 监视卡片收口情况并更新「刷新中 x/y」文案（R6-05）。
   *
   * 每 120ms 统计一次 `#dash-grid .pg-body[data-state=loading]`，全部收口后自动隐藏。
   * 采用轮询而非计数器，是因为 loadWidget 有「无渲染器 / 缺 symbol / 空态 / 错误」多条
   * 提前返回路径，逐个打点容易漏计。
   *
   * @returns {void} 无返回值
   */
  function watchProgress() {
    if (progressTimer) {
      window.clearInterval(progressTimer);
      progressTimer = null;
    }
    var node = document.getElementById("dash-progress");
    if (!node) {
      return;
    }
    var started = Date.now();
    progressTimer = window.setInterval(function () {
      var grid = document.getElementById("dash-grid");
      var current = document.getElementById("dash-progress");
      if (!grid || !current) {
        window.clearInterval(progressTimer);
        progressTimer = null;
        return;
      }
      var all = grid.querySelectorAll(".dash-widget").length;
      var loading = grid.querySelectorAll('.dash-widget .pg-body[data-state="loading"]').length;
      if (!loading || Date.now() - started > 60000) {
        current.hidden = true;
        current.textContent = "";
        window.clearInterval(progressTimer);
        progressTimer = null;
        return;
      }
      current.hidden = false;
      current.textContent = t("dashboard.refreshing", { done: all - loading, total: all });
    }, 120);
  }

  /** 打开「新建仪表板」输入弹窗：工具栏按钮与侧栏入口共用同一实现。
   * @returns {void}
   */
  function openCreateDashboard() {
    if (!window.UI) {
      return;
    }
    window.UI.modal({
      title: t("dashboard.new"),
      input: true,
      defaultValue: t("dashboard.defaultName"),
      /** 新建仪表板确认：空名回落默认名，创建后切为当前并重绘。
       * @param {*} value 待格式化或处理的值
       */
      onConfirm: function (value) {
        var name = value || t("dashboard.defaultName");
        var dash = { id: "dash-" + Date.now(), name: name, widgets: [] };
        state.dashboards.push(dash);
        state.activeId = dash.id;
        saveState(true);
        render();
      }
    });
  }

  /** 组件库抽屉。 */
  /** 组件库分组标题：把注册表里的路由（/#sectors）翻成中文视图名。
   * 缺键或未知路由时退回原始路由，避免出现英文/键名泄漏。
   * @param {string} page 注册表里的页面标识（形如 /#sectors）
   * @returns {string} 分组标题
   */
  function libraryGroupLabel(page) {
    var map = {
      sectors: "nav.sectors",
      stock: "nav.stock",
      news: "nav.news",
      fund: "nav.fund",
      portfolio: "nav.portfolio",
      "sector-val": "nav.sectorVal",
      dashboard: "nav.dashboard",
      home: "nav.home",
      yjb: "nav.yjb"
    };
    var name = String(page || "").replace(/^\/?#\/?/, "").split("/")[0];
    var key = map[name];
    var text = key ? t(key) : "";
    return text && text !== key ? text : String(page || "");
  }

  /** 组件库分类标签（6 类能力分类；未知分类回退到页面名，再回退到编号）。
   * @param {Object} item 注册表条目（含 category / page）
   * @returns {string} 中文分类名
   */
  function libraryCategoryLabel(item) {
    var cat = (item && item.category) || "";
    if (cat) {
      var key = "dashboard.libCat." + cat;
      var text = t(key);
      if (text && text !== key) {
        return text;
      }
    }
    return libraryGroupLabel(item && item.page);
  }

  /** 组件缩略预览：按配方渲染类型生成迷你示意（R3-12 L-02）。
   * @param {string} widgetId 组件编号
   * @returns {HTMLElement} 缩略预览节点（高 44px 的迷你图形）
   */
  function libraryThumb(widgetId) {
    var box = el("div", "dash-lib-thumb");
    var recipe = RECIPES[widgetId] || {};
    var kind = recipe.kind || "panel";
    if (kind === "chart") {
      // 迷你折线 + 成交量柱
      var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("viewBox", "0 0 120 44");
      svg.setAttribute("preserveAspectRatio", "none");
      svg.classList.add("dash-lib-thumb-svg");
      var line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
      line.setAttribute("points", "0,30 15,22 30,26 45,14 60,18 75,8 90,12 105,5 120,9");
      line.setAttribute("fill", "none");
      line.setAttribute("stroke", "var(--accent)");
      line.setAttribute("stroke-width", "1.6");
      svg.appendChild(line);
      [6, 18, 30, 42, 54, 66, 78, 90, 102, 114].forEach(function (x, position) {
        var bar = document.createElementNS("http://www.w3.org/2000/svg", "rect");
        var height = 4 + ((position * 7) % 11);
        bar.setAttribute("x", String(x));
        bar.setAttribute("y", String(44 - height));
        bar.setAttribute("width", "5");
        bar.setAttribute("height", String(height));
        bar.setAttribute("fill", "var(--border)");
        svg.appendChild(bar);
      });
      box.appendChild(svg);
    } else if (kind === "table") {
      // 迷你表格：三行四列
      for (var row = 0; row < 3; row += 1) {
        var lineBox = el("div", "dash-lib-thumb-row");
        for (var col = 0; col < 4; col += 1) {
          lineBox.appendChild(el("span", "dash-lib-thumb-cell" + (col === 0 ? " wide" : "")));
        }
        box.appendChild(lineBox);
      }
    } else if (kind === "metrics" || kind === "yjbCollect") {
      // 迷你指标块：2×2 数字块
      for (var i = 0; i < 4; i += 1) {
        var cellBox = el("div", "dash-lib-thumb-metric");
        cellBox.appendChild(el("span", "dash-lib-thumb-num"));
        cellBox.appendChild(el("span", "dash-lib-thumb-bar"));
        box.appendChild(cellBox);
      }
    } else if (kind === "news") {
      // 迷你资讯：三条文字行
      for (var n = 0; n < 3; n += 1) {
        box.appendChild(el("div", "dash-lib-thumb-line" + (n === 2 ? " short" : "")));
      }
    } else {
      // 面板/未知类型：文本块示意
      box.appendChild(el("div", "dash-lib-thumb-block"));
      box.appendChild(el("div", "dash-lib-thumb-line short"));
    }
    return box;
  }

  /** L-03 组件说明表：全量注册表（编号/组件名/分类/默认跨列/绑定接口/能力说明）。
   * @returns {void} 无返回值（弹窗挂载 DOM）
   */
  function openLibraryTable() {
    var registry = state.registry || {};
    var ids = Object.keys(registry).sort();
    if (!window.UI) {
      return;
    }
    var overlay = el("div", "ui-overlay");
    var box = el("div", "ui-modal wide");
    box.appendChild(el("div", "ui-modal-title", t("dashboard.libTableTitle")));
    box.appendChild(el("div", "ui-modal-sub",
      t("dashboard.libTableCount", { n: ids.length })));
    var table = el("table", "pg-table");
    var head = document.createElement("tr");
    [t("dashboard.libTable.col.id"), t("dashboard.libTable.col.name"),
     t("dashboard.libTable.col.category"), t("dashboard.libTable.col.span"),
     t("dashboard.libTable.col.api"), t("dashboard.libTable.col.desc")]
      .forEach(function (label) {
        var th = document.createElement("th");
        th.textContent = label;
        head.appendChild(th);
      });
    table.appendChild(head);
    ids.forEach(function (id) {
      var item = registry[id] || {};
      var recipe = RECIPES[id] || {};
      var tr = document.createElement("tr");
      var cells = [
        { text: id, cls: "mono" },
        { text: item.title || id },
        { text: libraryCategoryLabel(item) },
        { text: String(item.span || "—") + t("dashboard.libTable.spanUnit") },
        { text: recipe.path || (item.api && item.api[0]) || "—", cls: "mono" },
        { text: item.desc || "—" }
      ];
      cells.forEach(function (cell) {
        var td = document.createElement("td");
        td.textContent = cell.text;
        if (cell.cls) {
          td.className = cell.cls;
        }
        tr.appendChild(td);
      });
      table.appendChild(tr);
    });
    box.appendChild(table);
    var closeRow = el("div", "page-toolbar");
    var closeBtn = el("button", "pg-btn primary", t("action.close"));
    closeBtn.type = "button";
    closeBtn.addEventListener("click", function () { overlay.remove(); });
    closeRow.appendChild(closeBtn);
    box.appendChild(closeRow);
    overlay.appendChild(box);
    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) {
        overlay.remove();
      }
    });
    document.body.appendChild(overlay);
  }

  /** 渲染组件库主体：搜索 + 分类 chips + 预览卡网格（DS-02）。
   *
   * 抽屉 `openLibrary` 与独立页 `#/widget-library` 共用本函数，
   * 保证两处组件清单、搜索、分类与拖拽行为完全一致（单一实现）。
   * @param {HTMLElement} host 宿主容器
   * @param {Object} opts 选项：{onAdded:Function(添加成功回调)}
   * @returns {void} 无返回值
   */
  function renderLibraryGrid(host, opts) {
    var options = opts || {};
    var registry = state.registry || {};
    // r9：按**能力分类**分组（设计基准 §5.4 L-01 的 6 类），不再按页面路由分组——
    // 页面分组会把 "#/watchlist" "#/lhb" 这类路由串直接漏成 chips 文案，且与冻结原型不符。
    var buckets = {};
    Object.keys(registry).forEach(function (id) {
      var item = registry[id];
      var cat = item.category || "table";
      buckets[cat] = buckets[cat] || [];
      buckets[cat].push(item);
    });
    var catOrder = ["market", "chart", "table", "news", "fin", "filter"];
    var catKeys = catOrder.filter(function (cat) { return (buckets[cat] || []).length > 0; });
    var activeCat = "__all__";

    // 搜索框
    var searchRow = el("div", "page-toolbar");
    var search = document.createElement("input");
    search.type = "search";
    search.placeholder = t("dashboard.librarySearch");
    search.setAttribute("aria-label", t("dashboard.librarySearch"));
    search.style.cssText =
      "flex:1;min-width:160px;background:var(--bg-panel);border:1px solid var(--border);" +
      "color:var(--text);border-radius:5px;padding:4px 8px";
    searchRow.appendChild(search);
    host.appendChild(searchRow);

    var filterRow = el("div", "page-toolbar dash-lib-filters");
    host.appendChild(filterRow);

    var listHost = el("div", "dash-lib-grid");
    listHost.id = "dash-lib-grid";
    host.appendChild(listHost);
    host.appendChild(el("div", "pg-card-sub", t("dashboard.libDragHint")));

    /** 按关键词与分类过滤后重绘预览卡网格（关键词匹配 id/标题/说明）。
     * @returns {void} 无返回值
     */
    function renderList() {
      var keyword = (search.value || "").trim().toLowerCase();
      listHost.innerHTML = "";
      var total = 0;
      catKeys.forEach(function (cat) {
        if (activeCat !== "__all__" && cat !== activeCat) {
          return;
        }
        var items = buckets[cat].filter(function (item) {
          if (!keyword) {
            return true;
          }
          var hay = (item.id + " " + item.title + " " + (item.desc || "")).toLowerCase();
          return hay.indexOf(keyword) >= 0;
        });
        if (!items.length) {
          return;
        }
        total += items.length;
        items.forEach(function (item) {
          // L-02 预览卡：缩略预览 + 编号 + 标题 + 能力说明 + 默认跨列 + 添加按钮，整卡可拖拽
          var card = el("div", "dash-lib-card");
          card.setAttribute("draggable", "true");
          card.dataset.widgetId = item.id;
          card.appendChild(libraryThumb(item.id));
          var info = el("div", "dash-lib-info");
          var titleRow = el("div", "dash-lib-title-row");
          titleRow.appendChild(el("span", "dash-lib-id", item.id));
          titleRow.appendChild(el("span", "dash-lib-title", item.title));
          info.appendChild(titleRow);
          if (item.desc) {
            info.appendChild(el("div", "dash-lib-desc", item.desc));
          }
          info.appendChild(el("div", "dash-lib-meta",
            t("dashboard.libSpan", { n: item.span || "—" })));
          var actions = el("div", "dash-lib-actions");
          // R6-04：页面控件与禁用通道组件不可添加 —— 禁用按钮并写明原因（禁止静默失败）
          var blocked = UNAVAILABLE[item.id];
          var add = el("button", "pg-btn primary", t("action.add"));
          add.type = "button";
          if (blocked) {
            add.textContent = t("dashboard.addDisabled");
            add.disabled = true;
            add.title = blocked.reason;
            add.classList.add("is-disabled");
            info.appendChild(el("div", "dash-lib-blocked",
              (blocked.control ? t("dashboard.controlOnly") : t("dashboard.unavailable")) + "：" + blocked.reason));
          } else {
            add.addEventListener("click", function (event) {
              event.stopPropagation();
              addWidgetById(item.id);
              if (options.onAdded) {
                options.onAdded(item);
              }
              toast(t("dashboard.widgetAdded", { name: item.title || item.id }));
            });
          }
          actions.appendChild(add);
          info.appendChild(actions);
          card.appendChild(info);

          // 拖拽入位：拖动到仪表板画布即完成添加（与画布 drop 监听配对）
          if (!blocked) {
            card.addEventListener("dragstart", function (event) {
              event.dataTransfer.setData("application/x-openbb-widget", item.id);
              event.dataTransfer.setData("text/plain", item.id);
              event.dataTransfer.effectAllowed = "copy";
              card.classList.add("dragging");
            });
            card.addEventListener("dragend", function () {
              card.classList.remove("dragging");
            });
          } else {
            card.setAttribute("draggable", "false");
            card.classList.add("is-blocked");
          }
          listHost.appendChild(card);
        });
      });
      if (!total) {
        listHost.appendChild(el("div", "pg-state", t("dashboard.emptyLibrary")));
      }
    }

    // 分类 chips：全部 + 6 个能力分类（设计基准 §5.4 L-01 → 共 7 个 chips）
    var allChip = el("button", "pg-tab active", t("dashboard.libraryAll"));
    allChip.type = "button";
    allChip.addEventListener("click", function () {
      activeCat = "__all__";
      filterRow.querySelectorAll(".pg-tab").forEach(function (node) {
        node.classList.toggle("active", node === allChip);
      });
      renderList();
    });
    filterRow.appendChild(allChip);
    catKeys.forEach(function (cat) {
      var chip = el("button", "pg-tab", libraryCategoryLabel({ category: cat }));
      chip.type = "button";
      chip.setAttribute("data-cat", cat);
      chip.addEventListener("click", function () {
        activeCat = cat;
        filterRow.querySelectorAll(".pg-tab").forEach(function (node) {
          node.classList.toggle("active", node === chip);
        });
        renderList();
      });
      filterRow.appendChild(chip);
    });

    search.addEventListener("input", renderList);
    renderList();
  }

  /** 组件库抽屉（保留原入口：仪表板页工具栏「组件库」按钮）。
   * @returns {void} 无返回值（直接挂载 DOM）
   */
  function openLibrary() {
    var overlay = el("div", "ui-overlay");
    var panel = el("aside", "dash-library");
    var head = el("div", "pg-card-head");
    head.appendChild(el("span", "pg-card-title", t("dashboard.library")));
    head.appendChild(el("span", "pg-spacer"));
    // L-03：全量组件说明表（与 /api/widgets 注册表实时一致）
    var tableBtn = el("button", "pg-btn", t("dashboard.libTable"));
    tableBtn.type = "button";
    tableBtn.addEventListener("click", openLibraryTable);
    head.appendChild(tableBtn);
    var close = el("button", "pg-btn", t("action.close"));
    close.type = "button";
    close.addEventListener("click", function () { overlay.remove(); });
    head.appendChild(close);
    panel.appendChild(head);

    // DS-02：与独立页共用同一渲染主体
    renderLibraryGrid(panel, { onAdded: function () { overlay.remove(); } });

    overlay.appendChild(panel);
    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) {
        overlay.remove();
      }
    });
    document.body.appendChild(overlay);
  }

  /** 独立路由 #/widget-library：组件库整页（DS-02，与抽屉同一实现）。
   * @param {HTMLElement} container 页面容器（#page-widget-library）
   */
  function renderLibraryPage(container) {
    loadState();
    container.innerHTML = "";
    var toolbar = el("div", "page-toolbar");
    toolbar.appendChild(el("span", "pg-card-title", t("dashboard.library")));
    toolbar.appendChild(el("span", "pg-spacer"));
    var tableBtn = el("button", "pg-btn", t("dashboard.libTable"));
    tableBtn.type = "button";
    tableBtn.addEventListener("click", openLibraryTable);
    toolbar.appendChild(tableBtn);
    var back = el("button", "pg-btn", t("dashboard.backToBoard"));
    back.type = "button";
    back.addEventListener("click", function () {
      location.hash = "#/dashboard";
    });
    toolbar.appendChild(back);
    container.appendChild(toolbar);

    var host = el("div", "dash-lib-page");
    container.appendChild(host);
    // DS-02 缺陷修复（r9）：此前只有仪表板画布 `render()` 会调 `loadRegistry()`，
    // 直接打开 `#/widget-library`（URL 直达/命令面板/侧栏）时 `state.registry` 仍是 null，
    // 整页显示「没有可添加的组件」——路由可达但功能为空。现显式 ensure 注册表，
    // 并按四态给「加载中 / 默认 / 错误」三态（空态由 renderLibraryGrid 内部处理）。
    container.setAttribute("data-state", "loading");
    host.appendChild(el("div", "pg-card-sub", t("state.loadingShort")));
    loadRegistry().then(function () {
      host.innerHTML = "";
      renderLibraryGrid(host, {});
      container.setAttribute("data-state", "default");
    }).catch(function () {
      host.innerHTML = "";
      var box = el("div", "pg-state");
      box.appendChild(el("div", "pg-state-title", t("dashboard.registryFailed")));
      var retry = el("button", "pg-btn", t("action.refresh"));
      retry.type = "button";
      retry.addEventListener("click", function () {
        renderLibraryPage(container);
      });
      box.appendChild(retry);
      host.appendChild(box);
      container.setAttribute("data-state", "error");
    });
  }

  /** 独立路由 #/dashboard-manage：仪表板管理整页（DS-01，7 操作 + 分组树）。
   * @param {HTMLElement} container 页面容器（#page-dashboard-manage）
   */
  function renderDashboardManage(container) {
    loadState();
    container.innerHTML = "";

    var toolbar = el("div", "page-toolbar");
    toolbar.appendChild(el("span", "pg-card-title", t("dashboard.manage")));
    toolbar.appendChild(el("span", "pg-spacer"));
    var addBtn = el("button", "pg-btn primary", "+ " + t("dashboard.new"));
    addBtn.type = "button";
    addBtn.addEventListener("click", openCreateDashboard);
    toolbar.appendChild(addBtn);
    var libBtn = el("button", "pg-btn", t("dashboard.library"));
    libBtn.type = "button";
    libBtn.addEventListener("click", function () {
      location.hash = "#/widget-library";
    });
    toolbar.appendChild(libBtn);
    container.appendChild(toolbar);

    // 四态之一：无仪表板
    if (!state.dashboards.length) {
      var empty = el("div", "pg-state");
      empty.appendChild(el("div", "pg-state-title", t("dashboard.emptyList")));
      empty.appendChild(el("div", "pg-state-hint", t("dashboard.emptyListHint")));
      var create = el("button", "pg-btn primary", "+ " + t("dashboard.new"));
      create.type = "button";
      create.addEventListener("click", openCreateDashboard);
      empty.appendChild(create);
      container.appendChild(empty);
      container.setAttribute("data-state", "empty");
      return;
    }

    var grid = el("div", "pg-grid");

    // 左栏：分组树（按 group 聚合；点击切换当前仪表板）
    var tree = buildCard("DM-01", t("dashboard.groups"), "g-4", "");
    var groups = {};
    state.dashboards.forEach(function (dash) {
      var group = dash.group || t("dashboard.ungrouped");
      groups[group] = groups[group] || [];
      groups[group].push(dash);
    });
    Object.keys(groups).forEach(function (group) {
      tree.body.appendChild(el("div", "sidebar-heading", group));
      groups[group].forEach(function (dash) {
        var item = el("div", "sidebar-item" + (dash.id === state.activeId ? " active" : ""), dash.name);
        item.addEventListener("click", function () {
          state.activeId = dash.id;
          saveState(true);
          renderDashboardManage(container);
        });
        tree.body.appendChild(item);
      });
    });
    tree.body.setAttribute("data-state", "default");
    grid.appendChild(tree.card);

    // 右栏：当前仪表板 + 7 操作 + 列表
    var main = buildCard("DM-02", t("dashboard.currentBoard"), "g-8",
      state.dashboards.length + " " + t("dashboard.countUnit"));
    var dash = activeDashboard();
    if (dash) {
      var ops = el("div", "page-toolbar");
      [
        { label: t("dashboard.rename"), run: renameDashboard },
        { label: t("dashboard.duplicate"), run: duplicateDashboard },
        { label: t("dashboard.groupTitle"), run: groupDashboard },
        { label: t("dashboard.moveLeft"), run: function () { moveDashboard(-1); } },
        { label: t("dashboard.moveRight"), run: function () { moveDashboard(1); } },
        { label: t("dashboard.remove"), run: deleteDashboard }
      ].forEach(function (action) {
        var button = el("button", "pg-btn", action.label);
        button.type = "button";
        button.addEventListener("click", function () {
          action.run();
          // 操作后在管理页内即时重绘，避免停留在旧快照
          window.setTimeout(function () {
            if (document.getElementById("page-dashboard-manage") &&
                !document.getElementById("page-dashboard-manage").hidden) {
              renderDashboardManage(container);
            }
          }, 120);
        });
        ops.appendChild(button);
      });
      main.body.appendChild(el("div", "pg-card-sub", t("dashboard.currentBoardHint")));
      main.body.appendChild(ops);
    }

    var columns = [
      { title: t("dashboard.colName"), render: function (row) { return row.name; } },
      { title: t("dashboard.colGroup"), render: function (row) { return row.group || t("dashboard.ungrouped"); } },
      { title: t("dashboard.colCount"), num: true, render: function (row) { return (row.widgets || []).length; } },
      {
        title: t("dashboard.colActive"),
        render: function (row) {
          return row.id === state.activeId ? t("dashboard.isActive") : "—";
        }
      }
    ];
    window.Utils.renderTable(main.body, columns, state.dashboards, {
      tableClass: "pg-table",
      wrapClass: "pg-table-wrap",
      onRowClick: function (row) {
        state.activeId = row.id;
        saveState(true);
        location.hash = "#/dashboard";
      }
    });
    main.body.setAttribute("data-state", "default");
    grid.appendChild(main.card);

    container.appendChild(grid);
    container.setAttribute("data-state", "default");
  }

  /** 打开中的画布菜单遮罩节点（同一时刻只允许一个）。 */
  var canvasMenuNode = null;

  /** 关闭画布右键菜单。
   * @returns {void} 无返回值
   */
  function closeCanvasMenu() {
    if (canvasMenuNode && canvasMenuNode.parentNode) {
      canvasMenuNode.parentNode.removeChild(canvasMenuNode);
    }
    canvasMenuNode = null;
  }

  /** 画布操作菜单的条目定义（右键与「⋯ 更多」共用，保证单一实现）。
   * @returns {Array<Object>} 菜单项列表（含分隔线与分组标题）
   */
  function canvasMenuItems() {
    return [
      { group: t("dashboard.ctxGroupBoard") },
      { label: t("dashboard.new"), hint: "Ctrl+N", run: openCreateDashboard },
      { label: t("dashboard.rename"), hint: "F2", run: renameDashboard },
      { label: t("dashboard.duplicate"), run: duplicateDashboard },
      { label: t("dashboard.groupTitle") + "…", run: groupDashboard },
      { sep: true },
      { group: t("dashboard.ctxGroupOrder") },
      { label: t("dashboard.moveLeft"), run: function () { moveDashboard(-1); } },
      { label: t("dashboard.moveRight"), run: function () { moveDashboard(1); } },
      { sep: true },
      { label: t("dashboard.library") + "…", run: openLibrary },
      { label: t("dashboard.templates") + "…", run: openTemplates },
      { label: t("dashboard.save"), hint: "Ctrl+S", run: function () { saveState(false); } },
      { sep: true },
      { label: t("dashboard.remove"), danger: true, run: deleteDashboard }
    ];
  }

  /** 打开画布操作菜单（锚定到视口坐标，自动避让边界）。
   * @param {number} x 视口 X 坐标
   * @param {number} y 视口 Y 坐标
   * @returns {void} 无返回值
   */
  function openCanvasMenu(x, y) {
    closeCanvasMenu();
    if (!state.dashboards.length) {
      toast(t("dashboard.noneHint"));
      return;
    }
    var mask = el("div", "dash-ctx-mask");
    var menu = el("div", "dash-ctx-menu");
    menu.setAttribute("role", "menu");

    canvasMenuItems().forEach(function (item) {
      if (item.sep) {
        menu.appendChild(el("div", "dash-ctx-sep"));
        return;
      }
      if (item.group) {
        menu.appendChild(el("div", "dash-ctx-group", item.group));
        return;
      }
      var button = el("button", "dash-ctx-item" + (item.danger ? " danger" : ""), item.label);
      button.type = "button";
      button.setAttribute("role", "menuitem");
      if (item.hint) {
        button.appendChild(el("span", "pg-spacer"));
        button.appendChild(el("span", "dash-ctx-hint", item.hint));
      }
      button.addEventListener("click", function () {
        closeCanvasMenu();
        item.run();
      });
      menu.appendChild(button);
    });

    mask.appendChild(menu);
    document.body.appendChild(mask);
    canvasMenuNode = mask;

    var width = menu.offsetWidth || 214;
    var height = menu.offsetHeight || 320;
    menu.style.left = Math.max(6, Math.min(x, window.innerWidth - width - 8)) + "px";
    menu.style.top = Math.max(6, Math.min(y, window.innerHeight - height - 8)) + "px";

    mask.addEventListener("click", function (event) {
      if (event.target === mask) {
        closeCanvasMenu();
      }
    });
    mask.addEventListener("contextmenu", function (event) {
      event.preventDefault();
      closeCanvasMenu();
      openCanvasMenu(event.clientX, event.clientY);
    });
    document.addEventListener("keydown", onCanvasMenuKey);

    /** 菜单键盘处理：Esc 关闭。
     * @param {KeyboardEvent} event 键盘事件
     * @returns {void} 无返回值
     */
    function onCanvasMenuKey(event) {
      if (event.key === "Escape") {
        document.removeEventListener("keydown", onCanvasMenuKey);
        closeCanvasMenu();
      }
    }
  }

  /** 组件卡片右键菜单（R6-01 / F6-01）：改参数 / 复位拉伸 / 移除。
   *
   * 与画布右键菜单分开：卡片内右键不冒泡到画布，避免拖拽与菜单互相干扰（冲突检查 #1/#2）。
   *
   * @param {Object} entry 组件条目
   * @param {number} index 卡片在 widgets 中的下标
   * @param {Object} meta 组件元数据
   * @param {number} x 视口 X
   * @param {number} y 视口 Y
   * @returns {void} 无返回值
   */
  function openWidgetMenu(entry, index, meta, x, y) {
    closeCanvasMenu();
    var recipeDef = RECIPES[entry.id];
    var paramKeys = recipeDef ? Object.keys(recipeDef.params || {}).concat(Object.keys(entry.params || {})) : [];
    var uniqueKeys = paramKeys.filter(function (key, position) { return paramKeys.indexOf(key) === position; });

    var mask = el("div", "dash-ctx-mask");
    var menu = el("div", "dash-ctx-menu");
    menu.setAttribute("role", "menu");
    menu.appendChild(el("div", "dash-ctx-group", meta.title || entry.id));

    /** 追加一个菜单项。
     * @param {string} label 文案
     * @param {Function} run 执行函数
     * @param {boolean} disabled 是否禁用
     * @returns {void} 无返回值
     */
    function addItem(label, run, disabled) {
      var button = el("button", "dash-ctx-item", label);
      button.type = "button";
      button.setAttribute("role", "menuitem");
      if (disabled) {
        button.disabled = true;
      } else {
        button.addEventListener("click", function () {
          closeCanvasMenu();
          run();
        });
      }
      menu.appendChild(button);
    }

    addItem(t("dashboard.editParams") + "…", function () {
      editWidgetParams(entry, uniqueKeys);
    }, !uniqueKeys.length);
    addItem(t("dashboard.resetSize"), function () {
      entry.span = 24;
      delete entry.h;
      saveState(true);
      render();
    });
    menu.appendChild(el("div", "dash-ctx-sep"));
    var removeItem = el("button", "dash-ctx-item danger", t("dashboard.removeWidget"));
    removeItem.type = "button";
    removeItem.setAttribute("role", "menuitem");
    removeItem.addEventListener("click", function () {
      closeCanvasMenu();
      if (!window.confirm(t("dashboard.removeWidgetConfirm", { name: meta.title || entry.id }))) {
        return;
      }
      var dash = activeDashboard();
      if (dash) {
        dash.widgets.splice(index, 1);
        saveState(true);
        render();
      }
    });
    menu.appendChild(removeItem);

    mask.appendChild(menu);
    document.body.appendChild(mask);
    canvasMenuNode = mask;
    var width = menu.offsetWidth || 200;
    var height = menu.offsetHeight || 160;
    menu.style.left = Math.max(6, Math.min(x, window.innerWidth - width - 8)) + "px";
    menu.style.top = Math.max(6, Math.min(y, window.innerHeight - height - 8)) + "px";
    mask.addEventListener("click", function (event) {
      if (event.target === mask) {
        closeCanvasMenu();
      }
    });
    document.addEventListener("keydown", function onKey(event) {
      if (event.key === "Escape") {
        document.removeEventListener("keydown", onKey);
        closeCanvasMenu();
      }
    });
  }

  /** 绑定画布右键：卡片内不拦截（卡片菜单另行处理），空白处弹画布菜单。
   * @param {HTMLElement} grid 画布网格节点
   * @returns {void} 无返回值
   */
  function bindCanvasMenu(grid) {
    grid.addEventListener("contextmenu", function (event) {
      if (event.target.closest && event.target.closest(".dash-widget")) {
        return;
      }
      event.preventDefault();
      openCanvasMenu(event.clientX, event.clientY);
    });
    bindDashboardShortcuts();
  }

  /** 仪表板快捷键是否已绑定（全局只绑一次，避免每次渲染叠加监听器）。 */
  var shortcutsBound = false;

  /** 绑定仪表板快捷键（R6-01 / F6-01）。
   *
   * - `Ctrl+N` 新建仪表板 / `F2` 重命名当前板 / `Ctrl+S` 保存布局
   * - `Shift+F10` 或菜单键：在画布空白处唤出操作菜单（键盘可达路径）
   *
   * 仅在 `#/dashboard` 生效；输入框/文本域聚焦时不拦截，避免抢走打字。
   *
   * @returns {void} 无返回值
   */
  function bindDashboardShortcuts() {
    if (shortcutsBound) {
      return;
    }
    shortcutsBound = true;
    document.addEventListener("keydown", function (event) {
      var view = (location.hash || "").replace(/^#\/?/, "").split("?")[0];
      if (view !== "dashboard") {
        return;
      }
      var tag = (event.target && event.target.tagName) || "";
      if (tag === "INPUT" || tag === "TEXTAREA" || (event.target && event.target.isContentEditable)) {
        return;
      }
      var mod = event.ctrlKey || event.metaKey;
      if (mod && (event.key === "n" || event.key === "N")) {
        event.preventDefault();
        openCreateDashboard();
        return;
      }
      if (mod && (event.key === "s" || event.key === "S")) {
        event.preventDefault();
        saveState(false);
        return;
      }
      if (event.key === "F2") {
        event.preventDefault();
        renameDashboard();
        return;
      }
      if ((event.shiftKey && event.key === "F10") || event.key === "ContextMenu") {
        event.preventDefault();
        var grid = document.getElementById("dash-grid");
        var rect = grid ? grid.getBoundingClientRect() : { left: 120, top: 120 };
        openCanvasMenu(rect.left + 40, rect.top + 40);
      }
    });
  }

  /** 绑定画布拖拽入位：接收组件库拖入的组件 id（R3-12 L-02）。
   * @param {HTMLElement} grid 仪表板画布网格节点
   * @returns {void} 无返回值
   */
  function bindCanvasDrop(grid) {
    grid.addEventListener("dragover", function (event) {
      // 只响应组件库拖入（自定义 MIME），避免与卡片换位拖拽互相干扰
      var types = event.dataTransfer ? Array.prototype.slice.call(event.dataTransfer.types) : [];
      if (types.indexOf("application/x-openbb-widget") < 0) {
        return;
      }
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
      grid.classList.add("dash-drop-active");
    });
    grid.addEventListener("dragleave", function () {
      grid.classList.remove("dash-drop-active");
    });
    grid.addEventListener("drop", function (event) {
      grid.classList.remove("dash-drop-active");
      var widgetId = event.dataTransfer ? event.dataTransfer.getData("application/x-openbb-widget") : "";
      if (!widgetId) {
        return;
      }
      event.preventDefault();
      addWidgetById(widgetId);
      var meta = (state.registry || {})[widgetId] || {};
      toast(t("dashboard.widgetAdded", { name: meta.title || widgetId }));
    });
  }

  /** 模板库：三个预设布局（全部真实接口组件）。
   * @returns {Object} 处理结果
   */
  function openTemplates() {
    if (!window.UI) {
      return;
    }
    var overlay = el("div", "ui-overlay");
    var box = el("div", "ui-modal");
    box.appendChild(el("div", "ui-modal-title", t("dashboard.templates")));
    var presets = [
      { name: t("dashboard.template.overview"), widgets: ["S-05", "S-03", "S-06", "S-04", "S-07"] },
      { name: t("dashboard.template.news"), widgets: ["N-02", "N-03", "S-03", "S-06"] },
      { name: t("dashboard.template.portfolio"), widgets: ["P-01", "P-03", "F-04", "F-03"] }
    ];
    presets.forEach(function (preset) {
      var button = el("button", "copilot-suggest", preset.name + t("dashboard.widgetCount", { n: preset.widgets.length }));
      button.type = "button";
      button.addEventListener("click", function () {
        var dash = activeDashboard();
        dash.widgets = preset.widgets.map(function (id) {
          var meta = state.registry[id] || {};
          return { id: id, span: meta.span || 24 };
        });
        saveState(true);
        overlay.remove();
        render();
        toast(t("dashboard.saved"));
      });
      box.appendChild(button);
    });
    overlay.appendChild(box);
    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) {
        overlay.remove();
      }
    });
    document.body.appendChild(overlay);
  }

  /** 按 id 添加组件（供命令面板与组件库调用）。
   * @param {*} widgetId 组件 id
   * @param {Object} params 用户传入参数（如 symbol/market，P2-01 透传当前标的）
   */
  function addWidgetById(widgetId, params) {
    // R6-04：页面控件 / 禁用通道组件不允许加入仪表板，如实说明原因
    var blocked = UNAVAILABLE[widgetId];
    if (blocked) {
      toast(blocked.reason);
      return;
    }
    // 命令面板可能在未进入仪表板前调用，此时 state 尚未装载 → 先补 loadState，
    // 否则 activeDashboard() 取空数组会抛 TypeError（P2-01 相关）
    if (!state.dashboards.length) {
      loadState();
    }
    var dash = activeDashboard();
    if (!dash) {
      // 兜底：无当前仪表板时不得抛 TypeError（原先 dash.widgets 会直接崩）
      toast(t("dashboard.noneHint"));
      return;
    }
    if (dash.widgets.some(function (entry) { return entry.id === widgetId; })) {
      toast(t("dashboard.duplicateWidget"));
      return;
    }
    var meta = (state.registry && state.registry[widgetId]) || {};
    dash.widgets.push({ id: widgetId, span: meta.span || 24, params: params || {} });
    saveState(true);
    if (location.hash === "#/dashboard") {
      render();
    }
  }

  /** 渲染侧栏分组树（R3-02）：按 group 归组，未分组排最后。
   * 分组树与页内 tab 同步，任何增删改后都会刷新。
   * @returns {void} 无返回值
   */
  function renderSidebar() {
    var host = document.getElementById("sidebar-dashboards");
    if (!host) {
      return;
    }
    host.innerHTML = "";
    var groups = [];
    state.dashboards.forEach(function (dash) {
      var name = dash.group || "";
      if (groups.indexOf(name) < 0) {
        groups.push(name);
      }
    });
    // 具名分组在前（保持出现顺序），未分组固定排最后
    groups.sort(function (a, b) {
      if (a === b) {
        return 0;
      }
      if (!a) {
        return 1;
      }
      if (!b) {
        return -1;
      }
      return 0;
    });
    groups.forEach(function (group) {
      var heading = el("li", "sidebar-heading", group || t("sidebar.noGroup"));
      host.appendChild(heading);
      state.dashboards.forEach(function (dash) {
        if ((dash.group || "") !== group) {
          return;
        }
        var item = el("li", "sidebar-item" + (dash.id === state.activeId ? " active" : ""),
          dash.name + (dash.id === state.activeId ? t("sidebar.current") : ""));
        item.setAttribute("data-action", "dashboard");
        item.setAttribute("data-dash-id", dash.id);
        host.appendChild(item);
      });
    });
  }

  /** 切换当前仪表板（侧栏分组树点击时调用）。
   * @param {string} dashId 目标仪表板 id
   * @returns {void} 无返回值
   */
  function activate(dashId) {
    var exists = state.dashboards.some(function (dash) {
      return dash.id === dashId;
    });
    if (!exists) {
      return;
    }
    state.activeId = dashId;
    saveState(true);
    render();
  }

  window.Dashboard = {
    render: render,
    addWidgetById: addWidgetById,
    // 侧栏「新建仪表板 / 组件库」入口依赖这两个方法（P1-07），不导出会退化成只跳页不弹层
    createDashboard: openCreateDashboard,
    openLibrary: openLibrary,
    // DS-01/DS-02：独立路由视图（#/dashboard-manage、#/widget-library）由 pages.js 路由调用
    renderManage: renderDashboardManage,
    renderLibraryPage: renderLibraryPage,
    // R3-02：侧栏分组树点击切换 + 管理操作
    activate: activate,
    rename: renameDashboard,
    duplicate: duplicateDashboard,
    remove: deleteDashboard,
    /** 重载仪表板（重新读配置并重绘）。
     * @returns {void} 无返回值
     */
    reload: function () {
      loadState(); render();
    }
  };

  // 暴露初始化：pages.js 路由到 #/dashboard 时调用
  window.renderDashboard = function () {
    loadState();
    render();
  };

  // R6-01 补强：首屏直达二级路由时补渲染一次。
  // 背景：index.html 的脚本顺序是 utils → pages → ... → dashboard，pages.js 的 route()
  // 在首屏执行时 window.Dashboard 尚未定义，`if (window.Dashboard && ...)` 分支被
  // 静默跳过，之后再无触发 → #/dashboard-manage 首屏进来是整页空白（只有切换 hash 才渲染）。
  // 这里在 dashboard.js 完成导出后主动补一次，覆盖"收藏夹/深链直接打开"的场景。
  (function bootOnDeepLink() {
    var raw = (location.hash || "").replace(/^#\/?/, "").split("?")[0];
    if (raw === "dashboard-manage") {
      var manageBox = document.getElementById("page-dashboard-manage");
      if (manageBox) {
        renderDashboardManage(manageBox);
      }
    } else if (raw === "widget-library") {
      var libraryBox = document.getElementById("page-widget-library");
      if (libraryBox) {
        renderLibraryPage(libraryBox);
      }
    } else if (raw === "dashboard") {
      window.renderDashboard();
    }
  })();

  // 时序兜底：pages.js 先于本文件执行，首次进入 #/dashboard 时 renderDashboard 尚未定义，
  // 故本文件加载完成后按当前 hash 补渲染一次（否则首屏空白，刷新后才出现）。
  // R3-02：侧栏分组树不依赖当前路由，初始化后立即渲染一次。
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      loadState();
      renderSidebar();
      if ((location.hash || "").indexOf("#/dashboard") === 0) {
        window.renderDashboard();
      }
    });
  } else {
    loadState();
    renderSidebar();
    if ((location.hash || "").indexOf("#/dashboard") === 0) {
      window.renderDashboard();
    }
  }
})();
