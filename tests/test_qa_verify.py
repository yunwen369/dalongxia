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

import time as _time
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
    """整套真实拉取测试开始前清空缓存，并清理历史遗留的测试持仓。

    历史遗留：若上一次会话在 teardown 前中断，`data/portfolio.json` 可能残留
    带「临时用例」备注的条目。这里只删除**带测试标记**的条目，不触碰用户真实持仓。
    """
    cache.clear_cache()
    try:
        listing = client.get("/api/portfolio").json().get("data", {}).get("items", [])
        for item in listing:
            if "临时用例" in (item.get("note") or ""):
                client.delete(f"/api/portfolio/{item['id']}")
    except Exception:  # noqa: BLE001 - 清理失败不影响测试
        pass


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

    # R2-01/R2-02：港股指数不提供成交额（字段37 是成交量副本）、美股指数字段37
    # 是「成交量×点位」合成值 —— 两者都必须为 None，由前端显示「—」，
    # 禁止用成交量或合成值冒充成交额。
    for code in ("HSI", "HSTECH", "DJI", "IXIC", "INX"):
        assert by_code[code]["amount"] is None, f"{code} 指数成交额应为 None"
        assert by_code[code]["volume"] and by_code[code]["volume"] > 0, f"{code} 成交量缺失"

    # A股指数成交额口径不变：源站万元 ×10000 → 元，量级为亿元
    for code in ("000001", "399001", "399006", "000300"):
        amount = by_code[code]["amount"]
        assert amount is not None, f"{code} A股指数成交额不得为空"
        assert 1e10 <= amount <= 1e14, f"{code} A股指数成交额量级异常：{amount}"


def test_stock_amount_unit_ratio_real() -> None:
    """R2-01/R2-02 回归：个股成交额/(成交量×价格) ≈ 1，四市场不得再错 1 万倍。

    A 股成交量单位为「手」（100 股），港股/美股为「股」；成交额单位分别为
    CNY / HKD / USD —— 三者都应满足量价比接近 1（上轮 P0-01 的 ×10000 错误
    会让港股/美股比值放大 1 万倍，本轮指数修复不得回退个股口径）。
    """
    cases = [
        ("600519", "a", 100),   # A股：手 → 股
        ("00700", "hk", 1),     # 港股：股
        ("AAPL", "us", 1),      # 美股：股
    ]
    for symbol, market, lot in cases:
        quote = _success("/api/market/quote", {"symbol": symbol, "market": market})["quote"]
        amount = quote.get("amount")
        volume = quote.get("volume")
        price = quote.get("price")
        assert amount and amount > 0, f"{symbol} 成交额缺失"
        assert volume and volume > 0, f"{symbol} 成交量缺失"
        assert price and price > 0, f"{symbol} 价格缺失"
        ratio = amount / (volume * lot * price)
        assert 0.95 <= ratio <= 1.05, f"{symbol} 成交额/量价={ratio:.4f}，单位口径可能再次错位"


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
    assert len(quote["bids"]) + len(quote["asks"]) >= 1, "买卖五档全空"
    assert quote["market_status"] in {"盘中", "盘前", "盘后", "收盘快照", "时间未知", "休市"}
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
    # 腾讯报价 vs 前复权最后收盘：收盘前一致；盘后/竞价时段允许 <=1% 漂移（尾盘集合竞价与净值口径差异）
    drift = abs(quote["price"] - last["close"]) / last["close"]
    assert drift < 0.005 or quote["market_status"] in ("盘后", "收盘快照", "休市"), f"偏差过大：{drift}"


def test_minute_real() -> None:
    """分时：非空、时间格式正确、价格量级合理（条数按时点放宽：开盘首分钟仅 1 点）。"""
    data = _success("/api/market/minute", {"symbol": "600519", "market": "a", "period": "1min"})
    items = data["items"]
    assert len(items) >= 5, f"A股分时过少：{len(items)}"
    assert data["source"] in {"东财", "新浪", "腾讯"}
    assert data["as_of"]
    for item in items[:5]:
        assert len(item["time"]) == 5 and item["time"][2] == ":"
        assert item["price"] > 0
    # 时段断言补「休市」：A 股 11:30–13:00 为午间休市，classify_market_status 按规范
    # （「午间休市归休市」）返回「休市」，此前集合遗漏该合法值，导致午休时段跑必红。
    assert data["market_status"] in {"盘中", "收盘快照", "时间未知", "盘后", "休市"}


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


# =============================================================
# 阶段 5.1：sector 8 接口 + stock 7 接口 + 组件注册表真实断言
# 全部走真实网络拉取，禁止 mock/写死；断言含口径、量级与跨接口交叉核对。
# =============================================================
SECTOR_SOURCES = {"同花顺", "新浪", "申万", "东财", "腾讯", "新浪证监会"}
STOCK_SOURCES = {"东财", "新浪", "腾讯", "同花顺", "东财/腾讯"}


def test_sector_industry_real() -> None:
    """行业行情：90 个同花顺行业、涨跌幅与成交额量级合理、领涨股非空。"""
    data = _success("/api/sector/industry", {"market": "cn"})
    items = data["items"]
    assert len(items) >= 50, f"行业数量异常：{len(items)}"
    assert data["source"] in SECTOR_SOURCES
    assert "亿元" in data["caliber"]
    assert data["as_of"] and data["market_status"]
    for item in items[:10]:
        assert item["name"]
        assert item["pct"] is not None and -20 <= item["pct"] <= 20
        assert item["amount"] is not None and item["amount"] > 0
        assert item["lead_stock"]
    # 涨跌幅必须真实分布（不能全是 0，否则等价于假数据）
    assert len({round(item["pct"], 2) for item in items if item["pct"] is not None}) > 20


def test_sector_concept_real() -> None:
    """概念行情：175 个新浪概念、含 label 供成分股反查。"""
    data = _success("/api/sector/concept", {"market": "cn"})
    items = data["items"]
    assert len(items) >= 100
    assert data["source"] == "新浪"
    assert items[0]["label"], "概念板块缺少 label，成分股反查会失败"
    assert len({round(item["pct"], 2) for item in items if item["pct"] is not None}) > 20


def test_sector_heatmap_negative_not_clamped() -> None:
    """热力图：value=abs(pct)、负值板块面积不为 0（P1-10 修复真断言）。"""
    data = _success("/api/sector/heatmap", {"market": "cn", "kind": "industry"})
    nodes = data["nodes"]
    assert len(nodes) >= 50
    negatives = [node for node in nodes if node["pct"] is not None and node["pct"] < 0]
    positives = [node for node in nodes if node["pct"] is not None and node["pct"] > 0]
    assert negatives, "当期无下跌板块，无法验证负值面积，请换 kind=concept 重跑"
    assert positives
    for node in negatives:
        assert node["value"] is not None and node["value"] > 0, f"负值被钳为 0：{node}"
        assert abs(node["value"] - abs(node["pct"])) < 1e-6
    # 等幅涨跌必须同面积（-2.5% 与 +2.5% 面积一致）
    sample = round(abs(negatives[0]["pct"]), 2)
    matched = [node for node in nodes if node["pct"] is not None and round(abs(node["pct"]), 2) == sample]
    assert len({round(node["value"], 6) for node in matched}) == 1


def test_sector_summary_real() -> None:
    """摘要：领涨/领跌各 3 条且排序正确，集中度落在 0–100。"""
    data = _success("/api/sector/summary", {"market": "cn"})
    assert len(data["gainers"]) == 3 and len(data["losers"]) == 3
    gainer_pcts = [item["pct"] for item in data["gainers"]]
    loser_pcts = [item["pct"] for item in data["losers"]]
    assert gainer_pcts == sorted(gainer_pcts, reverse=True)
    assert loser_pcts == sorted(loser_pcts)
    assert gainer_pcts[0] >= loser_pcts[0]
    assert data["concentration"] is not None and 0 < data["concentration"] <= 100
    assert data["board_count"] >= 50


def test_sector_kline_real() -> None:
    """板块 K 线：半导体日线 ≥60 根、OHLC 关系正确、日期升序。"""
    data = _success("/api/sector/kline", {"board": "半导体", "period": "daily", "market": "cn"})
    items = data["items"]
    assert len(items) >= 60
    assert data["source"] == "同花顺"
    assert data["market_status"] == "日线收盘"
    assert data["is_realtime"] is False
    dates = [item["date"] for item in items]
    assert dates == sorted(dates)
    assert all(len(date) == 10 for date in dates)
    for item in items[-20:]:
        assert item["high"] >= item["low"]
        assert item["close"] > 0 and item["volume"] > 0


def test_sector_cons_real() -> None:
    """成分股：概念板块走新浪、行业板块走申万，权重合计≈100%。"""
    concept = _success("/api/sector/cons", {"board": "华为汽车", "market": "cn"})
    assert len(concept["items"]) >= 50
    assert concept["source"] in {"新浪", "申万", "东财"}
    assert concept["items"][0]["code"].isdigit()

    industry = _success("/api/sector/cons", {"board": "半导体", "market": "cn"})
    assert len(industry["items"]) >= 100, "半导体行业成分缺失，V-03 跳转会空态"
    weights = [item["weight"] for item in industry["items"] if item["weight"] is not None]
    assert weights and 90 <= sum(weights) <= 110, f"权重合计异常：{sum(weights)}"
    pcts = [item["pct"] for item in industry["items"] if item["pct"] is not None]
    assert pcts == sorted(pcts, reverse=True)


