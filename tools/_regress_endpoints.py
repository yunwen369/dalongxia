"""v3.7.2 端点层回归：对**运行中的实例**做真实 HTTP 断言（含冷启动计时）。

与 ``tools/_regress_v372.py``（进程内契约）互补：本脚本验证「用户实际看到的响应」。
默认打正式环境 6900（跑的是打包产物），也可指向源码实例。

覆盖：
    E1  health.build 与版本一致
    E2  单板块拥挤度 → 精确口径（同花顺成分流通市值合计）
    E3  清缓存后冷启动耗时（B6/B7 的真实读数）+ 口径不退化（B2/B3）
    E4  排行 limit=10/50/120 → 条数正确、scanned/total 一致（一份缓存服务所有长度）
    E5  缓存键去 limit 的磁盘证据（md5 命中/未命中）
    E6  其它板块端点与降级链（industry/fundflow/kline/index-kline/quote）

用法::

    ./.venv/Scripts/python.exe tools/_regress_endpoints.py \
        --base http://127.0.0.1:6900 --cache-dir "../正式环境/data/cache"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RESULTS = []


def _unwrap(payload):
    """取出信封里的 data（失败返回 None 并保留 error 文本）。"""
    if not isinstance(payload, dict):
        return None
    if payload.get("error"):
        return None
    return payload.get("data")


def check(name, fn, *, section=""):
    """执行一条 HTTP 断言并记录。

    Args:
        name: 断言名。
        fn: 无参可调用，返回 (bool, str) 或 bool。
        section: 分组（对应回归项编号）。
    """
    started = time.time()
    try:
        out = fn()
        passed, detail = out if isinstance(out, tuple) else (bool(out), "")
    except Exception as exc:  # noqa: BLE001 - 探针收集一切异常
        passed, detail = False, f"{type(exc).__name__}: {exc}"
    cost = time.time() - started
    RESULTS.append({"section": section, "name": name, "passed": bool(passed),
                    "detail": str(detail)[:600], "cost_s": round(cost, 3)})
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return passed


def main() -> int:
    """跑端点层全部断言。

    Returns:
        int: 0 = 全过；1 = 有失败。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:6900")
    parser.add_argument("--cache-dir", default=str(ROOT / "data" / "cache"))
    parser.add_argument("--password", default=os.environ.get("OPENBB_ACCESS_PASSWORD", ""))
    parser.add_argument("--cold", action="store_true", help="执行清缓存后的冷启动计时")
    parser.add_argument("--boards", default="半导体,银行",
                        help="冷启动计时用的板块（逗号分隔）")
    args = parser.parse_args()

    base = args.base.rstrip("/")
    cache_dir = Path(args.cache_dir)
    if not cache_dir.is_absolute():
        cache_dir = (ROOT / cache_dir).resolve()
    session = requests.Session()
    session.trust_env = False
    session.headers["User-Agent"] = "OpenBB-Regress/1.0"

    def get(path, **params):
        """带计时地发一次 GET。"""
        started = time.time()
        response = session.get(f"{base}{path}", params=params, timeout=300)
        cost = time.time() - started
        try:
            payload = response.json()
        except ValueError:
            payload = {"error": f"非 JSON（HTTP {response.status_code}）"}
        return payload, cost, response.status_code

    print("=" * 78)
    print(f"v3.7.2 端点层回归 —— {base}")
    print("=" * 78)

    if args.password:
        auth = session.post(f"{base}/auth", data={"token": args.password}, timeout=30)
        print(f"鉴权：HTTP {auth.status_code}")
    else:
        print("鉴权：未提供口令（实例若开了口令将全部 401）")

    # ------------------------------------------------------------------ E1
    print("\n[E1] 版本与健康")
    health, cost, status = get("/api/health")
    build = ((_unwrap(health) or {}).get("build") or "")

    check("health 可达且 build=v3.7.2-20261115",
          lambda: (build == "v3.7.2-20261115" and status == 200,
                   f"HTTP {status} build={build} {cost * 1000:.0f}ms"), section="E1")

    # ------------------------------------------------------------------ E2
    print("\n[E2] 单板块拥挤度口径（修复 B2）")
    boards = [b.strip() for b in args.boards.split(",") if b.strip()]
    # 预热：服务刚重启时首调是「半冷」（外层 1h 缓存键不存在，需完整算六维），
    # 热态断言必须测「缓存命中后的重复访问」，先各打一遍不计入计时。
    for board in boards:
        get("/api/sector/crowding", board=board, kind="industry")
    warm = {}
    for board in boards:
        payload, cost, status = get("/api/sector/crowding", board=board, kind="industry")
        data = _unwrap(payload) or {}
        # 口径藏在「换手率分位」part 的 note 里（v3.7.2 无独立 float_cap 键），
        # 精确口径串 =「换手率=日成交额/成分市值（同花顺成分流通市值合计，…」。
        caliber = None
        for part in data.get("parts") or []:
            if isinstance(part, dict) and "换手率分位" in str(part.get("label") or ""):
                note = str(part.get("note") or "")
                if "同花顺成分流通市值合计" in note:
                    caliber = "同花顺成分流通市值合计"
                elif "近似" in note or "本地全市场快照" in note:
                    caliber = "近似口径（本地快照总市值）"
                break
        warm[board] = {"cost": cost, "caliber": caliber, "score": data.get("score")}
        check(f"{board} 拥挤度口径为「同花顺成分流通市值合计」",
              lambda c=caliber, b=board: (c == "同花顺成分流通市值合计",
                                          f"caliber={c}"), section="E2")
        check(f"{board} 热态响应 <2s",
              lambda t=cost, b=board: (t < 2.0, f"{t * 1000:.0f}ms"), section="E2")

    # ------------------------------------------------------------------ E4
    print("\n[E4] 排行：一次扫描服务所有 limit（修复 B7 / 功能 F6）")
    rank = {}
    for limit in (10, 50, 120):
        payload, cost, status = get("/api/sector/crowding/rankings", kind="industry", limit=limit)
        data = _unwrap(payload) or {}
        rank[limit] = {"n": len(data.get("items") or []), "cost": cost,
                       "scanned": data.get("scanned"), "total": data.get("total")}
        # 行业总数只有 90（< 120）：切片语义是「最多 limit 条」，实得数 = min(limit, total)
        expected = min(limit, rank[limit]["total"] or 0)
        check(f"limit={limit} → 返回 min(limit, total)={expected} 条",
              lambda d=data, e=expected: (len(d.get("items") or []) == e,
                                          f"实得 {len(d.get('items') or [])} 条 / {cost * 1000:.0f}ms"),
              section="E4")

    def _same_scan():
        scanned = {v["scanned"] for v in rank.values()}
        total = {v["total"] for v in rank.values()}
        return (len(scanned) == 1 and len(total) == 1,
                f"scanned={sorted(s for s in scanned if s is not None)} "
                f"total={sorted(t for t in total if t is not None)}（三个 limit 必须同源）")

    check("三个 limit 的 scanned/total 完全一致（同一份缓存）", _same_scan, section="E4")

    def _rank_order():
        """切片不得改变排序：limit=10 的前 10 名应与 limit=120 的前 10 名逐一相同。"""
        small = _unwrap(get("/api/sector/crowding/rankings",
                            kind="industry", limit=10)[0]) or {}
        big = _unwrap(get("/api/sector/crowding/rankings",
                          kind="industry", limit=120)[0]) or {}
        a = [i.get("board") or i.get("name") for i in small.get("items") or []]
        b = [i.get("board") or i.get("name") for i in big.get("items") or []][:10]
        return a == b, f"前 10 名一致={a == b}"

    check("切片不破坏排序（两手 limit 的前 10 名逐一相同）", _rank_order, section="E4")

    # ------------------------------------------------------------------ E5
    print("\n[E5] 缓存键去 limit 的磁盘证据")
    if cache_dir.is_dir():
        def _digest(key):
            return hashlib.md5(key.encode("utf-8")).hexdigest()

        new_key = cache_dir / f"{_digest('sector:crowding:rankings:industry')}.json"
        old50 = cache_dir / f"{_digest('sector:crowding:rankings:industry:50')}.json"
        old120 = cache_dir / f"{_digest('sector:crowding:rankings:industry:120')}.json"
        check("缓存目录存在新键（不带 limit），且无任何带 limit 旧键",
              lambda: (new_key.is_file() and not old50.is_file() and not old120.is_file(),
                       f"新键={new_key.is_file()} 旧:50={old50.is_file()} 旧:120={old120.is_file()}"),
              section="E5")
    else:
        check("缓存目录可读", lambda: (False, f"目录不存在 {cache_dir}"), section="E5")

    # ------------------------------------------------------------------ E3
    if args.cold:
        print("\n[E3] 清缓存后冷启动计时（修复 B6/B7 / 功能 F1-F5）")
        cleared, cost, status = get("/api/cache/clear")
        # /api/cache/clear 可能是 POST
        if status >= 400 or cleared.get("error"):
            response = session.post(f"{base}/api/cache/clear", timeout=60)
            cleared = response.json() if response.content else {}
            status = response.status_code
        print(f"  清缓存：HTTP {status} {json.dumps(cleared, ensure_ascii=False)[:120]}")

        cold = {}
        for index, board in enumerate(boards):
            payload, cost, status = get("/api/sector/crowding", board=board, kind="industry")
            data = _unwrap(payload) or {}
            caliber = None
            for part in ((data.get("parts") or []) if isinstance(data.get("parts"), list) else []):
                if isinstance(part, dict) and "换手率分位" in str(part.get("label") or ""):
                    note = str(part.get("note") or "")
                    if "同花顺成分流通市值合计" in note:
                        caliber = "同花顺成分流通市值合计"
                    elif "近似" in note or "本地全市场快照" in note:
                        caliber = "近似口径（本地快照总市值）"
                    break
            cold[board] = cost
            check(f"冷启动 #{index + 1} {board} 口径未退化",
                  lambda c=caliber: (c == "同花顺成分流通市值合计", f"caliber={c}"),
                  section="E3")
            check(f"冷启动 #{index + 1} {board} <12s（东财被封时的降级链口径）",
                  lambda t=cost: (t < 12.0, f"{t:.2f}s"), section="E3")

        def _cold_pair():
            if len(cold) < 2:
                return True, "单板块，跳过首末对比"
            first, second = list(cold.values())[:2]
            return (second <= first * 1.15,
                    f"首个 {first:.2f}s / 次个 {second:.2f}s（同板块不同名应无额外一次性成本）")

        check("第二个板块不再承担一次性初始化成本", _cold_pair, section="E3")

    # ------------------------------------------------------------------ E6
    print("\n[E6] 其余板块端点与降级链")
    payload, cost, status = get("/api/sector/industry", market="a")
    items = (_unwrap(payload) or {}).get("items") or []
    check("行业列表 items>0", lambda: (len(items) > 0, f"{len(items)} 条 / {cost * 1000:.0f}ms"),
          section="E6")

    payload, cost, status = get("/api/sector/fundflow", market="a")
    items = (_unwrap(payload) or {}).get("items") or []
    check("行业资金流 items>0", lambda: (len(items) > 0, f"{len(items)} 条 / {cost * 1000:.0f}ms"),
          section="E6")

    payload, cost, status = get("/api/sector/kline", board=boards[0], period="daily")
    items = (_unwrap(payload) or {}).get("items") or []
    check("板块 K 线 items>0", lambda: (len(items) > 0, f"{len(items)} 条 / {cost * 1000:.0f}ms"),
          section="E6")

    payload, cost, status = get("/api/market/index-kline", code="000001")
    items = (_unwrap(payload) or {}).get("items") or []
    check("指数 K 线 items>0（V8 通道，修复 B1 的运行侧佐证）",
          lambda: (len(items) > 0, f"{len(items)} 条 / {cost * 1000:.0f}ms"), section="E6")

    payload, cost, status = get("/api/market/quote", symbol="600519")
    data = _unwrap(payload) or {}
    # 实际结构：data.quote.price（兼容旧双层 data.quote.quote.price 形态）
    q = data.get("quote") or {}
    if isinstance(q, dict) and "price" not in q and isinstance(q.get("quote"), dict):
        q = q["quote"]
    price = (q or {}).get("price") if isinstance(q, dict) else None
    check("个股报价 price>0（降级链：东财被封时落腾讯）",
          lambda: (isinstance(price, (int, float)) and price > 0,
                   f"price={price} / {cost * 1000:.0f}ms"), section="E6")

    payload, cost, status = get("/api/market/kline", symbol="600519")
    items = (_unwrap(payload) or {}).get("items") or []
    check("个股 K 线 items>0（含新浪 V8 降级链）",
          lambda: (len(items) > 0, f"{len(items)} 条 / {cost * 1000:.0f}ms"), section="E6")

    # ------------------------------------------------------------------ 汇总
    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["passed"])
    failed = [r for r in RESULTS if not r["passed"]]
    print("\n" + "=" * 78)
    print(f"合计 {passed}/{total} 通过" + (f"，失败 {len(failed)} 项" if failed else "，全部通过"))
    for item in failed:
        print(f"  FAIL [{item['section']}] {item['name']} — {item['detail']}")
    print("=" * 78)

    out_dir = ROOT / "tools" / "_out"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "regress_endpoints.json").write_text(
        json.dumps({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "base": base,
                    "total": total, "passed": passed, "failed": len(failed),
                    "warm": warm, "cold": cold if args.cold else {},
                    "rankings": {str(k): v for k, v in rank.items()},
                    "items": RESULTS}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"明细已写入 {out_dir / 'regress_endpoints.json'}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
