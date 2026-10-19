# -*- coding: utf-8 -*-
"""生成桌面壳与浏览器图标（assets/icon.ico + frontend/favicon.ico）。

纯标准库实现：32×32 / 32 位色 BMP-in-ICO，绘制品牌色圆环 + 中心方块，
无需 Pillow。运行：
    .venv\\Scripts\\python.exe tools\\make_icon.py
"""

from __future__ import annotations

import struct
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
SIZE = 32
BG = (0x82, 0x26, 0x61)   # 品牌深色 #822661（与 theme.css --accent-deep 一致）
FG = (0xF2, 0xF2, 0xF2)   # 前景浅色（--text）
RING_INNER = 7.0
RING_OUTER = 12.5


def build_pixels() -> bytes:
    """生成 32×32 的 BGRA 像素（自下而上，BMP 约定）。"""
    rows = []
    center = (SIZE - 1) / 2.0
    for y in range(SIZE - 1, -1, -1):
        row = bytearray()
        for x in range(SIZE):
            dx = x - center
            dy = y - center
            distance = (dx * dx + dy * dy) ** 0.5
            if RING_INNER <= distance <= RING_OUTER:
                r, g, b = FG
            else:
                # 圆角方形底：四角透明，其余品牌色
                corner = 4
                if (x < corner or x >= SIZE - corner) and (y < corner or y >= SIZE - corner):
                    row.extend((0, 0, 0, 0))
                    continue
                r, g, b = BG
            row.extend((b, g, r, 255))
        rows.append(bytes(row))
    return b"".join(rows)


def build_ico() -> bytes:
    """组装 ICO 文件（单张 32×32，32bpp）。"""
    pixels = build_pixels()
    header_size = 40
    image_size = header_size + len(pixels)
    # ICONDIR
    data = struct.pack("<HHH", 0, 1, 1)
    # ICONDIRENTRY：宽高 32、颜色数 0、保留 0、平面 1、位深 32、大小、偏移 22
    data += struct.pack("<BBBBHHII", SIZE, SIZE, 0, 0, 1, 32, image_size, 22)
    # BITMAPINFOHEADER：高度为 2 倍（BMP 约定包含掩码区）
    data += struct.pack("<IiiHHIIiiII", header_size, SIZE, SIZE * 2, 1, 32, 0, len(pixels), 0, 0, 0, 0)
    data += pixels
    # AND 掩码（32bpp 下可为全 0）
    data += b"\x00" * ((SIZE // 8) * SIZE)
    return data


def main() -> None:
    """写出 ico 到 assets/ 与 frontend/（后者供浏览器 favicon）。"""
    ico = build_ico()
    targets = [PROJECT_DIR / "assets" / "icon.ico", PROJECT_DIR / "frontend" / "favicon.ico"]
    for target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(ico)
        print(f"已生成 {target}（{len(ico)} 字节）")


if __name__ == "__main__":
    main()
