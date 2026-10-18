"""数据三性校验（真实网络拉取，不使用 mock/写死行情）。

对应规范 v7.0 §6.6 / §6.7 / §11.11 与附录 D：
1. 每个 market 接口真实调用，断言规模非零、关键字段存在、数值量级合理；
2. A股至少 3 个标的（600519/688012/000001），覆盖报价、K线、分时、概况；
3. 报价与 K 线最后收盘、指数快照与指数 K 线交叉核对；
4. 主动禁用主源，验证降级链真实生效且 source 为实际命中源；
5. 验证 {data,error,error_code} 契约与休市/盘后状态标注。

运行：.venv\\Scripts\\python.exe -m pytest tests/test_qa_verify.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.app import config
from backend.app.main import app
from backend.app.core import cache
from backend.app.datasource import utils

client = TestClient(app)
A_SYMBOLS = ("600519", "688012", "000001")
ALLOWED_SOURCES = {"腾讯", "新浪", "东财", "东财F10", "东财F10/腾讯", "巨潮", "巨潮/腾讯"}


@pytest.fixture(scope="session", autouse=True)
def _clear_cache_once() -> None:
    """整套真实拉取测试开始前清空缓存，避免旧数据掩盖源站变化。"""
    cache.clear_cache()


def _body(path: str, params: dict) -> dict:
    """请求接口并断言 HTTP 200 与基线契约。"""
    response = client.get(path, params=params)
    assert response.status_code == 200, f"{path} HTTP {response.status_code}"
    body = response.json()
    assert "data" in body and "error" in body
    return body


def _success(path: str, params: dict) -> dict:
    """请求成功接口，返回 data 并断言 error 为空。"""
    body = _body(path, params)
    assert body["error"] is None, f"{path} error={body['error']}"
    assert body.get("error_code") is None
    return body["data"]


def test_index_snapshot_real() -> None:
    """核心指数快照：9 个市场指数、量级合理、双徽章字段齐全。"""
    data = _success("/api/market/index", {"market": "all"})
    items = data["items"]
    assert len(items) == 9
    assert data["source"] in ALLOWED_SOURCES
    assert data["caliber"]
    assert data["as_of"]

    by_code = {item["code"]: item for item in items}
    assert by_code["000001"]["price"] > 2000
    assert by_code["399001"]["price"] > 5000
    assert by_code["HSI"]["price"] > 10000  # 拦截恒生取到 4029 这类张冠李戴
    assert by_code["DJI"]["price"] > 20000
    assert by_code["INX"]["price"] > 3000
    for item in items:
        assert item["name"]
        assert item["price"] > 0
        assert item["market_status"] in {"盘中", "盘前", "盘后", "收盘快照", "多市场快照", "休市", "快照"}


def test_index_kline_real() -> None:
    """指数 K 线：320 条、OHLC 关系正确、与快照点位一致。"""
    data = _success("/api/market/index-kline", {"code": "000001", "period": "日k"})
    items = data["items"]
    assert len(items) >= 60
    assert data["source"] in ALLOWED_SOURCES
    assert data["market_status"] == "日线收盘"
    assert data["is_realtime"] is False
    last = items[-1]
    for key in ("date", "open", "close", "high", "low", "volume"):
        assert last[key] is not None
    assert last["high"] >= last["low"]
    assert 2000 < last["close"] < 6000
    # 与指数快照交叉核对
    snapshot = _success("/api/market/index", {"market": "a"})["items"][0]
    assert abs(snapshot["price"] - last["close"]) / last["close"] < 0.005


@pytest.mark.parametrize("symbol", A_SYMBOLS)
def test_quote_and_kline_real(symbol: str) -> None:
    """3 个 A股标的：报价与 K 线均真实、字段齐全、最后收盘一致。"""
    quote_data = _success("/api/market/quote", {"symbol": symbol, "market": "a"})
    quote = quote_data["quote"]
    assert quote["name"]
    assert quote["price"] > 0
    assert quote["prev_close"] > 0
    assert quote["high"] >= quote["low"]
    assert -30 < quote["pct"] < 30
    assert len(quote["bids"]) >= 1 and len(quote["asks"]) >= 1
    assert quote["market_status"] in {"盘中", "盘前", "盘后", "收盘快照", "时间未知"}
    if quote["market_status"] == "收盘快照":
        assert quote["is_realtime"] is False

    kline_data = _success(
        "/api/market/kline",
        {"symbol": symbol, "market": "a", "period": "日k", "adjust": "qfq"},
    )
    items = kline_data["items"]
    assert len(items) >= 60
    assert kline_data["source"] in {"东财", "新浪", "腾讯"}
    assert kline_data["market_status"] == "日线收盘"
    assert kline_data["is_realtime"] is False
    dates = [item["date"] for item in items]
    assert dates == sorted(dates)
    last = items[-1]
    assert last["high"] >= last["low"]
    assert last["close"] > 0
    # 腾讯报价 vs 新浪/东财前复权最后收盘，允许极小误差
    assert abs(quote["price"] - last["close"]) / last["close"] < 0.005


def test_minute_real() -> None:
    """分时：至少 30 个点、时间格式正确、价格量级合理。"""
    data = _success("/api/market/minute", {"symbol": "600519", "market": "a", "period": "1min"})
    items = data["items"]
    assert len(items) >= 30
    assert data["source"] in {"东财", "新浪", "腾讯"}
    assert data["as_of"]
    for item in items[:5]:
        assert len(item["time"]) == 5 and item["time"][2] == ":"
        assert item["price"] > 0
    assert data["market_status"] in {"盘中", "收盘快照", "时间未知"}


def test_search_real() -> None:
    """搜索：中微命中中微公司/中微半导，字段齐全。"""
    data = _success("/api/market/search", {"q": "中微", "limit": 5})
    items = data["items"]
    assert items
    assert any("中微" in item["name"] for item in items)
    assert items[0]["symbol"]
    assert items[0]["market"] in {"a", "hk", "us"}


def test_profile_real() -> None:
    """个股概况：名称/行业/上市日期/市值均存在。"""
    data = _success("/api/market/profile", {"symbol": "600519", "market": "a"})
    profile = data["profile"]
    assert profile["name"] == "贵州茅台"
    assert profile["industry"]
    assert profile["list_date"]
    assert profile["total_market_cap"] and profile["total_market_cap"] > 1e10
    assert data["source"] in ALLOWED_SOURCES


def test_downgrade_quote_to_sina(monkeypatch: pytest.MonkeyPatch) -> None:
    """主源腾讯被禁用时，报价必须真实回退新浪并返回 source=新浪。"""
    cache.clear_cache()
    monkeypatch.setitem(config.PROVIDER_SWITCHES, "tencent", False)
    data = _success("/api/market/quote", {"symbol": "600519", "market": "a"})
    assert data["source"] == "新浪"
    assert data["quote"]["price"] > 0
    cache.clear_cache()


def test_downgrade_kline_to_sina(monkeypatch: pytest.MonkeyPatch) -> None:
    """主源东财被禁用时，K线必须真实回退新浪并返回 source=新浪。"""
    cache.clear_cache()
    monkeypatch.setitem(config.PROVIDER_SWITCHES, "eastmoney", False)
    data = _success(
        "/api/market/kline",
        {"symbol": "600519", "market": "a", "period": "日k", "adjust": "qfq"},
    )
    assert data["source"] == "新浪"
    assert len(data["items"]) >= 60
    cache.clear_cache()


def test_error_contract() -> None:
    """参数错误必须返回中文 error 与 INVALID_PARAM，不裸抛 500。"""
    body = _body("/api/market/quote", {"symbol": "abc", "market": "a"})
    assert body["error"]
    assert body["error_code"] == "INVALID_PARAM"

    missing = client.get("/api/market/search")
    assert missing.status_code == 200
    payload = missing.json()
    assert payload["error"]
    assert payload["error_code"] == "INVALID_PARAM"


def test_market_status_boundaries() -> None:
    """时段判定：盘中/盘前/盘后/休市/收盘快照，不允许收盘伪装实时。"""
    day = datetime(2026, 10, 19, 10, 0, 0)
    status, realtime = utils.classify_market_status(day, "a", now=day)
    assert status == "盘中" and realtime is True

    before = datetime(2026, 10, 19, 8, 0, 0)
    status, realtime = utils.classify_market_status(before, "a", now=before)
    assert status == "盘前" and realtime is False

    after = datetime(2026, 10, 19, 16, 0, 0)
    status, realtime = utils.classify_market_status(after, "a", now=after)
    assert status == "盘后" and realtime is False

    previous = datetime(2026, 10, 16, 15, 0, 0)
    status, realtime = utils.classify_market_status(previous, "a", now=day)
    assert status == "收盘快照" and realtime is False

    unknown = datetime(2026, 10, 17, 10, 0, 0)
    status, realtime = utils.classify_market_status(unknown, "a", now=unknown + timedelta(days=1))
    assert status == "收盘快照" and realtime is False
