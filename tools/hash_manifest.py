"""文件哈希清单工具：为指定目录生成 SHA256 清单，并可与既有清单逐项比对。

用途：开工基线与收尾改动前后哈希留证（规范要求"可追溯、可验证"）。
输出格式与第三方验收基线一致：`<sha256>  <字节数>  <相对路径>`。

用法：
    python tools/hash_manifest.py write  --root . --dirs backend/app frontend tests sdk --out 基线.txt
    python tools/hash_manifest.py diff   --old 基线.txt --new 收尾.txt
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from typing import Dict, List, Tuple

# 清单行格式：64 位十六进制 + 两个空格 + 字节数 + 两个空格 + 路径
LINE_RE = re.compile(r"^([a-f0-9]{64})\s+(\d+)\s+(.+?)\s*$")

# 跳过目录：字节码与缓存不参与哈希（非源码资产）
SKIP_DIR_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}

# 跳过后缀：编译产物与临时文件
SKIP_SUFFIXES = {".pyc", ".pyo", ".log", ".tmp"}


def sha256_file(path: str) -> str:
    """计算单文件 SHA256。

    Args:
        path: 文件绝对路径或相对当前工作目录的路径。

    Returns:
        str: 64 位小写十六进制摘要。

    Raises:
        OSError: 文件不存在或不可读时抛出。
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_files(root: str, dirs: List[str]) -> List[str]:
    """遍历指定子目录下的全部资产文件（相对 root 的路径，正斜杠分隔）。

    Args:
        root: 工程根目录。
        dirs: 需要遍历的相对子目录名列表。

    Returns:
        List[str]: 排序后的相对路径列表。
    """
    collected: List[str] = []
    for rel_dir in dirs:
        abs_dir = os.path.join(root, rel_dir.replace("/", os.sep))
        if not os.path.isdir(abs_dir):
            continue
        for current, sub_dirs, files in os.walk(abs_dir):
            # 原地过滤缓存目录，避免无谓遍历
            sub_dirs[:] = [d for d in sub_dirs if d not in SKIP_DIR_NAMES]
            for name in files:
                if os.path.splitext(name)[1].lower() in SKIP_SUFFIXES:
                    continue
                abs_path = os.path.join(current, name)
                rel_path = os.path.relpath(abs_path, root).replace(os.sep, "/")
                collected.append(rel_path)
    return sorted(collected)


def read_manifest(path: str) -> Dict[str, Tuple[str, int]]:
    """读取既有清单文件。

    Args:
        path: 清单文件路径。

    Returns:
        Dict[str, Tuple[str, int]]: 相对路径 → (摘要, 字节数)。
    """
    result: Dict[str, Tuple[str, int]] = {}
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = LINE_RE.match(line)
            if match:
                result[match.group(3).replace("\\", "/")] = (match.group(1), int(match.group(2)))
    return result


def write_manifest(root: str, dirs: List[str], out_path: str) -> int:
    """生成并写盘哈希清单。

    Args:
        root: 工程根目录。
        dirs: 需要遍历的相对子目录名列表。
        out_path: 清单输出路径。

    Returns:
        int: 写入的文件条目数。
    """
    rows: List[str] = []
    for rel_path in iter_files(root, dirs):
        abs_path = os.path.join(root, rel_path.replace("/", os.sep))
        size = os.path.getsize(abs_path)
        # 汇总为固定三列格式，便于与第三方基线直接 diff
        rows.append(f"{sha256_file(abs_path)}  {size}  {rel_path}")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(rows) + ("\n" if rows else ""))
    return len(rows)


def diff_manifests(old_path: str, new_path: str, root: str) -> List[str]:
    """比对两份清单，输出新增/删除/修改三类差异行。

    Args:
        old_path: 旧清单（开工基线）路径。
        new_path: 新清单（收尾）路径。
        root: 工程根目录，用于提示当前实际哈希。

    Returns:
        List[str]: 差异描述行；空列表表示完全一致。
    """
    old = read_manifest(old_path)
    new = read_manifest(new_path)
    lines: List[str] = []
    for rel_path in sorted(set(old) | set(new)):
        old_item = old.get(rel_path)
        new_item = new.get(rel_path)
        if old_item and not new_item:
            lines.append(f"DELETED  {old_item[0]}  {old_item[1]}  {rel_path}")
        elif new_item and not old_item:
            lines.append(f"ADDED    {new_item[0]}  {new_item[1]}  {rel_path}")
        elif old_item and new_item and old_item[0] != new_item[0]:
            lines.append(
                f"MODIFIED {old_item[0]} -> {new_item[0]}  {old_item[1]} -> {new_item[1]}  {rel_path}"
            )
    if not lines:
        lines.append("IDENTICAL  两份清单完全一致")
    return lines


def main(argv: List[str]) -> int:
    """命令行入口。

    Args:
        argv: 去除程序名后的参数列表。

    Returns:
        int: 进程退出码，0 表示成功。
    """
    parser = argparse.ArgumentParser(description="文件哈希清单生成与比对工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p_write = sub.add_parser("write", help="生成哈希清单")
    p_write.add_argument("--root", default=".", help="工程根目录")
    p_write.add_argument("--dirs", nargs="+", default=["backend/app", "frontend", "tests", "sdk"])
    p_write.add_argument("--out", required=True, help="清单输出路径")

    p_diff = sub.add_parser("diff", help="比对两份清单")
    p_diff.add_argument("--old", required=True)
    p_diff.add_argument("--new", required=True)
    p_diff.add_argument("--root", default=".")

    args = parser.parse_args(argv)
    if args.command == "write":
        count = write_manifest(args.root, args.dirs, args.out)
        print(f"已写入 {count} 个文件条目 → {args.out}")
        return 0
    for line in diff_manifests(args.old, args.new, args.root):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