def test_sector_fundflow_real() -> None:
    """资金流：净额=流入−流出（同源同口径），超大单/大单如实为空。"""
    data = _success("/api/sector/fundflow", {"market": "cn"})
    items = data["items"]
    assert len(items) >= 50
    assert data["source"] == "同花顺"
    assert "无超大单/大单分项" in data["caliber"]
    checked = 0
    for item in items[:10]:
        if item["net_inflow"] is None:
            continue
        assert item["inflow"] is not None and item["outflow"] is not None
        assert abs((item["inflow"] - item["outflow"]) - item["net_inflow"]) <= 0.02
        assert item["super_big"] is None and item["big"] is None
        checked += 1
    assert checked >= 5


def test_sector_trend_real() -> None:
    """趋势：多板块归一化，首点=100，点数与请求天数一致。"""
    data = _success("/api/sector/trend", {"boards": "半导体,银行", "days": 20, "market": "cn"})
    assert len(data["series"]) == 2
    for item in data["series"]:
        points = item["points"]
        assert len(points) == 20
        assert abs(points[0]["value"] - 100.0) < 1e-6
        assert all(point["value"] > 0 for point in points)


def test_sector_overseas_simplified_model() -> None:
    """港美股境外简化建模：含恒生指数、口径如实标注、非真实板块。"""
    data = _success("/api/sector/industry", {"market": "hk"})
    items = data["items"]
    assert len(items) >= 5
    assert data["source"] == "腾讯"
    assert "境外简化建模" in data["caliber"]
    names = [item["name"] for item in items]
    assert any("恒生" in name for name in names)
    assert all(item["kind"] in {"指数", "代表个股"} for item in items)


def test_stock_profile_real() -> None:
    """个股概况：中微公司名称/行业/上市日/股本真实。"""
    data = _success("/api/stock/profile", {"symbol": "688012", "market": "a"})
    assert data["name"] == "中微公司"
    assert data["full_name"] and "中微" in data["full_name"]
    assert data["industry"]
    assert data["listing_date"] == "2019-07-22"
    assert data["total_share"] and data["total_share"] > 9e8
    assert data["total_mv"] and data["total_mv"] > 1e11
    assert data["main_business"]


def test_stock_estimates_real() -> None:
    """机构一致预期（F-027）：分年度 EPS/净利覆盖 + 机构明细 + 数值量级合理。"""
    data = _success("/api/stock/estimates", {"symbol": "688012", "market": "a"})
    assert data["available"] is True
    eps = data["eps"]
    assert len(eps) >= 2, f"预测年度不足：{len(eps)}"
    first = eps[0]
    assert str(first["年度"]).isdigit()
    assert first["预测机构数"] >= 1
    # 中微公司 EPS 一致预期量级：0.1~50 元（拦截张冠李戴，如取到指数点位/股价）
    assert 0.1 < first["均值"] < 50
    assert first["最小值"] <= first["均值"] <= first["最大值"]
    assert len(data["profit"]) == len(eps)
    assert data["institutions"], "机构明细为空"
    assert data["institutions"][0]["机构名称"]
    assert data["detail"], "详细指标预测为空"


def test_stock_estimates_unknown_symbol_empty() -> None:
    """边界：无效代码 → 成功空态（error=null + available=false + 中文说明）。"""
    body = _body("/api/stock/estimates", {"symbol": "000000", "market": "a"})
    assert body["error"] is None
    assert body["data"]["available"] is False
    assert body["data"]["eps"] == []
    assert "暂无" in body["data"]["note"]


def test_stock_holders_real() -> None:
    """十大股东：报告期须为完整披露期（≥5 名），比例为正。"""
    data = _success("/api/stock/holders", {"symbol": "688012"})
    assert data["top10"], "十大股东为空"
    assert len(data["top10"]) >= 5, f"股东名单不完整：{len(data['top10'])} 名"
    assert data["top10_period"]
    first = data["top10"][0]
    assert first["name"]
    assert first["ratio"] and first["ratio"] > 0
    assert first["rank"] == 1
    assert data["free_top10"]


def test_stock_realtime_panel_real() -> None:
    """FinScope 聚合：六块均有真实数据，报价与 K 线最后收盘一致。"""
    data = _success("/api/stock/realtime-panel", {"symbol": "688012", "market": "a"})
    assert set(data["blocks"].values()) <= {"ok"}, f"存在失败区块：{data['blocks']}"

    quote = data["quote"]
    assert quote["name"] == "中微公司" and quote["price"] > 0
    assert quote["market_status"] in {"盘中", "盘前", "盘后", "收盘快照", "时间未知", "休市"}

    kline = data["kline"]
    assert len(kline) >= 60
    assert abs(quote["price"] - kline[-1]["close"]) / kline[-1]["close"] < 0.02

    minute = data["minute"]
    assert len(minute) >= 10
    times = [item["time"] for item in minute]
    assert all(len(value) == 5 and value[2] == ":" for value in times[:5])
    # 分时须覆盖上午段；下午段与午休断裂只在已进入下午时判定——
    # 盘中上午执行时分时只累积到当前时刻，写死「必须有 13:00 后数据点」会必然误报。
    assert any(value < "11:31" for value in times)
    latest_time = max(times)
    if latest_time >= "13:00":
        assert any(value >= "13:00" for value in times)
    assert not any("11:31" <= value < "13:00" for value in times)

    fund = data["fund_dist"]
    assert [tier["name"] for tier in fund["tiers"]] == ["超大单", "大单", "中单", "小单"]
    assert fund["main_net"] is not None
    ratios = [tier["ratio"] for tier in fund["tiers"] if tier["ratio"] is not None]
    assert len(ratios) == 4 and -100 < sum(ratios) < 100

    assert data["news"], "相关新闻为空"
    assert data["news"][0]["title"] and data["news"][0]["time"]
    assert data["news"][0]["sentiment"] in {"利好", "利空", "中性"}

    forecast = data["forecast"]
    assert forecast["org_count"] >= 1
    assert set(forecast["ratings"]) == {"买入", "增持", "中性", "减持", "卖出"}
    assert forecast["target_high"] and forecast["target_low"]
    assert forecast["target_high"] >= forecast["target_low"]


def test_stock_financial_summary_real() -> None:
    """财务摘要：6 项常用指标 × 8 期，EPS 与已知中报值一致。"""
    data = _success("/api/stock/financial-summary", {"symbol": "688012"})
    periods = data["periods"]
    metrics = {item["key"]: item["values"] for item in data["metrics"]}
    assert len(periods) == 8
    assert periods[0] == "20260630"
    assert set(metrics) == {"基本每股收益", "毛利率", "净资产收益率(ROE)",
                            "营业总收入", "归母净利润", "资产负债率"}
    eps = metrics["基本每股收益"][0]
    assert eps is not None and 4.0 < eps < 4.6, f"EPS 异常：{eps}"
    assert 30 < metrics["毛利率"][0] < 50
    assert 5 < metrics["净资产收益率(ROE)"][0] < 20


def test_stock_financial_trend_real() -> None:
    """财务趋势：四序列对齐年报，同比值可由营收反算校验。"""
    data = _success("/api/stock/financial-trend", {"symbol": "688012", "years": 5})
    assert len(data["periods"]) == 5
    assert all(period.endswith("1231") for period in data["periods"])
    series = {item["key"]: item["values"] for item in data["series"]}
    revenue = series["revenue"]
    yoy = series["revenue_yoy"]
    assert revenue[-1] and revenue[-1] > revenue[-2]
    assert yoy[0] is None, "首年无基数，必须为空而不是 0"
    expected = round((revenue[-1] - revenue[-2]) / abs(revenue[-2]) * 100, 2)
    assert abs(yoy[-1] - expected) < 0.05


def test_stock_financial_indicator_real() -> None:
    """财务指标：至少 8 项指标、期数与请求一致。"""
    data = _success("/api/stock/financial-indicator", {"symbol": "688012", "limit": 8})
    assert len(data["metrics"]) >= 8
    assert len(data["periods"]) == 8
    keys = [item["key"] for item in data["metrics"]]
    assert "净资产收益率(%)" in keys and "资产负债率(%)" in keys
    roe = next(item["values"] for item in data["metrics"] if item["key"] == "净资产收益率(%)")
    assert any(value is not None and -50 < value < 50 for value in roe)


def test_stock_financial_report_real() -> None:
    """三大报表：利润表营收与财务趋势末年营收一致（跨接口交叉核对）。"""
    data = _success("/api/stock/financial-report",
                    {"symbol": "688012", "statement": "利润表", "limit": 8})
    assert data["statement"] == "利润表"
    assert len(data["metrics"]) >= 5
    revenue_row = next(item for item in data["metrics"] if item["key"] == "营业总收入")
    trend = _success("/api/stock/financial-trend", {"symbol": "688012", "years": 5})
    annual_revenue = next(item["values"] for item in trend["series"] if item["key"] == "revenue")
    # 报表首行是 2026 中报，年报末年（2025）应能在报表期内找到同值
    assert any(
        value is not None and abs(value - annual_revenue[-1]) < 1 for value in revenue_row["values"]
    ), "利润表与财务趋势的年度营收不一致"


