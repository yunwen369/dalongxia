# -*- coding: utf-8 -*-
"""第五轮 P1-01 回归：guard_v8 失败归因分层与熔断冷却半开。

覆盖「假绿」根因：修复前业务性异常（KeyError）被计为 V8 引擎失败，
3 次即进程级永久熔断；现要求：
1. 业务性异常永不触发熔断，其后的正常调用仍真实执行；
2. 引擎类异常按时间窗累计，达阈值熔断；
3. 冷却期内直接拒绝（不执行 func），冷却后半开放行，成功即清零；
4. 半开试探失败立即重新熔断；
5. 窗口过期后旧失败不再累计；
6. /api/health 必须暴露 v8 健康字段。

这些断言全部基于真实函数执行（非 mock 返回值），与 ``tools/_ad_hoc/repro_v8_circuit_v2.py``
的真实 HTTP 复现互为两层证据。
"""

from __future__ import annotations

from typing import Any, Callable

import pytest

from backend.app import config
from backend.app.core import v8_guard
from backend.app.core.errors import NonRetryableError


class FakeClock:
    """可控时钟：替换 ``v8_guard._NOW``，让冷却/窗口逻辑可确定性地推进。"""

    def __init__(self, start: float = 1_000_000.0) -> None:
        """初始化固定起点。

        Args:
            start: 起始时间戳（秒）。
        """
        self.now = start

    def __call__(self) -> float:
        """返回当前（可控）时间戳。

        Returns:
            float: 当前时间戳。
        """
        return self.now

    def advance(self, seconds: float) -> None:
        """把时钟向前推进指定秒数。

        Args:
            seconds: 推进秒数。

        Returns:
            None.
        """
        self.now += seconds


def _raiser(exc: BaseException) -> Callable[[], Any]:
    """构造一个必定抛出指定异常的零参函数。

    Args:
        exc: 要抛出的异常实例。

    Returns:
        Callable[[], Any]: 调用即抛异常的函数。
    """

    def _fn() -> Any:
        """抛出预置异常。

        Raises:
            BaseException: 传入的异常实例。

        Returns:
            Any: 正常路径不会返回。
        """
        raise exc

    return _fn


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """注入可控时钟并保证 V8 状态在用例前后干净。

    Args:
        monkeypatch: pytest 补丁器。

    Yields:
        FakeClock: 可控时钟实例。
    """
    fake = FakeClock()
    monkeypatch.setattr(v8_guard, "_NOW", fake)
    monkeypatch.setattr(config, "V8_SOURCES_ENABLED", True)
    v8_guard.reset_state()
    yield fake
    v8_guard.reset_state()


def test_business_errors_never_trip_circuit(clock: FakeClock) -> None:
    """业务性异常（板块名不存在）连发 5 次，也不得触发熔断。

    Args:
        clock: 可控时钟。

    Returns:
        None.
    """
    for _ in range(5):
        with pytest.raises(KeyError):
            v8_guard.guard_v8(_raiser(KeyError("板块不存在")))()
    health = v8_guard.v8_health()
    assert health["fail_streak"] == 0
    assert health["circuit_open"] is False
    # 关键：业务异常之后，正常调用必须仍然真实执行（修复前这里会 NonRetryableError）
    assert v8_guard.guard_v8(lambda: 42)() == 42


def test_value_error_also_not_counted(clock: FakeClock) -> None:
    """ValueError 同属业务性异常，不得计数。

    Args:
        clock: 可控时钟。

    Returns:
        None.
    """
    for _ in range(4):
        with pytest.raises(ValueError):
            v8_guard.guard_v8(_raiser(ValueError("数据解析不符")))()
    assert v8_guard.v8_health()["fail_streak"] == 0


