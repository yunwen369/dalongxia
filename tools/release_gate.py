# -*- coding: utf-8 -*-
"""发版门禁：在**打包产物**上实测养基宝三项接口（不是源码 dev 服务）。

背景（第五波 YJB-01/YJB-02）：曾出现"用户实际运行的 exe 是旧包 + 凭据没落位"，
前端根本没有 Y-05/Y-06 区块，凭据目录也缺 token/盐，导致用户看不到账户汇总与持仓。
本脚本把"必须在打包产物上实测"固化为可执行门禁，失败即退出码非 0，禁止发版。

检查项：
1. 产物存在性：``<产物目录>/OpenBB-CN.exe`` + ``_internal/frontend/js/``（第十轮起为拆分目录，非单文件 pages.js）；
2. 前端完整性：js 目录拼接后必须命中 ``loadYjbCollect`` / ``loadYjbHoldings``
   / ``/api/fund/yjb/collect`` / ``/api/fund/yjb/holdings`` / ``/api/fund/yjb/secret``；
3. 凭据落位：``<产物目录>/data/`` 下 ``yjb_token.json``、``yjb_secret.json`` 存在且非空；
4. 起真实进程：``OpenBB-CN.exe --serve-only``（无窗口），跑在门禁端口（默认 6916），
   不影响用户正在使用的 6900；
5. 三项接口实测：``/api/fund/yjb/status`` 三个 true、``/collect`` 总资产>0、
   ``/holdings?account_id=all`` 条数>0 且每条 ``pct``/``nav`` 非空；
6. 写 ``release_gate.json`` 报告并打印中文结论。

用法：
    .venv\\Scripts\\python.exe tools\\release_gate.py
    .venv\\Scripts\\python.exe tools\\release_gate.py --artifact "A_运行资产\\正式环境"
    .venv\\Scripts\\python.exe tools\\release_gate.py --port 6917 --json 报告路径.json

退出码：0=门禁通过；1=门禁不通过（禁止发版）；2=环境性问题（产物缺失/端口占用）。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_DIR = Path(__file__).resolve().parents[1]  # openbb-cn 工程根
DEFAULT_ARTIFACT = PROJECT_DIR.parent / "正式环境"  # EXE 同级目录（onedir 产物）
GATE_PORT = 6916  # 门禁专用端口，避开用户正在使用的 6900
BJ_TZ = timezone(timedelta(hours=8))  # 北京时间（报告时间戳口径）

# 前端必须命中的标记（旧包 grep 为 0，是本轮事故的直接判据）。
# 2026-11-12 架构重构 B1：原 5 项中 loadYjbCollect/loadYjbHoldings//api/fund/yjb/secret
# 仅存在于旧层 pages/yjb.js（已归档退役），判据曾靠死代码命中——本轮换为
# v7_app.js / views.js 活层真实标记（绑定/toggle/collect/holdings/poll 五链路），
# 判据语义不变：活层不含这些串即非本版产物。
PAGES_JS_MARKERS = [
    "/api/fund/yjb/collect",
    "/api/fund/yjb/holdings",
    "/api/fund/yjb/toggle",
    "/api/fund/yjb/bind",
    "/api/fund/yjb/poll",
]


# 门禁固定顺序（阶段 9）：①静态 → ②单测 → ③端点 → ④界面资源。
# 任一失败即不得发版；pyflakes 本可拦住「F821 undefined name 'netfix'」这类 P1。
STATIC_TARGET = "backend/app"
STATIC_TIMEOUT = 180
PYTEST_TIMEOUT = 900


def _run_step(command: List[str], cwd: Path, timeout: int) -> Tuple[int, str]:
    """执行门禁子步骤（异常也转为失败结果，不抛出）。

    Args:
        command: 命令与参数列表。
        cwd: 工作目录。
        timeout: 超时秒数。

    Returns:
        Tuple[int, str]: (退出码, 合并输出)；超时返回 -1，启动失败返回 -2。
    """
    try:
        proc = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True,
                              timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return -1, f"超时（>{timeout}s）"
    except OSError as exc:
        return -2, f"{type(exc).__name__}: {exc}"
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def _check_pyflakes(project: Path) -> Dict[str, Any]:
    """静态检查：pyflakes 对 backend/app 必须零输出。

    Args:
        project: 工程根目录。

    Returns:
        Dict[str, Any]: ``{ok, exit_code, output}``；F821/F401 等任一告警都判失败。
    """
    code, out = _run_step([sys.executable, "-m", "pyflakes", STATIC_TARGET], project, STATIC_TIMEOUT)
    return {"ok": code == 0 and not out.strip(), "exit_code": code, "output": out.strip()[-3000:]}


def _check_pytest(project: Path) -> Dict[str, Any]:
    """全量 pytest 必须全绿。

    Args:
        project: 工程根目录。

    Returns:
        Dict[str, Any]: ``{ok, exit_code, tail}``。

    Notes:
        basetemp 必须指向工程内目录：默认落 %TEMP% 时，pytest 收尾清理会触发
        沙箱 safe-delete 批量阈值（>50 文件）导致进程异常退出。
    """
    stamp = datetime.now(BJ_TZ).strftime("%Y%m%d_%H%M%S")
    basetemp = project / "build" / "_tmp" / f"gate_{stamp}"
    basetemp.parent.mkdir(parents=True, exist_ok=True)
    code, out = _run_step([sys.executable, "-m", "pytest", f"--basetemp={basetemp}",
                           "-p", "no:cacheprovider"], project, PYTEST_TIMEOUT)
    tail = "\n".join(out.strip().splitlines()[-8:])
    return {"ok": code == 0, "exit_code": code, "tail": tail}


def _bj_text() -> str:
    """返回北京时间的可读时间戳。

    Returns:
        str: ``YYYY-MM-DD HH:MM:SS``（UTC+8）。
    """
    return datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    """探测端口是否有进程监听。

    Args:
        host: 主机地址。
        port: 端口号。
        timeout: 连接超时秒数。

    Returns:
        bool: 有监听返回 True。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def _get_json(url: str, timeout: float = 20.0) -> Tuple[Optional[Dict[str, Any]], str]:
    """GET 并解析 JSON（HTTP 非 200 也算失败）。

    Args:
        url: 完整地址。
        timeout: 超时秒数。

    Returns:
        tuple: (解析后的 JSON 或 None, 失败说明；成功时说明为空串)。

    Note:
        2026-11-10 复验补修（两处环境坑，均实测踩到）：
        1. 本机 ``HTTP_PROXY`` 会把 ``127.0.0.1`` 也转发给代理，表现为
           ``upstream connect failed ... 10061`` → 门禁误判端点失败；
           故对回环地址显式禁用代理。
        2. 部署环境用 ``setx`` 设了 ``OPENBB_ACCESS_PASSWORD``，产物会默认启用
           访问口令，未带凭证的探测一律 401 → 门禁误判 ``http_ok=false``；
           故读取同名环境变量并以 ``Authorization: Bearer`` 带上（未设置则不加）。
    """
    host = urllib.parse.urlsplit(url).hostname or ""
    headers: Dict[str, str] = {}
    pwd = os.environ.get("OPENBB_ACCESS_PASSWORD", "").strip()
    if pwd:
        headers["Authorization"] = f"Bearer {pwd}"
    if host in ("127.0.0.1", "localhost", "::1"):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        _open = opener.open
    else:
        _open = urllib.request.urlopen
    request = urllib.request.Request(url, headers=headers) if headers else url
    try:
        with _open(request, timeout=timeout) as response:
            status = response.status
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:  # 有状态码的失败
        return None, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError) as exc:
        return None, f"连接失败（{type(exc).__name__}）"
    if status != 200:
        return None, f"HTTP {status}"
    try:
        return json.loads(raw), ""
    except json.JSONDecodeError:
        return None, "响应不是合法 JSON"


