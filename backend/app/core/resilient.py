"""多源降级与重试公共模块。

对应规范 v7.0 §6.4 / §6.5：
1. 同一数据注册多个候选源，按顺序逐源尝试，首个返回有效数据的源胜出；
2. 网络抖动按 MAX_RETRY 指数退避重试，确定性失效（如 404）不重试；
3. 所有源均失败时返回自然中文错误文案，由路由层包成 {data,error}；
4. 串行调用之间加入随机延时，降低被封 IP 的风险。

本模块只做调度，不包含任何具体业务接口。
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from backend.app import config

logger = logging.getLogger(__name__)

Candidate = Tuple[str, Callable[[], Any]]  # (数据源名称, 无参取数函数)


class NonRetryableError(Exception):
    """确定性失效异常（如 404、无效 Token），不应重试。"""


def polite_sleep() -> None:
    """串行调用前随机等待 0.3~1.0 秒，降低源站风控概率。"""
    time.sleep(random.uniform(config.RATE_LIMIT_MIN_DELAY, config.RATE_LIMIT_MAX_DELAY))


def retry_call(
    func: Callable[[], Any],
    *,
    retries: int = config.MAX_RETRY,
    backoff: Sequence[float] = config.RETRY_BACKOFF,
) -> Any:
    """执行一次可能失败的网络调用，按指数退避重试。

    Args:
        func: 无参可调用对象。
        retries: 最大重试次数（不含首次调用）。
        backoff: 每次重试前的等待秒数序列。

    Returns:
        Any: func 的返回值。

    Raises:
        NonRetryableError: 确定性失效，立即向上抛出。
        Exception: 重试耗尽后抛出最后一次异常。
    """
    last_error: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            return func()
        except NonRetryableError:
            # 确定性错误重试无意义，直接失败，避免放大请求
            raise
        except Exception as exc:  # noqa: BLE001 - 需要兜底所有网络异常
            last_error = exc
            if attempt >= retries:
                break
            delay = backoff[min(attempt, len(backoff) - 1)]
            logger.warning(
                "调用失败，%.1f 秒后重试（第 %d/%d 次）：%s",
                delay,
                attempt + 1,
                retries,
                type(exc).__name__,
            )
            time.sleep(delay)
    assert last_error is not None
    raise last_error


def _has_rows(value: Any) -> bool:
    """判断返回值是否包含有效数据。

    Args:
        value: 数据源返回值，可能是 DataFrame、list、dict 或标量。

    Returns:
        bool: 非空返回 True。
    """
    if value is None:
        return False
    empty_attr = getattr(value, "empty", None)
    if isinstance(empty_attr, bool):
        return not empty_attr
    if isinstance(value, (list, tuple, dict, str, set)):
        return len(value) > 0
    return True


def with_fallback(
    candidates: Sequence[Candidate],
    *,
    need_rows: bool = True,
    validator: Optional[Callable[[Any], bool]] = None,
) -> Dict[str, Any]:
    """按优先级逐源尝试，返回首个有效结果。

    Args:
        candidates: [(源名称, 取数函数), ...]，顺序即优先级。
        need_rows: 是否要求返回值非空。
        validator: 可选的业务校验函数，返回 True 才算有效。

    Returns:
        Dict[str, Any]: 形如
            {"data": 载荷, "source": 命中源, "error": None, "attempts": [...]}；
            全部失败时 data 为空结构、source 为 None、error 为中文文案。
    """
    attempts: List[Dict[str, str]] = []

    for name, func in candidates:
        try:
            polite_sleep()  # 串行限频，避免连续高速请求
            value = func()
            valid = _has_rows(value) if need_rows else value is not None
            if valid and validator is not None:
                valid = bool(validator(value))
            if valid:
                return {"data": value, "source": name, "error": None, "attempts": attempts}
            attempts.append({"source": name, "error": "返回空数据"})
            logger.warning("数据源 %s 返回空数据，尝试下一源", name)
        except NonRetryableError as exc:
            attempts.append({"source": name, "error": f"确定性失效：{type(exc).__name__}"})
            logger.error("数据源 %s 确定性失效，跳过：%s", name, type(exc).__name__)
        except Exception as exc:  # noqa: BLE001 - 单源失败不应中断降级链
            attempts.append({"source": name, "error": type(exc).__name__})
            logger.warning("数据源 %s 调用失败，尝试下一源：%s", name, type(exc).__name__)

    logger.error("全部数据源均失败，候选数：%d", len(candidates))
    return {
        "data": [] if need_rows else None,
        "source": None,
        "error": "数据源暂不可用，请检查网络后点击刷新重试",
        "attempts": attempts,
    }