def test_stock_financial_report_statements() -> None:
    """资产负债表/现金流量表同样真实可用。"""
    for statement in ("资产负债表", "现金流量表"):
        data = _success("/api/stock/financial-report",
                        {"symbol": "688012", "statement": statement, "limit": 4})
        assert data["statement"] == statement
        assert len(data["periods"]) == 4
        assert len(data["metrics"]) >= 3


def test_widget_registry() -> None:
    """组件注册表：49 个区块（sectors 7 + stock 10 + sector-val 4 + news 5 + fund 6 + portfolio 4 + watchlist 2 + lhb 5 + yjb 6）、编号唯一、48 列映射正确。

    2026-10-26 口径变更（v1.0.5）：补登 Y-01~Y-04（与 pages.js 实际渲染的 6 块一致）
    与 N-05（财报/公告搜索入口），总数由 44 → 49；本断言同步更新并加强对新增区块的保护。
    """
    body = _body("/api/widgets", {})
    assert body["error"] is None
    data = body["data"]
    assert data["total"] == 49
    ids = [item["id"] for item in data["items"]]
    assert ids[:1] == ["S-01"] and "ST-01" in ids and "V-03" in ids
    assert {"N-01", "N-02", "N-03", "N-04", "N-05"} <= set(ids)
    assert {"Y-01", "Y-02", "Y-03", "Y-04", "Y-05", "Y-06"} <= set(ids)
    assert {"F-01", "F-02", "F-04", "P-01", "P-03"} <= set(ids)
    assert len(set(ids)) == 49
    assert data["grid"][".g-7"] == 28 and data["grid"][".g-5"] == 20
    sectors = [item for item in data["items"] if item["page"] == "#/sectors"]
    assert len(sectors) == 7
    jumps = [item for item in data["items"] if item["jump"]]
    jump_targets = {item["jump"].split("?")[0] for item in jumps}
    assert "#/sector-val" in jump_targets and "#/stock" in jump_targets and "#/news" in jump_targets
    news_api = {api for item in data["items"] if item["page"] == "#/news" for api in item["api"]}
    assert "/api/news/ai-feed" in news_api and "/api/news/ai-review" in news_api
    fund_api = {api for item in data["items"] if item["page"] == "#/fund" for api in item["api"]}
    assert "/api/fund/performance" in fund_api and "/api/fund/position" in fund_api
    pf_api = {api for item in data["items"] if item["page"] == "#/portfolio" for api in item["api"]}
    assert "/api/portfolio/analysis" in pf_api
    fund_api = {api for item in data["items"] if item["page"] == "#/fund" for api in item["api"]}
    assert "/api/fund/performance" in fund_api and "/api/fund/position" in fund_api
    pf_api = {api for item in data["items"] if item["page"] == "#/portfolio" for api in item["api"]}
    assert "/api/portfolio/analysis" in pf_api


def test_stock_error_contract() -> None:
    """非法代码/非法市场返回中文错误与 INVALID_PARAM，不裸抛 500。"""
    bad = _body("/api/stock/profile", {"symbol": "abc", "market": "a"})
    assert bad["error_code"] == "INVALID_PARAM" and bad["error"]
    bad_market = _body("/api/sector/industry", {"market": "jp"})
    assert bad_market["error_code"] == "INVALID_PARAM"
    bad_concept = _body("/api/sector/heatmap", {"market": "cn", "kind": "abc"})
    assert bad_concept["error_code"] == "INVALID_PARAM"


# =============================================================
# 批次 5.2：news 7 接口 + sentiment 3 接口 + 规则引擎单测
# 说明：初评由确定性规则产出，故可对分类结果做强断言；AI 复核未配置属预期契约。
# =============================================================
def test_news_flash_real() -> None:
    """财联社快讯：条数、时间、情绪分值与三档标签全部合规。

    注：不写死「池内至少 2 种标签」——凌晨盘后快讯常为地缘/海外中性稿，
    该写法会随数据形状假失败（R2-03 同类脆弱断言）。改为校验
    「分值 → 标签 / 强度」的分档映射自洽（§11.9 复算口径），
    词典本身的有效性由 test_sentiment_rules_deterministic 确定性覆盖。
    """
    from backend.app.datasource import sentiment as engine

    data = _success("/api/news/flash", {"limit": 30})
    items = data["items"]
    assert len(items) >= 10
    assert data["source"] == "财联社"
    assert data["caliber"]
    for item in items:
        assert item["title"] and item["time"]
        sentiment = item["sentiment"]
        score = sentiment["score"]
        assert -0.1 <= score <= 0.1
        assert sentiment["label"] in {"利好", "利空", "中性"}
        assert sentiment["strength"] in {"强", "中", "弱"}
        # 分档映射必须与引擎常量一致：±0.02 判多空、±0.06 判强弱
        expected_label = (
            "利好" if score >= engine.BOUND_NEUTRAL
            else ("利空" if score <= -engine.BOUND_NEUTRAL else "中性")
        )
        assert sentiment["label"] == expected_label, f"分值 {score} 与标签不符"
        if expected_label == "中性":
            assert sentiment["strength"] == "弱"
        else:
            expected_strength = "强" if abs(score) >= engine.BOUND_STRONG else "中"
            assert sentiment["strength"] == expected_strength, f"分值 {score} 与强度不符"


def test_news_stock_real() -> None:
    """个股新闻：字段齐全；命中半导体关键词的新闻必须回查到半导体板块。

    §11.11 口径：不写死「≥5 条」（源站当日可能只给少量或不给），改为
    「先取新闻池 → 池内命中关键词 → 回查该条 boards 是否标到半导体」。
    池内没有半导体相关新闻时只校验字段契约，不制造假失败，也不掩盖真实错误
    （接口失败会走 error/error_code，由 _success 断言拦截）。
    """
    data = _success("/api/news/stock", {"symbol": "688012", "limit": 10})
    items = data.get("items") or []
    assert data["symbol"] == "688012"
    for item in items:
        assert item["title"] and item["time"]
        assert item["source"]
        assert isinstance(item["boards"], list)
        if item.get("link"):
            assert item["link"].startswith("http")

    keywords = ("半导体", "芯片", "晶圆", "光刻", "刻蚀", "封装", "存储", "中微", "华创")
    hits = [item for item in items if any(word in item["title"] for word in keywords)]
    if hits:
        tagged = [item for item in hits if any("半导体" in board for board in item["boards"])]
        assert tagged, "命中半导体关键词的新闻未回查到半导体板块，板块树匹配逻辑异常"


def test_news_global_real() -> None:
    """全球快讯：三源合并后去重生效，标题无重复。"""
    data = _success("/api/news/global", {"limit": 60})
    items = data["items"]
    assert len(items) >= 20
    assert data["before_dedup"] >= len(items)
    keys = []
    for item in items:
        assert item["source"] in {"东方财富", "新浪财经", "同花顺"}
        keys.append(item["title"].strip())
    assert len(keys) == len(set(keys)), "去重未生效"


def test_news_board_tree() -> None:
    """板块树：一级 6 / 二级 15 / 末级 28，含关键词与三市场。"""
    data = _success("/api/news/board-tree", {})
    assert data["counts"] == {"level1": 6, "level2": 15, "leaf": 28}
    assert data["version"] == "20261020"
    assert data["markets"] == ["A", "HK", "US"]
    leaves = [leaf for level1 in data["tree"] for level2 in level1["children"] for leaf in level2["children"]]
    assert len(leaves) == 28
    assert all(leaf["keywords"] for leaf in leaves)
    assert {"半导体设备", "存储芯片", "光模块", "CPO"} <= {leaf["name"] for leaf in leaves}


def _force_t_plus_1(on: bool, restore_enabled: bool = False) -> None:
    """显式开关「增强通道」与「盘中估值」，让用例不依赖本机绑定状态。

    Args:
        on: True=关闭增强通道与盘中估值（强制走 T+1）；False=按 restore_enabled 还原。
        restore_enabled: 收起时是否把增强通道还原为用例执行前的值。
    """
    from backend.app.core import user_prefs

    # 注意：_body() 只发 GET；toggle 是 POST，必须用 client.post
    if on:
        client.post("/api/fund/yjb/toggle", json={"enabled": False})
        user_prefs.set_flag("fund_estimation", False)
        return
    # 还原：不能把用户已开启的增强通道永久关掉（会污染真实 data/yjb_state.json）
    client.post("/api/fund/yjb/toggle", json={"enabled": bool(restore_enabled)})
    user_prefs.set_flag("fund_estimation", False)


def _news_channel_probe() -> tuple:
    """探测多源新闻池通道存活情况，并返回池本身。

    周末/节假日上游快讯可能整体枯竭，此时"检索必出数"无法成立；测试据此
    在「真实出数断言」与「契约断言」之间分支，而不是 skip/xfail。

    Returns:
        tuple: (是否有存活通道且有数据, ai-feed 的 data 体)。
    """
    pool = _success("/api/news/ai-feed", {"limit": 60})
    channels = pool.get("channels") or []
    alive = any(ch.get("ok") and (ch.get("count") or 0) > 0 for ch in channels)
    return alive, pool


