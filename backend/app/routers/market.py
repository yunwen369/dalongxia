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
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from backend.app import config
from backend.app.datasource import utils
from backend.app.core import cache
from backend.app.core.errors import pack_success, ErrorCode, NonRetryableError, api_error, bad_request, failure_pack
from backend.app.core.resilient import circuit_open, trip_circuit, with_fallback
from backend.app.datasource import ashare, overseas, tencent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/market", tags=["行情"])

# 行情/个股域白名单：a=A股、hk=港股、us=美股
_ALLOWED_MARKETS = utils.MARKET_WHITELIST_QUOTE


def _symbol_missing(code: str, market: str) -> bool:
    """存在性闸门：判断代码在源站是否确实查无此物（P1-03）。

    腾讯单只报价是本项目最快的存在性探针（实测 0.1~0.3 秒）。明确「不存在」时
    直接返回 NOT_FOUND，避免降级链在每个源上重试退避（修复前 9~24 秒）。

    Args:
        code: 归一化后的证券代码。
        market: a/hk/us。

    Returns:
        bool: True=确认不存在；False=存在或无法判定（继续正常取数）。
    """
    if not config.PROVIDER_SWITCHES.get("tencent", True):
        return False  # 探测源被禁用时不做闸门，交由正常降级链
    return tencent.symbol_exists(code, market) is False


