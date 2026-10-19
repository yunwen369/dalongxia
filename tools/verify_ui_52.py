# -*- coding: utf-8 -*-
"""批次 5.2 前端取证脚本（#/news）：双分辨率、四态、板块树、排序、AI 复核。

复用 verify_ui_51.py 的 CDP 工具函数，不重复实现浏览器驱动。
用法：
    .venv\\Scripts\\python.exe tools\\verify_ui_52.py --base http://127.0.0.1:6906 --out <截图目录>

取证要点：
- 默认态 1366×768 / 1024×640 各一张；
- 板块树：二级选中 → 末级选中，逐级改变资讯流；
- 排序：在同一板块（命中 ≥3 条）下对比前 6 条标题顺序；
- AI 复核：未配置时必须给出「去设置」引导，不弹错误码；
- 空态：选中无命中板块（如 食品饮料）时显示自然空态；
- 错误态：CDP 阻断 /api/news/* 与 /api/sentiment/*，验证组件级错误态 + 重试。
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


def click_by_text(cdp: CDP, selector: str, text: str) -> bool:
    """按可见文本点击元素（用于板块树层级节点）。"""
    expression = (
        "(function(){var nodes=document.querySelectorAll('" + selector + "');"
        "for(var i=0;i<nodes.length;i++){if(nodes[i].textContent.trim().indexOf('" + text + "')>=0){"
        "nodes[i].click();return true;}}return false;})()"
    )
    return bool(cdp.eval_js(expression))


def titles_js(limit: int = 6) -> str:
    """返回取前 N 条资讯标题的 JS 表达式。"""
    return (
        "(function(){return Array.prototype.slice.call("
        "document.querySelectorAll('.pg-feed-item .pg-feed-title'),0," + str(limit) + ")"
        ".map(function(n){return n.textContent;});})()"
    )


def main() -> None:
    """执行 #/news 的截图与交互取证。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:6906")
    parser.add_argument("--out", required=True)
    parser.add_argument("--port", type=int, default=9336)
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    profile = os.path.join(os.environ.get("TEMP", "."), "edge_verify_52b")
    proc = launch_edge(args.port, profile)
    result: Dict[str, Any] = {"base": args.base, "checks": {}}

    try:
        cdp = CDP(page_ws(args.port))
        cdp.call("Runtime.enable")
        cdp.call("Network.enable")

        # ---------- 1. 双分辨率默认态 ----------
        for width, height in ((1366, 768), (1024, 640)):
            set_viewport(cdp, width, height)
            goto(cdp, args.base + "/#/news")
            state = wait_blocks(cdp, 40)
            shot(cdp, os.path.join(args.out, "news_%dx%d.png" % (width, height)))
            result["checks"]["state_%dx%d" % (width, height)] = state

        # ---------- 2. 板块树：二级选中 → 末级选中 ----------
        set_viewport(cdp, 1366, 900)
        goto(cdp, args.base + "/#/news")
        wait_blocks(cdp, 40)
        click_by_text(cdp, ".pg-tree-row.level2", "半导体")
        time.sleep(1.6)
        wait_blocks(cdp, 40)
        count_level2 = cdp.eval_js("document.querySelectorAll('.pg-feed-item').length")
        click_by_text(cdp, ".pg-tree-row.level3", "存储芯片")
        time.sleep(1.8)
        wait_blocks(cdp, 40)
        result["checks"]["tree"] = {
            "active_leaf": cdp.eval_js(
                "(document.querySelector('.pg-tree-row.level3.active')||{}).textContent||null"),
            "count_level2": count_level2,
            "count_leaf": cdp.eval_js("document.querySelectorAll('.pg-feed-item').length"),
        }
        shot(cdp, os.path.join(args.out, "news_tree_存储芯片.png"))

        # ---------- 3. 排序：选命中较多的「能源化工」，对比前 6 条顺序 ----------
        click_by_text(cdp, ".pg-tree-row.level2", "能源化工")
        time.sleep(1.8)
        wait_blocks(cdp, 40)
        order_relevance = cdp.eval_js(titles_js())
        cdp.eval_js("document.querySelector('#N-sort .pg-tab[data-sort=\"time\"]').click()")
        time.sleep(1.8)
        wait_blocks(cdp, 40)
        order_time = cdp.eval_js(titles_js())
        result["checks"]["sort"] = {
            "count": cdp.eval_js("document.querySelectorAll('.pg-feed-item').length"),
            "order_relevance": order_relevance,
            "order_time": order_time,
            "changed": bool(order_relevance) and order_relevance != order_time,
        }

        # ---------- 4. AI 复核（未配置 → 设置引导） ----------
        cdp.eval_js("(document.querySelector('.pg-feed-item .pg-btn')||{click:function(){}}).click()")
        time.sleep(1.8)
        result["checks"]["ai_review"] = cdp.eval_js(
            "(function(){var n=document.querySelector('.pg-review-box');"
            "return n?{cls:n.className,text:(n.textContent||'').slice(0,120)}:null;})()")
        shot(cdp, os.path.join(args.out, "news_ai_review_未配置.png"))

        # ---------- 5. 空态：切到无命中板块「食品饮料」 ----------
        click_by_text(cdp, ".pg-tree-row.level2", "食品饮料")
        time.sleep(1.8)
        result["checks"]["empty"] = cdp.eval_js(
            "(function(){var e=document.querySelector('#n02-body .pg-state');"
            "return e?e.textContent.trim().slice(0,40):null;})()")
        shot(cdp, os.path.join(args.out, "news_empty_board.png"))

        # ---------- 6. 错误态：阻断新闻与情绪接口 ----------
        cdp.call("Network.setBlockedURLs", {"urls": ["*/api/news/*", "*/api/sentiment/*"]})
        cdp.call("Page.enable")
        cdp.call("Page.navigate", {"url": args.base + "/#/news"})
        cdp.call("Page.reload", {"ignoreCache": True})  # 仅改 hash 不会重载页面，必须强制刷新
        time.sleep(3.0)
        result["checks"]["error"] = cdp.eval_js(
            "(function(){var e=document.querySelector('#n02-body .pg-state');"
            "return e?e.textContent.trim().slice(0,60):null;})()")
        shot(cdp, os.path.join(args.out, "news_error_blocked.png"))
        cdp.call("Network.setBlockedURLs", {"urls": []})

        # ---------- 7. 徽章与泄漏 ----------
        goto(cdp, args.base + "/#/news")
        wait_blocks(cdp, 40)
        result["checks"]["badges"] = cdp.eval_js(
            "(function(){var c=document.getElementById('N-02');if(!c)return null;"
            "return {src:c.querySelectorAll('.badge-src').length,cal:c.querySelectorAll('.badge-caliber').length};})()")
        result["checks"]["leaks"] = cdp.eval_js(
            "(function(){var t=document.body.innerText;"
            "return {err_code:/ERR_[A-Z_]+|UPSTREAM_|INVALID_PARAM|NOT_CONFIGURED/.test(t),"
            "placeholder:/dev-placeholder|\\(P1\\)/.test(t)};})()")

        cdp.close()
    finally:
        proc.terminate()

    out_json = os.path.join(args.out, "ui_verify_result_52.json")
    with open(out_json, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps(result["checks"], ensure_ascii=False, indent=1))
    print("结果文件：", out_json)


if __name__ == "__main__":
    main()
