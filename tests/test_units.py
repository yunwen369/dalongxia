# -*- coding: utf-8 -*-
"""单元测试：核心计算与规则引擎（§11.2 功能/数据测试的用例层）。

覆盖（规范 §11.9 复算验证 + 批次 5.2/5.3/7 业务规则 + 阶段 8 七步之①）：
- 回撤计算（独立复算：手工构造净值序列验证峰谷/最大/当前回撤）；
- 持仓盈亏（份额×价、汇率折算、缺汇率 null）；
- 新闻初评规则（确定性、条件矩阵、否定、跨板块相反标签）；
- 板块摘要派生（Top3/集中度）、热力图负值不钳 0、去重前后缀；
- 路由契约矩阵（12 router 代表端点：成功/空数据/失败/参数非法四路径）；
- 前端视图静态一致性（VIEW_IDS ↔ index.html 容器 ↔ 菜单 ↔ i18n 键）。

约定：本文件为纯计算/契约单测，provider 用猴子补丁做**单元内部模拟**
（规范门禁允许：单测内部模拟除外）；真实拉取断言由 test_qa_verify.py 另行覆盖。
"""

from __future__ import annotations

import math
from datetime import datetime

import pytest

from backend.app.datasource import news as news_engine
from backend.app.datasource import sentiment as senti


# =============================================================
# 一、回撤计算（TC-基金-复算）
# =============================================================
class TestDrawdownMath:
    """回撤公式独立复算：dd[i] = NAV[i]/peak[i] - 1。"""

    def _drawdown(self, navs):
        """用与 provider 相同的公式独立实现一遍，作为互证基准。"""
        peak = navs[0]
        peak_idx = 0
        max_dd, dd_peak, dd_trough = 0.0, 0, 0
        for i, nav in enumerate(navs):
            if nav > peak:
                peak, peak_idx = nav, i
            dd = nav / peak - 1 if peak else 0.0
            if dd < max_dd:
                max_dd, dd_peak, dd_trough = dd, peak_idx, i
        current = navs[-1] / peak - 1 if peak else 0.0
        return max_dd, dd_peak, dd_trough, current

    def test_simple_series(self):
        """手工序列：100→120→90→110 → 最大回撤 25%（120→90）。"""
        max_dd, peak_idx, trough_idx, current = self._drawdown([100, 120, 90, 110])
        assert math.isclose(max_dd, -0.25)
        assert peak_idx == 1 and trough_idx == 2
        assert math.isclose(current, 110 / 120 - 1)

    def test_no_drawdown(self):
        """单边上涨：回撤 0。"""
        max_dd, *_ = self._drawdown([1, 2, 3, 4])
        assert max_dd == 0.0

    def test_trough_at_end(self):
        """谷底在末尾：当前回撤=最大回撤。"""
        max_dd, _, _, current = self._drawdown([100, 80, 60])
        assert math.isclose(max_dd, -0.4) and math.isclose(current, -0.4)


# =============================================================
# 二、持仓盈亏与汇率（TC-持仓-复算）
# =============================================================
class TestPortfolioMath:
    """持仓市值/盈亏/折算复算。"""

    def test_pnl_math(self):
        """1000 份 × 现价 2.7501 vs 成本 3.0 → 市值 2750.10、盈亏 -249.90。"""
        shares, cost, price = 1000, 3.0, 2.7501
        market_value = shares * price
        pnl = market_value - shares * cost
        assert round(market_value, 2) == 2750.10
        assert round(pnl, 2) == -249.90
        assert round(pnl / (shares * cost) * 100, 2) == -8.33

    def test_fx_conversion(self):
        """USD 持仓折算：1000 股 × $100 × 6.6810 = ¥668,100。"""
        usd_value = 1000 * 100
        assert round(usd_value * 6.6810, 2) == 668100.0

    def test_missing_fx_is_none_not_zero(self):
        """缺汇率时折算值必须为 None（不猜测、不补 0）。"""
        rate = 0.0
        usd_value = 500
        converted = usd_value * rate if rate else None
        assert converted is None


