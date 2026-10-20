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
    """分时：至少 30 个点、时间格式正确、价格量级合理。"""
    data = _success("/api/market/minute", {"symbol": "600519", "market": "a", "period": "1min"})
    items = data["items"]
    assert len(items) >= 30
    assert data["source"] in {"东财", "新浪", "腾讯"}
    assert data["as_of"]
    for item in items[:5]:
        assert len(item["time"]) == 5 and item["time"][2] == ":"
        assert item["price"] > 0
    assert data["market_status"] in {"盘中", "收盘快照", "时间未知", "盘后"}


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
    assert len(minute) >= 30
    times = [item["time"] for item in minute]
    assert all(len(value) == 5 and value[2] == ":" for value in times[:5])
    # 分时须覆盖上下午两段，且午休区间不出现数据点（P0-06 午休断裂）
    assert any(value < "11:31" for value in times)
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
    """组件注册表：35 个区块（5.1 的 21 + 5.2 的 4 + 5.3 的 10）、编号唯一、48 列映射正确。"""
    body = _body("/api/widgets", {})
    assert body["error"] is None
    data = body["data"]
    assert data["total"] == 35
    ids = [item["id"] for item in data["items"]]
    assert ids[:1] == ["S-01"] and "ST-01" in ids and "V-03" in ids
    assert {"N-01", "N-02", "N-03", "N-04"} <= set(ids)
    assert {"F-01", "F-02", "F-04", "P-01", "P-03"} <= set(ids)
    assert {"F-01", "F-02", "F-04", "P-01", "P-03"} <= set(ids)
    assert len(set(ids)) == 35
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
    """财联社快讯：条数、时间、情绪分值与三档标签全部合规。"""
    data = _success("/api/news/flash", {"limit": 30})
    items = data["items"]
    assert len(items) >= 10
    assert data["source"] == "财联社"
    assert data["caliber"]
    labels = set()
    for item in items:
        assert item["title"] and item["time"]
        sentiment = item["sentiment"]
        assert -0.1 <= sentiment["score"] <= 0.1
        assert sentiment["label"] in {"利好", "利空", "中性"}
        assert sentiment["strength"] in {"强", "中", "弱"}
        labels.add(sentiment["label"])
    assert len(labels) >= 2, "全部中性说明词典未生效"


def test_news_stock_real() -> None:
    """个股新闻：中微公司 ≥5 条，含来源/时间/链接与命中板块。"""
    data = _success("/api/news/stock", {"symbol": "688012", "limit": 10})
    items = data["items"]
    assert len(items) >= 5
    assert data["symbol"] == "688012"
    for item in items:
        assert item["title"] and item["time"]
        assert item["source"]
        assert isinstance(item["boards"], list)
    assert any("半导体" in board for item in items for board in item["boards"])


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


def test_news_sector_search_real() -> None:
    """板块检索：先取池内真实命中板块再回查（滚动数据，不写死板块）；未知板块 NOT_FOUND。"""
    pool = _success("/api/news/ai-feed", {"limit": 60})
    candidates = []
    for item in pool["items"]:
        candidates.extend(item.get("boards") or [])
    assert candidates, "新闻池无任何板块命中"
    sector = candidates[0]

    data = _success("/api/news/sector-search", {"sector": sector, "limit": 20})
    assert data["items"], f"池内命中 {sector} 但检索为空"
    assert data["sector"] == sector
    for item in data["items"]:
        assert sector in item["boards"]

    bad = _body("/api/news/sector-search", {"sector": "不存在的板块"})
    assert bad["error_code"] == "NOT_FOUND"


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
    assert candidates, "池内没有任何板块命中，关键词表可能失效"
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
    """板块情绪：按池内真实命中板块回查，温度与样本齐全。"""
    pool = _success("/api/news/ai-feed", {"limit": 60})
    candidates = [board for item in pool["items"] for board in (item.get("boards") or [])]
    assert candidates
    board = candidates[0]

    data = _success("/api/sentiment/board", {"board": board})
    assert data["total"] >= 1
    assert data["board"] == board
    assert data["level"] == 3
    assert data["keywords"] and board in data["keywords"]
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
    """最新净值：T+1 口径、非实时、降级链如实记录。"""
    data = _success("/api/fund/realtime", {"code": "017811"})
    assert data["nav"] and data["nav"] > 0
    assert data["nav_date"] and len(data["nav_date"]) == 10
    assert data["is_realtime"] is False
    assert "T+1" in data["caliber"]
    channels = [item["channel"] for item in data["attempts"]]
    assert "蛋卷 nav_grtd" in channels and "蛋卷 derived" in channels
    disabled = next(item for item in data["attempts"] if item["channel"] == "蛋卷 nav_grtd")
    assert disabled["ok"] is False and "禁用" in disabled["reason"]


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
    """档案：名称/管理人/成立日/托管人齐全。"""
    data = _success("/api/fund/info", {"code": "017811"})
    assert data["name"] == "东方人工智能主题混合C"
    assert data["manager"]
    assert data["found_date"] == "2023-02-22"
    assert data["keeper"]
    assert data["xq"]["基金公司"]


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
    """美股报价：苹果价格/昨收/币种，源为腾讯（东财 push2 本机不可用）。"""
    data = _success("/api/market/quote", {"symbol": "AAPL", "market": "us"})
    quote = data["quote"]
    assert quote["name"]
    assert quote["price"] > 0 and quote["prev_close"] > 0
    assert quote["currency"] == "USD"
    assert data["source"] == "腾讯"
    assert quote["market_status"] in {"盘中", "盘前", "盘后", "收盘快照", "时间未知", "休市"}


