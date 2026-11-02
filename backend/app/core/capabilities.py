# -*- coding: utf-8 -*-
"""外部通道能力探测与状态文案（P1-05 / P2-16）。

背景：健康检查原先只用 ``importlib.util.find_spec`` 判断 pip 包是否安装，
对「同花顺 / 财联社 / 蛋卷」这类**纯 HTTP 外部通道**永远探不到，因而长期显示
「待接入」；但实测板块资金流、快讯、基金净值都能出数 —— 状态与实际能力矛盾。

处置（规范 §6.6 真实性、§11.11 三源一致性）：
1. 对外部通道做**最小真实请求**（各 2 秒超时，并行执行），按结果给状态；
2. 结果缓存 10 分钟，避免 /api/health 被高频轮询时反复打源站；
3. 状态文案统一由本模块产出，``/api/health`` 与 ``/api/connectors/status``
   共用同一份快照，杜绝两处说法不一致；
4. 同花顺已改走纯 HTTP 通道（第八轮），不再依赖 py_mini_racer(V8)，
   故状态统一为「已接入（纯 HTTP，无 V8 依赖）」，不再出现「V8 降级」文案。
"""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlencode

from backend.app import config
from backend.app.datasource import utils
from backend.app.core import netfix

logger = logging.getLogger(__name__)

PROBE_TTL = 600  # 探测结果缓存秒数（10 分钟）——仅供探测结果专用短缓存，与数据缓存不同
PROBE_TIMEOUT = 2.0  # 单个通道探测超时（秒）

# {源键: (过期时间戳, {"ok": bool, "detail": str, "probed_at": str})}
_PROBE_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_PROBE_LOCK = threading.Lock()


def _now_text() -> str:
    """当前时间字符串（本地时区，秒级）。

    Returns:
        str: 形如 2026-10-24 00:30:00。
    """
    return utils.now_text()


def _probe_cls() -> Tuple[bool, str]:
    """财联社电报通道最小探测（真实取 20 条，需 md5(sha1(params)) 签名）。

    Returns:
        Tuple[bool, str]: (是否可用, 中文说明)。
    """
    url = "https://www.cls.cn/v1/roll/get_roll_list"
    params = {
        "app": "CailianpressWeb",
        "category": "",
        "last_time": int(time.time()),
        "os": "web",
        "refresh_type": "1",
        "rn": "20",
        "sv": "8.4.6",
    }
    params["sign"] = hashlib.md5(
        hashlib.sha1(urlencode(params).encode("utf-8")).hexdigest().encode("utf-8")
    ).hexdigest()
    try:
        session = netfix.make_session()
        response = session.get(
            url,
            params=params,
            headers={"User-Agent": config.BROWSER_USER_AGENT, "Referer": "https://www.cls.cn/telegraph"},
            timeout=PROBE_TIMEOUT,
        )
        if response.status_code != 200:
            return False, f"HTTP {response.status_code}"
        rows = (response.json() or {}).get("data", {}).get("roll_data") or []
        if not rows:
            return False, "返回空列表"
        return True, f"电报 {len(rows)} 条"
    except Exception as exc:  # noqa: BLE001 - 探测失败按不可用处理
        return False, type(exc).__name__


def _probe_fund() -> Tuple[bool, str]:
    """蛋卷（场外基金净值）最小探测：取 017811 最近 1 条净值。

    Returns:
        Tuple[bool, str]: (是否可用, 中文说明)。

    Notes:
        R3-20：直接复用 ``fund._danjuan``（含 netfix 统一会话、Referer、超时与
        result_code 校验），不再复刻 URL 拼接与解析；通道行为与基金净值链路一致。
    """
    try:
        # 延迟导入避免 core ↔ datasource 启动期循环依赖
        from backend.app.datasource.fund import _danjuan

        data = _danjuan("/djapi/fund/nav/history/017811", {"page": 1, "size": 1})
        items = data.get("items") or []
        if not items:
            return False, "净值列表为空"
        return True, f"净值 {items[0].get('date', '')} {items[0].get('nav', '')}"
    except Exception as exc:  # noqa: BLE001 - 探测失败按不可用处理
        return False, type(exc).__name__


def _probe_ths() -> Tuple[bool, str]:
    """同花顺行情通道最小探测（取贵州茅台日线元数据，不使用 V8 签名）。

    Returns:
        Tuple[bool, str]: (是否可用, 中文说明)。
    """
    url = "https://d.10jqka.com.cn/v6/line/hs_600519/01/last.js"
    try:
        session = netfix.make_session()
        response = session.get(
            url,
            headers={"User-Agent": config.BROWSER_USER_AGENT, "Referer": "https://stockpage.10jqka.com.cn/"},
            timeout=PROBE_TIMEOUT,
        )
        if response.status_code != 200:
            return False, f"HTTP {response.status_code}"
        if "quotebridge" not in response.text[:200]:
            return False, "返回格式异常"
        return True, "行情通道可达"
    except Exception as exc:  # noqa: BLE001 - 探测失败按不可用处理
        return False, type(exc).__name__


# 可替换的探测器表（测试可 monkeypatch 单源，不影响其它源）
PROBES: Dict[str, Callable[[], Tuple[bool, str]]] = {
    "ths": _probe_ths,
    "cls": _probe_cls,
    "fund": _probe_fund,
}


