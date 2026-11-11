#!/usr/bin/env python3
"""V6 定稿落地截图验收（带严格就绪闸门）。

修正背景：首版对拍脚本仅 sleep 固定时长，导致 ①`#/market` 拍到首页、②拍到
「统计中…/加载中…」中间态，比对结果无效。本版闸门要求连续两次满足：
    hash 命中 + 目标容器可见 + 标题匹配（非首页）+ 无 .loading + 无加载文案 + 文本长度稳定

比对指标：
    size_match  尺寸一致（1342×676，与定稿原型图同规格）
    mae         灰度平均绝对误差
    diff_ratio  灰度差 >24 的像素占比
    corr        64×32 灰度相关（结构一致性）
    hist        32bin 颜色直方图余弦相似度（配色一致性）

用法：
    ./.venv/Scripts/python.exe tools/v6_shot_compare.py --base-url http://127.0.0.1:6903
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import io
import json
import math
import os
import shutil
import sys
import time

from PIL import Image, ImageChops

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("ui_clickthrough", os.path.join(HERE, "ui_clickthrough.py"))
ui = importlib.util.module_from_spec(_spec)
sys.modules["ui_clickthrough"] = ui
_spec.loader.exec_module(ui)

ROOT = os.path.dirname(HERE)
PROTO = os.path.join(ROOT, "data", "_tmp", "proto_views")
W, H = 1342, 676
VIEWS = [
    "home", "today", "market", "dashboard", "dashboard-manage", "widget-library",
    "watchlist", "news", "sectors", "stock", "fin", "ann", "tools", "cand",
    "charting", "sector-val", "macro", "valuation", "crowding", "screener",
    "lhb", "fund-combo", "portfolio", "fund", "yjb", "mgr",
    "alerts", "connectors", "settings",
]

READY_JS = ("JSON.stringify((function(){"
            "var g=document.getElementById('view-generic'),h=document.getElementById('home-main');"
            "var vis=g&&!g.hidden, home=h&&!h.hidden;"
            "var t=(document.getElementById('view-title')||{}).textContent||'';"
            "var scope=vis?document.getElementById('view-body'):h;"
            "var txt=scope?scope.innerText:'';"
            "var ld=scope?scope.querySelectorAll('.loading').length:-1;"
            "var busy=/加载中|统计中|生成中|榜单加载中/.test(txt);"
            "return {hash:location.hash,vis:vis,home:home,title:t,ld:ld,busy:busy,len:txt.length};})())")


def state(cdp) -> dict:
    raw = ui.eval_js(cdp, READY_JS)
    try:
        return json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception:
        return {}


def wait_ready(cdp, vid: str, expect_title: str, timeout: float = 120.0) -> tuple:
    """连续两次满足闸门才算就绪；返回 (ok, 最后状态)。"""
    deadline = time.time() + timeout
    stable = 0
    last = {}
    while time.time() < deadline:
        last = state(cdp)
        ok = (
            last.get("hash") == "#/" + vid
            and (last.get("home") is True if vid == "home" else last.get("vis") is True)
            and (vid == "home" or last.get("title") == expect_title)
            and last.get("ld") == 0
            and last.get("busy") is False
            and (last.get("len") or 0) > 40
        )
        stable = stable + 1 if ok else 0
        if stable >= 2:
            return True, last
        time.sleep(0.6)
    return False, last


def grab(cdp) -> Image.Image:
    data = cdp.call("Page.captureScreenshot", {"format": "png"})["result"]["data"]
    return Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB")


def metrics(proto: Image.Image, live: Image.Image) -> dict:
    if proto.size != live.size:
        live = live.resize(proto.size)
    d = ImageChops.difference(proto.convert("L"), live.convert("L"))
    hist = d.histogram()
    total = proto.size[0] * proto.size[1]
    mae = sum(i * hist[i] for i in range(256)) / total
    over = sum(hist[24:])
    ga = list(proto.convert("L").resize((64, 32)).getdata())
    gb = list(live.convert("L").resize((64, 32)).getdata())
    ma, mb = sum(ga) / len(ga), sum(gb) / len(gb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ga, gb))
    da = math.sqrt(sum((x - ma) ** 2 for x in ga)) or 1.0
    db = math.sqrt(sum((y - mb) ** 2 for y in gb)) or 1.0

    def vec(img):
        raw = []
        for ch in range(3):
            raw.extend(img.histogram()[ch * 256:(ch + 1) * 256])
        return [sum(raw[i:i + 8]) for i in range(0, 768, 8)]
    va, vb = vec(proto), vec(live)
    dot = sum(x * y for x, y in zip(va, vb))
    na = math.sqrt(sum(x * x for x in va)) or 1.0
    nb = math.sqrt(sum(y * y for y in vb)) or 1.0
    return {"mae": round(mae, 2), "diff_ratio": round(over / total, 4),
            "corr": round(num / (da * db), 4), "hist": round(dot / (na * nb), 4)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:6903")
    ap.add_argument("--port", type=int, default=9363)
    ap.add_argument("--shots", default=os.path.join(ROOT, "data", "_tmp", "v6_shots_final"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "_tmp", "v6_shot_final.json"))
    args = ap.parse_args()

    token = os.environ.get("OPENBB_ACCESS_PASSWORD", "").strip()
    base = args.base_url.rstrip("/")
    profile = os.path.join(HERE, "_v6_final_profile")
    if os.path.isdir(profile):
        shutil.rmtree(profile, ignore_errors=True)
    os.makedirs(args.shots, exist_ok=True)

    proc = ui.launch_chrome(args.port, profile, 1366, 768)
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
        time.sleep(3.0)

        # 标题期望值从 views.js 取权威值（eval_js 已把 JSON 解析为 dict）
        raw_titles = ui.eval_js(
            cdp, "JSON.stringify(Object.keys(window.V6_VIEWS).reduce(function(a,k){a[k]=window.V6_VIEWS[k].title;return a;},{}))")
        if isinstance(raw_titles, dict):
            titles = raw_titles
        else:
            try:
                titles = json.loads(raw_titles or "{}")
            except Exception:
                titles = {}

        for vid in VIEWS:
            ui.eval_js(cdp, "location.hash='#/" + vid + "'")
            ok, st = wait_ready(cdp, vid, titles.get(vid, ""))
            img = grab(cdp)
            path = os.path.join(args.shots, vid + ".png")
            img.save(path)
            row = {"id": vid, "ready": ok, "state": st, "size": list(img.size)}
            proto_path = os.path.join(PROTO, vid + ".png")
            if os.path.exists(proto_path):
                proto = Image.open(proto_path).convert("RGB")
                row["size_match"] = proto.size == img.size
                row.update(metrics(proto, img))
            rows.append(row)
            print("[%s] %-16s ready=%s size=%s mae=%s diff=%s corr=%s" % (
                "OK " if ok else "!! ", vid, ok, row.get("size_match"),
                row.get("mae"), row.get("diff_ratio"), row.get("corr")), flush=True)
    finally:
        proc.terminate()

    ok_rows = [r for r in rows if "diff_ratio" in r]
    summary = {
        "views": len(rows),
        "ready": sum(1 for r in rows if r["ready"]),
        "size_match": sum(1 for r in ok_rows if r.get("size_match")),
        "mean_diff_ratio": round(sum(r["diff_ratio"] for r in ok_rows) / len(ok_rows), 4) if ok_rows else None,
        "mean_corr": round(sum(r["corr"] for r in ok_rows) / len(ok_rows), 4) if ok_rows else None,
        "mean_hist": round(sum(r["hist"] for r in ok_rows) / len(ok_rows), 4) if ok_rows else None,
        "high_diff_views": [r["id"] for r in ok_rows if r["diff_ratio"] > 0.08],
        "not_ready": [r["id"] for r in rows if not r["ready"]],
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": rows}, f, ensure_ascii=False, indent=2)
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
