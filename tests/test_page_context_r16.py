# -*- coding: utf-8 -*-
"""R16/C16：Copilot 页面上下文注入单测。

面 1：page_context 构造器（provider/cache monkeypatch，全离线）；
面 2：/api/ai/chat 注入链路（本地桩 HTTP 服务实录出站 messages）；
面 3：失败静默（构建返回 None → 不注入、对话行为与 R16 之前一致）。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Iterator, List
from types import SimpleNamespace

import pytest

from backend.app.datasource import page_context


# ============================================================
# 一、构造器（全离线）
# ============================================================

def test_sectors_summary_has_board_names_and_pcts(monkeypatch: pytest.MonkeyPatch) -> None:
    """C16 数据面：云图页摘要必须含页内真实板块名与涨幅数字。"""
    rows = [
        {"name": "银行", "pct": 0.99, "amount": 354.36},
        {"name": "半导体", "pct": -2.10, "amount": 2264.13},
        {"name": "元件", "pct": -5.48, "amount": 793.29},
        {"name": "种植业与林业", "pct": 1.67, "amount": 93.94},
    ]
    monkeypatch.setattr(page_context.cache, "get", lambda key, ttl, **kw: None)
    import backend.app.datasource.sector as sector_module

    monkeypatch.setattr(sector_module, "get_industry", lambda market="cn": rows)
    monkeypatch.setattr(sector_module, "get_concept", lambda market="cn": [])
    text = page_context.build_summary("sectors", {})
    assert text is not None
    assert "银行" in text and "0.99" in text
    assert "元件" in text and "-5.48" in text
    assert "涨幅" in text and "跌幅" in text
    assert "不得编造" in text


def test_sectors_summary_from_cache_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    """缓存信封（路由写入形状 {data:[...]}）优先命中=与页面同源。"""
    hit = {"data": [{"name": "电力", "pct": 0.76, "amount": 348.79}], "source": "同花顺"}

    def fake_get(key: str, ttl: int, **kwargs: Any) -> Any:
        """只命中行业缓存键。

        Args:
            key: 缓存键。
            ttl: 参数（未用）。
            **kwargs: 透传。

        Returns:
            Any: 行业键返回信封，其余 None。
        """
        return hit if key == "sector:industry:cn" else None

    monkeypatch.setattr(page_context.cache, "get", fake_get)
    text = page_context.build_summary("sectors", {})
    assert text and "电力" in text and "0.76" in text


def test_stock_summary_uses_quote(monkeypatch: pytest.MonkeyPatch) -> None:
    """个股页摘要：报价字段全量入文（与页面行情同源）。"""
    import backend.app.datasource.tencent as tencent_module

    monkeypatch.setattr(tencent_module, "get_quote", lambda symbol, market="a": {
        "name": "贵州茅台", "price": 1453.0, "pct": 0.07, "high": 1457.72,
        "low": 1436.0, "open": 1450.0, "prev_close": 1452.0, "turnover": 0.34,
    })
    text = page_context.build_summary("stock", {"symbol": "600519"})
    assert text and "贵州茅台" in text and "1453.0" in text and "+0.07%" in text


def test_summary_none_cases_never_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """未知页面 / 缺参数 / provider 抛错 → 全部 None，不向上抛。"""
    assert page_context.build_summary("settings", {}) is None
    assert page_context.build_summary("stock", {}) is None
    assert page_context.build_summary("", {}) is None

    import backend.app.datasource.tencent as tencent_module

    def _boom(symbol: str, market: str = "a") -> Dict[str, Any]:
        """抛错替身。

        Args:
            symbol: 代码。
            market: 市场。

        Returns:
            dict: 不会返回。

        Raises:
            RuntimeError: 恒定。
        """
        raise RuntimeError("源站炸了")

    monkeypatch.setattr(tencent_module, "get_quote", _boom)
    assert page_context.build_summary("stock", {"symbol": "600519"}) is None


def test_crowding_summary_cache_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """拥挤度页只读排行缓存（绝不触发全量扫描）：未命中=None。"""
    monkeypatch.setattr(page_context.cache, "get", lambda key, ttl, **kw: None)
    assert page_context.build_summary("crowding", {}) is None

    warm = {"items": [{"board": "半导体", "score": 82.5}, {"board": "银行", "score": 31.0}]}
    monkeypatch.setattr(page_context.cache, "get", lambda key, ttl, **kw: warm)
    text = page_context.build_summary("crowding", {})
    assert text and "半导体 82.5" in text


# ============================================================
# 二、/api/ai/chat 注入链路（桩服务实录出站 messages）
# ============================================================

class _StubHandler(BaseHTTPRequestHandler):
    """OpenAI 兼容桩：记录出站 body 并返回固定回复。"""

    calls: List[Dict[str, Any]] = []

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        """处理 /v1/chat/completions。

        Returns:
            None.
        """
        length = int(self.headers.get("Content-Length") or 0)
        raw_body = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw_body or "{}")
        except json.JSONDecodeError:
            parsed = {}
        _StubHandler.calls.append({"body": parsed})
        payload = {
            "id": "chatcmpl-stub", "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "STUB-OK"},
                         "finish_reason": "stop"}],
        }
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        """静默桩日志。

        Args:
            fmt: 格式串。
            *args: 参数。

        Returns:
            None.
        """


@pytest.fixture()
def stub_base() -> Iterator[str]:
    """本地桩基址。

    Yields:
        str: http://127.0.0.1:<port>。
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


