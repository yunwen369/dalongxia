/* =============================================================
 * OpenBB 中文投研工作台 · pages/init.js
 * 初始化（bindResize · 铃铛轮询 · 路由启动）
 * 由 tools/split_pages.py 从 pages.js 机械切分生成（R17），逻辑零改动；
 * 重新生成请改 pages.js 后重跑脚本，勿直接编辑本文件的结构性段落。
 * ============================================================= */
(function (P) {
  "use strict";

  // ---- 跨模块依赖（core/兄弟模块导出，运行时解析） ----
  var charts = P.charts;
  var route = P.route;
  var t = P.t;
  var toast = P.toast;

  // =============================================================
  // 十三、初始化
  // =============================================================
  var resizeTimer = null;

  /** 窗口尺寸变化时重算所有图表尺寸（缩到 1024×640 最小窗口也不空白，P0-05）。 */
  function bindResize() {
    window.addEventListener("resize", function () {
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(function () {
        charts.forEach(function (instance) {
          try {
            instance.resize();
          } catch (error) {
            /* 已释放则忽略 */
          }
        });
      }, 120);
    });
  }

  function startBellPolling() {
    var tick = function () {
      if (document.hidden) {
        return;
      }
      P.alertEvaluate(false).then(function (result) {
        P.updateBell(result.unread || 0);
        // 新事件：toast 提醒 + 徽标刷新（在提醒页时由页面自身刷新列表）
        if (result.new_events && result.events && result.events.length) {
          toast(t("alr.newEventToast", { first: result.events[0].message || "" }));
        }
      }).catch(function () {
        /* 后台轮询失败静默 */
      });
    };
    tick();
    window.setInterval(tick, 120000);
  }

  startBellPolling();

  /** 入口：绑定路由与尺寸监听。 */
  function init() {
    P.bindNav();
    bindResize();
    window.addEventListener("hashchange", route);
    P.route();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // ---- 导出到 window.Pages（供兄弟模块与 init 使用） ----
  P.bindResize = bindResize;
  P.init = init;
  P.resizeTimer = resizeTimer;
  P.startBellPolling = startBellPolling;
})(window.Pages = window.Pages || {});
