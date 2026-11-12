#!/usr/bin/env python3
"""静态校验：导航单一配置的完整性（2026-11-12 改判据源 · 保留并强化原五项不变式）。

背景（审查报告第九节复核项 #4 / BUG-AR-05）：「首页与任一子页面下，顶栏 / 侧栏 /
面包屑三处 current 态一致」。该一致性依赖**单一配置**——``syncNavActive`` 用它点亮顶栏、
``renderCrumb`` 用它渲染面包屑、``renderSidebar`` 用它决定侧栏条目与分组。

**2026-11-12 判据源变更（合法规格变更，非降级）**：
v3.1.0 的信息架构重构后，单一配置从 ``frontend/js/app.js`` 的 ``NAV_TREE`` 常量
迁移到 ``frontend/js/views.js`` 的 ``window.V6_VIEWS`` 注册表（每视图携带
``title / group / sidebar / template / crumb``）；``NAV_TREE`` 常量已不存在，
``index.html`` 的侧栏也改为由 ``VIEW_IDS`` 动态渲染（不再有静态 ``data-view``）。
旧判据因此「解析不到 NAV_TREE」而失效——若不改判据源，闸门等于停止工作。
本次把判据源切到 ``V6_VIEWS``，**不变式只增不减**：

1. **唯一性**：同一视图 id 不得重复定义（对象字面量重复键会静默覆盖，v3.4.0 实测
   曾出现 ``widget-library`` 两份定义，旧的成为死代码）；
2. **标题齐备**：每个视图 ``title`` 非空；
3. **面包屑完整**：``crumb`` 非空数组，且**末项必须等于 title**（页面标题与面包屑
   自相矛盾即判失败；v3.4.0 实测 ``home`` 曾写成「市场总览」）；
4. **顶栏分组合法**：``group`` 必须命中 index.html 顶栏的 ``data-group``；
5. **侧栏登记合法**：``sidebar`` 必须是 app.js ``SIDEBAR_GROUPS`` 的键；显式为
   ``null`` 的只允许「子页面」（id 以 ``-edit`` 结尾、不出现在侧栏），且每个侧栏
   分组至少有一个视图（防空分组整组消失）。

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
VIEWS_JS = os.path.join(ROOT, "frontend", "js", "views.js")

# V.<id> = {  或  V["<id>"] = {
VIEW_HEAD = re.compile(r'^  V(?:\["([\w-]+)"\]|\.([\w-]+))\s*=\s*\{', re.M)
SIDEBAR_BLOCK = re.compile(r"var SIDEBAR_GROUPS = \[(.*?)\];", re.S)
SIDEBAR_KEY = re.compile(r'\[\s*"([\w-]+)"\s*,')
TOPBAR_GROUP = re.compile(r'<button[^>]*data-group="([\w-]+)"')
FIELD = {
    "title": re.compile(r'\btitle\s*:\s*("(?:[^"\\]|\\.)*")'),
    "group": re.compile(r'\bgroup\s*:\s*("(?:[^"\\]|\\.)*")'),
    "sidebar": re.compile(r'\bsidebar\s*:\s*("(?:[^"\\]|\\.)*"|null)'),
    "crumb": re.compile(r'\bcrumb\s*:\s*(\[[^\]]*\])'),
}


def read(path: str) -> str:
    """读取 UTF-8 文本。

    Args:
        path: 文件绝对路径。

    Returns:
        str: 文件全文。
    """
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def brace_span(src: str, start: int) -> int:
    """从 ``start`` 处的 ``{`` 起做花括号配对，返回配对右括号下标。

    Args:
        src: 全文。
        start: 左花括号下标。

    Returns:
        int: 配对右花括号下标。
    """
    depth = 0
    index = start
    while index < len(src):
        char = src[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return len(src) - 1


def parse_views(src: str) -> list:
    """解析 views.js 的 V.<id> 条目（只取配置头，不含 render 体）。

    Args:
        src: views.js 全文。

    Returns:
        list: 每项为 ``{id, title, group, sidebar, crumb}`` 的原始字面量字典。
    """
    out = []
    for match in VIEW_HEAD.finditer(src):
        vid = match.group(1) or match.group(2)
        start = src.index("{", match.end() - 1)
        body = src[start:brace_span(src, start) + 1]
        # 配置头：截到 api/render 之前，避免误抓 render 内的同名字段
        head = re.split(r"\bapi\s*:|\brender\s*:|\bchartKey\s*:", body)[0]
        item = {"id": vid, "raw": {}}
        for key, pattern in FIELD.items():
            hit = pattern.search(head)
            item["raw"][key] = hit.group(1) if hit else None
        out.append(item)
    return out


def literal(text):
    """把 JS 字面量（字符串 / 数组 / null）转 Python 值；失败返回 None。

    Args:
        text: JS 字面量文本。

    Returns:
        object: 解析结果。
    """
    if not text or text == "null":
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def main() -> None:
    """入口：解析三处配置并逐项判定。"""
    parser = argparse.ArgumentParser(description="检查导航单一配置完整性")
    parser.add_argument("--json", help="结果 JSON 输出路径")
    args = parser.parse_args()

    entries = parse_views(read(VIEWS_JS))
    sidebar_hit = SIDEBAR_BLOCK.search(read(APP_JS))
    sidebar_groups = SIDEBAR_KEY.findall(sidebar_hit.group(1)) if sidebar_hit else []
    topbar_groups = sorted(set(TOPBAR_GROUP.findall(read(INDEX))))

    findings = []
    seen = {}
    parsed = []
    used_groups = set()

    for item in entries:
        vid = item["id"]
        title = literal(item["raw"]["title"])
        group = literal(item["raw"]["group"])
        crumb = literal(item["raw"]["crumb"])
        sidebar_raw = item["raw"]["sidebar"]
        sidebar = None if sidebar_raw == "null" else literal(sidebar_raw)

        seen[vid] = seen.get(vid, 0) + 1
        if seen[vid] > 1:
            findings.append(f"视图 id 重复定义：{vid}（对象重复键会静默覆盖前者）")

        if not title:
            findings.append(f"{vid}: title 缺失或为空")
        if not crumb:
            findings.append(f"{vid}: crumb 缺失或为空数组")
        elif title and crumb[-1] != title:
            findings.append(f"{vid}: crumb 末项「{crumb[-1]}」≠ title「{title}」")
        if group not in topbar_groups:
            findings.append(f"{vid}: group「{group}」不在顶栏 data-group {topbar_groups}")

        if sidebar_raw is None:
            findings.append(f"{vid}: 未声明 sidebar（必须显式写明分组或 null）")
        elif sidebar_raw == "null":
            if not vid.endswith("-edit"):
                findings.append(f"{vid}: sidebar=null 但非子页面（仅 -edit 允许不出现在侧栏）")
        elif sidebar not in sidebar_groups:
            findings.append(f"{vid}: sidebar「{sidebar}」不在 SIDEBAR_GROUPS {sidebar_groups}")
        else:
            used_groups.add(sidebar)

        parsed.append({"id": vid, "title": title, "group": group,
                       "sidebar": sidebar_raw, "crumb": crumb})

    for group in sidebar_groups:
        if group not in used_groups:
            findings.append(f"侧栏分组「{group}」没有任何视图（整组会消失）")

    payload = {
        "views": sorted(seen),
        "view_count": len(seen),
        "sidebar_groups": sidebar_groups,
        "topbar_groups": topbar_groups,
        "entries": parsed,
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
    sub_pages = [p["id"] for p in parsed if p["sidebar"] == "null"]
    print(
        f"OK  导航单一配置 {len(seen)} 视图（V6_VIEWS）/ 侧栏 {len(sidebar_groups)} 组全覆盖 / "
        f"顶栏 {len(topbar_groups)} 组 / 子页面 {len(sub_pages)} 个"
        f"（{('/'.join(sub_pages)) or '无'}）/ 面包屑与标题一致 / 重复定义 0"
    )


if __name__ == "__main__":
    main()
