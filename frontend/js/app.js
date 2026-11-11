/* =============================================================
 * OpenBB 中文投研工作台 · 前端入口脚本（阶段 1）
 * 职责：四区占位交互、主题切换、Copilot 抽屉、健康检查渲染。
 * 依赖接口：GET /api/health（唯一调用的后端接口）。
 * 四态处理：本文件只处理健康检查的「加载 / 成功 / 失败」，
 *          业务数据的四态渲染将在后续阶段由 widgets.js 统一实现。
 * ============================================================= */

(function () {
  "use strict";

  // P2-09：共享工具别名（app.js 原本不使用 t，现统一走 i18n）
  var t = function (key, vars) {
    return window.I18N ? window.I18N.t(key, vars) : key;
  };

  // 页面标题映射：与主菜单 data-view 一一对应（P2-09：统一走 i18n）（P2-09：统一走 i18n）
  /** 视图 → 页面标题。**直接引用侧栏的 i18n 键**，不另建一套文案：
   * 标题与侧栏永远同源，避免「板块分析 vs 板块行情」这类不一致。
   * 缺视图会退化为不更新标题（曾导致 watchlist 继承上一页标题），故必须列全。
   */
  const VIEW_TITLES = {
    home: t("nav.homeTitle"),
    dashboard: t("page.dashboard"),
    "dashboard-manage": t("nav.dashboardManage"),
    "widget-library": t("sidebar.library"),
    watchlist: t("sidebar.myWatchlist"),
    news: t("nav.news"),
    sectors: t("nav.sectors"),
    macro: t("nav.macro"),
    valuation: t("nav.valuation"),
    crowding: t("nav.crowding"),
    stock: t("nav.stock"),
    charting: t("nav.charting"),
    "sector-val": t("nav.sectorVal"),
    lhb: t("nav.lhb"),
    portfolio: t("nav.portfolio"),
    fund: t("nav.fund"),
    "fund-combo": t("nav.fundCombo"),
    screener: t("nav.screener"),
    alerts: t("nav.alerts"),
    yjb: t("nav.yjbHoldings"),
    connectors: t("nav.connectors"),
    settings: t("nav.settings"),
    // 第十轮 P1 新 IA（2026-11-10 复验补修：改用 i18n 键，与其余条目同口径，
    // 避免中文硬编码绕过词条字典；键值与 index.html 侧栏 data-i18n 一致）
    today: t("nav.today"),
    market: t("nav.marketOverview"),
    fin: t("nav.finSummary"),
    ann: t("nav.annLib"),
    mgr: t("nav.mgr"),
    tools: t("nav.tools"),
    cand: t("nav.cand")
  };

  let toastTimer = null;

  /** 显示 3 秒自动消失的 Toast 提示。
   * @param {string} message 提示文案（中文）
   */
  function showToast(message) {
    const toast = document.getElementById("toast");
    if (!toast) {
      return;
    }
    toast.textContent = message;
    toast.classList.add("show");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () {
      toast.classList.remove("show");
    }, 2600); // P2-09：统一 toast 超时为 2600ms
  }

  /** 支持的皮肤白名单（REQ-20261111-01）。
   *  - `v6`    ：V6 量化看盘台皮肤（**默认**，用户 2026-11-11 10:31 裁决）
   *  - `light` ：砚浅色（DS-R10-v2.1 §7 定稿色板）
   *  - `dark`  ：`:root` 深色基准（§7.4 OpenBB 官方色板，全等断言基准）
   * 非白名单值一律回落 `v6`，防止把未知值写进 `data-theme` 导致三套样式全部失效。
   */
  const THEMES = ["v6", "light", "dark"];
  const DEFAULT_THEME = "v6";

  /** 把任意输入归一化为白名单内的皮肤名。
   * @param {string} theme 候选皮肤名
   * @returns {string} 白名单内的皮肤名（非法值回落默认）
   */
  function normalizeTheme(theme) {
    return THEMES.indexOf(theme) >= 0 ? theme : DEFAULT_THEME;
  }

  /** 同步皮肤切换器的选中态与 aria-pressed。
   * @param {string} theme 当前生效皮肤
   * @returns {void} 无返回值
   */
  function syncSkinSwitch(theme) {
    const buttons = document.querySelectorAll("#skin-switch button");
    Array.prototype.forEach.call(buttons, function (button) {
      const on = button.getAttribute("data-skin") === theme;
      button.classList.toggle("active", on);
      button.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  /** 应用主题并持久化到 localStorage。
   * @param {string} theme 皮肤名（v6 / light / dark，非法值回落 v6）
   * @returns {void} 无返回值
   */
  function applyTheme(theme) {
    const nextTheme = normalizeTheme(theme);
    document.documentElement.setAttribute("data-theme", nextTheme);
    try {
      window.localStorage.setItem("openbb-theme", nextTheme);
    } catch (error) {
      // 隐私模式下 localStorage 不可用时忽略，主题仍在当前页面生效
    }
    syncSkinSwitch(nextTheme);
    // 换肤后立即重排图表（同步，不等下一帧）。
    // 原因：--sidebar-w / --menu-w 变化会让容器宽度立刻改变，而 ECharts canvas
    // 宽度是固定像素值，必须同步 resize 才不会出现横向溢出（B-01 实测 sw=572/535）。
    resizeCharts();
    // 下一帧再补一次：字体/滚动条等异步布局稳定后再校正
    window.setTimeout(resizeCharts, 60);
  }

  /** 让已挂载的图表重新适配尺寸与主题（换肤后调用）。
   *  缺陷修复（REQ-20261111-01 落地验收 B-01 暴露）：
   *  换肤会改变 --menu-w / --sidebar-w / --toolbar-h，容器宽度随之变化，
   *  但 ECharts canvas 不会自动跟随，导致 `sw=572/535` 横向溢出。
   *  这里对所有已初始化的 ECharts 实例统一 resize（不改任何数据/曲线）。
   * @returns {void} 无返回值
   */
  function resizeCharts() {
    // 1) 本应用自管的实例（core.js P.charts）
    const charts = (window.Pages && window.Pages.charts) || [];
    charts.forEach(function (instance) {
      try {
        instance.resize();
      } catch (error) {
        /* 已释放则忽略 */
      }
    });
    // 2) 兜底：按 DOM 反查所有 ECharts 实例（market.js 等独立初始化的实例）
    if (typeof window.echarts !== "undefined" && echarts.getInstanceByDom) {
      document.querySelectorAll(".market-chart, .mk-chart-box, .pg-chart, .sd-chart").forEach(function (node) {
        try {
          const instance = echarts.getInstanceByDom(node);
          if (instance) {
            instance.resize();
          }
        } catch (error) {
          /* 非图表容器则忽略 */
        }
      });
    }
  }

  /** 切换深浅主题（对应 Ctrl+M）。
   *  REQ-20261111-01：V6 成为默认后，Ctrl+M 在 `v6` ↔ `light` 之间切换；
   *  `dark`（§7.4 深色基准）保留为第三态，由顶栏皮肤切换器显式进入。
   * @returns {void} 无返回值
   */
  function toggleTheme() {
    const current = normalizeTheme(document.documentElement.getAttribute("data-theme"));
    applyTheme(current === "light" ? "v6" : "light");
  }

  /** 打开 Copilot 抽屉。 */
  function openCopilot() {
    const drawer = document.getElementById("copilot");
    if (!drawer) {
      return;
    }
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
  }

  /** 关闭 Copilot 抽屉。 */
  function closeCopilot() {
    const drawer = document.getElementById("copilot");
    if (!drawer) {
      return;
    }
    drawer.classList.remove("open");
    drawer.setAttribute("aria-hidden", "true");
  }

  /** R3-28/R3-30：全局标的代码/市场共享 store（支持订阅/广播）。
   * 三个输入框（mk-symbol / stock-symbol / ch-symbol）通过本 store 互通，
   * 切换页面时自动带代码，避免用户重复输入；各页仍可独立改代码。
   * 与 ui.js 命令面板的 URL query ?symbol=&market= 统一。
   */
  var _current = { symbol: "600519", market: "a" };
  var _subscribers = [];  // 订阅回调列表

  /** 通知全部订阅者：把当前标的与市场广播出去（单个回调异常不阻断主流程）。 */
  function _notify() {
    _subscribers.forEach(function (cb) {
      try {
        cb(_current.symbol, _current.market);
      } catch (e) {
        // 订阅回调异常不影响主流程
      }
    });
  }

  window.SymbolStore = {
    /** 获取当前标的。
     * @returns {{symbol: string, market: string}} 当前标的代码与市场
     */
    get: function () { return { symbol: _current.symbol, market: _current.market }; },

    /** 设置当前标的并广播。
     * @param {string} symbol 标的代码
     * @param {string} market 市场(a/hk/us/fund)
     */
    set: function (symbol, market) {
      _current.symbol = (symbol || "").trim() || _current.symbol;
      _current.market = market || _current.market;
      _notify();
    },

    /** 订阅标的变更（用于输入框自动同步）。
     * @param {Function} callback 回调(symbol, market)
     * @returns {Function} 取消订阅函数
     */
    subscribe: function (callback) {
      _subscribers.push(callback);
      return function () {
        var idx = _subscribers.indexOf(callback);
        if (idx >= 0) _subscribers.splice(idx, 1);
      };
    }
  };

  /** 渲染数据源状态列表（只使用 textContent，避免 HTML 注入）。
   * @param {*} sources 数据源列表
   */
  function renderSources(sources) {
    const list = document.getElementById("source-list");
    if (!list) {
      return;
    }
    list.textContent = "";
    const entries = Object.entries(sources || {});
    if (entries.length === 0) {
      const empty = document.createElement("li");
      empty.className = "source-item";
      empty.textContent = t("sys.sourceEmpty");
      list.appendChild(empty);
      return;
    }
    entries.forEach(function (entry) {
      const key = entry[0];
      const meta = entry[1] || {};
      const item = document.createElement("li");
      item.className = "source-item";

      const name = document.createElement("span");
      name.className = "source-name";
      name.textContent = meta.label || key;

      const status = document.createElement("span");
      status.className = "source-status";
      status.textContent = meta.status || t("sys.unknown");

      item.appendChild(name);
      item.appendChild(status);
      list.appendChild(item);
    });
  }

  /** 渲染运行环境信息。
   * @param {Object} data 数据主体（接口 data 段）
   */
  function renderSystemInfo(data) {
    const container = document.getElementById("system-info");
    if (!container) {
      return;
    }
    const app = data.app || {};
    const cache = data.cache || {};
    const ai = data.ai || {};
    const lines = [
      t("sys.appLabel") + (app.name || t("sys.unknown")) + " v" + (app.version || t("value.dash")),
      t("sys.buildLabel") + (data.build || t("value.dash")),
      t("sys.pythonLabel") + (data.python || t("value.dash")),
      t("sys.listenLabel") + (data.host || t("value.dash")) + ":" + (data.port || t("value.dash")),
      t("sys.cacheFormat", { memory: cache.memory || 0, disk: cache.disk || 0 }),
      ai.configured ? t("sys.aiConfigured", { source: ai.source || t("sys.unknownSource") }) : t("sys.aiNotConfigured"),
      t("sys.serverTimeLabel") + (data.server_time || t("value.dash"))
    ];
    container.textContent = lines.join("\n");
    container.style.whiteSpace = "pre-line";
  }

  /** 设置健康徽章状态。
   * @param {string} text 文本内容
   * @param {Object} state 状态对象
   */
  function setHealthBadge(text, state) {
    const badge = document.getElementById("health-badge");
    if (!badge) {
      return;
    }
    badge.textContent = text;
    badge.classList.remove("ok", "error");
    if (state) {
      badge.classList.add(state);
    }
  }

  /** 调用 /api/health 并渲染真实返回结果。 */
  async function loadHealth() {
    setHealthBadge(t("health.checking"), "");
    try {
      const response = await fetch("/api/health", { cache: "no-store" });
      if (!response.ok) {
        throw new Error(t("sys.appNotOk", { code: response.status }));
      }
      const body = await response.json();
      if (body.error) {
        throw new Error(body.error);
      }
      const data = body.data || {};
      const buildBadge = document.getElementById("build-badge");
      if (buildBadge) {
        buildBadge.textContent = t("sys.buildBadge", { build: data.build || t("value.dash") });
      }
      setHealthBadge(t("health.ok"), "ok");
      renderSources(data.datasources);
      renderSystemInfo(data);
    } catch (error) {
      // 失败时只展示中文提示，不把英文异常原文暴露给用户
      setHealthBadge(t("health.error"), "error");
      renderSources({});
      const container = document.getElementById("system-info");
      if (container) {
        container.textContent = t("error.health");
      }
      showToast(t("health.toastFail"));
    }
  }

  /** 导航单一配置表（REQ-20261109-01 AR-05：NAV_TREE，取代原 VIEW_GROUP + CRUMBS 两套硬编码）。
   * 每个视图一行：group=顶栏一级组（""=首页等特殊页，顶栏不亮）；
   * sidebar=侧栏 data-group（决定面包屑外、侧栏树的展开与高亮归属）；
   * crumb=面包屑链 [[名称键, 落点视图或 null], ...]，末层为当前页。
   * 顶栏高亮 / 侧栏选中 / 面包屑 / 折叠展开四处全部由本表派生，新增视图只改这一处。
   */
  const NAV_TREE = {
    // 2026-11-10 复验补修（审查报告 #4 建议 2）：首页此前 group="" → 顶栏 8 项无一高亮，
    // 「顶栏 / 侧栏 / 面包屑 / 标题四处一致」不成立。按报告建议归入「仪表板」组（总览落地组）。
    home: { group: "dashboard", sidebar: "overview", crumb: [["nav.marketOverview", null]] },
    // 2026-11-10 复验补修：第十轮 P1 新 IA 的 7 个视图此前未登记 NAV_TREE，
    // 结果面包屑整条缺失（renderCrumb 取不到链）+ 顶栏无高亮，违反审查报告
    // 第九节复核项 #4「任一子页面下顶栏 / 侧栏 / 面包屑三处 current 态一致」。
    today: { group: "dashboard", sidebar: "overview", crumb: [["nav.marketOverview", "home"], ["nav.today", null]] },
    market: { group: "market", sidebar: "market", crumb: [["nav.marketOverview", null]] },
    dashboard: { group: "dashboard", sidebar: "overview", crumb: [["nav.dashboard", "dashboard"], ["dashboard.mine", null]] },
    "dashboard-manage": { group: "dashboard", sidebar: "overview", crumb: [["nav.dashboard", "dashboard"], ["nav.dashboardManage", null]] },
    "widget-library": { group: "dashboard", sidebar: "overview", crumb: [["nav.dashboard", "dashboard"], ["sidebar.library", null]] },
    watchlist: { group: "watchlist", sidebar: "my", crumb: [["nav.watchlist", "watchlist"], ["sidebar.myWatchlist", null]] },
    news: { group: "news", sidebar: "research", crumb: [["nav.newsGroup", "news"], ["nav.news", null]] },
    sectors: { group: "market", sidebar: "market", crumb: [["nav.market", "market"], ["nav.sectors", null]] },
    stock: { group: "market", sidebar: "research", crumb: [["nav.market", "market"], ["nav.stock", null]] },
    // 2026-11-10 复验补修：投研分析组 4 页（财报摘要 / 公告库 / 工具 / 候选池）补入 NAV_TREE，
    // 顶栏归「市场」组（与 stock / news 同组口径），侧栏归「投研分析」组。
    fin: { group: "market", sidebar: "research", crumb: [["nav.market", "market"], ["nav.finSummary", null]] },
    ann: { group: "market", sidebar: "research", crumb: [["nav.market", "market"], ["nav.annLib", null]] },
    tools: { group: "market", sidebar: "research", crumb: [["nav.market", "market"], ["nav.tools", null]] },
    cand: { group: "market", sidebar: "research", crumb: [["nav.market", "market"], ["nav.cand", null]] },
    charting: { group: "market", sidebar: "research", crumb: [["nav.market", "market"], ["nav.charting", null]] },
    "sector-val": { group: "market", sidebar: "market", crumb: [["nav.market", "market"], ["nav.sectorVal", null]] },
    macro: { group: "market", sidebar: "market", crumb: [["nav.market", "market"], ["nav.macro", null]] },
    valuation: { group: "market", sidebar: "market", crumb: [["nav.market", "market"], ["nav.valuation", null]] },
    crowding: { group: "market", sidebar: "market", crumb: [["nav.market", "market"], ["nav.crowding", null]] },
    screener: { group: "market", sidebar: "market", crumb: [["nav.market", "market"], ["nav.screener", null]] },
    lhb: { group: "money", sidebar: "market", crumb: [["nav.money", "lhb"], ["nav.lhb", null]] },
    "fund-combo": { group: "portfolio", sidebar: "market", crumb: [["nav.portfolioGroup", "portfolio"], ["nav.fundCombo", null]] },
    portfolio: { group: "portfolio", sidebar: "my", crumb: [["nav.portfolioGroup", "portfolio"], ["nav.portfolio", null]] },
    fund: { group: "portfolio", sidebar: "market", crumb: [["nav.portfolioGroup", "portfolio"], ["nav.fund", null]] },
    yjb: { group: "yjb", sidebar: "my", crumb: [["nav.yjb", "yjb"], ["nav.yjbHoldings", null]] },
    // 2026-11-10 复验补修：经理画像页此前未登记（无面包屑 / 无顶栏高亮）
    mgr: { group: "portfolio", sidebar: "my", crumb: [["nav.portfolioGroup", "portfolio"], ["nav.mgr", null]] },
    // AR-08：提醒中心由铃铛直入，无父级语义 → 面包屑单层（消除「点系统却到设置」的错配）
    alerts: { group: "system", sidebar: "sys", crumb: [["nav.alerts", null]] },
    connectors: { group: "system", sidebar: "sys", crumb: [["nav.system", "settings"], ["nav.connectors", null]] },
    settings: { group: "system", sidebar: "sys", crumb: [["nav.system", "settings"], ["nav.settings", null]] }
  };

  /** 视图 → 顶栏一级项分组（兼容别名：由 NAV_TREE 派生，R6-13 语义不变）。 */
  const VIEW_GROUP = {};
  Object.keys(NAV_TREE).forEach(function (view) {
    VIEW_GROUP[view] = NAV_TREE[view].group;
  });

  /** 面包屑层级表（兼容别名：由 NAV_TREE 派生，AR-08 已修正落点）。 */
  const CRUMBS = {};
  Object.keys(NAV_TREE).forEach(function (view) {
    CRUMBS[view] = NAV_TREE[view].crumb;
  });

  /** 渲染面包屑（同页面只有一个导航项高亮，面包屑体现三级归属）。
   * @param {string} view 当前视图标识
   * @returns {void} 无返回值
   */
  function renderCrumb(view) {
    const host = document.getElementById("crumb");
    if (!host) {
      return;
    }
    host.innerHTML = "";
    const chain = CRUMBS[view] || [];
    if (!chain.length) {
      host.hidden = true;
      return;
    }
    host.hidden = false;
    chain.forEach(function (item, index) {
      const isLast = index === chain.length - 1;
      const label = t(item[0]);
      if (isLast || !item[1]) {
        host.appendChild(elSpan("crumb-item current", label));
      } else {
        const link = elSpan("crumb-item", label);
        link.setAttribute("role", "link");
        link.setAttribute("tabindex", "0");
        link.dataset.view = item[1];
        /** 点击上级回跳。
         * @returns {void} 无返回值
         */
        const go = function () { window.location.hash = "#/" + item[1]; };
        link.addEventListener("click", go);
        link.addEventListener("keydown", function (event) {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            go();
          }
        });
        host.appendChild(link);
      }
      if (!isLast) {
        host.appendChild(elSpan("crumb-sep", "/"));
      }
    });
  }

  /** 创建带类名与文本的 span（面包屑专用，避免与 Utils.el 的语义混淆）。
   * @param {string} cls 类名
   * @param {string} text 文本
   * @returns {HTMLElement} span 节点
   */
  function elSpan(cls, text) {
    const node = document.createElement("span");
    if (cls) {
      node.className = cls;
    }
    if (text !== undefined && text !== null) {
      node.textContent = String(text);
    }
    return node;
  }

  /** 按当前视图同步全部导航选中态（AR-05/AR-06：顶栏 + 侧栏 + 面包屑三处一致）。
   * - 顶栏：同组二级视图共用一级高亮（R6-13 语义不变）；
   * - 侧栏：data-view-target === view 的项设 .active（前进/后退与点击路径均覆盖）；
   * - 折叠：展开当前视图所属分组并写入 tree.js 的折叠记忆（openbb-sb-groups-v1），
   *   其余组保持用户手动状态（不强制收起）。
   * @param {string} view 当前视图标识
   * @returns {void} 无返回值
   */
  function syncNavActive(view) {
    const nav = document.getElementById("main-nav");
    if (nav) {
      const group = VIEW_GROUP[view] !== undefined ? VIEW_GROUP[view] : "";
      nav.querySelectorAll(".nav-item").forEach(function (item) {
        const itemGroup = item.getAttribute("data-group") || "";
        item.classList.toggle("active", !!group && itemGroup === group);
      });
    }

    // AR-06：侧栏选中态 —— 顶栏不亮时（如首页）用户仍能从侧栏看出当前位置
    const sidebar = document.querySelector(".sidebar");
    if (sidebar) {
      sidebar.querySelectorAll(".sidebar-item").forEach(function (item) {
        const target = item.getAttribute("data-view-target") || item.getAttribute("data-view");
        item.classList.toggle("active", !!target && target === view);
      });
      // AR-09/C11：展开当前视图所属分组。
      // 只改 DOM 不写折叠记忆（2026-11-09 复查）：记忆仅由用户手动 toggle 写入，
      // 否则首次进入会被路由预写成「全部展开」，tree.js 无法识别「无记忆」初态。
      const entry = NAV_TREE[view];
      if (entry && entry.sidebar) {
        const current = document.querySelector(
          '.sidebar details.sb-group[data-group="' + entry.sidebar + '"]'
        );
        if (current && !current.open) {
          current.open = true;
        }
      }
    }

    renderCrumb(view);
  }

  /** 绑定主菜单点击。 */
  function bindNavigation() {
    const nav = document.getElementById("main-nav");
    const title = document.getElementById("page-title");
    if (!nav) {
      return;
    }
    // AR-04（REQ-20261109-01）：点 Logo 回首页 —— HTML 注释承诺的行为此前未实现（导航孤儿）
    const logo = document.querySelector(".topbar .logo");
    if (logo) {
      logo.addEventListener("click", function () {
        window.location.hash = "#/home";
        syncNavActive("home");
        if (title) {
          title.textContent = VIEW_TITLES.home || t("nav.homeTitle");
        }
      });
    }
    nav.addEventListener("click", function (event) {
      const button = event.target.closest(".nav-item");
      if (!button) {
        return;
      }
      const view = button.getAttribute("data-view");
      syncNavActive(view);
      if (title) {
        title.textContent = VIEW_TITLES[view] || button.textContent;
      }
      // 视图切换由 pages.js 的 hash 路由接管，此处只维护选中态与标题
    });
  }

  /** 刷新当前视图（P2-03）：按 hash 交给对应视图重取数据，同时刷新健康徽章。 */
  function refreshCurrentView() {
    const view = (window.location.hash.replace(/^#\/?/, "").split("?")[0]) || "home";
    if (view === "home") {
      if (window.Market && typeof window.Market.reload === "function") {
        window.Market.reload();
      }
    } else if (view === "dashboard") {
      if (window.renderDashboard) {
        window.renderDashboard();
      }
    } else if (window.Pages && typeof window.Pages.reload === "function") {
      window.Pages.reload();
    }
    loadHealth();
    showToast(t("sys.refreshToast", { view: VIEW_TITLES[view] || view }));
  }

  /** 绑定侧栏项点击：仪表板能力直连，系统项走 hash 路由（P1-07）。
   * R3-02：改为事件委托 —— 仪表板分组树由 dashboard.js 动态渲染，
   * 初始化时绑定的监听器覆盖不到后插入的节点。
   */
  function bindSidebar() {
    const sidebar = document.querySelector(".sidebar");
    if (!sidebar) {
      return;
    }
    // AR-06（REQ-20261109-01）：侧栏项现在带 role=link + tabindex=0，
    // 但非原生 <a> 的 Enter/Space 不会自动触发 click —— 这里补键盘委托。
    sidebar.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" && event.key !== " ") {
        return;
      }
      const item = event.target.closest(".sidebar-item");
      if (!item) {
        return;
      }
      event.preventDefault();
      item.click();
    });
    sidebar.addEventListener("click", function (event) {
      const item = event.target.closest(".sidebar-item");
      if (!item) {
        return;
      }
      {
        const action = item.getAttribute("data-action");
        if (action === "view") {
          const target = item.getAttribute("data-view-target");
          // W-03：自选分组树节点带 data-group → 进入自选页并预过滤该市场
          const group = item.getAttribute("data-group");
          if (target) {
            // AR-06：点击立即反馈选中态（hash 路由随后会再次统一同步，双保险）
            document.querySelectorAll(".sidebar-item").forEach(function (node) {
              node.classList.toggle("active", node === item);
            });
            window.location.hash = "#/" + target + (group ? "?group=" + encodeURIComponent(group) : "");
          }
          return;
        }
        if (action === "fs") {
          // 新增功能 ⑥：侧栏「财报 / 公告搜索」直达唯一实现页（与 N-05 入口卡同跳）
          window.location.hash = "#/news?fs=1";
          return;
        }
        if (action === "dashboard") {
          const sub = item.getAttribute("data-dashboard");
          // R3-02：点击分组树里的某个仪表板 → 切为当前并重绘
          const dashId = item.getAttribute("data-dash-id");
          if (dashId && window.Dashboard && window.Dashboard.activate) {
            window.Dashboard.activate(dashId);
          }
          document.querySelectorAll(".sidebar-item").forEach(function (node) {
            node.classList.toggle("active", node === item);
          });
          // 先确保仪表板已渲染，再执行新建/打开组件库
          const run = function () {
            if (!window.Dashboard) {
              showToast(t("sys.dashboardNotReady"));
              return;
            }
            if (sub === "new" && window.Dashboard.createDashboard) {
              window.Dashboard.createDashboard();
            } else if (sub === "library" && window.Dashboard.openLibrary) {
              window.Dashboard.openLibrary();
            } else if (!dashId && window.renderDashboard) {
              window.renderDashboard();
            }
          };
          if (window.location.hash.indexOf("#/dashboard") === 0) {
            run();
          } else {
            window.location.hash = "#/dashboard";
            window.setTimeout(run, 260);
          }
        }
      }
    });
  }

  /** 绑定快捷键：Ctrl+M 主题、Ctrl+K 搜索、Esc 关闭抽屉、F5 刷新健康检查。 */
  function bindShortcuts() {
    document.addEventListener("keydown", function (event) {
      if (event.ctrlKey && event.key.toLowerCase() === "m") {
        event.preventDefault();
        toggleTheme();
        return;
      }
      if (event.ctrlKey && event.key.toLowerCase() === "k") {
        event.preventDefault();
        const search = document.getElementById("global-search");
        if (search) {
          search.focus();
        }
        return;
      }
      if (event.key === "Escape") {
        closeCopilot();
        return;
      }
      // F5 不再拦截（REQ-20261109-01 AR-03）：恢复浏览器原生刷新——
      // 劫持刷新会导致前端卡死/版本更新残留旧脚本时失去自救手段；工具栏「刷新」按钮承担数据刷新职责。
    });
  }

  /** 移动端侧栏抽屉（AR-01，REQ-20261109-01）：≤768px 时汉堡唤出 + 遮罩关闭。
   * 桌面宽度（>768）下按钮由 CSS 隐藏、抽屉样式不生效，本函数的所有交互自动退化为无操作；
   * 点击侧栏任一导航项后自动收起抽屉（移动端单手操作闭环）。
   * @returns {void} 无返回值
   */
  function bindMobileDrawer() {
    const sidebar = document.getElementById("sidebar");
    const toggle = document.getElementById("menu-toggle");
    const backdrop = document.getElementById("sb-backdrop");
    if (!sidebar || !toggle || !backdrop) {
      return;
    }

    const isOpen = () => sidebar.classList.contains("open");

    function openDrawer() {
      sidebar.classList.add("open");
      backdrop.hidden = false;
      toggle.setAttribute("aria-expanded", "true");
    }

    function closeDrawer() {
      sidebar.classList.remove("open");
      backdrop.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
    }

    toggle.addEventListener("click", function () {
      if (isOpen()) {
        closeDrawer();
      } else {
        openDrawer();
      }
    });
    backdrop.addEventListener("click", closeDrawer);

    // 侧栏项点击后自动收起（事件委托，覆盖静态与动态创建的导航项）
    sidebar.addEventListener("click", function (event) {
      if (!isOpen()) {
        return;
      }
      const target = event.target;
      const isNavItem = target.closest
        && target.closest(".sidebar-item, .sb-group-h");
      if (isNavItem && target.closest(".sidebar-item")) {
        // 分组标题只折叠分组不关抽屉；二级导航项跳转后收起
        closeDrawer();
      }
    });

    // 桌面宽度恢复时清理抽屉状态，避免残留 open 类影响桌面布局
    window.addEventListener("resize", function () {
      if (window.innerWidth > 768 && isOpen()) {
        closeDrawer();
      }
    });
  }

  /** 首页引导卡（AR-10，REQ-20261109-01）：K线分时/搜索两张引导卡改为
   * 「可关闭的一次性提示」——localStorage 记忆关闭意图，此后不再显示；
   * 首次访问默认显示（与原行为一致，不丢失引导价值）。
   * @returns {void} 无返回值
   */
  function bindHomeHints() {
    const cards = [
      { card: "mk-kline-card", dismiss: "mk-kline-dismiss", key: "openbb-hint-kline-v1" },
      { card: "mk-search-card", dismiss: "mk-search-dismiss", key: "openbb-hint-search-v1" },
    ];
    cards.forEach(function (entry) {
      const card = document.getElementById(entry.card);
      const btn = document.getElementById(entry.dismiss);
      if (!card || !btn) {
        return;
      }
      let dismissed = false;
      try {
        dismissed = window.localStorage.getItem(entry.key) === "1";
      } catch (error) {
        dismissed = false;  // 隐私模式下每次都显示
      }
      card.hidden = dismissed;
      btn.addEventListener("click", function () {
        card.hidden = true;
        try {
          window.localStorage.setItem(entry.key, "1");
        } catch (error) {
          // 隐私模式下仅本次会话关闭
        }
      });
    });
  }

  /** 页面初始化。 */
  function init() {
    // REQ-20261111-01：默认皮肤改为 V6；localStorage 里的旧值（light/dark）仍被尊重，
    // 非法值由 applyTheme 内部的 normalizeTheme 回落 v6。
    let savedTheme = DEFAULT_THEME;
    try {
      savedTheme = window.localStorage.getItem("openbb-theme") || DEFAULT_THEME;
    } catch (error) {
      savedTheme = DEFAULT_THEME;
    }
    applyTheme(savedTheme);

    // R3-13 S-02：恢复持久化的字号档位（sm/md/lg，缺省 md）
    try {
      const savedFont = window.localStorage.getItem("openbb-font") || "md";
      document.documentElement.setAttribute("data-font", savedFont);
    } catch (error) {
      // 隐私模式下忽略持久化，使用默认字号
    }

    bindNavigation();
    bindSidebar();
    bindShortcuts();
    bindMobileDrawer();
    bindHomeHints();

    const themeButton = document.getElementById("theme-toggle");
    if (themeButton) {
      themeButton.addEventListener("click", toggleTheme);
    }

    // REQ-20261111-01 SK-02：三皮肤切换器（V6 / 现行 / 砚）。
    // 与 #theme-toggle 共用 applyTheme 同一实现，不重复主题逻辑。
    const skinSwitch = document.getElementById("skin-switch");
    if (skinSwitch) {
      skinSwitch.addEventListener("click", function (event) {
        const button = event.target.closest("button[data-skin]");
        if (!button) {
          return;
        }
        applyTheme(button.getAttribute("data-skin"));
      });
    }

    const copilotButton = document.getElementById("copilot-toggle");
    if (copilotButton) {
      copilotButton.addEventListener("click", openCopilot);
    }

    const copilotClose = document.getElementById("copilot-close");
    if (copilotClose) {
      copilotClose.addEventListener("click", closeCopilot);
    }

    const refreshButton = document.getElementById("refresh-button");
    if (refreshButton) {
      refreshButton.addEventListener("click", refreshCurrentView);
    }

    // 第七轮 M6：顶栏铃铛 → 跳转提醒中心（事件列表在 #/alerts 页内渲染）
    const bell = document.getElementById("alerts-bell");
    if (bell) {
      bell.addEventListener("click", function () {
        window.location.hash = "#/alerts";
      });
    }

    const search = document.getElementById("global-search");
    if (search) {
      search.addEventListener("keydown", function (event) {
        if (event.key !== "Enter") {
          return;
        }
        const keyword = search.value.trim();
        if (!keyword) {
          showToast(t("sys.inputHint"));
          return;
        }
        if (window.UI && typeof window.UI.openPalette === "function") {
          // 复用命令面板的同一套过滤与键盘导航，并带上关键词（P1-08）
          window.UI.openPalette(keyword);
        } else {
          showToast(t("sys.paletteNotReady"));
        }
      });
    }

    loadHealth();
  }

  // R6-13：导出导航高亮同步给 pages.js 路由复用（单一实现，不重复维护映射表）
  /** 取视图的显示标题（VIEW_TITLES 唯一实现，pages.js 不再自维护映射表）。
   * @param {string} view 视图标识
   * @returns {string} 标题文案；未登记时回退空串（由调用方决定兜底）
   */
  function viewTitle(view) {
    const text = VIEW_TITLES[view];
    return text === undefined ? "" : text;
  }

  window.App = {
    syncNavActive: syncNavActive,
    VIEW_GROUP: VIEW_GROUP,
    VIEW_TITLES: VIEW_TITLES,
    viewTitle: viewTitle,
    // REQ-20261111-01：换肤与图表重排对外暴露（三皮肤切换器与验收探针共用）
    applyTheme: applyTheme,
    toggleTheme: toggleTheme,
    resizeCharts: resizeCharts,
    THEMES: THEMES,
    DEFAULT_THEME: DEFAULT_THEME,
    normalizeTheme: normalizeTheme
  };

  // defer 脚本执行时 DOM 已解析完成，可直接初始化
  init();

  // 首屏兜底：pages.js 先于本文件加载，其首次 route() 执行时 window.App 尚未定义，
  // 标题会停留在 HTML 默认值（「首页 · 市场总览」）。此处补一次同步。
  (function syncTitleOnBoot() {
    try {
      const raw = (location.hash || "").replace(/^#\/?/, "").split("?")[0];
      const view = raw || "home";
      const node = document.getElementById("page-title");
      const text = viewTitle(view);
      if (node && text) {
        node.textContent = text;
      }
    } catch (error) {
      // 标题同步失败不影响主流程
    }
  })();
})();
