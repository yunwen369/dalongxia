#!/usr/bin/env python3
"""静态检查前端 i18n 键覆盖：JS 里 t("key") 与 HTML 的 data-i18n* 是否都在字典中。"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONT = os.path.join(ROOT, "frontend")

with open(os.path.join(FRONT, "js", "i18n.js"), encoding="utf-8") as handle:
    SRC = handle.read()

# 只取中文字典段：形如 "key": "中文"
DICT_KEYS = set(re.findall(r'"([A-Za-z][A-Za-z0-9_.]*)"\s*:\s*"', SRC))

files = [os.path.join(FRONT, "index.html")]
for name in sorted(os.listdir(os.path.join(FRONT, "js"))):
    if name.endswith(".js") and name != "i18n.js":
        files.append(os.path.join(FRONT, "js", name))

KEY_RE = re.compile(r"""\bt\(\s*["']([A-Za-z][A-Za-z0-9_.]*)["']""")
ATTR_RE = re.compile(r"""data-i18n(?:-title|-placeholder)?=["']([A-Za-z][A-Za-z0-9_.]*)["']""")

missing = {}
for path in files:
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    keys = set(KEY_RE.findall(text)) | set(ATTR_RE.findall(text))
    absent = sorted(k for k in keys if k not in DICT_KEYS)
    if absent:
        missing[os.path.relpath(path, ROOT)] = absent

if not missing:
    print(f"OK：全部键命中字典（字典 {len(DICT_KEYS)} 条）")
    sys.exit(0)

total = 0
for path, keys in missing.items():
    total += len(keys)
    print(f"\n{path}（{len(keys)} 个缺键）")
    for key in keys:
        print("  -", key)
print(f"\n合计缺失 {total} 个键")
sys.exit(1)
