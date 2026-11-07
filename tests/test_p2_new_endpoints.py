# -*- coding: utf-8 -*-
"""第十轮 P2 新增能力契约用例（R13 持仓快照 / 经理画像 / 北向新口径）。

覆盖：
1. `portfolio_trend.ensure_today` 三条件（非交易日 / 未到 15:05 / 当日已有）；
2. `take_snapshot` 同日幂等（覆盖不追加）；
3. `load_trend` 点数 = 快照文件数、禁补零、断点即休市；
4. `/api/fund/manager` 信封与字段契约（found / manager.name / funds）；
5. `/api/moneyflow/northbound?metric=turnover` 与 `legacy_net` 并存不冲突。

全部用例用 tmp_path 重定向落盘目录，不触碰真实 data 目录。
"""
from __future__ import annotations

import json
from datetime import datetime  # 第十轮 C17：去掉未用的 date/timedelta

import pytest

from backend.app.datasource import portfolio_trend


@pytest.fixture()
def snap_dir(tmp_path, monkeypatch):
    """把快照目录重定向到临时目录（避免污染真实 data/portfolio_snapshots）。"""
    target = tmp_path / "portfolio_snapshots"
    target.mkdir()
    monkeypatch.setattr(portfolio_trend, "SNAP_DIR", target)
    monkeypatch.setattr(portfolio_trend, "_ensure_dir", lambda: None)
    return target


def _fake_analysis(monkeypatch, value: float = 100000.0) -> None:
    """把持仓分析替换为固定值，避免依赖真实台账。"""
    def fake():
        return {"totals": {"cny_market_value": value, "cny_cost": value * 0.9,
                           "cny_pnl": value * 0.1}, "count": 2}
    monkeypatch.setattr(portfolio_trend.portfolio_provider, "analyze", fake)


class TestSnapshotSchedule:
    """R13：快照调度三条件与幂等。"""

    def test_weekend_writes_nothing(self, snap_dir, monkeypatch):
        """非工作日不产生快照。"""
        _fake_analysis(monkeypatch)
        saturday = datetime(2026, 11, 7, 16, 0)  # 周六
        result = portfolio_trend.ensure_today(now=saturday)
        assert result["written"] is False
        assert "非工作日" in result["reason"]
        assert not list(snap_dir.glob("*.json"))

    def test_before_close_writes_nothing(self, snap_dir, monkeypatch):
        """未到 15:05 不产生快照。"""
        _fake_analysis(monkeypatch)
        early = datetime(2026, 11, 6, 15, 4)  # 周五 15:04
        result = portfolio_trend.ensure_today(now=early)
        assert result["written"] is False
        assert "15:05" in result["reason"]
        assert not list(snap_dir.glob("*.json"))

    def test_after_close_writes_once(self, snap_dir, monkeypatch):
        """工作日 15:05 后写一条，重复调用不重复写（幂等）。"""
        _fake_analysis(monkeypatch)
        moment = datetime(2026, 11, 6, 15, 30)
        first = portfolio_trend.ensure_today(now=moment)
        assert first["written"] is True
        assert (snap_dir / "2026-11-06.json").exists()

        second = portfolio_trend.ensure_today(now=moment)
        assert second["written"] is False
        assert "已存在" in second["reason"] or "补" in second["reason"]
        assert len(list(snap_dir.glob("*.json"))) == 1

    def test_take_snapshot_overwrites_same_day(self, snap_dir, monkeypatch):
        """同日重复执行覆盖而非追加（文件数恒为 1）。"""
        _fake_analysis(monkeypatch, 100000.0)
        portfolio_trend.take_snapshot("2026-11-06")
        _fake_analysis(monkeypatch, 120000.0)
        record = portfolio_trend.take_snapshot("2026-11-06")
        files = list(snap_dir.glob("*.json"))
        assert len(files) == 1
        assert record["market_value"] == 120000.0
        saved = json.loads(files[0].read_text(encoding="utf-8"))
        assert saved["market_value"] == 120000.0


class TestTrendSeries:
    """R13：走势点数 = 快照数，断点即休市，禁止补零。"""

    def test_points_equal_files_and_no_fill(self, snap_dir, monkeypatch):
        """3 个快照 → 3 个点；缺失日期不得补零（不生成中间点）。"""
        _fake_analysis(monkeypatch)
        for day in ("2026-11-03", "2026-11-04", "2026-11-06"):
            portfolio_trend.take_snapshot(day)
        data = portfolio_trend.load_trend(days=30)
        points = data.get("items") or []
        assert len(points) == 3
        assert [p["date"] for p in points] == ["2026-11-03", "2026-11-04", "2026-11-06"]
        # 11-05 无快照，序列中不得出现补零点
        assert all(p["date"] != "2026-11-05" for p in points)
        assert data.get("count") == 3

    def test_empty_state_has_hint(self, snap_dir):
        """无快照时返回空列表 + 中文说明，不报错。"""
        data = portfolio_trend.load_trend(days=30)
        assert data.get("items") == []
        assert data.get("count") == 0
        assert data.get("hint")


class TestNewEndpointsContract:
    """新端点信封与字段契约（走 TestClient）。"""

    def test_fund_manager_contract(self):
        """/api/fund/manager 返回五字段信封 + manager 结构。"""
        from fastapi.testclient import TestClient

        from backend.app.main import create_app

        client = TestClient(create_app())
        body = client.get("/api/fund/manager", params={"code": "017811"}).json()
        assert body["error"] is None, body
        data = body["data"]
        for key in ("source", "caliber", "as_of", "market_status", "is_realtime"):
            assert key in data, f"缺五字段之一：{key}"
        assert "found" in data
        if data["found"]:
            manager = data["manager"]
            assert manager.get("name")
            assert isinstance(manager.get("funds"), list)

    def test_northbound_two_metrics_coexist(self):
        """两种口径并存：turnover 给成交总额、legacy_net 给历史净买额。"""
        from fastapi.testclient import TestClient

        from backend.app.main import create_app

        client = TestClient(create_app())
        turn = client.get("/api/moneyflow/northbound",
                          params={"days": 20, "metric": "turnover"}).json()["data"]
        legacy = client.get("/api/moneyflow/northbound",
                            params={"days": 20, "metric": "legacy_net"}).json()["data"]
        assert turn["metric"] == "turnover"
        assert "成交总额" in (turn.get("caliber") or "")
        assert turn["items"] and "total" in turn["items"][0]
        assert legacy["items"] and "net" in legacy["items"][0]
        assert "净买额" in (legacy.get("caliber") or "")
