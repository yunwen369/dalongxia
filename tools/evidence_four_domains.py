# -*- coding: utf-8 -*-
"""四域增强交互取证（CDP）：组件库 / 设置 / 连接器 / 导航。

用法（工程根）：
    .venv\\Scripts\\python.exe tools\\evidence_four_domains.py

产物写到 `B_交付归档/03_测试报告/20261025_four/`：
- lib_01_open.png          组件库抽屉（搜索 + 分类 + 预览卡网格）
- lib_02_search.png        搜索过滤后
- lib_03_table.png         L-03 组件说明表（44 项）
- lib_04_drag.png          拖拽入位后画布新增组件
- set_01_ai.png            S-01 AI 设置（服务商/密钥掩码/测试连接）
- set_02_test.png          测试连接结果 toast（真实上游）
- set_03_font.png          字号档位切换
- set_04_cache.png         S-03 缓存统计 + TTL 表
- set_05_compliance.png    S-04 合规双开关
- set_06_reset.png         恢复出厂二次确认弹窗
- con_01_overview.png      C-01 总览 + C-02 源表格
- con_02_test.png          测试连接 toast
- con_03_matrix.png        C-04 四市场能力矩阵
- nav_01_palette.png       Ctrl+K 命令面板（含 4 个新页）
- four_domains_evidence.json
"""

from __future__ import annotations

import base64
import json
import pathlib
import subprocess
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "B_交付归档" / "03_测试报告" / "20261025_four"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PORT = 9334
BASE = "http://127.0.0.1:6907"


