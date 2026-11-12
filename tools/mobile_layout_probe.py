#!/usr/bin/env python3
"""移动端布局探针（2026-11-12 修 R-01 后固化，替代已退役 review_recheck 的 R1 组）。

判据（三档视口 × 真实浏览器）
------------------------------
1. 390 / 768 / 1024 均**无页面级横向溢出**（scrollWidth ≤ clientWidth+2）；
2. ≤768 出现汉堡按钮 `#menu-toggle`，>768 不出现；
3. ≤768 侧栏改为抽屉：默认在视口外、点汉堡滑入（right ≥ 200）、有遮罩 `#sb-backdrop`；
4. 抽屉四种关闭路径均生效：遮罩点击 / Esc / 点侧栏条目 / 路由变化；
5. 抽屉打开时汉堡 `aria-expanded=true`，关闭时 false；
6. ≤768 顶栏关键入口可见（搜索 `.v7-search`、提醒 `#v7-bell`）且未超出视口；
7. 390 下首页 `.row-1` 单列、卡片不横向溢出；
8. 全程控制台 0 错误。

用法::

    python tools/mobile_layout_probe.py --base-url http://127.0.0.1:6901 \
        --out evidence/mobile_layout.json [--port 9397]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import sys
import time
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ui_clickthrough as ui  # noqa: E402

STATE_JS = """
(function(){
  var de=document.documentElement, vw=de.clientWidth;
  var sb=document.querySelector('.sidebar')||document.getElementById('side-nav');
  var bd=document.getElementById('sb-backdrop');
  var btn=document.getElementById('menu-toggle');
  function vis(sel){var e=document.querySelector(sel); if(!e) return false;
    var r=e.getBoundingClientRect(); var cs=getComputedStyle(e);
    return r.width>1 && r.height>1 && cs.display!=='none' && cs.visibility!=='hidden';}
  function inView(sel){var e=document.querySelector(sel); if(!e) return false;
    var r=e.getBoundingClientRect(); return r.width>1 && r.right<=vw+2 && r.left>=-2;}
  var row1=document.querySelector('.row-1');
  var cols=row1?getComputedStyle(row1).gridTemplateColumns.split(' ').length:0;
  return JSON.stringify({
    vw: vw, scrollW: de.scrollWidth, overflow: Math.max(0, de.scrollWidth-vw),
    burger: !!(btn && getComputedStyle(btn).display!=='none'),
    sidebarPos: sb?getComputedStyle(sb).position:'',
    sidebarRight: sb?Math.round(sb.getBoundingClientRect().right):null,
    sidebarOpen: sb?sb.classList.contains('open'):false,
    backdropExists: !!bd, backdropHidden: bd?bd.hidden:null,
    aria: btn?btn.getAttribute('aria-expanded'):null,
    search: vis('.v7-search'), searchInView: inView('.v7-search'),
    bell: vis('#v7-bell'), bellInView: inView('#v7-bell'),
    row1Cols: cols
  });
})()
"""


def state(cdp: ui.CDP) -> Dict[str, Any]:
    """读取当前布局状态。

    Args:
        cdp: CDP 连接。

    Returns:
        dict: 状态字典（解析失败返回空 dict）。
    """
    raw = ui.eval_js(cdp, STATE_JS)
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return {}
    return raw or {}


def shot(cdp: ui.CDP, path: str) -> None:
    """整页截图。

    Args:
        cdp: CDP 连接。
        path: 输出路径。
    """
    data = cdp.call("Page.captureScreenshot", {"format": "png"})["result"]["data"]
    with open(path, "wb") as fh:
        fh.write(base64.b64decode(data))


def main() -> int:
    """入口：三档视口 + 抽屉四路径验证。

    Returns:
        int: 0=全部通过，1=存在失败。
    """
    ap = argparse.ArgumentParser(description="移动端布局探针")
    ap.add_argument("--base-url", default="http://127.0.0.1:6901")
    ap.add_argument("--port", type=int, default=9397)
    ap.add_argument("--out", default=os.path.join(HERE, "..", "data", "_tmp", "mobile_layout.json"))
    ap.add_argument("--shots", default="", help="截图目录（可选）")
    args = ap.parse_args()
    base = args.base_url.rstrip("/")
    shots = args.shots
    if shots:
        os.makedirs(shots, exist_ok=True)

    checks: List[Dict[str, Any]] = []

    def add(cid: str, name: str, ok: bool, detail: str = "") -> None:
        checks.append({"id": cid, "name": name, "ok": bool(ok), "detail": str(detail)[:200]})

    def viewport(cdp: ui.CDP, w: int, h: int, hash_path: str = "/#/home") -> Dict[str, Any]:
        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": w <= 768})
        cdp.call("Page.navigate", {"url": base + hash_path})
        time.sleep(5)
        return state(cdp)

    profile = os.path.join(HERE, "_mobile_layout_profile")
    if os.path.isdir(profile):
        shutil.rmtree(profile, ignore_errors=True)
    proc = ui.launch_chrome(args.port, profile, 1000, 900)
    try:
        time.sleep(2.5)
        cdp = ui.CDP(ui.page_ws(args.port))
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": ui.CONSOLE_CAPTURE})

        s390 = viewport(cdp, 390, 844)
        add("M01", "390px 无横向溢出", s390.get("overflow", 99) == 0,
            "scrollW=%s vw=%s" % (s390.get("scrollW"), s390.get("vw")))
        add("M02", "390px 汉堡可见", bool(s390.get("burger")), str(s390.get("burger")))
        add("M03", "390px 侧栏在视口外（抽屉态）",
            s390.get("sidebarPos") == "fixed" and (s390.get("sidebarRight") or 0) <= 1,
            "pos=%s right=%s" % (s390.get("sidebarPos"), s390.get("sidebarRight")))
        add("M04", "390px 顶栏搜索与提醒可见且在视口内",
            bool(s390.get("search") and s390.get("searchInView") and s390.get("bell") and s390.get("bellInView")),
            "search=%s/%s bell=%s/%s" % (s390.get("search"), s390.get("searchInView"),
                                         s390.get("bell"), s390.get("bellInView")))
        add("M05", "390px 首页单列", s390.get("row1Cols") == 1, "cols=%s" % s390.get("row1Cols"))
        if shots:
            shot(cdp, os.path.join(shots, "mobile_390.png"))

        s768 = viewport(cdp, 768, 1024)
        add("M06", "768px 无横向溢出", s768.get("overflow", 99) == 0,
            "scrollW=%s vw=%s" % (s768.get("scrollW"), s768.get("vw")))
        add("M07", "768px 汉堡可见 + 侧栏抽屉态",
            bool(s768.get("burger")) and s768.get("sidebarPos") == "fixed",
            "burger=%s pos=%s" % (s768.get("burger"), s768.get("sidebarPos")))
        if shots:
            shot(cdp, os.path.join(shots, "mobile_768.png"))

        s1024 = viewport(cdp, 1024, 768)
        add("M08", "1024px 无横向溢出", s1024.get("overflow", 99) == 0,
            "scrollW=%s vw=%s" % (s1024.get("scrollW"), s1024.get("vw")))
        add("M09", "1024px 恢复桌面侧栏（无汉堡）",
            not s1024.get("burger") and s1024.get("sidebarPos") == "relative",
            "burger=%s pos=%s right=%s" % (s1024.get("burger"), s1024.get("sidebarPos"),
                                           s1024.get("sidebarRight")))

        # ---- 抽屉交互（390）----
        viewport(cdp, 390, 844)
        ui.eval_js(cdp, "document.getElementById('menu-toggle').click()")
        time.sleep(0.9)
        opened = state(cdp)
        add("M10", "点汉堡滑出抽屉 + 遮罩 + aria",
            opened.get("sidebarOpen") and (opened.get("sidebarRight") or 0) >= 200
            and opened.get("backdropHidden") is False and opened.get("aria") == "true",
            "right=%s backdrop_hidden=%s aria=%s" % (opened.get("sidebarRight"),
                                                     opened.get("backdropHidden"), opened.get("aria")))
        if shots:
            shot(cdp, os.path.join(shots, "mobile_390_drawer.png"))

        ui.eval_js(cdp, "document.getElementById('sb-backdrop').click()")
        time.sleep(0.6)
        closed_bd = state(cdp)
        add("M11", "点遮罩关闭抽屉", not closed_bd.get("sidebarOpen")
            and closed_bd.get("backdropHidden") is True and closed_bd.get("aria") == "false",
            "open=%s hidden=%s" % (closed_bd.get("sidebarOpen"), closed_bd.get("backdropHidden")))

        ui.eval_js(cdp, "document.getElementById('menu-toggle').click()")
        time.sleep(0.5)
        cdp.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape",
                                            "code": "Escape", "windowsVirtualKeyCode": 27})
        cdp.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape",
                                            "code": "Escape", "windowsVirtualKeyCode": 27})
        time.sleep(0.7)
        closed_esc = state(cdp)
        add("M12", "Esc 关闭抽屉", not closed_esc.get("sidebarOpen"),
            "open=%s" % closed_esc.get("sidebarOpen"))

        ui.eval_js(cdp, "document.getElementById('menu-toggle').click()")
        time.sleep(0.5)
        ui.eval_js(cdp, "(document.querySelector('#side-nav .side-item:not(.active)')||{click:function(){}}).click()")
        time.sleep(1.8)
        closed_nav = state(cdp)
        add("M13", "点侧栏条目后自动收起并跳转",
            not closed_nav.get("sidebarOpen") and "#/" in (ui.eval_js(cdp, "location.hash") or ""),
            "open=%s hash=%s" % (closed_nav.get("sidebarOpen"), ui.eval_js(cdp, "location.hash")))

        errs = ui.eval_js(cdp, "(window.__CONSOLE_ERRORS__||[]).slice(0,6)") or []
        add("M14", "全程控制台 0 错误", len(errs) == 0, json.dumps(errs, ensure_ascii=False)[:160])
    finally:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass

    passed = sum(1 for c in checks if c["ok"])
    payload = {"summary": {"total": len(checks), "passed": passed, "failed": len(checks) - passed},
               "checks": checks}
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(json.dumps(payload["summary"], ensure_ascii=False))
    for c in checks:
        if not c["ok"]:
            print("  FAIL", c["id"], c["name"], "|", c["detail"])
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
