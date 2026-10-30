# -*- coding: utf-8 -*-
"""宏观域路由（第七轮 M1）：GET /api/macro/overview。

设计要点：
- 单个大接口返回「指标卡 + 周期象限 + 序列」，避免前端并发 9 个请求（§11.4 性能）；
- `force=1` 绕过 24h 缓存（用户手动刷新）；
- 单指标失败不影响整体，失败项带 `available=false` + `reason`（§6.3 空错分离）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Query

from backend.app.core.errors import ErrorCode, api_error, ok
from backend.app.datasource import macro

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/macro", tags=["宏观"])


@router.get("/overview")
def macro_overview(force: bool = Query(False, description="true=绕过缓存强制重取")) -> Dict[str, Any]:
    """宏观总览：PMI / CPI / PPI / M2 / 国债收益率 + 周期象限判定。

    Args:
        force: 是否绕过缓存。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/is_realtime。
    """
    try:
        payload = macro.get_overview(force=force)
    except Exception as exc:  # noqa: BLE001 - 上游异常统一转可读错误
        logger.warning("宏观总览失败：%s", type(exc).__name__)
        return api_error(ErrorCode.UPSTREAM_ERROR, f"宏观数据取数失败（{type(exc).__name__}），请稍后重试")
    return ok(payload)
