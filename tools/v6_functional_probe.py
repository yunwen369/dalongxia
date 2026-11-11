#!/usr/bin/env python3
"""V6 定稿页功能逐项测试（Edge headless + CDP）。

覆盖（能实现即判定，未实现即 FAIL，不预设结论）：
  F01 侧栏分组折叠可恢复        F02 顶栏 8 组点击跳转
  F03 侧栏点击切换视图 + active  F04 面包屑 + 页面标题
  F05 三皮肤切换 + 本地记忆      F06 V6_LIVE 12s 自动刷新
  F07 组件库 49 卡 + 添加弹窗 + 确认计数
  F08 Esc 关闭弹窗              F09 Ctrl+M 主题切换
  F10 Ctrl+K 命令面板           F11 Ctrl+F 页内搜索
  F12 排行榜表格渲染            F13 数据源角标非演示
  F14 首页半环 gauge 渲染        F15 K 线 ECharts canvas
  F16 全程控制台无错误

用法：
  ./.venv/Scripts/python.exe tools/v6_functional_probe.py --base-url http://127.0.0.1:6903
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ui_clickthrough", os.path.join(HERE, "ui_clickthrough.py"))
ui = importlib.util.module_from_spec(spec)
sys.modules["ui_clickthrough"] = ui
spec.loader.exec_module(ui)


def js(cdp, expr):
    return ui.eval_js(cdp, expr)


def key(cdp, key_name: str, code: str, ctrl: bool = False) -> None:
    """派发一次按键（含可选的 Ctrl 修饰）。"""
    mods = 2 if ctrl else 0  # 2 = Ctrl
    base = {"key": key_name, "code": code, "windowsVirtualKeyCode": ord(code.upper()) if len(code) == 1 else 27,
            "modifiers": mods}
    cdp.call("Input.dispatchKeyEvent", dict(base, type="keyDown"))
    cdp.call("Input.dispatchKeyEvent", dict(base, type="keyUp"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:6903")
    ap.add_argument("--port", type=int, default=9359)
    ap.add_argument("--out", default=os.path.join(HERE, "..", "data", "_tmp", "v6_functional.json"))
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    token = os.environ.get("OPENBB_ACCESS_PASSWORD", "").strip()
    profile = os.path.join(HERE, "_v6_func_profile")
    if os.path.isdir(profile):
        shutil.rmtree(profile, ignore_errors=True)

    checks = []

    def add(cid, name, ok, detail=""):
        checks.append({"id": cid, "name": name, "ok": bool(ok), "detail": str(detail)[:180]})

    proc = ui.launch_chrome(args.port, profile, 1440, 900)
    try:
        time.sleep(2.5)
        cdp = ui.CDP(ui.page_ws(args.port))
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": ui.CONSOLE_CAPTURE})
        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        cdp.call("Page.navigate", {"url": base + "/auth?token=" + token})
        time.sleep(2.5)
        cdp.call("Page.navigate", {"url": base + "/"})
        time.sleep(3.0)

        # F01 侧栏分组折叠（等侧栏渲染完成再测）
        for _ in range(60):
            if js(cdp, "document.querySelectorAll('#side-nav .side-group').length") == 5:
                break
            time.sleep(0.5)
        before = js(cdp, "document.querySelectorAll('#side-nav .side-item').length")
        groups = js(cdp, "document.querySelectorAll('#side-nav .side-group').length")
        js(cdp, "document.querySelectorAll('#side-nav .side-group')[1].click()")
        time.sleep(0.6)
        after_open = js(cdp, "document.querySelectorAll('#side-nav .side-item').length")
        js(cdp, "document.querySelectorAll('#side-nav .side-group')[1].click()")
        time.sleep(0.6)
        after_close = js(cdp, "document.querySelectorAll('#side-nav .side-item').length")
        add("F01", "侧栏分组折叠可恢复", groups == 5 and before == after_close and after_open != before,
            f"groups={groups} before={before} open={after_open} close={after_close}")

        # F02 顶栏 8 组点击跳转
        tops = js(cdp, "document.querySelectorAll('.topnav button').length")
        js(cdp, "document.querySelectorAll('.topnav button')[1].click()")
        time.sleep(1.2)
        hash1 = js(cdp, "location.hash")
        add("F02", "顶栏 8 组点击跳转", tops == 8 and hash1.startswith("#/"), f"tops={tops} hash={hash1}")

        # F03 侧栏点击 + active（2026-11-12：改独立起点，避免与 F01/F02 的分组展开状态耦合）
        js(cdp, "location.hash='#/home'")
        for _ in range(24):
            if (js(cdp, "document.querySelectorAll('#side-nav .side-item').length") or 0) > 1:
                break
            time.sleep(0.5)
        clicked = js(cdp, "(function(){var it=document.querySelectorAll('#side-nav .side-item');"
                          "for(var i=0;i<it.length;i++){var v=it[i].getAttribute('data-view');"
                          "if(v&&v!=='home'){it[i].click();return v;}}return null;})()")
        time.sleep(2.0)
        hash3 = js(cdp, "location.hash")
        active = js(cdp, "document.querySelectorAll('#side-nav .side-item.active').length")
        add("F03", "侧栏点击切换视图并高亮",
            bool(clicked) and hash3 == "#/" + str(clicked) and active == 1,
            f"clicked={clicked} hash={hash3} active={active}")

        # F04 面包屑 + 标题
        js(cdp, "location.hash='#/sectors'")
        time.sleep(2.2)
        title = js(cdp, "(document.getElementById('view-title')||{}).textContent")
        crumb = js(cdp, "(document.getElementById('view-crumb')||{}).textContent")
        add("F04", "面包屑与页面标题", title == "板块行情" and "板块" in (crumb or ""),
            f"title={title} crumb={crumb}")

        # F05 三皮肤 + 记忆
        seen = {}
        for skin in ("dark", "light", "v6"):
            js(cdp, "document.querySelector('.skin-switch [data-skin=\"" + skin + "\"]').click()")
            time.sleep(0.7)
            seen[skin] = js(cdp, "document.body.getAttribute('data-theme')")
        stored = js(cdp, "localStorage.getItem('openbb-theme')")
        add("F05", "三皮肤切换（v6/dark/light）",
            seen.get("dark") is None and seen.get("light") == "light" and seen.get("v6") == "v6",
            f"seen={seen}")
        add("F05b", "主题选择本地记忆", stored == "v6", f"localStorage openbb-theme={stored}")

        # F06 动态刷新（等 13s 观察 tick 自增）
        t0 = js(cdp, "(window.V6_LIVE||{}).ticks")
        time.sleep(13.0)
        t1 = js(cdp, "(window.V6_LIVE||{}).ticks")
        add("F06", "V6_LIVE 12s 自动刷新", isinstance(t1, int) and isinstance(t0, int) and t1 > t0,
            f"ticks {t0} -> {t1}")

        # F07 组件库 49 卡 + 添加弹窗 + 计数
        js(cdp, "location.hash='#/widget-library'")
        time.sleep(2.5)
        cards = js(cdp, "document.querySelectorAll('.widget-card').length")
        js(cdp, "document.querySelector('.widget-add').click()")
        time.sleep(0.6)
        opened = js(cdp, "!document.getElementById('v6-add-modal').hidden")
        opts = js(cdp, "document.querySelectorAll('#v6-add-target option').length")

        # F08 Esc 关闭（在弹窗打开时测）
        key(cdp, "Escape", "Escape")
        time.sleep(0.5)
        esc_closed = js(cdp, "document.getElementById('v6-add-modal').hidden")
        add("F08", "Esc 关闭弹窗", esc_closed, "Escape 未关闭弹窗（无监听）" if not esc_closed else "已关闭")

        js(cdp, "if(document.getElementById('v6-add-modal').hidden){document.querySelector('.widget-add').click()}")
        time.sleep(0.4)
        js(cdp, "document.getElementById('v6-add-target').value='__dashboard__'")
        js(cdp, "document.getElementById('v6-add-ok').click()")
        time.sleep(0.6)
        adds = js(cdp, "(window.V6_ADDS||[]).length")
        count_txt = js(cdp, "(document.getElementById('v6-add-count')||{}).textContent")
        add("F07", "组件库 49 卡 + 添加弹窗 + 确认计数",
            cards == 49 and opened and opts == 30 and adds >= 1,
            f"cards={cards} opened={opened} opts={opts} adds={adds} count={count_txt}")

        # F09 Ctrl+M 主题切换
        theme_before = js(cdp, "document.body.getAttribute('data-theme')")
        key(cdp, "m", "KeyM", ctrl=True)
        time.sleep(0.6)
        theme_after = js(cdp, "document.body.getAttribute('data-theme')")
        add("F09", "Ctrl+M 主题切换", theme_before != theme_after,
            f"{theme_before} -> {theme_after}" if theme_before != theme_after else "无监听（未实现）")

        # F10 Ctrl+K 命令面板
        key(cdp, "k", "KeyK", ctrl=True)
        time.sleep(0.6)
        panel = js(cdp, "!!document.querySelector('.command-palette,#command-palette,[class*=palette]')")
        add("F10", "Ctrl+K 命令面板", panel, "未找到命令面板元素")

        # F11 Ctrl+F 页内搜索
        key(cdp, "f", "KeyF", ctrl=True)
        time.sleep(0.6)
        search = js(cdp, "!!document.querySelector('.page-search,#page-search,[class*=search-box]')")
        add("F11", "Ctrl+F 页内搜索", search, "未找到页内搜索元素")

        # F12 排行榜表格
        js(cdp, "location.hash='#/home'")
        time.sleep(2.5)
        rows = js(cdp, "document.querySelectorAll('#rank-body tr').length")
        updown = js(cdp, "document.querySelectorAll('#rank-body .up, #rank-body .down').length")
        add("F12", "首页排行榜表格渲染", rows >= 5 and updown >= 1, f"rows={rows} colored={updown}")

        # F13 数据源角标
        demo_tags = js(cdp, "document.querySelectorAll('.src-tag.demo').length")
        tags = js(cdp, "document.querySelectorAll('.src-tag').length")
        add("F13", "数据源角标非演示兜底", tags >= 5 and demo_tags == 0, f"tags={tags} demo={demo_tags}")

        # F14 gauge
        arcs = js(cdp, "document.querySelectorAll('#gauge-svg path').length")
        gauge_txt = js(cdp, "(document.querySelector('#gauge-svg text')||{}).textContent")
        add("F14", "首页半环 gauge 渲染", arcs >= 2 and bool(gauge_txt), f"arcs={arcs} text={gauge_txt}")

        # F15 K 线 canvas
        canvas = js(cdp, "document.querySelectorAll('#kline-chart canvas').length")
        add("F15", "K 线 ECharts 渲染", canvas >= 1, f"canvas={canvas}")

        # F16 控制台
        errs = js(cdp, "(window.__cb_errors||[]).filter(function(e){return e.kind==='error'||e.kind==='onerror';}).length")
        add("F16", "全程控制台无错误", errs == 0, f"errors={errs}")
    finally:
        proc.terminate()

    passed = sum(1 for c in checks if c["ok"])
    summary = {"total": len(checks), "passed": passed, "failed": len(checks) - passed,
               "failed_ids": [c["id"] for c in checks if not c["ok"]]}
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "checks": checks}, fh, ensure_ascii=False, indent=2)
    print(json.dumps({"summary": summary, "checks": checks}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
