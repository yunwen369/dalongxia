# -*- coding: utf-8 -*-
"""全路由全量验证（CDP）：13 路由逐一加载，抓 JS 错误 + 四态 + 关键区块存在性。

产物：F:/workbuddy记录/OpenBB Terminal Pro/B_交付归档/03_测试报告/20261025_full/routes_full.json
"""
from __future__ import annotations

import base64
import json
import pathlib
import subprocess
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = pathlib.Path("F:/workbuddy记录/OpenBB Terminal Pro/B_交付归档/03_测试报告/20261025_full")
EDGE = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
PORT = 9370
BASE = "http://127.0.0.1:6916"

ROUTES = [
    ("home", "#/home", [(".pg-card", 1)]),
    ("dashboard", "#/dashboard", [("#dash-grid", 1)]),
    ("watchlist", "#/watchlist", [("#W-01", 1), ("#W-02", 1)]),
    ("charting", "#/charting", [("#ch-market", 1), ("#ch-symbol", 1)]),
    ("news", "#/news", [(".pg-card", 1)]),
    ("sectors", "#/sectors", [(".pg-card", 1)]),
    ("stock", "#/stock?symbol=600519&market=a", [("#stock-symbol", 1), (".pg-card", 3)]),
    ("sector-val", "#/sector-val?board=%E5%8D%8A%E5%AF%BC%E4%BD%93&market=cn", [(".pg-card", 1)]),
    ("lhb", "#/lhb", [(".pg-card", 1)]),
    ("fund", "#/fund?code=017811", [(".pg-card", 3)]),
    ("portfolio", "#/portfolio", [(".pg-card", 1)]),
    ("yjb", "#/yjb", [("#Y-01", 1), ("#Y-02", 1)]),
    ("connectors", "#/connectors", [("#C-01", 1), ("#C-03", 1), ("#C-04", 1)]),
    ("settings", "#/settings", [("#set-ai-body", 1), ("#set-cache-body", 1), ("#set-compliance-body", 1), ("#set-about-body", 1)]),
]


class CDP:
    """极简 CDP 客户端。"""

    def __init__(self, ws_url: str) -> None:
        import websocket
        self.ws = websocket.create_connection(ws_url, timeout=60)
        self.seq = 0

    def call(self, method, params=None):
        self.seq += 1
        self.ws.send(json.dumps({"id": self.seq, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.seq:
                return msg

    def js(self, expression):
        res = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        return (res.get("result") or {}).get("result", {}).get("value")

    def shot(self, name):
        res = self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        (OUT / name).write_bytes(base64.b64decode(res.get("result", {}).get("data", "")))

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def main() -> int:
    """逐路由验证。"""
    OUT.mkdir(parents=True, exist_ok=True)
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    log = open(OUT / "_routes_server.log", "ab")
    srv = subprocess.Popen([str(py), "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1",
                            "--port", "6916", "--log-level", "warning"], cwd=str(ROOT),
                           stdout=log, stderr=log, stdin=subprocess.DEVNULL)
    edge = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", f"--remote-debugging-port={PORT}",
                             f"--user-data-dir={OUT / '_rt_profile'}", "--window-size=1440,1000", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    result = {}
    try:
        for _ in range(40):
            time.sleep(1)
            try:
                urllib.request.urlopen(BASE + "/api/health", timeout=3).read()
                break
            except Exception:
                continue
        ws_url = None
        for _ in range(30):
            time.sleep(0.5)
            try:
                for item in json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=3).read()):
                    if item.get("type") == "page":
                        ws_url = item["webSocketDebuggerUrl"]
                        break
            except Exception:
                continue
            if ws_url:
                break
        assert ws_url
        cdp = CDP(ws_url)
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source":
            "window.__errs=[];window.addEventListener('error',function(e){window.__errs.push(String(e.message||e))});"
            "window.addEventListener('unhandledrejection',function(e){window.__errs.push('REJECT:'+String(e.reason||e).slice(0,120))});"})
        cdp.js("try{localStorage.setItem('openbb-theme','dark')}catch(e){}")
        for name, route, checks in ROUTES:
            cdp.call("Page.navigate", {"url": BASE + "/" + route})
            time.sleep(6)
            info = {
                "errors": cdp.js("window.__errs") or [],
                "undefined": cdp.js("(document.body.innerText.match(/undefined/g)||[]).length"),
                "cards": cdp.js("document.querySelectorAll('.pg-card').length"),
                "states": cdp.js("Array.prototype.slice.call(document.querySelectorAll('.pg-body[data-state]')).map(function(n){return n.getAttribute('data-state')})"),
                "badges": cdp.js("document.querySelectorAll('.badge-src').length"),
                "tables": cdp.js("document.querySelectorAll('.pg-table').length"),
            }
            for selector, minimum in checks:
                got = cdp.js(f"document.querySelectorAll('{selector}').length")
                info[f"has:{selector}"] = f"{got}>={minimum}"
            result[name] = info
            cdp.shot(f"route_{name}.png")
            flag = "OK" if (not info["errors"] and not info["undefined"] and all(
                str(v).startswith("True") or not k.startswith("has:") for k, v in info.items() if k.startswith("has:"))) else "** CHECK **"
            print(f"{name:<12} cards={info['cards']:<3} tables={info['tables']:<2} badges={info['badges']:<2} "
                  f"states={sorted(set(info['states']))} errors={len(info['errors'])} undef={info['undefined']} {flag}")
            for e in info["errors"][:3]:
                print("    ERR:", e[:120])
        cdp.close()
    finally:
        srv.terminate()
        edge.terminate()
        try:
            srv.wait(timeout=10)
        except Exception:
            srv.kill()
        try:
            edge.wait(timeout=10)
        except Exception:
            edge.kill()
    (OUT / "routes_full.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    bad = [k for k, v in result.items() if v["errors"] or v["undefined"]]
    print(f"\n路由 {len(result)} 个；有问题: {bad if bad else '无 ✓'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
