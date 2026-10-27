"""财报/公告搜索数据源聚合（新增功能 ⑥，2026-10-26）。

对应《设计基准_财报搜索_20261025.md》§7.1/§7.2 与《工作规范 v7.3》§8.2 九字段：
- A 股：巨潮资讯（主，POST hisAnnouncement/query）→ 东方财富公告（备，GET security/ann）；
- 港股：HKEX 披露易（prefix.do 查 stockId + titleSearchServlet.do 取官方返回）；
- 美股：SEC EDGAR（company_tickers.json 映射 CIK + submissions/CIK*.json，强制 UA）。

红线：
1. 九字段全部来自源站真实响应，不造数、不补零；源站无摘要字段时 summary 置空字符串，
   并在返回的 caliber 中写明「源站未提供摘要，请查看原文」；
2. 时间统一换算北京时间（UTC+8）；源站只精确到日的记录如实只显示日期，不虚构时分；
3. 同花顺公告无公开免鉴权 JSON，本模块不接入（接口说明书登记「需抓包/需鉴权」）。
"""

from __future__ import annotations

import json
import logging
import time
import re
import zlib
import difflib
import html as html_lib
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from backend.app import config
from backend.app.core import cache, netfix
from backend.app.datasource import utils

logger = logging.getLogger(__name__)

# 北京时区（UTC+8）：全模块唯一时间口径，直接引用 utils 的常量，避免多处各定义一份
_BJ = utils.BEIJING_TZ

# 列表缓存 5 分钟：财报/公告属事件驱动数据，短 TTL 兼顾时效与源站限频（规范 §6.8）
_LIST_TTL = 300
# 单页条数：与前端 FS-02 的「加载更多」步长一致
_PAGE_SIZE = 20
# 港股/美股在源站不支持服务端分页，一次取回上限后本地切片
_LOCAL_FETCH = 100

# 前端类型 chips 的合法取值（顺序与设计基准 §5.3 一致）
TYPE_VALUES: Tuple[str, ...] = (
    "全部", "年报", "半年报", "一季报", "三季报", "业绩预告", "招股书", "10-K", "10-Q", "8-K", "其他",
)
# 市场枚举：all=三市场合并
MARKET_VALUES: Tuple[str, ...] = utils.MARKET_WHITELIST_ALL
# 时间范围枚举（近 N 天）
SINCE_VALUES: Tuple[str, ...] = ("all", "7", "30", "90", "365")

_CNINFO_TOP = "https://www.cninfo.com.cn/new/information/topSearch/query"
_CNINFO_QUERY = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
_CNINFO_STATIC = "https://static.cninfo.com.cn/"
_EM_ANN = "https://np-anotice-stock.eastmoney.com/api/security/ann"
_EM_DETAIL = "https://data.eastmoney.com/notices/detail/"
_HKEX_PREFIX = "https://www1.hkexnews.hk/search/prefix.do"
_HKEX_SEARCH = "https://www1.hkexnews.hk/search/titleSearchServlet.do"
_HKEX_BASE = "https://www1.hkexnews.hk"
_EDGAR_TICKERS = "https://www.sec.gov/files/company_tickers.json"
_EDGAR_SUB = "https://data.sec.gov/submissions/CIK{cik}.json"
_EDGAR_ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
# EDGAR 强制要求可识别 UA，否则返回 403（设计基准 §7.2）
_EDGAR_UA = "OpenBB-CN/1.0.5 (local research tool; contact: local-user)"

# 中文/英文常用名 → 代码（阶段 8.1）：HKEX prefix 仅认代码/繁体/英文，
# EDGAR 仅认 ticker/英文名，故内置常用别名做一次归一；未命中时再走源站原名检索，
# 仍失败则返回带示例代码的中文提示（绝不静默返回空）。
_HK_NAME_ALIASES: Dict[str, str] = {
    "腾讯": "00700", "腾讯控股": "00700", "阿里": "09988", "阿里巴巴": "09988",
    "美团": "03690", "小米": "01810", "小米集团": "01810", "比亚迪": "01211",
    "京东": "09618", "京东集团": "09618", "网易": "09999", "快手": "01024",
    "中芯国际": "00981", "中芯": "00981", "中国移动": "00941", "工商银行": "01398",
    "建设银行": "00939", "中国平安": "02318", "招商银行": "03968", "理想汽车": "02015",
    "小鹏汽车": "09868", "蔚来": "09866", "百度": "09888", "百度集团": "09888",
    "商汤": "00020", "联想": "00992", "联想集团": "00992", "中兴通讯": "00763",
    "中海油": "00883", "中国海洋石油": "00883", "药明生物": "02269", "农夫山泉": "09633",
    "香港交易所": "00388", "汇丰": "00005", "汇丰控股": "00005",
}
_HK_EN_ALIASES: Dict[str, str] = {
    "tencent": "00700", "alibaba": "09988", "meituan": "03690", "xiaomi": "01810",
    "byd": "01211", "jd": "09618", "netease": "09999", "kuaishou": "01024",
    "smic": "00981", "china mobile": "00941", "icbc": "01398", "ccb": "00939",
    "ping an": "02318", "cmb": "03968", "li auto": "02015", "xpeng": "09868",
    "nio": "09866", "baidu": "09888", "lenovo": "00992", "zte": "00763",
    "cnooc": "00883", "wuxi biologies": "02269", "nongfu": "09633", "hkex": "00388",
    "hsbc": "00005",
}
_US_NAME_ALIASES: Dict[str, str] = {
    "苹果": "AAPL", "微软": "MSFT", "英伟达": "NVDA", "特斯拉": "TSLA",
    "谷歌": "GOOGL", "亚马逊": "AMZN", "脸书": "META", "台积电": "TSM",
    "博通": "AVGO", "英特尔": "INTC", "甲骨文": "ORCL", "超微": "AMD",
    "超威": "AMD", "美光": "MU", "高通": "QCOM", "奈飞": "NFLX",
    "腾讯": "TCEHY", "阿里": "BABA", "阿里巴巴": "BABA", "京东": "JD",
    "拼多多": "PDD", "百度": "BIDU", "网易": "NTES", "蔚来": "NIO",
    "小鹏": "XPEV", "理想": "LI", "哔哩哔哩": "BILI", "贝壳": "BEKE",
    "沃尔玛": "WMT", "可口可乐": "KO", "摩根大通": "JPM", "伯克希尔": "BRK-B",
}


def _hk_alias_code(keyword: str) -> str:
    """把中文/英文常用名归一为 5 位港股代码。

    Args:
        keyword: 用户输入（代码 / 中文简称 / 英文名）。

    Returns:
        str: 5 位代码；无法归一返回空串。
    """
    text = str(keyword or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"\d{1,5}", text):
        return text.zfill(5)
    if text in _HK_NAME_ALIASES:
        return _HK_NAME_ALIASES[text]
    lowered = text.lower()
    if lowered in _HK_EN_ALIASES:
        return _HK_EN_ALIASES[lowered]
    for name, code in _HK_NAME_ALIASES.items():
        if name and name in text:  # "腾讯控股" 包含 "腾讯"
            return code
    return ""


def _us_alias_ticker(keyword: str) -> str:
    """把中文常用名 / 英文小写名归一为美股 ticker。

    Args:
        keyword: 用户输入。

    Returns:
        str: ticker（大写）；无法归一返回空串。
    """
    text = str(keyword or "").strip()
    if not text:
        return ""
    if text in _US_NAME_ALIASES:
        return _US_NAME_ALIASES[text]
    return ""

# 巨潮反爬较严：两次请求之间至少间隔 1.5s（设计基准 §7.2）
_CNINFO_MIN_GAP = 1.5
_cninfo_last_call = 0.0  # 模块级节流时间戳（秒）

# EDGAR 全量 ticker→CIK 映射缓存（24 小时），避免每次检索都拉 800KB
_TICKERS_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}


def _bj_now_text() -> str:
    """取当前北京时间的标准格式串。

    Returns:
        str: ``YYYY-MM-DD HH:MM:SS``（北京时间）。
    """
    return utils.now_text()  # TZ-06：统一走 utils 单一助手


