"""一次性补齐全部板块的「流通市值合计」落盘缓存（v3.7.2 升级辅助工具）。

背景：``data/board_cap.json`` 是 v3.7.2 新增的落盘缓存，由 ``_cons_ths`` 翻页时
顺手写入。旧版本升级上来时 ``board_cons.json`` 已有成分映射但缺市值，于是每个
板块的首次 ``/api/sector/crowding`` 只能按「本地快照总市值」口径返回。

服务运行期间 ``main._bg_prewarm`` 会每轮补 4 个板块兜底；本工具用于**发布前
一次性补一批**，让部署后即刻就是精确口径。

⚠️ 限速（实测）：连续补 **52 个板块（约 7 分钟、400+ 次 q 域请求）会触发同花顺
401 反爬**，冷却 15s 后恢复。故默认 ``--limit 30``、板块间 ``--sleep 1``；
要补更多请分多次跑（每次间隔 10 分钟以上），不要用 ``--limit 0`` 一把梭。

用法（在 openbb-cn 目录下）::

    ./.venv/Scripts/python.exe tools/_fill_board_caps.py              # 补最多 30 个
    ./.venv/Scripts/python.exe tools/_fill_board_caps.py --limit 5    # 只补 5 个（试跑）
    ./.venv/Scripts/python.exe tools/_fill_board_caps.py --check      # 只统计不补
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    """脚本入口。

    Returns:
        int: 进程退出码（0=全部就绪）。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=30, help="最多补多少个板块（默认 30，见限速说明）")
    parser.add_argument("--sleep", type=float, default=1.0, help="每个板块之间的喘息秒数")
    parser.add_argument("--check", action="store_true", help="只统计缺口数量，不实际补齐")
    args = parser.parse_args()

    from backend.app.datasource import sector

    sector._load_cons_cache()
    sector._load_cap_cache()
    total = len(sector._CONS_CACHE)
    missing = [b for b in sector._CONS_CACHE if sector._cached_cap(b) is None]
    print(f"成分映射：{total} 个板块 ｜ 已有市值：{total - len(missing)} ｜ 待补：{len(missing)}")
    if args.check or not missing:
        return 0

    targets = missing[: args.limit] if args.limit else missing
    print(f"开始补齐 {len(targets)} 个板块（每板块约 8s，本次约 {len(targets) * 8 // 60} 分钟）")
    started = time.time()
    filled = 0
    for index, board in enumerate(targets, 1):
        one_start = time.time()
        try:
            sector.get_cons_ths(board)  # 全量翻页 → 内部 _remember_cap 落盘
        except Exception as exc:  # noqa: BLE001 - 单板块失败继续
            print(f"  [{index}/{len(targets)}] {board} 失败：{type(exc).__name__}", flush=True)
            continue
        hit = sector._cached_cap(board) is not None
        filled += 1 if hit else 0
        print(
            f"  [{index}/{len(targets)}] {board} "
            f"{'OK' if hit else '缺'} {time.time() - one_start:.1f}s "
            f"cap={(sector._cached_cap(board) or 0) / 1e8:.1f} 亿",
            flush=True,
        )
        if args.sleep > 0 and index < len(targets):
            time.sleep(args.sleep)
    print(f"完成：{filled}/{len(targets)} 个板块，耗时 {time.time() - started:.0f}s")
    rest = [b for b in sector._CONS_CACHE if sector._cached_cap(b) is None]
    print(f"剩余待补：{len(rest)} 个" + (f"（{', '.join(rest[:5])}…）" if rest else ""))
    return 0 if not rest else 1


if __name__ == "__main__":
    raise SystemExit(main())
