#!/usr/bin/env python3
"""V6 定稿工程落地后的真实浏览器 UI 自测（Edge headless + CDP）。

复用 tools/ui_clickthrough.py 的 CDP 原语，覆盖：
  1) 会话鉴权（/auth?token=）后加载定稿页；
  2) 29 视图逐个 hash 导航，采集标题 / 卡片数 / 控制台错误 / 截图；
  3) 三皮肤切换（v6 / dark / light）后 body[data-theme] 与布局变量；
  4) 动态刷新 V6_LIVE.tick() 生效；
  5) 组件库「＋ 添加到页面」弹窗与目标下拉选项数。

用法：
  set NO_PROXY=127.0.0.1,localhost
  ./.venv/Scripts/python.exe tools/v6_landing_ui_probe.py --base-url http://127.0.0.1:6903
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ui_clickthrough", os.path.join(HERE, "ui_clickthrough.py"))
ui = importlib.util.module_from_spec(spec)
sys.modules["ui_clickthrough"] = ui
spec.loader.exec_module(ui)

VIEW_IDS = [
    "home", "today", "market", "dashboard", "dashboard-manage", "widget-library",
    "watchlist", "news", "sectors", "stock", "fin", "ann", "tools", "cand",
    "charting", "sector-val", "macro", "valuation", "crowding", "screener",
    "lhb", "fund-combo", "portfolio", "fund", "yjb", "mgr",
    "alerts", "connectors", "settings",
]


def shot(cdp, path: str) -> str:
    """截图并落盘。"""
    result = cdp.call("Page.captureScreenshot", {"captureBeyondViewport": False, "format": "png"})
    data = result.get("result", {}).get("data", "")
    if not data:
        return ""
    with open(path, "wb") as handle:
        handle.write(base64.b64decode(data))
    return path


def wait_ready(cdp, timeout: float = 40.0) -> bool:
    """等待定稿页挂载（views 配置 + 首页卡片）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        probe = ui.eval_js(cdp, "!!(window.V6_VIEWS && document.querySelectorAll('.card').length >= 4)")
        if probe is True:
            return True
        time.sleep(0.5)
    return False


def errs(cdp) -> list:
    return ui.eval_js(cdp, "(window.__cb_errors||[]).filter(function(e){"
                            "return e.kind==='error'||e.kind==='onerror';})") or []


def clear_errs(cdp) -> None:
    ui.eval_js(cdp, "(window.__cb_errors=[])")


