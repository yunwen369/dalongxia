# -*- coding: utf-8 -*-
"""v7_app.js 巨石切割器——RF-20261112-01 阶段 4。

原理（行为零变更的关键设计）：
1. 代码体 100% 原样保留，只按节边界切到 9 个域文件；
2. 跨域函数在使用域头部注入【惰性桥】（运行时才解引用 W7.<dom>.<fn>，
   加载顺序无关、无循环依赖问题），调用点零改动；
3. 13 个跨域可变状态量声明行原位改写为 W7.st.<name> 赋值，全部引用点
   （含定义域内）机械替换为 W7.st.<name>；
4. 共享原语（$/toast/apiJson/queryParam + LS_*/DEMO_SEARCH）收进 w7_core，
   各域头部值拷贝；
5. 装配文件 w7_app.js 只含 init()（16 个 bind 调用改 W7.<dom>.<fn>() 直呼）
   + window.V7 对外 API + DOMContentLoaded 闸门。

用法：
  python tools/cut_v7.py            # 切割并写出 frontend/js/w7/*.js
  python tools/cut_v7.py --dry-run  # 只打印统计
"""
import io
import os
import re
import sys
from collections import defaultdict

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SRC = os.path.join(ROOT, "frontend", "js", "v7_app.js")
OUT_DIR = os.path.join(ROOT, "frontend", "js", "w7")

# 节 -> 域
DOMAIN_OF_SEC = {}
for n in (1, 2, 3, 4, 35):
    DOMAIN_OF_SEC[n] = "shell"
for n in (5, 6, 7):
    DOMAIN_OF_SEC[n] = "topbar"
for n in (8, 33, 34):
    DOMAIN_OF_SEC[n] = "ai"
for n in (9, 13, 15, 16, 20):
    DOMAIN_OF_SEC[n] = "forms"
for n in (10, 18, 24):
    DOMAIN_OF_SEC[n] = "detail"
for n in (17, 21, 22, 30):
    DOMAIN_OF_SEC[n] = "portfolio"
for n in (23, 26, 27):
    DOMAIN_OF_SEC[n] = "fund"
for n in (25, 28, 29, 32):
    DOMAIN_OF_SEC[n] = "data"
for n in (11, 12, 14, 19, 31):
    DOMAIN_OF_SEC[n] = "misc"
DOMAIN_OF_SEC[99] = "INIT"

STATE_VARS = [
    "importRows", "importFileName", "importBase64",
    "northMetric", "comboRisk", "comboState",
    "newsInput", "newsSort", "newsRegion",
    "sectorTabState", "sectorMarket", "sectorKind",
    "alertsHintText",
]

# 这两个在 §8 内定义但属纯函数/薄委托 → 抽到 core
MOVE_TO_CORE = ["apiJson", "queryParam"]
# §8 区间内「统一请求」小节 + queryParam 的行范围（切割时从 ai 域剔除）
AI_EXCLUDE_RANGES = []  # 运行时填 (start,end) 0-based 左闭右开

CORE_COPIES = ["$", "toast", "apiJson", "queryParam",
               "LS_LAYOUT", "LS_CUSTOM", "DEMO_SEARCH"]

SEC_RE = re.compile(r"^  /\* ---------- (\d+)\. (.+?) ----------")
FN_RE = re.compile(r"^  function (\w+)")
VAR_RE = re.compile(r"^  var ([A-Za-z_$][\w$]*)")
WS_RE = re.compile(r"\s+")


def strip_code_comment(line):
    """剥掉行内 // 注释（跳过引号内内容），用于引用扫描。"""
    out = []
    in_s = in_d = False
    i = 0
    while i < len(line):
        c = line[i]
        if c == "'" and not in_d:
            in_s = not in_s
        elif c == '"' and not in_s:
            in_d = not in_d
        elif c == "/" and not in_s and not in_d and i + 1 < len(line) and line[i + 1] == "/":
            break
        out.append(c)
        i += 1
    return "".join(out)


