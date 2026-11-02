# -*- coding: utf-8 -*-
"""腾讯板块（行业/概念）纯 HTTP 通道。

背景（第八轮，2026-11-02）
--------------------------
东财 push2/push2his 在本机被源站持续重置（L-20），同花顺概念行情页需 hexin-v 签名
（V8）。腾讯 ``proxy.finance.qq.com`` 板块排行接口与 ``web.ifzq.gtimg.cn`` 板块 K 线
接口**均为纯 HTTP、无需签名**，实测覆盖：

- 行业：``board_type=hy2`` 返回申万二级行业 124 个（含主力净流入/领涨股/换手/量比）；
- 概念：``board_type=gn`` 返回概念板块 805 个（同口径）；
- K 线：``newfqkline`` 支持板块代码 ``ptXXXXXXX``，返回 11 字段（含成交额）。

单位口径（实测校准）
--------------------
- ``turnover``/``zllr``/``zllc``/``zljlr``/``ltsz``/``zsz``：万元；
- K 线第 5 位 ``volume``：手；第 8 位 ``amount``：万元；
- 本模块统一折算为「亿元 / 股」，与板块行情表同列（前端不再做二次换算）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from backend.app import config
from backend.app.core.errors import NonRetryableError
from backend.app.datasource import utils

logger = logging.getLogger(__name__)

# 板块类型 → 腾讯 board_type（hy2=申万二级行业，gn=概念）
_BOARD_TYPE = {"industry": "hy2", "concept": "gn"}
# 周期 → 腾讯 K 线参数段
_PERIOD = {"daily": "day", "weekly": "week", "monthly": "month"}


def _get_json(url: str, params: Dict[str, Any], *, referer: str) -> Any:
    """请求腾讯接口并解析 JSON（统一异常转译）。

    Args:
        url: 完整 URL。
        params: 查询参数。
        referer: 站点 Referer。

    Returns:
        Any: 解析后的 JSON。

    Raises:
        NonRetryableError: 网络确定性失败（不重试）。
        ValueError: 响应不是合法 JSON。
    """
    import json

    from backend.app.core import netfix

    session = netfix.make_session()
    try:
        response = session.get(
            url,
            params=params,
            headers={"User-Agent": config.BROWSER_USER_AGENT, "Referer": referer},
            timeout=config.DATASOURCE_TIMEOUT,
        )
        response.raise_for_status()
        return json.loads(response.text)
    except Exception as exc:  # noqa: BLE001 - 统一转译为可降级异常
        name = type(exc).__name__
        if name in ("ProxyError", "SSLError", "ConnectionError", "RemoteDisconnected"):
            raise NonRetryableError(f"腾讯板块通道不可用：{name}") from exc
        raise
    finally:
        session.close()


def _wan_to_yi(value: Any) -> Optional[float]:
    """万元 → 亿元（保留两位）。

    Args:
        value: 万元数值（字符串或数字）。

    Returns:
        Optional[float]: 亿元数值；不可解析返回 None。
    """
    number = utils.to_float(value)
    if number is None:
        return None
    return round(number / 1e4, 4)


def get_board_rows(kind: str = "industry", *, max_pages: int = 12) -> List[Dict[str, Any]]:
    """取腾讯板块行情全表（行业 124 / 概念 805）。

    每页 100 条，翻页直到取满 ``total``；不确定的字段（涨跌家数）不猜，留 None。

    Args:
        kind: industry=申万二级行业 / concept=概念。
        max_pages: 最多翻页数（防异常导致无限循环）。

    Returns:
        List[Dict[str, Any]]: [{name, code, pct, price, change, amount, turnover,
        volume_ratio, net_inflow, main_in, main_out, float_cap, total_cap,
        lead_stock, lead_pct, pct_5d, pct_20d, pct_60d, kind}]

    Raises:
        ValueError: 首页即无数据。
    """
    board_type = _BOARD_TYPE.get(kind, "hy2")
    items: List[Dict[str, Any]] = []
    total = 0
    for page in range(max_pages):
        payload = _get_json(
            config.TENCENT_BOARD_URL,
            {
                "board_type": board_type,
                "sort_type": "price",
                "direct": "down",
                "offset": page * 100,
                "count": 100,
            },
            referer="https://gu.qq.com/",
        )
        data = payload.get("data") or {}
        rank_list = data.get("rank_list") or []
        if not rank_list:
            break
        total = int(data.get("total") or 0)
        for row in rank_list:
            lead = row.get("lzg") or {}
            items.append(
                {
                    "name": str(row.get("name") or "").strip(),
                    "code": str(row.get("code") or "").strip(),
                    "price": utils.to_float(row.get("zxj")),
                    "change": utils.to_float(row.get("zd")),
                    "pct": utils.to_float(row.get("zdf")),
                    "amount": _wan_to_yi(row.get("turnover")),
                    "volume": utils.to_float(row.get("volume")),
                    "turnover_rate": utils.to_float(row.get("hsl")),
                    "volume_ratio": utils.to_float(row.get("lb")),
                    "net_inflow": _wan_to_yi(row.get("zljlr")),
                    "main_in": _wan_to_yi(row.get("zllr")),
                    "main_out": _wan_to_yi(row.get("zllc")),
                    "float_cap": _wan_to_yi(row.get("ltsz")),
                    "total_cap": _wan_to_yi(row.get("zsz")),
                    "lead_stock": lead.get("name"),
                    "lead_code": str(lead.get("code") or "")[-6:] or None,
                    "lead_pct": utils.to_float(lead.get("zdf")),
                    "pct_5d": utils.to_float(row.get("zdf_d5")),
                    "pct_20d": utils.to_float(row.get("zdf_d20")),
                    "pct_60d": utils.to_float(row.get("zdf_d60")),
                    "kind": "行业" if kind == "industry" else "概念",
                }
            )
        if len(items) >= total or len(rank_list) < 100:
            break
    items = [row for row in items if row["name"]]
    if not items:
        raise ValueError(f"腾讯板块行情返回空数据（board_type={board_type}）")
    return items


def get_board_kline(
    code: str,
    *,
    period: str = "daily",
    limit: Optional[int] = None,
    adjust: str = "qfq",
) -> List[Dict[str, Any]]:
    """取腾讯板块指数 K 线（含成交额，纯 HTTP）。

    响应字段序（实测）：``[日期, 开, 收, 高, 低, 成交量(手), {}, 换手率, 成交额(万元), ...]``。
    注意与常见 OHLC 顺序不同：**第 2 位是收盘、第 3 位是最高**。

    Args:
        code: 腾讯板块代码（ptXXXXXXX）。
        period: daily / weekly / monthly。
        limit: 仅返回最近 N 根；None 表示请求源站默认上限（320 根）。
        adjust: qfq / hfq / 空（不复权）。

    Returns:
        List[Dict[str, Any]]: [{date, open, high, low, close, volume, amount, turnover_rate, pct}]

    Raises:
        ValueError: 响应无 K 线数据。
    """
    period_key = _PERIOD.get(str(period or "daily").strip().lower(), "day")
    count = int(limit) if limit else 320
    adjust_text = str(adjust or "").strip().lower()
    param = f"{code},{period_key},,,{count}"
    if adjust_text in ("qfq", "hfq"):
        param = f"{param},{adjust_text}"
    payload = _get_json(
        "https://web.ifzq.gtimg.cn/appstock/app/newfqkline/get",
        {"param": param},
        referer="https://gu.qq.com/",
    )
    node = (payload.get("data") or {}).get(code) or {}
    series = node.get("qfq" + period_key) or node.get("hfq" + period_key) or node.get(period_key) or []
    items: List[Dict[str, Any]] = []
    previous_close: Optional[float] = None
    for row in series:
        if not isinstance(row, list) or len(row) < 6:
            continue
        date_text = str(row[0])[:10]
        open_p = utils.to_float(row[1])
        close_p = utils.to_float(row[2])
        high_p = utils.to_float(row[3])
        low_p = utils.to_float(row[4])
        volume = utils.to_float(row[5])
        turnover_rate = utils.to_float(row[7]) if len(row) > 7 else None
        amount_wan = utils.to_float(row[8]) if len(row) > 8 else None
        pct = None
        if previous_close and close_p is not None and previous_close > 0:
            pct = round((close_p / previous_close - 1) * 100, 4)
        items.append(
            {
                "date": date_text,
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
                "volume": volume,
                # 万元 → 亿元，与同花顺板块行情同列
                "amount": round(amount_wan / 1e4, 4) if amount_wan is not None else None,
                "turnover_rate": turnover_rate,
                "pct": pct,
            }
        )
        if close_p is not None:
            previous_close = close_p
    if not items:
        raise ValueError(f"腾讯板块 K 线无有效行（code={code}）")
    return items


def get_board_quote(code: str) -> Dict[str, Any]:
    """取板块实时快照（复用 K 线响应内的 ``qt`` 节点，不额外发请求）。

    Args:
        code: 腾讯板块代码。

    Returns:
        Dict[str, Any]: {name, price, prev_close, open, pct, change, amount, as_of}

    Raises:
        ValueError: 响应缺少行情节点。
    """
    payload = _get_json(
        "https://web.ifzq.gtimg.cn/appstock/app/newfqkline/get",
        {"param": f"{code},day,,,1"},
        referer="https://gu.qq.com/",
    )
    node = (payload.get("data") or {}).get(code) or {}
    quote = (node.get("qt") or {}).get(code) or []
    if not quote:
        raise ValueError(f"腾讯板块快照缺 qt 节点（code={code}）")
    amount_wan = utils.to_float(quote[37]) if len(quote) > 37 else None
    return {
        "name": str(quote[1]) if len(quote) > 1 else None,
        "price": utils.to_float(quote[3]) if len(quote) > 3 else None,
        "prev_close": utils.to_float(quote[4]) if len(quote) > 4 else None,
        "open": utils.to_float(quote[5]) if len(quote) > 5 else None,
        "pct": utils.to_float(quote[32]) if len(quote) > 32 else None,
        "change": utils.to_float(quote[31]) if len(quote) > 31 else None,
        "amount": round(amount_wan / 1e4, 4) if amount_wan is not None else None,
        "as_of": utils.now_text(utils.bj_now()),
    }


__all__ = ["get_board_rows", "get_board_kline", "get_board_quote"]
