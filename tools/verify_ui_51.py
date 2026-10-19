# -*- coding: utf-8 -*-
"""阶段 5.1 前端验收取证脚本（CDP 驱动 Edge 无头）。

用途（对应《设计基准说明书_阶段5.1》第十二章检查表）：
1. 三页面 × 1366×768 / 1024×640 截图（A-01/A-02）；
2. 四态截图：加载（限速网络抓骨架）/ 空 / 错 / 默认（A-18）；
3. 跳转实测 #1/#2/#5/#9：真实点击后校验 location.hash（A-08/A-13/A-16）；
4. 48 列栅格 computed style 抽查（A-03）、涨跌色抽查（A-04）、双徽章抽查（A-17 相关）。

用法：
    .venv\\Scripts\\python.exe tools\\verify_ui_51.py --base http://127.0.0.1:6901 --out <截图目录>

设计说明：脚本只做只读观测与点击，不修改任何业务数据。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

import requests
import websocket

EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_edge() -> str:
    """定位 Edge 可执行文件。"""
    for path in EDGE_PATHS:
        if os.path.exists(path):
            return path
    raise SystemExit("未找到 msedge.exe，请确认 Edge 已安装")


class CDP:
    """极简 Chrome DevTools Protocol 客户端（单页面 target，扁平会话）。"""

    def __init__(self, ws_url: str) -> None:
        """建立 websocket 连接并递增消息 id。"""
        self.ws = websocket.create_connection(ws_url, timeout=60)
        self.next_id = 1

    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """发送命令并等待同 id 的回包；事件消息直接丢弃。"""
        message_id = self.next_id
        self.next_id += 1
        payload = {"id": message_id, "method": method, "params": params or {}}
        self.ws.send(json.dumps(payload))
        while True:
            raw = self.ws.recv()
            data = json.loads(raw)
            if data.get("id") == message_id:
                if "error" in data:
                    raise RuntimeError(f"CDP {method} 失败：{data['error']}")
                return data.get("result", {})

    def eval_js(self, expression: str, *, await_promise: bool = False) -> Any:
        """执行 JS 并返回值（返回值为 JSON 可序列化对象）。"""
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
                "userGesture": True,
            },
        )
        return result.get("result", {}).get("value")

    def close(self) -> None:
        """关闭连接。"""
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001 - 关闭失败不影响结果
            pass


def launch_edge(port: int, profile_dir: str) -> subprocess.Popen:
    """启动带远程调试端口的无头 Edge。"""
    edge = find_edge()
    args = [
        edge,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--no-first-run",
        "--no-default-browser-check",
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",  # 允许本机 websocket 直连（CDP 必需）
        f"--user-data-dir={profile_dir}",
        "about:blank",
    ]
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def page_ws(port: int, timeout: float = 20.0) -> str:
    """等待调试端口就绪并返回页面 target 的 websocket 地址。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            targets = requests.get(f"http://127.0.0.1:{port}/json", timeout=2).json()
            for target in targets:
                if target.get("type") == "page":
                    return target["webSocketDebuggerUrl"]
        except Exception:  # noqa: BLE001 - 调试端口尚未就绪
            time.sleep(0.4)
    raise SystemExit("Edge 调试端口未就绪")


def set_viewport(cdp: CDP, width: int, height: int) -> None:
    """设置视口尺寸（同时影响 CSS 断点与截图分辨率）。"""
    cdp.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False},
    )


def throttle(cdp: CDP, latency_ms: int) -> None:
    """模拟网络延迟（用于抓取加载骨架）。"""
    cdp.call(
        "Network.enable",
        {},
    )
    cdp.call(
        "Network.emulateNetworkConditions",
        {
            "offline": False,
            "latency": latency_ms,
            "downloadThroughput": 200 * 1024,
            "uploadThroughput": 100 * 1024,
        },
    )


def goto(cdp: CDP, url: str) -> None:
    """导航到目标 URL 并等待 load 事件。"""
    cdp.call("Page.enable")
    cdp.call("Page.navigate", {"url": url})
    deadline = time.time() + 30
    while time.time() < deadline:
        state = cdp.eval_js("document.readyState")
        if state == "complete":
            return
        time.sleep(0.2)


