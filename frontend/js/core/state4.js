/* =============================================================
 * OpenBB 中文投研工作台 · core/state4.js
 * 四态演示层（GL-06）：顶栏下拉手动触发 当前页 loading / empty / error。
 *
 * 设计依据：DS-R10-v2.1 §6「每页支持 default/loading/empty/error；原型内 js/state4.js
 * 提供评审演示层；**开发版由请求生命周期驱动，语义一致**」。
 *
 * 定位
 * ----
 * - 开发版真正生效的是请求生命周期（setLoading → setEmpty/setError），本层只用于
 *   **验收时把某一页强制推到某态**，验证语义与视觉一致；正常使用不影响业务渲染；
 * - 调用的是 core 的同一套四态函数（唯一实现，不另写样式），故演示态 ≡ 真实态；
 * - 恢复「正常」= 就地还原演示前现场（同一个 DOM 节点、同一份数据），等价于
 *   从未进入过演示态；不改动 hash、不重载页面、不触发额外网络请求。
 *
 * 缺陷修复沿革（2026-11-11 V6 全量回归复验，D-02 / D-03）
 * ------------------------------------------------------
 * 1) bodies() 原只找 `.pg-body`；`#/fund-combo`、`#/widget-library` 的根容器就是
 *    `.page-view` 本身 → 找不到容器直接 return，四态截图与默认态全同。改为回退页面根节点。
 * 2) 首页 `#/home` 是 index.html 里的静态 market-panel，**没有 `.page-view`** →
 *    bodies() 返回 [] → 顶栏四态下拉在首页完全无反应。改为回退 `#viewport .card-body`。
 * 3) 修完 1)/2) 后空/错态生效，但「恢复正常」无效：pages.route() 对 home 没有
 *    render 分支（core.js 的 route() 仅 showView 后即返回），被 setEmpty/setError
 *    清空的静态卡片不会重建；hash 往返回到 `#/home` 同样不重绘，故无效。
 *    正解：进入演示态前保存**原始子节点引用**（不是 innerHTML 字符串），default 时
 *    把同一批节点放回容器 —— 事件监听与 ECharts 实例绑定不会丢失，恢复是同步的，
 *    也没有异步刷新与演示态互相覆盖的竞态；动态页面仍额外走 pages.route() 重渲染。
 * ============================================================= */
(function () {
  "use strict";

  var selectId = "state4-select";

  /** 演示前现场快照：{ hash, items:[{ node, children, state }] }。跨页自动作废。 */
  var snapshot = null;

  /** 当前可见页的所有区块容器。
   * @returns {Array} 容器数组
   */
  function bodies() {
    var page = document.querySelector(".page-view:not([hidden])");
    if (page) {
      var list = Array.prototype.slice.call(page.querySelectorAll(".pg-body"));
      return list.length ? list : [page];
    }
    // 首页（#/home）是 index.html 里的静态 market-panel 布局，没有 .page-view 容器。
    // 回退到 #viewport 内的 .card-body，与 tools/shoot_states.py 取证路径保持同源。
    return Array.prototype.slice.call(
      document.querySelectorAll("#viewport .card-body")
    );
  }

  /** 保存演示前现场（仅在第一次进入演示态时拍一次）。
   * 保存的是**子节点对象引用**而非 HTML 字符串：setEmpty/setError 会清空 innerHTML，
   * 但被移除的节点对象仍在内存中，恢复时放回即可保留事件监听与图表实例。
   */
  function capture(list) {
    return {
      hash: location.hash,
      items: list.map(function (body) {
        return {
          node: body,
          children: Array.prototype.slice.call(body.childNodes),
          state: body.getAttribute("data-state")
        };
      })
    };
  }

  /** 就地还原现场：放回原始子节点与 data-state，清掉演示态标记。
   * 全程同步执行，保证「恢复正常」在下拉 change 当次即生效，不留异步竞态。
   */
  function restore() {
    if (!snapshot) {
      return;
    }
    // 已切换到别的视图：快照不再适用，直接作废（由路由自行渲染）。
    if (snapshot.hash !== location.hash) {
      snapshot = null;
      return;
    }
    snapshot.items.forEach(function (item) {
      if (!item.node.isConnected) {
        return;
      }
      while (item.node.firstChild) {
        item.node.removeChild(item.node.firstChild);
      }
      item.children.forEach(function (child) {
        item.node.appendChild(child);
      });
      if (item.state === null) {
        item.node.removeAttribute("data-state");
      } else {
        item.node.setAttribute("data-state", item.state);
      }
    });
    snapshot = null;
  }

  /** 切到指定态：default 还原现场，其余调用 core 四态函数。 */
  function apply(kind) {
    var pages = window.Pages;
    if (!pages) {
      return;
    }
    var list = bodies();
    if (!list.length) {
      return;
    }
    if (kind === "default") {
      var isHome = !document.querySelector(".page-view:not([hidden])");
      restore();
      // 首页静态卡片不由 route() 渲染，且 route() 会 disposeCharts() 影响图表实例，
      // 故首页只做现场还原；动态页面再走一次路由，由各页 render 重渲染真实数据。
      if (!isHome) {
        pages.route();
      }
      return;
    }
    if (!snapshot) {
      snapshot = capture(list);
    }
    list.forEach(function (body) {
      if (kind === "loading") {
        pages.setLoading(body);
        return;
      }
      if (kind === "empty") {
        pages.setEmpty(body, "暂无数据", function () { apply("default"); });
        return;
      }
      pages.setError(body, "演示态：数据源暂时不可用（HTTP 503）", function () { apply("default"); });
    });
  }

  /** 入口：绑定顶栏下拉。 */
  function init() {
    var select = document.getElementById(selectId);
    if (!select) {
      return;
    }
    select.addEventListener("change", function () {
      apply(select.value);
      // 演示后回到「正常」选项，避免误导下一次进入
      if (select.value !== "default") {
        window.setTimeout(function () { select.value = "default"; }, 300);
      }
    });
    // 路由切换后清空选择与现场快照，防止跨页残留
    window.addEventListener("hashchange", function () {
      select.value = "default";
      snapshot = null;
    });
  }

  window.State4 = { apply: apply };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
