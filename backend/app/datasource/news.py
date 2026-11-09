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
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlencode

from backend.app import config
from backend.app.core import netfix
from backend.app.core.errors import NonRetryableError
from backend.app.core.source_prefs import ensure_enabled
from backend.app.core.v8_guard import guard_v8
netfix.apply_netfix()
import akshare as ak  # noqa: E402

from backend.app.datasource import utils  # noqa: E402
from backend.app.datasource.common import call_akshare as _common_call_akshare  # noqa: E402

logger = logging.getLogger(__name__)

# 板块树版本号：内置静态配置，变更需同步《接口说明书》与前端
BOARD_TREE_VERSION = "20261020"

# A股涨跌/停牌等无意义前缀，用于标题归一化去重
_TITLE_NOISE = re.compile(r"[【】\[\]（）()\s　:：,，。.、!！?？\"'“”‘’|/\\-]+")


# P1 收敛：_call 统一委托 common.call_akshare（原 news/overseas/sector/stock 四处同体副本）。
_call = _common_call_akshare

def _call_ths(func) -> Any:
    """同花顺/财联社 V8 类调用入口：进程级互斥 + 代理错误分类。

    这两个源的签名/解密走 py_mini_racer（内嵌 V8），多线程并发首次创建
    MiniRacer 上下文会触发 PartitionAlloc 重复初始化 FATAL、进程原生崩溃
    （实测并发必崩，详见 core/v8_guard.py 背景说明）。

    Args:
        func: 待执行的无参取数函数。

    Returns:
        Any: 结果原样透传（类型由 loader/func 约定）。
    """
    return _call(guard_v8(func))


def _text(value: Any) -> str:
    """安全转字符串并去首尾空白。

    Args:
        value: 待处理值（类型由调用方约定）。

    Returns:
        str: 去首尾空白后的字符串；None 返回空串。
    """
    return "" if value is None else str(value).strip()


def _clean_title(title: str, content: str = "") -> str:
    """标题为空时从正文【】前缀派生；仍为空则截取正文前 40 字。

    Args:
        title: 标题。
        content: 正文内容。

    Returns:
        str: 清洗后的标题（可能由正文派生或截取）。
    """
    if title:
        return title
    match = re.match(r"^【([^】]{4,60})】", content or "")
    if match:
        return match.group(1)
    return (content or "")[:40]


def normalize_key(title: str) -> str:
    """标题归一化：去括号/标点/空白并转小写，用于跨源去重。

    Args:
        title: 标题。

    Returns:
        str: 归一化后的标题键（全小写、无标点空白），供跨源去重。
    """
    return _TITLE_NOISE.sub("", (title or "").lower())


def make_id(source: str, title: str, time_text: str) -> str:
    """生成稳定 id（源+标题+时间），供前端 key 与去重使用。

    Args:
        source: 数据源标识（用于降级链溯源与徽章展示）。
        title: 标题。
        time_text: 时间文本。

    Returns:
        str: 稳定 id（同源同标题同时间恒定，可作前端 key）。
    """
    raw = f"{source}|{normalize_key(title)}|{time_text[:16]}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


