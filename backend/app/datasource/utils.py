"""数据源公共工具：HTTP 会话、GBK 解码、代码归一化、行情时段判定。

对应规范 v7.0 §6.4 / §6.5 / §8.2：
1. 所有请求统一 trust_env=False、浏览器 UA、站点 Referer、15 秒超时；
2. 腾讯/新浪部分接口返回 GBK/GB18030，必须显式解码，禁止依赖 requests 猜测；
3. 代码归一化集中在本文件，provider 不再各自拼接前缀；
4. 行情时间与“盘中/盘前/盘后/收盘快照”判定集中在本文件，避免各接口口径不一。
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, time as clock_time
from typing import Any, Dict, Optional, Tuple

import requests

from backend.app import config
from backend.app.core import netfix
from backend.app.core.errors import UpstreamTimeoutError

logger = logging.getLogger(__name__)


def http_get_text(
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    referer: str = "",
    encoding: str = "utf-8",
    timeout: Optional[int] = None,
) -> str:
    """发起 GET 请求并返回文本。

    Args:
        url: 完整 URL。
        params: 查询参数。
        referer: 站点 Referer，部分源站缺省会 403。
        encoding: 响应编码，腾讯/新浪常用 gbk/gb18030。
        timeout: 超时秒数，缺省 config.DATASOURCE_TIMEOUT。

    Returns:
        str: 解码后的文本。

    Raises:
        UpstreamTimeoutError: 请求超时。
        requests.RequestException: 连接失败等上游异常。
    """
    headers = {
        "User-Agent": config.BROWSER_USER_AGENT,
        "Accept": "*/*",
    }
    if referer:
        headers["Referer"] = referer

    session = netfix.make_session()  # trust_env=False，绕过本机残留代理
    try:
        response = session.get(
            url,
            params=params,
            headers=headers,
            timeout=timeout or config.DATASOURCE_TIMEOUT,
        )
        response.raise_for_status()
        response.encoding = encoding
        return response.text
    except requests.Timeout as exc:
        raise UpstreamTimeoutError(f"请求超时：{url}") from exc
    finally:
        session.close()


def http_get_json(
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    referer: str = "",
    encoding: str = "utf-8",
    timeout: Optional[int] = None,
) -> Any:
    """发起 GET 请求并返回 JSON。

    Args:
        url: 完整 URL。
        params: 查询参数。
        referer: 站点 Referer。
        encoding: 响应编码。
        timeout: 超时秒数。

    Returns:
        Any: 解析后的 JSON 对象。

    Raises:
        ValueError: 响应不是合法 JSON。
    """
    text = http_get_text(
        url,
        params=params,
        referer=referer,
        encoding=encoding,
        timeout=timeout,
    )
    import json

    return json.loads(text)


def to_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    """把源站字符串安全转换为 float。

    Args:
        value: 原始值，可能是 "--"、""、千分位字符串。
        default: 转换失败时的缺省值。

    Returns:
        Optional[float]: 数值或 default。
    """
    if value is None:
        return default
    text = str(value).strip().replace(",", "")
    if text in ("", "-", "--", "None", "null"):
        return default
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def to_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    """把源站字符串安全转换为 int。"""
    number = to_float(value)
    if number is None:
        return default
    return int(round(number))


def parse_datetime(value: str) -> Optional[datetime]:
    """解析腾讯/新浪常见时间格式。

    Args:
        value: 形如 20261016161447、2026/10/16 18:31:15、2026-10-16 16:42:09。

    Returns:
        Optional[datetime]: 解析结果；失败返回 None。
    """
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%Y%m%d%H%M%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def classify_market_status(
    as_of: Optional[datetime],
    market: str,
    now: Optional[datetime] = None,
) -> Tuple[str, bool]:
    """判断行情时间对应的市场状态。

    Args:
        as_of: 数据时间（北京时间）。
        market: a/hk/us。
        now: 可注入的当前时间，仅用于测试时段判定；生产缺省 datetime.now()。

    Returns:
        Tuple[str, bool]: (状态文案, 是否盘中实时)。
        状态只使用“盘中/盘前/盘后/收盘快照/时间未知”，不把收盘快照伪装成实时。
    """
    if as_of is None:
        return "时间未知", False

    now = now or datetime.now()
    if as_of.date() < now.date():
        return "收盘快照", False
    if as_of.date() > now.date():
        return "数据时间异常", False

    current = now.time()
    normalized = (market or "a").lower()

    if normalized == "a":
        morning = clock_time(9, 15) <= current <= clock_time(11, 30)
        afternoon = clock_time(13, 0) <= current <= clock_time(15, 0)
        if morning or afternoon:
            return "盘中", True
        if current < clock_time(9, 15):
            return "盘前", False
        return "盘后", False

    if normalized == "hk":
        morning = clock_time(9, 30) <= current <= clock_time(12, 0)
        afternoon = clock_time(13, 0) <= current <= clock_time(16, 0)
        if morning or afternoon:
            return "盘中", True
        if current < clock_time(9, 30):
            return "盘前", False
        return "盘后", False

    if normalized == "us":
        # 美股常规时段按北京时间 21:30-04:00（夏令时）近似；冬令时顺延 1 小时
        late_night = current >= clock_time(21, 30) or current <= clock_time(4, 0)
        if late_night:
            return "盘中", True
        return "休市", False

    return "快照", False


def market_label(market: str) -> str:
    """返回市场中文名。"""
    return config.MARKETS.get((market or "a").lower(), "A股")


def normalize_symbol(symbol: str, market: str = "a") -> Tuple[str, str, str]:
    """把用户输入的代码归一化为 (纯代码, 市场, 腾讯代码)。

    Args:
        symbol: 600519、sh600519、600519.SH、00700、HK00700、AAPL、usAAPL 等。
        market: a/hk/us，缺省 a。

    Returns:
        Tuple[str, str, str]: (纯代码, 市场, 腾讯请求代码)。

    Raises:
        ValueError: 代码为空或不符合该市场格式。
    """
    raw = (symbol or "").strip()
    if not raw:
        raise ValueError("代码不能为空")

    lowered = raw.lower()
    # 处理带交易所前缀/后缀的输入
    if lowered.startswith(("sh", "sz", "bj")):
        market = "a"
        raw = raw[2:]
    elif lowered.startswith("hk"):
        market = "hk"
        raw = raw[2:]
    elif lowered.startswith("us"):
        market = "us"
        raw = raw[2:]

    raw = raw.upper()
    for suffix in (".SH", ".SZ", ".BJ", ".HK", ".OQ", ".N", ".US"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)]
            break

    normalized = (market or "a").lower()
    if normalized == "a":
        digits = re.sub(r"\D", "", raw)
        if len(digits) != 6:
            raise ValueError("A股代码必须为 6 位数字")
        if digits.startswith(("6", "9")):
            tencent = f"sh{digits}"
        elif digits.startswith(("0", "2", "3")):
            tencent = f"sz{digits}"
        elif digits.startswith(("4", "8")):
            tencent = f"bj{digits}"
        else:
            tencent = f"sh{digits}"
        return digits, "a", tencent

    if normalized == "hk":
        digits = re.sub(r"\D", "", raw)
        if len(digits) != 5:
            raise ValueError("港股代码必须为 5 位数字")
        return digits, "hk", f"hk{digits}"

    if normalized == "us":
        ticker = re.sub(r"[^A-Z0-9.\-]", "", raw)
        if not ticker:
            raise ValueError("美股代码不能为空")
        ticker = ticker.split(".")[0]
        return ticker, "us", f"us{ticker}"

    raise ValueError("market 仅支持 a/hk/us")


def index_code_to_tencent(code: str) -> Tuple[str, str]:
    """把指数代码转换为 (统一 code, 腾讯代码)。

    Args:
        code: 000001、SH000001、HSI、SPX 等。

    Returns:
        Tuple[str, str]: (统一 code, 腾讯请求代码)。

    Raises:
        ValueError: 不支持的指数代码。
    """
    raw = (code or "").strip().upper()
    normalized = config.INDEX_CODE_ALIASES.get(raw)
    if normalized is None and raw.startswith(("SH", "SZ")):
        normalized = config.INDEX_CODE_ALIASES.get(raw[-6:])
    if normalized is None:
        raise ValueError(f"不支持的指数代码：{code}")

    for item in config.MARKET_INDEXES:
        if item["code"] == normalized:
            return item["code"], item["tencent"]
    raise ValueError(f"不支持的指数代码：{code}")


def period_to_tencent(period: str) -> str:
    """把周期转换为腾讯 K 线参数。"""
    key = (period or "daily").strip().lower()
    mapped = config.PERIOD_MAP.get(key)
    if mapped is None:
        raise ValueError("period 仅支持 daily/weekly/monthly 或 日k/周k/月k")
    return mapped


def normalize_adjust(adjust: str) -> str:
    """校验复权参数，返回 ""/qfq/hfq。"""
    value = (adjust or "").strip().lower()
    aliases = {"none": "", "no": "", "不复权": "", "前复权": "qfq", "后复权": "hfq"}
    value = aliases.get(value, value)
    if value not in config.ADJUST_VALUES:
        raise ValueError("adjust 仅支持 qfq/hfq/不复权")
    return value


def today_text() -> str:
    """返回今天的 ISO 日期。"""
    return date.today().isoformat()
