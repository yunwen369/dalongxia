# -*- coding: utf-8 -*-
"""阶段 11：A 股公告正文详情（详情弹窗按需拉取）回归。

覆盖：
1. 参数校验：title/doc_url 双空 → INVALID_PARAM；doc_url 域名白名单外 → INVALID_PARAM；
2. 东财来源直取：docUrl 含 art_code → 单源正文（verified=False）；
3. 巨潮来源交叉验证：标题+代码+公告日三重比对命中 → verified=True；
4. 交叉验证不过：成功信封 + 空 content + 中文 caliber（空/错分离，不造数）；
5. 上游通道异常：UPSTREAM_ERROR 中文提示；
6. 离线单元：标题归一（前缀剥离/标点剔除）、±1 日放宽、正文节选标记。

上游全部 mock，不打真实源站（真实链路验收由 tools/_ad_hoc 探针 + 起服实测承担）。
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.datasource import financial_report as fr

client = TestClient(app)


def _fake_response(payload: Dict[str, Any], status_code: int = 200) -> Any:
    """构造 requests.Response 的最小替身。"""
    class _R:
        def __init__(self) -> None:
            self.status_code = status_code

        def json(self) -> Dict[str, Any]:
            return payload
    return _R()


_EM_ROW = {
    "title": "贵州茅台2026年半年度报告",
    "code": "600519",
    "pubTime": "2026-08-14 20:41",
    "docUrl": "https://data.eastmoney.com/notices/detail/AN202608141827994407.html",
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
    """doc_url 非巨潮/东财已知域必须 INVALID_PARAM（安全约束）。"""
    body = client.get("/api/news/financial-detail", params={
        "title": "任意", "doc_url": "https://evil.example.com/a.pdf",
    }).json()
    assert body.get("error_code") == "INVALID_PARAM"
    assert "已拒绝" in body.get("error", ""), body


# ---------------------------------------------------------------
# 二、数据层离线单测（mock 上游）
# ---------------------------------------------------------------


def test_norm_title_strips_prefix_and_punct() -> None:
    """归一标题：东财「公司名:」前缀剥离 + 标点/空白剔除后全等。"""
    assert fr._norm_cn_title("贵州茅台:贵州茅台2026年半年度报告") \
        == fr._norm_cn_title("贵州茅台2026年半年度报告")
    assert fr._norm_cn_title("某司:关于会计政策变更的公告。") \
        == fr._norm_cn_title("关于 会计政策变更 的公告")


def test_day_diff_allows_one_day_shift() -> None:
    """日期放宽：巨潮整点化跨日允许 ±1，往期公告（>1 天）必须拒绝。"""
    assert fr._day_diff("2026-08-14", "2026-08-15") == 1
    assert fr._day_diff("2026-08-14", "2026-08-20") == 6
    assert fr._day_diff("2026-08-14", "bad-date") is None


def test_em_source_direct_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """东财来源：docUrl 直含 art_code → 拉正文成功，verified=False。"""
    payload = {"data": {"notice_content": "第一段正文。\r\n第二段正文。", "notice_title": "t"}}
    monkeypatch.setattr(fr.netfix, "http_request",
                        lambda *a, **k: _fake_response(payload))
    out = fr.cn_detail(
        title="贵州茅台2026年半年度报告", code="600519", pub_time="2026-08-14",
        doc_url="https://data.eastmoney.com/notices/detail/AN202608141827994407.html",
    )
    assert out["content"].startswith("第一段正文。")
    assert "\n" in out["content"]  # \r\n 已归一
    assert out["verified"] is False
    assert out["truncated"] is False


def test_cninfo_source_cross_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    """巨潮来源：跨源三重比对命中 → verified=True 且正文非空。"""
    payload = {"data": {"notice_content": "交叉验证正文", "notice_title": "t"}}
    monkeypatch.setattr(fr.netfix, "http_request",
                        lambda *a, **k: _fake_response(payload))
    with patch.object(fr, "_em_query", return_value=([_EM_ROW], 1)):
        out = fr.cn_detail(
            title="贵州茅台2026年半年度报告", code="600519", pub_time="2026-08-14",
            doc_url="https://static.cninfo.com.cn/finalpage/2026-08-14/x.PDF",
        )
    assert out["verified"] is True
    assert out["content"] == "交叉验证正文"
    assert "三重比对" in out["caliber"]


def test_cninfo_cross_match_rejects_stale_date(monkeypatch: pytest.MonkeyPatch) -> None:
    """同标题往期公告（日期差 >1 天）必须拒绝匹配，返回验证未通过空态。"""
    stale = dict(_EM_ROW, pubTime="2025-08-14 20:41")
    with patch.object(fr, "_em_query", return_value=([stale], 1)):
        out = fr.cn_detail(
            title="贵州茅台2026年半年度报告", code="600519", pub_time="2026-08-14",
            doc_url="https://static.cninfo.com.cn/finalpage/x.PDF",
        )
    assert out["content"] == ""
    assert "交叉验证" in out["caliber"]


def test_cross_match_miss_gives_caliber(monkeypatch: pytest.MonkeyPatch) -> None:
    """东财列表无匹配 → 成功信封 + 空 content + 中文口径（不造数）。"""
    with patch.object(fr, "_em_query", return_value=([], 0)):
        out = fr.cn_detail(title="不存在标题XYZ9527", code="600519",
                           pub_time="2026-08-14", doc_url="")
    assert out["content"] == ""
    assert "交叉验证" in out["caliber"]


def test_empty_content_is_empty_state_not_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """源站无文本版正文 → 空态 caliber（图片型公告），不走错误码。"""
    payload = {"data": {"notice_content": "", "notice_title": "t"}}
    monkeypatch.setattr(fr.netfix, "http_request",
                        lambda *a, **k: _fake_response(payload))
    out = fr.cn_detail(
        title="t", code="600519", pub_time="2026-08-14",
        doc_url="https://data.eastmoney.com/notices/detail/AN1.html",
    )
    assert out["content"] == ""
    assert "无文本版正文" in out["caliber"]


def test_upstream_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """东财正文接口非 200 → RuntimeError（通道异常），路由层转 UPSTREAM_ERROR。"""
    monkeypatch.setattr(fr.netfix, "http_request",
                        lambda *a, **k: _fake_response({}, status_code=503))
    with pytest.raises(RuntimeError):
        fr.cn_detail(title="t", code="600519", pub_time="2026-08-14",
                     doc_url="https://data.eastmoney.com/notices/detail/AN1.html")


def test_route_translates_upstream_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """路由层：cn_detail 抛异常 → UPSTREAM_ERROR 中文提示，不裸抛 500。"""
    def _boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("东财公告正文 HTTP 503")
    monkeypatch.setattr(fr, "cn_detail_cached", _boom)
    body = client.get("/api/news/financial-detail", params={
        "title": "t", "doc_url": "https://data.eastmoney.com/notices/detail/AN1.html",
    }).json()
    assert body.get("error_code") == "UPSTREAM_ERROR"
    assert "重新打开" in body.get("error", ""), body


def test_detail_cache_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    """同参数二次调用命中缓存（上游计数不增加）。"""
    payload = {"data": {"notice_content": "缓存正文", "notice_title": "t"}}
    calls = {"n": 0}

    def _req(*a: Any, **k: Any) -> Any:
        calls["n"] += 1
        return _fake_response(payload)
    monkeypatch.setattr(fr.netfix, "http_request", _req)
    params = {
        "title": "缓存测试标题9527", "code": "600519", "pub_time": "2026-08-14",
        "doc_url": "https://data.eastmoney.com/notices/detail/ANCACHE1.html",
    }
    out1 = fr.cn_detail_cached(**params)
    out2 = fr.cn_detail_cached(**params)
    assert out1["content"] == out2["content"] == "缓存正文"
    assert calls["n"] == 1, f"缓存未命中，上游被调用 {calls['n']} 次"


def test_long_content_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    """超长正文节选前 6000 字且 truncated=True、caliber 注明。"""
    payload = {"data": {"notice_content": "长" * 9000, "notice_title": "t"}}
    monkeypatch.setattr(fr.netfix, "http_request",
                        lambda *a, **k: _fake_response(payload))
    out = fr.cn_detail(title="t", code="600519", pub_time="2026-08-14",
                       doc_url="https://data.eastmoney.com/notices/detail/AN1.html")
    assert len(out["content"]) == fr._DETAIL_MAX_CHARS
    assert out["truncated"] is True
    assert "节选" in out["caliber"]
