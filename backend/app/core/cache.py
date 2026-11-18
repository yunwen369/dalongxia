"""两级缓存模块（内存 TTL + 磁盘兜底）。

对应规范 v7.0 §6.5 / §6.8：
1. 内存缓存使用 threading.Lock 保护，命中且未过期直接返回；
2. 磁盘缓存位于 data/cache/<md5(key)>.json，用于上游异常时兜底；
3. is_meaningful() 拦截空值与错误结构，防止一次空结果永久污染缓存；
4. clear_cache() 同时清空内存与磁盘，供 /api/cache/clear 调用。

注意：缓存值必须可 JSON 序列化；DataFrame 请先在 provider 层转为 dict/list。
"""

from __future__ import annotations

import builtins
import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from backend.app import config
from backend.app.core import atomic

logger = logging.getLogger(__name__)

_MEMORY: Dict[str, Tuple[float, Any]] = {}  # 键 -> (写入时间戳, 值)
_LOCK = threading.Lock()  # 保护内存字典的并发读写


def is_meaningful(value: Any) -> bool:
    """判断缓存值是否有效，拦截空值与错误结构。

    Args:
        value: 待判断的缓存值。

    Returns:
        bool: 有效返回 True；None/空列表/空字符串/空字典/含 error 的字典返回 False。
    """
    if value is None:
        return False
    # 注意：本模块定义了同名函数 ``set``，直接写 ``isinstance(..., set)`` 会把函数
    # 当成类型传进去——对 str/list/tuple/dict 因先命中前面的类型而不报错，只有
    # frozenset、int 等类型才会走到它并抛 TypeError，属于隐蔽缺陷。
    # 故此处用 ``builtins.set`` 显式指向内建类型。
    if isinstance(value, (str, list, tuple, dict, builtins.set, frozenset)) and len(value) == 0:
        return False
    if isinstance(value, dict) and value.get("error"):
        return False
    # pandas.DataFrame 等对象带 empty 属性时一并拦截
    empty_attr = getattr(value, "empty", None)
    if isinstance(empty_attr, bool) and empty_attr:
        return False
    return True


def _disk_path(key: str) -> Path:
    """把缓存键映射为磁盘文件路径（md5 避免中文与非法字符）。

    Args:
        key: 缓存键 / 字典键。

    Returns:
        Path: 文件路径。
    """
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()
    return config.CACHE_DIR / f"{digest}.json"


def _read_disk(key: str) -> Optional[Tuple[float, Any]]:
    """读取磁盘缓存，返回 (时间戳, 值)；失败时返回 None。

    Args:
        key: 缓存键 / 字典键。

    Returns:
        Optional[Tuple[float, Any]]: 可能为 None。
    """
    path = _disk_path(key)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        timestamp = float(payload["ts"])
        return timestamp, payload.get("value")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning("读取磁盘缓存失败：%s（%s）", path.name, type(exc).__name__)
        return None


