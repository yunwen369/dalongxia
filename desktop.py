# -*- coding: utf-8 -*-
"""桌面壳启动器（阶段 6）：pywebview 独立窗口 + 本机 uvicorn 服务。

行为约定：
1. 端口固定 `127.0.0.1:6900`（不另起端口，规范 §5.4）；
2. 启动前先探测：
   - 端口空闲 → 以子进程启动 uvicorn，等待 /api/health 就绪；
   - 端口已被**本应用**占用（health.build 存在）→ 直接复用，退出时不动它；
   - 端口被其他进程占用 → 弹出中文提示并退出，不静默换端口；
3. 窗口：1366×768，最小 1024×640，标题「OpenBB 中文投研工作台」，图标 assets/icon.ico；
4. 关闭窗口 → 终止本次拉起的 uvicorn 子进程（进程干净退出）；
5. `--check` 仅做启动前检查并打印结果（供自动化验收），不创建窗口。

依赖：pywebview（requirements.txt 已锁定 6.2.1）、uvicorn。
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
import urllib.request
from pathlib import Path
from typing import Optional

PROJECT_DIR = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 6900
BASE_URL = f"http://{HOST}:{PORT}"
HEALTH_URL = f"{BASE_URL}/api/health"
WINDOW_TITLE = "OpenBB 中文投研工作台"
ICON_PATH = PROJECT_DIR / "assets" / "icon.ico"


def _show_error(message: str) -> None:
    """跨形态显示中文错误：优先 Windows 消息框，失败则打印到控制台。"""
    printed = False
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, WINDOW_TITLE, 0x10)
            printed = True
        except Exception:  # noqa: BLE001 - 无 GUI 时退回控制台
            printed = False
    if not printed:
        print(f"[错误] {message}", flush=True)


def _port_open(host: str = HOST, port: int = PORT, timeout: float = 0.6) -> bool:
    """检测端口是否有进程监听。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def _health(timeout: float = 1.5) -> Optional[dict]:
    """请求 /api/health，成功返回 JSON，失败返回 None。"""
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError):
        return None


