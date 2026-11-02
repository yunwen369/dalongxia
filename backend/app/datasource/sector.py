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
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple


from backend.app import config
from backend.app.core import netfix
from backend.app.core.errors import NonRetryableError
from backend.app.core.resilient import polite_sleep, retry_call
from backend.app.core.v8_guard import guard_v8
from backend.app.datasource import sina_flow, tencent, tencent_board, ths_http, utils

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


def _ensure_enabled(source: str = "akshare") -> None:
    """检查数据源开关，被环境变量禁用时抛出确定性异常（不重试）。

    Args:
        source: 数据源标识（用于降级链溯源与徽章展示）。

    Raises:
        NonRetryableError: 确定性失效（不重试）时抛出，交由降级链切换下一源。
    """
    if not config.PROVIDER_SWITCHES.get(source, True):
        raise NonRetryableError(f"{source} 源已被环境变量禁用")


def _call(func) -> Any:
    """统一包装 akshare 调用。

    本机代理环境会让东财 push2 系抛 ProxyError，这类失败属确定性失效，
    重试只会放大无效请求，因此直接降级到备源。

    Args:
        func: 待执行的无参取数函数。

    Returns:
        Any: 结果原样透传（类型由 loader/func 约定）。

    Raises:
        NonRetryableError: 确定性失效（不重试）时抛出，交由降级链切换下一源。
    """
    try:
        return func()
    except Exception as exc:  # noqa: BLE001 - 统一转译为可降级异常
        name = type(exc).__name__
        if name in ("ProxyError", "SSLError", "ConnectionError", "RemoteDisconnected"):
            raise NonRetryableError(f"上游通道不可用：{name}") from exc
        raise


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


def _num(value: Any) -> Optional[float]:
    """安全转 float，None/空串/NaN 一律返回 None（空值不补 0）。

    Args:
        value: 待处理值（类型由调用方约定）。

    Returns:
        float | None: 数值；无法解析时返回 None（不补 0）。
    """
    return utils.to_float(value)


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    """统一保留小数位，None 原样返回。

    Args:
        value: 待处理数值。
        digits: 保留小数位数。

    Returns:
        float | None: 数值；无法解析时返回 None（不补 0）。
    """
    return utils.round_or_none(value, digits)


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
    _ensure_enabled()
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
    _ensure_enabled()
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
    _ensure_enabled()
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
    if market in ("hk", "us"):
        return _overseas_items(market)
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
    """板块 K 线默认区间（同花顺接口必须显式传起止日期）。

    Args:
        days: 统计天数。

    Returns:
        Tuple[str, str]: 计算结果。
    """
    end = utils.today_bj()  # TZ-06：板块区间按北京时间取当日
    start = end - timedelta(days=days)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


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
    _ensure_enabled()
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
        code = _tencent_board_code(board, "concept")
        if not code:
            raise ValueError(f"腾讯概念榜无此板块：{board}")
        rows = tencent_board.get_board_kline(code, period=period_key, limit=320)
        model_source = "腾讯"
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
_TX_BOARD_CACHE: Dict[str, Any] = {}


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
    except Exception:  # noqa: BLE001 - 名录不可用时不阻断判断
        pass
    try:
        if _tencent_board_code(name, "concept"):
            return True
    except Exception:  # noqa: BLE001
        pass
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
    _ensure_enabled()
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
            except Exception:  # noqa: BLE001 - 单个分类失败不影响整体映射
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
    _ensure_enabled()
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
    _ensure_enabled()

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
    # 单靠 retry_call 无法覆盖，故对「空表」单独退避重试（1.0s / 2.5s），
    # 三次仍空才判定通道异常（R2-03 回归中被真实触发，属数据可用性缺陷）。
    rows: List[Dict[str, Any]] = []
    for attempt, wait in enumerate((0.0, 1.0, 2.5)):
        if wait:
            time.sleep(wait)
        polite_sleep()
        try:
            frame = retry_call(
                lambda: ak.sw_index_third_cons(symbol=target), retries=3, backoff=(1.0, 2.5, 5.0)
            )
            rows = utils.clean_records(frame)
        except (KeyError, IndexError, AttributeError) as exc:
            # 第五轮阶段 5：akshare 解析申万空表/结构变动时会抛 KeyError(slice) 等
            # （实测首次冷启动偶发一次，第二次即恢复）。这类异常属"本次无数据"，
            # 不能穿透到降级链顶层当作整体失败，否则会把夜间单源抖动放大成全链路不可用。
            logger.warning("申万成分解析异常（第 %d/3 次）：%s（%s）",
                           attempt + 1, board, type(exc).__name__)
            rows = []
        if rows:
            break
        logger.warning("申万成分为空（第 %d/3 次尝试）：%s", attempt + 1, board)
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
    _ensure_enabled()
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
    _ensure_enabled()
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
    return {"board": board, "items": _sort_cons(_cons_ths(board)), "source_hint": None}


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
    return {"board": board, "items": _sort_cons(_cons_sina(board)), "source_hint": "新浪"}


