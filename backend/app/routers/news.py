"""新闻域路由：/api/news 下 7 个接口（批次 5.2）。

对应规范 v7.2 §8.6 与基线03 §3.6：
- GET  /flash          财联社 7×24 快讯（本地初评）
- GET  /stock          东财个股新闻（本地初评）
- GET  /global         全球快讯三源合并去重（东财 + 新浪 + 同花顺）
- GET  /sector-search  快讯池按板块命中过滤 + 影响度排序
- GET  /board-tree     内置三市场板块树（一级 6 / 二级 15 / 末级 28）
- GET  /ai-feed        多源池合并去重 + 板块命中 + 本地初评（确定性规则）
- POST /ai-review      复用用户 AI 配置做二次复核（未配置时明确引导，不静默降级）

红线：初评一律由 datasource/sentiment.py 的确定性规则产出，不调用大模型；
只有用户显式点击「AI 复核」才发起 /ai-review，且不把结果写回缓存。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Body, Query

from backend.app import config
from backend.app.core import cache
from backend.app.core.errors import ErrorCode, api_error, ok
from backend.app.core.resilient import with_fallback
from backend.app.datasource import news as news_provider
from backend.app.datasource import sentiment as sentiment_engine
from backend.app.datasource import utils

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/news", tags=["新闻"])

_SORTS = ("time", "impact", "relevance")
_AI_FEED_REGIONS = ("global", "cn")
_MARKETS = ("all", "a", "hk", "us")


def _bad(message: str) -> Dict[str, Any]:
    """参数错误响应。

    Args:
        message: 中文提示文案。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    return api_error(ErrorCode.INVALID_PARAM, message)


def _failure(result: Dict[str, Any], empty: Any) -> Dict[str, Any]:
    """降级链失败 → 统一错误响应。

    Args:
        result: 上游返回结果。
        empty: 空数据时的返回值。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    try:
        code = ErrorCode(str(result.get("error_code")))
    except ValueError:
        code = ErrorCode.UPSTREAM_ERROR
    return api_error(code, result.get("error"), data=empty)


def _pack(data: Dict[str, Any], source: str, caliber: str, as_of: Optional[str],
          market_status: str = "资讯快照", is_realtime: bool = False) -> Dict[str, Any]:
    """组装响应并附带数据源/口径/时间。

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
    payload.update({
        "source": source,
        "caliber": caliber,
        "as_of": as_of or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "market_status": market_status,
        "is_realtime": bool(is_realtime),
    })
    return ok(payload)


def _sort_items(items: List[Dict[str, Any]], sort: str) -> List[Dict[str, Any]]:
    """按 time / impact / relevance 排序。

    - time：发布时间倒序；
    - impact：|情绪分| 倒序（影响度）；
    - relevance：板块命中数倒序，其次 |情绪分|，最后时间。

    Args:
        items: 记录列表。
        sort: 排序方式：time=按时间倒序 / relevance=按相关度。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。
    """
    if sort == "impact":
        return sorted(
            items,
            key=lambda item: abs(float((item.get("sentiment") or {}).get("score") or 0.0)),
            reverse=True,
        )
    if sort == "relevance":
        return sorted(
            items,
            key=lambda item: (
                len(item.get("boards") or []),
                abs(float((item.get("sentiment") or {}).get("score") or 0.0)),
                item.get("time") or "",
            ),
            reverse=True,
        )
    return sorted(items, key=lambda item: item.get("time") or "", reverse=True)


@router.get("/flash")
def news_flash(
    limit: int = Query(50, ge=1, le=200),
    board: str = Query("", description="按板块名过滤（可选）"),
    sort: str = Query("time", description="time/impact"),
) -> Dict[str, Any]:
    """财联社 7×24 快讯 + 本地初评。

    Args:
        limit: 返回条数上限（超出上限按上限截断）。
        board: 按板块名过滤（可选）。
        sort: 排序方式：time=按时间倒序 / relevance=按相关度。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    if sort not in _SORTS:
        return _bad("sort 仅支持 time/impact/relevance")

    def loader() -> Dict[str, Any]:
        """取数并打分。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
        return with_fallback(
            [("财联社", "财联社/7×24 快讯（标题+内容）；不可用时回落东财/新浪全球快讯",
              lambda: news_provider.get_flash_multi(limit))],
            empty_value=[],
        )

    result = cache.get_or_load("news:flash", config.TTL["news_flash"], loader)
    if result.get("error"):
        # 空数据属自然空态（§7 空态与错误态严格区分），只有通道异常才走错误态
        if result.get("error_code") == ErrorCode.EMPTY_DATA.value:
            return _pack({"items": [], "count": 0, "board": board or None},
                         "财联社", "快讯池为空，请稍后刷新", None)
        return _failure(result, {"items": []})
    items = sentiment_engine.analyze_items(result["data"] or [], sectors=news_provider.sector_keywords())
    if board:
        node = news_provider.resolve_board(board)
        words = (node or {}).get("keywords") or [board]
        items = [
            item for item in items
            if any(word and word in f"{item.get('title', '')} {item.get('text', '')}" for word in words)
        ]
    items = _sort_items(items, sort)[:limit]
    return _pack(
        {"items": items, "count": len(items), "board": board or None},
        result.get("source") or "",
        result.get("caliber") or "",
        items[0].get("time") if items else None,
    )


