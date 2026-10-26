# -*- coding: utf-8 -*-
"""仪表板本地持久化（R3-02 · 分组 / 增删改 / 复制 / 排序）。

设计基准：`设计基准_五功能_20261024.md` D-01 / D-02。
存储：`data/dashboards.json`，结构 `{dashboards: [...], activeId: "..."}`；
每个仪表板含 `id / name / group / widgets[{id, span}]`。

约束（规范 §6.5 写副作用）：
1. 读-改-写全程持进程内锁，写入用 atomic.atomic_write_json 原子替换（R3-09 收口）；
2. 文件损坏按默认空结构处理并把原文件备份为 `.bad`，不静默覆盖用户数据；
3. 只做结构校验（类型/长度/白名单字段），不做业务渲染，前端负责交互。
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict, List, Optional

from backend.app import config
from backend.app.core.errors import NonRetryableError
from backend.app.core import atomic

logger = logging.getLogger(__name__)

DASHBOARDS_FILE = config.DATA_DIR / "dashboards.json"
MAX_DASHBOARDS = 50        # 单用户仪表板上限，防止误导入超大清单
MAX_WIDGETS_PER_DASH = 60  # 单仪表板组件上限
MAX_NAME_LEN = 60          # 仪表板/分组名称最大字符数
_LOCK = threading.Lock()


def _empty() -> Dict[str, Any]:
    """空结构（无仪表板）。

    Returns:
        dict: {"dashboards": [], "activeId": ""}。
    """
    return {"dashboards": [], "activeId": ""}


def _clean_name(value: Any, fallback: str = "") -> str:
    """规范化名称：去首尾空白、截断超长、空值回落。

    Args:
        value: 原始名称。
        fallback: 空值时使用的兜底名称。

    Returns:
        str: 规范化后的名称。
    """
    text = str(value or "").strip()
    if not text:
        return fallback
    return text[:MAX_NAME_LEN]


def _clean_widget(item: Any) -> Optional[Dict[str, Any]]:
    """校验并规范化单个组件条目。

    Args:
        item: 原始条目（应含 id / span）。

    Returns:
        Optional[dict]: 合法时返回 {id, span}，否则 None。
    """
    if not isinstance(item, dict):
        return None
    widget_id = str(item.get("id") or "").strip()
    if not widget_id:
        return None
    try:
        span = int(item.get("span") or 48)
    except (TypeError, ValueError):
        span = 48
    # 48 列栅格：允许 12–48，其他值向上吸附到最近的合法档位
    span = max(12, min(48, span))
    payload: Dict[str, Any] = {"id": widget_id, "span": span}
    # R3-11：卡片自定义高度（拖拽下边缘后持久化，120–900px）
    if item.get("h") is not None:
        try:
            height = int(item.get("h"))
        except (TypeError, ValueError):
            height = 0
        if height > 0:
            payload["h"] = max(120, min(900, height))
    # 透传可选参数（个股标的/周期等），仅保留可序列化的标量
    params = item.get("params")
    if isinstance(params, dict):
        payload["params"] = {
            str(k): v for k, v in params.items()
            if isinstance(v, (str, int, float, bool)) or v is None
        }
    return payload


def _clean_dashboard(item: Any, index: int) -> Optional[Dict[str, Any]]:
    """校验并规范化单个仪表板。

    Args:
        item: 原始仪表板对象。
        index: 序号（用于生成缺省名称）。

    Returns:
        Optional[dict]: 合法时返回规范化对象，否则 None。
    """
    if not isinstance(item, dict):
        return None
    dash_id = str(item.get("id") or "").strip() or f"dash-auto-{index}"
    name = _clean_name(item.get("name"), f"仪表板 {index + 1}")
    group = _clean_name(item.get("group"), "")
    raw_widgets = item.get("widgets")
    widgets: List[Dict[str, Any]] = []
    if isinstance(raw_widgets, list):
        for entry in raw_widgets[:MAX_WIDGETS_PER_DASH]:
            cleaned = _clean_widget(entry)
            if cleaned:
                widgets.append(cleaned)
    return {"id": dash_id, "name": name, "group": group, "widgets": widgets}


def normalize(payload: Any) -> Dict[str, Any]:
    """把任意入参规范化为可落盘的结构（幂等）。

    Args:
        payload: 前端提交的对象，期望含 dashboards / activeId。

    Returns:
        dict: {"dashboards": [...], "activeId": "..."}。

    Raises:
        NonRetryableError: 入参不是对象，或仪表板数量超限。
    """
    if not isinstance(payload, dict):
        raise NonRetryableError("仪表板数据必须是对象")
    raw_list = payload.get("dashboards")
    if not isinstance(raw_list, list):
        raise NonRetryableError("dashboards 必须是数组")
    if len(raw_list) > MAX_DASHBOARDS:
        raise NonRetryableError(f"仪表板数量超限（最多 {MAX_DASHBOARDS} 个）")
    dashboards: List[Dict[str, Any]] = []
    seen = set()
    for index, item in enumerate(raw_list):
        cleaned = _clean_dashboard(item, index)
        if not cleaned:
            continue
        # id 去重：重复时补后缀，避免前端按 id 定位到错误仪表板
        if cleaned["id"] in seen:
            cleaned["id"] = f"{cleaned['id']}-{index}"
        seen.add(cleaned["id"])
        dashboards.append(cleaned)
    active = str(payload.get("activeId") or "").strip()
    if dashboards and active not in seen:
        active = dashboards[0]["id"]
    return {"dashboards": dashboards, "activeId": active if dashboards else ""}


def load() -> Dict[str, Any]:
    """读取仪表板配置；文件缺失或损坏时返回空结构。

    Returns:
        dict: {"dashboards": [...], "activeId": "..."}。
    """
    with _LOCK:
        return _load_unlocked()


def _load_unlocked() -> Dict[str, Any]:
    """无锁读取（调用方须持锁）。

    Returns:
        dict: 规范化结构；异常时为空结构。
    """
    if not DASHBOARDS_FILE.exists():
        return _empty()
    try:
        raw = json.loads(DASHBOARDS_FILE.read_text(encoding="utf-8"))
        return normalize(raw)
    except (OSError, ValueError, NonRetryableError) as exc:
        # 损坏文件先备份再返回空结构，避免下次写入直接覆盖用户数据
        logger.warning("仪表板配置损坏，已备份并重置：%s（%s）", DASHBOARDS_FILE, type(exc).__name__)
        try:
            backup = DASHBOARDS_FILE.with_suffix(".json.bad")
            DASHBOARDS_FILE.replace(backup)
        except OSError:
            pass
        return _empty()


def save(payload: Any) -> Dict[str, Any]:
    """规范化后原子写入仪表板配置。

    Args:
        payload: 前端提交的对象。

    Returns:
        dict: 落盘后的规范化结构。

    Raises:
        NonRetryableError: 结构非法或磁盘写入失败。
    """
    cleaned = normalize(payload)
    with _LOCK:
        try:
            atomic.atomic_write_json(DASHBOARDS_FILE, cleaned, mode=0o600)
        except OSError as exc:
            raise NonRetryableError(f"仪表板写入失败：{exc}") from exc
    return cleaned


def groups(payload: Optional[Dict[str, Any]] = None) -> List[str]:
    """汇总当前全部分组名（去重、保持出现顺序）。

    Args:
        payload: 可选的结构对象；缺省读取本地文件。

    Returns:
        list[str]: 分组名列表（不含空分组）。
    """
    data = payload if payload is not None else load()
    result: List[str] = []
    for item in data.get("dashboards") or []:
        group = str(item.get("group") or "").strip()
        if group and group not in result:
            result.append(group)
    return result
