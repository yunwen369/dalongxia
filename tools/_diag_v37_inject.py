# -*- coding: utf-8 -*-
"""临时诊断：v3.7.0 板块研判卡 / 仪表板管理注入器为何未生效。

用法: python tools/_diag_v37_inject.py --base http://127.0.0.1:6905 --view market
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ui_clickthrough as uc  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:6905")
    ap.add_argument("--view", default="market")
    ap.add_argument("--seq", default="", help="逗号分隔的 hash 切换序列（模拟探针 SPA 导航）")
    ap.add_argument("--seqwait", type=float, default=8.0, help="每个 seq 步骤等待秒数")
    ap.add_argument("--port", type=int, default=9871)
    args = ap.parse_args()

    profile = os.path.join(os.environ.get("TEMP", "/tmp"), "diag37_%d" % int(time.time()))
    proc = uc.launch_chrome(args.port, profile, 1500, 950)
    time.sleep(3)
    cdp = uc.CDP(uc.page_ws(args.port))
    cdp.call("Page.enable")
    cdp.call("Runtime.enable")
    uc.eval_js(cdp, uc.CONSOLE_CAPTURE)

    cdp.call("Page.navigate", {"url": args.base + "/#/" + args.view})
    time.sleep(9)

    if args.seq:
        for v in [x.strip() for x in args.seq.split(",") if x.strip()]:
            uc.eval_js(cdp, "location.hash = '#/%s'" % v)
            time.sleep(args.seqwait)
            r = uc.eval_js(cdp, """JSON.stringify({
              hash:location.hash,
              cards:document.querySelectorAll('#view-body .card').length,
              brief:!!document.querySelector('.v7-brief-board'),
              mgr:!!document.querySelector('.v7-dash-mgr'),
              rows:document.querySelectorAll('tr[data-dash-id]').length,
              injectErrs:(window.__inject_errors||[]).slice(0,8),
              briefManual:(function(){
                try { W7.data.injectBoardBriefPanel(); } catch(e){ return 'ERR ' + (e && e.message || e); }
                return document.querySelector('.v7-brief-board') ? 'ok' : 'none';
              })(),
              title:(document.getElementById('view-title')||{}).textContent||''
            })""")
            print("[seq %s] %s" % (v, r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)))

    def ev(expr, label):
        val = uc.eval_js(cdp, expr)
        print("[%s] %s" % (label, json.dumps(val, ensure_ascii=False)[:600] if not isinstance(val, str) else val[:600]))
        return val

    ev("JSON.stringify({hash:location.hash, viewBody:!!document.getElementById('view-body'), "
       "title:(document.getElementById('view-title')||{}).textContent||''})", "page")
    ev("JSON.stringify({w7:typeof window.W7, data:typeof (window.W7&&W7.data), "
       "hasBrief:!!(window.W7&&W7.data&&W7.data.injectBoardBriefPanel), "
       "hasMgr:!!(window.W7&&W7.data&&W7.data.injectDashboardManager), "
       "hasEnsure:!!(window.W7&&W7.data&&W7.data.ensureBoardPool), "
       "hasPortfolioMgr:!!(window.W7&&W7.portfolio&&W7.portfolio.injectDashboardManager), "
       "helpers:typeof window.V6_HELPERS, bp:!!(window.V6_HELPERS&&V6_HELPERS.boardPickerHtml)})", "namespaces")

    # 手动调用注入器，捕获异常
    ev("""(function(){
      try { W7.data.injectBoardBriefPanel(); return JSON.stringify({called:true, card:!!document.querySelector('.v7-brief-board'), msg:''}); }
      catch(e){ return JSON.stringify({called:false, card:!!document.querySelector('.v7-brief-board'), msg:String(e && e.message || e)}); }
    })()""", "callBrief")

    ev("""(function(){
      try { W7.portfolio.injectDashboardManager(); return JSON.stringify({called:true, bar:!!document.querySelector('.v7-dash-mgr')}); }
      catch(e){ return JSON.stringify({called:false, bar:!!document.querySelector('.v7-dash-mgr'), msg:String(e && e.message || e)}); }
    })()""", "callMgr")

    ev("JSON.stringify({cards:document.querySelectorAll('#view-body .card').length, "
       "cardTitles:[].map.call(document.querySelectorAll('#view-body .card-title'),function(x){return (x.textContent||'').trim();})})", "cards")

    errs = uc.eval_js(cdp, "(window.__cb_errors||[]).slice(0,8)") or []
    print("[console]", json.dumps(errs, ensure_ascii=False)[:800])

    try:
        proc.terminate()
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
