#!/usr/bin/env python3
"""逐页四态截图（§7.3.1(7)）。

用途：对 29 视图 × 四态（默认/加载/空/错误）逐页截图，命名 `<路由>_<状态>.png`，
      存 `verify/states/`，作为门禁 A 的必需证据。

四态触发方式（唯一实现，不另造演示层）
------------------------------------
· 非 home 视图：调用工程既有 `window.State4.apply(kind)`（core/state4.js），
  它内部调用 `Pages.setLoading/setEmpty/setError`，与真实请求生命周期同一套函数。
· home 视图：`#viewport` 由 app.js 渲染，State4 只作用于 `.page-view`，
  故对 home 用 `Pages.setLoading/setEmpty/setError` 直接作用于 `#viewport .card-body`。
· 「默认」态：重新走一次路由（`location.hash` 重设），等价于用户重新进入该页。

用法:
  python tools/shoot_states.py --base-url http://127.0.0.1:6903 --out-dir <目录>
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v6_skin_probe import CDP, launch_edge, open_target, free_port  # noqa: E402

# 29 视图（与 frontend/js/app.js NAV_TREE 逐条一致）
VIEWS = [
    "home", "today", "market", "dashboard", "dashboard-manage", "widget-library",
    "watchlist", "news", "sectors", "stock", "fin", "ann", "tools", "cand",
    "charting", "sector-val", "macro", "valuation", "crowding", "screener",
    "lhb", "fund-combo", "portfolio", "fund", "yjb", "mgr", "alerts",
    "connectors", "settings",
]

STATES = ["默认", "加载", "空", "错误"]
STATE_KIND = {"默认": "default", "加载": "loading", "空": "empty", "错误": "error"}

# home 专用：State4 只作用于 .page-view，home 的 #viewport 需直接调 Pages 三态函数
JS_APPLY_HOME = r"""
(function (kind) {
  var P = window.Pages;
  if (!P) { return "no Pages"; }
  var nodes = document.querySelectorAll('#viewport .card-body');
  if (!nodes.length) { return "no card-body"; }
  Array.prototype.forEach.call(nodes, function (body) {
    if (kind === 'loading') { P.setLoading(body); }
    else if (kind === 'empty') { P.setEmpty(body, '暂无数据'); }
    else if (kind === 'error') { P.setError(body, '数据源暂时不可用（HTTP 503）'); }
  });
  return "ok:" + nodes.length;
})(%s)
"""

JS_APPLY_STATE4 = r"""
(function (kind) {
  var S = window.State4;
  if (!S) { return "no State4"; }
  S.apply(kind);
  return "ok";
})(%s)
"""


def shoot(cdp: CDP, path: str) -> bool:
    """截图到指定路径，返回是否成功。"""
    try:
        res = cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        data = res.get("result", {}).get("data")
        if not data:
            return False
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(data))
        return True
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:6903")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--views", default="")
    ap.add_argument("--width", type=int, default=1366)
    ap.add_argument("--height", type=int, default=768)
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    views = [v for v in args.views.split(",") if v] if args.views else VIEWS

    port = free_port()
    profile = tempfile.mkdtemp(prefix="states_")
    proc = None
    cdp = None
    records: list[dict] = []
    try:
        proc = launch_edge(port, profile)
        # 注意（2026-11-11 实测）：`/json/new?<url>` 传带 `#fragment` 的 URL 时，
        # DevTools 协议会把 fragment 丢掉（location.hash 变空 → 路由落到 home）。
        # 因此先打开裸 URL，再逐个视图用 location.hash 赋值。
        ws_url = open_target(port, args.base_url + "/")
        cdp = CDP(ws_url)
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.viewport(args.width, args.height)

        # 就绪闸门
        ready = False
        for _ in range(40):
            time.sleep(0.5)
            try:
                if cdp.eval_js("!!(window.App && window.Pages && window.State4)"):
                    ready = True
                    break
            except Exception:
                continue
        if not ready:
            raise RuntimeError("页面未就绪：App/Pages/State4 未全部可用")

        for vid in views:
            for state in STATES:
                kind = STATE_KIND[state]
                # 1) 先回默认态（重设 hash 触发重渲染）
                cdp.eval_js("location.hash = '#/__reset__'")
                time.sleep(0.15)
                cdp.eval_js(f"location.hash = '#/{vid}'")
                time.sleep(2.2)

                applied = "default"
                if kind != "default":
                    if vid == "home":
                        applied = cdp.eval_js(JS_APPLY_HOME % json.dumps(kind))
                    else:
                        applied = cdp.eval_js(JS_APPLY_STATE4 % json.dumps(kind))
                    time.sleep(0.9)

                fname = f"{vid}_{state}.png"
                fpath = os.path.join(args.out_dir, fname)
                ok = shoot(cdp, fpath)
                records.append({
                    "view": vid, "state": state, "file": fname,
                    "ok": ok, "applied": str(applied),
                })
                print(f"[{'OK' if ok else 'FAIL'}] {fname} applied={applied}")

    finally:
        if cdp:
            cdp.close()
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=8)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    ok_n = sum(1 for r in records if r["ok"])
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base_url": args.base_url,
        "viewport": f"{args.width}x{args.height}",
        "total": len(records),
        "ok": ok_n,
        "failed": len(records) - ok_n,
        "records": records,
    }
    with open(os.path.join(args.out_dir, "_states_index.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"\n=== 四态截图 {ok_n}/{len(records)} 成功 ===")
    return 0 if ok_n == len(records) else 1


if __name__ == "__main__":
    sys.exit(main())
