#!/usr/bin/env python3
"""人工模拟测试（Human-like UI walkthrough）· v2.0（2026-11-14 判据层重构）

定位
----
与 `ui_clickthrough.py`（逐视图遍历 + 错误收集）互补：本脚本**模拟一个真实用户
连续操作**，按真实使用顺序走完主流程，每一步都做「操作 → 观察 → 断言」，
并把不符合预期的现象记为缺陷。

v2.0 修复（v3.6.1 批次 · 对应检测发现 D-3）
------------------------------------------
v1.0 的判据层绑定**已退役的 v6 壳层**（`#page-title` / `#main-nav` / `.sidebar-item`
/ `.page-view` / `.sb-group` / `window.App|Pages|State4`），V6 整站替换（v3.1.0）后
这些元素与全局全部不存在 → 工具在任何实例上都以 `RuntimeError: 页面未就绪` 退出，
**完全不可用**。本次按**现行 V6/W7 契约**重写判据层：

| 语义 | v1.0（已退役） | v2.0（现行） |
|---|---|---|
| 视图标题 | `#page-title` | `#view-title` |
| 面包屑 | `#crumb` | `#view-crumb` |
| 顶栏分组 | `#main-nav .nav-item` | `.topnav button[data-group]` |
| 侧栏项 / 组 | `.sidebar-item` / `.sb-group` | `.side-item` / `.side-group[data-group]` |
| 视图容器 | `.page-view:not([hidden])` / `#viewport` | `#home-main`（首页）/ `#view-generic`（通用） |
| 皮肤按钮 | `#skin-switch button` | `.skin-switch button[data-skin]` |
| 主题属性 | `documentElement` | `body[data-theme]`（dark=无属性） |
| 就绪闸门 | `window.App && window.Pages && window.State4` | `#view-title` + `#side-nav .side-item` |
| 页签 | `.pg-tab` | `.v7-tab[data-*]` |
| 空态 | `window.State4.apply('error')` | `.v-empty`（真实空态，如空台账） |

**两处按现行架构重定义**（不改产品，只改判据口径，均在此登记）：
1. **B3 面包屑**：现行 `renderHead()` 只渲染「非末层=纯文本、末层=`span.cur`」，
   面包屑**不是导航控件**（设计态）。故改为断言「面包屑存在且末项=视图标题」；
   「面包屑可点回上级」属**功能新增**，未获原型确认前不做（需另行立项）。
2. **E2 表格行点击跳转**：现行预览表**行不可跳转**（跳转由右键菜单/详情按钮承担），
   故改为断言「表行右键菜单可打开且含 6 项动作」（该能力为 v3.3.0 回收项，真实可用）。

覆盖的人工动作（24 个场景）
--------------------------
A 启动与首屏：A0 登录 / A1 首屏渲染 / A2 四处一致 / A3 首页真实数据
B 导航：B1 侧栏 28 项逐项点击 / B1b 内容出现 / B2 顶栏 8 分组 / B3 面包屑一致 / B4 后退 / B5 URL 直达
C 侧栏折叠：C1 默认只展开当前组 / C2 展开增加 / C3 收起回落 / C4 手动展开保留
D 皮肤：D1 现行布局 236/52 / D2 砚浅色 / D3 V6 布局 130/40 / D4 Ctrl+M / D4b 切回 / D5 刷新记忆
E 视图内交互：E1 个股页页签 / E2 表行右键菜单 / E3 表单聚焦 / E4 主区滚动 / E5 canvas 无溢出
F 断点：F1 1024 无横向溢出 / F2 1280 顶栏右侧不裁
G 异常与恢复：G1 空态→默认态可恢复 / G2 快速切换无错误
Z 全程无未捕获 JS 错误

用法
----
  python tools/human_walkthrough.py --base-url http://127.0.0.1:6903 --out <json> [--shots <dir>]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v6_skin_probe import CDP, launch_edge, open_target, free_port  # noqa: E402

# 侧栏可见视图（28 项，与 views.js 的 V6_VIEWS.sidebar 归属一致）
SIDEBAR_VIEWS = [
    "home", "today", "market", "dashboard", "watchlist", "news", "sectors",
    "stock", "fin", "ann", "tools", "cand", "charting", "macro", "valuation",
    "crowding", "screener", "lhb", "portfolio", "fund", "yjb", "mgr",
    "alerts", "connectors", "settings", "ai", "backtest", "dca",
]

# 顶栏 8 分组（与 index.html 的 .topnav button[data-group] 一致）
TOP_GROUPS = [
    ("dashboard", "总览"), ("market", "市场"), ("news", "资讯"), ("portfolio", "组合"),
    ("money", "资金"), ("watchlist", "自选"), ("yjb", "养基宝"), ("system", "系统"),
]

SB_KEY = "openbb-sb-groups-v1"

JS_STATE = r"""
(function () {
  var body = document.body || {};
  var t = document.getElementById('view-title');
  var c = document.getElementById('view-crumb');
  var nav = document.querySelector('.topnav button.active');
  var side = document.querySelector('.side-item.active');
  var generic = document.getElementById('view-generic');
  var home = document.getElementById('home-main');
  // v2.0 修正：主题属性与三皮肤布局变量都挂在 <body>（app.css 用 [data-theme=…] 元素选择器），
  // 必须读 body 的计算样式；读 documentElement 会拿到 :root 默认值（D2/D3 假阴根因）。
  var bodyStyle = getComputedStyle(body);
  var rootStyle = getComputedStyle(document.documentElement);
  var cssVar = function (name) {
    return (bodyStyle.getPropertyValue(name) || rootStyle.getPropertyValue(name) || '').trim();
  };
  // 当前可见区：home 用 #home-main，其余用 #view-generic（两者互斥 hidden）
  var visible = (home && !home.hidden) ? home : (generic && !generic.hidden ? generic : null);
  var scrollHost = (home && !home.hidden) ? home : generic;
  // 展开的分组 = 组头 caret 为 ▾（renderSidebar 只渲染已展开组的项）
  var openKeys = [];
  Array.prototype.forEach.call(document.querySelectorAll('.side-group'), function (g) {
    var caret = g.querySelector('.grp-caret');
    if (caret && (caret.textContent || '').indexOf('\u25be') >= 0) {
      openKeys.push(g.getAttribute('data-group'));
    }
  });
  return {
    title: t ? t.textContent.trim() : '',
    crumb: c ? c.innerText.replace(/\s+/g, ' ').trim() : '',
    crumbCur: (function () {var e = c && c.querySelector('.cur');return e ? e.textContent.trim() : ''; })(),
    crumbHidden: !!(c && c.closest && c.closest('.view-head') &&
                    c.closest('.view-head').offsetParent === null),
    navText: nav ? nav.textContent.trim() : '',
    navGroup: nav ? nav.getAttribute('data-group') : '',
    sideText: side ? side.textContent.trim() : '',
    sideView: side ? side.getAttribute('data-view') : '',
    hash: location.hash,
    isHome: !!(home && !home.hidden),
    theme: body.getAttribute('data-theme'),
    menuH: cssVar('--menu-h'),
    sidebarW: cssVar('--sidebar-w'),
    toolbarH: cssVar('--toolbar-h'),
    bgApp: cssVar('--bg-app'),
    visibleText: visible ? (visible.innerText || '') : '',
    visibleChildren: visible ? visible.children.length : 0,
    visibleRows: visible ? visible.querySelectorAll('table tbody tr').length : 0,
    scroll: scrollHost ? {
      cls: scrollHost.id,
      sh: scrollHost.scrollHeight, ch: scrollHost.clientHeight,
      st: scrollHost.scrollTop,
    } : null,
    sideGroups: document.querySelectorAll('.side-group').length,
    sideItems: document.querySelectorAll('.side-item').length,
    sideOpenKeys: openKeys,
    sideOpen: openKeys.length,
    canvasOverflow: (function () {
      var bad = [];
      Array.prototype.forEach.call(document.querySelectorAll('canvas'), function (cv) {
        var p = cv.parentElement;
        if (!p) { return; }
        if (cv.getBoundingClientRect().width > p.getBoundingClientRect().width + 2) {
          bad.push(Math.round(cv.getBoundingClientRect().width) + '>' +
                   Math.round(p.getBoundingClientRect().width));
        }
      });
      return bad;
    })(),
    scrollOverflow: {
      sh: document.documentElement.scrollHeight,
      ih: window.innerHeight,
      sw: document.documentElement.scrollWidth,
      iw: window.innerWidth,
    },
  };
})()
"""

JS_READY = (
    "!!(document.getElementById('view-title') && "
    "document.querySelector('#side-nav .side-item'))"
)

JS_ERRORS = r"""
(function () {
  if (!window.__hw) { window.__hw = { errs: [] }; }
  return window.__hw.errs.slice(0, 20);
})()
"""

INSTALL_ERR_HOOK = r"""
(function () {
  window.__hw = { errs: [] };
  window.addEventListener('error', function (e) {
    window.__hw.errs.push('ERR:' + (e.message || ''));
  });
  window.addEventListener('unhandledrejection', function (e) {
    var r = e.reason;
    window.__hw.errs.push('REJ:' + ((r && r.message) || String(r)));
  });
  var ce = console.error;
  console.error = function () {
    window.__hw.errs.push('CONSOLE:' + Array.prototype.join.call(arguments, ' ').slice(0, 140));
    ce.apply(console, arguments);
  };
})();
"""


class Walker:
    """人工模拟测试执行器。"""

    def __init__(self, cdp: CDP, shots_dir: str) -> None:
        self.cdp = cdp
        self.shots = shots_dir
        self.records: list = []
        self.defects: list = []

    def shot(self, name: str) -> str:
        """截图（可选目录）。"""
        if not self.shots:
            return ""
        os.makedirs(self.shots, exist_ok=True)
        path = os.path.join(self.shots, name + ".png")
        try:
            res = self.cdp.call("Page.captureScreenshot",
                                {"format": "png", "captureBeyondViewport": False})
            data = res.get("result", {}).get("data")
            if data:
                with open(path, "wb") as fh:
                    fh.write(base64.b64decode(data))
                return path
        except Exception:
            pass
        return ""

    def step(self, code: str, name: str, ok: bool, detail: str, defect: str = "") -> None:
        """记录一条断言语义。"""
        self.records.append({"code": code, "name": name, "ok": bool(ok), "detail": detail})
        if not ok and defect:
            self.defects.append({"code": code, "name": name, "defect": defect, "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {code} {name} :: {detail}")

    def st(self) -> dict:
        """读取当前页面状态。"""
        return self.cdp.eval_js(JS_STATE) or {}

    def goto(self, view: str, wait: float = 2.0) -> dict:
        """按 hash 跳视图并等待。"""
        self.cdp.eval_js(f"location.hash = '#/{view}'")
        time.sleep(wait)
        return self.st()

    def click_selector(self, selector: str, wait: float = 1.2) -> bool:
        """真实鼠标点击（按元素中心坐标）。"""
        loc = self.cdp.eval_js(
            "(function(){var e=document.querySelector(%s);if(!e)return null;"
            "var r=e.getBoundingClientRect();if(r.width<=0||r.height<=0)return null;"
            "return {x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)};})()"
            % json.dumps(selector)
        )
        if not loc:
            return False
        for ev in ("mousePressed", "mouseReleased"):
            self.cdp.call("Input.dispatchMouseEvent", {
                "type": ev, "x": loc["x"], "y": loc["y"],
                "button": "left", "clickCount": 1,
            })
        time.sleep(wait)
        return True

    def right_click_selector(self, selector: str, wait: float = 0.8) -> bool:
        """在元素中心发起真实右键（触发 contextmenu）。"""
        loc = self.cdp.eval_js(
            "(function(){var e=document.querySelector(%s);if(!e)return null;"
            "var r=e.getBoundingClientRect();if(r.width<=0||r.height<=0)return null;"
            "return {x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)};})()"
            % json.dumps(selector)
        )
        if not loc:
            return False
        for ev in ("mousePressed", "mouseReleased"):
            self.cdp.call("Input.dispatchMouseEvent", {
                "type": ev, "x": loc["x"], "y": loc["y"],
                "button": "right", "clickCount": 1,
            })
        time.sleep(wait)
        return True

    def press_key(self, key: str, code: str = "", modifiers: int = 0) -> None:
        """真实按键。"""
        params = {"type": "keyDown", "key": key, "code": code or key, "modifiers": modifiers}
        self.cdp.call("Input.dispatchKeyEvent", params)
        params["type"] = "keyUp"
        self.cdp.call("Input.dispatchKeyEvent", params)
        time.sleep(0.9)


def wait_ready(cdp: CDP, timeout: float = 25.0) -> bool:
    """就绪闸门：等 `#view-title` 与侧栏项出现（现行 V6/W7 判据）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.5)
        try:
            if cdp.eval_js(JS_READY):
                return True
        except Exception:
            continue
    return False


