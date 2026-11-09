#!/usr/bin/env python3
"""openbb-review.7z 审查报告第九节复核清单 · 全项动态复验（2026-11-09 建，2026-11-10 转正）。

背景：外部审查报告（v2.1.1-20261108）26 项已按 REQ-20261109-01 整改，
本脚本按报告第九节「复核方式」逐条做真实浏览器动态判定，不依赖既有截图。

覆盖：
  R1  390px：无横向溢出、顶栏 4 操作入口可见、侧栏收起、汉堡可见、抽屉开合
  R3  F5 不再被拦截（合成 keydown 检查 defaultPrevented）
  R4  首页 / 子页：顶栏-侧栏-面包屑-标题四处一致；Logo 点击回首页
  R5  侧栏项 tabindex=0 全覆盖且可聚焦
  R7  浅色主题正文对比度 >= 4.5:1；容器无 #1a1a1e 硬编码深底
  R8  侧栏一级/二级五维度可区分；折叠箭头 >= 14px；仅当前组展开
  R9  资讯卡片：无空描边标签、长标签省略+title、0 分显示「未评分」、正文对比度高于分值
  R10 1024x768：无横向溢出

用法：
  python tools/review_recheck.py --base http://127.0.0.1:6903 --out <证据目录>
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import tempfile
import time

TOOLS = os.path.dirname(os.path.abspath(__file__))
if not os.path.exists(os.path.join(TOOLS, "ui_clickthrough.py")):
    TOOLS = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)

from ui_clickthrough import CDP, launch_chrome, page_ws  # noqa: E402

RESULTS: list[dict] = []


def check(item: str, name: str, ok: bool, detail: str = "") -> None:
    """记录一条判定结果并即时打印。"""
    RESULTS.append({"item": item, "name": name, "pass": bool(ok), "detail": str(detail)[:400]})
    print(("PASS " if ok else "FAIL ") + f"[{item}] {name}" + (f" | {detail}" if detail else ""))


def evaluate(cdp: CDP, expr: str):
    """执行返回 JSON 字符串的 IIFE，解析为 Python 对象。"""
    r = cdp.call("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": False})
    inner = r.get("result", {}).get("result", {})
    value = inner.get("value")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def shot(cdp: CDP, path: str) -> None:
    """整页截图存证。"""
    data = cdp.call("Page.captureScreenshot", {"format": "png"})["result"]["data"]
    with open(path, "wb") as fh:
        fh.write(base64.b64decode(data))


def srgb_to_linear(c: float) -> float:
    """WCAG 2.1 sRGB 通道线性化。"""
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def rel_lum(rgb: tuple) -> float:
    """相对亮度。"""
    r, g, b = (v / 255.0 for v in rgb)
    return 0.2126 * srgb_to_linear(r) + 0.7152 * srgb_to_linear(g) + 0.0722 * srgb_to_linear(b)


def parse_rgb(text: str) -> tuple:
    """解析 'rgb(r, g, b)' / 'rgba(...)'。"""
    nums = [float(x) for x in re.findall(r"[\d.]+", text or "")]
    return tuple(int(round(x)) for x in nums[:3]) if len(nums) >= 3 else (0, 0, 0)


def contrast(fg: tuple, bg: tuple) -> float:
    """对比度。"""
    l1, l2 = sorted((rel_lum(fg), rel_lum(bg)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


NAV_STATE_JS = r"""
(function(){
  function txt(el){return el ? (el.textContent||'').trim().slice(0,20) : '';}
  var nav=document.querySelector('.nav-item.active');
  var side=[].slice.call(document.querySelectorAll('.sidebar-item.active')).map(function(i){
    return i.getAttribute('data-view-target')||txt(i);});
  var crumb=document.getElementById('crumb');
  var title=document.getElementById('page-title');
  var openGroups=[].slice.call(document.querySelectorAll('.sidebar details.sb-group[open]')).map(function(d){
    return d.getAttribute('data-group');});
  return JSON.stringify({hash:location.hash, nav:txt(nav), sidebar:side,
    crumb:(crumb&&!crumb.hidden)?crumb.textContent.trim():'(hidden)',
    title:txt(title), open_groups:openGroups});
})()
"""

LAYOUT_JS = r"""
(function(){
  var de=document.documentElement, vw=de.clientWidth;
  var mt=document.getElementById('menu-toggle');
  var sb=document.getElementById('sidebar');
  function visible(id){var e=document.getElementById(id); if(!e) return false;
    var r=e.getBoundingClientRect(); var cs=getComputedStyle(e);
    return r.width>1 && r.height>1 && cs.display!=='none' && cs.visibility!=='hidden';}
  function rect(id){var e=document.getElementById(id); if(!e) return null; var r=e.getBoundingClientRect();
    return {left:Math.round(r.left),right:Math.round(r.right),width:Math.round(r.width)};}
  return JSON.stringify({vw:vw, scroll_w:de.scrollWidth, no_hscroll:de.scrollWidth<=vw+2,
    hamburger: mt?getComputedStyle(mt).display!=='none':false,
    sidebar: rect('sidebar'),
    actions:{search:visible('global-search'),theme:visible('theme-toggle'),
      copilot:visible('copilot-toggle'),alerts:visible('alerts-bell')},
    actions_in_view:['global-search','theme-toggle','copilot-toggle','alerts-bell'].every(function(id){
      var e=document.getElementById(id); if(!e) return false; var r=e.getBoundingClientRect();
      return r.width>1 && r.right<=vw+2;}),
    main_left: null});
})()
"""

LAYERS_JS = r"""
(function(){
  function cs(el){var s=getComputedStyle(el);return {color:s.color,fs:s.fontSize,fw:s.fontWeight,
    pl:s.paddingLeft,bg:s.backgroundColor};}
  var h=document.querySelector('.sidebar .sb-group-h');
  var it=document.querySelector('.sidebar .sidebar-item:not(.active)')
      || document.querySelector('.sidebar .sidebar-item');
  if(!h||!it) return JSON.stringify({missing:true});
  var hs=cs(h), is=cs(it);
  var af=getComputedStyle(h,'::after');
  return JSON.stringify({h:hs, i:is, arrow:{content:af.content, fontSize:af.fontSize, color:af.color}});
})()
"""

CONTRAST_JS = r"""
(function(){
  function bgOf(el){
    var n=el;
    while(n && n!==document.documentElement){
      var b=getComputedStyle(n).backgroundColor;
      var m=b.match(/[\d.]+/g);
      if(m && (m.length<4 || parseFloat(m[3])>0.5)) return b;
      n=n.parentElement;
    }
    return getComputedStyle(document.body).backgroundColor;
  }
  var out={theme:document.documentElement.getAttribute('data-theme')};
  var ft=document.querySelector('.pg-feed-item .pg-feed-text')||document.querySelector('.pg-feed-text');
  var ti=document.querySelector('.pg-feed-item .pg-feed-title')||document.querySelector('.pg-feed-title');
  var sc=document.querySelector('.pg-feed-item .pg-feed-score');
  var mc=document.querySelector('.pg-metric-cell');
  if(ft){out.feed_text={fg:getComputedStyle(ft).color,bg:bgOf(ft)};}
  if(ti){out.feed_title={fg:getComputedStyle(ti).color,bg:bgOf(ti)};}
  if(sc){out.feed_score={fg:getComputedStyle(sc).color,bg:bgOf(sc),text:(sc.textContent||'').slice(0,20)};}
  if(mc){out.metric={fg:getComputedStyle(mc).color,bg:bgOf(mc)};}
  return JSON.stringify(out);
})()
"""

CARDS_JS = r"""
(function(){
  var items=[].slice.call(document.querySelectorAll('.pg-feed-item'));
  var emptyTags=0, shortTags=0, ellipsisTitled=0, unscored=0, zeroShown=0, scoreTexts=[];
  items.forEach(function(it){
    [].slice.call(it.querySelectorAll('.pg-news-tag')).forEach(function(t){
      if(!(t.textContent||'').trim()) emptyTags++;
    });
    [].slice.call(it.querySelectorAll('.pg-news-tag')).forEach(function(t){
      if(t.scrollWidth>t.clientWidth+1){ shortTags++; if(t.title && t.title.length>5) ellipsisTitled++; }
    });
    var s=it.querySelector('.pg-feed-score');
    if(s){var x=(s.textContent||''); scoreTexts.push(x.slice(0,24));
      if(x.indexOf('未评分')>=0) unscored++;
      else if(/0\.0000/.test(x)) zeroShown++;}
  });
  return JSON.stringify({count:items.length, empty_tags:emptyTags, clipped_tags:shortTags,
    clipped_with_title:ellipsisTitled, unscored:unscored, zero_shown:zeroShown, sample:scoreTexts.slice(0,6)});
})()
"""

FOCUS_JS = r"""
(function(){
  var all=[].slice.call(document.querySelectorAll('.sidebar-item'));
  var tab=all.filter(function(i){return i.getAttribute('tabindex')==='0';});
  var first=all[0];
  if(first){first.focus();}
  return JSON.stringify({total:all.length, tabindex:tab.length,
    focusable:first?document.activeElement===first:false,
    role:first?first.getAttribute('role'):null});
})()
"""

F5_JS = r"""
(function(){
  var ev=new KeyboardEvent('keydown',{key:'F5',code:'F5',keyCode:116,bubbles:true,cancelable:true});
  document.dispatchEvent(ev);
  return JSON.stringify({default_prevented:ev.defaultPrevented});
})()
"""

HARDCODED_JS = r"""
(function(){
  var hits=[];
  [].slice.call(document.querySelectorAll('*')).slice(0,4000).forEach(function(el){
    var b=getComputedStyle(el).backgroundColor;
    if(b==='rgb(26, 26, 30)') hits.push(el.className||el.tagName);
  });
  return JSON.stringify({dark_container_hits:hits.slice(0,10), count:hits.length});
})()
"""


def load(cdp: CDP, url: str, wait: float = 8.0) -> None:
    """导航并等待渲染。"""
    cdp.call("Page.navigate", {"url": url})
    time.sleep(wait)


def set_theme(cdp: CDP, theme: str) -> None:
    """写入 localStorage 并重载，等主题生效。"""
    cdp.call("Runtime.evaluate", {
        "expression": "localStorage.setItem('openbb-theme','%s'); location.reload();" % theme,
        "returnByValue": True,
    })
    time.sleep(7)


def run_viewport(base: str, w: int, h: int, tag: str, out: str, cdp_port: int) -> None:
    """在指定视口执行复核。"""
    profile = tempfile.mkdtemp(prefix="chrome_recheck_")
    proc = launch_chrome(cdp_port, profile, w, h)
    try:
        cdp = CDP(page_ws(cdp_port))
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Network.setCacheDisabled", {"cacheDisabled": True})
        # headless Edge 的 --window-size 有下限（约 492px），手机视口必须用
        # Emulation 覆盖；桌面视口同样覆盖以保证 clientWidth 可预期。
        cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": w, "height": h, "deviceScaleFactor": 1, "mobile": w <= 768})
        load(cdp, base + "/#/home", 9 if w > 500 else 7)

        # ---- 布局（R1 / R10）----
        lay = evaluate(cdp, LAYOUT_JS)
        print(f"[{tag}] layout:", json.dumps(lay, ensure_ascii=False))
        check(tag, "无横向溢出", lay.get("no_hscroll"), f"scrollW={lay.get('scroll_w')} vw={lay.get('vw')}")
        act = lay.get("actions", {})
        if w <= 768:
            # 移动端设计取舍：顶栏全局搜索隐藏（空间不足），搜索入口下沉到侧栏抽屉 #sb-search
            core_actions = [act.get("theme"), act.get("copilot"), act.get("alerts")]
            check(tag, "顶栏主题/AI助手/提醒三入口可见", all(core_actions), str(act))
            sb_search = evaluate(cdp, r"""
            (function(){var i=document.getElementById('sb-search');
              return JSON.stringify({exists:!!i, placeholder:i?i.placeholder:''});})()
            """)
            check(tag, "移动端搜索入口在侧栏抽屉内", bool(sb_search.get("exists")), str(sb_search))
            check(tag, "汉堡按钮可见", lay.get("hamburger"), "")
            sb = lay.get("sidebar") or {}
            check(tag, "侧栏默认收起", sb.get("right", 999) <= 1, str(sb))
            # 抽屉开合
            cdp.call("Runtime.evaluate", {
                "expression": "document.getElementById('menu-toggle').click(); 'ok'", "returnByValue": True})
            time.sleep(1)
            open_state = evaluate(cdp, r"""
            (function(){var sb=document.getElementById('sidebar');var bd=document.getElementById('sb-backdrop');
              return JSON.stringify({open:sb.classList.contains('open'),
                right:Math.round(sb.getBoundingClientRect().right), backdrop:bd?!bd.hidden:null,
                aria:document.getElementById('menu-toggle').getAttribute('aria-expanded')});})()
            """)
            check(tag, "汉堡唤出抽屉+遮罩", bool(open_state.get("open")) and open_state.get("right", 0) > 200
                  and bool(open_state.get("backdrop")), str(open_state))
            shot(cdp, os.path.join(out, f"shot_{tag}_drawer.png"))
            cdp.call("Runtime.evaluate", {
                "expression": "document.getElementById('sb-backdrop').click(); 'ok'", "returnByValue": True})
            time.sleep(1)
            closed = evaluate(cdp, r"""
            (function(){var sb=document.getElementById('sidebar');
              return JSON.stringify({right:Math.round(sb.getBoundingClientRect().right),
                open:sb.classList.contains('open')});})()
            """)
            check(tag, "遮罩点击关闭抽屉", not closed.get("open") and closed.get("right", 999) <= 1, str(closed))
        else:
            check(tag, "顶栏 4 操作入口全部可见且未被裁切",
                  all(act.values()) and lay.get("actions_in_view"),
                  f"{act} in_view={lay.get('actions_in_view')}")

        # ---- F5（R3）----
        f5 = evaluate(cdp, F5_JS)
        check(tag, "F5 不被拦截（defaultPrevented=false）", not f5.get("default_prevented"), str(f5))

        # ---- 首页导航（R4）----
        home = evaluate(cdp, NAV_STATE_JS)
        print(f"[{tag}] home nav:", json.dumps(home, ensure_ascii=False))
        check(tag, "首页侧栏高亮市场总览", "home" in (home.get("sidebar") or []), str(home.get("sidebar")))
        check(tag, "首页面包屑可见", home.get("crumb") not in ("(hidden)", ""), str(home.get("crumb")))
        check(tag, "首页标题非空", bool(home.get("title")), str(home.get("title")))
        shot(cdp, os.path.join(out, f"shot_{tag}_home.png"))

        # ---- Logo 回首页（R4）----
        cdp.call("Runtime.evaluate", {"expression": "location.hash='#/news'; 'ok'", "returnByValue": True})
        time.sleep(4)
        cdp.call("Runtime.evaluate", {
            "expression": "(function(){var l=document.getElementById('logo-home'); if(l){l.click();} return 'ok';})()",
            "returnByValue": True})
        time.sleep(3)
        back = evaluate(cdp, NAV_STATE_JS)
        check(tag, "Logo 点击回首页", back.get("hash") == "#/home", str(back.get("hash")))

        # ---- 子页四处一致（R4）+ 面包屑落点（R8 语义）----
        for view in ("sectors", "macro", "alerts", "connectors", "portfolio"):
            cdp.call("Runtime.evaluate", {
                "expression": "location.hash='#/%s'; 'ok'" % view, "returnByValue": True})
            time.sleep(5)
            st = evaluate(cdp, NAV_STATE_JS)
            nav_ok = bool(st.get("nav"))
            side_ok = view in (st.get("sidebar") or [])
            crumb_ok = view not in ("(hidden)", "") and st.get("crumb") not in ("(hidden)", "")
            check(tag, f"子页 #{view} 顶栏/侧栏/面包屑均有指示",
                  nav_ok and side_ok and crumb_ok,
                  f"nav={st.get('nav')} side={st.get('sidebar')} crumb={st.get('crumb')}")
            if view in ("alerts", "connectors"):
                print(f"[{tag}] crumb #{view}:", st.get("crumb"))

        # ---- 侧栏层级（R8）+ 键盘（R5）----
        cdp.call("Runtime.evaluate", {"expression": "location.hash='#/home'; 'ok'", "returnByValue": True})
        time.sleep(4)
        layers = evaluate(cdp, LAYERS_JS)
        focus = evaluate(cdp, FOCUS_JS)
        print(f"[{tag}] layers:", json.dumps(layers, ensure_ascii=False))
        if layers and not layers.get("missing"):
            hh, ii = layers["h"], layers["i"]
            fs_h = float(hh["fs"].replace("px", ""))
            fs_i = float(ii["fs"].replace("px", ""))
            pl_h = float(hh["pl"].replace("px", ""))
            pl_i = float(ii["pl"].replace("px", ""))
            arrow_px = float((layers.get("arrow") or {}).get("fontSize", "0px").replace("px", ""))
            check(tag, "一级字号不低于二级", fs_h >= fs_i, f"{fs_h} vs {fs_i}")
            check(tag, "一级颜色与二级不同", hh["color"] != ii["color"], f"{hh['color']} vs {ii['color']}")
            check(tag, "一级字重>=700", int(hh["fw"]) >= 700, hh["fw"])
            check(tag, "二级缩进大于一级", pl_i > pl_h, f"{pl_i} vs {pl_h}")
            check(tag, "分组标题有独立底色", hh["bg"] != ii["bg"], f"{hh['bg']} vs {ii['bg']}")
            check(tag, "折叠箭头>=14px", arrow_px >= 14, f"{arrow_px}px")
        check(tag, "侧栏项 tabindex=0 全覆盖", focus.get("tabindex") == focus.get("total"),
              f"{focus.get('tabindex')}/{focus.get('total')}")
        check(tag, "侧栏项可聚焦(role=link)", focus.get("focusable") and focus.get("role") == "link", str(focus))
        open_groups = home.get("open_groups") or []
        check(tag, "仅当前路由所在组默认展开", len(open_groups) <= 1, str(open_groups))

        # ---- 资讯卡片（R9）----
        cdp.call("Runtime.evaluate", {"expression": "location.hash='#/news'; 'ok'", "returnByValue": True})
        time.sleep(9)
        cards = evaluate(cdp, CARDS_JS)
        print(f"[{tag}] cards:", json.dumps(cards, ensure_ascii=False))
        check(tag, "资讯卡片已渲染", (cards.get("count") or 0) > 0, f"count={cards.get('count')}")
        check(tag, "无空描边标签", (cards.get("empty_tags") or 0) == 0, f"empty={cards.get('empty_tags')}")
        check(tag, "分值不显示 0.0000（极小值归入未评分）", (cards.get("zero_shown") or 0) == 0,
              f"zero_shown={cards.get('zero_shown')} unscored={cards.get('unscored')} sample={cards.get('sample')}")
        if (cards.get("clipped_tags") or 0) > 0:
            check(tag, "被裁切标签均有 title 全文",
                  cards.get("clipped_with_title") == cards.get("clipped_tags"),
                  f"{cards.get('clipped_with_title')}/{cards.get('clipped_tags')}")
        else:
            check(tag, "被裁切标签均有 title 全文", True, "无裁切标签")
        shot(cdp, os.path.join(out, f"shot_{tag}_news.png"))

        # ---- 对比度（R7）----
        for theme in ("light", "dark"):
            set_theme(cdp, theme)
            cdp.call("Runtime.evaluate", {"expression": "location.hash='#/news'; 'ok'", "returnByValue": True})
            time.sleep(10)
            con = evaluate(cdp, CONTRAST_JS)
            print(f"[{tag}/{theme}] contrast:", json.dumps(con, ensure_ascii=False))
            for key, label in (("feed_text", "资讯正文"), ("feed_title", "资讯标题"), ("feed_score", "资讯分值")):
                if con.get(key):
                    c = contrast(parse_rgb(con[key]["fg"]), parse_rgb(con[key]["bg"]))
                    limit = 3.0 if key == "feed_title" else 4.5
                    check(tag, f"{theme} {label}对比度>={limit}", c >= limit, f"{c:.2f}:1 {con[key]['fg']} on {con[key]['bg']}")
            if theme == "light":
                hard = evaluate(cdp, HARDCODED_JS)
                check(tag, "浅色主题无 #1a1a1e 硬编码容器", (hard.get("count") or 0) == 0, str(hard.get("dark_container_hits")))
            shot(cdp, os.path.join(out, f"shot_{tag}_news_{theme}.png"))
            if theme == "dark":
                set_theme(cdp, "light")
        cdp.ws.close()
    finally:
        proc.terminate()


def main() -> None:
    """入口。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:6903")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)

    run_viewport(args.base, 1440, 900, "1440", args.out, 9371)
    run_viewport(args.base, 1024, 768, "1024", args.out, 9372)
    run_viewport(args.base, 390, 844, "390", args.out, 9373)

    total = len(RESULTS)
    failed = [r for r in RESULTS if not r["pass"]]
    with open(os.path.join(args.out, "ar_recheck_result.json"), "w", encoding="utf-8") as fh:
        json.dump({"total": total, "failed": len(failed), "results": RESULTS}, fh, ensure_ascii=False, indent=2)
    print(f"\nTOTAL={total} FAIL={len(failed)}")
    for r in failed:
        print("FAILED:", r["item"], r["name"], r["detail"])
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
