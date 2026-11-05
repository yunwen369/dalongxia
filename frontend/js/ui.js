/* =============================================================
 * OpenBB 中文投研工作台 · 全局交互（阶段 6）
 * 职责：Toast、模态框、Ctrl+K 命令面板（切视图 / 加组件 / 清缓存 / 切主题）。
 * 依赖：I18N.t（文案）、/api/widgets（组件列表）、/api/cache/clear（清缓存）。
 * 约束：不新增配色；所有文案走 i18n；错误提示为中文且含下一步动作。
 * ============================================================= */

(function () {
  "use strict";

  // P1-13：共享工具别名（el / t / toast 原本各 IIFE 重复定义于此）
  var t = window.Utils.t;
  var el = window.Utils.el;
  var toast = window.Utils.toast;

  /**
   * 模态框.
   * options: {title, message, input:bool, defaultValue, confirmText, onConfirm(value), onCancel}
   * @param {*} options 可选项集合
   */
  function modal(options) {
    var overlay = el("div", "ui-overlay");
    var box = el("div", "ui-modal");
    box.appendChild(el("div", "ui-modal-title", options.title || ""));
    if (options.message) {
      box.appendChild(el("div", "ui-modal-message", options.message));
    }
    var input = null;
    var fieldInputs = null;
    if (options.fields && options.fields.length) {
      // 多字段模式（如卡片「改参数」：标的 + 周期）；onConfirm 收到 {key: value}
      fieldInputs = {};
      options.fields.forEach(function (field) {
        var row = el("div", "ui-modal-field");
        row.appendChild(el("label", "ui-modal-label", field.label || field.key));
        var box2 = document.createElement("input");
        box2.className = "ui-modal-input";
        box2.value = field.value === undefined || field.value === null ? "" : String(field.value);
        box2.placeholder = field.placeholder || "";
        if (field.mono) {
          box2.style.fontFamily = "var(--font-mono)";
        }
        row.appendChild(box2);
        box.appendChild(row);
        fieldInputs[field.key] = box2;
      });
    } else if (options.input) {
      input = document.createElement("input");
      input.className = "ui-modal-input";
      input.value = options.defaultValue || "";
      box.appendChild(input);
    }
    var actions = el("div", "ui-modal-actions");
    var cancel = el("button", "pg-btn", options.cancelText || t("action.cancel"));
    cancel.type = "button";
    // danger=true 时确认按钮转为危险样式（删除/恢复出厂等不可逆操作，R3-13）
    var confirm = el("button", "pg-btn" + (options.danger ? " danger" : " primary"),
      options.confirmText || t("action.confirm"));
    confirm.type = "button";
    actions.appendChild(cancel);
    actions.appendChild(confirm);
    box.appendChild(actions);
    overlay.appendChild(box);
    document.body.appendChild(overlay);
    if (input) {
      input.focus();
      input.select();
    } else if (fieldInputs) {
      var firstKey = Object.keys(fieldInputs)[0];
      if (firstKey) {
        fieldInputs[firstKey].focus();
        fieldInputs[firstKey].select();
      }
    }

    /** 关闭浮层：移除节点并解绑键盘监听（防内存泄漏）。 */
    function close() {
      overlay.remove();
      document.removeEventListener("keydown", onKey);
    }
    /** Esc 关闭浮层（与全局快捷键互不干扰）。
     * @param {Event} event 事件对象
     */
    function onKey(event) {
      if (event.key === "Escape") {
        close();
        if (options.onCancel) {
          options.onCancel();
        }
      }
      if (event.key === "Enter" && input) {
        event.preventDefault();
        confirm.click();
      }
    }
    cancel.addEventListener("click", function () {
      close();
      if (options.onCancel) {
        options.onCancel();
      }
    });
    confirm.addEventListener("click", function () {
      var value;
      if (fieldInputs) {
        value = {};
        Object.keys(fieldInputs).forEach(function (key) {
          value[key] = fieldInputs[key].value.trim();
        });
      } else {
        value = input ? input.value.trim() : true;
      }
      close();
      if (options.onConfirm) {
        options.onConfirm(value);
      }
    });
    document.addEventListener("keydown", onKey);
  }

  // =============================================================
  // Ctrl+K 命令面板
  // =============================================================
  var widgetCache = null;

  /** 视图命令（含快捷键说明）。
   * @returns {Array} 处理结果
   */
  function viewCommands() {
    return [
      { id: "view:home", group: "palette.views", label: t("nav.home"), run: function () { location.hash = "#/home"; } },
      { id: "view:dashboard", group: "palette.views", label: t("nav.dashboard"), run: function () { location.hash = "#/dashboard"; } },
      { id: "view:dashboard-manage", group: "palette.views", label: t("nav.dashboardManage"), run: function () { location.hash = "#/dashboard-manage"; } },
      { id: "view:widget-library", group: "palette.views", label: t("sidebar.library"), run: function () { location.hash = "#/widget-library"; } },
      { id: "view:charting", group: "palette.views", label: t("nav.charting"), run: function () { location.hash = "#/charting"; } },
      { id: "view:sectors", group: "palette.views", label: t("nav.sectors"), run: function () { location.hash = "#/sectors"; } },
      { id: "view:stock", group: "palette.views", label: t("nav.stock"), run: function () { location.hash = "#/stock?symbol=688012&market=a"; } },
      { id: "view:sector-val", group: "palette.views", label: t("nav.sectorVal"), run: function () { location.hash = "#/sector-val?board=半导体&market=a"; } },
      { id: "view:watchlist", group: "palette.views", label: t("nav.watchlist"), run: function () { location.hash = "#/watchlist"; } },
      { id: "view:lhb", group: "palette.views", label: t("nav.lhb"), run: function () { location.hash = "#/lhb"; } },
      { id: "view:news", group: "palette.views", label: t("nav.news"), run: function () { location.hash = "#/news"; } },
      { id: "view:fund", group: "palette.views", label: t("nav.fund"), run: function () { location.hash = "#/fund?code=017811"; } },
      { id: "view:portfolio", group: "palette.views", label: t("nav.portfolio"), run: function () { location.hash = "#/portfolio"; } },
      { id: "view:yjb", group: "palette.views", label: t("nav.yjb"), run: function () { location.hash = "#/yjb"; } },
      { id: "view:connectors", group: "palette.views", label: t("nav.connectors"), run: function () { location.hash = "#/connectors"; } },
      { id: "view:settings", group: "palette.views", label: t("nav.sysSettings"), run: function () { location.hash = "#/settings"; } },
      // 第十轮 P1 新 IA：7 个新页直达（GL-01）
      { id: "view:today", group: "palette.views", label: t("nav.today"), run: function () { location.hash = "#/today"; } },
      { id: "view:market", group: "palette.views", label: t("nav.marketOverview"), run: function () { location.hash = "#/market"; } },
      { id: "view:fin", group: "palette.views", label: t("nav.finSummary"), run: function () { location.hash = "#/fin"; } },
      { id: "view:ann", group: "palette.views", label: t("nav.annLib"), run: function () { location.hash = "#/ann"; } },
      { id: "view:mgr", group: "palette.views", label: t("nav.mgr"), run: function () { location.hash = "#/mgr"; } },
      { id: "view:tools", group: "palette.views", label: t("nav.tools"), run: function () { location.hash = "#/tools"; } },
      { id: "view:cand", group: "palette.views", label: t("nav.cand"), run: function () { location.hash = "#/cand"; } }
    ];
  }

  /** 操作命令。
   * @returns {Array} 处理结果
   */
  function actionCommands() {
    return [
      {
        id: "action:cache", group: "palette.actions", label: t("palette.clearCache"),
        /** 执行清缓存：POST /api/cache/clear，成功/失败均给出中文提示。 */
        run: function () {
          fetch("/api/cache/clear", { method: "POST" })
            .then(function (response) { return response.json(); })
            .then(function (body) {
              toast(body.error ? t("toast.cacheFailed") : t("toast.cacheCleared"));
            })
            .catch(function () { toast(t("toast.cacheFailed")); });
        }
      },
      {
        id: "action:theme", group: "palette.actions", label: t("action.themeToggle"),
        /** 执行主题切换：dark ↔ light，并写入 localStorage 持久化。 */
        run: function () {
          var current = document.documentElement.getAttribute("data-theme") || "dark";
          var next = current === "dark" ? "light" : "dark";
          document.documentElement.setAttribute("data-theme", next);
          try { localStorage.setItem("openbb-theme", next); } catch (error) { /* 隐私模式忽略 */ }
          toast(next === "dark" ? t("toast.themeDark") : t("toast.themeLight"));
        }
      }
    ];
  }

  /** 搜索命令：把顶栏关键词交给真实智能联想，命中则跳个股页（P1-08）。
   * @param {string} keyword 顶栏搜索框关键词
   * @returns {Array} 命令列表（关键词为空时返回空数组）
   */
  // ---------- 跨实体检索（GL-01）：板块本地索引 + 股票/基金远程联想 ----------
  var sectorIndex = null;
  var sectorIndexAt = 0;

  /** 板块名录索引（10 分钟缓存；失败不阻塞面板，返回空表）。 */
  function ensureSectorIndex(callback) {
    if (sectorIndex && Date.now() - sectorIndexAt < 600000) {
      callback(sectorIndex);
      return;
    }
    Promise.all([
      fetch("/api/sector/industry", { cache: "no-store" }).then(function (r) { return r.json(); }).catch(function () { return null; }),
      fetch("/api/sector/concept", { cache: "no-store" }).then(function (r) { return r.json(); }).catch(function () { return null; })
    ]).then(function (res) {
      var list = [];
      [res[0], res[1]].forEach(function (body, idx) {
        var items = (body && body.data && (body.data.items || body.data.list)) || [];
        items.forEach(function (item) {
          if (item && item.name) {
            list.push({ name: item.name, kind: idx === 0 ? "industry" : "concept" });
          }
        });
      });
      sectorIndex = list;
      sectorIndexAt = Date.now();
      callback(list);
    }).catch(function () { callback([]); });
  }

  /** 匹配分：完全相等 100 / 前缀 80 / 包含 60，用于「精确名称优先」排序。 */
  function matchScore(label, query) {
    var text = String(label || "").toLowerCase();
    var q = String(query || "").toLowerCase();
    if (!q) {
      return 0;
    }
    if (text === q) {
      return 100;
    }
    if (text.indexOf(q) === 0) {
      return 80;
    }
    if (text.indexOf(q) >= 0) {
      return 60;
    }
    return 0;
  }

  /** 按关键词生成跨实体命令：页面直达 + 板块 + 股票 + 基金。 */
  function entityCommands(keyword, callback) {
    /** 排序后回调：精确匹配优先，同分按 页面 > 股票 > 板块 > 基金。 */
    function finish(list) {
      list.forEach(function (item) {
        item._score = matchScore(item.label, keyword);
      });
      list.sort(function (a, b) {
        return (b._score - a._score) || ((b._type || 0) - (a._type || 0));
      });
      callback(list);
    }
    var query = (keyword || "").trim();
    if (query.length < 1) {
      callback([]);
      return;
    }
    var lower = query.toLowerCase();
    var out = [];
    viewCommands().forEach(function (cmd) {
      if (cmd.label.toLowerCase().indexOf(lower) >= 0) {
        cmd._type = 4;
        out.push(cmd);
      }
    });
    ensureSectorIndex(function (list) {
      list.filter(function (item) { return item.name.indexOf(query) >= 0; })
        .slice(0, 4)
        .forEach(function (item) {
          out.push({
            id: "sector:" + item.kind + ":" + item.name,
            group: "palette.sectors",
            label: item.name,
            _type: 2,
            /** 板块详情联动选中（B1：搜「白酒」落板块页并选中）。 */
            run: function () {
              location.hash = "#/sector-val?board=" + encodeURIComponent(item.name) + "&market=a";
            }
          });
        });
      Promise.all([
        fetch("/api/market/search?limit=6&q=" + encodeURIComponent(query), { cache: "no-store" })
          .then(function (r) { return r.json(); }).catch(function () { return null; }),
        fetch("/api/fund/search?limit=5&q=" + encodeURIComponent(query), { cache: "no-store" })
          .then(function (r) { return r.json(); }).catch(function () { return null; })
      ]).then(function (res) {
        var stocks = (res[0] && res[0].data && (res[0].data.items || res[0].data.list)) || [];
        stocks.slice(0, 6).forEach(function (item) {
          var symbol = item.symbol || item.code || "";
          if (!symbol) {
            return;
          }
          out.push({
            id: "stock:" + symbol,
            group: "palette.stocks",
            label: (item.name || "") + " " + symbol,
            _type: 3,
            run: function () {
              location.hash = "#/stock?symbol=" + encodeURIComponent(symbol) +
                "&market=" + encodeURIComponent(item.market || "a");
            }
          });
        });
        var funds = (res[1] && res[1].data && (res[1].data.items || res[1].data.list)) || [];
        funds.slice(0, 5).forEach(function (item) {
          if (!item || !item.code) {
            return;
          }
          out.push({
            id: "fund:" + item.code,
            group: "palette.funds",
            label: (item.name || "") + " " + item.code,
            _type: 1,
            run: function () {
              location.hash = "#/fund?code=" + encodeURIComponent(item.code);
            }
          });
        });
        finish(out);
      }).catch(function () { finish(out); });
    });
  }

  function searchCommand(keyword) {
    var query = (keyword || "").trim();
    if (!query) {
      return [];
    }
    return [{
      id: "search:" + query,
      group: "palette.search",
      label: t("palette.searchStock", { q: query }),
      /** 执行搜索：取联想首条结果跳转个股详情；无结果给中文提示。 */
      run: function () {
        fetch("/api/market/search?limit=5&q=" + encodeURIComponent(query), { cache: "no-store" })
          .then(function (response) { return response.json(); })
          .then(function (body) {
            var items = (body.data && body.data.items) || [];
            if (!items.length) {
              toast(t("ui.searchNotFound", { q: query }));
              return;
            }
            var first = items[0];
            var market = first.market || "a";
            var symbol = first.symbol || first.code || "";
            location.hash = "#/stock?symbol=" + encodeURIComponent(symbol) + "&market=" + encodeURIComponent(market);
          })
          .catch(function () { toast(t("error.unknown")); });
      }
    }];
  }

  /** 组件命令：按 /api/widgets 注册表动态生成。
   * @param {*} callback 回调函数
   */
  function widgetCommands(callback) {
    if (widgetCache) {
      callback(buildWidgetCommands(widgetCache));
      return;
    }
    fetch("/api/widgets", { cache: "no-store" })
      .then(function (response) { return response.json(); })
      .then(function (body) {
        widgetCache = (body.data && body.data.items) || [];
        callback(buildWidgetCommands(widgetCache));
      })
      .catch(function () { callback([]); });
  }

  /** 把注册表项转成命令。
   * @param {Array} items 记录列表
   * @returns {*} 处理结果
   */
  function buildWidgetCommands(items) {
    return items.map(function (item) {
      return {
        id: "widget:" + item.id,
        group: "palette.widgets",
        label: item.id + " " + item.title,
        /** 执行添加组件：调用仪表板 API 并跳转到 #/dashboard。 */
        run: function () {
          if (window.Dashboard && window.Dashboard.addWidgetById) {
            // 当前在个股页时自动带上该标的（用户自选，避免硬编码默认标的）
            var symbolMatch = /symbol=([^&]+)/.exec(location.hash || "");
            var marketMatch = /market=([^&]+)/.exec(location.hash || "");
            var params = {};
            if (symbolMatch) {
              params.symbol = decodeURIComponent(symbolMatch[1]);
              params.market = marketMatch ? decodeURIComponent(marketMatch[1]) : "a";
            }
            window.Dashboard.addWidgetById(item.id, params);
            location.hash = "#/dashboard";
            toast(t("dashboard.added", { title: item.title }));
          } else {
            toast(t("dashboard.notReady"));
          }
        }
      };
    });
  }

  var paletteNode = null;
  var paletteState = { items: [], dynamic: [], index: 0 };

  /** 渲染候选列表。
   * @param {*} input 输入框节点
   * @returns {*} 处理结果
   */
  function renderPaletteList(input) {
    if (!paletteNode) {
      return;
    }
    var keyword = (input || "").trim().toLowerCase();
    var list = paletteNode.querySelector(".ui-palette-list");
    list.innerHTML = "";
    var all = paletteState.items.concat(paletteState.dynamic || []);
    var filtered = all.filter(function (item) {
      return !keyword || (item.label + " " + item.group).toLowerCase().indexOf(keyword) >= 0;
    });
    if (!filtered.length) {
      list.appendChild(el("div", "ui-palette-empty", t("palette.noResult")));
      return;
    }
    paletteState.index = Math.min(paletteState.index, filtered.length - 1);
    filtered.forEach(function (item, position) {
      var row = el("div", "ui-palette-item" + (position === paletteState.index ? " active" : ""));
      row.appendChild(el("span", "ui-palette-group", t(item.group)));
      row.appendChild(el("span", "ui-palette-label", item.label));
      row.addEventListener("click", function () {
        closePalette();
        item.run();
      });
      list.appendChild(row);
    });
    paletteNode._filtered = filtered;
  }

  /** 关闭命令面板。 */
  function closePalette() {
    if (paletteNode) {
      paletteNode.remove();
      paletteNode = null;
    }
  }

  /** 打开命令面板。
   * @param {string} initialQuery 可选的初始关键词（顶栏搜索回车时传入）
   */
  function openPalette(initialQuery) {
    if (paletteNode) {
      closePalette();
      return;
    }
    paletteNode = el("div", "ui-overlay ui-palette-overlay");
    var box = el("div", "ui-palette");
    var input = document.createElement("input");
    input.className = "ui-palette-input";
    input.placeholder = t("palette.placeholder");
    box.appendChild(input);
    var list = el("div", "ui-palette-list");
    box.appendChild(list);
    box.appendChild(el("div", "ui-palette-hint", t("palette.hint")));
    paletteNode.appendChild(box);
    document.body.appendChild(paletteNode);

    var keyword = typeof initialQuery === "string" ? initialQuery.trim() : "";
    if (keyword) {
      input.value = keyword;
    }
    paletteState.items = viewCommands().concat(actionCommands());
    paletteState.dynamic = [];
    paletteState.index = 0;
    renderPaletteList(input.value);
    if (keyword) {
      refreshEntities();
    }
    widgetCommands(function (commands) {
      paletteState.items = paletteState.items.concat(commands);
      renderPaletteList(input.value);
    });

    var entityTimer = null;
    /** 输入即本地过滤；320ms 后补远程实体结果（避免逐字打满接口）。 */
    function refreshEntities() {
      window.clearTimeout(entityTimer);
      entityTimer = window.setTimeout(function () {
        entityCommands(input.value, function (commands) {
          paletteState.dynamic = commands;
          paletteState.index = 0;
          renderPaletteList(input.value);
        });
      }, 320);
    }
    input.addEventListener("input", function () {
      paletteState.index = 0;
      renderPaletteList(input.value);
      refreshEntities();
    });
    input.addEventListener("keydown", function (event) {
      var filtered = paletteNode && paletteNode._filtered ? paletteNode._filtered : [];
      if (event.key === "ArrowDown") {
        event.preventDefault();
        paletteState.index = Math.min(paletteState.index + 1, filtered.length - 1);
        renderPaletteList(input.value);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        paletteState.index = Math.max(paletteState.index - 1, 0);
        renderPaletteList(input.value);
      } else if (event.key === "Enter") {
        event.preventDefault();
        var target = filtered[paletteState.index];
        if (target) {
          closePalette();
          target.run();
        }
      } else if (event.key === "Escape") {
        closePalette();
      }
    });
    paletteNode.addEventListener("click", function (event) {
      if (event.target === paletteNode) {
        closePalette();
      }
    });
    input.focus();
  }

  /** 绑定全局快捷键：Ctrl+K 面板、Esc 关闭。 */
  function bindShortcuts() {
    document.addEventListener("keydown", function (event) {
      if (event.ctrlKey && event.key.toLowerCase() === "k") {
        event.preventDefault();
        openPalette();
      }
      if (event.key === "Escape") {
        closePalette();
      }
    });
  }

  window.UI = { toast: toast, modal: modal, openPalette: openPalette, closePalette: closePalette };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindShortcuts);
  } else {
    bindShortcuts();
  }
})();
