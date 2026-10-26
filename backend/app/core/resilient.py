"""多源降级与重试公共模块。

对应规范 v7.0 §6.4 / §6.5，并按本阶段指令把重试次数调整为 3 次：
1. 同一数据注册多个候选源，按顺序逐源尝试，首个返回有效数据的源胜出；
2. 每个源内部按 3 次重试 + 指数退避（1s/2s/4s）+ 随机抖动；
3. 返回体携带真实命中的数据源名与口径，供前端双徽章；
4. 全部失败时返回统一错误码与自然中文文案，不向路由层抛裸异常。

本模块只做调度，不包含任何具体业务接口。
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from backend.app import config
from backend.app.core import source_prefs
from backend.app.core.errors import (
    ERROR_MESSAGES,
    ErrorCode,
    NonRetryableError,
    NotFoundError,
    UpstreamTimeoutError,
)

logger = logging.getLogger(__name__)

# 确定性异常：同一入参重试结果必然相同，重试只会白等退避时间（P1-03 快速失败）。
# 实测修复前「不存在的代码」在 3 个源上各重试 3 次（1+2+4 秒退避），累计 9–24 秒。
DETERMINISTIC_ERRORS = (
    NonRetryableError,  # 含 NotFoundError（源禁用 / 标的不存在）
    ValueError,  # 返回格式异常、字段不足、上游返回空
    KeyError,  # 响应结构缺键
    TypeError,  # 结构类型不符
    AttributeError,  # akshare 解析空文档时的典型异常
    IndexError,  # 字段越界
    NameError,  # 代码级错误（未定义名），重试只会掩盖缺陷
)

Candidate = Union[
    Tuple[str, Callable[[], Any]],
    Tuple[str, str, Callable[[], Any]],
]


def _normalize_candidate(candidate: Candidate) -> Tuple[str, str, Callable[[], Any]]:
    """把二元组或三元组候选源统一为 (名称, 口径, 函数)。

    Args:
        candidate: (源名, 函数) 或 (源名, 口径, 函数)。

    Returns:
        tuple: (源名, 口径, 无参取数函数)。
    """
    if len(candidate) == 3:
        name, caliber, func = candidate  # type: ignore[misc]
        return str(name), str(caliber), func
    name, func = candidate  # type: ignore[misc]
    return str(name), "", func


def polite_sleep() -> None:
    """串行调用之间随机等待 0.3~1.0 秒，降低源站风控概率。"""
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
        retries: 最大重试次数（不含首次调用）；本阶段默认 3。
        backoff: 每次重试前的等待秒数序列。

    Returns:
        Any: func 的返回值。

    Raises:
        NonRetryableError: 确定性失效，立即向上抛出。
        UpstreamTimeoutError: 超时，重试耗尽后向上抛出。
        Exception: 重试耗尽后抛出最后一次异常。
    """
    last_error: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            return func()
        except DETERMINISTIC_ERRORS:
            # 确定性错误（参数错误、源被禁用、404、解析异常、标的不存在）重试无意义
            raise
        except Exception as exc:  # noqa: BLE001 - 单源异常需转为重试/失败
            last_error = exc
            if attempt >= retries:
                break
            delay = backoff[min(attempt, len(backoff) - 1)] + random.uniform(0, 0.3)
            logger.warning(
                "调用失败，%.1f 秒后重试（第 %d/%d 次）：%s",
                delay,
                attempt + 1,
                retries,
                type(exc).__name__,
            )
            time.sleep(delay)
    if last_error is None:
        raise RuntimeError("调用未执行且未记录异常")
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


def _classify_failure(
    attempts: List[Dict[str, Any]],
    *,
    total: int,
) -> ErrorCode:
    """根据各源失败记录推断统一错误码。

    Args:
        attempts: 每个源的失败记录。
        total: 候选源总数。

    Returns:
        ErrorCode: 超时优先，其次上游异常，最后空数据。
    """
    codes = [item.get("error_code") for item in attempts]
    if ErrorCode.UPSTREAM_TIMEOUT.value in codes:
        return ErrorCode.UPSTREAM_TIMEOUT
    if any(code in (ErrorCode.UPSTREAM_ERROR.value, ErrorCode.INVALID_PARAM.value) for code in codes):
        return ErrorCode.UPSTREAM_ERROR
    if total > 0 and len(attempts) == total:
        return ErrorCode.EMPTY_DATA
    return ErrorCode.UPSTREAM_ERROR