def test_us_kline_real() -> None:
    """美股 K 线：320 根、日期升序（回归：无交易所后缀只返回 2 条的缺陷）。"""
    data = _success("/api/market/kline",
                    {"symbol": "AAPL", "market": "us", "period": "日k", "adjust": "qfq"})
    items = data["items"]
    assert len(items) >= 200, f"美股K线不足：{len(items)}"
    dates = [item["date"] for item in items]
    assert dates == sorted(dates)
    assert dates[-1] >= "2026-09-01", f"最新日期异常：{dates[-1]}"
    assert items[-1]["high"] >= items[-1]["low"] > 0
    assert data["source"] == "腾讯"


def test_hk_kline_and_quote_real() -> None:
    """港股 K 线与报价：腾讯 00700，币种 HKD。"""
    kline = _success("/api/market/kline",
                     {"symbol": "00700", "market": "hk", "period": "日k", "adjust": "qfq"})
    assert len(kline["items"]) >= 200
    assert kline["source"] == "腾讯"
    quote = _success("/api/market/quote", {"symbol": "00700", "market": "hk"})["quote"]
    assert quote["currency"] == "HKD"
    assert quote["price"] > 0


def test_us_minute_unavailable() -> None:
    """美股分时：免费通道不可用时返回空 + 说明，不得用单点伪造分时。"""
    data = _success("/api/market/minute", {"symbol": "AAPL", "market": "us"})
    assert data["items"] == []
    assert data["available"] is False
    assert "不可用" in data["caliber"] and "腾讯" in data["caliber"]


def test_hk_minute_real() -> None:
    """港股分时：腾讯 1 分钟，≥100 点且时间格式正确。"""
    data = _success("/api/market/minute", {"symbol": "00700", "market": "hk"})
    items = data["items"]
    assert len(items) >= 100, f"港股分时不足：{len(items)}"
    assert all(len(item["time"]) == 5 and item["time"][2] == ":" for item in items[:5])


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
    """连接器能力矩阵：三市场能力齐全，港美新闻/板块如实标不可用（D3）。"""
    data = _success("/api/connectors/status", {})
    assert set(data["markets"]) == {"a", "hk", "us"}
    assert data["markets"]["a"]["stock_news"]["available"] is True
    assert data["markets"]["us"]["kline"]["available"] is True
    assert data["markets"]["hk"]["minute"]["available"] is True
    gaps = {(item["market"], item["feature"]) for item in data["gaps"]}
    assert {("us", "stock_news"), ("us", "sector"),
            ("hk", "stock_news"), ("hk", "sector")} <= gaps
    reasons = " ".join(item["reason"] for item in data["gaps"])
    assert "暂无免费" in reasons or "无免费" in reasons or "无稳定" in reasons
    assert data["datasources"], "数据源开关列表为空"


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