@router.get("/stock")
def news_stock(
    symbol: str = Query(..., description="证券代码"),
    limit: int = Query(20, ge=1, le=100),
) -> Dict[str, Any]:
    """个股新闻（东财）+ 本地初评 + 命中板块。

    Args:
        symbol: 证券代码。
        limit: 返回条数上限（超出上限按上限截断）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    code = str(symbol).strip()
    if not code:
        return _bad("symbol 参数不可为空")

    def loader() -> Dict[str, Any]:
        """个股新闻取数。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
        return with_fallback(
            [("东财", "东财/个股新闻（stock_news_em）", lambda: news_provider.get_stock_news(code, limit))],
            empty_value=[],
        )

    result = cache.get_or_load(f"news:stock:{code}:{limit}", config.TTL["news_flash"], loader)
    if result.get("error"):
        if result.get("error_code") == ErrorCode.EMPTY_DATA.value:
            return _pack({"symbol": code, "items": [], "count": 0},
                         "东财", "该标的暂无相关新闻", None)
        return _failure(result, {"items": [], "symbol": code})
    items = sentiment_engine.analyze_items(result["data"] or [], sectors=news_provider.sector_keywords())
    for item in items:
        item["boards"] = news_provider.match_boards(f"{item.get('title', '')} {item.get('text', '')}")
    return _pack(
        {"symbol": code, "items": items, "count": len(items)},
        result.get("source") or "",
        result.get("caliber") or "",
        items[0].get("time") if items else None,
    )


@router.get("/global")
def news_global(limit: int = Query(100, ge=1, le=300)) -> Dict[str, Any]:
    """全球快讯三源合并 + 去重 + 本地初评。

    Args:
        limit: 返回条数上限（超出上限按上限截断）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """

    def loader() -> Dict[str, Any]:
        """三源合并取数。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
        return with_fallback(
            [("东财/新浪/同花顺", "东财全球+新浪+同花顺（失败源自动跳过）",
              lambda: news_provider.get_global(limit))],
            empty_value=[],
        )

    result = cache.get_or_load(f"news:global:{limit}", config.TTL["news_flash"], loader)
    if result.get("error"):
        if result.get("error_code") == ErrorCode.EMPTY_DATA.value:
            return _pack({"items": [], "count": 0, "before_dedup": 0},
                         "东财/新浪/同花顺", "三源均无新条目，请稍后刷新", None)
        return _failure(result, {"items": []})
    raw = result["data"] or []
    merged = news_provider.dedup(raw)
    items = sentiment_engine.analyze_items(merged, sectors=news_provider.sector_keywords())
    return _pack(
        {"items": items, "count": len(items), "before_dedup": len(raw)},
        result.get("source") or "",
        "三源合并后按标题归一化去重",
        items[0].get("time") if items else None,
    )


@router.get("/sector-search")
def news_sector_search(
    sector: str = Query(..., description="板块名（末级/二级/一级均可）"),
    sort: str = Query("time", description="time/impact"),
    limit: int = Query(50, ge=1, le=200),
) -> Dict[str, Any]:
    """板块快讯检索：快讯池按板块命中过滤 + 排序。

    Args:
        sector: 板块名（末级/二级/一级均可）。
        sort: 排序方式：time=按时间倒序 / relevance=按相关度。
        limit: 返回条数上限（超出上限按上限截断）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    if sort not in _SORTS:
        return _bad("sort 仅支持 time/impact/relevance")
    name = (sector or "").strip()
    if not name:
        return _bad("sector 参数不可为空")
    node = news_provider.resolve_board(name)
    if node is None:
        return api_error(ErrorCode.NOT_FOUND, "板块不存在，请从板块树中选择。",
                         data={"items": [], "sector": name})

    def loader() -> Dict[str, Any]:
        """板块快讯取数（财联社 + 三源全球）。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
        return with_fallback(
            [("财联社/东财/新浪/同花顺", "快讯池按板块关键词命中过滤",
              lambda: news_provider.search_by_sector(name, "time", max(limit, 60)))],
            empty_value=[],
        )

    result = cache.get_or_load(f"news:sector:{name}:{limit}", config.TTL["news_flash"], loader)
    if result.get("error"):
        if result.get("error_code") == ErrorCode.EMPTY_DATA.value:
            return _pack({"sector": name, "level": node.get("level"), "items": [], "count": 0},
                         "财联社/东财/新浪/同花顺", "该板块近期无命中资讯（关键词命中口径）", None)
        return _failure(result, {"items": [], "sector": name})
    items = sentiment_engine.analyze_items(result["data"] or [], sectors=news_provider.sector_keywords())
    # 该接口本身已按板块命中过滤，逐条回填 boards 供前端展示与排序
    for item in items:
        item["boards"] = [name]
    items = _sort_items(items, sort)[:limit]
    return _pack(
        {"sector": name, "level": node.get("level"), "items": items, "count": len(items)},
        result.get("source") or "",
        "板块关键词命中（含别名），非语义检索",
        items[0].get("time") if items else None,
    )


@router.get("/board-tree")
def news_board_tree() -> Dict[str, Any]:
    """内置三市场板块树（版本化静态配置，非实时数据）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    tree = news_provider.get_board_tree()
    return _pack(
        tree,
        "内置配置",
        "本地静态板块树（版本 %s），供筛选与板块命中" % tree["version"],
        None,
        "静态配置",
        False,
    )


