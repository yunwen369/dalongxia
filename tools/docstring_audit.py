# -*- coding: utf-8 -*-
"""Google 风格 docstring 结构段审计（规范 §10.8 / §11.13 / 附录 G）。

判定口径（严格版，逐函数）：
  - 有参数 → docstring 必须含 ``Args:`` 段，且每个参数名都被列出；
  - 有返回值注解（非 None）→ 必须含 ``Returns:`` 段；
  - 函数体内有 ``raise`` → 必须含 ``Raises:`` 段；
  - 无参数、无返回值 → 单行摘要即合规（Google 官方允许，不强行造段）；
  - 摘要必填：docstring 第一行非空且为中文（含 CJK）。

JS：JSDoc 口径 —— 有参数需 ``@param {Type} name``（每个参数一条），
有 return 值需 ``@returns {Type}``；无参数无返回值的回调可只有一行中文说明。

输出：JSON（逐函数缺口）+ Markdown 汇总（按文件排序，便于分批补齐）。
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
from typing import Any, Dict, List, Optional

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
SECTION_ARGS = re.compile(r"^\s*Args:\s*$", re.MULTILINE)
SECTION_RETURNS = re.compile(r"^\s*Returns:\s*$", re.MULTILINE)
SECTION_RAISES = re.compile(r"^\s*Raises:\s*$", re.MULTILINE)


def _arg_names(func: ast.AST) -> List[str]:
    """取出函数的位置参数与关键字参数名（不含 self/cls）。"""
    args = func.args
    names = [a.arg for a in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)]
    return [n for n in names if n not in ("self", "cls")]


def _returns_needed(func: ast.AST) -> bool:
    """是否需要 Returns 段：有返回注解且非 None，或有非空 return 语句。"""
    annotation = func.returns
    if annotation is not None:
        text = ast.unparse(annotation) if hasattr(ast, "unparse") else ""
        if text not in ("None", "NoneType"):
            return True
        return False
    for node in ast.walk(func):
        if isinstance(node, ast.Return) and node.value is not None:
            # 只统计属于本函数作用域的 return（不含嵌套函数内部）
            return True
    return False


def _raises_needed(func: ast.AST) -> bool:
    """是否需要 Raises 段：函数体内（不含嵌套 def）存在 raise。"""
    for node in ast.walk(func):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node is not func:
            continue
        if isinstance(node, ast.Raise):
            return True
    return False


def _docstring_entry(func: ast.AST, name: str, lineno: int) -> Dict[str, Any]:
    """构造单个函数的缺口记录。"""
    doc = ast.get_docstring(func, clean=False) or ""
    summary = (doc.strip().splitlines() or [""])[0].strip()
    params = _arg_names(func)
    need_args = bool(params)
    need_returns = _returns_needed(func)
    need_raises = _raises_needed(func)

    has_summary = bool(summary) and bool(CJK_RE.search(summary))
    has_args = bool(SECTION_ARGS.search(doc))
    has_returns = bool(SECTION_RETURNS.search(doc))
    has_raises = bool(SECTION_RAISES.search(doc))

    missing: List[str] = []
    if not has_summary:
        missing.append("summary")
    if need_args and not has_args:
        missing.append("Args")
    if need_args and has_args:
        # Args 段存在但漏参数名也算缺口
        absent = [p for p in params if not re.search(rf"^\s*{re.escape(p)}\s*[:(]", doc, re.MULTILINE)]
        if absent:
            missing.append("Args.missing:" + ",".join(absent))
    if need_returns and not has_returns:
        missing.append("Returns")
    if need_raises and not has_raises:
        missing.append("Raises")

    return {
        "name": name,
        "line": lineno,
        "params": params,
        "need_args": need_args,
        "need_returns": need_returns,
        "need_raises": need_raises,
        "has_doc": bool(doc.strip()),
        "missing": missing,
        "ok": not missing,
    }


def audit_python(path: str) -> Dict[str, Any]:
    """审计单个 Python 文件的全部函数/方法。"""
    with open(path, "r", encoding="utf-8") as handle:
        source = handle.read()
    tree = ast.parse(source)
    entries: List[Dict[str, Any]] = []

    def _walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                entries.append(_docstring_entry(child, f"{prefix}{child.name}", child.lineno))
                _walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, ast.ClassDef):
                _walk(child, f"{prefix}{child.name}.")
            else:
                _walk(child, prefix)

    _walk(tree, "")
    bad = [e for e in entries if not e["ok"]]
    return {
        "file": path,
        "total": len(entries),
        "ok": len(entries) - len(bad),
        "bad": len(bad),
        "rate": round((len(entries) - len(bad)) / len(entries), 4) if entries else 1.0,
        "gaps": bad,
    }


JS_FUNC_RE = re.compile(
    r"(?:^|\n)[ \t]*(?:function\s+([\w$]+)\s*\(([^)]*)\)|([\w$]+)\s*[:=]\s*function\s*\(([^)]*)\))"
)


def _js_body(source: str, start: int) -> str:
    """按花括号配平截取函数体（避免 30 行窗口把相邻函数的 return 算进来）。"""
    open_idx = source.find("{", start)
    if open_idx < 0:
        return ""
    depth = 0
    for idx in range(open_idx, len(source)):
        ch = source[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[open_idx:idx + 1]
    return source[open_idx:]


def audit_js(path: str) -> Dict[str, Any]:
    """审计单个 JS 文件的函数 JSDoc（@param/@returns）覆盖。"""
    with open(path, "r", encoding="utf-8") as handle:
        source = handle.read().replace("\r\n", "\n").replace("\r", "\n")
    lines = source.split("\n")
    entries: List[Dict[str, Any]] = []
    for match in JS_FUNC_RE.finditer("\n".join(lines)):
        name = match.group(1) or match.group(3) or "<anonymous>"
        raw_params = match.group(2) if match.group(1) else match.group(4)
        params = [p.strip().split("=")[0].strip() for p in (raw_params or "").split(",") if p.strip()]
        line_idx = source[: match.end()].count("\n")
        # 向上收集紧邻的注释块
        doc_lines: List[str] = []
        for back in range(line_idx - 1, max(-1, line_idx - 14), -1):
            text = lines[back].strip()
            if text.endswith("*/") or text.startswith("*") or text.startswith("//") or text.startswith("/**"):
                doc_lines.append(text)
            elif text == "":
                if doc_lines:
                    continue
                break
            else:
                break
        doc = "\n".join(reversed(doc_lines))
        missing: List[str] = []
        if not CJK_RE.search(doc):
            missing.append("summary")
        for param in params:
            if f"@param" not in doc or not re.search(rf"@param\s*\{{[^}}]*\}}\s*{re.escape(param)}\b", doc):
                missing.append("param:" + param)
        body = _js_body(source, match.end())
        if re.search(r"^\s*return\s+\S", body, re.MULTILINE) and "@returns" not in doc:
            missing.append("returns")
        entries.append({"name": name, "line": line_idx, "params": params,
                        "missing": missing, "ok": not missing})
    bad = [e for e in entries if not e["ok"]]
    return {
        "file": path,
        "total": len(entries),
        "ok": len(entries) - len(bad),
        "bad": len(bad),
        "rate": round((len(entries) - len(bad)) / len(entries), 4) if entries else 1.0,
        "gaps": bad,
    }


def scan(root: str) -> Dict[str, Any]:
    """扫描 backend 与 frontend/js。"""
    py_files = []
    for base, _dirs, names in os.walk(os.path.join(root, "backend", "app")):
        py_files += [os.path.join(base, n) for n in names if n.endswith(".py")]
    js_dir = os.path.join(root, "frontend", "js")
    js_files = [os.path.join(js_dir, n) for n in sorted(os.listdir(js_dir)) if n.endswith(".js")]

    py = [audit_python(p) for p in sorted(py_files)]
    js = [audit_js(p) for p in js_files]

    def _agg(reports: List[Dict[str, Any]]) -> Dict[str, Any]:
        total = sum(r["total"] for r in reports)
        ok = sum(r["ok"] for r in reports)
        return {"files": len(reports), "total": total, "ok": ok, "bad": total - ok,
                "rate": round(ok / total, 4) if total else 1.0}

    return {"python": {"agg": _agg(py), "files": py},
            "js": {"agg": _agg(js), "files": js}}


def render_md(result: Dict[str, Any]) -> str:
    """输出按缺口数排序的 Markdown 汇总。"""
    out = ["# Google 结构段审计（阶段 8 · §10.8 逐函数）", ""]
    for layer in ("python", "js"):
        agg = result[layer]["agg"]
        out.append(f"## {layer}: {agg['ok']}/{agg['total']} 合规（{agg['rate']:.2%}），缺口 {agg['bad']}")
        out.append("")
        out.append("| 文件 | 函数 | 合规 | 缺口 | 缺口函数（前 12） |")
        out.append("|---|---|---|---|---|")
        for rep in sorted(result[layer]["files"], key=lambda r: -r["bad"]):
            names = "、".join(g["name"] for g in rep["gaps"][:12]) or "—"
            out.append(f"| {os.path.basename(rep['file'])} | {rep['total']} | {rep['ok']} | {rep['bad']} | {names} |")
        out.append("")
    return "\n".join(out)


def main() -> None:
    """入口。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=os.path.join(os.path.dirname(
        os.path.abspath(__file__)), ".."))
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-md", required=True)
    args = parser.parse_args()
    result = scan(os.path.abspath(args.root))
    with open(args.out_json, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    with open(args.out_md, "w", encoding="utf-8") as handle:
        handle.write(render_md(result))
    for layer in ("python", "js"):
        agg = result[layer]["agg"]
        print(f"[{layer}] total={agg['total']} ok={agg['ok']} bad={agg['bad']} rate={agg['rate']:.2%}")


if __name__ == "__main__":
    main()
