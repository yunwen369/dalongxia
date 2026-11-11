# -*- coding: utf-8 -*-
"""诊断脚本：全页面接口巡检（只读，不修改任何业务数据）。

用途：按 widget 清单逐页探测后端接口，输出「正常 / 异常 + 错误码 + 文案」，
      定位「数据源暂不可用」影响面。

运行：  set OPENBB_TOKEN=口令 && .venv\\Scripts\\python.exe tools\\diag_page_sweep.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("OPENBB_BASE", "http://127.0.0.1:6900")
TOKEN = os.environ.get("OPENBB_TOKEN", "")

# 各页面的代表参数（用于把接口探测出真实数据）
DEFAULT_PARAMS = {
    "symbol": "600519",
    "market": "a",
    "board": "半导体",
    "code": "017811",
    "fund": "017811",
    "keyword": "通信",
    "period": "daily",
    "limit": "60",
}


def get(path: str, timeout: int = 25) -> tuple[int, dict]:
    """调用本机 API，返回 (HTTP 状态码, JSON 体)。异常统一折算为不可用。"""
    req = urllib.request.Request(BASE + path)
    if TOKEN:
        req.add_header("Authorization", "Bearer " + TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return e.code, {"error": "HTTP %s" % e.code}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": "%s" % type(e).__name__, "error_code": "TIMEOUT"}


def build_url(api: str) -> str:
    """给接口补齐常用查询参数，便于探测到真实数据。"""
    q = dict(DEFAULT_PARAMS)
    if "?" in api:
        base, _, qs = api.partition("?")
        for kv in qs.split("&"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                q[k] = v
        return base + "?" + urllib.parse.urlencode(q)
    return api + "?" + urllib.parse.urlencode(q)


def main() -> None:
    _, body = get("/api/widgets")
    items = body.get("data", {}).get("items", [])
    pages: dict[str, set[str]] = {}
    for it in items:
        for a in it.get("api") or []:
            pages.setdefault(it.get("page", "?"), set()).add(a)

    for page in sorted(pages):
        print("\n==== 页面 %s ====" % page)
        for api in sorted(pages[page]):
            url = build_url(api)
            _, res = get(url)
            code = res.get("error_code") or "OK"
            flag = "OK " if code == "OK" else "FAIL"
            print("  [%s] %-42s %-14s %s" % (flag, api, code, (res.get("error") or "")[:40]))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
