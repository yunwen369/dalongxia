# -*- coding: utf-8 -*-
"""提醒（M6）单元测试：规则 CRUD / 24h 去重 / 节流 / 评估引擎（全离线）。

网络评估器（报价/拥挤度/估值/公告）全部 monkeypatch，
不触真实源站（§11.11 测试隔离）。
"""

from __future__ import annotations

import pytest

from backend.app.datasource import alerts


@pytest.fixture(autouse=True)
def isolated_alerts(tmp_path, monkeypatch):
    """alerts.json 指向用例级临时目录（conftest 已做，这里再收窄一次并复位节流）。"""
    path = tmp_path / "data" / "alerts.json"
    monkeypatch.setattr(alerts, "ALERTS_FILE", path)
    alerts.reset_eval_state()
    yield path
    alerts.reset_eval_state()


def _add_price_rule(target="600519", op="lte", value=1350.0):
    """便捷建一条价格规则。"""
    return alerts.add_rule({"type": "price", "target": target, "op": op, "value": value})


def _patch_quote(monkeypatch, price=None, pct=None, error=None):
    """替换报价评估的取数函数。"""
    from backend.app.core.errors import NotFoundError

    def fake_quote(symbol, market="a"):
        if error == "notfound":
            raise NotFoundError("未找到")
        if error == "net":
            raise RuntimeError("ConnectionError")
        return {"price": price, "pct": pct}

    monkeypatch.setattr("backend.app.datasource.tencent.get_quote", fake_quote)


def test_rule_crud_cycle(isolated_alerts):
    """新增 → 更新 → 删除全流程。"""
    rule = _add_price_rule()
    assert rule["id"] and rule["enabled"] is True
    rules = alerts.list_rules()
    assert len(rules) == 1
    assert rules[0]["type_label"] == "价格触达"

    updated = alerts.update_rule(rule["id"], value=1300.0, enabled=False)
    assert updated["value"] == 1300.0
    assert updated["enabled"] is False

    assert alerts.delete_rule(rule["id"]) is True
    assert alerts.list_rules() == []
    # 删除不存在的规则返回 False
    assert alerts.delete_rule("no_such") is False


def test_rule_validation(isolated_alerts):
    """规则参数校验：类型/目标/阈值。"""
    with pytest.raises(ValueError):
        alerts.add_rule({"type": "unknown_type", "target": "600519"})
    with pytest.raises(ValueError):
        alerts.add_rule({"type": "price", "target": ""})
    with pytest.raises(ValueError):
        alerts.add_rule({"type": "price", "target": "600519"})  # 缺 op/value
    with pytest.raises(ValueError):
        alerts.add_rule({"type": "price", "target": "600519", "op": "between", "value": 1})
    with pytest.raises(ValueError):
        alerts.add_rule({"type": "crowding", "target": "半导体", "op": "gte", "value": "x"})
    with pytest.raises(ValueError):
        alerts.add_rule({"type": "pct_change", "target": "600519"})  # 缺 value
    with pytest.raises(ValueError):
        alerts.add_rule({"type": "pct_change", "target": "600519", "value": -1})  # 必须正数


def test_price_rule_fires_and_dedup(monkeypatch, isolated_alerts):
    """价格触达触发一次；24h 内不重复（去重）。"""
    _add_price_rule(op="lte", value=1350.0)
    _patch_quote(monkeypatch, price=1340.0)

    result = alerts.evaluate(force=True)
    assert result["new_events"] == 1
    assert result["unread"] == 1
    assert result["events"][0]["message"].startswith("600519")

    # 再次评估：同规则 24h 内 → 不再产生事件
    result2 = alerts.evaluate(force=True)
    assert result2["new_events"] == 0
    assert result2["unread"] == 1


def test_price_rule_no_fire_when_above(monkeypatch, isolated_alerts):
    """价格未触线 → 不产生事件。"""
    _add_price_rule(op="lte", value=1300.0)
    _patch_quote(monkeypatch, price=1392.0)
    result = alerts.evaluate(force=True)
    assert result["new_events"] == 0
    assert alerts.unread_count() == 0


def test_quote_source_error_no_event(monkeypatch, isolated_alerts):
    """数据源异常 → 不产生事件也不产生错误（安静跳过）。"""
    _add_price_rule()
    _patch_quote(monkeypatch, error="net")
    result = alerts.evaluate(force=True)
    assert result["new_events"] == 0
    _patch_quote(monkeypatch, error="notfound")
    result = alerts.evaluate(force=True)
    assert result["new_events"] == 0


def test_pct_change_rule(monkeypatch, isolated_alerts):
    """涨跌幅规则：|涨跌| 超阈值触发。"""
    alerts.add_rule({"type": "pct_change", "target": "600519", "value": 3.0})
    _patch_quote(monkeypatch, pct=-4.2)
    result = alerts.evaluate(force=True)
    assert result["new_events"] == 1
    assert "超 ±3%" in result["events"][0]["message"]


