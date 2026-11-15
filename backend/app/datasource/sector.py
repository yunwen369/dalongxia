"""板块（sector）provider：行业/概念/热力图/摘要/K线/成分/资金流/趋势。

对应规范 v7.2 §6.4 / §8.6 与《设计基准说明书_阶段5.1》第十节 10.1。
所有 akshare 接口名均以本机 akshare 1.19.1 实测为准（2026-10-20 实测）：

- 行业行情：`stock_board_industry_summary_ths`（同花顺，90 行，成交额/净流入单位=亿元）
  → 备源 `stock_sector_spot(indicator="新浪行业")`（49 行，成交额单位=元）
  → 三级 `stock_sector_spot(indicator="证监会行业")` 在 1.19.1 实测抛 UnboundLocalError，**不可用**，
    故该级仅保留占位并如实记录，不当成可用通道（红线三：不掩盖）。
- 概念行情：`stock_sector_spot(indicator="概念")`（新浪，175 行）
  → 备源 `stock_board_concept_name_ths`（同花顺概念名录 375 行，仅名称/代码，无行情）
- 板块 K 线：`stock_board_industry_index_ths` → `stock_board_concept_index_ths`
  → 港美股走腾讯指数 K 线（境外简化建模）。
- 板块成分：新浪 `stock_sector_detail(label)`（label 需从新浪板块列表反查）
  → 申万 `sw_index_third_info` + `sw_index_third_cons` → 东财 `stock_board_industry_cons_em`
  （东财 push2 域在本机被源站重置，L-20，仅作生产环境备用）。
- 板块资金流：`stock_fund_flow_industry(symbol="即时")`（同花顺，净额/流入/流出，单位=亿元）。
  该源不提供超大单/大单分项，故 `super_big`/`big` 返回 None 并在接口说明书登记为源能力缺口，
  前端按 §9「空值显示无数据」呈现，不补 0、不编造。

本模块只负责取数与口径清洗；缓存与降级调度由 routers/sector.py + core.resilient 负责。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date
from typing import Any, Dict, List, Optional, Tuple


from backend.app import config
from backend.app.core import cache, memcache, netfix
from backend.app.core.source_prefs import ensure_enabled
from backend.app.core.resilient import polite_sleep, retry_call, retry_until
from backend.app.core.v8_guard import guard_v8
from backend.app.datasource import ashare, sina_flow, tencent, tencent_board, ths_http, utils
from backend.app.datasource.common import default_range as _common_default_range
from backend.app.datasource.common import call_akshare as _common_call_akshare

# 关键顺序：证书/代理修复必须先于 akshare import（与 ashare.py 保持一致）
netfix.apply_netfix()
from backend.app.datasource.sw_board_map import SW_INDEX_CODE
import akshare as ak  # noqa: E402

logger = logging.getLogger(__name__)

# 板块名负缓存：{板块名: 失效时间戳}，避免重复为「查无此板块」付出两次名录抓取
_BOARD_MISS: Dict[str, float] = {}

# 港美股「境外简化建模」的代表个股：无免费板块专项通道（D3 / 设计基准附录 B-1），
# 用「主要指数 + 代表个股」建模，前端徽章标注「境外简化建模」，不得冒充真实板块。
OVERSEAS_REPRESENTATIVES: Dict[str, List[str]] = {
    "hk": ["00700", "09988", "03690", "01810", "00981"],
    "us": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "TSLA"],
}


# P1 收敛：_call 统一委托 common.call_akshare（原 sector/news/overseas/stock 四处同体副本）。
_call = _common_call_akshare


def _call_ths(func) -> Any:
    """同花顺 V8 类调用的统一入口：进程级互斥 + 代理错误分类。

    同花顺 hexin-v 签名走 py_mini_racer（内嵌 V8），多线程并发首次创建
    MiniRacer 上下文会触发 PartitionAlloc 重复初始化 FATAL，整个 worker
    进程原生崩溃（实测并发必崩）。故全部同花顺调用经 v8_guard 串行化。

    Args:
        func: 待执行的无参取数函数。

    Returns:
        Any: 结果原样透传（类型由 loader/func 约定）。
    """
    return _call(guard_v8(func))


# P1 收敛：以下两个私有工具函数原在 sector/news/overseas/stock/fund/moneyflow/peers
# 中重复定义，现已统一委托 utils 的唯一实现。保留模块级名字供本文件调用点使用。
_num = utils.to_float
_round = utils.round_or_none


def _maybe_zero_none(value: Any, threshold: float = 0.0) -> Optional[float]:
    """把 0 / 零值 当作 None（量额为 0 意味着不可用，非真实缺量）。

    R3-07：同花顺 K 线在字段错位/备源无量时返回 volume=0、amount=25-29 个位数，
    这些零值或误值必须显式 null，禁止让前端显示 0 或假的个位数。

    Args:
        value: 原始值。
        threshold: 判零阈（默认 0，amount 可传 1 过滤疑似错位的个位数）。

    Returns:
        Optional[float]: 非零有效值原样返回；零值 / 不可解析返回 None。
    """
    num = _num(value)
    if num is None or num <= threshold:
        return None
    return num


# 名称→代码/标签的映射表变化极慢，进程内缓存 1 小时，避免每次 /cons 重复拉全表
_LOOKUP: Dict[str, Any] = {}
_LOOKUP_TS: Dict[str, float] = {}


def _lookup(key: str, ttl: int, loader) -> Any:
    """进程内映射表缓存（仅用于「板块名→代码」这类低频变动字典）。

    Args:
        key: 缓存键 / 字典键。
        ttl: 缓存有效期（秒）。
        loader: 缓存未命中时的加载函数（返回数据或 {data, error}）。

    Returns:
        Any: 结果原样透传（类型由 loader/func 约定）。
    """
    import time

    now = time.time()
    if key in _LOOKUP and now - _LOOKUP_TS.get(key, 0.0) < ttl:
        return _LOOKUP[key]
    value = loader()
    _LOOKUP[key] = value
    _LOOKUP_TS[key] = now
    return value


# =============================================================
# 一、行业 / 概念行情列表（cn 真实数据，hk/us 境外简化建模）
# =============================================================
def _industry_ths() -> List[Dict[str, Any]]:
    """同花顺行业行情（主源，纯 HTTP，无需 V8 签名）。

    2026-11-02 第八轮：原实现走 akshare ``stock_board_industry_summary_ths``
    （hexin-v 签名需 py_mini_racer），冻结形态被禁用后整块降级。实测同花顺
    「行情中心」页面无需签名即可取数，故改为 :mod:`ths_http` 直连，
    **V8 依赖从行业行情链路移除**（降级点 G-2 消除）。

    实测列：板块/涨跌幅(%)/总成交量(万手)/总成交额(亿元)/净流入(亿元)/上涨家数/下跌家数/领涨股。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。

    Raises:
        ValueError: 同花顺行业行情返回空数据。
    """
    ensure_enabled()
    items: List[Dict[str, Any]] = []
    for row in ths_http.get_industry_rows():
        items.append(
            {
                "name": row.get("name"),
                "code": row.get("code"),
                "pct": _round(_num(row.get("pct"))),
                "amount": _round(_num(row.get("amount")), 2),  # 亿元
                "net_inflow": _round(_num(row.get("net_inflow")), 2),  # 亿元
                "lead_stock": row.get("lead_stock"),
                "lead_pct": _round(_num(row.get("lead_pct"))),
                "up": utils.to_int(row.get("up")),
                "down": utils.to_int(row.get("down")),
                "kind": "行业",
            }
        )
    if not items:
        raise ValueError("同花顺行业行情返回空数据")
    return items


def _sina_sector_items(indicator: str, kind: str) -> List[Dict[str, Any]]:
    """新浪板块行情（通用：新浪行业/概念/地域）。

    实测列：label/板块/公司家数/涨跌幅(%)/总成交额(元)/个股-涨跌幅/股票名称。
    新浪不提供「板块主力净流入」，故 net_inflow=None（空值显示无数据）。

    Args:
        indicator: 指标名（如 预测年报每股收益）。
        kind: 类别：industry=行业 / concept=概念。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。

    Raises:
        ValueError: 参数不合法或上游数据不满足口径时抛出。
    """
    ensure_enabled()
    frame = _call(lambda: ak.stock_sector_spot(indicator=indicator))
    items: List[Dict[str, Any]] = []
    for row in utils.clean_records(frame):
        name = str(row.get("板块") or "").strip()
        if not name:
            continue
        amount_raw = _num(row.get("总成交额"))
        items.append(
            {
                "name": name,
                "label": row.get("label"),  # 供 /cons 反查新浪板块代码
                "pct": _round(_num(row.get("涨跌幅"))),
                "amount": _round(amount_raw / 1e8, 2) if amount_raw is not None else None,  # 元→亿元
                "net_inflow": None,
                "lead_stock": row.get("股票名称"),
                "lead_pct": _round(_num(row.get("个股-涨跌幅"))),
                "up": None,
                "down": None,
                "company_count": utils.to_int(row.get("公司家数")),
                "kind": kind,
            }
        )
    if not items:
        raise ValueError(f"新浪{indicator}返回空数据")
    return items


def _concept_ths_names() -> List[Dict[str, Any]]:
    """概念板块行情（腾讯概念榜，纯 HTTP，805 个）。

    2026-11-02 第八轮：原实现走 akshare ``stock_board_concept_name_ths``（V8 签名，
    且只给名称/代码无行情）。改用腾讯 ``board_type=gn`` 板块榜：含涨跌幅、成交额、
    换手率、量比、主力净流入、领涨股与 5/20/60 日涨幅，口径完整且无需签名。
    该通道同时承担「概念行情」与「概念 K 线 / 拥挤度」的代码体系（ptXXXXXXX）。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。

    Raises:
        ValueError: 腾讯概念榜返回空数据。
    """
    ensure_enabled()
    items: List[Dict[str, Any]] = []
    for row in tencent_board.get_board_rows("concept"):
        items.append(
            {
                "name": row.get("name"),
                "code": row.get("code"),
                "label": None,
                "pct": _round(_num(row.get("pct"))),
                "amount": _round(_num(row.get("amount")), 2),  # 亿元
                "net_inflow": _round(_num(row.get("net_inflow")), 2),  # 亿元
                "inflow": _round(_num(row.get("main_in")), 2),
                "outflow": _round(_num(row.get("main_out")), 2),
                "turnover_rate": _round(_num(row.get("turnover_rate")), 2),
                "volume_ratio": _round(_num(row.get("volume_ratio")), 2),
                "pct_5d": _round(_num(row.get("pct_5d")), 2),
                "pct_20d": _round(_num(row.get("pct_20d")), 2),
                "lead_stock": row.get("lead_stock"),
                "lead_pct": _round(_num(row.get("lead_pct"))),
                "up": None,
                "down": None,
                "kind": "概念",
            }
        )
    if not items:
        raise ValueError("腾讯概念板块榜返回空数据")
    return items


def get_us_sector_items() -> List[Dict[str, Any]]:
    """美股行业池：SPDR 11 大行业 ETF（真实行业口径，非东财行业板块）。

    Returns:
        list[dict]: 与行业列表同构的条目；amount 单位=亿美元（上游为 USD 元值）。

    Raises:
        ValueError: 上游返回空。
    """
    items: List[Dict[str, Any]] = []
    for quote in tencent.get_us_etf_quotes():
        amount = _num(quote.get("amount"))
        items.append(
            {
                "name": quote.get("name"),
                "code": quote.get("code"),
                "pct": _round(_num(quote.get("pct"))),
                "amount": _round(amount / 1e8, 2) if amount else None,
                "net_inflow": None,
                "lead_stock": None,
                "lead_pct": None,
                "up": None,
                "down": None,
                "kind": "行业ETF",
            }
        )
    if not items:
        raise ValueError("美股行业 ETF 返回空列表")
    return items


def get_hk_pool_items() -> List[Dict[str, Any]]:
    """港股池：恒生指数系列（含 3 个中国内地行业指数）+ 核心龙头个股。

    Notes:
        akshare 1.19.1 无港股行业板块免费通道（东财板块接口本机不可达），本函数按
        「指数系列 + 龙头」如实建模；指数类成交额源站不可信 → 置 None 不补 0。

    Returns:
        list[dict]: 与行业列表同构的条目；龙头 amount 单位=亿港元。

    Raises:
        ValueError: 上游返回空。
    """
    items: List[Dict[str, Any]] = []
    for quote in tencent.get_hk_pool_quotes():
        amount = _num(quote.get("amount"))
        items.append(
            {
                "name": quote.get("name"),
                "code": quote.get("code"),
                "pct": _round(_num(quote.get("pct"))),
                "amount": _round(amount / 1e8, 2) if amount else None,
                "net_inflow": None,
                "lead_stock": None,
                "lead_pct": None,
                "up": None,
                "down": None,
                "kind": quote.get("kind") or "龙头",
            }
        )
    if not items:
        raise ValueError("港股池返回空列表")
    return items


def _overseas_items(market: str) -> List[Dict[str, Any]]:
    """港美股「境外简化建模」：主要指数 + 代表个股（真实行情，非板块）。

    设计基准附录 B-1：akshare 1.19.1 无港美股板块专项免费通道，本批按此建模并如实标注口径。

    Args:
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。

    Raises:
        ValueError: 港美股代表标的返回空数据。
    """
    items: List[Dict[str, Any]] = []
    for quote in tencent.get_index_quotes(market):
        items.append(
            {
                "name": quote.get("name") or quote.get("code"),
                "code": quote.get("code"),
                "pct": _round(_num(quote.get("pct"))),
                # 指数成交额单位=元，转亿元便于与 A 股表格同列
                "amount": _round((_num(quote.get("amount")) or 0) / 1e8, 2) if quote.get("amount") else None,
                "net_inflow": None,
                "lead_stock": None,
                "lead_pct": None,
                "up": None,
                "down": None,
                "kind": "指数",
            }
        )
    for symbol in OVERSEAS_REPRESENTATIVES.get(market, []):
        try:
            quote = tencent.get_quote(symbol, market)
        except Exception as exc:  # noqa: BLE001 - 单只代表股失败不影响整体
            logger.warning("境外代表个股取数失败：%s %s（%s）", market, symbol, type(exc).__name__)
            continue
        items.append(
            {
                "name": quote.get("name") or symbol,
                "code": symbol,
                "pct": _round(_num(quote.get("pct"))),
                "amount": _round((_num(quote.get("amount")) or 0) / 1e8, 2) if quote.get("amount") else None,
                "net_inflow": None,
                "lead_stock": None,
                "lead_pct": None,
                "up": None,
                "down": None,
                "kind": "代表个股",
            }
        )
    if not items:
        raise ValueError("港美股代表标的返回空数据")
    return items


def get_industry(market: str = "cn") -> List[Dict[str, Any]]:
    """行业板块行情列表（cn 主源同花顺；hk/us 境外简化建模）。

    Args:
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。
    """
    if market == "hk":
        return get_hk_pool_items()
    if market == "us":
        return get_us_sector_items()
    return _industry_ths()


def get_industry_ths() -> List[Dict[str, Any]]:
    """行业行情·主源（同花顺）。供路由层降级链直接调用。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。
    """
    return _industry_ths()


def get_industry_sina() -> List[Dict[str, Any]]:
    """行业行情·备源（新浪行业）。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。
    """
    return _sina_sector_items("新浪行业", "行业")


def get_industry_csrc() -> List[Dict[str, Any]]:
    """行业行情·三级兜底（新浪证监会行业）。

    注意：akshare 1.19.1 实测该分支抛 UnboundLocalError（上游解析缺陷），
    保留调用入口以便上游修复后自动生效，当前命中必然失败并降级，不得计入可用通道。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。
    """
    return _sina_sector_items("证监会行业", "行业")


def get_concept(market: str = "cn") -> List[Dict[str, Any]]:
    """概念板块行情列表。港美股无免费概念通道，返回空列表由上层渲染空态。

    Args:
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。
    """
    if market in ("hk", "us"):
        return []
    return _concept_ths_names()


def get_concept_sina() -> List[Dict[str, Any]]:
    """概念行情·备源（新浪概念 175 个，无主力净流入）。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。
    """
    return _sina_sector_items("概念", "概念")


def get_concept_ths() -> List[Dict[str, Any]]:
    """概念行情·主源（腾讯概念榜 805 个，含主力净流入与多周期涨幅）。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。
    """
    return _concept_ths_names()


def get_overseas_items(market: str) -> List[Dict[str, Any]]:
    """港美股境外简化建模项（主要指数 + 代表个股）。

    Args:
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。
    """
    return _overseas_items(market)


# =============================================================
# 二、热力图 / 摘要（由行情列表派生，不额外请求）
# =============================================================
def heatmap_from_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """由行情列表派生热力图节点：面积用 abs(涨跌幅)，颜色/标签用原值（P1-10）。

    Args:
        items: 行业或概念行情列表（含 pct/amount/lead_stock）。

    Returns:
        List[Dict[str, Any]]: name/value(绝对值)/pct(原值)/amount/lead_stock。
    """
    nodes: List[Dict[str, Any]] = []
    for item in items:
        pct = _num(item.get("pct"))
        nodes.append(
            {
                "name": item.get("name"),
                # 负值不得钳为 0：面积取绝对值，方向由 pct 与颜色表达
                "value": abs(pct) if pct is not None else None,
                "pct": pct,
                "amount": item.get("amount"),
                # v3.7.0（⑦）：净流入随节点下发 —— 云图明细「净流入」列与资金流入/流出块同源
                "net_inflow": item.get("net_inflow"),
                "lead_stock": item.get("lead_stock"),
                "up": item.get("up"),
                "down": item.get("down"),
            }
        )
    return nodes


def summary_from_items(items: List[Dict[str, Any]], market: str = "cn") -> Dict[str, Any]:
    """由行情列表派生板块摘要：领涨/领跌/主力净流入 Top3 + 成交额集中度。

    Args:
        items: 记录列表。
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    def _pct_key(entry: Dict[str, Any]) -> float:
        """按涨跌幅排序的取值函数，空值按 0 处理。

        Args:
            entry: 组件条目（含 id 与 span）。

        Returns:
            float: 数值结果。
        """
        value = _num(entry.get("pct"))
        return value if value is not None else 0.0

    def _flow_key(entry: Dict[str, Any]) -> float:
        """按净流入排序的取值函数，空值按 0 处理。

        Args:
            entry: 组件条目（含 id 与 span）。

        Returns:
            float: 数值结果。
        """
        value = _num(entry.get("net_inflow"))
        return value if value is not None else 0.0

    with_pct = [item for item in items if _num(item.get("pct")) is not None]
    gainers = sorted(with_pct, key=_pct_key, reverse=True)[:3]
    losers = sorted(with_pct, key=_pct_key)[:3]
    with_flow = [item for item in items if _num(item.get("net_inflow")) is not None]
    inflow = sorted(with_flow, key=_flow_key, reverse=True)[:3]

    amounts = [(_num(item.get("amount")) or 0.0) for item in items]
    total = sum(amounts)
    top10 = sum(sorted(amounts, reverse=True)[:10])
    concentration = round(top10 / total * 100, 2) if total > 0 else None

    def _brief(entry: Dict[str, Any]) -> Dict[str, Any]:
        """摘要卡只保留必要字段，避免把整表塞进首页。

        Args:
            entry: 组件条目（含 id 与 span）。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
        return {
            "name": entry.get("name"),
            "pct": entry.get("pct"),
            "net_inflow": entry.get("net_inflow"),
            "amount": entry.get("amount"),
        }

    return {
        "market": market,
        "gainers": [_brief(item) for item in gainers],
        "losers": [_brief(item) for item in losers],
        "inflow": [_brief(item) for item in inflow],
        "concentration": concentration,
        "total_amount": _round(total, 2),
        "board_count": len(items),
    }


# =============================================================
# 三、板块 K 线 / 成分股
# =============================================================
def _default_kline_range(days: int = 400) -> Tuple[str, str]:
    """板块 K 线默认区间（第十轮 C17 收敛）：委托 ``common.default_range``。

    Args:
        days: 向前回溯的自然日天数。

    Returns:
        Tuple[str, str]: ``(起始日, 结束日)``，格式均为 YYYY-MM-DD。
    """
    return _common_default_range(days)


def _kline_ths(kind: str, board: str, period: str, *, fill_today: bool = True) -> List[Dict[str, Any]]:
    """板块指数 K 线（纯 HTTP：行业走同花顺、概念走腾讯）。

    2026-11-02 第八轮：原实现走 akshare ``stock_board_*_index_ths``（V8 签名），
    冻结形态不可用。实测两条纯 HTTP 通道可完整替代：
        - 行业：同花顺 ``d.10jqka.com.cn/v6/line/bk_{881xxx}``（日线 140 根含成交额，
          周/月线 984/231 根），按板块名经行业名录反查代码；
        - 概念：腾讯 ``newfqkline``（``ptXXXXXXX``，320 根含成交额），
          按板块名经概念榜反查代码。
    两者均**无 V8 依赖**，故冻结版与源码版行为一致（降级点 G-15 消除）。

    Args:
        kind: 类别：industry=行业 / concept=概念。
        board: 板块名称（与板块树中的名称一致）。
        period: 周期：日k/周k/月k，或分钟数（1/5/15/30/60）。
        fill_today: 是否补当日成交额（批量排行扫描时置 False 省请求）。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。

    Raises:
        ValueError: 板块名无代码映射或上游返回空数据。
    """
    ensure_enabled()
    period_key = _period_key(period)
    if kind == "industry":
        code = ths_http.get_board_code(board, kind="industry")
        if not code:
            raise ValueError(f"同花顺行业名录无此板块：{board}")
        hint = ths_http.get_industry_amount(board) if fill_today else None
        rows = ths_http.get_board_kline(
            code, period=period_key, fill_today=fill_today, amount_hint=hint
        )
        model_source = "同花顺"
    else:
        # R11 双链路（概念日 K）：腾讯概念榜为主源，失败退同花顺概念指数线（885xxx）。
        # 腾讯 newfqkline 偶发空返回/超时（即「接口不稳定」原始症状）；同花顺 885 线
        # 覆盖 294 个成熟概念（新股概念两源都无 60 日历史，属数据积累期，另计 failed）。
        code = _tencent_board_code(board, "concept")
        rows: List[Dict[str, Any]] = []
        model_source = "腾讯"
        tx_error: Optional[Exception] = None
        if code:
            try:
                rows = tencent_board.get_board_kline(code, period=period_key, limit=320) or []
            except Exception as exc:  # noqa: BLE001 - 主源失败转备源，不在此抛出
                tx_error = exc
        if not rows:
            index_codes = ths_http.get_concept_index_codes()
            ths_code = index_codes.get(board)
            if ths_code:
                try:
                    rows = ths_http.get_board_kline(
                        ths_code, period=period_key, fill_today=False
                    ) or []
                    if rows:
                        model_source = "同花顺概念指数(备)"
                except Exception as exc:  # noqa: BLE001 - 备源失败按主源错误归因
                    logger.info("概念 K 线备源也失败 %s：%s", board, type(exc).__name__)
        if not rows:
            reason = f"（主源异常 {type(tx_error).__name__}）" if tx_error else ""
            raise ValueError(f"腾讯概念榜无此板块或两源 K 线均为空：{board}{reason}")
    if not rows:
        raise ValueError(f"{model_source}板块 K 线为空：{board}")
    # 周/月线由源站直接给出（同花顺 11/21、腾讯 week/month），无需再聚合
    return [
        {
            "date": row.get("date"),
            "open": _round(_num(row.get("open"))),
            "high": _round(_num(row.get("high"))),
            "low": _round(_num(row.get("low"))),
            "close": _round(_num(row.get("close"))),
            "volume": _round(_maybe_zero_none(row.get("volume")), 0),
            "amount": _round(_maybe_zero_none(row.get("amount"), threshold=0.0001), 4),
            "pct": _round(_num(row.get("pct"))),
        }
        for row in rows
    ]


def _period_key(period: str) -> str:
    """把中文/英文周期统一为 daily/weekly/monthly。

    Args:
        period: 周期：日k/周k/月k，或 daily/weekly/monthly。

    Returns:
        str: daily / weekly / monthly（无法识别时按 daily）。
    """
    text = str(period or "daily").strip().lower()
    if text in ("weekly", "周k", "week", "w"):
        return "weekly"
    if text in ("monthly", "月k", "month", "m"):
        return "monthly"
    return "daily"


# 腾讯板块「名称 → 代码」缓存：{kind: (时间戳, {名称: 代码})}
_TX_BOARD_CACHE = memcache.get_cache("tx_board_map")  # B2 收编：原手写 {kind: (ts, mapping)}，TTL config.TTL["tx_board_map"]


def _tencent_board_code(name: str, kind: str = "concept") -> Optional[str]:
    """按板块名取腾讯板块代码（ptXXXXXXX，进程内缓存 10 分钟）。

    Args:
        name: 板块名。
        kind: industry / concept。

    Returns:
        Optional[str]: 代码；未命中返回 None。
    """
    key = f"tx:{kind}"
    cached = _TX_BOARD_CACHE.get(key)
    if cached and time.time() - cached[0] < 600:
        return cached[1].get(str(name or "").strip())
    mapping: Dict[str, str] = {}
    try:
        for row in tencent_board.get_board_rows(kind):
            if row.get("name") and row.get("code"):
                mapping[row["name"]] = row["code"]
    except Exception as exc:  # noqa: BLE001 - 名录不可用则本次返回 None
        logger.info("腾讯%s板块名录不可用：%s", "行业" if kind == "industry" else "概念", type(exc).__name__)
        return None
    _TX_BOARD_CACHE[key] = (time.time(), mapping)
    return mapping.get(str(name or "").strip())


def _aggregate_period(rows: List[Dict[str, Any]], period: str) -> List[Dict[str, Any]]:
    """把日线升频为周线/月线（开=首日开，收=末日收，高低取极值，量额求和）。

    Args:
        rows: 记录行列表。
        period: 周期：日k/周k/月k，或分钟数（1/5/15/30/60）。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。
    """
    key = (period or "daily").lower()
    if key in ("daily", "日k", "day"):
        return rows

    def bucket(date_text: str) -> str:
        """按周（ISO 周）或月分组，返回分组键。

        Args:
            date_text: 日期文本。

        Returns:
            str: 分组键（如 2026-W43 / 2026-10）。
        """
        try:
            day = date.fromisoformat(date_text)
        except ValueError:
            return date_text[:7]
        if key in ("weekly", "周k", "week"):
            year, week, _ = day.isocalendar()
            return f"{year}-W{week:02d}"
        return date_text[:7]

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(bucket(row["date"]), []).append(row)

    result: List[Dict[str, Any]] = []
    for _, group in grouped.items():
        first, last = group[0], group[-1]
        result.append(
            {
                "date": last["date"],
                "open": first["open"],
                "high": max((_num(item["high"]) or 0.0) for item in group),
                "low": min((_num(item["low"]) or 0.0) for item in group),
                "close": last["close"],
                "volume": _round(_maybe_zero_none(sum((_num(item["volume"]) or 0.0) for item in group)), 0),
                "amount": _round(_maybe_zero_none(sum((_num(item["amount"]) or 0.0) for item in group), threshold=1.0), 0),
            }
        )
    return result


def get_sector_kline(board: str, period: str = "daily", market: str = "cn") -> Dict[str, Any]:
    """板块指数 K 线。返回 {items, board, period, index_code}。

    降级链：同花顺行业 → 同花顺概念 → 港美股/指数走腾讯指数 K 线。

    Args:
        board: 板块名称（与板块树中的名称一致）。
        period: 周期：日k/周k/月k，或分钟数（1/5/15/30/60）。
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。

    Raises:
        ValueError: board 参数不可为空。
    """
    if not board:
        raise ValueError("board 参数不可为空")

    if market in ("hk", "us"):
        # 境外简化建模：板块名若命中主要指数则取指数 K 线，否则按错误处理（上层给空态）
        for meta in config.MARKET_INDEXES:
            if meta["market"] == market and meta["name"] == board:
                items = tencent.get_index_kline(meta["code"], period)
                return {"board": board, "period": period, "items": items, "index_code": meta["code"],
                        "model": "境外简化建模"}
        raise ValueError(f"港美股暂无板块 K 线：{board}")

    errors: List[str] = []
    for kind in ("industry", "concept"):
        try:
            items = _kline_ths(kind, board, period)
            return {"board": board, "period": period, "items": items, "index_code": None,
                    "model": "行业" if kind == "industry" else "概念"}
        except Exception as exc:  # noqa: BLE001 - 记录后尝试下一级
            errors.append(f"{kind}:{type(exc).__name__}")
            logger.info("板块K线降级：%s %s 失败（%s）", kind, board, type(exc).__name__)
    # A 股备源：申万行业指数日线（纯 HTTP，冻结环境可用；命名不一致时按缺失处理）
    try:
        items = _kline_sw(board, period)
        return {"board": board, "period": period, "items": items, "index_code": SW_INDEX_CODE.get(board),
                "model": "申万指数日线"}
    except Exception as exc:  # noqa: BLE001 - 映射缺失或接口失败 → 透出给上层空态
        errors.append(f"申万:{type(exc).__name__}")
    raise ValueError("板块 K 线全部通道失败：" + "，".join(errors))


def is_known_board(board: str) -> bool:
    """判断板块名是否存在于任一板块名录（同花顺行业 / 腾讯概念 / 申万映射）。

    用途：把「板块名写错」与「通道故障」区分开 —— 前者是空态（给可选列表指引），
    后者是错误态（提示稍后重试），符合原型 §7.1「空/错严格分离」。

    Args:
        board: 板块名。

    Returns:
        bool: 名录中存在返回 True。
    """
    name = str(board or "").strip()
    if not name:
        return False
    try:
        if name in ths_http.get_industry_codes():
            return True
    except Exception as exc:  # noqa: BLE001 - 名录不可用时不阻断判断
        logger.info("同花顺行业名录不可用，继续用腾讯/申万判定 %s：%s", name, type(exc).__name__)
    try:
        if _tencent_board_code(name, "concept"):
            return True
    except Exception as exc:  # noqa: BLE001 - 腾讯板块码不可用时不阻断判断
        logger.info("腾讯板块码查询失败，继续用申万名录判定 %s：%s", name, type(exc).__name__)
    return name in SW_INDEX_CODE


def _kline_sw(board: str, period: str) -> List[Dict[str, Any]]:
    """申万行业指数日线（纯 HTTP 备源，冻结环境可用）。

    来源：`ak.index_hist_sw(symbol=代码)`，列 代码/日期/收盘/开盘/最高/最低/成交量/成交额。
    映射表由 `sw_board_map.SW_INDEX_CODE`（497 个申万一/二/三级行业，见该文件说明）承载；
    同花顺板块名与申万行业名命名不一致时按缺失处理，不伪造指数。

    Args:
        board: 板块名称（与板块树中的名称一致）。
        period: 周期：日k/周k/月k，或分钟数（1/5/15/30/60）。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。

    Raises:
        ValueError: 板块名无申万映射或接口返回空数据时抛出。
    """
    code = SW_INDEX_CODE.get(board)
    if not code:
        raise ValueError(f"申万行业映射缺失：{board}")
    ensure_enabled()
    symbol = code.split(".")[0]
    frame = _call(lambda: ak.index_hist_sw(symbol=symbol, period="day"))
    rows: List[Dict[str, Any]] = []
    for row in utils.clean_records(frame):
        rows.append(
            {
                "date": str(row.get("日期"))[:10],
                "open": _round(_num(row.get("开盘"))),
                "high": _round(_num(row.get("最高"))),
                "low": _round(_num(row.get("最低"))),
                "close": _round(_num(row.get("收盘"))),
                # R3-07：volume/amount 零值 → None，避免前端显示 0 或假的个位数
                "volume": _round(_maybe_zero_none(row.get("成交量")), 0),
                "amount": _round(_maybe_zero_none(row.get("成交额"), threshold=1.0), 0),
            }
        )
    if not rows:
        raise ValueError(f"申万行业指数日线为空：{board}")
    return _aggregate_period(rows, period)


def _sina_label(board: str) -> Optional[str]:
    """在新冠板块列表中反查 label（stock_sector_detail 必须传 label 而非名称）。

    板块名录变动极慢，映射表进程内缓存 1 小时，避免每次请求都拉两张全表。

    Args:
        board: 板块名称（与板块树中的名称一致）。

    Returns:
        str | None: 文本；缺失返回 None。
    """

    def build() -> Dict[str, str]:
        """构建 {板块名: label} 映射（概念优先，其次新浪行业）。

        Returns:
            dict: Dict[str, str] 结构的结果。
        """
        mapping: Dict[str, str] = {}
        for indicator in ("新浪行业", "概念", "地域"):
            try:
                frame = _call(lambda: ak.stock_sector_spot(indicator=indicator))
            except Exception as exc:  # noqa: BLE001 - 单个分类失败不影响整体映射
                logger.info("板块映射：%s 分类取数失败，跳过：%s", indicator, type(exc).__name__)
                continue
            for row in utils.clean_records(frame):
                name = str(row.get("板块") or "").strip()
                label = str(row.get("label") or "").strip()
                if name and label and name not in mapping:
                    mapping[name] = label
        return mapping

    mapping = _lookup("sina:board-labels", 3600, build)
    return mapping.get(board)


def _cons_sina(board: str) -> List[Dict[str, Any]]:
    """新浪板块成分（主源）。

    实测列：code/name/changepercent/trade/amount/nmc(流通市值)/mktcap。
    权重按流通市值占比计算（新浪不直接提供权重）。

    Args:
        board: 板块名称（与板块树中的名称一致）。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。

    Raises:
        ValueError: 参数不合法或上游数据不满足口径时抛出。
    """
    label = _sina_label(board)
    if not label:
        raise ValueError(f"新浪板块列表未找到：{board}")
    ensure_enabled()
    frame = _call(lambda: ak.stock_sector_detail(sector=label))
    raw_rows = utils.clean_records(frame)
    total_nmc = sum((_num(row.get("nmc")) or 0.0) for row in raw_rows)
    items: List[Dict[str, Any]] = []
    for row in raw_rows:
        nmc = _num(row.get("nmc"))
        items.append(
            {
                "code": str(row.get("code") or ""),
                "name": row.get("name"),
                "pct": _round(_num(row.get("changepercent"))),
                "price": _round(_num(row.get("trade"))),
                "amount": _round((_num(row.get("amount")) or 0.0) / 1e8, 2) if row.get("amount") is not None else None,
                "weight": _round(nmc / total_nmc * 100, 2) if (nmc and total_nmc) else None,
            }
        )
    if not items:
        raise ValueError(f"新浪板块成分为空：{board}")
    return items


def _cons_sw(board: str) -> List[Dict[str, Any]]:
    """申万行业成分（备源）。

    实测：`sw_index_third_info`（335 个三级）/ `sw_index_second_info`（131 个二级）/
    `sw_index_first_info`（31 个一级）均返回 行业代码/行业名称；
    `sw_index_third_cons(symbol=行业代码)` 对一/二/三级代码均可返回成分
    （实测 801081.SI 半导体二级返回 182 只），列含 股票代码/股票简称/价格/市值/近1日涨幅。

    Args:
        board: 板块名称（与板块树中的名称一致）。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。

    Raises:
        ValueError: 参数不合法或上游数据不满足口径时抛出。
    """
    ensure_enabled()

    def build() -> Dict[str, str]:
        """构建 {申万行业名: 行业代码} 映射（只取一级+二级，三级按需加载）。

        申万三个层级的 HTML 页面偶发返回空文档（akshare 解析抛 AttributeError），
        因此按「一级→二级」顺序合并，且必须拿到一级名录才允许缓存，
        避免把残缺映射写进进程缓存导致后续请求持续失败。

        三级名录单独缓存（P1-04）：三级页最慢且多数板块名在一/二级即可命中，
        默认不拉取可将成分股首次加载从 15 秒级降到 6 秒级。

        Returns:
            dict: Dict[str, str] 结构的结果。

        Raises:
            ValueError: 申万行业名录暂不可用（一级名录未取到）。
        """
        mapping: Dict[str, str] = {}
        first_ok = False
        for loader, level in (
            (ak.sw_index_first_info, "一级"),
            (ak.sw_index_second_info, "二级"),
        ):
            try:
                info = utils.clean_records(_call(loader))
            except Exception as exc:  # noqa: BLE001 - 单级失败继续下一级
                logger.info("申万%s名录暂不可用（%s）", level, type(exc).__name__)
                continue
            if level == "一级" and info:
                first_ok = True
            for row in info:
                name = str(row.get("行业名称") or "").strip()
                code = str(row.get("行业代码") or "").strip()
                if name and code and name not in mapping:
                    mapping[name] = code
        if not first_ok:
            raise ValueError("申万行业名录暂不可用（一级名录未取到）")
        return mapping

    def build_third() -> Dict[str, str]:
        """按需构建三级行业映射（仅在名称未命中一/二级时才拉取）。

        Returns:
            dict: Dict[str, str] 结构的结果。

        Raises:
            ValueError: 三级名录暂时不可用（页面偶发空表）。
        """
        mapping: Dict[str, str] = {}
        try:
            info = utils.clean_records(_call(ak.sw_index_third_info))
        except Exception as exc:  # noqa: BLE001 - 统一转为可降级异常
            raise ValueError(f"申万三级名录暂不可用：{type(exc).__name__}") from exc
        for row in info or []:
            name = str(row.get("行业名称") or "").strip()
            code = str(row.get("行业代码") or "").strip()
            if name and code:
                mapping[name] = code
        if not mapping:
            raise ValueError("申万三级名录为空")
        return mapping

    # 负缓存（P1-03）：确认查不到的板块名 10 分钟内直接失败，
    # 不再重复抓「一/二级 + 三级」两轮 HTML 名录（单轮约 5 秒）。
    miss_until = _BOARD_MISS.get(board, 0.0)
    if miss_until > time.time():
        raise ValueError(f"申万行业未找到：{board}")
    # 静态名录优先（497 个申万一/二/三级行业名）：命中即免去名录抓取（P1-04）
    target = SW_INDEX_CODE.get(board)
    if not target:
        target = _lookup("sw:industry-map", 3600, build).get(board)
    if not target:
        # 一/二级未命中时才拉三级名录（懒加载，避免每次冷启动都多等一个慢页面）
        try:
            target = _lookup("sw:industry-map:l3", 3600, build_third).get(board)
        except ValueError as exc:
            logger.info("申万三级名录不可用：%s", exc)
            target = None
    if not target:
        _BOARD_MISS[board] = time.time() + 600
        raise ValueError(f"申万行业未找到：{board}")

    # 申万成分页对连续请求敏感（返回空表）：akshare 空表**不抛异常**，
    # 单靠 retry_call 无法覆盖，故用 retry_until（RF-20261112-01 阶段 1 收编，
    # 原手写 (0.0, 1.0, 2.5) 外层退避等价迁移）按「行数非空」重试，三次仍空
    # 才判定通道异常（R2-03 回归中被真实触发，属数据可用性缺陷）。
    def _fetch_sw_cons() -> List[Dict[str, Any]]:
        polite_sleep()  # 限频：每次外层尝试前（原手写循环同位保留）
        return utils.clean_records(
            retry_call(lambda: ak.sw_index_third_cons(symbol=target), retries=3, backoff=(1.0, 2.5, 5.0))
        )

    rows: List[Dict[str, Any]] = retry_until(
        _fetch_sw_cons,
        ok=bool,
        retries=2,
        backoff=(1.0, 2.5),
        on_fail=lambda exc, n: logger.warning(
            "申万成分解析异常（第 %d/3 次）：%s（%s）", n, board, type(exc).__name__
        ),
    ) or []
    # 第五轮阶段 5：akshare 解析申万空表/结构变动时会抛 KeyError(slice) 等
    # （实测首次冷启动偶发一次，第二次即恢复）。这类异常属"本次无数据"，
    # 不能穿透到降级链顶层当作整体失败，否则会把夜间单源抖动放大成全链路不可用
    # ——现由 retry_until 的 on_fail 承接（记日志、该轮按空处理）。
    if not rows:
        logger.warning("申万成分为空：%s", board)
    if not rows:
        raise ValueError(f"申万成分为空：{board}")
    total_cap = sum((_num(row.get("市值")) or 0.0) for row in rows)
    pct_col = next((key for key in rows[0].keys() if str(key).startswith("近1日涨幅")), None)
    items: List[Dict[str, Any]] = []
    for row in rows:
        cap = _num(row.get("市值"))
        code = str(row.get("股票代码") or "")
        items.append(
            {
                "code": code.split(".")[0],
                "name": row.get("股票简称"),
                "pct": _round(_num(row.get(pct_col))) if pct_col else None,
                "price": _round(_num(row.get("价格"))),
                "amount": None,
                "weight": _round(cap / total_cap * 100, 2) if (cap and total_cap) else None,
            }
        )
    return items


def _cons_em(board: str) -> List[Dict[str, Any]]:
    """东财板块成分（生产环境备用；本机 push2 被源站重置，见 L-20）。

    Args:
        board: 板块名称（与板块树中的名称一致）。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。

    Raises:
        ValueError: 参数不合法或上游数据不满足口径时抛出。
    """
    ensure_enabled()
    frame = _call(lambda: ak.stock_board_industry_cons_em(symbol=board))
    rows = utils.clean_records(frame)
    total_cap = sum((_num(row.get("流通市值")) or 0.0) for row in rows)
    items: List[Dict[str, Any]] = []
    for row in rows:
        cap = _num(row.get("流通市值"))
        items.append(
            {
                "code": str(row.get("代码") or ""),
                "name": row.get("名称"),
                "pct": _round(_num(row.get("涨跌幅"))),
                "price": _round(_num(row.get("最新价"))),
                "amount": _round((_num(row.get("成交额")) or 0.0) / 1e8, 2) if row.get("成交额") is not None else None,
                "weight": _round(cap / total_cap * 100, 2) if (cap and total_cap) else None,
            }
        )
    if not items:
        raise ValueError(f"东财板块成分为空：{board}")
    return items


def _cons_ths(board: str) -> List[Dict[str, Any]]:
    """同花顺板块成分（主源，纯 HTTP 详情页，行业/概念均可）。

    2026-11-02 第八轮：同花顺板块详情页无需 hexin-v 签名，覆盖行业（``/thshy``）与
    概念（``/gn``），每页 10/20 行、可翻页取全；字段含现价/涨跌幅/成交额/流通市值/总市值/PE。
    权重按流通市值占比计算（与东财/申万口径一致）。

    Args:
        board: 板块名称（与板块树中的名称一致）。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。

    Raises:
        ValueError: 板块名无代码映射或成分页不可用。
    """
    ensure_enabled()
    code = ths_http.get_board_code(board, kind="industry") or ths_http.get_board_code(
        board, kind="concept"
    )
    if not code:
        raise ValueError(f"同花顺板块名录无此板块：{board}")
    kind = "industry" if code.startswith("88") else "concept"
    rows = ths_http.get_board_cons(code, kind=kind)
    total_cap = sum((_num(row.get("float_cap")) or 0.0) for row in rows)
    items: List[Dict[str, Any]] = []
    for row in rows:
        cap = _num(row.get("float_cap"))
        items.append(
            {
                "code": row.get("code"),
                "name": row.get("name"),
                "pct": _round(_num(row.get("pct"))),
                "price": _round(_num(row.get("price"))),
                "amount": _round(_num(row.get("amount")), 4),
                "weight": _round(cap / total_cap * 100, 2) if (cap and total_cap) else None,
            }
        )
    if not items:
        raise ValueError(f"同花顺板块成分为空：{board}")
    return items


def get_cons_ths(board: str) -> Dict[str, Any]:
    """成分股·同花顺主源，返回结构供路由降级链直接使用。

    Args:
        board: 板块名称（与板块树中的名称一致）。

    Returns:
        dict: {board, items, source_hint}
    """
    result = {"board": board, "items": _sort_cons(_cons_ths(board)), "source_hint": None}
    _remember_cons(board, result["items"])
    return result


def _remember_cons(board: str, items: List[Dict[str, Any]]) -> None:
    """把成分股代码列表写入 ``_CONS_CACHE``（供 R5/拥挤度等聚合维度复用）。

    Args:
        board: 板块名。
        items: 成分股记录列表（含 ``code`` 字段）。
    """
    codes = [str(item.get("code")) for item in items if item.get("code")]
    if not codes:
        return
    _load_cons_cache()
    if _CONS_CACHE.get(board) != codes:
        _CONS_CACHE[board] = codes
        _save_cons_cache()


def _sort_cons(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """成分股按涨跌幅降序排列，缺失涨跌幅的排在末尾（不参与比较）。

    Args:
        items: 记录列表。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。
    """
    return sorted(items, key=lambda item: (_num(item.get("pct")) is None, -(_num(item.get("pct")) or 0.0)))


def get_cons_sina(board: str) -> Dict[str, Any]:
    """成分股·主源（新浪），返回结构供路由降级链直接使用。

    Args:
        board: 板块名称（与板块树中的名称一致）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    _ensure_board_known(board)
    items = _sort_cons(_cons_sina(board))
    _remember_cons(board, items)
    return {"board": board, "items": items, "source_hint": "新浪"}


