# -*- coding: utf-8 -*-
"""阶段 8 · charting/settings 新视图 CDP 取证（阶段 8 门禁 B 补齐项）。

验证点：
  1. #/charting 图表分析：CH-01 报价卡渲染（价格/涨跌红绿）、CH-02 四 pane K线 canvas、
     MA/VOL/MACD/KDJ 系列齐全、分时 Tab 切换（午休断裂）、画线开关、换市场（00700 港股）重取；
  2. #/settings 设置：AI 配置表单回显与保存入口、主题切换按钮、关于卡 health 表格；
  3. 四态：loading→default 显式收口（wait_blocks 判定）；
  4. 双分辨率截图：1366×900 与 1024×640。

用法：
    .venv\\Scripts\\python.exe tools\\verify_ui_8.py --base http://127.0.0.1:6911 --out <截图目录>
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
    """执行 charting/settings 两视图取证。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:6911")
    parser.add_argument("--out", required=True)
    parser.add_argument("--port", type=int, default=9345)
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    profile = os.path.join(os.environ.get("TEMP", "."), "edge_verify_8")
    proc = launch_edge(args.port, profile)
    result: Dict[str, Any] = {"base": args.base, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                              "checks": {}, "errors": []}
    try:
        cdp = CDP(page_ws(args.port))
        cdp.call("Runtime.enable")

        # ---------- 1. #/charting 图表分析（1366×900） ----------
        set_viewport(cdp, 1366, 900)
        goto(cdp, args.base + "/#/charting")
        state = wait_blocks(cdp, 40)
        result["checks"]["charting_state"] = state
        result["checks"]["charting_quote"] = cdp.eval_js(
            "(function(){var n=document.querySelector('#ch01-body .pg-quote-name');"
            "var p=document.querySelector('#ch01-body .pg-quote-price');"
            "return n&&p?{name:n.textContent,price:p.textContent}:null;})()")
        # CH-02 主图：canvas 存在 + ECharts 四 pane 系列（K线/MA/成交量/MACD/KDJ）
        result["checks"]["charting_canvas"] = cdp.eval_js(
            "document.querySelectorAll('#ch02-body canvas').length")
        result["checks"]["charting_series"] = cdp.eval_js(
            "(function(){var box=document.querySelector('#ch02-body .pg-chart');"
            "if(!box)return null;var inst=window.echarts.getInstanceByDom(box);"
            "if(!inst)return null;return (inst.getOption().series||[]).map(function(s){return s.name;});})()")
        result["checks"]["charting_data_state"] = cdp.eval_js(
            "(function(){return Array.prototype.map.call("
            "document.querySelectorAll('#page-charting .pg-body'),function(n){"
            "return n.getAttribute('data-state');});})()")
        shot(cdp, os.path.join(args.out, "charting_1366x900.png"))

        # 换市场重取：港股 00700（验证查询链路真实工作）
        cdp.eval_js(
            "(function(){document.getElementById('ch-market').value='hk';"
            "document.getElementById('ch-symbol').value='00700';"
            "document.getElementById('ch-query').click();})()")
        result["checks"]["charting_hk_quote"] = cdp.eval_js(
            "(function(){return new Promise(function(resolve){"
            "setTimeout(function(){var n=document.querySelector('#ch01-body .pg-quote-name');"
            "var p=document.querySelector('#ch01-body .pg-quote-price');"
            "resolve(n&&p?{name:n.textContent,price:p.textContent}:null);},12000);});})()",
            await_promise=True)
        shot(cdp, os.path.join(args.out, "charting_hk_1366x900.png"))

        # 分时 Tab + 画线开关
        cdp.eval_js(
            "(function(){var tab=document.querySelector('#ch-tabs .pg-tab[data-tab=\"minute\"]');"
            "if(tab)tab.click();})()")
        result["checks"]["charting_minute_state"] = cdp.eval_js(
            "(function(){return new Promise(function(resolve){"
            "setTimeout(function(){resolve(document.querySelector('#ch02-body')"
            ".getAttribute('data-state'));},5000);});})()",
            await_promise=True)
        result["checks"]["charting_draw_button"] = cdp.eval_js(
            "(function(){var b=document.getElementById('ch-draw');"
            "return b?b.textContent:null;})()")
        shot(cdp, os.path.join(args.out, "charting_minute_1366x900.png"))

        # ---------- 2. #/settings 设置 ----------
        set_viewport(cdp, 1366, 900)
        goto(cdp, args.base + "/#/settings")
        state = wait_blocks(cdp, 40)
        result["checks"]["settings_state"] = state
        result["checks"]["settings_ai_form"] = cdp.eval_js(
            "(function(){var b=document.getElementById('set-base-url');"
            "var k=document.getElementById('set-api-key');"
            "var m=document.getElementById('set-model');"
            "var s=document.getElementById('set-ai-save');"
            "return b&&k&&m&&s?{base:true,keyType:k.type,save:true}:null;})()")
        result["checks"]["settings_ai_status"] = cdp.eval_js(
            "(function(){var n=document.getElementById('set-ai-status');"
            "return n?n.textContent:null;})()")
        result["checks"]["settings_about_rows"] = cdp.eval_js(
            "document.querySelectorAll('#set-about-body table tr').length")
        result["checks"]["settings_theme"] = cdp.eval_js(
            "(function(){var b=document.getElementById('set-theme-toggle');"
            "return b?b.textContent:null;})()")
        shot(cdp, os.path.join(args.out, "settings_1366x900.png"))

        # 主题切换实测：dark→light→dark（Ctrl+M 之外的第二入口）
        cdp.eval_js(
            "(function(){document.getElementById('set-theme-toggle').click();})()")
        result["checks"]["settings_theme_after"] = cdp.eval_js(
            "(function(){return document.documentElement.getAttribute('data-theme');})()")
        shot(cdp, os.path.join(args.out, "settings_light_1366x900.png"))

        # ---------- 3. 1024×640 小窗复测 ----------
        set_viewport(cdp, 1024, 640)
        goto(cdp, args.base + "/#/charting")
        state = wait_blocks(cdp, 40)
        result["checks"]["charting_state_1024"] = state
        shot(cdp, os.path.join(args.out, "charting_1024x640.png"))
        goto(cdp, args.base + "/#/settings")
        state = wait_blocks(cdp, 40)
        result["checks"]["settings_state_1024"] = state
        shot(cdp, os.path.join(args.out, "settings_1024x640.png"))
    except Exception as error:  # noqa: BLE001 - 取证脚本如实记录失败
        result["errors"].append(str(error))
    finally:
        proc.terminate()

    out_path = os.path.join(args.out, "ui_verify_result_8.json")
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=1)
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
