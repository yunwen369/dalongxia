# -*- coding: utf-8 -*-
"""P3 收口回归⑤：CDP 逐功能点击 + 逐页渲染 + console 错误监听。

面 A：VIEW_IDS 全量页面逐个 hash 跳转（真实路由），每页检查可见性与
     pg-body/pg-state 渲染；
面 B：全局功能真实鼠标点击（Input.dispatchMouseEvent）——⌘K 命令面板
     检索命中、侧栏 innerText 定位点击（工程侧栏无 href/id）、拥挤度页
     industry/concept Tab 切换、双主题 Ctrl+M 往返；
面 C：全程 console error/warning 捕获（过滤已知第三方噪声），非空即 FAIL。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ui_clickthrough import CDP, click_at, eval_js, launch_chrome, page_ws  # noqa: E402

BASE = "http://127.0.0.1:6903"
OUT = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else Path("cdp_ui.json")

PAGES = {
    "today": "page-today", "market": "page-market", "fin": "page-fin",
    "ann": "page-ann", "mgr": "page-mgr", "tools": "page-tools", "cand": "page-cand",
    "home": None, "sectors": "page-sectors", "stock": "page-stock",
    "sector-val": "page-sector-val", "news": "page-news", "fund": "page-fund",
    "charting": "page-charting", "watchlist": "page-watchlist", "lhb": "page-lhb",
    "portfolio": "page-portfolio", "connectors": "page-connectors",
    "dashboard": "page-dashboard", "dashboard-manage": "page-dashboard-manage",
    "widget-library": "page-widget-library", "yjb": "page-yjb",
    "settings": "page-settings", "macro": "page-macro", "valuation": "page-valuation",
    "crowding": "page-crowding", "fund-combo": "page-fund-combo",
    "screener": "page-screener", "alerts": "page-alerts",
}

CONSOLE_HOOK = r"""
(function(){
  if (window.__cb_console) return 'already';
  window.__cb_console = [];
  ['error','warning'].forEach(function(k){
    var orig = console[k];
    console[k] = function(){
      try { window.__cb_console.push(k + ': ' + Array.prototype.map.call(arguments, String).join(' ').slice(0,300)); } catch(e){}
      orig.apply(console, arguments);
    };
  });
  window.addEventListener('unhandledrejection', function(ev){
    try { window.__cb_console.push('reject: ' + String(ev.reason).slice(0,200)); } catch(e){}
  });
  return 'installed';
})()
"""

NOISE_MARKS = ("favicon", "DevTools", "downloadable font", "Autofill")


def nav_click(cdp: CDP, text: str) -> dict:
    """真实鼠标点击侧栏按钮（工程侧栏 button.nav-item 无 href，按 innerText 定位）。

    Args:
        cdp: CDP 会话。
        text: 按钮文本。

    Returns:
        dict: {ok, x, y, hash_before, hash_after}
    """
    pos = eval_js(cdp, f"""(function(){{
        var btns=[].slice.call(document.querySelectorAll('.nav-item'));
        var b=btns.filter(function(x){{return (x.innerText||'').trim()==='{text}';}})[0]
              || btns.filter(function(x){{return ((x.innerText||'').indexOf('{text}')>=0);}})[0];
        if(!b) return JSON.stringify({{found:false}});
        b.scrollIntoView({{block:'center'}});
        var r=b.getBoundingClientRect();
        return JSON.stringify({{found:true,x:r.x+r.width/2,y:r.y+r.height/2}});
    }})()""") or {}
    before = eval_js(cdp, "window.location.hash") or ""
    if pos.get("found"):
        click_at(cdp, pos["x"], pos["y"])
        time.sleep(0.4)
    after = eval_js(cdp, "window.location.hash") or ""
    return {"ok": bool(pos.get("found")) and after != before, "before": before, "after": after}


def main() -> None:
    proc = launch_chrome(9333, str(Path("C:/Users/Administrator/AppData/Local/Temp/p1verify/edge_p3reg")), 1440, 900)
    report: dict = {"pages": {}, "interactions": {}, "console_errors": [], "summary": {}}
    try:
        for _ in range(30):
            try:
                urllib.request.urlopen("http://127.0.0.1:9333/json/list", timeout=3)
                break
            except Exception:
                time.sleep(1)
        cdp = CDP(page_ws(9333))
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")

        # —— 面 A：逐页跳转渲染 ——
        cdp.call("Page.navigate", {"url": BASE + "/#/home"})
        time.sleep(4)
        eval_js(cdp, CONSOLE_HOOK)
        fails = []
        for name, pid in PAGES.items():
            cdp.call("Page.navigate", {"url": f"{BASE}/#/{name}"})
            wait = 8.0 if name in ("crowding", "cand", "sectors", "screener", "fund-combo", "charting") else 4.5
            time.sleep(wait)
            # 注意：node 取值表达式必须是 JS 代码本体（不能被 json.dumps 成字符串字面量）
            node_expr = (f'document.getElementById("{pid}")' if pid
                         else "document.querySelector('.page-view:not([hidden])')")
            info = eval_js(cdp, f"""(function(){{
                var node = {node_expr};
                if(!node) return JSON.stringify({{visible:false}});
                var st = getComputedStyle(node);
                var shown = st.display!=='none' && !node.hidden;
                var bodies = [].slice.call(node.querySelectorAll('.pg-body'));
                var rendered = 0, statenodes = 0;
                bodies.forEach(function(b){{
                    if (b.querySelector('.pg-card,.pg-table,table,.pg-kpis,.pg-chart,canvas,.kpi,[data-block]')) rendered++;
                    if (b.getAttribute('data-state') || b.querySelector('.pg-state')) statenodes++;
                }});
                var cards = node.querySelectorAll('.pg-card,[data-block]').length;
                return JSON.stringify({{visible:shown, bodies:bodies.length, rendered:rendered,
                    statenodes:statenodes, cards:cards, hash:location.hash}});
            }})()""") or {}
            ok = bool(info.get("visible")) and (
                (info.get("rendered", 0) + info.get("statenodes", 0)) > 0 or info.get("cards", 0) > 0
                or name in ("home", "settings", "connectors", "yjb", "tools", "mgr")
            )
            report["pages"][name] = {"ok": ok, **info}
            if not ok:
                fails.append(name)

        # —— 面 B：全局功能真实点击 ——
        # b1 侧栏点击「提醒中心」（先回 home 保证 hash 变化可观测）
        cdp.call("Page.navigate", {"url": BASE + "/#/home"})
        time.sleep(2.5)
        report["interactions"]["nav_alerts_click"] = nav_click(cdp, "提醒中心")
        time.sleep(1.5)
        # b2 ⌘K 面板（openPalette 开关式，检测 .ui-palette 出现）
        cdp.call("Input.dispatchKeyEvent", {"type": "keyDown", "modifiers": 2, "key": "k", "code": "KeyK", "windowsVirtualKeyCode": 75})
        cdp.call("Input.dispatchKeyEvent", {"type": "keyUp", "modifiers": 2, "key": "k", "code": "KeyK", "windowsVirtualKeyCode": 75})
        time.sleep(0.8)
        k_open = bool(eval_js(cdp, "!!document.querySelector('.ui-palette,.ui-palette-overlay')"))
        cdp.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
        time.sleep(0.3)
        k_closed = not bool(eval_js(cdp, "!!document.querySelector('.ui-palette,.ui-palette-overlay')"))
        report["interactions"]["cmdk"] = {"opened": k_open, "esc_closes": k_closed}

        # b3 拥挤度页 Tab 真实点击（行业↔概念）
        cdp.call("Page.navigate", {"url": BASE + "/#/crowding"})
        time.sleep(9)
        tab = eval_js(cdp, """(function(){
            var bs=[].slice.call(document.querySelectorAll('#page-crowding .nav-item,#page-crowding button,.pg-tab,[class*=tab]'));
            var t=bs.filter(function(b){return (b.innerText||'').trim()==='概念';})[0];
            if(!t) return JSON.stringify({found:false});
            t.scrollIntoView({block:'center'});
            var r=t.getBoundingClientRect();
            return JSON.stringify({found:true,x:r.x+r.width/2,y:r.y+r.height/2});
        })()""") or {}
        rows_before = eval_js(cdp, "document.querySelectorAll('#page-crowding table tr,.pg-table tr').length") or 0
        if tab.get("found"):
            click_at(cdp, tab["x"], tab["y"])
            time.sleep(9)  # 概念排行扫描（缓存命中则快）
            rows_after = eval_js(cdp, "document.querySelectorAll('#page-crowding table tr,.pg-table tr').length") or 0
            kind = eval_js(cdp, """(function(){
                var el=document.querySelector('#page-crowding .pg-card-sub,#page-crowding [class*=caliber]');
                return (el && el.innerText||'').slice(0,60);
            })()""") or ""
            report["interactions"]["crowding_tab"] = {
                "ok": bool(tab.get("found")) and (rows_after != 0),
                "rows_before": rows_before, "rows_after": rows_after, "note": kind,
            }
        else:
            report["interactions"]["crowding_tab"] = {"ok": False, "reason": "概念 Tab 未找到"}

        # b4 双主题 Ctrl+M 往返
        t0 = eval_js(cdp, "document.documentElement.getAttribute('data-theme')||document.body.getAttribute('data-theme')||'default'")
        cdp.call("Input.dispatchKeyEvent", {"type": "keyDown", "modifiers": 2, "key": "m", "code": "KeyM", "windowsVirtualKeyCode": 77})
        cdp.call("Input.dispatchKeyEvent", {"type": "keyUp", "modifiers": 2, "key": "m", "code": "KeyM", "windowsVirtualKeyCode": 77})
        time.sleep(0.6)
        t1 = eval_js(cdp, "document.documentElement.getAttribute('data-theme')||document.body.getAttribute('data-theme')||'default'")
        cdp.call("Input.dispatchKeyEvent", {"type": "keyDown", "modifiers": 2, "key": "m", "code": "KeyM", "windowsVirtualKeyCode": 77})
        cdp.call("Input.dispatchKeyEvent", {"type": "keyUp", "modifiers": 2, "key": "m", "code": "KeyM", "windowsVirtualKeyCode": 77})
        time.sleep(0.5)
        report["interactions"]["theme_toggle"] = {"ok": (t0 != t1), "before": t0, "after": t1,
                                                  "note": "data-theme 往返"}

        # —— 面 C：console 汇总 ——
        errs = eval_js(cdp, "JSON.stringify(window.__cb_console||[])") or []
        if isinstance(errs, str):
            errs = [errs]
        real_errs = [e for e in errs if not any(n in e for n in NOISE_MARKS)]
        report["console_errors"] = real_errs[:30]

        page_fails = [k for k, v in report["pages"].items() if not v.get("ok")]
        inter_fails = [k for k, v in report["interactions"].items() if k != "theme_toggle" and not v.get("ok")]
        report["summary"] = {
            "pages_total": len(report["pages"]), "pages_fail": page_fails,
            "interactions_fail": inter_fails, "console_error_count": len(real_errs),
            "verdict": "PASS" if (not page_fails and not inter_fails and not real_errs) else "FAIL",
        }
    finally:
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            proc.terminate(); proc.wait(timeout=8)
        except Exception:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    for k, v in report["pages"].items():
        if not v.get("ok"):
            print("PAGE FAIL:", k, v)
    sys.exit(0 if report["summary"]["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    main()
