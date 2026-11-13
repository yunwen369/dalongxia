"""阶段 1 冒烟测试。

覆盖门禁要求：
1. /api/health 返回 200，且结构为 {data, error}、data 非空；
2. 根路径返回 index.html（单端口同源托管生效）；
3. 未知 API 也返回统一契约，不裸抛英文 404。

运行方式（在 openbb-cn 目录）：
    .venv/Scripts/python.exe -m pytest
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


def test_health_returns_data() -> None:
    """健康检查必须返回真实构建信息与非空数据源状态。"""
    response = client.get("/api/health")
    assert response.status_code == 200

    payload = response.json()
    assert {"data", "error"} <= set(payload.keys())
    assert payload.get("error_code") is None
    assert payload["error"] is None

    data = payload["data"]
    assert data, "health.data 不能为空"
    assert data["build"], "health.data.build 不能为空"
    assert data["app"]["name"] == "大龙虾投研工作台"
    assert isinstance(data["datasources"], dict) and data["datasources"]
    assert "akshare" in data["datasources"]


def test_root_serves_index_html() -> None:
    """根路径必须返回前端首页，而不是 JSON 或 404。"""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "大龙虾投研工作台" in response.text


def test_unknown_api_returns_contract() -> None:
    """未知 API 必须返回 {data,error} 契约，错误文案为中文。"""
    response = client.get("/api/not-exist")
    assert response.status_code == 200

    payload = response.json()
    assert payload["data"] is None
    assert isinstance(payload["error"], str) and payload["error"]
    assert "不存在" in payload["error"]
