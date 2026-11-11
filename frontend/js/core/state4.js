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
 * - 恢复「正常」= 重新走一次路由，等价于用户重新进入该页。
 * ============================================================= */
(function () {
  "use strict";

  var selectId = "state4-select";

  /** 当前可见页的所有区块容器。
   *
   * 缺陷修复（2026-11-11 四态取证暴露）：
   * 原实现只找 `.pg-body`，但 `#/fund-combo`、`#/widget-library` 等页面
   * 的根容器直接是 `.page-view` 本身（内部无 `.pg-body`），导致
   * `State4.apply()` 找不到容器直接 return，四态截图与默认态完全一致。
   * 现改为：优先取 `.pg-body`；若无则回退到页面根节点，保证「每页四态可用」。
   * @returns {Array} 容器数组
   */
  function bodies() {
    var page = document.querySelector(".page-view:not([hidden])");
    if (!page) {
      return [];
    }
    var list = Array.prototype.slice.call(page.querySelectorAll(".pg-body"));
    return list.length ? list : [page];
  }

  /** 切到指定态：default 走重渲染，其余调用 core 四态函数。 */
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
      pages.route();
      return;
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
    // 路由切换后清空选择，防止跨页残留
    window.addEventListener("hashchange", function () { select.value = "default"; });
  }

  window.State4 = { apply: apply };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
