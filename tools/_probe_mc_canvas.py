"""大盘云图 canvas 版（v3.0）快速取证：截图 + 控制台错误 + 布局计数。

运行：
    python tools/_probe_mc_canvas.py --base http://127.0.0.1:6971 --out _tmp_api/mc_v3.png
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ui_clickthrough import CDP, click_at, eval_js, launch_chrome, page_ws  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:6971")
    ap.add_argument("--path", default="/market-cloud/index.html")
    ap.add_argument("--port", type=int, default=9444)
    ap.add_argument("--out", default="_tmp_api/mc_v3.png")
    ap.add_argument("--wait", type=float, default=9.0)
    args = ap.parse_args()

    profile = os.path.join(os.environ.get("TEMP", "."), f"mc_probe_{int(time.time())}")
    proc = launch_chrome(args.port, profile, 1600, 940)
    cdp = None
    try:
        for _ in range(40):
            try:
                cdp = CDP(page_ws(args.port))
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.3)
        if cdp is None:
            print("FAIL 无法连接调试端口")
            return 2

        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Log.enable")
        cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": 1600, "height": 940, "deviceScaleFactor": 1, "mobile": False,
        })
        cdp.call("Page.navigate", {"url": args.base + args.path})
        time.sleep(args.wait)

        info = eval_js(cdp, """(function(){
          var all=(window.MCV&&MCV.all())||[];
          var leaf=all.filter(function(b){return b.kind==='leaf';}).length;
          var g1=all.filter(function(b){return b.kind==='g1';}).length;
          var g2=all.filter(function(b){return b.kind==='g2';}).length;
          var cv=document.getElementById('B-inner');
          return JSON.stringify({
            meta: window.MC_META||null,
            leaf: leaf, g1: g1, g2: g2,
            cw: cv?cv.width:0, ch: cv?cv.height:0,
            items: (window.DEMO_DATA&&DEMO_DATA[STATE.market]||[]).length,
            side: document.querySelector('.mc-side')?document.querySelector('.mc-side').getBoundingClientRect().width:0,
            bottom: document.querySelector('.mc-bottom')?document.querySelector('.mc-bottom').getBoundingClientRect().height:0,
            crumb: (document.getElementById('B-crumb')||{}).className||''
          });
        })()""")
        print("INFO " + str(info)[:900])

        errs = eval_js(cdp, "JSON.stringify((window.__mcErrors||[]).slice(0,8))")
        print("ERRORS " + str(errs)[:600])

        shot = cdp.call("Page.captureScreenshot", {"format": "png"})
        data = (shot.get("result") or {}).get("data")
        if data:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_bytes(base64.b64decode(data))
            print("SHOT " + str(Path(args.out).resolve()))

        # 悬浮第一个可命中叶子（取面积最大的叶子中心）
        pt = eval_js(cdp, """(function(){
          var all=(window.MCV&&MCV.all())||[];
          var leaves=all.filter(function(b){return b.kind==='leaf';}).sort(function(a,b){return b.w*b.h-a.w*a.h;});
          if(!leaves.length)return null;
          var cv=document.getElementById('B-inner'),r=cv.getBoundingClientRect();
          var L=leaves[0];
          return JSON.stringify({x:Math.round(r.left+(L.x+L.w/2)*STATE.zoom+STATE.tx),
                                 y:Math.round(r.top+(L.y+L.h/2)*STATE.zoom+STATE.ty),
                                 code:L.item.code,name:L.item.name});
        })()""")
        print("LEAF " + str(pt))
        if pt:
            p = json.loads(pt) if isinstance(pt, str) else pt
            cdp.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": p["x"], "y": p["y"], "button": "none"})
            time.sleep(1.2)
            pop = eval_js(cdp, "(function(){var e=document.getElementById('B-popover');return e?e.className+'|'+e.textContent.slice(0,60):null;})()")
            print("POPOVER " + str(pop))
            shot2 = cdp.call("Page.captureScreenshot", {"format": "png"})
            d2 = (shot2.get("result") or {}).get("data")
            if d2:
                out2 = str(args.out).replace(".png", "_hover.png")
                Path(out2).write_bytes(base64.b64decode(d2))
                print("SHOT " + str(Path(out2).resolve()))

            # 单击同一位置 → 应打开检视器
            click_at(cdp, p["x"], p["y"])
            time.sleep(2.0)
            insp = eval_js(cdp, """(function(){
              var r=document.getElementById('market-cloud-root');
              return JSON.stringify({cls:r.className, sel:STATE.selected,
                rows:document.querySelectorAll('#C-inspector .mc-insp-metrics > div').length});
            })()""")
            print("INSPECTOR " + str(insp))

        return 0
    finally:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
