"""基金域路由：/api/fund 下 9 个接口（批次 5.3）。

对应规范 v7.2 §8.6 与基线03 §3.9（14 个接口中含 5 个养基宝接口，属阶段 7；本批实现 9 个）：
- GET /search       基金搜索（代码/简称/拼音）
- GET /realtime     最新净值与日涨幅（T+1 口径，D1 合规）
- GET /nav          净值历史（蛋卷→东财 lsjz）
- GET /holding      重仓股（东财季报）
- GET /info         基金档案（蛋卷 + 雪球）
- GET /performance  阶段业绩/排名/分年度/定投（蛋卷 derived）
- GET /drawdown     最大回撤（净值自算）
- GET /dividend     分红记录（东财 fhsp HTML）
- GET /position     持仓分布（蛋卷需登录，依规范禁用并如实标注）

统一返回 {data,error,error_code}；`source/caliber` 如实标注「T+1 公布净值」，不得伪装盘中实时。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query

from backend.app import config
from backend.app.core import cache
from backend.app.core.errors import ErrorCode, api_error, ok
from backend.app.datasource import fund as fund_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/fund", tags=["基金"])


def _bad(message: str) -> Dict[str, Any]:
    """参数错误响应。"""
    return api_error(ErrorCode.INVALID_PARAM, message)


def _not_found(message: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """未找到响应。"""
    return api_error(ErrorCode.NOT_FOUND, message, data=data or {})


def _pack(data: Dict[str, Any], source: str, caliber: str, as_of: Optional[str],
          market_status: str = "T+1 公布净值") -> Dict[str, Any]:
    """组装响应，附带数据源/口径/时间。"""
    payload = dict(data)
    payload.update({
        "source": source,
        "caliber": caliber,
        "as_of": as_of or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "market_status": market_status,
        "is_realtime": False,  # 场外基金本批无盘中估值通道，恒为 False
    })
    return ok(payload)


def _run(key: str, ttl: int, loader):
    """缓存执行，异常统一转中文错误。"""
    try:
        return cache.get_or_load(key, ttl, lambda: {"data": loader(), "error": None})
    except Exception as exc:  # noqa: BLE001 - 转结构化错误
        logger.info("基金接口失败：%s（%s）", key, type(exc).__name__)
        return {"data": None, "error": str(exc) or "数据源暂不可用", "error_type": type(exc).__name__}


@router.get("/search")
def fund_search(
    q: str = Query(..., description="代码/简称/拼音"),
    limit: int = Query(20, ge=1, le=50),
) -> Dict[str, Any]:
    """基金搜索。"""
    keyword = (q or "").strip()
    if not keyword:
        return _bad("q 参数不可为空")
    result = _run(f"fund:search:{keyword}:{limit}", config.TTL["search"],
                  lambda: fund_provider.get_search(keyword, limit))
    if result["error"]:
        return api_error(ErrorCode.UPSTREAM_ERROR, "基金名录暂不可用，请稍后点击刷新重试。",
                         data={"items": []})
    items = result["data"]
    return _pack({"items": items, "count": len(items), "keyword": keyword},
                 "天天基金", "天天基金全量名录本地过滤（代码/简称/拼音）", None, "名录快照")


@router.get("/realtime")
def fund_realtime(code: str = Query(..., description="6 位基金代码")) -> Dict[str, Any]:
    """最新净值与日涨幅（T+1 口径）。"""
    symbol = (code or "").strip()
    if not symbol:
        return _bad("code 参数不可为空")
    result = _run(f"fund:realtime:{symbol}", config.TTL["fund_nav"],
                  lambda: fund_provider.get_realtime(symbol))
    if result["error"]:
        return api_error(ErrorCode.UPSTREAM_ERROR, "基金净值暂不可用，请稍后点击刷新重试。",
                         data={"code": symbol})
    data = result["data"]
    return _pack(data, data.get("source") or "蛋卷", data.get("caliber") or "T+1 公布净值",
                 data.get("nav_date"))


@router.get("/nav")
def fund_nav(
    code: str = Query(..., description="6 位基金代码"),
    page: int = Query(1, ge=1, le=200),
    size: int = Query(30, ge=1, le=500),
) -> Dict[str, Any]:
    """净值历史。"""
    symbol = (code or "").strip()
    if not symbol:
        return _bad("code 参数不可为空")
    result = _run(f"fund:nav:{symbol}:{page}:{size}", config.TTL["fund_nav"],
                  lambda: fund_provider.get_nav(symbol, page, size))
    if result["error"]:
        return api_error(ErrorCode.UPSTREAM_ERROR, "净值历史暂不可用，请稍后点击刷新重试。",
                         data={"items": [], "code": symbol})
    data = result["data"]
    first = (data.get("items") or [{}])[0]
    return _pack(data, data.get("source") or "蛋卷", data.get("caliber") or "",
                 first.get("date"))


@router.get("/holding")
def fund_holding(code: str = Query(..., description="6 位基金代码")) -> Dict[str, Any]:
    """重仓股（季报口径）。"""
    symbol = (code or "").strip()
    if not symbol:
        return _bad("code 参数不可为空")
    result = _run(f"fund:holding:{symbol}", config.TTL["fund_holding"],
                  lambda: fund_provider.get_holding(symbol))
    if result["error"]:
        return api_error(ErrorCode.UPSTREAM_ERROR, "重仓数据暂不可用，请稍后点击刷新重试。",
                         data={"items": [], "code": symbol})
    data = result["data"]
    return _pack(data, data.get("source") or "东财季报", data.get("caliber") or "",
                 data.get("quarter"), "定期报告")


@router.get("/info")
def fund_info(code: str = Query(..., description="6 位基金代码")) -> Dict[str, Any]:
    """基金档案。"""
    symbol = (code or "").strip()
    if not symbol:
        return _bad("code 参数不可为空")
    result = _run(f"fund:info:{symbol}", config.TTL["fund_holding"],
                  lambda: fund_provider.get_info(symbol))
    if result["error"]:
        return api_error(ErrorCode.UPSTREAM_ERROR, "基金档案暂不可用，请稍后点击刷新重试。",
                         data={"code": symbol})
    data = result["data"]
    return _pack(data, data.get("source") or "蛋卷", data.get("caliber") or "",
                 data.get("end_date"), "档案快照")


@router.get("/performance")
def fund_performance(code: str = Query(..., description="6 位基金代码")) -> Dict[str, Any]:
    """阶段业绩与排名。"""
    symbol = (code or "").strip()
    if not symbol:
        return _bad("code 参数不可为空")
    result = _run(f"fund:performance:{symbol}", config.TTL["fund_performance"],
                  lambda: fund_provider.get_performance(symbol))
    if result["error"]:
        return api_error(ErrorCode.UPSTREAM_ERROR, "阶段业绩暂不可用，请稍后点击刷新重试。",
                         data={"periods": [], "code": symbol})
    data = result["data"]
    return _pack(data, data.get("source") or "蛋卷", data.get("caliber") or "",
                 data.get("end_date"))


@router.get("/drawdown")
def fund_drawdown(
    code: str = Query(..., description="6 位基金代码"),
    start: str = Query("", description="起始日期 YYYY-MM-DD"),
    end: str = Query("", description="结束日期 YYYY-MM-DD"),
) -> Dict[str, Any]:
    """最大回撤（净值自算）。"""
    symbol = (code or "").strip()
    if not symbol:
        return _bad("code 参数不可为空")
    result = _run(f"fund:drawdown:{symbol}:{start}:{end}", config.TTL["fund_performance"],
                  lambda: fund_provider.get_drawdown(symbol, start, end))
    if result["error"]:
        return api_error(ErrorCode.UPSTREAM_ERROR, "回撤计算暂不可用，请稍后点击刷新重试。",
                         data={"series": [], "code": symbol})
    data = result["data"]
    return _pack(data, data.get("source") or "自算", data.get("caliber") or "",
                 data.get("end"))


@router.get("/dividend")
def fund_dividend(code: str = Query(..., description="6 位基金代码")) -> Dict[str, Any]:
    """分红记录。"""
    symbol = (code or "").strip()
    if not symbol:
        return _bad("code 参数不可为空")
    result = _run(f"fund:dividend:{symbol}", config.TTL["fund_performance"],
                  lambda: fund_provider.get_dividend(symbol))
    if result["error"]:
        return api_error(ErrorCode.UPSTREAM_ERROR, "分红数据暂不可用，请稍后点击刷新重试。",
                         data={"items": [], "code": symbol})
    data = result["data"]
    first = (data.get("items") or [{}])[0]
    return _pack(data, data.get("source") or "东财", data.get("caliber") or "",
                 first.get("record_date"), "历史记录")


@router.get("/position")
def fund_position(code: str = Query(..., description="6 位基金代码")) -> Dict[str, Any]:
    """持仓分布：蛋卷端点需登录，依规范禁用（如实标注，不编造）。"""
    symbol = (code or "").strip()
    if not symbol:
        return _bad("code 参数不可为空")
    return api_error(
        ErrorCode.EMPTY_DATA,
        "基金持仓分布需登录蛋卷账号，依规范已禁用该通道；可查看「重仓股」替代。",
        data={"code": symbol, "items": [], "disabled": True,
              "reason": "蛋卷 /djapi/fund/position 需登录（实测 rc=300001）"},
    )
