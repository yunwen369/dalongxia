"""单板块拥挤度（/api/sector/crowding）冷启动耗时拆解探针。

复用第十一轮的性能诊断思路：**直接计时，不用 cProfile**（Py3.13 的 cProfile 会
剖析工作线程，绝对墙钟放大 ~2.5×，报耗时必须以直接计时为准）。

用法（在 openbb-cn 目录下）::

    ./.venv/Scripts/python.exe tools/_prof_crowding.py --board 半导体
    ./.venv/Scripts/python.exe tools/_prof_crowding.py --board 半导体 --raw

``--raw`` 额外拆内部：``_board_float_cap`` 走的是哪一级口径、快照读取耗时、
``_constituent_correlation`` 单只 K 线耗时分布。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _t(fn, *args, **kwargs):
    """执行并返回 (结果, 耗时秒)。"""
    start = time.perf_counter()
    value = fn(*args, **kwargs)
    return value, time.perf_counter() - start


def main() -> int:
    """脚本入口。

    Returns:
        int: 进程退出码。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", default="半导体")
    parser.add_argument("--kind", default="industry", choices=["industry", "concept"])
    parser.add_argument("--raw", action="store_true", help="额外拆内部步骤")
    parser.add_argument("--fill", action="store_true", help="先同步补齐该板块的流通市值缓存")
    args = parser.parse_args()

    from backend.app.datasource import sector

    if args.fill:
        # 直接同步翻一次该板块（内部 _remember_cap 落盘），避免只补到 _CONS_CACHE 前几个
        _, fill_s = _t(sector.get_cons_ths, args.board)
        print(f"[fill] get_cons_ths 全量翻页 {fill_s:.2f}s  "
              f"cap={sector._cached_cap(args.board)}")
        sector._LOOKUP.pop(f"sector:floatcap:{args.board}", None)  # 失效进程内 6h 缓存
        sector._LOOKUP_TS.pop(f"sector:floatcap:{args.board}", None)

    sector._load_cons_cache()
    codes = sector._CONS_CACHE.get(args.board) or []
    print(f"板块：{args.board}（{args.kind}）")
    print(f"成分映射缓存：{len(codes)} 只 {'（已就绪）' if codes else '（未预热）'}")

    if args.raw:
        print("\n── 内部步骤拆解 ──")
        cap_block, cap_s = _t(sector._board_float_cap, args.board)
        caliber = (cap_block or {}).get("caliber")
        value = (cap_block or {}).get("value")
        print(f"  _board_float_cap            {cap_s:6.2f}s  口径={caliber} 值={value}")

        from backend.app.datasource import screener as _screener

        snap, snap_s = _t(_screener._read_snapshot)
        rows = (snap or {}).get("rows") or (snap or {}).get("items") or []
        print(f"  _screener._read_snapshot    {snap_s:6.2f}s  行数={len(rows)}")

        if codes:
            sample = [{"code": c, "board": args.board} for c in codes[:20]]
            corr, corr_s = _t(sector._constituent_correlation, sample)
            print(f"  _constituent_correlation    {corr_s:6.2f}s  corr={ (corr or {}).get('corr') }"
                  f" samples={ (corr or {}).get('samples') }")
            turn, turn_s = _t(sector._constituent_turnover, sample)
            print(f"  _constituent_turnover       {turn_s:6.2f}s  turnover={ (turn or {}).get('turnover') }"
                  f" samples={ (turn or {}).get('samples') }")

    print("\n── get_crowding 整体 ──")
    payload, total_s = _t(sector.get_crowding, args.board, kind=args.kind)
    print(f"  total                       {total_s:6.2f}s  score={payload.get('score')}")
    parts = payload.get("parts") or []
    part_iter = parts.items() if isinstance(parts, dict) else enumerate(parts)
    for key, part in part_iter:
        if isinstance(part, dict):
            print(f"    part {str(key):12s} weight={part.get('weight')} pct={part.get('pct')}")
    for key in ("correlation", "turnover", "turnover_pct"):
        block = payload.get("extra", {}).get(key)
        if block:
            print(f"  extra.{key}: { {k: v for k, v in block.items() if k != 'caliber'} }")
            print(f"      caliber: {block.get('caliber') or block.get('note')}")
        else:
            print(f"  extra.{key}: 缺失")
    caliber = payload.get("caliber")
    if isinstance(caliber, dict):
        missing = caliber.get("missing") or []
    else:
        missing = payload.get("missing") or []
    if missing:
        print(f"  missing: {missing}")
    print(f"  caliber: {caliber if isinstance(caliber, str) else '（结构化）'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