def _since_start(since: str) -> Optional[date]:
    """把「近 N 天」换算为起始日期。

    Args:
        since: ``all`` / ``7`` / ``30`` / ``90`` / ``365``。

    Returns:
        Optional[date]: 起始日期；``all`` 返回 None（不限起始）。
    """
    if since not in SINCE_VALUES or since == "all":
        return None
    return (utils.bj_now() - timedelta(days=int(since))).date()


def _classify_cn(title: str) -> str:
    """按标题把 A 股/港股公告归类为前端类型 chips。

    Args:
        title: 公告标题原文（简体或繁体）。

    Returns:
        str: 类型；无法归类返回「其他」。

    Notes:
        源站不提供统一 type 字段，只能按标题关键词归类；归类失败不猜造，一律「其他」。
    """
    text = title or ""
    # 繁体先归一到简体关键词，避免港股标题漏判
    text = text.replace("年報", "年报").replace("半年報", "半年报")
    text = text.replace("業績", "业绩").replace("招股章程", "招股")
    text = text.replace("中期報告", "半年度报告").replace("季度報告", "季度报告")
    if "业绩预告" in text or "业绩预增" in text or "业绩预亏" in text or "盈利警告" in text:
        return "业绩预告"
    if "招股" in text:
        return "招股书"
    if "半年度报告" in text or "中期报告" in text:
        return "半年报"
    if "年度报告" in text:
        return "年报"
    if "第一季度报告" in text or "一季度报告" in text:
        return "一季报"
    if "第三季度报告" in text or "三季度报告" in text:
        return "三季报"
    return "其他"


def _classify_us(form: str) -> str:
    """把 SEC 申报表种映射为前端类型 chips。

    Args:
        form: EDGAR 的 form 字段（如 10-K / 10-Q / 8-K）。

    Returns:
        str: 类型；非定期报告与常见事件表一律「其他」。
    """
    normalized = (form or "").upper().strip()
    if normalized in ("10-K", "10-Q", "8-K"):
        return normalized
    return "其他"


