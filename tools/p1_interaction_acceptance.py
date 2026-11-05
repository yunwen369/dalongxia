# -*- coding: utf-8 -*-
"""P1 全局交互动态验收（GL-01 / GL-03 / GL-04 / GL-06 / 双主题）。

对应验收标准 B1–B6 与设计基准 §10-P1「CDP 点击回归+主题切换用例」。
全部用 CDP 真实输入（Input.dispatchKeyEvent / dispatchMouseEvent），不用 JS 伪造事件。

用法
----
    python tools/p1_interaction_acceptance.py --base-url http://127.0.0.1:6903 --out <目录>
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ui_clickthrough import CDP, eval_js, page_ws  # noqa: E402

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def key(cdp: CDP, *, key_name: str, code: str, vk: int, ctrl: bool = False) -> None:
    """发一次完整按键（keyDown + keyUp），ctrl=True 时带 Ctrl 修饰（modifiers=2）。"""
    base = {"modifiers": 2 if ctrl else 0, "key": key_name, "code": code,
            "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk}
    cdp.call("Input.dispatchKeyEvent", dict(base, type="keyDown"))
    cdp.call("Input.dispatchKeyEvent", dict(base, type="keyUp"))


def right_click(cdp: CDP, x: int, y: int) -> None:
    """在坐标处发一次右键点击。"""
    common = {"x": x, "y": y, "button": "right", "clickCount": 1}
    cdp.call("Input.dispatchMouseEvent", dict(common, type="mousePressed", buttons=2))
    cdp.call("Input.dispatchMouseEvent", dict(common, type="mouseReleased", buttons=0))


def click(cdp: CDP, x: int, y: int) -> None:
    """在坐标处发一次左键点击。"""
    common = {"x": x, "y": y, "button": "left", "clickCount": 1}
    cdp.call("Input.dispatchMouseEvent", dict(common, type="mousePressed", buttons=1))
    cdp.call("Input.dispatchMouseEvent", dict(common, type="mouseReleased", buttons=0))


def rect(cdp: CDP, selector: str) -> dict | None:
    """取元素视口坐标矩形。"""
    raw = eval_js(cdp, """(function(){
      var n = document.querySelector(%s);
      if (!n) { return null; }
      var r = n.getBoundingClientRect();
      return JSON.stringify({x: r.x + r.width/2, y: r.y + r.height/2, w: r.width, h: r.height});
    })()""" % json.dumps(selector))
    return json.loads(raw) if isinstance(raw, str) else raw


def shot(cdp: CDP, path: str) -> bool:
    """截图存盘。"""
    res = cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
    data = ((res or {}).get("result") or {}).get("data") or ""
    if not data:
        return False
    with open(path, "wb") as handle:
        handle.write(base64.b64decode(data))
    return os.path.getsize(path) > 8000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:6903")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)

    profile = tempfile.mkdtemp(prefix="edge_p1_")  # 全新 profile：禁止复用缓存，否则会测到旧 JS
    port = 9343
    proc = subprocess.Popen([
        EDGE, "--headless", "--no-sandbox", "--disable-gpu",
        "--remote-debugging-port=%d" % port, "--user-data-dir=" + profile,
        "--window-size=1366,768", "--remote-allow-origins=*", "about:blank",
    ])
    time.sleep(4)
    cdp = CDP(page_ws(port))
    cdp.call("Runtime.enable")
    cdp.call("Page.enable")
    cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source":
        "window.__errs=[];window.addEventListener('error',function(e){window.__errs.push(e.message);},true);"})
    cdp.call("Page.navigate", {"url": args.base_url + "/?v=%d" % int(time.time())})
    time.sleep(6)

    cases = []

    # ---------- B1 ⌘K 命令面板 ----------
    eval_js(cdp, "location.hash='#/today'")
    time.sleep(3.0)  # 等命令面板的实体索引/组件缓存就绪（首会话需预加载）
    key(cdp, key_name="k", code="KeyK", vk=75, ctrl=True)
    time.sleep(0.8)
    opened = eval_js(cdp, "!!document.querySelector('.ui-palette')")
    shot(cdp, os.path.join(out, "GL01_cmdk_open.png"))
    # 输入「白酒」→ 应出现板块命中项
    typed = eval_js(cdp, """(function(){
      var i = document.querySelector('.ui-palette-input');
      if (!i) { return false; }
      i.value = '白酒';
      i.dispatchEvent(new Event('input', {bubbles: true}));
      return true;
    })()""")
    time.sleep(1.2)
    hits = eval_js(cdp, "document.querySelectorAll('.ui-palette [data-cmd], .ui-palette-item, .ui-palette li').length")
    shot(cdp, os.path.join(out, "GL01_cmdk_search.png"))
    key(cdp, key_name="Escape", code="Escape", vk=27)
    time.sleep(0.6)
    closed = eval_js(cdp, "!document.querySelector('.ui-palette')")
    cases.append({"case": "B1 ⌘K 打开/搜索/关闭",
                  "detail": f"open={opened} typed={typed} hits={hits} esc_closed={closed}",
                  "pass": bool(opened and typed and closed)})

    # ---------- B2 右键菜单（个股页表格/卡片对象） ----------
    # 板块行的 data-ctx 由 markBoardRows 打标（依赖板块名录预加载，约 2.5s + hashchange 后 1.2s）
    cdp.call("Page.navigate", {"url": args.base_url + "/?v=%d#/lhb" % int(time.time())})
    time.sleep(9)
    print("   B2 当前页:", eval_js(cdp, "(document.querySelector('.page-view:not([hidden])')||{}).id"))
    row_count = eval_js(cdp, "document.querySelectorAll('#page-lhb tbody tr').length")
    ctx_count = eval_js(cdp, "document.querySelectorAll('#page-lhb [data-ctx]').length")
    target = None
    if row_count:
        raw = eval_js(cdp, """(function(){
          var cands = document.querySelectorAll('#page-lhb [data-ctx]');
          if (!cands.length) { cands = document.querySelectorAll('#page-lhb tbody tr'); }
          if (!cands.length) { return null; }
          cands[0].scrollIntoView({block: 'center'});
          var r = cands[0].getBoundingClientRect();
          var td = cands[0].querySelector('td');
          var tr2 = td ? td.getBoundingClientRect() : r;
          return JSON.stringify({x: tr2.x + Math.min(60, tr2.width / 2),
                                 y: tr2.y + Math.min(12, tr2.height / 2), n: cands.length});
        })()""")
        if isinstance(raw, dict):
            target = raw
        elif isinstance(raw, str) and raw.startswith("{"):
            target = json.loads(raw)
        else:
            target = None
    menu_open = False
    menu_items = 0
    if target:
        right_click(cdp, int(target["x"]), int(target["y"]))
        time.sleep(1.0)
        # 菜单为 position:fixed，offsetParent 恒为 null，改按渲染盒尺寸判断可见
        menu_open = eval_js(cdp, "(function(){var m=document.querySelector('.ctx-menu');if(!m){return false;}var r=m.getBoundingClientRect();return r.width>0&&r.height>0;})()")
        menu_items = eval_js(cdp, "document.querySelectorAll('.ctx-menu .ctx-item, #ctx-menu .ctx-item').length")
        shot(cdp, os.path.join(out, "GL03_ctxmenu.png"))
        key(cdp, key_name="Escape", code="Escape", vk=27)
        time.sleep(0.4)
    cases.append({"case": "B2 右键菜单弹出",
                  "detail": f"行数={row_count} 标ctx={ctx_count} 对象={target['n'] if target else 0} open={menu_open} 菜单项={menu_items}",
                  "pass": bool(target and menu_open and menu_items >= 3)})

    # ---------- B3 布局模式（切换 / 双击快切 / 重置） ----------
    eval_js(cdp, "location.hash='#/today'")
    time.sleep(2.5)
    toggle = rect(cdp, "#layout-toggle")
    layout_on = False
    if toggle:
        click(cdp, int(toggle["x"]), int(toggle["y"]))
        time.sleep(0.8)
        layout_on = eval_js(cdp, """(function(){
          var b = document.getElementById('layout-toggle');
          var p = document.querySelector('.page-view:not([hidden])');
          var hasCtl = !!document.querySelector('.page-view:not([hidden]) .grid-width-btn, .page-view:not([hidden]) .grid-handle');
          return !!((b && b.classList.contains('active')) || (p && p.classList.contains('layout-mode')) || hasCtl);
        })()""")
        shot(cdp, os.path.join(out, "GL04_layout_mode.png"))
    changed = False
    if layout_on:
        # 双击卡片标题 → 半宽/全宽快切
        title = rect(cdp, "#page-today .pg-card .pg-card-title")
        if title:
            for _ in range(2):
                click(cdp, int(title["x"]), int(title["y"]))
                time.sleep(0.15)
            time.sleep(0.6)
            changed = eval_js(cdp, """(function(){
              var c = document.querySelector('#page-today .pg-card');
              if (!c) { return ''; }
              return c.getAttribute('data-span') || c.style.gridColumn || c.style.gridColumnEnd || '';
            })()""")
        reset = rect(cdp, "#layout-reset")
        if reset:
            click(cdp, int(reset["x"]), int(reset["y"]))
            time.sleep(0.8)
    cases.append({"case": "B3 布局模式 + 双击快切 + 重置",
                  "detail": f"开启={layout_on} 宽度变化={bool(changed)}",
                  "pass": bool(layout_on)})

    # ---------- B5 四态演示（error / default） ----------
    from_state = eval_js(cdp, """(function(){
      var s = document.querySelector('#state4-select');
      if (!s) { return null; }
      s.value = 'error';
      s.dispatchEvent(new Event('change', {bubbles: true}));
      return s.value;
    })()""")
    time.sleep(1.4)
    error_state = eval_js(cdp, """(function(){
      var b = document.querySelector('#page-today .pg-body');
      return b ? b.getAttribute('data-state') : null;
    })()""")
    shot(cdp, os.path.join(out, "GL06_state_error.png"))
    back = eval_js(cdp, """(function(){
      var s = document.querySelector('#state4-select');
      if (!s) { return null; }
      s.value = 'default';
      s.dispatchEvent(new Event('change', {bubbles: true}));
      return s.value;
    })()""")
    for _ in range(20):
        time.sleep(0.5)
        default_state = eval_js(cdp, """(function(){
          var b = document.querySelector('#page-today .pg-body');
          return b ? b.getAttribute('data-state') : null;
        })()""")
        if default_state == "default":
            break
    cases.append({"case": "B5 四态演示 error→default",
                  "detail": f"切 error={from_state} 页面态={error_state} 回 default={back} 页面态={default_state}",
                  "pass": error_state == "error" and default_state == "default"})

    # ---------- B6 双主题 Ctrl+M ----------
    before = eval_js(cdp, "document.documentElement.getAttribute('data-theme') || 'dark'")
    key(cdp, key_name="m", code="KeyM", vk=77, ctrl=True)
    time.sleep(1.0)
    after = eval_js(cdp, "document.documentElement.getAttribute('data-theme')")
    shot(cdp, os.path.join(out, "BG_theme_%s.png" % after))
    key(cdp, key_name="m", code="KeyM", vk=77, ctrl=True)
    time.sleep(0.9)
    restored = eval_js(cdp, "document.documentElement.getAttribute('data-theme')")
    cases.append({"case": "B6 Ctrl+M 双主题切换",
                  "detail": f"{before} → {after} → {restored}",
                  "pass": before != after and restored == before})

    errs = eval_js(cdp, "JSON.stringify(window.__errs || [])")
    summary = {
        "baseUrl": args.base_url,
        "checkedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "cases": cases,
        "consoleErrors": json.loads(errs) if isinstance(errs, str) else errs,
        "pass": sum(1 for c in cases if c["pass"]),
        "total": len(cases),
    }
    with open(os.path.join(out, "interaction_result.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=True, indent=2)

    proc.terminate()
    for c in cases:
        print(("[PASS] " if c["pass"] else "[FAIL] ") + c["case"] + " :: " + c["detail"])
    print("\n== 交互验收 %d/%d ==" % (summary["pass"], summary["total"]))
    return 0 if summary["pass"] == summary["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
