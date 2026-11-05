# -*- coding: utf-8 -*-
"""基金经理画像（第十轮 P2 · `/api/fund/manager`）。

数据源与口径（2026-11-06 实测）
--------------------------------
- 主源：东财基金经理全量表 `ak.fund_manager_em()` —— 36782 行（经理 × 在管基金长表），
  字段：姓名 / 所属公司 / 现任基金代码 / 现任基金 / 累计从业时间(天) /
  现任基金资产总规模(亿元) / 现任基金最佳回报(%)；单次全量约 11 秒；
- 缓存：外层 router 以 24h TTL 缓存全量结果（日更数据，避免每次请求拉全表）；
- 聚合口径：先用基金代码定位经理，再按「姓名 + 所属公司」聚合其在管基金列表；
  规模与最佳回报为**经理级**指标（长表中每行相同），不做求和；
- 未找到经理时返回 `found=false` + 中文说明，不臆造数据。

约束
----
1. 不改动既有 `/api/fund/info` 契约（该端点仍返回 manager 字段，本端点做深度画像）；
2. 失败只影响本端点，不拖垮基金页其他区块；
3. 无 MCP、无付费源依赖。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

SOURCE = "东财基金经理"
CALIBER = "经理画像 · 长表按基金代码定位后聚合（规模/最佳回报为经理级）"


def _to_float(value: Any) -> float | None:
    """安全转 float（空值/异常返回 None，不补 0）。"""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def load_all() -> List[Dict[str, Any]]:
    """拉取东财基金经理全量表（长表，经理 × 在管基金）。

    Returns:
        List[Dict[str, Any]]: 记录列表；失败抛异常由上层转错误码。
    """
    import akshare as ak  # 延迟导入：避免包体启动期加载

    frame = ak.fund_manager_em()
    return frame.to_dict("records")


def get_manager(code: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """按基金代码定位经理并聚合画像。

    Args:
        code: 6 位基金代码。
        rows: `load_all()` 返回的全量长表记录。

    Returns:
        dict: 画像数据；未命中时 `found=False`。
    """
    symbol = (code or "").strip()
    target = None
    for row in rows:
        if str(row.get("现任基金代码") or "").strip() == symbol:
            target = row
            break
    if target is None:
        return {
            "found": False,
            "code": symbol,
            "reason": "该基金未在基金经理表中命中（可能为新发基金或代码有误）",
            "as_of": date.today().isoformat(),
        }

    name = str(target.get("姓名") or "").strip()
    company = str(target.get("所属公司") or "").strip()
    funds: List[Dict[str, Any]] = []
    seen = set()
    for row in rows:
        if str(row.get("姓名") or "").strip() != name:
            continue
        if str(row.get("所属公司") or "").strip() != company:
            continue
        fund_code = str(row.get("现任基金代码") or "").strip()
        if not fund_code or fund_code in seen:
            continue
        seen.add(fund_code)
        funds.append({"code": fund_code, "name": str(row.get("现任基金") or "").strip()})

    tenure_days = _to_float(target.get("累计从业时间"))
    scale = _to_float(target.get("现任基金资产总规模"))
    best = _to_float(target.get("现任基金最佳回报"))
    return {
        "found": True,
        "code": symbol,
        "manager": {
            "name": name,
            "company": company,
            "tenure_days": int(tenure_days) if tenure_days is not None else None,
            "tenure_years": round(tenure_days / 365.0, 1) if tenure_days else None,
            "scale_aum": scale,
            "best_return": best,
            "fund_count": len(funds),
            "funds": funds,
        },
        "source": SOURCE,
        "caliber": CALIBER,
        "as_of": date.today().isoformat(),
        "market_status": "档案快照",
        "is_realtime": False,
    }
