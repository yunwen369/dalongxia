#!/usr/bin/env python3
"""静态校验：前端注释与实现是否漂移（REQ-20261109-01 AR-26）。

背景（openbb-review #13 / BUG-AR-15）：审查报告指出两处「注释承诺与实现不符」——
``pages.css`` 注释称「1024 断点」而实际媒体查询是 ``max-width: 1200px``；
``index.html`` 注释称「点左上角 Logo 回首页」而当时并未绑定点击。前者属
「注释数字漂移」，后者属「承诺未实现」，两者都会误导后续维护。

本脚本把三类判据固化为静态闸门（不依赖浏览器）：

1. **断点数字一致**：CSS 注释出现「NNNN 断点」时，其后 5 行内首个
   ``@media (max-width: X)`` 的 X 必须等于 NNNN；HTML/JS 注释出现
   「NNNN 断点」时，全仓 ``frontend/css`` 必须存在该宽度的媒体查询。
2. **编号映射一致**：注释中 ``C11`` 只能与 ``AR-09`` 同现（BUG-AR 清单里
   C11 ↔ AR-09）；出现 ``C11（AR-15）`` 之类即判为编号错位。
3. **承诺可实现**：HTML 注释写「点左上角 Logo 回首页」时，``index.html``
   必须存在 ``id="logo-home"`` 且 ``js/app.js`` 必须绑定 Logo 点击。

任一违规 → 退出码 1；全绿输出统计行。

用法：
    python tools/check_comment_drift.py [--json <输出路径>]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND = os.path.join(ROOT, "frontend")
CSS_DIR = os.path.join(FRONTEND, "css")
JS_DIR = os.path.join(FRONTEND, "js")

BREAKPOINT_COMMENT = re.compile(r"(\d{3,4})\s*断点")
MEDIA_MAX = re.compile(r"@media\s*\(max-width:\s*(\d+)px\)")
CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
JS_LINE_COMMENT = re.compile(r"//[^\n]*")
AR_ID = re.compile(r"AR-(\d{2})")
C11_AR = re.compile(r"C11[（(]\s*AR-(\d{2})\s*[)）]")


def read(path: str) -> str:
    """读取 UTF-8 文本。"""
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def css_files() -> list[str]:
    """列出全部 CSS 文件。"""
    return sorted(os.path.join(CSS_DIR, n) for n in os.listdir(CSS_DIR) if n.endswith(".css"))


def js_files() -> list[str]:
    """列出全部 JS 文件（含子目录）。"""
    out = []
    for base, _dirs, files in os.walk(JS_DIR):
        out.extend(os.path.join(base, n) for n in files if n.endswith(".js"))
    return sorted(out)


def media_widths() -> set[int]:
    """收集全仓 max-width 媒体查询宽度。"""
    widths: set[int] = set()
    for path in css_files():
        widths.update(int(x) for x in MEDIA_MAX.findall(read(path)))
    return widths


def check_css_breakpoints(text: str, path: str, findings: list[str]) -> int:
    """判据 1a：CSS 注释断点数字与紧随其后媒体查询一致。"""
    checked = 0
    lines = text.splitlines()
    for match in CSS_COMMENT.finditer(text):
        comment = match.group(0)
        for num in BREAKPOINT_COMMENT.findall(comment):
            checked += 1
            start_line = text[: match.start()].count("\n")
            window = "\n".join(lines[start_line : start_line + 6])
            widths = MEDIA_MAX.findall(window)
            if widths and int(widths[0]) != int(num):
                findings.append(
                    f"{os.path.relpath(path, ROOT)}:{start_line + 1} 注释称「{num} 断点」"
                    f"但紧随 @media max-width:{widths[0]}px"
                )
            elif not widths and int(num) not in media_widths():
                findings.append(
                    f"{os.path.relpath(path, ROOT)}:{start_line + 1} 注释称「{num} 断点」"
                    f"但全仓无该宽度媒体查询"
                )
    return checked


def check_text_breakpoints(text: str, path: str, findings: list[str], widths: set[int]) -> int:
    """判据 1b：HTML/JS 注释断点数字在全仓存在同名媒体查询。"""
    checked = 0
    blocks = list(HTML_COMMENT.finditer(text)) + list(CSS_COMMENT.finditer(text))
    for match in blocks:
        for num in BREAKPOINT_COMMENT.findall(match.group(0)):
            checked += 1
            if int(num) not in widths:
                line = text[: match.start()].count("\n") + 1
                findings.append(
                    f"{os.path.relpath(path, ROOT)}:{line} 注释称「{num} 断点」但全仓无该宽度媒体查询"
                )
    for match in JS_LINE_COMMENT.finditer(text):
        for num in BREAKPOINT_COMMENT.findall(match.group(0)):
            checked += 1
            if int(num) not in widths:
                line = text[: match.start()].count("\n") + 1
                findings.append(
                    f"{os.path.relpath(path, ROOT)}:{line} 注释称「{num} 断点」但全仓无该宽度媒体查询"
                )
    return checked


def check_ar_ids(text: str, path: str, findings: list[str]) -> int:
    """判据 2/3：AR 编号范围 01–26；C11 只能配 AR-09。"""
    checked = 0
    for match in AR_ID.finditer(text):
        checked += 1
        num = int(match.group(1))
        if not 1 <= num <= 26:
            line = text[: match.start()].count("\n") + 1
            findings.append(f"{os.path.relpath(path, ROOT)}:{line} AR 编号越界（AR-{num:02d}）")
    for match in C11_AR.finditer(text):
        if match.group(1) != "09":
            line = text[: match.start()].count("\n") + 1
            findings.append(
                f"{os.path.relpath(path, ROOT)}:{line} C11 对应 AR-09，实际写作 AR-{match.group(1)}"
            )
    return checked


def check_logo_promise(findings: list[str]) -> int:
    """判据 4：注释承诺「点 Logo 回首页」时，DOM 与绑定必须同时存在。"""
    index = os.path.join(FRONTEND, "index.html")
    app = os.path.join(JS_DIR, "app.js")
    html = read(index)
    checked = 0
    if "点左上角 Logo 回首页" in html:
        checked += 1
        if 'id="logo-home"' not in html:
            findings.append("index.html 注释承诺 Logo 回首页，但缺少 id=\"logo-home\"")
        if "logo-home" not in read(app) and ".topbar .logo" not in read(app):
            findings.append("index.html 注释承诺 Logo 回首页，但 app.js 未绑定点击")
    return checked


def main() -> None:
    """入口。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default=None, help="结果输出 JSON 路径")
    args = parser.parse_args()

    findings: list[str] = []
    widths = media_widths()
    checked_bp = 0
    checked_ids = 0

    for path in css_files():
        text = read(path)
        checked_bp += check_css_breakpoints(text, path, findings)
        checked_ids += check_ar_ids(text, path, findings)

    for path in js_files():
        text = read(path)
        checked_bp += check_text_breakpoints(text, path, findings, widths)
        checked_ids += check_ar_ids(text, path, findings)

    index = os.path.join(FRONTEND, "index.html")
    html = read(index)
    checked_bp += check_text_breakpoints(html, index, findings, widths)
    checked_ids += check_ar_ids(html, index, findings)
    checked_promise = check_logo_promise(findings)

    payload = {
        "checked": {"breakpoints": checked_bp, "ar_ids": checked_ids, "promises": checked_promise},
        "media_widths": sorted(widths),
        "findings": findings,
    }
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

    if findings:
        print(f"FAIL  发现 {len(findings)} 项注释漂移：")
        for item in findings:
            print("  -", item)
        sys.exit(1)
    print(
        f"OK  注释漂移 0 项（断点 {checked_bp} / AR 编号 {checked_ids} / 承诺 {checked_promise}；"
        f"媒体查询宽度 {sorted(widths)}）"
    )


if __name__ == "__main__":
    main()
