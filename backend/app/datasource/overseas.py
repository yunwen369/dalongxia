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

import json
import logging
from typing import Any, Dict, List, Optional

from backend.app import config
from backend.app.core import netfix
from backend.app.core.errors import NonRetryableError
from backend.app.core.source_prefs import ensure_enabled
from backend.app.datasource import tencent, utils

netfix.apply_netfix()
import akshare as ak  # noqa: E402

logger = logging.getLogger(__name__)

# 东财美股历史接口要求「市场前缀.代码」：105=纳斯达克，106=纽交所，107=美交所
US_PREFIXES = ("105.", "106.", "107.")


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
    ensure_enabled("eastmoney")
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
    ensure_enabled("eastmoney")
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


# ── 港美股中文财务指标与个股新闻（G-4 补齐，2026-11-02）──────────────────────

# 指标英文键 → 中文名（源站键名固定；两套市场键集不同，分别映射）
_US_INDICATOR_LABELS = {
    "basic_eps": "基本每股收益", "eps_dlt": "稀释每股收益", "oips": "每股营业利润",
    "revenue_ps": "每股营业收入", "nav_ps": "每股净资产", "ncf_from_oa_ps": "每股经营现金流",
    "roe_avg": "净资产收益率(平均)", "net_sales_rate": "销售净利率",
    "gross_selling_rate": "销售毛利率", "np_to_revenue": "净利率(净利/营收)",
    "sales_cost_rate": "销售成本率", "net_interest_of_ta": "总资产净利率",
    "asset_liab_ratio": "资产负债率", "current_ratio": "流动比率", "quick_ratio": "速动比率",
    "equity_multiplier": "权益乘数", "equity_ratio": "产权比率",
    "account_receivable_turnover": "应收账款周转率",
    "receivable_turnover_days": "应收账款周转天数",
    "inventory_turnover": "存货周转率", "inventory_turnover_days": "存货周转天数",
    "operating_cycle": "营业周期", "current_asset_turnover": "流动资产周转率",
    "fixed_asset_turnover_ratio": "固定资产周转率", "total_capital_turnover": "总资产周转率",
    "nag_respond_boy": "净利润同比增长", "assets_relative_boy_growth": "总资产同比增长",
    "equity_atsopc_growth_boy": "归属股东权益同比增长",
    "equity_atsopc_to_total_liab": "归属股东权益/负债合计",
    "flow_assets_to_total_assets": "流动资产/总资产", "flow_debt_to_total_debt": "流动负债/总负债",
    "noncurrent_assets_to_ta": "非流动资产/总资产",
    "noncurrent_liab_to_total_liab": "非流动负债/总负债",
    "ncf_from_oa_to_revenue": "经营现金流/营业收入",
    "income_tax_to_total_profit": "所得税/利润总额", "capital_reserve": "资本公积",
}
_HK_INDICATOR_LABELS = {
    "beps": "基本每股收益", "beps_aju": "调整后基本每股收益", "bps": "每股净资产",
    "tsrps": "每股营业收入", "opps": "每股营业利润", "plobtxps": "每股税前利润",
    "dps": "每股股利", "ncfps": "每股经营现金流", "nocfps": "每股经营活动现金流",
    "ninvcfps": "每股投资现金流", "nfcgcfps": "每股筹资现金流",
    "roe": "净资产收益率", "qro": "净资产收益率", "rota": "总资产收益率",
    "ctioro": "总资产报酬率", "gpm": "毛利率", "opemg": "营业利润率", "lnrerate": "净利率",
    "tlia_ta": "资产负债率", "cro": "流动比率", "tto": "总资产周转率",
    "dprerate": "股利支付率", "divporo": "股息率",
    "ploashh": "归属股东净利润同比", "apycvspd": "每股经营现金流同比",
    "arbcvspd": "应收账款同比", "ivcvspd": "存货同比",
}
_XUEQIU_HOST = "https://stock.xueqiu.com"


def _text(value: Any) -> str:
    """取字符串值并去空白（None → 空串）。

    Args:
        value: 原始值。

    Returns:
        str: 清洗后的文本。
    """
    return str(value or "").strip()


