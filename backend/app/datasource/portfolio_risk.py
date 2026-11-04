"""M10 · 组合风险指标（年化波动 / 最大回撤 / 夏普 / 詹森 α / 基准 β / 相关性矩阵）。

口径（设计基准 §12.1）：
- **按当前持仓权重回溯**：用当前市值权重 × 各标的历史日收益合成组合日收益序列，
  属于「当前结构的历史表现」，**不是**真实逐日持仓净值（台账无逐日快照）。
- 基准：沪深300（000300），取腾讯指数日线。
- 无风险利率：优先取 M1 的 10Y 国债（macro），失败回退 2.0%（标注）。
- 现金仓位按 0 波动计入（用持仓市值 / 台账总资产的口径差表达）；本模块只用有日线的标的，
  场外基金无日线 → 不纳入序列并如实登记 `missing`。
- 样本 < 30 个交易日 → `reliable=false` + 「样本不足，指标仅供参考」，不给结论标签。
- 只做历史特征陈述，**不预测未来收益**。

数据流：portfolio.load() → 每标的 tencent.get_kline(250) → 日期对齐（基准交易日为轴，前值填充）
→ 组合日收益 → 指标计算。
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

from backend.app.datasource import ashare, portfolio, tencent, utils
from backend.app.core import cache

logger = logging.getLogger(__name__)

BENCHMARK_CODE = "000300"       # 沪深300
BENCHMARK_LABEL = "沪深300"
RISK_FREE_FALLBACK = 2.0        # 10Y 国债取不到时用 2.0%（显著标注）
MIN_SAMPLE = 30                 # 样本下限（交易日）
MAX_POINTS = 250                # 取近 250 个交易日（约一年）


def _returns(closes: List[float]) -> List[float]:
    """日收益率序列（小数）。

    Args:
        closes: 收盘价序列（按日期升序）。

    Returns:
        List[float]: 长度 = len(closes) − 1。
    """
    return [
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(1, len(closes))
        if closes[i - 1]
    ]


def _mean(values: List[float]) -> float:
    """均值。

    Args:
        values: 数值序列。

    Returns:
        float: 均值；空序列返回 0.
    """
    return sum(values) / len(values) if values else 0.0


def _std(values: List[float]) -> float:
    """样本标准差（n−1）。

    Args:
        values: 数值序列。

    Returns:
        float: 标准差；样本 < 2 返回 0.
    """
    if len(values) < 2:
        return 0.0
    avg = _mean(values)
    return math.sqrt(sum((v - avg) ** 2 for v in values) / (len(values) - 1))


def _max_drawdown(closes: List[float]) -> Tuple[Optional[float], Optional[str], Optional[int]]:
    """最大回撤（peak-to-trough）。

    Args:
        closes: 组合净值序列（按日期升序）。

    Returns:
        Tuple[Optional[float], Optional[str], Optional[int]]:
            (回撤百分比（负数）, 峰值日期近似索引对应的日期文本, 持续天数)；序列不足返回 (None, None, None)。
    """
    if len(closes) < 2:
        return None, None, None
    peak = closes[0]
    peak_index = 0
    worst = 0.0
    worst_peak_index = 0
    worst_end_index = 0
    for i, value in enumerate(closes):
        if value > peak:
            peak = value
            peak_index = i
        drawdown = value / peak - 1 if peak else 0.0
        if drawdown < worst:
            worst = drawdown
            worst_peak_index = peak_index
            worst_end_index = i
    return round(worst * 100, 2), str(worst_peak_index), worst_end_index - worst_peak_index


def _bond_rate() -> Tuple[float, str]:
    """无风险利率（10Y 国债，失败回退 2%）。

    Returns:
        Tuple[float, str]: (年化无风险利率 %, 来源说明)。
    """
    try:
        from backend.app.datasource import macro

        overview = macro.get_overview()
        for item in overview.get("indicators") or []:
            if item.get("key") == "bond_10y" and item.get("value") is not None:
                return float(item["value"]), "10Y 国债（M1 同源）"
    except Exception as exc:  # noqa: BLE001 - 失败回退，不阻断风险计算
        logger.info("无风险利率取数失败：%s", type(exc).__name__)
    return RISK_FREE_FALLBACK, f"回退 {RISK_FREE_FALLBACK}%（10Y 国债不可用）"


def _aligned_matrix(
    series: Dict[str, List[Dict[str, Any]]],
    benchmark: List[Dict[str, Any]],
) -> Tuple[List[str], List[float], List[float], Dict[str, List[float]]]:
    """以基准交易日为轴对齐各标的收盘价（缺失前值填充）。

    Args:
        series: {symbol: kline 记录列表}（记录含 date/close）。
        benchmark: 基准 kline 记录列表。

    Returns:
        Tuple[List[str], List[float], List[float], Dict[str, List[float]]]:
            (日期轴, 基准收盘价序列, 组合加权收盘价序列占位(此处为空), 各标的对齐收盘价)。
    """
    dates = [item["date"] for item in benchmark]
    bench_closes = [float(item["close"]) for item in benchmark]
    aligned: Dict[str, List[float]] = {}
    for symbol, rows in series.items():
        by_date = {item["date"]: float(item["close"]) for item in rows}
        values: List[float] = []
        last: Optional[float] = None
        for date in dates:
            price = by_date.get(date, last)
            if price is None:
                values.append(0.0)  # 轴首日之前无数据，占位 0（后续作差会被剔除）
            else:
                last = price
                values.append(price)
        aligned[symbol] = values
    return dates, bench_closes, [], aligned


def risk_report(force: bool = False) -> Dict[str, Any]:
    """组合风险指标主入口（6h 缓存，`force` 绕过）。

    Args:
        force: True 时绕过缓存。

    Returns:
        Dict[str, Any]: {
            available, reliable, sample_days, metrics, correlation, weights, missing, caliber, as_of
        }
    """
    cache_key = "portfolio:risk"

    def loader() -> Dict[str, Any]:
        """实际计算（不做缓存包装）。

        Returns:
            Dict[str, Any]: 风险指标结果。
        """
        store = portfolio.load()
        items = store.get("items") or []
        as_of = utils.now_text(utils.bj_now())
        if not items:
            return {
                "available": False,
                "reliable": False,
                "sample_days": 0,
                "metrics": {},
                "correlation": [],
                "weights": [],
                "missing": [],
                "caliber": "台账为空：请先在「持仓」页录入或用 xlsx 导入",
                "as_of": as_of,
            }

        # 取价与权重（与 attribution 同口径：市值权重）
        priced: List[Dict[str, Any]] = []
        missing: List[str] = []
        total_value = 0.0
        for raw in items:
            market = raw.get("market", "a")
            symbol = str(raw.get("symbol") or "")
            shares = float(raw.get("shares") or 0)
            price = None
            try:
                quote = tencent.get_quote(symbol, market)
                price = float(quote.get("price") or 0.0) or None
            except Exception as exc:  # noqa: BLE001
                logger.info("风险指标取价失败 %s：%s", symbol, type(exc).__name__)
            if price is None:
                missing.append(f"{symbol}（无最新价）")
                continue
            value = shares * price
            total_value += value
            priced.append({"symbol": symbol, "name": raw.get("name") or symbol,
                           "market": market, "value": value})

        if len(priced) < 2:
            return {
                "available": False,
                "reliable": False,
                "sample_days": 0,
                "metrics": {},
                "correlation": [],
                "weights": [],
                "missing": missing + ["有效持仓少于 2 只，无法计算组合风险"],
                "caliber": "组合风险至少需要 2 只有效持仓",
                "as_of": as_of,
            }

        # 拉日线（只取有日线行情的标的；场外基金无日线 → missing）
        series: Dict[str, List[Dict[str, Any]]] = {}
        for row in priced:
            if row["market"] == "fund":
                missing.append(f"{row['symbol']}（场外基金无日线，未纳入风险序列）")
                continue
            try:
                if row["market"] == "a":
                    rows = ashare.get_kline(row["symbol"], "daily", "qfq", MAX_POINTS)
                else:
                    rows = tencent.get_kline(row["symbol"], market=row["market"], period="daily",
                                             adjust="qfq", limit=MAX_POINTS)
                if len(rows) >= MIN_SAMPLE:
                    series[row["symbol"]] = rows
                else:
                    missing.append(f"{row['symbol']}（日线仅 {len(rows)} 条，样本不足）")
            except Exception as exc:  # noqa: BLE001 - 单标的失败不影响整体
                missing.append(f"{row['symbol']}（日线取数失败：{type(exc).__name__}）")

        if len(series) < 2:
            return {
                "available": False,
                "reliable": False,
                "sample_days": 0,
                "metrics": {},
                "correlation": [],
                "weights": [],
                "missing": missing + ["可用日线的持仓少于 2 只，无法计算"],
                "caliber": "场外基金无日线，暂不纳入风险序列",
                "as_of": as_of,
            }

        try:
            benchmark = tencent.get_index_kline(BENCHMARK_CODE, period="daily", limit=MAX_POINTS)
        except Exception as exc:  # noqa: BLE001
            benchmark = []
            missing.append(f"{BENCHMARK_LABEL}指数日线取数失败（{type(exc).__name__}）")

        if len(benchmark) < MIN_SAMPLE:
            return {
                "available": False,
                "reliable": False,
                "sample_days": 0,
                "metrics": {},
                "correlation": [],
                "weights": [],
                "missing": missing + [f"{BENCHMARK_LABEL}基准不可用，无法计算 β/α"],
                "caliber": "缺少基准日线，波动/回撤仍可算但 β/α 不可用",
                "as_of": as_of,
            }

        dates, bench_closes, _, aligned = _aligned_matrix(series, benchmark)

        # 权重（在纳入序列的标的内重新归一；现金/基金不参与 → 标注口径）
        weight_sum = sum(row["value"] for row in priced if row["symbol"] in series)
        weights = []
        for row in priced:
            if row["symbol"] not in series:
                continue
            weights.append({
                "symbol": row["symbol"],
                "name": row["name"],
                "weight": round(row["value"] / weight_sum * 100, 2) if weight_sum else 0.0,
            })

        # 组合日收益：Σ(权重 × 标的日收益)
        ret_by_symbol: Dict[str, List[float]] = {}
        for symbol, closes in aligned.items():
            ret_by_symbol[symbol] = _returns(closes)
        bench_returns = _returns(bench_closes)

        length = min(len(ret_by_symbol[s]) for s in ret_by_symbol)
        length = min(length, len(bench_returns))
        if length < MIN_SAMPLE:
            return {
                "available": True,
                "reliable": False,
                "sample_days": length,
                "metrics": {},
                "correlation": [],
                "weights": weights,
                "missing": missing + [f"对齐后样本仅 {length} 个交易日（< {MIN_SAMPLE}）"],
                "caliber": "样本不足，指标仅供参考；不给结论标签",
                "as_of": as_of,
            }

        # 对齐尾部（最近 length 个交易日）
        port_returns: List[float] = []
        for i in range(length):
            value = 0.0
            for item in weights:
                value += (item["weight"] / 100.0) * ret_by_symbol[item["symbol"]][len(ret_by_symbol[item["symbol"]]) - length + i]
            port_returns.append(value)
        bench_tail = bench_returns[len(bench_returns) - length:]

        # 指标
        daily_mean = _mean(port_returns)
        daily_std = _std(port_returns)
        annual_return = (1 + daily_mean) ** 252 - 1
        annual_vol = daily_std * math.sqrt(252)
        rf, rf_source = _bond_rate()
        rf_decimal = rf / 100.0
        sharpe = (annual_return - rf_decimal) / annual_vol if annual_vol else None

        # β 与 R²
        bench_mean = _mean(bench_tail)
        cov = sum((port_returns[i] - daily_mean) * (bench_tail[i] - bench_mean) for i in range(length)) / (length - 1)
        var_bench = _std(bench_tail) ** 2
        # 除零保护：基准零波动（如单调序列）时 β 无定义，返回 None 而不是爆出天文数字
        beta = cov / var_bench if var_bench > 1e-18 else None
        # 相关系数
        port_std = _std(port_returns)
        bench_std = _std(bench_tail)
        corr = cov / (port_std * bench_std) if port_std and bench_std else None
        r2 = corr ** 2 if corr is not None else None
        bench_annual = (1 + bench_mean) ** 252 - 1
        alpha = (annual_return - (rf_decimal + (beta or 0.0) * (bench_annual - rf_decimal))) if beta is not None else None

        # 组合净值（用于回撤；起点 1.0）
        equity = [1.0]
        for value in port_returns:
            equity.append(equity[-1] * (1 + value))
        max_dd, dd_index, dd_days = _max_drawdown(equity)

        # 相关性矩阵（持仓两两）
        correlation: List[Dict[str, Any]] = []
        symbols = [item["symbol"] for item in weights]
        for i, left in enumerate(symbols):
            for right in symbols[i + 1:]:
                a = ret_by_symbol[left][len(ret_by_symbol[left]) - length:]
                b = ret_by_symbol[right][len(ret_by_symbol[right]) - length:]
                mean_a, mean_b = _mean(a), _mean(b)
                cov_ab = sum((a[k] - mean_a) * (b[k] - mean_b) for k in range(length)) / (length - 1)
                sd_a, sd_b = _std(a), _std(b)
                value = cov_ab / (sd_a * sd_b) if sd_a and sd_b else None
                correlation.append({
                    "left": left, "right": right,
                    "value": round(value, 3) if value is not None else None,
                    "high": bool(value is not None and value > 0.8),
                })

        return {
            "available": True,
            "reliable": length >= MIN_SAMPLE,
            "sample_days": length,
            "metrics": {
                "annual_return": round(annual_return * 100, 2),
                "annual_vol": round(annual_vol * 100, 2),
                "max_drawdown": max_dd,
                "max_drawdown_days": dd_days,
                "sharpe": round(sharpe, 2) if sharpe is not None else None,
                "beta": round(beta, 2) if beta is not None else None,
                "alpha": round(alpha * 100, 2) if alpha is not None else None,
                "r2": round(r2, 2) if r2 is not None else None,
                "benchmark_annual": round(bench_annual * 100, 2),
                "risk_free": rf,
                "risk_free_source": rf_source,
            },
            "correlation": correlation,
            "weights": weights,
            "missing": missing,
            "caliber": (
                f"按当前持仓权重回溯 {length} 个交易日（非真实逐日持仓净值）；"
                f"基准 {BENCHMARK_LABEL}；现金与场外基金不纳入序列；"
                f"无风险利率 {rf}%（{rf_source}）；夏普/α/β 为日频年化近似，不预测未来收益"
            ),
            "as_of": as_of,
        }

    if force:
        return loader()
    return cache.get_or_load(cache_key, 6 * 3600, loader)
