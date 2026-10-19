# -*- coding: utf-8 -*-
"""批次 5.2 人工对账：新闻接口 vs 源站原始返回逐字段比对。

对账口径（规范 §11.9）：
- 3 个接口 × 每个 ≥3 数据点，记录 时间 / 标的 / 字段 / 接口值 / 源站值 / 是否一致；
- 源站值由本脚本直接调用 akshare 原始函数取得，不复用 backend provider；
- 新闻为滚动数据，比对采用「标题 + 发布时间」精确匹配；
- 结果写入 对账记录_阶段5.2.json。

用法：
    .venv\\Scripts\\python.exe tools\\reconcile_52.py --base http://127.0.0.1:6907 --out <目录>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List

import requests

BASE_URL = "http://127.0.0.1:6907"


def api(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """调用本项目接口并返回 data。"""
    body = requests.get(BASE_URL + path, params=params, timeout=90).json()
    if body.get("error"):
        raise RuntimeError(f"{path} 失败：{body['error']}")
    return body["data"]


def record(name: str, target: str, field: str, api_value: Any, source_value: Any) -> Dict[str, Any]:
    """登记一条对账记录。"""
    same = str(api_value).strip() == str(source_value).strip()
    return {
        "interface": name,
        "target": target,
        "field": field,
        "api_value": api_value,
        "source_value": source_value,
        "same": same,
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def main() -> None:
    """执行三类新闻源对账。"""
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
    import akshare as ak  # noqa: E402

    rows: List[Dict[str, Any]] = []

    # ---------- 1. 财联社快讯 vs stock_info_global_cls ----------
    flash = api("/api/news/flash", {"limit": 30})["items"]
    raw_flash = ak.stock_info_global_cls(symbol="全部").to_dict("records")
    by_title = {str(item.get("标题") or "").strip(): item for item in raw_flash}
    for item in flash[:3]:
        raw = by_title.get(item["title"].strip())
        rows.append(record("/api/news/flash", item["title"][:24], "title", item["title"],
                           str(raw.get("标题")).strip() if raw else None))
        expected_time = f"{raw.get('发布日期')} {raw.get('发布时间')}".strip() if raw else None
        rows.append(record("/api/news/flash", item["title"][:24], "time", item["time"], expected_time))

    # ---------- 2. 东财全球 vs stock_info_global_em ----------
    global_items = api("/api/news/global", {"limit": 60})["items"]
    raw_em = ak.stock_info_global_em().to_dict("records")
    em_by_title = {str(item.get("标题") or "").strip(): item for item in raw_em}
    em_api = [item for item in global_items if item["source"] == "东方财富"][:3]
    for item in em_api:
        raw = em_by_title.get(item["title"].strip())
        rows.append(record("/api/news/global", item["title"][:24], "title", item["title"],
                           str(raw.get("标题")).strip() if raw else None))
        rows.append(record("/api/news/global", item["title"][:24], "time", item["time"],
                           str(raw.get("发布时间")).strip() if raw else None))

    # ---------- 3. 个股新闻 vs stock_news_em ----------
    stock_items = api("/api/news/stock", {"symbol": "688012", "limit": 10})["items"]
    raw_stock = ak.stock_news_em(symbol="688012").to_dict("records")
    stock_by_title = {str(item.get("新闻标题") or "").strip(): item for item in raw_stock}
    for item in stock_items[:3]:
        raw = stock_by_title.get(item["title"].strip())
        rows.append(record("/api/news/stock", "688012", "title", item["title"],
                           str(raw.get("新闻标题")).strip() if raw else None))
        rows.append(record("/api/news/stock", "688012", "time", item["time"],
                           str(raw.get("发布时间")).strip() if raw else None))

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
    out_json = os.path.join(args.out, "对账记录_阶段5.2.json")
    with open(out_json, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)

    for item in rows:
        print(f"{'一致' if item['same'] else '不一致'} | {item['interface']} | {item['target']} | "
              f"{item['field']} | 接口={item['api_value']} | 源站={item['source_value']}")
    print(f"\n合计 {passed}/{total} 一致，结果：{out_json}")


if __name__ == "__main__":
    main()