def wait_blocks(cdp: CDP, timeout: float = 40.0) -> str:
    """等待当前可见页面的所有 .pg-body 离开 loading 态，返回最终状态串。

    只统计可见 .page-view 内的区块，避免隐藏页面（此前访问过的路由）干扰判定。
    """
    expression = (
        "(function(){var v=Array.prototype.filter.call(document.querySelectorAll('.page-view'),"
        "function(n){return !n.hidden;})[0];if(!v)return 'no-view';"
        "var n=v.querySelectorAll('.pg-body');if(!n.length)return 'no-body';"
        "return Array.prototype.map.call(n,function(x){return x.getAttribute('data-state')||'none';}).join(',');})()"
    )
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        last = cdp.eval_js(expression)
        if last and "loading" not in str(last):
            return str(last)
        time.sleep(0.3)
    return str(last)


def shot(cdp: CDP, path: str) -> None:
    """截图并写入文件。"""
    result = cdp.call("Page.captureScreenshot", {"format": "png", "fromSurface": True})
    import base64

    with open(path, "wb") as handle:
        handle.write(base64.b64decode(result["data"]))
    print(f"  截图 {os.path.basename(path)}")


def click_first_row(cdp: CDP, card_id: str) -> Dict[str, Any]:
    """点击指定卡片的第一个表格行，返回点击前后 hash。"""
    before = cdp.eval_js("location.hash")
    clicked = cdp.eval_js(
        "(function(){var c=document.getElementById('" + card_id + "');"
        "if(!c)return 'no-card';var r=c.querySelector('tbody tr');"
        "if(!r)return 'no-row';r.click();return r.innerText.slice(0,40);})()"
    )
    time.sleep(1.2)
    after = cdp.eval_js("location.hash")
    return {"card": card_id, "before": before, "clicked": clicked, "after": after}


def click_first_news(cdp: CDP) -> Dict[str, Any]:
    """点击相关新闻第一条标题。"""
    before = cdp.eval_js("location.hash")
    clicked = cdp.eval_js(
        "(function(){var n=document.querySelector('#ST-05 .pg-news-title');"
        "if(!n)return 'no-news';n.click();return n.innerText.slice(0,40);})()"
    )
    time.sleep(1.0)
    after = cdp.eval_js("location.hash")
    return {"before": before, "clicked": clicked, "after": after}


def tab_checks(cdp: CDP, base: str) -> Dict[str, Any]:
    """实测三组 Tab 是否真实重渲染（S-01 市场 / S-02 行业概念 / ST-02 与 ST-03、财务页签）。"""
    out: Dict[str, Any] = {}
    set_viewport(cdp, 1366, 900)

    # --- S-01 市场切换：cn → hk，校验数据与徽章同步变化 ---
    goto(cdp, base + "/#/sectors")
    wait_blocks(cdp, 40)
    cn_first = cdp.eval_js("(document.querySelector('#S-04 tbody tr')||{}).innerText||null")
    cdp.eval_js("document.querySelector('#S-01 .pg-tab[data-market=\"hk\"]').click()")
    time.sleep(1.5)
    wait_blocks(cdp, 40)
    hk_first = cdp.eval_js("(document.querySelector('#S-04 tbody tr')||{}).innerText||null")
    out["S-01"] = {
        "cn_first_row": cn_first,
        "hk_first_row": hk_first,
        "changed": bool(cn_first) and cn_first != hk_first,
        "hk_badge": cdp.eval_js("(document.querySelector('#S-03 .badge-src')||{}).textContent||null"),
        "hk_caliber": cdp.eval_js("(document.querySelector('#S-03 .badge-caliber')||{}).textContent||null"),
    }
    cdp.eval_js("document.querySelector('#S-01 .pg-tab[data-market=\"cn\"]').click()")
    time.sleep(1.2)
    wait_blocks(cdp, 40)

    # --- S-02 行业/概念切换：卡片编号 S-04 → S-05、行数变化 ---
    industry_rows = cdp.eval_js("document.querySelectorAll('#S-04 tbody tr').length")
    cdp.eval_js("document.querySelector('#S-02 .pg-tab[data-kind=\"concept\"]').click()")
    time.sleep(1.5)
    wait_blocks(cdp, 40)
    concept_rows = cdp.eval_js("document.querySelectorAll('#S-05 tbody tr').length")
    out["S-02"] = {
        "industry_rows": industry_rows,
        "concept_rows": concept_rows,
        "rank_id": cdp.eval_js("(document.querySelector('#S-05')||{}).id||null"),
        "title": cdp.eval_js(
            "document.querySelector('#S-05 .pg-card-title')?document.querySelector('#S-05 .pg-card-title').textContent:null"
        ),
        "changed": concept_rows != industry_rows and concept_rows > 0,
    }
    cdp.eval_js("document.querySelector('#S-02 .pg-tab[data-kind=\"industry\"]').click()")
    time.sleep(1.0)

    # --- ST-02 / ST-03 Tab 与财务页签 ---
    goto(cdp, base + "/#/stock?symbol=688012&market=a")
    wait_blocks(cdp, 40)
    cdp.eval_js("document.querySelector('#ST-02 .pg-tab[data-tab=\"minute\"]').click()")
    time.sleep(1.2)
    minute_card = cdp.eval_js("document.getElementById('ST-03')?document.getElementById('ST-03').id:null")
    minute_canvas = cdp.eval_js("document.querySelectorAll('#ST-03 canvas').length")
    cdp.eval_js("document.querySelector('#ST-03 .pg-tab[data-tab=\"kline\"]').click()")
    time.sleep(1.0)
    kline_card = cdp.eval_js("document.getElementById('ST-02')?document.getElementById('ST-02').id:null")

    # 财务页签：三大报表
    cdp.eval_js("document.querySelector('#st09-body .pg-tab[data-fin=\"report\"]').click()")
    time.sleep(2.5)
    report_buttons = cdp.eval_js(
        "(function(){var n=document.querySelectorAll('#st09-pane .pg-fin-tabs .pg-tab');"
        "return Array.prototype.map.call(n,function(x){return x.textContent;});})()"
    )
    report_rows = cdp.eval_js("document.querySelectorAll('#st09-pane tbody tr').length")
    out["ST"] = {
        "minute_card": minute_card,
        "minute_canvas": minute_canvas,
        "kline_card": kline_card,
        "report_buttons": report_buttons,
        "report_rows": report_rows,
    }
    return out


