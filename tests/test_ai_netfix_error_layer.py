# -*- coding: utf-8 -*-
"""第五轮 P1 回归：AI 路由 netfix 未导入（NameError）修复与异常分层。

背景（实测，A 报告 §一 / B 报告 §三）：
``routers/ai.py`` 顶部从未 import netfix，但 ``/api/ai/test`` 与 ``/api/ai/chat``
都调用 ``netfix.http_request(...)`` → 必抛 ``NameError``，被 ``except Exception``
吞成 ``UPSTREAM_ERROR``「无法连接 AI 接口（网络问题）」。表现为**1ms 返回**、
配置完全正确也永远失败；原有用例只断言 ``error_code == UPSTREAM_ERROR``，
NameError 被吞后同样判绿（假绿）。

本文件用四类断言锁死：
1. 成功路径必须**真实发到网络**（本地桩 HTTP 服务），修复前必失败；
2. 不可达主机必须是**网络类分因**（日志含 ConnectionError），且不得出现 NameError；
3. 内部缺陷（例如 NameError）必须报 ``INTERNAL_ERROR`` 并记录堆栈，不得伪装网络问题；
4. 对话路径同样走真实网络并返回真实回复。
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.routers import ai as ai_router

client = TestClient(app)


class _StubHandler(BaseHTTPRequestHandler):
    """OpenAI 兼容桩：记录请求并返回固定回复。"""

    calls: List[Dict[str, Any]] = []

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        """处理 POST /v1/chat/completions。

        Returns:
            None.
        """
        length = int(self.headers.get("Content-Length") or 0)
        raw_body = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw_body or "{}")
        except json.JSONDecodeError:
            parsed = {}
        _StubHandler.calls.append({
            "path": self.path,
            "auth": self.headers.get("Authorization"),
            "body": parsed,
        })
        payload = {
            "id": "chatcmpl-stub",
            "object": "chat.completion",
            "model": parsed.get("model") or "stub-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "STUB-REPLY-OK"},
                         "finish_reason": "stop"}],
        }
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        """静默桩日志，避免污染 pytest 输出。

        Args:
            fmt: 格式串。
            *args: 参数。

        Returns:
            None.
        """


@pytest.fixture()
def stub_base() -> Iterator[str]:
    """启动本地桩服务，返回其 base_url（端口随机，避免冲突）。

    Yields:
        str: 形如 ``http://127.0.0.1:<port>`` 的基址。
    """
    _StubHandler.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_ai_test_really_hits_network(stub_base: str) -> None:
    """/api/ai/test 必须真实发出 HTTP 请求并返回成功（修复前 NameError 必失败）。

    Args:
        stub_base: 本地桩基址。

    Returns:
        None.
    """
    body = client.post("/api/ai/test", json={
        "base_url": stub_base + "/v1", "api_key": "sk-stub", "model": "stub-model",
    }).json()

    assert body["error"] is None, f"应成功，实际：{body}"
    assert body["data"]["ok"] is True
    assert body["data"]["latency_ms"] >= 1, "真实网络请求不可能 0-1ms"
    assert "连接成功" in body["data"]["detail"]

    # 桩侧证据：路径、鉴权头、模型名都真实送达
    assert len(_StubHandler.calls) == 1
    call = _StubHandler.calls[0]
    assert call["path"] == "/v1/chat/completions"
    assert call["auth"] == "Bearer sk-stub"
    assert call["body"]["model"] == "stub-model"
    # 2026-11-18 AI 修复：/api/ai/test 的 max_tokens 由 1 先改为 64、再提为 512，
    # 兼容 GLM 推理模型的思考消耗（实测 reasoning_tokens=66 曾超过 64 预算导致
    # content 恒空、测试结果不稳定，故留足思考+输出预算）
    assert call["body"]["max_tokens"] == 512


def test_ai_chat_really_hits_network(stub_base: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """/api/ai/chat 必须真实发出请求并返回模型回复（修复前 NameError 必失败）。

    Args:
        stub_base: 本地桩基址。
        monkeypatch: pytest 补丁器。

    Returns:
        None.
    """
    monkeypatch.setattr(
        ai_router.config, "load_ai_config",
        lambda: SimpleNamespace(base_url=stub_base + "/v1", api_key="sk-stub", model="stub-model",
                                reasoning_effort="", context_kb=0, max_tokens=0),
    )
    body = client.post("/api/ai/chat", json={"message": "你好，做个自我介绍"}).json()

    assert body["error"] is None, f"应成功，实际：{body}"
    assert body["data"]["reply"] == "STUB-REPLY-OK"
    assert body["data"]["elapsed_ms"] >= 1
    assert _StubHandler.calls and _StubHandler.calls[-1]["path"] == "/v1/chat/completions"
    sent = _StubHandler.calls[-1]["body"]
    assert sent["messages"][-1] == {"role": "user", "content": "你好，做个自我介绍"}


def test_ai_chat_max_tokens_zero_omitted_from_request(
    stub_base: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """输出配置 max_tokens=0 → 不传该参数，按上游模型默认（2026-11-19 KB 配置改造）。"""
    monkeypatch.setattr(
        ai_router.config, "load_ai_config",
        lambda: SimpleNamespace(base_url=stub_base + "/v1", api_key="sk-stub", model="stub-model",
                                reasoning_effort="", context_kb=0, max_tokens=0),
    )
    client.post("/api/ai/chat", json={"message": "hi"})
    sent = _StubHandler.calls[-1]["body"]
    assert "max_tokens" not in sent
    assert sent["model"] == "stub-model"


def test_ai_chat_max_tokens_positive_is_sent(
    stub_base: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """输出配置 max_tokens>0 → 原样透传给上游。"""
    monkeypatch.setattr(
        ai_router.config, "load_ai_config",
        lambda: SimpleNamespace(base_url=stub_base + "/v1", api_key="sk-stub", model="stub-model",
                                reasoning_effort="", context_kb=0, max_tokens=2048),
    )
    client.post("/api/ai/chat", json={"message": "hi"})
    assert _StubHandler.calls[-1]["body"]["max_tokens"] == 2048


def test_ai_chat_context_kb_budget_truncates_older_messages(
    stub_base: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """上下文输入 context_kb>0：按 KB 字符预算从最新消息向前累积，旧消息被裁掉。"""
    monkeypatch.setattr(
        ai_router.config, "load_ai_config",
        lambda: SimpleNamespace(base_url=stub_base + "/v1", api_key="sk-stub", model="stub-model",
                                reasoning_effort="", context_kb=1, max_tokens=0),
    )
    history = [
        {"role": "user", "content": "A" * 900},
        {"role": "assistant", "content": "B" * 900},
        {"role": "user", "content": "最新问题"},
    ]
    client.post("/api/ai/chat", json={"messages": history})
    sent = _StubHandler.calls[-1]["body"]["messages"]
    assert sent[-1] == {"role": "user", "content": "最新问题"}
    # 1KB 预算装不下两条 900 字历史，第一条必须被裁掉
    assert all(m["content"] != "A" * 900 for m in sent)


def test_ai_chat_context_kb_zero_keeps_all_messages(
    stub_base: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """context_kb=0 → 不裁剪，完整历史送达上游。"""
    monkeypatch.setattr(
        ai_router.config, "load_ai_config",
        lambda: SimpleNamespace(base_url=stub_base + "/v1", api_key="sk-stub", model="stub-model",
                                reasoning_effort="", context_kb=0, max_tokens=0),
    )
    history = [
        {"role": "user", "content": "问题一"},
        {"role": "assistant", "content": "回答一"},
        {"role": "user", "content": "问题二"},
    ]
    client.post("/api/ai/chat", json={"messages": history})
    sent = _StubHandler.calls[-1]["body"]["messages"]
    assert len(sent) == 3
    assert sent[0] == {"role": "user", "content": "问题一"}


def test_ai_test_unreachable_is_network_not_nameerror(caplog: pytest.LogCaptureFixture) -> None:
    """不可达主机必须是网络类分因，且日志/响应均不得出现 NameError。

    Args:
        caplog: pytest 日志捕获器。

    Returns:
        None.
    """
    with caplog.at_level(logging.WARNING):
        body = client.post("/api/ai/test", json={
            "base_url": "http://127.0.0.1:9/v1", "api_key": "sk-x", "model": "m",
        }).json()

    assert body["error_code"] == "UPSTREAM_ERROR"
    assert "NameError" not in json.dumps(body, ensure_ascii=False)
    assert "NameError" not in caplog.text
    # 走的是 RequestException 分支（真实网络异常），而不是笼统兜底
    assert "网络类" in caplog.text
    assert "ConnectionError" in caplog.text or "ConnectError" in caplog.text


def test_ai_test_internal_defect_maps_to_internal_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """内部缺陷（NameError）必须报 INTERNAL_ERROR 并记堆栈，不得伪装网络问题。

    Args:
        monkeypatch: pytest 补丁器。
        caplog: pytest 日志捕获器。

    Returns:
        None.
    """

    def _boom(*args: Any, **kwargs: Any) -> Any:
        """模拟修复前的 NameError。

        Raises:
            NameError: 固定抛出。

        Returns:
            Any: 正常路径不会返回。
        """
        raise NameError("name 'netfix' is not defined")

    monkeypatch.setattr(ai_router, "netfix", SimpleNamespace(http_request=_boom))
    with caplog.at_level(logging.ERROR):
        body = client.post("/api/ai/test", json={
            "base_url": "https://api.example.com/v1", "api_key": "sk-x", "model": "m",
        }).json()

    assert body["error_code"] == "INTERNAL_ERROR", "代码缺陷不得报成 UPSTREAM_ERROR"
    assert "内部异常" in caplog.text
    assert "NameError" in caplog.text, "堆栈需落日志便于排查"


def test_ai_chat_internal_defect_maps_to_internal_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """/api/ai/chat 的内部缺陷同样报 INTERNAL_ERROR。

    Args:
        monkeypatch: pytest 补丁器。
        caplog: pytest 日志捕获器。

    Returns:
        None.
    """

    def _boom(*args: Any, **kwargs: Any) -> Any:
        """模拟未预期异常。

        Raises:
            AttributeError: 固定抛出。

        Returns:
            Any: 正常路径不会返回。
        """
        raise AttributeError("unexpected internal bug")

    monkeypatch.setattr(
        ai_router.config, "load_ai_config",
        lambda: SimpleNamespace(base_url="https://api.example.com/v1", api_key="sk-x", model="m",
                                reasoning_effort="", context_kb=0, max_tokens=0),
    )
    monkeypatch.setattr(ai_router, "netfix", SimpleNamespace(http_request=_boom))
    with caplog.at_level(logging.ERROR):
        body = client.post("/api/ai/chat", json={"message": "hi"}).json()

    assert body["error_code"] == "INTERNAL_ERROR"
    assert "内部异常" in caplog.text
