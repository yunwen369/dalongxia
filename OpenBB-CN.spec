# -*- mode: python ; coding: utf-8 -*-
"""OpenBB 中文投研工作台 · PyInstaller 打包配置（onedir，规范 §13.1 / §13.6）。

用法（在干净构建环境执行，避免把开发期依赖打进包）：
    ..\\.venv_build_20261021\\Scripts\\python.exe -m PyInstaller OpenBB-CN.spec --noconfirm

产物：
    dist/OpenBB-CN/OpenBB-CN.exe        主程序（窗口化，无控制台）
    dist/OpenBB-CN/_internal/…          运行库与只读资源（frontend/assets/…）
    dist/OpenBB-CN/data、logs/          首次启动时创建（EXE 同级可写）

要点（对应规范条款）：
  - onedir 而非 onefile（§13.6-1）；入口为 backend/app/main.py 的冻结形态；
  - hiddenimports 显式收录 akshare 依赖树、uvicorn 动态子模块、pywebview 的
    edgechromium 后端、pythonnet/clr_loader（§13.6-2 / §13.6-5）；
  - 随包资源：frontend（含 vendor/echarts）、assets（图标）、sdk（§13.6-3）；
  - **不随包**：data/（持仓、token、AI key）、logs/、tests/、tools/（§13.6-4 / 安全门禁）；
  - UPX 关闭：压缩会显著提高杀软启发式误报率（§15.2 误报预案），体积代价可接受。
"""

import os

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

# ---------------- 只读资源 ----------------
datas = [
    ('frontend', 'frontend'),  # 静态前端（含 vendor/echarts 本地库）
    ('assets', 'assets'),      # 图标等品牌资源
]
# Python SDK：规范 §13.6-3 要求随包分发（当前为占位结构，见安装测试报告「已知限制」）
if os.path.isdir('sdk'):
    datas += [('sdk', 'sdk')]
datas += collect_data_files('akshare')   # akshare 内置数据文件（板块树等）
datas += collect_data_files('certifi')   # CA 证书包（requests/curl_cffi 依赖）
datas += collect_data_files('segno')     # 二维码库（纯 Python，保持依赖完整）

# ---------------- 隐式依赖 ----------------
binaries = []
hiddenimports = []
hiddenimports += collect_submodules('akshare')   # akshare 按需导入，PyInstaller 无法静态追踪
hiddenimports += collect_submodules('segno')
hiddenimports += collect_submodules('uvicorn')   # uvicorn 的 loop/http 协议实现是动态导入
hiddenimports += [
    'webview.platforms.edgechromium',  # Windows 主后端（WebView2）
    'webview.platforms.winforms',
    'pythonnet',
    'clr_loader',
]
for package in ('webview', 'pythonnet', 'clr_loader', 'bottle', 'proxy_tools'):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    ['backend/app/main.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 明确排除开发期与测试期内容，确保安装包不含源码备份/测试夹具
        'pytest',
        'tests',
        'tools',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='OpenBB-CN',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # 关闭 UPX：降低杀软启发式误报（§15.2）
    console=False,      # 窗口化：无控制台，stdio 由 desktop_shell.redirect_stdio 重定向
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='OpenBB-CN',
)
