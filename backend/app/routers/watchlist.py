# -*- coding: utf-8 -*-
"""自选股路由：/api/watchlist（W 自选股 · P0）。

契约（统一信封 {data,error,error_code}）：
- GET    /api/watchlist            读取清单 + 实时行情快照（四市场混合）
- POST   /api/watchlist            新增（幂等去重），body {symbol, market, name?}
- DELETE /api/watchlist/{symbol}   移除，query market=a/hk/us/fund
- PATCH  /api/watchlist/{symbol}   改展示名，body {name}

行情口径复用既有 provider：A股腾讯→新浪、港股/美股腾讯、场外基金取最新净值；
任一标的取数失败只影响该行（quote=null + note），不拖垮整张表。
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict

from fastapi import APIRouter, Body, Query

from backend.app import config
from backend.app.core import cache
from backend.app.core.errors import ErrorCode, NonRetryableError, api_error, ok
from backend.app.core.resilient import with_fallback
from backend.app.datasource import ashare, fund as fund_provider, tencent, utils
from backend.app.datasource import watchlist as store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/watchlist", tags=["自选股"])
# R3-21：复用 datasource/watchlist.py 的市场白名单，避免两份定义。


def _quote_for(item: Dict[str, Any]) -> Dict[str, Any]:
    """取单个自选项的行情快照。

    Args:
        item: 自选条目 {symbol, market, name}。

    Returns:
        dict: 条目 + quote（可能为 None）+ note（失败原因，中文）。
    """
    symbol = str(item.get("symbol") or "")
    market = str(item.get("market") or "a")
    row = {**item, "quote": None, "note": ""}
    if market == "fund":
        try:
            nav = fund_provider.get_nav(symbol, 1, 1)
            latest = (nav.get("items") or [{}])[0]
            row["quote"] = {
                "price": latest.get("nav"),
                "prev_close": None,
                "change": None,
                "pct": latest.get("percentage"),
                "amount": None,
                "pe_ttm": None,
                "currency": "CNY",
                "market_status": "净值",
                "quote_time": latest.get("date"),
            }
            row["name"] = row.get("name") or fund_provider.get_info(symbol).get("name") or symbol
        except Exception as exc:  # noqa: BLE001 - 单标的失败只标记该行
            row["note"] = f"净值暂不可用（{type(exc).__name__}）"
        return row

    candidates = [("腾讯", "腾讯/实时快照（收盘后为最后成交快照）", lambda: tencent.get_quote(symbol, market))]
    if market == "a":
        candidates.append(("新浪", "新浪/实时快照（收盘后为最后成交快照）", lambda: ashare.get_quote_sina(symbol)))
    result = with_fallback(candidates, key=f"watchlist:quote:{market}")
    if result.get("error"):
        row["note"] = result.get("error")
        return row
    quote = result["data"]
    row["quote"] = {
        "price": quote.get("price"),
        "prev_close": quote.get("prev_close"),
        "change": quote.get("change"),
        "pct": quote.get("pct"),
        "amount": quote.get("amount"),
        "pe_ttm": quote.get("pe_ttm"),
        "currency": quote.get("currency"),
        "market_status": quote.get("market_status"),
        "quote_time": quote.get("quote_time"),
    }
    row["name"] = row.get("name") or quote.get("name") or symbol
    row["source"] = result.get("source")
    row["caliber"] = result.get("caliber")
    return row


@router.get("")
def list_watchlist() -> Dict[str, Any]:
    """读取自选清单与实时行情。

    Returns:
        dict: 统一返回结构；data.items 每行含 quote 与双徽章字段。
    """
    items = store.list_items()
    if not items:
        return ok({
            "items": [],
            "counts": {"total": 0, "up": 0, "down": 0, "flat": 0,
                       "by_market": {}},
            "source": "本地自选清单",
            "caliber": "自选股=关注清单（不含成本），与持仓台账分离",
            "as_of": utils.now_text(),
            "market_status": "空清单",
            "is_realtime": False,
            "empty_note": "还没有自选股，从个股详情页点 ★ 添加",
        })

    def load(item: Dict[str, Any]) -> Dict[str, Any]:
        """带缓存的单行取数（15s TTL，避免 30 个标的打爆源站）。

        Args:
            item: 自选条目。

        Returns:
            dict: 含 quote 的行。
        """
        key = f"watchlist:row:{item['market']}:{item['symbol']}"
        return cache.get_or_load(key, config.TTL["watchlist"], lambda: _quote_for(item))

    with ThreadPoolExecutor(max_workers=min(8, len(items))) as executor:
        rows = list(executor.map(load, items))

    up = sum(1 for row in rows if (row.get("quote") or {}).get("pct") is not None and row["quote"]["pct"] > 0)
    down = sum(1 for row in rows if (row.get("quote") or {}).get("pct") is not None and row["quote"]["pct"] < 0)
    sources = {row.get("source") for row in rows if row.get("source")}
    statuses = {(row.get("quote") or {}).get("market_status") for row in rows if row.get("quote")}
    by_market: Dict[str, int] = {}
    for row in rows:
        key = str(row.get("market") or "")
        by_market[key] = by_market.get(key, 0) + 1
    return ok({
        "items": rows,
        "counts": {"total": len(rows), "up": up, "down": down, "flat": len(rows) - up - down,
                   "by_market": by_market},
        "source": "、".join(sorted(sources)) or "腾讯/新浪",
        "caliber": "自选股=关注清单（不含成本）；行情为实时快照，收盘后为最后成交快照",
        "as_of": utils.now_text(),
        "market_status": "多市场快照" if len(statuses) > 1 else (next(iter(statuses), "快照")),
        "is_realtime": any(bool((row.get("quote") or {}).get("market_status") == "盘中") for row in rows),
    })


@router.post("")
def add_watchlist(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """新增自选项（幂等）。

    Body:
        {symbol, market?, name?}

    Args:
        payload: 请求体。

    Returns:
        dict: 统一返回结构；data.item 为落盘条目，data.created 表示是否新建。
    """
    raw_symbol = str(payload.get("symbol") or "").strip()
    market = utils.normalize_market(str(payload.get("market") or "a"), extra=("fund",))
    name = str(payload.get("name") or "").strip()
    if not raw_symbol:
        return api_error(ErrorCode.INVALID_PARAM, "symbol 不可为空。")
    if market is None:
        return api_error(ErrorCode.INVALID_PARAM, "market 仅支持 a/hk/us/fund（cn 为 a 的兼容别名）。")
    if market == "fund":
        symbol = raw_symbol.upper()
    else:
        try:
            symbol, market, _ = utils.normalize_symbol(raw_symbol, market)
        except ValueError as exc:
            return api_error(ErrorCode.INVALID_PARAM, f"代码格式不正确：{exc}")
    try:
        state, item = store.add_item(symbol, market, name)
    except NonRetryableError as exc:
        return api_error(ErrorCode.INTERNAL_ERROR, str(exc))
    except ValueError as exc:
        return api_error(ErrorCode.INVALID_PARAM, str(exc))
    return ok({
        "item": item,
        "created": state == "created",
        "source": "本地自选清单",
        "caliber": f"自选上限 {store.MAX_ITEMS} 条，重复添加自动去重",
        "as_of": utils.now_text(),
        "market_status": "配置快照",
        "is_realtime": False,
    })


@router.delete("/{symbol}")
def remove_watchlist(symbol: str, market: str = Query("a", description="a/hk/us/fund")) -> Dict[str, Any]:
    """移除自选项。

    Args:
        symbol: 代码。
        market: 市场。

    Returns:
        dict: 统一返回结构；data.removed 表示是否删除了既有条目。
    """
    normalized = utils.normalize_market(market, extra=("fund",))
    if normalized is None:
        return api_error(ErrorCode.INVALID_PARAM, "market 仅支持 a/hk/us/fund（cn 为 a 的兼容别名）。")
    removed = store.remove_item(symbol, normalized)
    return ok({
        "removed": removed,
        "symbol": symbol,
        "market": normalized,
        "source": "本地自选清单",
        "caliber": "移除后不可恢复，可重新从个股页添加",
        "as_of": utils.now_text(),
        "market_status": "配置快照",
        "is_realtime": False,
    })


@router.patch("/{symbol}")
def rename_watchlist(symbol: str, payload: Dict[str, Any] = Body(...),
                     market: str = Query("a", description="a/hk/us/fund")) -> Dict[str, Any]:
    """修改自选项展示名。

    Args:
        symbol: 代码。
        payload: {"name": "新名称"}。
        market: 市场。

    Returns:
        dict: 统一返回结构；未找到条目返回 NOT_FOUND。
    """
    normalized = (market or "a").lower()
    name = str(payload.get("name") or "").strip()
    if not name:
        return api_error(ErrorCode.INVALID_PARAM, "name 不可为空。")
    item = store.rename_item(symbol, normalized, name)
    if item is None:
        return api_error(ErrorCode.NOT_FOUND, "自选中没有该标的。", data={"symbol": symbol})
    return ok({
        "item": item,
        "source": "本地自选清单",
        "caliber": "仅改展示名，不改代码与市场",
        "as_of": utils.now_text(),
        "market_status": "配置快照",
        "is_realtime": False,
    })
