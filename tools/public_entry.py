# -*- coding: utf-8 -*-
"""公网入口守护：起本机服务 + Cloudflare 临时隧道，并把最新网址同步到桌面图标。

为什么需要它
------------
``OpenBB-CN.exe --public`` 只在「浏览器模式」下起隧道，且隧道地址仅存在于托盘菜单里，
用户下次开机地址就变了、桌面图标还指向旧地址。快速隧道（``*.trycloudflare.com``）
本身是**免账号**就能拿到 HTTPS 公网地址的唯一方式，代价是地址随机且重启即变。

本脚本把「地址会变」这件事对人屏蔽掉：
    地址变了 → 自动重写桌面 ``.url`` 快捷方式 → 用户永远双击同一个图标。

工作流程
--------
1. 探测 6900 端口：没起服务就先拉 ``OpenBB-CN.exe --serve-only``（隐藏窗口）；
2. 起 ``cloudflared tunnel --url http://127.0.0.1:6900 --no-autoupdate``；
3. 从 cloudflared 输出解析出 ``https://<随机串>.trycloudflare.com``；
4. 写 ``<安装目录>/data/public_url.txt``；
5. 生成/覆盖桌面 ``OpenBB 工作台（公网）.url``，指向 ``<地址>/auth?token=<口令>``；
6. 进程常驻；隧道意外退出时自动重连（最多 20 次，间隔 5 秒递增）。

前置：``OPENBB_ACCESS_PASSWORD`` 必须已设置（用户级环境变量即可），否则拒绝起隧道。

用法：
    python tools/public_entry.py            # 正常守护（可放开机自启）
    python tools/public_entry.py --once     # 起一次并把地址打印出来（调试用）
    python tools/public_entry.py --no-desktop  # 不写桌面图标

退出码：0=正常退出；1=前置条件不满足；2=隧道始终拿不到地址。
"""

from __future__ import annotations

import argparse
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

PROJECT_DIR = Path(__file__).resolve().parents[1]
FROZEN_DIR = PROJECT_DIR.parent / "正式环境"
INSTALL_DIR = FROZEN_DIR if (FROZEN_DIR / "OpenBB-CN.exe").exists() else PROJECT_DIR
EXE = INSTALL_DIR / "OpenBB-CN.exe"
CLOUDFLARED = INSTALL_DIR / "data" / "bin" / "cloudflared.exe"
PORT = 6900
# 单实例锁：用「绑定本地端口」充当互斥量（进程退出后操作系统自动释放，
# 不会留下需要人工清理的僵尸锁文件）。多开守护会互相抢着起服务、抢着建隧道，
# 实测表现为服务被反复杀掉又重启（2026-11-09 app.log 里 1 小时内 4 次重启）。
LOCK_PORT = 6901
_LOCK_SOCK: Optional[socket.socket] = None
SHORTCUT_NAME = "OpenBB 工作台（公网）.url"
URL_PATTERN = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")


def acquire_single_instance() -> bool:
    """抢占单实例锁。

    Returns:
        bool: 抢到锁返回 True；已有实例在跑返回 False。
    """
    global _LOCK_SOCK
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", LOCK_PORT))
        sock.listen(1)
    except OSError:
        sock.close()
        return False
    _LOCK_SOCK = sock  # 必须持有引用，否则被回收即释放
    return True


def log(message: str) -> None:
    """带时间戳打印（守护进程日志直接看终端/日志文件）。"""
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def redirect_output_to_file() -> Path:
    """把 stdout/stderr 落到 ``data/public_entry.log``。

    由 ``pythonw.exe`` 拉起时没有控制台，日志会全部丢失；重定向到文件后，
    「双击了但没反应」这类问题可以直接看日志定位。

    Returns:
        Path: 日志文件路径。
    """
    log_path = INSTALL_DIR / "data" / "public_entry.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = handle
    sys.stderr = handle
    return log_path