def get_cons_sw(board: str) -> Dict[str, Any]:
    """成分股·备源（申万，支持一/二/三级行业名）。

    Args:
        board: 板块名称（与板块树中的名称一致）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    _ensure_board_known(board)
    return {"board": board, "items": _sort_cons(_cons_sw(board)), "source_hint": "申万"}


def get_cons_em(board: str) -> Dict[str, Any]:
    """成分股·三级兜底（东财；本机 push2 被重置，仅生产备用）。

    Args:
        board: 板块名称（与板块树中的名称一致）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    _ensure_board_known(board)
    return {"board": board, "items": _sort_cons(_cons_em(board)), "source_hint": "东财"}


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
    except Exception:  # noqa: BLE001 - 板块树不可用时仅靠申万名录
        pass
    for getter in (get_industry, get_concept):
        try:
            for row in getter("cn"):
                # 同花顺行业/概念行字段为 name；申万等其他源可能用 名称
                names.add(str(row.get("name") or row.get("名称") or "").strip())
        except Exception:  # noqa: BLE001 - 单个列表失败不影响判定
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
    _ensure_enabled()
    rows = ths_http.get_industry_flow_rows()
    # 四档依赖成分映射；未预热完时按时间预算补 1–2 个板块，
    # 使「首次使用也能逐步补齐」，而不是永久空白（预算内补不完由后台线程继续）。
    _ensure_cons_progress(rows, budget_seconds=6.0, max_boards=2)
    items: List[Dict[str, Any]] = []
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
                "pct": _round(_num(row.get("pct"))),
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
_FLOW_TIER_CACHE: Dict[str, Any] = {}
_FLOW_TIER_TTL = 600  # 四档 10 分钟刷新
# 板块成分映射：{板块名: [6 位代码]}（落盘缓存 7 天，构建慢）
_CONS_CACHE: Dict[str, List[str]] = {}
_CONS_CACHE_LOADED = False
_CONS_CACHE_LOCK = threading.Lock()
_TIER_WARMING: set[str] = set()


def _cons_cache_file():
    """板块成分映射落盘文件路径。

    Returns:
        Path: ``<CACHE_DIR>/board_cons.json``。
    """
    return config.CACHE_DIR / "board_cons.json"


def _load_cons_cache() -> None:
    """从磁盘载入板块成分映射（进程内只载一次；文件缺失或损坏时保持为空）。"""
    global _CONS_CACHE_LOADED
    if _CONS_CACHE_LOADED:
        return
    with _CONS_CACHE_LOCK:
        if _CONS_CACHE_LOADED:
            return
        _CONS_CACHE_LOADED = True
        path = _cons_cache_file()
        try:
            if path.is_file():
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    for key, value in payload.items():
                        if isinstance(value, list):
                            _CONS_CACHE[key] = [str(code) for code in value]
                logger.info("板块成分映射已载入：%d 个板块", len(_CONS_CACHE))
        except Exception as exc:  # noqa: BLE001 - 缓存损坏不影响主流程
            logger.warning("板块成分映射载入失败：%s", type(exc).__name__)


