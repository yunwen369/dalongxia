/* W7 core——共享原语与常量（RF-20261112-01 阶段4 切割自 v7_app.js §0 与 §8 统一请求小节） */
(function () {
  "use strict";
  var W7 = window.W7 = window.W7 || {};
  W7.st = W7.st || {};   // 跨域可变状态（原巨石 13 个模块级 var）
  W7.core = {};

  /* ---- 原 v7_app.js §0 头部，原样 ---- */
    var $ = function (id) { return document.getElementById(id); };
    var LS_LAYOUT = "openbb-layout";
    var LS_CUSTOM = "openbb-layout-custom";
    var DEMO_SEARCH = [
      { symbol: "600519", name: "贵州茅台", market: "A股" },
      { symbol: "300750", name: "宁德时代", market: "A股" },
      { symbol: "000001", name: "平安银行", market: "A股" },
      { symbol: "017811", name: "易方达蓝筹精选", market: "基金" }
    ];
    /** 右下角轻提示。（阶段3：唯一实现迁 lib/ui.js，此处委托别名） */
    function toast(text) {
      OB.ui.toast(text);
    }

  /* ---- 原 §8「统一请求（401 跳登录，D-19）」小节，原样 ---- */
  /* ---------- 统一请求（401 跳登录，D-19） ----------
  * RF-20261112-01 阶段2：唯一实现迁至 lib/http.js（OB.http.apiJson），
  * 此处保留本地委托别名，巨石内全部调用点行为不变。 */
  function apiJson(path, opts) {
  return OB.http.apiJson(path, opts);
  }

  function queryParam(name) {
  var m = String(location.hash || "").split("?")[1] || "";
  var hit = new RegExp("(?:^|&)" + name + "=([^&]*)").exec(m);
  return hit ? decodeURIComponent(hit[1]) : "";
  }


  /* ---- core 导出 ---- */
  W7.core.$ = $;
  W7.core.toast = toast;
  W7.core.apiJson = apiJson;
  W7.core.queryParam = queryParam;
  W7.core.LS_LAYOUT = LS_LAYOUT;
  W7.core.LS_CUSTOM = LS_CUSTOM;
  W7.core.DEMO_SEARCH = DEMO_SEARCH;
})();
