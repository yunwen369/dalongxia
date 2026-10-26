"""破坏性操作的脱敏审计日志（RT-01 整改配套）。

第四方扫描器误打空 body 曾清空本机养基宝绑定，事后无从追溯「谁在什么时候清掉的」。
本模块为所有不可逆/状态变更操作留一条可核验的本地痕迹：

- **只记事实，不记凭据**：动作名、结果、状态位（如 ``bound`` / ``secret_configured``）
  与时间；token、签名盐、新旧盐值等敏感字段按 key 名一律替换为 ``***``，
  即使审计文件被他人读到也无法据此重放或冒用。
- **失败不阻断业务**：审计写盘异常只记 warning，绝不让「解绑」因为日志不可写而失败。
- **追加写、一行一条**：JSONL 便于 grep 与脚本核验，不做轮转删除（体积按次增长，单条 < 300B）。

存放位置：``<DATA_DIR>/audit/yjb_audit.jsonl``（冻结态即 EXE 同级 ``data/audit/``）。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, Optional

from backend.app import config
from backend.app.datasource import utils

logger = logging.getLogger(__name__)

AUDIT_DIR = config.DATA_DIR / "audit"
AUDIT_FILE = AUDIT_DIR / "yjb_audit.jsonl"

# 命中即整值打码的敏感键（子串匹配，覆盖 token / secret / sign / authorization 等）
_SENSITIVE_KEYS = ("token", "secret", "password", "passwd", "sign", "cookie", "authorization")

# 键名完全相等才认为是敏感值的短词（"new"/"old" 是 set_secret 的入参名，
# 若用子串匹配会误伤 "renew_count" 这类正常字段，故只做精确匹配）
_EXACT_SENSITIVE_KEYS = frozenset({"new", "old"})

# 状态位白名单：键名虽含敏感子串（如 secret_configured 含 "secret"），但值是布尔/枚举，
# 属于审计必须保留的判断依据（「被拒时到底有没有配过盐」）。此前被误打码成 *** 已修正。
_STATUS_KEYS = frozenset({
    "secret_configured", "secret_source", "consent_accepted",
    "bound", "enabled", "cleared", "was_bound",
})

# 单条 detail 里字符串值的最大保留长度（防止把整段响应误写进审计）
_MAX_TEXT = 120


def _is_sensitive(key: str) -> bool:
    """判断字段名是否属于敏感键。

    规则（按优先级）：
    1. 状态位白名单（``secret_configured`` 等）一律**不打码**——它们不含凭据值；
    2. ``new`` / ``old`` 精确匹配（短词，子串匹配误伤面过大）；
    3. 其余按子串匹配，覆盖 ``token`` / ``secret_key`` / ``X-Authorization`` 等各种拼写。

    Args:
        key: 字段名。

    Returns:
        bool: 属敏感键返回 True（其值必须打码）。
    """
    text = str(key).strip()
    lowered = text.lower()
    if lowered in _STATUS_KEYS:
        return False
    if lowered in _EXACT_SENSITIVE_KEYS:
        return True
    return any(word in lowered for word in _SENSITIVE_KEYS)


def _redact(value: Any, depth: int = 0) -> Any:
    """递归脱敏：敏感键整值打码，长文本截断，容器限深。

    Args:
        value: 待脱敏的值。
        depth: 当前递归深度（超过 3 层直接返回类型名，防止环引用与体积膨胀）。

    Returns:
        Any: 脱敏后的值（dict/list/标量）。
    """
    if depth > 3:
        return f"<{type(value).__name__}>"
    if isinstance(value, dict):
        return {
            str(k): ("***" if _is_sensitive(k) else _redact(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item, depth + 1) for item in value]
    if isinstance(value, str):
        return value if len(value) <= _MAX_TEXT else value[:_MAX_TEXT] + "…"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return f"<{type(value).__name__}>"


def record(action: str, result: str, detail: Optional[Dict[str, Any]] = None,
           actor: str = "local-api") -> bool:
    """追加一条脱敏审计记录（失败不抛异常，不影响调用方业务）。

    Args:
        action: 动作标识，如 ``yjb.unbind`` / ``yjb.secret.clear``。
        result: 结果标识，如 ``ok`` / ``rejected:missing-confirm``。
        detail: 附加上下文（状态位等）；敏感键自动打码。
        actor: 触发来源（本应用为本地 HTTP 接口，固定 ``local-api``）。

    Returns:
        bool: 写入成功返回 True；失败返回 False（仅记 warning）。
    """
    entry = {
        "ts": utils.now_text(),
        "action": str(action),
        "result": str(result),
        "actor": str(actor),
        "detail": _redact(detail or {}),
    }
    try:
        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True
    except OSError as exc:  # 磁盘只读/权限不足等：不阻断业务
        logger.warning("审计日志写入失败（动作=%s，结果=%s）：%s", action, result, type(exc).__name__)
        return False


def tail(limit: int = 20) -> Iterable[Dict[str, Any]]:
    """读取最近的审计记录（供自检脚本/门禁取证使用）。

    Args:
        limit: 最多返回条数（从文件尾部取）。

    Returns:
        Iterable[dict]: 审计条目列表（新的在前）；文件缺失时为空列表。
    """
    if not AUDIT_FILE.exists():
        return []
    try:
        lines = AUDIT_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(out) >= limit:
            break
    return out
