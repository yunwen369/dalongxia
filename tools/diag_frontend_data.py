#!/usr/bin/env python3
"""真实浏览器诊断：登录正式环境，逐视图采集网络请求失败与页面报错。

用途：定位「数据无法显示」——区分是接口 401/404/超时，还是前端渲染失败。

用法：
    python tools/diag_frontend_data.py --base http://127.0.0.1:6900 \
        --token <口令> --out diag.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)

from ui_clickthrough import CDP, launch_chrome, page_ws  # noqa: E402

NET_CAPTURE = """
(function(){
  if (window.__net) return;
  window.__net = {reqs: [], fails: []};
  var of = window.fetch;
  window.fetch = function(input, init){
    var url = (typeof input === 'string') ? input : (input && input.url) || '';
    var t0 = Date.now();
    return of.apply(this, arguments).then(function(r){
      window.__net.reqs.push({url: url, status: r.status, ms: Date.now()-t0});
      if (!r.ok) { window.__net.fails.push({url: url, status: r.status}); }
      return r;
    }, function(e){
      window.__net.fails.push({url: url, status: 'NETERR', err: String(e && e.message || e)});
      throw e;
    });
  };
  window.addEventListener('error', function(e){
    window.__net.fails.push({url: 'JS:' + (e.filename||''), status: 'JSERR', err: String(e.message||'')});
  });
  window.addEventListener('unhandledrejection', function(e){
    window.__net.fails.push({url: 'PROMISE', status: 'REJECT', err: String((e.reason && e.reason.message) || e.reason || '')});
  });
})();
"""

VIEWS = ["home", "market", "sectors", "stock", "news", "fund", "macro", "valuation",
         "crowding", "screener", "lhb", "portfolio", "watchlist", "yjb", "settings",
         "alerts", "today", "fin", "ann", "mgr", "tools", "cand", "fund-combo"]

STATE_JS = """JSON.stringify({
  title: (document.getElementById('page-title')||{}).textContent||'',
  bodyLen: (document.body||{}).innerText ? document.body.innerText.length : 0,
  emptyHints: Array.from(document.querySelectorAll('.pg-empty,[data-state="empty"]'))
      .map(function(x){return (x.innerText||'').slice(0,40);}).slice(0,3),
  tables: document.querySelectorAll('table').length,
  rows: document.querySelectorAll('tbody tr').length,
  charts: document.querySelectorAll('canvas').length,
  net: window.__net || {reqs: [], fails: []}
})"""


def eval_js(cdp: CDP, expr: str):
    """求值并解析（与 ui_clickthrough 同口径）。"""
    wrapped = ("(function(){try{window.__dv=(" + expr + ");}"
               "catch(e){window.__dv='ERR:'+(e&&e.message||e);}})()")
    cdp.call("Runtime.evaluate", {"expression": wrapped, "returnByValue": True})
    res = cdp.call("Runtime.evaluate", {"expression": "window.__dv", "returnByValue": True})
    inner = res.get("result", {}).get("result", {})
    value = inner.get("value") if isinstance(inner, dict) else None
    if isinstance(value, str):
        if value.startswith("ERR:"):
            return None
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def main() -> int:
    """入口。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:6900")
    parser.add_argument("--token", default=os.environ.get("OPENBB_ACCESS_PASSWORD", ""))
    parser.add_argument("--out", required=True)
    parser.add_argument("--port", type=int, default=9391)
    args = parser.parse_args()

    url0 = f"{args.base}/auth?token={args.token}" if args.token else f"{args.base}/"
    profile = tempfile.mkdtemp(prefix="edge_diag_")
    proc = launch_chrome(args.port, profile, 1440, 900)
    rows: list[dict] = []
    try:
        time.sleep(6)
        cdp = CDP(page_ws(args.port))
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Page.navigate", {"url": url0})
        time.sleep(6)
        cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": NET_CAPTURE})
        for view in VIEWS:
            cdp.call("Page.navigate", {"url": f"{args.base}/#/{view}"})
            time.sleep(5)
            state = eval_js(cdp, STATE_JS) or {}
            state["view"] = view
            rows.append(state)
    finally:
        proc.terminate()

    fails = []
    for row in rows:
        net = row.get("net") or {}
        for item in (net.get("fails") or []):
            fails.append({"view": row.get("view"), **item})
    payload = {"base": args.base, "views": rows, "fails": fails,
               "summary": {"views": len(rows), "fails": len(fails)}}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    for row in rows:
        net = row.get("net") or {}
        reqs = net.get("reqs") or []
        print(f"[{row['view']:<12}] title={row.get('title','')[:14]:<14} "
              f"rows={row.get('rows')} tables={row.get('tables')} charts={row.get('charts')} "
              f"bodyLen={row.get('bodyLen')} reqs={len(reqs)} fails={len(net.get('fails') or [])}")
    print(f"\n总失败请求 {len(fails)}")
    for f in fails[:30]:
        print("  -", f.get("view"), f.get("status"), (f.get("url") or "")[:90], (f.get("err") or "")[:60])
    return 0


if __name__ == "__main__":
    sys.exit(main())
