"""个股域路由：/api/stock 下 7 个接口（阶段 5.1）。

对应规范 v7.2 §8.6 与《设计基准说明书_阶段5.1》第十节 10.2：
- GET /profile             公司概况（东财 F10 + 东财估值 + 腾讯报价）
- GET /holders             十大股东 / 十大流通股东（东财 F10，最新一期）
- GET /realtime-panel      FinScope 聚合（报价/K线/分时/资金分布/相关新闻/机构预测）
- GET /financial-summary   财务摘要（新浪，EPS/毛利率/ROE 等）
- GET /financial-trend     财务趋势四序列（新浪利润表，年报口径）
- GET /financial-indicator 财务指标明细（新浪财务分析指标）
- GET /financial-report    三大报表（新浪资产负债表/利润表/现金流量表）

统一返回 {data, error, error_code}；data 含 source/caliber/as_of/market_status/is_realtime。
港美股无 F10 与中文财报通道（D3 已确认），相关接口返回明确中文说明的空结构，不编造数据。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query

from backend.app import config
from backend.app.core import cache
from backend.app.core.errors import ErrorCode, api_error, ok
from backend.app.core.resilient import with_fallback
from backend.app.datasource import stock as stock_provider
from backend.app.datasource import tencent, utils

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stock", tags=["个股"])

_ALLOWED_MARKETS = ("a", "hk", "us")


def _bad_request(message: str) -> Dict[str, Any]:
    """参数错误响应。

    Args:
        message: 中文提示文案。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    return api_error(ErrorCode.INVALID_PARAM, message)


def _failure(result: Dict[str, Any], empty_value: Any) -> Dict[str, Any]:
    """降级链失败 → 统一错误响应。

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
    """组装成功响应，附带数据源/口径/时间/市场状态。

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


def _normalize(symbol: str, market: str) -> Optional[tuple]:
    """校验并归一化代码；非法代码返回 None（由调用方转参数错误）。

    Args:
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。

    Returns:
        Optional[tuple]: 可能为 None。
    """
    try:
        return utils.normalize_symbol(symbol, market)
    except Exception:  # noqa: BLE001 - 归一化失败即参数问题
        return None


def _quote_status(quote: Optional[Dict[str, Any]]) -> tuple[Optional[str], str, bool]:
    """从报价中取口径三要素，缺失时退回当前时间快照。

    Args:
        quote: 报价快照。

    Returns:
        tuple[Optional[str], str, bool]: 计算结果。
    """
    if quote:
        return quote.get("quote_time"), quote.get("market_status") or "快照", bool(quote.get("is_realtime"))
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return now, "快照", False


# =============================================================
# 一、概况 / 股东
# =============================================================
@router.get("/profile")
def stock_profile(
    symbol: str = Query(..., description="证券代码，如 688012"),
    market: str = Query("a", description="a/hk/us"),
) -> Dict[str, Any]:
    """公司概况（ST-08）。

    Args:
        symbol: 证券代码，如 688012。
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    selected = (market or "a").lower()
    if selected not in _ALLOWED_MARKETS:
        return _bad_request("market 仅支持 a/hk/us")
    normalized = _normalize(symbol, selected)
    if normalized is None:
        return _bad_request("证券代码格式不正确，请输入 6 位 A股代码或有效港美股代码")
    code = normalized[0]

    def loader() -> Dict[str, Any]:
        """概况取数（东财 F10 主源；港美股退化为腾讯行情）。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
        return with_fallback(
            [
                ("东财", "东财 F10 基本资料 + 东财估值 + 腾讯报价",
                 lambda: stock_provider.get_profile(code, selected)),
            ],
            empty_value={},
            validator=lambda value: bool(value.get("name")),
        )

    result = cache.get_or_load(f"stock:profile:{selected}:{code}", config.TTL["profile"], loader)
    if result.get("error"):
        return _failure(result, {"symbol": code})
    data = result["data"] or {}
    as_of, status, is_realtime = _quote_status(None)
    return _pack(data, result.get("source") or "", result.get("caliber") or "", as_of, status, is_realtime)


@router.get("/holders")
def stock_holders(symbol: str = Query(..., description="6 位 A股代码")) -> Dict[str, Any]:
    """十大股东与十大流通股东（ST-10）。

    Args:
        symbol: 6 位 A股代码。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    normalized = _normalize(symbol, "a")
    if normalized is None:
        return _bad_request("证券代码格式不正确，请输入 6 位 A股代码")
    code = normalized[0]

    def loader() -> Dict[str, Any]:
        """股东取数（东财 F10，最新一期）。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
        return with_fallback(
            [("东财", "东财 F10 十大股东/十大流通股东（最新一期）",
              lambda: stock_provider.get_holders(code))],
            empty_value={},
            validator=lambda value: bool(value.get("top10") or value.get("free_top10")),
        )

    result = cache.get_or_load(f"stock:holders:{code}", config.TTL["profile"], loader)
    if result.get("error"):
        return _failure(result, {"symbol": code, "top10": [], "free_top10": []})
    data = result["data"] or {}
    as_of = data.get("top10_period") or data.get("free_top10_period")
    return _pack(data, result.get("source") or "", result.get("caliber") or "",
                 f"{as_of} 报告期" if as_of else None, "报告期披露", False)


