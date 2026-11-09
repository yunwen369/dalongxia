# -*- coding: utf-8 -*-
"""pytest 全局隔离夹具（R2-03）：逐用例重置进程级状态与缓存目录。

背景：本轮独立验收 R2-03 指出全量 pytest 稳定 3 failed —— 真实网络用例
（test_qa_verify，字母序先跑）把命中源写入 ``resilient._PREFERRED``、把探测结果
写入 ``tencent._EXISTS_CACHE``，后续 monkeypatch 单测（test_units）被这些进程级
记忆污染：降级链按记忆重排优先源，直接绕过 monkeypatch，断言必然失败。

处置（规范 §11.11 测试隔离、§11.4 可重复）：
1. 每个用例前/后清空 ``_PREFERRED``、``_CIRCUIT_UNTIL``、``_EXISTS_CACHE``；
2. 每个用例前/后清空内存缓存 ``cache._MEMORY``；
3. 磁盘缓存目录按用例重定向到 ``tmp_path``，避免真实 ``data/cache`` 跨用例串味，
   同时避免 ``clear_cache()`` 批量删除真实文件触发沙箱 safe-delete 钩子；
4. 第四波新增的进程内偏好快照（``user_prefs._CACHE`` / ``source_prefs._CACHE``）
   同样在用例前后重置，防止「某用例改开关 → 后续用例读到脏值」。

约束：不改任何用例期望值、不加 skip/xfail、不把真实网络用例改成 mock。
"""

from __future__ import annotations

import os

import pytest

from backend.app import config
from backend.app.core import audit as audit_module
from backend.app.core import cache as cache_module
from backend.app.core import resilient, source_prefs, user_prefs, v8_guard
from backend.app.datasource import alerts as alerts_module
from backend.app.datasource import dashboards as dashboards_module
from backend.app.datasource import portfolio as portfolio_module
from backend.app.datasource import screener as screener_module
from backend.app.datasource import tencent
from backend.app.datasource import watchlist as watchlist_module
from backend.app.datasource import yangjibao as yangjibao_module

# REQ-20261109-01 批次发现（全套修复的一半）：部署环境可能 setx 了
# OPENBB_ACCESS_PASSWORD（公网访问口令），pytest 进程继承后——
# ① middleware 在全部单测生效 → 401/429 假失败（实测 226 failed）；
# ② 测试模块顶层 `client = TestClient(create_app())` 在 import 时刻按
#    「口令已启用」冻结 app 形态（openapi_url=None）→ 契约对账拿空 schema。
# conftest 在 pytest 启动早期加载、先于所有测试模块 import，此处直接清环境；
# autouse fixture 再逐用例兜底（防测试中途改环境）。
os.environ.pop("OPENBB_ACCESS_PASSWORD", None)


def _reset_process_state() -> None:
    """清空进程级记忆与内存缓存（幂等，前后各调用一次）。"""
    with cache_module._LOCK:  # noqa: SLF001 - 测试隔离需直接操作私有锁
        cache_module._MEMORY.clear()
    resilient._PREFERRED.clear()  # noqa: SLF001 - 备源优先级记忆
    # 第四波新增：偏好类模块的进程内快照（置 None 表示下次读取重新从磁盘加载）
    user_prefs._CACHE = None  # noqa: SLF001 - 测试隔离需重置模块级快照
    source_prefs._CACHE = None  # noqa: SLF001 - 测试隔离需重置模块级快照
    with resilient._CIRCUIT_LOCK:  # noqa: SLF001 - 熔断表有独立锁
        resilient._CIRCUIT_UNTIL.clear()
    tencent._EXISTS_CACHE.clear()  # noqa: SLF001 - 存在性探测缓存
    # 第五轮 P1-01：V8 熔断/半开是进程级状态，跨用例会互相污染
    # （修复前未重置 → 「用例全绿但真实服务会被打挂」的假绿成因之一）
    v8_guard.reset_state()
    # 第七轮波次 5：提醒评估节流状态复位
    alerts_module.reset_eval_state()