@router.get("/ai-feed")
def news_ai_feed(
    boards: str = Query("", description="逗号分隔板块名（可选）"),
    sort: str = Query("relevance", description="relevance/time/impact"),
    limit: int = Query(60, ge=1, le=200),
    region: str = Query("global", description="global/cn"),
    market: str = Query("all", description="all/a/hk/us"),
    symbol: str = Query("", description="可选：并入东财个股新闻"),
) -> Dict[str, Any]:
    """多源新闻池：合并去重 + 板块命中 + 本地初评（确定性规则）。

    Args:
        boards: 逗号分隔板块名（可选）。
        sort: 排序方式：time=按时间倒序 / relevance=按相关度。
        limit: 返回条数上限（超出上限按上限截断）。
        region: 区域标识：cn=A股 / us=美股 / hk=港股。
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。
        symbol: 可选：并入东财个股新闻。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    if sort not in _SORTS:
        return _bad("sort 仅支持 relevance/time/impact")
    if region not in _AI_FEED_REGIONS:
        return _bad("region 仅支持 global/cn")
    if market not in _MARKETS:
        return _bad("market 仅支持 all/a/hk/us")

    board_list = [item.strip() for item in (boards or "").split(",") if item.strip()]
    unknown = [item for item in board_list if news_provider.resolve_board(item) is None]
    if unknown:
        return _bad("以下板块不在板块树中：" + "、".join(unknown))
    if len(board_list) > 6:
        return _bad("boards 最多支持 6 个板块")

    code = str(symbol).strip()
    key = f"news:aifeed:{region}:{','.join(board_list)}:{limit}:{code}"

    def loader() -> Dict[str, Any]:
        """多源池取数（财联社 + 三源全球 + 可选个股）。

        板块过滤放到路由层做：这样「板块无命中」是空态而不是上游错误，
        避免把自然空态渲染成错误态（§7 空错严格区分）。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
        return with_fallback(
            [("财联社/东财/新浪/同花顺", "多源合并去重（个股源在指定 symbol 时并入）",
              lambda: news_provider.build_ai_feed(boards=None, limit=max(limit, 60), symbol=code))],
            empty_value={"items": [], "channels": []},
            validator=lambda value: bool(value.get("items")),
        )

    result = cache.get_or_load(key, config.TTL["news_flash"], loader)
    if result.get("error"):
        if result.get("error_code") == ErrorCode.EMPTY_DATA.value:
            return _pack({"items": [], "count": 0, "channels": [], "region": region,
                          "market": market, "boards": board_list},
                         "财联社/东财/新浪/同花顺", "多源池暂无新条目，请稍后刷新", None)
        return _failure(result, {"items": [], "channels": []})
    payload = result["data"] or {}
    raw_items = payload.get("items") or []
    items = sentiment_engine.analyze_items(raw_items, sectors=news_provider.sector_keywords())
    # 板块过滤：一/二级板块展开为末级子孙再匹配（新闻命中只标末级）
    if board_list:
        allowed_leaves = {
            leaf for board in board_list for leaf in news_provider.board_leaf_names(board)
        }
        items = [item for item in items if set(item.get("boards") or []) & allowed_leaves]

    # 区域过滤：cn 只保留国内快讯（财联社/东财个股），global 全量
    if region == "cn":
        items = [item for item in items if item.get("channel") in ("财联社快讯", "东财个股")]
    # 市场过滤：仅保留「该市场有代表个股」的末级板块命中项，口径在 caliber 注明
    if market != "all":
        market_key = market.upper()
        allowed: List[str] = []
        for leaf_name in {board for item in items for board in (item.get("boards") or [])}:
            node = news_provider.resolve_board(leaf_name)
            if not node:
                continue
            for level1 in news_provider.BOARD_TREE:
                for level2 in level1["children"]:
                    for leaf in level2["children"]:
                        if leaf["name"] == leaf_name and leaf["stocks"].get(market_key):
                            allowed.append(leaf_name)
        items = [item for item in items if any(board in allowed for board in (item.get("boards") or []))]

    items = _sort_items(items, sort)[:limit]
    caliber = "多源合并去重 + 板块关键词命中 + 本地确定性初评"
    if region == "cn":
        caliber += "；region=cn 仅保留国内快讯"
    if market != "all":
        caliber += f"；market={market} 仅保留该市场有代表个股的板块命中项"
    return _pack(
        {
            "items": items,
            "count": len(items),
            "channels": payload.get("channels") or [],
            "before_dedup": payload.get("total_before_dedup"),
            "after_dedup": payload.get("total_after_dedup"),
            "region": region,
            "market": market,
            "boards": board_list,
        },
        result.get("source") or "",
        caliber,
        items[0].get("time") if items else None,
    )


