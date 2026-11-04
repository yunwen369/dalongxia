"""A股 provider：akshare 东财/新浪主备源 + 巨潮概况 + 新浪指数。

对应规范 v7.0 §6.4 / §8.6.1；接口名均以本机 akshare 1.19.1 实测为准：
- K 线：stock_zh_a_hist（东财）→ stock_zh_a_daily（新浪）→ 腾讯；
- 分时：stock_zh_a_hist_min_em（东财）→ stock_zh_a_minute（新浪）→ 腾讯；
- 概况：stock_individual_info_em（东财）→ stock_profile_cninfo（巨潮）；
- 全市场快照：stock_zh_a_spot_em（东财），仅封装不在单标的主链使用；
- 指数：stock_zh_index_daily（新浪）+ 新浪 s_ 前缀快照。

本模块只负责取数与字段清洗；降级调度由 core.resilient 负责。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List

import requests

from backend.app import config
from backend.app.core import netfix
from backend.app.core.errors import NonRetryableError
from backend.app.core.source_prefs import ensure_enabled
from backend.app.datasource import tencent, utils

# 关键顺序：证书/代理修复必须先于 akshare import
netfix.apply_netfix()
import akshare as ak  # noqa: E402

logger = logging.getLogger(__name__)


def _call_ak(func) -> Any:
    """调用 akshare；把本机代理配置导致的 ProxyError 视为确定性失效，跳过重试。

    Args:
        func: 无参 akshare 调用。

    Returns:
        Any: akshare 返回值。

    Raises:
        NonRetryableError: 本机代理配置异常，重试只会放大无效请求。
    """
    try:
        return func()
    except requests.exceptions.ProxyError as exc:
        raise NonRetryableError("东财通道代理异常，跳过重试并切换备源") from exc


def _date_text(value: Any) -> str:
    """把日期/时间列统一为字符串。

    Args:
        value: 待处理值（类型由调用方约定）。

    Returns:
        str: 统一格式的日期字符串；无法解析时原样转字符串。
    """
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    return str(value)


def _default_range(days: int = 730) -> tuple[str, str]:
    """返回默认起止日期（YYYYMMDD）。

    Args:
        days: 统计天数。

    Returns:
        tuple[str, str]: 计算结果。
    """
    end = utils.today_bj()  # TZ-06：按北京时间取当日，非 UTC+8 宿主不移位
    start = end - timedelta(days=days)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def _period_to_ak(period: str) -> str:
    """把周期转换为 akshare 参数。

    Args:
        period: 周期：日k/周k/月k，或分钟数（1/5/15/30/60）。

    Returns:
        str: akshare 的周期参数（如 daily / weekly）。

    Raises:
        ValueError: period 仅支持 daily/weekly/monthly。
    """
    key = (period or "daily").strip().lower()
    mapping = {"daily": "daily", "weekly": "weekly", "monthly": "monthly",
               "日k": "daily", "周k": "weekly", "月k": "monthly"}
    value = mapping.get(key)
    if value is None:
        raise ValueError("period 仅支持 daily/weekly/monthly")
    return value


def get_kline_em(symbol: str, period: str = "daily", adjust: str = "qfq") -> List[Dict[str, Any]]:
    """东财 K 线（主源）。

    Args:
        symbol: 6 位 A股代码。
        period: daily/weekly/monthly。
        adjust: qfq/hfq/不复权。

    Returns:
        List[Dict[str, Any]]: 日期/开/收/高/低/量/额/涨跌幅。

    Raises:
        ValueError: 东财K线返回空数据。
    """
    ensure_enabled("eastmoney")
    code, _, _ = utils.normalize_symbol(symbol, "a")
    start, end = _default_range()
    frame = _call_ak(
        lambda: ak.stock_zh_a_hist(
            symbol=code,
            period=_period_to_ak(period),
            start_date=start,
            end_date=end,
            adjust=utils.normalize_adjust(adjust),
        )
    )
    if frame is None or frame.empty:
        raise ValueError("东财K线返回空数据")
    items: List[Dict[str, Any]] = []
    for row in utils.clean_records(frame):
        items.append(
            {
                "date": _date_text(row.get("日期")),
                "open": utils.to_float(row.get("开盘")),
                "close": utils.to_float(row.get("收盘")),
                "high": utils.to_float(row.get("最高")),
                "low": utils.to_float(row.get("最低")),
                "volume": utils.to_float(row.get("成交量")),  # 手
                "amount": utils.to_float(row.get("成交额")),  # 元
                "change": utils.to_float(row.get("涨跌额")),
                "pct": utils.to_float(row.get("涨跌幅")),
                "turnover": utils.to_float(row.get("换手率")),
            }
        )
    items.sort(key=lambda item: item["date"])
    logger.info("东财K线命中：%s %s %s，%d 条", code, period, adjust, len(items))
    return items[-320:]


def get_kline_sina(symbol: str, period: str = "daily", adjust: str = "qfq") -> List[Dict[str, Any]]:
    """新浪 K 线（备源，仅日/周/月日线）。

    Args:
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。
        period: 周期：日k/周k/月k，或分钟数（1/5/15/30/60）。
        adjust: 复权方式：qfq=前复权 / hfq=后复权 / 空=不复权。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。

    Raises:
        NonRetryableError: 新浪K线仅支持日线。
        ValueError: 新浪K线返回空数据。
    """
    ensure_enabled("sina")
    if _period_to_ak(period) != "daily":
        # 新浪 stock_zh_a_daily 只有日线，周/月线必须交给腾讯备源，避免口径错位
        raise NonRetryableError("新浪K线仅支持日线")
    code, _, tencent_code = utils.normalize_symbol(symbol, "a")
    start, end = _default_range()
    frame = _call_ak(
        lambda: ak.stock_zh_a_daily(
            symbol=tencent_code,
            start_date=start,
            end_date=end,
            adjust=utils.normalize_adjust(adjust),
        )
    )
    if frame is None or frame.empty:
        raise ValueError("新浪K线返回空数据")
    items: List[Dict[str, Any]] = []
    for row in utils.clean_records(frame):
        items.append(
            {
                "date": _date_text(row.get("date")),
                "open": utils.to_float(row.get("open")),
                "close": utils.to_float(row.get("close")),
                "high": utils.to_float(row.get("high")),
                "low": utils.to_float(row.get("low")),
                "volume": utils.to_float(row.get("volume")) / 100 if row.get("volume") is not None else None,
                "amount": utils.to_float(row.get("amount")),
                "change": None,
                "pct": None,
                "turnover": utils.to_float(row.get("turnover")),
            }
        )
    items.sort(key=lambda item: item["date"])
    logger.info("新浪K线命中：%s %s，%d 条", code, period, len(items))
    return items[-320:]


def get_kline(symbol: str, period: str = "daily", adjust: str = "qfq",
              limit: int = 320) -> List[Dict[str, Any]]:
    """A股 K 线统一入口：东财 → 新浪 → 腾讯 三源降级。

    背景（2026-11-04 实测）：腾讯 A 股 ``fqkline`` 返回 HTTP 501 反爬页，
    直连调用会让个股面板 K 线块与组合风险同时失效，故收口为带降级链的单一入口。

    Args:
        symbol: 6 位 A股代码。
        period: daily/weekly/monthly（新浪仅日线，周/月线会自动落到腾讯）。
        adjust: qfq/hfq/不复权。
        limit: 返回条数上限（按日期升序取最近 N 条）。

    Returns:
        List[Dict[str, Any]]: 日期/开/收/高/低/量/额/涨跌幅，按日期升序。

    Raises:
        RuntimeError: 三个源均失败，异常信息内附各源失败原因。
    """
    errors: List[str] = []
    for name, caller in (
        ("东财", lambda: get_kline_em(symbol, period, adjust)),
        ("新浪", lambda: get_kline_sina(symbol, period, adjust)),
        ("腾讯", lambda: tencent.get_kline(symbol, "a", period, adjust, max(int(limit), 320))),
    ):
        try:
            rows = caller()
            if rows:
                logger.info("A股K线命中：%s %s（%s 源，%d 条）", symbol, period, name, len(rows))
                return rows[-max(1, int(limit)):]
            errors.append(f"{name}:空数据")
        except Exception as exc:  # noqa: BLE001 - 逐源降级，最终统一抛错
            errors.append(f"{name}:{type(exc).__name__}")
            logger.info("A股K线 %s 源不可用，切换下一源：%s", name, type(exc).__name__)
    raise RuntimeError("A股K线三源均失败（" + "；".join(errors) + "）")


def get_quote_sina(symbol: str) -> Dict[str, Any]:
    """新浪单只 A股报价（腾讯主源失败时的备源）。

    Args:
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。

    Raises:
        ValueError: 新浪报价返回格式异常。
    """
    ensure_enabled("sina")
    code, _, tencent_code = utils.normalize_symbol(symbol, "a")
    url = f"{config.SINA_QUOTE_BASE}/list={tencent_code}"
    text = utils.http_get_text(url, referer=config.SINA_REFERER, encoding="gb18030")
    if '="' not in text:
        raise ValueError("新浪报价返回格式异常")
    body = text.split('="', 1)[1].rstrip('";\n\r ')
    parts = body.split(",")
    if len(parts) < 32:
        raise ValueError("新浪报价字段不足")

    price = utils.to_float(parts[3])
    prev_close = utils.to_float(parts[2])
    change = round(price - prev_close, 4) if price is not None and prev_close is not None else None
    pct = round(change / prev_close * 100, 4) if change is not None and prev_close else None
    quote_time = utils.parse_datetime(f"{parts[30]} {parts[31]}")
    status, is_realtime = utils.classify_market_status(quote_time, "a")

    bids: List[Dict[str, Any]] = []
    asks: List[Dict[str, Any]] = []
    for index in range(5):
        bid_volume = utils.to_float(parts[10 + index * 2])
        bid_price = utils.to_float(parts[11 + index * 2])
        ask_volume = utils.to_float(parts[20 + index * 2])
        ask_price = utils.to_float(parts[21 + index * 2])
        if bid_price:
            bids.append({"price": bid_price, "volume": bid_volume or 0})
        if ask_price:
            asks.append({"price": ask_price, "volume": ask_volume or 0})

    quote = {
        "symbol": code,
        "name": parts[0],
        "market": "a",
        "currency": "CNY",
        "price": price,
        "prev_close": prev_close,
        "open": utils.to_float(parts[1]),
        "high": utils.to_float(parts[4]),
        "low": utils.to_float(parts[5]),
        "change": change,
        "pct": pct,
        "volume": (utils.to_float(parts[8]) or 0) / 100,  # 股→手
        "amount": utils.to_float(parts[9]),
        "turnover": None,
        "pe_ttm": None,
        "pb": None,
        "float_market_cap": None,
        "total_market_cap": None,
        "bids": bids,
        "asks": asks,
        "quote_time": utils.now_text(quote_time) if quote_time else None,
        "market_status": status,
        "is_realtime": is_realtime,
        "source": "新浪",
        "caliber": "新浪/实时快照（收盘后为最后成交快照）",
    }
    logger.info("新浪报价命中：%s", code)
    return quote


def get_minute_em(symbol: str, period: str = "1") -> List[Dict[str, Any]]:
    """东财分时（主源）。

    Args:
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。
        period: 周期：日k/周k/月k，或分钟数（1/5/15/30/60）。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。

    Raises:
        ValueError: 东财分时返回空数据。
    """
    ensure_enabled("eastmoney")
    code, _, _ = utils.normalize_symbol(symbol, "a")
    frame = _call_ak(
        lambda: ak.stock_zh_a_hist_min_em(symbol=code, period=str(period), adjust="")
    )
    if frame is None or frame.empty:
        raise ValueError("东财分时返回空数据")
    rows = utils.clean_records(frame)
    items = []
    for row in rows:
        time_text = str(row.get("时间", ""))
        items.append(
            {
                "time": time_text[11:16] if len(time_text) >= 16 else time_text[-5:],
                "date": time_text[:10],
                "price": utils.to_float(row.get("收盘")),
                "open": utils.to_float(row.get("开盘")),
                "high": utils.to_float(row.get("最高")),
                "low": utils.to_float(row.get("最低")),
                "volume": utils.to_float(row.get("成交量")),
                "amount": utils.to_float(row.get("成交额")),
                "change": None,
                "pct": None,
            }
        )
    items.sort(key=lambda item: (item["date"], item["time"]))
    logger.info("东财分时命中：%s %s，%d 条", code, period, len(items))
    return items


def get_minute_sina(symbol: str, period: str = "1") -> List[Dict[str, Any]]:
    """新浪分时（备源）。

    Args:
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。
        period: 周期：日k/周k/月k，或分钟数（1/5/15/30/60）。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。

    Raises:
        ValueError: 新浪分时返回空数据。
    """
    ensure_enabled("sina")
    code, _, tencent_code = utils.normalize_symbol(symbol, "a")
    frame = _call_ak(
        lambda: ak.stock_zh_a_minute(symbol=tencent_code, period=str(period), adjust="")
    )
    if frame is None or frame.empty:
        raise ValueError("新浪分时返回空数据")
    rows = utils.clean_records(frame)
    # 只保留最近一个交易日，避免把多日分钟线混成一天
    dates = sorted({str(row.get("day", ""))[:10] for row in rows if row.get("day")})
    latest = dates[-1] if dates else ""
    items = []
    for row in rows:
        day_text = str(row.get("day", ""))
        if latest and day_text[:10] != latest:
            continue
        items.append(
            {
                "time": day_text[11:16] if len(day_text) >= 16 else day_text,
                "date": day_text[:10],
                "price": utils.to_float(row.get("close")),
                "open": utils.to_float(row.get("open")),
                "high": utils.to_float(row.get("high")),
                "low": utils.to_float(row.get("low")),
                "volume": utils.to_float(row.get("volume")),
                "amount": utils.to_float(row.get("amount")),
                "change": None,
                "pct": None,
            }
        )
    items.sort(key=lambda item: item["time"])
    logger.info("新浪分时命中：%s %s，%d 条", code, period, len(items))
    return items


def get_index_quotes_sina() -> List[Dict[str, Any]]:
    """新浪 A股核心指数快照（腾讯指数源失败时备源）。

    新浪 s_ 前缀接口不返回开高低，只返回名称/点位/涨跌/涨跌幅/成交量/成交额；
    这里如实标注“无开高低”，并由路由层在徽章显示备源。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。

    Raises:
        ValueError: 新浪指数快照返回空数据。
    """
    ensure_enabled("sina")
    selected = [item for item in config.MARKET_INDEXES if item["market"] == "a"]
    codes = ",".join(f"s_{item['tencent']}" for item in selected)
    url = f"{config.SINA_QUOTE_BASE}/list={codes}"
    text = utils.http_get_text(url, referer=config.SINA_REFERER, encoding="gb18030")
    by_code = {item["tencent"]: item for item in selected}
    items: List[Dict[str, Any]] = []
    for line in text.strip().splitlines():
        if "hq_str_s_" not in line or '="' not in line:
            continue
        variable = line.split("=", 1)[0].replace("var hq_str_s_", "").strip()
        meta = by_code.get(variable)
        if not meta:
            continue
        parts = line.split('="', 1)[1].rstrip('";\n\r ').split(",")
        if len(parts) < 6:
            continue
        price = utils.to_float(parts[1])
        change = utils.to_float(parts[2])
        pct = utils.to_float(parts[3])
        items.append(
            {
                "code": meta["code"],
                "name": meta["name"],
                "market": "a",
                "currency": "CNY",
                "price": price,
                "prev_close": round(price - change, 4) if price is not None and change is not None else None,
                "open": None,
                "high": None,
                "low": None,
                "change": change,
                "pct": pct,
                "volume": utils.to_float(parts[4]),
                "amount": utils.to_float(parts[5]),
                "quote_time": utils.now_text(),
                "market_status": "快照",
                "is_realtime": False,
                "source": "新浪",
                "caliber": "新浪/指数快照（源站未提供开高低与数据时间）",
            }
        )
    if not items:
        raise ValueError("新浪指数快照返回空数据")
    logger.info("新浪指数快照命中：%d 个", len(items))
    return items


def get_index_kline_sina(code: str, period: str = "daily") -> List[Dict[str, Any]]:
    """新浪指数日线（A股指数备源；美股指数用 index_us_stock_sina）。

    Args:
        code: 证券或基金代码。
        period: 周期：日k/周k/月k，或分钟数（1/5/15/30/60）。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。

    Raises:
        ValueError: 新浪指数K线返回空数据。
    """
    ensure_enabled("sina")
    normalized, _ = utils.index_code_to_tencent(code)
    if normalized in ("DJI", "IXIC", "INX"):
        # 新浪美股指数历史：symbol 形如 .DJI，返回 date/open/high/low/close/volume
        frame = _call_ak(lambda: ak.index_us_stock_sina(symbol=f".{normalized}"))
        rows = utils.clean_records(frame)
        items = []
        for row in rows:
            items.append(
                {
                    "date": _date_text(row.get("date")),
                    "open": utils.to_float(row.get("open")),
                    "close": utils.to_float(row.get("close")),
                    "high": utils.to_float(row.get("high")),
                    "low": utils.to_float(row.get("low")),
                    "volume": utils.to_float(row.get("volume")),
                    "amount": None,
                    "change": None,
                    "pct": None,
                }
            )
        items.sort(key=lambda item: item["date"])
        logger.info("新浪美股指数K线命中：%s，%d 条", normalized, len(items))
        return items[-320:]
    frame = _call_ak(lambda: ak.stock_zh_index_daily(symbol=utils.index_code_to_tencent(code)[1]))
    if frame is None or frame.empty:
        raise ValueError("新浪指数K线返回空数据")
    rows = utils.clean_records(frame)
    items = []
    for row in rows:
        items.append(
            {
                "date": _date_text(row.get("date")),
                "open": utils.to_float(row.get("open")),
                "close": utils.to_float(row.get("close")),
                "high": utils.to_float(row.get("high")),
                "low": utils.to_float(row.get("low")),
                "volume": utils.to_float(row.get("volume")),
                "amount": None,
                "change": None,
                "pct": None,
            }
        )
    items.sort(key=lambda item: item["date"])
    logger.info("新浪指数K线命中：%s，%d 条", normalized, len(items))
    return items[-320:]


def get_profile_em(symbol: str) -> Dict[str, Any]:
    """东财个股概况（主源，失败时由巨潮备源兜底）。

    Args:
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。

    Raises:
        ValueError: 东财个股概况返回空数据。
    """
    ensure_enabled("eastmoney")
    code, _, _ = utils.normalize_symbol(symbol, "a")
    frame = _call_ak(lambda: ak.stock_individual_info_em(symbol=code))
    if frame is None or frame.empty:
        raise ValueError("东财个股概况返回空数据")
    mapping: Dict[str, Any] = {}
    for row in utils.clean_records(frame):
        key = str(row.get("item", "")).strip()
        if key:
            mapping[key] = row.get("value")
    return {
        "symbol": code,
        "name": mapping.get("股票简称") or "",
        "market": "a",
        "industry": mapping.get("行业"),
        "list_date": mapping.get("上市时间"),
        "total_shares": utils.to_float(mapping.get("总股本")),
        "float_shares": utils.to_float(mapping.get("流通股")),
        "total_market_cap": utils.to_float(mapping.get("总市值")),
        "float_market_cap": utils.to_float(mapping.get("流通市值")),
        "source": "东财",
        "caliber": "东财/个股概况（日频）",
    }


def get_profile_em_f10(symbol: str) -> Dict[str, Any]:
    """东财 F10 个股概况（直连 datacenter，不使用 V8/mini_racer）。

    Args:
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。

    Raises:
        ValueError: 东财F10个股概况返回空数据。
    """
    ensure_enabled("em_f10")
    code, _, secucode = utils.normalize_symbol(symbol, "a")
    secucode = f"{code}.{'SH' if code.startswith(('6', '9')) else 'SZ' if code.startswith(('0', '2', '3')) else 'BJ'}"
    url = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
    params = {
        "reportName": "RPT_F10_BASIC_ORGINFO",
        "columns": "ALL",
        "filter": f'(SECUCODE="{secucode}")',
        "pageNumber": "1",
        "pageSize": "1",
        "source": "HSF10",
        "client": "PC",
    }
    payload = utils.http_get_json(
        url,
        params=params,
        referer=config.EASTMONEY_REFERER,
    )
    rows = payload.get("result", {}).get("data") or []
    if not rows:
        raise ValueError("东财F10个股概况返回空数据")
    row = rows[0]
    registered_capital = utils.to_float(row.get("REG_CAPITAL"))  # 万元
    profile = {
        "symbol": code,
        "name": row.get("SECURITY_NAME_ABBR") or row.get("ORG_NAME") or "",
        "market": "a",
        "company_name": row.get("ORG_NAME"),
        "english_name": row.get("ORG_NAME_EN"),
        "industry": row.get("INDUSTRYCSRC1") or row.get("EM2016"),
        "market_board": row.get("SECURITY_TYPE") or row.get("TRADE_MARKETT"),
        "legal_representative": row.get("LEGAL_PERSON") or row.get("CHAIRMAN"),
        "registered_capital": registered_capital,
        "founded_date": (row.get("FOUND_DATE") or "")[:10] or None,
        "list_date": (row.get("LISTING_DATE") or "")[:10] or None,
        "website": row.get("ORG_WEB"),
        "email": row.get("ORG_EMAIL"),
        "phone": row.get("ORG_TEL"),
        "address": row.get("ADDRESS") or row.get("REG_ADDRESS"),
        "main_business": row.get("MAIN_BUSINESS"),
        "business_scope": row.get("BUSINESS_SCOPE"),
        "actual_holder": row.get("ACTUAL_HOLDER"),
        "total_shares": registered_capital * 10000 if registered_capital else None,
        "float_shares": None,
        "total_market_cap": None,
        "float_market_cap": None,
        "pe_ttm": None,
        "pb": None,
        "source": "东财F10",
        "caliber": "东财F10/公司概况（日频）；市值/估值为腾讯快照",
    }
    # 用腾讯快照补齐市值/估值/流通股本；失败不阻断概况，并如实改写口径
    try:
        quote = tencent.get_quote(code, "a")
        profile.update(
            {
                "name": profile["name"] or quote.get("name"),
                "total_market_cap": quote.get("total_market_cap"),
                "float_market_cap": quote.get("float_market_cap"),
                "pe_ttm": quote.get("pe_ttm"),
                "pb": quote.get("pb"),
                "float_shares": (
                    quote["float_market_cap"] / quote["price"]
                    if quote.get("float_market_cap") and quote.get("price")
                    else None
                ),
                "as_of": quote.get("quote_time"),
                "market_status": quote.get("market_status"),
                "is_realtime": quote.get("is_realtime"),
                "source": "东财F10/腾讯",
            }
        )
    except Exception as exc:  # noqa: BLE001 - 概况不因快照失败而丢失
        logger.warning("个股概况补齐腾讯快照失败：%s", type(exc).__name__)
        profile["caliber"] = "东财F10/公司概况（日频）；市值/估值暂缺（腾讯快照不可用）"
        profile.update({"as_of": None, "market_status": "时间未知", "is_realtime": False})
    logger.info("东财F10概况命中：%s", code)
    return profile


