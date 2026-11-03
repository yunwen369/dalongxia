"""全量 GET 端点的「信封契约基线」采集与比对（门禁 B 固定工具）。

背景：响应信封收敛（第九轮 BUG-R9-01）要求契约零变更。行情/新闻等数据每次请求都会变化，
逐值比对无法作为放行判据，因此本工具比对**结构性契约**：

1. HTTP 状态、error、error_code；
2. data 的五字段取值（source / caliber / market_status / is_realtime）——收敛时
   最易被机械替换丢失的正是这几个固化常量（方案风险 R6）；
3. data 的递归类型骨架（dict 键集合 / list 元素骨架），用于发现字段丢失或结构变化。

数值本身不比对（实时行情自然变化）；空/非空差异记为 DATA_VOLATILITY 供人工判读。

用法（改造前采集 → 改造后重采 → 比对，任一契约差异即退出码 1）：
    python tools/envelope_baseline.py capture --base http://127.0.0.1:6901 --out before.json
    python tools/envelope_baseline.py capture --base http://127.0.0.1:6901 --out after.json
    python tools/envelope_baseline.py compare --before before.json --after after.json
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Tuple

# 必填参数基线（与 tools/probe_endpoints.py 保持一致，仅取 GET 端点）
PARAMS: Dict[str, Dict[str, Any]] = {
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
    "/api/news/financial-detail": {
        "code": "600519",
        "market": "a",
        "title": "贵州茅台2026年第三季度报告",
    },
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
    "/api/fund/search": {"q": "沪深300"},
    "/api/fund/position": {"code": "017811"},
    "/api/sentiment/stock": {"symbol": "600519"},
    "/api/moneyflow/lhb": {"days": 7},
    "/api/moneyflow/northbound": {},
    "/api/sector/industry": {"market": "a"},
    "/api/sector/concept": {"market": "a"},
    "/api/sector/heatmap": {"market": "a"},
    "/api/sector/summary": {"market": "a"},
    "/api/sector/fundflow": {"market": "a"},
    "/api/news/flash": {"limit": 20},
    "/api/news/global": {"limit": 20},
    "/api/news/ai-feed": {"limit": 10},
    "/api/news/board-tree": {"market": "a"},
    "/api/sentiment/market": {"market": "a"},
    "/api/sentiment/board": {"board": "银行"},
    "/api/portfolio": {},
    "/api/portfolio/analysis": {},
    "/api/portfolio/risk": {},
    "/api/portfolio/attribution": {},
    "/api/watchlist": {},
    "/api/alerts": {},
    "/api/dashboards": {},
    "/api/widgets": {},
    "/api/health": {},
    "/api/macro/overview": {},
    "/api/valuation/percentile": {},
    "/api/screener/status": {},
    "/api/fund/yjb/status": {},
    "/api/fund/yjb/consent": {},
    "/api/fund/combo/evaluate": {},
    "/api/fund/candidates": {},
    "/api/sector/crowding/rankings": {},
    "/api/connectors": {},
    "/api/ai/config": {},
    "/api/system/desktop-prefs": {},
    "/api/logs/export": {},
}

FIVE = ("source", "caliber", "market_status", "is_realtime")


def shape_of(value: Any, depth: int = 0) -> Any:
    """提取数据的递归类型骨架（忽略具体数值）。

    Args:
        value: 任意 JSON 值。
        depth: 当前深度（超过 4 层截断，避免超大结构）。

    Returns:
        Any: 骨架表示；dict 为键→骨架，list 为 ["list", 元素骨架或 None]。
    """
    if depth > 4:
        return "..."
    if isinstance(value, dict):
        return {k: shape_of(v, depth + 1) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return ["list", shape_of(value[0], depth + 1) if value else None]
    if value is None:
        return "null"
    return type(value).__name__


def fetch(base: str, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """请求单个端点并提取契约指纹。

    Args:
        base: 服务基址。
        path: 端点路径。
        params: 查询参数。

    Returns:
        dict: 契约指纹；异常时含 error 字段说明。
    """
    url = base.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=90) as resp:
            status = resp.status
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        return {"http": exc.code, "error": f"HTTPError {exc.code}", "error_code": None}
    except Exception as exc:  # noqa: BLE001 - 采集工具需记录任意失败，不阻断整轮
        return {"http": None, "error": f"{type(exc).__name__}: {exc}", "error_code": None}

    if not path.startswith("/api") or path == "/api/logs/export":
        return {"http": status, "binary": len(raw)}
    try:
        body = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - 非 JSON 响应需如实记录
        return {"http": status, "error": f"JSONDecodeError: {exc}", "error_code": None}

    data = body.get("data")
    five = {}
    if isinstance(data, dict):
        for key in FIVE:
            if key in data:
                five[key] = data[key]
    return {
        "http": status,
        "error": body.get("error"),
        "error_code": body.get("error_code"),
        "five": five,
        "data_keys": sorted(data.keys()) if isinstance(data, dict) else None,
        "shape": shape_of(data),
    }


def route_list(base: str) -> List[str]:
    """从运行实例的 OpenAPI 取全部 GET 路径。

    Args:
        base: 服务基址。

    Returns:
        List[str]: 排序后的路径列表。
    """
    with urllib.request.urlopen(base.rstrip("/") + "/openapi.json", timeout=30) as resp:
        spec = json.loads(resp.read().decode("utf-8"))
    paths = []
    for path, methods in spec.get("paths", {}).items():
        if "get" in methods:
            paths.append(path)
    return sorted(paths)


def capture(base: str, out: str) -> int:
    """采集全量 GET 端点基线。

    Args:
        base: 服务基址。
        out: 输出 JSON 路径。

    Returns:
        int: 退出码。
    """
    result: Dict[str, Any] = {}
    for path in route_list(base):
        params = PARAMS.get(path, {})
        result[path] = fetch(base, path, params)
        mark = result[path].get("error_code") or ("BIN" if "binary" in result[path] else "OK")
        print(f"  {path:<45} {mark}")
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=1)
    print(f"\n采集完成：{len(result)} 条 → {out}")
    return 0


def compare(before_path: str, after_path: str) -> int:
    """比对两次基线。

    Args:
        before_path: 改造前基线。
        after_path: 改造后基线。

    Returns:
        int: 0=全等（允许 DATA_VOLATILITY）；1=存在契约差异。
    """
    before = json.load(open(before_path, encoding="utf-8"))
    after = json.load(open(after_path, encoding="utf-8"))
    diffs: List[Tuple[str, str]] = []
    volatility: List[str] = []
    for path in sorted(set(before) | set(after)):
        b, a = before.get(path), after.get(path)
        if b is None or a is None:
            diffs.append((path, "端点缺失"))
            continue
        for key in ("http", "error", "error_code", "data_keys"):
            if b.get(key) != a.get(key):
                diffs.append((path, f"{key}: {b.get(key)!r} → {a.get(key)!r}"))
        for key in FIVE:
            if b.get("five", {}).get(key) != a.get("five", {}).get(key):
                diffs.append((path, f"five.{key}: {b.get('five', {}).get(key)!r} → {a.get('five', {}).get(key)!r}"))
        if b.get("shape") != a.get("shape"):
            # 空/非空差异属实时数据波动，单列不阻断
            bs, as_ = json.dumps(b.get("shape"), ensure_ascii=False), json.dumps(a.get("shape"), ensure_ascii=False)
            if ("null" in bs) != ("null" in as_):
                volatility.append(path)
            else:
                diffs.append((path, "shape 变化"))

    print(f"比对端点：{len(set(before) | set(after))} 条")
    print(f"契约差异：{len(diffs)} 条；数据波动（非阻断）：{len(volatility)} 条")
    for path, msg in diffs:
        print(f"  DIFF {path}: {msg}")
    for path in volatility:
        print(f"  VOLA {path}: 空/非空差异")
    return 0 if not diffs else 1


def main() -> int:
    """入口。

    Returns:
        int: 退出码。
    """
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    cap = sub.add_parser("capture")
    cap.add_argument("--base", required=True)
    cap.add_argument("--out", required=True)
    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("--before", required=True)
    cmp_.add_argument("--after", required=True)
    args = parser.parse_args()
    if args.cmd == "capture":
        return capture(args.base, args.out)
    return compare(args.before, args.after)


if __name__ == "__main__":
    raise SystemExit(main())
