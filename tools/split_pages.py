# -*- coding: utf-8 -*-
"""pages.js 按页拆分（R17 可读性债消除）——机械切片，零逻辑改动。

设计基准依据：DS-R10-v2.1 §8 R17「pages.js（9474 行）按页分模块即自然消解；
`tools/envelope_baseline.py` 同款 AST 去重扫描入 CI」；验收断言「单文件 ≤800 行」。

规则
----
1. 按原文件的分区注释与顶层语句边界切片，切点必须是顶层语句边界（`  // ` / `  function ` /
   `  var ` / 空行），否则报错退出，绝不硬切函数体；
2. 每模块独立 IIFE，参数 `P = window.Pages`；顶部注入本模块用到的外部符号
   （`var buildCard = P.buildCard;` 形式，ES5 兼容），尾部把本模块定义的符号导出到 `P`；
3. 跨模块**函数调用**改写为 `P.名(`（运行时解析，无加载顺序依赖）；跨模块**变量**用顶部注入，
   其中 `charts` 在 core 内一律以 `P.charts` 访问（因 disposeCharts 会整体重置该数组）；
4. `core.js` 必须最先加载，`init.js` 必须最后加载（内含 init 与启动调用）。

用法
----
    python tools/split_pages.py --check     # 只校验切片边界与符号表，不写文件
    python tools/split_pages.py --apply     # 生成 frontend/js/pages/*.js
"""
from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "frontend", "js", "pages.js")
OUTDIR = os.path.join(ROOT, "frontend", "js", "pages")

# (模块名, [(起, 止), ...] 1-based 含端点, 说明)
# 切点全部落在「顶层函数定义行/顶层分区注释行」，由 boundary_ok 强校验；
# 分段而非连续区间的原因：历史追加导致个股/组合/新闻/设置在文件内交错。
MODULES = [
    ("core", [(18, 570)], "常量 · 通用工具 · 四态与徽章 · 表格渲染 · 路由"),
    ("sectors", [(571, 1066)], "板块分析 S-01~S-07"),
    ("stock_a", [(1067, 1296)], "个股页骨架与交互（ST-01 外壳/自选/K线 Tab）"),
    ("combo", [(1297, 1726)], "基金组合 M5/M13/M14（诊断 · 评估 · 候选池 · 权重明细）"),
    ("crowding", [(1727, 1901)], "拥挤度 M3/M12（查询 + 全板块排行）"),
    ("stock_b", [(1902, 2424)], "个股数据渲染（报价/K线/资金分布/新闻/舆情/一致预期）"),
    ("stock_c", [(2425, 2889)], "个股深度渲染（机构预测/概况/财务/股东）"),
    ("sectorval", [(2890, 3145)], "单板块估值 V-01~V-04"),
    ("news", [(3146, 3188), (3688, 4216)], "新闻与利好利空（外壳/板块树/资讯流/情绪）"),
    ("fins", [(3189, 3687)], "财报/公告搜索 FS-01~FS-09"),
    ("fund", [(4217, 4702)], "场外基金详情"),
    ("portfolio", [(4703, 5315)], "持仓管理 · 组合风险 M10 · xlsx 导入 M11"),
    ("connectors", [(5316, 5659)], "数据连接器"),
    ("yjb", [(5660, 6378)], "养基宝绑定"),
    ("charting", [(6379, 6985)], "图表分析"),
    ("settings", [(6986, 7496), (7835, 7977)], "设置（AI/外观/缓存/关于/启动关闭/养基宝）"),
    ("macro", [(7497, 7662)], "宏观与周期 M1"),
    ("valuation", [(7663, 7834)], "估值分位 M2"),
    ("watchlist", [(8000, 8723)], "自选股 W-01/W-02 与龙虎榜·北向 L-01~L-05"),
    ("screener", [(8724, 9064)], "全市场筛选器 M9"),
    ("alerts", [(9065, 9439)], "提醒中心 M6"),
    ("init", [(7978, 7999), (9440, 9473)], "初始化（bindResize · 铃铛轮询 · 路由启动）"),
]
HEADER_END = 14          # 文件头注释 1..14 归 core
IIFE_OPEN_LINE = 16      # `(function () {`
USE_STRICT_LINE = 17

