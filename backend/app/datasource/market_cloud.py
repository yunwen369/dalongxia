"""大盘云图数据源（PRD《市场大盘云图前后端开发技术方案 V1.0》§2.3 / §6 / §7.1）。

上游：东方财富 push2 行情列表 ``/api/qt/clist/get``
    - 分页上限 100 条/页（实测 pz=500/1000/6000 均被截断为 100），A 股 5,564 只
      需 56 页；用 6 路并发把首屏耗时压到约 2 秒，随后由 60 秒缓存承接。
    - 字段：f2 最新价 / f3 涨跌幅 / f4 涨跌额 / f6 成交额 / f8 换手率 /
      f12 代码 / f14 名称 / f15 最高 / f16 最低 / f17 今开 / f18 昨收 /
      f20 总市值 / f21 流通市值 / f100 行业（申万二级口径）。

红线（沿用项目既有数据源约定）：
1. 数值只来自源站，缺字段一律输出 ``None``，**不补零、不估算、不合成**；
2. 抓取不完整（有分页失败）时 ``stale=True`` 并在 ``sourceNote`` 写明缺失页数，
   由前端标注延迟，不把不完整快照当成完整快照；
3. 单位换算在适配器内完成（元 → 亿元），不把上游原始单位透传前端（§6.4）。

分组口径（§2.3.1）：
    - A 股：一级 = 申万一级（由 f100 的申万二级归并），二级 = 申万二级；
      行业缺失的标的一律进「未分类」，与「一级兜底」策略一致。
    - ETF：一级 = 资产类别（宽基/行业/跨境/策略/主题/商品/债券/货币），
      二级 = 名称中 ETF 之前的主体（跟踪指数或细分主题）。
    - 可转债：一级 = 价格带（§6.5），二级 = 信用评级；上游不提供评级，统一「未评级」。
    - 港股：一级 = 恒生行业（f100），二级 = 同一级兜底（上游无二级字段）。
"""

from __future__ import annotations

import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from backend.app import config
from backend.app.core import cache, netfix
from backend.app.datasource import utils
from backend.app.datasource.sw_industry import UNCLASSIFIED, l1_of, l2_of

logger = logging.getLogger(__name__)

_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
_REFERER = "https://quote.eastmoney.com/"
_PAGE_SIZE = 100
_MAX_PAGES = 90
_WORKERS = 6
_RETRY = 1

_FIELDS = "f2,f3,f4,f6,f8,f12,f14,f15,f16,f17,f18,f20,f21,f25,f100,f109,f110"

# 涨跌周期 → 上游字段（实测 2026-11-18 与 K 线聚合结果逐项对齐）
#   f3   当日涨跌幅
#   f109 5 日涨跌幅  → 周
#   f110 20 日涨跌幅 → 月
#   f25  年初至今    → 年
PERIOD_FIELDS: Dict[str, Tuple[str, str]] = {
    "day": ("f3", "当日"),
    "week": ("f109", "5 日（周）"),
    "month": ("f110", "20 日（月）"),
    "year": ("f25", "年初至今（年）"),
}

# 市场规格：fs 过滤串、面积口径、单位、色阶量程、价格精度
MARKET_SPECS: Dict[str, Dict[str, Any]] = {
    "stock": {
        "label": "A股",
        # 沪市主板 + 深市主板/中小板 + 创业板 + 科创板 + 北交所，
        # 与站点「沪深京全市场」口径一致（北交所 366 只由 m:0+t:81+s:2048 提供）。
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
        "area_unit": "亿元",
        "default_area": "marketCapTotal",
        "areas": ("marketCapTotal", "marketCapFloat", "turnover"),
        "legend": 4.0,
        "price_digits": 2,
        "calendar": "a",
    },
    "etf": {
        "label": "ETF",
        # 东财 ETF 行情中心的四个板块：股票型 / 货币型 / 跨境(QDII) / 商品型。
        # 实测债券型 ETF（如 511010 国债ETF）不在这四个板块内，且无独立板块编码，
        # 故「债券」分组本轮为空缺，由 sourceNote 如实说明，不用 LOF 板块凑数。
        "fs": "b:MK0021,b:MK0022,b:MK0023,b:MK0024",
        "area_unit": "亿元",
        "default_area": "scale",
        "areas": ("scale", "turnover"),
        "legend": 4.0,
        "price_digits": 3,
        "calendar": "a",
    },
    "convertible": {
        "label": "可转债",
        "fs": "b:MK0354",
        "area_unit": "亿元",
        "default_area": "scale",
        "areas": ("scale", "turnover"),
        "legend": 4.0,
        "price_digits": 3,
        "calendar": "a",
    },
    "hk": {
        "label": "港股",
        "fs": "m:128+t:3,m:128+t:4",
        "area_unit": "亿港元",
        "default_area": "marketCapFloat",
        "areas": ("marketCapFloat", "marketCapTotal", "turnover"),
        "legend": 10.0,
        "price_digits": 3,
        "calendar": "hk",
    },
    "us": {
        "label": "美股",
        "fs": "m:105,m:106,m:107",
        "area_unit": "亿美元",
        "default_area": "marketCapFloat",
        "areas": ("marketCapFloat", "marketCapTotal", "turnover"),
        "legend": 3.0,
        "price_digits": 2,
        "calendar": "us",
    },
}

