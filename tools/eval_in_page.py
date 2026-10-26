#!/usr/bin/env python3
"""在页面内触发指定表达式并返回结果/异常（用于定位初始化失败点）。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from verify_frontier_fixes import CDP, launch_chrome, page_ws, set_viewport  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:6912")
    parser.add_argument("--route", default="#/home")
    parser.add_argument("--expr", required=True)
    parser.add_argument("--port", type=int, default=9354)
    parser.add_argument("--wait", type=float, default=5.0)
    args = parser.parse_args()

    profile = tempfile.mkdtemp(prefix="chrome_eval_")
    proc = launch_chrome(args.port, profile, 1366, 768)
    try:
        cdp = CDP(page_ws(args.port))
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Network.enable")
        cdp.call("Network.setCacheDisabled", {"cacheDisabled": True})
        set_viewport(cdp, 1366, 768, 1.0)
        cdp.call("Page.navigate", {"url": args.base_url + "/" + args.route})
        time.sleep(args.wait)
        wrapped = (
            "(async function(){try{var r=await (" + args.expr + ");"
            "return {ok:true,value:typeof r==='object'?JSON.stringify(r):String(r)};}"
            "catch(e){return {ok:false,err:String(e&&e.stack||e)};}})()"
        )
        res = cdp.call("Runtime.evaluate", {
            "expression": wrapped, "returnByValue": True, "awaitPromise": True,
            "userGesture": True,
        })
        print(json.dumps(res.get("result", {}).get("value", res), ensure_ascii=False, indent=2))
    finally:
        proc.kill()


if __name__ == "__main__":
    main()