@pytest.fixture(autouse=True)
def isolate_process_state(monkeypatch, tmp_path):
    """逐用例隔离：重置全局态 + 磁盘缓存指向 tmp（R2-03 根因修复）。

    Args:
        monkeypatch: pytest 内置补丁器，用于重定向 ``config.CACHE_DIR``。
        tmp_path: pytest 提供的用例级临时目录。

    Yields:
        None: 用例执行期间的隔离环境。
    """
    # REQ-20261109-01 批次发现：部署环境可能 setx 了 OPENBB_ACCESS_PASSWORD
    # （公网访问口令），pytest 进程继承后中间件在全部单测中生效 → 大面积
    # 401/429 假失败（实测 226 failed）。测试基线统一为「未配置口令」，
    # 需要口令场景的用例（test_access_middleware_p5）自行 setenv 覆盖。
    monkeypatch.delenv("OPENBB_ACCESS_PASSWORD", raising=False)

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "CACHE_DIR", cache_dir)
    # AI 配置同样隔离：用例不得写入真实 data/ai_config.json（P1-15 后为密文，
    # 仍不应被测试覆盖，否则会污染用户已保存的密钥）
    monkeypatch.setattr(config, "AI_CONFIG_FILE", tmp_path / "ai_config.json")
    # RT-01 审计日志同样隔离：破坏性端点用例会逐条落审计，绝不能写进真实 data/audit/
    # （真实审计文件是用户本机的安全痕迹，被测试覆盖就失去取证价值）
    monkeypatch.setattr(audit_module, "AUDIT_FILE", tmp_path / "audit" / "yjb_audit.jsonl")
    # 第五轮阶段 2：数据文件全面隔离（此前只有 CACHE_DIR / AI_CONFIG_FILE）
    # 实测缺陷：tests/test_qa_verify.py 的 POST /api/portfolio 会改写真实
    # data/portfolio.json（54B→56B），等于每次跑 pytest 都改用户数据字节。
    # 这些常量在模块导入时求值，故必须逐个 patch 到 tmp_path；
    # 同时把 config.DATA_DIR 指向 tmp，覆盖运行时动态拼接的路径（如 cache.layout）。
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(portfolio_module, "PORTFOLIO_FILE", data_dir / "portfolio.json")
    monkeypatch.setattr(watchlist_module, "WATCHLIST_FILE", data_dir / "watchlist.json")
    monkeypatch.setattr(user_prefs, "PREFS_FILE", data_dir / "user_prefs.json")
    monkeypatch.setattr(dashboards_module, "DASHBOARDS_FILE", data_dir / "dashboards.json")
    monkeypatch.setattr(source_prefs, "CONNECTORS_FILE", data_dir / "connectors.json")
    monkeypatch.setattr(yangjibao_module, "YJB_TOKEN_FILE", data_dir / "yjb_token.json")
    monkeypatch.setattr(yangjibao_module, "YJB_STATE_FILE", data_dir / "yjb_state.json")
    monkeypatch.setattr(yangjibao_module, "YJB_SECRET_FILE", data_dir / "yjb_secret.json")
    # 第七轮波次 5：筛选器快照与提醒文件同样隔离（绝不写真实 data/）
    monkeypatch.setattr(screener_module, "SNAPSHOT_FILE", data_dir / "screener_snapshot.json")
    monkeypatch.setattr(alerts_module, "ALERTS_FILE", data_dir / "alerts.json")
    # 第十轮 R11/C11：概念排行抽检日志同样隔离（用例不得写真实 concept_spotcheck.jsonl）
    from backend.app.datasource import sector_health as sector_health_module
    monkeypatch.setattr(sector_health_module, "SPOTCHECK_FILE", data_dir / "concept_spotcheck.jsonl")
    # 提醒评估节流是进程级状态，跨用例会互相污染（60s 窗口内第二个用例被跳过）
    alerts_module.reset_eval_state()

    _reset_process_state()
    try:
        yield
    finally:
        _reset_process_state()