@router.post("/ai-review")
def news_ai_review(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """调用用户自备 AI 做二次复核（未配置时明确引导，不静默降级）。

    Body:
        {title, content, board, path, sentiment, strength}

    Args:
        payload: 请求体（JSON 对象）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    title = str(payload.get("title") or "").strip()
    content = str(payload.get("content") or "").strip()
    if not title and not content:
        return _bad("title 与 content 至少提供一个")

    ai = config.load_ai_config()
    if not ai.api_key or not ai.base_url:
        return api_error(
            ErrorCode.NOT_CONFIGURED,
            "AI 复核需要先在设置中配置 OpenAI 兼容接口（base_url / api_key / model），当前未配置。",
            data={"reviewed": False, "guidance": "请到「设置 → AI 设置」填写后重试"},
        )

    board = str(payload.get("board") or "").strip()
    local_label = str(payload.get("sentiment") or "").strip()
    local_strength = str(payload.get("strength") or "").strip()
    prompt = (
        "你是中文投研助手。请复核下面这条财经资讯对指定板块/个股的利好利空判断，"
        "只输出 JSON：{\"sentiment\":\"利好|利空|中性\",\"strength\":\"强|中|弱\",\"reason\":\"不超过80字\"}。\n"
        f"板块：{board or '未指定'}\n标题：{title}\n正文：{content[:800]}\n"
        f"本地规则初评：{local_label or '未提供'}/{local_strength or '未提供'}"
    )
    url = ai.base_url.rstrip("/") + "/chat/completions"
    started = time.time()
    try:
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {ai.api_key}", "Content-Type": "application/json"},
            json={
                "model": ai.model or "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
            },
            timeout=90,  # 规范：AI 复核超时 90s
        )
    except requests.exceptions.Timeout:
        return api_error(ErrorCode.UPSTREAM_TIMEOUT, "AI 复核超时（90 秒），请稍后重试或改用本地初评。",
                         data={"reviewed": False})
    except Exception as exc:  # noqa: BLE001 - 网络类异常统一转中文
        logger.warning("AI 复核请求失败：%s", type(exc).__name__)
        return api_error(ErrorCode.UPSTREAM_ERROR, "AI 接口连接失败，请检查 base_url 与网络。",
                         data={"reviewed": False})

    if response.status_code in (401, 403):
        return api_error(ErrorCode.UNAUTHORIZED, "AI 接口鉴权失败，请检查 api_key。",
                         data={"reviewed": False})
    if response.status_code >= 400:
        logger.warning("AI 复核返回 %s", response.status_code)
        return api_error(ErrorCode.UPSTREAM_ERROR, f"AI 接口返回异常状态（{response.status_code}）。",
                         data={"reviewed": False})

    try:
        body = response.json()
        text = body["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001 - 返回结构不符即视为上游异常
        return api_error(ErrorCode.UPSTREAM_ERROR, "AI 返回内容无法解析，请检查模型是否兼容 OpenAI 格式。",
                         data={"reviewed": False})

    return _pack(
        {
            "reviewed": True,
            "model": ai.model,
            "elapsed_ms": int((time.time() - started) * 1000),
            "local": {"sentiment": local_label or None, "strength": local_strength or None},
            "review": {"raw": text[:2000]},
        },
        "用户配置 AI",
        "AI 复核结果仅供参考，不写入缓存、不参与 sentiment 接口计算",
        None,
        "AI 复核",
        False,
    )
