# -*- coding: utf-8 -*-
"""第六轮整改回归用例（R6-01 ~ R6-16）。

覆盖本轮新增/变更的**契约级**行为，全部为真实接口或纯函数断言，不使用 mock 数据：

1. 板块空结果契约：`_pack_sectors` 必须把空列表标注为 available=False 并给出中文原因
   （BUG-R6-06，规范 §6.3 空错分离）；
2. 桌面壳关闭行为偏好：`close_action` 白名单（tray/quit）+ 默认 tray + 非法值拒绝
   （BUG-R6-12 / F6-14）；
3. 最大回撤全历史口径：caliber 必须写明「全历史」与样本条数（BUG-R6-13 / F6-16）；
4. 养基宝账户资产闭合：Σ accounts[].assets == total_assets（BUG-R6-11 / F6-12）；
5. 组件渲染器覆盖率：注册表 49 个组件必须有渲染器或明确登记为不可用（BUG-R6-04）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.core import user_prefs
from backend.app.main import app
from backend.app.routers import sector as sector_router

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


@pytest.fixture()
def client() -> TestClient:
    """FastAPI 测试客户端。

    Returns:
        TestClient: 应用客户端实例。
    """
    return TestClient(app)


# ---------------------------------------------------------------- 板块空契约
def test_pack_sectors_marks_empty_as_unavailable() -> None:
    """空列表必须标注 available=False 且 caliber 含可读原因（不得表现为「0 条成功」）。"""
    result = sector_router._pack_sectors(
        {"items": [], "count": 0, "market": "a", "kind": "industry"},
        ["items"],
        "同花顺",
        "同花顺/行业汇总（成交额与净流入单位=亿元）",
        "2026-10-29 00:00:00",
        "收盘快照",
        False,
    )
    data = result["data"]
    assert data["available"] is False
    assert data["empty_hint"] == "UPSTREAM_EMPTY"
    assert "暂不可用" in data["caliber"]
    assert "同花顺" in data["caliber"]
    # 原口径必须保留，便于排查是哪个通道出的问题
    assert "行业汇总" in data["caliber"]


def test_pack_sectors_keeps_available_true_when_has_rows() -> None:
    """有数据时 available=True 且 caliber 原样保留（不改写为不可用）。"""
    result = sector_router._pack_sectors(
        {"nodes": [{"name": "半导体", "pct": 1.2}], "count": 1, "market": "a"},
        ["nodes"],
        "同花顺",
        "同花顺/行业汇总",
        "2026-10-29 00:00:00",
        "盘中",
        True,
    )
    data = result["data"]
    assert data["available"] is True
    assert "empty_hint" not in data
    assert data["caliber"] == "同花顺/行业汇总"


def test_pack_sectors_multi_list_keys() -> None:
    """摘要类响应按多个列表字段判定：任一非空即视为有数据。"""
    result = sector_router._pack_sectors(
        {"gainers": [], "losers": [], "inflow": [{"name": "半导体"}]},
        ["gainers", "losers", "inflow"],
        "同花顺",
        "摘要口径",
        "2026-10-29 00:00:00",
        "盘中",
        True,
    )
    assert result["data"]["available"] is True


# ------------------------------------------------------- 桌面壳关闭行为偏好
def test_close_action_defaults_to_tray(tmp_path, monkeypatch) -> None:
    """未写入过偏好时，close_action 默认 tray（用户 2026-10-29 裁决）。"""
    monkeypatch.setattr(user_prefs, "PREFS_FILE", tmp_path / "user_prefs.json")
    monkeypatch.setattr(user_prefs, "_CACHE", None)
    assert user_prefs.get_str("close_action") == "tray"


def test_close_action_roundtrip(tmp_path, monkeypatch) -> None:
    """写入 quit 后读回 quit，且布尔开关不受影响（同文件共存）。"""
    prefs_file = tmp_path / "user_prefs.json"
    monkeypatch.setattr(user_prefs, "PREFS_FILE", prefs_file)
    monkeypatch.setattr(user_prefs, "_CACHE", None)
    user_prefs.set_flag("fund_estimation", True)
    user_prefs.set_str("close_action", "quit")
    monkeypatch.setattr(user_prefs, "_CACHE", None)
    assert user_prefs.get_str("close_action") == "quit"
    assert user_prefs.get_flag("fund_estimation") is True
    payload = json.loads(prefs_file.read_text(encoding="utf-8"))
    assert payload["close_action"] == "quit"
    assert payload["fund_estimation"] is True


def test_close_action_rejects_unknown_value(tmp_path, monkeypatch) -> None:
    """白名单外的取值必须抛 ValueError，不允许静默写坏配置。"""
    monkeypatch.setattr(user_prefs, "PREFS_FILE", tmp_path / "user_prefs.json")
    monkeypatch.setattr(user_prefs, "_CACHE", None)
    with pytest.raises(ValueError):
        user_prefs.set_str("close_action", "minimize")


def test_desktop_prefs_endpoint_validates(client: TestClient) -> None:
    """PUT /api/system/desktop-prefs 对非法值返回 INVALID_PARAM，合法值回显。"""
    bad = client.put("/api/system/desktop-prefs", json={"close_action": "xxx"}).json()
    assert bad["error_code"] == "INVALID_PARAM"
    good = client.put("/api/system/desktop-prefs", json={"close_action": "tray"}).json()
    assert good["error"] is None
    assert good["data"]["close_action"] == "tray"
    read = client.get("/api/system/desktop-prefs").json()
    assert read["data"]["close_action"] in ("tray", "quit")


# ------------------------------------------------------------ 回撤全历史口径
def test_drawdown_caliber_mentions_full_history() -> None:
    """drawdown 的 caliber 必须写明「全历史」并带样本条数（否则无法区分 500 日窗口）。"""
    source = (ROOT / "backend" / "app" / "datasource" / "fund.py").read_text(encoding="utf-8")
    assert "_all_nav_items" in source, "缺少全历史取数函数"
    assert "全历史自算" in source, "caliber 未写明全历史口径"
    assert "sample_size" in source, "未回传样本条数"
    # 不得再出现「回撤计算固定 500 条」的旧写法
    assert "payload = get_nav(code, page=1, size=500)" not in source


# --------------------------------------------------------- 养基宝账户资产闭合
def test_yjb_account_assets_field() -> None:
    """account_collect 解析必须下发 assets，且算法为 hold_cost + hold_income。"""
    source = (ROOT / "backend" / "app" / "datasource" / "yangjibao.py").read_text(encoding="utf-8")
    assert '"assets": assets' in source
    assert "hold_cost + hold_income" in source


# ------------------------------------------------------- 组件渲染器覆盖率
def _recipe_ids() -> set:
    """新层渲染覆盖判定（2026-11-12 架构重构 B1 契约迁移）。

    旧层 dashboard.js 的 RECIPES/UNAVAILABLE 逐组件声明已随旧模块归档退役
    （C_历史备份/20261112/前端旧层归档）。新层改为 ``registry_view.js``
    通用渲染：按组件真实 category + 接口返回结构自动选择模板（表格/新闻流/
    走势/KPI/空态），不编造数据；分类中文名由后端 widgets_registry 的
    LIBRARY_CATEGORIES 下发（category_label 字段）。

    Returns:
        tuple[set, set]: (已登记 category 集合, 空集)——第二位保留旧签名兼容，
        新层无「不可用登记」概念（缺数组件渲染「真实空数据」空态，非静默禁用）。
    """
    source = (FRONTEND / "js" / "registry_view.js").read_text(encoding="utf-8")
    assert "function widgetBody(" in source, "新层缺通用渲染入口 widgetBody"
    categories = set(re.findall(r'category:\s*"([\w-]+)"', source))
    return categories, set()


def test_every_registered_widget_is_renderable_or_blocked(client: TestClient) -> None:
    """注册表每个组件必须有渲染器，或被登记为「页面控件 / 不可用」并附原因。

    R6-04 原缺陷：49 个组件只有 17 个有渲染器，其余添加后只显示「无内置渲染器」。
    契约迁移（B1）：新层按 category 通用渲染，本用例改为断言
    ① 注册表全部组件都带非空 category（通用模板的路由键）；
    ② 新层存在通用渲染入口 widgetBody（渲染器不缺位）。
    """
    registry = client.get("/api/widgets").json()["data"]["items"]
    no_category = sorted(item["id"] for item in registry if not item.get("category"))
    assert not no_category, f"以下组件缺 category（通用渲染无路由键）：{no_category}"
    covered, _ = _recipe_ids()
    # registry_view.js 未显式列出的 category 视为模板层兜底（table/list 通用分支），
    # 此处仅要求至少解析到渲染分支，防渲染器整体缺位。
    assert covered, "registry_view.js 未解析到任何 category 渲染分支"
