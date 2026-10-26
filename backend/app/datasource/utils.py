"""数据源公共工具：HTTP 会话、GBK 解码、代码归一化、行情时段判定。

对应规范 v7.0 §6.4 / §6.5 / §8.2：
1. 所有请求统一 trust_env=False、浏览器 UA、站点 Referer、15 秒超时；
2. 腾讯/新浪部分接口返回 GBK/GB18030，必须显式解码，禁止依赖 requests 猜测；
3. 代码归一化集中在本文件，provider 不再各自拼接前缀；
4. 行情时间与“盘中/盘前/盘后/收盘快照”判定集中在本文件，避免各接口口径不一。
"""

from __future__ import annotations

import calendar
import json
import logging
import re
from datetime import date, datetime, time as clock_time, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests
import pandas as pd
from zoneinfo import ZoneInfo  # 标准库时区库：按 IANA 时区显式取值，摆脱宿主系统时区影响

from backend.app import config
from backend.app.core import netfix
from backend.app.core.errors import UpstreamTimeoutError

logger = logging.getLogger(__name__)

# 中国标准时间格式串（北京时间 YYYY-MM-DD HH:MM:SS），各接口 as_of 统一口径。
_TS_FMT = "%Y-%m-%d %H:%M:%S"

# 北京时区（UTC+8）：业务时间唯一口径，部署机器时区不同也不偏移（红线「时间一律北京时间」）。
BEIJING_TZ = ZoneInfo("Asia/Shanghai")

# 市场白名单（单一来源，SC-03/R3-21）：
# - 行情/个股域（quote/stock）：a=A股、hk=港股、us=美股
# - 板块域：cn=A股（国内）、hk=港股、us=美股
# 两个域的 a/cn 语义不同：前端跨页跳转由 normSectorMarket 映射 a->cn，后端各域引用各自白名单。
MARKET_WHITELIST_QUOTE = ("a", "hk", "us")
MARKET_WHITELIST_SECTOR = ("cn", "hk", "us")


def bj_now() -> datetime:
    """取当前北京时间（naive）。

    Returns:
        datetime: 无 tzinfo 的北京时间，可直接按既有 ``%Y-%m-%d %H:%M:%S`` 口径格式化。

    Notes:
        后端业务时间（as_of/now_text/时段判定/日志导出文件名）统一走本入口，
        不再直接调用 datetime.now()，避免宿主系统时区非 UTC+8 时输出偏移。
        返回值刻意去掉 tzinfo：对外字段格式与历史版本逐字节一致，不改变既有契约。
    """
    return datetime.now(BEIJING_TZ).replace(tzinfo=None)


def bj_now_aware() -> datetime:
    """取当前北京时间（**带 tzinfo**）。

    Returns:
        datetime: 带 ``+08:00`` 偏移的北京时间，适合 ``isoformat()`` 输出。

    Notes:
        与 :func:`bj_now` 并存是有意为之：``as_of`` 等字段与历史版本逐字节一致
        必须用 naive 串；而 ``server_time`` / 日志导出文件名需要带偏移，
        两者不能共用一个返回值，否则要么破坏契约、要么丢掉时区信息。
    """
    return datetime.now(BEIJING_TZ)


def today_bj() -> date:
    """取当前北京日期。

    Returns:
        date: 北京时间的当天日期（不受宿主系统时区影响）。

    Notes:
        业务取数区间（``date.today()`` 的历史用法）一律改用本入口：
        在 UTC-5 的机器上，本地日期可能比北京晚一天，直接用 ``date.today()``
        会把「昨天」当成今天，导致 K 线区间与龙虎榜日期错位。
    """
    return bj_now().date()


def now_text(dt: Optional[datetime] = None) -> str:
    """当前时间的标准格式字符串（北京时间）。

    Args:
        dt: 待格式化的时间；缺省取 :func:`bj_now` 的北京时间。

    Returns:
        str: 格式为 ``%Y-%m-%d %H:%M:%S`` 的字符串。
    """
    return (dt or bj_now()).strftime(_TS_FMT)