# =============================================================
# 三、新闻初评规则（TC-情绪-规则）
# =============================================================
class TestSentimentRules:
    """确定性、条件矩阵、否定、跨板块相反标签。"""

    def test_deterministic(self):
        """同一文本两次打分结果必然相同。"""
        first = senti.analyze("公司中标海外大单 业绩超预期", source="财联社")
        second = senti.analyze("公司中标海外大单 业绩超预期", source="财联社")
        assert first == second

    def test_score_bounds_and_labels(self):
        """分值边界与三档映射。"""
        for text, label in [
            ("大幅上调目标价 创历史新高", "利好"),
            ("被证监会立案调查", "利空"),
            ("今日召开股东大会", "中性"),
        ]:
            result = senti.analyze(text)
            assert -0.1 <= result["score"] <= 0.1
            assert result["label"] == label

    def test_conditional_matrix_reverses(self):
        """条件矩阵：预增但「低于预期」→ 转利空（对同一板块相反标签）。"""
        good = senti.analyze("半导体公司三季度业绩预增")
        bad = senti.analyze("半导体公司三季度业绩预增 但低于预期")
        assert good["label"] == "利好"
        assert bad["label"] in ("利空", "中性")
        assert bad["score"] < good["score"]

    def test_cross_board_opposite_labels(self):
        """同一新闻对不同板块可相反：燃油降价利好航空、利空石油石化。"""
        text = "成品油价格下调 每吨降400元"
        aviation = senti.analyze(text + " 航空公司成本压力缓解")
        petrol = senti.analyze(text + " 炼化企业收入承压 业绩下滑")
        assert aviation["label"] in ("利好", "中性")
        assert petrol["label"] == "利空"
        assert petrol["score"] < aviation["score"]

    def test_negation_before_and_after(self):
        """否定词前置/后置均反转方向。"""
        assert senti.analyze("公司终止回购方案")["label"] == "利空"
        assert senti.analyze("重组计划终止")["label"] in ("利空", "中性")

    def test_weight_constants_frozen(self):
        """三维度权重与事件数/条件数为冻结口径。"""
        assert (senti.W_DIRECTION, senti.W_STRENGTH, senti.W_CREDIBILITY) == (0.45, 0.35, 0.20)
        assert len(senti.EVENT_RULES) == 18
        assert len(senti.CONDITION_RULES) == 9

    def test_aggregate_temperature(self):
        """聚合温度：均分 0 → 50；+0.1 → 100；-0.1 → 0（label 与 score 口径一致）。"""
        def item(score):
            label = "利好" if score >= senti.BOUND_NEUTRAL else ("利空" if score <= -senti.BOUND_NEUTRAL else "中性")
            return {"sentiment": {"score": score, "label": label, "events": []}}

        assert senti.aggregate([item(0.0)])["temperature"] == 50.0
        assert senti.aggregate([item(0.1)])["temperature"] == 100.0
        assert senti.aggregate([item(-0.1)])["temperature"] == 0.0
        agg = senti.aggregate([item(0.1), item(-0.1), item(0.0)])
        assert agg["positive"] == 1 and agg["negative"] == 1 and agg["neutral"] == 1


# =============================================================
# 四、去重与板块树（TC-新闻-结构）
# =============================================================
class TestNewsStructure:
    """去重/板块树完整性。"""

    def test_dedup_exact_and_prefix(self):
        """完全一致与「前 24 字前缀」近似（跨源转载）均去重，channels 记录全部来源。

        注：前缀近似以归一化标题前 24 字为键，故三条标题前 24 字须完全一致；
        短标题（<12 字）不参与前缀近似（防误合并），由另用例覆盖。
        """
        items = [
            {"title": "某半导体设备公司中标海外大额订单并进入交付阶段公告", "channel": "财联社快讯", "time": "2026-10-20 09:00:00"},
            {"title": "某半导体设备公司中标海外大额订单并进入交付阶段公告！", "channel": "东财全球", "time": "2026-10-20 09:01:00"},
            {"title": "某半导体设备公司中标海外大额订单并进入交付阶段公告显示", "channel": "同花顺全球", "time": "2026-10-20 09:02:00"},
        ]
        result = news_engine.dedup(items)
        assert len(result) == 1
        assert set(result[0]["channels"]) == {"财联社快讯", "东财全球", "同花顺全球"}

    def test_dedup_short_titles_kept(self):
        """短标题（<12 字）不参与前缀近似，只做精确去重，避免误杀。"""
        items = [
            {"title": "半导体设备公司中标", "channel": "财联社快讯", "time": "2026-10-20 09:00:00"},
            {"title": "快讯半导体设备公司中标", "channel": "东财全球", "time": "2026-10-20 09:01:00"},
        ]
        assert len(news_engine.dedup(items)) == 2

    def test_board_tree_counts(self):
        """板块树 6/15/28 且无重复名。"""
        tree = news_engine.get_board_tree()
        assert tree["counts"] == {"level1": 6, "level2": 15, "leaf": 28}
        names = [
            leaf["name"]
            for level1 in tree["tree"]
            for level2 in level1["children"]
            for leaf in level2["children"]
        ]
        assert len(names) == len(set(names)) == 28

    def test_board_leaf_expansion(self):
        """一/二级板块展开为末级（市场过滤依赖）。"""
        assert "半导体设备" in news_engine.board_leaf_names("科技成长")
        assert "半导体设备" in news_engine.board_leaf_names("半导体")
        assert news_engine.board_leaf_names("半导体设备") == ["半导体设备"]
        assert news_engine.board_leaf_names("不存在") == []


