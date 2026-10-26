# -*- coding: utf-8 -*-
"""资讯池过滤顺序与缓存键（TZ-01 取证时发现的两处口径缺陷）。

背景：`/api/news/ai-feed` 的板块 / 区域 / 市场三个过滤都在**截断之后**执行。
旧实现只向 provider 要 ``max(limit, 60)`` 条，若目标板块的命中项落在第 61 条之后，
接口就会返回「暂无相关资讯」——用户看到的是空态，而不是「池子里其实有」。

这两处都用**合成数据**做确定性断言（不依赖当天新闻内容），
真实链路的契约仍由 `tests/test_qa_verify.py` 覆盖。
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.core import cache
from backend.app.datasource import news as news_provider
from backend.app.routers import news as news_router

client = TestClient(app)


def _synthetic_items(total: int, hit_index: int, board: str = "半导体设备") -> List[Dict[str, Any]]:
    """构造合成新闻池：只有第 ``hit_index`` 条命中目标板块。

    Args:
        total: 池内条数。
        hit_index: 命中项的索引（从 0 开始）。
        board: 命中板块名。

    Returns:
        List[Dict[str, Any]]: 合成条目列表。
    """
    items: List[Dict[str, Any]] = []
    for i in range(total):
        items.append({
            "title": f"合成资讯 {i}",
            "text": "",
            "time": f"2026-10-26 10:{i % 60:02d}:00",
            "channel": "东财全球",
            "source": "东财",
            "boards": [board] if i == hit_index else [],
        })
    return items


def test_board_filter_sees_beyond_old_pool_limit(monkeypatch) -> None:
    """板块过滤必须能看到第 60 条之后的命中项（过滤在截断前生效）。

    Args:
        monkeypatch: pytest 夹具。

    Notes:
        命中项故意放在索引 150：旧实现（池子 60 条）必然返回 0 条，
        新实现（过滤时池子 ≥240 条）能正常返回 1 条。断言同时锁定
        「向 provider 要的池子大小必须显著大于 limit」这一实现要点。
    """
    seen_limits: List[int] = []

    def fake_build_ai_feed(*, boards=None, limit=60, symbol=""):
        """替身：记录被请求的池子大小，返回合成池。"""
        seen_limits.append(int(limit))
        return {"items": _synthetic_items(240, 150), "channels": [],
                "total_before_dedup": 240, "total_after_dedup": 240}

    monkeypatch.setattr(news_provider, "build_ai_feed", fake_build_ai_feed)
    cache.clear_cache()

    resp = client.get("/api/news/ai-feed", params={"boards": "半导体设备", "limit": 10}).json()
    assert resp["error_code"] is None, resp.get("error")
    items = resp["data"]["items"]
    assert len(items) == 1, f"第 150 条命中项应被过滤出来，实际 {len(items)} 条"
    assert items[0]["boards"] == ["半导体设备"]
    assert seen_limits and seen_limits[0] >= 240, f"过滤时的池子应显著大于 limit，实际 {seen_limits}"
    cache.clear_cache()


def test_market_is_part_of_cache_key(monkeypatch) -> None:
    """``market`` 必须进缓存键，否则 a/hk/us 会互相命中同一份缓存。

    Args:
        monkeypatch: pytest 夹具。

    Notes:
        旧缓存键为 ``news:aifeed:{region}:{boards}:{limit}:{code}``，缺 ``market``：
        先查 ``market=a`` 再查 ``market=hk``，第二次会直接拿到 A 股的过滤结果。
        这里断言 provider 被调用两次（各自独立取数）。
    """
    calls: List[Dict[str, Any]] = []

    def fake_build_ai_feed(*, boards=None, limit=60, symbol=""):
        """替身：记录每次调用参数。"""
        calls.append({"boards": boards, "limit": limit, "symbol": symbol})
        return {"items": _synthetic_items(3, 0), "channels": [],
                "total_before_dedup": 3, "total_after_dedup": 3}

    monkeypatch.setattr(news_provider, "build_ai_feed", fake_build_ai_feed)
    cache.clear_cache()

    first = client.get("/api/news/ai-feed", params={"market": "a", "limit": 5}).json()
    second = client.get("/api/news/ai-feed", params={"market": "hk", "limit": 5}).json()
    assert first["error_code"] is None and second["error_code"] is None
    assert len(calls) == 2, f"market 未进缓存键，provider 只被调用 {len(calls)} 次"
    assert first["data"]["market"] == "a"
    assert second["data"]["market"] == "hk"
    cache.clear_cache()


def test_board_filter_returns_empty_when_truly_no_match(monkeypatch) -> None:
    """池内确实无命中时返回空态（不是错误），且文案说明是「无命中」而非接口故障。

    Args:
        monkeypatch: pytest 夹具。
    """

    def fake_build_ai_feed(*, boards=None, limit=60, symbol=""):
        """替身：返回一个完全没有目标板块标签的池。"""
        return {"items": _synthetic_items(240, -1), "channels": [],
                "total_before_dedup": 240, "total_after_dedup": 240}

    monkeypatch.setattr(news_provider, "build_ai_feed", fake_build_ai_feed)
    cache.clear_cache()

    resp = client.get("/api/news/ai-feed", params={"boards": "半导体设备", "limit": 10}).json()
    assert resp["error_code"] is None, "自然空态不得报错误码"
    assert resp["data"]["items"] == []
    assert resp["data"]["count"] == 0
    cache.clear_cache()


@pytest.mark.parametrize("boards", ["不存在的板块", "半导体设备,不存在的板块"])
def test_unknown_board_still_rejected(boards: str) -> None:
    """未知板块仍走参数校验（回归保护：改过滤顺序不得放松校验）。

    Args:
        boards: 待校验的板块参数。
    """
    body = client.get("/api/news/ai-feed", params={"boards": boards}).json()
    assert body["error_code"] == "INVALID_PARAM", f"实际 {body['error_code']}"
    assert "板块" in (body["error"] or "")
