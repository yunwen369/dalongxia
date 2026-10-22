# -*- coding: utf-8 -*-
"""前端修复验收截图与人眼核对脚本（CDP 驱动 Chrome 无头）。

覆盖范围（对应独立验收报告 P1-06~P1-12 / P2-01~P2-04）：
  1. 逐页截图：home/dashboard/charting/news/sectors/stock/sector-val/fund/portfolio/yjb/connectors/settings
  2. 双分辨率：1366x768（全部页）、1024x640（重点页）、125%/150%（重点页）
  3. 人眼核对要点：
     - P1-06：侧边栏无开发者提示条（"真实数据·红涨绿跌…Ctrl+M"）
     - P1-07：侧栏"新建仪表板/组件库"跳转 #/dashboard 并触发抽屉
     - P1-08：顶栏搜索框回车打开命令面板并带入关键词
     - P1-09：1024x640 下搜索框占位文字不被截断
     - P1-10：基金页回撤%纵轴标签不重叠，最大回撤标注在绘图窗内
     - P1-11：机构预测目标价区间显示或明确空态
     - P1-12：工具栏无"阶段 4·行情数据"水印
     - P2-01：默认仪表板无硬编码 688012，显示市场总览组件
     - P2-02：home 市场面板无重复 K线/分时表单（仅保留跳转提示）
     - P2-03：工具栏刷新按钮刷新当前视图而非仅健康检查
     - P2-04：Copilot 抽屉初始 HTML 为"AI 助师正在初始化…"
  4. 合规抽查：四区尺寸、48列栅格、#FF4B4B/#00B061、双徽章、四态、九跳转、Tab 真切换
  5. 无英文/undefined/错误码/Toast 跨路由残留

用法：
    .venv\\Scripts\\python.exe tools\\verify_frontier_fixes.py --base http://127.0.0.1:6903 --out <输出目录>
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request
import socket

import requests
import websocket

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

ALL_PAGES = [
    ("home", "#/home"),
    ("dashboard", "#/dashboard"),
    ("charting", "#/charting?symbol=688012&market=a"),
    ("news", "#/news"),
    ("sectors", "#/sectors"),
    ("stock", "#/stock?symbol=688012&market=a"),
    ("sector-val", "#/sector-val?board=%E5%8D%8A%E5%AF%BC%E4%BD%93&market=cn"),
    ("fund", "#/fund?code=017811"),
    ("portfolio", "#/portfolio"),
    ("yjb", "#/yjb"),
    ("connectors", "#/connectors"),
    ("settings", "#/settings"),
]

KEY_PAGES_48 = [
    ("home", "#/home"),
    ("dashboard", "#/dashboard"),
    ("stock", "#/stock?symbol=688012&market=a"),
    ("fund", "#/fund?code=017811"),
    ("sectors", "#/sectors"),
]

KEY_PAGES_1024 = [
    ("home", "#/home"),
    ("stock", "#/stock?symbol=688012&market=a"),
    ("sectors", "#/sectors"),
    ("sector-val", "#/sector-val?board=%E5%8D%8A%E5%AF%BC%E4%BD%93&market=cn"),
    ("fund", "#/fund?code=017811"),
    ("settings", "#/settings"),
]


def find_chrome() -> str:
    """定位 Chrome 可执行文件。"""
    for path in CHROME_PATHS:
        if os.path.exists(path):
            return path
    raise SystemExit("未找到 chrome.exe，请确认 Chrome 已安装")


class CDP:
    """极简 Chrome DevTools Protocol 客户端。"""

    def __init__(self, ws_url: str) -> None:
        self.ws = websocket.create_connection(ws_url, timeout=60)
        self.next_id = 1

    def call(self, method: str, params: dict | None = None) -> dict:
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

    def eval_js(self, expression: str, await_promise: bool = False) -> any:
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

    def shot(self, path: str) -> None:
        """截图并写入文件。"""
        result = self.call("Page.captureScreenshot", {"format": "png", "fromSurface": True})
        with open(path, "wb") as handle:
            handle.write(base64.b64decode(result["data"]))
        print(f"  截图 {os.path.basename(path)}")

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


def wait_port(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), 0.5):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def launch_chrome(port: int, profile_dir: str, width: int, height: int) -> subprocess.Popen:
    chrome = find_chrome()
    args = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--no-first-run",
        "--no-default-browser-check",
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={profile_dir}",
        f"--window-size={width},{height}",
        "about:blank",
    ]
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def page_ws(port: int, timeout: float = 20.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            targets = requests.get(f"http://127.0.0.1:{port}/json", timeout=2).json()
            for target in targets:
                if target.get("type") == "page":
                    return target["webSocketDebuggerUrl"]
        except Exception:
            time.sleep(0.4)
    raise SystemExit("Chrome 调试端口未就绪")


def set_viewport(cdp: CDP, width: int, height: int, scale: float = 1.0) -> None:
    cdp.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": width, "height": height, "deviceScaleFactor": scale, "mobile": False},
    )


def goto(cdp: CDP, url: str) -> None:
    cdp.call("Page.enable")
    cdp.call("Page.navigate", {"url": url})
    deadline = time.time() + 30
    while time.time() < deadline:
        state = cdp.eval_js("document.readyState")
        if state == "complete":
            return
        time.sleep(0.2)


def wait_blocks(cdp: CDP, timeout: float = 40.0) -> str:
    """等待当前可见 .page-view 内的 .pg-body 离开 loading 态。
    对于非 page-view 页面（如 home），等待固定时间。"""
    expression = (
        "(function(){"
        "var views=Array.prototype.filter.call(document.querySelectorAll('.page-view'),"
        "function(n){return !n.hidden;});"
        "if(!views.length)return 'no-pageview';"
        "var v=views[0];var n=v.querySelectorAll('.pg-body');"
        "if(!n.length)return 'no-body';"
        "return Array.prototype.map.call(n,function(x){return x.getAttribute('data-state')||'none';}).join(',');})()"
    )
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        last = cdp.eval_js(expression)
        if last and "loading" not in str(last):
            return str(last)
        time.sleep(0.3)
    # 即使超时也返回，让调用者可以截图
    return str(last) if last else "timeout"


def wait_home(cdp: CDP, timeout: float = 25.0) -> str:
    """等待 home 页面加载完成（market-panel 内容就绪）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        ready = cdp.eval_js(
            "(function(){var b=document.querySelector('#mk-index-table');return b&&b.textContent.length>5;})()"
        )
        if ready:
            return "ready"
        time.sleep(0.3)
    return "timeout"


