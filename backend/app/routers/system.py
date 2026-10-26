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
import platform
import zipfile
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Body
from fastapi.responses import FileResponse

from backend.app import config
from backend.app.core import cache, capabilities
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


@router.get("/health")
def health() -> Dict[str, Any]:
    """健康检查接口。

    Returns:
        Dict[str, Any]: {"data": {build, app, 数据源状态, ...}, "error": null}
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
        # 仅暴露是否已配置与来源，绝不返回密钥内容
        "ai": {"configured": ai_config.configured, "source": ai_config.source},
        "server_time": utils.bj_now_aware().isoformat(timespec="seconds"),
    }
    return ok(data)


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


# 恢复出厂可清理的范围（白名单）：只含应用偏好，绝不触碰用户数据与密钥
# 说明：路径在调用时构建（不建模块级常量），便于测试隔离与冻结形态 DATA_DIR 切换
_FACTORY_SCOPE_NAMES = ("prefs", "connectors", "dashboards", "ai")


def _factory_scopes() -> Dict[str, Path]:
    """构造恢复出厂白名单（scope 名 → 目标文件路径）。

    Returns:
        Dict[str, Path]: 应用偏好文件映射；**不含持仓/自选/密钥**。
    """
    return {
        "prefs": config.DATA_DIR / "prefs.json",
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
