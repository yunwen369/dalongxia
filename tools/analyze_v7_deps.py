# -*- coding: utf-8 -*-
"""v7_app.js 分节依赖矩阵分析——RF-20261112-01 阶段 4 切割前置。

输出：
1. 每节定义的顶层符号（function/var 声明）
2. 共享原语候选（被 >=MIN_SHARES 节引用的符号）
3. 按拟定域分组后的跨域调用清单（域 A 引用了域 B 定义的符号）

只读分析，不改文件。用法：
  python tools/analyze_v7_deps.py [--min-shares 3]
"""
import io
import re
import sys
from collections import defaultdict

SRC = r"F:\workbuddy记录\OpenBB Terminal Pro\A_运行资产\openbb-cn\frontend\js\v7_app.js"

# 拟定域：节号 -> 域名
DOMAINS = {
    "shell":   [1, 2, 3, 4, 35],
    "topbar":  [5, 6, 7],
    "ai":      [8, 33, 34],
    "forms":   [9, 13, 15, 16, 20],
    "detail":  [10, 18, 24],
    "portfolio": [17, 21, 22, 30],
    "fund":    [23, 26, 27],
    "data":    [25, 28, 29, 32],
    "misc":    [11, 12, 14, 19],
}

SEC_RE = re.compile(r"^  /\* ---------- (\d+)\. (.+?) ----------")
DEF_RE = re.compile(r"^  (?:function (\w+)|var (\w+))")


def main():
    min_shares = 3
    if "--min-shares" in sys.argv:
        min_shares = int(sys.argv[sys.argv.index("--min-shares") + 1])

    lines = io.open(SRC, encoding="utf-8").read().splitlines()
    total = len(lines)

    # 1) 切节
    sections = []  # (sec_no, title, start_line_idx, end_line_idx)
    for i, ln in enumerate(lines):
        m = SEC_RE.match(ln)
        if m:
            sections.append([int(m.group(1)), m.group(2), i, None])
    init_start = None
    for i, ln in enumerate(lines):
        if "---------- 初始化 ----------" in ln:
            init_start = i
            break
    for j in range(len(sections) - 1):
        sections[j][3] = sections[j + 1][2]
    sections[-1][3] = init_start if init_start else total
    # 头部（1..第一个节之前）
    sections.insert(0, [0, "HEADER", 0, sections[0][2]])
    if init_start:
        sections.append([99, "INIT", init_start, total])

    sec_of_line = {}
    for no, title, s, e in sections:
        for i in range(s, e):
            sec_of_line[i] = no

    # 2) 每节定义的符号
    defined = defaultdict(set)      # sec -> {names}
    for no, title, s, e in sections:
        for i in range(s, e):
            m = DEF_RE.match(lines[i])
            if m:
                defined[no].add(m.group(1) or m.group(2))

    all_defined = set()
    for s in defined.values():
        all_defined |= s

    # 3) 符号 -> 引用节集合
    refs = defaultdict(set)         # name -> {secs referencing it}
    for i, ln in enumerate(lines):
        sec = sec_of_line.get(i)
        for name in all_defined:
            if name in ln:
                refs[name].add(sec)

    # 共享原语：被 >= min_shares 个节引用
    shared = sorted(
        [(len(v - {k}), k, sorted(v)) for k, v in refs.items() if len(v) >= min_shares],
        reverse=True)

    print("== 节定义符号数 ==")
    for no, title, s, e in sections:
        print(f"  §{no:<3} {title[:26]:<28} lines {s+1}-{e}  defs={len(defined[no])}")

    print(f"\n== 共享原语候选（被 >= {min_shares} 节引用，按跨节引用数降序）==")
    for cross, name, secs in shared[:40]:
        print(f"  {name:<24} cross={cross:<3} secs={secs}")

    # 4) 域归属
    sec2dom = {}
    for dom, nos in DOMAINS.items():
        for n in nos:
            sec2dom[n] = dom
    sec2dom[0] = "HEADER"
    sec2dom[99] = "INIT"

    # 5) 跨域调用：域 A 的某节引用了只在域 B 定义的符号（且该符号非共享原语）
    shared_names = {name for _, name, _ in shared}
    cross_dom = defaultdict(set)    # (domA, domB) -> {names}
    for name in all_defined:
        if name in shared_names:
            continue
        doms_def = {sec2dom[s] for s in refs[name] if s in sec2dom and s in defined and name in defined[s]}
        if not doms_def:
            continue
        dom_def = list(doms_def)[0]
        for i, ln in enumerate(lines):
            sec = sec_of_line.get(i)
            if sec not in sec2dom or name not in ln:
                continue
            dom_use = sec2dom[sec]
            if dom_use != dom_def and sec not in defined or (dom_use != dom_def and name not in defined[sec]):
                cross_dom[(dom_use, dom_def)].add(name)

    print("\n== 跨域依赖（使用域 -> 定义域: 符号）==")
    for (du, dd), names in sorted(cross_dom.items()):
        print(f"  {du:<10} -> {dd:<10} ({len(names)}): {sorted(names)[:12]}{' ...' if len(names) > 12 else ''}")

    # 6) 每域行数
    print("\n== 域行数 ==")
    dom_lines = defaultdict(int)
    for no, title, s, e in sections:
        dom_lines[sec2dom.get(no, "?")] += e - s
    for dom, n in sorted(dom_lines.items(), key=lambda x: -x[1]):
        print(f"  {dom:<10} {n}")


if __name__ == "__main__":
    main()
