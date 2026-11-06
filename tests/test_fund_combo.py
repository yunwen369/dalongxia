# -*- coding: utf-8 -*-
"""第七轮 M5 基金组合契约测试。

覆盖：参数校验、六维打分值域、权重约束（单只/权益/现金）、相关性值域、空持仓语义。
真实调用上游（每只 1–2 次请求），上游不可用时 skip 而非假红。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from typing import Any, Dict

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


# ============================================================
# R7（设计基准 §8 / 验收 C7）：三条硬性排除——规模/年限/清盘风险
# ============================================================

def _patch_profile(monkeypatch, mapping: Dict[str, Dict[str, Any]]) -> None:
    """把 fund_provider.get_profile_basis 换成按代码查表的假实现。

    Args:
        monkeypatch: pytest 补丁器。
        mapping: {code: profile 字典}。
    """
    def fake(code: str) -> Dict[str, Any]:
        """查表返回预置档案。

        Args:
            code: 基金代码。

        Returns:
            dict: 预置档案。
        """
        return mapping[code]

    monkeypatch.setattr(fund_combo.fund_provider, "get_profile_basis", fake)


def test_hard_exclusion_passed_normal(monkeypatch: None) -> None:
    """规模 277 亿 / 成立 3.7 年 / 环比 -11% → passed，无排除理由。"""
    _patch_profile(monkeypatch, {"017811": {
        "scale_yi": 277.34, "age_years": 3.7, "scale_mom_pct": -11.47,
        "inception_date": "2023-02-22", "inception_source": "天天基金F10",
        "scale_source": "天天基金F10（季度净资产）", "missing": [],
    }})
    result = fund_combo._hard_exclusion("017811")
    assert result["status"] == "passed", result
    assert result["reasons"] == []


def test_hard_exclusion_scale_below_minimum(monkeypatch: None) -> None:
    """规模 1.5 亿 < 2 亿 → excluded，理由点名规模下限。"""
    _patch_profile(monkeypatch, {"888888": {
        "scale_yi": 1.5, "age_years": 5.0, "scale_mom_pct": 3.0,
        "inception_date": "2020-01-01", "inception_source": "天天基金F10",
        "scale_source": "天天基金F10（季度净资产）", "missing": [],
    }})
    result = fund_combo._hard_exclusion("888888")
    assert result["status"] == "excluded", result
    assert any("规模" in text and "2.0" in text for text in result["reasons"]), result["reasons"]


def test_hard_exclusion_age_below_minimum(monkeypatch: None) -> None:
    """成立 1.15 年 < 3 年 → excluded（025500 实测场景）。"""
    _patch_profile(monkeypatch, {"025500": {
        "scale_yi": 66.7, "age_years": 1.15, "scale_mom_pct": -60.91,
        "inception_date": "2025-09-12", "inception_source": "天天基金F10",
        "scale_source": "天天基金F10（季度净资产）", "missing": [],
    }})
    result = fund_combo._hard_exclusion("025500")
    assert result["status"] == "excluded", result
    assert any("成立" in text and "3.0" in text for text in result["reasons"]), result["reasons"]


def test_hard_exclusion_large_shrink_not_liquidation(monkeypatch: None) -> None:
    """56 亿大基金单季 -52.98%（012922 实测）→ passed，不得误判清盘风险。

    清盘风险只对「小型带（<5 亿）且急缩」生效；健康大基金的正常申赎波动
    离 5000 万监管红线两个数量级，用环比一刀切会误伤（本轮修正的核心断言）。
    """
    _patch_profile(monkeypatch, {"012922": {
        "scale_yi": 56.25, "age_years": 4.82, "scale_mom_pct": -52.98,
        "inception_date": "2022-01-11", "inception_source": "天天基金F10",
        "scale_source": "天天基金F10（季度净资产）", "missing": [],
    }})
    result = fund_combo._hard_exclusion("012922")
    assert result["status"] == "passed", f"大基金急缩被误判：{result['reasons']}"


def test_hard_exclusion_small_liquidation_risk(monkeypatch: None) -> None:
    """小型带（3 亿）且单季净流出 ≤ -30% → excluded 清盘风险。"""
    _patch_profile(monkeypatch, {"777777": {
        "scale_yi": 3.0, "age_years": 4.0, "scale_mom_pct": -40.0,
        "inception_date": "2021-01-01", "inception_source": "天天基金F10",
        "scale_source": "天天基金F10（季度净资产）", "missing": [],
    }})
    result = fund_combo._hard_exclusion("777777")
    assert result["status"] == "excluded", result
    assert any("清盘" in text for text in result["reasons"]), result["reasons"]


def test_hard_exclusion_unknown_not_pass_by_default(monkeypatch: None) -> None:
    """规模不可得 → unknown（绝不默认放行）；metrics 如实透出 None。"""
    _patch_profile(monkeypatch, {"968061": {
        "scale_yi": None, "age_years": 6.7, "scale_mom_pct": None,
        "inception_date": "2020-02-24", "inception_source": "蛋卷（found_date）",
        "scale_source": None,
        "missing": ["净资产规模不可得（F10 无序列且雪球兜底失败）"],
    }})
    result = fund_combo._hard_exclusion("968061")
    assert result["status"] == "unknown", f"规模缺失不能算 passed：{result}"
    assert result["metrics"]["scale_yi"] is None
    assert result["profile_missing"], "unknown 必须透出档案缺失原因"


def test_allocate_skips_excluded(monkeypatch: None) -> None:
    """_allocate 必须把 exclusion.status=excluded 者剔出目标权重。"""
    items = [
        {"code": "017811", "exclusion": {"status": "passed"},
         "score": 40.0, "stats": {"volatility": 18.0}},
        {"code": "025500", "exclusion": {"status": "excluded"},
         "score": 30.0, "stats": {"volatility": 20.0}},
        {"code": "012922", "exclusion": {"status": "unknown"},
         "score": 50.0, "stats": {"volatility": 15.0}},
    ]
    weights = fund_combo._allocate(items, fund_combo.RISK_PROFILES["balanced"])
    codes = {row["code"] for row in weights}
    assert "025500" not in codes, f"被排除者混入权重：{codes}"
    # passed 与 unknown 都可参与配权（unknown 只是判定缺依据，非确认排除）
    assert "017811" in codes and "012922" in codes, codes


def test_profile_basis_real_structure(client: TestClient) -> None:
    """真实档案通道契约：017811 的规模/成立日字段齐全且口径自洽（上游不可用 skip）。

    C7 要求「F10 与天天基金页面值一致」——此处断言结构、来源标注与年限推导；
    数值一致由人核（5 只）完成，见本轮自检报告。
    """
    from backend.app.datasource import fund as fund_provider

    profile = fund_provider.get_profile_basis("017811")
    if profile.get("scale_yi") is None and profile.get("inception_date") is None:
        pytest.skip("天天基金档案通道本次不可用")
    assert profile["code"] == "017811"
    assert profile["inception_date"] and len(profile["inception_date"]) == 10
    assert profile["inception_source"], "成立日必须标注来源（F10/蛋卷/净值下界之一）"
    assert isinstance(profile["age_years"], (int, float)) and profile["age_years"] > 0
    assert isinstance(profile["scale_series"], list)
    for row in profile["scale_series"]:
        assert {"date", "scale_yi", "mom_pct"} <= set(row)
    if profile["scale_yi"] is not None:
        assert profile["scale_source"], "有规模值必须有来源"
    assert "caliber" in profile and profile["caliber"]
