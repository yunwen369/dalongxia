#!/usr/bin/env python3
"""导航一致性动态探针（审查报告第九节复核项 #4 · 三处 current 态一致）。

背景：``frontend/js/app.js`` 的 NAV_TREE 是顶栏高亮、侧栏选中、面包屑渲染、
侧栏分组展开的唯一数据源。静态闸门 ``check_nav_tree.py`` 只能保证「配置齐备」，
无法证明「运行时真的三处一致」——2026-11-10 复验曾实测到 8/25 视图不符
（7 个 P1 新 IA 视图未登记 + 首页无归属），静态闸门当时尚未建立。

本探针遍历 ``index.html`` 侧栏全部 ``data-view``，逐视图真实渲染后读取：

- ``#main-nav .nav-item.active`` 文本（顶栏）；
- ``.sidebar-item.active`` 的 ``data-view``（侧栏）；
- ``#crumb`` 是否可见及文本（面包屑）。

三者任一为空即 FAIL。用法::

    python tools/nav_consistency_probe.py --base http://127.0.0.1:6905 --out nav.json

退出码：0 全通过 / 1 存在不符。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
import urllib.request

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)

from ui_clickthrough import CDP, launch_chrome, page_ws  # noqa: E402

STATE_JS = """JSON.stringify({
  "hash": location.hash,
  "nav": (document.querySelector('#main-nav .nav-item.active')||{}).textContent||'',
  "side": Array.from(document.querySelectorAll('.sidebar-item.active')).map(function(x){
      return x.getAttribute('data-view'); }),
  "crumb": (function(){var c=document.getElementById('crumb');
      return c && !c.hidden ? c.textContent.trim() : '';})(),
  "title": (document.getElementById('page-title')||{}).textContent||'',
  "open_groups": Array.from(document.querySelectorAll('.sidebar details.sb-group[open]'))
      .map(function(d){ return d.getAttribute('data-group'); })
})"""


def eval_js(cdp: CDP, expr: str):
    """执行 JS 并解析返回值（与 ui_clickthrough.eval_js 同口径）。

    Args:
        cdp: 已建立的 CDP 连接。
        expr: 待求值表达式（内部会用 IIFE 包装，勿自带 ``try/catch``）。

    Returns:
        Any: 解析后的值；表达式抛错或返回非法 JSON 时为 ``None``。
    """
    wrapped = ("(function(){try{window.__nv=(" + expr + ");}"
               "catch(e){window.__nv='ERR:'+(e&&e.message||e);}})()")
    cdp.call("Runtime.evaluate", {"expression": wrapped, "returnByValue": True})
    res = cdp.call("Runtime.evaluate", {"expression": "window.__nv", "returnByValue": True})
    inner = res.get("result", {}).get("result", {})
    value = inner.get("value") if isinstance(inner, dict) else None
    if isinstance(value, str):
        if value.startswith("ERR:"):
            return None
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def sidebar_views(base: str) -> list[str]:
    """从 index.html 抽取侧栏全部 data-view（保持文档顺序、去重）。

    Args:
        base: 服务地址。

    Returns:
        list[str]: 视图标识列表。
    """
    html = urllib.request.urlopen(base + "/", timeout=15).read().decode("utf-8", "replace")
    out: list[str] = []
    for match in re.finditer(r'data-action="view"\s+data-view="([^"]+)"', html):
        if match.group(1) not in out:
            out.append(match.group(1))
    return out


def main() -> int:
    """入口：逐视图核验三处一致并落盘 JSON。

    Returns:
        int: 0 = 全部通过；1 = 存在不符（供 CI/门禁使用）。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:6905")
    parser.add_argument("--out", required=True)
    parser.add_argument("--port", type=int, default=9366)
    args = parser.parse_args()

    views = sidebar_views(args.base)
    profile = tempfile.mkdtemp(prefix="edge_nav_")
    proc = launch_chrome(args.port, profile, 1440, 900)
    rows: list[dict] = []
    try:
        time.sleep(6)
        cdp = CDP(page_ws(args.port))
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        for view in views:
            cdp.call("Page.navigate", {"url": f"{args.base}/#/{view}"})
            time.sleep(2.6)
            state = eval_js(cdp, STATE_JS) or {}
            state["view"] = view
            state["ok_nav"] = bool(state.get("nav"))
            state["ok_side"] = view in (state.get("side") or [])
            state["ok_crumb"] = bool(state.get("crumb"))
            rows.append(state)
    finally:
        proc.terminate()

    bad = [r for r in rows if not (r["ok_nav"] and r["ok_side"] and r["ok_crumb"])]
    payload = {"base": args.base, "views": rows, "bad": bad,
               "summary": {"total": len(rows), "bad": len(bad)}}
    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    for row in rows:
        flag = "PASS" if (row["ok_nav"] and row["ok_side"] and row["ok_crumb"]) else "FAIL"
        print(f"{flag} {row['view']:<18} nav={row['nav']!r} side={row['side']} "
              f"crumb={row['crumb']!r} groups={row['open_groups']}")
    print(f"\nTOTAL={len(rows)} BAD={len(bad)}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
