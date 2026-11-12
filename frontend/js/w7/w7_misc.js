/* W7.misc——切割自 v7_app.js（RF-20261112-01 阶段4）。代码体原样保留。 */
(function () {
  "use strict";
  var W7 = window.W7 = window.W7 || {};
  var C = W7.core;
  var $ = C.$, toast = C.toast, apiJson = C.apiJson, queryParam = C.queryParam;
  var currentId = function () { return W7.detail.currentId.apply(null, arguments); };
  var esc = function () { return W7.forms.esc.apply(null, arguments); };
  var markIndicator = function () { return W7.detail.markIndicator.apply(null, arguments); };
  var markPeriod = function () { return W7.detail.markPeriod.apply(null, arguments); };
  var preview = function () { return W7.shell.preview.apply(null, arguments); };
  var secFixed = function () { return W7.data.secFixed.apply(null, arguments); };
  var statKpis = function () { return W7.portfolio.statKpis.apply(null, arguments); };

  /* ---------- 11. 组件参数（W8） ---------- */
  function bindWidgetParams() {
    var modal = $("v6-add-modal");
    if (!modal) { return; }
    new MutationObserver(function () {
      if (modal.hidden) { return; }
      var body = modal.querySelector(".v6-modal-body");
      if (!body || body.querySelector(".v7-params")) { return; }
      var box = document.createElement("div");
      box.className = "v7-params";
      box.innerHTML =
        '<div class="v7-field"><span>标的（可选）</span>' +
          '<input id="v7p-symbol" placeholder="如 600519" style="width:120px"></div>' +
        '<div class="v7-field"><span>周期</span><select id="v7p-period">' +
          '<option>近 5 日</option><option>近 20 日</option><option>近 60 日</option></select></div>' +
        '<div class="v7-field"><span>板块（可选）</span><select id="v7p-board">' +
          '<option>不限</option><option>半导体</option><option>新能源</option></select></div>';
      body.appendChild(box);
      var note = modal.querySelector(".v6-modal-note");
      if (note) {
        note.textContent = "参数随组件一并保存到仪表板（PUT /api/dashboards）；原型演示不写后端。";
      }
    }).observe(modal, { attributes: true, attributeFilter: ["hidden"] });
  }

  /* ---------- 12. 自选管理工具栏（保留双卡） ---------- */
  var DEMO_WL = [
    { code: "600519", name: "贵州茅台" }, { code: "300750", name: "宁德时代" },
    { code: "017811", name: "易方达蓝筹精选" }
  ];

  function fillWatchOptions() {
    var sel = $("v7-wl-select");
    if (!sel) { return; }
    function render(items) {
      sel.innerHTML = '<option value="">选择标的…</option>' + items.map(function (x) {
        return '<option value="' + esc(x.code) + '">' + esc(x.code) + " " + esc(x.name) + "</option>";
      }).join("");
    }
    OB.http.getJSON("/api/watchlist")
      .then(function (j) {
        var items = (j && j.data && j.data.items) || [];
        render(items.length ? items : DEMO_WL);
      })
      .catch(function () { render(DEMO_WL); });
  }

  function injectWatchToolbar() {
    if (currentId() !== "watchlist") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-toolbar")) { return; }
    var bar = document.createElement("div");
    bar.className = "v7-toolbar";
    bar.innerHTML =
      '<button class="v7-btn primary" data-act="watch-add" type="button">＋ 添加自选</button>' +
      '<select id="v7-wl-select"><option value="">选择标的…</option></select>' +
      '<button class="v7-btn" data-act="watch-edit-sel" type="button">编辑</button>' +
      '<button class="v7-btn danger" data-act="watch-del-sel" type="button">删除</button>' +
      '<span class="v7-hint">W-01 / W-02 双卡保持不变，管理操作集中在工具栏</span>';
    body.insertBefore(bar, body.firstChild);
    fillWatchOptions();
  }

  /* ---------- 14. 表格工具：导出 / 排序 / 分页 / 分享（D-08 / D-18） ---------- */
  function injectTableTools() {
    var row = document.querySelector(".view-title-row");
    if (!row) { return; }
    if (!row.querySelector(".v7-share")) {
      var share = document.createElement("button");
      share.className = "v7-btn v7-share";
      share.type = "button";
      share.textContent = "复制链接";
      share.addEventListener("click", function () {
        var url = location.href;
        if (navigator.clipboard) { navigator.clipboard.writeText(url); }
        toast("链接已复制：" + url.slice(0, 64));
      });
      row.appendChild(share);
    }
    var table = document.querySelector("#view-body table");
    if (!table || row.querySelector(".v7-export")) { return; }
    var exp = document.createElement("button");
    exp.className = "v7-btn v7-export";
    exp.type = "button";
    exp.textContent = "导出 CSV";
    exp.addEventListener("click", function () {
      var lines = [];
      Array.prototype.forEach.call(table.querySelectorAll("tr"), function (tr) {
        var cells = [];
        Array.prototype.forEach.call(tr.querySelectorAll("th,td"), function (cell) {
          cells.push('"' + String(cell.innerText || "").replace(/"/g, '""').trim() + '"');
        });
        if (cells.length) { lines.push(cells.join(",")); }
      });
      if (!lines.length) { toast("表格为空"); return; }
      var blob = new Blob(["\ufeff" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = (currentId() || "export") + ".csv";
      a.click();
      URL.revokeObjectURL(a.href);
      toast("已导出 CSV（" + (lines.length - 1) + " 行）");
    });
    row.appendChild(exp);

    // 排序：点击表头
    Array.prototype.forEach.call(table.querySelectorAll("thead th"), function (th, idx) {
      th.style.cursor = "pointer";
      th.title = "点击排序";
      th.addEventListener("click", function () {
        var tbody = table.querySelector("tbody");
        var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr"));
        var asc = th.getAttribute("data-asc") !== "1";
        th.setAttribute("data-asc", asc ? "1" : "0");
        rows.sort(function (a, b) {
          var av = (a.children[idx] || {}).innerText || "";
          var bv = (b.children[idx] || {}).innerText || "";
          var an = parseFloat(String(av).replace(/[^\d.\-]/g, ""));
          var bn = parseFloat(String(bv).replace(/[^\d.\-]/g, ""));
          if (isFinite(an) && isFinite(bn)) { return asc ? an - bn : bn - an; }
          return asc ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av));
        });
        rows.forEach(function (tr) { tbody.appendChild(tr); });
      });
    });
  }

  /* ---------- 19. 右键菜单（恢复旧版 ctxmenu） ---------- */
  var ctxMenu = null;

  function closeCtx() {
    if (ctxMenu) { ctxMenu.remove(); ctxMenu = null; }
  }

  function openCtx(x, y, items) {
    closeCtx();
    ctxMenu = document.createElement("div");
    ctxMenu.className = "v7-ctx";
    ctxMenu.innerHTML = items.map(function (it, i) {
      return it === "-" ? '<hr>' : '<button type="button" data-i="' + i + '">' + esc(it.label) + "</button>";
    }).join("");
    document.body.appendChild(ctxMenu);
    var w = ctxMenu.offsetWidth, h = ctxMenu.offsetHeight;
    ctxMenu.style.left = Math.min(x, window.innerWidth - w - 6) + "px";
    ctxMenu.style.top = Math.min(y, window.innerHeight - h - 6) + "px";
    ctxMenu.addEventListener("click", function (event) {
      var btn = event.target.closest("button[data-i]");
      if (!btn) { return; }
      var item = items[+btn.getAttribute("data-i")];
      closeCtx();
      if (item && item.run) { item.run(); }
    });
  }

  function bindContextMenu() {
    document.addEventListener("contextmenu", function (event) {
      var tr = event.target.closest ? event.target.closest("#view-body table tbody tr") : null;
      if (tr) {
        event.preventDefault();
        var code = (tr.children[0] || {}).innerText || "";
        var name = (tr.children[1] || {}).innerText || "";
        openCtx(event.clientX, event.clientY, [
          { label: "查看详情", run: function () {
            localStorage.setItem("openbb-symbol", code.trim());
            location.hash = "#/stock";
          } },
          { label: "加入自选", run: function () {
            apiJson("/api/watchlist", { method: "POST", body: JSON.stringify({ symbol: code.trim(), market: "a", name: name.trim() }) })
              .then(function (r) { toast(r.ok && r.body && !r.body.error ? "已加入自选" : ((r.body && r.body.error) || "失败")); });
          } },
          { label: "设为提醒", run: function () { location.hash = "#/alerts-edit"; } },
          "-",
          { label: "复制代码", run: function () {
            if (navigator.clipboard) { navigator.clipboard.writeText(code.trim()); }
            toast("已复制：" + code.trim());
          } },
          { label: "复制整行", run: function () {
            var line = Array.prototype.map.call(tr.children, function (c) { return (c.innerText || "").trim(); }).join("\t");
            if (navigator.clipboard) { navigator.clipboard.writeText(line); }
            toast("已复制整行");
          } },
          { label: "导出该表", run: function () {
            var btn = document.querySelector(".v7-export");
            if (btn) { btn.click(); }
          } }
        ]);
        return;
      }
      var kl = event.target.closest ? event.target.closest("#kline-chart") : null;
      if (kl) {
        event.preventDefault();
        openCtx(event.clientX, event.clientY, [
          { label: "周期：日K", run: function () { window.V6_APP && window.V6_APP.setPeriod("日k"); markPeriod("日k"); } },
          { label: "周期：周K", run: function () { window.V6_APP && window.V6_APP.setPeriod("周k"); markPeriod("周k"); } },
          { label: "周期：月K", run: function () { window.V6_APP && window.V6_APP.setPeriod("月k"); markPeriod("月k"); } },
          "-",
          { label: "指标：MA", run: function () { window.V6_APP && window.V6_APP.setIndicator("MA"); markIndicator("MA"); } },
          { label: "指标：MACD", run: function () { window.V6_APP && window.V6_APP.setIndicator("MACD"); markIndicator("MACD"); } },
          { label: "指标：KDJ", run: function () { window.V6_APP && window.V6_APP.setIndicator("KDJ"); markIndicator("KDJ"); } },
          "-",
          { label: "复制当前链接", run: function () {
            if (navigator.clipboard) { navigator.clipboard.writeText(location.href); }
            toast("已复制链接");
          } }
        ]);
      }
    });
    document.addEventListener("click", closeCtx);
    document.addEventListener("scroll", closeCtx, true);
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") { closeCtx(); } });
  }

  /* ---------- 31. 系统运维 / 提醒已读 / 连接器测速（v3.4.0） ---------- */
  W7.st.alertsHintText = "";
  /** 设置页注入「缓存与访问」运维卡（cache/stats · cache/clear · system/access）。 */
  function injectSystemPanel() {
    if (currentId() !== "settings") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-sys")) { return; }
    var card = document.createElement("div");
    card.className = "card v7-sys";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">⚙</span>缓存与访问</span>' +
        '<span class="card-note">缓存统计 / 清空缓存 / 访问方式</span></div>' +
        '<div class="v-body"><div id="v7-sys-body"><div class="loading">加载中…</div></div></div>';
    body.insertBefore(card, body.firstChild);
    loadSystemPanel();
  }

  /** 读取缓存统计与访问方式并渲染。 */
  function loadSystemPanel() {
    var box = $("v7-sys-body");
    if (!box) { return; }
    Promise.all([apiJson("/api/cache/stats"), apiJson("/api/system/access")]).then(function (res) {
      var target = $("v7-sys-body");
      if (!target) { return; }
      var cs = (res[0].body && res[0].body.data) || {};
      var ac = (res[1].body && res[1].body.data) || {};
      var html = statKpis([
        ["内存缓存", secFixed(cs.memory_mb, 2) + " MB"],
        ["磁盘缓存", secFixed(cs.disk_mb, 2) + " MB"],
        ["内存条目", String(cs.memory == null ? "—" : cs.memory)],
        ["磁盘条目", String(cs.disk == null ? "—" : cs.disk)],
        ["布局占用", secFixed(cs.layout_kb, 1) + " KB"]
      ]);
      html += '<div class="v7-kline-tools" style="margin:8px 0">' +
        '<button class="v7-btn" data-act="sys-cache-clear" type="button">清空缓存</button>' +
        '<span class="v7-hint" style="margin-left:8px">清空后下一次请求会重新取数（不影响持仓/自选/密钥）</span></div>';
      if ((cs.ttl_map || []).length) {
        html += '<div class="v7-hint" style="margin:8px 0 4px">缓存 TTL 口径</div>' +
          '<div class="v7-preview-table"><table><tr><th>缓存键</th><th>TTL(秒)</th><th>说明</th></tr>' +
          cs.ttl_map.map(function (x) {
            return "<tr><td>" + esc(x.key) + "</td><td>" + esc(String(x.ttl)) + "</td><td>" + esc(x.note || "") + "</td></tr>";
          }).join("") + "</table></div>";
      }
      html += '<div class="v7-hint" style="margin:10px 0 4px">访问入口（当前访问：' + esc(location.origin) +
        "；口令：" + (ac.password_required ? "已启用" : "未启用") +
        "；局域网：" + (ac.lan_visible ? "已放开" : "仅本机") + "）</div>";
      var points = ac.points || [];
      html += points.length
        ? '<div class="v7-preview-table"><table><tr><th>范围</th><th>入口</th><th>二维码</th></tr>' +
          points.map(function (p) {
            var qr = p.qr ? '<img src="' + p.qr + '" alt="QR" style="width:84px;height:84px;image-rendering:pixelated">' : "—";
            return "<tr><td>" + esc(p.label || p.scope) + "</td><td>" + esc(p.url || "") + "</td><td>" + qr + "</td></tr>";
          }).join("") + "</table></div>"
        : '<div class="v-empty">无可用入口</div>';
      if (ac.public && ac.public.url) {
        html += '<div class="v7-sec-note">公网临时地址：' + esc(ac.public.url) + "（重启会变）</div>";
      } else if (ac.public && ac.public.error) {
        html += '<div class="v7-sec-miss">公网：' + esc(ac.public.error) + "</div>";
      }
      if (ac.caliber) { html += '<div class="v7-sec-note">口径：' + esc(ac.caliber) + "</div>"; }
      if (cs.caliber) { html += '<div class="v7-sec-note">缓存口径：' + esc(cs.caliber) + "</div>"; }
      target.innerHTML = html;
    });
  }

  /** 提醒中心注入「全部标为已读」工具条（alerts/events/read）。 */
  function injectAlertsToolbar() {
    if (currentId() !== "alerts") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-alerts-bar")) { return; }
    var bar = document.createElement("div");
    bar.className = "v7-toolbar v7-alerts-bar";
    bar.innerHTML = '<span>未读提醒</span><b id="v7-alerts-unread">—</b>' +
      '<button class="v7-btn" data-act="alerts-read-all" type="button">全部标为已读</button>' +
      '<span class="v7-hint" id="v7-alerts-hint">' + esc(W7.st.alertsHintText) + "</span>";
    body.insertBefore(bar, body.firstChild);
    apiJson("/api/alerts/events?limit=1").then(function (r) {
      var d = (r.body && r.body.data) || {};
      var el = $("v7-alerts-unread");
      if (el) { el.textContent = String(d.unread == null ? "—" : d.unread); }
    });
  }

  /** 连接器页「逐个测速」结果区（connectors/test）。 */
  function injectConnectorsTest() {
    if (currentId() !== "connectors") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-cn-test")) { return; }
    var card = document.createElement("div");
    card.className = "card v7-cn-test";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">◎</span>连通性自检</span>' +
        '<button class="v7-btn" data-act="cn-test-all" type="button">逐个测速</button></div>' +
        '<div class="v-body"><div id="v7-cn-test"><div class="v7-hint">点「逐个测速」对各数据源跑一次真实探测（不读缓存、不改配置）</div></div></div>';
    body.insertBefore(card, body.firstChild);
  }

  /** 逐个跑 connectors/test，逐行追加结果（仅对后端提供探测实现的源）。 */
  function runConnectorTests() {
    var box = $("v7-cn-test");
    if (!box) { return; }
    box.innerHTML = '<div class="loading">读取数据源清单…</div>';
    apiJson("/api/connectors/status").then(function (r) {
      var list = ((r.body && r.body.data && r.body.data.datasources) || [])
        .filter(function (x) { return x.enabled !== false; });
      var target = $("v7-cn-test");
      if (!target) { return; }
      if (!list.length) { target.innerHTML = '<div class="v-empty">没有启用的数据源</div>'; return; }
      target.innerHTML = '<div class="v7-hint" style="margin-bottom:4px">共 ' + list.length +
        ' 个源，逐个真实探测（每源约 1–3 秒；后端仅对部分源提供探测实现，未实现的标「未提供探测」）…</div>' +
        '<div class="v7-preview-table"><table id="v7-cn-test-tbl">' +
        '<tr><th>数据源</th><th>结果</th><th>说明</th><th>探测时间</th></tr></table></div>';
      var idx = 0;
      function step() {
        var tbl = $("v7-cn-test-tbl");
        if (!tbl || idx >= list.length) {
          if (tbl && !$("v7-cn-sum")) {
            tbl.insertAdjacentHTML("afterend", '<div class="v7-hint" id="v7-cn-sum"></div>');
          }
          var okN = 0, probedN = 0;
          if (tbl) {
            Array.prototype.forEach.call(tbl.querySelectorAll("tr"), function (tr) {
              if (tr.getAttribute("data-probed") === "1") {
                probedN += 1;
                if (tr.getAttribute("data-ok") === "1") { okN += 1; }
              }
            });
          }
          var sum = $("v7-cn-sum");
          if (sum) {
            sum.textContent = "完成：可用 " + okN + " / 已探测 " + probedN + "（共 " + list.length +
              " 源，未提供探测 " + (list.length - probedN) + " 源）";
          }
          return;
        }
        var item = list[idx];
        var key = item.key || item.source || item.label || "";
        var label = item.label || key;
        idx += 1;
        apiJson("/api/connectors/test?source=" + encodeURIComponent(key), { method: "POST" }).then(function (tr) {
          var d = (tr.body && tr.body.data) || {};
          var err = (tr.body && tr.body.error) || "";
          var cell, detailText, probed;
          if (err) {
            cell = '<span style="opacity:.6">未提供探测</span>';
            detailText = err;
            probed = "0";
          } else if (d.ok) {
            cell = '<span style="color:#2fd6a4">可用</span>';
            detailText = d.detail || "—";
            probed = "1";
          } else {
            cell = '<span style="color:#ff6b78">不可用</span>';
            detailText = d.detail || "—";
            probed = "1";
          }
          var tbl2 = $("v7-cn-test-tbl");
          if (tbl2) {
            tbl2.insertAdjacentHTML("beforeend",
              '<tr data-ok="' + (d.ok ? "1" : "0") + '" data-probed="' + probed + '"><td>' + esc(label) +
              "</td><td>" + cell + "</td><td>" + esc(detailText) + "</td><td>" + esc(d.probed_at || "—") + "</td></tr>");
          }
          step();
        });
      }
      step();
    });
  }


  /* 跨域导出（W7.misc） */
  W7.misc = W7.misc || {};
  W7.misc.bindContextMenu = bindContextMenu;
  W7.misc.bindWidgetParams = bindWidgetParams;
  W7.misc.injectAlertsToolbar = injectAlertsToolbar;
  W7.misc.injectConnectorsTest = injectConnectorsTest;
  W7.misc.injectSystemPanel = injectSystemPanel;
  W7.misc.injectTableTools = injectTableTools;
  W7.misc.injectWatchToolbar = injectWatchToolbar;
  W7.misc.loadSystemPanel = loadSystemPanel;
  W7.misc.runConnectorTests = runConnectorTests;
})();
