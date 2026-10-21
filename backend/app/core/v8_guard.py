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
"""

from __future__ import annotations

import threading
from typing import Callable, TypeVar

T = TypeVar("T")

# 进程级可重入锁：同花顺 V8 调用串行化。RLock 允许 provider 内部
# 嵌套包装（_call → _call_ths → loader）不自杀。
_V8_LOCK = threading.RLock()


def guard_v8(func: Callable[..., T]) -> Callable[..., T]:
    """把函数调用包进进程级 V8 互斥锁（同步阻塞语义）。

    Args:
        func: 无参 loader（lambda），内部会触发 akshare 同花顺 V8 路径。

    Returns:
        加锁后的同名函数；异常原样透传，由上层 resilient 处理。
    """

    def wrapper(*args, **kwargs) -> T:
        """加锁执行原函数；异常原样透传，由上层 resilient 决定重试或降级。

        Returns:
            T: 计算结果。
        """
        with _V8_LOCK:
            return func(*args, **kwargs)

    wrapper.__name__ = getattr(func, "__name__", "wrapper")
    wrapper.__doc__ = func.__doc__
    return wrapper
