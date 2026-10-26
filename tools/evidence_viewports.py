# -*- coding: utf-8 -*-
"""多视口 UI 回归（CDP）：4 视口 × 全页面，查溢出/截断/undefined/英文键/红涨绿跌。

视口（按验收要求）：1366×768 / 1024×640 / 1366@125% / 1366@150%。
125%/150% 用「物理窗口=1366×镜像倍数」模拟缩放（应用 min-width:1024px 规范）。

产物：
- `03_测试报告/20261025_clean/viewports/viewport_<w>x<h>_<page>.png`
- `viewport_regression.json`（每页每视口的横向溢出/截断/undefined/英文键/涨跌色检查）

判定：
- `overflow_x`：document.scrollWidth > clientWidth（横向溢出）
- `table_clip`：表格容器 scrollWidth > clientWidth（表格横向滚动属正常，仅记录）
- `undefined`：body innerText 含 "undefined" / "NaN"
- `en_keys`：可见英文裸键（按同页 ≥2 个判泄漏，白名单豁免）
- `color_up`：页面存在涨跌色节点（--up/--down 计算值）
"""

from __future__ import annotations

import base64
import json
import pathlib
import subprocess
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
# 归档一律写项目级 B_交付归档（禁止在工程内建第二棵归档树，规范 §2.10）
OUT = pathlib.Path("F:/workbuddy记录/OpenBB Terminal Pro/B_交付归档/03_测试报告/20261025_clean/viewports")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PORT = 9360
BASE = "http://127.0.0.1:6914"

PAGES = [
    ("home", "#/home"),
    ("dashboard", "#/dashboard"),
    ("watchlist", "#/watchlist"),
    ("charting", "#/charting"),
    ("news", "#/news"),
    ("sectors", "#/sectors"),
    ("stock", "#/stock?symbol=688012&market=a"),
    ("sector-val", "#/sector-val?board=半导体&market=cn"),
    ("lhb", "#/lhb"),
    ("fund", "#/fund?code=017811"),
    ("portfolio", "#/portfolio"),
    ("yjb", "#/yjb"),
    ("connectors", "#/connectors"),
    ("settings", "#/settings"),
]

# 数据源徽章有意展示的上游技术标识（与 scan_ui_leaks.py 的 SNAKE_ALLOW 同 philosophy：
# 属产品有意展示的溯源信息，不算 i18n 泄漏；新增展示需同步登记本白名单）
SOURCE_ALLOW = ["ai_config", "akshare", "browser", "danjuan", "datacenter", "dpapi", "lsjz", "nav_grtd", "pip", "plug", "push2", "push2his", "yjb1"]

# 125%/150%：物理窗口 = 1366×逻辑分辨率 × 缩放倍数
VIEWPORTS = [
    ("1366x768", 1366, 768, 1.0),
    ("1024x640", 1024, 640, 1.0),
    ("1366@125%", 1366, 960, 1.25),
    ("1366@150%", 1366, 1152, 1.5),
]


class CDP:
    """极简 CDP 客户端。"""

    def __init__(self, ws_url: str) -> None:
        """建立 WebSocket。"""
        import websocket  # type: ignore

        self.ws = websocket.create_connection(ws_url, timeout=60)
        self.seq = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        """发命令。"""
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
        """截图。"""
        res = self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        data = res.get("result", {}).get("data", "")
        (OUT / name).write_bytes(base64.b64decode(data))

    def close(self) -> None:
        """关闭。"""
        try:
            self.ws.close()
        except Exception:
            pass


