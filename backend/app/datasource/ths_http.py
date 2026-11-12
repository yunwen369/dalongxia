# -*- coding: utf-8 -*-
"""同花顺纯 HTTP 板块通道（不依赖 py_mini_racer / V8 签名）。

背景（第八轮，2026-11-02）
--------------------------
同花顺板块系数据原先统一走 akshare（``stock_board_industry_summary_ths`` 等），
其 hexin-v 签名由 py_mini_racer 内嵌 V8 生成；冻结形态按规范 §13.6-2 禁用 V8 后，
板块行情 / K 线 / 资金流 / 拥挤度全部降级为空态。本轮实测确认：
同花顺「行情中心」（``q.10jqka.com.cn``）与「行情数据」（``d.10jqka.com.cn``）
两个域名**不需要 hexin-v 签名**即可取数，故新建本模块作为板块数据的正式主源，
把 V8 依赖从板块链路上彻底移除（降级点 G-2/G-15 的消除手段）。

覆盖能力
--------
1. 行业行情：``/thshy/index/field/199112/order/desc/page/N/``（90 行，含净流入/涨跌家数）；
2. 行业名录：同上（解析板块名 → 板块代码 881xxx）；
3. 板块成分：``/thshy/detail/code/{code}/`` 与 ``/gn/detail/code/{code}/``（每页 10/20 行，翻页取全）；
4. 板块 K 线：``d.10jqka.com.cn/v6/line/bk_{code}/01|11|21/all.js``（日/周/月，含成交额）；
5. 板块实时快照：``d.10jqka.com.cn/v6/realhead/bk_{code}/last.js``。

数据口径
--------
- 成交额单位=亿元（页面列头「总成交额（亿元）」），净流入单位=亿元；
- 成交量单位=万手；价格=板块指数点；
- K 线 ``data`` 字段为 ``日期,开,高,低,收,成交量(股),成交额(元)`` 的分号分隔串，
  其中成交额单位为元，本模块统一折算为亿元后返回。

限频与稳健性
------------
同花顺对高频访问会返回 401/403（chameleon 反爬）。本模块：
1. 每次请求间隔 ``_POLITE_DELAY`` 秒（默认 0.35s），并对 401/403 做一次退避重试；
2. 列表类结果由调用方缓存（路由层 TTL 30s～1h），本模块自身不缓存，保持纯取数职责。
"""

from __future__ import annotations

import json
import codecs
import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional

from backend.app import config
from backend.app.core.errors import NonRetryableError
from backend.app.datasource import utils

logger = logging.getLogger(__name__)

# 同花顺反爬：连续请求需保持间隔，过快会返回 401（chameleon）/ 403（nginx forbidden）。
# 实测（2026-11-02）：q.10jqka.com.cn 间隔 1.2s 连续 8 次全部 200；间隔 0.35s 连续 6 次
# 即被 401 拦截。故对「行情中心」域名做**进程级最小间隔**限流，行情数据域名（d.10jqka.com.cn）
# 为纯静态 JSON 无此限制，保持低延迟。
_POLITE_DELAY = {"q": 1.1, "d": 0.1}
# 401/403 处理：同花顺反爬是**按 IP 的短时封禁**（实测约 30–60 秒），
# 单请求逐个退避会拖成几十秒且拖慢整批预热。改为「全局冷却 + 指数退避」：
# 一旦命中 401/403，整个进程对该域名暂停 ``_COOLDOWN`` 秒，后续每次失败
# 冷却时间翻倍（上限 ``_COOLDOWN_MAX``），最多重试 ``_MAX_ATTEMPTS`` 次；
# 仍失败才判定通道异常（交由上层降级链）。2026-11-06 实测封禁可达 60s+，
# 原 12s×1 次不够，改为 15→30→60s 三次退避。
# RF-20261112-01 阶段 1 备案：本模块为**领域特定退避**（动态全局冷却 +
# 跨线程共享 cool_until，与 core.resilient 的「单调用指数退避」语义不同），
# 有意不收编 retry_call/retry_until——收编会把 IP 短封的进程级冷却
# 降格为单调用级重试，反而放大封禁面。变更须经门禁 R 等价性评审。
_COOLDOWN = 15.0
_COOLDOWN_MAX = 60.0
_MAX_ATTEMPTS = 3
_COOLDOWN_UNTIL: Dict[str, float] = {}
# 周期 → 同花顺 K 线路径段（01=日 11=周 21=月）
_PERIOD_PATH = {"daily": "01", "weekly": "11", "monthly": "21"}

