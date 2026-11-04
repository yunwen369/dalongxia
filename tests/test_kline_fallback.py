"""A股 K 线三源降级链单测（D1/D2 修复回归）。

背景：2026-11-04 实测腾讯 A 股 ``fqkline`` 返回 HTTP 501 反爬页，
直连导致个股面板 K 线块与组合风险同时失效。修复方案是收口到
:func:`backend.app.datasource.ashare.get_kline`（东财 → 新浪 → 腾讯）。

覆盖：
1. 东财成功即返回，不触碰后续源；
2. 东财失败 → 落新浪；
3. 东财/新浪均失败 → 落腾讯；
4. 三源全失败 → RuntimeError 且带各源原因；
5. 个股面板对 A 股走统一入口（不再直连腾讯）。
"""

from __future__ import annotations

import pytest

from backend.app.datasource import ashare, stock


def _rows(n: int = 3) -> list[dict]:
    """构造最小 K 线样本。

    Args:
        n: 条数。

    Returns:
        list[dict]: 日期升序的 K 线记录。
    """
    return [{"date": f"2026-11-0{i + 1}", "open": 1.0, "close": 2.0, "high": 3.0,
             "low": 0.5, "volume": 100.0, "amount": 1000.0, "change": None, "pct": None} for i in range(n)]


class TestKlineFallback:
    """三源降级顺序与异常收敛。"""

    def test_primary_hit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """东财成功即返回，不再调用备源。"""
        calls: list[str] = []
        monkeypatch.setattr(ashare, "get_kline_em", lambda *a, **k: calls.append("em") or _rows(5))
        monkeypatch.setattr(ashare, "get_kline_sina", lambda *a, **k: calls.append("sina") or _rows(2))
        monkeypatch.setattr(ashare.tencent, "get_kline", lambda *a, **k: calls.append("tencent") or _rows(1))

        rows = ashare.get_kline("600519", "daily", "qfq", 3)
        assert len(rows) == 3
        assert calls == ["em"]

    def test_fallback_to_sina(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """东财失败后落新浪。"""

        def boom(*a, **k):
            raise RuntimeError("em down")

        monkeypatch.setattr(ashare, "get_kline_em", boom)
        monkeypatch.setattr(ashare, "get_kline_sina", lambda *a, **k: _rows(4))
        rows = ashare.get_kline("600519", "daily", "qfq", 10)
        assert len(rows) == 4

    def test_fallback_to_tencent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """东财与新浪都失败后落腾讯。"""

        def boom(*a, **k):
            raise RuntimeError("down")

        monkeypatch.setattr(ashare, "get_kline_em", boom)
        monkeypatch.setattr(ashare, "get_kline_sina", boom)
        monkeypatch.setattr(ashare.tencent, "get_kline", lambda *a, **k: _rows(6))
        rows = ashare.get_kline("600519", "daily", "qfq", 20)
        assert len(rows) == 6

    def test_all_failed_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """三源全失败抛 RuntimeError，信息含各源原因。"""

        def boom(*a, **k):
            raise RuntimeError("down")

        monkeypatch.setattr(ashare, "get_kline_em", boom)
        monkeypatch.setattr(ashare, "get_kline_sina", boom)
        monkeypatch.setattr(ashare.tencent, "get_kline", boom)
        with pytest.raises(RuntimeError) as exc:
            ashare.get_kline("600519")
        assert "三源均失败" in str(exc.value)
        assert "东财" in str(exc.value)


class TestRealtimePanelKline:
    """个股面板 A 股 K 线块必须走统一入口。"""

    def test_panel_uses_fallback_entry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """面板对 A 股调用 ashare.get_kline；腾讯 K 线不再被直接调用。"""
        called = {"ashare": 0, "tencent": 0}
        monkeypatch.setattr(ashare, "get_kline",
                            lambda *a, **k: called.__setitem__("ashare", called["ashare"] + 1) or _rows(3))
        monkeypatch.setattr(stock.tencent, "get_kline",
                            lambda *a, **k: called.__setitem__("tencent", called["tencent"] + 1) or _rows(1))
        monkeypatch.setattr(stock.tencent, "get_quote", lambda *a, **k: {"price": 1.0})
        monkeypatch.setattr(stock.tencent, "get_minute", lambda *a, **k: [])
        monkeypatch.setattr(stock, "get_fund_dist", lambda *a, **k: None)
        monkeypatch.setattr(stock, "get_news", lambda *a, **k: [])
        monkeypatch.setattr(stock, "get_forecast", lambda *a, **k: None)

        panel = stock.get_realtime_panel("600519", "a")
        assert panel["blocks"]["kline"] == "ok"
        assert called["ashare"] == 1
        assert called["tencent"] == 0
