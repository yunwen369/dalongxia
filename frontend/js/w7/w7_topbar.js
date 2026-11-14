/* W7.topbar——切割自 v7_app.js（RF-20261112-01 阶段4）。代码体原样保留。 */
(function () {
  "use strict";
  var W7 = window.W7 = window.W7 || {};
  var C = W7.core;
  var $ = C.$, toast = C.toast, apiJson = C.apiJson, queryParam = C.queryParam;
  /* esc 惰性桥（W7.forms.esc 在 w7_forms.js 定义；RF-20261112-01 遗留收口：搜索联想动态值转义） */
  var esc = function () { return W7.forms.esc.apply(null, arguments); };
  /* ---------- 5. 顶栏统一搜索（标的 + 板块；v3.7.0 ⑦） ---------- */
  function bindSearch() {
    var input = $("v7-search");
    var list = $("v7-search-list");
    if (!input || !list) { return; }
    var timer = null;
    var seq = 0;  // R18：在途请求序号，慢响应不覆盖新请求结果（stale 丢弃）

    /** 板块池本地匹配（行业 90 / 概念 800+；池经 ensureBoardPool 一次性加载，输入不再发请求）。 */
    function boardHits(q) {
      var pool = (W7.st && W7.st.boardPool) || { industry: [], concept: [] };
      var kw = String(q || "").toLowerCase();
      var out = [];
      [["industry", "行业"], ["concept", "概念"]].forEach(function (pair) {
        (pool[pair[0]] || []).forEach(function (b) {
          if (String(b.name || "").toLowerCase().indexOf(kw) >= 0) {
            out.push({ name: b.name, kind: pair[0], kindText: pair[1], pct: b.pct });
          }
        });
      });
      return out.slice(0, 6);
    }

    function render(stocks, boards) {
      var html = "";
      if (stocks.length) {
        html += '<div class="v7-search-g">标的</div>' + stocks.map(function (x) {
          return '<div class="v7-search-item" data-symbol="' + esc(x.symbol) + '" data-name="' + esc(x.name) + '">' +
            "<b>" + esc(x.symbol) + "</b><span>" + esc(x.name) + " · " + esc(x.market || "") + "</span></div>";
        }).join("");
      }
      if (boards.length) {
        html += '<div class="v7-search-g">板块（行业 / 概念）</div>' + boards.map(function (b) {
          var pctText = (b.pct == null || !isFinite(b.pct)) ? "" :
            " · " + (b.pct > 0 ? "+" : "") + Number(b.pct).toFixed(2) + "%";
          return '<div class="v7-search-item" data-board="' + esc(b.name) + '" data-board-kind="' + esc(b.kind) + '">' +
            '<b class="bd-ico">▦</b><span>' + esc(b.name) + " · " + esc(b.kindText) + pctText + "</span></div>";
        }).join("");
      }
      list.innerHTML = html || '<div class="v7-search-item"><b>无匹配</b><span>换个代码、名称或板块试试</span></div>';
      list.hidden = false;
    }

    /** 板块选中 → 市场总览页并自动跑该板块研判（板块研判卡由 afterRoute 注入）。 */
    function pickBoard(board, kind) {
      list.hidden = true;
      input.value = board;
      W7.st.briefBoard = board;
      W7.st.briefKind = kind || "industry";
      var target = "#/market";
      if (location.hash === target) { window.dispatchEvent(new HashChangeEvent("hashchange")); }
      else { location.hash = target; }
      toast("板块研判：" + board + "（" + (kind === "concept" ? "概念" : "行业") + "）");
      var tries = 0;
      var timer2 = setInterval(function () {
        tries += 1;
        if (tries > 40) { clearInterval(timer2); return; }   // 8s 等不到研判卡就放弃
        var out = document.getElementById("v7-brief-out");
        if (!out) { return; }
        clearInterval(timer2);
        if (W7.data && W7.data.runBoardBrief) { W7.data.runBoardBrief(board, kind); }
      }, 200);
    }

    function runQuery(q) {
      var my = ++seq;
      // R18：请求在途显示「检索中…」；该项无 data-symbol，点击不触发跳转（禁用态）
      list.innerHTML = '<div class="v7-search-item v7-search-busy"><b>检索中…</b><span>' + esc(q) + "</span></div>";
      list.hidden = false;
      OB.http.getJSON("/api/market/search?q=" + encodeURIComponent(q))
        .then(function (j) {
          if (my !== seq) { return; }
          var items = (j && j.data && (j.data.items || j.data.results)) || [];
          var stocks = items.length ? items.slice(0, 8) : DEMO_SEARCH.filter(function (x) {
            return (x.symbol + x.name).indexOf(q) >= 0;
          });
          render(stocks, boardHits(q));
        })
        .catch(function () {
          if (my !== seq) { return; }
          render(DEMO_SEARCH.filter(function (x) {
            return (x.symbol + x.name).toLowerCase().indexOf(q.toLowerCase()) >= 0;
          }), boardHits(q));
        });
    }

    input.addEventListener("input", function () {
      var q = input.value.trim();
      if (!q) { list.hidden = true; return; }
      clearTimeout(timer);
      timer = setTimeout(function () {
        runQuery(q);
        // 板块池懒加载：装好后若输入框仍是同一关键词，补跑一次（首次搜索也能出板块）
        if (W7.data && W7.data.ensureBoardPool) {
          W7.data.ensureBoardPool(function () {
            if (input.value.trim() === q) { runQuery(q); }
          });
        }
      }, 220);
    });

    list.addEventListener("click", function (event) {
      var bitem = event.target.closest(".v7-search-item[data-board]");
      if (bitem) {
        pickBoard(bitem.getAttribute("data-board"), bitem.getAttribute("data-board-kind"));
        return;
      }
      var item = event.target.closest(".v7-search-item");
      if (!item || !item.getAttribute("data-symbol")) { return; }
      var symbol = item.getAttribute("data-symbol");
      var name = item.getAttribute("data-name");
      list.hidden = true;
      input.value = symbol;
      try { localStorage.setItem("openbb-symbol", symbol); } catch (e) { /* noop */ }
      // BUG-20261112-06：跳转必须带 symbol，否则同页切换标的时 hash 不变、页面不刷新
      var target = "#/stock?symbol=" + encodeURIComponent(symbol);
      if (location.hash === target) {
        window.dispatchEvent(new HashChangeEvent("hashchange"));
      } else {
        location.hash = target;
      }
      toast("已切换标的：" + symbol + " " + name);
    });

    document.addEventListener("click", function (e) {
      if (!e.target.closest(".v7-search")) { list.hidden = true; }
    });
  }

  /* ---------- 6. 提醒铃铛 ---------- */
  function bindBell() {
    var bell = $("v7-bell");
    var dot = $("v7-bell-dot");
    if (!bell) { return; }
    bell.addEventListener("click", function () { location.hash = "#/alerts"; });
    OB.http.getJSON("/api/alerts/events")
      .then(function (j) {
        var events = (j && j.data && (j.data.events || j.data.items)) || [];
        var unread = events.filter(function (e) { return !e.read; }).length;
        if (unread > 0) { dot.hidden = false; dot.textContent = unread > 99 ? "99+" : String(unread); }
      })
      .catch(function () {
        dot.hidden = false;
        dot.textContent = "3";   // 演示稿：真实接口不可达时给固定角标
      });
  }

  /* ---------- 7. 健康 / 版本徽章 ---------- */
  function bindBadges() {
    var health = $("v7-health");
    var build = $("v7-build");
    OB.http.getJSON("/api/health")
      .then(function (j) {
        var data = (j && j.data) || {};
        var ok = data.status === "ok";
        health.className = "v7-badge" + (ok ? "" : " warn");
        health.innerHTML = '<i class="dot"></i>' + (ok ? "数据正常" : "降级中");
        if (data.build) { build.textContent = data.build; build.title = "构建标识（/api/health）"; }
      })
      .catch(function () {
        health.className = "v7-badge warn";
        health.innerHTML = '<i class="dot"></i>演示稿';
        build.textContent = "v3.2.0";
      });
  }


  /* 跨域导出（W7.topbar） */
  W7.topbar = W7.topbar || {};
  W7.topbar.bindBadges = bindBadges;
  W7.topbar.bindBell = bindBell;
  W7.topbar.bindSearch = bindSearch;
})();
