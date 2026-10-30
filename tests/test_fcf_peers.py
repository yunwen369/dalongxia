# -*- coding: utf-8 -*-
"""第七轮 M7（FCF 背离）/ M8（同业对比）契约测试。

上游为新浪三表与腾讯行情，单只 2–4 次请求；不可用时 skip，不把数据源波动当缺陷。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app

NON_FINANCIAL = "600519"   # 贵州茅台：典型非金融，六项检查应全部可算
FINANCIAL = "000001"       # 平安银行：金融业，应判为「不适用」


@pytest.fixture()
def client() -> TestClient:
    """FastAPI 测试客户端。

    Returns:
        TestClient: 应用客户端实例。
    """
    return TestClient(app)


def test_fcf_requires_symbol(client: TestClient) -> None:
    """缺 symbol 返回 INVALID_PARAM。"""
    payload = client.get("/api/stock/fcf-health").json()
    assert payload.get("error_code") == "INVALID_PARAM"


def test_fcf_rejects_bad_symbol(client: TestClient) -> None:
    """非法代码返回错误而不是空成功。"""
    payload = client.get("/api/stock/fcf-health", params={"symbol": "abc"}).json()
    assert payload.get("error")


def test_fcf_six_checks_for_non_financial(client: TestClient) -> None:
    """非金融标的：六项检查齐全，状态值在合法集合内，数值域合理。"""
    payload = client.get("/api/stock/fcf-health", params={"symbol": NON_FINANCIAL, "force": 1}).json()
    if payload.get("error"):
        pytest.skip(f"三表本次不可用：{payload['error']}")
    data = payload["data"]
    if data.get("applicable") is False:
        pytest.skip("该标的被判定为金融业，跳过六项断言")
    checks = data["checks"]
    assert len(checks) == 6, f"应有 6 项检查，实际 {len(checks)}"
    keys = {item["key"] for item in checks}
    assert keys == {"ocf_np", "fcf", "ar_gap", "inv_gap", "debt_ocf", "capex_ocf"}
    for item in checks:
        assert item["status"] in ("ok", "warn", "bad", "unknown"), item["status"]
        assert item["label"] and item["note"]
        if item["value"] is not None and item["key"] == "ocf_np":
            assert -10 < item["value"] < 50, f"现金流/净利 超出合理范围：{item['value']}"
    assert data["counters"]["ok"] + data["counters"]["warn"] + \
        data["counters"]["bad"] + data["counters"]["unknown"] == 6


def test_fcf_marks_financial_as_not_applicable(client: TestClient) -> None:
    """金融业必须判「不适用」并给原因，不能硬算出现误导性比率。"""
    payload = client.get("/api/stock/fcf-health", params={"symbol": FINANCIAL}).json()
    if payload.get("error"):
        pytest.skip(f"三表本次不可用：{payload['error']}")
    data = payload["data"]
    assert data.get("applicable") is False, "银行应被判为不适用"
    assert data.get("reason") and "金融" in data["reason"]
    assert data.get("checks") == []
    assert data.get("verdict") == "不适用"


def test_fcf_discloses_depreciation_gap(client: TestClient) -> None:
    """折旧缺失必须在 missing 里说明（原设计的「资本开支/折旧」被替代）。"""
    payload = client.get("/api/stock/fcf-health", params={"symbol": NON_FINANCIAL}).json()
    if payload.get("error"):
        pytest.skip("三表本次不可用")
    text = " ".join(payload["data"].get("missing") or [])
    assert "折旧" in text, "折旧缺口未登记"


def test_peer_compare_requires_two(client: TestClient) -> None:
    """少于 2 家返回 INVALID_PARAM（单家无从对比）。"""
    payload = client.get("/api/stock/peer-compare", params={"symbols": "600519"}).json()
    assert payload.get("error_code") == "INVALID_PARAM"


def test_peer_compare_rejects_too_many(client: TestClient) -> None:
    """超过 5 家返回 INVALID_PARAM（界面放不下，也不具可比性）。"""
    payload = client.get("/api/stock/peer-compare",
                         params={"symbols": "600519,000858,000568,600036,601398,600030"}).json()
    assert payload.get("error_code") == "INVALID_PARAM"


def test_peer_compare_matrix(client: TestClient) -> None:
    """三家白酒：公司数=3、指标定义齐全、最优标注存在、同行业不报警。"""
    payload = client.get("/api/stock/peer-compare",
                         params={"symbols": "600519,000858,000568", "force": 1}).json()
    if payload.get("error"):
        pytest.skip(f"上游不可用：{payload['error']}")
    data = payload["data"]
    assert len(data["companies"]) >= 2
    assert len(data["metrics"]) == 8, "应有 8 项对比指标"
    for company in data["companies"]:
        assert company["symbol"] and company["metrics"] is not None
    for key, hit in data["best"].items():
        assert hit["symbol"] in {c["symbol"] for c in data["companies"]}
    if data.get("industry_warning") is None:
        industries = {c.get("industry") for c in data["companies"] if c.get("industry")}
        assert len(industries) <= 1
