# -*- coding: utf-8 -*-
"""提醒域路由（第七轮 M6）：规则 CRUD / 事件 / 评估 / 未读数。

契约要点：
- 统一 HTTP 200 + {data, error, error_code}（§6.3）；
- 提醒仅应用内：本路由不涉及任何系统通知通道（用户裁决 5）；
- 评估由前端铃铛轮询驱动（60s 节流在后端），也可手动 evaluate?force=true。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from backend.app.core.errors import ErrorCode, api_error, ok
from backend.app.datasource import alerts

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/alerts", tags=["提醒"])


class AlertRuleIn(BaseModel):
    """新增规则请求体。"""

    type: str = Field(..., description="price/pct_change/valuation/crowding/announcement/report_date/subscription（R15 订阅流）")
    target: str = Field(..., description="对象：股票代码、板块名或指数名；subscription 填板块名")
    op: Optional[str] = Field(None, description="gte（≥）/ lte（≤），比较类规则必填")
    value: Optional[float] = Field(None, description="数值阈值，比较类规则必填")
    note: str = Field("", description="备注")


class AlertRulePatch(BaseModel):
    """更新规则请求体（部分字段）。"""

    op: Optional[str] = None
    value: Optional[float] = None
    note: Optional[str] = None
    target: Optional[str] = None
    enabled: Optional[bool] = None


@router.get("")
def alerts_root() -> Dict[str, Any]:
    """返回规则列表与未读数（铃铛轮询主接口）。

    Returns:
        dict: ``{rules, unread, type_labels, as_of}``；异常时返回统一错误信封。
    """
    try:
        payload = {
            "rules": alerts.list_rules(),
            "unread": alerts.unread_count(),
            "type_labels": alerts.RULE_TYPE_LABELS,
            "as_of": _now(),
        }
        return ok(payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("规则列表失败：%s", type(exc).__name__)
        return api_error(ErrorCode.INTERNAL_ERROR, f"提醒规则读取失败（{type(exc).__name__}）")


@router.post("")
def alerts_add(rule: AlertRuleIn) -> Dict[str, Any]:
    """新增提醒规则。

    Args:
        rule: 规则入参（type / target / op / value / note / enabled）。

    Returns:
        dict: 已落盘的规则；参数不合法返回 INVALID_PARAM。
    """
    try:
        created = alerts.add_rule(rule.model_dump())
        return ok(created)
    except ValueError as exc:
        return api_error(ErrorCode.INVALID_PARAM, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("新增规则失败：%s", type(exc).__name__)
        return api_error(ErrorCode.INTERNAL_ERROR, f"新增规则失败（{type(exc).__name__}）")


@router.patch("/{rule_id}")
def alerts_update(rule_id: str, patch: AlertRulePatch) -> Dict[str, Any]:
    """更新规则（启用/停用、阈值、备注等）。

    Args:
        rule_id: 规则 ID。
        patch: 待更新字段；值为 None 表示不修改。

    Returns:
        dict: 更新后的规则；不存在或参数不合法返回错误信封。
    """
    fields = {k: v for k, v in patch.model_dump().items() if v is not None}
    try:
        updated = alerts.update_rule(rule_id, **fields)
        return ok(updated)
    except ValueError as exc:
        return api_error(ErrorCode.INVALID_PARAM, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("更新规则失败：%s", type(exc).__name__)
        return api_error(ErrorCode.INTERNAL_ERROR, f"更新规则失败（{type(exc).__name__}）")


@router.delete("/{rule_id}")
def alerts_delete(rule_id: str) -> Dict[str, Any]:
    """删除规则（历史事件保留）。

    Args:
        rule_id: 规则 ID。

    Returns:
        dict: ``{deleted}``；规则不存在返回 NOT_FOUND。
    """
    try:
        removed = alerts.delete_rule(rule_id)
        if not removed:
            return api_error(ErrorCode.NOT_FOUND, f"规则不存在：{rule_id}")
        return ok({"deleted": rule_id})
    except Exception as exc:  # noqa: BLE001
        logger.warning("删除规则失败：%s", type(exc).__name__)
        return api_error(ErrorCode.INTERNAL_ERROR, f"删除规则失败（{type(exc).__name__}）")


@router.get("/events")
def alerts_events(
    unread_only: bool = Query(False, description="true=只看未读"),
    limit: int = Query(100, ge=1, le=200),
) -> Dict[str, Any]:
    """返回事件列表（未读在前、时间倒序）与未读数。

    Args:
        unread_only: True 时只返回未读事件。
        limit: 返回条数上限（1–200）。

    Returns:
        dict: ``{events, unread, as_of}``。
    """
    try:
        events = alerts.list_events(unread_only=unread_only, limit=limit)
        return ok({"events": events, "unread": alerts.unread_count(), "as_of": _now()})
    except Exception as exc:  # noqa: BLE001
        logger.warning("事件列表失败：%s", type(exc).__name__)
        return api_error(ErrorCode.INTERNAL_ERROR, f"提醒事件读取失败（{type(exc).__name__}）")


@router.post("/events/read")
def alerts_mark_read(event_id: Optional[str] = Query(None, description="事件 ID；缺省=全部已读")) -> Dict[str, Any]:
    """标记已读（单条或全部）。

    Args:
        event_id: 事件 ID；缺省表示全部标记已读。

    Returns:
        dict: ``{marked, unread}``，marked 为实际标记条数。
    """
    try:
        changed = alerts.mark_read(event_id)
        return ok({"marked": changed, "unread": alerts.unread_count()})
    except Exception as exc:  # noqa: BLE001
        logger.warning("标记已读失败：%s", type(exc).__name__)
        return api_error(ErrorCode.INTERNAL_ERROR, f"标记已读失败（{type(exc).__name__}）")


@router.post("/evaluate")
def alerts_evaluate(force: bool = Query(False, description="true=跳过节流立即评估")) -> Dict[str, Any]:
    """执行一轮规则评估（节流：行情 60s / 公告 10min / 宏观类 6h）。

    Args:
        force: True 时跳过节流，立即评估。

    Returns:
        dict: {evaluated, new_events, events, unread}；新增事件供 toast 展示。
    """
    try:
        result = alerts.evaluate(force=force)
        return ok(result)
    except Exception as exc:  # noqa: BLE001
        logger.warning("规则评估失败：%s", type(exc).__name__)
        return api_error(ErrorCode.INTERNAL_ERROR, f"规则评估失败（{type(exc).__name__}）")


def _now() -> str:
    """返回当前北京时间文本（路由层与数据层同源）。

    Returns:
        str: ``YYYY-MM-DD HH:MM:SS``。
    """
    from backend.app.datasource import utils

    return utils.now_text(utils.bj_now())
