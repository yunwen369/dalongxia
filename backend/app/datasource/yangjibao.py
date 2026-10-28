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

接口契约（2026-10-25 依据 GitHub 公开实现 `zwls666/yangjibao-edge-extension` 对齐，并已实测签名通过）：
- 基址：`https://browser-plug-api.yangjibao.com`（原 `wxapp-api` 小程序通道已废弃，会 403）
- 签名：`Request-Sign = md5(去查询串 path + token + 秒级 Request-Time + SECRET)`，
  `Authorization` 直接放**原始 token**（无 `ios:` 前缀）；早期假设的「含基址」「毫秒时间戳」均已证伪
- `GET /qr_code`            → `data.url` / `data.id`（实测：`{"code":200,"data":{"url":"http://weixin.qq.com/q/…","id":"loginQrId…"}}`）
- `GET /qr_code_state/{id}` → `data.state`：`"2"` 且带 `data.token` = 登录成功；`"3"` = 二维码失效
- `GET /user_account`       → `data` 为数组或 `{list|accounts}`，账户 id 取 `id` / `account_id`
- `GET /fund_hold?account_id=` → 持仓（`fund_id` / `money` / `hold_earn` 等）
- `GET /income_data?account_id=` → 估值列表（`fund_id` / `code` / `short_name`，估算涨跌幅在 `nv_info.vgszzl`）

仍待实测确认（非阻塞：见下方「未确认项登记」，共 6 项，**不得按猜测补全**）。

### 未确认项登记（与接口说明书 §5.2 同步，改一处须改另一处）

以下 6 项在 `养基宝接口文档_GitHub调研_20261025.md` §五 中被明确标注为「未找到 / 需抓包」，
本工程**没有任何代码假装知道答案**：凡是依赖这些未知项的判断，一律走「宁可显示无数据，
不猜字段、不补零、不沿用上一交易日」的保守分支。

1. **token 精确有效期与失效码值**：实测 17h 仍有效，上限未知；仅按「HTTP 401 或 code≠200」判失效。
2. **`/income_line_data` 真实字段**：仅见于 S1 源码，未本机实测；本工程未接入。
3. **增删持仓字段**（`POST /fund_hold`、`DELETE /remove_fund_hold`）：同上；本工程只读，不做写持仓。
4. **`/version_info` 必需参数**：裸调 400「必要参数缺失」，参数名未逆向出；未接入任何业务路径。
5. **非交易时段 `vgszzl` 是否置空**：仅知周六夜间 `gszzl` 仍有值；取值链已兼容任一为空，
   空即返回 `None`（前端显示「无数据」），不上溯旧值。
6. **新通道 app-api 可用性**：`https://app-api.yangjibao.com` 本轮未验证可达性；不做切换尝试，
   `sign_new` 保留但标注「未启用」。
   （另：`zwls666/yangjibao-edge-extension` 仓库 URL 未直接命中，但同算法/同 SECRET/同端点
   已由 S1 源码 + 本机实测双重坐实，不影响结论。）

本模块只做协议、存储与状态机，不拼装 HTTP 响应。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from backend.app import config
from backend.app.core import atomic, netfix, secure_store
from backend.app.datasource import utils
from backend.app.core.errors import NonRetryableError
netfix.apply_netfix()

logger = logging.getLogger(__name__)


class YjbUnauthorizedError(NonRetryableError):
    """HTTP 401 或业务 code=401：授权凭据失效，需重新扫码。

    接口文档 §3.4 明确「401 → 未授权，请重新登录（token 失效需重扫）」，
    且失效判据为 **401 或 code!=200**（无 refresh_token，只能重扫）。
    路由层据此返回 ``UNAUTHORIZED``，前端展示「重新扫码」引导。
    """


class YjbTimeoutError(NonRetryableError):
    """HTTP 408 或 requests 超时：请求超时，可稍后重试（不清 token）。"""


class YjbRateLimitError(NonRetryableError):
    """HTTP 429：触发限频（S1 风控），提示「请求频繁，请稍后再试」。"""


class YjbNotConfiguredError(NonRetryableError):
    """本地尚未完成授权配置（缺 token/盐）：属「配置缺失」而非「上游故障」。

    首启向导场景若沿用通用「接口不可用」文案，会与页面上方的三步引导自相矛盾
    （东西没坏，只是还没配），故单列一类，由路由层映射 ``NOT_CONFIGURED``。
    """


# 接口文档 §3.4 的状态码 → (异常类, 中文文案)；未列出的状态码仍走通用 UPSTREAM_ERROR。
# 文案直接取自文档原文，避免自创措辞导致前端提示与文档不一致。
_STATUS_HINTS: Dict[int, Tuple[type, str]] = {
    401: (YjbUnauthorizedError, "未授权，请重新登录（授权凭据已失效，请在养基宝页重新扫码）"),
    408: (YjbTimeoutError, "请求超时，请稍后重试。"),
    429: (YjbRateLimitError, "请求频繁，请稍后再试。"),
}

YJB_TOKEN_FILE = config.DATA_DIR / "yjb_token.json"
YJB_STATE_FILE = config.DATA_DIR / "yjb_state.json"
# 签名盐本地加密配置（DPAPI + YJB2 魔数）；环境变量优先，文件仅作本机注入通道
YJB_SECRET_FILE = config.DATA_DIR / "yjb_secret.json"
_SECRET_MAGIC = b"YJB2"

