# -*- coding: utf-8 -*-
"""F12 补验：拥挤度单查结果表「口径说明」列（只读，长等待版）。

_F12 原始探针仅等待 20s，低于该端点自身提示的冷启动 10–40s 口径 → 假阴。
本脚本把等待上限提到 120s，并输出结果表头与行数作为判据。
用法: python probe_f12_crowding.py --base http://127.0.0.1:6916 --out out.json
"""
import argparse, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ui_clickthrough as uc  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--port", type=int, default=9532)
    ap.add_argument("--wait", type=int, default=120)
    args = ap.parse_args()

    proc = uc.launch_chrome(args.port, os.path.join(os.environ.get("TEMP", "/tmp"), "f12_profile"), 1440, 900)
    time.sleep(3)
    cdp = uc.CDP(uc.page_ws(args.port))
    cdp.call("Page.enable")
    cdp.call("Runtime.enable")
    cdp.call("Page.navigate", {"url": args.base + "/#/sectors"})
    time.sleep(10)

    pos = uc.eval_js(cdp, """(function(){
        var b=document.querySelector('#view-body [data-sec-tab="拥挤度单查"]');
        if(!b) return JSON.stringify({found:false});
        b.scrollIntoView({block:'center'});
        var r=b.getBoundingClientRect();
        return JSON.stringify({found:true,x:r.x+r.width/2,y:r.y+r.height/2});
    })()""")
    pos = json.loads(pos) if isinstance(pos, str) else pos
    steps = {"tab_found": bool(pos.get("found"))}
    if pos.get("found"):
        uc.click_at(cdp, pos["x"], pos["y"])
        time.sleep(2.5)
        pos2 = uc.eval_js(cdp, """(function(){
            var b=document.querySelector('#view-body [data-sec-crowd]');
            if(!b) return JSON.stringify({found:false});
            var r=b.getBoundingClientRect();
            return JSON.stringify({found:true,x:r.x+r.width/2,y:r.y+r.height/2});
        })()""")
        pos2 = json.loads(pos2) if isinstance(pos2, str) else pos2
        steps["btn_found"] = bool(pos2.get("found"))
        if pos2.get("found"):
            t_click = time.time()
            uc.click_at(cdp, pos2["x"], pos2["y"])
            info = {}
            while time.time() - t_click < args.wait:
                time.sleep(3.0)
                raw = uc.eval_js(cdp, """(function(){
                    var box = document.getElementById('v7-sec-crowd-body');
                    if(!box) return JSON.stringify({box:false});
                    var txt = (box.innerText||'');
                    var heads = [].slice.call(box.querySelectorAll('th')).map(function(x){return x.textContent.trim();});
                    return JSON.stringify({box:true, hasNote: heads.indexOf('口径说明')>=0,
                        hasRef: txt.indexOf('仅参考')>=0, heads:heads, rows: box.querySelectorAll('tbody tr, tr').length,
                        len:txt.length, head: txt.slice(0,60)});
                })()""")
                info = json.loads(raw) if isinstance(raw, str) else (raw or {})
                if info.get("hasNote"):
                    break
            steps["elapsed"] = round(time.time() - t_click, 1)
            steps["result"] = info
            steps["verdict"] = "PASS" if info.get("hasNote") else "FAIL"

    out = {"base": args.base, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), **steps}
    json.dump(out, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    try:
        proc.terminate()
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
