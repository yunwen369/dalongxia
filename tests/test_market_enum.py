# -*- coding: utf-8 -*-
"""第五轮阶段 4 回归：market 枚举口径统一（对外 a/hk/us，cn 为兼容别名）。

背景（B 报告 §四）：板块接口默认 ``cn``、白名单 ``cn/hk/us``；行情/指数用
``a/hk/us``；前端靠 ``normSectorMarket`` 做 a↔cn 兜底 —— 跨页传参易错。
本阶段统一为：
- **对外唯一口径 a/hk/us**（板块 provider 内部由 ``sector_source_market()`` 映射 a→cn）；
- ``cn`` 保留为 **a 的兼容别名**，归一化后响应一律回写 ``a``；
- 白名单集中在 ``utils.MARKET_WHITELIST*``，各路由不再各自维护元组。

覆盖：
1. 板块 8 个端点：``a`` 与 ``cn`` 等价（均非 INVALID_PARAM 且响应 market=a）；
2. 板块 hk/us：允许调用，响应 market 为 hk/us（业务上可能空态，但参数合法）；
3. 非法值 → INVALID_PARAM；
4. 行情指数 / 新闻池 / 财报搜索：all 与 a 合法，cn 归一为 a，非法值报错。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)

# 板块 8 个端点（board/boards 均取真实板块名，避免"板块不存在"干扰）
SECTOR_ENDPOINTS = [
    ("/api/sector/industry", {}),
    ("/api/sector/concept", {}),
    ("/api/sector/heatmap", {"kind": "industry"}),
    ("/api/sector/summary", {}),
    ("/api/sector/kline", {"board": "半导体", "period": "daily"}),
    ("/api/sector/cons", {"board": "半导体"}),
    ("/api/sector/fundflow", {}),
    ("/api/sector/trend", {"boards": "半导体,银行", "days": 10}),
]


@pytest.mark.parametrize("path,params", SECTOR_ENDPOINTS)
@pytest.mark.parametrize("market", ["a", "cn"])
def test_sector_a_and_cn_are_equivalent(path: str, params: dict, market: str) -> None:
    """板块端点：a 与 cn 均可调用，且响应 market 统一回写 a。

    Args:
        path: 端点路径。
        params: 其余查询参数。
        market: a 或兼容别名 cn。

    Returns:
        None.
    """
    body = client.get(path, params={**params, "market": market}).json()
    assert body.get("error_code") != "INVALID_PARAM", f"{path} market={market} 被判非法：{body}"
    data = body.get("data") or {}
    assert data.get("market") == "a", f"{path} market={market} 响应口径={data.get('market')}"


@pytest.mark.parametrize("market", ["hk", "us"])
def test_sector_hk_us_accepted(market: str) -> None:
    """板块端点：hk/us 参数合法（业务上可能空态，但不得报 INVALID_PARAM）。

    Args:
        market: hk 或 us。

    Returns:
        None.
    """
    body = client.get("/api/sector/industry", params={"market": market}).json()
    assert body.get("error_code") != "INVALID_PARAM", body
    data = body.get("data") or {}
    assert data.get("market") == market


@pytest.mark.parametrize("path,params", SECTOR_ENDPOINTS[:4])
def test_sector_invalid_market_rejected(path: str, params: dict) -> None:
    """板块端点：非法 market → INVALID_PARAM。

    Args:
        path: 端点路径。
        params: 其余查询参数。

    Returns:
        None.
    """
    body = client.get(path, params={**params, "market": "xx"}).json()
    assert body.get("error_code") == "INVALID_PARAM", f"{path} 未拒绝非法 market：{body}"
    assert "market" in (body.get("error") or "")


@pytest.mark.parametrize("market,expect_ok", [("all", True), ("a", True), ("cn", True), ("xx", False)])
def test_market_index_enum(market: str, expect_ok: bool) -> None:
    """指数接口：all/a/cn 合法（cn 归一 a），非法值报错。

    Args:
        market: 取值。
        expect_ok: 是否应合法。

    Returns:
        None.
    """
    body = client.get("/api/market/index", params={"market": market}).json()
    if expect_ok:
        assert body.get("error_code") != "INVALID_PARAM", body
        data = body.get("data") or {}
        expected = "all" if market == "all" else "a"
        assert data.get("market", expected) == expected
    else:
        assert body.get("error_code") == "INVALID_PARAM"


@pytest.mark.parametrize("market,expect_ok", [("all", True), ("a", True), ("cn", True), ("xx", False)])
def test_news_pool_market_enum(market: str, expect_ok: bool) -> None:
    """新闻池（ai-feed）：all/a/cn 合法，非法值报错。

    Args:
        market: 取值。
        expect_ok: 是否应合法。

    Returns:
        None.
    """
    body = client.get("/api/news/ai-feed", params={"market": market, "limit": 3}).json()
    if expect_ok:
        assert body.get("error_code") != "INVALID_PARAM", body
    else:
        assert body.get("error_code") == "INVALID_PARAM"


@pytest.mark.parametrize("market,expect_ok", [("all", True), ("a", True), ("cn", True), ("xx", False)])
def test_financial_search_market_enum(market: str, expect_ok: bool) -> None:
    """财报搜索：all/a/cn 合法（cn 归一 a），非法值报错。

    Args:
        market: 取值。
        expect_ok: 是否应合法。

    Returns:
        None.
    """
    body = client.get("/api/news/financial-search",
                      params={"keyword": "茅台", "market": market}).json()
    if expect_ok:
        assert body.get("error_code") != "INVALID_PARAM", body
    else:
        assert body.get("error_code") == "INVALID_PARAM"


def test_market_whitelist_is_single_source() -> None:
    """白名单唯一来源：各模块引用同一常量，且不含 cn（cn 仅作别名）。"""
    from backend.app.datasource import financial_report, utils, watchlist
    from backend.app.routers import market as market_router
    from backend.app.routers import news as news_router
    from backend.app.routers import sector as sector_router
    from backend.app.routers import stock as stock_router

    assert utils.MARKET_WHITELIST == ("a", "hk", "us")
    assert utils.MARKET_WHITELIST_ALL == ("all", "a", "hk", "us")
    assert utils.MARKET_WHITELIST_WATCHLIST == ("a", "hk", "us", "fund")
    assert utils.MARKET_ALIASES == {"cn": "a"}
    assert "cn" not in utils.MARKET_WHITELIST

    assert market_router._ALLOWED_MARKETS is utils.MARKET_WHITELIST  # noqa: SLF001
    assert stock_router._ALLOWED_MARKETS is utils.MARKET_WHITELIST  # noqa: SLF001
    assert sector_router._ALLOWED_MARKETS is utils.MARKET_WHITELIST  # noqa: SLF001
    assert news_router._MARKETS is utils.MARKET_WHITELIST_ALL  # noqa: SLF001
    assert financial_report.MARKET_VALUES is utils.MARKET_WHITELIST_ALL
    assert watchlist._ALLOWED_MARKETS is utils.MARKET_WHITELIST_WATCHLIST  # noqa: SLF001


def test_normalize_market_behaviour() -> None:
    """归一化函数：大小写/空值/别名/allow_all 行为正确。"""
    from backend.app.datasource import utils

    assert utils.normalize_market(None) == "a"
    assert utils.normalize_market("") == "a"
    assert utils.normalize_market("A") == "a"
    assert utils.normalize_market("CN") == "a"
    assert utils.normalize_market("cn") == "a"
    assert utils.normalize_market("hk") == "hk"
    assert utils.normalize_market("all") is None
    assert utils.normalize_market("all", allow_all=True) == "all"
    assert utils.normalize_market("xx") is None
    assert utils.normalize_market("xx", allow_all=True) is None
    assert utils.sector_source_market("a") == "cn"
    assert utils.sector_source_market("hk") == "hk"
