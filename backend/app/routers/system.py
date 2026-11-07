"""系统域路由：健康检查、缓存管理、日志导出与恢复出厂。

对应规范 v7.0 §8.7 system 域。所有响应统一为 {data, error} 结构：
成功时 error=null；失败时 data 为空结构、error 为自然中文文案。
本模块提供 /api/health、/api/cache/clear、/api/cache/stats、
/api/logs/export、/api/factory-reset，不提供任何业务数据接口。

安全要点（R3-13）：
- factory-reset 白名单只含应用偏好，**绝不删除持仓/自选/密钥**；
- logs/export 只打包本应用自己的日志文件；
- 所有删除动作记日志，不出现任何密钥字符。
"""

from __future__ import annotations

import logging
import os
import platform
import zipfile
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Body
from fastapi.responses import FileResponse

from backend.app import config
from backend.app.core import cache, capabilities, public_tunnel, user_prefs, v8_guard, web_access
from backend.app.core.errors import ErrorCode, api_error, ok
from backend.app.datasource import utils

logger = logging.getLogger(__name__)

# 对外时间字段统一走 utils.bj_now_aware()（带 +08:00 偏移），不再各模块自建时区常量

router = APIRouter(prefix="/api", tags=["系统"])


def _datasource_status() -> Dict[str, Dict[str, Any]]:
    """汇总各数据源的真实接入状态（P1-05）。

    Returns:
        Dict[str, Dict[str, Any]]: 每个数据源的启用状态、依赖安装状态与中文状态文案。

    Notes:
        pip 依赖用 find_spec 判断；外部 HTTP 通道（同花顺/财联社/蛋卷）由
        ``core.capabilities`` 做最小真实请求探测（结果缓存 10 分钟），
        因此不再出现「通道可用却显示待接入」的自相矛盾。
    """
    return capabilities.datasource_status()


def _northbound_status() -> Dict[str, Any]:
    """北向数据通道状态（供 /health 展示，第十轮验收 A19）。

    只读内存缓存，不触发网络请求；未预热时如实标注 ``loaded: false``。

    Returns:
        Dict[str, Any]: {metric, disclosure, loaded, latest, stale, source}
    """
    metric = "turnover"
    disclosure = "沪深股通成交总额 · 交易所 T+0 收市后披露；2024-08-19 起无净买额披露"
    payload = None
    try:
        payload = cache.get("moneyflow:northbound:turnover", config.TTL.get("northbound", 3600))
    except Exception:  # noqa: BLE001 - 健康检查不得因缓存异常而失败
        payload = None
    # get_or_load 直接缓存 data 载荷（无 {data,error} 信封），故兼容两种形态
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        data = payload["data"]
    else:
        data = payload if isinstance(payload, dict) else None
    latest = data.get("latest") if isinstance(data, dict) else None
    return {
        "metric": metric,
        "disclosure": disclosure,
        "loaded": bool(latest),
        "latest": latest,
        "stale": not bool(latest),
        "source": data.get("source") if isinstance(data, dict) else None,
    }


@router.get("/health")
def health() -> Dict[str, Any]:
    """健康检查接口。

    Returns:
        Dict[str, Any]: {"data": {build, app, 数据源状态, v8 引擎状态, ...}, "error": null}
    """
    ai_config = config.load_ai_config()
    data = {
        "build": config.BUILD_TAG,
        "app": {"name": config.APP_NAME, "version": config.APP_VERSION},
        "python": platform.python_version(),
        "host": config.HOST,
        "port": config.PORT,
        "datasources": _datasource_status(),
        "cache": cache.stats(),
        # 第五轮 P1-01：暴露同花顺 V8 通道健康态（熔断/半开/最近成功时间），
        # 修复前 v8_health() 无调用方，熔断在外部完全不可观测。
        "v8": v8_guard.v8_health(),
        # 第十轮验收 A19：北向口径状态行（R3 新披露口径的可观测性）
        # 只读缓存与常量，不发网络请求，保证 /health 亚秒级响应。
        "northbound": _northbound_status(),
        # 仅暴露是否已配置与来源，绝不返回密钥内容
        "ai": {"configured": ai_config.configured, "source": ai_config.source},
        "server_time": utils.bj_now_aware().isoformat(timespec="seconds"),
    }
    return ok(data)


