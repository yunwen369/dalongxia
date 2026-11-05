/* =============================================================
 * OpenBB 中文投研工作台 · core/tree.js
 * 侧栏 5 组目录树交互：折叠状态记忆 + 搜索过滤（GL-02 / DS-R10 §5）。
 *
 * 行为约定
 * --------
 * 1. 折叠记忆：`<details data-group>` 的 open 状态写入 localStorage，
 *    刷新/重启后恢复；键名一版一换（v1），结构变更时旧值自动失效。
 * 2. 搜索过滤：按**已渲染文案**匹配（i18n 之后是中文），命中项保留、
 *    其余 `display:none`；组内零命中整组淡化并强制展开，便于看清无结果。
 * 3. Esc 清空搜索并恢复全部条目。
 * 4. 不改动 DOM 顺序、不重建节点 —— 避免打断 app.js 的 click 委托。
 * ============================================================= */
(function () {
  "use strict";

  var STORE_KEY = "openbb-sb-groups-v1";

  /** 读取折叠状态（损坏时静默回落空对象）。 */
  function loadState() {
    try {
      return JSON.parse(window.localStorage.getItem(STORE_KEY) || "{}") || {};
    } catch (error) {
      return {};
    }
  }

  /** 写入折叠状态（隐私模式不可用时忽略）。 */
  function saveState(state) {
    try {
      window.localStorage.setItem(STORE_KEY, JSON.stringify(state));
    } catch (error) {
      /* 忽略：折叠记忆非关键路径 */
    }
  }

  /** 按关键词过滤条目；空关键词恢复全部。 */
  function filterTree(tree, keyword) {
    var query = (keyword || "").trim();
    var groups = tree.querySelectorAll(".sb-group");
    Array.prototype.forEach.call(groups, function (group) {
      var items = group.querySelectorAll(".sidebar-item, .sidebar-subtree");
      var hits = 0;
      Array.prototype.forEach.call(items, function (node) {
        var text = (node.textContent || "").trim();
        var matched = !query || text.indexOf(query) >= 0;
        node.classList.toggle("sb-hidden", !matched);
        if (matched && node.classList.contains("sidebar-item")) {
          hits += 1;
        }
      });
      group.classList.toggle("sb-empty-group", Boolean(query) && hits === 0);
      if (query && hits > 0) {
        group.open = true;
      }
    });
  }

  /** 入口：绑定折叠记忆与搜索。 */
  function init() {
    var tree = document.getElementById("sb-tree");
    if (!tree) {
      return;
    }
    var state = loadState();
    var groups = tree.querySelectorAll(".sb-group");
    Array.prototype.forEach.call(groups, function (group) {
      var name = group.getAttribute("data-group") || "";
      if (name && Object.prototype.hasOwnProperty.call(state, name)) {
        group.open = Boolean(state[name]);
      }
      group.addEventListener("toggle", function () {
        if (!name) {
          return;
        }
        var next = loadState();
        next[name] = group.open;
        saveState(next);
      });
    });

    var input = document.getElementById("sb-search");
    if (input) {
      input.addEventListener("input", function () {
        filterTree(tree, input.value);
      });
      input.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
          input.value = "";
          filterTree(tree, "");
          input.blur();
        }
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