def _cached(key: str, ttl: int, loader) -> Dict[str, Any]:
    """缓存优先执行；缓存层已处理空值防污染与磁盘兜底。

    Args:
        key: 缓存键 / 字典键。
        ttl: 缓存有效期（秒）。
        loader: 缓存未命中时的加载函数（返回数据或 {data, error}）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    return cache.get_or_load(key, ttl, loader)


def _kline_status(items: List[Dict[str, Any]]) -> tuple[Optional[str], str, bool]:
    """K 线以最后一根日期作为数据时间，始终标记为日线收盘口径。

    Args:
        items: 记录列表。

    Returns:
        tuple[Optional[str], str, bool]: 计算结果。
    """
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
    selected = utils.normalize_market(market, allow_all=True, default="all")
    if selected is None:
        return bad_request(utils.market_error_message(allow_all=True))
    cache_key = f"market:index:{selected}"

    def loader() -> Dict[str, Any]:
        """指数快照加载器：腾讯优先，A股可降级新浪（口径注明无开高低）。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
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
        # key：记住上次命中的源并隔离失效源（P1-04）
        return with_fallback(candidates, empty_value=[], key="market:index")

    result = _cached(cache_key, config.TTL["index_quote"], loader)
    if result.get("error"):
        return failure_pack(result, {"items": []})
    items = result["data"]
    # 多市场混合时取最晚时间作为 as_of；状态统一标“多市场快照”，不冒充实时
    as_of = max((item.get("quote_time") or "" for item in items), default="")
    return pack_success(
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
    """指数 K 线。

    Args:
        code: 指数代码，如 000001/HSI/DJI。
        period: 日k/周k/月k。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    try:
        normalized_code, _ = utils.index_code_to_tencent(code)
        utils.period_to_tencent(period)
    except ValueError as exc:
        return bad_request(str(exc))
    cache_key = f"market:index-kline:{normalized_code}:{period}"

    def loader() -> Dict[str, Any]:
        """指数 K 线加载器：美股指数优先新浪（腾讯 fqkline 仅 1 根），A股腾讯优先。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
        tencent_candidate = ("腾讯", f"腾讯/指数{period}", lambda: tencent.get_index_kline(normalized_code, period))
        sina_candidate = ("新浪", f"新浪/指数{period}", lambda: ashare.get_index_kline_sina(normalized_code, period))
        # 美股指数腾讯 fqkline 仅返回最近 1 根，优先新浪历史；A股指数腾讯优先、新浪兜底
        if normalized_code in ("DJI", "IXIC", "INX"):
            candidates: List[Any] = [sina_candidate, tencent_candidate]
        else:
            candidates = [tencent_candidate]
            if normalized_code in ("000001", "399001", "399006", "000300"):
                candidates.append(sina_candidate)
        # key：指数 K 线冷启动避免反复先撞失效源（P1-04）
        return with_fallback(candidates, empty_value=[], key=f"market:index-kline:{normalized_code}")

    result = _cached(cache_key, utils.kline_cache_ttl(), loader)
    if result.get("error"):
        return failure_pack(result, {"items": [], "code": normalized_code, "period": period})
    items = result["data"]
    as_of, status, realtime = _kline_status(items)
    return pack_success(
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
    """单只股票报价。

    Args:
        symbol: 股票代码，如 600519。
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。

    Raises:
        NonRetryableError: 仅降级链内部使用（东财快照熔断时由内层函数抛出，不向外抛出）。
    """
    normalized_market = utils.normalize_market(market)
    if normalized_market is None:
        return bad_request(utils.market_error_message())
    try:
        code, normalized_market, _ = utils.normalize_symbol(symbol, normalized_market)
    except ValueError as exc:
        return bad_request(str(exc))
    # 存在性闸门（P1-03）：确认查无此物立即 NOT_FOUND，不进降级链
    if _symbol_missing(code, normalized_market):
        return api_error(
            ErrorCode.NOT_FOUND,
            f"未找到代码 {code} 的报价，请检查代码或市场后重试。",
            data={"quote": {}, "symbol": code, "market": normalized_market},
        )
    cache_key = f"market:quote:{normalized_market}:{code}"

    def loader() -> Dict[str, Any]:
        """个股报价加载器：A股腾讯→新浪；港美股东财（生产候选）→腾讯。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。

        Raises:
            NonRetryableError: 由内层东财快照在熔断期抛出，最终由 with_fallback 捕获。
        """
        candidates: List[Any] = [
            ("腾讯", "腾讯/实时快照（收盘后为最后成交快照）", lambda: tencent.get_quote(code, normalized_market))
        ]
        if normalized_market == "a":
            candidates.append(("新浪", "新浪/实时快照（收盘后为最后成交快照）", lambda: ashare.get_quote_sina(code)))
        else:
            # 港美股：东财快照（生产候选，本机 push2 被重置）→ 腾讯。
            # 东财快照需拉全市场（数千行），失效时单次要等 60 秒；加进程级熔断，
            # 冷却期内直接跳过（§6.5），避免每轮 TTL 过期都卡一分钟。
            def _eastmoney_quote() -> Dict[str, Any]:
                """东财港美股快照（带熔断）。

                Returns:
                    Dict[str, Any]: 东财快照报价字段（price/prev_close/amount 等）。

                Raises:
                    NonRetryableError: 熔断冷却期内直接跳过，或本次取数失败（同时触发熔断）。
                """
                circuit_key = f"eastmoney:quote:{normalized_market}"
                if circuit_open(circuit_key):
                    raise NonRetryableError("东财港美股快照处于熔断期，已跳过")
                try:
                    return overseas.get_quote_em(code, normalized_market)
                except Exception:  # noqa: BLE001 - 任何失败都触发熔断，交由腾讯兜底
                    trip_circuit(circuit_key, 300.0)
                    raise

            # 顺序：腾讯优先（单只快照，字段完整、耗时 <1s），东财作备源。
            # 依据阶段 9 实测：东财港美股快照需拉全市场（上万行），本机 push2 被重置时
            # 单轮耗 170s+，作为首候选会让港美股报价首次加载不可用；腾讯为首候选后
            # 首屏 <1s，东财仅在腾讯失败时兜底（并受熔断保护）。
            candidates.append(("东财", "东财/港美股快照（收盘后为最后成交快照）",
                               _eastmoney_quote))
        # 完整性校验：涨跌幅依赖前收，缺 prev_close 的快照（实测东财 AAPL 偶发只给价格）
        # 视为不可用并降级到下一源，避免把残缺报价渲染成“涨跌 0/空”。
        return with_fallback(candidates, need_rows=False, empty_value={},
                             validator=lambda q: bool(q.get("price")) and bool(q.get("prev_close")),
                             key=f"market:quote:{normalized_market}")

    result = _cached(cache_key, config.TTL["realtime_quote"], loader)
    if result.get("error"):
        return failure_pack(result, {"quote": {}})
    quote = result["data"]
    return pack_success(
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
    """股票 K 线（A股东财→新浪→腾讯；港美股腾讯）。

    Args:
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。
        period: 日k/周k/月k。
        adjust: qfq/hfq/不复权。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。

    Raises:
        NonRetryableError: 仅降级链内部使用（东财快照熔断时由内层函数抛出，不向外抛出）。
    """
    normalized_market = utils.normalize_market(market)
    if normalized_market is None:
        return bad_request(utils.market_error_message())
    try:
        code, normalized_market, _ = utils.normalize_symbol(symbol, normalized_market)
        utils.period_to_tencent(period)
        adjust_value = utils.normalize_adjust(adjust)
    except ValueError as exc:
        return bad_request(str(exc))
    # 存在性闸门（P1-03）：确认查无此物立即 NOT_FOUND，不进降级链
    if _symbol_missing(code, normalized_market):
        return api_error(
            ErrorCode.NOT_FOUND,
            f"未找到代码 {code} 的 K 线数据，请检查代码或市场后重试。",
            data={"items": [], "symbol": code, "market": normalized_market},
        )
    cache_key = f"market:kline:{normalized_market}:{code}:{period}:{adjust_value}"

    def loader() -> Dict[str, Any]:
        """K 线加载器：A股东财→新浪→腾讯，港美股东财→腾讯；复权口径随参数。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
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
            # 港美股：东财 push2his（生产候选，本机被重置）→ 腾讯 usfqkline/hkfqkline
            candidates = [
                ("东财", f"东财/{period}{'不复权' if not adjust_value else '前复权' if adjust_value == 'qfq' else '后复权'}",
                 lambda: overseas.get_kline_em(code, normalized_market, period, adjust_value)),
                ("腾讯", f"腾讯/{period}{'不复权' if not adjust_value else '前复权' if adjust_value == 'qfq' else '后复权'}",
                 lambda: tencent.get_kline(code, normalized_market, period, adjust_value)),
            ]
        # key：A 股东财 push2 本机被重置，记住上次命中源避免每次冷启动先等失效源（P1-04）
        return with_fallback(candidates, empty_value=[], key=f"market:kline:{normalized_market}")

    result = _cached(cache_key, utils.kline_cache_ttl(), loader)
    if result.get("error"):
        return failure_pack(result, {"items": [], "symbol": code, "market": normalized_market})
    items = result["data"]
    as_of, status, realtime = _kline_status(items)
    return pack_success(
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
    """分时数据（A股东财→新浪→腾讯；港美股腾讯）。

    Args:
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。
        period: 1/5/15/30/60 分钟。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    normalized_market = utils.normalize_market(market)
    if normalized_market is None:
        return bad_request(utils.market_error_message())
    period_value = str(period or "1min").lower().replace("min", "").replace("m", "")
    if period_value not in ("1", "5", "15", "30", "60"):
        return bad_request("period 仅支持 1/5/15/30/60 分钟")
    try:
        code, normalized_market, _ = utils.normalize_symbol(symbol, normalized_market)
    except ValueError as exc:
        return bad_request(str(exc))
    # 存在性闸门（P1-03）：确认查无此物立即 NOT_FOUND，不进降级链
    if _symbol_missing(code, normalized_market):
        return api_error(
            ErrorCode.NOT_FOUND,
            f"未找到代码 {code} 的分时数据，请检查代码或市场后重试。",
            data={"items": [], "symbol": code, "market": normalized_market},
        )
    cache_key = f"market:minute:{normalized_market}:{code}:{period_value}"

    def loader() -> Dict[str, Any]:
        """分时加载器：A股东财→新浪→腾讯；港美股走腾讯 1 分钟（东财无稳定免费通道）。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
        if normalized_market == "a":
            candidates: List[Any] = [
                ("东财", f"东财/{period_value}分钟", lambda: ashare.get_minute_em(code, period_value)),
                ("新浪", f"新浪/{period_value}分钟", lambda: ashare.get_minute_sina(code, period_value)),
                ("腾讯", "腾讯/1分钟", lambda: tencent.get_minute(code, normalized_market, period_value)),
            ]
        elif normalized_market == "us":
            # 第八轮：美股分时补入新浪通道（1/5/15/30 分钟，纯 HTTP）；
            # 腾讯仅返回最新一笔，降为末级兜底。
            candidates = [
                ("新浪", f"新浪美股/{period_value}分钟",
                 lambda: overseas.get_minute_sina_us(code, period_value)),
                ("腾讯", "腾讯/最新一笔",
                 lambda: overseas.get_minute(code, normalized_market)),
            ]
        else:
            candidates = [
                ("腾讯", "腾讯/1分钟（港股，纯 HTTP）",
                 lambda: overseas.get_minute(code, normalized_market))
            ]
        # key：分时冷启动同样走备源优先级记忆（P1-04）
        return with_fallback(candidates, empty_value=[], key=f"market:minute:{normalized_market}")

    result = _cached(cache_key, config.TTL["minute"], loader)
    if result.get("error"):
        return failure_pack(result, {"items": [], "symbol": code, "market": normalized_market})
    items = result["data"]
    # 美股分时：主源已是新浪（1/5/15/30 分钟，约千点）。若只剩腾讯单点，说明新浪通道
    # 当次失败，属瞬时异常 → 返回空 + 说明，让前端走「重试」而不是把单点画成分时图。
    if normalized_market == "us" and len(items) < 5:
        return pack_success(
            {"items": [], "symbol": code, "market": "us", "available": False},
            result["source"] or "腾讯",
            "美股分时本次仅取到单点数据（新浪分时通道本次异常，腾讯仅返回最新一笔），请稍后重试",
            items[0].get("time") if items else None,
            "通道异常",
            False,
        )
    last = items[-1] if items else {}
    last_date = str(last.get("date", ""))
    last_time = str(last.get("time", "15:00"))
    as_of = f"{last_date} {last_time}:00" if last_date else None
    parsed = utils.parse_datetime(as_of) if as_of else None
    status, realtime = utils.classify_market_status(parsed, normalized_market)
    return pack_success(
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
    """智能联想搜索。

    Args:
        q: 代码/名称/拼音。
        limit: 返回条数上限（超出上限按上限截断）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    keyword = (q or "").strip()
    if not keyword:
        return bad_request("搜索关键词不能为空")
    cache_key = f"market:search:{keyword}:{limit}"

    def loader() -> Dict[str, Any]:
        """标的搜索加载器：腾讯智能联想（无备源，空结果按空态返回）。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
        return with_fallback(
            [("腾讯", "腾讯/智能联想", lambda: tencent.get_search(keyword, limit))],
            empty_value=[],
        )

    result = _cached(cache_key, config.TTL["search"], loader)
    if result.get("error"):
        return failure_pack(result, {"items": []})
    return pack_success(
        {"items": result["data"], "query": keyword},
        result["source"] or "",
        result["caliber"] or "",
        utils.now_text(),
        "搜索",
        False,
    )


@router.get("/profile")
def market_profile(
    symbol: str = Query(..., min_length=1),
    market: str = Query("a", description="a/hk/us"),
) -> Dict[str, Any]:
    """个股概况（A股东财→巨潮；港美股腾讯基础字段）。

    Args:
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。

    Raises:
        NonRetryableError: 仅降级链内部使用（东财快照熔断时由内层函数抛出，不向外抛出）。
    """
    normalized_market = utils.normalize_market(market)
    if normalized_market is None:
        return bad_request(utils.market_error_message())
    try:
        code, normalized_market, _ = utils.normalize_symbol(symbol, normalized_market)
    except ValueError as exc:
        return bad_request(str(exc))
    # 存在性闸门（P1-03）：确认查无此物立即 NOT_FOUND，不进降级链
    if _symbol_missing(code, normalized_market):
        return api_error(
            ErrorCode.NOT_FOUND,
            f"未找到代码 {code} 的概况数据，请检查代码或市场后重试。",
            data={"profile": {}, "symbol": code, "market": normalized_market},
        )
    cache_key = f"market:profile:{normalized_market}:{code}"

    def loader() -> Dict[str, Any]:
        """个股概况加载器：A股东财 F10；港美股由腾讯快照派生（标记简化建模）。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
        if normalized_market == "a":
            candidates: List[Any] = [
                ("东财", "东财/个股概况（日频）", lambda: ashare.get_profile_em(code)),
                ("东财F10", "东财F10/公司概况（日频）；市值/估值为腾讯快照", lambda: ashare.get_profile_em_f10(code)),
            ]
        else:
            def basic_profile() -> Dict[str, Any]:
                """境外简化概况：仅取腾讯快照可得字段，缺失项留空不臆造。

                Returns:
                    dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
                """
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
        return with_fallback(candidates, need_rows=False, empty_value={},
                             validator=lambda p: bool(p.get("name")),
                             key=f"market:profile:{normalized_market}")

    result = _cached(cache_key, config.TTL["profile"], loader)
    if result.get("error"):
        return failure_pack(result, {"profile": {}})
    profile = result["data"]
    # 概况可能内部合并腾讯快照并在失败时改写口径，优先取 profile 内的实际口径
    return pack_success(
        {"profile": profile},
        profile.get("source") or result["source"] or "",
        profile.get("caliber") or result["caliber"] or "",
        profile.get("as_of"),
        profile.get("market_status") or "日频",
        bool(profile.get("is_realtime")),
    )
