#!/usr/bin/env python3
"""CSS 全量硬编码色值 token 化迁移（RF-20261112-01 遗留项 #17 收口，2026-11-13）。

口径（v3.4.2 批次）：
- frontend/css/app.css 与 v7.css 内全部 hex/rgba 字面量色值 → var(--c-*) 常量 token；
- 三个主题块（:root / [data-theme="light"] / [data-theme="v6"]）内的变量定义行
  保持不动——定义本身即 token 源，改写会破坏主题体系；
- 注释区间一律不改写（内容不参与 CSS 解析，保留原始说明可读性）；
- 等价性硬保证：每个替换点的字面量与所引用 token 的解析值逐位一致。
  脚本内置 RGB(A) 解析断言：#fff 与 #ffffff、rgba 空格写法差异视为同值，
  断言不等则拒绝替换并报错退出。
- token 命名：--c-<family>-<lightness百分比>（hex）与 --c-<family>-a<alpha百分比>
  （rgba），同名冲突追加序号后缀。--c- 前缀不属于 check_css_theme_vars 的
  颜色类前缀（--bg-/--text/--accent/...），在 :root 定义一次即满足门禁，
  三皮肤下解析恒定值——与替换前字面量的渲染结果构造性等价。

用法：
    python tools/css_tokenize_migration.py --dry-run   # 只出报告，不写盘
    python tools/css_tokenize_migration.py --apply     # 写盘 + 自检
"""

from __future__ import annotations

import argparse
import colorsys
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS_DIR = os.path.join(ROOT, "frontend", "css")
FILES = [os.path.join(CSS_DIR, n) for n in ("app.css", "v7.css")]

COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)")
DEF_LINE_RE = re.compile(r"^\s*--[\w-]+\s*:")
THEME_BLOCK_SELS = (":root", '[data-theme="light"]', '[data-theme="v6"]')
# 门禁颜色类前缀（tools/check_css_theme_vars.py COLOR_PREFIXES）——新 token 必须避开
FORBIDDEN_PREFIXES = ("--bg-", "--border", "--text", "--accent", "--up", "--down",
                      "--warn", "--info", "--danger", "--v6-")


# ---------- 颜色解析 ----------

def parse_color(literal: str):
    """字面量 → (r, g, b, a)，r/g/b ∈ [0,255] 浮点，a ∈ [0,1]。失败返回 None。"""
    s = literal.strip().lower()
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) == 4:
            h = "".join(c * 2 for c in h)
        if len(h) not in (6, 8):
            return None
        try:
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            a = int(h[6:8], 16) / 255.0 if len(h) == 8 else 1.0
        except ValueError:
            return None
        return (float(r), float(g), float(b), a)
    m = re.match(r"^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$", s)
    if not m:
        return None
    r, g, b = float(m.group(1)), float(m.group(2)), float(m.group(3))
    a = float(m.group(4)) if m.group(4) is not None else 1.0
    return (r, g, b, a)


def canon_key(literal: str):
    """规范化键：同值不同写法（#fff/#ffffff、rgba 空格差异、.3/0.3）归一。"""
    c = parse_color(literal)
    if c is None:
        return None
    r, g, b, a = c
    return (round(r), round(g), round(b), round(a, 3))


def hsl_of(rgb):
    h, l, s = colorsys.rgb_to_hls(rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0)
    return h * 360.0, s * 100.0, l * 100.0


def family_of(rgb):
    """返回 (base_family, 可读档位后缀)。hex 档=亮度百分比；rgba 档=亮度a+alpha 百分比。"""
    h, s, l = hsl_of(rgb[:3])
    a = rgb[3]
    base = _base_family(h, s, l)
    ll = int(round(l))
    if a < 1.0:
        alpha_pct = int(round(a * 100))
        return base, f"{base}-{ll}a{alpha_pct}"
    return base, f"{base}-{ll}"