def now_date_text(dt: Optional[datetime] = None) -> str:
    """当前日期的标准格式字符串（YYYY-MM-DD）。

    Args:
        dt: 待格式化的时间；缺省取 :func:`bj_now` 的北京时间。

    Returns:
        str: 格式为 ``%Y-%m-%d`` 的字符串。
    """
    return (dt or bj_now()).strftime("%Y-%m-%d")


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
    """把源站字符串安全转换为 int。

    Args:
        value: 待处理值（类型由调用方约定）。
        default: 缺省值。

    Returns:
        Optional[int]: 可能为 None。
    """
    number = to_float(value)
    if number is None:
        return default
    return int(round(number))


def clean_records(frame: pd.DataFrame) -> List[Dict[str, Any]]:
    """把 DataFrame 转为可 JSON 序列化的 records（NaN→None；日期转 ISO 字符串）。

    注意：`date`/`datetime`/`Timestamp` 若不转字符串，会在缓存落盘时触发
    `TypeError: Object of type date is not JSON serializable`（同花顺盈利预测的
    机构明细表含「报告日期」列，实测必现）。

    Args:
        frame: 源站原始表（pandas DataFrame）。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。
    """
    records: List[Dict[str, Any]] = []
    for raw in frame.to_dict("records"):
        row: Dict[str, Any] = {}
        for key, value in raw.items():
            if pd.isna(value):
                row[str(key)] = None
            elif isinstance(value, (datetime, date)):
                row[str(key)] = value.strftime("%Y-%m-%d")
            elif hasattr(value, "item"):
                row[str(key)] = value.item()
            else:
                row[str(key)] = value
        records.append(row)
    return records


def parse_datetime(value: str) -> Optional[datetime]:
    """解析腾讯/新浪/东财常见时间格式。

    支持：紧凑 `20261016161447`、`2026/10/16 18:31:15`、`2026-10-16 16:42:09`，
    以及**无横线日期+时间** `20261021 10:11:00`（东财分钟线返回此形态；
    缺此格式会使港股盘中数据被判为「时间未知」、is_realtime 误为 false）。

    Args:
        value: 时间字符串。

    Returns:
        Optional[datetime]: 解析结果；失败返回 None。
    """
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%Y%m%d%H%M%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y%m%d %H:%M:%S", "%Y%m%d %H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _us_dst_offset_hours(value: datetime) -> int:
    """按美国夏令时规则计算美东→北京的时差小时数（无 tzdata 时的回退实现）。

    规则：3 月第 2 个周日 02:00 起为 EDT（UTC-4，北京 +12h），
    11 月第 1 个周日 02:00 起回到 EST（UTC-5，北京 +13h）。

    Args:
        value: 美东本地时间（naive）。

    Returns:
        int: 12（夏令时）或 13（冬令时）。
    """
    # 3 月第 2 个周日：monthcalendar 每行一周，周一为 0，周日为 6
    march = calendar.monthcalendar(value.year, 3)
    second_sunday = [week[calendar.SUNDAY] for week in march if week[calendar.SUNDAY]][1]
    # 11 月第 1 个周日
    november = calendar.monthcalendar(value.year, 11)
    first_sunday = [week[calendar.SUNDAY] for week in november if week[calendar.SUNDAY]][0]
    dst_start = datetime(value.year, 3, second_sunday, 2, 0, 0)
    dst_end = datetime(value.year, 11, first_sunday, 2, 0, 0)
    return 12 if dst_start <= value < dst_end else 13


