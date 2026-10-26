"""养基宝连接器路由：/api/fund/yjb 下 11 个接口（阶段 7 + 第四波补盐注入 + 第五波安全加固）。

- GET  /status    连接器状态（是否绑定/告知书/开关/密钥/安全存储），绝不返回 token 或盐值
- POST /consent   确认合规告知书（附录 E）；请求体必须显式带 accepted 布尔值，空 body 拒绝
- POST /toggle    一键开关增强通道；请求体必须显式带 enabled 布尔值，空 body 拒绝
- POST /secret    写入签名盐（DPAPI 加密落盘；不回显）；清除签名盐必须显式 confirm=true
- GET  /qrcode    获取二维码（segno 本地生成 base64；接口不可用时给中文原因）
- GET  /poll      2 秒轮询扫码状态（waiting/scanning/success/expired/timeout）
- POST /bind      token 四要件校验后加密落盘
- POST /unbind    解绑（不可逆：必须显式 confirm=true + 前端二次确认，留审计日志）
- GET  /collect   多账户汇总（总资产/今日估算收益/累计收益/账户明细）
- GET  /holdings  持仓与估值列表（可按账户过滤，account_id=all 合并）
- GET  /realtime  盘中估值链（养基宝→东财估值→拟合→T+1），逐次返回命中的源与口径

安全：token 与签名盐只在后端内存与 DPAPI 加密文件中出现；响应、日志、错误文案均不含明文。
破坏性/不可逆操作（RT-01 整改）统一原则：拒绝空 body、要求显式确认标识、执行后落脱敏审计日志。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Body, Query

from backend.app.datasource import utils
from backend.app.core import cache
from backend.app.core.errors import ErrorCode, NonRetryableError, api_error, ok
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
        "as_of": utils.now_text(),
        "market_status": "连接器状态",
        "is_realtime": False,
    })
    return ok(payload)


def _cached_load(key: str, loader, source: str, caliber: str) -> Dict[str, Any]:
    """带 30 秒缓存的加载包装（养基宝接口不宜高频调用）。

    Args:
        key: 缓存键后缀。
        loader: 无参加载函数，返回数据字典。
        source: 数据源标识（前端徽章）。
        caliber: 口径说明（前端徽章）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`。
    """
    try:
        # 凭据失效（401）不得用磁盘旧值兜底：否则用户看到的是过期收益，
        # 却以为仍处于已绑定正常状态（§3.4 要求引导重新授权）
        data = cache.get_or_load(
            f"yjb:{key}", 30, loader,
            stale_ok=lambda exc: not isinstance(exc, yangjibao.YjbUnauthorizedError),
        )
    except yangjibao.YjbUnauthorizedError as exc:
        # 接口文档 §3.4：401 属凭据失效，前端应引导重新扫码而非提示「稍后重试」
        return api_error(ErrorCode.UNAUTHORIZED, str(exc))
    except yangjibao.YjbNotConfiguredError:
        # 首启态：页面顶部已有三步向导，这里给「未配置」而非「接口不可用」，
        # 避免与向导自相矛盾，也让用户知道点「重试」不会有变化，需要先配置。
        return api_error(
            ErrorCode.NOT_CONFIGURED,
            "养基宝尚未完成授权：请按页面顶部三步完成配置（注入签名盐 → 扫码授权）后重试。",
        )
    except yangjibao.YjbTimeoutError as exc:
        return api_error(ErrorCode.UPSTREAM_TIMEOUT, str(exc))
    except yangjibao.YjbRateLimitError as exc:
        return api_error(ErrorCode.UPSTREAM_ERROR, str(exc))
    except NonRetryableError as exc:
        return api_error(ErrorCode.UPSTREAM_ERROR, f"养基宝接口不可用：{exc}")
    except Exception as exc:  # noqa: BLE001 - 上游异常统一降级
        logger.info("养基宝数据读取失败：%s（%s）", key, type(exc).__name__)
        return api_error(ErrorCode.UPSTREAM_ERROR, "养基宝数据暂不可用，请稍后重试。")
    return _pack(data, source, caliber)


def _require_explicit(payload: Dict[str, Any], field: str, hint: str):
    """破坏性/状态变更 POST 的请求体显式校验（RT-01 整改）。

    空 body 或字段缺失/类型不符时一律拒绝执行，返回 INVALID_PARAM，
    确保第三方扫描器误打空 body 不会触发真实状态变更（清绑/清盐/翻转开关）。

    Args:
        payload: 请求体（JSON 对象）。
        field: 必须显式出现的字段名。
        hint: 拒绝时给用户的中文提示。

    Returns:
        Any | None: 字段值（合法时）；None 表示已拒绝（调用方应直接返回错误响应）。
    """
    value = payload.get(field)
    if field not in payload or not isinstance(value, bool):
        return None
    return value


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
    """确认或拒绝合规告知书（显式 accepted 布尔值；空 body 拒绝执行）。

    Args:
        payload: 请求体，必须含布尔字段 `accepted`（true=确认/false=拒绝）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    accepted = _require_explicit(payload, "accepted", "accepted")
    if accepted is None:
        # 空 body 或缺字段：不落任何状态变更（RT-01：第三方空 body 扫描曾误清状态）
        return api_error(ErrorCode.INVALID_PARAM, "缺少确认标识：请显式提交 accepted 布尔值。")
    state = yangjibao.accept_consent(accepted)
    cache.clear_cache()  # 合规状态变化后失效估值缓存，避免旧口径残留
    logger.info("养基宝告知书状态变更：accepted=%s（操作经显式请求体）", bool(accepted))
    return _pack(
        {"consent": state.get("consent"), "enabled": state.get("enabled")},
        "本地配置",
        "确认记录仅存本机；拒绝将同时关闭增强通道",
    )


@router.post("/toggle")
def yjb_toggle(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """一键开关增强通道（显式 enabled 布尔值；空 body 拒绝执行）。

    Args:
        payload: 请求体，必须含布尔字段 `enabled`。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    enabled = _require_explicit(payload, "enabled", "enabled")
    if enabled is None:
        # 空 body 不得翻转开关（RT-01）
        return api_error(ErrorCode.INVALID_PARAM, "缺少确认标识：请显式提交 enabled 布尔值。")
    try:
        state = yangjibao.set_enabled(enabled)
        cache.clear_cache()  # 开关变化立即影响估值链
    except PermissionError as exc:
        return api_error(ErrorCode.INVALID_PARAM, str(exc), data={"enabled": False})
    logger.info("养基宝增强通道开关：enabled=%s（操作经显式请求体）", bool(enabled))
    return _pack(
        {"enabled": state.get("enabled")},
        "本地配置",
        "关闭后基金估值回退 T+1 净值 + 重仓股涨跌参考，不再请求养基宝",
    )


@router.post("/secret")
def yjb_secret(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """写入或清除养基宝签名盐（DPAPI 加密，不回显任何明文）。

    写入需 `{"new": "...", "old": "..."}`（任一非空即写入）；
    **清除（两者皆空）属不可逆操作**，必须显式 `{"confirm": true}`，
    空 body 一律拒绝执行（RT-01 整改）。

    Args:
        payload: 请求体（JSON 对象）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 仅含配置状态与来源。
    """
    new_secret = str(payload.get("new") or "")
    old_secret = str(payload.get("old") or "")
    if not new_secret and not old_secret:
        # 清除签名盐：不可逆，必须显式二次确认标识（空 body 拒绝）
        if payload.get("confirm") is not True:
            return api_error(
                ErrorCode.INVALID_PARAM,
                "清除签名盐需二次确认：请显式提交 confirm=true。",
                data={"secret_configured": yangjibao.secret_configured()},
            )
        try:
            written = yangjibao.set_secret(new_secret, old_secret)
        except Exception as exc:  # noqa: BLE001 - DPAPI 不可用时拒绝明文落盘
            logger.info("养基宝签名盐写入失败：%s", type(exc).__name__)
            return api_error(ErrorCode.INTERNAL_ERROR,
                             "签名盐写入失败：本机安全存储（DPAPI）不可用，已拒绝明文落盘。",
                             data={"secret_configured": False})
        cache.clear_cache()
        logger.info("养基宝签名盐已清除（confirm=true 显式确认；盐值不记录）")
        return _pack(
            {
                "secret_configured": yangjibao.secret_configured(),
                "secret_source": yangjibao.secret_source(),
                "cleared": not written,
            },
            "本地配置",
            "签名盐以 DPAPI 加密落盘（YJB2），不回显、不写日志；环境变量优先于本地配置",
        )
    try:
        written = yangjibao.set_secret(new_secret, old_secret)
    except Exception as exc:  # noqa: BLE001 - DPAPI 不可用时拒绝明文落盘
        logger.info("养基宝签名盐写入失败：%s", type(exc).__name__)
        return api_error(ErrorCode.INTERNAL_ERROR,
                         "签名盐写入失败：本机安全存储（DPAPI）不可用，已拒绝明文落盘。",
                         data={"secret_configured": False})
    cache.clear_cache()
    return _pack(
        {
            "secret_configured": yangjibao.secret_configured(),
            "secret_source": yangjibao.secret_source(),
            "cleared": not written,
        },
        "本地配置",
        "签名盐以 DPAPI 加密落盘（YJB2），不回显、不写日志；环境变量优先于本地配置",
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
def yjb_unbind(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """解绑：清除本地 token 与内存会话（不可逆操作，必须显式 confirm=true）。

    RT-01 整改：空 body 一律拒绝执行；前端必须弹出二次确认后再提交。

    Args:
        payload: 请求体，必须含 `confirm: true`。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    if payload.get("confirm") is not True:
        # 解绑不可逆：缺确认标识时不删 token（RT-01：第三方空 body 扫描曾清空本机绑定）
        return api_error(
            ErrorCode.INVALID_PARAM,
            "解绑需二次确认：请显式提交 confirm=true（不可逆操作）。",
            data={"bound": yangjibao.status().get("bound", True)},
        )
    yangjibao.clear_token()
    cache.clear_cache()
    logger.info("养基宝已解绑（confirm=true 显式确认；token 内容不记录）")
    return _pack({"bound": False}, "本地配置", "已清除本地 token；不影响基金 T+1 净值功能")


@router.get("/collect")
def yjb_collect(estimates: int = Query(1, ge=0, le=1,
                                       description="1=采用盘中估算口径；0=回退 T+1 官方净值口径")) -> Dict[str, Any]:
    """多账户汇总：总资产 / 今日估算收益 / 累计持有收益 / 各账户明细。

    Args:
        estimates: 1（默认）给出盘中估算收益；0 表示用户已一键关闭估算，
            今日收益类字段返回 null（回退 T+1 净值口径，不补 0、不用旧值冒充）。

    Returns:
        dict: 统一返回结构；data 内含 accounts 数组与 estimates 标记。
    """
    use_estimates = bool(estimates)
    caliber = ("账户级汇总；今日收益为估算值，非官方净值" if use_estimates
               else "账户级汇总；已按用户设置关闭盘中估算，今日收益不展示（T+1 官方净值口径）")
    return _cached_load(f"collect:{int(use_estimates)}",
                        lambda: yangjibao.get_collect(estimates=use_estimates),
                        "养基宝/browser-plug-api account_collect", caliber)


@router.get("/holdings")
def yjb_holdings(account_id: str = Query("", description="账户 id；留空=全部账户"),
                 estimates: int = Query(1, ge=0, le=1,
                                        description="1=含估算涨跌幅；0=仅 T+1 公布净值")):
    """持仓与估值列表（可按账户过滤）。

    Args:
        account_id: 账户 id；留空或 all 表示合并全部账户。
        estimates: 1（默认）返回估算涨跌幅；0 时 ``pct`` 为 null（T+1 口径）。

    Returns:
        dict: 统一返回结构；data 内含 items / accounts。
    """
    use_estimates = bool(estimates)
    key = f"holdings:{account_id or 'all'}:{int(use_estimates)}"
    caliber = ("持仓市值与持有收益为账户口径；估算涨跌幅非官方净值" if use_estimates
               else "持仓市值与持有收益为账户口径；已关闭盘中估算，涨跌幅列留空（T+1 公布净值为准）")
    return _cached_load(key, lambda: yangjibao.get_holdings_view(account_id, estimates=use_estimates),
                        "养基宝/browser-plug-api fund_hold", caliber)


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
    disclaimer = "非官方·拟合估算·仅供参考·不构成投资建议"
    if estimate and disclaimer not in caliber:
        # 数据源可能已自带该前缀（如养基宝），避免重复拼接
        caliber = f"{disclaimer}｜{caliber}"
    return _pack(
        data,
        data.get("source") or "",
        caliber,
    )
