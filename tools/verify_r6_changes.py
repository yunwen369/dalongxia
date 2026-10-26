# -*- coding: utf-8 -*-
"""第六波改动功能取证（CDP，v1.0.6）：把"改了但要有人眼/机器确认"的点逐条断言。

覆盖：
1. ``#/yjb``：Y-00 首启向导显隐逻辑、Y-05 总资产（元 + ≈万元）、今日收益红涨绿跌、
   Y-06 持仓 14 条且估算涨跌幅非空、估算角标常驻与「一键关闭 → T+1」往返；
2. ``#/settings``：ST-YJ「养基宝连接」卡 + 掩码输入框（type=password）+ 状态 chip；
3. ``#/news``：资讯源 Tab（综合/快讯/全球/板块）四个端点都能出数（FN-03）；
4. ``#/sectors``：S-04 行业与 S-05 概念两张独立排行表同屏（DS-07）；
5. ``#/connectors``：C-01 总览与 C-02 明细两张独立卡（LG-01）；
6. 顺带收集每页 console 错误。

产物：``<out>/r6_functional.json`` + 关键截图。

用法：
    .venv\\Scripts\\python.exe tools\\verify_r6_changes.py --base http://127.0.0.1:6903
"""

from __future__ import annotations

import argparse
import base64
import json
import pathlib
import subprocess
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_OUT = pathlib.Path("F:/workbuddy记录/OpenBB Terminal Pro/B_交付归档/03_测试报告/20261026_r6/evidence")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


class CDP:
    """极简 CDP 客户端（与 tools/evidence_viewports_r5.py 同一实现口径）。"""

    def __init__(self, ws_url: str) -> None:
        """建立 WebSocket 连接。"""
        import websocket  # type: ignore

        self.ws = websocket.create_connection(ws_url, timeout=60)
        self.seq = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        """发送 CDP 命令并等待同 id 响应。"""
        self.seq += 1
        self.ws.send(json.dumps({"id": self.seq, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.seq:
                return msg

    def js(self, expression: str):
        """执行 JS 表达式并返回其值。"""
        res = self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        })
        return (res.get("result") or {}).get("result", {}).get("value")

    def goto(self, url: str, wait: float = 2.6) -> None:
        """跳转并等待渲染。"""
        self.call("Page.navigate", {"url": url})
        time.sleep(wait)

    def shot(self, path: pathlib.Path) -> None:
        """整页截图。"""
        res = self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        path.write_bytes(base64.b64decode(res.get("result", {}).get("data", "")))

    def close(self) -> None:
        """关闭连接。"""
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def _open_target(port: int, deadline: float = 40.0) -> str:
    """等待 Edge 调试端口就绪并取页面 ws 地址。"""
    end = time.time() + deadline
    while time.time() < end:
        try:
            raw = urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3).read()
            for item in json.loads(raw):
                if item.get("type") == "page":
                    return item["webSocketDebuggerUrl"]
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)
    raise RuntimeError("Edge 调试端口未就绪")


def check_yjb(cdp: CDP, base: str, out: pathlib.Path) -> dict:
    """养基宝页：向导显隐 / Y-05 汇总 / Y-06 持仓 / 角标一键关闭往返。"""
    cdp.goto(f"{base}/#/yjb", 3.2)
    res: dict = {}
    res["wizard_hidden"] = cdp.js("document.getElementById('Y-00').hidden === true")
    res["y05_text"] = cdp.js("(document.getElementById('y05-body').innerText||'').slice(0,220)")
    res["y05_has_yuan"] = bool(cdp.js("(document.getElementById('y05-body').innerText||'').includes('155,518.87')"))
    res["y05_has_wan"] = bool(cdp.js("(document.getElementById('y05-body').innerText||'').includes('15.55')"))
    res["y05_badge"] = cdp.js(
        "(function(){var n=document.querySelector('#y05-body .yjb-est-badge');return n?n.innerText.trim():'';})()")
    res["hold_rows"] = cdp.js("document.querySelectorAll('#y06-body tbody tr').length")
    res["hold_pct_none"] = cdp.js(
        "(function(){var c=0;document.querySelectorAll('#y06-body tbody tr').forEach(function(tr){"
        "if((tr.children[1]||{}).innerText && tr.children[1].innerText.indexOf('无数据')>=0){c++;}});return c;})()")
    res["today_income_class"] = cdp.js(
        "(function(){var n=document.querySelectorAll('#y05-body .pg-kpi');if(n.length<2){return '';}"
        "var v=n[1].querySelector('.pg-kpi-v');return v?v.className:'';})()")
    cdp.js("(function(){var n=document.getElementById('Y-05');if(n){n.scrollIntoView({block:'start'});}})()")
    time.sleep(0.6)
    cdp.shot(out / "yjb_y05_y06.png")
    # 一键关闭估算 → T+1
    cdp.js("(function(){var b=document.querySelector('#y05-body .yjb-est-close');if(b){b.click();}})()")
    time.sleep(2.4)
    res["off_badge"] = cdp.js(
        "(function(){var n=document.querySelector('#y05-body .yjb-est-badge');return n?n.innerText.trim():'';})()")
    res["off_income_text"] = cdp.js(
        "(function(){var n=document.querySelectorAll('#y05-body .pg-kpi');if(n.length<2){return '';}"
        "return n[1].querySelector('.pg-kpi-v').innerText;})()")
    res["off_pct_all_empty"] = cdp.js(
        "(function(){var c=0,t=0;document.querySelectorAll('#y06-body tbody tr').forEach(function(tr){t++;"
        "if(tr.children[1] && tr.children[1].innerText.indexOf('无数据')>=0){c++;}});return t?c===t:false;})()")
    res["off_hint"] = cdp.js("(document.getElementById('y06-body').innerText||'').includes('已关闭盘中估算')")
    cdp.shot(out / "yjb_estimates_off_t1.png")
    # 恢复估算
    cdp.js("(function(){var b=document.querySelector('#y05-body .yjb-est-close');if(b){b.click();}})()")
    time.sleep(2.4)
    res["restored_badge"] = cdp.js(
        "(function(){var n=document.querySelector('#y05-body .yjb-est-badge');return n?n.innerText.trim():'';})()")
    return res


