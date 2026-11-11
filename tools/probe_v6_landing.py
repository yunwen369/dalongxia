"""V6 定稿工程落地后的一次性取证探针（端点核对 + 真实 HTTP 自测）。

用途（本地自测，不写任何工程树文件）：
1. 解析定稿原型工程引用的全部 /api 路径（views.js 文本 + registry_data.js 注册表）；
2. 与本地服务逐条 HTTP 探测，记录状态码与信封形态；
3. 验根路径 / 在带会话 Cookie 时返回定稿原型页。

用法：
    set NO_PROXY=127.0.0.1,localhost
    ./.venv/Scripts/python.exe tools/probe_v6_landing.py --base http://127.0.0.1:6903
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

PROTO_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "B_交付归档",
    "01_原型设计",
    "20261111-V6视觉换肤",
    "原型_HTML_v6.0_20261111",
)
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
SKIP_GET = {"/api/fund/yjb/consent", "/api/news/ai-review", "/api/sentiment/board"}
URL = ""


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _resolve_query(value: str) -> str:
    """把 PARAM_HINTS 的 JS 表达式还原为查询串（支持字符串拼接 + encodeURIComponent）。"""
    out = ""
    for part in re.split(r"\s*\+\s*", value):
        part = part.strip()
        match = re.search(r'encodeURIComponent\("([^"]*)"\)', part)
        if match:
            out += urllib.parse.quote(match.group(1))
        elif part.startswith('"') and part.endswith('"'):
            out += part[1:-1]
    return out


def parse_param_hints() -> dict:
    """从 registry_view.js 抽取 PARAM_HINTS（端点 -> 默认查询串）。"""
    text = _read(os.path.join(PROTO_DIR, "js", "registry_view.js"))
    block = text[text.index("PARAM_HINTS"):]
    block = block[block.index("{"): block.index("}") + 1]
    hints = {}
    for line in block.splitlines():
        match = re.match(r'\s*"(/api/[^"]+)"\s*:\s*(.+?)\s*,?\s*$', line)
        if match:
            hints[match.group(1)] = _resolve_query(match.group(2))
    return hints


def registry_paths(hints: dict) -> list:
    """解析 registry_data.js 的 49 组件，取可用首端点 + 默认参数。"""
    text = _read(os.path.join(PROTO_DIR, "data", "registry_data.js"))
    data = json.loads(text[text.index("{"): text.rindex("}") + 1])
    paths = []
    for widget in data.get("widgets", []):
        usable = [p for p in (widget.get("api") or []) if p not in SKIP_GET]
        if not usable:
            continue
        path = usable[0]
        if "?" not in path:
            path += hints.get(path, "")
        paths.append(path)
    return paths


def views_paths() -> list:
    """从 views.js 抽取全部 "/api/..." 字面量的真实路径（去掉尾随的来源说明文案）。"""
    raw = re.findall(r'"(/api/[^"]+)"', _read(os.path.join(PROTO_DIR, "js", "views.js")))
    paths = []
    for item in raw:
        base = re.split(r"\s*·|\s*（|\s*\(|\s+\+|\s", item)[0].strip()
        if base.startswith("/api/"):
            paths.append(base)
    return paths


def http(path: str, token: str, cookie: str = "") -> tuple:
    headers = {"Authorization": "Bearer " + token} if token else {}
    if cookie:
        headers["Cookie"] = cookie
    request = urllib.request.Request(URL + path, headers=headers)
    try:
        with OPENER.open(request, timeout=180) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - 探针需报可读错误
        return -1, str(exc)


def envelope(body: str) -> str:
    """归类：信封正常(OK) / 业务态(BIZ:code) / 非 JSON。"""
    try:
        payload = json.loads(body)
    except Exception:  # noqa: BLE001
        return "NON_JSON"
    if not isinstance(payload, dict):
        return "NON_JSON"
    if payload.get("error_code"):
        return "BIZ:" + str(payload["error_code"])
    return "OK" if "data" in payload else "RAW"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:6903")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    global URL
    URL = args.base.rstrip("/")
    token = os.environ.get("OPENBB_ACCESS_PASSWORD", "").strip()

    hints = parse_param_hints()
    paths = sorted(set(registry_paths(hints) + views_paths()))
    print("待探测端点：%d 条；PARAM_HINTS %d 条" % (len(paths), len(hints)), flush=True)

    cookie = ""
    if token:
        try:
            with OPENER.open(urllib.request.Request(URL + "/auth?token=" + token), timeout=15) as response:
                cookie = (response.headers.get("Set-Cookie") or "").split(";")[0]
        except Exception:  # noqa: BLE001
            cookie = ""

    results, bad = [], []
    for index, path in enumerate(paths, 1):
        code, body = http(path, token)
        kind = envelope(body) if code == 200 else ("HTTP_%d" % code)
        ok = code == 200 and (kind == "OK" or kind.startswith("BIZ:"))
        results.append({"path": path, "code": code, "kind": kind, "ok": ok})
        if not ok:
            bad.append({"path": path, "code": code, "kind": kind, "body": body[:200]})
        print("[%02d/%02d] %s -> %s %s" % (index, len(paths), path, code, kind), flush=True)

    code_root, body_root = http("/", token, cookie)
    root_ok = code_root == 200 and "registry_view.js" in body_root
    summary = {
        "base": URL,
        "endpoints_total": len(results),
        "endpoints_ok": len(results) - len(bad),
        "endpoints_bad": len(bad),
        "root_code": code_root,
        "root_is_draft_page": root_ok,
        "bad": bad,
    }
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump({"summary": summary, "results": results}, handle, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
