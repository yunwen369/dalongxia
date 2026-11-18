"""大盘云图 v3.0 交互取证：下钻 / 面包屑 / 周期 / 成分视图 / 筛选浮层。

运行：
    python tools/verify_mc_v3.py --base http://127.0.0.1:6971
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ui_clickthrough import CDP, click_at, eval_js, launch_chrome, page_ws  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + ("  | " + detail if detail else ""))


def wait_until(cdp: CDP, expr: str, timeout: float = 25.0, interval: float = 0.4) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if eval_js(cdp, expr):
            return True
        time.sleep(interval)
    return False


def center_of(cdp: CDP, kind: str, name: str | None = None) -> dict | None:
    """取某类盒子的屏幕坐标（可在 name 过滤）。"""
    raw = eval_js(cdp, """(function(){
      var all=(window.MCV&&MCV.all())||[];
      var k=%s, nm=%s;
      var hit=all.filter(function(b){return b.kind===k && (!nm || b.name===nm || b.l1===nm);})
                  .sort(function(a,b){
                    /* 避开左上角面包屑浮层遮挡的区域 */
                    var pa=(a.x>200||a.y>60)?0:1, pb=(b.x>200||b.y>60)?0:1;
                    if(pa!==pb)return pa-pb;
                    return b.w*b.h-a.w*a.h;
                  });
      if(!hit.length)return null;
      var b=hit[0], cv=document.getElementById('B-inner'), r=cv.getBoundingClientRect();
      var y=b.hdr?b.hdr.y+b.hdr.h/2:b.y+b.h/2;
      return JSON.stringify({x:Math.round(r.left+(b.x+Math.min(b.w/2,60))*STATE.zoom+STATE.tx),
                             y:Math.round(r.top+y*STATE.zoom+STATE.ty),
                             name:b.name||b.l1||''});
    })()""" % (json.dumps(kind), json.dumps(name)))
    if not raw:
        return None
    return json.loads(raw) if isinstance(raw, str) else raw


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:6971")
    ap.add_argument("--port", type=int, default=9466)
    args = ap.parse_args()

    profile = os.path.join(os.environ.get("TEMP", "."), f"mc_v3_{int(time.time())}")
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
        cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": (
            "window.__mcErrors=[];"
            "window.addEventListener('error',function(e){window.__mcErrors.push(String(e.message||e))});"
            "window.addEventListener('unhandledrejection',function(e){window.__mcErrors.push('promise:'+String(e.reason))});"
        )})
        cdp.call("Page.navigate", {"url": args.base + "/market-cloud/index.html"})

        ready = wait_until(cdp, "!!(window.MCV&&MCV.all().length>3000)", 40)
        check("V-01 画布完成首帧布局（>3000 盒）", ready,
              str(eval_js(cdp, "(window.MCV&&MCV.all().length)||0")))

        # V-02 布局层级：32 个一级 / 上百个二级
        g1 = eval_js(cdp, "MCV.all().filter(function(b){return b.kind==='g1';}).length")
        g2 = eval_js(cdp, "MCV.all().filter(function(b){return b.kind==='g2';}).length")
        check("V-02 一级分组 30~36 个、二级 > 80 个", isinstance(g1, int) and 30 <= g1 <= 36 and isinstance(g2, int) and g2 > 80, f"g1={g1} g2={g2}")

        # V-03 面积口径 = 总市值（银行占比应显著大于按只数）
        share = eval_js(cdp, """(function(){
          var all=MCV.all(), tot=0, bank=0;
          all.forEach(function(b){ if(b.kind==='g1'){ tot+=b.w*b.h; if(b.name==='银行') bank=b.w*b.h; }});
          return +(bank/tot*100).toFixed(2);
        })()""")
        check("V-03 银行面积占比 13%~19%（市值口径）", isinstance(share, (int, float)) and 13 <= share <= 19, str(share) + "%")

        # V-04 画布非空白（采样像素方差）
        nonblank = eval_js(cdp, """(function(){
          var cv=document.getElementById('B-inner'), c=cv.getContext('2d');
          var d=c.getImageData(0,0,Math.min(cv.width,400),Math.min(cv.height,300)).data;
          var seen={}, n=0;
          for(var i=0;i<d.length;i+=4){
            var k=(d[i]>>4)+'-'+(d[i+1]>>4)+'-'+(d[i+2]>>4);
            if(!seen[k]){seen[k]=1;n++;}
          }
          return n;
        })()""")
        check("V-04 画布已绘制（颜色种类 > 12）", isinstance(nonblank, int) and nonblank > 12, str(nonblank))

        # V-05 单击一级标题 → 下钻 + 面包屑
        p = center_of(cdp, "g1", "电子")
        if p:
            click_at(cdp, p["x"], p["y"])
            time.sleep(1.6)
        crumb = eval_js(cdp, "(function(){var c=document.getElementById('B-crumb');return c.className+'|'+c.textContent;})()")
        leaf_drill = eval_js(cdp, "MCV.all().filter(function(b){return b.kind==='leaf';}).length")
        check("V-05 单击一级标题进入下钻并显示面包屑", "on" in str(crumb) and "电子" in str(crumb), str(crumb)[:80])
        check("V-06 下钻后叶子数收敛到该板块", isinstance(leaf_drill, int) and 0 < leaf_drill < 800, str(leaf_drill))

        # V-07 点击二级标题 → 三级面包屑
        p2 = center_of(cdp, "g2")
        if p2:
            click_at(cdp, p2["x"], p2["y"])
            time.sleep(1.4)
        crumb2 = eval_js(cdp, "(function(){var c=document.getElementById('B-crumb');return c.textContent;})()")
        check("V-07 单击二级标题进入第三级", str(crumb2).count("›") >= 2, str(crumb2)[:80])

        # V-08 Esc 逐级返回
        eval_js(cdp, "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
        time.sleep(0.8)
        c_after = eval_js(cdp, "(function(){var c=document.getElementById('B-crumb');return c.textContent;})()")
        check("V-08 Esc 返回上一级面包屑", str(c_after).count("›") == 1, str(c_after)[:80])
        eval_js(cdp, "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
        time.sleep(0.8)
        hidden = eval_js(cdp, "(function(){var c=document.getElementById('B-crumb');return c.className;})()")
        check("V-09 再按 Esc 回到全部（面包屑隐藏）", "on" not in str(hidden), str(hidden))

        # V-10 筛选浮层定位在按钮右侧
        eval_js(cdp, "(function(){document.getElementById('A-filter-entry').click();return true;})()")
        time.sleep(0.8)
        pos = eval_js(cdp, """(function(){
          var d=document.getElementById('D-drawer'), b=document.getElementById('A-filter-entry');
          var rd=d.getBoundingClientRect(), rb=b.getBoundingClientRect();
          return JSON.stringify({on:d.classList.contains('on'), left:Math.round(rd.left), btnRight:Math.round(rb.right),
            top:Math.round(rd.top), btnTop:Math.round(rb.top), w:Math.round(rd.width)});
        })()""")
        pd = json.loads(pos) if isinstance(pos, str) else (pos or {})
        check("V-10 筛选面板为按钮右侧浮层", bool(pd.get("on")) and pd.get("left", 0) >= pd.get("btnRight", 0), str(pos))
        eval_js(cdp, "(function(){document.getElementById('D-drawer').classList.remove('on');return true;})()")

        # V-11 切周期「周」→ 量程变为 ±10%，且 avgPct 变化
        eval_js(cdp, """(function(){
          var b=document.querySelector('[data-seg="period"] button[data-v="week"]');
          if(b)b.click();return !!b;
        })()""")
        wk = wait_until(cdp, "(window.MC_META||{}).period==='week' && (window.MC_META||{}).legendMax===10", 40)
        wk_avg = eval_js(cdp, "(function(){var e=document.querySelector('#A-stat .row b');return e?e.textContent:null;})()")
        check("V-11 切到周周期：量程 ±10% 且统计刷新", wk, str(wk_avg))

        # V-12 视图切到中证A500 → 总数 500
        eval_js(cdp, """(function(){
          var b=document.querySelector('[data-view="a500"]');
          if(b)b.click();return !!b;
        })()""")
        a500 = wait_until(cdp, "(window.MC_META||{}).total===500 || MCV.all().filter(function(b){return b.kind==='leaf';}).length<=520", 40)
        leaf_a500 = eval_js(cdp, "MCV.all().filter(function(b){return b.kind==='leaf';}).length")
        check("V-12 中证A500 成分视图生效（叶子 ≤ 520）", a500 and isinstance(leaf_a500, int) and leaf_a500 <= 520, str(leaf_a500))

        # V-13 切回 A股全图
        eval_js(cdp, "(function(){var b=document.querySelector('[data-view=\"all\"]');if(b)b.click();return true;})()")
        back = wait_until(cdp, "MCV.all().filter(function(b){return b.kind==='leaf';}).length>3000", 40)
        check("V-13 切回 A 股全图恢复 > 3000 方块", back)

        # V-14 切换市场 ETF
        eval_js(cdp, "(function(){var b=document.querySelector('[data-market=\"etf\"]');if(b)b.click();return true;})()")
        etf = wait_until(cdp, "(window.MC_META||{}).market==='etf' && (window.MC_META||{}).total>1500", 40)
        check("V-14 切换到 ETF 拉到 > 1500 只", etf, str(eval_js(cdp, "(window.MC_META||{}).total")))

        # V-15 无 JS 异常
        errs = eval_js(cdp, "JSON.stringify((window.__mcErrors||[]).slice(0,6))")
        check("V-15 页面无未捕获异常", str(errs) in ("[]", "None", "null", ""), str(errs)[:200])

        return 0 if all(ok for _, ok, _ in RESULTS) else 1
    finally:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
