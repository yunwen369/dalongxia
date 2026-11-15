# -*- coding: utf-8 -*-
"""同花顺行情数据域（d.10jqka.com.cn）限流容忍度实测。

背景（2026-11-15，v3.7.1 REQ-20261115-02）
------------------------------------------
拥挤度排行要逐板块拉 K 线，90 个板块 × 2 个文件（``all.js`` + ``last.js``）
= 180 次请求，会被 :data:`ths_http._POLITE_DELAY` 的**进程级最小间隔 0.1s**
串成 ≥18s —— 这才是冷启动 24s 的真正下限（TLS 握手已由会话复用消除，仅省 4.5s）。

``_POLITE_DELAY`` 里 0.1s 是第八轮为避免误伤而取的保守值（注释原文：
「行情数据域为纯静态 JSON 无此限制，保持低延迟」），从未实测。本工具直接打
该域，按指定间隔与并发跑一轮，统计 HTTP 状态码分布，用来决定能否下调间隔。

判据：**0 个 401/403** 且全部 200 才认为该间隔安全；出现任何一个 401/403 即判不安全
（同花顺反爬是按 IP 的短时封禁，一旦触发会拖累所有板块链路）。

用法：
    ./.venv/Scripts/python.exe tools/_probe_ths_rate.py                 # 默认 0.05s / 8 并发
    ./.venv/Scripts/python.exe tools/_probe_ths_rate.py --gap 0 --workers 8
    ./.venv/Scripts/python.exe tools/_probe_ths_rate.py --boards 30 --join

注意：
    - 本工具**只发只读 GET**，不改任何落盘状态；
    - ``--join`` 让请求真正并发抢跑（用于测「无间隔时源站是否限流」），
      不加则按 gap 预约时间片（复刻 ths_http._throttle 语义）。
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SESSION_LOCAL = threading.local()


def _session() -> Any:
    """取线程内复用的会话（与 ths_http._session 同构）。

    Returns:
        requests.Session: 已配置 trust_env=False 的会话。
    """
    from backend.app.core import netfix

    session = getattr(_SESSION_LOCAL, "value", None)
    if session is None:
        session = netfix.make_session()
        _SESSION_LOCAL.value = session
    return session


def _fetch(url: str, referer: str) -> Tuple[int, int]:
    """发一次 GET，返回 (状态码, 正文字节数)；异常时状态码记 -1。

    Args:
        url: 目标 URL。
        referer: Referer。

    Returns:
        Tuple[int, int]: (状态码, 字节数)。
    """
    from backend.app import config

    try:
        response = _session().get(
            url,
            headers={
                "User-Agent": config.BROWSER_USER_AGENT,
                "Referer": referer,
                "Accept": "text/html,application/javascript,*/*",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
            timeout=config.DATASOURCE_TIMEOUT,
        )
        return response.status_code, len(response.content or b"")
    except Exception:  # noqa: BLE001 - 探测工具：异常折算为 -1 便于统计
        return -1, 0


def main() -> int:
    """入口。

    Returns:
        int: 0=全部 200（间隔安全）；1=出现非 200（间隔不安全或网络异常）。
    """
    parser = argparse.ArgumentParser(description="同花顺 d 域限流容忍度实测")
    parser.add_argument("--gap", type=float, default=0.05, help="请求间隔秒（0=不间隔）")
    parser.add_argument("--workers", type=int, default=8, help="并发线程数")
    parser.add_argument("--boards", type=int, default=40, help="抽测板块数（默认 40，全量 90）")
    parser.add_argument("--join", action="store_true", help="忽略 gap，全部并发抢跑")
    parser.add_argument("--files", default="all", choices=["all", "both"], help="每板块请求文件数")
    args = parser.parse_args()

    from backend.app import config
    from backend.app.datasource import ths_http

    codes = list(ths_http.get_industry_codes().values())[: max(1, args.boards)]
    if not codes:
        print("行业名录为空，无法探测（先确认网络可通同花顺）")
        return 1

    lock = threading.Lock()
    next_slot = [time.time()]

    def _reserve() -> float:
        """预约下一个时间片（复刻 _throttle 的锁内算术语义）。

        Returns:
            float: 需要等待的秒数。
        """
        if args.join or args.gap <= 0:
            return 0.0
        with lock:
            slot = max(next_slot[0] + args.gap, time.time())
            next_slot[0] = slot
        return max(0.0, slot - time.time())

    targets: List[str] = []
    for code in codes:
        targets.append(f"{config.THS_LINE_BASE}/v6/line/bk_{code}/01/all.js")
        if args.files == "both":
            targets.append(f"{config.THS_LINE_BASE}/v6/line/bk_{code}/01/last.js")

    def _one(url: str) -> Tuple[int, int]:
        """按预约时间片执行一次请求。

        Args:
            url: 目标 URL。

        Returns:
            Tuple[int, int]: (状态码, 字节数)。
        """
        wait = _reserve()
        if wait > 0:
            time.sleep(wait)
        return _fetch(url, config.THS_REFERER)

    started = time.time()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        results = list(pool.map(_one, targets))
    elapsed = time.time() - started

    counter: Counter = Counter(code for code, _ in results)
    sizes = [size for code, size in results if code == 200]
    print(f"间隔={args.gap}s 并发={args.workers} 请求={len(targets)} "
          f"墙钟={elapsed:.2f}s 有效QPS={len(targets) / elapsed:.1f}")
    print("状态码分布：" + "，".join(f"{code}×{count}" for code, count in sorted(counter.items())))
    if sizes:
        print(f"200 响应体：最小 {min(sizes)}B / 平均 {sum(sizes) // len(sizes)}B / 最大 {max(sizes)}B")
    blocked = sum(count for code, count in counter.items() if code in (401, 403))
    if blocked:
        print(f"!! 出现 {blocked} 个 401/403 —— 该间隔不安全，维持或上调 _POLITE_DELAY")
        return 1
    not_ok = [code for code in counter if code != 200]
    if not_ok:
        print(f"!! 出现非 200 状态码 {not_ok} —— 结论不可用，需排查网络")
        return 1
    print("全部 200 —— 该间隔本轮安全（同花顺反爬为概率触发，建议连测 3 轮再定论）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
