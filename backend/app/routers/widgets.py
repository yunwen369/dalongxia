"""组件元数据路由：/api/widgets 下发 widget_registry。

前端按 type/id 渲染区块；本批仅下发阶段 5.1 三个页面的 21 个区块元数据。
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Query

from backend.app import widgets_registry
from backend.app.core.errors import api_error, ok
from backend.app.core.errors import ErrorCode

router = APIRouter(prefix="/api", tags=["组件"])


@router.get("/widgets")
def list_widgets(page: str = Query("", description="按页面筛选，如 #/sectors；留空返回全部")) -> Dict[str, Any]:
    """返回组件注册表。

    Args:
        page: 可选页面路由筛选。

    Returns:
        dict: {data:{items,total,grid},error,error_code}
    """
    items = widgets_registry.by_page(page) if page else widgets_registry.all_widgets()
    if page and not items:
        return api_error(ErrorCode.NOT_FOUND, "该页面暂无组件注册", data={"items": [], "total": 0})
    return ok(
        {
            "items": items,
            "total": len(items),
            "grid": widgets_registry.GRID_SPAN,
        }
    )