# 进程级限流状态：{域名键: 上次请求完成时间}
_THROTTLE_LOCK = threading.Lock()
_THROTTLE_TS: Dict[str, float] = {}

# 行业行情短缓存：(时间戳, 行列表)，供行情/K线/资金流共用，避免重复翻页触发限流
_INDUSTRY_ROWS: Dict[str, Any] = {}
_INDUSTRY_ROWS_TTL = 20
# 板块代码缓存：{("industry"|"concept"): {板块名: 代码}}
_CODE_CACHE: Dict[str, Dict[str, str]] = {}
_CODE_CACHE_TS: Dict[str, float] = {}
_CODE_TTL = 3600  # 板块名录变化极慢，进程内缓存 1 小时


def _throttle(host_key: str) -> None:
    """按域名键做进程级最小请求间隔（同花顺行情中心 1.1s，行情数据 0.1s）。

    Args:
        host_key: ``q``（行情中心）/ ``d``（行情数据）。
    """
    gap = _POLITE_DELAY.get(host_key, 0.1)
    with _THROTTLE_LOCK:
        # 预约下一个可用时间片：锁内只做算术，不在锁内 sleep，
        # 否则并发扫描会被串行化（90 板块排行曾因此耗时翻倍）。
        slot = max(_THROTTLE_TS.get(host_key, 0.0) + gap, time.time())
        _THROTTLE_TS[host_key] = slot
    wait = slot - time.time()
    if wait > 0:
        time.sleep(wait)


