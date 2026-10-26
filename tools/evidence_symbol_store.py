# -*- coding: utf-8 -*-
"""R3-28/30 符号共享 store 取证（CDP）：跨页带代码闭环。

场景：
1. 个股页输入 000001 + 市场 hk → 提交
2. 无参进入 #/charting → ch-symbol 应回填 000001、ch-market 应回填 hk
3. 无参进入 #/stock → stock-symbol 应回填 000001
4. 行情页（mk）点击列表行 → 仪表板刷新用该标的；再进图表页带回同一标的

产物：B_交付归档/03_测试报告/20261025_clean/symbol_store_evidence.json + 2 张截图
"""

from __future__ import annotations

import base64
import json
import pathlib
import subprocess
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "B_交付归档" / "03_测试报告" / "20261025_clean"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PORT = 9340
BASE = "http://127.0.0.1:6911"


class CDP:
    """极简 CDP 客户端。"""

    def __init__(self, ws_url: str) -> None:
        """建立 WebSocket 连接。"""
        import websocket  # type: ignore

        self.ws = websocket.create_connection(ws_url, timeout=30)
        self.seq = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        """发送 CDP 命令。"""
        self.seq += 1
        self.ws.send(json.dumps({"id": self.seq, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.seq:
                return msg

    def js(self, expression: str):
        """执行 JS 并返回值。"""
        res = self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        })
        return (res.get("result") or {}).get("result", {}).get("value")

    def shot(self, name: str) -> None:
        """截图落盘。"""
        res = self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
        (OUT / name).write_bytes(base64.b64decode(res.get("result", {}).get("data", "")))
        print(f"  截图 {name}")

    def close(self) -> None:
        """关闭连接。"""
        try:
            self.ws.close()
        except Exception:
            pass


def main() -> int:
    """启动服务与 Edge，逐场景取证。

    Returns:
        int: 退出码。
    """
    OUT.mkdir(parents=True, exist_ok=True)
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    log = open(OUT / "_sym_server.log", "ab")
    srv = subprocess.Popen(
        [str(py), "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1",
         "--port", "6911", "--log-level", "warning"],
        cwd=str(ROOT), stdout=log, stderr=log, stdin=subprocess.DEVNULL,
    )
    edge = subprocess.Popen(
        [EDGE, "--headless=new", "--disable-gpu", f"--remote-debugging-port={PORT}",
         f"--user-data-dir={OUT / '_sym_profile'}", "--window-size=1440,1200", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    result: dict = {}
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
                for item in json.loads(urllib.request.urlopen(
                        f"http://127.0.0.1:{PORT}/json/list", timeout=3).read()):
                    if item.get("type") == "page":
                        ws_url = item["webSocketDebuggerUrl"]
                        break
            except Exception:
                continue
            if ws_url:
                break
        assert ws_url, "未取到 CDP 地址"

        cdp = CDP(ws_url)
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")

        # 场景 1：个股页改代码并提交（应写共享 store）
        print("[1] 个股页提交 → 写 store")
        cdp.js("localStorage.clear()")
        cdp.call("Page.navigate", {"url": BASE + "/#/stock?symbol=600519&market=a"})
        time.sleep(6)
        cdp.js(
            "(function(){var i=document.getElementById('stock-symbol');"
            "if(!i){return null;}i.value='000001';"
            "var m=document.getElementById('stock-market');if(m){m.value='hk';}"
            "return true;})()")
        time.sleep(0.5)
        cdp.js("(document.getElementById('stock-go')||{click:function(){}}).click()")
        time.sleep(5)
        result["stock_submit"] = {
            "hash": cdp.js("location.hash"),
            "store": cdp.js("window.SymbolStore ? window.SymbolStore.get() : null"),
        }
        cdp.shot("sym_01_stock_submit.png")

        # 场景 2：无参进图表页（应带出 000001/hk）
        print("[2] 无参进图表页 → 带代码")
        cdp.js("location.hash = '#/charting'")
        time.sleep(6)
        result["charting_carry"] = {
            "hash": cdp.js("location.hash"),
            "ch_symbol": cdp.js("(document.getElementById('ch-symbol')||{}).value"),
            "ch_market": cdp.js("(document.getElementById('ch-market')||{}).value"),
            "store": cdp.js("window.SymbolStore ? window.SymbolStore.get() : null"),
        }
        cdp.shot("sym_02_charting_carry.png")

        # 场景 3：无参进个股页（应带出 000001/hk）
        print("[3] 无参进个股页 → 带代码")
        cdp.js("location.hash = '#/stock'")
        time.sleep(6)
        result["stock_carry"] = {
            "stock_symbol": cdp.js("(document.getElementById('stock-symbol')||{}).value"),
            "stock_market": cdp.js("(document.getElementById('stock-market')||{}).value"),
            "hash": cdp.js("location.hash"),
        }

        # 场景 4：行情页 mk 面板空输入自动带出 + 图表跳转
        print("[4] 行情页 mk 面板 → 带出 + 跳转图表")
        cdp.js("location.hash = '#/home'")
        time.sleep(4)
        result["market_panel"] = {
            "mk_symbol": cdp.js("(document.getElementById('mk-symbol')||{}).value"),
            "mk_market": cdp.js("(document.getElementById('mk-market')||{}).value"),
        }
        # 手工改 mk 代码后点图表跳转（不经 refresh）
        cdp.js(
            "(function(){var i=document.getElementById('mk-symbol');"
            "if(i){i.value='688012';}var m=document.getElementById('mk-market');"
            "if(m){m.value='a';}var b=document.getElementById('mk-chart-link');"
            "if(b){b.click();}return true;})()")
        time.sleep(6)
        result["market_to_charting"] = {
            "hash": cdp.js("location.hash"),
            "ch_symbol": cdp.js("(document.getElementById('ch-symbol')||{}).value"),
            "store": cdp.js("window.SymbolStore ? window.SymbolStore.get() : null"),
        }
        cdp.shot("sym_03_market_jump.png")
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

    (OUT / "symbol_store_evidence.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