def get_cons_sw(board: str) -> Dict[str, Any]:
    """成分股·备源（申万，支持一/二/三级行业名）。

    Args:
        board: 板块名称（与板块树中的名称一致）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    _ensure_board_known(board)
    items = _sort_cons(_cons_sw(board))
    _remember_cons(board, items)
    return {"board": board, "items": items, "source_hint": "申万"}


def get_cons_em(board: str) -> Dict[str, Any]:
    """成分股·三级兜底（东财；本机 push2 被重置，仅生产备用）。

    Args:
        board: 板块名称（与板块树中的名称一致）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    _ensure_board_known(board)
    items = _sort_cons(_cons_em(board))
    _remember_cons(board, items)
    return {"board": board, "items": items, "source_hint": "东财"}


# 板块成分「不存在/暂无」冷却表（R2-10）：与 P1-03 同策略——先做本地存在性闸门，
# 不存在立即失败，不再让三个源各等满超时（原实测 12.4s）。
_CONS_MISS: Dict[str, float] = {}
_KNOWN_NAMES_KEY = "sector:known-names"

def _known_board_names() -> set:
    """汇总可判定的板块名（申万名录 + 本地板块树 + 同花顺行业/概念）。

    Returns:
        set: 板块名集合（空串已剔除）。
    """
    names = set(SW_INDEX_CODE)
    try:
        from backend.app.datasource.news import BOARD_TREE  # 延迟导入避免循环依赖

        for level1 in BOARD_TREE:
            names.add(level1["name"])
            for level2 in level1["children"]:
                names.add(level2["name"])
                for leaf in level2["children"]:
                    names.add(leaf["name"])
    except Exception as exc:  # noqa: BLE001 - 板块树不可用时仅靠申万名录
        logger.info("板块树不可用，仅用申万名录判定：%s", type(exc).__name__)
    for getter in (get_industry, get_concept):
        try:
            for row in getter("cn"):
                # 同花顺行业/概念行字段为 name；申万等其他源可能用 名称
                names.add(str(row.get("name") or row.get("名称") or "").strip())
        except Exception as exc:  # noqa: BLE001 - 单个列表失败不影响判定
            logger.info("板块名录取数失败，跳过该来源：%s", type(exc).__name__)
            continue
    return {name for name in names if name}


