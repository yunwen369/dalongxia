/* =============================================================
 * OpenBB 中文投研工作台 · core/grid.js
 * 12 列布局引擎（GL-04）：拖拽排序 / 宽度档位 4·6·12 / 持久化 / 重置 / 双击快切。
 *
 * 设计依据：DS-R10-v2.1 §6「12 列栅格（v5.1 安全版口径）：默认每卡 span 12（零风险基线）；
 * 布局模式=跨列档位吸附 4/6/12，仅顶层直接子卡可拖，嵌套卡免疫；持久化键 `页id#序号`；
 * 顶栏 ↺ 重置；双击标题快切全宽/半宽（带标题卡生效）；改宽自动重绘图表」。
 * 验收 B3：句柄全页覆盖（仅顶层卡）/ 档位吸附 / 持久化键 / ↺ 清 LS / 双击仅带标题卡 / 改宽后重绘。
 *
 * 关键约束
 * --------
 * 1. **仅顶层卡可拖**：父链上再没有 `.pg-card` 的才算顶层，嵌套卡（如云图内的子块）免疫；
 * 2. 只改样式与顺序，**不动各页渲染逻辑**；路由重建 DOM 后由 applyLayout 重放；
 * 3. 持久化键 `页面id#序号`，与 dashboard.js 的 `openbb-dashboards-v1` 命名空间隔离；
 * 4. 宽度只认 4/6/12 三档（吸附），非法值回落 12；
 * 5. 图表重绘：容器宽度变化后调用 echarts resize（复用 Pages.charts 登记表）。
 * ============================================================= */
