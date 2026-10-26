# -*- coding: utf-8 -*-
"""YJB-P2-5 验收：`get_holdings_view` 全账户合并时上游 `/user_account` 只调 1 次。

做法：在进程内把 `_plug_get` 换成计数桩（不触网、不碰真实凭据），打印完整调用序列
并断言次数。相比抓包，这种方式直接证明「数据层没有重复请求」，且可重复执行。

用法：
    ./.venv/Scripts/python.exe tools/verify_yjb_upstream_calls.py [--out report.json]

退出码：0=断言通过；1=次数不符。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.core import cache  # noqa: E402
from backend.app.datasource import yangjibao as yjb  # noqa: E402


def build_stub(calls: List[str]):
    """构造按路径返回契约假数据的 `_plug_get` 桩（同时记录调用序列）。

    Args:
        calls: 调用序列收集列表（原地追加路径）。

    Returns:
        Callable[[str, dict | None], Any]: 可直接替换 `yjb._plug_get` 的函数。
    """
    def stub(path: str, params: Dict[str, Any] | None = None) -> Any:
        base = str(path).split("?")[0]
        calls.append(base)
        if base == "/user_account":
            return {"is_single_account_model": False, "target_account_id": "acc-1",
                    "list": [{"id": "acc-1", "title": "账户一"},
                             {"id": "acc-2", "title": "账户二"},
                             {"id": "acc-3", "title": "账户三"}]}
        if base == "/fund_hold":
            account = str((params or {}).get("account_id") or "")
            return [{"code": f"00000{account[-1]}", "short_name": f"基金-{account}",
                     "hold_share": "100.00", "hold_cost": "1000.00",
                     "nv_info": {"gszzl": "0.50", "dwjz": "1.0100",
                                 "gztime": "2026-10-26 10:00:00"}}]
        raise AssertionError(f"未预期的上游路径：{path}")

    return stub


def main() -> int:
    """执行计数验证并输出报告。

    Returns:
        int: 进程退出码。
    """
    parser = argparse.ArgumentParser(description="验证 /user_account 单次调用")
    parser.add_argument("--out", help="JSON 报告输出路径")
    args = parser.parse_args()

    calls: List[str] = []
    original = yjb._plug_get  # noqa: SLF001 - 验收脚本需临时替换数据层边界
    yjb._plug_get = build_stub(calls)  # noqa: SLF001
    try:
        cache.clear_cache()
        view = yjb.get_holdings_view("all")
    finally:
        yjb._plug_get = original  # noqa: SLF001

    user_account_calls = calls.count("/user_account")
    fund_hold_calls = calls.count("/fund_hold")
    report = {
        "accounts": len(view.get("accounts") or []),
        "total": view.get("total"),
        "call_sequence": calls,
        "user_account_calls": user_account_calls,
        "fund_hold_calls": fund_hold_calls,
        "passed": user_account_calls == 1 and fund_hold_calls == len(view.get("accounts") or []),
    }
    print(f"账户数={report['accounts']}  持仓数={report['total']}")
    print(f"调用序列={calls}")
    print(f"/user_account 次数={user_account_calls}（要求 1）")
    print(f"/fund_hold 次数={fund_hold_calls}（要求 = 账户数）")
    print(f"[{'OK  ' if report['passed'] else 'FAIL'}] user_account_once")
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告：{out_path}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