@router.get("/ready")
def ready() -> Dict[str, Any]:
    """服务就绪检查（2026-11-02 第二轮检测 P3-2 补齐）。

    区别于 ``/health``（进程/数据源健康），``/ready`` 回答「服务是否已就绪、
    能否接受业务请求」：路由全部注册、前端静态资源可达、数据目录可写即为就绪。
    常用于自动化部署的就绪闸门（未就绪时返回 503，就绪时 200）。

    Returns:
        Dict[str, Any]: {"data": {ready, checked_at, checks}, "error": null}
    """
    checks: Dict[str, Any] = {}
    all_ok = True

    # 路由是否已注册（OpenAPI schema 非空）
    try:
        from backend.app.main import app as _app
        schema = getattr(_app, "openapi_schema", None)
        if schema is None:
            schema = _app.openapi()
        paths = (schema or {}).get("paths") or {}
        checks["routes"] = {"ok": len(paths) > 0, "count": len(paths)}
    except Exception as exc:  # noqa: BLE001
        checks["routes"] = {"ok": False, "error": type(exc).__name__}
    all_ok = all_ok and bool(checks.get("routes", {}).get("ok"))

    # 前端静态资源是否可达
    checks["frontend"] = {"ok": config.FRONTEND_DIR.exists(), "path": str(config.FRONTEND_DIR)}
    all_ok = all_ok and bool(checks["frontend"]["ok"])

    # 数据目录是否可写
    try:
        writable = os.access(config.DATA_DIR, os.W_OK)
    except Exception:  # noqa: BLE001
        writable = False
    checks["data_dir"] = {"ok": writable, "path": str(config.DATA_DIR)}
    all_ok = all_ok and writable

    return ok({
        "ready": all_ok,
        "checked_at": utils.bj_now_aware().isoformat(timespec="seconds"),
        "checks": checks,
    })


@router.post("/cache/clear")
def clear_cache_endpoint() -> Dict[str, Any]:
    """清空两级缓存。

    Returns:
        Dict[str, Any]: {"data": {"removed": 删除文件数}, "error": null}
    """
    removed = cache.clear_cache()
    return ok({"removed": removed})


@router.get("/cache/stats")
def cache_stats() -> Dict[str, Any]:
    """缓存占用明细（R3-13 设置页 S-03）。

    Returns:
        Dict[str, Any]: {"data": {memory, disk, memory_bytes, disk_bytes,
        layout_bytes, ttl_map[]}, "error": null}
    """
    detail = cache.stats_detail()
    return ok({
        **detail,
        "memory_mb": round(detail["memory_bytes"] / 1024 / 1024, 2),
        "disk_mb": round(detail["disk_bytes"] / 1024 / 1024, 2),
        "layout_kb": round(detail["layout_bytes"] / 1024, 1),
        "source": "本机缓存目录统计（data/cache）",
        "caliber": "内存字节数按 JSON 序列化长度估算，仅表示量级；布局为 data/dashboards.json",
        "as_of": utils.bj_now_aware().isoformat(timespec="seconds"),
        "is_realtime": True,
    })


