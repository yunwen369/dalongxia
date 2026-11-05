/* =============================================================
 * OpenBB 中文投研工作台 · pages/sector_detail.js
 * 板块详情页（SD-01~SD-07）—— 第十轮 P2
 *
 * 设计依据：DS-R10-v2.1 §5 页 6「板块详情」+ §12 口径表（净额统一 fundflow 口径，R1）；
 * 原型说明书 SD-01 选择器 / SD-02 头部 / SD-03 走势+相对沪深300 / SD-04 新闻 /
 * SD-05 资金流四档 / SD-06 成分股贡献度 / SD-07 拥挤度分解。
 *
 * 口径约束：
 * - 主力净额一律取 `/api/sector/fundflow`（R1 全站唯一口径），不混用 industry 源；
 * - 相对强弱以 20 日归一（base=100）对比沪深300，基准缺失时只画板块线并如实标注；
 * - 拥挤度分解直接呈现后端 parts（六维，权重和为 1），前端不重算、不猜维度；
 * - 成分股相关性/换手率见 extra：仅作参考值展示，无历史分位，不参与加权；
 * - 四档资金取 fundflow 的 super_big/big/medium/small（新浪成分股聚合），covered 标注覆盖数；
 * - 空值显示「无数据」，不补 0（§八 边界）。
 * ============================================================= */