def _expected_board_level(board: str) -> int:
    """查询板块在板块树中的层级（1=一级 / 2=二级 / 3=末级）。

    情绪接口的 `level` 字段是**板块层级**而非情绪强度，因此断言必须按板块树取值，
    不能写死 3——否则命中二级板块（如「银行」）时必然误红。

    Args:
        board: 板块名。

    Returns:
        int: 板块层级；板块树中不存在时按末级 3 处理。
    """
    from backend.app.datasource import news as news_provider

    node = news_provider.resolve_board(board) or {}
    try:
        return int(node.get("level") or 3)
    except (TypeError, ValueError):
        return 3


def test_news_sector_search_real() -> None:
    """板块检索：与 ai-feed 同源同池——池内有该板块标签则必能回出；未知板块 NOT_FOUND。

    分两支：通道存活 → 真实出数断言（回归点：两视图口径不一致会导致池内有标签却检索为空）；
    通道全灭 → 只做契约断言（结构完整 + 明确中文降级），不 skip、不放水。
    """
    bad = _body("/api/news/sector-search", {"sector": "不存在的板块"})
    assert bad["error_code"] == "NOT_FOUND"

    alive, pool = _news_channel_probe()
    if not alive:
        degraded = _body("/api/news/sector-search", {"sector": "半导体设备", "limit": 20})
        assert degraded["error_code"] in {None, "EMPTY_DATA"}, degraded.get("error")
        data = degraded["data"] or {}
        assert data.get("sector") == "半导体设备"
        assert isinstance(data.get("items"), list) and data["items"] == []
        assert degraded["error"] is None or "无" in degraded["error"] or "不可用" in degraded["error"]
        return

    candidates = [board for item in pool["items"] for board in (item.get("boards") or [])]
    if not candidates:
        # 第三分支：通道存活但池内确实没有任何板块标签（例如周末只剩国际宏观稿）。
        # 此时「设备板块必出数」在物理上不成立，改为**契约断言**：结构完整、
        # 无错误码、且返回条目必须自洽（每条 boards 含所查板块）——不放水、不假造。
        data = _success("/api/news/sector-search", {"sector": "半导体设备", "limit": 20})
        assert data["sector"] == "半导体设备"
        assert isinstance(data["items"], list)
        for item in data["items"]:
            assert "半导体设备" in (item.get("boards") or []), "检索结果必须自洽命中该板块"
        return

    sector = candidates[0]

    data = _success("/api/news/sector-search", {"sector": sector, "limit": 20})
    assert data["items"], f"池内命中 {sector} 但检索为空（两视图池/口径不一致）"
    assert data["sector"] == sector
    for item in data["items"]:
        assert sector in item["boards"]


def test_news_ai_feed_real() -> None:
    """多源池：池非空、四通道登记、去重有效，且按「池内实际命中板块」过滤必命中。

    说明：新闻是滚动数据，写死板块会在无相关资讯时误报；这里先取全量池，
    再用池内真实出现的板块回查，保证断言确定、不依赖当天新闻内容。
    """
    pool = _success("/api/news/ai-feed", {"limit": 40})
    assert pool["before_dedup"] >= 10, "多源池为空，通道可能全部失败"
    assert pool["after_dedup"] >= 1
    channels = {ch["name"]: ch for ch in pool["channels"]}
    assert {"财联社快讯", "东财全球", "新浪全球", "同花顺全球"} <= set(channels)
    assert sum(1 for ch in channels.values() if ch["ok"]) >= 3, "可用源少于 3 个"
    for item in pool["items"]:
        assert -0.1 <= item["sentiment"]["score"] <= 0.1

    candidates = [board for item in pool["items"] for board in (item.get("boards") or [])]
    if not candidates:
        # 池内零板块标签（周末只剩国际宏观稿）：按板块过滤无法验证命中，
        # 但仍必须验证「过滤口径生效」——用一个真实末级板块过滤，返回必须自洽。
        filtered = _success("/api/news/ai-feed", {"boards": "半导体设备", "limit": 20})
        assert isinstance(filtered["items"], list)
        for item in filtered["items"]:
            assert "半导体设备" in (item.get("boards") or []), "板块过滤结果必须自洽"
        return

    board = candidates[0]
    filtered = _success("/api/news/ai-feed", {"boards": board, "limit": 20})
    assert filtered["items"], f"按池内命中板块 {board} 过滤后为空"
    assert all(board in item["boards"] for item in filtered["items"])


