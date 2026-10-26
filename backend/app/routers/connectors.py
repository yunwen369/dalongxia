"""连接器状态路由：/api/connectors/status（批次 5.4）。

目的：把「每条业务能力实际由哪个源支撑、哪些暂不可用」透明化，供前端连接器页展示，
并作为 D3 决策（港美股个股新闻/板块暂无免费通道）的界面落地，避免用户误以为已支持。

返回内容：
- datasources：与 /api/health 同源的能力探测快照（core.capabilities，P1-05）；
- markets：a/hk/us 四类能力（quote/kline/minute/index/stock_news/sector）可用性与实际源；
- gaps：明确标注「暂不可用」的项与原因（不编造能力）。

R3-03 可操作能力（本批新增）：
- POST /api/connectors/test    对指定源跑一次真实连通性探测（不写缓存、不改配置）；
- POST /api/connectors/toggle  启用/停用某个源，落盘 data/connectors.json；
  停用后降级链在 core/resilient.with_fallback 统一跳过该源（全部停用时自动忽略，防误关）。

R3-13 展示能力（2026-10-25 第三轮四域增强）：
- /status 的 datasources 每项附 rate_limit（限流策略）/ ttl（缓存）/ fallback（降级链）；
- markets 增加 fund（场外基金）列，构成四市场能力矩阵，缺口一律显式标注。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Body, Query

from backend.app.datasource import utils
from backend.app.core import capabilities, source_prefs
from backend.app.core.errors import ErrorCode, NonRetryableError, api_error, ok
from backend.app.datasource import overseas

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/connectors", tags=["连接器"])

# A股能力矩阵（阶段 4/5.1/5.2 已交付能力）
_A_SHARE = {
    "quote": {"available": True, "source": "腾讯/新浪"},
    "kline": {"available": True, "source": "东财/新浪/腾讯"},
    "minute": {"available": True, "source": "东财/新浪/腾讯"},
    "index": {"available": True, "source": "腾讯/新浪"},
    "stock_news": {"available": True, "source": "东财 stock_news_em"},
    "sector": {"available": True, "source": "同花顺/新浪/申万"},
}

# 场外基金能力矩阵（R3-13 C-04 第四市场列）：只登记已交付能力，缺口显式标注
_FUND = {
    "quote": {"available": False, "source": "—", "note": "场外基金无盘中报价，仅有 T+1 净值与盘中估值（估算）"},
    "kline": {"available": False, "source": "—", "note": "场外基金无 K 线概念，以净值序列呈现"},
    "minute": {"available": False, "source": "—", "note": "场外基金无分时；养基宝通道可给盘中估值涨跌"},
    "index": {"available": False, "source": "—", "note": "基金无指数点位；以单位净值/累计净值替代"},
    "stock_news": {"available": False, "source": "—", "note": "基金无个股新闻；资讯见基金公告与研报"},
    "sector": {"available": False, "source": "—", "note": "基金无板块专项；可通过重仓/板块暴露分析"},
}

# 每数据源的限流与降级策略（R3-13 C-02 展示；键 = config.DATA_SOURCES 的 label，
# 与 07_数据 数据字典、各 provider 实现同口径；AI 服务商（DeepSeek/通义）不计入数据连接器，
# 其密钥与测试连接在「设置 → AI 设置」管理）
_SOURCE_POLICY = {
    "akshare（东财/新浪等）": {
        "rate_limit": "封装多站点，各站限流不一；东财 push2 本机网络受限（已实测登记）",
        "ttl": "30s / 60s",
        "fallback": "东财/新浪多站点互备；龙虎榜/资金流/财报主源",
    },
    "腾讯行情": {
        "rate_limit": "免费公开未限流；批量报价一次最多 60 只",
        "ttl": "15s",
        "fallback": "行情主源 → 新浪 → 东财快照",
    },
    "东方财富": {
        "rate_limit": "免费公开；push2 本机网络受限（已实测登记）",
        "ttl": "30s / 60s",
        "fallback": "龙虎榜/资金流/财报主源；失败给中文错误态",
    },
    "新浪财经": {
        "rate_limit": "免费公开，偶发 456 限流（已退避重试）",
        "ttl": "15s",
        "fallback": "行情备源；同花顺 V8 不可用时承接板块/快讯",
    },
    "同花顺": {
        "rate_limit": "V8 签名接口；多线程并发首建上下文曾崩溃（进程级 RLock 已根治）",
        "ttl": "60s",
        "fallback": "板块主源；冻结形态降级走新浪备源",
    },
    "财联社": {
        "rate_limit": "公开快讯接口，访问频率温和",
        "ttl": "60s",
        "fallback": "快讯主源；冻结形态改走东财/新浪",
    },
    "天天基金/蛋卷": {
        "rate_limit": "净值接口需登录态；未登录仅公开估值可用",
        "ttl": "300s",
        "fallback": "基金净值/业绩主源；失败回落东财 lsjz",
    },
    "养基宝（非官方）": {
        "rate_limit": "个人账户签名接口（md5 签名）；本产品 30s 缓存限频",
        "ttl": "30s",
        "fallback": "盘中估值一级通道；失败回落蛋卷 T+1",
    },
}


@router.get("/status")
def connectors_status() -> Dict[str, Any]:
    """返回数据源开关、限流降级策略与四市场能力矩阵。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    capability = overseas.capability_matrix()
    markets = {"a": _A_SHARE, "hk": capability["hk"], "us": capability["us"], "fund": _FUND}

    gaps = []
    for market, label in (("hk", "港股"), ("us", "美股"), ("fund", "场外基金")):
        for feature, name in (("stock_news", "个股新闻"), ("sector", "板块专项"),
                              ("quote", "盘中报价"), ("kline", "K 线"),
                              ("minute", "分时"), ("index", "指数点位")):
            cell = markets[market].get(feature) or {}
            if not cell.get("available"):
                gaps.append({
                    "market": market,
                    "market_label": label,
                    "feature": feature,
                    "feature_label": name,
                    "reason": cell.get("note") or "该市场暂无此能力通道",
                    "decision": "D3：如实标注暂不可用，不编造数据",
                })

    # 数据源状态与 /api/health 同源（P1-05）：同一份能力探测快照，避免两处说法不一致
    status_map = capabilities.datasource_status()
    sources = []
    for key, meta in status_map.items():
        policy = _SOURCE_POLICY.get(meta.get("label") or key, {})
        sources.append({
            "key": key,
            "label": meta.get("label") or key,
            "enabled": bool(meta.get("enabled")),
            "stage": meta.get("stage"),
            "active": bool(meta.get("active")),
            "status": meta.get("status"),
            "detail": meta.get("detail"),
            "probed_at": meta.get("probed_at"),
            # R3-13：限流与降级策略（表格列）
            "rate_limit": policy.get("rate_limit", "未登记"),
            "ttl": policy.get("ttl", "—"),
            "fallback": policy.get("fallback", "—"),
        })

    return ok({
        "datasources": sources,
        "markets": markets,
        "gaps": gaps,
        "disabled": sorted(source_prefs.disabled_sources()),
        "source": "本机配置 + 实测能力矩阵",
        "caliber": "能力矩阵按 akshare 1.19.1 与腾讯实测；不可用项为 D3 已确认缺口",
        "as_of": utils.now_text(),
        "market_status": "配置快照",
        "is_realtime": False,
    })


