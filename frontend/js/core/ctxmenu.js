/* =============================================================
 * OpenBB 中文投研工作台 · core/ctxmenu.js
 * 全局右键菜单（GL-03）：标的 / 板块 / 基金 / 新闻 四类对象。
 *
 * 设计依据：原型说明书 GL-03「右键菜单（标的/板块行/新闻/重仓股行 四类）」；
 *          验收标准 B2「右键四类对象菜单项齐全；跳转联动；点击外/Esc 关闭；出屏翻转」。
 *
 * 约定
 * ----
 * 1. **全局单例**（§11 实现自由度：右键菜单=全局单例），同一时刻只存在一个菜单节点；
 * 2. 对象识别优先级：`data-ctx` 显式声明 → 表格首列代码推断（6 位数字 / 名称+代码）；
 *    识别不出时不弹菜单，避免误伤普通文本；
 * 3. 定位：以鼠标点为锚，超出视口右/下边界时翻转（B2 出屏翻转）；
 * 4. 关闭：点击菜单外、Esc、滚动、窗口失焦、执行菜单项；
 * 5. 菜单项只做**跳转与复制**，写操作（加自选）走既有 API 并给 toast 反馈；
 * 6. 不复制各页渲染实现（§7.11）：跳转目标即各页唯一实现。
 * ============================================================= */
