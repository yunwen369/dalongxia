# -*- coding: utf-8 -*-
"""估值分位路由（第七轮 M2）：GET /api/valuation/percentile。

返回指数 / 全 A / 行业的 PE 分位与 ERP。分位口径见 `datasource/valuation.py` 模块头，
行业为横截面分位（`cross_pct`），不是纵向历史分位——字段名已区分，避免误读。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Query

from backend.app.core.errors import ErrorCode, api_error, ok
from backend.app.datasource import valuation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/valuation", tags=["估值"])


@router.get("/percentile")
def valuation_percentile(force: bool = Query(False, description="true=绕过缓存强制重取")) -> Dict[str, Any]:
    """估值分位总览（指数 / 全 A / 行业 + ERP）。

    Args:
        force: 是否绕过缓存。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/is_realtime。
    """
    try:
        payload = valuation.get_percentile(force=force)
    except Exception as exc:  # noqa: BLE001 - 上游异常统一转可读错误
        logger.warning("估值分位失败：%s", type(exc).__name__)
        return api_error(ErrorCode.UPSTREAM_ERROR, f"估值分位取数失败（{type(exc).__name__}），请稍后重试")
    return ok(payload)
