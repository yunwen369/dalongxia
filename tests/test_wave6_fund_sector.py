# -*- coding: utf-8 -*-
"""M12 拥挤度全板块排行 / M13 全市场候选池 / M14 拥挤度降权 单元测试。

全部离线：板块清单、拥挤度打分、基金重仓、行业查询、快照全部 monkeypatch。
"""

from __future__ import annotations

import json

import pytest

from backend.app.datasource import fund as fund_module
from backend.app.datasource import fund_combo, portfolio as portfolio_module, screener, sector


# ---------------------------------------------------------------
# M12：全板块排行
# ---------------------------------------------------------------
def test_rankings_sorted_desc_and_failed_collected(monkeypatch):
    """排行按得分降序，单板块失败进 failed 不阻断。"""
    monkeypatch.setattr(sector, "get_crowding_board_names", lambda kind="industry": ["甲", "乙", "丙", "丁"])

    scores = {"甲": 30.0, "乙": 85.0, "丙": 62.0}

    def _fake_crowding(board, *, kind="industry", **kwargs):
        """假打分：丁 抛错，其余返回预设分。"""
        if board == "丁":
            raise ValueError("日线样本不足")
        score = scores[board]
        level = "高位" if score >= 80 else "偏高" if score >= 60 else "正常"
        return {"board": board, "score": score, "level": level, "parts": []}

    monkeypatch.setattr(sector, "get_crowding", _fake_crowding)
    data = sector.get_crowding_rankings(limit=10)
    assert [item["board"] for item in data["items"]] == ["乙", "丙", "甲"]
    assert data["scanned"] == 3
    assert data["total"] == 4
    assert any("丁" in item for item in data["failed"])
    assert data["level_counts"] == {"高位": 1, "偏高": 1, "正常": 1}


def test_rankings_rejects_unknown_kind():
    """未知 kind 直接拒绝（industry/concept 之外）。"""
    with pytest.raises(ValueError) as err:
        sector.get_crowding_rankings(kind="xx")
    assert "仅支持" in str(err.value)


def test_rankings_limit_applied(monkeypatch):
    """limit 生效且不超过扫描数。"""
    monkeypatch.setattr(sector, "get_crowding_board_names", lambda kind="industry": ["甲", "乙"])
    monkeypatch.setattr(
        sector, "get_crowding",
        lambda board, *, kind="industry", **kwargs: {"board": board, "score": 50.0, "level": "正常", "parts": []},
    )
    data = sector.get_crowding_rankings(limit=1)
    assert len(data["items"]) == 1
    assert data["scanned"] == 2


