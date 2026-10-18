"""行情域路由：/api/market 下 7 个接口。

对应规范 v7.0 §8.6.1 / §8.7 与基线03 §3.3：
- GET /api/market/index        核心指数快照（A/港/美）
- GET /api/market/index-kline  指数 K 线
- GET /api/market/quote        单只股票/指数报价
- GET /api/market/kline        股票 K 线（东财→新浪→腾讯）
- GET /api/market/minute       分时（东财→新浪→腾讯）
- GET /api/market/search       智能联想（腾讯）
- GET /api/market/profile      个股概况（东财→巨潮；港美股用腾讯基础字段）

所有接口统一返回 {data,error,error_code}；data 内含 source/caliber/as_of/
market_status/is_realtime，前端据此渲染双徽章，不把收盘快照伪装成盘中实时。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from backend.app import config
from backend.app.core import cache
from backend.app.core.errors import ErrorCode, api_error, ok
from backend.app.core.resilient import with_fallback
from backend.app.datasource import ashare, tencent, utils

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/market", tags=["行情"])

_ALLOWED_MARKETS = ("a", "hk", "us")


def _bad_request(message: str) -> Dict[str, Any]:
    """返回参数错误响应。"""
    return api_error(ErrorCode.INVALID_PARAM, message)


def _failure(result: Dict[str, Any], empty_value: Any) -> Dict[str, Any]:
    """把 with_fallback 的失败结果转换为统一错误响应。"""
    try:
        code = ErrorCode(str(result.get("error_code")))
    except ValueError:
        code = ErrorCode.UPSTREAM_ERROR
    return api_error(code, result.get("error"), data=empty_value)


def _cached(key: str, ttl: int, loader) -> Dict[str, Any]:
    """缓存优先执行；缓存层已处理空值防污染与磁盘兜底。"""
    return cache.get_or_load(key, ttl, loader)


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


def _kline_status(items: List[Dict[str, Any]]) -> tuple[Optional[str], str, bool]:
    """K 线以最后一根日期作为数据时间，始终标记为日线收盘口径。"""
    if not items:
        return None, "时间未知", False
    last_date = str(items[-1].get("date", ""))
    as_of = f"{last_date} 15:00:00" if last_date else None
    return as_of, "日线收盘", False


@router.get("/index")
def market_index(market: str = Query("all", description="all/a/hk/us")) -> Dict[str, Any]:
    """核心指数快照。

    Args:
        market: all/a/hk/us，缺省 all。

    Returns:
        dict: {data:{items,source,caliber,as_of,market_status,is_realtime},error,error_code}
    """
    selected = (market or "all").lower()
    if selected not in ("all", "a", "hk", "us"):
        return _bad_request("market 仅支持 all/a/hk/us")
    cache_key = f"market:index:{selected}"

    def loader() -> Dict[str, Any]:
        candidates: List[Any] = [
            ("腾讯", "腾讯/指数快照（A/港/美）", lambda: tencent.get_index_quotes(selected))
        ]
        # 新浪 s_ 前缀仅覆盖 A股；腾讯不可用时允许降级为“仅A股”，但口径必须注明
        if selected == "a":
            candidates.append(("新浪", "新浪/指数快照（无开高低）", ashare.get_index_quotes_sina))
        else:
            candidates.append(
                ("新浪", "新浪/指数快照（仅A股；腾讯不可用时的部分降级）", ashare.get_index_quotes_sina)
            )
        return with_fallback(candidates, empty_value=[])

    result = _cached(cache_key, config.TTL["index_quote"], loader)
    if result.get("error"):
        return _failure(result, {"items": []})
    items = result["data"]
    # 多市场混合时取最晚时间作为 as_of；状态统一标“多市场快照”，不冒充实时
    as_of = max((item.get("quote_time") or "" for item in items), default="")
    return _pack(
        {"items": items},
        result["source"] or "",
        result["caliber"] or "",
        as_of or None,
        "多市场快照" if selected == "all" else (items[0].get("market_status") or "快照"),
        all(bool(item.get("is_realtime")) for item in items) if items else False,
    )


@router.get("/index-kline")
def market_index_kline(
    code: str = Query("000001", description="指数代码，如 000001/HSI/DJI"),
    period: str = Query("日k", description="日k/周k/月k"),
) -> Dict[str, Any]:
    """指数 K 线。"""
    try:
        normalized_code, _ = utils.index_code_to_tencent(code)
        utils.period_to_tencent(period)
    except ValueError as exc:
        return _bad_request(str(exc))
    cache_key = f"market:index-kline:{normalized_code}:{period}"

    def loader() -> Dict[str, Any]:
        tencent_candidate = ("腾讯", f"腾讯/指数{period}", lambda: tencent.get_index_kline(normalized_code, period))
        sina_candidate = ("新浪", f"新浪/指数{period}", lambda: ashare.get_index_kline_sina(normalized_code, period))
        # 美股指数腾讯 fqkline 仅返回最近 1 根，优先新浪历史；A股指数腾讯优先、新浪兜底
        if normalized_code in ("DJI", "IXIC", "INX"):
            candidates: List[Any] = [sina_candidate, tencent_candidate]
        else:
            candidates = [tencent_candidate]
            if normalized_code in ("000001", "399001", "399006", "000300"):
                candidates.append(sina_candidate)
        return with_fallback(candidates, empty_value=[])

    result = _cached(cache_key, config.TTL["kline"], loader)
    if result.get("error"):
        return _failure(result, {"items": [], "code": normalized_code, "period": period})
    items = result["data"]
    as_of, status, realtime = _kline_status(items)
    return _pack(
        {"items": items, "code": normalized_code, "period": period},
        result["source"] or "",
        result["caliber"] or "",
        as_of,
        status,
        realtime,
    )


@router.get("/quote")
def market_quote(
    symbol: str = Query(..., min_length=1, description="股票代码，如 600519"),
    market: str = Query("a", description="a/hk/us"),
) -> Dict[str, Any]:
    """单只股票报价。"""
    normalized_market = (market or "a").lower()
    if normalized_market not in _ALLOWED_MARKETS:
        return _bad_request("market 仅支持 a/hk/us")
    try:
        code, normalized_market, _ = utils.normalize_symbol(symbol, normalized_market)
    except ValueError as exc:
        return _bad_request(str(exc))
    cache_key = f"market:quote:{normalized_market}:{code}"

    def loader() -> Dict[str, Any]:
        candidates: List[Any] = [
            ("腾讯", "腾讯/实时快照（收盘后为最后成交快照）", lambda: tencent.get_quote(code, normalized_market))
        ]
        if normalized_market == "a":
            candidates.append(("新浪", "新浪/实时快照（收盘后为最后成交快照）", lambda: ashare.get_quote_sina(code)))
        return with_fallback(candidates, need_rows=False, empty_value={}, validator=lambda q: bool(q.get("price")))

    result = _cached(cache_key, config.TTL["realtime_quote"], loader)
    if result.get("error"):
        return _failure(result, {"quote": {}})
    quote = result["data"]
    return _pack(
        {"quote": quote},
        result["source"] or quote.get("source") or "",
        result["caliber"] or quote.get("caliber") or "",
        quote.get("quote_time"),
        quote.get("market_status") or "时间未知",
        bool(quote.get("is_realtime")),
    )


@router.get("/kline")
def market_kline(
    symbol: str = Query(..., min_length=1),
    market: str = Query("a", description="a/hk/us"),
    period: str = Query("日k", description="日k/周k/月k"),
    adjust: str = Query("qfq", description="qfq/hfq/不复权"),
) -> Dict[str, Any]:
    """股票 K 线（A股东财→新浪→腾讯；港美股腾讯）。"""
    normalized_market = (market or "a").lower()
    if normalized_market not in _ALLOWED_MARKETS:
        return _bad_request("market 仅支持 a/hk/us")
    try:
        code, normalized_market, _ = utils.normalize_symbol(symbol, normalized_market)
        utils.period_to_tencent(period)
        adjust_value = utils.normalize_adjust(adjust)
    except ValueError as exc:
        return _bad_request(str(exc))
    cache_key = f"market:kline:{normalized_market}:{code}:{period}:{adjust_value}"

    def loader() -> Dict[str, Any]:
        if normalized_market == "a":
            candidates: List[Any] = [
                ("东财", f"东财/{period}{'不复权' if not adjust_value else '前复权' if adjust_value == 'qfq' else '后复权'}",
                 lambda: ashare.get_kline_em(code, period, adjust_value)),
                ("新浪", f"新浪/{period}{'不复权' if not adjust_value else '前复权' if adjust_value == 'qfq' else '后复权'}",
                 lambda: ashare.get_kline_sina(code, period, adjust_value)),
                ("腾讯", f"腾讯/{period}{'不复权' if not adjust_value else '前复权' if adjust_value == 'qfq' else '后复权'}",
                 lambda: tencent.get_kline(code, normalized_market, period, adjust_value)),
            ]
        else:
            candidates = [
                ("腾讯", f"腾讯/{period}{'不复权' if not adjust_value else '前复权' if adjust_value == 'qfq' else '后复权'}",
                 lambda: tencent.get_kline(code, normalized_market, period, adjust_value))
            ]
        return with_fallback(candidates, empty_value=[])

    result = _cached(cache_key, config.TTL["kline"], loader)
    if result.get("error"):
        return _failure(result, {"items": [], "symbol": code, "market": normalized_market})
    items = result["data"]
    as_of, status, realtime = _kline_status(items)
    return _pack(
        {
            "items": items,
            "symbol": code,
            "market": normalized_market,
            "period": period,
            "adjust": adjust_value or "none",
        },
        result["source"] or "",
        result["caliber"] or "",
        as_of,
        status,
        realtime,
    )


@router.get("/minute")
def market_minute(
    symbol: str = Query(..., min_length=1),
    market: str = Query("a", description="a/hk/us"),
    period: str = Query("1min", description="1/5/15/30/60 分钟"),
) -> Dict[str, Any]:
    """分时数据（A股东财→新浪→腾讯；港美股腾讯）。"""
    normalized_market = (market or "a").lower()
    if normalized_market not in _ALLOWED_MARKETS:
        return _bad_request("market 仅支持 a/hk/us")
    period_value = str(period or "1min").lower().replace("min", "").replace("m", "")
    if period_value not in ("1", "5", "15", "30", "60"):
        return _bad_request("period 仅支持 1/5/15/30/60 分钟")
    try:
        code, normalized_market, _ = utils.normalize_symbol(symbol, normalized_market)
    except ValueError as exc:
        return _bad_request(str(exc))
    cache_key = f"market:minute:{normalized_market}:{code}:{period_value}"

    def loader() -> Dict[str, Any]:
        if normalized_market == "a":
            candidates: List[Any] = [
                ("东财", f"东财/{period_value}分钟", lambda: ashare.get_minute_em(code, period_value)),
                ("新浪", f"新浪/{period_value}分钟", lambda: ashare.get_minute_sina(code, period_value)),
                ("腾讯", "腾讯/1分钟", lambda: tencent.get_minute(code, normalized_market, period_value)),
            ]
        else:
            candidates = [
                ("腾讯", "腾讯/1分钟", lambda: tencent.get_minute(code, normalized_market, period_value))
            ]
        return with_fallback(candidates, empty_value=[])

    result = _cached(cache_key, config.TTL["minute"], loader)
    if result.get("error"):
        return _failure(result, {"items": [], "symbol": code, "market": normalized_market})
    items = result["data"]
    last = items[-1] if items else {}
    last_date = str(last.get("date", ""))
    last_time = str(last.get("time", "15:00"))
    as_of = f"{last_date} {last_time}:00" if last_date else None
    parsed = utils.parse_datetime(as_of) if as_of else None
    status, realtime = utils.classify_market_status(parsed, normalized_market)
    return _pack(
        {
            "items": items,
            "symbol": code,
            "market": normalized_market,
            "period": f"{period_value}min",
        },
        result["source"] or "",
        result["caliber"] or "",
        as_of,
        status,
        realtime,
    )


@router.get("/search")
def market_search(
    q: str = Query(..., min_length=1, description="代码/名称/拼音"),
    limit: int = Query(20, ge=1, le=50),
) -> Dict[str, Any]:
    """智能联想搜索。"""
    keyword = (q or "").strip()
    if not keyword:
        return _bad_request("搜索关键词不能为空")
    cache_key = f"market:search:{keyword}:{limit}"

    def loader() -> Dict[str, Any]:
        return with_fallback(
            [("腾讯", "腾讯/智能联想", lambda: tencent.get_search(keyword, limit))],
            empty_value=[],
        )

    result = _cached(cache_key, config.TTL["search"], loader)
    if result.get("error"):
        return _failure(result, {"items": []})
    return _pack(
        {"items": result["data"], "query": keyword},
        result["source"] or "",
        result["caliber"] or "",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "搜索",
        False,
    )


@router.get("/profile")
def market_profile(
    symbol: str = Query(..., min_length=1),
    market: str = Query("a", description="a/hk/us"),
) -> Dict[str, Any]:
    """个股概况（A股东财→巨潮；港美股腾讯基础字段）。"""
    normalized_market = (market or "a").lower()
    if normalized_market not in _ALLOWED_MARKETS:
        return _bad_request("market 仅支持 a/hk/us")
    try:
        code, normalized_market, _ = utils.normalize_symbol(symbol, normalized_market)
    except ValueError as exc:
        return _bad_request(str(exc))
    cache_key = f"market:profile:{normalized_market}:{code}"

    def loader() -> Dict[str, Any]:
        if normalized_market == "a":
            candidates: List[Any] = [
                ("东财", "东财/个股概况（日频）", lambda: ashare.get_profile_em(code)),
                ("东财F10", "东财F10/公司概况（日频）；市值/估值为腾讯快照", lambda: ashare.get_profile_em_f10(code)),
            ]
        else:
            def basic_profile() -> Dict[str, Any]:
                quote = tencent.get_quote(code, normalized_market)
                return {
                    "symbol": code,
                    "name": quote.get("name"),
                    "market": normalized_market,
                    "price": quote.get("price"),
                    "total_market_cap": quote.get("total_market_cap"),
                    "float_market_cap": quote.get("float_market_cap"),
                    "pe_ttm": quote.get("pe_ttm"),
                    "pb": quote.get("pb"),
                    "source": "腾讯",
                    "caliber": "腾讯/基础概况（港美股暂无 F10 行业字段）",
                    "as_of": quote.get("quote_time"),
                    "market_status": quote.get("market_status"),
                    "is_realtime": quote.get("is_realtime"),
                }
            candidates = [("腾讯", "腾讯/基础概况（港美股暂无 F10 行业字段）", basic_profile)]
        return with_fallback(candidates, need_rows=False, empty_value={}, validator=lambda p: bool(p.get("name")))

    result = _cached(cache_key, config.TTL["profile"], loader)
    if result.get("error"):
        return _failure(result, {"profile": {}})
    profile = result["data"]
    # 概况可能内部合并腾讯快照并在失败时改写口径，优先取 profile 内的实际口径
    return _pack(
        {"profile": profile},
        profile.get("source") or result["source"] or "",
        profile.get("caliber") or result["caliber"] or "",
        profile.get("as_of"),
        profile.get("market_status") or "日频",
        bool(profile.get("is_realtime")),
    )
