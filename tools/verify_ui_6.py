# -*- coding: utf-8 -*-
"""阶段 6 前端取证：仪表板、命令面板、i18n、缩放、全视图四态巡检。

用法：
    .venv\\Scripts\\python.exe tools\\verify_ui_6.py --base http://127.0.0.1:6910 --out <截图目录>

取证点：
1. 仪表板默认 6 组件（含表格/图表/资讯/指标），双分辨率截图；
2. 加组件 + 刷新后仍存在（localStorage 持久化）；宽度调整改变 grid-column；
3. Ctrl+K 命令面板（视图/组件/操作三组，中文占位）；
4. i18n 生效（导航/标题来自语言包）、CSV 带 BOM；
5. 125%/150% 缩放无横向溢出；
6. 9 个视图 1366 截图，均无白屏/错误态。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from verify_ui_51 import CDP, goto, launch_edge, page_ws, set_viewport, shot, wait_blocks  # noqa: E402

VIEWS = [
    ("home", "#/home"),
    ("dashboard", "#/dashboard"),
    ("sectors", "#/sectors"),
    ("stock", "#/stock?symbol=688012&market=a"),
    ("sector-val", "#/sector-val?board=半导体&market=cn"),
    ("news", "#/news"),
    ("fund", "#/fund?code=017811"),
    ("portfolio", "#/portfolio"),
    ("connectors", "#/connectors"),
]


def main() -> None:
    """执行阶段 6 取证。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:6910")
    parser.add_argument("--out", required=True)
    parser.add_argument("--port", type=int, default=9342)
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    profile = os.path.join(os.environ.get("TEMP", "."), "edge_verify_6")
    proc = launch_edge(args.port, profile)
    result: Dict[str, Any] = {"base": args.base, "checks": {}}

    try:
        cdp = CDP(page_ws(args.port))
        cdp.call("Runtime.enable")

        # ---------- 1. 仪表板默认态 ----------
        set_viewport(cdp, 1366, 900)
        goto(cdp, args.base + "/#/dashboard")
        # 清掉上一次取证留下的布局，保证基线为默认 6 组件
        cdp.eval_js("localStorage.removeItem('openbb-dashboards-v1')")
        cdp.call("Page.enable")
        cdp.call("Page.reload", {"ignoreCache": True})
        time.sleep(3.0)
        wait_blocks(cdp, 40)
        result["checks"]["dash_widgets"] = cdp.eval_js("document.querySelectorAll('.dash-widget').length")
        result["checks"]["dash_tables"] = cdp.eval_js("document.querySelectorAll('.dash-widget tbody tr').length")
        result["checks"]["dash_canvas"] = cdp.eval_js("document.querySelectorAll('.dash-widget canvas').length")
        result["checks"]["i18n_nav"] = cdp.eval_js(
            "(function(){var n=document.querySelector('#main-nav .nav-item');return n?n.textContent:null;})()")
        result["checks"]["i18n_title"] = cdp.eval_js("window.I18N ? I18N.t('nav.dashboard') : null")
        result["checks"]["csv_bom"] = cdp.eval_js(
            "(function(){return window.L10N ? L10N.csvText(['a'],[['1']]).charCodeAt(0) : null;})()")
        shot(cdp, os.path.join(args.out, "dashboard_1366x900.png"))

        # ---------- 2. 加组件 + 持久化 ----------
        cdp.eval_js("window.Dashboard.addWidgetById('S-07')")
        time.sleep(1.2)
        after_add = cdp.eval_js("document.querySelectorAll('.dash-widget').length")
        cdp.call("Page.enable")
        cdp.call("Page.reload", {"ignoreCache": True})
        time.sleep(3.0)
        after_reload = cdp.eval_js("document.querySelectorAll('.dash-widget').length")
        result["checks"]["persistence"] = {"after_add": after_add, "after_reload": after_reload}

        # ---------- 3. 宽度调整（48 列吸附） ----------
        before_span = cdp.eval_js(
            "(function(){var n=document.querySelector('.dash-widget');return n?getComputedStyle(n).gridColumnStart:null;})()")
        cdp.eval_js("(function(){var b=document.querySelector('.dash-widget .pg-btn');if(b)b.click();})()")
        time.sleep(0.6)
        after_span = cdp.eval_js(
            "(function(){var n=document.querySelector('.dash-widget');return n?getComputedStyle(n).gridColumnStart:null;})()")
        result["checks"]["resize"] = {"before": before_span, "after": after_span,
                                      "changed": before_span != after_span}

        # ---------- 4. 命令面板 ----------
        cdp.eval_js("UI.openPalette()")
        time.sleep(1.0)
        result["checks"]["palette"] = {
            "open": cdp.eval_js("!!document.querySelector('.ui-palette')"),
            "placeholder": cdp.eval_js(
                "(function(){var n=document.querySelector('.ui-palette-input');return n?n.placeholder:null;})()"),
            "items": cdp.eval_js("document.querySelectorAll('.ui-palette-item').length"),
        }
        cdp.eval_js(
            "(function(){var i=document.querySelector('.ui-palette-input');"
            "i.value='板块';i.dispatchEvent(new Event('input'));})()")
        time.sleep(0.6)
        result["checks"]["palette"]["filtered"] = cdp.eval_js("document.querySelectorAll('.ui-palette-item').length")
        shot(cdp, os.path.join(args.out, "palette_1366x900.png"))
        cdp.eval_js("UI.closePalette()")

        # ---------- 5. 1024 与缩放 ----------
        set_viewport(cdp, 1024, 640)
        goto(cdp, args.base + "/#/dashboard")
        time.sleep(2.5)
        shot(cdp, os.path.join(args.out, "dashboard_1024x640.png"))
        result["checks"]["overflow_1024"] = cdp.eval_js(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth")

        # 缩放验证：应用强制 min-width 1024px（设计基准 §2.1），
        # 故按「物理窗口 = 1024×768 × 缩放倍数」设置，使 CSS 视口恒为 1024×768，
        # 以此验证 125%/150% 下栅格与卡片不错位（而非检查小窗口的横向滚动）。
        for scale in (1.25, 1.5):
            cdp.call("Emulation.setDeviceMetricsOverride",
                     {"width": int(1024 * scale), "height": int(768 * scale),
                      "deviceScaleFactor": scale, "mobile": False})
            time.sleep(1.2)
            shot(cdp, os.path.join(args.out, "dashboard_zoom_%d.png" % int(scale * 100)))
            result["checks"]["overflow_zoom_%d" % int(scale * 100)] = cdp.eval_js(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth")
            result["checks"]["overflow_nodes_%d" % int(scale * 100)] = cdp.eval_js(
                "(function(){var w=document.documentElement.clientWidth;var out=[];"
                "document.querySelectorAll('body *').forEach(function(n){var r=n.getBoundingClientRect();"
                "if(r.right>w+4&&r.width>40&&out.length<6){out.push((n.id||n.className||n.tagName)+':'+Math.round(r.right));}});"
                "return out;})()")
        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": 1366, "height": 900, "deviceScaleFactor": 1, "mobile": False})

        # ---------- 6. 逐视图巡检 ----------
        view_report = {}
        for name, route in VIEWS:
            goto(cdp, args.base + "/" + route)
            time.sleep(2.2)
            view_report[name] = {
                "height": cdp.eval_js(
                    "(function(){var v=Array.prototype.filter.call(document.querySelectorAll('.page-view'),"
                    "function(n){return !n.hidden;})[0];"
                    "if(!v){v=document.getElementById('viewport');}"
                    "return v?Math.round(v.getBoundingClientRect().height):0;})()"),
                "error": cdp.eval_js("document.querySelectorAll('.pg-state-title').length ? "
                                     "Array.prototype.filter.call(document.querySelectorAll('.pg-state-title'),"
                                     "function(n){return n.textContent.indexOf('加载失败')>=0;}).length : 0"),
            }
            shot(cdp, os.path.join(args.out, "view_%s_1366.png" % name))
        result["checks"]["views"] = view_report

        # ---------- 7. 浅色主题 ----------
        cdp.eval_js("document.documentElement.setAttribute('data-theme','light')")
        time.sleep(0.8)
        shot(cdp, os.path.join(args.out, "dashboard_light.png"))
        cdp.eval_js("document.documentElement.setAttribute('data-theme','dark')")

        result["checks"]["leaks"] = cdp.eval_js(
            "(function(){var t=document.body.innerText;"
            "return {err_code:/ERR_[A-Z_]+|UPSTREAM_|NOT_CONFIGURED|INVALID_PARAM/.test(t),"
            "english:/\\bundefined\\b|\\bNaN\\b/.test(t)};})()")

        cdp.close()
    finally:
        proc.terminate()

    out_json = os.path.join(args.out, "ui_verify_result_6.json")
    with open(out_json, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps(result["checks"], ensure_ascii=False, indent=1))
    print("结果文件：", out_json)


if __name__ == "__main__":
    main()
