/* dalongxia 前端公共基础层 · lib/dom.js
 * ------------------------------------------------------------------
 * RF-20261112-01 阶段 1b：DOM 构建/转义唯一入口（L1 lib 层）。
 * 背景：esc() 现仅存一处实现（v7_app.js:1046），但 146 处 innerHTML 字符串拼接
 * 是 XSS 面与维护成本的双重来源；阶段 4 模板化后逐点切换到本层。
 *
 * 设计约定：
 *   - OB.dom.esc：与 v7_app.js esc() 逐字符等价（转义映射一致），阶段 4 让
 *     v7_app 内部 esc 改为委托此处，期间两处定义保持行为一致；
 *   - OB.dom.h：createElement 构建器。children 中的【字符串默认按文本节点
 *     处理（自动安全）】，需要插入 HTML 必须显式用 {html:"..."} 属性，
 *     把「注入意图」变成代码里可审查的显式行为。
 */
(function () {
  "use strict";

  window.OB = window.OB || {};

  var ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

  OB.dom = {
    /** HTML 转义：null/undefined 安全（→ 空串），与 v7_app.js esc 等价 */
    esc: function (s) {
      return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
        return ESC_MAP[c];
      });
    },

    /**
     * 建元素：h("div", {className:"card", text:"hi"}, [child, "文本", ...])
     * attrs 支持：className / id / text（textContent，安全）/ html（innerHTML，
     * 仅限受控模板）/ dataset / style（对象）/ 布尔属性 disabled/hidden/selected/
     * checked/type/value/title/placeholder/href/src/onclick 等原生属性直传。
     * children：元素节点直接挂、字符串按文本节点挂、null/undefined 跳过。
     */
    h: function (tag, attrs, children) {
      var el = document.createElement(tag);
      if (attrs) {
        for (var k in attrs) {
          if (!Object.prototype.hasOwnProperty.call(attrs, k)) { continue; }
          var v = attrs[k];
          if (v === null || v === undefined || v === false) { continue; }
          if (k === "className") { el.className = v; }
          else if (k === "text") { el.textContent = v; }
          else if (k === "html") { el.innerHTML = v; }
          else if (k === "dataset" && typeof v === "object") {
            for (var dk in v) {
              if (Object.prototype.hasOwnProperty.call(v, dk)) { el.dataset[dk] = v[dk]; }
            }
          }
          else if (k === "style" && typeof v === "object") {
            for (var sk in v) {
              if (Object.prototype.hasOwnProperty.call(v, sk)) { el.style[sk] = v[sk]; }
            }
          }
          else { el.setAttribute(k, v === true ? "" : v); }
        }
      }
      if (children) {
        for (var i = 0; i < children.length; i++) {
          var c = children[i];
          if (c === null || c === undefined) { continue; }
          if (typeof c === "string") { el.appendChild(document.createTextNode(c)); }
          else { el.appendChild(c); }
        }
      }
      return el;
    },

    /** 清空并批量挂载子节点（innerHTML="" 的安全替代） */
    fill: function (el, children) {
      while (el.firstChild) { el.removeChild(el.firstChild); }
      if (children) {
        for (var i = 0; i < children.length; i++) {
          var c = children[i];
          if (c === null || c === undefined) { continue; }
          if (typeof c === "string") { el.appendChild(document.createTextNode(c)); }
          else { el.appendChild(c); }
        }
      }
      return el;
    }
  };
})();
