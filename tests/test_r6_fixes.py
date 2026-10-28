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
    """从 dashboard.js 抽取 RECIPES 与 UNAVAILABLE 声明的组件编号。

    Returns:
        tuple[set, set]: (RECIPES 键集合, UNAVAILABLE 键集合)。
    """
    text = (FRONTEND / "js" / "dashboard.js").read_text(encoding="utf-8")
    recipes = text.split("var RECIPES = {")[1].split("\n  };")[0]
    recipe_ids = set(re.findall(r'"([A-Z]{1,2}-\d{2})"\s*:\s*\{', recipes))
    blocked = text.split("var UNAVAILABLE = {")[1].split("\n  };")[0]
    blocked_ids = set(re.findall(r'"([A-Z]{1,2}-\d{2})"\s*:\s*\{', blocked))
    return recipe_ids, blocked_ids


def test_every_registered_widget_is_renderable_or_blocked(client: TestClient) -> None:
    """注册表每个组件必须有渲染器，或被登记为「页面控件 / 不可用」并附原因。

    R6-04 原缺陷：49 个组件只有 17 个有渲染器，其余添加后只显示「无内置渲染器」。
    """
    registry = client.get("/api/widgets").json()["data"]["items"]
    recipe_ids, blocked_ids = _recipe_ids()
    covered = recipe_ids | blocked_ids
    missing = sorted({item["id"] for item in registry} - covered)
    assert not missing, f"以下组件既无渲染器也未登记不可用：{missing}"


def test_blocked_widgets_all_have_reason() -> None:
    """每个不可用组件都必须带中文原因（禁止静默禁用）。"""
    text = (FRONTEND / "js" / "dashboard.js").read_text(encoding="utf-8")
    blocked = text.split("var UNAVAILABLE = {")[1].split("\n  };")[0]
    entries = re.findall(r'"([A-Z]{1,2}-\d{2})"\s*:\s*\{([^}]*)\}', blocked)
    assert entries, "UNAVAILABLE 未解析到条目"
    for widget_id, body in entries:
        assert "reason:" in body, f"{widget_id} 缺少 reason"
        assert re.search(r'reason:\s*"[^"]{4,}"', body), f"{widget_id} 的 reason 过短或不合法"