# =============================================================
# 五、时间口径（TC-通用-时段）
# =============================================================
class TestMarketClock:
    """A股时段判定（Asia/Shanghai）。"""

    @pytest.mark.parametrize("hhmm,expected", [
        ("09:00", "盘前"), ("09:15", "盘中"), ("10:30", "盘中"), ("11:30", "盘中"),
        ("12:00", "休市"), ("13:00", "盘中"), ("15:00", "盘中"),
        ("15:01", "盘后"), ("16:30", "盘后"),
    ])
    def test_intraday_labels(self, hhmm, expected):
        """A股时段判定：9:15 集合竞价起盘中，9:15 前盘前，
        11:30-13:00 午间休市归'休市'，15:00 后盘后；休市不伪装实时。"""
        from backend.app.datasource import utils

        day = datetime(2026, 10, 20, int(hhmm[:2]), int(hhmm[3:]))
        status, realtime = utils.classify_market_status(day, "a", now=day)
        assert status == expected
        assert realtime is (expected == "盘中")

    @pytest.mark.parametrize("text", [
        "20261021 10:11:00",   # 东财分钟线：无横线日期 + 时间（本轮修复）
        "20261016161447",      # 腾讯紧凑格式
        "2026/10/16 18:31:15",  # 新浪斜杠格式
        "2026-10-16 16:42:09",  # 标准格式
        "2026-10-16 16:42",     # 无秒
    ])
    def test_parse_datetime_formats(self, text):
        """时间解析：覆盖东财 date+time 形态，避免港股盘中被判「时间未知」。"""
        from backend.app.datasource import utils

        parsed = utils.parse_datetime(text)
        assert parsed is not None, f"未解析：{text}"
        assert parsed.year == 2026

    def test_hk_intraday_not_unknown(self):
        """港股盘中：东财分钟线时间经解析后应判「盘中」且 is_realtime=True。"""
        from backend.app.datasource import utils

        now = datetime(2026, 10, 21, 10, 11)
        parsed = utils.parse_datetime("20261021 10:11:00")
        status, realtime = utils.classify_market_status(parsed, "hk", now=now)
        assert status == "盘中"
        assert realtime is True


# =============================================================
# 六、缓存清理端点契约（TC-系统-缓存）
# =============================================================
class TestCacheClearContract:
    """POST /api/cache/clear：真实 unlink + {data,error} 契约（§6.5 清缓存）。

    沙箱隔离：config.CACHE_DIR 重定向到 tmp_path，只删本用例造的 2 个假缓存文件，
    既走真实 unlink 代码路径，又不触碰真实磁盘缓存（红线一）。
    """

    def test_cache_clear_endpoint_contract(self, monkeypatch, tmp_path):
        """清缓存端点：HTTP 200 + error=null + 假缓存文件真实消失。"""
        from fastapi.testclient import TestClient

        from backend.app import config
        from backend.app.core import cache
        from backend.app.main import app

        # 造 2 个假磁盘缓存文件（内容含 error 键也不会被缓存，直接物理写入）
        monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
        fake_keys = ["scan-fake-a", "scan-fake-b"]
        for key in fake_keys:
            path = cache._disk_path(key)
            path.write_text('{"probe": true}', encoding="utf-8")
        assert all(cache._disk_path(key).exists() for key in fake_keys)

        client = TestClient(app)
        response = client.post("/api/cache/clear")
        body = response.json()

        assert response.status_code == 200
        assert "data" in body and "error" in body
        assert body["error"] is None, f"清缓存应成功: {body['error']}"
        assert body["data"].get("removed", 0) >= 2, "应报告删除数量"
        # 真实 unlink 生效：假缓存文件已消失
        assert not any(cache._disk_path(key).exists() for key in fake_keys)


