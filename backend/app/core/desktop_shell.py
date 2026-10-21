# -*- coding: utf-8 -*-
"""桌面壳能力（冻结形态与源码桌面模式共用）。

职责（规范 §13.6-1 / §13.6-5）：
1. ``redirect_stdio``：窗口化构建（console=False）下 sys.stdout/stderr 为 None，
   必须重定向到日志文件，否则 uvicorn 初始化与 print 会抛错；
2. ``wait_for_server``：轮询 ``/api/health`` 等待本地服务就绪（超时给中文提示）；
3. ``check_webview2``：检测 WebView2 运行时（注册表优先、文件兜底）；
4. ``open_window``：用 pywebview 打开主窗口；WebView2 缺失时给中文引导而非白屏；
5. ``show_error``：Windows 消息框 + 控制台双通道的中文错误提示。

本模块不依赖 backend 业务模块（只依赖 config 的路径常量），便于被冻结入口
``backend/app/main.py`` 与开发启动器 ``desktop.py`` 同时复用。
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import IO, Optional, Tuple

WINDOW_TITLE = "OpenBB 中文投研工作台"
WINDOW_SIZE = (1366, 768)
WINDOW_MIN_SIZE = (1024, 640)

# WebView2 Runtime 的两个常见注册位置（32/64 位视图）
_WEBVIEW2_REG_KEYS = (
    r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"
    r"\{F1E7E1C4-4C48-4B2E-9C8B-8E9A5B1B3F0A}",
    r"SOFTWARE\Microsoft\EdgeUpdate\Clients"
    r"\{F1E7E1C4-4C48-4B2E-9C8B-8E9A5B1B3F0A}",
)
_WEBVIEW2_DIRS = (
    Path(r"C:\Program Files (x86)\Microsoft\EdgeWebView\Application"),
    Path(r"C:\Program Files\Microsoft\EdgeWebView\Application"),
)


def show_error(message: str, title: str = WINDOW_TITLE) -> None:
    """显示中文错误：Windows 优先消息框，失败退回控制台。

    Args:
        message: 中文错误文案（含下一步指引）。
        title: 消息框标题。

    Returns:
        None: 仅做提示，不抛出异常。
    """
    printed = False
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
            printed = True
        except Exception:  # noqa: BLE001 - 无 GUI 时退回控制台
            printed = False
    if not printed:
        stream = sys.stderr or sys.stdout
        if stream is not None:
            print(f"[错误] {message}", file=stream, flush=True)


def redirect_stdio(log_dir: Path) -> Optional[Path]:
    """把 stdout/stderr 重定向到 ``logs/stdout.log``（窗口化构建下为 None）。

    Args:
        log_dir: 日志目录（冻结模式为 EXE_DIR/logs）。

    Returns:
        Optional[Path]: 日志文件路径；创建失败返回 None（不阻断启动）。
    """
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "stdout.log"
        handle: IO[str] = open(path, "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = handle
        if sys.stderr is None:
            sys.stderr = handle
        return path
    except Exception:  # noqa: BLE001 - 重定向失败不应阻断启动
        return None


def wait_for_server(url: str, timeout: float = 40.0, interval: float = 0.5) -> bool:
    """轮询健康检查地址，直到服务返回可解析 JSON。

    Args:
        url: ``/api/health`` 完整地址。
        timeout: 最长等待秒数。
        interval: 轮询间隔秒数。

    Returns:
        bool: 就绪返回 True，超时返回 False。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(interval)
    return False


def check_webview2() -> Tuple[bool, str]:
    """检测 WebView2 运行时是否可用。

    先查注册表（EdgeUpdate Clients 的 WebView2 GUID），再查常见安装目录，
    两者都没有才判缺失——避免仅因注册表视图差异误报。

    Returns:
        Tuple[bool, str]: (是否可用, 中文说明/版本信息)。
    """
    if sys.platform != "win32":
        return True, "非 Windows，跳过 WebView2 检测"
    try:
        import winreg
    except Exception:  # noqa: BLE001 - 非预期环境
        return True, "无法导入 winreg，跳过检测"

    for key_path in _WEBVIEW2_REG_KEYS:
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(root, key_path) as key:
                    version, _ = winreg.QueryValueEx(key, "pv")
                    if version:
                        return True, f"WebView2 运行时已安装（{version}）"
            except OSError:
                continue
    for directory in _WEBVIEW2_DIRS:
        if directory.exists() and any(directory.glob("*/msedgewebview2.exe")):
            return True, f"WebView2 运行时已安装（{directory}）"
    return False, "未检测到 Microsoft Edge WebView2 运行时"


def open_window(
    url: str,
    title: str = WINDOW_TITLE,
    icon: Optional[Path] = None,
) -> bool:
    """打开桌面主窗口（pywebview），WebView2 缺失时给中文引导。

    Args:
        url: 本地服务地址（如 http://127.0.0.1:6900）。
        title: 窗口标题。
        icon: 窗口图标路径（Windows 下由 exe 图标决定，此处兼容其它平台）。

    Returns:
        bool: 窗口正常关闭返回 True；依赖缺失返回 False（调用方决定退出码）。
    """
    ok, detail = check_webview2()
    if not ok:
        show_error(
            "启动失败：本机缺少 Microsoft Edge WebView2 运行时。\n\n"
            "该运行时为 Windows 10/11 常见组件，缺失时界面无法显示。\n"
            "请安装后重试：https://developer.microsoft.com/microsoft-edge/webview2/\n"
            "（选择「Evergreen Standalone Installer」的 x64 版本）\n\n"
            "提示：也可先运行 OpenBB-CN.exe --check 查看自检结果。"
        )
        return False
    try:
        import webview
    except Exception as exc:  # noqa: BLE001 - 缺 pywebview 时给中文指引
        show_error(
            "启动失败：缺少 pywebview 组件。\n\n"
            f"（原始错误：{type(exc).__name__}）\n"
            "此问题通常出现在安装包不完整，请重新安装。"
        )
        return False

    webview.create_window(
        title,
        url,
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        min_size=WINDOW_MIN_SIZE,
        text_select=True,
    )
    icon_text = str(icon) if icon and Path(icon).exists() else None
    try:
        webview.start(icon=icon_text)
    except TypeError:  # 旧版本不支持 icon 参数
        webview.start()
    return True


def resolve_icon(exe_dir: Path) -> Optional[Path]:
    """定位图标文件（优先 EXE 同级 assets/icon.ico）。

    Args:
        exe_dir: exe 所在目录或项目根目录。

    Returns:
        Optional[Path]: 存在的图标路径；缺失返回 None。
    """
    for candidate in (exe_dir / "assets" / "icon.ico", exe_dir / "icon.ico"):
        if candidate.exists():
            return candidate
    return None
