"""持仓存储与分析（本地 JSON，不上传、不入 Git）。

对应规范 v7.2 §8.4（数据文件规范）与基线03 §2.7（portfolio_store.py）：
- 存储文件：`data/portfolio.json`，结构 `{"items": [ ... ], "updated_at": "..."}`；
- 单条持仓字段：id / market(a|hk|us|fund) / symbol / name / shares / cost（单位成本，本币）/ created_at / note；
- 写入采用「临时文件 + 原子替换」，避免中途失败导致文件损坏；
- 金额口径：`shares × 最新价` 得本币市值；折合人民币使用新浪实时汇率（USD/CNY、HKD/CNY），
  汇率获取失败时**不猜测**，返回 `fx_missing=true` 且只给本币口径。

本模块只负责存取与计算，不负责 HTTP。
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from backend.app import config
from backend.app.core import netfix
from backend.app.core.errors import NonRetryableError
from backend.app.datasource import tencent, utils

netfix.apply_netfix()

logger = logging.getLogger(__name__)

PORTFOLIO_FILE = config.DATA_DIR / "portfolio.json"

# 市场 → 本币（用于盈亏与折算口径）
CURRENCY_BY_MARKET = {"a": "CNY", "hk": "HKD", "us": "USD", "fund": "CNY"}
MARKET_LABEL = {"a": "A股", "hk": "港股", "us": "美股", "fund": "场外基金"}

# 新浪外汇代码：fx_susdcny = 美元兑人民币，fx_shkdcny = 港币兑人民币
FX_URL = "https://hq.sinajs.cn/list=fx_susdcny,fx_shkdcny"
FX_REFERER = "https://finance.sina.com.cn/"


def _empty_store() -> Dict[str, Any]:
    """返回空持仓结构。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    return {"items": [], "updated_at": None}