def main() -> int:
    """命令行入口。

    Returns:
        int: 0 = 全部通过；1 = 有失败；2 = 执行器未初始化。
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:6903")
    ap.add_argument("--out", required=True)
    ap.add_argument("--shots", default="")
    ap.add_argument("--password-env", default="OPENBB_ACCESS_PASSWORD",
                    help="从该环境变量取访问口令；为空则跳过登录（服务未启用口令）")
    args = ap.parse_args()

    password = (os.environ.get(args.password_env) or "").strip()

    port = free_port()
    profile = tempfile.mkdtemp(prefix="hw_")
    proc = None
    cdp = None
    w = None
    try:
        proc = launch_edge(port, profile)
        ws_url = open_target(port, args.base_url + "/")
        cdp = CDP(ws_url)
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": INSTALL_ERR_HOOK})
        cdp.viewport(1366, 768)
        w = Walker(cdp, args.shots)

        # ---------- A0 登录（真实用户第一步） ----------
        cdp.call("Page.navigate", {"url": args.base_url + "/"})
        time.sleep(2.5)
        if password and cdp.eval_js("location.pathname === '/auth'"):
            filled = cdp.eval_js(
                "(function(){var i=document.querySelector('input[name=token]');"
                "if(!i)return 'no-input';i.focus();i.value=%s;return 'ok';})()"
                % json.dumps(password)
            )
            time.sleep(0.3)
            cdp.eval_js(
                "(function(){var f=document.querySelector('form');"
                "if(f){f.submit();return 'submitted';}return 'no-form';})()"
            )
            time.sleep(3.5)
            w.step("A0", "口令登录成功进入工作台",
                   filled == "ok" and cdp.eval_js("location.pathname === '/'"),
                   f"fill={filled} path={cdp.eval_js('location.pathname')}",
                   defect="登录流程失败")
        else:
            w.step("A0", "无需登录（服务未启用口令）", True,
                   f"path={cdp.eval_js('location.pathname')}")

        if not wait_ready(cdp):
            raise RuntimeError("页面未就绪：等不到 #view-title / 侧栏项（现行判据）")
        time.sleep(1.5)

        # ================= A 启动与首屏 =================
        st = w.st()
        w.step("A1", "首屏已渲染（标题非空）", bool(st.get("title")),
               f"title='{st.get('title')}' hash={st.get('hash')}")
        # A2：首页走 #home-main（顶栏/侧栏/标题三处）；面包屑属通用视图外壳，
        # 首页按设计不渲染 → 另取一个通用视图校验「四处一致」。
        st_home = w.st()
        st_gen = w.goto("market", wait=2.0)
        w.step("A2", "顶栏/侧栏/标题/面包屑四处有值（首页三处 + 通用视图四处）",
               bool(st_home.get("title")) and bool(st_home.get("navText"))
               and bool(st_home.get("sideText"))
               and bool(st_gen.get("title")) and bool(st_gen.get("crumb"))
               and bool(st_gen.get("navText")) and bool(st_gen.get("sideText")),
               f"home: nav='{st_home.get('navText')}' side='{st_home.get('sideText')}' "
               f"title='{st_home.get('title')}' | 通用: nav='{st_gen.get('navText')}' "
               f"crumb='{st_gen.get('crumb')}' title='{st_gen.get('title')}'")
        w.goto("home", wait=1.6)
        # A3：只判当前可见区；等数据就绪（首页多卡并行取数）
        ready_data = False
        waited = 0.0
        while waited < 25.0:
            st = w.st()
            txt = st.get("visibleText", "")
            if len(txt) > 80 and "加载中" not in txt:
                ready_data = True
                break
            time.sleep(1.0)
            waited += 1.0
        w.step("A3", "首页真实数据已渲染（当前可见区无「加载中」）", ready_data,
               f"等待 {waited:.0f}s / 可见区 {len(st.get('visibleText', ''))} 字 / "
               f"cards={st.get('visibleChildren')}",
               defect="首屏停在加载态或可见区无内容")
        w.shot("A_home")

        # ================= B 导航 =================
        fail_views = []
        empty_views = []
        for view in SIDEBAR_VIEWS:
            st = w.goto(view, wait=1.4)
            if not st.get("title"):
                fail_views.append(f"{view}(title 空)")
                continue
            waited = 0.0
            while waited < 8.0 and st.get("visibleChildren", 0) == 0:
                time.sleep(1.0)
                waited += 1.0
                st = w.st()
            if st.get("visibleChildren", 0) == 0:
                empty_views.append(view)
        w.step("B1", f"侧栏 {len(SIDEBAR_VIEWS)} 项逐项点击（标题均响应）", not fail_views,
               "全部标题正常" if not fail_views else f"{len(fail_views)} 项异常: {fail_views[:4]}",
               defect="部分侧栏视图点击后标题为空")
        w.step("B1b", f"{len(SIDEBAR_VIEWS)} 项内容均在 8s 内出现", not empty_views,
               "全部有内容" if not empty_views else f"迟迟无内容: {empty_views}",
               defect="部分视图 8s 内无任何内容渲染")

        fail_top = []
        top_detail = []
        for grp, label in TOP_GROUPS:
            sel = f".topnav button[data-group='{grp}']"
            before = cdp.eval_js(
                "(function(){var b=document.querySelector(%s);"
                "if(!b)return JSON.stringify({found:false});"
                "var r=b.getBoundingClientRect();"
                "var x=Math.round(r.left+r.width/2),y=Math.round(r.top+r.height/2);"
                "var top=document.elementFromPoint(x,y);"
                "return JSON.stringify({found:true,x:x,y:y,"
                "topGroup:(top&&top.getAttribute)?top.getAttribute('data-group'):null,"
                "topTag:top?top.tagName:'',hash:location.hash,"
                "active:((document.querySelector('.topnav button.active')||{}).getAttribute"
                "?document.querySelector('.topnav button.active').getAttribute('data-group'):'')});})()"
                % json.dumps(sel)
            )
            before = json.loads(before) if isinstance(before, str) else (before or {})
            clicked = w.click_selector(sel, wait=1.8)
            after = cdp.eval_js(
                "(function(){var a=document.querySelector('.topnav button.active');"
                "return JSON.stringify({hash:location.hash,"
                "active:a?a.getAttribute('data-group'):''});})()")
            after = json.loads(after) if isinstance(after, str) else (after or {})
            top_detail.append({"group": grp, "before": before, "after": after})
            if not clicked:
                fail_top.append(f"{label}(click-miss)")
            elif after.get("active") != grp:
                fail_top.append(
                    f"{label}(active={after.get('active')} hash={after.get('hash')} "
                    f"命中={before.get('topGroup')}/{before.get('topTag')} "
                    f"xy={before.get('x')},{before.get('y')})")
        w.step("B2", "顶栏 8 分组逐项点击", not fail_top,
               "全部跳转并高亮正确" if not fail_top else f"{fail_top[:4]}",
               defect="顶栏分组点击未跳转或高亮不符")

        # B3 面包屑：现行架构为「非末层纯文本 + 末层 .cur」，与标题同源
        st = w.goto("stock", wait=2.2)
        w.step("B3", "面包屑与视图标题一致（末项=标题）",
               bool(st.get("crumb")) and st.get("crumbCur") == st.get("title"),
               f"crumb='{st.get('crumb')}' cur='{st.get('crumbCur')}' title='{st.get('title')}'",
               defect="面包屑缺失或末项与标题不一致")

        # B4 浏览器后退
        cdp.eval_js("history.back()")
        time.sleep(1.6)
        st3 = w.st()
        w.step("B4", "浏览器后退可回上一视图", bool(st3.get("title")),
               f"hash={st3.get('hash')} title='{st3.get('title')}'",
               defect="history.back 后页面异常")

        # B5 URL 直达
        st4 = w.goto("valuation", wait=2.2)
        w.step("B5", "URL hash 直达深层视图", bool(st4.get("title")),
               f"title='{st4.get('title')}'", defect="URL 直达失败")

        # ================= C 侧栏折叠 =================
        # 侧栏 open 状态由 localStorage[openbb-sb-groups-v1] + 当前视图组决定，
        # 改 localStorage 后必须 Page.reload 才会重跑 renderSidebar。
        cdp.eval_js(f"localStorage.removeItem('{SB_KEY}')")
        cdp.eval_js("location.hash = '#/home'")
        time.sleep(1.0)
        cdp.call("Page.reload")
        time.sleep(3.0)
        if not wait_ready(cdp):
            raise RuntimeError("reload 后页面未就绪")
        cdp.eval_js(INSTALL_ERR_HOOK)
        if cdp.eval_js("location.pathname === '/auth'") and password:
            cdp.eval_js(
                "(function(){var i=document.querySelector('input[name=token]');"
                "if(i){i.value=%s;document.querySelector('form').submit();}})()"
                % json.dumps(password)
            )
            time.sleep(4.0)
        st = w.goto("home", wait=2.4)
        w.step("C1", "首次访问只展开当前组（5 组 / 展开≤1）",
               st.get("sideGroups") == 5 and (st.get("sideOpen") or 0) <= 1,
               f"groups={st.get('sideGroups')} open={st.get('sideOpen')} "
               f"items={st.get('sideItems')}",
               defect="侧栏默认全部展开，信息过载")

        # C2/C3：点击第 2 组组头 → 可见项增加 → 再点 → 回落
        cnt_js = ("(function(){return document.querySelectorAll('.side-item').length;})()")
        grp_sel = ".side-group[data-group='market']"
        before = cdp.eval_js(cnt_js) or 0
        opened = w.click_selector(grp_sel, wait=1.2)
        mid = cdp.eval_js(cnt_js) or 0
        w.step("C2", "点开分组后可见项增加", opened and mid > before,
               f"{before} → {mid}", defect="分组展开无效")
        w.click_selector(grp_sel, wait=1.2)
        after = cdp.eval_js(cnt_js) or 0
        w.step("C3", "再收起后可见项回落", after < mid,
               f"{mid} → {after}", defect="分组收起无效")

        # C4：手动展开一个非当前组，切视图后仍展开（渲染态）且落盘（v3.6.1 已接线持久化）
        w.goto("home", wait=1.6)
        w.click_selector(".side-group[data-group='research']", wait=1.0)
        stored = cdp.eval_js(
            "(function(){return JSON.parse(localStorage.getItem('" + SB_KEY + "')||'{}');})()")
        st2 = w.goto("lhb", wait=1.8)
        open_keys = st2.get("sideOpenKeys") or []
        has_news = bool(cdp.eval_js(
            "!!document.querySelector('.side-item[data-view=\"news\"]')"))
        keep = ("research" in open_keys) and has_news and bool(stored and stored.get("research"))
        w.step("C4", "手动展开的组切视图后保留（渲染态 + 落盘）", keep,
               f"open={open_keys} news项可见={has_news} 落盘={stored}",
               defect="手动展开的组被强制收起或未落盘")

        # ================= D 皮肤切换 =================
        st = w.goto("home", wait=2.2)
        d1 = w.click_selector(".skin-switch button[data-skin='dark']", wait=1.2)
        st_dark = w.st()
        w.step("D1", "点「现行」布局变 236/52",
               d1 and st_dark.get("sidebarW") == "236px" and st_dark.get("toolbarH") == "52px",
               f"sidebar={st_dark.get('sidebarW')} toolbar={st_dark.get('toolbarH')} "
               f"menuH={st_dark.get('menuH')}",
               defect="现行深色皮肤布局参数不符")

        d2 = w.click_selector(".skin-switch button[data-skin='light']", wait=1.2)
        st_light = w.st()
        light_bg = (st_light.get("bgApp") or "").lower()
        w.step("D2", "点「砚」浅色皮肤生效（body[data-theme=light] + 浅背景）",
               d2 and st_light.get("theme") == "light" and light_bg in ("#ffffff", "#fff"),
               f"theme={st_light.get('theme')} bg-app={light_bg}",
               defect="砚浅色皮肤未生效")

        d3 = w.click_selector(".skin-switch button[data-skin='v6']", wait=1.2)
        st_v6 = w.st()
        w.step("D3", "点「V6」恢复 130/40",
               d3 and st_v6.get("sidebarW") == "130px" and st_v6.get("toolbarH") == "40px",
               f"sidebar={st_v6.get('sidebarW')} toolbar={st_v6.get('toolbarH')} "
               f"menuH={st_v6.get('menuH')}",
               defect="V6 皮肤恢复失败")

        # D4 Ctrl+M（modifiers=2 为 Ctrl）
        w.press_key("m", "KeyM", modifiers=2)
        st_cm = w.st()
        w.step("D4", "Ctrl+M 切到砚", st_cm.get("theme") == "light",
               f"theme={st_cm.get('theme')}", defect="Ctrl+M 快捷键无效")
        w.press_key("m", "KeyM", modifiers=2)
        st_cm2 = w.st()
        w.step("D4b", "Ctrl+M 再切回 V6", st_cm2.get("theme") == "v6",
               f"theme={st_cm2.get('theme')}", defect="Ctrl+M 未切回 V6")

        # D5 刷新后记忆
        cdp.call("Page.reload")
        time.sleep(4)
        wait_ready(cdp)
        st_reload = w.st()
        w.step("D5", "刷新后皮肤记忆保持 V6", st_reload.get("theme") == "v6",
               f"theme={st_reload.get('theme')}", defect="刷新后皮肤重置")
        cdp.eval_js(INSTALL_ERR_HOOK)
        w.shot("D_v6_home")

        # ================= E 视图内交互 =================
        # E1 页签切换（个股页：v7-tab[data-stock-tab]）
        st = w.goto("stock", wait=2.6)
        tabs = cdp.eval_js("document.querySelectorAll('#view-body .v7-tab[data-stock-tab]').length")
        tab_ok = False
        before_txt = ""
        after_txt = ""
        if tabs > 1:
            before_txt = cdp.eval_js(
                "(function(){var e=document.getElementById('v7-stock-body');"
                "return e?(e.innerText||'').slice(0,80):'';})()") or ""
            tab_ok = w.click_selector("#view-body .v7-tab[data-stock-tab]:nth-of-type(2)", wait=2.5)
            after_txt = cdp.eval_js(
                "(function(){var e=document.getElementById('v7-stock-body');"
                "return e?(e.innerText||'').slice(0,80):'';})()") or ""
        w.step("E1", "个股页页签可切换（内容随之变化）",
               tabs == 0 or (tab_ok and after_txt != before_txt),
               f"tabs={tabs} clicked={tab_ok} changed={after_txt != before_txt}",
               defect="页签点击无效或切换后内容不变")

        # E2 表行右键菜单（v3.3.0 回收能力；只开菜单不发动作，零写入）
        w.goto("sectors", wait=2.0)
        rows = 0
        waited = 0.0
        while waited < 45.0:
            rows = cdp.eval_js("document.querySelectorAll('#view-body table tbody tr').length") or 0
            if rows > 0:
                break
            time.sleep(1.5)
            waited += 1.5
        menu_items = 0
        if rows:
            w.right_click_selector("#view-body table tbody tr", wait=0.9)
            menu_items = cdp.eval_js(
                "document.querySelectorAll('.v7-ctx button[data-i]').length") or 0
            cdp.eval_js(
                "(function(){var m=document.querySelector('.v7-ctx');if(m){m.remove();}})()")
        w.step("E2", "表行右键菜单可打开且含 6 项动作",
               rows > 0 and menu_items >= 6,
               f"rows={rows} 等待{waited:.0f}s menu_items={menu_items}",
               defect="表行右键菜单缺失或菜单项不全")

        # E3 搜索/表单输入可聚焦
        w.goto("screener", wait=2.6)
        typed = cdp.eval_js(
            "(function(){var i=document.querySelector('#view-body input[type=text],"
            "#view-body input[type=number],#view-body input:not([type]),#view-body select');"
            "if(!i)return 'no-input';i.focus();return i.tagName;})()")
        w.step("E3", "筛选器表单可聚焦输入", typed != "no-input",
               f"input={typed}", defect="表单输入框缺失")

        # E4 主区可滚动
        st = w.goto("sectors", wait=2.4)
        sc = st.get("scroll") or {}
        scrolled = cdp.eval_js(
            "(function(){var e=document.getElementById('view-generic');"
            "e.scrollTop = 9999;return e.scrollTop;})()") or 0
        w.step("E4", "主区可滚动", (sc.get("sh", 0) <= sc.get("ch", 0)) or scrolled > 0,
               f"host={sc.get('cls')} sh={sc.get('sh')} ch={sc.get('ch')} scrollTop={scrolled}",
               defect="主区无法滚动")

        # E5 图表 canvas 无溢出
        st = w.goto("home", wait=2.8)
        w.step("E5", "图表 canvas 无横向溢出", not st.get("canvasOverflow"),
               f"overflow={st.get('canvasOverflow') or 'none'}",
               defect="图表 canvas 超出容器")

        # ================= F 断点 =================
        cdp.viewport(1024, 640)
        st = w.goto("stock", wait=2.6)
        so = st.get("scrollOverflow", {})
        w.step("F1", "1024×640 无横向溢出", so.get("sw", 0) <= so.get("iw", 0) + 2,
               f"sw={so.get('sw')} iw={so.get('iw')}",
               defect="1024 断点横向溢出")
        w.shot("F_stock_1024")

        cdp.viewport(1280, 768)
        time.sleep(1.2)
        right_ok = cdp.eval_js(
            "(function(){var a=document.querySelector('.topright');"
            "if(!a)return {ok:false};var r=a.getBoundingClientRect();"
            "return {ok:r.right<=window.innerWidth+1,right:Math.round(r.right),"
            "iw:window.innerWidth};})()")
        w.step("F2", "1280 顶栏右侧不裁", bool(right_ok and right_ok.get("ok")),
               f"right={right_ok.get('right')} iw={right_ok.get('iw')}",
               defect="1280 断点顶栏右侧被裁")

        cdp.viewport(1366, 768)
        time.sleep(0.8)

        # ================= G 异常与恢复 =================
        # 现行架构无全局四态机：以「真实空态页（空台账）→ 回到首页默认态」替代，
        # 判据=空态有可读文案（不是白屏/报错），切回后默认态内容恢复。
        w.goto("portfolio", wait=3.0)
        empty_txt = cdp.eval_js(
            "(function(){var e=document.getElementById('view-body');"
            "return e?(e.innerText||''):'';})()") or ""
        empty_shown = ("v-empty" in (cdp.eval_js(
            "(function(){var e=document.getElementById('view-body');"
            "return e?(e.innerHTML||''):'';})()") or "")) or len(empty_txt.strip()) > 8
        st_back = w.goto("home", wait=3.0)
        recovered = len(st_back.get("visibleText", "")) > 40
        w.step("G1", "空态页有可读文案且可恢复默认态", empty_shown and recovered,
               f"emptyShown={empty_shown} recovered={recovered} "
               f"emptyLen={len(empty_txt.strip())}",
               defect="空态不可读或回默认态失败")

        # G2 快速连续切换
        for v in ("home", "news", "fund", "lhb", "settings", "home"):
            cdp.eval_js(f"location.hash = '#/{v}'")
            time.sleep(0.35)
        time.sleep(2.0)
        errs = cdp.eval_js(JS_ERRORS) or []
        w.step("G2", "连续快速切视图无 JS 错误", len(errs) == 0,
               f"errors={errs[:3]}" if errs else "0 错误",
               defect="快速切换产生 JS 异常")

        # 收尾：全局错误汇总
        all_errs = cdp.eval_js(JS_ERRORS) or []
        w.step("Z1", "全程无未捕获 JS 错误", len(all_errs) == 0,
               f"{len(all_errs)} 条: {all_errs[:3]}" if all_errs else "0 错误",
               defect="存在未捕获 JS 异常")

    finally:
        if cdp:
            cdp.close()
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=8)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    if w is None:
        print("执行器未初始化")
        return 2

    passed = sum(1 for r in w.records if r["ok"])
    failed = len(w.records) - passed
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base_url": args.base_url,
        "total": len(w.records),
        "passed": passed,
        "failed": failed,
        "defects": w.defects,
        "records": w.records,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"\n=== 人工模拟 {passed} PASS / {failed} FAIL ===")
    if w.defects:
        print(f"\n发现 {len(w.defects)} 项缺陷：")
        for d in w.defects:
            print(f"  {d['code']} {d['name']}: {d['defect']} | {d['detail']}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
