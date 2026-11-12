#!/usr/bin/env python3
"""源码快照：按版本目录归档源码 + 生成 SHA256 清单与元数据。

用途：每次发版后把工作树的**源码**（不含 .venv/dist/data/logs/二进制）复制到
``B_交付归档/06_源码快照/<版本目录>/``，并产出 ``SHA256SUMS.txt`` 与
``_snapshot_meta.json``，供回滚锚点与交付审计使用。

排除规则与历史快照保持一致（目录 + 扩展名两组，见 ``EXCLUDE_DIRS`` / ``EXCLUDE_EXT``）。

用法::

    python tools/make_snapshot.py --dest <快照目录> \\
        --version 3.4.0 --build-tag v3.4.0-20261112 \\
        [--note "本版说明"] [--release-gate 正式环境/release_gate.json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from typing import Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)

EXCLUDE_DIRS = {
    ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv", ".venv-verify",
    "__pycache__", "build", "data", "dist", "dist_setup", "logs", "node_modules",
}
# 探针运行期生成的 Chrome 配置目录（tools/_xxx_profile，数十万小文件）：
# 属临时产物，绝不进快照。
EXCLUDE_DIR_PREFIXES = ("_",)
EXCLUDE_DIR_SUFFIXES = ("_profile", "_profile.tmp")
EXCLUDE_EXT = {".7z", ".dll", ".exe", ".log", ".pdb", ".pyc", ".pyd", ".pyo", ".zip"}


def sha256_of(path: str) -> str:
    """计算文件 SHA256。

    Args:
        path: 文件路径。

    Returns:
        str: 64 位十六进制摘要。
    """
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(*args: str) -> str:
    """执行 git 命令并返回 stdout（失败返回空串）。

    Args:
        *args: git 子命令与参数。

    Returns:
        str: 去空白后的输出。
    """
    try:
        out = subprocess.run(["git", *args], cwd=PROJECT, capture_output=True,
                             text=True, timeout=30)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:  # noqa: BLE001 - git 不可用不影响快照
        return ""


def main() -> int:
    """入口：复制源码并生成清单。

    Returns:
        int: 0=成功，1=目标路径非法。
    """
    ap = argparse.ArgumentParser(description="生成源码快照")
    ap.add_argument("--dest", required=True, help="快照目标目录（须不存在或为空）")
    ap.add_argument("--version", required=True)
    ap.add_argument("--build-tag", required=True)
    ap.add_argument("--note", default="")
    ap.add_argument("--gate", default="", help="release_gate.json 路径（可选）")
    args = ap.parse_args()

    dest = os.path.abspath(args.dest)
    if os.path.isdir(dest) and os.listdir(dest):
        print(f"目标目录非空，拒绝覆盖：{dest}")
        return 1
    os.makedirs(dest, exist_ok=True)

    copied: List[str] = []
    for root, dirs, files in os.walk(PROJECT):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS
                   and not (d.startswith(EXCLUDE_DIR_PREFIXES) and d.endswith(EXCLUDE_DIR_SUFFIXES))]
        for name in files:
            if os.path.splitext(name)[1].lower() in EXCLUDE_EXT:
                continue
            src = os.path.join(root, name)
            rel = os.path.relpath(src, PROJECT)
            if rel.split(os.sep)[0] in EXCLUDE_DIRS:
                continue
            dst = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(rel)

    sums = []
    for rel in sorted(copied):
        sums.append(f"{sha256_of(os.path.join(dest, rel))}  {rel.replace(os.sep, '/')}")
    with open(os.path.join(dest, "SHA256SUMS.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(sums) + "\n")

    gate: Dict[str, object] = {}
    if args.gate and os.path.exists(args.gate):
        try:
            data = json.load(open(args.gate, encoding="utf-8"))
            checks = data.get("checks") or {}
            gate = {
                "passed": data.get("passed"),
                "pytest": ((checks.get("pytest") or {}).get("summary")
                           or (checks.get("pytest") or {}).get("detail") or ""),
                "report": args.gate,
            }
        except Exception:  # noqa: BLE001
            gate = {"report": args.gate, "note": "解析失败"}

    meta = {
        "version": args.version,
        "build_tag": args.build_tag,
        "git_commit": git("rev-parse", "--short", "HEAD"),
        "git_branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "files": len(copied),
        "excluded_dirs": sorted(EXCLUDE_DIRS),
        "excluded_ext": sorted(EXCLUDE_EXT),
        "note": args.note,
        "release_gate": gate,
    }
    with open(os.path.join(dest, "_snapshot_meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)

    print(f"[完成] 快照 {len(copied)} 文件 → {dest}")
    print("       清单 SHA256SUMS.txt / 元数据 _snapshot_meta.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