DEF_FUNC = re.compile(r"^  function ([A-Za-z_$][\w$]*)\(")
DEF_VAR = re.compile(r"^  var ([A-Za-z_$][\w$]*)")


def boundary_ok(lines: list[str], lineno: int) -> bool:
    """判断某行能否作为模块起始切点（顶层语句边界）。"""
    if lineno == 1:
        return True
    text = lines[lineno - 1]
    return (
        text.startswith("  // ")
        or text.startswith("  function ")
        or text.startswith("  var ")
        or text.startswith("  /*")
        or text.startswith("  /**")
        or text.strip() == ""
        or text.strip() == "})();"  # 原文件 IIFE 收尾，仅出现在文件末
    )


def collect_symbols(lines: list[str]) -> tuple[list[str], list[str]]:
    """返回 (顶层函数名列表, 顶层变量名列表)。"""
    funcs, vars_ = [], []
    for line in lines:
        m = DEF_FUNC.match(line)
        if m:
            funcs.append(m.group(1))
            continue
        m = DEF_VAR.match(line)
        if m:
            vars_.append(m.group(1))
    return funcs, vars_


def build_body(lines: list[str], start: int, end: int) -> list[str]:
    """取 [start,end] 行区间，去掉首尾空行。"""
    seg = lines[start - 1:end]
    while seg and not seg[0].strip():
        seg.pop(0)
    while seg and not seg[-1].strip():
        seg.pop()
    return seg


def inject_block(body: list[str], own: set[str], all_syms: set[str]) -> tuple[list[str], set[str]]:
    """生成外部依赖注入行，返回 (注入行, 本模块实际引用的外部符号集合)。"""
    text = "\n".join(body)
    # 去掉注释后判断引用，减少误注入
    stripped = re.sub(r"//[^\n]*", "", text)
    needed: set[str] = set()
    for name in all_syms:
        if name in own:
            continue
        if re.search(r"(?<![\w$.])" + re.escape(name) + r"(?![\w$])", stripped):
            needed.add(name)
    lines = []
    if needed:
        lines.append("  // ---- 跨模块依赖（core/兄弟模块导出，运行时解析） ----")
        for name in sorted(needed):
            lines.append(f"  var {name} = P.{name};")
        lines.append("")
    return lines, needed


