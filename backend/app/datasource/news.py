"""新闻 provider：快讯 / 个股 / 全球 / 板块检索 / 板块树 / 五源聚合。

对应规范 v7.2 §8.6 与基线03 §3.6、§4.7、§4.9：
- 财联社快讯：`stock_info_global_cls(symbol="全部")`（实测 20 条，字段 标题/内容/发布日期/发布时间）
- 东财全球：`stock_info_global_em()`（实测 200 条，标题/摘要/发布时间/链接）
- 新浪全球：`stock_info_global_sina()`（实测 20 条，仅 时间/内容，标题由正文【】前缀派生）
- 同花顺全球：`stock_info_global_ths()`（实测 20 条，标题/内容/发布时间/链接）
- 东财个股：`stock_news_em(symbol)`（实测 10 条/只）

本模块只做取数、归一化、去重、板块命中与排序；情绪打分在 datasource/sentiment.py。
所有接口名以 akshare 1.19.1 本机实测为准，无源能力时如实返回空，不编造。
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Dict, Iterable, List, Optional

from backend.app import config
from backend.app.core import netfix
from backend.app.core.errors import NonRetryableError
from backend.app.datasource import utils

netfix.apply_netfix()
import akshare as ak  # noqa: E402

logger = logging.getLogger(__name__)

# 板块树版本号：内置静态配置，变更需同步《接口说明书》与前端
BOARD_TREE_VERSION = "20261020"

# A股涨跌/停牌等无意义前缀，用于标题归一化去重
_TITLE_NOISE = re.compile(r"[【】\[\]（）()\s　:：,，。.、!！?？\"'“”‘’|/\\-]+")


def _call(func):
    """统一包装 akshare 调用：确定性网络失效直接降级。"""
    try:
        return func()
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        if name in ("ProxyError", "SSLError", "ConnectionError", "RemoteDisconnected"):
            raise NonRetryableError(f"上游通道不可用：{name}") from exc
        raise


def _ensure_enabled(source: str = "akshare") -> None:
    """检查数据源开关。"""
    if not config.PROVIDER_SWITCHES.get(source, True):
        raise NonRetryableError(f"{source} 源已被环境变量禁用")


def _text(value: Any) -> str:
    """安全转字符串并去首尾空白。"""
    return "" if value is None else str(value).strip()


def _clean_title(title: str, content: str = "") -> str:
    """标题为空时从正文【】前缀派生；仍为空则截取正文前 40 字。"""
    if title:
        return title
    match = re.match(r"^【([^】]{4,60})】", content or "")
    if match:
        return match.group(1)
    return (content or "")[:40]


def normalize_key(title: str) -> str:
    """标题归一化：去括号/标点/空白并转小写，用于跨源去重。"""
    return _TITLE_NOISE.sub("", (title or "").lower())


def make_id(source: str, title: str, time_text: str) -> str:
    """生成稳定 id（源+标题+时间），供前端 key 与去重使用。"""
    raw = f"{source}|{normalize_key(title)}|{time_text[:16]}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


# =============================================================
# 一、内置三市场板块树（一级 6 / 二级 15 / 末级 28）
# =============================================================
def _leaf(name: str, keywords: List[str], aliases: Optional[List[str]] = None,
          stocks: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
    """构造末级板块节点：关键词用于新闻命中，个股为代表性标的（非成分表）。"""
    return {
        "name": name,
        "keywords": keywords,
        "aliases": aliases or [],
        "stocks": stocks or {"A": [], "HK": [], "US": []},
    }


BOARD_TREE: List[Dict[str, Any]] = [
    {
        "name": "科技成长",
        "children": [
            {"name": "半导体", "children": [
                _leaf("半导体设备", ["半导体设备", "刻蚀", "薄膜沉积", "光刻", "清洗设备"], ["设备"],
                      {"A": ["688012", "002371"], "HK": [], "US": ["AMAT", "LRCX"]}),
                _leaf("半导体材料", ["半导体材料", "硅片", "光刻胶", "电子特气", "靶材"], ["材料"],
                      {"A": ["688126", "300346"], "HK": [], "US": []}),
                _leaf("芯片设计", ["芯片设计", "SoC", "MCU", "模拟芯片", "GPU"], ["设计"],
                      {"A": ["688256", "603986"], "HK": [], "US": ["NVDA", "AMD"]}),
                _leaf("存储芯片", ["存储芯片", "DRAM", "NAND", "HBM"], ["存储"],
                      {"A": ["688008", "603501"], "HK": [], "US": ["MU"]}),
            ]},
            {"name": "算力", "children": [
                _leaf("算力租赁", ["算力租赁", "IDC", "数据中心", "智算中心"], ["算力"],
                      {"A": ["300383", "603019"], "HK": [], "US": []}),
                _leaf("AI服务器", ["AI服务器", "服务器", "液冷", "交换机"], ["服务器"],
                      {"A": ["000977", "601138"], "HK": [], "US": ["SMCI", "DELL"]}),
            ]},
            {"name": "光通信", "children": [
                _leaf("光模块", ["光模块", "800G", "1.6T", "硅光"], ["光模块"],
                      {"A": ["300308", "300502"], "HK": [], "US": []}),
                _leaf("CPO", ["CPO", "共封装", "光电共封装"], ["CPO"],
                      {"A": ["300502", "002281"], "HK": [], "US": ["AVGO"]}),
            ]},
        ],
    },
    {
        "name": "消费医药",
        "children": [
            {"name": "食品饮料", "children": [
                _leaf("白酒", ["白酒", "酱酒", "酒企"], ["白酒"], {"A": ["600519", "000858"], "HK": [], "US": []}),
                _leaf("乳制品", ["乳制品", "原奶", "奶粉"], ["乳业"], {"A": ["600887", "002329"], "HK": [], "US": []}),
            ]},
            {"name": "医药生物", "children": [
                _leaf("创新药", ["创新药", "新药", "临床", "获批上市"], ["创新药"], {"A": ["600276", "688180"], "HK": [], "US": []}),
                _leaf("医疗器械", ["医疗器械", "影像设备", "耗材", "集采"], ["器械"], {"A": ["300760", "688271"], "HK": [], "US": []}),
            ]},
            {"name": "农业", "children": [
                _leaf("种植养殖", ["种植", "养殖", "生猪", "粮食", "饲料"], ["农业"], {"A": ["000876", "300498"], "HK": [], "US": []}),
            ]},
        ],
    },
    {
        "name": "高端制造",
        "children": [
            {"name": "新能源", "children": [
                _leaf("光伏", ["光伏", "组件", "硅料", "逆变器"], ["光伏"], {"A": ["601012", "300274"], "HK": [], "US": ["FSLR"]}),
                _leaf("风电", ["风电", "风机", "海风", "塔筒"], ["风电"], {"A": ["002202", "601615"], "HK": [], "US": []}),
                _leaf("储能", ["储能", "电化学储能", "储能电池"], ["储能"], {"A": ["300750", "688063"], "HK": [], "US": []}),
                _leaf("锂电池", ["锂电池", "动力电池", "正极", "负极", "隔膜"], ["锂电"], {"A": ["300750", "002460"], "HK": [], "US": ["TSLA"]}),
            ]},
            {"name": "机械设备", "children": [
                _leaf("工程机械", ["工程机械", "挖掘机", "起重机"], ["工程机械"], {"A": ["600031", "000425"], "HK": [], "US": ["CAT"]}),
                _leaf("工业母机", ["工业母机", "机床", "数控"], ["机床"], {"A": ["600837", "002527"], "HK": [], "US": []}),
            ]},
            {"name": "军工", "children": [
                _leaf("军工电子", ["军工", "国防", "军品", "航空发动机"], ["军工"], {"A": ["600760", "000768"], "HK": [], "US": []}),
            ]},
        ],
    },
    {
        "name": "周期资源",
        "children": [
            {"name": "有色金属", "children": [
                _leaf("铜", ["铜价", "电解铜", "铜矿"], ["铜"], {"A": ["601899", "600362"], "HK": [], "US": ["FCX"]}),
                _leaf("黄金", ["黄金", "金价", "贵金属"], ["黄金"], {"A": ["600547", "601899"], "HK": [], "US": ["GLD"]}),
                _leaf("稀土", ["稀土", "永磁", "镨钕"], ["稀土"], {"A": ["600111", "000831"], "HK": [], "US": []}),
            ]},
            {"name": "能源化工", "children": [
                _leaf("石油石化", ["原油", "油价", "石油", "炼化", "天然气"], ["石化"], {"A": ["601857", "600028"], "HK": [], "US": ["XOM"]}),
            ]},
        ],
    },
    {
        "name": "金融地产",
        "children": [
            {"name": "银行", "children": [
                _leaf("银行", ["银行", "净息差", "存款", "信贷"], ["银行"], {"A": ["600036", "601166"], "HK": [], "US": []}),
            ]},
            {"name": "非银金融", "children": [
                _leaf("券商", ["券商", "证券", "两融", "投行"], ["券商"], {"A": ["600030", "601211"], "HK": [], "US": []}),
            ]},
        ],
    },
    {
        "name": "境外市场",
        "children": [
            {"name": "港股", "children": [
                _leaf("恒生科技", ["恒生科技", "港股科技", "南向资金"], ["港股"], {"A": [], "HK": ["00700", "09988"], "US": []}),
            ]},
            {"name": "美股", "children": [
                _leaf("美股半导体", ["费城半导体", "美股半导体", "SOX"], ["美股芯片"], {"A": [], "HK": [], "US": ["NVDA", "AMD", "MU"]}),
            ]},
        ],
    },
]


def _iter_leaves() -> Iterable[Dict[str, Any]]:
    """遍历全部末级板块。"""
    for level1 in BOARD_TREE:
        for level2 in level1["children"]:
            for leaf in level2["children"]:
                yield leaf


def sector_keywords() -> Dict[str, List[str]]:
    """返回 {末级板块名: 命中关键词} 映射，供情绪引擎标注板块。

    注意：只用 `keywords` 参与匹配，**不使用 aliases**——别名含「设备/材料/存储」这类
    超通用词，会把「显微镜设备」等无关新闻误判为板块命中（实测踩坑，2026-10-20）。
    """
    mapping: Dict[str, List[str]] = {}
    for leaf in _iter_leaves():
        mapping[leaf["name"]] = [word for word in leaf["keywords"] if word]
    return mapping


def get_board_tree() -> Dict[str, Any]:
    """返回三市场板块树（含版本号与统计）。"""
    level1_count = len(BOARD_TREE)
    level2_count = sum(len(item["children"]) for item in BOARD_TREE)
    leaf_count = sum(len(level2["children"]) for item in BOARD_TREE for level2 in item["children"])
    tree: List[Dict[str, Any]] = []
    for level1 in BOARD_TREE:
        node1 = {"id": level1["name"], "name": level1["name"], "level": 1, "children": []}
        for level2 in level1["children"]:
            node2 = {
                "id": f"{level1['name']}/{level2['name']}",
                "name": level2["name"],
                "level": 2,
                "children": [],
            }
            for leaf in level2["children"]:
                node2["children"].append(
                    {
                        "id": f"{level1['name']}/{level2['name']}/{leaf['name']}",
                        "name": leaf["name"],
                        "level": 3,
                        "keywords": leaf["keywords"],
                        "aliases": leaf["aliases"],
                        "stocks": leaf["stocks"],
                    }
                )
            node1["children"].append(node2)
        tree.append(node1)
    return {
        "version": BOARD_TREE_VERSION,
        "counts": {"level1": level1_count, "level2": level2_count, "leaf": leaf_count},
        "markets": ["A", "HK", "US"],
        "tree": tree,
    }


def board_leaf_names(name: str) -> List[str]:
    """返回某板块下辖的全部末级板块名。

    一级/二级板块本身不是命中单位（新闻命中只标末级），因此按一/二级筛选时
    必须展开为其末级子孙，否则过滤结果恒为空。
    """
    if not name:
        return []
    for level1 in BOARD_TREE:
        if level1["name"] == name:
            return [leaf["name"] for level2 in level1["children"] for leaf in level2["children"]]
        for level2 in level1["children"]:
            if level2["name"] == name:
                return [leaf["name"] for leaf in level2["children"]]
            for leaf in level2["children"]:
                if leaf["name"] == name:
                    return [leaf["name"]]
    return []


def resolve_board(name: str) -> Optional[Dict[str, Any]]:
    """按末级/二级/一级名反查板块节点（含关键词），未命中返回 None。"""
    if not name:
        return None
    for level1 in BOARD_TREE:
        if level1["name"] == name:
            words: List[str] = []
            for level2 in level1["children"]:
                for leaf in level2["children"]:
                    words.extend(leaf["keywords"])
            return {"name": name, "level": 1, "keywords": words}
        for level2 in level1["children"]:
            if level2["name"] == name:
                words = []
                for leaf in level2["children"]:
                    words.extend(leaf["keywords"])
                return {"name": name, "level": 2, "keywords": words}
            for leaf in level2["children"]:
                if leaf["name"] == name:
                    return {
                        "name": name,
                        "level": 3,
                        # 匹配只用 keywords，别名仅供展示（避免超通用词误命中）
                        "keywords": list(leaf["keywords"]),
                        "aliases": list(leaf["aliases"]),
                        "parent": level2["name"],
                        "level1": level1["name"],
                    }
    return None


# =============================================================
# 二、四个新闻源取数（统一结构）
# =============================================================
def _item(title: str, text: str, source: str, time_text: str, url: str, channel: str) -> Dict[str, Any]:
    """构造统一新闻条目。"""
    clean_title = _clean_title(title, text)
    return {
        "id": make_id(channel, clean_title, time_text),
        "title": clean_title,
        "text": (text or "")[:600],
        "source": source,
        "time": time_text,
        "url": url,
        "channel": channel,
    }


def get_flash(limit: int = 50) -> List[Dict[str, Any]]:
    """财联社 7×24 快讯（字段：标题/内容/发布日期/发布时间）。"""
    _ensure_enabled()
    frame = _call(lambda: ak.stock_info_global_cls(symbol="全部"))
    records = frame.to_dict("records")
    items: List[Dict[str, Any]] = []
    for row in records:
        title = _text(row.get("标题"))
        content = _text(row.get("内容"))
        time_text = f"{_text(row.get('发布日期'))} {_text(row.get('发布时间'))}".strip()
        if not (title or content):
            continue
        items.append(_item(title, content, "财联社", time_text, "", "财联社快讯"))
    items.sort(key=lambda item: item["time"], reverse=True)
    return items[: max(1, limit)]


def get_stock_news(symbol: str, limit: int = 20) -> List[Dict[str, Any]]:
    """东财个股新闻（stock_news_em）。"""
    _ensure_enabled()
    frame = _call(lambda: ak.stock_news_em(symbol=str(symbol)))
    items: List[Dict[str, Any]] = []
    for row in frame.to_dict("records"):
        title = _text(row.get("新闻标题"))
        content = _text(row.get("新闻内容"))
        if not (title or content):
            continue
        items.append(
            _item(title, content, _text(row.get("文章来源")) or "东方财富",
                  _text(row.get("发布时间")), _text(row.get("新闻链接")), "东财个股")
        )
    return items[: max(1, limit)]


def _global_em() -> List[Dict[str, Any]]:
    """东财全球快讯（200 条，标题/摘要/发布时间/链接）。"""
    frame = _call(lambda: ak.stock_info_global_em())
    items: List[Dict[str, Any]] = []
    for row in frame.to_dict("records"):
        title = _text(row.get("标题"))
        summary = _text(row.get("摘要"))
        if not (title or summary):
            continue
        items.append(_item(title, summary, "东方财富", _text(row.get("发布时间")),
                           _text(row.get("链接")), "东财全球"))
    return items


def _global_sina() -> List[Dict[str, Any]]:
    """新浪全球快讯（20 条，仅 时间/内容，标题由【】前缀派生）。"""
    frame = _call(lambda: ak.stock_info_global_sina())
    items: List[Dict[str, Any]] = []
    for row in frame.to_dict("records"):
        content = _text(row.get("内容"))
        if not content:
            continue
        items.append(_item("", content, "新浪财经", _text(row.get("时间")), "", "新浪全球"))
    return items


def _global_ths() -> List[Dict[str, Any]]:
    """同花顺全球快讯（20 条，标题/内容/发布时间/链接）。"""
    frame = _call(lambda: ak.stock_info_global_ths())
    items: List[Dict[str, Any]] = []
    for row in frame.to_dict("records"):
        title = _text(row.get("标题"))
        content = _text(row.get("内容"))
        if not (title or content):
            continue
        items.append(_item(title, content, "同花顺", _text(row.get("发布时间")),
                           _text(row.get("链接")), "同花顺全球"))
    return items


def get_global(limit: int = 100) -> List[Dict[str, Any]]:
    """全球财经快讯三源合并（东财 + 新浪 + 同花顺），失败源自动跳过。"""
    _ensure_enabled()
    items: List[Dict[str, Any]] = []
    errors: List[str] = []
    for name, loader in (("东财", _global_em), ("新浪", _global_sina), ("同花顺", _global_ths)):
        try:
            items.extend(loader())
        except Exception as exc:  # noqa: BLE001 - 单源失败不影响合并
            errors.append(f"{name}:{type(exc).__name__}")
    if not items:
        raise ValueError("全球快讯三源均返回空：" + "，".join(errors))
    logger.info("全球快讯合并：%d 条（失败源：%s）", len(items), "，".join(errors) or "无")
    items.sort(key=lambda item: item["time"], reverse=True)
    return items[: max(1, limit)]


# =============================================================
# 三、去重 / 板块命中 / 排序
# =============================================================
def dedup(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按标题归一化去重，保留首次出现并把重复来源并入 channels。

    Args:
        items: 已归一化的新闻条目（含 channel 字段）。

    Returns:
        List[Dict[str, Any]]: 去重后的条目，新增 channels[]。
    """
    seen: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    # 二级键：标题前 24 字。跨源转载常出现「【xx】标题」与「标题」两种形态，
    # 归一化后仍可能因前后缀差异漏判，故用前缀做近似去重（同源不合并，避免误杀）。
    prefix_map: Dict[str, str] = {}
    for item in items:
        key = normalize_key(item.get("title") or "")
        if not key:
            key = item.get("id") or ""
        channel = item.get("channel") or ""
        prefix = key[:24] if len(key) >= 12 else ""
        matched_key = key if key in seen else prefix_map.get(prefix or "-")
        if matched_key and matched_key in seen:
            channels = seen[matched_key].setdefault("channels", [])
            if channel and channel not in channels:
                channels.append(channel)
            continue
        payload = dict(item)
        payload["channels"] = [channel] if channel else []
        seen[key] = payload
        order.append(key)
        if prefix:
            prefix_map.setdefault(prefix, key)
    result = [seen[key] for key in order]
    # 常规通道去重后按时间倒序
    result.sort(key=lambda item: item.get("time") or "", reverse=True)
    return result


