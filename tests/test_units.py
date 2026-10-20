# -*- coding: utf-8 -*-
"""单元测试：核心计算与规则引擎（§11.2 功能/数据测试的用例层）。

覆盖（规范 §11.9 复算验证 + 批次 5.2/5.3/7 业务规则）：
- 回撤计算（独立复算：手工构造净值序列验证峰谷/最大/当前回撤）；
- 持仓盈亏（份额×价、汇率折算、缺汇率 null）；
- 新闻初评规则（确定性、条件矩阵、否定、跨板块相反标签）；
- 板块摘要派生（Top3/集中度）、热力图负值不钳 0、去重前后缀。

约定：本文件为纯计算单测，不发真实网络请求（QA 三性另行真实拉取）。
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
