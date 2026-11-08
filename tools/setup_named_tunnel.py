# -*- coding: utf-8 -*-
"""Cloudflare 命名隧道一键配置：把工作台固定映射到自有域名。

背景（v2.1.0 网页化）：
    quick tunnel（``--public``）给出的是 ``*.trycloudflare.com`` 临时地址，重启即变。
    要「一个网址永远能进」，需要用**命名隧道** + 已托管在 Cloudflare 的域名。

前置条件（人工完成，脚本会自动检查并提示）：
    1. 域名已在 Cloudflare 添加站点，且状态为 Active（NS 已生效）；
    2. ``cloudflared`` 已就位（默认 ``<安装目录>/data/bin/cloudflared.exe``）。

分步执行（每步幂等，可重复跑）：
    check      体检：cloudflared / cert.pem / 域名解析
    login      浏览器授权（会弹出 Cloudflare 授权页，点 Allow 即可）
    create     创建隧道 ``openbb``（已存在则复用）
    dns        给域名加 CNAME 指向隧道
    config     写 config.yml（ingress 指向 http://127.0.0.1:6900）
    autostart  写开机自启（启动文件夹 VBS，隐藏窗口）
    verify     实测 https://<域名>/api/health
    all        依次跑 create → dns → config → autostart → verify

用法：
    python tools/setup_named_tunnel.py --domain openbb.abc.dpdns.org --step all

退出码：0=成功；1=失败（打印具体原因）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
FROZEN_DIR = PROJECT_DIR.parent / "正式环境"
TUNNEL_NAME = "openbb"
PORT = 6900

# 安装目录：优先正式环境（冻结版），其次源码树
INSTALL_DIR = FROZEN_DIR if (FROZEN_DIR / "OpenBB-CN.exe").exists() else PROJECT_DIR


def cloudflared_path() -> Path:
    """定位 cloudflared 可执行文件（安装目录 data/bin 优先）。"""
    for candidate in (
        INSTALL_DIR / "data" / "bin" / "cloudflared.exe",
        PROJECT_DIR / "data" / "bin" / "cloudflared.exe",
        PROJECT_DIR / "tools" / "cloudflared.exe",
    ):
        if candidate.exists():
            return candidate
    return INSTALL_DIR / "data" / "bin" / "cloudflared.exe"


def cloudflared_home() -> Path:
    """cloudflared 凭据目录（cert.pem / <uuid>.json 都在这里）。"""
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".cloudflared"


def run(args: list[str], timeout: float = 120.0, inherit: bool = False) -> tuple[int, str]:
    """执行 cloudflared 子命令。

    Args:
        args: 命令参数（不含可执行文件本身）。
        timeout: 超时秒数。
        inherit: True 时把子进程输出直接打到当前终端（登录流程需要用户看 URL）。

    Returns:
        tuple[int, str]: ``(退出码, 输出文本)``。
    """
    exe = str(cloudflared_path())
    print(f"  $ cloudflared {' '.join(args)}")
    if inherit:
        code = subprocess.call([exe] + args, timeout=timeout)
        return code, ""
    proc = subprocess.run(
        [exe] + args, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


# =============================================================
# 各步骤
# =============================================================
def step_check(domain: str) -> bool:
    """体检：cloudflared 可用、授权状态、域名解析。"""
    exe = cloudflared_path()
    print(f"[1/3] cloudflared：{exe}")
    if not exe.exists():
        print("      ✗ 不存在。把 cloudflared.exe 放到该路径，或先在设置页启动一次公网隧道自动下载。")
        return False
    code, out = run(["--version"], timeout=30)
    print(f"      {'✓' if code == 0 else '✗'} {out.strip().splitlines()[0] if out.strip() else '版本取不到'}")

    cert = cloudflared_home() / "cert.pem"
    print(f"[2/3] 授权文件：{cert}")
    print(f"      {'✓ 已授权' if cert.exists() else '✗ 未授权（需要跑 --step login）'}")

    print(f"[3/3] 域名：{domain}")
    import socket

    try:
        infos = socket.getaddrinfo(domain, None)
        addresses = sorted({item[4][0] for item in infos})
        print(f"      ✓ 解析到 {', '.join(addresses[:3])}")
    except OSError as exc:
        print(f"      ✗ 解析失败（{exc}）——NS 可能还没生效，或域名尚未在 Cloudflare 添加站点")
    return True


def step_login(domain: str) -> bool:
    """浏览器授权：cloudflared tunnel login。"""
    cert = cloudflared_home() / "cert.pem"
    if cert.exists():
        print(f"[login] 已存在授权文件，跳过：{cert}")
        return True
    print("[login] 即将打开浏览器，请登录 Cloudflare 并点「Allow」授权本机……")
    try:
        code, _ = run(["tunnel", "login"], timeout=600, inherit=True)
    except subprocess.TimeoutExpired:
        print("[login] ✗ 超时（10 分钟未完成授权）")
        return False
    if not cert.exists():
        print(f"[login] ✗ 未生成 {cert}（退出码 {code}）")
        return False
    print(f"[login] ✓ 授权完成：{cert}")
    return True


def step_create(domain: str) -> bool:
    """创建（或复用）隧道 openbb，返回凭据文件路径。"""
    code, out = run(["tunnel", "list", "--output", "json"], timeout=60)
    tunnel_id = ""
    if code == 0 and out.strip():
        try:
            tunnels = json.loads(out[out.index("["):])
        except (ValueError, json.JSONDecodeError):
            tunnels = []
        for item in tunnels:
            if item.get("name") == TUNNEL_NAME:
                tunnel_id = item.get("id", "")
                break
    if tunnel_id:
        print(f"[create] 隧道已存在，复用：{TUNNEL_NAME} / {tunnel_id}")
    else:
        code, out = run(["tunnel", "create", TUNNEL_NAME], timeout=120)
        if code != 0:
            print(f"[create] ✗ 创建失败：{out.strip()[-400:]}")
            return False
        match = re.search(r"with id ([0-9a-f-]{36})", out) or re.search(r"([0-9a-f-]{36})", out)
        if not match:
            print(f"[create] ✗ 拿不到 tunnel id：{out.strip()[-400:]}")
            return False
        tunnel_id = match.group(1)
        print(f"[create] ✓ 已创建：{TUNNEL_NAME} / {tunnel_id}")

    cred = cloudflared_home() / f"{tunnel_id}.json"
    if not cred.exists():
        print(f"[create] ✗ 凭据文件缺失：{cred}")
        return False
    (INSTALL_DIR / "data" / "bin" / "tunnel_id.txt").write_text(tunnel_id + "\n", encoding="utf-8")
    print(f"[create] ✓ 凭据：{cred}")
    return True


def read_tunnel_id() -> str:
    """从 data/bin/tunnel_id.txt 读回隧道 id。"""
    path = INSTALL_DIR / "data" / "bin" / "tunnel_id.txt"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def step_dns(domain: str) -> bool:
    """给域名加 CNAME 指向隧道。"""
    if not read_tunnel_id():
        print("[dns] ✗ 先跑 --step create")
        return False
    code, out = run(["tunnel", "route", "dns", TUNNEL_NAME, domain], timeout=120)
    text = out.strip()
    if code != 0:
        if "already exists" in text or "already configured" in text:
            print(f"[dns] ✓ 记录已存在：{domain}")
            return True
        print(f"[dns] ✗ 失败：{text[-400:]}")
        return False
    print(f"[dns] ✓ 已加 CNAME：{domain} → {read_tunnel_id()}.cfargotunnel.com")
    return True


def step_config(domain: str) -> bool:
    """写 config.yml（ingress 指向本机 6900）。"""
    tunnel_id = read_tunnel_id()
    if not tunnel_id:
        print("[config] ✗ 先跑 --step create")
        return False
    cred = cloudflared_home() / f"{tunnel_id}.json"
    config = INSTALL_DIR / "data" / "bin" / "config.yml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        "# OpenBB 中文投研工作台 · Cloudflare 命名隧道配置（由 tools/setup_named_tunnel.py 生成）\n"
        f"tunnel: {tunnel_id}\n"
        f"credentials-file: {cred}\n"
        "no-autoupdate: true\n"
        "ingress:\n"
        f"  - hostname: {domain}\n"
        f"    service: http://127.0.0.1:{PORT}\n"
        "  - service: http_status:404\n",
        encoding="utf-8",
    )
    print(f"[config] ✓ 已写入：{config}")
    return True


def step_autostart(domain: str) -> bool:
    """写开机自启：启动文件夹放 VBS（隐藏窗口），先起服务再起隧道。"""
    startup = (
        Path(os.environ.get("APPDATA", ""))
        / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    )
    if not startup.exists():
        print(f"[autostart] ✗ 启动文件夹不存在：{startup}")
        return False
    exe = INSTALL_DIR / "OpenBB-CN.exe"
    if not exe.exists():
        print(f"[autostart] ✗ 未找到 {exe}")
        return False
    bat = INSTALL_DIR / "data" / "bin" / "start_tunnel.bat"
    bat.write_text(
        "@echo off\r\n"
        "rem OpenBB 工作台开机自启：先起本机服务，再起 Cloudflare 命名隧道\r\n"
        f'start "" /min "{exe}" --serve-only\r\n'
        "timeout /t 12 /nobreak >nul\r\n"
        f'"{INSTALL_DIR / "data" / "bin" / "cloudflared.exe"}" tunnel --config '
        f'"{INSTALL_DIR / "data" / "bin" / "config.yml"}" --no-autoupdate run\r\n',
        encoding="gbk",
    )
    vbs = startup / "OpenBB工作台自启.vbs"
    vbs.write_text(
        'Set ws = CreateObject("WScript.Shell")\r\n'
        f'ws.Run """{bat}""", 0, False\r\n',
        encoding="gbk",
    )
    print(f"[autostart] ✓ 自启脚本：{vbs}")
    print(f"            → 隐藏调用：{bat}")
    print(f"            → 访问地址：https://{domain}")
    return True


def step_verify(domain: str) -> bool:
    """实测 https://<域名>/api/health（域名 DNS 生效后才有意义）。"""
    url = f"https://{domain}/api/health"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt in range(1, 13):
        try:
            with opener.open(url, timeout=12) as resp:
                body = resp.read(400).decode("utf-8", "ignore")
                print(f"[verify] ✓ {url} → HTTP {resp.status}")
                print(f"          {body}")
                return True
        except urllib.error.HTTPError as exc:
            print(f"[verify] HTTP {exc.code}（第 {attempt}/12 次）")
        except Exception as exc:  # noqa: BLE001 - 探测期任何异常都视为未就绪
            print(f"[verify] 第 {attempt}/12 次未通：{type(exc).__name__}")
        time.sleep(10)
    print("[verify] ✗ 12 次探测均未通过。检查：隧道是否在跑（data/bin/start_tunnel.bat）、"
          "域名 NS 是否生效、服务是否已启动。")
    return False


STEPS = {
    "check": step_check,
    "login": step_login,
    "create": step_create,
    "dns": step_dns,
    "config": step_config,
    "autostart": step_autostart,
    "verify": step_verify,
}
ORDER = ["check", "create", "dns", "config", "autostart", "verify"]


def main() -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="Cloudflare 命名隧道配置（OpenBB 工作台）")
    parser.add_argument("--domain", required=True, help="要绑定的完整域名，如 openbb.abc.dpdns.org")
    parser.add_argument("--step", default="all", choices=ORDER + ["login", "all"])
    args = parser.parse_args()

    print("=" * 68)
    print(f"OpenBB 命名隧道配置　域名 {args.domain}　安装目录 {INSTALL_DIR}")
    print("=" * 68)

    if args.step == "all":
        if not step_check(args.domain):
            return 1
        for name in ("create", "dns", "config", "autostart", "verify"):
            if not STEPS[name](args.domain):
                print(f"\n[中断] 步骤 {name} 失败")
                return 1
        print(f"\n完成：https://{args.domain}")
        return 0

    ok = STEPS[args.step](args.domain)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
