"""进程内存 TTL 缓存（B2 口径收编 · 2026-11-12）。

收编此前散落在 portfolio / fund / fund_combo / sector / capabilities 的
5 处手写「Dict + time.time() + TTL」模式缓存（同一模式各写一份、TTL 各异、
线程安全靠约定），统一为本模块的命名实例。

与 :mod:`core.cache` 的分工：
- ``core.cache``：两级缓存（内存 + 磁盘兜底），值必须可 JSON 序列化，
  写入经 ``is_meaningful`` 拦截空值——适合「上游接口响应」这类大对象；
- ``core.memcache``：纯进程内存小缓存，**允许缓存 None / 空表**（负缓存语义，
  如「个股行业不可得」也要记下，避免每次归因都重打源站），无磁盘 IO。

线程安全：每实例独立 Lock；全局实例由 :func:`get_cache` 工厂统一登记，
``core.cache.clear_cache()`` 会联动清空全部实例（/api/cache/clear 语义完整）。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Hashable, Optional, Tuple

logger = logging.getLogger(__name__)

_INSTANCES: Dict[str, "MemCache"] = {}
_REGISTRY_LOCK = threading.Lock()


class MemCache:
    """线程安全的进程内存 TTL 缓存（键值对，TTL 按读取时传入）。

    语义要点：
    - **允许存 None**：与 ``core.cache.set`` 的 is_meaningful 拦截不同，
      本类 set 不做真值过滤——负缓存（空结果）是部分调用方的明确需求；
    - TTL 在 ``get`` 时传入：同一键可在不同读取点按各自口径判过期，
      迁移自旧手写实现（如 fund_combo 费率表整体 24h、sector 腾讯板块 10 分钟）；
    - 命中判定使用 ``key in store``（与旧实现一致，可区分「存了 None」与「没存」）。
    """

    def __init__(self, name: str) -> None:
        """初始化命名实例。

        Args:
            name: 实例名（用于日志与 stats 汇总，如 "stock_industry"）。
        """
        self.name = name
        self._store: Dict[Hashable, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: Hashable, ttl: int) -> Optional[Any]:
        """读取缓存（过期即丢弃）。

        Args:
            key: 缓存键（任意可哈希对象）。
            ttl: 有效期（秒）。

        Returns:
            Optional[Any]: 命中返回存储值；未命中/过期返回 None。
            「存了 None」与「未命中」的区分请用 :meth:`contains`（负缓存语义）。
        """
        now = time.time()
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            ts, value = item
            if now - ts <= ttl:
                return value
            self._store.pop(key, None)  # 过期即移除，防无限增长
            return None

    def contains(self, key: Hashable, ttl: int) -> bool:
        """判断键是否命中（含负缓存条目），供「None 也是有效值」的调用方使用。

        Args:
            key: 缓存键。
            ttl: 有效期（秒）。

        Returns:
            bool: 存在且未过期返回 True。
        """
        now = time.time()
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return False
            if now - item[0] > ttl:
                self._store.pop(key, None)
                return False
            return True

    def set(self, key: Hashable, value: Any) -> None:
        """写入缓存（不做真值过滤，允许 None / 空容器）。"""
        with self._lock:
            self._store[key] = (time.time(), value)

    def get_or_load(self, key: Hashable, ttl: int, loader: Callable[[], Any]) -> Any:
        """缓存优先；未命中调 loader 并写入（含 None——loader 结果总是被缓存）。

        Args:
            key: 缓存键。
            ttl: 有效期（秒）。
            loader: 无参取数函数。

        Returns:
            Any: 缓存值或 loader 结果。
        """
        if self.contains(key, ttl):
            return self.get(key, ttl)
        value = loader()
        self.set(key, value)
        return value

    def clear(self) -> int:
        """清空本实例。

        Returns:
            int: 清除的条目数。
        """
        with self._lock:
            count = len(self._store)
            self._store.clear()
        return count

    def stats(self) -> Dict[str, Any]:
        """返回本实例规模（条目数 + 字节估算，供设置页 S-03 展示）。

        Returns:
            Dict[str, Any]: ``{name, entries, bytes}``。
        """
        import json

        with self._lock:
            entries = len(self._store)
            total = 0
            for key, (_, value) in self._store.items():
                try:
                    total += len(json.dumps({"k": str(key), "v": value}, default=str))
                except Exception:  # noqa: BLE001 - 不可序列化条目按 64 字节估
                    total += 64
        return {"name": self.name, "entries": entries, "bytes": total}


def get_cache(name: str) -> MemCache:
    """按名取缓存实例（不存在则创建并登记）。

    Args:
        name: 实例名。同一 name 全进程共享一个实例（模块级调用点请传常量）。

    Returns:
        MemCache: 命名实例。
    """
    with _REGISTRY_LOCK:
        instance = _INSTANCES.get(name)
        if instance is None:
            instance = MemCache(name)
            _INSTANCES[name] = instance
            logger.info("MemCache 实例已创建：%s", name)
        return instance


def clear_all() -> int:
    """清空全部命名实例（由 core.cache.clear_cache 联动调用）。

    Returns:
        int: 全部清除的条目数。
    """
    with _REGISTRY_LOCK:
        instances = list(_INSTANCES.values())
    return sum(instance.clear() for instance in instances)


def stats_all() -> list:
    """全部实例的规模明细（设置页 S-03 缓存占用展示）。"""
    with _REGISTRY_LOCK:
        instances = list(_INSTANCES.values())
    return [instance.stats() for instance in instances]