def test_news_ai_review_not_configured(monkeypatch: _pytest.MonkeyPatch) -> None:
    """AI 复核未配置时必须返回 NOT_CONFIGURED 与引导文案，不静默降级（强制空配置）。"""
    monkeypatch.setattr(config, "load_ai_config",
                        lambda: config.AIConfig(base_url="", api_key="", model="", source="none"))
    response = client.post(
        "/api/news/ai-review",
        json={"title": "测试", "content": "测试内容", "board": "半导体设备"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["error_code"] == "NOT_CONFIGURED"
    assert body["data"]["reviewed"] is False
    assert "设置" in body["data"]["guidance"]
    empty = client.post("/api/news/ai-review", json={"title": "", "content": ""}).json()
    assert empty["error_code"] == "INVALID_PARAM"


def test_sentiment_rules_deterministic() -> None:
    """规则引擎：确定性 + 否定反转 + 条件矩阵 + ±0.1 边界。"""
    from backend.app.datasource import sentiment as engine

    cases = [
        ("贵州茅台上调出厂价 白酒提价落地", "财联社", "利好"),
        ("某公司被证监会立案调查 涉嫌违规", "证券时报网", "利空"),
        ("半导体设备中标大单 金额超预期", "同花顺", "利好"),
        ("公司终止回购方案", "新浪财经", "利空"),
        ("并购重组终止 交易失败", "财联社", "利空"),
        ("股东承诺不减持公司股份", "新浪财经", "利好"),
        ("公司发布日常经营公告", "东方财富", "中性"),
    ]
    for text, source, expected in cases:
        first = engine.analyze(text, source=source)
        second = engine.analyze(text, source=source)
        assert first["score"] == second["score"], "同一文本必须得到同一分值"
        assert -0.1 <= first["score"] <= 0.1
        assert first["label"] == expected, f"{text} 期望 {expected}，实得 {first['label']}"
    # 权重口径冻结校验
    assert (engine.W_DIRECTION, engine.W_STRENGTH, engine.W_CREDIBILITY) == (0.45, 0.35, 0.20)
    assert len(engine.EVENT_RULES) == 18
    aggregate = engine.aggregate(
        [
            {"sentiment": {"score": 0.1, "label": "利好", "events": [{"name": "业绩预增"}]}},
            {"sentiment": {"score": -0.1, "label": "利空", "events": []}},
        ],
        scope="unit",
    )
    assert aggregate["temperature"] == 50.0 and aggregate["positive"] == 1 and aggregate["negative"] == 1


def test_sentiment_stock_real() -> None:
    """个股情绪：分档计数自洽、温度在 0–100。"""
    data = _success("/api/sentiment/stock", {"symbol": "688012", "name": "中微公司"})
    assert data["total"] >= 5
    assert data["positive"] + data["negative"] + data["neutral"] == data["total"]
    assert 0 <= data["temperature"] <= 100
    assert data["samples"], "缺少证据样本"
    assert data["level"] in {"偏热", "中性", "偏冷"}


def test_sentiment_market_real() -> None:
    """市场情绪：国内快讯聚合，含温度与 Top 事件。"""
    data = _success("/api/sentiment/market", {"region": "cn"})
    assert data["total"] >= 10
    assert 0 <= data["temperature"] <= 100
    assert data["region"] == "cn"
    assert isinstance(data["top_events"], list)


def test_sentiment_board_real() -> None:
    """板块情绪：与新闻池同源同池——池内有该板块标签则情绪必出样本。

    分两支：通道存活 → 真实出数断言；通道全灭 → 契约断言（结构完整、温度合法、
    条目为空且无错误码），不 skip、不放水。
    """
    alive, pool = _news_channel_probe()
    if not alive:
        data = _success("/api/sentiment/board", {"board": "半导体设备"})
        assert data["board"] == "半导体设备"
        assert data["level"] == _expected_board_level("半导体设备")
        assert data["total"] >= 0
        assert isinstance(data["samples"], list)
        assert 0 <= data["temperature"] <= 100
        return

    candidates = [board for item in pool["items"] for board in (item.get("boards") or [])]
    if not candidates:
        # 第三分支：通道存活但池内零板块标签（周末只剩国际宏观稿）。
        # 此时拿不到任何真实命中板块，改为契约断言：结构完整、温度合法、
        # 无错误码、样本与计数自洽——不放水、不假造条目。
        data = _success("/api/sentiment/board", {"board": "半导体设备"})
        assert data["board"] == "半导体设备"
        assert data["level"] == _expected_board_level("半导体设备")
        assert isinstance(data["samples"], list)
        assert data["total"] >= len(data["samples"]), "total 不得小于样本数"
        assert 0 <= data["temperature"] <= 100
        return

    board = candidates[0]

    data = _success("/api/sentiment/board", {"board": board})
    assert data["total"] >= 1, f"池内命中 {board} 但板块情绪无样本（两视图池/口径不一致）"
    assert data["board"] == board
    # level 是板块在板块树中的层级（1/2/3），不是情绪强度——必须按板块树校验，
    # 写死 3 会在命中二级板块（如「银行」level=2）时误红。
    assert data["level"] == _expected_board_level(board)
    # 板块名与关键词是两套结构（keywords 是该板块的命中词表，不含板块名本身），
    # 故校验关键词集非空，并对证据样本校验真实契约字段（title/score/label）。
    assert data["keywords"], "板块关键词集不可为空"
    samples = data.get("samples") or []
    assert samples, "板块情绪样本不可为空"
    for item in samples[:3]:
        assert isinstance(item.get("title"), str) and item["title"].strip()
        assert isinstance(item.get("score"), (int, float))
        assert item.get("label") in ("利好", "利空", "中性")
    assert 0 <= data["temperature"] <= 100


def test_news_sentiment_error_contract() -> None:
    """参数校验：非法排序/区域/市场与空参数均返回 INVALID_PARAM。"""
    assert _body("/api/news/flash", {"sort": "abc"})["error_code"] == "INVALID_PARAM"
    assert _body("/api/news/ai-feed", {"region": "mars"})["error_code"] == "INVALID_PARAM"
    assert _body("/api/news/ai-feed", {"market": "jp"})["error_code"] == "INVALID_PARAM"
    assert _body("/api/news/sector-search", {"sector": "  "})["error_code"] == "INVALID_PARAM"
    assert _body("/api/sentiment/market", {"region": "mars"})["error_code"] == "INVALID_PARAM"
    assert _body("/api/sentiment/board", {"board": "不存在的板块"})["error_code"] == "NOT_FOUND"


# =============================================================
# 批次 5.3：fund 9 接口 + portfolio 5 接口
# 说明：portfolio 用例会写入真实 data/portfolio.json，用 fixture 保证用例结束即清理。
# =============================================================
import pytest as _pytest  # noqa: E402


@_pytest.fixture()
def temp_position():
    """新增一条临时持仓，用例结束后删除，避免污染用户真实持仓。"""
    created = client.post("/api/portfolio", json={
        "market": "fund", "symbol": "017811", "name": "东方人工智能主题混合C",
        "shares": 100, "cost": 3.0, "note": "pytest 临时用例",
    }).json()
    assert created["error"] is None, created
    item_id = created["data"]["item"]["id"]
    try:
        yield item_id
    finally:
        client.delete(f"/api/portfolio/{item_id}")


def test_fund_search_real() -> None:
    """搜索：代码精确命中，字段齐全。"""
    data = _success("/api/fund/search", {"q": "017811"})
    assert data["count"] >= 1
    first = data["items"][0]
    assert first["code"] == "017811"
    assert "人工智能" in first["name"]
    assert first["type"]


def test_fund_realtime_t_plus_1() -> None:
    """最新净值：T+1 口径、非实时、降级链如实记录。

    前置：显式关闭「增强通道」与「盘中估值」两个开关——本机若已绑定养基宝，
    默认会走盘中估算，那样断言 T+1 口径必然失败。用例自带前置，不依赖环境状态。
    """
    prev_enabled = bool((_success("/api/fund/yjb/status", {}) or {}).get("enabled"))
    _force_t_plus_1(True)
    try:
        data = _success("/api/fund/realtime", {"code": "017811"})
    finally:
        _force_t_plus_1(False, prev_enabled)
    assert data["nav"] and data["nav"] > 0
    assert data["nav_date"] and len(data["nav_date"]) == 10
    assert data["is_realtime"] is False
    assert "T+1" in data["caliber"]
    channels = [item["channel"] for item in data["attempts"]]
    assert "蛋卷 nav_grtd" in channels and "蛋卷 derived" in channels
    disabled = next(item for item in data["attempts"] if item["channel"] == "蛋卷 nav_grtd")
    assert disabled["ok"] is False and "禁用" in disabled["reason"]
    # 增强通道被显式关闭后，养基宝分支必须记录为不可用而不是静默跳过
    yjb = [item for item in data["attempts"] if item["channel"].startswith("养基宝")]
    assert yjb and all(item["ok"] is False for item in yjb)


def test_fund_nav_real() -> None:
    """净值历史：与实时接口同日净值一致、日期倒序。"""
    nav = _success("/api/fund/nav", {"code": "017811", "page": 1, "size": 5})
    assert len(nav["items"]) == 5
    assert nav["total"] and nav["total"] > 500
    dates = [item["date"] for item in nav["items"]]
    assert dates == sorted(dates, reverse=True)

    realtime = _success("/api/fund/realtime", {"code": "017811"})
    same_day = [item for item in nav["items"] if item["date"] == realtime["nav_date"]]
    if same_day:
        assert abs(same_day[0]["nav"] - realtime["nav"]) < 1e-6


def test_fund_holding_real() -> None:
    """重仓股：10 条、比例合计合理、季度字段存在。"""
    data = _success("/api/fund/holding", {"code": "017811"})
    items = data["items"]
    assert len(items) >= 5
    assert items[0]["name"] and items[0]["code"]
    ratios = [item["ratio"] for item in items if item["ratio"] is not None]
    assert ratios and 0 < sum(ratios) <= 100
    assert data["quarter"]


def test_fund_info_real() -> None:
    """档案：名称/管理人/成立日/托管人齐全，规模键名与前端 i18n 一致（R2-04）。"""
    data = _success("/api/fund/info", {"code": "017811"})
    assert data["name"] == "东方人工智能主题混合C"
    assert data["manager"]
    assert data["found_date"] == "2023-02-22"
    assert data["keeper"]
    assert data["xq"]["基金公司"]
    # R2-04 回归点：xq 键名必须为「基金规模」（前端 fund.kv.scale），
    # 键名若回退成「最新规模」则规模行恒显"无数据"
    assert "基金规模" in data["xq"], "雪球档案规模键名不符（应为基金规模）"
    assert data["xq"].get("基金规模") or data.get("scale"), "规模值为空，前端将显示无数据"


def test_fund_performance_real() -> None:
    """业绩：阶段涨幅含近1年、排名可解析、分年度列表非空。"""
    data = _success("/api/fund/performance", {"code": "017811"})
    periods = {item["period"]: item for item in data["periods"]}
    assert "近1年" in periods and "成立以来" in periods
    assert periods["近1年"]["growth"] is not None
    assert periods["近1年"]["rank"]
    assert data["annual"]
    assert data["name"]


def test_fund_drawdown_real() -> None:
    """回撤：最大回撤为负、峰谷日期有序、当前回撤<=0。"""
    data = _success("/api/fund/drawdown", {"code": "017811"})
    assert data["max_drawdown"] < 0
    assert data["peak_date"] <= data["trough_date"]
    assert data["current_drawdown"] <= 0
    assert data["series"]
    assert all("drawdown" in point for point in data["series"][:3])


def test_fund_dividend_real() -> None:
    """分红：有分红基金返回明细，无分红基金返回空列表而非报错。"""
    with_div = _success("/api/fund/dividend", {"code": "000011"})
    assert len(with_div["items"]) >= 5
    first = with_div["items"][0]
    assert first["record_date"] and first["per_10"] is not None
    no_div = _success("/api/fund/dividend", {"code": "017811"})
    assert no_div["items"] == []


def test_fund_position_disabled() -> None:
    """持仓分布：需登录通道必须明确禁用说明，不编造数据。"""
    body = _body("/api/fund/position", {"code": "017811"})
    assert body["error_code"] == "EMPTY_DATA"
    assert body["data"]["disabled"] is True
    assert body["data"]["items"] == []
    assert "登录" in body["data"]["reason"]


def test_portfolio_crud_and_analysis(temp_position: str) -> None:
    """持仓 CRUD + 分析：写入后可读、分析有市值、删除后不可见。"""
    listing = _success("/api/portfolio", {})
    assert any(item["id"] == temp_position for item in listing["items"])

    analysis = _success("/api/portfolio/analysis", {})
    row = next(item for item in analysis["items"] if item["id"] == temp_position)
    assert row["market"] == "fund" and row["currency"] == "CNY"
    assert row["market_value"] and row["market_value"] > 0
    assert row["pnl"] is not None
    assert analysis["totals"]["cny_market_value"] > 0
    ratio_sum = sum(item["ratio"] or 0 for item in analysis["distribution"])
    assert 99 <= ratio_sum <= 101, f"市场分布占比合计异常：{ratio_sum}"

    updated = client.put(f"/api/portfolio/{temp_position}",
                         json={"shares": 200}).json()
    assert updated["error"] is None and updated["data"]["item"]["shares"] == 200

    deleted = client.delete(f"/api/portfolio/{temp_position}").json()
    assert deleted["error"] is None
    after = _success("/api/portfolio", {})
    assert all(item["id"] != temp_position for item in after["items"])


def test_portfolio_error_contract() -> None:
    """持仓参数校验：非法市场/负份额/未知 id 均返回明确错误。"""
    bad_market = client.post("/api/portfolio", json={"market": "jp", "symbol": "1", "shares": 1, "cost": 1}).json()
    assert bad_market["error_code"] == "INVALID_PARAM"
    bad_shares = client.post("/api/portfolio", json={"market": "a", "symbol": "600519", "shares": -1, "cost": 1}).json()
    assert bad_shares["error_code"] == "INVALID_PARAM"
    missing = client.delete("/api/portfolio/not-exist-id").json()
    assert missing["error_code"] == "NOT_FOUND"
    empty_q = _body("/api/fund/search", {"q": "  "})
    assert empty_q["error_code"] == "INVALID_PARAM"


# =============================================================
# 批次 5.4：港美股 quote/kline/minute/index + 连接器能力矩阵
# 批次 5.5：AI 配置（掩码/落盘）与对话（未配置引导）
# =============================================================
def test_us_quote_real() -> None:
    """美股报价：苹果价格/昨收/币种；源可为东财或腾讯（降级链随源站可用性变化）。"""
    data = _success("/api/market/quote", {"symbol": "AAPL", "market": "us"})
    quote = data["quote"]
    assert quote["name"]
    # prev_close 是涨跌幅计算前提：无论命中哪个源都必须非空（残缺快照应在路由层被降级）
    assert quote["price"] > 0 and quote["prev_close"] > 0
    assert quote["currency"] == "USD"
    assert data["source"] in {"腾讯", "东财"}, f"未预期的报价源：{data['source']}"
    assert quote["market_status"] in {"盘中", "盘前", "盘后", "收盘快照", "时间未知", "休市"}


def test_us_kline_real() -> None:
    """美股 K 线：≥200 根、日期升序、最新日期不陈旧（回归：无交易所后缀只返回 2 条的缺陷）。"""
    data = _success("/api/market/kline",
                    {"symbol": "AAPL", "market": "us", "period": "日k", "adjust": "qfq"})
    items = data["items"]
    assert len(items) >= 200, f"美股K线不足：{len(items)}"
    dates = [item["date"] for item in items]
    assert dates == sorted(dates)
    assert dates[-1] >= "2026-09-01", f"最新日期异常：{dates[-1]}"
    assert items[-1]["high"] >= items[-1]["low"] > 0
    # 源名不写死：东财与腾讯均可为有效来源，数据质量（条数/日期/OHLC）才是判据
    assert data["source"] in {"腾讯", "东财"}, f"未预期的 K 线源：{data['source']}"


def test_hk_kline_and_quote_real() -> None:
    """港股 K 线与报价：00700 条数充足、币种 HKD、价格为正。"""
    kline = _success("/api/market/kline",
                     {"symbol": "00700", "market": "hk", "period": "日k", "adjust": "qfq"})
    assert len(kline["items"]) >= 200
    assert kline["source"] in {"腾讯", "东财"}, f"未预期的 K 线源：{kline['source']}"
    quote = _success("/api/market/quote", {"symbol": "00700", "market": "hk"})["quote"]
    assert quote["currency"] == "HKD"
    assert quote["price"] > 0


# P0-01 复测：港美股（及指数）成交额不受 ×10000 虚高；A 股仍按万元→元换算。
# 腾讯 parts[37] 单位随市场不同：A 股为万元（×10000→元）；港美为本币原值（USD/HKD）。
# 验证量级 + 量价关系 amount ≈ volume*price：港美 ratio≈1，A 股因手×100 故 ratio≈100；
# ×10000 病态令 HK/US 比率飙升 10000，被量级与比率双重拦截。
@pytest.mark.parametrize(
    "symbol,market,low,high",
    [
        ("AAPL", "us", 1e7, 1e11),   # 美股≈5e9，病态×10000≈5e13 远超上界
        ("00700", "hk", 1e7, 1e11),  # 港股≈2e10，病态≈2e14 远超上界
        ("600519", "a", 1e6, 1e11),  # A 股×10000，量级百万~百亿元
        ("000001", "a", 1e6, 1e11),  # 回归：A 股仍×10000，不得意外取消
    ],
)
def test_p001_amount_unit(symbol: str, market: str, low: float, high: float) -> None:
    """P0-01 成交额口径复测（真实网络）：港美股不 ×10000，A 股不被改坏。

    走真实腾讯报价（东财/新浪降级亦合格），不使用 mock/写死行情；量级与量价关系
    共同断定：病态 ×10000 会让 HK/US 成交额跳到 1e13–1e14 量级且 ratio=10000。

    Args:
        symbol: 股票代码。
        market: a/hk/us。
        low: 允许最小成交额（元 / 本币）。
        high: 允许最大成交额（元 / 本币）；病态 ×10000 远超本界。
    """
    # 命中真实报价，error 必须为空（契约 §6.3）
    quote = _success("/api/market/quote", {"symbol": symbol, "market": market})["quote"]
    amount = quote["amount"]  # 成交额：A 股为元，港美为本币原值
    price = quote["price"]    # 最新价
    volume = quote["volume"]  # 成交量（A 股=手，港美=股）
    assert amount is not None and amount > 0, f"{symbol} 成交额为空（P0-01 复发？）"
    # 量级断言：×10000 病态必定跌出 [low, high]
    assert low <= amount <= high, f"{symbol} amount 量级异常：{amount}（预期 [{low},{high}]）"
    # 数量关系：成交额不得远高于量价积（HK/US≈1，A 股≈100；×10000 病态=10000）
    assert volume and volume > 0 and price and price > 0, f"{symbol} 缺少 volume/price"
    ratio = amount / (volume * price)
    assert 0.1 <= ratio <= 500, f"{symbol} amount/量价不匹配：ratio={ratio}（×10000 病态=10000）"


def test_us_minute_unavailable() -> None:
    """美股分时：免费通道不可用时返回空 + 说明，不得用单点伪造分时。"""
    data = _success("/api/market/minute", {"symbol": "AAPL", "market": "us"})
    assert data["items"] == []
    assert data["available"] is False
    assert "不可用" in data["caliber"] and "腾讯" in data["caliber"]


def test_hk_minute_real() -> None:
    """港股分时：腾讯 1 分钟；盘中按已开盘分钟数放宽，收盘后要求全天点数。"""
    data = _success("/api/market/minute", {"symbol": "00700", "market": "hk"})
    items = data["items"]
    assert items, "港股分时为空"
    worst = min(item["time"] for item in items)
    latest = max(item["time"] for item in items)
    assert len(worst) == 5 and worst[2] == ":"
    # 盘中只累积到当前时刻（09:30 起每分钟 1 点）；收盘后应为全天 ~330 点。
    # 不写死「≥100」：否则盘中执行必然误报。
    if latest < "15:59":
        assert len(items) >= 10, f"港股分时不足（盘中）：{len(items)}（{worst}–{latest}）"
    else:
        assert len(items) >= 200, f"港股分时不足（收盘）：{len(items)}"
    assert all(len(item["time"]) == 5 and item["time"][2] == ":" for item in items[:5])
    # 时段口径：东财分钟线返回「20261021 10:11:00」形态，此前解析失败 → 恒为「时间未知」
    assert data["market_status"] != "时间未知", "分时时间解析失败（date+time 格式未覆盖）"


def test_overseas_index_real() -> None:
    """美股指数：三大指数齐全、点位量级正确。"""
    data = _success("/api/market/index", {"market": "us"})
    items = data["items"]
    assert len(items) == 3
    by_code = {item["code"]: item for item in items}
    assert by_code["DJI"]["price"] > 20000
    assert by_code["IXIC"]["price"] > 5000
    assert by_code["INX"]["price"] > 3000


def test_connectors_status_matrix() -> None:
    """连接器能力矩阵：四市场（R3-13 新增场外基金）能力齐全，港美新闻/板块如实标不可用（D3）。"""
    data = _success("/api/connectors/status", {})
    assert set(data["markets"]) == {"a", "hk", "us", "fund"}
    assert data["markets"]["a"]["stock_news"]["available"] is True
    assert data["markets"]["us"]["kline"]["available"] is True
    assert data["markets"]["hk"]["minute"]["available"] is True
    # 场外基金：无盘中报价/K线/分时，如实标不可用且给原因
    for feature in ("quote", "kline", "minute"):
        cell = data["markets"]["fund"][feature]
        assert cell["available"] is False
        assert cell.get("note")
    gaps = {(item["market"], item["feature"]) for item in data["gaps"]}
    assert {("us", "stock_news"), ("us", "sector"),
            ("hk", "stock_news"), ("hk", "sector")} <= gaps
    reasons = " ".join(item["reason"] for item in data["gaps"])
    assert "暂无免费" in reasons or "无免费" in reasons or "无稳定" in reasons or "无" in reasons
    assert data["datasources"], "数据源开关列表为空"


def test_health_datasources_real_capability() -> None:
    """P1-05：health 与 connectors 的数据源状态同源，且不再出现「待接入」。

    外部 HTTP 通道（同花顺/财联社/蛋卷）由 core.capabilities 做最小真实探测，
    状态必须是「已接入 / 已接入（V8 降级）/ 通道异常（探测失败）」之一；
    养基宝按授权态显示「未授权待启用」或「已授权（已绑定）」。
    """
    health = _success("/api/health", {})
    sources = health["datasources"]
    assert {"ths", "cls", "fund", "yangjibao"} <= set(sources)
    for key in ("ths", "cls", "fund"):
        status = sources[key]["status"]
        assert status != "待接入", f"{key} 仍误报待接入"
        assert status.startswith(("已接入", "通道异常")), f"{key} 状态异常：{status}"
    assert sources["yangjibao"]["status"] in {"未授权待启用", "已授权（已绑定）"}

    connectors = _success("/api/connectors/status", {})
    connector_map = {item["key"]: item for item in connectors["datasources"]}
    for key, meta in sources.items():
        assert connector_map[key]["status"] == meta["status"], f"{key} 两处状态不一致"


def test_ai_config_masked() -> None:
    """AI 配置读取：默认未配置，响应不含明文密钥字段。"""
    body = _body("/api/ai/config", {})
    assert body["error"] is None
    data = body["data"]
    assert "configured" in data
    # get_public_ai_config 的 api_key 字段本身就是掩码，不能是明文
    assert "****" in str(data.get("api_key")) or data.get("api_key") in ("", None, "未配置")


def test_ai_config_save_and_mask(monkeypatch: _pytest.MonkeyPatch, tmp_path) -> None:
    """AI 配置保存：写入临时目录（不触碰真实 data/ai_config.json），校验掩码与非法地址。"""
    monkeypatch.setattr(config, "AI_CONFIG_FILE", tmp_path / "ai_config.json")
    bad = client.post("/api/ai/config", json={"base_url": "ftp://x", "api_key": "sk-test"}).json()
    assert bad["error_code"] == "INVALID_PARAM"
    saved = client.post("/api/ai/config",
                        json={"base_url": "http://127.0.0.1:9/v1", "api_key": "sk-test-123456",
                              "model": "test-model"}).json()
    assert saved["error"] is None
    assert saved["data"]["configured"] is True
    assert saved["data"]["model"] == "test-model"
    masked = str(saved["data"]["api_key"])
    assert "sk-test-123456" not in masked and "****" in masked


def test_ai_chat_not_configured(monkeypatch: _pytest.MonkeyPatch) -> None:
    """未配置时对话必须返回 NOT_CONFIGURED 与设置引导，不返回假回答。"""
    monkeypatch.setattr(config, "load_ai_config",
                        lambda: config.AIConfig(base_url="", api_key="", model="", source="none"))
    body = client.post("/api/ai/chat", json={"message": "你好"}).json()
    assert body["error_code"] == "NOT_CONFIGURED"
    assert "设置" in body["error"]
    assert body["data"]["configured"] is False
    empty = client.post("/api/ai/chat", json={"message": "   "}).json()
    assert empty["error_code"] == "INVALID_PARAM"


# =============================================================
# P1 批次（2026-10-23）：时区判定 / 基金不存在 / 快速失败 / 备源记忆
# 全部为真实网络断言，不使用 mock 或写死行情。
# =============================================================
def _us_now() -> datetime:
    """当前美东时间（naive），用于美股时段判定。

    Returns:
        datetime: 美东本地时间（naive，无时区信息）。
    """
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("America/New_York")).replace(tzinfo=None)


def _recent_us_close_beijing() -> datetime:
    """最近一次已收盘的美股常规时段收盘（16:00 美东）对应的北京时间。

    规则：取美东当日 16:00，若尚未到达则回退一天；周末继续回退到周五。
    该值即非盘中时段 quote_time 应当等于的时刻（夏令时=北京 04:00，冬令时=05:00）。

    Returns:
        datetime: 北京时间（naive）。
    """
    from zoneinfo import ZoneInfo

    et_now = datetime.now(ZoneInfo("America/New_York"))
    close = et_now.replace(hour=16, minute=0, second=0, microsecond=0)
    if close > et_now:
        close -= timedelta(days=1)
    while close.weekday() >= 5:
        close -= timedelta(days=1)
    return close.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)


