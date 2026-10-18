/* =============================================================
 * OpenBB 中文投研工作台 · 前端入口脚本（阶段 1）
 * 职责：四区占位交互、主题切换、Copilot 抽屉、健康检查渲染。
 * 依赖接口：GET /api/health（唯一调用的后端接口）。
 * 四态处理：本文件只处理健康检查的「加载 / 成功 / 失败」，
 *          业务数据的四态渲染将在后续阶段由 widgets.js 统一实现。
 * ============================================================= */

(function () {
  "use strict";

  // 页面标题映射：与主菜单 data-view 一一对应
  const VIEW_TITLES = {
    home: "首页 · 市场总览",
    charting: "图表分析",
    news: "新闻资讯",
    sectors: "板块分析",
    stock: "个股详情",
    "sector-val": "单板块估值",
    fund: "场外基金",
    portfolio: "持仓管理",
    yjb: "养基宝绑定",
    connectors: "数据连接器",
    settings: "设置"
  };

  let toastTimer = null;

  /** 显示 3 秒自动消失的 Toast 提示。 */
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
    }, 3000);
  }

  /** 应用主题并持久化到 localStorage。 */
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

  /** 渲染数据源状态列表（只使用 textContent，避免 HTML 注入）。 */
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
      empty.textContent = "暂无数据源状态";
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
      status.textContent = meta.status || "未知";

      item.appendChild(name);
      item.appendChild(status);
      list.appendChild(item);
    });
  }

  /** 渲染运行环境信息。 */
  function renderSystemInfo(data) {
    const container = document.getElementById("system-info");
    if (!container) {
      return;
    }
    const app = data.app || {};
    const cache = data.cache || {};
    const ai = data.ai || {};
    const lines = [
      "应用：" + (app.name || "未知") + " v" + (app.version || "—"),
      "构建：" + (data.build || "—"),
      "Python：" + (data.python || "—"),
      "监听：" + (data.host || "—") + ":" + (data.port || "—"),
      "缓存：内存 " + (cache.memory || 0) + " 条 / 磁盘 " + (cache.disk || 0) + " 个",
      "AI 配置：" + (ai.configured ? "已配置（" + (ai.source || "未知来源") + "）" : "未配置"),
      "服务器时间：" + (data.server_time || "—")
    ];
    container.textContent = lines.join("\n");
    container.style.whiteSpace = "pre-line";
  }

  /** 设置健康徽章状态。 */
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
    setHealthBadge("健康检查中", "");
    try {
      const response = await fetch("/api/health", { cache: "no-store" });
      if (!response.ok) {
        throw new Error("HTTP " + response.status);
      }
      const body = await response.json();
      if (body.error) {
        throw new Error(body.error);
      }
      const data = body.data || {};
      const buildBadge = document.getElementById("build-badge");
      if (buildBadge) {
        buildBadge.textContent = "构建 " + (data.build || "—");
      }
      setHealthBadge("服务正常", "ok");
      renderSources(data.datasources);
      renderSystemInfo(data);
    } catch (error) {
      // 失败时只展示中文提示，不把英文异常原文暴露给用户
      setHealthBadge("服务异常", "error");
      renderSources({});
      const container = document.getElementById("system-info");
      if (container) {
        container.textContent = "健康检查失败：请确认服务已在 127.0.0.1:6900 启动，然后点击「刷新」重试。";
      }
      showToast("健康检查失败，请确认后端服务已启动。");
    }
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
      nav.querySelectorAll(".nav-item").forEach(function (item) {
        item.classList.remove("active");
      });
      button.classList.add("active");
      const view = button.getAttribute("data-view");
      if (title) {
        title.textContent = VIEW_TITLES[view] || button.textContent;
      }
      if (view !== "home") {
        showToast("「" + button.textContent + "」视图将在后续阶段接入。");
      }
    });
  }

  /** 绑定侧栏占位项点击。 */
  function bindSidebar() {
    document.querySelectorAll(".sidebar-item").forEach(function (item) {
      item.addEventListener("click", function () {
        showToast("该功能将在后续阶段接入。");
      });
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
        // 拦截浏览器刷新，改为刷新当前数据（本阶段只有健康检查）
        event.preventDefault();
        loadHealth();
        showToast("已刷新健康检查。");
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
      refreshButton.addEventListener("click", loadHealth);
    }

    const search = document.getElementById("global-search");
    if (search) {
      search.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
          showToast("全局搜索将在阶段 2 接入。");
        }
      });
    }

    loadHealth();
  }

  // defer 脚本执行时 DOM 已解析完成，可直接初始化
  init();
})();