def _ensure_board_known(board: str) -> None:
    """板块存在性闸门（R2-10）：不存在直接快速失败，命中冷却表则立即返回。

    Args:
        board: 板块名称。

    Raises:
        ValueError: 板块不在任何已知名录中，或处于冷却期。
    """
    if _CONS_MISS.get(board, 0.0) > time.time():
        raise ValueError(f"板块成分为空或不存在（冷却中）：{board}")
    names = _lookup(_KNOWN_NAMES_KEY, 60, _known_board_names)
    if board not in names:
        _CONS_MISS[board] = time.time() + 600
        logger.info("板块不存在，成分请求快速失败：%s", board)
        raise ValueError(f"板块不存在或暂无成分数据：{board}")


# =============================================================
# 四、板块资金流 / 趋势对比
# =============================================================
def _fundflow_em_fallback() -> Tuple[List[Dict[str, Any]], str]:
    """同花顺资金流不可用时的备源：东财「板块资金流排行（今日·行业）」。

    2026-11-06 实测（同花顺被限流期间）：496 行、自带超大单/大单/中单/小单四档，
    单次约 0.9s，无需成分股聚合 —— 既补净额也补四档，是比「新浪成分股聚合」
    更省事的一条链路。

    口径差异（必须在 caliber 与 flow_source 里写明）：
        - 东财给的是「主力/超大/大/中/小」净额（元），同花顺给「净流入/流入/流出」（亿元），
          两套不可跨行相加，故本函数**只用于同花顺整表不可用时的整体切换**，不混行；
        - 东财备源没有「流入/流出」总额，`inflow`/`outflow` 置 None，前端按「无数据」展示。

    Returns:
        Tuple[List[Dict[str, Any]], str]: (行业行列表, 数据源文案)。
    """
    import akshare as ak

    frame = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
    rows: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for _, record in frame.iterrows():
        name = str(record.get("名称") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)

        def _yi(key: str) -> Optional[float]:
            """元 → 亿元（东财给原始元）。

            Args:
                key: 当前行记录里的字段名。

            Returns:
                Optional[float]: 亿元数值（保留 2 位）；缺值或不可转换返回 None。
            """
            value = record.get(key)
            if value is None or (isinstance(value, float) and value != value):
                return None
            try:
                return round(float(value) / 1e8, 2)
            except (TypeError, ValueError):
                return None

        rows.append({
            "name": name,
            "pct": _round(_num(record.get("今日涨跌幅")), 2),
            "net_inflow": _yi("今日主力净流入-净额"),
            "inflow": None,
            "outflow": None,
            "flow_source": "东财（板块资金流排行·今日）",
            "super_big": _yi("今日超大单净流入-净额"),
            "big": _yi("今日大单净流入-净额"),
            "medium": _yi("今日中单净流入-净额"),
            "small": _yi("今日小单净流入-净额"),
            "covered": None,
        })
    return rows, "东财（同花顺不可用时备源）"