# 面积口径中文名（前端弹窗/筛选面板展示用，PRD §6.3）
AREA_LABELS = {
    "marketCapTotal": "总市值",
    "marketCapFloat": "流通市值",
    "turnover": "成交额",
    "scale": "规模",
}

# 涨跌周期：四个周期全部由上游字段直接支撑（见 PERIOD_FIELDS），
# 无需逐票 K 线聚合；周=5 日、月=20 日、年=年初至今，口径在界面上如实标注。
PERIODS = ("day", "week", "month", "year")

# 色阶量程随周期放大：周/月/年涨跌幅天然比当日大，若沿用 ±4% 会大面积饱和成纯色，
# 失去组内强弱区分。系数按四周期实际分布标定（stock 日均 |p|≈1.5%，周≈5.8%，
# 月≈7.7%，年≈14.1%），保证 90% 分位落在 60%~100% 饱和度区间。
_PERIOD_LEGEND_SCALE = {"day": 1.0, "week": 2.5, "month": 3.5, "year": 6.0}

VIEWS = ("all", "sh", "sz", "hs300", "a500", "chinext", "star", "bj", "watchlist")
SORT_KEYS = (
    "changePct", "changeAmplitude", "turnover", "name",
    "marketCapTotal", "marketCapFloat",
)


