# -*- coding: utf-8 -*-
"""P5 访问口令中间件测试。"""

from __future__ import annotations

import pytest


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
    from starlette.requests import Request

    scope = {
        "type": "http", "headers": [(b"authorization", b"Bearer mytoken")],
        "method": "GET", "path": "/api/test", "query_string": b"",
        "server": ("127.0.0.1", 6900), "client": ("127.0.0.1", 12345),
        "scheme": "http", "root_path": "",
    }
    req = Request(scope)
    assert mod._extract_token(req) == "mytoken"


def test_extract_token_from_cookie():
    """Cookie access_token 提取。"""
    from backend.app.core import access_middleware as mod
    from starlette.requests import Request

    scope = {
        "type": "http", "headers": [(b"cookie", b"access_token=cookietoken")],
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

    assert any("/api/system/health".startswith(p) for p in mod._ALLOWED_PREFIXES)


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

    @app.get("/api/system/health")
    def t():
        return {"ok": True}

    client = TestClient(app)
    resp = client.get("/api/system/health")
    assert resp.status_code == 200
