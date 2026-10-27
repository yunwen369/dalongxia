"""基金域路由：/api/fund 下 9 个接口（批次 5.3）。

对应规范 v7.2 §8.6 与基线03 §3.9（14 个接口中含 5 个养基宝接口，属阶段 7；本批实现 9 个）：
- GET /search       基金搜索（代码/简称/拼音）
- GET /realtime     最新净值与日涨幅（T+1 口径，D1 合规）
- GET /nav          净值历史（蛋卷→东财 lsjz）
- GET /holding      重仓股（东财季报）
- GET /info         基金档案（蛋卷 + 雪球）
- GET /performance  阶段业绩/排名/分年度/定投（蛋卷 derived）
- GET /drawdown     最大回撤（净值自算）
- GET /dividend     分红记录（东财 fhsp HTML）
- GET /position     持仓分布（蛋卷需登录，依规范禁用并如实标注）

统一返回 {data,error,error_code}；`source/caliber` 如实标注「T+1 公布净值」，不得伪装盘中实时。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Query

from backend.app import config
from backend.app.datasource import utils
from backend.app.core import cache, user_prefs
from backend.app.core.errors import ErrorCode, NonRetryableError, api_error, ok
from backend.app.datasource import fund as fund_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/fund", tags=["基金"])


def _bad(message: str) -> Dict[str, Any]:
    """参数错误响应。

    Args:
        message: 中文提示文案。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    return api_error(ErrorCode.INVALID_PARAM, message)


