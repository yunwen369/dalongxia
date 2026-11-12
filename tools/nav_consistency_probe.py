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
  "nav": (document.querySelector('.topnav button.active')||{}).getAttribute
         ? (document.querySelector('.topnav button.active')||{}).getAttribute('data-group') : '',
  "side": Array.from(document.querySelectorAll('#side-nav .side-item.active'))
      .map(function(x){ return x.getAttribute('data-view'); }),
  "crumb": (function(){var c=document.getElementById('view-crumb');
      return c ? c.textContent.trim() : '';})(),
  "title": (document.getElementById('view-title')||{}).textContent||'',
  "generic_hidden": !!(document.getElementById('view-generic')||{}).hidden,
  "open_groups": Array.from(document.querySelectorAll('#side-nav .side-group'))
      .filter(function(g){ var c=g.querySelector('.grp-caret');
        return c && c.textContent.trim() === '\u25be'; })
      .map(function(g){ return g.getAttribute('data-group'); })
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
    """从服务端 views.js 的 V6_VIEWS 抽取全部视图 id（保持注册顺序、去重）。

    2026-11-12：v3.1.0 起侧栏由 ``VIEW_IDS`` 动态渲染，index.html 不再有静态
    ``data-view``。若沿用旧法会得到 0 条并「0 错 0 通过」假绿，故改为解析
    ``/js/views.js``，并在视图数为 0 时 fail-closed（返回空 → main 判失败）。

    Args:
        base: 服务地址。

    Returns:
        list[str]: 视图标识列表。
    """
    src = urllib.request.urlopen(base + "/js/views.js", timeout=15).read().decode("utf-8", "replace")
    out: list[str] = []
    for match in re.finditer(r'^  V(?:\["([\w-]+)"\]|\.([\w-]+))\s*=\s*\{', src, re.M):
        vid = match.group(1) or match.group(2)
        if vid not in out:
            out.append(vid)
    return out


def views_meta(base: str) -> list[dict]:
    """解析 views.js，返回 [{id, group, sidebar}]（注册顺序）。

    Args:
        base: 服务地址。

    Returns:
        list[dict]: 视图元信息；解析为空时由 main 判失败（fail-closed）。
    """
    src = urllib.request.urlopen(base + "/js/views.js", timeout=15).read().decode("utf-8", "replace")
    out: list[dict] = []
    heads = list(re.finditer(r'^  V(?:\["([\w-]+)"\]|\.([\w-]+))\s*=\s*\{', src, re.M))
    for idx, match in enumerate(heads):
        vid = match.group(1) or match.group(2)
        stop = heads[idx + 1].start() if idx + 1 < len(heads) else len(src)
        chunk = src[match.end():min(match.end() + 400, stop)]
        group = re.search(r'\bgroup\s*:\s*"([\w-]+)"', chunk)
        sidebar = re.search(r'\bsidebar\s*:\s*("([\w-]+)"|null)', chunk)
        if any(item["id"] == vid for item in out):
            continue
        out.append({
            "id": vid,
            "group": group.group(1) if group else "",
            "sidebar": (sidebar.group(2) if sidebar and sidebar.group(2) else None),
        })
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
    parser.add_argument("--settle", type=float, default=2.6, help="每视图渲染等待秒数")
    args = parser.parse_args()

    meta = views_meta(args.base)
    if len(meta) < 5:
        print(f"FAIL 视图发现异常：仅解析到 {len(meta)} 个视图（应为 30+），判失败而非假绿")
        return 1

    profile = tempfile.mkdtemp(prefix="edge_nav_")
    proc = launch_chrome(args.port, profile, 1440, 900)
    rows: list[dict] = []
    try:
        time.sleep(6)
        cdp = CDP(page_ws(args.port))
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        for item in meta:
            view = item["id"]
            cdp.call("Page.navigate", {"url": f"{args.base}/#/{view}"})
            time.sleep(args.settle)
            state = eval_js(cdp, STATE_JS) or {}
            state["view"] = view
            state["expect_group"] = item["group"]
            state["subpage"] = item["sidebar"] is None
            # 判定：顶栏分组命中 → 侧栏选中命中（子页面豁免）→ 面包屑非空且含标题
            state["ok_nav"] = bool(state.get("nav")) and state.get("nav") == item["group"]
            state["ok_side"] = item["sidebar"] is None or view in (state.get("side") or [])
            crumb = state.get("crumb") or ""
            title = state.get("title") or ""
            state["ok_crumb"] = bool(crumb) and (title in crumb) if not state.get("generic_hidden") else True
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
        good = row["ok_nav"] and row["ok_side"] and row["ok_crumb"]
        print(("PASS " if good else "FAIL ") + f"{row['view']:<18} nav={row['nav']!r} "
              f"expect={row['expect_group']!r} side={row['side']} crumb={(row['crumb'] or '')[:28]!r} "
              f"title={(row['title'] or '')[:16]!r}")
    print(f"\nTOTAL={len(rows)} BAD={len(bad)}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
