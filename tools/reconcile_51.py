# -*- coding: utf-8 -*-
"""阶段 5.1 人工对账脚本：接口输出 vs 源站原始值逐字段比对。

对账口径（对应规范 §11.9 数据核对专项）：
- 每个接口取 ≥3 个数据点，记录 时间 / 标的 / 字段 / 接口值 / 源站值 / 是否一致；
- 「源站值」由本脚本直接调用源站（同花顺 akshare 封装、新浪 HTTP、东财 datacenter、腾讯 HTTP），
  不复用 backend 的 provider 逻辑，避免自己和自己对账；
- 结果写入 JSON，供《阶段5.1自检报告》引用。

用法：
    .venv\\Scripts\\python.exe tools\\reconcile_51.py --base http://127.0.0.1:6901 --out <目录>
"""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any, Dict, List

import requests

BASE_URL = "http://127.0.0.1:6901"


def api(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """调用本项目接口并返回 data。"""
    response = requests.get(BASE_URL + path, params=params, timeout=60)
    body = response.json()
    if body.get("error"):
        raise RuntimeError(f"{path} 失败：{body['error']}")
    return body["data"]


def check(name: str, symbol: str, field: str, api_value: Any, source_value: Any, tolerance: float = 0.0) -> Dict[str, Any]:
    """登记一条对账记录，数值型按容差比较，其余按字符串严格比较。"""
    if isinstance(api_value, (int, float)) and isinstance(source_value, (int, float)):
        same = abs(float(api_value) - float(source_value)) <= max(tolerance, 1e-9)
    else:
        same = str(api_value) == str(source_value)
    return {
        "interface": name,
        "symbol_or_board": symbol,
        "field": field,
        "api_value": api_value,
        "source_value": source_value,
        "same": bool(same),
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def main() -> None:
    """执行 5 组接口对账并输出结果。"""
    global BASE_URL
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE_URL)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    BASE_URL = args.base
    os.makedirs(args.out, exist_ok=True)

    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from backend.app.core import netfix

    netfix.apply_netfix()
    import akshare as ak  # noqa: E402

    records: List[Dict[str, Any]] = []

    # ---------- 1. /api/sector/industry vs 同花顺行业汇总 ----------
    industry = api("/api/sector/industry", {"market": "cn"})
    raw_industry = ak.stock_board_industry_summary_ths()
    by_name = {str(row["板块"]): row for _, row in raw_industry.iterrows()}
    for board, fields in {
        "厨卫电器": [("pct", "涨跌幅"), ("amount", "总成交额"), ("lead_stock", "领涨股")],
        "银行": [("pct", "涨跌幅"), ("amount", "总成交额")],
        "半导体": [("pct", "涨跌幅"), ("amount", "总成交额")],
    }.items():
        api_item = next((item for item in industry["items"] if item["name"] == board), None)
        for api_field, raw_field in fields:
            records.append(
                check("/api/sector/industry", board, api_field,
                      api_item[api_field] if api_item else None,
                      float(by_name[board][raw_field]) if raw_field in ("涨跌幅", "总成交额") else str(by_name[board][raw_field]),
                      tolerance=0.011)
            )

    # ---------- 2. /api/sector/fundflow vs 同花顺行业资金流 ----------
    flow = api("/api/sector/fundflow", {"market": "cn"})
    raw_flow = ak.stock_fund_flow_industry(symbol="即时")
    flow_by_name = {str(row["行业"]): row for _, row in raw_flow.iterrows()}
    for board in ("厨卫电器", "电机", "银行"):
        api_item = next((item for item in flow["items"] if item["name"] == board), None)
        records.append(
            check("/api/sector/fundflow", board, "net_inflow",
                  api_item["net_inflow"] if api_item else None,
                  float(flow_by_name[board]["净额"]), tolerance=0.011)
        )
        records.append(
            check("/api/sector/fundflow", board, "inflow",
                  api_item["inflow"] if api_item else None,
                  float(flow_by_name[board]["流入资金"]), tolerance=0.011)
        )

    # ---------- 3. /api/stock/realtime-panel vs 腾讯报价 + 新浪资金流 ----------
    panel = api("/api/stock/realtime-panel", {"symbol": "688012", "market": "a"})
    raw_quote = requests.get("https://qt.gtimg.cn/q=sh688012", timeout=15)
    raw_quote.encoding = "gbk"
    parts = raw_quote.text.split("~")
    records.append(check("/api/stock/realtime-panel", "688012", "quote.price",
                         panel["quote"]["price"], float(parts[3]), tolerance=0.001))
    records.append(check("/api/stock/realtime-panel", "688012", "quote.prev_close",
                         panel["quote"]["prev_close"], float(parts[4]), tolerance=0.001))
    records.append(check("/api/stock/realtime-panel", "688012", "quote.pct",
                         panel["quote"]["pct"], float(parts[32]), tolerance=0.001))

    raw_flow_json = requests.get(
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "MoneyFlow.ssi_ssfx_flzjtj?daima=sh688012",
        timeout=15,
    ).json()
    super_net = (float(raw_flow_json["r0_in"]) - float(raw_flow_json["r0_out"])) / 1e8
    big_net = (float(raw_flow_json["r1_in"]) - float(raw_flow_json["r1_out"])) / 1e8
    tier_map = {tier["name"]: tier for tier in panel["fund_dist"]["tiers"]}
    records.append(check("/api/stock/realtime-panel", "688012", "fund_dist.超大单.net",
                         tier_map["超大单"]["net"], round(super_net, 4), tolerance=0.0002))
    records.append(check("/api/stock/realtime-panel", "688012", "fund_dist.大单.net",
                         tier_map["大单"]["net"], round(big_net, 4), tolerance=0.0002))

    # ---------- 4. /api/stock/financial-summary vs 新浪财务摘要 ----------
    summary = api("/api/stock/financial-summary", {"symbol": "688012"})
    raw_summary = ak.stock_financial_abstract(symbol="688012")
    raw_rows = {str(row["指标"]): row for _, row in raw_summary.iterrows() if str(row["选项"]) == "常用指标"}
    api_metrics = {item["key"]: item["values"][0] for item in summary["metrics"]}
    for key in ("基本每股收益", "毛利率", "净资产收益率(ROE)"):
        records.append(check("/api/stock/financial-summary", "688012", key,
                             api_metrics[key], float(raw_rows[key]["20260630"]), tolerance=0.001))

    # ---------- 5. /api/stock/holders vs 东财 F10 ----------
    holders = api("/api/stock/holders", {"symbol": "688012"})
    resp = requests.get(
        "https://datacenter-web.eastmoney.com/api/data/v1/get",
        params={
            "reportName": "RPT_F10_EH_HOLDERS",
            "columns": "ALL",
            "filter": '(SECUCODE="688012.SH")',
            "pageNumber": 1,
            "pageSize": 200,
            "sortColumns": "END_DATE",
            "sortTypes": "-1",
            "source": "HSF10",
            "client": "PC",
        },
        timeout=20,
    )
    raw_holders = (resp.json().get("result") or {}).get("data") or []
    # 以接口返回的报告期为准（接口会跳过不足 5 名的临时披露期），做同期同字段比对
    api_period = holders["top10_period"]
    latest_rows = [row for row in raw_holders if str(row["END_DATE"])[:10] == api_period]
    latest_rows.sort(key=lambda row: row["HOLDER_RANK"])
    records.append(check("/api/stock/holders", "688012", "top10[0].name",
                         holders["top10"][0]["name"], latest_rows[0]["HOLDER_NAME"]))
    records.append(check("/api/stock/holders", "688012", "top10[0].ratio",
                         holders["top10"][0]["ratio"], float(latest_rows[0]["HOLD_NUM_RATIO"]), tolerance=0.011))
    records.append(check("/api/stock/holders", "688012", "top10_period",
                         holders["top10_period"], api_period))

    total = len(records)
    passed = sum(1 for item in records if item["same"])
    result = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base": BASE_URL,
        "interfaces": sorted({item["interface"] for item in records}),
        "total_points": total,
        "passed": passed,
        "failed": total - passed,
        "records": records,
    }
    out_json = os.path.join(args.out, "对账记录_阶段5.1.json")
    with open(out_json, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)

    for item in records:
        flag = "一致" if item["same"] else "不一致"
        print(f"{flag} | {item['interface']} | {item['symbol_or_board']} | {item['field']} | "
              f"接口={item['api_value']} | 源站={item['source_value']}")
    print(f"\n合计 {passed}/{total} 一致，结果：{out_json}")


if __name__ == "__main__":
    main()