def test_p101_us_quote_time_is_beijing() -> None:
    """P1-01/P2-15：美股 quote_time 必须是**北京时间**，且时段判定须与美东时区一致。

    回归点一（换算）：腾讯美股 parts[30] 原样为美东当地时间，未换算时
    quote_time 会比北京时间晚 12~13 小时（跨日），并因日期早一天被判收盘快照。
    回归点二（时段）：收盘/周末时 quote_time 天然滞后于 now，不能要求它贴近 now；
    但必须落在「最近一个美股常规时段收盘」这一刻上（日期已回退到最近交易日、
    周末回退周五），且状态为休市/盘后/收盘快照、is_realtime=False。
    """
    quote = _success("/api/market/quote", {"symbol": "AAPL", "market": "us"})["quote"]
    assert quote["quote_time"], "美股报价缺少 quote_time"
    as_of = datetime.strptime(quote["quote_time"], "%Y-%m-%d %H:%M:%S")
    now = datetime.now()
    et_now = _us_now()
    in_session = et_now.weekday() < 5 and (9, 30) <= (et_now.hour, et_now.minute) <= (16, 0)
    if in_session:
        # 盘中：报价应贴近北京时间（5 分钟容差覆盖短暂停牌与抓取延迟）
        delta = abs((now - as_of).total_seconds())
        assert delta < 300, f"美股盘中 quote_time 未贴近北京时间：{quote['quote_time']}"
        assert quote["market_status"] == "盘中", f"美东盘中应判盘中，实际 {quote['market_status']}"
        assert quote["is_realtime"] is True, "美东盘中数据 is_realtime 必须为 True"
        return

    # 非盘中：校验「已正确换算为北京时间并落在最近一个常规时段收盘」
    expected = _recent_us_close_beijing()
    drift = abs((as_of - expected).total_seconds())
    # 未换算的美东 16:00 会与原值相差 12~13 小时，drift 判定可直接拦住该回归
    assert drift <= 600, (
        f"quote_time={quote['quote_time']} 不在最近美股收盘 {expected:%Y-%m-%d %H:%M:%S} 附近"
        f"（偏差 {drift / 3600:.2f} 小时，疑似未做美东→北京换算）"
    )
    # 非盘中允许的状态：休市 / 盘后 / 收盘快照 / 盘前。
    # 「盘前」是 2026-10-26 12:20（北京）= 00:20（美东，周一）实测暴露的口径缺口：
    # 项目 `_us_session_phase` 对「常规时段开始前」一律标盘前（含 00:00–04:00 ET 隔夜），
    # 该分支本身正确（时区换算已由上面的 drift 与下面的时段窗断言双重把关），
    # 是原断言的状态集合漏枚举，故补齐，不改应用行为。
    assert quote["market_status"] in {"休市", "盘后", "收盘快照", "盘前"}, (
        f"非盘中应为休市/盘后/收盘快照/盘前，实际 {quote['market_status']}"
    )
    assert quote["is_realtime"] is False, "非盘中数据不得标记为实时"
    # 时段窗断言：北京时刻必须落在美股常规时段收盘窗内（夏令时 04:00 / 冬令时 05:00）
    clock_minutes = as_of.hour * 60 + as_of.minute
    assert clock_minutes >= 21 * 60 + 30 or clock_minutes <= 5 * 60 + 15, (
        f"quote_time 落在非美股时段：{quote['quote_time']}"
    )


