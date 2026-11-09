/* =============================================================
 * OpenBB 中文投研工作台 · pages/news.js
 * 新闻与利好利空（外壳/板块树/资讯流/情绪）
 * 由 tools/split_pages.py 从 pages.js 机械切分生成（R17），逻辑零改动；
 * 重新生成请改 pages.js 后重跑脚本，勿直接编辑本文件的结构性段落。
 * ============================================================= */
(function (P) {
  "use strict";

  // ---- 跨模块依赖（core/兄弟模块导出，运行时解析） ----
  var VIEW_IDS = P.VIEW_IDS;
  var api = P.api;
  var el = P.el;
  var fmt = P.fmt;
  var fsState = P.fsState;
  var state = P.state;
  var t = P.t;
  var toast = P.toast;

  // =============================================================
  // 八、#/news 新闻与利好利空（批次 5.2）
  // 结构：N-01 三级板块树 / N-02 资讯流 / N-03 情绪温度 / N-04 AI 复核（内嵌于卡片）
  // =============================================================
  var newsState = {
    mode: "feed", // feed=多源资讯流，stock=个股新闻
    src: "feed", // 资讯源：feed=综合资讯流 / flash=7×24 快讯 / global=全球三源 / sector=板块命中
    symbol: "",
    board: "科技成长",
    sort: "relevance",
    region: "global",
    tree: null,
    expanded: {}
  };

  /** POST 请求：用于 AI 复核（返回 data，失败抛中文错误）。
   * @param {string} path 接口路径
   * @param {HTMLElement} body 区块容器（.pg-body，四态 data-state 挂在此节点）
   * @returns {*} 处理结果
   */
  function apiPost(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    })
      .then(function (response) {
        return response.json();
      })
      .then(function (payload) {
        if (payload && payload.error) {
          var error = new Error(payload.error);
          error.code = payload.error_code;
          error.data = payload.data;
          throw error;
        }
        return payload.data || {};
      });
  }

  /** 构建新闻页骨架。
   * @param {HTMLElement} container DOM 容器节点
   */

  function buildNewsShell(container) {
    container.innerHTML = "";
    var toolbar = el("div", "page-toolbar");

    // 模式切换：资讯流 / 个股新闻（个股模式需带 symbol）
    var modeTabs = el("div", "pg-tabs");
    modeTabs.id = "N-mode";
    [["feed", t("news.tab.feed")], ["stock", t("news.tab.stock")]].forEach(function (item) {
      var button = el("button", "pg-tab", item[1]);
      button.type = "button";
      button.setAttribute("data-mode", item[0]);
      modeTabs.appendChild(button);
    });
    toolbar.appendChild(modeTabs);

    // 资讯源切换（FN-03 整改）：把此前无界面入口的三个端点接上——
    // 综合=/api/news/ai-feed、快讯=/api/news/flash、全球=/api/news/global、板块=/api/news/sector-search
    var srcTabs = el("div", "pg-tabs");
    srcTabs.id = "N-src";
    [["feed", t("news.src.feed")], ["flash", t("news.src.flash")],
     ["global", t("news.src.global")], ["sector", t("news.src.sector")]].forEach(function (item) {
      var button = el("button", "pg-tab", item[1]);
      button.type = "button";
      button.setAttribute("data-src", item[0]);
      srcTabs.appendChild(button);
    });
    toolbar.appendChild(srcTabs);

    // 排序切换
    var sortTabs = el("div", "pg-tabs");
    sortTabs.id = "N-sort";
    [["relevance", t("news.sort.relevance")], ["time", t("th.time")], ["impact", t("news.sort.impact")]].forEach(function (item) {
      var button = el("button", "pg-tab", item[1]);
      button.type = "button";
      button.setAttribute("data-sort", item[0]);
      sortTabs.appendChild(button);
    });
    toolbar.appendChild(sortTabs);

    // 区域切换（仅资讯流模式生效）
    var regionTabs = el("div", "pg-tabs");
    regionTabs.id = "N-region";
    [["global", t("news.region.global")], ["cn", t("sectors.market.cn")]].forEach(function (item) {
      var button = el("button", "pg-tab", item[1]);
      button.type = "button";
      button.setAttribute("data-region", item[0]);
      regionTabs.appendChild(button);
    });
    toolbar.appendChild(regionTabs);
    toolbar.appendChild(el("span", "pg-spacer"));

    var refresh = el("button", "pg-btn", t("action.reload"));
    refresh.type = "button";
    refresh.id = "news-refresh";
    toolbar.appendChild(refresh);
    container.appendChild(toolbar);

    var grid = el("div", "pg-grid");
    grid.id = "news-grid";

    // N-05：财报/公告搜索入口卡（新增功能 ⑥，48 列通栏，置于资讯流上方）
    var fsEntryCard = P.buildCard("N-05", t("fs.entry.title"), "g-12", "");
    P.setBadges(fsEntryCard.card, "巨潮/东财/HKEX/EDGAR", t("fs.entry.sub"), false);
    var entryBox = el("div", "fs-entry");
    entryBox.id = "fs-entry-btn";
    entryBox.title = t("fs.entry.tip");
    entryBox.appendChild(el("div", "ic", "📑"));
    var entryText = el("div");
    entryText.appendChild(el("div", "t", t("fs.entry.title")));
    entryText.appendChild(el("div", "d", t("fs.entry.desc")));
    entryBox.appendChild(entryText);
    entryBox.appendChild(el("div", "go", t("fs.entry.go")));
    // R6-10 / F6-11：默认就地展开（原实现跳转 #/news?fs=1 会整页替换、资讯流消失）。
    // 「在新页面打开」保留深链语义，供分享/书签使用。
    entryBox.addEventListener("click", function () {
      var host = document.getElementById("fs-inline");
      if (!host) {
        return;
      }
      if (fsState.expanded) {
        host.innerHTML = "";
        host.hidden = true;
        fsState.expanded = false;
        return;
      }
      host.hidden = false;
      fsState.expanded = true;
      P.renderFinancialSearch(host, true);
      host.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    // 深链按钮：单独一个小按钮，点了才跳整页
    var openPage = el("button", "pg-btn dash-btn", t("fs.entry.openPage"));
    openPage.type = "button";
    openPage.addEventListener("click", function (event) {
      event.stopPropagation();
      window.location.hash = "#/news?fs=1";
    });
    entryBox.appendChild(openPage);
    fsEntryCard.body.appendChild(entryBox);
    // 内联结果宿主：默认隐藏，点入口卡后就地展开
    var inlineHost = el("div", "fs-inline");
    inlineHost.id = "fs-inline";
    inlineHost.hidden = !fsState.expanded;
    fsEntryCard.body.appendChild(inlineHost);
    if (fsState.expanded) {
      P.renderFinancialSearch(inlineHost, true);
    }
    fsEntryCard.body.setAttribute("data-state", "default");
    grid.appendChild(fsEntryCard.card);

    // 左栏：板块树 + 情绪温度
    var left = el("div", "g-4");
    left.style.display = "flex";
    left.style.flexDirection = "column";
    left.style.gap = "16px";
    var treeCard = P.buildCard("N-01", t("news.treeTitle"), "", t("news.treeSub"));
    treeCard.body.id = "n01-body";
    treeCard.body.classList.add("short");
    left.appendChild(treeCard.card);
    var sentiCard = P.buildCard("N-03", t("news.sentiTitle"), "", t("news.sentiSub"));
    sentiCard.body.id = "n03-body";
    left.appendChild(sentiCard.card);
    grid.appendChild(left);

    // 右栏：资讯流
    var feedCard = P.buildCard("N-02", t("news.feedTitle"), "g-8", t("news.feedSub"));
    feedCard.body.id = "n02-body";
    grid.appendChild(feedCard.card);
    container.appendChild(grid);

    srcTabs.addEventListener("click", function (event) {
      var button = event.target.closest(".pg-tab");
      if (!button) {
        return;
      }
      newsState.src = button.getAttribute("data-src") || "feed";
      newsState.mode = "feed"; // 任意资讯源都属于资讯流模式，个股模式需重新带 symbol
      syncNewsTabs();
      loadNews(true);
    });
    modeTabs.addEventListener("click", function (event) {
      var button = event.target.closest(".pg-tab");
      if (!button) {
        return;
      }
      var mode = button.getAttribute("data-mode");
      if (mode === "stock" && !newsState.symbol) {
        toast(t("news.stockRedirectToast"));
        return;
      }
      newsState.mode = mode;
      syncNewsTabs();
      loadNews();
    });
    sortTabs.addEventListener("click", function (event) {
      var button = event.target.closest(".pg-tab");
      if (!button) {
        return;
      }
      newsState.sort = button.getAttribute("data-sort");
      syncNewsTabs();
      loadNews(true);
    });
    regionTabs.addEventListener("click", function (event) {
      var button = event.target.closest(".pg-tab");
      if (!button) {
        return;
      }
      newsState.region = button.getAttribute("data-region");
      syncNewsTabs();
      loadNews(true);
    });
    refresh.addEventListener("click", function () {
      newsState.tree = null;
      loadNews(true);
    });
  }

  /** 同步 Tab 选中态与可见性。 */
  function syncNewsTabs() {
    var modeBox = document.getElementById("N-mode");
    var regionBox = document.getElementById("N-region");
    var srcBox = document.getElementById("N-src");
    if (modeBox) {
      modeBox.querySelectorAll(".pg-tab").forEach(function (button) {
        button.classList.toggle("active", button.getAttribute("data-mode") === newsState.mode);
      });
    }
    if (srcBox) {
      // 资讯源只在资讯流模式下有意义（个股新闻固定一个源）
      srcBox.hidden = newsState.mode !== "feed";
      srcBox.querySelectorAll(".pg-tab").forEach(function (button) {
        button.classList.toggle("active", button.getAttribute("data-src") === newsState.src);
      });
    }
    document.querySelectorAll("#N-sort .pg-tab").forEach(function (button) {
      button.classList.toggle("active", button.getAttribute("data-sort") === newsState.sort);
    });
    if (regionBox) {
      // 区域只影响「综合资讯流」这一源，切到快讯/全球/板块时隐藏，避免无效控件
      regionBox.hidden = newsState.mode !== "feed" || newsState.src !== "feed";
      regionBox.querySelectorAll(".pg-tab").forEach(function (button) {
        button.classList.toggle("active", button.getAttribute("data-region") === newsState.region);
      });
    }
  }

  /** 渲染新闻页。
   * @param {Object} params 查询参数对象
   */
  function renderNews(params) {
    var container = document.getElementById(VIEW_IDS.news);
    // 财报/公告搜索：唯一实现页 #/news?fs=1（新增功能 ⑥），不另起第二套外壳
    if (String(params.fs || "") === "1") {
      P.renderFinancialSearch(container);
      return;
    }
    if (container.getAttribute("data-fs") === "1") {
      // 从财报搜索返回：容器内是 FS 视图，先清空并摘掉标记，再走常规新闻页渲染
      container.innerHTML = "";
      container.removeAttribute("data-fs");
    }
    if (params.symbol) {
      newsState.symbol = params.symbol;
      newsState.mode = "stock";
    }
    if (!container.querySelector("#news-grid")) {
      buildNewsShell(container);
    }
    syncNewsTabs();
    loadNews();
  }

  /** 拉取并渲染：板块树 + 资讯流 + 情绪温度。
   * @param {boolean} force 是否强制刷新（true 时跳过本地缓存）
   * @returns {Object} 处理结果
   */
  function loadNews(force) {
    var body = document.getElementById("n02-body");
    var sentiBody = document.getElementById("n03-body");
    var treeBody = document.getElementById("n01-body");
    if (!body) {
      return;
    }
    if (force) {
      newsState.tree = null;
    }
    P.setLoading(body);
    P.setLoading(sentiBody);
    if (!newsState.tree) {
      P.setLoading(treeBody);
    }

    var treeTask = newsState.tree
      ? Promise.resolve(newsState.tree)
      : api("/api/news/board-tree", {});
    var feedTask;
    if (newsState.mode === "stock") {
      feedTask = api("/api/news/stock", { symbol: newsState.symbol, limit: 30 });
    } else if (newsState.src === "flash") {
      // FN-03：7×24 快讯（财联社，不可用回退东财/新浪）
      feedTask = api("/api/news/flash", { limit: 40, sort: "time" });
    } else if (newsState.src === "global") {
      // FN-03：全球快讯三源合并去重
      feedTask = api("/api/news/global", { limit: 60 });
    } else if (newsState.src === "sector") {
      // FN-03：板块命中检索（当前板块树选中项）
      feedTask = api("/api/news/sector-search", {
        sector: newsState.board, sort: newsState.sort, limit: 40
      });
    } else {
      feedTask = api("/api/news/ai-feed", {
        boards: newsState.board,
        sort: newsState.sort,
        limit: 40,
        region: newsState.region
      });
    }
    var sentiTask =
      newsState.mode === "stock"
        ? api("/api/sentiment/stock", { symbol: newsState.symbol })
        : api("/api/sentiment/board", { board: newsState.board }).catch(function (error) {
            return { error: error.message };
          });

    Promise.all([treeTask, feedTask, sentiTask])
      .then(function (result) {
        newsState.tree = result[0];
        paintBoardTree(treeBody, result[0]);
        paintNewsFeed(body, result[1]);
        paintNewsSentiment(sentiBody, result[2]);
      })
      .catch(function (error) {
        P.setError(body, error.message, function () {
          loadNews(true);
        });
        P.setError(sentiBody, error.message, function () {
          loadNews(true);
        });
      });
  }

  /** 渲染三级板块树（一级/二级可折叠，末级可选中）。
   * @param {HTMLElement} body 区块容器（.pg-body，四态 data-state 挂在此节点）
   * @param {*} tree 树形结构（板块树）
   */
  function paintBoardTree(body, tree) {
    if (!body) {
      return;
    }
    var levels = (tree && tree.tree) || [];
    if (!levels.length) {
      P.setEmpty(body, t("news.empty.board"), function () {
        loadNews(true);
      });
      return;
    }
    body.innerHTML = "";
    var meta = el("div", "pg-card-sub",
      t("sectors.versionFormat", { version: tree.version, l1: tree.counts.level1, l2: tree.counts.level2, leaf: tree.counts.leaf }));
    body.appendChild(meta);

    levels.forEach(function (level1) {
      var open1 = newsState.expanded[level1.id] !== false;
      var row1 = el("div", "pg-tree-row level1" + (level1.name === newsState.board ? " active" : ""));
      row1.appendChild(el("span", "pg-tree-toggle", open1 ? "▾" : "▸"));
      row1.appendChild(el("span", "pg-tree-name", level1.name));
      row1.addEventListener("click", function () {
        // 一级节点同样可作过滤条件（展开为其全部末级子孙）
        newsState.expanded[level1.id] = !open1;
        if (newsState.board !== level1.name) {
          newsState.board = level1.name;
          newsState.mode = "feed";
          syncNewsTabs();
          loadNews();
        } else {
          paintBoardTree(body, tree);
        }
      });
      body.appendChild(row1);
      if (!open1) {
        return;
      }
      level1.children.forEach(function (level2) {
        var open2 = newsState.expanded[level2.id] === true;
        var row2 = el("div", "pg-tree-row level2" + (level2.name === newsState.board ? " active" : ""));
        row2.appendChild(el("span", "pg-tree-toggle", open2 ? "▾" : "▸"));
        row2.appendChild(el("span", "pg-tree-name", level2.name));
        row2.addEventListener("click", function () {
          // 二级节点同时承担「展开」和「作为过滤板块」两个职责
          newsState.expanded[level2.id] = !open2;
          if (newsState.board !== level2.name) {
            newsState.board = level2.name;
            newsState.mode = "feed";
            syncNewsTabs();
            loadNews();
          } else {
            paintBoardTree(body, tree);
          }
        });
        body.appendChild(row2);
        if (!open2) {
          return;
        }
        level2.children.forEach(function (leaf) {
          var row3 = el("div", "pg-tree-row level3" + (leaf.name === newsState.board ? " active" : ""));
          row3.appendChild(el("span", "pg-tree-dot", "·"));
          row3.appendChild(el("span", "pg-tree-name", leaf.name));
          row3.addEventListener("click", function () {
            newsState.board = leaf.name;
            newsState.mode = "feed";
            syncNewsTabs();
            loadNews();
          });
          body.appendChild(row3);
        });
      });
    });
    body.setAttribute("data-state", "default");
  }

  /** 渲染资讯流：每条含情绪标签、事件、板块、AI 复核按钮。
   * @param {HTMLElement} body 区块容器（.pg-body，四态 data-state 挂在此节点）
   * @param {*} payload 渲染载荷（聚合后的数据）
   */
  function paintNewsFeed(body, payload) {
    var card = document.getElementById("N-02");
    if (!body) {
      return;
    }
    P.setBadges(card, payload.source, payload.caliber, false);
    var items = payload.items || [];
    if (!items.length) {
      P.setEmpty(body, t("news.empty.feed"), function () {
        loadNews(true);
      });
      return;
    }
    body.innerHTML = "";
    var list = el("div", "pg-feed-list");
    items.forEach(function (item) {
      var sentiment = item.sentiment || {};
      var box = el("article", "pg-feed-item");

      var head = el("div", "pg-feed-head");
      var chipClass = sentiment.label === t("stock.sentiment.up") ? "chip up" : sentiment.label === t("stock.sentiment.down") ? "chip down" : "chip";
      head.appendChild(el("span", chipClass, sentiment.label || t("stock.sentiment.neutral")));
      // AR-21（REQ-20261109-01）：分值为 0 / 缺失时如实显示「未评分」，
      // 不把缺失值伪装成「中性 0.0000」类有效结论；有分值才展示数值与强度
      const rawScore = Number(sentiment.score);
      // 2026-11-09 复查补强：按 4 位小数展示口径，|score| < 0.00005 四舍五入即
      // 显示 0.0000，仍属「未评分」——否则页面会出现「分：0.0000 · 强」这类
      // 把缺失值伪装成有效结论的卡片（openbb-review C7 残留）。
      const hasScore = sentiment.score !== undefined && sentiment.score !== null
        && !Number.isNaN(rawScore) && Math.abs(rawScore) >= 0.00005;
      head.appendChild(el("span", "pg-feed-score",
        hasScore ? t("news.scoreLabel") + fmt(rawScore, 4) + " · " + (sentiment.strength || t("news.weak")) : t("news.scoreMissing")));
      head.appendChild(el("span", "pg-spacer"));
      head.appendChild(P.newsTime(item.time));
      box.appendChild(head);

      var title = el("div", "pg-feed-title", item.title || t("news.titleMissing"));
      if (item.url) {
        title.style.cursor = "pointer";
        title.addEventListener("click", function () {
          window.open(item.url, "_blank", "noopener");
        });
      }
      box.appendChild(title);

      if (item.text) {
        box.appendChild(el("div", "pg-feed-text", item.text.slice(0, 180) + (item.text.length > 180 ? "…" : "")));
      }

      var foot = el("div", "pg-feed-foot");
      foot.appendChild(el("span", "pg-card-sub", item.source || t("news.sourceUnknown")));
      // AR-20（REQ-20261109-01）：空值 / undefined 不渲染标签节点（filter(Boolean)），
      // 杜绝「无文字无图标」的空描边框；事件类长标签加 title 全文（AR-19 防硬裁切）
      (item.channels || []).filter(Boolean).forEach(function (channel) {
        foot.appendChild(el("span", "pg-news-tag", channel));
      });
      (item.boards || []).filter(Boolean).forEach(function (board) {
        foot.appendChild(el("span", "pg-news-tag board", board));
      });
      if ((sentiment.events || []).length) {
        const eventTag = el("span", "pg-news-tag event",
          t("news.eventLabel") + sentiment.events.map(function (event) { return event.name; }).join("、"));
        eventTag.title = eventTag.textContent;  // AR-19：裁切部分悬停可见全文
        foot.appendChild(eventTag);
      }
      var reviewButton = el("button", "pg-btn", t("news.button.review"));
      reviewButton.type = "button";
      var reviewBox = el("div", "pg-review-box");
      reviewButton.addEventListener("click", function () {
        reviewButton.disabled = true;
        reviewButton.textContent = t("news.button.reviewing");
        apiPost("/api/news/ai-review", {
          title: item.title,
          content: item.text,
          board: (item.boards || [])[0] || newsState.board,
          path: newsState.mode === "stock" ? "stock" : "feed",
          sentiment: sentiment.label,
          strength: sentiment.strength
        })
          .then(function (data) {
            reviewButton.textContent = t("news.button.review");
            reviewButton.disabled = false;
            reviewBox.textContent = "AI（" + (data.model || t("news.reviewConfigured")) + "）：" + (data.review && data.review.raw ? data.review.raw.slice(0, 200) : t("news.reviewReturned"));
            reviewBox.className = "pg-review-box ok";
          })
          .catch(function (error) {
            reviewButton.textContent = t("news.button.review");
            reviewButton.disabled = false;
            // AI 未配置属预期契约：给出明确引导，不报错、不冒充结果
            reviewBox.textContent =
              error.code === "NOT_CONFIGURED"
                ? t("copilot.aiNotConfigured")
                : t("news.reviewFailed") + error.message;
            reviewBox.className = "pg-review-box warn";
          });
      });
      foot.appendChild(reviewButton);
      box.appendChild(foot);
      box.appendChild(reviewBox);
      list.appendChild(box);
    });
    body.appendChild(list);
    body.setAttribute("data-state", "default");
  }

  /** 渲染情绪温度卡（N-03）。
   * @param {HTMLElement} body 区块容器（.pg-body，四态 data-state 挂在此节点）
   * @param {*} payload 渲染载荷（聚合后的数据）
   * @returns {*} 处理结果
   */
  function paintNewsSentiment(body, payload) {
    if (!body) {
      return;
    }
    if (!payload || payload.error || payload.temperature === undefined) {
      P.setEmpty(body, (payload && payload.error) || t("senti.empty"), function () {
        loadNews(true);
      });
      return;
    }
    body.innerHTML = "";
    var grid = el("div", "pg-metric-grid");
    [
      [t("news.senti.temperature"), payload.temperature, payload.temperature >= 65 ? "up" : payload.temperature <= 35 ? "down" : "flat"],
      [t("news.senti.level"), payload.level, "flat"],
      [t("stock.sentiment.up"), payload.positive, "up"],
      [t("stock.sentiment.down"), payload.negative, "down"],
      [t("news.senti.sample"), payload.total, "flat"]
    ].forEach(function (item) {
      var cell = el("div", "pg-metric-cell");
      cell.appendChild(el("div", "pg-metric-label", item[0]));
      cell.appendChild(el("div", "pg-metric-value " + item[2], item[1]));
      grid.appendChild(cell);
    });
    body.appendChild(grid);

    if ((payload.top_events || []).length) {
      var events = payload.top_events.map(function (event) {
        return event.name + "×" + event.count;
      }).join("、");
      body.appendChild(el("div", "pg-card-sub", t("news.senti.driving") + events));
    }
    if ((payload.samples || []).length) {
      var list = el("ul", "pg-news-list");
      payload.samples.slice(0, 3).forEach(function (sample) {
        var li = el("li", "pg-news-item");
        li.appendChild(el("span", "pg-news-title", sample.title || ""));
        li.appendChild(el("span", "chip", fmt(sample.score, 4)));
        list.appendChild(li);
      });
      body.appendChild(list);
    }
    body.appendChild(el("div", "pg-card-sub",
      t("news.senti.caliber") + (payload.caliber || t("news.senti.engine")) + t("news.senti.equation")));
    body.setAttribute("data-state", "default");
  }

  // ---- 导出到 window.Pages（供兄弟模块与 init 使用） ----
  P.apiPost = apiPost;
  P.buildNewsShell = buildNewsShell;
  P.loadNews = loadNews;
  P.newsState = newsState;
  P.paintBoardTree = paintBoardTree;
  P.paintNewsFeed = paintNewsFeed;
  P.paintNewsSentiment = paintNewsSentiment;
  P.renderNews = renderNews;
  P.syncNewsTabs = syncNewsTabs;
})(window.Pages = window.Pages || {});
