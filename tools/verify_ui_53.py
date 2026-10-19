# -*- coding: utf-8 -*-
"""批次 5.3 前端取证（#/fund、#/portfolio）：双分辨率、空态、增删、跳转、徽章。

复用 verify_ui_51.py 的 CDP 工具；写入的临时持仓在结束时通过接口删除。
用法：
    .venv\\Scripts\\python.exe tools\\verify_ui_53.py --base http://127.0.0.1:6908 --out <截图目录>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from verify_ui_51 import CDP, goto, launch_edge, page_ws, set_viewport, shot, wait_blocks  # noqa: E402


def main() -> None:
    """执行基金页与持仓页取证。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:6908")
    parser.add_argument("--out", required=True)
    parser.add_argument("--port", type=int, default=9338)
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    profile = os.path.join(os.environ.get("TEMP", "."), "edge_verify_53")
    proc = launch_edge(args.port, profile)
    result: Dict[str, Any] = {"base": args.base, "checks": {}}
    created_id = None

    try:
        cdp = CDP(page_ws(args.port))
        cdp.call("Runtime.enable")

        # ---------- 1. 基金页双分辨率 ----------
        for width, height in ((1366, 900), (1024, 640)):
            set_viewport(cdp, width, height)
            goto(cdp, args.base + "/#/fund?code=017811")
            state = wait_blocks(cdp, 40)
            shot(cdp, os.path.join(args.out, "fund_%dx%d.png" % (width, height)))
            result["checks"]["fund_%dx%d" % (width, height)] = state
        result["checks"]["fund_badges"] = cdp.eval_js(
            "(function(){var c=document.getElementById('F-01');if(!c)return null;"
            "return {src:c.querySelectorAll('.badge-src').length,cal:c.querySelectorAll('.badge-caliber').length};})()")
        result["checks"]["fund_caliber"] = cdp.eval_js(
            "(function(){var n=document.querySelector('#F-01 .badge-caliber');return n?n.textContent:null;})()")
        result["checks"]["fund_disabled_hint"] = cdp.eval_js(
            "(function(){var n=document.querySelector('#f06-body .pg-state-hint');"
            "return n?n.textContent.slice(0,60):null;})()")

        # ---------- 2. 重仓股行点击 → #/stock ----------
        cdp.eval_js("(document.querySelector('#f04-body tbody tr')||{click:function(){}}).click()")
        time.sleep(1.5)
        result["checks"]["jump_to_stock"] = cdp.eval_js("location.hash")

        # ---------- 3. 持仓页空态 ----------
        set_viewport(cdp, 1366, 800)
        goto(cdp, args.base + "/#/portfolio")
        time.sleep(1.5)
        result["checks"]["portfolio_empty"] = cdp.eval_js(
            "(function(){var n=document.querySelector('#p01-body .pg-state');return n?n.textContent.slice(0,30):null;})()")
        shot(cdp, os.path.join(args.out, "portfolio_empty.png"))

        # ---------- 4. 新增持仓 → 列表/分布/汇总 ----------
        created = requests.post(args.base + "/api/portfolio", json={
            "market": "fund", "symbol": "017811", "name": "东方人工智能主题混合C",
            "shares": 1000, "cost": 3.0, "note": "UI 取证临时",
        }, timeout=30).json()
        created_id = created["data"]["item"]["id"]
        # 已在 #/portfolio 时仅改 hash 不会重载，必须强制刷新才能看到新数据
        cdp.call("Page.enable")
        cdp.call("Page.reload", {"ignoreCache": True})
        time.sleep(3.0)
        wait_blocks(cdp, 40)
        result["checks"]["portfolio_rows"] = cdp.eval_js("document.querySelectorAll('#p03-body tbody tr').length")
        result["checks"]["portfolio_metrics"] = cdp.eval_js(
            "(function(){return Array.prototype.map.call("
            "document.querySelectorAll('#p01-body .pg-metric-value'),function(n){return n.textContent;});})()")
        result["checks"]["portfolio_pie"] = cdp.eval_js(
            "document.querySelectorAll('#p04-body canvas').length")
        shot(cdp, os.path.join(args.out, "portfolio_filled.png"))

        # ---------- 5. 删除按钮 ----------
        cdp.eval_js("(function(){var b=document.querySelector('#p03-body .pg-btn');if(b)b.click();})()")
        time.sleep(3.0)
        wait_blocks(cdp, 30)
        result["checks"]["after_delete_rows"] = cdp.eval_js("document.querySelectorAll('#p03-body tbody tr').length")
        created_id = None  # 已被 UI 删除，finally 不再处理

        # ---------- 6. 1024 持仓页与泄漏 ----------
        set_viewport(cdp, 1024, 640)
        goto(cdp, args.base + "/#/portfolio")
        time.sleep(2.0)
        shot(cdp, os.path.join(args.out, "portfolio_1024x640.png"))
        result["checks"]["leaks"] = cdp.eval_js(
            "(function(){var t=document.body.innerText;"
            "return {err_code:/ERR_[A-Z_]+|UPSTREAM_|INVALID_PARAM|NOT_CONFIGURED/.test(t),"
            "placeholder:/dev-placeholder|\\(P1\\)/.test(t)};})()")

        cdp.close()
    finally:
        proc.terminate()
        if created_id:
            try:
                requests.delete(args.base + "/api/portfolio/" + created_id, timeout=10)
            except Exception:  # noqa: BLE001 - 清理失败不影响结果
                pass

    out_json = os.path.join(args.out, "ui_verify_result_53.json")
    with open(out_json, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps(result["checks"], ensure_ascii=False, indent=1))
    print("结果文件：", out_json)


if __name__ == "__main__":
    main()
