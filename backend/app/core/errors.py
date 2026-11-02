"""统一错误码与 {data,error} 响应构造。

本模块是路由层唯一允许构造 API 响应的入口，保证：
1. 所有接口都带 `data` 与 `error` 两个基线字段；
2. 额外提供 `error_code`，取值固定为 ErrorCode，便于前端/测试按类型处理；
3. 错误文案一律为自然中文，禁止把 Python 堆栈或英文异常原文吐给用户。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional


class ErrorCode(str, Enum):
    """API 错误码枚举（字符串值可直接进入 JSON）。"""

    INVALID_PARAM = "INVALID_PARAM"          # 参数错误
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"    # 上游超时
    UPSTREAM_ERROR = "UPSTREAM_ERROR"        # 上游异常/连接失败
    EMPTY_DATA = "EMPTY_DATA"                # 数据为空
    NOT_CONFIGURED = "NOT_CONFIGURED"        # 未配置（如 AI Key）
    UNAUTHORIZED = "UNAUTHORIZED"            # 未授权/凭据失效
    NOT_FOUND = "NOT_FOUND"                  # 未找到
    INTERNAL_ERROR = "INTERNAL_ERROR"        # 服务内部异常


ERROR_MESSAGES: Dict[ErrorCode, str] = {
    ErrorCode.INVALID_PARAM: "请求参数不合法，请检查后重试。",
    ErrorCode.UPSTREAM_TIMEOUT: "上游数据源响应超时，请稍后点击刷新重试。",
    ErrorCode.UPSTREAM_ERROR: "数据源暂不可用，请稍后点击刷新重试。",
    ErrorCode.EMPTY_DATA: "数据源返回空数据，请稍后点击刷新重试。",
    ErrorCode.NOT_CONFIGURED: "尚未完成配置，请先在设置中填写。",
    ErrorCode.UNAUTHORIZED: "未授权或凭据已失效，请重新配置。",
    ErrorCode.NOT_FOUND: "未找到匹配的数据，请检查代码或关键词。",
    ErrorCode.INTERNAL_ERROR: "服务内部异常，请稍后重试；如持续出现请查看 logs/app.log。",
}


class UpstreamTimeoutError(Exception):
    """上游请求超时（requests.Timeout / socket.timeout）。"""


class NonRetryableError(Exception):
    """确定性失效（如 404、无效参数），不允许重试。"""


class NotFoundError(NonRetryableError):
    """标的不存在（P1-03）：如代码/板块名在源站查无此物。

    与普通 ``NonRetryableError`` 的区别在于**语义**：源被禁用、通道失效这类
    确定性错误应继续尝试下一源，而「标的本身不存在」继续降级毫无意义，
    必须立即终止整条降级链并返回 ``NOT_FOUND``，避免逐个源等满超时
    （实测修复前 /api/market/quote 不存在代码耗时 24 秒）。
    """


def ok(
    data: Any,
    *,
    source: Optional[str] = None,
    caliber: Optional[str] = None,
    as_of: Optional[str] = None,
    market_status: Optional[str] = None,
    is_realtime: Optional[bool] = None,
) -> Dict[str, Any]:
    """构造成功响应，并**保证** data 含数据三性五字段。

    规范依据：设计基准说明书_阶段5.1 + 新增功能设计基准_20261022
    「统一返回 ``{data,error,error_code}``；data 公共字段
    ``source/caliber/as_of/market_status/is_realtime``」。

    2026-11-02 第八轮（第二轮检测 P0-1 根因修复）：
        原实现的 ``ok()`` 不注入五字段，59 处 ``return ok(...)`` 直接绕过各路由的
        ``_pack``，导致 19 个端点缺字段。现改为**单点兜底**：
        载荷已带某字段则保留，缺失时按显式入参 → 载荷自身推断 → 中性默认值补齐，
        使契约在唯一出口处成立，新增端点无需再记着调 ``_pack``。

    Args:
        data: 业务载荷（dict 时补齐五字段；list/其他原样返回，由调用方改为 dict 包裹）。
        source: 数据源标识；缺省取载荷内 ``source``，再缺省为「本系统」。
        caliber: 口径说明；缺省取载荷内 ``caliber``，再缺省为「本地口径」。
        as_of: 数据时间；缺省取载荷内 ``as_of``，再缺省为当前北京时间。
        market_status: 市场状态；缺省取载荷内字段，再缺省为「本地态」。
        is_realtime: 是否实时；缺省取载荷内字段，再缺省 False。

    Returns:
        dict: {"data": 载荷（含五字段）, "error": None, "error_code": None}
    """
    payload = dict(data) if isinstance(data, dict) else data
    if isinstance(payload, dict):
        # 延迟导入避免 core.errors ↔ datasource.utils 启动期循环依赖
        from backend.app.datasource import utils

        payload.setdefault("source", source if source is not None else "本系统")
        payload.setdefault("caliber", caliber if caliber is not None else "本地口径")
        payload.setdefault("as_of", as_of if as_of is not None else utils.now_text(utils.bj_now()))
        payload.setdefault(
            "market_status", market_status if market_status is not None else "本地态"
        )
        payload.setdefault(
            "is_realtime", bool(is_realtime) if is_realtime is not None else False
        )
    return {"data": payload, "error": None, "error_code": None}


def pack_success(
    data: Any,
    source: str,
    caliber: str,
    as_of: Optional[str] = None,
    market_status: str = "本地态",
    is_realtime: bool = False,
) -> Dict[str, Any]:
    """构造**业务数据端点**的成功响应，统一注入数据三性五字段。

    规范依据：设计基准说明书_阶段5.1「统一返回 {data,error,error_code}；
    data 公共字段 source/caliber/as_of/market_status/is_realtime」，
    以及 ``新增功能设计基准_20261022``「统一返回 {data,error,error_code}，
    data 含 source/caliber/as_of/market_status/is_realtime」。

    本函数是路由层构造成功响应的**唯一入口**，取代各路由自行实现的 ``_pack``，
    从根上消除「59 处 return ok(...) 绕过 _pack 导致的信封五字段缺口」。

    Args:
        data: 业务载荷，必须为 JSON 可序列化结构。
        source: 数据源标识（用于降级链溯源与数据源徽章展示）。
        caliber: 口径说明（展示在口径徽章）。
        as_of: 数据时间（北京时间，格式 YYYY-MM-DD HH:MM:SS）。
        market_status: 市场状态文案（盘中/盘前/盘后/休市/收盘快照/T+1 净值…）。
        is_realtime: 是否为盘中实时数据。

    Returns:
        dict: {"data": 载荷+五字段, "error": None, "error_code": None}
    """
    # 唯一实现：直接复用 ok() 的补齐逻辑，避免两套规则漂移
    return ok(
        data,
        source=source,
        caliber=caliber,
        as_of=as_of,
        market_status=market_status,
        is_realtime=is_realtime,
    )


def api_error(
    code: ErrorCode,
    message: Optional[str] = None,
    data: Any = None,
) -> Dict[str, Any]:
    """构造失败响应。

    Args:
        code: 错误码。
        message: 可选的自定义中文文案；缺省使用 ERROR_MESSAGES。
        data: 与成功载荷同构的空结构（默认 None）。

    Returns:
        dict: {"data": 空结构, "error": 中文文案, "error_code": 错误码}
    """
    return {
        "data": data,
        "error": message or ERROR_MESSAGES.get(code, "请求未能完成，请稍后重试。"),
        "error_code": code.value,
    }


def bad_request(message: str) -> Dict[str, Any]:
    """构造参数错误响应（各路由 ``_bad`` 的统一实现）。

    Args:
        message: 中文提示文案。

    Returns:
        dict: 统一返回结构，error_code=INVALID_PARAM。
    """
    return api_error(ErrorCode.INVALID_PARAM, message)