def _write_disk(key: str, timestamp: float, value: Any) -> bool:
    """原子写入磁盘缓存，成功返回 True。

    Args:
        key: 缓存键 / 字典键。
        timestamp: 时间戳（秒）。
        value: 待处理值（类型由调用方约定）。

    Returns:
        bool: 判定结果。
    """
    path = _disk_path(key)
    try:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        text = json.dumps(
            {"ts": timestamp, "value": value},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        # 统一走 core/atomic：同目录临时文件 + os.replace，与其它落盘点同一实现
        atomic.atomic_write_text(path, text)
        return True
    except (OSError, TypeError, ValueError) as exc:
        # TypeError 通常来自 DataFrame 等不可序列化对象，仅记录类型不打印值
        logger.warning("写入磁盘缓存失败：%s（%s）", path.name, type(exc).__name__)
        return False


def get(key: str, ttl: int, *, allow_stale: bool = False) -> Optional[Any]:
    """读取缓存。

    Args:
        key: 缓存键。
        ttl: 有效期（秒）。
        allow_stale: 为 True 时允许返回已过期但仍在磁盘上的旧值（降级兜底）。

    Returns:
        Optional[Any]: 命中返回值；未命中或已过期返回 None。
    """
    now = time.time()
    with _LOCK:
        memory_item = _MEMORY.get(key)
        if memory_item is not None:
            timestamp, value = memory_item
            if now - timestamp <= ttl:
                return value
            _MEMORY.pop(key, None)  # 内存过期即移除，避免无限增长

    disk_item = _read_disk(key)
    if disk_item is None:
        return None
    timestamp, value = disk_item
    if now - timestamp <= ttl:
        with _LOCK:
            _MEMORY[key] = (timestamp, value)  # 回填内存，减少磁盘 IO
        return value
    if allow_stale and is_meaningful(value):
        return value
    return None


def set(key: str, value: Any, ttl: Optional[int] = None) -> bool:  # noqa: ARG001
    """写入缓存（内存 + 磁盘）。

    Args:
        key: 缓存键。
        value: 缓存值，必须可 JSON 序列化才能落盘。
        ttl: 预留参数，当前由读取方按数据类传入；写入时不裁剪。

    Returns:
        bool: 有效值写入成功返回 True；空值或写入失败返回 False。
    """
    if not is_meaningful(value):
        # 关键防污染：空值绝不写入，避免后续正常数据被旧空结果遮蔽
        return False
    timestamp = time.time()
    with _LOCK:
        _MEMORY[key] = (timestamp, value)
    _write_disk(key, timestamp, value)
    return True


def get_or_load(key: str, ttl: int, loader: Callable[[], Any],
                stale_ok: Optional[Callable[[BaseException], bool]] = None) -> Any:
    """缓存优先读取；未命中时调用 loader，失败则回退磁盘旧值。

    Args:
        key: 缓存键。
        ttl: 有效期（秒）。
        loader: 上游取数函数，无参数、返回可缓存值。
        stale_ok: 可选判定函数，入参为 loader 抛出的异常，返回 False 表示
            **禁止**用磁盘旧值兜底。用于「凭据失效」这类必须让用户看见的错误：
            拿旧数据糊过去会让用户误以为仍处于正常状态（§3.4 401 需引导重新授权）。

    Returns:
        Any: 缓存值或 loader 返回值。

    Raises:
        Exception: 上游失败且（无可用磁盘兜底 或 被 `stale_ok` 拒绝）时，
            原样抛出由路由层统一处理。
    """
    cached = get(key, ttl)
    if cached is not None:
        return cached

    try:
        value = loader()
    except Exception as exc:
        if stale_ok is not None and not stale_ok(exc):
            raise
        stale = get(key, ttl, allow_stale=True)
        if stale is not None:
            logger.warning("上游取数失败，已使用磁盘缓存兜底：key=%s", key)
            return stale
        raise

    set(key, value)
    return value


def clear_cache() -> int:
    """清空内存与磁盘缓存。

    Returns:
        int: 删除的磁盘缓存文件数量。
    """
    with _LOCK:
        _MEMORY.clear()

    # B2（2026-11-12）：联动清空 core/memcache 全部命名实例
    #（个股行业/通道探测/费率表等进程内存小缓存），/api/cache/clear 语义完整。
    try:
        from backend.app.core import memcache

        mem_clear = memcache.clear_all()
        if mem_clear:
            logger.info("已联动清空 MemCache 条目 %d 个", mem_clear)
    except Exception as exc:  # noqa: BLE001 - 联动失败不影响主清空
        logger.warning("MemCache 联动清空失败：%s", type(exc).__name__)

    removed = 0
    if config.CACHE_DIR.is_dir():
        for path in config.CACHE_DIR.glob("*.json"):
            try:
                path.unlink()
                removed += 1
            except OSError as exc:
                logger.warning("删除缓存文件失败：%s（%s）", path.name, type(exc).__name__)
    logger.info("缓存已清空，删除磁盘文件 %d 个", removed)
    return removed


def stats() -> Dict[str, int]:
    """返回缓存规模，供诊断使用。

    Returns:
        Dict[str, int]: 内存条目数与磁盘文件数。
    """
    with _LOCK:
        memory_count = len(_MEMORY)
    disk_count = len(list(config.CACHE_DIR.glob("*.json"))) if config.CACHE_DIR.is_dir() else 0
    return {"memory": memory_count, "disk": disk_count}


# 缓存 TTL 口径表（R3-13 设置页 S-03 展示；与 07_数据 数据字典 §9 同源，改此处须同步字典）
CACHE_TTL: tuple = (
    ("realtime_quote / index_quote", 15, "实时报价（个股/指数）"),
    ("minute", 30, "分时"),
    ("kline", "60 / 900", "K 线（盘中 60；盘后与休市 900，日K收盘后不再变化）"),
    ("sector / fund_flow", "30 / 60", "板块与资金流"),
    ("news_flash / sentiment", "60 / 300", "快讯与情绪"),
    ("fund_nav", 300, "基金净值"),
    ("profile / financial / estimates", "3600 / 3600 / 1800", "概况、财报、一致预期"),
    ("search", 600, "搜索联想"),
    ("yjb:collect / yjb:holdings:*", 30, "养基宝账户汇总与持仓（账户级接口不宜高频）"),
    ("memcache:*（行业/探测/费率等）", "见 config.TTL", "进程内 MemCache 命名实例（B2 口径收编，config.TTL 统一登记）"),
)


def stats_detail() -> Dict[str, Any]:
    """返回缓存占用明细（R3-13 设置页 S-03：内存/磁盘/布局字节数与 TTL 表）。

    Returns:
        Dict[str, Any]: `{memory, disk, memory_bytes, disk_bytes, layout_bytes, ttl_map}`。

    Notes:
        - 内存字节数按 JSON 序列化长度估算（含 key），只用于展示量级；
        - 布局字节数取 ``data/dashboards.json``（仪表板布局持久化文件）；
        - TTL 表取自 :data:`CACHE_TTL`，与数据字典 §9 同源。
    """
    with _LOCK:
        memory_count = len(_MEMORY)
        memory_bytes = 0
        skipped = 0
        for key, payload in _MEMORY.items():
            try:
                memory_bytes += len(json.dumps({"k": key, "v": payload}, ensure_ascii=False, default=str))
            except Exception:  # noqa: BLE001 - 不可序列化条目跳过，不影响统计
                skipped += 1
        if skipped:
            logger.info("缓存统计跳过 %d 个不可序列化条目（不影响计数）", skipped)

    disk_count = 0
    disk_bytes = 0
    if config.CACHE_DIR.is_dir():
        for path in config.CACHE_DIR.glob("*.json"):
            try:
                disk_bytes += path.stat().st_size
                disk_count += 1
            except OSError:
                continue

    layout_bytes = 0
    layout_file = config.DATA_DIR / "dashboards.json"
    if layout_file.is_file():
        try:
            layout_bytes = layout_file.stat().st_size
        except OSError:
            layout_bytes = 0

    return {
        "memory": memory_count,
        "disk": disk_count,
        "memory_bytes": memory_bytes,
        "disk_bytes": disk_bytes,
        "layout_bytes": layout_bytes,
        "ttl_map": [{"key": key, "ttl": ttl, "note": note} for key, ttl, note in CACHE_TTL],
    }