def _save_cons_cache() -> None:
    """把板块成分映射原子写盘（失败仅告警，不影响接口）。"""
    try:
        from backend.app.core import atomic

        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        atomic.atomic_write_json(_cons_cache_file(), _CONS_CACHE, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001 - 写盘失败不影响内存缓存
        logger.warning("板块成分映射写盘失败：%s", type(exc).__name__)


def get_board_constituents(board: str, *, kind: str = "industry") -> List[str]:
    """取板块成分股代码列表（内存 + 磁盘缓存，避免重复翻页触发同花顺限流）。

    Args:
        board: 板块名。
        kind: industry / concept。

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
    rows = ths_http.get_board_cons(code, kind=kind)
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


def _flow_tier(board: str, code: Optional[str]) -> Optional[Dict[str, Any]]:
    """取某板块的四档资金流（10 分钟缓存；未预热时返回 None 并触发后台预热）。

    Args:
        board: 板块名。
        code: 同花顺板块代码（未用到，保留以兼容调用方）。

    Returns:
        Optional[Dict[str, Any]]: 四档聚合结果；未就绪返回 None。
    """
    cached = _FLOW_TIER_CACHE.get(board)
    if cached and time.time() - cached[0] < _FLOW_TIER_TTL:
        return cached[1]
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
        _FLOW_TIER_CACHE[board] = (time.time(), payload)
        return payload
    return None


def warm_fundflow_tiers(*, max_boards: int = 90) -> int:
    """后台预热板块四档：逐个构建成分映射并聚合四档（守护线程调用）。

    顺序执行的目的是遵守同花顺 1.1s 限流；成分映射落盘后 7 天内不再重复翻页，
    后续刷新只走新浪批量接口（约 3 秒完成 90 个板块）。

    Args:
        max_boards: 本次最多预热多少个板块（默认全量 90）。

    Returns:
        int: 成功聚合的板块数。
    """
    _load_cons_cache()
    done = 0
    try:
        names = [row["name"] for row in ths_http.get_industry_rows() if row.get("name")]
    except Exception as exc:  # noqa: BLE001 - 名录不可用则本次不预热
        logger.info("板块四档预热跳过（名录不可用：%s）", type(exc).__name__)
        return 0
    for name in names[:max_boards]:
        if name in _TIER_WARMING:
            continue
        _TIER_WARMING.add(name)
        try:
            if name not in _CONS_CACHE:
                get_board_constituents(name)
            payload = _flow_tier(name, None)
            if payload:
                done += 1
        except Exception as exc:  # noqa: BLE001 - 单板块失败继续下一个
            logger.info("板块四档预热跳过 %s：%s", name, type(exc).__name__)
        finally:
            _TIER_WARMING.discard(name)
    logger.info("板块四档预热完成：%d/%d 个板块", done, min(len(names), max_boards))
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
    "amount_share": 0.45,   # 成交额占比分位
    "bias": 0.25,           # 乖离率分位（收盘/MA20−1）
    "volume_share": 0.15,   # 成交量占比分位
    "momentum": 0.15,       # 近 20 日动量分位
}

CROWDING_MISSING = (
    "五项里有两项口径调整、三项未纳入："
    "① 成交额维度改用「当日/近250日均值」的相对强度，不是「占全市场比重」——"
    "全市场成交额源（东财 push2his）在本机被源站重置，新浪指数日线只有成交量无成交额；"
    "② 换手率分位未纳入，同花顺行业日线不含换手率与流通股本；"
    "③ 行业融资余额分位未纳入，融资余额仅有全市场口径无行业口径；"
    "④ 成分股相关性未纳入，东财 push2 成分股接口在本机被源站重置，取不到行业成分"
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


def _index_kline_func(kind: str = "industry"):
    """兼容占位：板块指数日线已改由纯 HTTP 通道取（见 :func:`_kline_ths`）。

    Args:
        kind: industry=行业 / concept=概念。

    Returns:
        None: 恒返回 None，调用方应改用 :func:`_kline_ths`。

    Notes:
        第八轮移除以 py_mini_racer(V8) 为前置的 akshare 板块指数函数；
        本函数仅为旧调用方的显式失效点，避免静默回退到 V8 通道。
    """
    return None


def get_crowding(board: str, *, kind: str = "industry", fill_today: bool = True) -> Dict[str, Any]:
    """单板块拥挤度打分（M3）。

    四个维度的分位加权成 0–100 分，>60 提示、>80 提示复核（用户口径）。
    只陈述「交易结构位置」，不给买卖指令。

    Args:
        board: 板块名（如「半导体」）。
        kind: 板块类型：industry=行业 / concept=概念（均走纯 HTTP 板块日线）。
        fill_today: 是否补当日成交额（批量排行扫描时置 False）。

    Returns:
        dict: {board, kind, score, level, parts, sample_days, as_of, caliber, missing}

    Raises:
        ValueError: 板块日线取不到或样本不足。
    """
    _ensure_enabled()
    rows: List[Dict[str, Any]] = []
    for row in _kline_ths(kind, board, "daily", fill_today=fill_today):
        day = str(row.get("date") or "")[:10]
        close = _num(row.get("close"))
        amount = _num(row.get("amount"))
        volume = _num(row.get("volume"))
        if day and close and amount and volume:
            rows.append({"date": day, "close": close, "amount": amount, "volume": volume})
    rows.sort(key=lambda item: item["date"])
    if len(rows) < 60:
        raise ValueError(f"{board} 日线样本不足（{len(rows)} 个交易日），无法计算分位")

    # 成交额强度 = 当日成交额 / 近 250 日均值（消除市场规模自然增长带来的趋势抬升）。
    # 原设计用「占全市场比重」，但全市场成交额源（东财 push2his）在本机被源站重置，
    # 新浪指数日线只有成交量没有成交额，无法合成可靠分母；改用「相对自身历史强度」，
    # 只依赖板块日线自身，口径自洽且可复算。差异已在 caliber 写明。
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
    cached_codes = _CONS_CACHE.get(board) or []
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
    else:
        cons_missing.append("成分映射尚未预热（服务启动后由后台线程构建，约数分钟）")
    # ③ 行业融资余额：本机无行业口径源（融资余额仅有全市场口径），如实标注
    cons_missing.append("行业融资余额分位未纳入（融资余额仅有全市场口径，无行业口径源）")

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
            f"{kind_label}拥挤度=四维分位加权（成交额占比 {int(CROWDING_WEIGHTS['amount_share']*100)}% / "
            f"乖离率 {int(CROWDING_WEIGHTS['bias']*100)}% / 量能 {int(CROWDING_WEIGHTS['volume_share']*100)}% / "
            f"动量 {int(CROWDING_WEIGHTS['momentum']*100)}%），分位窗口=近 {len(rows)} 个交易日；"
            "成交额维度用「当日/近250日均值」的相对强度（全市场成交额源不可用，改相对自身历史）；"
            "只陈述交易结构位置，不构成买卖建议"
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
    max_workers: int = 6,
) -> Dict[str, Any]:
    """全板块拥挤度排行（第七轮 M12）。

    复用 `get_crowding()` 同一套四维打分（同口径、同函数），逐板块并发计算后按得分降序。
    只陈述交易结构位置，不给买卖指令。

    Args:
        kind: 板块类型，目前仅支持 industry（概念板块接口在本机不稳定，未纳入）。
        limit: 返回前 N 名（1–120）。
        max_workers: 并发线程数（同花顺签名在进程级锁内串行，HTTP 可部分并行）。

    Returns:
        Dict[str, Any]: {items, scanned, total, failed, level_counts, caliber, as_of}

    Raises:
        ValueError: 板块类型不支持或清单为空。
    """
    _ensure_enabled()
    # 本函数为**纯同步扫描体**（供测试直连与后台线程复用）；
    # 缓存查表与「后台计算 + 立即返回计算中态」的 HTTP 语义由 routers/sector.py 接管，
    # 故此处不查缓存、不启后台，避免把「计算中」中间态落盘 1 小时。
    return _scan_rankings(kind, limit, max_workers=max_workers)


def _scan_rankings(kind: str, limit: int, *, max_workers: int = 6) -> Dict[str, Any]:
    """排行实际扫描体（后台线程调用；不查缓存、不启动后台）。

    Args:
        kind: industry / concept。
        limit: 返回前 N 名。
        max_workers: 并发线程数。

    Returns:
        Dict[str, Any]: 排行结果（同 get_crowding_rankings 成功态）。
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
        """单板块打分（异常向上抛，由调用方归类到 failed）。"""
        data = get_crowding(board, kind=key_kind, fill_today=False)
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
            f"与单板块拥挤度同一函数（四维分位加权：成交额强度 {int(CROWDING_WEIGHTS['amount_share']*100)}% / "
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
    _ensure_enabled()
    key_kind = str(kind or "industry").strip().lower()
    if key_kind == "concept":
        names = [row.get("name") for row in tencent_board.get_board_rows("concept")]
    else:
        names = [row.get("name") for row in ths_http.get_industry_rows()]
    return [str(name).strip() for name in names if str(name or "").strip()]


# 成分股源的确定性失效类型（本机代理会拦截东财 push2，不重试）
_CONS_FAIL = ("ProxyError", "SSLError", "ConnectionError", "RemoteDisconnected", "ValueError")


def get_sector_constituents(board: str, *, kind: str = "industry") -> List[Dict[str, Any]]:
    """取某板块的成分股列表（供拥挤度的换手率/相关性维度、资金流四档聚合使用）。

    2026-11-02 第八轮重写：主源改为同花顺板块详情页（纯 HTTP，``/thshy/detail`` 与
    ``/gn/detail``），不再依赖东财 push2（本机被源站重置）与申万（页面改版后返回空表）。
    同花顺反爬触发时（401/403）自动退避重试，仍失败则回退新浪板块成分。

    Args:
        board: 板块名（如「半导体」「白酒概念」）。
        kind: industry / concept。

    Returns:
        List[Dict[str, Any]]: 成分股列表 [{code, name, pct, board}]。

    Raises:
        ValueError: 全部源均失败时抛出，附具体源名与异常类型。
    """
    _ensure_enabled()
    errors: List[str] = []
    try:
        code = ths_http.get_board_code(board, kind=kind)
        if not code:
            errors.append("同花顺名录无此板块")
        else:
            rows = ths_http.get_board_cons(code, kind=kind)
            items = [
                {"code": row["code"], "name": row.get("name"), "pct": row.get("pct"),
                 "board": board}
                for row in rows
                if row.get("code")
            ]
            if items:
                return items
            errors.append("同花顺成分返回空")
    except Exception as exc:  # noqa: BLE001 - 记录后尝试备源
        errors.append(f"同花顺成分:{type(exc).__name__}")

    # 备源：新浪板块成分（需先反查 label）
    try:
        label = _sina_label(board)
        if not label:
            errors.append("新浪板块映射缺失")
        else:
            df = _call(lambda: ak.stock_sector_detail(sector=label))
            rows = utils.clean_records(df)
            items = [
                {"code": str(r.get("代码") or r.get("CODE") or "").zfill(6),
                 "name": str(r.get("名称") or r.get("NAME") or ""), "board": board}
                for r in rows
            ]
            items = [i for i in items if i["code"].isdigit()]
            if items:
                return items
            errors.append("新浪成分返回空")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"新浪成分:{type(exc).__name__}")

    raise ValueError("；".join(errors) if errors else "成分股不可用")


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
    from backend.app.datasource import tencent

    sample = constituents[:max_samples]
    series: Dict[str, List[float]] = {}
    for item in sample:
        code = item.get("code") or ""
        if not code:
            continue
        try:
            klines = tencent.get_kline(code, period="daily", limit=days + 5)
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
