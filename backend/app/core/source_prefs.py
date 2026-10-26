# -*- coding: utf-8 -*-
"""数据源启用偏好（R3-03 · 连接器可操作）。

用途：让用户在「数据连接器」页把某个源**停用**（例如同花顺 V8 崩溃频繁、
或某源在某些网络下长期超时），降级链会跳过它、自动落到下一优先源。

设计要点：
1. 唯一写入点 `data/connectors.json`，原子写（复用 core/atomic）；
2. 进程内缓存 + 变更即刷新，`with_fallback` 每次只做一次集合查表，无 IO；
3. **安全阀**：若某次降级链的全部候选源都被停用，则忽略偏好照常尝试，
   避免用户误操作把功能关死（宁可多试一次，也不返回空）；
4. 只按**源名精确匹配**（如「腾讯」「新浪」「同花顺」），不做模糊匹配，
   防止误伤同名子串。
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict, List, Optional, Set

from backend.app import config
from backend.app.core import atomic

logger = logging.getLogger(__name__)

CONNECTORS_FILE = config.DATA_DIR / "connectors.json"
_LOCK = threading.Lock()
_CACHE: Optional[Set[str]] = None  # 已停用的源名集合（进程内快照）


def _empty() -> Dict[str, Any]:
    """空配置结构。

    Returns:
        dict: {"disabled": []}。
    """
    return {"disabled": []}


def _load_disk() -> Dict[str, Any]:
    """从磁盘读取配置（损坏时按空配置处理）。

    Returns:
        dict: 规范化配置。
    """
    if not CONNECTORS_FILE.exists():
        return _empty()
    try:
        raw = json.loads(CONNECTORS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("连接器配置损坏，按空配置处理：%s（%s）", CONNECTORS_FILE, type(exc).__name__)
        return _empty()
    disabled = raw.get("disabled") if isinstance(raw, dict) else None
    if not isinstance(disabled, list):
        return _empty()
    return {"disabled": [str(item) for item in disabled if str(item or "").strip()]}


def disabled_sources() -> Set[str]:
    """当前已停用的源名集合（带进程内缓存）。

    Returns:
        set[str]: 源名集合；无配置时为空集。
    """
    global _CACHE
    with _LOCK:
        if _CACHE is None:
            _CACHE = set(_load_disk()["disabled"])
        return set(_CACHE)


def reload() -> Set[str]:
    """强制从磁盘重载（写配置后调用）。

    Returns:
        set[str]: 重载后的源名集合。
    """
    global _CACHE
    with _LOCK:
        _CACHE = set(_load_disk()["disabled"])
        return set(_CACHE)


def set_disabled(sources: List[str]) -> Set[str]:
    """整体替换停用源集合并落盘。

    Args:
        sources: 停用的源名列表。

    Returns:
        set[str]: 落盘后的源名集合。

    Raises:
        NonRetryableError: 磁盘写入失败。
    """
    global _CACHE
    cleaned = sorted({str(item).strip() for item in sources if str(item or "").strip()})
    payload = {"disabled": cleaned}
    with _LOCK:
        try:
            atomic.atomic_write_json(CONNECTORS_FILE, payload, mode=0o600)
        except OSError as exc:
            from backend.app.core.errors import NonRetryableError

            raise NonRetryableError(f"连接器配置写入失败：{exc}") from exc
        _CACHE = set(cleaned)
        return set(_CACHE)


def toggle(source: str, enabled: bool) -> Set[str]:
    """启用/停用单个源。

    Args:
        source: 源名（精确匹配，如「同花顺」）。
        enabled: True=启用（从停用集合移除），False=停用（加入集合）。

    Returns:
        set[str]: 更新后的停用源集合。
    """
    current = disabled_sources()
    name = str(source or "").strip()
    if not name:
        return current
    if enabled:
        current.discard(name)
    else:
        current.add(name)
    return set_disabled(sorted(current))


def filter_candidates(names: List[str]) -> List[str]:
    """按停用偏好过滤候选源名；全部被停用时返回原列表（安全阀）。

    Args:
        names: 候选源名（保持原顺序）。

    Returns:
        list[str]: 过滤后的源名列表。
    """
    disabled = disabled_sources()
    if not disabled:
        return list(names)
    kept = [name for name in names if name not in disabled]
    # 安全阀：全部停用视为误操作，忽略偏好，保证功能可用
    return kept or list(names)
