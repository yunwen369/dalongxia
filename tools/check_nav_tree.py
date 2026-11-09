#!/usr/bin/env python3
"""静态校验：导航单一配置 NAV_TREE 的完整性（2026-11-10 复验补修 · 新增闸门）。

背景（审查报告第九节复核项 #4 / BUG-AR-05）：「首页与任一子页面下，顶栏 / 侧栏 /
面包屑三处 current 态一致」。该一致性完全依赖 ``frontend/js/app.js`` 的 NAV_TREE
单一配置——``syncNavActive`` 用它点亮顶栏、``renderCrumb`` 用它渲染面包屑、
``syncNavActive`` 还用它决定展开哪个侧栏分组。

2026-11-10 全量复验实测发现：第十轮 P1 新 IA 的 7 个视图（today / market / fin /
ann / tools / cand / mgr）**从未登记进 NAV_TREE**，结果这 7 页面包屑整条缺失、
顶栏无高亮，而既有复验只抽样 5 个「已登记」子页，属漏检。本闸门把该判据固化：

1. **覆盖性**：``index.html`` 侧栏出现的每个 ``data-view`` 必须是 NAV_TREE 的键；
2. **字段完整性**：每个条目必须有 ``sidebar``（非空）与 ``crumb``（非空链）；
3. **落点合法**：``crumb`` 中的跳转目标（第二元素非 null）必须是 NAV_TREE 的键；
4. **顶栏分组合法**：``group`` 非空时必须命中 ``index.html`` 顶栏的 ``data-group``；
5. **标题齐备**：``VIEW_TITLES`` 必须覆盖 NAV_TREE 的全部键。

任一违规 → 退出码 1；全绿输出统计行。

用法::

    python tools/check_nav_tree.py [--json <输出路径>]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "frontend", "index.html")
APP_JS = os.path.join(ROOT, "frontend", "js", "app.js")

NAV_BLOCK = re.compile(r"const NAV_TREE = \{(.*?)\n  \};", re.S)
# 条目：缩进 4 空格 + 键（可带引号）+ 冒号 + 花括号体（体内只有字符串与方括号）
ENTRY = re.compile(r"^\s{4}(?:\"([\w-]+)\"|([\w-]+)):\s*\{([^{}]*)\}", re.M)
SIDEBAR_VIEW = re.compile(r'data-action="view"\s+data-view="([\w-]+)"')
TOPBAR_GROUP = re.compile(r'class="nav-item"[^>]*data-group="([\w-]+)"')
TITLES_BLOCK = re.compile(r"const VIEW_TITLES = \{(.*?)\n  \};", re.S)
TITLE_KEY = re.compile(r"^\s{4}(?:\"([\w-]+)\"|([\w-]+)):", re.M)
CRUMB_TARGET = re.compile(r"\[\s*\"[^\"]+\"\s*,\s*(?:\"([\w-]+)\"|null)\s*\]")


def read(path: str) -> str:
    """读取 UTF-8 文本。"""
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def parse_nav_tree(js: str) -> dict[str, dict]:
    """解析 app.js 中的 NAV_TREE 条目。

    Args:
        js: app.js 全文。

    Returns:
        dict[str, dict]: 视图 → ``{group, sidebar, crumb, line}``；解析失败返回空字典。
    """
    block = NAV_BLOCK.search(js)
    if not block:
        return {}
    body = block.group(1)
    offset = block.start(1)
    out: dict[str, dict] = {}
    for match in ENTRY.finditer(body):
        key = match.group(1) or match.group(2)
        inner = match.group(3)
        group = re.search(r'group:\s*"([^"]*)"', inner)
        sidebar = re.search(r'sidebar:\s*"([^"]*)"', inner)
        crumb_raw = re.search(r"crumb:\s*(\[\[.*?\]\])", inner)
        targets = CRUMB_TARGET.findall(crumb_raw.group(1)) if crumb_raw else []
        out[key] = {
            "group": group.group(1) if group else None,
            "sidebar": sidebar.group(1) if sidebar else None,
            "crumb_targets": [t for t in targets if t],
            "has_crumb": bool(crumb_raw),
            "line": js[: offset + match.start()].count("\n") + 1,
        }
    return out


def main() -> None:
    """入口。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default=None, help="结果输出 JSON 路径")
    args = parser.parse_args()

    html = read(INDEX)
    js = read(APP_JS)
    sidebar_views = list(dict.fromkeys(SIDEBAR_VIEW.findall(html)))
    topbar_groups = list(dict.fromkeys(TOPBAR_GROUP.findall(html)))
    nav = parse_nav_tree(js)
    titles_match = TITLES_BLOCK.search(js)
    titles = {(m.group(1) or m.group(2)) for m in TITLE_KEY.finditer(titles_match.group(1))} \
        if titles_match else set()

    findings: list[str] = []
    if not nav:
        findings.append("app.js 未解析到 NAV_TREE（正则失配或配置被移除）")

    for view in sidebar_views:
        if view not in nav:
            findings.append(f"侧栏视图 {view} 未登记 NAV_TREE（面包屑缺失 + 顶栏无高亮）")

    for view, entry in nav.items():
        if not entry["sidebar"]:
            findings.append(f"NAV_TREE[{view}] 缺 sidebar 分组（L{entry['line']}）")
        if not entry["has_crumb"]:
            findings.append(f"NAV_TREE[{view}] 缺 crumb 链（L{entry['line']}）")
        group = entry["group"]
        if group and group not in topbar_groups:
            findings.append(
                f"NAV_TREE[{view}] group={group!r} 不在顶栏 data-group {topbar_groups} 内"
                f"（L{entry['line']}）"
            )
        for target in entry["crumb_targets"]:
            if target not in nav:
                findings.append(
                    f"NAV_TREE[{view}] 面包屑落点 {target!r} 不是已知视图（L{entry['line']}）"
                )

    for view in nav:
        if view not in titles:
            findings.append(f"VIEW_TITLES 缺 {view} 的标题（切换后标题会沿用上一页）")

    payload = {
        "sidebar_views": sidebar_views,
        "topbar_groups": topbar_groups,
        "nav_tree": sorted(nav),
        "titles": sorted(titles),
        "findings": findings,
    }
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

    if findings:
        print(f"FAIL  导航配置 {len(findings)} 项不一致：")
        for item in findings:
            print("  -", item)
        sys.exit(1)
    print(
        f"OK  NAV_TREE {len(nav)} 视图 / 侧栏 {len(sidebar_views)} 项全覆盖 / "
        f"顶栏 {len(topbar_groups)} 组 / 标题 {len(titles)} 条齐备"
    )


if __name__ == "__main__":
    main()
