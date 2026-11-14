# -*- coding: utf-8 -*-
"""首屏「可用时间」实测（只读）：从导航到各渲染里程碑的耗时。

里程碑：
  t_nav_done    导航完成
  t_title       视图标题渲染
  t_cards       4 个指数卡有数值
  t_spark       4 个迷你走势出结果（SVG 或「走势无数据」）
  t_idle        页面上不再出现「加载中/统计中/榜单加载中」文案
用法: python probe_first_paint.py --base http://127.0.0.1:6905 --out out.json
"""
import argparse, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ui_clickthrough as uc  # noqa: E402

POLL = """
(function(){
  var cards = document.querySelectorAll('#index-row .card');
  var vals = 0, sparks = 0;
  Array.prototype.forEach.call(cards, function(c){
    var v = c.querySelector('.index-value');
    if (v && (v.textContent||'').trim() && (v.textContent||'').trim() !== '—') vals++;
    var sp = c.querySelector('.spark');
    if (sp && (sp.querySelector('svg') || /走势无数据/.test(sp.textContent||''))) sparks++;
  });
  var body = document.body ? (document.body.innerText||'') : '';
  var loading = /加载中|统计中|榜单加载中/.test(body);
  return JSON.stringify({
    title: (document.querySelector('#view-title')||{}).textContent||'',
    vals: vals, sparks: sparks, loading: loading
  });
})()
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--port", type=int, default=9530)
    ap.add_argument("--timeout", type=int, default=90)
    args = ap.parse_args()

    proc = uc.launch_chrome(args.port, os.path.join(os.environ.get("TEMP", "/tmp"), "fp_profile"), 1440, 900)
    time.sleep(3)
    cdp = uc.CDP(uc.page_ws(args.port))
    cdp.call("Page.enable")
    cdp.call("Runtime.enable")

    t0 = time.time()
    cdp.call("Page.navigate", {"url": args.base + "/"})
    marks = {"t_nav_done": None, "t_title": None, "t_cards": None, "t_spark": None, "t_idle": None}
    last = {}
    while time.time() - t0 < args.timeout:
        el = round(time.time() - t0, 2)
        raw = uc.eval_js(cdp, POLL)
        try:
            st = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:  # noqa: BLE001
            st = {}
        last = st
        if marks["t_title"] is None and st.get("title"):
            marks["t_title"] = el
        if marks["t_cards"] is None and st.get("vals", 0) >= 4:
            marks["t_cards"] = el
        if marks["t_spark"] is None and st.get("sparks", 0) >= 4:
            marks["t_spark"] = el
        if marks["t_idle"] is None and st.get("title") and not st.get("loading", True):
            marks["t_idle"] = el
            break
        time.sleep(1.0)

    out = {"base": args.base, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "marks": marks, "final": last}
    json.dump(out, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    try:
        proc.terminate()
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
