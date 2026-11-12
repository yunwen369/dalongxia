# -*- coding: utf-8 -*-
"""重复模式静态扫描门禁——RF-20261112-01 阶段 6。

扫描 backend/app/ 的高风险重复模式（历史教训逐条对应）：
  P1  手写退避循环（while True:/for attempt + time.sleep 同函数）
      → 应 retry_call / retry_until（core/resilient.py，阶段 1a 收编口径）
  P2  字面量构造 HTTP 信封专属字段（"error_code": 直接出现在代码行）
      → 应 core/errors.py 唯一实现（ok/pack_success/api_error/bad_request/
      failure_pack / routers/common.pack_result）

语义边界（2026-11-12 阶段 6 甄别定版，54 项首扫误报归零依据）：
  1. P2 只认 ``error_code`` —— 它是 HTTP 业务信封专属字段；缓存 loader 内部
     协议（fund/sentiment 的 ``{"items":..., "error":...}``）与状态快照
     （public_tunnel._STATE、sector_health.record）只有 ``error``，天然不命中。
  2. docstring/注释里的信封示例文案（``Returns: {"data":..., "error_code":...}``）
     不是代码构造，ast 解析剥离后不参与扫描。
  3. core/errors.py（信封唯一实现层）与 core/resilient.py（retry 机制 +
     降级 result 组装唯一实现层，其输出是 failure_pack 的上游输入协议）整体豁免。
  4. 中间件层 access_middleware.py 的 401/429 是 HTTP 语义响应
     （status code 承载主信息），非 200 业务信封，``# dup-ok`` 备案。

白名单机制：行尾 `# dup-ok: <原因>` 显式豁免（需写明原因，评审可查）。

用法：python tools/check_dup_patterns.py   （0 退出 = 通过）
"""
import ast
import io
import os
import re
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
APP = os.path.join(ROOT, "backend", "app")

WHILE_RE = re.compile(r"^\s*while True:\s*$")
FOR_ATTEMPT_RE = re.compile(r"^\s*for\s+attempt\b")
SLEEP_RE = re.compile(r"time\.sleep\(")
# 信封专属字段判定：内部协议/状态 dict 只有 error，只有 HTTP 业务信封带 error_code
ENVELOPE_RE = re.compile(r"[\"']error_code[\"']\s*:")
DUP_OK_RE = re.compile(r"#\s*dup-ok:\s*(.+)$")

# 唯一实现层整体豁免（与白名单同等待遇，机制自身不是重复）：
#   errors.py   —— HTTP 信封唯一实现
#   resilient.py —— retry 机制 + 降链 result 内部协议唯一组装层
WHITELIST_FILES = (
    os.path.join("core", "errors.py"),
    os.path.join("core", "resilient.py"),
)


def read_lines(p):
    return io.open(p, encoding="utf-8").read().splitlines()


def docstring_line_set(source):
    """ast 解析收集全部 docstring 所在行号（1-based），防止示例文案误报。"""
    out = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            out.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return out


def scan_file(path):
    """返回 [(line_no, rule_id, message)]；函数级近似：sleep 与循环起点间隔 <= 40 行视为同函数。"""
    source = io.open(path, encoding="utf-8").read()
    lines = source.splitlines()
    docs = docstring_line_set(source)
    hits = []
    sleep_idx = [i for i, ln in enumerate(lines) if SLEEP_RE.search(ln)]
    loop_idx = [i for i, ln in enumerate(lines) if WHILE_RE.match(ln) or FOR_ATTEMPT_RE.match(ln)]
    for li in sleep_idx:
        # 是否在重试语境（40 行内出现循环头）——重试语境不自动违例，仅无备案注释才违例
        near_loop = any(0 < li - lo <= 40 for lo in loop_idx)
        if near_loop:
            # 同行或前 3 行内找 dup-ok 豁免
            ctx = "\n".join(lines[max(0, li - 3):li + 1])
            if not DUP_OK_RE.search(ctx):
                hits.append((li + 1, "P1", "疑似手写退避循环（time.sleep + 重试语境）；应 retry_call/retry_until 或 # dup-ok 备案"))
    for i, ln in enumerate(lines):
        if (i + 1) in docs:
            continue  # docstring 示例文案非代码构造
        if ENVELOPE_RE.search(ln) and not DUP_OK_RE.search(ln):
            hits.append((i + 1, "P2", "字面量构造信封专属字段 error_code；应经 core/errors.py 唯一实现"))
    return hits


def main():
    errors = []
    scanned = 0
    for dirpath, _dirnames, filenames in os.walk(APP):
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            norm = rel.replace("/", os.sep)
            if any(norm.endswith(w) or rel.endswith(w.replace(os.sep, "/")) for w in WHITELIST_FILES):
                continue  # 唯一实现层自身豁免
            scanned += 1
            for line_no, rule, msg in scan_file(p):
                errors.append("P? %s:%d [%s] %s" % (rel, line_no, rule, msg))
    if errors:
        print("重复模式扫描：FAIL（%d 项，扫描 %d 文件）" % (len(errors), scanned))
        for e in errors:
            print("  ✗ " + e)
        return 1
    print("OK  重复模式扫描：手写退避 / 信封字面量 全部通过（扫描 %d 文件）" % scanned)
    return 0


if __name__ == "__main__":
    sys.exit(main())
