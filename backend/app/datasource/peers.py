# -*- coding: utf-8 -*-
"""同业对比（第七轮 M8）。

设计依据 `DS-R7-v1.0` §三 M7（原编号 M7，实施时归入波次 4 的 M8）。

只回答「同一门类里，谁贵谁便宜、谁赚钱效率高」，**不做排名打分、不给买卖建议**。
跨行业对比会给出警告：不同行业的估值中枢天然不同（银行 5 倍、半导体 50 倍都合理），
横向比 PE 容易得出错误结论。

五类指标（每类挑 2–3 个可复算项）：
- 估值：PE-TTM / PB（来自实时报价）
- 质量：ROE / 毛利率
- 成长：营收同比 / 净利同比（同季对比）
- 杠杆：资产负债率
- 现金流：经营现金流 / 净利润（与 M7 同口径，便于交叉印证）
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from backend.app.datasource import stock as stock_provider
from backend.app.datasource import tencent, utils

logger = logging.getLogger(__name__)

# 指标定义：(键, 名称, 分组, 单位, 是否越大越好)
METRICS = [
    ("pe", "PE-TTM", "估值", "倍", False),
    ("pb", "PB", "估值", "倍", False),
    ("roe", "ROE", "质量", "%", True),
    ("gross_margin", "毛利率", "质量", "%", True),
    ("revenue_yoy", "营收同比", "成长", "%", True),
    ("profit_yoy", "净利同比", "成长", "%", True),
    ("debt_ratio", "资产负债率", "杠杆", "%", False),
    ("ocf_np", "经营现金流/净利", "现金流", "倍", True),
]


def _num(value: Any) -> Optional[float]:
    """安全转 float。

    Args:
        value: 任意值。

    Returns:
        Optional[float]: 数值；无法转换返回 None。
    """
    try:
        if value is None:
            return None
        number = float(value)
        return number if number == number else None  # 过滤 NaN
    except (TypeError, ValueError):
        return None


def _yoy_index(periods: List[str], latest: int = 0) -> Optional[int]:
    """找上年同期下标。

    Args:
        periods: 报告期（降序）。
        latest: 基准下标。

    Returns:
        Optional[int]: 上年同期下标。
    """
    if latest >= len(periods):
        return None
    current = str(periods[latest])
    if len(current) < 8:
        return None
    target = str(int(current[:4]) - 1) + current[4:]
    if target in periods:
        return periods.index(target)
    fallback = latest + 4
    return fallback if fallback < len(periods) else None


def _one(symbol: str) -> Dict[str, Any]:
    """取单家公司的对比指标。

    Args:
        symbol: 6 位代码。

    Returns:
        Dict[str, Any]: {symbol, name, industry, metrics:{...}, missing:[...]}
    """
    missing: List[str] = []
    metrics: Dict[str, Optional[float]] = {}
    name = symbol
    industry = None

    # 行情：名称 / PE / PB
    try:
        quote = tencent.get_quote(symbol, "a") or {}
        name = quote.get("name") or symbol
        metrics["pe"] = _num(quote.get("pe_ttm"))
        metrics["pb"] = _num(quote.get("pb"))
    except Exception as exc:  # noqa: BLE001 - 行情失败不阻断财报部分
        logger.info("同业对比-行情失败 %s：%s", symbol, type(exc).__name__)
        missing.append(f"{symbol}（行情）")

    # 行业（用于跨行业警告）
    try:
        profile = stock_provider.get_profile(symbol)
        industry = profile.get("industry")
        raw_industry = profile.get("industry")
        if raw_industry:
            industry = str(raw_industry).split("-")[-1]
    except Exception:  # noqa: BLE001 - 行业缺失不阻断
        industry = None

    # 财务：ROE / 毛利率 / 增速 / 负债率（financial-summary 已有六项）
    try:
        summary = stock_provider.get_financial_summary(symbol)
        periods = summary.get("periods") or []
        table = {item["key"]: item.get("values") or [] for item in summary.get("metrics") or []}
        latest = 0
        prev = _yoy_index(periods, latest)
        metrics["roe"] = _num((table.get("净资产收益率(ROE)") or [None])[0])
        metrics["gross_margin"] = _num((table.get("毛利率") or [None])[0])
        metrics["debt_ratio"] = _num((table.get("资产负债率") or [None])[0])
        if prev is not None:
            revenue = table.get("营业总收入") or []
            profit = table.get("归母净利润") or []
            if len(revenue) > prev and revenue[prev]:
                metrics["revenue_yoy"] = round((revenue[0] / revenue[prev] - 1) * 100, 2)
            if len(profit) > prev and profit[prev] and profit[prev] > 0:
                metrics["profit_yoy"] = round((profit[0] / profit[prev] - 1) * 100, 2)
    except Exception as exc:  # noqa: BLE001 - 财务摘要失败不阻断
        logger.info("同业对比-摘要失败 %s：%s", symbol, type(exc).__name__)
        missing.append(f"{symbol}（财务摘要）")

    # 现金流 / 净利（与 M7 同口径，复用现金流量表）
    try:
        cash = stock_provider.get_financial_report(symbol, "现金流量表", 4)
        table = {item["key"]: item.get("values") or [] for item in cash.get("metrics") or []}
        ocf = _num((table.get("经营活动产生的现金流量净额") or [None])[0])
        if ocf is not None:
            try:
                income = stock_provider.get_financial_report(symbol, "利润表", 4)
                inc = {i["key"]: i.get("values") or [] for i in income.get("metrics") or []}
                net_profit = _num((inc.get("净利润") or [None])[0])
                if net_profit and net_profit > 0:
                    # 保留 3 位：实测五粮液 2026Q3 为 0.005（现金流断崖），两位会显示成 0.0 像缺值
                    metrics["ocf_np"] = round(ocf / net_profit, 3)
            except Exception as exc:  # noqa: BLE001 - 单指标失败不影响其余同业指标
                logger.info("同业对比-现金流/净利比失败 %s：%s", symbol, type(exc).__name__)
    except Exception as exc:  # noqa: BLE001
        logger.info("同业对比-现金流失败 %s：%s", symbol, type(exc).__name__)
        missing.append(f"{symbol}（现金流）")

    return {
        "symbol": symbol,
        "name": name,
        "industry": industry,
        "metrics": {key: (round(value, 3) if key == "ocf_np" and value is not None
                             else (round(value, 2) if value is not None else None))
                    for key, value in metrics.items()},
        "missing": missing,
    }


def compare(symbols: List[str]) -> Dict[str, Any]:
    """多家公司同业对比。

    Args:
        symbols: 2–5 个 6 位代码。

    Returns:
        dict: {companies, best, industry_warning, missing, caliber, as_of}

    Raises:
        ValueError: 有效样本不足 2 家。
    """
    companies = []
    missing: List[str] = []
    for code in symbols:
        item = _one(code)
        missing.extend(item.get("missing") or [])
        if any(value is not None for value in item["metrics"].values()):
            companies.append(item)

    if len(companies) < 2:
        raise ValueError("有效样本不足 2 家（财报或行情取不到），无法对比")

    # 每项指标标出「同组最优」（数值，不做排名分；缺值跳过）
    best: Dict[str, Dict[str, Any]] = {}
    for key, _label, _group, _unit, higher_better in METRICS:
        pairs = [(c["symbol"], c["metrics"].get(key)) for c in companies]
        pairs = [(sym, val) for sym, val in pairs if val is not None]
        if not pairs:
            continue
        target = max(pairs, key=lambda p: p[1]) if higher_better else min(pairs, key=lambda p: p[1])
        best[key] = {"symbol": target[0], "value": target[1]}

    industries = {c["industry"] for c in companies if c.get("industry")}
    warning = None
    if len(industries) > 1:
        warning = (
            f"所选公司分属 {len(industries)} 个行业（{'、'.join(sorted(industries))}）："
            "不同行业的估值中枢与毛利率结构天然不同，横向比 PE/PB 仅供参考，不宜直接下结论"
        )

    return {
        "companies": companies,
        "metrics": [{"key": k, "label": label, "group": group, "unit": unit, "higher_better": hb}
                    for k, label, group, unit, hb in METRICS],
        "best": best,
        "industry_warning": warning,
        "missing": missing,
        "caliber": (
            "估值取实时报价（PE-TTM / PB）；ROE/毛利率/负债率取最新报告期；"
            "增速用同季对比（最新期 vs 上年同期）；经营现金流/净利用现金流量表与利润表同期；"
            "「最优」仅表示该指标在同组内的数值方向，不代表投资优劣"
        ),
        "as_of": utils.now_text(utils.bj_now()),
        "is_realtime": True,
    }