BASE_NEW = os.environ.get("YJB_BASE_URL_NEW", "https://app-api.yangjibao.com").rstrip("/")
# 老接口默认仍为 http 明文（社区逆向得到的唯一入口）；运行时优先尝试 https，
# 失败再回落 http 并在 status.transport 中如实标注「明文风险」。
BASE_OLD = os.environ.get("YJB_BASE_URL_OLD", "http://browser-plug-api.yangjibao.com").rstrip("/")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 二维码会话有效期（秒）：真实实现说明「二维码 4 分钟内有效」，故取 240，
# 后端与前端轮询次数共用同一来源，避免两处硬编码漂移
QRCODE_TTL = 240
# 二维码轮询会话（进程内；未绑定成功前 token 不落盘）
#
# R4 多 worker 评估：本工具为**本地单用户桌面形态**（desktop_shell 以单进程 uvicorn
# 启动、不指定 workers），二维码会话只在扫码这 120 秒内需要跨请求共享，因此进程内
# dict 已满足；若未来改为多 worker/多实例部署，必须改为 `data/yjb_state.json` 或
# Redis 等共享存储，否则轮询会落到没有会话的 worker 上并恒返回 expired。
# 为防长时间运行后无界增长，每次创建新会话时按 TTL 清理过期项并限制总量。
_SESSIONS: Dict[str, Dict[str, Any]] = {}
_SESSION_MAX = 32                 # 同时存在的会话上限（本地单用户足够）
_SESSION_KEEP = QRCODE_TTL * 2    # 会话最长保留：2×TTL（成功会话留给 /bind 使用）
# 老接口实际可用的传输方案（进程内记忆，避免每次请求都先试 https 再回落）
_OLD_SCHEME: Dict[str, str] = {}
# 统一网络会话（trust_env=False + 重试），惰性创建
_SESSION: Optional[requests.Session] = None


def _purge_sessions() -> None:
    """清理过期二维码会话，并限制会话总量（防进程内 dict 无界增长）。

    保留策略：`created_at` 超过 `2×QRCODE_TTL` 的一律删除；总量超上限时按创建
    时间淘汰最旧的。成功会话在保留窗内仍可被 `/bind` 使用。
    """
    now = time.time()
    stale = [
        sid for sid, item in _SESSIONS.items()
        if now - float(item.get("created_at") or 0) > _SESSION_KEEP
    ]
    for sid in stale:
        _SESSIONS.pop(sid, None)
    if len(_SESSIONS) > _SESSION_MAX:
        ordered = sorted(_SESSIONS.items(), key=lambda kv: float(kv[1].get("created_at") or 0))
        for sid, _ in ordered[: len(_SESSIONS) - _SESSION_MAX]:
            _SESSIONS.pop(sid, None)


# =============================================================
# 一、DPAPI 加密存储（token 不明文落盘）
# =============================================================
# DPAPI 实现已收口到 backend.app.core.secure_store（P1-15：AI Key 与 token 共用同一机制）。
# 这里保留同名薄封装，避免调用点与既有测试改动。
_DataBlob = secure_store._DataBlob  # noqa: SLF001 - 兼容历史引用


def _dpapi_available() -> bool:
    """当前环境是否支持 DPAPI 加密（Windows + crypt32）。

    Returns:
        bool: 支持返回 True。
    """
    return secure_store.available()


def _dpapi(protect: bool, data: bytes) -> bytes:
    """调用 DPAPI 加/解密（薄封装，实现见 core.secure_store）。

    Args:
        protect: True=加密，False=解密。
        data: 待处理字节。

    Returns:
        bytes: DPAPI 处理后的字节（protect=True 为密文，False 为明文）。

    Raises:
        OSError: DPAPI 调用失败。
    """
    return secure_store._dpapi(protect, data)  # noqa: SLF001 - 复用唯一实现


def _write_secure(path: Path, payload: Dict[str, Any]) -> None:
    """加密写入 token 文件（YJB1 格式，DPAPI 不可用时拒绝明文落盘）。

    Args:
        path: 目标文件路径。
        payload: 待落盘字典。

    Raises:
        NonRetryableError: DPAPI 不可用，按规范拒绝明文落盘。
    """
    try:
        secure_store.write_secure(path, payload, b"YJB1")
    except OSError as exc:
        raise NonRetryableError(f"{exc}，已拒绝明文落盘") from exc


def _read_secure(path: Path) -> Optional[Dict[str, Any]]:
    """读取 YJB1 加密文件；缺失/损坏/非本机账户加密时返回 None。

    Args:
        path: 文件路径。

    Returns:
        Optional[Dict[str, Any]]: 解密后的字典或 None。
    """
    data = secure_store.read_secure(path, b"YJB1")
    if data is None and path.exists():
        logger.warning("养基宝凭据读取失败：文件损坏或非本机账户加密")
    return data