def load() -> Dict[str, Any]:
    """读取持仓文件；文件不存在或损坏时返回空结构（不抛错，避免首页白屏）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    if not PORTFOLIO_FILE.exists():
        return _empty_store()
    try:
        with open(PORTFOLIO_FILE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception as exc:  # noqa: BLE001 - 损坏文件按空处理并记录
        logger.warning("持仓文件读取失败，按空持仓处理：%s", type(exc).__name__)
        return _empty_store()
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return _empty_store()
    return payload


def save(payload: Dict[str, Any]) -> None:
    """原子写入持仓文件（先写 .tmp 再替换），并更新时间戳。

    Args:
        payload: 请求体（JSON 对象）。
    """
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    temp_path = PORTFOLIO_FILE.with_suffix(".json.tmp")
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(temp_path, PORTFOLIO_FILE)


def _validate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """校验并规范化一条持仓。

    Raises:
        ValueError: 市场/代码/份额/成本不合法。

    Args:
        payload: 请求体（JSON 对象）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    market = str(payload.get("market") or "a").lower()
    if market not in CURRENCY_BY_MARKET:
        raise ValueError("market 仅支持 a/hk/us/fund")
    symbol = str(payload.get("symbol") or "").strip()
    if not symbol:
        raise ValueError("symbol 不可为空")
    try:
        shares = float(payload.get("shares"))
        cost = float(payload.get("cost"))
    except (TypeError, ValueError) as exc:
        raise ValueError("shares 与 cost 必须为数值") from exc
    if shares <= 0:
        raise ValueError("shares 必须大于 0")
    if cost < 0:
        raise ValueError("cost 不能为负")
    return {
        "id": str(payload.get("id") or uuid.uuid4().hex[:12]),
        "market": market,
        "symbol": symbol,
        "name": str(payload.get("name") or "").strip() or symbol,
        "shares": round(shares, 4),
        "cost": round(cost, 4),
        "note": str(payload.get("note") or "").strip(),
        "created_at": payload.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def add_item(payload: Dict[str, Any]) -> Dict[str, Any]:
    """新增持仓并返回该条记录。

    Args:
        payload: 请求体（JSON 对象）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    item = _validate(payload)
    store = load()
    store["items"].append(item)
    save(store)
    return item


def update_item(item_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """按 id 更新持仓（部分字段）。

    Args:
        item_id: 条目主键。
        payload: 请求体（JSON 对象）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。

    Raises:
        KeyError: 必需字段缺失时抛出。
    """
    store = load()
    for index, item in enumerate(store["items"]):
        if item.get("id") != item_id:
            continue
        merged = dict(item)
        merged.update(payload)
        merged["id"] = item_id
        merged["created_at"] = item.get("created_at")
        store["items"][index] = _validate(merged)
        save(store)
        return store["items"][index]
    raise KeyError(item_id)


def remove_item(item_id: str) -> None:
    """按 id 删除持仓。

    Args:
        item_id: 条目主键。

    Raises:
        KeyError: 必需字段缺失时抛出。
    """
    store = load()
    remaining = [item for item in store["items"] if item.get("id") != item_id]
    if len(remaining) == len(store["items"]):
        raise KeyError(item_id)
    store["items"] = remaining
    save(store)


def get_fx() -> Dict[str, Any]:
    """获取美元/港币兑人民币汇率（新浪外汇，失败返回空字典）。

    Returns:
        dict: {"USD": 6.68, "HKD": 0.85, "as_of": "...", "source": "新浪外汇"}
    """
    try:
        text = utils.http_get_text(
            FX_URL, referer=FX_REFERER, encoding="gbk", timeout=config.DATASOURCE_TIMEOUT
        )
    except Exception as exc:  # noqa: BLE001 - 汇率失败不阻断盈亏计算
        logger.info("汇率获取失败：%s", type(exc).__name__)
        return {}
    rates: Dict[str, Any] = {}
    for line in text.splitlines():
        if "fx_susdcny" in line:
            parts = line.split('="', 1)[-1].strip('";').split(",")
            if len(parts) > 1:
                rates["USD"] = utils.to_float(parts[1])
                rates["as_of"] = parts[-1] if parts else None
        elif "fx_shkdcny" in line:
            parts = line.split('="', 1)[-1].strip('";').split(",")
            if len(parts) > 1:
                rates["HKD"] = utils.to_float(parts[1])
    if rates:
        rates["source"] = "新浪外汇"
    return rates


def _price_of(item: Dict[str, Any]) -> Tuple[Optional[float], Optional[str], str]:
    """取某条持仓的最新价与价格日期。

    Returns:
        tuple: (price, price_time, source_label)；失败返回 (None, None, 原因)。

    Args:
        item: 单条记录。
    """
    market = item.get("market")
    symbol = item.get("symbol")
    try:
        if market == "fund":
            from backend.app.datasource import fund as fund_provider

            realtime = fund_provider.get_realtime(symbol)
            return realtime.get("nav"), realtime.get("nav_date"), f"基金净值/{realtime.get('source')}"
        quote = tencent.get_quote(symbol, market)
        return quote.get("price"), quote.get("quote_time"), "腾讯行情"
    except NonRetryableError as exc:
        return None, None, str(exc)
    except Exception as exc:  # noqa: BLE001 - 单条失败不影响其他持仓
        return None, None, f"取价失败：{type(exc).__name__}"


def analyze() -> Dict[str, Any]:
    """持仓分析：逐条算本币市值/盈亏，再折算人民币并给出市场分布。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    store = load()
    fx = get_fx()
    items: List[Dict[str, Any]] = []
    total_local: Dict[str, float] = {}
    total_cny = 0.0
    total_cost_cny = 0.0
    missing_fx = False

    for raw in store["items"]:
        price, price_time, price_source = _price_of(raw)
        market = raw.get("market", "a")
        currency = CURRENCY_BY_MARKET.get(market, "CNY")
        shares = float(raw.get("shares") or 0)
        cost = float(raw.get("cost") or 0)
        cost_value = shares * cost
        market_value = shares * price if price is not None else None
        pnl = (market_value - cost_value) if market_value is not None else None
        pnl_pct = (pnl / cost_value * 100) if (pnl is not None and cost_value) else None

        # 折算人民币：CNY 直接 1.0；USD/HKD 用实时汇率；缺汇率时只给本币口径
        rate = 1.0
        if currency != "CNY":
            rate = fx.get(currency) or 0.0
            if not rate:
                missing_fx = True
        market_value_cny = market_value * rate if (market_value is not None and rate) else None
        cost_value_cny = cost_value * rate if rate else None
        pnl_cny = (market_value_cny - cost_value_cny) if (market_value_cny is not None and cost_value_cny is not None) else None

        if market_value_cny is not None:
            total_cny += market_value_cny
        if cost_value_cny is not None:
            total_cost_cny += cost_value_cny
        total_local[currency] = round(total_local.get(currency, 0.0) + (market_value or 0.0), 2)

        items.append(
            {
                "id": raw.get("id"),
                "market": market,
                "market_label": MARKET_LABEL.get(market, market),
                "symbol": raw.get("symbol"),
                "name": raw.get("name"),
                "shares": shares,
                "cost": cost,
                "currency": currency,
                "price": price,
                "price_time": price_time,
                "price_source": price_source,
                "cost_value": round(cost_value, 2),
                "market_value": round(market_value, 2) if market_value is not None else None,
                "pnl": round(pnl, 2) if pnl is not None else None,
                "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
                "market_value_cny": round(market_value_cny, 2) if market_value_cny is not None else None,
                "pnl_cny": round(pnl_cny, 2) if pnl_cny is not None else None,
            }
        )

    # 市场分布按人民币市值占比（缺汇率时该项不参与占比分母）
    distribution = []
    for market, label in MARKET_LABEL.items():
        subset = [item for item in items if item["market"] == market and item["market_value_cny"]]
        value = sum(item["market_value_cny"] for item in subset)
        distribution.append(
            {
                "market": market,
                "label": label,
                "value_cny": round(value, 2),
                "ratio": round(value / total_cny * 100, 2) if total_cny else None,
                "count": len([item for item in items if item["market"] == market]),
            }
        )

    return {
        "items": items,
        "count": len(items),
        "totals": {
            "cny_market_value": round(total_cny, 2) if total_cny else 0.0,
            "cny_cost": round(total_cost_cny, 2) if total_cost_cny else 0.0,
            "cny_pnl": round(total_cny - total_cost_cny, 2) if total_cny and total_cost_cny else None,
            "local_by_currency": total_local,
        },
        "distribution": distribution,
        "fx": fx,
        "fx_missing": missing_fx,
        "updated_at": store.get("updated_at"),
        "caliber": "市值=份额×最新价（基金用单位净值）；折合人民币用新浪实时汇率；缺汇率时不给折算值",
    }
