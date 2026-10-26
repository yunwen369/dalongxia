# -*- coding: utf-8 -*-
"""Windows DPAPI 安全存储（P1-15 / 规范 §5.6、§12.3）。

职责：把「密钥类数据不得明文落盘」收敛为唯一实现，供
① AI 接口密钥（data/ai_config.json 的 api_key 字段）
② 养基宝 token（data/yjb_token.json 全文）
共同复用。

约束：
1. 仅 Windows + crypt32 可用时允许加密；不可用时**拒绝写明文**并抛错，
   由调用方转成中文文案（禁止静默降级为明文）；
2. 加解密绑定当前 Windows 账户（DPAPI 用户级），换账户/换机器无法解密；
3. 本模块不读配置、不打日志内容，避免密钥进入日志。
"""

from __future__ import annotations

import base64
import ctypes
import json
import platform
from pathlib import Path
from typing import Any, Dict, Optional

from backend.app.core import atomic

# 文本密钥（如 AI api_key）的密文前缀：便于与历史明文区分
SECRET_PREFIX = "ENC1:"


class _DataBlob(ctypes.Structure):
    """Windows DPAPI DATA_BLOB 结构。"""

    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def available() -> bool:
    """判断当前环境是否支持 DPAPI 加密。

    Returns:
        bool: Windows 且 crypt32 可加载返回 True。
    """
    if platform.system() != "Windows":
        return False
    try:
        ctypes.windll.crypt32  # noqa: B018 - 探测可用性
        return True
    except Exception:  # noqa: BLE001 - 非 Windows 或缺 DLL
        return False


def _dpapi(protect: bool, data: bytes) -> bytes:
    """调用 DPAPI 加/解密原始字节。

    Args:
        protect: True=加密，False=解密。
        data: 待处理字节。

    Returns:
        bytes: DPAPI 处理后的字节（protect=True 为密文，False 为明文）。

    Raises:
        OSError: DPAPI 调用失败（权限/数据损坏）。
    """
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DataBlob()
    flags = 0x1 if protect else 0x0  # CRYPTPROTECT_UI_FORBIDDEN：不弹 UI
    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None, flags, ctypes.byref(blob_out)
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, flags, ctypes.byref(blob_out)
        )
    if not ok:
        raise OSError("DPAPI 操作失败")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def encrypt_text(text: str) -> str:
    """加密一段文本，返回带前缀的 base64 密文。

    Args:
        text: 明文（如 API Key）。

    Returns:
        str: ``ENC1:`` + base64(DPAPI 密文)。

    Raises:
        OSError: 当前环境不支持 DPAPI 或加密失败。
    """
    if not available():
        raise OSError("当前系统不支持安全存储（DPAPI 不可用）")
    return SECRET_PREFIX + base64.b64encode(_dpapi(True, text.encode("utf-8"))).decode("ascii")


def decrypt_text(token: str) -> Optional[str]:
    """解密 :func:`encrypt_text` 生成的密文。

    Args:
        token: 带 ``ENC1:`` 前缀的密文。

    Returns:
        Optional[str]: 明文；前缀不符/损坏/换账户时返回 None（调用方按未配置处理）。
    """
    if not token or not token.startswith(SECRET_PREFIX):
        return None
    try:
        return _dpapi(False, base64.b64decode(token[len(SECRET_PREFIX):])).decode("utf-8")
    except Exception:  # noqa: BLE001 - 损坏密文不视为可用密钥
        return None


def is_encrypted(value: Any) -> bool:
    """判断字段是否为密文格式（用于迁移历史明文）。

    Args:
        value: 待判断值。

    Returns:
        bool: 以 ``ENC1:`` 开头返回 True。
    """
    return isinstance(value, str) and value.startswith(SECRET_PREFIX)


def write_secure(path: Path, payload: Dict[str, Any], magic: bytes) -> None:
    """把字典整体加密后写入文件（养基宝 token 沿用此格式）。

    Args:
        path: 目标文件路径。
        payload: 待落盘字典（不得含不可 JSON 序列化对象）。
        magic: 文件头魔数（如 b"YJB1"），用于识别加密文件。

    Raises:
        OSError: DPAPI 不可用或写入失败。
    """
    if not available():
        raise OSError("当前系统不支持安全存储（DPAPI 不可用），已拒绝明文落盘")
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    blob = magic + base64.b64encode(_dpapi(True, raw))
    # R3-09：使用统一原子写助手（含 fsync + 0o600 权限），替代手动 temp + replace + chmod
    atomic.atomic_write_bytes(path, blob, mode=0o600)


def read_secure(path: Path, magic: bytes) -> Optional[Dict[str, Any]]:
    """读取 :func:`write_secure` 写入的文件。

    Args:
        path: 文件路径。
        magic: 需要匹配的文件头魔数。

    Returns:
        Optional[Dict[str, Any]]: 解密后的字典；缺失/损坏/魔数不符返回 None。
    """
    if not path.exists():
        return None
    try:
        blob = path.read_bytes()
        if not blob.startswith(magic):
            return None
        data = json.loads(_dpapi(False, base64.b64decode(blob[len(magic):])).decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 - 损坏按不可用处理
        return None