def us_eastern_to_beijing(value: Optional[datetime]) -> Optional[datetime]:
    """把美东本地时间换算为北京时间（UTC+8）。

    腾讯美股行情取回的时间戳是**美东当地时间**（如 ``2026-10-22 12:24:40``
    表示 EDT 盘中）。若直接当北京时间判时段，会因日期比北京时间早一天而
    命中「收盘快照」分支，把盘中实时数据标成 ``is_realtime=False``
    （独立验收 P1-01）。

    Args:
        value: 美东本地时间（naive，无时区信息）。

    Returns:
        Optional[datetime]: 换算后的北京时间（naive）；入参为空返回 None。

    Note:
        优先用 zoneinfo + IANA 时区（自动处理夏令时）；冻结包缺少 tzdata 时
        退回 ``_us_dst_offset_hours`` 的规则计算，两种路径结果一致。
    """
    if value is None:
        return None
    try:
        from zoneinfo import ZoneInfo

        eastern = value.replace(tzinfo=ZoneInfo("America/New_York"))
        return eastern.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    except Exception:  # noqa: BLE001 - 缺 tzdata 时按夏令时规则回退
        from datetime import timedelta

        return value + timedelta(hours=_us_dst_offset_hours(value))


def beijing_to_us_eastern(value: Optional[datetime]) -> Optional[datetime]:
    """把北京时间换算为美东本地时间（naive）。

    与 :func:`us_eastern_to_beijing` 互逆：优先用 zoneinfo + IANA 时区（自动处理
    夏令时）；冻结包缺少 tzdata 时按 ``_us_dst_offset_hours`` 规则迭代两级收敛。

    Args:
        value: 北京时间（naive，无时区信息）。

    Returns:
        Optional[datetime]: 美东本地时间（naive）；入参为空返回 None。
    """
    if value is None:
        return None
    try:
        from zoneinfo import ZoneInfo

        beijing = value.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        return beijing.astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)
    except Exception:  # noqa: BLE001 - 缺 tzdata 时按夏令时规则回退
        # 先用 12 小时估算美东时间，再用其所属区间的真实偏移复核；两次迭代即收敛
        guess = value - timedelta(hours=12)
        for _ in range(2):
            guess = value - timedelta(hours=_us_dst_offset_hours(guess))
        return guess


def _us_session_phase(as_of: datetime, now: datetime) -> Tuple[str, bool]:
    """按美东时区判定美股常规时段状态（含周末与夏令时）。

    美股常规时段固定为美东 09:30–16:00（夏令时/冬令时由时区换算自动切换），
    因此**必须**换算到美东判定，不能用北京时间 21:30–04:00 硬编码：
    后者在周末会误判「盘中」（周六北京 23:00 对应美东周六 11:00，实为休市）。

    Args:
        as_of: 报价时间（北京时间）。
        now: 当前时间（北京时间）。

    Returns:
        Tuple[str, bool]: (状态文案, 是否盘中实时)。
    """
    et_now = beijing_to_us_eastern(now) or now
    et_as_of = beijing_to_us_eastern(as_of) or as_of
    if et_now.weekday() >= 5:
        # 周六/周日：美股常规时段不交易，收盘快照也不得标成盘中
        return "休市", False
    current = et_now.time()
    if clock_time(9, 30) <= current <= clock_time(16, 0):
        # 盘中：仅当报价时间距今 15 分钟内才标 is_realtime；
        # 盘中但报价陈旧（>15 分钟）归为收盘快照，防止 stale quote 冒充实时。
        fresh = abs((et_now - et_as_of).total_seconds()) <= 900
        if fresh:
            return "盘中", True
        return "收盘快照", False
    if current < clock_time(9, 30):
        return "盘前", False
    if et_as_of.date() == et_now.date() and et_as_of.time() >= clock_time(16, 0):
        # 收盘后且报价时间即本交易日收盘（16:00 ET）：明确标注收盘快照
        return "收盘快照", False
    return "盘后", False


