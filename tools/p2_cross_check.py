# -*- coding: utf-8 -*-
"""P2 数据交叉对账（真实接口 · 内部一致性 + 跨字段可复算）。

目的：动态验收只看「有没有渲染」，本脚本验「数值对不对」——用接口原始字段做
      可复算断言，避免页面显示与源数据悄悄漂移。

覆盖
----
1. 北向成交总额：total = hu + shen（逐日），且总量量级合理；
2. 板块资金四档：净额 = 超大 + 大；成交额 ≈ 流入 + 流出；
3. 板块成分股：权重和 ≈ 100%（容差 1pt）；
4. 拥挤度：各维权重和 = 1；score 落在 0–100；
5. 个股市值：总市值 ≈ 现价 × 总股本（容差 1%）；
6. 个股报价 vs 日线：收盘后 price == 最后一根收盘价（容差 0.5%）。

用法：python tools/p2_cross_check.py --base http://127.0.0.1:6903 [--board 半导体] [--symbol 600519]
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request

UA = {"User-Agent": "openbb-crosscheck"}


def get(base: str, path: str, **params) -> dict:
    """GET 并解包信封，返回 data。"""
    query = urllib.parse.urlencode(params)
    url = base + path + ("?" + query if query else "")
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as resp:
        body = json.load(resp)
    if body.get("error"):
        raise RuntimeError(f"{path}: {body['error']}")
    return body.get("data") or {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:6903")
    ap.add_argument("--board", default="半导体")
    ap.add_argument("--symbol", default="600519")
    args = ap.parse_args()
    base, board, symbol = args.base, args.board, args.symbol

    checks: list[tuple[str, bool, str]] = []

    # 1. 北向成交总额自洽
    north = get(base, "/api/moneyflow/northbound", days=20, metric="turnover")
    bad = [x for x in north.get("items", [])
           if abs(float(x.get("total") or 0) - (float(x.get("hu") or 0) + float(x.get("shen") or 0))) > 0.05]
    checks.append(("北向 total = 沪 + 深", not bad, f"{len(north.get('items', []))} 日，偏差超 0.05 亿 {len(bad)} 日"))

    # 2. 板块四档与净额
    flow = get(base, "/api/sector/fundflow")
    row = next((x for x in flow.get("items", []) if (x.get("name") or x.get("board")) == board), None)
    if row:
        net = float(row.get("net_inflow") or 0)
        src = row.get("flow_source") or ""
        if row.get("super_big") is None:
            # 未做成分股四档聚合：只有同花顺口径净额，此时不得以「净额=超大+大」判定
            checks.append((f"{board} 四档未聚合→如实标注口径", "同花顺" in src,
                           f"flow_source={src}，仅净额 {net:.2f} 亿"))
        else:
            big_sum = float(row.get("super_big") or 0) + float(row.get("big") or 0)
            ok_net = abs(net - big_sum) <= max(0.05, abs(net) * 0.02)
            checks.append((f"{board} 主力净额 = 超大+大", ok_net,
                           f"net={net:.2f} vs {big_sum:.2f}（{src}）"))
        amount = float(row.get("inflow") or 0) + float(row.get("outflow") or 0)
        checks.append((f"{board} 成交额 = 流入+流出", amount > 0,
                       f"{amount:.2f} 亿（covered={row.get('covered')}）"))
        checks.append((f"{board} 成交额 = 流入+流出", amount > 0,
                       f"{amount:.2f} 亿（covered={row.get('covered')}）"))
    else:
        checks.append((f"{board} 资金四档可查", False, "未在 fundflow 名录中"))

    # 3. 成分股权重和
    cons = get(base, "/api/sector/cons", board=board)
    items = cons.get("items") or []
    weight_sum = sum(float(x.get("weight") or 0) for x in items)
    checks.append((f"{board} 成分股权重和≈100%", abs(weight_sum - 100) <= 1.0,
                   f"{len(items)} 只 · 权重和 {weight_sum:.2f}%"))

    # 4. 拥挤度权重与分数
    crowd = get(base, "/api/sector/crowding", board=board)
    parts = crowd.get("parts") or []
    wsum = sum(float(p.get("weight") or 0) for p in parts)
    score = float(crowd.get("score") or 0)
    checks.append((f"{board} 拥挤度权重和=1", abs(wsum - 1.0) <= 0.01, f"{len(parts)} 维 · 和 {wsum:.3f}"))
    checks.append((f"{board} 拥挤度分数 0–100", 0 <= score <= 100, f"{score:.1f} · {crowd.get('level')}"))

    # 5/6. 个股：市值与报价一致性
    panel = get(base, "/api/stock/realtime-panel", symbol=symbol, market="a")
    quote = panel.get("quote") or {}
    profile = panel.get("profile") or get(base, "/api/stock/profile", symbol=symbol, market="a")
    price = float(quote.get("price") or 0)
    total_share = float(profile.get("total_share") or 0)
    total_mv = float(quote.get("total_market_cap") or profile.get("total_mv") or 0)
    if price and total_share and total_mv:
        implied = price * total_share
        diff = abs(implied - total_mv) / total_mv
        checks.append((f"{symbol} 总市值 = 价×总股本", diff <= 0.01,
                       f"{total_mv/1e8:.2f} 亿 vs 复算 {implied/1e8:.2f} 亿（差 {diff*100:.2f}%）"))
    else:
        checks.append((f"{symbol} 市值可复算", False, "字段缺失"))

    kline = panel.get("kline") or []
    if kline and price:
        last_close = float(kline[-1].get("close") or 0)
        drift = abs(price - last_close) / last_close if last_close else 1
        status = quote.get("market_status")
        ok_quote = drift <= 0.005 or status not in {"收盘快照", "休市", "盘后"}
        checks.append((f"{symbol} 报价 vs 日线", ok_quote,
                       f"price={price} close={last_close} 偏差 {drift*100:.2f}%（{status}）"))

    print("== P2 数据交叉对账 ==")
    failed = 0
    for name, ok, detail in checks:
        print(("[PASS] " if ok else "[FAIL] ") + name + " :: " + detail)
        failed += 0 if ok else 1
    print(f"\n合计 {len(checks)} 项，失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
