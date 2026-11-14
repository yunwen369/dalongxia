/* W7.portfolio——切割自 v7_app.js（RF-20261112-01 阶段4）。代码体原样保留。 */
(function () {
  "use strict";
  var W7 = window.W7 = window.W7 || {};
  var C = W7.core;
  var $ = C.$, toast = C.toast, apiJson = C.apiJson, queryParam = C.queryParam;
  var bindImport = function () { return W7.forms.bindImport.apply(null, arguments); };
  var currentId = function () { return W7.detail.currentId.apply(null, arguments); };
  var esc = function () { return W7.forms.esc.apply(null, arguments); };
  var injectAboutCard = function () { return W7.forms.injectAboutCard.apply(null, arguments); };
  var injectAiTestPanel = function () { return W7.ai.injectAiTestPanel.apply(null, arguments); };
  var injectFinSummaryCard = function () { return W7.ai.injectFinSummaryCard.apply(null, arguments); };
  var injectAlertsToolbar = function () { return W7.misc.injectAlertsToolbar.apply(null, arguments); };
  var injectComboPanel = function () { return W7.fund.injectComboPanel.apply(null, arguments); };
  var injectConnectorsTest = function () { return W7.misc.injectConnectorsTest.apply(null, arguments); };
  var injectConnectorsToolbar = function () { return W7.forms.injectConnectorsToolbar.apply(null, arguments); };
  var injectFundPanel = function () { return W7.fund.injectFundPanel.apply(null, arguments); };
  var injectKlineTools = function () { return W7.detail.injectKlineTools.apply(null, arguments); };
  var injectMoneyPanel = function () { return W7.data.injectMoneyPanel.apply(null, arguments); };
  var injectNewsPanel = function () { return W7.data.injectNewsPanel.apply(null, arguments); };
  var injectPortfolioToolbar = function () { return W7.forms.injectPortfolioToolbar.apply(null, arguments); };
  var injectQuoteChart = function () { return W7.detail.injectQuoteChart.apply(null, arguments); };
  var injectScreenerPanel = function () { return W7.data.injectScreenerPanel.apply(null, arguments); };
  var injectSectorPanel = function () { return W7.data.injectSectorPanel.apply(null, arguments); };
  var injectSettingsPrefs = function () { return W7.forms.injectSettingsPrefs.apply(null, arguments); };
  var injectStockPanel = function () { return W7.detail.injectStockPanel.apply(null, arguments); };
  var injectSymbolBar = function () { return W7.detail.injectSymbolBar.apply(null, arguments); };
  var injectSystemPanel = function () { return W7.misc.injectSystemPanel.apply(null, arguments); };
  var injectTableTools = function () { return W7.misc.injectTableTools.apply(null, arguments); };
  var injectWatchToolbar = function () { return W7.misc.injectWatchToolbar.apply(null, arguments); };
  var injectYjbPanel = function () { return W7.fund.injectYjbPanel.apply(null, arguments); };
  var injectNavTrendPanel = function () { return W7.fund.injectNavTrendPanel.apply(null, arguments); };
  var preview = function () { return W7.shell.preview.apply(null, arguments); };
  var secFixed = function () { return W7.data.secFixed.apply(null, arguments); };
  var secSigned = function () { return W7.data.secSigned.apply(null, arguments); };
  var secSignedText = function () { return W7.data.secSignedText.apply(null, arguments); };

  /* ---------- 17. 仪表板持久化（D-03 / D-07 参数） ---------- */
  var pendingWidgetId = "";

  function bindDashboardPersist() {
    document.addEventListener("click", function (event) {
      var addBtn = event.target.closest ? event.target.closest(".widget-add") : null;
      if (addBtn && addBtn.getAttribute("data-id")) { pendingWidgetId = addBtn.getAttribute("data-id"); return; }
      if (event.target && event.target.id === "v6-add-ok" && pendingWidgetId) {
        var wid = pendingWidgetId;
        pendingWidgetId = "";
        // v3.7.0（⑤）：参数取真实控件（三级板块选择器 + 周期 + 起始）
        var psym = $("v7p-symbol"), pper = $("v7p-period"), pstart = $("v7p-start");
        var bp = document.getElementById("v7p-board-bp");
        var bpBoard = bp ? bp.querySelector('input[data-r="search"]') : null;
        var bpKind = bp ? bp.querySelector('select[data-r="kind"]') : null;
        var params = {
          symbol: psym ? (psym.value || "").trim() : "",
          period: pper ? pper.value : "",
          start: pstart ? (pstart.value || "") : "",
          board: bpBoard ? (bpBoard.value || "").trim() : "",
          kind: bpKind ? bpKind.value : ""
        };
        // 空值不入库，避免 params 里塞空串
        Object.keys(params).forEach(function (k) { if (!params[k]) { delete params[k]; } });
        OB.http.getJSON("/api/dashboards").catch(function () { return null; })
          .then(function (j) {
            var data = (j && j.data) || {};
            var list = data.dashboards || [];
            var target = list.filter(function (x) { return x.id === data.activeId; })[0] || list[0];
            if (!target) { toast("没有可用的仪表板配置"); return; }
            target.widgets = target.widgets || [];
            if (!target.widgets.some(function (w) { return w.id === wid; })) {
              target.widgets.push({ id: wid, span: 48, params: params });
            }
            apiJson("/api/dashboards", { method: "PUT", body: JSON.stringify({
              dashboards: list, activeId: data.activeId, groups: data.groups || [] }) })
              .then(function (res) {
                var ok = res.ok && res.body && !res.body.error;
                toast(ok ? "已添加到「" + (target.name || "仪表板") + "」并保存（刷新不丢）"
                    : ((res.body && res.body.error) || "仪表板保存失败"));
              });
          });
      }
    }, true);
  }

  /* ---------- 21. 仪表板栅格拖拽排序（恢复旧版 drag 能力，落库） ---------- */
  /** 宽度档位 → 分数标签（与 views.js 演示卡同口径）。 */
  var V7_SPAN_LABEL = { 12: "1/4", 16: "1/3", 20: "5/12", 24: "1/2", 28: "7/12", 32: "2/3", 48: "整行" };

  function bindGridDrag(grid, target, list, data) {
    var dragId = "";
    grid.addEventListener("dragstart", function (e) {
      var item = e.target.closest(".demo-widget");
      if (!item) { return; }
      dragId = item.getAttribute("data-id");
      item.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
    });
    grid.addEventListener("dragend", function () {
      Array.prototype.forEach.call(grid.querySelectorAll(".demo-widget"), function (x) {
        x.classList.remove("dragging");
      });
    });
    grid.addEventListener("dragover", function (e) {
      e.preventDefault();
      var over = e.target.closest(".demo-widget");
      if (!over || over.getAttribute("data-id") === dragId) { return; }
      var dragging = grid.querySelector('.demo-widget[data-id="' + dragId + '"]');
      if (!dragging || over === dragging) { return; }
      var rect = over.getBoundingClientRect();
      var after = (e.clientY - rect.top) > rect.height / 2;
      grid.insertBefore(dragging, after ? over.nextSibling : over);
    });
    grid.addEventListener("drop", function (e) {
      e.preventDefault();
      var order = Array.prototype.map.call(grid.querySelectorAll(".demo-widget"), function (x) {
        return x.getAttribute("data-id");
      });
      var byId = {};
      (target.widgets || []).forEach(function (w) { byId[w.id] = w; });
      target.widgets = order.map(function (id) { return byId[id] || { id: id, span: 48, params: {} }; });
      apiJson("/api/dashboards", { method: "PUT", body: JSON.stringify({
        dashboards: list, activeId: data.activeId, groups: data.groups || [] }) })
        .then(function (r) {
          var ok = r.ok && r.body && !r.body.error;
          toast(ok ? "布局顺序已保存" : ((r.body && r.body.error) || "保存失败"));
        });
    });
    // 预览卡「移除」：与画布一致——删组件并落库（BUG-20261112-04 恢复该能力）
    grid.addEventListener("click", function (e) {
      var btn = e.target.closest(".dw-remove");
      if (!btn) { return; }
      e.preventDefault();
      e.stopPropagation();
      var id = btn.getAttribute("data-remove");
      var before = (target.widgets || []).length;
      target.widgets = (target.widgets || []).filter(function (w) { return w.id !== id; });
      if (target.widgets.length === before) { return; }
      apiJson("/api/dashboards", { method: "PUT", body: JSON.stringify({
        dashboards: list, activeId: data.activeId, groups: data.groups || [] }) })
        .then(function (r) {
          var ok = r.ok && r.body && !r.body.error;
          toast(ok ? "组件已移除" : ((r.body && r.body.error) || "移除失败"));
          if (!ok) { return; }
          var node = btn.closest(".demo-widget");
          if (node) { node.remove(); }
          if (!grid.querySelector(".demo-widget")) {
            grid.innerHTML = '<div class="v-empty">仪表板为空，从组件库添加</div>';
          }
        });
    });
  }

  /* ---------- 21a. 卡片自由拉伸（v3.7.0 ⑩：宽 span 吸附档位 + 高 h 120–900px，松手落库） ---------- */
  var V7_SPAN_STEPS = [12, 16, 20, 24, 28, 32, 48];

  function v7SnapSpan(value) {
    var best = V7_SPAN_STEPS[0];
    V7_SPAN_STEPS.forEach(function (s) {
      if (Math.abs(s - value) < Math.abs(best - value)) { best = s; }
    });
    return best;
  }

  /** 右下角把手：横向拖 → 宽度吸附到合法档位；纵向拖 → 高度 120–900px；松手 PUT 落库。 */
  function bindGridResize(grid, target, list, data) {
    grid.addEventListener("mousedown", function (e) {
      var handle = e.target.closest ? e.target.closest(".dw-resize") : null;
      if (!handle) { return; }
      e.preventDefault();
      e.stopPropagation();
      var item = handle.closest(".demo-widget");
      if (!item) { return; }
      var id = item.getAttribute("data-id");
      var widget = (target.widgets || []).filter(function (w) { return w.id === id; })[0];
      if (!widget) { return; }
      var startY = e.clientY, startX = e.clientX;
      var startH = Math.round(item.getBoundingClientRect().height);
      var startSpan = Number(widget.span) || 48;
      var colW = grid.getBoundingClientRect().width / 48;
      var curH = startH, curSpan = startSpan;
      var meta = item.querySelector(".dw-meta");
      item.classList.add("resizing");
      function paint() {
        item.style.height = curH + "px";
        item.style.gridColumn = "span " + curSpan;
        if (meta) {
          meta.textContent = "span " + curSpan + " · " + (V7_SPAN_LABEL[curSpan] || "—") +
            " / 48 列 · 高 " + curH + "px（松手保存）";
        }
      }
      function move(ev) {
        curH = Math.max(120, Math.min(900, Math.round(startH + (ev.clientY - startY))));
        if (colW > 0) {
          curSpan = v7SnapSpan(Math.max(12, Math.min(48, Math.round(startSpan + (ev.clientX - startX) / colW))));
        }
        paint();
      }
      function up() {
        document.removeEventListener("mousemove", move);
        document.removeEventListener("mouseup", up);
        item.classList.remove("resizing");
        widget.h = curH;
        widget.span = curSpan;
        item.setAttribute("data-h", String(curH));
        item.setAttribute("data-span", String(curSpan));
        apiJson("/api/dashboards", { method: "PUT", body: JSON.stringify({
          dashboards: list, activeId: data.activeId, groups: data.groups || [] }) })
          .then(function (r) {
            var ok = r.ok && r.body && !r.body.error;
            toast(ok ? "卡片尺寸已保存（span " + curSpan + " · 高 " + curH + "px）"
              : ((r.body && r.body.error) || "尺寸保存失败"));
          });
      }
      document.addEventListener("mousemove", move);
      document.addEventListener("mouseup", up);
    });
  }

  /** 把 /api/dashboards 的真实布局注入「48 栅格」预览卡。
   *
   * BUG-20261112-04：视图是异步渲染的，afterRoute 首次调用时栅格容器往往还没挂载，
   * 原实现直接 return 且无重试，导致同一页面在「切路由进入」时才注入、刷新进入时不注入，
   * 表现为时好时坏。此处改为退避重试（最多 12 次 / 约 7 秒）。
   *
   * @param {number} [attempt] 当前重试次数（内部递归用）
   * @returns {void} 无返回值
   */
  function injectDashboardDrag(attempt) {
    if (currentId() !== "dashboard") { return; }
    var grid = document.querySelector(".demo-dash-grid");
    if (!grid) {
      if ((attempt || 0) < 12) {
        setTimeout(function () { injectDashboardDrag((attempt || 0) + 1); }, 600);
      }
      return;
    }
    if (grid.getAttribute("data-v7-bound") === "1") { return; }
    Promise.all([
      OB.http.getJSON("/api/dashboards").catch(function () { return null; }),
      OB.http.getJSON("/api/widgets").catch(function () { return null; })
    ]).then(function (res) {
      var data = (res[0] && res[0].data) || {};
      var list = data.dashboards || [];
      var target = list.filter(function (x) { return x.id === data.activeId; })[0] || list[0];
      if (!target) { return; }
      var meta = {};
      // /api/widgets 的真实字段名是 items（不是 widgets），旧写法取不到标题与分类
      var reg = (res[1] && res[1].data) || {};
      (reg.items || reg.widgets || []).forEach(function (x) { meta[x.id] = x; });
      var ws = target.widgets || [];
      // BUG-20261112-04：此前覆写丢失 grid-column: span N，全部卡片退化成 1/48 列宽，
      // 被压成一条竖排文字。此处按 widget.span 还原列宽，并补齐标题/宽度/移除三段结构。
      grid.innerHTML = ws.length ? ws.map(function (x) {
        var m = meta[x.id] || {};
        var span = Number(x.span) || 48;
        // v3.7.0（⑩）：卡片高度 h（120–900px，后端 _clean_widget 已收）随渲染还原
        var h = Number(x.h) || 0;
        return '<div class="demo-widget cat-' + esc(m.category || "table") + '" draggable="true" data-id="' + esc(x.id) +
          '" data-span="' + span + '" data-h="' + h + '" style="grid-column: span ' + span +
          (h ? ";height:" + h + "px" : "") + '">' +
          '<div class="dw-head"><b>' + esc(x.id) + "</b><span>" + esc(m.title || x.id) + "</span></div>" +
          '<div class="dw-meta">span ' + span + " · " + (V7_SPAN_LABEL[span] || "—") + " / 48 列" +
          " · 高 " + (h ? h + "px" : "自动") + "</div>" +
          '<button class="dw-remove" type="button" draggable="false" data-remove="' + esc(x.id) + '">移除</button>' +
          '<span class="dw-resize" draggable="false" title="拖拽右下角自由拉伸：左右改宽度（档位）/ 上下改高度（120–900px）"></span>' +
          "</div>";
      }).join("") : '<div class="v-empty">仪表板为空，从组件库添加</div>';
      grid.setAttribute("data-v7-bound", "1");
      bindGridDrag(grid, target, list, data);
      bindGridResize(grid, target, list, data);
      var note = grid.closest(".card") && grid.closest(".card").querySelector(".card-note");
      if (note) { note.textContent = "拖拽卡片调顺序 · 拖右下角把手自由拉伸宽高，松手即保存到 /api/dashboards"; }
    }).catch(function () { /* 静默：预览卡属辅助视图，失败不影响主画布 */ });
  }

  /* ---------- 21b. 仪表板管理 CRUD + 右键菜单（v3.7.0 ④） ---------- */
  var _dashSel = "";       // 当前选中仪表板 id
  var _dashDelArm = "";    // 删除二次确认的 id（首次点击只「预备」，再点才执行）

  /** 读取仪表板配置（dashboards / activeId / groups 三件套）。 */
  function dashState() {
    return OB.http.getJSON("/api/dashboards").then(function (j) {
      var data = (j && j.data) || {};
      return { list: data.dashboards || [], activeId: data.activeId || "", groups: data.groups || [] };
    });
  }

  /** 保存仪表板配置（PUT /api/dashboards）；返回是否成功。 */
  function dashSave(st) {
    return apiJson("/api/dashboards", { method: "PUT", body: JSON.stringify({
      dashboards: st.list, activeId: st.activeId, groups: st.groups }) })
      .then(function (r) { return !!(r.ok && r.body && !r.body.error); })
      .catch(function () { return false; });
  }

  function dashNewId(list) {
    var used = {};
    list.forEach(function (x) { used[x.id] = 1; });
    var n = 1;
    while (used["dash" + n]) { n += 1; }
    return "dash" + n;
  }

  /** 仪表板管理页：工具栏（新建/重命名/设为当前/删除）+ 行选中 + 右键上下文菜单。 */
  function injectDashboardManager() {
    if (currentId() !== "dashboard-manage") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-dash-mgr")) { return; }
    var bar = document.createElement("div");
    bar.className = "v7-toolbar v7-dash-mgr";
    bar.innerHTML =
      '<input id="v7-dash-name" placeholder="仪表板名称（新建/重命名用）">' +
      '<button class="v7-btn primary" data-dash-act="new" type="button">＋ 新建仪表板</button>' +
      '<button class="v7-btn" data-dash-act="rename" type="button">重命名</button>' +
      '<button class="v7-btn" data-dash-act="activate" type="button">设为当前</button>' +
      '<button class="v7-btn danger" data-dash-act="delete" type="button">删除</button>' +
      '<span class="v7-hint">点选行选中；右键行弹出同一组操作 · 改动即时落库 /api/dashboards</span>';
    body.insertBefore(bar, body.firstChild);

    var menu = document.createElement("div");
    menu.className = "v7-ctx";
    menu.hidden = true;
    menu.innerHTML = [["new", "新建仪表板"], ["rename", "重命名"], ["activate", "设为当前"], ["delete", "删除"]]
      .map(function (x) { return '<button type="button" data-dash-act="' + x[0] + '">' + x[1] + "</button>"; })
      .join("");
    document.body.appendChild(menu);

    var delBtn = bar.querySelector('[data-dash-act="delete"]');
    function resetDelArm() {
      _dashDelArm = "";
      if (delBtn) { delBtn.textContent = "删除"; delBtn.classList.remove("armed"); }
    }
    function syncSel() {
      Array.prototype.forEach.call(body.querySelectorAll("tr[data-dash-id]"), function (tr) {
        var on = tr.getAttribute("data-dash-id") === _dashSel;
        tr.classList.toggle("sel", on);
        if (on) { tr.setAttribute("aria-selected", "true"); } else { tr.removeAttribute("aria-selected"); }
      });
      var cur = body.querySelector("tr[data-dash-id].sel");
      var input = document.getElementById("v7-dash-name");
      if (cur && input && !input.value) { input.value = cur.getAttribute("data-dash-name") || ""; }
    }
    /** 给表格行补 data 属性（顺序与 /api/dashboards 一致，views.js 同一数据源）。 */
    function bindRows(st) {
      var trs = Array.prototype.filter.call(body.querySelectorAll("table tr"), function (tr) {
        return tr.querySelectorAll("td").length > 0;
      });
      trs.forEach(function (tr, i) {
        var item = st.list[i];
        if (!item) { return; }
        tr.setAttribute("data-dash-id", item.id);
        tr.setAttribute("data-dash-name", item.name || item.id);
        tr.classList.add("v7-dash-row");
        tr.classList.toggle("is-active", item.id === st.activeId);
        var tag = tr.querySelector(".dash-active-tag");
        if (item.id === st.activeId && !tag) {
          var td = tr.querySelector("td");
          if (td) { td.insertAdjacentHTML("beforeend", ' <span class="dash-active-tag">当前</span>'); }
        }
      });
      if (!_dashSel || !st.list.some(function (x) { return x.id === _dashSel; })) {
        _dashSel = st.activeId || (st.list[0] && st.list[0].id) || "";
      }
      syncSel();
    }
    function reload(st) {
      if (st) { bindRows(st); return; }
      dashState().then(bindRows).catch(function () { /* 配置不可读：保持静态表 */ });
    }
    function act(kind) {
      menu.hidden = true;
      dashState().then(function (st) {
        var input = document.getElementById("v7-dash-name");
        var name = ((input && input.value) || "").trim();
        var cur = st.list.filter(function (x) { return x.id === _dashSel; })[0] || null;
        if (kind === "new") {
          if (!name) { toast("请先在输入框填写新仪表板名称"); return; }
          if (st.list.some(function (x) { return x.name === name; })) { toast("已存在同名仪表板"); return; }
          var id = dashNewId(st.list);
          st.list.push({ id: id, name: name, group: "自定义", widgets: [] });
          if (!st.activeId) { st.activeId = id; }
          dashSave(st).then(function (ok) {
            toast(ok ? "已新建「" + name + "」（可到组件库添加组件）" : "新建失败（PUT /api/dashboards）");
            if (!ok) { return; }
            _dashSel = id;
            if (input) { input.value = ""; }
            window.dispatchEvent(new HashChangeEvent("hashchange"));
          });
          return;
        }
        if (!cur) { toast("请先点选一行仪表板（或右键行）"); return; }
        if (kind === "rename") {
          if (!name) { toast("请在输入框填写新名称"); return; }
          cur.name = name;
          dashSave(st).then(function (ok) {
            toast(ok ? "已重命名为「" + name + "」" : "重命名失败");
            if (ok) { window.dispatchEvent(new HashChangeEvent("hashchange")); }
          });
          return;
        }
        if (kind === "activate") {
          st.activeId = cur.id;
          dashSave(st).then(function (ok) {
            toast(ok ? "「" + (cur.name || cur.id) + "」已设为当前仪表板" : "设置失败");
            if (ok) { reload(); }
          });
          return;
        }
        if (kind === "delete") {
          if (_dashDelArm !== cur.id) {
            _dashDelArm = cur.id;
            if (delBtn) { delBtn.textContent = "确认删除"; delBtn.classList.add("armed"); }
            setTimeout(resetDelArm, 4000);
            toast("再点一次「确认删除」删除「" + (cur.name || cur.id) + "」（含其组件配置）");
            return;
          }
          resetDelArm();
          if (st.list.length <= 1) { toast("至少保留一个仪表板（当前仅 1 个）"); return; }
          st.list = st.list.filter(function (x) { return x.id !== cur.id; });
          if (st.activeId === cur.id) { st.activeId = st.list[0].id; }
          dashSave(st).then(function (ok) {
            toast(ok ? "已删除「" + (cur.name || cur.id) + "」" : "删除失败");
            if (!ok) { return; }
            _dashSel = st.activeId;
            window.dispatchEvent(new HashChangeEvent("hashchange"));
          });
        }
      }).catch(function () { toast("仪表板配置读取失败"); });
    }

    bar.addEventListener("click", function (e) {
      var btn = e.target.closest ? e.target.closest("[data-dash-act]") : null;
      if (btn) { act(btn.getAttribute("data-dash-act")); }
    });
    menu.addEventListener("click", function (e) {
      var btn = e.target.closest ? e.target.closest("[data-dash-act]") : null;
      if (btn) { act(btn.getAttribute("data-dash-act")); }
    });
    body.addEventListener("click", function (e) {
      var tr = e.target.closest ? e.target.closest("tr[data-dash-id]") : null;
      if (!tr) { return; }
      _dashSel = tr.getAttribute("data-dash-id");
      resetDelArm();
      var input = document.getElementById("v7-dash-name");
      if (input) { input.value = tr.getAttribute("data-dash-name") || ""; }
      syncSel();
    });
    body.addEventListener("contextmenu", function (e) {
      var tr = e.target.closest ? e.target.closest("tr[data-dash-id]") : null;
      if (!tr) { return; }
      e.preventDefault();
      _dashSel = tr.getAttribute("data-dash-id");
      resetDelArm();
      syncSel();
      menu.hidden = false;
      menu.style.left = Math.min(e.clientX, Math.max(8, window.innerWidth - 168)) + "px";
      menu.style.top = Math.min(e.clientY, Math.max(8, window.innerHeight - 176)) + "px";
    });
    document.addEventListener("click", function (e) {
      if (!menu.hidden && !menu.contains(e.target)) { menu.hidden = true; }
    });
    reload();
  }

  /* ---------- 22. 组合回测 / 定投计算器（v3.4.0 恢复） ---------- */
  var btCache = null;   // {html, dates, curve}：12s 动态刷新会重渲染视图，需回填
  var dcaCache = null;  // {html}

  /** 拉取基金全量净值（自动翻页），返回按日期升序的 [{date, nav}]。 */
  function fetchNav(code) {
    function page(n) {
      return apiJson("/api/fund/nav?code=" + encodeURIComponent(code) + "&size=500&page=" + n)
        .then(function (r) {
          var data = (r.body && r.body.data) || {};
          return { items: data.items || [], total: data.total_pages || 1, err: r.body && r.body.error };
        });
    }
    return page(1).then(function (first) {
      if (first.err) { throw new Error(first.err); }
      var pages = [first.items];
      var chain = Promise.resolve();
      for (var i = 2; i <= Math.min(first.total, 4); i += 1) {
        chain = chain.then((function (n) {
          return function () { return page(n).then(function (x) { pages.push(x.items); }); };
        })(i));
      }
      return chain.then(function () {
        var all = [];
        pages.forEach(function (list) { all = all.concat(list); });
        var byDate = {};
        all.forEach(function (x) { if (x && x.date) { byDate[x.date] = Number(x.nav); } });
        return Object.keys(byDate).sort().map(function (d) { return { date: d, nav: byDate[d] }; })
          .filter(function (x) { return isFinite(x.nav) && x.nav > 0; });
      });
    });
  }

  function statKpis(items) {
    return '<div class="v-kpi-grid">' + items.map(function (kv) {
      return '<div class="v-kpi"><span class="k-label">' + esc(kv[0]) + '</span>' +
        '<span class="k-value ' + (kv[2] || "") + '">' + esc(kv[1]) + '</span></div>';
    }).join("") + "</div>";
  }

  function runBacktest() {
    var box = $("bt-result");
    var hint = $("bt-hint");
    if (!box) { return; }
    var codes = (($("bt-codes") || {}).value || "").split(/[,，\s]+/).filter(Boolean);
    var weightsRaw = (($("bt-weights") || {}).value || "").split(/[,，\s]+/).filter(Boolean).map(Number);
    var cash = parseFloat((($("bt-cash") || {}).value || "100000"));
    var range = parseInt((($("bt-range") || {}).value || "250"), 10);
    if (!codes.length) { if (hint) { hint.textContent = "请填写至少一个基金代码"; } return; }
    var weights = codes.map(function (_, i) {
      return isFinite(weightsRaw[i]) && weightsRaw[i] > 0 ? weightsRaw[i] : 0;
    });
    var sumW = weights.reduce(function (a, b) { return a + b; }, 0);
    weights = sumW > 0 ? weights.map(function (w) { return w / sumW; })
      : codes.map(function () { return 1 / codes.length; });
    box.innerHTML = '<div class="loading">回测计算中…</div>';
    Promise.all(codes.map(fetchNav)).then(function (sets) {
      var maps = sets.map(function (rows) {
        var m = {};
        rows.forEach(function (r) { m[r.date] = r.nav; });
        return m;
      });
      // 取各基金日期交集
      var dates = Object.keys(maps[0]).filter(function (d) {
        return maps.every(function (m) { return m[d] != null; });
      }).sort();
      if (dates.length < 20) { throw new Error("可用共同交易日不足（" + dates.length + " 天）"); }
      dates = dates.slice(-range);
      var base = dates.map(function (d) { return maps.map(function (m) { return m[d]; }); });
      var curve = base.map(function (row) {
        var acc = 0;
        row.forEach(function (v, i) { acc += (v / base[0][i]) * weights[i]; });
        return acc;
      });
      var equity = curve.map(function (v) { return +((v - 1) * cash).toFixed(2); });
      var totalRet = curve[curve.length - 1] - 1;
      var days = dates.length;
      var annual = Math.pow(curve[curve.length - 1], 250 / days) - 1;
      var peak = curve[0], mdd = 0;
      curve.forEach(function (v) { peak = Math.max(peak, v); mdd = Math.min(mdd, v / peak - 1); });
      var rets = [];
      for (var i = 1; i < curve.length; i += 1) { rets.push(curve[i] / curve[i - 1] - 1); }
      var mean = rets.reduce(function (a, b) { return a + b; }, 0) / (rets.length || 1);
      var sd = Math.sqrt(rets.reduce(function (a, b) { return a + (b - mean) * (b - mean); }, 0) / (rets.length || 1));
      var sharpe = sd > 0 ? (mean / sd) * Math.sqrt(250) : 0;
      var pct = function (v) { return (v >= 0 ? "+" : "") + (v * 100).toFixed(2) + "%"; };
      var btHtml = statKpis([
        ["区间收益", pct(totalRet), totalRet >= 0 ? "up" : "down"],
        ["年化收益", pct(annual), annual >= 0 ? "up" : "down"],
        ["最大回撤", pct(mdd), "down"],
        ["夏普比率", sharpe.toFixed(2)],
        ["交易日", days + " 天"],
        ["期末权益", equity[equity.length - 1].toLocaleString() + " 元"]
      ]) + '<div id="bt-chart" style="height:230px;margin-top:8px"></div>';
      btCache = { html: btHtml, dates: dates, curve: curve };
      box.innerHTML = btHtml;
      if (window.echarts) {
        var chart = OB.chart.init($("bt-chart"));
        chart.setOption({
          animation: false, grid: { left: 8, right: 12, top: 12, bottom: 8, containLabel: true },
          xAxis: { type: "category", data: dates, axisLabel: { fontSize: 9 } },
          yAxis: { type: "value", scale: true, axisLabel: { fontSize: 9 } },
          series: [{ name: "组合净值", type: "line", showSymbol: false, data: curve,
            lineStyle: { width: 1.6, color: "#4a9bff" },
            areaStyle: { color: "rgba(74,155,255,.14)" } }]
        });
        window.__btChart = chart;
      }
      if (hint) { hint.textContent = "已按 " + codes.length + " 只基金、" + days + " 个共同交易日计算"; }
    }).catch(function (e) {
      box.innerHTML = '<div class="v-empty">' + esc(e.message || "回测失败") + "</div>";
    });
  }

  function runDca() {
    var box = $("dca-result");
    if (!box) { return; }
    var code = (($("dca-code") || {}).value || "").trim();
    var amount = parseFloat((($("dca-amount") || {}).value || "1000"));
    var freq = (($("dca-freq") || {}).value || "month");
    var range = parseInt((($("dca-range") || {}).value || "250"), 10);
    if (!code || !(amount > 0)) { box.innerHTML = '<div class="v-empty">请填写基金代码与定投金额</div>'; return; }
    box.innerHTML = '<div class="loading">定投模拟中…</div>';
    fetchNav(code).then(function (rows) {
      rows = rows.slice(-range);
      if (rows.length < 10) { throw new Error("净值序列不足"); }
      var picks = [];
      if (freq === "week") {
        picks = rows.filter(function (_, i) { return i % 5 === 0; });
      } else {
        var seen = {};
        picks = rows.filter(function (r) {
          var k = r.date.slice(0, 7);
          if (seen[k]) { return false; }
          seen[k] = 1; return true;
        });
      }
      var shares = 0, invested = 0;
      picks.forEach(function (r) { shares += amount / r.nav; invested += amount; });
      var last = rows[rows.length - 1];
      var value = shares * last.nav;
      var profit = value - invested;
      var ret = invested > 0 ? profit / invested : 0;
      var years = rows.length / 250;
      var annual = years > 0 ? Math.pow(value / invested, 1 / years) - 1 : 0;
      var pct = function (v) { return (v >= 0 ? "+" : "") + (v * 100).toFixed(2) + "%"; };
      dcaCache = { html: statKpis([
        ["定投次数", picks.length + " 次"],
        ["累计投入", invested.toLocaleString() + " 元"],
        ["累计份额", shares.toFixed(2)],
        ["期末市值", value.toFixed(2) + " 元"],
        ["累计收益", profit.toFixed(2) + " 元", profit >= 0 ? "up" : "down"],
        ["收益率", pct(ret), ret >= 0 ? "up" : "down"],
        ["年化（近似）", pct(annual), annual >= 0 ? "up" : "down"],
        ["区间", rows[0].date + " ~ " + last.date]
      ]) + '<div class="v-hint" style="margin-top:6px">口径：按选定频率定期定额买入，份额=当日单位净值；年化为现金流近似折算，非严格 XIRR。</div>' };
      box.innerHTML = dcaCache.html;
    }).catch(function (e) {
      box.innerHTML = '<div class="v-empty">' + esc(e.message || "定投计算失败") + "</div>";
    });
  }

  function watchViewRender() {
    var body = $("view-body");
    if (!body || body.getAttribute("data-v7-watch") === "1") { return; }
    body.setAttribute("data-v7-watch", "1");
    var timer = null;
    new MutationObserver(function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        injectSymbolBar();
        injectWatchToolbar();
        injectTableTools();
        injectPortfolioToolbar();
        injectConnectorsToolbar();
        injectSettingsPrefs();
        injectKlineTools();
        injectAboutCard();
        injectDashboardDrag();
        injectYjbPanel();
        injectStockPanel();
        injectQuoteChart();
        injectMoneyPanel();
        injectFundPanel();
        injectNavTrendPanel();
        injectComboPanel();
        injectNewsPanel();
        injectSectorPanel();
        injectPortfolioTrendChart();
        injectPortfolioAttrPanel();
        injectSystemPanel();
        injectAlertsToolbar();
        injectConnectorsTest();
        injectScreenerPanel();
        injectAiTestPanel();
        injectFinSummaryCard();
        bindImport();
        restoreCalcResults();
      }, 150);
    }).observe(body, { childList: true });
  }

  /** 动态刷新重渲染后回填回测/定投结果。 */
  function restoreCalcResults() {
    var id = currentId();
    if (id === "backtest" && btCache) {
      var box = $("bt-result");
      if (box && !box.querySelector(".v-kpi")) {
        box.innerHTML = btCache.html;
        if (window.echarts && $("bt-chart")) {
          var chart = OB.chart.init($("bt-chart"));
          chart.setOption({
            animation: false, grid: { left: 8, right: 12, top: 12, bottom: 8, containLabel: true },
            xAxis: { type: "category", data: btCache.dates, axisLabel: { fontSize: 9 } },
            yAxis: { type: "value", scale: true, axisLabel: { fontSize: 9 } },
            series: [{ name: "组合净值", type: "line", showSymbol: false, data: btCache.curve,
              lineStyle: { width: 1.6, color: "#4a9bff" },
              areaStyle: { color: "rgba(74,155,255,.14)" } }]
          });
        }
      }
    }
    if (id === "dca" && dcaCache) {
      var dbox = $("dca-result");
      if (dbox && !dbox.querySelector(".v-kpi")) { dbox.innerHTML = dcaCache.html; }
    }
  }

  /* ---------- 31. 持仓收益走势卡（v3.5.0 · F2/R13 前端消费） ---------- */
  var trendCache = null;  // {html, dates, mv, cost, pnl}：12s 动态刷新重渲染后回填

  /** 在 #v7-trend-chart 上渲染市值/成本/盈亏三线图。 */
  function trendChart(series) {
    var el = $("v7-trend-chart");
    if (!el || !window.echarts) { return; }
    var chart = OB.chart.init(el);
    chart.setOption({
      animation: false,
      legend: { top: 0, textStyle: { fontSize: 10 } },
      grid: { left: 8, right: 12, top: 26, bottom: 8, containLabel: true },
      tooltip: { trigger: "axis" },
      xAxis: { type: "category", data: series.dates, axisLabel: { fontSize: 9 } },
      yAxis: { type: "value", scale: true, axisLabel: { fontSize: 9 } },
      series: [
        { name: "市值", type: "line", showSymbol: false, data: series.mv,
          lineStyle: { width: 1.6, color: "#4a9bff" } },
        { name: "成本", type: "line", showSymbol: false, data: series.cost,
          lineStyle: { width: 1.2, color: "#8a8f98", type: "dashed" } },
        { name: "浮动盈亏", type: "line", showSymbol: false, data: series.pnl,
          lineStyle: { width: 1.4, color: "#e6a23c" } }
      ]
    });
    window.__trendChart = chart;
  }

  /** 组装走势卡 HTML 并写入 trendCache。items 为升序快照 [{date, market_value, cost, pnl}]。 */
  function renderTrend(items) {
    var dates = items.map(function (x) { return (x.date || "").slice(5); });
    var mv = items.map(function (x) { return x.market_value; });
    var cost = items.map(function (x) { return x.cost; });
    var pnl = items.map(function (x) { return x.pnl; });
    var last = items[items.length - 1] || {};
    var html = statKpis([
      ["快照数", String(items.length)],
      ["最新市值(元)", secFixed(last.market_value, 2)],
      ["最新成本(元)", secFixed(last.cost, 2)],
      ["浮动盈亏(元)", secSignedText(last.pnl, 2)],
      ["区间", (items[0] || {}).date + " ~ " + (last.date || "—")]
    ]) + '<div id="v7-trend-chart" style="height:240px;margin-top:8px"></div>' +
      '<div class="v7-sec-note">口径：交易日 15:05 后落盘的本地快照序列，缺日即断点、不补值（周末与节假日无数据点）。</div>';
    trendCache = { html: html, dates: dates, mv: mv, cost: cost, pnl: pnl };
    return html;
  }

  /** 持仓页注入收益走势卡（置于组合归因卡之上）。 */
  function injectPortfolioTrendChart() {
    if (currentId() !== "portfolio") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-trend")) { return; }
    var card = document.createElement("div");
    card.className = "card v7-trend";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">〽</span>持仓收益走势（快照 · 最近 90 自然日）</span></div>' +
      '<div class="v-body" id="v7-trend-body"><div class="loading">加载中…</div></div>';
    body.insertBefore(card, body.firstChild);
    var box = $("v7-trend-body");
    if (trendCache) {
      box.innerHTML = trendCache.html;
      trendChart(trendCache);
      return;
    }
    apiJson("/api/portfolio/trend?days=90").then(function (r) {
      var b = $("v7-trend-body");
      if (!b) { return; }
      if (r.body && r.body.error) { b.innerHTML = '<div class="v-empty">' + esc(r.body.error) + "</div>"; return; }
      var d = (r.body && r.body.data) || {};
      var items = d.items || [];
      if (!items.length) {
        b.innerHTML = '<div class="v-empty">' + esc(d.hint || d.caliber || "尚无快照数据") + "</div>";
        return;
      }
      b.innerHTML = renderTrend(items);
      trendChart(trendCache);
    }).catch(function () {
      var b2 = $("v7-trend-body");
      if (b2) { b2.innerHTML = '<div class="v-empty">持仓走势加载失败（/api/portfolio/trend）</div>'; }
    });
  }

  /* ---------- 30. 组合归因与风险（v3.4.0：恢复 portfolio/attribution · portfolio/risk） ---------- */
  var PA_TABS = ["归因", "风险"];
  var paTabState = "归因";

  /** 持仓页注入组合归因/风险卡。 */
  function injectPortfolioAttrPanel() {
    if (currentId() !== "portfolio") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-pa")) { return; }
    var card = document.createElement("div");
    card.className = "card v7-pa";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">◱</span>组合归因与风险</span>' +
        '<span class="v7-pa-tools">' + PA_TABS.map(function (t) {
          return '<button class="v7-tab' + (t === paTabState ? " on" : "") +
            '" data-pa-tab="' + t + '" type="button">' + t + "</button>";
        }).join("") + "</span></div>" +
        '<div class="v-body" id="v7-pa-body"><div class="loading">加载中…</div></div>';
    body.insertBefore(card, body.firstChild);
    loadPortfolioAttr(paTabState);
  }

  /** 切换归因/风险页签。 */
  function loadPortfolioAttr(tab) {
    paTabState = tab || paTabState;
    var box = $("v7-pa-body");
    if (!box) { return; }
    box.innerHTML = '<div class="loading">加载中…</div>';
    if (paTabState === "归因") { loadAttribution(); return; }
    loadPortfolioRisk();
  }

  /** 收益归因（portfolio/attribution）。 */
  function loadAttribution() {
    apiJson("/api/portfolio/attribution").then(function (r) {
      var box = $("v7-pa-body");
      if (!box) { return; }
      if (r.body && r.body.error) { box.innerHTML = '<div class="v-empty">' + esc(r.body.error) + "</div>"; return; }
      var d = (r.body && r.body.data) || {};
      var t = d.totals || {};
      if (!t.count) {
        box.innerHTML = '<div class="v-empty">' + esc(d.caliber || "台账为空，无法归因") + "</div>" +
          ((d.missing || []).length ? '<div class="v7-sec-miss">' + d.missing.map(esc).join("；") + "</div>" : "");
        return;
      }
      var html = statKpis([
        ["持仓数", String(t.count)],
        ["市值(元)", secFixed(t.value, 2)],
        ["成本(元)", secFixed(t.cost, 2)],
        ["浮动盈亏(元)", secSignedText(t.pnl, 2)],
        ["盈亏率", t.pnl_pct == null ? "—" : secSignedText(t.pnl_pct, 2, "%")]
      ]);
      html += '<div class="v7-hint" style="margin:10px 0 4px">收益贡献（贡献=权重×盈亏率，单位百分点）</div>' +
        '<div class="v7-preview-table"><table><tr><th>标的</th><th>市场</th><th>行业</th>' +
        "<th>市值</th><th>权重%</th><th>盈亏%</th><th>贡献</th><th>取价源</th></tr>" +
        (d.items || []).map(function (x) {
          return "<tr><td>" + esc(x.name) + ' <span style="opacity:.6">' + esc(x.symbol) + "</span></td><td>" +
            esc(x.market_label) + "</td><td>" + esc(x.industry || "—") + "</td><td>" + secFixed(x.value, 2) +
            "</td><td>" + secFixed(x.weight, 2) + "</td><td>" + secSigned(x.pnl_pct, 2, "%") + "</td><td>" +
            secSigned(x.contribution, 2) + "</td><td>" + esc(x.price_source || "—") + "</td></tr>";
        }).join("") + "</table></div>";
      if ((d.by_industry || []).length) {
        html += '<div class="v7-hint" style="margin:10px 0 4px">行业暴露</div>' +
          '<div class="v7-preview-table"><table><tr><th>行业</th><th>市值</th><th>权重%</th><th>只数</th></tr>' +
          d.by_industry.map(function (x) {
            return "<tr><td>" + esc(x.name) + "</td><td>" + secFixed(x.value, 2) + "</td><td>" +
              secFixed(x.weight, 2) + "</td><td>" + esc(String(x.count)) + "</td></tr>";
          }).join("") + "</table></div>";
      }
      var c = d.concentration || {};
      html += '<div class="v7-hint" style="margin:10px 0 4px">集中度：HHI ' + secFixed(c.hhi, 4) +
        "（" + esc(c.level || "—") + "，≥0.25 偏集中）</div>";
      if ((c.top5 || []).length) {
        html += '<div class="v7-preview-table"><table><tr><th>前五大</th><th>代码</th><th>权重%</th></tr>' +
          c.top5.map(function (x) {
            return "<tr><td>" + esc(x.name) + "</td><td>" + esc(x.symbol) + "</td><td>" + secFixed(x.weight, 2) + "</td></tr>";
          }).join("") + "</table></div>";
      }
      if ((d.scenarios || []).length) {
        html += '<div class="v7-hint" style="margin:10px 0 4px">情景（单市场 −10% 线性近似，非压力测试）</div>' +
          '<div class="v7-preview-table"><table><tr><th>情景</th><th>影响额(元)</th><th>影响比例%</th></tr>' +
          d.scenarios.map(function (x) {
            return "<tr><td>" + esc(x.label) + "</td><td>" + secSigned(x.impact, 2) + "</td><td>" +
              (x.impact_pct == null ? "—" : secSigned(x.impact_pct, 2)) + "</td></tr>";
          }).join("") + "</table></div>";
      }
      if (d.caliber) { html += '<div class="v7-sec-note">口径：' + esc(d.caliber) + "</div>"; }
      if ((d.missing || []).length) { html += '<div class="v7-sec-miss">缺价/缺汇率：' + d.missing.map(esc).join("；") + "</div>"; }
      box.innerHTML = html;
    }).catch(function () {   // F14：失败态
      var b = $("v7-pa-body");
      if (b) { b.innerHTML = '<div class="v-empty">组合归因加载失败（/api/portfolio/attribution）</div>'; }
    });
  }

  /** 组合风险指标（portfolio/risk）。 */
  function loadPortfolioRisk() {
    apiJson("/api/portfolio/risk").then(function (r) {
      var box = $("v7-pa-body");
      if (!box) { return; }
      if (r.body && r.body.error) { box.innerHTML = '<div class="v-empty">' + esc(r.body.error) + "</div>"; return; }
      var d = (r.body && r.body.data) || {};
      if (!d.available) {
        box.innerHTML = '<div class="v-empty">' + esc(d.caliber || "组合风险暂不可用") + "</div>" +
          ((d.missing || []).length ? '<div class="v7-sec-miss">' + d.missing.map(esc).join("；") + "</div>" : "");
        return;
      }
      var m = d.metrics || {};
      var html = statKpis([
        ["年化收益", secSignedText(m.annual_return, 2, "%")],
        ["年化波动", secFixed(m.annual_vol, 2) + "%"],
        ["最大回撤", secFixed(m.max_drawdown, 2) + "%"],
        ["回撤天数", m.max_drawdown_days == null ? "—" : String(m.max_drawdown_days)],
        ["夏普", secFixed(m.sharpe, 2)],
        ["β", secFixed(m.beta, 2)],
        ["α", secSignedText(m.alpha, 2, "%")],
        ["R²", secFixed(m.r2, 2)],
        ["样本天数", String(d.sample_days || 0)]
      ]);
      if (!d.reliable) {
        html += '<div class="v7-sec-miss">样本仅 ' + esc(String(d.sample_days || 0)) + " 个交易日，低于可靠性阈值：仅给数值、不给结论标签</div>";
      }
      if ((d.weights || []).length) {
        html += '<div class="v7-hint" style="margin:10px 0 4px">纳入序列的权重（现金与场外基金不参与）</div>' +
          '<div class="v7-preview-table"><table><tr><th>标的</th><th>代码</th><th>权重%</th></tr>' +
          d.weights.map(function (x) {
            return "<tr><td>" + esc(x.name) + "</td><td>" + esc(x.symbol) + "</td><td>" + secFixed(x.weight, 2) + "</td></tr>";
          }).join("") + "</table></div>";
      }
      if ((d.correlation || []).length) {
        html += '<div class="v7-hint" style="margin:10px 0 4px">两两相关性（>0.8 标红：同涨同跌，分散失效）</div>' +
          '<div class="v7-preview-table"><table><tr><th>标的 A</th><th>标的 B</th><th>相关系数</th><th>提示</th></tr>' +
          d.correlation.map(function (x) {
            var v = x.value == null ? '<span style="opacity:.55">—</span>'
              : '<span' + (x.high ? ' class="v7-flag-hi"' : "") + ">" + secFixed(x.value, 3) + "</span>";
            return "<tr><td>" + esc(x.left) + "</td><td>" + esc(x.right) + "</td><td>" + v + "</td><td>" +
              (x.high ? '<span class="v7-flag-hi">高相关</span>' : "—") + "</td></tr>";
          }).join("") + "</table></div>";
      }
      if (d.caliber) { html += '<div class="v7-sec-note">口径：' + esc(d.caliber) + "</div>"; }
      if ((d.missing || []).length) { html += '<div class="v7-sec-miss">未纳入：' + d.missing.map(esc).join("；") + "</div>"; }
      box.innerHTML = html;
    }).catch(function () {   // F14：失败态
      var b = $("v7-pa-body");
      if (b) { b.innerHTML = '<div class="v-empty">组合风险加载失败（/api/portfolio/risk）</div>'; }
    });
  }


  /* 跨域导出（W7.portfolio） */
  W7.portfolio = W7.portfolio || {};
  W7.portfolio.bindDashboardPersist = bindDashboardPersist;
  W7.portfolio.fetchNav = fetchNav;
  W7.portfolio.injectDashboardDrag = injectDashboardDrag;
  W7.portfolio.injectDashboardManager = injectDashboardManager;   // v3.7.0 ④：CRUD + 右键
  W7.portfolio.injectPortfolioAttrPanel = injectPortfolioAttrPanel;
  W7.portfolio.injectPortfolioTrendChart = injectPortfolioTrendChart;
  W7.portfolio.loadPortfolioAttr = loadPortfolioAttr;
  W7.portfolio.restoreCalcResults = restoreCalcResults;
  W7.portfolio.runBacktest = runBacktest;
  W7.portfolio.runDca = runDca;
  W7.portfolio.statKpis = statKpis;
  W7.portfolio.watchViewRender = watchViewRender;
})();
