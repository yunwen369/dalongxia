# -*- coding: utf-8 -*-
"""v3.7.1 / v3.7.2 板块端点提速的**取值等价性**对拍。

背景（2026-11-15，REQ-20261115-02）
----------------------------------
本轮把三个板块端点的冷启动从 24s 压到 8s 内，改动全是「取数方式」，不应动任何数值：

1. ``get_fundflow``：四档改成 ``_prefetch_flow_tiers`` 并发预热（原为循环内串行取）；
2. ``warm_fundflow_tiers``：阶段 B 改并发（原串行）；
3. ``/crowding/rankings``：缓存键去掉 limit，请求侧按 ``limit`` 切片。

因此需要证明：
A. 并发预取与串行取数得到**逐字段相同**的四档（含「取不到就 None、不补 0」的降级行为）；
B. ``limit=N`` 的返回 == 全量结果的前 N 条（切片不改变任何字段与顺序）。

v3.7.2 追加（单板块 ``/api/sector/crowding`` 冷启动 16.1s → 2.9s）：
C. 成分股相关性的并发版 == 改造前串行实现（``_ref_*`` 为**原样复刻的黄金标准**）；
D. 成分股换手率的并发版 == 改造前串行实现；
E. 换手率分母口径：``_board_float_cap`` 命中 ``board_cap.json`` 时用同花顺流通市值、
   未命中时用本地快照总市值，两者只影响水平值、**不影响分位**（验证同比缩放）。

用法：
    ./.venv/Scripts/python.exe tools/_verify_sector_equiv.py
    ./.venv/Scripts/python.exe tools/_verify_sector_equiv.py --skip-rankings
    ./.venv/Scripts/python.exe tools/_verify_sector_equiv.py --board 银行

返回码：0=全部等价；1=存在差异（打印首个差异字段）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 四档口径里可能因取数方式不同而变化的字段（若并发改造引入口径差异，必然落在这些字段上）
_TIER_FIELDS = ("super_big", "big", "medium", "small", "covered")


def _ref_correlation(
    constituents: List[Dict[str, Any]],
    *,
    days: int = 60,
    max_samples: int = 20,
) -> Optional[Dict[str, Any]]:
    """改造前（全串行）的相关性实现，作为取值黄金标准。

    逐字复刻 v3.7.1 的 ``sector._constituent_correlation``，唯一区别是不并发。
    保留这份副本而不是复用 provider，是因为对拍的意义就在于「两份独立实现」——
    若两边共用同一份代码，改造引入的偏差会被同步复制而永远对不出来。

    Args:
        constituents: 成分股列表。
        days: 计算相关性的窗口天数。
        max_samples: 最多取多少只。

    Returns:
        Optional[Dict[str, Any]]: {corr, samples, pairs, window_days, caliber}。
    """
    from backend.app.datasource import ashare, utils

    sample = constituents[:max_samples]
    series: Dict[str, List[float]] = {}
    for item in sample:
        code = item.get("code") or ""
        if not code:
            continue
        try:
            klines = ashare.get_kline(code, "daily", "qfq", days + 5)
        except Exception:  # noqa: BLE001 - 单只失败不阻断
            continue
        closes = [utils.to_float(k.get("close")) for k in klines]
        closes = [c for c in closes if c is not None]
        if len(closes) >= days + 1:
            series[code] = closes[-days - 1:]
    if len(series) < 3:
        return None

    codes = list(series)
    rets: Dict[str, List[float]] = {}
    for code in codes:
        vals = series[code]
        rets[code] = [vals[i] / vals[i - 1] - 1 for i in range(1, len(vals))]
    min_len = min(len(v) for v in rets.values())
    for code in codes:
        rets[code] = rets[code][-min_len:]

    def _corr(a: List[float], b: List[float]) -> Optional[float]:
        """两条收益率序列的皮尔逊相关系数。"""
        if len(a) != len(b) or len(a) < 5:
            return None
        ma = sum(a) / len(a)
        mb = sum(b) / len(b)
        cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        va = sum((x - ma) ** 2 for x in a)
        vb = sum((y - mb) ** 2 for y in b)
        if va <= 0 or vb <= 0:
            return None
        denom = (va * vb) ** 0.5
        return cov / denom if denom else None

    pairs = 0
    total = 0.0
    for i in range(len(codes)):
        for j in range(i + 1, len(codes)):
            c = _corr(rets[codes[i]], rets[codes[j]])
            if c is not None:
                pairs += 1
                total += c
    if pairs == 0:
        return None
    avg = total / pairs
    return {
        "corr": round(avg, 4),
        "samples": len(codes),
        "pairs": pairs,
        "window_days": days,
        "caliber": (
            f"成分股相关性=近 {days} 日日线收益率的 {len(codes)} 只样本两两 Pearson 相关系数均值"
            f"（{pairs} 对）；相关性越高越拥挤"
        ),
    }


def _ref_turnover(
    constituents: List[Dict[str, Any]],
    *,
    max_samples: int = 20,
) -> Optional[Dict[str, Any]]:
    """改造前（全串行）的成分股换手率实现，作为取值黄金标准。

    Args:
        constituents: 成分股列表。
        max_samples: 最多取多少只。

    Returns:
        Optional[Dict[str, Any]]: {turnover, samples, caliber}。
    """
    from backend.app.datasource import tencent, utils

    sample = constituents[:max_samples]
    values: List[float] = []
    for item in sample:
        code = item.get("code") or ""
        if not code:
            continue
        try:
            quote = tencent.get_quote(code, market="a")
            tv = utils.to_float(quote.get("turnover"))
            if tv is not None:
                values.append(tv)
        except Exception:  # noqa: BLE001 - 单只失败不阻断
            continue
    if len(values) < 3:
        return None
    avg = sum(values) / len(values)
    return {
        "turnover": round(avg, 2),
        "samples": len(values),
        "caliber": (
            f"成分股换手率= {len(values)} 只样本当日换手率均值 {avg:.2f}%"
            "（腾讯报价快照，非分位，仅作水平参考，不计入拥挤度加权分）"
        ),
    }


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
    parser = argparse.ArgumentParser(description="v3.7.1/v3.7.2 板块端点取值等价性对拍")
    parser.add_argument("--skip-rankings", action="store_true", help="只对拍资金流（跳过 90 板块扫描）")
    parser.add_argument("--skip-crowding", action="store_true", help="跳过单板块拥挤度的 C/D/E 段")
    parser.add_argument("--board", default="半导体", help="C/D/E 段使用的板块名")
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

    # ---------- C/D. 拥挤度成分股二维：并发 vs 改造前串行参考实现 ----------
    if not args.skip_crowding:
        sector._load_cons_cache()
        codes = sector._CONS_CACHE.get(args.board) or []
        if len(codes) < 3:
            print("[C] 拥挤度：跳过（%s 无成分映射或不足 3 只）" % args.board)
        else:
            sample = [{"code": code, "board": args.board} for code in codes]
            for tag, label, ref_fn, new_fn in (
                ("C", "成分股相关性", _ref_correlation, sector._constituent_correlation),
                ("D", "成分股换手率", _ref_turnover, sector._constituent_turnover),
            ):
                ref = ref_fn(sample)
                new = new_fn(sample)
                same = ref == new
                print("[%s] %s：串行 %r / 并发 %r，逐字段一致=%s"
                      % (tag, label,
                         (ref or {}).get("corr", (ref or {}).get("turnover")),
                         (new or {}).get("corr", (new or {}).get("turnover")), same))
                if not same:
                    failures += 1
                    keys = set((ref or {})) | set((new or {}))
                    for key in sorted(keys):
                        if (ref or {}).get(key) != (new or {}).get(key):
                            print("    差异字段 %s：%r vs %r" % (key, (ref or {}).get(key), (new or {}).get(key)))

    # ---------- E. 换手率分母口径：只缩放水平值，不动分位 ----------
    if not args.skip_crowding:
        rows = sector.get_sector_kline(args.board, "daily", "cn").get("items") or []
        cap_hit = sector._cached_cap(args.board)
        cap_fallback = None
        try:
            from backend.app.datasource import screener as _screener

            snapshot = _screener._read_snapshot()
            snapshot_rows = snapshot.get("rows") or snapshot.get("items") or []
            codes_set = set(sector._CONS_CACHE.get(args.board) or [])
            cap_fallback = sum(
                float(row.get("mktcap") or 0) for row in snapshot_rows
                if str(row.get("code")) in codes_set
            ) or None
        except Exception:  # noqa: BLE001 - 快照不可用则不验口径
            cap_fallback = None
        if cap_hit and cap_fallback and rows:
            ratio = cap_fallback / cap_hit
            series_hit = [
                float(row.get("amount")) * 1e8 / cap_hit * 100.0
                for row in rows[-250:] if (sector._num(row.get("amount")) or 0) > 0
            ]
            series_fb = [
                float(row.get("amount")) * 1e8 / cap_fallback * 100.0
                for row in rows[-250:] if (sector._num(row.get("amount")) or 0) > 0
            ]
            pct_hit = sector._pct_rank(series_hit, series_hit[-1]) if series_hit else None
            pct_fb = sector._pct_rank(series_fb, series_fb[-1]) if series_fb else None
            print("[E] 换手率口径：流通市值 %.1f 亿 / 快照总市值 %.1f 亿（比 %.3f），"
                  "分位 %s vs %s，分位不变=%s"
                  % (cap_hit / 1e8, cap_fallback / 1e8, ratio, pct_hit, pct_fb, pct_hit == pct_fb))
            if pct_hit != pct_fb:
                failures += 1

    print("\n结论：" + ("全部等价（无口径变化）" if failures == 0 else "存在 %d 项差异，需排查" % failures))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
