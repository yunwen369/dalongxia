# -*- coding: utf-8 -*-
"""R14 财报/公告中文摘要 单测（N3 契约）。

覆盖：
1. 路由层：未配置 → NOT_CONFIGURED；market 非法 / 缺标识 / doc_url 越界 → 400。
2. 空/错分离：detail 返回空正文 → 成功信封 + status=no_content + summary=None（不造数）。
3. 幂等落库：相同 key 第二次命中 cached=True，不重调 LLM。
4. prompt 三段式结构：key_table/points/risks 字段清洗与围栏容忍。
5. 超长正文截断：source_text_chars 可大于 MAX_SOURCE_CHARS，truncated=True。
"""

from __future__ import annotations

import json
from typing import Any, Dict
from unittest.mock import patch

import pytest


@pytest.fixture
def summary_mod(monkeypatch, tmp_path):
    """隔离 SUMMARY_DIR 到 tmp，避免写真实 data/summaries/。"""
    from backend.app.datasource import ai_summary as mod

    summary_dir = tmp_path / "summaries"
    summary_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mod, "SUMMARY_DIR", summary_dir)
    return mod, summary_dir


def _stub_detail(content: str = "", source: str = "eastmoney"):
    """构造 detail_cached 的返回（content 可空，模拟正文不可得）。"""
    return {
        "content": content,
        "source": source,
        "caliber": "测试正文",
        "content_chars": len(content),
    }


def _stub_ai_config(monkeypatch, base_url: str = "https://api.test/v1", model: str = "test-model", api_key: str = "k"):
    from backend.app import config

    _AI = type("_AI", (), {
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "reasoning_effort": "",
        "context_kb": 0,
        "max_tokens": 0,
    })
    monkeypatch.setattr(config, "load_ai_config", lambda: _AI())


def _fake_llm_reply(reply_text: str):
    """返回一个假的 _llm_summarize，绕过真实网络。"""
    from backend.app.datasource import ai_summary as mod

    def _fake(content: str) -> Dict[str, Any]:
        parsed = mod._parse_reply(reply_text)  # noqa: SLF001
        summary = mod._normalize(parsed)
        if summary is None:
            from backend.app.core.errors import NonRetryableError
            raise NonRetryableError("原文无有效内容")
        return {"summary": summary, "model": "test-model"}

    return _fake


# ---------- 路由层契约 ----------

class TestSummaryRouteContract:
    """/api/ai/summary 信封与参数校验。"""

    def test_not_configured_returns_not_configured(self, summary_mod):
        """未配置 AI → NOT_CONFIGURED，不静默降级。"""
        from fastapi.testclient import TestClient

        from backend.app.main import create_app

        client = TestClient(create_app())
        body = client.post(
            "/api/ai/summary",
            json={"market": "a", "code": "000001", "title": "平安银行公告"},
        ).json()
        assert body["error_code"] == "NOT_CONFIGURED"
        assert body["data"]["configured"] is False

    def test_invalid_market_rejected(self, summary_mod):
        """market 非 a/hk/us → INVALID_PARAM（信封式错误）。"""
        from fastapi.testclient import TestClient

        from backend.app.main import create_app

        client = TestClient(create_app())
        body = client.post(
            "/api/ai/summary",
            json={"market": "jp", "code": "000001", "title": "x"},
        ).json()
        assert body["error_code"] == "INVALID_PARAM"

    def test_missing_identifier_rejected(self, summary_mod):
        """code 与 title 都空 → INVALID_PARAM。"""
        from fastapi.testclient import TestClient

        from backend.app.main import create_app

        client = TestClient(create_app())
        body = client.post(
            "/api/ai/summary",
            json={"market": "a"},
        ).json()
        assert body["error_code"] == "INVALID_PARAM"

    def test_doc_url_whitelist(self, summary_mod, monkeypatch):
        """doc_url 越界 → INVALID_PARAM（防 SSRF）。"""
        from fastapi.testclient import TestClient

        from backend.app.main import create_app

        client = TestClient(create_app())
        body = client.post(
            "/api/ai/summary",
            json={"market": "a", "code": "000001", "title": "x",
                  "doc_url": "https://evil.com/steal"},
        ).json()
        assert body["error_code"] == "INVALID_PARAM"
        # 合法前缀可通过参数校验（后续因未配置返回 NOT_CONFIGURED，不是 INVALID_PARAM）
        resp_ok = client.post(
            "/api/ai/summary",
            json={"market": "a", "code": "000001", "title": "x",
                  "doc_url": "https://np-anotice-stock.eastmoney.com/api/xx"},
        ).json()
        assert resp_ok.get("error_code") != "INVALID_PARAM"


# ---------- 空/错分离 ----------

