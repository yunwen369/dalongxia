# -*- coding: utf-8 -*-
"""FCF 与利润背离检查（第七轮 M7）。

设计依据 `DS-R7-v1.0` §三 M6（原编号 M6，实施时归入波次 4 的 M7）。
对应循理八条原则第 8 条：**高资本开支期 FCF 先行转负，是利润恶化的领先信号**。

六项检查（原设计六项，实施时第 6 项因缺折旧数据改为「资本开支 / 经营现金流」）：

| 键 | 口径 | 告警阈值 |
|---|---|---|
| `ocf_np` | 经营活动现金流净额 / 净利润 | <0.8 且连续两期 → warn；<0.5 → bad |
| `fcf` | OCF − 资本开支 | 连续两期为负且净利为正 → bad |
| `ar_gap` | 应收增速 − 营收增速 | >20pt → warn |
| `inv_gap` | 存货增速 − 营收增速 | >30pt → warn |
| `debt_ocf` | 有息负债 / OCF | >5 年 → bad |
| `capex_ocf` | 资本开支 / OCF | >1.0 → warn（重资产扩张期，FCF 必为负） |

口径说明（重要）：
- 三表数据是**累计值**（Q1/Q2/Q3/年报），同比必须用「同季对比」（2026Q3 vs 2025Q3），
  不能用相邻期相减——否则会把季节性误判成趋势。
- 折旧数据不可得（在财报附注里），故原「资本开支/折旧」改为「资本开支/OCF」，
  已在返回体 `missing` 中注明。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from backend.app.datasource import stock as stock_provider
from backend.app.datasource import utils

logger = logging.getLogger(__name__)

# 从三表取哪些字段（键名与新浪原始列一致，见 stock.REPORT_COLUMNS）
FIELDS = {
    "ocf": "经营活动产生的现金流量净额",
    "capex": "购建固定资产、无形资产和其他长期资产所支付的现金",
    "np": "净利润",
    "revenue": "营业总收入",
    "ar": "应收账款",
    "inventory": "存货",
    "debt_short": "短期借款",
    "debt_long": "长期借款",
    "debt_current": "一年内到期的非流动负债",
    "debt_bond": "应付债券",
}

MISSING_NOTE = (
    "「资本开支 / 折旧」不可算：折旧数据在财报附注里，现金流量表与资产负债表均无该列；"
    "已改用「资本开支 / 经营现金流」衡量扩张强度。"
)


def _metric_map(report: Dict[str, Any]) -> Dict[str, List[Optional[float]]]:
    """把报表 metrics 转成 {字段名: 值序列}。

    Args:
        report: `get_financial_report` 的 data 段。

    Returns:
        Dict[str, List[Optional[float]]]: 字段 → 各期数值（与 periods 同序）。
    """
    return {item["key"]: item.get("values") or [] for item in report.get("metrics") or []}


def _pick(values: List[Optional[float]], index: int) -> Optional[float]:
    """安全取某一期的值。

    Args:
        values: 值序列。
        index: 下标。

    Returns:
        Optional[float]: 数值；越界或 None 返回 None。
    """
    if index < 0 or index >= len(values):
        return None
    value = values[index]
    return float(value) if value is not None else None


def _yoy_index(periods: List[str], latest_index: int = 0) -> Optional[int]:
    """找同一报告期的上一年下标（20260930 → 20250930）。

    不能用「index + 4」硬编码：若中间有缺期，+4 会错位到别的季度。
    先按年份字面匹配，找不到再退回 +4。

    Args:
        periods: 报告期列表（降序）。
        latest_index: 基准期下标。

    Returns:
        Optional[int]: 上年同期下标；找不到返回 None。
    """
    if latest_index >= len(periods):
        return None
    current = str(periods[latest_index])
    if len(current) < 8:
        return None
    target = str(int(current[:4]) - 1) + current[4:]
    if target in periods:
        return periods.index(target)
    fallback = latest_index + 4
    return fallback if fallback < len(periods) else None


def _growth(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    """同比增速（%）。

    Args:
        current: 本期值。
        previous: 上年同期值。

    Returns:
        Optional[float]: 增速百分数；分母非正返回 None。
    """
    if current is None or previous is None or previous <= 0:
        return None
    return round((current / previous - 1) * 100, 2)


def _check(key: str, label: str, value: Optional[float], unit: str,
           status: str, note: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """构造一条检查结果。

    Args:
        key: 检查键。
        label: 中文名。
        value: 计算结果。
        unit: 单位（倍 / 年 / % / 亿元）。
        status: ok / warn / bad / unknown。
        note: 中文说明。
        extra: 附加字段（如同比前值）。

    Returns:
        Dict[str, Any]: 检查条目。
    """
    item = {"key": key, "label": label, "value": value, "unit": unit, "status": status, "note": note}
    if extra:
        item.update(extra)
    return item


def get_fcf_health(symbol: str) -> Dict[str, Any]:
    """FCF 与利润背离检查（M7）。

    Args:
        symbol: 6 位 A股代码。

    Returns:
        dict: {symbol, period, compare_period, checks, verdict, counters, missing, caliber, as_of}

    Raises:
        ValueError: 三表取不到或期数不足。
    """
    cash = stock_provider.get_financial_report(symbol, "现金流量表", 8)
    balance = stock_provider.get_financial_report(symbol, "资产负债表", 8)
    income = stock_provider.get_financial_report(symbol, "利润表", 8)

    periods = cash.get("periods") or []
    if len(periods) < 2:
        raise ValueError(f"现金流量表期数不足（{len(periods)}）：{symbol}")

    cash_map = _metric_map(cash)
    bal_map = _metric_map(balance)
    inc_map = _metric_map(income)
    latest = 0
    prev = _yoy_index(periods, latest)
    period = str(periods[latest])
    compare = str(periods[prev]) if prev is not None else None

    missing: List[str] = [MISSING_NOTE]
    checks: List[Dict[str, Any]] = []

    # 取本期 / 上年同期
    def pair(source: Dict[str, List[Optional[float]]], field: str) -> Tuple[Optional[float], Optional[float]]:
        """取某字段的本期与上年同期值。

        Args:
            source: 字段→序列映射。
            field: 字段名。

        Returns:
            tuple: (本期, 上年同期)。
        """
        values = source.get(field) or []
        return _pick(values, latest), (_pick(values, prev) if prev is not None else None)

    ocf_now, ocf_prev = pair(cash_map, FIELDS["ocf"])
    capex_now, capex_prev = pair(cash_map, FIELDS["capex"])
    np_now, np_prev = pair(inc_map, FIELDS["np"])
    rev_now, rev_prev = pair(inc_map, FIELDS["revenue"])
    ar_now, ar_prev = pair(bal_map, FIELDS["ar"])
    inv_now, inv_prev = pair(bal_map, FIELDS["inventory"])

    # 适用性判断：银行/保险/券商没有「存货 / 应收 / 资本开支」科目（报表结构不同），
    # FCF 分析对其不适用。若不识别，会把银行巨大的存款波动算成「经营现金流」，
    # 得出「现金流/净利 7.23 倍、回款正常」这种误导性结论。
    no_inventory = inv_now in (None, 0)
    no_capex = capex_now in (None, 0)
    no_ar = ar_now in (None, 0)
    if no_inventory and no_capex and no_ar:
        return {
            "symbol": symbol,
            "period": period,
            "compare_period": compare,
            "applicable": False,
            "reason": (
                "该标的无「存货 / 应收 / 资本开支」科目，符合同业银行/保险/券商等金融业报表特征，"
                "FCF 与营运资本背离分析不适用（金融业的现金流主要来自存贷款与投资，口径不可比）"
            ),
            "checks": [],
            "verdict": "不适用",
            "counters": {"ok": 0, "warn": 0, "bad": 0, "unknown": 0},
            "missing": [MISSING_NOTE],
            "caliber": "金融业不适用 FCF 分析，已跳过六项检查",
            "as_of": utils.now_text(utils.bj_now()),
            "is_realtime": False,
        }

    # ① 经营现金流 / 净利润
    ratio_now = round(ocf_now / np_now, 2) if (ocf_now is not None and np_now and np_now > 0) else None
    ratio_prev = round(ocf_prev / np_prev, 2) if (ocf_prev is not None and np_prev and np_prev > 0) else None
    if ratio_now is None:
        checks.append(_check("ocf_np", "经营现金流/净利润", None, "倍", "unknown", "净利润非正或数据缺失，比率无意义"))
    else:
        status = "ok"
        note = "现金回款覆盖利润正常"
        if ratio_now < 0.5:
            status, note = "bad", "现金回款严重不足，利润含金量低"
        elif ratio_now < 0.8:
            status = "warn"
            note = "现金流低于净利润，需看是否连续"
            if ratio_prev is not None and ratio_prev < 0.8:
                status, note = "bad", "连续两期低于 0.8，利润未转化为现金"
        checks.append(_check("ocf_np", "经营现金流/净利润", ratio_now, "倍", status, note,
                             {"prev": ratio_prev}))

    # ② 自由现金流 = OCF − CapEx
    fcf_now = round((ocf_now - capex_now) / 1e8, 2) if (ocf_now is not None and capex_now is not None) else None
    fcf_prev = round((ocf_prev - capex_prev) / 1e8, 2) if (ocf_prev is not None and capex_prev is not None) else None
    if fcf_now is None:
        checks.append(_check("fcf", "自由现金流", None, "亿元", "unknown", "经营现金流或资本开支缺失"))
    else:
        status = "ok"
        note = "经营现金流覆盖资本开支"
        if fcf_now < 0:
            if np_now is not None and np_now > 0:
                status, note = "bad", "净利为正但自由现金流为负，扩张在吃现金"
                if fcf_prev is not None and fcf_prev < 0:
                    note = "连续两期自由现金流为负而净利为正，需高度警惕"
            else:
                status, note = "warn", "自由现金流为负（净利亦为负，属亏损期常态）"
        checks.append(_check("fcf", "自由现金流", fcf_now, "亿元", status, note,
                             {"prev": fcf_prev}))

    # ③ 应收增速 − 营收增速
    # 基数校验：应收占营收不足 2% 时，同比增速会被小基数放大成几百个百分点
    # （实测茅台应收基数仅千万级，算出来 1325pt，数学正确但业务上无意义）。
    rev_g = _growth(rev_now, rev_prev)
    ar_g = _growth(ar_now, ar_prev)
    ar_share = (ar_now / rev_now * 100) if (ar_now is not None and rev_now and rev_now > 0) else None
    if rev_g is None or ar_g is None:
        checks.append(_check("ar_gap", "应收增速−营收增速", None, "pt", "unknown", "应收或营收数据缺失"))
    elif ar_share is None or ar_share < 2.0:
        checks.append(_check(
            "ar_gap", "应收增速−营收增速", None, "pt", "unknown",
            f"应收仅占营收 {ar_share:.2f}%，基数过小，增速差不具参考意义" if ar_share is not None
            else "应收占营收比例不可得",
            {"detail": {"ar": ar_g, "revenue": rev_g, "ar_share_pct": round(ar_share, 2) if ar_share else None}},
        ))
    else:
        gap = round(ar_g - rev_g, 2)
        status = "ok" if gap <= 20 else "warn"
        note = "应收增速与营收匹配" if gap <= 20 else "应收增长快于收入，可能在靠赊销做收入"
        checks.append(_check("ar_gap", "应收增速−营收增速", gap, "pt", status, note,
                             {"prev": None, "detail": {"ar": ar_g, "revenue": rev_g,
                                                       "ar_share_pct": round(ar_share, 2)}}))

    # ④ 存货增速 − 营收增速（同样做基数校验，阈值 5%）
    inv_g = _growth(inv_now, inv_prev)
    inv_share = (inv_now / rev_now * 100) if (inv_now is not None and rev_now and rev_now > 0) else None
    if rev_g is None or inv_g is None:
        checks.append(_check("inv_gap", "存货增速−营收增速", None, "pt", "unknown", "存货或营收数据缺失"))
    elif inv_share is None or inv_share < 5.0:
        checks.append(_check(
            "inv_gap", "存货增速−营收增速", None, "pt", "unknown",
            f"存货仅占营收 {inv_share:.2f}%，基数过小，增速差不具参考意义" if inv_share is not None
            else "存货占营收比例不可得",
            {"detail": {"inventory": inv_g, "revenue": rev_g,
                        "inv_share_pct": round(inv_share, 2) if inv_share else None}},
        ))
    else:
        gap = round(inv_g - rev_g, 2)
        status = "ok" if gap <= 30 else "warn"
        note = "存货与收入同步" if gap <= 30 else "存货积压快于收入，可能在向渠道压货"
        checks.append(_check("inv_gap", "存货增速−营收增速", gap, "pt", status, note,
                             {"prev": None, "detail": {"inventory": inv_g, "revenue": rev_g,
                                                       "inv_share_pct": round(inv_share, 2)}}))

    # ⑤ 有息负债 / 经营现金流
    debt_parts = []
    debt_detail: Dict[str, Optional[float]] = {}
    for field in ("debt_short", "debt_long", "debt_current", "debt_bond"):
        value_now, _value_prev = pair(bal_map, FIELDS[field])
        debt_detail[field] = round(value_now / 1e8, 2) if value_now is not None else None
        if value_now:
            debt_parts.append(value_now)
    debt_now = sum(debt_parts) if debt_parts else 0.0
    years = round(debt_now / ocf_now, 2) if (ocf_now and ocf_now > 0 and debt_now > 0) else None
    if years is not None and years < 0.01:
        # 负债相对经营现金流极小（如茅台仅 6582 万 / 840 亿），显示 0.0 年信息量为零
        years = None
        debt_now = 0.0
    if years is None:
        note = "无有息负债或经营现金流为负" if debt_now == 0 else "经营现金流为负，无法计算偿债年限"
        checks.append(_check("debt_ocf", "有息负债/经营现金流", None, "年", "ok" if debt_now == 0 else "unknown",
                             "无有息负债" if debt_now == 0 else note, {"detail": debt_detail}))
    else:
        status = "ok" if years <= 5 else "bad"
        note = "偿债压力可控" if years <= 5 else "有息负债超过 5 年经营现金流，偿债压力大"
        checks.append(_check("debt_ocf", "有息负债/经营现金流", years, "年", status, note,
                             {"detail": debt_detail}))

    # ⑥ 资本开支 / 经营现金流（替代原「资本开支/折旧」）
    capex_ratio = round(capex_now / ocf_now, 2) if (capex_now is not None and ocf_now and ocf_now > 0) else None
    if capex_ratio is None:
        checks.append(_check("capex_ocf", "资本开支/经营现金流", None, "倍", "unknown", "资本开支或经营现金流缺失"))
    else:
        status = "warn" if capex_ratio > 1.0 else "ok"
        note = "扩张强度可控" if capex_ratio <= 1.0 else "资本开支超过经营现金流，重资产扩张期，FCF 必为负"
        checks.append(_check("capex_ocf", "资本开支/经营现金流", capex_ratio, "倍", status, note))

    warn = sum(1 for item in checks if item["status"] == "warn")
    bad = sum(1 for item in checks if item["status"] == "bad")
    unknown = sum(1 for item in checks if item["status"] == "unknown")
    if bad:
        verdict = f"发现 {bad} 项异常、{warn} 项需关注" + (f"、{unknown} 项数据不足" if unknown else "")
    elif warn:
        verdict = f"{warn} 项需关注" + (f"，{unknown} 项数据不足" if unknown else "")
    else:
        verdict = "六项检查均正常" + (f"（{unknown} 项数据不足）" if unknown else "")

    return {
        "symbol": symbol,
        "period": period,
        "compare_period": compare,
        "applicable": True,
        "checks": checks,
        "verdict": verdict,
        "counters": {"ok": sum(1 for i in checks if i["status"] == "ok"), "warn": warn, "bad": bad, "unknown": unknown},
        "missing": missing,
        "caliber": (
            f"数据为累计口径（{period} vs 上年同期 {compare}），同比按同季对比，不做相邻期相减；"
            "有息负债=短期借款+长期借款+一年内到期的非流动负债+应付债券；"
            "OCF=经营活动产生的现金流量净额；资本开支=购建固定资产、无形资产和其他长期资产所支付的现金"
        ),
        "as_of": utils.now_text(utils.bj_now()),
        "is_realtime": False,
    }
