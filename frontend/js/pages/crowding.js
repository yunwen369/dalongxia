/* =============================================================
 * OpenBB 中文投研工作台 · pages/crowding.js
 * 拥挤度 M3/M12（查询 + 全板块排行）
 * 由 tools/split_pages.py 从 pages.js 机械切分生成（R17），逻辑零改动；
 * 重新生成请改 pages.js 后重跑脚本，勿直接编辑本文件的结构性段落。
 * ============================================================= */
(function (P) {
  "use strict";

  // ---- 跨模块依赖（core/兄弟模块导出，运行时解析） ----
  var api = P.api;
  var el = P.el;
  var fmt = P.fmt;
  var state = P.state;
  var t = P.t;
  var toast = P.toast;

  // =============================================================
  // 第七轮 M12：拥挤度全板块排行（复用 M3 同一函数，首次扫描约 1 分钟）
  // =============================================================
  /** 渲染全板块排行（排行榜 + 等级分布 + 热力条）。
   *
   * @returns {void} 无返回值（异步渲染）
   */
  function paintCrowdingRankings() {
    var body = document.getElementById("m1201-body");
    if (!body) {
      return;
    }
    body.innerHTML = "";
    body.appendChild(el("div", "pg-state", t("crowd.rank.loading")));
    body.setAttribute("data-state", "loading");
    api("/api/sector/crowding/rankings", { kind: state.crowdingKind, limit: 30 }).then(function (data) {
      body.innerHTML = "";
      // 后台计算中：不冒充结果，显示计算中态并稍后自动重试
      if (data.computing) {
        body.appendChild(el("div", "pg-state", data.caliber || t("crowd.rank.loading")));
        body.setAttribute("data-state", "loading");
        setTimeout(paintCrowdingRankings, 8000);
        return;
      }
      var counts = data.level_counts || {};
      var kpis = el("div", "pg-kpis");
      [
        [t("crowd.rank.scanned"), (data.scanned || 0) + " / " + (data.total || 0), "flat"],
        [t("crowd.rank.high"), String(counts["高位"] || 0), (counts["高位"] ? "up" : "flat")],
        [t("crowd.rank.warn"), String(counts["偏高"] || 0), "flat"],
        [t("crowd.rank.normal"), String(counts["正常"] || 0), "down"]
      ].forEach(function (row) {
        var box = el("div", "pg-kpi");
        box.appendChild(el("div", "pg-kpi-k", row[0]));
        box.appendChild(el("div", "pg-kpi-v " + row[2], row[1]));
        kpis.appendChild(box);
      });
      body.appendChild(kpis);

      var items = data.items || [];
      if (!items.length) {
        body.appendChild(el("div", "pg-state", t("crowd.rank.empty")));
        body.setAttribute("data-state", "empty");
        return;
      }
      var scroll = el("div", "table-scroll");
      P.renderTable(scroll, [
        { key: "rank", title: "#", num: true, render: function (row) { return String(items.indexOf(row) + 1); } },
        { key: "board", title: t("crowd.rank.col.board"), render: function (row) { return el("b", "", row.board); } },
        { key: "score", title: t("crowd.rank.col.score"), num: true, render: function (row) {
            var cls = row.score >= 80 ? "up" : (row.score >= 60 ? "flat" : "down");
            return el("span", cls, fmt(row.score, 1));
          } },
        { key: "level", title: t("crowd.rank.col.level"), render: function (row) { return row.level; } },
        { key: "parts", title: t("crowd.rank.col.parts"), render: function (row) {
            return (row.parts || []).map(function (part) {
              return part.label + " " + fmt(part.pct, 0) + "%";
            }).join(" · ");
          } }
      ], items, {});
      body.appendChild(scroll);

      // 热力条：颜色越深越拥挤（橙→红，非红涨绿跌语义）
      var heat = el("div", "crowd-heat");
      items.slice(0, 30).forEach(function (row) {
        var cell = el("span", "crowd-heat-cell", row.board);
        var ratio = Math.min(100, Math.max(0, row.score)) / 100;
        cell.style.background = "rgba(224,90,62," + (0.12 + ratio * 0.75).toFixed(2) + ")";
        cell.title = row.board + " " + fmt(row.score, 1) + "（" + row.level + "）";
        heat.appendChild(cell);
      });
      body.appendChild(heat);

      if ((data.failed || []).length) {
        body.appendChild(el("div", "pg-card-sub", t("crowd.rank.failed", { list: data.failed.join("、") })));
      }
      body.appendChild(el("div", "pg-card-sub", data.caliber || ""));
      body.setAttribute("data-state", "default");
    }).catch(function (error) {
      P.setError(body, error.message || t("state.errorHint"), paintCrowdingRankings);
      body.setAttribute("data-state", "error");
    });
  }

  /** 渲染拥挤度查询区（输入 + 结果 + 缺失说明）。
   *
   * @returns {void} 无返回值
   */
  function paintCrowding() {
    var body = document.getElementById("m301-body");
    if (!body) {
      return;
    }
    body.innerHTML = "";
    var bar = el("div", "page-toolbar");
    var input = document.createElement("input");
    input.type = "text";
    input.className = "wl-addbar-input";
    input.id = "crowd-board";
    input.placeholder = t("sectors.crowd.placeholder");
    input.value = state.sectors.crowdBoard || "";
    bar.appendChild(input);
    var btn = el("button", "pg-btn primary", t("sectors.crowd.query"));
    btn.type = "button";
    bar.appendChild(btn);
    bar.appendChild(el("span", "pg-card-sub", t("sectors.crowd.hint")));
    body.appendChild(bar);

    var result = el("div", "");
    body.appendChild(result);
    body.setAttribute("data-state", "default");

    /** 查询指定板块。
     * @returns {void} 无返回值
     */
    function run() {
      var board = (input.value || "").trim();
      if (!board) {
        toast(t("sectors.crowd.needBoard"));
        return;
      }
      state.sectors.crowdBoard = board;
      result.innerHTML = "";
      result.appendChild(el("div", "pg-state", t("state.loading")));
      api("/api/sector/crowding", { board: board }).then(function (data) {
        var card = document.getElementById("M3-01");
        P.setBadges(card, "同花顺行业日线", data.caliber);
        result.innerHTML = "";
        var head = el("div", "pg-kpis");
        var main = el("div", "pg-kpi");
        main.appendChild(el("div", "pg-kpi-k", t("sectors.crowd.score")));
        main.appendChild(el("div", "pg-kpi-v " + (data.score >= 80 ? "up" : data.score >= 60 ? "flat" : "down"),
          fmt(data.score, 1)));
        main.appendChild(el("div", "pg-kpi-x", data.level + " · " + data.advice));
        head.appendChild(main);
        result.appendChild(head);

        var list = el("div", "crowd-parts");
        (data.parts || []).forEach(function (part) {
          var line = el("div", "crowd-part");
          line.appendChild(el("span", "crowd-part-name", part.label));
          var track = el("div", "crowd-part-bar");
          var fill = el("i", "");
          fill.style.width = (part.pct === null || part.pct === undefined ? 0 : part.pct) + "%";
          track.appendChild(fill);
          line.appendChild(track);
          line.appendChild(el("span", "crowd-part-val",
            t("sectors.crowd.rankLabel") + fmt(part.pct, 1) + "%"));
          line.appendChild(el("span", "crowd-part-raw", fmt(part.value, 2) + part.unit));
          list.appendChild(line);
        });
        result.appendChild(list);

        (data.missing || []).forEach(function (note) {
          result.appendChild(el("div", "pg-card-sub", note));
        });
        body.setAttribute("data-state", "default");
      }).catch(function (error) {
        result.innerHTML = "";
        result.appendChild(el("div", "pg-state", error.message || t("state.errorHint")));
        body.setAttribute("data-state", "error");
      });
    }

    btn.addEventListener("click", run);
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        run();
      }
    });
  }

  /** 渲染个股页。
   * @param {Object} params 查询参数对象
   */

  // ---- 导出到 window.Pages（供兄弟模块与 init 使用） ----
  P.paintCrowding = paintCrowding;
  P.paintCrowdingRankings = paintCrowdingRankings;
})(window.Pages = window.Pages || {});
