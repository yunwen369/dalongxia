/* dalongxia 前端公共基础层 · lib/ui.js
 * ------------------------------------------------------------------
 * RF-20261112-01 阶段 3：轻量 UI 单例（toast / loading 口径）。
 * 背景：toast 原唯一实现在 v7_app.js:19（82 处调用），core/ctxmenu.js 与
 * core/grid.js 曾引用【不存在的 window.Utils.toast】而静默失效
 * （BUG-20261112-07，阶段 3 收编时接通）。
 * loading 为全站统一的 `<div class="loading">文案</div>` 口径（样式在 app.css）。
 */
(function () {
  "use strict";

  window.OB = window.OB || {};

  OB.ui = {
    /** 右下角轻提示（v7-toast 样式，2200ms 自毁）。重复调用各自独立冒泡。 */
    toast: function (text) {
      var el = document.createElement("div");
      el.className = "v7-toast";
      el.textContent = text;
      document.body.appendChild(el);
      setTimeout(function () { el.remove(); }, 2200);
    },

    /** 统一加载态 HTML 片段（拼进 innerHTML 场景使用） */
    loadingHTML: function (text) {
      return '<div class="loading">' + (text || "加载中…") + "</div>";
    }
  };
})();