def _not_found(message: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """未找到响应。

    Args:
        message: 中文提示文案。
        data: 待包装或清洗的数据主体。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    return api_error(ErrorCode.NOT_FOUND, message, data=data or {})


def _pack(data: Dict[str, Any], source: str, caliber: str, as_of: Optional[str],
          market_status: str = "T+1 公布净值") -> Dict[str, Any]:
    """组装响应，附带数据源/口径/时间。

    Args:
        data: 待包装或清洗的数据主体。
        source: 数据源标识（用于降级链溯源与徽章展示）。
        caliber: 口径说明（展示在口径徽章）。
        as_of: 数据时间（北京时间，格式 YYYY-MM-DD HH:MM:SS）。
        market_status: 市场状态文案（盘中/盘前/盘后/休市/收盘快照）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    payload = dict(data)
    payload.update({
        "source": source,
        "caliber": caliber,
        "as_of": as_of or utils.now_text(),
        "market_status": market_status,
        "is_realtime": False,  # 场外基金本批无盘中估值通道，恒为 False
    })
    return ok(payload)


def _run(key: str, ttl: int, loader):
    """缓存执行，异常统一转中文错误。

    Args:
        key: 缓存键 / 字典键。
        ttl: 缓存有效期（秒）。
        loader: 缓存未命中时的加载函数（返回数据或 {data, error}）。

    Returns:
        dict: 函数内构造的结果字典。
    """
    try:
        return cache.get_or_load(key, ttl, lambda: {"data": loader(), "error": None})
    except Exception as exc:  # noqa: BLE001 - 转结构化错误
        logger.info("基金接口失败：%s（%s）", key, type(exc).__name__)
        return {"data": None, "error": str(exc) or "数据源暂不可用", "error_type": type(exc).__name__}


@router.get("/search")
def fund_search(
    q: str = Query(..., description="代码/简称/拼音"),
    limit: int = Query(20, ge=1, le=50),
) -> Dict[str, Any]:
    """基金搜索。

    Args:
        q: 代码/简称/拼音。
        limit: 返回条数上限（超出上限按上限截断）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    keyword = (q or "").strip()
    if not keyword:
        return _bad("q 参数不可为空")
    result = _run(f"fund:search:{keyword}:{limit}", config.TTL["search"],
                  lambda: fund_provider.get_search(keyword, limit))
    if result["error"]:
        return api_error(ErrorCode.UPSTREAM_ERROR, "基金名录暂不可用，请稍后点击刷新重试。",
                         data={"items": []})
    items = result["data"]
    return _pack({"items": items, "count": len(items), "keyword": keyword},
                 "天天基金", "天天基金全量名录本地过滤（代码/简称/拼音）", None, "名录快照")


@router.get("/estimation")
def fund_estimation_status() -> Dict[str, Any]:
    """盘中估值开关状态（默认关闭，opt-in）。

    Returns:
        dict: 统一信封；data 含 enabled / caliber。
    """
    return ok({
        "enabled": user_prefs.get_flag("fund_estimation"),
        "source": "本地 data/user_prefs.json",
        "caliber": "盘中估值=非官方·拟合估算·仅供参考·不构成投资建议；关闭后回退 T+1 公布净值",
        "as_of": utils.now_text(),
    })


@router.post("/estimation")
def fund_estimation_toggle(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """一键开关盘中估值（关闭即回退 T+1 净值）。

    Args:
        payload: {"enabled": true/false}。

    Returns:
        dict: 统一信封；data 含 enabled。
    """
    if "enabled" not in payload:
        return _bad("缺少 enabled 参数。")
    try:
        prefs = user_prefs.set_flag("fund_estimation", bool(payload.get("enabled")))
    except NonRetryableError as exc:
        return api_error(ErrorCode.UPSTREAM_ERROR, str(exc))
    return ok({
        "enabled": prefs.get("fund_estimation", False),
        "source": "本地 data/user_prefs.json",
        "caliber": "盘中估值=非官方·拟合估算·仅供参考·不构成投资建议；关闭后回退 T+1 公布净值",
        "as_of": utils.now_text(),
    })


@router.get("/realtime")
def fund_realtime(code: str = Query(..., description="6 位基金代码")) -> Dict[str, Any]:
    """最新净值与日涨幅（T+1 口径）。

    Args:
        code: 6 位基金代码。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    symbol = (code or "").strip()
    if not symbol:
        return _bad("code 参数不可为空")
    result = _run(f"fund:realtime:{symbol}", config.TTL["fund_nav"],
                  lambda: fund_provider.get_realtime(symbol))
    if result["error"]:
        # P1-02：标的不存在必须与「通道故障」分离——前者 NOT_FOUND（不该重试/不该显示为 0 净值）
        if result.get("error_type") == "NotFoundError":
            return _not_found("未找到该基金代码的净值数据，请检查代码后重试。",
                              data={"code": symbol, "items": []})
        return api_error(ErrorCode.UPSTREAM_ERROR, "基金净值暂不可用，请稍后点击刷新重试。",
                         data={"code": symbol})
    data = result["data"] or {}
    # 兜底校验（P1-02）：磁盘缓存可能带回修复前的历史错误载荷（nav=0.0 且无净值日期），
    # 这类载荷不得当成功返回，否则前端仍会渲染「净值 0 的真基金」。
    nav_value = data.get("nav")
    if data.get("is_estimate") is not True and not (nav_value and nav_value > 0):
        return _not_found("未找到该基金代码的净值数据，请检查代码后重试。",
                          data={"code": symbol, "items": []})
    return _pack(data, data.get("source") or "蛋卷", data.get("caliber") or "T+1 公布净值",
                 data.get("nav_date"))


@router.get("/nav")
def fund_nav(
    code: str = Query(..., description="6 位基金代码"),
    page: int = Query(1, ge=1, le=200),
    size: int = Query(30, ge=1, le=500),
) -> Dict[str, Any]:
    """净值历史。

    Args:
        code: 6 位基金代码。
        page: 页码（从 1 开始）。
        size: 条数 / 字节数。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    symbol = (code or "").strip()
    if not symbol:
        return _bad("code 参数不可为空")
    result = _run(f"fund:nav:{symbol}:{page}:{size}", config.TTL["fund_nav"],
                  lambda: fund_provider.get_nav(symbol, page, size))
    if result["error"]:
        return api_error(ErrorCode.UPSTREAM_ERROR, "净值历史暂不可用，请稍后点击刷新重试。",
                         data={"items": [], "code": symbol})
    data = result["data"]
    first = (data.get("items") or [{}])[0]
    return _pack(data, data.get("source") or "蛋卷", data.get("caliber") or "",
                 first.get("date"))


@router.get("/holding")
def fund_holding(code: str = Query(..., description="6 位基金代码")) -> Dict[str, Any]:
    """重仓股（季报口径）。

    Args:
        code: 6 位基金代码。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    symbol = (code or "").strip()
    if not symbol:
        return _bad("code 参数不可为空")
    result = _run(f"fund:holding:{symbol}", config.TTL["fund_holding"],
                  lambda: fund_provider.get_holding(symbol))
    if result["error"]:
        return api_error(ErrorCode.UPSTREAM_ERROR, "重仓数据暂不可用，请稍后点击刷新重试。",
                         data={"items": [], "code": symbol})
    data = result["data"]
    return _pack(data, data.get("source") or "东财季报", data.get("caliber") or "",
                 data.get("quarter"), "定期报告")


@router.get("/info")
def fund_info(code: str = Query(..., description="6 位基金代码")) -> Dict[str, Any]:
    """基金档案。

    Args:
        code: 6 位基金代码。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    symbol = (code or "").strip()
    if not symbol:
        return _bad("code 参数不可为空")
    result = _run(f"fund:info:{symbol}", config.TTL["fund_holding"],
                  lambda: fund_provider.get_info(symbol))
    if result["error"]:
        return api_error(ErrorCode.UPSTREAM_ERROR, "基金档案暂不可用，请稍后点击刷新重试。",
                         data={"code": symbol})
    data = result["data"]
    return _pack(data, data.get("source") or "蛋卷", data.get("caliber") or "",
                 data.get("end_date"), "档案快照")


@router.get("/performance")
def fund_performance(code: str = Query(..., description="6 位基金代码")) -> Dict[str, Any]:
    """阶段业绩与排名。

    Args:
        code: 6 位基金代码。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    symbol = (code or "").strip()
    if not symbol:
        return _bad("code 参数不可为空")
    result = _run(f"fund:performance:{symbol}", config.TTL["fund_performance"],
                  lambda: fund_provider.get_performance(symbol))
    if result["error"]:
        return api_error(ErrorCode.UPSTREAM_ERROR, "阶段业绩暂不可用，请稍后点击刷新重试。",
                         data={"periods": [], "code": symbol})
    data = result["data"]
    return _pack(data, data.get("source") or "蛋卷", data.get("caliber") or "",
                 data.get("end_date"))


@router.get("/drawdown")
def fund_drawdown(
    code: str = Query(..., description="6 位基金代码"),
    start: str = Query("", description="起始日期 YYYY-MM-DD"),
    end: str = Query("", description="结束日期 YYYY-MM-DD"),
) -> Dict[str, Any]:
    """最大回撤（净值自算）。

    Args:
        code: 6 位基金代码。
        start: 起始日期 YYYY-MM-DD。
        end: 结束日期 YYYY-MM-DD。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    symbol = (code or "").strip()
    if not symbol:
        return _bad("code 参数不可为空")
    result = _run(f"fund:drawdown:{symbol}:{start}:{end}", config.TTL["fund_performance"],
                  lambda: fund_provider.get_drawdown(symbol, start, end))
    if result["error"]:
        return api_error(ErrorCode.UPSTREAM_ERROR, "回撤计算暂不可用，请稍后点击刷新重试。",
                         data={"series": [], "code": symbol})
    data = result["data"]
    return _pack(data, data.get("source") or "自算", data.get("caliber") or "",
                 data.get("end"))


@router.get("/dividend")
def fund_dividend(code: str = Query(..., description="6 位基金代码")) -> Dict[str, Any]:
    """分红记录。

    Args:
        code: 6 位基金代码。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    symbol = (code or "").strip()
    if not symbol:
        return _bad("code 参数不可为空")
    result = _run(f"fund:dividend:{symbol}", config.TTL["fund_performance"],
                  lambda: fund_provider.get_dividend(symbol))
    if result["error"]:
        return api_error(ErrorCode.UPSTREAM_ERROR, "分红数据暂不可用，请稍后点击刷新重试。",
                         data={"items": [], "code": symbol})
    data = result["data"]
    first = (data.get("items") or [{}])[0]
    return _pack(data, data.get("source") or "东财", data.get("caliber") or "",
                 first.get("record_date"), "历史记录")


@router.get("/position")
def fund_position(code: str = Query(..., description="6 位基金代码")) -> Dict[str, Any]:
    """持仓分布：蛋卷端点需登录，依规范禁用（如实标注，不编造）。

    Args:
        code: 6 位基金代码。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    symbol = (code or "").strip()
    if not symbol:
        return _bad("code 参数不可为空")
    return api_error(
        ErrorCode.EMPTY_DATA,
        "基金持仓分布需登录蛋卷账号，依规范已禁用该通道；可查看「重仓股」替代。",
        data={"code": symbol, "items": [], "disabled": True,
              "reason": "蛋卷 /djapi/fund/position 需登录（实测 rc=300001）"},
    )
