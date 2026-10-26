# -*- coding: utf-8 -*-
"""时区口径门禁（TZ-06）：在**打包产物**上核对对外时间字段一律为北京时间。

背景：后端曾用 ``datetime.now()`` 取宿主本地时间当北京时间。本机（以及多数国内
部署机）本身就是 UTC+8，所以「看起来是对的」——换到 UTC-5/UTC-6 的机器上，
``as_of``、行情时段判定、龙虎榜日期都会整体偏移，而且不会报错，只会悄悄给错数据。

本脚本在打包产物上核对三件事（都不触网，只用本地计算字段）：

1. ``/api/health`` 的 ``server_time`` 必须带 ``+08:00`` 偏移（显式时区，不是宿主本地）；
2. ``server_time`` 与真实北京时间偏差 < 10 秒（防止「偏移写对了、时钟取错了」）；
3. ``/api/cache/stats`` 的 ``as_of`` 为北京 **naive** 串且与北京时间偏差 < 10 秒
   （该字段是全站 ``as_of`` 的同一条 ``utils.now_text()`` 通道）。

关于「把系统时区改成非 UTC+8」：Windows 上 CPython 不读 ``TZ`` 环境变量
（``time.tzset()`` 是 Unix-only），改系统时区需要管理员权限且会影响用户其它程序，
不适合写进自动门禁。因此「宿主时区免疫性」由 ``tests/test_tz_contract.py`` 用
**伪造 datetime** 的方式证明（把 ``utils.datetime`` 换成 ``now()`` 返回西六区时间的假类，
等价于把机器搬到 UTC-6）；本脚本负责证明「产物里的实现确实用了显式时区」。
两者合起来才构成完整证据，缺一不可。

用法：
    .venv\\Scripts\\python.exe tools\\verify_tz.py
    .venv\\Scripts\\python.exe tools\\verify_tz.py --artifact "A_运行资产\\正式环境" --port 6950
    .venv\\Scripts\\python.exe tools\\verify_tz.py --base-url http://127.0.0.1:6903

退出码：0=通过；1=不通过；2=环境性问题（产物缺失/端口占用）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT = PROJECT_DIR.parent / "正式环境"
TZ_PORT = 6950  # 门禁专用端口，避开 6900 / 6916

BJ = timezone(timedelta(hours=8))  # 北京时区（UTC+8）
TOLERANCE_SEC = 10.0  # 时钟偏差容忍（跨进程取数有几秒延迟属正常）


def _bj_now() -> datetime:
    """取当前北京时间（aware）。

    Returns:
        datetime: 带 +08:00 偏移的当前时间。
    """
    return datetime.now(BJ)


def _get_json(url: str, timeout: float = 8.0) -> Dict[str, Any]:
    """GET 并解析 JSON（本应用统一信封，HTTP 状态恒 200）。

    Args:
        url: 完整 URL。
        timeout: 超时秒数。

    Returns:
        Dict[str, Any]: 解析后的信封；失败返回空字典。
    """
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - 本机回环
            return json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - 就绪探测阶段允许失败
        return {}


def _wait_ready(base: str, timeout: float = 90.0) -> bool:
    """等待服务就绪。

    Args:
        base: 服务基址。
        timeout: 最长等待秒数。

    Returns:
        bool: 就绪返回 True。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = _get_json(f"{base}/api/health", timeout=2.0)
        if isinstance(payload.get("data"), dict):
            return True
        time.sleep(0.6)
    return False


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    """探测端口是否已被占用。

    Args:
        port: 端口号。
        host: 主机地址。

    Returns:
        bool: 有监听返回 True。
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def _check_server_time(health: Dict[str, Any]) -> Tuple[bool, str]:
    """校验 ``/api/health`` 的 ``server_time`` 是否带 +08:00 且接近当前北京时间。

    Args:
        health: ``/api/health`` 的 data 段。

    Returns:
        Tuple[bool, str]: (是否通过, 中文说明)。
    """
    raw = str(health.get("server_time") or "")
    if not raw:
        return False, "server_time 缺失"
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return False, f"server_time 无法解析：{raw}"
    if moment.tzinfo is None:
        return False, f"server_time 无时区偏移（疑似取宿主本地时间）：{raw}"
    offset = moment.utcoffset()
    if offset != timedelta(hours=8):
        return False, f"server_time 偏移为 {offset}，应为 +08:00：{raw}"
    drift = abs((_bj_now() - moment).total_seconds())
    if drift > TOLERANCE_SEC:
        return False, f"server_time 与北京时间相差 {drift:.1f}s（阈值 {TOLERANCE_SEC:.0f}s）"
    return True, f"server_time={raw}（+08:00，偏差 {drift:.1f}s）"


def _check_as_of(payload: Dict[str, Any], field: str, label: str,
                 tolerance: float = TOLERANCE_SEC) -> Tuple[bool, str]:
    """校验某个 ``as_of`` 字段是否为当前北京时间。

    Args:
        payload: 接口 data 段。
        field: 字段名（通常为 ``as_of``）。
        label: 中文标签（用于报告）。
        tolerance: 允许的时间偏差（秒）；带缓存的业务接口需放宽到 TTL 量级。

    Returns:
        Tuple[bool, str]: (是否通过, 中文说明)。

    Notes:
        本应用有两种合法形态：**naive 北京串**（多数 provider 的 ``as_of``，
        与历史契约逐字节一致）与**带 +08:00 的 aware 串**（system 域的
        ``server_time`` / 缓存统计）。两者都按北京口径比较；
        若某字段变成了「无偏移但其实是宿主本地时间」，这里会因偏差超阈值而失败。
    """
    raw = str(payload.get(field) or "")
    if not raw:
        return False, f"{label}.{field} 缺失"
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return False, f"{label}.{field} 无法解析：{raw}"
    if moment.tzinfo is None:
        drift = abs((_bj_now().replace(tzinfo=None) - moment).total_seconds())
        kind = "naive 北京串"
    else:
        if moment.utcoffset() != timedelta(hours=8):
            return False, f"{label}.{field} 偏移为 {moment.utcoffset()}，应为 +08:00：{raw}"
        drift = abs((_bj_now() - moment).total_seconds())
        kind = "带 +08:00"
    if drift > tolerance:
        return False, f"{label}.{field}={raw} 与北京时间相差 {drift:.1f}s（阈值 {tolerance:.0f}s，疑似取宿主本地时间）"
    return True, f"{label}.{field}={raw}（{kind}，偏差 {drift:.1f}s）"


def run_gate(artifact: Optional[Path], base_url: str, port: int) -> Dict[str, Any]:
    """执行时区门禁。

    Args:
        artifact: 打包产物目录；与 ``base_url`` 二选一。
        base_url: 已在运行的服务基址。
        port: 自行拉起进程时使用的端口。

    Returns:
        Dict[str, Any]: 报告字典（含 passed / checks / observed）。
    """
    checks: List[Dict[str, Any]] = []
    observed: Dict[str, Any] = {}
    proc: Optional[subprocess.Popen] = None
    started = False

    try:
        if base_url:
            base = base_url.rstrip("/")
        else:
            if artifact is None or not (artifact / "OpenBB-CN.exe").exists():
                return {"passed": False, "error": "产物缺失：未找到 OpenBB-CN.exe", "checks": checks}
            if _port_open(port):
                return {"passed": False, "error": f"端口 {port} 已被占用，请换 --port", "checks": checks}
            env = dict(os.environ)
            env["OPENBB_PORT"] = str(port)
            env.setdefault("CODEBUDDY_SAFE_DELETE_ENABLED", "0")
            proc = subprocess.Popen(  # noqa: S603 - 启动本工程产物
                [str(artifact / "OpenBB-CN.exe"), "--serve-only"],
                cwd=str(artifact), env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            started = True
            base = f"http://127.0.0.1:{port}"
            if not _wait_ready(base):
                return {"passed": False, "error": "产物启动后 90s 内 /api/health 未就绪", "checks": checks}

        health = _get_json(f"{base}/api/health").get("data") or {}
        observed["build"] = health.get("build")
        observed["server_time"] = health.get("server_time")
        ok, msg = _check_server_time(health)
        checks.append({"item": "health.server_time 显式 +08:00 且为当前北京时间", "passed": ok, "detail": msg})

        stats = _get_json(f"{base}/api/cache/stats").get("data") or {}
        observed["cache_stats_as_of"] = stats.get("as_of")
        ok, msg = _check_as_of(stats, "as_of", "cache/stats")
        checks.append({"item": "cache/stats.as_of 为当前北京时间（北京口径）", "passed": ok, "detail": msg})

        # 业务侧样本：板块汇总的 as_of 走 provider 的 utils.now_text() 通道（naive 北京串）。
        # 该接口有 30~60s 缓存，故放宽到 300s；上游不可用时如实标注「跳过」而非判失败，
        # 避免把「源站挂了」误报成「时区错了」。
        sector = _get_json(f"{base}/api/sector/industry", timeout=40.0)
        block = sector.get("data") or {}
        observed["sector_as_of"] = block.get("as_of")
        observed["sector_market_status"] = block.get("market_status")
        if not block:
            checks.append({"item": "业务接口 as_of（板块汇总，真实 provider）", "passed": True,
                           "detail": f"跳过：上游不可用（{sector.get('error') or '无数据'}）"})
        else:
            ok, msg = _check_as_of(block, "as_of", "sector/industry", tolerance=300.0)
            checks.append({"item": "业务接口 as_of（板块汇总，真实 provider）", "passed": ok,
                           "detail": msg + f"；market_status={block.get('market_status')}"})

        passed = all(item["passed"] for item in checks)
        return {"passed": passed, "checks": checks, "observed": observed,
                "artifact": str(artifact) if artifact else "", "base_url": base,
                "checked_at": _bj_now().strftime("%Y-%m-%d %H:%M:%S")}
    finally:
        if started and proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def main() -> int:
    """命令行入口。

    Returns:
        int: 0=通过；1=不通过；2=环境性问题。
    """
    parser = argparse.ArgumentParser(description="OpenBB 时区口径门禁（TZ-06）")
    parser.add_argument("--artifact", default=str(DEFAULT_ARTIFACT), help="打包产物目录（含 OpenBB-CN.exe）")
    parser.add_argument("--base-url", default="", help="已在运行的服务基址（与 --artifact 二选一）")
    parser.add_argument("--port", type=int, default=TZ_PORT, help=f"自拉进程端口（默认 {TZ_PORT}）")
    parser.add_argument("--json", default="", help="报告输出路径")
    args = parser.parse_args()

    artifact = None if args.base_url else Path(args.artifact).resolve()
    report = run_gate(artifact, args.base_url, args.port)

    print("=" * 66)
    print("时区口径门禁（TZ-06）")
    print("=" * 66)
    for item in report.get("checks", []):
        print(f"[{'OK  ' if item['passed'] else 'FAIL'}] {item['item']}")
        print(f"       {item['detail']}")
    if report.get("error"):
        print(f"[FAIL] {report['error']}")
    print("-" * 66)
    print(f"结论：{'门禁通过' if report.get('passed') else '门禁不通过'}")
    print("=" * 66)

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告已写入：{out}")

    if "产物缺失" in str(report.get("error", "")) or "占用" in str(report.get("error", "")):
        return 2
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    sys.exit(main())
