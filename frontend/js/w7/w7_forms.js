/* W7.forms——切割自 v7_app.js（RF-20261112-01 阶段4）。代码体原样保留。 */
(function () {
  "use strict";
  var W7 = window.W7 = window.W7 || {};
  var C = W7.core;
  var $ = C.$, toast = C.toast, apiJson = C.apiJson, queryParam = C.queryParam;
  var currentId = function () { return W7.detail.currentId.apply(null, arguments); };
  var handleTabs = function () { return W7.detail.handleTabs.apply(null, arguments); };
  var loadEstimation = function () { return W7.fund.loadEstimation.apply(null, arguments); };
  var loadScreenerStatus = function () { return W7.data.loadScreenerStatus.apply(null, arguments); };
  var loadSystemPanel = function () { return W7.misc.loadSystemPanel.apply(null, arguments); };
  var preview = function () { return W7.shell.preview.apply(null, arguments); };
  var refreshYjb = function () { return W7.fund.refreshYjb.apply(null, arguments); };
  var runAiTest = function () { return W7.ai.runAiTest.apply(null, arguments); };
  var runBacktest = function () { return W7.portfolio.runBacktest.apply(null, arguments); };
  var runConnectorTests = function () { return W7.misc.runConnectorTests.apply(null, arguments); };
  var runDca = function () { return W7.portfolio.runDca.apply(null, arguments); };
  var runScreener = function () { return W7.data.runScreener.apply(null, arguments); };
  var saveEdit = function () { return W7.ai.saveEdit.apply(null, arguments); };
  var yjbQr = function () { return W7.fund.yjbQr.apply(null, arguments); };

  /* ---------- 9. 列表与表单写操作（真实接口） ---------- */
  function bindActions() {
    document.addEventListener("click", function (event) {
      var btn = event.target.closest("[data-act]");
      if (!btn) { handleTabs(event); return; }
      var act = btn.getAttribute("data-act");
      var id = btn.getAttribute("data-id");

      if (act === "est-toggle") {
        apiJson("/api/fund/estimation").then(function (r0) {
          var cur = ((r0.body && r0.body.data) || {}).enabled;
          apiJson("/api/fund/estimation/toggle", { method: "POST", body: JSON.stringify({ enabled: !cur }) })
            .then(function (r) {
              toast(r.ok && r.body && !r.body.error ? "盘中估值已" + (!cur ? "启用" : "关闭") : ((r.body && r.body.error) || "失败"));
              loadEstimation();
            });
        });
        return;
      }
      if (act === "yjb-toggle") {
        var en = ($("yjb-enabled") || {}).value === "1";
        apiJson("/api/fund/yjb/toggle", { method: "POST", body: JSON.stringify({ enabled: en }) })
          .then(function (r) { toast(r.ok && r.body && !r.body.error ? "开关已保存" : ((r.body && r.body.error) || "失败")); refreshYjb(); });
        return;
      }
      if (act === "yjb-consent") {
        var acc = ($("yjb-consent") || {}).value === "1";
        apiJson("/api/fund/yjb/consent", { method: "POST", body: JSON.stringify({ accepted: acc }) })
          .then(function (r) { toast(r.ok && r.body && !r.body.error ? "授权已提交" : ((r.body && r.body.error) || "失败")); refreshYjb(); });
        return;
      }
      if (act === "yjb-qr") { yjbQr(); return; }
      if (act === "yjb-poll") {
        var sid1 = btn.getAttribute("data-sid");
        apiJson("/api/fund/yjb/poll?session_id=" + encodeURIComponent(sid1)).then(function (r) {
          var d = (r.body && r.body.data) || {};
          toast("轮询结果：" + (d.state || d.status || (r.body && r.body.error) || "等待扫码"));
        });
        return;
      }
      if (act === "yjb-bind") {
        var sid2 = btn.getAttribute("data-sid");
        apiJson("/api/fund/yjb/bind", { method: "POST", body: JSON.stringify({ session_id: sid2 }) })
          .then(function (r) {
            toast(r.ok && r.body && !r.body.error ? "绑定成功" : ((r.body && r.body.error) || "绑定失败"));
            refreshYjb();
          });
        return;
      }
      if (act === "yjb-collect") {
        apiJson("/api/fund/yjb/collect").then(function (r) {
          var d = (r.body && r.body.data) || {};
          toast(r.ok && !(r.body && r.body.error)
            ? "采集完成：" + (d.count != null ? d.count + " 条" : "已更新")
            : ((r.body && r.body.error) || "采集失败"));
          refreshYjb();
        });
        return;
      }
      if (act === "yjb-unbind") {
        if (btn.getAttribute("data-armed") !== "1") {
          btn.setAttribute("data-armed", "1");
          btn.textContent = "确认解绑？";
          return;
        }
        apiJson("/api/fund/yjb/unbind", { method: "POST", body: JSON.stringify({ confirm: true }) })
          .then(function (r) { toast(r.ok && r.body && !r.body.error ? "已解绑" : ((r.body && r.body.error) || "解绑失败")); refreshYjb(); });
        return;
      }
      if (act === "bt-run") { runBacktest(); return; }
      if (act === "dca-run") { runDca(); return; }
      if (act === "pf-add") { location.hash = "#/portfolio-edit"; return; }
      if (act === "pf-del") {
        var pfSel = $("v7-pf-select");
        if (!pfSel || !pfSel.value) { toast("请先选择持仓"); return; }
        if (btn.getAttribute("data-armed") === "1") {
          apiJson("/api/portfolio/" + encodeURIComponent(pfSel.value), { method: "DELETE" })
            .then(function (r) {
              if (r.ok && r.body && !r.body.error) {
                toast("持仓已删除");
                window.dispatchEvent(new HashChangeEvent("hashchange"));
              } else { toast((r.body && r.body.error) || "删除失败"); }
            });
          return;
        }
        btn.setAttribute("data-armed", "1");
        btn.textContent = "确认删除？";
        return;
      }
      if (act === "cn-on" || act === "cn-off") {
        var cnSel = $("v7-cn-select");
        if (!cnSel || !cnSel.value) { toast("请先选择数据源"); return; }
        apiJson("/api/connectors/toggle", { method: "POST", body: JSON.stringify({
          source: cnSel.value, enabled: act === "cn-on" }) })
          .then(function (r) {
            var ok = r.ok && r.body && !r.body.error;
            toast(ok ? (act === "cn-on" ? "已启用 " : "已停用 ") + cnSel.value
              : ((r.body && r.body.error) || "操作失败"));
          });
        return;
      }
      if (act === "scr-run") {
        runScreener();
        return;
      }
      if (act === "scr-refresh") {
        apiJson("/api/screener/refresh", { method: "POST" }).then(function (r) {
          var d = (r.body && r.body.data) || {};
          toast(r.ok && r.body && !r.body.error
            ? (d.started ? "快照构建已启动（异步，进度见状态）" : (d.message || "已有构建任务在进行"))
            : "刷新失败");
          loadScreenerStatus();
        });
        return;
      }
      if (act === "ai-test") {
        runAiTest();
        return;
      }
      if (act === "sys-cache-clear") {
        apiJson("/api/cache/clear", { method: "POST" }).then(function (r) {
          var d = (r.body && r.body.data) || {};
          toast(r.ok && r.body && !r.body.error ? ("已清空缓存（" + (d.removed || 0) + " 个文件）") : "清空失败");
          loadSystemPanel();
        });
        return;
      }
      if (act === "alerts-read-all") {
        var hint = $("v7-alerts-hint");
        if (hint) { hint.textContent = "处理中…"; }
        apiJson("/api/alerts/events/read", { method: "POST" }).then(function (r) {
          var d = (r.body && r.body.data) || {};
          if (r.ok && r.body && !r.body.error) {
            var un = $("v7-alerts-unread");
            if (un) { un.textContent = String(d.unread == null ? 0 : d.unread); }
            W7.st.alertsHintText = "已标记 " + (d.marked || 0) + " 条为已读";
            if (hint) { hint.textContent = W7.st.alertsHintText; }
            toast(W7.st.alertsHintText);
            setTimeout(function () { window.dispatchEvent(new HashChangeEvent("hashchange")); }, 700);
            setTimeout(function () { W7.st.alertsHintText = ""; }, 8000);
          } else if (hint) {
            hint.textContent = (r.body && r.body.error) || "标记失败";
          }
        });
        return;
      }
      if (act === "cn-test-all") {
        runConnectorTests();
        return;
      }
      if (act === "about-log") {
        window.open("/api/logs/export", "_blank");
        toast("日志导出已开始下载");
        return;
      }
      if (act === "about-reset") {
        if (btn.getAttribute("data-armed") !== "1") {
          btn.setAttribute("data-armed", "1");
          btn.textContent = "确认恢复出厂？";
          return;
        }
        apiJson("/api/factory-reset", { method: "POST" }).then(function (r) {
          var ok = r.ok && r.body && !r.body.error;
          toast(ok ? "已恢复出厂设置" : ((r.body && r.body.error) || "失败"));
        });
        return;
      }
      if (act === "pref-save") {
        var pref = $("v7-pref-close");
        apiJson("/api/system/desktop-prefs", { method: "PUT", body: JSON.stringify({
          close_action: pref ? pref.value : "tray" }) })
          .then(function (r) {
            var ok = r.ok && r.body && !r.body.error;
            toast(ok ? "偏好已保存" : ((r.body && r.body.error) || "保存失败"));
          });
        return;
      }

      if (act === "watch-add") { location.hash = "#/watchlist-edit"; return; }
      if (act === "watch-edit-sel") {
        var sel = $("v7-wl-select");
        if (!sel || !sel.value) { toast("请先选择标的"); return; }
        location.hash = "#/watchlist-edit?id=" + encodeURIComponent(sel.value);
        return;
      }
      if (act === "watch-del-sel") {
        var sel2 = $("v7-wl-select");
        if (!sel2 || !sel2.value) { toast("请先选择标的"); return; }
        if (btn.getAttribute("data-armed") === "1") {
          apiJson("/api/watchlist/" + encodeURIComponent(sel2.value) + "?market=a", { method: "DELETE" })
            .then(function (r) {
              if (r.ok && r.body && !r.body.error) {
                toast("已删除 " + sel2.value);
                window.dispatchEvent(new HashChangeEvent("hashchange"));
              } else { toast((r.body && r.body.error) || "删除失败"); }
            });
          return;
        }
        btn.setAttribute("data-armed", "1");
        btn.textContent = "确认删除？";
        return;
      }
      if (act === "watch-edit") { location.hash = "#/watchlist-edit?id=" + encodeURIComponent(id); return; }
      if (act === "watch-del") {
        if (btn.getAttribute("data-armed") === "1") {
          toast("已删除 " + id + "（原型演示，未写后端）");
          var tr = btn.closest("tr");
          if (tr) { tr.style.opacity = ".35"; }
          return;
        }
        btn.setAttribute("data-armed", "1");
        btn.textContent = "确认删除？";
        toast("再次点击确认删除 " + id);
        return;
      }

      if (act === "edit-save") {
        saveEdit(btn.getAttribute("data-kind"), $("v7-form-hint"));
        return;
      }
      if (act === "edit-cancel") {
        if (history.length > 1) { history.back(); } else { location.hash = "#/home"; }
        return;
      }
      if (act === "edit-test") {
        apiJson("/api/alerts/evaluate?force=true", { method: "POST" }).then(function (r) {
          toast(r.ok ? "已执行一轮规则评估" : "评估失败");
        });
        return;
      }
      if (act === "edit-delete") {
        var ruleId = queryParam("id");
        if (!ruleId) { toast("缺少规则 ID"); return; }
        apiJson("/api/alerts/" + encodeURIComponent(ruleId), { method: "DELETE" }).then(function (r) {
          if (r.ok && r.body && !r.body.error) { toast("规则已删除"); setTimeout(function () { location.hash = "#/alerts"; }, 600); }
          else { toast((r.body && r.body.error) || "删除失败"); }
        });
        return;
      }

      if (act === "import-check") {
        if (!W7.st.importRows || !W7.st.importRows.length) { toast("请先选择 .csv / .xlsx 文件"); return; }
        renderImport(W7.st.importRows, W7.st.importFileName);
        return;
      }
      if (act === "import-confirm") {
        var okRows = (W7.st.importRows || []).filter(function (r) { return r.ok; });
        if (!okRows.length) { toast("没有可导入的行"); return; }
        if (W7.st.importBase64) {
          apiJson("/api/portfolio/import-xlsx", { method: "POST", body: JSON.stringify({
            filename: W7.st.importFileName, content_base64: W7.st.importBase64, confirm: true }) })
            .then(function (r) {
              if (r.ok && r.body && !r.body.error) { toast("已导入台账"); setTimeout(function () { location.hash = "#/portfolio"; }, 700); }
              else { toast((r.body && r.body.error) || "导入失败"); }
            });
          return;
        }
        var okN = 0, badN = 0;
        okRows.forEach(function (row) {
          apiJson("/api/portfolio", { method: "POST", body: JSON.stringify({
            market: /^\d{6}$/.test(row.code) ? "a" : "fund",
            symbol: row.code, shares: parseFloat(row.qty), cost: parseFloat(row.cost) }) })
            .then(function (r) {
              if (r.ok && r.body && !r.body.error) { okN += 1; } else { badN += 1; }
              if (okN + badN === okRows.length) {
                toast("导入完成：" + okN + " 成功 / " + badN + " 失败");
                if (okN) { setTimeout(function () { location.hash = "#/portfolio"; }, 800); }
              }
            });
        });
        return;
      }

      /* R17 收敛（v3.5.0）：此处原本重复三份 edit/watch 系列分支与一份原型 import 分支
         （真实两份 + 原型一份）。单监听内同名 act 首份命中即 return，其余永不执行，
         属死代码，已全部删除；真实实现保留首份 + import 真实段。 */
    });
  }

  /* ---------- 13. 真实文件导入（CSV 解析；xlsx 需服务端） ---------- */
  W7.st.importRows = null;
  W7.st.importFileName = "";

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function parseCsv(text) {
    var lines = String(text || "").replace(/\r/g, "").split("\n").filter(function (l) { return l.trim(); });
    if (lines.length < 2) { return []; }
    var head = lines[0].split(/[,\t]/).map(function (s) { return s.trim(); });
    var idx = { code: -1, name: -1, qty: -1, cost: -1 };
    head.forEach(function (h, i) {
      if (/代码|code/i.test(h)) { idx.code = i; }
      else if (/名称|name/i.test(h)) { idx.name = i; }
      else if (/数量|qty|shares/i.test(h)) { idx.qty = i; }
      else if (/成本|cost|price/i.test(h)) { idx.cost = i; }
    });
    if (idx.code < 0) { return []; }
    return lines.slice(1).map(function (line) {
      var c = line.split(/[,\t]/);
      var qty = (c[idx.qty] || "").trim();
      var cost = (c[idx.cost] || "").trim();
      var okQty = /^\d+(\.\d+)?$/.test(qty);
      var okCost = /^\d+(\.\d+)?$/.test(cost);
      return {
        code: (c[idx.code] || "").trim(),
        name: idx.name >= 0 ? (c[idx.name] || "").trim() : "",
        qty: qty, cost: cost,
        ok: okQty && okCost,
        note: !okQty ? "数量非法" : (!okCost ? "成本非法" : "可导入")
      };
    });
  }

  function renderImport(rows, fileName) {
    var box = $("v7-import-preview");
    if (!box) { return; }
    var okN = rows.filter(function (r) { return r.ok; }).length;
    box.hidden = false;
    box.innerHTML = "<table><tr><th>代码</th><th>名称</th><th>数量</th><th>成本</th><th>状态</th></tr>" +
      rows.map(function (r) {
        return "<tr><td>" + esc(r.code) + "</td><td>" + esc(r.name) + "</td><td>" + esc(r.qty) +
          "</td><td>" + esc(r.cost) + '</td><td class="' + (r.ok ? "ok" : "bad") + '">' + esc(r.note) + "</td></tr>";
      }).join("") + "</table>";
    var cf = document.querySelector('[data-act="import-confirm"]');
    if (cf) { cf.disabled = okN === 0; }
    var ih = $("v7-import-hint");
    if (ih) { ih.textContent = rows.length + " 行：" + okN + " 可导入 / " + (rows.length - okN) + " 错误" + (fileName ? " · " + fileName : ""); }
  }

  W7.st.importBase64 = "";

  function handleFile(file) {
    if (!file) { return; }
    W7.st.importFileName = file.name;
    W7.st.importBase64 = "";
    var ext = (file.name.split(".").pop() || "").toLowerCase();
    var info = $("v7-file-info");
    if (info) { info.textContent = "已接收：" + file.name + "（" + (file.size / 1024).toFixed(1) + " KB）"; }
    if (ext === "csv" || ext === "tsv") {
      var reader = new FileReader();
      reader.onload = function () {
        W7.st.importRows = parseCsv(String(reader.result || ""));
        if (W7.st.importRows.length) {
          renderImport(W7.st.importRows, file.name);
        } else {
          var ih = $("v7-import-hint");
          if (ih) { ih.textContent = "未识别到「代码」列或数据为空，请检查表头"; }
        }
      };
      reader.readAsText(file, "utf-8");
      return;
    }
    var fr = new FileReader();
    fr.onload = function () {
      var dataUrl = String(fr.result || "");
      W7.st.importBase64 = dataUrl.indexOf(",") >= 0 ? dataUrl.split(",")[1] : "";
      apiJson("/api/portfolio/import-xlsx", { method: "POST", body: JSON.stringify({
        filename: file.name, content_base64: W7.st.importBase64, confirm: false }) })
        .then(function (r) {
          var body = (r.body && r.body.data) || {};
          var rows = body.rows || [];
          var errs = body.errors || [];
          W7.st.importRows = rows.map(function (x) {
            return { code: x.symbol || x.code || "—", name: x.name || "—",
              qty: x.shares != null ? x.shares : (x.qty != null ? x.qty : "—"),
              cost: x.cost != null ? x.cost : "—", ok: true, note: "可导入" };
          });
          errs.forEach(function (e) {
            W7.st.importRows.push({ code: e.symbol || "—", name: e.name || "—", qty: "—", cost: "—",
              ok: false, note: e.reason || e.message || "校验失败" });
          });
          if (W7.st.importRows.length) { renderImport(W7.st.importRows, file.name); }
          else {
            var ih = $("v7-import-hint");
            if (ih) { ih.textContent = (r.body && r.body.error) || "未解析到数据行"; }
          }
        });
    };
    fr.readAsDataURL(file);
  }

  function bindImport() {
    var drop = $("v7-drop");
    var input = $("v7-file");
    if (!drop || !input || drop.getAttribute("data-bound") === "1") { return; }
    drop.setAttribute("data-bound", "1");
    drop.addEventListener("click", function () { input.click(); });
    input.addEventListener("change", function () { handleFile(input.files && input.files[0]); });
    ["dragenter", "dragover"].forEach(function (name) {
      drop.addEventListener(name, function (e) { e.preventDefault(); drop.classList.add("drag"); });
    });
    ["dragleave", "drop"].forEach(function (name) {
      drop.addEventListener(name, function (e) { e.preventDefault(); drop.classList.remove("drag"); });
    });
    drop.addEventListener("drop", function (e) {
      var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) { handleFile(f); }
    });
  }

  /* ---------- 15. 持仓删除入口（D-12） ---------- */
  function injectPortfolioToolbar() {
    if (currentId() !== "portfolio") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-toolbar")) { return; }
    var bar = document.createElement("div");
    bar.className = "v7-toolbar";
    bar.innerHTML = '<select id="v7-pf-select"><option value="">选择持仓…</option></select>' +
      '<button class="v7-btn danger" data-act="pf-del" type="button">删除持仓</button>' +
      '<button class="v7-btn primary" data-act="pf-add" type="button">＋ 添加持仓</button>' +
      '<span class="v7-hint">删除会真实写入 data/portfolio.json</span>';
    body.insertBefore(bar, body.firstChild);
    OB.http.getJSON("/api/portfolio").catch(function () { return null; })
      .then(function (j) {
        var items = ((j && j.data && j.data.items) || []);
        var sel = $("v7-pf-select");
        if (sel) {
          sel.innerHTML = '<option value="">选择持仓…</option>' + items.map(function (x) {
            return '<option value="' + esc(x.id) + '">' + esc(x.symbol || "") + " " + esc(x.name || "") + "</option>";
          }).join("");
        }
      }).catch(function () { /* 静默 */ });
  }

  /* ---------- 16. 连接器开关 / 设置保存（D-01 剩余） ---------- */
  function injectConnectorsToolbar() {
    if (currentId() !== "connectors") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-toolbar")) { return; }
    var bar = document.createElement("div");
    bar.className = "v7-toolbar";
    bar.innerHTML = '<select id="v7-cn-select"><option value="">选择数据源…</option></select>' +
      '<button class="v7-btn" data-act="cn-on" type="button">启用</button>' +
      '<button class="v7-btn danger" data-act="cn-off" type="button">停用</button>';
    body.insertBefore(bar, body.firstChild);
    OB.http.getJSON("/api/connectors/status").catch(function () { return null; })
      .then(function (j) {
        var list = ((j && j.data && j.data.datasources) || []);
        var sel = $("v7-cn-select");
        if (sel) {
          sel.innerHTML = '<option value="">选择数据源…</option>' + list.map(function (x) {
            return '<option value="' + esc(x.label || x.source || "") + '">' + esc(x.label || x.source || "") + "</option>";
          }).join("");
        }
      }).catch(function () { /* 静默 */ });
  }

  function injectSettingsPrefs() {
    if (currentId() !== "settings") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-toolbar")) { return; }
    var bar = document.createElement("div");
    bar.className = "v7-toolbar";
    bar.innerHTML = '<span>关闭窗口行为</span>' +
      '<select id="v7-pref-close"><option value="tray">最小化到托盘</option><option value="quit">直接退出</option></select>' +
      '<button class="v7-btn primary" data-act="pref-save" type="button">保存</button>';
    body.insertBefore(bar, body.firstChild);
    OB.http.getJSON("/api/system/desktop-prefs").catch(function () { return null; })
      .then(function (j) {
        var v = (j && j.data && j.data.close_action) || "tray";
        var sel = $("v7-pref-close");
        if (sel) { sel.value = v; }
      }).catch(function () { /* 静默 */ });
  }

  /* ---------- 20. 关于卡（恢复旧版 settings S-05） ---------- */
  function injectAboutCard() {
    if (currentId() !== "settings") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-about")) { return; }
    var card = document.createElement("div");
    card.className = "card v7-about";
    card.innerHTML = '<div class="card-head"><span class="card-title"><span class="badge-ico">◈</span>关于</span>' +
      '<span class="card-note">版本 / 数据源 / 日志 / 恢复出厂</span></div>' +
      '<div class="v-body"><div class="v-kpi-grid" id="v7-about-kpis"></div>' +
      '<div class="v7-form-foot">' +
      '<button class="v7-btn" data-act="about-log" type="button">导出日志</button>' +
      '<button class="v7-btn danger" data-act="about-reset" type="button">恢复出厂设置</button>' +
      '</div></div>';
    body.appendChild(card);
    Promise.all([
      OB.http.getJSON("/api/health").catch(function () { return {}; }),
      OB.http.getJSON("/api/connectors/status").catch(function () { return {}; })
    ]).then(function (res) {
      var h = (res[0] && res[0].data) || {};
      var c = (res[1] && res[1].data) || {};
      var srcs = (c.datasources || []).length;
      var box = $("v7-about-kpis");
      if (box) {
        box.innerHTML = [
          ["版本", (h.app && h.app.version) || "—"],
          ["构建", h.build || "—"],
          ["数据源", srcs + " 个"],
          ["口径", h.caliber || "—"]
        ].map(function (kv) {
          return '<div class="v-kpi"><span class="k-label">' + kv[0] + '</span><span class="k-value">' + esc(kv[1]) + "</span></div>";
        }).join("");
      }
    });
  }


  /* 跨域导出（W7.forms） */
  W7.forms = W7.forms || {};
  W7.forms.bindActions = bindActions;
  W7.forms.bindImport = bindImport;
  W7.forms.esc = esc;
  W7.forms.injectAboutCard = injectAboutCard;
  W7.forms.injectConnectorsToolbar = injectConnectorsToolbar;
  W7.forms.injectPortfolioToolbar = injectPortfolioToolbar;
  W7.forms.injectSettingsPrefs = injectSettingsPrefs;
})();