@router.get("/system/access")
def access_info() -> Dict[str, Any]:
    """访问方式（Web 化）：本机与局域网入口、二维码、口令开关状态。

    Returns:
        Dict[str, Any]: data.points 为入口清单（scope/label/url/qr），
        data.lan_visible 表示是否已放开局域网监听，
        data.password_required 表示是否已启用访问口令。
    """
    lan_visible = config.HOST not in ("127.0.0.1", "localhost", "::1")
    points = web_access.access_points(config.PORT, lan_visible)
    for point in points:
        # 二维码内容即访问地址，手机扫码后直接进入同一应用（口令启用时先验口令）
        point["qr"] = web_access.qr_data_uri(point["url"])
    tunnel = public_tunnel.state()
    if tunnel.get("url"):
        points.append({
            "scope": "public",
            "label": "公网（临时地址）",
            "url": tunnel["url"],
            "qr": web_access.qr_data_uri(tunnel["url"]),
        })
    return ok({
        "points": points,
        "port": config.PORT,
        "host": config.HOST,
        "lan_visible": lan_visible,
        "password_required": bool(os.environ.get("OPENBB_ACCESS_PASSWORD", "").strip()),
        "public": {
            "url": tunnel.get("url", ""),
            "error": tunnel.get("error", ""),
            "started_at": tunnel.get("started_at", ""),
            "ready": bool(tunnel.get("ready")),
        },
        "source": "本机网卡枚举（UDP 出口探测 + 主机名解析）",
        "caliber": "局域网地址仅同网段可达；公网为 Cloudflare 临时隧道，重启会变；二维码内容即访问 URL，不含口令",
        "as_of": utils.bj_now_aware().isoformat(timespec="seconds"),
        "is_realtime": True,
    })


@router.get("/system/desktop-prefs")
def get_desktop_prefs() -> Dict[str, Any]:
    """桌面壳偏好（R6-12 / F6-14）：点击关闭按钮的行为。

    Returns:
        Dict[str, Any]: {"data": {"close_action": "tray"|"quit", "options": [...]}, "error": null}
    """
    return ok({
        "close_action": user_prefs.get_str("close_action"),
        "options": ["tray", "quit"],
        "source": "本机 data/user_prefs.json",
        "caliber": "tray=最小化到托盘（默认）；quit=直接退出并停止本地服务",
        "as_of": utils.bj_now_aware().isoformat(timespec="seconds"),
        "is_realtime": True,
    })


