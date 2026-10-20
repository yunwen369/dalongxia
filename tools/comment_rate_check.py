# -*- coding: utf-8 -*-
"""阶段 8 · 代码注释率抽检（规范 §11.11 / 附录 G）。

统计范围：
  - backend/app/**/*.py：AST 解析函数/方法 docstring 覆盖率；
    口径/单位/接口来源注释命中（函数体或紧邻注释出现
    口径|单位|来源|接口|源站|亿元|万元|‰|TTL|降级 等关键词）；
  - frontend/js/*.js：函数定义（含对象方法简写）的 JSDoc/注释覆盖率。

输出：JSON（按文件汇总 + 未覆盖函数明细）+ Markdown 抽检表骨架。
纪律：只读不写被测文件；统计口径透明可复算。
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
from typing import Any, Dict, List

# 口径/单位/来源注释关键词（命中任一即视为该函数带口径注释）
CALIBER_WORDS = [
    "口径", "单位", "来源", "接口", "源站", "上游", "亿元", "万元", "万",
    "复权", "前复权", "后复权", "TTL", "缓存", "降级", "回退", "限频",
    "北京时间", "UTC", "交易日", "净值", "市值", "收益率", "回撤",
]

JS_FUNC_RE = re.compile(
    r"(?:^|\n)\s*(?:function\s+([\w$]+)\s*\(|([\w$]+)\s*[:=]\s*function\s*\()"
)


def python_file_report(path: str) -> Dict[str, Any]:
    """统计单个 Python 文件的函数 docstring 与口径注释覆盖。"""
    with open(path, "r", encoding="utf-8") as handle:
        source = handle.read()
    lines = source.splitlines()
    tree = ast.parse(source)
    funcs: List[Dict[str, Any]] = []

    def _walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                doc = ast.get_docstring(child, clean=False)
                # 口径注释：函数签名行向上 4 行注释 + docstring 本体 + 函数体前 6 行
                start = max(0, child.lineno - 5)
                ctx_lines = lines[start:child.lineno - 1]
                body_ctx = lines[child.lineno - 1:child.lineno + 6]
                ctx_text = "\n".join(ctx_lines + body_ctx) + "\n" + (doc or "")
                hits = sorted({w for w in CALIBER_WORDS if w in ctx_text})
                trivial = bool(re.search(r"# (TODO|占位|临时|xxx|随便|无所谓)", "\n".join(lines)))
                funcs.append({
                    "name": name,
                    "line": child.lineno,
                    "docstring": bool(doc),
                    "caliber_hits": hits,
                    "has_caliber": bool(hits),
                    "trivial_comment": trivial,
                })
                _walk(child, name + ".")
            elif isinstance(child, ast.ClassDef):
                _walk(child, prefix + child.name + ".")
            else:
                _walk(child, prefix)

    _walk(tree, "")
    total = len(funcs)
    with_doc = sum(1 for f in funcs if f["docstring"])
    with_caliber = sum(1 for f in funcs if f["has_caliber"])
    return {
        "file": path,
        "funcs": total,
        "docstring": with_doc,
        "caliber": with_caliber,
        "docstring_rate": round(with_doc / total, 4) if total else None,
        "caliber_rate": round(with_caliber / total, 4) if total else None,
        "missing_doc": [f["name"] for f in funcs if not f["docstring"]],
        "missing_caliber": [f["name"] for f in funcs if not f["has_caliber"]],
    }


def js_file_report(path: str) -> Dict[str, Any]:
    """统计单个 JS 文件的函数注释覆盖（JSDoc 块或上方行注释）。

    换行符统一为 \\n 后再定位行号：源文件为 CRLF 时，
    `"\\n".join(lines)` 与原文长度不一致会导致行号偏移、注释误判缺失。
    """
    with open(path, "r", encoding="utf-8") as handle:
        source = handle.read().replace("\r\n", "\n").replace("\r", "\n")
    lines = source.split("\n")
    funcs: List[Dict[str, Any]] = []
    for match in JS_FUNC_RE.finditer("\n".join(lines)):
        name = match.group(1) or match.group(2) or "<anonymous>"
        # 用匹配结尾位置换算行索引：正则含前导换行，用 match.start() 会少算一行
        line_idx = source[: match.end()].count("\n")
        # 向上寻找 JSDoc 块收尾 */ 或行注释 //
        has_doc = False
        for back in range(line_idx - 1, max(-1, line_idx - 8), -1):
            text = lines[back].strip()
            if text.endswith("*/") or text.startswith("*") or text.startswith("//"):
                has_doc = True
                break
            if text and not text.startswith("*") and not text.startswith("//"):
                break
        ctx_text = "\n".join(lines[line_idx:line_idx + 8])
        hits = sorted({w for w in CALIBER_WORDS if w in ctx_text})
        funcs.append({"name": name, "line": line_idx + 1,
                      "docstring": has_doc, "has_caliber": bool(hits)})
    total = len(funcs)
    with_doc = sum(1 for f in funcs if f["docstring"])
    with_caliber = sum(1 for f in funcs if f["has_caliber"])
    return {
        "file": path,
        "funcs": total,
        "docstring": with_doc,
        "caliber": with_caliber,
        "docstring_rate": round(with_doc / total, 4) if total else None,
        "caliber_rate": round(with_caliber / total, 4) if total else None,
        "missing_doc": [f["name"] for f in funcs if not f["docstring"]],
        "missing_caliber": [f["name"] for f in funcs if not f["has_caliber"]],
    }


def scan(root: str) -> Dict[str, Any]:
    """扫描 backend 与 frontend/js，返回分层统计。"""
    py_files: List[str] = []
    for base, _dirs, names in os.walk(os.path.join(root, "backend", "app")):
        for name in names:
            if name.endswith(".py"):
                py_files.append(os.path.join(base, name))
    js_files: List[str] = []
    js_dir = os.path.join(root, "frontend", "js")
    for name in sorted(os.listdir(js_dir)):
        if name.endswith(".js"):
            js_files.append(os.path.join(js_dir, name))

    py_reports = [python_file_report(p) for p in sorted(py_files)]
    js_reports = [js_file_report(p) for p in js_files]

    def _agg(reports: List[Dict[str, Any]]) -> Dict[str, Any]:
        funcs = sum(r["funcs"] for r in reports)
        docs = sum(r["docstring"] for r in reports)
        caliber = sum(r["caliber"] for r in reports)
        return {
            "files": len(reports), "funcs": funcs, "docstring": docs, "caliber": caliber,
            "docstring_rate": round(docs / funcs, 4) if funcs else None,
            "caliber_rate": round(caliber / funcs, 4) if funcs else None,
        }

    return {
        "python": {"agg": _agg(py_reports), "files": py_reports},
        "js": {"agg": _agg(js_reports), "files": js_reports},
    }


def render_markdown(result: Dict[str, Any]) -> str:
    """输出附录 G 形式的 Markdown 抽检表。"""
    out = ["# 代码注释率抽检表（阶段 8 自动统计）", ""]
    for layer in ("python", "js"):
        agg = result[layer]["agg"]
        out.append(f"## {layer}")
        out.append(f"- 文件 {agg['files']} 个 / 函数 {agg['funcs']} 个；"
                   f"docstring 覆盖 {agg['docstring']}（{agg['docstring_rate']}）；"
                   f"口径/单位/来源注释 {agg['caliber']}（{agg['caliber_rate']}）")
        out.append("")
        out.append("| 文件 | 函数数 | docstring | 口径注释 | 缺 docstring 函数（前 15） |")
        out.append("|---|---|---|---|---|")
        for rep in sorted(result[layer]["files"],
                          key=lambda r: (-(r["docstring_rate"] or 0), r["file"])):
            missing = "、".join(rep["missing_doc"][:15]) or "—"
            out.append(f"| {os.path.basename(rep['file'])} | {rep['funcs']} | "
                       f"{rep['docstring_rate']} | {rep['caliber_rate']} | {missing} |")
        out.append("")
    return "\n".join(out)


def main() -> None:
    """入口：扫描并落盘 JSON + Markdown。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=os.path.join(os.path.dirname(
        os.path.abspath(__file__)), ".."))
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-md", required=True)
    args = parser.parse_args()

    result = scan(os.path.abspath(args.root))
    os.makedirs(os.path.dirname(os.path.abspath(args.out_json)), exist_ok=True)
    with open(args.out_json, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    with open(args.out_md, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(result))
    for layer in ("python", "js"):
        agg = result[layer]["agg"]
        print(f"[{layer}] files={agg['files']} funcs={agg['funcs']} "
              f"docstring_rate={agg['docstring_rate']} caliber_rate={agg['caliber_rate']}")


if __name__ == "__main__":
    main()
