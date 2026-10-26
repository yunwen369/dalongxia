"""情绪规则引擎（确定性，不调用大模型）。

对应《工作规范》v7.2 §6.4 与基线03 §4.8（本地词典情绪引擎）、§4.10（news_review.py 初评引擎），
批次 5.2 要求：18 类事件 + 条件性属性矩阵 + 三维度权重 0.45/0.35/0.20，输出 ±0.1 三档口径。

设计原则（红线：严禁把 LLM 当数据源）：
1. 全部规则为**可回归的确定性规则**：同一文本必然得到同一分值，便于 pytest 断言；
2. 分值区间为 [-0.1, +0.1]：|score| ≥ 0.02 判利好/利空，否则中性；强度分强/中/弱三档；
3. 三维度：方向（0.45）+ 强度（0.35）+ 可信度（0.20），可信度由来源权重决定，
   不使用随机数、不使用时间衰减（避免同文不同分）。

本模块只做文本打分与聚合，不取数、不缓存。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# =============================================================
# 一、事件词典（18 类，方向 + 基础强度）
# =============================================================
EVENT_RULES: List[Dict[str, Any]] = [
    {"id": 1, "name": "业绩预增", "direction": 1, "strength": 0.85,
     "keywords": ["预增", "超预期", "扭亏", "业绩大增", "净利润增长", "同比增长", "创历史新高"]},
    {"id": 2, "name": "业绩下滑", "direction": -1, "strength": 0.85,
     "keywords": ["预亏", "首亏", "续亏", "业绩下滑", "净利润下降", "同比下降", "由盈转亏"]},
    {"id": 3, "name": "订单中标", "direction": 1, "strength": 0.6,
     "keywords": ["中标", "订单", "签约", "合同金额", "框架协议", "大额采购"]},
    {"id": 4, "name": "产能扩张", "direction": 1, "strength": 0.5,
     "keywords": ["扩产", "投产", "产能", "量产", "新产线", "产能释放"]},
    {"id": 5, "name": "产品涨价", "direction": 1, "strength": 0.7,
     "keywords": ["涨价", "提价", "价格上调", "供不应求", "缺货", "价格上调函"]},
    {"id": 6, "name": "价格战", "direction": -1, "strength": 0.7,
     "keywords": ["降价", "价格战", "跌价", "毛利率承压", "让利", "价格下调"]},
    {"id": 7, "name": "回购增持", "direction": 1, "strength": 0.6,
     "keywords": ["回购", "增持", "员工持股", "大股东增持"]},
    {"id": 8, "name": "减持解禁", "direction": -1, "strength": 0.6,
     "keywords": ["减持", "解禁", "清仓减持", "拟减持"]},
    {"id": 9, "name": "分红送转", "direction": 1, "strength": 0.4,
     "keywords": ["分红", "派息", "送转", "高送转", "现金分红"]},
    {"id": 10, "name": "再融资", "direction": -1, "strength": 0.3,
     "keywords": ["定增", "增发", "配股", "可转债", "再融资"]},
    {"id": 11, "name": "并购重组", "direction": 1, "strength": 0.5,
     "keywords": ["并购", "重组", "收购", "资产注入", "借壳"]},
    {"id": 12, "name": "股权激励", "direction": 1, "strength": 0.5,
     "keywords": ["股权激励", "激励计划", "期权激励", "限制性股票"]},
    {"id": 13, "name": "监管处罚", "direction": -1, "strength": 0.9,
     "keywords": ["处罚", "立案", "问询函", "警示函", "违规", "监管措施", "行政处罚"]},
    {"id": 14, "name": "诉讼仲裁", "direction": -1, "strength": 0.6,
     "keywords": ["诉讼", "仲裁", "败诉", "索赔", "执行裁定"]},
    {"id": 15, "name": "停产事故", "direction": -1, "strength": 0.8,
     "keywords": ["停产", "事故", "爆炸", "火灾", "召回", "安全事故"]},
    {"id": 16, "name": "政策支持", "direction": 1, "strength": 0.7,
     "keywords": ["补贴", "政策支持", "专项债", "减税", "国产替代", "扶持", "产业规划"]},
    {"id": 17, "name": "关税摩擦", "direction": -1, "strength": 0.8,
     "keywords": ["关税", "制裁", "出口管制", "实体清单", "贸易摩擦", "反倾销"]},
    {"id": 18, "name": "人事变动", "direction": 0, "strength": 0.3,
     "keywords": ["辞职", "离职", "董事长", "高管变动", "换届", "总裁变更"]},
]

# 条件性属性矩阵：命中「事件 + 条件词」时对方向分量做二次修正（确定性查表）
CONDITION_RULES: List[Dict[str, Any]] = [
    # 预期反转：幅度 >1，确保“预增但低于预期”由正转负、“下滑但好于预期”由负转正
    {"event": 1, "condition": ["低于预期", "不及预期", "下修"], "delta": -1.5},
    {"event": 2, "condition": ["好于预期", "超预期", "上修"], "delta": +1.5},
    {"event": 4, "condition": ["产能过剩", "过剩", "消化压力"], "delta": -0.5},
    {"event": 5, "condition": ["涨幅收窄", "已回落", "见顶"], "delta": -0.4},
    # 降价/价格战对下游受益方是利好（如“油价下调+航空公司成本压力缓解”）
    {"event": 6, "condition": ["成本压力缓解", "成本下降", "成本降低", "受益"], "delta": +1.5},
    {"event": 8, "condition": ["承诺不减持", "终止减持", "提前终止"], "delta": +1.5},
    {"event": 13, "condition": ["轻微", "已整改"], "delta": +0.2},
    {"event": 16, "condition": ["低于预期", "落空"], "delta": -0.4},
    {"event": 17, "condition": ["豁免", "缓和"], "delta": +0.5},
]

# 否定词：出现在事件关键词之前 10 个字符内时，反转该事件方向（如「终止回购」「取消订单」）
# 只收录多字否定词，避免单个「不」在「不超 2%」这类表述里误翻转
NEGATION_WORDS = ["终止", "取消", "撤回", "中止", "失败", "否认", "辟谣", "不予", "不再", "未实施"]

# 强度词：决定第二维度打分（出现越多强度越高）
INTENSITY_WORDS = ["大幅", "显著", "超", "创", "暴", "严重", "急剧", "全面", "首次", "历史新高", "翻倍"]

# 来源可信度权重（0–1）：官方/头部财经媒体权重高，自媒体与未知来源权重低
SOURCE_WEIGHTS: Dict[str, float] = {
    "财联社": 1.0,
    "证券时报网": 1.0,
    "上海证券报": 1.0,
    "中国证券报": 1.0,
    "新浪财经": 0.9,
    "东方财富": 0.9,
    "同花顺": 0.9,
    "央视新闻": 1.0,
    "CCTV": 1.0,
}
DEFAULT_SOURCE_WEIGHT = 0.75

# 三维度权重（冻结口径，改动须走 §15.1 数据口径变更流程）
W_DIRECTION = 0.45
W_STRENGTH = 0.35
W_CREDIBILITY = 0.20

# 分值边界：±0.02 为利好/利空与中性的分界；±0.06 为强/中分界
BOUND_NEUTRAL = 0.02
BOUND_STRONG = 0.06


def _clamp(value: float, low: float, high: float) -> float:
    """把数值限制在 [low, high] 区间。

    Args:
        value: 待处理数值。
        low: 最低值。
        high: 最高值。

    Returns:
        float: 数值结果。
    """
    return max(low, min(high, value))


def _match_events(text: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """匹配事件与条件修正。

    Args:
        text: 新闻标题 + 正文拼接后的文本。

    Returns:
        tuple: (命中事件列表, 命中条件列表)，每个事件含 id/name/direction/strength/hits。
    """
    matched: List[Dict[str, Any]] = []
    for rule in EVENT_RULES:
        hits = [word for word in rule["keywords"] if word in text]
        if not hits:
            continue
        # 否定检测：关键词前 10 个字符内出现否定词则反转方向（确定性，不做语义推断）
        direction = rule["direction"]
        negated = False
        for word in hits:
            position = text.find(word)
            if position < 0:
                continue
            # 中文否定既可能前置（"终止回购"）也可能后置（"重组终止"），两侧都给窗口
            before = text[max(0, position - 10):position]
            after = text[position + len(word): position + len(word) + 4]
            if any(negative in before or negative in after for negative in NEGATION_WORDS):
                negated = True
                break
        if negated:
            direction = -direction
        matched.append(
            {
                "id": rule["id"],
                "name": rule["name"],
                "direction": direction,
                "strength": rule["strength"],
                "hits": hits,
                "negated": negated,
            }
        )

    conditions: List[Dict[str, Any]] = []
    for rule in CONDITION_RULES:
        # 条件只在对应事件命中时生效，避免"终止"等词脱离语境误判
        if not any(item["id"] == rule["event"] for item in matched):
            continue
        hits = [word for word in rule["condition"] if word in text]
        if hits:
            conditions.append({"event": rule["event"], "delta": rule["delta"], "hits": hits})
    return matched, conditions


def _source_weight(source: str) -> float:
    """按来源返回可信度权重；未登记来源取 0.75。

    Args:
        source: 数据源标识（用于降级链溯源与徽章展示）。

    Returns:
        float: 数值结果。
    """
    if not source:
        return DEFAULT_SOURCE_WEIGHT
    for name, weight in SOURCE_WEIGHTS.items():
        if name in source:
            return weight
    return DEFAULT_SOURCE_WEIGHT


def analyze(text: str, *, source: str = "", sectors: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
    """对单条文本做确定性情绪打分。

    Args:
        text: 标题 + 正文（可为空，空文本返回中性 0 分）。
        source: 来源名称，用于可信度维度。
        sectors: 可选 {板块名: 关键词列表} 映射，用于命中板块。

    Returns:
        dict: {score(-0.1~0.1), label(利好/利空/中性), strength(强/中/弱),
               direction/strength_component/credibility, events[], conditions[], sectors[]}
    """
    body = (text or "").strip()
    if not body:
        return {
            "score": 0.0, "label": "中性", "strength": "弱",
            "direction": 0.0, "strength_component": 0.0, "credibility": _source_weight(source),
            "events": [], "conditions": [], "sectors": [],
        }

    matched, conditions = _match_events(body)

    # 维度一：方向 = 各事件「方向×强度」的加权均值，再叠加条件修正
    if matched:
        total_weight = sum(item["strength"] for item in matched) or 1.0
        direction = sum(item["direction"] * item["strength"] for item in matched) / total_weight
    else:
        direction = 0.0
    for item in conditions:
        direction += item["delta"]
    direction = _clamp(direction, -1.0, 1.0)

    # 维度二：强度 = 强度词命中数与事件数归一，符号跟随方向
    intensity_hits = sum(1 for word in INTENSITY_WORDS if word in body)
    intensity_ratio = _clamp((intensity_hits + len(matched)) / 3.0, 0.0, 1.0)
    sign = 1.0 if direction > 0 else (-1.0 if direction < 0 else 0.0)
    strength_component = sign * intensity_ratio

    # 维度三：可信度 = 来源权重，符号跟随方向
    credibility = _source_weight(source)
    credibility_component = sign * credibility

    raw = (
        W_DIRECTION * direction
        + W_STRENGTH * strength_component
        + W_CREDIBILITY * credibility_component
    )
    score = round(_clamp(raw, -1.0, 1.0) * 0.1, 4)

    if score >= BOUND_NEUTRAL:
        label = "利好"
    elif score <= -BOUND_NEUTRAL:
        label = "利空"
    else:
        label = "中性"
    if abs(score) >= BOUND_STRONG:
        strength_label = "强"
    elif abs(score) >= 0.03:
        strength_label = "中"
    else:
        strength_label = "弱"

    sector_hits: List[str] = []
    if sectors:
        for board, keywords in sectors.items():
            if any(word in body for word in keywords):
                sector_hits.append(board)

    return {
        "score": score,
        "label": label,
        "strength": strength_label,
        "direction": round(direction, 4),
        "strength_component": round(strength_component, 4),
        "credibility": credibility,
        "events": matched,
        "conditions": conditions,
        "sectors": sector_hits,
    }


def analyze_items(items: Iterable[Dict[str, Any]], *, sectors: Optional[Dict[str, List[str]]] = None) -> List[Dict[str, Any]]:
    """批量打分：为每条新闻附加 sentiment 字段。

    Args:
        items: 新闻条目列表，需含 title 与 text（或 content）字段。
        sectors: 板块关键词映射。

    Returns:
        List[Dict[str, Any]]: 原条目 + sentiment 子结构。
    """
    result: List[Dict[str, Any]] = []
    for item in items:
        text = f"{item.get('title', '')} {item.get('text', '') or item.get('content', '')}"
        payload = dict(item)
        payload["sentiment"] = analyze(text, source=str(item.get("source") or ""), sectors=sectors)
        result.append(payload)
    return result


def aggregate(items: Iterable[Dict[str, Any]], *, scope: str = "") -> Dict[str, Any]:
    """聚合情绪：多空条数、均分、温度（0–100）与分档。

    温度口径（确定性）：temperature = 50 + mean(score) * 500，裁剪到 0–100。
    即均分 +0.1 → 100（极热），-0.1 → 0（极冷），0 → 50（中性）。

    Args:
        items: 已带 sentiment 的新闻条目。
        scope: 作用域标识（stock/market/board 名称），仅回显。

    Returns:
        dict: {scope,total,positive,negative,neutral,mean_score,temperature,level,top_events}
    """
    rows = list(items)
    scores = [float((item.get("sentiment") or {}).get("score") or 0.0) for item in rows]
    positive = sum(1 for item in rows if (item.get("sentiment") or {}).get("label") == "利好")
    negative = sum(1 for item in rows if (item.get("sentiment") or {}).get("label") == "利空")
    neutral = len(rows) - positive - negative
    mean_score = round(sum(scores) / len(scores), 4) if scores else 0.0
    temperature = round(_clamp(50 + mean_score * 500, 0, 100), 1)

    if temperature >= 65:
        level = "偏热"
    elif temperature <= 35:
        level = "偏冷"
    else:
        level = "中性"

    # Top 事件：按出现次数排序，取前 5，便于前端展示驱动因素
    counter: Dict[str, int] = {}
    for item in rows:
        for event in (item.get("sentiment") or {}).get("events") or []:
            counter[event["name"]] = counter.get(event["name"], 0) + 1
    top_events = [
        {"name": name, "count": count}
        for name, count in sorted(counter.items(), key=lambda pair: pair[1], reverse=True)[:5]
    ]

    return {
        "scope": scope,
        "total": len(rows),
        "positive": positive,
        "negative": negative,
        "neutral": neutral,
        "mean_score": mean_score,
        "temperature": temperature,
        "level": level,
        "top_events": top_events,
    }
