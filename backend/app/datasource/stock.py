"""个股（stock）provider：概况/股东/FinScope 聚合/财务四接口。

对应规范 v7.2 §6.4 / §8.6 与《设计基准说明书_阶段5.1》第十节 10.2。
接口名与字段均以本机实测为准（akshare 1.19.1，2026-10-20）：

- 公司概况：东财 datacenter `RPT_F10_BASIC_ORGINFO`（字段 ORG_NAME/EM2016/LISTING_DATE/
  MAIN_BUSINESS/REG_CAPITAL 等），估值与股本取 `RPT_VALUEANALYSIS_DET`
  （TOTAL_SHARES/FREE_SHARES_A/TOTAL_MARKET_CAP/PE_TTM）。两者均为 datacenter-web 域，
  不走已被源站重置的 push2 域（L-20）。
- 十大股东：`RPT_F10_EH_HOLDERS` / `RPT_F10_EH_FREEHOLDERS`，按 END_DATE 取最新一期。
- 报价/K线/分时：复用阶段 4 已验收的 tencent provider（不重复实现，遵循单一实现原则）。
- 资金分布：新浪 `MoneyFlow.ssi_ssfx_flzjtj`，返回 r0/r1/r2/r3 四档（超大/大/中/小单）真实数据。
  东财 push2his 与腾讯 ff_ 两个候选通道实测均不可用（前者代理重置、后者返回 pv_none_match），
  故四档唯一可用源为新浪，已在接口说明书登记。
- 相关新闻：`stock_news_em`（须先 netfix 修复证书路径，否则 curl_cffi 报 CAfile 中文路径错误）。
- 机构预测：同花顺 `basic.10jqka.com.cn/{code}/worth.html`
  （评级分布 = profit-forecast-tab-count-N；目标价从研报摘要按正则抽取，非一致预期均值）
  → 备源东财研报 `stock_research_report_em`（仅评级/机构数，无目标价）。
- 财务：新浪 `stock_financial_abstract` / `stock_financial_report_sina` /
  `stock_financial_analysis_indicator`，全部为真实财报数据，TTL 3600s。

本模块只负责取数与口径清洗；缓存与降级调度由 routers/stock.py 负责。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.app import config
from backend.app.core import netfix
from backend.app.core.errors import NonRetryableError
from backend.app.datasource import tencent, utils

netfix.apply_netfix()
import akshare as ak  # noqa: E402

logger = logging.getLogger(__name__)

# 东财 datacenter 公共入口（非 push2 域，本机实测可用）；URL 收口到 config（P2-08）
EM_DATACENTER_URL = config.EASTMONEY_DATACENTER_URL

# 本地确定性情绪词典（±0.1 三档口径的极简版，仅用于 ST-05 新闻标签，
# 不是 AI 判读、不接大模型，符合「严禁把 LLM 当数据源」红线）
POSITIVE_WORDS = (
    "增长", "中标", "突破", "上调", "买入", "增持", "创新高", "扩产", "订单", "超预期",
    "盈利", "涨价", "获批", "合作", "回购", "分红", "领先", "放量", "提升", "扭亏",
)
NEGATIVE_WORDS = (
    "下滑", "亏损", "减持", "下调", "处罚", "违规", "诉讼", "降价", "裁员", "风险",
    "终止", "退市", "质押", "冻结", "不及预期", "跌", "警示", "问询", "解禁", "计提",
)


def _ensure_enabled(source: str = "akshare") -> None:
    """检查数据源开关。

    Args:
        source: 数据源标识（用于降级链溯源与徽章展示）。

    Raises:
        NonRetryableError: 确定性失效（不重试）时抛出，交由降级链切换下一源。
    """
    if not config.PROVIDER_SWITCHES.get(source, True):
        raise NonRetryableError(f"{source} 源已被环境变量禁用")


def _call(func) -> Any:
    """统一包装 akshare 调用：确定性网络失效直接降级，不做无效重试。

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
            raise NonRetryableError(f"上游通道不可用：{name}") from exc
        raise


def _num(value: Any) -> Optional[float]:
    """安全转 float，空值返回 None（不补 0）。

    Args:
        value: 待处理值（类型由调用方约定）。

    Returns:
        float | None: 数值；无法解析时返回 None（不补 0）。
    """
    return utils.to_float(value)


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    """保留小数位。

    Args:
        value: 待处理数值。
        digits: 保留小数位数。

    Returns:
        float | None: 数值；无法解析时返回 None（不补 0）。
    """
    return utils.round_or_none(value, digits)


