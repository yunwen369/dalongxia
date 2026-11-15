# -*- coding: utf-8 -*-
"""v3.7.1 板块端点提速的**取值等价性**对拍。

背景（2026-11-15，REQ-20261115-02）
----------------------------------
本轮把三个板块端点的冷启动从 24s 压到 8s 内，改动全是「取数方式」，不应动任何数值：

1. ``get_fundflow``：四档改成 ``_prefetch_flow_tiers`` 并发预热（原为循环内串行取）；
2. ``warm_fundflow_tiers``：阶段 B 改并发（原串行）；
3. ``/crowding/rankings``：缓存键去掉 limit，请求侧按 ``limit`` 切片。

因此需要证明：
A. 并发预取与串行取数得到**逐字段相同**的四档（含「取不到就 None、不补 0」的降级行为）；
B. ``limit=N`` 的返回 == 全量结果的前 N 条（切片不改变任何字段与顺序）。

用法：
    ./.venv/Scripts/python.exe tools/_verify_sector_equiv.py
    ./.venv/Scripts/python.exe tools/_verify_sector_equiv.py --skip-rankings

返回码：0=全部等价；1=存在差异（打印首个差异字段）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 四档口径里可能因取数方式不同而变化的字段（若并发改造引入口径差异，必然落在这些字段上）
_TIER_FIELDS = ("super_big", "big", "medium", "small", "covered")


def _diff_fundflow(old: List[Dict[str, Any]], new: List[Dict[str, Any]]) -> List[str]:
    """逐行比对两版资金流结果。

    Args:
        old: 串行取数（改造前口径）结果。
        new: 并发预取结果。

    Returns:
        List[str]: 差异描述列表；空列表表示完全一致。
    """
    problems: List[str] = []
    if len(old) != len(new):
        problems.append("行数不同：%d vs %d" % (len(old), len(new)))
    for left, right in zip(old, new):
        if left.get("name") != right.get("name"):
            problems.append("行序不同：%s vs %s" % (left.get("name"), right.get("name")))
            break
        for field in _TIER_FIELDS:
            if left.get(field) != right.get(field):
                problems.append(
                    "板块 %s 字段 %s 不同：%r vs %r"
                    % (left.get("name"), field, left.get(field), right.get(field))
                )
    return problems


def main() -> int:
    """入口。

    Returns:
        int: 0=等价，1=存在差异或执行异常。
    """
    parser = argparse.ArgumentParser(description="v3.7.1 板块端点取值等价性对拍")
    parser.add_argument("--skip-rankings", action="store_true", help="只对拍资金流（跳过 90 板块扫描）")
    args = parser.parse_args()

    from backend.app.datasource import sector

    failures = 0

    # ---------- A. 资金流：串行取数（禁用预取）vs 并发预取 ----------
    original_prefetch = sector._prefetch_flow_tiers
    original_tier_cache = sector._FLOW_TIER_CACHE

    sector._prefetch_flow_tiers = lambda rows, **kw: 0  # 关闭预取 → 回落到循环内串行取
    sector._FLOW_TIER_CACHE = type(original_tier_cache)("flow_tier_serial_baseline")
    try:
        baseline = sector.get_fundflow("cn")
    finally:
        sector._prefetch_flow_tiers = original_prefetch
        sector._FLOW_TIER_CACHE = original_tier_cache

    sector._prefetch_flow_tiers = original_prefetch
    concurrent = sector.get_fundflow("cn")

    diffs = _diff_fundflow(baseline, concurrent)
    covered_old = sum(1 for row in baseline if row.get("covered"))
    covered_new = sum(1 for row in concurrent if row.get("covered"))
    print("[A] 资金流：串行 %d 行（四档有值 %d）/ 并发 %d 行（四档有值 %d）"
          % (len(baseline), covered_old, len(concurrent), covered_new))
    if diffs:
        failures += 1
        for line in diffs[:10]:
            print("    差异：" + line)
    else:
        print("    A 结论：四档逐字段一致（含 None 不补 0 的降级行为）")

    # ---------- B. 排行：limit 切片 == 全量前 N 条 ----------
    if not args.skip_rankings:
        full = sector.get_crowding_rankings(kind="industry", limit=120)
        items = full.get("items") or []
        top10 = sector.get_crowding_rankings(kind="industry", limit=10)
        sliced = (top10.get("items") or [])
        ok = sliced == items[:len(sliced)] and len(sliced) == min(10, len(items))
        print("[B] 排行：全量 %d 条 / limit=10 返回 %d 条，切片等价=%s"
              % (len(items), len(sliced), ok))
        if not ok:
            failures += 1
            for index, (left, right) in enumerate(zip(sliced, items)):
                if left != right:
                    print("    首个差异在第 %d 条：%r vs %r" % (index, left, right))
                    break

    print("\n结论：" + ("全部等价（无口径变化）" if failures == 0 else "存在 %d 项差异，需排查" % failures))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
