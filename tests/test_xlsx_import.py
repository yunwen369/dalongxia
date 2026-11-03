# -*- coding: utf-8 -*-
"""M11 xlsx 一次性导入单元测试：列映射 / 校验 / 二次确认 / 写入台账。

全部离线：xlsx 在内存生成（openpyxl），行情查询 monkeypatch，
台账文件由 conftest 隔离到 tmp_path（§11.11 测试隔离）。
"""

from __future__ import annotations

import io

import pytest
from openpyxl import Workbook

from backend.app.core.errors import NotFoundError
from backend.app.datasource import portfolio, tencent, xlsx_import


def _make_xlsx(rows, headers=("代码", "名称", "数量", "成本", "市值")) -> bytes:
    """在内存生成一个 xlsx，返回字节。

    Args:
        rows: 数据行（list of list）。
        headers: 表头。

    Returns:
        bytes: 文件字节。
    """
    wb = Workbook()
    ws = wb.active
    ws.append(list(headers))
    for row in rows:
        ws.append(list(row))
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


@pytest.fixture()
def fake_quote(monkeypatch):
    """把腾讯报价替换为固定价（离线）。

    Returns:
        dict: {symbol: price}。
    """
    prices = {"600519": 1400.0, "000001": 10.0, "000333": 80.0}

    def _get_quote(symbol, market="a"):
        """假报价（未知代码抛 NotFoundError，模拟真实行为）。

        Args:
            symbol: 代码。
            market: 市场。

        Returns:
            dict: {price: ...}
        """
        code = str(symbol)
        if code not in prices:
            raise NotFoundError("腾讯返回空体或无有效价格")
        return {"price": prices[code], "symbol": code, "market": market}

    monkeypatch.setattr(tencent, "get_quote", _get_quote)
    return prices


def test_parse_maps_columns_and_checks_market_value(fake_quote):
    """列映射正确、市值比对差 >1 元时报警。"""
    raw = _make_xlsx([
        ["600519", "贵州茅台", 100, 1500, 140000],
        ["000001", "平安银行", 1000, 10, 10000],
    ])
    result = xlsx_import.parse(raw, "持仓.xlsx")
    assert result["mapped"]["symbol"] == "代码"
    assert result["mapped"]["shares"] == "数量"
    assert result["summary"]["total"] == 2
    assert result["summary"]["importable"] == 2
    # 表格市值 150000 vs 按现价 100×1400+1000×10 = 150000 → 无差
    assert not any("合计市值与表格原值差" in item for item in result["warnings"])


def test_parse_rejects_unknown_symbol(fake_quote):
    """行情查不到的代码标红不导入。"""
    raw = _make_xlsx([["999999", "不存在", 100, 1, 100]])
    result = xlsx_import.parse(raw, "持仓.xlsx")
    row = result["rows"][0]
    assert row["ok"] is False
    assert any("行情接口查不到" in item for item in row["errors"])
    assert result["summary"]["importable"] == 0
    assert "999999" in result["errors"][0]


def test_parse_missing_required_column():
    """缺必需列（代码/数量/成本）时抛 ValueError 并点名。"""
    raw = _make_xlsx([["600519", 100]], headers=("名称", "数量"))
    with pytest.raises(ValueError) as err:
        xlsx_import.parse(raw, "持仓.xlsx")
    assert "代码" in str(err.value)


def test_parse_rejects_bad_file_type():
    """非 xlsx/xls 后缀直接拒绝。"""
    with pytest.raises(ValueError) as err:
        xlsx_import.parse(b"abc", "持仓.csv")
    assert "仅支持 .xlsx 或 .xls" in str(err.value)


def test_parse_invalid_numbers(fake_quote):
    """数量/成本非数值或为负 → 行内报错。"""
    raw = _make_xlsx([
        ["600519", "茅台", "abc", 1500, 140000],
        ["000001", "平安", -5, 10, 100],
    ])
    result = xlsx_import.parse(raw, "持仓.xlsx")
    assert result["rows"][0]["ok"] is False
    assert "数量不是数值" in result["rows"][0]["errors"][0]
    assert result["rows"][1]["ok"] is False
    assert any("数量必须大于 0" in item for item in result["rows"][1]["errors"])


def test_commit_requires_confirm(fake_quote):
    """未显式 confirm 一律拒绝（覆盖台账属不可逆操作）。"""
    raw = _make_xlsx([["600519", "茅台", 100, 1500, 140000]])
    preview = xlsx_import.parse(raw, "持仓.xlsx")
    with pytest.raises(ValueError) as err:
        xlsx_import.commit(preview["rows"], confirm=False)
    assert "二次确认" in str(err.value)


def test_commit_writes_ledger(fake_quote):
    """confirm=true 时写入台账，只写 ok 行。"""
    raw = _make_xlsx([
        ["600519", "贵州茅台", 100, 1500, 140000],
        ["999999", "不存在", 100, 1, 100],
    ])
    preview = xlsx_import.parse(raw, "持仓.xlsx")
    result = xlsx_import.commit(preview["rows"], confirm=True)
    assert result["imported"] == 1
    assert result["skipped"] == 1
    store = portfolio.load()
    assert len(store["items"]) == 1
    assert store["items"][0]["symbol"] == "600519"
    assert store["items"][0]["note"] == "xlsx 导入"


def test_commit_empty_rows_raises(fake_quote):
    """没有可导入行时拒绝写库。"""
    with pytest.raises(ValueError) as err:
        xlsx_import.commit([], confirm=True)
    assert "没有可导入的行" in str(err.value)


def test_infer_market_variants():
    """市场推断：类型列优先，其后代码形态。"""
    assert xlsx_import._infer_market("600519", "")[0] == "a"
    assert xlsx_import._infer_market("000001", "股票")[0] == "a"
    assert xlsx_import._infer_market("017811", "基金")[0] == "fund"
    assert xlsx_import._infer_market("017811", "")[0] == "fund"
    assert xlsx_import._infer_market("00700", "")[0] == "hk"
    assert xlsx_import._infer_market("AAPL", "")[0] == "us"
    assert xlsx_import._infer_market("123456", "")[0] == "fund"
    # 777777 既不在 A 股前缀也不在基金前缀 → 按 A 股处理并给出提示（不静默）
    market, hint = xlsx_import._infer_market("777777", "")
    assert market == "a"
    assert hint and "无法确定" in hint


def test_parse_handles_xls_extension_rejection():
    """损坏的 xls 字节 → ValueError（不冒泡为 500）。"""
    with pytest.raises(ValueError):
        xlsx_import.parse(b"not-an-excel-file", "持仓.xls")