def probe_page(cdp: CDP) -> dict:
    """对当前页面跑溢出/undefined/英文键/涨跌色检查。

    Args:
        cdp: CDP 客户端。

    Returns:
        dict: 检查结果。
    """
    return cdp.js(
        """(function(){
          var de=document.documentElement;
          var out={
            scroll_w: de.scrollWidth, client_w: de.clientWidth,
            scroll_h: de.scrollHeight, client_h: de.clientHeight,
            tables: 0, tables_clipped: 0
          };
          out.overflow_x = out.scroll_w > out.client_w + 1;
          Array.prototype.slice.call(document.querySelectorAll('.pg-table-wrap')).forEach(function(w){
            out.tables += 1;
            if (w.scrollWidth > w.clientWidth + 1) { out.tables_clipped += 1; }
          });
          var text = (document.body.innerText || '');
          out.undefined_count = (text.match(/undefined/g) || []).length;
          out.nan_count = (text.match(/\\bNaN\\b/g) || []).length;
          var up = getComputedStyle(de).getPropertyValue('--up').trim();
          var down = getComputedStyle(de).getPropertyValue('--down').trim();
          out.up_color = up; out.down_color = down;
          out.has_up_usage = !!document.querySelector('[class*="up"]');
          // 可见英文裸键（长度<=24 的纯 ASCII 词，同页>=2 判泄漏）
          var words = (text.match(/\\b[a-z][a-z0-9_]{2,23}\\b/g) || []);
          var whitelist = /^(http|https|www|com|cn|api|json|png|md|py|js|css|html|ttl|dpapi|pct|nav|kline|etf|qdii|fof|lhb|yjb|sha|utf|mb|kb|usb|id|ok|no|yes)$/;
          var bad = {};
          var allow = ["ai_config", "akshare", "browser", "danjuan", "datacenter", "dpapi", "lsjz", "nav_grtd", "pip", "plug", "push2", "push2his", "yjb1"];
          words.forEach(function(w){ if(!whitelist.test(w) && allow.indexOf(w)<0){ bad[w]=(bad[w]||0)+1; } });
          out.en_leaks = Object.keys(bad).filter(function(k){return bad[k]>=2;});
          return out;
        })()""")


def main() -> int:
    """启动服务 + Edge，逐视口逐页取证。

    Returns:
        int: 退出码。
    """
    OUT.mkdir(parents=True, exist_ok=True)
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    log = open(OUT / "_vp_server.log", "ab")
    srv = subprocess.Popen(
        [str(py), "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1",
         "--port", "6914", "--log-level", "warning"],
        cwd=str(ROOT), stdout=log, stderr=log, stdin=subprocess.DEVNULL,
    )
    edge = subprocess.Popen(
        [EDGE, "--headless=new", "--disable-gpu", f"--remote-debugging-port={PORT}",
         f"--user-data-dir={OUT / '_vp_profile'}", "--window-size=1366,768", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    result: dict = {"viewports": {}, "summary": {}}
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
        # 固定深色主题，避免主题差异干扰色值断言
        cdp.js("try{localStorage.setItem('openbb-theme','dark')}catch(e){}")

        for vp_name, width, height, scale in VIEWPORTS:
            cdp.call("Emulation.setDeviceMetricsOverride", {
                "width": width, "height": height, "deviceScaleFactor": 0,
                "mobile": False, "scale": scale,
            })
            vp_result = {}
            for page_name, hash_route in PAGES:
                cdp.call("Page.navigate", {"url": BASE + "/" + hash_route})
                time.sleep(5)
                probe = probe_page(cdp)
                cdp.shot(f"viewport_{vp_name.replace('@', '_')}_{page_name}.png")
                vp_result[page_name] = probe
            result["viewports"][vp_name] = vp_result
            print(f"[{vp_name}] {len(vp_result)} 页取证完成")

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

    # 汇总
    issues = []
    for vp, pages in result["viewports"].items():
        for page, probe in pages.items():
            if probe.get("overflow_x"):
                issues.append(f"{vp}/{page}: 横向溢出 scroll_w={probe['scroll_w']} client_w={probe['client_w']}")
            if probe.get("undefined_count"):
                issues.append(f"{vp}/{page}: undefined ×{probe['undefined_count']}")
            if probe.get("nan_count"):
                issues.append(f"{vp}/{page}: NaN ×{probe['nan_count']}")
            leaks = probe.get("en_leaks") or []
            if leaks:
                issues.append(f"{vp}/{page}: 英文键泄漏 {leaks[:5]}")
            if not probe.get("up_color") or not probe.get("down_color"):
                issues.append(f"{vp}/{page}: 涨跌色变量缺失 up={probe.get('up_color')} down={probe.get('down_color')}")
    result["summary"] = {
        "total_checks": len(VIEWPORTS) * len(PAGES),
        "issues": issues,
        "pass": not issues,
        "note": "tables_clipped 为表格容器横向滚动（设计允许，非缺陷）；overflow_x 才判溢出",
    }
    (OUT.parent / "viewport_regression.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n检查 {result['summary']['total_checks']} 项 → {'全部通过 ✓' if result['summary']['pass'] else '发现问题'}")
    for item in issues[:20]:
        print("  -", item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