def _is_transport_error(exc: BaseException) -> bool:
    """判断异常是否属于传输层失败（用于触发熔断）。

    Args:
        exc: 单源取数抛出的异常。

    Returns:
        bool: 超时/连接失败/requests 或 urllib3 异常返回 True。

    Note:
        解析类异常（ValueError/KeyError 等）不触发熔断：它们随「标的」或
        「上游返回形态」变化，熔断整条源会误伤其他正常标的。
    """
    if isinstance(exc, (UpstreamTimeoutError, ConnectionError, OSError)):
        return True
    module = type(exc).__module__ or ""
    return module.startswith(("requests", "urllib3"))


# 备源优先级记忆：{降级链键: 上次命中的源名}（P1-04）
_PREFERRED: Dict[str, str] = {}


def with_fallback(
    candidates: Sequence[Candidate],
    *,
    need_rows: bool = True,
    validator: Optional[Callable[[Any], bool]] = None,
    empty_value: Any = None,
    key: Optional[str] = None,
) -> Dict[str, Any]:
    """按优先级逐源尝试，返回首个有效结果。

    Args:
        candidates: [(源名, 函数), ...] 或 [(源名, 口径, 函数), ...]，顺序即优先级。
        need_rows: 是否要求返回值非空。
        validator: 可选的业务校验函数，返回 True 才算有效。
        empty_value: 全部失败时返回的空结构（list 接口传 []，dict 接口传 {}）。
        key: 可选降级链键（如 ``market:kline:us``）。传入后启用两项进程级记忆：
            ① 备源优先级记忆——上次命中的源下次排到最前，避免每次冷启动
               都先撞已知失效的源（P1-04）；
            ② 熔断跳过——传输层失败过的源在冷却期内直接跳过并留痕。

    Returns:
        Dict[str, Any]: 成功时
            {"data": 载荷, "source": 命中源, "caliber": 口径, "error": None,
             "error_code": None, "attempts": [...]}；
            全部失败时 data=empty_value、source=None、error 为中文文案、
            error_code 为 ErrorCode 字符串（标的不存在为 NOT_FOUND）。
    """
    attempts: List[Dict[str, Any]] = []
    normalized = [_normalize_candidate(item) for item in candidates]

    # R3-03：按连接器页的「停用」偏好过滤候选源（全部停用时自动忽略，防误关功能）
    enabled_names = set(source_prefs.filter_candidates([item[0] for item in normalized]))
    if len(enabled_names) != len(normalized):
        skipped = [item[0] for item in normalized if item[0] not in enabled_names]
        normalized = [item for item in normalized if item[0] in enabled_names]
        for name in skipped:
            attempts.append(
                {
                    "source": name,
                    "caliber": "",
                    "error": "已在连接器页停用，跳过",
                    "error_code": ErrorCode.UPSTREAM_ERROR.value,
                    "retries": 0,
                }
            )
            logger.info("数据源 %s 已被用户停用，跳过（键 %s）", name, key)

    # 备源优先级记忆：把上次命中的源稳定提到最前，其余源保持声明顺序
    if key:
        preferred = _PREFERRED.get(key)
        if preferred:
            normalized.sort(key=lambda item: 0 if item[0] == preferred else 1)

    for index, (name, caliber, func) in enumerate(normalized):
        # 熔断跳过：传输层失败的源在冷却期内不再消耗超时（P1-04）
        if key and circuit_open(f"{key}:{name}"):
            attempts.append(
                {
                    "source": name,
                    "caliber": caliber,
                    "error": "熔断冷却中，已跳过",
                    "error_code": ErrorCode.UPSTREAM_ERROR.value,
                    "retries": 0,
                }
            )
            logger.info("数据源 %s 处于熔断期，跳过（键 %s）", name, key)
            continue
        if index > 0:
            polite_sleep()  # 只在源之间限频，避免首次请求无谓等待
        try:
            value = retry_call(func)
            valid = _has_rows(value) if need_rows else value is not None
            if valid and validator is not None:
                valid = bool(validator(value))
            if valid:
                if key:
                    # 记住本次生效源，并清除其熔断标记
                    _PREFERRED[key] = name
                    reset_circuit(f"{key}:{name}")
                return {
                    "data": value,
                    "source": name,
                    "caliber": caliber,
                    "error": None,
                    "error_code": None,
                    "attempts": attempts,
                }
            attempts.append(
                {
                    "source": name,
                    "caliber": caliber,
                    "error": "返回空数据",
                    "error_code": ErrorCode.EMPTY_DATA.value,
                    "retries": config.MAX_RETRY,
                }
            )
            logger.warning("数据源 %s 返回空数据，尝试下一源", name)
        except NotFoundError as exc:
            # 标的不存在：继续降级毫无意义，立即终止整条链（P1-03 快速失败）
            attempts.append(
                {
                    "source": name,
                    "caliber": caliber,
                    "error": str(exc) or "标的不存在",
                    "error_code": ErrorCode.NOT_FOUND.value,
                    "retries": 0,
                }
            )
            logger.warning("数据源 %s 判定标的不存在，终止降级链：%s", name, exc)
            break
        except NonRetryableError as exc:
            attempts.append(
                {
                    "source": name,
                    "caliber": caliber,
                    "error": f"确定性失效：{type(exc).__name__}",
                    "error_code": ErrorCode.INVALID_PARAM.value,
                    "retries": 0,
                }
            )
            logger.warning("数据源 %s 确定性失效，跳过：%s", name, type(exc).__name__)
        except UpstreamTimeoutError as exc:
            attempts.append(
                {
                    "source": name,
                    "caliber": caliber,
                    "error": f"上游超时：{type(exc).__name__}",
                    "error_code": ErrorCode.UPSTREAM_TIMEOUT.value,
                    "retries": config.MAX_RETRY,
                }
            )
            logger.warning("数据源 %s 超时，尝试下一源", name)
            if key:
                trip_circuit(f"{key}:{name}", config.CIRCUIT_COOLDOWN)
        except Exception as exc:  # noqa: BLE001 - 单源失败不应中断降级链
            attempts.append(
                {
                    "source": name,
                    "caliber": caliber,
                    "error": type(exc).__name__,
                    "error_code": ErrorCode.UPSTREAM_ERROR.value,
                    "retries": config.MAX_RETRY,
                }
            )
            logger.warning("数据源 %s 调用失败，尝试下一源：%s", name, type(exc).__name__)
            if key and _is_transport_error(exc):
                # 传输层失败才熔断，避免把「某标的解析异常」误判成整源失效
                trip_circuit(f"{key}:{name}", config.CIRCUIT_COOLDOWN)

    # 标的不存在优先于其他分类：不能把「查无此物」报成「源暂不可用」
    if any(item.get("error_code") == ErrorCode.NOT_FOUND.value for item in attempts):
        error_code = ErrorCode.NOT_FOUND
    else:
        error_code = _classify_failure(attempts, total=len(normalized))
    logger.error("全部数据源均失败，候选数：%d，错误码：%s", len(normalized), error_code.value)
    return {
        "data": empty_value,
        "source": None,
        "caliber": None,
        "error": ERROR_MESSAGES[error_code],
        "error_code": error_code.value,
        "attempts": attempts,
    }


