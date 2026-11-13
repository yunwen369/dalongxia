# -*- coding: utf-8 -*-
"""fin/ann/news 注入卡视觉取证截图（一次性，配合 R14 诊断基建）。"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ui_clickthrough import CDP, eval_js, launch_chrome, page_ws  # noqa: E402

BASE = "http://127.0.0.1:6903"
TOKEN = os.environ.get("OPENBB_ACCESS_PASSWORD", "")
OUT = sys.argv[1] if len(sys.argv) > 1 else "."


def shot(cdp, path):
    res = cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
    import base64
    with open(path, "wb") as f:
        f.write(base64.b64decode(res["result"]["data"]))
    print("shot:", path)


def main():
    profile = tempfile.mkdtemp(prefix="edge_finshot_")
    proc = launch_chrome(9359, profile, 1366, 768)
    try:
        time.sleep(4)
        cdp = CDP(page_ws(9359))
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Page.navigate", {"url": BASE + "/"})
        time.sleep(3.5)
        token_js = json.dumps(TOKEN)
        eval_js(cdp, (
            "(function(){return fetch('/auth',{method:'POST',headers:{'Content-Type':"
            "'application/x-www-form-urlencoded'},body:'token='+encodeURIComponent(" + token_js + ")})"
            ".then(function(r){return 'auth:'+r.status;}).catch(function(e){return 'err:'+e;});})()"
        ))
        time.sleep(1.0)
        cdp.call("Page.navigate", {"url": BASE + "/"})
        for _ in range(12):
            time.sleep(2)
            if eval_js(cdp, "!!document.getElementById('view-body')"):
                break
        for view, name in (("fin", "fin_ai_card.png"), ("news", "news_market_select.png"), ("ann", "ann_ai_card.png")):
            cdp.call("Page.navigate", {"url": f"{BASE}/#/{view}"})
            time.sleep(5)
            shot(cdp, os.path.join(OUT, name))
    finally:
        proc.terminate()


if __name__ == "__main__":
    main()
