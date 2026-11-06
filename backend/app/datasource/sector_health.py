# -*- coding: utf-8 -*-
"""概念排行健康抽检（第十轮 P3 · R11 / 验收 C11）。

C11 判据 = 「概念排行 175 项完整率 ≥98%，**连续 3 日抽检**」。
设计基准 §8 R11 的解法三件套里，双链路（排行逐板块取概念日 K：
`sector.py::_kline_ths` 概念分支，腾讯 pt 概念日 K 主源→同花顺概念指数
885xxx 备源）与失败重试 1 次后错误态（`sector.py::_scan_rankings._work`）
已在 d6fc60e 落地重试；本模块补齐**「定时抽检日志」**这一环：

- 每个交易日 15:35 后自动扫描一次概念排行（可选行业排行），把
  `{date, scanned, total, completeness_pct, failed, pass}` 追加到
  `DATA_DIR/concept_spotcheck.jsonl`（一日一行，重复触发覆盖当日行）；
- 读取端 `load_records()` 给 `/api/sector/concept/health`，按「最近 N 个
  交易日全部 ≥98%」给出 `streak_ok`（C11 的连续 3 日判据）；
- 调度幂等与失败隔离沿用 R13 持仓快照模式（`portfolio_trend.ensure_today`）：
  工作日 + 15:35 后 + 当日缺档才跑；扫描失败当日记 error 行，不重试轰炸源站。

完整率口径：`scanned / total`，total=概念榜全量（腾讯 805 / 新浪 175 备源时
以当次实际清单为准）。新概念板块（上市不足 60 个交易日）属数据积累期失败，
计入 failed 拉低完整率——这正是抽检日志要暴露的信号，不做人为剔除。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from backend.app import config
from backend.app.datasource.utils import bj_now, now_text

logger = logging.getLogger(__name__)

SPOTCHECK_FILE = config.DATA_DIR / "concept_spotcheck.jsonl"
COMPLETENESS_PASS_PCT = 98.0   # C11：完整率阈值
STREAK_DAYS = 3               # C11：连续 N 个交易日
SPOTCHECK_HOUR, SPOTCHECK_MINUTE = 15, 35  # 收盘后（排行缓存 1h，避开 15:05 快照窗口）


def _read_all() -> List[Dict[str, Any]]:
    """读全部抽检记录（文件缺失/坏行容错跳过）。

    Returns:
        List[Dict[str, Any]]: 按 date 升序。
    """
    if not SPOTCHECK_FILE.exists():
        return []
    records: List[Dict[str, Any]] = []
    for line in SPOTCHECK_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            if isinstance(row, dict) and row.get("date"):
                records.append(row)
        except json.JSONDecodeError:
            continue
    records.sort(key=lambda row: str(row.get("date")))
    return records


def _rewrite_all(records: List[Dict[str, Any]]) -> None:
    """全量重写抽检文件（一日一行语义：同日新记录替换旧行）。

    Args:
        records: 待写记录（任意顺序）。
    """
    by_day: Dict[str, Dict[str, Any]] = {}
    for row in records:
        by_day[str(row.get("date"))] = row
    ordered = [by_day[key] for key in sorted(by_day)]
    SPOTCHECK_FILE.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(row, ensure_ascii=False) for row in ordered)
    SPOTCHECK_FILE.write_text(text + "\n" if text else "", encoding="utf-8")


def is_workday(day: date) -> bool:
    """工作日判定（节假日无排行数据，自然不产生记录；与 R13 同口径）。

    Args:
        day: 日期。

    Returns:
        bool: 周一至周五为 True。
    """
    return day.weekday() < 5


def run_spotcheck(kind: str = "concept", day: Optional[str] = None) -> Dict[str, Any]:
    """执行一次概念/行业排行全量扫描并落抽检记录（当日重复触发覆盖）。

    Args:
        kind: concept（C11 主判据）/ industry。
        day: 记录归属日期 YYYY-MM-DD；默认取真实当天。**调度注入时钟时
            必须由 ensure_today 传入同一日期**——否则真实墙钟跨午夜后，
            判重键（注入日）与落档日（真实日）错位，「当日已抽检」永不命中，
            同日重复扫描（全量回归 2026-11-07 00:00 后实测暴露）。

    Returns:
        Dict[str, Any]: 抽检记录 {date, kind, scanned, total,
        completeness_pct, failed, pass, error, taken_at}。
    """
    from backend.app.datasource import sector as sector_provider

    key = day or bj_now().date().isoformat()
    record: Dict[str, Any] = {
        "date": key, "kind": kind, "scanned": 0, "total": 0,
        "completeness_pct": None, "failed": [], "pass": False,
        "error": None, "taken_at": now_text(),
    }
    try:
        # 排行扫描体对**全清单**逐板块打分（scanned/total 即全量口径），
        # limit 只截返回条数 → 抽检用 limit=1 省序列化，完整率不受影响。
        data = sector_provider.get_crowding_rankings(kind=kind, limit=1)
        record["scanned"] = int(data.get("scanned") or 0)
        record["total"] = int(data.get("total") or 0)
        record["failed"] = (data.get("failed") or [])[:20]
        record["failed_count"] = len(data.get("failed") or [])
        if record["total"] > 0:
            record["completeness_pct"] = round(record["scanned"] / record["total"] * 100, 2)
            record["pass"] = record["completeness_pct"] >= COMPLETENESS_PASS_PCT
    except Exception as exc:  # noqa: BLE001 - 通道整体失败也要留痕（错误态记录）
        logger.warning("概念排行抽检失败（%s）：%s", kind, type(exc).__name__)
        record["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    records = [row for row in _read_all() if not (row.get("date") == key and row.get("kind") == kind)]
    records.append(record)
    _rewrite_all(records)
    logger.info(
        "概念排行抽检落档：%s %s 完整率 %s%%（%s）",
        key, kind, record["completeness_pct"], "达标" if record["pass"] else "未达标",
    )
    return record


def ensure_today(now: Optional[datetime] = None, kind: str = "concept") -> Dict[str, Any]:
    """调度入口：工作日 + 15:35 后 + 当日无 concept 记录 → 跑一次抽检。

    Args:
        now: 注入当前时间（测试用）。
        kind: 抽检口径。

    Returns:
        Dict[str, Any]: {written, reason, date}。
    """
    moment = now or bj_now()
    today = moment.date()
    key = today.isoformat()
    if not is_workday(today):
        return {"written": False, "reason": "非工作日", "date": key}
    if moment.hour < SPOTCHECK_HOUR or (moment.hour == SPOTCHECK_HOUR and moment.minute < SPOTCHECK_MINUTE):
        return {"written": False, "reason": f"未到收盘后时点（{SPOTCHECK_HOUR}:{SPOTCHECK_MINUTE:02d}）", "date": key}
    if any(row.get("date") == key and row.get("kind") == kind for row in _read_all()):
        return {"written": False, "reason": "当日抽检已存在", "date": key}
    run_spotcheck(kind, day=key)
    return {"written": True, "reason": "已落档", "date": key}


def load_records(days: int = 10, kind: str = "concept") -> Dict[str, Any]:
    """读取抽检日志（C11 取证视图）。

    Args:
        days: 最近 N 条（按日去重后倒序截取）。
        kind: 口径过滤。

    Returns:
        Dict[str, Any]: {items(新→旧), count, streak_days_ok, streak_required,
        latest_pass, all_pass, source, caliber, as_of}。
    """
    rows = [row for row in _read_all() if row.get("kind") == kind]
    rows.sort(key=lambda row: str(row.get("date")), reverse=True)
    items = rows[: max(1, min(int(days or 10), 60))]
    streak = 0
    for row in items:
        if row.get("pass"):
            streak += 1
        else:
            break
    # all_pass：最近 STREAK_DAYS 个交易日是否全部达标；不足 N 天记录=None（证据未攒够，
    # 绝不默认 True——J1 零降级）。
    recent = items[:STREAK_DAYS]
    all_pass: Optional[bool] = None
    if len(recent) >= STREAK_DAYS:
        all_pass = all(bool(row.get("pass")) for row in recent)
    return {
        "items": items,
        "count": len(rows),
        "streak_days_ok": streak,
        "streak_required": STREAK_DAYS,
        "latest_pass": bool(items and items[0].get("pass")),
        "all_pass": all_pass,
        "threshold_pct": COMPLETENESS_PASS_PCT,
        "source": "本地抽检日志",
        "caliber": (
            "概念排行完整率=扫描命中/概念清单（排行逐板块取概念日 K，"
            "双链路=腾讯概念日 K 主源→同花顺概念指数 885xxx 备源；"
            f"单板块失败自动重试 1 次仍败计入 failed）；交易日 15:35 后自动抽检一次，"
            f"一日一行落 {SPOTCHECK_FILE.name}；C11 判据=完整率 ≥{COMPLETENESS_PASS_PCT}% "
            f"连续 {STREAK_DAYS} 个交易日；新概念板块上市不足 60 交易日属数据积累期，"
            "如实计入 failed 不人为剔除"
        ),
        "as_of": now_text(),
        "market_status": "本地记录",
        "is_realtime": False,
    }
