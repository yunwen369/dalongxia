# -*- coding: utf-8 -*-
"""批次 5.3 人工对账：基金接口 vs 源站原始值。

对账范围（3 接口 × 每接口 ≥3 点）：
1. /api/fund/realtime  → 蛋卷 derived 原始 unit_nav / end_date / nav_grtd；
2. /api/fund/nav       → 蛋卷净值历史原始 date / nav（取前 3 条）；
3. /api/fund/dividend  → 东财 fhsp HTML 原始 权益登记日 / 每10份派现（取 000011 前 3 条）。

用法：
    .venv\\Scripts\\python.exe tools\\reconcile_53.py --base http://127.0.0.1:6909 --out <目录>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import Any, Dict, List

import requests

BASE_URL = "http://127.0.0.1:6909"


def api(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """调用本项目接口并返回 data。"""
    body = requests.get(BASE_URL + path, params=params, timeout=90).json()
    if body.get("error"):
        raise RuntimeError(f"{path} 失败：{body['error']}")
    return body["data"]


def record(name: str, target: str, field: str, api_value: Any, source_value: Any, tol: float = 0.0) -> Dict[str, Any]:
    """登记一条对账记录（数值按容差比较）。"""
    if isinstance(api_value, (int, float)) and isinstance(source_value, (int, float)):
        same = abs(float(api_value) - float(source_value)) <= max(tol, 1e-9)
    else:
        same = str(api_value) == str(source_value)
    return {
        "interface": name,
        "target": target,
        "field": field,
        "api_value": api_value,
        "source_value": source_value,
        "same": bool(same),
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def main() -> None:
    """执行三类基金数据对账。"""
    global BASE_URL
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE_URL)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    BASE_URL = args.base
    os.makedirs(args.out, exist_ok=True)

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from backend.app.core import netfix

    netfix.apply_netfix()
    from backend.app.datasource import utils  # noqa: E402

    rows: List[Dict[str, Any]] = []
    code = "017811"

    # ---------- 1. realtime vs 蛋卷 derived ----------
    realtime = api("/api/fund/realtime", {"code": code})
    raw = utils.http_get_json(
        f"https://danjuanfunds.com/djapi/fund/derived/{code}", referer="https://danjuanfunds.com/"
    )["data"]
    rows.append(record("/api/fund/realtime", code, "nav", realtime["nav"], float(raw["unit_nav"]), 1e-6))
    rows.append(record("/api/fund/realtime", code, "nav_date", realtime["nav_date"], raw["end_date"]))
    rows.append(record("/api/fund/realtime", code, "day_growth", realtime["day_growth"], float(raw["nav_grtd"]), 1e-6))

    # ---------- 2. nav vs 蛋卷净值历史 ----------
    nav = api("/api/fund/nav", {"code": code, "page": 1, "size": 5})
    raw_nav = utils.http_get_json(
        f"https://danjuanfunds.com/djapi/fund/nav/history/{code}?page=1&size=5",
        referer="https://danjuanfunds.com/",
    )["data"]["items"]
    for index in range(3):
        rows.append(record("/api/fund/nav", code, f"items[{index}].date",
                           nav["items"][index]["date"], raw_nav[index]["date"]))
        rows.append(record("/api/fund/nav", code, f"items[{index}].nav",
                           nav["items"][index]["nav"], float(raw_nav[index]["nav"]), 1e-6))

    # ---------- 3. dividend vs 东财 fhsp HTML ----------
    div_code = "000011"
    dividend = api("/api/fund/dividend", {"code": div_code})
    html = utils.http_get_text(
        f"https://fundf10.eastmoney.com/fhsp_{div_code}.html",
        referer="https://fundf10.eastmoney.com/",
        encoding="utf-8",
        timeout=20,
    )
    segment = re.search(r"class='w782 comm cfxq'.*?</table>", html, re.S).group(0)
    raw_rows: List[List[str]] = []
    for row_html in re.findall(r"<tr>(.*?)</tr>", segment, re.S):
        cells = [
            re.sub(r"<[^>]+>", "", cell).replace("&nbsp;", "").strip()
            for cell in re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.S)
        ]
        if len(cells) >= 5 and re.match(r"\d{4}", cells[0] or ""):
            raw_rows.append(cells)
    for index in range(3):
        rows.append(record("/api/fund/dividend", div_code, f"items[{index}].record_date",
                           dividend["items"][index]["record_date"], raw_rows[index][1]))
        rows.append(record("/api/fund/dividend", div_code, f"items[{index}].per_10_text",
                           dividend["items"][index]["per_10_text"], raw_rows[index][3]))

    total = len(rows)
    passed = sum(1 for item in rows if item["same"])
    result = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base": BASE_URL,
        "interfaces": sorted({item["interface"] for item in rows}),
        "total_points": total,
        "passed": passed,
        "failed": total - passed,
        "records": rows,
    }
    out_json = os.path.join(args.out, "对账记录_阶段5.3.json")
    with open(out_json, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)

    for item in rows:
        print(f"{'一致' if item['same'] else '不一致'} | {item['interface']} | {item['target']} | "
              f"{item['field']} | 接口={item['api_value']} | 源站={item['source_value']}")
    print(f"\n合计 {passed}/{total} 一致，结果：{out_json}")


if __name__ == "__main__":
    main()