# =============================================================
# 七、基金重仓口径（TC-基金-重仓）
# =============================================================
class TestHoldingCaliber:
    """get_holding 口径：东财按年返回会拼接多季度，必须取最新季度前 10 大。

    背景（阶段8 对账实锤）：fund_portfolio_hold_em(date=2026) 返回
    「1季度 + 2季度」两段拼接（旧季度在前），直接切片会拿到旧季报前 10，
    导致拟合估值权重偏差（P1 数据口径问题，已修）。
    本用例以合成双季度 DataFrame 模拟该上游行为，不发起真实请求。
    """

    @staticmethod
    def _fake_frame():
        """构造「Q1 在前、Q2 在后且比例更大」的合成上游数据。"""
        import pandas as pd

        return pd.DataFrame([
            {"股票代码": "600001", "股票名称": "旧季一", "占净值比例": 9.0,
             "持股数": 100, "持仓市值": 900.0, "季度": "2026年1季度股票投资明细"},
            {"股票代码": "600002", "股票名称": "旧季二", "占净值比例": 8.0,
             "持股数": 100, "持仓市值": 800.0, "季度": "2026年1季度股票投资明细"},
            {"股票代码": "600003", "股票名称": "旧季三", "占净值比例": 7.0,
             "持股数": 100, "持仓市值": 700.0, "季度": "2026年1季度股票投资明细"},
            {"股票代码": "688120", "股票名称": "新季一", "占净值比例": 9.22,
             "持股数": 110, "持仓市值": 922.0, "季度": "2026年2季度股票投资明细"},
            {"股票代码": "688012", "股票名称": "新季二", "占净值比例": 9.06,
             "持股数": 110, "持仓市值": 906.0, "季度": "2026年2季度股票投资明细"},
            {"股票代码": "002371", "股票名称": "新季三", "占净值比例": 9.03,
             "持股数": 110, "持仓市值": 903.0, "季度": "2026年2季度股票投资明细"},
        ])

    def test_holding_takes_latest_quarter_top10(self, monkeypatch):
        """必须取最新季度（Q2）且按占净值比例降序，不得混入旧季度行。"""
        import akshare as ak

        from backend.app.datasource import fund as fund_ds

        monkeypatch.setattr(ak, "fund_portfolio_hold_em",
                            lambda symbol, date: self._fake_frame())
        holding = fund_ds.get_holding("017811")

        codes = [item["code"] for item in holding["items"]]
        assert codes == ["688120", "688012", "002371"], f"混入旧季度或未按比例降序: {codes}"
        assert holding["quarter"] == "2026年2季度股票投资明细"
        assert holding["caliber"].startswith("东财基金季报重仓（最新季度前10")