def get_fundflow(market: str = "cn") -> List[Dict[str, Any]]:
    """板块资金流向（同花顺行业资金流 + 新浪个股四档聚合）。

    2026-11-02 第八轮重写：
        - 净额/流入/流出：同花顺「资金流向」频道页面（``data.10jqka.com.cn/funds/hyzjl``），
          纯 HTTP、90 个行业、单位亿元 —— 解决冻结形态整表为空的问题；
        - 超大单/大单/中单/小单：按板块成分股逐只取新浪四档后聚合（:mod:`sina_flow`），
          解决降级点 G-1「板块资金流无超大单/大单分项」。

    四档聚合依赖「板块成分股」映射：映射首次构建需逐板块翻页（同花顺有 1.1s 限流），
    由 :func:`warm_fundflow_tiers` 在服务启动后后台完成并落盘缓存 7 天；
    完成后每次刷新只需 90 次新浪批量请求（约 3 秒）。未预热到的板块四档为 None，
    caliber 会写明「四档聚合预热中」，不补 0、不估算。

    Args:
        market: 市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。

    Raises:
        ValueError: 同花顺板块资金流返回空数据。
    """
    if market in ("hk", "us"):
        return []
    ensure_enabled()
    try:
        rows = ths_http.get_industry_flow_rows()
    except Exception as exc:  # noqa: BLE001 - 同花顺限流/改版时切东财备源，避免整表空
        logger.info("同花顺行业资金流不可用（%s），切换东财备源", type(exc).__name__)
        fallback_rows, _source = _fundflow_em_fallback()
        if not fallback_rows:
            raise
        return fallback_rows
    # 同花顺反爬时可能返回空表或仅 1–2 条（非异常、非空），此时切东财备源。
    # 阈值 45：正常 90 条，限流时通常 < 10 条；45 既能挡住限流降级，又不误伤正常数据。
    if len(rows) < 45:
        logger.warning("同花顺行业资金流仅返回 %d 条（<45，疑似反爬降级），切换东财备源", len(rows))
        fallback_rows, _source = _fundflow_em_fallback()
        if len(fallback_rows) >= len(rows):
            return fallback_rows
        logger.warning("东财备源仅 %d 条，少于同花顺 %d 条，退回同花顺数据", len(fallback_rows), len(rows))
    # 四档依赖成分映射；未预热完时按时间预算补 1–2 个板块，
    # 使「首次使用也能逐步补齐」，而不是永久空白（预算内补不完由后台线程继续）。
    _ensure_cons_progress(rows, budget_seconds=6.0, max_boards=2)
    # 涨跌幅与 /api/sector/industry 同源对齐（2026-11-12 全量回归 R-02）：
    # 行情链路取「行情列表」的 pct，资金流链路取「资金流列表」的 pct，
    # 两个同花顺接口刷新节奏不同 → 同一板块两处可差 0.1–0.3pp，用户对不上账。
    # 这里复用行情链路的**已缓存**结果覆盖 pct（不额外打源站）；缓存未命中则保留原值，
    # 并在 caliber 里保留「pct 以行情列表为准、未命中时为本链路值」的说明。
    quote_pct: Dict[str, Any] = {}
    try:
        cached_industry = cache.get(f"sector:industry:{market}", config.TTL["sector"])
        # 缓存里存的是**信封**（{data, source, caliber, error...}），items 在 data 内；
        # 兼容裸结构，避免 provider 形态变化时静默失效。
        payload = cached_industry or {}
        if isinstance(payload.get("value"), dict):
            payload = payload["value"]
        rows_src = payload.get("data") if "data" in payload else payload
        # 缓存形态随链路而变：provider 返回列表 → 信封里 data 是 list；
        # 若已被路由层打包，则是 {"items": [...]}。两种都要认。
        if isinstance(rows_src, dict):
            rows_src = rows_src.get("items") or []
        for quote in rows_src or []:
            if isinstance(quote, dict) and quote.get("name") and quote.get("pct") is not None:
                quote_pct[quote["name"]] = quote["pct"]
    except Exception as exc:  # noqa: BLE001 - 对齐失败不影响资金流主功能
        logger.info("资金流 pct 同源对齐跳过：%s", type(exc).__name__)
    items: List[Dict[str, Any]] = []
    # v3.7.1：先把整表四档并发预热（原串行 90 次 HTTP 占整链 92%），循环内只读缓存。
    _prefetch_flow_tiers(rows)
    for row in rows:
        name = row.get("name")
        tier = _flow_tier(name, row.get("code")) or {}
        if tier.get("super_big") is not None:
            # 口径一致性（2026-11-02 交叉验证发现）：同花顺「净流入」与新浪四档聚合
            # 属两套统计口径（前者主力单口径，后者全单四档），直接并列会让「四档之和
            # 大于净额」。故**同一行只用一套口径**：已聚合板块的净额/流入/流出
            # 一律取新浪聚合值，四档与净额同源自洽；未聚合板块仍用同花顺。
            net_inflow = tier.get("net_inflow")
            inflow = tier.get("inflow")
            outflow = tier.get("outflow")
            flow_source = "新浪（成分股四档聚合）"
        else:
            net_inflow = _round(_num(row.get("net_inflow")), 2)
            inflow = _round(_num(row.get("inflow")), 2)
            outflow = _round(_num(row.get("outflow")), 2)
            flow_source = "同花顺"
        items.append(
            {
                "name": name,
                "pct": quote_pct.get(name, _round(_num(row.get("pct")))),
                "net_inflow": _round(_num(net_inflow), 2),
                "inflow": _round(_num(inflow), 2),
                "outflow": _round(_num(outflow), 2),
                "flow_source": flow_source,
                # 四档：由成分股四档聚合（真实值）；未预热到的板块为 None
                "super_big": tier.get("super_big"),
                "big": tier.get("big"),
                "medium": tier.get("medium"),
                "small": tier.get("small"),
                "covered": tier.get("covered"),
            }
        )
    if not items:
        raise ValueError("同花顺板块资金流返回空数据")
    return items


