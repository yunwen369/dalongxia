# -*- coding: utf-8 -*-
"""
gen_start_guide.py — 生成根级《开始使用.txt》（build-time artifact，v7.7 P0-4 根因修复）
内容版本一律取 backend/app/config.py 的 BUILD_TAG / APP_VERSION / PORT，禁止手工维护。
用法: python tools/gen_start_guide.py   （在项目根 openbb-cn 或任意位置执行均可）
PEP8；仅 stdlib。
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

# openbb-cn 根 = 本脚本所在 tools/ 的上级；项目根 = 再上一级的上一级(A_运行资产 之上)
TOOLS = Path(__file__).resolve().parent
PROJ = TOOLS.parent                      # A_运行资产\openbb-cn
ROOT = PROJ.parent.parent                # F:\workbuddy记录\OpenBB Terminal Pro
CFG = PROJ / "backend" / "app" / "config.py"
OUT = ROOT / "开始使用.txt"


def read_config_values() -> dict:
    """从 config.py 静态提取 APP_NAME/APP_VERSION/BUILD_TAG/PORT 默认值。"""
    src = CFG.read_text(encoding="utf-8")
    def grab(rx: str, default: str = "") -> str:
        m = re.search(rx, src)
        return m.group(1) if m else default
    return {
        "APP_NAME": grab(r'APP_NAME\s*=\s*"([^"]+)"', "OpenBB 中文投研工作台"),
        "APP_VERSION": grab(r'APP_VERSION\s*=\s*"([^"]+)"', "?"),
        "BUILD_TAG": grab(r'BUILD_TAG\s*=\s*"([^"]+)"', "?"),
        "PORT": grab(r'PORT\s*=\s*int\(os\.environ\.get\("OPENBB_PORT"\)\s*or\s*(\d+)\)', "6900"),
    }


TEMPLATE = """{APP_NAME} · 开始使用
================================================
版本：v{APP_VERSION}（BUILD_TAG：{BUILD_TAG}）
本文件由 tools/gen_start_guide.py 在打包/发版时自动生成，禁止手工编辑（2.8 步骤 4）。

【启动】
  方式一（推荐）：双击桌面快捷方式「OpenBB 中文投研工作台」
  方式二：进入 正式环境 目录运行 OpenBB-CN.exe
  方式三（开发态）：A_运行资产\\openbb-cn\\start.bat

【访问地址】
  本机服务：http://127.0.0.1:{PORT}（仅本机回环，不对外网暴露）
  健康检查：http://127.0.0.1:{PORT}/api/health（build 字段应等于上方 BUILD_TAG）

【常见问题】
  1. 端口 {PORT} 被占用 → 启动弹窗提示并落日志；请勿静默改端口（规范 5.4）。
  2. 界面空白/数据不加载 → 先开 /api/health 确认服务在跑；再看数据源徽章是否已降级。
  3. 数据缺失显示"—" → 多为源站限频或休市，口径徽章有标注；盘中估值见合规说明。
  4. 深色/浅色切换：Ctrl+M；帮助：主菜单条。

【数据与合规】
  数据源：akshare 直连（东财/新浪/腾讯等，无 SLA）+ 养基宝非官方通道（可一键关闭）。
  本软件仅本机个人研究使用，只看数据、不交易、不导流、不分发。
"""


def main() -> int:
    if not CFG.is_file():
        print(f"[错误] 未找到 config.py: {CFG}", file=sys.stderr)
        return 2
    vals = read_config_values()
    text = TEMPLATE.format(**vals)
    OUT.write_text(text, encoding="utf-8")
    print(f"[完成] 已生成 {OUT}")
    print(f"       版本 v{vals['APP_VERSION']} / BUILD_TAG {vals['BUILD_TAG']} / 端口 {vals['PORT']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
