/* OpenBB-CN 前端公共基础层 · lib/config.js
 * ------------------------------------------------------------------
 * RF-20261112-01 阶段 1b：配置/常量唯一来源（L6 配置常量层）。
 * 目标：localStorage 键名、共享常量「一处定义、全量引用」。
 * 约束：零构建（规范 6.1-2）——IIFE + window.OB 命名空间，禁 npm/模块化。
 * 挂载顺序：本文件必须是 lib/* 中第一个加载（负责创建 OB 根命名空间）。
 */
(function () {
  "use strict";

  window.OB = window.OB || {};

  /* localStorage 键名枚举 —— 全站唯一（新增键必须先在此登记）。
   * 现存 7 键（2026-11-12 全量盘点，来源：v7_app.js / v6_keys.js / core/tree.js / core/grid.js）。
   * 兼容性铁律：键名一旦发布即为持久化契约，禁止改名。 */
  OB.config = {
    KEYS: {
      SYMBOL: "openbb-symbol",            // 个股代码记忆（v7_app.js）
      THEME: "openbb-theme",              // 主题皮肤 v6/dark/light（v6_keys.js）
      LAYOUT: "openbb-layout",            // 布局密度 comfort/cozy（v7_app.js）
      LAYOUT_CUSTOM: "openbb-layout-custom", // 自定义布局 JSON（v7_app.js）
      SB_GROUPS: "openbb-sb-groups-v1",   // 侧栏分组折叠记忆（core/tree.js）
      GRID: "openbb-grid-v1",             // 卡片网格布局（core/grid.js）
      GRID_MODE: "openbb-grid-mode"       // 网格编辑模式开关（core/grid.js）
    }
  };
})();
