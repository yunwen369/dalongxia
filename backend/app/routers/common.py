# -*- coding: utf-8 -*-
"""路由层公共帮手——RF-20261112-01 阶段 5。

收口「服务结果 → 统一信封」的重复管道。此前 40 处端点各自书写
``if result.get("error"): return failure_pack(...)`` +
``pack_success(data, result.get("source") or "", result.get("caliber") or "", ...)``；
五字段口径若再变化（如新增第 7 个 data 公共字段），需改 40 处。

现在：口径变化只改本模块 ``pack_result`` 一处，全部端点生效。
判据：core/errors.py 仍是信封唯一实现层；本模块只是调用侧的收口薄层，
不新增任何口径规则。
"""
from typing import Any, Dict, Optional

from backend.app.core.errors import failure_pack, pack_success


def pack_result(
    result: Optional[Dict[str, Any]],
    empty_value: Any,
    as_of: Optional[str] = None,
    market_status: str = "本地态",
    is_realtime: bool = False,
    data: Any = None,
    caliber: Optional[str] = None,
) -> Dict[str, Any]:
    """服务结果 → 统一信封。

    Args:
        result: 服务层结果（``cache.get_or_load`` 返回的 dict）。
            为空或带 ``error`` 键时走 ``failure_pack``。
        empty_value: 空态兜底载荷（端点特有语义，如 ``{"symbol": code}``）。
        as_of: 数据时间文案（端点特有，如 ``"{periods[0]} 报告期"``）。
        market_status: 市场状态文案。
        is_realtime: 是否盘中实时。
        data: 显式载荷；缺省取 ``result.get("data") or {}``。
        caliber: 显式口径文案；缺省取 ``result.get("caliber") or ""``
            （端点口径为静态文案时传入，如 news 的三源合并说明）。

    Returns:
        dict: 统一信封 ``{data, error, error_code}``。
    """
    if not result or result.get("error"):
        return failure_pack(result or {}, empty_value)
    payload = data if data is not None else (result.get("data") or {})
    caliber_text = result.get("caliber") or "" if caliber is None else caliber
    return pack_success(
        payload,
        result.get("source") or "",
        caliber_text,
        as_of,
        market_status,
        is_realtime,
    )
