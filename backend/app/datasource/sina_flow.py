# -*- coding: utf-8 -*-
"""新浪资金流通道（板块级净额 + 个股级四档，纯 HTTP）。

背景（第八轮，2026-11-02）
--------------------------
板块资金流原先只有同花顺单源，且**不提供超大单/大单分项**（降级点 G-1）。
东财 push2 的 ``f62/f66/f72/f78/f84`` 是全市场四档口径，但该域在本机被源站持续重置
（L-20），冻结形态不可用。新浪财经的两组公开接口可补齐：

1. ``MoneyFlow.ssl_bkzj_bk``：板块资金流（fenlei=0 新浪行业 / 1 概念 / 2 证监会行业），
   返回 ``inamount`` / ``outamount`` / ``netamount``（单位=元）；
2. ``MoneyFlow.ssi_ssfx_flzjtj``：**个股四档**资金流，``r0``=超大单、``r1``=大单、
   ``r2``=中单、``r3``=小单，各含 ``_in`` / ``_out``（单位=元）；
   支持 ``daima=a,b,c`` 批量（实测 500 只/次），故可按板块成分聚合出板块四档。

口径注意
--------
- 批量接口**不保证返回顺序**（按源站内部顺序返回，且无效代码被丢弃），因此
  **只能做求和聚合，不可按位置映射**；单只标的的四档仍用单代码请求；
- 四档净额 = ``_in - _out``；四档合计 ≈ ``netamount``（实测 600519 误差 < 1 元）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

from backend.app import config
from backend.app.core.errors import NonRetryableError
from backend.app.datasource import utils

logger = logging.getLogger(__name__)

# 板块分类：0=新浪行业 1=概念 2=证监会行业
_FENLEI = {"industry": 0, "concept": 1, "csrc": 2}
# 单次批量请求的最大代码数（实测 500 可用，留余量取 200 降低单请求体积）
_BATCH_SIZE = 200


def _get_api(url: str, params: Dict[str, Any]) -> Any:
    """请求新浪 JSON 接口（统一异常转译）。

    Args:
        url: 完整 URL。
        params: 查询参数。

    Returns:
        Any: 解析后的 JSON（可能是 list / dict）。

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
            headers={
                "User-Agent": config.BROWSER_USER_AGENT,
                "Referer": "http://vip.stock.finance.sina.com.cn/moneyflow/",
            },
            timeout=config.DATASOURCE_TIMEOUT,
        )
        response.raise_for_status()
        text = response.text.strip()
        # 新浪偶发返回 /*<script>...*/ 前缀与 undefined，先剥离再解析
        if text.startswith("/*"):
            text = text[text.find("*/") + 2:].strip()
        if not text or text in ("null", "undefined"):
            return []
        return json.loads(text)
    except Exception as exc:  # noqa: BLE001 - 统一转译为可降级异常
        name = type(exc).__name__
        if name in ("ProxyError", "SSLError", "ConnectionError", "RemoteDisconnected"):
            raise NonRetryableError(f"新浪资金流通道不可用：{name}") from exc
        raise
    finally:
        session.close()


def get_board_flow(kind: str = "industry", *, limit: int = 100) -> List[Dict[str, Any]]:
    """取新浪板块资金流（净额/流入/流出，单位=亿元）。

    Args:
        kind: industry=新浪行业 / concept=概念 / csrc=证监会行业。
        limit: 最多返回条数。

    Returns:
        List[Dict[str, Any]]: [{name, code, pct, inflow, outflow, net_inflow, lead_stock, lead_pct}]

    Raises:
        ValueError: 返回空数据。
    """
    fenlei = _FENLEI.get(str(kind or "industry").strip().lower(), 0)
    payload = _get_api(
        config.SINA_BOARD_FLOW_URL,
        {
            "page": 1,
            "num": max(20, min(int(limit or 100), 200)),
            "sort": "netamount",
            "asc": 0,
            "fenlei": fenlei,
        },
    )
    items: List[Dict[str, Any]] = []
    for row in payload if isinstance(payload, list) else []:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        inflow = utils.to_float(row.get("inamount"))
        outflow = utils.to_float(row.get("outamount"))
        net = utils.to_float(row.get("netamount"))
        # 涨幅字段为小数（0.0384 = 3.84%），统一换算为百分数
        pct = utils.to_float(row.get("avg_changeratio"))
        items.append(
            {
                "name": name,
                "code": row.get("category"),
                "pct": round(pct * 100, 4) if pct is not None else None,
                "inflow": round(inflow / 1e8, 4) if inflow is not None else None,
                "outflow": round(outflow / 1e8, 4) if outflow is not None else None,
                "net_inflow": round(net / 1e8, 4) if net is not None else None,
                "lead_stock": row.get("ts_name"),
                "lead_code": str(row.get("ts_symbol") or "")[-6:] or None,
            }
        )
    if not items:
        raise ValueError("新浪板块资金流返回空数据")
    return items


def _to_sina_symbol(code: str) -> str:
    """把 6 位代码转成新浪带市场前缀的代码。

    Args:
        code: 6 位证券代码。

    Returns:
        str: sh600519 / sz000001 / bj430047 形式；无法判定时原样返回。
    """
    text = str(code or "").strip()
    if len(text) != 6 or not text.isdigit():
        return text
    if text[0] in ("6", "9"):
        return "sh" + text
    if text[0] in ("0", "3", "2"):
        return "sz" + text
    if text[0] in ("4", "8"):
        return "bj" + text
    return "sh" + text