def port_open(port: int) -> bool:
    """探测本机端口是否已有服务监听。

    Args:
        port: 端口号。

    Returns:
        bool: 可连接为 True。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def wait_health(timeout: float = 120.0) -> bool:
    """等服务 /api/health 通。

    Args:
        timeout: 最长等待秒数。

    Returns:
        bool: 通为 True。
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with opener.open(f"http://127.0.0.1:{PORT}/api/health", timeout=3) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
    return False


def start_server() -> Optional[subprocess.Popen]:
    """必要时拉起 OpenBB-CN.exe --serve-only（隐藏窗口）。

    Returns:
        Optional[subprocess.Popen]: 新起的服务进程；已在运行时返回 None。
    """
    if port_open(PORT):
        # 端口在监听不等于可用：进程可能已卡死。这里给一次短探活，结果如实记日志。
        if wait_health(timeout=5):
            log("6900 已在监听且健康，复用现有服务")
        else:
            log("⚠ 6900 在监听但健康检查未通过，先复用观察")
        return None
    if not EXE.exists():
        log(f"✗ 未找到 {EXE}")
        return None
    log(f"拉起服务：{EXE.name} --serve-only --lan")
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    proc = subprocess.Popen(
        [str(EXE), "--serve-only", "--lan"],
        cwd=str(INSTALL_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    if wait_health():
        log("服务就绪")
    else:
        log("⚠ 服务 120 秒内未通过健康检查，仍继续尝试建隧道")
    return proc


def start_tunnel() -> tuple[Optional[subprocess.Popen], str]:
    """起 quick tunnel 并解析出公网地址。

    输出重定向到 ``data/tunnel.log`` 而非管道：管道会把 cloudflared 的生命周期
    绑在调用进程上，父进程一退管道就断、隧道随之失效（实测表现为边缘 530）。

    Returns:
        tuple[Optional[subprocess.Popen], str]: ``(隧道进程, 公网地址)``；失败时地址为空串。
    """
    if not CLOUDFLARED.exists():
        log(f"✗ 未找到 {CLOUDFLARED}")
        return None, ""
    log("建立 Cloudflare 临时隧道…")
    log_path = INSTALL_DIR / "data" / "tunnel.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = open(log_path, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [str(CLOUDFLARED), "tunnel", "--url", f"http://127.0.0.1:{PORT}", "--no-autoupdate"],
        cwd=str(INSTALL_DIR / "data" / "bin"),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
    )

    def _scan() -> str:
        """从隧道日志里扫出公网地址。"""
        try:
            text = log_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""
        match = URL_PATTERN.search(text)
        return match.group(0) if match else ""

    url = ""
    deadline = time.time() + 90
    while time.time() < deadline:
        url = _scan()
        if url:
            log(f"拿到公网地址：{url}")
            return proc, url
        if proc.poll() is not None:
            log("✗ cloudflared 提前退出，末尾日志：")
            try:
                tail = log_path.read_text(encoding="utf-8", errors="ignore").strip().splitlines()[-6:]
                for line in tail:
                    log(f"    {line[:180]}")
            except OSError:
                pass
            return proc, ""
        time.sleep(1)
    return proc, ""


def probe(url: str, timeout: float = 35.0) -> bool:
    """直连探测公网地址（新域名 DNS 传播需 60–100 秒，失败不代表隧道坏）。

    Args:
        url: 公网地址。
        timeout: 单次请求超时秒数。

    Returns:
        bool: 返回 200 为 True。
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"{url}/api/health", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001 - 探测期任何异常都算未就绪
        return False


def write_shortcut(public_url: str, password: str) -> Path:
    """把带 token 的公网地址写进桌面 .url 快捷方式。

    Args:
        public_url: 公网基址（``https://xxx.trycloudflare.com``）。
        password: 访问口令（为空则不带 token）。

    Returns:
        Path: 快捷方式路径。
    """
    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    target = public_url + ("/auth?token=" + password if password else "/")
    path = desktop / SHORTCUT_NAME
    icon = PROJECT_DIR / "assets" / "icon.ico"
    icon_line = f"IconFile={icon}\r\nIconIndex=0\r\n" if icon.exists() else ""
    path.write_text(
        "[InternetShortcut]\r\n"
        f"URL={target}\r\n"
        f"{icon_line}",
        encoding="utf-8",
    )
    return path


def main() -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="OpenBB 公网入口守护（免账号快速隧道）")
    parser.add_argument("--once", action="store_true", help="起一次、打印地址后不常驻")
    parser.add_argument("--no-desktop", action="store_true", help="不写桌面快捷方式")
    args = parser.parse_args()

    if sys.stdout is None:  # pythonw.exe 无控制台，日志落文件
        redirect_output_to_file()

    if not acquire_single_instance():
        log("✗ 已有守护实例在运行，本次退出（避免多实例互相抢服务与隧道）")
        return 0

    password = (os.environ.get("OPENBB_ACCESS_PASSWORD") or "").strip()
    if len(password) < 4:
        log("✗ 未设置 OPENBB_ACCESS_PASSWORD（公网必须有口令）。先执行：")
        log("    setx OPENBB_ACCESS_PASSWORD 你的口令")
        return 1

    log("=" * 60)
    log("公网入口守护启动")
    log(f"安装目录：{INSTALL_DIR}")
    server = start_server()

    failures = 0
    tunnel: Optional[subprocess.Popen] = None
    while failures < 20:
        tunnel, url = start_tunnel()
        if url:
            break
        failures += 1
        wait = min(5 * failures, 60)
        log(f"第 {failures} 次未拿到地址，{wait} 秒后重试")
        time.sleep(wait)
    if not url:
        log("✗ 20 次重试均未拿到公网地址（检查网络或代理拦截）")
        return 2

    url_path = INSTALL_DIR / "data" / "public_url.txt"
    url_path.parent.mkdir(parents=True, exist_ok=True)
    url_path.write_text(url + "\n", encoding="utf-8")
    log(f"地址已记录：{url_path}")

    if not args.no_desktop:
        shortcut = write_shortcut(url, password)
        log(f"桌面图标已更新：{shortcut}")
    log(f"直达链接：{url}/auth?token={password}")
    log("首次打开需 60–100 秒等 DNS 传播，期间 502/无法访问都属正常")

    if args.once:
        log("--once：打印完成即退出（隧道进程保留在后台）")
        return 0

    # 常驻：隧道退出则重连（地址会变，图标随之更新）
    #
    # 2026-11-09 修复（真实故障）：原循环写作
    #     if server is not None and server.poll() is not None: server = start_server()
    # 而 start_server() 在「6900 已在监听」时返回 None。于是复用端口之后 server 变 None，
    # 上面的条件恒为假 —— 服务从此彻底脱离监督。故障现场：00:38:09 记下「复用现有服务」，
    # 之后服务在 00:57 死掉，循环再没打过任何一行日志，公网地址持续 502 到第二天早上。
    # 现改为「server 为 None 也要定期探活」，并区分「没监听」与「在监听但不健康」。
    unhealthy_streak = 0
    next_probe = 0.0
    while True:
        time.sleep(5)
        if tunnel is not None and tunnel.poll() is not None:
            log("隧道断开，重连中…")
            tunnel, new_url = start_tunnel()
            if new_url:
                url = new_url
                url_path.write_text(url + "\n", encoding="utf-8")
                if not args.no_desktop:
                    write_shortcut(url, password)
                log(f"新地址：{url}/auth?token={password}")

        if time.time() < next_probe:
            continue
        next_probe = time.time() + 10
        if server is not None and server.poll() is not None:
            log("服务进程已退出，尝试重启…")
            server = start_server()
            unhealthy_streak = 0
        elif server is None:
            if not port_open(PORT):
                log("服务未监听，尝试拉起…")
                server = start_server()
                unhealthy_streak = 0
            elif wait_health(timeout=4):
                unhealthy_streak = 0
            else:
                unhealthy_streak += 1
                if unhealthy_streak == 1 or unhealthy_streak % 6 == 0:
                    log(f"⚠ 6900 在监听但健康检查连续失败 {unhealthy_streak} 次（服务可能已卡死）")


if __name__ == "__main__":
    sys.exit(main())
