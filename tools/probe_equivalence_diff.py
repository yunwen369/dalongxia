#!/usr/bin/env python3
"""改前/改后探针 JSON 深度对拍（v3.4.2 等价性验收）。

对 data/_tmp/baseline_v342_{ui,panels,nav}.json 与 data/_tmp/after_v342_*.json
做规范化深度比较：逐键递归，输出首个差异点与差异总数。浮点按 1e-9 容差。
退出码 0 = 三份全等价（diff=0）。
"""

from __future__ import annotations

import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = os.path.join(HERE, "..", "data", "_tmp")
PAIRS = [
    ("ui", "baseline_v342_ui.json", "after_v342_ui.json"),
    ("panels", "baseline_v342_panels.json", "after_v342_panels.json"),
    ("nav", "baseline_v342_nav.json", "after_v342_nav.json"),
]


def deep_diff(a, b, path="$", out=None):
    if out is None:
        out = []
    TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?")
    if isinstance(a, str) and isinstance(b, str):
        a, b = TS_RE.sub("<TS>", a), TS_RE.sub("<TS>", b)
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"{path}.{k}: 仅改后存在={b[k]!r}")
            elif k not in b:
                out.append(f"{path}.{k}: 仅改前存在={a[k]!r}")
            else:
                deep_diff(a[k], b[k], f"{path}.{k}", out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: 长度 {len(a)} -> {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            deep_diff(x, y, f"{path}[{i}]", out)
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        if not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9):
            out.append(f"{path}: {a!r} -> {b!r}")
    else:
        if a != b:
            out.append(f"{path}: {a!r} -> {b!r}")
    return out


def main() -> int:
    total = 0
    all_ok = True
    for name, base_f, after_f in PAIRS:
        bp = os.path.join(TMP, base_f)
        ap = os.path.join(TMP, after_f)
        if not (os.path.exists(bp) and os.path.exists(ap)):
            print(f"[{name}] SKIP 文件缺失（{base_f} / {after_f}）")
            all_ok = False
            continue
        b = json.load(open(bp, encoding="utf-8"))
        a = json.load(open(ap, encoding="utf-8"))
        # 时变字段剔除：探针运行时间戳、截图路径等与等价性无关的键
        VOLATILE = {"ts", "time", "timestamp", "server_time", "as_of", "duration", "elapsed",
                    "shots", "shot_dir", "base", "port", "base_url", "started", "finished"}
        strip(b, VOLATILE)
        strip(a, VOLATILE)
        diffs = deep_diff(b, a)
        total += len(diffs)
        status = "DIFF=0 等价" if not diffs else f"DIFF={len(diffs)}"
        print(f"[{name}] {status}")
        for d in diffs[:30]:
            print("   ", d)
        if len(diffs) > 30:
            print(f"    …（共 {len(diffs)} 处差异）")
        if diffs:
            all_ok = False
    print(f"\n总结：{'三份探针 JSON 全等价（diff=0）' if all_ok and total == 0 else f'共 {total} 处差异'}")
    return 0 if all_ok and total == 0 else 1


def strip(node, keys):
    """就地剔除时变键（递归）。"""
    if isinstance(node, dict):
        for k in list(node):
            lk = k.lower()
            if any(v == lk or lk.endswith(v) for v in keys):
                del node[k]
            else:
                strip(node[k], keys)
    elif isinstance(node, list):
        for x in node:
            strip(x, keys)


if __name__ == "__main__":
    sys.exit(main())