class CDP:
    """极简 CDP 客户端（Runtime.evaluate / Page.captureScreenshot）。"""

    def __init__(self, ws_url: str) -> None:
        """建立到目标页的 WebSocket 连接。

        Args:
            ws_url: DevTools WebSocket 地址。
        """
        import websocket  # type: ignore

        self.ws = websocket.create_connection(ws_url, timeout=30)
        self.seq = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        """发送一条 CDP 命令并等待结果。

        Args:
            method: 方法名。
            params: 参数。

        Returns:
            dict: 结果。
        """
        self.seq += 1
        self.ws.send(json.dumps({"id": self.seq, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.seq:
                return msg

    def js(self, expression: str):
        """在页面执行 JS 并返回其值。

        Args:
            expression: JS 表达式。

        Returns:
            任意: 表达式结果。
        """
        res = self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        })
        return (res.get("result") or {}).get("result", {}).get("value")

    def shot(self, name: str) -> None:
        """截图落盘。

        Args:
            name: 文件名（不含目录）。
        """
        res = self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
        data = res.get("result", {}).get("data", "")
        path = OUT / name
        path.write_bytes(base64.b64decode(data))
        print(f"  截图 {name} ({path.stat().st_size} bytes)")

    def close(self) -> None:
        """关闭连接。"""
        try:
            self.ws.close()
        except Exception:
            pass


def wait_ready(url: str, rounds: int = 40) -> None:
    """等待服务就绪。

    Args:
        url: 健康检查地址。
        rounds: 轮询次数（每次 1 秒）。
    """
    for _ in range(rounds):
        time.sleep(1)
        try:
            urllib.request.urlopen(url, timeout=3).read()
            return
        except Exception:
            continue
    raise RuntimeError("服务未就绪")


def main() -> int:
    """启动服务 + Edge，逐场景取证。

    Returns:
        int: 退出码。
    """
    OUT.mkdir(parents=True, exist_ok=True)
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    log = open(OUT / "_four_server.log", "ab")
    srv = subprocess.Popen(
        [str(py), "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1",
         "--port", "6907", "--log-level", "warning"],
        cwd=str(ROOT), stdout=log, stderr=log, stdin=subprocess.DEVNULL,
    )
    profile = OUT / "_edge_profile"
    edge = subprocess.Popen(
        [EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
         "--window-size=1440,1500", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    result: dict = {}
    try:
        wait_ready(f"{BASE}/api/health")

        ws_url = None
        for _ in range(30):
            time.sleep(0.5)
            try:
                raw = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=3).read()
                for item in json.loads(raw):
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
        # 关掉后端未就绪时的告警条，避免遮挡取证
        cdp.js("try{localStorage.setItem('openbb-theme','dark')}catch(e){}")

        # ---------- 播种：一个固定仪表板（保证组件库拖拽有画布可落） ----------
        cdp.call("Page.navigate", {"url": BASE + "/#/dashboard"})
        time.sleep(5)
        seed = {
            "dashboards": [{
                "id": "dash-four", "name": "取证仪表板", "group": "取证",
                "widgets": [{"id": "S-04", "span": 24}, {"id": "N-02", "span": 24}],
            }],
            "activeId": "dash-four",
        }
        cdp.js("fetch('/api/dashboards',{method:'PUT',headers:{'Content-Type':'application/json'},"
               "body:%s})" % json.dumps(json.dumps(seed)))
        cdp.js("localStorage.setItem('openbb-dashboards-v1', %s)" % json.dumps(json.dumps(seed)))
        cdp.call("Page.reload", {"ignoreCache": True})
        time.sleep(7)

        # ================= 组件库 =================
        print("[1] 组件库")
        cdp.js("location.hash = '#/dashboard'")
        time.sleep(3)
        # 打开组件库抽屉（工具栏「组件库」按钮）
        opened = cdp.js(
            "(function(){var b=Array.prototype.slice.call("
            "document.querySelectorAll('#page-dashboard .page-toolbar button'))"
            ".filter(function(x){return x.textContent.trim()==='组件库';});"
            "if(b.length){b[0].click();return true;}return false;})()")
        time.sleep(2.5)
        result["lib_open"] = {
            "opened": opened,
            "cards": cdp.js("document.querySelectorAll('.dash-lib-card').length"),
            "id_first": cdp.js("(document.querySelector('.dash-lib-id')||{}).textContent"),
            "thumbs": cdp.js("document.querySelectorAll('.dash-lib-thumb').length"),
            "draggable": cdp.js(
                "(document.querySelector('.dash-lib-card')||{}).getAttribute"
                " ? document.querySelector('.dash-lib-card').getAttribute('draggable') : null"),
        }
        cdp.shot("lib_01_open.png")

        # 搜索过滤（真实输入事件）
        cdp.js(
            "(function(){var s=document.querySelector('.dash-library input[type=search]');"
            "if(!s){return false;}s.value='自选';s.dispatchEvent(new Event('input',{bubbles:true}));"
            "return true;})()")
        time.sleep(1.5)
        result["lib_search"] = {
            "cards": cdp.js("document.querySelectorAll('.dash-lib-card').length"),
            "first": cdp.js("(document.querySelector('.dash-lib-title')||{}).textContent"),
        }
        cdp.shot("lib_02_search.png")
        # 清回全部
        cdp.js(
            "(function(){var s=document.querySelector('.dash-library input[type=search]');"
            "if(s){s.value='';s.dispatchEvent(new Event('input',{bubbles:true}));}})()")
        time.sleep(1)

        # L-03 组件说明表
        tableOpened = cdp.js(
            "(function(){var b=Array.prototype.slice.call(document.querySelectorAll('.dash-library .pg-card-head button'))"
            ".filter(function(x){return x.textContent.trim()==='组件说明表';});"
            "if(b.length){b[0].click();return true;}return false;})()")
        time.sleep(1.5)
        result["lib_table"] = {
            "opened": tableOpened,
            "rows": cdp.js("document.querySelectorAll('.ui-modal.wide .pg-table tr').length"),
            "registry_total": cdp.js(
                "fetch('/api/widgets').then(function(r){return r.json()})"
                ".then(function(b){return (b.data||{}).total})"),
        }
        cdp.shot("lib_03_table.png")
        cdp.js(
            "(function(){var b=Array.prototype.slice.call(document.querySelectorAll('.ui-modal button'))"
            ".filter(function(x){return x.textContent.trim()==='关闭';});if(b.length){b[0].click();}})()")
        time.sleep(0.8)

        # 拖拽入位：合成 DataTransfer 走真实 drop 分支
        before = cdp.js("document.querySelectorAll('#dash-grid .dash-widget').length")
        dragged = cdp.js(
            "(function(){"
            "var card=document.querySelector('.dash-lib-card');"
            "if(!card){return null;}"
            "var id=card.getAttribute('data-widget-id');"
            "var dt=new DataTransfer();"
            "dt.setData('application/x-openbb-widget',id);"
            "dt.setData('text/plain',id);"
            "var grid=document.getElementById('dash-grid');"
            "grid.dispatchEvent(new DragEvent('dragover',{bubbles:true,cancelable:true,dataTransfer:dt}));"
            "grid.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:dt}));"
            "return id;})()")
        time.sleep(2.5)
        after = cdp.js("document.querySelectorAll('#dash-grid .dash-widget').length")
        result["lib_drag"] = {"dragged": dragged, "before": before, "after": after}
        cdp.shot("lib_04_drag.png")
        cdp.js(
            "(function(){var b=Array.prototype.slice.call(document.querySelectorAll('.dash-library .pg-card-head button'))"
            ".filter(function(x){return x.textContent.trim()==='关闭';});if(b.length){b[0].click();}})()")
        time.sleep(1)

        # ================= 设置 =================
        print("[2] 设置")
        cdp.js("location.hash = '#/settings'")
        time.sleep(4)
        result["set_ai"] = {
            "provider_options": cdp.js("document.getElementById('set-provider').options.length"),
            "key_type": cdp.js("document.getElementById('set-api-key').type"),
            "has_test_btn": cdp.js("!!document.getElementById('set-ai-test')"),
            "security_note": cdp.js(
                "(function(){var n=document.getElementById('set-ai-body');"
                "return n?n.textContent.indexOf('DPAPI')>=0:null})()"),
        }
        cdp.shot("set_01_ai.png")

        # 测试连接（填不可达地址走真实失败路径，或真实上游鉴权失败）
        cdp.js(
            "(function(){document.getElementById('set-base-url').value='https://api.deepseek.com/v1';"
            "document.getElementById('set-model').value='deepseek-chat';"
            "document.getElementById('set-api-key').value='sk-invalid-evidence-key';"
            "document.getElementById('set-ai-test').click();return true;})()")
        time.sleep(12)
        toast = cdp.js("(document.getElementById('toast')||{}).textContent")
        result["set_test"] = {"toast": toast}
        cdp.shot("set_02_test.png")

        # 字号档位
        font_before = cdp.js("document.documentElement.getAttribute('data-font')")
        cdp.js(
            "(function(){var row=document.getElementById('set-theme-body');"
            "var b=Array.prototype.slice.call(row.querySelectorAll('button'))"
            ".filter(function(x){return x.textContent.trim()==='大';});"
            "if(b.length){b[0].click();return true;}return false;})()")
        time.sleep(1.5)
        result["set_font"] = {
            "before": font_before,
            "after": cdp.js("document.documentElement.getAttribute('data-font')"),
            "persisted": cdp.js("localStorage.getItem('openbb-font')"),
            "fs_body": cdp.js(
                "getComputedStyle(document.documentElement).getPropertyValue('--fs-body')"),
        }
        cdp.shot("set_03_font.png")
        # 还原字号，避免污染现场
        cdp.js(
            "(function(){var row=document.getElementById('set-theme-body');"
            "var b=Array.prototype.slice.call(row.querySelectorAll('button'))"
            ".filter(function(x){return x.textContent.trim()==='中';});"
            "if(b.length){b[0].click();}})()")
        time.sleep(1)

        # 缓存卡
        result["set_cache"] = {
            "memory": cdp.js(
                "(function(){var n=document.getElementById('set-cache-body');"
                "return n?n.textContent.indexOf('MB')>=0:null})()"),
            "ttl_rows": cdp.js(
                "document.querySelectorAll('#set-cache-body .pg-table tr').length"),
            "has_clear": cdp.js(
                "(function(){var n=document.getElementById('set-cache-body');"
                "return n?n.textContent.indexOf('清理缓存')>=0:null})()"),
        }
        cdp.shot("set_04_cache.png")

        # 合规卡（养基宝开关——本机已绑定，应出现开关）
        result["set_compliance"] = {
            "estimation": cdp.js(
                "(function(){var n=document.getElementById('set-compliance-body');"
                "return n?n.textContent.indexOf('盘中估值')>=0:null})()"),
            "yjb": cdp.js(
                "(function(){var n=document.getElementById('set-compliance-body');"
                "return n?n.textContent.indexOf('养基宝增强通道')>=0:null})()"),
            "yjb_toggle": cdp.js(
                "(function(){var n=document.getElementById('set-compliance-body');"
                "if(!n){return null;}"
                "var b=Array.prototype.slice.call(n.querySelectorAll('button'));"
                "return b.length?b.map(function(x){return x.textContent.trim()}):null})()"),
        }
        cdp.shot("set_05_compliance.png")

        # 恢复出厂：只开弹窗不确认（确认会清真实偏好）
        cdp.js(
            "(function(){var b=Array.prototype.slice.call(document.querySelectorAll('#set-about-body button'))"
            ".filter(function(x){return x.textContent.trim()==='恢复出厂';});"
            "if(b.length){b[0].click();return true;}return false;})()")
        time.sleep(1.2)
        result["set_reset"] = {
            "modal_title": cdp.js(
                "(function(){var n=document.querySelector('.ui-modal-title');"
                "return n?n.textContent:null})()"),
            "danger_btn": cdp.js(
                "(function(){var b=document.querySelector('.ui-modal-actions .pg-btn.danger');"
                "return b?b.textContent:null})()"),
            "kept_note": cdp.js(
                "(function(){var n=document.querySelector('.ui-modal-message');"
                "return n?n.textContent.indexOf('持仓')>=0:null})()"),
        }
        cdp.shot("set_06_reset.png")
        cdp.js(
            "(function(){var b=Array.prototype.slice.call(document.querySelectorAll('.ui-modal-actions button'))"
            ".filter(function(x){return x.textContent.trim()==='取消';});if(b.length){b[0].click();}})()")
        time.sleep(0.8)

        # ================= 连接器 =================
        print("[3] 连接器")
        cdp.js("location.hash = '#/connectors'")
        time.sleep(5)
        result["con_overview"] = {
            "stats": cdp.js(
                "document.querySelectorAll('#c01-body .pg-metric-cell').length"),
            "rows": cdp.js(
                "document.querySelectorAll('#c01-body .pg-table tbody tr').length"),
            "pill_states": cdp.js(
                "Array.prototype.slice.call("
                "document.querySelectorAll('#c01-body .pg-table .chip')).map(function(n){"
                "return n.textContent.trim()})"),
            "rate_cell": cdp.js(
                "(function(){var n=document.querySelector('#c01-body .pg-table tbody tr');"
                "return n?n.textContent.indexOf('限流')>=0:null})()"),
            "cache_btn": cdp.js(
                "(function(){var n=document.getElementById('c01-body');"
                "return n?n.textContent.indexOf('清理缓存')>=0:null})()"),
        }
        cdp.shot("con_01_overview.png")

        # 测试连接（真实探测第一行源）
        tested = cdp.js(
            "(function(){var b=Array.prototype.slice.call("
            "document.querySelectorAll('#c01-body .pg-table tbody tr button'))"
            ".filter(function(x){return x.textContent.trim()==='测试连接';});"
            "if(b.length){b[0].click();return true;}return false;})()")
        time.sleep(12)
        result["con_test"] = {
            "clicked": tested,
            "toast": cdp.js("(document.getElementById('toast')||{}).textContent"),
            "btn_text": cdp.js(
                "(function(){var b=Array.prototype.slice.call("
                "document.querySelectorAll('#c01-body .pg-table tbody tr button'))"
                ".filter(function(x){return x.textContent.indexOf('测试')>=0});"
                "return b.length?b[0].textContent.trim():null})()"),
        }
        cdp.shot("con_02_test.png")

        # 矩阵卡（滚到底部截图）
        cdp.js("document.getElementById('C-04').scrollIntoView(false)")
        time.sleep(1)
        result["con_matrix"] = {
            "cols": cdp.js(
                "Array.prototype.slice.call("
                "document.querySelectorAll('#c04-body .pg-table th,"
                " #c04-body .pg-table tr:first-child td')).map(function(n){return n.textContent.trim()})"),
            "fund_col": cdp.js(
                "(function(){var n=document.getElementById('c04-body');"
                "return n?n.textContent.indexOf('场外基金')>=0||n.textContent.indexOf('基金')>=0:null})()"),
            "gaps": cdp.js(
                "document.querySelectorAll('#c04-body .pg-news-item').length"),
        }
        cdp.shot("con_03_matrix.png")

        # ================= 导航 =================
        print("[4] 命令面板")
        cdp.js("location.hash = '#/home'")
        time.sleep(2)
        cdp.js("window.UI.openPalette('')")
        time.sleep(2)
        labels = cdp.js(
            "Array.prototype.slice.call(document.querySelectorAll('.ui-palette-list *'))"
            ".map(function(n){return (n.textContent||'').trim()})"
            ".filter(function(s){return s && s.length<24})")
        result["nav_palette"] = {
            "labels": labels[:26],
            "has_charting": "图表" in labels,
            "has_lhb": "龙虎榜" in labels,
            "has_watchlist": "自选股" in labels,
            "has_yjb": "养基宝" in labels,
        }
        cdp.shot("nav_01_palette.png")
        cdp.js("window.UI.closePalette()")

        # 顶栏顺序（R3-31）
        result["nav_topbar"] = {"order": cdp.js(
            "Array.prototype.slice.call(document.querySelectorAll('#main-nav .nav-item'))"
            ".map(function(n){return n.getAttribute('data-view')})")}
        # 侧栏系统区唯一入口（R3-10）
        result["nav_sidebar"] = {"system_items": cdp.js(
            "Array.prototype.slice.call(document.querySelectorAll('.sidebar-item'))"
            ".filter(function(n){var v=n.getAttribute('data-view');"
            "return v==='connectors'||v==='settings';}).length")}

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

    (OUT / "four_domains_evidence.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== 取证结果 ===")
    print(json.dumps(result, ensure_ascii=False, indent=2)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
