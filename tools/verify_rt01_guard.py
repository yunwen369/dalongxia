# -*- coding: utf-8 -*-
"""RT-01 验收脚本：四个破坏性 POST 的空 body 防护 + 状态文件哈希不变 + 脱敏审计。

用途：在**真实进程**（源码开发服务或打包产物 `--serve-only`）上复现「第四方扫描器
枚举端点打空 body」的场景，并给出三份可核验证据：

1. 四个端点空 body（`{}` 与 `Content-Length: 0` 两种）一律返回 `INVALID_PARAM`；
2. `yjb_token.json` / `yjb_secret.json` / `yjb_state.json` 的 SHA256 逐字节不变
   （不读内容，只比哈希，避免脚本本身泄露凭据）；
3. 审计日志新增 4 条以上 `rejected:` 记录，且文件全文不含 token/盐明文。

用法：
    ./.venv/Scripts/python.exe tools/verify_rt01_guard.py --artifact <产物目录> --port 6940
    ./.venv/Scripts/python.exe tools/verify_rt01_guard.py --base-url http://127.0.0.1:6903

退出码：0=全部通过；1=有断言失败；2=启动/连接失败。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
CRED_FILES = ("yjb_token.json", "yjb_secret.json", "yjb_state.json")
ENDPOINTS = ("/api/fund/yjb/consent", "/api/fund/yjb/toggle",
             "/api/fund/yjb/unbind", "/api/fund/yjb/secret")


def _post(url: str, body: Optional[bytes]) -> Dict[str, Any]:
    """POST 原始字节体并解析统一信封（本应用 HTTP 状态恒 200）。

    Args:
        url: 完整 URL。
        body: 请求体字节；``None`` 表示完全不带 body（Content-Length=0）。

    Returns:
        dict: 解析后的响应体；解析失败返回 `{"_raw": 文本}`。
    """
    request = urllib.request.Request(url, data=body if body is not None else b"", method="POST")
    request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=30) as resp:  # noqa: S310 - 本机回环
        text = resp.read().decode("utf-8", "replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_raw": text[:200]}


def _digests(data_dir: Path) -> Dict[str, str]:
    """三个凭据/状态文件的 SHA256 快照（文件缺失记 MISSING）。

    Args:
        data_dir: 运行时 data 目录。

    Returns:
        dict: 文件名 → 哈希或 ``MISSING``。
    """
    out: Dict[str, str] = {}
    for name in CRED_FILES:
        path = data_dir / name
        out[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "MISSING"
    return out


def _audit_lines(path: Path) -> List[Dict[str, Any]]:
    """读取审计 JSONL 的全部条目（坏行跳过）。

    Args:
        path: 审计文件路径。

    Returns:
        list[dict]: 审计条目（保持文件顺序）。
    """
    if not path.exists():
        return []
    entries: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _wait_health(base: str, timeout: float = 90.0) -> bool:
    """轮询 /api/health 直到服务可用。

    Args:
        base: 服务基址。
        timeout: 最长等待秒数。

    Returns:
        bool: 就绪返回 True。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(base + "/api/health", timeout=3).read()
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.8)
    return False


