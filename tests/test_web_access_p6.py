# -*- coding: utf-8 -*-
"""Web 化访问支撑（P6）测试：局域网地址探测、二维码、访问方式端点。"""

from __future__ import annotations


def test_lan_addresses_excludes_loopback():
    """局域网地址列表不含回环地址，且每项都是合法 IPv4 形态。"""
    from backend.app.core import web_access

    for address in web_access.lan_addresses():
        assert not address.startswith("127.")
        assert address.count(".") == 3


def test_access_points_without_lan_only_local():
    """未放开局域网监听时只返回本机入口。"""
    from backend.app.core import web_access

    points = web_access.access_points(6900, False)
    assert len(points) == 1
    assert points[0]["scope"] == "local"
    assert points[0]["url"] == "http://127.0.0.1:6900"


def test_access_points_with_lan_keeps_local_first():
    """放开局域网监听时本机入口仍排第一，其余均为局域网地址。"""
    from backend.app.core import web_access

    points = web_access.access_points(6900, True)
    assert points[0]["scope"] == "local"
    for point in points[1:]:
        assert point["scope"] == "lan"
        assert point["url"].startswith("http://")
        assert ":6900" in point["url"]


def test_qr_data_uri_is_png():
    """二维码输出 PNG data URI；空文本不做编码。"""
    from backend.app.core import web_access

    uri = web_access.qr_data_uri("http://127.0.0.1:6900")
    assert uri.startswith("data:image/png;base64,")
    assert len(uri) > 200
    assert web_access.qr_data_uri("") == ""


def test_access_endpoint_envelope():
    """访问方式端点返回统一信封，入口带二维码且字段齐全。"""
    from fastapi.testclient import TestClient

    from backend.app.main import app

    client = TestClient(app)
    response = client.get("/api/system/access")
    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    data = payload["data"]
    assert data["port"] == 6900
    assert data["points"]
    assert data["points"][0]["scope"] == "local"
    assert data["points"][0]["qr"].startswith("data:image/png;base64,")
    for key in ("lan_visible", "password_required", "source", "caliber", "as_of", "is_realtime"):
        assert key in data


def test_lan_requested_by_env(monkeypatch):
    """OPENBB_HOST 指向全网卡时视为已放开局域网。"""
    from backend.app import main as main_module

    monkeypatch.setattr(main_module.config, "HOST", "0.0.0.0")
    monkeypatch.setattr(main_module.sys, "argv", ["main"])
    assert main_module._lan_requested() is True


def test_lan_requested_false_by_default(monkeypatch):
    """默认回环监听且无 --lan 参数时不放开局域网。"""
    from backend.app import main as main_module

    monkeypatch.setattr(main_module.config, "HOST", "127.0.0.1")
    monkeypatch.setattr(main_module.sys, "argv", ["main"])
    assert main_module._lan_requested() is False


def test_tunnel_parse_url():
    """从 cloudflared 日志行中提取 quick tunnel 公网地址。"""
    from backend.app.core import public_tunnel

    line = "2026-11-08T00:00:00Z INF |  https://brave-tiger-music-abc.trycloudflare.com  |"
    assert public_tunnel.parse_url(line) == "https://brave-tiger-music-abc.trycloudflare.com"
    assert public_tunnel.parse_url("INF Starting metrics server on 127.0.0.1:20241") is None
    assert public_tunnel.parse_url("") is None


def test_tunnel_state_roundtrip():
    """隧道状态可写入读取，清空后地址为空。"""
    from backend.app.core import public_tunnel

    public_tunnel.set_state(url="https://demo.trycloudflare.com")
    snapshot = public_tunnel.state()
    assert snapshot["url"] == "https://demo.trycloudflare.com"
    assert snapshot["started_at"]
    public_tunnel.set_state()
    assert public_tunnel.state()["url"] == ""


def test_access_endpoint_includes_public_block():
    """访问方式端点带 public 状态块（未开隧道时 url 为空）。"""
    from fastapi.testclient import TestClient

    from backend.app.main import app

    client = TestClient(app)
    payload = client.get("/api/system/access").json()
    public = payload["data"]["public"]
    assert set(public) == {"url", "error", "started_at", "ready"}
    assert public["url"] == ""
    assert public["ready"] is False
