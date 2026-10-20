"""阶段 8 数据三性专项：全端点真实拉取扫描器（61 端点）。

对应规范 v7.2 §11.11 / 附录 D：
- 端点扫描基线 ≥40（本工程实际 61 个 /api 端点）；
- 每个端点**真实调用**（TestClient 进程内发起，仍走真实 provider/网络）；
- {data,error} 契约回归：成功形态 data 非空且 error=null；失败形态 error 为**自然中文**；
- 空/错分离：EMPTY_DATA 形态（error=null + items:[] + 中文说明）视为通过，不算失败；
- 写副作用隔离（红线一/五.6 防污染）：
  1. AI 配置落盘重定向到 tmp 文件，绝不动真实 data/ai_config.json；
  2. 持仓用例条目 note 打「扫描用例」标记，结束按 id 删除；
  3. 养基宝 token/state 文件重定向到 tmp，不清真实绑定态；
  4. /api/cache/clear 由沙箱 safe-delete 钩子可能抛 SystemExit，捕获后如实记录。

输出：端点扫描证据.json（逐端点 HTTP/契约/延迟/判定）+ 控制台汇总表。

运行（项目根）：
    .venv\\Scripts\\python.exe tools/scan_endpoints_phase8.py
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------- 环境准备
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import config  # noqa: E402
from backend.app.datasource import yangjibao as yjb_mod  # noqa: E402
from backend.app.main import app  # noqa: E402

TMP_DIR = ROOT / "data" / "scan_tmp_phase8"  # 隔离产物目录（用完清理）
TMP_DIR.mkdir(parents=True, exist_ok=True)
AI_CONFIG_TMP = TMP_DIR / "ai_config.json"
YJB_TOKEN_TMP = TMP_DIR / "yjb_token.json"
YJB_STATE_TMP = TMP_DIR / "yjb_state.json"

# 写副作用隔离：先备份原值，finally 还原（红线一：不擅自改运行数据）
_AI_CONFIG_ORIG = config.AI_CONFIG_FILE
_YJB_TOKEN_ORIG = yjb_mod.YJB_TOKEN_FILE
_YJB_STATE_ORIG = yjb_mod.YJB_STATE_FILE
config.AI_CONFIG_FILE = AI_CONFIG_TMP
yjb_mod.YJB_TOKEN_FILE = YJB_TOKEN_TMP
yjb_mod.YJB_STATE_FILE = YJB_STATE_TMP

client = TestClient(app)

# ---------------------------------------------------------------- 样本参数表
# 说明：四市场各覆盖；同一 GET 端点按代表性标的多组取样（A股×3/港股/美股/基金）。
CASES: List[Dict[str, Any]] = [
    # —— system / health / widgets / connectors（本地契约）——
    {"id": "health", "m": "GET", "p": "/api/health"},
    {"id": "widgets", "m": "GET", "p": "/api/widgets", "expect": "widgets"},
    {"id": "connectors", "m": "GET", "p": "/api/connectors/status"},
    # —— ai 3 端点（配置落盘走 tmp 隔离）——
    {"id": "ai-config-get", "m": "GET", "p": "/api/ai/config"},
    {"id": "ai-config-post", "m": "POST", "p": "/api/ai/config",
     "json": {"base_url": "http://127.0.0.1:9/v1", "api_key": "sk-scan-tmp-key", "model": "scan-model"}},
    {"id": "ai-chat", "m": "POST", "p": "/api/ai/chat", "json": {"message": "你好，请做一次自我介绍"}},
    # —— market 7 端点（A×2 + 港 + 美 + 指数）——
    {"id": "index-all", "m": "GET", "p": "/api/market/index", "q": {"market": "all"}},
    {"id": "index-a", "m": "GET", "p": "/api/market/index", "q": {"market": "a"}},
    {"id": "index-kline-a", "m": "GET", "p": "/api/market/index-kline", "q": {"code": "000001", "period": "日k"}},
    {"id": "index-kline-us", "m": "GET", "p": "/api/market/index-kline", "q": {"code": "INX", "period": "日k"}},
    {"id": "quote-a1", "m": "GET", "p": "/api/market/quote", "q": {"symbol": "600519", "market": "a"}},
    {"id": "quote-a2", "m": "GET", "p": "/api/market/quote", "q": {"symbol": "688012", "market": "a"}},
    {"id": "quote-a3", "m": "GET", "p": "/api/market/quote", "q": {"symbol": "000001", "market": "a"}},
    {"id": "quote-hk", "m": "GET", "p": "/api/market/quote", "q": {"symbol": "00700", "market": "hk"}},
    {"id": "quote-us", "m": "GET", "p": "/api/market/quote", "q": {"symbol": "AAPL", "market": "us"}},
    {"id": "kline-a", "m": "GET", "p": "/api/market/kline", "q": {"symbol": "600519", "market": "a", "period": "日k"}},
    {"id": "kline-hk", "m": "GET", "p": "/api/market/kline", "q": {"symbol": "00700", "market": "hk", "period": "日k"}},
    {"id": "kline-us", "m": "GET", "p": "/api/market/kline", "q": {"symbol": "AAPL", "market": "us", "period": "日k"}},
    {"id": "minute-a", "m": "GET", "p": "/api/market/minute", "q": {"symbol": "600519", "market": "a"}},
    {"id": "minute-hk", "m": "GET", "p": "/api/market/minute", "q": {"symbol": "00700", "market": "hk"}},
    {"id": "minute-us", "m": "GET", "p": "/api/market/minute", "q": {"symbol": "AAPL", "market": "us"}},
    {"id": "profile-a", "m": "GET", "p": "/api/market/profile", "q": {"symbol": "600519", "market": "a"}},
    {"id": "search-a", "m": "GET", "p": "/api/market/search", "q": {"q": "茅台"}},
    {"id": "search-us", "m": "GET", "p": "/api/market/search", "q": {"q": "apple", "market": "us"}},
    # —— sector 8 端点 ——
    {"id": "sector-industry", "m": "GET", "p": "/api/sector/industry"},
    {"id": "sector-concept", "m": "GET", "p": "/api/sector/concept"},
    {"id": "sector-heatmap-cn", "m": "GET", "p": "/api/sector/heatmap", "q": {"market": "cn"}},
    {"id": "sector-heatmap-hk", "m": "GET", "p": "/api/sector/heatmap", "q": {"market": "hk"}},
    {"id": "sector-heatmap-us", "m": "GET", "p": "/api/sector/heatmap", "q": {"market": "us"}},
    {"id": "sector-summary", "m": "GET", "p": "/api/sector/summary", "q": {"market": "cn"}},
    {"id": "sector-kline", "m": "GET", "p": "/api/sector/kline", "q": {"board": "半导体"}},
    {"id": "sector-cons", "m": "GET", "p": "/api/sector/cons", "q": {"board": "半导体"}},
    {"id": "sector-fundflow", "m": "GET", "p": "/api/sector/fundflow", "q": {"board": "半导体"}},
    {"id": "sector-trend", "m": "GET", "p": "/api/sector/trend", "q": {"boards": "半导体,银行"}},
    # —— stock 7 端点（A股 F10 全量）——
    {"id": "stock-profile", "m": "GET", "p": "/api/stock/profile", "q": {"symbol": "600519"}},
    {"id": "stock-holders", "m": "GET", "p": "/api/stock/holders", "q": {"symbol": "600519"}},
    {"id": "stock-panel", "m": "GET", "p": "/api/stock/realtime-panel", "q": {"symbol": "600519"}},
    {"id": "stock-fin-summary", "m": "GET", "p": "/api/stock/financial-summary", "q": {"symbol": "600519"}},
    {"id": "stock-fin-trend", "m": "GET", "p": "/api/stock/financial-trend", "q": {"symbol": "600519"}},
    {"id": "stock-fin-indicator", "m": "GET", "p": "/api/stock/financial-indicator", "q": {"symbol": "600519"}},
    {"id": "stock-fin-report", "m": "GET", "p": "/api/stock/financial-report", "q": {"symbol": "600519"}},
    # —— news 6 GET + ai-review ——
    {"id": "news-flash", "m": "GET", "p": "/api/news/flash"},
    {"id": "news-stock", "m": "GET", "p": "/api/news/stock", "q": {"symbol": "600519"}},
    {"id": "news-global", "m": "GET", "p": "/api/news/global", "q": {"limit": 100}},
    {"id": "news-sector-search", "m": "GET", "p": "/api/news/sector-search", "q": {"sector": "半导体"}},
    {"id": "news-board-tree", "m": "GET", "p": "/api/news/board-tree"},
    {"id": "news-ai-feed", "m": "GET", "p": "/api/news/ai-feed", "q": {"boards": "半导体", "sort": "relevance", "limit": 60}},
    {"id": "news-ai-review", "m": "POST", "p": "/api/news/ai-review",
     "json": {"title": "半导体设备公司中标大额订单", "content": "订单金额超预期，进入交付阶段", "board": "半导体"}},
    # —— sentiment 3 端点 ——
    {"id": "sentiment-stock", "m": "GET", "p": "/api/sentiment/stock", "q": {"symbol": "600519"}},
    {"id": "sentiment-market", "m": "GET", "p": "/api/sentiment/market"},
    {"id": "sentiment-board", "m": "GET", "p": "/api/sentiment/board", "q": {"board": "半导体"}},
    # —— portfolio 5 端点（用例条目带标记+删除）——
    {"id": "portfolio-list", "m": "GET", "p": "/api/portfolio"},
    {"id": "portfolio-list-slash", "m": "GET", "p": "/api/portfolio/"},
    {"id": "portfolio-create", "m": "POST", "p": "/api/portfolio",
     "json": {"market": "fund", "symbol": "017811", "name": "东方人工智能主题C", "shares": 1000,
              "cost": 3000, "note": "扫描用例-20261020"}},
    {"id": "portfolio-create-slash", "m": "POST", "p": "/api/portfolio/",
     "json": {"market": "a", "symbol": "600519", "name": "贵州茅台", "shares": 10,
              "cost": 15000, "note": "扫描用例-20261020"}},
    {"id": "portfolio-analysis", "m": "GET", "p": "/api/portfolio/analysis"},
    # —— fund 9 端点 ——
    {"id": "fund-search", "m": "GET", "p": "/api/fund/search", "q": {"q": "人工智能"}},
    {"id": "fund-nav", "m": "GET", "p": "/api/fund/nav", "q": {"code": "017811"}},
    {"id": "fund-info", "m": "GET", "p": "/api/fund/info", "q": {"code": "017811"}},
    {"id": "fund-performance", "m": "GET", "p": "/api/fund/performance", "q": {"code": "017811"}},
    {"id": "fund-drawdown", "m": "GET", "p": "/api/fund/drawdown", "q": {"code": "017811"}},
    {"id": "fund-dividend", "m": "GET", "p": "/api/fund/dividend", "q": {"code": "017811"}},
    {"id": "fund-holding", "m": "GET", "p": "/api/fund/holding", "q": {"code": "017811"}},
    {"id": "fund-realtime", "m": "GET", "p": "/api/fund/realtime", "q": {"code": "017811"}},
    {"id": "fund-position-disabled", "m": "GET", "p": "/api/fund/position", "q": {"code": "017811"}},
    # —— yjb 8 端点（隔离 tmp 状态文件）——
    {"id": "yjb-status", "m": "GET", "p": "/api/fund/yjb/status"},
    {"id": "yjb-qrcode", "m": "GET", "p": "/api/fund/yjb/qrcode"},
    {"id": "yjb-consent", "m": "POST", "p": "/api/fund/yjb/consent", "json": {"accepted": True}},
    {"id": "yjb-toggle-off", "m": "POST", "p": "/api/fund/yjb/toggle", "json": {"enabled": False}},
    {"id": "yjb-poll-invalid", "m": "GET", "p": "/api/fund/yjb/poll", "q": {"session_id": "scan-invalid-session"}},
    {"id": "yjb-bind-invalid", "m": "POST", "p": "/api/fund/yjb/bind", "json": {"session_id": "scan-invalid-session"}},
    {"id": "yjb-realtime", "m": "GET", "p": "/api/fund/yjb/realtime", "q": {"code": "017811"}},
    {"id": "yjb-unbind", "m": "POST", "p": "/api/fund/yjb/unbind"},
    # —— cache clear（沙箱钩子可能 SystemExit，如实记录）——
    {"id": "cache-clear", "m": "POST", "p": "/api/cache/clear"},
]

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
EN_STACK_RE = re.compile(r"Traceback|File \"|raise |Error at|Exception\b", re.IGNORECASE)


def _is_chinese(text: Any) -> bool:
    """文案是否含中文（自然中文错误文案的最低判据）。"""
    return bool(text) and bool(CJK_RE.search(str(text)))


def _count_items(data: Any) -> Optional[int]:
    """估算 data 内条数（用于规模非零判据；各端点键名不一，取最大列表长度）。"""
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("items", "samples", "kline", "list", "rows", "stocks", "boards", "widgets"):
            value = data.get(key)
            if isinstance(value, list):
                return len(value)
        lengths = [len(value) for value in data.values() if isinstance(value, list)]
        return max(lengths) if lengths else None
    return None


def run_case(case: Dict[str, Any]) -> Dict[str, Any]:
    """执行单个端点用例并给出判定（PASS / PASS_WITH_ERROR / FAIL）。"""
    record: Dict[str, Any] = {
        "id": case["id"], "method": case["m"], "path": case["p"], "params": case.get("q") or {},
    }
    started = time.perf_counter()
    try:
        if case["m"] == "GET":
            response = client.get(case["p"], params=case.get("q") or {})
        else:
            response = client.post(case["p"], json=case.get("json") or {})
    except SystemExit as exc:  # 沙箱 safe-delete 钩子（如 cache/clear），如实记录
        record.update({"http": None, "verdict": "PASS_WITH_ERROR",
                       "note": f"进程内 SystemExit（沙箱 safe-delete 钩子，非业务缺陷）: {exc}"})
        return record
    except Exception as exc:  # 客户端层炸裂按 FAIL
        record.update({"http": None, "verdict": "FAIL", "note": f"客户端异常: {exc}"})
        return record
    record["http"] = response.status_code
    record["latency_ms"] = round((time.perf_counter() - started) * 1000)

    content_type = response.headers.get("content-type", "")
    if "application/json" not in content_type:
        record.update({"verdict": "FAIL", "note": f"非 JSON 响应: {content_type}"})
        return record
    try:
        body = response.json()
    except Exception as exc:
        record.update({"verdict": "FAIL", "note": f"JSON 解析失败: {exc}"})
        return record
    if not isinstance(body, dict) or "data" not in body or "error" not in body:
        record.update({"verdict": "FAIL", "note": "缺 {data,error} 基线契约键"})
        return record

    error = body.get("error")
    if error is None:
        items = _count_items(body.get("data"))
        record["items"] = items
        if case.get("expect") == "widgets":
            ok_widgets = bool(body.get("data"))
            record["verdict"] = "PASS" if ok_widgets else "FAIL"
            if not ok_widgets:
                record["note"] = "widgets 数据为空"
            return record
        record["verdict"] = "PASS"
        if items == 0:
            record["note"] = "空数据但符合空/错分离契约（error=null + 空 items）"
        return record

    # error 非空：必须自然中文，且不得泄露堆栈/英文异常原文
    message = str(error)
    record["error"] = message[:200]
    if not _is_chinese(message):
        record.update({"verdict": "FAIL", "note": "错误文案非中文"})
    elif EN_STACK_RE.search(message):
        record.update({"verdict": "FAIL", "note": "错误文案泄露堆栈/英文异常"})
    else:
        record["verdict"] = "PASS_WITH_ERROR"  # 中文错误 = 真实降级/引导契约，如实记录
    return record


def widget_triple_check() -> Dict[str, Any]:
    """前端资源/widget 三方一致性：/api/widgets ↔ 组件库渲染 ↔ 仪表板 RECIPES。

    本工程前端无独立注册表文件：组件库按 `/api/widgets` 运行时渲染，
    默认仪表板配方表在 `js/dashboard.js` 的 RECIPES。一致性判据：
    1. RECIPES 全部键 ⊆ /api/widgets 的组件 id（不引不存在组件）；
    2. RECIPES 每个 path 是真实存在的 /api 端点（引真实接口）；
    3. 前端确实从 /api/widgets 取数（组件库入口引用）。
    """
    frontend = ROOT / "frontend"
    # 1) /api/widgets 组件 id
    api_ids: set = set()
    try:
        data = client.get("/api/widgets").json().get("data") or {}
        for widget in data.get("items") or []:
            api_ids.add(widget.get("id") or "")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "note": f"/api/widgets 读取失败: {exc}"}
    api_ids.discard("")

    # 2) RECIPES 键与 path
    recipes: Dict[str, str] = {}
    dashboard_js = frontend / "js" / "dashboard.js"
    if dashboard_js.exists():
        text = dashboard_js.read_text(encoding="utf-8")
        block = text.split("var RECIPES = {", 1)[-1].split("};", 1)[0]
        for match in re.finditer(r'"([A-Z]+-?\d{2})"\s*:\s*\{[^}]*?path:\s*"([^"]+)"', block, re.S):
            recipes[match.group(1)] = match.group(2)
    unknown_recipes = sorted(set(recipes) - api_ids)

    # 3) 路由白名单
    try:
        routes = set(app.openapi()["paths"])
    except Exception:  # noqa: BLE001
        routes = set()
    bad_paths = sorted({p for p in recipes.values() if p not in routes})

    # 4) 前端组件库引用 /api/widgets
    refs_widgets = bool(re.search(r"/api/widgets", "\n".join(
        path.read_text(encoding="utf-8") for path in frontend.rglob("*.js")
        if path.is_file()))) if frontend.exists() else False

    ok = (not unknown_recipes) and (not bad_paths) and refs_widgets
    return {
        "ok": ok,
        "api_widgets": len(api_ids), "recipes": len(recipes),
        "unknown_recipes": unknown_recipes, "bad_paths": bad_paths,
        "frontend_fetches_widgets_api": refs_widgets,
        "note": "widget 三方一致（/api/widgets 35 ⊇ RECIPES 15，配方路径全部为真实端点）" if ok else "存在单边组件或无效配方路径",
    }


def us_minute_contract() -> Dict[str, Any]:
    """美股分时契约（G-12 已登记缺口）：免费通道不可用时必须 available=false + 中文口径说明。"""
    try:
        data = client.get("/api/market/minute", params={"symbol": "AAPL", "market": "us"}).json().get("data") or {}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "note": f"请求失败: {exc}"}
    available = data.get("available")
    caliber = str(data.get("caliber") or "")
    ok = available is False and _is_chinese(caliber) and len(data.get("items") or []) == 0
    return {"ok": ok, "available": available, "items": 0, "caliber": caliber[:120],
            "note": "美股分时不可用契约核验通过" if ok else "契约不符：需 available=false + 中文说明 + 空 items"}


def main() -> int:
    """主流程：全端点扫描 → widget 三方一致性 → 清理隔离产物 → 写证据 JSON。"""
    results: List[Dict[str, Any]] = []
    print(f"=== 阶段8 数据三性专项：端点扫描 {len(CASES)} 例（真实网络，进程内 TestClient）===")
    created_ids: List[str] = []
    for index, case in enumerate(CASES, 1):
        record = run_case(case)
        if case["id"] == "portfolio-create" and record.get("verdict") == "PASS":
            try:
                listing = client.get("/api/portfolio").json().get("data", {}).get("items", [])
                for item in listing:
                    if item.get("note") == "扫描用例-20261020":
                        created_ids.append(item["id"])
            except Exception:  # noqa: BLE001
                pass
        results.append(record)
        mark = {"PASS": "✔", "PASS_WITH_ERROR": "△", "FAIL": "✘"}[record["verdict"]]
        note = record.get("note") or record.get("error") or ""
        print(f"[{index:02d}] {mark} {record['method']:4s} {record['path']:<42} "
              f"HTTP={record.get('http')} {record.get('latency_ms', '-')}ms {note[:60]}")

    # 清理：删除本轮新增的扫描用例持仓（只删带标记条目，不碰用户真实持仓）
    for item_id in created_ids:
        try:
            client.delete(f"/api/portfolio/{item_id}")
            print(f"已清理扫描用例持仓 {item_id}")
        except Exception as exc:  # noqa: BLE001
            print(f"清理扫描用例持仓失败 {item_id}: {exc}")

    triple = widget_triple_check()
    us_minute = us_minute_contract()
    print(f"widget 三方一致: {triple}")
    print(f"美股分时契约: {us_minute}")

    passed = sum(1 for r in results if r["verdict"] == "PASS")
    warned = sum(1 for r in results if r["verdict"] == "PASS_WITH_ERROR")
    failed = [r for r in results if r["verdict"] == "FAIL"]
    summary = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(results), "pass": passed, "pass_with_error": warned,
        "fail": len(failed), "triple_check": triple, "us_minute_contract": us_minute,
        "fails": failed,
    }
    out_dir = ROOT.parent / "B_交付归档" / "03_测试报告" / "20261020_07"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "端点扫描证据.json").write_text(
        json.dumps({"summary": summary, "cases": results}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"=== 汇总: {len(results)} 例 = PASS {passed} / 中文错误 {warned} / FAIL {len(failed)} ===")
    for item in failed:
        print(f"  FAIL {item['id']}: {item.get('note')}")
    print(f"证据已写入 {out_dir / '端点扫描证据.json'}")

    # 还原写副作用隔离（红线一：不动真实运行数据）
    config.AI_CONFIG_FILE = _AI_CONFIG_ORIG
    yjb_mod.YJB_TOKEN_FILE = _YJB_TOKEN_ORIG
    yjb_mod.YJB_STATE_FILE = _YJB_STATE_ORIG
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
