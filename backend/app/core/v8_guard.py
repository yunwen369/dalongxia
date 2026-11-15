# -*- coding: utf-8 -*-
"""进程级 V8 互斥守卫（同花顺 hexin-v 签名专用）。

**背景（阶段 8 实测实锤，2026-10-21）**
akshare 1.19.1 在 Windows 平台依赖 py_mini_racer（内嵌 V8）：同花顺系接口
（`stock_board_*_ths` / `stock_fund_flow_industry` / `stock_info_global_ths`
等）内部每次调用都会新建 MiniRacer 上下文执行 hexin-v 签名 JS。
V8 的 PartitionAlloc 在「多线程并发首次创建」时会双初始化，直接 FATAL：

    [FATAL:partition_address_space.cc(243)]
    Check failed: !IsConfigurablePoolInitialized().

实测：
  - 4 线程并发首次调用不同 ths 接口 → **必崩**（进程原生死亡，无 Python 栈）；
  - 串行调用偶发崩（GC 析构时序，概率约 30%）；
  - 干净进程串行调用 → 正常（90 行数据）。

**影响**：uvicorn worker 被原生崩溃带走，浏览器端所有请求连带 error 态
（阶段 8 界面取证 12 视图全 error 的根因，一度被误判为前端缺陷）。

**措施**：进程内所有 akshare 同花顺 V8 类调用经本锁串行化，从根上消除
「并发双初始化」这一主路径。锁为进程级（非 asyncio），可安全用于 sync def
端点（anyio 工作线程）与线程池。

**边界**：本锁只防并发触发，不改变数据口径；锁外仍保留 resilient 的
重试/降级链。残余偶发崩溃（串行 GC 时序）登记为环境级风险，
兜底方案见 docs/风险登记册 R-012；若后续频发，改走子进程隔离取数。

**第五轮修复（2026-10-27，P1「假绿」）**
原实现 ``except Exception: _record_failure()`` 把**一切异常**都算作 V8 引擎故障，
包括「板块名不存在」这类业务性 ``KeyError``；连续 3 次即进程级熔断，且熔断后
失败计数永不清零、无冷却、无半开入口 —— 只能重启进程才能恢复。
实测：2 个不存在的板块名请求即打挂整条同花顺链路
（板块资金流 90 行 → 0 行 + ``UPSTREAM_ERROR``，板块 K 线静默切申万）。
修复三点：
1. **归因分层**：业务性异常（``KeyError`` / ``ValueError`` / ``TypeError`` /
   ``IndexError`` / ``LookupError`` / ``AttributeError`` / ``NonRetryableError``）
   不计入健康失败；仅引擎类异常（RuntimeError / OSError 等）计数；
2. **熔断可恢复**：``_V8_FAIL_WINDOW`` 秒滑动窗口内累计 ``_V8_MAX_FAILS`` 次才熔断；
   熔断后冷却 ``_V8_COOLDOWN`` 秒自动半开，放行单次试探 —— 成功清零、失败立即重新熔断；
3. **状态可观测**：``v8_health()`` 增加熔断字段，并由 ``/api/health`` 的 ``v8`` 字段暴露。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Tuple, TypeVar

from backend.app import config
from backend.app.core.errors import NonRetryableError

logger = logging.getLogger(__name__)

T = TypeVar("T")

# 进程级可重入锁：同花顺 V8 调用串行化。RLock 允许 provider 内部
# 嵌套包装（_call → _call_ths → loader）不自杀。
_V8_LOCK = threading.RLock()

# 状态锁：只保护下面几个标量的读写，持锁时间极短（不覆盖网络调用）。
_V8_STATE_LOCK = threading.Lock()

# 健康状态（进程级，随进程销毁）
_V8_SUCCESS_AT = 0.0          # 最近一次引擎调用成功的时间戳
_V8_FAIL_STREAK = 0           # 当前窗口内的引擎失败次数
_V8_FAIL_WINDOW_START = 0.0   # 当前失败窗口起点
_V8_OPEN_UNTIL = 0.0          # 熔断到期时间戳（0=未熔断）
_V8_PROBE_PENDING = False     # 半开试探是否已放行（true 时下一次失败立即重熔断）

# 熔断参数（第五轮：窗口 + 冷却半开，避免永久熔断）
_V8_MAX_FAILS = 3
_V8_FAIL_WINDOW = 120.0
_V8_COOLDOWN = 60.0

# 时钟别名：便于测试注入（monkeypatch ``v8_guard._NOW``）
_NOW = time.time

# 业务性异常：源站查无此物 / 数据解析不符，**不代表 V8 引擎故障**
_BUSINESS_EXCEPTIONS: Tuple[type, ...] = (
    KeyError,
    IndexError,
    ValueError,
    TypeError,
    AttributeError,
    LookupError,   # KeyError/IndexError 的基类，显式列出以表达语义
    NonRetryableError,
)


def _is_engine_fault(exc: BaseException) -> bool:
    """判断异常是否属于 V8 引擎/通道故障（需计入健康失败）。

    Args:
        exc: 捕获到的异常实例。

    Returns:
        bool: True=引擎类故障（计数）；False=业务性异常（不计数）。
    """
    return not isinstance(exc, _BUSINESS_EXCEPTIONS)


def v8_health() -> Dict[str, Any]:
    """返回 V8 通道当前健康状态（供 /api/health 与日志取证）。

    Returns:
        dict: 含 enabled / fail_streak / circuit_open / open_remaining /
             probe_pending / last_ok_age / window_seconds / cooldown_seconds /
             max_fails。enabled=False 表示冻结形态已禁用。
    """
    with _V8_STATE_LOCK:
        streak = _V8_FAIL_STREAK
        open_until = _V8_OPEN_UNTIL
        probe_pending = _V8_PROBE_PENDING
    now = _NOW()
    return {
        "enabled": bool(config.V8_SOURCES_ENABLED),
        "fail_streak": streak,
        "circuit_open": open_until > now,
        "open_remaining": round(max(0.0, open_until - now), 1) if open_until else 0.0,
        "probe_pending": probe_pending,
        "last_ok_age": round(now - _V8_SUCCESS_AT, 1) if _V8_SUCCESS_AT else None,
        "window_seconds": _V8_FAIL_WINDOW,
        "cooldown_seconds": _V8_COOLDOWN,
        "max_fails": _V8_MAX_FAILS,
    }


def _record_failure(exc: BaseException) -> None:
    """记录一次引擎失败；业务性异常只记 info，不计入熔断。

    Args:
        exc: 导致失败的异常实例。

    Returns:
        None.
    """
    global _V8_FAIL_STREAK, _V8_FAIL_WINDOW_START, _V8_OPEN_UNTIL, _V8_PROBE_PENDING
    if not _is_engine_fault(exc):
        with _V8_STATE_LOCK:
            _V8_PROBE_PENDING = False
        logger.info("V8 业务性异常不计入健康失败：%s", type(exc).__name__)
        return

    now = _NOW()
    with _V8_STATE_LOCK:
        if _V8_PROBE_PENDING:
            # 半开试探失败：立即重新熔断，避免反复试探冲击源站
            _V8_FAIL_STREAK = _V8_MAX_FAILS
            _V8_OPEN_UNTIL = now + _V8_COOLDOWN
            _V8_PROBE_PENDING = False
            streak = _V8_FAIL_STREAK
        else:
            if now - _V8_FAIL_WINDOW_START > _V8_FAIL_WINDOW:
                _V8_FAIL_WINDOW_START = now
                _V8_FAIL_STREAK = 0
            _V8_FAIL_STREAK += 1
            streak = _V8_FAIL_STREAK
            if streak >= _V8_MAX_FAILS:
                _V8_OPEN_UNTIL = now + _V8_COOLDOWN
    logger.warning(
        "V8 引擎异常 %s，窗口内连续 %d 次（阈值 %d）",
        type(exc).__name__, streak, _V8_MAX_FAILS,
    )
    if streak >= _V8_MAX_FAILS:
        logger.error("V8 通道熔断，冷却 %.0f 秒后半开重试", _V8_COOLDOWN)


def _record_success() -> None:
    """记录一次引擎调用成功，清零失败计数并关闭熔断。"""
    global _V8_FAIL_STREAK, _V8_FAIL_WINDOW_START, _V8_SUCCESS_AT, _V8_OPEN_UNTIL, _V8_PROBE_PENDING
    with _V8_STATE_LOCK:
        _V8_FAIL_STREAK = 0
        _V8_FAIL_WINDOW_START = 0.0
        _V8_OPEN_UNTIL = 0.0
        _V8_PROBE_PENDING = False
    _V8_SUCCESS_AT = _NOW()


def reset_state() -> None:
    """重置 V8 健康状态（测试隔离与运维诊断用，不改动串行化锁）。

    Returns:
        None.

    Notes:
        第五轮 P1-01 配套：熔断状态是进程级的，跨用例会互相污染 —— 之前
        ``conftest`` 未重置它，正是「测试全绿但真实服务会被打挂」的成因之一。
    """
    global _V8_FAIL_STREAK, _V8_FAIL_WINDOW_START, _V8_OPEN_UNTIL, _V8_PROBE_PENDING
    global _V8_SUCCESS_AT
    with _V8_STATE_LOCK:
        _V8_FAIL_STREAK = 0
        _V8_FAIL_WINDOW_START = 0.0
        _V8_OPEN_UNTIL = 0.0
        _V8_PROBE_PENDING = False
        _V8_SUCCESS_AT = 0.0


def serial_v8(func: Callable[..., T]) -> Callable[..., T]:
    """把函数包进 V8 串行锁，但**不做冻结禁用判断、不计入健康熔断**。

    与 :func:`guard_v8` 的分工（两者共用同一把 ``_V8_LOCK``，彼此也互斥）：

    - ``guard_v8``：用于 ths hexin-v 签名这类**冻结形态按规范 §13.6-2 应禁用**的源，
      额外带「冻结即拒」与「连续失败熔断」；
    - ``serial_v8``：用于「py_mini_racer 是合法依赖、只是不允许并发初始化」的通道
      （典型：akshare 新浪日线 ``stock_zh_a_daily``，v3.6.1 已把 dll 打进产物）。
      这类源在冻结形态下也要能用，所以不能套 ``guard_v8`` 的冻结判断。

    **为什么必须加锁**（2026-11-15 实测实锤）：V8 的 PartitionAlloc 在「多线程并发
    首次创建」时双初始化，直接 FATAL 带走进程：

        [FATAL:partition_address_space.cc(243)]
        Check failed: !IsConfigurablePoolInitialized().

    触发场景：``_constituent_correlation`` 把 20 只样本 K 线改成 8 路并发后，东财通道
    一旦 ``ConnectionError`` 就会降级到新浪；8 个线程同时首次初始化 V8 ⇒ 源码实例
    实测**整进程崩溃**（uvicorn 退出码 3）。串行时代不会并发首初始化，所以这个缺口
    一直没暴露。

    Args:
        func: 无参 loader（lambda），内部会触发 py_mini_racer 路径。

    Returns:
        加锁后的函数；异常原样透传。
    """

    def wrapper(*args, **kwargs) -> T:
        """加锁执行原函数。

        Returns:
            T: 计算结果（透传 func 返回值）。
        """
        with _V8_LOCK:
            return func(*args, **kwargs)

    wrapper.__name__ = getattr(func, "__name__", "wrapper")
    wrapper.__doc__ = func.__doc__
    return wrapper


def guard_v8(func: Callable[..., T]) -> Callable[..., T]:
    """把函数调用包进进程级 V8 互斥锁（同步阻塞语义）。

    冻结形态（PyInstaller onedir）下直接拒绝：规范 §13.6-2 要求打包后禁用
    一切依赖 py_mini_racer 的 akshare 接口，由纯 HTTP 备源降级承接。

    Args:
        func: 无参 loader（lambda），内部会触发 akshare 同花顺 V8 路径。

    Returns:
        加锁后的同名函数；异常原样透传，由上层 resilient 处理。

    Raises:
        NonRetryableError: 冻结模式（V8 源禁用）或熔断冷却期内抛出，
            触发降级链切换备源。
    """

    def wrapper(*args, **kwargs) -> T:
        """加锁执行原函数；冻结/熔断直接拒绝，交由降级链换源。

        Returns:
            T: 计算结果（透传 func 返回值）。

        Raises:
            NonRetryableError: 冻结模式或熔断冷却期内抛出。
        """
        global _V8_OPEN_UNTIL, _V8_FAIL_STREAK, _V8_FAIL_WINDOW_START, _V8_PROBE_PENDING
        if not config.V8_SOURCES_ENABLED:
            raise NonRetryableError(
                "冻结环境已禁用 py_mini_racer 依赖源（规范 §13.6-2），降级至纯 HTTP 备源"
            )
        now = _NOW()
        with _V8_STATE_LOCK:
            open_until = _V8_OPEN_UNTIL
            if open_until > now:
                raise NonRetryableError(
                    f"V8 通道熔断中（剩余 {open_until - now:.0f} 秒），降级至纯 HTTP 备源"
                )
            if open_until:
                # 冷却到期：半开放行单次试探
                _V8_OPEN_UNTIL = 0.0
                _V8_FAIL_STREAK = 0
                _V8_FAIL_WINDOW_START = now
                _V8_PROBE_PENDING = True
        try:
            with _V8_LOCK:
                value = func(*args, **kwargs)
        except Exception as exc:
            _record_failure(exc)
            raise
        _record_success()
        return value

    wrapper.__name__ = getattr(func, "__name__", "wrapper")
    wrapper.__doc__ = func.__doc__
    return wrapper