def _in_range(pub: str, since: str) -> bool:
    """判断发布时间是否落在「近 N 天」窗口内。

    Args:
        pub: 北京时间字符串，形如 ``2026-10-22`` 或 ``2026-10-22 12:03``。
        since: 时间范围枚举值。

    Returns:
        bool: 在窗口内（或 since=all）返回 True。
    """
    start = _since_start(since)
    if start is None:
        return True
    try:
        day = datetime.strptime(pub[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return True  # 源站时间不可解析时不做窗口过滤，避免误丢真实记录
    return day >= start


def _row(
    *,
    title: str,
    company: str,
    code: str,
    market: str,
    type_value: str,
    source: str,
    pub_time: str,
    doc_url: str,
    summary: str = "",
) -> Dict[str, str]:
    """组装九字段记录（§8.2）。

    Args:
        title: 公告标题。
        company: 公司名称。
        code: 证券代码。
        market: a/hk/us。
        type_value: 财报类型。
        source: 实际命中的源站名。
        pub_time: 北京时间字符串。
        doc_url: 原文链接。
        summary: 摘要；源站不提供时为空字符串。

    Returns:
        Dict[str, str]: 九字段记录。
    """
    return {
        "title": title.strip(),
        "company": company.strip(),
        "code": code.strip(),
        "market": market,
        "type": type_value,
        "source": source,
        "pubTime": pub_time,
        "docUrl": doc_url,
        "summary": summary,
    }


# =============================================================
# 一、A 股：巨潮资讯（主）→ 东方财富公告（备）
# =============================================================


def _cninfo_throttle() -> None:
    """巨潮请求节流：两次请求之间至少间隔 1.5 秒，降低 403 概率。"""
    global _cninfo_last_call
    gap = time.time() - _cninfo_last_call
    if gap < _CNINFO_MIN_GAP:
        time.sleep(_CNINFO_MIN_GAP - gap)
    _cninfo_last_call = time.time()


def _cninfo_resolve(keyword: str) -> List[Dict[str, str]]:
    """用巨潮 topSearch 把代码/简称解析为 (code, orgId) 候选。

    Args:
        keyword: 6 位代码或公司简称。

    Returns:
        List[Dict[str, str]]: 形如 ``[{"code": "600519", "orgId": "gssh0600519", "name": "贵州茅台"}]``；
        无匹配返回空列表。
    """
    _cninfo_throttle()
    resp = netfix.http_request(
        "POST",
        _CNINFO_TOP,
        data={"keyWord": keyword, "maxNum": 10},
        headers={"Referer": "https://www.cninfo.com.cn/new/disclosure"},
        timeout=config.DATASOURCE_TIMEOUT,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"巨潮代码检索 HTTP {resp.status_code}")
    items = resp.json() or []
    out: List[Dict[str, str]] = []
    for item in items:
        if str(item.get("category", "")).find("A股") < 0:
            continue
        code = str(item.get("code", "")).strip()
        org_id = str(item.get("orgId", "")).strip()
        if code and org_id:
            out.append({"code": code, "orgId": org_id, "name": str(item.get("zwjc", "")).strip()})
        if len(out) >= 3:  # 名称模糊匹配最多取前 3 家，避免一次检索发太多请求
            break
    return out


def _cninfo_query(
    *,
    stock: str,
    searchkey: str,
    page: int,
    page_size: int,
    since: str,
) -> Tuple[List[Dict[str, str]], int, str]:
    """调用巨潮历史公告查询接口。

    Args:
        stock: ``代码,orgId`` 组合；空串表示全市场。
        searchkey: 全市场关键词（仅在不带 stock 时生效）。
        page: 页码（从 1 开始）。
        page_size: 每页条数。
        since: 时间范围枚举值。

    Returns:
        Tuple[List[Dict[str, str]], int, str]: (记录列表, 源站总命中数, 口径说明)。
    """
    start = _since_start(since)
    se_date = ""
    if start is not None:
        se_date = f"{start.isoformat()}~{utils.today_bj().isoformat()}"
    _cninfo_throttle()
    resp = netfix.http_request(
        "POST",
        _CNINFO_QUERY,
        data={
            "pageNum": page,
            "pageSize": min(page_size, 30),  # 巨潮上限 30
            "column": "szse",
            "tabName": "fulltext",
            "stock": stock,
            "searchkey": searchkey,
            "seDate": se_date,
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
        },
        headers={"Referer": "https://www.cninfo.com.cn/new/disclosure"},
        timeout=config.DATASOURCE_TIMEOUT,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"巨潮公告查询 HTTP {resp.status_code}")
    payload = resp.json() or {}
    total = int(payload.get("totalAnnouncement") or 0)
    rows: List[Dict[str, str]] = []
    for item in payload.get("announcements") or []:
        title = str(item.get("announcementTitle") or "").replace("<em>", "").replace("</em>", "")
        adjunct = str(item.get("adjunctUrl") or "").strip()
        stamp_ms = item.get("announcementTime")
        # 巨潮 announcementTime 为毫秒时间戳；多数记录只精确到日（UTC 00:00），此处如实降级为日期
        pub = ""
        if isinstance(stamp_ms, (int, float)) and stamp_ms > 0:
            moment = datetime.fromtimestamp(stamp_ms / 1000, _BJ)
            # 巨潮对多数记录只在整点零点给出日期（时分秒全 0）：此时只显示日期，不虚构时刻
            only_day = moment.minute == 0 and moment.second == 0
            pub = moment.strftime("%Y-%m-%d") if only_day else moment.strftime("%Y-%m-%d %H:%M")
        rows.append(
            _row(
                title=title,
                company=str(item.get("secName") or ""),
                code=str(item.get("secCode") or ""),
                market="a",
                type_value=_classify_cn(title),
                source="巨潮资讯",
                pub_time=pub,
                doc_url=f"{_CNINFO_STATIC}{adjunct}" if adjunct else "",
            )
        )
    return rows, total, "巨潮资讯法定披露原文，按披露时间倒序；部分记录源站仅精确到日，故只显示日期；列表接口不提供摘要（摘要需打开原文），港股/美股摘要见各自口径"


def _em_query(
    *,
    stock_list: str,
    page: int,
    page_size: int,
) -> Tuple[List[Dict[str, str]], int]:
    """调用东方财富公告接口（A 股备源 / 全市场）。

    Args:
        stock_list: 6 位代码；空串表示全市场。
        page: 页码（从 1 开始）。
        page_size: 每页条数。

    Returns:
        Tuple[List[Dict[str, str]], int]: (记录列表, 源站总命中数)。
    """
    resp = netfix.http_request(
        "GET",
        _EM_ANN,
        params={
            "sr": -1,
            "page_size": page_size,
            "page_index": page,
            "ann_type": "A",
            "client_source": "web",
            "stock_list": stock_list,
            "f_node": 0,
            "s_node": 0,
        },
        headers={"Referer": "https://data.eastmoney.com/notices/"},
        timeout=config.DATASOURCE_TIMEOUT,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"东财公告 HTTP {resp.status_code}")
    body = resp.json() or {}
    data = body.get("data") or {}
    total = int(data.get("total_hits") or 0)
    rows: List[Dict[str, str]] = []
    for item in data.get("list") or []:
        title = str(item.get("title") or "")
        code = ""
        company = ""
        codes = item.get("codes") or []
        if codes:
            code = str(codes[0].get("stock_code") or "")
            company = str(codes[0].get("short_name") or "")
        # display_time 形如 2026-08-14 20:41:29:276，取前 16 位即北京时间分钟精度
        display = str(item.get("display_time") or "")
        pub = display[:16] if len(display) >= 16 else str(item.get("notice_date") or "")[:10]
        art = str(item.get("art_code") or "")
        rows.append(
            _row(
                title=title.replace(f"{company}:", "").strip(":： "),
                company=company,
                code=code,
                market="a",
                type_value=_classify_cn(title),
                source="东方财富",
                pub_time=pub,
                doc_url=f"{_EM_DETAIL}{art}.html" if art else "",
            )
        )
    return rows, total


def search_cn(*, q: str, type_value: str, since: str, page: int, page_size: int) -> Tuple[List[Dict[str, str]], int, str, str]:
    """A 股检索：巨潮主源失败自动切东财备源。

    Args:
        q: 关键词（公司简称/代码/空）。
        type_value: 类型过滤值（「全部」表示不过滤）。
        since: 时间范围枚举值。
        page: 页码。
        page_size: 每页条数。

    Returns:
        Tuple[List[Dict[str, str]], int, str, str]: (记录, 总命中, 命中源名, 口径说明)。
    """
    keyword = (q or "").strip()
    resolved: List[Dict[str, str]] = []
    if keyword:
        try:
            resolved = _cninfo_resolve(keyword)
        except Exception as exc:  # noqa: BLE001 - 解析失败直接走备源，不阻断
            logger.warning("巨潮代码解析失败，转东财备源：%s", exc)
            resolved = []

    if keyword and not resolved:
        # 巨潮查不到该主体时，东财可用「代码」直查；名称无法直查，如实返回空
        if keyword.isdigit() and len(keyword) == 6:
            try:
                rows, total = _em_query(stock_list=keyword, page=page, page_size=page_size)
            except Exception as exc:  # noqa: BLE001
                logger.warning("东财公告查询失败：%s", exc)
                return [], 0, "东方财富", "A 股公告源暂不可用（巨潮未命中且东财连接异常），请稍后重试"
            filtered = [r for r in rows if type_value == "全部" or r["type"] == type_value]
            return filtered, total, "东方财富", "东财公告库（A 股），巨潮未命中该代码，已切备源"
        return [], 0, "巨潮资讯", "巨潮未检索到与该关键词匹配的 A 股上市公司，请改用公司简称或 6 位代码"

    all_rows: List[Dict[str, str]] = []
    total_hits = 0
    source_used = "巨潮资讯"
    caliber = ""
    try:
        if resolved:
            # 多候选逐家查询（最多 3 家），每家取当前页
            for idx, item in enumerate(resolved):
                if idx:  # 该轮已由 _cninfo_query 内部节流，这里补充跨候选间隔
                    time.sleep(_CNINFO_MIN_GAP)
                rows, total, note = _cninfo_query(
                    stock=f"{item['code']},{item['orgId']}",
                    searchkey="",
                    page=page,
                    page_size=page_size,
                    since=since,
                )
                all_rows.extend(rows)
                total_hits += total
                caliber = note
        else:
            rows, total, note = _cninfo_query(
                stock="", searchkey="", page=page, page_size=page_size, since=since
            )
            all_rows = rows
            total_hits = total
            caliber = note
    except Exception as exc:  # noqa: BLE001 - 主源失败切备源
        logger.warning("巨潮公告查询失败，切东财备源：%s", exc)
        try:
            stock_list = resolved[0]["code"] if resolved else (keyword if keyword.isdigit() else "")
            rows, total = _em_query(stock_list=stock_list, page=page, page_size=page_size)
        except Exception as exc2:  # noqa: BLE001
            logger.warning("东财公告备源亦失败：%s", exc2)
            return [], 0, "东方财富", "A 股公告源暂不可用（巨潮与东财均连接异常），请稍后点击重试"
        all_rows = rows
        total_hits = total
        source_used = "东方财富"
        caliber = "巨潮限频或不可用，已自动切换东财公告备源（披露时间取东财 display_time）"

    filtered = [r for r in all_rows if type_value == "全部" or r["type"] == type_value]
    filtered = [r for r in filtered if _in_range(r["pubTime"] or "", since)]
    return filtered, total_hits, source_used, caliber or "巨潮资讯法定披露原文"


# =============================================================
# 二、港股：HKEX 披露易
# =============================================================


def _hkex_stock_id(keyword: str) -> Optional[Dict[str, str]]:
    """用 HKEX prefix.do 把代码/简称解析为 stockId。

    Args:
        keyword: 港股代码（可 4~5 位）或公司简称。

    Returns:
        Optional[Dict[str, str]]: ``{"stockId": "7609", "code": "00700", "name": "騰訊控股"}``；无匹配返回 None。
    """
    # 阶段 8.1：简体中文名先归一为 5 位代码（HKEX prefix 仅认代码/繁体/英文）
    lookup = _hk_alias_code(keyword) or keyword
    resp = netfix.http_request(
        "GET",
        _HKEX_PREFIX,
        params={"callback": "cb", "lang": "ZH", "type": "A", "name": lookup, "market": "SEHK"},
        headers={"Referer": "https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=zh"},
        timeout=config.DATASOURCE_TIMEOUT,
    )
    if resp.status_code != 200:
        return None
    text = resp.text.strip()
    # 返回为 JSONP：callback({...})
    start = text.find("(")
    end = text.rfind(")")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start + 1:end])
    except json.JSONDecodeError:
        return None
    items = payload.get("stockInfo") or []
    if not items:
        return None
    first = items[0]
    return {
        "stockId": str(first.get("stockId") or ""),
        "code": str(first.get("code") or ""),
        "name": str(first.get("name") or ""),
    }