# =============================================================
# 二、FinScope 聚合面板
# =============================================================
@router.get("/realtime-panel")
def stock_realtime_panel(
    symbol: str = Query(..., description="证券代码"),
    market: str = Query("a", description="a/hk/us"),
) -> Dict[str, Any]:
    """FinScope 聚合面板（ST-01/02/03/04/05/06）。

    单块失败不影响整体：`blocks` 字段逐块标注 ok/failed/unsupported，
    前端据此就地渲染空态或错误态，不整页白屏。

    Args:
        symbol: 证券代码。
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    selected = (market or "a").lower()
    if selected not in _ALLOWED_MARKETS:
        return _bad_request("market 仅支持 a/hk/us")
    normalized = _normalize(symbol, selected)
    if normalized is None:
        return _bad_request("证券代码格式不正确，请输入 6 位 A股代码或有效港美股代码")
    code = normalized[0]

    def loader() -> Dict[str, Any]:
        """聚合取数：报价/K线/分时走腾讯，资金/新闻/预测走新浪与东财/同花顺。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
        return with_fallback(
            [
                ("腾讯", "腾讯报价+腾讯K线/分时 + 新浪资金流 + 东财新闻 + 同花顺机构预测",
                 lambda: stock_provider.get_realtime_panel(code, selected)),
            ],
            empty_value={"symbol": code, "market": selected, "blocks": {}},
            validator=lambda value: bool(value.get("quote")),
        )

    result = cache.get_or_load(f"stock:panel:{selected}:{code}", config.TTL["realtime_quote"], loader)
    if result.get("error"):
        return _failure(result, {"symbol": code, "market": selected, "blocks": {}})
    data = result["data"] or {}
    as_of, status, is_realtime = _quote_status(data.get("quote"))
    return _pack(data, result.get("source") or "", result.get("caliber") or "", as_of, status, is_realtime)


# =============================================================
# 三、财务四接口（A股专用）
# =============================================================
def _require_a_share(symbol: str, market: str) -> Optional[Dict[str, Any]]:
    """财务接口仅支持 A股；非 A股返回明确中文说明的错误结构。

    Args:
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。

    Returns:
        dict | None: 命中时返回记录，缺失返回 None。
    """
    if market != "a":
        return api_error(
            ErrorCode.EMPTY_DATA,
            "港美股中文财报通道暂无免费来源，本批如实标注不可用。",
            data={"items": []},
        )
    return None


@router.get("/financial-summary")
def stock_financial_summary(symbol: str = Query(..., description="6 位 A股代码")) -> Dict[str, Any]:
    """财务摘要（ST-09 关键指标表：EPS/毛利率/ROE）。

    Args:
        symbol: 6 位 A股代码。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    normalized = _normalize(symbol, "a")
    if normalized is None:
        return _bad_request("证券代码格式不正确，请输入 6 位 A股代码")
    code = normalized[0]

    def loader() -> Dict[str, Any]:
        """新浪财务摘要取数。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
        return with_fallback(
            [("新浪", "新浪/财务摘要（常用指标，报告期倒序）",
              lambda: stock_provider.get_financial_summary(code))],
            empty_value={"periods": [], "metrics": []},
            validator=lambda value: bool(value.get("metrics")),
        )

    result = cache.get_or_load(f"stock:fin-summary:{code}", config.TTL["financial"], loader)
    if result.get("error"):
        return _failure(result, {"symbol": code, "periods": [], "metrics": []})
    data = result["data"] or {}
    periods = data.get("periods") or []
    return _pack(data, result.get("source") or "", result.get("caliber") or "",
                 f"{periods[0]} 报告期" if periods else None, "定期报告", False)


@router.get("/estimates")
def stock_estimates(
    symbol: str = Query(..., description="证券代码，如 688012"),
    market: str = Query("a", description="a/hk/us"),
) -> Dict[str, Any]:
    """机构一致预期（F-027）：分年度 EPS/净利润预测 + 机构明细 + 详细指标。

    对应原版 FinScope 的 analyst estimates；同花顺盈利预测为等价数据源
    （覆盖机构聚合 + 逐机构明细 + 营业收入等详细指标），港美股无免费通道如实空态。

    Args:
        symbol: 证券代码，如 688012。
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    selected = (market or "a").lower()
    if selected not in _ALLOWED_MARKETS:
        return _bad_request("market 仅支持 a/hk/us")
    normalized = _normalize(symbol, selected)
    if normalized is None:
        return _bad_request("证券代码格式不正确，请输入 6 位 A股代码或有效港美股代码")
    code = normalized[0]

    def loader() -> Dict[str, Any]:
        """一致预期取数（同花顺 worth 页四表）。

        validator 恒真：空数据由 provider 以 available=false 表达（§6.3 空/错分离），
        不得因“无机构覆盖”落入错误态；通道异常仍由 with_fallback 转错误码。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
        return with_fallback(
            [("同花顺", "同花顺/盈利预测（机构覆盖聚合 + 逐机构明细）",
              lambda: stock_provider.get_estimates(code, selected))],
            empty_value={"eps": [], "profit": [], "institutions": [], "detail": [], "years": [],
                         "available": False},
            validator=lambda value: True,
        )

    result = cache.get_or_load(f"stock:estimates:{selected}:{code}", config.TTL["estimates"], loader)
    if result.get("error"):
        return _failure(result, {"symbol": code, "market": selected,
                                 "eps": [], "profit": [], "institutions": [], "detail": [], "years": []})
    data = result["data"] or {}
    years = data.get("years") or []
    return _pack(
        {"symbol": code, "market": selected, **data},
        result.get("source") or "",
        result.get("caliber") or data.get("caliber_note") or "",
        f"{years[0]}–{years[-1]} 预测年度" if years else None,
        "机构预测（日频更新）",
        False,
    )


