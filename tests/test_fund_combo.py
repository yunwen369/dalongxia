# -*- coding: utf-8 -*-
"""第七轮 M5 基金组合契约测试。

覆盖：参数校验、六维打分值域、权重约束（单只/权益/现金）、相关性值域、空持仓语义。
真实调用上游（每只 1–2 次请求），上游不可用时 skip 而非假红。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.datasource import fund_combo
from backend.app.main import app

CODES = "017811,025500,021528"


@pytest.fixture()
def client() -> TestClient:
    """FastAPI 测试客户端。

    Returns:
        TestClient: 应用客户端实例。
    """
    return TestClient(app)


def test_evaluate_requires_codes(client: TestClient) -> None:
    """缺 codes 返回 INVALID_PARAM，不做默认基金猜测。"""
    payload = client.get("/api/fund/combo/evaluate").json()
    assert payload.get("error_code") == "INVALID_PARAM"


def test_evaluate_rejects_bad_risk(client: TestClient) -> None:
    """非法风险档位必须拒绝，不允许静默回落到 balanced。"""
    payload = client.get("/api/fund/combo/evaluate", params={"codes": CODES, "risk": "yolo"}).json()
    assert payload.get("error_code") == "INVALID_PARAM"


def test_risk_profiles_cash_floor_fixed() -> None:
    """现金下限固定 20%（用户 2026-10-31 裁决：不可调）。"""
    for name, profile in fund_combo.RISK_PROFILES.items():
        assert profile["cash_floor"] == 0.20, f"{name} 的现金下限应为 20%"
        assert 0 < profile["single_cap"] <= 0.20
        assert 0 < profile["equity_cap"] <= 1.0


def test_evaluate_weights_respect_constraints(client: TestClient) -> None:
    """权重必须同时满足：单只 ≤ 上限、合计 ≤ min(1−现金, 权益)。"""
    for profile in ("balanced", "conservative"):
        payload = client.get("/api/fund/combo/evaluate",
                             params={"codes": CODES, "risk": profile, "force": 1}).json()
        if payload.get("error"):
            pytest.skip(f"上游不可用：{payload['error']}")
        data = payload["data"]
        if not data.get("weights"):
            pytest.skip("没有可用样本，跳过约束断言")
        limits = data["constraints"]
        total = sum(item["weight"] for item in data["weights"])
        assert total <= (1 - limits["cash_floor"]) * 100 + 0.01, f"{profile} 合计超预算：{total}"
        assert total <= limits["equity_cap"] * 100 + 0.01, f"{profile} 超权益上限：{total}"
        for item in data["weights"]:
            assert item["weight"] <= limits["single_cap"] * 100 + 0.01, \
                f"{profile} 单只超上限：{item['code']}={item['weight']}%"


def test_evaluate_scores_and_correlation(client: TestClient) -> None:
    """六维分在 0–100；相关系数在 -1~1；样本字段齐全。"""
    payload = client.get("/api/fund/combo/evaluate", params={"codes": CODES}).json()
    if payload.get("error"):
        pytest.skip(f"上游不可用：{payload['error']}")
    data = payload["data"]
    if not data.get("items"):
        pytest.skip("没有可用样本")
    for item in data["items"]:
        assert item["score"] is None or 0 <= item["score"] <= 100
        stats = item["stats"]
        assert stats["max_drawdown"] <= 0, "最大回撤应为负数或 0"
        if stats["volatility"] is not None:
            assert stats["volatility"] >= 0
        for key in ("sharpe", "calmar", "drawdown", "rank", "style", "fee"):
            assert key in item["parts"]
    for row in data["correlation"]:
        if row["value"] is not None:
            assert -1.0 <= row["value"] <= 1.0


def test_evaluate_discloses_unknowns(client: TestClient) -> None:
    """缺失项必须在 missing 里（规模/成立年限不可用这条要出现）。"""
    payload = client.get("/api/fund/combo/evaluate", params={"codes": CODES}).json()
    if payload.get("error"):
        pytest.skip("上游不可用")
    data = payload["data"]
    text = " ".join(data.get("missing") or []) + str(data.get("caliber") or "")
    assert "风格" in text or "规模" in text, "数据缺口必须在 caliber/missing 里说明"


def test_diagnose_empty_or_consistent(client: TestClient) -> None:
    """诊断：台账无基金持仓时给空态说明；有持仓时目标+实际都有值。"""
    payload = client.get("/api/fund/combo/diagnose", params={"force": 1}).json()
    assert not payload.get("error")
    data = payload["data"]
    if not data.get("holdings"):
        assert data.get("missing"), "空持仓必须给出 missing 说明"
        assert "台账" in " ".join(data["missing"])
        return
    for row in data["holdings"]:
        assert "actual_weight" in row
        assert row["actual_weight"] >= 0
    for row in data.get("deviation") or []:
        assert abs(row["gap"]) >= fund_combo.REBALANCE_SINGLE_PT - 1e-9