def search_hk(*, q: str, type_value: str, since: str, page: int, page_size: int) -> Tuple[List[Dict[str, str]], int, str, str]:
    """港股检索：HKEX 披露易标题检索（需先解析 stockId）。

    Args:
        q: 关键词（公司简称/5 位代码/空）。
        type_value: 类型过滤值。
        since: 时间范围枚举值。
        page: 页码。
        page_size: 每页条数。

    Returns:
        Tuple[List[Dict[str, str]], int, str, str]: (记录, 总命中, 命中源名, 口径说明)。
    """
    keyword = (q or "").strip()
    if not keyword:
        return [], 0, "HKEX披露易", "港股检索需输入公司名称或 5 位代码（如 00700、腾讯控股）；HKEX 不支持无关键词的全市场检索"
    try:
        target = _hkex_stock_id(keyword)
    except Exception as exc:  # noqa: BLE001
        logger.warning("HKEX 代码解析失败：%s", exc)
        return [], 0, "HKEX披露易", "HKEX 代码解析暂不可用（网络异常或限频），请稍后重试"
    if not target:
        return ([], 0, "HKEX披露易",
                f"港股未匹配到「{keyword}」：请尝试 5 位代码（如 00700、09988）或繁体/英文全称"
                "（HKEX 仅认代码、繁体名与英文名）")

    start = _since_start(since) or (utils.today_bj() - timedelta(days=365))
    resp = netfix.http_request(
        "GET",
        _HKEX_SEARCH,
        params={
            "sortDir": "0",
            "sortByOptions": "DateTime",
            "category": "0",
            "market": "SEHK",
            "stockId": target["stockId"],
            "documentType": "-1",
            "fromDate": start.strftime("%Y%m%d"),
            "toDate": utils.bj_now().strftime("%Y%m%d"),
            "title": "",
            "searchType": "0",
            "t1code": "-2",
            "t2Gcode": "-2",
            "t2code": "-2",
            "rowRange": str(_LOCAL_FETCH),
            "lang": "ZH",
        },
        headers={"Referer": "https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=zh"},
        timeout=config.DATASOURCE_TIMEOUT,
    )
    if resp.status_code != 200:
        return [], 0, "HKEX披露易", f"HKEX 检索接口 HTTP {resp.status_code}，请稍后重试"
    payload = resp.json() or {}
    try:
        items = json.loads(payload.get("result") or "[]")
    except json.JSONDecodeError:
        items = []

    rows: List[Dict[str, str]] = []
    for item in items:
        title = str(item.get("TITLE") or "").replace("<br/>", " ").strip()
        code = str(item.get("STOCK_CODE") or "").split("<br/>")[0].strip()
        company = str(item.get("STOCK_NAME") or "").split("<br/>")[0].strip()
        # DATE_TIME 形如 22/10/2026 12:03（香港时间 = 北京时间）
        raw_time = str(item.get("DATE_TIME") or "").strip()
        pub = ""
        try:
            pub = datetime.strptime(raw_time, "%d/%m/%Y %H:%M").strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pub = raw_time
        link = str(item.get("FILE_LINK") or "").strip()
        # 阶段 8.2：HKEX 不提供正文摘要，源站唯一的描述字段是公告类别（SHORT_TEXT），
        # 如实填入 summary 并在 caliber 注明口径，避免前端把「无摘要」误显示成缺失。
        summary = str(item.get("SHORT_TEXT") or "").replace("<br/>", " ").strip()
        summary = re.sub(r"\s+", " ", summary)
        rows.append(
            _row(
                title=title,
                company=company,
                code=code,
                market="hk",
                type_value=_classify_cn(title),
                source="HKEX披露易",
                pub_time=pub,
                doc_url=f"{_HKEX_BASE}{link}" if link else "",
                summary=summary,
            )
        )
    filtered = [r for r in rows if type_value == "全部" or r["type"] == type_value]
    filtered = [r for r in filtered if _in_range(r["pubTime"] or "", since)]
    sliced = filtered[(page - 1) * page_size: page * page_size]
    return (sliced, len(filtered), "HKEX披露易",
            "港交所披露易原文（繁体标题按披露原文展示），时间为香港时间＝北京时间；"
            "摘要为源站公告类别（HKEX 不提供正文摘要），正文请打开原文")


# =============================================================
# 三、美股：SEC EDGAR
# =============================================================


def _edgar_tickers() -> Dict[str, Dict[str, Any]]:
    """取 EDGAR 全量 ticker→CIK 映射（内存缓存 24 小时）。

    Returns:
        Dict[str, Dict[str, Any]]: 形如 ``{"NVDA": {"cik_str": 1045810, "title": "NVIDIA CORP"}}``。
    """
    now = time.time()
    cached = _TICKERS_CACHE.get("data")
    if cached is not None and now - float(_TICKERS_CACHE.get("ts") or 0) < 86400:
        return cached
    resp = netfix.http_request(
        "GET", _EDGAR_TICKERS, headers={"User-Agent": _EDGAR_UA}, timeout=config.DATASOURCE_TIMEOUT
    )
    if resp.status_code != 200:
        raise RuntimeError(f"EDGAR ticker 映射 HTTP {resp.status_code}")
    raw = resp.json() or {}
    mapping: Dict[str, Dict[str, Any]] = {}
    for item in raw.values():
        ticker = str(item.get("ticker") or "").upper().strip()
        if ticker:
            mapping[ticker] = {
                "cik_str": int(item.get("cik_str") or 0),
                "title": str(item.get("title") or ""),
            }
    _TICKERS_CACHE["ts"] = now
    _TICKERS_CACHE["data"] = mapping
    return mapping


def _edgar_resolve(keyword: str) -> Optional[Dict[str, Any]]:
    """把 ticker 或公司英文名解析为 EDGAR 申报人。

    Args:
        keyword: ticker（如 NVDA）或英文公司名片段。

    Returns:
        Optional[Dict[str, Any]]: ``{"ticker": "NVDA", "cik": 1045810, "title": "NVIDIA CORP"}``；无匹配返回 None。
    """
    mapping = _edgar_tickers()
    alias = _us_alias_ticker(keyword)
    if alias and alias in mapping:
        item = mapping[alias]
        return {"ticker": alias, "cik": item["cik_str"], "title": item["title"]}
    upper = keyword.upper().strip()
    if upper in mapping:
        item = mapping[upper]
        return {"ticker": upper, "cik": item["cik_str"], "title": item["title"]}
    # 退化为公司名包含匹配（仅取第一个命中，避免歧义时拼造结果）
    lowered = keyword.lower().strip()
    for ticker, item in mapping.items():
        if lowered and lowered in item["title"].lower():
            return {"ticker": ticker, "cik": item["cik_str"], "title": item["title"]}
    return None


