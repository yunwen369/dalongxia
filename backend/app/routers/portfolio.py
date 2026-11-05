"""持仓域路由：/api/portfolio 下 5 个接口（批次 5.3）。

对应规范 v7.2 §8.4 与基线03 §3.8：
- GET    /portfolio            读取持仓（data/portfolio.json）
- POST   /portfolio            新增持仓
- PUT    /portfolio/{id}       更新持仓
- DELETE /portfolio/{id}       删除持仓
- GET    /portfolio/analysis   持仓 × 实时报价 → 盈亏、市场分布、币种折算

写操作只落本机 `data/portfolio.json`（不入 Git、不上传）；金额口径见 datasource/portfolio.py。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Body, Path, Query

from backend.app.core import cache
from backend.app.core.errors import pack_success, ErrorCode, api_error
from backend.app.datasource import portfolio as store
from backend.app.datasource import portfolio_trend

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/portfolio", tags=["持仓"])


@router.get("")
@router.get("/")
def list_portfolio() -> Dict[str, Any]:
    """读取全部持仓。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    data = store.load()
    return pack_success(
        {"items": data.get("items") or [], "count": len(data.get("items") or []),
         "updated_at": data.get("updated_at")},
        "本地持仓",
        "本地 JSON（data/portfolio.json），单位成本与份额按用户录入",
        data.get("updated_at"),
        "本地快照",
        False,
    )


