"""板块域路由：/api/sector 下 8 个接口（阶段 5.1）。

对应规范 v7.2 §8.6 与《设计基准说明书_阶段5.1》第十节 10.1：
- GET /industry  行业板块行情（同花顺 → 新浪行业 → 新浪证监会行业）
- GET /concept   概念板块行情（新浪概念 → 同花顺概念名录）
- GET /heatmap   热力图节点（由 industry/concept 派生，面积=abs(涨跌幅)）
- GET /summary   板块摘要（领涨/领跌/净流入 Top3 + 成交额集中度）
- GET /kline     板块指数 K 线（同花顺行业 → 同花顺概念 → 港美股走腾讯指数）
- GET /cons      板块成分股（新浪 → 申万 → 东财）
- GET /fundflow  板块资金流（同花顺，净额/流入/流出）
- GET /trend     多板块归一化走势（复用板块 K 线聚合）

统一返回 {data, error, error_code}；data 含 source/caliber/as_of/market_status/is_realtime，
前端据此渲染数据源与口径双徽章，降级命中时如实显示实际命中源（§8）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence

from fastapi import APIRouter, Query

from backend.app import config
from backend.app.core import cache
from backend.app.core.errors import ErrorCode, api_error, ok
from backend.app.core.resilient import with_fallback
from backend.app.datasource import sector as sector_provider
from backend.app.datasource import utils

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sector", tags=["板块"])

_ALLOWED_MARKETS = ("cn", "hk", "us")


def _bad_request(message: str) -> Dict[str, Any]:
    """参数错误响应。"""
    return api_error(ErrorCode.INVALID_PARAM, message)


def _failure(result: Dict[str, Any], empty_value: Any) -> Dict[str, Any]:
    """把降级链失败结果转为统一错误响应，空结构保持与成功态同构。"""
    try:
        code = ErrorCode(str(result.get("error_code")))
    except ValueError:
        code = ErrorCode.UPSTREAM_ERROR
    return api_error(code, result.get("error"), data=empty_value)


def _pack(
    data: Dict[str, Any],
    source: str,
    caliber: str,
    as_of: Optional[str],
    market_status: str,
    is_realtime: bool,
) -> Dict[str, Any]:
    """组装成功响应，统一附带数据源/口径/时间/市场状态。"""
    payload = dict(data)
    payload.update(
        {
            "source": source,
            "caliber": caliber,
            "as_of": as_of,
            "market_status": market_status,
            "is_realtime": bool(is_realtime),
        }
    )
    return ok(payload)


def _status_now() -> tuple[str, str, bool]:
    """按当前时间推断市场状态与是否实时（收盘后为快照，不伪装实时）。"""
    now = datetime.now()
    status, is_realtime = utils.classify_market_status(now, "a")
    return now.strftime("%Y-%m-%d %H:%M:%S"), status, is_realtime


def _run_chain(
    cache_key: str,
    ttl: int,
    candidates: Sequence[Any],
    empty_value: Any,
) -> Dict[str, Any]:
    """缓存 + 降级链执行。

    Args:
        cache_key: 缓存键（含全部入参）。
        ttl: 缓存秒数。
        candidates: [(源名, 口径, 无参函数), ...]。
        empty_value: 全部失败时的空结构。

    Returns:
        Dict[str, Any]: with_fallback 结果；缓存命中时来源/口径一并复用。
    """

    def loader() -> Dict[str, Any]:
        """实际取数入口，交给 with_fallback 逐源尝试。"""
        return with_fallback(candidates, empty_value=empty_value)

    return cache.get_or_load(cache_key, ttl, loader)


# =============================================================
# 一、行业 / 概念行情
# =============================================================
def _industry_cached(market: str) -> Dict[str, Any]:
    """行业行情（带缓存）；heatmap/summary 复用同一份缓存，避免重复请求源站。"""
    if market in ("hk", "us"):
        candidates: List[Any] = [
            ("腾讯", "境外简化建模：主要指数+代表个股（无板块专项通道）",
             lambda: sector_provider.get_overseas_items(market)),
        ]
    else:
        candidates = [
            ("同花顺", "同花顺/行业汇总（成交额与净流入单位=亿元）", sector_provider.get_industry_ths),
            ("新浪", "新浪/行业板块（成交额单位=元，已折算亿元；无主力净流入）",
             sector_provider.get_industry_sina),
            ("新浪证监会", "新浪/证监会行业（akshare 1.19.1 实测解析缺陷，命中即失败）",
             sector_provider.get_industry_csrc),
        ]
    return _run_chain(f"sector:industry:{market}", config.TTL["sector"], candidates, [])


def _concept_cached(market: str) -> Dict[str, Any]:
    """概念行情（带缓存）。"""
    if market in ("hk", "us"):
        candidates: List[Any] = [
            ("腾讯", "境外简化建模：港美股无免费概念板块通道", lambda: sector_provider.get_overseas_items(market)),
        ]
    else:
        candidates = [
            ("新浪", "新浪/概念板块（成交额单位=元，已折算亿元；无主力净流入）",
             sector_provider.get_concept_sina),
            ("同花顺", "同花顺/概念名录（仅名称，无行情，pct/amount 为空）",
             sector_provider.get_concept_ths),
        ]
    return _run_chain(f"sector:concept:{market}", config.TTL["sector"], candidates, [])


@router.get("/industry")
def sector_industry(market: str = Query("cn", description="cn/hk/us")) -> Dict[str, Any]:
    """行业板块行情列表（S-04 排行表数据源）。"""
    selected = (market or "cn").lower()
    if selected not in _ALLOWED_MARKETS:
        return _bad_request("market 仅支持 cn/hk/us")
    result = _industry_cached(selected)
    if result.get("error"):
        return _failure(result, {"items": [], "market": selected})
    items = result["data"] or []
    as_of, status, is_realtime = _status_now()
    return _pack(
        {"items": items, "count": len(items), "market": selected, "kind": "industry"},
        result.get("source") or "",
        result.get("caliber") or "",
        as_of,
        status,
        is_realtime,
    )


@router.get("/concept")
def sector_concept(market: str = Query("cn", description="cn/hk/us")) -> Dict[str, Any]:
    """概念板块行情列表（S-05 排行表数据源）。"""
    selected = (market or "cn").lower()
    if selected not in _ALLOWED_MARKETS:
        return _bad_request("market 仅支持 cn/hk/us")
    result = _concept_cached(selected)
    if result.get("error"):
        return _failure(result, {"items": [], "market": selected})
    items = result["data"] or []
    as_of, status, is_realtime = _status_now()
    return _pack(
        {"items": items, "count": len(items), "market": selected, "kind": "concept"},
        result.get("source") or "",
        result.get("caliber") or "",
        as_of,
        status,
        is_realtime,
    )


# =============================================================
# 二、热力图 / 摘要
# =============================================================
@router.get("/heatmap")
def sector_heatmap(
    market: str = Query("cn", description="cn/hk/us"),
    kind: str = Query("industry", description="industry/concept"),
) -> Dict[str, Any]:
    """热力图节点（S-03）。value=abs(涨跌幅)，pct 保留原值供颜色与标签使用。"""
    selected = (market or "cn").lower()
    if selected not in _ALLOWED_MARKETS:
        return _bad_request("market 仅支持 cn/hk/us")
    kind_value = (kind or "industry").lower()
    if kind_value not in ("industry", "concept"):
        return _bad_request("kind 仅支持 industry/concept")

    result = _concept_cached(selected) if kind_value == "concept" else _industry_cached(selected)
    if result.get("error"):
        return _failure(result, {"nodes": [], "market": selected, "kind": kind_value})
    nodes = sector_provider.heatmap_from_items(result["data"] or [])
    as_of, status, is_realtime = _status_now()
    return _pack(
        {"nodes": nodes, "count": len(nodes), "market": selected, "kind": kind_value},
        result.get("source") or "",
        result.get("caliber") or "",
        as_of,
        status,
        is_realtime,
    )


@router.get("/summary")
def sector_summary(market: str = Query("cn", description="cn/hk/us")) -> Dict[str, Any]:
    """板块摘要（S-06）：领涨/领跌/净流入 Top3 + 成交额集中度。"""
    selected = (market or "cn").lower()
    if selected not in _ALLOWED_MARKETS:
        return _bad_request("market 仅支持 cn/hk/us")
    result = _industry_cached(selected)
    if result.get("error"):
        return _failure(result, {"gainers": [], "losers": [], "inflow": [], "market": selected})
    summary = sector_provider.summary_from_items(result["data"] or [], selected)
    as_of, status, is_realtime = _status_now()
    return _pack(
        summary,
        result.get("source") or "",
        result.get("caliber") or "",
        as_of,
        status,
        is_realtime,
    )


# =============================================================
# 三、板块 K 线 / 成分股
# =============================================================
@router.get("/kline")
def sector_kline(
    board: str = Query(..., description="板块名，如 半导体"),
    period: str = Query("daily", description="daily/weekly/monthly"),
    market: str = Query("cn", description="cn/hk/us"),
) -> Dict[str, Any]:
    """板块指数 K 线（V-01）。"""
    if not board.strip():
        return _bad_request("board 参数不可为空")
    selected = (market or "cn").lower()
    if selected not in _ALLOWED_MARKETS:
        return _bad_request("market 仅支持 cn/hk/us")

    cache_key = f"sector:kline:{selected}:{board}:{period}"

    if selected in ("hk", "us"):
        candidates: List[Any] = [
            ("腾讯", "腾讯/指数日线（境外简化建模，板块名需命中主要指数）",
             lambda: sector_provider.get_sector_kline(board, period, selected)),
        ]
    else:
        candidates = [
            ("同花顺", "同花顺/板块指数日线（行业→概念，周月线由日线聚合）",
             lambda: sector_provider.get_sector_kline(board, period, selected)),
        ]

    def loader() -> Dict[str, Any]:
        """板块 K 线取数。"""
        return with_fallback(
            candidates,
            empty_value={"items": [], "board": board, "period": period},
            validator=lambda value: bool(value.get("items")),
        )

    result = cache.get_or_load(cache_key, config.TTL["kline"], loader)
    if result.get("error"):
        return _failure(result, {"items": [], "board": board, "period": period})
    data = result["data"] or {}
    items = data.get("items") or []
    as_of = f"{items[-1].get('date')} 15:00:00" if items else None
    return _pack(
        {
            "board": data.get("board", board),
            "period": data.get("period", period),
            "items": items,
            "model": data.get("model"),
            "index_code": data.get("index_code"),
        },
        result.get("source") or "",
        result.get("caliber") or "",
        as_of,
        "日线收盘",
        False,
    )


@router.get("/cons")
def sector_cons(
    board: str = Query(..., description="板块名"),
    market: str = Query("cn", description="cn/hk/us"),
) -> Dict[str, Any]:
    """板块成分股（V-03）。"""
    if not board.strip():
        return _bad_request("board 参数不可为空")
    selected = (market or "cn").lower()
    if selected not in _ALLOWED_MARKETS:
        return _bad_request("market 仅支持 cn/hk/us")

    cache_key = f"sector:cons:{selected}:{board}"

    if selected in ("hk", "us"):
        return api_error(
            ErrorCode.EMPTY_DATA,
            "港美股板块暂无免费成分股通道，已如实标注不可用。",
            data={"items": [], "board": board},
        )

    def loader() -> Dict[str, Any]:
        """成分股取数：新浪 → 申万 → 东财，三个独立候选便于徽章如实显示命中源。"""
        return with_fallback(
            [
                ("新浪", "新浪/板块成分（权重按流通市值占比）",
                 lambda: sector_provider.get_cons_sina(board)),
                ("申万", "申万/行业成分（一/二/三级行业名均可，权重按市值占比）",
                 lambda: sector_provider.get_cons_sw(board)),
                ("东财", "东财/板块成分（生产环境备用，本机 push2 被源站重置）",
                 lambda: sector_provider.get_cons_em(board)),
            ],
            empty_value={"items": [], "board": board},
            validator=lambda value: bool(value.get("items")),
        )

    result = cache.get_or_load(cache_key, config.TTL["kline"], loader)
    if result.get("error"):
        return _failure(result, {"items": [], "board": board})
    data = result["data"] or {}
    as_of, status, is_realtime = _status_now()
    return _pack(
        {
            "board": data.get("board", board),
            "items": data.get("items") or [],
            "source_hint": data.get("source_hint"),
        },
        result.get("source") or "",
        result.get("caliber") or "",
        as_of,
        status,
        is_realtime,
    )


# =============================================================
# 四、资金流 / 趋势
# =============================================================
@router.get("/fundflow")
def sector_fundflow(market: str = Query("cn", description="cn/hk/us")) -> Dict[str, Any]:
    """板块资金流向（S-07）。主力净额/流入/流出为真实值；超大单/大单源不提供，返回 None。"""
    selected = (market or "cn").lower()
    if selected not in _ALLOWED_MARKETS:
        return _bad_request("market 仅支持 cn/hk/us")
    if selected in ("hk", "us"):
        return api_error(
            ErrorCode.EMPTY_DATA,
            "港美股板块资金流暂无免费通道，已如实标注不可用。",
            data={"items": [], "market": selected},
        )

    def loader() -> Dict[str, Any]:
        """资金流取数（同花顺行业资金流）。"""
        return with_fallback(
            [("同花顺", "同花顺/行业资金流（单位=亿元；无超大单/大单分项）",
              sector_provider.get_fundflow)],
            empty_value=[],
        )

    result = cache.get_or_load(f"sector:fundflow:{selected}", config.TTL["fund_flow"], loader)
    if result.get("error"):
        return _failure(result, {"items": [], "market": selected})
    items = result["data"] or []
    as_of, status, is_realtime = _status_now()
    return _pack(
        {"items": items, "count": len(items), "market": selected},
        result.get("source") or "",
        result.get("caliber") or "",
        as_of,
        status,
        is_realtime,
    )


@router.get("/trend")
def sector_trend(
    boards: str = Query(..., description="逗号分隔板块名，最多 6 个"),
    days: int = Query(30, ge=5, le=120, description="取最近 N 个交易日"),
    market: str = Query("cn", description="cn/hk/us"),
) -> Dict[str, Any]:
    """多板块归一化走势对比（首个交易日=100），由板块 K 线聚合，不额外编造数据。"""
    selected = (market or "cn").lower()
    if selected not in _ALLOWED_MARKETS:
        return _bad_request("market 仅支持 cn/hk/us")
    names = [item.strip() for item in (boards or "").split(",") if item.strip()]
    if not names:
        return _bad_request("boards 参数不可为空")
    if len(names) > 6:
        return _bad_request("boards 最多支持 6 个板块")

    cache_key = f"sector:trend:{selected}:{','.join(names)}:{days}"

    def loader() -> Dict[str, Any]:
        """趋势取数：逐个板块复用 K 线，单板块失败不阻断整体。"""
        return with_fallback(
            [
                ("同花顺", "同花顺/板块指数日线归一化（首日=100）",
                 lambda: sector_provider.get_trend(names, days, selected)),
            ],
            empty_value={"series": [], "base": 100, "failures": names},
            validator=lambda value: bool(value.get("series")),
        )

    result = cache.get_or_load(cache_key, config.TTL["kline"], loader)
    if result.get("error"):
        return _failure(result, {"series": [], "boards": names})
    data = result["data"] or {}
    _, status, is_realtime = _status_now()
    return _pack(
        {
            "boards": names,
            "days": days,
            "base": data.get("base", 100),
            "series": data.get("series") or [],
            "failures": data.get("failures") or [],
        },
        result.get("source") or "",
        result.get("caliber") or "",
        None,
        status,
        is_realtime,
    )
