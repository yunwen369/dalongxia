# -*- coding: utf-8 -*-
"""板块端点冷启动耗时剖析（cProfile 热点定位 + 分阶段计时）。

背景（2026-11-15）：端点全量探针在**服务重启后**实测
    /api/sector/crowding 24.3s、/api/sector/fundflow 24.3s（其余 115 条均 <10s）。
已有「缓存优先 + 后台刷新 + 启动预热」设计，故需确认：是预热未覆盖、缓存只在内存，
还是取数链路本身有串行放大。本工具在**独立进程**（自带冷内存缓存）里跑，
按函数累计耗时排序，避免凭印象改代码。

用法：
    ./.venv/Scripts/python.exe tools/_prof_sector_endpoints.py --target fundflow
    ./.venv/Scripts/python.exe tools/_prof_sector_endpoints.py --target rankings
    ./.venv/Scripts/python.exe tools/_prof_sector_endpoints.py --target both --top 25

要点：
    - 直接调 provider 层（绕过 HTTP 与路由缓存），暴露真实取数成本；
    - 末尾打印「缓存是否命中」以区分「算得慢」与「每次都重算」；
    - 磁盘缓存（板块成分映射等）是共享的，保持现状即真实冷启动条件。
"""

from __future__ import annotations

import argparse
import cProfile
import io
import pstats
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _profile(label: str, func, top: int) -> float:
    """对单个可调用对象做 cProfile，并打印累计耗时 Top N。

    Args:
        label: 打印用标签。
        func: 无参可调用对象。
        top: 打印条数。

    Returns:
        float: 墙钟耗时（秒）。
    """
    profiler = cProfile.Profile()
    started = time.time()
    profiler.enable()
    func()
    profiler.disable()
    elapsed = time.time() - started

    buffer = io.StringIO()
    pstats.Stats(profiler, stream=buffer).sort_stats("cumulative").print_stats(top)
    lines = buffer.getvalue().splitlines()
    print(f"\n===== {label} 墙钟 {elapsed:.2f}s =====")
    # 只保留热点区（跳过 pstats 头部噪声）
    for line in lines:
        if "ncalls" in line or "function calls" in line:
            print(line)
            break
    started_out = False
    for line in lines:
        if "ncalls" in line:
            started_out = True
            continue
        if started_out:
            if line.strip().startswith("{"):
                break
            print(line)
    return elapsed


def main() -> int:
    """入口：按 --target 选择被剖析对象。

    Returns:
        int: 0=正常，1=被剖析对象抛错。
    """
    parser = argparse.ArgumentParser(description="板块端点冷启动剖析")
    parser.add_argument("--target", choices=("fundflow", "rankings", "both"), default="both")
    parser.add_argument("--top", type=int, default=18, help="打印热点条数")
    parser.add_argument("--limit", type=int, default=50, help="排行返回条数")
    args = parser.parse_args()

    from backend.app import config  # noqa: F401 - 确保环境变量与配置先加载
    from backend.app.core import cache
    from backend.app.datasource import sector

    print(f"[env] APP_VERSION={getattr(config, 'APP_VERSION', '?')} "
          f"TTL.fund_flow={config.TTL.get('fund_flow')} TTL.sector={config.TTL.get('sector')}")

    # 清进程内存缓存，模拟「服务刚重启」的冷态（磁盘映射缓存仍共享，属真实条件）
    if hasattr(cache, "clear_cache"):
        cache.clear_cache()

    if args.target in ("fundflow", "both"):
        print("\n[1] provider.get_fundflow（同花顺行业资金流 + 四档聚合）")
        try:
            _profile("get_fundflow", lambda: sector.get_fundflow("cn"), args.top)
        except Exception as exc:  # noqa: BLE001 - 剖析工具需打印而非中断
            print(f"!! get_fundflow 抛错：{type(exc).__name__}: {exc}")
            return 1

    if args.target in ("rankings", "both"):
        print("\n[2] provider.get_crowding_rankings（90 行业逐板块六维打分）")
        try:
            elapsed = _profile(
                "get_crowding_rankings",
                lambda: sector.get_crowding_rankings(kind="industry", limit=args.limit),
                args.top,
            )
            print(f"[2] 单向扫描 {elapsed:.2f}s")
        except Exception as exc:  # noqa: BLE001
            print(f"!! get_crowding_rankings 抛错：{type(exc).__name__}: {exc}")
            return 1

    print("\n[3] 组件缓存状态（判断「算得慢」还是「每次重算」）")
    for key in ("sector:fundflow:cn", "sector:crowding:rankings:industry:50",
                "sector:industry:cn"):
        value = cache.get(key, 3600 * 24)
        print(f"    {key:<42} cached={'是' if value is not None else '否'}")
    cons_file = sector._cons_cache_file()
    print(f"    成分映射文件：{cons_file} 存在={cons_file.exists()} "
          f"大小={cons_file.stat().st_size if cons_file.exists() else 0}B")
    print(f"    内存成分表条目：{len(getattr(sector, '_CONS_CACHE', {}) or {})}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
