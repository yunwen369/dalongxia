# -*- coding: utf-8 -*-
"""诊断脚本：对个股详情页各 widget 接口做健康巡检（不改动任何业务文件）。

用途：定位「数据源暂不可用」的具体接口清单，区分「个股可用 / ETF 不可用」。
运行：  .venv\\Scripts\\python.exe tools\\diag_widget_health.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:6900"
TOKEN = os.environ.get("OPENBB_TOKEN", "")
SAMPLES = {"个股-贵州茅台": "600519", "ETF-通信ETF国泰": "515880"}


def get(path: str, timeout: int = 30) -> tuple[int, dict]:
    """调用本机 API，返回 (HTTP 状态码, JSON 体)。"""
    req = urllib.request.Request(BASE + path)
    if TOKEN:
        req.add_header("Authorization", "Bearer " + TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"error": "HTTP %s" % e.code}
    except Exception as e:  # noqa: BLE001 网络/超时统一归为不可用
        return 0, {"error": "%s: %s" % (type(e).__name__, e)}


def main() -> None:
    status, body = get("/api/widgets")
    widgets = body.get("data", {}).get("items", [])
    # 只取个股详情页（#/stock）的 widget
    targets = [w for w in widgets if str(w.get("page", "")).startswith("#/stock")]
    print("个股详情页 widget 数：%d" % len(targets))

    apis: list[str] = []
    for w in targets:
        for a in w.get("api") or []:
            if a not in apis:
                apis.append(a)
    print("涉及接口数：%d\n" % len(apis))

    rows = []
    for api in apis:
        row = {"api": api}
        for label, sym in SAMPLES.items():
            if "{symbol}" in api:
                url = api.replace("{symbol}", sym)
            elif "symbol=" in api:
                url = api
            elif "?" in api:
                url = api + "&symbol=" + sym
            else:
                url = api + "?symbol=" + sym
            code, res = get(url)
            row[label] = res.get("error_code") or "OK"
            if code == 0:
                row[label] = "TIMEOUT"
        rows.append(row)

    w1, w2 = list(SAMPLES.keys())
    print("%-46s %-14s %-14s" % ("接口", w1, w2))
    print("-" * 80)
    for r in rows:
        print("%-46s %-14s %-14s" % (r["api"][:46], str(r[w1])[:14], str(r[w2])[:14]))

    bad_etf = [r["api"] for r in rows if r[w2] not in ("OK",)]
    bad_stock = [r["api"] for r in rows if r[w1] not in ("OK",)]
    print("\nETF 异常接口 %d 个：%s" % (len(bad_etf), bad_etf))
    print("个股异常接口 %d 个：%s" % (len(bad_stock), bad_stock))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