(function () {
  "use strict";

  var STORE_KEY = "openbb-grid-v1";
  var MODE_KEY = "openbb-grid-mode";
  var SPANS = [4, 6, 12];

  var layoutMode = false;
  var dragState = null;
  var host = null;      // 当前布局容器（.page-view:not([hidden])）
  var pageId = "";

  /** 读取全部布局配置（损坏时回落空对象）。 */
  function loadAll() {
    try {
      return JSON.parse(window.localStorage.getItem(STORE_KEY) || "{}") || {};
    } catch (error) {
      return {};
    }
  }

  /** 写入全部布局配置。 */
  function saveAll(data) {
    try {
      window.localStorage.setItem(STORE_KEY, JSON.stringify(data));
    } catch (error) {
      /* 忽略：布局非关键路径 */
    }
  }

  /** 当前页布局：{"页id#序号": {span}}，顺序即数组下标。 */
  function pageLayout() {
    var all = loadAll();
    return all[pageId] || null;
  }

  /** 保存当前页的卡顺序与宽度。 */
  function persist() {
    if (!host || !pageId) {
      return;
    }
    var all = loadAll();
    var cards = topCards(host);
    all[pageId] = cards.map(function (card) {
      var span = Number(card.getAttribute("data-span") || 12);
      return { key: card.getAttribute("data-card-key") || "", span: SPANS.indexOf(span) >= 0 ? span : 12 };
    });
    saveAll(all);
  }

  /** 顶层卡：父链上无 .pg-card 的所有卡（嵌套卡免疫）。 */
  function topCards(scope) {
    return Array.prototype.filter.call(scope.querySelectorAll(".pg-card"), function (card) {
      return !(card.parentElement && card.parentElement.closest(".pg-card"));
    });
  }

  /** 宽度落地：写 CSS 变量 + data 属性，并重绘该卡内图表。 */
  function applySpan(card, span) {
    var value = SPANS.indexOf(span) >= 0 ? span : 12;
    card.setAttribute("data-span", String(value));
    card.style.setProperty("--card-span", String(value));
    // 改宽后重绘图表：宽高变化时 echarts 需要显式 resize
    if (window.Pages && window.Pages.charts && window.Pages.charts.length) {
      window.setTimeout(function () {
        window.Pages.charts.forEach(function (instance) {
          try {
            instance.resize();
          } catch (error) {
            /* 已释放的实例忽略 */
          }
        });
      }, 60);
    }
  }

  /** 给每张顶层卡补齐标识与句柄（幂等）。 */
  function decorate(cards) {
    cards.forEach(function (card, index) {
      // 稳定标识：优先用卡片自身 id（拖拽/重渲染后不变），无 id 才回落到序号
      if (!card.getAttribute("data-card-key")) {
        card.setAttribute("data-card-key", card.id || (pageId + "#" + index));
      }
      card.classList.add("grid-card");
      if (!card.querySelector(":scope > .grid-handle")) {
        var handle = document.createElement("span");
        handle.className = "grid-handle";
        handle.title = "拖拽调整位置";
        handle.textContent = "⠿";
        card.appendChild(handle);
      }
      if (!card.querySelector(":scope > .grid-width")) {
        var widths = document.createElement("span");
        widths.className = "grid-width";
        [4, 6, 12].forEach(function (span) {
          var btn = document.createElement("button");
          btn.type = "button";
          btn.className = "grid-width-btn";
          btn.textContent = String(span);
          btn.title = "占 " + span + " 列";
          btn.addEventListener("click", function (event) {
            event.preventDefault();
            event.stopPropagation();
            applySpan(card, span);
            persist();
          });
          widths.appendChild(btn);
        });
        card.appendChild(widths);
      }
    });
  }

  /** 应用持久化布局（顺序 + 宽度）；无记录时保持渲染顺序、宽度 12。 */
  function applyLayout() {
    host = document.querySelector(".page-view:not([hidden])");
    pageId = host ? host.id : "";
    if (!host) {
      return;
    }
    var cards = topCards(host);
    decorate(cards);
    // 多卡父容器启用 12 列栅格（单卡容器无意义，避免改变原有单列布局）
    var parents = [];
    cards.forEach(function (card) {
      if (parent = card.parentElement, parents.indexOf(parent) < 0) {
        parents.push(parent);
      }
    });
    parents.forEach(function (parent) {
      var count = cards.filter(function (card) { return card.parentElement === parent; }).length;
      parent.classList.toggle("grid-host", count > 1);
    });
    var saved = pageLayout();
    if (saved && saved.length) {
      var byKey = {};
      cards.forEach(function (card) {
        byKey[card.getAttribute("data-card-key")] = card;
      });
      // 依次 appendChild 到同一父容器 = 按记录顺序重排（saved 覆盖全部顶层卡时完整还原）
      saved.forEach(function (item) {
        var card = byKey[item.key];
        if (!card) {
          return;
        }
        if (card.parentElement) {
          card.parentElement.appendChild(card);
        }
        if (item.span) {
          applySpan(card, item.span);
        }
      });
    }
    cards.forEach(function (card) {
      if (!card.hasAttribute("data-span")) {
        applySpan(card, 12);
      }
    });
    host.classList.toggle("layout-mode", layoutMode);
  }

  /** 进入/退出布局模式：句柄与宽度按钮的显隐由 CSS 控制。 */
  function setLayoutMode(next) {
    layoutMode = Boolean(next);
    try {
      window.localStorage.setItem(MODE_KEY, layoutMode ? "1" : "0");
    } catch (error) {
      /* 忽略 */
    }
    if (host) {
      host.classList.toggle("layout-mode", layoutMode);
    }
    var button = document.getElementById("layout-toggle");
    if (button) {
      button.classList.toggle("active", layoutMode);
      button.textContent = layoutMode ? "◧ 布局中" : "◧ 布局";
    }
    if (window.OB && OB.ui && OB.ui.toast) {
      OB.ui.toast(layoutMode ? "布局模式：拖拽卡片调序，点数字切 4/6/12 列" : "已退出布局模式");
    }
  }

  /** 重置当前页布局（清该页记录并恢复全宽）。 */
  function resetLayout() {
    if (!pageId) {
      return;
    }
    var all = loadAll();
    delete all[pageId];
    saveAll(all);
    var cards = topCards(host);
    cards.forEach(function (card) { applySpan(card, 12); });
    if (window.OB && OB.ui && OB.ui.toast) {
      OB.ui.toast("已重置本页布局");
    }
  }

  /** 拖拽：按下句柄开始，移动时高亮插入位，松开重排并持久化。 */
  function bindDrag() {
    document.addEventListener("mousedown", function (event) {
      if (!layoutMode || !host) {
        return;
      }
      var handle = event.target.closest ? event.target.closest(".grid-handle") : null;
      if (!handle) {
        return;
      }
      var card = handle.closest(".pg-card");
      if (!card || card.parentElement.closest(".pg-card")) {
        return;
      }
      event.preventDefault();
      dragState = { card: card, startX: event.clientX, startY: event.clientY, target: null };
      card.classList.add("grid-dragging");
    });

    document.addEventListener("mousemove", function (event) {
      if (!dragState) {
        return;
      }
      var cards = topCards(host).filter(function (item) { return item !== dragState.card; });
      var found = null;
      cards.forEach(function (item) {
        var rect = item.getBoundingClientRect();
        if (event.clientX >= rect.left && event.clientX <= rect.right &&
            event.clientY >= rect.top && event.clientY <= rect.bottom) {
          found = item;
        }
      });
      if (dragState.target && dragState.target !== found) {
        dragState.target.classList.remove("grid-drop");
      }
      dragState.target = found;
      if (found) {
        found.classList.add("grid-drop");
      }
    });

    document.addEventListener("mouseup", function () {
      if (!dragState) {
        return;
      }
      var card = dragState.card;
      var target = dragState.target;
      card.classList.remove("grid-dragging");
      if (target) {
        target.classList.remove("grid-drop");
        var parent = card.parentElement;
        // 插到目标卡之前（同一父容器内才移动，跨容器不动）
        if (target.parentElement === parent) {
          parent.insertBefore(card, target);
          persist();
        }
      }
      dragState = null;
    });
  }

  /** 双击卡片标题：全宽 ⇄ 半宽 快切（仅带标题卡生效）。 */
  function bindDoubleClick() {
    document.addEventListener("dblclick", function (event) {
      if (!host) {
        return;
      }
      var title = event.target.closest ? event.target.closest(".pg-card-title") : null;
      if (!title) {
        return;
      }
      var card = title.closest(".pg-card");
      if (!card || card.parentElement.closest(".pg-card")) {
        return;
      }
      var current = Number(card.getAttribute("data-span") || 12);
      applySpan(card, current === 12 ? 6 : 12);
      persist();
      if (window.OB && OB.ui && OB.ui.toast) {
        OB.ui.toast("宽度：" + (current === 12 ? "半宽（6 列）" : "全宽（12 列）"));
      }
    });
  }

  /** 顶栏按钮：布局模式开关 + 重置。 */
  function bindToolbar() {
    var toggle = document.getElementById("layout-toggle");
    if (toggle) {
      toggle.addEventListener("click", function () { setLayoutMode(!layoutMode); });
    }
    var reset = document.getElementById("layout-reset");
    if (reset) {
      reset.addEventListener("click", resetLayout);
    }
  }

  /** 入口：恢复模式、绑定交互、首次应用；路由切换后重放布局。 */
  function init() {
    try {
      layoutMode = window.localStorage.getItem(MODE_KEY) === "1";
    } catch (error) {
      layoutMode = false;
    }
    bindToolbar();
    bindDrag();
    bindDoubleClick();
    window.setTimeout(applyLayout, 600);
    window.addEventListener("hashchange", function () {
      window.setTimeout(applyLayout, 700);
    });
    var button = document.getElementById("layout-toggle");
    if (button) {
      button.classList.toggle("active", layoutMode);
      button.textContent = layoutMode ? "◧ 布局中" : "◧ 布局";
    }
  }

  window.Grid = {
    apply: applyLayout,
    setMode: setLayoutMode,
    reset: resetLayout,
    isMode: function () { return layoutMode; }
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