# 板块四档缓存：{板块名: (时间戳, {super_big, big, medium, small, covered})}
_FLOW_TIER_CACHE = memcache.get_cache("flow_tier")  # B2 收编：原手写 {board: (ts, payload)}，TTL config.TTL["flow_tier"]
# 板块四档并发预取的线程数（v3.7.1）：90 个板块各 1 次新浪批量请求，
# 串行实测 13.9s；8 路并发实测 ~2s 且无失败（新浪批量接口无严格限流）。
FLOW_TIER_WORKERS = 8
# 板块成分映射：{板块名: [6 位代码]}（落盘缓存 7 天，构建慢）
_CONS_CACHE: Dict[str, List[str]] = {}
_CONS_CACHE_LOADED = False
_CONS_CACHE_MTIME: float = 0.0
_CONS_CACHE_LOCK = threading.Lock()
_TIER_WARMING: set[str] = set()


def _cons_cache_file():
    """板块成分映射落盘文件路径。

    Returns:
        Path: ``<DATA_DIR>/board_cons.json``（2026-11-06 从 CACHE_DIR 迁出：
        清缓存不应连带删掉需要数十分钟预热才能重建的成分映射）。
    """
    return config.DATA_DIR / "board_cons.json"


def _load_cons_cache() -> None:
    """从磁盘载入板块成分映射（进程内缓存；文件有更新时重载）。

    守卫逻辑：首次加载后记录文件 mtime；后续调用若文件 mtime 未变则跳过，
    若文件已更新（其他请求写入了新成分）则重新加载并合并到内存缓存
    （不覆盖内存中已有的、可能更新的板块）。
    """
    global _CONS_CACHE_LOADED, _CONS_CACHE_MTIME
    path = _cons_cache_file()
    try:
        mtime = path.stat().st_mtime if path.is_file() else 0.0
    except OSError:
        mtime = 0.0
    with _CONS_CACHE_LOCK:
        if _CONS_CACHE_LOADED and mtime <= _CONS_CACHE_MTIME:
            return
        _CONS_CACHE_LOADED = True
        _CONS_CACHE_MTIME = mtime
        if mtime == 0.0:
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                for key, value in payload.items():
                    if isinstance(value, list) and key not in _CONS_CACHE:
                        _CONS_CACHE[key] = [str(code) for code in value]
                logger.info("板块成分映射已载入：%d 个板块", len(_CONS_CACHE))
        except Exception as exc:  # noqa: BLE001 - 缓存损坏不影响主流程
            logger.warning("板块成分映射载入失败：%s", type(exc).__name__)


