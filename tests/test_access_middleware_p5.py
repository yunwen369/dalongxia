# -*- coding: utf-8 -*-
"""P5 访问口令中间件测试。"""

from __future__ import annotations

import pytest
from starlette.requests import Request  # noqa: F401 - mini app 路由注解需模块级可见（get_type_hints 解析）


@pytest.fixture
def _reset_fails():
    from backend.app.core import access_middleware as mod

    mod._fail_counts.clear()
    yield
    mod._fail_counts.clear()


def test_disabled_when_no_env(monkeypatch):
    """未配置口令→is_enabled False，中间件透传。"""
    from backend.app.core import access_middleware as mod

    monkeypatch.delenv("OPENBB_ACCESS_PASSWORD", raising=False)
    assert mod.is_enabled() is False
    assert mod._get_password() is None


def test_enabled_when_env_set(monkeypatch):
    """配置≥4字符口令→启用。"""
    from backend.app.core import access_middleware as mod

    monkeypatch.setenv("OPENBB_ACCESS_PASSWORD", "secret123")
    assert mod.is_enabled() is True
    assert mod._get_password() == "secret123"


def test_short_password_disabled(monkeypatch):
    """口令<4字符→不启用。"""
    from backend.app.core import access_middleware as mod

    monkeypatch.setenv("OPENBB_ACCESS_PASSWORD", "ab")
    assert mod.is_enabled() is False


def test_extract_token_from_header():
    """Authorization Bearer 提取口令。"""
    from backend.app.core import access_middleware as mod

    scope = {
        "type": "http", "headers": [(b"authorization", b"Bearer mytoken")],
        "method": "GET", "path": "/api/test", "query_string": b"",
        "server": ("127.0.0.1", 6900), "client": ("127.0.0.1", 12345),
        "scheme": "http", "root_path": "",
    }
    req = Request(scope)
    assert mod._extract_token(req) == "mytoken"


def test_extract_token_from_cookie():
    """Cookie openbb_session 提取（AR-02：Cookie 名已由 access_token 改名）。"""
    from backend.app.core import access_middleware as mod

    scope = {
        "type": "http", "headers": [(b"cookie", b"openbb_session=cookietoken")],
        "method": "GET", "path": "/api/test", "query_string": b"",
        "server": ("127.0.0.1", 6900), "client": ("127.0.0.1", 12345),
        "scheme": "http", "root_path": "",
    }
    req = Request(scope)
    assert mod._extract_token(req) == "cookietoken"


def test_rate_limit(_reset_fails, monkeypatch):
    """同 IP 连续失败达阈值→拒绝。"""
    from backend.app.core import access_middleware as mod

    ip = "1.2.3.4"
    for _ in range(mod._FAIL_THRESHOLD):
        mod._record_fail(ip)
    assert mod._check_rate_limit(ip) is False


def test_health_endpoint_bypasses_auth():
    """健康探针路径在放行前缀内。"""
    from backend.app.core import access_middleware as mod

    assert any("/api/health".startswith(p) for p in mod._ALLOWED_PREFIXES)


def test_middleware_passthrough_when_disabled(monkeypatch, _reset_fails):
    """未配置口令时中间件透传（不返回 401）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.core.access_middleware import AccessPasswordMiddleware

    monkeypatch.delenv("OPENBB_ACCESS_PASSWORD", raising=False)
    app = FastAPI()
    app.add_middleware(AccessPasswordMiddleware)

    @app.get("/api/test")
    def t():
        return {"ok": True}

    client = TestClient(app)
    resp = client.get("/api/test")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_middleware_blocks_without_token(monkeypatch, _reset_fails):
    """配置口令但请求无 token→401。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.core.access_middleware import AccessPasswordMiddleware

    monkeypatch.setenv("OPENBB_ACCESS_PASSWORD", "secret123")
    app = FastAPI()
    app.add_middleware(AccessPasswordMiddleware)

    @app.get("/api/test")
    def t():
        return {"ok": True}

    client = TestClient(app)
    resp = client.get("/api/test")
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "UNAUTHORIZED"


