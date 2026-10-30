# -*- coding: utf-8 -*-
"""宏观模块（第七轮 M1）契约与口径测试。

分两层：
1. **纯函数**：月份解析、方向判定、象限映射——不依赖网络，快速且稳定；
2. **接口契约**：`/api/macro/overview` 的结构与空错分离——只跑一次（有缓存后很快）。

只断言**结构与口径**，不断言具体数值（宏观数据每月更新，硬编码数值会造成假绿/假红）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from backend.app.datasource import macro
from backend.app.main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    """FastAPI 测试客户端（模块级，复用缓存）。

    Returns:
        TestClient: 应用客户端。
    """
    return TestClient(app)


# ---------------------------------------------------------------- 纯函数
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026年09月份", "2026-09"),
        ("2008年01月份", "2008-01"),
        ("2026年12月", "2026-12"),
        ("", None),
        ("无数据", None),
    ],
)
def test_parse_month(raw: str, expected) -> None:
    """中文月份解析：正常值转 ISO 年月，异常值返回 None。"""
    assert macro._parse_month(raw) == expected


def test_build_quadrant_maps_four_stages() -> None:
    """四个方向组合必须映射到四个不同阶段，且结论里含阶段名。"""
    cases = {
        ("up", "up"): "过热",
        ("up", "down"): "复苏",
        ("down", "up"): "滞胀",
        ("down", "down"): "衰退",
    }
    for (growth, inflation), stage in cases.items():
        # 构造方向：up = 后3期均值明显高于前3期；down = 明显低于
        base = 50.0
        if growth == "up":
            pmi = [{"value": base}] * 3 + [{"value": base + 1.0}] * 3
        else:
            pmi = [{"value": base + 1.0}] * 3 + [{"value": base}] * 3
        if inflation == "up":
            ppi = [{"value": 0.0}] * 3 + [{"value": 2.0}] * 3
        else:
            ppi = [{"value": 2.0}] * 3 + [{"value": 0.0}] * 3
        result = macro._build_quadrant(pmi, ppi)
        assert result["growth"] == growth
        assert result["inflation"] == inflation
        assert result["stage"] == stage, f"{growth}/{inflation} 应判定为 {stage}"
        assert stage in result["conclusion"]
        assert result["disclaimer"], "结论文案必须带免责声明"


def test_build_quadrant_flat_growth_is_transition() -> None:
    """增长方向走平（差异在阈值内）时不得硬判象限，应为过渡期。"""
    pmi = [{"value": 50.0}] * 6
    ppi = [{"value": 1.0}] * 3 + [{"value": 3.0}] * 3
    result = macro._build_quadrant(pmi, ppi)
    assert result["growth"] == "flat"
    assert result["stage"] == "过渡期"


def test_build_quadrant_insufficient_samples() -> None:
    """样本不足（不足两个窗口）时方向为 flat，不得抛异常。"""
    result = macro._build_quadrant([{"value": 50.0}], [{"value": 1.0}])
    assert result["growth"] == "flat"
    assert result["inflation"] == "flat"


def test_social_financing_follows_empty_contract() -> None:
    """社融无可用源时必须返回 available=False + 原因，禁止用 0 冒充。"""
    item = macro._fetch_social_financing()
    assert item["key"] == "social_financing"
    assert item["available"] is False
    assert item["value"] is None
    assert item["reason"] and len(item["reason"]) > 8


# ---------------------------------------------------------------- 接口契约
def test_macro_overview_contract(client: TestClient) -> None:
    """接口返回结构：9 个指标 + 象限 + 序列，且每个指标都带可用性标记。"""
    body = client.get("/api/macro/overview").json()
    assert body["error"] is None
    data = body["data"]
    assert len(data["indicators"]) == 9
    keys = [item["key"] for item in data["indicators"]]
    assert keys == ["pmi", "pmi_service", "cpi", "ppi", "m2", "m1_m2",
                    "bond_10y", "term_spread", "social_financing"]
    for item in data["indicators"]:
        assert "available" in item
        if not item["available"]:
            assert item.get("reason"), f"{item['key']} 不可用但没给原因"
        else:
            assert item["value"] is not None
    quadrant = data["quadrant"]
    assert quadrant["stage"] in ("复苏", "过热", "滞胀", "衰退", "过渡期")
    assert data["source"] and data["caliber"] and data["as_of"]
    assert data["available_count"] >= 6, "至少应拿到 6 个指标（社融缺源时仍应有 8 个）"


def test_macro_overview_series_shape(client: TestClient) -> None:
    """序列按月升序、字段齐全，供前端画双轴图。"""
    data = client.get("/api/macro/overview").json()["data"]
    pmi = data["series"].get("pmi") or []
    ppi = data["series"].get("ppi") or []
    assert pmi and ppi, "PMI/PPI 序列不应为空"
    assert len(pmi) <= 24 and len(ppi) <= 24, "序列应截断到 24 期"
    periods = [item["period"] for item in pmi]
    assert periods == sorted(periods), "序列必须按月份升序"
    assert all(item.get("value") is not None for item in pmi)
