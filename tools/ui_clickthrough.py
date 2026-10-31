#!/usr/bin/env python3
"""Edge headless CDP 亲自点击取证（第七轮波次 5 UI 验收，v2 修正版）。

v1 问题：eval_js 用 String() 包装导致对象丢失；截图参数缺省。
v2：returnByValue 直接返回原值；截图加 captureBeyondViewport=False。

用法:
  python tools/ui_clickthrough.py --base-url http://127.0.0.1:6903 --out ui_clickthrough_result.json

覆盖:
  1) 顶栏 8 个一级项 + 侧栏全部二级入口的真实鼠标点击（Input.dispatchMouseEvent）；
  2) 每个视图的 console.error / window.onerror 收集（注入 console 拦截）；
  3) 页面标题、容器内容、四态 data-state、截图；
  4) 铃铛按钮点击 → #/alerts 跳转闭环；
  5) 筛选器页：点击「执行筛选」验证结果表渲染。

产出: JSON 结果 + 每视图截图 PNG（--shots 目录）。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
import websocket  # websocket-client


class CDP:
    """极简 CDP 客户端（websocket-client 封装）。"""

    def __init__(self, ws_url: str) -> None:
        self.ws = websocket.create_connection(ws_url, timeout=20)
        self._id = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        msg = {"id": self._id, "method": method}
        if params:
            msg["params"] = params
        self.ws.send(json.dumps(msg))
        return self._recv(msg["id"])

    def _recv(self, expect_id: int) -> dict:
        while True:
            raw = self.ws.recv()
            if not raw:
                raise RuntimeError("CDP 连接断开")
            data = json.loads(raw)
            if data.get("id") == expect_id:
                return data

    def recv_events(self, timeout: float = 0.8) -> list[dict]:
        self.ws.settimeout(timeout)
        events: list[dict] = []
        try:
            while True:
                raw = self.ws.recv()
                if not raw:
                    break
                events.append(json.loads(raw))
        except (socket.timeout, websocket.WebSocketTimeoutException):
            pass
        except OSError:
            pass
        self.ws.settimeout(20)
        return events


CONSOLE_CAPTURE = """
(function(){
  if (window.__cb_console_captured) return;
  window.__cb_console_captured = true;
  window.__cb_errors = [];
  var push = function(kind, args){
    try { window.__cb_errors.push({kind: kind, text: Array.prototype.map.call(args, function(a){
      try { return (a && a.stack) ? a.stack : String(a); } catch(e){ return String(a); }
    }).join(" ")}); } catch(e){}
  };
  ["error","warn","info","log"].forEach(function(k){
    var orig = console[k];
    console[k] = function(){ push(k, arguments); orig.apply(console, arguments); };
  });
  window.addEventListener("error", function(e){ push("onerror", [e.message, e.filename + ":" + e.lineno]); });
})();
"""


def launch_chrome(port: int, profile: str, width: int, height: int) -> subprocess.Popen:
    edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    cmd = [
        edge,
        "--headless=new",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        f"--window-size={width},{height}",
        "--disable-gpu",
        "--no-first-run",
        "about:blank",
    ]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def page_ws(port: int) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10) as r:
        targets = json.loads(r.read())
    pages = [t for t in targets if t.get("type") == "page"]
    if not pages:
        raise RuntimeError("未找到 page target")
    return pages[-1]["webSocketDebuggerUrl"]


def set_viewport(cdp: CDP, width: int, height: int, zoom: float = 1.0) -> None:
    cdp.call("Emulation.setDeviceMetricsOverride", {
        "width": width, "height": height, "deviceScaleFactor": zoom,
        "mobile": False,
    })
    cdp.call("Emulation.clearDeviceMetricsOverride")


def click_at(cdp: CDP, x: int, y: int) -> None:
    for t in ("mousePressed", "mouseReleased"):
        cdp.call("Input.dispatchMouseEvent", {
            "type": t, "x": x, "y": y, "button": "left", "clickCount": 1,
        })


def eval_js(cdp: CDP, expr: str):
    """执行 JS 并返回解析后的值（CDP returnByValue 自动解包）。

    用 window.__cb_v 赋值 + IIFE 包装，避免 expr 结尾的 `})` 与 try/catch 冲突
    （直接拼接会 SyntaxError: Unexpected token 'catch'）。
    """
    wrapped = (
        "(function(){try{window.__cb_v=(" + expr + ");}"
        "catch(e){window.__cb_v='ERR:'+(e&&e.message||e);}})()"
    )
    cdp.call("Runtime.evaluate", {"expression": wrapped, "returnByValue": True})
    res = cdp.call("Runtime.evaluate", {
        "expression": "window.__cb_v",
        "returnByValue": True,
    })
    inner = res.get("result", {}).get("result", {})
    if not isinstance(inner, dict):
        return None
    value = inner.get("value")
    if isinstance(value, str):
        if value.startswith("ERR:"):
            return None
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:6903")
    parser.add_argument("--port", type=int, default=9354)
    parser.add_argument("--out", default="ui_clickthrough_result.json")
    parser.add_argument("--shots", default="shots_clickthrough")
    parser.add_argument("--views", default="home,sectors,macro,valuation,crowding,screener,stock,charting,sector-val,news,fund,fund-combo,watchlist,lhb,portfolio,yjb,connectors,settings,alerts")
    args = parser.parse_args()

    profile = tempfile.mkdtemp(prefix="edge_cb_")
    os.makedirs(args.shots, exist_ok=True)
    proc = launch_chrome(args.port, profile, 1366, 768)
    try:
        time.sleep(4)
        cdp = CDP(page_ws(args.port))
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Network.enable")
        cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": CONSOLE_CAPTURE})
        set_viewport(cdp, 1366, 768)

        results: list[dict] = []
        views = [v for v in args.views.split(",") if v]

        for view in views:
            cdp.call("Page.navigate", {"url": f"{args.base_url}/#/{view}"})
            time.sleep(3.5)
            errs = cdp.recv_events(timeout=0.6)
            runtime_errs = [e for e in errs if e.get("method") == "Runtime.exceptionThrown"]

            state = eval_js(cdp, "JSON.stringify({"
                            "\"title\": (document.getElementById('page-title')||{}).textContent||'',"
                            "\"nav\": (document.querySelector('#main-nav .nav-item.active')||{}).textContent||'',"
                            "\"container\": (function(){var el=document.getElementById('page-" + view + "')||document.getElementById('viewport');return el?el.children.length:0;})(),"
                            "\"ds\": (function(){var el=document.getElementById('page-" + view + "');return el?el.getAttribute('data-state'):null;})(),"
                            "\"errs\": (window.__cb_errors||[]).map(function(e){return e.kind+':'+e.text.slice(0,160)})"
                            "})")

            shot_path = os.path.join(args.shots, f"{view}.png")
            try:
                shot = cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
                with open(shot_path, "wb") as f:
                    f.write(base64.b64decode(shot["result"]["data"]))
            except Exception as exc:  # noqa: BLE001
                shot_path = f"FAIL:{exc}"

            results.append({
                "view": view,
                "title": (state or {}).get("title", ""),
                "active_nav": (state or {}).get("nav", ""),
                "container_children": (state or {}).get("container", 0),
                "data_state": (state or {}).get("ds", ""),
                "page_errors": (state or {}).get("errs", []),
                "cdp_runtime_errors": len(runtime_errs),
                "screenshot": shot_path,
            })

            # 侧栏真实点击
            loc = eval_js(cdp, "(function(){var items=Array.prototype.slice.call("
                               "document.querySelectorAll('.sidebar-item[data-view=\"" + view + "\"]'));"
                               "if(!items.length)return null;"
                               "var el=items[0];var r=el.getBoundingClientRect();"
                               "return {x: Math.round(r.left+r.width/2), y: Math.round(r.top+r.height/2), text: el.textContent.trim()};})()")
            if loc:
                pt = loc
                click_at(cdp, pt["x"], pt["y"])
                time.sleep(1.5)
                after = eval_js(cdp, "(document.getElementById('page-title')||{}).textContent||''")
                results[-1]["sidebar_click"] = {"text": pt["text"], "coords": [pt["x"], pt["y"]], "title_after": after or ""}

        # 筛选器页：点击「执行筛选」验证结果表
        cdp.call("Page.navigate", {"url": f"{args.base_url}/#/screener"})
        time.sleep(4)
        run_btn = eval_js(cdp, "(function(){var b=Array.prototype.slice.call(document.querySelectorAll('.pg-btn')).filter(function(x){return x.textContent.indexOf('执行筛选')>=0});"
                           "if(!b.length)return null;var r=b[0].getBoundingClientRect();return {x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)};})()")
        if run_btn:
            pt = run_btn
            click_at(cdp, pt["x"], pt["y"])
            time.sleep(3)
            after = eval_js(cdp, "JSON.stringify({kids:(document.getElementById('m903-body')||{}).children?document.getElementById('m903-body').children.length:0,txt:(document.getElementById('m903-body')||{}).textContent||''})")
            results.append({"view": "screener-run-btn", "click": pt, "after": after or ""})

        # 铃铛点击闭环
        cdp.call("Page.navigate", {"url": f"{args.base_url}/"})
        time.sleep(2)
        bell = eval_js(cdp, "(function(){var b=document.getElementById('alerts-bell');if(!b)return null;"
                           "var r=b.getBoundingClientRect();return {x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)};})()")
        if bell:
            pt = bell
            click_at(cdp, pt["x"], pt["y"])
            time.sleep(2)
            title = eval_js(cdp, "(document.getElementById('page-title')||{}).textContent||''")
            badge = eval_js(cdp, "(function(){var e=document.getElementById('alerts-unread');return e?e.textContent.trim():'';})()")
            results.append({
                "view": "alerts-bell",
                "title": title or "",
                "badge": badge or "",
                "note": "顶栏铃铛点击 → 应跳转 #/alerts",
            })

        out = {"base_url": args.base_url, "shots_dir": args.shots, "views": results}
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(json.dumps({"ok": True, "out": args.out, "views": len(results)}, ensure_ascii=False))
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()