@router.get("/financial-trend")
def stock_financial_trend(
    symbol: str = Query(..., description="6 位 A股代码"),
    years: int = Query(5, ge=1, le=10, description="年报年数"),
) -> Dict[str, Any]:
    """财务趋势四序列（ST-09 趋势图）。

    Args:
        symbol: 6 位 A股代码。
        years: 年报年数。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    normalized = _normalize(symbol, "a")
    if normalized is None:
        return _bad_request("证券代码格式不正确，请输入 6 位 A股代码")
    code = normalized[0]

    def loader() -> Dict[str, Any]:
        """新浪利润表取数并计算同比。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
        return with_fallback(
            [("新浪", "新浪/利润表年报（营收/净利及同比）",
              lambda: stock_provider.get_financial_trend(code, years))],
            empty_value={"periods": [], "series": []},
            validator=lambda value: bool(value.get("series")),
        )

    result = cache.get_or_load(f"stock:fin-trend:{code}:{years}", config.TTL["financial"], loader)
    if result.get("error"):
        return _failure(result, {"symbol": code, "periods": [], "series": []})
    data = result["data"] or {}
    periods = data.get("periods") or []
    return _pack(data, result.get("source") or "", result.get("caliber") or "",
                 f"{periods[-1]} 年报" if periods else None, "年报披露", False)


@router.get("/financial-indicator")
def stock_financial_indicator(
    symbol: str = Query(..., description="6 位 A股代码"),
    limit: int = Query(8, ge=1, le=20, description="返回期数"),
) -> Dict[str, Any]:
    """财务指标明细（ST-09 指标页签）。

    Args:
        symbol: 6 位 A股代码。
        limit: 返回期数。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    normalized = _normalize(symbol, "a")
    if normalized is None:
        return _bad_request("证券代码格式不正确，请输入 6 位 A股代码")
    code = normalized[0]

    def loader() -> Dict[str, Any]:
        """新浪财务分析指标取数。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
        return with_fallback(
            [("新浪", "新浪/财务分析指标（最近 N 期）",
              lambda: stock_provider.get_financial_indicator(code, limit))],
            empty_value={"periods": [], "metrics": []},
            validator=lambda value: bool(value.get("metrics")),
        )

    result = cache.get_or_load(f"stock:fin-indicator:{code}:{limit}", config.TTL["financial"], loader)
    if result.get("error"):
        return _failure(result, {"symbol": code, "periods": [], "metrics": []})
    data = result["data"] or {}
    periods = data.get("periods") or []
    return _pack(data, result.get("source") or "", result.get("caliber") or "",
                 f"{periods[-1]} 报告期" if periods else None, "定期报告", False)


@router.get("/financial-report")
def stock_financial_report(
    symbol: str = Query(..., description="6 位 A股代码"),
    statement: str = Query("利润表", description="资产负债表/利润表/现金流量表"),
    limit: int = Query(8, ge=1, le=20, description="返回期数"),
) -> Dict[str, Any]:
    """三大报表（ST-09 报表页签）。

    Args:
        symbol: 6 位 A股代码。
        statement: 资产负债表/利润表/现金流量表。
        limit: 返回期数。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    normalized = _normalize(symbol, "a")
    if normalized is None:
        return _bad_request("证券代码格式不正确，请输入 6 位 A股代码")
    if statement not in ("资产负债表", "利润表", "现金流量表"):
        return _bad_request("statement 仅支持资产负债表/利润表/现金流量表")
    code = normalized[0]

    def loader() -> Dict[str, Any]:
        """新浪三表取数。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
        return with_fallback(
            [("新浪", f"新浪/{statement}（最近 {limit} 期）",
              lambda: stock_provider.get_financial_report(code, statement, limit))],
            empty_value={"periods": [], "metrics": []},
            validator=lambda value: bool(value.get("metrics")),
        )

    result = cache.get_or_load(
        f"stock:fin-report:{code}:{statement}:{limit}", config.TTL["financial"], loader
    )
    if result.get("error"):
        return _failure(result, {"symbol": code, "statement": statement, "periods": [], "metrics": []})
    data = result["data"] or {}
    periods = data.get("periods") or []
    return _pack(data, result.get("source") or "", result.get("caliber") or "",
                 f"{periods[0]} 报告期" if periods else None, "定期报告", False)
