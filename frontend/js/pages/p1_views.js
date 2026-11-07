/* =============================================================
 * OpenBB 中文投研工作台 · pages/p1_views.js
 * 第十轮 P1 新 IA 的 7 个新页：今日研判 / 市场概览 / 财报摘要 / 公告库 /
 * 经理画像 / 工具 / 候选池。
 *
 * 设计依据：DS-R10-v2.1 §5（19 页 IA）§6（四态）§10-P1；原型说明书
 * TD-01~05 / MK-01~04 / FR-01~02 / AN-01 / MG-01 / TL-01~05 / CD-01。
 *
 * 口径约束：
 * - 每区块右上角常驻「数据源 + 口径」双徽章（§9.6）；
 * - 四态就地渲染，空/错严格分离（§7.9）；空值显示「无数据」不补 0；
 * - 红涨绿跌走 CSS 变量，数字走 fmt 千分位；
 * - 所有数据来自真实接口，无 mock（1.8 第 7 条）；
 * - 工具页只做导航入口，渲染仍由各页唯一实现承担（§7.11 功能整合）。
 * ============================================================= */
(function (P) {
  "use strict";

  // ---- 跨模块依赖（core 导出，运行时解析） ----
  var VIEW_IDS = P.VIEW_IDS;
  var api = P.api;
  var buildCard = P.buildCard;
  var el = P.el;
  var fmt = P.fmt;
  var pctClass = P.pctClass;
  var renderTable = P.renderTable;
  var setBadges = P.setBadges;
  var setEmpty = P.setEmpty;
  var setError = P.setError;
  var setLoading = P.setLoading;
  var state = P.state;
  var t = P.t;
  var toast = P.toast;

  /** 取页面容器并清空（新 IA 每页单入口，重进即重建）。 */
  function host(name) {
    var node = document.getElementById(VIEW_IDS[name]);
    if (node) {
      node.innerHTML = "";
    }
    return node;
  }

  /** 把接口信封解包成 {data,error} 并保留口径三字段。 */
  function unwrap(body) {
    if (body && body.error) {
      throw new Error(body.error);
    }
    return body && body.data ? body.data : body || {};
  }

  /** 数字安全化：空值统一「无数据」，不补 0。 */
  function num(value, digits) {
    if (value === null || value === undefined || value === "" || isNaN(Number(value))) {
      return "无数据";
    }
    return fmt(Number(value), digits === undefined ? 2 : digits);
  }

  // =============================================================
  // 一、今日研判（TD-01~05）
  // =============================================================
  function renderToday() {
    var node = host("today");
    if (!node) {
      return;
    }
    var pulse = buildCard("td-pulse", "市场脉搏", "pg-col-6", "/api/sentiment/market · 指数与涨跌家数");
    var feed = buildCard("td-feed", "与你有关 · 要闻", "pg-col-6", "/api/news/flash · 财联社快讯");
    var sector = buildCard("td-sector", "板块领涨 / 领跌", "pg-col-6", "/api/sector/industry · 行业快照");
    var discipline = buildCard("td-discipline", "投资纪律检查", "pg-col-6", "本地规则引擎 · 八原则阈值");
    [pulse, feed, sector, discipline].forEach(function (card) {
      node.appendChild(card.card);
      setLoading(card.body);
    });

    // 1) 市场脉搏：指数 + 涨跌家数
    Promise.all([api("/api/market/index"), api("/api/sentiment/market")])
      .then(function (res) {
        var idx = unwrap(res[0]);
        var senti = unwrap(res[1]);
        var items = idx.items || [];
        setBadges(pulse.card, idx.source || "东财/腾讯", idx.caliber || "指数快照", false);
        pulse.body.innerHTML = "";
        if (!items.length) {
          setEmpty(pulse.body, "暂无指数数据", function () { renderToday(); });
          return;
        }
        var wrap = el("div", "td-pulse-grid");
        items.slice(0, 4).forEach(function (item) {
          var cell = el("div", "td-pulse-cell");
          cell.appendChild(el("div", "td-pulse-name", item.name || item.symbol));
          var price = el("div", "td-pulse-price", num(item.price, 2));
          cell.appendChild(price);
          var pct = el("div", "td-pulse-pct " + pctClass(item.pct), (item.pct > 0 ? "+" : "") + num(item.pct, 2) + "%");
          cell.appendChild(pct);
          wrap.appendChild(cell);
        });
        pulse.body.appendChild(wrap);
        var breadth = senti.breadth || {};
        var line = el("div", "td-breadth");
        // 平盘家数源站不提供时如实显示「无数据」，不补 0（§八 边界：空值不补 0）
        line.textContent = "涨 " + num(breadth.up, 0) + " / 跌 " + num(breadth.down, 0) + " / 平 " + num(breadth.flat, 0);
        if (senti.temperature !== null && senti.temperature !== undefined) {
          line.textContent += " · 情绪温度 " + num(senti.temperature, 1) + (senti.level ? "（" + senti.level + "）" : "");
        }
        if (senti.as_of) {
          line.textContent += " · " + String(senti.as_of).slice(11, 19);
        }
        pulse.body.appendChild(line);
        if (breadth.caliber) {
          pulse.body.appendChild(el("p", "pg-hint", breadth.caliber));
        }
        pulse.body.setAttribute("data-state", "default");
      })
      .catch(function (err) {
        setError(pulse.body, err.message || "市场数据加载失败", function () { renderToday(); });
      });

    // 2) 要闻流
    api("/api/news/flash?limit=8")
      .then(function (body) {
        var data = unwrap(body);
        var items = data.items || [];
        setBadges(feed.card, data.source || "财联社", data.caliber || "快讯", false);
        feed.body.innerHTML = "";
        if (!items.length) {
          setEmpty(feed.body, "暂无快讯", function () { renderToday(); });
          return;
        }
        var list = el("ul", "td-news-list");
        items.forEach(function (item) {
          var li = el("li", "td-news-item");
          var time = el("span", "td-news-time", (item.time || "").slice(5, 16));
          var text = el("span", "td-news-text", item.title || item.summary || "");
          li.appendChild(time);
          li.appendChild(text);
          list.appendChild(li);
        });
        feed.body.appendChild(list);
        feed.body.setAttribute("data-state", "default");
      })
      .catch(function (err) {
        setError(feed.body, err.message || "快讯加载失败", function () { renderToday(); });
      });

    // 3) 板块领涨/领跌
    api("/api/sector/industry")
      .then(function (body) {
        var data = unwrap(body);
        var items = (data.items || []).slice();
        setBadges(sector.card, data.source || "腾讯/东财", data.caliber || "行业快照", false);
        sector.body.innerHTML = "";
        if (!items.length) {
          setEmpty(sector.body, "暂无板块数据", function () { renderToday(); });
          return;
        }
        items.sort(function (a, b) { return Number(b.pct || 0) - Number(a.pct || 0); });
        var top = items.slice(0, 5);
        var bottom = items.slice(-5).reverse();
        var wrap = el("div", "td-sector-cols");
        [["领涨", top], ["领跌", bottom]].forEach(function (pair) {
          var col = el("div", "td-sector-col");
          col.appendChild(el("div", "td-sector-h", pair[0]));
          pair[1].forEach(function (item) {
            var row = el("div", "td-sector-row");
            row.appendChild(el("span", "td-sector-name", item.name || item.board || ""));
            row.appendChild(el("span", "td-sector-pct " + pctClass(item.pct), (item.pct > 0 ? "+" : "") + num(item.pct, 2) + "%"));
            col.appendChild(row);
          });
          wrap.appendChild(col);
        });
        sector.body.appendChild(wrap);
        sector.body.setAttribute("data-state", "default");
      })
      .catch(function (err) {
        setError(sector.body, err.message || "板块数据加载失败", function () { renderToday(); });
      });

    // 4) 纪律检查：本地规则（八原则），逐条给判定与阈值
    renderDiscipline(discipline);
  }

  /** 纪律检查卡：集中度 / 拥挤度 / 现金比 / FCF 背离 四条本地规则。 */
  function renderDiscipline(card) {
    api("/api/portfolio/analysis")
      .then(function (body) {
        var data = unwrap(body);
        setBadges(card.card, data.source || "本地持仓", data.caliber || "本地快照", false);
        card.body.innerHTML = "";
        var rows = [];
        var total = Number(data.total_value || data.total || 0);
        var items = data.items || data.holdings || [];
        var maxWeight = 0;
        items.forEach(function (item) {
          var w = Number(item.weight || item.weight_pct || 0);
          if (w > maxWeight) {
            maxWeight = w;
          }
        });
        rows.push({
          rule: "单票 ≤20% 总资产",
          value: items.length ? num(maxWeight, 2) + "%" : "无数据",
          verdict: !items.length ? "待导入持仓" : (maxWeight > 20 ? "违反" : "通过"),
          tone: !items.length ? "neu" : (maxWeight > 20 ? "neg" : "pos")
        });
        var cash = Number(data.cash_ratio || data.cash_pct || 0);
        rows.push({
          rule: "现金 20–30%",
          value: data.cash_ratio === undefined && data.cash_pct === undefined ? "无数据" : num(cash, 2) + "%",
          verdict: data.cash_ratio === undefined && data.cash_pct === undefined ? "待导入持仓" : (cash < 20 ? "警示" : "通过"),
          tone: data.cash_ratio === undefined && data.cash_pct === undefined ? "neu" : (cash < 20 ? "warn" : "pos")
        });
        rows.push({
          rule: "拥挤度 ≤60 提示 / ≤80 减仓",
          value: "见板块详情",
          verdict: "待核对",
          tone: "neu"
        });
        rows.push({
          rule: "FCF 与利润背离检查",
          value: "见个股 FCF 卡",
          verdict: "待核对",
          tone: "neu"
        });
        var table = el("table", "pg-table");
        var head = el("tr");
        ["纪律条款", "当前值", "判定"].forEach(function (text) {
          head.appendChild(el("th", "", text));
        });
        table.appendChild(head);
        rows.forEach(function (row) {
          var tr = el("tr");
          tr.appendChild(el("td", "", row.rule));
          tr.appendChild(el("td", "", row.value));
          var td = el("td");
          td.appendChild(el("span", "pill " + row.tone, row.verdict));
          tr.appendChild(td);
          table.appendChild(tr);
        });
        card.body.appendChild(table);
        var note = el("p", "pg-hint", "规则阈值取自设计基准 §8 与「八原则」；持仓口径为本地台账，非实时估值。");
        card.body.appendChild(note);
        card.body.setAttribute("data-state", "default");
      })
      .catch(function (err) {
        setError(card.body, err.message || "持仓分析不可用（可先在「我的投资」导入持仓表）", function () { renderToday(); });
      });
  }

  // =============================================================
  // 二、市场概览（MK-01~04）
  // =============================================================
  function renderMarketOverview() {
    var node = host("market");
    if (!node) {
      return;
    }
    var indexCard = buildCard("mk-index", "核心指数", "pg-col-12", "/api/market/index");
    var chartCard = buildCard("mk-chart", "指数走势", "pg-col-12", "/api/market/index-kline · 日线");
    node.appendChild(indexCard.card);
    node.appendChild(chartCard.card);
    setLoading(indexCard.body);
    setLoading(chartCard.body);

    api("/api/market/index")
      .then(function (body) {
        var data = unwrap(body);
        var items = data.items || [];
        setBadges(indexCard.card, data.source || "东财/腾讯", data.caliber || "指数快照", false);
        indexCard.body.innerHTML = "";
        if (!items.length) {
          setEmpty(indexCard.body, "暂无指数数据", function () { renderMarketOverview(); });
          return;
        }
        var wrap = el("div", "mk-idx-row");
        items.forEach(function (item) {
          var cell = el("button", "mk-idx-cell");
          cell.type = "button";
          cell.appendChild(el("div", "mk-idx-name", item.name || item.symbol));
          cell.appendChild(el("div", "mk-idx-price", num(item.price, 2)));
          cell.appendChild(el("div", "mk-idx-pct " + pctClass(item.pct), (item.pct > 0 ? "+" : "") + num(item.pct, 2) + "%"));
          var code = item.symbol || item.code || "000001";
          cell.addEventListener("click", function () {
            history.replaceState(null, "", "#/market?code=" + encodeURIComponent(code));
            renderMarketChart(chartCard, code);
          });
          wrap.appendChild(cell);
        });
        indexCard.body.appendChild(wrap);
        indexCard.body.setAttribute("data-state", "default");
        var first = items[0] && (items[0].symbol || items[0].code);
        if (first) {
          renderMarketChart(chartCard, first);
        }
      })
      .catch(function (err) {
        setError(indexCard.body, err.message || "指数加载失败", function () { renderMarketOverview(); });
      });
  }

  /** 指数 K 线主图（日线收盘 + 成交额副图，双 grid 不混用 px/%）。 */
  function renderMarketChart(card, code) {
    api("/api/market/index-kline?code=" + encodeURIComponent(code) + "&period=daily")
      .then(function (body) {
        var data = unwrap(body);
        var items = data.items || data.bars || [];
        setBadges(card.card, data.source || "东财", data.caliber || "日线 · 前复权", false);
        card.body.innerHTML = "";
        if (!items.length) {
          setEmpty(card.body, "暂无K线数据", function () { renderMarketChart(card, code); });
          return;
        }
        var box = el("div", "mk-chart-box");
        card.body.appendChild(box);
        card.body.setAttribute("data-state", "default");
        var instance = P.mountChart(box);
        if (!instance) {
          return;
        }
        var dates = items.map(function (i) { return i.date || i.time || ""; });
        var closes = items.map(function (i) { return Number(i.close || i.price || 0); });
        var vols = items.map(function (i) { return Number(i.volume || 0); });
        instance.setOption({
          animation: false,
          grid: [
            { left: 56, right: 16, top: 16, height: "58%" },
            { left: 56, right: 16, top: "74%", height: "18%" }
          ],
          xAxis: [
            { type: "category", data: dates, boundaryGap: false, axisLabel: { fontSize: 10 } },
            { type: "category", gridIndex: 1, data: dates, axisLabel: { show: false } }
          ],
          yAxis: [
            { type: "value", scale: true, axisLabel: { fontSize: 10 } },
            { type: "value", gridIndex: 1, axisLabel: { show: false }, splitLine: { show: false } }
          ],
          tooltip: { trigger: "axis" },
          series: [
            { name: "收盘", type: "line", data: closes, showSymbol: false, lineStyle: { width: 1.6 } },
            { name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: vols }
          ]
        });
      })
      .catch(function (err) {
        setError(card.body, err.message || "K线加载失败", function () { renderMarketChart(card, code); });
      });
  }

  // =============================================================
  // 三、财报摘要（FR-01~02）
  // =============================================================
  function renderFinSummary(params) {
    var node = host("fin");
    if (!node) {
      return;
    }
    var query = (params && params.q) || "";
    var searchCard = buildCard("fr-search", "财报检索", "pg-col-12", "/api/stock/financial-report · 三市场");
    var summaryCard = buildCard("fr-summary", "中文摘要", "pg-col-12", "/api/ai/summary · P4 流水线");
    node.appendChild(searchCard.card);
    node.appendChild(summaryCard.card);
    buildSearchBar(searchCard, query, function (value, done) {
      history.replaceState(null, "", "#/fin?q=" + encodeURIComponent(value));
      runFinSearch(searchCard, summaryCard, value);
      if (done) {
        done();
      }
    });
    if (query) {
      runFinSearch(searchCard, summaryCard, query);
    } else {
      // 注意：此处不能用 setEmpty 覆盖 body —— 会把上方的检索条一并清掉（P1 动态验收逮到的缺陷）。
      searchCard.body.appendChild(el("p", "pg-hint", "输入股票代码或简称后回车检索（例：600519 / 宁德时代）"));
      searchCard.body.setAttribute("data-state", "empty");
      renderSummaryPlaceholder(summaryCard);
    }
  }

  /** 统一检索条：输入框 + 按钮，回车即查。 */
  function buildSearchBar(card, value, onSearch) {
    card.body.innerHTML = "";
    var bar = el("div", "p1-search-bar");
    var input = el("input", "p1-search-input");
    input.type = "search";
    input.placeholder = "股票代码 / 简称";
    input.value = value || "";
    var button = el("button", "pg-btn", "检索");
    button.type = "button";
    var busy = false;
    var run = function () {
      if (busy) {
        return;
      }
      var text = (input.value || "").trim();
      if (!text) {
        P.toast("请输入股票代码或简称");
        return;
      }
      busy = true;
      button.disabled = true;
      button.textContent = "检索中…";
      onSearch(text, function () {
        busy = false;
        button.disabled = false;
        button.textContent = "检索";
      });
    };
    button.addEventListener("click", run);
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        run();
      }
    });
    bar.appendChild(input);
    bar.appendChild(button);
    card.body.appendChild(bar);
  }

  /** 财报检索：命中后渲染关键数据表；摘要区给出 P4 接入说明（功能分期，非降级）。 */
  function runFinSearch(searchCard, summaryCard, value) {
    var holder = el("div", "p1-result");
    searchCard.body.appendChild(holder);
    setLoading(holder);
    api("/api/news/financial-search?q=" + encodeURIComponent(value) + "&market=a&limit=20")
      .then(function (body) {
        var data = unwrap(body);
        var items = data.items || data.list || [];  // 该端点契约字段为 list
        setBadges(searchCard.card, data.source || "巨潮/东财", data.caliber || "财报检索", false);
        holder.innerHTML = "";
        if (!items.length) {
          setEmpty(holder, "未检索到匹配财报（可换简称或代码重试）", null);
          return;
        }
        var rows = items.slice(0, 20).map(function (item) {
          return [
            String(item.pubTime || item.date || "").slice(0, 10),
            item.title || item.name || "",
            item.market_label || item.market || "",
            item.type || "定期报告"
          ];
        });
        renderTable(holder, ["披露日", "标题", "市场", "类型"], rows, null, "table-scroll");
        Array.prototype.forEach.call(holder.querySelectorAll("tr"), function (tr, index) {
          var item = items[index];
          if (!item || !item.code) {
            return;
          }
          tr.setAttribute("data-ctx", "stock");
          tr.setAttribute("data-symbol", item.code);
          tr.setAttribute("data-name", item.company || item.name || "");
          tr.setAttribute("data-market", item.market || "a");
        });
        holder.setAttribute("data-state", "default");
        renderSummaryPlaceholder(summaryCard, items[0]);
      })
      .catch(function (err) {
        setError(holder, err.message || "检索失败", null);
      });
  }

  /** 摘要占位：P4 才接 AI 流水线，此处如实说明接入时点（不伪造数据）。 */
  function renderSummaryPlaceholder(card, item) {
    card.body.innerHTML = "";
    var tip = el("p", "pg-hint");
    tip.textContent = item
      ? "已定位「" + (item.title || "") + "」；三段式中文摘要（关键数据表 / 要点 / 风险）由 P4 摘要流水线上线后在此渲染，当前不生成任何推测内容。"
      : "三段式中文摘要（关键数据表 / 要点 / 风险）由 P4 摘要流水线上线后在此渲染。";
    card.body.appendChild(tip);
    card.body.setAttribute("data-state", "empty");
  }

  // =============================================================
  // 四、公告库 · 正文（AN-01）
  // =============================================================
  function renderAnnLib(params) {
    var node = host("ann");
    if (!node) {
      return;
    }
    var query = (params && params.q) || "";
    var card = buildCard("an-search", "公告检索 · 三市场", "pg-col-12", "/api/news/financial-search + financial-detail");
    node.appendChild(card.card);
    buildSearchBar(card, query, function (value, done) {
      var holder = el("div", "p1-result");
      card.body.appendChild(holder);
      setLoading(holder);
      api("/api/news/financial-search?q=" + encodeURIComponent(value) + "&market=a&limit=30")
        .then(function (body) {
          var data = unwrap(body);
          var items = data.items || data.list || [];  // 该端点契约字段为 list
          setBadges(card.card, data.source || "巨潮/东财", data.caliber || "公告检索", false);
          holder.innerHTML = "";
          if (!items.length) {
            setEmpty(holder, "未检索到公告", null);
            done();
            return;
          }
          var table = el("table", "pg-table");
          var head = el("tr");
          ["披露日", "标题", "市场", "操作"].forEach(function (text) {
            head.appendChild(el("th", "", text));
          });
          table.appendChild(head);
          items.slice(0, 30).forEach(function (item) {
            var tr = el("tr");
            tr.appendChild(el("td", "", String(item.pubTime || item.date || "").slice(0, 10)));
            tr.appendChild(el("td", "", item.title || ""));
            tr.appendChild(el("td", "", item.market_label || item.market || ""));
            tr.setAttribute("data-ctx", "news");
            tr.setAttribute("data-title", item.title || "");
            tr.setAttribute("data-url", item.docUrl || "");
            var td = el("td");
            var view = el("button", "pg-btn pg-btn-sm", "看正文");
            view.type = "button";
            view.addEventListener("click", function () {
              openAnnDetail(card, item);
            });
            td.appendChild(view);
            tr.appendChild(td);
            table.appendChild(tr);
          });
          holder.appendChild(table);
          holder.setAttribute("data-state", "default");
          done();
        })
        .catch(function (err) {
          setError(holder, err.message || "检索失败", null);
          done();
        });
    });
    // 注意：此处不能用 setEmpty 覆盖 body —— 会把上方的检索条一并清掉（P1 动态验收逮到的缺陷）。
    card.body.appendChild(el(
      "p",
      "pg-hint",
      query
        ? "按回车或点「检索」重新查询；正文经东财 art_code 链取回（A 股）"
        : "输入代码或简称检索公告；正文经东财 art_code 链取回（A 股）"
    ));
    card.body.setAttribute("data-state", "empty");
  }

  /** 公告正文：分页拼接，>6000 字截断标注（AN-01 口径）；R14 追加中文摘要按钮。 */
  function openAnnDetail(card, item) {
    var code = item.code || item.symbol || "";
    var market = item.market || "a";
    var title = item.title || "";
    var pubTime = (item.pubTime || item.date || "").slice(0, 10);
    var docUrl = item.docUrl || "";
    api(
      "/api/news/financial-detail?code=" + encodeURIComponent(code) +
      "&market=" + encodeURIComponent(market) +
      "&title=" + encodeURIComponent(title) +
      "&date=" + encodeURIComponent(pubTime)
    )
      .then(function (body) {
        var data = unwrap(body);
        var panel = el("div", "p1-detail");
        var title = el("h4", "p1-detail-title", data.title || title || "");
        panel.appendChild(title);

        // R14：中文摘要按钮（未配置时点击后端返回 NOT_CONFIGURED，前端给引导）
        var summaryBar = el("div", "p1-detail-summary-bar");
        var summaryBtn = el("button", "pg-btn pg-btn-sm", "AI 中文摘要");
        summaryBtn.type = "button";
        var summaryBox = el("div", "p1-detail-summary-box");
        summaryBtn.addEventListener("click", function () {
          if (summaryBtn.getAttribute("data-loading") === "1") return;
          summaryBtn.setAttribute("data-loading", "1");
          summaryBtn.textContent = "生成中…";
          summaryBox.innerHTML = "";
          fetch("/api/ai/summary", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              market: market, code: code, title: title,
              pub_time: pubTime, doc_url: docUrl, force: false,
            }),
          })
            .then(function (resp) { return resp.json(); })
            .then(function (sb) {
              if (sb && sb.error) {
                throw new Error(sb.error);
              }
              var sd = (sb && sb.data) || {};
              summaryBtn.textContent = "重新生成摘要";
              summaryBtn.removeAttribute("data-loading");
              renderSummary(summaryBox, sd);
            })
            .catch(function (err) {
              summaryBtn.textContent = "AI 中文摘要";
              summaryBtn.removeAttribute("data-loading");
              var msg = (err && err.message) ? err.message : "摘要生成失败";
              if (msg.indexOf("未配置") >= 0 || msg.indexOf("NOT_CONFIGURED") >= 0) {
                summaryBox.appendChild(el("p", "pg-hint",
                  "未配置 AI 模型：请到「设置 → AI 设置」填写 base_url / api_key / model 后再生成中文摘要。"));
              } else {
                summaryBox.appendChild(el("p", "pg-hint", msg));
              }
            });
        });
        summaryBar.appendChild(summaryBtn);
        panel.appendChild(summaryBar);
        panel.appendChild(summaryBox);

        var content = data.content || data.text || "";
        if (!content) {
          panel.appendChild(el("p", "pg-hint", "该公告未取到正文（部分公告仅有 PDF 原文，源站未提供结构化正文）。"));
        } else {
          var truncated = content.length > 6000;
          var text = truncated ? content.slice(0, 6000) : content;
          var pre = el("pre", "p1-detail-body", text);
          panel.appendChild(pre);
          if (truncated) {
            panel.appendChild(el("p", "pg-hint", "正文超过 6000 字，已截断展示（完整原文见源站链接）。"));
          }
        }
        if (data.source_url) {
          var link = el("a", "p1-detail-link", "打开源站原文");
          link.href = data.source_url;
          link.target = "_blank";
          link.rel = "noopener";
          panel.appendChild(link);
        }
        panel.setAttribute("data-state", "default");
        card.body.appendChild(panel);
        P.toast("正文已加载");
      })
      .catch(function (err) {
        P.toast(err.message || "正文加载失败");
      });
  }

  /** 渲染 R14 中文摘要（关键数据表 / 要点 / 风险 三段式）。 */
  function renderSummary(box, sd) {
    if (!sd) {
      box.appendChild(el("p", "pg-hint", "摘要数据为空。"));
      return;
    }
    if (sd.status === "no_content" || !sd.summary) {
      box.appendChild(el("p", "pg-hint", sd.caliber || "正文未能获取，未生成摘要（不造数）。"));
      return;
    }
    var s = sd.summary;
    if (s.key_table && s.key_table.length) {
      var h = el("h5", "p1-summary-h", "关键数据表");
      box.appendChild(h);
      var tbl = el("table", "pg-table p1-summary-table");
      var trh = el("tr");
      ["指标", "数值", "同比"].forEach(function (x) { trh.appendChild(el("th", "", x)); });
      tbl.appendChild(trh);
      s.key_table.forEach(function (row) {
        var tr = el("tr");
        tr.appendChild(el("td", "", row.metric || ""));
        tr.appendChild(el("td", "", row.value || ""));
        tr.appendChild(el("td", "", row.yoy || ""));
        tbl.appendChild(tr);
      });
      box.appendChild(tbl);
    }
    if (s.points && s.points.length) {
      box.appendChild(el("h5", "p1-summary-h", "要点"));
      var ul = el("ul", "p1-summary-list");
      s.points.forEach(function (p) {
        var li = el("li");
        li.textContent = p;
        ul.appendChild(li);
      });
      box.appendChild(ul);
    }
    if (s.risks && s.risks.length) {
      box.appendChild(el("h5", "p1-summary-h", "风险 / 不确定事项"));
      var ur = el("ul", "p1-summary-list");
      s.risks.forEach(function (p) {
        var li = el("li");
        li.textContent = p;
        ur.appendChild(li);
      });
      box.appendChild(ur);
    }
    var meta = el("p", "pg-hint");
    meta.textContent = (sd.caliber || "") + (sd.model ? "（模型：" + sd.model + "）" : "") +
      (sd.cached ? "（缓存命中）" : "");
    box.appendChild(meta);
  }

  // =============================================================
  // 五、经理画像（MG-01）
  // =============================================================
  function renderManager(params) {
    var node = host("mgr");
    if (!node) {
      return;
    }
    var code = (params && params.code) || state.fundCode || "017811";
    var card = buildCard("mg-profile", "基金经理画像", "pg-col-12", "/api/fund/info · 经理与在管");
    node.appendChild(card.card);
    buildSearchBar(card, code, function (value, done) {
      history.replaceState(null, "", "#/mgr?code=" + encodeURIComponent(value));
      loadManager(card, value, done);
    });
    loadManager(card, code, null);
  }

  /** 经理画像：主用 /api/fund/manager（深度画像 + 在管列表），未命中回落到 fund/info 基础档案。 */
  function loadManager(card, code, done) {
    var holder = el("div", "p1-result");
    card.body.appendChild(holder);
    setLoading(holder);
    api("/api/fund/manager?code=" + encodeURIComponent(code))
      .then(function (body) {
        var data = unwrap(body);
        if (!data.found) {
          throw new Error(data.reason || "该基金未命中经理画像");
        }
        var info = data.manager || {};
        setBadges(card.card, data.source || "东财基金经理", data.caliber || "经理画像", false);
        holder.innerHTML = "";
        var grid = el("div", "mg-grid");
        grid.setAttribute("data-ctx", "fund");
        grid.setAttribute("data-code", code);
        grid.setAttribute("data-name", info.name || "");
        [
          ["基金经理", info.name || "无数据"],
          ["所属公司", info.company || "无数据"],
          ["累计从业", info.tenure_days ? info.tenure_days + " 天（" + num(info.tenure_years, 1) + " 年）" : "无数据"],
          ["在管规模", info.scale_aum === null || info.scale_aum === undefined ? "无数据" : num(info.scale_aum, 2) + " 亿"],
          ["最佳回报", info.best_return === null || info.best_return === undefined ? "无数据" : num(info.best_return, 2) + "%"],
          ["在管基金", (info.fund_count || 0) + " 只"]
        ].forEach(function (pair) {
          var cell = el("div", "mg-cell");
          cell.appendChild(el("div", "mg-k", pair[0]));
          cell.appendChild(el("div", "mg-v", String(pair[1])));
          grid.appendChild(cell);
        });
        holder.appendChild(grid);

        var funds = info.funds || [];
        if (funds.length) {
          holder.appendChild(el("div", "mg-sub", "在管基金（点行进入基金详情）"));
          var table = el("table", "pg-table");
          var head = el("tr");
          ["代码", "基金名称", "操作"].forEach(function (text) { head.appendChild(el("th", "", text)); });
          table.appendChild(head);
          funds.forEach(function (item) {
            var tr = el("tr");
            tr.setAttribute("data-ctx", "fund");
            tr.setAttribute("data-code", item.code);
            tr.setAttribute("data-name", item.name || "");
            tr.appendChild(el("td", "", item.code));
            tr.appendChild(el("td", "", item.name || ""));
            var td = el("td");
            var go = el("button", "pg-btn pg-btn-sm", "基金详情");
            go.type = "button";
            go.addEventListener("click", function () {
              location.hash = "#/fund?code=" + encodeURIComponent(item.code);
            });
            td.appendChild(go);
            tr.appendChild(td);
            table.appendChild(tr);
          });
          holder.appendChild(table);
        }
        var note = el("p", "pg-hint", "口径：规模与最佳回报为经理级指标（长表按经理聚合，非单只基金）；数据日更，24 小时缓存。");
        holder.appendChild(note);
        holder.setAttribute("data-state", "default");
        if (done) {
          done();
        }
      })
      .catch(function () {
        // 未命中经理表时回落基础档案（不伪造画像字段）
        api("/api/fund/info?code=" + encodeURIComponent(code))
          .then(function (body) {
            var data = unwrap(body);
            setBadges(card.card, data.source || "天天基金/东财", data.caliber || "基金档案", false);
            holder.innerHTML = "";
            var grid = el("div", "mg-grid");
            [
              ["基金", (data.name || "无数据") + (data.code ? " " + data.code : "")],
              ["基金经理", data.manager || "无数据"],
              ["成立日期", data.established || "无数据"],
              ["基金公司", data.company || "无数据"]
            ].forEach(function (pair) {
              var cell = el("div", "mg-cell");
              cell.appendChild(el("div", "mg-k", pair[0]));
              cell.appendChild(el("div", "mg-v", String(pair[1])));
              grid.appendChild(cell);
            });
            holder.appendChild(grid);
            holder.appendChild(el("p", "pg-hint", "该基金未出现在经理长表中（新发或代码差异），以上为基础档案字段。"));
            holder.setAttribute("data-state", "default");
            if (done) { done(); }
          })
          .catch(function (err) {
            setError(holder, err.message || "基金档案加载失败", null);
            if (done) { done(); }
          });
      });
  }

  // =============================================================
  // 六、工具（TL-01~05 的导航入口，渲染仍由各页唯一实现）
  // =============================================================
  function renderTools() {
    var node = host("tools");
    if (!node) {
      return;
    }
    var card = buildCard("tl-nav", "工具箱", "pg-col-12", "五类工具 · 跳转各页唯一实现");
    node.appendChild(card.card);
    card.body.innerHTML = "";
    var entries = [
      ["股票筛选器", "12+1 因子 · 快照查询", "#/screener"],
      ["估值分位", "指数/行业/全A 分位", "#/valuation"],
      ["板块拥挤度", "六维分解 · 全板块排行", "#/crowding"],
      ["图表分析", "K线六副图 · 分时", "#/charting"],
      ["候选池", "入池来源 · 偏离价", "#/cand"]
    ];
    var grid = el("div", "tl-grid");
    entries.forEach(function (row) {
      var cell = el("button", "tl-cell");
      cell.type = "button";
      cell.appendChild(el("div", "tl-title", row[0]));
      cell.appendChild(el("div", "tl-desc", row[1]));
      cell.addEventListener("click", function () {
        location.hash = row[2];
      });
      grid.appendChild(cell);
    });
    card.body.appendChild(grid);
    var note = el("p", "pg-hint", "按功能整合规范（§7.11），工具页只做入口，不复制各页渲染实现；组合回测与定投计算器随 P4 上线后加入本页。");
    card.body.appendChild(note);
    card.body.setAttribute("data-state", "default");
  }

  // =============================================================
  // 七、候选池（CD-01）
  // =============================================================
  function renderCandidates() {
    var node = host("cand");
    if (!node) {
      return;
    }
    var card = buildCard("cd-list", "候选池", "pg-col-12", "/api/fund/candidates · 全市场快照筛选");
    node.appendChild(card.card);
    setLoading(card.body);
    api("/api/fund/candidates?limit=200")
      .then(function (body) {
        var data = unwrap(body);
        var items = data.items || data.candidates || [];
        setBadges(card.card, data.source || "本地快照", data.caliber || "M13 基金友好模板", false);
        card.body.innerHTML = "";
        if (!items.length) {
          setEmpty(card.body, "候选池为空（快照未建时后端会自动触发构建，约 1–2 分钟后重试）", function () { renderCandidates(); });
          return;
        }
        var rows = items.map(function (item) {
          return [
            (item.code || "") + " " + (item.name || ""),
            item.industry || item.board || "无数据",
            num(item.price, 2),
            (item.pct > 0 ? "+" : "") + num(item.pct, 2) + "%",
            item.reason || item.template || "快照命中"
          ];
        });
        renderTable(card.body, ["标的", "行业", "现价", "涨跌", "入池依据"], rows, function (index) {
          var item = items[index];
          if (item && item.code) {
            location.hash = "#/stock?symbol=" + encodeURIComponent(item.code);
          }
        }, "table-scroll");
        // GL-03：候选行右键 → 标的菜单（显式声明，避免兜底推断误判）
        Array.prototype.forEach.call(card.body.querySelectorAll("tr"), function (tr, index) {
          var item = items[index];
          if (!item || !item.code) {
            return;
          }
          tr.setAttribute("data-ctx", "stock");
          tr.setAttribute("data-symbol", item.code);
          tr.setAttribute("data-name", item.name || "");
          tr.setAttribute("data-market", item.market || "a");
        });
        card.body.setAttribute("data-state", "default");
      })
      .catch(function (err) {
        setError(card.body, err.message || "候选池加载失败", function () { renderCandidates(); });
      });
  }

  // ---- 导出到 window.Pages ----
  P.renderToday = renderToday;
  P.renderMarketOverview = renderMarketOverview;
  P.renderFinSummary = renderFinSummary;
  P.renderAnnLib = renderAnnLib;
  P.renderManager = renderManager;
  P.renderTools = renderTools;
  P.renderCandidates = renderCandidates;
})(window.Pages = window.Pages || {});
