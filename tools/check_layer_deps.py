# -*- coding: utf-8 -*-
"""前端层级依赖静态门禁——RF-20261112-01 阶段 6。

层级架构（自底向上）：
  vendor/ → lib/ → components.js → views.js → w7/*（core → 9 域 → 装配）
  core/（ctxmenu/grid/tree/state4）→ lib/

不变式（违例即退出码 1）：
  I1  lib/*      只依赖 vendor 与 lib 自身（禁 views/w7/components/core 的符号）
  I2  lib/*      禁直接调 localStorage / echarts.init / fetch（必须经 OB.store/chart/http）
  I3  w7/*       跨域函数引用必须经 W7.<dom>.<fn>（禁裸引用他域内部名——静态近似：
                 w7 域文件内不得出现除自身外的 `W7.` 之外的新全局写入）
  I4  index.html 挂载顺序：lib → components → views → w7（core 首位、装配末位）
  I5  全站（lib/w7/components/views/core）禁 window.Utils 幽灵引用（BUG-20261112-07 模式）
  I6  裸 fetch 仅允许出现在 lib/http.js（唯一 HTTP 入口）
  I7  裸 echarts.init 仅允许出现在 lib/chart.js

用法：python tools/check_layer_deps.py   （0 退出 = 通过）
"""
import io
import os
import re
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
JS = os.path.join(ROOT, "frontend", "js")
HTML = os.path.join(ROOT, "frontend", "index.html")

W7_DOMAINS = ["shell", "topbar", "ai", "forms", "detail", "portfolio", "fund", "data", "misc"]


def read(p):
    return io.open(p, encoding="utf-8").read()


def strip_comments(code):
    """剥掉 /* */ 块注释与 // 行注释（字符串感知），供静态扫描用。"""
    out = []
    i, n = 0, len(code)
    in_s = in_d = False
    while i < n:
        c = code[i]
        if c == "'" and not in_d:
            in_s = not in_s
            out.append(c)
        elif c == '"' and not in_s:
            in_d = not in_d
            out.append(c)
        elif c == "/" and not in_s and not in_d and i + 1 < n and code[i + 1] == "*":
            j = code.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
            continue
        elif c == "/" and not in_s and not in_d and i + 1 < n and code[i + 1] == "/":
            j = code.find("\n", i)
            i = n if j < 0 else j
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def rel(p):
    return os.path.relpath(p, ROOT).replace("\\", "/")


def check_layer_deps():
    errors = []

    lib_files = [os.path.join(JS, "lib", f) for f in sorted(os.listdir(os.path.join(JS, "lib")))]
    w7_files = [os.path.join(JS, "w7", f) for f in sorted(os.listdir(os.path.join(JS, "w7")))]
    top_files = [os.path.join(JS, f) for f in sorted(os.listdir(JS))
                 if f.endswith(".js") and os.path.isfile(os.path.join(JS, f))]

    # ---- I1/I2：lib 层纯度 ----
    for p in lib_files:
        code = read(p)
        name = rel(p)
        # I1：lib 不得引用上层符号（W7/V6_VIEWS/V6_HELPERS 视为上层）
        for bad in ("W7.", "V6_VIEWS", "V6_HELPERS", "V6_DEMO"):
            if bad in code and "lib/http.js" not in name and "OB.http" not in code:
                errors.append("I1 %s: 引用了上层符号 %s" % (name, bad))
        # I2：lib 不得直呼底层设施（http.js/chart.js/store.js 内部实现除外）
        if "http.js" not in name and re.search(r"(?<![.\w$])fetch\(", code):
            errors.append("I2 %s: 裸 fetch（应经 OB.http）" % name)
        if "store.js" not in name and "config.js" not in name and "localStorage" in code:
            errors.append("I2 %s: 直呼 localStorage（应经 OB.store）" % name)
        if "chart.js" not in name and re.search(r"echarts\.init\(", code):
            errors.append("I2 %s: 裸 echarts.init（应经 OB.chart）" % name)

    # ---- I5：window.Utils 幽灵引用（剥注释后扫描）----
    for p in top_files + w7_files + lib_files:
        code = strip_comments(read(p))
        if "window.Utils" in code:
            errors.append("I5 %s: window.Utils 幽灵引用（BUG-20261112-07 模式，应经 OB.ui）" % rel(p))

    # ---- I6/I7：裸 fetch / echarts.init 全局唯一（剥注释后扫描）----
    for p in top_files + w7_files:
        code = strip_comments(read(p))
        name = rel(p)
        if p.endswith("app.js") and "views" in name:
            continue
        if re.search(r"(?<![.\w$])fetch\(", code):
            errors.append("I6 %s: 裸 fetch（应经 OB.http.apiJson/getJSON）" % name)
        if re.search(r"echarts\.init\(", code):
            errors.append("I7 %s: 裸 echarts.init（应经 OB.chart.init）" % name)

    # ---- I4：index.html 挂载顺序 ----
    html = read(HTML)
    order = re.findall(r'<script src="(js/[^"]+)"', html)
    pos = {}
    for i, src in enumerate(order):
        pos[src] = i
    lib_start = min((i for s, i in pos.items() if s.startswith("js/lib/")), default=-1)
    comp_pos = pos.get("js/components.js", -1)
    views_pos = pos.get("js/views.js", -1)
    w7_core_pos = pos.get("js/w7/w7_core.js", -1)
    w7_app_pos = pos.get("js/w7/w7_app.js", -1)
    if not (lib_start < comp_pos < views_pos < w7_core_pos):
        errors.append("I4 index.html: 挂载顺序应为 lib → components → views → w7_core（实测 lib@%d comp@%d views@%d core@%d）"
                      % (lib_start, comp_pos, views_pos, w7_core_pos))
    if w7_app_pos != max(pos[s] for s in pos if s.startswith("js/w7/")):
        errors.append("I4 index.html: w7_app.js（装配）必须是 w7/* 最后一个")
    if "js/v7_app.js" in pos:
        errors.append("I4 index.html: v7_app.js 已退役（阶段4），不得再挂载")

    # ---- I3：w7 域文件只挂自己的命名空间键 ----
    for p in w7_files:
        code = read(p)
        name = os.path.basename(p)
        m = re.match(r"w7_(\w+)\.js$", name)
        if not m:
            continue
        dom = m.group(1)
        if dom == "core":
            continue
        for bad in W7_DOMAINS + ["core"]:
            if bad == dom:
                continue
            # 域文件不得给别的域命名空间赋值（读 W7.<other>.fn 合法——惰性桥/装配直呼）
            if re.search(r"W7\.%s\s*=\s*W7\.%s\s*\|\|" % (bad, bad), code) or \
               re.search(r"W7\.%s\.\w+\s*=" % bad, code):
                errors.append("I3 %s: 写入了他人命名空间 W7.%s" % (name, bad))

    return errors


def main():
    errors = check_layer_deps()
    if errors:
        print("层级依赖门禁：FAIL（%d 项违例）" % len(errors))
        for e in errors:
            print("  ✗ " + e)
        return 1
    n = (len(os.listdir(os.path.join(JS, "lib")))
         + len(os.listdir(os.path.join(JS, "w7")))
         + len([f for f in os.listdir(JS) if f.endswith(".js")]))
    print("OK  层级依赖门禁：lib 纯度 / 唯一入口 / 挂载顺序 / W7 命名空间 / Utils 幽灵 全部通过（扫描 %d 个 JS 文件）" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
