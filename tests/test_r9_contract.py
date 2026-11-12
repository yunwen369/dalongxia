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
    """R3-32：报价不得与 K 线/分时共用同一个 Promise.all（慢源下旧响应带累新标的）。

    契约迁移（2026-11-12 架构重构 B1）：旧层 pages/charting.js 的 loadCharting
    已随整站替换退役。新层 K 线实现为 v7_app.js ``loadQuote()``——分时与 K 线
    各自独立 apiJson 请求，无 Promise.all 捆绑。本用例改为断言新层结构：
    loadQuote 存在、体内无 Promise.all、分时与 kline 两分支各自独立发请求。
    """
    source = (ROOT / "frontend" / "js" / "v7_app.js").read_text(encoding="utf-8")
    assert "function loadQuote(" in source, "新层未找到 loadQuote"
    start = source.index("function loadQuote(")
    body = source[start:start + 3000]
    # 体内不得把报价塞进 Promise.all（旧缺陷模式）
    for match in re.finditer(r"Promise\.all\(\[(.*?)\]\)", body, re.S):
        inner = match.group(1)
        assert "/api/market/quote" not in inner, "报价仍被塞进 Promise.all"
    # 分时与 K 线各自独立请求（互不捆绑）
    assert '"/api/market/minute?' in body, "缺少独立的分时请求"
    assert '"/api/market/kline?' in body, "缺少独立的 K 线请求"


def test_heatmap_subtitle_has_no_internal_caliber() -> None:
    """UI-01：热力图卡副标题不得出现内部实现说明（abs( / 为原值 / 口径=）。

    契约迁移（2026-11-12 架构重构 B1）：旧层副标题文案在 i18n.js
    ``sectors.heatSub``（已归档）。新层热力图卡由 views.js ``type:"heat"``
    通用模板渲染、v7_app.js ``loadSector("板块热力")`` 注入，文案集中在
    这两个文件——改为对两者全文断言违规字样为 0。
    """
    combined = "\n".join(
        (ROOT / "frontend" / "js" / name).read_text(encoding="utf-8")
        for name in ("views.js", "v7_app.js")
    )
    for mark in ("为原值", "口径="):
        assert mark not in combined, f"新层仍含内部口径「{mark}」"
    for line in combined.splitlines():
        if '"heat"' in line or "板块热力" in line:
            assert "abs(" not in line, f"热力图行仍含 abs(：{line.strip()}"


def test_library_category_labels_covered_by_registry() -> None:
    """6 个分类中文名由后端 /api/widgets 的 category_label 直接下发（不再依赖前端 i18n）。

    契约迁移（2026-11-12 架构重构 B1）：旧层前端用 i18n 键 ``dashboard.libCat.*``
    渲染分类 chips（i18n.js 已归档，多语言为非目标）。新层分类中文名来自
    widgets_registry.LIBRARY_CATEGORIES → 每个组件的 category_label 字段。
    本用例断言：注册表 49 组件的 category 全部有中文名，防裸键回显。
    """
    mapping = widgets_registry.LIBRARY_CATEGORIES
    missing = [
        item["id"] for item in widgets_registry.all_widgets()
        if not mapping.get(item.get("category", ""))
    ]
    assert missing == [], f"以下组件的 category 无中文名：{missing}"