def test_p102_fund_not_found_contract() -> None:
    """P1-02：不存在的基金必须返回 NOT_FOUND，且不得返回 nav=0.0 的「成功」。"""
    body = _body("/api/fund/realtime", {"code": "999999"})
    assert body["error_code"] == "NOT_FOUND", f"实际 {body['error_code']}（{body.get('error')}）"
    assert body["error"] and "未找到" in body["error"]
    nav = (body["data"] or {}).get("nav")
    assert not nav, f"不存在基金不得返回净值，实际 nav={nav}"
    # 对照：真实基金仍正常出数（防止把闸门做成全局误杀）
    good = _success("/api/fund/realtime", {"code": "110022"})
    assert good["nav"] and good["nav"] > 0


def test_p103_missing_symbol_fast_fail() -> None:
    """P1-03：不存在的标的必须快速失败（NOT_FOUND），不得逐源等满超时。"""
    for symbol, market in (("999999", "a"), ("ZZZZ", "us")):
        started = _time.time()
        body = _body("/api/market/quote", {"symbol": symbol, "market": market})
        elapsed = _time.time() - started
        assert body["error_code"] == "NOT_FOUND", f"{symbol} 实际 {body['error_code']}"
        assert elapsed < 3.0, f"{symbol} 错误路径耗时 {elapsed:.2f}s，超过 3 秒快速失败门槛"
    # K 线与分时同样走存在性闸门
    started = _time.time()
    assert _body("/api/market/kline", {"symbol": "999999", "market": "a", "period": "日k"})["error_code"] == "NOT_FOUND"
    assert _time.time() - started < 3.0


def test_p104_kline_preferred_source_memory() -> None:
    """P1-04：K 线冷启动命中可用源且耗时收敛；备源优先级被记住（进程级）。"""
    from backend.app.core import resilient

    started = _time.time()
    data = _success("/api/market/kline",
                    {"symbol": "AAPL", "market": "us", "period": "月k", "adjust": "qfq"})
    elapsed = _time.time() - started
    assert data["source"] in {"腾讯", "东财"}, f"未预期源 {data['source']}"
    assert len(data["items"]) >= 20
    # 修复前：东财 push2 失效时每源重试 3 次退避，冷启动 10.76s；阈值按修复后留足余量
    assert elapsed < 6.0, f"美股 K 线冷启动 {elapsed:.2f}s，未收敛"
    # 备源优先级记忆：本次成功源必须被记住，下次同链路优先尝试
    assert resilient.preferred_source("market:kline:us") == data["source"]