def _secucode(symbol: str) -> str:
    """6 位代码 → 东财 SECUCODE（688012.SH / 000001.SZ / 830799.BJ）。

    Args:
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。

    Returns:
        str: 东财 SECUCODE（带 .SH/.SZ/.BJ 市场后缀）。
    """
    code = str(symbol).strip()
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    if code.startswith(("0", "2", "3")):
        return f"{code}.SZ"
    return f"{code}.BJ"


def _sina_prefix(symbol: str) -> str:
    """6 位代码 → 新浪前缀（sh/sz/bj），供 stock_financial_report_sina 使用。

    Args:
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。

    Returns:
        str: 新浪市场前缀（sh/sz/bj）。
    """
    code = str(symbol).strip()
    if code.startswith(("6", "9")):
        return f"sh{code}"
    if code.startswith(("0", "2", "3")):
        return f"sz{code}"
    return f"bj{code}"


def _em_report(report_name: str, filter_text: str, *, sort: str = "", size: int = 10) -> List[Dict[str, Any]]:
    """调用东财 datacenter 报表接口。

    Args:
        report_name: 报表名，如 RPT_F10_BASIC_ORGINFO。
        filter_text: filter 表达式，如 (SECUCODE="688012.SH")。
        sort: 排序字段，配合 sortTypes=-1 使用。
        size: 返回条数。

    Returns:
        List[Dict[str, Any]]: 数据行列表，空结果返回 []。

    Raises:
        NonRetryableError: 确定性失效（不重试）时抛出，交由降级链切换下一源。
    """
    params: Dict[str, Any] = {
        "reportName": report_name,
        "columns": "ALL",
        "filter": filter_text,
        "pageNumber": 1,
        "pageSize": size,
        "source": "HSF10",
        "client": "PC",
    }
    if sort:
        params["sortColumns"] = sort
        params["sortTypes"] = "-1"
    try:
        # P2-07：不再裸 requests.get，统一走 utils 会话（浏览器 UA / Referer / trust_env=False）
        payload = utils.http_get_json(
            EM_DATACENTER_URL,
            params=params,
            referer=config.EASTMONEY_REFERER,
            timeout=config.DATASOURCE_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 - 统一转译为可降级异常
        raise NonRetryableError(f"东财 datacenter 不可用：{type(exc).__name__}") from exc
    result = payload.get("result") or {}
    return result.get("data") or []


# =============================================================
# 一、公司概况（ST-08）
# =============================================================
def get_profile(symbol: str, market: str = "a") -> Dict[str, Any]:
    """公司概况：基本信息 / 股本市值 / 主营范围。

    港美股无 F10 通道，退化为「腾讯行情 + 境外简化建模」口径（D3 已确认的缺口）。

    Args:
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。

    Raises:
        ValueError: 参数不合法或上游数据不满足口径时抛出。
    """
    if market in ("hk", "us"):
        quote = tencent.get_quote(symbol, market)
        return {
            "symbol": symbol,
            "name": quote.get("name"),
            "full_name": quote.get("name"),
            "industry": None,
            "board": None,
            "listing_date": None,
            "reg_capital": None,
            "total_share": None,
            "float_share": None,
            "total_mv": quote.get("total_market_cap"),
            "float_mv": quote.get("float_market_cap"),
            "pe_ttm": quote.get("pe_ttm"),
            "pb": quote.get("pb"),
            "main_business": None,
            "business_scope": None,
            "website": None,
            "province": None,
            "employees": None,
            "caliber_note": "境外简化建模：无 F10 概况通道",
        }

    info_rows = _em_report("RPT_F10_BASIC_ORGINFO", f'(SECUCODE="{_secucode(symbol)}")', size=1)
    if not info_rows:
        raise ValueError(f"东财 F10 未返回概况：{symbol}")
    info = info_rows[0]

    # 估值/股本：同一报表按交易日倒序取最新一条
    valuation: Dict[str, Any] = {}
    try:
        val_rows = _em_report("RPT_VALUEANALYSIS_DET", f'(SECURITY_CODE="{symbol}")', sort="TRADE_DATE", size=1)
        if val_rows:
            valuation = val_rows[0]
    except Exception as exc:  # noqa: BLE001 - 估值缺失不阻断概况主体
        logger.info("概况估值降级：%s（%s）", symbol, type(exc).__name__)

    quote: Dict[str, Any] = {}
    try:
        quote = tencent.get_quote(symbol, "a")
    except Exception as exc:  # noqa: BLE001
        logger.info("概况报价降级：%s（%s）", symbol, type(exc).__name__)

    reg_capital = _num(info.get("REG_CAPITAL"))  # 单位：万元
    return {
        "symbol": symbol,
        "name": info.get("SECURITY_NAME_ABBR") or (quote.get("name") if quote else None),
        "full_name": info.get("ORG_NAME"),
        "industry": info.get("EM2016") or info.get("BOARD_NAME_LEVEL"),
        "board": info.get("TRADE_MARKETT") or info.get("SECURITY_TYPE"),
        "listing_date": str(info.get("LISTING_DATE") or "")[:10] or None,
        "reg_capital": _round(reg_capital, 2),
        "total_share": _num(valuation.get("TOTAL_SHARES")),
        "float_share": _num(valuation.get("FREE_SHARES_A")),
        "total_mv": _num(valuation.get("TOTAL_MARKET_CAP")) or (quote.get("total_market_cap") if quote else None),
        "float_mv": _num(valuation.get("NOTLIMITED_MARKETCAP_A")) or (quote.get("float_market_cap") if quote else None),
        "pe_ttm": _num(valuation.get("PE_TTM")) or (quote.get("pe_ttm") if quote else None),
        "pb": _num(valuation.get("PB_MRQ")) or (quote.get("pb") if quote else None),
        "main_business": info.get("MAIN_BUSINESS"),
        "business_scope": info.get("BUSINESS_SCOPE"),
        "website": info.get("ORG_WEB"),
        "province": info.get("PROVINCE"),
        "employees": utils.to_int(info.get("EMP_NUM")),
        "price": quote.get("price") if quote else None,
        "turnover": quote.get("turnover") if quote else None,
        "caliber_note": "东财 F10 基本资料 + 东财估值 + 腾讯报价",
    }


# =============================================================
# 二、十大股东（ST-10）
# =============================================================
def _holders_of(report: str, symbol: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """取某报表的股东名单。

    口径说明：东财 F10 的 END_DATE 既包含定期报告期，也包含临时披露日（如 2026-09-09 仅 3 名股东）。
    「十大股东」应以**完整披露期**为准，故按 END_DATE 倒序取第一个股东数 ≥5 的期次；
    若全部期次都不足 5 名，则退回最新一期并如实返回实际条数（不编造、不补齐）。

    Args:
        report: 报告标识。
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。

    Returns:
        Tuple[List[Dict[str, Any]], Optional[str]]: 计算结果。
    """
    rows = _em_report(report, f'(SECUCODE="{_secucode(symbol)}")', sort="END_DATE", size=400)
    if not rows:
        return [], None

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        date_text = str(row.get("END_DATE") or "")[:10]
        grouped.setdefault(date_text, []).append(row)

    ordered_dates = sorted(grouped.keys(), reverse=True)
    latest = ordered_dates[0] if ordered_dates else None
    chosen = next((date_text for date_text in ordered_dates if len(grouped[date_text]) >= 5), latest)
    if not chosen:
        return [], None

    picked: List[Dict[str, Any]] = []
    for row in grouped[chosen]:
        # 持股比例字段两张报表命名不同：HOLD_NUM_RATIO / FREE_HOLDNUM_RATIO
        ratio = _num(row.get("HOLD_NUM_RATIO"))
        if ratio is None:
            ratio = _num(row.get("FREE_HOLDNUM_RATIO"))
        change_text = row.get("HOLD_NUM_CHANGE")
        change_num = _num(change_text)
        picked.append(
            {
                "rank": utils.to_int(row.get("HOLDER_RANK")),
                "name": row.get("HOLDER_NAME"),
                "ratio": _round(ratio, 2),
                "hold_num": _num(row.get("HOLD_NUM")),
                "change": change_num,
                "change_text": change_text if change_num is None else None,
            }
        )
    picked.sort(key=lambda item: item.get("rank") or 99)
    return picked[:10], chosen


def get_holders(symbol: str) -> Dict[str, Any]:
    """十大股东 + 十大流通股东（最新一期）。

    Args:
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。

    Raises:
        ValueError: 参数不合法或上游数据不满足口径时抛出。
    """
    top, top_date = _holders_of("RPT_F10_EH_HOLDERS", symbol)
    free, free_date = _holders_of("RPT_F10_EH_FREEHOLDERS", symbol)
    if not top and not free:
        raise ValueError(f"东财 F10 未返回股东数据：{symbol}")
    return {
        "symbol": symbol,
        "top10": top,
        "top10_period": top_date,
        "free_top10": free,
        "free_top10_period": free_date,
    }


# =============================================================
# 三、资金分布（ST-04）—— 新浪四档真实数据
# =============================================================
def get_fund_dist(symbol: str) -> Dict[str, Any]:
    """个股当日资金分布：超大单/大单/中单/小单 + 主力（超大+大）。

    数据源：新浪 MoneyFlow.ssi_ssfx_flzjtj，字段 r0/r1/r2/r3 分别对应四档，
    `*_in` 为流入、`*_out` 为流出，单位元。占比以四档流入+流出合计为基数，
    与东财「主力净流入占成交额比」口径接近但非完全相同，已在接口说明书注明。

    Args:
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。

    Raises:
        NonRetryableError: 新浪资金流返回格式异常。
        ValueError: 参数不合法或上游数据不满足口径时抛出。
    """
    code = str(symbol).strip()
    url = (
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"MoneyFlow.ssi_ssfx_flzjtj?daima={_sina_prefix(code)}"
    )
    text = utils.http_get_text(url, encoding="gbk", timeout=config.DATASOURCE_TIMEOUT)
    try:
        import json

        payload = json.loads(text)
    except Exception as exc:  # noqa: BLE001 - 返回非 JSON 视为源异常
        raise NonRetryableError("新浪资金流返回格式异常") from exc
    if not isinstance(payload, dict) or "r0_in" not in payload:
        raise ValueError(f"新浪资金流无有效数据：{symbol}")

    tiers: List[Dict[str, Any]] = []
    total_turnover = 0.0
    raw: Dict[str, Tuple[float, float]] = {}
    for key, label in (("r0", "超大单"), ("r1", "大单"), ("r2", "中单"), ("r3", "小单")):
        inflow = _num(payload.get(f"{key}_in")) or 0.0
        outflow = _num(payload.get(f"{key}_out")) or 0.0
        raw[key] = (inflow, outflow)
        total_turnover += inflow + outflow

    for key, label in (("r0", "超大单"), ("r1", "大单"), ("r2", "中单"), ("r3", "小单")):
        inflow, outflow = raw[key]
        # 单位：元 → 亿元；新浪原始值均为字符串金额
        tiers.append(
            {
                "name": label,
                "inflow": _round(inflow / 1e8, 4),
                "outflow": _round(outflow / 1e8, 4),
                "net": _round((inflow - outflow) / 1e8, 4),
                "ratio": _round((inflow - outflow) / total_turnover * 100, 2) if total_turnover else None,
            }
        )

    main_net = (raw["r0"][0] - raw["r0"][1]) + (raw["r1"][0] - raw["r1"][1])
    return {
        "symbol": symbol,
        "tiers": tiers,
        "main_net": _round(main_net / 1e8, 4),
        "main_ratio": _round(main_net / total_turnover * 100, 2) if total_turnover else None,
        "net_amount": _round((_num(payload.get("netamount")) or 0.0) / 1e8, 4)
        if payload.get("netamount") is not None else None,
        "trade": _round(_num(payload.get("trade"))),
        "changeratio": _round(_num(payload.get("changeratio"))),
        "caliber_note": "新浪四档资金流（占比基数为四档流入+流出合计）",
    }


# =============================================================
# 四、相关新闻（ST-05）
# =============================================================
def _sentiment_of(text: str) -> Dict[str, Any]:
    """本地词典情绪打分（确定性规则，±1 分制映射为利好/利空/中性）。

    仅用于新闻标签，不使用大模型，符合「严禁把 LLM 当数据源」。

    Args:
        text: 待解析或清洗的文本。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    body = text or ""
    pos = sum(1 for word in POSITIVE_WORDS if word in body)
    neg = sum(1 for word in NEGATIVE_WORDS if word in body)
    score = pos - neg
    if score > 0:
        label = "利好"
    elif score < 0:
        label = "利空"
    else:
        label = "中性"
    return {"label": label, "score": score, "positive_hits": pos, "negative_hits": neg}


def get_news(symbol: str, limit: int = 10) -> List[Dict[str, Any]]:
    """个股相关新闻（东财 stock_news_em）。

    Args:
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。
        limit: 返回条数上限（超出上限按上限截断）。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。
    """
    _ensure_enabled()
    frame = _call(lambda: ak.stock_news_em(symbol=str(symbol)))
    items: List[Dict[str, Any]] = []
    for row in utils.clean_records(frame)[: max(1, limit)]:
        title = str(row.get("新闻标题") or "").strip()
        if not title:
            continue
        sentiment = _sentiment_of(title + " " + str(row.get("新闻内容") or ""))
        items.append(
            {
                "title": title,
                "time": str(row.get("发布时间") or ""),
                "source": row.get("文章来源"),
                "url": row.get("新闻链接"),
                "sentiment": sentiment["label"],
                "sentiment_score": sentiment["score"],
            }
        )
    return items


# =============================================================
# 五、机构预测（ST-06）
# =============================================================
_RATING_NAMES = {0: "买入", 1: "增持", 2: "中性", 3: "减持", 4: "卖出"}


def _forecast_ths(symbol: str) -> Dict[str, Any]:
    """同花顺 worth.html：评级分布（结构化 span）+ 研报摘要中的目标价。

    Args:
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。

    Raises:
        ValueError: 参数不合法或上游数据不满足口径时抛出。
    """
    url = f"https://basic.10jqka.com.cn/{symbol}/worth.html"
    html = utils.http_get_text(url, encoding="gbk", timeout=config.DATASOURCE_TIMEOUT)
    counts = re.findall(r'profit-forecast-tab-count-(\d)">(\d+)<', html)
    ratings = {name: 0 for name in _RATING_NAMES.values()}
    for index_text, count_text in counts:
        name = _RATING_NAMES.get(int(index_text))
        if name:
            ratings[name] = int(count_text)
    org_count = sum(ratings.values())

    # 目标价：研报摘要句式「给予目标价486元」「目标价上调至人民币425.00元」
    targets: List[float] = []
    for match in re.finditer(r"目标价[^0-9]{0,14}([0-9]+(?:\.[0-9]+)?)\s*元", html):
        value = _num(match.group(1))
        if value and 0 < value < 100000:
            targets.append(value)
    unique_targets = sorted(set(targets))

    if org_count == 0 and not unique_targets:
        raise ValueError(f"同花顺机构预测无数据：{symbol}")
    return {
        "ratings": ratings,
        "org_count": org_count,
        "target_low": unique_targets[0] if unique_targets else None,
        "target_high": unique_targets[-1] if unique_targets else None,
        "target_avg": _round(sum(unique_targets) / len(unique_targets), 2) if unique_targets else None,
        "target_samples": len(unique_targets),
        "caliber_note": "同花顺评级分布 + 研报摘要目标价（区间取自摘要样本，非一致预期均值）",
    }


def get_forecast(symbol: str) -> Dict[str, Any]:
    """机构预测：评级分布 + 目标价区间。

    Args:
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    return _forecast_ths(symbol)


# =============================================================
# 六、机构一致预期（F-027：原版 FinScope analyst estimates 的等价实现）
# =============================================================
def _estimates_ths(symbol: str) -> Dict[str, Any]:
    """同花顺 worth.html 四张预测表 → 结构化一致预期。

    源页面为公开披露聚合（无需登录），akshare 内部 requests + pandas.read_html，
    不走 py_mini_racer；仍经 `_call` 串行包装以与其它同花顺调用保持同一纪律。

    口径（2026-10-21 实测 688012）：
      - eps：年度 / 预测机构数 / 最小 / 均值 / 最大 / 行业平均，单位 = 元/股；
      - profit：同结构，单位 = 亿元；
      - institutions：逐机构明细（机构/研究员/三年 EPS 与净利预测/报告日期），源仅列前 10 家；
      - detail：详细指标预测（营业收入、增长率等 9 项，含历史实际值 + 未来三年均值）。
    空表（如次新股无覆盖）返回空列表，由路由转空态，不伪造数据。

    Args:
        symbol: 6 位 A 股代码，如 "688012"。

    Returns:
        dict: 含 eps / profit / institutions / detail 四个列表与 caliber_note。

    Raises:
        ValueError: 四张表全部为空时抛出（交由上游转为空态或错误态）。
    """

    def _table(indicator: str) -> List[Dict[str, Any]]:
        """取单张预测表；上游返回空 DataFrame 时归一为空列表。

        Args:
            indicator: 指标名（如 预测年报每股收益）。

        Returns:
            list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。
        """
        frame = _call(lambda: ak.stock_profit_forecast_ths(symbol=str(symbol), indicator=indicator))
        if frame is None or len(frame) == 0:
            return []
        return utils.clean_records(frame)

    # 先探测页面是否有预测表：同花顺对「无效代码 / 无机构覆盖」返回 200 但无
    # `profit-forecast` 表（实测 000000 → 37KB 无表、999999 → 2.2KB 空页），
    # 此时 akshare 的 read_html 会抛解析异常，若直接透传会被误判为「通道故障」。
    probe = _call(lambda: utils.http_get_text(
        f"https://basic.10jqka.com.cn/{symbol}/worth.html",
        encoding="gbk", timeout=config.DATASOURCE_TIMEOUT,
    ))
    if "profit-forecast" not in probe:
        return {
            "eps": [], "profit": [], "institutions": [], "detail": [], "years": [],
            "available": False,
            "note": "该标的暂无机构预测覆盖（代码可能不存在或暂无机构覆盖）",
            "caliber_note": "同花顺盈利预测（页面无预测表）",
        }

    eps = _table("预测年报每股收益")
    profit = _table("预测年报净利润")
    institutions = _table("业绩预测详表-机构")
    detail = _table("业绩预测详表-详细指标预测")
    years = [row.get("年度") for row in eps if row.get("年度") is not None]
    if not (eps or profit or institutions or detail):
        # 空数据不是通道异常：按 §6.3 空/错分离，返回 available=false 的空结构，
        # 由前端渲染中文空态（不得伪造成错误码，也不得编造数值）。
        return {
            "eps": [], "profit": [], "institutions": [], "detail": [], "years": [],
            "available": False,
            "note": "该标的暂无机构预测覆盖（同花顺盈利预测四表均为空）",
            "caliber_note": "同花顺盈利预测（暂无机构覆盖）",
        }
    return {
        "eps": eps,
        "profit": profit,
        "institutions": institutions,
        "detail": detail,
        "years": years,
        "available": True,
        "caliber_note": "同花顺盈利预测（机构覆盖聚合：均值/区间/行业平均；机构明细仅列前 10 家）",
    }


def get_estimates(symbol: str, market: str = "a") -> Dict[str, Any]:
    """机构一致预期（分年度 EPS/净利润 + 机构明细 + 详细指标）。

    港美股无免费一致预期通道（境外简化建模，D3 口径），返回空结构由前端如实空态。

    Args:
        symbol: 归一化后的证券代码。
        market: a/hk/us。

    Returns:
        dict: eps / profit / institutions / detail 四列表 + years + caliber_note。
    """
    if market in ("hk", "us"):
        return {"eps": [], "profit": [], "institutions": [], "detail": [], "years": [],
                "available": False,
                "note": "港美股无免费机构一致预期通道（境外简化建模）",
                "caliber_note": "港美股无免费机构一致预期通道（境外简化建模）"}
    return _estimates_ths(symbol)



# =============================================================
# 六、财务四接口（ST-09）
# =============================================================
SUMMARY_METRICS: List[Tuple[str, str]] = [
    ("基本每股收益", "EPS(元)"),
    ("毛利率", "毛利率(%)"),
    ("净资产收益率(ROE)", "ROE(%)"),
    ("营业总收入", "营业总收入(元)"),
    ("归母净利润", "归母净利润(元)"),
    ("资产负债率", "资产负债率(%)"),
]


def get_financial_summary(symbol: str) -> Dict[str, Any]:
    """财务摘要（新浪 stock_financial_abstract）。

    实测结构：行=指标（含「常用指标/每股指标/盈利能力」分组），列=报告期（倒序）。
    取「常用指标」分组的 6 个关键指标 × 最近 8 个报告期。

    Args:
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。

    Raises:
        ValueError: 参数不合法或上游数据不满足口径时抛出。
    """
    _ensure_enabled()
    frame = _call(lambda: ak.stock_financial_abstract(symbol=str(symbol)))
    rows = utils.clean_records(frame)
    if not rows:
        raise ValueError(f"新浪财务摘要为空：{symbol}")

    period_cols = [key for key in rows[0].keys() if re.fullmatch(r"\d{8}", str(key))]
    period_cols = period_cols[:8]

    # 只取「常用指标」分组，避免同名字段在多个分组重复
    picked: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if str(row.get("选项") or "") != "常用指标":
            continue
        name = str(row.get("指标") or "").strip()
        picked[name] = row

    metrics: List[Dict[str, Any]] = []
    for raw_name, label in SUMMARY_METRICS:
        row = picked.get(raw_name)
        if not row:
            continue
        metrics.append(
            {
                "key": raw_name,
                "name": label,
                "values": [_num(row.get(period)) for period in period_cols],
            }
        )
    if not metrics:
        raise ValueError(f"新浪财务摘要缺少常用指标：{symbol}")
    return {"symbol": symbol, "periods": period_cols, "metrics": metrics}


def get_financial_trend(symbol: str, years: int = 5) -> Dict[str, Any]:
    """财务趋势四序列：营业总收入 / 净利润 / 营收增速 / 净利增速（年报口径）。

    数据源：新浪 stock_financial_report_sina（利润表，单位元）。

    Args:
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。
        years: 统计年数。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。

    Raises:
        ValueError: 参数不合法或上游数据不满足口径时抛出。
    """
    _ensure_enabled()
    frame = _call(lambda: ak.stock_financial_report_sina(stock=_sina_prefix(symbol), symbol="利润表"))
    rows = utils.clean_records(frame)
    if not rows:
        raise ValueError(f"新浪利润表为空：{symbol}")

    # 报告日形如 20260630；按年报筛选并升序排列，便于前端从左到右渲染
    rows = sorted(rows, key=lambda item: str(item.get("报告日") or ""))
    annual = [row for row in rows if str(row.get("报告日") or "").endswith("1231")][-years:]

    def series(key: str) -> List[Optional[float]]:
        """取出某字段的年度序列（元）。

        Args:
            key: 缓存键 / 字典键。

        Returns:
            list: List[Optional[float]] 结构的结果。
        """
        return [_num(row.get(key)) for row in annual]

    revenue = series("营业总收入")
    profit = series("净利润")

    def growth(values: List[Optional[float]]) -> List[Optional[float]]:
        """同比增速（%）；首年无上期基数返回 None，不补 0。

        Args:
            values: 数值列表。

        Returns:
            list: List[Optional[float]] 结构的结果。
        """
        result: List[Optional[float]] = []
        for index, value in enumerate(values):
            if index == 0 or value is None or values[index - 1] in (None, 0):
                result.append(None)
            else:
                result.append(round((value - values[index - 1]) / abs(values[index - 1]) * 100, 2))
        return result

    periods = [str(row.get("报告日") or "")[:8] for row in annual]
    return {
        "symbol": symbol,
        "periods": periods,
        "series": [
            {"key": "revenue", "name": "营业总收入", "unit": "元", "values": revenue},
            {"key": "profit", "name": "净利润", "unit": "元", "values": profit},
            {"key": "revenue_yoy", "name": "营收同比", "unit": "%", "values": growth(revenue)},
            {"key": "profit_yoy", "name": "净利同比", "unit": "%", "values": growth(profit)},
        ],
        "caliber_note": "年报口径（报告日 1231），新浪利润表",
    }


INDICATOR_KEYS: List[str] = [
    "摊薄每股收益(元)",
    "净资产收益率(%)",
    "销售毛利率(%)",
    "资产负债率(%)",
    "流动比率",
    "速动比率",
    "总资产周转率(次)",
    "应收账款周转天数(天)",
    "存货周转率(次)",
    "主营业务收入增长率(%)",
    "净利润增长率(%)",
]


def get_financial_indicator(symbol: str, limit: int = 8) -> Dict[str, Any]:
    """财务指标明细（新浪 stock_financial_analysis_indicator，最近 N 期）。

    Args:
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。
        limit: 返回条数上限（超出上限按上限截断）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。

    Raises:
        ValueError: 参数不合法或上游数据不满足口径时抛出。
    """
    _ensure_enabled()
    frame = _call(lambda: ak.stock_financial_analysis_indicator(symbol=str(symbol), start_year="2022"))
    rows = utils.clean_records(frame)
    if not rows:
        raise ValueError(f"新浪财务指标为空：{symbol}")
    rows = sorted(rows, key=lambda item: str(item.get("日期") or ""))[-limit:]
    periods = [str(row.get("日期") or "")[:10] for row in rows]
    metrics: List[Dict[str, Any]] = []
    for key in INDICATOR_KEYS:
        if key not in rows[0]:
            continue
        metrics.append(
            {
                "key": key,
                "name": key,
                "values": [_num(row.get(key)) for row in rows],
            }
        )
    return {"symbol": symbol, "periods": periods, "metrics": metrics}


REPORT_COLUMNS: List[str] = [
    "报告日",
    "营业总收入",
    "营业总成本",
    "营业利润",
    "利润总额",
    "净利润",
    "归属于母公司所有者的净利润",
    "基本每股收益",
    "资产总计",
    "负债合计",
    "所有者权益(或股东权益)合计",
    "经营活动产生的现金流量净额",
    "投资活动产生的现金流量净额",
    "筹资活动产生的现金流量净额",
    "货币资金",
    "应收账款",
    "存货",
    # 第七轮 M7（FCF 背离检查）：白名单只收常用列，但 FCF 需要资本开支与有息负债，
    # 新浪原始表其实都有（现金流量表 71 列 / 资产负债表 147 列），此处按需补齐。
    "购建固定资产、无形资产和其他长期资产所支付的现金",  # 资本开支（FCF 的减项）
    "短期借款",
    "长期借款",
    "一年内到期的非流动负债",
    "应付债券",
]


def get_financial_report(symbol: str, statement: str = "利润表", limit: int = 8) -> Dict[str, Any]:
    """三大报表（新浪 stock_financial_report_sina）。

    Args:
        symbol: 6 位代码。
        statement: 资产负债表 / 利润表 / 现金流量表。
        limit: 返回期数。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。

    Raises:
        ValueError: statement 仅支持资产负债表/利润表/现金流量表。
    """
    allowed = ("资产负债表", "利润表", "现金流量表")
    if statement not in allowed:
        raise ValueError("statement 仅支持资产负债表/利润表/现金流量表")
    _ensure_enabled()
    frame = _call(lambda: ak.stock_financial_report_sina(stock=_sina_prefix(symbol), symbol=statement))
    rows = utils.clean_records(frame)
    if not rows:
        raise ValueError(f"新浪{statement}为空：{symbol}")
    rows = sorted(rows, key=lambda item: str(item.get("报告日") or ""), reverse=True)[:limit]
    periods = [str(row.get("报告日") or "")[:8] for row in rows]
    # 只挑三表共有或该表存在的关键字段，避免 80+ 列全量下传
    available = [key for key in REPORT_COLUMNS if key in rows[0]]
    metrics = [
        {"key": key, "name": key, "values": [_num(row.get(key)) for row in rows]}
        for key in available
        if key != "报告日"
    ]
    return {"symbol": symbol, "statement": statement, "periods": periods, "metrics": metrics}


# =============================================================
# 七、FinScope 聚合面板（ST-01/02/03/04/05/06）
# =============================================================
def get_realtime_panel(symbol: str, market: str = "a") -> Dict[str, Any]:
    """FinScope 聚合：报价 / 日K / 分时 / 资金分布 / 相关新闻 / 机构预测。

    每块独立容错：单块失败记入 `blocks` 状态并返回 None，不让整页白屏，
    前端按块渲染空态或错误态（§7 四态就地渲染）。

    Args:
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    blocks: Dict[str, str] = {}
    panel: Dict[str, Any] = {"symbol": symbol, "market": market}

    try:
        panel["quote"] = tencent.get_quote(symbol, market)
        blocks["quote"] = "ok"
    except Exception as exc:  # noqa: BLE001
        panel["quote"] = None
        blocks["quote"] = f"failed:{type(exc).__name__}"
        logger.info("FinScope 报价失败：%s（%s）", symbol, type(exc).__name__)

    try:
        panel["kline"] = tencent.get_kline(symbol, market, "daily", "qfq", 160)
        blocks["kline"] = "ok"
    except Exception as exc:  # noqa: BLE001
        panel["kline"] = []
        blocks["kline"] = f"failed:{type(exc).__name__}"

    try:
        panel["minute"] = tencent.get_minute(symbol, market)
        blocks["minute"] = "ok"
    except Exception as exc:  # noqa: BLE001
        panel["minute"] = []
        blocks["minute"] = f"failed:{type(exc).__name__}"

    if market == "a":
        try:
            panel["fund_dist"] = get_fund_dist(symbol)
            blocks["fund_dist"] = "ok"
        except Exception as exc:  # noqa: BLE001
            panel["fund_dist"] = None
            blocks["fund_dist"] = f"failed:{type(exc).__name__}"

        try:
            panel["news"] = get_news(symbol, 10)
            blocks["news"] = "ok"
        except Exception as exc:  # noqa: BLE001
            panel["news"] = []
            blocks["news"] = f"failed:{type(exc).__name__}"

        try:
            panel["forecast"] = get_forecast(symbol)
            blocks["forecast"] = "ok"
        except Exception as exc:  # noqa: BLE001
            panel["forecast"] = None
            blocks["forecast"] = f"failed:{type(exc).__name__}"
    else:
        panel["fund_dist"] = None
        panel["news"] = []
        panel["forecast"] = None
        blocks["fund_dist"] = "unsupported:境外简化建模"
        blocks["news"] = "unsupported:D3 港美股个股新闻无免费通道"
        blocks["forecast"] = "unsupported:境外简化建模"

    panel["blocks"] = blocks
    return panel