def _save_cons_cache() -> None:
    """把板块成分映射原子写盘（失败仅告警，不影响接口）。"""
    try:
        from backend.app.core import atomic

        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        atomic.atomic_write_json(_cons_cache_file(), _CONS_CACHE, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001 - 写盘失败不影响内存缓存
        logger.warning("板块成分映射写盘失败：%s", type(exc).__name__)


def get_board_constituents(board: str, *, kind: str = "industry", max_pages: int = 40) -> List[str]:
    """取板块成分股代码列表（内存 + 磁盘缓存，避免重复翻页触发同花顺限流）。

    Args:
        board: 板块名。
        kind: industry / concept。
        max_pages: 最多翻页数（每页约 20 只）；预热场景可调小以缩短单板块耗时。

    Returns:
        List[str]: 6 位代码列表。

    Raises:
        ValueError: 板块名无代码或成分页不可用。
    """
    _load_cons_cache()
    cached = _CONS_CACHE.get(board)
    if cached:
        return cached
    code = ths_http.get_board_code(board, kind=kind)
    if not code:
        raise ValueError(f"板块名录无此板块：{board}")
    rows = ths_http.get_board_cons(code, kind=kind, max_pages=max_pages)
    codes = [row["code"] for row in rows if row.get("code")]
    if not codes:
        raise ValueError(f"板块成分为空：{board}")
    _CONS_CACHE[board] = codes
    _save_cons_cache()
    return codes


def _ensure_cons_progress(
    rows: List[Dict[str, Any]],
    *,
    budget_seconds: float = 6.0,
    max_boards: int = 2,
) -> None:
    """在时间预算内为尚未建映射的板块补齐成分（供四档聚合）。

    设计取舍：板块成分映射首次构建需逐页翻同花顺详情页（1.1s 限流），
    90 个行业串行需数分钟，不能放进单次请求。故这里只做**增量补齐**：
    每次刷新最多补 ``max_boards`` 个、且不超过 ``budget_seconds``；其余交给
    服务启动时的后台预热线程。映射落盘后 7 天内不再重复翻页。

    Args:
        rows: 行业资金流行（含 name）。
        budget_seconds: 本次补建的时间预算（秒）。
        max_boards: 本次最多补建的板块数。
    """
    _load_cons_cache()
    started = time.time()
    built = 0
    for row in rows:
        if built >= max_boards or time.time() - started > budget_seconds:
            break
        name = row.get("name")
        if not name or name in _CONS_CACHE:
            continue
        try:
            get_board_constituents(name)
            built += 1
        except Exception as exc:  # noqa: BLE001 - 单板块失败不影响整表
            logger.info("板块成分补建跳过 %s：%s", name, type(exc).__name__)


def get_margin_by_board(*, kind: str = "industry") -> Dict[str, Any]:
    """行业融资余额强度（设计基准 R5 / 验收 C5）。

    做法：上交所 + 深交所「两融明细」按板块成分映射聚合融资余额，
    再除以该板块当日成交额得「杠杆强度」，最后做**横截面分位**（板块之间比）。

    为什么用横截面分位而不是时序分位：
        两融明细只有当日快照（历史需逐日回溯 250 次请求，源站不可承受），
        无法构成时间序列 ⇒ 与 M3 其他维度（时序分位）不同质，
        故在拥挤度里以 weight=0 的独立分项呈现，并在 note 里写明口径，
        不混进 0–100 加权分（避免不同质数值相加）。

    数据源：``stock_margin_detail_sse``（约 2000 行 / 3.6s）+ ``stock_margin_detail_szse``
    （约 2119 行 / 0.9s），均含「融资余额」（元）。6 小时缓存。

    Args:
        kind: 目前仅 industry（概念板块无成映射时不参与）。

    Returns:
        Dict[str, Any]: `{items, sample, as_of, source, caliber, missing}`；
        items 为 `[{board, balance_yi, amount_yi, intensity, pct}]` 按强度降序。
    """
    import akshare as ak

    from backend.app.core import cache as _cache

    def _load() -> Dict[str, Any]:
        """拉取并聚合一版快照（供缓存 loader 调用）。

        Returns:
            Dict[str, Any]: 聚合结果。
        """
        balances: Dict[str, float] = {}
        errors: List[str] = []
        try:
            sse = ak.stock_margin_detail_sse()
            code_col = "标的证券代码" if "标的证券代码" in sse.columns else sse.columns[1]
            for _, row in sse.iterrows():
                code = str(row.get(code_col) or "").strip()
                if len(code) != 6 or not code.isdigit():
                    continue
                value = row.get("融资余额")
                if value is None:
                    continue
                try:
                    balances[code] = balances.get(code, 0.0) + float(value)
                except (TypeError, ValueError):
                    continue
        except Exception as exc:  # noqa: BLE001 - 单市场失败降级为部分覆盖
            errors.append(f"上交所两融明细不可得（{type(exc).__name__}）")
        try:
            szse = ak.stock_margin_detail_szse()
            code_col = "证券代码" if "证券代码" in szse.columns else szse.columns[0]
            for _, row in szse.iterrows():
                code = str(row.get(code_col) or "").strip()
                if len(code) != 6 or not code.isdigit():
                    continue
                value = row.get("融资余额")
                if value is None:
                    continue
                try:
                    balances[code] = balances.get(code, 0.0) + float(value)
                except (TypeError, ValueError):
                    continue
        except Exception as exc:  # noqa: BLE001 - 单市场失败降级为部分覆盖
            errors.append(f"深交所两融明细不可得（{type(exc).__name__}）")

        if not balances:
            return {"items": [], "sample": 0, "missing": errors or ["两融明细不可得"]}

        # 板块成交额作分母（同花顺行业资金流表自带 amount，单位亿元）
        amounts: Dict[str, float] = {}
        try:
            for row in ths_http.get_industry_rows():
                name = row.get("name")
                if name:
                    amounts[name] = float(_num(row.get("amount")) or 0.0)
        except Exception:  # noqa: BLE001 - 分母不可得则只给余额绝对值
            amounts = {}

        _load_cons_cache()
        raw: List[Dict[str, Any]] = []
        for board_name, codes in list(_CONS_CACHE.items()):
            total = sum(balances.get(code, 0.0) for code in codes)
            if total <= 0:
                continue
            balance_yi = total / 1e8
            amount_yi = amounts.get(board_name) or 0.0
            raw.append({
                "board": board_name,
                "balance_yi": round(balance_yi, 2),
                "amount_yi": round(amount_yi, 2),
                "intensity": round(balance_yi / amount_yi, 4) if amount_yi > 0 else None,
            })

        # 横截面分位：强度越高 → 分位越高（无强度时退回余额绝对值分位）
        key = "intensity" if any(x["intensity"] is not None for x in raw) else "balance_yi"
        values = [x[key] for x in raw if x[key] is not None]
        if values:
            for item in raw:
                v = item.get(key)
                if v is None:
                    continue
                below = sum(1 for x in values if x <= v)
                item["pct"] = round(below / len(values) * 100, 1)
        raw.sort(key=lambda x: (x.get("pct") or 0), reverse=True)

        return {
            "items": raw,
            "sample": len(balances),
            "missing": errors,
        }

    cached = _cache.get_or_load("sector:margin_by_board", 6 * 3600, _load)
    data = cached if isinstance(cached, dict) else {}
    items = data.get("items") or []
    return {
        "items": items,
        "sample": data.get("sample") or 0,
        "as_of": utils.now_text(utils.bj_now()),
        "source": "上交所/深交所两融明细（按板块成分聚合）",
        "caliber": (
            "融资余额=板块成分股融资余额合计（元→亿元）；强度=融资余额/板块成交额；"
            "分位为**横截面**（板块之间比较），不是时间序列分位；"
            "分母取同花顺行业成交额，缺失时强度为空并退回余额绝对值排序"
        ),
        "missing": data.get("missing") or ([] if items else ["板块成分映射未预热，无法按板块聚合"]),
    }


def _flow_tier(board: str, code: Optional[str]) -> Optional[Dict[str, Any]]:
    """取某板块的四档资金流（10 分钟缓存；未预热时返回 None 并触发后台预热）。

    Args:
        board: 板块名。
        code: 同花顺板块代码（未用到，保留以兼容调用方）。

    Returns:
        Optional[Dict[str, Any]]: 四档聚合结果；未就绪返回 None。
    """
    cached = _FLOW_TIER_CACHE.get(board, config.TTL["flow_tier"])
    if cached is not None:
        return cached
    _load_cons_cache()
    codes = _CONS_CACHE.get(board)
    if not codes:
        return None
    try:
        payload = sina_flow.aggregate_flow(codes)
    except Exception as exc:  # noqa: BLE001 - 单板块四档失败不阻断整表
        logger.info("板块四档聚合失败 %s：%s", board, type(exc).__name__)
        return None
    if payload.get("covered"):
        _FLOW_TIER_CACHE.set(board, payload)
        return payload
    return None


def _prefetch_flow_tiers(rows: List[Dict[str, Any]], *, max_workers: int = FLOW_TIER_WORKERS) -> int:
    """并发预取整表板块四档（把 90 次串行 HTTP 压成并发）。

    v3.7.1（REQ-20261115-02）：`get_fundflow` 原先在结果循环里逐板块调 :func:`_flow_tier`，
    即「90 个板块 × 1 次新浪批量请求」**串行**执行 —— 实测单项 13.9s（占整条链路 15.1s 的 92%），
    是冷启动 24.3s 的主因。此处先并发预热，循环里只剩缓存命中。

    口径不变：单板块失败仍返回 None（四档为 None、不补 0、不估算），
    成功项写 `_FLOW_TIER_CACHE`；`covered` 判据与 :func:`_flow_tier` 完全一致。

    Args:
        rows: 资金流行（含 name）。
        max_workers: 并发线程数（新浪批量接口无严格限流，8 路实测稳定）。

    Returns:
        int: 本次实际发请求的板块数（已命中缓存的跳过，不重复打源站）。
    """
    from concurrent.futures import ThreadPoolExecutor

    _load_cons_cache()
    pending: List[str] = []
    for row in rows:
        name = row.get("name")
        if not name:
            continue
        if _FLOW_TIER_CACHE.get(name, config.TTL["flow_tier"]) is not None:
            continue  # 缓存命中：不重复请求
        if not _CONS_CACHE.get(name):
            continue  # 无成分映射：交给预热线程，本次按 None 处理
        pending.append(name)
    if not pending:
        return 0
    if len(pending) == 1 or max_workers <= 1:
        for name in pending:
            _flow_tier(name, None)
        return len(pending)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        # 逐个消费以捕获异常：_flow_tier 内部已 try/except，此处仅防御线程池异常
        list(pool.map(lambda board: _flow_tier(board, None), pending))
    return len(pending)


def warm_fundflow_tiers(*, max_boards: int = 90) -> int:
    """后台预热板块四档：先补成分映射，再并发刷新四档（守护线程调用）。

    v3.7.1（REQ-20261115-02）把原「一个 for 循环里既建映射又算四档」拆成两段：

    - **阶段 A｜成分映射**：仍**顺序**执行（同花顺详情页有 1.1s 进程级限流，
      并发只会互相排队）。映射落盘后 7 天内不再重复翻页。
    - **阶段 B｜四档数值**：改走 :func:`_prefetch_flow_tiers` 并发刷新。

    分开的原因：原实现阶段 B 也是串行的 —— 90 个板块 × 1 次新浪批量请求 ≈ 13.9s，
    而同一时刻 ``/api/sector/fundflow`` 的预取也在跑同样 90 个板块，
    **同一份数据被算两遍且互相拖慢**（实测服务启动后首个 fundflow 请求 16.8s，
    单跑只要 3.1s）。改并发后预热一轮约 2s，且与请求侧共享同一份 ``_FLOW_TIER_CACHE``。

    Args:
        max_boards: 本次最多预热多少个板块（默认全量 90）。

    Returns:
        int: 预热后缓存中可用的板块数（四档有值才算）。
    """
    _load_cons_cache()
    try:
        names = [row["name"] for row in ths_http.get_industry_rows() if row.get("name")]
    except Exception as exc:  # noqa: BLE001 - 名录不可用则本次不预热
        logger.info("板块四档预热跳过（名录不可用：%s）", type(exc).__name__)
        return 0
    targets = names[:max_boards]

    # 阶段 A：补齐缺失的成分映射（顺序执行，遵守同花顺 1.1s 限流）
    for name in targets:
        if name in _CONS_CACHE or name in _TIER_WARMING:
            continue
        _TIER_WARMING.add(name)
        try:
            # 预热优先速度：12 页约 240 只，已覆盖绝大多数行业板块（半导体 182 只全覆盖）；
            # 全量 40 页实测单板块 24s，89 个板块要 35 分钟，启动后台不划算。
            get_board_constituents(name, max_pages=12)
        except Exception as exc:  # noqa: BLE001 - 单板块失败继续下一个
            logger.info("板块成分映射预热跳过 %s：%s", name, type(exc).__name__)
        finally:
            _TIER_WARMING.discard(name)

    # 阶段 B：并发刷新四档（与 /api/sector/fundflow 的预取共用同一实现与同一缓存）
    try:
        _prefetch_flow_tiers([{"name": name} for name in targets])
    except Exception as exc:  # noqa: BLE001 - 预热失败不影响主流程
        logger.info("板块四档并发预热失败：%s", type(exc).__name__)
    done = sum(
        1 for name in targets
        if _FLOW_TIER_CACHE.get(name, config.TTL["flow_tier"]) is not None
    )
    logger.info("板块四档预热完成：%d/%d 个板块", done, len(targets))
    return done


def get_trend(boards: List[str], days: int = 30, market: str = "cn") -> Dict[str, Any]:
    """多板块归一化走势对比（复用板块 K 线，首个交易日=100）。

    Args:
        boards: 板块名列表（1–6 个）。
        days: 取最近 N 个交易日。
        market: cn/hk/us。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    series: List[Dict[str, Any]] = []
    failures: List[str] = []
    for board in boards:
        try:
            payload = get_sector_kline(board, "daily", market)
        except Exception as exc:  # noqa: BLE001 - 单板块失败不中断整体
            failures.append(f"{board}:{type(exc).__name__}")
            continue
        items = payload["items"][-max(1, days):]
        base = None
        points: List[Dict[str, Any]] = []
        for item in items:
            close = _num(item.get("close"))
            if close is None:
                continue
            if base is None and close:
                base = close
            points.append({"date": item.get("date"), "value": _round(close / base * 100, 2) if base else None})
        series.append({"board": board, "points": points})
    return {"series": series, "base": 100, "failures": failures, "days": days}


# =============================================================
# 第七轮 M3：板块拥挤度（0–100 分，阈值 60 提示 / 80 复核）
# =============================================================

# 拥挤度权重：只保留数据可得的维度，缺失维度在 caliber 里逐条写明（不编造）
CROWDING_WEIGHTS = {
    "amount_share": 0.35,   # 成交额强度分位（当日/250 日均值）
    "bias": 0.20,           # 乖离率分位（收盘/MA20−1）
    "volume_share": 0.15,   # 量能偏离分位（当日/20 日均量−1）
    "momentum": 0.15,       # 近 20 日动量分位
    "amplitude": 0.08,      # 振幅分位（R4 新增：波动放大=分歧加剧）
    "high_distance": 0.07,  # 距 60 日高点分位（R4 新增：贴近高点=追高密集）
}

# 拥挤度排行扫描的并发线程数（v3.7.1）。原为 6，实测（同一进程多轮取均值）：
# 6 路 7.40s / 8 路 6.90s / 10 路 7.33s / **12 路 6.06s** / 14 路 6.14s / 16 路 6.24s。
# 单板块成本以「两次 HTTP + 解析」为主，提高并发能把 90 板块的等待重叠掉，12 路见底。
# 同花顺行情数据域实测 8 并发 / 78 QPS 零拦截（tools/_probe_ths_rate.py 三轮共 440 次），
# 12 路仍在该域充裕范围内。
CROWDING_SCAN_WORKERS = 12

CROWDING_MISSING = (
    "设计原定五维（成交额/换手率/两融/相关性/动量），第十轮 P3 R4 扩为六维实测："
    "① 成交额维度用「当日/近250日均值」相对强度，不是「占全市场比重」——"
    "全市场成交额源（东财 push2his）在本机被源站重置，新浪指数日线只有成交量无成交额；"
    "② 换手率分位已纳入（第十轮验收 C4）：以「板块日成交额 / 成分市值合计」构造 250 日换手率序列取分位，作为 weight=0 独立分项呈现（口径见该项 note）；"
    "③ 行业融资余额分位未纳入：两融仅有全市场口径，无行业口径；"
    "④ 成分股相关性/换手率只作参考值不参与加权：相关性需逐只成分股日线（约 20 只 × 60 日），"
    "换手率只有当日快照，两者都无历史序列可算分位，混进 0–100 分会破坏同质性，故列在 extra 里供参考；"
    "⑤ 新增两维替代上述缺口：振幅分位（波动放大→分歧加剧）与距 60 日高点分位（贴近高点→追高密集），"
    "两者都只用板块日线自身，可复算且能与历史分位同质比较"
)


def _pct_rank(series: List[float], current: float) -> Optional[float]:
    """当前值在序列中的分位（≤ 当前值占比 × 100）。

    Args:
        series: 历史样本。
        current: 当前值。

    Returns:
        Optional[float]: 0–100 分位；样本不足 30 个返回 None（不做不可信的分位）。
    """
    clean = [value for value in series if value is not None]
    if len(clean) < 30 or current is None:
        return None
    below = sum(1 for value in clean if value <= current)
    return round(below / len(clean) * 100, 1)


def get_crowding(board: str, *, kind: str = "industry", fill_today: bool = True,
                 light: bool = False) -> Dict[str, Any]:
    """单板块拥挤度打分（M3）。

    四个维度的分位加权成 0–100 分，>60 提示、>80 提示复核（用户口径）。
    只陈述「交易结构位置」，不给买卖指令。

    Args:
        board: 板块名（如「半导体」）。
        kind: 板块类型：industry=行业 / concept=概念（均走纯 HTTP 板块日线）。
        fill_today: 是否补当日成交额（批量排行扫描时置 False）。
        light: 轻量模式（第十轮 D6 整改）：跳过成分股相关性/换手率与融资余额横截面
            —— 三者均为 weight=0 的参考分项，不影响 score；排行扫描用它把单板块耗时
            从 ~1.4s 压到 ~0.3s，避免 90 板块冷启动超 20s。

    Returns:
        dict: {board, kind, score, level, parts, sample_days, as_of, caliber, missing}

    Raises:
        ValueError: 板块日线取不到或样本不足。
    """
    ensure_enabled()
    rows: List[Dict[str, Any]] = []
    for row in _kline_ths(kind, board, "daily", fill_today=fill_today):
        day = str(row.get("date") or "")[:10]
        close = _num(row.get("close"))
        amount = _num(row.get("amount"))
        volume = _num(row.get("volume"))
        high = _num(row.get("high"))
        low = _num(row.get("low"))
        if day and close and amount and volume:
            rows.append({"date": day, "close": close, "amount": amount,
                         "volume": volume, "high": high, "low": low})
    rows.sort(key=lambda item: item["date"])
    if len(rows) < 60:
        raise ValueError(f"{board} 日线样本不足（{len(rows)} 个交易日），无法计算分位")

    # 成交额强度 = 当日成交额 / 近 250 日均值（消除市场规模自然增长带来的趋势抬升）。
    # 原设计用「占全市场比重」，但全市场成交额源（东财 push2his）在本机被源站重置，
    # 新浪指数日线只有成交量没有成交额，无法合成可靠分母；改用「相对自身历史强度」，
    # 只依赖板块日线自身，口径自洽且可复算。差异已在 caliber 写明。
    # 注：虽然 all.js 返回全历史（实测 1932 根），但其中只有近 140 根带成交额
    # （last.js 覆盖范围），故 amounts 实际长度约 140，本循环 O(n×250) 仅 ~3.5 万次加法
    # （实测 0.2ms），无需改滚动和（v3.7.1 曾试改，实测无收益且有浮点尾差，已回退）。
    amounts = [item["amount"] for item in rows]
    strengths: List[float] = []
    for index in range(0, len(amounts)):
        window = amounts[max(0, index - 249): index + 1]
        avg = sum(window) / len(window)
        strengths.append(amounts[index] / avg if avg else 1.0)
    if len(strengths) < 30:
        raise ValueError(f"{board} 成交额强度样本不足（{len(strengths)} 日）")

    # 乖离率：收盘 / MA20 − 1
    closes = [item["close"] for item in rows]
    biases: List[float] = []
    for index in range(19, len(closes)):
        window = closes[index - 19: index + 1]
        ma = sum(window) / len(window)
        biases.append(closes[index] / ma - 1)
    # 动量：近 20 日涨幅
    momenta: List[float] = []
    for index in range(20, len(closes)):
        momenta.append(closes[index] / closes[index - 20] - 1)

    # R4 新增：振幅（当日 high−low / 前收）× 100，缺 high/low 的交易日跳过
    amplitudes: List[float] = []
    for index in range(1, len(rows)):
        high = rows[index].get("high")
        low = rows[index].get("low")
        prev = closes[index - 1]
        if high is None or low is None or not prev:
            continue
        amplitudes.append((high - low) / prev * 100)

    # R4 新增：距 60 日高点（收盘 / 近 60 日最高收盘 − 1）× 100，越接近 0 越贴近高点
    high_distances: List[float] = []
    for index in range(59, len(closes)):
        window_high = max(closes[index - 59: index + 1])
        high_distances.append((closes[index] / window_high - 1) * 100 if window_high else 0.0)

    # 成交量占比（用成交量相对全市场：市场成交量取不到，退化用板块自身 20 日均量比值）
    vols = [item["volume"] for item in rows]
    vol_ratios: List[float] = []
    for index in range(19, len(vols)):
        window = vols[index - 19: index + 1]
        avg = sum(window) / len(window)
        vol_ratios.append(vols[index] / avg - 1 if avg else 0.0)

    parts = {
        "amount_share": {
            "label": "成交额强度(250日均值)",
            "value": round(strengths[-1] * 100, 1),
            "pct": _pct_rank(strengths[:-1], strengths[-1]),
            "weight": CROWDING_WEIGHTS["amount_share"],
            "unit": "%",
        },
        "bias": {
            "label": "乖离率(MA20)",
            "value": round(biases[-1] * 100, 2),
            "pct": _pct_rank(biases[:-1], biases[-1]),
            "weight": CROWDING_WEIGHTS["bias"],
            "unit": "%",
        },
        "volume_share": {
            "label": "量能偏离(20日均量)",
            "value": round(vol_ratios[-1] * 100, 2),
            "pct": _pct_rank(vol_ratios[:-1], vol_ratios[-1]),
            "weight": CROWDING_WEIGHTS["volume_share"],
            "unit": "%",
        },
        "momentum": {
            "label": "20 日动量",
            "value": round(momenta[-1] * 100, 2),
            "pct": _pct_rank(momenta[:-1], momenta[-1]),
            "weight": CROWDING_WEIGHTS["momentum"],
            "unit": "%",
        },
        "amplitude": {
            "label": "振幅(相对前收)",
            "value": round(amplitudes[-1], 2) if amplitudes else 0.0,
            "pct": _pct_rank(amplitudes[:-1], amplitudes[-1]) if len(amplitudes) > 5 else 50.0,
            "weight": CROWDING_WEIGHTS["amplitude"],
            "unit": "%",
        },
        "high_distance": {
            "label": "距60日高点",
            "value": round(high_distances[-1], 2) if high_distances else 0.0,
            # 距高点越近（值越接近 0）越拥挤：分位取「−距离」的历史分位
            "pct": _pct_rank([-x for x in high_distances[:-1]], -high_distances[-1]) if len(high_distances) > 5 else 50.0,
            "weight": CROWDING_WEIGHTS["high_distance"],
            "unit": "%",
        },
    }

    # 加权求和（分位缺失的维度按 50 计中位，并在 missing 里点名，避免静默降权）
    missing_parts: List[str] = []
    score = 0.0
    used_weight = 0.0
    for key, part in parts.items():
        pct = part["pct"]
        if pct is None:
            missing_parts.append(part["label"])
            continue
        score += part["weight"] * pct
        used_weight += part["weight"]
    if used_weight <= 0:
        raise ValueError(f"{board} 四个维度分位均不可用")
    score = round(score / used_weight, 1)

    if score >= 80:
        level, advice = "高位", "交易结构拥挤，建议复核"
    elif score >= 60:
        level, advice = "偏高", "拥挤度进入提示区间"
    else:
        level, advice = "正常", "拥挤度处于常规区间"

    kind_label = "行业" if kind == "industry" else "概念"

    # ── 成分股维度（换手率/相关性）：仅使用已缓存的成分映射 ──
    # 第八轮：成分映射由后台预热线程落盘（首次构建需逐页翻同花顺，1.1s 限流下数十秒），
    # 单板块接口不能为此阻塞，故此处只读缓存；未预热到则在 caliber 里如实说明。
    extra: Dict[str, Any] = {}
    cons_missing: List[str] = []
    _load_cons_cache()
    cached_codes = [] if light else (_CONS_CACHE.get(board) or [])
    if cached_codes:
        constituents = [{"code": code, "board": board} for code in cached_codes]
        extra["constituent_count"] = len(constituents)
        corr = _constituent_correlation(constituents)
        if corr:
            extra["correlation"] = corr
        else:
            cons_missing.append("成分股相关性不可得（样本 K 线不足）")
        turnover = _constituent_turnover(constituents)
        if turnover:
            extra["turnover"] = turnover
        else:
            cons_missing.append("成分股换手率不可得（样本报价不足）")
        turnover_pct = _board_turnover_percentile(board, rows)
        if turnover_pct:
            extra["turnover_pct"] = turnover_pct
        else:
            cons_missing.append("换手率分位不可得（流通市值或成交额样本不足）")
    else:
        cons_missing.append("成分映射尚未预热（服务启动后由后台线程构建，约数分钟）")
    # R5（C5）：行业融资余额强度作为**独立分项**列出（weight=0，不参与加权）。
    # 依据：两融明细只有当日快照（历史需逐日回溯 250 次请求，源站不可承受），
    # 无法构成时间序列 ⇒ 只能做横截面分位（板块之间比较），与其他维度（时序分位）不同质，
    # 故不混进 0–100 加权分，单独呈现并在 note 里写明口径。
    margin_pct: Optional[float] = None
    margin_note: str = ""
    if light:
        cons_missing.append("轻量模式：成分股相关/换手与融资余额分位未计算（排行场景）")
    elif kind == "industry":
        try:
            margin_data = get_margin_by_board(kind="industry")
            for item in margin_data.get("items") or []:
                if item.get("board") == board:
                    margin_pct = item.get("pct")
                    intensity = item.get("intensity")
                    balance = item.get("balance_yi")
                    margin_note = (
                        f"横截面分位（板块之间比较，非时间序列）；"
                        f"融资余额 {balance} 亿元 / 强度 {intensity}" if intensity is not None
                        else f"横截面分位（按余额绝对值排序）；融资余额 {balance} 亿元"
                    )
                    break
            if margin_pct is None and (margin_data.get("items") or []):
                cons_missing.append(f"融资余额分位不可得（{board} 不在两融覆盖范围内）")
        except Exception as exc:  # noqa: BLE001 - 单维度失败不阻塞整体
            cons_missing.append(f"融资余额分位不可得（{type(exc).__name__}）")
    else:
        cons_missing.append("概念板块无融资余额分位（仅行业口径支持）")

    # R6（C6）：成分股相关性作为**独立分项**列出（weight=0，不参与加权）。
    # 依据：相关系数只有当期值、无历史序列 ⇒ 无法算分位，硬塞进 0–100 分会破坏同质性；
    # 但它对「同涨同跌是否极端」有直接指示意义，故以绝对水平映射（0→0 分，1→100 分）单列，
    # 并在 note 里写明口径，供方向性核对（半导体分散 / 银行同质）。
    turnover_block = extra.get("turnover_pct") or {}
    if turnover_block.get("percentile") is not None:
        parts["turnover_pct"] = {
            "label": "换手率分位",
            "value": turnover_block.get("turnover_pct"),
            "pct": turnover_block.get("percentile"),
            "weight": 0.0,
            "unit": "%",
            "note": turnover_block.get("note"),
        }

    corr_block = extra.get("correlation") or {}
    if corr_block.get("corr") is not None:
        corr_value = float(corr_block["corr"])
        parts["correlation"] = {
            "label": "成分股相关性",
            "value": round(corr_value, 3),
            "pct": round(max(0.0, min(1.0, corr_value)) * 100, 1),
            "weight": 0.0,
            "unit": "",
            "note": (
                f"绝对水平映射（0→0 分 / 1→100 分）；样本 "
                f"{corr_block.get('samples') or '无数据'} 只 × {corr_block.get('window_days') or '无数据'} 日；"
                "无历史分位，权重 0 不参与加权，仅供方向性参考"
            ),
        }
    if margin_pct is not None:
        parts["margin"] = {
            "label": "融资余额强度(横截面)",
            "value": margin_pct,
            "pct": round(margin_pct, 1),
            "weight": 0.0,
            "unit": "分位",
            "note": f"{margin_note}；权重 0 不参与加权",
        }

    return {
        "board": board,
        "kind": kind,
        "source": "同花顺" if kind == "industry" else "腾讯",
        "market_status": "日线收盘",
        "is_realtime": False,
        "score": score,
        "level": level,
        "advice": advice,
        "parts": list(parts.values()),
        "sample_days": len(rows),
        "extra": extra,
        "as_of": utils.now_text(utils.bj_now()),
        "caliber": (
            f"{kind_label}拥挤度=六维分位加权（成交额强度 {int(CROWDING_WEIGHTS['amount_share']*100)}% / "
            f"乖离率 {int(CROWDING_WEIGHTS['bias']*100)}% / 量能偏离 {int(CROWDING_WEIGHTS['volume_share']*100)}% / "
            f"动量 {int(CROWDING_WEIGHTS['momentum']*100)}% / 振幅 {int(CROWDING_WEIGHTS['amplitude']*100)}% / "
            f"距60日高点 {int(CROWDING_WEIGHTS['high_distance']*100)}%）；"
            f"缺失维度不清零而是按可用权重归一；成分股相关性、换手率、融资余额强度见 parts（权重 0，仅参考，不参与加权）；{CROWDING_MISSING}"
        ),
        "missing": (
            [CROWDING_MISSING]
            + ([f"分位不可用：{'、'.join(missing_parts)}"] if missing_parts else [])
            + ([f"成分股维度：{'、'.join(cons_missing)}"] if cons_missing else [])
        ),
    }


def get_crowding_rankings(
    *,
    kind: str = "industry",
    limit: int = 50,
    max_workers: int = CROWDING_SCAN_WORKERS,
) -> Dict[str, Any]:
    """全板块拥挤度排行（第七轮 M12）。

    复用 `get_crowding()` 同一套六维打分（同口径、同函数），逐板块并发计算后按得分降序。
    只陈述交易结构位置，不给买卖指令。

    Args:
        kind: 板块类型，目前仅支持 industry（概念板块接口在本机不稳定，未纳入）。
        limit: 返回前 N 名（1–120）。
        max_workers: 并发线程数（纯 HTTP 通道无签名锁，可并行；默认值见
            :data:`CROWDING_SCAN_WORKERS`）。

    Returns:
        Dict[str, Any]: {items, scanned, total, failed, level_counts, caliber, as_of}

    Raises:
        ValueError: 板块类型不支持或清单为空。
    """
    ensure_enabled()
    # 本函数为**纯同步扫描体**（供测试直连与后台线程复用）；
    # 缓存查表与「后台计算 + 立即返回计算中态」的 HTTP 语义由 routers/sector.py 接管，
    # 故此处不查缓存、不启后台，避免把「计算中」中间态落盘 1 小时。
    return _scan_rankings(kind, limit, max_workers=max_workers)


def _scan_rankings(kind: str, limit: int, *, max_workers: int = CROWDING_SCAN_WORKERS) -> Dict[str, Any]:
    """排行实际扫描体（后台线程调用；不查缓存、不启动后台）。

    Args:
        kind: industry / concept。
        limit: 返回前 N 名。
        max_workers: 并发线程数。

    Returns:
        Dict[str, Any]: 排行结果（同 get_crowding_rankings 成功态）。

    Raises:
        ValueError: kind 非法，或对应板块清单为空。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    key_kind = str(kind or "industry").strip().lower()
    if key_kind not in ("industry", "concept"):
        raise ValueError("kind 仅支持 industry（行业）/ concept（概念）")
    limit = max(1, min(int(limit or 50), 120))

    names = get_crowding_board_names(key_kind)
    if not names:
        raise ValueError(f"同花顺{'行业' if key_kind=='industry' else '概念'}清单为空，无法计算排行")

    items: List[Dict[str, Any]] = []
    failed: List[str] = []

    def _work(board: str) -> Dict[str, Any]:
        """单板块打分（R11：失败重试 1 次后再归类到 failed）。

        概念板块日线偶发空表/超时（源站抖动），单次失败就计入 failed 会把完整率
        压到 90% 以下；重试一次即可覆盖绝大多数瞬时抖动，同时不做无上限重试
        （避免个别坏板块拖住整个排行）。

        Args:
            board: 板块名。

        Returns:
            Dict[str, Any]: 单板块结果，含 board / kind / score / level / parts。

        Raises:
            Exception: 两次都失败时抛出最后一次异常。
        """
        last_error: Optional[Exception] = None
        for attempt in (1, 2):
            try:
                data = get_crowding(board, kind=key_kind, fill_today=False, light=True)
                return {
                    "board": data["board"],
                    "kind": data.get("kind"),
                    "score": data["score"],
                    "level": data["level"],
                    "parts": [
                        {"label": part["label"], "pct": part["pct"], "value": part["value"], "unit": part["unit"]}
                        for part in data.get("parts") or []
                    ],
                }
            except Exception as exc:  # noqa: BLE001 - 记录后重试一次
                last_error = exc
                if attempt == 1:
                    time.sleep(0.3)  # dup-ok: 池内并发确定性时序（避免 retry_call 随机抖动放大并发方差，备案见下）
        raise last_error if last_error else RuntimeError(f"{board} 打分失败")
        # RF-20261112-01 备注：此处手写 2 次重试为「池内并发 + 限频友好」的
        # 轻量退避（0.3s 短等待，避免线程池内 retry_call 的随机抖动叠加），
        # 语义等价于 retry_call(retries=1, backoff=(0.3,))；保留手写以维持
        # 线程池内的确定性时序（随机抖动 uniform(0,0.3) 会放大并发方差）。

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_work, name): name for name in names}
        for future in as_completed(futures):
            board = futures[future]
            try:
                items.append(future.result())
            except Exception as exc:  # noqa: BLE001 - 单板块失败不阻断整体
                failed.append(f"{board}（{type(exc).__name__}）")
                logger.info("拥挤度排行单板块失败 %s：%s", board, type(exc).__name__)

    items.sort(key=lambda item: item["score"], reverse=True)
    level_counts = {"高位": 0, "偏高": 0, "正常": 0}
    for item in items:
        if item["level"] in level_counts:
            level_counts[item["level"]] += 1

    kind_label = "行业" if key_kind == "industry" else "概念"
    return {
        "items": items[:limit],
        "scanned": len(items),
        "total": len(names),
        "failed": failed,
        "kind": key_kind,
        "source": "同花顺" if key_kind == "industry" else "腾讯",
        "market_status": "日线收盘",
        "is_realtime": False,
        "level_counts": level_counts,
        "caliber": (
            f"与单板块拥挤度同一函数（六维分位加权：成交额强度 {int(CROWDING_WEIGHTS['amount_share']*100)}% / "
            f"乖离率 {int(CROWDING_WEIGHTS['bias']*100)}% / 量能 {int(CROWDING_WEIGHTS['volume_share']*100)}% / "
            f"动量 {int(CROWDING_WEIGHTS['momentum']*100)}%）；"
            f"扫描 {len(items)}/{len(names)} 个{kind_label}；"
            "只陈述交易结构位置，不构成买卖建议"
        ),
        "as_of": utils.now_text(utils.bj_now()),
    }