def match_boards(text: str, boards: Optional[List[str]] = None) -> List[str]:
    """文本命中的末级板块列表，可按 boards 入参过滤。"""
    body = text or ""
    hits: List[str] = []
    for leaf in _iter_leaves():
        if boards and leaf["name"] not in boards:
            continue
        if any(word and word in body for word in leaf["keywords"]):
            hits.append(leaf["name"])
    return hits


def search_by_sector(sector: str, sort: str = "time", limit: int = 50) -> List[Dict[str, Any]]:
    """按板块过滤快讯池（财联社 + 三源全球），并做去重与排序。

    Args:
        sector: 板块名（末级/二级/一级均可）。
        sort: time=按时间倒序；impact=按情绪强度绝对值倒序（由路由层打分后重排）。
        limit: 返回条数。
    """
    node = resolve_board(sector)
    keywords = (node or {}).get("keywords") or ([sector] if sector else [])
    pool = get_flash(60) + get_global(120)
    rows = dedup(pool)
    matched: List[Dict[str, Any]] = []
    for item in rows:
        body = f"{item.get('title', '')} {item.get('text', '')}"
        if any(word and word in body for word in keywords):
            payload = dict(item)
            payload["board"] = (node or {}).get("name", sector)
            matched.append(payload)
    if sort == "time":
        matched.sort(key=lambda item: item.get("time") or "", reverse=True)
    return matched[: max(1, limit)]