# =============================================================
# 进程级熔断（§6.5 韧性补充）
# =============================================================
_CIRCUIT_UNTIL: Dict[str, float] = {}
_CIRCUIT_LOCK = threading.Lock()


def circuit_open(key: str) -> bool:
    """判断某个源是否处于熔断冷却期。

    场景：东财港美股快照需拉全市场（数千行），本机 push2 被重置时每次要耗
    60 秒（20s×3 重试）才降级；缓存 TTL 仅 15 秒，等于每轮都卡一分钟。
    熔断后冷却期内直接跳过，避免反复无效等待。

    Args:
        key: 熔断键（如 ``eastmoney:quote:hk``）。

    Returns:
        bool: True 表示仍在冷却期（应跳过该源）。
    """
    with _CIRCUIT_LOCK:
        until = _CIRCUIT_UNTIL.get(key, 0.0)
    return time.time() < until


def trip_circuit(key: str, cooldown: float = 300.0) -> None:
    """标记某源失效并进入冷却期。

    Args:
        key: 熔断键。
        cooldown: 冷却秒数（默认 300 秒）。

    Returns:
        None: 仅更新进程内状态。
    """
    with _CIRCUIT_LOCK:
        _CIRCUIT_UNTIL[key] = time.time() + cooldown
    logger.warning("数据源进入熔断：%s（冷却 %.0f 秒）", key, cooldown)


def reset_circuit(key: str) -> None:
    """清除某源的熔断标记（源恢复后立即解除，不必等冷却到期）。

    Args:
        key: 熔断键。

    Returns:
        None: 仅更新进程内状态。
    """
    with _CIRCUIT_LOCK:
        _CIRCUIT_UNTIL.pop(key, None)


def preferred_source(key: str) -> Optional[str]:
    """读取某降级链记住的优先源（供自检/测试断言）。

    Args:
        key: 降级链键。

    Returns:
        Optional[str]: 记住的源名；未命中过返回 None。
    """
    return _PREFERRED.get(key)
