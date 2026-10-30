# -*- coding: utf-8 -*-
"""估值分位（第七轮 M2）：指数 / 全 A / 行业的 PE 分位与股权风险溢价（ERP）。

数据源与口径（2026-10-31 实测）：
- 指数：`stock_index_pe_lg(symbol)` → 259 期月频（2005-04 ~ 2026-10），取「滚动市盈率」
- 全 A：`stock_a_ttm_lyr()` → 262 期，取 `middlePETTM`（中位数 PE-TTM）
- 行业：`stock_industry_pe_ratio_cninfo()` → 120 行**单时点快照**，取「静态市盈率-加权平均」
- 无风险利率：复用 `macro.get_overview` 的 10Y 国债

**分位口径（写进返回值）**：
- 纵向分位：窗口内 `≤ 当前值` 的样本占比（3/5/10 年），样本为月频；
- 行业分位：源站只给单时点快照，无法算纵向分位 → 改用**横截面分位**
  （该行业 PE 在全部行业中升序排名占比），字段名 `cross_pct` 以区别于 `pct3y`。

约束：
1. 分位低 ≠ 会涨：返回值固定附 `disclaimer`，前端必须展示；
2. 单指数失败不影响其他（`available=false` + `reason`）；
3. ERP = 1/PE − 10Y，PE 缺失或为负时返回 None（不硬算）。
"""

from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from backend.app import config
from backend.app.core import cache
from backend.app.datasource import macro, utils

logger = logging.getLogger(__name__)

# 指数清单（2026-10-31 实测：乐咕仅支持以下 4 个；
# 创业板指/深证成指/中小板指返回 KeyError，中证1000/创业板50/上证380 站点限频不可用，
# 中证指数官网接口（stock_zh_index_value_csindex）历史仅 20 个交易日、且 399006 返回 404，
# 均不足以计算分位 → 本轮不纳入，界面注明「该指数暂无可用估值源」）
INDEX_LIST = ["沪深300", "中证500", "上证50", "上证180"]

DISCLAIMER = "分位只表示当前估值在历史区间中的相对位置，不代表未来涨跌，不构成投资建议。"


def _to_float(value: Any) -> Optional[float]:
    """安全转 float（NaN / 空值返回 None）。

    Args:
        value: 原始值。

    Returns:
        Optional[float]: 转换结果。
    """
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def _as_date(value: Any) -> Optional[date]:
    """把多种形态的日期转为 date。

    Args:
        value: date / datetime / 字符串。

    Returns:
        Optional[date]: 日期对象。
    """
    if isinstance(value, date):
        return value
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        parts = text.replace("/", "-").split("-")
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (IndexError, ValueError):
        return None


def percentile_of(series: List[float], current: Optional[float]) -> Optional[float]:
    """计算 current 在 series 中的分位（≤ 当前值的样本占比）。

    Args:
        series: 历史样本（升序或无序均可）。
        current: 当前值。

    Returns:
        Optional[float]: 0–100 的分位；样本不足或当前值缺失返回 None。
    """
    if current is None or not series:
        return None
    clean = [value for value in series if value is not None]
    if len(clean) < 12:
        return None
    below = sum(1 for value in clean if value <= current)
    return round(below / len(clean) * 100, 1)