# =============================================================
# 一、抓取
# =============================================================
def _fetch_page(fs: str, page: int) -> Dict[str, Any]:
    """抓取单页行情列表。

    Args:
        fs: 东财市场过滤串（如 ``m:0+t:6,m:0+t:80``）。
        page: 页码，从 1 开始。

    Returns:
        Dict[str, Any]: 原始 JSON 的 ``data`` 段；失败时返回 ``{}``。
    """
    params = {
        "pn": page,
        "pz": _PAGE_SIZE,
        "po": 1,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": "f3",
        "fs": fs,
        "fields": _FIELDS,
    }
    last: Optional[BaseException] = None
    for attempt in range(_RETRY + 1):
        try:
            resp = netfix.http_request(
                "GET", _CLIST_URL, params=params,
                headers={"Referer": _REFERER, "User-Agent": "Mozilla/5.0"},
                timeout=config.DATASOURCE_TIMEOUT,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            return (resp.json() or {}).get("data") or {}
        except Exception as exc:  # noqa: BLE001 - 单页失败由调用方统计缺失
            last = exc
            logger.warning("云图分页失败 fs=%s page=%s attempt=%s: %s",
                           fs, page, attempt + 1, exc)
    logger.debug("云图分页彻底失败 fs=%s page=%s: %s", fs, page, last)
    return {}


def _fetch_all(market: str) -> Dict[str, Any]:
    """抓取某市场的全部行情快照。

    Args:
        market: stock/etf/convertible/hk/us。

    Returns:
        Dict[str, Any]: ``{raw, total, pages_expected, pages_ok, as_of}``。
    """
    spec = MARKET_SPECS[market]
    fs = spec["fs"]
    as_of = utils.now_text()

    first = _fetch_page(fs, 1)
    diff = first.get("diff") or []
    total = int(first.get("total") or 0)
    if not diff or total <= 0:
        return {"raw": [], "total": 0, "pages_expected": 0, "pages_ok": 0, "as_of": as_of}

    pages = min(math.ceil(total / _PAGE_SIZE), _MAX_PAGES)
    raw: List[Dict[str, Any]] = list(diff)
    ok = 1
    if pages > 1:
        with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
            results = list(pool.map(lambda p: _fetch_page(fs, p), range(2, pages + 1)))
        for chunk in results:
            rows = chunk.get("diff") or []
            if rows:
                raw.extend(rows)
                ok += 1

    return {
        "raw": raw,
        "total": total,
        "pages_expected": pages,
        "pages_ok": ok,
        "as_of": as_of,
    }


# =============================================================
# 二、归一化（§6.1 ItemDTO + §6.4 单位换算）
# =============================================================
def _num(value: Any) -> Optional[float]:
    """把上游数值安全转为 float；``-``/空/非数值一律返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _yi(value: Any) -> Optional[float]:
    """元 → 亿元（保留两位小数）；缺失返回 None，不补零。"""
    num = _num(value)
    return None if num is None else round(num / 1e8, 2)


def _round2(value: Optional[float]) -> Optional[float]:
    """保留两位小数；None 原样返回。"""
    return None if value is None else round(value, 2)


def _price_band(price: Optional[float]) -> str:
    """可转债价格带（PRD §6.5）。"""
    if price is None:
        return "无价格"
    if price < 110:
        return "债性区"
    if price < 120:
        return "平衡区"
    if price < 130:
        return "偏股区"
    if price < 150:
        return "股性区"
    return "高价区"


# ETF 资产类别关键词（顺序即优先级，命中即停）
_ETF_RULES: Sequence[Tuple[str, Tuple[str, ...]]] = (
    ("债券", ("债", "转债", "信用", "政金", "国债", "地方债", "城投")),
    # 只认「货币」，不认裸「现金」——否则「自由现金流」类策略 ETF 会被误判为货币
    ("货币", ("货币", "现金添富", "保证金", "日日添利")),
    ("商品", ("黄金", "白银", "豆粕", "能源化工", "原油", "商品", "有色期货")),
    ("跨境", (
        "恒生", "港股", "香港", "恒指", "纳斯达克", "纳指", "标普", "道琼斯",
        "日经", "东证", "德国", "法国", "英国", "东南亚", "亚太", "中概",
        "海外", "全球", "美股", "越南", "印度", "沙特", "美国", "中韩",
    )),
    ("宽基", (
        "沪深300", "上证50", "上证180", "上证380", "中证500", "中证1000",
        "中证2000", "中证A500", "中证A50", "中证A100", "中证100", "中证800",
        "A500", "A50", "A100", "科创50", "科创100", "科创200", "科创综指",
        "创业板", "深证100", "深证成指", "双创", "北证50", "MSCI",
        "中证全指", "上证综指", "深证综指", "全指",
    )),
    ("策略", (
        "红利", "低波", "价值", "成长", "质量", "动量", "基本面", "央企",
        "国企", "等权", "增强", "自由现金流", "股息", "多因子", "龙头",
    )),
    ("行业", (
        "半导体", "芯片", "医药", "医疗", "生物", "创新药", "军工", "国防",
        "新能源", "光伏", "风电", "电池", "银行", "证券", "保险", "非银",
        "白酒", "食品", "饮料", "消费", "家电", "汽车", "有色", "稀土",
        "煤炭", "钢铁", "化工", "建材", "建筑", "农业", "养殖", "环保",
        "传媒", "计算机", "软件", "通信", "电子", "地产", "房地产", "电力",
        "公用", "交通", "物流", "航空", "旅游", "教育", "纺织", "轻工",
        "机械", "机器人", "黄金股", "基建", "电网", "储能",
    )),
)

_ETF_TAG = re.compile(r"(ETF|LOF|联接)", re.IGNORECASE)


def _etf_class(name: str) -> str:
    """按名称关键词判定 ETF 资产类别；无命中归「主题」。"""
    if "黄金股" in name:
        return "行业"
    for label, keys in _ETF_RULES:
        if any(k in name for k in keys):
            return label
    return "主题"


def _etf_theme(name: str) -> str:
    """取 ETF 名称中标签之前的主体作为细分主题（跟踪指数/主题）。"""
    head = _ETF_TAG.split(name)[0].strip()
    return head or name


def _to_item(raw: Dict[str, Any], market: str) -> Optional[Dict[str, Any]]:
    """把一条上游记录转成 ItemDTO（§6.1）。

    Args:
        raw: 东财 clist 单条记录。
        market: 市场键。

    Returns:
        Optional[Dict[str, Any]]: 转换后的标的项；代码/名称缺失时返回 None。
    """
    code = str(raw.get("f12") or "").strip()
    name = str(raw.get("f14") or "").strip()
    if not code or not name:
        return None

    price = _num(raw.get("f2"))
    pct = _num(raw.get("f3"))
    change_amt = _num(raw.get("f4"))
    high, low, prev = _num(raw.get("f15")), _num(raw.get("f16")), _num(raw.get("f18"))
    amplitude = None
    if high is not None and low is not None and prev not in (None, 0):
        amplitude = round((high - low) / prev * 100, 2)

    mcap_total = _yi(raw.get("f20"))
    mcap_float = _yi(raw.get("f21"))
    turnover = _yi(raw.get("f6"))

    industry = raw.get("f100")
    turnover_rate = _num(raw.get("f8"))
    extra: Dict[str, Any] = {}
    if market == "stock":
        l1, l2 = l1_of(industry), l2_of(industry)
        extra = {"industry": l1, "subIndustry": l2, "estimated": False, "turnoverRate": turnover_rate}
        scale = None
    elif market == "etf":
        l1, l2 = _etf_class(name), _etf_theme(name)
        extra = {"estimated": False, "turnoverRate": turnover_rate}
        scale = mcap_total
    elif market == "convertible":
        l1, l2 = _price_band(price), "未评级"
        extra = {"estimated": False, "turnoverRate": turnover_rate}
        scale = mcap_total
    elif market == "hk":
        base = (str(industry).strip() if industry not in (None, "-", "") else UNCLASSIFIED)
        l1, l2 = base, base
        extra = {"industry": l1, "subIndustry": l2, "estimated": False, "turnoverRate": turnover_rate}
        scale = None
    else:  # us
        base = (str(industry).strip() if industry not in (None, "-", "") else UNCLASSIFIED)
        l1, l2 = base, base
        extra = {"industry": l1, "subIndustry": l2, "estimated": False, "turnoverRate": turnover_rate}
        scale = None

    # 四个周期的涨跌幅一并下发：前端切换「日/周/月/年」无需重新请求，
    # 同时 overview(period=…) 仍按契约把 changePct 换成对应周期的值。
    extra["changePctDay"] = None if pct is None else round(pct, 2)
    extra["changePctWeek"] = _round2(_num(raw.get("f109")))
    extra["changePctMonth"] = _round2(_num(raw.get("f110")))
    extra["changePctYear"] = _round2(_num(raw.get("f25")))

    return {
        "code": code,
        "name": name,
        "price": None if price is None else round(price, MARKET_SPECS[market]["price_digits"]),
        "changePct": None if pct is None else round(pct, 2),
        "changeAmt": None if change_amt is None else round(change_amt, 3),
        "amplitude": amplitude,
        "marketCapFloat": mcap_float,
        "marketCapTotal": mcap_total,
        "turnover": turnover,
        "scale": scale,
        "status": "suspended" if price is None else "trading",
        "extra": extra,
        # 内部分组字段：序列化前由 _strip 删除，仅用于 build_groups
        "_l1": l1,
        "_l2": l2,
    }


def _code_for(market: str, level1: str) -> str:
    """生成一级分组编码（稳定、可读、可过滤）。"""
    return f"{market}:{level1}"


# =============================================================
# 三、分组（§2.3.1 / §6.2）
# =============================================================
def _group_stat(items: Sequence[Dict[str, Any]]) -> Tuple[int, int, int, Optional[float]]:
    """组内涨跌家数与平均涨跌幅（空值时不计入样本）。"""
    up = flat = down = 0
    total = 0.0
    sample = 0
    for it in items:
        pct = it.get("changePct")
        if pct is None:
            continue
        sample += 1
        total += pct
        if pct > 0.05:
            up += 1
        elif pct < -0.05:
            down += 1
        else:
            flat += 1
    avg = round(total / sample, 2) if sample else None
    return up, flat, down, avg


def _area_value(item: Dict[str, Any], area_metric: str) -> float:
    """取面积口径数值；缺失按 0 处理（排序与合计用）。"""
    value = item.get(area_metric)
    return float(value) if isinstance(value, (int, float)) else 0.0


def build_groups(items: Sequence[Dict[str, Any]], market: str,
                 area_metric: str) -> List[Dict[str, Any]]:
    """把扁平标的列表构造成 GroupNode[]（§6.2）。

    排序遵循 §2.3：一级、二级按当前面积口径合计值降序，合计相同按名称升序。

    Args:
        items: ItemDTO 列表（含内部 ``_l1`` / ``_l2``）。
        market: 市场键。
        area_metric: 当前生效面积口径。

    Returns:
        List[Dict[str, Any]]: 分组树。
    """
    tree: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for it in items:
        l1 = it.get("_l1") or UNCLASSIFIED
        l2 = it.get("_l2") or l1
        tree.setdefault(l1, {}).setdefault(l2, []).append(it)

    groups: List[Dict[str, Any]] = []
    for l1, subs in tree.items():
        all_items = [it for rows in subs.values() for it in rows]
        group_total = sum(_area_value(it, area_metric) for it in all_items)
        sub_nodes = []
        for l2, rows in subs.items():
            rows.sort(key=lambda x: (-_area_value(x, area_metric), x["name"]))
            sub_nodes.append({
                "level2": l2,
                "items": rows,
                "_total": sum(_area_value(it, area_metric) for it in rows),
            })
        sub_nodes.sort(key=lambda n: (-n["_total"], n["level2"]))
        for node in sub_nodes:
            node.pop("_total", None)
        up, flat, down, avg = _group_stat(all_items)
        groups.append({
            "level1": l1,
            "level1Code": _code_for(market, l1),
            "count": len(all_items),
            "up": up,
            "flat": flat,
            "down": down,
            "avgPct": avg,
            "totalScale": round(group_total, 2),
            "children": sub_nodes,
            "_total": group_total,
        })

    groups.sort(key=lambda g: (-g["_total"], g["level1"]))
    for g in groups:
        g.pop("_total", None)
    return groups


# =============================================================
# 四、过滤（§6.7 FilterQuery）
# =============================================================
def _match_view(item: Dict[str, Any], view: str) -> bool:
    """A 股快捷视图过滤（§6.3 view）。

    指数成分类视图（hs300/a500）依赖成分股名单，本轮上游未接入，
    交由 :func:`_unsupported_views` 显式说明，这里只做代码前缀可判定的视图。
    """
    code = item["code"]
    if view == "all":
        return True
    if view == "sh":
        return code.startswith("6")
    if view == "sz":
        return code.startswith(("0", "3"))
    if view == "chinext":
        return code.startswith("3")
    if view == "star":
        return code.startswith("688")
    if view == "bj":
        return code.startswith(("8", "4", "9"))
    return True


_UNSUPPORTED_VIEWS = {
    "watchlist": "自选",
}

# 指数成分视图：沪深300 走东财板块（BK0500，300 只，3 页），
# 中证A500 走上游中证指数名录（akshare 封装，500 只，月频更新 → 12 小时缓存）。
_INDEX_BOARD = {"hs300": "BK0500"}
_INDEX_CSINDEX = {"a500": "000510"}


def _members_from_board(board: str) -> frozenset:
    """按东财板块码取成分股集合（分页抓全）。"""
    codes = set()
    page = 1
    while page <= 12:
        data = _fetch_page(f"b:{board}", page)
        rows = data.get("diff") or []
        if not rows:
            break
        for row in rows:
            code = str(row.get("f12") or "").strip()
            if code:
                codes.add(code)
        total = int(data.get("total") or 0)
        if len(codes) >= total or len(rows) < _PAGE_SIZE:
            break
        page += 1
    return frozenset(codes)


def _members_from_csindex(symbol: str) -> frozenset:
    """按中证指数代码取成分股集合（akshare 名录，失败返回空集）。"""
    try:
        import akshare as ak

        frame = ak.index_stock_cons_csindex(symbol=symbol)
        column = next((c for c in frame.columns if "成分券代码" in str(c)), None)
        if column is None:
            return frozenset()
        return frozenset(str(v).strip().zfill(6) for v in frame[column].tolist())
    except Exception as exc:  # noqa: BLE001 - 成分名录失败时该视图退回全市场并注明
        logger.warning("指数成分名录不可用 symbol=%s：%s", symbol, type(exc).__name__)
        return frozenset()


def index_members(view: str) -> frozenset:
    """取指数成分集合（带 12 小时缓存）。

    Args:
        view: hs300 / a500。

    Returns:
        frozenset: 成分代码集合；不可用时为空集合。
    """
    loader = None
    if view in _INDEX_BOARD:
        loader = lambda: _members_from_board(_INDEX_BOARD[view])  # noqa: E731
    elif view in _INDEX_CSINDEX:
        loader = lambda: _members_from_csindex(_INDEX_CSINDEX[view])  # noqa: E731
    if loader is None:
        return frozenset()

    # 缓存值必须是可 JSON 序列化的 list（磁盘兜底要落盘），读取时再转 frozenset
    cached = cache.get_or_load(
        f"market_cloud:members:{view}",
        int(config.TTL.get("market_cloud_index", 43200)),
        lambda: sorted(loader()),
    )
    return frozenset(cached or ())

# 上游口径局限（随响应下发，前端在「数据来源与口径」处展示）
_MARKET_NOTES = {
    "etf": "上游 ETF 板块仅覆盖股票型/货币型/跨境/商品型四类，债券型 ETF 不在其中，债券分组本轮空缺。",
    "convertible": "上游不提供可转债信用评级，二级分组统一归入「未评级」；一级按 PRD §6.5 价格带划分。",
    "hk": "上游港股仅提供恒生行业一级分类，无细分行业，二级按一级兜底。",
}


def _filter_items(items: Iterable[Dict[str, Any]], *, market: str, view: str,
                  groups: Sequence[str], keyword: str, direction: str,
                  pct_min: Optional[float], pct_max: Optional[float],
                  watchlist: Optional[Sequence[str]],
                  members: Optional[frozenset] = None) -> List[Dict[str, Any]]:
    """按 FilterQuery 过滤标的。

    Args:
        items: 已按周期改写 ``changePct`` 的标的列表。
        market: 市场键。
        view: 快捷视图。
        groups: 分组编码。
        keyword: 名称/代码关键词。
        direction: all/up/down（按当前周期的 changePct 判定）。
        pct_min: 涨跌幅下限。
        pct_max: 涨跌幅上限。
        watchlist: 自选键集合。
        members: 指数成分代码集合；为空表示不按成分过滤。

    Returns:
        List[Dict[str, Any]]: 命中标的。
    """
    key = (keyword or "").strip()
    selected = {g.strip() for g in groups if g and g.strip()}
    picked_l1 = set()
    picked_l2 = set()
    for g in selected:
        if "/" in g:
            l1, l2 = g.split("/", 1)
            picked_l1.add(l1.strip())
            picked_l2.add(l2.strip())
        else:
            picked_l1.add(g.strip())

    out: List[Dict[str, Any]] = []
    for it in items:
        if market == "stock" and not _match_view(it, view):
            continue
        if members and it["code"] not in members:
            continue
        if market == "stock" and view == "watchlist":
            keys = set(watchlist or ())
            if f"stock:{it['code']}" not in keys:
                continue
        if picked_l1 and it["_l1"] not in picked_l1:
            continue
        if picked_l2 and it["_l2"] not in picked_l2:
            continue
        if key and key not in it["name"] and key not in it["code"]:
            continue
        pct = it.get("changePct")
        if direction == "up" and not (pct is not None and pct > 0.05):
            continue
        if direction == "down" and not (pct is not None and pct < -0.05):
            continue
        if pct_min is not None and (pct is None or pct < pct_min):
            continue
        if pct_max is not None and (pct is None or pct > pct_max):
            continue
        out.append(it)
    return out


# =============================================================
# 五、快照缓存与响应组装（§7.1）
# =============================================================
def _ttl() -> int:
    """云图快照缓存秒数（config.TTL 未登记时退回 60 秒）。"""
    return int(config.TTL.get("market_cloud", 60))


def _load(market: str) -> Dict[str, Any]:
    """未命中缓存时加载整市场快照并归一。"""
    fetched = _fetch_all(market)
    items: List[Dict[str, Any]] = []
    for row in fetched["raw"]:
        item = _to_item(row, market)
        if item is not None:
            items.append(item)
    expected = fetched["pages_expected"]
    ok = fetched["pages_ok"]
    missing = max(expected - ok, 0)
    return {
        "items": items,
        "total": fetched["total"],
        "pages_expected": expected,
        "pages_ok": ok,
        "missing_pages": missing,
        "as_of": fetched["as_of"],
    }


def snapshot(market: str, force: bool = False) -> Dict[str, Any]:
    """取某市场快照（60 秒缓存，``force`` 时跳过缓存）。

    Args:
        market: 市场键。
        force: True 时强制回源（对应 ``refresh=force=1``）。

    Returns:
        Dict[str, Any]: ``{items, total, pages_*, as_of}``。
    """
    key = f"market_cloud:snapshot:{market}"
    if force:
        value = _load(market)
        try:
            cache.set(key, value)
        except Exception:  # noqa: BLE001 - 写缓存失败不影响本次返回
            logger.debug("云图快照写缓存失败 key=%s", key)
        return value
    return cache.get_or_load(key, _ttl(), lambda: _load(market))


def _session_of(calendar: str, as_of: str) -> Tuple[str, str, bool]:
    """把项目统一的时段判定映射为 PRD §6.3 session 枚举。

    Args:
        calendar: a/hk/us。
        as_of: 数据时间文本。

    Returns:
        Tuple[str, str, bool]: (session, 中文状态, 是否盘中实时)。
    """
    try:
        dt = utils.parse_datetime(as_of)
    except Exception:  # noqa: BLE001 - 解析失败按未知处理，不伪造盘中
        dt = None
    text, realtime = utils.classify_market_status(dt, calendar)
    mapping = {
        "盘前": "preopen",
        "盘中": "open",
        "休市": "noon",
        "盘后": "closed",
        "收盘快照": "closed",
        "时间未知": "closed",
        "数据时间异常": "closed",
        "快照": "closed",
    }
    return mapping.get(text, "closed"), text, realtime


def _clean(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """输出前删除内部分组字段。"""
    out = []
    for it in items:
        row = {k: v for k, v in it.items() if not k.startswith("_")}
        out.append(row)
    return out


def _summary(items: Sequence[Dict[str, Any]], area_metric: str) -> Dict[str, Any]:
    """汇总统计（§7.1 summary）。

    成交额在市值口径下取各标的成交额之和；面积口径本身为成交额时同值。
    ``turnoverDiff`` 需要「昨日同期」数据，上游 clist 不提供，按项目红线
    输出 ``None``（前端显示 --），不用常量冒充。
    """
    up = flat = down = 0
    total_pct = 0.0
    sample = 0
    turnover = 0.0
    for it in items:
        pct = it.get("changePct")
        if pct is not None:
            sample += 1
            total_pct += pct
            if pct > 0.05:
                up += 1
            elif pct < -0.05:
                down += 1
            else:
                flat += 1
        if isinstance(it.get("turnover"), (int, float)):
            turnover += float(it["turnover"])
    return {
        "up": up,
        "flat": flat,
        "down": down,
        "avgPct": round(total_pct / sample, 2) if sample else None,
        "turnover": round(turnover, 2),
        "turnoverUnit": "亿元",
        "turnoverDiff": None,
        "turnoverDiffUnit": "亿元",
        "count": len(items),
    }


def overview(
    market: str,
    *,
    area_metric: Optional[str] = None,
    filters: Optional[str] = None,
    keyword: Optional[str] = None,
    view: Optional[str] = None,
    period: Optional[str] = None,
    direction: Optional[str] = None,
    pct_min: Optional[float] = None,
    pct_max: Optional[float] = None,
    sort_by: Optional[str] = None,
    force: bool = False,
    watchlist: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """组装 §7.1 概览响应（不含统一响应外壳）。

    Args:
        market: 市场键（调用方已校验）。
        area_metric: 面积口径；非法/不支持时回退市场默认。
        filters: 一级/二级分组编码，逗号分隔。
        keyword: 代码/名称模糊匹配（≤20 字）。
        view: A 股快捷视图。
        period: 涨跌周期；仅 day 有上游支撑。
        direction: all/up/down。
        pct_min: 涨跌幅下限。
        pct_max: 涨跌幅上限。
        sort_by: 排序维度（云图内弹窗/排行使用，概览仅回显）。
        force: 是否强制回源。
        watchlist: 自选键列表（``stock:600519`` 形式）。

    Returns:
        Dict[str, Any]: 符合 §7.1 字段全集的响应体。
    """
    spec = MARKET_SPECS[market]
    metric = area_metric if area_metric in spec["areas"] else spec["default_area"]
    snap = snapshot(market, force=force)
    items: List[Dict[str, Any]] = snap["items"]

    group_codes = [g for g in (filters or "").split(",") if g.strip()]
    if group_codes:
        known = {_code_for(market, l1) for l1 in {it["_l1"] for it in items}}
        known |= {it["_l1"] for it in items}
        dropped = [g for g in group_codes if g.split("/", 1)[0] not in known
                   and g not in known]
        if dropped:
            logger.info("云图 filters 忽略未知分组编码：%s", dropped)

    direction_value = direction if direction in ("all", "up", "down") else "all"
    period_value = period if period in PERIODS else "day"

    # 周期口径：把 changePct 换成对应周期的值（副本，不改缓存快照）
    period_key = {"day": "changePctDay", "week": "changePctWeek",
                  "month": "changePctMonth", "year": "changePctYear"}[period_value]
    if period_value != "day":
        items = []
        for raw in snap["items"]:
            clone = dict(raw)
            clone["changePct"] = (raw.get("extra") or {}).get(period_key)
            items.append(clone)

    view_value = view if view in VIEWS else "all"
    notice = None
    notes_extra = []
    members: Optional[frozenset] = None
    if market == "stock" and view_value in _UNSUPPORTED_VIEWS:
        notice = (
            f"「{_UNSUPPORTED_VIEWS[view_value]}」视图需自选数据接入，"
            "本轮按全市场返回，已在界面标注。"
        )
        view_value = "all"
    elif market == "stock" and view_value in ("hs300", "a500"):
        members = index_members(view_value)
        if not members:
            notice = (
                f"「{_UNSUPPORTED_VIEWS.get(view_value, view_value)}」成分名录暂不可用，"
                "本轮按全市场返回，已在界面标注。"
            )
            notes_extra.append(f"{view_value} 成分名录拉取失败")
            view_value = "all"

    picked = _filter_items(
        items, market=market, view=view_value, groups=group_codes,
        keyword=keyword or "", direction=direction_value,
        pct_min=pct_min, pct_max=pct_max, watchlist=watchlist,
        members=members,
    )
    groups = build_groups(picked, market, metric)
    for group in groups:
        for child in group["children"]:
            child["items"] = _clean(child["items"])

    session, session_text, realtime = _session_of(spec["calendar"], snap["as_of"])
    missing = snap.get("missing_pages") or 0
    notes = []
    if missing:
        notes.append(
            f"本次抓取 {snap['pages_ok']}/{snap['pages_expected']} 页成功，"
            f"缺失 {missing} 页，快照不完整，已标注延迟。"
        )
    if _MARKET_NOTES.get(market):
        notes.append(_MARKET_NOTES[market])
    if notice:
        notes.append(notice)
    notes.extend(notes_extra)

    return {
        "market": market,
        "marketLabel": spec["label"],
        "asOf": snap["as_of"],
        "session": session,
        "sessionText": session_text,
        "isRealtime": realtime,
        "stale": bool(missing),
        "source": "eastmoney_clist",
        "sourceNote": "；".join(notes) if notes else None,
        "areaMetric": metric,
        "areaUnit": spec["area_unit"],
        "legend": {
            "min": -round(spec["legend"] * _PERIOD_LEGEND_SCALE[period_value], 2),
            "max": round(spec["legend"] * _PERIOD_LEGEND_SCALE[period_value], 2),
            "upColor": "#f03a3f",
            "downColor": "#12b146",
            "period": period_value,
        },
        "summary": _summary(picked, metric),
        "groups": groups,
        "period": period_value,
        "view": view_value,
        "memberCount": len(members) if members else None,
        "sortBy": sort_by if sort_by in SORT_KEYS else "changePct",
        "availableMetrics": list(spec["areas"]),
        "total": snap["total"],
        "notice": notice,
    }


def filters_tree(market: str) -> Dict[str, Any]:
    """左侧筛选树（§7.2）。

    Args:
        market: 市场键（调用方已校验）。

    Returns:
        Dict[str, Any]: ``{market, areaMetric, areaUnit, nodes}``，节点结构为
        ``code/name/level/count/avgPct/totalScale/children``。
    """
    spec = MARKET_SPECS[market]
    metric = spec["default_area"]
    snap = snapshot(market)
    groups = build_groups(snap["items"], market, metric)
    nodes = []
    for group in groups:
        children = [
            {
                "code": f"{group['level1']}/{child['level2']}",
                "name": child["level2"],
                "level": 2,
                "count": len(child["items"]),
            }
            for child in group["children"]
        ]
        nodes.append({
            "code": group["level1Code"],
            "name": group["level1"],
            "level": 1,
            "count": group["count"],
            "avgPct": group["avgPct"],
            "totalScale": group["totalScale"],
            "children": children,
        })
    return {
        "market": market,
        "areaMetric": metric,
        "areaUnit": spec["area_unit"],
        "nodes": nodes,
    }


# =============================================================
# 六、单标的详情（§7.3 PopoverDTO）
# =============================================================
_RANKING_LIMIT = 50
_KLINE_BARS = 60


def _moving_average(values: Sequence[Optional[float]], window: int) -> List[Optional[float]]:
    """对齐收盘序列的简单移动平均，不足窗口的位置为 None。"""
    out: List[Optional[float]] = []
    for i in range(len(values)):
        if i + 1 < window:
            out.append(None)
            continue
        window_values = values[i + 1 - window:i + 1]
        if any(v is None for v in window_values):
            out.append(None)
            continue
        out.append(round(sum(v for v in window_values if v is not None) / window, 3))
    return out


def _kline_of(market: str, code: str) -> Optional[Dict[str, Any]]:
    """取近 60 个交易日日 K（仅 A 股已接入）。

    Args:
        market: 市场键。
        code: 标的代码。

    Returns:
        Optional[Dict[str, Any]]: §6.8 kline 结构；未接入或取数失败返回 None。
    """
    if market != "stock":
        return None
    try:
        from backend.app.datasource import ashare

        rows = ashare.get_kline(code, "daily", "qfq", _KLINE_BARS)
    except Exception as exc:  # noqa: BLE001 - K 线缺失不整体报错（§7.3）
        logger.warning("云图单标的 K 线取数失败 code=%s：%s", code, type(exc).__name__)
        return None
    if not rows:
        return None
    closes = [utils.to_float(r.get("close")) for r in rows]
    return {
        "dates": [r.get("date") for r in rows],
        "open": [utils.to_float(r.get("open")) for r in rows],
        "close": closes,
        "high": [utils.to_float(r.get("high")) for r in rows],
        "low": [utils.to_float(r.get("low")) for r in rows],
        "volume": [utils.to_float(r.get("volume")) for r in rows],
        "ma": {"ma5": _moving_average(closes, 5), "ma10": _moving_average(closes, 10)},
    }


def _rank_value(item: Dict[str, Any], key: str) -> Any:
    """排序取值：字段缺失时回退 changePct（§6.8 约束）。"""
    value = item.get(key)
    if value is None:
        value = item.get("changePct")
    return value


def item_detail(market: str, code: str, sort_by: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """单标的详情（§7.3）。

    Args:
        market: 市场键（调用方已校验）。
        code: 标的代码。
        sort_by: 排行维度；非法值回退 changePct。

    Returns:
        Optional[Dict[str, Any]]: PopoverDTO；代码不存在时返回 None。
    """
    snap = snapshot(market)
    target = next((x for x in snap["items"] if x["code"] == code), None)
    if target is None:
        return None

    peers = [x for x in snap["items"] if x["_l1"] == target["_l1"]]
    up, flat, down, avg = _group_stat(peers)
    key = sort_by if sort_by in SORT_KEYS else "changePct"

    if key == "name":
        ranked = sorted(peers, key=lambda x: x["name"])
    else:
        ranked = sorted(
            peers,
            key=lambda x: (x.get(key) is None, -(x.get(key) or 0)),
        )
    ranking = [
        {
            "code": x["code"],
            "name": x["name"],
            "price": x.get("price"),
            "changePct": x.get("changePct"),
            "changeAmplitude": x.get("amplitude"),
            "turnover": x.get("turnover"),
            "marketCapTotal": x.get("marketCapTotal"),
            "marketCapFloat": x.get("marketCapFloat"),
            "miniTrend": None,
        }
        for x in ranked[:_RANKING_LIMIT]
    ]

    notes = []
    if market != "stock":
        notes.append("该市场日 K 线尚未接入，K 线区按空态展示")
    notes.append("同分组迷你走势需逐票历史行情，本轮未接入，排行列表以「--」展示")

    return {
        "item": {k: v for k, v in target.items() if not k.startswith("_")},
        "groupPath": {"level1": target["_l1"], "level2": target["_l2"]},
        "groupStat": {"up": up, "flat": flat, "down": down, "avgPct": avg, "count": len(peers)},
        "kline": _kline_of(market, code),
        "ranking": ranking,
        "sortBy": key,
        "sourceNote": "；".join(notes),
        "asOf": snap["as_of"],
    }
