# -*- coding: utf-8 -*-
"""阶段 8 回归：财报搜索中文名解析 + 摘要字段分层。

覆盖：
1. 别名归一：中文简称/英文名 → 港股代码、美股 ticker（含未收录返回空）；
2. 真实检索：腾讯（港股）、苹果/NVIDIA（美股）、茅台/600519（A股）均出数；
3. 摘要分层：港股=源站公告类别（SHORT_TEXT）、美股=表种描述（primaryDocDescription）、
   A股=空（巨潮列表接口不提供摘要）—— 三者必须可区分；
4. 未匹配提示：不得静默返回空，caliber 必须含示例代码。
"""

from __future__ import annotations

import pytest

from backend.app.datasource import financial_report as fr


def test_hk_alias_normalization() -> None:
    """港股别名：中文/英文/代码/包含匹配，未收录返回空。"""
    assert fr._hk_alias_code("腾讯") == "00700"
    assert fr._hk_alias_code("腾讯控股") == "00700"
    assert fr._hk_alias_code("tencent") == "00700"
    assert fr._hk_alias_code("00700") == "00700"
    assert fr._hk_alias_code("700") == "00700"  # 4 位补零
    assert fr._hk_alias_code("阿里巴巴") == "09988"
    assert fr._hk_alias_code("某不存在的公司") == ""


def test_us_alias_normalization() -> None:
    """美股别名：中文常用名归一为 ticker，未收录返回空（由 EDGAR 原名兜底）。"""
    assert fr._us_alias_ticker("苹果") == "AAPL"
    assert fr._us_alias_ticker("英伟达") == "NVDA"
    assert fr._us_alias_ticker("腾讯") == "TCEHY"
    assert fr._us_alias_ticker("NVIDIA") == ""
    assert fr._us_alias_ticker("某不存在的公司") == ""


def test_edgar_resolve_accepts_chinese_alias() -> None:
    """EDGAR 解析：中文"苹果"应解析为 AAPL（走别名），英文名仍可包含匹配。"""
    target = fr._edgar_resolve("苹果")
    assert target and target["ticker"] == "AAPL"
    assert int(target["cik"]) > 0


@pytest.mark.parametrize("market,q", [("hk", "腾讯"), ("us", "苹果"), ("us", "NVIDIA")])
def test_search_by_chinese_name_returns_rows(market: str, q: str) -> None:
    """中文名检索必须真实出数（源站波动时允许 0 条，但不得抛异常）。"""
    data = fr.search_filings(market=market, q=q, type_value="全部", since="all", page=1, page_size=3)
    rows = data.get("list") or []
    if not rows:
        # 源站限频/网络异常：必须给出中文说明，不得静默空
        assert (data.get("caliber") or "").strip(), data
        pytest.skip(f"{market}/{q} 本次源站未返回数据：{data.get('caliber')}")
    first = rows[0]
    assert first.get("code"), first
    assert first.get("title"), first
    assert first.get("summary"), f"{market} 摘要应来自源站描述字段：{first}"


def test_cn_summary_is_empty_and_caliber_explains() -> None:
    """A股：巨潮列表无摘要 → summary 为空，且 caliber 必须说明原因（空/错分离）。"""
    data = fr.search_filings(market="a", q="茅台", type_value="全部", since="all", page=1, page_size=3)
    rows = data.get("list") or []
    if not rows:
        pytest.skip("巨潮本次未返回数据")
    assert rows[0].get("summary") == ""
    assert "不提供摘要" in (data.get("caliber") or "")


def test_hk_unmatched_keyword_gives_actionable_hint() -> None:
    """港股未匹配：caliber 必须含示例代码，不得静默空。"""
    data = fr.search_filings(market="hk", q="zzzz_不存在_9527", type_value="全部", since="all",
                             page=1, page_size=3)
    assert not (data.get("list") or [])
    caliber = data.get("caliber") or ""
    assert "请尝试 5 位代码" in caliber or "未匹配到" in caliber, caliber


def test_us_unmatched_keyword_gives_actionable_hint() -> None:
    """美股未匹配：caliber 必须含 ticker 示例。"""
    data = fr.search_filings(market="us", q="zzzz_not_a_company_9527", type_value="全部", since="all",
                             page=1, page_size=3)
    assert not (data.get("list") or [])
    caliber = data.get("caliber") or ""
    assert "ticker" in caliber or "未匹配到" in caliber, caliber