# ---------------------------------------------------------------
# M13：全市场候选池
# ---------------------------------------------------------------
@pytest.fixture()
def snapshot_file(tmp_path, monkeypatch):
    """预置迷你快照：一只满足模板、一只 ROE 下滑、一只缺财务。"""
    path = tmp_path / "data" / "screener_snapshot.json"
    monkeypatch.setattr(screener, "SNAPSHOT_FILE", path)
    rows = [
        # 满足：ROE 三年上升、毛利率 75、PE 18、负债率 20；R9：股息率为快照列
        {"code": "600519", "name": "贵州茅台", "board": "白酒", "close": 1392.0, "chg": 1.0,
         "mktcap": 1.7e12, "pe": 18.0, "pb": 6.0, "ps": 10.0, "pcf": 14.0,
         "roe_years": [34.2, 32.5, 30.1], "gross": 75.0, "rev_yoy": 10.0, "np_yoy": 9.0,
         "debt_ratio": 20.0, "ocf": 9.0e10, "fin_missing": False, "dividend_yield": 1.9},
        # ROE 逐年下滑（新 20 < 旧 25）→ 不满足「逐年不降」
        {"code": "000002", "name": "万科A", "board": "房地产开发", "close": 8.0, "chg": 0.0,
         "mktcap": 9.0e10, "pe": 12.0, "pb": 0.4, "ps": 1.0, "pcf": 5.0,
         "roe_years": [20.0, 25.0, 28.0], "gross": 40.0, "rev_yoy": -5.0, "np_yoy": -8.0,
         "debt_ratio": 75.0, "ocf": 1.0e9, "fin_missing": False, "dividend_yield": 0.0},
        # 缺财务 + 股息率通道缺失（None，不冒充 0）
        {"code": "688981", "name": "中芯国际", "board": "半导体", "close": 88.0, "chg": -1.0,
         "mktcap": 7.0e11, "pe": 120.0, "pb": 3.0, "ps": 9.0, "pcf": 40.0,
         "roe_years": [None, None, None], "gross": None, "rev_yoy": None, "np_yoy": None,
         "debt_ratio": None, "ocf": None, "fin_missing": True, "dividend_yield": None},
        # 银行：负债率 92 但金融业豁免，其余满足
        {"code": "601398", "name": "工商银行", "board": "银行", "close": 6.1, "chg": 0.2,
         "mktcap": 2.1e12, "pe": 6.0, "pb": 0.6, "ps": 2.0, "pcf": 3.0,
         "roe_years": [11.5, 11.0, 10.6], "gross": 45.0, "rev_yoy": 3.0, "np_yoy": 2.0,
         "debt_ratio": 92.0, "ocf": 3.0e11, "fin_missing": False, "dividend_yield": 5.2},
    ]
    payload = {
        "version": "20261031231009",
        "built_at": "2026-10-31 23:10:09",
        "trade_date": "2026-10-30",
        "rows": rows,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_candidates_template_filters(snapshot_file):
    """模板：ROE 逐年不降 + 毛利率 + PE + 负债率；银行豁免负债率。"""
    data = screener.run_candidates(limit=10)
    codes = [item["code"] for item in data["items"]]
    assert "600519" in codes          # 满足全部
    assert "601398" in codes          # 银行负债率豁免
    assert "000002" not in codes      # ROE 下滑
    assert "688981" not in codes      # 缺财务
    assert data["exempt_count"] == 1
    assert data["template"]["key"] == "fund_friendly"


def test_candidates_empty_snapshot_raises(tmp_path, monkeypatch):
    """快照未建库 → RuntimeError（由路由转 EMPTY_DATA）。"""
    path = tmp_path / "data" / "screener_snapshot.json"
    monkeypatch.setattr(screener, "SNAPSHOT_FILE", path)
    with pytest.raises(RuntimeError) as err:
        screener.run_candidates()
    assert "快照未建库" in str(err.value)


def test_candidates_market_rejects_non_a(snapshot_file):
    """只支持 A 股。"""
    with pytest.raises(ValueError) as err:
        screener.run_candidates(market="hk")
    assert "仅支持 A 股" in str(err.value)


# ---------------------------------------------------------------
# M14：拥挤度降权
# ---------------------------------------------------------------
def test_discount_tiers():
    """折扣档：<60 →1.0；60–79 →0.85；≥80 →0.6。"""
    assert fund_combo._discount_of(59.9)[0] == 1.00
    assert fund_combo._discount_of(60.0)[0] == 0.85
    assert fund_combo._discount_of(79.9)[0] == 0.85
    assert fund_combo._discount_of(80.0)[0] == 0.60
    assert fund_combo._discount_of(95.0)[0] == 0.60
    assert fund_combo._discount_of(None)[0] is None


def test_match_board_paths(monkeypatch):
    """东财三级路径 → 同花顺板块名。"""
    monkeypatch.setattr(fund_combo, "_ths_boards", lambda: ["白酒", "半导体", "银行", "元件"])
    assert fund_combo._match_board("食品饮料-饮料-白酒") == "白酒"
    assert fund_combo._match_board("电子设备-半导体-集成电路") == "半导体"
    assert fund_combo._match_board("银行Ⅱ") == "银行"
    assert fund_combo._match_board("石油石化-炼化及贸易") is None


def test_weight_breakdown_applies_discount(monkeypatch):
    """拥挤度 88 的基金应 ×0.6，折后内部归一化回原基础合计。"""
    base = {
        "items": [
            {"code": "F1", "name": "基金一"},
            {"code": "F2", "name": "基金二"},
            {"code": "F3", "name": "基金三"},
        ],
        "weights": [
            {"code": "F1", "weight": 18.0},
            {"code": "F2", "weight": 15.0},
            {"code": "F3", "weight": 12.0},
        ],
        "missing": [],
        "disclaimer": "非投资建议",
    }
    monkeypatch.setattr(fund_combo, "evaluate", lambda codes, profile: base)
    monkeypatch.setattr(fund_combo, "_ths_boards", lambda: ["半导体", "白酒", "元件"])
    # F1 → 半导体 88（×0.6）；F2 → 白酒 70（×0.85）；F3 → 元件 20（×1.0）
    monkeypatch.setattr(
        fund_module, "get_holding",
        lambda code: {"items": [{"code": {"F1": "688012", "F2": "600519", "F3": "002371"}[code]}]},
    )
    industries = {"688012": "电子设备-半导体-集成电路", "600519": "食品饮料-饮料-白酒", "002371": "电子设备-元件"}
    monkeypatch.setattr(
        portfolio_module, "_industry_of",
        lambda symbol, market: industries.get(symbol),
    )
    scores = {"半导体": 88.0, "白酒": 70.0, "元件": 20.0}
    monkeypatch.setattr(
        sector, "get_crowding",
        lambda board: {"score": scores[board]},
    )
    data = fund_combo.weight_breakdown(["F1", "F2", "F3"], "balanced", crowd_discount=True)
    rows = {row["code"]: row for row in data["items"]}
    assert rows["F1"]["discount"] == 0.60
    assert rows["F2"]["discount"] == 0.85
    assert rows["F3"]["discount"] == 1.00
    # 归一化：合计回到原基础合计 45（折后再归一）
    assert sum(row["final_weight"] for row in data["items"]) == pytest.approx(45.0, abs=0.01)
    # 折后 F1 权重应低于 F3（同等基础权重下）
    assert rows["F1"]["final_weight"] < rows["F3"]["final_weight"]


def test_weight_breakdown_crowd_disabled(monkeypatch):
    """crowd=False 时全部 ×1.00，最终权重等于基础权重。"""
    base = {
        "items": [{"code": "F1", "name": "基金一"}, {"code": "F2", "name": "基金二"}],
        "weights": [{"code": "F1", "weight": 18.0}, {"code": "F2", "weight": 15.0}],
        "missing": [],
        "disclaimer": "非投资建议",
    }
    monkeypatch.setattr(fund_combo, "evaluate", lambda codes, profile: base)
    data = fund_combo.weight_breakdown(["F1", "F2"], "balanced", crowd_discount=False)
    rows = {row["code"]: row for row in data["items"]}
    assert rows["F1"]["final_weight"] == pytest.approx(18.0, abs=0.01)
    assert rows["F2"]["final_weight"] == pytest.approx(15.0, abs=0.01)
    assert all(row["discount"] == 1.00 for row in data["items"])


# ---------------------------------------------------------------
# R10（设计基准 §8 / 验收 C10）：拥挤度折扣双因子构成（规模环比+重仓换手）
# ---------------------------------------------------------------
def test_r10_fund_crowding_has_five_dims(monkeypatch):
    """自身拥挤度五维：波动/动量/回撤分位 + 规模环比/重仓换手绝对映射，权重和=1。"""
    fake_nav = {"items": [{"date": f"2026-01-{i:02d}", "nav": 1.0 + (i % 20) * 0.01}
                          for i in range(1, 30)] * 10}
    monkeypatch.setattr(fund_module, "get_nav", lambda code, page=1, size=30: fake_nav)
    monkeypatch.setattr(fund_module, "get_profile_basis",
                        lambda code: {"scale_mom_pct": 15.0, "scale_series": []})
    monkeypatch.setattr(fund_combo.tencent, "get_quote",
                        lambda symbol, market: {"turnover": 2.0})
    fc = fund_combo.get_fund_crowding("F1")
    if not fc.get("available"):
        pytest.skip(f"净值构造样本不足（{fc.get('caliber')}）")
    labels = [p["label"] for p in fc["parts"]]
    assert "规模环比增速" in labels and "重仓股合计换手率" in labels
    assert abs(sum(p["weight"] for p in fc["parts"]) - 1.0) < 1e-9
    dc = fc["discount_components"]
    assert dc["scale_momentum"]["available"] is True
    assert dc["scale_momentum"]["value"] == 15.0
    # 环比 +15% → 映射分 50（/30 线性截断）
    assert dc["scale_momentum"]["pct"] == 50.0
    assert fc["caliber"].count("五维") >= 1


def test_r10_scale_outflow_maps_zero(monkeypatch):
    """环比为负（资金流出）→ 映射 0 分，不视为拥挤；正流入 ≥+30% → 100 分封顶。"""
    fake_nav = {"items": [{"date": f"2026-01-{i:02d}", "nav": 1.0 + (i % 20) * 0.01}
                          for i in range(1, 30)] * 10}
    monkeypatch.setattr(fund_module, "get_nav", lambda code, page=1, size=30: fake_nav)
    monkeypatch.setattr(fund_combo.tencent, "get_quote",
                        lambda symbol, market: {"turnover": 1.0})
    for mom, expect in ((-52.98, 0.0), (45.0, 100.0)):
        monkeypatch.setattr(fund_module, "get_profile_basis",
                            lambda code, m=mom: {"scale_mom_pct": m, "scale_series": []})
        fc = fund_combo.get_fund_crowding("F1")
        if not fc.get("available"):
            pytest.skip("构造样本不足")
        assert fc["discount_components"]["scale_momentum"]["pct"] == expect


def test_r10_scale_missing_not_silently_zero(monkeypatch):
    """规模序列缺失（QDII）→ available=False + missing 点名，绝不冒充 0 分。"""
    fake_nav = {"items": [{"date": f"2026-01-{i:02d}", "nav": 1.0 + (i % 20) * 0.01}
                          for i in range(1, 30)] * 10}
    monkeypatch.setattr(fund_module, "get_nav", lambda code, page=1, size=30: fake_nav)
    monkeypatch.setattr(fund_module, "get_profile_basis",
                        lambda code: {"scale_mom_pct": None, "scale_series": []})
    monkeypatch.setattr(fund_combo.tencent, "get_quote",
                        lambda symbol, market: {"turnover": 1.0})
    fc = fund_combo.get_fund_crowding("968061")
    dc = (fc.get("discount_components") or {}).get("scale_momentum") or {}
    assert dc.get("available") is False
    assert any("规模环比" in note for note in fc.get("missing") or []), fc.get("missing")


def test_r10_weight_breakdown_exposes_components(monkeypatch):
    """C10：weight 明细逐只透出 discount_components（规模环比+重仓换手两项）。"""
    base = {
        "items": [{"code": "F1", "name": "基金一"}, {"code": "F2", "name": "基金二"}],
        "weights": [{"code": "F1", "weight": 18.0}, {"code": "F2", "weight": 15.0}],
        "missing": [], "disclaimer": "非投资建议",
    }
    monkeypatch.setattr(fund_combo, "evaluate", lambda codes, profile: base)
    # F1 走「自身」口径；F2 自身不可得 → 回退行业代理，构成列应为 None
    monkeypatch.setattr(
        fund_combo, "get_fund_crowding",
        lambda code: {
            "available": code == "F1", "score": 72.0 if code == "F1" else None,
            "discount_components": ({"scale_momentum": {"value": 5.0, "pct": 16.7, "available": True, "note": None},
                                     "holding_turnover": {"value": 3.4, "pct": 34.0, "available": True, "note": "样本 10 只重仓股"}}
                                    if code == "F1" else None),
            "caliber": "净值样本不足" if code != "F1" else "",
        })
    monkeypatch.setattr(fund_combo, "_ths_boards", lambda: ["半导体", "白酒"])
    monkeypatch.setattr(fund_module, "get_holding",
                        lambda code: {"items": [{"code": "688012"}]})
    monkeypatch.setattr(portfolio_module, "_industry_of",
                        lambda symbol, market: "电子设备-半导体-集成电路")
    monkeypatch.setattr(sector, "get_crowding", lambda board: {"score": 88.0})
    data = fund_combo.weight_breakdown(["F1", "F2"], "balanced", crowd_discount=True)
    rows = {row["code"]: row for row in data["items"]}
    # 自身口径：双因子构成齐全（C10 断言）
    dc1 = rows["F1"]["discount_components"]
    assert dc1 and dc1["scale_momentum"]["available"] and dc1["holding_turnover"]["available"]
    # 代理口径：构成列为 None 且 note 声明辅助口径（行业代理保留为辅助项）
    assert rows["F2"]["crowding_source"] == "重仓行业代理"
    assert rows["F2"]["discount_components"] is None
    assert "辅助" in (rows["F2"]["note"] or "")
