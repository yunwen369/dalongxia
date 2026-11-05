/* =============================================================
 * OpenBB 中文投研工作台 · pages/portfolio.js
 * 持仓管理 · 组合风险 M10 · xlsx 导入 M11
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
  var fmtPct = P.fmtPct;
  var state = P.state;
  var t = P.t;
  var toast = P.toast;

  // =============================================================
  // 十、#/portfolio 持仓管理（批次 5.3）
  // 结构：P-01 汇总 / P-02 新增表单 / P-03 持仓表 / P-04 市场分布
  // =============================================================
  /** 构建持仓页骨架。
   * @param {HTMLElement} container DOM 容器节点
   * @returns {*} 处理结果
   */
  function buildPortfolioShell(container) {
    container.innerHTML = "";
    var form = el("div", "page-toolbar");
    form.appendChild(el("span", "pg-card-sub", t("portfolio.addPosition")));
    var market = document.createElement("select");
    market.id = "pf-market";
    market.style.cssText =
      "background:var(--bg-panel);border:1px solid var(--border);color:var(--text);border-radius:5px;padding:4px 8px";
    [["a", t("stock.market.a")], ["hk", t("sectors.market.hk")], ["us", t("sectors.market.us")], ["fund", t("page.fund")]].forEach(function (item) {
      var option = document.createElement("option");
      option.value = item[0];
      option.textContent = item[1];
      market.appendChild(option);
    });
    form.appendChild(market);

    /** 构造一个带 id/占位文案/宽度的输入框（估值页查询表单用）。
     * @param {string} id 元素 id / 组件 id
     * @param {*} placeholder 占位文案
     * @param {*} width 宽度（栅格列数）
     * @returns {*} 处理结果
     */
    function field(id, placeholder, width) {
      var input = document.createElement("input");
      input.id = id;
      input.placeholder = placeholder;
      input.style.cssText =
        "width:" + (width || 110) + "px;background:var(--bg-panel);border:1px solid var(--border);" +
        "color:var(--text);border-radius:5px;padding:4px 8px";
      return input;
    }

    form.appendChild(field("pf-symbol", t("th.code"), 100));
    form.appendChild(field("pf-name", t("portfolio.namePlaceholder"), 140));
    form.appendChild(field("pf-shares", t("portfolio.col.shares"), 100));
    form.appendChild(field("pf-cost", t("portfolio.costPlaceholder"), 90));
    var add = el("button", "pg-btn", t("portfolio.addBtn"));
    add.type = "button";
    add.id = "pf-add";
    form.appendChild(add);
    form.appendChild(el("span", "pg-spacer"));
    var refresh = el("button", "pg-btn", t("action.refresh"));
    refresh.type = "button";
    form.appendChild(refresh);
    container.appendChild(form);

    var grid = el("div", "pg-grid");
    grid.id = "portfolio-grid";
    var summary = P.buildCard("P-01", t("portfolio.summaryTitle"), "g-12", t("portfolio.summarySub"));
    summary.body.id = "p01-body";
    grid.appendChild(summary.card);
    // 第七轮 M4：归因（赚亏从哪来 / 风险堆在哪），与 P-01 同用一份台账
    var attr = P.buildCard("P-05", t("portfolio.attr.title"), "g-12", t("portfolio.attr.sub"));
    attr.body.id = "p05-body";
    grid.appendChild(attr.card);
    var dist = P.buildCard("P-04", t("portfolio.distTitle"), "g-5", t("portfolio.distSub"));
    dist.body.id = "p04-body";
    grid.appendChild(dist.card);
    var table = P.buildCard("P-03", t("portfolio.tableTitle"), "g-12", t("portfolio.tableSub"));
    table.body.id = "p03-body";
    grid.appendChild(table.card);
    // 第七轮 M10：组合风险指标（波动/回撤/夏普/α/β/相关性），与归因同用一份台账
    var riskCard = P.buildCard("P-06", t("risk.title"), "g-12", t("risk.sub"));
    riskCard.body.id = "p06-body";
    grid.appendChild(riskCard.card);
    // 第七轮 M11：xlsx 一次性导入（只读导入，覆盖当前台账）
    var xlsxCard = P.buildCard("P-07", t("xlsx.title"), "g-12", t("xlsx.sub"));
    xlsxCard.body.id = "p07-body";
    grid.appendChild(xlsxCard.card);
    container.appendChild(grid);

    add.addEventListener("click", function () {
      var payload = {
        market: market.value,
        symbol: (document.getElementById("pf-symbol").value || "").trim(),
        name: (document.getElementById("pf-name").value || "").trim(),
        shares: document.getElementById("pf-shares").value,
        cost: document.getElementById("pf-cost").value
      };
      if (!payload.symbol || !payload.shares) {
        toast(t("portfolio.inputRequired"));
        return;
      }
      P.apiSend("POST", "/api/portfolio", payload)
        .then(function () {
          toast(t("portfolio.added"));
          ["pf-symbol", "pf-name", "pf-shares", "pf-cost"].forEach(function (id) {
            var node = document.getElementById(id);
            if (node) {
              node.value = "";
            }
          });
          loadPortfolio();
        })
        .catch(function (error) {
          toast(error.message);
        });
    });
    refresh.addEventListener("click", function () {
      loadPortfolio();
    });
  }

  /** 渲染组合归因（第七轮 M4）：权重/贡献、行业暴露、集中度、情景。
   *
   * 与 P-01 的分工：P-01 回答「值多少」，本卡回答「赚亏从哪来、风险堆在哪」。
   * 共用同一份台账（/api/portfolio），不另存数据。
   *
   * @returns {void} 无返回值（异步渲染）
   */
  function loadPortfolioAttribution() {
    var body = document.getElementById("p05-body");
    if (!body) {
      return;
    }
    P.setLoading(body);
    api("/api/portfolio/attribution").then(function (data) {
      var totals = data.totals || {};
      var card = document.getElementById("P-05");
      P.setBadges(card, "本机台账 data/portfolio.json", data.caliber);
      body.innerHTML = "";
      if (!totals.count) {
        P.setEmpty(body, t("portfolio.attr.empty"), loadPortfolioAttribution);
        return;
      }

      // —— 头部 KPI ——
      var kpis = el("div", "pg-kpis");
      var conc = data.concentration || {};
      [
        [t("portfolio.attr.value"), fmt(totals.value, 2), "flat", t("yjb.kpi.yuan")],
        [t("portfolio.attr.pnl"), (totals.pnl > 0 ? "+" : "") + fmt(totals.pnl, 2),
          P.pctClass(totals.pnl), fmtPct(totals.pnl_pct)],
        [t("portfolio.attr.hhi"), fmt(conc.hhi, 4), "flat", conc.level || ""],
        [t("portfolio.attr.count"), String(totals.count), "flat", t("portfolio.attr.countTag")]
      ].forEach(function (row) {
        var box = el("div", "pg-kpi");
        box.appendChild(el("div", "pg-kpi-k", row[0]));
        box.appendChild(el("div", "pg-kpi-v " + row[2], row[1]));
        if (row[3]) {
          box.appendChild(el("div", "pg-kpi-x", row[3]));
        }
        kpis.appendChild(box);
      });
      body.appendChild(kpis);

      // —— 权重与贡献表 ——
      var tableHost = el("div", "");
      body.appendChild(tableHost);
      P.renderTable(tableHost, [
        { title: t("portfolio.col.name"), cls: "l",
          /** 标的：代码 + 名称 + 行业。
           * @param {Object} row 归因行
           * @returns {HTMLElement} 单元格节点
           */
          render: function (row) {
            var box = el("span", "");
            box.appendChild(el("b", "", row.symbol));
            box.appendChild(document.createTextNode(" " + (row.name || "")));
            if (row.industry) {
              // 东财 F10 的 industry 是「食品饮料-饮料-白酒」这类全路径，
              // 卡片列宽有限，展示末级行业名，完整路径放 title 供悬浮查看
              var shortIndustry = String(row.industry).split(/[-—/]/).pop();
              var tag = el("span", "pg-card-sub", " · " + shortIndustry);
              tag.title = row.industry;
              box.appendChild(tag);
            }
            return box;
          } },
        { title: t("portfolio.attr.weight"), num: true,
          render: function (row) { return fmtPct(row.weight); } },
        { title: t("portfolio.attr.pnlPct"), num: true,
          render: function (row) { return el("span", P.pctClass(row.pnl_pct), fmtPct(row.pnl_pct)); } },
        { title: t("portfolio.attr.contribution"), num: true,
          render: function (row) {
            return el("span", P.pctClass(row.contribution),
              (row.contribution > 0 ? "+" : "") + fmt(row.contribution, 2) + "pt");
          } },
        { title: t("portfolio.attr.market"), cls: "l",
          render: function (row) { return row.market_label || row.market; } }
      ], data.items || [], null);

      // —— 行业暴露 + 情景 ——
      var bottom = el("div", "pg-grid");
      var ind = el("div", "card g-7");
      ind.appendChild(el("div", "card-title", t("portfolio.attr.byIndustry")));
      (data.by_industry || []).slice(0, 8).forEach(function (item) {
        var line = el("div", "portfolio-expo");
        line.appendChild(el("span", "portfolio-expo-name", item.name));
        var bar = el("div", "portfolio-expo-bar");
        var fill = el("i", "");
        fill.style.width = Math.min(100, item.weight || 0) + "%";
        bar.appendChild(fill);
        line.appendChild(bar);
        line.appendChild(el("span", "portfolio-expo-val", fmtPct(item.weight)));
        ind.appendChild(line);
      });
      bottom.appendChild(ind);

      var scen = el("div", "card g-5");
      scen.appendChild(el("div", "card-title", t("portfolio.attr.scenario")));
      (data.scenarios || []).forEach(function (item) {
        var line = el("div", "portfolio-scen");
        line.appendChild(el("span", "", item.label));
        line.appendChild(el("b", "down", fmt(item.impact, 0) + " 元"));
        line.appendChild(el("span", "pg-card-sub", "（" + fmtPct(item.impact_pct) + "）"));
        scen.appendChild(line);
      });
      scen.appendChild(el("div", "pg-card-sub", t("portfolio.attr.scenarioNote")));
      bottom.appendChild(scen);
      body.appendChild(bottom);

      if ((data.missing || []).length) {
        body.appendChild(el("div", "pg-card-sub", t("portfolio.attr.missing", { list: data.missing.join("；") })));
      }
      body.appendChild(el("div", "pg-card-sub", data.caliber || ""));
      body.setAttribute("data-state", "default");
    }).catch(function (error) {
      P.setError(body, error.message || t("state.errorHint"), loadPortfolioAttribution);
    });
  }

  /** 渲染持仓页。 */
  function renderPortfolio() {
    var container = document.getElementById(VIEW_IDS.portfolio);
    if (!container.querySelector("#portfolio-grid")) {
      buildPortfolioShell(container);
    }
    loadPortfolio();
    loadPortfolioAttribution();
    loadPortfolioRisk();
    paintXlsxImport();
  }

  // =============================================================
  // 第七轮 M10：组合风险指标（波动 / 回撤 / 夏普 / α / β / 相关性）
  // 口径：按当前持仓权重回溯（非真实逐日净值）；场外基金不纳入；样本<30 日只给数不给结论
  // =============================================================
  /** 加载并渲染组合风险指标。
   *
   * @returns {void} 无返回值（异步渲染）
   */
  function loadPortfolioRisk() {
    var body = document.getElementById("p06-body");
    if (!body) {
      return;
    }
    P.setLoading(body);
    api("/api/portfolio/risk", {}).then(function (data) {
      body.innerHTML = "";
      if (!data.available) {
        body.appendChild(el("div", "pg-state", data.caliber || t("risk.empty")));
        (data.missing || []).forEach(function (note) {
          body.appendChild(el("div", "pg-card-sub", note));
        });
        body.setAttribute("data-state", "empty");
        return;
      }
      var m = data.metrics || {};
      var table = el("table", "pg-table");
      var head = el("thead");
      var headRow = el("tr");
      [t("risk.col.metric"), t("risk.col.value"), t("risk.col.note")].forEach(function (title, index) {
        headRow.appendChild(el("th", index > 0 ? "num" : "", title));
      });
      head.appendChild(headRow);
      table.appendChild(head);
      var tbody = el("tbody");
      [
        [t("risk.annualVol"), fmt(m.annual_vol, 2) + "%", t("risk.annualVolNote", { bench: fmt(m.benchmark_annual, 2) })],
        [t("risk.maxDrawdown"), (m.max_drawdown === null ? t("value.none") : fmt(m.max_drawdown, 2) + "%"),
          m.max_drawdown_days ? t("risk.maxDrawdownNote", { days: m.max_drawdown_days }) : ""],
        [t("risk.sharpe"), fmt(m.sharpe, 2), t("risk.sharpeNote")],
        [t("risk.alpha"), (m.alpha === null ? t("value.none") : fmtPct(m.alpha)), "R²=" + fmt(m.r2, 2)],
        [t("risk.beta"), fmt(m.beta, 2), t("risk.betaNote")],
        [t("risk.annualReturn"), fmtPct(m.annual_return), t("risk.riskFree", { rate: fmt(m.risk_free, 2), source: m.risk_free_source || "" })]
      ].forEach(function (row) {
        var tr = el("tr");
        tr.appendChild(el("td", "", row[0]));
        tr.appendChild(el("td", "num", row[1]));
        tr.appendChild(el("td", "", row[2] || "—"));
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      body.appendChild(table);
      body.appendChild(el("div", "pg-card-sub", data.caliber || ""));

      var pairs = (data.correlation || []).filter(function (item) { return item.value !== null; });
      if (pairs.length) {
        body.appendChild(el("div", "pg-card-sub", t("risk.corrTitle")));
        var corrBox = el("div", "risk-corr");
        pairs.forEach(function (item) {
          var line = el("div", "risk-corr-row");
          line.appendChild(el("span", "", item.left + " ↔ " + item.right));
          line.appendChild(el("b", item.high ? "down" : "", fmt(item.value, 3)
            + (item.high ? t("risk.corrHigh") : "")));
          corrBox.appendChild(line);
        });
        body.appendChild(corrBox);
      }
      if (!data.reliable) {
        body.appendChild(el("div", "pg-card-sub", t("risk.notReliable", { days: data.sample_days })));
      }
      body.setAttribute("data-state", "default");
    }).catch(function (error) {
      P.setError(body, error.message || t("state.errorHint"), loadPortfolioRisk);
      body.setAttribute("data-state", "error");
    });
  }

  // =============================================================
  // 第七轮 M11：xlsx 一次性导入（base64 提交；预览 → 二次确认覆盖）
  // =============================================================
  /** 渲染 xlsx 导入区（文件选择 + 预览 + 确认）。
   *
   * @returns {void} 无返回值
   */
  function paintXlsxImport() {
    var body = document.getElementById("p07-body");
    if (!body) {
      return;
    }
    body.innerHTML = "";
    var bar = el("div", "page-toolbar");
    var input = document.createElement("input");
    input.type = "file";
    input.accept = ".xlsx,.xls";
    input.id = "p07-file";
    var previewBtn = el("button", "pg-btn primary", t("xlsx.preview"));
    previewBtn.type = "button";
    bar.appendChild(el("span", "pg-card-sub", t("xlsx.hint")));
    bar.appendChild(input);
    bar.appendChild(previewBtn);
    body.appendChild(bar);
    var result = el("div", "");
    body.appendChild(result);
    body.setAttribute("data-state", "default");

    var cached = null; // {filename, content_base64}

    /** 读取文件为 base64 并请求预览。
     *
     * @returns {void} 无返回值
     */
    function doPreview() {
      var file = input.files && input.files[0];
      if (!file) {
        toast(t("xlsx.needFile"));
        return;
      }
      var reader = new FileReader();
      reader.onload = function () {
        var dataUrl = String(reader.result || "");
        var base64 = dataUrl.indexOf(",") >= 0 ? dataUrl.split(",")[1] : dataUrl;
        cached = { filename: file.name, content_base64: base64 };
        result.innerHTML = "";
        result.appendChild(el("div", "pg-state", t("xlsx.parsing")));
        P.apiSend("POST", "/api/portfolio/import-xlsx", {
          filename: cached.filename, content_base64: cached.content_base64, confirm: false
        }).then(function (data) {
          renderPreview(data);
        }).catch(function (error) {
          result.innerHTML = "";
          result.appendChild(el("div", "pg-state", error.message || t("state.errorHint")));
          body.setAttribute("data-state", "error");
        });
      };
      reader.readAsDataURL(file);
    }

    /** 渲染预览表（标红不可导入行 + 确认按钮）。
     *
     * @param {Object} data 预览结果
     * @returns {void} 无返回值
     */
    function renderPreview(data) {
      result.innerHTML = "";
      var summary = data.summary || {};
      var kpis = el("div", "pg-kpis");
      [
        [t("xlsx.kpi.total"), String(summary.total || 0)],
        [t("xlsx.kpi.importable"), String(summary.importable || 0)],
        [t("xlsx.kpi.rejected"), String(summary.rejected || 0)]
      ].forEach(function (pair) {
        var box = el("div", "pg-kpi");
        box.appendChild(el("div", "pg-kpi-k", pair[0]));
        box.appendChild(el("div", "pg-kpi-v flat", pair[1]));
        kpis.appendChild(box);
      });
      result.appendChild(kpis);

      var table = el("div", "table-scroll");
      P.renderTable(table, [
        { key: "line", title: "#", num: true },
        { key: "symbol", title: t("xlsx.col.symbol"), render: function (row) {
            return el("b", row.ok ? "" : "down", row.symbol);
          } },
        { key: "name", title: t("xlsx.col.name") },
        { key: "market", title: t("xlsx.col.market") },
        { key: "shares", title: t("xlsx.col.shares"), num: true, render: function (row) { return fmt(row.shares, 2); } },
        { key: "cost", title: t("xlsx.col.cost"), num: true, render: function (row) { return fmt(row.cost, 2); } },
        { key: "price", title: t("xlsx.col.price"), num: true, render: function (row) { return fmt(row.price, 2); } },
        { key: "value", title: t("xlsx.col.value"), num: true, render: function (row) { return fmt(row.value, 0); } },
        { key: "status", title: t("xlsx.col.status"), render: function (row) {
            if (row.ok) {
              return el("span", "down", t("xlsx.ok"));
            }
            return el("span", "up", (row.errors || []).join("；"));
          } }
      ], data.rows || [], {});
      result.appendChild(table);
      (data.warnings || []).forEach(function (note) {
        result.appendChild(el("div", "pg-card-sub", note));
      });

      var bar2 = el("div", "page-toolbar");
      var confirmBtn = el("button", "pg-btn primary", t("xlsx.confirm"));
      confirmBtn.type = "button";
      confirmBtn.disabled = !(summary.importable > 0);
      confirmBtn.addEventListener("click", function () {
        if (!cached) {
          return;
        }
        confirmBtn.disabled = true;
        P.apiSend("POST", "/api/portfolio/import-xlsx", {
          filename: cached.filename, content_base64: cached.content_base64, confirm: true
        }).then(function (payload) {
          var done = payload.result || {};
          toast(t("xlsx.importedToast", { count: done.imported || 0 }));
          result.innerHTML = "";
          result.appendChild(el("div", "pg-state",
            t("xlsx.importedNote", { count: done.imported || 0, skipped: done.skipped || 0 })));
          loadPortfolio();
          loadPortfolioRisk();
        }).catch(function (error) {
          toast(error.message || t("state.errorHint"));
          confirmBtn.disabled = false;
        });
      });
      bar2.appendChild(confirmBtn);
      bar2.appendChild(el("span", "pg-card-sub", t("xlsx.overwriteNote")));
      result.appendChild(bar2);
      body.setAttribute("data-state", "default");
    }

    previewBtn.addEventListener("click", doPreview);
  }

  /** 拉取持仓与分析。
   * @returns {*} 处理结果
   */
  function loadPortfolio() {
    var bodies = ["p01-body", "p03-body", "p04-body", "p05-body"].map(function (id) {
      return document.getElementById(id);
    });
    bodies.forEach(function (body) {
      if (body) {
        P.setLoading(body);
      }
    });
    Promise.all([
      api("/api/portfolio", {}).catch(function (e) { return { error: e.message }; }),
      api("/api/portfolio/analysis", {}).catch(function (e) { return { error: e.message }; })
    ]).then(function (result) {
      paintPortfolio({ listing: result[0], analysis: result[1] });
    });
  }

  /** 渲染持仓汇总 / 明细 / 分布。
   * @param {*} payload 渲染载荷（聚合后的数据）
   * @returns {*} 处理结果
   */
  function paintPortfolio(payload) {
    var listing = payload.listing || {};
    var analysis = payload.analysis || {};

    // ---- P-01 汇总 ----
    var p01 = document.getElementById("p01-body");
    if (p01) {
      if (listing.error || analysis.error) {
        P.setError(p01, listing.error || analysis.error, function () { loadPortfolio(); });
      } else if (!(listing.items || []).length) {
        P.setEmpty(p01, t("portfolio.empty.listing"), function () { loadPortfolio(); });
      } else {
        P.setBadges(document.getElementById("P-01"), analysis.source || t("portfolio.source.local"), analysis.caliber || "", false);
        var totals = analysis.totals || {};
        p01.innerHTML = "";
        var grid = el("div", "pg-metric-grid");
        [
          [t("portfolio.kv.valueCny"), fmt(totals.cny_market_value, 2), "flat"],
          [t("portfolio.kv.costCny"), fmt(totals.cny_cost, 2), "flat"],
          [t("portfolio.kv.pnlCny"), fmt(totals.cny_pnl, 2), P.pctClass(totals.cny_pnl)],
          [t("portfolio.kv.count"), (analysis.count || 0) + " 条", "flat"],
          [t("portfolio.kv.fx"), analysis.fx && analysis.fx.USD ? ("USD " + fmt(analysis.fx.USD, 4) + " / HKD " + fmt(analysis.fx.HKD, 4)) : t("value.none"), "flat"]
        ].forEach(function (item) {
          var cell = el("div", "pg-metric-cell");
          cell.appendChild(el("div", "pg-metric-label", item[0]));
          cell.appendChild(el("div", "pg-metric-value " + item[2], item[1]));
          grid.appendChild(cell);
        });
        p01.appendChild(grid);
        if (analysis.fx_missing) {
          p01.appendChild(el("div", "pg-card-sub", t("portfolio.fxMissing")));
        }
        p01.setAttribute("data-state", "default");
      }
    }

    // ---- P-04 市场分布 ----
    var p04 = document.getElementById("p04-body");
    if (p04) {
      var dist = (analysis.distribution || []).filter(function (item) { return item.value_cny; });
      if (!dist.length) {
        P.setEmpty(p04, t("portfolio.empty.distribution"), function () { loadPortfolio(); });
      } else {
        p04.innerHTML = "";
        var box = el("div", "pg-chart small");
        p04.appendChild(box);
        var chart = P.mountChart(box);
        if (chart) {
          chart.setOption({
            backgroundColor: "transparent",
            tooltip: {
              /** 持仓分布饼图悬浮说明：名称 + 市值（元）+ 占比%。
               * @param {*} info 信息对象
               * @returns {*} 处理结果
               */
              formatter: function (info) {
                return info.name + "<br/>" + fmt(info.value, 2) + " 元（" + fmt(info.percent, 2) + "%）";
              }
            },
            legend: { bottom: 0, textStyle: { color: P.chartTheme().text } },
            series: [
              {
                type: "pie",
                radius: ["45%", "68%"],
                center: ["50%", "45%"],
                label: { color: P.chartTheme().text, fontSize: 11, formatter: "{b}\n{d}%" },
                data: dist.map(function (item) {
                  return { name: item.label, value: item.value_cny };
                })
              }
            ]
          });
        }
        p04.setAttribute("data-state", "default");
      }
    }

    // ---- P-03 持仓明细 ----
    var p03 = document.getElementById("p03-body");
    if (p03) {
      var rows = (analysis.items || []).filter(function (item) { return item.id; });
      if (!rows.length) {
        P.setEmpty(p03, t("portfolio.empty.detail"), function () { loadPortfolio(); });
      } else {
        var wrap = el("div", "");
        p03.innerHTML = "";
        p03.appendChild(wrap);
        P.renderTable(
          wrap,
          [
            { title: t("portfolio.col.market"), key: "market_label" },
            { title: t("th.code"), key: "symbol", cls: "num" },
            { title: t("th.name"), key: "name", cls: "name" },
            { title: t("portfolio.col.shares"), cls: "num", render: function (row) { return fmt(row.shares, 2); } },
            { title: t("portfolio.col.cost"), cls: "num", render: function (row) { return fmt(row.cost, 4); } },
            { title: t("portfolio.col.price"), cls: "num", render: function (row) { return fmt(row.price, 4); } },
            { title: t("portfolio.col.value"), cls: "num", render: function (row) { return fmt(row.market_value, 2); } },
            { title: t("portfolio.col.pnl"), cls: "num", render: function (row) { return P.pctNode(row.pnl); } },
            { title: t("portfolio.col.pnlPct"), cls: "num", render: function (row) { return P.pctNode(row.pnl_pct); } },
            {
              title: t("portfolio.col.action"),
              /** 持仓表「操作」列：渲染删除按钮，点击后调用 DELETE 并重载。
               * @param {*} row 单行记录
               * @returns {*} 处理结果
               */
              render: function (row) {
                var button = el("button", "pg-btn", t("portfolio.col.delete"));
                button.type = "button";
                button.addEventListener("click", function (event) {
                  event.stopPropagation();
                  P.apiSend("DELETE", "/api/portfolio/" + encodeURIComponent(row.id))
                    .then(function () {
                      toast(t("portfolio.deleted"));
                      loadPortfolio();
                    })
                    .catch(function (error) {
                      toast(error.message);
                    });
                });
                return button;
              }
            }
          ],
          rows,
          null,
          null
        );
        // 有持仓时表格渲染完成必须显式收口，否则四态巡检一直停在 loading
        p03.setAttribute("data-state", "default");
      }
    }
  }

  // ---- 导出到 window.Pages（供兄弟模块与 init 使用） ----
  P.buildPortfolioShell = buildPortfolioShell;
  P.loadPortfolio = loadPortfolio;
  P.loadPortfolioAttribution = loadPortfolioAttribution;
  P.loadPortfolioRisk = loadPortfolioRisk;
  P.paintPortfolio = paintPortfolio;
  P.paintXlsxImport = paintXlsxImport;
  P.renderPortfolio = renderPortfolio;
})(window.Pages = window.Pages || {});
