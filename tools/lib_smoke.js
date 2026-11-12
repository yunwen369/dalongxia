/* lib 四件套冒烟测试（node 环境，模拟浏览器全局）——RF-20261112-01 阶段1b
 * 运行：node tools/lib_smoke.js（无外部依赖，node >= 18）
 * 用途：lib 基础层（config/store/fmt/dom）行为门禁 —— 任何对 lib/* 的修改
 *       必须先过本脚本（17 断言），再跑 UI 探针。
 */
"use strict";
var assert = require("assert");
var path = require("path");

/* ---- 模拟浏览器全局 ---- */
var mem = {};
global.localStorage = {
  getItem: function (k) { return Object.prototype.hasOwnProperty.call(mem, k) ? mem[k] : null; },
  setItem: function (k, v) { mem[k] = String(v); },
  removeItem: function (k) { delete mem[k]; }
};
global.window = global;
global.document = { createElement: function () { throw new Error("node 无 DOM，h() 不在 node 冒烟范围"); } };

var LIB = path.join(__dirname, "..", "frontend", "js", "lib") + path.sep;
require(LIB + "config.js");
require(LIB + "store.js");
require(LIB + "fmt.js");
/* dom.js 顶层不碰 document，可加载（只测 esc） */
require(LIB + "dom.js");

var passed = 0, failed = 0;
function t(name, fn) {
  try { fn(); passed++; console.log("  PASS " + name); }
  catch (e) { failed++; console.log("  FAIL " + name + " -> " + e.message); }
}

console.log("[config]");
t("OB 命名空间存在", function () { assert.ok(window.OB); });
t("KEYS 7 键齐全", function () {
  var k = OB.config.KEYS;
  assert.equal(k.SYMBOL, "openbb-symbol");
  assert.equal(k.THEME, "openbb-theme");
  assert.equal(k.LAYOUT, "openbb-layout");
  assert.equal(k.LAYOUT_CUSTOM, "openbb-layout-custom");
  assert.equal(k.SB_GROUPS, "openbb-sb-groups-v1");
  assert.equal(k.GRID, "openbb-grid-v1");
  assert.equal(k.GRID_MODE, "openbb-grid-mode");
});

console.log("[store] 原始字符串口径");
t("set/get 原始字符串", function () {
  OB.store.set(OB.config.KEYS.SYMBOL, "300750");
  assert.strictEqual(OB.store.get(OB.config.KEYS.SYMBOL), "300750");
});
t("getJSON 不误伤裸数字字符串（openbb-symbol 兼容关键）", function () {
  // 关键回归：裸字符串 "300750" 若被当 JSON 读，会变数字 300750 —— 本层设计已隔离
  OB.store.set("openbb-symbol", "300750");
  assert.strictEqual(OB.store.get("openbb-symbol"), "300750");
  assert.strictEqual(OB.store.getJSON("不存在键x", "fb"), "fb");
});
t("get 不存在键返回 fallback", function () {
  assert.strictEqual(OB.store.get("openbb-none", "dft"), "dft");
  assert.strictEqual(OB.store.get("openbb-none"), null);
});
t("JSON 往返", function () {
  OB.store.setJSON("openbb-test-obj", { a: 1, b: [2, 3] });
  assert.deepEqual(OB.store.getJSON("openbb-test-obj", null), { a: 1, b: [2, 3] });
});
t("JSON 损坏返回 fallback", function () {
  OB.store.set("openbb-test-bad", "{oops");
  assert.strictEqual(OB.store.getJSON("openbb-test-bad", "fb"), "fb");
});
t("remove", function () {
  OB.store.set("openbb-test-del", "1");
  OB.store.remove("openbb-test-del");
  assert.strictEqual(OB.store.get("openbb-test-del", "gone"), "gone");
});
t("has", function () {
  OB.store.set("openbb-test-has", "");
  assert.strictEqual(OB.store.has("openbb-test-has"), true);
  assert.strictEqual(OB.store.has("openbb-test-nope"), false);
});

console.log("[fmt]");
t("num 基础/无效值", function () {
  assert.strictEqual(OB.fmt.num(3.14159), "3.14");
  assert.strictEqual(OB.fmt.num(3.14159, 4), "3.1416");
  assert.strictEqual(OB.fmt.num(null), "—");
  assert.strictEqual(OB.fmt.num("abc"), "—");
  assert.strictEqual(OB.fmt.num(""), "—");
  assert.strictEqual(OB.fmt.num(NaN), "—");
});
t("num 千分位", function () {
  assert.strictEqual(OB.fmt.num(1234567.89, 2, { thousand: true }), "1,234,567.89");
});
t("sign 正负号", function () {
  assert.strictEqual(OB.fmt.sign(3.25), "+3.25");
  assert.strictEqual(OB.fmt.sign(-1.1), "-1.10");
  assert.strictEqual(OB.fmt.sign(0), "0.00");
  assert.strictEqual(OB.fmt.sign(null), "—");
});
t("pct 含 % 口径", function () {
  assert.strictEqual(OB.fmt.pct(3.25), "3.25%");
  assert.strictEqual(OB.fmt.pct(3.25, 2, { sign: true }), "+3.25%");
  assert.strictEqual(OB.fmt.pct(-1.1, 2, { sign: true }), "-1.10%");
  assert.strictEqual(OB.fmt.pct(undefined, 2, { na: "N/A" }), "N/A");
});
t("money 千分位/auto 亿", function () {
  assert.strictEqual(OB.fmt.money(1234.5), "¥1,234.50");
  assert.strictEqual(OB.fmt.money(123456789, 2, { unit: "auto" }), "¥1.23亿");
  assert.strictEqual(OB.fmt.money(52300, 2, { unit: "auto" }), "¥5.23万");
  assert.strictEqual(OB.fmt.money(-999), "¥-999.00");
});
t("scaled 成交额", function () {
  assert.strictEqual(OB.fmt.scaled(2345678901), "23.46亿");
  assert.strictEqual(OB.fmt.scaled(52300, 1), "5.2万");
  assert.strictEqual(OB.fmt.scaled(123.456), "123.46");
});
t("date/dt", function () {
  assert.strictEqual(OB.fmt.date("2026-11-12T08:30:00"), "2026-11-12");
  assert.strictEqual(OB.fmt.date(1765507200000).length, 10);
  assert.strictEqual(OB.fmt.dt("2026-11-12T08:30:00"), "2026-11-12 08:30");
  assert.strictEqual(OB.fmt.date("garbage"), "—");
});

console.log("[dom]");
t("esc 与 v7_app.js esc 逐字符等价", function () {
  var cases = [
    "plain", "", null, undefined, 123,
    "<b class=\"x\">&'</b>",
    "300750", "a<b&&c>d\"e'f",
    "中文<script>alert(1)</script>"
  ];
  var v7esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };
  cases.forEach(function (c) { assert.strictEqual(OB.dom.esc(c), v7esc(c), "case: " + String(c)); });
});

console.log("\n结果: " + passed + " passed, " + failed + " failed");
process.exit(failed ? 1 : 0);