def test_middleware_allows_with_correct_token(monkeypatch, _reset_fails):
    """正确 Bearer token→放行。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.core.access_middleware import AccessPasswordMiddleware

    monkeypatch.setenv("OPENBB_ACCESS_PASSWORD", "secret123")
    app = FastAPI()
    app.add_middleware(AccessPasswordMiddleware)

    @app.get("/api/test")
    def t():
        return {"ok": True}

    client = TestClient(app)
    resp = client.get("/api/test", headers={"Authorization": "Bearer secret123"})
    assert resp.status_code == 200


def test_health_endpoint_allowed_without_token(monkeypatch, _reset_fails):
    """健康探针无需口令。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.core.access_middleware import AccessPasswordMiddleware

    monkeypatch.setenv("OPENBB_ACCESS_PASSWORD", "secret123")
    app = FastAPI()
    app.add_middleware(AccessPasswordMiddleware)

    @app.get("/api/health")
    def t():
        return {"ok": True}

    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_correct_token_bypasses_throttle(monkeypatch, _reset_fails):
    """节流已触发时，正确 token 仍放行并清除该 IP 失败计数。

    修复点：token 校验先于节流检查，避免「登录前误触节流 → 登录后仍 429」死锁。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.core import access_middleware as mod
    from backend.app.core.access_middleware import AccessPasswordMiddleware

    monkeypatch.setenv("OPENBB_ACCESS_PASSWORD", "secret123")
    app = FastAPI()
    app.add_middleware(AccessPasswordMiddleware)

    @app.get("/api/test")
    def t():
        return {"ok": True}

    client = TestClient(app)
    # 先触发节流：连续失败达阈值
    for _ in range(mod._FAIL_THRESHOLD + 2):
        client.get("/api/test")
    assert mod._check_rate_limit("testclient") is False
    # 正确 token 仍应放行（200），且失败计数被清除
    resp = client.get("/api/test", headers={"Authorization": "Bearer secret123"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert mod._fail_counts.get("testclient") in (None, [])


# =============================================================
# AR-02（REQ-20261109-01）：随机 session 模型（POST 登录 / 兑换 / 登出）
# =============================================================


@pytest.fixture
def _reset_sessions():
    """清空进程内会话映射，保证用例隔离。"""
    from backend.app.core import access_middleware as mod

    mod._sessions.clear()
    yield
    mod._sessions.clear()


def test_session_create_validate_revoke(_reset_sessions):
    """会话生命周期：创建→校验→吊销后失效。"""
    from backend.app.core import access_middleware as mod

    sid = mod._create_session()
    assert sid and sid != "secret123"  # session id 不是口令
    assert mod._validate_session(sid) is True
    mod.revoke_session(sid)
    assert mod._validate_session(sid) is False


def test_post_login_sets_random_session_cookie(_reset_sessions, monkeypatch):
    """POST /auth 表单登录：303 + Cookie 值 ≠ 口令原文 + 原会话可访问 API。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.core.access_middleware import AccessPasswordMiddleware

    monkeypatch.setenv("OPENBB_ACCESS_PASSWORD", "secret123")
    app = FastAPI()
    app.add_middleware(AccessPasswordMiddleware)

    @app.post("/auth")
    async def auth_post(request: Request):  # pragma: no cover - 由中间件外的路由承接
        from backend.app.core.access_middleware import post_auth_response

        return await post_auth_response(request)

    @app.get("/api/test")
    def t():
        return {"ok": True}

    client = TestClient(app)
    resp = client.post("/auth", data={"token": "secret123"}, follow_redirects=False)
    assert resp.status_code == 303
    cookie = resp.headers.get("set-cookie", "")
    assert "openbb_session=" in cookie
    # Cookie 值绝不能是口令原文（AR-02 核心断言）
    assert "secret123" not in cookie.split("openbb_session=")[1].split(";")[0]
    sid = cookie.split("openbb_session=")[1].split(";")[0]
    # 带 session Cookie 访问 API 放行
    assert client.get("/api/test").status_code in (200, 401)  # 无 cookie 时 401
    client.cookies.set("openbb_session", sid)
    assert client.get("/api/test").status_code == 200


def test_post_login_wrong_password_redirects_error(_reset_sessions, monkeypatch):
    """POST /auth 口令错误：303 → /auth?error=1（PRG，不暴露细节）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.core.access_middleware import AccessPasswordMiddleware, post_auth_response

    monkeypatch.setenv("OPENBB_ACCESS_PASSWORD", "secret123")
    app = FastAPI()
    app.add_middleware(AccessPasswordMiddleware)

    @app.post("/auth")
    async def auth_post(request: Request):
        return await post_auth_response(request)

    client = TestClient(app)
    resp = client.post("/auth", data={"token": "wrong"}, follow_redirects=False)
    assert resp.status_code == 303
    assert "/auth?error=1" in resp.headers.get("location", "")


def test_get_token_exchange_sets_random_session(_reset_sessions, monkeypatch):
    """GET /auth?token= 兑换：302 + Cookie 为随机 session（≠口令原文）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.core.access_middleware import AccessPasswordMiddleware, auth_response

    monkeypatch.setenv("OPENBB_ACCESS_PASSWORD", "secret123")
    app = FastAPI()
    app.add_middleware(AccessPasswordMiddleware)

    @app.get("/auth")
    def auth_get(request: Request, token: str = ""):
        return auth_response(request, token)

    client = TestClient(app)
    resp = client.get("/auth", params={"token": "secret123"}, follow_redirects=False)
    assert resp.status_code == 302
    cookie = resp.headers.get("set-cookie", "")
    assert "openbb_session=" in cookie
    assert "secret123" not in cookie.split("openbb_session=")[1].split(";")[0]


