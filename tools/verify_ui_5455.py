# -*- coding: utf-8 -*-
"""批次 5.4/5.5 前端取证：美股个股页、连接器页、Copilot 未配置引导。

用法：
    .venv\\Scripts\\python.exe tools\\verify_ui_5455.py --base http://127.0.0.1:6910 --out <截图目录>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from verify_ui_51 import CDP, goto, launch_edge, page_ws, set_viewport, shot, wait_blocks  # noqa: E402


def main() -> None:
    """执行三处取证。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:6910")
    parser.add_argument("--out", required=True)
    parser.add_argument("--port", type=int, default=9340)
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    profile = os.path.join(os.environ.get("TEMP", "."), "edge_verify_5455")
    proc = launch_edge(args.port, profile)
    result: Dict[str, Any] = {"base": args.base, "checks": {}}

    try:
        cdp = CDP(page_ws(args.port))
        cdp.call("Runtime.enable")

        # 1. 美股个股页
        set_viewport(cdp, 1366, 900)
        goto(cdp, args.base + "/#/stock?symbol=AAPL&market=us")
        state = wait_blocks(cdp, 40)
        result["checks"]["us_stock_state"] = state
        result["checks"]["us_quote"] = cdp.eval_js(
            "(function(){var n=document.querySelector('#st01-body .pg-quote-name');"
            "var p=document.querySelector('#st01-body .pg-quote-price');"
            "return n&&p?{name:n.textContent,price:p.textContent}:null;})()")
        result["checks"]["us_kline_canvas"] = cdp.eval_js(
            "document.querySelectorAll('#ST-02 canvas').length")
        shot(cdp, os.path.join(args.out, "stock_us_1366x900.png"))

        # 2. 连接器页
        set_viewport(cdp, 1366, 900)
        goto(cdp, args.base + "/#/connectors")
        wait_blocks(cdp, 40)
        result["checks"]["connectors_rows"] = cdp.eval_js(
            "document.querySelectorAll('#c01-body tbody tr').length")
        result["checks"]["connectors_gaps"] = cdp.eval_js(
            "(function(){return Array.prototype.map.call("
            "document.querySelectorAll('#c01-body .pg-news-title'),function(n){return n.textContent;})"
            ".filter(function(t){return t.indexOf('暂')<0;});})()")
        result["checks"]["connectors_badges"] = cdp.eval_js(
            "(function(){var c=document.getElementById('C-01');if(!c)return null;"
            "return {src:c.querySelectorAll('.badge-src').length,cal:c.querySelectorAll('.badge-caliber').length};})()")
        shot(cdp, os.path.join(args.out, "connectors_1366x900.png"))

        # 3. Copilot 抽屉：未配置时发送应给设置引导
        goto(cdp, args.base + "/#/sectors")
        time.sleep(1.0)
        cdp.eval_js("document.getElementById('copilot-toggle').click()")
        time.sleep(0.8)
        cdp.eval_js(
            "(function(){var i=document.querySelector('.copilot-input');"
            "if(i){i.value='你好';}var b=document.querySelector('.copilot-send');if(b)b.click();})()")
        time.sleep(2.5)
        result["checks"]["copilot_warn"] = cdp.eval_js(
            "(function(){var n=document.querySelector('.copilot-msg.warn .copilot-msg-body');"
            "return n?n.textContent.slice(0,80):null;})()")
        result["checks"]["copilot_status"] = cdp.eval_js(
            "(function(){var n=document.querySelector('.copilot-status');return n?n.textContent:null;})()")
        shot(cdp, os.path.join(args.out, "copilot_unconfigured.png"))

        result["checks"]["leaks"] = cdp.eval_js(
            "(function(){var t=document.body.innerText;"
            "return {err_code:/ERR_[A-Z_]+|UPSTREAM_|NOT_CONFIGURED/.test(t),"
            "placeholder:/dev-placeholder|\\(P1\\)/.test(t)};})()")

        cdp.close()
    finally:
        proc.terminate()

    out_json = os.path.join(args.out, "ui_verify_result_5455.json")
    with open(out_json, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps(result["checks"], ensure_ascii=False, indent=1))
    print("结果文件：", out_json)


if __name__ == "__main__":
    main()
