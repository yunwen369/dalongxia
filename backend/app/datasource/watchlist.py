# -*- coding: utf-8 -*-
"""自选股本地持久化（W 自选股 · P0）。

设计基准：`新增功能设计基准_20261022.md` W-04。
存储：`data/watchlist.json`，单用户、无成本字段，与「持仓」（成本盈亏台账）严格分离。
能力：增（去重）/删/查/改备注名，跨四市场（a/hk/us/fund），上限 100 条。

约束（规范 §6.5 写副作用）：
1. 读-改-写全程持进程内锁，写入用 atomic.atomic_write_json 原子替换（R3-09 收口）；
2. 文件损坏按空表处理并告警，不静默覆盖用户数据（先备份为 .bad）；
3. 仅存关注清单元数据，不存行情快照（行情由路由层实时取）。
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

from backend.app import config
from backend.app.core.errors import NonRetryableError
from backend.app.datasource import utils
from backend.app.core import atomic

logger = logging.getLogger(__name__)

WATCHLIST_FILE = config.DATA_DIR / "watchlist.json"
MAX_ITEMS = 100  # 单用户自选上限，防止误导入超大清单拖垮行情批量
_LOCK = threading.Lock()
_ALLOWED_MARKETS = utils.MARKET_WHITELIST_WATCHLIST


def _empty() -> Dict[str, Any]:
    """空的清单结构。

    Returns:
        dict: {"items": [], "updated_at": ""}。
    """
    return {"items": [], "updated_at": ""}


def _read_raw() -> Dict[str, Any]:
    """读取磁盘清单（损坏时备份并返回空表）。

    Returns:
        dict: 清单结构。
    """
    if not WATCHLIST_FILE.exists():
        return _empty()
    try:
        payload = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("自选清单读取失败：%s（%s），已备份为 .bad", WATCHLIST_FILE.name, type(exc).__name__)
        try:
            WATCHLIST_FILE.replace(WATCHLIST_FILE.with_suffix(".json.bad"))
        except OSError:
            pass
        return _empty()
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return _empty()
    payload.setdefault("updated_at", "")
    return payload


def _write_raw(payload: Dict[str, Any]) -> None:
    """原子写入清单。

    Args:
        payload: 完整清单结构。

    Raises:
        NonRetryableError: 写入失败（磁盘只读/权限不足）。
    """
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        atomic.atomic_write_json(WATCHLIST_FILE, payload, ensure_ascii=False, indent=2)
    except OSError as exc:
        raise NonRetryableError(f"自选清单写入失败：{exc}") from exc


def list_items() -> List[Dict[str, Any]]:
    """返回全部自选项（按加入时间正序）。

    Returns:
        list[dict]: [{symbol, market, name, added_at}]。
    """
    with _LOCK:
        return list(_read_raw()["items"])


def add_item(symbol: str, market: str, name: str = "", note: str = "") -> Tuple[str, Dict[str, Any]]:
    """新增自选项（幂等去重）。

    Args:
        symbol: 归一化后的代码。
        market: a/hk/us/fund。
        name: 展示名称（可空，前端可后续补）。
        note: 用户备注。

    Returns:
        Tuple[str, dict]: ("created"|"exists", 条目)。

    Raises:
        NonRetryableError: 超过上限或写入失败。
        ValueError: 市场不合法。
    """
    normalized = utils.normalize_market(market, extra=("fund",))
    if normalized is None:
        raise ValueError("market 仅支持 a/hk/us/fund（cn 为 a 的兼容别名）")
    with _LOCK:
        payload = _read_raw()
        items = payload["items"]
        for item in items:
            if item.get("symbol") == symbol and item.get("market") == normalized:
                if note:
                    item["note"] = note
                    payload["updated_at"] = utils.now_text()
                    _write_raw(payload)
                return "exists", item
        if len(items) >= MAX_ITEMS:
            raise NonRetryableError(f"自选上限 {MAX_ITEMS} 条，请先移除部分标的")
        entry = {
            "symbol": symbol,
            "market": normalized,
            "name": name or "",
            "note": note or "",
            "added_at": utils.now_text(),
        }
        items.append(entry)
        payload["updated_at"] = entry["added_at"]
        _write_raw(payload)
        return "created", entry


def remove_item(symbol: str, market: str) -> bool:
    """移除自选项。

    Args:
        symbol: 代码。
        market: 市场。

    Returns:
        bool: 是否删除了既有条目。
    """
    normalized = (market or "a").lower()
    with _LOCK:
        payload = _read_raw()
        before = len(payload["items"])
        payload["items"] = [
            item for item in payload["items"]
            if not (item.get("symbol") == symbol and item.get("market") == normalized)
        ]
        removed = len(payload["items"]) != before
        if removed:
            payload["updated_at"] = utils.now_text()
            _write_raw(payload)
        return removed


def rename_item(symbol: str, market: str, name: str) -> Optional[Dict[str, Any]]:
    """修改自选项展示名（用于源站无名时的补充）。

    Args:
        symbol: 代码。
        market: 市场。
        name: 新名称。

    Returns:
        Optional[dict]: 更新后的条目；未找到返回 None。
    """
    normalized = (market or "a").lower()
    with _LOCK:
        payload = _read_raw()
        for item in payload["items"]:
            if item.get("symbol") == symbol and item.get("market") == normalized:
                item["name"] = name or item.get("name") or ""
                payload["updated_at"] = utils.now_text()
                _write_raw(payload)
                return item
        return None


def store_path() -> str:
    """返回清单文件绝对路径（供健康检查/测试断言）。

    Returns:
        str: 文件路径。
    """
    return str(WATCHLIST_FILE)
