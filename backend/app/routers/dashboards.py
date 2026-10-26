# -*- coding: utf-8 -*-
"""仪表板路由：/api/dashboards（R3-02 · 分组 / 增删改 / 复制 / 排序）。

契约（统一信封 {data,error,error_code}）：
- GET  /api/dashboards          读取全部仪表板 + 分组清单
- PUT  /api/dashboards          整表保存（前端在内存完成增删改/复制/排序/分组后一次提交）

设计取舍：仪表板的增删改都是纯前端状态变更，逐操作开 REST 端点会让
「撤销/批量排序」难以原子化，因此这里只保留整表读写；后端负责结构校验、
分组汇总与原子落盘（data/dashboards.json），并保证任何非法输入不落盘。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Body

from backend.app.datasource import utils
from backend.app.core.errors import ErrorCode, NonRetryableError, api_error, ok
from backend.app.datasource import dashboards as store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboards", tags=["仪表板"])


@router.get("")
def get_dashboards() -> Dict[str, Any]:
    """读取全部仪表板与分组清单。

    Returns:
        dict: 统一信封；data 含 dashboards / activeId / groups / source / as_of。
    """
    try:
        data = store.load()
    except Exception as exc:  # noqa: BLE001 - 读取异常给确定性错误
        logger.warning("仪表板读取失败：%s（%s）", type(exc).__name__, exc)
        return api_error(ErrorCode.UPSTREAM_ERROR, "仪表板配置读取失败，请稍后重试。")
    return ok({
        **data,
        "groups": store.groups(data),
        "source": "本地 data/dashboards.json",
        "caliber": "用户自建布局（本地单机，不上传云端）",
        "as_of": utils.now_text(),
    })


@router.put("")
def put_dashboards(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """整表保存仪表板配置。

    Args:
        payload: {dashboards: [...], activeId: "..."}。

    Returns:
        dict: 统一信封；data 为落盘后的规范化结构。
    """
    try:
        data = store.save(payload)
    except NonRetryableError as exc:
        return api_error(ErrorCode.INVALID_PARAM, str(exc))
    except Exception as exc:  # noqa: BLE001 - 落盘异常
        logger.warning("仪表板保存失败：%s（%s）", type(exc).__name__, exc)
        return api_error(ErrorCode.UPSTREAM_ERROR, "仪表板保存失败，请检查磁盘权限后重试。")
    return ok({
        **data,
        "groups": store.groups(data),
        "source": "本地 data/dashboards.json",
        "caliber": "用户自建布局（本地单机，不上传云端）",
        "as_of": utils.now_text(),
    })
