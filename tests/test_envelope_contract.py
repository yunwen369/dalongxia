"""响应信封唯一实现层契约测试（第九轮 · DS-R9-v1.0）。

覆盖设计基准 §5.1 的 A3（公共 API 唯一性）与 V13（`failure_pack` 三路径单测）：

1. `failure_pack` 合法错误码 / 非法错误码 / 缺失错误码三条路径；
2. `bad_request` 与 `pack_success` 的信封形状（`{data, error, error_code}` + 五字段）；
3. AST 扫描 `routers/` 不得再出现 `_pack` / `_bad` / `_bad_request` / `_failure`
   本地定义——本项是「公共 API 推广完成」的机器可验证闸门（§7.11 单一实现）。
"""

from __future__ import annotations

import ast
from pathlib import Path

from backend.app.core.errors import (
    ErrorCode,
    bad_request,
    failure_pack,
    pack_success,
)

ROUTERS_DIR = Path(__file__).resolve().parents[1] / "backend" / "app" / "routers"
LOCAL_HELPERS = {"_pack", "_bad", "_bad_request", "_failure"}


class TestFailurePack:
    """`failure_pack`：降级链失败 → 统一错误响应。"""

    def test_known_code_preserved(self) -> None:
        """合法 error_code 原样保留，空结构与文案透传。"""
        result = failure_pack({"error": "数据源暂不可用", "error_code": "EMPTY_DATA"}, {"items": []})
        assert result == {
            "data": {"items": []},
            "error": "数据源暂不可用",
            "error_code": ErrorCode.EMPTY_DATA.value,
        }

    def test_unknown_code_falls_back(self) -> None:
        """非法 error_code 归入 UPSTREAM_ERROR，不向上抛异常。"""
        result = failure_pack({"error": "上游异常", "error_code": "NOT_A_CODE"}, {})
        assert result["error_code"] == ErrorCode.UPSTREAM_ERROR.value
        assert result["error"] == "上游异常"

    def test_missing_code_falls_back(self) -> None:
        """error_code 缺失（None）同样归入 UPSTREAM_ERROR。"""
        result = failure_pack({"error": "上游异常"}, {"quote": {}})
        assert result["error_code"] == ErrorCode.UPSTREAM_ERROR.value
        assert result["data"] == {"quote": {}}

    def test_default_message_when_error_missing(self) -> None:
        """error 缺失时使用错误码的默认中文文案，不返回 None。"""
        result = failure_pack({"error_code": "UPSTREAM_TIMEOUT"}, [])
        assert result["error"] == "上游数据源响应超时，请稍后点击刷新重试。"
        assert result["data"] == []


class TestEnvelopeShape:
    """成功/参数错误信封形状。"""

    def test_pack_success_five_fields(self) -> None:
        """成功响应 data 必须含数据三性五字段。"""
        result = pack_success({"items": []}, "东财", "盘中实时", "2026-11-03 10:00:00", "盘中", True)
        assert result["error"] is None
        assert result["error_code"] is None
        for key in ("source", "caliber", "as_of", "market_status", "is_realtime"):
            assert key in result["data"]

    def test_bad_request_contract(self) -> None:
        """参数错误固定 INVALID_PARAM，文案原样透传。"""
        result = bad_request("code 参数不可为空")
        assert result == {
            "data": None,
            "error": "code 参数不可为空",
            "error_code": ErrorCode.INVALID_PARAM.value,
        }


class TestSingleImplementation:
    """router 层不得再定义响应信封本地实现（§7.11 / §1.8-2）。"""

    def test_no_local_envelope_helpers(self) -> None:
        """AST 扫描：9 个 router 的本地信封函数定义数必须为 0。"""
        offenders = []
        for path in sorted(ROUTERS_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in LOCAL_HELPERS:
                    offenders.append(f"{path.name}:{node.lineno} {node.name}")
        assert offenders == [], f"发现本地信封实现：{offenders}"