@router.put("/system/desktop-prefs")
def set_desktop_prefs(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """写入桌面壳偏好（仅接受 tray / quit）。

    Args:
        payload: 请求体，形如 {"close_action": "tray"}。

    Returns:
        Dict[str, Any]: {"data": {"close_action": ...}, "error": null}

    Raises:
        None: 参数非法时走统一错误结构返回。
    """
    value = str(payload.get("close_action") or "").strip()
    if value not in ("tray", "quit"):
        return api_error(ErrorCode.INVALID_PARAM, "close_action 仅支持 tray（最小化到托盘）或 quit（直接退出）")
    try:
        prefs = user_prefs.set_str("close_action", value)
    except ValueError as exc:
        return api_error(ErrorCode.INVALID_PARAM, str(exc))
    except Exception as exc:  # noqa: BLE001 - 落盘失败统一转可读错误
        return api_error(ErrorCode.INTERNAL_ERROR, f"偏好保存失败：{exc}")
    logger.info("桌面壳关闭行为已设为 %s", value)
    return ok({
        "close_action": prefs.get("close_action"),
        "source": "本机 data/user_prefs.json",
        "as_of": utils.bj_now_aware().isoformat(timespec="seconds"),
    })


# 恢复出厂可清理的范围（白名单）：只含应用偏好，绝不触碰用户数据与密钥
# 说明：路径在调用时构建（不建模块级常量），便于测试隔离与冻结形态 DATA_DIR 切换
_FACTORY_SCOPE_NAMES = ("prefs", "connectors", "dashboards", "ai")


def _factory_scopes() -> Dict[str, Path]:
    """构造恢复出厂白名单（scope 名 → 目标文件路径）。

    Returns:
        Dict[str, Path]: 应用偏好文件映射；**不含持仓/自选/密钥**。
    """
    return {
        "prefs": config.DATA_DIR / "user_prefs.json",
        "connectors": config.DATA_DIR / "connectors.json",
        "dashboards": config.DATA_DIR / "dashboards.json",
        "ai": config.AI_CONFIG_FILE,
    }


@router.post("/factory-reset")
def factory_reset(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """恢复出厂：按 scope 删除应用偏好文件（二次确认由前端弹窗完成）。

    Body:
        {"confirm": true, "scopes": ["prefs", "connectors", "dashboards"]}

    Args:
        payload: 请求体；confirm 必须为 true，scopes 为空时按全部偏好处理。

    Returns:
        dict: 统一信封；data 含 removed（实际删除的文件名）与保留项说明。

    Notes:
        **红线**：白名单只含应用偏好（prefs/connectors/dashboards/ai），
        绝不删除持仓（portfolio.json）、自选（watchlist.json）、
        养基宝 token/盐（yjb_token.json / yjb_secret.json / yjb_state.json）。
        删除后相关功能回到默认态（如未绑定养基宝、默认仪表板重建）。
    """
    if not payload.get("confirm"):
        return api_error(ErrorCode.INVALID_PARAM, "缺少确认标识：恢复出厂需在前端二次确认后提交。")

    raw_scopes = payload.get("scopes")
    scopes_map = _factory_scopes()
    if isinstance(raw_scopes, list) and raw_scopes:
        scopes = [str(name) for name in raw_scopes if str(name) in scopes_map]
        unknown = [str(name) for name in raw_scopes if str(name) not in scopes_map]
    else:
        scopes = list(_FACTORY_SCOPE_NAMES)
        unknown = []

    removed, missing = [], []
    for scope in scopes:
        target = scopes_map[scope]
        if target.is_file():
            try:
                target.unlink()
                removed.append(scope)
            except OSError as exc:
                logger.warning("恢复出厂删除失败：%s（%s）", target.name, type(exc).__name__)
                missing.append(scope)
        else:
            missing.append(scope)

    logger.info("恢复出厂执行：removed=%s missing=%s", removed, missing)
    return ok({
        "removed": removed,
        "not_present": missing,
        "unknown_scopes": unknown,
        "kept": ["portfolio.json（持仓）", "watchlist.json（自选）",
                 "yjb_token.json / yjb_secret.json / yjb_state.json（养基宝授权）"],
        "note": "删除后下次进入相关页面会按默认值重建；用户数据与密钥一律保留。",
        "caliber": "本地偏好文件删除（data/），不动已缓存的历史数据与用户数据",
        "as_of": utils.bj_now_aware().isoformat(timespec="seconds"),
    })


@router.get("/logs/export")
def export_logs() -> Any:
    """导出日志为 zip（R3-13 设置页 S-05）。

    Returns:
        Any: zip 文件响应；无日志时返回中文错误信封。

    Notes:
        日志按天滚动存放在 ``logs/``；导出的 zip 落在 ``data/exports/``
        （已在 .gitignore），文件名带时间戳，可直接下载或作为附件。
    """
    if not config.LOG_DIR.is_dir():
        return api_error(ErrorCode.NOT_FOUND, "未找到日志目录，当前运行环境可能未开启文件日志。")
    log_files = sorted(p for p in config.LOG_DIR.glob("*.log*") if p.is_file())
    if not log_files:
        return api_error(ErrorCode.NOT_FOUND, "日志目录为空，暂无日志可导出。")

    stamp = utils.bj_now_aware().strftime("%Y%m%d_%H%M%S")
    export_dir = config.EXPORT_DIR
    export_dir.mkdir(parents=True, exist_ok=True)
    target = export_dir / f"logs_{stamp}.zip"

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in log_files:
            bundle.write(path, arcname=path.name)

    total = sum(p.stat().st_size for p in log_files)
    logger.info("日志导出：%d 个文件，%d 字节 → %s", len(log_files), total, target.name)
    return FileResponse(
        path=str(target),
        media_type="application/zip",
        filename=f"openbb_logs_{stamp}.zip",
    )
