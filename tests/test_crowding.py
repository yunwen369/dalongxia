# -*- coding: utf-8 -*-
"""第七轮 M3 板块拥挤度契约测试。

覆盖：参数校验、分值域、四维权重完整性、等级与分值一致、缺失维度如实登记。
真实调用同花顺日线，上游不可用时 skip（不把数据源波动当实现缺陷）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.datasource import sector as sector_provider
from backend.app.main import app


@pytest.fixture()
def client() -> TestClient:
    """FastAPI 测试客户端。

    Returns:
        TestClient: 应用客户端实例。
    """
    return TestClient(app)


def test_crowding_requires_board(client: TestClient) -> None:
    """缺 board 参数返回 INVALID_PARAM，不做默认板块猜测。

    项目契约是「HTTP 200 + body.error」（main.py 全局异常处理把参数校验统一转成该结构），
    因此断言 error_code 而不是 HTTP 状态码。
    """
    payload = client.get("/api/sector/crowding").json()
    assert payload.get("error"), "缺参数必须返回错误而不是空成功"
    assert payload.get("error_code") == "INVALID_PARAM"


def test_crowding_weights_sum_to_one() -> None:
    """四维权重之和必须为 1（加权后不再归一才不失真）。"""
    total = sum(sector_provider.CROWDING_WEIGHTS.values())
    assert abs(total - 1.0) < 1e-9, f"权重合计应为 1，实际 {total}"


def test_crowding_score_and_parts(client: TestClient) -> None:
    """正常板块：分值 0–100、四维齐全、等级与阈值一致。"""
    payload = client.get("/api/sector/crowding", params={"board": "半导体"}).json()
    if payload.get("error"):
        pytest.skip(f"板块日线本次不可用：{payload['error']}")

    data = payload["data"]
    assert 0 <= data["score"] <= 100
    assert len(data["parts"]) == 4, "四维缺一不可（缺维度应在 missing 里说明，不是悄悄少一项）"
    for part in data["parts"]:
        assert part["label"]
        assert part["weight"] in (0.45, 0.25, 0.15)
        if part["pct"] is not None:
            assert 0 <= part["pct"] <= 100

    score = data["score"]
    if score >= 80:
        assert data["level"] == "高位"
    elif score >= 60:
        assert data["level"] == "偏高"
    else:
        assert data["level"] == "正常"
    assert data["sample_days"] >= 60


def test_crowding_missing_disclosed(client: TestClient) -> None:
    """缺失维度必须出现在 missing 里（不允许静默降级）。"""
    payload = client.get("/api/sector/crowding", params={"board": "半导体"}).json()
    if payload.get("error"):
        pytest.skip("板块日线本次不可用")
    missing = " ".join(payload["data"].get("missing") or [])
    for keyword in ("换手率", "融资余额", "成分股相关性"):
        assert keyword in missing, f"缺失维度未登记：{keyword}"


def test_crowding_unknown_board_is_empty_data(client: TestClient) -> None:
    """不存在的板块名返回 EMPTY_DATA（而非 500）。"""
    payload = client.get("/api/sector/crowding", params={"board": "不存在的板块XYZ"}).json()
    assert payload.get("error"), "不存在的板块应返回错误而不是空成功"
    assert payload.get("error_code") in ("EMPTY_DATA", "UPSTREAM_ERROR")