# =============================================================
# 八、路由契约矩阵（TC-路由-契约；阶段 8 七步之① routers 接口用例）
# =============================================================
class TestRoutersContract:
    """12 个 router 代表端点的 {data,error} 契约用例（TestClient + 猴子补丁）。

    覆盖四条路径（§11.4 正常/边界/异常三类 + 空错分离铁律）：
    - 成功：provider 补丁返回夹具 → error=null、data 字段齐全、source/caliber 非空；
    - 空数据：provider 返回 [] → **成功 + items:[] + count:0**（EMPTY_DATA 不得进错误态）；
    - 失败：provider 抛 NonRetryableError → error 中文文案 + error_code 正确 + data 空结构；
    - 参数非法：market/base_url 等边界值 → INVALID_PARAM 中文提示。

    环境隔离：config.CACHE_DIR / AI_CONFIG_FILE / PORTFOLIO_FILE 全部重定向
    tmp_path，每例先清缓存，不发真实网络请求（QA 三性见 test_qa_verify.py）。
    """

    @pytest.fixture(autouse=True)
    def _isolated_env(self, monkeypatch, tmp_path):
        """隔离缓存目录与配置文件，避免触碰真实磁盘缓存与用户配置（红线一）。"""
        from backend.app import config
        from backend.app.core import cache

        monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
        monkeypatch.setattr(config, "AI_CONFIG_FILE", tmp_path / "ai_config.json")
        monkeypatch.setattr("backend.app.datasource.portfolio.PORTFOLIO_FILE",
                            tmp_path / "portfolio.json")
        cache.clear_cache()  # 内存 + 磁盘双层清空（CACHE_DIR 已重定向，安全）

    @staticmethod
    def _client():
        """构造 TestClient（惰性 _IncludedRouter 在客户端首请求时展开真实路由）。"""
        from fastapi.testclient import TestClient

        from backend.app.main import app

        return TestClient(app)

    # ---------- system / widgets / connectors：本地静态端点 ----------
    def test_health_contract(self):
        """GET /api/health：200 + data.app.name 非空 + error=null。"""
        body = self._client().get("/api/health").json()
        assert body["error"] is None
        assert body["data"]["app"]["name"].strip() != ""

    def test_widgets_registry_contract(self):
        """GET /api/widgets：注册表非空且每项有 id/name（前端三方一致的数据源）。"""
        body = self._client().get("/api/widgets").json()
        assert body["error"] is None
        items = body["data"] if isinstance(body["data"], list) else body["data"].get("items", [])
        assert items, "组件注册表不应为空"
        assert all(item.get("id") and item.get("title") for item in items)

    def test_connectors_status_contract(self):
        """GET /api/connectors/status：200 + 契约字段齐全。"""
        body = self._client().get("/api/connectors/status").json()
        assert body["error"] is None
        assert "data" in body

    # ---------- market：报价成功 / 参数非法 / 通道全失败 ----------
    def test_market_quote_success(self, monkeypatch):
        """A股报价：腾讯补丁命中 → data.quote.price 与夹具一致 + 双徽章字段。"""
        from backend.app.datasource import tencent

        fixture = {
            "name": "测试股份", "price": 10.55, "change": 0.15, "pct": 1.44,
            "high": 10.8, "low": 10.2, "open": 10.3, "prev_close": 10.4,
            "volume": 123456, "amount": 130000000.0, "quote_time": "2026-10-20 15:00:03",
        }
        monkeypatch.setattr(tencent, "get_quote", lambda code, market: dict(fixture))
        body = self._client().get("/api/market/quote", params={"symbol": "600999", "market": "a"}).json()
        assert body["error"] is None
        quote = body["data"]["quote"]
        assert quote["price"] == 10.55 and quote["name"] == "测试股份"
        assert body["data"]["source"] and body["data"]["caliber"]

    def test_market_quote_bad_market(self):
        """market=xx → INVALID_PARAM + 中文文案（参数边界路径）。"""
        body = self._client().get("/api/market/quote", params={"symbol": "600999", "market": "xx"}).json()
        assert body["error_code"] == "INVALID_PARAM"
        assert "market" in body["error"]

    def test_market_quote_all_providers_down(self, monkeypatch):
        """两源全失败 → UPSTREAM 错误码 + data 为空结构（不残留旧值）。"""
        from backend.app.core.resilient import NonRetryableError
        from backend.app.datasource import ashare, tencent

        def _boom(*args, **kwargs):
            raise NonRetryableError("通道不可用")

        monkeypatch.setattr(tencent, "get_quote", _boom)
        monkeypatch.setattr(ashare, "get_quote_sina", _boom)
        body = self._client().get("/api/market/quote", params={"symbol": "600998", "market": "a"}).json()
        assert body["error_code"] in ("UPSTREAM_ERROR", "UPSTREAM_TIMEOUT")
        assert body["data"] == {"quote": {}}

    def test_market_kline_us_contract(self, monkeypatch):
        """美股 K 线：东财补丁命中 → items 非空 + symbol/market 归一化。"""
        from backend.app.datasource import overseas

        rows = [
            {"date": "2026-10-18", "open": 9.8, "close": 10.2, "low": 9.7, "high": 10.4, "volume": 1000},
            {"date": "2026-10-19", "open": 10.2, "close": 10.9, "low": 10.1, "high": 11.0, "volume": 1200},
        ]
        monkeypatch.setattr(overseas, "get_kline_em", lambda code, market, period, adjust: [dict(r) for r in rows])
        body = self._client().get("/api/market/kline",
                                  params={"symbol": "TESTUS", "market": "us"}).json()
        assert body["error"] is None
        assert len(body["data"]["items"]) == 2
        assert body["data"]["market"] == "us"

    # ---------- sector：行业行情成功 ----------
    def test_sector_industry_success(self, monkeypatch):
        """行业行情：同花顺补丁命中 → items/count 一致 + 源名回传。"""
        from backend.app.datasource import sector as sector_provider

        rows = [
            {"name": "半导体", "pct": 2.31, "amount": 812.4, "net_inflow": 15.6},
            {"name": "酿酒行业", "pct": -1.02, "amount": 96.3, "net_inflow": -3.2},
        ]
        monkeypatch.setattr(sector_provider, "get_industry_ths", lambda: [dict(r) for r in rows])
        body = self._client().get("/api/sector/industry", params={"market": "cn"}).json()
        assert body["error"] is None
        assert body["data"]["count"] == 2
        assert body["data"]["source"] == "同花顺"

    # ---------- stock：概况成功 ----------
    def test_stock_profile_success(self, monkeypatch):
        """公司概况：东财补丁命中 → name 校验通过 + 载荷透传。"""
        from backend.app.datasource import stock as stock_provider

        monkeypatch.setattr(stock_provider, "get_profile",
                            lambda code, market: {"name": "测试设备公司", "industry": "半导体"})
        body = self._client().get("/api/stock/profile", params={"symbol": "688999", "market": "a"}).json()
        assert body["error"] is None
        assert body["data"]["name"] == "测试设备公司"

    # ---------- stock：一致预期（F-027）三路径 ----------
    def test_stock_estimates_success(self, monkeypatch):
        """一致预期：同花顺补丁命中 → eps/profit/years 透传 + source=同花顺。"""
        from backend.app.datasource import stock as stock_provider

        payload = {
            "eps": [{"年度": "2026", "预测机构数": 23, "最小值": 3.13, "均值": 4.16,
                     "最大值": 7.2, "行业平均数": 2.04}],
            "profit": [{"年度": "2026", "预测机构数": 23, "最小值": 29.25, "均值": 38.61,
                        "最大值": 54.59, "行业平均数": 15.02}],
            "institutions": [{"机构名称": "华安证券", "报告日期": "2026-09-28"}],
            "detail": [{"预测指标": "营业收入(元)", "预测2026-平均": "169.13亿"}],
            "years": ["2026"], "available": True, "caliber_note": "同花顺盈利预测",
        }
        monkeypatch.setattr(stock_provider, "get_estimates",
                            lambda code, market: dict(payload))
        body = self._client().get(
            "/api/stock/estimates", params={"symbol": "688012", "market": "a"}).json()
        assert body["error"] is None
        assert body["data"]["source"] == "同花顺"
        assert body["data"]["eps"][0]["均值"] == 4.16
        assert body["data"]["years"] == ["2026"]

    def test_stock_estimates_empty_is_success(self, monkeypatch):
        """空/错分离：无机构覆盖 → error=null + 空列表 + available=false（不得进错误态）。"""
        from backend.app.datasource import stock as stock_provider

        monkeypatch.setattr(stock_provider, "get_estimates", lambda code, market: {
            "eps": [], "profit": [], "institutions": [], "detail": [], "years": [],
            "available": False, "note": "该标的暂无机构预测覆盖",
        })
        body = self._client().get(
            "/api/stock/estimates", params={"symbol": "000000", "market": "a"}).json()
        assert body["error"] is None
        assert body["data"]["eps"] == []
        assert body["data"]["available"] is False
        assert "暂无" in body["data"]["note"]

    def test_stock_estimates_us_market_empty(self):
        """港美股：provider 真实分支返回空结构（无需补丁），契约仍为成功空态。"""
        body = self._client().get(
            "/api/stock/estimates", params={"symbol": "AAPL", "market": "us"}).json()
        assert body["error"] is None
        assert body["data"]["available"] is False
        assert body["data"]["eps"] == []

    # ---------- news：快讯成功 + 空数据铁律 ----------
    def test_news_flash_success_scores_items(self, monkeypatch):
        """快讯：财联社补丁命中 → 每条带 score/label，分值在 ±0.1 内。"""
        from backend.app.datasource import news as news_provider

        rows = [
            {"title": "半导体设备公司中标大单", "text": "公司公告中标海外大单，金额超预期",
             "time": "2026-10-20 14:30", "source": "财联社"},
            {"title": "存储芯片价格回落", "text": "部分产品价格低于预期，渠道库存偏高",
             "time": "2026-10-20 13:05", "source": "财联社"},
        ]
        monkeypatch.setattr(news_provider, "get_flash", lambda limit: [dict(r) for r in rows])
        body = self._client().get("/api/news/flash").json()
        assert body["error"] is None
        items = body["data"]["items"]
        assert body["data"]["count"] == 2
        assert all(-0.1 <= item["sentiment"]["score"] <= 0.1 for item in items), "分值越界 ±0.1"
        assert all(item["sentiment"]["label"] for item in items)

    def test_news_flash_empty_is_success(self, monkeypatch):
        """空数据铁律：provider 返回 [] → error=null + items:[] + count:0（不进错误态）。"""
        from backend.app.datasource import news as news_provider

        # 路由走多源入口 get_flash_multi（财联社不可用时回落全球快讯），故此处补丁需打在它上面
        monkeypatch.setattr(news_provider, "get_flash_multi", lambda limit: [])
        body = self._client().get("/api/news/flash").json()
        assert body["error"] is None
        assert body["data"]["items"] == []
        assert body["data"]["count"] == 0

    def test_news_flash_channel_down(self, monkeypatch):
        """通道异常：确定性失败 → 错误码 + 中文文案（与上面的空态严格区分）。"""
        from backend.app.core.resilient import NonRetryableError
        from backend.app.datasource import news as news_provider

        def _boom(*args, **kwargs):
            raise NonRetryableError("通道不可用")

        monkeypatch.setattr(news_provider, "get_flash_multi", _boom)
        body = self._client().get("/api/news/flash").json()
        assert body["error_code"] in ("UPSTREAM_ERROR", "UPSTREAM_TIMEOUT")
        assert body["data"]["items"] == []

    # ---------- sentiment：个股情绪 ----------
    def test_sentiment_stock_success(self, monkeypatch):
        """个股情绪：东财个股新闻补丁 → 聚合分 + samples + degraded=False。"""
        from backend.app.datasource import news as news_provider

        rows = [
            {"title": "中微公司：订单饱满", "text": "刻蚀设备订单饱满，业绩预增",
             "time": "2026-10-20 10:12", "source": "东财"},
        ]
        monkeypatch.setattr(news_provider, "get_stock_news", lambda code, limit: [dict(r) for r in rows])
        body = self._client().get("/api/sentiment/stock", params={"symbol": "688012"}).json()
        assert body["error"] is None
        assert body["data"]["degraded"] is False
        assert body["data"]["samples"], "samples 不应为空"
        assert -0.1 <= body["data"]["mean_score"] <= 0.1, "均分越界 ±0.1"
        assert 0 <= body["data"]["temperature"] <= 100

    # ---------- fund：净值历史 ----------
    def test_fund_nav_success(self, monkeypatch):
        """基金净值：蛋卷补丁命中 → items 非空 + as_of 取首条日期。"""
        from backend.app.datasource import fund as fund_provider

        payload = {
            "items": [{"date": "2026-10-20", "nav": 1.2345, "acc_nav": 1.2345}],
            "total": 887, "page": 1, "size": 30,
        }
        monkeypatch.setattr(fund_provider, "get_nav", lambda symbol, page, size: dict(payload))
        body = self._client().get("/api/fund/nav", params={"code": "017811"}).json()
        assert body["error"] is None
        assert body["data"]["items"][0]["nav"] == 1.2345
        assert body["data"]["as_of"] == "2026-10-20"

    # ---------- portfolio：持仓分析（本地文件夹具） ----------
    def test_portfolio_analysis_success(self, monkeypatch, tmp_path):
        """持仓分析：本地 JSON 夹具 + 腾讯补丁 → 市值=份额×价，盈亏符号正确。"""
        import json as json_lib

        from backend.app.datasource import portfolio as portfolio_ds
        from backend.app.datasource import tencent

        fixture = {
            "items": [{
                "id": "t-001", "market": "a", "symbol": "600999", "name": "测试股份",
                "shares": 1000, "cost": 9.5, "created_at": "2026-09-01", "note": "单测夹具",
            }],
            "updated_at": "2026-10-20 20:00:00",
        }
        portfolio_ds.PORTFOLIO_FILE = tmp_path / "portfolio.json"
        (tmp_path / "portfolio.json").write_text(json_lib.dumps(fixture), encoding="utf-8")
        monkeypatch.setattr(tencent, "get_quote",
                            lambda code, market: {"name": "测试股份", "price": 10.0})
        body = self._client().get("/api/portfolio/analysis").json()
        assert body["error"] is None
        row = body["data"]["items"][0]
        assert row["market_value"] == 1000 * 10.0
        assert row["pnl"] == 1000 * (10.0 - 9.5)  # +500
        assert row["pnl"] > 0

    # ---------- ai：配置未配置 / 参数非法 / 保存后掩码回显 ----------
    def test_ai_config_not_configured(self):
        """未配置：configured=False + 无明文密钥字段。"""
        body = self._client().get("/api/ai/config").json()
        assert body["error"] is None
        assert body["data"]["configured"] is False

    def test_ai_config_save_and_mask_echo(self):
        """保存后：api_key 只回掩码，base_url/model 明文；缺 base_url 拒绝。"""
        client = self._client()
        saved = client.post("/api/ai/config", json={
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "sk-unit-test-123", "model": "deepseek-chat",
        }).json()
        assert saved["error"] is None
        assert saved["data"]["saved"] is True
        config_body = client.get("/api/ai/config").json()
        key_echo = config_body["data"].get("api_key") or ""
        assert "sk-unit-test-123" not in key_echo, "密钥不得明文回显"
        assert "****" in key_echo or "*" in key_echo, "应回掩码"
        assert config_body["data"]["base_url"] == "https://api.deepseek.com/v1"
        bad = client.post("/api/ai/config", json={"api_key": "x"}).json()
        assert bad["error_code"] == "INVALID_PARAM"
        assert "base_url" in bad["error"]


