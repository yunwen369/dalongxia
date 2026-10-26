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
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from backend.app import config
from backend.app.core import cache, netfix

logger = logging.getLogger(__name__)

# 北京时区（UTC+8）：全模块唯一时间口径，与 datasource/utils.py 保持一致
_BJ = ZoneInfo("Asia/Shanghai")

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
MARKET_VALUES: Tuple[str, ...] = ("all", "a", "hk", "us")
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
    return datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S")


def _since_start(since: str) -> Optional[date]:
    """把「近 N 天」换算为起始日期。

    Args:
        since: ``all`` / ``7`` / ``30`` / ``90`` / ``365``。

    Returns:
        Optional[date]: 起始日期；``all`` 返回 None（不限起始）。
    """
    if since not in SINCE_VALUES or since == "all":
        return None
    return (datetime.now(_BJ) - timedelta(days=int(since))).date()


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
        se_date = f"{start.isoformat()}~{datetime.now(_BJ).date().isoformat()}"
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
    return rows, total, "巨潮资讯法定披露原文，按披露时间倒序；部分记录源站仅精确到日，故只显示日期"


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
    resp = netfix.http_request(
        "GET",
        _HKEX_PREFIX,
        params={"callback": "cb", "lang": "ZH", "type": "A", "name": keyword, "market": "SEHK"},
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
        return [], 0, "HKEX披露易", "HKEX 未检索到与该关键词匹配的港股，请检查 5 位代码或公司简称"

    start = _since_start(since) or (datetime.now(_BJ).date() - timedelta(days=365))
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
            "toDate": datetime.now(_BJ).strftime("%Y%m%d"),
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
            )
        )
    filtered = [r for r in rows if type_value == "全部" or r["type"] == type_value]
    filtered = [r for r in filtered if _in_range(r["pubTime"] or "", since)]
    sliced = filtered[(page - 1) * page_size: page * page_size]
    return sliced, len(filtered), "HKEX披露易", "港交所披露易原文（繁体标题按披露原文展示），时间为香港时间＝北京时间"


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
        return [], 0, "SEC EDGAR", "EDGAR 未检索到与该关键词匹配的美股申报人，请改用 ticker（如 NVDA）或英文公司名"

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
            )
        )
    rows.sort(key=lambda r: r["pubTime"], reverse=True)
    sliced = rows[(page - 1) * page_size: page * page_size]
    return (
        sliced,
        len(rows),
        "SEC EDGAR",
        "SEC EDGAR 官方申报原文；发布时间由 acceptanceDateTime(UTC) 换算北京时间，美东盘后申报对应北京次日凌晨",
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
