# -*- coding: utf-8 -*-
"""筛选器 + 提醒路由 HTTP 契约测试（TestClient，全离线）。

覆盖 §6.3 统一返回结构：HTTP 200 + {data, error, error_code}；
快照未建库 → EMPTY_DATA + 中文说明；条件不合法 → INVALID_PARAM。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.app.datasource import alerts as alerts_module
from backend.app.datasource import screener as screener_module
from backend.app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """隔离数据文件 + TestClient。"""
    monkeypatch.setattr(screener_module, "SNAPSHOT_FILE", tmp_path / "data" / "snap.json")
    monkeypatch.setattr(alerts_module, "ALERTS_FILE", tmp_path / "data" / "alerts.json")
    alerts_module.reset_eval_state()
    with TestClient(app) as test_client:
        yield test_client
    alerts_module.reset_eval_state()


def _seed_snapshot(monkeypatch, tmp_path):
    """预置迷你快照。"""
    path = tmp_path / "data" / "snap.json"
    payload = {
        "version": "v1", "built_at": "2026-10-31 12:00:00", "trade_date": "2026-10-30",
        "rows": [
            {"code": "600519", "name": "贵州茅台", "board": "白酒", "close": 1392.0, "chg": 2.8,
             "mktcap": 1.74e12, "pe": 21.4, "pb": 6.4, "ps": 10.0, "pcf": 14.6,
             "roe_years": [34.2, 36.0, 32.5], "gross": 91.9, "rev_yoy": 15.6, "np_yoy": 14.1,
             "debt_ratio": 20.1, "ocf": 9.2e10, "fin_missing": False},
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_screener_status_not_ready(client, tmp_path, monkeypatch):
    """快照未建库：status ready=false（HTTP 200）。"""
    # 不触发真实后台构建（auto/manual 均禁），保证纯状态读取
    monkeypatch.setattr(screener_module, "maybe_auto_build",
                        lambda: {"triggered": False, "building": False, "reason": "test"})
    resp = client.get("/api/screener/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    assert body["data"]["ready"] is False
    assert body["data"]["progress"]["step"] in ("none", "idle")


def test_screener_run_without_snapshot(client):
    """未建库直接 run → EMPTY_DATA + 中文说明（不以 0 命中冒充）。"""
    resp = client.post("/api/screener/run", json={"conditions": []})
    body = resp.json()
    assert resp.status_code == 200
    assert body["error_code"] == "EMPTY_DATA"
    assert "快照未建库" in body["error"]


def test_screener_run_ok(client, tmp_path, monkeypatch):
    """预置快照后 run 命中。"""
    _seed_snapshot(monkeypatch, tmp_path)
    resp = client.post("/api/screener/run", json={
        "conditions": [{"factor": "pe", "op": "lte", "value": 30}],
    })
    body = resp.json()
    assert resp.status_code == 200
    assert body["error"] is None
    assert body["data"]["total_matched"] == 1
    assert body["data"]["items"][0]["code"] == "600519"


def test_screener_run_invalid_factor(client, tmp_path, monkeypatch):
    """未知因子 → INVALID_PARAM。"""
    _seed_snapshot(monkeypatch, tmp_path)
    resp = client.post("/api/screener/run", json={
        "conditions": [{"factor": "nope", "op": "gte", "value": 1}],
    })
    assert resp.json()["error_code"] == "INVALID_PARAM"


def test_screener_refresh_async(client, monkeypatch):
    """手动刷新：异步启动立即返回（真实网络函数被替换，不触源站）。"""
    monkeypatch.setattr(screener_module, "_run_build",
                        lambda reason: {"version": "t", "row_count": 0, "elapsed": 0.0,
                                        "trade_date": "2026-10-30"})
    resp = client.post("/api/screener/refresh")
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    assert body["data"]["started"] in (True, False)  # CI 无网络也不报 5xx
    assert "message" in body["data"]


def test_alerts_root_empty(client):
    """空规则列表 + 0 未读。"""
    resp = client.get("/api/alerts")
    body = resp.json()
    assert resp.status_code == 200
    assert body["data"]["rules"] == []
    assert body["data"]["unread"] == 0


def test_alerts_add_and_delete(client):
    """新增规则 → 出现在列表 → 删除。"""
    resp = client.post("/api/alerts", json={
        "type": "price", "target": "600519", "op": "lte", "value": 1350,
    })
    body = resp.json()
    assert body["error"] is None
    rule_id = body["data"]["id"]

    resp = client.get("/api/alerts")
    assert resp.json()["data"]["rules"][0]["type_label"] == "价格触达"

    resp = client.delete(f"/api/alerts/{rule_id}")
    assert resp.json()["data"]["deleted"] == rule_id
    assert client.get("/api/alerts").json()["data"]["rules"] == []


def test_alerts_add_invalid(client):
    """非法规则 → INVALID_PARAM（HTTP 仍 200）。"""
    resp = client.post("/api/alerts", json={"type": "bad", "target": "x"})
    assert resp.json()["error_code"] == "INVALID_PARAM"


def test_alerts_events_read_flow(client, monkeypatch):
    """评估产生事件 → 未读 → 全部已读。"""
    client.post("/api/alerts", json={
        "type": "price", "target": "600519", "op": "lte", "value": 9999,
    })
    monkeypatch.setattr(
        "backend.app.datasource.tencent.get_quote",
        lambda symbol, market="a": {"price": 1.0, "pct": 0.5},
    )
    resp = client.post("/api/alerts/evaluate?force=true")
    body = resp.json()
    assert body["data"]["new_events"] == 1

    resp = client.get("/api/alerts/events")
    assert resp.json()["data"]["unread"] == 1

    resp = client.post("/api/alerts/events/read")
    assert resp.json()["data"]["marked"] == 1
    assert client.get("/api/alerts/events").json()["data"]["unread"] == 0
