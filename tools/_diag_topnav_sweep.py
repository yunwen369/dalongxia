# -*- coding: utf-8 -*-
"""只读诊断：顶栏宽度扫描 —— 各断点下「8 分组是否可点」「是否横向溢出」「右状态区可见项」。

用法: python diag_topnav_sweep.py --base http://127.0.0.1:6905 --out out.json
"""
import argparse, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ui_clickthrough as uc  # noqa: E402

WIDTHS = [1920, 1600, 1440, 1366, 1280, 1200, 1024, 768]
SKINS = ["v6", "dark", "light"]

MEASURE = r"""
(function(){
  var nav = document.querySelector('.topnav');
  var tr = document.querySelector('.topright');
  var nb = nav ? nav.getBoundingClientRect() : null;
  var tb = tr ? tr.getBoundingClientRect() : null;
  var items = [];
  Array.prototype.forEach.call(document.querySelectorAll('.topnav button'), function (b) {
    var r = b.getBoundingClientRect();
    var x = Math.round(r.left + r.width / 2), y = Math.round(r.top + r.height / 2);
    var top = document.elementFromPoint(x, y);
    items.push({group: b.getAttribute('data-group'), x: x, right: Math.round(r.right),
      inNav: nb ? (r.right <= nb.right + 0.5 && r.left >= nb.left - 0.5) : null,
      hit: (top && top.getAttribute) ? top.getAttribute('data-group') : null});
  });
  var vis = [];
  Array.prototype.forEach.call(tr ? tr.children : [], function (c) {
    var r = c.getBoundingClientRect();
    var s = getComputedStyle(c);
    var inside = tb ? (r.right <= tb.right + 0.5) : false;
    if (s.display !== 'none' && r.width > 0) {
      vis.push({cls: (c.className || c.tagName) + '', clipped: !inside,
                id: c.id || ''});
    }
  });
  return JSON.stringify({
    iw: window.innerWidth, sidebarW: getComputedStyle(document.body).getPropertyValue('--sidebar-w').trim(),
    docSW: document.documentElement.scrollWidth,
    navBox: nb ? {l: Math.round(nb.left), r: Math.round(nb.right), w: Math.round(nb.width)} : null,
    topBox: tb ? {l: Math.round(tb.left), r: Math.round(tb.right), w: Math.round(tb.width)} : null,
    topScrollW: tr ? tr.scrollWidth : 0,
    items: items,
    trVisible: vis
  });
})()
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--port", type=int, default=9580)
    args = ap.parse_args()

    proc = uc.launch_chrome(args.port, os.path.join(os.environ.get("TEMP", "/tmp"),
                                                    "sweep_%d" % int(time.time())), 1920, 1080)
    time.sleep(3)
    cdp = uc.CDP(uc.page_ws(args.port))
    cdp.call("Page.enable")
    cdp.call("Runtime.enable")
    cdp.call("Page.navigate", {"url": args.base + "/"})
    time.sleep(9)

    out = {"base": args.base, "runs": []}
    for skin in SKINS:
        uc.eval_js(cdp, "(function(){var b=document.querySelector(\".skin-switch button[data-skin='%s']\");"
                        "if(b){b.click();}return 1;})()" % skin)
        time.sleep(1.0)
        for w in WIDTHS:
            cdp.call("Emulation.setDeviceMetricsOverride",
                     {"width": w, "height": 900, "deviceScaleFactor": 1, "mobile": False})
            time.sleep(0.9)
            raw = uc.eval_js(cdp, MEASURE)
            data = json.loads(raw) if isinstance(raw, str) else raw
            if not data or not data.get("items"):
                # 页面未就绪/目标不可达：明确跳过而不是崩在格式化上
                print(f"  {skin:6s} w={w:5d} —— 未取到 .topnav（目标未就绪或服务不可达），跳过")
                out["runs"].append({"skin": skin, "w": w, "skipped": "no-topnav"})
                continue
            bad = [i["group"] for i in data["items"] if i["hit"] != i["group"]]
            clipped = [v["id"] or v["cls"] for v in data["trVisible"] if v["clipped"]]
            rec = {"skin": skin, "w": w, "ok_nav": not bad, "bad_nav": bad,
                   "docSW": data["docSW"], "overflow": data["docSW"] > data["iw"],
                   "nav_w": (data["navBox"] or {}).get("w"), "top_w": (data["topBox"] or {}).get("w"),
                   "top_scrollW": data["topScrollW"], "tr_clipped": clipped}
            out["runs"].append(rec)
            print(f"  {skin:6s} w={w:5d} nav={rec['nav_w']:4} top={rec['top_w']:4}/content {rec['top_scrollW']:4} "
                  f"导航遮挡={bad or '无'} 溢出={rec['overflow']} 右区裁切={clipped or '无'}")
    cdp.call("Emulation.clearDeviceMetricsOverride")
    json.dump(out, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("证据:", args.out)
    try:
        proc.terminate()
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