def test_crowding_rule_uses_m3(monkeypatch, isolated_alerts):
    """拥挤度规则走 M3 同一函数（sector.get_crowding）。"""
    alerts.add_rule({"type": "crowding", "target": "半导体", "op": "gte", "value": 80})
    calls = {}

    def fake_crowding(board):
        calls["board"] = board
        return {"score": 86.0, "level": "高位", "advice": "交易结构拥挤，建议复核"}

    monkeypatch.setattr("backend.app.datasource.sector.get_crowding", fake_crowding)
    result = alerts.evaluate(force=True)
    assert calls["board"] == "半导体"  # 证明调用了 M3 同一函数
    assert result["new_events"] == 1
    assert "86" in result["events"][0]["message"]


def test_valuation_rule_uses_m2(monkeypatch, isolated_alerts):
    """估值分位规则走 M2 同一接口（valuation.get_percentile，取 5 年分位）。"""
    alerts.add_rule({"type": "valuation", "target": "沪深300", "op": "lte", "value": 25})
    monkeypatch.setattr(
        "backend.app.datasource.valuation.get_percentile",
        lambda: {"indices": [
            {"name": "沪深300", "available": True, "pct5y": 21.0},
            {"name": "中证500", "available": True, "pct5y": 60.0},
        ]},
    )
    result = alerts.evaluate(force=True)
    assert result["new_events"] == 1
    assert "21" in result["events"][0]["message"]


def test_announcement_rule(monkeypatch, isolated_alerts):
    """公告规则：财报检索同源，只提醒新公告。"""
    rule = alerts.add_rule({"type": "announcement", "target": "600519"})

    def fake_filings(**kwargs):
        assert kwargs.get("q") == "600519"
        return {"list": [
            {"title": "贵州茅台：关于召开股东大会的通知", "company": "贵州茅台",
             "code": "600519", "pubTime": "2026-10-30 18:00:00"},
        ], "total": 1}

    monkeypatch.setattr("backend.app.datasource.financial_report.search_filings", fake_filings)
    result = alerts.evaluate(force=True)
    assert result["new_events"] == 1
    assert "新公告" in result["events"][0]["message"]

    # 再次评估：无更新的公告 → 不重复（last_fired 之后发布的才提醒）
    result2 = alerts.evaluate(force=True)
    assert result2["new_events"] == 0
    _ = rule  # 避免 lint 未使用告警


def test_report_date_rule_keyword_match(monkeypatch, isolated_alerts):
    """财报披露日规则：只命中年报/季报类标题（近似口径）。"""
    alerts.add_rule({"type": "report_date", "target": "600519"})
    monkeypatch.setattr(
        "backend.app.datasource.financial_report.search_filings",
        lambda **kwargs: {"list": [
            {"title": "贵州茅台：2026年第三季度报告", "company": "贵州茅台",
             "code": "600519", "pubTime": "2026-10-30 19:00:00"},
            {"title": "贵州茅台：关于回购股份进展的公告", "company": "贵州茅台",
             "code": "600519", "pubTime": "2026-10-30 19:01:00"},
        ], "total": 2},
    )
    result = alerts.evaluate(force=True)
    # 只命中「第三季度报告」，回购公告不算财报披露
    assert result["new_events"] == 1
    assert "财报披露" in result["events"][0]["message"]


def test_throttle_non_force(isolated_alerts, monkeypatch):
    """非 force 评估受节流约束：quote 类 60s 内第二轮跳过。"""
    _add_price_rule()
    _patch_quote(monkeypatch, price=1000.0)
    first = alerts.evaluate(force=False)  # 建立节流时间戳
    assert first["new_events"] == 1
    second = alerts.evaluate(force=False)
    assert second["new_events"] == 0
    assert second["skipped"].get("quote", 0) >= 1


def test_disabled_rule_skipped(isolated_alerts):
    """停用的规则不评估。"""
    rule = _add_price_rule()
    alerts.update_rule(rule["id"], enabled=False)
    result = alerts.evaluate(force=True)
    assert result["new_events"] == 0


def test_mark_read_and_events_list(isolated_alerts, monkeypatch):
    """已读标记与事件列表排序（未读在前，时间倒序）。"""
    _add_price_rule()
    _patch_quote(monkeypatch, price=1000.0)
    alerts.evaluate(force=True)
    events = alerts.list_events()
    assert len(events) == 1 and events[0]["read"] is False

    marked = alerts.mark_read()
    assert marked == 1
    assert alerts.unread_count() == 0
    assert alerts.list_events()[0]["read"] is True
    assert alerts.list_events(unread_only=True) == []
