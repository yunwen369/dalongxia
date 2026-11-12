/* OpenBB-CN 前端公共基础层 · lib/store.js
 * ------------------------------------------------------------------
 * RF-20261112-01 阶段 1b：localStorage 唯一读写入口（L1 lib 层）。
 * 目标：全站 localStorage 访问收编到一处，统一 try/catch（隐私模式兜底）。
 *
 * 行为兼容铁律（阶段 1b 只建层不迁移，迁移在阶段 4 逐点切换）：
 *   - get/set 是【原始字符串】口径 —— 现存键 openbb-symbol/theme/layout
 *     均为裸字符串，若做成 JSON.parse 口径会把 "300750" 解析成数字，属行为变更，禁止；
 *   - getJSON/setJSON 才是 JSON 口径 —— 对应现存 openbb-layout-custom /
 *     openbb-sb-groups-v1 / openbb-grid-v1 的 JSON.stringify 存取。
 */
(function () {
  "use strict";

  window.OB = window.OB || {};

  OB.store = {
    /** 读原始字符串；任何异常（隐私模式/被禁用）返回 fallback */
    get: function (key, fallback) {
      try {
        var v = localStorage.getItem(key);
        return v === null ? (fallback === undefined ? null : fallback) : v;
      } catch (e) {
        return fallback === undefined ? null : fallback;
      }
    },

    /** 写原始字符串；异常静默（与现存各处 catch-noop 口径一致） */
    set: function (key, value) {
      try { localStorage.setItem(key, String(value)); } catch (e) { /* noop */ }
    },

    /** 删除键；异常静默 */
    remove: function (key) {
      try { localStorage.removeItem(key); } catch (e) { /* noop */ }
    },

    /** 读 JSON；解析失败或异常返回 fallback（deep 不拷贝，调用方按值语义使用） */
    getJSON: function (key, fallback) {
      try {
        var raw = localStorage.getItem(key);
        if (raw === null) { return fallback; }
        var parsed = JSON.parse(raw);
        return parsed === null || parsed === undefined ? fallback : parsed;
      } catch (e) {
        return fallback;
      }
    },

    /** 写 JSON；异常静默 */
    setJSON: function (key, value) {
      try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* noop */ }
    },

    /** 键是否存在且非空 */
    has: function (key) {
      try { return localStorage.getItem(key) !== null; } catch (e) { return false; }
    }
  };
})();