# 排行后台计算状态（P1-1 冷启动优化：首次扫描 44s+ 超 15s 规范，改为后台预热 + 缓存）
_RANKINGS_IN_FLIGHT: Dict[str, bool] = {}
_RANKINGS_LOCK = threading.Lock()


def rankings_in_flight(cache_key: str) -> bool:
    """查询某 key 的排行是否正在后台计算（供路由层判断是否返回「计算中」态）。

    Args:
        cache_key: 缓存键。

    Returns:
        bool: 是否已在飞行中。
    """
    with _RANKINGS_LOCK:
        return bool(_RANKINGS_IN_FLIGHT.get(cache_key))


def start_rankings_bg(key_kind: str, limit: int, cache_key: str) -> None:
    """未命中缓存时启动后台排行计算（不阻塞调用方）。

    同一 key 在计算期间重复请求不重复启线程，避免多用户并发重复扫描。
    计算完成后写缓存（1 小时 TTL），后续请求直接命中。

    Args:
        key_kind: industry / concept。
        limit: 返回前 N 名。
        cache_key: 缓存键。
    """
    with _RANKINGS_LOCK:
        if _RANKINGS_IN_FLIGHT.get(cache_key):
            return
        _RANKINGS_IN_FLIGHT[cache_key] = True

    def _runner() -> None:
        """后台线程体：扫描排行并写缓存（失败仅记日志，不抛出）。"""
        try:
            result = _scan_rankings(key_kind, limit)
            from backend.app.core import cache as _cache2
            _cache2.set(cache_key, result, 3600)
            logger.info("拥挤度排行（%s）后台计算完成：扫描 %d/%d",
                        key_kind, result.get("scanned"), result.get("total"))
        except Exception as exc:  # noqa: BLE001 - 后台失败不影响下次重试
            logger.info("拥挤度排行（%s）后台计算失败：%s", key_kind, type(exc).__name__)
        finally:
            with _RANKINGS_LOCK:
                _RANKINGS_IN_FLIGHT[cache_key] = False

    threading.Thread(target=_runner, daemon=True, name=f"rankings-{key_kind}").start()