def rewrite_calls(body: list[str], own: set[str], all_funcs: set[str]) -> list[str]:
    """把跨模块函数调用改写为 P.名(；函数定义行跳过。"""
    out = []
    for line in body:
        if line.lstrip().startswith("//") or line.lstrip().startswith("*"):
            out.append(line)
            continue
        if re.match(r"^\s*function\s", line):
            out.append(line)
            continue
        def repl(m: re.Match[str]) -> str:
            name = m.group(1)
            if name in own or name not in all_funcs:
                return m.group(0)
            return "P." + name + "("
        out.append(re.sub(r"(?<![\w$.])([A-Za-z_$][\w$]*)\(", repl, line))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not (args.check or args.apply):
        ap.print_help()
        return 2

    lines = open(SRC, encoding="utf-8").read().split("\n")
    total = len(lines)
    all_funcs, all_vars = collect_symbols(lines)
    all_syms = set(all_funcs) | set(all_vars)

    # ---- 边界校验 ----
    errors = []
    for name, rngs, _desc in MODULES:
        for s, e in rngs:
            if not boundary_ok(lines, s):
                errors.append(f"{name} 起点 {s} 非顶层边界: {lines[s-1][:60]!r}")
            if e + 1 <= total and not boundary_ok(lines, e + 1):
                errors.append(f"{name} 终点 {e} 后一行非顶层边界: {lines[e][:60]!r}")

    # ---- 归属校验：每个顶层符号只属于一个模块 ----
    owner: dict[str, str] = {}
    dup: list[str] = []
    for name, rngs, _d in MODULES:
        for s, e in rngs:
            for i in range(s, e + 1):
                m = DEF_FUNC.match(lines[i - 1]) or DEF_VAR.match(lines[i - 1])
                if not m:
                    continue
                sym = m.group(1)
                if sym in owner:
                    dup.append(f"{sym}: {owner[sym]} / {name}")
                owner[sym] = name

    if dup:
        errors.append("符号重复归属: " + "; ".join(dup))
    missing = all_syms - set(owner)
    if missing:
        errors.append("符号未归属任何模块: " + ", ".join(sorted(missing)))

    print(f"源文件 {total} 行；顶层函数 {len(all_funcs)}、顶层变量 {len(all_vars)}")
    for name, rngs, desc in MODULES:
        n_lines = sum(e - s + 1 for s, e in rngs)
        n_own = sum(1 for k, v in owner.items() if v == name)
        rng_txt = "+".join(f"{s}-{e}" for s, e in rngs)
        print(f"  {name:<12} {rng_txt:<14} 行数≈{n_lines:<5} 符号 {n_own:<3} {desc}")
    if errors:
        print("\n!! 校验失败:")
        for e in errors:
            print("  -", e)
        return 1
    print("\n边界与符号归属校验通过")
    if args.check:
        return 0

    # ---- 生成 ----
    os.makedirs(OUTDIR, exist_ok=True)
    header = lines[:HEADER_END]
    generated = []
    for name, rngs, desc in MODULES:
        body: list[str] = []
        for idx, (s, e) in enumerate(rngs):
            if idx:
                body.append("")
            body.extend(build_body(lines, s, e))
        own = {k for k, v in owner.items() if v == name}
        body = rewrite_calls(body, own, set(all_funcs))
        # 原文件末尾 `window.Pages = { reload: route };`（供 app.js 强制重载）会整对象覆盖
        # 已注册的命名空间，拆分后必须改为在现有命名空间上追加，否则先加载模块全丢。
        body = [
            "  P.reload = P.route;  // 原 window.Pages={reload:route}：改为追加，避免覆盖已注册符号"
            if ln.strip() == "window.Pages = { reload: route };" else ln
            for ln in body
        ]
        if name == "core":
            # core 内 charts 一律走 P.charts（disposeCharts 会整体重置该数组）；
            # 声明行 `var charts = []` 改写为 `P.charts = []`（P 为 IIFE 参数，属性赋值合法）
            fixed = []
            for ln in body:
                if ln.startswith("  var charts"):
                    fixed.append(ln.replace("var charts", "P.charts", 1))
                else:
                    fixed.append(re.sub(r"(?<![\w$.])charts(?![\w$])", "P.charts", ln))
            body = fixed

        inject, _needed = inject_block(body, own, all_syms)

        out: list[str] = []
        out.append("/* " + "=" * 61)
        out.append(f" * OpenBB 中文投研工作台 · pages/{name}.js")
        out.append(f" * {desc}")
        out.append(" * 由 tools/split_pages.py 从 pages.js 机械切分生成（R17），逻辑零改动；")
        out.append(" * 重新生成请改 pages.js 后重跑脚本，勿直接编辑本文件的结构性段落。")
        out.append(" * " + "=" * 61 + " */")
        out.append("(function (P) {")
        out.append('  "use strict";')
        out.append("")
        if name != "core":
            out.extend(inject)
        out.extend(body)
        out.append("")
        out.append("  // ---- 导出到 window.Pages（供兄弟模块与 init 使用） ----")
        for sym in sorted(own):
            if name == "core" and sym == "charts":
                continue  # charts 已在 core 内直接以 P.charts 声明与访问，无同名局部变量
            out.append(f"  P.{sym} = {sym};")
        out.append("})(window.Pages = window.Pages || {});")
        out.append("")

        text = "\n".join(out)
        target = os.path.join(OUTDIR, f"{name}.js")
        open(target, "w", encoding="utf-8").write(text)
        generated.append((name, len(out), target))

    print("\n已生成:")
    for name, n, path in generated:
        flag = "  ⚠>800" if n > 800 else ""
        print(f"  {os.path.relpath(path, ROOT):<46} {n:>5} 行{flag}")
    over = [n for n, cnt, _ in generated if cnt > 800]
    if over:
        print("\n!! 超 800 行模块:", ", ".join(over))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
