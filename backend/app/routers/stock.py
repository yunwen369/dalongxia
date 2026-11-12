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
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query

from backend.app import config
from backend.app.core import cache
from backend.app.core.errors import pack_success, ErrorCode, api_error, ok, bad_request, failure_pack
from backend.app.routers.common import pack_result
from backend.app.core.resilient import with_fallback
from backend.app.datasource import overseas as overseas_provider
from backend.app.datasource import fcf_health
from backend.app.datasource import peers as peers_provider
from backend.app.datasource import stock as stock_provider
from backend.app.datasource import utils

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stock", tags=["个股"])

# 行情/个股域白名单：a=A股、hk=港股、us=美股
_ALLOWED_MARKETS = utils.MARKET_WHITELIST_QUOTE


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
    now = utils.now_text()
    return now, "快照", False


# =============================================================
# 〇、场内基金（ETF/LOF）个股通道空态
# =============================================================
# 2026-11-10：ETF/LOF 没有个股 F10 概况、十大股东、中文财报与券商个股研报。
# 原实现让这些接口落进降级链并以 UPSTREAM_ERROR 收尾，
# 前端 stock_b.js 的 Promise.all 因此整体 reject，把本可用的
# 报价/K线/分时/新闻/舆情一起置为「加载失败」。
# 现改为返回成功信封 + available=false + 中文说明（空态），只影响个股专属卡片。
_FUND_EMPTY_NOTES = {
    "profile": "ETF/LOF 无个股 F10 概况，请查看基金档案或标的指数",
    "holders": "ETF/LOF 无十大股东口径，份额持有人见基金定期报告",
    "financial": "ETF/LOF 无公司财报口径，净值与跟踪误差见基金页",
    "fcf": "ETF/LOF 无公司现金流口径，FCF 检查不适用",
}