def search_us(*, q: str, type_value: str, since: str, page: int, page_size: int) -> Tuple[List[Dict[str, str]], int, str, str]:
    """美股检索：SEC EDGAR 官方申报（无备源）。

    Args:
        q: 关键词（ticker/英文公司名/空）。
        type_value: 类型过滤值。
        since: 时间范围枚举值。
        page: 页码。
        page_size: 每页条数。

    Returns:
        Tuple[List[Dict[str, str]], int, str, str]: (记录, 总命中, 命中源名, 口径说明)。

    Raises:
        RuntimeError: EDGAR 连接失败（无备源，由上层转错误态）。
    """
    keyword = (q or "").strip()
    if not keyword:
        return [], 0, "SEC EDGAR", "美股检索需输入 ticker（如 NVDA、AAPL）或英文公司名；EDGAR 不支持无关键词的全市场检索"
    target = _edgar_resolve(keyword)
    if not target:
        return ([], 0, "SEC EDGAR",
                f"美股未匹配到「{keyword}」：请尝试 ticker（如 AAPL、NVDA、TCEHY）或英文公司名"
                "（EDGAR 仅认 ticker 与英文申报人名称）")

    cik = f"{int(target['cik']):010d}"
    resp = netfix.http_request(
        "GET", _EDGAR_SUB.format(cik=cik), headers={"User-Agent": _EDGAR_UA}, timeout=config.DATASOURCE_TIMEOUT
    )
    if resp.status_code != 200:
        raise RuntimeError(f"EDGAR submissions HTTP {resp.status_code}")
    body = resp.json() or {}
    recent = (body.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accept = recent.get("acceptanceDateTime") or []
    accessions = recent.get("accessionNumber") or []
    documents = recent.get("primaryDocument") or []
    descriptions = recent.get("primaryDocDescription") or []   # 阶段 8.2：表种描述（如 FORM 4 / 10-K）
    item_nos = recent.get("items") or []                        # 8-K 项目编号（如 2.02,9.01）

    rows: List[Dict[str, str]] = []
    for idx, form in enumerate(forms):
        type_value_row = _classify_us(str(form))
        if type_value_row == "其他":
            continue  # 只收录定期报告与 8-K 事件表，其余（内部人持股等）不进财报搜索
        if type_value != "全部" and type_value_row != type_value:
            continue
        # acceptanceDateTime 为 UTC（如 2026-10-13T16:30:00.000Z），换算北京时间
        pub = str(dates[idx] if idx < len(dates) else "")
        acc_dt = str(accept[idx] if idx < len(accept) else "")
        if acc_dt:
            try:
                moment = datetime.fromisoformat(acc_dt.replace("Z", "+00:00")).astimezone(_BJ)
                pub = moment.strftime("%Y-%m-%d %H:%M")
            except ValueError:
                pass
        if not _in_range(pub, since):
            continue
        acc_no = str(accessions[idx] if idx < len(accessions) else "").replace("-", "")
        doc = str(documents[idx] if idx < len(documents) else "")
        doc_url = _EDGAR_ARCHIVE.format(cik=int(target["cik"]), acc=acc_no, doc=doc) if acc_no and doc else ""
        title = f"{target['title']} · {form} 申报（{pub[:10]}）"  # EDGAR 无标题字段，按表种+日期如实拼接
        # 阶段 8.2：EDGAR 无正文摘要，用源站 primaryDocDescription（表种描述）+ items（8-K 项目）
        desc = str(descriptions[idx] if idx < len(descriptions) else "").strip()
        item_no = str(item_nos[idx] if idx < len(item_nos) else "").strip()
        summary = desc
        if item_no:
            summary = f"{desc} · 项目 {item_no}" if desc else f"8-K 项目 {item_no}"
        rows.append(
            _row(
                title=title,
                company=str(body.get("name") or target["title"]),
                code=str(target["ticker"]),
                market="us",
                type_value=type_value_row,
                source="SEC EDGAR",
                pub_time=pub,
                doc_url=doc_url,
                summary=summary,
            )
        )
    rows.sort(key=lambda r: r["pubTime"], reverse=True)
    sliced = rows[(page - 1) * page_size: page * page_size]
    return (
        sliced,
        len(rows),
        "SEC EDGAR",
        "SEC EDGAR 官方申报原文；发布时间由 acceptanceDateTime(UTC) 换算北京时间，美东盘后申报对应北京次日凌晨；"
        "摘要为源站表种描述（primaryDocDescription）与 8-K 项目编号，EDGAR 不提供正文摘要",
    )


# =============================================================
# 四、聚合入口
# =============================================================


def search_filings(
    *,
    market: str = "all",
    q: str = "",
    type_value: str = "全部",
    since: str = "all",
    page: int = 1,
    page_size: int = _PAGE_SIZE,
) -> Dict[str, Any]:
    """财报/公告聚合检索（§8.2 九字段 + §8.8 接口模板）。

    Args:
        market: all/a/hk/us。
        q: 关键词（公司名/代码/空）。
        type_value: 类型过滤值。
        since: 时间范围枚举值。
        page: 页码（从 1 开始）。
        page_size: 每页条数。

    Returns:
        Dict[str, Any]: ``{list, total, source, caliber, as_of}``；源站无摘要时 summary 为空串。

    Notes:
        三市场任一失败不整体失败：A 股有备源，港股/美股失败时把原因写入 caliber，
        由前端展示为「该市场未出数 + 原因」，绝不补零或造数。
    """
    market = market if market in MARKET_VALUES else "all"
    type_value = type_value if type_value in TYPE_VALUES else "全部"
    since = since if since in SINCE_VALUES else "all"
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or _PAGE_SIZE), 50))

    key = f"finsearch:{market}:{q.strip()}:{type_value}:{since}:{page}:{page_size}"

    def _load() -> Dict[str, Any]:
        rows: List[Dict[str, str]] = []
        total = 0
        sources: List[str] = []
        notes: List[str] = []

        if market in ("all", "a"):
            try:
                part, part_total, src, note = search_cn(
                    q=q, type_value=type_value, since=since, page=page, page_size=page_size
                )
                rows.extend(part)
                total += part_total
                sources.append(src)
                if note:
                    notes.append(note)
            except Exception as exc:  # noqa: BLE001 - 单市场失败不影响其它市场
                logger.warning("A 股财报检索失败：%s", exc)
                notes.append("A 股检索失败（巨潮/东财均异常），请稍后重试")

        if market in ("all", "hk"):
            try:
                part, part_total, src, note = search_hk(
                    q=q, type_value=type_value, since=since, page=page, page_size=page_size
                )
                rows.extend(part)
                total += part_total
                if part:
                    sources.append(src)
                if note:
                    notes.append(note)
            except Exception as exc:  # noqa: BLE001
                logger.warning("港股财报检索失败：%s", exc)
                notes.append("港股检索失败（HKEX 连接异常），请稍后重试")

        if market in ("all", "us"):
            try:
                part, part_total, src, note = search_us(
                    q=q, type_value=type_value, since=since, page=page, page_size=page_size
                )
                rows.extend(part)
                total += part_total
                if part:
                    sources.append(src)
                if note:
                    notes.append(note)
            except Exception as exc:  # noqa: BLE001
                logger.warning("美股财报检索失败：%s", exc)
                notes.append("美股检索失败（SEC EDGAR 连接异常，无备用源），请稍后重试")

        # 统一按发布时间倒序；无时间的记录排最后
        rows.sort(key=lambda r: (r.get("pubTime") or ""), reverse=True)
        source_text = " · ".join(dict.fromkeys(sources)) if sources else "—"
        caliber = "；".join(dict.fromkeys(notes)) if notes else "披露原文 · 北京时间 UTC+8"
        return {
            "list": rows,
            "total": total,
            "source": source_text,
            "caliber": caliber,
            "as_of": _bj_now_text(),
        }

    return cache.get_or_load(key, _LIST_TTL, _load)


# =============================================================
# 五、公告正文详情（详情弹窗按需拉取；A 股 / 港股 / 美股）
# =============================================================

# 东财公告正文接口（np-cnotice-stock，与列表备源同属东财公告库）
_EM_CONTENT = "https://np-cnotice-stock.eastmoney.com/api/content/ann"
# 详情正文缓存 30 分钟：正文一经披露不再变化，缓存只为减少重复拉取与源站压力
_DETAIL_TTL = 1800
# 正文返回上限（字符）：弹窗展示场景，超长正文节选并在 caliber 注明，不整篇回传
_DETAIL_MAX_CHARS = 6000
# 标题归一时剔除的标点/空白（跨源比对：巨潮与东财的标点习惯不一致）
_TITLE_STRIP_RE = re.compile(r"[\s:：,，。.·\-—()（）\[\]【】\"'“”]")
# 东财公告详情链接内的 art_code
_EM_DETAIL_RE = re.compile(r"/notices/detail/([A-Za-z0-9]+)\.html")
# 阶段 12 实测：东财 content 接口分页返回，每页固定 5000 字且 page_index 递增内容不同；
# 展示 6000 字需 2 页，取 3 页留余量（长公告首页常为封面/目录）
_CONTENT_PAGE_CHARS = 5000
_CONTENT_MAX_PAGES = 3
# 阶段 12 实测：东财 ann 列表 page_size 上限 100；20 页 = 2000 条，
# 中大型个股可回溯 5-10 年，并配合「本页最早日期已早于目标」提前终止
_MATCH_PAGE_SIZE = 100
_MATCH_MAX_PAGES = 20

