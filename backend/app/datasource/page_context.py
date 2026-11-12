# -*- coding: utf-8 -*-
"""Copilot 页面上下文摘要（第十轮 P3 · R16 / C16）。

设计基准 §8 R16：「Copilot 不读页面上下文 → P3 起注入当前页数据摘要
（页面 ID+关键数值）到 system prompt」。

口径与约束
----------
- **数据与页面同源**：只读复用各域路由的缓存键（页面刚渲染即热，读到的就是
  用户所见的那份数据）；缓存未命中时退对应 provider 主源即时取数。全程
  只读不写缓存（不污染路由的 with_fallback 结果形状）。
- **零降级**（裁决 J1）：任何页面没有构造器、或取数失败 → 返回 None，
  chat 按无上下文原样工作（=R16 之前的行为，不报错不编造）。
- **只喂事实**：摘要只含板块名/代码/涨幅/成交额等页面真实数值，无推测；
  system 文案要求模型「引用上述数值、不得编造」。
- C16 断言面：云图页（sectors）问「谁涨了」，答案能含页内板块名+涨幅数字。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from backend.app import config
from backend.app.core import cache
from backend.app.datasource import utils

logger = logging.getLogger(__name__)

_MAX_LINES = 5  # 每个榜单最多列出条数（控制 system 长度）

# 只读探测用 TTL：路由写入用 1h~24h（日频），这里放宽到 24h 只影响
# 「读不到则退主源」的概率，不会读到超过一天的旧数据（内存缓存在重启即清）
_PROBE_TTL = config.TTL["page_probe"]


def _fmt_pct(value: Any) -> str:
    """涨幅百分比文本（None→—）。

    Args:
        value: 原始值。

    Returns:
        str: 形如「+1.23%」。
    """
    try:
        return f"{float(value):+.2f}%"
    except (TypeError, ValueError):
        return "—"


def _row_line(item: Dict[str, Any], *, with_amount: bool = False) -> str:
    """把行情行压成一行文本。

    Args:
        item: {name/pct/amount/...}。
        with_amount: 是否附成交额（亿元）。

    Returns:
        str: 形如「半导体 +2.10%（额 512.3 亿）」。
    """
    name = str(item.get("name") or item.get("board") or "?")
    pct = _fmt_pct(item.get("pct"))
    if with_amount and item.get("amount") is not None:
        return f"{name} {pct}（额 {item.get('amount')} 亿）"
    return f"{name} {pct}"


def _industry_summary() -> Optional[str]:
    """云图/板块页摘要：行业+概念行情（与页面 heatmap/rank 同源）。

    Returns:
        Optional[str]: 摘要文本；两源皆不可得返回 None。
    """
    rows: Optional[List[Dict[str, Any]]] = None
    cached = cache.get("sector:industry:cn", _PROBE_TTL)
    if isinstance(cached, dict) and isinstance(cached.get("data"), list) and cached["data"]:
        rows = cached["data"]
    else:
        from backend.app.datasource import sector as sector_provider

        try:
            direct = sector_provider.get_industry("cn")
            rows = direct if isinstance(direct, list) and direct else None
        except Exception:  # noqa: BLE001 - 主源失败=无上下文，不报错
            rows = None
    if not rows:
        return None

    priced = [r for r in rows if isinstance(r, dict) and r.get("pct") is not None]
    if not priced:
        return None
    by_pct = sorted(priced, key=lambda r: float(r["pct"]), reverse=True)
    gainers = [_row_line(r, with_amount=True) for r in by_pct[:_MAX_LINES]]
    losers = [_row_line(r) for r in by_pct[-_MAX_LINES:][::-1]]
    lines = [
        "【当前页面上下文】用户正停留在「板块热力图」页（行业板块行情，与页面同一数据源）。",
        "涨幅前" + str(len(gainers)) + "：" + "；".join(gainers),
        "跌幅前" + str(len(losers)) + "：" + "；".join(losers),
    ]
    with_amount = [r for r in priced if r.get("amount") is not None]
    if with_amount:
        by_amount = sorted(with_amount, key=lambda r: float(r["amount"]), reverse=True)[:_MAX_LINES]
        lines.append(
            "成交额前" + str(len(by_amount)) + "：" + "；".join(
                f"{r.get('name')}（{r.get('amount')} 亿）" for r in by_amount
            )
        )
    # 概念榜（腾讯 805）同样只读缓存→主源兜底；取不到静默省略
    concept_rows: Optional[List[Dict[str, Any]]] = None
    cached_c = cache.get("sector:concept:cn", _PROBE_TTL)
    if isinstance(cached_c, dict) and isinstance(cached_c.get("data"), list) and cached_c["data"]:
        concept_rows = cached_c["data"]
    else:
        from backend.app.datasource import sector as sector_provider

        try:
            direct_c = sector_provider.get_concept("cn")
            concept_rows = direct_c if isinstance(direct_c, list) and direct_c else None
        except Exception:  # noqa: BLE001 - 概念为辅，不阻断
            concept_rows = None
    if concept_rows:
        cp = [r for r in concept_rows if isinstance(r, dict) and r.get("pct") is not None]
        cp.sort(key=lambda r: float(r["pct"]), reverse=True)
        if cp:
            lines.append("概念涨幅前" + str(_MAX_LINES) + "：" + "；".join(
                _row_line(r) for r in cp[:_MAX_LINES]
            ))
    lines.append("回答该页问题必须引用上述真实数值（北京时间 " + utils.now_text(utils.bj_now())
                 + " 快照），不得编造或另取他源；页面只含涨跌幅与成交额，无主力净流入数值时不得臆断。")
    return "\n".join(lines)


def _stock_summary(params: Dict[str, Any]) -> Optional[str]:
    """个股页摘要：当前标的报价（与页面行情同源）。

    Args:
        params: hash 参数（symbol）。

    Returns:
        Optional[str]: 摘要文本；不可得返回 None。
    """
    symbol = str(params.get("symbol") or "").strip()
    market = str(params.get("market") or "a").strip().lower() or "a"
    if not symbol:
        return None
    from backend.app.datasource import tencent

    try:
        quote = tencent.get_quote(symbol, "a" if market not in ("hk", "us") else market)
    except Exception:  # noqa: BLE001 - 报价失败=无上下文
        return None
    name = quote.get("name") or symbol
    parts = [f"{name}({symbol}) 最新 {quote.get('price')}，涨跌 {_fmt_pct(quote.get('pct'))}"]
    for label, key in (("高", "high"), ("低", "low"), ("开", "open"), ("昨收", "prev_close")):
        if quote.get(key) is not None:
            parts.append(f"{label} {quote.get(key)}")
    if quote.get("turnover") is not None:
        parts.append(f"换手 {quote.get('turnover')}%")
    return ("【当前页面上下文】用户正停留在「个股行情」页，标的 " + "，".join(parts)
            + f"（北京时间 {utils.now_text(utils.bj_now())} 快照）。回答必须引用上述真实数值，不得编造。")


def _sector_val_summary(params: Dict[str, Any]) -> Optional[str]:
    """板块详情页摘要：板块近 5 日行情（与页面走势同源）。

    Args:
        params: hash 参数（board）。

    Returns:
        Optional[str]: 摘要文本；不可得返回 None。
    """
    board = str(params.get("board") or "").strip()
    if not board:
        return None
    from backend.app.datasource import sector as sector_provider

    try:
        data = sector_provider.get_sector_kline(board, "daily")
    except Exception:  # noqa: BLE001 - K 线失败=无上下文
        return None
    items = data.get("items") if isinstance(data, dict) else None
    tail = items[-5:] if isinstance(items, list) and items else []
    if not tail:
        return None
    seq = "；".join(
        f"{str(r.get('date'))[-5:]} {_fmt_pct(r.get('pct'))}"
        for r in tail if isinstance(r, dict)
    )
    last = tail[-1]
    return ("【当前页面上下文】用户正停留在「板块详情」页，板块=" + board
            + f"，最新收盘 {last.get('close')}，近 5 日涨跌：{seq}"
            + f"（数据与页面同源，北京时间 {utils.now_text(utils.bj_now())}）。"
              "回答必须引用上述真实数值，不得编造。")


def _market_summary() -> Optional[str]:
    """市场脉搏页摘要：主要指数行情（与页面同源）。

    Returns:
        Optional[str]: 摘要文本；不可得返回 None。
    """
    from backend.app.datasource import tencent

    try:
        rows = tencent.get_index_quotes()
    except Exception:  # noqa: BLE001 - 指数失败=无上下文
        return None
    if not isinstance(rows, list) or not rows:
        return None
    lines = []
    for r in rows[:8]:
        if isinstance(r, dict):
            lines.append(f"{r.get('name')} {r.get('price')} {_fmt_pct(r.get('pct'))}")
    if not lines:
        return None
    return ("【当前页面上下文】用户正停留在「市场总览」页，指数行情：" + "；".join(lines)
            + f"（北京时间 {utils.now_text(utils.bj_now())} 快照）。回答必须引用上述真实数值，不得编造。")


def _news_summary() -> Optional[str]:
    """新闻页摘要：快讯最新标题（与页面同源）。

    Returns:
        Optional[str]: 摘要文本；不可得返回 None。
    """
    from backend.app.datasource import news as news_provider

    titles: List[str] = []
    cached = cache.get("news:flash", _PROBE_TTL)
    if isinstance(cached, dict) and isinstance(cached.get("data"), list):
        titles = [str(r.get("title") or "") for r in cached["data"][:10] if isinstance(r, dict) and r.get("title")]
    elif isinstance(cached, list):
        titles = [str(r.get("title") or "") for r in cached[:10] if isinstance(r, dict) and r.get("title")]
    if not titles:
        try:
            flash = news_provider.get_flash(20)
            titles = [str(r.get("title") or "") for r in (flash or [])[:10] if isinstance(r, dict) and r.get("title")]
        except Exception:  # noqa: BLE001 - 快讯失败=无上下文
            return None
    if not titles:
        return None
    return ("【当前页面上下文】用户正停留在「新闻快讯」页，最新标题：" + "；".join(titles)
            + f"（北京时间 {utils.now_text(utils.bj_now())}）。回答该页问题请以上述真实标题为准，"
              "标题之外的事实不得编造。")


def _crowding_summary() -> Optional[str]:
    """拥挤度页摘要：行业排行前 10（只读页面缓存，未预热则不注入）。

    Returns:
        Optional[str]: 摘要文本；不可得返回 None。
    """
    for key in ("sector:crowding:rankings:industry:30", "sector:crowding:rankings:industry:50",
                "sector:crowding:rankings:industry:20"):
        cached = cache.get(key, _PROBE_TTL)
        if isinstance(cached, dict) and isinstance(cached.get("items"), list) and cached["items"]:
            top = [
                f"{r.get('board')} {r.get('score')}" for r in cached["items"][:10]
                if isinstance(r, dict) and r.get("board") and r.get("score") is not None
            ]
            if not top:
                continue
            return ("【当前页面上下文】用户正停留在「板块拥挤度排行」页（六维加权 0–100，"
                    "越热越高）。拥挤度前 10：" + "；".join(top)
                    + "（排行生成于页面最近一次扫描）。回答必须引用上述真实数值，不得编造。")
    return None


_BUILDERS = {
    "sectors": lambda p: _industry_summary(),
    "stock": _stock_summary,
    "sector-val": _sector_val_summary,
    "market": lambda p: _market_summary(),
    "news": lambda p: _news_summary(),
    "crowding": lambda p: _crowding_summary(),
}


def build_summary(page: str, params: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """按页面 ID 构造 system prompt 摘要（R16）。

    Args:
        page: 前端 hash 视图名（sectors/stock/sector-val/market/news/crowding）。
        params: hash 查询参数（board/symbol 等）。

    Returns:
        Optional[str]: system 摘要文本；页面未支持或数据不可得返回 None
        （调用方据此不注入，chat 行为与 R16 之前完全一致——零降级）。
    """
    key = str(page or "").strip().lower()
    builder = _BUILDERS.get(key)
    if builder is None:
        return None
    try:
        text = builder(dict(params or {}))
    except Exception as exc:  # noqa: BLE001 - 上下文构建失败绝不影响对话主链路
        logger.info("页面上下文摘要构建失败 %s：%s", key, type(exc).__name__)
        return None
    if not text:
        return None
    return str(text)[:3500]  # system 上限 3500 字，防 prompt 过长
