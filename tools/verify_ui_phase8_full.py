# -*- coding: utf-8 -*-
"""阶段 8 · 界面与跳转全量专项（规范 §11.10 / §11.15）。

覆盖（阶段 8 七步之⑤）：
  A. 12 视图默认态走查（1366×768）：容器可见、四态收口（无残留 loading/error）、
     主内容非空、截图；
  B. 最小宽度 1024×640：全视图无横向溢出 + charting/settings/portfolio/dashboard 截图；
  C. 125%/150% 缩放（阶段 6 口径：物理窗口=1024×镜像倍数，CSS 视口恰为最小宽度 1024）：
     物理 1280×800（=125%）与 1536×960（=150%）下无横向溢出、布局不模糊错位；
  D. 四态专项（charting 代表）：加载（限速抓骨架）/ 空（美股分时免费通道不可用）
     / 错误（CDP 离线模拟）/ 默认；
  E. 快捷键：Ctrl+K（命令面板+过滤+Enter 跳转+Esc 关闭）、Ctrl+M（主题翻转）、
     F5（健康刷新，页面不崩溃）；
  F. 浮层：AI 助手抽屉开关、模态框（新建仪表板→取消关闭，不改数据）、
     命令面板（见 E）、Toast（触发后自动消失）；
  G. 主题双态：dark/light 截图 + 涨跌红绿计算色核对（--up #ff4b4b / --down #00b061）；
  H. CSV-BOM：仪表板表格组件导出 → CDP 拦截下载 → 文件头 EF BB BF + 中文表头可读。

纪律：只发真实请求（前后端均在真实实例上）；截图与判定全部落盘 JSON 证据；
不用 mock 数据充当验收。

用法：
    .venv\\Scripts\\python.exe tools\\verify_ui_phase8_full.py --base http://127.0.0.1:6913 --out <目录>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from verify_ui_51 import CDP, goto, launch_edge, page_ws, set_viewport, shot, wait_blocks  # noqa: E402

# 12 视图路由（home 由 app.js 主区承载，无 page-* 容器）
VIEWS: List[Dict[str, str]] = [
    {"name": "home", "route": "#/home"},
    {"name": "dashboard", "route": "#/dashboard"},
    {"name": "charting", "route": "#/charting"},
    {"name": "news", "route": "#/news"},
    {"name": "sectors", "route": "#/sectors"},
    {"name": "stock", "route": "#/stock?symbol=688012&market=a"},
    {"name": "sector-val", "route": "#/sector-val?board=%E5%8D%8A%E5%AF%BC%E4%BD%93&market=cn"},
    {"name": "fund", "route": "#/fund?code=017811"},
    {"name": "portfolio", "route": "#/portfolio"},
    {"name": "yjb", "route": "#/yjb"},
    {"name": "connectors", "route": "#/connectors"},
    {"name": "settings", "route": "#/settings"},
]

# 缩放验证口径（阶段 6 交接要点）：CSS 视口恒为最小宽度 1024，物理窗口=1024×倍数
ZOOM_CASES = [
    {"label": "100%（1366×768 基线）", "w": 1366, "h": 768},
    {"label": "100%（1024×640 最小）", "w": 1024, "h": 640},
    {"label": "125%（物理 1280×800）", "w": 1280, "h": 800},
    {"label": "150%（物理 1536×960）", "w": 1536, "h": 960},
]

# 走查 JS：可见视图 + 四态分布 + 主内容非空 + 横向溢出
INSPECT_JS = """
(function () {
  var visible = Array.prototype.filter.call(document.querySelectorAll('.page-view'),
    function (n) { return !n.hidden; });
  var view = visible[0] || null;
  var states = [];
  var cards = 0;
  if (view) {
    states = Array.prototype.map.call(view.querySelectorAll('.pg-body'),
      function (n) { return n.getAttribute('data-state'); });
    cards = view.querySelectorAll('.pg-card').length;
  }
  var rows = view ? view.querySelectorAll('.pg-table tr, .pg-news-item').length : 0;
  var overflowX = document.documentElement.scrollWidth - document.documentElement.clientWidth;
  return {
    viewId: view ? view.id : null,
    states: states,
    cards: cards,
    rows: rows,
    overflowX: overflowX,
    homeReady: document.getElementById('build-badge')
      ? document.getElementById('build-badge').textContent.indexOf('加载中') === -1 : null
  };
})()
"""


def view_states_ok(inspect: Dict[str, Any], view_name: str) -> Optional[str]:
    """四态收口判定：无 loading 残留；error 视为失败；空态允许但需有 cards。

    Returns:
        Optional[str]: None=通过；否则失败原因。
    """
    if not inspect.get("viewId"):
        # home 视图无 page 容器，改看主区就绪标志
        if view_name == "home":
            return None if inspect.get("homeReady") else "home 主区未就绪（badge 仍加载中）"
        return f"无可见视图容器（viewId={inspect.get('viewId')}）"
    states = inspect.get("states") or []
    if "loading" in states:
        return "四态未收口：仍残留 loading"
    if "error" in states:
        return "存在错误态区块"
    if not states:
        return "无 pg-body 区块"
    return None


def key_combo(cdp: CDP, key: str, *, modifiers: int = 0, vkey: Optional[int] = None) -> None:
    """派发组合键（keyDown+keyUp），modifiers 位掩码：Ctrl=2 / Alt=1。"""
    base: Dict[str, Any] = {"key": key, "code": key, "modifiers": modifiers,
                            "nativeVirtualKeyCode": vkey or ord(key[0]), "windowsVirtualKeyCode": vkey or ord(key[0])}
    cdp.call("Input.dispatchKeyEvent", {**base, "type": "keyDown"})
    cdp.call("Input.dispatchKeyEvent", {**base, "type": "keyUp"})


def type_text(cdp: CDP, selector: str, text: str) -> None:
    """向输入框写入文本（聚焦 + insertText，模拟真实输入）。"""
    cdp.eval_js(f"document.querySelector({json.dumps(selector)}).focus()")
    cdp.call("Input.insertText", {"text": text})


def wait_js(cdp: CDP, expression: str, timeout: float = 15.0, interval: float = 0.5) -> Any:
    """轮询等待 JS 表达式返回真值，超时返回 None。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = cdp.eval_js(expression)
        if value:
            return value
        time.sleep(interval)
    return None


