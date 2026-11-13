/* W7.fund——切割自 v7_app.js（RF-20261112-01 阶段4）。代码体原样保留。 */
(function () {
  "use strict";
  var W7 = window.W7 = window.W7 || {};
  var C = W7.core;
  var $ = C.$, toast = C.toast, apiJson = C.apiJson, queryParam = C.queryParam;
  var currentId = function () { return W7.detail.currentId.apply(null, arguments); };
  var esc = function () { return W7.forms.esc.apply(null, arguments); };
  var objTable = function () { return W7.data.objTable.apply(null, arguments); };
  var preview = function () { return W7.shell.preview.apply(null, arguments); };
  var statKpis = function () { return W7.portfolio.statKpis.apply(null, arguments); };

  /* ---------- 23. 养基宝连接面板（v3.4.0：恢复 8 个未接端点） ---------- */
  function injectYjbPanel() {
    if (currentId() !== "yjb") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-yjb")) { return; }
    var card = document.createElement("div");
    card.className = "card v7-yjb";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">⛨</span>养基宝连接</span>' +
        '<span class="card-note">扫码授权 · 采集 · 解绑 · 增强通道</span></div>' +
        '<div class="v-body">' +
          '<div class="v-kpi-grid" id="yjb-kpis"><div class="loading">读取状态…</div></div>' +
          '<div class="v7-form" style="margin-top:10px">' +
            '<div><label>增强通道</label><select id="yjb-enabled">' +
              '<option value="1">启用</option><option value="0">停用</option></select></div>' +
            '<div><label>授权告知</label><select id="yjb-consent">' +
              '<option value="1">已阅读并同意</option><option value="0">不同意</option></select></div>' +
          '</div>' +
          '<div class="v7-form-foot">' +
            '<button class="v7-btn" data-act="yjb-toggle" type="button">保存开关</button>' +
            '<button class="v7-btn" data-act="yjb-consent" type="button">提交授权</button>' +
            '<button class="v7-btn primary" data-act="yjb-qr" type="button">生成扫码链接</button>' +
            '<button class="v7-btn" data-act="yjb-collect" type="button">立即采集</button>' +
            '<button class="v7-btn danger" data-act="yjb-unbind" type="button">解除绑定</button>' +
          '</div>' +
          '<div id="yjb-qr-box" class="v7-preview-table" style="margin-top:8px"></div>' +
          '<div class="v7-hint" id="yjb-hint"></div>' +
        '</div>';
    body.appendChild(card);
    refreshYjb();
  }

  function refreshYjb() {
    var box = $("yjb-kpis");
    if (!box) { return; }
    apiJson("/api/fund/yjb/status").then(function (r) {
      var d = (r.body && r.body.data) || {};
      var rows = [
        ["绑定状态", d.bound ? "已绑定" : "未绑定", d.bound ? "up" : "down"],
        ["增强通道", d.enabled ? "启用" : "停用"],
        ["授权", d.consent ? "已同意" : "未同意"],
        ["账户数", d.accounts != null ? String(d.accounts) : "—"]
      ];
      box.innerHTML = rows.map(function (kv) {
        return '<div class="v-kpi"><span class="k-label">' + esc(kv[0]) + '</span>' +
          '<span class="k-value ' + (kv[2] || "") + '">' + esc(kv[1]) + '</span></div>';
      }).join("");
    }).catch(function () { box.innerHTML = '<div class="v-empty">状态读取失败</div>'; });
  }

  function yjbQr() {
    var box0 = $("yjb-qr-box");
    if (box0) { box0.innerHTML = '<div class="loading">生成扫码会话…</div>'; }
    apiJson("/api/fund/yjb/qrcode").then(function (r) {
      var box = $("yjb-qr-box");            // 回调内重取：面板可能已被重渲染替换
      var hint = $("yjb-hint");
      if (!box) { return; }
      var d = (r.body && r.body.data) || {};
      if (r.body && r.body.error) { box.innerHTML = '<div class="v-empty">' + esc(r.body.error) + "</div>"; return; }
      if (d.available === false) {
        box.innerHTML = '<div class="v-empty">当前不可扫码：' + esc(d.reason || "环境不支持") + "</div>";
        return;
      }
      var sid = d.session_id || d.id || "";
      var url = d.url || d.qr_url || "";
      var img = d.image_base64 ? '<img alt="养基宝登录二维码" style="width:160px;height:160px;background:#fff;padding:6px;border-radius:6px" src="data:image/png;base64,' + d.image_base64 + '">' : "";
      box.innerHTML = '<div style="display:flex;gap:14px;align-items:flex-start;flex-wrap:wrap">' + img +
        '<div style="min-width:220px">' +
          '<div class="v7-hint">会话：' + esc(sid) + "</div>" +
          (url ? '<div class="v7-hint" style="word-break:break-all">链接：' + esc(url) + "</div>" : "") +
          '<div class="v7-form-foot" style="justify-content:flex-start;margin-top:6px">' +
            '<button class="v7-btn" data-act="yjb-poll" data-sid="' + esc(sid) + '" type="button">轮询状态</button>' +
            '<button class="v7-btn primary" data-act="yjb-bind" data-sid="' + esc(sid) + '" type="button">确认已扫码</button>' +
          "</div></div></div>";
      if (hint) { hint.textContent = "用养基宝 App 扫码后点「确认已扫码」；会话仅存内存，重启需重扫"; }
    }).catch(function () {
      var box = $("yjb-qr-box");
      if (box) { box.innerHTML = '<div class="v-empty">生成失败</div>'; }
    });
  }

  /* ---------- 26. 基金：经理画像 + 盘中估值（v3.4.0：fund/manager、fund/estimation） ---------- */
  function injectFundPanel() {
    if (currentId() !== "fund") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-fund")) { return; }
    var view = (window.V6_VIEWS || {}).fund || {};
    var arr = (view.api && view.api.f) || [];
    var m = String(arr[0] || "").match(/code=([^&]+)/);
    var code = m ? decodeURIComponent(m[1]) : "017811";
    var card = document.createElement("div");
    card.className = "card v7-fund";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">F</span>基金经理与估值开关 · ' + esc(code) + "</span>" +
        '<span class="card-note">/api/fund/manager · /api/fund/estimation</span></div>' +
        '<div class="v-body"><div class="v-kpi-grid" id="v7-mgr-kpis"><div class="loading">加载中…</div></div>' +
        '<div class="v7-form-foot" style="margin-top:8px">' +
          '<span id="v7-est-state" class="v7-hint">盘中估值：—</span>' +
          '<button class="v7-btn" data-act="est-toggle" type="button">切换盘中估值</button>' +
        "</div>" +
        '<div class="v-hint" id="v7-est-note"></div></div>';
    body.insertBefore(card, body.firstChild);
    loadManager(code);
    loadEstimation();
  }

  function loadManager(code) {
    apiJson("/api/fund/manager?code=" + encodeURIComponent(code)).then(function (r) {
      var box = $("v7-mgr-kpis");
      if (!box) { return; }
      var d = (r.body && r.body.data) || {};
      if (r.body && r.body.error) { box.innerHTML = '<div class="v-empty">' + esc(r.body.error) + "</div>"; return; }
      if (!d.found) { box.innerHTML = '<div class="v-empty">未找到该基金经理</div>'; return; }
      var g = d.manager || {};
      box.innerHTML = statKpis([
        ["经理", g.name || "—"],
        ["公司", g.company || "—"],
        ["任职", g.tenure_years != null ? g.tenure_years + " 年" : (g.tenure_days != null ? g.tenure_days + " 天" : "—")],
        ["在管规模", g.scale_aum != null ? g.scale_aum + " 亿" : "—"],
        ["最佳回报", g.best_return != null ? g.best_return + "%" : "—"]
      ]);
    }).catch(function () {   // F14：失败态，避免停留「加载中…」
      var box2 = $("v7-mgr-kpis");
      if (box2) { box2.innerHTML = '<div class="v-empty">经理画像加载失败（/api/fund/manager）</div>'; }
    });
  }

  function loadEstimation() {
    apiJson("/api/fund/estimation").then(function (r) {
      var st = $("v7-est-state"), note = $("v7-est-note");
      var d = (r.body && r.body.data) || {};
      if (st) { st.textContent = "盘中估值：" + (d.enabled ? "已启用" : "已关闭") + " · " + (d.source || ""); }
      if (note) { note.textContent = d.caliber || ""; }
    }).catch(function () {   // F14
      var st = $("v7-est-state");
      if (st) { st.textContent = "盘中估值：读取失败"; }
    });
  }

  /* ---------- 27. 基金组合：诊断 / 权重 / 评估 / 候选池（v3.4.0） ---------- */
  W7.st.comboRisk = "balanced";
  W7.st.comboState = "组合诊断";

  function injectComboPanel() {
    if (currentId() !== "fund-combo") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-combo")) { return; }
    var card = document.createElement("div");
    card.className = "card v7-combo";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">◫</span>组合工具</span>' +
        '<span class="card-note">诊断 / 权重建议 / 组合评估 / 候选池</span></div>' +
        '<div class="v-body">' +
          '<div class="v7-kline-tools" style="margin:0 0 8px">' +
            ["组合诊断", "权重建议", "组合评估", "候选池"].map(function (t) {
              return '<button class="v7-tab' + (t === W7.st.comboState ? " on" : "") + '" data-combo-tab="' + t + '" type="button">' + t + "</button>";
            }).join("") +
            ["conservative", "balanced", "aggressive"].map(function (x) {
              return '<button class="v7-tab' + (x === W7.st.comboRisk ? " on" : "") + '" data-combo-risk="' + x + '" type="button">' +
                ({ conservative: "保守", balanced: "均衡", aggressive: "进取" }[x]) + "</button>";
            }).join("") +
          "</div>" +
          '<div class="v7-form" style="margin-bottom:8px">' +
            '<div class="full"><label>基金代码（逗号分隔，用于权重/评估）</label>' +
              '<input id="v7-combo-codes" value="017811" placeholder="如 017811,025500"></div>' +
          "</div>" +
          '<div id="v7-combo-body"><div class="loading">加载中…</div></div>' +
        "</div>";
    body.insertBefore(card, body.firstChild);
    loadCombo(W7.st.comboState);
  }

  function loadCombo(tab) {
    W7.st.comboState = tab;
    var box = $("v7-combo-body");
    if (!box) { return; }
    var put = function (h) { var b = $("v7-combo-body"); if (b) { b.innerHTML = h; } };
    box.innerHTML = '<div class="loading">加载中…</div>';
    var codes = (($("v7-combo-codes") || {}).value || "").trim();

    if (tab === "组合诊断") {
      apiJson("/api/fund/combo/diagnose?risk=" + W7.st.comboRisk).then(function (r) {
        var d = (r.body && r.body.data) || {};
        if (r.body && r.body.error) { put('<div class="v-empty">' + esc(r.body.error) + "</div>"); return; }
        if (d.missing && d.missing.length && !(d.holdings || []).length) {
          put('<div class="v-empty">' + esc(d.missing.join("；")) + "</div>");
          return;
        }
        put(objTable((d.holdings || []).map(function (h, i) {
          return { name: h.name || h.code || ("#" + (i + 1)), weight: h.weight, target: (d.target || [])[i],
            deviation: (d.deviation || [])[i] };
        }), [["name", "持仓"], ["weight", "当前权重"], ["target", "目标权重"], ["deviation", "偏离"]],
          "组合诊断无持仓数据") + '<div class="v-hint" style="margin-top:6px">' + esc(d.caliber || "") + "</div>");
      }).catch(function () { put('<div class="v-empty">组合诊断请求失败</div>'); });   // F14
      return;
    }
    if (tab === "权重建议") {
      if (!codes) { put('<div class="v-empty">请填写基金代码</div>'); return; }
      box.innerHTML = '<div class="loading">计算中…（首次约 20–30 秒，含拥挤度降权）</div>';
      apiJson("/api/fund/combo/weight?codes=" + encodeURIComponent(codes) + "&risk=" + W7.st.comboRisk + "&crowd=true").then(function (r) {
        var d = (r.body && r.body.data) || {};
        if (r.body && r.body.error) { put('<div class="v-empty">' + esc(r.body.error) + "</div>"); return; }
        var items = d.items || [];
        var tot = d.totals || {};
        put(statKpis([
          ["风险档", W7.st.comboRisk === "conservative" ? "保守" : W7.st.comboRisk === "aggressive" ? "进取" : "均衡"],
          ["基础权重合计", tot.base != null ? tot.base + "%" : "—"],
          ["最终权重合计", tot.final != null ? tot.final + "%" : "—", "up"],
          ["现金保留", tot.cash != null ? tot.cash + "%" : "—"]
        ]) + objTable(items, [["code", "代码"], ["name", "名称"],
          ["base_weight", "基础%"], ["crowding", "拥挤度"],
          ["discount", "折扣", function (v) { return v == null ? "—" : String(v); }],
          ["final_weight", "最终%"], ["discount_label", "说明"]], "无权重建议"));
      }).catch(function () { put('<div class="v-empty">权重建议请求失败</div>'); });   // F14
      return;
    }
    if (tab === "组合评估") {
      if (!codes) { put('<div class="v-empty">请填写基金代码</div>'); return; }
      apiJson("/api/fund/combo/evaluate?codes=" + encodeURIComponent(codes) + "&risk=" + W7.st.comboRisk).then(function (r) {
        var d = (r.body && r.body.data) || {};
        if (r.body && r.body.error) { put('<div class="v-empty">' + esc(r.body.error) + "</div>"); return; }
        var items = d.items || [];
        if (!items.length) {
          put('<div class="v-empty">' + esc((d.missing || []).join("；") || "无评估结果") + "</div>");
          return;
        }
        var rows = items.map(function (it) {
          var st = it.stats || {};
          return {
            code: it.code, name: it.name || it.code,
            annual: st.annual_return != null ? st.annual_return + "%" : "—",
            vol: st.volatility != null ? st.volatility + "%" : "—",
            mdd: st.max_drawdown != null ? st.max_drawdown + "%" : "—",
            sharpe: st.sharpe != null ? st.sharpe : "—",
            days: st.sample_days != null ? st.sample_days : "—",
            weight: (function () {
              var w = (d.weights || []).filter(function (x) { return x.code === it.code; })[0];
              return w && w.weight != null ? w.weight + "%" : "—";
            })()
          };
        });
        put(objTable(rows, [["code", "代码"], ["name", "名称"], ["weight", "建议权重"],
          ["annual", "年化"], ["vol", "波动率"], ["mdd", "最大回撤"], ["sharpe", "夏普"], ["days", "样本天数"]],
          "无评估结果") + '<div class="v-hint" style="margin-top:6px">' + esc(d.caliber || "") + "</div>");
      }).catch(function () { put('<div class="v-empty">组合评估请求失败</div>'); });   // F14
      return;
    }
    if (tab === "候选池") {
      apiJson("/api/fund/candidates?limit=60&template=fund_friendly").then(function (r) {
        var d = (r.body && r.body.data) || {};
        if (r.body && r.body.error) { put('<div class="v-empty">' + esc(r.body.error) + "</div>"); return; }
        var items = d.items || [];
        var tpl = d.template || {};
        put('<div class="v-hint">模板：' + esc(tpl.label || "基金友好") + " · 命中 " + esc(String(d.total_matched || items.length)) +
          (d.truncated ? "（已截断显示）" : "") + "</div>" +
          objTable(items, [["code", "代码"], ["name", "名称"], ["board", "行业"], ["close", "收盘"],
            ["chg", "涨跌%", function (v) { return v == null ? "—" : Number(v).toFixed(2); }],
            ["pe", "PE", function (v) { return v == null ? "—" : Number(v).toFixed(1); }],
            ["pb", "PB", function (v) { return v == null ? "—" : Number(v).toFixed(2); }]], "候选池为空"));
      }).catch(function () { put('<div class="v-empty">候选池请求失败</div>'); });   // F14
      return;
    }
  }


  /* ---------- 28. 基金净值三档走势（v3.5.0 · F6）：近1月 / 近1年 / 成立以来（本地裁剪） ---------- */
  var NAV_RANGES = {
    "1m": { label: "近1月", days: 30 },
    "1y": { label: "近1年", days: 365 },
    "all": { label: "成立以来", days: 0 }
  };
  W7.st.navRange = W7.st.navRange || "1m";

  /** 基金页注入净值走势卡（三档切换，数据复用组合回测分页通道）。 */
  function injectNavTrendPanel() {
    if (currentId() !== "fund") { return; }
    var body = $("view-body");
    if (!body || body.querySelector(".v7-navtrend")) { return; }
    var code = fundViewCode();
    var card = document.createElement("div");
    card.className = "card v7-navtrend";
    card.innerHTML =
      '<div class="card-head"><span class="card-title"><span class="badge-ico">∿</span>净值走势 · ' + esc(code) + "</span>" +
        '<span class="v7-kline-tools">' + Object.keys(NAV_RANGES).map(function (k) {
          return '<button class="v7-tab' + (k === W7.st.navRange ? " on" : "") +
            '" data-nav-range="' + k + '" type="button">' + NAV_RANGES[k].label + "</button>";
        }).join("") + "</span></div>" +
        '<div class="v-body" id="v7-navtrend-body"><div class="loading">加载中…</div></div>';
    body.insertBefore(card, body.firstChild);
    loadNavRange(W7.st.navRange, code);
  }

  /** 从 V6_VIEWS.fund 的 api 配置解析当前基金代码（支持 #/fund?code= 覆盖后的实际值）。 */
  function fundViewCode() {
    var view = (window.V6_VIEWS || {}).fund || {};
    var arr = (view.api && view.api.f) || [];
    var m = String(arr[0] || "").match(/code=([^&]+)/);
    return m ? decodeURIComponent(m[1]) : "017811";
  }

  /** 按当前档位渲染净值走势（缓存命中不重拉；code 变化重拉）。 */
  function loadNavRange(range, codeForce) {
    W7.st.navRange = range || W7.st.navRange || "1m";
    var box = $("v7-navtrend-body");
    if (!box) { return; }
    var code = codeForce || fundViewCode();
    var cached = W7.st.fundNavData;
    if (cached && cached.code === code && cached.rows && cached.rows.length) {
      drawNavTrend(cached.rows);
      return;
    }
    if (cached && cached.code === code && cached.rows && !cached.rows.length) {
      box.innerHTML = '<div class="v-empty">该基金无可用净值序列（/api/fund/nav 返回空）</div>';
      return;
    }
    box.innerHTML = '<div class="loading">净值加载中…（全量分页，单基金首次约 2–5 秒）</div>';
    W7.portfolio.fetchNav(code).then(function (rows) {
      if (!rows || !rows.length) {
        box.innerHTML = '<div class="v-empty">该基金无可用净值序列（/api/fund/nav 返回空）</div>';
        return;
      }
      W7.st.fundNavData = { code: code, rows: rows };
      drawNavTrend(rows);
    }).catch(function (e) {
      var b2 = $("v7-navtrend-body");
      if (b2) { b2.innerHTML = '<div class="v-empty">' + esc(e.message || "净值加载失败") + "</div>"; }
    });
  }

  /** 按档位本地裁剪并画图（1m=30 自然日 / 1y=365 / all=全序列）。 */
  function drawNavTrend(rows) {
    var box = $("v7-navtrend-body");
    if (!box) { return; }
    var conf = NAV_RANGES[W7.st.navRange] || NAV_RANGES["1m"];
    var cut = rows;
    if (conf.days > 0) {
      var lastMs = new Date((rows[rows.length - 1].date || "").replace(/-/g, "/")).getTime();
      var from = lastMs - conf.days * 86400000;
      cut = rows.filter(function (x) {
        return new Date((x.date || "").replace(/-/g, "/")).getTime() >= from;
      });
    }
    if (cut.length < 2) {
      box.innerHTML = '<div class="v-empty">该区间净值样本不足（' + cut.length + " 点），请切换更长区间</div>";
      return;
    }
    var first = cut[0].nav;
    var last = cut[cut.length - 1].nav;
    var ret = (last / first - 1) * 100;
    var hi = -Infinity, lo = Infinity;
    cut.forEach(function (x) { hi = Math.max(hi, x.nav); lo = Math.min(lo, x.nav); });
    var xs = cut.map(function (x) { return (x.date || "").slice(conf.days === 0 ? 0 : 5); });
    box.innerHTML = statKpis([
      ["区间涨幅", (ret >= 0 ? "+" : "") + ret.toFixed(2) + "%", ret >= 0 ? "up" : "down"],
      ["最新净值", String(last)],
      ["区间高点", String(hi)],
      ["区间低点", String(lo)],
      ["样本", cut.length + " 个净值日"]
    ]) + '<div id="v7-navtrend-chart" style="height:230px;margin-top:8px"></div>' +
      '<div class="v7-sec-note">口径：/api/fund/nav 全量分页拉取后按档位本地裁剪；成立以来为成立日至今全序列，不补值。</div>';
    var el = $("v7-navtrend-chart");
    if (el && window.echarts) {
      var chart = OB.chart.init(el);
      chart.setOption({
        animation: false, grid: { left: 8, right: 12, top: 12, bottom: 8, containLabel: true },
        tooltip: { trigger: "axis" },
        xAxis: { type: "category", data: xs, axisLabel: { fontSize: 9 } },
        yAxis: { type: "value", scale: true, axisLabel: { fontSize: 9 } },
        series: [{ name: "单位净值", type: "line", showSymbol: false,
          data: cut.map(function (x) { return x.nav; }),
          lineStyle: { width: 1.5, color: "#4a9bff" },
          areaStyle: { color: "rgba(74,155,255,.14)" } }]
      });
    }
  }


  /* 跨域导出（W7.fund） */
  W7.fund = W7.fund || {};
  W7.fund.injectComboPanel = injectComboPanel;
  W7.fund.injectFundPanel = injectFundPanel;
  W7.fund.injectNavTrendPanel = injectNavTrendPanel;
  W7.fund.injectYjbPanel = injectYjbPanel;
  W7.fund.loadCombo = loadCombo;
  W7.fund.loadEstimation = loadEstimation;
  W7.fund.loadNavRange = loadNavRange;
  W7.fund.refreshYjb = refreshYjb;
  W7.fund.yjbQr = yjbQr;
})();
