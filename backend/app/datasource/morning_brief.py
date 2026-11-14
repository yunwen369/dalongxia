# -*- coding: utf-8 -*-
"""盘前/盘后定时研判落库（第十轮 P3 · 设计基准 §10-P3「盘前 08:30/盘后 15:30 研判落库」）。

口径
----
- **确定性规则聚合**：不调用大模型、不使用随机数——同一输入必然同一输出，
  pytest 可回归；研判≠预测，输出为「多维事实的五档映射」，仅供决策参考；
- **维度**（各维独立打分 +1/0/-1，失败不参与）：
  ① 市场宽度（涨跌家数比）② 新闻情绪温度（50+均分×500 口径）
  ③ 拥挤度风险（行业拥挤度 ≥80 的板块数）④ 宏观利率（期限利差倒挂 / 10Y 上行）
  盘后再附北向成交额展示维度（只展示不参与打分——成交额活跃度无方向语义）;
- **五档信号**：score ≥2 偏多 / 1 偏多谨慎 / 0 中性 / -1 偏空谨慎 / ≤-2 偏空；
  全部维度不可得 → 「数据不足」（绝不硬凑结论）;
- **落库**：`DATA_DIR/briefs.json` 单文件 ``{records: [...]}``，同日同 kind
  幂等覆盖，原子写（tmp+replace）;
- **调度**：``ensure_today()`` 由 main.py 后台线程每 15 分钟调用，判定 =
  工作日 + 盘前 ≥08:30 / 盘后 ≥15:30 + 当日该 kind 缺档。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from backend.app import config
from backend.app.datasource.utils import bj_now, now_text, today_bj
from backend.app.datasource.portfolio_trend import is_workday

logger = logging.getLogger(__name__)

BRIEFS_PATH = config.DATA_DIR / "briefs.json"
SOURCE = "本地规则聚合"
CALIBER = "确定性规则五档研判（宽度+情绪温度+拥挤度+利率），非 AI 输出，仅供参考"

# 五档信号映射：score 总和 → 信号
_SIGNALS: List[tuple] = [
    (2, "偏多"),
    (1, "偏多谨慎"),
    (0, "中性"),
    (-1, "偏空谨慎"),
]


def _read_all() -> Dict[str, Any]:
    """读全量落库文件（不存在/损坏按空处理）。

    Returns:
        dict: ``{records: [...]}``。
    """
    try:
        payload = json.loads(BRIEFS_PATH.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("records"), list):
            return payload
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001 - 损坏文件按空处理并留痕
        logger.info("briefs.json 读取失败按空处理：%s", type(exc).__name__)
    return {"records": []}


def _write_all(payload: Dict[str, Any]) -> None:
    """原子写全量落库文件（tmp + replace，防半写）。"""
    BRIEFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = BRIEFS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(BRIEFS_PATH)


def _dim_breadth() -> Dict[str, Any]:
    """维度①市场宽度：涨跌家数比（>1.5 多 / <0.67 空 / 其余中性）。"""
    from backend.app.datasource import breadth as breadth_provider

    data = breadth_provider.market_breadth() or {}
    if not data.get("available"):
        return {"key": "breadth", "label": "市场宽度", "known": False, "detail": "涨跌家数不可用"}
    up = float(data.get("up") or 0)
    down = float(data.get("down") or 0)
    if down <= 0:
        ratio = 3.0 if up > 0 else 1.0
    else:
        ratio = up / down
    score = 1 if ratio > 1.5 else (-1 if ratio < 0.67 else 0)
    return {
        "key": "breadth", "label": "市场宽度", "known": True, "score": score,
        "value_text": f"涨 {int(up)} 家 / 跌 {int(down)} 家（比值 {ratio:.2f}）",
        "detail": "比值 >1.5 偏多 / <0.67 偏空",
    }


def _dim_temperature() -> Dict[str, Any]:
    """维度②新闻情绪温度：temperature >60 多 / <40 空（50+均分×500 口径）。"""
    from backend.app.datasource import news as news_provider
    from backend.app.datasource import sentiment as sentiment_engine

    items = news_provider.get_flash_multi(60)
    scored = sentiment_engine.analyze_items(items or [], sectors=news_provider.sector_keywords())
    agg = sentiment_engine.aggregate(scored, scope="brief")
    temperature = agg.get("temperature")
    if temperature is None:
        return {"key": "temperature", "label": "新闻情绪温度", "known": False, "detail": "无有效新闻样本"}
    temperature = float(temperature)
    score = 1 if temperature > 60 else (-1 if temperature < 40 else 0)
    return {
        "key": "temperature", "label": "新闻情绪温度", "known": True, "score": score,
        "value_text": f"{temperature:.0f} 分（样本 {agg.get('total')} 条，利好 {agg.get('positive')} / 利空 {agg.get('negative')}）",
        "detail": "温度 >60 偏多 / <40 偏空",
    }


def _dim_crowding() -> Dict[str, Any]:
    """维度③拥挤度风险：行业拥挤度 ≥80 的板块数 ≥3 → 风险提示（-1）。"""
    from backend.app.datasource import sector as sector_provider

    data = sector_provider.get_crowding_rankings(kind="industry", limit=50) or {}
    items = data.get("items") or []
    hot = []
    for item in items:
        score = item.get("score")
        if isinstance(score, (int, float)) and score >= 80:
            hot.append(f"{item.get('board')}{score:.0f}")
    score = -1 if len(hot) >= 3 else 0
    return {
        "key": "crowding", "label": "拥挤度风险", "known": bool(items), "score": score if items else 0,
        "value_text": f"≥80 高危板块 {len(hot)} 个" + (f"（{('、'.join(hot[:5]))}）" if hot else ""),
        "detail": "高危板块 ≥3 触发风险提示（拥挤度监控口径 >80 提示减仓）",
    }


def _dim_macro() -> Dict[str, Any]:
    """维度④宏观利率：期限利差倒挂或 10Y 单期上行 ≥10bp → -1。"""
    from backend.app.datasource import macro as macro_provider

    overview = macro_provider.get_overview() or {}
    cards = {item.get("key"): item for item in overview.get("indicators") or []}
    parts: List[str] = []
    score = 0
    known = False
    spread = cards.get("term_spread") or {}
    if spread.get("value") is not None:
        known = True
        if float(spread["value"]) < 0:
            score -= 1
            parts.append(f"期限利差倒挂（{spread['value']}%）")
        else:
            parts.append(f"期限利差 {spread['value']}%")
    ten = cards.get("bond_10y") or {}
    if ten.get("value") is not None:
        known = True
        delta = ten.get("delta")
        text = f"10Y {ten['value']}%"
        if isinstance(delta, (int, float)) and delta >= 0.10:
            score -= 1
            text += f"（单期上行 {delta} 个百分点）"
        parts.append(text)
    if not known:
        return {"key": "macro", "label": "宏观利率", "known": False, "detail": "利率指标不可用"}
    return {
        "key": "macro", "label": "宏观利率", "known": True, "score": score,
        "value_text": "；".join(parts),
        "detail": "利差倒挂 / 10Y 单期上行 ≥10bp 记风险",
    }


def _dim_north() -> Dict[str, Any]:
    """盘后展示维度：北向当日成交总额（只展示不参与打分——活跃度无方向语义）。

    直调 ``moneyflow.get_northbound_turnover``（亿元口径，router 层才有缓存；
    本任务为每日一次的低频后台任务，直调可接受）。字段单位核对：deal_amt
    百万元 → 亿元（moneyflow.py 已换算），items[].total 即亿元。
    """
    from backend.app.datasource import moneyflow as moneyflow_provider

    try:
        payload = moneyflow_provider.get_northbound_turnover(20) or {}
        items = [row for row in payload.get("items") or []
                 if isinstance(row, dict) and row.get("total") is not None]
        if not items:
            return {"key": "north", "label": "北向成交", "known": False, "score": 0,
                    "detail": "暂无成交总额数据", "weight": "展示维度，不参与打分"}
        latest = items[-1]
        return {"key": "north", "label": "北向成交", "known": True, "score": 0,
                "value_text": f"当日 {latest['total']} 亿元（{latest.get('date')}，沪深股通合计）",
                "detail": "展示维度，不参与打分（成交额活跃度无方向语义）"}
    except Exception as exc:  # noqa: BLE001 - 展示维度失败不影响信号
        return {"key": "north", "label": "北向成交", "known": False, "score": 0,
                "detail": f"取数失败（{type(exc).__name__}）", "weight": "展示维度，不参与打分"}


def build_brief(kind: str) -> Dict[str, Any]:
    """生成一份研判（确定性规则聚合）。

    Args:
        kind: ``pre``=盘前（08:30 后）/ ``post``=盘后（15:30 后）。

    Returns:
        dict: 研判记录（signal/score/dimensions/note）。
    """
    builders = [_dim_breadth, _dim_temperature, _dim_crowding, _dim_macro]
    if kind == "post":
        builders.append(_dim_north)
    dimensions: List[Dict[str, Any]] = []
    for build in builders:
        try:
            dimensions.append(build())
        except Exception as exc:  # noqa: BLE001 - 单维度失败记 detail 不中断
            logger.info("研判维度构建失败：%s（%s）", build.__name__, type(exc).__name__)
            dimensions.append({"key": build.__name__, "label": "维度异常", "known": False,
                               "detail": f"构建失败（{type(exc).__name__}）"})
    known_scores = [d.get("score") for d in dimensions
                    if d.get("known") and isinstance(d.get("score"), (int, float))
                    and d.get("key") != "north"]  # north 为展示维度不打分
    total = int(sum(known_scores)) if known_scores else None
    if total is None:
        signal = "数据不足"
    else:
        signal = "偏空"
        for threshold, name in _SIGNALS:
            if total >= threshold:
                signal = name
                break
    return {
        "kind": kind,
        "date": today_bj().isoformat(),
        "signal": signal,
        "score": total,
        "dimensions": dimensions,
        "source": SOURCE,
        "caliber": CALIBER,
        "note": "确定性规则聚合（非 AI 输出、非预测），仅供决策参考；维度失败不参与打分",
        "generated_at": now_text(),
    }


def _board_lookup(board: str) -> Dict[str, Any]:
    """按板块名在「行业 → 概念」两池中查找该板块当前行情。

    Args:
        board: 板块名（行业或概念）。

    Returns:
        dict: `{name, kind, pct, amount, net_inflow, lead_stock}`；未命中返回 kind="未收录"。
    """
    from backend.app.datasource import sector as sector_provider

    for kind, loader in (("industry", sector_provider.get_industry), ("concept", sector_provider.get_concept)):
        try:
            items = loader("cn") or []
        except Exception:  # noqa: BLE001 - 单个池失败不中断
            continue
        for item in items:
            if str(item.get("name") or "") == board:
                return {
                    "name": board, "kind": kind,
                    "pct": item.get("pct"), "amount": item.get("amount"),
                    "net_inflow": item.get("net_inflow"), "lead_stock": item.get("lead_stock"),
                }
    return {"name": board, "kind": "未收录"}


def build_board_brief(board: str, kind: str = "pre") -> Dict[str, Any]:
    """生成「指定板块」的确定性规则研判（不落库，按需计算）。

    维度（板块视角，规则同全市场口径但样本限定为该板块）：
      ① 板块动量：板块当日涨跌幅（>+1% 多 / <-1% 空）
      ② 板块资金：主力净流入（>0 多 / <0 空；无该字段的概念池按「不可用」不计分）
      ③ 板块快讯：命中该板块名的快讯情绪温度（>60 多 / <40 空）

    Args:
        board: 板块名（行业或概念）。
        kind: ``pre`` / ``post``（沿用调用时机语义，不影响板块维度口径）。

    Returns:
        dict: 与 `build_brief` 同构的研判记录（附 board / board_kind 字段）。
    """
    info = _board_lookup(board)
    dimensions: List[Dict[str, Any]] = []

    pct = info.get("pct")
    if isinstance(pct, (int, float)):
        dimensions.append({
            "key": "board_momentum", "label": "板块动量（当日涨跌幅）", "known": True,
            "score": 1 if pct > 1 else (-1 if pct < -1 else 0),
            "value_text": f"{pct:+.2f}%",
            "detail": ">+1% 偏多 / <-1% 偏空（板块视角）",
        })
    else:
        dimensions.append({"key": "board_momentum", "label": "板块动量", "known": False,
                          "detail": "该板块当日行情不可用"})

    flow = info.get("net_inflow")
    if isinstance(flow, (int, float)):
        dimensions.append({
            "key": "board_flow", "label": "板块主力净流入", "known": True,
            "score": 1 if flow > 0 else (-1 if flow < 0 else 0),
            "value_text": f"{flow:+.2f} 亿",
            "detail": "净流入为正偏多 / 为负偏空",
        })
    else:
        dimensions.append({"key": "board_flow", "label": "板块主力净流入", "known": False,
                          "detail": "该板块口径无净流入字段（如概念池/境外池）→ 不计分"})

    try:
        from backend.app.datasource import news as news_provider
        from backend.app.datasource import sentiment as sentiment_engine

        items = [x for x in (news_provider.get_flash_multi(120) or [])
                 if board in str(x.get("title") or "") or board in str(x.get("text") or "")]
        if items:
            scored = sentiment_engine.analyze_items(items, sectors=news_provider.sector_keywords())
            agg = sentiment_engine.aggregate(scored, scope="brief")
            temperature = agg.get("temperature")
        else:
            agg, temperature = {}, None
        if temperature is None:
            dimensions.append({"key": "board_news", "label": "板块快讯情绪", "known": False,
                              "detail": f"近 120 条快讯中命中「{board}」的样本不足"})
        else:
            temperature = float(temperature)
            dimensions.append({
                "key": "board_news", "label": "板块快讯情绪温度", "known": True,
                "score": 1 if temperature > 60 else (-1 if temperature < 40 else 0),
                "value_text": f"{temperature:.0f} 分（命中 {agg.get('total')} 条，利好 {agg.get('positive')} / 利空 {agg.get('negative')}）",
                "detail": "仅统计标题/正文命中该板块名的快讯",
            })
    except Exception as exc:  # noqa: BLE001 - 维度失败不中断
        dimensions.append({"key": "board_news", "label": "板块快讯情绪", "known": False,
                          "detail": f"构建失败（{type(exc).__name__}）"})

    known = [d.get("score") for d in dimensions
             if d.get("known") and isinstance(d.get("score"), (int, float))]
    total = int(sum(known)) if known else None
    if total is None:
        signal = "数据不足"
    else:
        signal = "偏空"
        for threshold, name in _SIGNALS:
            if total >= threshold:
                signal = name
                break
    return {
        "kind": kind,
        "board": board,
        "board_kind": info.get("kind"),
        "date": today_bj().isoformat(),
        "signal": signal,
        "score": total,
        "dimensions": dimensions,
        "source": SOURCE,
        "caliber": f"板块确定性规则（板块视角：{board}）——维度样本限定该板块，非全市场口径",
        "note": "确定性规则聚合（非 AI 输出、非预测），仅供决策参考；维度失败不参与打分",
        "generated_at": now_text(),
    }


def ensure_today(kind: str, *, now: Optional[Any] = None) -> Dict[str, Any]:
    """按调度条件补当日研判：工作日 + 时段 + 当日该 kind 缺档。

    Args:
        kind: ``pre`` / ``post``。
        now: 注入当前时间（测试用）；默认北京时间 now。

    Returns:
        dict: ``{written, reason, date, signal?}``。
    """
    if kind not in ("pre", "post"):
        return {"written": False, "reason": f"未知 kind：{kind}"}
    moment = now or bj_now()
    today = today_bj()
    key = today.isoformat()
    if not is_workday(today):
        return {"written": False, "reason": "非工作日", "date": key}
    threshold = (8, 30) if kind == "pre" else (15, 30)
    if (moment.hour, moment.minute) < threshold:
        return {"written": False, "reason": f"未到{'盘前' if kind == 'pre' else '盘后'}生成时段（{threshold[0]:02d}:{threshold[1]:02d}）", "date": key}
    payload = _read_all()
    for record in payload["records"]:
        if record.get("kind") == kind and record.get("date") == key:
            return {"written": False, "reason": "当日已有档", "date": key,
                    "signal": record.get("signal")}
    record = build_brief(kind)
    payload["records"].append(record)
    # 单文件体积守护：每 kind 保留最近 120 条（约半年）
    for keep_kind in ("pre", "post"):
        rows = [r for r in payload["records"] if r.get("kind") == keep_kind]
        if len(rows) > 120:
            drop = {id(r) for r in sorted(rows, key=lambda r: str(r.get("date")))[:-120]}
            payload["records"] = [r for r in payload["records"] if id(r) not in drop]
    _write_all(payload)
    logger.info("研判已落库：%s %s（%s）", key, kind, record.get("signal"))
    return {"written": True, "reason": "ok", "date": key, "signal": record.get("signal")}


def list_briefs(limit: int = 10, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    """读最近研判（按日期倒序）。

    Args:
        limit: 返回条数上限。
        kind: 可选过滤 pre/post。

    Returns:
        list[dict]: 研判记录列表。
    """
    records = _read_all().get("records") or []
    if kind in ("pre", "post"):
        records = [r for r in records if r.get("kind") == kind]
    records = sorted(records, key=lambda r: (str(r.get("date")), str(r.get("generated_at"))), reverse=True)
    return records[:limit]
