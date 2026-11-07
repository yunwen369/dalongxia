# -*- coding: utf-8 -*-
"""datasource 层公共小工具（第十轮验收整改 C17：消除 AST 重复函数体）。

抽自 alerts/macro/valuation 的 ``_to_float``、screener 的 ``_num``，以及
ashare 的 ``_default_range`` 与 sector 的 ``_default_kline_range``——
三组函数体逐字相同，此前各留一份副本（R17 红线：AST 重复体扫描须 0 命中）。

统一实现后语义取**最严**口径：``None`` / 空串 / 不可转换 / NaN / ±Inf 一律 None。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Optional, Tuple

from backend.app.datasource import utils


def to_float(value: Any) -> Optional[float]:
    """安全转 float，None/空串/不可转换/NaN/±Inf 一律返回 None。

    Args:
        value: 待转换值（数字 / 字符串 / None）。

    Returns:
        Optional[float]: 转换结果；异常值返回 None。
    """
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result


def default_range(days: int = 730) -> Tuple[str, str]:
    """返回默认起止日期（YYYYMMDD，北京时间取当日，非 UTC+8 宿主不移位）。

    Args:
        days: 向前回溯的自然日天数。

    Returns:
        Tuple[str, str]: (start, end)，均为 YYYYMMDD。
    """
    end = utils.today_bj()
    start = end - timedelta(days=days)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