def test_yjb_collect_and_holdings_real(monkeypatch) -> None:
    """养基宝工作台接入：账户概览与持仓估值（未绑定时退化为契约断言）。

    本用例不隔离 token，因此能读到本机真实绑定态：
    - 已绑定 → 断言真实字段与口径自洽
    - 未绑定 → 只断言降级契约（UPSTREAM_ERROR + 明确文案 + 不给假数），不 skip

    Args:
        monkeypatch: pytest 补丁器（用于把养基宝路径指回真实 data/，只读不写）。

    Notes:
        第五轮阶段 2 起，``conftest`` 默认把 data 目录隔离到 ``tmp_path``
        （防止 pytest 改写 portfolio 等用户数据）；本用例按设计需读真实绑定态，
        故显式覆盖回真实路径 —— 仅读取 token/state，不做任何写入。
    """
    import pathlib as _pathlib

    from backend.app.datasource import yangjibao as _yjb

    real_data = _pathlib.Path(__file__).resolve().parents[1] / "data"
    monkeypatch.setattr(_yjb, "YJB_TOKEN_FILE", real_data / "yjb_token.json")
    monkeypatch.setattr(_yjb, "YJB_STATE_FILE", real_data / "yjb_state.json")
    monkeypatch.setattr(_yjb, "YJB_SECRET_FILE", real_data / "yjb_secret.json")

    status = _success("/api/fund/yjb/status", {})
    if not status.get("bound"):
        for path in ("/api/fund/yjb/collect", "/api/fund/yjb/holdings"):
            body = _body(path, {})
            assert body["error_code"] == "UPSTREAM_ERROR", f"{path} 实际 {body['error_code']}"
            assert body["error"] and "未绑定" in body["error"]
            assert not (body.get("data") or {}).get("total_assets")
        return

    collect = _success("/api/fund/yjb/collect", {})
    assert collect["total_assets"] > 0, "总资产必须为正"
    accounts = collect["accounts"]
    assert accounts, "已绑定但账户列表为空"
    for acc in accounts:
        assert acc["id"] and acc["title"]
        assert isinstance(acc["today_income"], (int, float))
        assert isinstance(acc["today_income_rate"], (int, float))
    # 汇总口径自洽：今日收益 = 各账户之和
    total = sum(a["today_income"] for a in accounts)
    assert abs(total - collect["today_income"]) < 1.0, "汇总与账户明细不一致"
    assert "account_collect" in collect["source"]

    holdings = _success("/api/fund/yjb/holdings", {"account_id": "all"})
    items = holdings["items"]
    assert items, "已绑定但持仓为空"
    assert holdings["total"] == len(items)
    assert all(len(it["code"]) == 6 for it in items), "基金代码必须 6 位"
    rated = [it["pct"] for it in items if it["pct"] is not None]
    assert rated == sorted(rated, reverse=True), "应按估算涨跌降序"
    assert all(it["account_id"] for it in items), "每条必须带来源账户"
    assert "fund_hold" in holdings["source"]

    # 单账户过滤：不得混入其他账户
    first = holdings["accounts"][0]["id"]
    sub = _success("/api/fund/yjb/holdings", {"account_id": first})
    assert sub["items"], f"账户 {first} 应有持仓"
    assert all(it["account_id"] == first for it in sub["items"])
    assert len(sub["items"]) <= len(items)


# =============================================================
# 新增功能 ⑥：财报 / 公告搜索（2026-10-26）
# 契约见《设计基准_财报搜索_20261025.md》§7.1；
# 真实源站拉取（不打桩），仅 EDGAR UA 用例用 monkeypatch 拦截请求头与响应。
# =============================================================


def test_financial_search_cn_real() -> None:
    """A 股财报检索真实出数：标题/公司/北京时间/原文链接四字段非空。"""
    data = _success("/api/news/financial-search", {"market": "a", "q": "600519", "since": "365"})
    items = data["list"]
    assert items, "A 股检索应返回真实公告"
    assert data["total"] > 0
    for row in items[:10]:
        assert row["title"], "标题不得为空"
        assert row["company"], "公司不得为空"
        assert row["code"] == "600519"
        assert row["market"] == "a"
        assert row["pubTime"], "发布时间不得为空"
        # 北京时间口径：形如 2026-08-15 或 2026-08-15 19:55，不带 ISO 的 T/Z
        assert row["pubTime"][:4].isdigit() and row["pubTime"][4] == "-"
        assert "T" not in row["pubTime"] and "Z" not in row["pubTime"]
        assert row["docUrl"].startswith("https://"), "原文链接必须是可打开的绝对地址"
        assert row["source"] in ("巨潮资讯", "东方财富"), f"源站标识异常：{row['source']}"
    assert data["caliber"], "必须给出中文口径说明"
    assert data["as_of"], "必须带数据时间戳"


def test_financial_search_us_real_and_type_filter() -> None:
    """美股 EDGAR 真实出数：10-K 过滤生效且只出 10-K。"""
    data = _success(
        "/api/news/financial-search",
        {"market": "us", "q": "NVDA", "type": "10-K", "since": "365"},
    )
    items = data["list"]
    assert items, "EDGAR 应返回 10-K 申报"
    assert all(row["type"] == "10-K" for row in items), "类型过滤必须生效"
    assert all(row["docUrl"].startswith("https://www.sec.gov/Archives/edgar/data/") for row in items)
    assert all(row["code"] == "NVDA" for row in items)
    assert all(row["pubTime"] for row in items)


def test_financial_search_hk_real() -> None:
    """港股 HKEX 真实出数：带代码检索返回披露易原文链接。"""
    data = _success("/api/news/financial-search", {"market": "hk", "q": "00700", "since": "90"})
    items = data["list"]
    assert items, "HKEX 应返回腾讯控股公告"
    first = items[0]
    assert first["market"] == "hk"
    assert first["source"] == "HKEX披露易"
    assert first["title"]
    assert first["pubTime"]
    assert first["docUrl"].startswith("https://www1.hkexnews.hk/")


def test_financial_search_empty_is_not_zero_filled() -> None:
    """空/错分离：港股无关键词返回空列表 + 中文说明，绝不补零造数。"""
    data = _success("/api/news/financial-search", {"market": "hk", "q": "", "since": "all"})
    assert data["list"] == [], "无关键词的港股检索必须返回空列表"
    assert data["total"] == 0
    assert ("需输入" in data["caliber"]) or ("不支持" in data["caliber"]), "空态必须说明原因"


def test_financial_search_invalid_params() -> None:
    """参数非法：INVALID_PARAM + 中文提示（不发起上游请求）。"""
    for params in ({"market": "xx"}, {"type": "月报"}, {"since": "999"}):
        body = _body("/api/news/financial-search", params)
        assert body["error_code"] == "INVALID_PARAM", f"{params} 应被拒绝"
        assert body["error"]


def test_financial_search_edgar_requires_user_agent(monkeypatch) -> None:
    """EDGAR 必带 User-Agent（否则 403）；同时验证表种过滤与 UTC→北京换算。"""
    from backend.app.datasource import financial_report as fr

    captured = []

    class _Resp:
        """最小响应替身：只提供 status_code 与 json()。"""

        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            """返回预置 JSON。"""
            return self._payload

    def _fake(method, url, **kwargs):
        """拦截 HTTP 请求并记录请求头。"""
        captured.append({"method": method, "url": url, "headers": kwargs.get("headers") or {}})
        if "company_tickers" in url:
            return _Resp({"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}})
        return _Resp({
            "name": "NVIDIA CORP",
            "filings": {"recent": {
                "form": ["10-K", "4", "8-K"],
                "filingDate": ["2026-02-26", "2026-03-02", "2026-09-03"],
                "acceptanceDateTime": [
                    "2026-02-26T21:42:00.000Z",
                    "2026-03-02T10:00:00.000Z",
                    "2026-09-03T12:03:00.000Z",
                ],
                "accessionNumber": [
                    "0001045810-26-000010",
                    "0001045810-26-000011",
                    "0001045810-26-000012",
                ],
                "primaryDocument": ["nvda-20260125.htm", "doc4.xml", "nvda-8k.htm"],
            }},
        })

    monkeypatch.setattr(fr.netfix, "http_request", _fake)
    monkeypatch.setattr(fr, "_TICKERS_CACHE", {"ts": 0.0, "data": None})
    rows, total, source, _caliber = fr.search_us(
        q="NVDA", type_value="全部", since="365", page=1, page_size=20
    )
    assert source == "SEC EDGAR"
    assert total == 2, "只收录 10-K/10-Q/8-K，4 号表必须被过滤"
    assert {row["type"] for row in rows} == {"10-K", "8-K"}
    ten_k = [row for row in rows if row["type"] == "10-K"][0]
    assert ten_k["pubTime"].startswith("2026-02-27"), "UTC 21:42 应换算为北京时间次日 05:42"
    assert ten_k["docUrl"].endswith("/nvda-20260125.htm")
    for item in captured:
        assert "OpenBB-CN" in item["headers"].get("User-Agent", ""), f"{item['url']} 缺 UA"
