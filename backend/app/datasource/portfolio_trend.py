# -*- coding: utf-8 -*-
"""持仓收益走势快照（第十轮 P2 · `/api/portfolio/trend` · R13）。

口径（DS-R10-v2.1 §12）
------------------------
- **逐交易日快照**：每个交易日收盘后写一条 `{date, market_value, cost, pnl}` 到
  `DATA_DIR/portfolio_snapshots/YYYY-MM-DD.json`；非交易日**不产生记录**；
- **禁止补零**：读取时按文件实际存在日期出点，缺日（休市/服务未运行）就是断点，
  不做线性插值、不填 0；
- 幂等：同一天重复触发只覆盖当日文件，不新增点；
- 失败隔离：快照失败只记日志，不影响 portfolio 主接口。

调度：`ensure_today()` 由后台线程每小时调用一次（见 `main.py` 的 startup hook）；
判定条件 = 工作日 + 当日 15:05 之后 + 当日文件不存在。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List

from backend.app import config
from backend.app.datasource import portfolio as portfolio_provider
from backend.app.datasource.utils import bj_now, now_text, today_bj

logger = logging.getLogger(__name__)

SNAP_DIR = config.DATA_DIR / "portfolio_snapshots"
SOURCE = "本地快照"
CALIBER = "逐交易日收盘快照 · 非交易日不产生点 · 禁止补零"


def _ensure_dir() -> None:
    """确保快照目录存在（不可写时抛给上层转错误）。"""
    SNAP_DIR.mkdir(parents=True, exist_ok=True)


def _snap_path(day: str) -> Any:
    """某日快照文件路径。"""
    return SNAP_DIR / f"{day}.json"


def take_snapshot(day: str | None = None) -> Dict[str, Any]:
    """写一条当日快照（同日覆盖，幂等）。

    Args:
        day: 日期字符串 YYYY-MM-DD；默认今天。

    Returns:
        dict: 写入的记录；失败抛异常由调用方处理。
    """
    target = day or today_bj().isoformat()
    analysis = portfolio_provider.analyze()
    totals = analysis.get("totals") or {}
    record = {
        "date": target,
        "market_value": totals.get("cny_market_value"),
        "cost": totals.get("cny_cost"),
        "pnl": totals.get("cny_pnl"),
        "count": analysis.get("count") or 0,
        "taken_at": now_text(),
    }
    _ensure_dir()
    _snap_path(target).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("持仓快照已写：%s（%s 项）", target, record["count"])
    return record


def is_workday(day: date) -> bool:
    """工作日判定（不含法定节假日表，节日由「无快照」自然形成断点）。"""
    return day.weekday() < 5


def ensure_today(now: datetime | None = None) -> Dict[str, Any]:
    """按调度条件补当日快照：工作日 + 15:05 后 + 当日缺失。

    Args:
        now: 注入当前时间（测试用），默认取系统时间。

    Returns:
        dict: `{written, reason, date}`。
    """
    moment = now or bj_now()
    today = moment.date()
    key = today.isoformat()
    if not is_workday(today):
        return {"written": False, "reason": "非工作日", "date": key}
    if moment.hour < 15 or (moment.hour == 15 and moment.minute < 5):
        return {"written": False, "reason": "未到收盘后时点（15:05）", "date": key}
    if _snap_path(key).exists():
        return {"written": False, "reason": "当日快照已存在", "date": key}
    try:
        take_snapshot(key)
        return {"written": True, "reason": "已补写", "date": key}
    except Exception as exc:  # noqa: BLE001 - 快照失败不影响主流程
        logger.warning("持仓快照写入失败 %s: %s", key, type(exc).__name__)
        return {"written": False, "reason": f"写入失败：{type(exc).__name__}", "date": key}


def load_trend(days: int = 90) -> Dict[str, Any]:
    """读取快照序列（按日期升序，缺日即断点，不补值）。

    Args:
        days: 取最近 N 个自然日窗口内的快照（默认 90）。

    Returns:
        dict: `{items, count, from, to, source, caliber, market_status, is_realtime}`。
    """
    items: List[Dict[str, Any]] = []
    if not SNAP_DIR.exists():
        return {
            "items": [],
            "count": 0,
            "from": None,
            "to": None,
            "source": SOURCE,
            "caliber": CALIBER,
            "market_status": "本地快照",
            "is_realtime": False,
            "hint": "尚无快照：服务需在交易日 15:05 后运行一次才会产生首个数据点",
        }
    cutoff = (today_bj() - timedelta(days=max(1, min(days, 3650)))).isoformat()
    for path in sorted(SNAP_DIR.glob("*.json")):
        stem = path.stem
        if stem < cutoff:
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 单个损坏文件跳过，不拖垮整条曲线
            logger.warning("快照文件损坏，已跳过：%s", path.name)
            continue
        if not isinstance(record, dict) or not record.get("date"):
            continue
        items.append(record)
    result: Dict[str, Any] = {
        "items": items,
        "count": len(items),
        "from": items[0]["date"] if items else None,
        "to": items[-1]["date"] if items else None,
        "source": SOURCE,
        "caliber": CALIBER,
        "market_status": "本地快照",
        "is_realtime": False,
    }
    if not items:
        # 目录已建但尚无快照（新装 / 首次运行）时也必须给引导，
        # 否则前端只剩空列表（P2 契约用例发现：原实现仅目录不存在才给 hint）
        result["hint"] = "尚无快照：服务需在交易日 15:05 后运行一次才会产生首个数据点"
    return result