def get_crowding_board_names(kind: str = "industry") -> List[str]:
    """板块名清单（供 M12 排行与 M14 行业匹配共用）。

    2026-11-02 第八轮：行业取同花顺行业名录（90 个，纯 HTTP），
    概念取腾讯概念榜（805 个，纯 HTTP），均不再依赖 V8 签名。

    Args:
        kind: industry=行业 / concept=概念。

    Returns:
        List[str]: 板块名列表（行业约 90 个 / 概念约 800 个）；接口异常时向上抛，由调用方降级。
    """
    ensure_enabled()
    key_kind = str(kind or "industry").strip().lower()
    if key_kind == "concept":
        names = [row.get("name") for row in tencent_board.get_board_rows("concept")]
    else:
        names = [row.get("name") for row in ths_http.get_industry_rows()]
    return [str(name).strip() for name in names if str(name or "").strip()]


# 成分股源的确定性失效类型（本机代理会拦截东财 push2，不重试）
_CONS_FAIL = ("ProxyError", "SSLError", "ConnectionError", "RemoteDisconnected", "ValueError")


def _constituent_correlation(
    constituents: List[Dict[str, Any]],
    *,
    days: int = 60,
    max_samples: int = 20,
) -> Optional[Dict[str, Any]]:
    """成分股相关性（拥挤度维度 ④）：取样本股近 N 日日线收益率，算平均两两相关系数。

    数据源：腾讯日 K 线（本机可用）。样本数受同花顺 V8 串行锁限制，最多 20 只。
    相关性越高 → 成分股同涨同跌越强 → 交易结构越拥挤。

    Args:
        constituents: 成分股列表。
        days: 计算相关性的窗口天数。
        max_samples: 最多取多少只（避免 V8 串行过久）。

    Returns:
        Optional[Dict[str, Any]]: {corr, samples, caliber}；样本不足时 None。
    """
    sample = constituents[:max_samples]
    series: Dict[str, List[float]] = {}
    for item in sample:
        code = item.get("code") or ""
        if not code:
            continue
        try:
            klines = ashare.get_kline(code, "daily", "qfq", days + 5)
        except Exception as exc:  # noqa: BLE001 - 单只失败不阻断
            logger.info("成分股 K 线失败 %s：%s", code, type(exc).__name__)
            continue
        closes = [utils.to_float(k.get("close")) for k in klines]
        closes = [c for c in closes if c is not None]
        if len(closes) >= days + 1:
            series[code] = closes[-days - 1:]
    if len(series) < 3:
        return None

    codes = list(series)
    n = len(codes)
    # 两两 Pearson 相关系数
    rets: Dict[str, List[float]] = {}
    for code in codes:
        vals = series[code]
        rets[code] = [vals[i] / vals[i - 1] - 1 for i in range(1, len(vals))]
    min_len = min(len(v) for v in rets.values())
    for code in codes:
        rets[code] = rets[code][-min_len:]

    def _corr(a: List[float], b: List[float]) -> Optional[float]:
        """两条收益率序列的皮尔逊相关系数。

        Args:
            a: 序列 A。
            b: 序列 B。

        Returns:
            Optional[float]: 相关系数；样本不足 5 或方差为 0 返回 None。
        """
        if len(a) != len(b) or len(a) < 5:
            return None
        ma = sum(a) / len(a)
        mb = sum(b) / len(b)
        cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        va = sum((x - ma) ** 2 for x in a)
        vb = sum((y - mb) ** 2 for y in b)
        if va <= 0 or vb <= 0:
            return None
        denom = (va * vb) ** 0.5
        return cov / denom if denom else None

    pairs = 0
    total = 0.0
    for i in range(n):
        for j in range(i + 1, n):
            c = _corr(rets[codes[i]], rets[codes[j]])
            if c is not None:
                pairs += 1
                total += c
    if pairs == 0:
        return None
    avg = total / pairs
    return {
        "corr": round(avg, 4),
        "samples": n,
        "pairs": pairs,
        "window_days": days,
        "caliber": (
            f"成分股相关性=近 {days} 日日线收益率的 {n} 只样本两两 Pearson 相关系数均值"
            f"（{pairs} 对）；相关性越高越拥挤"
        ),
    }


def _board_float_cap(board: str) -> Optional[Dict[str, Any]]:
    """板块成分市值合计（元），供换手率分位用（第十轮验收 C4）。

    口径说明（重要）：优先用同花顺成分「流通市值」；取不到时退回**本地全市场
    快照的总市值**（``TOTAL_MARKET_CAP`` 合计）。两者都是「同一分母作用于历史
    成交额序列」，分位（相对位置）对分母口径不敏感，但**绝对换手率水平会偏小**，
    故在 note 内明确标注所用口径，不做无标注的静默替换。

    Args:
        board: 板块名。

    Returns:
        Optional[Dict[str, Any]]: {value, unit, caliber}；取不到返回 None。
    """
    def build() -> Optional[Dict[str, Any]]:
        """缓存未命中时的三级取数：同花顺 → 新浪 → 本地全市场快照。

        Returns:
            Optional[Dict[str, Any]]: {value, unit, caliber}；三级皆失败返回 None。
        """
        # ① 同花顺成分（含流通市值，口径最准）——失败不影响后续兜底
        try:
            payload = get_cons_ths(board)
            items = payload.get("items") or []
            total = 0.0
            for item in items:
                value = _num(item.get("float_cap")) or _num(item.get("流通市值"))
                if value and value > 0:
                    total += value
            if total > 0:
                return {"value": total, "unit": "元", "caliber": "同花顺成分流通市值合计"}
        except Exception:  # noqa: BLE001 - 换源兜底
            pass
        # ② 新浪成分 nmc（板块名可能不在新浪名录，失败正常）
        try:
            items = _cons_sina(board)
            total = sum((_num(item.get("nmc")) or 0.0) for item in items or [])
            if total > 0:
                return {"value": total, "unit": "元", "caliber": "新浪成分流通市值(nmc)合计"}
        except Exception:  # noqa: BLE001 - 换源兜底
            pass
        # ③ 本地全市场快照总市值（零网络、稳定；口径近似）
        try:
            from backend.app.datasource import screener as _screener

            snapshot = _screener._read_snapshot()
            rows = snapshot.get("rows") or snapshot.get("items") or []
            codes = set(_CONS_CACHE.get(board) or [])
            if rows and codes:
                total = sum(
                    _num(row.get("mktcap")) or 0.0
                    for row in rows if str(row.get("code")) in codes
                )
                if total > 0:
                    return {
                        "value": total,
                        "unit": "元",
                        "caliber": "本地全市场快照：成分股总市值合计（近似，非流通市值）",
                    }
        except Exception:  # noqa: BLE001 - 兜底亦失败则无分位
            pass
        return None

    return _lookup(f"sector:floatcap:{board}", 6 * 3600, build)


def _board_turnover_percentile(
    board: str,
    rows: List[Dict[str, Any]],
    *,
    window: int = 250,
) -> Optional[Dict[str, Any]]:
    """板块换手率及其历史分位（R4 / 验收 C4）。

    口径（设计基准 §8-R4）：板块换手率 = 板块成交额 / 板块自由流通市值，
    取近 ``window`` 个交易日序列的当前值分位。分钟/日内换手率拿不到历史，
    故用「日成交额 ÷ 当期流通市值」构造时序——分子有历史、分母取当期
    近似（市值变动对换手率量级影响远小于成交额波动），口径在 note 内如实标注。

    Args:
        board: 板块名。
        rows: 板块日线（含 amount 亿元），按日期升序。
        window: 分位窗口（交易日）。

    Returns:
        Optional[Dict[str, Any]]: {turnover_pct, percentile, samples, window_days, note}。
    """
    cap_block = _board_float_cap(board)
    if not cap_block or not cap_block.get("value"):
        return None
    cap = float(cap_block["value"])
    series: List[float] = []
    for row in rows[-window:]:
        amount = _num(row.get("amount"))  # 亿元
        if amount is None or amount <= 0:
            continue
        series.append(amount * 1e8 / cap * 100.0)  # 换手率（%）
    if len(series) < 60:  # 少于 60 个交易日不足以谈分位
        return None
    current = series[-1]
    pct = _pct_rank(series, current)
    return {
        "turnover_pct": round(current, 2),
        "percentile": pct,
        "samples": len(series),
        "window_days": min(window, len(series)),
        "note": (
            f"换手率=日成交额/成分市值（{cap_block.get('caliber')}，合计 "
            f"{round(cap / 1e8, 1)} 亿元）；分位取近 {min(window, len(series))} 个交易日序列；"
            "分母固定为当期值，属近似序列，分位（相对位置）不受口径整体缩放影响"
        ),
    }


def _constituent_turnover(
    constituents: List[Dict[str, Any]],
    *,
    max_samples: int = 20,
) -> Optional[Dict[str, Any]]:
    """成分股换手率（拥挤度维度 ②）：取样本股当日换手率算均值。

    数据源：腾讯报价（含 turnover 字段）。注意：换手率只有当日快照值，
    **无法构成「分位」**（无历史换手率序列），故只作为水平值标注，
    不计入加权分——避免把不可比分位的数值混进 0–100 分。

    Args:
        constituents: 成分股列表。
        max_samples: 最多取多少只。

    Returns:
        Optional[Dict[str, Any]]: {turnover, samples, caliber}；样本不足时 None。
    """
    from backend.app.datasource import tencent

    sample = constituents[:max_samples]
    values: List[float] = []
    for item in sample:
        code = item.get("code") or ""
        if not code:
            continue
        try:
            quote = tencent.get_quote(code, market="a")
            tv = utils.to_float(quote.get("turnover"))
            if tv is not None:
                values.append(tv)
        except Exception as exc:  # noqa: BLE001
            logger.info("成分股报价失败 %s：%s", code, type(exc).__name__)
    if len(values) < 3:
        return None
    avg = sum(values) / len(values)
    return {
        "turnover": round(avg, 2),
        "samples": len(values),
        "caliber": (
            f"成分股换手率= {len(values)} 只样本当日换手率均值 {avg:.2f}%"
            "（腾讯报价快照，非分位，仅作水平参考，不计入拥挤度加权分）"
        ),
    }
