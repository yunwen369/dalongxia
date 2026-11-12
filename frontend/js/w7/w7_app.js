/* W7 装配——v7_app.js 初始化节（RF-20261112-01 阶段4）。
   全部域文件加载完成后执行；bind 调用顺序与原 init() 逐一对应。 */
(function () {
  "use strict";
  var W7 = window.W7 = window.W7 || {};

  function init() {
    W7.shell.bindLogo();
    W7.shell.bindCollapse();
    W7.shell.bindMobileNav();
    W7.shell.bindLayout();
    W7.topbar.bindSearch();
    W7.topbar.bindBell();
    W7.topbar.bindBadges();
    W7.ai.bindAi();
    W7.forms.bindActions();
    W7.detail.watchRoute();
    W7.misc.bindWidgetParams();
    W7.portfolio.bindDashboardPersist();
    W7.detail.bindKlineTools();
    W7.misc.bindContextMenu();
    W7.portfolio.watchViewRender();
    var mode = W7.shell.currentLayout();
    W7.shell.applyLayout(mode);
    if (mode === "custom") { W7.shell.applyLayout("custom", W7.shell.customOpts()); }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.V7 = { applyLayout: W7.shell.applyLayout, toast: W7.core.toast, openPanel: W7.shell.openPanel };
})();
