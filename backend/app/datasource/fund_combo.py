# -*- coding: utf-8 -*-
"""基金组合（第七轮 M5）：六维打分 / 相关性去重 / 约束配权 / 再平衡。

设计依据 `DS-R7-v1.0` §二。**合规边界**：本模块只做「筛选与诊断」，不做「荐基」——
不输出预期收益、不给单只买入评级、不使用「推荐/最优」措辞。界面须展示免责徽章。

数据来源与已知缺口（全部在返回体 `missing` 里逐条写明，不静默降级）：
- 净值序列：复用 `fund._all_nav_items`（全历史，第六轮已修）
- 同类排名：`fund_open_fund_info_em(indicator="同类排名百分比")`，约 837 期
- 费率：`fund_open_fund_rank_em` 的「手续费」列（按类型批量拉一次并缓存）
- **规模与成立年限不可用**：东财「基金规模」indicator 返回空表、排行榜无规模列 →
  「规模 ≥2 亿 / 成立 ≥3 年」两条硬性排除**未能实现**，改为按「近 1 年 + 近 3 年收益」
  做分位初筛，并在界面提示用户自行确认清盘风险
- **风格稳定性不可用**：需 8 个季度持仓做重合度，公开接口无历史持仓 →
  该维度固定给中位分 50，并在 `missing` 点名
- **拥挤度降权未接入**：需「基金→重仓行业→行业拥挤度」三级映射，成本高，留待 M5b

权重算法（不用均值-方差，理由见设计基准）：w ∝ (1/σ) × score^0.5，
再按「权益上限 / 单只 ≤20% / 现金 ≥20%」迭代裁剪。选它是因为 σ 与 score 都相对稳定，
不会算出「某只 80%」的极端解，且每一步都能向用户解释。
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Dict, List, Optional, Tuple

from backend.app.datasource import fund as fund_provider
from backend.app.datasource import macro, portfolio, utils

logger = logging.getLogger(__name__)

# 六维权重（设计基准 §2.3）
DIMENSIONS: Dict[str, float] = {
    "sharpe": 0.25,
    "calmar": 0.25,
    "drawdown": 0.20,
    "rank": 0.15,
    "style": 0.10,
    "fee": 0.05,
}

# 风险档位 → 权益上限 / 单只上限 / 现金下限（用户 2026-10-31 裁决：现金固定 20%，不可调）
RISK_PROFILES: Dict[str, Dict[str, float]] = {
    "conservative": {"equity_cap": 0.40, "single_cap": 0.15, "cash_floor": 0.20},
    "balanced": {"equity_cap": 0.70, "single_cap": 0.20, "cash_floor": 0.20},
    "aggressive": {"equity_cap": 0.90, "single_cap": 0.20, "cash_floor": 0.20},
}

# 再平衡阈值（设计基准 §2.5）
REBALANCE_SINGLE_PT = 5.0
REBALANCE_CLASS_PT = 8.0

# 费率缓存（按类型批量拉一次，24 小时）
_FEE_CACHE: Dict[str, float] = {}
_FEE_TS = 0.0
_FEE_TTL = 24 * 3600


def _risk_free() -> Optional[float]:
    """无风险利率（10Y 国债，%）：与估值页同源，不硬编码。

    Returns:
        Optional[float]: 收益率百分数；取不到返回 None（夏普/索提诺将退化为 None）。
    """
    try:
        overview = macro.get_overview() or {}
        for item in overview.get("indicators") or []:
            if item.get("key") == "bond_10y" and item.get("available"):
                return float(item["value"])
    except Exception as exc:  # noqa: BLE001 - 取不到就不算风险调整指标
        logger.info("无风险利率取数失败：%s", type(exc).__name__)
    return None


def _load_fee_map() -> Dict[str, float]:
    """批量拉取各类型基金手续费（%），进程内缓存 24 小时。

    Returns:
        Dict[str, float]: {基金代码: 手续费%}；单类失败不影响其他类。
    """
    global _FEE_TS
    now = time.time()
    if _FEE_CACHE and now - _FEE_TS < _FEE_TTL:
        return _FEE_CACHE
    import akshare as ak

    for kind in ("股票型", "混合型", "债券型", "指数型"):
        try:
            frame = ak.fund_open_fund_rank_em(symbol=kind)
        except Exception as exc:  # noqa: BLE001 - 单类失败不阻断
            logger.info("费率拉取失败 %s：%s", kind, type(exc).__name__)
            continue
        for row in frame.to_dict("records"):
            code = str(row.get("基金代码") or "").strip()
            raw = str(row.get("手续费") or "").strip().replace("%", "")
            if not code or not raw:
                continue
            try:
                _FEE_CACHE[code] = float(raw)
            except ValueError:
                continue
    _FEE_TS = now
    return _FEE_CACHE


def _rank_percentile(code: str) -> Optional[float]:
    """近 3 年同类排名分位（越小越好），取最近 120 期的均值。

    Args:
        code: 基金代码。

    Returns:
        Optional[float]: 0–100 的平均排名百分位；取不到返回 None。
    """
    import akshare as ak

    try:
        frame = ak.fund_open_fund_info_em(symbol=code, indicator="同类排名百分比")
    except Exception as exc:  # noqa: BLE001 - 排名缺失不阻断打分
        logger.info("同类排名取数失败 %s：%s", code, type(exc).__name__)
        return None
    column = next((name for name in frame.columns if "排名" in str(name)), None)
    if column is None or not len(frame):
        return None
    values: List[float] = []
    for value in frame[column].tail(120).tolist():
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if 0 <= number <= 100:
            values.append(number)
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _stats_from_nav(code: str) -> Tuple[Dict[str, Any], Optional[List[Dict[str, Any]]]]:
    """由净值序列计算收益与风险指标。

    Args:
        code: 基金代码。

    Returns:
        tuple: (指标字典, 净值序列)；取数失败返回 ({}, None)。
    """
    try:
        payload = fund_provider._all_nav_items(code)  # noqa: SLF001 - 同包复用全历史取数
    except Exception as exc:  # noqa: BLE001 - 单只失败不阻断组合
        logger.info("净值取数失败 %s：%s", code, type(exc).__name__)
        return {}, None
    items = [item for item in payload.get("items") or [] if item.get("nav")]
    if len(items) < 60:
        return {}, items
    items.sort(key=lambda item: item.get("date") or "")
    navs = [float(item["nav"]) for item in items]
    dates = [str(item.get("date")) for item in items]

    # 日收益序列（用于波动率，缺一天就跳过该点，不插值）
    returns: List[float] = []
    for index in range(1, len(navs)):
        if navs[index - 1] > 0:
            returns.append(navs[index] / navs[index - 1] - 1)

    # 取最近 3 年（约 750 个交易日）作为打分窗口，与同类排名口径对齐
    window = min(len(navs), 750)
    navs_w = navs[-window:]
    rets_w = returns[-window:]

    years = len(navs_w) / 252.0
    total_return = navs_w[-1] / navs_w[0] - 1 if navs_w[0] > 0 else None
    annual = ((1 + total_return) ** (1 / years) - 1) if (total_return is not None and years > 0) else None
    if annual is not None and annual <= -1:
        annual = None

    mean = sum(rets_w) / len(rets_w) if rets_w else 0.0
    variance = sum((value - mean) ** 2 for value in rets_w) / (len(rets_w) - 1) if len(rets_w) > 1 else 0.0
    vol = math.sqrt(variance) * math.sqrt(252)

    downside = [min(0.0, value) for value in rets_w]
    d_var = sum(value ** 2 for value in downside) / (len(downside) - 1) if len(downside) > 1 else 0.0
    d_vol = math.sqrt(d_var) * math.sqrt(252)

    peak = navs_w[0]
    max_dd = 0.0
    dd_series: List[float] = []
    for value in navs_w:
        peak = max(peak, value)
        dd = value / peak - 1 if peak > 0 else 0.0
        dd_series.append(dd)
        max_dd = min(max_dd, dd)

    # 找最大回撤的谷底日（用于展示「什么时候跌的」）
    trough_index = dd_series.index(max_dd) if dd_series else 0
    trough_date = dates[-window:][trough_index] if dates else None

    rf = _risk_free()
    sharpe = None
    if rf is not None and annual is not None and vol > 0:
        sharpe = (annual * 100 - rf) / (vol * 100)
    sortino = None
    if rf is not None and annual is not None and d_vol > 0:
        sortino = (annual * 100 - rf) / (d_vol * 100)
    calmar = None
    if annual is not None and max_dd < 0:
        calmar = annual / abs(max_dd)

    stats = {
        "sample_days": len(navs_w),
        "from": dates[-window] if dates else None,
        "to": dates[-1] if dates else None,
        "annual_return": round(annual * 100, 2) if annual is not None else None,
        "volatility": round(vol * 100, 2) if vol else None,
        "max_drawdown": round(max_dd * 100, 2),
        "trough_date": trough_date,
        "sharpe": round(sharpe, 3) if sharpe is not None else None,
        "sortino": round(sortino, 3) if sortino is not None else None,
        "calmar": round(calmar, 3) if calmar is not None else None,
        "nav_series": [{"date": dates[-window:][i], "nav": navs_w[i]} for i in range(0, len(navs_w), max(1, len(navs_w) // 120))],
    }
    return stats, items


# 绝对分档表：样本 <3 时 min-max 无意义（1 只必然得 50 分），改用绝对阈值。
# 阈值取自国内基金评价的常见口径（夏普>1 为优、卡玛>1.2 为优、回撤<20% 为可控）。
_ABSOLUTE_BANDS: Dict[str, List[Tuple[float, float]]] = {
    "sharpe": [(1.5, 90), (1.0, 80), (0.5, 65), (0.0, 50), (float("-inf"), 30)],
    "calmar": [(1.2, 90), (0.8, 80), (0.4, 65), (0.0, 50), (float("-inf"), 30)],
    "drawdown": [(-0.10, 90), (-0.20, 80), (-0.35, 65), (-0.50, 50), (float("-inf"), 30)],
    "rank": [(10, 90), (30, 80), (50, 65), (70, 50), (float("inf"), 30)],
    "fee": [(0.10, 90), (0.50, 80), (1.00, 65), (1.50, 50), (float("inf"), 30)],
}


def _score_absolute(key: str, value: Optional[float]) -> Optional[float]:
    """按绝对阈值给分（用于候选池少于 3 只的场景）。

    Args:
        key: 维度键（sharpe/calmar/drawdown/rank/fee）。
        value: 原始值。

    Returns:
        Optional[float]: 0–100 分；无阈值表或缺值返回 None。
    """
    bands = _ABSOLUTE_BANDS.get(key)
    if bands is None or value is None:
        return None
    for threshold, score in bands:
        if key == "rank":
            if value <= threshold:
                return float(score)
        elif key == "fee":
            if value <= threshold:
                return float(score)
        elif value >= threshold:
            return float(score)
    return 30.0


def _normalize(values: List[Optional[float]], higher_better: bool = True) -> List[Optional[float]]:
    """在候选池内做 min-max 归一（0–100），缺值返回 None。

    Args:
        values: 原始值列表。
        higher_better: True 表示数值越大越好。

    Returns:
        List[Optional[float]]: 归一后的分数。
    """
    clean = [value for value in values if value is not None]
    if not clean:
        return [None] * len(values)
    low, high = min(clean), max(clean)
    if high - low < 1e-12:
        return [50.0 if value is not None else None for value in values]
    result: List[Optional[float]] = []
    for value in values:
        if value is None:
            result.append(None)
        else:
            ratio = (value - low) / (high - low)
            result.append(round((ratio if higher_better else 1 - ratio) * 100, 1))
    return result


def evaluate(codes: List[str], risk_profile: str = "balanced") -> Dict[str, Any]:
    """批量评估基金并给出六维打分、相关性、目标权重（不涉及用户持仓）。

    Args:
        codes: 基金代码列表（建议 ≤30 只，每只 1–2 次上游请求）。
        risk_profile: conservative / balanced / aggressive。

    Returns:
        dict: {items, correlation, weights, constraints, missing, caliber, disclaimer}

    Raises:
        ValueError: 未提供代码。
    """
    unique: List[str] = []
    for code in codes:
        text = str(code or "").strip()
        if text and text not in unique:
            unique.append(text)
    if not unique:
        raise ValueError("请至少提供 1 只基金代码")
    if len(unique) > 30:
        unique = unique[:30]
    profile = RISK_PROFILES.get(risk_profile) or RISK_PROFILES["balanced"]
    fee_map = _load_fee_map()

    items: List[Dict[str, Any]] = []
    missing: List[str] = []
    nav_map: Dict[str, Dict[str, float]] = {}

    for code in unique:
        stats, nav_items = _stats_from_nav(code)
        if not stats:
            missing.append(f"{code}（净值序列不可用）")
            continue
        rank = _rank_percentile(code)
        if rank is None:
            missing.append(f"{code}（同类排名不可用）")
        fee = fee_map.get(code)
        if fee is None:
            missing.append(f"{code}（费率不可用）")
        if nav_items:
            nav_map[code] = {str(row.get("date")): float(row["nav"])
                             for row in nav_items if row.get("nav")}
        items.append({
            "code": code,
            "name": "",
            "stats": {key: value for key, value in stats.items() if key != "nav_series"},
            "series": stats.get("nav_series") or [],
            "rank_pct": rank,
            "fee": fee,
            "style_score": 50.0,  # 风格稳定性数据不可得，固定中位（见 missing）
        })

    if not items:
        return {
            "items": [],
            "correlation": [],
            "weights": [],
            "constraints": profile,
            "missing": missing + ["全部基金净值都取不到"],
            "caliber": "无可用样本，未计算打分与权重",
            "disclaimer": "本页仅做筛选与结构诊断，不构成任何投资建议。",
        }

    # —— 六维打分 ——
    # 候选池 ≥3 只时用池内 min-max（相对排名更有意义）；
    # <3 只时改用绝对阈值分档，否则单只/min-max 恒等会使所有维度都得 50 分。
    use_absolute = len(items) < 3
    if use_absolute:
        shares = [_score_absolute("sharpe", item["stats"].get("sharpe")) for item in items]
        calmar_s = [_score_absolute("calmar", item["stats"].get("calmar")) for item in items]
        dd_s = [_score_absolute("drawdown", item["stats"].get("max_drawdown")) for item in items]
        rank_s = [_score_absolute("rank", item["rank_pct"]) for item in items]
        fee_s = [_score_absolute("fee", item["fee"]) for item in items]
    else:
        shares = _normalize([item["stats"].get("sharpe") for item in items], True)
        calmar_s = _normalize([item["stats"].get("calmar") for item in items], True)
        dd_s = _normalize([item["stats"].get("max_drawdown") for item in items], True)  # 回撤是负数，越接近 0 越好
        rank_s = _normalize([item["rank_pct"] for item in items], False)  # 排名分位越小越好
        fee_s = _normalize([item["fee"] for item in items], False)
    style_s = [item["style_score"] for item in items]

    for index, item in enumerate(items):
        parts = {
            "sharpe": shares[index],
            "calmar": calmar_s[index],
            "drawdown": dd_s[index],
            "rank": rank_s[index],
            "style": style_s[index],
            "fee": fee_s[index],
        }
        total = 0.0
        used = 0.0
        absent: List[str] = []
        for key, weight in DIMENSIONS.items():
            score = parts.get(key)
            if score is None:
                absent.append(key)
                continue
            total += weight * score
            used += weight
        item["parts"] = {key: (round(value, 1) if value is not None else None) for key, value in parts.items()}
        item["score"] = round(total / used, 1) if used else None
        item["score_missing"] = absent

    # —— 相关性（用净值日期交集，最少 60 个共同点）——
    codes_ok = [item["code"] for item in items]
    correlation: List[Dict[str, Any]] = []
    for i, left in enumerate(codes_ok):
        for right in codes_ok[i + 1:]:
            common = sorted(set(nav_map.get(left, {})) & set(nav_map.get(right, {})))
            if len(common) < 60:
                correlation.append({"left": left, "right": right, "value": None,
                                    "note": "共同交易日不足 60 个"})
                continue
            left_returns, right_returns = [], []
            for index in range(1, len(common)):
                a, b = nav_map[left][common[index - 1]], nav_map[left][common[index]]
                c, d = nav_map[right][common[index - 1]], nav_map[right][common[index]]
                if a > 0 and c > 0:
                    left_returns.append(b / a - 1)
                    right_returns.append(d / c - 1)
            if len(left_returns) < 30:
                correlation.append({"left": left, "right": right, "value": None,
                                    "note": "有效收益点不足 30 个"})
                continue
            correlation.append({"left": left, "right": right,
                                "value": round(_pearson(left_returns, right_returns), 3),
                                "note": None})

    # —— 权重：逆波动率 × 得分^0.5，再按约束裁剪 ——
    weights = _allocate(items, profile)
    return {
        "items": items,
        "correlation": correlation,
        "weights": weights,
        "constraints": profile,
        "missing": missing,
        "caliber": (
            ("六维打分=绝对阈值分档后加权（候选池 <3 只，min-max 会使单只恒得 50 分）；"
             if use_absolute else
             "六维打分=池内 min-max 归一后加权；") +
            "（夏普25/卡玛25/回撤20/排名分位15/风格10/费率5），"
            "风格稳定性因无历史持仓数据固定给 50；"
            "权重=逆波动率×得分^0.5，再按权益上限/单只上限/现金下限迭代裁剪；"
            "相关性用净值日收益的皮尔逊系数（共同交易日≥60）"
        ),
        "disclaimer": "本页仅做筛选与结构诊断，不构成任何投资建议；打分高低不代表未来收益。",
    }


def _pearson(left: List[float], right: List[float]) -> float:
    """皮尔逊相关系数。

    Args:
        left: 序列 A。
        right: 序列 B（等长）。

    Returns:
        float: 相关系数，范围 -1~1；分母为 0 时返回 0。
    """
    n = len(left)
    mean_l = sum(left) / n
    mean_r = sum(right) / n
    cov = sum((left[i] - mean_l) * (right[i] - mean_r) for i in range(n))
    var_l = sum((left[i] - mean_l) ** 2 for i in range(n))
    var_r = sum((right[i] - mean_r) ** 2 for i in range(n))
    if var_l <= 0 or var_r <= 0:
        return 0.0
    return cov / math.sqrt(var_l * var_r)


def _allocate(items: List[Dict[str, Any]], profile: Dict[str, float]) -> List[Dict[str, Any]]:
    """按「逆波动率 × 得分^0.5」生成目标权重，并迭代裁剪到满足约束。

    约束口径（全部按**总资产**计，不是按可投资金计）：
      - 单只 ≤ single_cap（balanced 为 20%）
      - 基金合计 ≤ min(1 − cash_floor, equity_cap)
      - 未分配部分即为现金/其他，不做强制满仓

    裁剪顺序：先把超上限的压到上限，把多出的额度按比例分给未触顶的；全触顶后
    剩余额度留作现金。**最后不再做「归一化到 100%」**——那会把刚压下去的单只
    重新放大回上限之上（首版就是在这里把 20% 算成了 26.67%）。

    Args:
        items: 已打分的基金条目。
        profile: 风险档位（equity_cap / single_cap / cash_floor）。

    Returns:
        List[Dict[str, Any]]: [{code, raw, weight, capped_by}]
    """
    usable = [item for item in items if item.get("stats", {}).get("volatility") and item.get("score")]
    if not usable:
        return []

    single_cap = profile["single_cap"]
    budget = min(1.0 - profile["cash_floor"], profile["equity_cap"])

    raw: Dict[str, float] = {}
    for item in usable:
        vol = max(float(item["stats"]["volatility"]), 1.0)
        raw[item["code"]] = (1.0 / vol) * math.sqrt(item["score"])
    total_raw = sum(raw.values())
    weights = {code: value / total_raw * budget for code, value in raw.items()}

    capped: Dict[str, str] = {}
    for _ in range(20):
        over = {code: weight for code, weight in weights.items() if weight > single_cap + 1e-12}
        if not over:
            break
        excess = sum(weight - single_cap for weight in over.values())
        for code in over:
            weights[code] = single_cap
            capped[code] = f"触及单只上限 {int(single_cap * 100)}%"
        free = {code: weight for code, weight in weights.items() if weight < single_cap - 1e-12}
        free_total = sum(free.values())
        if free_total <= 1e-12:
            break
        for code in free:
            weights[code] += excess * (free[code] / free_total)

    total = sum(weights.values())
    if total > 1e-12:
        # 总额不得超过预算；超出部分按比例缩减（缩到预算内，不再放大）
        scale = min(1.0, budget / total)
        weights = {code: weight * scale for code, weight in weights.items()}

    return [
        {
            "code": code,
            "raw": round(raw[code], 4),
            "weight": round(weight * 100, 2),
            "capped_by": capped.get(code),
        }
        for code, weight in weights.items()
    ]


def diagnose(risk_profile: str = "balanced") -> Dict[str, Any]:
    """诊断用户台账里的基金持仓：目标权重 vs 实际权重、相关性、偏离。

    实际权重来自 `portfolio.load()`（唯一台账），不另存数据。

    Args:
        risk_profile: 风险档位。

    Returns:
        dict: {holdings, correlation, target, deviation, constraints, missing, caliber, disclaimer}
    """
    store = portfolio.load()
    funds = [item for item in store.get("items") or []
             if str(item.get("market") or "") == "fund"]
    if not funds:
        return {
            "holdings": [],
            "correlation": [],
            "target": [],
            "deviation": [],
            "constraints": RISK_PROFILES.get(risk_profile) or RISK_PROFILES["balanced"],
            "missing": ["台账里没有基金持仓（组合页先添加，或从养基宝同步）"],
            "caliber": "无基金持仓，未计算",
            "disclaimer": "本页仅做结构诊断，不构成任何投资建议。",
        }

    codes = [str(item.get("symbol")) for item in funds]
    base = evaluate(codes, risk_profile)
    # 用真实市值替代等权：实际权重 = 该基金市值 / 基金合计市值
    value_map: Dict[str, float] = {}
    for item in funds:
        code = str(item.get("symbol"))
        price, _time, _source = portfolio._price_of(item)  # noqa: SLF001 - 复用同一取价口径
        shares = float(item.get("shares") or 0)
        if price is not None and shares:
            value_map[code] = price * shares
    total_value = sum(value_map.values())

    holdings: List[Dict[str, Any]] = []
    for entry in base.get("items") or []:
        code = entry["code"]
        actual = (value_map.get(code, 0.0) / total_value * 100) if total_value else 0.0
        holdings.append({**entry, "actual_weight": round(actual, 2)})

    target_map = {row["code"]: row["weight"] for row in base.get("weights") or []}
    deviation: List[Dict[str, Any]] = []
    for row in holdings:
        target = target_map.get(row["code"], 0.0)
        gap = round(row["actual_weight"] - target, 2)
        if abs(gap) >= REBALANCE_SINGLE_PT:
            deviation.append({
                "code": row["code"],
                "actual": row["actual_weight"],
                "target": target,
                "gap": gap,
                "action": "高于目标，需降低" if gap > 0 else "低于目标，需补充",
            })

    floor = (RISK_PROFILES.get(risk_profile) or RISK_PROFILES["balanced"])["cash_floor"]
    cash_note = None
    if total_value:
        # 台账只有基金，无法得知现金余额，因此只提示"未记录现金"，不臆造占比
        cash_note = f"台账未记录现金类资产，无法判断是否满足现金下限 {int(floor * 100)}%"

    high_corr = [row for row in base.get("correlation") or []
                 if row.get("value") is not None and row["value"] > 0.90]

    return {
        "holdings": holdings,
        "correlation": base.get("correlation") or [],
        "target": base.get("weights") or [],
        "deviation": deviation,
        "high_corr": high_corr,
        "cash_note": cash_note,
        "constraints": base.get("constraints"),
        "missing": base.get("missing") or [],
        "caliber": base.get("caliber"),
        "disclaimer": "本页仅做结构诊断，不构成任何投资建议；偏离是客观差值，调不调由你决定。",
        "as_of": utils.now_text(utils.bj_now()),
    }


# =============================================================
# 第七轮 M14：拥挤度降权（在基础权重上乘拥挤度折扣因子）
# =============================================================
CROWD_DISCOUNT_TIERS = (
    (60.0, 1.00, "拥挤度 <60，不折扣"),
    (80.0, 0.85, "拥挤度 60–79，轻度折扣 ×0.85"),
    (100.1, 0.60, "拥挤度 80–100，重度折扣 ×0.60"),
)

_THS_BOARD_CACHE: Dict[str, Any] = {"boards": [], "ts": 0.0}
_THS_BOARD_TTL = 3600.0


def _ths_boards() -> List[str]:
    """同花顺行业名清单（进程内缓存 1 小时，用于东财行业 → 同花顺板块匹配）。

    Returns:
        List[str]: 行业名列表；取不到返回空列表。
    """
    import time as _time

    now = _time.time()
    if _THS_BOARD_CACHE["boards"] and now - _THS_BOARD_CACHE["ts"] <= _THS_BOARD_TTL:
        return _THS_BOARD_CACHE["boards"]
    try:
        from backend.app.datasource import sector as sector_provider

        names = sector_provider.get_crowding_board_names()
        _THS_BOARD_CACHE["boards"] = names
        _THS_BOARD_CACHE["ts"] = now
        return names
    except Exception as exc:  # noqa: BLE001 - 清单取不到时退化为空（不折扣并标注）
        logger.info("同花顺行业清单取数失败：%s", type(exc).__name__)
        return []


def _match_board(industry: Optional[str]) -> Optional[str]:
    """把东财行业名匹配到同花顺板块名（去罗马数字后缀 → 精确 → 子串）。

    Args:
        industry: 东财行业名（如「银行Ⅱ」「白酒Ⅱ」）。

    Returns:
        Optional[str]: 同花顺板块名；匹配不到返回 None（不猜测）。
    """
    if not industry:
        return None
    boards = _ths_boards()
    if not boards:
        return None
    text = str(industry).strip()
    # 东财行业可能是三级路径（如「食品饮料-饮料-白酒」）：按 - 拆分，从最后一段往前逐段匹配
    segments = [segment.strip() for segment in text.replace("/", "-").split("-") if segment.strip()]
    candidates: List[str] = []
    for segment in reversed(segments):
        candidates.append(segment)
        # 去罗马数字后缀（Ⅰ Ⅱ Ⅲ 等）
        trimmed = segment.rstrip("ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ").strip()
        if trimmed and trimmed != segment:
            candidates.append(trimmed)
    candidates.append(text)
    for candidate in candidates:
        if candidate in boards:
            return candidate
    # 最后尝试子串匹配（长的先试，避免「银行」误配「银行Ⅱ」之类）
    for candidate in candidates:
        if len(candidate) < 2:
            continue
        for board in sorted(boards, key=len, reverse=True):
            if candidate in board or board in candidate:
                return board
    return None


def _discount_of(score: Optional[float]) -> Tuple[Optional[float], str]:
    """拥挤度 → 折扣因子。

    Args:
        score: 拥挤度 0–100；None 表示不可得。

    Returns:
        Tuple[Optional[float], str]: (折扣因子, 说明)；不可得时为 (None, 原因)。
    """
    if score is None:
        return None, "拥挤度不可得，未折扣"
    for upper, factor, label in CROWD_DISCOUNT_TIERS:
        if score < upper:
            return factor, label
    return 0.60, "拥挤度 80–100，重度折扣 ×0.60"


def get_fund_crowding(code: str) -> Dict[str, Any]:
    """基金自身拥挤度（第七轮 M14 补齐，替代「重仓行业代理」）。

    从基金**自身净值历史**计算三维度分位（不依赖重仓股行业）：
    ① 近 60 日年化波动率 → 对自身近 250 日历史的分位；
    ② 近 20 日动量（净值涨幅）→ 分位；
    ③ 近 60 日最大回撤 → 分位（回撤越大越「拥挤/过热」）。
    三维等权加权成 0–100 分，>60 提示、>80 复核，与 M3 股板块拥挤度同口径。

    净值源：蛋卷 ``/djapi/fund/nav/history``（size≤500，一次取够 260 日）→ 东财 lsjz 兜底。
    样本不足或净值源不可用时返回 available=false + 中文说明（不冒充分数）。

    Args:
        code: 基金代码。

    Returns:
        Dict[str, Any]: {code, score, level, parts, sample_days, available, caliber, as_of, missing}
    """
    from backend.app.datasource import fund as fund_provider

    try:
        nav = fund_provider.get_nav(code, page=1, size=500)
    except Exception as exc:  # noqa: BLE001 - 净值源不可用，如实标不可得
        return {
            "code": code, "score": None, "available": False,
            "level": "不可得", "parts": [], "sample_days": 0,
            "caliber": f"基金自身拥挤度不可得（净值源异常：{type(exc).__name__}），未折扣",
            "as_of": utils.now_text(utils.bj_now()),
            "missing": [f"净值源不可得（{type(exc).__name__}）"],
        }
    items = nav.get("items") or []
    rows = []
    for item in items:
        d = str(item.get("date") or "")[:10]
        v = utils.to_float(item.get("nav"))
        if d and v:
            rows.append({"date": d, "nav": v})
    rows.sort(key=lambda r: r["date"])
    if len(rows) < 120:
        return {
            "code": code, "score": None, "available": False,
            "level": "不可得", "parts": [], "sample_days": len(rows),
            "caliber": f"净值样本不足（{len(rows)} < 120 日），无法计算自身拥挤度，未折扣",
            "as_of": utils.now_text(utils.bj_now()),
            "missing": [f"净值样本不足（{len(rows)} 日）"],
        }

    navs = [r["nav"] for r in rows]
    # 日收益率
    rets = []
    for i in range(1, len(navs)):
        prev = navs[i - 1]
        if prev:
            rets.append(navs[i] / prev - 1)
    if len(rets) < 119:
        return {
            "code": code, "score": None, "available": False,
            "level": "不可得", "parts": [], "sample_days": len(rows),
            "caliber": "收益率序列不足，无法计算自身拥挤度，未折扣",
            "as_of": utils.now_text(utils.bj_now()),
            "missing": ["收益率序列不足"],
        }

    # 近 60 日年化波动率（历史全序列做分母样本）
    window_60 = rets[-60:]
    vol_now = (sum(r * r for r in window_60) / len(window_60)) ** 0.5 * (252 ** 0.5)
    vol_hist = []
    for i in range(60, len(rets)):
        w = rets[i - 59: i + 1]
        vol_hist.append((sum(r * r for r in w) / len(w)) ** 0.5 * (252 ** 0.5))

    # 近 20 日动量
    mom_now = navs[-1] / navs[-21] - 1 if len(navs) > 21 else None
    mom_hist = []
    for i in range(21, len(navs)):
        mom_hist.append(navs[i] / navs[i - 21] - 1)

    # 近 60 日最大回撤
    dd_now = _max_drawdown(navs[-60:])
    dd_hist = []
    for i in range(60, len(navs)):
        dd_hist.append(_max_drawdown(navs[i - 59: i + 1]))

    def _rank(hist, current, higher_is_worse=True):
        """把当前值换算成 0–100 分位。

        Args:
            hist: 历史样本序列。
            current: 当前值。
            higher_is_worse: 值越大是否越“拥挤”。

        Returns:
            Optional[float]: 分位；样本不足 30 个返回 None。
        """
        if current is None or len(hist) < 30:
            return None
        if higher_is_worse:
            below = sum(1 for v in hist if v >= current)
        else:
            below = sum(1 for v in hist if v <= current)
        return round(below / len(hist) * 100, 1)

    # 波动率与回撤：值越大越拥挤（higher_is_worse=True）；动量：值越大越热→也视为拥挤
    p_vol = _rank(vol_hist, vol_now, True)
    p_mom = _rank(mom_hist, mom_now, True)
    p_dd = _rank(dd_hist, dd_now, True)

    parts = {
        "volatility": {"label": "近60日波动率(年化)", "value": round(vol_now * 100, 2),
                       "pct": p_vol, "weight": 1 / 3, "unit": "%"},
        "momentum": {"label": "近20日动量", "value": round((mom_now or 0) * 100, 2),
                     "pct": p_mom, "weight": 1 / 3, "unit": "%"},
        "max_drawdown": {"label": "近60日最大回撤", "value": round((dd_now or 0) * 100, 2),
                         "pct": p_dd, "weight": 1 / 3, "unit": "%"},
    }

    missing_parts = []
    score = 0.0
    used = 0.0
    for key, part in parts.items():
        pct = part["pct"]
        if pct is None:
            missing_parts.append(part["label"])
            continue
        score += part["weight"] * pct
        used += part["weight"]
    if used <= 0:
        return {
            "code": code, "score": None, "available": False, "level": "不可得",
            "parts": [], "sample_days": len(rows),
            "caliber": "三维分位均不可用，无法计算自身拥挤度，未折扣",
            "as_of": utils.now_text(utils.bj_now()),
            "missing": missing_parts,
        }
    score = round(score / used, 1)
    if score >= 80:
        level, advice = "高位", "基金自身交易结构拥挤，建议复核"
    elif score >= 60:
        level, advice = "偏高", "自身拥挤度进入提示区间"
    else:
        level, advice = "正常", "自身拥挤度处于常规区间"

    return {
        "code": code, "score": score, "available": True, "level": level, "advice": advice,
        "parts": list(parts.values()), "sample_days": len(rows),
        "caliber": (
            f"基金自身拥挤度=三维分位等权（波动率 {int(parts['volatility']['weight']*100)}% / "
            f"动量 {int(parts['momentum']['weight']*100)}% / 最大回撤 {int(parts['max_drawdown']['weight']*100)}%），"
            f"分位窗口=自身近 {len(rows)} 个交易日净值；"
            "只陈述自身交易结构位置，不构成买卖建议"
        ),
        "as_of": utils.now_text(utils.bj_now()),
        "missing": [f"分位不可用：{'、'.join(missing_parts)}"] if missing_parts else [],
    }


def _max_drawdown(navs: List[float]) -> Optional[float]:
    """计算净值序列的最大回撤。

    Args:
        navs: 按时间升序的净值序列。

    Returns:
        Optional[float]: 最大回撤（0–1 小数）；空序列返回 0.0。
    """
    peak = None
    max_dd = 0.0
    for v in navs:
        if peak is None or v > peak:
            peak = v
        if peak:
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


def weight_breakdown(
    codes: List[str],
    risk_profile: str = "balanced",
    *,
    crowd_discount: bool = True,
) -> Dict[str, Any]:
    """权重明细：基础权重 × 拥挤度折扣 → 最终权重（第七轮 M14）。

    拥挤度口径：优先取基金**自身拥挤度**（M14 补齐，从净值历史算）；
    自身不可得时回退到「第一大重仓股所属行业」的同花顺板块分数（调 M3 同一函数），
    回退情形在 `caliber` 与逐条 `note` 中如实写明。

    Args:
        codes: 基金代码列表。
        risk_profile: 风险档位（conservative / balanced / aggressive）。
        crowd_discount: 是否启用拥挤度折扣。

    Returns:
        Dict[str, Any]: 含 items（逐只基础/折扣/最终权重）、totals（base/final/cash）、
        missing、caliber、disclaimer、as_of。
    """

    base = evaluate(codes, risk_profile)
    weights = {row["code"]: row for row in base.get("weights") or []}
    items = base.get("items") or []
    if not items:
        return {
            "items": [],
            "totals": {"base": 0.0, "final": 0.0, "cash": 100.0},
            "missing": base.get("missing") or ["无可打分的基金"],
            "caliber": "无有效基金，无法计算权重",
            "disclaimer": base.get("disclaimer"),
            "as_of": utils.now_text(utils.bj_now()),
        }

    profile = RISK_PROFILES.get(risk_profile) or RISK_PROFILES["balanced"]
    single_cap = float(profile["single_cap"])
    budget = min(1.0 - float(profile["cash_floor"]), float(profile["equity_cap"]))

    from backend.app.datasource import fund as fund_provider
    from backend.app.datasource import portfolio as portfolio_provider
    from backend.app.datasource import sector as sector_provider

    missing: List[str] = []
    rows: List[Dict[str, Any]] = []
    crowd_cache: Dict[str, Optional[float]] = {}

    for item in items:
        code = item.get("code") or ""
        base_weight = float((weights.get(code) or {}).get("weight") or 0.0)
        board: Optional[str] = None
        score: Optional[float] = None
        crowd_source = "自身"
        note = ""
        if crowd_discount and base_weight > 0:
            # M14 补齐：优先取基金**自身拥挤度**（净值历史算）；不可得再回退重仓行业代理
            try:
                fc = get_fund_crowding(code)
                if fc.get("available") and fc.get("score") is not None:
                    score = fc["score"]
                    crowd_source = "自身"
                else:
                    # 回退到第一大重仓股所属行业的同花顺板块分数（原代理口径）
                    holding = fund_provider.get_holding(code)
                    top = (holding.get("items") or [{}])[0]
                    top_code = str(top.get("code") or top.get("symbol") or "")
                    industry = portfolio_provider._industry_of(top_code, "a")
                    board = _match_board(industry)
                    crowd_source = "重仓行业代理"
                    if not board:
                        note = f"自身拥挤度不可得（{fc.get('caliber', '净值样本不足')}），" \
                               f"且重仓行业「{industry or '未知'}」未匹配到同花顺板块，未折扣"
                    elif board in crowd_cache:
                        score = crowd_cache[board]
                    else:
                        score = sector_provider.get_crowding(board).get("score")
                        crowd_cache[board] = score
                    if score is None:
                        note = (note + "；" if note else "") + "行业拥挤度取数失败，未折扣"
            except Exception as exc:  # noqa: BLE001 - 单只失败不影响整体
                note = f"拥挤度取数失败（{type(exc).__name__}），未折扣"
        factor, factor_label = _discount_of(score) if crowd_discount else (1.00, "未启用拥挤度降权")
        applied = float(factor or 1.0)
        rows.append({
            "code": code,
            "name": item.get("name") or code,
            "base_weight": round(base_weight, 2),
            "board": board,
            "crowding": round(score, 1) if score is not None else None,
            "crowding_source": crowd_source,
            "discount": applied,
            "discount_label": factor_label,
            "final_weight": round(base_weight * applied, 4),
            "note": note,
        })

    # 基金部分重新归一化到「基础权重合计」，再按单只上限裁剪
    base_total = sum(row["base_weight"] for row in rows)
    target_total = base_total if base_total > 0 else budget * 100
    discounted_total = sum(row["final_weight"] for row in rows)
    if discounted_total > 1e-9:
        scale = target_total / discounted_total
        for row in rows:
            row["final_weight"] = row["final_weight"] * scale

    # 单只上限裁剪（超出部分留现金，不放大其他基金）
    capped: List[str] = []
    cap_pct = single_cap * 100
    for row in rows:
        if row["final_weight"] > cap_pct + 1e-9:
            row["final_weight"] = cap_pct
            capped.append(row["code"])

    for row in rows:
        row["final_weight"] = round(row["final_weight"], 2)
        row["delta"] = round(row["final_weight"] - row["base_weight"], 2)
        if row["code"] in capped:
            row["note"] = (row["note"] + "；" if row["note"] else "") + f"折扣后触及单只上限 {int(cap_pct)}%"

    final_total = sum(row["final_weight"] for row in rows)
    undiscounted = [row["code"] for row in rows if row["discount"] >= 0.999 and row["base_weight"] > 0]
    if undiscounted:
        missing.append(f"未折扣（拥挤度不可得或未启用）：{'、'.join(undiscounted)}")
    for row in rows:
        if row["note"]:
            missing.append(f"{row['code']}：{row['note']}")

    return {
        "items": rows,
        "totals": {
            "base": round(base_total, 2),
            "final": round(final_total, 2),
            "cash": round(100 - final_total, 2),
            "capped": capped,
        },
        "missing": missing,
        "caliber": (
            "最终权重 = 基础权重（逆波动率 × 得分）× 拥挤度折扣因子；"
            "折扣档：<60 ×1.00 / 60–79 ×0.85 / 80–100 ×0.60；"
            "折后基金部分内部归一化到原基础权重合计，再按单只上限裁剪，余额留现金；"
            "拥挤度优先取基金**自身拥挤度**（M14 补齐：近60日波动率/近20日动量/近60日最大回撤"
            "三维分位等权，源=自身净值历史），自身不可得时回退到「第一大重仓股所属行业」的"
            "同花顺板块分数（crowding_source 字段逐只标注）；"
            "折扣是风险控制手段，不是收益预期"
        ),
        "disclaimer": base.get("disclaimer"),
        "as_of": utils.now_text(utils.bj_now()),
    }
