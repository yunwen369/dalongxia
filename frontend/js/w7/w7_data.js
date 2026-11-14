/* W7.data——切割自 v7_app.js（RF-20261112-01 阶段4）。代码体原样保留。 */
(function () {
  "use strict";
  var W7 = window.W7 = window.W7 || {};
  var C = W7.core;
  var $ = C.$, toast = C.toast, apiJson = C.apiJson, queryParam = C.queryParam;
  var currentId = function () { return W7.detail.currentId.apply(null, arguments); };
  var esc = function () { return W7.forms.esc.apply(null, arguments); };
  var loadAiSummary = function () { return W7.ai.loadAiSummary.apply(null, arguments); };
  var preview = function () { return W7.shell.preview.apply(null, arguments); };
  var statKpis = function () { return W7.portfolio.statKpis.apply(null, arguments); };

  /* ---------- 25. 资金明细（v3.4.0：恢复 lhb-broker / lhb-institution / north-top / northbound） ---------- */
  var moneyTabState = "北向成交";
  W7.st.northMetric = "turnover";

  /** 对象数组 → 表格（cols = [[key, label, 格式化?], ...]）。 */
  function objTable(rows, cols, emptyNote) {
    if (!rows || !rows.length) { return '<div class="v-empty">' + esc(emptyNote || "接口返回空数据") + "</div>"; }
    return '<div class="v7-preview-table"><table><tr>' +
      cols.map(function (c) { return "<th>" + esc(c[1]) + "</th>"; }).join("") + "</tr>" +
      rows.map(function (r) {
        return "<tr>" + cols.map(function (c) {
          var v = r[c[0]];
          var text = c[2] ? c[2](v) : (v == null ? "—" : String(v));
          return "<td>" + esc(text) + "</td>";
        }).join("") + "</tr>";
      }).join("") + "</table></div>";
  }

  function injectMoneyPanel() {
    if (currentId() !== "lhb") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-money")) { return; }
    var card = document.createElement("div");
    card.className = "card v7-money";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">◎</span>资金明细</span>' +
        '<span class="card-note">北向成交 / 前十 · 龙虎榜机构 / 营业部</span></div>' +
        '<div class="v-body">' +
          '<div class="v7-kline-tools" style="margin:0 0 8px">' +
            ["北向成交", "北向前十", "龙虎榜机构", "龙虎榜营业部"].map(function (t) {
              return '<button class="v7-tab' + (t === moneyTabState ? " on" : "") + '" data-money-tab="' + t + '" type="button">' + t + "</button>";
            }).join("") +
            '<span data-money-metric-wrap style="margin-left:8px">' +
              '<button class="v7-tab on" data-money-metric="turnover" type="button">成交总额</button>' +
              '<button class="v7-tab" data-money-metric="legacy_net" type="button">历史净买额</button>' +
            "</span>" +
          "</div>" +
          '<div id="v7-money-body"><div class="loading">加载中…</div></div>' +
        "</div>";
    body.insertBefore(card, body.firstChild);
    loadMoney(moneyTabState);
  }

  function loadMoney(tab) {
    moneyTabState = tab;
    var box = $("v7-money-body");
    if (!box) { return; }
    box.innerHTML = '<div class="loading">加载中…</div>';
    var put = function (html) { var b = $("v7-money-body"); if (b) { b.innerHTML = html; } };

    if (tab === "北向成交") {
      apiJson("/api/moneyflow/northbound?days=20&metric=" + W7.st.northMetric).then(function (r) {
        var d = (r.body && r.body.data) || {};
        if (r.body && r.body.error) { put('<div class="v-empty">' + esc(r.body.error) + "</div>"); return; }
        var items = (d.items || []).slice().reverse();
        var html = '<div class="v7-preview-table"><table><tr><th>日期</th><th>合计(亿)</th><th>沪(亿)</th><th>深(亿)</th><th>笔数</th></tr>' +
          items.map(function (x) {
            return "<tr><td>" + esc(x.date) + "</td><td>" + esc(x.total == null ? "—" : x.total) +
              "</td><td>" + esc(x.hu == null ? "—" : x.hu) + "</td><td>" + esc(x.shen == null ? "—" : x.shen) +
              "</td><td>" + esc(x.deals == null ? "—" : x.deals) + "</td></tr>";
          }).join("") + "</table></div>" +
          '<div class="v-hint" style="margin-top:6px">' + esc(d.caliber || "") + "</div>";
        put(html);
      });
      return;
    }
    if (tab === "北向前十") {
      apiJson("/api/moneyflow/north-top?limit=10").then(function (r) {
        var d = (r.body && r.body.data) || {};
        var items = d.items || [];
        put(objTable(items, [["name", "名称"], ["code", "代码"], ["net", "净买额(亿)"],
          ["buy", "买入(亿)"], ["sell", "卖出(亿)"], ["pct", "涨跌幅"]],
          d.note || "当前无北向前十数据（可能未到披露时点）"));
      });
      return;
    }
    if (tab === "龙虎榜机构") {
      apiJson("/api/moneyflow/lhb-institution?limit=50").then(function (r) {
        var d = (r.body && r.body.data) || {};
        put(objTable(d.items, [["date", "日期"], ["code", "代码"], ["name", "名称"], ["close", "收盘"],
          ["pct", "涨跌幅"], ["buy_orgs", "买方机构"], ["sell_orgs", "卖方机构"],
          ["net_buy", "净买(亿)"]], "当日无机构席位上榜") +
          '<div class="v-hint" style="margin-top:6px">披露日：' + esc(d.date || "—") + " · " + esc(d.source || "") + "</div>");
      });
      return;
    }
    if (tab === "龙虎榜营业部") {
      apiJson("/api/moneyflow/lhb-broker?limit=50").then(function (r) {
        var d = (r.body && r.body.data) || {};
        put(objTable(d.items, [["date", "日期"], ["name", "营业部"], ["buy_count", "买入次数"],
          ["sell_count", "卖出次数"], ["buy_amount", "买入(亿)"], ["sell_amount", "卖出(亿)"],
          ["net_buy", "净买(亿)"]], "当日无营业部上榜"));
      });
      return;
    }
  }

  /* ---------- 28. 资讯工具（v3.4.0：ai-feed / board-tree / sector-search / financial-detail） ---------- */
  var newsTabState = "AI 资讯流";
  /* R14（v3.6.0）：newsInput 增加市场字段（a/hk/us），财报正文 / AI 摘要三市场共用。 */
  W7.st.newsInput = { sector: "半导体", code: "", title: "", market: "a" };
  /** 输入框优先、状态兜底（12 秒刷新会重建卡片，输入值必须持久）。 */
  function newsVal(id, fallback) {
    var el = $(id);
    var v = el && el.value != null ? String(el.value) : "";
    return v.trim() ? v.trim() : (fallback || "");
  }
  W7.st.newsSort = "relevance";
  W7.st.newsRegion = "global";

  function injectNewsPanel() {
    if (currentId() !== "news") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-news")) { return; }
    var card = document.createElement("div");
    card.className = "card v7-news";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">▦</span>资讯工具</span>' +
        '<span class="card-note">AI 资讯流 / 板块树 / 板块资讯 / 财报正文</span></div>' +
        '<div class="v-body">' +
          '<div class="v7-kline-tools" style="margin:0 0 8px">' +
            ["AI 资讯流", "板块树", "板块资讯", "财报正文", "AI 摘要"].map(function (t) {
              return '<button class="v7-tab' + (t === newsTabState ? " on" : "") + '" data-news-tab="' + t + '" type="button">' + t + "</button>";
            }).join("") +
            ["relevance:相关", "time:最新", "impact:影响"].map(function (x) {
              var k = x.split(":")[0];
              return '<button class="v7-tab' + (k === W7.st.newsSort ? " on" : "") + '" data-news-sort="' + k + '" type="button">' + x.split(":")[1] + "</button>";
            }).join("") +
            ["global:全球", "cn:国内"].map(function (x) {
              var k = x.split(":")[0];
              return '<button class="v7-tab' + (k === W7.st.newsRegion ? " on" : "") + '" data-news-region="' + k + '" type="button">' + x.split(":")[1] + "</button>";
            }).join("") +
          "</div>" +
          '<div class="v7-form" style="margin-bottom:8px">' +
            '<div><label>板块（板块资讯用）</label><input id="v7-news-sector" value="' + esc(W7.st.newsInput.sector) + '"></div>' +
            '<div><label>市场（财报正文 / AI 摘要用）</label><select id="v7-news-market">' +
              [["a", "A股"], ["hk", "港股"], ["us", "美股"]].map(function (x) {
                return '<option value="' + x[0] + '"' + (x[0] === (W7.st.newsInput.market || "a") ? " selected" : "") + ">" + x[1] + "</option>";
              }).join("") +
            "</select></div>" +
            '<div><label>公告代码（财报正文 / AI 摘要用）</label><input id="v7-news-code" value="' + esc(W7.st.newsInput.code) + '" placeholder="A股 600519 / 港股 00700 / 美股 AAPL"></div>' +
            '<div><label>公告标题（AI 摘要用，可空）</label><input id="v7-ai-title" value="' + esc(W7.st.newsInput.title) + '" placeholder="如 2026年第三季度报告"></div>' +
          "</div>" +
          '<div id="v7-news-body"><div class="loading">加载中…</div></div>' +
        "</div>";
    body.insertBefore(card, body.firstChild);
    card.addEventListener("input", function (ev) {
      var t = ev.target;
      if (!t || !t.id) { return; }
      if (t.id === "v7-news-sector") { W7.st.newsInput.sector = t.value; }
      else if (t.id === "v7-news-code") { W7.st.newsInput.code = t.value; }
      else if (t.id === "v7-ai-title") { W7.st.newsInput.title = t.value; }
    });
    /* R14：市场下拉用 change 事件（select 不触发 input 兼容路径），输入值必须持久。 */
    card.addEventListener("change", function (ev) {
      var t = ev.target;
      if (t && t.id === "v7-news-market" && t.value) {
        W7.st.newsInput.market = t.value;
        var codeEl = $("v7-news-code");
        if (codeEl && !codeEl.value.trim()) {
          codeEl.placeholder = t.value === "hk" ? "如 00700" : t.value === "us" ? "如 AAPL" : "如 600519";
        }
      }
    });
    loadNews(newsTabState);
  }

  function newsItemHtml(x) {
    // v3.7.0（REQ-20261115-01 ③）：快讯补「原文 ↗」入口（后端已返回 url；无 url 显示「—」不补默认链接）
    var url = String(x.url || "").trim();
    var link = url
      ? '<a href="' + esc(url) + '" target="_blank" rel="noopener noreferrer" class="v7-news-src">原文 ↗</a>'
      : '<span class="v7-news-src muted">原文 —</span>';
    return '<div class="v7-news-item"><b>' + esc(x.title || "—") + "</b>" +
      '<div class="v7-hint">' + esc((x.text || x.summary || "").slice(0, 160)) + "</div>" +
      '<div class="v7-hint v7-news-meta"><span>' +
      esc(x.time || x.publish_time || x.source || "") + " · " + esc(x.source || "") + "</span>" + link + "</div></div>";
  }

  function loadNews(tab) {
    newsTabState = tab;
    var box = $("v7-news-body");
    if (!box) { return; }
    var put = function (h) { var b = $("v7-news-body"); if (b) { b.innerHTML = h; } };
    box.innerHTML = '<div class="loading">加载中…</div>';

    if (tab === "AI 资讯流") {
      apiJson("/api/news/ai-feed?limit=20&sort=" + W7.st.newsSort + "&region=" + W7.st.newsRegion + "&market=all").then(function (r) {
        var d = (r.body && r.body.data) || {};
        if (r.body && r.body.error) { put('<div class="v-empty">' + esc(r.body.error) + "</div>"); return; }
        var items = d.items || [];
        put(items.length ? '<div class="v7-hint" style="margin-bottom:6px">共 ' + esc(String(d.count || items.length)) +
          " 条 · 去重前 " + esc(String(d.before_dedup || "—")) + " · 源：" + esc(d.source || "") + "</div>" +
          items.map(newsItemHtml).join("") : '<div class="v-empty">无资讯</div>');
      });
      return;
    }
    if (tab === "板块树") {
      apiJson("/api/news/board-tree").then(function (r) {
        var d = (r.body && r.body.data) || {};
        if (r.body && r.body.error) { put('<div class="v-empty">' + esc(r.body.error) + "</div>"); return; }
        var tree = d.tree || [];
        var cnt = d.counts || {};
        var html = '<div class="v7-hint" style="margin-bottom:6px">版本 ' + esc(d.version || "—") +
          " · 一级 " + esc(String(cnt.level1 || tree.length)) + " 二级 " + esc(String(cnt.level2 || "—")) +
          " 三级 " + esc(String(cnt.level3 || "—")) + "</div>";
        tree.forEach(function (lv1) {
          html += '<div style="margin-bottom:6px"><b>' + esc(lv1.name) + "</b>";
          (lv1.children || []).forEach(function (lv2) {
            html += '<div style="margin-left:12px">▸ ' + esc(lv2.name);
            (lv2.children || []).forEach(function (lv3) {
              html += '<button class="v7-tab" data-news-search="' + esc(lv3.name) + '" style="margin-left:6px">' + esc(lv3.name) + "</button>";
            });
            html += "</div>";
          });
          html += "</div>";
        });
        put(html || '<div class="v-empty">板块树为空</div>');
      });
      return;
    }
    if (tab === "板块资讯") {
      var sector = newsVal("v7-news-sector", W7.st.newsInput.sector);
      if (!sector) { put('<div class="v-empty">请填写板块名</div>'); return; }
      apiJson("/api/news/sector-search?sector=" + encodeURIComponent(sector) + "&sort=time&limit=30").then(function (r) {
        var d = (r.body && r.body.data) || {};
        if (r.body && r.body.error) { put('<div class="v-empty">' + esc(r.body.error) + "</div>"); return; }
        var items = d.items || [];
        put(items.length ? '<div class="v7-hint" style="margin-bottom:6px">「' + esc(d.sector || sector) + "」命中 " +
          esc(String(d.count || items.length)) + " 条 · 层级 " + esc(d.level || "—") + "</div>" +
          items.map(newsItemHtml).join("") : '<div class="v-empty">该板块无资讯</div>');
      });
      return;
    }
    if (tab === "财报正文") {
      var code = newsVal("v7-news-code", W7.st.newsInput.code);
      if (!code) { put('<div class="v-empty">请填写公告代码</div>'); return; }
      /* R14：市场取自下拉（默认 a，与 v3.5.0 前行为一致）。有标题时先搜公告列表定位文档，再取正文。 */
      var mkt = W7.st.newsInput.market || "a";
      var title = newsVal("v7-ai-title", W7.st.newsInput.title);
      var detailUrl = "/api/news/financial-detail?market=" + encodeURIComponent(mkt) +
        "&code=" + encodeURIComponent(code) + "&title=" + encodeURIComponent(title || "");
      apiJson(detailUrl).then(function (r) {
        var d = (r.body && r.body.data) || {};
        if (r.body && r.body.error) { put('<div class="v-empty">' + esc(r.body.error) + "</div>"); return; }
        var text = d.content || d.text || d.body || "";
        put(text ? '<div class="v7-hint">' + esc(d.title || "") + " · 市场 " + esc(mkt.toUpperCase()) + '</div><div class="v7-pre">' +
          esc(String(text).slice(0, 4000)) + "</div>" : '<div class="v-empty">未取到正文（' + esc(d.note || "需从公告列表行传入标题/链接") + "）</div>");
      });
      return;
    }
    if (tab === "AI 摘要") {
      loadAiSummary();
      return;
    }
  }

  /* ---------- 29. 板块工具（v3.4.0：恢复 sector/concept · sector/heatmap · sector/crowding） ---------- */
  var SECTOR_TABS = ["概念板块", "板块热力", "拥挤度单查"];
  W7.st.sectorTabState = "概念板块";
  W7.st.sectorMarket = "a";
  W7.st.sectorKind = "industry";
  var sectorBoard = "半导体";

  /** 数值格式化（非数值返回占位符，不把 NaN 打到界面上）。 */
  function secFixed(v, digits) {
    var n = parseFloat(v);
    if (!isFinite(n)) { return "—"; }
    return n.toFixed(digits == null ? 2 : digits);
  }

  /** 带符号数值 + 涨跌配色（红涨绿跌，A 股习惯；0 不着色）。 */
  function secSigned(v, digits, suffix) {
    var n = parseFloat(v);
    if (!isFinite(n)) { return '<span style="opacity:.55">—</span>'; }
    var color = n > 0 ? "#ff6b78" : (n < 0 ? "#2fd6a4" : "");
    return '<span' + (color ? ' style="color:' + color + '"' : "") + ">" +
      (n > 0 ? "+" : "") + n.toFixed(digits == null ? 2 : digits) + (suffix || "") + "</span>";
  }

  /** 纯文本版带符号数值（statKpis 内部会 esc，不能传 HTML）。 */
  function secSignedText(v, digits, suffix) {
    var n = parseFloat(v);
    if (!isFinite(n)) { return "—"; }
    return (n > 0 ? "+" : "") + n.toFixed(digits == null ? 2 : digits) + (suffix || "");
  }

  /** 板块工具页签 + 市场/口径切换条。 */
  function sectorToolsHtml() {
    var html = '<div class="v7-kline-tools" style="margin:0 0 8px">' +
      SECTOR_TABS.map(function (t) {
        return '<button class="v7-tab' + (t === W7.st.sectorTabState ? " on" : "") +
          '" data-sec-tab="' + t + '" type="button">' + t + "</button>";
      }).join("");
    if (W7.st.sectorTabState !== "拥挤度单查") {
      html += '<span style="flex:1"></span>' +
        [["a", "A股"], ["hk", "港股"], ["us", "美股"]].map(function (m) {
          return '<button class="v7-tab' + (m[0] === W7.st.sectorMarket ? " on" : "") +
            '" data-sec-market="' + m[0] + '" type="button">' + m[1] + "</button>";
        }).join("");
      // F9/R12：美股模式下展示美东时段四态徽章（盘前/盘中/盘后/休市），避免把休市数据当实时
      if (W7.st.sectorMarket === "us" && window.OB && OB.fmt && OB.fmt.usSession) {
        var sess = OB.fmt.usSession();
        html += '<span class="v7-us-session" title="美东时段判定（节假日未内置，口径见 lib/fmt.js）· ' +
          esc(sess.phase) + '">' + esc(sess.text) + "</span>";
      }
    }
    html += "</div>";
    if (W7.st.sectorTabState === "板块热力") {
      html += '<div class="v7-kline-tools" style="margin:0 0 8px">' +
        [["industry", "行业"], ["concept", "概念"]].map(function (k) {
          return '<button class="v7-tab' + (k[0] === W7.st.sectorKind ? " on" : "") +
            '" data-sec-kind="' + k[0] + '" type="button">' + k[1] + "</button>";
        }).join("") + "</div>";
    }
    return html;
  }

  /** 板块行情页注入板块工具卡（概念/热力/单板块拥挤度）。 */
  function injectSectorPanel() {
    if (currentId() !== "sectors") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-sector")) { return; }
    var card = document.createElement("div");
    card.className = "card v7-sector";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">▤</span>板块工具</span>' +
        '<span class="card-note">概念板块 / 板块热力 / 单板块拥挤度</span></div>' +
        '<div class="v-body">' +
          '<div id="v7-sec-tools"></div>' +
          '<div id="v7-sec-body"><div class="loading">加载中…</div></div>' +
        "</div>";
    body.insertBefore(card, body.firstChild);
    loadSector(W7.st.sectorTabState);
  }

  /** 切换页签后重新加载对应子面板。 */
  function loadSector(tab) {
    W7.st.sectorTabState = tab || W7.st.sectorTabState;
    var tools = $("v7-sec-tools");
    if (tools) { tools.innerHTML = sectorToolsHtml(); }
    var box = $("v7-sec-body");
    if (!box) { return; }
    box.innerHTML = '<div class="loading">加载中…</div>';
    if (W7.st.sectorTabState === "概念板块") { loadConcept(); return; }
    if (W7.st.sectorTabState === "板块热力") { loadHeatmap(); return; }
    renderCrowdForm();
  }

  /** 概念板块列表（sector/concept）。 */
  function loadConcept() {
    apiJson("/api/sector/concept?market=" + encodeURIComponent(W7.st.sectorMarket)).then(function (r) {
      var box = $("v7-sec-body");
      if (!box) { return; }
      if (r.body && r.body.error) { box.innerHTML = '<div class="v-empty">' + esc(r.body.error) + "</div>"; return; }
      var d = (r.body && r.body.data) || {};
      var items = (d.items || []).slice().sort(function (a, b) { return (b.pct || 0) - (a.pct || 0); });
      if (!items.length) { box.innerHTML = '<div class="v-empty">该市场无概念板块数据</div>'; return; }
      var html = '<div class="v7-hint" style="margin-bottom:6px">共 ' + items.length + " 个概念 · 上涨 " +
        items.filter(function (x) { return (x.pct || 0) > 0; }).length + " 个 · 源 " +
        esc(d.source || "—") + " · " + esc(d.as_of || "") + "</div>";
      html += '<div class="v7-preview-table"><table><tr><th>概念</th><th>涨跌幅</th><th>5日</th><th>20日</th>' +
        "<th>成交额(亿)</th><th>净流入(亿)</th><th>换手%</th><th>量比</th><th>领涨股</th></tr>" +
        items.map(function (x) {
          return "<tr><td>" + esc(x.name) + "</td><td>" + secSigned(x.pct, 2, "%") + "</td>" +
            "<td>" + secSigned(x.pct_5d, 2, "%") + "</td><td>" + secSigned(x.pct_20d, 2, "%") + "</td>" +
            "<td>" + secFixed(x.amount, 2) + "</td><td>" + secSigned(x.net_inflow, 2) + "</td>" +
            "<td>" + secFixed(x.turnover_rate, 2) + "</td><td>" + secFixed(x.volume_ratio, 2) + "</td>" +
            "<td>" + esc(x.lead_stock || "—") + (x.lead_pct == null ? "" : " " + secSigned(x.lead_pct, 2, "%")) + "</td></tr>";
        }).join("") + "</table></div>";
      if (d.caliber) { html += '<div class="v7-sec-note">口径：' + esc(d.caliber) + "</div>"; }
      box.innerHTML = html;
    });
  }

  /** 板块热力图（sector/heatmap）：色块 + 明细表。 */
  function loadHeatmap() {
    apiJson("/api/sector/heatmap?market=" + encodeURIComponent(W7.st.sectorMarket) + "&kind=" + encodeURIComponent(W7.st.sectorKind))
      .then(function (r) {
        var box = $("v7-sec-body");
        if (!box) { return; }
        if (r.body && r.body.error) { box.innerHTML = '<div class="v-empty">' + esc(r.body.error) + "</div>"; return; }
        var d = (r.body && r.body.data) || {};
        var nodes = (d.nodes || []).slice().sort(function (a, b) { return (b.pct || 0) - (a.pct || 0); });
        if (!nodes.length) { box.innerHTML = '<div class="v-empty">该市场无热力数据</div>'; return; }
        var up = nodes.filter(function (x) { return (x.pct || 0) > 0; }).length;
        var html = '<div class="v7-hint" style="margin-bottom:6px">共 ' + nodes.length + " 个" +
          (W7.st.sectorKind === "industry" ? "行业" : "概念") + " · 上涨 " + up + " / 下跌 " +
          (nodes.length - up) + " · 源 " + esc(d.source || "—") + " · " + esc(d.as_of || "") + "</div>";
        // v3.7.0（①⑦）：等尺寸色块网格 → 云图 treemap（面积∝成交额、色∝涨跌幅、块内明细）
        var tmKey = "sec" + (window.__V6_TM_SEQ = (window.__V6_TM_SEQ || 0) + 1);
        window.__V6_TM = window.__V6_TM || {};
        window.__V6_TM[tmKey] = { nodes: nodes };
        html += '<div class="v-tm v-tm-board" data-key="' + tmKey + '"></div>' +
          '<div class="v7-hint" style="margin:6px 0 2px">云图口径：面积 ∝ 成交额，颜色 ∝ 涨跌幅（红涨绿跌）；' +
          '悬停看明细，点击块可查看该板块成分股。</div>';

        // 明细表：全量（前端分页展示前 30），中文表头 + 点表头排序（injectTableTools 统一注入）
        html += '<div class="v7-hint" style="margin:10px 0 4px">板块明细（按成交额降序，点表头可排序）</div>' +
          '<div class="v7-preview-table"><table><tr><th>板块</th><th>涨跌幅</th><th>成交额(亿)</th><th>净流入(亿)</th><th>领涨股</th><th>上涨/下跌</th></tr>' +
          nodes.slice().sort(function (a, b) { return (b.amount || 0) - (a.amount || 0); }).slice(0, 30).map(function (n) {
            return "<tr data-board=\"" + esc(n.name) + "\"><td>" + esc(n.name) + "</td><td>" +
              secSigned(n.pct, 2, "%") + "</td><td>" + secFixed(n.amount, 2) + "</td><td>" +
              (n.net_inflow == null ? "—" : secSigned(n.net_inflow, 2)) + "</td><td>" +
              esc(n.lead_stock || "—") + "</td><td>" +
              esc((n.up == null ? "—" : n.up) + " / " + (n.down == null ? "—" : n.down)) + "</td></tr>";
          }).join("") + "</table></div>";

        // v3.7.0（⑦）：行业资金流入 / 流出（双向条），口径字段缺失时如实说明
        var withFlow = nodes.filter(function (n) { return typeof n.net_inflow === "number"; });
        if (withFlow.length) {
          var sorted = withFlow.slice().sort(function (a, b) { return b.net_inflow - a.net_inflow; });
          var top = sorted.slice(0, 8), bottom = sorted.slice(-8).reverse();
          var maxAbs = sorted.reduce(function (m, n) { return Math.max(m, Math.abs(n.net_inflow)); }, 0) || 1;
          var bar = function (n) {
            var w0 = Math.min(50, Math.abs(n.net_inflow) / maxAbs * 50);
            return '<div class="v7-ff-row"><span>' + esc(n.name) + '</span>' +
              '<span class="v7-ff-bar"><i class="' + (n.net_inflow >= 0 ? "in" : "out") + '" style="width:' + w0.toFixed(1) + '%"></i></span>' +
              '<span class="' + (n.net_inflow > 0 ? "up" : "down") + '">' + secSigned(n.net_inflow, 2) + " 亿</span></div>";
          };
          html += '<div class="v7-hint" style="margin:10px 0 4px">行业资金流入 / 流出（主力净额，亿元）</div>' +
            '<div class="v7-ff"><div><div class="v7-hint">净流入 Top8</div>' + top.map(bar).join("") + "</div>" +
            '<div><div class="v7-hint">净流出 Top8</div>' + bottom.map(bar).join("") + "</div></div>";
        } else {
          html += '<div class="v7-hint" style="margin:10px 0 4px">行业资金流入 / 流出：' +
            '当前市场口径无「主力净额」字段（港股/美股池），故不展示（不补 0）。</div>';
        }
        if (d.caliber) { html += '<div class="v7-sec-note">口径：' + esc(d.caliber) + "</div>"; }
        box.innerHTML = html;
      });
  }

  /** 单板块拥挤度查询表单（渲染后自动跑一次当前板块）。 */
  function renderCrowdForm() {
    var box = $("v7-sec-body");
    if (!box) { return; }
    box.innerHTML =
      '<div class="v7-form" style="margin-bottom:8px">' +
        '<div><label>板块名</label><input id="v7-sec-board" value="' + esc(sectorBoard) + '" placeholder="如 半导体"></div>' +
        '<div><label>类型</label><select id="v7-sec-ctype">' +
          '<option value="industry"' + (W7.st.sectorKind === "industry" ? " selected" : "") + ">行业</option>" +
          '<option value="concept"' + (W7.st.sectorKind === "concept" ? " selected" : "") + ">概念</option></select></div>" +
        '<div><button class="v7-btn primary" data-sec-crowd="1" type="button">查询拥挤度</button></div>' +
      "</div>" +
      '<div id="v7-sec-crowd-body"><div class="loading">计算中…</div></div>';
    runCrowd();
  }

  /** 执行单板块拥挤度查询（sector/crowding）。 */
  function runCrowd() {
    var boardEl = $("v7-sec-board");
    var kindEl = $("v7-sec-ctype");
    var board = ((boardEl && boardEl.value) || "").trim();
    var kind = (kindEl && kindEl.value) || "industry";
    var box = $("v7-sec-crowd-body");
    if (!box) { return; }
    if (!board) { box.innerHTML = '<div class="v-empty">请填写板块名</div>'; return; }
    sectorBoard = board;
    box.innerHTML = '<div class="loading">计算中（首次约 10–40 秒）…</div>';
    apiJson("/api/sector/crowding?board=" + encodeURIComponent(board) + "&kind=" + encodeURIComponent(kind))
      .then(function (r) {
        var target = $("v7-sec-crowd-body");
        if (!target) { return; }
        if (r.body && r.body.error) { target.innerHTML = '<div class="v-empty">' + esc(r.body.error) + "</div>"; return; }
        var d = (r.body && r.body.data) || {};
        if (d.score == null) {
          target.innerHTML = '<div class="v-empty">该板块暂无拥挤度数据</div>' +
            ((d.missing || []).length ? '<div class="v7-sec-miss">' + d.missing.map(esc).join("；") + "</div>" : "");
          return;
        }
        var levelText = d.level || "—";
        var html = statKpis([
          ["板块", (d.board || board) + "（" + (d.kind === "concept" ? "概念" : "行业") + "）"],
          ["拥挤度评分", secFixed(d.score, 1) + " / 100"],
          ["档位", levelText],
          ["样本天数", String(d.sample_days == null ? "—" : d.sample_days)]
        ]);
        html += '<div class="v7-hint" style="margin:8px 0 4px">' + esc(d.advice || "") +
          " · 源 " + esc(d.source || "—") + " · " + esc(d.as_of || "") + " · " + esc(d.market_status || "") + "</div>";
        html += '<div class="v7-preview-table"><table><tr><th>维度</th><th>原始值</th><th>历史分位</th><th>权重</th><th>口径说明</th></tr>' +
          (d.parts || []).map(function (p) {
            var refOnly = !p.weight;  // 权重 0 = 仅参考分项（换手率/相关性/融资余额强度）
            return "<tr><td>" + esc(p.label) +
              (refOnly ? ' <span style="opacity:.6;font-size:11px">仅参考</span>' : "") + "</td><td>" +
              secFixed(p.value, 2) + esc(p.unit || "") +
              "</td><td>" + secFixed(p.pct, 1) + "</td><td>" + secFixed((p.weight || 0) * 100, 0) + "%</td><td>" +
              (p.note ? '<span style="opacity:.75">' + esc(p.note) + "</span>" : "—") + "</td></tr>";
          }).join("") + "</table></div>";
        var ex = d.extra || {};
        if (ex.constituent_count != null) {
          html += '<div class="v7-hint" style="margin-top:6px">成分股 ' + esc(String(ex.constituent_count)) + " 只</div>";
        }
        if (ex.correlation && ex.correlation.caliber) {
          html += '<div class="v7-sec-note">成分股相关性：' + esc(ex.correlation.caliber) + "</div>";
        }
        if (ex.turnover && ex.turnover.caliber) {
          html += '<div class="v7-sec-note">成分股换手率：' + esc(ex.turnover.caliber) + "</div>";
        }
        if (d.caliber) { html += '<div class="v7-sec-note">口径：' + esc(d.caliber) + "</div>"; }
        if ((d.missing || []).length) {
          html += '<div class="v7-sec-miss">缺失维度：' + (d.missing || []).map(esc).join("；") + "</div>";
        }
        target.innerHTML = html;
      }).catch(function () {   // F14：失败态，避免停留「计算中…」
        var t2 = $("v7-sec-crowd-body");
        if (t2) { t2.innerHTML = '<div class="v-empty">拥挤度查询失败（/api/sector/crowding）</div>'; }
      });
  }

  /* ---------- 31b. 板块研判卡（v3.7.0 ③：情绪/研判按具体板块） ---------- */
  var _boardPoolState = 0;        // 0=未加载 1=加载中 2=就绪
  var _boardPoolWaiters = [];

  /** 板块池（行业 90 / 概念 800+）懒加载，供所有三级选择器共用；就绪后回调（幂等）。 */
  function ensureBoardPool(cb) {
    var st = W7.st;
    if (_boardPoolState === 2) { if (cb) { cb(st.boardPool); } return; }
    if (cb) { _boardPoolWaiters.push(cb); }
    if (_boardPoolState === 1) { return; }
    _boardPoolState = 1;
    st.boardPool = { industry: [], concept: [], tree: { industry: [], concept: [] } };
    var pending = 2;
    function finish() {
      pending -= 1;
      if (pending > 0) { return; }
      _boardPoolState = 2;
      var pool = st.boardPool;
      _boardPoolWaiters.splice(0).forEach(function (fn) {
        try { fn(pool); } catch (e) { /* 单个选择器绑定失败不影响其它 */ }
      });
    }
    OB.http.getJSON("/api/sector/industry?market=a").then(function (r) {
      st.boardPool.industry = (r && r.data && r.data.items) || [];
      st.boardPool.tree.industry = [{ label: "全部行业", children: st.boardPool.industry.map(function (x) { return x.name; }) }];
      finish();
    }).catch(finish);
    OB.http.getJSON("/api/sector/concept?market=a").then(function (r) {
      st.boardPool.concept = (r && r.data && r.data.items) || [];
      st.boardPool.tree.concept = [{ label: "全部概念", children: st.boardPool.concept.map(function (x) { return x.name; }) }];
      finish();
    }).catch(finish);
  }

  /** 板块级研判：并行取板块口径情绪（sentiment/market?board）与规则研判（sentiment/brief?board）。 */
  function runBoardBrief(board, kind) {
    var out = $("v7-brief-out");
    if (!out) { return; }
    board = (board || "").trim();
    if (!board) { out.innerHTML = '<div class="v-empty">请先选择或输入板块名</div>'; return; }
    W7.st.briefBoard = board;
    W7.st.briefKind = kind || "industry";
    // 注意：/api/sentiment/brief 的 kind 是「pre/post/all（盘前/盘后）」，不是板块类型；
    // 传 board 即走板块视角分支（后端不落库）。板块类型仅供界面显示。
    var qs = "board=" + encodeURIComponent(board);
    out.innerHTML = '<div class="loading">计算中…（板块级首次约 5–20 秒）</div>';
    Promise.all([
      apiJson("/api/sentiment/market?" + qs).catch(function () { return null; }),
      apiJson("/api/sentiment/brief?" + qs).catch(function () { return null; })
    ]).then(function (arr) {
      var target = $("v7-brief-out");
      if (!target) { return; }
      var senErr = arr[0] && arr[0].body && arr[0].body.error;
      var briefBody = (arr[1] && arr[1].body) || {};
      var sen = (arr[0] && arr[0].body && arr[0].body.data) || {};
      var brief = ((briefBody.data && briefBody.data.items) || [])[0] || null;
      var scoreText = brief && brief.score != null
        ? (brief.score > 0 ? "+" + brief.score : String(brief.score)) : "—";
      var html = statKpis([
        ["板块", board + "（" + (W7.st.briefKind === "concept" ? "概念" : "行业") + "）"],
        ["研判信号", brief && brief.signal ? brief.signal : "—"],
        ["维度得分", scoreText],
        ["情绪温度", sen.temperature != null ? secFixed(sen.temperature, 1) : "—"]
      ]);
      if (brief && (brief.dimensions || []).length) {
        html += '<div class="v7-preview-table"><table><tr><th>研判维度</th><th>结论</th><th>口径</th></tr>' +
          brief.dimensions.map(function (x) {
            return "<tr><td>" + esc(x.label || x.key) + "</td><td>" +
              (x.known === false ? '<span style="opacity:.6">样本不足</span>' : esc(x.value_text || "—")) +
              "</td><td>" + (x.detail ? '<span style="opacity:.72">' + esc(x.detail) + "</span>" : "—") + "</td></tr>";
          }).join("") + "</table></div>";
      } else if (!brief) {
        html += '<div class="v-empty">' +
          esc(briefBody.error || "该板块暂无研判数据（样本不足或接口未返回）") + "</div>";
      }
      html += '<div class="v7-hint" style="margin-top:6px">情绪样本 ' +
        esc(String(sen.total == null ? "—" : sen.total)) + " 条 · 判级 " + esc(sen.level || "—") +
        "（正面 " + esc(String(sen.positive == null ? "—" : sen.positive)) +
        " / 负面 " + esc(String(sen.negative == null ? "—" : sen.negative)) + "）" +
        (senErr ? " · 情绪接口：" + esc(senErr) : "") + "</div>";
      if (brief && brief.caliber) { html += '<div class="v7-sec-note">口径：' + esc(brief.caliber) + "</div>"; }
      if (brief && brief.note) { html += '<div class="v7-sec-note">' + esc(brief.note) + "</div>"; }
      target.innerHTML = html;
    });
  }

  /** 注入「板块研判」卡（今日看点 / 市场总览）：三级选择器 + 板块口径情绪与研判。 */
  function injectBoardBriefPanel() {
    var id = currentId();
    if (id !== "today" && id !== "market") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-brief-board")) { return; }
    var H = window.V6_HELPERS || {};
    if (!H.boardPickerHtml || !H.bindBoardPicker) { return; }
    var card = document.createElement("div");
    card.className = "card v7-brief-board";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">研</span>板块研判</span>' +
        '<span class="card-note">选定具体板块，按板块口径重算情绪与规则研判</span></div>' +
      '<div class="v-bp-bar">' +
        H.boardPickerHtml("v7-brief-bp", { kind: W7.st.briefKind || "industry", value: W7.st.briefBoard || "" }) +
        '<button class="v7-btn primary" id="v7-brief-go" type="button">研判</button>' +
      "</div>" +
      '<div id="v7-brief-out"><div class="v-empty">选择板块后自动研判；也可输入板块名后点「研判」或回车</div></div>' +
      '<div class="v7-sec-note">数据源：/api/sentiment/market?board= 与 /api/sentiment/brief?board=（即时计算，不落库）</div>';
    var first = body.firstChild;
    body.insertBefore(card, first ? first.nextSibling : null);
    var el = card.querySelector("#v7-brief-bp");
    ensureBoardPool(function (pool) {
      H.bindBoardPicker(el, pool, function (kind, value) {
        if (value) { runBoardBrief(value, kind); }
      });
    });
    var goBtn = card.querySelector("#v7-brief-go");
    var search = card.querySelector('#v7-brief-bp [data-r="search"]');
    var kindSel = card.querySelector('#v7-brief-bp [data-r="kind"]');
    function go() { runBoardBrief((search && search.value) || "", (kindSel && kindSel.value) || "industry"); }
    if (goBtn) { goBtn.addEventListener("click", go); }
    if (search) {
      search.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter") { ev.preventDefault(); go(); }
      });
    }
  }

  /* ---------- 32. 选股器（v3.4.0：恢复 screener/refresh · screener/run） ---------- */
  var SCREENER_FACTORS = [
    ["pe", "PE-TTM", "倍"], ["pb", "PB-MRQ", "倍"], ["ps", "PS-TTM", "倍"],
    ["pcf", "经营现金流市值比", "倍"], ["chg", "涨跌幅", "%"], ["mktcap", "总市值", "亿元"],
    ["roe_min", "ROE（近3年年报均）", "%"], ["gross", "销售毛利率", "%"],
    ["rev_yoy", "营收同比", "%"], ["np_yoy", "净利同比", "%"],
    ["debt_ratio", "资产负债率", "%"], ["ocf", "经营现金流净额", "元"],
    ["dividend_yield", "股息率", "%"]
  ];

  /** 条件行 HTML（4 行固定槽位，留空=忽略）。 */
  function screenerRowsHtml() {
    var opts = SCREENER_FACTORS.map(function (f) {
      return '<option value="' + f[0] + '">' + esc(f[1]) + "</option>";
    }).join("");
    var rows = "";
    for (var i = 0; i < 4; i += 1) {
      rows += '<div class="v7-form" style="margin-bottom:6px">' +
        '<div><label>因子 ' + (i + 1) + '</label><select class="scr-factor">' + opts + "</select></div>" +
        '<div><label>关系</label><select class="scr-op"><option value="lte">≤</option><option value="gte">≥</option></select></div>' +
        '<div><label>阈值</label><input class="scr-value" placeholder="留空=不用该条件"></div>' +
        "</div>";
    }
    return rows;
  }

  /** 选股页注入条件筛选卡。 */
  function injectScreenerPanel() {
    if (currentId() !== "screener") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-screener")) { return; }
    var card = document.createElement("div");
    card.className = "card v7-screener";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">⌕</span>条件选股</span>' +
        '<span class="card-note">查本地快照（&lt;1s），不触源站</span></div>' +
        '<div class="v-body">' +
          '<div id="v7-scr-status"><div class="loading">读取快照状态…</div></div>' +
          '<div style="margin:8px 0 4px" class="v7-hint">筛选条件（AND，最多 4 条；留空自动忽略）</div>' +
          screenerRowsHtml() +
          '<div class="v7-form" style="margin-bottom:8px">' +
            '<div><label>返回上限</label><input id="v7-scr-limit" value="50"></div>' +
            '<div><button class="v7-btn primary" data-act="scr-run" type="button">执行筛选</button></div>' +
            '<div><button class="v7-btn" data-act="scr-refresh" type="button">刷新快照</button></div>' +
          "</div>" +
          '<div id="v7-scr-body"><div class="v7-hint">设置条件后点「执行筛选」</div></div>' +
        "</div>";
    body.insertBefore(card, body.firstChild);
    loadScreenerStatus();
  }

  /** 快照状态（screener/status）。 */
  function loadScreenerStatus() {
    apiJson("/api/screener/status").then(function (r) {
      var box = $("v7-scr-status");
      if (!box) { return; }
      if (r.body && r.body.error) { box.innerHTML = '<div class="v-empty">' + esc(r.body.error) + "</div>"; return; }
      var d = (r.body && r.body.data) || {};
      var html = statKpis([
        ["快照版本", d.version || "—"],
        ["交易日", d.trade_date || "—"],
        ["覆盖个股", String(d.row_count == null ? "—" : d.row_count)],
        ["股息率覆盖", secFixed(d.dividend_coverage, 1) + "%"],
        ["构建状态", d.building ? "构建中" : "空闲"]
      ]);
      if ((d.progress || {}).total) {
        html += '<div class="v7-hint">进度：' + esc(String(d.progress.done || 0)) + " / " +
          esc(String(d.progress.total)) + "（" + esc(d.progress.step || "—") + "）</div>";
      }
      if (d.last_error) { html += '<div class="v7-sec-miss">上次构建错误：' + esc(d.last_error) + "</div>"; }
      box.innerHTML = html;
    });
  }

  /** 执行筛选（screener/run）。 */
  function runScreener() {
    var box = $("v7-scr-body");
    if (!box) { return; }
    var factors = document.querySelectorAll(".scr-factor");
    var ops = document.querySelectorAll(".scr-op");
    var values = document.querySelectorAll(".scr-value");
    var conds = [];
    for (var i = 0; i < factors.length; i += 1) {
      var raw = ((values[i] || {}).value || "").trim();
      if (!raw) { continue; }
      var num = parseFloat(raw);
      if (!isFinite(num)) { box.innerHTML = '<div class="v-empty">第 ' + (i + 1) + " 条阈值不是数字</div>"; return; }
      conds.push({ factor: factors[i].value, op: (ops[i] || {}).value || "lte", value: num });
    }
    var limit = parseInt((($("v7-scr-limit") || {}).value || "50"), 10);
    if (!isFinite(limit) || limit < 1) { limit = 50; }
    box.innerHTML = '<div class="loading">筛选计算中…</div>';
    apiJson("/api/screener/run", { method: "POST", body: JSON.stringify({ conditions: conds, limit: limit }) })
      .then(function (r) {
        var target = $("v7-scr-body");
        if (!target) { return; }
        if (r.body && r.body.error) {
          target.innerHTML = '<div class="v-empty">' + esc(r.body.error) + "</div>";
          return;
        }
        var d = (r.body && r.body.data) || {};
        var items = d.items || [];
        if (!items.length) {
          target.innerHTML = '<div class="v-empty">无符合条件的个股（条件：' +
            (conds.length ? conds.map(function (c) { return c.factor + (c.op === "lte" ? "≤" : "≥") + c.value; }).join(" 且 ")
              : "未设置，返回快照前 " + limit + " 只") + "）</div>";
          return;
        }
        var html = '<div class="v7-hint" style="margin-bottom:4px">命中 ' + esc(String(d.total_matched)) +
          " 只，返回 " + esc(String(d.returned)) + " 只" + (d.truncated ? "（已截断）" : "") +
          " · 快照 " + esc((d.snapshot || {}).trade_date || "—") + " · " + esc(d.caliber || "") + "</div>";
        html += '<div class="v7-preview-table"><table><tr><th>代码</th><th>名称</th><th>板块</th><th>收盘</th>' +
          "<th>涨跌幅</th><th>PE</th><th>PB</th><th>ROE(3年最低)</th><th>资产负债率</th><th>股息率</th></tr>" +
          items.map(function (x) {
            return "<tr><td>" + esc(x.code) + "</td><td>" + esc(x.name) + "</td><td>" + esc(x.board || "—") + "</td><td>" +
              secFixed(x.close, 2) + "</td><td>" + secSigned(x.chg, 2, "%") + "</td><td>" + secFixed(x.pe, 2) +
              "</td><td>" + secFixed(x.pb, 2) + "</td><td>" + secFixed(x.roe3y_min, 2) + "</td><td>" +
              secFixed(x.debt_ratio, 2) + "</td><td>" + secFixed(x.dividend_yield, 2) + "</td></tr>";
          }).join("") + "</table></div>";
        target.innerHTML = html;
      });
  }


  /* 跨域导出（W7.data） */
  W7.data = W7.data || {};
  W7.data.injectMoneyPanel = injectMoneyPanel;
  W7.data.injectNewsPanel = injectNewsPanel;
  W7.data.injectScreenerPanel = injectScreenerPanel;
  W7.data.injectSectorPanel = injectSectorPanel;
  W7.data.loadMoney = loadMoney;
  W7.data.loadNews = loadNews;
  W7.data.loadScreenerStatus = loadScreenerStatus;
  W7.data.loadSector = loadSector;
  W7.data.newsVal = newsVal;
  W7.data.objTable = objTable;
  W7.data.runCrowd = runCrowd;
  W7.data.runScreener = runScreener;
  W7.data.ensureBoardPool = ensureBoardPool;          // v3.7.0 ③：板块池共用
  W7.data.injectBoardBriefPanel = injectBoardBriefPanel;
  W7.data.runBoardBrief = runBoardBrief;
  W7.data.secFixed = secFixed;
  W7.data.secSigned = secSigned;
  W7.data.secSignedText = secSignedText;
})();
