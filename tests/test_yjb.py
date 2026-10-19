# -*- coding: utf-8 -*-
"""养基宝连接器测试（阶段 7）：状态机 / 四要件 / 加密存储 / 开关与降级。

真实接口路径保留在 datasource/yangjibao.py；本文件对 HTTP 边界做状态机模拟
（规范 §12.8 允许「不必真实扫码」），但所有分支都走真实实现代码。
"""

from __future__ import annotations

import json

import pytest
import requests
from fastapi.testclient import TestClient

from backend.app import config
from backend.app.core import cache
from backend.app.datasource import yangjibao as yjb
from backend.app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    """把 token/状态文件重定向到临时目录，避免触碰真实 data/。"""
    monkeypatch.setattr(yjb, "YJB_TOKEN_FILE", tmp_path / "yjb_token.json")
    monkeypatch.setattr(yjb, "YJB_STATE_FILE", tmp_path / "yjb_state.json")
    cache.clear_cache()
    yield
    cache.clear_cache()


def test_status_unbound_and_no_token_leak() -> None:
    """未绑定状态：字段齐全且响应中不含 token 字段。"""
    body = client.get("/api/fund/yjb/status").json()
    assert body["error"] is None
    data = body["data"]
    assert data["bound"] is False
    assert data["enabled"] is False
    # 不出现 token 字段（caliber 文案里提到“token 加密存储”不算泄漏）
    assert '"token"' not in json.dumps(data).lower()
    assert "告知书" in data["consent_text"]


def test_toggle_requires_consent() -> None:
    """未确认告知书时开启增强通道必须被拒绝。"""
    body = client.post("/api/fund/yjb/toggle", json={"enabled": True}).json()
    assert body["error_code"] == "INVALID_PARAM"
    assert "告知书" in body["error"]
    assert body["data"]["enabled"] is False


def test_consent_then_enable_and_disable() -> None:
    """确认后可开启；关闭后真实回退 T+1 口径（清缓存后验证）。"""
    assert client.post("/api/fund/yjb/consent", json={"accepted": True}).json()["error"] is None
    on = client.post("/api/fund/yjb/toggle", json={"enabled": True}).json()
    assert on["data"]["enabled"] is True
    off = client.post("/api/fund/yjb/toggle", json={"enabled": False}).json()
    assert off["data"]["enabled"] is False
    status = client.get("/api/fund/yjb/status").json()["data"]
    assert status["enabled"] is False and status["consent"]["accepted"] is True


def test_bind_without_session_rejected() -> None:
    """无扫码会话直接绑定 → 参数错误。"""
    assert client.post("/api/fund/yjb/bind", json={}).json()["error_code"] == "INVALID_PARAM"
    assert client.post("/api/fund/yjb/bind", json={"session_id": "nope"}).json()["error_code"] == "UNAUTHORIZED"


def test_poll_unknown_session_expired() -> None:
    """未知/过期会话返回 expired，不抛异常。"""
    body = client.get("/api/fund/yjb/poll", params={"session_id": "missing"}).json()
    assert body["data"]["state"] == "expired"
    assert "刷新" in body["data"]["message"]


def test_four_elements_404_is_deterministic(monkeypatch) -> None:
    """404 → 确定性无效（可清 token）；网络异常 → 非确定性（不清 token）。"""
    monkeypatch.setattr(yjb, "_request", lambda *a, **k: {"status": 404, "text": ""})
    result = yjb.verify_token_four_elements("bad-token")
    assert result["deterministic_invalid"] is True and result["ok"] is False

    def boom(*args, **kwargs):
        raise requests.exceptions.ConnectTimeout("timeout")

    monkeypatch.setattr(yjb, "_request", boom)
    jitter = yjb.verify_token_four_elements("good-token")
    assert jitter["deterministic_invalid"] is False and jitter["ok"] is False
    assert "网络" in jitter["reason"]


def test_four_elements_success(monkeypatch) -> None:
    """四要件齐全才判有效。"""
    monkeypatch.setattr(yjb, "_request", lambda *a, **k: {
        "status": 200, "text": json.dumps({"code": 200, "data": {"id": 1}})})
    assert yjb.verify_token_four_elements("t")["ok"] is True
    monkeypatch.setattr(yjb, "_request", lambda *a, **k: {
        "status": 200, "text": json.dumps({"code": 200, "data": {}})})
    assert yjb.verify_token_four_elements("t")["deterministic_invalid"] is True


def test_token_encrypted_at_rest(tmp_path, monkeypatch) -> None:
    """token 落盘为 DPAPI 密文：文件不含明文，读取可还原，状态接口不回显。"""
    monkeypatch.setattr(yjb, "YJB_TOKEN_FILE", tmp_path / "yjb_token.json")
    secret = "unit-test-token-1234567890"
    yjb.save_token(secret, "ios")
    blob = (tmp_path / "yjb_token.json").read_bytes()
    assert secret.encode() not in blob
    assert blob.startswith(b"YJB1")
    assert yjb.load_token()["token"] == secret
    payload = client.get("/api/fund/yjb/status").json()
    assert secret not in json.dumps(payload, ensure_ascii=False)


def test_unbind_clears_token(tmp_path, monkeypatch) -> None:
    """解绑后 token 文件消失、状态回到未绑定。"""
    monkeypatch.setattr(yjb, "YJB_TOKEN_FILE", tmp_path / "yjb_token.json")
    yjb.save_token("abc", "ios")
    assert (tmp_path / "yjb_token.json").exists()
    assert client.post("/api/fund/yjb/unbind").json()["data"]["bound"] is False
    assert not (tmp_path / "yjb_token.json").exists()
    assert client.get("/api/fund/yjb/status").json()["data"]["bound"] is False
