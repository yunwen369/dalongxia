# -*- coding: utf-8 -*-
"""公网访问支撑：Cloudflare 隧道（cloudflared）的获取、启动与停止。

安全前提：公网暴露必须同时启用访问口令（``OPENBB_ACCESS_PASSWORD``），
本模块只负责通道，鉴权由 ``access_middleware`` 承担，两者不可拆分使用。

形态说明：
- **quick tunnel**（本模块默认）：免账号，Cloudflare 分配
  ``https://<随机串>.trycloudflare.com``，每次启动都会变，适合临时在外查看；
- 地址固定需自备域名并改用命名隧道（``cloudflared tunnel run --token``），
  本模块预留 ``token`` 参数，未提供时一律走 quick tunnel。

二进制获取：优先本机已有（data/bin、PATH、tools/），缺失时按镜像顺序下载，
下载目标为 ``DATA_DIR/bin/cloudflared.exe``，不写系统目录、不需要管理员权限。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from backend.app import config

TUNNEL_URL_PATTERN = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")
QUICK_TIMEOUT = 75.0  # 建链等待上限（秒），首次握手受网络影响
DOWNLOAD_TIMEOUT = 600.0  # 下载上限（秒），约 60 MB

# 下载源顺序：按实测速度排序（gh-proxy.com 约 1.6 MB/s，ghproxy.net 仅约 12 KB/s），
# 最后回退官方地址。实测数据来自 2026-11-08 本机 52.8 MB 完整包。
MIRRORS: List[str] = [
    "https://gh-proxy.com/https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe",
    "https://ghproxy.net/https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe",
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe",
]


@dataclass
class TunnelHandle:
    """运行中的隧道句柄（由 :func:`start` 返回）。"""

    process: subprocess.Popen
    url: str = ""
    error: str = ""


# 进程内隧道状态快照：供 /api/system/access 展示（无需跨模块传句柄）
_STATE: dict = {"url": "", "error": "", "started_at": "", "ready": False}


def set_state(url: str = "", error: str = "", ready: bool = False) -> None:
    """写入隧道状态快照。

    Args:
        url: 公网地址，空串表示未建立。
        error: 失败原因（中文），成功时为空串。
        ready: 公网地址是否已实测可用（DNS 传播 + 回源就绪）。
    """
    from backend.app.datasource import utils  # 延迟导入：core 与 datasource 互相引用时避免环

    _STATE["url"] = url
    _STATE["error"] = error
    _STATE["ready"] = bool(ready)
    _STATE["started_at"] = utils.bj_now_aware().isoformat(timespec="seconds") if url else ""


def state() -> dict:
    """读取隧道状态快照。

    Returns:
        dict: 含 ``url`` / ``error`` / ``started_at`` / ``ready`` 的副本。
    """
    return dict(_STATE)


def binary_path() -> Path:
    """隧道二进制的存放位置（随程序数据目录，不写系统目录）。

    Returns:
        Path: 形如 ``<data>/bin/cloudflared.exe``。
    """
    return config.DATA_DIR / "bin" / ("cloudflared.exe" if sys.platform == "win32" else "cloudflared")


def find_binary() -> Optional[Path]:
    """查找本机可用的 cloudflared。

    查找顺序：数据目录自备副本 → 项目 tools 目录 → 系统 PATH。

    Returns:
        Optional[Path]: 可执行文件路径；未找到返回 None。
    """
    candidates = [
        binary_path(),
        config.PROJECT_DIR / "tools" / ("cloudflared.exe" if sys.platform == "win32" else "cloudflared"),
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.stat().st_size > 1024 * 1024:
            return candidate
    found = shutil.which("cloudflared")
    return Path(found) if found else None


def download_binary(progress: Optional[Callable[[int, int], None]] = None) -> Path:
    """按镜像顺序下载 cloudflared 到数据目录。

    Args:
        progress: 进度回调 ``(已下载字节, 总字节)``；总字节未知时为 0。

    Returns:
        Path: 下载完成的二进制路径。

    Raises:
        RuntimeError: 所有镜像都失败时抛出（消息为中文，可直接展示给用户）。
    """
    target = binary_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".part")
    errors: List[str] = []
    for url in MIRRORS:
        started = time.time()
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "OpenBB-CN/tunnel"})
            with urllib.request.urlopen(request, timeout=60) as response:
                total = int(response.headers.get("Content-Length") or 0)
                received = 0
                with open(temp, "wb") as handle:
                    while True:
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                        received += len(chunk)
                        if progress is not None:
                            progress(received, total)
                        if time.time() - started > DOWNLOAD_TIMEOUT:
                            raise RuntimeError("下载超时")
                        if received > 200 * 1024 * 1024:  # 防御异常源，正常约 53 MB
                            raise RuntimeError("下载体积异常")
            # 慢速镜像会提前断开，必须比对总长度，避免把残缺文件当成功
            if total and received != total:
                raise RuntimeError(f"下载不完整（{received}/{total} 字节）")
            if temp.stat().st_size < 1024 * 1024:
                raise RuntimeError("文件体积过小，下载不完整")
            temp.replace(target)
            if sys.platform != "win32":
                target.chmod(0o755)
            elif not version(target):
                # 文件完整但不可执行（多为安全软件截断）：删除后换源重试
                try:
                    target.unlink()
                except OSError:
                    pass
                raise RuntimeError("下载的组件无法执行（可能被安全软件拦截）")
            return target
        except Exception as exc:  # noqa: BLE001 - 逐个镜像回退，错误汇总后统一报
            errors.append(f"{url.split('/')[2]}：{type(exc).__name__}")
            if temp.exists():
                try:
                    temp.unlink()
                except OSError:
                    pass
    raise RuntimeError("隧道组件下载失败（已尝试 3 个下载源）：" + "；".join(errors))


def ensure_binary(progress: Optional[Callable[[int, int], None]] = None) -> Path:
    """确保 cloudflared 可用，缺失时自动下载。

    Args:
        progress: 下载进度回调，见 :func:`download_binary`。

    Returns:
        Path: 可执行文件路径。

    Raises:
        RuntimeError: 需要下载但下载失败时抛出。
    """
    found = find_binary()
    if found is not None:
        return found
    return download_binary(progress)


def parse_url(line: str) -> Optional[str]:
    """从 cloudflared 输出中提取 quick tunnel 公网地址。

    Args:
        line: 单行日志文本。

    Returns:
        Optional[str]: 命中的 https 地址；未命中返回 None。
    """
    match = TUNNEL_URL_PATTERN.search(line or "")
    return match.group(0) if match else None


def _probe_public(url: str, timeout: float = 35.0) -> bool:
    """轮询公网地址，确认隧道回源已生效。

    快速隧道（quick tunnel）分配域名后，Cloudflare 边缘需要数秒下发路由；
    期间访问会得到 502。先探测再交付地址，避免用户点开就看到错误页。

    Args:
        url: 公网地址。
        timeout: 轮询上限秒数。

    Returns:
        bool: 能返回 200 即为 True。
    """
    # 探测必须直连，不能走本机 HTTP 代理（代理会改变出口，导致误判）
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    target = url.rstrip("/") + "/api/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            request = urllib.request.Request(target, headers={"User-Agent": "OpenBB-CN/probe"})
            with opener.open(request, timeout=10) as response:
                if response.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - 边缘未就绪时会 502/连接失败，继续轮询
            pass
        time.sleep(2.0)
    return False


def _watch_ready(handle: TunnelHandle, timeout: float = 300.0) -> None:
    """后台轮询公网地址，就绪后写入状态快照。

    快速隧道的域名要等 Cloudflare 边缘下发记录并传播到本地 DNS 才可解析，
    实测首次可用约 90–100 秒，因此这里异步等待，不阻塞程序启动。

    Args:
        handle: 隧道句柄（需已拿到公网地址）。
        timeout: 最长等待秒数。
    """
    if not handle.url:
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        if handle.process.poll() is not None:
            set_state(url=handle.url, error="隧道进程已退出")
            return
        if _probe_public(handle.url, timeout=12):
            set_state(url=handle.url, error="", ready=True)
            return
        time.sleep(4)
    set_state(url=handle.url, error="公网地址长时间未生效，请检查网络后重启")


def start(port: int, token: str = "", timeout: float = QUICK_TIMEOUT) -> TunnelHandle:
    """启动隧道并等待公网地址就绪。

    Args:
        port: 本地服务端口（隧道回源目标，固定走回环）。
        token: 命名隧道 token；为空则走免账号的 quick tunnel。
        timeout: 等待公网地址的上限秒数。

    Returns:
        TunnelHandle: 进程句柄与公网地址；失败时 ``url`` 为空、``error`` 有中文原因。

    Raises:
        RuntimeError: 二进制不可用时抛出。
    """
    exe = ensure_binary()
    if token:
        command = [str(exe), "tunnel", "--no-autoupdate", "run", "--token", token]
    else:
        command = [str(exe), "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"]
    creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creation,
    )
    handle = TunnelHandle(process=process)
    ready = threading.Event()

    def _pump() -> None:
        """逐行读取隧道输出，抓到公网地址即置位就绪事件。"""
        stream = process.stdout
        if stream is None:
            return
        for line in stream:
            if not handle.url:
                found = parse_url(line)
                if found:
                    handle.url = found
                    set_state(url=found)
                    ready.set()
        ready.set()

    threading.Thread(target=_pump, name="cloudflared-log", daemon=True).start()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if handle.url:
            set_state(url=handle.url)
            threading.Thread(target=_watch_ready, args=(handle,), daemon=True,
                             name="tunnel-ready").start()
            return handle
        if process.poll() is not None:
            handle.error = "隧道进程已退出（可能是网络不通或被安全软件拦截）"
            set_state(error=handle.error)
            return handle
        time.sleep(0.4)
    handle.error = f"等待公网地址超时（{int(timeout)} 秒），请检查网络后重试"
    set_state(error=handle.error)
    return handle


def stop(handle: Optional[TunnelHandle]) -> None:
    """停止隧道进程（幂等，不抛异常）。

    Args:
        handle: :func:`start` 返回的句柄，可为 None。
    """
    if handle is None:
        return
    set_state()  # 隧道已停，清空公网地址
    process = handle.process
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=6)
    except Exception:  # noqa: BLE001 - 停不掉时强制结束，仍不抛给调用方
        try:
            process.kill()
        except Exception:  # noqa: BLE001 - 已退出
            pass


def version(exe: Optional[Path] = None) -> str:
    """读取 cloudflared 版本号（用于交付自检）。

    Args:
        exe: 可执行文件路径，缺省用 :func:`find_binary` 的结果。

    Returns:
        str: 形如 ``cloudflared version 2025.x.y``；不可用时返回空串。
    """
    target = exe or find_binary()
    if target is None:
        return ""
    try:
        result = subprocess.run(
            [str(target), "--version"],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return (result.stdout or result.stderr or "").strip().splitlines()[0]
    except Exception:  # noqa: BLE001 - 自检不阻断主流程
        return ""