def check_settings(cdp: CDP, base: str, out: pathlib.Path) -> dict:
    """设置页：养基宝连接卡 + 掩码输入 + 状态 chip。"""
    cdp.goto(f"{base}/#/settings", 3.0)
    res = {
        "card_exists": cdp.js("!!document.getElementById('set-yjb-body')"),
        "password_inputs": cdp.js("document.querySelectorAll('#set-yjb-secret input[type=password]').length"),
        "chips": cdp.js("(document.getElementById('set-yjb-body').innerText||'').slice(0,180)"),
        "plaintext_guard": cdp.js(
            "(function(){var n=document.querySelectorAll('#set-yjb-secret input');var bad=0;"
            "n.forEach(function(i){if(i.type!=='password'){bad++;}});return bad;})()"),
    }
    cdp.shot(out / "settings_yjb_card.png")
    return res


def check_news(cdp: CDP, base: str, out: pathlib.Path) -> dict:
    """新闻页：四个资讯源都能出条目（FN-03 三孤立端点接入口）。"""
    cdp.goto(f"{base}/#/news", 3.4)
    res: dict = {"tabs": cdp.js("document.querySelectorAll('#N-src .pg-tab').length")}
    for key in ("flash", "global", "sector", "feed"):
        cdp.js(f"(function(){{var b=document.querySelector('#N-src .pg-tab[data-src=\"{key}\"]');"
               f"if(b){{b.click();}}}})()")
        # 上游快讯接口冷启动可能 3–8s：轮询等待，避免误判为"没接上"
        rows = 0
        for _ in range(10):
            time.sleep(1.0)
            rows = cdp.js("document.querySelectorAll('#n02-body .pg-feed-item').length") or 0
            if rows:
                break
        res[f"{key}_items"] = rows
        res[f"{key}_empty"] = cdp.js("(document.getElementById('n02-body').innerText||'').trim().slice(0,60)")
        if key in ("flash", "sector"):
            cdp.shot(out / f"news_src_{key}.png")
    res["failover_note"] = cdp.js("(document.querySelector('#n02-body .pg-state-hint')||{}).innerText || ''")
    return res


def check_sectors(cdp: CDP, base: str, out: pathlib.Path) -> dict:
    """板块页：S-04 行业与 S-05 概念两张独立表同屏。"""
    cdp.goto(f"{base}/#/sectors", 4.2)
    for _ in range(10):
        if (cdp.js("document.querySelectorAll('#s05-body tbody tr').length") or 0) > 0:
            break
        time.sleep(1.0)
    res = {
        "s04_rows": cdp.js("document.querySelectorAll('#s04-body tbody tr').length"),
        "s05_rows": cdp.js("document.querySelectorAll('#s05-body tbody tr').length"),
        "s05_title": cdp.js("(document.querySelector('#S-05 .pg-card-title')||{}).innerText||''"),
        "both_present": cdp.js("!!document.getElementById('S-04') && !!document.getElementById('S-05')"),
        "heat_labels": cdp.js("document.querySelectorAll('#s03-chart canvas').length"),
    }
    cdp.js("(function(){var n=document.getElementById('S-05');if(n){n.scrollIntoView({block:'center'});}})()")
    time.sleep(0.6)
    cdp.shot(out / "sectors_s04_s05.png")
    return res