def _wait_ready(timeout: float = 40.0) -> bool:
    """等待服务就绪（health 可解析）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _health():
            return True
        time.sleep(0.5)
    return False


def _start_server() -> subprocess.Popen:
    """以子进程启动 uvicorn（与开发模式同一入口、同一端口）。"""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    command = [
        sys.executable, "-m", "uvicorn", "backend.app.main:app",
        "--host", HOST, "--port", str(PORT), "--log-level", "warning",
    ]
    creation = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    return subprocess.Popen(command, cwd=str(PROJECT_DIR), env=env, creationflags=creation)


def _stop_server(process: subprocess.Popen) -> None:
    """终止本次拉起的服务进程（含子进程树）。"""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()


def _expected_build() -> str:
    """读取当前源码的 BUILD_TAG，用于识别端口上的实例是否与本目录一致。"""
    try:
        from backend.app import config as app_config

        return str(app_config.BUILD_TAG)
    except Exception:  # noqa: BLE001 - 导入失败不影响启动判断
        return ""


def _resolve_service() -> tuple[Optional[subprocess.Popen], str]:
    """决定复用已有服务还是新起一个。

    Returns:
        (子进程或 None, 中文状态说明)
    """
    if _port_open():
        health = _health()
        if health and health.get("data"):
            running = str(health["data"].get("build") or "")
            expected = _expected_build()
            if expected and running and running != expected:
                # 旧版本实例占用端口：界面会是旧代码，必须提示用户处理，不自动杀进程
                return None, f"旧版本实例占用（运行中 {running} / 当前 {expected}）"
            return None, "检测到本应用已在运行，直接复用现有服务（关闭窗口不会停止它）"
        return None, "端口冲突"
    process = _start_server()
    if not _wait_ready():
        _stop_server(process)
        return None, "启动超时"
    return process, "已启动本地服务"


def run_check() -> int:
    """启动前检查（不建窗口）：端口状态 + 健康信息 + 资源可用性。"""
    print("== OpenBB 桌面壳自检 ==", flush=True)
    print(f"项目目录：{PROJECT_DIR}", flush=True)
    print(f"监听地址：{BASE_URL}", flush=True)
    print(f"图标：{ICON_PATH}（存在={ICON_PATH.exists()}）", flush=True)
    try:
        import webview  # noqa: F401

        print("pywebview：已安装", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"pywebview：未安装（{type(exc).__name__}）", flush=True)
        return 2

    if _port_open():
        health = _health()
        if health and health.get("data"):
            app = (health["data"].get("app") or {})
            running = str(health["data"].get("build") or "")
            expected = _expected_build()
            if expected and running and running != expected:
                print(f"端口 {PORT}：被旧版本实例占用（运行中 {running} / 当前 {expected}），桌面模式将提示先关闭", flush=True)
                return 3
            print(f"端口 {PORT}：已被本应用占用（{app.get('name')} v{app.get('version')}），可直接复用", flush=True)
            return 0
        print(f"端口 {PORT}：被其他进程占用，桌面模式将提示冲突并退出", flush=True)
        return 3

    print(f"端口 {PORT}：空闲，桌面模式将自动拉起 uvicorn", flush=True)
    return 0


def main() -> int:
    """入口：解析参数 → 准备服务 → 打开窗口 → 关闭后清理。"""
    parser = argparse.ArgumentParser(description="OpenBB 中文投研工作台 · 桌面壳")
    parser.add_argument("--check", action="store_true", help="仅做启动前检查，不打开窗口")
    args = parser.parse_args()

    if args.check:
        return run_check()

    try:
        import webview
    except Exception as exc:  # noqa: BLE001 - 缺依赖时给中文指引
        _show_error(
            "桌面模式需要 pywebview，但当前环境未安装。\n\n"
            "请在项目目录执行：\n.venv\\Scripts\\python.exe -m pip install pywebview\n\n"
            f"（原始错误：{type(exc).__name__}）"
        )
        return 2

    process, status = _resolve_service()
    if status in ("端口冲突", "启动超时") or status.startswith("旧版本实例占用"):
        if status.startswith("旧版本实例占用"):
            _show_error(
                f"端口 {PORT} 被旧版本实例占用（{status}）。\n\n"
                "请先关闭旧窗口/命令行进程（Ctrl+C 或任务管理器结束 python.exe），再重新启动桌面模式。\n"
                "为避免数据错乱，本应用不会自动终止既有进程。"
            )
        elif status == "端口冲突":
            _show_error(
                f"端口 {PORT} 已被其他程序占用，无法启动桌面窗口。\n\n"
                "请关闭占用该端口的程序后重试；本应用不会自动更换端口（规范 §5.4）。\n"
                "可用命令查看占用进程：netstat -ano | findstr :6900"
            )
        else:
            _show_error(
                "本地服务启动超时（40 秒）。\n\n"
                "请检查 logs\\app.log，或在开发模式下运行 start.bat 查看报错。"
            )
        return 3

    print(f"[桌面模式] {status}：{BASE_URL}", flush=True)
    window = webview.create_window(
        WINDOW_TITLE,
        BASE_URL,
        width=1366,
        height=768,
        min_size=(1024, 640),
        text_select=True,
    )

    def on_closed() -> None:
        """窗口关闭：停止本次拉起的服务，避免残留进程。"""
        if process is not None:
            _stop_server(process)
            print("[桌面模式] 服务已停止，进程退出", flush=True)

    window.events.closed += on_closed
    try:
        # 图标：pywebview 在 Windows 下由可执行文件图标决定，start(icon=) 供 Linux/macOS 生效；
        # 仍显式传入，保证三端一致意图，并把 ico 一并用于任务栏（打包阶段写入 exe）。
        try:
            webview.start(icon=str(ICON_PATH) if ICON_PATH.exists() else None)
        except TypeError:
            webview.start()
    finally:
        if process is not None:
            _stop_server(process)
    return 0


if __name__ == "__main__":
    sys.exit(main())
