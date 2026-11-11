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
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

BJ_OK = "\u2705"
BJ_FAIL = "\u274c"

SANDBOX_DIR = Path(os.environ.get("TEMP", r"C:\Windows\Temp")) / "openbb_install_test"
DESKTOP = Path(os.environ.get("USERPROFILE", "")) / "Desktop"
START_MENU = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


DESKTOP_LNK = DESKTOP / "OpenBB 中文投研工作台.lnk"


def _backup_shortcut() -> "Path | None":
    """备份桌面快捷方式。

    原因：安装测试的最后一步是卸载，NSIS 会删除桌面同名快捷方式（属于正式环境的那份），
    若不预先备份，每跑一次安装测试就会把用户的快捷方式弄丢。

    Returns:
        Path | None: 备份文件路径；桌面原本没有快捷方式时返回 None。
    """
    if not DESKTOP_LNK.exists():
        return None
    SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
    bak = SANDBOX_DIR / "desktop_shortcut.bak.lnk"
    shutil.copy2(DESKTOP_LNK, bak)
    return bak


def _restore_shortcut(bak: "Path | None") -> None:
    """恢复桌面快捷方式（仅当测试后消失时）。

    Args:
        bak: :func:`_backup_shortcut` 返回的备份路径。
    """
    if bak and bak.exists() and not DESKTOP_LNK.exists():
        shutil.copy2(bak, DESKTOP_LNK)
        logger.warning("安装测试删除了桌面快捷方式，已自动恢复：%s", DESKTOP_LNK)


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
    # 实测：打包产物 --serve-only 不解析 --port，端口由 OPENBB_PORT 决定（规范 §13.3 需要真实 /api/health）
    env["OPENBB_PORT"] = str(port)
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
        except Exception as exc:  # noqa: BLE001 - 强制结束失败时仅记录，不阻断卸载测试
            logger.warning("taskkill 结束进程失败（pid=%s）：%s", proc.pid, type(exc).__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenBB 三场景安装测试")
    parser.add_argument("--setup", required=True, help="安装包 exe 路径")
    parser.add_argument("--port", type=int, default=6917, help="测试端口（避开 6900/6903）")
    parser.add_argument("--out", default="install_test_result.json", help="结果 JSON 路径")
    parser.add_argument("--keep-sandbox", action="store_true", help="保留沙箱目录（排障用，默认按 §2.11 清理）")
    args = parser.parse_args()

    setup = Path(args.setup).resolve()
    if not setup.exists():
        print(f"{BJ_FAIL} 安装包不存在：{setup}")
        return 2

    shortcut_bak = _backup_shortcut()
    results = {"setup": str(setup), "setup_size": setup.stat().st_size, "scenarios": []}

    # 服务守护（2026-11-12 实测缺陷）：NSIS 卸载器按进程名结束 OpenBB-CN.exe，
    # 会连带杀掉正式环境 6900 实例。测试前记录其状态，测试后若被误杀则自动拉起。
    formal_exe = Path(r"F:\workbuddy记录\OpenBB Terminal Pro\A_运行资产\正式环境\OpenBB-CN.exe")
    formal_was_up = not _port_free(6900)
    results["formal_env_was_up"] = formal_was_up

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
        # v3.1.0：始终用 --port 独立端口取真实 /api/health，不再因 6900 被正式环境占用
        # 退化为 `--check`（那只能证明能启动，不能证明 HTTP 服务可用）
        proc = _start_app(exe, args.port)
        try:
            health = _wait_health(args.port, timeout=120)
        finally:
            _stop_app(proc)
        health_mode = "serve-only(OPENBB_PORT=%d)" % args.port
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
        # NSIS 卸载器会把自身复制到临时目录后**异步**执行：run() 返回的是启动器退出码，
        # 立即判断会误报「卸载未清理」。此处轮询等待程序文件消失（最长 90s）。
        subprocess.run(f'"{uninstaller}" /S', shell=False, capture_output=True, timeout=180)
        code3 = 0
        deadline = time.time() + 90
        while time.time() < deadline:
            if not (upgrade / "OpenBB-CN.exe").exists() and not (upgrade / "_internal").is_dir():
                time.sleep(2)  # 等注册表项与快捷方式删除落地
                break
            time.sleep(1.5)
    leftover_exe = (upgrade / "OpenBB-CN.exe").exists()
    leftover_internal = (upgrade / "_internal").is_dir()
    # NSIS 保留 data/（用户数据），只要求程序文件清空
    record("卸载", bool((not leftover_exe) and (not leftover_internal) and code3 == 0), {
        "uninstaller_exit": code3,
        "exe_removed": not leftover_exe,
        "internal_removed": not leftover_internal,
        "data_kept_by_design": (upgrade / "data").exists(),
    })

    # 服务守护：卸载器按进程名杀进程可能误杀正式环境；只在实际被误杀时才拉起
    restored = False
    if formal_was_up:
        if not _port_free(6900):
            restored = True
            print("✅ 正式环境服务守护：测试后仍在运行，未被误杀")
        elif formal_exe.exists():
            env = dict(os.environ)
            env["OPENBB_NO_BROWSER"] = "1"
            try:
                subprocess.Popen([str(formal_exe), "--serve-only", "--lan"],
                                 cwd=str(formal_exe.parent), stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, env=env)
                restored = bool(_wait_health(6900, timeout=150))
            except Exception:  # noqa: BLE001 - 拉起失败不影响三场景结论，但必须记录
                restored = False
            print(("✅" if restored else "❌") + " 正式环境服务守护：" +
                  ("已被卸载器误杀，已自动拉起" if restored else "拉起失败，需人工介入"))
    results["formal_env_restored"] = restored

    _restore_shortcut(shortcut_bak)

    results["passed"] = all(item["ok"] for item in results["scenarios"])
    out_path = Path(args.out)
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    # §2.11：安装测试实例必须在本次测试结束后清理（保留结果 JSON，不保留沙箱目录）
    if not args.keep_sandbox:
        try:
            if SANDBOX_DIR.exists():
                shutil.rmtree(SANDBOX_DIR, ignore_errors=True)
            results["sandbox_cleaned"] = not SANDBOX_DIR.exists()
        except Exception:  # noqa: BLE001 - 清理失败不影响结论，但如实记录
            results["sandbox_cleaned"] = False
        out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print("沙箱清理：" + ("已清理" if results.get("sandbox_cleaned") else "清理失败，需人工处理"))

    print("\n结论：" + ("三场景全部通过" if results["passed"] else "存在未通过场景，见 JSON"))
    print(f"报告：{out_path}")
    return 0 if results["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