@pytest.fixture()
def client() -> Any:
    """FastAPI 测试客户端。

    Returns:
        TestClient: 应用客户端。
    """
    from fastapi.testclient import TestClient

    from backend.app.main import app

    return TestClient(app)


def test_chat_injects_system_when_page_supported(
    client: Any, stub_base: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C16 链路面：page=stock 时出站首条=system 且含页面真实数值，context_used=True。"""
    from backend.app.routers import ai as ai_router

    monkeypatch.setattr(
        ai_router.config, "load_ai_config",
        lambda: SimpleNamespace(base_url=stub_base + "/v1", api_key="sk-stub", model="stub-model",
                                reasoning_effort="", context_kb=0, max_tokens=0),
    )
    import backend.app.datasource.tencent as tencent_module

    monkeypatch.setattr(tencent_module, "get_quote", lambda symbol, market="a": {
        "name": "平安银行", "price": 11.91, "pct": 0.5,
    })
    body = client.post("/api/ai/chat", json={
        "messages": [{"role": "user", "content": "这只票现在怎么样"}],
        "page": "stock", "page_params": {"symbol": "000001"},
    }).json()
    assert body["error"] is None, body
    assert body["data"]["context_used"] is True
    sent = _StubHandler.calls[-1]["body"]["messages"]
    assert sent[0]["role"] == "system"
    assert "平安银行" in sent[0]["content"] and "11.91" in sent[0]["content"]
    assert sent[-1] == {"role": "user", "content": "这只票现在怎么样"}


def test_chat_without_page_is_unchanged(
    client: Any, stub_base: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """零降级面：不传 page（或未知页）→ 无 system 注入，与 R16 之前行为一致。"""
    from backend.app.routers import ai as ai_router

    monkeypatch.setattr(
        ai_router.config, "load_ai_config",
        lambda: SimpleNamespace(base_url=stub_base + "/v1", api_key="sk-stub", model="stub-model",
                                reasoning_effort="", context_kb=0, max_tokens=0),
    )
    body = client.post("/api/ai/chat", json={
        "messages": [{"role": "user", "content": "你好"}], "page": "settings",
    }).json()
    assert body["error"] is None
    assert body["data"]["context_used"] is False
    sent = _StubHandler.calls[-1]["body"]["messages"]
    assert all(m["role"] != "system" for m in sent)


def test_chat_summary_failure_never_blocks_reply(
    client: Any, stub_base: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """构建器内部抛错 → 静默无注入，对话照常（绝不影响主链路）。"""
    from backend.app.routers import ai as ai_router

    monkeypatch.setattr(
        ai_router.config, "load_ai_config",
        lambda: SimpleNamespace(base_url=stub_base + "/v1", api_key="sk-stub", model="stub-model",
                                reasoning_effort="", context_kb=0, max_tokens=0),
    )
    monkeypatch.setattr(page_context, "build_summary",
                        lambda page, params=None: (_ for _ in ()).throw(RuntimeError("构造炸了")))
    body = client.post("/api/ai/chat", json={"message": "hi", "page": "sectors"}).json()
    assert body["error"] is None and body["data"]["reply"] == "STUB-OK"
    assert body["data"]["context_used"] is False


# ============================================================
# 面 4：智能助手页持仓上下文（2026-11-18「诊断持仓」修复）
# ============================================================

def test_ai_page_summary_includes_holdings(monkeypatch: pytest.MonkeyPatch) -> None:
    """「诊断持仓」上下文：ai 页摘要必须含持仓台账的真实标的/份额/成本。"""
    import backend.app.datasource.portfolio as portfolio_module

    monkeypatch.setattr(portfolio_module, "load", lambda: {
        "items": [
            {"market": "a", "symbol": "600519", "name": "贵州茅台", "shares": 100, "cost": 1680.5},
            {"market": "a", "symbol": "300750", "name": "宁德时代", "shares": 200, "cost": 180.2},
        ],
        "updated_at": None,
    })
    text = page_context.build_summary("ai", {})
    assert text is not None
    assert "贵州茅台" in text and "600519" in text
    assert "宁德时代" in text and "300750" in text
    assert "100" in text and "1680.5" in text
    assert "不得编造" in text


def test_ai_page_summary_empty_portfolio_injects_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    """零持仓（用户真实状态，非异常）→ 注入明确说明，AI 告知未读取到持仓并引导添加，
    替代空数据下输出通用方法论的“空壳”观感（2026-11-19 BUG-20261119-02）。"""
    import backend.app.datasource.portfolio as portfolio_module
    from backend.app.datasource import page_context

    monkeypatch.setattr(portfolio_module, "load", lambda: {"items": [], "updated_at": None})
    text = page_context.build_summary("ai", {})
    assert text is not None
    assert "持仓台账为空" in text
    assert "未读取到持仓数据" in text


def test_ai_page_summary_load_failure_never_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """持仓读取抛错 → 返回 None，对话主链路不受影响。"""
    import backend.app.datasource.portfolio as portfolio_module
    from backend.app.datasource import page_context

    monkeypatch.setattr(portfolio_module, "load", lambda: (_ for _ in ()).throw(RuntimeError("读炸了")))
    assert page_context.build_summary("ai", {}) is None


def test_chat_ai_page_injects_portfolio_context(
    client: Any, stub_base: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """端到端：page=ai 时 /api/ai/chat 必须注入持仓 system 上下文并回传 context_used。"""
    from backend.app.routers import ai as ai_router
    import backend.app.datasource.portfolio as portfolio_module

    monkeypatch.setattr(
        ai_router.config, "load_ai_config",
        lambda: SimpleNamespace(base_url=stub_base + "/v1", api_key="sk-stub", model="stub-model",
                                reasoning_effort="", context_kb=0, max_tokens=0),
    )
    monkeypatch.setattr(portfolio_module, "load", lambda: {
        "items": [{"market": "a", "symbol": "600519", "name": "贵州茅台", "shares": 100, "cost": 1680.5}],
        "updated_at": None,
    })
    body = client.post("/api/ai/chat", json={
        "messages": [{"role": "user", "content": "诊断持仓"}], "page": "ai",
    }).json()
    assert body["error"] is None
    assert body["data"]["context_used"] is True
    sent = _StubHandler.calls[-1]["body"]["messages"]
    sys_msg = next(m for m in sent if m["role"] == "system")
    assert "贵州茅台" in sys_msg["content"] and "600519" in sys_msg["content"]
