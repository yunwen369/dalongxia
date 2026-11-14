# -*- coding: utf-8 -*-
"""基金组合接口「挂起」定位工具（faulthandler 周期打栈）。

背景（2026-11-15，v3.7.0 门禁）：
    全量 pytest 偶发挂在 ``test_fund_combo.py::test_evaluate_weights_respect_constraints``，
    进程 CPU 冻结、日志不再增长，netstat 可见一条 ESTABLISHED 但无数据往来的出向 443 连接。
    该用例走真实上游（天天基金 F10 / 雪球兜底 / akshare），需确认究竟卡在哪个调用点，
    是「无超时 HTTP」还是「DNS 解析」，还是锁等待。

用法：
    ./.venv/Scripts/python.exe tools/_diag_fund_hang.py --after 60
    ./.venv/Scripts/python.exe tools/_diag_fund_hang.py --after 45 --node "tests/test_fund_combo.py::test_evaluate_discloses_unknowns"

参数：
    --after   首次打栈延时（秒），默认 60。
    --repeat  每次间隔重复打栈（默认只打一次）。
    --node    pytest 节点，默认挂起的那条用例。

输出：
    全部线程的栈（含 File/line），直接定位阻塞函数。
"""

from __future__ import annotations

import argparse
import faulthandler
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_NODE = "tests/test_fund_combo.py::test_evaluate_weights_respect_constraints"


def main() -> int:
    """入口：装 faulthandler 定时打栈后执行指定 pytest 节点。

    Returns:
        int: pytest 退出码（被定时打栈不影响）。
    """
    parser = argparse.ArgumentParser(description="基金组合挂起定位")
    parser.add_argument("--after", type=float, default=60.0, help="首次打栈延时（秒）")
    parser.add_argument("--repeat", action="store_true", help="周期重复打栈")
    parser.add_argument("--node", default=DEFAULT_NODE, help="pytest 节点 id")
    args = parser.parse_args()

    faulthandler.dump_traceback_later(args.after, repeat=args.repeat)
    print(f"[diag] {args.after:.0f}s 后打印全部线程栈：{args.node}", flush=True)

    import pytest  # 延迟导入：确保 sys.path 已注入项目根

    code = pytest.main([args.node, "-q", "--tb=short", "-p", "no:cacheprovider", "-s"])
    faulthandler.cancel_dump_traceback_later()
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