@router.post("/test")
def connectors_test(source: str = Query(..., description="源名，如 财联社/蛋卷/同花顺")) -> Dict[str, Any]:
    """对指定源跑一次真实连通性探测（不写缓存、不改配置）。

    Args:
        source: 源名，取值见 /api/connectors/status 的 datasources[].label。

    Returns:
        dict: 统一信封；data 含 ok / detail / probed_at。
    """
    result = capabilities.probe_one(source)
    if result is None:
        return api_error(ErrorCode.NOT_FOUND, f"未登记的源：{source}（可用源见连接器状态列表）")
    return ok({
        "source": source,
        "ok": bool(result.get("ok")),
        "detail": result.get("detail") or "",
        "probed_at": result.get("probed_at") or utils.now_text(),
        "caliber": "真实 HTTP 探测（2 秒超时），不读缓存、不写缓存",
        "is_realtime": True,
    })


@router.post("/toggle")
def connectors_toggle(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """启用/停用某个源（落盘 data/connectors.json）。

    Args:
        payload: {"source": "同花顺", "enabled": false}。

    Returns:
        dict: 统一信封；data 含 disabled（停用源列表）。
    """
    source = str(payload.get("source") or "").strip()
    if not source:
        return api_error(ErrorCode.INVALID_PARAM, "缺少 source 参数。")
    enabled = bool(payload.get("enabled", True))
    try:
        disabled = source_prefs.toggle(source, enabled)
    except NonRetryableError as exc:
        return api_error(ErrorCode.UPSTREAM_ERROR, str(exc))
    return ok({
        "source": source,
        "enabled": enabled,
        "disabled": sorted(disabled),
        "note": "停用后降级链会自动跳过该源；若全部源被停用，系统将忽略停用设置以保证可用。",
        "caliber": "本地偏好（data/connectors.json，原子写），不影响已缓存的历史数据",
        "as_of": utils.now_text(),
    })
