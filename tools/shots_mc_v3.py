"""大盘云图 v3.0 交付截图：全图 / 下钻 / 悬浮 / 周周期 / 筛选浮层。

运行：
    python tools/shots_mc_v3.py --base http://127.0.0.1:6971 --out _tmp_api/shots
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


def shot(cdp: CDP, path: Path) -> None:
    data = (cdp.call("Page.captureScreenshot", {"format": "png"}).get("result") or {}).get("data")
    if data:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(data))
        print("SHOT " + str(path))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:6971")
    ap.add_argument("--port", type=int, default=9477)
    ap.add_argument("--out", default="_tmp_api/shots")
    args = ap.parse_args()
    out = Path(args.out)

    profile = os.path.join(os.environ.get("TEMP", "."), f"mc_shot_{int(time.time())}")
    proc = launch_chrome(args.port, profile, 1600, 940)
    try:
        cdp = None
        for _ in range(40):
            try:
                cdp = CDP(page_ws(args.port))
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.3)
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": 1600, "height": 940, "deviceScaleFactor": 1, "mobile": False,
        })
        cdp.call("Page.navigate", {"url": args.base + "/market-cloud/index.html"})
        time.sleep(9)
        shot(cdp, out / "01_overview.png")

        # 悬浮详情卡
        pt = eval_js(cdp, """(function(){
          var all=MCV.all(), leaves=all.filter(function(b){return b.kind==='leaf';})
              .sort(function(a,b){return b.w*b.h-a.w*a.h;});
          var L=leaves[0], cv=document.getElementById('B-inner'), r=cv.getBoundingClientRect();
          return JSON.stringify({x:Math.round(r.left+(L.x+L.w/2)*STATE.zoom+STATE.tx),
                                 y:Math.round(r.top+(L.y+L.h/2)*STATE.zoom+STATE.ty)});
        })()""")
        p = json.loads(pt) if isinstance(pt, str) else pt
        cdp.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": p["x"], "y": p["y"], "button": "none"})
        time.sleep(1.6)
        shot(cdp, out / "02_hover.png")

        # 下钻：点「银行」一级标题
        bk = eval_js(cdp, """(function(){
          var b=MCV.all().filter(function(x){return x.kind==='g1'&&x.name==='银行';})[0];
          if(!b)return null;
          var cv=document.getElementById('B-inner'), r=cv.getBoundingClientRect();
          return JSON.stringify({x:Math.round(r.left+(b.x+50)*STATE.zoom+STATE.tx),
                                 y:Math.round(r.top+(b.hdr.y+b.hdr.h/2)*STATE.zoom+STATE.ty)});
        })()""")
        if bk:
            q = json.loads(bk) if isinstance(bk, str) else bk
            click_at(cdp, q["x"], q["y"])
            time.sleep(2.0)
        shot(cdp, out / "03_drill.png")
        eval_js(cdp, "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
        time.sleep(0.8)

        # 周周期
        eval_js(cdp, """(function(){
          var b=document.querySelector('[data-seg="period"] button[data-v="week"]');
          if(b)b.click();return !!b;
        })()""")
        time.sleep(5)
        shot(cdp, out / "04_week.png")

        # 筛选浮层
        eval_js(cdp, "(function(){document.getElementById('A-filter-entry').click();return true;})()")
        time.sleep(1.0)
        shot(cdp, out / "05_filter.png")

        # 回到日线并切到中证A500
        eval_js(cdp, "(function(){document.getElementById('D-drawer').classList.remove('on');"
                     "var b=document.querySelector('[data-seg=\"period\"] button[data-v=\"day\"]');if(b)b.click();return true;})()")
        time.sleep(4)
        eval_js(cdp, "(function(){var b=document.querySelector('[data-view=\"a500\"]');if(b)b.click();return true;})()")
        time.sleep(5)
        shot(cdp, out / "06_a500.png")

        return 0
    finally:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