def classify_market_status(
    as_of: Optional[datetime],
    market: str,
    now: Optional[datetime] = None,
) -> Tuple[str, bool]:
    """判断行情时间对应的市场状态。

    Args:
        as_of: 数据时间（北京时间）。
        market: a/hk/us。
        now: 可注入的当前时间，仅用于测试时段判定；生产缺省取北京时间 :func:`bj_now`。

    Returns:
        Tuple[str, bool]: (状态文案, 是否盘中实时)。
        状态只使用"盘中/盘前/盘后/休市/收盘快照/时间未知"，
        午间休市归"休市"，不把收盘快照或休市伪装成实时。
    """
    if as_of is None:
        return "时间未知", False

    now = now or bj_now()
    current = now.time()
    normalized = (market or "a").lower()
    # 美股跨零点与周末口径由 _us_session_phase 按美东时区独立判定，
    # 不走这里的「日期早一天即收盘快照」通用分支（否则周六会误标盘中/周日会误标快照）。
    if normalized != "us" and as_of.date() < now.date():
        return "收盘快照", False
    if as_of.date() > now.date():
        return "数据时间异常", False
    if normalized == "us":
        return _us_session_phase(as_of, now)

    if normalized == "a":
        morning = clock_time(9, 15) <= current <= clock_time(11, 30)
        afternoon = clock_time(13, 0) <= current <= clock_time(15, 0)
        if morning or afternoon:
            return "盘中", True
        if current < clock_time(9, 15):
            return "盘前", False
        if clock_time(11, 30) < current < clock_time(13, 0):
            # A股午间休市（11:30-13:00），非盘中、非实时
            return "休市", False
        return "盘后", False

    if normalized == "hk":
        morning = clock_time(9, 30) <= current <= clock_time(12, 0)
        afternoon = clock_time(13, 0) <= current <= clock_time(16, 0)
        if morning or afternoon:
            return "盘中", True
        if current < clock_time(9, 30):
            return "盘前", False
        if clock_time(12, 0) < current < clock_time(13, 0):
            # 港股午间休市（12:00-13:00），非盘中、非实时
            return "休市", False
        return "盘后", False

    return "快照", False


def market_label(market: str) -> str:
    """返回市场中文名。

    Args:
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。

    Returns:
        str: 市场中文名（A股/港股/美股/场外基金）；未登记时原样返回。
    """
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
    """把周期转换为腾讯 K 线参数。

    Args:
        period: 周期：日k/周k/月k，或分钟数（1/5/15/30/60）。

    Returns:
        str: 腾讯 K 线周期参数。

    Raises:
        ValueError: period 仅支持 daily/weekly/monthly 或 日k/周k/月k。
    """
    key = (period or "daily").strip().lower()
    mapped = config.PERIOD_MAP.get(key)
    if mapped is None:
        raise ValueError("period 仅支持 daily/weekly/monthly 或 日k/周k/月k")
    return mapped


def normalize_adjust(adjust: str) -> str:
    """校验复权参数，返回 ""/qfq/hfq。

    Args:
        adjust: 复权方式：qfq=前复权 / hfq=后复权 / 空=不复权。

    Returns:
        str: 规范后的复权参数（""/qfq/hfq）。

    Raises:
        ValueError: adjust 仅支持 qfq/hfq/不复权。
    """
    value = (adjust or "").strip().lower()
    aliases = {"none": "", "no": "", "不复权": "", "前复权": "qfq", "后复权": "hfq"}
    value = aliases.get(value, value)
    if value not in config.ADJUST_VALUES:
        raise ValueError("adjust 仅支持 qfq/hfq/不复权")
    return value


def today_text() -> str:
    """返回今天的 ISO 日期（北京时间）。

    Returns:
        str: 今天日期（YYYY-MM-DD，按 Asia/Shanghai 计）。

    Notes:
        原实现为 ``date.today().isoformat()``，在非 UTC+8 宿主上会取到本地日期
        （UTC-5 时比北京晚一天）。TZ-06 收口后统一走 :func:`today_bj`。
    """
    return today_bj().isoformat()