def get_stock_flow(code: str) -> Optional[Dict[str, Any]]:
    """取单只标的的四档资金流（单位=亿元）。

    Args:
        code: 6 位代码（也接受 sh600519 形式）。

    Returns:
        Optional[Dict[str, Any]]: {super_big, big, medium, small, net_inflow,
        inflow, outflow, name}；不可得返回 None。

    Raises:
        NonRetryableError: 通道异常（交由上层降级链）。
    """
    symbol = code if str(code).startswith(("sh", "sz", "bj")) else _to_sina_symbol(code)
    payload = _get_api(config.SINA_STOCK_FLOW_URL, {"daima": symbol})
    rows = payload if isinstance(payload, list) else [payload]
    if not rows or not isinstance(rows[0], dict) or not rows[0]:
        return None
    row = rows[0]
    tiers = {}
    for tier, key in (("super_big", "r0"), ("big", "r1"), ("medium", "r2"), ("small", "r3")):
        in_amt = utils.to_float(row.get(key + "_in"))
        out_amt = utils.to_float(row.get(key + "_out"))
        if in_amt is None and out_amt is None:
            tiers[tier] = None
        else:
            tiers[tier] = round(((in_amt or 0) - (out_amt or 0)) / 1e8, 4)
    inflow = sum(
        utils.to_float(row.get(k + "_in")) or 0.0 for k in ("r0", "r1", "r2", "r3")
    )
    outflow = sum(
        utils.to_float(row.get(k + "_out")) or 0.0 for k in ("r0", "r1", "r2", "r3")
    )
    return {
        "name": row.get("name"),
        "price": utils.to_float(row.get("trade")),
        "pct": round((utils.to_float(row.get("changeratio")) or 0) * 100, 4),
        **tiers,
        "inflow": round(inflow / 1e8, 4),
        "outflow": round(outflow / 1e8, 4),
        "net_inflow": round((inflow - outflow) / 1e8, 4),
    }


def aggregate_flow(codes: Iterable[str]) -> Dict[str, Any]:
    """按一组标的聚合四档资金流（单位=亿元）。

    批量接口不保证顺序、且会丢弃无效代码，故本函数**只做求和**，不做逐只映射。

    Args:
        codes: 6 位代码序列（板块成分股）。

    Returns:
        Dict[str, Any]: {super_big, big, medium, small, inflow, outflow,
        net_inflow, covered}；无有效数据时 returncode 字段为 0。

    Raises:
        NonRetryableError: 首个批次即通道异常（交由上层降级链）。
    """
    symbols = [_to_sina_symbol(code) for code in codes if str(code or "").strip()]
    symbols = [s for s in symbols if len(s) == 8]
    totals = {"r0": [0.0, 0.0], "r1": [0.0, 0.0], "r2": [0.0, 0.0], "r3": [0.0, 0.0]}
    covered = 0
    for start in range(0, len(symbols), _BATCH_SIZE):
        chunk = symbols[start:start + _BATCH_SIZE]
        payload = _get_api(config.SINA_STOCK_FLOW_URL, {"daima": ",".join(chunk)})
        rows = payload if isinstance(payload, list) else [payload]
        for row in rows:
            if not isinstance(row, dict) or not row:
                continue
            covered += 1
            for key, bucket in totals.items():
                bucket[0] += utils.to_float(row.get(key + "_in")) or 0.0
                bucket[1] += utils.to_float(row.get(key + "_out")) or 0.0
    if covered == 0:
        return {
            "super_big": None,
            "big": None,
            "medium": None,
            "small": None,
            "inflow": None,
            "outflow": None,
            "net_inflow": None,
            "covered": 0,
        }
    inflow = sum(bucket[0] for bucket in totals.values())
    outflow = sum(bucket[1] for bucket in totals.values())
    return {
        "super_big": round((totals["r0"][0] - totals["r0"][1]) / 1e8, 4),
        "big": round((totals["r1"][0] - totals["r1"][1]) / 1e8, 4),
        "medium": round((totals["r2"][0] - totals["r2"][1]) / 1e8, 4),
        "small": round((totals["r3"][0] - totals["r3"][1]) / 1e8, 4),
        "inflow": round(inflow / 1e8, 4),
        "outflow": round(outflow / 1e8, 4),
        "net_inflow": round((inflow - outflow) / 1e8, 4),
        "covered": covered,
    }


def get_node_constituents(node: str, *, max_pages: int = 6) -> List[Dict[str, Any]]:
    """取新浪板块节点成分股（供概念板块四档聚合使用）。

    Args:
        node: 新浪板块节点代码（如 ``new_blhy`` / ``gn_rzrq``）。
        max_pages: 最多翻页数（每页 100 只）。

    Returns:
        List[Dict[str, Any]]: [{code, name, price, pct, amount}]

    Raises:
        ValueError: 首页即无数据。
    """
    items: List[Dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        payload = _get_api(
            "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "Market_Center.getHQNodeData",
            {"page": page, "num": 100, "sort": "symbol", "asc": 1, "node": node},
        )
        rows = payload if isinstance(payload, list) else []
        if not rows:
            break
        for row in rows:
            code = str(row.get("code") or "").strip()
            if len(code) != 6 or not code.isdigit():
                continue
            items.append(
                {
                    "code": code,
                    "name": row.get("name"),
                    "price": utils.to_float(row.get("trade")),
                    "pct": utils.to_float(row.get("changepercent")),
                    "amount": round((utils.to_float(row.get("amount")) or 0) / 1e8, 4),
                }
            )
        if len(rows) < 100:
            break
    if not items:
        raise ValueError(f"新浪板块节点成分返回空数据（node={node}）")
    return items


__all__ = [
    "get_board_flow",
    "get_stock_flow",
    "aggregate_flow",
    "get_node_constituents",
]
