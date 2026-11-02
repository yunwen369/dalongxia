"""港美股 provider：报价 / K 线 / 分时 / 指数（批次 5.4）。

对应规范 v7.2 §8.6 与基线03 §3.3、§4.13；D3 决策见项目状态文档。
本机实测（2026-10-20）：
- akshare `stock_us_spot_em` / `stock_hk_spot_em` → 走 push2.eastmoney.com，**被源站重置**（L-20/G-3）；
- akshare `stock_us_hist` / `stock_hk_hist` → 走 push2his.eastmoney.com，同样被重置；
- 腾讯 `qt.gtimg.cn/q=usAAPL|hk00700`、`usfqkline/hkfqkline`、`minute/query` 全部可用。

因此实际主源为腾讯，东财作为「生产环境候选」保留在降级链首位：本机命中即快速失败，
由 core.resilient 记录 attempts 后切换腾讯，徽章显示实际命中源，不伪装东财可用。

港美股**无免费个股新闻与板块专项通道**（D3 结论）：本模块不提供该能力，
由 `/api/connectors/status` 如实返回「暂不可用」，禁止编造。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from backend.app import config
from backend.app.core import netfix
from backend.app.core.errors import NonRetryableError
from backend.app.datasource import tencent, utils

netfix.apply_netfix()
import akshare as ak  # noqa: E402

logger = logging.getLogger(__name__)

# 东财美股历史接口要求「市场前缀.代码」：105=纳斯达克，106=纽交所，107=美交所
US_PREFIXES = ("105.", "106.", "107.")


def _ensure_enabled(source: str) -> None:
    """检查数据源开关。

    Args:
        source: 数据源标识（用于降级链溯源与徽章展示）。

    Raises:
        NonRetryableError: 确定性失效（不重试）时抛出，交由降级链切换下一源。
    """
    from backend.app import config

    if not config.PROVIDER_SWITCHES.get(source, True):
        raise NonRetryableError(f"{source} 源已被环境变量禁用")


def _call(func) -> Any:
    """统一包装 akshare 调用：本机 push2 系失败为确定性失效，直接降级。

    Args:
        func: 待执行的无参取数函数。

    Returns:
        Any: 结果原样透传（类型由 loader/func 约定）。

    Raises:
        NonRetryableError: 确定性失效（不重试）时抛出，交由降级链切换下一源。
    """
    try:
        return func()
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        if name in ("ProxyError", "SSLError", "ConnectionError", "RemoteDisconnected"):
            raise NonRetryableError(f"东财港美股通道不可用（{name}），切换腾讯") from exc
        raise


def get_quote_em(symbol: str, market: str) -> Dict[str, Any]:
    """东财港美股快照（生产候选；本机被源站重置）。

    实现方式：拉全市场快照后按代码查单条（东财无单只港美快照接口）。

    Args:
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。

    Raises:
        ValueError: 参数不合法或上游数据不满足口径时抛出。
    """
    _ensure_enabled("eastmoney")
    if market == "us":
        frame = _call(lambda: ak.stock_us_spot_em())
        code_column, name_column = "代码", "名称"
    else:
        frame = _call(lambda: ak.stock_hk_spot_em())
        code_column, name_column = "代码", "名称"
    target = str(symbol).upper()
    for row in frame.to_dict("records"):
        raw_code = str(row.get(code_column) or "").upper()
        if raw_code == target or raw_code.endswith("." + target) or raw_code.endswith(target):
            price = utils.to_float(row.get("最新价"))
            prev = utils.to_float(row.get("昨收"))
            pct = utils.to_float(row.get("涨跌幅"))
            return {
                "symbol": symbol,
                "name": row.get(name_column),
                "market": market,
                "currency": "USD" if market == "us" else "HKD",
                "price": price,
                "prev_close": prev,
                "open": utils.to_float(row.get("今开")),
                "high": utils.to_float(row.get("最高")),
                "low": utils.to_float(row.get("最低")),
                "change": utils.to_float(row.get("涨跌额")),
                "pct": pct,
                "volume": utils.to_float(row.get("成交量")),
                "amount": utils.to_float(row.get("成交额")),
                "market_status": "收盘快照",
                "is_realtime": False,
                "source": "东财",
                "caliber": "东财/港美股快照（收盘后为最后成交快照）",
            }
    raise ValueError(f"东财快照未找到标的：{market} {symbol}")


def get_kline_em(symbol: str, market: str, period: str, adjust: str) -> List[Dict[str, Any]]:
    """东财港美股 K 线（生产候选；本机被源站重置）。

    Args:
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。
        period: 周期：日k/周k/月k，或分钟数（1/5/15/30/60）。
        adjust: 复权方式：qfq=前复权 / hfq=后复权 / 空=不复权。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。

    Raises:
        ValueError: 参数不合法或上游数据不满足口径时抛出。
    """
    _ensure_enabled("eastmoney")
    period_map = {"日k": "daily", "周k": "weekly", "月k": "monthly",
                  "daily": "daily", "weekly": "weekly", "monthly": "monthly"}
    ak_period = period_map.get(str(period).lower(), "daily")
    if market == "us":
        code = str(symbol).upper()
        candidates = [f"{prefix}{code}" for prefix in US_PREFIXES]
        last_error: Optional[Exception] = None
        for full_code in candidates:
            try:
                frame = _call(lambda: ak.stock_us_hist(symbol=full_code, period=ak_period,
                                                       start_date="20250101", end_date="20500101",
                                                       adjust=adjust or ""))
                if not frame.empty:
                    return _to_kline_rows(frame)
            except Exception as exc:  # noqa: BLE001 - 逐个前缀尝试
                last_error = exc
                continue
        raise ValueError(f"东财美股 K 线为空：{symbol}（{type(last_error).__name__ if last_error else '无数据'}）")
    frame = _call(lambda: ak.stock_hk_hist(symbol=str(symbol), period=ak_period,
                                           start_date="20250101", end_date="20500101",
                                           adjust=adjust or ""))
    return _to_kline_rows(frame)


def _to_kline_rows(frame) -> List[Dict[str, Any]]:
    """东财 K 线列（日期/开盘/收盘/最高/最低/成交量/成交额/涨跌幅）→ 统一结构。

    Args:
        frame: 源站原始表（pandas DataFrame）。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。

    Raises:
        ValueError: 东财 K 线为空。
    """
    items: List[Dict[str, Any]] = []
    for row in frame.to_dict("records"):
        items.append(
            {
                "date": str(row.get("日期"))[:10],
                "open": utils.to_float(row.get("开盘")),
                "close": utils.to_float(row.get("收盘")),
                "high": utils.to_float(row.get("最高")),
                "low": utils.to_float(row.get("最低")),
                "volume": utils.to_float(row.get("成交量")),
                "amount": utils.to_float(row.get("成交额")),
                "pct": utils.to_float(row.get("涨跌幅")),
            }
        )
    if not items:
        raise ValueError("东财 K 线为空")
    return items


def get_minute_sina_us(symbol: str, period: str = "1") -> List[Dict[str, Any]]:
    """美股分时（新浪 ``US_MinKService.getMinK``，纯 HTTP，1/5/15/30 分钟）。

    2026-11-02 第八轮：原先美股分时被判「无免费通道」（降级点 G-12），
    实测新浪美股分时接口可用（1 分钟返回近 3 个交易日约 1000 点），
    故补入主链路，港股仍走腾讯。

    Args:
        symbol: 美股代码（如 AAPL）。
        period: 1/5/15/30 分钟。

    Returns:
        List[Dict[str, Any]]: [{time, price, volume, amount}]（按时间升序）。

    Raises:
        ValueError: 返回空数据或结构异常。
    """
    code = str(symbol or "").strip().upper()
    period_text = str(period or "1").lower().replace("min", "").replace("m", "")
    if period_text not in ("1", "5", "15", "30"):
        period_text = "1"
    payload = utils.http_get_json(
        config.SINA_US_MINUTE_URL,
        params={"symbol": code, "type": period_text},
        referer="https://stock.finance.sina.com.cn/",
    )
    rows = payload if isinstance(payload, list) else []
    items: List[Dict[str, Any]] = []
    previous_price: Optional[float] = None
    for row in rows:
        stamp = str(row.get("d") or "").strip()
        price = utils.to_float(row.get("c"))
        if not stamp or price is None:
            continue
        day, _, clock = stamp.partition(" ")
        change = None
        pct = None
        if previous_price is not None:
            change = round(price - previous_price, 4)
            if previous_price:
                pct = round((price - previous_price) / previous_price * 100, 4)
        items.append(
            {
                "time": clock[:5] if clock else stamp,
                "date": day,
                "price": price,
                "open": utils.to_float(row.get("o")),
                "high": utils.to_float(row.get("h")),
                "low": utils.to_float(row.get("l")),
                "volume": utils.to_float(row.get("v")),
                "amount": utils.to_float(row.get("a")),
                "change": change,
                "pct": pct,
            }
        )
        previous_price = price
    if not items:
        raise ValueError(f"新浪美股分时返回空数据：{code}")
    items.sort(key=lambda item: f"{item['date']} {item['time']}")
    return items


def get_minute(symbol: str, market: str) -> List[Dict[str, Any]]:
    """分时：港股腾讯 1 分钟；美股新浪 1 分钟（两者均为纯 HTTP）。

    Args:
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。
    """
    if market == "us":
        return get_minute_sina_us(symbol, "1")
    return tencent.get_minute(symbol, market, "1min")


def capability_matrix() -> Dict[str, Any]:
    """港美股能力矩阵（供 /api/connectors/status 展示，D3 如实标注）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    return {
        "us": {
            "quote": {"available": True, "source": "腾讯", "note": "收盘后为最后成交快照"},
            "kline": {"available": True, "source": "腾讯（东财 push2his 本机不可用）"},
            "minute": {"available": True, "source": "新浪美股分时（1/5/15/30 分钟）",
                       "note": "第八轮补齐：新浪 US_MinKService 纯 HTTP，约 1000 点/3 个交易日"},
            "index": {"available": True, "source": "腾讯（道指/纳指/标普）"},
            "stock_news": {"available": False, "source": None,
                           "note": "D3：akshare 无 stock_us_news，无免费免鉴权稳定通道"},
            "sector": {"available": False, "source": None,
                       "note": "D3：无美股板块专项通道；板块页用「主要指数+代表个股」简化建模"},
        },
        "hk": {
            "quote": {"available": True, "source": "腾讯", "note": "收盘后为最后成交快照"},
            "kline": {"available": True, "source": "腾讯（东财 push2his 本机不可用）"},
            "minute": {"available": True, "source": "腾讯 1 分钟"},
            "index": {"available": True, "source": "腾讯（恒生指数/恒生科技）"},
            "stock_news": {"available": False, "source": None,
                           "note": "D3：akshare 无 stock_hk_news，无免费免鉴权稳定通道"},
            "sector": {"available": False, "source": None,
                       "note": "D3：无港股板块专项通道；板块页用「主要指数+代表个股」简化建模"},
        },
    }
