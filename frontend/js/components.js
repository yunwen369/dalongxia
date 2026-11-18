/* dalongxia 业务组件层（RF-20261112-01 阶段4 抽取自 views.js）——
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
      // v3.7.0（③）：有 url 时给「原文 ↗」入口（新窗口），无 url 显示「—」不补默认链接
      var url = String(item.url || "").trim();
      var src = url
        ? '<a class="v-feed-src" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' +
          esc(item.source || "原文") + " ↗</a>"
        : '<span class="f-src">' + esc(item.source || "—") + "</span>";
      return '<div class="v-feed-row"><span class="f-time mono">' + esc(item.time || "—") +
        '</span><span class="f-title">' + esc(item.title || item.text || "—") +
        "</span>" + src + "</div>";
    }).join("") + "</div>";
  }

  var rankCell = function (v, row, i) {
    return '<span class="rank-badge r' + (i + 1) + '">' + (i + 1) + "</span>";
  };
  var pctCell = function (v) {
    return '<span class="' + cls(v) + ' mono">' + pct(v) + "</span>";
  };
  var plainNum = function (v) { return '<span class="mono">' + num(v, 2) + "</span>"; };
  /** 带正负号的数值单元格（涨红跌绿；0 与缺失值不着色）。
   *  2026-11-17 新增：净流入/净买入等「正负语义但非百分比」列统一用它，
   *  与 pctCell（百分比）配套，避免这类列一直是中性色、与其它涨跌列不一致。
   *  @param {*} v 原始数值
   *  @param {number} [digits=2] 小数位
   */
  var signedNum = function (v, digits) {
    var n = parseFloat(v);
    if (!isFinite(n)) { return '<span class="mono">—</span>'; }
    var c = n > 0 ? "up" : (n < 0 ? "down" : "");
    return '<span class="' + (c ? c + " " : "") + 'mono">' +
      (n > 0 ? "+" : "") + num(n, digits == null ? 2 : digits) + "</span>";
  };
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

  /* ------------------------------------------------------------------
   * v3.7.0（REQ-20261115-01）新增公共件：走势 / 指数卡 / 云图 / 三级板块选择器 / 中文表头
   * 单一实现：全站唯一来源（规范 §1.8-2 复用优先）；调用方不得各自再写一套。
   * ---------------------------------------------------------------- */

  /** 字段 → 中文列名映射（唯一来源；表格表头禁用英文标识符）。 */
  var COL = {
    name: "板块", code: "代码", symbol: "代码", pct: "涨跌幅", change_pct: "涨跌幅",
    price: "最新价", close: "收盘", open: "开盘", high: "最高", low: "最低",
    amount: "成交额(亿)", net_inflow: "净流入(亿)", main_net: "主力净额(亿)",
    lead_stock: "领涨股", lead_pct: "领涨股涨幅", up: "上涨家数", down: "下跌家数",
    turnover: "换手率(%)", volume: "成交量", score: "评分", level: "档位", advice: "建议",
    board: "板块", kind: "类型", date: "日期", time: "时间", title: "标题",
    source: "来源", rule: "规则", target: "标的", detail: "说明", id: "编号",
    group: "分组", widgets: "组件数", count: "数量", rank: "排名", label: "项目",
    value: "数值", temp: "温度", signal: "信号", days: "天数", value_text: "数值说明",
    definition: "口径", nav: "净值", shares: "份额", market_value: "市值", cost: "成本",
    pnl: "盈亏", pnl_pct: "收益率", total_assets: "总资产", today_income: "当日收益",
    pe: "市盈率", pb: "市净率", roe: "净资产收益率(%)", revenue: "营收", profit: "净利",
    margin: "毛利率(%)", yield: "股息率(%)", beta: "贝塔系数", vol: "波动率(%)",
    drawdown: "最大回撤(%)", sharpe: "夏普", net: "净额(亿)", inflow: "流入(亿)",
    outflow: "流出(亿)", zd: "涨跌家数", is_realtime: "实时", market: "市场",
    // 以下为接口实际返回但早期未登记的字段（v3.7.0 ⑧ 由探针实测暴露后补全）
    flow_source: "资金流来源", source_detail: "来源说明", updated_at: "更新时间",
    created_at: "创建时间", at: "时点", ts: "时间戳", weight: "权重", unit: "单位",
    sample_days: "样本天数", missing: "缺失项", extra: "附加信息", constituent_count: "成分股数",
    correlation: "相关性", index_value: "指数点位", change: "涨跌额", prev_close: "昨收",
    open_interest: "持仓量", settlement: "结算价", basis: "基差", premium: "溢价",
    discount: "折价", float_market_cap: "流通市值", total_market_cap: "总市值",
    pe_ttm: "市盈率(近12月)", ps_ttm: "市销率(近12月)", pcf: "市现率", dividend_yield: "股息率(%)",
    gross_margin: "毛利率(%)", debt_ratio: "资产负债率(%)", rev_yoy: "营收同比(%)",
    np_yoy: "净利同比(%)", ocf: "经营现金流", mktcap: "总市值(亿)", factor: "因子",
    threshold: "阈值", hit: "命中", size: "规模", span: "宽度", h: "高度",
    params: "参数", market_status: "市场状态", caliber: "口径", as_of: "数据时间",
    is_demo: "演示数据", demo: "演示数据", status: "状态", note: "备注", advice_text: "建议",
    board_kind: "板块类型", signal_text: "信号说明", pct_text: "涨跌幅文案", nav_date: "净值日期",
    estimate_nav: "估算净值", estimate_pct: "估算涨幅", day_gain: "当日收益", hold_pct: "占比(%)",
    account_id: "账户", market_value_pct: "市值占比(%)", fee: "费率(%)", manager: "基金经理",
    tenure_days: "任职天数", tenure_years: "任职年限", scale_aum: "管理规模(亿)", cat: "分类",
    kind_text: "类型说明", ts_text: "时间", abs_pct: "涨跌幅绝对值", v: "数值", n: "名称"
  };
  /** 取中文列名（未登记键回退原键，便于发现遗漏）。 */
  function colName(key) { return COL[key] || key; }

  /** 迷你走势 SVG（30 点，红涨绿跌，与首页同口径）。 */
  function sparkSVG(values, positive) {
    var vs = (values || []).slice(-30);
    if (!vs.length) { return '<span class="err">走势无数据</span>'; }
    var w = 200, h = 26;
    var min = Math.min.apply(null, vs), max = Math.max.apply(null, vs), span = (max - min) || 1;
    var pts = vs.map(function (v, i) { return [(i / Math.max(1, vs.length - 1)) * w, h - ((v - min) / span) * (h - 4) - 2]; });
    var line = pts.map(function (q) { return q[0].toFixed(1) + "," + q[1].toFixed(1); }).join(" L");
    var area = "M" + pts[0][0].toFixed(1) + "," + h + " L" + line + " L" + pts[pts.length - 1][0].toFixed(1) + "," + h + " Z";
    var c = positive ? "#ff3b4e" : "#00c087";
    var gid = "spk" + (sparkSVG._n = (sparkSVG._n || 0) + 1);
    return '<svg viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="none">' +
      '<defs><linearGradient id="' + gid + '" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0%" stop-color="' + c + '" stop-opacity="0.55"/>' +
      '<stop offset="100%" stop-color="' + c + '" stop-opacity="0.02"/></linearGradient></defs>' +
      '<path d="' + area + '" fill="url(#' + gid + ')"/>' +
      '<path d="M' + line + '" fill="none" stroke="' + c + '" stroke-width="1.6"/></svg>';
  }

  /** 指数卡网格（带走势占位；走势由 injectIndexSparks 异步填充 → 不阻塞主渲染）。 */
  function idxCards(items) {
    return '<div class="v-idx-grid">' + (items || []).map(function (x) {
      return '<div class="card v-idx-card"><div class="card-head"><span class="card-title">' + esc(x.name || x.code) +
        '</span><span class="card-note">' + esc(x.code || "") + "</span></div>" +
        '<div class="v-idx-row"><span class="index-value ' + cls(x.pct) + '">' + num(x.price, 2) + "</span>" +
        '<span class="' + cls(x.pct) + '">' + pct(x.pct) + "</span></div>" +
        '<div class="spark" data-symbol="' + esc(x.code || "") + '"><span class="loading">走势加载中…</span></div>' +
        "</div>";
    }).join("") + "</div>";
  }

  /** 标准 squarify（Bruls 等）：面积∝权重，长宽比最优。 */
  function squarify(items, x, y, w, h) {
    var out = [];
    var total = items.reduce(function (s2, d2) { return s2 + d2.v; }, 0);
    if (!total || w <= 0 || h <= 0) { return out; }
    var scale = (w * h) / total;
    var rest = items.slice().sort(function (a, b) { return b.v - a.v; })
      .map(function (d2) { return { d: d2, a: Math.max(d2.v * scale, 1) }; });
    var cx = x, cy = y, cw = w, ch = h;
    function worst(row, side, sum) {
      var mx = -Infinity, mn = Infinity;
      row.forEach(function (r) { mx = Math.max(mx, r.a); mn = Math.min(mn, r.a); });
      var s2 = sum * sum, side2 = side * side;
      return Math.max(side2 * mx / s2, s2 / (side2 * mn));
    }
    function layoutRow(row, side, horiz, sum) {
      var thick = sum / side, off = 0;
      row.forEach(function (r, i) {
        var len = (i === row.length - 1) ? (side - off) : (r.a / thick);
        if (horiz) { out.push({ d: r.d, x: cx, y: cy + off, w: thick, h: len }); }
        else { out.push({ d: r.d, x: cx + off, y: cy, w: len, h: thick }); }
        off += len;
      });
      if (horiz) { cx += thick; cw -= thick; } else { cy += thick; ch -= thick; }
    }
    while (rest.length) {
      var horiz = cw >= ch;
      var side = horiz ? ch : cw;
      var row = [], sum = 0, best = Infinity;
      while (rest.length) {
        var s2 = sum + rest[0].a;
        var sc = worst(row.concat([rest[0]]), side, s2);
        if (row.length && sc > best) { break; }
        row.push(rest.shift()); sum = s2; best = sc;
      }
      layoutRow(row, side, horiz, sum);
    }
    return out;
  }

  /** 云图（大盘云图 / 板块云图）：面积∝成交额、颜色∝涨跌幅（红涨绿跌）。 */
  function treemapHtml(nodes, opts) {
    opts = opts || {};
    var useAmount = opts.areaBy !== "pct";
    var items = (nodes || []).map(function (n) {
      var w0 = useAmount ? Math.abs(Number(n.amount) || 0) : Math.abs(Number(n.pct) || 0);
      return { v: Math.max(w0 || (Math.abs(Number(n.pct) || 0) + 0.01), 0.01), n: n };
    });
    return '<div class="v-tm" data-area="' + (useAmount ? "amount" : "pct") + '"></div>';
  }

  /** 在已插入的 .v-tm 容器内绘制云图（需容器已有尺寸）。 */
  function drawTreemap(host, nodes, opts) {
    opts = opts || {};
    if (!host) { return; }
    var W = host.clientWidth || 900, H = host.clientHeight || 360;
    var useAmount = opts.areaBy !== "pct";
    var items = (nodes || []).map(function (n) {
      var w0 = useAmount ? Math.abs(Number(n.amount) || 0) : Math.abs(Number(n.pct) || 0);
      return { v: Math.max(w0 || (Math.abs(Number(n.pct) || 0) + 0.01), 0.01), n: n };
    });
    var rects = squarify(items, 0, 0, W, H);
    host.innerHTML = rects.map(function (r) {
      // squarify 返回 {d: item, x, y, w, h}；item 形如 {v, n}
      var n = (r.d && r.d.n) || {}, p = Number(n.pct) || 0;
      var area = r.w * r.h;
      var small = area < 900, tiny = area < 420;
      var color = p > 0 ? "#ff3b4e" : (p < 0 ? "#00c087" : "#8899aa");
      var op = (0.55 + 0.45 * Math.min(1, Math.abs(p) / 4)).toFixed(2);
      return '<div class="v-tm-cell' + (tiny ? " tiny" : (small ? " small" : "")) + '" data-name="' + esc(n.name) +
        '" style="left:' + (r.x + 1).toFixed(1) + "px;top:" + (r.y + 1).toFixed(1) + "px;width:" + Math.max(4, r.w - 2).toFixed(1) +
        "px;height:" + Math.max(14, r.h - 2).toFixed(1) + "px;background:" + color + ";opacity:" + op + '">' +
        '<span class="v-tm-n">' + esc(n.name) + "</span>" +
        '<span class="v-tm-p">' + pct(p) + "</span>" +
        (area >= 9000 ? '<span class="v-tm-a">成交 ' + num(n.amount, 2) + " 亿</span>" : "") +
        (area >= 14000 && n.lead_stock ? '<span class="v-tm-a">领涨 ' + esc(n.lead_stock) + "</span>" : "") +
        "</div>";
    }).join("");
  }

  /** 三级板块选择器 HTML（类型 → 一级 → 板块，可搜索）。 */
  function boardPickerHtml(id, opts) {
    opts = opts || {};
    var kind = opts.kind || "industry";
    return '<span class="v-bp" id="' + esc(id) + '">' +
      '<select data-r="kind"><option value="industry"' + (kind === "industry" ? " selected" : "") + '>行业</option>' +
      '<option value="concept"' + (kind === "concept" ? " selected" : "") + '>概念</option></select>' +
      '<select data-r="group"></select>' +
      '<input data-r="search" placeholder="搜索板块" value="' + esc(opts.value || "") + '">' +
      '<div class="v-bp-list" data-r="list" hidden></div></span>';
  }

  /** 待绘 K 线/云图统一绘制（DOM 出现后调用；幂等，已绘制跳过）。
   *
   * 说明：注册表页/云图卡以字符串渲染 → 先出占位 div，再由本函数用 echarts 绘制，
   * 避免在渲染期同步建图导致的卡顿与实例泄漏（实例经 OB.chart.init 单例复用）。
   */
  function drawPendingKlines(scope) {
    var root = scope || document;
    Array.prototype.forEach.call(root.querySelectorAll(".v-kline[data-key]:not([data-drawn])"), function (host) {
      var key = host.getAttribute("data-key");
      var bundle = (window.__V6_KLINE || {})[key];
      if (!bundle || !bundle.rows || !bundle.rows.length) { return; }
      host.setAttribute("data-drawn", "1");
      drawCandles(host, bundle.rows, bundle.title);
    });
    Array.prototype.forEach.call(root.querySelectorAll(".v-tm[data-key]:not([data-drawn])"), function (host) {
      var key = host.getAttribute("data-key");
      var bundle = (window.__V6_TM || {})[key];
      if (!bundle || !bundle.nodes || !bundle.nodes.length) { return; }
      host.setAttribute("data-drawn", "1");
      drawTreemap(host, bundle.nodes, {});
    });
    // 指数卡迷你走势（今日/市场/首页共用）
    Array.prototype.forEach.call(root.querySelectorAll(".spark[data-symbol]:not([data-drawn])"), function (host) {
      var sym = host.getAttribute("data-symbol");
      if (!sym) { return; }
      host.setAttribute("data-drawn", "1");
      OB.http.getJSON("/api/market/index-kline?symbol=" + encodeURIComponent(sym) + "&period=daily&limit=60")
        .then(function (r) {
          var rows = ((r && r.data && r.data.items) || []).slice(-30);
          var vals = rows.map(function (x) { return x.close; });
          var first = vals.length ? vals[0] : 0, last = vals.length ? vals[vals.length - 1] : 0;
          host.innerHTML = sparkSVG(vals, last >= first);
        })
        .catch(function () { host.innerHTML = '<span class="err">走势无数据</span>'; });
    });
  }

  /** 蜡烛 + 成交量 + MA5/10/20 完整 K 线（源：任意 OHLC 行集）。 */
  function drawCandles(host, rows, title) {
    var inst = OB.chart.init(host, { renderer: "canvas" });
    if (!inst) { host.innerHTML = '<div class="v-empty">图表库未加载</div>'; return; }
    var dates = rows.map(function (r) { return String(r.date || "").slice(5); });
    var candles = rows.map(function (r) { return [r.open, r.close, r.low, r.high]; });
    var vols = rows.map(function (r) {
      return { value: r.volume, itemStyle: { color: r.close >= r.open ? "#ff3b4e" : "#00c087" } };
    });
    function ma(n) {
      return rows.map(function (_, i) {
        if (i < n - 1) { return "-"; }
        var t = 0;
        for (var j = i - n + 1; j <= i; j += 1) { t += rows[j].close; }
        return +(t / n).toFixed(2);
      });
    }
    var hasVol = rows.some(function (r) { return typeof r.volume === "number"; });
    inst.setOption({
      animation: false,
      grid: hasVol ? [{ left: 52, right: 14, top: 26, height: 176 }, { left: 52, right: 14, top: 214, height: 62 }]
                   : [{ left: 52, right: 14, top: 26, bottom: 30 }],
      tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
      legend: { right: 10, top: 0, data: ["5日均线", "10日均线", "20日均线"], textStyle: { fontSize: 10 } },
      xAxis: hasVol ? [
        { type: "category", data: dates, axisLabel: { fontSize: 10 } },
        { type: "category", gridIndex: 1, data: dates, axisLabel: { show: false } }
      ] : [{ type: "category", data: dates, axisLabel: { fontSize: 10 } }],
      yAxis: hasVol ? [{ scale: true, splitLine: { show: false } }, { gridIndex: 1, splitLine: { show: false }, axisLabel: { show: false } }]
                    : [{ scale: true, splitLine: { show: false } }],
      dataZoom: [{ type: "inside", start: 50, end: 100 }],
      series: [
        { name: "K线", type: "candlestick", data: candles,
          itemStyle: { color: "#ff3b4e", color0: "#00c087", borderColor: "#ff3b4e", borderColor0: "#00c087" } },
        { name: "5日均线", type: "line", data: ma(5), smooth: true, showSymbol: false, lineStyle: { width: 1, color: "#f0b429" } },
        { name: "10日均线", type: "line", data: ma(10), smooth: true, showSymbol: false, lineStyle: { width: 1, color: "#4dabf7" } },
        { name: "20日均线", type: "line", data: ma(20), smooth: true, showSymbol: false, lineStyle: { width: 1, color: "#c084fc" } }
      ].concat(hasVol ? [{ name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: vols }] : [])
    });
  }

  /** 三级板块选择器绑定：需要传入 {industry:[...], concept:[...], tree:{...}}（缺省从 V6 板块数据取）。 */
  function bindBoardPicker(el, data, onChange) {
    if (!el) { return; }
    var kindSel = el.querySelector('[data-r="kind"]');
    var grpSel = el.querySelector('[data-r="group"]');
    var input = el.querySelector('[data-r="search"]');
    var list = el.querySelector('[data-r="list"]');
    if (!kindSel) { return; }
    function pool() { return (data && data[kindSel.value]) || []; }
    function fillGroups() {
      var groups = (data && data.tree && data.tree[kindSel.value]) || [];
      grpSel.innerHTML = '<option value="">全部</option>' + groups.map(function (g) {
        return '<option value="' + esc(g.label) + '">' + esc(g.label) + "</option>";
      }).join("");
    }
    function render(kw) {
      var hits = pool().filter(function (b) { return !kw || String(b.name).indexOf(kw) >= 0; });
      list.innerHTML = '<div class="v-bp-g">共 ' + hits.length + " 项</div>" + hits.slice(0, 60).map(function (b) {
        return '<div class="v-bp-i" data-name="' + esc(b.name) + '"><span>' + esc(b.name) + '</span><span class="' +
          cls(b.pct) + '">' + pct(b.pct) + "</span></div>";
      }).join("");
      list.hidden = false;
    }
    fillGroups();
    kindSel.onchange = function () { fillGroups(); if (onChange) { onChange(kindSel.value, input.value); } };
    grpSel.onchange = function () { if (grpSel.value) { input.value = grpSel.value; render(grpSel.value); } };
    input.onfocus = function () { render(input.value); };
    input.oninput = function () { render(input.value); };
    list.onclick = function (e) {
      var it = e.target.closest(".v-bp-i");
      if (!it) { return; }
      input.value = it.getAttribute("data-name");
      list.hidden = true;
      if (onChange) { onChange(kindSel.value, input.value); }
    };
    document.addEventListener("click", function (e) { if (!el.contains(e.target)) { list.hidden = true; } });
  }

  window.V6_HELPERS = {
    esc: esc, num: num, cls: cls, pct: pct, d: d, card: card, table: table, kpis: kpis,
    feed: feed, chartCard: chartCard, pctCell: pctCell, plainNum: plainNum, signedNum: signedNum,
    levelCell: levelCell,
    COL: COL, colName: colName, sparkSVG: sparkSVG, idxCards: idxCards,
    treemapHtml: treemapHtml, drawTreemap: drawTreemap, squarify: squarify, boardPickerHtml: boardPickerHtml,
    drawPendingKlines: drawPendingKlines, drawCandles: drawCandles, bindBoardPicker: bindBoardPicker
  };
})();
