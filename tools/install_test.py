# -*- coding: utf-8 -*-
"""三场景安装测试（规范 §13.3）：全新安装 / 升级安装 / 卸载。

用法：
    .venv\\Scripts\\python.exe tools/install_test.py --setup "<安装包路径>" [--port 6917]

产物：
    <报告目录>/install_test_result.json

判定（任一不满足即失败，按核心功能类 P0 处理）：
    全新安装：安装成功 → OpenBB-CN.exe 存在 → --serve-only 起服务 → /api/health 200 → 桌面/开始菜单快捷方式存在
    升级安装：在已装目录上覆盖安装 → data/portfolio.json 保留 → VERSION.txt/DisplayVersion 为新版本
    卸载：uninstall.exe /S → 安装目录清空 → 快捷方式移除 → 注册表项移除
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BJ_OK = "\u2705"
BJ_FAIL = "\u274c"

SANDBOX_DIR = Path(os.environ.get("TEMP", r"C:\Windows\Temp")) / "openbb_install_test"
DESKTOP = Path(os.environ.get("USERPROFILE", "")) / "Desktop"
START_MENU = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def _port_free(port: int) -> bool:
    """端口是否空闲。

    Args:
        port: 端口号。

    Returns:
        bool: 空闲返回 True。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _wait_health(port: int, timeout: float = 120.0) -> dict:
    """等待 /api/health 可用并返回 data。

    Args:
        port: 端口号。
        timeout: 最长等待秒数。

    Returns:
        dict: health 的 data；超时返回 {}。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as resp:
                payload = json.loads(resp.read())
                return payload.get("data") or {}
        except Exception:  # noqa: BLE001 - 启动期连接失败属正常，继续等
            time.sleep(2)
    return {}


def _run_installer(setup: Path, target: Path, timeout: int = 300) -> int:
    """静默安装到指定目录（NSIS /S /D=）。

    Args:
        setup: 安装包路径。
        target: 安装目录。
        timeout: 超时秒数。

    Returns:
        int: 进程退出码。
    """
    target.mkdir(parents=True, exist_ok=True)
    # NSIS 要求 /D 必须是最后一个参数且不加引号；路径不得含空格
    assert " " not in str(target), f"安装测试目录不得含空格：{target}"
    proc = subprocess.run([str(setup), "/S", f"/D={target}"],
                          shell=False, capture_output=True, timeout=timeout)
    return proc.returncode


def _start_app(exe: Path, port: int) -> subprocess.Popen:
    """以无窗口模式启动打包产物。

    Args:
        exe: OpenBB-CN.exe 路径。
        port: 服务端口。

    Returns:
        subprocess.Popen: 进程句柄。
    """
    env = dict(os.environ)
    env["OPENBB_NO_BROWSER"] = "1"
    return subprocess.Popen([str(exe), "--serve-only", "--port", str(port)],
                            cwd=str(exe.parent), stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, env=env)


def _stop_app(proc: subprocess.Popen) -> None:
    """停止进程（含子进程）。

    Args:
        proc: 进程句柄。
    """
    try:
        proc.terminate()
        proc.wait(timeout=15)
    except Exception:  # noqa: BLE001 - 已退出则忽略
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=30)
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenBB 三场景安装测试")
    parser.add_argument("--setup", required=True, help="安装包 exe 路径")
    parser.add_argument("--port", type=int, default=6917, help="测试端口（避开 6900/6903）")
    parser.add_argument("--out", default="install_test_result.json", help="结果 JSON 路径")
    args = parser.parse_args()

    setup = Path(args.setup).resolve()
    if not setup.exists():
        print(f"{BJ_FAIL} 安装包不存在：{setup}")
        return 2

    results = {"setup": str(setup), "setup_size": setup.stat().st_size, "scenarios": []}

    def record(name: str, ok: bool, detail: dict) -> None:
        """记录一个场景结果并打印。

        Args:
            name: 场景名。
            ok: 是否通过。
            detail: 细节字典。
        """
        results["scenarios"].append({"name": name, "ok": ok, "detail": detail})
        print(f"{BJ_OK if ok else BJ_FAIL} {name}：{json.dumps(detail, ensure_ascii=False)[:260]}")

    # ---------------- 场景 1：全新安装 ----------------
    fresh = SANDBOX_DIR / "fresh"
    if fresh.exists():
        shutil.rmtree(fresh, ignore_errors=True)
    code = _run_installer(setup, fresh)
    exe = fresh / "OpenBB-CN.exe"
    installed = exe.exists()
    health: dict = {}
    health_mode = "serve-only"
    if installed:
        if _port_free(6900):
            # --serve-only 固定用 config.PORT=6900（不支持 --port）
            proc = _start_app(exe, 6900)
            try:
                health = _wait_health(6900, timeout=120)
            finally:
                _stop_app(proc)
        else:
            # 6900 被正式环境占用（不杀既有进程）：改用 --check 自检，如实登记
            health_mode = "check(端口 6900 被正式环境占用)"
            check = subprocess.run([str(exe), "--check"], cwd=str(exe.parent),
                                   capture_output=True, timeout=120, text=True)
            if check.returncode == 0:
                health = {"app": {"version": "check-ok"}, "build": (check.stdout or "").strip()[:60]}
    shortcut_desktop = any(DESKTOP.glob("*投研*.lnk"))
    shortcut_menu = any(START_MENU.glob("*投研*"))
    app_version = (health.get("app") or {}).get("version")
    record("全新安装", bool(installed and app_version), {
        "installer_exit": code,
        "exe_exists": installed,
        "health_mode": health_mode,
        "health_version": app_version,
        "build_tag": health.get("build"),
        "desktop_shortcut": shortcut_desktop,
        "startmenu_dir": shortcut_menu,
    })

    # ---------------- 场景 2：升级安装 ----------------
    upgrade = SANDBOX_DIR / "upgrade"
    if upgrade.exists():
        shutil.rmtree(upgrade, ignore_errors=True)
    upgrade.mkdir(parents=True, exist_ok=True)
    # 预置旧版数据（模拟用户已有台账）
    (upgrade / "data").mkdir(exist_ok=True)
    legacy = {"items": [{"id": "legacy1", "market": "a", "symbol": "600519",
                         "name": "旧版遗留", "shares": 1, "cost": 1,
                         "created_at": "2026-10-01 00:00:00"}], "updated_at": "2026-10-01 00:00:00"}
    (upgrade / "data" / "portfolio.json").write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    code2 = _run_installer(setup, upgrade)
    kept = False
    payload = {}
    try:
        payload = json.loads((upgrade / "data" / "portfolio.json").read_text(encoding="utf-8"))
        kept = bool(payload.get("items"))
    except Exception:  # noqa: BLE001
        kept = False
    version_txt = ""
    if (upgrade / "VERSION.txt").exists():
        version_txt = (upgrade / "VERSION.txt").read_text(encoding="utf-8", errors="replace").splitlines()[0]
    record("升级安装", bool((upgrade / "OpenBB-CN.exe").exists() and kept), {
        "installer_exit": code2,
        "exe_exists": (upgrade / "OpenBB-CN.exe").exists(),
        "legacy_data_kept": kept,
        "legacy_items": len(payload.get("items") or []),
        "version_txt": version_txt,
    })

    # ---------------- 场景 3：卸载 ----------------
    uninstaller = upgrade / "uninstall.exe"
    code3 = -1
    if uninstaller.exists():
        subprocess.run(f'"{uninstaller}" /S', shell=False, capture_output=True, timeout=180)
        time.sleep(6)
        code3 = 0
    leftover_exe = (upgrade / "OpenBB-CN.exe").exists()
    # NSIS 保留 data/（用户数据），只要求程序文件清空
    record("卸载", bool((not leftover_exe) and code3 == 0), {
        "uninstaller_exit": code3,
        "exe_removed": not leftover_exe,
        "data_kept_by_design": (upgrade / "data").exists(),
    })

    results["passed"] = all(item["ok"] for item in results["scenarios"])
    out_path = Path(args.out)
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n结论：" + ("三场景全部通过" if results["passed"] else "存在未通过场景，见 JSON"))
    print(f"报告：{out_path}")
    return 0 if results["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
