"""养基宝连接器路由：/api/fund/yjb 下 8 个接口（阶段 7）。

- GET  /status    连接器状态（是否绑定/告知书/开关/密钥/安全存储），绝不返回 token
- POST /consent   确认合规告知书（附录 E）；拒绝即关闭增强通道
- POST /toggle    一键开关增强通道（开启前必须已确认告知书）
- GET  /qrcode    获取二维码（segno 本地生成 base64；接口不可用时给中文原因）
- GET  /poll      2 秒轮询扫码状态（waiting/scanning/success/expired/timeout）
- POST /bind      token 四要件校验后加密落盘
- POST /unbind    解绑（清除本地 token）
- GET  /realtime  盘中估值链（养基宝→拟合→T+1），逐次返回命中的源与口径

安全：token 只在后端内存与 DPAPI 加密文件中出现；响应、日志、错误文案均不含 token。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, Body, Query

from backend.app import config
from backend.app.core import cache
from backend.app.core.errors import ErrorCode, api_error, ok
from backend.app.datasource import fund as fund_provider
from backend.app.datasource import yangjibao

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/fund/yjb", tags=["养基宝"])

CONSENT_TEXT = """# 养基宝 / 盘中估值 合规告知书

本工具为【本地个人研究用途】的投研软件：

1. 养基宝数据为【非官方、无公开 API】通道，接口易变、可能随时失效；
2. 盘中实时估值为【基于重仓股与指数涨跌幅的拟合估算】，非官方净值，【仅供参考、不构成任何投资建议】；
3. 本工具【不分发、不导流、不交易】，不展示加仓榜/实盘榜等被监管点名的排行榜；
4. 你可随时在设置中【关闭盘中估值/养基宝通道】，系统将回退到 T+1 收盘净值；
5. 如监管进一步收紧，本工具将自动停用相关通道。

本人已知悉并同意上述说明后，方可启用。"""


def _pack(data: Dict[str, Any], source: str, caliber: str) -> Dict[str, Any]:
    """统一响应（连接器状态为配置/状态快照，非行情）。

    Args:
        data: 待包装或清洗的数据主体。
        source: 数据源标识（用于降级链溯源与徽章展示）。
        caliber: 口径说明（展示在口径徽章）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    payload = dict(data)
    payload.update({
        "source": source,
        "caliber": caliber,
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "market_status": "连接器状态",
        "is_realtime": False,
    })
    return ok(payload)


@router.get("/status")
def yjb_status() -> Dict[str, Any]:
    """连接器状态（不含任何 token 内容）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    data = yangjibao.status()
    data["consent_text"] = CONSENT_TEXT
    return _pack(data, "本地配置", "token 仅以 DPAPI 加密存储，禁用明文/日志/前端回显")


@router.post("/consent")
def yjb_consent(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """确认或拒绝合规告知书。

    Args:
        payload: 请求体（JSON 对象）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    accepted = bool(payload.get("accepted"))
    state = yangjibao.accept_consent(accepted)
    cache.clear_cache()  # 合规状态变化后失效估值缓存，避免旧口径残留
    return _pack(
        {"consent": state.get("consent"), "enabled": state.get("enabled")},
        "本地配置",
        "确认记录仅存本机；拒绝将同时关闭增强通道",
    )


@router.post("/toggle")
def yjb_toggle(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """一键开关增强通道（开启前必须有告知书确认）。

    Args:
        payload: 请求体（JSON 对象）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    enabled = bool(payload.get("enabled"))
    try:
        state = yangjibao.set_enabled(enabled)
        cache.clear_cache()  # 开关变化立即影响估值链
    except PermissionError as exc:
        return api_error(ErrorCode.INVALID_PARAM, str(exc), data={"enabled": False})
    return _pack(
        {"enabled": state.get("enabled")},
        "本地配置",
        "关闭后基金估值回退 T+1 净值 + 重仓股涨跌参考，不再请求养基宝",
    )


@router.get("/qrcode")
def yjb_qrcode() -> Dict[str, Any]:
    """生成扫码会话与二维码（本地 segno 渲染）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    try:
        session = yangjibao.start_qrcode_session()
    except Exception as exc:  # noqa: BLE001 - 通道不可用时给中文原因
        logger.info("养基宝二维码不可用：%s", type(exc).__name__)
        return api_error(
            ErrorCode.UPSTREAM_ERROR,
            "养基宝二维码通道暂不可用（非官方接口易变）。可先使用拟合估算或 T+1 净值。",
            data={"available": False, "reason": str(exc)[:120]},
        )
    return _pack(session, "养基宝", "扫码会话仅在内存，成功绑定后 token 才加密落盘")


@router.get("/poll")
def yjb_poll(session_id: str = Query(..., description="二维码会话 id")) -> Dict[str, Any]:
    """轮询扫码状态（前端 2 秒一次；幂等）。

    Args:
        session_id: 二维码会话 id。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    data = yangjibao.poll_session(session_id)
    return _pack(data, "养基宝", "状态机：等待扫码/已扫码待确认/成功/失效/超时")


@router.post("/bind")
def yjb_bind(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """四要件校验通过后绑定（token 加密落盘，不回显）。

    Args:
        payload: 请求体（JSON 对象）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    session_id = str(payload.get("session_id") or "")
    if not session_id:
        return api_error(ErrorCode.INVALID_PARAM, "缺少 session_id，请重新扫码。")
    try:
        result = yangjibao.confirm_bind(session_id)
    except PermissionError as exc:
        return api_error(ErrorCode.UNAUTHORIZED, str(exc), data={"bound": False})
    except Exception as exc:  # noqa: BLE001
        logger.info("养基宝绑定失败：%s", type(exc).__name__)
        return api_error(ErrorCode.UPSTREAM_ERROR, "绑定失败，请刷新二维码后重试。",
                         data={"bound": False})
    return _pack(result, "养基宝", "token 已 DPAPI 加密落盘，前端与日志均不可见")


@router.post("/unbind")
def yjb_unbind() -> Dict[str, Any]:
    """解绑：清除本地 token 与内存会话。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    yangjibao.clear_token()
    cache.clear_cache()
    return _pack({"bound": False}, "本地配置", "已清除本地 token；不影响基金 T+1 净值功能")


@router.get("/realtime")
def yjb_realtime(code: str = Query(..., description="6 位基金代码")) -> Dict[str, Any]:
    """盘中估值链（养基宝→拟合→T+1），返回命中源与合规口径。

    Args:
        code: 6 位基金代码。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    symbol = (code or "").strip()
    if not symbol:
        return api_error(ErrorCode.INVALID_PARAM, "code 参数不可为空")
    try:
        # 30 秒缓存：盘中估值不宜高频请求，也避免重仓股批量取价重复
        data = cache.get_or_load(
            f"yjb:realtime:{symbol}", 30, lambda: fund_provider.get_realtime(symbol)
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("基金实时估值失败：%s（%s）", symbol, type(exc).__name__)
        return api_error(ErrorCode.UPSTREAM_ERROR, "基金估值暂不可用，请稍后点击刷新重试。",
                         data={"code": symbol})
    estimate = bool(data.get("is_estimate"))
    caliber = data.get("caliber") or ""
    if estimate:
        caliber = "非官方·拟合估算·仅供参考·不构成投资建议｜" + caliber
    return _pack(
        data,
        data.get("source") or "",
        caliber,
    )
