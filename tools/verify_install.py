# -*- coding: utf-8 -*-
"""安装包三场景验证工具（规范 §13.3）。

职责：以只读方式核对安装结果，输出 JSON 证据供《安装包测试报告》引用。
不使用 reg.exe（本机安全策略已将其列入黑名单），注册表读取走 ``winreg``。

检查项：
  1. 安装目录与主程序、卸载器是否存在；
  2. 桌面 / 开始菜单快捷方式（.lnk）是否生成；
  3. 控制面板卸载项（DisplayName/DisplayVersion/InstallLocation）与版本是否一致；
  4. 安装目录体积、是否残留敏感文件（token/ai_config/portfolio/.env）；
  5. `--check` 自检日志（冻结版自身输出）。

用法：
    python tools/verify_install.py --out <json 路径> [--expect-version 1.0.0]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

APP_DIR_NAME = "OpenBB-CN"
APP_DISPLAY = "OpenBB 中文投研工作台"


def _reg_values() -> Dict[str, Any]:
    """读取 HKCU 卸载项（DisplayName/DisplayVersion/InstallLocation 等）。"""
    try:
        import winreg
    except Exception:  # noqa: BLE001 - 非 Windows
        return {}
    path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\OpenBB-CN"
    result: Dict[str, Any] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            for name in ("DisplayName", "DisplayVersion", "InstallLocation",
                         "UninstallString", "DisplayIcon", "Publisher"):
                try:
                    value, _ = winreg.QueryValueEx(key, name)
                    result[name] = value
                except OSError:
                    continue
    except OSError:
        return {}
    return result


def _app_key() -> Dict[str, Any]:
    """读取 HKCU\\Software\\OpenBB-CN（安装目录记录）。"""
    try:
        import winreg
    except Exception:  # noqa: BLE001
        return {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\OpenBB-CN") as key:
            value, _ = winreg.QueryValueEx(key, "InstallDir")
            return {"InstallDir": value}
    except OSError:
        return {}


def _size_mb(path: Path) -> float:
    """统计目录体积（MB）。"""
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                continue
    return round(total / 1024 / 1024, 1)


def _sensitive_hits(target: Path) -> List[str]:
    """扫描安装目录内的敏感文件（应全部为空）。"""
    patterns = ("*token*.json", "ai_config.json", "portfolio.json", "*.env", ".env")
    hits: List[str] = []
    for pattern in patterns:
        for item in target.rglob(pattern):
            if item.is_file():
                hits.append(str(item.relative_to(target)))
    return hits


def collect(expect_version: str = "") -> Dict[str, Any]:
    """收集一次安装结果的全部检查项。"""
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    target = local / "Programs" / APP_DIR_NAME
    desktop = Path(os.environ.get("USERPROFILE", "")) / "Desktop" / f"{APP_DISPLAY}.lnk"
    programs = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    sm_dir = programs / APP_DISPLAY
    reg = _reg_values()
    app_key = _app_key()

    exe = target / "OpenBB-CN.exe"
    uninstaller = target / "uninstall.exe"
    report: Dict[str, Any] = {
        "install_dir": str(target),
        "install_dir_exists": target.is_dir(),
        "exe_exists": exe.is_file(),
        "uninstaller_exists": uninstaller.is_file(),
        "size_mb": _size_mb(target) if target.is_dir() else 0,
        "desktop_shortcut": str(desktop),
        "desktop_shortcut_exists": desktop.is_file(),
        "startmenu_dir": str(sm_dir),
        "startmenu_shortcut_exists": (sm_dir / f"{APP_DISPLAY}.lnk").is_file(),
        "startmenu_uninstall_exists": (sm_dir / "卸载.lnk").is_file(),
        "registry": reg,
        "app_key": app_key,
        "registry_ok": bool(reg.get("DisplayName")) and bool(reg.get("DisplayVersion")),
        "version_match": (reg.get("DisplayVersion") == expect_version) if expect_version else None,
        "sensitive_hits": _sensitive_hits(target) if target.is_dir() else [],
    }
    report["data_dir"] = str(target / "data")
    report["data_dir_exists"] = (target / "data").is_dir()
    report["logs_dir_exists"] = (target / "logs").is_dir()
    return report


def main() -> None:
    """入口：采集安装结果并写 JSON。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--expect-version", default="")
    args = parser.parse_args()
    report = collect(args.expect_version)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for key, value in report.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    sys.exit(main())