class TestNoContentSeparation:
    """正文不可得 → 成功信封 + status=no_content，不造数、不调 LLM。"""

    def test_empty_content_returns_no_content(self, summary_mod, monkeypatch):
        mod, _ = summary_mod
        _stub_ai_config(monkeypatch)
        with patch.object(mod.financial_report, "detail_cached", return_value=_stub_detail("")):
            result = mod.summarize(market="a", code="000001", title="空公告")
        assert result["status"] == "no_content"
        assert result["summary"] is None
        assert "正文未能获取" in result["caliber"]
        assert result["source_text_chars"] == 0


# ---------- 幂等落库 ----------

class TestIdempotentCache:
    """相同 key 第二次命中 cached=True，不重调 LLM。"""

    def test_cache_hit_skips_llm(self, summary_mod, monkeypatch):
        mod, summary_dir = summary_mod
        _stub_ai_config(monkeypatch)
        reply = json.dumps({
            "key_table": [{"metric": "营收", "value": "100亿", "yoy": "+10%"}],
            "points": ["业绩增长"],
            "risks": [],
        }, ensure_ascii=False)
        call_count = {"n": 0}

        def _fake(content: str) -> Dict[str, Any]:
            call_count["n"] += 1
            return {"summary": mod._normalize(mod._parse_reply(reply)), "model": "test-model"}  # noqa: SLF001

        with patch.object(mod.financial_report, "detail_cached", return_value=_stub_detail("正文内容")), \
             patch.object(mod, "_llm_summarize", side_effect=_fake):
            r1 = mod.summarize(market="a", code="000001", title="营收公告", pub_time="2026-01-01")
            r2 = mod.summarize(market="a", code="000001", title="营收公告", pub_time="2026-01-01")

        assert r1["cached"] is False
        assert r2["cached"] is True
        assert call_count["n"] == 1
        assert r2["summary"]["key_table"][0]["metric"] == "营收"
        # 落盘文件存在
        assert list(summary_dir.glob("*.json"))

    def test_force_overrides_cache(self, summary_mod, monkeypatch):
        mod, _ = summary_mod
        _stub_ai_config(monkeypatch)
        reply = json.dumps({"key_table": [{"metric": "A", "value": "1"}], "points": ["p"], "risks": []})
        with patch.object(mod.financial_report, "detail_cached", return_value=_stub_detail("x")), \
             patch.object(mod, "_llm_summarize", side_effect=_fake_llm_reply(reply)):
            mod.summarize(market="a", code="000001", title="t", pub_time="2026-01-01")
            r = mod.summarize(market="a", code="000001", title="t", pub_time="2026-01-01", force=True)
        assert r["cached"] is False


# ---------- prompt 三段式结构 ----------

class TestPromptStructure:
    """_parse_reply + _normalize：围栏容忍、字段清洗、空内容判失败。"""

    def test_parse_tolerates_fenced_json(self, summary_mod):
        mod, _ = summary_mod
        text = "```json\n" + json.dumps({"key_table": [{"metric": "m", "value": "v"}], "points": ["p"], "risks": []}) + "\n```"
        parsed = mod._parse_reply(text)  # noqa: SLF001
        assert parsed["key_table"][0]["metric"] == "m"

    def test_normalize_strips_invalid_rows(self, summary_mod):
        mod, _ = summary_mod
        parsed = {
            "key_table": [
                {"metric": "营收", "value": "100亿", "yoy": "+5%"},
                {"metric": "", "value": "x"},  # metric 空 → 丢弃
                "not-a-dict",  # 非 dict → 丢弃
            ],
            "points": ["p1", "", "  "],
            "risks": None,
        }
        norm = mod._normalize(parsed)  # noqa: SLF001
        assert norm is not None
        assert len(norm["key_table"]) == 1
        assert len(norm["points"]) == 1
        assert norm["risks"] == []

    def test_error_marker_returns_none(self, summary_mod):
        mod, _ = summary_mod
        assert mod._normalize({"error": "原文无有效内容"}) is None  # noqa: SLF001

    def test_both_empty_returns_none(self, summary_mod):
        mod, _ = summary_mod
        assert mod._normalize({"key_table": [], "points": [], "risks": []}) is None  # noqa: SLF001


# ---------- 超长截断 ----------

class TestTruncation:
    """正文超长时 source_text_chars>MAX_SOURCE_CHARS，truncated=True，但不截断落库正文长度。"""

    def test_long_content_truncated_flag(self, summary_mod, monkeypatch):
        mod, _ = summary_mod
        _stub_ai_config(monkeypatch)
        long_content = "正" * (mod.MAX_SOURCE_CHARS + 5000)
        reply = json.dumps({"key_table": [{"metric": "字数", "value": "很多"}], "points": ["长"], "risks": []})
        with patch.object(mod.financial_report, "detail_cached", return_value=_stub_detail(long_content)), \
             patch.object(mod, "_llm_summarize", side_effect=_fake_llm_reply(reply)):
            r = mod.summarize(market="a", code="000001", title="长文", pub_time="2026-01-01")
        assert r["truncated"] is True
        assert r["source_text_chars"] == len(long_content)
