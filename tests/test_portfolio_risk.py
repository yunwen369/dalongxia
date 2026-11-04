# -*- coding: utf-8 -*-
"""M10 组合风险指标单元测试：样本门槛 / 回撤 / β-α 口径 / 空台账语义。

全部离线：行情与基准 monkeypatch 为构造序列，台账由 conftest 隔离到 tmp_path。
"""

from __future__ import annotations


import pytest

from backend.app.datasource import ashare, portfolio, portfolio_risk, tencent


@pytest.fixture(autouse=True)
def offline_bond(monkeypatch):
    """无风险利率固定 2%（不触 macro 实网）。"""
    monkeypatch.setattr(portfolio_risk, "_bond_rate", lambda: (2.0, "测试回退"))


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    """预置两条持仓（A 股），并把台账文件指向 tmp。"""
    path = tmp_path / "data" / "portfolio.json"
    monkeypatch.setattr(portfolio, "PORTFOLIO_FILE", path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        __import__("json").dumps({
            "items": [
                {"id": "a1", "market": "a", "symbol": "600519", "name": "贵州茅台",
                 "shares": 100, "cost": 1500, "created_at": "2026-08-01 00:00:00"},
                {"id": "a2", "market": "a", "symbol": "000001", "name": "平安银行",
                 "shares": 1000, "cost": 10, "created_at": "2026-08-01 00:00:00"},
            ],
            "updated_at": "2026-10-31 00:00:00",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _series(n: int, base: float, step: float, start_day: int = 1, wave: float = 0.0):
    """构造日线（date/close）；wave>0 时叠加交替涨跌制造真实波动。

    Args:
        n: 条数。
        base: 起始价。
        step: 每日基础涨幅（小数）。
        start_day: 起始日序号（用于生成日期）。
        wave: 交替波动幅度（小数）；0 表示单调序列（零方差）。

    Returns:
        list[dict]: [{date, close}]
    """
    rows = []
    price = base
    for i in range(n):
        rows.append({"date": f"2026-{1 + (start_day + i) // 28:02d}-{1 + (start_day + i) % 28:02d}",
                     "close": round(price, 4)})
        daily = step + (wave if i % 2 == 0 else -wave)
        price *= (1 + daily)
    return rows


def test_empty_ledger_not_available(tmp_path, monkeypatch):
    """空台账 → available=false + 中文引导。"""
    path = tmp_path / "data" / "portfolio.json"
    monkeypatch.setattr(portfolio, "PORTFOLIO_FILE", path)
    data = portfolio_risk.risk_report(force=True)
    assert data["available"] is False
    assert "台账为空" in data["caliber"]


def test_single_holding_not_available(ledger, monkeypatch):
    """只有 1 只有效持仓 → 无法算组合风险。"""
    from backend.app.core.errors import NotFoundError

    def _quote(symbol, market="a"):
        """000001 报价失败，只剩茅台一只。"""
        if symbol == "000001":
            raise NotFoundError("无有效价格")
        return {"price": 1400.0}

    monkeypatch.setattr(tencent, "get_quote", _quote)
    data = portfolio_risk.risk_report(force=True)
    assert data["available"] is False
    assert any("少于 2 只" in item for item in data["missing"])


def test_sample_too_short_not_reliable(ledger, monkeypatch):
    """日线不足 30 条 → reliable=false（不给结论标签）。"""
    monkeypatch.setattr(tencent, "get_quote", lambda symbol, market="a": {"price": 100.0})
    monkeypatch.setattr(tencent, "get_kline", lambda *a, **k: _series(10, 100.0, 0.001))
    monkeypatch.setattr(ashare, "get_kline", lambda *a, **k: _series(10, 100.0, 0.001))
    monkeypatch.setattr(tencent, "get_index_kline", lambda *a, **k: _series(10, 4000.0, 0.001))
    data = portfolio_risk.risk_report(force=True)
    # 两只都只有 10 条 → 全部进 missing，可用序列 <2 → available=false
    assert data["available"] is False
    assert any("样本不足" in item for item in data["missing"])


def test_risk_metrics_with_synthetic_series(ledger, monkeypatch):
    """构造「组合与基准完全同步」的序列：β≈1、R²≈1、相关性≈1。"""
    monkeypatch.setattr(tencent, "get_quote", lambda symbol, market="a": {"price": 100.0})
    # 两只股票同比例波动（与基准同步）
    monkeypatch.setattr(tencent, "get_kline", lambda *a, **k: _series(120, 100.0, 0.002, wave=0.01))
    monkeypatch.setattr(ashare, "get_kline", lambda *a, **k: _series(120, 100.0, 0.002, wave=0.01))
    monkeypatch.setattr(tencent, "get_index_kline", lambda *a, **k: _series(120, 4000.0, 0.002, wave=0.01))
    data = portfolio_risk.risk_report(force=True)
    assert data["available"] is True
    assert data["reliable"] is True
    metrics = data["metrics"]
    assert metrics["beta"] == pytest.approx(1.0, abs=0.05)
    assert metrics["r2"] == pytest.approx(1.0, abs=0.05)
    # 两条序列完全相同 → 相关性 1.0
    pairs = data["correlation"]
    assert pairs and pairs[0]["value"] == pytest.approx(1.0, abs=0.05)
    assert pairs[0]["high"] is True


def test_max_drawdown_helper():
    """回撤算法：峰谷差与持续天数。"""
    closes = [1.0, 1.2, 0.9, 1.1]
    drawdown, _, days = portfolio_risk._max_drawdown(closes)
    assert drawdown == pytest.approx(-25.0, abs=0.01)  # 1.2 → 0.9
    assert days == 1


def test_returns_and_stats_helpers():
    """日收益与标准差基本性质。"""
    assert portfolio_risk._returns([100.0, 110.0]) == [pytest.approx(0.1)]
    assert portfolio_risk._std([1.0, 1.0, 1.0]) == 0.0
    assert portfolio_risk._mean([1.0, 3.0]) == 2.0
    # 空序列不炸
    assert portfolio_risk._returns([]) == []
    assert portfolio_risk._max_drawdown([]) == (None, None, None)


def test_benchmark_missing_not_available(ledger, monkeypatch):
    """基准取不到 → 不可算 β/α，返回 available=false 并说明。"""
    monkeypatch.setattr(tencent, "get_quote", lambda symbol, market="a": {"price": 100.0})
    monkeypatch.setattr(tencent, "get_kline", lambda *a, **k: _series(60, 100.0, 0.001))

    def _boom(*args, **kwargs):
        raise RuntimeError("指数通道异常")

    monkeypatch.setattr(tencent, "get_index_kline", _boom)
    data = portfolio_risk.risk_report(force=True)
    assert data["available"] is False
    assert any("基准" in item for item in data["missing"])
