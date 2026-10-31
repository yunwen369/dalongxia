# -*- coding: utf-8 -*-
"""筛选器（M9）单元测试：条件解析 / AND 语义 / 金融业豁免 / 快照状态。

全部离线：快照文件 patch 到 tmp_path，网络函数 monkeypatch，
不触真实源站（§11.11 测试隔离）。
"""

from __future__ import annotations

import json

import pytest

from backend.app.datasource import screener


@pytest.fixture()
def snapshot_file(tmp_path, monkeypatch):
    """把快照文件指向用例级临时目录并预置一份迷你快照。"""
    path = tmp_path / "data" / "screener_snapshot.json"
    monkeypatch.setattr(screener, "SNAPSHOT_FILE", path)
    rows = [
        # 白酒：财务齐全
        {"code": "600519", "name": "贵州茅台", "board": "白酒", "close": 1392.0, "chg": 2.8,
         "mktcap": 1.74e12, "pe": 21.4, "pb": 6.4, "ps": 10.0, "pcf": 14.6,
         "roe_years": [34.2, 36.0, 32.5], "gross": 91.9, "rev_yoy": 15.6, "np_yoy": 14.1,
         "debt_ratio": 20.1, "ocf": 9.2e10, "fin_missing": False},
        # 银行：豁免样本（debt_ratio 有值但应被豁免）
        {"code": "601398", "name": "工商银行", "board": "银行", "close": 6.1, "chg": 0.3,
         "mktcap": 2.1e12, "pe": 6.2, "pb": 0.6, "ps": 2.1, "pcf": 3.8,
         "roe_years": [10.8, 11.2, 10.5], "gross": None, "rev_yoy": 2.1, "np_yoy": 1.8,
         "debt_ratio": 92.0, "ocf": 3.0e11, "fin_missing": False},
        # 缺财务（fin_missing）
        {"code": "688981", "name": "中芯国际", "board": "半导体", "close": 88.0, "chg": -1.2,
         "mktcap": 7.0e11, "pe": 120.0, "pb": 3.3, "ps": 9.5, "pcf": 40.0,
         "roe_years": [None, None, None], "gross": None, "rev_yoy": None, "np_yoy": None,
         "debt_ratio": None, "ocf": None, "fin_missing": True},
    ]
    payload = {
        "version": "20261031120000",
        "built_at": "2026-10-31 12:00:00",
        "reason": "manual",
        "trade_date": "2026-10-30",
        "quarter": "20260930",
        "annuals": ["20251231", "20241231", "20231231"],
        "elapsed": 45.0,
        "rows": rows,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    # 复位构建状态，避免跨用例污染
    monkeypatch.setattr(screener, "_BUILD_STATE",
                        {"running": False, "done": 0, "total": 0, "step": "idle", "last_error": None})
    return path


def test_run_basic_and_conditions(snapshot_file):
    """基础 AND 语义：多条件同时满足才命中。"""
    result = screener.run_screener([
        {"factor": "pe", "op": "lte", "value": 30},
        {"factor": "gross", "op": "gte", "value": 50},
    ])
    assert result["ready"] is True
    codes = [item["code"] for item in result["items"]]
    assert codes == ["600519"]  # 工行毛利率缺失不命中；中芯 PE 120 不命中


def test_run_finance_missing_value_excluded(snapshot_file):
    """财务因子缺值 → 不满足条件（不以 0 冒充，AC-X1）。"""
    result = screener.run_screener([{"factor": "rev_yoy", "op": "gte", "value": 0}])
    codes = [item["code"] for item in result["items"]]
    assert "688981" not in codes  # 财务缺失被排除
    assert "601398" in codes


def test_run_financial_debt_ratio_exempt(snapshot_file):
    """银行对资产负债率条件自动豁免并在行内标注（M7 同口径）。"""
    result = screener.run_screener([{"factor": "debt_ratio", "op": "lte", "value": 50}])
    codes = [item["code"] for item in result["items"]]
    # 工行负债率 92 本应不满足，但银行豁免 → 保留并标注
    assert "601398" in codes
    hit = next(item for item in result["items"] if item["code"] == "601398")
    assert "debt_ratio" in hit["exempt"]
    assert result["exempt_count"] >= 1
    # 中芯国际（非金融）财务缺失 → 不命中
    assert "688981" not in codes


def test_run_roe_min_three_years(snapshot_file):
    """ROE 连续 3 年：取最弱一年做比较；任一年缺值视为不满足。"""
    result = screener.run_screener([{"factor": "roe_min", "op": "gte", "value": 32.4}])
    codes = [item["code"] for item in result["items"]]
    assert "600519" in codes       # 最弱一年 32.5 ≥ 32.4
    assert "601398" not in codes   # 最弱 10.5
    assert "688981" not in codes   # 全 None


def test_run_invalid_condition(snapshot_file):
    """条件不合法 → ValueError（路由转 INVALID_PARAM）。"""
    with pytest.raises(ValueError):
        screener.run_screener([{"factor": "unknown_factor", "op": "gte", "value": 1}])
    with pytest.raises(ValueError):
        screener.run_screener([{"factor": "pe", "op": "between", "value": 1}])
    with pytest.raises(ValueError):
        screener.run_screener([{"factor": "pe", "op": "gte", "value": "abc"}])
    # 超过 8 条
    with pytest.raises(ValueError):
        screener.run_screener([{"factor": "pe", "op": "gte", "value": 1}] * 9)


def test_run_empty_snapshot_raises(tmp_path, monkeypatch):
    """快照未建库 → RuntimeError（路由转 EMPTY_DATA + 说明）。"""
    path = tmp_path / "data" / "none.json"
    monkeypatch.setattr(screener, "SNAPSHOT_FILE", path)
    with pytest.raises(RuntimeError):
        screener.run_screener([{"factor": "pe", "op": "lte", "value": 30}])


def test_snapshot_meta_and_mktcap_scaling(snapshot_file):
    """元信息就绪态 + 市值元→亿换算。"""
    meta = screener.get_snapshot_meta()
    assert meta["ready"] is True
    assert meta["row_count"] == 3
    assert meta["building"] is False
    assert meta["progress"]["step"] == "idle"
    result = screener.run_screener([{"factor": "mktcap", "op": "gte", "value": 10000}])
    hit = result["items"][0]
    assert hit["mktcap"] == pytest.approx(17400, rel=0.01)  # 1.74e12 元 → 17400 亿


def test_last_completed_quarter_boundary():
    """季末当天取上一季末（披露未齐全）。"""
    from datetime import date

    assert screener._last_completed_quarter(date(2026, 10, 31)) == "20260930"
    assert screener._last_completed_quarter(date(2026, 11, 2)) == "20260930"
    assert screener._last_completed_quarter(date(2026, 5, 20)) == "20260331"
    assert screener._last_completed_quarter(date(2026, 1, 1)) == "20251231"


def test_annual_reports_skips_future_disclosure():
    """年报期必须过滤尚未披露的当年 1231（否则该期整列缺值 → 全市场 ROE 判不满足）。"""
    from datetime import date

    # 2026-10-31：2026 年报未披露，应取 2025/2024/2023
    assert screener._annual_reports(date(2026, 10, 31)) == ["20251231", "20241231", "20231231"]
    # 2027-01-15：2026 年报仍未披露（4/30 前后才出），同样跳过
    assert screener._annual_reports(date(2027, 1, 15)) == ["20251231", "20241231", "20231231"]
    # 2027-05-15：2026 年报已披露，应取 2026/2025/2024
    assert screener._annual_reports(date(2027, 5, 15)) == ["20261231", "20251231", "20241231"]
