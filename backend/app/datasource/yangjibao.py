"""养基宝连接器 provider（阶段 7）。

定位（规范 §12.1）：养基宝为**非官方、无公开 API** 的手机 App，本项目对接其内部接口属
社区逆向调用，随时可能因加签/改接口而失效。调研01 §三的结论是「token 逆向接口建议废弃」，
因此本实现遵循三条原则：

1. **保留真实调用路径，但不内置任何签名盐**：`YJB_SECRET_NEW` / `YJB_SECRET_OLD` 只从环境变量读取，
   缺失时判定「未配置签名密钥」，绑定不可用，降级链自动落到拟合/ T+1 净值（不伪造成功）；
2. **token 安全**：落盘 `data/yjb_token.json` 前用 Windows DPAPI（CryptProtectData）加密，
   禁止进 Git、禁止下发前端、禁止写日志；非 Windows 或 DPAPI 不可用时拒绝落盘（不降级为明文）；
3. **合规**：仅本地个人研究；不提供加仓榜/实盘榜/减仓榜；盘中估值必须标注
   「非官方·拟合估算·仅供参考·不构成投资建议」。

签名算法（规范 §12.4，来源：社区逆向实现，非官方文档）：
- 新接口 `Request-Sign = md5(基址 + 路径 + token + SECRET_NEW + 时间戳)`，`Authorization = "ios:" + token`；
- 老接口 `Request-Sign = md5(路径 + token + 时间戳 + SECRET_OLD)`，`Authorization = token`；
- 公共头 `Request-Time = 时间戳` + 浏览器 UA。

本模块只做协议、存储与状态机，不拼装 HTTP 响应。
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import logging
import os
import platform
import secrets as py_secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from backend.app import config
from backend.app.core import netfix
from backend.app.core.errors import NonRetryableError
from backend.app.datasource import utils

netfix.apply_netfix()

logger = logging.getLogger(__name__)

YJB_TOKEN_FILE = config.DATA_DIR / "yjb_token.json"
YJB_STATE_FILE = config.DATA_DIR / "yjb_state.json"

BASE_NEW = os.environ.get("YJB_BASE_URL_NEW", "https://app-api.yangjibao.com").rstrip("/")
BASE_OLD = os.environ.get("YJB_BASE_URL_OLD", "http://browser-plug-api.yangjibao.com").rstrip("/")
SECRET_NEW = os.environ.get("YJB_SECRET_NEW", "")
SECRET_OLD = os.environ.get("YJB_SECRET_OLD", "")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 二维码轮询会话（进程内，2 秒一次；未绑定成功前不落盘）
_SESSIONS: Dict[str, Dict[str, Any]] = {}


# =============================================================
# 一、DPAPI 加密存储（token 不明文落盘）
# =============================================================
class _DataBlob(ctypes.Structure):
    """Windows DPAPI DATA_BLOB 结构。"""

    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi_available() -> bool:
    """仅 Windows 且 crypt32 可用时支持加密落盘。"""
    if platform.system() != "Windows":
        return False
    try:
        ctypes.windll.crypt32  # noqa: B018 - 探测可用性
        return True
    except Exception:  # noqa: BLE001
        return False


def _dpapi(protect: bool, data: bytes) -> bytes:
    """调用 DPAPI 加/解密（用户级，绑定当前 Windows 账户）。"""
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DataBlob()
    flags = 0x1 if protect else 0x0  # CRYPTPROTECT_UI_FORBIDDEN
    ok = crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, flags, ctypes.byref(blob_out)
    ) if protect else crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, flags, ctypes.byref(blob_out)
    )
    if not ok:
        raise OSError("DPAPI 操作失败")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _write_secure(path: Path, payload: Dict[str, Any]) -> None:
    """加密写入 JSON；不可加密时拒绝写明文（规范 §12.3）。"""
    if not _dpapi_available():
        raise NonRetryableError("当前系统不支持安全存储（DPAPI 不可用），已拒绝明文落盘")
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    encrypted = _dpapi(True, raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(b"YJB1" + base64.b64encode(encrypted))
    os.replace(temp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _read_secure(path: Path) -> Optional[Dict[str, Any]]:
    """读取加密 JSON；文件缺失/损坏/非本机账户加密时返回 None。"""
    if not path.exists():
        return None
    try:
        blob = path.read_bytes()
        if not blob.startswith(b"YJB1"):
            return None
        decrypted = _dpapi(False, base64.b64decode(blob[4:]))
        data = json.loads(decrypted.decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001 - 损坏按未绑定处理
        logger.warning("养基宝凭据读取失败：%s", type(exc).__name__)
        return None


# =============================================================
# 二、状态与合规（告知书 / 增强开关）
# =============================================================
def _load_state() -> Dict[str, Any]:
    """读取非敏感状态（告知书确认、增强通道开关）。"""
    if not YJB_STATE_FILE.exists():
        return {"consent": None, "enabled": False}
    try:
        with open(YJB_STATE_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {"consent": None, "enabled": False}
    except Exception:  # noqa: BLE001
        return {"consent": None, "enabled": False}


def _save_state(state: Dict[str, Any]) -> None:
    """保存非敏感状态（不含 token）。"""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp = YJB_STATE_FILE.with_suffix(".json.tmp")
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
    os.replace(temp, YJB_STATE_FILE)


def accept_consent(accepted: bool) -> Dict[str, Any]:
    """记录合规告知书确认；拒绝则同时关闭增强通道。"""
    state = _load_state()
    state["consent"] = {
        "accepted": bool(accepted),
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
    } if accepted else None
    if not accepted:
        state["enabled"] = False
    _save_state(state)
    return state


def set_enabled(enabled: bool) -> Dict[str, Any]:
    """开启/关闭增强通道；开启前必须已确认告知书。"""
    state = _load_state()
    if enabled and not (state.get("consent") or {}).get("accepted"):
        raise PermissionError("请先阅读并确认《养基宝 / 盘中估值 合规告知书》")
    state["enabled"] = bool(enabled)
    _save_state(state)
    return state


def token_path_exists() -> bool:
    """是否已存在 token 文件（不读取内容）。"""
    return YJB_TOKEN_FILE.exists()


def load_token() -> Optional[Dict[str, Any]]:
    """读取 token（仅后端使用，绝不返回前端）。"""
    return _read_secure(YJB_TOKEN_FILE)


def save_token(token: str, platform_name: str = "ios") -> None:
    """加密落盘 token。"""
    if not token:
        raise ValueError("token 不可为空")
    _write_secure(YJB_TOKEN_FILE, {
        "token": token,
        "platform": platform_name,
        "bound_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    logger.info("养基宝 token 已加密落盘（长度 %d，内容不记录）", len(token))


def clear_token() -> None:
    """清除本地 token（解绑 / 确定性失效）。"""
    if YJB_TOKEN_FILE.exists():
        try:
            YJB_TOKEN_FILE.unlink()
            logger.info("养基宝 token 已清除（原因：解绑或确定性失效）")
        except OSError as exc:
            logger.warning("养基宝 token 清除失败：%s", type(exc).__name__)


def secret_configured() -> bool:
    """签名盐是否已通过环境变量配置。"""
    return bool(SECRET_NEW or SECRET_OLD)


# =============================================================
# 三、协议调用（签名 + 四要件校验）
# =============================================================
def sign_new(path: str, token: str, timestamp: str) -> str:
    """新接口签名：md5(基址+路径+token+SECRET_NEW+时间戳)。"""
    if not SECRET_NEW:
        raise NonRetryableError("未配置 YJB_SECRET_NEW，无法调用养基宝新接口")
    raw = f"{BASE_NEW}{path}{token}{SECRET_NEW}{timestamp}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def sign_old(path: str, token: str, timestamp: str) -> str:
    """老接口签名：md5(路径+token+SECRET_OLD+时间戳)。"""
    if not SECRET_OLD:
        raise NonRetryableError("未配置 YJB_SECRET_OLD，无法调用养基宝老接口")
    raw = f"{path}{token}{SECRET_OLD}{timestamp}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _request(method: str, base: str, path: str, token: str = "", params: Optional[Dict[str, Any]] = None,
             use_old: bool = False) -> Dict[str, Any]:
    """带签名的请求；签名盐缺失/连接失败均转确定性异常，便于上层判定。"""
    timestamp = str(int(time.time() * 1000))
    sign = sign_old(path, token, timestamp) if use_old else sign_new(path, token, timestamp)
    headers = {
        "Request-Time": timestamp,
        "Request-Sign": sign,
        "Authorization": token if use_old else f"ios:{token}",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    response = requests.request(
        method, base + path, params=params, headers=headers,
        timeout=config.DATASOURCE_TIMEOUT,
    )
    return {"status": response.status_code, "text": response.text}


def verify_token_four_elements(token: str) -> Dict[str, Any]:
    """四要件校验：HTTP 200 + JSON 可解析 + code==200 + data 非空。

    Returns:
        dict: {ok, deterministic_invalid, reason}

    判定口径（规范 §12.3）：
    - 404 → 确定性无效（token 失效，可清除）；
    - 200 但 code!=200 或 data 空 → 无效（同样确定性，提示重绑）；
    - 连接异常/超时 → 非确定性（网络抖动，**不清 token**）。
    """
    try:
        result = _request("GET", BASE_OLD, "/user_account", token, use_old=True)
    except NonRetryableError as exc:
        return {"ok": False, "deterministic_invalid": False, "reason": str(exc)}
    except requests.exceptions.RequestException as exc:
        return {"ok": False, "deterministic_invalid": False, "reason": f"网络异常：{type(exc).__name__}"}

    status = result["status"]
    if status == 404:
        return {"ok": False, "deterministic_invalid": True, "reason": "登录态无效（404）"}
    if status != 200:
        return {"ok": False, "deterministic_invalid": False, "reason": f"HTTP {status}"}
    try:
        body = json.loads(result["text"])
    except json.JSONDecodeError:
        return {"ok": False, "deterministic_invalid": False, "reason": "响应非 JSON"}
    try:
        code = int(body.get("code"))
    except (TypeError, ValueError):
        return {"ok": False, "deterministic_invalid": False, "reason": "缺少 code 字段"}
    data = body.get("data")
    if code != 200 or not data:
        return {"ok": False, "deterministic_invalid": True, "reason": "登录态无效（code/data 不满足）"}
    return {"ok": True, "deterministic_invalid": False, "reason": "有效"}


def start_qrcode_session() -> Dict[str, Any]:
    """创建扫码会话：调用 /qr_code 并用 segno 本地生成二维码（base64）。"""
    if not secret_configured():
        raise NonRetryableError("未配置养基宝签名密钥（YJB_SECRET_NEW/OLD），扫码绑定不可用")
    result = _request("GET", BASE_OLD, "/qr_code", use_old=True)
    if result["status"] != 200:
        raise NonRetryableError(f"二维码接口不可用（HTTP {result['status']}）")
    try:
        body = json.loads(result["text"])
        data = body.get("data") or {}
        url = str(data.get("url") or "")
        session_id = str(data.get("id") or "")
    except Exception as exc:  # noqa: BLE001
        raise NonRetryableError("二维码接口返回格式异常") from exc
    if not url or not session_id:
        raise NonRetryableError("二维码接口未返回 url/id")

    try:
        import io

        import segno

        qr = segno.make(url)
        buffer = io.BytesIO()
        qr.save(buffer, kind="png", scale=6, border=2)
        image_base64 = base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception as exc:  # noqa: BLE001 - segno 缺失时仍返回 url，前端可降级
        logger.warning("二维码本地生成失败：%s", type(exc).__name__)
        image_base64 = ""

    _SESSIONS[session_id] = {"created_at": time.time(), "state": "waiting", "token": None}
    return {"session_id": session_id, "url": url, "image_base64": image_base64,
            "expires_in": 120}


def poll_session(session_id: str) -> Dict[str, Any]:
    """轮询扫码状态（幂等：同一会话成功一次后固定返回 success）。

    状态机：waiting（等待扫码）→ scanning（已扫码待确认）→ success（拿到 token）
           → expired（二维码失效）→ timeout（本地会话超时）。
    """
    session = _SESSIONS.get(session_id)
    if not session:
        return {"state": "expired", "message": "二维码会话不存在或已失效，请刷新重扫"}
    if session["state"] == "success":
        return {"state": "success", "message": "绑定成功", "bound": True}
    if time.time() - session["created_at"] > 120 and session["state"] != "success":
        session["state"] = "timeout"
        return {"state": "timeout", "message": "二维码已超时，请刷新重扫"}

    try:
        result = _request("GET", BASE_OLD, f"/qr_code_state/{session_id}", use_old=True)
    except NonRetryableError as exc:
        return {"state": "error", "message": str(exc)}
    except requests.exceptions.RequestException as exc:
        # 网络抖动：保持当前状态，不清会话，前端继续轮询
        return {"state": session["state"], "message": f"网络异常，稍后重试（{type(exc).__name__}）"}

    if result["status"] == 404:
        session["state"] = "expired"
        return {"state": "expired", "message": "二维码已失效，请刷新重扫"}
    try:
        body = json.loads(result["text"])
    except json.JSONDecodeError:
        return {"state": session["state"], "message": "状态响应异常，稍后重试"}

    code = body.get("code")
    data = body.get("data") or {}
    token = str(data.get("token") or "")
    if token:
        session["state"] = "success"
        session["token"] = token
        return {"state": "success", "message": "绑定成功", "bound": False}
    state_code = str(data.get("state") or data.get("status") or "")
    if state_code in ("2", "scanned", "SCANNED"):
        session["state"] = "scanning"
        return {"state": "scanning", "message": "已扫码，请在 App 内确认"}
    return {"state": "waiting", "message": "等待扫码"}


def confirm_bind(session_id: str) -> Dict[str, Any]:
    """取会话内 token，四要件复核后加密落盘。"""
    session = _SESSIONS.get(session_id)
    if not session or session.get("state") != "success" or not session.get("token"):
        raise PermissionError("尚未扫码成功，无法绑定")
    token = str(session["token"])
    check = verify_token_four_elements(token)
    if not check["ok"]:
        raise PermissionError(f"token 校验失败：{check['reason']}")
    save_token(token, "ios")
    session["token"] = None  # 内存中也不长期保留
    return {"bound": True, "platform": "ios"}


def fetch_increase_rate(fund_id: str) -> Dict[str, Any]:
    """新接口实时涨幅（一级通道）。"""
    result = _request("GET", BASE_NEW, "/market/v1/fund/increase-rate",
                      token=(load_token() or {}).get("token", ""), params={"fund_id": fund_id})
    return _parse_estimate(result.get("text"), "养基宝/increase-rate")


def fetch_gz_data(fund_id: str) -> Dict[str, Any]:
    """新接口估值明细（一级通道备选）。"""
    result = _request("GET", BASE_NEW, "/market/v1/fund/gz-data",
                      token=(load_token() or {}).get("token", ""), params={"fund_id": fund_id})
    return _parse_estimate(result.get("text"), "养基宝/gz-data")


def _parse_estimate(text: str, source: str) -> Dict[str, Any]:
    """解析估值响应；缺字段即报错，不猜测。"""
    try:
        body = json.loads(text)
    except json.JSONDecodeError as exc:
        raise NonRetryableError(f"{source} 返回非 JSON") from exc
    data = body.get("data") or {}
    pct = data.get("increase_rate", data.get("rate", data.get("gz")))
    if pct is None:
        raise NonRetryableError(f"{source} 未返回涨幅字段")
    try:
        value = float(str(pct).replace("%", ""))
    except ValueError as exc:
        raise NonRetryableError(f"{source} 涨幅字段无法解析") from exc
    return {"pct": value, "source": source, "as_of": time.strftime("%Y-%m-%d %H:%M:%S")}


def search_fund_id(code: str) -> str:
    """按基金代码换取养基宝 fund_id（新接口搜索）。"""
    token = (load_token() or {}).get("token", "")
    if not token:
        raise NonRetryableError("未绑定养基宝，无法搜索 fund_id")
    result = _request("GET", BASE_NEW, "/market/v1/fund/search", token, params={"keyword": code})
    if result["status"] != 200:
        raise NonRetryableError(f"养基宝搜索不可用（HTTP {result['status']}）")
    try:
        body = json.loads(result["text"])
    except json.JSONDecodeError as exc:
        raise NonRetryableError("养基宝搜索返回非 JSON") from exc
    for item in (body.get("data") or {}).get("list") or []:
        if str(item.get("fd_code") or "") == str(code):
            return str(item.get("fund_id") or item.get("id") or "")
    raise NonRetryableError(f"养基宝未找到该基金：{code}")


def status() -> Dict[str, Any]:
    """连接器状态（不含 token 内容）。"""
    state = _load_state()
    token = load_token()
    return {
        "bound": bool(token and token.get("token")),
        "bound_at": (token or {}).get("bound_at"),
        "platform": (token or {}).get("platform"),
        "consent": state.get("consent"),
        "enabled": bool(state.get("enabled")),
        "secret_configured": secret_configured(),
        "secure_storage": _dpapi_available(),
        "channel_available": bool(token and token.get("token") and secret_configured()),
        "notice": "非官方通道：接口易变、可能随时失效；仅供本地个人研究，不构成投资建议",
    }


def new_session_id() -> str:
    """生成备用会话 id（二维码接口不可用时不使用）。"""
    return py_secrets.token_hex(8)
