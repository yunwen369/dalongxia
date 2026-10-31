# -*- coding: utf-8 -*-
"""提醒与预警（第七轮 M6）：规则 + 事件 + 24h 去重 + 仅应用内。

数据流（全站指标唯一实现，提醒不另算一份）：
- 价格/涨跌幅：``tencent.get_quote``（与个股页同一报价链路）；
- 拥挤度：``sector.get_crowding``（M3 同一函数）；
- 估值分位：``valuation.get_percentile``（M2 同一接口）；
- 公告：``financial_report.search_filings``（q=代码，与财报检索页同源）；
- 财报披露日：复用公告检索，标题匹配年报/季报关键词近似判断（口径写明）。

存储：``data/alerts.json``（{rules:[], events:[]}，原子写）。
频率保护：同一 (rule_key) 24h 内只触发一次（rule_key = 规则 ID + 对象 + 类型）。
评估节流：行情 60s、公告 10min、宏观类（估值/拥挤度）6h——与设计规格一致。
提示形态：仅应用内（铃铛 + toast + 标题闪烁），不做系统通知（用户裁决 5）。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

from backend.app import config
from backend.app.core import atomic
from backend.app.datasource import utils

logger = logging.getLogger(__name__)

ALERTS_FILE = config.DATA_DIR / "alerts.json"

RULE_TYPES = ("price", "pct_change", "valuation", "crowding", "announcement", "report_date")
RULE_TYPE_LABELS = {
    "price": "价格触达",
    "pct_change": "涨跌幅超阈值",
    "valuation": "估值分位越线",
    "crowding": "拥挤度越线",
    "announcement": "目标股新公告",
    "report_date": "财报披露日",
}
OPERATORS = ("gte", "lte")
MAX_RULES = 50
MAX_EVENTS = 200  # 事件上限，超出丢最旧
DEDUP_WINDOW = 24 * 3600.0  # 24h 去重窗口（秒）

# 评估节流（秒）：行情 60s / 公告 10min / 宏观类 6h
EVAL_INTERVALS = {
    "quote": 60.0,        # price / pct_change
    "announcement": 600.0,  # announcement / report_date
    "slow": 6 * 3600.0,   # valuation / crowding
}
_SLOW_TYPES = ("valuation", "crowding")
_ANN_TYPES = ("announcement", "report_date")


def _now_text() -> str:
    """北京时间文本。"""
    return utils.now_text(utils.bj_now())


def _now_ts() -> float:
    """当前秒级时间戳（用北京 now 推回，保证与落盘时间文本同源）。"""
    return utils.bj_now().timestamp()


# =============================================================
# 一、存储
# =============================================================
def _read_all() -> Dict[str, Any]:
    """读取规则与事件；缺失/损坏返回空结构。"""
    import json

    try:
        payload = json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload.setdefault("rules", [])
            payload.setdefault("events", [])
            return payload
    except (OSError, ValueError):
        pass
    return {"rules": [], "events": []}


def _write_all(payload: Dict[str, Any]) -> None:
    """原子写 alerts.json。"""
    atomic.atomic_write_json(ALERTS_FILE, payload, ensure_ascii=False)


# =============================================================
# 二、规则 CRUD
# =============================================================
def list_rules() -> List[Dict[str, Any]]:
    """返回全部规则（含派生字段 last_fired_text / paused）。"""
    payload = _read_all()
    rules: List[Dict[str, Any]] = []
    for rule in payload.get("rules") or []:
        item = dict(rule)
        fired = item.get("last_fired")
        item["last_fired_text"] = _ts_to_text(fired)
        item["paused"] = bool(item.get("paused", False))
        item["type_label"] = RULE_TYPE_LABELS.get(str(item.get("type")), str(item.get("type")))
        rules.append(item)
    return rules


def _ts_to_text(ts: Any) -> Optional[str]:
    """时间戳转北京文本；None/0 → None。"""
    try:
        value = float(ts)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    from datetime import datetime, timezone, timedelta

    dt = datetime.fromtimestamp(value, tz=timezone(timedelta(hours=8)))
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def add_rule(rule: Dict[str, Any]) -> Dict[str, Any]:
    """新增规则。

    Args:
        rule: {type, target, op, value, note?}；type ∈ RULE_TYPES。

    Returns:
        Dict[str, Any]: 已落盘的规则（含 id/created_at）。

    Raises:
        ValueError: 类型/运算符/参数不合法或超量。
    """
    rule_type = str(rule.get("type") or "").strip()
    if rule_type not in RULE_TYPES:
        raise ValueError(f"规则类型必须是 {'/'.join(RULE_TYPES)}")
    target = str(rule.get("target") or "").strip()
    if not target:
        raise ValueError("提醒对象不能为空（代码或板块/指数名）")
    payload = _read_all()
    rules = payload.get("rules") or []
    if len(rules) >= MAX_RULES:
        raise ValueError(f"规则最多 {MAX_RULES} 条，请先删除部分规则")

    item: Dict[str, Any] = {
        "id": f"r_{int(_now_ts() * 1000) % 10**10}",
        "type": rule_type,
        "target": target,
        "note": str(rule.get("note") or ""),
        "enabled": bool(rule.get("enabled", True)),
        "created_at": _now_text(),
        "last_fired": 0,
    }
    # 需要显式 op/value 的规则：price / valuation / crowding；
    # pct_change 语义固定为「单日 |涨跌| ≥ value」，不需要 op
    if rule_type in ("price", "valuation", "crowding"):
        op = str(rule.get("op") or "").strip()
        if op not in OPERATORS:
            raise ValueError("该规则需要比较运算符 op：gte（≥）或 lte（≤）")
        value = _to_float(rule.get("value"))
        if value is None:
            raise ValueError("该规则需要数字阈值 value")
        item["op"] = op
        item["value"] = value
    elif rule_type == "pct_change":
        value = _to_float(rule.get("value"))
        if value is None or value <= 0:
            raise ValueError("涨跌幅规则需要正数阈值 value（百分比，如 3 表示 ±3%）")
        item["value"] = value
    rules.append(item)
    payload["rules"] = rules
    _write_all(payload)
    return item


def delete_rule(rule_id: str) -> bool:
    """删除规则（保留历史事件）。

    Args:
        rule_id: 规则 ID。

    Returns:
        bool: 是否删除了规则。
    """
    payload = _read_all()
    rules = payload.get("rules") or []
    kept = [rule for rule in rules if str(rule.get("id")) != str(rule_id)]
    if len(kept) == len(rules):
        return False
    payload["rules"] = kept
    _write_all(payload)
    return True


def update_rule(rule_id: str, **fields: Any) -> Dict[str, Any]:
    """更新规则（enabled / op / value / note / target）。

    Args:
        rule_id: 规则 ID。
        **fields: 待更新字段。

    Returns:
        Dict[str, Any]: 更新后的规则。

    Raises:
        ValueError: 规则不存在或字段不合法。
    """
    payload = _read_all()
    rules = payload.get("rules") or []
    target_rule: Optional[Dict[str, Any]] = None
    for rule in rules:
        if str(rule.get("id")) == str(rule_id):
            target_rule = rule
            break
    if target_rule is None:
        raise ValueError(f"规则不存在：{rule_id}")
    if "enabled" in fields:
        target_rule["enabled"] = bool(fields["enabled"])
    if "op" in fields:
        if fields["op"] not in OPERATORS:
            raise ValueError("op 必须是 gte 或 lte")
        target_rule["op"] = fields["op"]
    if "value" in fields:
        value = _to_float(fields["value"])
        if value is None:
            raise ValueError("value 必须为数字")
        target_rule["value"] = value
    if "note" in fields:
        target_rule["note"] = str(fields["note"] or "")
    if "target" in fields:
        text = str(fields["target"] or "").strip()
        if not text:
            raise ValueError("target 不能为空")
        target_rule["target"] = text
    _write_all(payload)
    return target_rule


def _to_float(value: Any) -> Optional[float]:
    """安全转 float。"""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:
        return None
    return result


# =============================================================
# 三、事件与 24h 去重
# =============================================================
def list_events(unread_only: bool = False, limit: int = 100) -> List[Dict[str, Any]]:
    """返回事件列表（时间倒序，未读在前）。

    Args:
        unread_only: 只看未读。
        limit: 上限。

    Returns:
        List[Dict[str, Any]]: 事件列表。
    """
    events = _read_all().get("events") or []
    events = sorted(
        events,
        key=lambda item: (item.get("read", False), -float(item.get("ts") or 0)),
    )
    if unread_only:
        events = [item for item in events if not item.get("read")]
    return events[:limit]


def unread_count() -> int:
    """未读事件数。"""
    return sum(1 for item in (_read_all().get("events") or []) if not item.get("read"))


def mark_read(event_id: Optional[str] = None) -> int:
    """标记已读；event_id 为空时全部已读。

    Args:
        event_id: 事件 ID；None 表示全部。

    Returns:
        int: 实际标记条数。
    """
    payload = _read_all()
    changed = 0
    for item in payload.get("events") or []:
        if item.get("read"):
            continue
        if event_id is None or str(item.get("id")) == str(event_id):
            item["read"] = True
            changed += 1
    if changed:
        _write_all(payload)
    return changed


def _fire_event(payload: Dict[str, Any], rule: Dict[str, Any], message: str,
                value: Optional[float]) -> Dict[str, Any]:
    """写入一条事件（同 rule_key 24h 去重在调用方完成）。"""
    event = {
        "id": f"e_{int(_now_ts() * 1000) % 10**10}",
        "rule_id": rule.get("id"),
        "type": rule.get("type"),
        "type_label": RULE_TYPE_LABELS.get(str(rule.get("type")), str(rule.get("type"))),
        "target": rule.get("target"),
        "message": message,
        "value": value,
        "ts": _now_ts(),
        "time": _now_text(),
        "read": False,
    }
    events = payload.get("events") or []
    events.append(event)
    # 上限裁剪：丢最旧
    if len(events) > MAX_EVENTS:
        events = sorted(events, key=lambda item: float(item.get("ts") or 0))[-MAX_EVENTS:]
    payload["events"] = events
    rule["last_fired"] = _now_ts()
    return event


def _dedup_ok(events: List[Dict[str, Any]], rule: Dict[str, Any], now_ts: float) -> bool:
    """同规则 24h 内是否已触发过（防轰炸）。"""
    rule_id = str(rule.get("id"))
    for item in events:
        if str(item.get("rule_id")) == rule_id and now_ts - float(item.get("ts") or 0) < DEDUP_WINDOW:
            return False
    return True


# =============================================================
# 四、评估引擎（节流 + 指标同源）
# =============================================================
_EVAL_LOCK = threading.Lock()
_EVAL_STATE: Dict[str, float] = {}  # {kind: 上次评估时间戳}


def _throttle_ok(kind: str) -> bool:
    """按类别节流（quote 60s / announcement 10min / slow 6h）。

    Args:
        kind: 评估类别。

    Returns:
        bool: 是否允许本轮评估。
    """
    interval = EVAL_INTERVALS.get(kind, 60.0)
    now = _now_ts()
    last = _EVAL_STATE.get(kind, 0.0)
    if now - last < interval:
        return False
    _EVAL_STATE[kind] = now
    return True


def _rule_kind(rule_type: str) -> str:
    """规则类型 → 节流类别。"""
    if rule_type in _SLOW_TYPES:
        return "slow"
    if rule_type in _ANN_TYPES:
        return "announcement"
    return "quote"


def _check_op(actual: float, op: str, threshold: float) -> bool:
    """按 op 比较。"""
    return actual >= threshold if op == "gte" else actual <= threshold


def _quote_eval(payload: Dict[str, Any], rule: Dict[str, Any]) -> List[Dict[str, Any]]:
    """价格/涨跌幅规则评估（tencent 同源）。"""
    from backend.app.core.errors import NotFoundError
    from backend.app.datasource import tencent

    fired: List[Dict[str, Any]] = []
    try:
        quote = tencent.get_quote(rule["target"], "a")
    except NotFoundError:
        return []
    except Exception as exc:  # noqa: BLE001 - 源异常不产生事件也不产生错误
        logger.info("提醒取报价失败 %s：%s", rule["target"], type(exc).__name__)
        return []
    price = _to_float(quote.get("price"))
    pct = _to_float(quote.get("pct"))
    if rule["type"] == "price":
        if price is None:
            return []
        op, threshold = rule["op"], float(rule["value"])
        if _check_op(price, op, threshold):
            op_text = "触及" if op == "lte" else "达到"
            fired.append((rule, f"{rule['target']} {op_text} {threshold:g}（当前 {price:g}）", price))
    else:  # pct_change
        if pct is None:
            return []
        threshold = abs(float(rule["value"]))
        if abs(pct) >= threshold:
            fired.append((rule, f"{rule['target']} 单日涨跌 {pct:+.2f}%，超 ±{threshold:g}% 阈值", pct))
    return fired


def _crowding_eval(payload: Dict[str, Any], rule: Dict[str, Any]) -> List[Dict[str, Any]]:
    """拥挤度规则评估（M3 同一函数）。"""
    from backend.app.datasource import sector

    try:
        result = sector.get_crowding(rule["target"])
    except Exception as exc:  # noqa: BLE001
        logger.info("提醒取拥挤度失败 %s：%s", rule["target"], type(exc).__name__)
        return []
    score = _to_float(result.get("score"))
    if score is None:
        return []
    op, threshold = rule["op"], float(rule["value"])
    if _check_op(score, op, threshold):
        level = result.get("level") or ""
        op_text = "升至" if op == "gte" else "降至"
        return [(rule, f"{rule['target']} 拥挤度 {op_text} {score:g}（{level}，阈值 {threshold:g}）", score)]
    return []


def _valuation_eval(payload: Dict[str, Any], rule: Dict[str, Any]) -> List[Dict[str, Any]]:
    """估值分位规则评估（M2 同一接口；目标=指数名）。

    分位取 5 年窗口（pct5y）；规则阈值语义为「分位 ≥/≤ X%」。
    """
    from backend.app.datasource import valuation

    try:
        result = valuation.get_percentile()
    except Exception as exc:  # noqa: BLE001
        logger.info("提醒取估值分位失败：%s", type(exc).__name__)
        return []
    target = str(rule["target"]).strip()
    matched = None
    for item in result.get("indices") or []:
        if str(item.get("name")) == target:
            matched = item
            break
    if not matched or not matched.get("available"):
        return []
    pct = _to_float(matched.get("pct5y"))
    if pct is None:
        return []
    op, threshold = rule["op"], float(rule["value"])
    if _check_op(pct, op, threshold):
        op_text = "升至" if op == "gte" else "跌破"
        return [(rule, f"{target} PE 5 年分位 {op_text} {pct:g}%（阈值 {threshold:g}%）", pct)]
    return []


def _announcement_eval(payload: Dict[str, Any], rule: Dict[str, Any]) -> List[Dict[str, Any]]:
    """公告/财报披露规则评估（财报检索同源）。

    报告期窗口：取「自上次触发以来」的新公告，上限 5 条；
    财报披露日 = 标题命中年报/季报/业绩报告关键词（近似口径，caliber 写明）。
    """
    from backend.app.datasource import financial_report

    target = str(rule["target"]).strip()
    try:
        result = financial_report.search_filings(
            market="a", q=target, type_value="全部", since="week", page=1, page_size=10,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("提醒取公告失败 %s：%s", target, type(exc).__name__)
        return []
    items = result.get("list") or []
    rule_type = rule["type"]
    keywords = ("年度报告", "季度报告", "半年度报告", "业绩报告", "业绩快报", "业绩预告")
    fired: List[Dict[str, Any]] = []
    last_ts = float(rule.get("last_fired") or 0)
    for item in items:
        title = str(item.get("title") or "")
        pub = str(item.get("pubTime") or "")
        code = str(item.get("code") or "")
        # 匹配对象：代码精确或公司名包含（与检索语义一致）
        if code and code != target and target not in str(item.get("company") or ""):
            continue
        if rule_type == "report_date":
            if not any(k in title for k in keywords):
                continue
        # 只提醒上次触发之后发布的新公告（last_fired=0 视为首次，全量提醒一次）
        pub_ts = _pub_to_ts(pub)
        if pub_ts and last_ts and pub_ts <= last_ts:
            continue
        label = "财报披露" if rule_type == "report_date" else "新公告"
        fired.append((rule, f"{target} {label}：{title[:60]}", None))
        if len(fired) >= 5:  # 单轮上限，防公告密集时轰炸
            break
    return fired


def _pub_to_ts(pub: str) -> Optional[float]:
    """'2026-10-30 18:00:00' / '2026-10-30' → 时间戳。"""
    text = str(pub or "").strip()
    if not text:
        return None
    from datetime import datetime, timezone, timedelta

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text[: len(fmt) + 2].strip(), fmt)
            return dt.replace(tzinfo=timezone(timedelta(hours=8))).timestamp()
        except ValueError:
            continue
    return None


_EVALUATORS = {
    "price": _quote_eval,
    "pct_change": _quote_eval,
    "crowding": _crowding_eval,
    "valuation": _valuation_eval,
    "announcement": _announcement_eval,
    "report_date": _announcement_eval,
}


def evaluate(force: bool = False) -> Dict[str, Any]:
    """执行一轮规则评估（按类别节流 + 24h 去重），返回新增事件。

    Args:
        force: True 时跳过节流（手动「立即评估」）。

    Returns:
        Dict[str, Any]: {evaluated, new_events, skipped_reason, unread}。
    """
    with _EVAL_LOCK:
        payload = _read_all()
        rules = payload.get("rules") or []
        events = payload.get("events") or []
        now_ts = _now_ts()
        fired_total: List[Tuple[Dict[str, Any], str, Optional[float]]] = []
        evaluated_types: set[str] = set()
        skipped: Dict[str, int] = {}

        for rule in rules:
            if not rule.get("enabled", True):
                continue
            rule_type = str(rule.get("type"))
            kind = _rule_kind(rule_type)
            if not force and not _throttle_ok(kind):
                skipped[kind] = skipped.get(kind, 0) + 1
                continue
            # 同一类别本轮只实际评估一次（同类规则共享一次节流窗口）
            if kind in evaluated_types and not force:
                continue
            evaluator = _EVALUATORS.get(rule_type)
            if evaluator is None:
                continue
            try:
                fired = evaluator(payload, rule)
            except Exception as exc:  # noqa: BLE001 - 单规则失败不拖垮整轮
                logger.info("规则评估失败 %s：%s", rule.get("id"), type(exc).__name__)
                fired = []
            for rule_hit, message, value in fired:
                if not _dedup_ok(events, rule_hit, now_ts):
                    continue
                event = _fire_event(payload, rule_hit, message, value)
                events.append(event)
                fired_total.append((rule_hit, message, value))
            evaluated_types.add(kind)

        if fired_total:
            _write_all(payload)
        # 新事件直接取刚写入的尾部（_fire_event 已加入 payload.events）
        new_items = events[-len(fired_total):] if fired_total else []
        return {
            "evaluated": len(evaluated_types),
            "new_events": len(fired_total),
            "skipped": skipped,
            "unread": unread_count(),
            "events": [
                {
                    "id": item.get("id"),
                    "type_label": item.get("type_label"),
                    "target": item.get("target"),
                    "message": item.get("message"),
                    "value": item.get("value"),
                    "time": item.get("time"),
                }
                for item in new_items
            ],
            "as_of": _now_text(),
        }


def reset_eval_state() -> None:
    """清空节流状态（测试隔离用）。"""
    _EVAL_STATE.clear()
