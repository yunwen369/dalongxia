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
    """把 token/状态/盐文件重定向到临时目录，避免触碰真实 data/。"""
    monkeypatch.setattr(yjb, "YJB_TOKEN_FILE", tmp_path / "yjb_token.json")
    monkeypatch.setattr(yjb, "YJB_STATE_FILE", tmp_path / "yjb_state.json")
    monkeypatch.setattr(yjb, "YJB_SECRET_FILE", tmp_path / "yjb_secret.json")
    # 会话表是进程级 dict，逐用例清空，避免用例间互相污染
    yjb._SESSIONS.clear()  # noqa: SLF001 - 测试隔离需直接清会话表
    cache.clear_cache()
    yield
    yjb._SESSIONS.clear()  # noqa: SLF001 - 用例结束同样清空
    cache.clear_cache()


def test_qrcode_session_carries_expires_in(monkeypatch) -> None:
    """R9：二维码会话必须下发 expires_in，供前端推导轮询次数（不再硬编码 60）。"""
    monkeypatch.setattr(yjb, "_load_secret", lambda: ("fake-new", "fake-old"))

    def fake_request(method, base, path, token="", params=None, use_old=False):
        """返回一份符合 /qr_code 契约的假响应（不触网）。"""
        return {"status": 200, "text": json.dumps({"data": {"url": "https://example.com/q", "id": "sess-1"}}),
                "base": base, "scheme": "https"}

    monkeypatch.setattr(yjb, "_request", fake_request)
    result = yjb.start_qrcode_session()
    assert result["session_id"] == "sess-1"
    assert result["expires_in"] == yjb.QRCODE_TTL > 0
    assert result["url"] == "https://example.com/q"
    # segno 存在时应渲染出 PNG base64；缺失时留空但不得报错
    assert isinstance(result["image_base64"], str)


def test_session_table_is_bounded(monkeypatch) -> None:
    """R4：会话表不得无界增长——超上限时淘汰最旧，长期运行不泄漏内存。"""
    now = 1_700_000_000.0
    monkeypatch.setattr(yjb.time, "time", lambda: now)
    for index in range(yjb._SESSION_MAX + 20):  # noqa: SLF001 - 直接构造超量会话
        yjb._SESSIONS[f"s{index}"] = {"created_at": now - index, "state": "waiting", "token": None}
    yjb._purge_sessions()
    assert len(yjb._SESSIONS) <= yjb._SESSION_MAX  # noqa: SLF001
    # 最旧的应被淘汰，最新的保留
    assert "s0" in yjb._SESSIONS  # noqa: SLF001
    assert f"s{yjb._SESSION_MAX + 19}" not in yjb._SESSIONS  # noqa: SLF001


def test_expired_session_purged_by_ttl(monkeypatch) -> None:
    """R4：超过 2×TTL 的会话必须被清理（含已成功会话，防止 token 长期滞留内存）。"""
    now = 1_700_000_000.0
    monkeypatch.setattr(yjb.time, "time", lambda: now)
    old_created = now - (yjb.QRCODE_TTL * 2 + 5)
    yjb._SESSIONS["stale"] = {"created_at": old_created, "state": "success", "token": "x"}  # noqa: SLF001
    yjb._SESSIONS["fresh"] = {"created_at": now, "state": "waiting", "token": None}  # noqa: SLF001
    yjb._purge_sessions()
    assert "stale" not in yjb._SESSIONS  # noqa: SLF001
    assert "fresh" in yjb._SESSIONS  # noqa: SLF001


def test_secret_encrypted_at_rest_and_cleared() -> None:
    """R1：签名盐只以密文落盘（不含明文），清除后文件消失、状态回到未配置。"""
    assert yjb.set_secret("PLAINTEXT-NEW-abc", "PLAINTEXT-OLD-xyz") is True
    assert yjb.YJB_SECRET_FILE.exists()
    raw = yjb.YJB_SECRET_FILE.read_bytes()
    assert b"PLAINTEXT-NEW-abc" not in raw, "签名盐不得以明文落盘"
    assert b"PLAINTEXT-OLD-xyz" not in raw, "签名盐不得以明文落盘"
    assert yjb.secret_configured() is True
    assert yjb.secret_source() == "本地加密配置"

    yjb.clear_secret()
    assert not yjb.YJB_SECRET_FILE.exists()
    assert yjb.secret_configured() is False
    assert yjb.secret_source() is None