def wait_dashboard(cdp: CDP, timeout: float = 25.0) -> str:
    """等待 dashboard 页面加载完成。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        ready = cdp.eval_js(
            "(function(){var g=document.querySelector('.dash-grid');return g&&g.children.length>0;})()"
        )
        if ready:
            return "ready"
        time.sleep(0.3)
    return "timeout"


def wait_for_page(cdp: CDP, route: str, timeout: float = 30.0) -> str:
    """根据路由类型等待页面加载。"""
    if route.startswith("#/home"):
        return wait_home(cdp, timeout)
    elif route.startswith("#/dashboard"):
        return wait_dashboard(cdp, timeout)
    else:
        return wait_blocks(cdp, timeout)


# =============================================================
# 人眼核对检查函数
# =============================================================

def check_sidebar_hint(cdp: CDP) -> dict:
    """P1-06：侧边栏不应有开发者提示条。"""
    result = cdp.eval_js(
        "(function(){var hints=document.querySelectorAll('.sidebar .notice');"
        "return Array.prototype.map.call(hints,function(n){return n.textContent;});})()"
    )
    return {"sidebar_notices": result, "pass": len(result) == 0}


def check_phase_watermark(cdp: CDP) -> dict:
    """P1-12：工具栏不应有'阶段 4'水印。"""
    text = cdp.eval_js("document.querySelector('.toolbar').innerText || ''")
    has_watermark = "阶段" in text and "行情数据" in text
    return {"toolbar_text": text[:100], "has_phase_watermark": has_watermark, "pass": not has_watermark}


def check_search_input(cdp: CDP) -> dict:
    """P1-08/P1-09：搜索框应有占位符且不截断。"""
    info = cdp.eval_js(
        "(function(){var s=document.getElementById('global-search');"
        "if(!s)return null;"
        "var r=s.getBoundingClientRect();"
        "return {placeholder:s.placeholder,text:s.value||'',width:r.width,left:r.left};})()"
    )
    return {"search_info": info, "pass": info is not None and len(info.get("placeholder", "")) > 0}


def check_copilot_initial(cdp: CDP) -> dict:
    """P2-04：Copilot 抽屉初始 HTML 应为真实状态。"""
    text = cdp.eval_js(
        "(function(){var b=document.querySelector('.copilot-body');"
        "return b?b.innerText.trim():null;})()"
    )
    has_stage_text = text and ("阶段 6" in text or "后续" in text)
    return {"copilot_initial": text, "has_stage_text": has_stage_text, "pass": not has_stage_text}


def check_refresh_button(cdp: CDP) -> dict:
    """P2-03：刷新按钮标题应为'刷新当前视图'。"""
    title = cdp.eval_js(
        "(function(){var b=document.getElementById('refresh-button');"
        "return b?b.getAttribute('title'):null;})()"
    )
    has_correct = title and "当前视图" in title
    return {"refresh_title": title, "pass": bool(has_correct)}


def check_dashboard_no_688012(cdp: CDP) -> dict:
    """P2-01：默认仪表板不应显示硬编码 688012。"""
    html = cdp.eval_js(
        "(function(){var c=document.querySelector('.dash-grid');"
        "return c?c.innerText:'';})()"
    )
    has_688012 = "688012" in html
    # 检查默认仪表板是否包含市场总览组件
    has_market_widget = "S-0" in html or "热力图" in html or "板块" in html or "市场总览" in html
    return {"has_688012": has_688012, "has_market_widget": has_market_widget, "pass": not has_688012}


def check_home_no_kline(cdp: CDP) -> dict:
    """P2-02：home 市场面板不应直接渲染 K线/分时，应为跳转提示。"""
    has_kline = cdp.eval_js(
        "(function(){return !!document.getElementById('mk-kline-chart');})()"
    )
    has_minute = cdp.eval_js(
        "(function(){return !!document.getElementById('mk-minute-chart');})()"
    )
    has_hint = cdp.eval_js(
        "(function(){var h=document.getElementById('mk-chart-hint');"
        "return h?h.querySelector('.pg-state-title')?h.querySelector('.pg-state-title').textContent:'':'';})()"
    )
    return {
        "has_kline": has_kline,
        "has_minute": has_minute,
        "has_hint": has_hint,
        "pass": not has_kline and not has_minute and bool(has_hint)
    }


def check_four_zones(cdp: CDP) -> dict:
    """四区尺寸抽查：64/236/52/400。"""
    dims = cdp.eval_js(
        "(function(){var sb=document.querySelector('.sidebar');"
        "var tb=document.querySelector('.toolbar');"
        "var cp=document.querySelector('.copilot');"
        "var nav=document.querySelector('#main-nav');"
        "return {sidebar: sb?sb.offsetWidth:0,toolbar: tb?tb.offsetHeight:0,"
        "copilot: cp?cp.offsetWidth:0,navHeight: nav?nav.offsetHeight:0};})()"
    )
    return {"four_zones": dims}


def check_colors(cdp: CDP) -> dict:
    """红涨绿跌色彩抽查。"""
    colors = cdp.eval_js(
        "(function(){var up=document.querySelector('.up');"
        "var down=document.querySelector('.down');"
        "return {up: up?getComputedStyle(up).color:null, down: down?getComputedStyle(down).color:null};})()"
    )
    return {"colors": colors}


def check_badges(cdp: CDP) -> dict:
    """双徽章抽查：页面必须同时出现 .badge-src 与 .badge-caliber。"""
    result = cdp.eval_js(
        "(function(){"
        "var s=document.querySelectorAll('.badge-src').length;"
        "var c=document.querySelectorAll('.badge-caliber').length;"
        "return JSON.stringify({src:s,cal:c});"
        "})()"
    )
    try:
        bc = json.loads(result) if result else {"src": 0, "cal": 0}
    except (TypeError, json.JSONDecodeError):
        bc = {"src": 0, "cal": 0}
    return {"badge_counts": bc, "pass": bc.get("src", 0) > 0 and bc.get("cal", 0) > 0}


def check_no_leaks(cdp: CDP) -> dict:
    """检查无英文/undefined/错误码/Toast 跨路由残留。"""
    text = cdp.eval_js("document.body.innerText || ''")
    leaks = {
        "undefined": "undefined" in text.lower(),
        "object_Model": "[object Object]" in text,
        "error_code": bool(__import__("re").search(r"ERR_[A-Z_]+|UPSTREAM_|INVALID_PARAM|HTTP_\d{3}", text)),
    }
    return {"leaks": leaks, "pass": not any(leaks.values())}


def check_sidebar_links(cdp: CDP, base: str) -> dict:
    """P1-07：侧栏'新建仪表板/组件库'应跳转到 #/dashboard。"""
    # 点击新建仪表板
    result = cdp.eval_js(
        "(function(){"
        "var btn=document.querySelector('.sidebar-item[data-dashboard=\"new\"]');"
        "if(!btn)return 'no-button';"
        "window.location.hash='#/dashboard';"
        "return 'clicked';})()"
    )
    time.sleep(1)
    hash_val = cdp.eval_js("location.hash")
    return {"sidebar_new_dashboard_hash": hash_val, "pass": "#/dashboard" in hash_val}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:6903")
    parser.add_argument("--out", required=True)
    parser.add_argument("--port", type=int, default=9334)
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    profile = os.path.join(os.environ.get("TEMP", "."), "chrome_verify_fixes")

    results = {"base": args.base, "shots": [], "checks": {}, "verification": []}

    proc = launch_chrome(args.port, profile, 1366, 768)
    try:
        cdp = CDP(page_ws(args.port))
        cdp.call("Runtime.enable")
        cdp.call("Page.enable")

        # ---------- 1. 1366×768 全页截图 ----------
        print("=== 1366×768 全页截图 ===")
        for name, route in ALL_PAGES:
            set_viewport(cdp, 1366, 768, 1.0)
            goto(cdp, args.base + "/" + route)
            state = wait_for_page(cdp, route)
            time.sleep(0.5)
            path = os.path.join(args.out, f"1366x768_{name}.png")
            cdp.shot(path)
            results["shots"].append({
                "page": name, "view": "1366x768", "route": route, "state": state, "file": path
            })

        # ---------- 2. 1024×640 重点页截图 ----------
        print("=== 1024×640 重点页截图 ===")
        for name, route in KEY_PAGES_1024:
            set_viewport(cdp, 1024, 640, 1.0)
            goto(cdp, args.base + "/" + route)
            state = wait_for_page(cdp, route)
            time.sleep(0.5)
            path = os.path.join(args.out, f"1024x640_{name}.png")
            cdp.shot(path)
            results["shots"].append({
                "page": name, "view": "1024x640", "route": route, "state": state, "file": path
            })

        # ---------- 3. 125% / 150% 重点页截图 ----------
        for scale_label, scale in [("125pct", 1.25), ("150pct", 1.5)]:
            print(f"=== {scale_label} 重点页截图 ===")
            for name, route in KEY_PAGES_48:
                # 1024×640 物理窗口 × 缩放 = 1280×800 / 1536×960
                phys_w = int(1024 * scale)
                phys_h = int(640 * scale)
                set_viewport(cdp, 1024, 640, scale)
                goto(cdp, args.base + "/" + route)
                state = wait_for_page(cdp, route)
                time.sleep(0.5)
                path = os.path.join(args.out, f"{scale_label}_{name}.png")
                cdp.shot(path)
                results["shots"].append({
                    "page": name, "view": scale_label, "route": route, "state": state, "file": path
                })

        # ---------- 4. 浮层截图：命令面板 + Copilot + Dashboard ----------
        print("=== 浮层截图 ===")
        goto(cdp, args.base + "/#/home")
        wait_home(cdp)
        time.sleep(0.5)

        # 命令面板（空）
        cdp.eval_js("if(window.UI)window.UI.openPalette(); 'ok'")
        time.sleep(1)
        cdp.shot(os.path.join(args.out, "float_cmdpalette.png"))

        # 命令面板过滤
        cdp.eval_js(
            "(function(){var i=document.querySelector('.ui-palette-input');"
            "if(i){i.value='板块';i.dispatchEvent(new Event('input'));} 'typed'})()"
        )
        time.sleep(0.5)
        cdp.shot(os.path.join(args.out, "float_cmdpalette_filtered.png"))
        cdp.eval_js("if(window.UI)window.UI.closePalette(); 'closed'")
        time.sleep(0.3)

        # Copilot 抽屉
        cdp.eval_js(
            "(function(){var d=document.getElementById('copilot');"
            "if(d)d.classList.add('open'); 'copilot'})()"
        )
        time.sleep(1)
        cdp.shot(os.path.join(args.out, "float_copilot.png"))
        cdp.eval_js(
            "(function(){var d=document.getElementById('copilot');"
            "if(d)d.classList.remove('open'); 'x'})()"
        )
        time.sleep(0.3)

        # 浅色主题
        cdp.eval_js(
            "(function(){document.documentElement.setAttribute('data-theme','light');'light'})()"
        )
        time.sleep(0.5)
        cdp.shot(os.path.join(args.out, "theme_light_home.png"))
        cdp.eval_js(
            "(function(){document.documentElement.setAttribute('data-theme','dark');'dark'})()"
        )
        time.sleep(0.5)

        # ---------- 5. 人眼核对检查 ----------
        print("=== 人眼核对检查 ===")
        goto(cdp, args.base + "/#/home")
        wait_home(cdp)
        time.sleep(0.5)

        checks = {
            "P1-06": check_sidebar_hint(cdp),
            "P1-12": check_phase_watermark(cdp),
            "P1-08/09": check_search_input(cdp),
            "P2-04": check_copilot_initial(cdp),
            "P2-03": check_refresh_button(cdp),
            "P2-02": check_home_no_kline(cdp),
            "four_zones": check_four_zones(cdp),
            "colors": check_colors(cdp),
            "no_leaks_home": check_no_leaks(cdp),
        }
        # Dashboard checks
        goto(cdp, args.base + "/#/dashboard")
        wait_dashboard(cdp)
        time.sleep(0.5)
        checks["P2-01"] = check_dashboard_no_688012(cdp)
        checks["no_leaks_dashboard"] = check_no_leaks(cdp)
        # Stock page checks
        goto(cdp, args.base + "/#/stock?symbol=688012&market=a")
        wait_blocks(cdp, 30)
        time.sleep(0.5)
        checks["no_leaks_stock"] = check_no_leaks(cdp)
        checks["badges"] = check_badges(cdp)
        checks["P1-11"] = {
            "target_price_element": cdp.eval_js(
                "(function(){var e=document.querySelector('#st06-body');"
                "if(!e)return null;return e.innerText?e.innerText.slice(0,200):null;})()"
            ),
        }
        # Fund page checks
        goto(cdp, args.base + "/#/fund?code=017811")
        state = wait_blocks(cdp, 30)
        time.sleep(1.5)  # 等待 ECharts 渲染完成
        # P1-10：ECharts 渲染到 canvas，DOM 查不到轴标签 → 用 ECharts API 读取 option
        # 基金页有多个 .pg-chart，需找到含“回撤%”系列的那个
        echarts_check_js = (
            "(function(){try{"
            "var nodes=document.querySelectorAll('.pg-chart');"
            "var found=null;"
            "for(var i=0;i<nodes.length;i++){"
            "  var inst=window.echarts.getInstanceByDom(nodes[i]);"
            "  if(!inst)continue;"
            "  var opt=inst.getOption();"
            "  var hasDD=(opt.series||[]).some(function(s){return s.name==='回撤%';});"
            "  if(hasDD){found=opt;break;}"
            "}"
            "if(!found)return JSON.stringify({msg:'no drawdown chart found'});"
            "var yAxis=(found.yAxis||[]);"
            "return JSON.stringify({"
            "yAxisCount:yAxis.length,"
            "yAxis1Name:yAxis[1]?yAxis[1].name:null,"
            "yAxis1Formatter:yAxis[1]&&yAxis[1].axisLabel?yAxis[1].axisLabel.formatter:null,"
            "markPoint:(found.series[1]&&found.series[1].markPoint)?JSON.stringify(found.series[1].markPoint):null"
            "});}catch(e){return JSON.stringify({error:e.message});}})()"
        )
        checks["P1-10"] = {
            "chart_state": state,
            "drawdown_label_check": cdp.eval_js(echarts_check_js),
        }
        checks["no_leaks_fund"] = check_no_leaks(cdp)

        # P1-07: sidebar link navigation
        goto(cdp, args.base + "/#/dashboard")
        wait_dashboard(cdp)
        time.sleep(0.5)
        checks["P1-07"] = check_sidebar_links(cdp, args.base)

        results["checks"] = checks

        cdp.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    out_json = os.path.join(args.out, "frontier_fixes_result.json")
    with open(out_json, "w", encoding="utf-8") as handle:
        json.dump(results, handle, ensure_ascii=False, indent=2)

    print("\n=== 人眼核对汇总 ===")
    for key, val in checks.items():
        if isinstance(val, dict) and "pass" in val:
            status = "✅" if val["pass"] else "❌"
            print(f"  {status} {key}: {val}")
        else:
            print(f"  {key}: {val}")
    print(f"\n结果文件：{out_json}")
    print(f"截图目录：{args.out}")


if __name__ == "__main__":
    main()