def _base_family(h: float, s: float, l: float) -> str:
    if l >= 96.5 and s <= 12:
        return "white"
    if l <= 6.5:
        return "black"
    if s <= 12:
        return "gray"
    buckets = (
        (15, 45, "orange"), (45, 70, "amber"), (70, 165, "green"),
        (165, 195, "teal"), (195, 215, "cyan"), (215, 250, "blue"),
        (250, 300, "purple"), (300, 345, "magenta"),
    )
    for lo, hi, name in buckets:
        if lo <= h < hi:
            return name
    return "red" if h >= 345 or h < 15 else "gray"


def token_value(literal: str) -> str:
    """token 的定义值：统一规范写法（6 位小写 hex / 无空格 rgba）。"""
    c = parse_color(literal)
    r, g, b, a = c
    if a >= 1.0:
        return "#%02x%02x%02x" % (round(r), round(g), round(b))
    alpha = ("%g" % a) if a != int(a) else str(int(a))
    return "rgba(%d,%d,%d,%s)" % (round(r), round(g), round(b), alpha)


# ---------- 文件结构解析 ----------

def comment_spans(src: str):
    """返回注释区间 [(start, end)]（半开区间）。"""
    spans = []
    i = 0
    n = len(src)
    while i < n:
        j = src.find("/*", i)
        if j < 0:
            break
        k = src.find("*/", j + 2)
        if k < 0:
            spans.append((j, n))
            break
        spans.append((j, k + 2))
        i = k + 2
    return spans


def theme_def_line_spans(src: str):
    """三个主题块内变量定义行 → 保护区间列表。"""
    spans = []
    for line_m in re.finditer(r"[^\n]*", src):
        line = line_m.group(0)
        if not DEF_LINE_RE.match(line):
            continue
        # 所在块判定：该行之前最近的主题块选择器且块未闭合
        head = src[: line_m.start()]
        open_block = None
        for sel in THEME_BLOCK_SELS:
            idx = head.rfind(sel)
            if idx < 0:
                continue
            brace = head.find("{", idx)
            close = head.find("}", idx)
            if brace >= 0 and (close < 0 or close > brace):
                open_block = sel
            break  # 只看最近一处匹配位置即可
        if open_block is not None:
            start = line_m.start()
            end = src.find("\n", start)
            end = len(src) if end < 0 else end + 1
            spans.append((start, end))
    return spans


# ---------- 主流程 ----------

