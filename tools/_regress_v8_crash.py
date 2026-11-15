"""V8 双初始化崩溃回归：同一代码路径，只切换「有没有 serial_v8 串行」做 A/B 对照。

背景（2026-11-15 实测实锤）
---------------------------
``py_mini_racer`` 的 V8 PartitionAlloc **禁止多线程并发首次初始化**。一旦并发进去，
进程被原生 FATAL 直接带走，连 Python 栈都没有：

    [FATAL:partition_address_space.cc(243)]
    Check failed: !IsConfigurablePoolInitialized().

触发场景：v3.7.2 把成分股 K 线改成并发取数后，东财通道一旦 ``ConnectionError``
就会降级到新浪；N 个线程同时首次初始化 V8 ⇒ **整进程崩溃**（uvicorn 退出码 3）。

修复：``ashare._call_ak_v8`` 把 4 处新浪系调用统一包进 ``v8_guard.serial_v8``。

本脚本的判据
------------
``--mode serial``（修复后）：N 线程并发调 ``get_kline_sina``，进程必须**存活**
（退出码 0），且至少部分调用拿到数据。

``--mode raw``（还原修复前：把 ``_call_ak_v8`` 换回 ``_call_ak``）：同一批调用
应能复现 FATAL（退出码非 0 / 输出含 ``partition_address_space``）。若此模式未崩溃，
说明当前 akshare 版本的 V8 构造点已被上游改为模块级——那就只能靠 serial 模式
「存活」这一条正向证据，报告里需如实标注。

用法::

    ./.venv/Scripts/python.exe tools/_regress_v8_crash.py --mode serial
    ./.venv/Scripts/python.exe tools/_regress_v8_crash.py --mode raw
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 本机 HTTP_PROXY 会拦截外部域名；新浪直连必须绕过代理
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

SYMBOLS = [
    "sz300750", "sh600519", "sz000001", "sh601318",
    "sz002594", "sh600036", "sz000858", "sh601899",
    "sh600276", "sz000651", "sh601166", "sz002415",
]


def main() -> int:
    """并发打新浪日线，观察进程是否被 V8 双初始化带走。

    Returns:
        int: 0 = 进程存活且至少一只成功；2 = 存活但全失败；崩溃时不会有返回值。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("serial", "raw"), required=True,
                        help="serial=修复后（加锁）；raw=还原修复前（不加锁）")
    parser.add_argument("--threads", type=int, default=8, help="并发线程数")
    args = parser.parse_args()

    from backend.app.datasource import ashare

    if args.mode == "raw":
        # 精确还原修复前行为：_call_ak_v8 退化为不加锁的 _call_ak
        ashare._call_ak_v8 = ashare._call_ak  # noqa: SLF001 - 探针故意还原缺陷

    symbols = (SYMBOLS * ((args.threads // len(SYMBOLS)) + 1))[: args.threads]
    barrier = threading.Barrier(args.threads)
    outcome = {"ok": 0, "err": 0, "detail": []}
    lock = threading.Lock()

    def worker(symbol: str) -> None:
        """等齐所有线程后同时冲进新浪日线，最大化并发首次初始化概率。"""
        try:
            barrier.wait(30)
        except threading.BrokenBarrierError:
            pass
        try:
            rows = ashare.get_kline_sina(symbol, "daily", "qfq")
            with lock:
                if rows:
                    outcome["ok"] += 1
                else:
                    outcome["err"] += 1
                    outcome["detail"].append(f"{symbol}:空")
        except Exception as exc:  # noqa: BLE001 - 单只失败不算崩溃
            with lock:
                outcome["err"] += 1
                outcome["detail"].append(f"{symbol}:{type(exc).__name__}")

    print(f"[mode={args.mode}] {args.threads} 线程并发调用 ashare.get_kline_sina ...",
          flush=True)
    started = time.time()
    threads = [threading.Thread(target=worker, args=(s,), daemon=True) for s in symbols]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    cost = time.time() - started

    print(f"[mode={args.mode}] 进程存活，耗时 {cost:.2f}s，成功 {outcome['ok']} / "
          f"失败 {outcome['err']}", flush=True)
    if outcome["detail"]:
        print(f"[mode={args.mode}] 失败样例：{outcome['detail'][:6]}", flush=True)
    return 0 if outcome["ok"] > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
