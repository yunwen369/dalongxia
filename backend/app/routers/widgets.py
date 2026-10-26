"""组件元数据路由：/api/widgets 下发 widget_registry。

前端按 type/id 渲染区块；当前注册 9 个页面共 49 个区块元数据
（sectors 7 / stock 10 / sector-val 4 / news 5 / fund 6 / portfolio 4 / watchlist 2 / lhb 5 / yjb 6）；
dashboard、home 等页由仪表板配方渲染，查询时返回显式空态（P2-14）。
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Query

from backend.app import widgets_registry
from backend.app.core.errors import ok

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
        # P2-14：dashboard/home 等页不走注册表（仪表板由 RECIPES 渲染），
        # 按「空态与错误分离」口径返回成功 + 空列表 + 中文说明，不再抛 404
        return ok({
            "items": [],
            "total": 0,
            "grid": widgets_registry.GRID_SPAN,
            "categories": widgets_registry.LIBRARY_CATEGORIES,
            "empty": True,
            "note": "该页面暂不提供组件注册（仪表板请通过组件库与配方渲染）",
        })
    return ok(
        {
            "items": items,
            "total": len(items),
            "grid": widgets_registry.GRID_SPAN,
            "categories": widgets_registry.LIBRARY_CATEGORIES,
        }
    )
