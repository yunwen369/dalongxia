# -*- coding: utf-8 -*-
"""只读性能探针：首屏/慢端点冷热 latency 计时（不写任何数据）。

用法: python perf_latency.py --base http://127.0.0.1:6916 --out perf.json --label cold
端点清单 = index.html/app.js 首屏实际调用（GET，只读）。
"""
import argparse, json, time, urllib.request, urllib.error, os

# 首屏 app.js main() + route 实际请求（GET，只读）
FIRST_SCREEN = [
    ("/api/health", "健康/版本"),
    ("/api/market/index?market=a", "指数四卡"),
    ("/api/market/index-kline?symbol=000001&period=daily&limit=60", "指数迷你走势×1"),
    ("/api/market/index-kline?symbol=399001&period=daily&limit=60", "指数迷你走势×2"),
    ("/api/market/index-kline?symbol=399006&period=daily&limit=60", "指数迷你走势×3"),
    ("/api/market/index-kline?symbol=000300&period=daily&limit=60", "指数迷你走势×4"),
    ("/api/sentiment/market", "市场情绪"),
    ("/api/sector/industry", "行业板块"),
    ("/api/sector/crowding/rankings?kind=industry&limit=50", "拥挤度排行"),
    ("/api/connectors/status", "连接器状态"),
    ("/api/widgets", "组件注册表"),
    ("/api/dashboards", "仪表板"),
    ("/api/alerts", "提醒"),
    ("/api/watchlist", "自选"),
]
# 已知慢端点（探针实测 TOP）
SLOW_SPOTS = [
    ("/api/sector/summary", "板块总览"),
    ("/api/market/index-kline?symbol=000001&period=daily&limit=120", "K线(长)"),
    ("/api/fund/search?q=沪深300", "基金搜索"),
    ("/api/stock/realtime-panel?symbol=300750", "个股实时面板"),
    ("/api/news/global?limit=20", "全球资讯"),
]


def timed(base, path, timeout=180):
    req = urllib.request.Request(base + path)
    req.add_header("Accept", "application/json")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            code = resp.status
        return round(time.time() - t0, 3), code, len(body)
    except urllib.error.HTTPError as err:
        return round(time.time() - t0, 3), err.code, 0
    except Exception:  # noqa: BLE001 - 连接失败等一律按 -1 记录，不中断批量计时
        return round(time.time() - t0, 3), -1, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--label", default="run")
    args = ap.parse_args()

    rows = []
    for group, items in (("首屏", FIRST_SCREEN), ("慢点", SLOW_SPOTS)):
        for path, label in items:
            el, code, n = timed(args.base, path)
            rows.append({"group": group, "path": path, "label": label,
                         "elapsed": el, "http": code, "bytes": n})
            print(f"  [{args.label}] {group} {label:16s} {el:8.3f}s http={code} bytes={n}", flush=True)

    out = {"base": args.base, "label": args.label,
           "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "rows": rows}
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    json.dump(out, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    fs = [r for r in rows if r["group"] == "首屏"]
    print(f"\n[{args.label}] 首屏 {len(fs)} 项：合计 {sum(r['elapsed'] for r in fs):.2f}s，"
          f"最大 {max(r['elapsed'] for r in fs):.2f}s，最小 {min(r['elapsed'] for r in fs):.2f}s")
    print(f"证据已写: {args.out}")


if __name__ == "__main__":
    main()
