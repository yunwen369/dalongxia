"""腾讯行情 provider：A/港/美实时报价、指数、K 线、分时、智能联想。

对应规范 v7.0 §6.4 / §8.6：
- 报价：https://qt.gtimg.cn/q=<code>（GBK），字段索引见 _parse_quote_line；
- K 线：https://ifzq.gtimg.cn/appstock/app/fqkline/get（A/港/美）；
- 分时：https://ifzq.gtimg.cn/appstock/app/minute/query；
- 搜索：https://smartbox.gtimg.cn/s3/（JS 转义，需还原 \\uXXXX）。

本模块只负责取数与字段清洗；降级调度由 core.resilient 负责。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.app import config
from backend.app.core.errors import NonRetryableError
from backend.app.datasource import utils

logger = logging.getLogger(__name__)


def _ensure_enabled() -> None:
    """检查腾讯源开关；被环境变量禁用时立即失败，供降级链切换。

    Raises:
        NonRetryableError: 腾讯源已被 OPENBB_DISABLE_TENCENT 禁用。
    """
    if not config.PROVIDER_SWITCHES.get("tencent", True):
        raise NonRetryableError("腾讯源已被 OPENBB_DISABLE_TENCENT 禁用")


def _split_quote_body(text: str) -> List[str]:
    """从 v_sh600519="..." 中取出字段数组。

    Args:
        text: 待解析或清洗的文本。

    Returns:
        list[str]: 字符串列表。

    Raises:
        ValueError: 腾讯报价返回格式异常。
    """
    if '="' not in text:
        raise ValueError("腾讯报价返回格式异常")
    body = text.split('="', 1)[1].rstrip('";\n\r ')
    parts = body.split("~")
    if len(parts) < 35:
        raise ValueError("腾讯报价字段不足")
    return parts


def _parse_quote_line(line: str, market_hint: str = "") -> Dict[str, Any]:
    """解析腾讯单条报价。

    Args:
        line: 形如 v_sh600519="1~贵州茅台~600519~...";
        market_hint: 调用方已知市场，缺省从变量前缀推断。

    Returns:
        Dict[str, Any]: 报价字段、时间、市场状态与买卖五档。
    """
    variable = line.split("=", 1)[0].strip()
    prefix = variable.replace("v_", "")[:2].lower()
    if prefix in ("sh", "sz", "bj"):
        market = "a"
    elif prefix == "hk":
        market = "hk"
    elif prefix == "us":
        market = "us"
    else:
        market = (market_hint or "a").lower()

    parts = _split_quote_body(line)
    quote_time = utils.parse_datetime(parts[30]) if len(parts) > 30 else None
    status, is_realtime = utils.classify_market_status(quote_time, market)

    def number(index: int) -> Optional[float]:
        """按列序号取腾讯行情字段并转 float（越界返回 None）。

        Args:
            index: 腾讯行情串 `parts` 的列序号。

        Returns:
            Optional[float]: 数值或 None（列不存在/不可解析）。
        """
        return utils.to_float(parts[index]) if index < len(parts) else None

    # 买卖五档：买一~买五在 9/11/13/15/17，卖一~卖五在 19/21/23/25/27
    bids = []
    asks = []
    for offset in (9, 11, 13, 15, 17):
        price = number(offset)
        volume = number(offset + 1)
        if price and price > 0:
            bids.append({"price": price, "volume": volume or 0})
    for offset in (19, 21, 23, 25, 27):
        price = number(offset)
        volume = number(offset + 1)
        if price and price > 0:
            asks.append({"price": price, "volume": volume or 0})

    amount_raw = number(37)  # 腾讯成交额单位为万元
    return {
        "symbol": parts[2],
        "name": parts[1],
        "market": market,
        "currency": {"a": "CNY", "hk": "HKD", "us": "USD"}.get(market, "CNY"),
        "price": number(3),
        "prev_close": number(4),
        "open": number(5),
        "high": number(33),
        "low": number(34),
        "change": number(31),
        "pct": number(32),
        "volume": number(36),  # 手
        "amount": amount_raw * 10000 if amount_raw is not None else None,  # 元
        "turnover": number(38),  # %
        "pe_ttm": number(39),
        "pb": number(46),
        "float_market_cap": number(44) * 1e8 if number(44) is not None else None,
        "total_market_cap": number(45) * 1e8 if number(45) is not None else None,
        "bids": bids,
        "asks": asks,
        "quote_time": quote_time.strftime("%Y-%m-%d %H:%M:%S") if quote_time else None,
        "market_status": status,
        "is_realtime": is_realtime,
        "source": "腾讯",
        "caliber": "腾讯/实时快照（收盘后为最后成交快照）",
    }


def get_quote(symbol: str, market: str = "a") -> Dict[str, Any]:
    """获取单只股票/指数报价。

    Args:
        symbol: 股票代码。
        market: a/hk/us。

    Returns:
        Dict[str, Any]: 报价字段，含 quote_time / market_status / is_realtime。
    """
    _ensure_enabled()
    code, normalized_market, tencent_code = utils.normalize_symbol(symbol, market)
    url = f"{config.TENCENT_QUOTE_BASE}/q={tencent_code}"
    text = utils.http_get_text(
        url,
        referer=config.TENCENT_REFERER,
        encoding="gbk",
    )
    quote = _parse_quote_line(text.strip(), normalized_market)
    quote["symbol"] = code
    quote["tencent_code"] = tencent_code
    logger.info("腾讯报价命中：%s %s", normalized_market, code)
    return quote


def get_index_quotes(market: str = "all") -> List[Dict[str, Any]]:
    """获取核心指数快照（A股/港股/美股）。

    Args:
        market: all/a/hk/us。

    Returns:
        List[Dict[str, Any]]: 指数报价列表。

    Raises:
        ValueError: 参数不合法或上游数据不满足口径时抛出。
    """
    _ensure_enabled()
    selected = [
        item for item in config.MARKET_INDEXES
        if market in ("", "all") or item["market"] == market
    ]
    if not selected:
        raise ValueError(f"不支持的指数市场：{market}")

    codes = ",".join(item["tencent"] for item in selected)
    url = f"{config.TENCENT_QUOTE_BASE}/q={codes}"
    text = utils.http_get_text(url, referer=config.TENCENT_REFERER, encoding="gbk")

    by_tencent = {item["tencent"]: item for item in selected}
    items: List[Dict[str, Any]] = []
    for line in text.strip().splitlines():
        if '="' not in line:
            continue
        variable = line.split("=", 1)[0].replace("v_", "")
        meta = by_tencent.get(variable)
        if not meta:
            continue
        quote = _parse_quote_line(line, meta["market"])
        quote["code"] = meta["code"]
        quote["name"] = meta["name"]
        quote["tencent_code"] = meta["tencent"]
        items.append(quote)

    if not items:
        raise ValueError("腾讯指数报价返回空列表")
    logger.info("腾讯指数报价命中：%d 个", len(items))
    return items


def _kline_key(period_key: str, adjust: str, is_index: bool = False) -> str:
    """返回腾讯 K 线 JSON 中的数据键。

    Args:
        period_key: 周期键名。
        adjust: 复权方式：qfq=前复权 / hfq=后复权 / 空=不复权。
        is_index: 是否为指数。

    Returns:
        str: 文本结果。
    """
    if is_index:
        return period_key
    return f"{adjust}{period_key}" if adjust else period_key


def _parse_kline_rows(rows: List[List[Any]]) -> List[Dict[str, Any]]:
    """将腾讯 K 线行转换为统一字段。

    腾讯行格式：[日期, 开, 收, 高, 低, 成交量(手), 成交额?]；
    成交额字段并非所有周期都返回，缺失时为 None。

    Args:
        rows: 数据行列表。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。

    Raises:
        ValueError: 腾讯 K 线返回空数据。
    """
    items: List[Dict[str, Any]] = []
    prev_close: Optional[float] = None
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            continue
        date_text = str(row[0])
        open_price = utils.to_float(row[1])
        close_price = utils.to_float(row[2])
        high_price = utils.to_float(row[3])
        low_price = utils.to_float(row[4])
        volume = utils.to_float(row[5])
        amount = utils.to_float(row[6]) if len(row) > 6 else None
        change = None
        pct = None
        if prev_close is not None and close_price is not None:
            change = round(close_price - prev_close, 4)
            if prev_close:
                pct = round((close_price - prev_close) / prev_close * 100, 4)
        items.append(
            {
                "date": date_text,
                "open": open_price,
                "close": close_price,
                "high": high_price,
                "low": low_price,
                "volume": volume,
                "amount": amount,
                "change": change,
                "pct": pct,
            }
        )
        if close_price is not None:
            prev_close = close_price
    if not items:
        raise ValueError("腾讯 K 线返回空数据")
    return items


def get_kline(
    symbol: str,
    market: str = "a",
    period: str = "daily",
    adjust: str = "qfq",
    limit: int = 320,
) -> List[Dict[str, Any]]:
    """获取腾讯 K 线（A/港/美）。

    Args:
        symbol: 股票代码。
        market: a/hk/us。
        period: daily/weekly/monthly 或 日k/周k/月k。
        adjust: qfq/hfq/不复权。
        limit: 返回条数上限（腾讯接口上限以内）。

    Returns:
        List[Dict[str, Any]]: 日期/开/收/高/低/量/额/涨跌幅。

    Raises:
        ValueError: 腾讯 K 线返回空数据。
    """
    _ensure_enabled()
    code, normalized_market, tencent_code = utils.normalize_symbol(symbol, market)
    period_key = utils.period_to_tencent(period)
    adjust_value = utils.normalize_adjust(adjust)
    limit_value = max(1, min(limit, 640))

    # 端点按市场区分：美股 usfqkline / 港股 hkfqkline / A股 fqkline（2026-10-20 实测）
    endpoint = {"us": "usfqkline", "hk": "hkfqkline"}.get(normalized_market, "fqkline")
    url = f"{config.TENCENT_KLINE_BASE}/appstock/app/{endpoint}/get"
    key = _kline_key(period_key, adjust_value)

    def fetch(param_code: str) -> List[Dict[str, Any]]:
        """按给定腾讯代码请求并解析；返回空列表表示该代码无有效数据。

        Args:
            param_code: 待校验的代码参数。

        Returns:
            list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。
        """
        param = f"{param_code},{period_key},,,{limit_value},{adjust_value}"
        payload = utils.http_get_json(url, params={"param": param}, referer=config.TENCENT_REFERER)
        data = payload.get("data", {}).get(param_code, {})
        rows = data.get(key) or data.get(period_key) or []
        return _parse_kline_rows(rows) if rows else []

    items: List[Dict[str, Any]] = []
    if normalized_market == "us" and "." not in tencent_code:
        # 美股必须带交易所后缀（.OQ=纳斯达克 / .N=纽交所），否则只返回上市首日与最新各 1 条。
        # 实测 usAAPL: 2 条 → usAAPL.OQ: 60 条；无后缀时按 OQ→N 依次尝试。
        for suffix in (".OQ", ".N"):
            try:
                items = fetch(tencent_code + suffix)
            except Exception as exc:  # noqa: BLE001 - 单个后缀失败继续下一个
                logger.info("腾讯美股K线后缀尝试失败：%s（%s）", suffix, type(exc).__name__)
                continue
            if len(items) >= 5:
                tencent_code = tencent_code + suffix
                break
    else:
        items = fetch(tencent_code)

    if not items:
        # 保留原有异常语义：空数据交给上层降级链处理
        raise ValueError("腾讯 K 线返回空数据")
    logger.info("腾讯K线命中：%s %s %s %s，%d 条", normalized_market, tencent_code, period_key, adjust_value, len(items))
    return items


def get_index_kline(code: str, period: str = "daily", limit: int = 320) -> List[Dict[str, Any]]:
    """获取指数 K 线。

    Args:
        code: 000001/HSI/DJI 等统一指数代码。
        period: daily/weekly/monthly。
        limit: 条数上限。

    Returns:
        List[Dict[str, Any]]: 指数 K 线。
    """
    _ensure_enabled()
    normalized_code, tencent_code = utils.index_code_to_tencent(code)
    period_key = utils.period_to_tencent(period)
    param = f"{tencent_code},{period_key},,,{max(1, min(limit, 640))},"
    url = f"{config.TENCENT_KLINE_BASE}/appstock/app/fqkline/get"
    payload = utils.http_get_json(
        url,
        params={"param": param},
        referer=config.TENCENT_REFERER,
    )
    data = payload.get("data", {}).get(tencent_code, {})
    rows = data.get(period_key) or data.get(f"qfq{period_key}") or []
    items = _parse_kline_rows(rows)
    logger.info("腾讯指数K线命中：%s %s，%d 条", normalized_code, period_key, len(items))
    return items


def get_minute(symbol: str, market: str = "a", period: str = "1min") -> List[Dict[str, Any]]:
    """获取腾讯分时（仅 1 分钟粒度）。

    Args:
        symbol: 股票代码。
        market: a/hk/us。
        period: 仅支持 1min/1；其他粒度由新浪/东财 provider 处理。

    Returns:
        List[Dict[str, Any]]: 时间/价格/成交量/成交额。

    Raises:
        NonRetryableError: 腾讯分时仅支持 1 分钟粒度。
        ValueError: 腾讯分时返回空数据。
    """
    _ensure_enabled()
    if str(period).lower() not in ("1", "1min", "1m"):
        raise NonRetryableError("腾讯分时仅支持 1 分钟粒度")
    code, normalized_market, tencent_code = utils.normalize_symbol(symbol, market)
    url = f"{config.TENCENT_KLINE_BASE}/appstock/app/minute/query"

    def fetch(param_code: str) -> Dict[str, Any]:
        """按给定腾讯代码取分时容器；返回 {date, data}。

        Args:
            param_code: 待校验的代码参数。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
        payload = utils.http_get_json(
            url,
            params={"code": param_code},
            referer=config.TENCENT_REFERER,
        )
        node = payload.get("data", {}).get(param_code, {})
        return node.get("data", {}) or {}

    container = fetch(tencent_code)
    if normalized_market == "us" and "." not in tencent_code and len(container.get("data") or []) < 5:
        # 美股分时同样需要交易所后缀（实测无后缀仅 1 条）
        for suffix in (".OQ", ".N"):
            candidate = fetch(tencent_code + suffix)
            if len(candidate.get("data") or []) >= 5:
                tencent_code = tencent_code + suffix
                container = candidate
                break
    date_text = str(container.get("date", ""))
    raw_rows = container.get("data", []) or []
    items: List[Dict[str, Any]] = []
    prev_price: Optional[float] = None
    for raw in raw_rows:
        parts = str(raw).split()
        if len(parts) < 3:
            continue
        hhmm = parts[0]
        price = utils.to_float(parts[1])
        volume = utils.to_float(parts[2])
        amount = utils.to_float(parts[3]) if len(parts) > 3 else None
        change = None
        pct = None
        if prev_price is not None and price is not None:
            change = round(price - prev_price, 4)
            if prev_price:
                pct = round((price - prev_price) / prev_price * 100, 4)
        items.append(
            {
                "time": f"{hhmm[:2]}:{hhmm[2:]}" if len(hhmm) >= 4 else hhmm,
                "date": date_text,
                "price": price,
                "volume": volume,
                "amount": amount,
                "change": change,
                "pct": pct,
            }
        )
        if price is not None:
            prev_price = price
    if not items:
        raise ValueError("腾讯分时返回空数据")
    logger.info("腾讯分时命中：%s %s，%d 条", normalized_market, code, len(items))
    return items