def test_session_ids_differ_across_logins(_reset_sessions):
    """两次登录产生不同随机 session id（不可预测性）。"""
    from backend.app.core import access_middleware as mod

    sid1 = mod._create_session()
    sid2 = mod._create_session()
    assert sid1 != sid2


def test_logout_revokes_session(_reset_sessions, monkeypatch):
    """GET /auth/logout：吊销会话 + 清 Cookie；原会话随即失效。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.core import access_middleware as mod
    from backend.app.core.access_middleware import (
        AccessPasswordMiddleware,
        logout_response,
    )

    monkeypatch.setenv("OPENBB_ACCESS_PASSWORD", "secret123")
    app = FastAPI()
    app.add_middleware(AccessPasswordMiddleware)

    @app.get("/auth/logout")
    def auth_logout(request: Request):
        return logout_response(request)

    @app.get("/api/test")
    def t():
        return {"ok": True}

    client = TestClient(app)
    sid = mod._create_session()
    client.cookies.set("openbb_session", sid)
    assert client.get("/api/test").status_code == 200
    resp = client.get("/auth/logout", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers.get("location") == "/auth"
    assert mod._validate_session(sid) is False  # 服务端已吊销


def test_middleware_validates_session_cookie(_reset_sessions, _reset_fails, monkeypatch):
    """有效 session Cookie 放行 API；无效/吊销后 401。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.core import access_middleware as mod
    from backend.app.core.access_middleware import AccessPasswordMiddleware

    monkeypatch.setenv("OPENBB_ACCESS_PASSWORD", "secret123")
    app = FastAPI()
    app.add_middleware(AccessPasswordMiddleware)

    @app.get("/api/test")
    def t():
        return {"ok": True}

    client = TestClient(app)
    sid = mod._create_session()
    client.cookies.set("openbb_session", sid)
    assert client.get("/api/test").status_code == 200
    # 旧版口令 Cookie 名 access_token 不再被读取（旧会话强制失效）
    client.cookies.clear()
    client.cookies.set("access_token", "secret123")
    assert client.get("/api/test").status_code == 401


def test_html_shell_redirects_when_unauthed(_reset_sessions, _reset_fails, monkeypatch):
    """AR-14：未登录访问 / 直接 302 登录页（无骨架闪烁）；静态资源放行。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.core.access_middleware import AccessPasswordMiddleware

    monkeypatch.setenv("OPENBB_ACCESS_PASSWORD", "secret123")
    app = FastAPI()
    app.add_middleware(AccessPasswordMiddleware)

    @app.get("/")
    def index():
        return {"html": True}

    @app.get("/css/app.css")
    def css():
        return {"css": True}

    client = TestClient(app)
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers.get("location") == "/auth"
    assert client.get("/css/app.css").status_code == 200  # 静态资源不拦


def test_health_minimal_for_anonymous_full_for_authed(monkeypatch):
    """AR-14 health 分级：匿名=最小集（无 python/datasources），已鉴权=全量。

    直接调用路由函数（system.health）验证两级输出，不依赖全量 app。
    """

    from backend.app import config
    from backend.app.core import access_middleware as mod
    from backend.app.routers import system

    monkeypatch.setenv("OPENBB_ACCESS_PASSWORD", "secret123")

    def _req(headers):
        return Request({
            "type": "http", "headers": headers, "method": "GET", "path": "/api/health",
            "query_string": b"", "server": ("127.0.0.1", 6900),
            "client": ("127.0.0.1", 12345), "scheme": "http", "root_path": "",
        })

    anon = system.health(_req([]))
    assert anon["data"]["build"] == config.BUILD_TAG
    assert "python" not in anon["data"] and "datasources" not in anon["data"]

    sid = mod._create_session()
    authed_headers = [(b"cookie", f"openbb_session={sid}".encode())]
    full = system.health(_req(authed_headers))
    assert "python" in full["data"] and "datasources" in full["data"]


def test_docs_disabled_when_password_enabled(monkeypatch):
    """AR-14：启用口令时 create_app 禁用 /docs 与 /openapi.json。

    本项目统一信封把路由缺失也包成 HTTP 200 + NOT_FOUND（空/错分离口径），
    故断言 error_code 而非 HTTP 状态码。
    """
    from fastapi.testclient import TestClient

    from backend.app.main import create_app

    monkeypatch.setenv("OPENBB_ACCESS_PASSWORD", "secret123")
    client = TestClient(create_app())
    assert client.get("/docs").json()["error_code"] == "NOT_FOUND"
    assert client.get("/openapi.json").json()["error_code"] == "NOT_FOUND"
