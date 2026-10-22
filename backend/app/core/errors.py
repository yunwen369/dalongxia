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


def ok(data: Any) -> Dict[str, Any]:
    """构造成功响应。

    Args:
        data: 业务载荷，必须为 JSON 可序列化结构。

    Returns:
        dict: {"data": 载荷, "error": None, "error_code": None}
    """
    return {"data": data, "error": None, "error_code": None}


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