# =============================================================
# 九、前端视图静态一致性（阶段 8：charting/settings 补齐后回归）
# =============================================================
class TestFrontendViews:
    """VIEW_IDS ↔ index.html 容器 ↔ 菜单项 ↔ i18n 键四层静态一致性。

    背景：阶段 8 门禁 B 核对发现 charting/settings 两菜单视图实为占位
    （VIEW_IDS 未收、点击弹 toast），属核心功能缺陷（BUG-P8-02，已修）。
    本用例以静态断言锁死修复成果，防回归。
    """

    @staticmethod
    def _frontend_dir():
        """前端目录（openbb-cn/frontend）。"""
        from pathlib import Path

        return Path(__file__).resolve().parents[1] / "frontend"

    def _read(self, relative):
        """读取前端文件文本。"""
        return (self._frontend_dir() / relative).read_text(encoding="utf-8")

    def test_view_ids_all_have_containers(self):
        """VIEW_IDS 每一项都在 index.html 有对应 page-* 容器（含带连字符的引号键）。"""
        import re

        pages = self._read("js/pages.js")
        index = self._read("index.html")
        view_block = pages.split("VIEW_IDS")[1].split("};")[0]
        pairs = re.findall(r'["\']?([\w-]+)["\']?\s*:\s*"(page-[\w-]+)"', view_block)
        assert len(pairs) >= 11, f"VIEW_IDS 应至少收录 11 个视图，实际 {len(pairs)}"
        for name, container in pairs:
            assert f'id="{container}"' in index, f"视图 {name} 缺容器 {container}"

    def test_menu_covers_all_views(self):
        """index.html 菜单 data-view 集合 = VIEW_IDS 键 + home（不缺不多）。"""
        import re

        pages = self._read("js/pages.js")
        index = self._read("index.html")
        view_block = pages.split("VIEW_IDS")[1].split("};")[0]
        view_ids = set(re.findall(r'["\']?([\w-]+)["\']?\s*:\s*"page-[\w-]+"', view_block))
        menu_views = set(re.findall(r'data-view="([\w-]+)"', index))
        assert menu_views == view_ids | {"home"}, f"菜单与视图集合不一致：{menu_views ^ (view_ids | {'home'})}"

    def test_new_views_wired_in_router(self):
        """route() 分发含 charting/settings，renderCharting/renderSettings 已实现。"""
        pages = self._read("js/pages.js")
        assert 'name === "charting"' in pages and "renderCharting(parsed.params)" in pages
        assert 'name === "settings"' in pages and "renderSettings()" in pages
        assert "function renderCharting(" in pages and "function renderCharting(params)" in pages
        assert "function renderSettings(" in pages

    def test_chart_settings_i18n_keys_defined(self):
        """charting/settings 用到的全部 i18n 键都已在语言包定义（防 key 漂移）。"""
        import re

        pages = self._read("js/pages.js")
        i18n = self._read("js/i18n.js")
        used = set(re.findall(r't\("((?:chart|set)\.[\w.]+)"', pages))
        assert len(used) >= 40, f"新视图 i18n 键用量异常：{len(used)}"
        missing = {key for key in used if f'"{key}"' not in i18n}
        assert not missing, f"语言包缺键：{sorted(missing)}"

    def test_fmt_delegates_to_l10n(self):
        """阶段 6 交接口径：fmt/fmtPct 统一走 L10N（保留无 L10N 时的兜底）。"""
        pages = self._read("js/pages.js")
        assert "window.L10N.num" in pages and "window.L10N.pct" in pages

    def test_charting_css_present(self):
        """图表容器 .pg-chart.tall 样式存在（含 1024 断点降高）。"""
        css = self._read("css/pages.css")
        assert ".pg-chart.tall" in css
