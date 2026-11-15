"""v3.7.2 回归契约探针：把本次会话的每个 bug 修复 / 功能新增映射为可执行断言。

覆盖范围（``fdf4040..HEAD``，3 个提交）：

    修复类
      B1 并发降级新浪触发 V8 双初始化 ⇒ 整进程崩溃（本文件只验静态契约，
         动态崩溃复现见 ``tools/_regress_v8_crash.py``）
      B2 ``_board_float_cap`` 第①②级死代码 ⇒ 永远落到快照口径
      B3 ``_cached_cap`` 单档 TTL ⇒ 每 6h 静默换换手率分母口径
      B4 后台补齐线程污染 pytest ⇒ 套件 24% 处卡死
      B5 每板块一线程抢用户时间片 ⇒ 37.7s 极值
      B6 降级新浪被完全串行化 ⇒ 东财全挂时 16.2s
      B7 排行预热/请求双扫 + 缓存键带 limit ⇒ 首请求 7.4s、榜单长度互不共享

    功能类
      F1 市值落盘缓存 ``data/board_cap.json``
      F2 三档过期语义（6h 新鲜 / 7d 可信上限）
      F3 两阶段取数（东财并发 + 新浪限量补）
      F4 单线程串行补齐队列 + ``warm_board_caps``
      F5 ``warm_crowding_once`` 一次性初始化预热
      F6 排行缓存键去 limit + ``_slice_rankings`` 切片 + 合流等待
      F7 ``v8_guard.serial_v8`` 通道（与 guard_v8 共用同一把锁）
      F8 ``ths_http`` 线程内会话复用 + ``d`` 域限流 0.01s

用法::

    ./.venv/Scripts/python.exe tools/_regress_v372.py

退出码 0 = 全过；1 = 有失败项。结果同时写入 ``tools/_out/regress_v372.json``。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 探测进程一律按「测试环境」对待：禁用一切后台补齐/预热副作用，
# 否则本脚本会真的去翻同花顺成分页（5–10 页/板块），既慢又干扰被测状态。
os.environ.setdefault("PYTEST_CURRENT_TEST", "_regress_v372.py::probe")

RESULTS = []


def check(name, fn, *, section=""):
    """执行一条断言并记录结果。

    Args:
        name: 断言名称（报告里逐条列出）。
        fn: 无参可调用；返回 (bool, str) 或 bool。
        section: 所属分组（修复类 / 功能类 / 契约）。
    """
    started = time.time()
    try:
        out = fn()
        if isinstance(out, tuple):
            passed, detail = out
        else:
            passed, detail = bool(out), ""
    except Exception as exc:  # noqa: BLE001 - 探针要收集一切异常而非中断
        passed, detail = False, f"{type(exc).__name__}: {exc}"
    cost = time.time() - started
    RESULTS.append({
        "section": section,
        "name": name,
        "passed": bool(passed),
        "detail": str(detail)[:600],
        "cost_s": round(cost, 3),
    })
    mark = "PASS" if passed else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
    return passed


# ---------------------------------------------------------------- 导入被测模块

from backend.app import config  # noqa: E402
from backend.app.core import cache, v8_guard  # noqa: E402
from backend.app.datasource import ashare, sector as sector_provider  # noqa: E402
from backend.app.datasource import ths_http  # noqa: E402
from backend.app.routers import sector as sector_router  # noqa: E402


def main() -> int:
    """跑完全部契约断言。

    Returns:
        int: 0 = 全过；1 = 有失败。
    """
    print("=" * 78)
    print("v3.7.2 回归契约探针")
    print("=" * 78)

    # ---------------------------------------------------------------- S1 版本
    print("\n[S1] 版本与构建标识")
    check("APP_VERSION == 3.7.2", lambda: (config.APP_VERSION == "3.7.2", config.APP_VERSION),
          section="版本")
    check("BUILD_TAG == v3.7.2-20261115",
          lambda: (config.BUILD_TAG == "v3.7.2-20261115", config.BUILD_TAG), section="版本")

    # ------------------------------------------- S2 F7/B1 serial_v8 通道契约
    print("\n[S2] F7/B1 serial_v8 通道契约")

    def _serial_v8_exists():
        return callable(v8_guard.serial_v8), "serial_v8 可调用"

    def _serial_v8_shares_lock():
        wrapper = v8_guard.serial_v8(lambda: None)
        same_mod = wrapper.__globals__ is vars(v8_guard)
        has_lock = "_V8_LOCK" in wrapper.__globals__
        return same_mod and has_lock, f"同模块全局={same_mod} 含_V8_LOCK={has_lock}"

    def _serial_v8_blocks_on_lock():
        """持锁期间另一线程调用 serial_v8 必须阻塞 ⇒ 证明用的是同一把锁。"""
        held = threading.Event()
        release = threading.Event()

        def holder():
            with v8_guard._V8_LOCK:  # noqa: SLF001 - 探针直取内部锁
                held.set()
                release.wait(5)

        t = threading.Thread(target=holder, daemon=True)
        t.start()
        if not held.wait(5):
            return False, "持锁线程未就绪"
        elapsed = {"v": 0.0}

        def worker():
            started = time.time()
            v8_guard.serial_v8(lambda: None)()
            elapsed["v"] = time.time() - started

        t2 = threading.Thread(target=worker, daemon=True)
        t2.start()
        time.sleep(0.4)
        blocked = t2.is_alive()
        release.set()
        t2.join(5)
        t.join(1)
        return blocked and elapsed["v"] >= 0.3, f"阻塞={blocked} 等待={elapsed['v']:.2f}s"

    def _serial_v8_exception_passthrough():
        def boom():
            raise ValueError("x")

        try:
            v8_guard.serial_v8(boom)()
        except ValueError:
            return True, "异常原样透传"
        return False, "异常被吞"

    check("serial_v8 存在且可调用", _serial_v8_exists, section="修复B1")
    check("serial_v8 与 guard_v8 共用 _V8_LOCK（全局引用）", _serial_v8_shares_lock, section="修复B1")
    check("serial_v8 在锁被占用时阻塞", _serial_v8_blocks_on_lock, section="修复B1")
    check("serial_v8 异常原样透传", _serial_v8_exception_passthrough, section="修复B1")

    def _ashare_call_sites():
        """4 个新浪系调用点必须全走 _call_ak_v8；东财系不得被误加锁。

        用 AST 而非字符串搜索：文件名在模块文档字符串/注释里也会出现，
        按 ``str.find`` 取「首次出现」会命中注释而误判（探针首版即栽在这）。
        """
        import ast

        src = (ROOT / "backend/app/datasource/ashare.py").read_text(encoding="utf-8")
        wrapped = {}
        for node in ast.walk(ast.parse(src)):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id not in ("_call_ak", "_call_ak_v8"):
                continue
            for arg in node.args:
                if not isinstance(arg, ast.Lambda):
                    continue
                body = arg.body
                if (isinstance(body, ast.Call)
                        and isinstance(body.func, ast.Attribute)
                        and isinstance(body.func.value, ast.Name)
                        and body.func.value.id == "ak"):
                    wrapped.setdefault(body.func.attr, set()).add(node.func.id)
        want_v8 = ("stock_zh_a_daily", "stock_zh_a_minute",
                   "index_us_stock_sina", "stock_zh_index_daily")
        want_plain = ("stock_zh_a_hist", "stock_zh_a_hist_min_em")
        problems = []
        for name in want_v8:
            got = wrapped.get(name, set())
            if got != {"_call_ak_v8"}:
                problems.append(f"{name}→{sorted(got) or '未包装'}")
        for name in want_plain:
            got = wrapped.get(name, set())
            if "_call_ak_v8" in got or not got:
                problems.append(f"{name}→{sorted(got) or '未包装'}")
        detail = "；".join(f"{k}→{sorted(v)}" for k, v in sorted(wrapped.items()))
        return not problems, (f"不符={problems} | 实际={detail}" if problems else detail)

    check("ashare 4 处新浪系调用全走 _call_ak_v8，东财系不串行",
          _ashare_call_sites, section="修复B1")

    # ---------------------------------------------- S3 F8 ths_http 会话与限流
    print("\n[S3] F8 ths_http 会话复用与 d 域限流")

    check("_POLITE_DELAY['d'] == 0.01（实测下调）",
          lambda: (ths_http._POLITE_DELAY.get("d") == 0.01, str(ths_http._POLITE_DELAY)),
          section="功能F8")
    check("_POLITE_DELAY['q'] == 1.1（维持不动，该域实证有 401）",
          lambda: (ths_http._POLITE_DELAY.get("q") == 1.1, str(ths_http._POLITE_DELAY.get("q"))),
          section="功能F8")

    def _session_reuse():
        s1 = ths_http._session()  # noqa: SLF001
        s2 = ths_http._session()  # noqa: SLF001
        same = s1 is s2
        ths_http._drop_session()  # noqa: SLF001
        s3 = ths_http._session()  # noqa: SLF001
        rebuilt = s3 is not s1
        ths_http._drop_session()  # noqa: SLF001
        return same and rebuilt, f"同线程复用={same} drop后重建={rebuilt}"

    def _session_per_thread():
        seen = {}

        def grab(tag):
            seen[tag] = id(ths_http._session())  # noqa: SLF001
            ths_http._drop_session()  # noqa: SLF001

        threads = [threading.Thread(target=grab, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        distinct = len(set(seen.values())) == 2
        return distinct, f"两线程会话 id 不同={distinct}"

    def _throttle_d_fast():
        """d 域连续两次预约应几乎不等待（<0.1s）⇒ 180 次请求不再被串成 18s。"""
        ths_http._THROTTLE_TS.clear()  # noqa: SLF001
        started = time.time()
        for _ in range(5):
            ths_http._throttle("d")  # noqa: SLF001
        cost = time.time() - started
        ths_http._THROTTLE_TS.clear()  # noqa: SLF001
        return cost < 0.15, f"5 次预约耗时 {cost * 1000:.1f}ms（阈值 150ms）"

    def _throttle_q_slow():
        """q 域连续两次预约必须等待 ≈1.1s ⇒ 反爬保护仍在。"""
        ths_http._THROTTLE_TS.clear()  # noqa: SLF001
        ths_http._throttle("q")  # noqa: SLF001
        started = time.time()
        ths_http._throttle("q")  # noqa: SLF001
        cost = time.time() - started
        ths_http._THROTTLE_TS.clear()  # noqa: SLF001
        return cost >= 1.0, f"第二次预约等待 {cost:.2f}s（应 ≥1.0s）"

    check("线程内会话复用 + drop 后重建", _session_reuse, section="功能F8")
    check("会话按线程隔离（threading.local）", _session_per_thread, section="功能F8")
    check("d 域限流 0.01s 不再成为瓶颈", _throttle_d_fast, section="功能F8")
    check("q 域限流 1.1s 反爬保护仍在", _throttle_q_slow, section="功能F8")

    # ------------------------------------------ S4 F1/F2 市值缓存与三档语义
    print("\n[S4] F1/F2 流通市值落盘缓存 + 三档过期语义")

    check("_CAP_CACHE_TTL == 6h",
          lambda: (sector_provider._CAP_CACHE_TTL == 6 * 3600,
                   str(sector_provider._CAP_CACHE_TTL)), section="功能F2")
    check("_CAP_CACHE_MAX_STALE == 7d",
          lambda: (sector_provider._CAP_CACHE_MAX_STALE == 7 * 86400,
                   str(sector_provider._CAP_CACHE_MAX_STALE)), section="功能F2")

    def _cap_file():
        path = sector_provider._cap_cache_file()  # noqa: SLF001
        if not path.is_file():
            return False, f"缺失 {path}"
        payload = json.loads(path.read_text(encoding="utf-8"))
        bad = [k for k, v in payload.items()
               if not (isinstance(v, dict) and "value" in v and "ts" in v)]
        total_yi = sum(v["value"] for v in payload.values()) / 1e8
        ok = len(payload) >= 80 and not bad and total_yi > 40e4
        return ok, (f"{path.name} 板块 {len(payload)} 条 / 结构异常 {len(bad)} / "
                    f"合计 {total_yi:,.0f} 亿元")

    check("board_cap.json 落盘且结构完整（≥80 板块、合计量级合理）",
          _cap_file, section="功能F1")

    CAP_BOARD = "_REGRESS_CAP_BOARD_"
    CAP_VALUE = 4.195433999999e12

    def _with_probe_cap(seconds_old):
        """在内存里放一条指定年龄的 cap 记录，执行 fn，随后还原。

        用 ``_CAP_CACHE_LOADED=True`` + mtime=inf 冻结磁盘重载，避免真实文件覆盖探测值。
        """
        def run(fn):
            saved = (dict(sector_provider._CAP_CACHE),
                     dict(sector_provider._CAP_CACHE_TS),
                     sector_provider._CAP_CACHE_LOADED,
                     sector_provider._CAP_CACHE_MTIME)
            sector_provider._CAP_CACHE_LOADED = True
            sector_provider._CAP_CACHE_MTIME = float("inf")
            sector_provider._CAP_CACHE[CAP_BOARD] = CAP_VALUE
            sector_provider._CAP_CACHE_TS[CAP_BOARD] = time.time() - seconds_old
            try:
                return fn()
            finally:
                sector_provider._CAP_CACHE.clear()
                sector_provider._CAP_CACHE.update(saved[0])
                sector_provider._CAP_CACHE_TS.clear()
                sector_provider._CAP_CACHE_TS.update(saved[1])
                sector_provider._CAP_CACHE_LOADED = saved[2]
                sector_provider._CAP_CACHE_MTIME = saved[3]
        return run

    def _fresh():
        def fn():
            value = sector_provider._cached_cap(CAP_BOARD)  # noqa: SLF001
            stale = sector_provider._cap_is_stale(CAP_BOARD)  # noqa: SLF001
            return (value == CAP_VALUE and stale is False,
                    f"60s → value={value} stale={stale}")
        return _with_probe_cap(60)(fn)

    def _stale_keeps_value():
        """B3 核心：6h~7d 之间必须**仍返回同口径值**（旧实现在此返回 None ⇒ 换分母）。"""
        def fn():
            value = sector_provider._cached_cap(CAP_BOARD)  # noqa: SLF001
            stale = sector_provider._cap_is_stale(CAP_BOARD)  # noqa: SLF001
            return (value == CAP_VALUE and stale is True,
                    f"6h+600s → value={value} stale={stale}（旧实现此处 None）")
        return _with_probe_cap(6 * 3600 + 600)(fn)

    def _beyond_max():
        def fn():
            value = sector_provider._cached_cap(CAP_BOARD)  # noqa: SLF001
            return value is None, f"7d+60s → value={value}"
        return _with_probe_cap(7 * 86400 + 60)(fn)

    def _absent():
        def fn():
            value = sector_provider._cached_cap("_REGRESS_NOT_EXIST_")  # noqa: SLF001
            stale = sector_provider._cap_is_stale("_REGRESS_NOT_EXIST_")  # noqa: SLF001
            return (value is None and stale is True, f"未知板块 → value={value} stale={stale}")
        return fn()

    check("新鲜期（60s）直接复用、不排刷新", _fresh, section="功能F2")
    check("过期但未超硬上限（6h+600s）仍返回同口径值", _stale_keeps_value, section="修复B3")
    check("超过硬上限（7d+60s）才返回 None", _beyond_max, section="功能F2")
    check("无缓存板块视为需补齐", _absent, section="功能F2")

    # ------------------------------------- S5 B2 第①②级死代码 ⇒ 口径是否精确
    print("\n[S5] B2 流通市值口径（第①②级死代码修复）")

    def _caliber_exact():
        payload = json.loads(sector_provider._cap_cache_file().read_text(encoding="utf-8"))  # noqa: SLF001
        board = max(payload, key=lambda k: payload[k]["value"])
        expected = payload[board]["value"]
        captured = {}
        original = sector_provider._lookup  # noqa: SLF001

        def fake_lookup(key, ttl, loader):
            captured["key"] = key
            captured["ttl"] = ttl
            return loader()

        sector_provider._lookup = fake_lookup  # noqa: SLF001
        try:
            result = sector_provider._board_float_cap(board)  # noqa: SLF001
        finally:
            sector_provider._lookup = original  # noqa: SLF001
        ok = (result is not None
              and result.get("caliber") == "同花顺成分流通市值合计"
              and abs(result.get("value", 0) - expected) / expected < 1e-9
              and captured.get("ttl") == 6 * 3600)
        return ok, (f"{board} → caliber={result.get('caliber') if result else None} "
                    f"value={result.get('value') if result else None:.0f} "
                    f"ttl={captured.get('ttl')}")

    def _caliber_unknown_board():
        captured = {}
        original = sector_provider._lookup  # noqa: SLF001

        def fake_lookup(key, ttl, loader):
            captured["ttl"] = ttl
            return loader()

        sector_provider._lookup = fake_lookup  # noqa: SLF001
        try:
            result = sector_provider._board_float_cap("_REGRESS_UNKNOWN_BOARD_")  # noqa: SLF001
        finally:
            sector_provider._lookup = original  # noqa: SLF001
        caliber = (result or {}).get("caliber", "")
        ok = caliber != "同花顺成分流通市值合计" and captured.get("ttl") == 60
        return ok, f"无缓存板块 → caliber='{caliber}' ttl={captured.get('ttl')}（应为 60s 短 TTL）"

    check("有 cap 缓存 → 精确口径 + 6h TTL", _caliber_exact, section="修复B2")
    check("无 cap 缓存 → 不冒称精确口径 + 60s 短 TTL", _caliber_unknown_board, section="修复B2")

    # ------------------------------------------------ S6 B4/B5 补齐队列与守卫
    print("\n[S6] B4/B5 串行补齐队列与测试守卫")

    def _guard_on():
        prev = os.environ.pop("PYTEST_CURRENT_TEST", None)
        try:
            allowed = sector_provider._cap_fill_allowed()  # noqa: SLF001
        finally:
            if prev is not None:
                os.environ["PYTEST_CURRENT_TEST"] = prev
        return allowed is True, f"非测试环境 allowed={allowed}"

    def _guard_off():
        prev = os.environ.get("PYTEST_CURRENT_TEST")
        os.environ["PYTEST_CURRENT_TEST"] = "x::y"
        try:
            allowed = sector_provider._cap_fill_allowed()  # noqa: SLF001
            filled = sector_provider.warm_board_caps(max_boards=1)
            warmed = sector_provider.warm_crowding_once()
        finally:
            if prev is None:
                os.environ.pop("PYTEST_CURRENT_TEST", None)
            else:
                os.environ["PYTEST_CURRENT_TEST"] = prev
        ok = allowed is False and filled == 0 and warmed == 0.0
        return ok, f"测试环境 allowed={allowed} warm_board_caps={filled} warm_crowding_once={warmed}"

    def _serial_queue():
        """补齐必须是「单线程串行消费队列」，不能是「每板块一线程」。

        B5 的原始缺陷正是每板块起一个线程 ⇒ 多个线程同时抢 q 域 1.1s 进程级
        限流 ⇒ 用户那次 /api/sector/crowding 被排到队尾（实测 37.7s 极值）。
        """
        import ast

        src = (ROOT / "backend/app/datasource/sector.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        start_src = ast.get_source_segment(src, funcs["_start_cap_fill"]) or ""
        loop_src = ast.get_source_segment(src, funcs["_cap_fill_loop"]) or ""
        thread_calls = [
            n for n in ast.walk(funcs["_start_cap_fill"])
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "Thread"
        ]
        one_thread = len(thread_calls) == 1
        target_ok = one_thread and any(
            kw.arg == "target" and isinstance(kw.value, ast.Name)
            and kw.value.id == "_cap_fill_loop"
            for kw in thread_calls[0].keywords
        )
        guard_ok = "_CAP_FILL_WORKER.is_alive()" in start_src
        serial_ok = "while True" in loop_src and "_CAP_FILL_PENDING.pop(0)" in loop_src
        return (one_thread and target_ok and guard_ok and serial_ok,
                f"仅1处Thread={one_thread} target=_cap_fill_loop={target_ok} "
                f"存活守卫={guard_ok} 逐个pop(0)={serial_ok}")

    check("测试环境外允许补齐", _guard_on, section="修复B4")
    check("测试环境内 warm_board_caps/warm_crowding_once 均空转",
          _guard_off, section="修复B4")
    check("补齐为单线程串行队列（非每板块一线程）", _serial_queue, section="修复B5")

    # ------------------------------------- S7 B6/F3 两阶段取数（东财并发+新浪限量）
    print("\n[S7] B6/F3 两阶段取数")

    check("CONSTITUENT_FALLBACK_MAX == 8",
          lambda: (sector_provider.CONSTITUENT_FALLBACK_MAX == 8,
                   str(sector_provider.CONSTITUENT_FALLBACK_MAX)), section="修复B6")
    check("CONSTITUENT_WORKERS == 4（降密度避免东财 RemoteDisconnected）",
          lambda: (sector_provider.CONSTITUENT_WORKERS == 4,
                   str(sector_provider.CONSTITUENT_WORKERS)), section="修复B6")

    def _two_phase_sina_cap():
        """东财全挂时必须走新浪补，但**限量**且总耗时受控。"""
        calls = {"em": 0, "sina": 0}
        orig_em = ashare.get_kline_em
        orig_sina = ashare.get_kline_sina
        rows = [{"date": f"2026-01-{i + 1:02d}", "close": 10.0 + i * 0.01} for i in range(80)]

        def fake_em(code, period="daily", adjust="qfq"):
            calls["em"] += 1
            raise ConnectionError("eastmoney blocked")

        def fake_sina(code, period="daily", adjust="qfq"):
            calls["sina"] += 1
            return rows

        ashare.get_kline_em = fake_em
        ashare.get_kline_sina = fake_sina
        try:
            sample = [{"code": f"6000{i:02d}", "board": "x"} for i in range(20)]
            started = time.time()
            result = sector_provider._constituent_correlation(sample)  # noqa: SLF001
            cost = time.time() - started
        finally:
            ashare.get_kline_em = orig_em
            ashare.get_kline_sina = orig_sina
        ok = (calls["em"] == 20 and calls["sina"] <= 8 and cost < 3.0
              and result is not None and result["samples"] >= 3)
        return ok, (f"东财尝试 {calls['em']} 次 / 新浪补 {calls['sina']} 次（上限 8）/ "
                    f"耗时 {cost * 1000:.0f}ms / samples={result and result['samples']}")

    def _two_phase_em_only():
        """东财全成功时不应触发任何新浪调用（避免无谓 V8 串行）。"""
        calls = {"sina": 0}
        orig_em = ashare.get_kline_em
        orig_sina = ashare.get_kline_sina
        rows = [{"date": f"2026-01-{i + 1:02d}", "close": 10.0 + i * 0.01} for i in range(80)]

        def fake_em(code, period="daily", adjust="qfq"):
            return rows

        def fake_sina(code, period="daily", adjust="qfq"):
            calls["sina"] += 1
            return rows

        ashare.get_kline_em = fake_em
        ashare.get_kline_sina = fake_sina
        try:
            sample = [{"code": f"6001{i:02d}", "board": "x"} for i in range(20)]
            result = sector_provider._constituent_correlation(sample)  # noqa: SLF001
        finally:
            ashare.get_kline_em = orig_em
            ashare.get_kline_sina = orig_sina
        ok = calls["sina"] == 0 and result is not None and result["samples"] == 20
        return ok, f"新浪调用 {calls['sina']} 次（应为 0）/ samples={result and result['samples']}"

    check("东财全挂 → 新浪限量补 ≤8 只且总耗时 <3s", _two_phase_sina_cap, section="修复B6")
    check("东财全成功 → 零新浪调用", _two_phase_em_only, section="功能F3")

    # ------------------------------------------ S8 F6/B7 排行键去 limit 与切片
    print("\n[S8] F6/B7 排行缓存键去 limit + 切片 + 合流")

    check("RANKINGS_CACHE_LIMIT == 120",
          lambda: (sector_router.RANKINGS_CACHE_LIMIT == 120,
                   str(sector_router.RANKINGS_CACHE_LIMIT)), section="功能F6")
    check("RANKINGS_WAIT_TIMEOUT industry=60 concept=180",
          lambda: (sector_router.RANKINGS_WAIT_TIMEOUT == {"industry": 60.0, "concept": 180.0},
                   str(sector_router.RANKINGS_WAIT_TIMEOUT)), section="功能F6")

    def _slice_basic():
        payload = {"items": [{"name": f"b{i}"} for i in range(120)],
                   "scanned": 90, "total": 90, "failed": 0, "level_counts": {"高": 3}}
        out = sector_router._slice_rankings(payload, 10)  # noqa: SLF001
        untouched = len(payload["items"]) == 120
        ok = (len(out["items"]) == 10 and untouched
              and out["scanned"] == 90 and out["total"] == 90
              and out["level_counts"] == {"高": 3})
        return ok, f"limit=10 → {len(out['items'])} 条，原 payload 保留 {len(payload['items'])} 条"

    def _slice_clamp():
        payload = {"items": [{"name": f"b{i}"} for i in range(120)]}
        out = sector_router._slice_rankings(payload, 999)  # noqa: SLF001
        return len(out["items"]) == 120, f"limit=999 → 夹到 {len(out['items'])} 条"

    def _slice_short():
        payload = {"items": [{"name": "a"}, {"name": "b"}]}
        out = sector_router._slice_rankings(payload, 50)  # noqa: SLF001
        return out is payload, "缓存条数不足 limit 时原样返回（不复制）"

    def _key_without_limit():
        src = (ROOT / "backend/app/routers/sector.py").read_text(encoding="utf-8")
        bad = 'sector:crowding:rankings:{key_kind}:{limit}' in src
        good = 'f"sector:crowding:rankings:{key_kind}"' in src
        ctx = (ROOT / "backend/app/datasource/page_context.py").read_text(encoding="utf-8")
        ctx_old = any(f"sector:crowding:rankings:industry:{n}" in ctx
                      for n in ("50", "20", "30", "100"))
        ctx_new = '"sector:crowding:rankings:industry",' in ctx
        return (good and not bad and ctx_new and not ctx_old,
                f"路由键={good} 旧带limit={bad} page_context新键={ctx_new} 旧键残留={ctx_old}")

    def _bg_prewarm_wiring():
        src = (ROOT / "backend/app/main.py").read_text(encoding="utf-8")
        uses_bg = "start_rankings_bg(" in src
        waits = "rankings_in_flight(" in src
        warm_once = "warm_crowding_once(" in src
        cap_fill = "warm_board_caps(" in src
        direct = "get_crowding_rankings(kind=" in src
        return (uses_bg and waits and warm_once and cap_fill and not direct,
                f"start_rankings_bg={uses_bg} 合流等待={waits} warm_crowding_once={warm_once} "
                f"warm_board_caps={cap_fill} 仍直接调用={direct}")

    check("_slice_rankings 按 limit 切片且不改全量口径", _slice_basic, section="功能F6")
    check("_slice_rankings limit 上限夹到 120", _slice_clamp, section="功能F6")
    check("_slice_rankings 缓存不足时原样返回", _slice_short, section="功能F6")
    check("缓存键不含 limit；page_context 已同步新键", _key_without_limit, section="修复B7")
    check("预热改走 start_rankings_bg + 合流等待 + 两处新增预热",
          _bg_prewarm_wiring, section="功能F5")

    # ------------------------------------------------------------ 汇总
    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["passed"])
    failed = [r for r in RESULTS if not r["passed"]]
    print("\n" + "=" * 78)
    print(f"合计 {passed}/{total} 通过" + (f"，失败 {len(failed)} 项" if failed else "，全部通过"))
    if failed:
        for item in failed:
            print(f"  FAIL [{item['section']}] {item['name']} — {item['detail']}")
    print("=" * 78)

    out_dir = ROOT / "tools" / "_out"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "regress_v372.json").write_text(
        json.dumps({
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "version": config.APP_VERSION,
            "build": config.BUILD_TAG,
            "total": total,
            "passed": passed,
            "failed": len(failed),
            "items": RESULTS,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"明细已写入 {out_dir / 'regress_v372.json'}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