# =============================================================
# 二、状态与合规（告知书 / 增强开关）
# =============================================================
def _load_state() -> Dict[str, Any]:
    """读取非敏感状态（告知书确认、增强通道开关）。

    Returns:
        dict: 本地状态字典 `{consent, enabled}`（非统一信封，仅内部使用）。
    """
    if not YJB_STATE_FILE.exists():
        return {"consent": None, "enabled": False}
    try:
        with open(YJB_STATE_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {"consent": None, "enabled": False}
    except Exception:  # noqa: BLE001
        return {"consent": None, "enabled": False}


def _save_state(state: Dict[str, Any]) -> None:
    """保存非敏感状态（不含 token）。

    Args:
        state: 状态对象（读改写后原样返回）。
    """
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    # 原子写（R3-09 统一助手）：避免进程中断留下半截 JSON
    atomic.atomic_write_json(YJB_STATE_FILE, state)


def accept_consent(accepted: bool) -> Dict[str, Any]:
    """记录合规告知书确认；拒绝则同时关闭增强通道。

    Args:
        accepted: 是否已确认合规告知书。

    Returns:
        dict: 更新后的本地状态 `{consent, enabled}`（由路由层再包统一信封）。
    """
    state = _load_state()
    state["consent"] = {
        "accepted": bool(accepted),
        "at": utils.now_text(),
    } if accepted else None
    if not accepted:
        state["enabled"] = False
    _save_state(state)
    return state


def set_enabled(enabled: bool) -> Dict[str, Any]:
    """开启/关闭增强通道；开启前必须已确认告知书。

    Args:
        enabled: 是否启用。

    Returns:
        dict: 更新后的本地状态 `{consent, enabled}`（由路由层再包统一信封）。

    Raises:
        PermissionError: 请先阅读并确认《养基宝 / 盘中估值 合规告知书》。
    """
    state = _load_state()
    if enabled and not (state.get("consent") or {}).get("accepted"):
        raise PermissionError("请先阅读并确认《养基宝 / 盘中估值 合规告知书》")
    state["enabled"] = bool(enabled)
    _save_state(state)
    return state


def load_token() -> Optional[Dict[str, Any]]:
    """读取 token（仅后端使用，绝不返回前端）。

    Returns:
        dict | None: 命中时返回记录，缺失返回 None。
    """
    return _read_secure(YJB_TOKEN_FILE)


def save_token(token: str, platform_name: str = "ios") -> None:
    """加密落盘 token。

    Args:
        token: 访问令牌（敏感信息，不落日志）。
        platform_name: 平台名称。

    Raises:
        ValueError: token 不可为空。
    """
    if not token:
        raise ValueError("token 不可为空")
    _write_secure(YJB_TOKEN_FILE, {
        "token": token,
        "platform": platform_name,
        "bound_at": utils.now_text(),
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


def _load_secret() -> Tuple[str, str]:
    """读取签名盐（环境变量优先，其次本地 DPAPI 加密配置）。

    盐属凭据：只允许从环境变量或 DPAPI 加密文件读取，**不得**写入代码、
    日志、前端或 Git。读取顺序保证运维可用环境变量覆盖本地配置。

    Returns:
        Tuple[str, str]: (新接口盐, 老接口盐)；未配置时均为空串。
    """
    env_new = (os.environ.get("YJB_SECRET_NEW") or "").strip()
    env_old = (os.environ.get("YJB_SECRET_OLD") or "").strip()
    if env_new or env_old:
        return env_new, env_old
    data = secure_store.read_secure(YJB_SECRET_FILE, _SECRET_MAGIC) or {}
    return str(data.get("new") or "").strip(), str(data.get("old") or "").strip()


def secret_source() -> Optional[str]:
    """签名盐来源（用于前端提示「盐从哪来」，绝不返回值本身）。

    Returns:
        Optional[str]: `环境变量` / `本地加密配置` / None（未配置）。
    """
    env_new = (os.environ.get("YJB_SECRET_NEW") or "").strip()
    env_old = (os.environ.get("YJB_SECRET_OLD") or "").strip()
    if env_new or env_old:
        return "环境变量"
    if YJB_SECRET_FILE.exists():
        new_secret, old_secret = _load_secret()
        if new_secret or old_secret:
            return "本地加密配置"
    return None


def set_secret(new_secret: str, old_secret: str) -> bool:
    """把签名盐以 DPAPI 加密写入本机配置（不落明文、不回显）。

    Args:
        new_secret: 新接口签名盐（可空）。
        old_secret: 老接口签名盐（可空）。

    Returns:
        bool: 是否写入成功；盐全为空时清除本地配置并返回 False。

    Raises:
        NonRetryableError: DPAPI 不可用（拒绝明文落盘）。
    """
    new_value = (new_secret or "").strip()
    old_value = (old_secret or "").strip()
    if not (new_value or old_value):
        clear_secret()
        return False
    try:
        secure_store.write_secure(YJB_SECRET_FILE, {"new": new_value, "old": old_value}, _SECRET_MAGIC)
    except OSError as exc:
        raise NonRetryableError(f"{exc}，已拒绝明文落盘签名盐") from exc
    return True


def clear_secret() -> None:
    """清除本地加密的签名盐（解除配置即回到「待配置」态）。"""
    if YJB_SECRET_FILE.exists():
        try:
            YJB_SECRET_FILE.unlink()
            logger.info("养基宝签名盐已清除（本地加密配置）")
        except OSError as exc:
            logger.warning("养基宝签名盐清除失败：%s", type(exc).__name__)


def secret_configured() -> bool:
    """签名盐是否已配置（环境变量或本地加密配置任一命中）。

    Returns:
        bool: 判定结果。
    """
    new_secret, old_secret = _load_secret()
    return bool(new_secret or old_secret)


# =============================================================
# 三、协议调用（签名 + 四要件校验）
# =============================================================
def sign_new(path: str, token: str, timestamp: str) -> str:
    """新接口签名：md5(去查询串 path + token + 秒级时间戳 + SECRET_NEW)。

    ⚠️ 当前**未启用**（YJB-04）：本工程全部业务走旧通道 browser-plug-api
    （`sign_old` + 裸 token Authorization + Chrome UA，已实测 200）。
    本函数不得用于旧通道；若将来切换 app-api 新通道，必须按
    《养基宝接口文档_GitHub调研_20261025.md》§3.2 订正为：
      `md5(NEW_API_BASE + path + token + NEW_SECRET + ts)`
    且 `Authorization: ios:<token>`、UA 伪装 `YJB/2.0.13 … Alamofire`，
    与当前实现（不含基址、无 ios: 前缀、浏览器 UA）**三处都不同**。
    新通道域名 https://app-api.yangjibao.com 的可达性本轮未验证（登记为
    "未找到/需抓包"项），切换前必须重新实测，不得据本函数直接上线。

    Args:
        path: 接口路径（以 / 开头的相对路径）。
        token: 访问令牌（敏感信息，不落日志）。
        timestamp: 秒级时间戳字符串。

    Returns:
        str: 32 位小写十六进制 MD5 签名。

    Raises:
        NonRetryableError: 未配置 YJB_SECRET_NEW，无法调用养基宝新接口。
    """
    secret_new, _ = _load_secret()
    if not secret_new:
        raise NonRetryableError("未配置 YJB_SECRET_NEW，无法调用养基宝新接口")
    # ⚠️ YJB-04：以下拼接是**早期社区逆袭实现的写法，不是 app-api 的权威算法**，且当前无任何调用方
    # （全工程 use_old=True）。app-api 权威实现为 md5(NEW_API_BASE + path + token + NEW_SECRET + ts)
    # ——多一段基址前缀；仅此一处差异就会导致签名全错（表现为上游 401/签名无效）。
    # 若将来切换新通道，除了补基址，还必须同时改 Authorization 为 "ios:" + token、UA 伪装
    # "YJB/2.0.13 … Alamofire"，三处一起改，缺一不可。详见说明书 §3.2 与 §五「未闭环」。
    plain_path = path.split("?")[0]
    raw = f"{plain_path}{token}{timestamp}{secret_new}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def sign_old(path: str, token: str, timestamp: str) -> str:
    """老接口签名：md5(去查询串的路径 + token + 秒级时间戳 + SECRET_OLD)。

    Args:
        path: 接口路径（以 / 开头的相对路径）。
        token: 访问令牌（敏感信息，不落日志）。
        timestamp: 秒级时间戳字符串。

    Returns:
        str: 32 位小写十六进制 MD5 签名。

    Raises:
        NonRetryableError: 未配置 YJB_SECRET_OLD，无法调用养基宝老接口。
    """
    _, secret_old = _load_secret()
    if not secret_old:
        raise NonRetryableError("未配置 YJB_SECRET_OLD，无法调用养基宝老接口")
    raw = f"{path.split('?')[0]}{token}{timestamp}{secret_old}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _http_session() -> requests.Session:
    """统一的养基宝请求会话（trust_env=False + 重试，与其它数据源一致）。

    Returns:
        requests.Session: 进程内复用同一会话，避免每次请求重建连接池。
    """
    global _SESSION
    if _SESSION is None:
        _SESSION = netfix.make_session()
        _SESSION.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return _SESSION


def _old_base_candidates() -> list:
    """老接口可尝试的基址（https 优先，http 回落）。

    老接口历史只提供 http 明文；若源站支持 https 则优先走加密通道，
    避免 Authorization 头在明文链路传输（安全登记见接口说明书）。

    Returns:
        list[str]: 待尝试的基址列表，顺序即优先级。
    """
    if BASE_OLD.startswith("https://"):
        return [BASE_OLD]
    cached = _OLD_SCHEME.get("base")
    https_base = "https://" + BASE_OLD.split("://", 1)[-1]
    if cached == BASE_OLD:
        # 已确认 https 不可用，直接走 http（避免每次多一次失败请求）
        return [BASE_OLD]
    return [https_base, BASE_OLD]


def _status_error(status: Any, path: str) -> NonRetryableError:
    """把 HTTP 状态码映射为带中文文案的确定性异常（接口文档 §3.4）。

    Args:
        status: 上游 HTTP 状态码（可能为 int 或字符串）。
        path: 接口路径（仅用于通用文案，日志与提示均不含 token）。

    Returns:
        NonRetryableError: 401/408/429 返回对应子类实例；其余返回通用实例。
    """
    try:
        code = int(status)
    except (TypeError, ValueError):
        code = 0
    hint = _STATUS_HINTS.get(code)
    tail = path.split("?")[0]
    if hint:
        exc_cls, message = hint
        return exc_cls(message)
    return NonRetryableError(f"养基宝接口不可用（HTTP {status}，{tail}）")


def _request(method: str, base: str, path: str, token: str = "", params: Optional[Dict[str, Any]] = None,
             use_old: bool = False) -> Dict[str, Any]:
    """带签名的请求；签名盐缺失/连接失败均转确定性异常，便于上层判定。

    Args:
        method: HTTP 方法。
        base: 接口基址（新接口 https / 老接口 http）。
        path: 接口路径。
        token: 访问令牌（敏感信息，不落日志）。
        params: 请求参数（查询串键值对）。
        use_old: 是否使用旧密钥（密钥轮换期）。

    Returns:
        dict: `{status, text, base, scheme}`；status 为 HTTP 状态码，text 为响应正文。

    Raises:
        NonRetryableError: 签名盐未配置时抛出（不发请求）。
        requests.exceptions.RequestException: 网络层异常，交由调用方判定是否清 token。
    """
    # 真实实现用**秒级**时间戳（社区实现 Math.floor(Date.now()/1000)），毫秒会签名不匹配
    timestamp = str(int(time.time()))
    sign = sign_old(path, token, timestamp) if use_old else sign_new(path, token, timestamp)
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json;charset=UTF-8",
        "Request-Time": timestamp,
        "Request-Sign": sign,
        # 真实实现直接放原始 token；早期猜测的 "ios:" 前缀只在微信小程序通道使用
        "Authorization": token,
    }
    session = _http_session()
    bases = _old_base_candidates() if use_old else [base]
    last_error: Optional[Exception] = None
    for index, candidate in enumerate(bases):
        try:
            response = session.request(
                method, candidate + path, params=params, headers=headers,
                timeout=config.DATASOURCE_TIMEOUT,
            )
        except requests.exceptions.RequestException as exc:
            last_error = exc
            # https 失败且还有 http 备选时继续尝试，其余情况跳出循环统一分流
            if index + 1 < len(bases):
                logger.info("养基宝老接口 https 不可用，回落 http 明文（原因：%s）", type(exc).__name__)
                continue
            break
        if candidate.startswith("https://"):
            # 记录**成功的 https 基址**（不能记 BASE_OLD，否则状态会误报「仅 http 明文」）
            _OLD_SCHEME["base"] = candidate
            logger.info("养基宝老接口 https 通道可用（已登记进程内复用）")
        else:
            _OLD_SCHEME["base"] = candidate
        return {"status": response.status_code, "text": response.text,
                "base": candidate, "scheme": candidate.split("://", 1)[0]}
    if isinstance(last_error, requests.exceptions.Timeout):
        # 文档 §3.4：超时按「请求超时」中文提示；不清 token（非确定性失效）
        raise YjbTimeoutError("请求超时，请稍后重试。") from last_error
    raise last_error or requests.exceptions.ConnectionError("养基宝老接口不可达")


def verify_token_four_elements(token: str) -> Dict[str, Any]:
    """四要件校验：HTTP 200 + JSON 可解析 + code==200 + data 非空。

    Returns:
        dict: {ok, deterministic_invalid, reason}

    判定口径（规范 §12.3）：
    - 404 → 确定性无效（token 失效，可清除）；
    - 200 但 code!=200 或 data 空 → 无效（同样确定性，提示重绑）；
    - 连接异常/超时 → 非确定性（网络抖动，**不清 token**）。

    Args:
        token: 访问令牌（敏感信息，不落日志）。
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
    """创建扫码会话：调用 /qr_code 并用 segno 本地生成二维码（base64）。

    Returns:
        dict: `{session_id, qrcode, expires_in, status}`（二维码 base64 与本地会话标识）。

    Raises:
        NonRetryableError: 未配置养基宝签名密钥（YJB_SECRET_NEW/OLD），扫码绑定不可用。
    """
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

    _purge_sessions()
    _SESSIONS[session_id] = {"created_at": time.time(), "state": "waiting", "token": None}
    return {"session_id": session_id, "url": url, "image_base64": image_base64,
            "expires_in": QRCODE_TTL}


def poll_session(session_id: str) -> Dict[str, Any]:
    """轮询扫码状态（幂等：同一会话成功一次后固定返回 success）。

    状态机：waiting（等待扫码）→ scanning（已扫码待确认）→ success（拿到 token）
           → expired（二维码失效）→ timeout（本地会话超时）。

    Args:
        session_id: 会话标识。

    Returns:
        dict: `{state, bound?, reason?}`；state ∈ waiting/scanning/success/expired/timeout。
    """
    session = _SESSIONS.get(session_id)
    if not session:
        return {"state": "expired", "message": "二维码会话不存在或已失效，请刷新重扫"}
    if session["state"] == "success":
        return {"state": "success", "message": "绑定成功", "bound": True}
    if time.time() - session["created_at"] > QRCODE_TTL and session["state"] != "success":
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

    # R3-24：原 `code = body.get("code")` 为无用局部变量（轮询只认 data.state/token），已删
    data = body.get("data") or {}
    token = str(data.get("token") or "")
    state_code = str(data.get("state") if data.get("state") is not None else data.get("status") or "")
    # 真实契约（GitHub 公开实现，已实测对齐）：
    #   state == "2" 且带 token → 登录成功（token 即用户凭据）
    #   state == "3"            → 二维码失效，需重新生成
    #   其余                    → 等待扫码 / 已扫码待确认
    if state_code == "2" and token:
        session["state"] = "success"
        session["token"] = token
        return {"state": "success", "message": "扫码成功", "bound": False}
    if state_code == "3":
        session["state"] = "expired"
        return {"state": "expired", "message": "二维码已失效，请重新生成"}
    if token:
        # 兜底：个别版本先下发 token 而 state 未及时更新
        session["state"] = "success"
        session["token"] = token
        return {"state": "success", "message": "扫码成功", "bound": False}
    if state_code in ("1", "0", ""):
        return {"state": "waiting", "message": "等待扫码"}
    session["state"] = "scanning"
    return {"state": "scanning", "message": "已扫码，请在 App 内确认"}


def confirm_bind(session_id: str) -> Dict[str, Any]:
    """取会话内 token，四要件复核后加密落盘。

    Args:
        session_id: 会话标识。

    Returns:
        dict: `{bound, platform, bound_at}`（绑定成功后的本地摘要，不含 token）。

    Raises:
        PermissionError: 尚未扫码成功，无法绑定。
    """
    session = _SESSIONS.get(session_id)
    if not session or session.get("state") != "success" or not session.get("token"):
        raise PermissionError("尚未扫码成功，无法绑定")
    token = str(session["token"])
    check = verify_token_four_elements(token)
    if not check["ok"]:
        raise PermissionError(f"token 校验失败：{check['reason']}")
    save_token(token, "ios")
    # 绑定完成即销毁会话：token 已在 DPAPI 文件中，内存与会话表都不再保留
    _SESSIONS.pop(session_id, None)
    return {"bound": True, "platform": "ios"}


def _plug_get(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """调用 browser-plug 接口并解包 `{code,data,message}`。

    统一走 ``use_old=True`` 分支：真实通道（browser-plug-api）用老盐、https 优先。
    token 为空时也允许（``/qr_code`` 本身无需登录态）。

    Args:
        path: 接口路径（可含查询串；签名会自动去除查询串）。
        params: 查询参数（由 requests 负责编码）。

    Returns:
        Any: 响应体里的 ``data`` 字段。

    Raises:
        NonRetryableError: 上游返回非 200、非 JSON、或 code != 200。
    """
    token = str((load_token() or {}).get("token") or "")
    result = _request("GET", BASE_OLD, path, token, params=params, use_old=True)
    if result["status"] != 200:
        raise _status_error(result["status"], path)
    try:
        body = json.loads(result["text"])
    except json.JSONDecodeError as exc:
        raise NonRetryableError(f"养基宝接口返回非 JSON（{path.split('?')[0]}）") from exc
    code = body.get("code")
    if code is not None and str(code) != "200":
        # 文档 §3.4：失效判据为「401 或 code!=200」，故业务码仍需按 401/408/429 分流
        try:
            code_int = int(code)
        except (TypeError, ValueError):
            code_int = 0
        if code_int in _STATUS_HINTS:
            exc_cls, message = _STATUS_HINTS[code_int]
            raise exc_cls(message)
        raise NonRetryableError(f"养基宝返回错误（code={code}）：{body.get('message') or ''}")
    return body.get("data")


def get_accounts() -> Dict[str, Any]:
    """拉取账户列表（真实接口 ``GET /user_account``）。

    真实响应 ``data``：``{is_single_account_model, target_account_id, list:[{id,title,type}]}``。

    Returns:
        dict: `{accounts: [{id,title}], target_id: str, single: bool}`。

    Raises:
        NonRetryableError: 未绑定，或接口不可用。
    """
    if not (load_token() or {}).get("token"):
        raise YjbNotConfiguredError("未绑定养基宝，无法读取账户")
    data = _plug_get("/user_account")
    items: List[Dict[str, Any]] = []
    target = ""
    single = False
    if isinstance(data, list):
        items = [item for item in data if isinstance(item, dict)]
    elif isinstance(data, dict):
        single = bool(data.get("is_single_account_model"))
        target = str(data.get("target_account_id") or data.get("account_id") or "")
        for key in ("list", "accounts", "data"):
            value = data.get(key)
            if isinstance(value, list):
                items = [item for item in value if isinstance(item, dict)]
                break
    accounts = []
    for item in items:
        account_id = item.get("id", item.get("account_id"))
        if account_id in (None, ""):
            continue
        accounts.append({"id": str(account_id), "title": str(item.get("title") or "")})
    return {"accounts": accounts, "target_id": target, "single": single}


def resolve_account_id(explicit: str = "") -> str:
    """解析账户 id：显式传入 > 服务端推荐 ``target_account_id`` > 列表第一个。

    Args:
        explicit: 用户指定或已保存的账户 id。

    Returns:
        str: 账户 id。

    Raises:
        NonRetryableError: 无可用账户。
    """
    text = str(explicit or "").strip()
    if text:
        return text
    info = get_accounts()
    if info["target_id"]:
        return info["target_id"]
    if info["accounts"]:
        return info["accounts"][0]["id"]
    raise NonRetryableError("登录成功但未找到养基宝账户，请先在养基宝 App 中创建账户")


def get_holdings(account_id: str = "") -> List[Dict[str, Any]]:
    """拉取持仓基金列表（真实接口 ``GET /fund_hold?account_id=``）。

    这是**唯一**带估值信息的接口：每条含 ``fund_id`` / ``code`` / ``short_name`` /
    ``hold_share`` / ``hold_cost`` / ``hold_sum``，估值在 ``nv_info``
    （``gszzl`` / ``zsgzzl`` / ``vgszzl`` / ``dwjz`` / ``jzzzl`` / ``gztime`` / ``time``）。

    Args:
        account_id: 账户 id；为空时自动解析。

    Returns:
        list[dict]: 持仓基金条目。

    Raises:
        NonRetryableError: 未绑定或接口不可用。
    """
    if not (load_token() or {}).get("token"):
        raise YjbNotConfiguredError("未绑定养基宝，无法读取持仓")
    data = _plug_get("/fund_hold", {"account_id": resolve_account_id(account_id)})
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("fund_list", "list", "funds", "fund_data", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def get_beat_rate(account_id: str = "") -> Optional[float]:
    """当日击败基民百分比（真实接口 ``GET /income_data``，返回形如 ``"68.5"``）。

    Args:
        account_id: 账户 id；为空时自动解析。

    Returns:
        Optional[float]: 百分比数值；不可用时返回 None（不补 0）。
    """
    try:
        data = _plug_get("/income_data", {"account_id": resolve_account_id(account_id)})
    except NonRetryableError:
        return None
    try:
        return float(str(data).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> Optional[float]:
    """把上游字符串数值安全转为 float（"2.5792" → 2.5792）。

    Args:
        value: 原始值（可能是字符串、数字或空）。

    Returns:
        Optional[float]: 数值；空或不可解析返回 None（不补 0）。
    """
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _rate_of(item: Dict[str, Any]) -> Optional[float]:
    """取估算涨跌幅：``gszzl`` → ``zsgzzl`` → ``vgszzl``（真实字段优先级）。

    QDII / 港股（``market_type=ch_hk``）常无 ``gszzl``，回落到 ``vgszzl``。

    Args:
        item: 单条持仓记录。

    Returns:
        Optional[float]: 百分比数值；全空或无法解析时返回 None。
    """
    nv = item.get("nv_info") if isinstance(item.get("nv_info"), dict) else {}
    for key in ("gszzl", "zsgzzl", "vgszzl"):
        raw = nv.get(key, item.get(key))
        if raw in (None, ""):
            continue
        try:
            return float(str(raw).replace("%", "").replace("+", "").strip())
        except ValueError:
            continue
    return None


def get_collect(estimates: bool = True) -> Dict[str, Any]:
    """多账户汇总（真实接口 ``GET /account_collect``）。

    返回总资产 / 今日收益（估算）/ 今日收益率 / 累计持有收益 / 各账户明细。

    Args:
        estimates: 是否采用盘中估算口径。``False`` 时今日收益类字段一律置 ``None``
            （用户一键关闭估算 → 回退 T+1 官方净值口径），**不补 0、不用旧值冒充**。

    Returns:
        dict: `{total_assets, today_income, today_income_rate, hold_income,
        accounts[], estimates}`。

    Raises:
        NonRetryableError: 未绑定或接口不可用。
    """
    if not (load_token() or {}).get("token"):
        raise YjbNotConfiguredError("未绑定养基宝，无法读取账户汇总")
    data = _plug_get("/account_collect") or {}
    if not isinstance(data, dict):
        data = {}
    accounts: List[Dict[str, Any]] = []
    for item in data.get("account_data") or []:
        if not isinstance(item, dict):
            continue
        account_id = item.get("account_id", item.get("id"))
        if account_id in (None, ""):
            continue
        hold_cost = _as_float(item.get("hold_cost"))
        hold_income = _as_float(item.get("hold_income"))
        # R6-11 / F6-12：账户级「资产」= 持仓成本 + 累计持有收益。
        # 上游 account_collect 不下发该字段，但三项合计与 total_assets 恒等
        # （2026-10-28 实测：28,704.90+92,475.61+26,796.77 = 147,977.28），
        # 因此可直接推导，无需额外接口；任一分量为 None 时资产也给 None，避免 0 值误导。
        assets = None
        if hold_cost is not None and hold_income is not None:
            assets = round(hold_cost + hold_income, 2)
        accounts.append({
            "id": str(account_id),
            "title": str(item.get("title") or ""),
            "assets": assets,
            "today_income": _as_float(item.get("today_income")) if estimates else None,
            "today_income_rate": _as_float(item.get("today_income_rate")) if estimates else None,
            "hold_income": hold_income,
            "hold_cost": hold_cost,
            "up": int(_as_float(item.get("up")) or 0),
            "down": int(_as_float(item.get("down")) or 0),
        })
    return {
        "total_assets": _as_float(data.get("assets_collect")),
        "today_income": _as_float(data.get("today_income")) if estimates else None,
        "today_income_rate": _as_float(data.get("today_income_rate")) if estimates else None,
        "hold_income": _as_float(sum(a["hold_income"] or 0 for a in accounts)) if accounts else None,
        "is_single_account": bool(data.get("is_single_account_model")),
        "accounts": accounts,
        "estimates": bool(estimates),
    }


def get_holdings_view(account_id: str = "", estimates: bool = True) -> Dict[str, Any]:
    """持仓视图（含估值），支持单账户或全部账户合并。

    Args:
        account_id: 账户 id；传空或 "all" 表示合并全部账户。
        estimates: 是否给出盘中估算涨跌幅。``False`` 时 ``pct`` 统一为 ``None``
            （一键关闭估算 → 回退 T+1 公布净值口径，仅 ``nav``/``nav_date`` 有效）。

    Returns:
        dict: `{account_id, items[], total, accounts[], estimates}`；items 按估算涨跌幅降序。

    Raises:
        NonRetryableError: 未绑定或接口不可用。
    """
    if not (load_token() or {}).get("token"):
        raise YjbNotConfiguredError("未绑定养基宝，无法读取持仓")
    # YJB-P2-5：账户列表**只在这里取一次**，后续逐账户取持仓直接复用 id。
    # 不要在循环内调 get_accounts() 或传空 account_id（空值会让 get_holdings 再走
    # resolve_account_id → 又一次 /user_account，3 账户就退化成 4 次上游调用）。
    info = get_accounts()
    titles = {str(a["id"]): a["title"] for a in info["accounts"]}
    wanted = str(account_id or "").strip()
    targets = [wanted] if wanted and wanted != "all" else [str(a["id"]) for a in info["accounts"]]

    items: List[Dict[str, Any]] = []
    for current in targets:
        # current 必为非空账户 id：get_holdings 内部的 resolve_account_id 走「显式传入直接返回」分支，
        # 不再产生 /user_account 请求（回归用例 test_holdings_view_calls_user_account_once 守护）
        for raw in get_holdings(current):
            nv = raw.get("nv_info") if isinstance(raw.get("nv_info"), dict) else {}
            items.append({
                "code": str(raw.get("code") or ""),
                "name": str(raw.get("short_name") or ""),
                "account_id": current,
                "account_title": titles.get(current, ""),
                "share": _as_float(raw.get("hold_share")),
                "cost": _as_float(raw.get("hold_cost")),
                "money": _as_float(raw.get("money")),
                "hold_earn": _as_float(raw.get("hold_earn")),
                "pct": _rate_of(raw) if estimates else None,
                "nav": _as_float(nv.get("dwjz")),
                "nav_date": str(nv.get("jzrq") or nv.get("gztime") or "")[:10],
                "as_of": str(nv.get("gztime") or nv.get("time") or "")[:10],
                "market_type": str(raw.get("market_type") or ""),
                "is_fuzzy": bool(raw.get("is_fuzzy")),
            })
    items.sort(key=lambda x: (x["pct"] is None, -(x["pct"] or 0)))
    return {"account_id": wanted or "all", "items": items, "total": len(items),
            "accounts": [{"id": str(a["id"]), "title": a["title"]} for a in info["accounts"]],
            "estimates": bool(estimates)}


def fetch_increase_rate(fund_id: str) -> Dict[str, Any]:
    """按 6 位基金代码取盘中估算涨跌幅（一级通道：``/fund_hold``）。

    Args:
        fund_id: 6 位基金代码。

    Returns:
        dict: `{pct, source, as_of, name, nav}`。

    Raises:
        NonRetryableError: 未绑定、接口不可用，或该基金不在持仓列表中。
    """
    target = str(fund_id or "").strip()
    # 多账户：默认账户找不到时遍历其余账户（用户持仓常分散在多个账本）
    account_ids: List[str] = []
    try:
        info = get_accounts()
        account_ids = [str(a["id"]) for a in info["accounts"]]
    except NonRetryableError:
        account_ids = [""]
    if not account_ids:
        account_ids = [""]
    for account_id in account_ids:
        for item in get_holdings(account_id):
            if str(item.get("code") or "").strip() != target:
                continue
            rate = _rate_of(item)
            if rate is None:
                continue
            nv = item.get("nv_info") if isinstance(item.get("nv_info"), dict) else {}
            return {
                "pct": rate,
                "source": "养基宝/fund_hold",
                "as_of": str(nv.get("gztime") or nv.get("time") or utils.now_text()),
                "name": str(item.get("short_name") or ""),
                # 上游 dwjz 为字符串（"2.5792"），必须转数值，否则下游做算术会 TypeError
                "nav": _as_float(nv.get("dwjz")),
                "account_id": account_id,
            }
    raise NonRetryableError(f"养基宝持仓列表未包含该基金：{target}")


def fetch_gz_data(fund_id: str) -> Dict[str, Any]:
    """估值明细备选通道（与 increase-rate 同源，口径标识区分）。

    Args:
        fund_id: 6 位基金代码。

    Returns:
        dict: `{pct, source, as_of, name, nav}`。

    Raises:
        NonRetryableError: 同 fetch_increase_rate。
    """
    detail = fetch_increase_rate(fund_id)
    detail["source"] = "养基宝/fund_hold(明细)"
    return detail


def status() -> Dict[str, Any]:
    """连接器状态（不含 token 与盐的取值）。

    Returns:
        dict: `{bound, bound_at, platform, consent, enabled, secret_configured,
        secret_source, secure_storage, channel_available, transport, notice,
        setup_required, setup_missing, setup_steps, data_dir, is_frozen}`。
        ``setup_required`` 为真时前端必须显示「首启向导」，禁止静默空态。
    """
    state = _load_state()
    token = load_token()
    bound = bool(token and token.get("token"))
    has_secret = secret_configured()
    # 首启向导：凭据未齐（缺 token 或缺签名盐）时必须显式引导，不能伪装成已就绪
    # 缺失项编码用「用途命名」，不用 token/secret 这类字段名——避免与
    # 「status 响应不得出现 token 字样」的安全断言冲突（值本身从不返回）。
    missing: List[str] = []
    if not has_secret:
        missing.append("signing_salt")
    if not bound:
        missing.append("authorization")
    try:
        data_dir = str(config.DATA_DIR)
        is_frozen = bool(getattr(config, "IS_FROZEN", False))
    except Exception:  # noqa: BLE001 - 配置不可用时不影响状态返回
        data_dir = ""
        is_frozen = False
    return {
        "bound": bound,
        "bound_at": (token or {}).get("bound_at"),
        "platform": (token or {}).get("platform"),
        "consent": state.get("consent"),
        "enabled": bool(state.get("enabled")),
        "secret_configured": has_secret,
        "secret_source": secret_source(),
        "secure_storage": _dpapi_available(),
        "channel_available": bool(bound and has_secret),
        "transport": (
            "https（已实测可用）" if BASE_OLD.startswith("https://")
            or str(_OLD_SCHEME.get("base") or "").startswith("https://")
            else "https 优先尝试（未探测；若不可用回落 http 明文，已登记风险）"
        ),
        "notice": "非官方通道：接口易变、可能随时失效；仅供本地个人研究，不构成投资建议",
        "setup_required": bool(missing),
        "setup_missing": missing,
        "setup_steps": [
            "打开 #/settings → 「养基宝连接」→ 注入签名盐（DPAPI 加密，仅本机可解）",
            "回到本页 Y-03 → 点击「生成二维码」→ 用养基宝 App 扫码授权",
            "授权成功后自动写入 token，Y-05/Y-06 立即显示账户汇总与持仓",
        ],
        "data_dir": data_dir,
        "is_frozen": is_frozen,
    }


