"""市场宽度数据源（涨跌家数）：首页 FN-01「市场情绪」组件专用。

来源：东方财富 push2 指数统计字段
- f104 = 上涨家数、f105 = 下跌家数、f106 = 平盘家数；
- 取「上证指数 1.000001」+「深证综指 0.399106」两市合计（深证成指与综指家数同口径，取综指更全）。

红线：
1. 数值全部来自源站，失败时返回 ``available=False`` + 中文说明，**不补零、不估算、不合成**；
2. 缓存 60 秒（盘中家数变化快，但首页不必逐秒请求）；
3. 与 /api/sentiment/market 的温度指标同源同页展示，不新造第二个接口。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict
from zoneinfo import ZoneInfo

from backend.app import config
from backend.app.core import cache, netfix

logger = logging.getLogger(__name__)

_BJ = ZoneInfo("Asia/Shanghai")

# 东财指数列表接口：一次拿沪、深两市的涨跌平家数
_BREADTH_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
# 沪市（上证指数）+ 深市（深证综指）：两市家数相加即全市场口径
_SECIDS = "1.000001,0.399106"
# 缓存 60 秒：家数属高频变动指标，但仍需避免每次进首页都打源站
_TTL = 60


def _load() -> Dict[str, Any]:
    """从东财 push2 拉取沪深两市涨跌家数。

    Returns:
        Dict[str, Any]: ``{up, down, flat, available, source, caliber, as_of}``。

    Notes:
        任一市场缺字段即视为不可用，避免只统计一个市场却当成全市场。
    """
    as_of = datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S")
    try:
        resp = netfix.http_request(
            "GET",
            _BREADTH_URL,
            params={"fltt": 2, "secids": _SECIDS, "fields": "f12,f14,f104,f105,f106"},
            headers={"Referer": "https://quote.eastmoney.com/"},
            timeout=config.DATASOURCE_TIMEOUT,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}")
        diff = (resp.json() or {}).get("data", {}).get("diff") or []
    except Exception as exc:  # noqa: BLE001 - 统一转「不可用」，由前端显示空态
        logger.warning("涨跌家数获取失败：%s", exc)
        return {
            "up": None,
            "down": None,
            "flat": None,
            "available": False,
            "source": "东方财富 push2",
            "caliber": "涨跌家数暂不可用（东财 push2 连接异常），本卡显示空态，不以 0 代替",
            "as_of": as_of,
        }

    total_up = 0
    total_down = 0
    total_flat = 0
    hit = 0
    for item in diff:
        up = item.get("f104")
        down = item.get("f105")
        flat = item.get("f106")
        if up is None or down is None or flat is None:
            continue
        total_up += int(up)
        total_down += int(down)
        total_flat += int(flat)
        hit += 1
    if hit < 2:  # 沪深两市必须都拿到，否则口径不完整
        return {
            "up": None,
            "down": None,
            "flat": None,
            "available": False,
            "source": "东方财富 push2",
            "caliber": "涨跌家数数据不完整（沪深两市未同时返回），本卡显示空态，不以 0 代替",
            "as_of": as_of,
        }

    return {
        "up": total_up,
        "down": total_down,
        "flat": total_flat,
        "available": True,
        "source": "东方财富 push2",
        "caliber": "沪深两市合计（上证指数＋深证综指统计口径），盘中实时、收盘后为收盘值",
        "as_of": as_of,
    }


def market_breadth() -> Dict[str, Any]:
    """取沪深两市涨跌家数（带 60 秒缓存）。

    Returns:
        Dict[str, Any]: 见 :func:`_load` 的返回结构。
    """
    return cache.get_or_load("market:breadth:cn", _TTL, _load)
