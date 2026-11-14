# -*- coding: utf-8 -*-
"""首页指数迷你走势卡（spark）数据实测探针（只读，复用 ui_clickthrough 的 CDP 原语）。

用途：验证 /api/market/index-kline 链路在「源码实例」与「打包产物实例」上的真实渲染差异。
用法: python probe_home_spark.py --base http://127.0.0.1:6916 --out out.json [--shots dir]
"""
import argparse, base64, json, os, sys, time

TOOLS = r"F:/workbuddy记录/OpenBB Terminal Pro/A_运行资产/openbb-cn/tools"
sys.path.insert(0, TOOLS)
import ui_clickthrough as uc  # noqa: E402


def probe(base, port, profile, shots, settle=12):
    proc = uc.launch_chrome(port, profile, 1440, 900)
    time.sleep(3)
    ws = uc.page_ws(port)
    cdp = uc.CDP(ws)
    cdp.call("Page.enable")
    cdp.call("Runtime.enable")
    cdp.call("Page.navigate", {"url": base + "/"})
    time.sleep(settle)  # 等首屏 4 个 mini-kline 请求返回（冷启动实测 8–17s）

    res = uc.eval_js(cdp, """
    (function(){
      var cards = Array.prototype.map.call(document.querySelectorAll('#index-row .card'), function(c){
        var sp = c.querySelector('.spark');
        return {
          name: (c.querySelector('.index-name')||{}).textContent||'',
          value: (c.querySelector('.index-value')||{}).textContent||'',
          spark_text: sp ? (sp.textContent||'').trim() : null,
          spark_has_svg: sp ? !!sp.querySelector('svg') : null,
          src: (c.querySelector('.src-tag')||{}).textContent||''
        };
      });
      return JSON.stringify({
        title: (document.querySelector('#view-title')||document.querySelector('#page-title')||{}).textContent||'',
        cards: cards,
        src_name: (document.getElementById('src-name')||{}).textContent||'',
        conn_state: (document.getElementById('conn-state')||{}).textContent||'',
        errs: (window.__cb_errors||[]).slice(0,10)
      });
    })()
    """)
    data = json.loads(res) if isinstance(res, str) else res

    # K线视图（图表分析）
    cdp.call("Page.navigate", {"url": base + "/#/charting"})
    time.sleep(10)
    kline = uc.eval_js(cdp, """
    (function(){
      var host = document.getElementById('view-body');
      var txt = host ? (host.innerText||'') : '';
      return JSON.stringify({
        has_canvas: !!document.querySelector('#kline-chart canvas'),
        text_head: txt.slice(0, 400),
        errs: (window.__cb_errors||[]).slice(0,6)
      });
    })()
    """)
    kdata = json.loads(kline) if isinstance(kline, str) else kline

    if shots:
        os.makedirs(shots, exist_ok=True)
        cdp.call("Page.navigate", {"url": base + "/"})
        time.sleep(max(settle, 12))
        shot = cdp.call("Page.captureScreenshot", {"format": "png"})
        b64 = shot.get("result", {}).get("data")
        if b64:
            open(os.path.join(shots, "home_spark.png"), "wb").write(base64.b64decode(b64))

    out = {"base": base, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "home": data, "charting": kdata}
    json.dump(out, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print(f"== {base} 首页指数卡 ==")
    for c in data["cards"]:
        print(f"   {c['name']:12s} value={c['value']:>10s} spark_svg={c['spark_has_svg']} "
              f"spark='{c['spark_text']}' src={c['src'][:28]}")
    print(f"   src_name={data['src_name']} conn_state={data['conn_state']}")
    print(f"   console 错误数={len(data['errs'])}")
    print(f"== {base} 图表分析 ==")
    print(f"   canvas={kdata['has_canvas']} 正文前 200 字: {kdata['text_head'][:200]!r}")
    print(f"证据: {args.out}")
    try:
        proc.terminate()
    except Exception:  # noqa: BLE001
        pass
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--port", type=int, default=9522)
    ap.add_argument("--shots", default="")
    ap.add_argument("--settle", type=int, default=12)
    args = ap.parse_args()
    profile = os.path.join(os.environ.get("TEMP", "/tmp"), "probe_spark_profile")
    probe(args.base, args.port, profile, args.shots, args.settle)