def _get_text(url: str, *, referer: str = "", encoding: str = "gbk") -> str:
    """带限流与退避重试的同花顺文本请求。

    Args:
        url: 完整 URL。
        referer: Referer，缺省用行情中心首页。
        encoding: 响应编码，同花顺行情页为 GBK。

    Returns:
        str: 解码后的页面文本。

    Raises:
        NonRetryableError: 连续 401/403 或网络确定性失败（不重试，交由降级链）。
    """
    host_key = "d" if "d.10jqka.com.cn" in url else "q"
    headers = {
        "User-Agent": config.BROWSER_USER_AGENT,
        "Referer": referer or config.THS_REFERER,
        "Accept": "text/html,application/javascript,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    # 延迟导入避免 core → datasource 循环依赖
    from backend.app.core import netfix

    # 命中原站封禁时先等待全局冷却（其它线程同样受益，避免各自重试放大封禁）
    with _THROTTLE_LOCK:
        cool_until = _COOLDOWN_UNTIL.get(host_key, 0.0)
    if cool_until > time.time():
        time.sleep(cool_until - time.time())
    last_error = ""
    cooldown = _COOLDOWN
    for attempt in range(_MAX_ATTEMPTS):
        _throttle(host_key)
        session = netfix.make_session()
        try:
            response = session.get(url, headers=headers, timeout=config.DATASOURCE_TIMEOUT)
            if response.status_code in (401, 403):
                last_error = f"HTTP {response.status_code}（反爬拦截）"
                with _THROTTLE_LOCK:
                    _COOLDOWN_UNTIL[host_key] = time.time() + cooldown
                logger.warning("同花顺 %s 第 %d/%d 次命中反爬（%s），冷却 %.0fs 后重试",
                               host_key, attempt + 1, _MAX_ATTEMPTS, last_error, cooldown)
                if attempt < _MAX_ATTEMPTS - 1:
                    time.sleep(cooldown)
                    cooldown = min(cooldown * 2, _COOLDOWN_MAX)
                continue
            response.raise_for_status()
            response.encoding = encoding
            return response.text
        except Exception as exc:  # noqa: BLE001 - 统一转译为可降级异常
            name = type(exc).__name__
            if name in ("ProxyError", "SSLError", "ConnectionError", "RemoteDisconnected"):
                raise NonRetryableError(f"同花顺通道不可用：{name}") from exc
            last_error = f"{name}"
        finally:
            session.close()
    raise NonRetryableError(f"同花顺请求失败：{last_error or '未知原因'}（已重试 {_MAX_ATTEMPTS} 次）")


def _text_of(cell_html: str) -> str:
    """剥离 HTML 标签并归一化空白。

    Args:
        cell_html: 单元格 HTML 片段。

    Returns:
        str: 纯文本。
    """
    text = re.sub(r"<[^>]+>", "", cell_html or "")
    return text.replace("&nbsp;", " ").replace("\r", " ").replace("\n", " ").strip()


def _cells(row_html: str) -> List[str]:
    """把一行 ``<tr>`` 拆成单元格文本列表。

    Args:
        row_html: 行 HTML 片段。

    Returns:
        List[str]: 单元格文本（按列顺序）。
    """
    return [_text_of(m.group(1)) for m in re.finditer(r"<td[^>]*>(.*?)</td>", row_html, re.S)]


def _tbody_rows(html: str) -> List[str]:
    """取出表体区域的全部 ``<tr>`` 片段。

    Args:
        html: 页面 HTML。

    Returns:
        List[str]: 行片段列表；无表体时返回空列表。
    """
    start = html.find("<tbody")
    end = html.find("</tbody>")
    if start < 0 or end < 0:
        return []
    return re.findall(r"<tr[^>]*>(.*?)</tr>", html[start:end], re.S)


def _signed_number(text: str) -> Optional[float]:
    """把「+1.23」「-4.56」「--」等文本转为浮点。

    Args:
        text: 源站文本。

    Returns:
        Optional[float]: 数值；不可解析返回 None（不补 0）。
    """
    cleaned = str(text or "").strip().replace("%", "").replace(",", "")
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    return utils.to_float(cleaned)


# =============================================================
# 一、行业行情与名录（同花顺行情中心，90 行）
# =============================================================
def get_industry_rows(max_pages: int = 4) -> List[Dict[str, Any]]:
    """取同花顺行业行情全表（约 90 行，含成交额/净流入/涨跌家数）。

    页面列：序号｜板块｜涨跌幅(%)｜总成交量(万手)｜总成交额(亿元)｜净流入(亿元)｜
    上涨家数｜下跌家数｜均价｜领涨股｜最新价｜涨跌幅(%)。

    数据口径说明：
        同花顺页面不单列「主力流入/流出」，只给「净流入」；流入/流出由
        :mod:`sina_flow` 的板块资金流补齐（两者同口径为全单统计）。

    Args:
        max_pages: 最多翻页数（每页 50 行，90 个行业 2 页足够，留余量防名录扩张）。

    Returns:
        List[Dict[str, Any]]: [{name, code, pct, volume, amount, net_inflow,
        up, down, avg_price, lead_stock, lead_price, lead_pct, kind}]

    Raises:
        ValueError: 全部页均无有效行。
    """
    cached = _INDUSTRY_ROWS.get("rows")
    if cached and time.time() - _INDUSTRY_ROWS.get("ts", 0.0) < _INDUSTRY_ROWS_TTL:
        return [dict(row) for row in cached]
    items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        url = (
            f"{config.THS_QUOTE_BASE}/thshy/index/field/199112/order/desc/page/{page}/"
        )
        html = _get_text(url)
        rows = _tbody_rows(html)
        if not rows:
            break
        page_items = 0
        for row in rows:
            cells = _cells(row)
            if len(cells) < 8:
                continue
            name = cells[1]
            if not name or name in seen:
                continue
            code_match = re.search(r"/thshy/detail/code/(\d+)/", row)
            lead_match = re.search(
                r"stockpage\.10jqka\.com\.cn/(\d{6})/[^>]*>([^<]+)<", row
            )
            seen.add(name)
            items.append(
                {
                    "name": name,
                    # 板块代码 881xxx：K 线/成分接口的唯一键
                    "code": code_match.group(1) if code_match else None,
                    "pct": _signed_number(cells[2]),
                    "volume": _signed_number(cells[3]),  # 万手
                    "amount": _signed_number(cells[4]),  # 亿元
                    "net_inflow": _signed_number(cells[5]),  # 亿元
                    "up": utils.to_int(cells[6]),
                    "down": utils.to_int(cells[7]),
                    "avg_price": _signed_number(cells[8]) if len(cells) > 8 else None,
                    "lead_stock": lead_match.group(2).strip() if lead_match else None,
                    "lead_code": lead_match.group(1) if lead_match else None,
                    "lead_price": _signed_number(cells[10]) if len(cells) > 10 else None,
                    "lead_pct": _signed_number(cells[11]) if len(cells) > 11 else None,
                    "kind": "行业",
                }
            )
            page_items += 1
        if page_items == 0:
            break
        if len(rows) < 50:  # 末页
            break
    if not items:
        raise ValueError("同花顺行业行情页无可解析行（页面结构变更或反爬拦截）")
    _INDUSTRY_ROWS["rows"] = [dict(row) for row in items]
    _INDUSTRY_ROWS["ts"] = time.time()
    return items


def get_industry_amount(name: str) -> Optional[float]:
    """按板块名取当日成交额（亿元，来自行业行情页短缓存）。

    Args:
        name: 板块名。

    Returns:
        Optional[float]: 成交额（亿元）；未命中返回 None。
    """
    target = str(name or "").strip()
    if not target:
        return None
    try:
        for row in get_industry_rows():
            if row.get("name") == target:
                return row.get("amount")
    except Exception:  # noqa: BLE001 - 取不到不影响 K 线主数据
        return None
    return None


def get_industry_flow_rows(max_pages: int = 4) -> List[Dict[str, Any]]:
    """取同花顺「行业资金流」全表（90 行，含流入/流出/净额，单位=亿元）。

    页面列：序号｜行业｜行业指数｜涨跌幅｜流入资金(亿)｜流出资金(亿)｜净额(亿)｜
    公司家数｜领涨股｜涨跌幅｜当前价(元)。

    与 :func:`get_industry_rows` 的区别：
        - 本函数来自「资金流向」频道（``data.10jqka.com.cn/funds/hyzjl``），
          提供**流入/流出**两个方向金额；行情页只给净流入；
        - 两者栏目名同为同花顺行业名，可逐行对齐（本轮取净额与流入/流出）。

    Args:
        max_pages: 最多翻页数（每页 50 行，90 个行业 2 页足够）。

    Returns:
        List[Dict[str, Any]]: [{name, price, pct, inflow, outflow, net_inflow,
        company_count, lead_stock, lead_pct}]

    Raises:
        ValueError: 全部页均无有效行。
    """
    items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        url = (
            "http://data.10jqka.com.cn/funds/hyzjl/field/tradezdf/order/desc/"
            f"page/{page}/"
        )
        html = _get_text(url, referer="http://data.10jqka.com.cn/funds/hyzjl/")
        rows = _tbody_rows(html)
        if not rows:
            break
        page_items = 0
        for row in rows:
            cells = _cells(row)
            if len(cells) < 7:
                continue
            name = cells[1]
            if not name or name in seen:
                continue
            code_match = re.search(r"/thshy/detail/code/(\d+)/", row)
            lead_match = re.search(
                r"stockpage\.10jqka\.com\.cn/(\d{6})/[^>]*>([^<]+)<", row
            )
            seen.add(name)
            items.append(
                {
                    "name": name,
                    "code": code_match.group(1) if code_match else None,
                    "price": _signed_number(cells[2]),
                    "pct": _signed_number(cells[3]),
                    "inflow": _signed_number(cells[4]),  # 亿元
                    "outflow": _signed_number(cells[5]),  # 亿元
                    "net_inflow": _signed_number(cells[6]),  # 亿元
                    "company_count": utils.to_int(cells[7]) if len(cells) > 7 else None,
                    "lead_stock": lead_match.group(2).strip() if lead_match else None,
                    "lead_code": lead_match.group(1) if lead_match else None,
                    "lead_pct": _signed_number(cells[9]) if len(cells) > 9 else None,
                    "kind": "行业",
                }
            )
            page_items += 1
        if page_items == 0 or len(rows) < 50:
            break
    if not items:
        raise ValueError("同花顺行业资金流页无可解析行（页面结构变更或反爬拦截）")
    return items


def _refresh_code_cache(kind: str, mapping: Dict[str, str]) -> None:
    """刷新板块名→代码缓存。

    Args:
        kind: industry / concept。
        mapping: 板块名与代码的映射。
    """
    if mapping:
        _CODE_CACHE[kind] = dict(mapping)
        _CODE_CACHE_TS[kind] = time.time()


def get_industry_codes() -> Dict[str, str]:
    """取「同花顺行业名 → 板块代码(881xxx)」映射（进程内缓存 1 小时）。

    Returns:
        Dict[str, str]: 映射字典；取数失败时返回上次缓存（可能为空）。

    Raises:
        ValueError: 首次取数即失败且无缓存。
    """
    cached = _CODE_CACHE.get("industry")
    if cached and time.time() - _CODE_CACHE_TS.get("industry", 0) < _CODE_TTL:
        return cached
    try:
        rows = get_industry_rows()
    except Exception as exc:  # noqa: BLE001 - 有缓存则用缓存，无缓存上抛
        if cached:
            logger.warning("同花顺行业名录刷新失败，沿用缓存：%s", type(exc).__name__)
            return cached
        raise
    mapping = {row["name"]: row["code"] for row in rows if row.get("code")}
    _refresh_code_cache("industry", mapping)
    return mapping


def get_board_code(name: str, kind: str = "industry") -> Optional[str]:
    """按板块名取同花顺板块代码。

    Args:
        name: 板块名（如「半导体」「白酒概念」）。
        kind: industry / concept。

    Returns:
        Optional[str]: 板块代码；名录中无此板块时返回 None。
    """
    target = str(name or "").strip()
    if not target:
        return None
    if kind == "industry":
        return get_industry_codes().get(target)
    codes = get_concept_codes()
    return codes.get(target)


# =============================================================
# 二、概念名录与成分（同花顺概念详情页）
# =============================================================
def get_concept_codes() -> Dict[str, str]:
    """取「同花顺概念名 → 概念代码」映射（进程内缓存 1 小时）。

    同花顺概念清单页 ``/gn/`` 的表体按「新增日期」倒序、每页 10 条；
    本函数翻页直到连续两页无新概念或达到 ``max_pages``，用于 /cons 反查代码。

    Returns:
        Dict[str, str]: 映射字典（取不到时为空字典，不抛异常）。
    """
    cached = _CODE_CACHE.get("concept")
    if cached and time.time() - _CODE_CACHE_TS.get("concept", 0) < _CODE_TTL:
        return cached
    mapping: Dict[str, str] = dict(cached or {})
    empty_pages = 0
    try:
        for page in range(1, 61):
            url = f"{config.THS_QUOTE_BASE}/gn/index/field/addtime/order/desc/page/{page}/"
            html = _get_text(url)
            rows = _tbody_rows(html)
            if not rows:
                break
            added = 0
            for row in rows:
                match = re.search(r"/gn/detail/code/(\d+)/[^>]*>([^<]+)<", row)
                if not match:
                    continue
                name = match.group(2).strip()
                if name and name not in mapping:
                    mapping[name] = match.group(1)
                    added += 1
            empty_pages = empty_pages + 1 if added == 0 else 0
            if empty_pages >= 2:
                break
    except Exception as exc:  # noqa: BLE001 - 概念名录为尽力而为，不阻断主流程
        logger.info("同花顺概念名录抓取中断：%s", type(exc).__name__)
    if mapping:
        _refresh_code_cache("concept", mapping)
    return mapping


def get_concept_index_codes() -> Dict[str, str]:
    """取「同花顺概念名 → 概念**指数**代码(885xxx)」映射（R11 概念 K 线备源，缓存 1 小时）。

    ``/gn/`` 概览页内嵌 ``<input id="gnSection" value='{...}'>`` JSON：按序号索引，
    每项含 ``platecode``（885xxx 概念指数代码）与 ``platename``（unicode 转义概念名）。
    注意与 ``get_concept_codes()`` 的区别：后者给「概念详情页代码(3xxxxx)」用于
    取成分股，本函数给「概念指数代码(885xxx)」用于取概念指数日 K（含成交额），
    两者代码族不同、不可互换。

    Returns:
        Dict[str, str]: 概念名 → 885xxx 指数代码；页面结构变化时返回空字典（不抛）。
    """
    cached = _CODE_CACHE.get("concept_index")
    if cached and time.time() - _CODE_CACHE_TS.get("concept_index", 0) < _CODE_TTL:
        return cached
    mapping: Dict[str, str] = {}
    try:
        html = _get_text(f"{config.THS_QUOTE_BASE}/gn/")
        match = re.search(r"gnSection\"?\s+value='(\{.*?\})'>", html, re.S)
        if match:
            segment = match.group(1)
            for code, raw_name in re.findall(
                r'"platecode":"(\d+)","platename":"((?:[^"\\]|\\.)*)"', segment
            ):
                try:
                    name = codecs.decode(raw_name, "unicode_escape")
                except Exception:  # noqa: BLE001 - 个别转义异常跳过该条
                    continue
                name = name.strip()
                if name and code.startswith("88") and name not in mapping:
                    mapping[name] = code
    except Exception as exc:  # noqa: BLE001 - 备源名录尽力而为，不阻断主流程
        logger.info("同花顺概念指数名录解析失败：%s", type(exc).__name__)
    if mapping:
        _refresh_code_cache("concept_index", mapping)
        logger.info("同花顺概念指数名录已更新：%d 项", len(mapping))
    return mapping


def get_board_cons(
    code: str,
    *,
    kind: str = "industry",
    max_pages: int = 40,
) -> List[Dict[str, Any]]:
    """取板块成分股（同花顺板块详情页，10/20 行一页，翻页取全）。

    Args:
        code: 同花顺板块代码（行业 881xxx / 概念 3xxxxx）。
        kind: industry（``/thshy/detail``）/ concept（``/gn/detail``）。
        max_pages: 最多翻页数（防名录异常导致无限翻页）。

    Returns:
        List[Dict[str, Any]]: [{code, name, price, pct, amount, float_cap, total_cap, pe}]

    Raises:
        ValueError: 首页即无有效行。
    """
    section = "thshy" if kind == "industry" else "gn"
    items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        # 翻页用 /detail/code/{code}/page/{n}/ 形式：实测可用；
        # /detail/field/.../ajax/1/ 与 /detail/field/.../page/N/ 会被反爬直接 401。
        url = f"{config.THS_QUOTE_BASE}/{section}/detail/code/{code}/"
        if page > 1:
            url = f"{config.THS_QUOTE_BASE}/{section}/detail/code/{code}/page/{page}/"
        html = _get_text(url)
        rows = _tbody_rows(html)
        if not rows:
            break
        added = 0
        for row in rows:
            cells = _cells(row)
            if len(cells) < 3:
                continue
            stock_match = re.search(r"stockpage\.10jqka\.com\.cn/(\d{6})/", row)
            stock_code = stock_match.group(1) if stock_match else ""
            if not stock_code or stock_code in seen:
                continue
            seen.add(stock_code)
            added += 1
            # 表头实测：序号|代码|名称|现价|涨跌幅|涨跌|涨速|换手|量比|振幅|成交额|流通股|流通市值|市盈率
            items.append(
                {
                    "code": stock_code,
                    "name": cells[2],
                    "price": _signed_number(cells[3]) if len(cells) > 3 else None,
                    "pct": _signed_number(cells[4]) if len(cells) > 4 else None,
                    "turnover_rate": _signed_number(cells[7]) if len(cells) > 7 else None,
                    "volume_ratio": _signed_number(cells[8]) if len(cells) > 8 else None,
                    "amplitude": _signed_number(cells[9]) if len(cells) > 9 else None,
                    "amount": _cap_to_yi(cells[10]) if len(cells) > 10 else None,
                    "float_shares": _cap_to_yi(cells[11]) if len(cells) > 11 else None,
                    "float_cap": _cap_to_yi(cells[12]) if len(cells) > 12 else None,
                    "total_cap": None,
                    "pe": _signed_number(cells[13]) if len(cells) > 13 else None,
                }
            )
        if added == 0:
            break
    if not items:
        raise ValueError(f"同花顺板块成分页无可解析行（code={code}）")
    return items


def _cap_to_yi(text: str) -> Optional[float]:
    """把「211.43亿」这类市值文本统一折算为亿元。

    Args:
        text: 源站文本（单位 亿/万）。

    Returns:
        Optional[float]: 亿元数值；不可解析返回 None。
    """
    raw = str(text or "").strip()
    if not raw or raw in ("--", "-"):
        return None
    number = _signed_number(re.sub(r"[亿万]", "", raw))
    if number is None:
        return None
    if "万" in raw:
        return round(number / 1e4, 4)
    return round(number, 4)


# =============================================================
# 三、板块 K 线（同花顺行情数据域，含成交额）
# =============================================================
def _parse_line_json(text: str) -> Dict[str, Any]:
    """解析同花顺 JSONP 响应（``quotebridge_xxx({...})``）。

    Args:
        text: 原始响应文本。

    Returns:
        Dict[str, Any]: 解析后的字典。

    Raises:
        ValueError: 响应不是预期 JSONP 结构。
    """
    match = re.search(r"\((\{.*\})\)\s*;?\s*$", text, re.S)
    if not match:
        raise ValueError("同花顺 K 线响应不是预期 JSONP 结构")
    return json.loads(match.group(1))


def get_board_kline(
    code: str,
    *,
    period: str = "daily",
    limit: Optional[int] = None,
    fill_today: bool = True,
    amount_hint: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """取板块指数 K 线（日/周/月，含成交额），合并源站两个文件的优点。

    源站两个文件的差异（2026-11-02 实测）：
        - ``last.js``：近期 140 根（周 984 / 月 231），``data`` 为
          ``日期,开,高,低,收,成交量(股),成交额(元)`` 直接编码，**含成交额**，
          但**不含当日**（收盘后仍滞后一个交易日）；
        - ``all.js``：全历史，价格差分编码
          ``[低, 开-低, 高-低, 收-低]``（除数 ``priceFactor``）、``volumn`` 为成交量，
          **含当日**但**不含成交额**。

    本函数以 ``all.js`` 为主序列（含当日），按日期把 ``last.js`` 的成交额并入；
    当日成交额缺失时用 ``realhead`` 的成交额补齐（``fill_today`` 控制，排行批量扫描
    可关闭以省请求）。合并后金额缺失一律 None，不补 0。

    Args:
        code: 同花顺板块代码（881xxx）。
        period: daily / weekly / monthly（也接受 日k/周k/月k）。
        limit: 仅返回最近 N 根；None 表示可用全量。
        fill_today: 是否补当日成交额（用 ``amount_hint`` 或行业行情页当日成交额）。
        amount_hint: 调用方已知的当日成交额（亿元），优先使用。

    Returns:
        List[Dict[str, Any]]: [{date, open, high, low, close, volume, amount, pct}]

    Raises:
        ValueError: 两个文件均无有效行。
    """
    segment = _PERIOD_PATH.get(str(period or "daily").strip().lower())
    if segment is None:
        segment = _PERIOD_PATH["daily"]
    items: List[Dict[str, Any]] = []
    try:
        full = _parse_line_json(
            _get_text(f"{config.THS_LINE_BASE}/v6/line/bk_{code}/{segment}/all.js",
                      referer=config.THS_REFERER)
        )
        items = _parse_delta_series(full)
    except Exception as exc:  # noqa: BLE001 - 全历史失败时退回近期文件
        logger.info("同花顺板块全历史 K 线不可用（%s），改用近期文件", type(exc).__name__)
    recent_items: List[Dict[str, Any]] = []
    try:
        recent = _parse_line_json(
            _get_text(f"{config.THS_LINE_BASE}/v6/line/bk_{code}/{segment}/last.js",
                      referer=config.THS_REFERER)
        )
        recent_items = _parse_plain_series(str(recent.get("data") or ""))
    except Exception as exc:  # noqa: BLE001 - 近期文件失败时仅缺成交额
        logger.info("同花顺板块近期 K 线不可用（%s），成交额可能缺失", type(exc).__name__)
    if not items:
        items = recent_items
    elif recent_items:
        amount_map = {row["date"]: row["amount"] for row in recent_items
                      if row.get("amount") is not None}
        for row in items:
            if row.get("amount") is None:
                row["amount"] = amount_map.get(row["date"])
    if items and fill_today and items[-1].get("amount") is None:
        if amount_hint is not None:
            items[-1]["amount"] = amount_hint
    if not items:
        raise ValueError(f"同花顺板块 K 线无有效行（code={code}）")
    if limit is not None and limit > 0:
        return items[-int(limit):]
    return items


def _parse_plain_series(raw: str) -> List[Dict[str, Any]]:
    """解析 ``last.js`` 的直接编码序列。

    Args:
        raw: ``data`` 字段原文（``日期,开,高,低,收,量,额`` 以 ``;`` 分隔）。

    Returns:
        List[Dict[str, Any]]: K 线列表（成交额已折算为亿元）。
    """
    items: List[Dict[str, Any]] = []
    previous_close: Optional[float] = None
    for chunk in str(raw or "").split(";"):
        parts = chunk.split(",")
        if len(parts) < 7:
            continue
        date_text = parts[0].strip()
        if len(date_text) != 8 or not date_text.isdigit():
            continue
        close_p = _signed_number(parts[4])
        amount_yuan = _signed_number(parts[6])
        pct = None
        if previous_close and close_p is not None and previous_close > 0:
            pct = round((close_p / previous_close - 1) * 100, 4)
        items.append(
            {
                "date": f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:]}",
                "open": _signed_number(parts[1]),
                "high": _signed_number(parts[2]),
                "low": _signed_number(parts[3]),
                "close": close_p,
                "volume": _signed_number(parts[5]),
                # 源站成交额单位为元 → 亿元，与板块行情表同列
                "amount": round(amount_yuan / 1e8, 4) if amount_yuan is not None else None,
                "pct": pct,
            }
        )
        if close_p is not None:
            previous_close = close_p
    return items


