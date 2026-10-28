# -*- coding: utf-8 -*-
"""用户功能开关（本地偏好，默认全关）。

用途：需要用户明确 opt-in 的增强能力（如「盘中估值」）统一在这里存取，
避免各模块各自读环境变量或写死常量。

存储：`data/user_prefs.json`，结构 `{"fund_estimation": false, ...}`，原子写。
约束：
1. 只允许**已登记**的键，未登记键读写均报错，防止拼写错误静默生效；
2. 默认值全部为关闭（合规优先：不主动向用户展示估算数据）；
3. 进程内缓存 + 写入即刷新，读路径无 IO。
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict, Optional

from backend.app import config
from backend.app.core import atomic

logger = logging.getLogger(__name__)

PREFS_FILE = config.DATA_DIR / "user_prefs.json"
_LOCK = threading.Lock()
_CACHE: Optional[Dict[str, Any]] = None

# 已登记开关及其默认值（新增开关必须先登记，避免拼写错误静默失效）
DEFAULTS: Dict[str, bool] = {
    # 盘中估值：默认关；开启后基金页展示东财/养基宝的当日估算（非官方，仅供参考）
    "fund_estimation": False,
}

# 已登记的字符串型偏好（R6-12：桌面壳关闭行为）。
# 与布尔开关同文件共存：桌面壳 desktop.py 直接读该文件的 close_action 决定
# 关闭窗口时是隐藏到托盘还是直接退出；默认 tray（用户 2026-10-29 裁决）。
STR_DEFAULTS: Dict[str, str] = {
    "close_action": "tray",
}

# 字符串型偏好的合法取值（写入前校验，非法值拒绝而非静默纠正）
STR_ALLOWED: Dict[str, tuple] = {
    "close_action": ("tray", "quit"),
}


def _load_disk() -> Dict[str, Any]:
    """从磁盘读取偏好（只保留已登记键，缺省用默认值）。

    Returns:
        dict: 完整偏好字典（布尔开关 + 字符串偏好）。
    """
    payload: Dict[str, Any] = dict(DEFAULTS)
    payload.update(STR_DEFAULTS)
    if not PREFS_FILE.exists():
        return payload
    try:
        raw = json.loads(PREFS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("偏好配置损坏，按默认值处理：%s（%s）", PREFS_FILE, type(exc).__name__)
        return payload
    if isinstance(raw, dict):
        for key in DEFAULTS:
            if isinstance(raw.get(key), bool):
                payload[key] = raw[key]
        for key, allowed in STR_ALLOWED.items():
            value = raw.get(key)
            if isinstance(value, str) and value in allowed:
                payload[key] = value
    return payload


def _snapshot() -> Dict[str, Any]:
    """进程内快照（懒加载）。

    Returns:
        dict: 偏好副本。
    """
    global _CACHE
    with _LOCK:
        if _CACHE is None:
            _CACHE = _load_disk()
        return dict(_CACHE)


def get_flag(name: str) -> bool:
    """读取单个开关（未登记返回 False 并告警）。

    Args:
        name: 开关名。

    Returns:
        bool: 是否开启。
    """
    if name not in DEFAULTS:
        logger.warning("未登记的功能开关：%s（按关闭处理）", name)
        return False
    return bool(_snapshot().get(name, DEFAULTS[name]))


def set_flag(name: str, value: bool) -> Dict[str, Any]:
    """写入单个开关并落盘。

    Args:
        name: 开关名（必须已在 DEFAULTS 登记）。
        value: 是否开启。

    Returns:
        dict: 落盘后的全部偏好。

    Raises:
        KeyError: 开关未登记。
        NonRetryableError: 磁盘写入失败。
    """
    global _CACHE
    if name not in DEFAULTS:
        raise KeyError(name)
    with _LOCK:
        current = _load_disk()
        current[name] = bool(value)
        try:
            atomic.atomic_write_json(PREFS_FILE, current, mode=0o600)
        except OSError as exc:
            from backend.app.core.errors import NonRetryableError

            raise NonRetryableError(f"偏好写入失败：{exc}") from exc
        _CACHE = current
        return dict(current)


def get_str(name: str) -> str:
    """读取字符串型偏好（未登记返回默认值并告警）。

    Args:
        name: 偏好键名。

    Returns:
        str: 当前取值。
    """
    if name not in STR_DEFAULTS:
        logger.warning("未登记的字符串偏好：%s（按默认值处理）", name)
        return ""
    return str(_snapshot().get(name, STR_DEFAULTS[name]))


def set_str(name: str, value: str) -> Dict[str, Any]:
    """写入字符串型偏好并落盘（取值必须在 STR_ALLOWED 白名单内）。

    Args:
        name: 偏好键名（必须已在 STR_DEFAULTS 登记）。
        value: 目标取值。

    Returns:
        dict: 落盘后的全部偏好。

    Raises:
        KeyError: 键未登记。
        ValueError: 取值不在白名单。
        NonRetryableError: 磁盘写入失败。
    """
    global _CACHE
    if name not in STR_DEFAULTS:
        raise KeyError(name)
    allowed = STR_ALLOWED.get(name, ())
    if value not in allowed:
        raise ValueError(f"{name} 仅支持 {'/'.join(allowed)}")
    with _LOCK:
        current = _load_disk()
        current[name] = value
        try:
            atomic.atomic_write_json(PREFS_FILE, current, mode=0o600)
        except OSError as exc:
            from backend.app.core.errors import NonRetryableError

            raise NonRetryableError(f"偏好写入失败：{exc}") from exc
        _CACHE = current
        return dict(current)
