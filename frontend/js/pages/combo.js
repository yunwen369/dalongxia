/* =============================================================
 * OpenBB 中文投研工作台 · pages/combo.js
 * 基金组合 M5/M13/M14（诊断 · 评估 · 候选池 · 权重明细）
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

  function renderFundCombo() {
    var page = document.getElementById(VIEW_IDS["fund-combo"]);
    if (!page) {
      return;
    }
    page.innerHTML = "";
    var tab = state.fundCombo && state.fundCombo.tab ? state.fundCombo.tab : "diagnose";
    var bar = el("div", "page-toolbar");
    [["diagnose", t("fundCombo.tab.diagnose")], ["evaluate", t("fundCombo.tab.evaluate")], ["candidates", t("combo.candidates.tab")]].forEach(function (pair) {
      var btn = el("button", "pg-tab" + (tab === pair[0] ? " active" : ""), pair[1]);
      btn.type = "button";
      btn.addEventListener("click", function () {
        state.fundCombo = state.fundCombo || {};
        state.fundCombo.tab = pair[0];
        renderFundCombo();
      });
      bar.appendChild(btn);
    });
    bar.appendChild(el("span", "pg-spacer"));
    bar.appendChild(el("span", "badge legal", t("fundCombo.disclaimerBadge")));
    page.appendChild(bar);

    var host = el("div", "");
    page.appendChild(host);
    if (tab === "evaluate") {
      paintFundComboEvaluate(host);
    } else if (tab === "candidates") {
      paintFundComboCandidates(host);
    } else {
      paintFundComboDiagnose(host);
    }
    page.setAttribute("data-state", "default");
  }

  /** 诊断 Tab：读台账里的基金持仓，给实际 vs 目标权重、相关性、偏离。
   *
   * @param {HTMLElement} host 容器
   * @returns {void} 无返回值（异步渲染）
   */
  function paintFundComboDiagnose(host) {
    host.innerHTML = "";
    host.appendChild(el("div", "pg-state", t("state.loading")));
    api("/api/fund/combo/diagnose", {}).then(function (data) {
      host.innerHTML = "";
      var holdings = data.holdings || [];
      if (!holdings.length) {
        host.appendChild(el("div", "pg-state", (data.missing || []).join("；") || t("fundCombo.empty")));
        host.setAttribute("data-state", "empty");
        return;
      }

      // KPI：只放客观量，不放"预期收益"
      var kpis = el("div", "pg-kpis");
      var totalActual = holdings.reduce(function (sum, row) { return sum + (row.actual_weight || 0); }, 0);
      var avgScore = holdings.reduce(function (sum, row) { return sum + (row.score || 0); }, 0) / holdings.length;
      [
        [t("fundCombo.kpi.count"), String(holdings.length), "flat", t("fundCombo.kpi.countTag")],
        [t("fundCombo.kpi.actual"), fmt(totalActual, 2) + "%", "flat", t("fundCombo.kpi.actualTag")],
        [t("fundCombo.kpi.avgScore"), fmt(avgScore, 1), "flat", t("fundCombo.kpi.scoreTag")],
        [t("fundCombo.kpi.deviation"), String((data.deviation || []).length), "flat", t("fundCombo.kpi.devTag")]
      ].forEach(function (row) {
        var box = el("div", "pg-kpi");
        box.appendChild(el("div", "pg-kpi-k", row[0]));
        box.appendChild(el("div", "pg-kpi-v " + row[2], row[1]));
        if (row[3]) {
          box.appendChild(el("div", "pg-kpi-x", row[3]));
        }
        kpis.appendChild(box);
      });
      host.appendChild(kpis);

      // 权重对比
      var targetMap = {};
      (data.target || []).forEach(function (row) { targetMap[row.code] = row.weight; });
      var weightCard = el("div", "pg-card");
      weightCard.appendChild(el("div", "pg-card-title", t("fundCombo.card.weight")));
      holdings.forEach(function (row) {
        var line = el("div", "wt");
        line.appendChild(el("span", "n", row.code + (row.name ? " " + row.name : "")));
        var barBox = el("span", "b");
        var bar = el("div", "bar");
        var fill = el("i", "");
        var width = Math.min(100, row.actual_weight || 0);
        fill.style.width = width + "%";
        if ((row.actual_weight || 0) > (data.constraints.single_cap * 100)) {
          fill.classList.add("over");
        }
        bar.appendChild(fill);
        barBox.appendChild(bar);
        line.appendChild(barBox);
        var target = targetMap[row.code];
        // 权重不是涨跌幅，不能用 fmtPct（会给正数加「+」号）
        line.appendChild(el("span", "v",
          fmt(row.actual_weight, 2) + "%" + (target !== undefined ? " / " + fmt(target, 2) + "%" : "")));
        var gap = target !== undefined ? (row.actual_weight || 0) - target : null;
        var gapNode = el("span", "d" + (gap === null ? "" : (gap > 0 ? " up" : gap < 0 ? " down" : "")),
          gap === null ? "—" : (gap > 0 ? "+" : "") + fmt(gap, 1) + "pt");
        line.appendChild(gapNode);
        weightCard.appendChild(line);
      });
      weightCard.appendChild(el("div", "pg-card-sub",
        t("fundCombo.weightNote", { single: fmt(data.constraints.single_cap * 100, 0), cash: fmt(data.constraints.cash_floor * 100, 0) })));
      host.appendChild(weightCard);

      // 相关性
      var corr = (data.correlation || []).filter(function (row) { return row.value !== null; });
      if (corr.length) {
        var corrCard = el("div", "pg-card");
        corrCard.appendChild(el("div", "pg-card-title", t("fundCombo.card.corr")));
        corr.forEach(function (row) {
          var line = el("div", "portfolio-scen");
          line.appendChild(el("span", "", row.left + " ↔ " + row.right));
          line.appendChild(el("b", row.value > 0.90 ? "down" : "", fmt(row.value, 3)
            + (row.value > 0.90 ? t("fundCombo.corrHigh") : "")));
          corrCard.appendChild(line);
        });
        host.appendChild(corrCard);
      }

      // 偏离提示
      var dev = data.deviation || [];
      if (dev.length) {
        var devCard = el("div", "pg-card");
        devCard.appendChild(el("div", "pg-card-title", t("fundCombo.card.dev")));
        dev.forEach(function (row) {
          var line = el("div", "alert-row");
          line.appendChild(el("i", "dot-w"));
          line.appendChild(el("span", "",
            row.code + "：" + t("fundCombo.devLine", { actual: fmt(row.actual, 2), target: fmt(row.target, 2), gap: fmt(row.gap, 1) })));
          devCard.appendChild(line);
        });
        host.appendChild(devCard);
      }

      if (data.cash_note) {
        host.appendChild(el("div", "note warn", data.cash_note));
      }
      (data.missing || []).forEach(function (note) {
        host.appendChild(el("div", "pg-card-sub", note));
      });
      host.appendChild(el("div", "pg-card-sub", data.caliber || ""));
      host.appendChild(el("div", "note legal", data.disclaimer || ""));
      host.setAttribute("data-state", "default");
      // 第七轮 M14：权重明细（基础权重 × 拥挤度折扣）
      paintComboWeight(host, holdings.map(function (row) { return row.code; }));
    }).catch(function (error) {
      host.innerHTML = "";
      host.appendChild(el("div", "pg-state", error.message || t("state.errorHint")));
      host.setAttribute("data-state", "error");
    });
  }

  // =============================================================
  // 第七轮 M14：权重明细（基础权重 × 拥挤度折扣因子）
  // 拥挤度代理 = 该基金第一大重仓股所属行业的同花顺板块分数（调 M3 同一函数）
  // =============================================================
  /** 渲染权重明细表（附在诊断 Tab 下方）。
   *
   * @param {HTMLElement} host 容器
   * @param {Array<string>} codes 基金代码列表
   * @returns {void} 无返回值（异步渲染）
   */
  function paintComboWeight(host, codes) {
    if (!codes || codes.length < 2) {
      return;
    }
    var wrap = el("div", "pg-card");
    wrap.appendChild(el("div", "pg-card-title", t("combo.weight.title")));
    wrap.appendChild(el("div", "pg-card-sub", t("combo.weight.loading")));
    host.appendChild(wrap);
    api("/api/fund/combo/weight", { codes: codes.join(","), risk: "balanced", crowd: "true" }).then(function (data) {
      wrap.innerHTML = "";
      wrap.appendChild(el("div", "pg-card-title", t("combo.weight.title")));
      var totals = data.totals || {};
      var scroll = el("div", "table-scroll");
      P.renderTable(scroll, [
        { key: "code", title: t("combo.weight.col.code"), render: function (row) { return el("b", "", row.code); } },
        { key: "base_weight", title: t("combo.weight.col.base"), num: true, render: function (row) { return fmt(row.base_weight, 2) + "%"; } },
        { key: "board", title: t("combo.weight.col.board"), render: function (row) { return row.board || t("value.none"); } },
        { key: "crowding", title: t("combo.weight.col.crowd"), num: true, render: function (row) { return row.crowding === null ? t("value.none") : fmt(row.crowding, 1); } },
        { key: "crowding_source", title: t("combo.weight.col.crowdsrc"), render: function (row) { return row.crowding_source || t("value.none"); } },
        // R10 / C10：折扣构成两列（规模环比 + 重仓换手）；代理口径或数据缺失显示 —
        { key: "dc_scale", title: t("combo.weight.col.dcScale"), num: true, render: function (row) {
            var c = (row.discount_components || {}).scale_momentum || {};
            var node = (c.available && c.value !== null && c.value !== undefined)
              ? el("span", c.value > 0 ? "up" : "down", fmt(c.value, 1) + "%")
              : el("span", "", t("value.none"));
            node.title = t("combo.weight.dcScaleTip", { pct: (c.pct === null || c.pct === undefined) ? "—" : c.pct })
              + (c.note ? "；" + c.note : "");
            return node;
          } },
        { key: "dc_turnover", title: t("combo.weight.col.dcTurnover"), num: true, render: function (row) {
            var c = (row.discount_components || {}).holding_turnover || {};
            var node = (c.available && c.value !== null && c.value !== undefined)
              ? el("span", "flat", fmt(c.value, 2) + "%")
              : el("span", "", t("value.none"));
            node.title = t("combo.weight.dcTurnoverTip", { pct: (c.pct === null || c.pct === undefined) ? "—" : c.pct })
              + (c.note ? "；" + c.note : "");
            return node;
          } },
        { key: "discount", title: t("combo.weight.col.discount"), num: true, render: function (row) {
            return el("span", row.discount < 0.999 ? "down" : "flat", "×" + fmt(row.discount, 2));
          } },
        { key: "final_weight", title: t("combo.weight.col.final"), num: true, render: function (row) { return el("b", "", fmt(row.final_weight, 2) + "%"); } },
        { key: "delta", title: t("combo.weight.col.delta"), num: true, render: function (row) {
            return el("span", row.delta < 0 ? "down" : (row.delta > 0 ? "up" : "flat"),
              (row.delta > 0 ? "+" : "") + fmt(row.delta, 2) + "pt");
          } }
      ], data.items || [], {});
      wrap.appendChild(scroll);
      wrap.appendChild(el("div", "pg-card-sub",
        t("combo.weight.totals", {
          base: fmt(totals.base, 2),
          final: fmt(totals.final, 2),
          cash: fmt(totals.cash, 2)
        })));
      if ((data.missing || []).length) {
        wrap.appendChild(el("div", "pg-card-sub", data.missing.join("；")));
      }
      wrap.appendChild(el("div", "pg-card-sub", data.caliber || ""));
      wrap.appendChild(el("div", "note legal", data.disclaimer || ""));
    }).catch(function (error) {
      wrap.innerHTML = "";
      wrap.appendChild(el("div", "pg-card-title", t("combo.weight.title")));
      wrap.appendChild(el("div", "pg-card-sub", error.message || t("state.errorHint")));
    });
  }

  // =============================================================
  // 第七轮 M13：全市场候选池（复用 M9 快照 + 基金友好模板）
  // =============================================================
  /** 渲染候选池 Tab：模板条件 + 候选结果表（只筛选，不排序打分）。
   *
   * @param {HTMLElement} host 容器
   * @returns {void} 无返回值（异步渲染）
   */
  function paintFundComboCandidates(host) {
    host.innerHTML = "";
    host.appendChild(el("div", "pg-state", t("combo.candidates.loading")));
    loadCandidates(host, "fund_friendly");
  }

  /** 按模板加载候选池（R9：基金友好 / 红利低波 可切换）。
   *
   * @param {HTMLElement} host 容器
   * @param {string} tplKey 模板键
   * @returns {void} 无返回值（异步渲染）
   */
  function loadCandidates(host, tplKey) {
    api("/api/fund/candidates", { limit: 200, template: tplKey }).then(function (data) {
      host.innerHTML = "";
      var tpl = data.template || {};
      // R9：模板切换条（命中口径随模板变化，条件表与结果表同源刷新）
      var barRow = el("div", "page-toolbar");
      barRow.appendChild(el("span", "", t("combo.candidates.tplSwitch")));
      (data.templates || [{ key: "fund_friendly", label: tpl.label || "基金友好" }]).forEach(function (opt) {
        var btn = el("button", "btn small" + (opt.key === tpl.key ? " active" : ""), opt.label);
        btn.addEventListener("click", function () {
          if (opt.key !== tpl.key) {
            loadCandidates(host, opt.key);
          }
        });
        barRow.appendChild(btn);
      });
      host.appendChild(barRow);
      var condCard = el("div", "pg-card");
      condCard.appendChild(el("div", "pg-card-title", t("combo.candidates.condTitle")));
      var condScroll = el("div", "table-scroll");
      P.renderTable(condScroll, [
        { key: "factor", title: t("combo.candidates.col.factor"), render: function (row) { return row.label || row.factor; } },
        { key: "op", title: t("combo.candidates.col.op"), render: function (row) { return row.op === "gte" ? "≥" : "≤"; } },
        { key: "value", title: t("combo.candidates.col.value"), num: true, render: function (row) { return fmt(row.value, 0); } },
        { key: "note", title: t("combo.candidates.col.note") }
      ], tpl.rules || [], {});
      condCard.appendChild(condScroll);
      condCard.appendChild(el("div", "pg-card-sub", t("combo.candidates.tplNote")));
      host.appendChild(condCard);

      var items = data.items || [];
      var resultCard = el("div", "pg-card");
      resultCard.appendChild(el("div", "pg-card-title",
        t("combo.candidates.resultTitle", { count: data.total_matched || 0 })));
      if (!items.length) {
        resultCard.appendChild(el("div", "pg-state", t("combo.candidates.empty")));
        host.appendChild(resultCard);
        host.setAttribute("data-state", "empty");
        return;
      }
      var scroll = el("div", "table-scroll");
      P.renderTable(scroll, [
        { key: "code", title: t("scr.col.code"), render: function (row) {
            var box = el("span", "");
            box.appendChild(el("b", "", row.code));
            box.appendChild(document.createTextNode(" " + (row.name || "")));
            return box;
          } },
        { key: "board", title: t("scr.col.board") },
        { key: "pe", title: "PE", num: true, render: function (row) { return fmt(row.pe, 2); } },
        { key: "pb", title: "PB", num: true, render: function (row) { return fmt(row.pb, 2); } },
        { key: "roe3y_min", title: "ROE3年最弱", num: true, render: function (row) { return fmt(row.roe3y_min, 2); } },
        { key: "gross", title: t("scr.col.gross"), num: true, render: function (row) { return fmt(row.gross, 2); } },
        { key: "debt_ratio", title: t("scr.col.debt"), num: true, render: function (row) { return fmt(row.debt_ratio, 2); } },
        { key: "mktcap", title: t("scr.col.mktcap"), num: true, render: function (row) { return fmt(row.mktcap, 0); } },
        { key: "dividend_yield", title: t("scr.col.dividend"), num: true, render: function (row) {
            return row.dividend_yield === null || row.dividend_yield === undefined ? t("value.none")
              : fmt(row.dividend_yield, 2) + "%";
          } },
        { key: "exempt", title: t("scr.col.exempt"), render: function (row) {
            return (row.exempt && row.exempt.length) ? t("scr.exemptYes") : "";
          } }
      ], items, {});
      resultCard.appendChild(scroll);
      (data.missing || []).forEach(function (note) {
        resultCard.appendChild(el("div", "pg-card-sub", note));
      });
      resultCard.appendChild(el("div", "pg-card-sub", data.caliber || ""));
      host.appendChild(resultCard);
      host.setAttribute("data-state", "default");
    }).catch(function (error) {
      host.innerHTML = "";
      host.appendChild(el("div", "pg-state", error.message || t("state.errorHint")));
      host.setAttribute("data-state", "error");
    });
  }

  /** 评估 Tab：输入基金代码 → 六维打分 + 相关性 + 目标权重。
   *
   * @param {HTMLElement} host 容器
   * @returns {void} 无返回值
   */
  function paintFundComboEvaluate(host) {
    host.innerHTML = "";
    var bar = el("div", "page-toolbar");
    var input = document.createElement("input");
    input.type = "text";
    input.className = "wl-addbar-input";
    input.id = "fc-codes";
    input.placeholder = t("fundCombo.evalPlaceholder");
    input.value = (state.fundCombo && state.fundCombo.codes) || "";
    bar.appendChild(input);
    var select = document.createElement("select");
    select.className = "fs-select";
    select.id = "fc-risk";
    [["balanced", t("fundCombo.risk.balanced")], ["conservative", t("fundCombo.risk.conservative")], ["aggressive", t("fundCombo.risk.aggressive")]].forEach(function (pair) {
      var option = document.createElement("option");
      option.value = pair[0];
      option.textContent = pair[1];
      select.appendChild(option);
    });
    select.value = (state.fundCombo && state.fundCombo.risk) || "balanced";
    bar.appendChild(select);
    var btn = el("button", "pg-btn primary", t("fundCombo.evalRun"));
    btn.type = "button";
    bar.appendChild(btn);
    host.appendChild(bar);

    var result = el("div", "");
    host.appendChild(result);

    /** 执行评估。
     * @returns {void} 无返回值
     */
    function run() {
      var codes = (input.value || "").trim();
      if (!codes) {
        toast(t("fundCombo.needCodes"));
        return;
      }
      state.fundCombo = state.fundCombo || {};
      state.fundCombo.codes = codes;
      state.fundCombo.risk = select.value;
      result.innerHTML = "";
      result.appendChild(el("div", "pg-state", t("fundCombo.evaling")));
      api("/api/fund/combo/evaluate", { codes: codes, risk: select.value }).then(function (data) {
        result.innerHTML = "";
        var weightMap = {};
        (data.weights || []).forEach(function (row) { weightMap[row.code] = row; });
        P.renderTable(result, [
          { title: t("fundCombo.col.code"), cls: "l",
            render: function (row) { return el("b", "", row.code); } },
          { title: t("fundCombo.col.score"), num: true,
            render: function (row) { return row.score === null ? "—" : fmt(row.score, 1); } },
          { title: t("fundCombo.col.annual"), num: true,
            render: function (row) {
              var value = row.stats.annual_return;
              return el("span", P.pctClass(value), fmtPct(value));
            } },
          { title: t("fundCombo.col.dd"), num: true,
            render: function (row) { return el("span", "down", fmtPct(row.stats.max_drawdown)); } },
          { title: t("fundCombo.col.sharpe"), num: true,
            render: function (row) { return row.stats.sharpe === null ? "—" : fmt(row.stats.sharpe, 2); } },
          { title: t("fundCombo.col.rank"), num: true,
            render: function (row) { return row.rank_pct === null ? "—" : fmt(row.rank_pct, 1) + "%"; } },
          { title: t("fundCombo.col.target"), num: true,
            render: function (row) {
              var hit = weightMap[row.code];
              if (!hit) {
                return "—";
              }
              var node = el("span", "", fmt(hit.weight, 2) + "%");
              if (hit.capped_by) {
                node.title = hit.capped_by;
              }
              return node;
            } }
        ], data.items || [], null);

        (data.correlation || []).forEach(function (row) {
          if (row.value !== null && row.value > 0.90) {
            result.appendChild(el("div", "pg-card-sub",
              t("fundCombo.highCorrLine", { left: row.left, right: row.right, value: fmt(row.value, 3) })));
          }
        });
        result.appendChild(el("div", "pg-card-sub", data.caliber || ""));
        (data.missing || []).forEach(function (note) {
          result.appendChild(el("div", "pg-card-sub", note));
        });
        result.appendChild(el("div", "note legal", data.disclaimer || ""));
      }).catch(function (error) {
        result.innerHTML = "";
        result.appendChild(el("div", "pg-state", error.message || t("state.errorHint")));
      });
    }

    btn.addEventListener("click", run);
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        run();
      }
    });
  }

  /** 渲染板块拥挤度独立页（第七轮 M3 目录重排后从板块页提升）。
   *
   * 阈值按用户口径：>60 提示、>80 建议复核。只陈述交易结构位置，不给买卖指令。
   * 缺失维度在接口 missing 里逐条说明，界面原样呈现（不隐藏降级事实）。
   *
   * @returns {void} 无返回值
   */
  function renderCrowding() {
    var page = document.getElementById(VIEW_IDS.crowding);
    if (!page) {
      return;
    }
    page.innerHTML = "";
    var grid = el("div", "pg-grid");
    var card = P.buildCard("M3-01", t("sectors.crowd.title"), "g-12", t("sectors.crowd.sub"));
    card.body.id = "m301-body";
    grid.appendChild(card.card);
    // 第七轮 M12：全板块排行（与 M3-01 同一打分函数，全市场同口径）
    var rankCard = P.buildCard("M12-01", t("crowd.rank.title"), "g-12", t("crowd.rank.sub"));
    rankCard.body.id = "m1201-body";
    // 行业/概念口径切换（补齐概念排行，不降级）
    var kindBar = el("div", "page-toolbar");
    [["industry", t("sectors.kind.industry")], ["concept", t("sectors.kind.concept")]].forEach(function (item) {
      var btn = el("button", "nav-item" + (state.crowdingKind === item[0] ? " active" : ""));
      btn.textContent = item[1];
      btn.addEventListener("click", function () {
        if (state.crowdingKind === item[0]) {
          return;
        }
        state.crowdingKind = item[0];
        P.paintCrowdingRankings();
      });
      kindBar.appendChild(btn);
    });
    rankCard.card.insertBefore(kindBar, rankCard.body);
    grid.appendChild(rankCard.card);
    page.appendChild(grid);
    P.paintCrowding();
    P.paintCrowdingRankings();
  }

  // ---- 导出到 window.Pages（供兄弟模块与 init 使用） ----
  P.paintComboWeight = paintComboWeight;
  P.paintFundComboCandidates = paintFundComboCandidates;
  P.paintFundComboDiagnose = paintFundComboDiagnose;
  P.paintFundComboEvaluate = paintFundComboEvaluate;
  P.renderCrowding = renderCrowding;
  P.renderFundCombo = renderFundCombo;
})(window.Pages = window.Pages || {});