def _wait_health(base: str, timeout: float = 90.0) -> Tuple[bool, Dict[str, Any]]:
    """等待 /api/health 就绪。

    Args:
        base: 服务基址。
        timeout: 最长等待秒数。

    Returns:
        tuple: (是否就绪, health 的 data 段)。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload, _ = _get_json(f"{base}/api/health", timeout=2.0)
        if payload and isinstance(payload.get("data"), dict):
            return True, payload["data"]
        time.sleep(0.6)
    return False, {}


def _check_frontend(artifact: Path) -> Dict[str, Any]:
    """核对打包进 _internal 的前端是否含 Y-05/Y-06 相关代码。

    Args:
        artifact: 产物目录。

    Returns:
        dict: ``{path, bytes, markers:{标记:出现次数}, ok}``。
    """
    # 第十轮 C17 起 pages.js 已按页拆分为 pages/*.js（旧单文件删除），
    # 故改为遍历整个 js 目录拼接后检查标记——避免「文件改名/拆分」被误判成「旧包」。
    js_dir = artifact / "_internal" / "frontend" / "js"
    result: Dict[str, Any] = {"path": str(js_dir), "exists": js_dir.exists(), "bytes": 0,
                              "markers": {}, "ok": False}
    if not js_dir.exists():
        return result
    chunks: List[str] = []
    for path in sorted(js_dir.rglob("*.js")):
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    text = "\n".join(chunks)
    result["bytes"] = sum(len(chunk) for chunk in chunks)
    for marker in PAGES_JS_MARKERS:
        result["markers"][marker] = text.count(marker)
    result["ok"] = all(count > 0 for count in result["markers"].values())
    return result


def _check_credentials(artifact: Path) -> Dict[str, Any]:
    """核对 EXE 同级 data 目录中的养基宝凭据是否落位。

    Args:
        artifact: 产物目录。

    Returns:
        dict: 每个凭据文件的 ``{exists, bytes}`` 与总体 ``ok``。
    """
    data_dir = artifact / "data"
    files: Dict[str, Any] = {}
    for name in ("yjb_token.json", "yjb_secret.json", "yjb_state.json"):
        path = data_dir / name
        files[name] = {"exists": path.exists(), "bytes": path.stat().st_size if path.exists() else 0}
    ok = all(files[n]["exists"] and files[n]["bytes"] > 0
             for n in ("yjb_token.json", "yjb_secret.json"))
    return {"dir": str(data_dir), "files": files, "ok": ok}


def _launch(artifact: Path, port: int) -> subprocess.Popen:
    """以「仅服务」模式启动打包产物（无窗口）。

    Args:
        artifact: 产物目录。
        port: 门禁端口。

    Returns:
        subprocess.Popen: 进程句柄。
    """
    env = dict(os.environ)
    env["OPENBB_PORT"] = str(port)
    env["PYTHONUTF8"] = "1"
    exe = artifact / "OpenBB-CN.exe"
    creation = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    return subprocess.Popen([str(exe), "--serve-only"], cwd=str(artifact), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=creation)


def _stop(process: subprocess.Popen) -> None:
    """结束门禁拉起的进程（含子进程树）。

    Args:
        process: 进程句柄。
    """
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _assert_holds(items: List[Dict[str, Any]]) -> List[str]:
    """逐条校验持仓的 pct/nav 是否非空。

    Args:
        items: 持仓列表。

    Returns:
        list[str]: 问题描述列表（空表示全部合格）。
    """
    problems: List[str] = []
    for index, row in enumerate(items):
        code = row.get("code") or f"#{index}"
        if row.get("pct") is None:
            problems.append(f"{code}：估算涨跌幅为空")
        if row.get("nav") is None:
            problems.append(f"{code}：T+1 净值为空")
    return problems


def run_gate(artifact: Path, port: int, keep_running: bool = False) -> Dict[str, Any]:
    """执行完整门禁流程。

    Args:
        artifact: 产物目录。
        port: 门禁端口。
        keep_running: 通过后是否保留进程（调试用）。

    Returns:
        dict: 门禁报告。
    """
    report: Dict[str, Any] = {
        "as_of": _bj_text(),
        "artifact": str(artifact),
        "port": port,
        "checks": {},
        "responses": {},
        "passed": False,
        "notes": [],
        "steps": [],
    }
    # ---------- 步骤 1：静态检查（不依赖产物，最先失败最快反馈）----------
    static = _check_pyflakes(PROJECT_DIR)
    report["checks"]["pyflakes"] = static
    report["steps"].append({"step": "1. pyflakes 静态检查（backend/app 须 0 输出）",
                            "ok": static["ok"], "exit_code": static["exit_code"]})
    if not static["ok"]:
        report["notes"].append("pyflakes 未通过：未定义名 / 未用导入必须清零（见 checks.pyflakes.output）")
        return report

    # ---------- 步骤 2：全量 pytest ----------
    unit = _check_pytest(PROJECT_DIR)
    report["checks"]["pytest"] = unit
    report["steps"].append({"step": "2. 全量 pytest", "ok": unit["ok"],
                            "exit_code": unit["exit_code"]})
    if not unit["ok"]:
        report["notes"].append("pytest 未全绿：禁止发版（见 checks.pytest.tail）")
        return report

    exe = artifact / "OpenBB-CN.exe"
    report["checks"]["exe"] = {"path": str(exe), "exists": exe.exists(),
                               "bytes": exe.stat().st_size if exe.exists() else 0}
    if not exe.exists():
        report["notes"].append("产物目录不存在 OpenBB-CN.exe：请先执行打包（tools/build_and_gate.py）")
        return report

    report["checks"]["frontend"] = _check_frontend(artifact)
    report["checks"]["credentials"] = _check_credentials(artifact)

    if _port_open("127.0.0.1", port):
        report["notes"].append(f"门禁端口 {port} 已被占用：请换 --port 或先释放该端口")
        report["exit_code"] = 2
        return report

    base = f"http://127.0.0.1:{port}"
    process = _launch(artifact, port)
    try:
        ready, health = _wait_health(base)
        report["checks"]["health"] = {"ready": ready, "build": health.get("build"),
                                      "version": (health.get("app") or {}).get("version")}
        if not ready:
            report["notes"].append("打包产物启动 90 秒内未就绪：查看产物目录 logs/app.log")
            report["exit_code"] = 2
            return report

        status_payload, status_err = _get_json(f"{base}/api/fund/yjb/status")
        report["responses"]["status"] = status_payload or {"error": status_err}
        status_data = (status_payload or {}).get("data") or {}
        report["checks"]["status"] = {
            "http_ok": bool(status_payload),
            "bound": status_data.get("bound"),
            "secret_configured": status_data.get("secret_configured"),
            "channel_available": status_data.get("channel_available"),
            "setup_required": status_data.get("setup_required"),
            "setup_missing": status_data.get("setup_missing"),
            "ok": bool(status_data.get("bound") and status_data.get("secret_configured")
                       and status_data.get("channel_available")),
        }

        collect_payload, collect_err = _get_json(f"{base}/api/fund/yjb/collect", timeout=40.0)
        report["responses"]["collect"] = collect_payload or {"error": collect_err}
        collect_data = (collect_payload or {}).get("data") or {}
        total_assets = collect_data.get("total_assets")
        report["checks"]["collect"] = {
            "http_ok": bool(collect_payload),
            "total_assets": total_assets,
            "today_income": collect_data.get("today_income"),
            "accounts": len(collect_data.get("accounts") or []),
            "ok": bool(collect_payload) and isinstance(total_assets, (int, float)) and total_assets > 0,
        }

        holdings_payload, holdings_err = _get_json(
            f"{base}/api/fund/yjb/holdings?account_id=all", timeout=60.0)
        report["responses"]["holdings"] = holdings_payload or {"error": holdings_err}
        holdings_data = (holdings_payload or {}).get("data") or {}
        items = holdings_data.get("items") or []
        problems = _assert_holds(items)
        report["checks"]["holdings"] = {
            "http_ok": bool(holdings_payload),
            "total": holdings_data.get("total"),
            "rows": len(items),
            "sample": [{k: row.get(k) for k in ("code", "name", "pct", "nav", "account_id")}
                       for row in items[:3]],
            "bad_rows": problems[:10],
            "ok": bool(holdings_payload) and len(items) > 0 and not problems,
        }

        # ---------- 步骤 3/4 结果入账（与 1/2 一起构成完整门禁链）----------
        endpoint_ok = all(report["checks"][key]["ok"] for key in ("status", "collect", "holdings"))
        report["steps"].append({
            "step": "3. 端点全扫描（health / yjb status·collect·holdings）",
            "ok": endpoint_ok,
            "detail": {
                "health_build": (report["checks"].get("health") or {}).get("build"),
                "status_bound": (report["checks"].get("status") or {}).get("bound"),
                "collect_total_assets": (report["checks"].get("collect") or {}).get("total_assets"),
                "holdings_total": (report["checks"].get("holdings") or {}).get("total"),
            },
        })
        report["steps"].append({
            "step": "4. 界面资源取证（活层 yjb 五链路标记）",
            "ok": bool((report["checks"].get("frontend") or {}).get("ok")),
            "detail": {"bytes": (report["checks"].get("frontend") or {}).get("bytes")},
        })

        gate_ok = all(report["checks"][key]["ok"]
                      for key in ("frontend", "credentials", "status", "collect", "holdings"))
        report["passed"] = bool(gate_ok) and all(s.get("ok") for s in report["steps"])
        if not report["checks"]["credentials"]["ok"]:
            report["notes"].append(
                "凭据未落位：把源码树 data/yjb_token.json、yjb_secret.json 复制到产物 data/，"
                "或启动新包走首启向导（设置页注入签名盐 + 扫码授权）后重新执行门禁")
        if not report["checks"]["frontend"]["ok"]:
            report["notes"].append("前端不含 Y-05/Y-06 代码：这是旧包，禁止发版，必须重新打包")
        if report["checks"]["status"].get("setup_required"):
            report["notes"].append(
                f"首启向导待完成（缺 {report['checks']['status'].get('setup_missing')}）："
                "未绑定状态不得当作已交付")
        return report
    finally:
        if keep_running:
            print(f"[门禁] 已保留进程 pid={process.pid}（端口 {port}），调试完请手动结束")
        else:
            _stop(process)


def _print_report(report: Dict[str, Any]) -> None:
    """把报告打印成中文结论。

    Args:
        report: 门禁报告。
    """
    checks = report.get("checks") or {}
    print("=" * 66)
    print("OpenBB 中文投研工作台 · 发版门禁（打包产物实测）")
    print(f"时间：{report.get('as_of')}　产物：{report.get('artifact')}　端口：{report.get('port')}")
    print("-" * 66)
    for step in report.get("steps") or []:
        print(f"[{'OK' if step.get('ok') else 'FAIL'}] {step.get('step')}")
    exe = checks.get("exe") or {}
    print(f"[{'OK' if exe.get('exists') else 'FAIL'}] 主程序　{exe.get('bytes')} 字节　{exe.get('path')}")
    front = checks.get("frontend") or {}
    marks = front.get("markers") or {}
    print(f"[{'OK' if front.get('ok') else 'FAIL'}] 前端 js 目录　{front.get('bytes')} 字节　"
          f"命中 {sum(1 for v in marks.values() if v)}/{len(PAGES_JS_MARKERS)} 个标记")
    cred = checks.get("credentials") or {}
    files = cred.get("files") or {}
    detail = "，".join(f"{name}={info.get('bytes')}B" for name, info in files.items())
    print(f"[{'OK' if cred.get('ok') else 'FAIL'}] 凭据落位　{detail}")
    health = checks.get("health") or {}
    print(f"[{'OK' if health.get('ready') else 'FAIL'}] 服务就绪　build={health.get('build')}")
    status = checks.get("status") or {}
    print(f"[{'OK' if status.get('ok') else 'FAIL'}] /yjb/status　bound={status.get('bound')} "
          f"secret={status.get('secret_configured')} channel={status.get('channel_available')}")
    collect = checks.get("collect") or {}
    print(f"[{'OK' if collect.get('ok') else 'FAIL'}] /yjb/collect　total_assets={collect.get('total_assets')} "
          f"today_income={collect.get('today_income')} accounts={collect.get('accounts')}")
    holds = checks.get("holdings") or {}
    print(f"[{'OK' if holds.get('ok') else 'FAIL'}] /yjb/holdings　total={holds.get('total')} "
          f"rows={holds.get('rows')} bad_rows={len(holds.get('bad_rows') or [])}")
    for row in holds.get("sample") or []:
        print(f"      样例：{row.get('code')} {row.get('name')} pct={row.get('pct')} nav={row.get('nav')}")
    for note in report.get("notes") or []:
        print(f"  · {note}")
    print("-" * 66)
    print("结论：" + ("门禁通过，可以发版" if report.get("passed") else "门禁不通过，禁止发版"))
    print("=" * 66)


def main() -> int:
    """命令行入口。

    Returns:
        int: 0=通过；1=不通过；2=环境性问题。
    """
    parser = argparse.ArgumentParser(description="OpenBB 发版门禁（打包产物实测养基宝三项接口）")
    parser.add_argument("--artifact", default=str(DEFAULT_ARTIFACT), help="打包产物目录（含 OpenBB-CN.exe）")
    parser.add_argument("--port", type=int, default=GATE_PORT, help="门禁端口（默认 6916，避开 6900）")
    parser.add_argument("--json", default="", help="报告输出路径（默认 <产物>/release_gate.json）")
    parser.add_argument("--keep-running", action="store_true", help="通过后保留进程（调试用）")
    args = parser.parse_args()

    artifact = Path(args.artifact).resolve()
    report = run_gate(artifact, args.port, keep_running=args.keep_running)
    _print_report(report)

    out = Path(args.json) if args.json else artifact / "release_gate.json"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告已写入：{out}")
    except OSError as exc:
        print(f"[警告] 报告写入失败：{type(exc).__name__}", file=sys.stderr)

    if report.get("passed"):
        return 0
    return int(report.get("exit_code") or 1)


if __name__ == "__main__":
    sys.exit(main())
