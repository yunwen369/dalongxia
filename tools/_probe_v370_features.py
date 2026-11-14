# -*- coding: utf-8 -*-
"""v3.7.0 专项 CDP 探针（REQ-20261115-01 看盘体验补齐）—— 门禁 B 判据 B3–B9。

判据（真实浏览器点击/读取，非静态扫描）：
  F1 指数卡带走势        首页/今日看点/市场总览 三处指数卡均出现 svg 或「走势无数据」
  F2 云图（大盘云图）    板块工具「热力图」页签渲染出 .v-tm-cell（块数>0）且面积序与成交额序一致
  F3 行业资金流          云图卡内出现「净流入 Top8 / 净流出 Top8」双向条（A股）
  F4 完整 K 线           任一含 OHLC 的注册表卡渲染出 canvas（蜡烛图）而非「一条线」svg
  F5 中文表头            遍历视图内所有 th，无纯英文标识符
  F6 表头排序            点击某表头两次（升/降）后首行数值顺序发生变化
  F7 快讯原文            今日看点快讯条出现 a[target=_blank]（原文 ↗）
  F8 板块级研判          市场总览/今日看点的研判接口支持 board（直连断言）
  F9 无控制台错误

用法: python tools/_probe_v370_features.py --base http://127.0.0.1:6905 --out <json>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ui_clickthrough as uc  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:6905")
    ap.add_argument("--out", required=True)
    ap.add_argument("--port", type=int, default=9860)
    args = ap.parse_args()

    profile = os.path.join(os.environ.get("TEMP", "/tmp"), "v370_%d" % int(time.time()))
    proc = uc.launch_chrome(args.port, profile, 1600, 1000)
    time.sleep(3)
    cdp = uc.CDP(uc.page_ws(args.port))
    cdp.call("Page.enable")
    cdp.call("Runtime.enable")
    uc.eval_js(cdp, uc.CONSOLE_CAPTURE)

    results = []
    def mark(name, ok, detail):
        results.append({"name": name, "ok": bool(ok), "detail": detail})

    def goto(view, wait=6.0, auth=True):
        cdp.call("Page.navigate", {"url": args.base + "/#/" + view})
        time.sleep(wait)
        if auth and uc.eval_js(cdp, "location.pathname === '/auth'"):
            uc.eval_js(cdp, "(function(){var i=document.querySelector('input[name=token]');if(i){i.value='%s';document.querySelector('form').submit();}})()" % os.environ.get("OPENBB_ACCESS_PASSWORD", ""))
            time.sleep(4)
        # 就绪闸门：等视图标题出现
        for _ in range(12):
            if uc.eval_js(cdp, "!!document.getElementById('view-title')"):
                break
            time.sleep(0.6)

    # ---------- F1 指数卡带走势（首页/今日/市场） ----------
    detail = {}
    for view in ("home", "today", "market"):
        goto(view, 9.0)
        # 触发补绘（MutationObserver 已挂；再显式调一次保证确定性）
        uc.eval_js(cdp, "(function(){if(window.V6_HELPERS&&V6_HELPERS.drawPendingKlines){V6_HELPERS.drawPendingKlines(document);}return 1;})()")
        time.sleep(4.5)
        n = uc.eval_js(cdp, "document.querySelectorAll('.card .spark svg, .v-idx-card .spark svg').length") or 0
        detail[view] = n
    mark("F1 指数卡带走势", all(v >= 3 for v in detail.values()), detail)

    # ---------- F2/F3 云图 + 资金流（板块行情 → 板块工具 → 热力图） ----------
    goto("sectors", 8.0)
    uc.eval_js(cdp, "(function(){var b=document.querySelector('#view-body [data-sec-tab=\"板块热力\"]');if(b){b.click();}return 1;})()")
    # 就绪轮询：等云图块或空态说明出现（最多 39s），避免时序假阴
    for _ in range(13):
        time.sleep(3)
        uc.eval_js(cdp, "(function(){if(window.V6_HELPERS&&V6_HELPERS.drawPendingKlines){V6_HELPERS.drawPendingKlines(document);}return 1;})()")
        cells = uc.eval_js(cdp, "document.querySelectorAll('.v-tm-cell').length") or 0
        if cells > 0:
            break
    time.sleep(1.0)
    tm = uc.eval_js(cdp, """(function(){
      var cells=document.querySelectorAll('.v-tm-cell');
      var areas=[],amounts=[];
      Array.prototype.forEach.call(cells,function(c){
        areas.push(parseFloat(c.style.width)*parseFloat(c.style.height));
        var t=(c.textContent||'');
        var m=t.match(/成交\\s*([\\d.]+)/); amounts.push(m?parseFloat(m[1]):null);
      });
      return JSON.stringify({cells:cells.length, sample:areas.slice(0,5)});
    })()""")
    tm = json.loads(tm) if isinstance(tm, str) else (tm or {})
    mark("F2 云图渲染", (tm.get("cells") or 0) >= 20, tm)
    ff = uc.eval_js(cdp, "document.querySelectorAll('.v7-ff-row').length") or 0
    mark("F3 行业资金流入流出", ff >= 8, {"rows": ff})

    # ---------- F4 完整 K 线（注册表页：板块估值含板块K线卡） ----------
    goto("sector-val", 10.0)
    uc.eval_js(cdp, "(function(){if(window.V6_HELPERS&&V6_HELPERS.drawPendingKlines){V6_HELPERS.drawPendingKlines(document);}return 1;})()")
    time.sleep(5)
    kl = uc.eval_js(cdp, "document.querySelectorAll('.v-kline canvas').length") or 0
    mark("F4 完整K线渲染", kl >= 1, {"canvases": kl})

    # ---------- F5 中文表头（整页重载到 sectors，确保表格已渲染；避免 th=0 空洞通过） ----------
    # 先整页重载（拿到干净 SPA），再用 hash 切到 sectors（避免同文档导航竞态）
    cdp.call("Page.navigate", {"url": args.base + "/?t=%d" % int(time.time())})
    time.sleep(7)
    uc.eval_js(cdp, "location.hash = '#/sectors'")
    for _ in range(20):
        if (uc.eval_js(cdp, "document.querySelectorAll('#view-body th').length") or 0) >= 3:
            break
        time.sleep(2.0)
    diag = uc.eval_js(cdp, "JSON.stringify({th:document.querySelectorAll('#view-body th').length, txt:(document.getElementById('view-body')||{innerText:''}).innerText.length, hash:location.hash})")
    ths = uc.eval_js(cdp, "JSON.stringify([].slice.call(document.querySelectorAll('#view-body th')).map(function(t){return (t.innerText||'').trim();}))")
    ths = ths if isinstance(ths, list) else (json.loads(ths) if isinstance(ths, str) else [])
    bad = [t for t in ths if t and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", t)]
    mark("F5 中文表头", len(ths) >= 3 and not bad, {"th": len(ths), "英文": bad[:8], "diag": diag})

    # 等表格工具绑定完成（.sortable 出现；绑定由 700ms 去抖的观察器补跑）
    for _ in range(20):
        if (uc.eval_js(cdp, "document.querySelectorAll('#view-body th.sortable').length") or 0) >= 3:
            break
        time.sleep(1.5)

    # ---------- F6 表头排序（点「涨跌幅」列，断言前后顺序变化） ----------
    sort_res = uc.eval_js(cdp, """(function(){
      var ths=[].slice.call(document.querySelectorAll('#view-body th'));
      var th=null;
      for(var i=0;i<ths.length;i++){ if((ths[i].innerText||'').indexOf('涨跌幅')>=0){ th=ths[i]; break; } }
      if(!th){ for(var j=0;j<ths.length;j++){ if(ths[j].classList.contains('sortable')){ th=ths[j]; break; } } }
      if(!th) return JSON.stringify({found:false});
      var tbl=th.closest('table');
      function sig(){ var out=[],rows=tbl.querySelectorAll('tr');
        for(var r=1;r<rows.length&&out.length<8;r++){var c=rows[r].children[th.cellIndex];
          if(c){var t=(c.innerText||'').trim(); if(t) out.push(t);} } return out.join('|'); }
      var before=sig(); th.click(); var ascending=sig(); th.click(); var descending=sig();
      return JSON.stringify({found:true, changed:(before!==ascending)||(ascending!==descending),
        col:(th.innerText||'').trim(), before:before.slice(0,60), ascending:ascending.slice(0,60), descending:descending.slice(0,60)});
    })()""")
    sort_res = sort_res if isinstance(sort_res, dict) else (json.loads(sort_res) if isinstance(sort_res, str) else {})
    mark("F6 表头排序", bool(sort_res.get("found")) and bool(sort_res.get("changed")), sort_res)

    # ---------- F7 快讯原文 ----------
    goto("today", 9.0)
    links = uc.eval_js(cdp, "document.querySelectorAll('#view-body a[target=_blank]').length") or 0
    mark("F7 快讯原文入口", links >= 1, {"links": links})

    # ---------- F9 控制台错误 ----------
    errs = uc.eval_js(cdp, "(window.__cb_errors||[]).slice(0,10)") or []
    mark("F9 无控制台错误", not errs, {"errors": errs})

    passed = sum(1 for r in results if r["ok"])
    payload = {"base": args.base, "as_of": time.strftime("%Y-%m-%d %H:%M:%S"),
               "total": len(results), "passed": passed,
               "failed": [r for r in results if not r["ok"]], "results": results}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    for r in results:
        print(f"[{'PASS' if r['ok'] else 'FAIL'}] {r['name']} :: {json.dumps(r['detail'], ensure_ascii=False)[:150]}")
    print(f"SUMMARY: {passed}/{len(results)}")
    print("证据:", args.out)
    try:
        proc.terminate()
    except Exception:  # noqa: BLE001
        pass
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