def measure_skin(cdp) -> dict:
    """量测三皮肤：原型把 data-theme 挂 body，故自定义属性须从 body 取。"""
    return ui.eval_js(cdp, "JSON.stringify((function(){"
                            "var cs=getComputedStyle(document.body);"
                            "return {theme:document.body.getAttribute('data-theme'),"
                            "menu:(cs.getPropertyValue('--menu-h')||'').trim(),"
                            "side:(cs.getPropertyValue('--sidebar-w')||'').trim(),"
                            "bg:(cs.getPropertyValue('--bg-app')||'').trim()};})())")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:6903")
    parser.add_argument("--port", type=int, default=9356)
    parser.add_argument("--out", default="data/_tmp/v6_landing_ui.json")
    parser.add_argument("--shots", default="data/_tmp/v6_landing_shots")
    parser.add_argument("--settle", type=float, default=2.2)
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    token = os.environ.get("OPENBB_ACCESS_PASSWORD", "").strip()
    profile = os.path.join(HERE, "_v6_edge_profile")
    if os.path.isdir(profile):
        shutil.rmtree(profile, ignore_errors=True)
    os.makedirs(args.shots, exist_ok=True)

    proc = ui.launch_chrome(args.port, profile, 1440, 900)
    result = {"base": base, "views": [], "skins": {}, "live": {}, "modal": {}, "summary": {}}
    try:
        time.sleep(2.5)
        cdp = ui.CDP(ui.page_ws(args.port))
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": ui.CONSOLE_CAPTURE})
        ui.set_viewport(cdp, 1440, 900)

        # 1) 鉴权并加载定稿页
        cdp.call("Page.navigate", {"url": base + "/auth?token=" + token})
        time.sleep(3.0)
        cdp.call("Page.navigate", {"url": base + "/"})
        ready = wait_ready(cdp)
        result["summary"]["page_ready"] = ready
        result["summary"]["loaded_hash"] = ui.eval_js(cdp, "location.hash")
        result["summary"]["has_skin_switch"] = ui.eval_js(cdp, "!!document.querySelector('.skin-switch')")

        # 2) 29 视图
        for view_id in VIEW_IDS:
            clear_errs(cdp)
            ui.eval_js(cdp, "location.hash='#/" + view_id + "'")
            time.sleep(args.settle)
            title = ui.eval_js(cdp, "(document.getElementById('view-title')||{}).textContent")
            home_visible = ui.eval_js(cdp, "!document.getElementById('home-main').hidden")
            cards = ui.eval_js(cdp, "document.querySelectorAll("
                                    "'#view-body .card,#home-main .card').length")
            view_errors = errs(cdp)
            path = shot(cdp, os.path.join(args.shots, view_id + ".png"))
            result["views"].append({
                "id": view_id, "title": title, "home_visible": home_visible,
                "cards": cards, "errors": view_errors, "shot": os.path.basename(path),
            })
            print("[view] %-16s title=%s cards=%s err=%d" % (
                view_id, title, cards, len(view_errors)), flush=True)

        # 3) 三皮肤
        ui.eval_js(cdp, "location.hash='#/home'")
        time.sleep(1.2)
        for skin in ("v6", "dark", "light"):
            ui.eval_js(cdp, "(document.querySelector('.skin-switch [data-skin=\"" + skin + "\"]')||{}).click"
                            "&&document.querySelector('.skin-switch [data-skin=\"" + skin + "\"]').click()")
            time.sleep(1.2)
            result["skins"][skin] = measure_skin(cdp)
        ui.eval_js(cdp, "document.querySelector('.skin-switch [data-skin=\"v6\"]').click()")
        time.sleep(0.8)

        # 4) 动态刷新
        before = ui.eval_js(cdp, "(window.V6_LIVE||{}).ticks")
        ui.eval_js(cdp, "window.V6_LIVE && window.V6_LIVE.tick()")
        time.sleep(3.0)
        after = ui.eval_js(cdp, "(window.V6_LIVE||{}).ticks")
        result["live"] = {"ticks_before": before, "ticks_after": after,
                          "interval_ms": ui.eval_js(cdp, "(window.V6_LIVE||{}).intervalMs")}

        # 5) 组件添加弹窗
        ui.eval_js(cdp, "location.hash='#/widget-library'")
        time.sleep(2.5)
        widget_cards = ui.eval_js(cdp, "document.querySelectorAll('.widget-card').length")
        ui.eval_js(cdp, "(document.querySelector('.widget-add')||{}).click&&"
                        "document.querySelector('.widget-add').click()")
        time.sleep(0.8)
        modal_open = ui.eval_js(cdp, "!document.getElementById('v6-add-modal').hidden")
        option_count = ui.eval_js(cdp, "document.querySelectorAll('#v6-add-target option').length")
        result["modal"] = {"widget_cards": widget_cards, "modal_open": modal_open,
                           "target_options": option_count}
        shot(cdp, os.path.join(args.shots, "_add_modal.png"))

        result["summary"]["views_ok"] = sum(1 for v in result["views"] if not v["errors"])
        result["summary"]["views_total"] = len(result["views"])
        result["summary"]["views_with_errors"] = [v["id"] for v in result["views"] if v["errors"]]
    finally:
        proc.terminate()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print("\n=== SUMMARY ===")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print("skins:", json.dumps(result["skins"], ensure_ascii=False))
    print("live:", json.dumps(result["live"], ensure_ascii=False))
    print("modal:", json.dumps(result["modal"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