def main() -> int:
    """执行验收并写出 JSON 报告。

    Returns:
        int: 进程退出码（0 通过 / 1 断言失败 / 2 环境失败）。
    """
    parser = argparse.ArgumentParser(description="RT-01 空 body 防护验收")
    parser.add_argument("--artifact", help="打包产物目录（内含 OpenBB-CN.exe）")
    parser.add_argument("--base-url", help="已在运行的服务基址，如 http://127.0.0.1:6903")
    parser.add_argument("--port", type=int, default=6940, help="自起产物时的端口")
    parser.add_argument("--out", help="JSON 报告输出路径")
    args = parser.parse_args()

    proc: Optional[subprocess.Popen] = None
    if args.base_url:
        base = args.base_url.rstrip("/")
        data_dir = ROOT / "data"
        target = "existing-service"
    elif args.artifact:
        artifact = Path(args.artifact).resolve()
        exe = artifact / "OpenBB-CN.exe"
        if not exe.exists():
            print(f"[FAIL] 产物不存在：{exe}")
            return 2
        env = dict(os.environ)
        env["OPENBB_PORT"] = str(args.port)
        env.setdefault("CODEBUDDY_SAFE_DELETE_ENABLED", "0")
        proc = subprocess.Popen([str(exe), "--serve-only"], cwd=str(artifact), env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{args.port}"
        data_dir = artifact / "data"
        target = str(artifact)
    else:
        parser.error("必须指定 --artifact 或 --base-url")
        return 2

    report: Dict[str, Any] = {"target": target, "base": base, "cases": [], "passed": False}
    try:
        if not _wait_health(base):
            print("[FAIL] 服务未就绪")
            report["error"] = "service-not-ready"
            return 2

        audit_file = data_dir / "audit" / "yjb_audit.jsonl"
        audit_before = len(_audit_lines(audit_file))
        before = _digests(data_dir)
        report["digests_before"] = before

        # 两类空 body：JSON 空对象 与 完全无 body（Content-Length=0）
        for payload_name, body in (("{}", b"{}"), ("no-body", None)):
            for path in ENDPOINTS:
                try:
                    resp = _post(base + path, body)
                    code = resp.get("error_code")
                    message = resp.get("error") or ""
                except Exception as exc:  # noqa: BLE001 - 网络层异常也要记录
                    code, message = "EXCEPTION", f"{type(exc).__name__}: {exc}"
                ok = code == "INVALID_PARAM" and bool(message)
                report["cases"].append({"endpoint": path, "payload": payload_name,
                                       "error_code": code, "message": message[:120], "ok": ok})

        after = _digests(data_dir)
        report["digests_after"] = after
        report["digests_unchanged"] = before == after

        audit_after_entries = _audit_lines(audit_file)
        new_entries = audit_after_entries[audit_before:]
        report["audit_new_count"] = len(new_entries)
        report["audit_new"] = new_entries[:12]
        rejected = [entry for entry in new_entries if str(entry.get("result", "")).startswith("rejected:")]
        report["audit_rejected_count"] = len(rejected)

        # 明文检查：读真实凭据文件内容，确认它们没出现在审计里
        leaked: List[str] = []
        audit_text = audit_file.read_text(encoding="utf-8") if audit_file.exists() else ""
        for name in CRED_FILES:
            path = data_dir / name
            if not path.exists():
                continue
            raw = path.read_bytes()
            # 凭据文件是 base64/密文，取其中较长的可打印片段做泄漏探测
            probe = "".join(chr(b) if 32 <= b < 127 else " " for b in raw).split()
            for token in probe:
                if len(token) >= 16 and token in audit_text:
                    leaked.append(f"{name}:{token[:8]}…")
        report["audit_leaked_fragments"] = leaked

        checks = {
            "all_cases_invalid_param": all(case["ok"] for case in report["cases"]),
            "digests_unchanged": report["digests_unchanged"],
            "audit_rejected_at_least_4": report["audit_rejected_count"] >= 4,
            "audit_no_leak": not leaked,
        }
        report["checks"] = checks
        report["passed"] = all(checks.values())

        for key, value in checks.items():
            print(f"[{'OK  ' if value else 'FAIL'}] {key}")
        print(f"       用例 {len(report['cases'])} 条："
              f"{sum(1 for c in report['cases'] if c['ok'])} 通过 / "
              f"{sum(1 for c in report['cases'] if not c['ok'])} 失败")
        print(f"       哈希不变：{json.dumps(before, ensure_ascii=False)}")
        print(f"       新增审计 {report['audit_new_count']} 条，其中 rejected {report['audit_rejected_count']} 条")

        if args.out:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"       报告：{out_path}")
        return 0 if report["passed"] else 1
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=12)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
