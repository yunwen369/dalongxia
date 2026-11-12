/* W7.shell——切割自 v7_app.js（RF-20261112-01 阶段4）。代码体原样保留。 */
(function () {
  "use strict";
  var W7 = window.W7 = window.W7 || {};
  var C = W7.core;
  var $ = C.$, toast = C.toast, apiJson = C.apiJson, queryParam = C.queryParam;
  /* ---------- 1. Logo 回首页 ---------- */
  function bindLogo() {
    var brand = document.querySelector(".brand");
    if (!brand) { return; }
    brand.style.cursor = "pointer";
    brand.title = "回到首页";
    brand.addEventListener("click", function () { location.hash = "#/home"; });
  }

  /* ---------- 2. 侧栏整栏折叠 ---------- */
  function bindCollapse() {
    var btn = $("v7-collapse");
    if (!btn) { return; }
    btn.addEventListener("click", function () {
      document.body.classList.toggle("v7-side-collapsed");
      btn.title = document.body.classList.contains("v7-side-collapsed") ? "展开侧栏" : "收起侧栏";
    });
  }

  /* ---------- 3. 布局三档 + W9 自定义 ---------- */
  function currentLayout() {
    try { return localStorage.getItem(LS_LAYOUT) || "comfort"; } catch (e) { return "comfort"; }
  }

  function customOpts() {
    var def = { font: 13, side: 176, gap: 10, kline: 300, row: 34, density: "标准" };
    try {
      var raw = localStorage.getItem(LS_CUSTOM);
      return raw ? Object.assign(def, JSON.parse(raw)) : def;
    } catch (e) { return def; }
  }

  /** 应用布局（写 body class 与 CSS 变量）。 */
  function applyLayout(mode, opts) {
    document.body.classList.remove("v7-compact", "v7-custom");
    if (mode === "compact") { document.body.classList.add("v7-compact"); }
    if (mode === "custom") {
      var o = opts || customOpts();
      document.body.classList.add("v7-custom");
      document.body.style.setProperty("--v7-font", o.font + "px");
      document.body.style.setProperty("--v7-side-width", o.side + "px");
      document.body.style.setProperty("--v7-gap", o.gap + "px");
      document.body.style.setProperty("--v7-kline-h", o.kline + "px");
      document.body.style.setProperty("--v7-row-h", o.row + "px");
    }
    var label = { compact: "紧凑", comfort: "舒适", custom: "自定义" }[mode] || "舒适";
    var btn = $("v7-layout-btn");
    if (btn) { btn.textContent = "⇔ 布局：" + label; }
    try { localStorage.setItem(LS_LAYOUT, mode); } catch (e) { /* 隐私模式忽略 */ }
  }

  function bindLayout() {
    var btn = $("v7-layout-btn");
    var menu = $("v7-layout-menu");
    if (!btn || !menu) { return; }
    btn.addEventListener("click", function (event) {
      event.stopPropagation();
      menu.hidden = !menu.hidden;
    });
    document.addEventListener("click", function () { menu.hidden = true; });
    menu.addEventListener("click", function (event) {
      var item = event.target.closest("button[data-layout]");
      if (!item) { return; }
      var mode = item.getAttribute("data-layout");
      if (mode === "reset") {
        try { localStorage.removeItem(LS_CUSTOM); } catch (e) { /* noop */ }
        applyLayout("comfort");
        toast("已重置为「舒适」默认布局");
      } else if (mode === "custom") {
        applyLayout("custom");
        openPanel();
      } else {
        applyLayout(mode);
      }
      menu.hidden = true;
    });
  }

  /* ---------- 4. W9 自定义面板 ---------- */
  var panel = null;

  function buildPanel() {
    panel = document.createElement("div");
    panel.className = "v7-panel-mask";
    panel.id = "v7-panel";
    panel.hidden = true;
    panel.innerHTML =
      '<div class="v7-panel">' +
        '<div class="v7-panel-head"><span>自定义布局</span>' +
          '<button class="v7-btn" id="v7-panel-x">×</button></div>' +
        '<div class="v7-panel-body">' +
          '<div>' +
            '<div class="v7-field"><span>密度</span><select id="v7-o-density">' +
              '<option>紧凑</option><option selected>标准</option><option>宽松</option></select></div>' +
            '<div class="v7-field"><span>基准字号</span><select id="v7-o-font">' +
              '<option>12</option><option selected>13</option><option>14</option><option>15</option></select></div>' +
            '<div class="v7-field"><span>侧栏宽度</span><select id="v7-o-side">' +
              '<option>160</option><option selected>176</option><option>200</option><option>220</option></select></div>' +
            '<div class="v7-field"><span>卡片间距</span><select id="v7-o-gap">' +
              '<option>6</option><option>10</option><option>14</option></select></div>' +
            '<div class="v7-field"><span>K 线高度</span><select id="v7-o-kline">' +
              '<option>240</option><option selected>300</option><option>360</option></select></div>' +
            '<div class="v7-field"><span>表格行高</span><select id="v7-o-row">' +
              '<option>28</option><option selected>34</option><option>40</option></select></div>' +
          '</div>' +
          '<div class="v7-preview">' +
            '<div class="pv-top"></div>' +
            '<div class="pv-row"><div></div><div></div></div>' +
            '<div class="pv-kline" id="v7-pv-kline" style="height:56px"></div>' +
            '<div style="margin-top:4px;opacity:.7">实时预览</div>' +
          '</div>' +
        '</div>' +
        '<div class="v7-panel-foot">' +
          '<button class="v7-btn" id="v7-panel-reset">重置为默认</button>' +
          '<button class="v7-btn" id="v7-panel-cancel">取消</button>' +
          '<button class="v7-btn primary" id="v7-panel-save">保存为我的布局</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(panel);

    var opt = customOpts();
    $("v7-o-font").value = String(opt.font);
    $("v7-o-side").value = String(opt.side);
    $("v7-o-gap").value = String(opt.gap);
    $("v7-o-kline").value = String(opt.kline);
    $("v7-o-row").value = String(opt.row);

    ["density", "font", "side", "gap", "kline", "row"].forEach(function (k) {
      $("v7-o-" + k).addEventListener("change", preview);
    });
    $("v7-panel-x").addEventListener("click", closePanel);
    $("v7-panel-cancel").addEventListener("click", closePanel);
    $("v7-panel-reset").addEventListener("click", function () {
      try { localStorage.removeItem(LS_CUSTOM); } catch (e) { /* noop */ }
      applyLayout("comfort");
      closePanel();
      toast("已重置为默认布局");
    });
    $("v7-panel-save").addEventListener("click", function () {
      var data = readPanel();
      try { localStorage.setItem(LS_CUSTOM, JSON.stringify(data)); } catch (e) { /* noop */ }
      applyLayout("custom", data);
      closePanel();
      toast("已保存为我的布局（本机生效）");
    });
  }

  function readPanel() {
    return {
      density: $("v7-o-density").value,
      font: parseInt($("v7-o-font").value, 10),
      side: parseInt($("v7-o-side").value, 10),
      gap: parseInt($("v7-o-gap").value, 10),
      kline: parseInt($("v7-o-kline").value, 10),
      row: parseInt($("v7-o-row").value, 10)
    };
  }

  function preview() {
    var o = readPanel();
    document.body.style.setProperty("--v7-font", o.font + "px");
    document.body.style.setProperty("--v7-side-width", o.side + "px");
    document.body.style.setProperty("--v7-gap", o.gap + "px");
    document.body.style.setProperty("--v7-kline-h", o.kline + "px");
    document.body.style.setProperty("--v7-row-h", o.row + "px");
    var pv = $("v7-pv-kline");
    if (pv) { pv.style.height = Math.round(o.kline * 0.19) + "px"; }
  }

  function openPanel() {
    if (!panel) { buildPanel(); }
    panel.hidden = false;
    preview();
  }

  function closePanel() {
    if (!panel) { return; }
    panel.hidden = true;
    var mode = currentLayout();
    applyLayout(mode === "custom" ? "custom" : mode);
  }

  /* ---------- 35. 移动端抽屉（v3.4.0 修 R-01：≤768px 侧栏改为抽屉） ---------- */
  /** 绑定汉堡按钮 / 遮罩 / Esc / 点条目自动收起。 */
  function bindMobileNav() {
    var btn = $("menu-toggle");
    var side = $("side-nav");
    if (!btn || !side) { return; }
    var backdrop = $("sb-backdrop");
    if (!backdrop) {
      backdrop = document.createElement("div");
      backdrop.className = "sb-backdrop";
      backdrop.id = "sb-backdrop";
      backdrop.hidden = true;
      document.body.appendChild(backdrop);
    }

    function setOpen(open) {
      side.classList.toggle("open", open);
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      btn.setAttribute("aria-label", open ? "关闭导航" : "打开导航");
      backdrop.hidden = !open;
    }
    window.V7_SET_SIDEBAR = setOpen;

    btn.addEventListener("click", function (ev) {
      ev.stopPropagation();
      setOpen(!side.classList.contains("open"));
    });
    backdrop.addEventListener("click", function () { setOpen(false); });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") { setOpen(false); }
    });
    // 点侧栏条目后自动收起（移动端点完就走）
    side.addEventListener("click", function (ev) {
      var item = ev.target.closest ? ev.target.closest(".side-item") : null;
      if (item && window.matchMedia("(max-width: 768px)").matches) { setOpen(false); }
    });
    window.addEventListener("hashchange", function () { setOpen(false); });
    window.addEventListener("resize", function () {
      if (!window.matchMedia("(max-width: 768px)").matches) { setOpen(false); }
    });
  }


  /* 跨域导出（W7.shell） */
  W7.shell = W7.shell || {};
  W7.shell.applyLayout = applyLayout;
  W7.shell.bindCollapse = bindCollapse;
  W7.shell.bindLayout = bindLayout;
  W7.shell.bindLogo = bindLogo;
  W7.shell.bindMobileNav = bindMobileNav;
  W7.shell.currentLayout = currentLayout;
  W7.shell.customOpts = customOpts;
  W7.shell.openPanel = openPanel;
  W7.shell.preview = preview;
})();