def _parse_delta_series(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """解析 ``all.js`` 的差分编码序列。

    编码规则（实测反推，2026-11-02）：``price`` 每 4 个整数一组，
    依次为 ``低, 开-低, 高-低, 收-低``，真实价格 = 数值 / ``priceFactor``；
    ``dates`` 为 ``MMDD``，年份由 ``sortYear`` 的分段长度还原；
    ``volumn`` 为成交量（股）；该文件**不含成交额**。

    Args:
        payload: ``all.js`` 解析后的字典。

    Returns:
        List[Dict[str, Any]]: K 线列表；数据不完整时返回空列表。
    """
    dates = [d for d in str(payload.get("dates") or "").split(",") if d]
    raw_prices = [p for p in str(payload.get("price") or "").split(",") if p]
    volumes = str(payload.get("volumn") or "").split(",")
    sort_year = payload.get("sortYear") or []
    factor = utils.to_float(payload.get("priceFactor")) or 1000.0
    if not dates or len(raw_prices) < len(dates) * 4:
        return []
    # 年份还原：sortYear = [[year, count], ...]
    years: List[str] = []
    for entry in sort_year:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue
        years.extend([str(entry[0])] * int(entry[1]))
    if len(years) < len(dates):
        years.extend([years[-1] if years else "0000"] * (len(dates) - len(years)))
    items: List[Dict[str, Any]] = []
    previous_close: Optional[float] = None
    for index, md in enumerate(dates):
        base = index * 4
        try:
            low = int(raw_prices[base]) / factor
            open_p = low + int(raw_prices[base + 1]) / factor
            high = low + int(raw_prices[base + 2]) / factor
            close_p = low + int(raw_prices[base + 3]) / factor
        except (TypeError, ValueError):
            continue
        volume = utils.to_float(volumes[index]) if index < len(volumes) else None
        pct = None
        if previous_close and close_p and previous_close > 0:
            pct = round((close_p / previous_close - 1) * 100, 4)
        year = years[index] if index < len(years) else ""
        items.append(
            {
                "date": f"{year}-{md[:2]}-{md[2:]}" if len(md) == 4 else md,
                "open": round(open_p, 4),
                "high": round(high, 4),
                "low": round(low, 4),
                "close": round(close_p, 4),
                "volume": volume,
                "amount": None,
                "pct": pct,
            }
        )
        previous_close = close_p
    return items


def get_board_realtime(code: str) -> Dict[str, Any]:
    """取板块实时快照（同花顺 realhead，盘中为实时值）。

    字段编号含义（实测校验）：7=现价/指数点，8=最高，9=最低，10=均价，
    13=成交量(股)（与 all.js 的 volumn 逐值一致），19=总市值(元)，37/38/39=上涨/跌/平家数，
    199112=涨跌幅(%)，264648=涨跌额，526792=换手率(%)。
    **该接口不含成交额**，成交额请取行业行情页或 K 线的 last.js。

    Args:
        code: 同花顺板块代码。

    Returns:
        Dict[str, Any]: {price, high, low, avg_price, amount, market_cap,
        up, flat, down, pct, change, turnover, as_of}

    Raises:
        ValueError: 响应结构异常。
    """
    url = f"{config.THS_LINE_BASE}/v6/realhead/bk_{code}/last.js"
    payload = _parse_line_json(_get_text(url, referer=config.THS_REFERER))
    items = payload.get("items") or {}
    if not isinstance(items, dict) or not items:
        raise ValueError(f"同花顺板块实时快照结构异常（code={code}）")

    def pick(key: str) -> Optional[float]:
        """按字段编号取值。

        Args:
            key: 源站字段编号。

        Returns:
            Optional[float]: 数值或 None。
        """
        return _signed_number(items.get(key, ""))

    amount_yuan = None
    return {
        "price": pick("7"),
        "high": pick("8"),
        "low": pick("9"),
        "avg_price": pick("10"),
        "volume": pick("13"),
        "amount": round(amount_yuan / 1e8, 4) if amount_yuan is not None else None,
        "market_cap": pick("19"),
        "up": utils.to_int(items.get("37")),
        "flat": utils.to_int(items.get("38")),
        "down": utils.to_int(items.get("39")),
        "pct": pick("199112"),
        "change": pick("264648"),
        "turnover": pick("526792"),
        "as_of": utils.now_text(utils.bj_now()),
        "name": payload.get("name"),
    }


def board_code_to_name(code: str) -> Optional[str]:
    """由板块代码反查板块名（K 线响应自带 name 字段）。

    Args:
        code: 同花顺板块代码。

    Returns:
        Optional[str]: 板块名；取不到返回 None。
    """
    try:
        url = f"{config.THS_LINE_BASE}/v6/line/bk_{code}/01/last.js"
        payload = _parse_line_json(_get_text(url, referer=config.THS_REFERER))
        name = str(payload.get("name") or "").strip()
        return name or None
    except Exception:  # noqa: BLE001 - 反查失败不影响主流程
        return None


__all__ = [
    "get_industry_rows",
    "get_industry_codes",
    "get_concept_codes",
    "get_board_code",
    "get_board_cons",
    "get_board_kline",
    "get_board_realtime",
    "board_code_to_name",
]
