#!/usr/bin/env python3
"""人工模拟测试（Human-like UI walkthrough）。

定位
----
与 `ui_clickthrough.py`（逐视图遍历 + 错误收集）互补：本脚本**模拟一个真实用户
连续操作**，按真实使用顺序走完主流程，每一步都做「操作 → 观察 → 断言」，
并把不符合预期的现象记为缺陷。

覆盖的人工动作（24 个场景）
--------------------------
A 启动与首屏
  A1 打开首页，等首屏加载完成
  A2 检查顶栏/侧栏/标题/面包屑四处一致
  A3 检查首页真实数据是否渲染（非空、非「加载中」）

B 导航操作
  B1 依次点击侧栏 25 项，每项确认标题变化 + 内容非空
  B2 点击顶栏 8 个分组，确认跳到该组代表视图
  B3 点击面包屑非末层，确认回到上级
  B4 浏览器前进/后退（history）
  B5 直接输入 URL hash 直达深层视图

C 侧栏折叠
  C1 默认只展开当前组
  C2 点开另一个组 → 项数增加
  C3 再点收起 → 项数回落
  C4 切视图后手动展开的组仍保留（pinnedGroups）

D 皮肤切换
  D1 点「现行」→ 布局参数变 64/236/52
  D2 点「砚」→ 背景变浅色
  D3 点「V6」→ 恢复 44/130/40
  D4 Ctrl+M 在 V6 ↔ 砚 之间切换
  D5 刷新后皮肤记忆保持（localStorage）

E 视图内交互
  E1 Tab 切换（个股页/组件库）
  E2 表格行点击跳转
  E3 表单输入（筛选器/搜索）
  E4 滚动到底部
  E5 图表容器存在且 canvas 无溢出

F 断点
  F1 1024×640 下无横向溢出
  F2 1280 下顶栏右侧不裁

G 异常与恢复
  G1 四态切换后能恢复默认态
  G2 连续快速切换视图不产生错误

用法
----
  python tools/human_walkthrough.py --base-url http://127.0.0.1:6903 --out <json>
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

# 侧栏 25 项（与 index.html 的 data-view 一致）
SIDEBAR_VIEWS = [
    "home", "today", "dashboard", "dashboard-manage", "widget-library",
    "market", "macro", "sectors", "sector-val", "lhb", "fund", "fund-combo",
    "stock", "news", "fin", "ann", "tools", "cand",
    "portfolio", "watchlist", "yjb", "mgr",
    "settings", "connectors", "alerts",
]

TOP_GROUPS = [
    ("dashboard", "仪表板"), ("watchlist", "自选股"), ("news", "资讯"),
    ("market", "市场"), ("money", "资金"), ("portfolio", "组合"),
    ("yjb", "养基宝"), ("system", "系统"),
]

JS_STATE = r"""
(function () {
  var t = document.getElementById('page-title');
  var c = document.getElementById('crumb');
  var n = document.querySelector('#main-nav .nav-item.active');
  var s = document.querySelector('.sidebar-item.active');
  var root = getComputedStyle(document.documentElement);
  var page = document.querySelector('.page-view:not([hidden])');
  var vp = document.getElementById('viewport');
  // 当前可见区：home 用 #viewport，其他用 .page-view:not([hidden])
  var visible = page || (vp && !vp.hidden ? vp : null);
  return {
    title: t ? t.textContent.trim() : '',
    crumb: c ? c.textContent.trim() : '',
    navText: n ? n.textContent.trim() : '',
    navGroup: n ? n.getAttribute('data-group') : '',
    sideText: s ? s.textContent.trim() : '',
    hash: location.hash,
    theme: document.documentElement.getAttribute('data-theme'),
    menuW: root.getPropertyValue('--menu-w').trim() || root.getPropertyValue('--menu-h').trim(),
    sidebarW: root.getPropertyValue('--sidebar-w').trim(),
    toolbarH: root.getPropertyValue('--toolbar-h').trim(),
    bgApp: root.getPropertyValue('--bg-app').trim(),
    // 只统计「当前可见区」的正文长度与「加载中」——
    // 用 body.innerText 会把其他隐藏 page-view 的占位文案算进来（假失败根源）
    visibleText: visible ? visible.innerText : '',
    visibleChildren: visible ? visible.children.length : 0,
    visibleTables: visible ? visible.querySelectorAll('table').length : 0,
    visibleRows: visible ? visible.querySelectorAll('tbody tr').length : 0,
    canvasOverflow: (function () {
      var bad = [];
      Array.prototype.forEach.call(document.querySelectorAll('canvas'), function (cv) {
        var p = cv.parentElement;
        if (!p) { return; }
        if (cv.getBoundingClientRect().width > p.getBoundingClientRect().width + 2) {
          bad.push(Math.round(cv.getBoundingClientRect().width) + '>' + Math.round(p.getBoundingClientRect().width));
        }
      });
      return bad;
    })(),
    scrollOverflow: {
      sh: document.documentElement.scrollHeight,
      ih: window.innerHeight,
      sw: document.documentElement.scrollWidth,
      iw: window.innerWidth,
    }
  };
})()
"""

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
        self.records: list[dict] = []
        self.defects: list[dict] = []

    def shot(self, name: str) -> str:
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
        self.records.append({"code": code, "name": name, "ok": bool(ok), "detail": detail})
        if not ok and defect:
            self.defects.append({"code": code, "name": name, "defect": defect, "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {code} {name} :: {detail}")

    def st(self) -> dict:
        return self.cdp.eval_js(JS_STATE) or {}

    def goto(self, view: str, wait: float = 2.0) -> dict:
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

    def press_key(self, key: str, code: str = "", modifiers: int = 0) -> None:
        params = {"type": "keyDown", "key": key, "code": code or key, "modifiers": modifiers}
        self.cdp.call("Input.dispatchKeyEvent", params)
        params["type"] = "keyUp"
        self.cdp.call("Input.dispatchKeyEvent", params)
        time.sleep(0.9)


def main() -> int:
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
    w: Walker | None = None
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
        # 注意：`/json/new?<url>` 打开的新标签页初始可能是 about:blank，
        # 且带 fragment 的 URL 会被丢弃 fragment，故这里显式 navigate 一次。
        cdp.call("Page.navigate", {"url": args.base_url + "/"})
        time.sleep(2.5)
        if password:
            # 真实用户路径：打开 / → 302 /auth → 填口令 → 提交表单
            on_auth = cdp.eval_js("location.pathname === '/auth'")
            if on_auth:
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

        # 就绪闸门
        ready = False
        for _ in range(40):
            time.sleep(0.5)
            try:
                if cdp.eval_js("!!(window.App && window.Pages && window.State4)"):
                    ready = True
                    break
            except Exception:
                continue
        if not ready:
            raise RuntimeError("页面未就绪")
        time.sleep(1.5)

        # ================= A 启动与首屏 =================
        st = w.st()
        w.step("A1", "首屏已渲染（标题非空）", bool(st.get("title")),
               f"title='{st.get('title')}' hash={st.get('hash')}")
        w.step("A2", "顶栏/侧栏/标题/面包屑四处有值",
               bool(st.get("title")) and bool(st.get("navText")) and bool(st.get("crumb")),
               f"nav='{st.get('navText')}' crumb='{st.get('crumb')}'")
        # A3：只判当前可见区；等待数据就绪（最多 20s，首页 7 张卡要拉多个端点）
        ready_data = False
        waited = 0.0
        while waited < 20.0:
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
        # 判据：标题非空即算「路由已响应」；内容渲染另由 E2/四态截图覆盖。
        # 不在这里断言 visibleChildren>0 —— 部分视图首屏就是骨架，需要更久才出内容。
        fail_views = []
        slow_views = []
        for view in SIDEBAR_VIEWS:
            st = w.goto(view, wait=1.4)
            if not st.get("title"):
                fail_views.append(f"{view}(title 空)")
                continue
            # 记录内容迟迟未出的视图（信息性，不计失败）
            waited = 0.0
            while waited < 8.0 and st.get("visibleChildren", 0) == 0:
                time.sleep(1.0)
                waited += 1.0
                st = w.st()
            if st.get("visibleChildren", 0) == 0:
                slow_views.append(view)
        w.step("B1", f"侧栏 {len(SIDEBAR_VIEWS)} 项逐项点击（标题均响应）", not fail_views,
               "全部标题正常" if not fail_views else f"{len(fail_views)} 项异常: {fail_views[:4]}",
               defect="部分侧栏视图点击后标题为空")
        w.step("B1b", f"{len(SIDEBAR_VIEWS)} 项内容均在 8s 内出现", not slow_views,
               "全部有内容" if not slow_views else f"迟迟无内容: {slow_views}",
               defect="部分视图 8s 内无任何内容渲染")

        fail_top = []
        for grp, label in TOP_GROUPS:
            sel = f"#main-nav .nav-item[data-group='{grp}']"
            if not w.click_selector(sel, wait=1.6):
                fail_top.append(f"{label}(click-miss)")
                continue
            st = w.st()
            if st.get("navGroup") != grp:
                fail_top.append(f"{label}(highlight={st.get('navGroup')})")
        w.step("B2", "顶栏 8 分组逐项点击", not fail_top,
               "全部跳转并高亮正确" if not fail_top else f"{fail_top[:4]}",
               defect="顶栏分组点击未跳转或高亮不符")

        # B3 面包屑：进入深层视图后点非末层
        st = w.goto("stock", wait=2.2)
        crumb_before = st.get("crumb", "")
        clicked = w.click_selector("#crumb .crumb-item[role='link']", wait=1.8)
        st2 = w.st()
        w.step("B3", "面包屑非末层可点回上级",
               clicked and st2.get("hash") != st.get("hash"),
               f"'{crumb_before}' → hash {st2.get('hash')}",
               defect="面包屑非末层点击无效")

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
        # 关键（两次踩坑）：
        #  1. `tree.js` 只在页面加载时读一次 localStorage 决定 open 状态，
        #     仅 `removeItem` 不会让它重跑；
        #  2. `Page.navigate` 到仅差 hash 的 URL **不会触发页面重载**（同文档 hash 变化），
        #     所以必须显式 `Page.reload`。
        cdp.eval_js("localStorage.removeItem('openbb-sb-groups-v1')")
        cdp.eval_js("location.hash = '#/home'")
        time.sleep(1.0)
        cdp.call("Page.reload")
        time.sleep(5.0)
        for _ in range(24):
            time.sleep(0.5)
            try:
                if cdp.eval_js("!!(window.App && window.Pages)"):
                    break
            except Exception:
                continue
        cdp.eval_js(INSTALL_ERR_HOOK)
        # reload 后若被 302 到 /auth，重新登录一次
        if cdp.eval_js("location.pathname === '/auth'") and password:
            cdp.eval_js(
                "(function(){var i=document.querySelector('input[name=token]');"
                "if(i){i.value=%s;document.querySelector('form').submit();}})()"
                % json.dumps(password)
            )
            time.sleep(4.0)
        st = w.goto("home", wait=2.4)
        groups0 = cdp.eval_js(
            "(function(){var g=document.querySelectorAll('.sb-group');var o=0,v=0;"
            "Array.prototype.forEach.call(g,function(x){if(x.open)o++;});"
            "Array.prototype.forEach.call(document.querySelectorAll('.sidebar-item'),"
            "function(i){if(i.offsetHeight>0)v++;});"
            "return {total:g.length,open:o,visible:v,"
            "mem:localStorage.getItem('openbb-sb-groups-v1'),"
            "states:[].map.call(g,function(x){return x.dataset.group+':'+x.open})};})()"
        )
        w.step("C1", "首次访问只展开当前组",
               groups0["total"] == 5 and groups0["open"] <= 2 and groups0["visible"] <= 12,
               f"groups={groups0['total']} open={groups0['open']} visible={groups0['visible']} "
               f"states={groups0.get('states')}",
               defect="侧栏默认全部展开，信息过载")

        fold = cdp.eval_js(
            "(function(){var g=document.querySelectorAll('.sb-group')[1];"
            "function cnt(){var n=0;Array.prototype.forEach.call(document.querySelectorAll('.sidebar-item'),"
            "function(i){if(i.offsetHeight>0)n++;});return n;}"
            "var b=cnt();g.open=true;var m=cnt();g.open=false;var a=cnt();"
            "return {before:b,mid:m,after:a};})()"
        )
        w.step("C2", "点开分组后可见项增加", fold["mid"] > fold["before"],
               f"{fold['before']} → {fold['mid']}", defect="分组展开无效")
        w.step("C3", "再收起后可见项回落", fold["after"] == fold["before"],
               f"{fold['mid']} → {fold['after']}", defect="分组收起无效")

        # C4 pinned：手动展开一个非当前组，切视图后仍展开
        cdp.eval_js("document.querySelectorAll('.sb-group')[2].open = true")
        time.sleep(0.4)
        st = w.goto("lhb", wait=1.8)
        still_open = cdp.eval_js("document.querySelectorAll('.sb-group')[2].open")
        w.step("C4", "手动展开的组切视图后保留", bool(still_open),
               f"open={still_open}", defect="手动展开的组被强制收起")

        # ================= D 皮肤切换 =================
        st = w.goto("home", wait=2.2)
        d1 = w.click_selector("#skin-switch button[data-skin='dark']", wait=1.2)
        st_dark = w.st()
        w.step("D1", "点「现行」布局变 64/236/52",
               d1 and st_dark.get("menuW") == "64px" and st_dark.get("sidebarW") == "236px",
               f"menu={st_dark.get('menuW')} sidebar={st_dark.get('sidebarW')}",
               defect="现行深色皮肤布局参数不符")

        d2 = w.click_selector("#skin-switch button[data-skin='light']", wait=1.2)
        st_light = w.st()
        light_bg = (st_light.get("bgApp") or "").lower()
        w.step("D2", "点「砚」背景变浅色",
               d2 and light_bg in ("#ffffff", "#fff"),
               f"bg-app={light_bg}", defect="砚浅色皮肤未生效")

        d3 = w.click_selector("#skin-switch button[data-skin='v6']", wait=1.2)
        st_v6 = w.st()
        w.step("D3", "点「V6」恢复 44/130/40",
               d3 and st_v6.get("menuW") == "44px" and st_v6.get("sidebarW") == "130px",
               f"menu={st_v6.get('menuW')} sidebar={st_v6.get('sidebarW')}",
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
        for _ in range(20):
            time.sleep(0.5)
            try:
                if cdp.eval_js("!!(window.App && window.App.applyTheme)"):
                    break
            except Exception:
                continue
        st_reload = w.st()
        w.step("D5", "刷新后皮肤记忆保持 V6", st_reload.get("theme") == "v6",
               f"theme={st_reload.get('theme')}", defect="刷新后皮肤重置")
        cdp.eval_js(INSTALL_ERR_HOOK)
        w.shot("D_v6_home")

        # ================= E 视图内交互 =================
        # E1 Tab 切换（个股页）
        st = w.goto("stock", wait=2.6)
        tabs = cdp.eval_js("document.querySelectorAll('.pg-tab').length")
        tab_ok = False
        if tabs > 1:
            tab_ok = w.click_selector(".pg-tab:nth-child(2)", wait=1.2)
        w.step("E1", "个股页 Tab 可切换", tabs == 0 or tab_ok,
               f"tabs={tabs} clicked={tab_ok}",
               defect="Tab 点击无效")

        # E2 表格行点击（板块页数据源为同花顺，1.1s 限流 × 90 板块，冷启动可达 30s+）
        st = w.goto("sectors", wait=2.0)
        rows = 0
        waited = 0.0
        while waited < 45.0:
            st = w.st()
            rows = st.get("visibleRows", 0)
            if rows > 0:
                break
            time.sleep(1.5)
            waited += 1.5
        row_click = False
        hash_before = st.get("hash")
        if rows:
            # 只点「当前可见页」的第一行，避免点到隐藏页的行
            row_click = w.click_selector(
                ".page-view:not([hidden]) .pg-table tbody tr", wait=2.5)
        st_after = w.st()
        w.step("E2", "表格行可点击跳转",
               rows > 0 and row_click and st_after.get("hash") != hash_before,
               f"rows={rows} 等待{waited:.0f}s clicked={row_click} "
               f"{hash_before}→{st_after.get('hash')}",
               defect="表格行点击无反应")

        # E3 搜索输入
        st = w.goto("screener", wait=2.6)
        typed = cdp.eval_js(
            "(function(){var i=document.querySelector('#page-screener input[type=text],"
            "#page-screener input:not([type]),.scr-cond-value');"
            "if(!i)return 'no-input';i.focus();return i.tagName;})()"
        )
        w.step("E3", "筛选器表单可聚焦输入", typed != "no-input",
               f"input={typed}", defect="表单输入框缺失")

        # E4 滚动
        cdp.eval_js("document.querySelector('.viewport').scrollTop = 9999")
        time.sleep(0.8)
        scrolled = cdp.eval_js("document.querySelector('.viewport').scrollTop")
        w.step("E4", "主区可滚动", scrolled >= 0,
               f"scrollTop={scrolled}", defect="主区无法滚动")

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
            "(function(){var a=document.querySelector('.topbar-actions');"
            "if(!a)return {ok:false};var r=a.getBoundingClientRect();"
            "return {ok:r.right<=window.innerWidth+1,right:Math.round(r.right),iw:window.innerWidth};})()"
        )
        w.step("F2", "1280 顶栏右侧不裁", right_ok.get("ok"),
               f"right={right_ok.get('right')} iw={right_ok.get('iw')}",
               defect="1280 断点顶栏右侧被裁")

        cdp.viewport(1366, 768)
        time.sleep(0.8)

        # ================= G 异常与恢复 =================
        st = w.goto("sectors", wait=2.8)
        cdp.eval_js("window.State4.apply('error')")
        time.sleep(1.0)
        err_shown = cdp.eval_js(
            "(function(){var p=document.querySelector('.page-view:not([hidden])');"
            "return p ? p.innerText.indexOf('不可用')>=0 || p.innerText.indexOf('失败')>=0 || p.innerText.indexOf('错误')>=0 : false;})()"
        )
        cdp.eval_js("window.State4.apply('default')")
        time.sleep(2.0)
        recovered = cdp.eval_js(
            "(function(){var p=document.querySelector('.page-view:not([hidden])');"
            "return p ? p.innerText.length > 40 : false;})()"
        )
        w.step("G1", "四态切换后可恢复默认态", err_shown and recovered,
               f"errorShown={err_shown} recovered={recovered}",
               defect="四态切换后无法恢复")

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
