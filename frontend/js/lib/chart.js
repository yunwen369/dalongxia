/* OpenBB-CN 前端公共基础层 · lib/chart.js
 * ------------------------------------------------------------------
 * RF-20261112-01 阶段 3：ECharts 实例唯一管理入口。
 * 背景：全站 echarts.init 5 处（app.js×2 / v7_app.js×3）且【无任何 dispose】——
 * 常驻容器（如 index.html 的 #kline-chart）每次渲染都重复 init 新实例，
 * 旧实例滞留 ECharts 注册表形成泄漏。
 *
 * 行为兼容设计：
 *   - 同一 DOM 复用已有实例（getInstanceByDom），复用时先 clear() 再返回，
 *     随后的 setOption 等价于全新配置 → 与「每次新 init」的视觉行为一致；
 *   - innerHTML 重建过的容器：旧实例仍挂在原 DOM 节点上，复用路径同样生效；
 *   - opts 仅在首次 init 生效（renderer 全站一致为 canvas，复用无参数漂移）。
 */
(function () {
  "use strict";

  window.OB = window.OB || {};

  OB.chart = {
    /** 取/建图表实例：同 DOM 复用（clear 后返回），否则 echarts.init */
    init: function (el, opts) {
      if (!el || !window.echarts) { return null; }
      var inst = window.echarts.getInstanceByDom(el);
      if (inst) { inst.clear(); return inst; }
      return window.echarts.init(el, null, opts || undefined);
    },

    /** 显式释放（容器销毁场景；阶段 4 组件卸载时调用） */
    dispose: function (el) {
      if (!el || !window.echarts) { return; }
      var inst = window.echarts.getInstanceByDom(el);
      if (inst) { inst.dispose(); }
    }
  };
})();
