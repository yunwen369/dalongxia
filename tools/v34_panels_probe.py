#!/usr/bin/env python3
"""v3.4.0 新增功能面板深度探针（真实浏览器 + 真实接口，默认只读）。

覆盖 v3.4.0 恢复的 12 个面板（对应 43 个此前未接端点中的主体）：
板块工具（concept/heatmap/crowding）、组合归因与风险、系统运维（cache/stats·clear、
system/access）、提醒已读、连接器测速、条件选股（status/run/refresh）、AI 测试与摘要、
以及「12 秒实时刷新后面板自愈、输入不丢」这一 v6→v7 最易回归的横切行为。

设计约束
--------
- **默认只读**：不写台账、不改提醒已读状态、不触发快照重建；
  需要覆盖写操作时显式加 ``--write``（会真实改动并提示人工复核）。
- 复用 ``tools/ui_clickthrough.py`` 的 CDP 原语，口径与 ``v6_functional_probe.py`` 一致。
- 结果 JSON 可直接作为回归证据归档。

用法::

    python tools/v34_panels_probe.py --base-url http://127.0.0.1:6901 \
        --token <访问口令> --out evidence/v34_panels.json [--write] [--port 9391]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from typing import Any, Dict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ui_clickthrough as ui  # noqa: E402


def txt(cdp: ui.CDP, expr: str) -> str:
    """取页面文本：None/空 → ""，数值 0 保留为 "0"（避免 ``or ""`` 吞掉 0）。

    Args:
        cdp: CDP 连接。
        expr: JS 表达式。

    Returns:
        str: 文本结果。
    """
    value = ui.eval_js(cdp, expr)
    return "" if value is None else str(value)


def wait_for(cdp: ui.CDP, expr: str, timeout: float = 60.0) -> bool:
    """轮询等待表达式为真。

    Args:
        cdp: CDP 连接。
        expr: JS 表达式。
        timeout: 超时秒数。

    Returns:
        bool: 是否在超时前为真。
    """
    end = time.time() + timeout
    while time.time() < end:
        try:
            if ui.eval_js(cdp, "!!(" + expr + ")"):
                return True
        except Exception:  # noqa: BLE001 - 页面重渲染瞬间上下文失效，重试即可
            pass
        time.sleep(0.4)
    return False


def main() -> int:
    """入口：跑完所有面板检查并输出 JSON。

    Returns:
        int: 0=全部通过，1=存在失败项。
    """
    ap = argparse.ArgumentParser(description="v3.4.0 新增面板深度探针")
    ap.add_argument("--base-url", default="http://127.0.0.1:6901")
    ap.add_argument("--token", default=os.environ.get("OPENBB_ACCESS_PASSWORD", "").strip())
    ap.add_argument("--port", type=int, default=9391, help="CDP 调试端口")
    ap.add_argument("--out", default=os.path.join(HERE, "..", "data", "_tmp", "v34_panels.json"))
    ap.add_argument("--write", action="store_true", help="启用写操作项（清缓存/标已读/重建快照）")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    profile = os.path.join(HERE, "_v34_panels_profile")
    if os.path.isdir(profile):
        shutil.rmtree(profile, ignore_errors=True)

    checks: list[Dict[str, Any]] = []

    def add(cid: str, name: str, ok: bool, detail: str = "") -> None:
        checks.append({"id": cid, "name": name, "ok": bool(ok), "detail": str(detail)[:200]})

    proc = ui.launch_chrome(args.port, profile, 1600, 1000)
    try:
        time.sleep(2.5)
        cdp = ui.CDP(ui.page_ws(args.port))
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": ui.CONSOLE_CAPTURE})
        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": 1600, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
        cdp.call("Page.navigate", {"url": base + "/auth?token=" + args.token})
        time.sleep(2.5)

        # ---------- A. 板块工具（3 个端点） ----------
        cdp.call("Page.navigate", {"url": base + "/#/sectors"})
        ok = wait_for(cdp, "document.querySelector('.v7-sector')")
        add("P01", "板块工具卡注入", ok)
        ok = wait_for(cdp, "document.querySelectorAll('.v7-sector .v7-preview-table tr').length > 50")
        rows = ui.eval_js(cdp, "document.querySelectorAll('.v7-sector .v7-preview-table tr').length")
        add("P02", "概念板块表格有真实行（sector/concept）", ok, "rows=%s" % rows)
        head = txt(cdp, "document.querySelector('.v7-sector .v7-preview-table tr').innerText") or ""
        add("P03", "概念口径表头", "概念" in head and "涨跌幅" in head, head.replace("\n", "|")[:80])

        ui.eval_js(cdp, "document.querySelector('[data-sec-tab=\"板块热力\"]').click()")
        ok = wait_for(cdp, "document.querySelectorAll('.v7-heat-cell').length > 20")
        cells = ui.eval_js(cdp, "document.querySelectorAll('.v7-heat-cell').length")
        up = ui.eval_js(cdp, "document.querySelectorAll('.v7-heat-cell.up').length")
        down = ui.eval_js(cdp, "document.querySelectorAll('.v7-heat-cell.down').length")
        add("P04", "板块热力色块（sector/heatmap）", ok and (up or 0) + (down or 0) > 10,
            "cells=%s up=%s down=%s" % (cells, up, down))

        ui.eval_js(cdp, "document.querySelector('[data-sec-tab=\"拥挤度单查\"]').click()")
        ok = wait_for(cdp, "document.querySelector('#v7-sec-crowd-body .v-kpi')", 120)
        crowd = txt(cdp, "(document.querySelector('#v7-sec-crowd-body')||{}).innerText")
        add("P05", "单板块拥挤度六维（sector/crowding）",
            ok and ("拥挤度评分" in crowd) and ("维度" in crowd), crowd[:90].replace("\n", "|"))

        # ---------- B. 组合归因与风险（2 个端点） ----------
        cdp.call("Page.navigate", {"url": base + "/#/portfolio"})
        ok = wait_for(cdp, "document.querySelector('.v7-pa')")
        add("P06", "组合归因卡注入", ok)
        wait_for(cdp, "document.querySelector('#v7-pa-body') && "
                      "!document.querySelector('#v7-pa-body .loading') && "
                      "(document.querySelector('#v7-pa-body').innerText||'').length > 10", 60)
        body = txt(cdp, "(document.querySelector('#v7-pa-body')||{}).innerText")
        add("P07", "归因有内容（台账空时为可读空态）",
            ("持仓数" in body) or ("台账为空" in body), body[:90].replace("\n", "|"))
        ui.eval_js(cdp, "document.querySelector('[data-pa-tab=\"风险\"]').click()")
        ok = wait_for(cdp, "document.querySelector('#v7-pa-body') && !document.querySelector('#v7-pa-body .loading')", 60)
        risk = txt(cdp, "(document.querySelector('#v7-pa-body')||{}).innerText")
        add("P08", "风险页签渲染（portfolio/risk）",
            ok and (("夏普" in risk) or ("台账为空" in risk)), risk[:90].replace("\n", "|"))

        # ---------- C. 系统运维（3 个端点） ----------
        cdp.call("Page.navigate", {"url": base + "/#/settings"})
        ok = wait_for(cdp, "document.querySelector('.v7-sys .v-kpi')")
        sysbox = txt(cdp, "(document.querySelector('#v7-sys-body')||{}).innerText")
        add("P09", "缓存统计渲染（cache/stats）", ok and ("内存缓存" in sysbox) and ("MB" in sysbox),
            sysbox[:90].replace("\n", "|"))
        add("P10", "访问入口+二维码（system/access）",
            "访问入口" in sysbox and bool(ui.eval_js(cdp, "document.querySelectorAll('#v7-sys-body img[alt=QR]').length")),
            "")
        if args.write:
            ui.eval_js(cdp, "document.querySelector('[data-act=\"sys-cache-clear\"]').click()")
            msg = ""
            for _ in range(20):
                time.sleep(0.25)
                msg = txt(cdp, "(document.querySelector('.v7-toast')||{}).innerText")
                if msg:
                    break
            add("P11", "清空缓存执行（cache/clear）", "已清空缓存" in msg, msg)
        else:
            add("P11", "清空缓存（--write 未开启，跳过）", True, "skipped by design")

        # ---------- D. 提醒已读（1 个端点） ----------
        cdp.call("Page.navigate", {"url": base + "/#/alerts"})
        ok = wait_for(cdp, "document.querySelector('.v7-alerts-bar')")
        unread = txt(cdp, "(document.querySelector('#v7-alerts-unread')||{}).textContent")
        add("P12", "提醒工具条与未读数", ok and unread.strip().isdigit(), "unread=%s" % unread)
        if args.write:
            ui.eval_js(cdp, "document.querySelector('[data-act=\"alerts-read-all\"]').click()")
            time.sleep(4)
            after = txt(cdp, "(document.querySelector('#v7-alerts-unread')||{}).textContent")
            hint = txt(cdp, "(document.querySelector('#v7-alerts-hint')||{}).innerText")
            add("P13", "全部标为已读（alerts/events/read）",
                after.strip() == "0" and "已标记" in hint, "unread=%s hint=%s" % (after, hint))
        else:
            add("P13", "标为已读（--write 未开启，跳过）", True, "skipped by design")

        # ---------- E. 连接器测速（1 个端点） ----------
        cdp.call("Page.navigate", {"url": base + "/#/connectors"})
        ok = wait_for(cdp, "document.querySelector('.v7-cn-test')")
        add("P14", "连通性自检卡注入", ok)
        ui.eval_js(cdp, "document.querySelector('[data-act=\"cn-test-all\"]').click()")
        ok = wait_for(cdp, "document.querySelector('#v7-cn-sum') && /完成：可用 \\d+/.test(document.querySelector('#v7-cn-sum').innerText)", 150)
        summary = txt(cdp, "(document.querySelector('#v7-cn-sum')||{}).innerText")
        rows = ui.eval_js(cdp, "document.querySelectorAll('#v7-cn-test-tbl tr').length - 1")
        add("P15", "逐源真实探测（connectors/test）", ok and (rows or 0) >= 3,
            "%s rows=%s" % (summary, rows))

        # ---------- F. 条件选股（3 个端点） ----------
        cdp.call("Page.navigate", {"url": base + "/#/screener"})
        ok = wait_for(cdp, "document.querySelector('.v7-screener .v-kpi')")
        snap = txt(cdp, "(document.querySelector('#v7-scr-status')||{}).innerText")
        add("P16", "快照状态（screener/status）", ok and ("覆盖个股" in snap), snap[:90].replace("\n", "|"))
        ui.eval_js(cdp, """
          (function(){
            var f=document.querySelectorAll('.scr-factor'), o=document.querySelectorAll('.scr-op'), v=document.querySelectorAll('.scr-value');
            if (!f.length) { return; }
            f[0].value='pe'; o[0].value='lte'; v[0].value='15';
            document.querySelector('[data-act="scr-run"]').click();
          })()
        """)
        ok = wait_for(cdp, "document.querySelector('#v7-scr-body .v7-preview-table tr')", 90)
        res = txt(cdp, "(document.querySelector('#v7-scr-body')||{}).innerText")
        add("P17", "条件筛选出结果（screener/run）", ok and "命中" in res, res[:90].replace("\n", "|"))
        if args.write:
            ui.eval_js(cdp, "document.querySelector('[data-act=\"scr-refresh\"]').click()")
            msg = ""
            for _ in range(20):
                time.sleep(0.25)
                msg = txt(cdp, "(document.querySelector('.v7-toast')||{}).innerText")
                if msg:
                    break
            add("P18", "刷新快照（screener/refresh）", ("已启动" in msg) or ("构建" in msg), msg)
        else:
            add("P18", "刷新快照（--write 未开启，跳过）", True, "skipped by design")

        # ---------- G. AI（2 个端点） ----------
        cdp.call("Page.navigate", {"url": base + "/#/settings"})
        ok = wait_for(cdp, "document.querySelector('.v7-ai-test')")
        hint = txt(cdp, "(document.querySelector('#v7-ai-test-body')||{}).innerText")
        add("P19", "AI 测试卡（未配置给引导）", ok and ("未配置" in hint or "密钥" in hint), hint[:90])
        ui.eval_js(cdp, """
          (function(){
            var b=document.getElementById('v7-ai-base'); if (b) { b.value='https://example.com/v1'; }
            var m=document.getElementById('v7-ai-model'); if (m) { m.value='demo-model'; }
            document.querySelector('[data-act="ai-test"]').click();
          })()
        """)
        ok = wait_for(cdp, "document.querySelector('#v7-ai-test-body .v-empty') || document.querySelector('#v7-ai-test-body .v-kpi')", 60)
        res = txt(cdp, "(document.querySelector('#v7-ai-test-body')||{}).innerText")
        add("P20", "AI 测试给明确结果（ai/test）", ok and len(res.strip()) > 4, res[:100].replace("\n", "|"))

        cdp.call("Page.navigate", {"url": base + "/#/news"})
        wait_for(cdp, "document.querySelector('.v7-news')")
        ui.eval_js(cdp, """
          (function(){
            var c=document.getElementById('v7-news-code');
            if (c) { c.value='600519'; c.dispatchEvent(new Event('input', {bubbles:true})); }
            document.querySelector('[data-news-tab="AI 摘要"]').click();
          })()
        """)
        ok = wait_for(cdp, "document.querySelector('#v7-news-body .v-empty')", 60)
        res = txt(cdp, "(document.querySelector('#v7-news-body')||{}).innerText")
        add("P21", "AI 摘要不造假（未配置给引导）", ok and ("未配置" in res or "正文" in res), res[:100].replace("\n", "|"))

        # ---------- H. 12 秒刷新横切 ----------
        time.sleep(14)
        kept = txt(cdp, "(document.getElementById('v7-news-code')||{}).value")
        add("P22", "12 秒刷新后输入不丢（资讯页）", kept == "600519", "code=%s" % kept)
        cdp.call("Page.navigate", {"url": base + "/#/sectors"})
        ok = wait_for(cdp, "document.querySelector('#v7-sec-body') && "
                      "(document.querySelector('#v7-sec-body').innerText||'').length > 50", 90)
        time.sleep(14)  # 跨过 12 秒刷新，面板应被重新注入并重新取数
        ok2 = wait_for(cdp, "document.querySelector('#v7-sec-body') && "
                       "(document.querySelector('#v7-sec-body').innerText||'').length > 50", 60)
        cards = ui.eval_js(cdp, "document.querySelectorAll('.v7-sector').length")
        add("P23", "刷新后面板自愈且有数据、不重复注入", ok and ok2 and cards == 1,
            "cards=%s len=%s" % (cards, len(txt(cdp, "(document.querySelector('#v7-sec-body')||{}).innerText"))))

        errs = ui.eval_js(cdp, "(window.__CONSOLE_ERRORS__||[]).slice(0,8)") or []
        add("P24", "全程控制台无错误", len(errs) == 0, json.dumps(errs, ensure_ascii=False)[:200])
    finally:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass

    passed = sum(1 for item in checks if item["ok"])
    payload = {"summary": {"total": len(checks), "passed": passed,
                           "failed": len(checks) - passed, "write_mode": args.write},
               "checks": checks}
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(json.dumps(payload["summary"], ensure_ascii=False))
    for item in checks:
        if not item["ok"]:
            print("  FAIL", item["id"], item["name"], "|", item["detail"])
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
