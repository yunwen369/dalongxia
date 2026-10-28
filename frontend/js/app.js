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
  const VIEW_TITLES = {
    home: t("nav.homeTitle"),
    charting: t("page.charting"),
    news: t("page.news"),
    sectors: t("page.sectors"),
    stock: t("page.stock"),
    "sector-val": t("page.sectorVal"),
    fund: t("page.fund"),
    portfolio: t("page.portfolio"),
    yjb: t("page.yjb"),
    connectors: t("page.connectors"),
    settings: t("page.settings")
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

  /** 应用主题并持久化到 localStorage。
   * @param {Object} theme 主题色对象
   */
  function applyTheme(theme) {
    const nextTheme = theme === "light" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", nextTheme);
    try {
      window.localStorage.setItem("openbb-theme", nextTheme);
    } catch (error) {
      // 隐私模式下 localStorage 不可用时忽略，主题仍在当前页面生效
    }
  }

  /** 切换深浅主题（对应 Ctrl+M）。 */
  function toggleTheme() {
    const current = document.documentElement.getAttribute("data-theme") || "dark";
    applyTheme(current === "dark" ? "light" : "dark");
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

  /** 视图 → 顶栏一级项分组映射（R6-13：顶栏只放 8 个一级项，二级视图高亮其所属一级）。
   * 未登记的视图（如 dashboard-manage / widget-library）按前缀兜底。
   */
  const VIEW_GROUP = {
    home: "",
    dashboard: "dashboard",
    "dashboard-manage": "dashboard",
    "widget-library": "dashboard",
    watchlist: "watchlist",
    news: "news",
    sectors: "market",
    stock: "market",
    charting: "market",
    "sector-val": "market",
    lhb: "money",
    portfolio: "portfolio",
    fund: "portfolio",
    yjb: "yjb",
    connectors: "system",
    settings: "system"
  };

  /** 面包屑层级表（R6-13 / F6-13）：视图 → [[名称, 跳转视图或 null], ...]。
   * 最后一层为当前页（不可点）；前面各层点击回对应一级/二级页。
   */
  const CRUMBS = {
    home: [],
    dashboard: [["nav.dashboard", "dashboard"], ["dashboard.mine", null]],
    "dashboard-manage": [["nav.dashboard", "dashboard"], ["nav.dashboardManage", null]],
    "widget-library": [["nav.dashboard", "dashboard"], ["sidebar.library", null]],
    watchlist: [["nav.watchlist", "watchlist"], ["sidebar.myWatchlist", null]],
    news: [["nav.newsGroup", "news"], ["nav.news", null]],
    sectors: [["nav.market", "sectors"], ["nav.sectors", "sectors"], ["nav.sectorsIndustry", null]],
    stock: [["nav.market", "sectors"], ["nav.stock", null]],
    charting: [["nav.market", "sectors"], ["nav.charting", null]],
    "sector-val": [["nav.market", "sectors"], ["nav.sectorVal", null]],
    lhb: [["nav.money", "lhb"], ["nav.lhb", null]],
    portfolio: [["nav.portfolioGroup", "portfolio"], ["nav.portfolio", null]],
    fund: [["nav.portfolioGroup", "portfolio"], ["nav.fund", null]],
    yjb: [["nav.yjb", "yjb"], ["nav.yjbHoldings", null]],
    connectors: [["nav.system", "settings"], ["nav.connectors", null]],
    settings: [["nav.system", "settings"], ["nav.settings", null]]
  };

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

  /** 按当前视图高亮顶栏一级项（同组多个二级视图共用同一个高亮）。
   * @param {string} view 当前视图标识
   * @returns {void} 无返回值
   */
  function syncNavActive(view) {
    const nav = document.getElementById("main-nav");
    if (!nav) {
      return;
    }
    const group = VIEW_GROUP[view] !== undefined ? VIEW_GROUP[view] : "";
    nav.querySelectorAll(".nav-item").forEach(function (item) {
      const itemGroup = item.getAttribute("data-group") || "";
      item.classList.toggle("active", !!group && itemGroup === group);
    });
    renderCrumb(view);
  }

  /** 绑定主菜单点击。 */
  function bindNavigation() {
    const nav = document.getElementById("main-nav");
    const title = document.getElementById("page-title");
    if (!nav) {
      return;
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
      if (event.key === "F5") {
        // 拦截浏览器刷新，改为刷新当前视图数据（P2-03）
        event.preventDefault();
        refreshCurrentView();
      }
    });
  }

  /** 页面初始化。 */
  function init() {
    let savedTheme = "dark";
    try {
      savedTheme = window.localStorage.getItem("openbb-theme") || "dark";
    } catch (error) {
      savedTheme = "dark";
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

    const themeButton = document.getElementById("theme-toggle");
    if (themeButton) {
      themeButton.addEventListener("click", toggleTheme);
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
  window.App = {
    syncNavActive: syncNavActive,
    VIEW_GROUP: VIEW_GROUP
  };

  // defer 脚本执行时 DOM 已解析完成，可直接初始化
  init();
})();
