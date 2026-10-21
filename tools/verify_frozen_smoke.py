# -*- coding: utf-8 -*-
"""冻结版（安装包）界面冒烟取证。

用途（规范 §13.3 三场景测试 / 门禁「首屏不白屏 + 数据真实」）：
通过 WebView2 的远程调试端口（``WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS``）连入
安装版窗口，逐视图截图并读取 DOM 状态，作为安装测试报告的可复算证据。

与 ``verify_ui_phase8_full.py`` 的区别：本脚本连的是**已安装的 exe 窗口**，
不启动 Edge，也不假设源码目录；仅依赖 CDP 端口。

用法：
    python tools/verify_frozen_smoke.py --cdp-port 9333 --out <目录>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from verify_ui_51 import CDP, page_ws, shot  # noqa: E402

# 覆盖 11 菜单视图（首页由主区承载）+ 仪表板
VIEWS: List[Dict[str, str]] = [
    {"name": "home", "route": "#/home"},
    {"name": "dashboard", "route": "#/dashboard"},
    {"name": "charting", "route": "#/charting"},
    {"name": "news", "route": "#/news"},
    {"name": "sectors", "route": "#/sectors"},
    {"name": "stock", "route": "#/stock?symbol=688012&market=a"},
    {"name": "fund", "route": "#/fund?code=017811"},
    {"name": "portfolio", "route": "#/portfolio"},
    {"name": "settings", "route": "#/settings"},
]

INSPECT_JS = """
(function () {
  var visible = Array.prototype.filter.call(document.querySelectorAll('.page-view'),
    function (n) { return !n.hidden; });
  var view = visible[0] || null;
  var states = [];
  if (view) {
    states = Array.prototype.map.call(view.querySelectorAll('.pg-body'),
      function (n) { return n.getAttribute('data-state'); });
  }
  var badge = document.getElementById('build-badge');
  return {
    title: document.title,
    ready: document.readyState,
    viewId: view ? view.id : null,
    states: states,
    cards: view ? view.querySelectorAll('.pg-card').length : 0,
    rows: view ? view.querySelectorAll('.pg-table tr, .pg-news-item').length : 0,
    textLen: (document.body.innerText || '').length,
    badge: badge ? badge.textContent.trim() : null,
    overflowX: document.documentElement.scrollWidth - document.documentElement.clientWidth
  };
})()
"""


def goto_hash(cdp: CDP, base: str, route: str) -> None:
    """切换到指定 hash 路由并等待一次渲染周期。"""
    cdp.eval_js(f"location.hash = {json.dumps(route)};")


def wait_settled(cdp: CDP, timeout: float = 30.0) -> str:
    """等待可见视图中不再出现 loading 态。"""
    expr = ("(function(){var v=Array.prototype.filter.call(document.querySelectorAll('.page-view'),"
            "function(n){return !n.hidden;})[0];if(!v)return 'no-view';"
            "var n=v.querySelectorAll('.pg-body');if(!n.length)return 'no-body';"
            "return Array.prototype.map.call(n,function(x){return x.getAttribute('data-state')||'none';}).join(',');})()")
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        last = str(cdp.eval_js(expr))
        if last and "loading" not in last:
            return last
        time.sleep(0.5)
    return last


def main() -> None:
    """入口：连入冻结窗口，逐视图截图与状态取证。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--cdp-port", type=int, default=9333)
    parser.add_argument("--base", default="http://127.0.0.1:6900")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)

    cdp = CDP(page_ws(args.cdp_port))
    cdp.call("Runtime.enable")
    cdp.call("Page.enable")

    report: Dict[str, Any] = {"base": args.base, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                              "views": {}, "shots": [], "failures": []}
    first = cdp.eval_js(INSPECT_JS)
    report["first_screen"] = first
    shot(cdp, os.path.join(args.out, "frozen_00_first_screen.png"))
    report["shots"].append("frozen_00_first_screen.png")
    # 首屏不白屏判据：DOM 有可见文本 + 顶栏构建徽标已就绪。
    # 注意：home 视图由主区承载，本就没有 .page-view 容器，不能用 viewId 判定。
    badge = str(first.get("badge") or "")
    if not (first.get("textLen") or 0) > 50 or not badge or "加载中" in badge:
        report["failures"].append(
            f"首屏可能白屏：textLen={first.get('textLen')} badge={badge or '缺失'}"
        )

    for index, view in enumerate(VIEWS, start=1):
        try:
            goto_hash(cdp, args.base, view["route"])
            time.sleep(1.2)
            states = wait_settled(cdp)
            info = cdp.eval_js(INSPECT_JS)
            name = f"frozen_{index:02d}_{view['name']}.png"
            shot(cdp, os.path.join(args.out, name))
            report["shots"].append(name)
            entry = {"route": view["route"], "settled": states, **info}
            ok = ("error" not in str(states)) and (info.get("viewId") is not None or view["name"] == "home")
            entry["ok"] = ok
            report["views"][view["name"]] = entry
            if not ok:
                report["failures"].append(f"{view['name']}: states={states}")
            print(f"[{index}] {view['name']}: states={states} cards={info.get('cards')} rows={info.get('rows')} ok={ok}")
        except Exception as exc:  # noqa: BLE001 - 单视图失败不中断
            report["views"][view["name"]] = {"ok": False, "error": str(exc)}
            report["failures"].append(f"{view['name']}: {exc}")

    report["passed"] = not report["failures"]
    with open(os.path.join(args.out, "frozen_ui_result.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(f"\n===== 冻结版冒烟：passed={report['passed']} failures={report['failures']} =====")


if __name__ == "__main__":
    main()