def probe_one(source: str) -> Optional[Dict[str, Any]]:
    """对单个源跑一次真实探测（不写缓存，供连接器页「测试连接」）。

    Args:
        source: 源键（ths/cls/fund）或中文标签（同花顺/财联社/天天基金）。

    Returns:
        Optional[Dict[str, Any]]: {ok, detail, probed_at}；未登记的源返回 None。
    """
    name = str(source or "").strip()
    if not name:
        return None
    key = name if name in PROBES else ""
    if not key:
        # 允许传中文标签：按 DATA_SOURCES 的 label 反查键名
        for meta_key, meta in config.DATA_SOURCES.items():
            if str(meta.get("label") or "") == name and meta_key in PROBES:
                key = meta_key
                break
    if not key:
        return None
    try:
        ok, detail = PROBES[key]()
    except Exception as exc:  # noqa: BLE001 - 探测异常按不可用返回，不抛出
        ok, detail = False, type(exc).__name__
    logger.info("单源探测 %s：%s（%s）", key, "可用" if ok else "不可用", detail)
    return {"ok": bool(ok), "detail": detail, "probed_at": _now_text()}


def probe_snapshot(force: bool = False) -> Dict[str, Dict[str, Any]]:
    """并行探测外部通道，带 10 分钟缓存。

    Args:
        force: True 时忽略缓存强制重探（供连接器页“刷新”与测试使用）。

    Returns:
        Dict[str, Dict[str, Any]]: {源键: {ok, detail, probed_at}}。
    """
    now = time.time()
    result: Dict[str, Dict[str, Any]] = {}
    todo = []
    with _PROBE_LOCK:
        for key in PROBES:
            cached = _PROBE_CACHE.get(key)
            if not force and cached and cached[0] > now:
                result[key] = dict(cached[1])
            else:
                todo.append(key)

    if todo:
        with ThreadPoolExecutor(max_workers=len(todo)) as executor:
            futures = {key: executor.submit(PROBES[key]) for key in todo}
            for key, future in futures.items():
                try:
                    ok, detail = future.result(timeout=PROBE_TIMEOUT + 1)
                except Exception as exc:  # noqa: BLE001 - 单源失败不影响其它源
                    ok, detail = False, type(exc).__name__
                payload = {"ok": bool(ok), "detail": detail, "probed_at": _now_text()}
                result[key] = payload
                with _PROBE_LOCK:
                    _PROBE_CACHE[key] = (now + PROBE_TTL, payload)
                logger.info("通道探测 %s：%s（%s）", key, "可用" if ok else "不可用", detail)
    return result


def _module_installed(module_name: Optional[str]) -> Optional[bool]:
    """判断 pip 依赖是否可导入（不触发导入）。

    Args:
        module_name: 模块名；为 None 时返回 None（外部 HTTP 通道无 pip 依赖）。

    Returns:
        Optional[bool]: 已装 True / 未装 False / 不适用 None。
    """
    if not module_name:
        return None
    try:
        return importlib.util.find_spec(str(module_name)) is not None
    except (ImportError, ValueError):
        return False


def _yangjibao_bound() -> bool:
    """判断养基宝是否已绑定（存在加密 token 文件）。

    Returns:
        bool: 已绑定返回 True。
    """
    try:
        from backend.app.datasource import yangjibao  # 延迟导入，避免循环依赖

        return bool(yangjibao.load_token())
    except Exception:  # noqa: BLE001 - 读取失败按未绑定处理
        return False


def datasource_status(force_probe: bool = False) -> Dict[str, Dict[str, Any]]:
    """产出与真实能力一致的数据源状态（health / connectors 共用）。

    Args:
        force_probe: 是否强制重新探测外部通道。

    Returns:
        Dict[str, Dict[str, Any]]: {源键: {label, enabled, installed, status,
        stage, active, detail, probed_at}}。
    """
    probes = probe_snapshot(force=force_probe)
    result: Dict[str, Dict[str, Any]] = {}

    for key, meta in config.DATA_SOURCES.items():
        kind = meta.get("kind") or ("library" if meta.get("module") else "http")
        installed = _module_installed(meta.get("module"))
        detail = ""
        probed_at = ""
        active = False
        stage = "active"

        if kind == "optin":
            bound = _yangjibao_bound()
            status_text = "已授权（已绑定）" if bound else "未授权待启用"
            active = bound
            stage = "active" if bound else "optin"
            detail = "本地 DPAPI 加密 token" if bound else "需扫码绑定后启用"
        elif not meta.get("enabled"):
            status_text = "已禁用"
            stage = "disabled"
        elif kind == "library":
            if installed:
                status_text = "已接入"
                active = True
            else:
                status_text = "依赖缺失"
                stage = "missing"
            detail = "pip 依赖已安装" if installed else "pip 依赖未安装"
        elif kind == "v8":
            probe = probes.get(key) or {}
            detail = str(probe.get("detail") or "")
            probed_at = str(probe.get("probed_at") or "")
            # 第八轮（2026-11-02）：板块/快讯/分红已全部改走同花顺纯 HTTP 通道，
            # V8 签名不再位于任何主链路上，故不再有「V8 降级」状态。
            if probe.get("ok"):
                status_text = "已接入（纯 HTTP，无 V8 依赖）"
                active = True
            else:
                status_text = "通道异常（探测失败）"
                stage = "unavailable"
        else:  # http
            probe = probes.get(key) or {}
            detail = str(probe.get("detail") or "")
            probed_at = str(probe.get("probed_at") or "")
            if probe.get("ok"):
                status_text = "已接入"
                active = True
            else:
                status_text = "通道异常（探测失败）"
                stage = "unavailable"

        result[key] = {
            "label": meta.get("label", key),
            "enabled": bool(meta.get("enabled")),
            "installed": installed,
            "status": status_text,
            "stage": stage,
            "active": active,
            "detail": detail,
            "probed_at": probed_at,
        }
    return result