@router.post("")
@router.post("/")
def create_portfolio(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """新增持仓。

    Args:
        payload: 请求体（JSON 对象）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    try:
        item = store.add_item(payload)
    except ValueError as exc:
        return api_error(ErrorCode.INVALID_PARAM, str(exc))
    cache.clear_cache()  # 持仓变更后清空分析缓存，避免展示旧市值
    return pack_success({"item": item}, "本地持仓", "新增一条本地持仓", None, "本地快照", False)


@router.put("/{item_id}")
def update_portfolio(item_id: str = Path(...), payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """更新持仓。

    Args:
        item_id: 条目主键。
        payload: 请求体（JSON 对象）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    try:
        item = store.update_item(item_id, payload)
    except KeyError:
        return api_error(ErrorCode.NOT_FOUND, "未找到该持仓记录，请刷新后重试。")
    except ValueError as exc:
        return api_error(ErrorCode.INVALID_PARAM, str(exc))
    cache.clear_cache()
    return pack_success({"item": item}, "本地持仓", "按 id 更新本地持仓", None, "本地快照", False)


@router.delete("/{item_id}")
def delete_portfolio(item_id: str = Path(...)) -> Dict[str, Any]:
    """删除持仓。

    Args:
        item_id: 条目主键。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    try:
        store.remove_item(item_id)
    except KeyError:
        return api_error(ErrorCode.NOT_FOUND, "未找到该持仓记录，请刷新后重试。")
    cache.clear_cache()
    return pack_success({"deleted": item_id}, "本地持仓", "删除本地持仓", None, "本地快照", False)


@router.get("/trend")
def portfolio_trend_series(days: int = Query(90, ge=1, le=3650, description="最近 N 个自然日")) -> Dict[str, Any]:
    """持仓收益走势（第十轮 P2 新增 · R13）：逐交易日快照序列，缺日即断点、禁止补零。

    Args:
        days: 回溯自然日数（默认 90）。

    Returns:
        dict: 统一返回结构；无快照时 items=[] + hint 说明（空态非错误）。
    """
    data = portfolio_trend.load_trend(days)
    return pack_success(
        data,
        data.get("source") or "本地快照",
        data.get("caliber") or "",
        data.get("to"),
        "本地快照",
        False,
    )


@router.get("/analysis")
def portfolio_analysis() -> Dict[str, Any]:
    """持仓分析：本币市值/盈亏 + 人民币折算 + 市场分布。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """

    def loader() -> Dict[str, Any]:
        """逐条取价并计算（单条失败不阻断整体）。

        Returns:
            dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
        """
        return store.analyze()

    data = cache.get_or_load("portfolio:analysis", 30, loader)
    return pack_success(
        data,
        "本地持仓",
        data.get("caliber") or "市值=份额×最新价；人民币折算用新浪实时汇率",
        data.get("updated_at"),
        "本地快照",
        False,
    )


@router.get("/attribution")
def portfolio_attribution() -> Dict[str, Any]:
    """组合归因（第七轮 M4）：收益贡献 / 行业暴露 / 集中度 / 情景。

    与 `/analysis` 的分工：`/analysis` 回答「现在值多少」，本端点回答「赚亏从哪来、风险堆在哪」。
    共用同一份台账（data/portfolio.json），不另存数据，因此不会出现双源。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 caliber/as_of/is_realtime。
    """

    def loader() -> Dict[str, Any]:
        """计算归因（行情 60 秒缓存，避免频繁打上游）。

        Returns:
            dict: 归因结果。
        """
        return store.attribution()

    data = cache.get_or_load("portfolio:attribution", 60, loader)
    return pack_success(
        data,
        "本地持仓",
        data.get("caliber") or "权重=市值/合计；贡献=权重×盈亏率；HHI=Σ权重²",
        data.get("as_of"),
        "本地快照",
        False,
    )


@router.post("/import-xlsx")
def portfolio_import_xlsx(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """组合台账 xlsx 一次性导入（第七轮 M11）。

    请求体（**base64 方式**：本机未安装 python-multipart，不走 multipart 上传）：
        {"filename": "持仓.xlsx", "content_base64": "...", "confirm": false}

    - `confirm=false`（默认）：只解析 + 校验，返回预览（不入库）；
    - `confirm=true`：重新解析同一文件，校验通过的行**覆盖写入** `data/portfolio.json`。

    Args:
        payload: 请求体，含 filename / content_base64 / confirm 三个字段。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 含 rows/errors/warnings/summary。
    """
    from backend.app.datasource import xlsx_import

    filename = str(payload.get("filename") or "").strip()
    content = payload.get("content_base64") or ""
    confirm = bool(payload.get("confirm"))
    if not filename:
        return api_error(ErrorCode.INVALID_PARAM, "缺少 filename：请提供原始文件名（决定解析引擎）")
    if not content:
        return api_error(ErrorCode.INVALID_PARAM, "缺少 content_base64：请以 base64 提交文件内容")
    import base64

    try:
        raw = base64.b64decode(content, validate=True)
    except Exception:  # noqa: BLE001 - 编码错误统一转中文
        return api_error(ErrorCode.INVALID_PARAM, "content_base64 不是有效的 base64 编码")
    if len(raw) > 10 * 1024 * 1024:
        return api_error(ErrorCode.INVALID_PARAM, "文件超过 10 MB，请拆分后再导入")

    try:
        preview = xlsx_import.parse(raw, filename)
        if not confirm:
            cache.clear_cache()  # 预览不打缓存，但清一次避免旧分析残留（无副作用）
            return pack_success(
                preview,
                "本地持仓",
                preview.get("caliber") or "只读导入：预览阶段不写库",
                preview.get("as_of"),
                "本地快照",
                False,
            )
        result = xlsx_import.commit(preview["rows"], confirm=True)
        cache.clear_cache()  # 台账变更后清缓存，保证下一次 analysis 读新数据
        merged = dict(preview)
        merged["result"] = result
        return pack_success(
            merged,
            "本地持仓",
            preview.get("caliber") or "已覆盖写入系统台账",
            result.get("updated_at"),
            "本地快照",
            False,
        )
    except ValueError as exc:
        return api_error(ErrorCode.INVALID_PARAM, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("xlsx 导入失败：%s", type(exc).__name__)
        return api_error(ErrorCode.INTERNAL_ERROR, f"xlsx 导入失败（{type(exc).__name__}）")


@router.get("/risk")
def portfolio_risk(force: bool = False) -> Dict[str, Any]:
    """组合风险指标（第七轮 M10）：波动 / 回撤 / 夏普 / α / β / 相关性矩阵。

    口径：按当前持仓权重回溯（非真实逐日净值）；基准沪深300；场外基金不纳入；
    样本 < 30 日 → `reliable=false`，仅给数值不给结论标签。

    Args:
        force: true 时绕过 6 小时缓存。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 含 metrics/correlation/weights/missing。
    """
    from backend.app.datasource import portfolio_risk as risk

    try:
        data = risk.risk_report(force=force)
        return pack_success(
            data,
            "本地持仓",
            data.get("caliber") or "按当前持仓权重回溯，非真实逐日净值",
            data.get("as_of"),
            "本地快照",
            False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("组合风险计算失败：%s", type(exc).__name__)
        return api_error(ErrorCode.INTERNAL_ERROR, f"组合风险计算失败（{type(exc).__name__}）")
