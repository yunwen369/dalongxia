# -*- coding: utf-8 -*-
"""P5 访问口令中间件（远程访问保护）。

设计基准 §10 P5：访问口令中间件——为 Tailscale 异地访问增加一层应用层口令，
避免内网穿透后裸奔。本地单用户场景不配置口令=完全不启用（零打扰）。

口径
----
- 启用条件：环境变量 ``OPENBB_ACCESS_PASSWORD`` 非空（≥4 字符）。
- 放行路径：``/`` 及静态资源（浏览器直访）、``/api/health``（健康探针）。
- 鉴权方式（二选一）：
  1. ``Authorization: Bearer <口令>``；
  2. Cookie ``access_token=<口令>``。
- 失败：HTTP 401 + JSON ``{error, error_code:"UNAUTHORIZED"}``，不泄露口令正误细节。
- 口令不落盘、不入日志；从环境变量读取，进程启动时固定。
"""

from __future__ import annotations

import os
import time
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

# 放行路径前缀（静态前端 + 健康探针）
_ALLOWED_PREFIXES = (
    "/api/health",
    "/docs",
    "/openapi.json",
)

# 失败节流：避免口令爆破（同 IP 连续失败 10 次后 60s 内拒绝）
_FAIL_WINDOW = 60.0
_FAIL_THRESHOLD = 10
_fail_counts: dict[str, list[float]] = {}


def _get_password() -> Optional[str]:
    """从环境变量读取访问口令；空/过短视为未配置。

    Returns:
        Optional[str]: 口令字符串或 None（未启用）。
    """
    pwd = (os.environ.get("OPENBB_ACCESS_PASSWORD") or "").strip()
    return pwd if len(pwd) >= 4 else None


def is_enabled() -> bool:
    """访问口令是否已启用。

    Returns:
        bool: 环境变量配置且长度 ≥4 为 True。
    """
    return _get_password() is not None


def _extract_token(request: Request) -> str:
    """从请求提取口令（Authorization header 优先，其次 cookie）。

    Args:
        request: FastAPI 请求。

    Returns:
        str: 提取到的口令（可能为空）。
    """
    auth = request.headers.get("authorization") or ""
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return request.cookies.get("access_token", "")


def _check_rate_limit(ip: str) -> bool:
    """同 IP 失败节流检查。

    Args:
        ip: 客户端 IP。

    Returns:
        bool: True=放行鉴权，False=拒绝（触发节流）。
    """
    now = time.time()
    history = _fail_counts.get(ip, [])
    # 清理窗口外记录
    history = [t for t in history if now - t < _FAIL_WINDOW]
    if len(history) >= _FAIL_THRESHOLD:
        _fail_counts[ip] = history
        return False
    _fail_counts[ip] = history
    return True


def _record_fail(ip: str) -> None:
    """记录一次失败。"""
    now = time.time()
    history = _fail_counts.get(ip, [])
    history.append(now)
    _fail_counts[ip] = history


class AccessPasswordMiddleware(BaseHTTPMiddleware):
    """访问口令中间件（未配置时直接透传）。"""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """ASGI 请求入口：未配置口令则透传，已配置则按 Bearer/Cookie 校验。

        Args:
            request: 入站请求。
            call_next: 下游处理链。

        Returns:
            Response: 放行后的下游响应，或 401/429 拒绝响应。
        """
        pwd = _get_password()
        if pwd is None:
            # 未配置：完全不启用
            return await call_next(request)

        path = request.url.path
        # 放行静态前端与健康探针
        if not path.startswith("/api/") or any(path.startswith(p) for p in _ALLOWED_PREFIXES):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        if not _check_rate_limit(client_ip):
            return JSONResponse(
                status_code=429,
                content={
                    "error": "尝试次数过多，请稍后再试",
                    "error_code": "RATE_LIMITED",
                },
            )

        token = _extract_token(request)
        if token != pwd:
            _record_fail(client_ip)
            return JSONResponse(
                status_code=401,
                content={
                    "error": "访问口令错误或未提供",
                    "error_code": "UNAUTHORIZED",
                },
            )
        return await call_next(request)
