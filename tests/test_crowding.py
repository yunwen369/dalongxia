# -*- coding: utf-8 -*-
"""第七轮 M3 板块拥挤度契约测试。

覆盖：参数校验、分值域、六维权重完整性、等级与分值一致、缺失维度如实登记。
真实调用同花顺日线，上游不可用时 skip（不把数据源波动当实现缺陷）。
"""

from __future__ import annotations

import time

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
    """六维权重之和必须为 1（加权后不再归一才不失真）。"""
    total = sum(sector_provider.CROWDING_WEIGHTS.values())
    assert abs(total - 1.0) < 1e-9, f"权重合计应为 1，实际 {total}"


def test_crowding_score_and_parts(client: TestClient) -> None:
    """正常板块：分值 0–100、六维齐全、等级与阈值一致。"""
    payload = client.get("/api/sector/crowding", params={"board": "半导体"}).json()
    if payload.get("error"):
        pytest.skip(f"板块日线本次不可用：{payload['error']}")

    data = payload["data"]
    assert 0 <= data["score"] <= 100
    assert len(data["parts"]) == 6, "六维缺一不可（缺维度应在 missing 里说明，不是悄悄少一项）"
    # R4 新增两维必须在场（振幅 / 距60日高点），否则等于回退成四维
    labels = " ".join(part["label"] for part in data["parts"])
    assert "振幅" in labels and "距60日高点" in labels, f"R4 两维缺失：{labels}"
    for part in data["parts"]:
        assert part["label"]
        assert part["weight"] in (0.35, 0.20, 0.15, 0.08, 0.07)
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
    parts = payload["data"].get("parts") or []
    part_labels = {p.get("label", "") for p in parts}
    missing = " ".join(payload["data"].get("missing") or [])
    # 换手率 / 成分股相关性 如果不在 parts 里（不可得），必须在 missing 里登记
    for keyword, label in (("换手率", "换手率"), ("成分股相关性", "成分股相关性")):
        if not any(label in p for p in part_labels):
            assert keyword in missing, f"缺失维度未登记：{keyword}"
    # 融资余额：若 parts 里没有「融资余额强度」分项，则 missing 里必须提及融资余额
    if not any("融资余额" in p for p in part_labels):
        assert "融资余额" in missing, "融资余额维度未登记（既不在 parts 也不在 missing）"


def test_crowding_unknown_board_is_empty_data(client: TestClient) -> None:
    """不存在的板块名返回 EMPTY_DATA（而非 500）。"""
    payload = client.get("/api/sector/crowding", params={"board": "不存在的板块XYZ"}).json()
    assert payload.get("error"), "不存在的板块应返回错误而不是空成功"
    assert payload.get("error_code") in ("EMPTY_DATA", "UPSTREAM_ERROR")


def test_margin_by_board_shape(client: TestClient) -> None:
    """R5：行业融资余额强度端点返回正确结构（含降级态）。"""
    payload = client.get("/api/sector/margin").json()
    assert not payload.get("error"), payload.get("error")
    data = payload["data"]
    assert "items" in data and isinstance(data["items"], list)
    assert "sample" in data and isinstance(data["sample"], int)
    assert "caliber" in data and data["caliber"]
    # 有数据时：字段完整、分位 0–100、按强度降序
    if data["items"]:
        first = data["items"][0]
        for key in ("board", "balance_yi", "pct"):
            assert key in first, f"items 缺字段：{key}"
        pcts = [x["pct"] for x in data["items"] if x.get("pct") is not None]
        if pcts:
            assert all(0 <= p <= 100 for p in pcts), "分位应在 0–100"
            assert pcts == sorted(pcts, reverse=True), "应按分位降序"


# ---------------------------------------------------------------------------
# v3.7.2：板块流通市值合计缓存的三档过期语义（纯内存，不触网）
# ---------------------------------------------------------------------------

@pytest.fixture()
def cap_probe(monkeypatch: pytest.MonkeyPatch):
    """注入一个受控板块的流通市值缓存条目，并隔离落盘读写。

    隔离手段：把 ``_CAP_CACHE_LOADED`` 置 True 且 mtime 设成极大值，
    使 :func:`sector_provider._load_cap_cache` 直接早退，不读 ``board_cap.json``。
    同时把落盘函数换成空实现，避免测试污染真实缓存文件。

    Yields:
        Callable[[float], None]: 传入「已存在多少秒」，重设该条目的时间戳。
    """
    board = "_TEST_CAP_BOARD_"
    saved = (
        dict(sector_provider._CAP_CACHE),
        dict(sector_provider._CAP_CACHE_TS),
        sector_provider._CAP_CACHE_LOADED,
        sector_provider._CAP_CACHE_MTIME,
    )
    board_value = 4.195433999999e12  # 半导体量级（元）
    monkeypatch.setattr(sector_provider, "_save_cap_cache", lambda: None)
    monkeypatch.setattr(sector_provider, "_CAP_CACHE_LOADED", True)
    monkeypatch.setattr(sector_provider, "_CAP_CACHE_MTIME", float("inf"))
    sector_provider._CAP_CACHE[board] = board_value

    def set_age(seconds: float) -> None:
        """把该条目的时间戳设为「距今 seconds 秒前」。

        Args:
            seconds: 距今秒数。
        """
        sector_provider._CAP_CACHE_TS[board] = time.time() - seconds

    try:
        yield set_age
    finally:
        sector_provider._CAP_CACHE.clear()
        sector_provider._CAP_CACHE.update(saved[0])
        sector_provider._CAP_CACHE_TS.clear()
        sector_provider._CAP_CACHE_TS.update(saved[1])
        sector_provider._CAP_CACHE_LOADED = saved[2]
        sector_provider._CAP_CACHE_MTIME = saved[3]


def test_cap_cache_fresh_entry_is_reused(cap_probe) -> None:
    """新鲜条目：返回值，且不标记为需要刷新。"""
    cap_probe(60)  # 1 分钟前
    assert sector_provider._cached_cap("_TEST_CAP_BOARD_") is not None
    assert sector_provider._cap_is_stale("_TEST_CAP_BOARD_") is False


def test_cap_cache_stale_entry_still_returns_value(cap_probe) -> None:
    """v3.7.2 回归：超过新鲜期仍必须返回旧值（口径不能翻转），只标记待刷新。

    旧实现此处返回 None，导致 ``_board_float_cap`` 掉到「本地快照总市值」，
    即分母口径被静默换掉（实测差 2.084 倍）。
    """
    cap_probe(sector_provider._CAP_CACHE_TTL + 600)  # 6h 出头
    assert sector_provider._cached_cap("_TEST_CAP_BOARD_") is not None, (
        "过期只是需要刷新，不能返回 None——否则换手率分母会换口径"
    )
    assert sector_provider._cap_is_stale("_TEST_CAP_BOARD_") is True


def test_cap_cache_beyond_max_stale_returns_none(cap_probe) -> None:
    """超过 7 天硬上限：视为真过期，如实退回近似口径并标待刷新。"""
    cap_probe(sector_provider._CAP_CACHE_MAX_STALE + 60)
    assert sector_provider._cached_cap("_TEST_CAP_BOARD_") is None
    assert sector_provider._cap_is_stale("_TEST_CAP_BOARD_") is True


def test_cap_cache_absent_board_is_stale(cap_probe) -> None:
    """完全无缓存的板块：返回 None 且标记待补齐（走后台队列）。"""
    cap_probe(0)
    assert sector_provider._cached_cap("_TEST_CAP_UNKNOWN_") is None
    assert sector_provider._cap_is_stale("_TEST_CAP_UNKNOWN_") is True
