# -*- coding: utf-8 -*-
"""P5 访问口令中间件（远程访问保护）· v2（REQ-20261109-01 AR-02）。

设计基准 §10 P5：访问口令中间件——为 Tailscale/公网访问增加一层应用层口令，
避免内网穿透后裸奔。本地单用户场景不配置口令=完全不启用（零打扰）。

v2 变更（openbb-review AR-02，2026-11-09 用户裁决②=A：POST+随机 session）
--------------------------------------------------------------------
1. **Cookie 不再是口令原文**：校验通过后下发随机 session id
  （``secrets.token_urlsafe(32)``，服务端存 ``{sid: expires_at}`` 映射），
   Cookie 泄露无法反推口令，且可单独吊销单个会话。
2. **登录表单改 POST**（``POST /auth``，form 字段 ``token``），口令不进 URL；
   ``GET /auth?token=`` 保留为「兑换」入口（兼容桌面快捷方式/公网链接形态），
   兑换后 Cookie 同样是随机 session 而非口令原文。
3. **有效期 30 天 → 7 天**，并新增 ``GET /auth/logout`` 主动登出。
4. **旧 Cookie 自动作废**：Cookie 名由 ``access_token`` 改为 ``openbb_session``，
   旧版写入浏览器 30 天的口令原文 Cookie 不再被读取，等于强制重新登录。
5. HTTPS 场景 Cookie 自动附加 ``Secure``（按 ``X-Forwarded-Proto`` / scheme 判定）。

继承口径
--------
- 启用条件：环境变量 ``OPENBB_ACCESS_PASSWORD`` 非空（≥4 字符）。
- 鉴权方式（三选一）：``Authorization: Bearer <口令>``（脚本/Tailscale 兼容）、
  Cookie ``openbb_session=<随机 sid>``（浏览器）。
- 放行路径：``/`` 及静态资源（浏览器直访）、``/api/health``（健康探针）。
- 失败：HTTP 401 + JSON ``{error, error_code:"UNAUTHORIZED"}``，不泄露口令正误细节。
- 口令不落盘、不入日志；session 映射仅存进程内存（重启后需重新登录）。
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from typing import Optional

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

# 鉴权 Cookie 名与有效期（AR-02：随机 session + 7 天窗口；改名使旧口令 Cookie 自动作废）
COOKIE_NAME = "openbb_session"
COOKIE_MAX_AGE = 7 * 24 * 3600

# session 上限保护：超过即清最旧（口令是创建前提，正常远达不到，纯防御性）
_SESSION_LIMIT = 1000

# 放行路径前缀（健康探针；/docs 与 /openapi.json 在启用口令时已由 create_app 禁用）
_ALLOWED_PREFIXES = (
    "/api/health",
)

# 失败节流：避免口令爆破（同 IP 连续失败 10 次后 60s 内拒绝）
_FAIL_WINDOW = 60.0
_FAIL_THRESHOLD = 10
_fail_counts: dict[str, list[float]] = {}

# 随机会话映射（进程内存）：sid -> 过期时间戳；线程锁保护并发读写
_sessions: dict[str, float] = {}
_sessions_lock = threading.Lock()


def _check_rate_limit(ip: str) -> bool:
    """同 IP 失败节流检查（60s 窗口内连续失败达阈值即拒绝）。

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
    """记录一次鉴权失败（按 IP 累计，供节流判定）。

    Args:
        ip: 客户端 IP。
    """
    now = time.time()
    history = _fail_counts.get(ip, [])
    history.append(now)
    _fail_counts[ip] = history


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


def _create_session() -> str:
    """创建随机会话并登记过期时间。

    伴随惰性清理：先移除全部过期会话；若仍超上限则按过期时间清最旧。

    Returns:
        str: 随机 session id（token_urlsafe(32)）。
    """
    sid = secrets.token_urlsafe(32)
    now = time.time()
    with _sessions_lock:
        expired = [k for k, exp in _sessions.items() if exp <= now]
        for k in expired:
            del _sessions[k]
        if len(_sessions) >= _SESSION_LIMIT:
            overflow = sorted(_sessions.items(), key=lambda kv: kv[1])[: len(_sessions) - _SESSION_LIMIT + 1]
            for k, _ in overflow:
                del _sessions[k]
        _sessions[sid] = now + COOKIE_MAX_AGE
    return sid


def _validate_session(sid: str) -> bool:
    """校验会话 id 是否存在且未过期（过期即惰性清除）。

    Args:
        sid: Cookie/Bearer 中的会话 id。

    Returns:
        bool: 有效为 True。
    """
    if not sid:
        return False
    with _sessions_lock:
        exp = _sessions.get(sid)
        if exp is None:
            return False
        if exp <= time.time():
            del _sessions[sid]
            return False
        return True


def revoke_session(sid: str) -> None:
    """吊销会话（登出用）；不存在时静默。

    Args:
        sid: 待吊销的会话 id。
    """
    with _sessions_lock:
        _sessions.pop(sid, None)


def _extract_token(request: Request) -> str:
    """从请求提取凭证（Authorization header 优先，其次 session Cookie）。

    Args:
        request: FastAPI 请求。

    Returns:
        str: 口令（Bearer）或会话 id（Cookie），可能为空。
    """
    auth = request.headers.get("authorization") or ""
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return request.cookies.get(COOKIE_NAME, "")


def is_authed_request(request: Request) -> bool:
    """判定请求是否已鉴权（供 health 等端点做信息分级）。

    未启用口令（本机单用户）恒为 True。

    Args:
        request: FastAPI 请求。

    Returns:
        bool: Bearer 口令匹配或会话有效为 True。
    """
    pwd = _get_password()
    if pwd is None:
        return True
    token = _extract_token(request)
    return token == pwd or _validate_session(token)


