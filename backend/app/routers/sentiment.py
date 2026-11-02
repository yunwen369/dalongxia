"""情绪域路由：/api/sentiment 下 3 个接口（批次 5.2）。

对应规范 v7.2 §8.6 与基线03 §3.7 / §4.8：
- GET /stock   个股利好利空（词典/规则打分，±0.1 三档）
- GET /market  市场情绪温度（可按板块与区域过滤）
- GET /board   板块级情绪

全部由 datasource/sentiment.py 的确定性规则产出：同一文本必然得到同一分值，
不调用大模型、不使用随机数，便于 pytest 回归与前端稳定展示。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from backend.app import config
from backend.app.core import cache
from backend.app.core.errors import pack_success, ErrorCode, api_error
from backend.app.datasource import breadth as breadth_provider
from backend.app.datasource import news as news_provider
from backend.app.datasource import sentiment as sentiment_engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sentiment", tags=["情绪"])


def _bad(message: str) -> Dict[str, Any]:
    """参数错误响应。

    Args:
        message: 中文提示文案。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    return api_error(ErrorCode.INVALID_PARAM, message)


def _pack(data: Dict[str, Any], source: str, caliber: str, as_of: Optional[str] = None) -> Dict[str, Any]:
    """组装响应，附带口径与时间（情绪为派生指标，非实时行情）。

    Args:
        data: 待包装或清洗的数据主体。
        source: 数据源标识（用于降级链溯源与徽章展示）。
        caliber: 口径说明（展示在口径徽章）。
        as_of: 数据时间（北京时间，格式 YYYY-MM-DD HH:MM:SS）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    return pack_success(data, source, caliber, as_of, "情绪快照", False)


def _samples(items: List[Dict[str, Any]], limit: int = 5) -> List[Dict[str, Any]]:
    """取影响度最高的若干条作为证据样本（按 |score| 倒序）。

    Args:
        items: 记录列表。
        limit: 返回条数上限（超出上限按上限截断）。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。
    """
    ordered = sorted(
        items,
        key=lambda item: abs(float((item.get("sentiment") or {}).get("score") or 0.0)),
        reverse=True,
    )
    result: List[Dict[str, Any]] = []
    for item in ordered[:limit]:
        result.append(
            {
                "title": item.get("title"),
                "time": item.get("time"),
                "source": item.get("source"),
                "score": (item.get("sentiment") or {}).get("score"),
                "label": (item.get("sentiment") or {}).get("label"),
                "events": [event["name"] for event in (item.get("sentiment") or {}).get("events") or []],
            }
        )
    return result


@router.get("/stock")
def sentiment_stock(
    symbol: str = Query(..., description="证券代码"),
    name: str = Query("", description="证券名称（用于提升命中率，可选）"),
    limit: int = Query(20, ge=1, le=100),
) -> Dict[str, Any]:
    """个股情绪：个股新闻 + 快讯中命中代码/名称的条目。

    Args:
        symbol: 证券代码。
        name: 证券名称（用于提升命中率，可选）。
        limit: 返回条数上限（超出上限按上限截断）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    code = str(symbol).strip()
    if not code:
        return _bad("symbol 参数不可为空")
    keyword = name.strip()

    def loader() -> Dict[str, Any]:
        """个股新闻取数（东财）。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
        try:
            return {"items": news_provider.get_stock_news(code, limit), "error": None}
        except Exception as exc:  # noqa: BLE001 - 失败由上层转空态
            logger.info("个股新闻取数失败：%s（%s）", code, type(exc).__name__)
            return {"items": [], "error": "个股新闻暂不可用"}

    cached = cache.get_or_load(f"sentiment:stock:src:{code}:{limit}", config.TTL["news_flash"], loader)
    items = sentiment_engine.analyze_items(cached.get("items") or [], sectors=news_provider.sector_keywords())
    if keyword:
        # 名称命中但标题未含代码的条目也纳入（如"中微公司"）
        for item in items:
            if keyword in f"{item.get('title', '')}{item.get('text', '')}":
                item["matched_by"] = "name"
    aggregate = sentiment_engine.aggregate(items, scope=f"{code} {keyword}".strip())
    aggregate["samples"] = _samples(items)
    aggregate["degraded"] = bool(cached.get("error"))
    aggregate["symbol"] = code
    return _pack(
        aggregate,
        "东财/个股新闻",
        "本地规则引擎（18 类事件、三维度权重 0.45/0.35/0.20、±0.1 三档）",
        items[0].get("time") if items else None,
    )


@router.get("/market")
def sentiment_market(
    board: str = Query("", description="可选：限定板块"),
    region: str = Query("cn", description="cn=国内快讯 / global=全球快讯"),
    limit: int = Query(60, ge=1, le=200),
) -> Dict[str, Any]:
    """市场情绪温度：国内快讯或全球快讯聚合，可限定板块。

    Args:
        board: 可选：限定板块。
        region: cn=国内快讯 / global=全球快讯。
        limit: 返回条数上限（超出上限按上限截断）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    if region not in ("cn", "global"):
        return _bad("region 仅支持 cn/global")
    keywords: List[str] = []
    if board:
        node = news_provider.resolve_board(board)
        if node is None:
            return api_error(ErrorCode.NOT_FOUND, "板块不存在，请从板块树中选择。", data={"board": board})
        keywords = node.get("keywords") or [board]

    def loader() -> Dict[str, Any]:
        """按区域取新闻池（cn=财联社，global=三源合并）。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
        if region == "cn":
            # 多源快讯：冻结环境财联社不可用时回落东财/新浪，避免整条链路 500
            return {"items": news_provider.get_flash_multi(limit), "error": None}
        merged = news_provider.dedup(news_provider.get_global(limit))
        return {"items": merged, "error": None}

    cached = cache.get_or_load(f"sentiment:market:{region}:{limit}", config.TTL["news_flash"], loader)
    items = sentiment_engine.analyze_items(cached.get("items") or [], sectors=news_provider.sector_keywords())
    if keywords:
        items = [
            item for item in items
            if any(word and word in f"{item.get('title', '')} {item.get('text', '')}" for word in keywords)
        ]
    aggregate = sentiment_engine.aggregate(items, scope=f"market/{region}" + (f"/{board}" if board else ""))
    aggregate["samples"] = _samples(items)
    aggregate["region"] = region
    aggregate["board"] = board or None
    # FN-01：首页「市场情绪」组件复用本接口，附带沪深两市涨跌家数（同页双指标，不另造接口）
    aggregate["breadth"] = breadth_provider.market_breadth()
    return _pack(
        aggregate,
        "财联社" if region == "cn" else "东财/新浪/同花顺",
        "本地规则引擎聚合；温度为派生指标（50 + 均分×500，裁剪 0–100）",
        items[0].get("time") if items else None,
    )


@router.get("/board")
def sentiment_board(
    board: str = Query(..., description="板块名（末级/二级/一级）"),
    limit: int = Query(50, ge=1, le=200),
) -> Dict[str, Any]:
    """板块级情绪：板块命中快讯聚合。

    Args:
        board: 板块名（末级/二级/一级）。
        limit: 返回条数上限（超出上限按上限截断）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    name = (board or "").strip()
    if not name:
        return _bad("board 参数不可为空")
    # R2-06：板块解析 / 取数 / 打分 / 聚合任一步异常，都必须在路由层收口为
    # UPSTREAM_ERROR 中文契约（data 非 null），禁止漏成 500 INTERNAL_ERROR。
    # 同花顺 V8 中途崩溃属 R-012 环境项，但契约不能依赖它「不崩」。
    try:
        node = news_provider.resolve_board(name)
        if node is None:
            return api_error(ErrorCode.NOT_FOUND, "板块不存在，请从板块树中选择。", data={"board": name})

        def loader() -> Dict[str, Any]:
            """板块快讯取数。

            Returns:
                dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
            """
            return {"items": news_provider.search_by_sector(name, "time", max(limit, 60)), "error": None}

        cached = cache.get_or_load(f"sentiment:board:{name}:{limit}", config.TTL["news_flash"], loader)
        items = sentiment_engine.analyze_items(cached.get("items") or [], sectors=news_provider.sector_keywords())
        aggregate = sentiment_engine.aggregate(items, scope=name)
    except Exception as exc:  # noqa: BLE001 - 上游异常统一收敛为契约错误
        logger.warning("板块情绪计算失败：%s（%s）", name, type(exc).__name__, exc_info=True)
        return api_error(
            ErrorCode.UPSTREAM_ERROR,
            "板块情绪计算失败（数据源通道异常），请稍后点击刷新重试。",
            data={"board": name},
        )
    aggregate["samples"] = _samples(items)
    aggregate["board"] = name
    aggregate["level"] = node.get("level")
    aggregate["keywords"] = node.get("keywords")
    return _pack(
        aggregate,
        "财联社/东财/新浪/同花顺",
        "板块关键词命中（非语义检索）+ 本地规则引擎聚合",
        items[0].get("time") if items else None,
    )
