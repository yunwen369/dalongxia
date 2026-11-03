# -*- coding: utf-8 -*-
"""筛选器域路由（第七轮 M9）：快照状态 / 条件查询 / 手动刷新。

契约要点：
- 统一 HTTP 200 + {data, error, error_code}（§6.3）；
- 快照未建库时 run 返回错误码 EMPTY_DATA + 中文说明（不以 0 只命中冒充）；
- 刷新为异步任务：refresh 立即返回，status 查进度。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.app.core.errors import ErrorCode, api_error, ok
from backend.app.datasource import screener

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/screener", tags=["筛选器"])


class ScreenerCondition(BaseModel):
    """单个筛选条件。"""

    factor: str = Field(..., description="因子键，如 pe / roe_min / debt_ratio")
    op: str = Field(..., description="gte（≥）或 lte（≤）")
    value: float = Field(..., description="数值阈值")


class ScreenerRunRequest(BaseModel):
    """筛选请求体。"""

    conditions: List[ScreenerCondition] = Field(default_factory=list, description="条件 AND 连接，最多 8 个")
    limit: int = Field(100, ge=1, le=500, description="返回行数上限")


@router.get("/status")
def screener_status() -> Dict[str, Any]:
    """返回快照状态（是否就绪 / 版本 / 覆盖数 / 构建进度 / 自动构建窗口）。

    Returns:
        dict: 快照元信息，含 ``ready`` / ``version`` / ``coverage`` / ``progress`` / ``auto_build``。
    """
    try:
        auto = screener.maybe_auto_build()  # 18:00 后惰性自动构建（当日未建过）
        meta = screener.get_snapshot_meta()
        meta["auto_build"] = auto
        return ok(meta)
    except Exception as exc:  # noqa: BLE001
        logger.warning("快照状态失败：%s", type(exc).__name__)
        return api_error(ErrorCode.INTERNAL_ERROR, f"快照状态读取失败（{type(exc).__name__}）")


@router.post("/refresh")
def screener_refresh() -> Dict[str, Any]:
    """手动刷新快照（异步启动，进度见 ``/status``）。

    构建中查询读上一版快照，不阻塞（用户裁决 4）。

    Returns:
        dict: ``{started, building, message}``；已有构建任务时 started=False。
    """
    try:
        result = screener.start_build_async(reason="manual")
        if not result.get("started"):
            return ok({**result, "message": "已有构建任务在进行，进度见状态接口"})
        return ok({**result, "message": "快照构建已启动（异步），进度见状态接口"})
    except Exception as exc:  # noqa: BLE001
        logger.warning("快照刷新启动失败：%s", type(exc).__name__)
        return api_error(ErrorCode.INTERNAL_ERROR, f"快照构建启动失败（{type(exc).__name__}）")


@router.post("/run")
def screener_run(request: ScreenerRunRequest) -> Dict[str, Any]:
    """执行条件筛选（只查本地快照，<1s）。

    Args:
        request: 筛选请求，含 conditions（条件数组，AND 关系）与 limit（返回上限）。

    Returns:
        dict: 命中行与统计；快照未建库返回 EMPTY_DATA，参数非法返回 INVALID_PARAM。
    """
    conditions = [item.model_dump() for item in request.conditions]
    try:
        payload = screener.run_screener(conditions, limit=request.limit)
        return ok(payload)
    except ValueError as exc:
        return api_error(ErrorCode.INVALID_PARAM, str(exc))
    except RuntimeError as exc:
        # 快照未建库：给中文说明而非 0 只命中（AC-M9 / 空错分离）
        return api_error(ErrorCode.EMPTY_DATA, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("筛选执行失败：%s", type(exc).__name__)
        return api_error(ErrorCode.INTERNAL_ERROR, f"筛选执行失败（{type(exc).__name__}）")
