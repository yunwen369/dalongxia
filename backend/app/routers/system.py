"""系统域路由：健康检查与缓存清理。

对应规范 v7.0 §8.7 system 域。所有响应统一为 {data, error} 结构：
成功时 error=null；失败时 data 为空结构、error 为自然中文文案。
本阶段只实现 /api/health 与 /api/cache/clear，不提供任何业务数据接口。
"""

from __future__ import annotations

import importlib.util
import logging
import platform
from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter

from backend.app import config
from backend.app.core import cache
from backend.app.core.errors import ok

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["系统"])


def _datasource_status() -> Dict[str, Dict[str, Any]]:
    """汇总各数据源的真实接入状态。

    Returns:
        Dict[str, Dict[str, Any]]: 每个数据源的启用状态、依赖安装状态与阶段说明。

    Notes:
        本函数只做依赖探测（find_spec），不导入 akshare、不发网络请求，
        因此不会触发任何业务数据拉取，也不会伪造数据。
    """
    result: Dict[str, Dict[str, Any]] = {}
    for key, meta in config.DATA_SOURCES.items():
        module_name = meta.get("module")
        installed = None
        if module_name:
            try:
                installed = importlib.util.find_spec(str(module_name)) is not None
            except (ImportError, ValueError):
                installed = False

        if not meta.get("enabled"):
            status_text = "已禁用"
        elif meta.get("active"):
            status_text = "已接入"
        elif meta.get("stage") == "installed":
            status_text = "已安装（业务待接入）" if installed else "依赖缺失"
        else:
            status_text = "待接入"

        result[key] = {
            "label": meta.get("label", key),
            "enabled": bool(meta.get("enabled")),
            "installed": installed,
            "status": status_text,
        }
    return result


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
        "server_time": datetime.now().astimezone().isoformat(timespec="seconds"),
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
