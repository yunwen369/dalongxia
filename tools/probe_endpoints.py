# -*- coding: utf-8 -*-
"""第八轮全端点探针（规范 §11.4 / §11.11 端点扫描基线）。

用途：对真实运行实例做「全量 GET 端点 + 只读 POST」实测，产出可复算的分类结果：
    DATA（有真实数据）/ EMPTY_OK（合规空态）/ DEGRADED（降级但可用）/ ERROR_BIZ（业务错误）/ FAIL（HTTP/契约失败）

判据（与规范一致）：
1. HTTP 必须 200（本工程约定错误也走 200 + error_code）；
2. 成功响应 ``data`` 必须含 source/caliber/as_of/market_status/is_realtime 五字段；
3. 错误文案不得含堆栈（Traceback / File " / raise）；
4. 每个端点记录耗时，超 15s 记 SLOW（规范 §6.9 单接口超时预期）。

用法：
    python tools/probe_endpoints.py --base http://127.0.0.1:6903 --out report.json
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Tuple

# 必填参数的基线取值（未列出的端点会被记为 SKIPPED_MISSING_PARAM）
BASELINE: Dict[str, Dict[str, Any]] = {
    "/api/market/quote": {"symbol": "600519", "market": "a"},
    "/api/market/kline": {"symbol": "600519", "market": "a", "period": "daily"},
    "/api/market/minute": {"symbol": "600519", "market": "a", "period": "1"},
    "/api/market/search": {"q": "茅台"},
    "/api/market/index/kline": {"code": "000001", "period": "daily"},
    "/api/sector/kline": {"board": "半导体", "period": "daily"},
    "/api/sector/cons": {"board": "银行"},
    "/api/sector/crowding": {"board": "银行"},
    "/api/sector/trend": {"boards": "半导体,银行", "days": 20},
    "/api/news/stock": {"symbol": "600519", "limit": 5},
    "/api/news/sector": {"sector": "半导体", "limit": 5},
    "/api/news/financial-search": {"q": "茅台"},
    "/api/news/financial-detail": {"code": "600519", "market": "a",
                                   "title": "贵州茅台2026年第三季度报告"},
    "/api/stock/profile": {"symbol": "600519"},
    "/api/stock/holders": {"symbol": "600519"},
    "/api/stock/financial": {"symbol": "600519"},
    "/api/stock/financial-summary": {"symbol": "600519"},
    "/api/stock/financial-trend": {"symbol": "600519"},
    "/api/stock/financial-indicator": {"symbol": "600519"},
    "/api/stock/financial-report": {"symbol": "600519", "statement": "利润表"},
    "/api/stock/estimates": {"symbol": "600519"},
    "/api/stock/fcf-health": {"symbol": "600519"},
    "/api/stock/peer-compare": {"symbols": "600519,000858"},
    "/api/stock/realtime-panel": {"symbol": "600519"},
    "/api/stock/fund-dist": {"symbol": "600519"},
    "/api/fund/nav": {"code": "017811"},
    "/api/fund/info": {"code": "017811"},
    "/api/fund/performance": {"code": "017811"},
    "/api/fund/dividend": {"code": "017811"},
    "/api/fund/holding": {"code": "017811"},
    "/api/fund/drawdown": {"code": "017811"},
    "/api/fund/estimation": {"code": "017811"},
    "/api/fund/search": {"q": "东方"},
    "/api/fund/realtime": {"code": "017811"},
    "/api/fund/position": {"code": "017811"},
    "/api/fund/combo/evaluate": {"codes": "017811"},
    "/api/fund/combo/diagnose": {"codes": "017811"},
    "/api/fund/combo/weight": {"codes": "017811"},
    "/api/sentiment/stock": {"symbol": "600519"},
    "/api/sentiment/board": {"board": "半导体"},
    "/api/valuation/percentile": {"market": "a"},
    "/api/moneyflow/stock": {"symbol": "600519"},
    "/api/moneyflow/lhb": {},
    "/api/moneyflow/north": {},
    "/api/watchlist/quote": {},
    # 无必填参数的只读 GET（基线为空 dict，仍要实测契约与四态）
    "/api/ready": {},
    "/api/health": {},
    "/api/ai/config": {},
    "/api/alerts": {},
    "/api/alerts/events": {},
    "/api/cache/stats": {},
    "/api/connectors/status": {},
    "/api/dashboards": {},
    "/api/fund/candidates": {"limit": 5},
    "/api/fund/yjb/status": {},
    "/api/fund/yjb/collect": {},
    "/api/fund/yjb/holdings": {"account_id": "all"},
    "/api/macro/overview": {},
    "/api/market/index": {"market": "all"},
    "/api/market/index-kline": {"code": "000001"},
    "/api/moneyflow/lhb-broker": {},
    "/api/moneyflow/lhb-institution": {},
    "/api/moneyflow/north-top": {},
    "/api/moneyflow/northbound": {},
    "/api/news/ai-feed": {"limit": 10},
    "/api/news/board-tree": {},
    "/api/news/flash": {"limit": 10},
    "/api/news/global": {"limit": 10},
    "/api/news/sector-search": {"sector": "半导体", "limit": 5},
    "/api/portfolio": {},
    "/api/portfolio/analysis": {},
    "/api/portfolio/attribution": {},
    "/api/portfolio/risk": {},
    "/api/screener/status": {},
    "/api/sector/concept": {"market": "a"},
    "/api/sector/concept/health": {"days": 10},
    "/api/sector/crowding/rankings": {"kind": "industry", "limit": 20},
    "/api/sector/fundflow": {"market": "a"},
    "/api/sector/margin": {"kind": "industry"},
    "/api/sector/heatmap": {"market": "a", "kind": "industry"},
    "/api/sector/industry": {"market": "a"},
    "/api/sector/summary": {"market": "a"},
    "/api/sentiment/market": {"market": "a"},
    "/api/system/desktop-prefs": {},
    "/api/watchlist": {},
    "/api/widgets": {},
}

# 只读 POST（不改状态）
READONLY_POST: Dict[str, Dict[str, Any]] = {
    "/api/screener/run": {"factor": "pct", "op": ">", "value": 0, "limit": 5},
}

SKIP_PATHS = {
    "/api/logs/export",          # 返回 zip，非 JSON 契约
    "/api/system/factory-reset",  # 破坏性
    "/api/cache/clear",          # 破坏性
    "/api/system/shutdown",      # 破坏性
}


def _call(base: str, method: str, path: str, params: Dict[str, Any]) -> Tuple[int, float, str]:
    """发一次 HTTP 请求，返回 (状态码, 耗时秒, 响应文本)。"""
    url = f"{base}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if method == "GET":
        if params:
            url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    else:
        data = json.dumps(params, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            body = response.read().decode("utf-8", "replace")
            return response.status, time.time() - started, body
    except urllib.error.HTTPError as exc:  # 4xx/5xx
        return exc.code, time.time() - started, exc.read().decode("utf-8", "replace")


def _classify(payload: Any) -> Tuple[str, str]:
    """按 data/error 判定类别，返回 (类别, 说明)。"""
    if not isinstance(payload, dict) or "data" not in payload:
        return "FAIL", "响应不是 {data,error} 信封"
    data = payload.get("data")
    error = payload.get("error")
    code = payload.get("error_code")
    if error:
        text = str(error)
        if "Traceback" in text or 'File "' in text or "raise " in text:
            return "FAIL", "错误文案含堆栈"
        if code == "EMPTY_DATA":
            return "EMPTY_OK", text[:40]
        if code in ("NOT_FOUND", "INVALID_PARAM", "NOT_CONFIGURED", "UPSTREAM_ERROR", "INTERNAL_ERROR"):
            return "ERROR_BIZ", f"{code}:{text[:30]}"
        return "ERROR_BIZ", text[:40]
    if not isinstance(data, dict):
        return "FAIL", "成功响应 data 不是对象"
    missing = [k for k in ("source", "caliber", "as_of", "market_status", "is_realtime") if k not in data]
    if missing:
        return "FAIL", f"缺五字段：{','.join(missing)}"
    items = data.get("items")
    if isinstance(items, list):
        return ("DATA" if items else "EMPTY_OK"), f"items={len(items)}"
    if data.get("available") is False:
        return "DEGRADED", str(data.get("caliber") or "")[:40]
    return "DATA", "ok"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:6903")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    with urllib.request.urlopen(f"{args.base}/openapi.json", timeout=30) as resp:
        spec = json.loads(resp.read().decode("utf-8"))

    results: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    for path, methods in sorted(spec.get("paths", {}).items()):
        if path in SKIP_PATHS or not path.startswith("/api/"):
            continue
        for method in ("get", "post", "patch", "delete"):
            if method not in methods:
                continue
            if method in ("patch", "delete"):
                # 第十轮验收补盲：PATCH/DELETE 不在自动探测范围（写/删会破坏数据），
                # 但必须**显式登记**，避免「110 端点全覆盖」的错觉。
                results.append({"path": path, "method": method.upper(), "category": "SKIPPED",
                                "detail": "写/删方法：探针不执行（避免破坏用户数据），已登记为未探测", "elapsed": 0.0})
                continue
            baseline = BASELINE.get(path) if method == "get" else READONLY_POST.get(path)
            if baseline is None:
                results.append({"path": path, "method": method.upper(), "category": "SKIPPED",
                                "detail": "无基线参数（写操作或需业务上下文）", "elapsed": 0.0})
                continue
            status, elapsed, body = _call(args.base, method.upper(), path, baseline)
            if status != 200:
                category, detail = "FAIL", f"HTTP {status}"
            else:
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    category, detail = "FAIL", "响应非 JSON"
                else:
                    category, detail = _classify(payload)
            if elapsed > 15:
                detail = f"{detail}（SLOW {elapsed:.1f}s）"
            counts[category] = counts.get(category, 0) + 1
            results.append({"path": path, "method": method.upper(), "category": category,
                            "detail": detail, "elapsed": round(elapsed, 3)})

    report = {
        "base": args.base,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(results),
        "counts": counts,
        "results": results,
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"total": len(results), "counts": counts}, ensure_ascii=False))
    for item in results:
        if item["category"] in ("FAIL", "ERROR_BIZ"):
            print(f"  !! {item['method']} {item['path']} -> {item['category']} {item['detail']}")
    return 0 if counts.get("FAIL", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
