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
from typing import Any, Dict, List, Optional, Sequence

from fastapi import APIRouter, Query

from backend.app import config
from backend.app.core import cache
from backend.app.core.errors import pack_success, ErrorCode, api_error, ok
from backend.app.core.resilient import with_fallback
from backend.app.datasource import sector as sector_provider
from backend.app.datasource import utils

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sector", tags=["板块"])

# 板块域白名单：cn=国内（A股）、hk=港股、us=美股
_ALLOWED_MARKETS = utils.MARKET_WHITELIST_SECTOR


def _bad_request(message: str) -> Dict[str, Any]:
    """参数错误响应。

    Args:
        message: 中文提示文案。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    return api_error(ErrorCode.INVALID_PARAM, message)


def _failure(result: Dict[str, Any], empty_value: Any) -> Dict[str, Any]:
    """把降级链失败结果转为统一错误响应，空结构保持与成功态同构。

    Args:
        result: 上游返回结果。
        empty_value: 空数据时返回的同构空值（保持字段结构一致）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
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
    """组装成功响应，统一附带数据源/口径/时间/市场状态。

    Args:
        data: 待包装或清洗的数据主体。
        source: 数据源标识（用于降级链溯源与徽章展示）。
        caliber: 口径说明（展示在口径徽章）。
        as_of: 数据时间（北京时间，格式 YYYY-MM-DD HH:MM:SS）。
        market_status: 市场状态文案（盘中/盘前/盘后/休市/收盘快照）。
        is_realtime: 是否为盘中实时数据。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    return pack_success(data, source, caliber, as_of, market_status, is_realtime)


def _status_now() -> tuple[str, str, bool]:
    """按当前时间推断市场状态与是否实时（收盘后为快照，不伪装实时）。

    Returns:
        tuple[str, str, bool]: 计算结果。
    """
    now = utils.bj_now()  # 显式取北京时间：宿主时区非 UTC+8 时状态判定不偏移
    status, is_realtime = utils.classify_market_status(now, "a")
    return utils.now_text(now), status, is_realtime


def _pack_sectors(
    payload: Dict[str, Any],
    list_keys: Sequence[str],
    source: str,
    caliber: str,
    as_of: Optional[str],
    market_status: str,
    is_realtime: bool,
) -> Dict[str, Any]:
    """列表型板块响应的统一包装：空结果按 EMPTY_DATA 语义补齐 available 与中文说明。

    背景（R6-15 / BUG-R6-06）：同花顺限频时上游返回空 DataFrame 而不抛异常，原实现直接
    `_pack(items=[])`，对外表现为「source=同花顺 + 0 条 + error=null」。用户看到的是
    「0 个行业」而不是「数据暂不可用」，违反规范 §6.3 空/错分离与 §11.5 数据类不得降级。

    Args:
        payload: 待包装的数据主体（含一个或多个列表字段）。
        list_keys: 参与「是否有数据」判定的列表字段名。
        source: 数据源标识。
        caliber: 原口径说明。
        as_of: 数据时间（北京时间）。
        market_status: 市场状态文案。
        is_realtime: 是否为盘中实时数据。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；空结果时 data.available=False
            且 caliber 改写为可读原因。
    """
    body = dict(payload)
    total = 0
    for key in list_keys:
        value = body.get(key)
        if isinstance(value, list):
            total += len(value)
    available = total > 0
    final_caliber = caliber
    if not available:
        prefix = source or "上游"
        final_caliber = (
            f"{prefix}板块数据暂不可用（源站返回空，可能为限频或盘前未更新），"
            f"请稍后重试；原口径：{caliber or '—'}"
        )
        body["empty_hint"] = "UPSTREAM_EMPTY"
    body["available"] = available
    return _pack(body, source, final_caliber, as_of, market_status, is_realtime)


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
        """实际取数入口，交给 with_fallback 逐源尝试。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
        # key：按业务域记住上次命中源，并让熔断失效源在冷却期内被跳过（P1-04）
        return with_fallback(candidates, empty_value=empty_value,
                             key=":".join(cache_key.split(":")[:2]))

    return cache.get_or_load(cache_key, ttl, loader)