def _is_https(request: Request) -> bool:
    """判定请求是否经 HTTPS（公网隧道/反向代理场景 Cookie 附加 Secure）。

    Args:
        request: FastAPI 请求。

    Returns:
        bool: ``X-Forwarded-Proto: https`` 或请求 scheme 为 https 时 True。
    """
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return forwarded == "https" or request.url.scheme == "https"


def _login_response(request: Request) -> Response:
    """登录成功的统一响应：发随机 session Cookie 并 302 回首页。

    Args:
        request: FastAPI 请求（用于 HTTPS 判定）。

    Returns:
        Response: 302 重定向 + ``openbb_session`` Cookie。
    """
    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        COOKIE_NAME,
        _create_session(),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
        secure=_is_https(request),
    )
    return response


def auth_response(request: Request, token: str) -> Response:
    """``GET /auth`` 的统一响应：登录页 / 兑换 Cookie / 错误重试。

    分支：
    - 未配置口令（本机单用户）：直接 302 回首页，登录页不存在；
    - 无 token：返回 ``frontend/auth.html`` 口令表单（200）；
    - token 正确：**兑换**为随机 session Cookie（``openbb_session``）并 302 回首页
      —— 兼容桌面快捷方式/公网链接的「带 token 网址」形态，但 Cookie 不再是口令原文；
    - token 错误：302 回 ``/auth?error=1``，由登录页显示错误提示（不暴露细节）。

    Args:
        request: FastAPI 请求。
        token: URL 上的 ``token`` 查询参数（可为空）。

    Returns:
        Response: 登录页、重定向或错误重定向。
    """
    pwd = _get_password()
    if pwd is None:
        return RedirectResponse("/", status_code=302)
    if not token:
        from backend.app import config

        return FileResponse(str(config.FRONTEND_DIR / "auth.html"), media_type="text/html")
    if token != pwd:
        return RedirectResponse("/auth?error=1", status_code=302)
    return _login_response(request)


async def post_auth_response(request: Request) -> Response:
    """``POST /auth`` 表单登录（AR-02：口令不进 URL）。

    成功：发随机 session Cookie 后 303 回首页（PRG，防表单重复提交）；
    失败：303 回 ``/auth?error=1``。

    表单解析用标准库 parse_qs 手工读 urlencoded body——登录表单固定单字段
    urlencoded，无需引入 python-multipart（零新依赖，不影响打包清单）。

    Args:
        request: FastAPI 请求（表单字段 ``token``）。

    Returns:
        Response: 303 重定向（带或不带 Cookie）。
    """
    pwd = _get_password()
    if pwd is None:
        return RedirectResponse("/", status_code=303)
    try:
        from urllib.parse import parse_qs

        raw = (await request.body()).decode("utf-8", "replace")
        fields = parse_qs(raw, keep_blank_values=True)
        token = (fields.get("token") or [""])[0].strip()
    except Exception:  # noqa: BLE001 - 表单解析异常按登录失败处理
        token = ""
    if not token or token != pwd:
        return RedirectResponse("/auth?error=1", status_code=303)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        _create_session(),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
        secure=_is_https(request),
    )
    return response


def logout_response(request: Request) -> Response:
    """``GET /auth/logout``：吊销当前会话并清除 Cookie，回登录页。

    Args:
        request: FastAPI 请求（读取当前会话 id 以吊销）。

    Returns:
        Response: 302 → ``/auth``，并下发过期空 Cookie。
    """
    revoke_session(request.cookies.get(COOKIE_NAME, ""))
    response = RedirectResponse("/auth", status_code=302)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


class AccessPasswordMiddleware(BaseHTTPMiddleware):
    """访问口令中间件（未配置时直接透传）。"""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """ASGI 请求入口：未配置口令则透传，已配置则按 Bearer/会话校验。

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
        if not path.startswith("/api/"):
            # AR-14（REQ-20261109-01）：未登录访问 HTML 外壳直接 302 登录页，
            # 消除「先渲染完整骨架再被前端 401 跳走」的闪烁；静态资源仍放行
            # （CSS/JS 无敏感数据，全部业务数据由鉴权保护的 /api/* 提供）。
            if path == "/" and not is_authed_request(request):
                return RedirectResponse("/auth", status_code=302)
            return await call_next(request)
        # /api/health 放行（端点内部按是否鉴权做信息分级）；/docs 与 /openapi.json
        # 在启用口令时已由 create_app 禁用路由，不再列入放行清单。
        if any(path.startswith(p) for p in _ALLOWED_PREFIXES):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        token = _extract_token(request)
        if token == pwd or _validate_session(token):
            # 鉴权通过：清除该 IP 的失败计数并放行，避免「登录前误触节流 → 登录后仍 429」死锁。
            _fail_counts.pop(client_ip, None)
            return await call_next(request)
        # 口令/会话未通过：记录失败并检查节流（同 IP 连续失败 10 次后 60s 内拒绝）。
        _record_fail(client_ip)
        if not _check_rate_limit(client_ip):
            # dup-ok: 中间件层 HTTP 语义响应（429/401 状态码承载主信息，body 刻意最小化
            # 仅 error/error_code 不含 data），与 200 业务信封不同构；api_error 无法表达
            # status_code，见 RF-20261112-01 阶段 6 甄别备案。
            return JSONResponse(
                status_code=429,
                content={
                    "error": "尝试次数过多，请稍后再试",
                    "error_code": "RATE_LIMITED",  # dup-ok: 同上备案
                },
            )
        return JSONResponse(
            status_code=401,
            content={
                "error": "访问口令错误或未提供",
                "error_code": "UNAUTHORIZED",  # dup-ok: 同上备案
            },
        )
