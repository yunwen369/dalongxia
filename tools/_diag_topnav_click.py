# -*- coding: utf-8 -*-
"""只读诊断：顶栏 8 分组按钮点击 → hash/高亮/命中元素（判断是产品缺陷还是探针误判）。"""
import argparse, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ui_clickthrough as uc  # noqa: E402

GROUPS = ["dashboard", "market", "news", "portfolio", "money", "watchlist", "yjb", "system"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--port", type=int, default=9560)
    ap.add_argument("--pre-nav", action="store_true",
                    help="先按 walkthrough B1 的顺序遍历 28 个侧栏视图，再点顶栏（复现 B2 前置状态）")
    args = ap.parse_args()

    SIDEBAR_VIEWS = [
        "home", "today", "market", "dashboard", "watchlist", "news", "sectors",
        "stock", "fin", "ann", "tools", "cand", "charting", "macro", "valuation",
        "crowding", "screener", "lhb", "portfolio", "fund", "yjb", "mgr",
        "alerts", "connectors", "settings", "ai", "backtest", "dca",
    ]

    proc = uc.launch_chrome(args.port, os.path.join(os.environ.get("TEMP", "/tmp"), "tn_profile"), 1366, 768)
    time.sleep(3)
    cdp = uc.CDP(uc.page_ws(args.port))
    cdp.call("Page.enable")
    cdp.call("Runtime.enable")
    cdp.call("Page.navigate", {"url": args.base + "/"})
    time.sleep(8)

    if args.pre_nav:
        for v in SIDEBAR_VIEWS:
            uc.eval_js(cdp, "location.hash = '#/%s'" % v)
            time.sleep(1.4)
        print("pre-nav 完成，当前 hash=", uc.eval_js(cdp, "location.hash"))

    rows = []
    for g in GROUPS:
        info = uc.eval_js(cdp, """
        (function(){
          var b=document.querySelector('.topnav button[data-group="%s"]');
          if(!b) return JSON.stringify({found:false});
          b.scrollIntoView({block:'center'});
          var r=b.getBoundingClientRect();
          var x=Math.round(r.left+r.width/2), y=Math.round(r.top+r.height/2);
          var top=document.elementFromPoint(x,y);
          return JSON.stringify({found:true,x:x,y:y,w:Math.round(r.width),h:Math.round(r.height),
            topTag: top?top.tagName:'', topGroup: top&&top.getAttribute?top.getAttribute('data-group'):null,
            topCls: top?(top.className||''):''});
        })()
        """ % g)
        info = json.loads(info) if isinstance(info, str) else info
        rec = {"group": g, "btn": info}
        if info.get("found"):
            t0 = time.time()
            for ev in ("mousePressed", "mouseReleased"):
                cdp.call("Input.dispatchMouseEvent", {
                    "type": ev, "x": info["x"], "y": info["y"],
                    "button": "left", "clickCount": 1})
            time.sleep(1.5)
            after = uc.eval_js(cdp, """
            (function(){
              var a=document.querySelector('.topnav button.active');
              var t=document.getElementById('view-title');
              return JSON.stringify({hash:location.hash,
                active: a?a.getAttribute('data-group'):'',
                title: t?t.textContent.trim():''});
            })()""")
            rec["after"] = json.loads(after) if isinstance(after, str) else after
            rec["elapsed"] = round(time.time() - t0, 2)
        rows.append(rec)
        print(json.dumps(rec, ensure_ascii=False))

    json.dump({"base": args.base, "rows": rows}, open(args.out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("证据:", args.out)
    try:
        proc.terminate()
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