# =============================================================
# 一、行业 / 概念行情
# =============================================================
def _industry_cached(market: str) -> Dict[str, Any]:
    """行业行情（带缓存）；heatmap/summary 复用同一份缓存，避免重复请求源站。

    Args:
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
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
    """概念行情（带缓存）。

    Args:
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    if market in ("hk", "us"):
        candidates: List[Any] = [
            ("腾讯", "境外简化建模：港美股无免费概念板块通道", lambda: sector_provider.get_overseas_items(market)),
        ]
    else:
        candidates = [
            ("腾讯", "腾讯/概念板块榜（805 个，含涨跌幅·成交额·主力净流入·领涨股·5/20/60 日涨幅；纯 HTTP 无需签名）",
             sector_provider.get_concept_ths),
            ("新浪", "新浪/概念板块（175 个，成交额单位=元已折算亿元；无主力净流入）",
             sector_provider.get_concept_sina),
        ]
    return _run_chain(f"sector:concept:{market}", config.TTL["sector"], candidates, [])


@router.get("/industry")
def sector_industry(market: str = Query("a", description="a/hk/us（cn 为 a 的兼容别名）")) -> Dict[str, Any]:
    """行业板块行情列表（S-04 排行表数据源）。

    Args:
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    normalized = utils.normalize_market(market)
    if normalized is None:
        return _bad_request(utils.market_error_message())
    selected = utils.sector_source_market(normalized)   # 内部口径 cn/hk/us（provider 与缓存键不变）
    public_market = normalized                           # 对外口径 a/hk/us
    result = _industry_cached(selected)
    if result.get("error"):
        return _failure(result, {"items": [], "market": public_market})
    items = result["data"] or []
    as_of, status, is_realtime = _status_now()
    return _pack_sectors(
        {"items": items, "count": len(items), "market": public_market, "kind": "industry"},
        ["items"],
        result.get("source") or "",
        result.get("caliber") or "",
        as_of,
        status,
        is_realtime,
    )


@router.get("/concept")
def sector_concept(market: str = Query("a", description="a/hk/us（cn 为 a 的兼容别名）")) -> Dict[str, Any]:
    """概念板块行情列表（S-05 排行表数据源）。

    Args:
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    normalized = utils.normalize_market(market)
    if normalized is None:
        return _bad_request(utils.market_error_message())
    selected = utils.sector_source_market(normalized)   # 内部口径 cn/hk/us（provider 与缓存键不变）
    public_market = normalized                           # 对外口径 a/hk/us
    result = _concept_cached(selected)
    if result.get("error"):
        return _failure(result, {"items": [], "market": public_market})
    items = result["data"] or []
    as_of, status, is_realtime = _status_now()
    return _pack_sectors(
        {"items": items, "count": len(items), "market": public_market, "kind": "concept"},
        ["items"],
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
    market: str = Query("a", description="a/hk/us（cn 为 a 的兼容别名）"),
    kind: str = Query("industry", description="industry/concept"),
) -> Dict[str, Any]:
    """热力图节点（S-03）。value=abs(涨跌幅)，pct 保留原值供颜色与标签使用。

    Args:
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。
        kind: 类别：industry=行业 / concept=概念。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    normalized = utils.normalize_market(market)
    if normalized is None:
        return _bad_request(utils.market_error_message())
    selected = utils.sector_source_market(normalized)   # 内部口径 cn/hk/us（provider 与缓存键不变）
    public_market = normalized                           # 对外口径 a/hk/us
    kind_value = (kind or "industry").lower()
    if kind_value not in ("industry", "concept"):
        return _bad_request("kind 仅支持 industry/concept")

    result = _concept_cached(selected) if kind_value == "concept" else _industry_cached(selected)
    if result.get("error"):
        return _failure(result, {"nodes": [], "market": public_market, "kind": kind_value})
    nodes = sector_provider.heatmap_from_items(result["data"] or [])
    as_of, status, is_realtime = _status_now()
    return _pack_sectors(
        {"nodes": nodes, "count": len(nodes), "market": public_market, "kind": kind_value},
        ["nodes"],
        result.get("source") or "",
        result.get("caliber") or "",
        as_of,
        status,
        is_realtime,
    )


@router.get("/summary")
def sector_summary(market: str = Query("a", description="a/hk/us（cn 为 a 的兼容别名）")) -> Dict[str, Any]:
    """板块摘要（S-06）：领涨/领跌/净流入 Top3 + 成交额集中度。

    Args:
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    normalized = utils.normalize_market(market)
    if normalized is None:
        return _bad_request(utils.market_error_message())
    selected = utils.sector_source_market(normalized)   # 内部口径 cn/hk/us（provider 与缓存键不变）
    public_market = normalized                           # 对外口径 a/hk/us
    result = _industry_cached(selected)
    if result.get("error"):
        return _failure(result, {"gainers": [], "losers": [], "inflow": [], "market": public_market})
    summary = sector_provider.summary_from_items(result["data"] or [], selected)
    summary = {**summary, "market": public_market}  # 对外口径 a/hk/us（第五轮阶段 4 统一）
    as_of, status, is_realtime = _status_now()
    return _pack_sectors(
        summary,
        ["gainers", "losers", "inflow"],
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
    market: str = Query("a", description="a/hk/us（cn 为 a 的兼容别名）"),
) -> Dict[str, Any]:
    """板块指数 K 线（V-01）。

    Args:
        board: 板块名，如 半导体。
        period: 周期：日k/周k/月k，或分钟数（1/5/15/30/60）。
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    if not board.strip():
        return _bad_request("board 参数不可为空")
    normalized = utils.normalize_market(market)
    if normalized is None:
        return _bad_request(utils.market_error_message())
    selected = utils.sector_source_market(normalized)   # 内部口径 cn/hk/us（provider 与缓存键不变）
    public_market = normalized                           # 对外口径 a/hk/us

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
        """板块 K 线取数。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
        return with_fallback(
            candidates,
            empty_value={"items": [], "board": board, "period": period},
            validator=lambda value: bool(value.get("items")),
            key="sector:kline",
        )

    result = cache.get_or_load(cache_key, config.TTL["kline"], loader)
    if result.get("error"):
        # 空/错分离（原型 §7.1）：板块名写错属空态（给下一步动作），
        # 通道故障属错误态（可重试）。第八轮起同花顺已走纯 HTTP，不再有
        # 「安装版不可用」这一说，此前的降级文案一并下线。
        if not sector_provider.is_known_board(board):
            return api_error(
                ErrorCode.EMPTY_DATA,
                f"板块名录中未收录「{board}」，请从板块行情列表选择，或核对名称后重试。",
                data={"items": [], "board": board, "period": period, "market": public_market},
            )
        return api_error(
            ErrorCode.UPSTREAM_ERROR,
            f"「{board}」的指数序列本次取数失败，请稍后点击刷新重试。",
            data={"items": [], "board": board, "period": period, "market": public_market},
        )
    data = result["data"] or {}
    items = data.get("items") or []
    as_of = f"{items[-1].get('date')} 15:00:00" if items else None
    return _pack(
        {
            "board": data.get("board", board),
            "period": data.get("period", period),
            "market": public_market,
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
    market: str = Query("a", description="a/hk/us（cn 为 a 的兼容别名）"),
) -> Dict[str, Any]:
    """板块成分股（V-03）。

    Args:
        board: 板块名。
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    if not board.strip():
        return _bad_request("board 参数不可为空")
    normalized = utils.normalize_market(market)
    if normalized is None:
        return _bad_request(utils.market_error_message())
    selected = utils.sector_source_market(normalized)   # 内部口径 cn/hk/us（provider 与缓存键不变）
    public_market = normalized                           # 对外口径 a/hk/us

    cache_key = f"sector:cons:{selected}:{board}"

    if selected in ("hk", "us"):
        return api_error(
            ErrorCode.EMPTY_DATA,
            "港美股板块暂无免费成分股通道，已如实标注不可用。",
            data={"items": [], "board": board, "market": public_market},
        )

    def loader() -> Dict[str, Any]:
        """成分股取数：同花顺 → 新浪 → 申万 → 东财，四个独立候选便于徽章如实显示命中源。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
        return with_fallback(
            [
                ("同花顺", "同花顺/板块成分（行业与概念均可，纯 HTTP 详情页，权重按流通市值占比）",
                 lambda: sector_provider.get_cons_ths(board)),
                ("新浪", "新浪/板块成分（权重按流通市值占比）",
                 lambda: sector_provider.get_cons_sina(board)),
                ("申万", "申万/行业成分（一/二/三级行业名均可，权重按市值占比）",
                 lambda: sector_provider.get_cons_sw(board)),
                ("东财", "东财/板块成分（生产环境备用，本机 push2 被源站重置）",
                 lambda: sector_provider.get_cons_em(board)),
            ],
            empty_value={"items": [], "board": board},
            validator=lambda value: bool(value.get("items")),
            key="sector:cons",
        )

    result = cache.get_or_load(cache_key, config.TTL["cons"], loader)
    if result.get("error"):
        return _failure(result, {"items": [], "board": board, "market": public_market})
    data = result["data"] or {}
    as_of, status, is_realtime = _status_now()
    return _pack(
        {
            "board": data.get("board", board),
            "market": public_market,
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
def sector_fundflow(market: str = Query("a", description="a/hk/us（cn 为 a 的兼容别名）")) -> Dict[str, Any]:
    """板块资金流向（S-07）。主力净额/流入/流出为真实值；超大单/大单源不提供，返回 None。

    Args:
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    normalized = utils.normalize_market(market)
    if normalized is None:
        return _bad_request(utils.market_error_message())
    selected = utils.sector_source_market(normalized)   # 内部口径 cn/hk/us（provider 与缓存键不变）
    public_market = normalized                           # 对外口径 a/hk/us
    if selected in ("hk", "us"):
        return api_error(
            ErrorCode.EMPTY_DATA,
            "港美股板块资金流暂无免费通道，已如实标注不可用。",
            data={"items": [], "market": public_market},
        )

    def loader() -> Dict[str, Any]:
        """资金流取数（同花顺行业资金流 + 成分股四档聚合）。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
        return with_fallback(
            [("同花顺", "同花顺/行业资金流 + 成分股新浪四档聚合（超大单/大单/中单/小单）；"
                        "已聚合板块整行取新浪口径、未聚合板块取同花顺口径，"
                        "每行 flow_source 标明，两套口径不可跨行相加",
              sector_provider.get_fundflow)],
            empty_value=[],
        )

    result = cache.get_or_load(f"sector:fundflow:{selected}", config.TTL["fund_flow"], loader)
    if result.get("error"):
        return _failure(result, {"items": [], "market": public_market})
    items = result["data"] or []
    as_of, status, is_realtime = _status_now()
    return _pack_sectors(
        {"items": items, "count": len(items), "market": public_market},
        ["items"],
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
    market: str = Query("a", description="a/hk/us（cn 为 a 的兼容别名）"),
) -> Dict[str, Any]:
    """多板块归一化走势对比（首个交易日=100），由板块 K 线聚合，不额外编造数据。

    Args:
        boards: 逗号分隔板块名，最多 6 个。
        days: 取最近 N 个交易日。
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    normalized = utils.normalize_market(market)
    if normalized is None:
        return _bad_request(utils.market_error_message())
    selected = utils.sector_source_market(normalized)   # 内部口径 cn/hk/us（provider 与缓存键不变）
    public_market = normalized                           # 对外口径 a/hk/us
    names = [item.strip() for item in (boards or "").split(",") if item.strip()]
    if not names:
        return _bad_request("boards 参数不可为空")
    if len(names) > 6:
        return _bad_request("boards 最多支持 6 个板块")

    cache_key = f"sector:trend:{selected}:{','.join(names)}:{days}"

    def loader() -> Dict[str, Any]:
        """趋势取数：逐个板块复用 K 线，单板块失败不阻断整体。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
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
            "market": public_market,
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


@router.get("/crowding")
def sector_crowding(
    board: str = Query(..., description="板块名，如「半导体」"),
    force: bool = Query(False, description="true=绕过缓存"),
) -> Dict[str, Any]:
    """板块拥挤度（第七轮 M3）：四维分位加权 0–100 分，>60 提示 / >80 复核。

    只陈述交易结构位置，不给买卖指令；缺失维度在 `missing` 里逐条写明（不编造）。

    Args:
        board: 板块名。
        force: 是否绕过缓存。

    Returns:
        Dict[str, Any]: {"data": {...}, "error": null}
    """
    name = str(board or "").strip()
    if not name:
        return api_error(ErrorCode.INVALID_PARAM, "请提供板块名（board），如「半导体」")

    def loader() -> Dict[str, Any]:
        """取数并打分（1 小时缓存：日频数据，无需更短）。

        Returns:
            Dict[str, Any]: 拥挤度结果。
        """
        return sector_provider.get_crowding(name)

    try:
        if force:
            data = loader()
        else:
            data = cache.get_or_load(f"sector:crowding:{name}", 3600, loader)
    except ValueError as exc:
        return api_error(ErrorCode.EMPTY_DATA, str(exc))
    except Exception as exc:  # noqa: BLE001 - 上游异常转可读错误
        logger.warning("拥挤度计算失败 %s：%s", name, type(exc).__name__)
        return api_error(ErrorCode.UPSTREAM_ERROR, f"拥挤度计算失败（{type(exc).__name__}），请稍后重试")
    return ok(data)


@router.get("/crowding/rankings")
def sector_crowding_rankings(
    kind: str = Query("industry", description="板块类型：industry=行业 / concept=概念"),
    limit: int = Query(50, ge=1, le=120, description="返回前 N 名"),
    force: bool = Query(False, description="true=绕过缓存"),
) -> Dict[str, Any]:
    """全板块拥挤度排行（第七轮 M12 + 概念补齐）。

    复用 `/crowding` 同一函数逐板块打分后降序（同口径，不另算一套）。
    首次计算约 30–90 秒（行业 90 个 / 概念 375 个并发扫描），结果缓存 1 小时。

    Args:
        kind: 板块类型：industry=行业 / concept=概念。
        limit: 返回前 N 名。
        force: 是否绕过缓存。

    Returns:
        Dict[str, Any]: {"data": {items, scanned, total, failed, kind, level_counts, caliber, as_of}}
    """
    key_kind = str(kind or "industry").strip().lower()
    if key_kind not in ("industry", "concept"):
        return api_error(ErrorCode.INVALID_PARAM, "kind 仅支持 industry（行业）/ concept（概念）")

    # P1-1：排行需扫描 90 行业（约 20 秒）/ 800 概念（约 40 秒）。
    # 第八轮起全程纯 HTTP（无 V8 串行），并改为「缓存优先 + 后台刷新」：
    #   ① 命中未过期缓存 → 直接返回（<0.5s）；
    #   ② 缓存过期 → 先返回旧值（stale-while-revalidate）并触发后台重算；
    #   ③ 无缓存 → 本次同步计算（服务启动预热已覆盖默认 industry 口径）。
    # 不再返回「计算中」空态（空态不得掩盖正常数据）。
    cache_key = f"sector:crowding:rankings:{key_kind}:{limit}"
    cached = None if force else cache.get(cache_key, 3600 * 24)
    if cached is not None and not force:
        # 缓存命中即返回；若已超过 1 小时则在后台刷新（用户无感知）
        if cache.get(cache_key, 3600) is None and not sector_provider.rankings_in_flight(cache_key):
            sector_provider.start_rankings_bg(key_kind, limit, cache_key)
        return ok(cached)
    if sector_provider.rankings_in_flight(cache_key) and not force:
        stale = cache.get(cache_key, 3600 * 24)
        if stale is not None:
            return ok(stale)

    def loader() -> Dict[str, Any]:
        """全量扫描（1 小时缓存：日频数据）。

        Returns:
            Dict[str, Any]: 排行结果。
        """
        return sector_provider.get_crowding_rankings(kind=key_kind, limit=limit)

    try:
        data = loader()
        cache.set(cache_key, data, 3600)
    except ValueError as exc:
        return api_error(ErrorCode.EMPTY_DATA, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("拥挤度排行失败：%s", type(exc).__name__)
        return api_error(ErrorCode.UPSTREAM_ERROR, f"拥挤度排行计算失败（{type(exc).__name__}），请稍后重试")
    return ok(data)