def main():
    dry = "--dry-run" in sys.argv
    text = io.open(SRC, encoding="utf-8").read()
    lines = text.splitlines()
    total = len(lines)

    # ---- 切节 ----
    marks = []  # (idx, sec, title)
    for i, ln in enumerate(lines):
        m = SEC_RE.match(ln)
        if m:
            marks.append((i, int(m.group(1)), m.group(2)))
    init_i = next(i for i, ln in enumerate(lines) if "---------- 初始化" in ln)
    bounds = []  # (sec, start, end) 0-based 左闭右开；§0 头部含 IIFE 开壳
    for j, (i, sec, title) in enumerate(marks):
        end = marks[j + 1][0] if j + 1 < len(marks) else init_i
        bounds.append((sec, i, end))
    bounds.append((99, init_i, total))

    def sec_of_line(i):
        for sec, s, e in bounds:
            if s <= i < e:
                return sec
        return None

    # ---- 符号定义收集 ----
    defined_in = defaultdict(set)   # name -> {secs}
    for sec, s, e in bounds:
        for i in range(s, e):
            m = FN_RE.match(lines[i])
            if m:
                defined_in[m.group(1)].add(sec)
            m = VAR_RE.match(lines[i])
            if m and m.group(1) not in STATE_VARS:
                defined_in[m.group(1)].add(sec)

    # apiJson / queryParam 定义节覆盖（它们在 §8，但归属 core）
    core_syms = set(MOVE_TO_CORE) | {"$", "toast", "LS_LAYOUT", "LS_CUSTOM", "DEMO_SEARCH"}

    # ---- 跨域引用扫描（剥注释；节粒度：本节有定义的同名符号不算跨域引用）----
    use_domains = defaultdict(set)  # name -> {domains using it across sections}
    for i, ln in enumerate(lines):
        sec = sec_of_line(i)
        if sec is None:
            continue
        dom = DOMAIN_OF_SEC.get(sec, "core")
        code = strip_code_comment(lines[i])
        if not code.strip():
            continue
        for name in defined_in:
            if name in core_syms or name in STATE_VARS:
                continue
            if sec in defined_in[name]:
                continue  # 本节定义同名符号：节内引用解析到本地定义
            # (?<![.\w$])：排除 obj.init(...) 之类成员访问误报
            if re.search(r"(?<![.\w$])" + re.escape(name) + r"\b", code):
                use_domains[name].add(dom)

    # 定义域（同名多节定义时取包含其引用节的域；否则第一节）
    def def_domain(name):
        doms = {DOMAIN_OF_SEC[s] for s in defined_in[name]}
        using = use_domains[name]
        inter = doms & using
        if inter:
            return sorted(inter)[0]
        return sorted(doms)[0]

    cross_fn = {}   # name -> def_domain（跨域函数）
    for name, using in use_domains.items():
        dd = def_domain(name)
        external = using - {dd}
        if external:
            cross_fn[name] = dd

    # ---- 状态量引用替换（全文件）----
    new_lines = list(lines)
    n_state_refs = 0
    for name in STATE_VARS:
        pat = re.compile(r"\b" + re.escape(name) + r"\b")
        for i, ln in enumerate(new_lines):
            if "---------- 初始化" in ln or SEC_RE.match(ln):
                continue
            new_ln, n = pat.subn("W7.st." + name, ln)
            if n:
                # 声明行改写为 W7.st 赋值
                m = re.match(r"^  var (" + re.escape(name) + r"\s*=\s*.+);\s*$", ln)
                if m:
                    new_ln = "  W7.st." + name.replace("var ", "") + ""
                    new_ln = "  W7.st." + name + " = " + m.group(1).split("=", 1)[1].strip() + ";"
                    n_state_refs += 0
                n_state_refs += n
            new_lines[i] = new_ln
    lines = new_lines

    # ---- §8 内 apiJson/queryParam 小节剔除（归 core）----
    # 精确止步：从「统一请求」注释行开始，吞到下一个【非 apiJson/queryParam 的
    # 顶层 function 声明】为止（防止误吞 §8 的 saveEdit 等业务函数）。
    ai_excl = set()
    for sec, s, e in bounds:
        if sec != 8:
            continue
        started = False
        for i in range(s, e):
            m = FN_RE.match(lines[i])
            if started and m and m.group(1) not in ("apiJson", "queryParam"):
                break
            if "统一请求（401 跳登录" in lines[i]:
                started = True
            if started:
                ai_excl.add(i)

    # ---- 各域内容切分 ----
    domain_lines = defaultdict(list)
    for sec, s, e in bounds:
        dom = DOMAIN_OF_SEC.get(sec)
        if dom is None:
            continue  # §0 头部 / §99 INIT 单独处理
        for i in range(s, e):
            if sec == 8 and i in ai_excl:
                continue
            domain_lines[dom].append(lines[i])

    header_lines = [lines[i] for i in range(0, bounds[0][1])]  # §0：IIFE 开壳到第一节前

    # ---- 生成文件 ----
    os.makedirs(OUT_DIR, exist_ok=True)
    files = {}

    core_body = []
    core_body.append("/* W7 core——共享原语与常量（RF-20261112-01 阶段4 切割自 v7_app.js §0 与 §8 统一请求小节） */")
    core_body.append("(function () {")
    core_body.append('  "use strict";')
    core_body.append("  var W7 = window.W7 = window.W7 || {};")
    core_body.append("  W7.st = W7.st || {};   // 跨域可变状态（原巨石 13 个模块级 var）")
    core_body.append("  W7.core = {};")
    core_body.append("")
    core_body.append("  /* ---- 原 v7_app.js §0 头部，原样 ---- */")
    for ln in header_lines[7:]:  # 跳过文件头注释 + IIFE 开壳 + use strict
        ln = ln.rstrip()
        if ln.startswith("(function") or ln == '"use strict";' or ln == "":
            continue
        core_body.append("  " + ln)
    core_body.append("")
    core_body.append("  /* ---- 原 §8「统一请求（401 跳登录，D-19）」小节，原样 ---- */")
    for i in sorted(ai_excl):
        core_body.append("  " + lines[i].strip() if lines[i].strip() else "")
    core_body.append("")
    core_body.append("  /* ---- core 导出 ---- */")
    for n in CORE_COPIES:
        core_body.append("  W7.core.%s = %s;" % (n, n))
    core_body.append("})();")
    files["w7_core.js"] = core_body

    COPY_BLOCK = ("  var C = W7.core;\n"
                  "  var $ = C.$, toast = C.toast, apiJson = C.apiJson, queryParam = C.queryParam;\n")

    for dom in ("shell", "topbar", "ai", "forms", "detail", "portfolio", "fund", "data", "misc"):
        body = domain_lines.get(dom, [])
        if not body:
            continue
        out = []
        out.append("/* W7.%s——切割自 v7_app.js（RF-20261112-01 阶段4）。代码体原样保留。 */" % dom)
        out.append("(function () {")
        out.append('  "use strict";')
        out.append("  var W7 = window.W7 = window.W7 || {};")
        out.append(COPY_BLOCK.rstrip())
        # 惰性桥：本域用到、定义在别的域的函数
        bridges = []
        for name, dd in sorted(cross_fn.items()):
            if dd == dom:
                continue
            if dom in use_domains[name]:
                bridges.append((name, dd))
        for name, dd in sorted(bridges):
            out.append("  var %s = function () { return W7.%s.%s.apply(null, arguments); };" % (name, dd, name))
        if bridges:
            out.append("")
        out.extend(body)
        # 导出
        exports = [n for n, dd in cross_fn.items() if dd == dom]
        if exports:
            out.append("")
            out.append("  /* 跨域导出（W7.%s） */" % dom)
            out.append("  W7.%s = W7.%s || {};" % (dom, dom))
            for n in sorted(exports):
                out.append("  W7.%s.%s = %s;" % (dom, n, n))
        out.append("})();")
        files["w7_%s.js" % dom] = out

    # ---- 装配 ----
    asm = []
    asm.append("/* W7 装配——v7_app.js 初始化节（RF-20261112-01 阶段4）。")
    asm.append("   全部域文件加载完成后执行；bind 调用顺序与原 init() 逐一对应。 */")
    asm.append("(function () {")
    asm.append('  "use strict";')
    asm.append("  var W7 = window.W7 = window.W7 || {};")
    asm.append("")
    asm.append("  function init() {")
    asm.append("    W7.shell.bindLogo();")
    asm.append("    W7.shell.bindCollapse();")
    asm.append("    W7.shell.bindMobileNav();")
    asm.append("    W7.shell.bindLayout();")
    asm.append("    W7.topbar.bindSearch();")
    asm.append("    W7.topbar.bindBell();")
    asm.append("    W7.topbar.bindBadges();")
    asm.append("    W7.ai.bindAi();")
    asm.append("    W7.forms.bindActions();")
    asm.append("    W7.detail.watchRoute();")
    asm.append("    W7.misc.bindWidgetParams();")
    asm.append("    W7.portfolio.bindDashboardPersist();")
    asm.append("    W7.detail.bindKlineTools();")
    asm.append("    W7.misc.bindContextMenu();")
    asm.append("    W7.portfolio.watchViewRender();")
    asm.append("    var mode = W7.shell.currentLayout();")
    asm.append("    W7.shell.applyLayout(mode);")
    asm.append('    if (mode === "custom") { W7.shell.applyLayout("custom", W7.shell.customOpts()); }')
    asm.append("  }")
    asm.append("")
    asm.append('  if (document.readyState === "loading") {')
    asm.append('    document.addEventListener("DOMContentLoaded", init);')
    asm.append("  } else {")
    asm.append("    init();")
    asm.append("  }")
    asm.append("")
    asm.append("  window.V7 = { applyLayout: W7.shell.applyLayout, toast: W7.core.toast, openPanel: W7.shell.openPanel };")
    asm.append("})();")
    files["w7_app.js"] = asm

    # ---- 写出 ----
    print("== 切割统计 ==")
    print("  状态量引用替换点: %d" % n_state_refs)
    print("  跨域函数（桥）: %d 个" % len(cross_fn))
    for dom_src, files_n in [("core", 1)]:
        pass
    for fname, body in files.items():
        n = len([l for l in body if l is not None])
        print("  %-18s %5d 行" % (fname, n))
        if not dry:
            io.open(os.path.join(OUT_DIR, fname), "w", encoding="utf-8", newline="\n").write(
                "\n".join(l for l in body if l is not None) + "\n")
    print("输出目录: %s%s" % (os.path.abspath(OUT_DIR), "（dry-run 未写盘）" if dry else ""))


if __name__ == "__main__":
    main()
