# -*- coding: utf-8 -*-
"""宏观经济数据（第七轮 M1）：PMI / CPI / PPI / M2 / 国债收益率 + 周期象限判定。

数据源与口径（2026-10-31 实测，全部为官方月度公布值）：
- PMI：`macro_china_pmi` → 225 期，最新 2026-09（制造业 50.1 / 非制造业 50.2）
- CPI：`macro_china_cpi` → 225 期，最新 2026-09（同比 +0.9%）
- PPI：`macro_china_ppi` → 249 期，最新 2026-09（同比 +5.0%）
- M2/M1：`macro_china_money_supply` → 225 期，最新 2026-09（M2 +7.2% / M1 +3.2%）
- 国债收益率：`bond_china_yield` → 中债国债收益率曲线，最新 2026-10-30（10Y 1.65%）

**已废弃源（实测不可用，不得回退使用）**：
- `macro_china_pmi_yearly`：最新仅到 2025-08（落后 14 个月）
- `macro_china_cpi_monthly` / `macro_china_ppi_yearly`：最新到 2025-09 且「今值」为 NaN
- `macro_china_shrzgm`（社融）：接口指向 `data.mofcom.gov.cn` 已失效 → 本模块社融返回
  `available=false` + 原因，不以 0 冒充（规范 §6.3）。

周期象限判定口径（写在返回值里，供前端标注）：
- 增长轴：制造业 PMI 近 3 期均值 vs 前 3 期均值（方向而非绝对水平，避开荣枯线附近抖动）
- 通胀轴：PPI 同比近 3 期均值 vs 前 3 期均值
- 四象限：增长↑通胀↑=过热 / 增长↑通胀↓=复苏 / 增长↓通胀↑=滞胀 / 增长↓通胀↓=衰退

约束：
1. 每个指标独立取数，单指标失败不影响其他（空指标返回 value=None + reason）；
2. 附 `available` / `reason`，不静默给 0；
3. 不预测、不给买卖建议，`conclusion` 只描述"位置 + 理论含义"，并固定附免责声明。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from backend.app import config
from backend.app.core import cache
from backend.app.datasource import utils
from backend.app.datasource.common import to_float as _common_to_float

logger = logging.getLogger(__name__)

# 指标定义：key → (中文名, 单位, 小数位)
INDICATOR_META: Dict[str, Tuple[str, str, int]] = {
    "pmi": ("制造业 PMI", "", 1),
    "pmi_service": ("非制造业 PMI", "", 1),
    "cpi": ("CPI 同比", "%", 1),
    "ppi": ("PPI 同比", "%", 1),
    "m2": ("M2 同比", "%", 1),
    "m1_m2": ("M1−M2 剪刀差", "pt", 1),
    "bond_10y": ("10Y 国债", "%", 2),
    "term_spread": ("期限利差 10Y−1Y", "%", 2),
    "social_financing": ("社融存量同比", "%", 1),
}

# 四象限：增长方向 + 通胀方向 → 阶段名与理论含义
QUADRANTS: Dict[Tuple[str, str], Tuple[str, str]] = {
    ("up", "up"): ("过热", "增长与通胀同步上行，历史上对应大宗商品与周期股占优、债券承压"),
    ("up", "down"): ("复苏", "增长改善而通胀回落，历史上对应权益资产胜率较高的阶段"),
    ("down", "up"): ("滞胀", "增长放缓而通胀上行，历史上对应盈利与估值同时承压"),
    ("down", "down"): ("衰退", "增长与通胀同步回落，历史上对应债券与现金占优"),
}

DISCLAIMER = "以上为宏观数据的位置判断与历史规律描述，不构成投资建议，也不预测未来。"


def _parse_month(text: Any) -> Optional[str]:
    """把「2026年09月份」这类中文月份解析为 ISO 年月（2026-09）。

    Args:
        text: 原始月份文本。

    Returns:
        Optional[str]: 解析成功返回 `YYYY-MM`，失败返回 None。
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    digits = ""
    year = ""
    month = ""
    for char in raw:
        if char.isdigit():
            digits += char
        elif char == "年" and digits:
            year = digits
            digits = ""
        elif char == "月" and digits:
            month = digits
            break
    if len(year) == 4 and month:
        return f"{year}-{int(month):02d}"
    return None


# P1 收敛：_to_float 统一委托 common.to_float（原 alerts/macro/valuation 薄包装）。
_to_float = _common_to_float


def _frame_rows(dataframe) -> List[Dict[str, Any]]:
    """把 DataFrame 转为记录列表（保留原始列名）。

    Args:
        dataframe: pandas DataFrame。

    Returns:
        List[Dict[str, Any]]: 逐行字典。
    """
    if dataframe is None or len(dataframe) == 0:
        return []
    return dataframe.to_dict("records")


