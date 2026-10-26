"""第九波（r9）设计出入处置的契约测试。

锁定四类「文档与代码不许再漂移」的口径：

1. **注册表分类完备**（DS-05）：49 个区块每个都必须带 `category`，取值必须在 6 个冻结分类内，
   且 6 个分类都要被用到（否则组件库 chips 会少一个）；
2. **注册表绑定路径真实存在**（DS-04）：`api` 里写的每个路径都必须出现在 OpenAPI 里，
   杜绝「注册表写 `/api/watchlist/all` 但后端根本没有」这类幻觉路径；
3. **图表页报价与 K 线解耦**（R3-32）：`loadCharting` 不得再把 quote 塞进与 kline/minute
   同一个 `Promise.all`——慢源下那会让报价卡跟着慢 8-10s；
4. **热力图副标题无内部口径**（UI-01）：i18n 里不得再出现 `abs(` / `为原值` 这类实现说明。

运行：.venv\\Scripts\\python.exe -m pytest tests/test_r9_contract.py -v
"""

from __future__ import annotations

import pathlib
import re

from fastapi.testclient import TestClient

from backend.app import widgets_registry
from backend.app.main import app

client = TestClient(app)
ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_widget_category_complete() -> None:
    """49 个区块都必须有合法分类，且 6 个分类全部被用到。"""
    items = widgets_registry.all_widgets()
    assert len(items) == 49
    valid = set(widgets_registry.LIBRARY_CATEGORIES)
    assert len(valid) == 6
    used = set()
    for item in items:
        category = item.get("category")
        assert category in valid, f"{item['id']} 的分类非法：{category}"
        assert item.get("category_label") == widgets_registry.LIBRARY_CATEGORIES[category]
        used.add(category)
    assert used == valid, f"未被使用的分类：{valid - used}"


def test_widgets_api_exposes_categories() -> None:
    """/api/widgets 必须一并下发分类字典，供前端渲染 chips。"""
    body = client.get("/api/widgets").json()
    assert body["error"] is None
    data = body["data"]
    assert data["total"] == 49
    assert data["categories"] == widgets_registry.LIBRARY_CATEGORIES
    assert all("category" in item for item in data["items"])


def test_registry_api_paths_exist_in_openapi() -> None:
    """注册表里绑定的每个接口路径都必须真实存在（DS-04 防幻觉路径）。"""
    spec = client.get("/openapi.json").json()
    existed = set(spec.get("paths") or {})
    phantom = []
    for item in widgets_registry.all_widgets():
        for api_path in item.get("api") or []:
            clean = api_path.split("?")[0]
            if clean not in existed:
                phantom.append(f"{item['id']} → {clean}")
    assert phantom == [], f"注册表绑定了不存在的接口：{phantom}"
    assert "/api/watchlist" in existed
    assert "/api/watchlist/all" not in existed
    assert "/api/watchlist/quotes" not in existed


def test_charting_quote_decoupled_from_kline() -> None:
    """R3-32：loadCharting 里报价不得与 K 线/分时共用同一个 Promise.all。"""
    source = (ROOT / "frontend" / "js" / "pages.js").read_text(encoding="utf-8")
    start = source.index("function loadCharting()")
    end = source.index("function paintChartingQuote(", start)
    body = source[start:end]
    assert "chartingRequestToken" in body, "缺少请求令牌：切标的时旧响应会覆盖新标的"
    # 找出所有 Promise.all([...]) 的方括号内容，断言其中不含 quote
    for match in re.finditer(r"Promise\.all\(\[(.*?)\]\)", body, re.S):
        inner = match.group(1)
        assert "/api/market/quote" not in inner, "报价仍被塞进 Promise.all，慢源下会带累报价卡"
    assert body.count('api("/api/market/quote"') == 1


def test_heatmap_subtitle_has_no_internal_caliber() -> None:
    """UI-01：热力图卡副标题不得出现 abs( / 为原值 这类内部实现说明。"""
    i18n = (ROOT / "frontend" / "js" / "i18n.js").read_text(encoding="utf-8")
    match = re.search(r'"sectors\.heatSub":\s*"([^"]*)"', i18n)
    assert match, "sectors.heatSub 键丢失"
    text = match.group(1)
    for mark in ("abs(", "为原值", "口径="):
        assert mark not in text, f"副标题仍含内部口径「{mark}」：{text}"


def test_library_category_i18n_keys_present() -> None:
    """6 个分类的中文名必须在字典里，否则 chips 会显示成裸键。"""
    i18n = (ROOT / "frontend" / "js" / "i18n.js").read_text(encoding="utf-8")
    for code, label in widgets_registry.LIBRARY_CATEGORIES.items():
        key = f'"dashboard.libCat.{code}"'
        assert key in i18n, f"缺少 i18n 键 {key}"
        assert f"{key}: \"{label}\"" in i18n, f"{key} 的中文名与注册表不一致（应为「{label}」）"