# =============================================================
# 一、内置三市场板块树（一级 6 / 二级 15 / 末级 28）
# =============================================================
def _leaf(name: str, keywords: List[str], aliases: Optional[List[str]] = None,
          stocks: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
    """构造末级板块节点：关键词用于新闻命中，个股为代表性标的（非成分表）。

    Args:
        name: 名称。
        keywords: 关键词列表。
        aliases: 别名列表。
        stocks: 股票代码列表。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
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
            # 二级板块关键词（R2-03 衍生修复）：媒体标题常只写行业大类
            # （如「688012，半导体龙头…」），末级词「半导体设备/半导体材料」不出现，
            # 会导致明明属于该板块却一条都标不到。二级名命中时按 board_leaf_names
            # 同口径展开其全部末级，保证「大类 → 末级」标注闭环。
            {"name": "半导体", "keywords": ["半导体"], "children": [
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
    """遍历全部末级板块。

    Returns:
        Iterable[Dict[str, Any]]: 计算结果。
    """
    for level1 in BOARD_TREE:
        for level2 in level1["children"]:
            for leaf in level2["children"]:
                yield leaf


def sector_keywords() -> Dict[str, List[str]]:
    """返回 {末级板块名: 命中关键词} 映射，供情绪引擎标注板块。

    注意：只用 `keywords` 参与匹配，**不使用 aliases**——别名含「设备/材料/存储」这类
    超通用词，会把「显微镜设备」等无关新闻误判为板块命中（实测踩坑，2026-10-20）。

    Returns:
        dict: Dict[str, List[str]] 结构的结果。
    """
    mapping: Dict[str, List[str]] = {}
    for leaf in _iter_leaves():
        mapping[leaf["name"]] = [word for word in leaf["keywords"] if word]
    return mapping


def get_board_tree() -> Dict[str, Any]:
    """返回三市场板块树（含版本号与统计）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
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

    Args:
        name: 名称。

    Returns:
        list[str]: 字符串列表。
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
    """按末级/二级/一级名反查板块节点（含关键词），未命中返回 None。

    Args:
        name: 名称。

    Returns:
        dict | None: 命中时返回记录，缺失返回 None。
    """
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
    """构造统一新闻条目。

    Args:
        title: 标题。
        text: 待解析或清洗的文本。
        source: 数据源标识（用于降级链溯源与徽章展示）。
        time_text: 时间文本。
        url: 请求地址。
        channel: 通道/子来源名称。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
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
    """财联社 7×24 快讯（纯 HTTP，无需 V8 签名）。

    2026-11-02 第八轮：原实现走 akshare ``stock_info_global_cls``（hexin-v 签名需
    py_mini_racer），冻结形态禁用 V8 后该通道整体失效（降级点 G-6 的一部分）。
    实测财联社电报接口 ``/v1/roll/get_roll_list`` 使用 ``md5(sha1(sorted_params))``
    做签名，**纯 Python 即可计算**，故改为直连，并在 ``last_time`` 上翻页取更长列表
    （每条 20 条/页），同时解决「单次仅 20 条、无分页」的旧缺口。

    Args:
        limit: 返回条数上限（超出上限按上限截断）。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。

    Raises:
        NonRetryableError: 通道确定性失效（交由上层降级到东财/新浪快讯）。
    """
    ensure_enabled()
    wanted = max(1, int(limit or 50))
    items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    last_time = int(time.time())
    for _ in range(6):  # 最多 6 页 ≈ 120 条，足够覆盖列表页与情绪打分
        page = _cls_roll(last_time)
        if not page:
            break
        for row in page:
            title = _text(row.get("title"))
            content = _text(row.get("content")) or _text(row.get("brief"))
            if not (title or content):
                continue
            stamp = utils.to_int(row.get("ctime"))
            time_text = (
                utils.now_text(datetime.fromtimestamp(stamp, tz=timezone(timedelta(hours=8))))
                if stamp
                else ""
            )
            key = f"{title}|{time_text}"
            if key in seen:
                continue
            seen.add(key)
            items.append(
                _item(title, content, "财联社", time_text, row.get("shareurl") or "", "财联社快讯")
            )
        if len(items) >= wanted:
            break
        oldest = utils.to_int(page[-1].get("ctime"))
        if not oldest or oldest >= last_time:
            break
        last_time = oldest
    items.sort(key=lambda item: item["time"], reverse=True)
    if not items:
        raise NonRetryableError("财联社电报返回空数据")
    return items[:wanted]


def _cls_roll(last_time: int) -> List[Dict[str, Any]]:
    """请求财联社电报一页（``md5(sha1(urlencode(params)))`` 签名）。

    Args:
        last_time: 上一页最后一条的 ``ctime``（Unix 秒），首屏传当前时间。

    Returns:
        List[Dict[str, Any]]: ``roll_data`` 列表；结构异常时返回空列表。

    Raises:
        NonRetryableError: 网络确定性失效。
    """
    params: Dict[str, Any] = {
        "app": "CailianpressWeb",
        "category": "",
        "last_time": int(last_time),
        "os": "web",
        "refresh_type": "1",
        "rn": "20",
        "sv": "8.4.6",
    }
    params["sign"] = hashlib.md5(
        hashlib.sha1(urlencode(params).encode("utf-8")).hexdigest().encode("utf-8")
    ).hexdigest()
    session = netfix.make_session()
    try:
        response = session.get(
            config.CLS_ROLL_URL,
            params=params,
            headers={
                "User-Agent": config.BROWSER_USER_AGENT,
                "Referer": "https://www.cls.cn/telegraph",
            },
            timeout=config.DATASOURCE_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json() or {}
        return ((payload.get("data") or {}).get("roll_data") or [])
    except Exception as exc:  # noqa: BLE001 - 统一转译为可降级异常
        name = type(exc).__name__
        if name in ("ProxyError", "SSLError", "ConnectionError", "RemoteDisconnected"):
            raise NonRetryableError(f"财联社通道不可用：{name}") from exc
        raise
    finally:
        session.close()


def get_flash_multi(limit: int = 50) -> List[Dict[str, Any]]:
    """快讯多源取数：财联社优先，不可用时回落东财/新浪全球快讯。

    冻结环境（规范 §13.6-2）禁用 py_mini_racer 依赖源后，财联社通道不可用；
    此时自动改用纯 HTTP 的全球快讯，保证新闻与情绪视图仍有真实数据，
    口径在徽标中如实标注（不伪造“财联社”来源）。

    Args:
        limit: 返回条数上限（超出上限按上限截断）。

    Returns:
        list[dict]: 记录列表；全通道失败时抛出最后一个异常，由路由层转错误态。
    """
    try:
        items = get_flash(limit)
        if items:
            return items
    except Exception as exc:  # noqa: BLE001 - 主源不可用时降级到全球快讯
        logger.info("快讯主源不可用，降级全球快讯：%s", type(exc).__name__)
    return get_global(limit)


def get_stock_news(symbol: str, limit: int = 20) -> List[Dict[str, Any]]:
    """东财个股新闻（stock_news_em）。

    Args:
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。
        limit: 返回条数上限（超出上限按上限截断）。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。
    """
    ensure_enabled()
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
    """东财全球快讯（200 条，标题/摘要/发布时间/链接）。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。
    """
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
    """新浪全球快讯（20 条，仅 时间/内容，标题由【】前缀派生）。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。
    """
    frame = _call(lambda: ak.stock_info_global_sina())
    items: List[Dict[str, Any]] = []
    for row in frame.to_dict("records"):
        content = _text(row.get("内容"))
        if not content:
            continue
        items.append(_item("", content, "新浪财经", _text(row.get("时间")), "", "新浪全球"))
    return items


def _global_ths() -> List[Dict[str, Any]]:
    """同花顺全球快讯（20 条，标题/摘要/发布时间/链接）。

    2026-11-02 第八轮：原实现走 akshare ``stock_info_global_ths``（V8 签名），
    冻结形态不可用。改用同花顺资讯站公开接口
    ``news.10jqka.com.cn/tapp/news/push/stock/``（纯 HTTP，JSON，含 title/digest/url/ctime），
    保持「同花顺」这一真实来源不消失。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。

    Raises:
        NonRetryableError: 网络确定性失效。
    """
    url = "https://news.10jqka.com.cn/tapp/news/push/stock/"
    session = netfix.make_session()
    try:
        response = session.get(
            url,
            params={"page": 1, "tag": "", "track": "website", "pagesize": 50},
            headers={
                "User-Agent": config.BROWSER_USER_AGENT,
                "Referer": "https://news.10jqka.com.cn/",
            },
            timeout=config.DATASOURCE_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json() or {}
    except Exception as exc:  # noqa: BLE001 - 统一转译为可降级异常
        name = type(exc).__name__
        if name in ("ProxyError", "SSLError", "ConnectionError", "RemoteDisconnected"):
            raise NonRetryableError(f"同花顺快讯通道不可用：{name}") from exc
        raise
    finally:
        session.close()
    items: List[Dict[str, Any]] = []
    for row in ((payload.get("data") or {}).get("list") or []):
        title = _text(row.get("title"))
        digest = _text(row.get("digest"))
        if not (title or digest):
            continue
        stamp = utils.to_int(row.get("ctime"))
        time_text = (
            utils.now_text(datetime.fromtimestamp(stamp, tz=timezone(timedelta(hours=8))))
            if stamp
            else ""
        )
        items.append(
            _item(title, digest, "同花顺", time_text, row.get("url") or "", "同花顺全球")
        )
    return items


def get_global(limit: int = 100) -> List[Dict[str, Any]]:
    """全球财经快讯三源合并（东财 + 新浪 + 同花顺），失败源自动跳过。

    Args:
        limit: 返回条数上限（超出上限按上限截断）。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。

    Raises:
        ValueError: 参数不合法或上游数据不满足口径时抛出。
    """
    ensure_enabled()
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
    """文本命中的末级板块列表，可按 boards 入参过滤。

    Args:
        text: 待解析或清洗的文本。
        boards: 板块名列表（最多 6 个）。

    Returns:
        list[str]: 字符串列表。
    """
    body = text or ""
    hits: List[str] = []
    for leaf in _iter_leaves():
        if boards and leaf["name"] not in boards:
            continue
        if any(word and word in body for word in leaf["keywords"]):
            hits.append(leaf["name"])
    # 二级板块名整体命中：其全部末级一并标注（与 board_leaf_names 展开口径一致）。
    # 仅对显式声明了 keywords 的二级节点生效（当前为「半导体」），避免「银行/美股」
    # 这类通用词把无关新闻刷成多板块。
    for level1 in BOARD_TREE:
        for level2 in level1["children"]:
            words = level2.get("keywords") or []
            if not words or not any(word in body for word in words):
                continue
            for leaf in level2["children"]:
                if boards and leaf["name"] not in boards:
                    continue
                if leaf["name"] not in hits:
                    hits.append(leaf["name"])
    return hits


def build_pool(symbol: str = "") -> Dict[str, Any]:
    """多源新闻池唯一构建入口：财联社 + 东财/新浪/同花顺全球（+ 指定个股）。

    处理链：多源合并 → 标题归一化去重。**不**做板块过滤，供 ai-feed、
    板块检索（sector-search）与板块情绪（sentiment/board）共用同一池，
    确保「池内已标注某板块标签 → 板块检索必能回出这些条目」自洽。

    Args:
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）；为空则不并入个股新闻。

    Returns:
        dict: `{"items": 去重后的条目, "channels": 通道状态, "total_before_dedup": 合并条数}`。
    """
    pools: List[Dict[str, Any]] = []
    counter: Dict[str, Any] = {}

    def collect(name: str, loader) -> None:
        """采集单个源并记录条数，失败不阻断整体。

        Args:
            name: 通道名称（同时作为前端展示名）。
            loader: 该通道的取数函数。
        """
        try:
            rows = loader()
            pools.extend(rows)
            counter[name] = {"ok": True, "count": len(rows)}
        except Exception as exc:  # noqa: BLE001 - 单源失败必须降级而非整体失败
            counter[name] = {"ok": False, "count": 0, "error": type(exc).__name__}
            logger.info("新闻池源失败：%s（%s）", name, type(exc).__name__)

    collect("财联社快讯", lambda: get_flash(60))
    # 三个全球源各自只拉一次（避免按 channel 过滤触发三次全量拉取）
    collect("东财全球", _global_em)
    collect("新浪全球", _global_sina)
    collect("同花顺全球", _global_ths)
    if symbol:
        collect("东财个股", lambda: get_stock_news(symbol, 20))

    rows = dedup(pools)
    return {
        "items": rows,
        "channels": [{"name": name, **meta} for name, meta in counter.items()],
        "total_before_dedup": len(pools),
    }


def search_by_sector(sector: str, sort: str = "time", limit: int = 50) -> List[Dict[str, Any]]:
    """按板块过滤新闻池（与 ai-feed 同源同池、同一 match_boards 打标口径）。

    与 ai-feed 共用 build_pool 与 match_boards：只要池内某条已被标注该板块
    （含一级/二级板块展开到末级），本函数必能回出，消除两个视图互相矛盾。

    Args:
        sector: 板块名（末级/二级/一级均可）。
        sort: time=按时间倒序；impact=按情绪强度绝对值倒序（由路由层打分后重排）。
        limit: 返回条数。

    Returns:
        list[dict]: 记录列表；每条含 `board`（请求板块名）与 `boards`（真实命中末级板块）。
    """
    node = resolve_board(sector)
    node_name = (node or {}).get("name", sector)
    # 一级/二级板块需展开到末级再比对（match_boards 只输出末级标签）
    targets = set(board_leaf_names(node_name)) if node else {sector}
    matched: List[Dict[str, Any]] = []
    for item in build_pool()["items"]:
        body = f"{item.get('title', '')} {item.get('text', '')}"
        hits = match_boards(body)
        if not any(name in targets for name in hits):
            continue
        payload = dict(item)
        payload["board"] = node_name
        payload["boards"] = hits
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

    Args:
        boards: 板块名列表（最多 6 个）。
        limit: 返回条数上限（超出上限按上限截断）。
        symbol: 证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）。

    Returns:
        dict: `{items, channels, total_before_dedup, total_after_dedup}`。
    """
    pool = build_pool(symbol)
    rows = pool["items"]
    if boards:
        # R3-06 修复：match_boards 的 boards 参数是「末级白名单」，而调用方传入的
        # 可能是二级（半导体）或一级板块名，直接当白名单会筛掉全部末级 → 恒为空。
        # 这里与 search_by_sector 保持同一展开口径：先展开到末级再过滤。
        whitelist: set[str] = set()
        for name in boards:
            leaves = board_leaf_names(name)
            if leaves:
                whitelist.update(leaves)
            else:
                whitelist.add(name)
        filtered: List[Dict[str, Any]] = []
        for item in rows:
            hits = match_boards(f"{item.get('title', '')} {item.get('text', '')}", sorted(whitelist))
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
        "channels": pool["channels"],
        "total_before_dedup": pool["total_before_dedup"],
        "total_after_dedup": len(rows),
    }