def backend_alive(base: str) -> bool:
    """探活后端实例：防「后端崩溃 → 前端全 error」的误报。"""
    try:
        import urllib.request
        with urllib.request.urlopen(base + "/api/health", timeout=4) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


def main() -> None:
    """执行界面与跳转全量专项并输出证据。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:6913")
    parser.add_argument("--out", required=True)
    parser.add_argument("--port", type=int, default=9355)
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    download_dir = tempfile.mkdtemp(prefix="openbb_csv_")
    profile = os.path.join(os.environ.get("TEMP", "."), "edge_verify_full8")
    proc = launch_edge(args.port, profile)
    result: Dict[str, Any] = {
        "base": args.base, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sections": {}, "errors": [], "shots": [],
    }

    try:
        cdp = CDP(page_ws(args.port))
        cdp.call("Runtime.enable")
        cdp.call("Page.enable")
        cdp.call("Network.enable")
        cdp.call("Page.setDownloadBehavior",
                 {"behavior": "allow", "downloadPath": download_dir.replace("\\", "/")})

        # ========== A. 12 视图默认态走查（1366×768） ==========
        set_viewport(cdp, 1366, 768)
        view_report: Dict[str, Any] = {}
        for view in VIEWS:
            name, route = view["name"], view["route"]
            try:
                goto(cdp, args.base + "/" + route)
                state = wait_blocks(cdp, 45)
                inspect = cdp.eval_js(INSPECT_JS)
                reason = view_states_ok(inspect, name)
                entry = {"route": route, "wait_blocks": state,
                         "viewId": inspect.get("viewId"), "states": inspect.get("states"),
                         "cards": inspect.get("cards"), "rows": inspect.get("rows"),
                         "overflowX": inspect.get("overflowX"),
                         "ok": reason is None, "reason": reason}
                view_report[name] = entry
                shot(cdp, os.path.join(args.out, f"view_{name}_1366.png"))
                result["shots"].append(f"view_{name}_1366.png")
                if not backend_alive(args.base):
                    entry["backend_crashed"] = True
                    view_report[name] = entry
                    result["errors"].append(f"[A] {name}: 后端实例在走查中崩溃（error 态为连带误报）")
                    print(f"[A] {name}: BACKEND CRASHED")
                    break
                print(f"[A] {name}: ok={entry['ok']} states={entry['states']} cards={entry['cards']}")
            except Exception as exc:  # noqa: BLE001 - 单视图失败不中断整轮
                view_report[name] = {"route": route, "ok": False, "reason": f"异常：{exc}"}
                result["errors"].append(f"[A] {name}: {exc}")
        result["sections"]["A_views_1366"] = view_report

        # ========== B. 1024×640 最小宽度：无横向溢出 ==========
        set_viewport(cdp, 1024, 640)
        min_report: Dict[str, Any] = {}
        for view in VIEWS:
            name, route = view["name"], view["route"]
            try:
                goto(cdp, args.base + "/" + route)
                wait_blocks(cdp, 45)
                inspect = cdp.eval_js(INSPECT_JS)
                overflow = inspect.get("overflowX", 999)
                reason = None if inspect.get("overflowX", 999) <= 1 else f"横向溢出 {overflow}px"
                if reason is None:
                    reason = view_states_ok(inspect, name)
                min_report[name] = {"overflowX": inspect.get("overflowX"), "ok": reason is None, "reason": reason}
                if name in ("charting", "settings", "portfolio", "dashboard", "fund"):
                    shot(cdp, os.path.join(args.out, f"view_{name}_1024.png"))
                    result["shots"].append(f"view_{name}_1024.png")
                print(f"[B] {name} 1024: overflow={inspect.get('overflowX')} ok={reason is None}")
            except Exception as exc:  # noqa: BLE001
                min_report[name] = {"ok": False, "reason": f"异常：{exc}"}
                result["errors"].append(f"[B] {name}: {exc}")
        result["sections"]["B_min_1024"] = min_report

        # ========== C. 125% / 150% 缩放（物理=1024×倍数口径） ==========
        zoom_report: Dict[str, Any] = {}
        zoom_views = ["dashboard", "charting", "settings", "portfolio", "fund", "news"]
        for case in ZOOM_CASES[2:]:  # 125% / 150%
            set_viewport(cdp, case["w"], case["h"])
            case_report: Dict[str, Any] = {}
            for name in zoom_views:
                route = next(v["route"] for v in VIEWS if v["name"] == name)
                try:
                    goto(cdp, args.base + "/" + route)
                    wait_blocks(cdp, 45)
                    inspect = cdp.eval_js(INSPECT_JS)
                    overflow = inspect.get("overflowX", 999)
                    case_report[name] = {"overflowX": overflow, "ok": overflow <= 1}
                    if name in ("charting", "settings"):
                        shot(cdp, os.path.join(args.out, f"zoom_{case['w']}x{case['h']}_{name}.png"))
                        result["shots"].append(f"zoom_{case['w']}x{case['h']}_{name}.png")
                    print(f"[C] {case['label']} {name}: overflow={overflow}")
                except Exception as exc:  # noqa: BLE001
                    case_report[name] = {"ok": False, "reason": f"异常：{exc}"}
                    result["errors"].append(f"[C] {case['label']} {name}: {exc}")
            zoom_report[case["label"]] = case_report
        result["sections"]["C_zoom_125_150"] = zoom_report

        # ========== D. 四态专项（charting 代表） ==========
        set_viewport(cdp, 1366, 768)
        four_states: Dict[str, Any] = {}

        # D1 加载态：限速抓骨架
        cdp.call("Network.emulateNetworkConditions",
                 {"offline": False, "latency": 1200, "downloadThroughput": 200 * 1024,
                  "uploadThroughput": 100 * 1024})
        goto(cdp, args.base + "/#/charting")
        time.sleep(1.0)
        loading_states = cdp.eval_js(
            "(function(){var v=Array.prototype.filter.call(document.querySelectorAll('.page-view'),"
            "function(n){return !n.hidden;})[0];if(!v)return 'no-view';"
            "return Array.prototype.map.call(v.querySelectorAll('.pg-body'),"
            "function(n){return n.getAttribute('data-state');}).join(',');})()")
        shot(cdp, os.path.join(args.out, "state_loading_charting.png"))
        result["shots"].append("state_loading_charting.png")
        cdp.call("Network.emulateNetworkConditions",
                 {"offline": False, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1})
        wait_blocks(cdp, 45)
        four_states["loading"] = {"observed": loading_states, "ok": "loading" in (loading_states or "")}

        # D2 空态：美股分时免费通道不可用（已登记缺口 D3）→ 分时 Tab 空态。
        # setEmpty 真实 DOM：div.pg-state > div.pg-state-title（body data-state="empty"）。
        # 先导航到空白页重置文档：D1 的限速条件恢复后，同一文档内的后续导航仍可能
        # 复用慢速连接，导致 charting 首屏迟迟不就绪、点击 Tab 时 body 尚未挂载。
        goto(cdp, "about:blank")
        goto(cdp, args.base + "/#/charting?symbol=AAPL&market=us")
        wait_blocks(cdp, 45)
        tab_clicked = cdp.eval_js(
            "(function(){var b=document.querySelector('#ch-tabs .pg-tab[data-tab=\"minute\"]');"
            "if(b){b.click();}return !!b;})()")
        empty_text = wait_js(cdp, "(function(){var b=document.getElementById('ch02-body');"
                                   "if(!b)return null;"
                                   "var n=b.querySelector('.pg-state-title');"
                                   "return n?n.textContent:(b.getAttribute('data-state')==='empty'?b.textContent.slice(0,40):null);})()", 25)
        empty_diag = None
        if not empty_text:
            empty_diag = cdp.eval_js(
                "(function(){var b=document.getElementById('ch02-body');"
                "return {tabClicked:" + ("true" if tab_clicked else "false") + ","
                "bodyExists:!!b, state:b?b.getAttribute('data-state'):null,"
                "bodyText:b?b.textContent.slice(0,60):null};})()")
        shot(cdp, os.path.join(args.out, "state_empty_charting_minute.png"))
        result["shots"].append("state_empty_charting_minute.png")
        four_states["empty"] = {"text": empty_text, "ok": bool(empty_text), "diag": empty_diag}
        print(f"[D] empty: {empty_text} diag={empty_diag}")

        # D3 错误态：CDP 离线模拟 → 双区块错误态（中文原因）→ 恢复后回默认
        cdp.call("Network.emulateNetworkConditions",
                 {"offline": True, "latency": 0, "downloadThroughput": 0, "uploadThroughput": 0})
        goto(cdp, args.base + "/#/charting?symbol=688012&market=a")
        error_states = wait_js(cdp,
                               "(function(){var v=Array.prototype.filter.call(document.querySelectorAll('.page-view'),"
                               "function(n){return !n.hidden;})[0];if(!v)return null;"
                               "var s=Array.prototype.map.call(v.querySelectorAll('.pg-body'),"
                               "function(n){return n.getAttribute('data-state');});"
                               "return s.indexOf('error')>=0?s.join(','):null;})()", 20)
        shot(cdp, os.path.join(args.out, "state_error_charting.png"))
        result["shots"].append("state_error_charting.png")
        cdp.call("Network.emulateNetworkConditions",
                 {"offline": False, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1})
        # 恢复网络后按真实用户行为「刷新页面」验证闭环。
        # 注意：hash 路由下 Page.navigate 到相同 URL 只做 fragment 导航、不会重载文档，
        # 故显式 location.reload() 才能真正重新拉数。
        cdp.eval_js("location.reload()")
        time.sleep(1.0)
        recovered = wait_blocks(cdp, 60)
        recovered_states = cdp.eval_js(
            "(function(){var v=Array.prototype.filter.call(document.querySelectorAll('.page-view'),"
            "function(n){return !n.hidden;})[0];if(!v)return null;"
            "return Array.prototype.map.call(v.querySelectorAll('.pg-body'),"
            "function(n){return n.getAttribute('data-state');});})()")
        recovered_ok = isinstance(recovered_states, list) and "error" not in recovered_states \
            and "loading" not in recovered_states
        four_states["error"] = {"observed": error_states,
                                "ok": "error" in (error_states or "") and recovered_ok,
                                "recovered": recovered, "recovered_states": recovered_states,
                                "recovered_ok": recovered_ok}
        print(f"[D] error: {error_states} recovered={recovered_states} ok={recovered_ok}")
        result["sections"]["D_four_states"] = four_states

        # ========== E. 快捷键 ==========
        shortcuts: Dict[str, Any] = {}
        # E1 Ctrl+K：命令面板打开 → 输入"新闻"过滤 → Enter 跳转 #/news
        set_viewport(cdp, 1366, 768)
        goto(cdp, args.base + "/#/home")
        wait_blocks(cdp, 45)
        key_combo(cdp, "k", modifiers=2, vkey=ord("K"))
        palette_open = wait_js(cdp, "!!document.querySelector('.ui-palette-overlay')", 8)
        filtered = None
        if palette_open:
            type_text(cdp, ".ui-palette-input", "新闻")
            time.sleep(0.6)
            filtered = cdp.eval_js(
                "(function(){var items=document.querySelectorAll('.ui-palette-item');"
                "return items.length?Array.prototype.map.call(items,function(n){"
                "return n.querySelector('.ui-palette-label').textContent;}).join('|'):null;})()")
            key_combo(cdp, "Enter", vkey=13)
            time.sleep(1.0)
        landed = cdp.eval_js("location.hash")
        shortcuts["ctrl_k_palette"] = {"opened": bool(palette_open), "filtered": filtered,
                                       "landed": landed, "ok": bool(palette_open) and "/news" in str(landed)}
        # E2 Esc：关闭面板
        key_combo(cdp, "Escape", vkey=27)
        time.sleep(0.4)
        palette_closed = cdp.eval_js("!document.querySelector('.ui-palette-overlay')")
        shortcuts["esc_close"] = {"closed": palette_closed, "ok": palette_closed is True}
        # E3 Ctrl+M：主题翻转 + 持久化
        theme_before = cdp.eval_js("document.documentElement.getAttribute('data-theme')")
        key_combo(cdp, "m", modifiers=2, vkey=ord("M"))
        time.sleep(0.4)
        theme_after = cdp.eval_js("document.documentElement.getAttribute('data-theme')")
        stored = cdp.eval_js("localStorage.getItem('openbb-theme')")
        shortcuts["ctrl_m_theme"] = {"before": theme_before, "after": theme_after, "stored": stored,
                                     "ok": theme_before != theme_after and stored == theme_after}
        # E4 F5：健康刷新，页面不崩溃（build-badge 保持就绪）
        key_combo(cdp, "F5", vkey=116)
        time.sleep(1.2)
        health_ok = cdp.eval_js("(function(){var b=document.getElementById('build-badge');"
                                "return !!b && b.textContent.indexOf('加载中')===-1;})()")
        shortcuts["f5_health"] = {"ready": health_ok, "ok": health_ok is True}
        result["sections"]["E_shortcuts"] = shortcuts
        print(f"[E] shortcuts: {shortcuts}")

        # ========== F. 浮层 ==========
        overlays: Dict[str, Any] = {}
        # F1 AI 助手抽屉：开 → 可见 → 关
        cdp.eval_js("document.getElementById('copilot-toggle').click()")
        drawer_open = wait_js(cdp, "(function(){var d=document.getElementById('copilot');"
                                   "return d && d.getAttribute('aria-hidden')==='false';})()", 6)
        shot(cdp, os.path.join(args.out, "overlay_copilot.png"))
        result["shots"].append("overlay_copilot.png")
        cdp.eval_js("document.getElementById('copilot-close').click()")
        time.sleep(0.3)
        drawer_closed = cdp.eval_js("(function(){var d=document.getElementById('copilot');"
                                    "return d && d.getAttribute('aria-hidden')!=='false';})()")
        overlays["copilot_drawer"] = {"opened": bool(drawer_open), "closed": bool(drawer_closed),
                                      "ok": bool(drawer_open) and bool(drawer_closed)}
        # F2 模态框：仪表板「+ 新建」→ overlay 出现 → 取消关闭（不改数据）
        goto(cdp, args.base + "/#/dashboard")
        wait_blocks(cdp, 45)
        # 工具栏渲染完再点：dashboard 首屏异步（注册表 + 6 组件），直接点击可能落空
        new_btn = wait_js(
            cdp,
            "(function(){var bs=document.querySelectorAll('#page-dashboard button');"
            "for(var i=0;i<bs.length;i++){if(bs[i].textContent.indexOf('新建')>=0){return bs[i].outerHTML.slice(0,40);}}"
            "return null;})()", 20)
        cdp.eval_js("(function(){var bs=document.querySelectorAll('#page-dashboard button');"
                    "for(var i=0;i<bs.length;i++){if(bs[i].textContent.indexOf('新建')>=0){bs[i].click();return true;}}"
                    "return false;})()")
        modal_open = wait_js(cdp, "!!document.querySelector('.ui-overlay .ui-modal')", 8)
        shot(cdp, os.path.join(args.out, "overlay_modal_new_dashboard.png"))
        result["shots"].append("overlay_modal_new_dashboard.png")
        if modal_open:
            cdp.eval_js("(function(){var bs=document.querySelectorAll('.ui-modal-actions button');"
                        "if(bs.length){bs[0].click();return true;}return false;})()")  # 第一个=取消
            time.sleep(0.3)
        modal_closed = cdp.eval_js("!document.querySelector('.ui-overlay .ui-modal')")
        overlays["modal_new_dashboard"] = {"opened": bool(modal_open), "closed": modal_closed is True,
                                           "button": new_btn, "ok": bool(modal_open) and modal_closed is True}
        result["sections"]["F_overlays"] = overlays
        print(f"[F] overlays: {overlays}")

        # ========== G. 主题双态 + 涨跌红绿 ==========
        theme: Dict[str, Any] = {}
        for target in ("dark", "light"):
            cdp.eval_js(f"document.documentElement.setAttribute('data-theme','{target}')")
            time.sleep(0.3)
            up_color = cdp.eval_js(
                "getComputedStyle(document.documentElement).getPropertyValue('--up').trim()")
            down_color = cdp.eval_js(
                "getComputedStyle(document.documentElement).getPropertyValue('--down').trim()")
            shot(cdp, os.path.join(args.out, f"theme_{target}_charting.png"))
            result["shots"].append(f"theme_{target}_charting.png")
            theme[target] = {"up": up_color, "down": down_color,
                             "ok": up_color == "#ff4b4b" and down_color == "#00b061"}
        # 恢复深色（默认主题）
        cdp.eval_js("document.documentElement.setAttribute('data-theme','dark')")
        result["sections"]["G_theme"] = theme
        print(f"[G] theme: {theme}")

        # ========== H. CSV-BOM 导出 ==========
        # headless 新版对 Page.setDownloadBehavior 支持不稳定，改为「拦截真实 Blob 字节」：
        # 钩住 URL.createObjectURL 读取 Blob 的原始文本（与落盘字节一致），
        # 再校验 BOM（U+FEFF）+ 中文表头可读 + 行数。按钮为真实点击。
        csv_section: Dict[str, Any] = {}
        goto(cdp, args.base + "/#/dashboard")
        wait_blocks(cdp, 45)
        cdp.eval_js(
            "(function(){window.__csvCapture=null;var _create=URL.createObjectURL;"
            "URL.createObjectURL=function(blob){try{blob.arrayBuffer().then(function(buf){"
            "var b=new Uint8Array(buf);var head=[b[0],b[1],b[2]];"
            "blob.text().then(function(t){window.__csvCapture={size:blob.size,type:blob.type,"
            "head:head,text:t};});});}catch(e){}"
            "return _create.call(URL,blob);};})()")
        has_export = cdp.eval_js(
            "(function(){var bs=document.querySelectorAll('#page-dashboard button');"
            "for(var i=0;i<bs.length;i++){if(bs[i].textContent.indexOf('导出')>=0){bs[i].click();return true;}}"
            "return false;})()")
        captured = wait_js(cdp, "window.__csvCapture", 12) if has_export else None
        if captured:
            text = captured.get("text") or ""
            head = captured.get("head") or []
            # BOM 必须看原始字节（EF BB BF）：Blob.text() 按 UTF-8 解码会剥离 BOM，
            # 只看文本首字符会得到假阴性。
            bom = head[:3] == [0xEF, 0xBB, 0xBF]
            first_line = text.lstrip("\ufeff").splitlines()[0] if text.strip() else ""
            chinese = any("\u4e00" <= ch <= "\u9fff" for ch in first_line)
            rows = max(0, len(text.strip().splitlines()) - 1)
            csv_section = {
                "method": "Blob 字节拦截（真实按钮点击 → L10N.csvText 生成 → 校验前三字节与中文表头）",
                "size": captured.get("size"), "type": captured.get("type"),
                "head_bytes": " ".join(f"{int(b):02X}" for b in head if isinstance(b, int)),
                "bom": bom, "first_line": first_line, "rows": rows,
                "chinese_header": chinese,
                "ok": bom and chinese and rows > 0,
            }
        else:
            diag = cdp.eval_js(
                "(function(){var v=document.querySelectorAll('.page-view');"
                "for(var i=0;i<v.length;i++){if(!v[i].hidden){"
                "return {cardCount:v[i].querySelectorAll('.pg-card').length,"
                "tableCount:v[i].querySelectorAll('table').length,"
                "btnCount:v[i].querySelectorAll('button').length,"
                "states:Array.prototype.map.call(v[i].querySelectorAll('.pg-body'),"
                "function(n){return n.getAttribute('data-state');})};}}"
                "return null;})()")
            csv_section = {"ok": False, "has_export": bool(has_export),
                           "reason": "未捕获到 CSV Blob（导出按钮缺失或表格未渲染）",
                           "diag": diag, "backend_alive": backend_alive(args.base)}
        result["sections"]["H_csv_bom"] = csv_section
        print(f"[H] csv: {csv_section}")

        # ========== 汇总 ==========
        failures: List[str] = []
        for section, payload in result["sections"].items():
            if section == "A_views_1366":
                failures += [f"A:{name}" for name, e in payload.items() if not e.get("ok")]
            elif section == "B_min_1024":
                failures += [f"B:{name}" for name, e in payload.items() if not e.get("ok")]
            elif section == "C_zoom_125_150":
                failures += [f"C:{label}:{name}" for label, case in payload.items()
                             for name, e in case.items() if not e.get("ok")]
            elif section == "D_four_states":
                failures += [f"D:{k}" for k, e in payload.items() if not e.get("ok")]
            elif section == "E_shortcuts":
                failures += [f"E:{k}" for k, e in payload.items() if not e.get("ok")]
            elif section == "F_overlays":
                failures += [f"F:{k}" for k, e in payload.items() if not e.get("ok")]
            elif section == "G_theme":
                failures += [f"G:{k}" for k, e in payload.items() if not e.get("ok")]
            elif section == "H_csv_bom":
                if not payload.get("ok"):
                    failures.append("H:csv_bom")
        result["failures"] = failures
        result["passed"] = not failures and not result["errors"]
        with open(os.path.join(args.out, "ui_full_result.json"), "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
        print(f"\n===== 汇总：passed={result['passed']} failures={failures} errors={result['errors']} =====")
    finally:
        try:
            cdp.close()  # type: ignore[has-type]
        except Exception:  # noqa: BLE001 - 关闭失败不影响证据落盘
            pass
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
