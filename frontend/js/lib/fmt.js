/* OpenBB-CN 前端公共基础层 · lib/fmt.js
 * ------------------------------------------------------------------
 * RF-20261112-01 阶段 1b：数值/日期格式化唯一入口（L1 lib 层）。
 * 背景：全站 toFixed/toLocaleString 散布 49 处（v7_app 19 / views 12 / app 15 /
 * registry 3），口径不一（占位符、正负号、单位换算各自为政）。
 * 阶段 1b 只建层；阶段 4 拆分视图时逐点切换到本层。
 *
 * 设计约定（全站统一口径，取代各处散布写法）：
 *   - 无效值（null/undefined/NaN/空串）统一返回占位符，默认 "—"；
 *   - 百分比：pct() 接收【已乘 100 的数值】（与后端字段口径一致，如 3.25 表示 3.25%）；
 *   - 正负号：涨跌类字段用 sign() / pct(v,{sign:true})，涨红跌绿由 CSS class 决定，此处不染；
 *   - 大数：money/num 支持 {unit:"auto"} 自动换手 万/亿（中文习惯）。
 */
(function () {
  "use strict";

  window.OB = window.OB || {};

  var NA = "—"; // 统一占位符

  function toNum(v) {
    if (v === null || v === undefined || v === "") { return NaN; }
    var n = Number(v);
    return isFinite(n) ? n : NaN;
  }

  OB.fmt = {
    NA: NA,

    /** 数值 → 定长小数字符串；无效返回占位符 */
    num: function (v, digits, opts) {
      var n = toNum(v);
      if (isNaN(n)) { return (opts && opts.na) || NA; }
      var s = n.toFixed(digits === undefined ? 2 : digits);
      if (opts && opts.thousand) {
        var parts = s.split(".");
        parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",");
        s = parts.join(".");
      }
      return s;
    },

    /** 显式正负号（+3.25 / -1.10 / +0.00）；无效返回占位符 */
    sign: function (v, digits, opts) {
      var n = toNum(v);
      if (isNaN(n)) { return (opts && opts.na) || NA; }
      var s = n.toFixed(digits === undefined ? 2 : digits);
      return (n > 0 ? "+" : "") + s;
    },

    /** 百分比：数值已含 % 口径（3.25 → "3.25%"）；sign 选项加 + 号 */
    pct: function (v, digits, opts) {
      var n = toNum(v);
      if (isNaN(n)) { return (opts && opts.na) || NA; }
      var body = (opts && opts.sign && n > 0 ? "+" : "") + n.toFixed(digits === undefined ? 2 : digits);
      return body + "%";
    },

    /** 金额：¥ 前缀 + 千分位；unit:"auto" 自动 万/亿（1.2亿 → "¥1.20亿"） */
    money: function (v, digits, opts) {
      var n = toNum(v);
      if (isNaN(n)) { return (opts && opts.na) || NA; }
      var d = digits === undefined ? 2 : digits;
      if (opts && opts.unit === "auto") {
        var abs = Math.abs(n);
        if (abs >= 1e8) { return "¥" + (n / 1e8).toFixed(d) + "亿"; }
        if (abs >= 1e4) { return "¥" + (n / 1e4).toFixed(d) + "万"; }
      }
      return "¥" + n.toFixed(d).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    },

    /** 大数（成交量/成交额等）：auto 万/亿，无 ¥ 前缀 */
    scaled: function (v, digits) {
      var n = toNum(v);
      if (isNaN(n)) { return NA; }
      var d = digits === undefined ? 2 : digits;
      var abs = Math.abs(n);
      if (abs >= 1e8) { return (n / 1e8).toFixed(d) + "亿"; }
      if (abs >= 1e4) { return (n / 1e4).toFixed(d) + "万"; }
      return n.toFixed(d);
    },

    /** 日期：'YYYY-MM-DD'（接受 Date/时间戳/ISO 串）；无效返回占位符 */
    date: function (v) {
      var d = v instanceof Date ? v : new Date(v);
      if (isNaN(d.getTime())) { return NA; }
      var m = d.getMonth() + 1, day = d.getDate();
      return d.getFullYear() + "-" + (m < 10 ? "0" + m : m) + "-" + (day < 10 ? "0" + day : day);
    },

    /** 日期时间：'YYYY-MM-DD HH:mm' */
    dt: function (v) {
      var d = v instanceof Date ? v : new Date(v);
      if (isNaN(d.getTime())) { return NA; }
      var h = d.getHours(), mi = d.getMinutes();
      return OB.fmt.date(d) + " " + (h < 10 ? "0" + h : h) + ":" + (mi < 10 ? "0" + mi : mi);
    },

    /**
     * 美股时段四态（设计基准 R12：美股分时空态）。
     * 以美东时间（America/New_York，自动处理夏令时）判定：
     * 盘前 04:00–09:30 / 盘中 09:30–16:00 / 盘后 16:00–20:00 / 休市（含周末全天）。
     * 法定节假日表未内置（节假日按交易日时段显示，口径在徽章 title 注明）。
     * Returns: {phase: '盘前'|'盘中'|'盘后'|'休市', text: '美股盘中 · 10:32(美东)', open: bool}
     */
    usSession: function (now) {
      var d = now instanceof Date ? now : new Date();
      var parts = {};
      try {
        // 用 en-US 区域格式化提取美东时区时分（浏览器原生 API，无外部依赖）
        var s = d.toLocaleString("en-US", {
          timeZone: "America/New_York", hour12: false,
          weekday: "short", hour: "2-digit", minute: "2-digit"
        });
        // 形如 "Fri, 10:32" 或 "Fri 10:32:00 AM"（hour12:false 下通常 24 小时制）
        var wm = s.match(/^[A-Za-z]{3}/);
        var hm = s.match(/(\d{1,2}):(\d{2})/);
        parts.weekday = wm ? wm[0] : "";
        parts.minutes = hm ? (+hm[1]) * 60 + (+hm[2]) : 0;
      } catch (e) {
        return { phase: "休市", text: "美股时段未知", open: false };
      }
      var wd = parts.weekday;
      var minutes = parts.minutes;
      var isWeekend = wd === "Sat" || wd === "Sun";
      var phase = "休市";
      var open = false;
      if (!isWeekend) {
        if (minutes >= 240 && minutes < 570) { phase = "盘前"; open = true; }
        else if (minutes >= 570 && minutes < 960) { phase = "盘中"; open = true; }
        else if (minutes >= 960 && minutes < 1200) { phase = "盘后"; open = true; }
      }
      var hh = Math.floor(minutes / 60), mm = minutes % 60;
      var clock = (hh < 10 ? "0" + hh : hh) + ":" + (mm < 10 ? "0" + mm : mm);
      return {
        phase: isWeekend ? "休市" : phase,
        text: "美股" + (isWeekend ? "休市（周末）" : phase) + " · " + clock + "（美东）",
        open: open
      };
    }
  };
})();
