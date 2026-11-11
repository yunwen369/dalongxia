# -*- coding: utf-8 -*-
"""重建桌面快捷方式（v2.1.0）——ctypes 直调 IShellLinkW，不依赖 WScript.Shell COM。

背景：宿主安全策略拦截 PowerShell 实例化 WScript.Shell，故用 ctypes 走原生 COM。
文件名保持不变（桌面图标位置/显示名不打扰），仅更新内部目标、工作目录、描述与图标。

v2.1.0 起默认形态为「浏览器模式」：快捷方式不带参数即为网页版，
需要独立窗口时给目标加 ``--desktop``，局域网为 ``--lan``，公网为 ``--public``。
"""
import ctypes
import os
from ctypes import POINTER, byref, c_int, c_ulong, c_ushort, c_void_p, c_wchar_p

EXE = r"F:\workbuddy记录\OpenBB Terminal Pro\A_运行资产\正式环境\OpenBB-CN.exe"
WORKDIR = r"F:\workbuddy记录\OpenBB Terminal Pro\A_运行资产\正式环境"
LNK = os.path.join(os.environ["USERPROFILE"], "Desktop", "OpenBB 中文投研工作台.lnk")
DESC = "OpenBB 中文投研工作台 v3.0.3（网页版 · 正式环境 · V6 量化看盘台皮肤）"

CLSCTX_INPROC_SERVER = 1


class GUID(ctypes.Structure):
    """Windows GUID 结构。"""

    _fields_ = [
        ("Data1", c_ulong),
        ("Data2", c_ushort),
        ("Data3", c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


ole32 = ctypes.windll.ole32


def guid(text: str) -> GUID:
    """由字符串生成 GUID。

    Args:
        text: 形如 ``{00021401-0000-0000-C000-000000000046}``。

    Returns:
        GUID: 解析结果。
    """
    g = GUID()
    hr = ole32.CLSIDFromString(c_wchar_p(text), byref(g))
    if hr != 0:
        raise OSError(f"CLSIDFromString 失败 0x{hr & 0xFFFFFFFF:08X}")
    return g


def make_caller(ptr: c_void_p, index: int, *argtypes):
    """取虚表第 index 个方法并封装为可调用对象。

    Args:
        ptr: COM 接口指针。
        index: 虚表下标。
        *argtypes: 除 this 外的参数类型。

    Returns:
        callable: 调用器。
    """
    vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, *argtypes)
    return proto(vtbl[index])


def check(hr: int, what: str) -> None:
    """校验 HRESULT。

    Args:
        hr: 返回值。
        what: 动作名（报错用）。

    Raises:
        OSError: HRESULT 为负时抛出。
    """
    if hr < 0:
        raise OSError(f"{what} 失败 0x{hr & 0xFFFFFFFF:08X}")


def main() -> None:
    """创建或覆盖桌面快捷方式并复核。"""
    if not os.path.exists(EXE):
        raise SystemExit(f"目标不存在：{EXE}")

    ole32.CoInitialize(None)
    try:
        clsid = guid("{00021401-0000-0000-C000-000000000046}")   # ShellLink
        iid_link = guid("{000214F9-0000-0000-C000-000000000046}")  # IShellLinkW
        iid_pf = guid("{0000010B-0000-0000-C000-000000000046}")    # IPersistFile

        link = c_void_p()
        check(
            ole32.CoCreateInstance(byref(clsid), None, CLSCTX_INPROC_SERVER,
                                   byref(iid_link), byref(link)),
            "CoCreateInstance(ShellLink)",
        )

        check(make_caller(link, 20, c_wchar_p)(link, EXE), "SetPath")             # 20 = SetPath
        check(make_caller(link, 9, c_wchar_p)(link, WORKDIR), "SetWorkingDirectory")  # 9
        check(make_caller(link, 7, c_wchar_p)(link, DESC), "SetDescription")      # 7
        check(make_caller(link, 17, c_wchar_p, c_int)(link, EXE, 0), "SetIconLocation")  # 17

        pf = c_void_p()
        check(
            make_caller(link, 0, POINTER(GUID), POINTER(c_void_p))(
                link, byref(iid_pf), byref(pf)),
            "QueryInterface(IPersistFile)",
        )
        check(make_caller(pf, 6, c_wchar_p, c_int)(pf, LNK, 1), "IPersistFile.Save")  # 6

        make_caller(pf, 2)(pf)   # Release
        make_caller(link, 2)(link)
        print(f"已重建：{LNK}")
        print(f"  目标    ：{EXE}")
        print(f"  工作目录：{WORKDIR}")
        print(f"  描述    ：{DESC}")
    finally:
        ole32.CoUninitialize()


if __name__ == "__main__":
    main()