(function () {
  "use strict";

  /* 阶段3（BUG-20261112-07）：原 window.Utils 从未定义，toast 一直静默失效；
   * 现接通 lib/ui.js 单例。t/el 维持原 fallback（i18n 与 el 在本文件未实际使用 el）。 */
  var t = window.Utils ? window.Utils.t : function (k) { return k; };
  var el = window.Utils ? window.Utils.el : null;
  var toast = (window.OB && OB.ui) ? OB.ui.toast : function () {};

  var menuNode = null;
  var boardNames = null;   // 板块名录（90 行业 + 概念），用于旧表格的兜底识别

  /** 关闭并移除菜单。 */
  function closeMenu() {
    if (menuNode && menuNode.parentNode) {
      menuNode.parentNode.removeChild(menuNode);
    }
    menuNode = null;
  }

  /** 复制文本并提示（成功/失败都给中文反馈）。 */
  function copyText(text) {
    var value = String(text || "");
    if (!value) {
      return;
    }
    var done = function () { toast("已复制：" + value); };
    var fail = function () { toast("复制失败，请手动选择文本"); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(value).then(done).catch(fail);
      return;
    }
    var area = document.createElement("textarea");
    area.value = value;
    document.body.appendChild(area);
    area.select();
    try {
      document.execCommand("copy");
      done();
    } catch (error) {
      fail();
    }
    document.body.removeChild(area);
  }

  /** 按类型生成菜单项：{ label, run } 列表。 */
  function itemsFor(ctx) {
    var kind = ctx.kind;
    if (kind === "stock") {
      var symbol = ctx.symbol || "";
      var market = ctx.market || "a";
      return [
        { label: "个股研究", run: function () { location.hash = "#/stock?symbol=" + encodeURIComponent(symbol) + "&market=" + encodeURIComponent(market); } },
        { label: "加自选", run: function () { addWatch(symbol, ctx.name); } },
        { label: "相关新闻", run: function () { location.hash = "#/news?symbol=" + encodeURIComponent(symbol); } },
        { label: "财报摘要", run: function () { location.hash = "#/fin?q=" + encodeURIComponent(symbol); } },
        { label: "公告正文", run: function () { location.hash = "#/ann?q=" + encodeURIComponent(ctx.name || symbol); } },
        { label: "复制代码", run: function () { copyText(symbol); } }
      ];
    }
    if (kind === "sector") {
      var board = ctx.board || ctx.name || "";
      return [
        { label: "板块详情", run: function () { location.hash = "#/sector-val?board=" + encodeURIComponent(board) + "&market=a"; } },
        { label: "板块 K 线", run: function () { location.hash = "#/charting?board=" + encodeURIComponent(board); } },
        { label: "板块资讯", run: function () { location.hash = "#/news?sector=" + encodeURIComponent(board); } },
        { label: "成分股", run: function () { location.hash = "#/sector-val?board=" + encodeURIComponent(board) + "&tab=cons"; } },
        { label: "复制板块名", run: function () { copyText(board); } }
      ];
    }
    if (kind === "fund") {
      var code = ctx.code || "";
      return [
        { label: "基金详情", run: function () { location.hash = "#/fund?code=" + encodeURIComponent(code); } },
        { label: "经理画像", run: function () { location.hash = "#/mgr?code=" + encodeURIComponent(code); } },
        { label: "加入自选", run: function () { addWatch(code, ctx.name); } },
        { label: "复制代码", run: function () { copyText(code); } }
      ];
    }
    if (kind === "news") {
      return [
        { label: "复制标题", run: function () { copyText(ctx.title || ""); } },
        { label: "相关板块", run: function () { location.hash = "#/news?sector=" + encodeURIComponent(ctx.board || ""); } },
        { label: "打开原文", run: function () { if (ctx.url) { window.open(ctx.url, "_blank", "noopener"); } else { toast("该条资讯无源站链接"); } } }
      ];
    }
    return [];
  }

  /** 加自选：走既有 /api/watchlist，重复添加给明确提示。
   * （阶段4 收编 OB.http.apiJson：401 时由统一层跳登录，属语义增强） */
  function addWatch(symbol, name) {
    if (!symbol) {
      return;
    }
    OB.http.apiJson("/api/watchlist", {
      method: "POST",
      body: JSON.stringify({ symbol: String(symbol), market: "a", note: name || "" })
    })
      .then(function (r) {
        var body = r && r.body;
        if (r.ok && body && body.error) {
          toast(body.error);
          return;
        }
        if (!r.ok && !body) {
          toast("加自选失败，请稍后重试");
          return;
        }
        toast("已加入自选：" + (name || symbol));
      })
      .catch(function () { toast("加自选失败，请稍后重试"); });
  }

  /** 从事件目标推断对象；识别不出返回 null（不弹菜单）。 */
  function resolveContext(target) {
    var node = target && target.closest ? target.closest("[data-ctx]") : null;
    if (node) {
      var kind = node.getAttribute("data-ctx");
      if (kind === "stock") {
        return {
          kind: kind,
          symbol: node.getAttribute("data-symbol") || "",
          market: node.getAttribute("data-market") || "a",
          name: node.getAttribute("data-name") || ""
        };
      }
      if (kind === "sector") {
        return { kind: kind, board: node.getAttribute("data-board") || "", name: node.getAttribute("data-name") || "" };
      }
      if (kind === "fund") {
        return { kind: kind, code: node.getAttribute("data-code") || "", name: node.getAttribute("data-name") || "" };
      }
      if (kind === "news") {
        return {
          kind: kind,
          title: node.getAttribute("data-title") || "",
          board: node.getAttribute("data-board") || "",
          url: node.getAttribute("data-url") || ""
        };
      }
    }
    // 兜底推断 1：表格行首列是 6 位数字 → 标的
    var row = target && target.closest ? target.closest("tr") : null;
    if (!row || !row.querySelector) {
      return null;
    }
    var cell = row.querySelector("td");
    if (!cell) {
      return null;
    }
    var text = (cell.textContent || "").trim();
    var match = /(?:^|\s)(\d{6})(?:\s|$)/.exec(text);
    if (match) {
      return { kind: "stock", symbol: match[1], market: "a", name: text.replace(match[1], "").trim() };
    }
    // 兜底推断 2：首列是已知板块名 → 板块（名录预加载，缺失时不误判）
    if (boardNames && boardNames.has(text)) {
      return { kind: "sector", board: text, name: text };
    }
    return null;
  }

  /** 打开菜单：定位 + 出屏翻转。 */
  function openMenu(ctx, x, y) {
    closeMenu();
    var items = itemsFor(ctx);
    if (!items.length) {
      return;
    }
    var menu = document.createElement("div");
    menu.className = "ctx-menu";
    menu.setAttribute("role", "menu");
    items.forEach(function (item) {
      var row = document.createElement("button");
      row.type = "button";
      row.className = "ctx-item";
      row.setAttribute("role", "menuitem");
      row.textContent = item.label;
      row.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        closeMenu();
        item.run();
      });
      menu.appendChild(row);
    });
    document.body.appendChild(menu);
    menuNode = menu;

    // 出屏翻转：右/下越界时左/上贴边
    var rect = menu.getBoundingClientRect();
    var left = x;
    var top = y;
    if (left + rect.width > window.innerWidth - 8) {
      left = Math.max(8, x - rect.width);
    }
    if (top + rect.height > window.innerHeight - 8) {
      top = Math.max(8, y - rect.height);
    }
    menu.style.left = left + "px";
    menu.style.top = top + "px";
    var first = menu.querySelector(".ctx-item");
    if (first) {
      first.focus();
    }
  }

  /** 预加载板块名录（行业 + 概念），供旧表格兜底识别板块行；失败静默。
   * （阶段4 收编 OB.http.getJSON：!ok 抛错→catch null，与原 401/错误信封「无数据」分支等价） */
  function loadBoardNames() {
    Promise.all([
      OB.http.getJSON("/api/sector/industry").catch(function () { return null; }),
      OB.http.getJSON("/api/sector/concept").catch(function () { return null; })
    ]).then(function (res) {
      var set = new Set();
      [res[0], res[1]].forEach(function (body) {
        var items = (body && body.data && (body.data.items || body.data.list)) || [];
        items.forEach(function (item) {
          if (item && item.name) {
            set.add(String(item.name).trim());
          }
        });
      });
      boardNames = set;
      markBoardRows();
    }).catch(function () { boardNames = new Set(); });
  }

  /** 给已知板块表格行打标（名录未就绪时跳过）；hashchange 后重跑以覆盖新渲染的表。 */
  function markBoardRows() {
    if (!boardNames) {
      return;
    }
    var rows = document.querySelectorAll("table tr");
    Array.prototype.forEach.call(rows, function (row) {
      if (row.hasAttribute("data-ctx")) {
        return;
      }
      var cell = row.querySelector("td");
      if (!cell) {
        return;
      }
      var text = (cell.textContent || "").trim();
      if (boardNames.has(text)) {
        row.setAttribute("data-ctx", "sector");
        row.setAttribute("data-board", text);
        row.setAttribute("data-name", text);
      }
    });
  }

  /** 全局绑定：contextmenu 拦截、外点/Esc/滚动关闭。 */
  function init() {
    document.addEventListener("contextmenu", function (event) {
      var ctx = resolveContext(event.target);
      if (!ctx) {
        return; // 非目标对象：保留浏览器原生菜单
      }
      event.preventDefault();
      openMenu(ctx, event.clientX, event.clientY);
    });
    document.addEventListener("mousedown", function (event) {
      if (menuNode && !menuNode.contains(event.target)) {
        closeMenu();
      }
    }, true);
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        closeMenu();
      }
    });
    window.addEventListener("scroll", closeMenu, true);
    window.addEventListener("resize", closeMenu);
    window.addEventListener("blur", closeMenu);
    window.setTimeout(loadBoardNames, 2500);  // 空闲预加载，不阻塞首屏
    window.addEventListener("hashchange", function () {
      window.setTimeout(markBoardRows, 1200);  // 新页渲染完成后再标一次
    });
  }

  window.CtxMenu = { open: openMenu, close: closeMenu, resolve: resolveContext };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
