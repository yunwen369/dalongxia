#!/usr/bin/env python3
"""静态检查前端 i18n：① 键覆盖（t()/data-i18n 是否都在字典）；② 重复键与值冲突。

背景（第五轮阶段 2）：``i18n.js`` 的 ``fs.*`` 键曾被写两遍（第五波块A + 第九波块B），
``duplicate_keys=39 / conflicts=13``，JS 对象后者覆盖前者导致界面文案被悄悄替换。
原脚本只查「缺键」，查不出重复 —— 本版补齐重复/冲突校验，作为去重的验收工具。

判定：
- 缺键 > 0 → 退出码 1；
- ``duplicate_keys`` > 0 或 ``conflicts`` > 0 → 退出码 1；
- 全绿时输出「字典 N 条 / 定义 N 处 / 唯一键 N / 重复 0 / 冲突 0」。

用法：
    python tools/check_i18n_keys.py [--json <输出路径>]
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
from typing import Dict, List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONT = os.path.join(ROOT, "frontend")
I18N = os.path.join(FRONT, "js", "i18n.js")

with open(I18N, encoding="utf-8") as handle:
    SRC = handle.read()

# 只取中文字典段：形如 "key": "中文"
DICT_KEYS = set(re.findall(r'"([A-Za-z][A-Za-z0-9_.]*)"\s*:\s*"', SRC))

# 字典项（整行）：缩进 + "key": "value" + 可选逗号
ITEM_RE = re.compile(r'^\s*"([A-Za-z][A-Za-z0-9_.]*)"\s*:\s*"(.*)"\s*,?\s*$')

KEY_RE = re.compile(r"""\bt\(\s*["']([A-Za-z][A-Za-z0-9_.]*)["']""")
ATTR_RE = re.compile(r"""data-i18n(?:-title|-placeholder)?=["']([A-Za-z][A-Za-z0-9_.]*)["']""")


def collect_missing() -> Dict[str, List[str]]:
    """检查 JS/HTML 引用的键是否都在字典中。

    Returns:
        Dict[str, List[str]]: 文件 → 缺失键列表（空字典表示全部命中）。
    """
    files = [os.path.join(FRONT, "index.html")]
    js_dir = os.path.join(FRONT, "js")
    for name in sorted(os.listdir(js_dir)):
        if name.endswith(".js") and name != "i18n.js":
            files.append(os.path.join(js_dir, name))
    missing: Dict[str, List[str]] = {}
    for path in files:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        keys = set(KEY_RE.findall(text)) | set(ATTR_RE.findall(text))
        absent = sorted(k for k in keys if k not in DICT_KEYS)
        if absent:
            missing[os.path.relpath(path, ROOT)] = absent
    return missing


def scan_duplicates(lines: List[str]) -> Dict[str, object]:
    """统计字典区内的重复键与值冲突。

    Args:
        lines: i18n.js 的全部行。

    Returns:
        Dict[str, object]: definitions / unique_keys / duplicate_keys / conflicts / detail。
    """
    start = next(i for i, line in enumerate(lines) if line.strip().startswith("var dict = {"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "};")
    grouped: Dict[str, List[Tuple[str, int]]] = collections.OrderedDict()
    definitions = 0
    for idx in range(start + 1, end):
        match = ITEM_RE.match(lines[idx])
        if not match:
            continue
        definitions += 1
        grouped.setdefault(match.group(1), []).append((match.group(2), idx + 1))
    duplicates = {key: values for key, values in grouped.items() if len(values) > 1}
    conflicts = {
        key: values for key, values in duplicates.items()
        if len({value for value, _ in values}) > 1
    }
    detail = [
        {"key": key, "count": len(values), "conflict": key in conflicts,
         "values": [{"value": value, "line": line} for value, line in values]}
        for key, values in duplicates.items()
    ]
    return {
        "definitions": definitions,
        "unique_keys": len(grouped),
        "duplicate_keys": len(duplicates),
        "conflicts": len(conflicts),
        "detail": detail,
    }


def main(argv: List[str]) -> int:
    """命令行入口。

    Args:
        argv: 去掉程序名的参数列表。

    Returns:
        int: 0=全绿；1=缺键/重复/冲突。
    """
    parser = argparse.ArgumentParser(description="i18n 覆盖与重复校验")
    parser.add_argument("--json", default="", help="可选：把统计写入 JSON 文件")
    args = parser.parse_args(argv)

    with open(I18N, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    stats = scan_duplicates(lines)
    missing = collect_missing()

    if args.json:
        payload = {"dict_keys": len(DICT_KEYS), "missing": missing, **stats}
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    exit_code = 0
    if missing:
        total = sum(len(keys) for keys in missing.values())
        for path, keys in missing.items():
            print(f"\n{path}（{len(keys)} 个缺键）")
            for key in keys:
                print("  -", key)
        print(f"\n合计缺失 {total} 个键")
        exit_code = 1
    else:
        print(f"OK：全部键命中字典（字典 {len(DICT_KEYS)} 条）")

    print(f"字典定义 {stats['definitions']} 处 / 唯一键 {stats['unique_keys']} / "
          f"重复键 {stats['duplicate_keys']} / 值冲突 {stats['conflicts']}")
    if stats["duplicate_keys"]:
        for row in stats["detail"]:  # type: ignore[index]
            flag = "冲突" if row["conflict"] else "同值"
            print(f"\n[{flag}] {row['key']} ×{row['count']}")
            for item in row["values"]:
                print(f"   L{item['line']:>4} {item['value'][:80]}")
        exit_code = 1
    if stats["definitions"] != stats["unique_keys"]:
        print("不通过：定义处数与唯一键数不等（存在重复定义）")
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