def check_connectors(cdp: CDP, base: str, out: pathlib.Path) -> dict:
    """连接器页：C-01 与 C-02 独立成卡。"""
    cdp.goto(f"{base}/#/connectors", 3.4)
    res = {
        "c01": cdp.js("!!document.getElementById('c01-body')"),
        "c02": cdp.js("!!document.getElementById('c02-body')"),
        "c02_rows": cdp.js("document.querySelectorAll('#c02-body tbody tr').length"),
        "c02_metrics": cdp.js("document.querySelectorAll('#c01-body .pg-metric-cell').length"),
        "same_parent": cdp.js(
            "document.getElementById('c01-body').closest('.pg-card') === "
            "document.getElementById('c02-body').closest('.pg-card')"),
    }
    cdp.js("(function(){var n=document.getElementById('C-02');if(n){n.scrollIntoView({block:'center'});}})()")
    time.sleep(0.6)
    cdp.shot(out / "connectors_c01_c02.png")
    return res


def main() -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="第六波改动功能取证")
    parser.add_argument("--base", default="http://127.0.0.1:6903", help="被测服务基址")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="证据输出目录")
    parser.add_argument("--cdp-port", type=int, default=9381, help="Edge 远程调试端口")
    args = parser.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    profile = out / "_profile_r6"
    edge = subprocess.Popen(
        [EDGE, "--headless=new", "--disable-gpu", f"--remote-debugging-port={args.cdp_port}",
         f"--user-data-dir={profile}", "--window-size=1366,768",
         "--remote-allow-origins=*", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    errors: list[str] = []
    result: dict = {}
    try:
        cdp = CDP(_open_target(args.cdp_port))
        cdp.call("Runtime.enable")
        cdp.call("Page.enable")
        cdp.call("Log.enable")
        result["yjb"] = check_yjb(cdp, args.base, out)
        result["settings"] = check_settings(cdp, args.base, out)
        result["news"] = check_news(cdp, args.base, out)
        result["sectors"] = check_sectors(cdp, args.base, out)
        result["connectors"] = check_connectors(cdp, args.base, out)
        # 收尾：把缓冲的 console 错误一次性取出
        while True:
            try:
                msg = json.loads(cdp.ws.recv())
            except Exception:  # noqa: BLE001 - 超时即视为收完
                break
            if msg.get("method") == "Runtime.consoleAPICalled" and \
                    (msg.get("params") or {}).get("type") == "error":
                errors.append(str((msg["params"].get("args") or [{}])[0].get("value"))[:200])
        cdp.close()
    finally:
        edge.terminate()
        try:
            edge.wait(timeout=15)
        except subprocess.TimeoutExpired:
            edge.kill()

    result["console_errors"] = errors[:20]
    checks = {
        "yjb_wizard_hidden_when_bound": result["yjb"].get("wizard_hidden") is True,
        "yjb_total_assets_155518_87": result["yjb"].get("y05_has_yuan") is True,
        "yjb_total_assets_approx_wan": result["yjb"].get("y05_has_wan") is True,
        "yjb_badge_present": "非官方" in str(result["yjb"].get("y05_badge")),
        "yjb_hold_rows_14": result["yjb"].get("hold_rows") == 14,
        "yjb_hold_pct_all_filled": result["yjb"].get("hold_pct_none") == 0,
        "yjb_income_red_or_green": any(c in str(result["yjb"].get("today_income_class"))
                                       for c in ("up", "down")),
        "yjb_off_income_blank": result["yjb"].get("off_income_text") in ("无数据", "—", ""),
        "yjb_off_pct_all_blank": result["yjb"].get("off_pct_all_empty") is True,
        "yjb_off_hint_shown": result["yjb"].get("off_hint") is True,
        "yjb_restore_ok": "非官方" in str(result["yjb"].get("restored_badge")),
        "settings_card": result["settings"].get("card_exists") is True,
        "settings_masked_inputs": (result["settings"].get("password_inputs") or 0) >= 1,
        "settings_no_plaintext_input": result["settings"].get("plaintext_guard") == 0,
        "news_src_tabs_4": result["news"].get("tabs") == 4,
        "news_flash_rows": (result["news"].get("flash_items") or 0) > 0,
        "news_global_rows": (result["news"].get("global_items") or 0) > 0,
        "news_sector_rows": (result["news"].get("sector_items") or 0) > 0,
        "sectors_s05_independent": (result["sectors"].get("s05_rows") or 0) > 0
                                   and result["sectors"].get("both_present") is True,
        "connectors_c02_split": result["connectors"].get("c02") is True
                                and result["connectors"].get("same_parent") is False,
    }
    result["checks"] = checks
    result["pass"] = all(checks.values())
    (out / "r6_functional.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 64)
    for name, ok in checks.items():
        print(f"[{'OK' if ok else 'FAIL'}] {name}")
    print("-" * 64)
    print(f"结论：{'全部通过' if result['pass'] else '存在问题'}")
    print(f"console 错误 {len(errors)} 条")
    for item in errors[:5]:
        print("  ·", item)
    print(f"证据：{out / 'r6_functional.json'}")
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