# 繁体 → 简体 精简映射（覆盖港股公告标题高频字）：
# HKEX 标题为繁体、东财港股标题为简体，标题全等比较前需归一；
# 正文包含校验走「繁体正文 ⊇ 繁体标题」，不依赖此表。
_T2S = str.maketrans({
    "據": "据", "於": "于", "為": "为", "億": "亿", "萬": "万", "將": "将",
    "屆": "届", "滿": "满", "額": "额", "聯": "联", "對": "对", "內": "内",
    "負": "负", "責": "责", "準": "准", "確": "确", "聲": "声", "並": "并",
    "產": "产", "開": "开", "關": "关", "東": "东", "亞": "亚", "業": "业",
    "務": "务", "財": "财", "報": "报", "權": "权", "會": "会", "議": "议",
    "決": "决", "選": "选", "舉": "举", "辭": "辞", "職": "职", "調": "调",
    "訂": "订", "單": "单", "價": "价", "購": "购", "買": "买", "賣": "卖",
    "轉": "转", "讓": "让", "資": "资", "銀": "银", "團": "团", "貸": "贷",
    "債": "债", "計": "计", "劃": "划", "條": "条", "協": "协", "簽": "签",
    "諾": "诺", "擔": "担", "證": "证", "預": "预", "虧": "亏", "損": "损",
    "長": "长", "減": "减", "變": "变", "營": "营", "純": "纯", "潤": "润",
    "總": "总", "淨": "净", "攤": "摊", "數": "数", "顯": "显", "續": "续",
    "經": "经", "運": "运", "現": "现", "儲": "储", "備": "备", "餘": "余",
    "週": "周", "函": "函", "細": "细", "則": "则", "連": "连", "須": "须",
    "佈": "布", "載": "载", "網": "网", "頁": "页", "檔": "档", "結": "结",
    "圖": "图", "錄": "录", "註": "注", "冊": "册", "記": "记", "認": "认",
    "審": "审", "閱": "阅", "獨": "独", "綜": "综", "動": "动", "員": "员",
    "級": "级", "僱": "雇", "獎": "奖", "勵": "励", "鎖": "锁", "體": "体",
    "簡": "简", "譯": "译", "師": "师", "給": "给", "屬": "属", "離": "离",
    "隨": "随", "豐": "丰", "匯": "汇", "蘭": "兰", "點": "点", "處": "处",
    "發": "发", "騰": "腾", "訊": "讯", "過": "过", "還": "还", "進": "进",
    "這": "这", "說": "说", "與": "与", "參": "参", "見": "见", "該": "该",
    "們": "们", "時": "时", "間": "间", "問": "问", "題": "题", "應": "应",
    "擬": "拟", "標": "标", "紙": "纸", "華": "华", "辦": "办",
    "優": "优", "灣": "湾", "區": "区", "醫": "医", "藥": "药", "險": "险",
    "驗": "验", "電": "电", "構": "构", "機": "机", "領": "领", "項": "项",
    "類": "类", "風": "风", "順": "顺", "頂": "顶", "頻": "频", "靈": "灵",
    "雙": "双", "雜": "杂", "難": "难", "龍": "龙", "齊": "齐", "黨": "党",
    "鐘": "钟", "隱": "隐", "雖": "虽", "飯": "饭", "飲": "饮", "館": "馆",
    "幣": "币", "兩": "两", "個": "个", "傳": "传", "傷": "伤", "僅": "仅",
    "儀": "仪", "償": "偿", "兒": "儿", "兌": "兑", "凍": "冻", "勝": "胜",
    "勞": "劳", "勢": "势", "衛": "卫", "當": "当", "療": "疗", "監": "监",
    "盤": "盘", "稅": "税", "穩": "稳", "競": "竞", "筆": "笔", "節": "节",
    "範": "范", "籌": "筹", "紀": "纪", "約": "约", "素": "素", "索": "索",
    "累": "累", "維": "维", "綱": "纲", "緊": "紧", "線": "线", "練": "练",
    "組": "组", "終": "终", "統": "统", "絲": "丝", "綁": "绑", "綠": "绿",
    "罰": "罚", "羅": "罗", "義": "义", "習": "习", "老": "老", "聽": "听",
    "腦": "脑", "臉": "脸", "臨": "临", "舊": "旧", "藝": "艺", "築": "筑",
    "顧": "顾", "飛": "飞", "餐": "餐", "駐": "驻", "髮": "发", "齒": "齿",
    "齡": "龄", "執": "执", "掃": "扫", "掛": "挂", "採": "采", "換": "换",
    "搖": "摇", "撤": "撤", "撥": "拨", "擇": "择", "擴": "扩", "擺": "摆",
    "攜": "携", "斷": "断", "來": "来", "極": "极", "檢": "检", "樓": "楼",
    "樣": "样", "樹": "树", "橋": "桥", "橫": "横", "歐": "欧", "歸": "归",
    "殘": "残", "毀": "毁", "氣": "气", "沒": "没", "況": "况", "測": "测",
    "濟": "济", "熱": "热", "燈": "灯", "爭": "争", "牆": "墙", "狀": "状",
    "獲": "获", "環": "环", "畫": "画", "眾": "众", "礎": "础", "視": "视",
    "福": "福", "窮": "穷", "竊": "窃", "糾": "纠", "紅": "红", "聞": "闻",
})


def _norm_text(text: str) -> str:
    """把任意文本归一为可比对形式（剥标签、繁转简、剔标点空白、小写）。

    Args:
        text: 原文（可为 HTML 片段、繁体或简体）。

    Returns:
        str: 归一后的字符串；输入为空返回空串。
    """
    out = re.sub(r"<[^>]+>", "", str(text or ""))
    out = out.translate(_T2S)
    return _TITLE_STRIP_RE.sub("", out).strip().lower()


def _norm_cn_title(title: str) -> str:
    """把公告标题归一为跨源可比对的键。

    处理四件事：剥 HTML 标签、剥东财「公司名:」前缀、繁转简、剔除全部标点与空白后转小写。

    Args:
        title: 公告标题原文（巨潮简体 / 东财简体 / HKEX 繁体）。

    Returns:
        str: 归一标题键；输入为空返回空串。
    """
    text = re.sub(r"<[^>]+>", "", str(title or ""))
    if ":" in text:  # 东财标题固定带「公司名:」前缀（探针实测），先剥离再比对
        text = text.split(":", 1)[1]
    return _norm_text(text)


def _art_from_url(doc_url: str) -> str:
    """从东财公告详情链接中提取 art_code。

    Args:
        doc_url: 形如 ``https://data.eastmoney.com/notices/detail/AN2026....html``。

    Returns:
        str: art_code；未匹配返回空串。
    """
    match = _EM_DETAIL_RE.search(str(doc_url or ""))
    return match.group(1) if match else ""


