"""原子写助手：临时文件 + os.replace，统一全项目的落盘语义。

背景（R3-09）：`temp + os.replace` 曾在 watchlist / secure_store / yangjibao /
routers.ai 四处各写一遍，参数与权限口径不一致。这里收口为唯一实现：

1. **原子性**：同目录临时文件写完后 `os.replace` 覆盖，避免半截文件；
2. **权限**：可选 `mode=0o600`（POSIX）+ Windows 只读隐藏位不强加，保证密钥类文件不外泄；
3. **可观测**：失败时清理临时文件并把原始异常抛给调用方，不吞错。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional, Union

PathLike = Union[str, Path]


def atomic_write_bytes(path: PathLike, payload: bytes, *, mode: Optional[int] = None) -> None:
    """原子写入字节内容（同目录临时文件 → os.replace）。

    Args:
        path: 目标文件路径（父目录不存在时自动创建）。
        payload: 待写入的原始字节。
        mode: 可选权限位（如 0o600）；None 表示沿用系统默认 umask。

    Raises:
        OSError: 磁盘不可写、目标被占用等底层错误会原样抛出。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # 临时文件必须与目标同目录，跨盘 replace 不是原子操作
    handle = tempfile.NamedTemporaryFile(dir=str(target.parent), prefix=".tmp_", delete=False)
    temp_path = Path(handle.name)
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            try:
                os.chmod(temp_path, mode)
            except OSError:
                # Windows 下 chmod 语义有限，失败不影响原子替换
                pass
        os.replace(temp_path, target)
    except Exception:
        # 失败必须清理临时文件，避免 data 目录堆积半截文件
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def atomic_write_text(path: PathLike, text: str, *, encoding: str = "utf-8",
                      mode: Optional[int] = None) -> None:
    """原子写入文本内容。

    Args:
        path: 目标文件路径。
        text: 文本内容（不做换行符转换）。
        encoding: 编码，缺省 utf-8。
        mode: 可选权限位（如 0o600）。
    """
    atomic_write_bytes(path, text.encode(encoding), mode=mode)


def atomic_write_json(path: PathLike, payload: Any, *, indent: int = 2,
                      ensure_ascii: bool = False, mode: Optional[int] = None) -> None:
    """原子写入 JSON（中文不转义，便于人工核对数据文件）。

    Args:
        path: 目标文件路径。
        payload: 可序列化对象。
        indent: 缩进空格数。
        ensure_ascii: 是否转义非 ASCII 字符，缺省 False。
        mode: 可选权限位（如 0o600）。

    Raises:
        TypeError: 对象不可 JSON 序列化时抛出。
    """
    atomic_write_text(path, json.dumps(payload, ensure_ascii=ensure_ascii, indent=indent), mode=mode)


