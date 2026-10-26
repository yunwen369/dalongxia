# -*- coding: utf-8 -*-
"""资金面路由：龙虎榜 · 北向资金（L 区块 · P1）。

契约（统一信封 {data,error,error_code}，data 含 source/caliber/as_of/market_status/is_realtime）：
- GET /api/moneyflow/northbound?days=20  北向近 N 个「有披露」交易日净流入（亿元）
- GET /api/moneyflow/north-top?limit=10  北向个股净买入 TopN（无当日数据时空态 + 说明）
- GET /api/moneyflow/lhb?date=&limit=100 龙虎榜明细（默认最近披露日）
- GET /api/moneyflow/lhb-institution     龙虎榜机构席位统计（机构买卖每日统计，亿元）
- GET /api/moneyflow/lhb-broker          龙虎榜活跃营业部（亿元）

空/错分离：上游「确实没有数据」返回成功 + items=[] + 中文说明；
通道异常返回 UPSTREAM_ERROR + 中文原因，绝不裸抛 500。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, Query

from backend.app import config
from backend.app.datasource import utils
from backend.app.core import cache
from backend.app.core.errors import ErrorCode, api_error, ok
from backend.app.datasource import moneyflow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/moneyflow", tags=["资金面"])


def _bad(message: str) -> Dict[str, Any]:
    """参数错误响应。

    Args:
        message: 中文提示。

    Returns:
        dict: 统一信封的错误响应。
    """
    return api_error(ErrorCode.INVALID_PARAM, message)


def _lhb_like(*, cache_key: str, loader, empty_source: str, empty_caliber: str,
              error_message: str, extra: Dict[str, Any]) -> Dict[str, Any]:
    """龙虎榜三接口共用的取数与空/错分离封装。

    Args:
        cache_key: 缓存键（含日期与条数）。
        loader: 实际取数函数。
        empty_source: 空态下登记的源标识。
        empty_caliber: 空态下登记的口径说明。
        error_message: 通道异常时的中文提示。
        extra: 需要原样回带的请求参数（如 date）。

    Returns:
        dict: 统一信封响应。
    """
    try:
        payload = cache.get_or_load(cache_key, config.TTL.get("lhb", 3600), loader)
    except ValueError as exc:
        # 上游「确实没有披露」：成功 + 空列表 + 中文说明（空/错分离）
        return ok({
            **extra,
            "items": [],
            "total": 0,
            "note": str(exc) or moneyflow.LHB_EMPTY_NOTE,
            "source": empty_source,
            "caliber": empty_caliber,
            "as_of": utils.now_text(),
            "market_status": "盘后披露",
            "is_realtime": False,
        })
    except Exception as exc:  # noqa: BLE001 - 通道异常
        logger.warning("龙虎榜取数失败（%s）：%s（%s）", cache_key, type(exc).__name__, exc)
        return api_error(ErrorCode.UPSTREAM_ERROR, error_message, data={**extra, "items": []})
    return ok({
        **payload,
        "as_of": utils.now_text(),
        "market_status": "盘后披露",
        "is_realtime": False,
    })


@router.get("/northbound")
def northbound(days: int = Query(20, ge=5, le=60, description="交易日数量")) -> Dict[str, Any]:
    """北向资金近 N 个有披露交易日净流入。

    Args:
        days: 交易日数量（5–60）。

    Returns:
        dict: 统一信封；data.items 为 [{date,net,buy,sell}]（亿元）。
    """
    def loader() -> Dict[str, Any]:
        """带缓存的北向历史取数。

        Returns:
            dict: moneyflow.get_northbound 的载荷。
        """
        return moneyflow.get_northbound(days)

    try:
        payload = cache.get_or_load("moneyflow:northbound", config.TTL.get("northbound", 3600), loader)
    except Exception as exc:  # noqa: BLE001 - 上游异常统一中文错误
        logger.warning("北向资金取数失败：%s（%s）", type(exc).__name__, exc)
        return api_error(ErrorCode.UPSTREAM_ERROR, "北向资金数据暂不可用（东财通道异常），请稍后重试。",
                         data={"items": [], "days": days})
    return ok({
        **payload,
        "as_of": utils.now_text(),
        "market_status": "日频收盘",
        "is_realtime": False,
    })


@router.get("/north-top")
def north_top(limit: int = Query(10, ge=1, le=30, description="返回条数")) -> Dict[str, Any]:
    """北向个股净买入 TopN。

    Args:
        limit: 返回条数（1–30）。

    Returns:
        dict: 统一信封；无当日数据时 items=[] + note。
    """
    try:
        payload = cache.get_or_load(
            f"moneyflow:north-top:{limit}", config.TTL.get("north_top", 3600),
            lambda: moneyflow.get_north_top(limit),
        )
    except Exception as exc:  # noqa: BLE001 - 上游异常统一中文错误
        logger.warning("北向个股排行取数失败：%s（%s）", type(exc).__name__, exc)
        return api_error(ErrorCode.UPSTREAM_ERROR, "北向个股排行暂不可用，请稍后重试。",
                         data={"items": [], "limit": limit})
    return ok({
        **payload,
        "as_of": utils.now_text(),
        "market_status": "日频收盘",
        "is_realtime": False,
    })


@router.get("/lhb")
def lhb(date: str = Query("", description="YYYY-MM-DD；留空取最近披露日"),
        limit: int = Query(100, ge=1, le=300, description="返回条数")) -> Dict[str, Any]:
    """龙虎榜当日明细。

    Args:
        date: 指定日期（留空取最近披露日）。
        limit: 返回条数（1–300）。

    Returns:
        dict: 统一信封；无披露时 items=[] + note。
    """
    if date:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return _bad("date 需为 YYYY-MM-DD 格式。")
    return _lhb_like(
        cache_key=f"moneyflow:lhb:{date or 'latest'}:{limit}",
        loader=lambda: moneyflow.get_lhb(date, limit),
        empty_source="东财 datacenter / stock_lhb_detail_em",
        empty_caliber="龙虎榜当日明细（净买额/买入额/卖出额单位=亿元）",
        error_message="龙虎榜数据暂不可用（东财通道异常），请稍后重试。",
        extra={"date": date},
    )


@router.get("/lhb-institution")
def lhb_institution(date: str = Query("", description="YYYY-MM-DD；留空取最近披露日"),
                    limit: int = Query(50, ge=1, le=200, description="返回条数")) -> Dict[str, Any]:
    """龙虎榜机构席位统计（机构买卖每日统计）。

    Args:
        date: 指定日期（留空取最近披露日）。
        limit: 返回条数（1–200）。

    Returns:
        dict: 统一信封；data.items 含买方/卖方机构数与机构净买额（亿元）。
    """
    if date:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return _bad("date 需为 YYYY-MM-DD 格式。")
    return _lhb_like(
        cache_key=f"moneyflow:lhb-inst:{date or 'latest'}:{limit}",
        loader=lambda: moneyflow.get_lhb_institution(date, limit),
        empty_source="东财 datacenter / stock_lhb_jgmmtj_em",
        empty_caliber="龙虎榜机构席位统计（金额单位=亿元）",
        error_message="龙虎榜机构席位暂不可用（东财通道异常），请稍后重试。",
        extra={"date": date},
    )


@router.get("/lhb-broker")
def lhb_broker(date: str = Query("", description="YYYY-MM-DD；留空取最近披露日"),
               limit: int = Query(50, ge=1, le=200, description="返回条数")) -> Dict[str, Any]:
    """龙虎榜活跃营业部统计。

    Args:
        date: 指定日期（留空取最近披露日）。
        limit: 返回条数（1–200）。

    Returns:
        dict: 统一信封；data.items 含营业部买卖金额与净额（亿元）。
    """
    if date:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return _bad("date 需为 YYYY-MM-DD 格式。")
    return _lhb_like(
        cache_key=f"moneyflow:lhb-broker:{date or 'latest'}:{limit}",
        loader=lambda: moneyflow.get_lhb_broker(date, limit),
        empty_source="东财 datacenter / stock_lhb_hyyyb_em",
        empty_caliber="龙虎榜活跃营业部（金额单位=亿元）",
        error_message="龙虎榜营业部数据暂不可用（东财通道异常），请稍后重试。",
        extra={"date": date},
    )
