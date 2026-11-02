# -*- coding: utf-8 -*-
"""G-4 港美股免费源（中文财报指标 + 个股新闻）与 AI 翻译（真实接口断言）。

背景：第二轮检测报告把「港美股个股新闻 / 中文财报」登记为 D3 缺口（无免费免鉴权源）。
2026-11-02 实测两条免费通道可用并接入：
    1. 雪球 ``/v5/stock/finance/{us|hk}/indicator.json``：港美股**中文**主要财务指标；
    2. 东财 ``search-api-web`` 资讯搜索：港美股个股新闻（按标的中文名/代码做相关性过滤）。
另新增 ``POST /api/ai/translate``：用用户配置的 OpenAI 兼容模型做英文→中文机翻；
未配置时返回 NOT_CONFIGURED（不假装翻译）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


def test_financial_indicators_us() -> None:
    """美股中文财务指标：AAPL 至少 1 期、每期 ≥20 项、含核心指标。"""
    body = client.get("/api/stock/financial-indicator",
                      params={"symbol": "AAPL", "market": "us", "limit": 2}).json()
    assert body["error_code"] is None
    data = body["data"]
    assert data["source"] == "雪球"
    assert data["name"], "缺少中文名"
    assert data["currency"] == "USD"
    periods = data["periods"]
    assert periods, "期数为空"
    labels = {m["label"] for m in periods[0]["metrics"]}
    assert len(labels) >= 20
    for core in ("基本每股收益", "每股净资产", "资产负债率", "流动比率"):
        assert core in labels, f"缺核心指标 {core}"
    assert periods[0]["date"]


def test_financial_indicators_hk() -> None:
    """港股中文财务指标：00700 至少 1 期、每期 ≥15 项。"""
    body = client.get("/api/stock/financial-indicator",
                      params={"symbol": "00700", "market": "hk", "limit": 2}).json()
    assert body["error_code"] is None
    data = body["data"]
    assert data["source"] == "雪球"
    assert data["name"], "缺少中文名"
    periods = data["periods"]
    assert periods
    labels = {m["label"] for m in periods[0]["metrics"]}
    assert len(labels) >= 15
    assert "基本每股收益" in labels
    # 每项必须有值结构（value + yoy 成对）
    for metric in periods[0]["metrics"][:5]:
        assert "value" in metric and "yoy" in metric


def test_stock_news_us() -> None:
    """美股个股新闻：AAPL（苹果）相关性过滤后仍有条目，字段齐全。"""
    body = client.get("/api/news/stock",
                      params={"symbol": "AAPL", "market": "us", "limit": 5}).json()
    assert body["error_code"] is None
    data = body["data"]
    assert data["source"] == "东方财富"
    assert data["market"] == "us"
    items = data["items"]
    assert items, "美股个股新闻为空（相关性过滤后无命中，属源站当日内容，非通道故障）"
    for item in items:
        assert item["title"] and item["time"]
        assert item["source"] == "东方财富"


def test_stock_news_hk() -> None:
    """港股个股新闻：00700（腾讯控股）相关性过滤后仍有条目。"""
    body = client.get("/api/news/stock",
                      params={"symbol": "00700", "market": "hk", "limit": 5}).json()
    assert body["error_code"] is None
    data = body["data"]
    assert data["market"] == "hk"
    assert data["items"], "港股个股新闻为空"
    assert all(item["title"] and item["time"] for item in data["items"])


def test_translate_not_configured() -> None:
    """未配置 AI 时翻译必须报 NOT_CONFIGURED，不得返回原文冒充译文。"""
    body = client.post("/api/ai/translate", json={"text": "hello world"}).json()
    assert body["error_code"] == "NOT_CONFIGURED"
    assert body["data"]["configured"] is False


def test_translate_rejects_empty_text() -> None:
    """空文本直接 INVALID_PARAM（不调模型）。"""
    body = client.post("/api/ai/translate", json={"text": "   "}).json()
    assert body["error_code"] == "INVALID_PARAM"


def test_financial_indicator_market_guard() -> None:
    """market 仅支持 a/hk/us；A 股路径不回归（source=新浪）。"""
    bad = client.get("/api/stock/financial-indicator",
                     params={"symbol": "AAPL", "market": "xx"}).json()
    assert bad["error_code"] == "INVALID_PARAM"
    ok = client.get("/api/stock/financial-indicator",
                    params={"symbol": "600519", "market": "a", "limit": 2}).json()
    assert ok["error_code"] is None
    assert ok["data"]["source"] == "新浪"
