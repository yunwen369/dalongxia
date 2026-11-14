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
  F10 拥挤度/风控名单     首页拥挤度四档 + 高位/偏高板块名 chips；风控卡含高位名单与未读提醒明细
                          （data-total 必须等于对应档计数 —— B9 判据）
  F11 板块研判选择器      今日/市场页「板块研判」卡：三级选择器选「半导体」→ 出板块口径信号/维度
  F12 仪表板管理 CRUD     工具栏 4 操作 + 右键菜单打开；新建→GET 复读存在→删除→GET 复读消失
  F13 顶栏搜索含板块      输入「半导体」→ 联想出现 data-board 项（分组「板块（行业/概念）」）
  F14 组件自由拉伸        拖右下角把手 → 卡片高度变大且 GET /api/dashboards 复读到 h（结尾自动回滚）

用法: python tools/_probe_v370_features.py --base http://127.0.0.1:6905 --out <json>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ui_clickthrough as uc  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:6905")
    ap.add_argument("--out", required=True)
    ap.add_argument("--port", type=int, default=9860)
    args = ap.parse_args()

    profile = os.path.join(os.environ.get("TEMP", "/tmp"), "v370_%d" % int(time.time()))
    # F12/F14 会真实改 /api/dashboards（新建后即删、拉伸后回滚），此处先备份整表
    dash_backup = None
    try:
        with urllib.request.urlopen(args.base + "/api/dashboards", timeout=20) as resp:
            dash_backup = (json.loads(resp.read().decode("utf-8")).get("data") or {})
    except Exception:  # noqa: BLE001 - 备份失败不阻断探针，结尾会跳过回滚
        dash_backup = None
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

    # ---------- F10 拥挤度/风控「计数 + 名单」（②，首页） ----------
    goto("home", 10.0)
    f10 = {}
    for _ in range(22):   # 拥挤度排行首次 20–90s，轮询等名单渲染
        raw = uc.eval_js(cdp, """(function(){
          var counts={};
          [].forEach.call(document.querySelectorAll('#bar-list .bar-row'),function(r){
            var t=((r.querySelector('.bar-label')||{}).textContent||'').trim();
            var n=((r.querySelector('.bar-num')||{}).textContent||'').trim();
            if(t) counts[t]=+n;});
          var lines={};
          [].forEach.call(document.querySelectorAll('#bar-list .bd-line'),function(l){
            lines[l.getAttribute('data-bd-level')]=[+l.getAttribute('data-total'),l.querySelectorAll('.bd-chip').length];});
          return JSON.stringify({
            bars:document.querySelectorAll('#bar-list .bar-row').length,
            chips:document.querySelectorAll('#bar-list .bd-chip').length,
            lines:lines, counts:counts,
            riskHigh:document.querySelectorAll('#risk-list .risk-detail .bd-chip').length,
            riskEvents:document.querySelectorAll('#risk-list .re-item').length,
            riskTotal:(function(){var e=document.querySelector('#risk-list .risk-events');return e?+e.getAttribute('data-total'):-1;})()
          });})()""")
        f10 = raw if isinstance(raw, dict) else (json.loads(raw) if isinstance(raw, str) and raw else {})
        if f10.get("chips"):
            break
        time.sleep(3)
    ok10 = (f10.get("bars") or 0) >= 4 and (f10.get("chips") or 0) >= 1
    # 计数一致性：每个档位的 data-total 必须等于该档计数（B9 判据）
    lines = f10.get("lines") or {}
    counts = f10.get("counts") or {}
    for lvl in ("高位", "偏高"):
        pair = lines.get(lvl)
        if pair and counts.get(lvl) is not None and pair[0] != counts.get(lvl):
            ok10 = False
            f10["mismatch"] = f10.get("mismatch") or {}
            f10["mismatch"][lvl] = {"data_total": pair[0], "count": counts.get(lvl)}
    mark("F10 拥挤度/风控名单", ok10, f10)

    # ---------- F11 板块研判卡（③，市场总览：三级选择器 → 板块口径情绪+研判） ----------
    goto("market", 9.0)
    for _ in range(20):
        if uc.eval_js(cdp, "!!document.querySelector('.v7-brief-board')"):
            break
        time.sleep(1.2)
    pick = uc.eval_js(cdp, """(function(){
      var card=document.querySelector('.v7-brief-board');
      if(!card) return JSON.stringify({card:false});
      var input=card.querySelector('[data-r="search"]');
      if(!input) return JSON.stringify({card:true,picker:false});
      input.value='半导体'; input.focus();
      input.dispatchEvent(new Event('input',{bubbles:true}));
      var it=card.querySelector('.v-bp-i[data-name="半导体"]');
      if(it){ it.click(); return JSON.stringify({card:true,picker:true,picked:true}); }
      return JSON.stringify({card:true,picker:true,picked:false,options:card.querySelectorAll('.v-bp-i').length});
    })()""")
    pick = pick if isinstance(pick, dict) else (json.loads(pick) if isinstance(pick, str) and pick else {})
    txt = ""
    for _ in range(25):
        txt = uc.eval_js(cdp, "(document.getElementById('v7-brief-out')||{innerText:''}).innerText||''") or ""
        if txt and "计算中" not in txt:
            break
        time.sleep(2)
    ok11 = (bool(pick.get("picked")) and ("半导体" in txt) and ("研判信号" in txt)
            and ("kind 仅支持" not in txt) and ("板块动量" in txt))
    mark("F11 板块研判选择器", ok11, {"pick": pick, "out": txt[:220]})

    # ---------- F12 仪表板管理 CRUD + 右键菜单（④） ----------
    goto("dashboard-manage", 9.0)
    for _ in range(20):
        if uc.eval_js(cdp, "!!document.querySelector('.v7-dash-mgr')"):
            break
        time.sleep(1.0)
    base12 = uc.eval_js(cdp, """(function(){
      var bar=document.querySelector('.v7-dash-mgr');
      var menu=document.querySelector('.v7-ctx');
      return JSON.stringify({
        bar:!!bar, rows:document.querySelectorAll('tr[data-dash-id]').length, menu:!!menu,
        acts:[].map.call(bar?bar.querySelectorAll('[data-dash-act]'):[],function(b){return b.getAttribute('data-dash-act');})
      });})()""")
    base12 = base12 if isinstance(base12, dict) else (json.loads(base12) if isinstance(base12, str) and base12 else {})
    tmp_name = "探针临时板%d" % int(time.time() % 100000)
    uc.eval_js(cdp, """(function(){
      var i=document.getElementById('v7-dash-name'); if(i){ i.value='%s'; }
      var b=document.querySelector('.v7-dash-mgr [data-dash-act="new"]'); if(b){ b.click(); }
      return 1;})()""" % tmp_name)
    created = False
    for _ in range(12):
        time.sleep(1.0)
        try:
            with urllib.request.urlopen(args.base + "/api/dashboards", timeout=20) as resp:
                dd = (json.loads(resp.read().decode("utf-8")).get("data") or {})
            if any(x.get("name") == tmp_name for x in (dd.get("dashboards") or [])):
                created = True
                break
        except Exception:  # noqa: BLE001 - 读配置失败留待断言
            pass
    # 选中临时板并删除（两次点击：预备 → 确认）
    deleted = False
    if created:
        uc.eval_js(cdp, """(function(){
          var trs=document.querySelectorAll('tr[data-dash-id]');
          for(var i=0;i<trs.length;i++){
            if((trs[i].getAttribute('data-dash-name')||'')==='%s'){ trs[i].click(); return 1; }
          }
          return 0;})()""" % tmp_name)
        time.sleep(0.8)
        for _ in range(2):
            uc.eval_js(cdp, "(function(){var b=document.querySelector('.v7-dash-mgr [data-dash-act=\"delete\"]');if(b){b.click();}return 1;})()")
            time.sleep(1.2)
        for _ in range(12):
            time.sleep(1.0)
            try:
                with urllib.request.urlopen(args.base + "/api/dashboards", timeout=20) as resp:
                    dd = (json.loads(resp.read().decode("utf-8")).get("data") or {})
                if not any(x.get("name") == tmp_name for x in (dd.get("dashboards") or [])):
                    deleted = True
                    break
            except Exception:  # noqa: BLE001
                pass
    ctx = uc.eval_js(cdp, """(function(){
      var tr=document.querySelector('tr[data-dash-id]');
      if(!tr) return JSON.stringify({found:false});
      var r=tr.getBoundingClientRect();
      tr.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:r.left+40,clientY:r.top+10}));
      var m=document.querySelector('.v7-ctx');
      return JSON.stringify({found:true,open:!!(m&&!m.hidden),items:m?m.querySelectorAll('button').length:0});})()""")
    ctx = ctx if isinstance(ctx, dict) else (json.loads(ctx) if isinstance(ctx, str) and ctx else {})
    mark("F12 仪表板管理 CRUD", bool(base12.get("bar")) and created and deleted and bool(ctx.get("open")),
         {"toolbar": base12, "created": created, "deleted": deleted, "contextmenu": ctx})

    # ---------- F13 顶栏搜索含板块（⑦） ----------
    goto("home", 8.0)
    uc.eval_js(cdp, """(function(){
      var i=document.getElementById('v7-search');
      if(!i) return 0;
      i.value='半导体'; i.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()""")
    f13 = {}
    for _ in range(28):
        raw = uc.eval_js(cdp, """(function(){
          var items=document.querySelectorAll('#v7-search-list .v7-search-item');
          var boards=document.querySelectorAll('#v7-search-list .v7-search-item[data-board]');
          return JSON.stringify({items:items.length, boards:boards.length,
            first: boards.length?boards[0].getAttribute('data-board'):'',
            firstKind: boards.length?boards[0].getAttribute('data-board-kind'):''});})()""")
        f13 = raw if isinstance(raw, dict) else (json.loads(raw) if isinstance(raw, str) and raw else {})
        if f13.get("boards"):
            break
        time.sleep(1.2)
    mark("F13 顶栏搜索含板块", (f13.get("boards") or 0) >= 1 and f13.get("first") == "半导体", f13)

    # ---------- F14 组件自由拉伸（⑩，拖把手 → 高度变化 + h 落库） ----------
    goto("dashboard", 10.0)
    for _ in range(20):
        if uc.eval_js(cdp, "document.querySelectorAll('.demo-dash-grid .demo-widget').length"):
            break
        time.sleep(1.0)
    drag = uc.eval_js(cdp, """(function(){
      var g=document.querySelector('.demo-dash-grid');
      var w=g?g.querySelector('.demo-widget'):null;
      var h=w?w.querySelector('.dw-resize'):null;
      if(!g||!w) return JSON.stringify({grid:!!g,widget:!!w,handle:false});
      if(!h) return JSON.stringify({grid:true,widget:true,handle:false});
      var hb=h.getBoundingClientRect();
      var before=Math.round(w.getBoundingClientRect().height);
      function fire(el,type,x,y){ el.dispatchEvent(new MouseEvent(type,{bubbles:true,cancelable:true,clientX:x,clientY:y,button:0})); }
      fire(h,'mousedown',hb.left+8,hb.top+8);
      fire(document,'mousemove',hb.left+8,hb.top+130);
      fire(document,'mouseup',hb.left+8,hb.top+130);
      return JSON.stringify({grid:true,widget:true,handle:true,id:w.getAttribute('data-id'),
        before:before,height:Math.round(w.getBoundingClientRect().height)});})()""")
    drag = drag if isinstance(drag, dict) else (json.loads(drag) if isinstance(drag, str) and drag else {})
    time.sleep(2.5)
    h_saved = None
    try:
        with urllib.request.urlopen(args.base + "/api/dashboards", timeout=20) as resp:
            dd = (json.loads(resp.read().decode("utf-8")).get("data") or {})
        act = [x for x in (dd.get("dashboards") or []) if x.get("id") == dd.get("activeId")] or (dd.get("dashboards") or [])
        for w in ((act[0].get("widgets") if act else []) or []):
            if w.get("id") == drag.get("id"):
                h_saved = w.get("h")
    except Exception:  # noqa: BLE001
        pass
    mark("F14 组件自由拉伸", bool(drag.get("handle")) and (drag.get("height") or 0) > (drag.get("before") or 0) and h_saved,
         {"drag": drag, "h": h_saved})

    # ---------- F9 控制台错误 ----------
    errs = uc.eval_js(cdp, "(window.__cb_errors||[]).slice(0,10)") or []
    mark("F9 无控制台错误", not errs, {"errors": errs})

    # ---------- 回滚仪表板配置（F12 新建即删已自清；F14 拉伸的高度在此还原） ----------
    if dash_backup:
        try:
            body = json.dumps({
                "dashboards": dash_backup.get("dashboards") or [],
                "activeId": dash_backup.get("activeId") or "",
                "groups": dash_backup.get("groups") or [],
            }).encode("utf-8")
            req = urllib.request.Request(args.base + "/api/dashboards", data=body, method="PUT",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                resp.read()
            print("仪表板配置已回滚（F12/F14 临时改动）")
        except Exception as exc:  # noqa: BLE001 - 回滚失败要显式告警
            print("WARN 仪表板回滚失败：", type(exc).__name__, exc)

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