def test_engine_errors_trip_then_half_open_recovers(clock: FakeClock) -> None:
    """引擎异常 3 次熔断 → 冷却期拒绝 → 冷却后半开成功恢复。

    Args:
        clock: 可控时钟。

    Returns:
        None.
    """
    for _ in range(v8_guard._V8_MAX_FAILS):  # noqa: SLF001 - 断言阈值行为需读常量
        with pytest.raises(RuntimeError):
            v8_guard.guard_v8(_raiser(RuntimeError("v8 engine boom")))()
    health = v8_guard.v8_health()
    assert health["fail_streak"] == v8_guard._V8_MAX_FAILS  # noqa: SLF001
    assert health["circuit_open"] is True

    calls = {"n": 0}

    def _ok() -> str:
        """返回成功的探针函数。

        Returns:
            str: 固定成功值。
        """
        calls["n"] += 1
        return "ok"

    with pytest.raises(NonRetryableError):
        v8_guard.guard_v8(_ok)()
    assert calls["n"] == 0, "冷却期内不得执行真实调用"

    clock.advance(v8_guard._V8_COOLDOWN + 1)  # noqa: SLF001
    assert v8_guard.guard_v8(_ok)() == "ok"
    recovered = v8_guard.v8_health()
    assert recovered["fail_streak"] == 0
    assert recovered["circuit_open"] is False


def test_half_open_failure_retrips_immediately(clock: FakeClock) -> None:
    """半开试探失败必须立即重新熔断，不得再等 3 次。

    Args:
        clock: 可控时钟。

    Returns:
        None.
    """
    for _ in range(v8_guard._V8_MAX_FAILS):  # noqa: SLF001
        with pytest.raises(RuntimeError):
            v8_guard.guard_v8(_raiser(RuntimeError("boom")))()
    clock.advance(v8_guard._V8_COOLDOWN + 1)  # noqa: SLF001
    with pytest.raises(RuntimeError):
        v8_guard.guard_v8(_raiser(RuntimeError("still bad")))()
    assert v8_guard.v8_health()["circuit_open"] is True


def test_fail_window_expiry_resets_streak(clock: FakeClock) -> None:
    """窗口过期后旧失败不再累计（避免「一天前的两次失败」+今天一次就熔断）。

    Args:
        clock: 可控时钟。

    Returns:
        None.
    """
    with pytest.raises(RuntimeError):
        v8_guard.guard_v8(_raiser(RuntimeError("old")))()
    clock.advance(v8_guard._V8_FAIL_WINDOW + 1)  # noqa: SLF001
    with pytest.raises(RuntimeError):
        v8_guard.guard_v8(_raiser(RuntimeError("new")))()
    assert v8_guard.v8_health()["fail_streak"] == 1


def test_frozen_mode_rejects_without_calling(clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    """冻结模式（V8 源禁用）必须直接拒绝且不执行原函数。

    Args:
        clock: 可控时钟。
        monkeypatch: pytest 补丁器。

    Returns:
        None.
    """
    monkeypatch.setattr(config, "V8_SOURCES_ENABLED", False)
    calls = {"n": 0}

    def _fn() -> int:
        """被冻结模式拦截的函数。

        Returns:
            int: 正常路径返回值（不应发生）。
        """
        calls["n"] += 1
        return 1

    with pytest.raises(NonRetryableError):
        v8_guard.guard_v8(_fn)()
    assert calls["n"] == 0


def test_health_endpoint_exposes_v8(clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    """/api/health 必须暴露 v8 状态字段（修复前 v8_health 无调用方）。

    Args:
        clock: 可控时钟。
        monkeypatch: pytest 补丁器。

    Returns:
        None.
    """
    from fastapi.testclient import TestClient

    from backend.app.main import app
    from backend.app.routers import system

    # 避免真实网络探测拖慢用例：数据源状态在别的用例覆盖
    monkeypatch.setattr(system, "_datasource_status", lambda: {})
    client = TestClient(app)
    body = client.get("/api/health").json()
    assert "v8" in body["data"]
    v8 = body["data"]["v8"]
    assert v8["enabled"] is True
    assert v8["circuit_open"] is False
    assert v8["max_fails"] == 3