def main() -> None:
    """执行全部取证步骤并输出 JSON 结果。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:6901")
    parser.add_argument("--out", required=True)
    parser.add_argument("--port", type=int, default=9333)
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    profile = os.path.join(os.environ.get("TEMP", "."), "edge_verify_51")
    proc = launch_edge(args.port, profile)
    results: Dict[str, Any] = {"base": args.base, "shots": [], "jumps": [], "checks": {}}

    try:
        cdp = CDP(page_ws(args.port))
        cdp.call("Runtime.enable")

        pages = [
            ("sectors", "#/sectors"),
            ("stock", "#/stock?symbol=688012&market=a"),
            ("sector-val", "#/sector-val?board=%E5%8D%8A%E5%AF%BC%E4%BD%93&market=cn"),
        ]

        # ---------- 1. 双分辨率默认态 ----------
        for name, route in pages:
            for width, height in ((1366, 768), (1024, 640)):
                set_viewport(cdp, width, height)
                goto(cdp, args.base + "/" + route)
                state = wait_blocks(cdp)
                path = os.path.join(args.out, f"{name}_{width}x{height}.png")
                shot(cdp, path)
                results["shots"].append({"page": name, "view": f"{width}x{height}", "state": state, "file": path})
                print(f"{name} {width}x{height} state={state}")

        # ---------- 2. 四态：空 / 错 ----------
        set_viewport(cdp, 1366, 768)
        goto(cdp, args.base + "/#/sector-val")
        wait_blocks(cdp, 10)
        shot(cdp, os.path.join(args.out, "state_empty_sector_val.png"))

        goto(cdp, args.base + "/#/sector-val?board=%E4%B8%8D%E5%AD%98%E5%9C%A8%E7%9A%84%E6%9D%BF%E5%9D%97&market=cn")
        wait_blocks(cdp, 25)
        shot(cdp, os.path.join(args.out, "state_error_sector_val.png"))

        # ---------- 3. 四态：加载（限速抓骨架） ----------
        set_viewport(cdp, 1366, 900)
        throttle(cdp, 1200)
        goto(cdp, args.base + "/#/sector-val?board=%E9%93%B6%E8%A1%8C&market=cn")
        time.sleep(0.9)
        loading_state = cdp.eval_js(
            "(function(){var v=Array.prototype.filter.call(document.querySelectorAll('.page-view'),"
            "function(n){return !n.hidden;})[0];if(!v)return 'no-view';"
            "return Array.prototype.map.call(v.querySelectorAll('.pg-body'),"
            "function(n){return n.getAttribute('data-state');}).join(',');})()"
        )
        shot(cdp, os.path.join(args.out, "state_loading_sector_val.png"))
        throttle(cdp, 0)
        wait_blocks(cdp, 40)
        results["checks"]["loading_state"] = loading_state

        # ---------- 4. 跳转矩阵实测 ----------
        set_viewport(cdp, 1366, 900)
        goto(cdp, args.base + "/#/sectors")
        wait_blocks(cdp, 40)

        # #1 排行表行点击 → #/sector-val
        jump1 = click_first_row(cdp, "S-04")
        results["jumps"].append({"id": "#1", **jump1})

        # 目标页参数生效（板块名一致）
        wait_blocks(cdp, 40)
        results["checks"]["val_board_label"] = cdp.eval_js(
            "document.getElementById('val-board-label') ? document.getElementById('val-board-label').textContent : null"
        )

        # #2 成分股行点击 → #/stock
        jump2 = click_first_row(cdp, "V-03")
        results["jumps"].append({"id": "#2", **jump2})
        wait_blocks(cdp, 40)
        results["checks"]["stock_symbol"] = cdp.eval_js(
            "document.getElementById('stock-symbol') ? document.getElementById('stock-symbol').value : null"
        )

        # #5 相关新闻标题 → #/news
        jump5 = click_first_news(cdp)
        results["jumps"].append({"id": "#5", **jump5})

        # #9 热力图图块点击 → #/sector-val
        goto(cdp, args.base + "/#/sectors")
        wait_blocks(cdp, 40)
        box = cdp.eval_js(
            "(function(){var c=document.querySelector('#S-03 canvas');if(!c)return null;"
            "var r=c.getBoundingClientRect();return {x:r.x+r.width*0.25,y:r.y+r.height*0.35};})()"
        )
        before = cdp.eval_js("location.hash")
        if box:
            for event_type in ("mousePressed", "mouseReleased"):
                cdp.call(
                    "Input.dispatchMouseEvent",
                    {"type": event_type, "x": box["x"], "y": box["y"], "button": "left", "clickCount": 1},
                )
            time.sleep(1.5)
        results["jumps"].append({"id": "#9", "before": before, "after": cdp.eval_js("location.hash")})

        # ---------- 5. 栅格 / 色板 / 徽章抽查 ----------
        goto(cdp, args.base + "/#/sectors")
        wait_blocks(cdp, 40)
        results["checks"]["grid"] = cdp.eval_js(
            "(function(){var out={};"
            "['.g-7','.g-5','.g-12'].forEach(function(cls){var n=document.querySelector('.pg-grid '+cls);"
            "if(!n){out[cls]=null;return;}var s=getComputedStyle(n);"
            "out[cls]={start:s.gridColumnStart,end:s.gridColumnEnd};});return out;})()"
        )
        results["checks"]["colors"] = cdp.eval_js(
            "(function(){var up=document.querySelector('#S-04 .up');var down=document.querySelector('#S-04 .down');"
            "var st=document.querySelector('.badge-src');"
            "return {up:up?getComputedStyle(up).color:null,down:down?getComputedStyle(down).color:null,"
            "badge:st?getComputedStyle(st).color:null};})()"
        )
        results["checks"]["badge_counts"] = cdp.eval_js(
            "(function(){var out={};['S-03','S-04','S-06','S-07'].forEach(function(id){"
            "var c=document.getElementById(id);if(!c){out[id]=null;return;}"
            "out[id]={src:c.querySelectorAll('.badge-src').length,cal:c.querySelectorAll('.badge-caliber').length};});"
            "return out;})()"
        )
        results["checks"]["leaks"] = cdp.eval_js(
            "(function(){var t=document.body.innerText;"
            "return {err_code:/ERR_[A-Z_]+|UPSTREAM_|INVALID_PARAM/.test(t),"
            "placeholder:/dev-placeholder|\\(P1\\)/.test(t)};})()"
        )

        # ---------- 6. Tab 真实切换（P1-01：不得是静态标签） ----------
        results["checks"]["tabs"] = tab_checks(cdp, args.base)

        cdp.close()
    finally:
        proc.terminate()

    out_json = os.path.join(args.out, "ui_verify_result.json")
    with open(out_json, "w", encoding="utf-8") as handle:
        json.dump(results, handle, ensure_ascii=False, indent=2)
    print(json.dumps(results["jumps"], ensure_ascii=False, indent=1))
    print(json.dumps(results["checks"], ensure_ascii=False, indent=1)[:1200])
    print("结果文件：", out_json)


if __name__ == "__main__":
    main()
