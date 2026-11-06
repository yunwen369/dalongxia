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


# ============================================================
# R15（设计基准 §8 / 验收 C15）：订阅流——板块订阅公告并入事件流
# ============================================================

def _patch_subscription(monkeypatch, codes=None, rows=None, cons_error=None):
    """替换订阅评估器的两个取数点（板块成分 + 东财批量公告）。

    Args:
        monkeypatch: pytest 补丁器。
        codes: 成分股列表（默认 3 只）。
        rows: 公告行工厂（callable(pubspec)→list）或直接列表。
        cons_error: 注入成分取数异常类型名。

    Returns:
        dict: 记录调用次数的字典 {"cons": n, "multi": n}。
    """
    from datetime import datetime, timedelta, timezone

    bj = timezone(timedelta(hours=8))
    recent = (datetime.now(bj) + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
    calls = {"cons": 0, "multi": 0}

    def fake_cons(board, kind="industry", **kwargs):
        calls["cons"] += 1
        if cons_error:
            raise ValueError("模拟板块成分不可得")
        return codes if codes is not None else ["600519", "000650", "002371"]

    def fake_multi(got_codes, **kwargs):
        calls["multi"] += 1
        base = [
            {"title": "回购实施公告", "company": "贵州茅台", "code": "600519", "pubTime": recent},
            {"title": "三季度报告", "company": "仁和药业", "code": "000650", "pubTime": recent},
            {"title": "多年前的旧公告", "company": "北方华创", "code": "002371", "pubTime": "2020-01-01 10:00"},
        ]
        return rows if rows is not None else base

    monkeypatch.setattr("backend.app.datasource.sector.get_board_constituents", fake_cons)
    monkeypatch.setattr("backend.app.datasource.financial_report.em_multi_announcements", fake_multi)
    return calls


def _set_baseline(minutes_ago: float = 10.0) -> None:
    """把首条规则的订阅基线拨到指定分钟前（模拟「确认后的下一轮」）。

    Args:
        minutes_ago: 基线回拨分钟数。

    Returns:
        None
    """
    import time as _time

    payload = alerts._read_all()
    for rule in payload.get("rules") or []:
        rule["sub_baseline"] = _time.time() - minutes_ago * 60
    alerts._write_all(payload)
    alerts.reset_eval_state()


def test_subscription_first_round_is_confirmation_only(monkeypatch, isolated_alerts):
    """首次订阅只落「已订阅」确认事件，不回放历史公告（基线=当下）。"""
    _patch_subscription(monkeypatch)
    alerts.add_rule({"type": "subscription", "target": "白酒"})
    result = alerts.evaluate(force=True)
    assert result["new_events"] == 1
    assert "已订阅" in result["events"][0]["message"]
    assert "板块订阅公告" == result["events"][0]["type_label"]


def test_subscription_new_announcements_fire_with_24h_dedup(monkeypatch, isolated_alerts):
    """C15：订阅后新公告入事件列表；24h 内同公告不重复。"""
    _patch_subscription(monkeypatch)
    alerts.add_rule({"type": "subscription", "target": "白酒"})
    alerts.evaluate(force=True)
    _set_baseline(10.0)
    result = alerts.evaluate(force=True)
    # 两条 recent 公告触发（2020 旧公告早于基线必须过滤）
    assert result["new_events"] == 2, result["events"]
    assert all("订阅公告" in e["message"] for e in result["events"])
    # 同轮再评估：内容级 24h 去重，不重复入列
    alerts.reset_eval_state()
    again = alerts.evaluate(force=True)
    assert again["new_events"] == 0


def test_subscription_multi_fire_same_rule_not_rule_deduped(monkeypatch, isolated_alerts):
    """订阅是事件流：一条规则一轮多条（规则级 24h 去重不适用，内容级生效）。"""
    calls = _patch_subscription(monkeypatch)
    alerts.add_rule({"type": "subscription", "target": "半导体"})
    alerts.evaluate(force=True)
    _set_baseline(10.0)
    result = alerts.evaluate(force=True)
    assert result["new_events"] == 2  # 规则级去重会限成 1，能出 2 即证走内容级
    # 一轮内事件 id 全部唯一（同毫秒撞 id 回归防护）
    events = alerts.list_events()
    assert len({item["id"] for item in events}) == len(events)
    # 板块成分只取一次（行业命中即止），公告一次多代码查询
    assert calls["cons"] <= 2 and calls["multi"] >= 1


def test_subscription_single_round_cap(monkeypatch, isolated_alerts):
    """单轮入列上限 5 条防轰炸（第 6 条下轮续推，不丢不静默）。"""
    from datetime import datetime, timedelta, timezone

    bj = timezone(timedelta(hours=8))
    recent = (datetime.now(bj) + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
    rows = [{"title": f"公告{i}", "company": f"公司{i}", "code": f"60000{i}", "pubTime": recent}
            for i in range(8)]
    _patch_subscription(monkeypatch, codes=[f"60000{i}" for i in range(8)], rows=rows)
    alerts.add_rule({"type": "subscription", "target": "白酒"})
    alerts.evaluate(force=True)
    _set_baseline(10.0)
    result = alerts.evaluate(force=True)
    assert result["new_events"] == alerts.SUB_MAX_PER_ROUND == 5
    # 下轮：剩余 3 条继续入列（未被丢弃），前 5 条内容去重不再重复
    _set_baseline(10.0)
    second = alerts.evaluate(force=True)
    assert second["new_events"] == 3


def test_subscription_constituents_unavailable_no_event(monkeypatch, isolated_alerts):
    """板块成分不可得（反爬/无此板块）→ 本轮不产事件不产错误（下次轮询自愈）。"""
    _patch_subscription(monkeypatch, cons_error=True)
    alerts.add_rule({"type": "subscription", "target": "不存在板块XYZ"})
    result = alerts.evaluate(force=True)
    assert result["new_events"] == 0


def test_evaluate_no_double_append(monkeypatch, isolated_alerts):
    """R15 修复③回归：事件存储非空时新触发不得同 id 双写。"""
    _add_price_rule()
    _patch_quote(monkeypatch, price=1000.0)
    alerts.evaluate(force=True)
    # 第二轮再触发另一条规则（存储已非空）
    alerts.reset_eval_state()
    alerts.add_rule({"type": "price", "target": "000650", "op": "lte", "value": 1350.0})
    alerts.evaluate(force=True)
    events = alerts.list_events()
    ids = [item["id"] for item in events]
    assert len(ids) == len(set(ids)) == 2, ids


def test_same_kind_rules_all_evaluated(monkeypatch, isolated_alerts):
    """R15 修复①回归：同类别多条规则一轮全部评估（原实现第二条被饿死）。"""
    taken: list = []

    def fake_quote(symbol, market="a"):
        taken.append(symbol)
        return {"price": 1340.0, "pct": 1.0}

    import backend.app.datasource.tencent as tencent_module

    monkeypatch.setattr(tencent_module, "get_quote", fake_quote)
    _add_price_rule(target="600519")
    _add_price_rule(target="000650")
    _add_price_rule(target="002371")
    alerts.evaluate(force=False)
    assert taken == ["600519", "000650", "002371"], taken
