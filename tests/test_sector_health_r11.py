# -*- coding: utf-8 -*-
"""R11/C11 概念排行抽检日志单测（全离线：扫描体 monkeypatch）。

覆盖：落档/覆盖当日行、完整率判据、调度三条件（工作日/15:35/缺档）、
连续 N 日判据 streak/all_pass、读取容错（坏行跳过）、双链路透出字段结构。
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from backend.app.datasource import sector_health


def _fake_rankings(scanned: int, total: int, failed: list[str]):
    """构造排行扫描返回值工厂。

    Args:
        scanned: 命中板块数。
        total: 清单总数。
        failed: 失败清单。

    Returns:
        callable: 忽略 kwargs 的扫描替身。
    """
    def _fn(**kwargs):
        """替身扫描体。

        Returns:
            dict: 排行结果。
        """
        return {"items": [], "scanned": scanned, "total": total, "failed": failed}

    return _fn


def test_run_spotcheck_writes_and_passes(monkeypatch):
    """99.1% ≥98 → pass=True，字段齐全，落 jsonl 一行。"""
    monkeypatch.setattr(
        "backend.app.datasource.sector.get_crowding_rankings",
        _fake_rankings(798, 805, ["肿瘤疫苗（ValueError）"]),
    )
    record = sector_health.run_spotcheck("concept")
    assert record["scanned"] == 798 and record["total"] == 805
    assert record["completeness_pct"] == pytest.approx(99.13, abs=0.01)
    assert record["pass"] is True
    assert record["failed_count"] == 1
    rows = sector_health._read_all()
    assert len(rows) == 1 and rows[0]["date"] == record["date"]


def test_run_spotcheck_below_threshold(monkeypatch):
    """97% <98 → pass=False（阈值判据不放宽）。"""
    monkeypatch.setattr(
        "backend.app.datasource.sector.get_crowding_rankings",
        _fake_rankings(97, 100, [f"x{i}" for i in range(3)]),
    )
    record = sector_health.run_spotcheck("concept")
    assert record["completeness_pct"] == 97.0
    assert record["pass"] is False


def test_run_spotcheck_scan_error_records(monkeypatch):
    """扫描整体抛错 → error 留痕、pass=False（不静默吞）。"""
    def _boom(**kwargs):
        """抛错替身。"""
        raise RuntimeError("通道全挂")

    monkeypatch.setattr("backend.app.datasource.sector.get_crowding_rankings", _boom)
    record = sector_health.run_spotcheck("concept")
    assert record["error"] and "RuntimeError" in record["error"]
    assert record["pass"] is False


def test_run_spotcheck_overwrites_same_day(monkeypatch):
    """同口径同日重复触发覆盖当日行（一日一行语义）。"""
    monkeypatch.setattr(
        "backend.app.datasource.sector.get_crowding_rankings",
        _fake_rankings(100, 100, []),
    )
    sector_health.run_spotcheck("concept")
    sector_health.run_spotcheck("concept")
    rows = sector_health._read_all()
    assert len(rows) == 1


def test_ensure_today_gate_conditions():
    """调度三条件：非工作日 / 未到 15:35 / 当日已有 → 全部不跑。"""
    # 周六
    result = sector_health.ensure_today(datetime(2026, 11, 7, 16, 0))
    assert result["written"] is False and "非工作日" in result["reason"]
    # 工作日 15:34
    result = sector_health.ensure_today(datetime(2026, 11, 6, 15, 34))
    assert result["written"] is False and "15:35" in result["reason"]


def test_ensure_today_writes_once_per_day(monkeypatch):
    """工作日 15:35 后首次写档；第二次命中「当日抽检已存在」。"""
    monkeypatch.setattr(
        "backend.app.datasource.sector.get_crowding_rankings",
        _fake_rankings(200, 200, []),
    )
    result = sector_health.ensure_today(datetime(2026, 11, 6, 15, 35))
    assert result["written"] is True
    result2 = sector_health.ensure_today(datetime(2026, 11, 6, 16, 0))
    assert result2["written"] is False and "已存在" in result2["reason"]


def test_load_records_streak_and_all_pass():
    """连续判据：最近 3 条 过/过/挂 → streak=2（自最新往旧截断于首个未达标）、all_pass=False。"""
    days = ["2026-11-04", "2026-11-05", "2026-11-06"]
    passes = [False, True, True]  # 11-06（最新）过、11-05 过、11-04 挂
    rows = [
        {"date": d, "kind": "concept", "scanned": 798, "total": 805,
         "completeness_pct": 99.1, "failed": [], "pass": p, "error": None}
        for d, p in zip(days, passes)
    ]
    sector_health._rewrite_all(rows)
    data = sector_health.load_records(days=10, kind="concept")
    assert [item["date"] for item in data["items"]] == sorted(days, reverse=True)
    assert data["streak_days_ok"] == 2
    assert data["all_pass"] is False
    assert data["latest_pass"] is True


def test_load_records_three_days_all_pass():
    """连续 3 个交易日全部 ≥98% → streak=3、all_pass=True（C11 达标记据）。"""
    rows = [
        {"date": d, "kind": "concept", "scanned": 798, "total": 805,
         "completeness_pct": 99.13, "failed": [], "pass": True, "error": None}
        for d in ("2026-11-04", "2026-11-05", "2026-11-06")
    ]
    sector_health._rewrite_all(rows)
    data = sector_health.load_records(kind="concept")
    assert data["streak_days_ok"] == 3
    assert data["all_pass"] is True


def test_load_records_none_when_insufficient(monkeypatch):
    """不足 3 条记录 → all_pass=None（证据未攒够，绝不默认 True）。"""
    sector_health._rewrite_all([
        {"date": "2026-11-06", "kind": "concept", "scanned": 100, "total": 100,
         "completeness_pct": 100.0, "failed": [], "pass": True, "error": None}])
    data = sector_health.load_records(kind="concept")
    assert data["all_pass"] is None
    assert data["streak_days_ok"] == 1


def test_read_all_tolerates_bad_lines():
    """坏行（非 JSON / 缺 date）跳过，不抛。"""
    sector_health.SPOTCHECK_FILE.parent.mkdir(parents=True, exist_ok=True)
    sector_health.SPOTCHECK_FILE.write_text(
        "{ broken\n" + json.dumps({"date": "2026-11-06", "kind": "concept", "pass": True}) + "\n",
        encoding="utf-8",
    )
    rows = sector_health._read_all()
    assert len(rows) == 1


def test_concept_kline_dual_link_backup(monkeypatch):
    """R11 双链路：腾讯主源失败 → 同花顺概念指数线（885xxx）备源命中。"""
    from backend.app.datasource import sector, ths_http, tencent_board

    def _boom(code, **kwargs):
        """腾讯主源抛错替身。

        Args:
            code: 板块代码。
            **kwargs: 忽略。

        Returns:
            list: 不会返回。
        """
        raise ValueError("模拟源站抖动")

    monkeypatch.setattr(tencent_board, "get_board_kline", _boom)
    monkeypatch.setattr(sector, "_tencent_board_code", lambda name, kind: "pt02GN0001")
    fake_ths = [{"date": f"2026-01-{i:02d}", "open": 1, "high": 2, "low": 0.5,
                 "close": 1.5, "volume": 100, "amount": 200, "pct": 1.0}
                for i in range(1, 81)]
    monkeypatch.setattr(ths_http, "get_concept_index_codes", lambda: {"某概念": "885999"})
    monkeypatch.setattr(ths_http, "get_board_kline", lambda code, **kw: fake_ths)
    rows = sector._kline_ths("concept", "某概念", "daily", fill_today=False)
    assert len(rows) == 80
    assert rows[-1]["amount"] == 200.0


def test_concept_kline_dual_link_both_fail(monkeypatch):
    """两源皆失败 → ValueError 且错误信息含主源异常归因。"""
    from backend.app.datasource import sector, ths_http, tencent_board

    def _boom(code, **kwargs):
        """腾讯主源抛错替身。

        Args:
            code: 板块代码。
            **kwargs: 忽略。

        Returns:
            list: 不会返回。
        """
        raise ValueError("模拟源站抖动")

    monkeypatch.setattr(tencent_board, "get_board_kline", _boom)
    monkeypatch.setattr(sector, "_tencent_board_code", lambda name, kind: "pt02GN0002")
    monkeypatch.setattr(ths_http, "get_concept_index_codes", lambda: {})
    with pytest.raises(ValueError) as err:
        sector._kline_ths("concept", "新概念", "daily", fill_today=False)
    assert "两源 K 线均为空" in str(err.value)
    assert "ValueError" in str(err.value)  # 主源异常类型透出


def test_concept_kline_backup_error_also_falls_through(monkeypatch):
    """备源也抛错（同花顺限流）→ 归因主源并 ValueError，不吞异常。"""
    from backend.app.datasource import sector, ths_http, tencent_board

    def _boom(code, **kwargs):
        """腾讯主源抛错替身。

        Args:
            code: 板块代码。
            **kwargs: 忽略。

        Returns:
            list: 不会返回。
        """
        raise TimeoutError("模拟腾讯超时")

    def _boom2(code, **kwargs):
        """同花顺备源抛错替身。

        Args:
            code: 板块代码。
            **kwargs: 忽略。

        Returns:
            list: 不会返回。
        """
        raise ValueError("模拟同花顺限流")

    monkeypatch.setattr(tencent_board, "get_board_kline", _boom)
    monkeypatch.setattr(sector, "_tencent_board_code", lambda name, kind: "pt02GN0003")
    monkeypatch.setattr(ths_http, "get_concept_index_codes", lambda: {"某概念": "885999"})
    monkeypatch.setattr(ths_http, "get_board_kline", _boom2)
    with pytest.raises(ValueError) as err:
        sector._kline_ths("concept", "某概念", "daily", fill_today=False)
    assert "TimeoutError" in str(err.value)
