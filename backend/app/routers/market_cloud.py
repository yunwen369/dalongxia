"""大盘云图路由：/api/market-cloud 下的概览与筛选树接口。

对应 PRD《市场大盘云图前后端开发技术方案 V1.0》§7.1 / §7.2：
- GET /api/market-cloud/overview  行情云图概览（一次拉全市场分组树）
- GET /api/market-cloud/filters   左侧筛选树（一级/二级分组 + 数量/规模/均涨）

返回遵循项目统一外壳 ``{data, error, error_code}``；``data`` 内除 PRD 字段外
另带 ``source`` / ``asOf`` / ``stale`` 三性字段，前端据此渲染延迟徽章。

参数校验按 §7.1 表格逐条落地：
- market 未知 → INVALID_PARAM；
- areaMetric / period / direction / sortBy 非法 → 回退默认值，不报错；
- pctMin/pctMax 越界或互斥 → INVALID_PARAM（PRD 明确要求返回错误）；
- filters 中未知编码 → 忽略并记录日志，不报错；
- refresh=force=1 → 跳过缓存强制回源。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query

from backend.app.core.errors import ErrorCode, api_error, bad_request, pack_success
from backend.app.datasource import market_cloud
from backend.app.datasource.utils import now_text

logger = logging.getLogger(__name__)


def _truthy_refresh(value: Optional[str]) -> bool:
    """解析 ``refresh`` 查询参数。

    兼容三种写法：``refresh=1`` / ``refresh=true`` / ``refresh=force=1``
    （最后一种是前端历史遗留，参数值本身带 ``force=`` 前缀）。
    """
    text = str(value or "").strip().lower()
    if not text:
        return False
    if text in ("1", "true", "force", "yes"):
        return True
    return text.startswith("force")

router = APIRouter(prefix="/api/market-cloud", tags=["大盘云图"])

_ALLOWED_MARKETS = tuple(market_cloud.MARKET_SPECS)
_MAX_KEYWORD = 20


def _market_or_none(market: str):
    """校验市场枚举。

    Args:
        market: 请求参数 market。

    Returns:
        Optional[Dict[str, Any]]: 非法时返回统一错误响应，合法时返回 None。
    """
    if market not in _ALLOWED_MARKETS:
        return bad_request(
            f"market 取值非法：{market!r}，仅支持 {'/'.join(_ALLOWED_MARKETS)}"
        )
    return None


def _check_pct(pct_min: Optional[float], pct_max: Optional[float]):
    """校验涨跌幅区间（§7.1：-20 ~ +20，且下限 ≤ 上限）。

    Args:
        pct_min: 涨跌幅下限。
        pct_max: 涨跌幅上限。

    Returns:
        Optional[Dict[str, Any]]: 非法时返回统一错误响应，合法时返回 None。
    """
    for label, value in (("pctMin", pct_min), ("pctMax", pct_max)):
        if value is None:
            continue
        if value < -20 or value > 20:
            return bad_request(f"{label} 超出取值范围（-20 ~ 20）")
    if pct_min is not None and pct_max is not None and pct_min > pct_max:
        return bad_request("pctMin 不能大于 pctMax")
    return None


@router.get("/overview")
def market_cloud_overview(
    market: str = Query("stock", description="stock/etf/convertible/hk/us"),
    areaMetric: Optional[str] = Query(None, description="面积口径，非法值回退市场默认"),
    filters: Optional[str] = Query(None, description="一级/二级分组编码，逗号分隔"),
    keyword: Optional[str] = Query(None, description="代码/名称模糊匹配，≤20 字"),
    view: Optional[str] = Query(None, description="A股快捷视图"),
    period: Optional[str] = Query(None, description="day/week/month/year（周=5日、月=20日、年=年初至今）"),
    direction: Optional[str] = Query(None, description="all/up/down"),
    pctMin: Optional[float] = Query(None, description="涨跌幅下限（%）"),
    pctMax: Optional[float] = Query(None, description="涨跌幅上限（%）"),
    sortBy: Optional[str] = Query(None, description="弹窗/排行排序维度"),
    refresh: Optional[str] = Query(None, description="force=1 跳过缓存"),
) -> Dict[str, Any]:
    """行情云图概览（PRD §7.1）。

    Args:
        market: 市场枚举。
        areaMetric: 面积口径。
        filters: 分组编码，逗号分隔。
        keyword: 代码/名称模糊匹配。
        view: A 股快捷视图。
        period: 涨跌周期（day/week/month/year，均由上游字段直接支撑）。
        direction: 涨跌方向。
        pctMin: 涨跌幅下限。
        pctMax: 涨跌幅上限。
        sortBy: 排序维度（概览内回显，供弹窗复用）。
        refresh: ``force=1`` 时强制回源。

    Returns:
        Dict[str, Any]: 统一响应结构；``data`` 为 §7.1 字段全集。
    """
    invalid = _market_or_none(market) or _check_pct(pctMin, pctMax)
    if invalid is not None:
        return invalid

    text = (keyword or "").strip()
    if len(text) > _MAX_KEYWORD:
        return bad_request(f"keyword 长度不能超过 {_MAX_KEYWORD} 个字符")

    try:
        data = market_cloud.overview(
            market,
            area_metric=areaMetric,
            filters=filters,
            keyword=text,
            view=view,
            period=period,
            direction=direction,
            pct_min=pctMin,
            pct_max=pctMax,
            sort_by=sortBy,
            force=_truthy_refresh(refresh),
        )
    except Exception as exc:  # noqa: BLE001 - 上游异常统一转为可读错误
        logger.exception("大盘云图概览取数失败 market=%s", market)
        return api_error(
            ErrorCode.UPSTREAM_ERROR,
            f"行情源暂时不可用（{type(exc).__name__}），请稍后重试",
        )

    caliber = (
        f"东方财富 push2 行情快照，全市场 {data.get('total', 0)} 只标的；"
        f"面积口径 {market_cloud.AREA_LABELS.get(data['areaMetric'], data['areaMetric'])}"
        f"（{data['areaUnit']}），色阶量程 ±{data['legend']['max']:g}%"
    )
    return pack_success(
        data,
        "eastmoney_clist",
        caliber,
        data.get("asOf") or now_text(),
        data.get("sessionText") or "快照",
        bool(data.get("isRealtime")),
    )


@router.get("/filters")
def market_cloud_filters(
    market: str = Query("stock", description="stock/etf/convertible/hk/us"),
) -> Dict[str, Any]:
    """左侧筛选树（PRD §7.2）。

    Args:
        market: 市场枚举。

    Returns:
        Dict[str, Any]: 统一响应结构；``data.nodes`` 为一级/二级节点树。
    """
    invalid = _market_or_none(market)
    if invalid is not None:
        return invalid

    try:
        data = market_cloud.filters_tree(market)
    except Exception as exc:  # noqa: BLE001 - 上游异常统一转为可读错误
        logger.exception("大盘云图筛选树取数失败 market=%s", market)
        return api_error(
            ErrorCode.UPSTREAM_ERROR,
            f"筛选树暂时不可用（{type(exc).__name__}），请稍后重试",
        )

    return pack_success(
        data,
        "eastmoney_clist",
        "按面积口径合计值降序排列，节点含标的数、合计规模与平均涨跌幅",
        now_text(),
        "快照",
        False,
    )


@router.get("/item")
def market_cloud_item(
    market: str = Query("stock", description="stock/etf/convertible/hk/us"),
    code: str = Query(..., description="标的代码"),
    sortBy: Optional[str] = Query(None, description="排行维度，非法值回退 changePct"),
) -> Dict[str, Any]:
    """单标的详情（PRD §7.3）。

    Args:
        market: 市场枚举。
        code: 标的代码。
        sortBy: 排行维度。

    Returns:
        Dict[str, Any]: 统一响应结构；``data`` 为 PopoverDTO。
    """
    invalid = _market_or_none(market)
    if invalid is not None:
        return invalid

    target = (code or "").strip()
    if not target:
        return bad_request("code 不能为空")

    try:
        data = market_cloud.item_detail(market, target, sort_by=sortBy)
    except Exception as exc:  # noqa: BLE001 - 上游异常统一转为可读错误
        logger.exception("大盘云图单标的取数失败 market=%s code=%s", market, target)
        return api_error(
            ErrorCode.UPSTREAM_ERROR,
            f"标的详情暂时不可用（{type(exc).__name__}），请稍后重试",
        )

    if data is None:
        return api_error(ErrorCode.NOT_FOUND, f"未找到标的 {target}（market={market}）")

    return pack_success(
        data,
        "eastmoney_clist",
        f"标的详情与同分组排行（前 {market_cloud._RANKING_LIMIT} 条）",
        data.get("asOf") or now_text(),
        "快照",
        False,
    )
