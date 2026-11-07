# -*- coding: utf-8 -*-
"""网页访问支撑：局域网地址探测、访问二维码、默认浏览器打开、系统托盘常驻。

定位：本模块只服务「以浏览器为客户端」的网页形态，不涉及任何行情数据逻辑。

与 `desktop_shell` 的分工：
- `desktop_shell`：pywebview 内嵌窗口形态（WebView2 检查、窗口生命周期）；
- 本模块：浏览器形态（打开默认浏览器、托盘常驻、局域网地址与二维码）。

设计约定：
1. 对外展示的访问地址一律由本模块生成，调用方不得自行拼接，避免监听地址
   与"浏览器可用的地址"混淆（监听 0.0.0.0 时 `http://0.0.0.0:6900` 不可作为访问目标）；
2. 二维码用 segno（纯 Python，已随包分发），输出 PNG data URI，前端可直接 `<img src>`；
3. 托盘依赖缺失时静默降级（进程仍可通过任务管理器或 Ctrl+C 结束），不阻断启动。
"""

from __future__ import annotations

import base64
import io
import os
import socket
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Callable, Dict, List, Optional

TRAY_TITLE = "OpenBB 中文投研工作台"


def lan_addresses() -> List[str]:
    """枚举本机可用于局域网访问的 IPv4 地址（去重、排除回环）。

    取址顺序：先取默认出口网卡（UDP connect 不实际发包），再用主机名解析补充
    多网卡/多网段场景。任一来源失败都不影响其余来源。

    Returns:
        List[str]: 形如 ``["192.168.1.5"]``；无可用网卡时为空列表。
    """
    found: List[str] = []
    for probe in ("223.5.5.5", "8.8.8.8"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(0.4)
                sock.connect((probe, 80))
                address = sock.getsockname()[0]
        except OSError:
            continue
        if address and not address.startswith("127.") and address not in found:
            found.append(address)
    try:
        for address in socket.gethostbyname_ex(socket.gethostname())[2]:
            if address and not address.startswith("127.") and address not in found:
                found.append(address)
    except OSError:
        pass
    return found


def access_points(port: int, lan_visible: bool) -> List[Dict[str, str]]:
    """生成访问入口清单（本机 + 局域网），供托盘与前端「访问方式」展示。

    Args:
        port: 服务监听端口。
        lan_visible: 是否已放开局域网监听（False 时只返回本机入口）。

    Returns:
        List[Dict[str, str]]: 每项含 ``scope``（local/lan）、``label``（中文说明）、
        ``url``（可点击地址）。
    """
    points: List[Dict[str, str]] = [
        {"scope": "local", "label": "本机", "url": f"http://127.0.0.1:{port}"}
    ]
    if not lan_visible:
        return points
    for address in lan_addresses():
        points.append({
            "scope": "lan",
            "label": f"局域网（{address}）",
            "url": f"http://{address}:{port}",
        })
    return points


def qr_data_uri(text: str, scale: int = 6) -> str:
    """把文本渲染成 PNG 二维码的 data URI（前端可直接作为 img 的 src）。

    Args:
        text: 待编码内容，通常是一个访问地址。
        scale: 每个模块的像素边长，越大图越清晰（默认 6）。

    Returns:
        str: ``data:image/png;base64,...``；segno 缺失或编码失败时返回空串。
    """
    if not text:
        return ""
    try:
        import segno
    except Exception:  # noqa: BLE001 - 二维码是增强项，缺库时降级为空
        return ""
    try:
        buffer = io.BytesIO()
        segno.make(text, error="m").save(buffer, kind="png", scale=scale, border=2)
    except Exception:  # noqa: BLE001 - 编码失败不阻断页面渲染
        return ""
    payload = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{payload}"


def open_browser(url: str) -> bool:
    """用系统默认浏览器打开地址。

    Args:
        url: 完整访问地址。

    Returns:
        bool: 已交给系统浏览器返回 True；调用失败返回 False。
    """
    if not url:
        return False
    try:
        return bool(webbrowser.open(url))
    except Exception:  # noqa: BLE001 - 无图形环境时降级为不打开
        return False


def open_path(target: Path) -> None:
    """用系统文件管理器打开目录（失败静默，不影响主流程）。

    Args:
        target: 目录路径。
    """
    try:
        target.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(target))  # noqa: S606 - 仅打开目录，不执行用户文件
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])  # noqa: S603,S607
        else:
            subprocess.Popen(["xdg-open", str(target)])  # noqa: S603,S607
    except Exception:  # noqa: BLE001 - 打开失败不影响服务
        pass


