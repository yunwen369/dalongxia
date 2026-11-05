# -*- coding: utf-8 -*-
"""新增功能用例：自选股（W-01~W-04）与龙虎榜·北向（L-01~L-03）。

真实网络断言（不使用 mock/演示数据）；自选股清单写入临时文件，不污染用户 data/。
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend.app.datasource import watchlist as store
from backend.app.main import app

client = TestClient(app)


@pytest.fixture()
def temp_store(tmp_path, monkeypatch):
    """把自选清单重定向到临时文件（每用例独立）。

    Args:
        tmp_path: pytest 临时目录。
        monkeypatch: pytest 补丁器。

    Yields:
        Path: 临时清单路径。
    """
    target = tmp_path / "watchlist.json"
    monkeypatch.setattr(store, "WATCHLIST_FILE", target)
    yield target


def test_watchlist_crud_and_four_markets(temp_store) -> None:
    """自选股：四市场混合增删查、去重、真实行情、落盘可复原。"""
    empty = client.get("/api/watchlist").json()["data"]
    assert empty["counts"]["total"] == 0
    assert empty["empty_note"], "空清单必须给出中文引导"
    assert empty["items"] == []

    cases = [("600519", "a"), ("00700", "hk"), ("AAPL", "us"), ("017811", "fund")]
    for symbol, market in cases:
        body = client.post("/api/watchlist", json={"symbol": symbol, "market": market}).json()
        assert body["error"] is None, body
        assert body["data"]["created"] is True

    dup = client.post("/api/watchlist", json={"symbol": "600519", "market": "a"}).json()
    assert dup["data"]["created"] is False, "重复添加必须去重"

    listing = client.get("/api/watchlist").json()["data"]
    assert listing["counts"]["total"] == 4
    assert listing["source"] and listing["caliber"]
    by_key = {(item["symbol"], item["market"]): item for item in listing["items"]}
    a_row = by_key[("600519", "a")]
    assert a_row["quote"]["price"] > 0 and a_row["quote"]["pct"] is not None
    assert a_row["quote"]["amount"] and a_row["quote"]["amount"] > 1e8, "A股成交额应为元量级"
    hk_row = by_key[("00700", "hk")]
    assert hk_row["quote"]["currency"] == "HKD" and hk_row["quote"]["price"] > 0
    us_row = by_key[("AAPL", "us")]
    assert us_row["quote"]["currency"] == "USD" and us_row["quote"]["price"] > 0
    fund_row = by_key[("017811", "fund")]
    assert fund_row["quote"]["price"] > 0 and fund_row["quote"]["market_status"] == "净值"

    saved = json.loads(temp_store.read_text(encoding="utf-8"))
    assert len(saved["items"]) == 4, "清单必须落盘"
    assert client.get("/api/watchlist").json()["data"]["counts"]["total"] == 4

    removed = client.request("DELETE", "/api/watchlist/600519", params={"market": "a"}).json()
    assert removed["data"]["removed"] is True
    assert client.get("/api/watchlist").json()["data"]["counts"]["total"] == 3
    again = client.request("DELETE", "/api/watchlist/600519", params={"market": "a"}).json()
    assert again["data"]["removed"] is False, "重复移除必须幂等返回 False"


def test_watchlist_reject_bad_input(temp_store) -> None:
    """参数校验：空代码 / 非法市场 / 超长清单均返回中文错误，不落盘。"""
    bad = client.post("/api/watchlist", json={"symbol": "", "market": "a"}).json()
    assert bad["error_code"] == "INVALID_PARAM"
    bad_market = client.post("/api/watchlist", json={"symbol": "600519", "market": "xx"}).json()
    assert bad_market["error_code"] == "INVALID_PARAM"
    bad_code = client.post("/api/watchlist", json={"symbol": "12345", "market": "a"}).json()
    assert bad_code["error_code"] == "INVALID_PARAM"
    assert not temp_store.exists(), "非法请求不得写盘"


def test_northbound_real() -> None:
    """L-01：北向历史返回 20 个有披露交易日，日期升序、数值可用、口径自洽。"""
    # 第十轮 R3：northbound 扩参 metric=turnover|legacy_net，默认 turnover（新披露口径）。
    # 本用例校验历史净买额口径，显式指定 legacy_net（旧能力保留，不随新口径删除）。
    body = client.get("/api/moneyflow/northbound", params={"days": 20, "metric": "legacy_net"}).json()
    assert body["error"] is None, body
    data = body["data"]
    items = data["items"]
    assert len(items) == 20
    dates = [item["date"] for item in items]
    assert dates == sorted(dates), "日期必须升序"
    assert dates[-1] == data["latest_disclosed"]
    for item in items:
        assert isinstance(item["net"], (int, float))
        assert abs(item["net"]) < 5000, "净买额量级应为亿元"
    if data["discontinued"]:
        assert "停止披露" in data["note"], "停更必须给出中文说明"
    assert data["source"] and data["caliber"]


def test_northbound_turnover_new_caliber() -> None:
    """R3：新披露口径返回沪深股通当日成交总额（亿元）与前十活跃证券，且不再给净买额。"""
    body = client.get("/api/moneyflow/northbound", params={"days": 20, "metric": "turnover"}).json()
    assert body["error"] is None, body
    data = body["data"]
    assert data["metric"] == "turnover"
    items = data["items"]
    assert items, "成交总额序列不得为空"
    first = items[0]
    assert {"date", "total"} <= set(first)
    assert isinstance(first["total"], (int, float)) and first["total"] > 0
    # 单日沪深股通成交总额量级：百亿~万亿（亿元）
    assert 10 < first["total"] < 100000, f"成交总额量级异常：{first['total']}"
    assert "成交总额" in (data.get("caliber") or ""), "口径文案须说明成交总额"
    assert data.get("latest_label"), "须给出最新披露日标签"


def test_north_top_contract() -> None:
    """L-02：无当日数据时必须是「成功 + 空列表 + 说明」，不得报错。"""
    body = client.get("/api/moneyflow/north-top", params={"limit": 10}).json()
    assert body["error"] is None, body
    data = body["data"]
    assert isinstance(data["items"], list)
    assert len(data["items"]) <= 10
    if not data["items"]:
        assert data["note"], "空列表必须附带中文说明"
    else:
        for item in data["items"]:
            assert item["code"].isdigit() and item["name"]
            assert item["net"] is not None


def test_lhb_real() -> None:
    """L-03：龙虎榜返回最近披露日明细，字段齐全、净买额亿元量级、可跳个股。"""
    body = client.get("/api/moneyflow/lhb", params={"limit": 50}).json()
    assert body["error"] is None, body
    data = body["data"]
    items = data["items"]
    if not items:
        assert data["note"], "无披露必须给中文说明"
        return
    assert data["date"] and data["total"] >= len(items)
    assert date.fromisoformat(data["date"]) <= date.today()
    for row in items[:10]:
        assert row["code"].isdigit() and len(row["code"]) == 6
        assert row["name"] and row["reason"]
        assert isinstance(row["net_buy"], (int, float)) and abs(row["net_buy"]) < 100
        assert row["buy"] is None or row["buy"] >= 0
    assert data["source"] and data["caliber"]


# =============================================================
# 四域增强（2026-10-25）：AI 测试连接 / 缓存统计 / 日志导出 / 恢复出厂
# =============================================================
def test_ai_test_requires_key_and_validates_base_url() -> None:
    """/api/ai/test：未填 key 给 NOT_CONFIGURED；base_url 非法给 INVALID_PARAM。"""
    no_key = client.post("/api/ai/test", json={
        "base_url": "https://api.deepseek.com/v1", "api_key": "", "model": "deepseek-chat"
    }).json()
    assert no_key["error_code"] == "NOT_CONFIGURED"
    assert "api_key" in no_key["error"]

    bad_url = client.post("/api/ai/test", json={
        "base_url": "ftp://invalid", "api_key": "sk-x", "model": "m"
    }).json()
    assert bad_url["error_code"] == "INVALID_PARAM"
    assert "http" in bad_url["error"]

    empty_model = client.post("/api/ai/test", json={
        "base_url": "https://api.deepseek.com/v1", "api_key": "sk-x", "model": ""
    }).json()
    assert empty_model["error_code"] == "INVALID_PARAM"
    assert "model" in empty_model["error"]


def test_ai_test_unreachable_host_gives_chinese_error() -> None:
    """/api/ai/test：不可达主机 → UPSTREAM_ERROR，中文文案且 latency_ms 有值。"""
    result = client.post("/api/ai/test", json={
        "base_url": "http://127.0.0.1:9/v1", "api_key": "sk-x", "model": "m"
    }).json()
    assert result["error_code"] == "UPSTREAM_ERROR"
    assert result["data"]["latency_ms"] >= 0


def test_cache_stats_contract() -> None:
    """/api/cache/stats：字节数与 TTL 表齐全（R3-13 S-03 展示源）。"""
    body = client.get("/api/cache/stats").json()
    assert body["error"] is None
    data = body["data"]
    for key in ("memory", "disk", "memory_bytes", "disk_bytes", "layout_bytes",
                "memory_mb", "disk_mb", "layout_kb", "ttl_map"):
        assert key in data, f"缺少字段 {key}"
    assert data["memory_mb"] == round(data["memory_bytes"] / 1024 / 1024, 2)
    assert data["disk_mb"] == round(data["disk_bytes"] / 1024 / 1024, 2)
    # TTL 表与数据字典 §9 同源（9 条），含养基宝条目
    keys = [item["key"] for item in data["ttl_map"]]
    assert any("yjb" in key for key in keys)
    assert any("realtime_quote" in key for key in keys)


def test_logs_export_returns_zip() -> None:
    """/api/logs/export：打包本机日志为 zip 并带下载文件名。"""
    response = client.get("/api/logs/export")
    assert response.status_code == 200
    assert "application/zip" in response.headers.get("content-type", "")
    assert "openbb_logs_" in response.headers.get("content-disposition", "")


def test_factory_reset_requires_confirm(tmp_path, monkeypatch) -> None:
    """/api/factory-reset：缺 confirm 拒绝；白名单外 scope 忽略；用户数据绝不删。"""
    from backend.app import config

    # 隔离：偏好文件重定向到临时目录（不碰真实 data/）
    monkeypatch.setattr(config, "AI_CONFIG_FILE", tmp_path / "ai_config.json")
    prefs = tmp_path / "user_prefs.json"
    prefs.write_text('{"fund_estimation": true}', encoding="utf-8")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    denied = client.post("/api/factory-reset", json={"scopes": ["prefs"]}).json()
    assert denied["error_code"] == "INVALID_PARAM"
    assert prefs.is_file(), "缺 confirm 时不得删除任何文件"

    ok = client.post("/api/factory-reset", json={
        "confirm": True, "scopes": ["prefs", "portfolio", "watchlist"]
    }).json()
    assert ok["error"] is None
    assert "prefs" in ok["data"]["removed"]
    assert not prefs.is_file(), "确认后应删除 user_prefs.json"
    # 白名单外的 scope 被标记为未知，绝不当真执行
    assert set(ok["data"]["unknown_scopes"]) == {"portfolio", "watchlist"}
    assert "kept" in ok["data"]


def test_connectors_status_has_policy_and_fund_market() -> None:
    """/api/connectors/status：限流/TTL/降级链每源都有；矩阵含场外基金列。"""
    body = client.get("/api/connectors/status").json()
    assert body["error"] is None
    data = body["data"]
    sources = data["datasources"]
    assert len(sources) >= 8
    for item in sources:
        assert item["rate_limit"] and item["rate_limit"] != "未登记", item["label"]
        assert item["ttl"], item["label"]
        assert item["fallback"] and item["fallback"] != "—", item["label"]
    assert set(data["markets"].keys()) == {"a", "hk", "us", "fund"}
    assert data["markets"]["fund"]["quote"]["available"] is False
    assert data["markets"]["a"]["quote"]["available"] is True


# =============================================================
# W-02/W-03 自选分组（2026-10-25 全量验收轮补）
# =============================================================
def test_watchlist_counts_by_market(temp_store) -> None:
    """/api/watchlist：counts.by_market 按市场分组合计（W-02 chips / W-03 侧栏树数据源）。"""
    client.post("/api/watchlist", json={"market": "a", "symbol": "600519", "name": "贵州茅台"})
    client.post("/api/watchlist", json={"market": "hk", "symbol": "00700", "name": "腾讯控股"})
    client.post("/api/watchlist", json={"market": "fund", "symbol": "017811", "name": "测试基金"})
    data = client.get("/api/watchlist").json()["data"]
    by_market = data["counts"]["by_market"]
    assert by_market == {"a": 1, "hk": 1, "fund": 1}
    assert data["counts"]["total"] == 3
    # 空清单态同样带 by_market 键（契约稳定）
    client.delete("/api/watchlist/600519?market=a")
    client.delete("/api/watchlist/00700?market=hk")
    client.delete("/api/watchlist/017811?market=fund")
    empty = client.get("/api/watchlist").json()["data"]
    assert empty["counts"]["by_market"] == {}
    assert empty["counts"]["total"] == 0
