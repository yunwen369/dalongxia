#!/usr/bin/env python3
"""静态检查前端 CSS 主题变量完整性（REQ-20261109-01 AR-25）。

背景（openbb-review AR-04/C1，2026-11-09）：``theme.css`` 曾未定义
``--bg-widget-2``，而 ``pages.css`` 6 处写作 ``var(--bg-widget-2, #1a1a1e)``，
变量缺失使任何主题都回退到近黑底色；文字色却跟随主题变化，浅色主题下
正文与深底叠加对比度仅 1.05:1（黑底黑字）。本脚本把「变量必须先定义
再引用」固化为静态校验，防止 fallback 再度兜住主题色。

判定：
1. 颜色类变量（前缀 ``--bg-`` / ``--border`` / ``--text`` / ``--accent`` /
   ``--up`` / ``--down`` / ``--warn`` / ``--info`` / ``--danger``）必须同时
   出现在 ``theme.css`` 的 ``:root``（深色基准）与 ``[data-theme="light"]``
   （砚）两块 —— 只定义一块意味着另一主题取值回落，正是 AR-04 根因。
2. 非颜色类变量（字号 ``--fs-*`` / 布局尺寸 / ``--font*`` / ``--radius*``）
   只需 ``:root`` 或任一主题块定义一次（两主题共享）。
3. ``--bg-*`` 引用不得携带十六进制色 fallback —— fallback 会把未定义缺陷
   静默变成硬编码底色。
4. JS 动态注入变量在 ``JS_INJECTED`` 白名单登记（如 ``--card-span`` 由
   ``grid.js`` 布局引擎按卡片宽度运行时写入），跳过 CSS 侧定义检查。

任一违规 → 退出码 1；全绿输出统计行。

用法：
    python tools/check_css_theme_vars.py [--json <输出路径>]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Dict, List, Set

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONT = os.path.join(ROOT, "frontend")
THEME = os.path.join(FRONT, "css", "theme.css")

# JS 运行时注入的变量（不在 theme.css 定义属正常）；来源注明便于复核
JS_INJECTED: Set[str] = {
    "--card-span",  # core/grid.py 布局引擎 grid.js setProperty，按卡片跨列数写入
}

# 匹配一个主题块：选择器 { ... }（块内不含大括号，与 theme.css 现状一致）
BLOCK_RE = re.compile(r"(?P<sel>:root|\[data-theme=\"light\"\])\s*\{(?P<body>[^{}]*)\}")
# 匹配块内变量定义：--name: 值;
DEF_RE = re.compile(r"(--[\w-]+)\s*:")
# 匹配 var() 引用（含可选 fallback）：var(--name[, fallback])
USE_RE = re.compile(r"var\(\s*(--[\w-]+)\s*(,\s*[^)]+)?\)")
# 颜色类变量前缀：两主题块必须齐备
COLOR_PREFIXES = ("--bg-", "--border", "--text", "--accent", "--up", "--down", "--warn", "--info", "--danger")


def _is_color_var(name: str) -> bool:
    """判定是否颜色类变量（需两主题块齐备）。"""
    return name.startswith(COLOR_PREFIXES)


def _load_theme_blocks() -> Dict[str, Set[str]]:
    """解析 theme.css，返回 {主题块选择器: 该块定义的变量名集合}。"""
    with open(THEME, encoding="utf-8") as handle:
        src = handle.read()
    blocks: Dict[str, Set[str]] = {}
    for match in BLOCK_RE.finditer(src):
        blocks[match.group("sel")] = set(DEF_RE.findall(match.group("body")))
    return blocks


def _iter_css_files() -> List[str]:
    """收集 frontend/css 下全部样式表。"""
    css_dir = os.path.join(FRONT, "css")
    return sorted(
        os.path.join(css_dir, name)
        for name in os.listdir(css_dir)
        if name.endswith(".css")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 CSS 主题变量定义完整性")
    parser.add_argument("--json", help="结果 JSON 输出路径")
    args = parser.parse_args()

    blocks = _load_theme_blocks()
    root_vars = blocks.get(":root", set())
    light_vars = blocks.get("[data-theme=\"light\"]", set())

    used: Dict[str, List[str]] = {}   # 变量名 -> 引用位置列表
    bg_fallback: List[str] = []       # --bg-* 带色值 fallback 的违规行
    for path in _iter_css_files():
        rel = os.path.relpath(path, ROOT)
        with open(path, encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, 1):
                for match in USE_RE.finditer(line):
                    name = match.group(1)
                    used.setdefault(name, []).append(f"{rel}:{line_no}")
                    fallback = match.group(2)
                    # 违规判定 ③：背景类变量携带十六进制色 fallback
                    if fallback and name.startswith("--bg-") and re.search(r"#[0-9a-fA-F]{3,8}", fallback):
                        bg_fallback.append(f"{rel}:{line_no} {name}{fallback.strip()}")

    # 违规判定 ①/②：按变量类别检查定义完整性
    color_violations: List[str] = []      # 颜色类：两块不齐
    plain_violations: List[str] = []      # 非颜色类：任何块都未定义
    for name in sorted(used):
        if name in JS_INJECTED:
            continue
        if _is_color_var(name):
            if name not in root_vars or name not in light_vars:
                where = []
                if name not in root_vars:
                    where.append(":root")
                if name not in light_vars:
                    where.append("[data-theme=light]")
                color_violations.append(f"{name} 缺定义于 {'/'.join(where)}")
        else:
            if name not in root_vars and name not in light_vars:
                plain_violations.append(name)

    result = {
        "root_vars": len(root_vars),
        "light_vars": len(light_vars),
        "used_vars": len(used),
        "js_injected_whitelist": sorted(JS_INJECTED),
        "color_violations": color_violations,
        "plain_violations": plain_violations,
        "bg_color_fallbacks": bg_fallback,
    }
    ok = not color_violations and not plain_violations and not bg_fallback
    result["pass"] = ok

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)

    if ok:
        print(
            f"OK  引用变量 {len(used)} / :root {len(root_vars)} 条 / "
            f"light {len(light_vars)} 条 / 颜色类两块齐备 / 非颜色类已定义 / 背景 fallback 0"
        )
        return 0
    print("FAIL CSS 主题变量校验未通过：")
    for item in color_violations:
        print(f"  颜色类两块不齐: {item}（首处引用 {used[item.split()[0]][0]}）")
    for name in plain_violations:
        print(f"  未定义: {name}（首处引用 {used[name][0]}）")
    for item in bg_fallback:
        print(f"  背景色硬编码 fallback: {item}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
