/* =============================================================
 * OpenBB 中文投研工作台 · pages/fund_extras.js
 * 基金页三项补强（第十轮 P2）：FD-01 阶段四分位 / FD-02 回撤分析 / FD-04 经理入口
 *
 * 设计依据：DS-R10-v2.1 §5 页 8；原型说明书 FD-01（阶段涨幅四分位排名）、
 *          FD-02（三档走势含回撤分析）、FD-04（经理卡 → 经理画像页）。
 *
 * 口径约束：
 * - 排名与分位全部来自 `/api/fund/performance` 的 periods[].rank（形如 "4819/5658"），
 *   前端只做分位换算与档位映射，不臆造排名；
 * - 四分位定义：名次/总数 ≤0.25 为 Q1（前 25%），≤0.5 为 Q2，≤0.75 为 Q3，其余 Q4；
 * - 回撤取自 `/api/fund/drawdown`（最大回撤/峰谷日期/当前回撤），不重复计算；
 * - 缺失显示「无数据」，不补 0。
 * ============================================================= */
(function (P) {
  "use strict";

  var buildCard = P.buildCard;
  var el = P.el;
  var fmt = P.fmt;
  var pctClass = P.pctClass;
  var setBadges = P.setBadges;

  /** 空值安全数字。 */
  function num(value, digits) {
    if (value === null || value === undefined || value === "" || isNaN(Number(value))) {
      return "无数据";
    }
    return fmt(Number(value), digits === undefined ? 2 : digits);
  }

  /** 带符号百分比。 */
  function pct(value, digits) {
    if (value === null || value === undefined || value === "" || isNaN(Number(value))) {
      return "无数据";
    }
    var v = Number(value);
    return (v > 0 ? "+" : "") + fmt(v, digits === undefined ? 2 : digits) + "%";
  }

  /** 解析 "4819/5658" → {rank, total, ratio}；无法解析返回 null。 */
  function parseRank(text) {
    if (!text || typeof text !== "string") {
      return null;
    }
    var parts = text.split("/");
    if (parts.length !== 2) {
      return null;
    }
    var rank = Number(parts[0]);
    var total = Number(parts[1]);
    if (!rank || !total) {
      return null;
    }
    return { rank: rank, total: total, ratio: rank / total };
  }

  /** 分位 → 四分位档位与样式。 */
  function quartile(ratio) {
    if (ratio <= 0.25) {
      return { label: "Q1 前25%", cls: "pos" };
    }
    if (ratio <= 0.5) {
      return { label: "Q2 25-50%", cls: "pos" };
    }
    if (ratio <= 0.75) {
      return { label: "Q3 50-75%", cls: "warn" };
    }
    return { label: "Q4 后25%", cls: "neg" };
  }

  /** 在 F-03 之后插入卡片（幂等）。 */
  function ensureCard(id, title, sub, gridClass, afterId) {
    var existing = document.getElementById(id);
    if (existing) {
      return { card: existing, body: existing.querySelector(".pg-body") };
    }
    var page = document.getElementById("page-fund");
    if (!page) {
      return null;
    }
    var built = buildCard(id, title, gridClass || "", sub || "");
    var anchor = afterId ? document.getElementById(afterId) : null;
    if (anchor && anchor.parentNode) {
      anchor.parentNode.insertBefore(built.card, anchor.nextSibling);
    } else {
      page.appendChild(built.card);
    }
    return built;
  }

  /** FD-01：阶段涨幅 + 同类排名 + 四分位档。 */
  function paintQuartile(payload) {
    var holder = ensureCard("F-07", "阶段表现 · 四分位", "/api/fund/performance · periods[].rank", "", "F-03");
    if (!holder || !holder.body) {
      return;
    }
    var perf = (payload && payload.performance) || {};
    var periods = perf.periods || [];
    var body = holder.body;
    setBadges(holder.card, perf.source || "天天基金", perf.caliber || "同类排名", false);
    body.innerHTML = "";
    if (!periods.length) {
      P.setEmpty(body, "暂无阶段业绩与排名", null);
      return;
    }
    var table = el("table", "pg-table");
    var head = el("tr");
    ["区间", "涨幅", "同类排名", "分位", "档位"].forEach(function (text) {
      head.appendChild(el("th", "", text));
    });
    table.appendChild(head);
    periods.forEach(function (item) {
      var tr = el("tr");
      tr.appendChild(el("td", "", item.period || "无数据"));
      tr.appendChild(el("td", pctClass(item.growth), pct(item.growth, 2)));
      var parsed = parseRank(item.rank);
      tr.appendChild(el("td", "", item.rank || "无数据"));
      if (parsed) {
        tr.appendChild(el("td", "", num(parsed.ratio * 100, 1) + "%"));
        var td = el("td");
        var q = quartile(parsed.ratio);
        td.appendChild(el("span", "pill " + q.cls, q.label));
        tr.appendChild(td);
      } else {
        tr.appendChild(el("td", "", "无数据"));
        tr.appendChild(el("td", "", "无数据"));
      }
      table.appendChild(tr);
    });
    body.appendChild(table);
    body.appendChild(el("p", "pg-hint",
      "分位=名次/同类总数（越小越好）；Q1 前 25% / Q2 25-50% / Q3 50-75% / Q4 后 25%。排名口径由源站给出，非本系统计算。"));
    body.setAttribute("data-state", "default");
  }

  /** FD-02：回撤分析（最大回撤 / 峰谷 / 当前）。 */
  function paintDrawdown(payload) {
    var holder = ensureCard("F-08", "回撤分析", "/api/fund/drawdown · 峰谷与当前回撤", "", "F-02");
    if (!holder || !holder.body) {
      return;
    }
    var dd = (payload && payload.drawdown) || {};
    var body = holder.body;
    setBadges(holder.card, dd.source || "天天基金", dd.caliber || "净值序列回撤", false);
    body.innerHTML = "";
    if (dd.max_drawdown === undefined || dd.max_drawdown === null) {
      P.setEmpty(body, dd.error || "暂无回撤数据（净值序列不足）", null);
      return;
    }
    var grid = el("div", "sd-head-grid");
    [
      ["最大回撤", pct(dd.max_drawdown, 2), "down"],
      ["峰值日", dd.peak_date || "无数据", ""],
      ["谷底日", dd.trough_date || "无数据", ""],
      ["当前回撤", pct(dd.current_drawdown, 2), Number(dd.current_drawdown) < 0 ? "down" : ""],
      ["样本区间", (dd.sample_from || "无数据") + " 起", ""]
    ].forEach(function (cell) {
      var box = el("div", "sd-head-cell");
      box.appendChild(el("div", "sd-head-k", cell[0]));
      box.appendChild(el("div", "sd-head-v " + cell[2], String(cell[1])));
      grid.appendChild(box);
    });
    body.appendChild(grid);
    body.appendChild(el("p", "pg-hint",
      "最大回撤取样本区间内峰值到谷底的最大跌幅（" + (dd.sample_size || "无数据") +
      " 个净值点）；当前回撤=最新净值相对区间峰值；负值表示仍在回撤中。"));
    body.setAttribute("data-state", "default");
  }

  /** FD-04：把 F-01 的经理值改成可点击，跳到经理画像页。 */
  function bindManagerLink(payload) {
    var info = (payload && payload.info) || {};
    if (!info.manager) {
      return;
    }
    var head = document.getElementById("f01-body");
    if (!head) {
      return;
    }
    // F-01 的字段值是 <b> 包裹（span 只在外层标签），两者一起找
    var nodes = head.querySelectorAll("b, span");
    Array.prototype.forEach.call(nodes, function (node) {
      if (node.textContent !== info.manager) {
        return;
      }
      if (node.getAttribute("data-mgr-link") === "1") {
        return;
      }
      node.setAttribute("data-mgr-link", "1");
      node.style.cursor = "pointer";
      node.style.textDecoration = "underline dotted";
      node.title = "查看经理画像";
      node.addEventListener("click", function () {
        location.hash = "#/mgr?code=" + encodeURIComponent(info.code || "");
      });
    });
  }


  /** FD-03 补充：重仓股行业配置（按前十大重仓的个股行业聚合占比）。 */
  function paintIndustry(payload) {
    var holding = (payload && payload.holding) || {};
    var items = (holding.items || []).slice(0, 10);
    if (!items.length) {
      return;
    }
    var holder = ensureCard("F-09", "行业配置", "前十大重仓按个股行业聚合 · /api/stock/profile", "", "F-04");
    if (!holder || !holder.body) {
      return;
    }
    var body = holder.body;
    P.setLoading(body);
    var tasks = items.map(function (item) {
      return P.api("/api/stock/profile", { symbol: item.code, market: "a" })
        .then(function (res) {
          var data = (res && res.data) || res || {};
          return { ratio: Number(item.ratio) || 0, industry: data.industry || "未分类", name: item.name || item.code };
        })
        .catch(function () {
          return { ratio: Number(item.ratio) || 0, industry: "未分类", name: item.name || item.code };
        });
    });
    Promise.all(tasks).then(function (rows) {
      var buckets = {};
      rows.forEach(function (row) {
        buckets[row.industry] = (buckets[row.industry] || 0) + row.ratio;
      });
      var list = Object.keys(buckets).map(function (key) {
        return { industry: key, ratio: buckets[key] };
      }).sort(function (a, b) { return b.ratio - a.ratio; });
      var total = list.reduce(function (acc, x) { return acc + x.ratio; }, 0) || 1;
      var max = list.length ? list[0].ratio : 1;
      body.innerHTML = "";
      list.forEach(function (row) {
        var line = el("div", "sd-flow-row");
        line.appendChild(el("span", "sd-flow-k", row.industry));
        var track = el("div", "sd-flow-track");
        var bar = el("div", "sd-flow-bar pos");
        bar.style.width = Math.max(3, row.ratio / max * 100) + "%";
        track.appendChild(bar);
        line.appendChild(track);
        line.appendChild(el("span", "sd-flow-v", num(row.ratio, 2) + "%"));
        body.appendChild(line);
      });
      body.appendChild(el("p", "pg-hint",
        "口径：仅统计前十大重仓股（合计 " + num(total, 2) + "%），行业取个股档案 industry；" +
        "未分类=档案缺行业字段。非全持仓行业分布，不据此推断整体风格。"));
      setBadges(holder.card, holding.source || "天天基金", holding.caliber || "前十大重仓", false);
      body.setAttribute("data-state", "default");
    }).catch(function (err) {
      P.setError(body, err.message || "行业配置加载失败", null);
    });
  }

  /** 入口：在 paintFund 之后调用。 */
  function paintFundExtras(payload) {
    paintQuartile(payload);
    paintDrawdown(payload);
    paintIndustry(payload);
    bindManagerLink(payload);
  }

  P.paintFundExtras = paintFundExtras;
})(window.Pages = window.Pages || {});