def _tray_image(icon_path: Optional[Path]):
    """加载托盘图标；缺图标时用纯色方块兜底。

    Args:
        icon_path: ico 文件路径，可为 None。

    Returns:
        PIL.Image.Image | None: 图标对象；PIL 不可用返回 None。
    """
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001 - 无 Pillow 时退回无图标模式
        return None
    if icon_path and Path(icon_path).exists():
        try:
            return Image.open(str(icon_path))
        except Exception:  # noqa: BLE001 - 图标损坏时继续走兜底
            pass
    return Image.new("RGB", (64, 64), (180, 125, 160))


def run_tray(
    open_url: str,
    points: List[Dict[str, str]],
    data_dir: Optional[Path] = None,
    icon_path: Optional[Path] = None,
    on_quit: Optional[Callable[[], None]] = None,
) -> bool:
    """托盘常驻并阻塞主线程（浏览器形态的退出入口）。

    菜单：打开界面 / 访问地址（本机、各局域网地址）/ 打开数据目录 / 退出。
    依赖缺失或图标不可用时降级为「等待 Ctrl+C」的阻塞循环，保证进程不空转退出。

    Args:
        open_url: 默认打开的界面地址（本机回环地址）。
        points: 访问入口清单，来自 :func:`access_points`。
        data_dir: 数据目录，菜单项「打开数据目录」使用。
        icon_path: 托盘图标路径。
        on_quit: 用户选择「退出」时的回调（用于停服务、落日志）。

    Returns:
        bool: 用户从托盘主动退出返回 True；托盘不可用而降级返回 False。
    """
    try:
        import pystray
        from pystray import MenuItem as Item
    except Exception:  # noqa: BLE001 - 未安装托盘依赖
        _block_until_interrupt()
        return False

    image = _tray_image(icon_path)
    if image is None:
        _block_until_interrupt()
        return False

    def _open(url: str) -> Callable[[], None]:
        """构造「点击后打开指定地址」的菜单回调。

        Args:
            url: 待打开地址。

        Returns:
            Callable[[], None]: 无参回调。
        """
        return lambda: open_browser(url)

    remote_items = [
        Item(point["label"], _open(point["url"])) for point in points if point["scope"] != "local"
    ]
    entries = [Item("打开界面", _open(open_url), default=True)]
    if remote_items:
        entries.append(Item("访问地址（手机/远程）", pystray.Menu(*remote_items)))
    if data_dir is not None:
        entries.append(Item("打开数据目录", lambda: open_path(data_dir)))
    entries.append(pystray.Menu.SEPARATOR)
    entries.append(Item("退出", lambda: _quit(icon, on_quit)))

    icon = pystray.Icon("openbb-cn", image, TRAY_TITLE, pystray.Menu(*entries))
    icon.run()
    return True


def _quit(icon, on_quit: Optional[Callable[[], None]]) -> None:
    """托盘「退出」：先停图标，再执行清理回调。

    Args:
        icon: pystray.Icon 实例。
        on_quit: 清理回调，可为 None。
    """
    try:
        icon.stop()
    except Exception:  # noqa: BLE001 - 停止失败不阻断退出
        pass
    if on_quit is not None:
        try:
            on_quit()
        except Exception:  # noqa: BLE001 - 清理失败仍需退出
            pass


def _block_until_interrupt() -> None:
    """无托盘时的阻塞兜底：保持进程存活，直到 Ctrl+C 或进程被结束。"""
    stop = threading.Event()
    try:
        stop.wait()
    except KeyboardInterrupt:  # pragma: no cover - 交互式退出
        pass
