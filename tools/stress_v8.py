# -*- coding: utf-8 -*-
"""R-012 专项压测：定位 py_mini_racer 在串行调用下的残余崩溃路径。

背景（规范 §1.9 根因分析）：阶段 8 已用进程级 RLock 消除「多线程并发首次创建
MiniRacer 上下文」引发的 PartitionAlloc 双初始化原生崩溃（必崩）。但源码模式
（安装版按 §13.6-2 已禁用）仍偶发崩溃，需定位残余路径。

本脚本按三个维度压测，各自独立进程运行（任一崩溃可从父进程退出码观察）：
  A. 单线程连续 N 次（串行 GC 时序）
  B. 单实例复用 N 次（同一 MiniRacer 反复 eval）
  C. 低内存 + 高频创建（资源压力）

用法：
    python tools/stress_v8.py --mode a --n 300      # 单线程连续
    python tools/stress_v8.py --mode b --n 300      # 单实例复用
    python tools/stress_v8.py --mode c --n 300      # 资源压力
"""

from __future__ import annotations

import argparse
import gc
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py_mini_racer import MiniRacer  # noqa: E402


def mode_a(n: int) -> str:
    """单线程连续创建/销毁 MiniRacer（复刻串行调用形态）。"""
    for i in range(n):
        ctx = MiniRacer()
        ctx.eval("1+1")
        del ctx
        gc.collect()
    return f"mode_a 完成 {n} 次，未崩溃"


def mode_b(n: int) -> str:
    """单实例反复 eval（复刻长生命周期 V8 上下文）。"""
    ctx = MiniRacer()
    ctx.eval("var acc = 0;")
    for i in range(n):
        ctx.eval(f"acc += {i};")
        ctx.eval("acc")
    ctx.eval("acc")
    del ctx
    return f"mode_b 完成 {n} 次，未崩溃"


def mode_c(n: int) -> str:
    """极限堆 + 高频创建（RE-012 残余崩溃候选：分配器压力）。"""
    # Windows 无 RLIMIT_AS；改用负压测试（大量分配 + 立即释放）逼近分配器压力
    pass
    for i in range(n):
        ctx = MiniRacer()
        bytearray(1024 * 512)
        gc.collect()
        ctx.eval("'%d'" % i)
        del ctx
    return f"mode_c 完成 {n} 次，未崩溃"


MODES = {"a": mode_a, "b": mode_b, "c": mode_c}


def main() -> None:
    """入口：按模式执行压测，崩溃由父进程通过退出码捕获。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=sorted(MODES), default="a")
    parser.add_argument("--n", type=int, default=200)
    args = parser.parse_args()
    started = time.time()
    result = MODES[args.mode](args.n)
    print(result, f"（耗时 {time.time() - started:.1f}s）", flush=True)


if __name__ == "__main__":
    main()
