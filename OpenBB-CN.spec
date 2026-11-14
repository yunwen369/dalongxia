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
  - 随包资源：frontend（含 vendor/echarts）、assets（图标）、docs（部署文档，v3.6.0）、sdk（§13.6-3）；
  - **不随包**：data/（持仓、token、AI key）、logs/、tests/、tools/（§13.6-4 / 安全门禁）；
  - UPX 关闭：压缩会显著提高杀软启发式误报率（§15.2 误报预案），体积代价可接受。
  - **V8 运行库（py_mini_racer）必随包**：akshare 的新浪通道（`stock_zh_index_daily` /
    `stock_zh_a_daily` 等）用 `py_mini_racer.MiniRacer()` 执行新浪返回的 JS。该库按
    ``sys._MEIPASS/py_mini_racer/{mini_racer.dll,icudtl.dat}`` 定位（见 py_mini_racer/_dll.py
    `_open_dll` 的 PyInstaller 分支），缺失即抛 `LibNotFoundError`；PyInstaller 不会自动收集其
    包内 DLL 与 ICU 数据（ctypes 动态加载 + 非 import 依赖），必须显式 collect_all。
    v3.6.1 修复：此前未收集 → 冻结形态下指数K线（新浪）恒失败（D-1）。
"""

import os

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

# ---------------- 只读资源 ----------------
datas = [
    ('frontend', 'frontend'),  # 静态前端（含 vendor/echarts 本地库）
    ('assets', 'assets'),      # 图标等品牌资源
    ('docs', 'docs'),          # P5（v3.6.0）：部署文档随包分发（Tailscale/多用户/远程访问）
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
    # R6-12 / F6-14：托盘图标（关闭窗口后最小化到通知区）
    # pystray 的 Windows 后端按平台动态导入，PyInstaller 静态分析抓不到。
    'pystray',
    'pystray._win32',
    'PIL.Image',
    'PIL.ImageDraw',
]
# lxml 的 C 扩展（etree/html/_elementtree）与实体映射不被自动收全，
# 冻结形态下 akshare 的 read_html 通道会抛 LibNotFoundError（阶段 10 L-4 实测发现）
for package in ('lxml', 'lxml.etree', 'lxml.html', 'lxml.objectify', 'lxml.html.clean'):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hidden
    except Exception:  # noqa: BLE001 - 单个子包缺失不影响整体
        pass

for package in ('webview', 'pythonnet', 'clr_loader', 'bottle', 'proxy_tools'):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# ---------------- V8 运行库：py_mini_racer（必收，v3.6.1 修复 D-1） ----------------
# akshare 的新浪通道（stock_zh_index_daily / stock_zh_a_daily 等）用 py_mini_racer.MiniRacer()
# 执行新浪返回的 JS；该库按 sys._MEIPASS/py_mini_racer/{mini_racer.dll,icudtl.dat} 定位
# （见 py_mini_racer/_dll.py::_open_dll 的 PyInstaller 分支），缺失即抛 LibNotFoundError。
# PyInstaller 无法静态发现（ctypes 动态加载 + 包内数据文件），必须显式 collect_all；
# collect_all 的 dest 为包目录名，正好落在 _internal/py_mini_racer/。
# 注意：必须放在上面 `binaries = []` 初始化之后，否则本次收集会被清空。
_sw = ('mini_racer.dll', 'icudtl.dat')
_pmr_datas, _pmr_binaries, _pmr_hidden = collect_all('py_mini_racer')
# 动态库走 binaries（dest = 包目录名），数据文件走 datas；同一文件不重复登记，避免覆盖冲突
_bin_names = {os.path.basename(src).lower() for src, _dest in _pmr_binaries}
datas += [(src, dest) for src, dest in _pmr_datas
          if os.path.basename(src).lower() not in _bin_names]
binaries += list(_pmr_binaries)
hiddenimports += _pmr_hidden
# fail-fast：两个 V8 文件必须落在 py_mini_racer\ 子目录（库按 sys._MEIPASS/py_mini_racer/ 定位）
_placed = {os.path.basename(src).lower(): dest.replace("\\", "/")
           for src, dest in (list(datas) + list(binaries))}
for _need in _sw:
    if _need not in _placed:
        raise SystemExit(
            f"[spec] V8 运行库缺少 {_need}：py_mini_racer 收集失败，禁止打包"
            "（冻结形态下 akshare 新浪通道将不可用，D-1）"
        )
    if not _placed[_need].endswith("py_mini_racer"):
        raise SystemExit(f"[spec] V8 运行库 {_need} 落位错误：dest={_placed[_need]}，应为 py_mini_racer\\")
print(f"[spec] py_mini_racer 已收集：datas={len(_pmr_datas)} binaries={len(_pmr_binaries)} "
      f"hiddenimports={len(_pmr_hidden)}；落位={ {k: _placed[k] for k in _sw} }")

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