def _fund_empty(code: str, kind: str, shape: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """构造场内基金（ETF/LOF）个股通道的空态响应。

    Args:
        code: 归一化后的 6 位代码。
        kind: 通道类型，取值 profile / holders / financial / fcf。
        shape: 前端渲染所需的最小空结构骨架，如 {"top10": [], "free_top10": []}。

    Returns:
        dict: 统一成功信封，data 含 available=False 与口径说明。
    """
    data: Dict[str, Any] = {
        "symbol": code,
        "available": False,
        "note": _FUND_EMPTY_NOTES.get(kind, "该标的无个股口径数据"),
    }
    if shape:
        data.update(shape)
    return pack_success(data, "本系统", "场内基金无个股口径", None, "本地态", False)


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
    selected = utils.normalize_market(market)
    if selected is None:
        return bad_request(utils.market_error_message())
    normalized = _normalize(symbol, selected)
    if normalized is None:
        return bad_request("证券代码格式不正确，请输入 6 位 A股代码或有效港美股代码")
    code = normalized[0]
    if utils.is_fund_like(code):
        return _fund_empty(code, "profile")

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
    as_of, status, is_realtime = _quote_status(None)
    return pack_result(result, {"symbol": code}, as_of=as_of, market_status=status, is_realtime=is_realtime)


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
        return bad_request("证券代码格式不正确，请输入 6 位 A股代码")
    code = normalized[0]
    if utils.is_fund_like(code):
        return _fund_empty(code, "holders", {"top10": [], "free_top10": []})

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
    data = result.get("data") or {}
    as_of = data.get("top10_period") or data.get("free_top10_period")
    return pack_result(result, {"symbol": code, "top10": [], "free_top10": []},
                       as_of=f"{as_of} 报告期" if as_of else None, market_status="报告期披露", data=data)


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
    selected = utils.normalize_market(market)
    if selected is None:
        return bad_request(utils.market_error_message())
    normalized = _normalize(symbol, selected)
    if normalized is None:
        return bad_request("证券代码格式不正确，请输入 6 位 A股代码或有效港美股代码")
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
    data = result.get("data") or {}
    as_of, status, is_realtime = _quote_status(data.get("quote"))
    return pack_result(result, {"symbol": code, "market": selected, "blocks": {}},
                       as_of=as_of, market_status=status, is_realtime=is_realtime, data=data)


# =============================================================
# 三、财务四接口（A股专用）
# =============================================================
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
        return bad_request("证券代码格式不正确，请输入 6 位 A股代码")
    code = normalized[0]
    if utils.is_fund_like(code):
        return _fund_empty(code, "financial", {"periods": [], "metrics": []})

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
    data = result.get("data") or {}
    periods = data.get("periods") or []
    return pack_result(result, {"symbol": code, "periods": [], "metrics": []},
                       as_of=f"{periods[0]} 报告期" if periods else None,
                       market_status="定期报告", data=data)


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
    selected = utils.normalize_market(market)
    if selected is None:
        return bad_request(utils.market_error_message())
    normalized = _normalize(symbol, selected)
    if normalized is None:
        return bad_request("证券代码格式不正确，请输入 6 位 A股代码或有效港美股代码")
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
        return failure_pack(result, {"symbol": code, "market": selected,
                                 "eps": [], "profit": [], "institutions": [], "detail": [], "years": []})
    data = result["data"] or {}
    years = data.get("years") or []
    return pack_success(
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
        return bad_request("证券代码格式不正确，请输入 6 位 A股代码")
    code = normalized[0]
    if utils.is_fund_like(code):
        return _fund_empty(code, "financial", {"periods": [], "series": []})

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
    data = result.get("data") or {}
    periods = data.get("periods") or []
    return pack_result(result, {"symbol": code, "periods": [], "series": []},
                       as_of=f"{periods[-1]} 年报" if periods else None,
                       market_status="年报披露", data=data)


@router.get("/financial-indicator")
def stock_financial_indicator(
    symbol: str = Query(..., description="证券代码"),
    limit: int = Query(8, ge=1, le=20, description="返回期数"),
    market: str = Query("a", description="a=A股 / hk=港股 / us=美股（港美股为 G-4 新增中文指标）"),
) -> Dict[str, Any]:
    """财务指标明细（ST-09 指标页签）。

    Args:
        symbol: 证券代码。
        limit: 返回期数。
        market: a=A股（新浪财务分析指标）/ hk、us（雪球中文财务指标，G-4 免费源）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    market_text = (market or "a").lower()
    if market_text == "cn":
        market_text = "a"
    if market_text not in ("a", "hk", "us"):
        return bad_request("market 仅支持 a / hk / us")
    normalized = _normalize(symbol, market_text)
    if normalized is None:
        return bad_request("证券代码格式不正确，请检查代码或市场后重试")
    code = normalized[0]
    if utils.is_fund_like(code):
        return _fund_empty(code, "financial", {"items": []})
    if market_text in ("hk", "us"):
        def loader_overseas() -> Dict[str, Any]:
            """雪球港美股中文财务指标取数。

            Returns:
                dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
            """
            return with_fallback(
                [("雪球", "雪球/港美股主要财务指标（中文，36/28 项，含同比）",
                  lambda: overseas_provider.get_financial_indicators(code, market_text, limit))],
                empty_value={"periods": [], "metrics": []},
                validator=lambda value: bool(value.get("periods")),
            )
        result = cache.get_or_load(
            f"stock:fin-indicator:{market_text}:{code}:{limit}", config.TTL["financial"], loader_overseas)
        data = result.get("data") or {}
        last_period = ((data.get("periods") or [{}])[-1].get("date", "") or "").strip()
        return pack_result(result, {"symbol": code, "market": market_text, "periods": []},
                           as_of=f"{last_period} 报告期".strip() or None,
                           market_status="定期报告", data=data)

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
    data = result.get("data") or {}
    periods = data.get("periods") or []
    return pack_result(result, {"symbol": code, "periods": [], "metrics": []},
                       as_of=f"{periods[-1]} 报告期" if periods else None,
                       market_status="定期报告", data=data)


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
        return bad_request("证券代码格式不正确，请输入 6 位 A股代码")
    if statement not in ("资产负债表", "利润表", "现金流量表"):
        return bad_request("statement 仅支持资产负债表/利润表/现金流量表")
    code = normalized[0]
    if utils.is_fund_like(code):
        return _fund_empty(code, "financial", {"statement": statement, "rows": []})

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
    data = result.get("data") or {}
    periods = data.get("periods") or []
    return pack_result(result, {"symbol": code, "statement": statement, "periods": [], "metrics": []},
                       as_of=f"{periods[0]} 报告期" if periods else None,
                       market_status="定期报告", data=data)


# =============================================================
# 第七轮 M7 / M8：FCF 背离检查 + 同业对比（波次 4）
# =============================================================


@router.get("/fcf-health")
def stock_fcf_health(
    symbol: str = Query(..., description="6 位 A股代码"),
    force: bool = Query(False, description="true=绕过缓存"),
) -> Dict[str, Any]:
    """FCF 与利润背离检查（M7）：六项检查 + 结论。

    对应八条原则第 8 条「高资本开支期 FCF 先行转负是利润恶化的领先信号」。
    金融业（无存货/应收/资本开支科目）返回 `applicable=false` 并说明原因，不硬算。

    Args:
        symbol: 6 位 A股代码。
        force: 是否绕过缓存。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`。
    """
    normalized = _normalize(symbol, "a")
    if normalized is None:
        return bad_request("证券代码格式不正确，请输入 6 位 A股代码")
    code = normalized[0]
    if utils.is_fund_like(code):
        return _fund_empty(code, "fcf", {"applicable": False})

    def loader() -> Dict[str, Any]:
        """三表取数并计算（季报数据，缓存 6 小时）。

        Returns:
            dict: FCF 检查结果。
        """
        return fcf_health.get_fcf_health(code)

    try:
        data = loader() if force else cache.get_or_load(
            f"stock:fcf-health:{code}", config.TTL["financial"], loader)
    except ValueError as exc:
        return api_error(ErrorCode.EMPTY_DATA, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("FCF 检查失败 %s：%s", code, type(exc).__name__)
        return api_error(ErrorCode.UPSTREAM_ERROR, f"FCF 检查失败（{type(exc).__name__}），请稍后重试")
    return ok(data)


@router.get("/peer-compare")
def stock_peer_compare(
    symbols: str = Query(..., description="逗号分隔的 2–5 个 A股代码"),
    force: bool = Query(False, description="true=绕过缓存"),
) -> Dict[str, Any]:
    """同业对比（M8）：最多 5 家 × 估值/成长/质量/杠杆/现金流 五类指标。

    只给客观指标与"同组最优"，不做排名打分、不给买卖建议。
    跨行业对比会给出警告（不同行业估值不可直接比）。

    Args:
        symbols: 逗号分隔的代码。
        force: 是否绕过缓存。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`。
    """
    codes = [item.strip() for item in str(symbols or "").split(",") if item.strip()]
    if len(codes) < 2:
        return bad_request("请至少提供 2 个代码（symbols 用逗号分隔）")
    if len(codes) > 5:
        return bad_request("最多对比 5 家公司")
    normalized = []
    for code in codes:
        item = _normalize(code, "a")
        if item is None:
            return bad_request(f"证券代码格式不正确：{code}")
        normalized.append(item[0])

    def loader() -> Dict[str, Any]:
        """逐家取指标（财报 + 行情，缓存 6 小时）。

        Returns:
            dict: 对比结果。
        """
        return peers_provider.compare(normalized)

    try:
        data = loader() if force else cache.get_or_load(
            f"stock:peer:{'-'.join(sorted(normalized))}", config.TTL["financial"], loader)
    except ValueError as exc:
        return api_error(ErrorCode.EMPTY_DATA, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("同业对比失败：%s", type(exc).__name__)
        return api_error(ErrorCode.UPSTREAM_ERROR, f"同业对比失败（{type(exc).__name__}），请稍后重试")
    return ok(data)
