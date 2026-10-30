# -*- coding: utf-8 -*-
"""第七轮 M4 组合归因契约测试。

覆盖：空台账语义、权重合计 100%、贡献公式、HHI、情景符号、行业归集规则。
不 mock 行情：用 `_price_of` 的真实返回，取不到价的标的按实现跳过并记入 missing。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.datasource import portfolio as store
from backend.app.main import app


@pytest.fixture()
def client() -> TestClient:
    """FastAPI 测试客户端。

    Returns:
        TestClient: 应用客户端实例。
    """
    return TestClient(app)


def _write_ledger(tmp_path: Path, items):
    """把测试台账写入临时文件并指向 store。

    Args:
        tmp_path: pytest 临时目录。
        items: 持仓条目列表。

    Returns:
        Path: 台账文件路径。
    """
    target = tmp_path / "portfolio.json"
    target.write_text(json.dumps({"items": items, "updated_at": "2026-10-31T00:00:00+08:00"},
                                 ensure_ascii=False), encoding="utf-8")
    return target


def test_attribution_empty_ledger(client: TestClient, tmp_path, monkeypatch) -> None:
    """空台账：返回 count=0 且不报错（首页/组合页不应因空数据白屏）。"""
    target = _write_ledger(tmp_path, [])
    monkeypatch.setattr(store, "PORTFOLIO_FILE", target)
    data = client.get("/api/portfolio/attribution").json()["data"]
    assert data["totals"]["count"] == 0
    assert data["items"] == []
    assert "台账为空" in data["caliber"]


def test_attribution_weights_and_contributions(client: TestClient, tmp_path, monkeypatch) -> None:
    """有台账时：权重合计≈100%、贡献=权重×盈亏率、HHI∈(0,1]、情景为负。"""
    # 用贵州茅台 + 一只基金：两条都走真实取价，取不到的按实现跳过
    items = [
        {"id": "t1", "market": "a", "symbol": "600519", "name": "贵州茅台",
         "shares": 100, "cost": 1000.0},
        {"id": "t2", "market": "fund", "symbol": "017811", "name": "东方人工智能",
         "shares": 10000, "cost": 1.5},
    ]
    target = _write_ledger(tmp_path, items)
    monkeypatch.setattr(store, "PORTFOLIO_FILE", target)

    data = client.get("/api/portfolio/attribution").json()["data"]
    if data["totals"]["count"] == 0:
        pytest.skip("两条标的本次都取不到价，跳过归因断言")

    rows = data["items"]
    weight_sum = sum(row["weight"] for row in rows)
    assert abs(weight_sum - 100.0) < 0.05, f"权重合计应≈100，实际 {weight_sum}"

    for row in rows:
        if row["pnl_pct"] is None:
            continue
        expected = round(row["weight"] * row["pnl_pct"] / 100, 2)
        assert abs(row["contribution"] - expected) <= 0.02, f"{row['symbol']} 贡献与公式不符"

    total_value = data["totals"]["value"]
    for row in rows:
        if total_value:
            assert row["value"] <= total_value + 0.01

    hhi = data["concentration"]["hhi"]
    assert 0 < hhi <= 1.0, f"HHI 应在 (0,1]，实际 {hhi}"

    for scenario in data["scenarios"]:
        assert scenario["impact"] < 0, "下跌情景的影响必须为负"
        assert scenario["label"].endswith("−10%")


def test_attribution_fund_bucket(client: TestClient, tmp_path, monkeypatch) -> None:
    """基金归入「场外基金」这一桶，不与 A 股行业混排。"""
    items = [{"id": "t1", "market": "fund", "symbol": "017811", "name": "东方人工智能",
              "shares": 10000, "cost": 1.5}]
    target = _write_ledger(tmp_path, items)
    monkeypatch.setattr(store, "PORTFOLIO_FILE", target)

    data = client.get("/api/portfolio/attribution").json()["data"]
    if data["totals"]["count"] == 0:
        pytest.skip("基金净值本次取不到，跳过行业归集断言")
    names = [item["name"] for item in data["by_industry"]]
    assert names == ["场外基金"], f"基金应单列，实际归集为 {names}"


def test_attribution_missing_price_honest(client: TestClient, tmp_path, monkeypatch) -> None:
    """取不到价的标的进 missing，不被静默计为 0。"""
    items = [{"id": "t1", "market": "a", "symbol": "999999", "name": "不存在的票",
              "shares": 100, "cost": 10.0}]
    target = _write_ledger(tmp_path, items)
    monkeypatch.setattr(store, "PORTFOLIO_FILE", target)

    data = client.get("/api/portfolio/attribution").json()["data"]
    assert data["totals"]["count"] == 0
    assert any("999999" in note for note in data["missing"]), "取不到价必须进 missing"
