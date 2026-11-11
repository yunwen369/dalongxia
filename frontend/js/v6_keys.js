/* V6 桌面快捷键与主题持久化补丁（按《设计说明书》§5.5 + §八 补齐）。
 *
 * 背景：定稿原型工程未实现说明书中明确要求的 6 项快捷键与主题记忆，
 *       整站替换后一并缺失。本文件为**增量补丁**，不修改 app.js / app.css。
 *
 * 覆盖：
 *   Ctrl+M       V6 ↔ 砚 主题切换（并在 Ctrl+M 时联动图表 resize）
 *   Ctrl+K       全局命令面板（29 视图 + 我的仪表板，输入过滤，Enter 跳转）
 *   Ctrl+F       页面内搜索（高亮命中 + 计数，Esc 清除）
 *   F5           刷新当前视图数据（调用 V6_LIVE.tick）
 *   Esc          关闭弹窗 / 取消（先关本补丁浮层，再关组件添加弹窗）
 *   Ctrl+Enter   提交当前可见视图表单 / 触发主按钮
 *   localStorage["openbb-theme"]  主题记忆（v6 / dark / light）
 *
 * 加载顺序：必须位于 js/app.js 之后（依赖 window.V6_VIEWS 与 window.V6_LIVE）。
 */
(function () {
  "use strict";

  var THEME_KEY = "openbb-theme";
  var SKINS = ["v6", "dark", "light"];

  /* ---------- 1. 主题应用与持久化 ---------- */

  /** 切换皮肤并写入本地记忆；同步按钮选中态与 ECharts 尺寸。 */
  function setSkin(skin) {
    if (SKINS.indexOf(skin) < 0) { return; }
    if (skin === "dark") {
      document.body.removeAttribute("data-theme");
    } else {
      document.body.setAttribute("data-theme", skin);
    }
    Array.prototype.forEach.call(document.querySelectorAll(".skin-switch button"), function (btn) {
      btn.classList.toggle("active", btn.getAttribute("data-skin") === skin);
    });
    try { localStorage.setItem(THEME_KEY, skin); } catch (ignored) { /* 隐私模式忽略 */ }
    if (window.__v6Chart) { try { window.__v6Chart.resize(); } catch (ignored) { /* noop */ } }
    if (window.__v6ViewChart) { try { window.__v6ViewChart.resize(); } catch (ignored) { /* noop */ } }
  }

  /** 启动时恢复上次主题。 */
  function restoreSkin() {
    var saved = null;
    try { saved = localStorage.getItem(THEME_KEY); } catch (ignored) { saved = null; }
    if (saved && SKINS.indexOf(saved) >= 0) { setSkin(saved); }
  }

  /** 顶栏皮肤点击后补写记忆（不改变 app.js 的原有切换行为）。 */
  function bindSkinMemory() {
    var host = document.querySelector(".skin-switch");
    if (!host) { return; }
    host.addEventListener("click", function (event) {
      var btn = event.target.closest ? event.target.closest("button[data-skin]") : null;
      if (!btn) { return; }
      var skin = btn.getAttribute("data-skin");
      window.setTimeout(function () {
        try { localStorage.setItem(THEME_KEY, skin); } catch (ignored) { /* noop */ }
      }, 0);
    });
  }

  /* ---------- 2. 浮层基础（样式内联注入，避免改 css/app.css） ---------- */

  var STYLE = [
    ".v6k-mask{position:fixed;inset:0;background:rgba(4,8,16,.62);z-index:9998;display:flex;",
    "align-items:flex-start;justify-content:center;padding-top:12vh;font:13px/1.5 Inter,",
    "\"PingFang SC\",\"Microsoft YaHei\",system-ui,sans-serif;}",
    ".v6k-box{width:520px;max-width:88vw;background:#0d1b2e;border:1px solid #4a7096;border-radius:8px;",
    "box-shadow:0 18px 48px rgba(0,0,0,.55);overflow:hidden;color:#fff;}",
    ".v6k-box input{width:100%;box-sizing:border-box;padding:11px 14px;border:0;border-bottom:1px solid #2a4763;",
    "background:#09131e;color:#fff;font-size:13px;outline:none;}",
    ".v6k-list{max-height:46vh;overflow:auto;}",
    ".v6k-item{padding:8px 14px;display:flex;justify-content:space-between;gap:10px;cursor:pointer;}",
    ".v6k-item:hover,.v6k-item.on{background:#16324c;}",
    ".v6k-item b{font-weight:600;}",
    ".v6k-item span{color:#8fb0cc;font-size:12px;}",
    ".v6k-foot{padding:6px 14px;color:#8fb0cc;font-size:12px;border-top:1px solid #1d2f42;}",
    "mark.v6k-hit{background:#f5c451;color:#101820;border-radius:2px;}"
  ].join("");

  function injectStyle() {
    if (document.getElementById("v6k-style")) { return; }
    var style = document.createElement("style");
    style.id = "v6k-style";
    style.textContent = STYLE;
    document.head.appendChild(style);
  }

  /** 当前可见视图的内容容器（首页用 #home-main，其余用 #view-body）。 */
  function visibleScope() {
    var home = document.getElementById("home-main");
    var generic = document.getElementById("view-generic");
    if (home && !home.hidden) { return home; }
    if (generic && !generic.hidden) { return document.getElementById("view-body"); }
    return document.body;
  }

  /* ---------- 3. 命令面板（Ctrl+K） ---------- */

  var cmdMask = null;
  var cmdInput = null;
  var cmdList = null;
  var cmdIndex = 0;

  function titleMap() {
    var views = window.V6_VIEWS || {};
    return Object.keys(views).map(function (id) {
      return { id: id, title: views[id].title || id, group: views[id].group || "" };
    });
  }

  function renderCmd(keyword) {
    var rows = titleMap().filter(function (row) {
      var kw = (keyword || "").toLowerCase();
      return !kw || (row.title + " " + row.id + " " + row.group).toLowerCase().indexOf(kw) >= 0;
    });
    cmdIndex = 0;
    cmdList.innerHTML = rows.map(function (row, i) {
      return '<div class="v6k-item' + (i === 0 ? " on" : "") + '" data-view="' + row.id + '">' +
        "<b>" + row.title + "</b><span>#/" + row.id + " · " + row.group + "</span></div>";
    }).join("") || '<div class="v6k-item"><b>无匹配视图</b></div>';
    cmdList.setAttribute("data-rows", String(rows.length));
  }

  function openCmd() {
    injectStyle();
    ensureCmd();
    cmdMask.hidden = false;
    cmdInput.value = "";
    renderCmd("");
    cmdInput.focus();
  }

  /** 预先创建命令面板（保持隐藏），保证元素自页面加载即存在。 */
  function ensureCmd() {
    if (cmdMask) { return; }
    cmdMask = document.createElement("div");
    cmdMask.id = "v6k-cmd";
    cmdMask.className = "v6k-mask command-palette";
    cmdMask.hidden = true;
    cmdMask.innerHTML = '<div class="v6k-box"><input id="v6k-cmd-input" ' +
      'placeholder="输入视图名或路由…（Enter 跳转 · Esc 关闭）">' +
      '<div class="v6k-list" id="v6k-cmd-list"></div>' +
      '<div class="v6k-foot">↑ ↓ 选择 · Enter 跳转 · Esc 关闭</div></div>';
    document.body.appendChild(cmdMask);
    cmdInput = document.getElementById("v6k-cmd-input");
    cmdList = document.getElementById("v6k-cmd-list");
    cmdInput.addEventListener("input", function () { renderCmd(cmdInput.value); });
    cmdList.addEventListener("click", function (event) {
      var item = event.target.closest(".v6k-item");
      if (item && item.getAttribute("data-view")) { goView(item.getAttribute("data-view")); }
    });
  }

  function closeCmd() {
    if (cmdMask) { cmdMask.hidden = true; }
  }

  function goView(id) {
    closeCmd();
    location.hash = "#/" + id;
  }

  function moveCmd(delta) {
    var items = cmdList.querySelectorAll(".v6k-item[data-view]");
    if (!items.length) { return; }
    cmdIndex = (cmdIndex + delta + items.length) % items.length;
    Array.prototype.forEach.call(items, function (node, i) {
      node.classList.toggle("on", i === cmdIndex);
    });
    items[cmdIndex].scrollIntoView({ block: "nearest" });
  }

  function cmdEnter() {
    var items = cmdList.querySelectorAll(".v6k-item[data-view]");
    if (items[cmdIndex]) { goView(items[cmdIndex].getAttribute("data-view")); }
  }

  /* ---------- 4. 页内搜索（Ctrl+F） ---------- */

  var findMask = null;
  var findInput = null;
  var findCount = null;

  function clearMarks() {
    Array.prototype.forEach.call(document.querySelectorAll("mark.v6k-hit"), function (mark) {
      var text = document.createTextNode(mark.textContent);
      if (mark.parentNode) { mark.parentNode.replaceChild(text, mark); }
    });
  }

  /** 在可见容器内高亮命中词，返回命中数。 */
  function highlight(keyword) {
    clearMarks();
    if (!keyword) { return 0; }
    var scope = visibleScope();
    if (!scope) { return 0; }
    var walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        if (!node.nodeValue || !node.nodeValue.trim()) { return NodeFilter.FILTER_REJECT; }
        var tag = node.parentNode && node.parentNode.nodeName;
        if (tag === "SCRIPT" || tag === "STYLE" || tag === "MARK") { return NodeFilter.FILTER_REJECT; }
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    var targets = [];
    var node = walker.nextNode();
    while (node) { targets.push(node); node = walker.nextNode(); }
    var lower = keyword.toLowerCase();
    var hits = 0;
    targets.forEach(function (textNode) {
      var text = textNode.nodeValue;
      var idx = text.toLowerCase().indexOf(lower);
      if (idx < 0) { return; }
      var frag = document.createDocumentFragment();
      var rest = text;
      while (idx >= 0) {
        frag.appendChild(document.createTextNode(rest.slice(0, idx)));
        var mark = document.createElement("mark");
        mark.className = "v6k-hit";
        mark.textContent = rest.slice(idx, idx + keyword.length);
        frag.appendChild(mark);
        rest = rest.slice(idx + keyword.length);
        idx = rest.toLowerCase().indexOf(lower);
        hits += 1;
      }
      frag.appendChild(document.createTextNode(rest));
      textNode.parentNode.replaceChild(frag, textNode);
    });
    return hits;
  }

  function openFind() {
    injectStyle();
    ensureFind();
    findMask.hidden = false;
    findInput.value = "";
    findCount.textContent = "0 处命中";
    findInput.focus();
  }

  /** 预先创建页内搜索浮层（保持隐藏）。 */
  function ensureFind() {
    if (findMask) { return; }
    findMask = document.createElement("div");
    findMask.id = "v6k-find";
    findMask.className = "v6k-mask page-search";
    findMask.hidden = true;
    findMask.innerHTML = '<div class="v6k-box"><input id="v6k-find-input" ' +
      'placeholder="页面内搜索…（Esc 清除高亮）">' +
      '<div class="v6k-foot" id="v6k-find-count">0 处命中</div></div>';
    document.body.appendChild(findMask);
    findInput = document.getElementById("v6k-find-input");
    findCount = document.getElementById("v6k-find-count");
    findInput.addEventListener("input", function () {
      var hits = highlight(findInput.value);
      findCount.textContent = hits + " 处命中";
    });
  }

  function closeFind() {
    if (!findMask || findMask.hidden) { return false; }
    findMask.hidden = true;
    clearMarks();
    return true;
  }

  /* ---------- 5. 快捷键绑定 ---------- */

  function isTyping(event) {
    var tag = event.target && event.target.nodeName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" ||
      (event.target && event.target.isContentEditable);
  }

  function onKeyDown(event) {
    var ctrl = event.ctrlKey || event.metaKey;
    var key = (event.key || "").toLowerCase();

    if (ctrl && key === "m") {                       // V6 ↔ 砚
      event.preventDefault();
      var current = document.body.getAttribute("data-theme");
      setSkin(current === "light" ? "v6" : "light");
      return;
    }
    if (ctrl && key === "k") {                       // 命令面板
      event.preventDefault();
      if (cmdMask && !cmdMask.hidden) { closeCmd(); } else { openCmd(); }
      return;
    }
    if (ctrl && key === "f") {                       // 页内搜索
      event.preventDefault();
      openFind();
      return;
    }
    if (event.key === "F5") {                        // 刷新当前数据
      event.preventDefault();
      if (window.V6_LIVE && typeof window.V6_LIVE.tick === "function") {
        window.V6_LIVE.tick();
      } else {
        location.reload();
      }
      return;
    }
    if (event.key === "Escape") {                    // 关闭 / 取消
      if (closeFind()) { event.preventDefault(); return; }
      if (cmdMask && !cmdMask.hidden) { closeCmd(); event.preventDefault(); return; }
      var modal = document.getElementById("v6-add-modal");
      if (modal && !modal.hidden) { modal.hidden = true; event.preventDefault(); }
      return;
    }
    if (ctrl && event.key === "Enter") {             // 表单提交
      var scope = visibleScope();
      if (!scope) { return; }
      var form = scope.querySelector("form");
      if (form) {
        event.preventDefault();
        if (form.requestSubmit) { form.requestSubmit(); } else { form.submit(); }
        return;
      }
      var primary = scope.querySelector("button.primary,button[type=submit]");
      if (primary) { event.preventDefault(); primary.click(); }
      return;
    }

    // 命令面板内的上下键与回车
    if (cmdMask && !cmdMask.hidden) {
      if (event.key === "ArrowDown") { event.preventDefault(); moveCmd(1); }
      else if (event.key === "ArrowUp") { event.preventDefault(); moveCmd(-1); }
      else if (event.key === "Enter") { event.preventDefault(); cmdEnter(); }
    }
  }

  /* ---------- 6. 初始化 ---------- */

  function init() {
    injectStyle();
    ensureCmd();          // 预建浮层：元素自加载即存在（隐藏），避免首键延迟
    ensureFind();
    restoreSkin();
    bindSkinMemory();
    document.addEventListener("keydown", onKeyDown, true);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.V6_KEYS = { setSkin: setSkin, openCmd: openCmd, openFind: openFind };
})();