def _build_indicator(
    key: str,
    value: Optional[float],
    prev: Optional[float],
    period: Optional[str],
    source: str,
    reason: str = "",
) -> Dict[str, Any]:
    """组装单个指标卡数据。

    Args:
        key: 指标键（须在 INDICATOR_META 中登记）。
        value: 最新值。
        prev: 上期值。
        period: 数据期次（YYYY-MM）。
        source: 数据源标识。
        reason: 不可用时的原因（value 为 None 时必填）。

    Returns:
        Dict[str, Any]: 指标卡结构。
    """
    name, unit, digits = INDICATOR_META.get(key, (key, "", 2))
    delta = None
    direction = "flat"
    if value is not None and prev is not None:
        delta = round(value - prev, digits)
        if delta > 0:
            direction = "up"
        elif delta < 0:
            direction = "down"
    payload: Dict[str, Any] = {
        "key": key,
        "name": name,
        "unit": unit,
        "digits": digits,
        "value": None if value is None else round(value, digits),
        "prev": None if prev is None else round(prev, digits),
        "delta": delta,
        "direction": direction,
        "period": period,
        "source": source,
        "available": value is not None,
    }
    if value is None:
        payload["reason"] = reason or "数据源未返回有效值"
    return payload


# ---------------------------------------------------------------- 各指标取数
def _fetch_pmi() -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
    """制造业与非制造业 PMI。

    Returns:
        Tuple: (制造业指标, 非制造业指标, 序列)。序列为升序 [{period, value, service}]。

    Raises:
        ValueError: PMI 返回空。
    """
    import akshare as ak

    rows = _frame_rows(ak.macro_china_pmi())
    parsed: List[Dict[str, Any]] = []
    for row in rows:
        period = _parse_month(row.get("月份"))
        value = _to_float(row.get("制造业-指数"))
        service = _to_float(row.get("非制造业-指数"))
        if period and value is not None:
            parsed.append({"period": period, "value": value, "service": service})
    parsed.sort(key=lambda item: item["period"])
    if not parsed:
        raise ValueError("PMI 返回空")
    last = parsed[-1]
    prev = parsed[-2] if len(parsed) > 1 else None
    manufacturing = _build_indicator(
        "pmi", last["value"], prev["value"] if prev else None, last["period"], "国家统计局/akshare",
    )
    service_indicator = _build_indicator(
        "pmi_service", last.get("service"), (prev or {}).get("service"), last["period"], "国家统计局/akshare",
    )
    return manufacturing, service_indicator, parsed[-24:]


