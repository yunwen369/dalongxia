# -*- coding: utf-8 -*-
"""阶段 11/12：公告正文详情（详情弹窗按需拉取，A 股 / 港股 / 美股）回归。

覆盖：
1. 参数校验：title/doc_url 双空 → INVALID_PARAM；doc_url 域名白名单外 → INVALID_PARAM；
   market 非法 → INVALID_PARAM；cn 为兼容别名；
2. A 股东财直链：docUrl 含 art_code → 单源正文（verified=False）；
3. A 股巨潮来源交叉验证：翻页命中「标题全等 + 公告日一致」→ verified=True；
4. 深翻页：目标公告位于第 3 页仍能命中（阶段 12「不得降级」核心，替代原 50 条小窗口）；
5. 兜底链：标题不全等时用「正文 ⊇ 标题」定位；同日唯一候选直接采用；往期同名公告拒绝；
6. 港股：ann_type=H 定位 + 繁简归一标题校验 → verified=True；
7. 美股：EDGAR 归档 HTML 抓取文本化 → verified=True；
8. 正文分页拼接（每页 5000 字，page_index 递增内容不同）；
9. 空/错分离：无正文 → 成功信封 + 空 content + 中文口径；通道异常 → UPSTREAM_ERROR；
10. 缓存命中与超长节选。

上游全部 mock，不打真实源站（真实链路验收由 tools/_ad_hoc 探针 + 起服实测承担）。
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.datasource import financial_report as fr

client = TestClient(app)


def _resp(payload: Dict[str, Any], status_code: int = 200, text: str = "") -> Any:
    """构造 requests.Response 的最小替身。"""
    class _R:
        def __init__(self) -> None:
            self.status_code = status_code
            self.text = text

        def json(self) -> Dict[str, Any]:
            return payload
    return _R()


def _row(title: str, art: str, pub: str, code: str = "600519") -> Dict[str, str]:
    """构造一行东财公告列表记录。"""
    return {
        "title": title,
        "company": "贵州茅台",
        "code": code,
        "pubTime": pub,
        "docUrl": f"https://data.eastmoney.com/notices/detail/{art}.html",
        "art": art,
    }


# ---------------------------------------------------------------
# 一、路由参数校验（不发起上游请求）
# ---------------------------------------------------------------


def test_detail_requires_title_or_doc_url() -> None:
    """title 与 doc_url 双空必须 INVALID_PARAM，不得放行。"""
    body = client.get("/api/news/financial-detail", params={}).json()
    assert body.get("error_code") == "INVALID_PARAM"
    assert body.get("error"), body


def test_detail_rejects_unknown_doc_domain() -> None:
    """doc_url 非已知公告源域必须 INVALID_PARAM（安全约束）。"""
    body = client.get("/api/news/financial-detail", params={
        "title": "任意", "doc_url": "https://evil.example.com/a.pdf",
    }).json()
    assert body.get("error_code") == "INVALID_PARAM"
    assert "已拒绝" in body.get("error", ""), body


def test_detail_rejects_bad_market() -> None:
    """market 非法值必须 INVALID_PARAM。"""
    body = client.get("/api/news/financial-detail", params={
        "market": "jp", "title": "任意",
    }).json()
    assert body.get("error_code") == "INVALID_PARAM"


def test_detail_accepts_cn_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """market=cn 为兼容别名，应归一为 a 并走 A 股取数链。"""
    monkeypatch.setattr(fr, "_em_page", lambda **kw: ([], 0))
    body = client.get("/api/news/financial-detail", params={
        "market": "cn", "title": "不存在的标题XYZ9527",
        "code": "600519", "pub_time": "2026-08-14",
    }).json()
    assert body.get("error_code") in (None, "0"), body
    assert (body.get("data") or {}).get("content") == ""


# ---------------------------------------------------------------
# 二、归一与工具函数
# ---------------------------------------------------------------


def test_norm_title_strips_prefix_and_punct() -> None:
    """归一标题：东财「公司名:」前缀剥离 + 标点/空白剔除后全等。"""
    assert fr._norm_cn_title("贵州茅台:贵州茅台2026年半年度报告") \
        == fr._norm_cn_title("贵州茅台2026年半年度报告")
    assert fr._norm_cn_title("某司:关于会计政策变更的公告。") \
        == fr._norm_cn_title("关于 会计政策变更 的公告")


def test_norm_title_converts_traditional() -> None:
    """繁转简：HKEX 繁体标题与东财简体标题归一后全等。"""
    assert fr._norm_cn_title("根據騰訊控股有限公司最高限額為300億美元之全球中期票據計劃發行") \
        == fr._norm_cn_title("根据腾讯控股有限公司最高限额为300亿美元之全球中期票据计划发行")
    assert fr._norm_cn_title("翌日披露報表") == fr._norm_cn_title("翌日披露报表")


def test_day_diff_allows_one_day_shift() -> None:
    """日期放宽：巨潮整点化跨日允许 ±1，往期公告（>1 天）必须拒绝。"""
    assert fr._day_diff("2026-08-14", "2026-08-15") == 1
    assert fr._day_diff("2026-08-14", "2026-08-20") == 6
    assert fr._day_diff("2026-08-14", "bad-date") is None


def test_art_from_url() -> None:
    """从东财详情链接提取 art_code。"""
    assert fr._art_from_url("https://data.eastmoney.com/notices/detail/AN202608141827994407.html") \
        == "AN202608141827994407"
    assert fr._art_from_url("https://static.cninfo.com.cn/finalpage/x.pdf") == ""


def test_html_to_text() -> None:
    """HTML 文本化：剥脚本/样式/标签，块级断行，实体解码。"""
    html = ("<html><head><style>p{color:red}</style><script>var a=1;</script></head>"
            "<body><h1>Apple Inc.</h1><p>FORM 10-K &amp; notes</p>"
            "<div>Revenue&nbsp;2025</div></body></html>")
    text = fr._html_to_text(html)
    assert "Apple Inc." in text
    assert "FORM 10-K & notes" in text
    assert "Revenue 2025" in text
    assert "color:red" not in text and "var a=1" not in text


# ---------------------------------------------------------------
# 三、A 股取数链
# ---------------------------------------------------------------


def test_em_source_direct_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """东财来源：docUrl 直含 art_code → 拉正文成功，verified=False。"""
    payload = {"data": {"notice_content": "第一段正文。\r\n第二段正文。", "notice_title": "t"}}
    monkeypatch.setattr(fr.netfix, "http_request", lambda *a, **k: _resp(payload))
    out = fr.cn_detail(
        title="贵州茅台2026年半年度报告", code="600519", pub_time="2026-08-14",
        doc_url="https://data.eastmoney.com/notices/detail/AN202608141827994407.html",
    )
    assert out["content"].startswith("第一段正文。")
    assert "\n" in out["content"]  # \r\n 已归一
    assert out["verified"] is False
    assert out["truncated"] is False


def test_cninfo_source_cross_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    """巨潮来源：标题全等 + 公告日一致命中 → verified=True。"""
    monkeypatch.setattr(fr.netfix, "http_request",
                        lambda *a, **k: _resp({"data": {"notice_content": "交叉验证正文"}}))
    rows: List[Dict[str, str]] = [_row("贵州茅台2026年半年度报告", "AN1", "2026-08-14 20:41")]
    with patch.object(fr, "_em_page", return_value=(rows, 1)):
        out = fr.cn_detail(
            title="贵州茅台2026年半年度报告", code="600519", pub_time="2026-08-14",
            doc_url="https://static.cninfo.com.cn/finalpage/2026-08-14/x.PDF",
        )
    assert out["verified"] is True
    assert out["content"] == "交叉验证正文"
    assert "交叉验证" in out["caliber"]


def test_deep_paging_finds_old_announcement(monkeypatch: pytest.MonkeyPatch) -> None:
    """深翻页：目标公告在第 3 页仍能命中（不得降级核心用例）。"""
    monkeypatch.setattr(fr.netfix, "http_request",
                        lambda *a, **k: _resp({"data": {"notice_content": "历史公告正文"}}))
    page1 = [_row(f"无关公告{i}", f"ANP1_{i}", "2026-08-20") for i in range(100)]
    page2 = [_row(f"无关公告B{i}", f"ANP2_{i}", "2025-06-20") for i in range(100)]
    page3 = [_row("贵州茅台2019年年度报告", "ANTARGET", "2019-04-01")] + \
            [_row(f"无关公告C{i}", f"ANP3_{i}", "2019-03-20") for i in range(99)]

    def fake_page(**kwargs: Any) -> Any:
        return {1: (page1, 300), 2: (page2, 300), 3: (page3, 300)}.get(kwargs["page"], ([], 300))

    with patch.object(fr, "_em_page", side_effect=fake_page):
        out = fr.cn_detail(
            title="贵州茅台2019年年度报告", code="600519", pub_time="2019-04-01",
            doc_url="https://static.cninfo.com.cn/finalpage/x.PDF",
        )
    assert out["verified"] is True, out
    assert out["content"] == "历史公告正文"


def test_title_mismatch_falls_back_to_content_contains(monkeypatch: pytest.MonkeyPatch) -> None:
    """标题不全等 → 正文包含标题校验命中。"""
    body = "证券简称：贵州茅台\n关于会计政策变更的公告\n本公司董事会及全体董事保证……"
    monkeypatch.setattr(fr.netfix, "http_request",
                        lambda *a, **k: _resp({"data": {"notice_content": body}}))
    rows = [_row("关于会计政策变更的公告（修订版）", "ANX", "2026-08-14 19:00")]
    with patch.object(fr, "_em_page", return_value=(rows, 1)):
        out = fr.cn_detail(title="关于会计政策变更的公告", code="600519",
                           pub_time="2026-08-14", doc_url="")
    assert out["content"] == body
    assert "正文包含标题校验" in out["caliber"]


def test_unique_candidate_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """同日同代码仅 1 条候选时直接采用（唯一性确定）。"""
    monkeypatch.setattr(fr.netfix, "http_request",
                        lambda *a, **k: _resp({"data": {"notice_content": "另一段正文，不含标题"}}))
    rows = [_row("完全不同的标题内容", "ANU", "2026-08-14 18:00")]
    with patch.object(fr, "_em_page", return_value=(rows, 1)):
        out = fr.cn_detail(title="某标题", code="600519", pub_time="2026-08-14", doc_url="")
    assert out["content"] == "另一段正文，不含标题"
    assert "唯一公告" in out["caliber"]


def test_stale_date_candidate_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """同标题往期公告（日期差 >1 天）必须排除，避免误取历史同名公告。"""
    rows = [_row("贵州茅台2026年半年度报告", "ANOLD", "2025-08-14 20:41")]
    with patch.object(fr, "_em_page", return_value=(rows, 1)):
        out = fr.cn_detail(title="贵州茅台2026年半年度报告", code="600519",
                           pub_time="2026-08-14", doc_url="")
    assert out["content"] == ""
    assert "未匹配到" in out["caliber"]


def test_empty_content_is_empty_state_not_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """源站无文本版正文 → 空态口径（图片型公告），不走错误码。"""
    monkeypatch.setattr(fr.netfix, "http_request",
                        lambda *a, **k: _resp({"data": {"notice_content": ""}}))
    out = fr.cn_detail(title="t", code="600519", pub_time="2026-08-14",
                       doc_url="https://data.eastmoney.com/notices/detail/AN1.html")
    assert out["content"] == ""
    assert "无文本版正文" in out["caliber"]


def test_upstream_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """东财正文接口非 200 → RuntimeError（通道异常），路由层转 UPSTREAM_ERROR。"""
    monkeypatch.setattr(fr.netfix, "http_request", lambda *a, **k: _resp({}, status_code=503))
    with pytest.raises(RuntimeError):
        fr.cn_detail(title="t", code="600519", pub_time="2026-08-14",
                     doc_url="https://data.eastmoney.com/notices/detail/AN1.html")


def test_route_translates_upstream_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """路由层：detail_cached 抛异常 → UPSTREAM_ERROR 中文提示，不裸抛 500。"""
    def _boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("东财公告正文 HTTP 503")
    monkeypatch.setattr(fr, "detail_cached", _boom)
    body = client.get("/api/news/financial-detail", params={
        "title": "t", "doc_url": "https://data.eastmoney.com/notices/detail/AN1.html",
    }).json()
    assert body.get("error_code") == "UPSTREAM_ERROR"
    assert "重新打开" in body.get("error", ""), body


# ---------------------------------------------------------------
# 四、港股 / 美股
# ---------------------------------------------------------------


def test_hk_detail_matches_traditional_title(monkeypatch: pytest.MonkeyPatch) -> None:
    """港股：HKEX 繁体标题经繁简归一在东财港股库命中 → verified=True，正文为繁体原文。"""
    monkeypatch.setattr(fr.netfix, "http_request",
                        lambda *a, **k: _resp({"data": {"notice_content": "香港交易及結算所有限公司…公告正文"}}))
    rows = [_row("根据全球中期票据计划完成发行18亿美元及人民币260亿元票据",
                 "ANHK1", "2026-10-22", code="00700")]
    with patch.object(fr, "_em_page", return_value=(rows, 1)) as page_spy:
        out = fr.hk_detail(
            title="根據全球中期票據計劃完成發行18億美元及人民幣260億元票據",
            code="00700", pub_time="2026-10-22 12:03",
            doc_url="https://www1.hkexnews.hk/listedco/listconews/sehk/2026/1022/x.pdf",
        )
    assert out["verified"] is True, out
    assert out["content"].startswith("香港交易及結算所有限公司")
    assert page_spy.call_args.kwargs["ann_type"] == "H"


def test_hk_detail_rejects_bad_code() -> None:
    """港股代码非法（非 4~5 位数字）→ 空态口径，不发起检索。"""
    out = fr.hk_detail(title="x", code="abc", pub_time="2026-10-22", doc_url="")
    assert out["content"] == ""
    assert "港股代码不合法" in out["caliber"]


def test_us_detail_fetches_edgar_html(monkeypatch: pytest.MonkeyPatch) -> None:
    """美股：抓 EDGAR 归档 HTML → 文本化 → verified=True（与列表同源）。"""
    html = ("<html><body><h1>APPLE INC</h1><p>FORM 10-K</p>"
            "<p>Revenue $391,035 million</p></body></html>")
    monkeypatch.setattr(fr.netfix, "http_request", lambda *a, **k: _resp({}, text=html))
    out = fr.us_detail(
        title="APPLE INC · 10-K 申报（2025-10-30）", code="AAPL", pub_time="2025-10-30 20:41",
        doc_url="https://www.sec.gov/Archives/edgar/data/320193/000032019325000123/aapl-20250927.htm",
    )
    assert out["verified"] is True
    assert "FORM 10-K" in out["content"]
    assert "391,035" in out["content"]
    assert "SEC EDGAR" in out["source"]


def test_us_detail_rejects_non_edgar_url() -> None:
    """美股：非 EDGAR 归档链接 → 空态口径。"""
    out = fr.us_detail(title="t", code="AAPL", pub_time="", doc_url="https://example.com/a.htm")
    assert out["content"] == ""
    assert "EDGAR" in out["caliber"]


# ---------------------------------------------------------------
# 五、正文分页、缓存与节选
# ---------------------------------------------------------------


def test_content_paging_concatenates(monkeypatch: pytest.MonkeyPatch) -> None:
    """正文分页：page_index 递增返回不同内容时按页拼接，不足 5000 字即停止。"""
    pages = {1: "甲" * 5000, 2: "乙" * 5000, 3: "丙" * 100}
    calls: List[int] = []

    def fake_req(method: str, url: str, **kwargs: Any) -> Any:
        page = kwargs["params"]["page_index"]
        calls.append(page)
        return _resp({"data": {"notice_content": pages.get(page, "")}})

    monkeypatch.setattr(fr.netfix, "http_request", fake_req)
    content = fr._em_content("ANTEST")
    assert len(content) == 5000 + 5000 + 100 + 2  # 三段 + 两个换行
    assert calls == [1, 2, 3], calls


def test_detail_cache_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    """同参数二次调用命中缓存（上游计数不增加）。"""
    calls = {"n": 0}

    def _req(*a: Any, **k: Any) -> Any:
        calls["n"] += 1
        return _resp({"data": {"notice_content": "缓存正文"}})
    monkeypatch.setattr(fr.netfix, "http_request", _req)
    params = {
        "market": "a", "title": "缓存测试标题9527", "code": "600519",
        "pub_time": "2026-08-14",
        "doc_url": "https://data.eastmoney.com/notices/detail/ANCACHE1.html",
    }
    out1 = fr.detail_cached(**params)
    out2 = fr.detail_cached(**params)
    assert out1["content"] == out2["content"] == "缓存正文"
    assert calls["n"] == 1, f"缓存未命中，上游被调用 {calls['n']} 次"


def test_long_content_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    """超长正文节选前 6000 字且 truncated=True、caliber 注明。"""
    payload = {"data": {"notice_content": "长" * 9000}}
    monkeypatch.setattr(fr.netfix, "http_request", lambda *a, **k: _resp(payload))
    out = fr.cn_detail(title="t", code="600519", pub_time="2026-08-14",
                       doc_url="https://data.eastmoney.com/notices/detail/AN1.html")
    assert len(out["content"]) == fr._DETAIL_MAX_CHARS
    assert out["truncated"] is True
    assert "节选" in out["caliber"]