def build_ai_feed(
    *,
    boards: Optional[List[str]] = None,
    limit: int = 60,
    symbol: str = "",
) -> Dict[str, Any]:
    """五源新闻池：财联社 + 东财全球 + 新浪 + 同花顺（+ 东财个股，指定 symbol 时并入）。

    处理链：合并 → 标题去重 → 板块命中过滤 → 交由路由层做本地初评与排序。
    """
    pools: List[Dict[str, Any]] = []
    channels: List[Dict[str, Any]] = []

    def collect(name: str, loader, counter: Dict[str, Any]) -> None:
        """采集单个源并记录条数，失败不阻断整体。"""
        try:
            rows = loader()
            pools.extend(rows)
            counter[name] = {"ok": True, "count": len(rows)}
        except Exception as exc:  # noqa: BLE001
            counter[name] = {"ok": False, "count": 0, "error": type(exc).__name__}
            logger.info("ai-feed 源失败：%s（%s）", name, type(exc).__name__)

    counter: Dict[str, Any] = {}
    collect("财联社快讯", lambda: get_flash(60), counter)
    # 三个全球源各自只拉一次（避免按 channel 过滤触发三次全量拉取）
    collect("东财全球", _global_em, counter)
    collect("新浪全球", _global_sina, counter)
    collect("同花顺全球", _global_ths, counter)
    if symbol:
        collect("东财个股", lambda: get_stock_news(symbol, 20), counter)
    channels = [{"name": name, **meta} for name, meta in counter.items()]

    rows = dedup(pools)
    if boards:
        filtered: List[Dict[str, Any]] = []
        for item in rows:
            hits = match_boards(f"{item.get('title', '')} {item.get('text', '')}", boards)
            if hits:
                payload = dict(item)
                payload["boards"] = hits
                filtered.append(payload)
        rows = filtered
    else:
        for item in rows:
            item["boards"] = match_boards(f"{item.get('title', '')} {item.get('text', '')}")

    return {
        "items": rows[: max(1, limit)],
        "channels": channels,
        "total_before_dedup": len(pools),
        "total_after_dedup": len(rows),
    }
