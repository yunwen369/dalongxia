#!/usr/bin/env python3
"""第七轮波次 6 三页深度点击验证（M10 / M11 / M12 / M13 / M14）。

验证项：
1. #/crowding：M12-01 卡（全板块排行）——等待扫描完成，检查行数/热力格/console
2. #/portfolio：P-06（风险指标表）+ P-07（xlsx 文件选择）——检查指标行/相关性
3. #/fund-combo：第三个 Tab（候选池）点击 → 条件表 + 结果表；诊断 Tab 下 M14 权重表

用法：
  python tools/verify_wave6_ui.py --base-url http://127.0.0.1:6903 --out wave6_ui.json
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import tempfile
import time

import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ui_clickthrough import CDP, launch_chrome, page_ws, click_at, eval_js  # noqa: E402

logger = logging.getLogger(__name__)


CONSOLE_CAPTURE = """
(function(){
  if (window.__cb_console_captured) return;
  window.__cb_console_captured = true;
  window.__cb_errors = [];
  var push = function(kind, args){
    try { window.__cb_errors.push({kind: kind, text: Array.prototype.map.call(args, function(a){
      try { return (a && a.stack) ? a.stack : String(a); } catch(e){ return String(a); }
    }).join(" ")}); } catch(e){}
  };
  ["error","warn"].forEach(function(k){
    var orig = console[k];
    console[k] = function(){ push(k, arguments); orig.apply(console, arguments); };
  });
  window.addEventListener("error", function(e){ push("onerror", [e.message, e.filename + ":" + e.lineno]); });
})();
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:6903")
    parser.add_argument("--port", type=int, default=9390)
    parser.add_argument("--out", default="build/_tmp/wave6_ui.json")
    parser.add_argument("--shots", default="build/_tmp/shots_wave6")
    parser.add_argument("--wait-rank", type=float, default=90.0, help="拥挤度排行首次扫描最长等待（秒）")
    args = parser.parse_args()
    os.makedirs(args.shots, exist_ok=True)

    profile = tempfile.mkdtemp(prefix="edge_w6_")
    proc = launch_chrome(args.port, profile, 1440, 900)
    results = []
    try:
        time.sleep(4)
        cdp = CDP(page_ws(args.port))
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": CONSOLE_CAPTURE})

        def shot(name: str) -> str:
            data = cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
            path = os.path.join(args.shots, name + ".png")
            with open(path, "wb") as handle:
                handle.write(base64.b64decode(data["result"]["data"]))
            return path

        # ---------- 1) 拥挤度排行 ----------
        cdp.call("Page.navigate", {"url": f"{args.base_url}/#/crowding"})
        time.sleep(3)
        start = time.time()
        rows = 0
        while time.time() - start < args.wait_rank:
            state = eval_js(cdp, "(function(){var b=document.getElementById('m1201-body');return b?b.getAttribute('data-state'):'missing';})()")
            if state in ("default", "empty", "error"):
                break
            time.sleep(3)
        rows = eval_js(cdp, "(function(){var b=document.getElementById('m1201-body');return b?b.querySelectorAll('tbody tr').length:0;})()")
        heat = eval_js(cdp, "(function(){return document.querySelectorAll('.crowd-heat-cell').length;})()")
        state_rank = eval_js(cdp, "(function(){var b=document.getElementById('m1201-body');return b?b.getAttribute('data-state'):'missing';})()")
        results.append({
            "view": "crowding",
            "state": state_rank,
            "rank_rows": rows,
            "heat_cells": heat,
            "elapsed": round(time.time() - start, 1),
            "errors": eval_js(cdp, "JSON.stringify(window.__cb_errors||[])"),
            "shot": shot("crowding"),
        })

        # ---------- 2) 持仓风险 + xlsx ----------
        cdp.call("Page.navigate", {"url": f"{args.base_url}/#/portfolio"})
        time.sleep(5)
        risk_rows = eval_js(cdp, "(function(){var b=document.getElementById('p06-body');return b?b.querySelectorAll('tbody tr').length:0;})()")
        risk_state = eval_js(cdp, "(function(){var b=document.getElementById('p06-body');return b?b.getAttribute('data-state'):'missing';})()")
        corr_rows = eval_js(cdp, "(function(){return document.querySelectorAll('.risk-corr-row').length;})()")
        has_file = eval_js(cdp, "(function(){return !!document.getElementById('p07-file');})()")
        results.append({
            "view": "portfolio",
            "risk_state": risk_state,
            "risk_rows": risk_rows,
            "corr_rows": corr_rows,
            "xlsx_file_input": bool(has_file),
            "errors": eval_js(cdp, "JSON.stringify(window.__cb_errors||[])"),
            "shot": shot("portfolio"),
        })

        # ---------- 3) 基金组合：候选池 Tab + 权重表 ----------
        cdp.call("Page.navigate", {"url": f"{args.base_url}/#/fund-combo"})
        time.sleep(6)
        # 诊断 Tab 的 M14 权重表
        weight_rows = eval_js(cdp, "(function(){var tables=document.querySelectorAll('#page-fund-combo table');var last=tables[tables.length-1];return last?last.querySelectorAll('tbody tr').length:0;})()")
        # 点击「全市场候选池」Tab
        tab = eval_js(cdp, "(function(){var bs=Array.prototype.slice.call(document.querySelectorAll('#page-fund-combo .pg-tab'));for(var i=0;i<bs.length;i++){if(bs[i].textContent.indexOf('候选池')>=0){var r=bs[i].getBoundingClientRect();return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2),text:bs[i].textContent.trim()});}}return null;})()")
        cand_rows = 0
        cand_tab = ""
        if tab:
            point = tab if isinstance(tab, dict) else json.loads(tab)
            click_at(cdp, point["x"], point["y"])
            time.sleep(5)
            cand_tab = point["text"]
            cand_rows = eval_js(cdp, "(function(){var b=document.getElementById('page-fund-combo');if(!b)return 0;var tables=b.querySelectorAll('table');var n=0;for(var i=0;i<tables.length;i++){n+=tables[i].querySelectorAll('tbody tr').length;}return n;})()")
        results.append({
            "view": "fund-combo",
            "weight_rows": weight_rows,
            "candidates_tab": cand_tab,
            "candidates_table_rows": cand_rows,
            "errors": eval_js(cdp, "JSON.stringify(window.__cb_errors||[])"),
            "shot": shot("fund-combo"),
        })

        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump({"base_url": args.base_url, "results": results}, handle, ensure_ascii=False, indent=2)
        print(json.dumps({"ok": True, "out": args.out}, ensure_ascii=False))
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception as exc:  # noqa: BLE001 - 浏览器已退出时忽略，仅记录
            logger.info("浏览器进程结束失败（可能已退出）：%s", type(exc).__name__)


if __name__ == "__main__":
    main()
