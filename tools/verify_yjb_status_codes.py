# -*- coding: utf-8 -*-
"""养基宝 HTTP 错误分流实测（接口文档 §3.4）：在**打包产物**上验证 401/408/429 中文文案。

做法：本地起一个桩服务（默认 7654 端口），把 ``YJB_BASE_URL_OLD`` 指向它，
再以 ``--serve-only`` 启动待测产物，逐个状态码打真实 HTTP 接口，断言
``error_code`` 与中文提示符合文档口径（401→UNAUTHORIZED、408→UPSTREAM_TIMEOUT、
429→UPSTREAM_ERROR「请求频繁」），全程不触碰真实养基宝通道与凭据文件。

用法：
    .venv\\Scripts\\python.exe tools\\verify_yjb_status_codes.py \\
        --artifact "F:\\…\\正式环境" --port 6917 --stub-port 7654 \\
        --out "…\\release_gate_error_paths.json"
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BJ = timezone(timedelta(hours=8))

# 用例表：状态码 → (接口路径, 期望 error_code, 期望提示片段)
# 注意：429/408 允许磁盘旧值兜底（可用性优先），故每条用例前必须清缓存，
# 否则会读到旧数据而看不到错误分支；401 则被设计为**永不**使用旧值。
CASES = [
    (401, "/api/fund/yjb/collect", "UNAUTHORIZED", "未授权，请重新登录"),
    (408, "/api/fund/yjb/holdings?account_id=all", "UPSTREAM_TIMEOUT", "请求超时"),
    (429, "/api/fund/yjb/collect", "UPSTREAM_ERROR", "请求频繁，请稍后再试"),
]

_STUB_STATUS = {"code": 200}


class _StubHandler(BaseHTTPRequestHandler):
    """桩服务：按 `_STUB_STATUS` 返回指定状态码，正文不带任何敏感信息。

    ``timeout=2`` 让「先试 https」的 TLS 握手在 2 秒内被断开，被测端据此
    快速回落 http 明文候选，避免每条用例都等满上游超时。
    """

    timeout = 2

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        body = json.dumps({"code": 200, "data": []}).encode()
        if _STUB_STATUS["code"] != 200:
            body = json.dumps({"code": _STUB_STATUS["code"], "message": "stub"}).encode()
        self.send_response(_STUB_STATUS["code"])
        self.send_header("Content-Type", "application/json;charset=UTF-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # noqa: D102 - 静默桩服务访问日志
        return


def _wait_port(host: str, port: int, timeout: float = 40.0) -> bool:
    """轮询等待端口就绪。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.2)
    return False


def _get(url: str) -> dict:
    """GET 并解析 JSON（应用统一信封，HTTP 状态恒 200）。"""
    with urllib.request.urlopen(url, timeout=90) as resp:  # noqa: S310 - 本机回环
        return json.loads(resp.read().decode("utf-8"))


def _clear_cache(base: str) -> None:
    """清空被测进程缓存，避免磁盘旧值兜底掩盖错误分支（每条用例前置）。"""
    request = urllib.request.Request(base + "/api/cache/clear", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:  # noqa: S310 - 本机回环
            resp.read()
    except urllib.error.URLError as exc:
        print(f"[WARN] 清缓存失败（{type(exc).__name__}），用例结果可能受旧值影响")


def main() -> int:
    """执行错误分流实测并写出 JSON 证据。"""
    parser = argparse.ArgumentParser(description="养基宝 401/408/429 中文分流实测")
    parser.add_argument("--artifact", required=True, help="打包产物目录（含 OpenBB-CN.exe）")
    parser.add_argument("--port", type=int, default=6917, help="待测产物监听端口")
    parser.add_argument("--stub-port", type=int, default=7654, help="本地桩服务端口")
    parser.add_argument("--out", default="", help="证据 JSON 输出路径")
    args = parser.parse_args()

    artifact = Path(args.artifact).resolve()
    exe = artifact / ("OpenBB-CN.exe" if os.name == "nt" else "OpenBB-CN")
    if not exe.exists():
        print(f"[FAIL] 找不到可执行文件：{exe}")
        return 2

    stub = ThreadingHTTPServer(("127.0.0.1", args.stub_port), _StubHandler)
    threading.Thread(target=stub.serve_forever, daemon=True).start()

    env = dict(os.environ)
    env["OPENBB_PORT"] = str(args.port)
    env["YJB_BASE_URL_OLD"] = f"http://127.0.0.1:{args.stub_port}"
    proc = subprocess.Popen(  # noqa: S603 - 待测产物为本地已知可执行文件
        [str(exe), "--serve-only"], cwd=str(artifact), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    results = []
    try:
        if not _wait_port("127.0.0.1", args.port):
            print("[FAIL] 待测产物 40 秒内未监听端口")
            return 2
        base = f"http://127.0.0.1:{args.port}"
        for status, path, want_code, want_text in CASES:
            _STUB_STATUS["code"] = status
            _clear_cache(base)
            try:
                payload = _get(base + path)
            except urllib.error.URLError as exc:
                results.append({"stub_status": status, "path": path, "ok": False,
                                "detail": f"请求失败：{type(exc).__name__}"})
                continue
            error_code = payload.get("error_code")
            message = str(payload.get("error") or "")
            ok = error_code == want_code and want_text in message
            results.append({
                "stub_status": status, "path": path, "ok": ok,
                "want_error_code": want_code, "error_code": error_code,
                "error": message, "want_text": want_text,
            })
            print(f"[{'OK' if ok else 'FAIL'}] HTTP {status} → {error_code}｜{message}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        stub.shutdown()

    ok_all = bool(results) and all(item["ok"] for item in results)
    report = {
        "generated_at": datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S"),
        "artifact": str(artifact),
        "artifact_exe_bytes": exe.stat().st_size,
        "stub_base": f"http://127.0.0.1:{args.stub_port}",
        "spec": "养基宝接口文档 §3.4（401/408/429 中文分流）",
        "ok": ok_all,
        "items": results,
    }
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告已写入：{out}")
    print("结论：" + ("错误分流实测通过" if ok_all else "错误分流实测未通过"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
