# -*- coding: utf-8 -*-
"""REQ-20261105-01 P1 残留补齐（v3.5.0）：R1/R2/R12/R17/R18 验收断言。

覆盖设计基准第十轮 v2.0 的五个 P1 验收点：

1. R1 板块净额口径对账：/api/sector/fundflow 已聚合板块「净额 == 四档之和」且
   flow_source 显式标注（同花顺主力单口径 vs 新浪全单四档聚合两套口径不再混排，
   消除「跨端点净额差 20%」的根因——同一行只允许一套口径）；
2. R2 指数估值双通道：乐咕主源失败 → 中证官网备源接管（backup/window_days 显式
   标注），双通道皆失败 → available=False + 中文 reason（不臆造数据，不再出「待验证」）；
3. R12 美股分时四态：lib/fmt.js ``OB.fmt.usSession`` 四态判定边界完整
   （盘前/盘中/盘后/休市，美东时区）+ 板块工具美股模式引用该徽章；
4. R17 重复体清零：core/ 四个死代码文件已退役 + w7_forms 重复事件分支收敛为唯一；
5. R18 搜索禁用态：检索在途「检索中…」文案 + busy 项不可点（无 data-symbol）
   + stale 响应丢弃。

mock 边界说明：R1/R2 仅 mock 外部数据源的**取数函数**（同花顺资金流表、乐咕/中证
估值序列、10Y 国债），组装与降级逻辑全部走真实代码路径。
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from backend.app.datasource import sector as sector_provider
from backend.app.datasource import valuation

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


# ---------------------------------------------------------------------------
# R1：板块净额口径对账（fundflow 单行只允许一套口径）
# ---------------------------------------------------------------------------

def _fake_flow_rows(count: int = 50) -> list:
    """构造同花顺口径资金流行（亿元）。"""
    rows = []
    for i in range(count):
        name = f"行业{i:02d}"
        rows.append({
            "name": name,
            "code": f"BK{i:04d}",
            "pct": 1.0 + i * 0.01,
            "net_inflow": 3.0 + i * 0.1,
            "inflow": 10.0 + i,
            "outflow": 7.0 + i * 0.9,
        })
    return rows


def test_r1_fundflow_aggregated_net_matches_tiers(monkeypatch) -> None:
    """已聚合板块：净额必须等于四档之和，且 flow_source 标注为新浪聚合口径。"""
    monkeypatch.setattr(sector_provider, "_ensure_cons_progress",
                        lambda *args, **kwargs: None)
    # 半导体按新浪四档聚合：净额 12.5 == 5.0+4.0+2.0+1.5（同源自洽）
    tiers = {"半导体": {"net_inflow": 12.5, "inflow": 30.0, "outflow": 17.5,
                        "super_big": 5.0, "big": 4.0, "medium": 2.0, "small": 1.5,
                        "covered": True}}

    def fake_tier(board: str, code) -> dict:
        return tiers.get(board) or {}

    monkeypatch.setattr(sector_provider, "_flow_tier", fake_tier)
    rows = _fake_flow_rows(50) + [
        {"name": "半导体", "code": "BK9998", "pct": 2.0,
         "net_inflow": 20.0, "inflow": 40.0, "outflow": 20.0}
    ]
    monkeypatch.setattr(sector_provider.ths_http, "get_industry_flow_rows",
                        lambda: rows)

    items = sector_provider.get_fundflow("cn")
    semi = [x for x in items if x["name"] == "半导体"][0]
    assert semi["flow_source"] == "新浪（成分股四档聚合）"
    tier_sum = semi["super_big"] + semi["big"] + semi["medium"] + semi["small"]
    assert abs(semi["net_inflow"] - tier_sum) < 0.05, "已聚合板块净额必须等于四档之和"
    assert semi["net_inflow"] == 12.5, "聚合板块净额取新浪聚合值，不用同花顺值"
    assert semi["covered"] is True


def test_r1_fundflow_unaggregated_keeps_ths_caliber(monkeypatch) -> None:
    """未聚合板块：净额保留同花顺口径、四档为 None（不补 0、不估算）。"""
    monkeypatch.setattr(sector_provider.ths_http, "get_industry_flow_rows",
                        lambda: _fake_flow_rows())
    monkeypatch.setattr(sector_provider, "_ensure_cons_progress",
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(sector_provider, "_flow_tier", lambda board, code: {})

    items = sector_provider.get_fundflow("cn")
    assert len(items) == 50
    first = items[0]
    assert first["flow_source"] == "同花顺"
    assert first["super_big"] is None and first["small"] is None
    assert first["net_inflow"] == 3.0, "未聚合板块净额用同花顺原值"


def test_r1_fundflow_row_shape_contract(monkeypatch) -> None:
    """每行字段完整：双口径标注 + 四档 + covered（跨端点对账的契约基础）。"""
    monkeypatch.setattr(sector_provider.ths_http, "get_industry_flow_rows",
                        lambda: _fake_flow_rows())
    monkeypatch.setattr(sector_provider, "_ensure_cons_progress",
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(sector_provider, "_flow_tier", lambda board, code: {})
    items = sector_provider.get_fundflow("cn")
    required = {"name", "pct", "net_inflow", "inflow", "outflow",
                "flow_source", "super_big", "big", "medium", "small", "covered"}
    for row in items:
        assert required <= set(row.keys()), f"资金流行缺字段：{required - set(row.keys())}"


# ---------------------------------------------------------------------------
# R2：指数估值双通道互备（不再出「待验证」）
# ---------------------------------------------------------------------------

def _csindex_series(days: int = 25) -> list:
    base = date(2026, 10, 1)
    return [{"date": base + timedelta(days=i), "value": 12.0 + i * 0.01}
            for i in range(days)]


def test_r2_valuation_backup_channel(monkeypatch) -> None:
    """主源失败 → 备源接管：available=True、backup 标注备源口径、window_days=样本数。"""
    def broken(symbol: str) -> list:
        raise RuntimeError("乐咕限频")

    monkeypatch.setattr(valuation, "_index_series", broken)
    monkeypatch.setattr(valuation, "_index_series_csindex",
                        lambda symbol: _csindex_series(25))
    monkeypatch.setattr(valuation, "_bond_10y", lambda: 1.6)

    def broken_market() -> list:
        raise RuntimeError("全 A 备源不可用")

    monkeypatch.setattr(valuation, "_market_series", broken_market)
    payload = valuation._load_percentile()
    indices = payload["indices"]
    top = indices[0]
    assert top["available"] is True
    assert top["backup"] is not None and "备源" in top["backup"]
    assert top["window_days"] == 25


def test_r2_valuation_dual_failure_honest(monkeypatch) -> None:
    """双通道皆失败 → available=False + 中文 reason（不臆造、不含「待验证」）。"""
    def broken(symbol: str) -> list:
        raise RuntimeError("主源失败")

    monkeypatch.setattr(valuation, "_index_series", broken)
    monkeypatch.setattr(valuation, "_index_series_csindex", broken)
    monkeypatch.setattr(valuation, "_bond_10y", lambda: None)

    def broken_market() -> list:
        raise RuntimeError("全 A 备源不可用")

    monkeypatch.setattr(valuation, "_market_series", broken_market)
    payload = valuation._load_percentile()
    indices = payload["indices"]
    assert len(indices) == len(valuation.INDEX_LIST)
    for item in indices:
        assert item["available"] is False
        assert "双通道" in item["reason"]
        assert "待验证" not in (item.get("reason") or "")


# ---------------------------------------------------------------------------
# R12：美股分时四态（lib/fmt.js usSession + 板块工具徽章）
# ---------------------------------------------------------------------------

def test_r12_ussession_four_phases() -> None:
    """usSession 必须含美东时区、四段边界（240/570/960/1200 分钟）与四态标识。"""
    src = (FRONTEND / "js" / "lib" / "fmt.js").read_text(encoding="utf-8")
    assert "usSession" in src, "lib/fmt.js 必须导出 usSession"
    assert "America/New_York" in src, "判定必须用美东时区"
    # 一天分钟轴的四段边界：盘前 04:00=240 起 / 盘中 09:30=570 / 盘后 16:00=960 / 20:00=1200 收
    for boundary in ("240", "570", "960", "1200"):
        assert boundary in src, f"usSession 缺时段边界 {boundary}"
    for phase in ("盘前", "盘中", "盘后", "休市"):
        assert phase in src, f"usSession 缺四态标识 {phase}"


def test_r12_sector_tools_us_badge() -> None:
    """板块工具美股模式必须渲染 usSession 徽章（四态可见，避免休市数据当实时）。"""
    src = (FRONTEND / "js" / "w7" / "w7_data.js").read_text(encoding="utf-8")
    assert 'W7.st.sectorMarket === "us"' in src
    assert "OB.fmt.usSession" in src
    assert "v7-us-session" in src
    css = (FRONTEND / "css" / "v7.css").read_text(encoding="utf-8")
    assert ".v7-us-session" in css, "徽章样式必须落 v7.css（token 化约束）"


# ---------------------------------------------------------------------------
# R17：死代码退役 + 重复体清零
# ---------------------------------------------------------------------------

def test_r17_core_dead_files_removed() -> None:
    """core/ 四个死代码文件（state4/ctxmenu/grid/tree）必须已退役。"""
    core_dir = FRONTEND / "js" / "core"
    for name in ("state4.js", "ctxmenu.js", "grid.js", "tree.js"):
        assert not (core_dir / name).exists(), f"core/{name} 应已 git rm"
    index_html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    for name in ("state4", "ctxmenu", "grid", "tree"):
        assert f"js/core/{name}.js" not in index_html, f"index.html 不得再加载 core/{name}.js"


def test_r17_forms_duplicate_branches_zero() -> None:
    """w7_forms.js 同名 act 分支必须唯一（原 watch×4 + edit×3 + 原型 import 全收敛）。"""
    src = (FRONTEND / "js" / "w7" / "w7_forms.js").read_text(encoding="utf-8")
    for act in ("watch-add", "watch-edit-sel", "watch-del-sel", "watch-edit",
                "watch-del", "edit-save", "edit-cancel", "edit-test",
                "edit-delete", "import-check", "import-confirm"):
        count = src.count(f'act === "{act}"')
        assert count == 1, f"act={act} 出现 {count} 次（应为 1，R17 重复体清零）"
    # 原型尾巴不得复活：edit-test/edit-delete 的「原型演示」toast 版本必须为 0
    assert src.count('edit-test") { toast("测试提醒已发送到提醒中心（原型演示）")') == 0
    assert src.count('edit-delete") { toast("规则已删除（原型演示，未写后端）")') == 0
    assert src.count('toast("已导入 " + okN + " 条（原型演示，未写后端）")') == 0


# ---------------------------------------------------------------------------
# R18：搜索禁用态 + 「检索中…」文案
# ---------------------------------------------------------------------------

def test_r18_search_busy_state() -> None:
    """顶栏搜索：检索在途显示「检索中…」busy 项（无 data-symbol 不可点）+ stale 丢弃。"""
    src = (FRONTEND / "js" / "w7" / "w7_topbar.js").read_text(encoding="utf-8")
    assert "检索中…" in src, "请求在途必须显示「检索中…」文案"
    assert "v7-search-busy" in src, "busy 项必须带禁用态样式类"
    assert "my !== seq" in src, "慢响应必须按序号丢弃（stale 响应不覆盖新结果）"
    # busy 项不含 data-symbol → 点击分支 getAttribute("data-symbol") 为空直接 return（禁点）
    assert 'v7-search-busy"><b>检索中…</b>' in src
    css = (FRONTEND / "css" / "v7.css").read_text(encoding="utf-8")
    assert ".v7-search-busy" in css, "busy 样式必须落 v7.css"
