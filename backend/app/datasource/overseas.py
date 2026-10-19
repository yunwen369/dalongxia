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

from backend.app.core import netfix
from backend.app.core.errors import NonRetryableError
from backend.app.datasource import tencent, utils

netfix.apply_netfix()
import akshare as ak  # noqa: E402

logger = logging.getLogger(__name__)

# 东财美股历史接口要求「市场前缀.代码」：105=纳斯达克，106=纽交所，107=美交所
US_PREFIXES = ("105.", "106.", "107.")


def _ensure_enabled(source: str) -> None:
    """检查数据源开关。"""
    from backend.app import config

    if not config.PROVIDER_SWITCHES.get(source, True):
        raise NonRetryableError(f"{source} 源已被环境变量禁用")


def _call(func):
    """统一包装 akshare 调用：本机 push2 系失败为确定性失效，直接降级。"""
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
    """东财港美股 K 线（生产候选；本机被源站重置）。"""
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
    """东财 K 线列（日期/开盘/收盘/最高/最低/成交量/成交额/涨跌幅）→ 统一结构。"""
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


def get_minute(symbol: str, market: str) -> List[Dict[str, Any]]:
    """分时：腾讯 1 分钟（东财港美分时无稳定免费通道，实测不可用）。"""
    return tencent.get_minute(symbol, market, "1min")


def capability_matrix() -> Dict[str, Any]:
    """港美股能力矩阵（供 /api/connectors/status 展示，D3 如实标注）。"""
    return {
        "us": {
            "quote": {"available": True, "source": "腾讯", "note": "收盘后为最后成交快照"},
            "kline": {"available": True, "source": "腾讯（东财 push2his 本机不可用）"},
            "minute": {"available": False, "source": None,
                       "note": "D3：腾讯免费接口仅返回最新一笔，东财通道本机被重置"},
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