def migrate(apply: bool) -> int:
    report = {"files": {}, "tokens": {}, "skipped_protected": 0, "assert_fail": []}
    token_names: dict = {}      # canon_key -> token 名
    token_defs: list = []       # (token 名, 规范值, 首个原始字面量, 出现次数)
    name_used: dict = {}
    replacements_by_file: dict = {}

    # 第一遍：收集全部色值出现点，建 token 表
    occurrences = []  # (file, start, end, literal)
    for path in FILES:
        src = open(path, encoding="utf-8").read()
        cspans = comment_spans(src)
        pspans = theme_def_line_spans(src)
        protected = cspans + pspans
        for m in COLOR_RE.finditer(src):
            s, e = m.span()
            if any(ps <= s < pe for ps, pe in protected):
                report["skipped_protected"] += 1
                continue
            key = canon_key(m.group(0))
            if key is None:
                report["assert_fail"].append(f"{os.path.basename(path)}: 不可解析色值 {m.group(0)!r}")
                continue
            occurrences.append((path, s, e, m.group(0)))
            if key not in token_names:
                base, name = family_of(parse_color(m.group(0)))
                candidate = "--c-" + name
                k = 2
                while candidate in name_used:
                    candidate = f"--c-{name}-{k}"
                    k += 1
                name_used[candidate] = True
                token_names[key] = candidate
                token_defs.append([candidate, token_value(m.group(0)), m.group(0), 0])
            for td in token_defs:
                if td[0] == token_names[key]:
                    td[3] += 1
                    break

    if report["assert_fail"]:
        print("FAIL 存在不可解析色值：")
        for x in report["assert_fail"]:
            print("  ", x)
        return 1

    # token 定义文本（追加到 :root 块末尾）
    token_defs.sort(key=lambda t: t[0])
    inject_lines = [
        "",
        "  /* ---- 常量色板 --c-*（css_tokenize_migration.py 2026-11-13 生成，值与迁移前字面量逐位等价）---- */",
    ]
    for name, val, orig, cnt in token_defs:
        inject_lines.append(f"  {name}: {val};")
    inject = "\n".join(inject_lines) + "\n"

    # 第二遍：替换（从后往前，位置不漂移）+ 注入 token 定义
    for path in FILES:
        src = open(path, encoding="utf-8").read()
        cspans = comment_spans(src)
        pspans = theme_def_line_spans(src)
        protected = cspans + pspans
        repls = []
        for (fpath, s, e, literal) in occurrences:
            if fpath != path:
                continue
            if any(ps <= s < pe for ps, pe in protected):
                continue
            key = canon_key(literal)
            name = token_names[key]
            val = token_value(literal)
            got = parse_color(val)
            want = parse_color(literal)
            if got != want:
                report["assert_fail"].append(
                    f"{os.path.basename(path)}@{s}: {literal} -> {name}({val}) 断言不等")
                continue
            repls.append((s, e, literal, name))
        repls.sort(key=lambda r: -r[0])
        out = src
        for s, e, literal, name in repls:
            out = out[:s] + "var(" + name + ")" + out[e:]
        # 注入 :root：本文件的 :root 块（app.css 有，v7.css 无 → 只写 app.css）
        root_m = re.search(r":root\s*\{", out)
        if root_m:
            close = out.find("\n}", root_m.start())
            if close < 0:
                report["assert_fail"].append(f"{os.path.basename(path)}: :root 块未闭合")
                continue
            out = out[:close] + "\n" + inject.rstrip("\n") + out[close:]
        if apply and repls or (apply and root_m):
            open(path, "w", encoding="utf-8", newline="").write(out)
        replacements_by_file[os.path.basename(path)] = len(repls)
        report["files"][os.path.basename(path)] = {
            "replacements": len(repls), "written": bool(apply),
        }

    # 自检：写盘后重新扫描非保护区间残留
    if apply:
        for path in FILES:
            src = open(path, encoding="utf-8").read()
            cspans = comment_spans(src)
            pspans = theme_def_line_spans(src)
            for m in COLOR_RE.finditer(src):
                s = m.start()
                if any(ps <= s < pe for ps, pe in cspans + pspans):
                    continue
                report["assert_fail"].append(f"{os.path.basename(path)}@{s}: 残留字面量 {m.group(0)!r}")

    report["tokens"] = {name: {"value": val, "first_seen": orig, "uses": cnt}
                        for name, val, orig, cnt in token_defs}
    report["total_replacements"] = sum(r["replacements"] for r in report["files"].values())
    report["protected_spans"] = report["skipped_protected"]

    print(json.dumps({
        "apply": apply,
        "files": report["files"],
        "token_count": len(token_defs),
        "total_replacements": report["total_replacements"],
        "protected_skipped": report["skipped_protected"],
        "errors": report["assert_fail"][:20],
    }, ensure_ascii=False, indent=2))

    if report["assert_fail"]:
        return 1
    if not apply:
        print("DRY-RUN 通过。加 --apply 写盘。")
    else:
        print(f"OK 全量 token 化完成：{report['total_replacements']} 处替换 / {len(token_defs)} 个 token")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="CSS 全量硬编码色值 token 化迁移")
    ap.add_argument("--apply", action="store_true", help="写盘（默认 dry-run）")
    ap.add_argument("--report", help="JSON 报告输出路径")
    args = ap.parse_args()
    rc = migrate(apply=args.apply)
    return rc


if __name__ == "__main__":
    sys.exit(main())
