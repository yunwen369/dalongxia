"""持仓域路由：/api/portfolio 下 5 个接口（批次 5.3）。

对应规范 v7.2 §8.4 与基线03 §3.8：
- GET    /portfolio            读取持仓（data/portfolio.json）
- POST   /portfolio            新增持仓
- PUT    /portfolio/{id}       更新持仓
- DELETE /portfolio/{id}       删除持仓
- GET    /portfolio/analysis   持仓 × 实时报价 → 盈亏、市场分布、币种折算

写操作只落本机 `data/portfolio.json`（不入 Git、不上传）；金额口径见 datasource/portfolio.py。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Body, Path

from backend.app.datasource import utils
from backend.app.core import cache
from backend.app.core.errors import ErrorCode, api_error, ok
from backend.app.datasource import portfolio as store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/portfolio", tags=["持仓"])


def _pack(data: Dict[str, Any], caliber: str, as_of: str = None) -> Dict[str, Any]:
    """组装响应（持仓为本地数据，source 固定「本地持仓」）。

    Args:
        data: 待包装或清洗的数据主体。
        caliber: 口径说明（展示在口径徽章）。
        as_of: 数据时间（北京时间，格式 YYYY-MM-DD HH:MM:SS）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    payload = dict(data)
    payload.update({
        "source": "本地持仓",
        "caliber": caliber,
        "as_of": as_of or utils.now_text(),
        "market_status": "本地快照",
        "is_realtime": False,
    })
    return ok(payload)


@router.get("")
@router.get("/")
def list_portfolio() -> Dict[str, Any]:
    """读取全部持仓。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    data = store.load()
    return _pack(
        {"items": data.get("items") or [], "count": len(data.get("items") or []),
         "updated_at": data.get("updated_at")},
        "本地 JSON（data/portfolio.json），单位成本与份额按用户录入",
        data.get("updated_at"),
    )


@router.post("")
@router.post("/")
def create_portfolio(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """新增持仓。

    Args:
        payload: 请求体（JSON 对象）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    try:
        item = store.add_item(payload)
    except ValueError as exc:
        return api_error(ErrorCode.INVALID_PARAM, str(exc))
    cache.clear_cache()  # 持仓变更后清空分析缓存，避免展示旧市值
    return _pack({"item": item}, "新增一条本地持仓")


@router.put("/{item_id}")
def update_portfolio(item_id: str = Path(...), payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """更新持仓。

    Args:
        item_id: 条目主键。
        payload: 请求体（JSON 对象）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    try:
        item = store.update_item(item_id, payload)
    except KeyError:
        return api_error(ErrorCode.NOT_FOUND, "未找到该持仓记录，请刷新后重试。")
    except ValueError as exc:
        return api_error(ErrorCode.INVALID_PARAM, str(exc))
    cache.clear_cache()
    return _pack({"item": item}, "按 id 更新本地持仓")


@router.delete("/{item_id}")
def delete_portfolio(item_id: str = Path(...)) -> Dict[str, Any]:
    """删除持仓。

    Args:
        item_id: 条目主键。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    try:
        store.remove_item(item_id)
    except KeyError:
        return api_error(ErrorCode.NOT_FOUND, "未找到该持仓记录，请刷新后重试。")
    cache.clear_cache()
    return _pack({"deleted": item_id}, "删除本地持仓")


@router.get("/analysis")
def portfolio_analysis() -> Dict[str, Any]:
    """持仓分析：本币市值/盈亏 + 人民币折算 + 市场分布。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """

    def loader() -> Dict[str, Any]:
        """逐条取价并计算（单条失败不阻断整体）。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
        return store.analyze()

    data = cache.get_or_load("portfolio:analysis", 30, loader)
    return _pack(
        data,
        data.get("caliber") or "市值=份额×最新价；人民币折算用新浪实时汇率",
        data.get("updated_at"),
    )


@router.get("/attribution")
def portfolio_attribution() -> Dict[str, Any]:
    """组合归因（第七轮 M4）：收益贡献 / 行业暴露 / 集中度 / 情景。

    与 `/analysis` 的分工：`/analysis` 回答「现在值多少」，本端点回答「赚亏从哪来、风险堆在哪」。
    共用同一份台账（data/portfolio.json），不另存数据，因此不会出现双源。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 caliber/as_of/is_realtime。
    """

    def loader() -> Dict[str, Any]:
        """计算归因（行情 60 秒缓存，避免频繁打上游）。

        Returns:
            dict: 归因结果。
        """
        return store.attribution()

    data = cache.get_or_load("portfolio:attribution", 60, loader)
    return _pack(
        data,
        data.get("caliber") or "权重=市值/合计；贡献=权重×盈亏率；HHI=Σ权重²",
        data.get("as_of"),
    )
