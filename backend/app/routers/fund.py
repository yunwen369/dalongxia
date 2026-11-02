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
- GET /position     持仓分布（东财 F10 行业配置，季报口径）

统一返回 {data,error,error_code}；`source/caliber` 如实标注「T+1 公布净值」，不得伪装盘中实时。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Query

from backend.app import config
from backend.app.datasource import utils
from backend.app.core import cache, user_prefs
from backend.app.core.errors import pack_success, ErrorCode, NonRetryableError, api_error, ok
from backend.app.datasource import fund as fund_provider
from backend.app.datasource import fund_combo

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
    return pack_success(data, source, caliber, as_of, market_status, False)  # 场外基金无盘中估值通道


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
        # 代码不存在 → NOT_FOUND（快速失败）；通道异常 → UPSTREAM_ERROR（可重试）
        if result.get("error_type") == "NotFoundError":
            return api_error(ErrorCode.NOT_FOUND, f"未找到基金代码 {symbol}，请核对后重试。",
                             data={"items": [], "code": symbol})
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
    """持仓分布（行业配置，东财 F10）。

    Args:
        code: 6 位基金代码。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    symbol = (code or "").strip()
    if not symbol:
        return _bad("code 参数不可为空")
    try:
        payload = fund_provider.get_position(symbol)
    except Exception as exc:  # noqa: BLE001 - 上游失败转中文错误态
        logger.info("基金持仓分布取数失败 %s：%s", symbol, type(exc).__name__)
        return api_error(
            ErrorCode.UPSTREAM_ERROR,
            "基金持仓分布暂不可用，请稍后重试。",
            data={"code": symbol, "items": []},
        )
    return _pack(
        {
            "code": symbol,
            "name": payload.get("name"),
            "report_date": payload.get("report_date"),
            "items": payload.get("items") or [],
            "count": len(payload.get("items") or []),
        },
        payload.get("source") or "东财 F10",
        payload.get("caliber") or "",
        payload.get("report_date"),
        "季报口径",
    )


# =============================================================
# 第七轮 M5：基金组合（筛选与诊断，不荐基）
# =============================================================


@router.get("/combo/evaluate")
def fund_combo_evaluate(
    codes: str = Query(..., description="逗号分隔的基金代码，最多 30 只"),
    risk: str = Query("balanced", description="conservative / balanced / aggressive"),
    force: bool = Query(False, description="true=绕过缓存"),
) -> Dict[str, Any]:
    """批量评估基金：六维打分 + 相关性 + 约束配权。

    **合规边界**：只做筛选与结构诊断，不输出预期收益、不给买入评级。

    Args:
        codes: 逗号分隔的基金代码。
        risk: 风险档位。
        force: 是否绕过缓存。

    Returns:
        Dict[str, Any]: {"data": {...}, "error": null}
    """
    code_list = [item.strip() for item in str(codes or "").replace("　", " ").split(",") if item.strip()]
    if not code_list:
        return api_error(ErrorCode.INVALID_PARAM, "请提供至少 1 只基金代码（codes 用逗号分隔）")
    profile = str(risk or "balanced").strip()
    if profile not in ("conservative", "balanced", "aggressive"):
        return api_error(ErrorCode.INVALID_PARAM, "risk 仅支持 conservative / balanced / aggressive")
    cache_key = f"fund:combo:evaluate:{profile}:{','.join(sorted(code_list))}"

    def loader() -> Dict[str, Any]:
        """计算打分与配权（单只基金要拉净值 + 排名，30 只约 30–60 秒，故缓存 6 小时）。

        Returns:
            Dict[str, Any]: 评估结果。
        """
        return fund_combo.evaluate(code_list, profile)

    try:
        data = loader() if force else cache.get_or_load(cache_key, 21600, loader)
    except ValueError as exc:
        return api_error(ErrorCode.INVALID_PARAM, str(exc))
    except Exception as exc:  # noqa: BLE001 - 上游异常转可读错误
        logger.warning("基金组合评估失败：%s", type(exc).__name__)
        return api_error(ErrorCode.UPSTREAM_ERROR, f"评估失败（{type(exc).__name__}），请稍后重试")
    return ok({
        **data,
        "as_of": utils.now_text(utils.bj_now()),
        "is_realtime": False,
    })


@router.get("/combo/diagnose")
def fund_combo_diagnose(
    risk: str = Query("balanced", description="conservative / balanced / aggressive"),
    force: bool = Query(False, description="true=绕过缓存"),
) -> Dict[str, Any]:
    """诊断台账里的基金持仓：目标权重 vs 实际权重、相关性、偏离与再平衡提示。

    实际权重读 `data/portfolio.json`（唯一台账），不另存数据。

    Args:
        risk: 风险档位。
        force: 是否绕过缓存。

    Returns:
        Dict[str, Any]: {"data": {...}, "error": null}
    """
    profile = str(risk or "balanced").strip()
    if profile not in ("conservative", "balanced", "aggressive"):
        return api_error(ErrorCode.INVALID_PARAM, "risk 仅支持 conservative / balanced / aggressive")
    cache_key = f"fund:combo:diagnose:{profile}"

    def loader() -> Dict[str, Any]:
        """诊断（依赖持仓变动，缓存 10 分钟）。

        Returns:
            Dict[str, Any]: 诊断结果。
        """
        return fund_combo.diagnose(profile)

    try:
        data = loader() if force else cache.get_or_load(cache_key, 600, loader)
    except ValueError as exc:
        return api_error(ErrorCode.INVALID_PARAM, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("基金组合诊断失败：%s", type(exc).__name__)
        return api_error(ErrorCode.UPSTREAM_ERROR, f"诊断失败（{type(exc).__name__}），请稍后重试")
    return ok(data)


@router.get("/combo/weight")
def fund_combo_weight(
    codes: str = Query(..., description="基金代码，逗号分隔（2–30 只）"),
    risk: str = Query("balanced", description="conservative / balanced / aggressive"),
    crowd: bool = Query(True, description="是否启用拥挤度降权（M14）"),
    force: bool = Query(False, description="true=绕过缓存"),
) -> Dict[str, Any]:
    """权重明细（第七轮 M14）：基础权重 × 拥挤度折扣 → 最终权重。

    拥挤度代理=该基金第一大重仓股所属行业的同花顺板块分数（调 M3 同一函数）。
    折扣档：<60 ×1.00 / 60–79 ×0.85 / 80–100 ×0.60；折后内部归一并对单只上限裁剪。

    Args:
        codes: 逗号分隔的基金代码。
        risk: 风险档位。
        crowd: False 时全部 ×1.00（对照用）。
        force: 是否绕过缓存。

    Returns:
        Dict[str, Any]: {"data": {items, totals, missing, caliber, disclaimer}}
    """
    profile = str(risk or "balanced").strip()
    if profile not in ("conservative", "balanced", "aggressive"):
        return api_error(ErrorCode.INVALID_PARAM, "risk 仅支持 conservative / balanced / aggressive")
    code_list = [item.strip() for item in str(codes or "").split(",") if item.strip()]
    if not code_list:
        return api_error(ErrorCode.INVALID_PARAM, "请提供基金代码（codes），逗号分隔")
    if len(code_list) > 30:
        return api_error(ErrorCode.INVALID_PARAM, "最多 30 只基金")
    cache_key = f"fund:combo:weight:{profile}:{int(bool(crowd))}:{','.join(sorted(code_list))}"

    def loader() -> Dict[str, Any]:
        """计算权重明细（10 分钟缓存：依赖净值与拥挤度）。

        Returns:
            Dict[str, Any]: 权重明细。
        """
        return fund_combo.weight_breakdown(code_list, profile, crowd_discount=bool(crowd))

    try:
        data = loader() if force else cache.get_or_load(cache_key, 600, loader)
    except ValueError as exc:
        return api_error(ErrorCode.INVALID_PARAM, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("权重明细计算失败：%s", type(exc).__name__)
        return api_error(ErrorCode.UPSTREAM_ERROR, f"权重明细计算失败（{type(exc).__name__}），请稍后重试")
    return ok(data)


@router.get("/candidates")
def fund_candidates(
    market: str = Query("a", description="市场，目前仅 a"),
    limit: int = Query(200, ge=1, le=500, description="返回上限"),
    force: bool = Query(False, description="true=绕过缓存"),
) -> Dict[str, Any]:
    """全市场候选池（第七轮 M13）：复用 M9 快照 + 「基金友好」模板。

    只按条件筛选，不排序打分、不给推荐语；排序与打分交给「候选评估」。

    Args:
        market: 市场（仅 a）。
        limit: 返回上限。
        force: 是否绕过缓存。

    Returns:
        Dict[str, Any]: {"data": {ready, total_matched, items, template, missing, snapshot}}
    """
    from backend.app.datasource import screener

    cache_key = f"fund:candidates:{str(market or 'a').lower()}:{limit}"

    def loader() -> Dict[str, Any]:
        """读快照筛选（5 分钟缓存：快照每日一更）。

        Returns:
            Dict[str, Any]: 候选池结果。
        """
        return screener.run_candidates(limit=limit, market=market)

    try:
        data = loader() if force else cache.get_or_load(cache_key, 300, loader)
    except ValueError as exc:
        return api_error(ErrorCode.INVALID_PARAM, str(exc))
    except RuntimeError as exc:
        # P3-1：首次进入不得只让用户「自己去点按钮」——自动启动建库，
        # 并把进度状态回给前端展示（空态给下一步动作，而不是死路）。
        started = False
        try:
            started = bool(screener.start_build_async(reason="candidates").get("started"))
        except Exception:  # noqa: BLE001 - 建库启动失败不改变错误语义
            started = False
        hint = "已自动开始构建全市场快照，约 1–2 分钟完成，稍后重试即可看到候选"
        if not started:
            hint = "全市场快照正在构建中，请稍后重试（进度见筛选器页状态卡）"
        return api_error(ErrorCode.EMPTY_DATA, f"{exc}；{hint}", data={"items": [], "building": True})
    except Exception as exc:  # noqa: BLE001
        logger.warning("候选池计算失败：%s", type(exc).__name__)
        return api_error(ErrorCode.INTERNAL_ERROR, f"候选池计算失败（{type(exc).__name__}）")
    return ok(data)
