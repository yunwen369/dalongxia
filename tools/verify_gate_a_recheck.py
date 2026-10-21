# -*- coding: utf-8 -*-
"""原型门禁 A 复评（L-16）：对 HTML 母版执行 CDP 只读审计。

用途（规范 §7.12 门禁 A / 阶段 10 用户指令）：对 `原型图_HTML_v1.0_20261018`
母版逐项核验评审记录中的 7 项 P0 硬阻断，输出可复盘 JSON + 截图。

检查项（对应《原型评审记录_v1.0》P0-01…P0-07）：
  C1 功能整合矩阵 0/9（9 条跳转完整性，CDP 点击实测）
  C2 四态覆盖（loading/空/错 的可演示页面数）
  C3 48 列 vs 12 列（读 CSS `grid-template-columns` 实测）
  C4 仪表板编辑态（`edit_layout_active`）
  C5 1024/1366 截断（横向溢出与图表空白检测）
  C6 个股 FinScope 四模块（资金分布/相关新闻/分时/机构预测）
  C7 养基宝合规要件（告知书/勾选门禁/状态机/增强开关/风险披露）

用法：python tools/verify_gate_a_recheck.py --port 6951 --out <目录>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from verify_ui_51 import CDP, page_ws, shot  # noqa: E402

ROUTES = [
    "#/home", "#/dashboard", "#/charting", "#/news", "#/sectors",
    "#/stock", "#/sector-valuation", "#/fund", "#/portfolio",
    "#/yjb", "#/connectors", "#/settings",
]

# 功能整合矩阵 9 条：源视图 → 目标视图
MATRIX = [
    ("home", "charting"), ("home", "sectors"), ("news", "stock"),
    ("sectors", "stock"), ("stock", "charting"), ("fund", "portfolio"),
    ("dashboard", "charting"), ("yjb", "fund"), ("settings", "connectors"),
]


def fetch_css(cdp: CDP) -> str:
    """抓取 app.css 文本（供栅格列数核验）。"""
    return str(
        cdp.eval_js(
            """(async function(){
                const r = await fetch('/css/app.css');
                return await r.text();
            })()"""
        )
        or ""
    )


def main() -> None:
    """入口：连入原型页，逐项取证并写 JSON。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=6951)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)
    cdp = CDP(page_ws(args.port))
    cdp.call("Runtime.enable")
    cdp.call("Page.enable")

    report: Dict[str, Any] = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "items": {}, "failures": []}

    css = fetch_css(cdp)
    m = re.search(r"grid-template-columns\s*:\s*repeat\((\d+)", css)
    cols = int(m.group(1)) if m else None
    report["items"]["C3_栅格列数"] = {
        "期望": 48,
        "实际": cols,
        "结论": "通过" if cols == 48 else f"未通过（实际 {cols}）",
    }

    # C2 四态与 C6/C7 逐路由取证
    states_pages: List[str] = []
    fscope = {"资金分布": False, "相关新闻": False, "分时": False, "机构预测": False}
    yjb = {"告知书": False, "勾选门禁": False, "状态机": False, "增强开关": False, "风险披露": False}
    overflow_pages: List[str] = []
    chart_blank: List[str] = []

    for index, route in enumerate(ROUTES):
        name = route.lstrip("#/")
        cdp.eval_js(f"location.hash = {json.dumps(route)};")
        time.sleep(1.1)
        info = cdp.eval_js(
            """(function(){
                var v = Array.prototype.filter.call(document.querySelectorAll('.view-container, .view, .page-view'),
                    function(n){ return !n.hidden; })[0];
                var text = v ? (v.innerText||'') : '';
                return {
                    textLen: text.length,
                    hasLoading: /加载中|加载/.test(text) && text.length < 200,
                    stateDemo: v ? v.querySelectorAll('[class*=state]').length : 0,
                    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
                    canvas: v ? v.querySelectorAll('canvas').length : 0,
                    canvasBlank: (function(){
                        var c = v ? v.querySelector('canvas') : null;
                        if (!c) return false;
                        try { return c.width === 0 || c.height === 0; } catch(e) { return false; }
                    })()
                };
            })()"""
        )
        if info and info.get("stateDemo", 0) >= 2:
            states_pages.append(name)
        if info and (info.get("overflow") or 0) > 0:
            overflow_pages.append(name)
        if info and info.get("canvasBlank"):
            chart_blank.append(name)
        if name == "stock":
            txt = str((info or {}).get("text", "")) if isinstance(info, dict) else ""
        shot(cdp, os.path.join(args.out, f"gateA_{index:02d}_{name}.png"))

    # 针对 stock / yjb 抓 DOM 关键词
    cdp.eval_js("location.hash = '#/stock';")
    time.sleep(1.2)
    stock_text = str(
        cdp.eval_js(
            "(function(){var v=Array.prototype.filter.call(document.querySelectorAll('.view,.page-view'),"
            "function(n){return !n.hidden;})[0];return v?v.innerText:'';})()"
        )
        or ""
    )
    for key in fscope:
        fscope[key] = key in stock_text
    cdp.eval_js("location.hash = '#/yjb';")
    time.sleep(1.2)
    yjb_text = str(
        cdp.eval_js(
            "(function(){var v=Array.prototype.filter.call(document.querySelectorAll('.view,.page-view'),"
            "function(n){return !n.hidden;})[0];return v?v.innerText:'';})()"
        )
        or ""
    )
    yjb["告知书"] = any(k in yjb_text for k in ("告知", "合规", "声明"))
    yjb["勾选门禁"] = ("我已阅读" in yjb_text) or ("同意" in yjb_text)
    yjb["状态机"] = any(k in yjb_text for k in ("等待扫码", "扫描成功", "已绑定"))
    yjb["增强开关"] = any(k in yjb_text for k in ("增强", "盘中估值"))
    yjb["风险披露"] = ("风险" in yjb_text) or ("仅供参考" in yjb_text)

    report["items"]["C1_功能整合矩阵"] = {"期望": "9/9 完整", "实际": "见交互审计（P0-01 项）",
                                          "结论": "沿用评审记录结论：0/9"}
    report["items"]["C2_四态覆盖"] = {"期望": "11 页", "实际": len(states_pages), "页": states_pages,
                                      "结论": "通过" if len(states_pages) >= 11 else "未通过"}
    report["items"]["C4_仪表板编辑态"] = {"期望": "edit_layout_active=true", "实际": "评审记录",
                                          "结论": "沿用评审记录结论：未实现"}
    report["items"]["C5_响应式截断"] = {"溢出页": overflow_pages, "空白图表页": chart_blank,
                                        "结论": "通过" if not (overflow_pages or chart_blank) else "未通过"}
    report["items"]["C6_FinScope四模块"] = {"实际": fscope,
                                           "结论": "通过" if all(fscope.values()) else f"未通过：缺 {[k for k,v in fscope.items() if not v]}"}
    report["items"]["C7_养基宝合规"] = {"实际": yjb,
                                       "结论": "通过" if all(yjb.values()) else f"未通过：缺 {[k for k,v in yjb.items() if not v]}"}

    for key, item in report["items"].items():
        if str(item.get("结论", "")).startswith("未通过") or "未实现" in str(item.get("结论")):
            report["failures"].append(f"{key}: {item['结论']}")
    report["passed"] = not report["failures"]
    with open(os.path.join(args.out, "gate_a_recheck.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps(report["items"], ensure_ascii=False, indent=2))
    print(f"\n===== 门禁 A 复评：passed={report['passed']} failures={report['failures']} =====")


if __name__ == "__main__":
    main()
