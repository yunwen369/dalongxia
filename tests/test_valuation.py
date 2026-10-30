# -*- coding: utf-8 -*-
"""估值分位模块（第七轮 M2）契约与口径测试。

重点验证三件事：
1. 分位算法正确（`percentile_of` 是纯函数，用构造序列断言边界）；
2. 窗口截取正确（3/5/10 年）；
3. 行业为**横截面分位**而非纵向分位（字段名 `cross_pct`，不得与 `pct3y` 混用）。
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient
import pytest

from backend.app.datasource import valuation
from backend.app.main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    """FastAPI 测试客户端（模块级，复用缓存）。

    Returns:
        TestClient: 应用客户端。
    """
    return TestClient(app)


# ---------------------------------------------------------------- 纯函数
def test_percentile_extremes() -> None:
    """最小值分位接近 0、最大值分位为 100。"""
    series = list(range(1, 101))
    assert valuation.percentile_of(series, 1) == 1.0
    assert valuation.percentile_of(series, 100) == 100.0


def test_percentile_middle() -> None:
    """中位数分位应约等于 50。"""
    series = list(range(1, 101))
    result = valuation.percentile_of(series, 50)
    assert result == pytest.approx(50.0, abs=1.0)


def test_percentile_uses_less_equal() -> None:
    """口径为「≤ 当前值」的样本占比（含等号），不是严格小于。"""
    series = [10] * 50 + [20] * 50
    assert valuation.percentile_of(series, 10) == 50.0
    assert valuation.percentile_of(series, 20) == 100.0


def test_percentile_insufficient_samples() -> None:
    """样本不足 12 个时返回 None（不足以谈分位）。"""
    assert valuation.percentile_of([1, 2, 3], 2) is None
    assert valuation.percentile_of([], 2) is None


def test_percentile_handles_none_current() -> None:
    """当前值缺失时返回 None，不得抛异常。"""
    assert valuation.percentile_of(list(range(1, 50)), None) is None


def test_window_filters_by_years() -> None:
    """窗口按年截取：3 年窗口不含 4 年前的样本。"""
    today = date(2026, 10, 31)
    records = [
        {"date": today - timedelta(days=365 * 1), "value": 1.0},
        {"date": today - timedelta(days=365 * 3 + 40), "value": 2.0},
        {"date": today - timedelta(days=365 * 6), "value": 3.0},
    ]
    three_year = valuation._window(records, 3, today=today)
    assert 1.0 in three_year
    assert 2.0 not in three_year, "3 年窗口不应包含 3 年多前的点"
    ten_year = valuation._window(records, 10, today=today)
    assert len(ten_year) == 3


def test_as_date_parses_common_formats() -> None:
    """日期解析支持 date / ISO 字符串 / 斜杠分隔。"""
    assert valuation._as_date(date(2026, 10, 30)) == date(2026, 10, 30)
    assert valuation._as_date("2026-10-30") == date(2026, 10, 30)
    assert valuation._as_date("2026/10/30") == date(2026, 10, 30)
    assert valuation._as_date("") is None
    assert valuation._as_date("非法") is None


# ---------------------------------------------------------------- 接口契约
def test_valuation_percentile_contract(client: TestClient) -> None:
    """接口返回指数 / 全 A / 行业三部分，且都带可用性与口径说明。"""
    body = client.get("/api/valuation/percentile").json()
    assert body["error"] is None
    data = body["data"]

    assert isinstance(data["indices"], list) and data["indices"], "指数列表不应为空"
    for item in data["indices"]:
        if not item["available"]:
            assert item.get("reason"), "不可用的指数必须给原因"
            continue
        assert item["pe"] > 0
        for key in ("pct3y", "pct5y", "pct10y"):
            value = item.get(key)
            assert value is None or 0 <= value <= 100, f"{item['name']} 的 {key} 越界：{value}"
        assert item["samples"]["y3"] >= 12, "3 年窗口应有足够样本"

    market = data["market"]
    assert market["available"] in (True, False)
    if market["available"]:
        assert market["pe"] > 0

    assert data["source"] and data["caliber"]
    assert data["disclaimer"], "必须附带「分位不代表未来」的免责说明"


def test_industry_uses_cross_section_not_history(client: TestClient) -> None:
    """行业分位必须是横截面字段 `cross_pct`，且不得混入纵向分位字段。"""
    data = client.get("/api/valuation/percentile").json()["data"]
    industries = data["industries"]
    if not industries:
        pytest.skip("行业快照本次不可用")
    for item in industries:
        assert "cross_pct" in item, "行业必须提供横截面分位 cross_pct"
        assert "pct3y" not in item, "行业不应出现纵向分位字段（防止口径混用）"
        assert 0 < item["cross_pct"] <= 100
        assert item["pe"] > 0
    # 横截面分位按 PE 升序单调递增
    ordered = sorted(industries, key=lambda x: x["pe"])
    pcts = [item["cross_pct"] for item in ordered]
    assert pcts == sorted(pcts), "横截面分位应随 PE 单调递增"


def test_erp_formula_when_bond_available(client: TestClient) -> None:
    """有国债时 ERP = 1/PE×100 − 10Y，误差 <0.02。

    用 `force=1` 绕过缓存：ERP 依赖宏观模块的 10Y 国债，若缓存是在国债可取之前写入的，
    会残留 `erp=None`，用缓存测会得到假绿。

    两级 skip：① 10Y 国债取不到 → 无从校验；② 指数估值本次全部取不到（上游限频）
    → 没有可比样本。两者都属外部数据源波动，不是实现缺陷，硬断言会把偶发波动变成假红。
    """
    data = client.get("/api/valuation/percentile?force=1").json()["data"]
    bond = data.get("bond_10y")
    if bond is None:
        pytest.skip("10Y 国债本次不可用，跳过 ERP 校验")
    if not any(item.get("available") for item in data["indices"]):
        pytest.skip("指数估值本次全部不可用（上游限频），跳过 ERP 校验")
    checked = 0
    for item in data["indices"]:
        if not item["available"] or item.get("erp") is None:
            continue
        expected = round((1 / item["pe"]) * 100 - bond, 2)
        assert abs(item["erp"] - expected) < 0.02, f"{item['name']} ERP 与公式不符"
        checked += 1
    assert checked > 0, "至少应有一个指数能算出 ERP"