def _decode_hint(text: str) -> str:
    """还原腾讯 smartbox 的 \\uXXXX 转义。

    Args:
        text: 待解析或清洗的文本。

    Returns:
        str: 文本结果。
    """
    try:
        return json.loads(f'"{text}"')
    except json.JSONDecodeError:
        return text


def get_search(keyword: str, limit: int = 20) -> List[Dict[str, Any]]:
    """腾讯智能联想搜索。

    Args:
        keyword: 代码/名称/拼音。
        limit: 返回条数上限。

    Returns:
        List[Dict[str, Any]]: symbol/name/market/type/tencent_code。

    Raises:
        ValueError: 搜索关键词不能为空。
    """
    _ensure_enabled()
    query = (keyword or "").strip()
    if not query:
        raise ValueError("搜索关键词不能为空")
    url = f"{config.TENCENT_SEARCH_BASE}/s3/"
    text = utils.http_get_text(
        url,
        params={"q": query, "t": "all"},
        referer=config.TENCENT_REFERER,
        encoding="utf-8",
    )
    if '="' not in text:
        raise ValueError("腾讯搜索返回格式异常")
    raw = text.split('="', 1)[1].rstrip('";\n\r ')
    decoded = _decode_hint(raw)
    items: List[Dict[str, Any]] = []
    for entry in decoded.split("^"):
        fields = entry.split("~")
        if len(fields) < 4:
            continue
        prefix, raw_code, name, pinyin = fields[0], fields[1], fields[2], fields[3]
        kind = fields[4] if len(fields) > 4 else ""
        if prefix in ("sh", "sz", "bj"):
            market = "a"
            symbol = raw_code
        elif prefix == "hk":
            market = "hk"
            symbol = raw_code
        elif prefix == "us":
            market = "us"
            symbol = raw_code.split(".")[0].upper()
        else:
            continue
        items.append(
            {
                "symbol": symbol,
                "name": name,
                "market": market,
                "type": kind,
                "pinyin": pinyin,
                "tencent_code": f"{prefix}{raw_code}" if prefix != "us" else f"us{symbol}",
            }
        )
        if len(items) >= max(1, min(limit, 50)):
            break
    if not items:
        raise ValueError("腾讯搜索未找到匹配项")
    logger.info("腾讯搜索命中：%s，%d 条", query, len(items))
    return items
