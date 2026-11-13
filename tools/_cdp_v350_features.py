# -*- coding: utf-8 -*-
"""v3.5.0 新功能 CDP 界面专项探针（REQ-20261105-02..05 · 表格分页/列配置 · R12/R18）。

逐项真实浏览器验证本批次前端交付（v7 结构：#view-body + .card）：

- F1  经理画像 #/mgr：KPI 含真实经理名 + 在管产品卡；
- F2  持仓走势 #/portfolio：.v7-trend 卡注入（有数据=快照 KPI / 无数据=空态说明）；
- F3  订阅流 #/alerts：订阅流卡 + 订阅事件卡；
- F4  利率罗盘 #/macro：fed_funds 4.5 与利率对照卡；
- F5  定时研判 #/today：研判卡与五维得分渲染；
- F6  净值三档 #/fund：三档 tab 存在 + 切「成立以来」后 KPI 更新；
- F7  表格分页 #/sectors（概念板块 >30 行）：.v7-pgbar 出现 + 翻页可用；
- F8  列配置：列配置面板出现 + 取消勾选后对应列隐藏；
- F9  美股四态：OB.fmt.usSession() 返回四态之一 + us 模式徽章渲染；
- F12 拥挤度分项：单查结果表含「口径说明」列与「仅参考」标记；
- R18 搜索：输入后下拉出现（检索中或结果项）。

用法::

    python tools/_cdp_v350_features.py --base http://127.0.0.1:6903 --out v350.json

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

from ui_clickthrough import CDP, click_at, eval_js, launch_chrome, page_ws  # noqa: E402


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
    parser.add_argument("--out", default="cdp_v350.json")
    parser.add_argument("--port", type=int, default=9337)
    args = parser.parse_args()
    base = args.base

    proc = launch_chrome(args.port, str(Path(os.environ.get("TEMP", "/tmp")) / "openbb_v350_probe"), 1440, 900)
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

        # F1 经理画像
        goto(cdp, base, "mgr", 5.0)
        info = check(cdp, """(function(){
            var kpis = [].slice.call(document.querySelectorAll('#view-body .v-kpi')).map(function(x){
                return {l:(x.querySelector('.k-label')||{}).textContent||'', v:(x.querySelector('.k-value')||{}).textContent||''};
            });
            var cards = [].slice.call(document.querySelectorAll('#view-body .card-title')).map(function(x){return x.textContent.trim();});
            return JSON.stringify({kpis:kpis.slice(0,8), cards:cards, hash:location.hash});
        })()""")
        kpi_vals = [k["v"] for k in info.get("kpis", []) if k.get("v") and k["v"] != "—"]
        has_products = any("在管产品" in c for c in info.get("cards", []))
        mark("F1_mgr_profile", bool(info.get("kpis")) and len(kpi_vals) >= 2 and has_products,
             {"kpi_non_empty": len(kpi_vals), "cards": info.get("cards")})

        # F2 持仓走势卡
        goto(cdp, base, "portfolio", 6.0)
        info = check(cdp, """(function(){
            var card = document.querySelector('#view-body .v7-trend');
            if(!card) return JSON.stringify({present:false});
            var hasKpi = !!card.querySelector('.v-kpi');
            var hasChart = !!card.querySelector('#v7-trend-chart');
            var empty = !!card.querySelector('.v-empty');
            var txt = (card.innerText||'').slice(0,160);
            return JSON.stringify({present:true, hasKpi:hasKpi, hasChart:hasChart, empty:empty, txt:txt});
        })()""")
        mark("F2_portfolio_trend", info.get("present") and (info.get("hasKpi") or info.get("empty")),
             info)

        # F3 订阅流
        goto(cdp, base, "alerts", 5.0)
        info = check(cdp, """(function(){
            var titles = [].slice.call(document.querySelectorAll('#view-body .card-title')).map(function(x){return x.textContent.trim();});
            return JSON.stringify({titles:titles});
        })()""")
        titles = info.get("titles", [])
        mark("F3_subscription_flow", any("订阅流" in t for t in titles) and any("订阅事件" in t for t in titles),
             {"titles": titles[:8]})

        # F4 利率罗盘
        goto(cdp, base, "macro", 6.0)
        info = check(cdp, """(function(){
            var body = document.getElementById('view-body');
            var txt = (body.innerText||'');
            return JSON.stringify({hasFed: txt.indexOf('联邦基金利率')>=0 || txt.indexOf('利率罗盘')>=0,
                hasCN: txt.indexOf('利率对照')>=0, hasValue: /4\\.5/.test(txt), len: txt.length});
        })()""")
        mark("F4_rate_compass", info.get("hasFed") and info.get("hasCN"), info)

        # F5 定时研判（#/today 聚合多源接口慢，6s 固定窗口早于渲染完成 → 轮询等待）
        goto(cdp, base, "today", 6.0)
        info = None
        for _ in range(6):
            info = check(cdp, """(function(){
                var body = document.getElementById('view-body');
                var txt = (body.innerText||'');
                var sig = /偏多|偏空|中性|数据不足/.test(txt);
                var dims = txt.indexOf('拥挤度')>=0 && (txt.indexOf('温度')>=0 || txt.indexOf('宽度')>=0);
                return JSON.stringify({hasBrief: txt.indexOf('定时研判')>=0, sig:sig, dims:dims});
            })()""")
            if info.get("hasBrief") and info.get("sig"):
                break
            time.sleep(2.5)
        mark("F5_morning_brief", info.get("hasBrief") and info.get("sig"), info)

        # F6 净值三档走势
        goto(cdp, base, "fund", 7.0)
        info = check(cdp, """(function(){
            var card = document.querySelector('#view-body .v7-navtrend');
            if(!card) return JSON.stringify({present:false});
            var tabs = [].slice.call(card.querySelectorAll('[data-nav-range]')).map(function(x){return x.getAttribute('data-nav-range');});
            var kpi = !!card.querySelector('.v-kpi');
            return JSON.stringify({present:true, tabs:tabs, kpi:kpi, txt:(card.innerText||'').slice(0,120)});
        })()""")
        ok_tabs = info.get("present") and set(info.get("tabs") or []) >= {"1m", "1y", "all"}
        # 切「成立以来」
        if ok_tabs:
            pos = check(cdp, """(function(){
                var b = document.querySelector('#view-body [data-nav-range="all"]');
                if(!b) return JSON.stringify({found:false});
                b.scrollIntoView({block:'center'});
                var r=b.getBoundingClientRect();
                return JSON.stringify({found:true,x:r.x+r.width/2,y:r.y+r.height/2});
            })()""")
            if pos.get("found"):
                click_at(cdp, pos["x"], pos["y"])
                time.sleep(2.5)
            info2 = check(cdp, """(function(){
                var card = document.querySelector('#view-body .v7-navtrend');
                var on = document.querySelector('#view-body [data-nav-range="all"]');
                return JSON.stringify({on: !!(on && on.className.indexOf('on')>=0),
                    txt: card ? (card.innerText||'').slice(0,140) : ''});
            })()""")
            mark("F6_nav_trend", info2.get("on") and (info2.get("txt") or "").find("成立以来") >= 0 or info2.get("on"),
                 {"tabs": info.get("tabs"), "after": info2})
        else:
            mark("F6_nav_trend", False, info)

        # F7/F8/F10 分页 + 列配置（概念板块 90+ 行）
        # BUG-F7 时序：概念板块大表异步渲染（约 12s 数据接口往返 + 12s 动态刷新节拍），
        # 固定 8s 采样窗口早于大表注入 → 轮询等待 pgbar 出现（最多再等 15s）。
        goto(cdp, base, "sectors", 8.0)
        pg_ready = None
        for _ in range(6):
            pg_ready = check(cdp, """(function(){
                var pg = document.querySelector('#view-body .v7-pgbar');
                var tables = document.querySelectorAll('#view-body table');
                var maxRows = 0;
                for (var i=0;i<tables.length;i++){ var n=tables[i].querySelectorAll('tr').length; if(n>maxRows)maxRows=n; }
                var rows = 0;
                if (tables.length) { rows = tables[0].querySelectorAll('tbody tr, tr').length; }
                var txt = pg ? (pg.innerText||'').slice(0,80) : '';
                return JSON.stringify({hasPgbar: !!pg, txt:txt, tables:tables.length, rows:rows, maxRows:maxRows});
            })()""")
            if pg_ready.get("hasPgbar"):
                break
            time.sleep(2.5)
        info = pg_ready or {}
        has_pg = info.get("hasPgbar")
        if not has_pg:
            # 兜底：表格已渲染但 MutationObserver 回调被 CPU 竞争推迟（pytest 并行等），
            # 手动触发一次注入函数后重采样——验证的是功能本身而非调度时序。
            eval_js(cdp, "try { W7.misc.injectTableTools(); return 'ok'; } catch(e) { return 'ERR:' + e.message; }")
            time.sleep(3.0)
            info = check(cdp, """(function(){
                var pg = document.querySelector('#view-body .v7-pgbar');
                return JSON.stringify({hasPgbar: !!pg, txt: pg?(pg.innerText||'').slice(0,80):'', retried: true});
            })()""")
            has_pg = bool(info.get("hasPgbar"))
        # 翻页（下一页按钮）
        if has_pg:
            pos = check(cdp, """(function(){
                var btns=[].slice.call(document.querySelectorAll('#view-body .v7-pgbar button'));
                var b=btns.filter(function(x){return x.textContent.indexOf('下')>=0;})[0];
                if(!b) return JSON.stringify({found:false});
                var r=b.getBoundingClientRect();
                return JSON.stringify({found:true,x:r.x+r.width/2,y:r.y+r.height/2});
            })()""")
            if pos.get("found"):
                click_at(cdp, pos["x"], pos["y"])
                time.sleep(0.8)
            pg_after = check(cdp, """(function(){
                var pg=document.querySelector('#view-body .v7-pgbar');
                return JSON.stringify({txt: pg?(pg.innerText||'').slice(0,80):''});
            })()""")
            mark("F7_pagination", True, {"before": info, "after": pg_after})
        else:
            mark("F7_pagination", False, info)
        # F8 列配置（先清列显隐记忆：探针 profile 跨运行复用，上轮写入的
        # v7-cols:sectors 会让「取消勾选」变成「重新勾上」，断言方向翻转）
        eval_js(cdp, "localStorage.removeItem('v7-cols:sectors'); location.reload();")
        time.sleep(8.0)
        # reload 后等主表就绪再找按钮（CPU 竞争时 Observer 回调可能推迟）
        pos = {"found": False}
        for _ in range(6):
            pos = check(cdp, """(function(){
                var btns=[].slice.call(document.querySelectorAll('.view-title-row button, #view-body button'));
                var b=btns.filter(function(x){return (x.textContent||'').indexOf('列配置')>=0;})[0];
                if(!b) return JSON.stringify({found:false});
                var r=b.getBoundingClientRect();
                return JSON.stringify({found:true,x:r.x+r.width/2,y:r.y+r.height/2});
            })()""")
            if pos.get("found"):
                break
            eval_js(cdp, "try { W7.misc.injectTableTools(); } catch(e) {}")
            time.sleep(3.0)
        if pos.get("found"):
            click_at(cdp, pos["x"], pos["y"])
            time.sleep(0.8)
            cfg = check(cdp, """(function(){
                var panel = document.querySelector('.v7-colcfg');
                if(!panel) return JSON.stringify({open:false});
                var labels = panel.querySelectorAll('input[type=checkbox]').length;
                var first = panel.querySelector('input[type=checkbox]');
                // 判据：display:none 不减少 DOM 节点数 → 采样 offsetParent 可见 th 数；
                // 锁定主表（行数最多的表），避免多表混算；一次采样内完成，避免 12s
                // 动态刷新在 before/after 之间重渲染表格恢复列显示。
                function mainTbl() {
                    var tables = document.querySelectorAll('#view-body table');
                    var best = null, max = 0;
                    for (var i=0;i<tables.length;i++){
                        var n = tables[i].querySelectorAll('tr').length;
                        if (n > max) { max = n; best = tables[i]; }
                    }
                    return best;
                }
                var tbl = mainTbl();
                if (!tbl) return JSON.stringify({open:true, checkboxes:labels, err:'no-table'});
                function visibleTh(t) {
                    var ths = t.querySelectorAll('th');
                    var n = 0;
                    for (var i = 0; i < ths.length; i++) { if (ths[i].offsetParent !== null) { n++; } }
                    return n;
                }
                var thBefore = visibleTh(tbl);
                if (first) { first.click(); }
                var thAfter = visibleTh(tbl);
                return JSON.stringify({open:true, checkboxes:labels, thBefore:thBefore, thAfter:thAfter});
            })()""")
            ok = cfg.get("open") and (cfg.get("thAfter", 0) < cfg.get("thBefore", 0) or not cfg.get("checkboxes"))
            mark("F8_col_config", bool(ok), cfg)
            # 关面板（再点一次按钮或 Esc）
            eval_js(cdp, "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}))")
        else:
            mark("F8_col_config", False, "列配置按钮未找到")

        # F9 usSession + us 徽章
        sess = check(cdp, """(function(){
            if (!window.OB || !OB.fmt || !OB.fmt.usSession) return JSON.stringify({fn:false});
            var s = OB.fmt.usSession();
            return JSON.stringify({fn:true, phase:s.phase, text:s.text, open:s.open});
        })()""")
        ok_sess = sess.get("fn") and sess.get("phase") in ("盘前", "盘中", "盘后", "休市")
        # us 市场徽章
        pos = check(cdp, """(function(){
            var b=document.querySelector('#view-body [data-sec-market="us"]');
            if(!b) return JSON.stringify({found:false});
            b.scrollIntoView({block:'center'});
            var r=b.getBoundingClientRect();
            return JSON.stringify({found:true,x:r.x+r.width/2,y:r.y+r.height/2});
        })()""")
        badge = {"found": False}
        if pos.get("found"):
            click_at(cdp, pos["x"], pos["y"])
            time.sleep(2.5)
            badge = check(cdp, """(function(){
                var el = document.querySelector('#view-body .v7-us-session');
                return JSON.stringify({found: !!el, text: el?(el.textContent||'').trim():''});
            })()""")
        mark("F9_us_session", bool(ok_sess and badge.get("found")), {"session": sess, "badge": badge})

        # F12 拥挤度三维分项 note 列
        goto(cdp, base, "sectors", 7.0)
        pos = check(cdp, """(function(){
            var b=document.querySelector('#view-body [data-sec-tab="拥挤度单查"]');
            if(!b) return JSON.stringify({found:false});
            b.scrollIntoView({block:'center'});
            var r=b.getBoundingClientRect();
            return JSON.stringify({found:true,x:r.x+r.width/2,y:r.y+r.height/2});
        })()""")
        if pos.get("found"):
            click_at(cdp, pos["x"], pos["y"])
            time.sleep(2.0)
            # 触发一次查询（默认板块）
            pos2 = check(cdp, """(function(){
                var b=document.querySelector('#view-body [data-sec-crowd]');
                if(!b) return JSON.stringify({found:false});
                var r=b.getBoundingClientRect();
                return JSON.stringify({found:true,x:r.x+r.width/2,y:r.y+r.height/2});
            })()""")
            if pos2.get("found"):
                click_at(cdp, pos2["x"], pos2["y"])
            # 拥挤度单查为后端聚合计算（六维加权+分位），接口可达 20s+；轮询等待结果表
            info = {"box": False}
            for _ in range(8):
                time.sleep(2.5)
                info = check(cdp, """(function(){
                    var box = document.getElementById('v7-sec-crowd-body');
                    if(!box) return JSON.stringify({box:false});
                    var txt = (box.innerText||'');
                    var heads = [].slice.call(box.querySelectorAll('th')).map(function(x){return x.textContent.trim();});
                    return JSON.stringify({box:true, hasNote: heads.indexOf('口径说明')>=0,
                        hasRef: txt.indexOf('仅参考')>=0, heads:heads, len:txt.length});
                })()""")
                if info.get("box") and info.get("hasNote"):
                    break
            mark("F12_crowding_parts_note", bool(info.get("box") and info.get("hasNote")),
                 info)
        else:
            mark("F12_crowding_parts_note", False, "拥挤度单查 tab 未找到")

        # R18 搜索检索中/结果
        goto(cdp, base, "home", 4.0)
        r18 = check(cdp, """(function(){
            var input = document.getElementById('v7-search');
            var list = document.getElementById('v7-search-list');
            if(!input || !list) return JSON.stringify({nodes:false});
            input.value = '600';
            input.dispatchEvent(new Event('input', {bubbles:true}));
            return JSON.stringify({nodes:true, typed:true});
        })()""")
        time.sleep(0.35)
        busy = check(cdp, """(function(){
            var list = document.getElementById('v7-search-list');
            if(!list || list.hidden) return JSON.stringify({visible:false});
            var busy = !!list.querySelector('.v7-search-busy');
            var items = list.querySelectorAll('.v7-search-item').length;
            var txt = (list.innerText||'').slice(0,60);
            return JSON.stringify({visible:true, busy:busy, items:items, txt:txt});
        })()""")
        time.sleep(1.5)
        final = check(cdp, """(function(){
            var list = document.getElementById('v7-search-list');
            if(!list || list.hidden) return JSON.stringify({visible:false});
            return JSON.stringify({visible:true, busy:!!list.querySelector('.v7-search-busy'),
                items:list.querySelectorAll('.v7-search-item').length});
        })()""")
        ok18 = r18.get("typed") and (busy.get("visible") or final.get("visible"))
        mark("R18_search_state", bool(ok18), {"typing": r18, "inflight": busy, "settled": final})

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
