"""连接器状态路由：/api/connectors/status（批次 5.4）。

目的：把「每条业务能力实际由哪个源支撑、哪些暂不可用」透明化，供前端连接器页展示，
并作为 D3 决策（港美股个股新闻/板块暂无免费通道）的界面落地，避免用户误以为已支持。

返回内容：
- datasources：system 处已有的数据源开关状态（复用 config.DATA_SOURCES）；
- markets：a/hk/us 四类能力（quote/kline/minute/index/stock_news/sector）可用性与实际源；
- gaps：明确标注「暂不可用」的项与原因（不编造能力）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter

from backend.app import config
from backend.app.core.errors import ok
from backend.app.datasource import overseas

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


@router.get("/status")
def connectors_status() -> Dict[str, Any]:
    """返回数据源开关与三市场能力矩阵。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    capability = overseas.capability_matrix()
    markets = {"a": _A_SHARE, "hk": capability["hk"], "us": capability["us"]}

    gaps = []
    for market, label in (("hk", "港股"), ("us", "美股")):
        for feature, name in (("stock_news", "个股新闻"), ("sector", "板块专项")):
            if not markets[market][feature]["available"]:
                gaps.append({
                    "market": market,
                    "market_label": label,
                    "feature": feature,
                    "feature_label": name,
                    "reason": markets[market][feature]["note"],
                    "decision": "D3：如实标注暂不可用，不编造数据",
                })

    sources = []
    for key, meta in config.DATA_SOURCES.items():
        sources.append({
            "key": key,
            "label": meta.get("label") or key,
            "enabled": bool(meta.get("enabled")),
            "stage": meta.get("stage"),
            "active": bool(meta.get("active")),
        })

    return ok({
        "datasources": sources,
        "markets": markets,
        "gaps": gaps,
        "source": "本机配置 + 实测能力矩阵",
        "caliber": "能力矩阵按 akshare 1.19.1 与腾讯实测；不可用项为 D3 已确认缺口",
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "market_status": "配置快照",
        "is_realtime": False,
    })