def _xueqiu_get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """带 Cookie 预热的雪球 JSON 请求（免鉴权，需先访问首页拿 cookie）。

    Args:
        path: ``stock.xueqiu.com`` 下的路径（如 ``/v5/stock/finance/us/indicator.json``）。
        params: 查询参数。

    Returns:
        Dict[str, Any]: ``data`` 段（失败返回空字典）。

    Raises:
        NonRetryableError: 网络确定性失效。
    """
    session = netfix.make_session()
    try:
        session.get("https://xueqiu.com/", timeout=config.DATASOURCE_TIMEOUT)
        response = session.get(
            f"{_XUEQIU_HOST}{path}",
            params=params,
            headers={"User-Agent": config.BROWSER_USER_AGENT, "Referer": "https://xueqiu.com/"},
            timeout=config.DATASOURCE_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json() or {}
        return payload.get("data") or {}
    except Exception as exc:  # noqa: BLE001 - 统一转译为可降级异常
        name = type(exc).__name__
        if name in ("ProxyError", "SSLError", "ConnectionError", "RemoteDisconnected"):
            raise NonRetryableError(f"雪球通道不可用：{name}") from exc
        raise
    finally:
        session.close()


def get_financial_indicators(symbol: str, market: str = "us",
                             periods: int = 4) -> Dict[str, Any]:
    """港美股中文财务指标（雪球免费源，G-4 补齐）。

    2026-11-02：此前「港美股中文财报无免费源」登记为缺口。实测雪球
    ``/v5/stock/finance/{us|hk}/indicator.json`` 免鉴权（先访问首页取 cookie）可用，
    返回主要财务指标（美股 36 项 / 港股 28 项，含同比），源站即中文语义。

    Args:
        symbol: 证券代码（美股字母代码 / 港股 5 位数字）。
        market: us / hk。
        periods: 返回期数（1–8）。

    Returns:
        Dict[str, Any]: {symbol, market, name, currency, standard, periods:
        [{date, metrics: [{label, key, value, yoy}]}], source, caliber}

    Raises:
        ValueError: 参数不合法或返回空。
        NonRetryableError: 通道确定性失效。
    """
    normalized_market = str(market or "us").lower()
    if normalized_market not in ("us", "hk"):
        raise ValueError("财务指标仅支持 us / hk")
    code = str(symbol or "").strip().upper()
    if not code:
        raise ValueError("symbol 不可为空")
    count = max(1, min(int(periods or 4), 8))
    data = _xueqiu_get(
        f"/v5/stock/finance/{normalized_market}/indicator.json",
        {"symbol": code, "type": "Q4", "is_detail": "true", "count": count},
    )
    rows = data.get("list") or []
    labels = _US_INDICATOR_LABELS if normalized_market == "us" else _HK_INDICATOR_LABELS
    periods_out: List[Dict[str, Any]] = []
    for row in rows:
        date = str(row.get("ed") or "")[:10]
        metrics = []
        for key, label in labels.items():
            pair = row.get(key)
            if not isinstance(pair, list) or len(pair) < 2:
                continue
            metrics.append({"label": label, "key": key, "value": pair[0], "yoy": pair[1]})
        if date and metrics:
            periods_out.append({"date": date, "metrics": metrics})
    if not periods_out:
        raise ValueError(f"雪球财务指标返回空：{code}（{normalized_market}）")
    return {
        "symbol": code,
        "market": normalized_market,
        "name": data.get("quote_name"),
        "currency": data.get("currency") or data.get("currency_name"),
        "standard": data.get("sas"),
        "tip": data.get("tip"),
        "periods": periods_out,
        "source": "雪球",
        "caliber": (
            f"雪球/港美股主要财务指标（{normalized_market.upper()}，{len(periods_out)} 期，"
            f"每期 {len(periods_out[0]['metrics'])} 项；yoy=同比，比率型指标为小数）"
        ),
    }


def get_stock_news(symbol: str, market: str = "us", limit: int = 20,
                   name: str = "") -> List[Dict[str, Any]]:
    """港美股个股新闻（东财资讯搜索 + 相关性过滤，G-4 补齐）。

    2026-11-02：新浪港美股新闻接口已下线（``Service not valid``）、雪球 timeline 有 WAF、
    同花顺无港美股个股页。东财搜索（``search-api-web``）免鉴权可用，返回中文资讯。
    逐条做相关性过滤（标题或正文含标的中文名/代码），只保留与该标的直接相关的条目。

    Args:
        symbol: 证券代码。
        market: us / hk。
        limit: 返回条数上限。
        name: 标的中文名（如「苹果」），用于相关性过滤；为空时不过滤。

    Returns:
        List[Dict[str, Any]]: [{title, text, source, time, link, market}]。

    Raises:
        ValueError: 参数不合法或响应解析失败。
        NonRetryableError: 通道确定性失效。
    """
    code = str(symbol or "").strip()
    if not code:
        raise ValueError("symbol 不可为空")
    market_text = str(market or "us").lower()
    keyword = (name or code).strip()
    size = max(5, min(int(limit or 20) * 3, 60))
    param = json.dumps({
        "uid": "",
        "keyword": keyword,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {
            "searchScope": "default", "sort": "time", "pageIndex": 1,
            "pageSize": size, "preTag": "", "postTag": ""}},
    }, ensure_ascii=False)
    session = netfix.make_session()
    try:
        response = session.get(
            "https://search-api-web.eastmoney.com/search/jsonp",
            params={"cb": "cb", "param": param},
            headers={"User-Agent": config.BROWSER_USER_AGENT,
                     "Referer": "https://so.eastmoney.com/"},
            timeout=config.DATASOURCE_TIMEOUT,
        )
        response.raise_for_status()
        text = response.text
    except Exception as exc:  # noqa: BLE001
        raise NonRetryableError(f"东财资讯搜索不可用：{type(exc).__name__}") from exc
    finally:
        session.close()
    body = text[text.find("(") + 1: text.rfind(")")] if "(" in text and ")" in text else text
    try:
        parsed = json.loads(body)
    except ValueError as exc:  # noqa: BLE001
        raise ValueError("东财资讯搜索响应解析失败") from exc
    rows = ((parsed.get("result") or {}).get("cmsArticleWebOld") or [])
    items: List[Dict[str, Any]] = []
    for row in rows:
        title = _text(row.get("title"))
        content = _text(row.get("content"))
        if not title:
            continue
        haystack = f"{title} {content}"
        if name and name not in haystack and code.upper() not in haystack.upper():
            continue
        items.append({
            "title": title,
            "text": content[:200],
            "source": "东方财富",
            "time": _text(row.get("date")),
            "link": row.get("url") or row.get("uniqueUrl") or "",
            "market": market_text,
        })
        if len(items) >= limit:
            break
    return items


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
            "stock_news": {"available": True,
                           "source": "东财资讯搜索（相关性过滤，G-4 免费源）",
                           "note": "2026-11-02 G-4 补齐：东财搜索 + 标的中文名/代码过滤"},
            "financial": {"available": True, "source": "雪球（中文财务指标 36 项，含同比）",
                          "note": "G-4 补齐：/api/stock/financial-indicator?market=us"},
            "sector": {"available": False, "source": None,
                       "note": "D3：无美股板块专项通道；板块页用「主要指数+代表个股」简化建模"},
        },
        "hk": {
            "quote": {"available": True, "source": "腾讯", "note": "收盘后为最后成交快照"},
            "kline": {"available": True, "source": "腾讯（东财 push2his 本机不可用）"},
            "minute": {"available": True, "source": "腾讯 1 分钟"},
            "index": {"available": True, "source": "腾讯（恒生指数/恒生科技）"},
            "stock_news": {"available": True,
                           "source": "东财资讯搜索（相关性过滤，G-4 免费源）",
                           "note": "2026-11-02 G-4 补齐：东财搜索 + 标的中文名/代码过滤"},
            "financial": {"available": True, "source": "雪球（中文财务指标 28 项，含同比）",
                          "note": "G-4 补齐：/api/stock/financial-indicator?market=hk"},
            "sector": {"available": False, "source": None,
                       "note": "D3：无港股板块专项通道；板块页用「主要指数+代表个股」简化建模"},
        },
    }
