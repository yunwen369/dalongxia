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
    }
  };
})();
