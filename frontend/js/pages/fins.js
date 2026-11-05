/* =============================================================
 * OpenBB 中文投研工作台 · pages/fins.js
 * 财报/公告搜索 FS-01~FS-09
 * 由 tools/split_pages.py 从 pages.js 机械切分生成（R17），逻辑零改动；
 * 重新生成请改 pages.js 后重跑脚本，勿直接编辑本文件的结构性段落。
 * ============================================================= */
(function (P) {
  "use strict";

  // ---- 跨模块依赖（core/兄弟模块导出，运行时解析） ----
  var VIEW_IDS = P.VIEW_IDS;
  var api = P.api;
  var el = P.el;
  var state = P.state;
  var t = P.t;

  // =============================================================
  // 新增功能 ⑥：财报 / 公告搜索（#/news?fs=1，FS-01~FS-09）
  // 唯一实现页；入口 = 新闻页 N-05 入口卡 + 侧栏同名项（两处同跳）
  // 设计依据：验收方《设计基准_财报搜索_20261025.md》§5.3；
  //          样式复用现网 pages.css 的 FS-* 段，无第二套配色/栅格。
  // =============================================================
  var fsState = {
    market: "all",   // all / a / hk / us
    type: "全部",     // 财报类型 chips
    since: "all",    // 全部时间 / 近 N 天
    keyword: "",     // 当前生效关键词
    page: 1,         // 当前页码（「加载更多」递增）
    seq: 0,          // 请求序号：过期响应直接丢弃（防抖 + 竞态保护）
    timer: null,     // 输入防抖计时器（300ms）
    rows: [],        // 已累积结果（「加载更多」追加）
    total: 0,        // 源站总命中数
    loading: false,  // 加载中禁止重复提交
    expanded: false  // R6-10：新闻页内联财报区是否展开（跨路由重绘后保持）
  };

  var FS_MARKETS = [["all", "fs.market.all"], ["a", "fs.market.a"], ["hk", "fs.market.hk"], ["us", "fs.market.us"]];
  var FS_TIMES = [["all", "fs.time.all"], ["7", "fs.time.7"], ["30", "fs.time.30"], ["90", "fs.time.90"], ["365", "fs.time.365"]];
  var FS_TYPES = ["全部", "年报", "半年报", "一季报", "三季报", "业绩预告", "招股书", "10-K", "10-Q", "8-K"];

  /** 市场标签样式类：A 股绿描边 / 港股橙描边 / 美股蓝描边（设计基准 §5.3）。
   * @param {string} market a/hk/us
   * @returns {string} 样式类名
   */
  function fsMarketClass(market) {
    return { a: "mkt-a", hk: "mkt-hk", us: "mkt-us" }[market] || "";
  }

  /** 市场中文名。
   * @param {string} market a/hk/us
   * @returns {string} 中文名
   */
  function fsMarketLabel(market) {
    // 显式映射：避免动态拼接键（i18n 静态检查与翻译抽取都依赖字面量键）
    var labels = { a: "fs.market.a", hk: "fs.market.hk", us: "fs.market.us" };
    return t(labels[market] || "fs.market.all");
  }

  /** 定期报告类走「财报」色，其余公告走「公告」色。
   * @param {string} type 类型
   * @returns {boolean} 是否定期报告
   */
  function fsIsReport(type) {
    return ["年报", "半年报", "一季报", "三季报", "招股书", "10-K", "10-Q"].indexOf(type) >= 0;
  }

  /** HTML 转义：标题高亮走 innerHTML，必须先转义防注入。
   * @param {*} text 原文
   * @returns {string} 转义后的文本
   */
  function fsEscape(text) {
    return String(text === null || text === undefined ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  /** 标题关键词高亮（只高亮命中片段，其余原样转义）。
   * @param {*} text 标题
   * @param {string} keyword 关键词
   * @returns {string} 含 mark 标签的 HTML
   */
  function fsHighlight(text, keyword) {
    var safe = fsEscape(text);
    if (!keyword) {
      return safe;
    }
    var needle = fsEscape(keyword).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return safe.replace(new RegExp("(" + needle + ")", "gi"), "<mark>$1</mark>");
  }

  /** 渲染财报/公告搜索页（FS 唯一实现页，对齐原型 fs 视图）。
   * @param {HTMLElement} container 页面容器（#page-news）
   */
  function renderFinancialSearch(container, inline) {
    var embedded = inline === true;
    container.innerHTML = "";
    container.setAttribute("data-fs", "1");  // 标记当前容器是 FS 视图，返回时据此重建新闻壳

    var toolbar = el("div", "page-toolbar");
    // R6-10 / F6-11：内联模式下按钮语义为「收起」，深链模式下为「返回」
    var back = el("button", "fs-back", embedded ? t("fs.collapse") : t("fs.back"));
    back.type = "button";
    back.addEventListener("click", function () {
      if (embedded) {
        container.innerHTML = "";
        container.removeAttribute("data-fs");
        container.hidden = true;
        fsState.expanded = false;
        return;
      }
      window.location.hash = "#/news";
    });
    toolbar.appendChild(back);
    toolbar.appendChild(el("span", "pg-spacer"));
    var refresh = el("button", "pg-btn", t("action.reload"));
    refresh.type = "button";
    refresh.id = "fs-refresh";
    refresh.addEventListener("click", function () {
      loadFinancialSearch(true);
    });
    toolbar.appendChild(refresh);
    container.appendChild(toolbar);

    var grid = el("div", "pg-grid");
    grid.id = "fs-grid";

    // 顶行：市场 Tab（FS-01a）
    var topCard = P.buildCard("FS-00", t("fs.title"), "g-12", "");
    P.setBadges(topCard.card, "巨潮资讯 · 东财 · HKEX披露易 · SEC EDGAR", t("fs.entry.sub"), false);
    var marketTabs = el("div", "pg-tabs");
    marketTabs.id = "fs-market";
    FS_MARKETS.forEach(function (item) {
      var button = el("button", "pg-tab", t(item[1]));
      button.type = "button";
      button.setAttribute("data-market", item[0]);
      marketTabs.appendChild(button);
    });
    topCard.body.appendChild(marketTabs);
    topCard.body.setAttribute("data-state", "default");
    grid.appendChild(topCard.card);

    // FS-01：搜索条件区（输入框 + 时间下拉 + 搜索按钮 + 类型 chips）
    var cond = P.buildCard("FS-01", t("fs.condTitle"), "g-12", "");
    var bar = el("div", "fs-searchbar");
    var input = el("input", "fs-search-input");
    input.id = "fs-input";
    input.type = "search";
    input.placeholder = t("fs.placeholder");
    input.value = fsState.keyword;
    bar.appendChild(input);
    var select = el("select", "fs-select");
    select.id = "fs-time";
    FS_TIMES.forEach(function (item) {
      var option = document.createElement("option");
      option.value = item[0];
      option.textContent = t(item[1]);
      select.appendChild(option);
    });
    select.value = fsState.since;
    bar.appendChild(select);
    var searchBtn = el("button", "pg-btn primary", t("fs.search"));
    searchBtn.type = "button";
    searchBtn.id = "fs-search-btn";
    bar.appendChild(searchBtn);
    cond.body.appendChild(bar);

    var chips = el("div", "fs-chips");
    chips.id = "fs-type";
    FS_TYPES.forEach(function (name) {
      var chip = el("button", "fs-chip" + (fsState.type === name ? " on" : ""), name);
      chip.type = "button";
      chip.setAttribute("data-type", name);
      chips.appendChild(chip);
    });
    cond.body.appendChild(chips);
    cond.body.setAttribute("data-state", "default");
    grid.appendChild(cond.card);

    // FS-02：结果列表（四态容器）
    var result = P.buildCard("FS-02", t("fs.resultTitle"), "g-12", "");
    var count = el("span", "fs-count", "");
    count.id = "fs-count";
    result.card.querySelector(".pg-card-head").insertBefore(count, result.card.querySelector(".pg-spacer"));
    result.body.id = "fs-body";
    grid.appendChild(result.card);

    container.appendChild(grid);

    // ---------- 交互绑定 ----------
    marketTabs.addEventListener("click", function (event) {
      var button = event.target.closest(".pg-tab");
      if (!button) {
        return;
      }
      fsState.market = button.getAttribute("data-market");
      fsSyncTabs();
      loadFinancialSearch(true);
    });
    chips.addEventListener("click", function (event) {
      var chip = event.target.closest(".fs-chip");
      if (!chip) {
        return;
      }
      fsState.type = chip.getAttribute("data-type");
      fsSyncTabs();
      loadFinancialSearch(true);
    });
    select.addEventListener("change", function () {
      fsState.since = select.value;
      loadFinancialSearch(true);
    });
    searchBtn.addEventListener("click", function () {
      fsState.keyword = input.value.trim();
      loadFinancialSearch(true);
    });
    input.addEventListener("input", function () {
      // 防抖 300ms：连续输入只在停顿后发一次请求
      window.clearTimeout(fsState.timer);
      fsState.timer = window.setTimeout(function () {
        fsState.keyword = input.value.trim();
        loadFinancialSearch(true);
      }, 300);
    });
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        window.clearTimeout(fsState.timer);
        fsState.keyword = input.value.trim();
        loadFinancialSearch(true);
      }
    });

    fsSyncTabs();
    loadFinancialSearch(true);
  }

  /** 同步市场 Tab 与类型 chips 的选中态。*/
  function fsSyncTabs() {
    var marketBox = document.getElementById("fs-market");
    if (marketBox) {
      marketBox.querySelectorAll(".pg-tab").forEach(function (button) {
        button.classList.toggle("active", button.getAttribute("data-market") === fsState.market);
      });
    }
    var chipBox = document.getElementById("fs-type");
    if (chipBox) {
      chipBox.querySelectorAll(".fs-chip").forEach(function (chip) {
        chip.classList.toggle("on", chip.getAttribute("data-type") === fsState.type);
      });
    }
  }

  /** 设置结果区四态（default/loading/empty/error）。
   * @param {string} state 状态名
   * @param {string} message 空/错态说明文案
   * @param {Function} onRetry 重试回调
   */
  function fsSetState(state, message, onRetry) {
    var body = document.getElementById("fs-body");
    if (!body) {
      return;
    }
    body.innerHTML = "";
    body.setAttribute("data-state", state);
    if (state === "loading") {
      var skeleton = el("div", "pg-skeleton");
      for (var i = 0; i < 5; i += 1) {
        var row = el("div", "sk-row");
        row.appendChild(el("span", i % 2 ? "w2" : "w1", ""));
        skeleton.appendChild(row);
      }
      body.appendChild(skeleton);
      fsSetCount(t("fs.searching"));
      return;
    }
    var box = el("div", "pg-state");
    if (state === "empty") {
      box.appendChild(el("div", "pg-state-title", t("fs.emptyTitle")));
      box.appendChild(el("div", "pg-state-hint", message || t("fs.emptyHint")));
      var clear = el("button", "pg-btn", t("fs.clearRetry"));
      clear.type = "button";
      clear.addEventListener("click", function () {
        fsState.market = "all";
        fsState.type = "全部";
        fsState.since = "all";
        fsState.keyword = "";
        renderFinancialSearch(document.getElementById(VIEW_IDS.news));
      });
      box.appendChild(clear);
      fsSetCount(t("fs.countZero"));
    } else {
      box.appendChild(el("div", "pg-state-title", t("fs.errorTitle")));
      box.appendChild(el("div", "pg-state-hint", message || t("fs.errorHint")));
      var retry = el("button", "pg-btn primary", t("action.retry"));
      retry.type = "button";
      retry.addEventListener("click", function () {
        if (onRetry) {
          onRetry();
        }
      });
      box.appendChild(retry);
      fsSetCount("—");
    }
    body.appendChild(box);
  }

  /** 更新结果计数文案。
   * @param {string} text 计数文本
   */
  function fsSetCount(text) {
    var node = document.getElementById("fs-count");
    if (node) {
      node.textContent = text;
    }
  }

  /** 渲染结果行（FS-02 九字段 + 标题高亮）。
   * @param {Array} rows 记录列表
   */
  function fsRenderRows(rows) {
    var body = document.getElementById("fs-body");
    if (!body) {
      return;
    }
    body.innerHTML = "";
    body.setAttribute("data-state", "default");
    var list = el("div", "fs-list");
    rows.forEach(function (item) {
      var row = el("div", "fs-item");
      var title = el("div", "fs-item-title");
      title.innerHTML = fsHighlight(item.title, fsState.keyword);  // 已转义，仅插入 mark
      row.appendChild(title);

      var sub = el("div", "fs-item-sub");
      sub.appendChild(el("span", "co", item.company || t("value.none")));
      sub.appendChild(el("span", "code", item.code || "—"));
      sub.appendChild(el("span", "fs-tag " + fsMarketClass(item.market), fsMarketLabel(item.market)));
      sub.appendChild(el("span", "fs-tag " + (fsIsReport(item.type) ? "fr" : "ann"), item.type || "其他"));
      sub.appendChild(el("span", "", "来源：" + (item.source || "—")));
      var time = el("span", "fs-time");
      // TZ-01：财报跨 A股/港股/美股三个市场，时间一律标注北京口径，悬浮给完整说明
      time.innerHTML = fsEscape(item.pubTime || "—") + ' <span class="tz" title="'
        + fsEscape(t("fs.tzFull")) + '">' + fsEscape(t("fs.tz")) + "</span>";
      sub.appendChild(time);
      row.appendChild(sub);

      row.addEventListener("click", function () {
        fsOpenDetail(item);
      });
      list.appendChild(row);
    });
    body.appendChild(list);

    var more = el("div", "fs-more");
    more.appendChild(el("span", "fs-count", t("fs.shown", { n: rows.length })));
    var moreBtn = el("button", "pg-btn sm", t("fs.loadMore"));
    moreBtn.type = "button";
    moreBtn.addEventListener("click", function () {
      loadFinancialSearch(false);
    });
    more.appendChild(moreBtn);
    body.appendChild(more);
    fsSetCount(t("fs.count", { n: rows.length }));
  }

  /** FS-08 详情弹窗：标题/元信息/摘要/原文链接。
   * @param {Object} item 单条记录
   */
  function fsOpenDetail(item) {
    var mask = el("div", "fs-modal-mask");
    var modal = el("div", "fs-modal");
    modal.appendChild(el("h3", "", item.title || t("value.none")));

    var meta = el("div", "m-meta");
    meta.appendChild(el("span", "fs-tag " + fsMarketClass(item.market), fsMarketLabel(item.market)));
    meta.appendChild(el("span", "fs-tag " + (fsIsReport(item.type) ? "fr" : "ann"), item.type || "其他"));
    meta.appendChild(el("span", "fs-tag", (item.company || "") + " · " + (item.code || "")));
    meta.appendChild(el("span", "fs-time", (item.pubTime || "—") + " " + t("fs.tzFull")));
    modal.appendChild(meta);

    modal.appendChild(el("div", "m-sec", t("fs.summary")));
    // 源站不提供摘要时不编造：如实说明「未提供」并引导看原文
    var bodyNode = el("div", "m-body", item.summary || t("fs.summaryNone"));
    modal.appendChild(bodyNode);

    // 阶段 12：A 股（巨潮/东财）、港股（HKEX）、美股（EDGAR）均在打开弹窗时按需拉取正文
    //（A 股：东财直链或巨潮↔东财交叉验证；港股：HKEX↔东财港股库校验；美股：SEC 官方原文）
    fsLoadCnDetail(modal, bodyNode, item);

    modal.appendChild(el("div", "m-sec", t("fs.docLink", { source: item.source || "—" })));
    var link = el("a", "m-link", item.docUrl || t("value.none"));
    if (item.docUrl) {
      link.href = item.docUrl;
      link.target = "_blank";
      link.rel = "noopener";
    }
    modal.appendChild(link);

    var ops = el("div", "m-ops");
    var close = el("button", "pg-btn", t("fs.close"));
    close.type = "button";
    close.addEventListener("click", function () {
      mask.remove();
    });
    ops.appendChild(close);
    var open = el("button", "pg-btn primary", t("fs.openDoc"));
    open.type = "button";
    open.addEventListener("click", function () {
      if (item.docUrl) {
        window.open(item.docUrl, "_blank", "noopener");
      }
    });
    ops.appendChild(open);
    modal.appendChild(ops);

    mask.appendChild(modal);
    mask.addEventListener("click", function (event) {
      if (event.target === mask) {
        mask.remove();
      }
    });
    document.body.appendChild(mask);
  }

  /** 详情正文按需拉取（弹窗打开后异步加载，四态齐备；三市场共用）。
   * @param {HTMLElement} modal 弹窗容器（用于挂口径行与判断弹窗是否已关闭）
   * @param {HTMLElement} bodyNode 摘要/正文体节点
   * @param {Object} item 单条记录（market/title/code/pubTime/docUrl 原样回传后端）
   */
  function fsLoadCnDetail(modal, bodyNode, item) {
    bodyNode.textContent = t("fs.detailLoading");
    bodyNode.classList.add("m-loading");
    window.Utils.api("/api/news/financial-detail", {
      market: item.market || "a",
      title: item.title || "",
      code: item.code || "",
      pub_time: item.pubTime || "",
      doc_url: item.docUrl || ""
    }).then(function (data) {
      if (!document.body.contains(modal)) {
        return;  // 弹窗已关闭，过期响应丢弃
      }
      bodyNode.classList.remove("m-loading");
      if (data && data.content) {
        bodyNode.textContent = data.content;
        var caliber = el("div", "m-caliber",
          (data.verified ? t("fs.verified") : t("fs.unverified"))
          + " · " + (data.caliber || ""));
        modal.insertBefore(caliber, bodyNode.nextSibling);
      } else {
        bodyNode.textContent = (data && data.caliber) || t("fs.summaryNone");
      }
    }).catch(function () {
      if (!document.body.contains(modal)) {
        return;
      }
      bodyNode.classList.remove("m-loading");
      bodyNode.textContent = t("fs.detailFailed");
    });
  }

  /** R18：检索进行中禁用按钮并注明原因，防止重复提交被静默忽略。 */
  function fsSetBusy(busy) {
    var btn = document.getElementById("fs-search-btn");
    if (!btn) {
      return;
    }
    btn.disabled = Boolean(busy);
    btn.textContent = busy ? t("fs.searching") : t("fs.search");
    btn.setAttribute("aria-busy", busy ? "true" : "false");
  }

  /** 拉取财报/公告结果（reset=true 重新检索，false 追加下一页）。
   * @param {boolean} reset 是否重置分页与已累积结果
   */
  function loadFinancialSearch(reset) {
    var body = document.getElementById("fs-body");
    if (!body) {
      return;
    }
    if (fsState.loading) {
      return;  // 加载中禁止重复提交
    }
    if (reset) {
      fsState.page = 1;
      fsState.rows = [];
    }
    var seq = ++fsState.seq;
    fsState.loading = true;
    fsSetBusy(true);
    if (reset) {
      fsSetState("loading");
    }
    window.Utils.api("/api/news/financial-search", {
      market: fsState.market,
      q: fsState.keyword,
      type: fsState.type,
      since: fsState.since,
      page: fsState.page
    }).then(function (data) {
      if (seq !== fsState.seq) {
        return;  // 过期响应丢弃
      }
      fsState.loading = false;
      fsSetBusy(false);
      var rows = (data && data.list) || [];
      fsState.total = (data && data.total) || 0;
      fsState.rows = fsState.rows.concat(rows);
      var card = document.getElementById("FS-02");
      if (card) {
        P.setBadges(card, (data && data.source) || "—", (data && data.caliber) || "—", false);
      }
      if (!rows.length && !fsState.rows.length) {
        fsState.loading = false;
        fsSetBusy(false);
        fsSetState("empty", (data && data.caliber) || "");
        return;
      }
      fsRenderRows(fsState.rows);
      fsState.page += 1;
    }).catch(function (error) {
      if (seq !== fsState.seq) {
        return;
      }
      fsState.loading = false;
      fsSetBusy(false);
      fsSetState("error", (error && error.message) || "", function () {
        loadFinancialSearch(true);
      });
    });
  }

  // ---- 导出到 window.Pages（供兄弟模块与 init 使用） ----
  P.FS_MARKETS = FS_MARKETS;
  P.FS_TIMES = FS_TIMES;
  P.FS_TYPES = FS_TYPES;
  P.fsEscape = fsEscape;
  P.fsHighlight = fsHighlight;
  P.fsIsReport = fsIsReport;
  P.fsLoadCnDetail = fsLoadCnDetail;
  P.fsMarketClass = fsMarketClass;
  P.fsMarketLabel = fsMarketLabel;
  P.fsOpenDetail = fsOpenDetail;
  P.fsRenderRows = fsRenderRows;
  P.fsSetBusy = fsSetBusy;
  P.fsSetCount = fsSetCount;
  P.fsSetState = fsSetState;
  P.fsState = fsState;
  P.fsSyncTabs = fsSyncTabs;
  P.loadFinancialSearch = loadFinancialSearch;
  P.renderFinancialSearch = renderFinancialSearch;
})(window.Pages = window.Pages || {});