def _window(records: List[Dict[str, Any]], years: int, today: Optional[date] = None) -> List[float]:
    """取最近 N 年的样本值。

    Args:
        records: [{date, value}]，date 为 date 对象。
        years: 年数。
        today: 基准日，缺省用今天。

    Returns:
        List[float]: 窗口内数值。
    """
    base = today or utils.bj_now().date()
    floor = base - timedelta(days=365 * years + years // 4 * 30)
    values: List[float] = []
    for item in records:
        point = item.get("date")
        value = item.get("value")
        if point is None or value is None:
            continue
        if point >= floor:
            values.append(value)
    return values


def _index_series(symbol: str) -> List[Dict[str, Any]]:
    """拉取单个指数 PE 序列（月频）。

    Args:
        symbol: 指数名（乐咕口径）。

    Returns:
        List[Dict[str, Any]]: [{date, value}] 升序。

    Raises:
        ValueError: 返回为空。
    """
    import akshare as ak

    frame = ak.stock_index_pe_lg(symbol=symbol)
    if frame is None or len(frame) == 0:
        raise ValueError(f"{symbol} 估值序列为空")
    records: List[Dict[str, Any]] = []
    for row in frame.to_dict("records"):
        point = _as_date(row.get("日期"))
        value = _to_float(row.get("滚动市盈率"))
        if point is not None and value is not None and value > 0:
            records.append({"date": point, "value": value})
    if not records:
        raise ValueError(f"{symbol} 估值序列无有效值")
    records.sort(key=lambda item: item["date"])
    return records


def _market_series() -> List[Dict[str, Any]]:
    """全 A 中位数 PE-TTM 序列。

    Returns:
        List[Dict[str, Any]]: [{date, value}] 升序。

    Raises:
        ValueError: 返回为空。
    """
    import akshare as ak

    frame = ak.stock_a_ttm_lyr()
    if frame is None or len(frame) == 0:
        raise ValueError("全 A 估值序列为空")
    records: List[Dict[str, Any]] = []
    for row in frame.to_dict("records"):
        point = _as_date(row.get("date"))
        value = _to_float(row.get("middlePETTM"))
        if point is not None and value is not None and value > 0:
            records.append({"date": point, "value": value})
    if not records:
        raise ValueError("全 A 估值序列无有效值")
    records.sort(key=lambda item: item["date"])
    return records


def _industry_snapshot() -> List[Dict[str, Any]]:
    """行业 PE 单时点快照 + 横截面分位。

    接口要求 `date` 必须是具体交易日（传空串会 KeyError），这里回退最近 10 天取首个有数据的日期。

    Returns:
        List[Dict[str, Any]]: [{name, pe, cross_pct, companies}]，按 PE 升序。

    Raises:
        ValueError: 所有候选日期都取不到数据。
    """
    import akshare as ak

    # 北京时间取基准日：宿主时区非 UTC+8 时，date.today() 会在跨日时段取错日期
    today = utils.bj_now().date()
    frame = None
    used_date = ""
    for offset in range(0, 10):
        candidate = (today - timedelta(days=offset)).strftime("%Y%m%d")
        try:
            frame = ak.stock_industry_pe_ratio_cninfo(symbol="证监会行业分类", date=candidate)
        except Exception:  # noqa: BLE001 - 逐个日期回退
            frame = None
        if frame is not None and len(frame) > 0:
            used_date = candidate
            break
    if frame is None or len(frame) == 0:
        raise ValueError("行业 PE 快照在最近 10 天内均无数据")

    rows: List[Dict[str, Any]] = []
    for row in frame.to_dict("records"):
        name = str(row.get("行业名称") or "").strip()
        pe = _to_float(row.get("静态市盈率-加权平均"))
        if not name or pe is None or pe <= 0:
            continue
        # 该接口同时下发「门类(层级1)」与「细分行业(层级2)」，两者名称会重复
        # （如「房地产业」在两层级都出现），混在一起做横截面分位会导致：
        #   ① 界面上同名行业出现两次；② 门类与细分行业不可比。
        # 只保留层级 2（细分行业），并用公司数量 ≥3 过滤掉统计意义极弱的样本
        # （如「林业」仅 1 家公司，PE 244 会顶到 100% 分位，把整体分位拉歪）。
        level = _to_float(row.get("行业层级"))
        companies = int(_to_float(row.get("公司数量")) or 0)
        if level is not None and abs(level - 2.0) > 1e-6:
            continue
        if companies < 3:
            continue
        rows.append({
            "name": name,
            "pe": round(pe, 2),
            "companies": companies,
        })
    if not rows:
        raise ValueError("行业 PE 快照无有效值")
    # 接口存在「不同行业编码但同名」的情况（实测「其他制造业」两条：38 家 PE 30.84 /
    # 15 家 PE 42.54）。同名两条会让界面重复展示、也会让横截面分位算两次同一行业，
    # 按名称去重并保留公司数较多的一条（样本更全）。
    deduped: Dict[str, Dict[str, Any]] = {}
    for item in rows:
        key = item["name"]
        if key not in deduped or item["companies"] > deduped[key]["companies"]:
            deduped[key] = item
    rows = list(deduped.values())
    rows.sort(key=lambda item: item["pe"])
    total = len(rows)
    for index, item in enumerate(rows):
        item["cross_pct"] = round((index + 1) / total * 100, 1)
        item["snapshot_date"] = f"{used_date[:4]}-{used_date[4:6]}-{used_date[6:8]}"
    return rows


def _bond_10y() -> Optional[float]:
    """取 10Y 国债收益率（复用宏观模块，失败返回 None）。

    注意：`macro.get_overview()` 直接返回 payload（含 indicators），
    不是 `{data: ...}` 包装——这里少取一层曾在首版写成 `.get("data")` 导致 ERP 恒为 None。

    Returns:
        Optional[float]: 收益率（%）。
    """
    try:
        overview = macro.get_overview() or {}
        for item in overview.get("indicators") or []:
            if item.get("key") == "bond_10y" and item.get("available"):
                return float(item["value"])
    except Exception as exc:  # noqa: BLE001 - 取不到就不算 ERP
        logger.info("ERP 取国债失败：%s", type(exc).__name__)
    return None


def _load_percentile() -> Dict[str, Any]:
    """实际取数入口（交给缓存包装）。

    Returns:
        Dict[str, Any]: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/is_realtime。
    """
    bond = _bond_10y()
    indices: List[Dict[str, Any]] = []
    failures: List[str] = []

    for symbol in INDEX_LIST:
        try:
            records = _index_series(symbol)
        except Exception as exc:  # noqa: BLE001 - 单指数失败不影响其他
            logger.info("指数估值失败 %s：%s", symbol, type(exc).__name__)
            failures.append(f"{symbol}（{type(exc).__name__}）")
            indices.append({"name": symbol, "available": False, "reason": f"估值通道异常（{type(exc).__name__}）"})
            continue
        current = records[-1]["value"]
        win3 = _window(records, 3)
        win5 = _window(records, 5)
        win10 = _window(records, 10)
        pct3 = percentile_of(win3, current)
        pct5 = percentile_of(win5, current)
        pct10 = percentile_of(win10, current)
        erp = None
        if bond is not None and current > 0:
            # PE 为倍数，1/PE 得收益率（小数）；bond 为百分数，需同步换算
            erp = round((1 / current) * 100 - bond, 2)
        indices.append({
            "name": symbol,
            "available": True,
            "pe": round(current, 2),
            "period": records[-1]["date"].isoformat(),
            "pct3y": pct3,
            "pct5y": pct5,
            "pct10y": pct10,
            "erp": erp,
            # 样本数用于让用户判断分位可信度（月频：3 年≈36 个月）
            "samples": {"y3": len(win3), "y5": len(win5), "y10": len(win10)},
            "history_from": records[0]["date"].isoformat(),
        })

    market: Dict[str, Any] = {"available": False, "reason": "未取数"}
    try:
        market_records = _market_series()
        current = market_records[-1]["value"]
        market = {
            "name": "全 A 中位数",
            "available": True,
            "pe": round(current, 2),
            "period": market_records[-1]["date"].isoformat(),
            "pct3y": percentile_of(_window(market_records, 3), current),
            "pct5y": percentile_of(_window(market_records, 5), current),
            "pct10y": percentile_of(_window(market_records, 10), current),
            "history_from": market_records[0]["date"].isoformat(),
        }
    except Exception as exc:  # noqa: BLE001 - 全 A 失败不影响指数
        logger.info("全 A 估值失败：%s", type(exc).__name__)
        failures.append(f"全 A（{type(exc).__name__}）")
        market = {"available": False, "reason": f"估值通道异常（{type(exc).__name__}）"}

    industries: List[Dict[str, Any]] = []
    industry_note = ""
    try:
        industries = _industry_snapshot()
    except Exception as exc:  # noqa: BLE001 - 行业失败不影响指数
        logger.info("行业估值失败：%s", type(exc).__name__)
        failures.append(f"行业 PE（{type(exc).__name__}）")
        industry_note = f"行业 PE 通道异常（{type(exc).__name__}）"

    return {
        "indices": indices,
        "market": market,
        "industries": industries,
        "industry_note": industry_note,
        "bond_10y": bond,
        "source": "乐咕乐股（指数/全A）+ 巨潮资讯（行业 PE）+ 中债（10Y）",
        "caliber": (
            "指数与全 A 为月频历史分位（≤ 当前值样本占比，窗口 3/5/10 年）；"
            "行业为单时点快照，给的是横截面分位（该行业 PE 在全部行业中升序排名），"
            "字段名 cross_pct 以区别于纵向分位；ERP = 1/PE×100 − 10Y"
        ),
        "as_of": utils.now_text(utils.bj_now()),
        "is_realtime": False,
        "missing": failures,
        "disclaimer": DISCLAIMER,
    }


def get_percentile(force: bool = False) -> Dict[str, Any]:
    """估值分位总览，6 小时缓存。

    Args:
        force: 为 True 时绕过缓存强制重取。

    Returns:
        Dict[str, Any]: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/is_realtime。
    """
    if force:
        return _load_percentile()
    return cache.get_or_load("valuation:percentile", config.TTL.get("valuation", 21600), _load_percentile)