def _fetch_price_index(fn_name: str, key: str, column: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """CPI / PPI 同比。

    Args:
        fn_name: akshare 函数名。
        key: 指标键。
        column: 同比列名。

    Returns:
        Tuple: (指标卡, 序列)。

    Raises:
        ValueError: 对应指标返回空。
    """
    import akshare as ak

    rows = _frame_rows(getattr(ak, fn_name)())
    parsed: List[Dict[str, Any]] = []
    for row in rows:
        period = _parse_month(row.get("月份"))
        value = _to_float(row.get(column))
        if period and value is not None:
            parsed.append({"period": period, "value": value})
    parsed.sort(key=lambda item: item["period"])
    if not parsed:
        raise ValueError(f"{key} 返回空")
    last = parsed[-1]
    prev = parsed[-2] if len(parsed) > 1 else None
    indicator = _build_indicator(
        key, last["value"], prev["value"] if prev else None, last["period"], "国家统计局/akshare",
    )
    return indicator, parsed[-24:]


def _fetch_money() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """M2 与 M1−M2 剪刀差。

    Returns:
        Tuple: ([M2 指标, 剪刀差指标], 序列)。

    Raises:
        ValueError: M2 返回空。
    """
    import akshare as ak

    rows = _frame_rows(ak.macro_china_money_supply())
    parsed: List[Dict[str, Any]] = []
    for row in rows:
        period = _parse_month(row.get("月份"))
        m2 = _to_float(row.get("货币和准货币(M2)-同比增长"))
        m1 = _to_float(row.get("货币(M1)-同比增长"))
        if period and m2 is not None:
            parsed.append({"period": period, "m2": m2, "gap": None if m1 is None else round(m1 - m2, 2)})
    parsed.sort(key=lambda item: item["period"])
    if not parsed:
        raise ValueError("M2 返回空")
    last = parsed[-1]
    prev = parsed[-2] if len(parsed) > 1 else None
    m2_indicator = _build_indicator(
        "m2", last["m2"], prev["m2"] if prev else None, last["period"], "中国人民银行/akshare",
    )
    gap_indicator = _build_indicator(
        "m1_m2", last["gap"], prev["gap"] if prev else None, last["period"], "中国人民银行/akshare",
    )
    return [m2_indicator, gap_indicator], parsed[-24:]


def _fetch_yield() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """国债收益率（10Y 与期限利差）。

    Returns:
        Tuple: ([10Y 指标, 期限利差指标], 序列)。

    Raises:
        ValueError: 国债收益率返回空。
    """
    import akshare as ak

    now = utils.bj_now()
    start = (now.replace(day=1)).strftime("%Y%m%d")
    end = now.strftime("%Y%m%d")
    frame = ak.bond_china_yield(start_date=start, end_date=end)
    rows = [row for row in _frame_rows(frame) if "国债" in str(row.get("曲线名称") or "")]
    parsed: List[Dict[str, Any]] = []
    for row in rows:
        date_text = str(row.get("日期") or "")[:10]
        ten = _to_float(row.get("10年"))
        one = _to_float(row.get("1年"))
        if date_text and ten is not None:
            parsed.append({
                "period": date_text,
                "ten": ten,
                "one": one,
                "spread": None if one is None else round(ten - one, 4),
            })
    parsed.sort(key=lambda item: item["period"])
    if not parsed:
        raise ValueError("国债收益率返回空")
    last = parsed[-1]
    prev = parsed[-2] if len(parsed) > 1 else None
    ten_indicator = _build_indicator(
        "bond_10y", last["ten"], prev["ten"] if prev else None, last["period"], "中债/akshare",
    )
    spread_indicator = _build_indicator(
        "term_spread", last["spread"], prev["spread"] if prev else None, last["period"], "中债/akshare",
    )
    return [ten_indicator, spread_indicator], parsed[-24:]


def _fetch_social_financing() -> Dict[str, Any]:
    """社融存量同比（当前无可用源，按空契约返回）。

    Returns:
        Dict[str, Any]: 不可用指标卡。
    """
    return _build_indicator(
        "social_financing", None, None, None, "东财 datacenter（待接入）",
        reason="社融源站接口已失效（data.mofcom.gov.cn 不可达），正在切换东财 datacenter；暂不以 0 代替",
    )


# ---------------------------------------------------------------- 周期判定
def _mean(values: List[float]) -> Optional[float]:
    """求均值（空列表返回 None）。

    Args:
        values: 数值列表。

    Returns:
        Optional[float]: 均值。
    """
    clean = [item for item in values if item is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _direction(series: List[Dict[str, Any]], key: str, window: int = 3) -> Tuple[str, Dict[str, Any]]:
    """比较近 window 期与前 window 期的均值方向。

    Args:
        series: 升序序列。
        key: 取值字段。
        window: 窗口期数。

    Returns:
        Tuple[str, Dict[str, Any]]: ("up"/"down"/"flat", 明细)。
    """
    values = [item.get(key) for item in series if item.get(key) is not None]
    if len(values) < window * 2:
        return "flat", {"recent": None, "previous": None, "delta": None}
    recent = _mean(values[-window:])
    previous = _mean(values[-window * 2:-window])
    if recent is None or previous is None:
        return "flat", {"recent": None, "previous": None, "delta": None}
    delta = round(recent - previous, 3)
    threshold = 0.02 if key != "value" else 0.05
    if delta > threshold:
        direction = "up"
    elif delta < -threshold:
        direction = "down"
    else:
        direction = "flat"
    return direction, {"recent": round(recent, 3), "previous": round(previous, 3), "delta": delta}


def _build_quadrant(pmi_series: List[Dict[str, Any]], ppi_series: List[Dict[str, Any]]) -> Dict[str, Any]:
    """按 PMI 与 PPI 的方向定位周期象限并给出结论文案。

    Args:
        pmi_series: PMI 升序序列（含 value）。
        ppi_series: PPI 升序序列（含 value）。

    Returns:
        Dict[str, Any]: 象限判定结果。
    """
    growth, growth_detail = _direction(pmi_series, "value")
    inflation, inflation_detail = _direction(ppi_series, "value")
    if growth == "flat":
        stage, meaning = "过渡期", "增长方向不明显（近 3 期与前 3 期差异在阈值内），象限暂不定位"
    else:
        stage, meaning = QUADRANTS.get((growth, inflation), ("过渡期", "通胀方向不明显，象限暂不定位"))
    conclusion = (
        f"增长{'扩张' if growth == 'up' else ('放缓' if growth == 'down' else '走平')}、"
        f"通胀{'上行' if inflation == 'up' else ('回落' if inflation == 'down' else '走平')}，"
        f"当前位于「{stage}」阶段。{meaning}。"
    )
    return {
        "growth": growth,
        "inflation": inflation,
        "stage": stage,
        "conclusion": conclusion,
        "detail": {"growth": growth_detail, "inflation": inflation_detail},
        "window": 3,
        "caliber": "增长轴=制造业 PMI 近3期均值 vs 前3期均值；通胀轴=PPI 同比同口径；阈值 0.05 / 0.02",
        "disclaimer": DISCLAIMER,
    }


# ---------------------------------------------------------------- 对外入口
def _load_overview() -> Dict[str, Any]:
    """实际取数入口（交给缓存包装）。

    Returns:
        Dict[str, Any]: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/is_realtime。
    """
    indicators: List[Dict[str, Any]] = []
    series: Dict[str, List[Dict[str, Any]]] = {}
    sources: List[str] = []
    failures: List[str] = []

    try:
        pmi_indicator, service_indicator, pmi_series = _fetch_pmi()
        indicators.extend([pmi_indicator, service_indicator])
        series["pmi"] = pmi_series
        if pmi_indicator["available"]:
            sources.append("PMI")
    except Exception as exc:  # noqa: BLE001 - 单指标失败不影响其他
        logger.info("PMI 取数失败：%s", type(exc).__name__)
        failures.append(f"PMI（{type(exc).__name__}）")
        indicators.append(_build_indicator("pmi", None, None, None, "国家统计局", "PMI 通道异常"))
        indicators.append(_build_indicator("pmi_service", None, None, None, "国家统计局", "PMI 通道异常"))
        series["pmi"] = []

    for fn_name, key, column, label in (
        ("macro_china_cpi", "cpi", "全国-同比增长", "CPI"),
        ("macro_china_ppi", "ppi", "当月同比增长", "PPI"),
    ):
        try:
            indicator, price_series = _fetch_price_index(fn_name, key, column)
            indicators.append(indicator)
            series[key] = price_series
            if indicator["available"]:
                sources.append(label)
        except Exception as exc:  # noqa: BLE001 - 单指标失败不影响其他
            logger.info("%s 取数失败：%s", label, type(exc).__name__)
            failures.append(f"{label}（{type(exc).__name__}）")
            indicators.append(_build_indicator(key, None, None, None, "国家统计局", f"{label} 通道异常"))
            series[key] = []

    try:
        money_indicators, money_series = _fetch_money()
        indicators.extend(money_indicators)
        series["m2"] = money_series
        if money_indicators[0]["available"]:
            sources.append("M2")
    except Exception as exc:  # noqa: BLE001 - 单指标失败不影响其他
        logger.info("M2 取数失败：%s", type(exc).__name__)
        failures.append(f"M2（{type(exc).__name__}）")
        indicators.append(_build_indicator("m2", None, None, None, "中国人民银行", "M2 通道异常"))
        indicators.append(_build_indicator("m1_m2", None, None, None, "中国人民银行", "M2 通道异常"))
        series["m2"] = []

    try:
        yield_indicators, yield_series = _fetch_yield()
        indicators.extend(yield_indicators)
        series["bond"] = yield_series
        if yield_indicators[0]["available"]:
            sources.append("国债收益率")
    except Exception as exc:  # noqa: BLE001 - 单指标失败不影响其他
        logger.info("国债收益率取数失败：%s", type(exc).__name__)
        failures.append(f"国债收益率（{type(exc).__name__}）")
        indicators.append(_build_indicator("bond_10y", None, None, None, "中债", "国债通道异常"))
        indicators.append(_build_indicator("term_spread", None, None, None, "中债", "国债通道异常"))
        series["bond"] = []

    indicators.append(_fetch_social_financing())
    failures.append("社融（源站已失效，待换源）")

    # 指标按看板顺序排列（不依赖取数顺序）
    order = ["pmi", "pmi_service", "cpi", "ppi", "m2", "m1_m2", "bond_10y", "term_spread", "social_financing"]
    indicators.sort(key=lambda item: order.index(item["key"]) if item["key"] in order else 99)

    quadrant = _build_quadrant(series.get("pmi") or [], series.get("ppi") or [])
    available_count = sum(1 for item in indicators if item.get("available"))

    return {
        "indicators": indicators,
        "quadrant": quadrant,
        "series": series,
        "available_count": available_count,
        "total_count": len(indicators),
        "source": "源站名称：" + "、".join(sources) if sources else "无可用源",
        "caliber": (
            "均为官方月度公布值（PMI/CPI/PPI/M2 为国家统计局与人民银行，国债收益率为中债曲线）；"
            "国债为交易日日频，其余为月度；不预测、不构成投资建议"
        ),
        "as_of": utils.now_text(utils.bj_now()),
        "is_realtime": False,
        "missing": failures,
    }


def get_overview(force: bool = False) -> Dict[str, Any]:
    """宏观总览（6+3 指标 + 周期象限），24 小时缓存。

    Args:
        force: 为 True 时绕过缓存强制重取。

    Returns:
        Dict[str, Any]: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/is_realtime。
    """
    if force:
        return _load_overview()
    return cache.get_or_load("macro:overview", config.TTL.get("macro", 86400), _load_overview)
