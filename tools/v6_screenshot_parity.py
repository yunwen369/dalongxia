#!/usr/bin/env python3
"""V6 定稿页截图验收：与《V6定稿原型图》逐页比对。

做法：
  1) Edge headless，视口锁定 1342x676（与定稿原型图完全同尺寸）；
  2) 鉴权后逐视图 hash 导航、截图（captureBeyondViewport=False，仅可视区）；
  3) 与原型 PNG 逐像素比对：尺寸、MAE、差异像素占比；
  4) 生成并排对照图（左=原型，右=本地产物，下=差异热区）。

用法：
  ./.venv/Scripts/python.exe tools/v6_screenshot_parity.py --base-url http://127.0.0.1:6903
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import shutil
import sys
import time

from PIL import Image, ImageChops

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location("ui_clickthrough", os.path.join(HERE, "ui_clickthrough.py"))
ui = importlib.util.module_from_spec(spec)
sys.modules["ui_clickthrough"] = ui
spec.loader.exec_module(ui)

VIEW_IDS = [
    "home", "today", "market", "dashboard", "dashboard-manage", "widget-library",
    "watchlist", "news", "sectors", "stock", "fin", "ann", "tools", "cand",
    "charting", "sector-val", "macro", "valuation", "crowding", "screener",
    "lhb", "fund-combo", "portfolio", "fund", "yjb", "mgr",
    "alerts", "connectors", "settings",
]
W, H = 1342, 676

EXPECT_TITLE = {
    "home": None, "today": "今日看点", "market": "市场总览", "dashboard": "我的仪表板",
    "dashboard-manage": "仪表板管理", "widget-library": "组件库", "watchlist": "我的自选",
    "news": "新闻资讯", "sectors": "板块行情", "stock": "个股详情", "fin": "财报摘要",
    "ann": "公告库", "tools": "投研工具", "cand": "候选池", "charting": "图表分析",
    "sector-val": "板块估值", "macro": "宏观概览", "valuation": "估值分位",
    "crowding": "拥挤度", "screener": "智能选股", "lhb": "龙虎榜",
    "fund-combo": "基金组合", "portfolio": "我的持仓", "fund": "基金详情",
    "yjb": "养基宝持仓", "mgr": "经理画像", "alerts": "提醒中心",
    "connectors": "连接器", "settings": "系统设置",
}


def wait_until(cdp, expr: str, timeout: float, interval: float = 0.5) -> bool:
    """轮询直到表达式为真或超时。"""
    end = time.time() + timeout
    while time.time() < end:
        try:
            if ui.eval_js(cdp, expr) is True:
                return True
        except Exception:  # noqa: BLE001 - 页面切换瞬间可能取不到
            pass
        time.sleep(interval)
    return False


def wait_home_ready(cdp, timeout: float = 150.0) -> bool:
    """首页真正就绪：排行榜有行、无 loading 占位、gauge 有数字。"""
    return wait_until(
        cdp,
        "(document.querySelectorAll('#rank-body tr').length > 1) &&"
        "(document.querySelectorAll('.loading').length === 0) &&"
        "/\\d/.test((document.querySelector('#gauge-svg text')||{}).textContent||'')",
        timeout,
    )


def wait_view_ready(cdp, view_id: str, timeout: float = 120.0) -> bool:
    """通用视图就绪：标题匹配且无 loading 占位（或超时容忍）。"""
    want = EXPECT_TITLE.get(view_id)
    if not want:
        return True
    title_ok = "(document.getElementById('view-title')||{}).textContent === " + json.dumps(want, ensure_ascii=False)
    ready = wait_until(cdp, title_ok + " && document.querySelectorAll('.loading').length === 0", timeout)
    if not ready:
        return wait_until(cdp, title_ok, 5.0)
    return True


def grab(cdp, path: str) -> bool:
    """截取可视区并落盘。"""
    res = cdp.call("Page.captureScreenshot", {"captureBeyondViewport": False, "format": "png"})
    data = res.get("result", {}).get("data", "")
    if not data:
        return False
    with open(path, "wb") as fh:
        fh.write(base64.b64decode(data))
    return True


def diff_metrics(a_path: str, b_path: str) -> dict:
    """尺寸 + 灰度 MAE + 差异像素占比（阈值 24/255）。"""
    a = Image.open(a_path).convert("RGB")
    b = Image.open(b_path).convert("RGB")
    out = {"proto": a.size, "local": b.size, "size_match": a.size == b.size}
    if a.size != b.size:
        b = b.resize(a.size)
    d = ImageChops.difference(a.convert("L"), b.convert("L"))
    hist = d.histogram()
    total = a.size[0] * a.size[1]
    mae = sum(i * hist[i] for i in range(256)) / total
    over = sum(hist[24:])
    out.update({"mae": round(mae, 2), "diff_ratio": round(over / total, 4),
                "diff_px": over, "total_px": total})
    return out


def compose(proto: str, local: str, out_path: str) -> None:
    """并排对照：上=原型 | 本地，下=灰度差异热区。"""
    a = Image.open(proto).convert("RGB")
    b = Image.open(local).convert("RGB")
    if a.size != b.size:
        b = b.resize(a.size)
    diff = ImageChops.difference(a.convert("L"), b.convert("L")).point(lambda v: min(255, v * 3)).convert("RGB")
    canvas = Image.new("RGB", (a.width, a.height * 3 + 16), (16, 16, 16))
    canvas.paste(a, (0, 0))
    canvas.paste(b, (0, a.height + 8))
    canvas.paste(diff, (0, a.height * 2 + 16))
    canvas.save(out_path, quality=88)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:6903")
    ap.add_argument("--port", type=int, default=9358)
    ap.add_argument("--proto", default=os.path.join(ROOT, "data", "_tmp", "proto_views"))
    ap.add_argument("--shots", default=os.path.join(ROOT, "data", "_tmp", "v6_shots_parity"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "_tmp", "v6_shot_parity.json"))
    ap.add_argument("--settle", type=float, default=1.6)
    ap.add_argument("--view-timeout", type=float, default=120.0)
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    token = os.environ.get("OPENBB_ACCESS_PASSWORD", "").strip()
    profile = os.path.join(HERE, "_v6_parity_profile")
    if os.path.isdir(profile):
        shutil.rmtree(profile, ignore_errors=True)
    os.makedirs(args.shots, exist_ok=True)

    proc = ui.launch_chrome(args.port, profile, W, H)
    rows = []
    try:
        time.sleep(2.5)
        cdp = ui.CDP(ui.page_ws(args.port))
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": W, "height": H, "deviceScaleFactor": 1, "mobile": False})
        cdp.call("Page.navigate", {"url": base + "/auth?token=" + token})
        time.sleep(2.5)
        cdp.call("Page.navigate", {"url": base + "/"})
        time.sleep(2.0)
        print("[home] ready=%s" % wait_home_ready(cdp), flush=True)

        for vid in VIEW_IDS:
            ui.eval_js(cdp, "location.hash='#/" + vid + "'")
            ready = wait_home_ready(cdp, 30.0) if vid == "home" else wait_view_ready(cdp, vid, args.view_timeout)
            time.sleep(args.settle)
            local = os.path.join(args.shots, vid + ".png")
            ok = grab(cdp, local)
            proto = os.path.join(args.proto, vid + ".png")
            row = {"id": vid, "shot_ok": ok, "ready": bool(ready)}
            if ok and os.path.exists(proto):
                row.update(diff_metrics(proto, local))
                compose(proto, local, os.path.join(args.shots, "cmp_" + vid + ".jpg"))
            rows.append(row)
            print("[%s] size=%s mae=%s diff=%s" % (
                vid, row.get("size_match"), row.get("mae"), row.get("diff_ratio")), flush=True)
    finally:
        proc.terminate()

    ok_rows = [r for r in rows if "diff_ratio" in r]
    summary = {
        "views": len(rows),
        "compared": len(ok_rows),
        "size_match": sum(1 for r in ok_rows if r.get("size_match")),
        "mean_diff_ratio": round(sum(r["diff_ratio"] for r in ok_rows) / len(ok_rows), 4) if ok_rows else None,
        "max_diff": max(ok_rows, key=lambda r: r["diff_ratio"]) if ok_rows else None,
        "high_diff_views": [r["id"] for r in ok_rows if r["diff_ratio"] > 0.08],
    }
    payload = {"summary": summary, "rows": rows}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
