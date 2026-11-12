/* OpenBB-CN 业务组件层（RF-20261112-01 阶段4 抽取自 views.js）——
   card/table/kpis/feed/chartCard 五组件 + esc/num/cls/pct/pctCell/plainNum/levelCell/d 基础 helper。
   window.V6_HELPERS 导出（原 9 键不变，新增 4 键增量兼容）；views.js 头部解构引用，体内零改写。 */
(function () {
  "use strict";
  var esc = function (value) {
    return String(value == null ? "—" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  };
  var num = function (v, d) {
    return typeof v === "number" && isFinite(v) ? v.toFixed(d == null ? 2 : d) : "—";
  };
  var cls = function (v) { return v >= 0 ? "up" : "down"; };
  var pct = function (v) { return (v >= 0 ? "+" : "") + num(v, 2) + "%"; };
  var d = function (map, key) { return (map[key] && map[key].data) || {}; };

  /** 通用卡片容器。
   *
   * @param {string} title 标题
   * @param {string} body 卡片正文 HTML
   * @param {string} [note] 右上角接口说明
   * @param {string} [extra] 标题前的图标字符
   * @param {string} [chip] 标题后的参数摘要（如「影视院线 · A股」），空则不渲染
   * @returns {string} 卡片 HTML
   */
  function card(title, body, note, extra, chip) {
    return '<div class="card v-card">' +
      '<div class="card-head"><span class="card-title"><span class="badge-ico">' +
      (extra || "·") + "</span>" + esc(title) + "</span>" +
      (chip ? '<span class="param-chip">' + esc(chip) + "</span>" : "") +
      (note ? '<span class="card-note">' + esc(note) + "</span>" : "") + "</div>" +
      '<div class="v-body">' + body + "</div></div>";
  }

  /** 通用表格：cols = [[key, label, width, cssClass], ...]。 */
  function table(cols, rows, emptyNote) {
    if (!rows || !rows.length) {
      return '<div class="v-empty">' + esc(emptyNote || "该接口当前返回空数据") + "</div>";
    }
    var head = cols.map(function (c) {
      return "<th" + (c[2] ? ' style="width:' + c[2] + '"' : "") +
        (c[3] ? ' class="' + c[3] + '"' : "") + ">" + esc(c[1]) + "</th>";
    }).join("");
    var body = rows.map(function (row, i) {
      return "<tr>" + cols.map(function (c) {
        return "<td" + (c[3] ? ' class="' + c[3] + '"' : "") + ">" + String(c[4] ? c[4](row[c[0]], row, i) : esc(row[c[0]])) + "</td>";
      }).join("") + "</tr>";
    }).join("");
    return '<div class="v-table-wrap"><table class="v-table"><thead><tr>' +
      head + "</tr></thead><tbody>" + body + "</tbody></table></div>";
  }

  /** 指标卡网格。 */
  function kpis(items) {
    return '<div class="v-kpi-grid">' + items.map(function (item) {
      return '<div class="v-kpi"><span class="k-label">' + esc(item[0]) + "</span>" +
        '<span class="k-value ' + (item[2] || "") + '">' + String(item[1]) + "</span></div>";
    }).join("") + "</div>";
  }

  /** 资讯/文本流。 */
  function feed(items, limit) {
    if (!items || !items.length) { return '<div class="v-empty">暂无资讯</div>'; }
    return '<div class="v-feed">' + items.slice(0, limit || 8).map(function (item) {
      return '<div class="v-feed-row"><span class="f-time mono">' + esc(item.time || "—") +
        '</span><span class="f-title">' + esc(item.title || item.text || "—") +
        '</span><span class="f-src">' + esc(item.source || "—") + "</span></div>";
    }).join("") + "</div>";
  }

  var rankCell = function (v, row, i) {
    return '<span class="rank-badge r' + (i + 1) + '">' + (i + 1) + "</span>";
  };
  var pctCell = function (v) {
    return '<span class="' + cls(v) + ' mono">' + pct(v) + "</span>";
  };
  var plainNum = function (v) { return '<span class="mono">' + num(v, 2) + "</span>"; };
  var levelCell = function (v) {
    if (!v) { return "—"; }
    var c = v === "高位" ? "high" : (v === "偏高" ? "mid" : "low");
    return '<span class="level-pill ' + c + '">' + esc(v) + "</span>";
  };


  function chartCard(spec, series) {
    if (!series || !series.length) {
      return card(spec.title || "走势", emptyChartSVG(), spec.src || "真实接口", "图");
    }
    var body = spec.type === "line" ? lineSVG(series)
      : spec.type === "donut" ? donutSVG(series)
        : spec.type === "heat" ? heatHTML(series) : barSVG(series);
    return card(spec.title + " · " + series.length + " 项", body, spec.src || "真实接口派生", "图");
  }

  function barSVG(series) {
    var w = 420, h = 132, pad = 22;
    var rows = series.slice(0, 14);
    var max = Math.max.apply(null, rows.map(function (x) { return Math.abs(Number(x.value)) || 0; })) || 1;
    var bw = (w - 2 * pad) / (rows.length || 1);
    var bars = rows.map(function (item, i) {
      var v = Number(item.value) || 0;
      var bh = Math.max(2, (Math.abs(v) / max) * (h - 48));
      var y = v >= 0 ? (h - 26 - bh) : (h - 26);
      var color = v >= 0 ? "#ff4b5f" : "#00d68f";
      var x = pad + i * bw + bw * 0.18;
      return '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + (bw * 0.64).toFixed(1) +
        '" height="' + bh.toFixed(1) + '" rx="2" fill="' + color + '" opacity=".9"><title>' +
        esc(item.label) + ": " + num(v, 2) + "</title></rect>" +
        '<text x="' + (x + bw * 0.32).toFixed(1) + '" y="' + (h - 8) +
        '" text-anchor="middle" font-size="8" fill="#96a6b6">' +
        esc(String(item.label).slice(0, 3)) + "</text>";
    }).join("");
    return '<svg class="v-chart-svg" viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="none">' +
      '<line x1="' + pad + '" y1="' + (h - 26) + '" x2="' + (w - pad) + '" y2="' + (h - 26) + '" stroke="#1e364c"/>' +
      '<line x1="' + pad + '" y1="' + ((h - 26) / 2).toFixed(1) + '" x2="' + (w - pad) + '" y2="' +
      ((h - 26) / 2).toFixed(1) + '" stroke="#1c3247" stroke-dasharray="3 3" stroke-width=".6"/>' + bars + "</svg>";
  }

  function lineSVG(series) {
    var w = 420, h = 132, pad = 22;
    var rows = series.slice(-40);
    var vals = rows.map(function (x) { return Number(x.value) || 0; });
    if (!vals.length) { return ""; }
    var min = Math.min.apply(null, vals);
    var max = Math.max.apply(null, vals);
    var span = (max - min) || 1;
    var pts = vals.map(function (v, i) {
      return [pad + (i * (w - 2 * pad)) / (vals.length - 1 || 1), h - 26 - ((v - min) / span) * (h - 52)];
    });
    var line = pts.map(function (p) { return p[0].toFixed(1) + "," + p[1].toFixed(1); }).join(" L");
    var area = "M" + pts[0][0].toFixed(1) + "," + (h - 26) + " L" + line +
      " L" + pts[pts.length - 1][0].toFixed(1) + "," + (h - 26) + " Z";
    return '<svg class="v-chart-svg" viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="none">' +
      "<defs><linearGradient id=\"vlineG\" x1=\"0\" y1=\"0\" x2=\"0\" y2=\"1\">" +
      "<stop offset=\"0%\" stop-color=\"#ff4b5f\" stop-opacity=\".5\"/>" +
      "<stop offset=\"100%\" stop-color=\"#ff4b5f\" stop-opacity=\"0\"/></linearGradient></defs>" +
      '<line x1="' + pad + '" y1="' + (h - 26) + '" x2="' + (w - pad) + '" y2="' + (h - 26) + '" stroke="#1e364c"/>' +
      '<path d="' + area + '" fill="url(#vlineG)"/>' +
      '<path d="M' + line + '" fill="none" stroke="#ff4b5f" stroke-width="2"/>' +
      '<text x="' + pad + '" y="12" font-size="9" fill="#96a6b6">' + esc(rows[0].label) + " → " +
      esc(rows[rows.length - 1].label) + "</text></svg>";
  }

  function donutSVG(series) {
    var colors = ["#3a72a8", "#00d68f", "#ffab3d", "#ff5b6b", "#b06bff", "#3fa0c0"];
    var rows = series.slice(0, 6);
    var total = rows.reduce(function (s, x) { return s + Math.abs(Number(x.value) || 0); }, 0) || 1;
    var r = 42, cx = 70, cy = 66, circ = 2 * Math.PI * r, acc = 0;
    var arcs = rows.map(function (x, i) {
      var frac = Math.abs(Number(x.value) || 0) / total;
      var el = '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="none" stroke="' +
        colors[i] + '" stroke-width="16" stroke-dasharray="' + (frac * circ).toFixed(2) + " " +
        circ.toFixed(2) + '" stroke-dashoffset="' + (-acc * circ).toFixed(2) +
        '" transform="rotate(-90 ' + cx + " " + cy + ')"/>';
      acc += frac;
      return el;
    }).join("");
    var legend = rows.map(function (x, i) {
      return '<div class="v-legend-row"><i style="background:' + colors[i] + '"></i><span>' +
        esc(x.label) + "</span><b>" + num(x.value, 2) + "</b></div>";
    }).join("");
    return '<div class="v-donut"><svg viewBox="0 0 140 132">' +
      '<circle cx="70" cy="66" r="42" fill="none" stroke="#1a2836" stroke-width="16"/>' + arcs +
      '<text x="70" y="71" text-anchor="middle" font-size="13" fill="#edf1f5" font-family="monospace">' +
      rows.length + " 类</text></svg><div class=\"v-legend\">" + legend + "</div></div>";
  }

  function heatHTML(series) {
    return '<div class="v-heat">' + series.slice(0, 48).map(function (x) {
      var v = Number(x.value) || 0;
      var alpha = Math.min(0.85, 0.18 + Math.abs(v) / 12);
      var color = v >= 0 ? "rgba(255,75,95," + alpha.toFixed(2) + ")" : "rgba(0,214,143," + alpha.toFixed(2) + ")";
      return '<div class="v-heat-cell" style="background:' + color + '" title="' +
        esc(x.label) + " " + num(v, 2) + '%"><span>' + esc(String(x.label).slice(0, 4)) +
        "</span><b>" + num(v, 1) + "</b></div>";
    }).join("") + "</div>";
  }

  /** 真实空数据的图表骨架：保留图形位，但明确标注不编造数据。 */
  function emptyChartSVG() {
    return '<svg class="v-chart-svg" viewBox="0 0 420 132" preserveAspectRatio="none">' +
      '<line x1="22" y1="106" x2="398" y2="106" stroke="#1e364c"/>' +
      '<line x1="22" y1="53" x2="398" y2="53" stroke="#1c3247" stroke-dasharray="3 3" stroke-width=".6"/>' +
      '<polyline points="22,90 90,84 158,88 226,70 294,76 362,58 398,64" fill="none" ' +
      'stroke="#1e364c" stroke-width="1.5" opacity=".45"/></svg>' +
      '<div class="v-empty">该接口当前无可用走势数据（真实空数据）</div>';
  }

  window.V6_HELPERS = { esc: esc, num: num, cls: cls, pct: pct, d: d, card: card, table: table, kpis: kpis, feed: feed, chartCard: chartCard, pctCell: pctCell, plainNum: plainNum, levelCell: levelCell };
})();