def _em_content(art_code: str, *, max_pages: int = _CONTENT_MAX_PAGES) -> str:
    """分页拉取东财公告正文并拼接（每页 5000 字，实测连续页内容不同）。

    Args:
        art_code: 东财公告唯一编号（如 AN202608141827994407）。
        max_pages: 最多拉取页数（默认 3 页 = 15000 字）。

    Returns:
        str: 正文全文（未截断，截断由调用方按展示口径处理）。

    Raises:
        RuntimeError: 首页接口非 200 或请求异常（通道异常，由上层转错误态）。
    """
    parts: List[str] = []
    for page in range(1, max_pages + 1):
        try:
            resp = netfix.http_request(
                "GET",
                _EM_CONTENT,
                params={"art_code": art_code, "client_source": "web", "page_index": page},
                headers={"Referer": "https://data.eastmoney.com/notices/"},
                timeout=config.DATASOURCE_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            if parts:
                break  # 首页已成功，后续页失败时保留已取得的内容
            raise RuntimeError(f"东财公告正文请求异常：{exc}") from exc
        if resp.status_code != 200:
            if parts:
                break
            raise RuntimeError(f"东财公告正文 HTTP {resp.status_code}")
        data = (resp.json() or {}).get("data") or {}
        chunk = str(data.get("notice_content") or "").replace("\r\n", "\n").strip()
        if not chunk:
            break
        parts.append(chunk)
        if len(chunk) < _CONTENT_PAGE_CHARS:
            break  # 不足一整页说明已到正文末尾
    return "\n".join(parts).strip()


def _em_page(*, stock_list: str, page: int, page_size: int,
             ann_type: str = "A") -> Tuple[List[Dict[str, str]], int]:
    """按市场类型取一页东财公告列表（匹配用，保留 art_code）。

    Args:
        stock_list: 证券代码（A 股 6 位 / 港股 5 位）。
        page: 页码（从 1 开始）。
        page_size: 每页条数（实测上限 100）。
        ann_type: 东财市场类型：A=A 股，H=港股（阶段 12 探针实测有效）。

    Returns:
        Tuple[List[Dict[str, str]], int]: (记录列表, 源站总命中数)；
        每条含 title/company/code/pubTime/docUrl/art。

    Raises:
        RuntimeError: 接口非 200。
    """
    resp = netfix.http_request(
        "GET",
        _EM_ANN,
        params={
            "sr": -1,
            "page_size": min(int(page_size), 100),
            "page_index": page,
            "ann_type": ann_type,
            "client_source": "web",
            "stock_list": stock_list,
            "f_node": 0,
            "s_node": 0,
        },
        headers={"Referer": "https://data.eastmoney.com/notices/"},
        timeout=config.DATASOURCE_TIMEOUT,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"东财公告列表 HTTP {resp.status_code}")
    data = (resp.json() or {}).get("data") or {}
    total = int(data.get("total_hits") or 0)
    rows: List[Dict[str, str]] = []
    for item in data.get("list") or []:
        company = ""
        codes = item.get("codes") or []
        if codes:
            company = str(codes[0].get("short_name") or "")
        title = str(item.get("title") or "")
        if company and title.startswith(f"{company}:"):
            title = title[len(company) + 1:]
        display = str(item.get("display_time") or "")
        pub = display[:16] if len(display) >= 16 else str(item.get("notice_date") or "")[:10]
        art = str(item.get("art_code") or "")
        rows.append({
            "title": title.strip(":： "),
            "company": company,
            "code": str((codes[0].get("stock_code") if codes else "") or ""),
            "pubTime": pub,
            "docUrl": f"{_EM_DETAIL}{art}.html" if art else "",
            "art": art,
        })
    return rows, total


def _title_in_text(title_norm: str, text_norm: str, *, ratio: float = 0.82) -> bool:
    """判断归一标题是否出现在归一正文中（容忍正文对标题的少量增删）。

    港股正文为 PDF 文本化结果，标题处可能出现编辑差异（实测正文写成
    「…计划**「该计划」**发行…」，比列表标题多一个括注），故不能只做严格子串判断：
    先用标题前 20/12 字在正文中定位锚点，再取该处局部窗口做编辑距离相似度比对。

    Args:
        title_norm: 归一后的标题。
        text_norm: 归一后的正文。
        ratio: 相似度阈值（0~1）。

    Returns:
        bool: 命中返回 True；输入为空或锚点缺失返回 False。
    """
    if not title_norm or not text_norm:
        return False
    if title_norm in text_norm:
        return True
    pos = -1
    for probe_len in (20, 12):
        if len(title_norm) >= probe_len:
            pos = text_norm.find(title_norm[:probe_len])
            if pos >= 0:
                break
    if pos < 0:
        return False
    window = text_norm[pos: pos + len(title_norm) + 20]
    return difflib.SequenceMatcher(None, title_norm, window).ratio() >= ratio


def _day_diff(day_a: str, day_b: str) -> Optional[int]:
    """计算两个日期串（YYYY-MM-DD）相差天数（绝对值）；任一不可解析返回 None。"""
    signed = _signed_day_diff(day_a, day_b)
    return None if signed is None else abs(signed)


def _signed_day_diff(day_a: str, day_b: str) -> Optional[int]:
    """计算 day_a - day_b 的天数（正数表示 day_a 更晚）；任一不可解析返回 None。

    Args:
        day_a: 日期串（YYYY-MM-DD 或以空格分隔的时间串）。
        day_b: 日期串（同上）。

    Returns:
        Optional[int]: 带符号天数差；不可解析返回 None。

    Notes:
        翻页提前终止必须用带符号差值：目标日期早于本页记录时（如回溯历史公告），
        绝对差值同样很大，若用绝对差会误判为「已翻过目标」而提前停止。
    """
    try:
        da = datetime.strptime(day_a[:10], "%Y-%m-%d").date()
        db = datetime.strptime(day_b[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    return (da - db).days


def _match_em_art(
    *, title: str, code: str, pub_time: str, ann_type: str = "A"
) -> Tuple[str, bool, str]:
    """在东财公告库中定位目标公告的 art_code（不降级：逐页回溯 + 正文兜底）。

    匹配策略（阶段 12 重写，替代原「前 50 条」小窗口）：
    ① 按公告日倒序逐页翻（page_size=100，最多 20 页 = 2000 条），
       本页最早日期已早于目标日 2 天以上则提前终止；
    ② 页内「归一标题全等 + 公告日相差 ≤1 天」直接命中（exact=True）；
    ③ 未全等时，对同日同代码候选逐条拉正文，用「正文 ⊇ 标题」校验
       （港股正文为港交所繁体原文，与 HKEX 繁体标题可直接比对，绕开繁简差异）；
    ④ 兜底：同日同代码仅 1 条候选时直接采用（唯一性确定）。

    Args:
        title: 来源侧公告标题（巨潮简体 / HKEX 繁体）。
        code: 证券代码（A 股 6 位 / 港股 4~5 位）。
        pub_time: 来源侧发布时间（北京时间，可能只精确到日）。
        ann_type: 东财市场类型 A / H。

    Returns:
        Tuple[str, bool, str]: (art_code, 是否标题全等命中, 命中方式说明)；
        全部策略失败返回 ("", False, "")。

    Raises:
        RuntimeError: 东财列表通道异常（由上层转错误态）。
    """
    code = (code or "").strip()
    length_ok = len(code) == 6 or (ann_type == "H" and 4 <= len(code) <= 5)
    if not (code.isdigit() and length_ok):
        return "", False, ""
    want_title = _norm_cn_title(title)
    want_day = (pub_time or "")[:10]
    candidates: List[Dict[str, str]] = []

    try:
        for page in range(1, _MATCH_MAX_PAGES + 1):
            rows, _total = _em_page(
                stock_list=code, page=page, page_size=_MATCH_PAGE_SIZE, ann_type=ann_type
            )
            if not rows:
                break
            for row in rows:
                row_day = (row.get("pubTime") or "")[:10]
                if want_day and row_day:
                    signed = _signed_day_diff(row_day, want_day)
                    if signed is not None and abs(signed) > 1:
                        continue  # 日期不符（含往期同名公告）
                if not row.get("art"):
                    continue
                candidates.append(row)
                if want_title and _norm_cn_title(row["title"]) == want_title:
                    return row["art"], True, "标题全等 + 公告日一致"
            # 提前终止：本页最早记录已早于目标日 2 天以上（后续页只会更早）
            days = [r.get("pubTime") or "" for r in rows if r.get("pubTime")]
            if want_day and days:
                oldest_signed = _signed_day_diff(min(days)[:10], want_day)
                if oldest_signed is not None and oldest_signed < -2:
                    break
            if len(rows) < _MATCH_PAGE_SIZE:
                break  # 已到最后一页
    except Exception as exc:  # noqa: BLE001 - 通道异常向上抛，由路由转错误态
        raise RuntimeError(f"东财公告列表异常：{exc}") from exc

    # ② 正文包含校验（港股繁体正文 vs 繁体标题天然同文，A 股简体同理）
    if want_title:
        for row in candidates:
            try:
                body = _em_content(row["art"], max_pages=1)
            except Exception as exc:  # noqa: BLE001 - 单条失败不阻断其余候选
                logger.debug("候选正文拉取失败 art=%s：%s", row["art"], exc)
                continue
            if body and _title_in_text(want_title, _norm_text(body)):
                return row["art"], False, "正文包含标题校验"

    # ③ 唯一候选直接采用
    if len(candidates) == 1:
        return candidates[0]["art"], False, "同日同代码唯一公告"
    return "", False, ""


def cn_detail(*, title: str, code: str, pub_time: str, doc_url: str) -> Dict[str, Any]:
    """A 股公告正文详情（详情弹窗按需拉取）。

    两条取数链：
    - 东财来源记录：docUrl 直接含 art_code，单源直取（verified=False）；
    - 巨潮来源记录：docUrl 为 PDF 链接，按「代码 + 归一标题 + 公告日」
      在东财公告库逐页回溯交叉定位 art_code 后取正文（命中即 verified=True）。

    Args:
        title: 公告标题（列表行原样回传）。
        code: 6 位证券代码。
        pub_time: 发布时间（列表行原样回传）。
        doc_url: 原文链接（列表行原样回传）。

    Returns:
        Dict[str, Any]: ``{content, source, verified, truncated, caliber, as_of}``；
        验证未通过或正文缺失时 content 为空串并给中文 caliber，绝不造数。
    """
    title = str(title or "").strip()
    code = str(code or "").strip()
    pub_time = str(pub_time or "").strip()
    doc_url = str(doc_url or "").strip()

    art = _art_from_url(doc_url)
    verified = False
    how = "东财直链（该公告的直接来源）"

    if not art:
        found, _exact, hit_how = _match_em_art(title=title, code=code, pub_time=pub_time, ann_type="A")
        if not found:
            return _detail_empty(
                "未能取得正文：巨潮标题在东财公告库（含全部历史页）中未匹配到"
                "「标题+代码+公告日」一致的记录，请直接打开原文查看"
            )
        art = found
        verified = True
        how = f"与巨潮披露标题交叉验证一致（{hit_how}）"

    content = _em_content(art)
    if not content:
        return _detail_empty(
            "东财公告库该记录无文本版正文（常见于纯表格/图片型公告），请直接打开原文查看"
        )
    return _detail_payload(content, source="东方财富", verified=verified,
                           caliber=f"正文来自东财公告库（文本版），{how}")


def hk_detail(*, title: str, code: str, pub_time: str, doc_url: str) -> Dict[str, Any]:
    """港股公告正文详情（详情弹窗按需拉取）。

    取数链：HKEX 列表（繁体标题）→ 东财港股公告库（ann_type=H）按「代码 + 公告日」
    锁定候选 → 标题全等 / 正文包含标题校验（港交所繁体原文 vs 繁体标题，绕开繁简差异）
    → content 正文（繁体原文）。

    Args:
        title: 公告标题（HKEX 繁体原文，列表行原样回传）。
        code: 4~5 位港股代码。
        pub_time: 发布时间（北京时间，列表行原样回传）。
        doc_url: HKEX 原文 PDF 链接（仅作展示，不解析 PDF）。

    Returns:
        Dict[str, Any]: 同 :func:`cn_detail`；正文为港交所繁体原文。
    """
    title = str(title or "").strip()
    code = str(code or "").strip()
    pub_time = str(pub_time or "").strip()
    if not (code.isdigit() and 4 <= len(code) <= 5):
        return _detail_empty("港股代码不合法（需 4~5 位数字），无法定位东财公告库记录")
    found, exact, hit_how = _match_em_art(title=title, code=code.zfill(5), pub_time=pub_time, ann_type="H")
    if not found:
        return _detail_empty(
            "未能取得正文：HKEX 标题在东财港股公告库中未匹配到「标题+代码+公告日」"
            "一致的记录，请直接打开港交所原文（PDF）查看"
        )
    content = _em_content(found)
    if not content:
        return _detail_empty("东财港股公告库该记录无文本版正文，请直接打开港交所原文（PDF）查看")
    note = "标题全等" if exact else hit_how
    return _detail_payload(
        content, source="东方财富（港交所原文镜像）", verified=True,
        caliber=f"正文为港交所披露繁体原文文本版，与 HKEX 列表标题经「代码+公告日+{note}」校验一致",
    )


def us_detail(*, title: str, code: str, pub_time: str, doc_url: str) -> Dict[str, Any]:
    """美股公告正文详情（详情弹窗按需拉取）。

    取数链：直接抓 SEC EDGAR 官方申报文档（primaryDocument，实测 145/145 为 .htm）
    → 剥标签文本化。正文与列表同源（SEC 官方），无需跨源匹配。

    Args:
        title: 公告标题（列表行原样回传，仅用于展示核对）。
        code: ticker。
        pub_time: 发布时间。
        doc_url: EDGAR 归档文档链接。

    Returns:
        Dict[str, Any]: 同 :func:`cn_detail`；正文为 SEC 官方原文。
    """
    doc_url = str(doc_url or "").strip()
    if not doc_url.startswith("https://www.sec.gov/Archives/"):
        return _detail_empty("该记录不是 SEC EDGAR 归档文档链接，无法抓取正文")
    try:
        resp = netfix.http_request(
            "GET", doc_url, headers={"User-Agent": _EDGAR_UA}, timeout=60
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"EDGAR 文档请求异常：{exc}") from exc
    if resp.status_code != 200:
        raise RuntimeError(f"EDGAR 文档 HTTP {resp.status_code}")
    text = _html_to_text(resp.text or "")
    if not text:
        return _detail_empty("EDGAR 文档无可用文本（可能为纯图片/扫描件），请直接打开原文查看")
    return _detail_payload(
        text, source="SEC EDGAR", verified=True,
        caliber="正文为 SEC EDGAR 官方申报原文（HTML 文档文本化），与列表同源",
    )


def _html_to_text(html: str) -> str:
    """把 HTML 文档转为可读纯文本（剥脚本/样式/XBRL 头，块级标签断行，实体解码）。

    Args:
        html: 原始 HTML。

    Returns:
        str: 纯文本；输入为空返回空串。

    Notes:
        EDGAR 10-K/10-Q 的 HTML 开头含内联 XBRL ``<ix:header>``（隐藏段），
        不剥除会导致正文前 6000 字全是 `aapl-20250927 false 2025 FY ...` 这类上下文定义，
        实测剥除后首个可见文本即「UNITED STATES / SECURITIES AND EXCHANGE COMMISSION / FORM 10-K」。
    """
    text = re.sub(r"(?is)<(script|style|ix:header)[^>]*>.*?</\1>", " ", str(html or ""))
    text = re.sub(r"(?i)</(p|div|tr|li|h[1-6]|table|section)>", "\n", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html_lib.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _detail_empty(caliber: str) -> Dict[str, Any]:
    """构造「无正文」的统一返回（空/错分离：空态走成功信封 + 中文口径）。

    Args:
        caliber: 中文口径说明。

    Returns:
        Dict[str, Any]: content 为空串的详情结构。
    """
    return {
        "content": "",
        "source": "—",
        "verified": False,
        "truncated": False,
        "caliber": caliber,
        "as_of": _bj_now_text(),
    }


def _detail_payload(content: str, *, source: str, verified: bool, caliber: str) -> Dict[str, Any]:
    """按展示口径截断正文并组装详情返回。

    Args:
        content: 正文全文。
        source: 正文来源标识。
        verified: 是否通过交叉验证。
        caliber: 口径说明（截断时自动追加节选说明）。

    Returns:
        Dict[str, Any]: 详情结构。
    """
    truncated = len(content) > _DETAIL_MAX_CHARS
    note = caliber
    if truncated:
        note += f"；超长正文节选前 {_DETAIL_MAX_CHARS} 字，完整内容请打开原文"
    return {
        "content": content[:_DETAIL_MAX_CHARS],
        "source": source,
        "verified": verified,
        "truncated": truncated,
        "caliber": note,
        "as_of": _bj_now_text(),
    }


def detail_cached(*, market: str, title: str, code: str, pub_time: str, doc_url: str) -> Dict[str, Any]:
    """带缓存的公告正文详情（按市场分发 a/hk/us）。

    缓存口径：仅「取到正文」的结果落缓存（30 分钟）；空态/验证未通过不落缓存，
    下次打开弹窗立即重新拉取 —— 避免源站临时波动把空态固化半小时（不得降级）。

    Args:
        market: 市场枚举 a/hk/us。
        title: 公告标题。
        code: 证券代码。
        pub_time: 发布时间。
        doc_url: 原文链接。

    Returns:
        Dict[str, Any]: 同对应市场的详情函数。
    """
    market = market if market in ("a", "hk", "us") else "a"
    digest = f"{market}|{title}|{code}|{pub_time}|{doc_url}"
    key = f"findetail:{market}:{zlib.crc32(digest.encode('utf-8')):08x}"
    handlers = {"a": cn_detail, "hk": hk_detail, "us": us_detail}
    handler = handlers[market]
    cached = cache.get(key, _DETAIL_TTL)
    if isinstance(cached, dict) and cached.get("content"):
        return cached
    result = handler(title=title, code=code, pub_time=pub_time, doc_url=doc_url)
    if isinstance(result, dict) and result.get("content"):
        cache.set(key, result)
    return result


def cn_detail_cached(*, title: str, code: str, pub_time: str, doc_url: str) -> Dict[str, Any]:
    """带缓存的 A 股公告正文详情（detail_cached 的 A 股别名，保持既有调用兼容）。

    Args:
        title: 公告标题。
        code: 6 位证券代码。
        pub_time: 发布时间。
        doc_url: 原文链接。

    Returns:
        Dict[str, Any]: 同 :func:`cn_detail`。
    """
    return detail_cached(market="a", title=title, code=code, pub_time=pub_time, doc_url=doc_url)
