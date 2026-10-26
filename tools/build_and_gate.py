# -*- coding: utf-8 -*-
"""一键「打包 → 部署 → 发版门禁」：把"交付旧包/凭据没落位"彻底堵死在流程里。

门禁强制项（写死在脚本里，不可跳过）：
    打包产物上 ``GET /api/fund/yjb/collect`` 与 ``/holdings?account_id=all``
    必须 HTTP 200 且 total_assets>0 / total>0；``/status`` 三项必须为 true。
    任一不满足 → 进程以非 0 退出，**不得发版**。

步骤：
1. 备份：``dist/OpenBB-CN`` → ``dist/OpenBB-CN_prev_<时间戳>``（保留上一版便于回滚）；
2. 打包：用构建虚拟环境跑 PyInstaller（默认 ``../.venv_build_20261021``）；
3. 部署：把 ``dist/OpenBB-CN/*`` 同步到目标目录（默认 ``../正式环境``），
   目标已存在同版本产物时先整体备份为 ``<目标>_v<旧版本>_<时间戳>``；
4. 凭据落位：目标 ``data/`` 缺 ``yjb_token.json``/``yjb_secret.json`` 时，
   从源码树 ``data/`` 复制（同 Windows 用户 DPAPI 可解）；两者都没有则明确告警，
   由首启向导兜底（门禁会失败，不会静默放行）；
5. 门禁：调用 ``tools/release_gate.py`` 实打实起打包产物实测三项接口；
6. 汇总：把结论写入 ``<目标>/release_gate.json`` 并打印中文清单。

用法：
    .venv\\Scripts\\python.exe tools\\build_and_gate.py                # 全流程
    .venv\\Scripts\\python.exe tools\\build_and_gate.py --deploy-only   # 只部署+门禁（跳过打包）
    .venv\\Scripts\\python.exe tools\\build_and_gate.py --gate-only     # 只跑门禁
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]  # openbb-cn
DIST_DIR = PROJECT_DIR / "dist" / "OpenBB-CN"
BUILD_PY = PROJECT_DIR.parent / ".venv_build_20261021" / "Scripts" / "python.exe"
TARGET_DIR = PROJECT_DIR.parent / "正式环境"
SPEC = PROJECT_DIR / "OpenBB-CN.spec"
BJ = timezone(timedelta(hours=8))


def _stamp() -> str:
    """当前北京时间戳（用于备份目录命名）。"""
    return datetime.now(BJ).strftime("%Y%m%d_%H%M%S")


def _read_version() -> str:
    """读取源码树版本号（避免与产物不一致）。"""
    sys.path.insert(0, str(PROJECT_DIR))
    from backend.app import config  # noqa: PLC0415 - 延迟导入，避免脚本级副作用

    return f"{config.APP_VERSION}（{config.BUILD_TAG}）"


def _run(command: list[str], cwd: Path, title: str) -> int:
    """执行子进程并把输出落日志（失败信息完整可见）。"""
    print(f"\n== {title} ==")
    print("  $", " ".join(str(part) for part in command))
    proc = subprocess.run(command, cwd=str(cwd))
    return proc.returncode


def build() -> bool:
    """用构建虚拟环境打包（onedir）。"""
    if not BUILD_PY.exists():
        print(f"[失败] 构建虚拟环境不存在：{BUILD_PY}")
        return False
    if DIST_DIR.exists():
        backup = DIST_DIR.with_name(f"OpenBB-CN_prev_{_stamp()}")
        print(f"[备份] {DIST_DIR.name} → {backup.name}")
        shutil.copytree(DIST_DIR, backup)
    code = _run([str(BUILD_PY), "-m", "PyInstaller", str(SPEC), "--noconfirm"],
                PROJECT_DIR, "PyInstaller 打包（onedir）")
    if code != 0:
        print(f"[失败] PyInstaller 退出码 {code}")
        return False
    exe = DIST_DIR / "OpenBB-CN.exe"
    if not exe.exists():
        print("[失败] 打包完成但未找到 OpenBB-CN.exe")
        return False
    print(f"[完成] 产物：{exe}（{exe.stat().st_size} 字节）")
    return True


def _old_version(target: Path) -> str:
    """从目标目录的 health 版本无法离线读取，退化为读 _internal 里的构建标识文件名。"""
    marker = target / "VERSION.txt"
    if marker.exists():
        return marker.read_text(encoding="utf-8").splitlines()[0].strip() or "unknown"
    return "unknown"


def deploy(target: Path) -> bool:
    """把 dist 产物同步到目标目录，并做凭据落位。

    Args:
        target: 目标目录（EXE 同级）。

    Returns:
        bool: 是否成功。
    """
    if not DIST_DIR.exists():
        print(f"[失败] 未找到打包产物目录：{DIST_DIR}")
        return False
    if target.exists():
        old = _old_version(target)
        backup = target.with_name(f"{target.name}_v{old}_{_stamp()}")
        print(f"[备份] 目标目录整体备份 → {backup.name}")
        shutil.copytree(target, backup)
    target.mkdir(parents=True, exist_ok=True)
    # 只覆盖程序本体与 _internal，保留目标 data/ 与 logs/（凭据、缓存、日志不能清）
    for name in ("OpenBB-CN.exe", "_internal"):
        src = DIST_DIR / name
        dst = target / name
        if not src.exists():
            print(f"[失败] 产物缺少 {name}")
            return False
        if dst.is_dir():
            shutil.rmtree(dst)
        elif dst.exists():
            dst.unlink()
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        print(f"  覆盖 {name}")
    # 凭据落位（任务 2a）：同 Windows 用户 DPAPI 可解，直接复制；缺则告警
    src_data = PROJECT_DIR / "data"
    tgt_data = target / "data"
    tgt_data.mkdir(parents=True, exist_ok=True)
    for name in ("yjb_token.json", "yjb_secret.json", "yjb_state.json"):
        src = src_data / name
        dst = tgt_data / name
        if not src.exists():
            continue
        if dst.exists() and dst.stat().st_size == src.stat().st_size:
            print(f"  凭据已就位，跳过 {name}")
            continue
        shutil.copy2(src, dst)
        print(f"  复制凭据 {name}（{dst.stat().st_size} 字节）")
    missing = [n for n in ("yjb_token.json", "yjb_secret.json") if not (tgt_data / n).exists()]
    if missing:
        print("[告警] 目标 data 缺凭据：" + "、".join(missing))
        print("       处置：启动新包 → 设置页「养基宝连接」注入签名盐 → 养基宝页扫码授权；")
        print("       门禁会把「未绑定」判为不通过，不会静默放行。")
    (target / "VERSION.txt").write_text(
        f"{_read_version()}\n打包时间：{_stamp()}\n", encoding="utf-8")
    print(f"[完成] 部署到 {target}")
    return True


def gate(target: Path, port: int) -> bool:
    """调用发版门禁脚本（打包产物实测三项接口）。"""
    script = PROJECT_DIR / "tools" / "release_gate.py"
    code = _run([sys.executable, str(script), "--artifact", str(target), "--port", str(port)],
                PROJECT_DIR, "发版门禁（打包产物实测）")
    return code == 0


def main() -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="OpenBB 打包+部署+发版门禁")
    parser.add_argument("--deploy-only", action="store_true", help="跳过打包，直接用现有 dist 部署")
    parser.add_argument("--gate-only", action="store_true", help="只跑门禁（不打包不部署）")
    parser.add_argument("--target", default=str(TARGET_DIR), help="部署目标目录")
    parser.add_argument("--port", type=int, default=6916, help="门禁端口（避开 6900）")
    args = parser.parse_args()

    target = Path(args.target).resolve()
    print("=" * 66)
    print(f"OpenBB 发版流水线　源码版本 {_read_version()}")
    print(f"产物：{DIST_DIR}\n目标：{target}\n门禁端口：{args.port}")
    print("=" * 66)

    if not args.gate_only:
        if not args.deploy_only and not build():
            return 1
        if not deploy(target):
            return 1
    ok = gate(target, args.port)
    report = target / "release_gate.json"
    if report.exists():
        data = json.loads(report.read_text(encoding="utf-8"))
        print(f"\n门禁结论：{'通过' if data.get('passed') else '不通过'}"
              f"（collect.total_assets={((data.get('checks') or {}).get('collect') or {}).get('total_assets')}，"
              f"holdings.total={((data.get('checks') or {}).get('holdings') or {}).get('total')}）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