def test_secret_env_takes_priority(monkeypatch) -> None:
    """R1：环境变量优先于本地加密配置（运维可通过环境变量覆盖、随版本更新盐）。"""
    yjb.set_secret("local-new", "local-old")
    monkeypatch.setenv("YJB_SECRET_NEW", "env-new")
    monkeypatch.setenv("YJB_SECRET_OLD", "env-old")
    assert yjb._load_secret() == ("env-new", "env-old")  # noqa: SLF001
    assert yjb.secret_source() == "环境变量"
    yjb.clear_secret()


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
    """解绑后 token 文件消失、状态回到未绑定（RT-01 后需显式 confirm=true）。"""
    monkeypatch.setattr(yjb, "YJB_TOKEN_FILE", tmp_path / "yjb_token.json")
    yjb.save_token("abc", "ios")
    assert (tmp_path / "yjb_token.json").exists()
    assert client.post("/api/fund/yjb/unbind", json={"confirm": True}).json()["data"]["bound"] is False
    assert not (tmp_path / "yjb_token.json").exists()
    assert client.get("/api/fund/yjb/status").json()["data"]["bound"] is False


# =============================================================
# RT-01 安全回归：破坏性 POST 空 body 一律拒绝执行（第五波新增）
# =============================================================
def test_destructive_posts_reject_empty_body(tmp_path, monkeypatch) -> None:
    """RT-01：consent/toggle/unbind/secret(清除) 空 body 不得产生任何状态变更。

    回归点：第四方验收 run1 全端点枚举时空 body 击穿四个端点，
    导致本机真实绑定被解除（token/盐被删）。本用例把三个凭据/状态文件
    哈希前后比对，空 body 必须原样保留且返回 INVALID_PARAM。
    """
    import hashlib

    monkeypatch.setattr(yjb, "YJB_TOKEN_FILE", tmp_path / "yjb_token.json")
    monkeypatch.setattr(yjb, "YJB_SECRET_FILE", tmp_path / "yjb_secret.json")
    monkeypatch.setattr(yjb, "YJB_STATE_FILE", tmp_path / "yjb_state.json")
    # 造一份「已绑定 + 盐已配置 + 已确认告知书」的真实初始态
    yjb.save_token("real-token-abcdef", "ios")
    yjb.set_secret("SECRET-NEW-1", "SECRET-OLD-1")
    yjb.accept_consent(True)

    def _digest() -> dict:
        """三个凭据/状态文件的当前 SHA256 快照（token/盐不读内容）。"""
        snapshot = {}
        for path in (tmp_path / "yjb_token.json", tmp_path / "yjb_secret.json", tmp_path / "yjb_state.json"):
            snapshot[path.name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "MISSING"
        return snapshot

    before = _digest()
    # 四个破坏性端点逐一以空 body（或仅空 JSON 对象）击穿测试
    empty_bodies = [
        ("/api/fund/yjb/consent", {}),
        ("/api/fund/yjb/toggle", {}),
        ("/api/fund/yjb/unbind", {}),
        ("/api/fund/yjb/secret", {}),
    ]
    for url, payload in empty_bodies:
        body = client.post(url, json=payload).json()
        assert body["error_code"] == "INVALID_PARAM", f"{url} 空 body 必须被拒绝"
        assert body["error"], f"{url} 必须给出中文拒绝原因"
    # 完全无 body 的 POST（Content-Length=0）同样必须拒绝
    body = client.post("/api/fund/yjb/unbind", content=b"").json()
    assert body["error_code"] == "INVALID_PARAM"
    # 缺确认标识/字段类型错误同样拒绝（不只看空对象）
    assert client.post("/api/fund/yjb/toggle", json={"enabled": "yes"}).json()["error_code"] == "INVALID_PARAM"
    assert client.post("/api/fund/yjb/unbind", json={"confirm": False}).json()["error_code"] == "INVALID_PARAM"
    after = _digest()
    assert after == before, "空 body 击穿后凭据/状态文件必须逐字节不变"
    # 初始态保留：仍绑定、盐仍在、告知书确认记录仍在（未被空 body 清掉）
    status = client.get("/api/fund/yjb/status").json()["data"]
    assert status["bound"] is True
    assert status["secret_configured"] is True
    assert status["consent"]["accepted"] is True


def test_destructive_posts_execute_with_confirm(tmp_path, monkeypatch) -> None:
    """带正确确认标识时才真正执行（与上空 body 用例互为对照）。"""
    monkeypatch.setattr(yjb, "YJB_TOKEN_FILE", tmp_path / "yjb_token.json")
    monkeypatch.setattr(yjb, "YJB_SECRET_FILE", tmp_path / "yjb_secret.json")
    yjb.save_token("real-token-abcdef", "ios")
    yjb.set_secret("SECRET-NEW-1", "SECRET-OLD-1")
    # toggle 显式 false → 关闭（不抛错、无确认标识要求，因字段已显式）
    assert client.post("/api/fund/yjb/toggle", json={"enabled": False}).json()["error"] is None
    # consent 显式 false → 拒绝告知书（可恢复操作）
    assert client.post("/api/fund/yjb/consent", json={"accepted": False}).json()["error"] is None
    # secret 清除：显式 confirm=true 才执行
    cleared = client.post("/api/fund/yjb/secret", json={"confirm": True}).json()
    assert cleared["error"] is None
    assert cleared["data"]["secret_configured"] is False
    assert not (tmp_path / "yjb_secret.json").exists()
    # unbind：显式 confirm=true 才删除 token
    assert client.post("/api/fund/yjb/unbind", json={"confirm": True}).json()["error"] is None
    assert not (tmp_path / "yjb_token.json").exists()


def test_holdings_view_calls_user_account_once(monkeypatch) -> None:
    """YJB-P2-5：合并全部账户持仓时 /user_account 只允许调 1 次。

    回归点：早期实现逐账户循环 get_holdings，每次内部又解析一次账户列表，
    3 账户 = 4 次 /user_account（限频风险 + 无谓耗时）。本用例以调用计数锁死。
    """
    monkeypatch.setattr(yjb, "YJB_TOKEN_FILE", yjb.YJB_TOKEN_FILE)  # 沿用夹具隔离
    yjb.save_token("real-token-abcdef", "ios")
    calls = {"user_account": 0, "fund_hold": 0}

    def fake_plug_get(path, params=None):
        """按路径返回契约假数据并计数（不触网）。"""
        base = str(path).split("?")[0]
        if base == "/user_account":
            calls["user_account"] += 1
            return {"is_single_account_model": False, "target_account_id": "acc-1",
                    "list": [{"id": "acc-1", "title": "主账户"}, {"id": "acc-2", "title": "第二账户"}]}
        if base == "/fund_hold":
            calls["fund_hold"] += 1
            return [{"code": "000001", "short_name": "测试基金", "hold_share": "100.00",
                     "hold_cost": "10000.00", "hold_sum": "10500.00",
                     "nv_info": {"gszzl": "1.23", "dwjz": "1.0520", "gztime": "2026-10-26 10:00:00"}}]
        raise AssertionError(f"未预期的上游路径：{path}")

    monkeypatch.setattr(yjb, "_plug_get", fake_plug_get)
    view = yjb.get_holdings_view("all")
    assert view["total"] == 2, "两账户各 1 条持仓"
    assert calls["user_account"] == 1, f"/user_account 必须只调 1 次，实际 {calls['user_account']} 次"
    assert calls["fund_hold"] == 2, "两账户应各拉一次 /fund_hold"


# =============================================================
# 真实契约对齐（2026-10-25，GitHub 公开实现 zwls666/yangjibao-edge-extension）
# =============================================================
def test_sign_uses_seconds_and_no_query_string(monkeypatch) -> None:
    """签名算法：md5(去查询串 path + token + 秒级时间戳 + SECRET)。

    回归点：早期实现有三处错误——① 含基址；② 毫秒时间戳；③ secret 与 timestamp 顺序颠倒。
    这三处任一存在，上游都会返回 `code:401 非法请求`。
    """
    import hashlib

    monkeypatch.setenv("YJB_SECRET_OLD", "TEST-SECRET")
    ts = "1792868308"
    expected = hashlib.md5(("/qr_code" + "tok" + ts + "TEST-SECRET").encode()).hexdigest()

    # 含查询串时签名只取 path 部分
    assert yjb.sign_old("/qr_code?x=1", "tok", ts) == expected
    assert yjb.sign_old("/qr_code", "tok", ts) == expected
    # token 参与签名
    assert yjb.sign_old("/qr_code", "other", ts) != expected


def test_qr_poll_state_mapping(monkeypatch) -> None:
    """状态码：2+token=成功；3=失效；1/0=等待。"""
    def make(state, token=""):
        def fake(method, base, path, token_="", params=None, use_old=False):
            payload = {"code": 200, "data": {"state": state, "token": token}}
            return {"status": 200, "text": json.dumps(payload), "base": base, "scheme": "https"}
        return fake

    import time as _t
    now = _t.time()

    # state=2 且带 token → 成功并暂存 token
    yjb._SESSIONS["s1"] = {"created_at": now, "state": "waiting", "token": None}  # noqa: SLF001
    monkeypatch.setattr(yjb, "_request", make("2", "TOKEN-X"))
    result = yjb.poll_session("s1")
    assert result["state"] == "success"
    assert yjb._SESSIONS["s1"]["token"] == "TOKEN-X"  # noqa: SLF001

    # state=3 → 失效
    yjb._SESSIONS["s2"] = {"created_at": now, "state": "waiting", "token": None}  # noqa: SLF001
    monkeypatch.setattr(yjb, "_request", make("3"))
    assert yjb.poll_session("s2")["state"] == "expired"

    # state=1 → 等待（不得误判成已扫码）
    yjb._SESSIONS["s3"] = {"created_at": now, "state": "waiting", "token": None}  # noqa: SLF001
    monkeypatch.setattr(yjb, "_request", make("1"))
    assert yjb.poll_session("s3")["state"] == "waiting"


def test_income_rate_reads_nv_info_fields(monkeypatch) -> None:
    """估值字段：优先 `nv_info.gszzl` → `zsgzzl` → `vgszzl`；无涨幅即报错不猜。"""
    monkeypatch.setattr(yjb, "load_token", lambda: {"token": "t"})
    monkeypatch.setattr(yjb, "get_holdings", lambda account_id="": [
        {"code": "017811", "fund_id": 123, "short_name": "测试基金",
         "nv_info": {"vgszzl": "1.23", "time": "2026-10-24 15:00:00"}},
        {"code": "110022", "fund_id": 456, "vgszzl": "+2.50"},
        {"code": "000001", "fund_id": 789},
    ])
    got = yjb.fetch_increase_rate("017811")
    assert got["pct"] == 1.23
    assert got["as_of"] == "2026-10-24 15:00:00"
    assert yjb.fetch_increase_rate("110022")["pct"] == 2.50

    with pytest.raises(yjb.NonRetryableError):
        yjb.fetch_increase_rate("000001")
    with pytest.raises(yjb.NonRetryableError):
        yjb.fetch_increase_rate("999999")


def test_qrcode_parses_real_response_shape(monkeypatch) -> None:
    """真实 /qr_code 响应形状：{code:200, data:{url, id}} → 解析出 session 与 url。"""
    def fake(method, base, path, token="", params=None, use_old=False):
        payload = {"code": 200, "message": "SUCCESS",
                   "data": {"url": "http://weixin.qq.com/q/REAL", "id": "loginQrId123"}}
        return {"status": 200, "text": json.dumps(payload), "base": base, "scheme": "https"}

    monkeypatch.setattr(yjb, "_request", fake)
    monkeypatch.setattr(yjb, "secret_configured", lambda: True)
    result = yjb.start_qrcode_session()
    assert result["session_id"] == "loginQrId123"
    assert result["url"] == "http://weixin.qq.com/q/REAL"
    assert result["expires_in"] == 240


# =============================================================
# 工作台数据接入（Y-05 账户概览 / Y-06 持仓估值）
# =============================================================
def test_collect_unbound_degrades(monkeypatch) -> None:
    """未绑定：/collect 与 /holdings 都返回 UPSTREAM_ERROR，不得返回假数据。

    说明：本文件的 autouse 夹具把 YJB_TOKEN_FILE 重定向到 tmp_path，
    因此这里天然处于「未绑定」态——正好用来验证降级契约。
    真实账号的字段契约见 test_qa_verify.py（那里不隔离 token）。
    """
    from backend.app.core import cache

    monkeypatch.setattr(yjb, "load_token", lambda: None)
    cache.clear_cache()
    for path in ("/api/fund/yjb/collect", "/api/fund/yjb/holdings"):
        body = client.get(path).json()
        assert body["error_code"] == "UPSTREAM_ERROR", f"{path} 实际 {body['error_code']}"
        assert body["error"] and "未绑定" in body["error"]
        assert not (body.get("data") or {}).get("total_assets")
    cache.clear_cache()
