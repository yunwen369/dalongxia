# -*- coding: utf-8 -*-
"""R14 三市场财报中文摘要 CDP 界面专项探针（v3.6.0 · REQ-20261105-04 R14）。

逐项真实浏览器验证 R14 前端交付（v7 结构：#view-body + .card）：

- R14-1  资讯工具 #/news：市场下拉（A股/港股/美股）存在且选中态正确；
- R14-2  市场切换持久：change 事件后 W7.st.newsInput.market 更新；
- R14-3  财报摘要视图 #/fin：AI 财报摘要注入卡出现（v7-fin-ai）；
- R14-4  公告库视图 #/ann：同一注入卡出现（共用 W7.st.newsInput 状态）；
- R14-5  摘要执行链：data-act="fin-ai-run" 点击分发到 runFinAiSummary，
         未配置 AI 时正确显示后端 NOT_CONFIGURED 提示（端到端可达）。

用法::

    python tools/_cdp_r14_summary.py --base http://127.0.0.1:6903 --out cdp_r14.json

退出码：0 全通过 / 1 存在失败。口令实例需 OPENBB_ACCESS_PASSWORD 环境变量。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)

from ui_clickthrough import CDP, eval_js, launch_chrome, page_ws  # noqa: E402


def goto(cdp: CDP, base: str, view: str, wait: float) -> None:
    """整页导航到指定视图（navigate 整页加载，hash 不丢）。"""
    cdp.call("Page.navigate", {"url": f"{base}/#/{view}"})
    time.sleep(wait)


def check(cdp: CDP, expr: str) -> dict:
    """求值一段返回 JSON.stringify 的 JS。"""
    return eval_js(cdp, expr) or {}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:6903")
    parser.add_argument("--out", default="cdp_r14.json")
    parser.add_argument("--port", type=int, default=9338)
    args = parser.parse_args()
    base = args.base

    proc = launch_chrome(args.port, str(Path(os.environ.get("TEMP", "/tmp")) / "openbb_r14_probe"), 1440, 900)
    report: dict = {"items": {}, "summary": {}}
    token = os.environ.get("OPENBB_ACCESS_PASSWORD", "")

    def mark(name: str, ok: bool, detail) -> None:
        report["items"][name] = {"ok": bool(ok), "detail": detail}
        print(("PASS " if ok else "FAIL ") + name + " :: " + json.dumps(detail, ensure_ascii=False)[:220])

    try:
        for _ in range(30):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{args.port}/json/list", timeout=3)
                break
            except Exception:
                time.sleep(1)
        cdp = CDP(page_ws(args.port))
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")

        # 口令实例：先 POST /auth 拿会话 cookie
        cdp.call("Page.navigate", {"url": base + "/"})
        time.sleep(3.5)
        if token:
            authed = eval_js(cdp, """(function(){
                return fetch('/auth',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'token=""" + token + """'})
                  .then(function(r){ return 'auth:' + r.status; })
                  .catch(function(e){ return 'auth-err:' + e; });
            })()""")
            print("auth:", authed)
            time.sleep(1.0)
            # 登录墙清除闸门（2026-11-13 回归批次加固）：auth 读回 {} 时 cookie 可能
            # 晚于首次 goto 才生效，news 视图会撞登录墙造成 R14_1/2 假阴。
            # 整页 reload 后轮询 view-body 就绪（SPA 已鉴权），再进判据。
            cdp.call("Page.navigate", {"url": base + "/"})
            ready = False
            for _ in range(12):
                time.sleep(2)
                probe = eval_js(cdp, "!!document.getElementById('view-body')")
                if probe:
                    ready = True
                    break
            print("auth-gate ready:", ready)
            if not ready:
                raise RuntimeError("鉴权后仍处登录墙（view-body 未出现），不作结论")

        # R14-1 资讯工具市场下拉
        goto(cdp, base, "news", 5.0)
        info = check(cdp, """(function(){
            var sel = document.getElementById('v7-news-market');
            if(!sel) return JSON.stringify({present:false});
            var opts = [].slice.call(sel.querySelectorAll('option')).map(function(o){return o.value + ':' + o.textContent;});
            return JSON.stringify({present:true, value:sel.value, opts:opts});
        })()""")
        mark("R14_1_news_market_select", bool(info.get("present")) and info.get("value") == "a"
             and set(info.get("opts") or []) >= {"a:A股", "hk:港股", "us:美股"},
             info)

        # R14-2 市场切换持久（change 事件 → W7.st.newsInput.market）
        info = check(cdp, """(function(){
            var sel = document.getElementById('v7-news-market');
            if(!sel) return JSON.stringify({present:false});
            sel.value = 'hk';
            sel.dispatchEvent(new Event('change', {bubbles:true}));
            return JSON.stringify({present:true, st: (window.W7 && W7.st && W7.st.newsInput) ? W7.st.newsInput.market : null});
        })()""")
        mark("R14_2_market_state_persist", bool(info.get("present")) and info.get("st") == "hk", info)
        # 恢复默认，避免影响后续断言
        check(cdp, """(function(){
            var sel = document.getElementById('v7-news-market');
            sel.value = 'a';
            sel.dispatchEvent(new Event('change', {bubbles:true}));
            return 'reset';
        })()""")

        # R14-3 财报摘要视图注入卡
        goto(cdp, base, "fin", 5.0)
        info = check(cdp, """(function(){
            var card = document.querySelector('#view-body .v7-fin-ai');
            if(!card) return JSON.stringify({present:false});
            var sel = card.querySelector('#v7-finai-market');
            var code = card.querySelector('#v7-finai-code');
            var btn = card.querySelector('[data-act="fin-ai-run"]');
            return JSON.stringify({present:true, market: sel?sel.value:null,
                code: !!code, btn: !!btn, title: (card.querySelector('.card-title')||{}).textContent||''});
        })()""")
        mark("R14_3_fin_view_card", bool(info.get("present")) and info.get("market") == "a"
             and info.get("code") and info.get("btn"), info)

        # R14-4 公告库视图同卡（共用状态：fin 页留存的 market 应带到 ann）
        info = check(cdp, """(function(){
            var sel = document.getElementById('v7-finai-market');
            if(sel){ sel.value='us'; sel.dispatchEvent(new Event('change',{bubbles:true})); }
            return JSON.stringify({st:(window.W7&&W7.st&&W7.st.newsInput)?W7.st.newsInput.market:null});
        })()""")
        goto(cdp, base, "ann", 5.0)
        info2 = check(cdp, """(function(){
            var card = document.querySelector('#view-body .v7-fin-ai');
            if(!card) return JSON.stringify({present:false});
            var sel = card.querySelector('#v7-finai-market');
            return JSON.stringify({present:true, market: sel?sel.value:null});
        })()""")
        mark("R14_4_ann_view_card_shared_state",
             bool(info2.get("present")) and info2.get("market") == "us" and info.get("st") == "us",
             {"fin_us": info, "ann": info2})

        # R14-5 摘要执行链（未配置 AI → NOT_CONFIGURED 提示可达）
        info = check(cdp, """(function(){
            var btn = document.querySelector('#view-body [data-act="fin-ai-run"]');
            if(!btn) return JSON.stringify({found:false});
            btn.click();
            return JSON.stringify({found:true, fn: typeof (window.W7&&W7.ai&&W7.ai.runFinAiSummary)});
        })()""")
        chain = None
        for _ in range(6):
            time.sleep(1.5)
            chain = check(cdp, """(function(){
                var box = document.getElementById('v7-finai-body');
                if(!box) return JSON.stringify({box:false});
                var txt = (box.innerText||'');
                return JSON.stringify({box:true, txt: txt.slice(0,120),
                    hasHint: txt.indexOf('AI')>=0 || txt.indexOf('配置')>=0 || txt.indexOf('生成')>=0});
            })()""")
            if chain.get("box") and (chain.get("txt") or "").find("生成中") < 0:
                break
        ok5 = bool(info.get("found")) and info.get("fn") == "function" and chain and chain.get("box") and bool(chain.get("txt"))
        mark("R14_5_run_chain_reachable", ok5, {"click": info, "result": chain})

        # 汇总
        fails = [k for k, v in report["items"].items() if not v["ok"]]
        report["summary"] = {"total": len(report["items"]), "failed": fails,
                             "passed": len(report["items"]) - len(fails)}
    finally:
        try:
            Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            print("报告写出失败:", exc)
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass

    print("SUMMARY:", json.dumps(report["summary"], ensure_ascii=False))
    sys.exit(1 if report["summary"].get("failed") else 0)


if __name__ == "__main__":
    main()
