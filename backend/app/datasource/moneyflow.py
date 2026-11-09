# -*- coding: utf-8 -*-
"""龙虎榜 · 北向资金数据源（L 区块 · P1）。

设计基准：`新增功能设计基准_20261022.md` L-01/L-02/L-03。
数据源（东财 datacenter，纯 HTTP，规避本机 push2/V8 限制）：
- 北向历史：``ak.stock_hsgt_hist_em(symbol="北向资金")``（日期/当日成交净买额/买入额/卖出额，单位亿元）；
- 北向个股：``ak.stock_hsgt_hold_stock_em(market="北向", indicator="今日排行")``；
- 龙虎榜：``ak.stock_lhb_detail_em(start_date, end_date)``（金额单位元）。

**实测口径（2026-10-24）**：交易所自 2024-08-16 起停止披露北向每日成交净买额，
此后该列全为 NaN，个股每日净买入排行接口亦不可用。因此：
- L-01 取「最后 20 个有披露的交易日」，并在数据里带 ``discontinued`` 标记与中文说明，
  由前端在区块内如实提示，**不补零、不造数**；
- L-02 无当日个股净买入数据时返回空列表 + 中文说明（空/错分离），前端渲染空态。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import akshare as ak

from backend.app.core.resilient import polite_sleep
from backend.app.datasource import utils

logger = logging.getLogger(__name__)

NORTH_STOP_NOTE = "交易所自 2024-08-16 起停止披露北向资金每日成交净买额，图示为最后可得区间"
NORTH_TOP_NOTE = "北向个股每日净买入自 2024-08-19 起改为季度披露，暂无当日 Top10 数据"
LHB_EMPTY_NOTE = "当日无龙虎榜披露（周末/盘前可能尚未更新）"
# 龙虎榜区间回溯天数：东财按自然日区间查询，7 天可覆盖长假前后
LHB_RANGE_DAYS = 7


# P1 收敛：_num 统一委托 utils.to_float（NaN/±Inf 已在 utils 内过滤）。
_num = utils.to_float


def _yi(value: Any, digits: int = 2) -> Optional[float]:
    """把「元」为单位的金额统一换算为「亿元」。

    东财龙虎榜/北向接口的金额列一律以**元**返回（实测 2026-10-24：机构买入净额
    346765482.6 元 = 3.47 亿元），因此这里**始终**除以 1e8，不再按量级猜单位
    （旧实现 `abs(v)>1e6` 才除亿，导致小值返回原始元却标「亿元」自相矛盾，R3-08）。

    精度自适应：换算后绝对值 < 0.01 亿元（即不足 100 万元）时自动提高小数位，
    避免「500 元 → 0.0 亿元」这种被误读成补零的显示；单位始终是亿元。

    Args:
        value: 原始金额（元），可为空。
        digits: 保留小数位（小额场景会自动上调）。

    Returns:
        Optional[float]: 亿元金额；入参为空返回 None（不补 0）。
    """
    number = _num(value)
    if number is None:
        return None
    result = number / 1e8
    if result and abs(result) < 0.01:
        # 小额（不足 100 万元）：保留 4 位小数，避免 0.005 亿被压成 0.0；
        # 仍低于 1 万元时如实显示 0.0（不虚构精度，也不换单位）
        digits = max(digits, 4)
    return round(result, digits)


def _date_text(value: Any) -> str:
    """把日期列统一成 YYYY-MM-DD 文本（兼容 datetime.date / 字符串 / 时间戳）。

    Args:
        value: 原始日期值。

    Returns:
        str: 归一化日期文本；无法识别时返回空串。
    """
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else text


def get_northbound(days: int = 20) -> Dict[str, Any]:
    """北向资金近 N 个「有披露」交易日净流入（亿元）。

    Args:
        days: 需要的交易日数量（默认 20）。

    Returns:
        dict: {items:[{date,net,buy,sell}], latest_disclosed, discontinued, note, source, caliber}。

    Raises:
        ValueError: 上游返回空表或列缺失。
    """
    frame = ak.stock_hsgt_hist_em(symbol="北向资金")
    rows = utils.clean_records(frame)
    if not rows:
        raise ValueError("北向资金历史返回空表")
    picked: List[Dict[str, Any]] = []
    for row in rows:
        net = _num(row.get("当日成交净买额"))
        if net is None:
            continue
        picked.append({
            "date": str(row.get("日期") or "")[:10],
            "net": round(net, 2),
            "buy": _num(row.get("买入成交额")),
            "sell": _num(row.get("卖出成交额")),
        })
    if not picked:
        raise ValueError("北向资金历史无有效净买额")
    latest = picked[-1]["date"]
    stale = (utils.today_bj() - date.fromisoformat(latest)).days > 30  # TZ-06
    return {
        "items": picked[-max(1, days):],
        "latest_disclosed": latest,
        "discontinued": stale,
        "note": NORTH_STOP_NOTE if stale else "",
        "source": "东财 datacenter / stock_hsgt_hist_em",
        "caliber": "北向资金每日成交净买额（亿元，收盘口径）",
    }


def get_north_top(limit: int = 10) -> Dict[str, Any]:
    """北向个股净买入 TopN（无当日数据时返回空列表 + 说明）。

    Args:
        limit: 返回条数上限。

    Returns:
        dict: {items:[{code,name,net,market_value}], note, source, caliber}。
    """
    polite_sleep()
    try:
        frame = ak.stock_hsgt_hold_stock_em(market="北向", indicator="今日排行")
        rows = utils.clean_records(frame)
    except Exception as exc:  # noqa: BLE001 - 上游改版/停更均按不可用处理
        logger.info("北向个股排行不可用：%s（%s）", type(exc).__name__, str(exc)[:80])
        rows = []
    items: List[Dict[str, Any]] = []
    for row in rows:
        code = str(row.get("代码") or row.get("股票代码") or "")
        net = _num(row.get("今日净买额") or row.get("净买额") or row.get("今日增持估计-市值"))
        if not code:
            continue
        items.append({
            "code": code.split(".")[0],
            "name": row.get("名称") or row.get("股票简称"),
            # 源列单位为「元」，统一换算为亿元（口径与 caliber 一致）
            "net": _yi(net),
            "market_value": _num(row.get("今日持股市值") or row.get("持股市值")),
        })
    items = [item for item in items if item["net"] is not None]
    items.sort(key=lambda item: item["net"], reverse=True)
    return {
        "items": items[: max(1, limit)],
        "note": "" if items else NORTH_TOP_NOTE,
        "source": "东财 datacenter / stock_hsgt_hold_stock_em",
        "caliber": "北向持股个股当日净买入排行（亿元）",
    }


def get_lhb(day: str = "", limit: int = 100) -> Dict[str, Any]:
    """龙虎榜明细（默认取最近一个披露日）。

    Args:
        day: 指定日期 YYYY-MM-DD；留空取区间内最新披露日。
        limit: 返回条数上限。

    Returns:
        dict: {date, items:[{code,name,reason,pct,net_buy,buy,sell,turnover}], total, note, source, caliber}。

    Raises:
        ValueError: 上游返回空表。
    """
    end = date.fromisoformat(day) if day else utils.today_bj()  # TZ-06
    start = end - timedelta(days=LHB_RANGE_DAYS)
    polite_sleep()
    frame = ak.stock_lhb_detail_em(
        start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d")
    )
    rows = utils.clean_records(frame)
    if not rows:
        raise ValueError(LHB_EMPTY_NOTE)
    dates = sorted({str(row.get("上榜日") or "")[:10] for row in rows if row.get("上榜日")})
    target = day or (dates[-1] if dates else "")
    items: List[Dict[str, Any]] = []
    for row in rows:
        if str(row.get("上榜日") or "")[:10] != target:
            continue
        items.append({
            "date": target,
            "code": str(row.get("代码") or "").split(".")[0],
            "name": row.get("名称"),
            "reason": row.get("上榜原因"),
            "close": _num(row.get("收盘价")),
            "pct": _num(row.get("涨跌幅")),
            "net_buy": _yi(row.get("龙虎榜净买额")),
            "buy": _yi(row.get("龙虎榜买入额")),
            "sell": _yi(row.get("龙虎榜卖出额")),
            "turnover": _num(row.get("换手率")),
        })
    items.sort(key=lambda item: item.get("net_buy") or 0, reverse=True)
    return {
        "date": target,
        "items": items[: max(1, limit)],
        "total": len(items),
        "note": "" if items else LHB_EMPTY_NOTE,
        "source": "东财 datacenter / stock_lhb_detail_em",
        "caliber": "龙虎榜当日明细（净买额/买入额/卖出额单位=亿元）",
    }


def _lhb_target_date(rows: List[Dict[str, Any]], day: str, field: str) -> str:
    """在区间结果里确定「最新披露日」或用调用方指定日期。

    Args:
        rows: 上游返回的记录列表。
        day: 调用方指定日期（YYYY-MM-DD），空串表示取最新。
        field: 日期列名（机构表用「上榜日期」，营业部表用「上榜日」）。

    Returns:
        str: 目标日期文本（YYYY-MM-DD）。
    """
    if day:
        return day
    dates = sorted({text for text in (_date_text(row.get(field)) for row in rows) if text})
    return dates[-1] if dates else ""


def get_lhb_institution(day: str = "", limit: int = 50) -> Dict[str, Any]:
    """龙虎榜机构买卖统计（东财 stock_lhb_jgmmtj_em，按票汇总机构席位）。

    Args:
        day: 指定日期 YYYY-MM-DD；留空取区间内最新披露日。
        limit: 返回条数上限。

    Returns:
        dict: `{date, items, total, note, source, caliber}`；金额单位=亿元（统一 `_yi` 口径）。

    Raises:
        ValueError: 区间内无机构买卖披露。
    """
    end = date.fromisoformat(day) if day else utils.today_bj()  # TZ-06
    start = end - timedelta(days=LHB_RANGE_DAYS)
    polite_sleep()
    frame = ak.stock_lhb_jgmmtj_em(
        start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d")
    )
    rows = utils.clean_records(frame)
    if not rows:
        raise ValueError(LHB_EMPTY_NOTE)
    target = _lhb_target_date(rows, day, "上榜日期")
    items: List[Dict[str, Any]] = []
    for row in rows:
        if _date_text(row.get("上榜日期")) != target:
            continue
        items.append({
            "date": target,
            "code": str(row.get("代码") or "").split(".")[0],
            "name": row.get("名称"),
            "close": _num(row.get("收盘价")),
            "pct": _num(row.get("涨跌幅")),
            "buy_orgs": _num(row.get("买方机构数")),
            "sell_orgs": _num(row.get("卖方机构数")),
            "buy_amount": _yi(row.get("机构买入总额")),
            "sell_amount": _yi(row.get("机构卖出总额")),
            "net_buy": _yi(row.get("机构买入净额")),
            "ratio": _num(row.get("机构净买额占总成交额比")),
            "turnover": _num(row.get("换手率")),
            "reason": row.get("上榜原因"),
        })
    items.sort(key=lambda item: item.get("net_buy") or 0, reverse=True)
    return {
        "date": target,
        "items": items[: max(1, limit)],
        "total": len(items),
        "note": "" if items else LHB_EMPTY_NOTE,
        "source": "东财 datacenter / stock_lhb_jgmmtj_em",
        "caliber": f"龙虎榜机构席位统计（{target} 上榜日，净买额/买入额/卖出额单位=亿元，"
                   "比值为机构净买额占当日总成交额百分比）",
    }


def get_lhb_broker(day: str = "", limit: int = 50) -> Dict[str, Any]:
    """龙虎榜活跃营业部（东财 stock_lhb_hyyyb_em，按营业部汇总）。

    Args:
        day: 指定日期 YYYY-MM-DD；留空取区间内最新披露日。
        limit: 返回条数上限。

    Returns:
        dict: `{date, items, total, note, source, caliber}`；金额单位=亿元。

    Raises:
        ValueError: 区间内无活跃营业部披露。
    """
    end = date.fromisoformat(day) if day else utils.today_bj()  # TZ-06
    start = end - timedelta(days=LHB_RANGE_DAYS)
    polite_sleep()
    frame = ak.stock_lhb_hyyyb_em(
        start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d")
    )
    rows = utils.clean_records(frame)
    if not rows:
        raise ValueError(LHB_EMPTY_NOTE)
    target = _lhb_target_date(rows, day, "上榜日")
    items: List[Dict[str, Any]] = []
    for row in rows:
        if _date_text(row.get("上榜日")) != target:
            continue
        items.append({
            "date": target,
            "name": row.get("营业部名称"),
            "broker_code": str(row.get("营业部代码") or ""),
            "buy_count": _num(row.get("买入个股数")),
            "sell_count": _num(row.get("卖出个股数")),
            "buy_amount": _yi(row.get("买入总金额")),
            "sell_amount": _yi(row.get("卖出总金额")),
            "net_buy": _yi(row.get("总买卖净额")),
            "stocks": str(row.get("买入股票") or ""),
        })
    items.sort(key=lambda item: item.get("net_buy") or 0, reverse=True)
    return {
        "date": target,
        "items": items[: max(1, limit)],
        "total": len(items),
        "note": "" if items else LHB_EMPTY_NOTE,
        "source": "东财 datacenter / stock_lhb_hyyyb_em",
        "caliber": f"龙虎榜活跃营业部（{target} 上榜日，买卖金额单位=亿元，"
                   "个股数为当日上榜买入/卖出只数）",
    }



# =============================================================
# R3（第十轮）：北向新披露口径 —— 沪深股通成交总额 / 笔数 / 前十活跃
# =============================================================

_EM_DC_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

# 东财 mutual_type 编码：001 沪股通 / 003 深股通 / 005 北向合计
#                     002 港股通(沪) / 004 港股通(深) / 006 南向合计
_NORTH_TYPES = {"001": "沪股通", "003": "深股通", "005": "北向合计"}


def _em_dc(report: str, filter_text: str = "", sort: str = "TRADE_DATE", size: int = 60,
           page: int = 1) -> List[Dict[str, Any]]:
    """调用东财 datacenter 通用报表接口（纯 HTTP，绕开 push2 限制）。

    Args:
        report: 报表名，如 RPT_MUTUAL_DEAL_HISTORY。
        filter_text: filter 表达式。
        sort: 排序列（默认按交易日倒序）。
        size: 每页条数。
        page: 页码。

    Returns:
        List[Dict[str, Any]]: 数据行；接口无数据返回 []。

    Raises:
        ValueError: HTTP 失败或返回体不可解析。
    """
    params = {
        "reportName": report,
        "columns": "ALL",
        "pageNumber": page,
        "pageSize": size,
        "source": "WEB",
        "client": "WEB",
    }
    if filter_text:
        params["filter"] = filter_text
    if sort:
        params["sortColumns"] = sort
        params["sortTypes"] = "-1"
    body = utils.http_get_json(_EM_DC_URL, params=params, referer="https://data.eastmoney.com/", timeout=15)
    if not isinstance(body, dict):
        return []
    result = body.get("result") or {}
    return result.get("data") or []


def get_northbound_turnover(days: int = 20) -> Dict[str, Any]:
    """北向新披露口径（R3）：沪深股通**成交总额 / 成交笔数 / 前十活跃证券**。

    口径说明（2026-11-06 实测，DS-R10-v2.1 §12）
    --------------------------------------------
    - 2024-08-19 起交易所不再披露北向「净买额」，改为收市后披露成交总额与笔数；
    - 数据源：东财 datacenter `RPT_MUTUAL_DEAL_HISTORY`（成交额/笔数，单位百万元）与
      `RPT_MUTUAL_TOP10DEAL`（前十大活跃证券，成交额单位元）；
    - 不显示净买额新数据；历史净买额序列仍由 `get_northbound` 提供（至 2024-08-16）。

    Args:
        days: 近 N 个交易日（默认 20）。

    Returns:
        dict: `{latest, latest_label, turnover:{total, hu, shen, deals_total},
               items:[{date,total,hu,shen,deals}], top:[{rank,code,name,deal_amt,pct}],
               source, caliber, as_of, market_status, is_realtime}`。

    Raises:
        ValueError: 报表无数据（源站异常）。
    """
    limit = max(1, min(days, 60)) * 6
    rows = _em_dc("RPT_MUTUAL_DEAL_HISTORY", size=limit)
    if not rows:
        raise ValueError("北向成交报表返回空表")

    by_date: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        mutual = str(row.get("MUTUAL_TYPE") or "")
        if mutual not in _NORTH_TYPES:
            continue
        day = str(row.get("TRADE_DATE") or "")[:10]
        if not day:
            continue
        slot = by_date.setdefault(day, {})
        deal = _num(row.get("DEAL_AMT"))          # 百万元
        deals = _num(row.get("DEAL_NUM"))         # 笔
        slot[mutual] = {
            "deal_amt": round(deal / 100.0, 2) if deal is not None else None,  # → 亿元
            "deals": int(deals) if deals is not None else None,
        }

    dates = sorted(by_date.keys(), reverse=True)[:limit // 6]
    if not dates:
        raise ValueError("北向成交报表无有效记录")
    latest = dates[0]

    items: List[Dict[str, Any]] = []
    for day in sorted(dates):
        slot = by_date.get(day) or {}
        total = slot.get("005") or {}
        hu = slot.get("001") or {}
        shen = slot.get("003") or {}
        items.append({
            "date": day,
            "total": total.get("deal_amt"),
            "hu": hu.get("deal_amt"),
            "shen": shen.get("deal_amt"),
            "deals": total.get("deals"),
        })

    latest_slot = by_date.get(latest) or {}
    turnover = {
        "total": (latest_slot.get("005") or {}).get("deal_amt"),
        "hu": (latest_slot.get("001") or {}).get("deal_amt"),
        "shen": (latest_slot.get("003") or {}).get("deal_amt"),
        "deals_total": (latest_slot.get("005") or {}).get("deals"),
    }

    # 前十活跃证券：北向只取 001（沪股通）+ 003（深股通），按成交额合并排序取前 10
    top_rows = _em_dc("RPT_MUTUAL_TOP10DEAL", filter_text=f"(TRADE_DATE='{latest}')", size=60)
    merged: List[Dict[str, Any]] = []
    for row in top_rows:
        if str(row.get("MUTUAL_TYPE") or "") not in ("001", "003"):
            continue
        amount = _num(row.get("DEAL_AMT"))       # 元
        if amount is None:
            continue
        merged.append({
            "code": str(row.get("SECURITY_CODE") or ""),
            "name": str(row.get("SECURITY_NAME") or ""),
            "deal_amt": round(amount / 1e8, 2),  # → 亿元
            "pct": _num(row.get("CHANGE_RATE")),
            "close": _num(row.get("CLOSE_PRICE")),
        })
    merged.sort(key=lambda item: item["deal_amt"], reverse=True)
    top = []
    for index, item in enumerate(merged[:10], start=1):
        item["rank"] = index
        top.append(item)

    return {
        "metric": "turnover",
        "latest": latest,
        "latest_label": "沪深股通成交总额 · 交易所 T+0 收市后披露",
        "turnover": turnover,
        "items": items,
        "top": top,
        "source": "东财 datacenter / RPT_MUTUAL_DEAL_HISTORY + RPT_MUTUAL_TOP10DEAL",
        "caliber": "成交总额=买入+卖出成交金额（交易所收市后披露）；2024-08-19 起无净买额披露",
        "as_of": latest,
        "market_status": "收市后披露",
        "is_realtime": False,
    }
