/* OpenBB-CN 前端公共基础层 · lib/http.js
 * ------------------------------------------------------------------
 * RF-20261112-01 阶段 2：HTTP 唯一入口（L1 lib 层）。
 * 背景：统一信封契约 {data, error, error_code} 的前端消费原本散布——
 *   apiJson 仅存于 v7_app.js（巨石内部私有），app.js/core 各自裸 fetch，
 *   401 拦截（D-19）只有 v7_app 一处生效。
 *
 * 两个口径（语义不同，禁止互相替代）：
 *   - OB.http.apiJson(path, opts)：表单/操作类请求。统一 Content-Type + 401 跳登录
 *     （D-19）+ JSON 解析失败兜底 → {ok, body, status}。行为基线 = v7_app.js:383 原实现。
 *   - OB.http.getJSON(path, opts)：GET 读取类。no-store 默认 + !ok 抛错 + json()
 *     → 返回解析后 payload。调用方自行 try/catch（demo 降级 / 静默忽略）。
 *
 * 迁移状态（阶段 2）：v7_app.apiJson 已委托本层；app.js 3 处裸 fetch 已收编；
 * core/ctxmenu.js 3 处（401 语义差异）与 v7_app 内部旁路 fetch 在阶段 4 收编。
 */
(function () {
  "use strict";

  window.OB = window.OB || {};

  OB.http = {
    /** 操作类统一请求（401 跳登录 D-19；返回 {ok, body, status}，不抛错） */
    apiJson: function (path, opts) {
      var conf = Object.assign({ headers: { "Content-Type": "application/json" } }, opts || {});
      return fetch(path, conf).then(function (resp) {
        if (resp.status === 401) { location.href = "/auth"; return { ok: false, body: null, status: 401 }; }
        return resp.json().then(function (body) { return { ok: resp.ok, body: body, status: resp.status }; })
          .catch(function () { return { ok: resp.ok, body: null, status: resp.status }; });
      });
    },

    /** 读取类 GET：默认 no-store；HTTP 非 2xx 或网络错抛 Error；返回解析后 payload */
    getJSON: function (path, opts) {
      var conf = Object.assign({ cache: "no-store" }, opts || {});
      return fetch(path, conf).then(function (response) {
        if (!response.ok) { throw new Error("HTTP " + response.status); }
        return response.json();
      });
    }
  };
})();
