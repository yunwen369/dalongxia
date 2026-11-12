/* W7.topbar——切割自 v7_app.js（RF-20261112-01 阶段4）。代码体原样保留。 */
(function () {
  "use strict";
  var W7 = window.W7 = window.W7 || {};
  var C = W7.core;
  var $ = C.$, toast = C.toast, apiJson = C.apiJson, queryParam = C.queryParam;
  /* ---------- 5. 顶栏标的搜索 ---------- */
  function bindSearch() {
    var input = $("v7-search");
    var list = $("v7-search-list");
    if (!input || !list) { return; }
    var timer = null;

    function render(items) {
      list.innerHTML = items.length
        ? items.map(function (x) {
            return '<div class="v7-search-item" data-symbol="' + x.symbol + '" data-name="' + x.name + '">' +
              "<b>" + x.symbol + "</b><span>" + x.name + " · " + (x.market || "") + "</span></div>";
          }).join("")
        : '<div class="v7-search-item"><b>无匹配</b><span>换个代码或名称试试</span></div>';
      list.hidden = false;
    }

    input.addEventListener("input", function () {
      var q = input.value.trim();
      if (!q) { list.hidden = true; return; }
      clearTimeout(timer);
      timer = setTimeout(function () {
        OB.http.getJSON("/api/market/search?q=" + encodeURIComponent(q))
          .then(function (j) {
            var items = (j && j.data && (j.data.items || j.data.results)) || [];
            render(items.length ? items.slice(0, 8) : DEMO_SEARCH.filter(function (x) {
              return (x.symbol + x.name).indexOf(q) >= 0;
            }));
          })
          .catch(function () {
            render(DEMO_SEARCH.filter(function (x) {
              return (x.symbol + x.name).toLowerCase().indexOf(q.toLowerCase()) >= 0;
            }));
          });
      }, 220);
    });

    list.addEventListener("click", function (event) {
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
