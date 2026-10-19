# -*- coding: utf-8 -*-
"""阶段 7 前端取证：#/yjb 合规告知书、状态卡、扫码区、估值查询。

用法：.venv\\Scripts\\python.exe tools\\verify_ui_7.py --base http://127.0.0.1:6913 --out <目录>
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
    """打开 #/yjb，检查告知书/状态/扫码区，并做一次估值查询。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:6913")
    parser.add_argument("--out", required=True)
    parser.add_argument("--port", type=int, default=9344)
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)

    proc = launch_edge(args.port, os.path.join(os.environ.get("TEMP", "."), "edge_verify_7"))
    result: Dict[str, Any] = {"checks": {}}
    try:
        cdp = CDP(page_ws(args.port))
        cdp.call("Runtime.enable")
        set_viewport(cdp, 1366, 900)
        goto(cdp, args.base + "/#/yjb")
        time.sleep(2.5)
        wait_blocks(cdp, 30)
        result["checks"]["consent_title"] = cdp.eval_js(
            "(function(){var n=document.querySelector('#y01-body .pg-card-sub');"
            "return n?n.textContent.slice(0,18):null;})()")
        result["checks"]["consent_buttons"] = cdp.eval_js(
            "Array.prototype.map.call(document.querySelectorAll('#y01-body .pg-btn'),function(n){return n.textContent;})")
        result["checks"]["status_cells"] = cdp.eval_js(
            "Array.prototype.map.call(document.querySelectorAll('#y02-body .pg-metric-value'),function(n){return n.textContent;})")
        result["checks"]["bind_hint"] = cdp.eval_js(
            "(function(){var n=document.querySelector('#y03-body .pg-state-hint');return n?n.textContent.slice(0,30):null;})()")
        result["checks"]["notice"] = cdp.eval_js(
            "(function(){var n=document.querySelector('#y04-body .badge-src');return n?n.textContent:null;})()")
        shot(cdp, os.path.join(args.out, "yjb_1366x900.png"))

        # 查询估值（未开启增强通道时应为 T+1 口径）
        cdp.eval_js("(function(){var b=document.querySelector('#y04-body .pg-btn');if(b)b.click();})()")
        time.sleep(4.0)
        result["checks"]["estimate"] = cdp.eval_js(
            "(function(){return Array.prototype.map.call("
            "document.querySelectorAll('#yjb-result .pg-metric-value'),function(n){return n.textContent;});})()")
        result["checks"]["caliber"] = cdp.eval_js(
            "(function(){var n=document.querySelector('#yjb-result .pg-card-sub');return n?n.textContent.slice(0,50):null;})()")
        shot(cdp, os.path.join(args.out, "yjb_estimate.png"))
        set_viewport(cdp, 1024, 640)
        time.sleep(1.0)
        shot(cdp, os.path.join(args.out, "yjb_1024x640.png"))
        result["checks"]["overflow_1024"] = cdp.eval_js(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth")
        result["checks"]["leaks"] = cdp.eval_js(
            "(function(){var t=document.body.innerText;"
            "return {err_code:/ERR_[A-Z_]+|UPSTREAM_|INVALID_PARAM/.test(t),"
            "token_leak:/[A-Za-z0-9_-]{24,}/.test(t)};})()")
        cdp.close()
    finally:
        proc.terminate()

    out_json = os.path.join(args.out, "ui_verify_result_7.json")
    with open(out_json, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps(result["checks"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