(function (P) {
  "use strict";

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

  var BOARD_CACHE = { industry: [], concept: [], loaded: false };

  /** 数字安全化：空值「无数据」不补 0。 */
  function num(value, digits) {
    if (value === null || value === undefined || value === "" || isNaN(Number(value))) {
      return "无数据";
    }
    return fmt(Number(value), digits === undefined ? 2 : digits);
  }

  /** 解包信封：Utils.api 已解包时直接返回 body，未解包时取 data（两态兼容）。 */
  function unwrap(body) {
    if (body && body.error) {
      throw new Error(body.error);
    }
    if (!body) {
      return {};
    }
    return body.data || body;
  }

  /** 当前板块：优先 URL 参数，其次会话状态，再次默认半导体。 */
  function currentBoard(params) {
    var board = (params && (params.board || params.sector)) || (state.sectorVal && state.sectorVal.board) || "半导体";
    state.sectorVal = state.sectorVal || {};
    state.sectorVal.board = board;
    return board;
  }

  /** 加载行业+概念名录（一次，供选择器补全）。 */
  function ensureBoards(callback) {
    if (BOARD_CACHE.loaded) {
      callback();
      return;
    }
    Promise.all([
      api("/api/sector/industry").catch(function () { return {}; }),
      api("/api/sector/concept").catch(function () { return {}; })
    ]).then(function (res) {
      var ind = unwrap(res[0]).items || [];
      var con = unwrap(res[1]).items || [];
      BOARD_CACHE.industry = ind.map(function (x) { return { name: x.name || x.board, kind: "industry" }; });
      BOARD_CACHE.concept = con.map(function (x) { return { name: x.name || x.board, kind: "concept" }; });
      BOARD_CACHE.loaded = true;
      callback();
    }).catch(function () {
      callback();
    });
  }

  // =============================================================
  // 渲染入口
  // =============================================================
  function renderSectorDetail(params) {
    var page = document.getElementById("page-sector-val");
    if (!page) {
      return;
    }
    var board = currentBoard(params);
    page.innerHTML = "";
    page.setAttribute("data-board", board);

    // ---- SD-01 选择器条 ----
    var bar = el("div", "sd-selector");
    var input = el("input", "p1-search-input");
    input.type = "search";
    input.value = board;
    input.placeholder = "输入板块名（行业或概念），回车切换";
    input.setAttribute("list", "sd-board-list");
    var list = document.createElement("datalist");
    list.id = "sd-board-list";
    bar.appendChild(input);
    bar.appendChild(list);
    var go = el("button", "pg-btn", "切换板块");
    go.type = "button";
    bar.appendChild(go);
    var hint = el("span", "sd-hint", "");
    bar.appendChild(hint);
    page.appendChild(bar);

    var apply = function () {
      var value = (input.value || "").trim();
      if (!value) {
        return;
      }
      state.sectorVal.board = value;
      history.replaceState(null, "", "#/sector-val?board=" + encodeURIComponent(value));
      renderSectorDetail({ board: value });
    };
    // 聚焦即全选：预填的是当前板块名，用户直接输入应替换而非拼接（P2 验收发现）
    input.addEventListener("focus", function () {
      input.select();
    });
    // 聚焦即全选：预填的是当前板块名，用户直接输入应替换而非拼接（P2 验收发现）
    input.addEventListener("focus", function () {
      input.select();
    });
    go.addEventListener("click", apply);
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        apply();
      }
    });
    ensureBoards(function () {
      list.innerHTML = "";
      BOARD_CACHE.industry.concat(BOARD_CACHE.concept).forEach(function (item) {
        var option = document.createElement("option");
        option.value = item.name;
        list.appendChild(option);
      });
      hint.textContent = "行业 " + BOARD_CACHE.industry.length + " · 概念 " + BOARD_CACHE.concept.length;
    });

    // ---- 卡片骨架 ----
    var head = buildCard("sd-head", "板块概况", "g-12", board + " · /api/sector/fundflow（R1 统一口径）");
    var trend = buildCard("sd-trend", "走势与相对强弱", "g-7", "/api/sector/kline · 相对沪深300（20日归一）");
    var crowd = buildCard("sd-crowd", "拥挤度分解", "g-5", "/api/sector/crowding · 六维加权");
    var flow = buildCard("sd-flow", "资金流四档", "g-5", "/api/sector/fundflow · 新浪成分股聚合");
    var cons = buildCard("sd-cons", "成分股", "g-7", "/api/sector/cons · 贡献度按成交额");
    var news = buildCard("sd-news", "板块资讯", "g-12", "/api/news/sector-search");
    [head, trend, crowd, flow, cons, news].forEach(function (card) {
      page.appendChild(card.card);
      setLoading(card.body);
    });

    loadHead(head, board);
    loadTrend(trend, board);
    loadCrowding(crowd, board);
    loadFlow(flow, board);
    loadCons(cons, board);
    loadNews(news, board);
  }

  // =============================================================
  // SD-02 头部：涨幅 / 成交额 / 主力净额 / 涨跌家数 / 拥挤度
  // =============================================================
  function loadHead(card, board) {
    Promise.all([
      api("/api/sector/fundflow").catch(function (e) { throw e; }),
      api("/api/sector/cons", { board: board }).catch(function () { return {}; }),
      api("/api/sector/crowding", { board: board }).catch(function () { return {}; })
    ]).then(function (res) {
      var flow = unwrap(res[0]);
      var cons = unwrap(res[1]);
      var crowd = unwrap(res[2]);
      var row = (flow.items || []).filter(function (x) { return (x.name || x.board) === board; })[0];
      setBadges(card.card, flow.source || "同花顺", flow.caliber || "主力净额=fundflow 口径", false);
      card.body.innerHTML = "";

      if (!row) {
        setEmpty(card.body, "该板块不在行业资金流名录内（概念板块暂不支持四档资金）", function () { loadHead(card, board); });
        return;
      }
      var items = cons.items || [];
      var up = items.filter(function (x) { return Number(x.pct) > 0; }).length;
      var down = items.filter(function (x) { return Number(x.pct) < 0; }).length;
      var wrap = el("div", "sd-head-grid");
      [
        ["今日涨幅", (row.pct > 0 ? "+" : "") + num(row.pct, 2) + "%", pctClass(row.pct)],
        ["成交额", num(row.amount || (Number(row.inflow) || 0) + (Number(row.outflow) || 0), 2) + " 亿", ""],
        ["主力净额", (row.net_inflow > 0 ? "+" : "") + num(row.net_inflow, 2) + " 亿", pctClass(row.net_inflow)],
        ["净额口径", row.flow_source || "同花顺", ""],
        // cons 端点固定返回前 100 只（接口上限），故如实标注取样口径，不冒充全量
        ["成分涨跌", "涨 " + up + " / 跌 " + down + (items.length ? "（取样 " + items.length + " 只）" : ""), ""],
        ["拥挤度", num(crowd.score, 1) + (crowd.level ? "（" + crowd.level + "）" : ""), Number(crowd.score) >= 80 ? "down" : ""]
      ].forEach(function (cell) {
        var box = el("div", "sd-head-cell");
        box.appendChild(el("div", "sd-head-k", cell[0]));
        box.appendChild(el("div", "sd-head-v " + cell[2], cell[1]));
        wrap.appendChild(box);
      });
      card.body.appendChild(wrap);
      if (crowd.advice) {
        card.body.appendChild(el("p", "pg-hint", "拥挤度建议：" + crowd.advice));
      }
      if (!crowd.score) {
        card.body.appendChild(el("p", "pg-hint", "拥挤度暂不可用（该板块样本不足或非行业板块）"));
      }
      card.body.setAttribute("data-state", "default");
    }).catch(function (err) {
      setError(card.body, err.message || "板块概况加载失败", function () { loadHead(card, board); });
    });
  }

  // =============================================================
  // SD-03 走势：板块指数 + 相对沪深300（20 日归一）
  // =============================================================
  function loadTrend(card, board) {
    var periods = [["daily", "日线"], ["weekly", "周线"], ["monthly", "月线"]];
    var active = "daily";
    var renderBars = function () {
      var bar = card.card.querySelector(".sd-periods");
      if (!bar) {
        bar = el("div", "sd-periods");
        var head = card.card.querySelector(".pg-card-head");
        if (head) {
          head.appendChild(bar);
        }
      }
      bar.innerHTML = "";
      periods.forEach(function (pair) {
        var btn = el("button", "rt" + (pair[0] === active ? " sel" : ""), pair[1]);
        btn.type = "button";
        btn.addEventListener("click", function () {
          active = pair[0];
          renderBars();
          draw();
        });
        bar.appendChild(btn);
      });
    };
    var draw = function () {
      setLoading(card.body);
      Promise.all([
        api("/api/sector/kline", { board: board, period: active }),
        api("/api/market/index-kline", { code: "000300", period: active }).catch(function () { return {}; })
      ]).then(function (res) {
        var data = unwrap(res[0]);
        var bench = unwrap(res[1]);
        var items = data.items || [];
        setBadges(card.card, data.source || "同花顺/申万", data.caliber || "板块指数", false);
        card.body.innerHTML = "";
        if (!items.length) {
          setEmpty(card.body, "暂无板块指数数据", draw);
          return;
        }
        var tail = items.slice(-120);
        var dates = tail.map(function (x) { return x.date; });
        var closes = tail.map(function (x) { return Number(x.close); });
        var base = closes[0] || 1;
        var norm = closes.map(function (v) { return +(v / base * 100).toFixed(2); });

        var benchMap = {};
        (bench.items || []).forEach(function (x) { benchMap[x.date] = Number(x.close); });
        var benchNorm = dates.map(function (d, i) {
          var startDate = dates[0];
          var s = benchMap[startDate];
          var cur = benchMap[d];
          if (!s || !cur) {
            return null;
          }
          return +(cur / s * 100).toFixed(2);
        });

        var box = el("div", "sd-chart");
        card.body.appendChild(box);
        card.body.setAttribute("data-state", "default");
        var chart = P.mountChart(box);
        if (!chart) {
          return;
        }
        var series = [{
          name: board, type: "line", data: norm, showSymbol: false,
          lineStyle: { width: 1.8 }
        }];
        if (benchNorm.filter(function (v) { return v !== null; }).length > 2) {
          series.push({
            name: "沪深300", type: "line", data: benchNorm, showSymbol: false,
            lineStyle: { width: 1.2, type: "dashed" }
          });
        }
        chart.setOption({
          animation: false,
          grid: { left: 48, right: 16, top: 28, bottom: 28 },
          legend: { top: 0, textStyle: { fontSize: 10 } },
          tooltip: { trigger: "axis" },
          xAxis: { type: "category", data: dates, axisLabel: { fontSize: 9 }, boundaryGap: false },
          yAxis: { type: "value", scale: true, axisLabel: { fontSize: 9, formatter: "{value}" } },
          series: series
        });
        var tip = el("p", "pg-hint",
          "以区间首日归一为 100 对比沪深300；" + (benchNorm.filter(function (v) { return v !== null; }).length > 2 ? "基准线已叠加。" : "基准数据缺失，仅显示板块线。"));
        card.body.appendChild(tip);
      }).catch(function (err) {
        setError(card.body, err.message || "走势加载失败", draw);
      });
    };
    renderBars();
    draw();
  }

  // =============================================================
  // SD-07 拥挤度分解
  // =============================================================
  function loadCrowding(card, board) {
    api("/api/sector/crowding", { board: board })
      .then(function (body) {
        var data = unwrap(body);
        setBadges(card.card, data.source || "同花顺", data.caliber || "六维加权", false);
        card.body.innerHTML = "";
        var parts = data.parts || [];
        if (!parts.length) {
          setEmpty(card.body, "暂无拥挤度分解", function () { loadCrowding(card, board); });
          return;
        }
        var score = el("div", "sd-score");
        score.appendChild(el("span", "sd-score-v", num(data.score, 1)));
        score.appendChild(el("span", "sd-score-l " + (Number(data.score) >= 80 ? "down" : ""), data.level || ""));
        card.body.appendChild(score);
        parts.forEach(function (part) {
          var row = el("div", "sd-part");
          var top = el("div", "sd-part-top");
          top.appendChild(el("span", "sd-part-label", part.label + (part.weight ? "（权重 " + Math.round(part.weight * 100) + "%）" : "")));
          top.appendChild(el("span", "sd-part-val", num(part.value, 2) + (part.unit || "")));
          row.appendChild(top);
          var track = el("div", "sd-part-track");
          var fill = el("div", "sd-part-fill");
          var pct = Math.max(0, Math.min(100, Number(part.pct) || 0));
          fill.style.width = pct + "%";
          fill.className = "sd-part-fill" + (pct >= 80 ? " hot" : "");
          track.appendChild(fill);
          row.appendChild(track);
          row.appendChild(el("div", "sd-part-pct", "分位 " + num(part.pct, 1) + "%"));
          card.body.appendChild(row);
        });
        // 参考值（不参与加权）：成分股相关性/换手率只有当期值、无历史分位，故单列
        var extra = data.extra || {};
        var refs = [];
        if (extra.correlation && extra.correlation.corr !== undefined) {
          refs.push(["成分股相关性", num(extra.correlation.corr, 3) + "（" +
            (extra.correlation.samples || "无数据") + " 只 × " + (extra.correlation.window_days || "无数据") + " 日）"]);
        }
        if (extra.turnover) {
          var tv = extra.turnover.mean !== undefined ? extra.turnover.mean : extra.turnover.avg;
          if (tv !== undefined) {
            refs.push(["成分股平均换手率", num(tv, 2) + "%（" + (extra.turnover.samples || "无数据") + " 只）"]);
          }
        }
        if (extra.constituent_count) {
          refs.push(["成分股样本", extra.constituent_count + " 只"]);
        }
        if (refs.length) {
          var refBox = el("div", "sd-ref");
          refs.forEach(function (pair) {
            var line = el("div", "sd-ref-row");
            line.appendChild(el("span", "sd-ref-k", pair[0]));
            line.appendChild(el("span", "sd-ref-v", String(pair[1])));
            refBox.appendChild(line);
          });
          card.body.appendChild(refBox);
        }
        card.body.appendChild(el("p", "pg-hint",
          "样本 " + (data.sample_days || "无数据") + " 个交易日；分数=六维分位加权（权重和 1，缺失维度按可用权重归一），分项由后端计算，前端不重算。" +
          (refs.length ? "上方参考值只有当期值、无历史分位，不参与加权。" : "")));
        card.body.setAttribute("data-state", "default");
      })
      .catch(function (err) {
        setError(card.body, err.message || "拥挤度加载失败", function () { loadCrowding(card, board); });
      });
  }

  // =============================================================
  // SD-05 资金流四档
  // =============================================================
  function loadFlow(card, board) {
    api("/api/sector/fundflow")
      .then(function (body) {
        var data = unwrap(body);
        var row = (data.items || []).filter(function (x) { return (x.name || x.board) === board; })[0];
        setBadges(card.card, data.source || "同花顺", "主力净额=fundflow 口径（R1）", false);
        card.body.innerHTML = "";
        if (!row) {
          setEmpty(card.body, "该板块无四档资金数据（概念板块暂不覆盖）", function () { loadFlow(card, board); });
          return;
        }
        var hasTiers = row.super_big !== null && row.super_big !== undefined;
        if (!hasTiers) {
          // 90 行业里仅部分板块做了成分股四档聚合（flow_source 标明），
          // 未聚合板块只有同花顺口径的主力净额，不能画 0 值条冒充「无资金进出」。
          var onlyNet = el("div", "sd-flow-net");
          onlyNet.appendChild(el("span", "", "主力净额（" + (row.flow_source || "同花顺") + " 口径）"));
          onlyNet.appendChild(el("b", pctClass(row.net_inflow), (row.net_inflow > 0 ? "+" : "") + num(row.net_inflow, 2) + " 亿"));
          card.body.appendChild(onlyNet);
          card.body.appendChild(el("p", "pg-hint",
            "该板块尚未做成分股四档聚合（当前 90 个行业中 14 个已聚合），故只展示主力净额；" +
            "四档明细见「资金流向」页或已聚合板块（如光伏设备）。"));
          card.body.setAttribute("data-state", "default");
          return;
        }
        var tiers = [
          ["超大单", row.super_big], ["大单", row.big],
          ["中单", row.medium], ["小单", row.small]
        ];
        var max = Math.max.apply(null, tiers.map(function (t) { return Math.abs(Number(t[1]) || 0); }).concat([1]));
        tiers.forEach(function (tier) {
          var value = Number(tier[1]);
          var line = el("div", "sd-flow-row");
          line.appendChild(el("span", "sd-flow-k", tier[0]));
          var track = el("div", "sd-flow-track");
          var bar = el("div", "sd-flow-bar " + (value >= 0 ? "pos" : "neg"));
          bar.style.width = Math.max(2, Math.abs(value) / max * 100) + "%";
          track.appendChild(bar);
          line.appendChild(track);
          line.appendChild(el("span", "sd-flow-v " + pctClass(value), (value > 0 ? "+" : "") + num(value, 2) + " 亿"));
          card.body.appendChild(line);
        });
        var net = el("div", "sd-flow-net");
        net.appendChild(el("span", "", "主力净额（超大+大）"));
        net.appendChild(el("b", pctClass(row.net_inflow), (row.net_inflow > 0 ? "+" : "") + num(row.net_inflow, 2) + " 亿"));
        card.body.appendChild(net);
        card.body.appendChild(el("p", "pg-hint",
          "四档由新浪成分股资金流聚合（覆盖 " + (row.covered || "无数据") + " 只）；流入 " +
          num(row.inflow, 2) + " 亿 / 流出 " + num(row.outflow, 2) + " 亿。"));
        card.body.setAttribute("data-state", "default");
      })
      .catch(function (err) {
        setError(card.body, err.message || "资金流加载失败", function () { loadFlow(card, board); });
      });
  }

  // =============================================================
  // SD-06 成分股（贡献度按成交额占比）
  // =============================================================
  function loadCons(card, board) {
    api("/api/sector/cons", { board: board })
      .then(function (body) {
        var data = unwrap(body);
        var items = (data.items || []).slice();
        setBadges(card.card, data.source || "同花顺/申万", data.caliber || "成分股快照", false);
        card.body.innerHTML = "";
        if (!items.length) {
          setEmpty(card.body, "暂无成分股数据", function () { loadCons(card, board); });
          return;
        }
        var totalAmount = items.reduce(function (acc, x) { return acc + (Number(x.amount) || 0); }, 0) || 1;
        items.sort(function (a, b) { return (Number(b.amount) || 0) - (Number(a.amount) || 0); });
        var top = items.slice(0, 30);
        var rows = top.map(function (item) {
          var contrib = ((Number(item.amount) || 0) / totalAmount * 100);
          return [
            item.code + " " + (item.name || ""),
            (item.pct > 0 ? "+" : "") + num(item.pct, 2) + "%",
            num(item.price, 2),
            num(item.amount, 2) + " 亿",
            num(item.weight, 2) + "%",
            num(contrib, 2) + "%"
          ];
        });
        renderTable(card.body, ["标的", "涨幅", "现价", "成交额", "权重", "贡献度"], rows, function (index) {
          var item = top[index];
          if (item && item.code) {
            location.hash = "#/stock?symbol=" + encodeURIComponent(item.code) + "&market=a";
          }
        }, "table-scroll");
        card.body.appendChild(el("p", "pg-hint",
          "共 " + items.length + " 只成分股，按成交额降序取前 30；贡献度=个股成交额/板块合计。点击行进个股研究。"));
        card.body.setAttribute("data-state", "default");
      })
      .catch(function (err) {
        setError(card.body, err.message || "成分股加载失败", function () { loadCons(card, board); });
      });
  }

  // =============================================================
  // SD-04 板块资讯
  // =============================================================
  function loadNews(card, board) {
    api("/api/news/sector-search", { sector: board, limit: 20 })
      .then(function (body) {
        var data = unwrap(body);
        var items = data.items || [];
        setBadges(card.card, data.source || "多源", data.caliber || "板块关键词命中", false);
        card.body.innerHTML = "";
        if (!items.length) {
          setEmpty(card.body, "暂无该板块相关资讯", function () { loadNews(card, board); });
          return;
        }
        var list = el("ul", "sd-news-list");
        items.slice(0, 10).forEach(function (item) {
          var li = el("li", "sd-news-item");
          li.appendChild(el("span", "sd-news-time", String(item.time || "").slice(5, 16)));
          li.appendChild(el("span", "sd-news-text", item.title || item.text || ""));
          li.appendChild(el("span", "sd-news-src", item.source || ""));
          list.appendChild(li);
        });
        card.body.appendChild(list);
        card.body.appendChild(el("p", "pg-hint", "关键词命中 " + items.length + " 条，展示前 10 条；来源与时间由各源原样给出。"));
        card.body.setAttribute("data-state", "default");
      })
      .catch(function (err) {
        setError(card.body, err.message || "板块资讯加载失败", function () { loadNews(card, board); });
      });
  }

  P.renderSectorDetail = renderSectorDetail;
  P.loadSectorDetail = loadHead;
})(window.Pages = window.Pages || {});
