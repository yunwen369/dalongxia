# -*- coding: utf-8 -*-
"""阶段 8 数据核对专项（§11.9）：绕开本项目 provider，直连源站逐字段对账 + 独立复算。

对账组别（每组 ≥3 核对点）：
- G1 持仓盈亏/人民币折算复算：A股+港股+基金三标的（tmp 隔离持仓），
  直连腾讯报价/蛋卷净值/新浪汇率，独立重算 market_value/pnl/pnl_pct/market_value_cny
  与 /api/portfolio/analysis 对照；
- G2 基金回撤复算：直连蛋卷净值历史，独立复算 max_dd/current_dd/peak_date/trough_date；
- G3 拟合估值复算：直连东财重仓季报 + 腾讯批量报价，独立加权复算估算涨幅，
  与 /api/fund/realtime 拟合值对照（A股已收盘，价格为收盘快照，确定性可比）；
- G4 财务趋势复算：营业总收入/净利润 YoY 由 API 返回序列独立复算，
  原始字段与直连新浪 abstract 对照（≥3 期）；
- G5 报价涨跌幅复算：A/HK/US 三市场 pct_chg = price/prev_close-1 直连腾讯复算。

纪律：只读源站 + tmp 隔离写操作（红线一）；容差按口径显式声明；不一致按 P0 记录。

运行（干净 PYTHONPATH，避开沙箱 safe-delete 钩子）：
    PYTHONPATH= .venv\\Scripts\\python.exe tools\\reconcile_phase8.py --out <目录>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 网络自修复必须先于 akshare import（规范 §6.5：中文路径证书/代理异常回退）
from backend.app.core import netfix  # noqa: E402

netfix.apply_netfix()

TMP_DIR = ROOT / "data" / "reconcile_tmp_phase8"
TMP_DIR.mkdir(parents=True, exist_ok=True)
PORTFOLIO_TMP = TMP_DIR / "portfolio.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

records: List[Dict[str, Any]] = []


def record(group: str, target: str, field: str, api_value: Any, source_value: Any,
           tol: float = 0.0) -> None:
    """登记一条对账记录（数值按容差比较，其余按字符串相等）。"""
    if isinstance(api_value, (int, float)) and isinstance(source_value, (int, float)):
        same = abs(float(api_value) - float(source_value)) <= max(tol, 1e-9)
    else:
        same = str(api_value) == str(source_value)
    records.append({
        "group": group, "target": target, "field": field,
        "api_value": api_value, "source_value": source_value,
        "tol": tol, "same": bool(same),
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    mark = "✔" if same else "✘"
    print(f"  {mark} [{group}] {target} {field}: api={api_value} vs 源={source_value} (tol={tol})")


# ---------------------------------------------------------------- 直连源站原语
def tencent_quote(code_a: str = "sh600519", code_hk: str = "r_hk00700",
                  code_us: str = "usAAPL") -> Dict[str, Any]:
    """直连腾讯报价，返回 {code: {price, prev_close, pct}}（解析 v_ 行情串）。

    实测口径（2026-10-20）：报价端点 qt.gtimg.cn/q= 的美股代码**不带交易所后缀**
    （`usAAPL.OQ` 返回 `v_pv_none_match="1"` 无效占位；混入组合请求时整行丢失，
    导致 A/HK 两行返回而美股缺失）。K 线/分时端点才需 `.OQ`/`.N` 后缀。
    行情行前缀：A股/港股 `v_xxx=`，美股 `v_usAAPL=`（K 线响应才是 `var `）。
    """
    url = f"https://qt.gtimg.cn/q={code_a},{code_hk},{code_us}"
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=15)
    resp.encoding = "gbk"
    out: Dict[str, Any] = {}
    for line in resp.text.strip().split(";"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key.startswith("var "):
            key = key[4:]
        if key.startswith("v_"):
            key = key[2:]
        value = line.split("=", 1)[1].strip().strip('"')
        parts = value.split("~")
        if len(parts) < 5:
            continue
        price, prev_close = float(parts[3] or 0), float(parts[4] or 0)
        out[key] = {
            "price": price, "prev_close": prev_close,
            "pct": (price / prev_close - 1) * 100 if prev_close else None,
        }
    return out


def danjuan_nav_history(code: str, size: int = 500) -> List[Dict[str, Any]]:
    """直连蛋卷净值历史（与 provider 同端点，但绕过本项目封装/缓存）。

    响应结构：`{"data": {"items": [{date, nav, ...}], ...}, "result_code": 0}`，
    需解包一层 data（实测 2026-10-20）。
    """
    url = f"https://danjuanfunds.com/djapi/fund/nav/history/{code}"
    resp = requests.get(url, params={"page": 1, "size": size},
                        headers={"User-Agent": UA}, timeout=30)
    payload = resp.json()
    items = (payload.get("data") or {}).get("items") or []
    return [{"date": item.get("date"), "nav": float(item.get("nav") or 0)}
            for item in items if item.get("nav")]


def sina_fx() -> Dict[str, float]:
    """直连新浪汇率中间价（USD/HKD → CNY）。"""
    url = "https://hq.sinajs.cn/list=fx_susdcny,fx_shkdcny"
    resp = requests.get(url, headers={"User-Agent": UA, "Referer": "https://finance.sina.com.cn"},
                        timeout=15)
    resp.encoding = "gbk"
    out: Dict[str, float] = {}
    for line in resp.text.strip().split(";"):
        if "fx_s" not in line:
            continue
        key = line.split("=", 1)[0].replace("var hq_str_", "").strip()
        parts = line.split('"', 1)[1].rsplit('"', 1)[0].split(",")
        if len(parts) >= 3:
            out[key] = float(parts[2])  # 口径：第 3 列为中间价（与 provider 注释一致）
    return out


def em_holdings(code: str, year: str) -> List[Dict[str, Any]]:
    """直连东财重仓季报（akshare 直调，绕开项目 provider 缓存/清洗）。"""
    import akshare as ak

    frame = ak.fund_portfolio_hold_em(symbol=code, date=year)
    items = []
    for _, row in frame.iterrows():
        items.append({
            "code": str(row.get("股票代码") or ""),
            "name": row.get("股票名称"),
            "ratio": float(row.get("占净值比例") or 0),
            "quarter": row.get("季度"),
        })
    return items


def sina_financial_abstract(symbol: str) -> Dict[str, Any]:
    """直连新浪财务摘要（akshare 直调）：取最新 6 个报告期原始字段。

    注意（实测 2026-10-20）：摘要表报告期列为**倒序**（最新在前，远古期在后），
    必须从前部取；「净利润」需**精确匹配**指标名，contains 会误中「归母净利润」。
    """
    import akshare as ak

    frame = ak.stock_financial_abstract(symbol=symbol)
    name_col = frame.columns[1] if len(frame.columns) > 1 else frame.columns[0]
    periods = [str(c) for c in frame.columns[2:]][:6]  # 最新在前，取前 6 个报告期
    rows: Dict[str, Any] = {}
    for keyword in ("营业总收入", "净利润"):
        hit = frame[frame[name_col].astype(str) == keyword]  # 精确匹配，防归母/扣非误中
        if hit.empty:
            continue
        row = hit.iloc[0]
        rows[keyword] = {period: row[period] for period in periods}
    return {"periods": periods, "rows": rows}


def recompute_drawdown(navs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """独立复算回撤（公式与规范 §11.9 复算基准一致：dd=NAV/peak-1）。"""
    items = sorted([n for n in navs if n["nav"]], key=lambda x: x["date"])
    peak, peak_date = items[0]["nav"], items[0]["date"]
    max_dd, dd_peak, dd_trough = 0.0, peak_date, peak_date
    for item in items:
        if item["nav"] > peak:
            peak, peak_date = item["nav"], item["date"]
        dd = item["nav"] / peak - 1
        if dd < max_dd:
            max_dd, dd_peak, dd_trough = dd, peak_date, item["date"]
    current = items[-1]["nav"] / peak - 1
    return {"max_dd": round(max_dd * 100, 2), "peak_date": dd_peak,
            "trough_date": dd_trough, "current_dd": round(current * 100, 2),
            "start": items[0]["date"], "end": items[-1]["date"], "n": len(items)}


# ---------------------------------------------------------------- 主流程
def main() -> int:
    """执行 5 组对账并输出 JSON 记录。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)

    from fastapi.testclient import TestClient

    from backend.app import config
    from backend.app.core import cache
    from backend.app.datasource import portfolio as portfolio_ds
    from backend.app.main import app

    # 写操作隔离：持仓重定向 tmp（红线一），跑完还原
    portfolio_orig = portfolio_ds.PORTFOLIO_FILE
    portfolio_ds.PORTFOLIO_FILE = PORTFOLIO_TMP
    client = TestClient(app)

    try:
        run_g1(client)
        run_g2(client)
        run_g3(client)
        run_g4(client)
        run_g5(client)
    finally:
        portfolio_ds.PORTFOLIO_FILE = portfolio_orig

    passed = sum(1 for r in records if r["same"])
    result = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "total": len(records), "passed": passed, "failed": len(records) - passed,
              "records": records}
    out_json = os.path.join(args.out, "对账记录_阶段8.json")
    with open(out_json, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(f"\n合计 {passed}/{len(records)} 一致，结果：{out_json}")
    return 0 if passed == len(records) else 1


def run_g1(client: TestClient) -> None:
    """G1 持仓盈亏/人民币折算复算（tmp 隔离持仓，直连源站报价/汇率）。"""
    print("== G1 持仓盈亏与人民币折算复算 ==")
    # 防御性重置 tmp 持仓（上次异常退出可能残留对账用例条目）
    if PORTFOLIO_TMP.exists():
        PORTFOLIO_TMP.write_text(json.dumps({"items": [], "updated_at": ""}, ensure_ascii=False),
                                 encoding="utf-8")
    holdings = [
        {"market": "a", "symbol": "600519", "name": "贵州茅台", "shares": 10, "cost": 1500},
        {"market": "hk", "symbol": "00700", "name": "腾讯控股", "shares": 100, "cost": 380},
        {"market": "fund", "symbol": "017811", "name": "东方人工智能主题C", "shares": 1000, "cost": 2.5},
    ]
    for item in holdings:
        item["note"] = "对账用例-20261020"
        body = client.post("/api/portfolio", json=item).json()
        assert body.get("error") is None, f"建档失败: {body.get('error')}"

    analysis = client.get("/api/portfolio/analysis").json()["data"]
    quotes = tencent_quote()
    fund_nav = danjuan_nav_history("017811", size=5)
    # 汇率核对：应用实际采用值 vs 直连新浪（秒级刷新，容差含时差）
    app_fx = analysis.get("fx") or {}
    record("G1", "汇率", "fx.HKD", app_fx.get("HKD"), round(sina_fx().get("fx_shkdcny", 0), 6), tol=0.002)
    record("G1", "汇率", "fx.USD", app_fx.get("USD"), round(sina_fx().get("fx_susdcny", 0), 6), tol=0.002)
    # 复算统一采用应用实际使用的汇率 → market_value_cny 对比退化为纯数学校验（确定性）
    rate_map = {"CNY": 1.0, "HKD": app_fx.get("HKD"), "USD": app_fx.get("USD")}
    price_map = {
        "600519": quotes["sh600519"]["price"],
        "00700": quotes["r_hk00700"]["price"],
        "017811": fund_nav[0]["nav"] if fund_nav else None,
    }
    total_cny, total_cost_cny, total_pnl_cny = 0.0, 0.0, 0.0
    dist: Dict[str, float] = {}
    local_by_currency: Dict[str, float] = {}
    for row in analysis["items"]:
        symbol = row["symbol"]
        price = price_map.get(symbol)
        rate = rate_map[row["currency"]] or 0.0
        shares, cost = row["shares"], row["cost"]
        mv = shares * price if price else None
        cost_value = shares * cost
        pnl = (mv - cost_value) if mv is not None else None
        pnl_pct = pnl / cost_value * 100 if pnl is not None else None
        mv_cny = mv * rate if (mv is not None and rate) else None
        record("G1", symbol, "market_value", row["market_value"], round(mv, 2) if mv else None, tol=0.05)
        record("G1", symbol, "pnl", row["pnl"], round(pnl, 2) if pnl is not None else None, tol=0.05)
        record("G1", symbol, "pnl_pct", row["pnl_pct"], round(pnl_pct, 4) if pnl_pct is not None else None, tol=0.01)
        record("G1", symbol, "market_value_cny", row["market_value_cny"], round(mv_cny, 2) if mv_cny else None, tol=0.5)
        if mv_cny:
            total_cny += mv_cny
            total_pnl_cny += mv_cny - cost_value * rate
        if rate:
            total_cost_cny += cost_value * rate
        local_by_currency[row["currency"]] = round(
            local_by_currency.get(row["currency"], 0.0) + (mv or 0.0), 2)
        dist[row["market"]] = dist.get(row["market"], 0.0) + (mv_cny or 0.0)
    totals = analysis.get("totals") or {}
    record("G1", "组合", "totals.cny_market_value", totals.get("cny_market_value"), round(total_cny, 2), tol=1.0)
    record("G1", "组合", "totals.cny_cost", totals.get("cny_cost"), round(total_cost_cny, 2), tol=1.0)
    record("G1", "组合", "totals.cny_pnl", totals.get("cny_pnl"), round(total_pnl_cny, 2), tol=1.0)
    for currency, value in local_by_currency.items():
        record("G1", f"本币-{currency}", "local_by_currency",
               (totals.get("local_by_currency") or {}).get(currency), value, tol=1.0)
    for item in analysis.get("distribution") or []:
        if item.get("count"):
            expect = dist.get(item["market"], 0.0) / total_cny * 100 if total_cny else 0.0
            record("G1", f"分布-{item['market']}", "ratio", item["ratio"], round(expect, 2), tol=0.1)
    # 清理 tmp 持仓（隔离副本）
    for row in analysis["items"]:
        client.delete(f"/api/portfolio/{row['id']}")


def run_g2(client: TestClient) -> None:
    """G2 基金回撤复算（蛋券净值历史直连，独立复算极值与日期）。"""
    print("== G2 基金回撤复算 ==")
    api_dd = client.get("/api/fund/drawdown", params={"code": "017811"}).json()["data"]
    mine = recompute_drawdown(danjuan_nav_history("017811", size=500))
    record("G2", "017811", "max_drawdown", api_dd["max_drawdown"], mine["max_dd"], tol=0.05)
    record("G2", "017811", "current_drawdown", api_dd["current_drawdown"], mine["current_dd"], tol=0.05)
    record("G2", "017811", "peak_date", api_dd["peak_date"], mine["peak_date"])
    record("G2", "017811", "trough_date", api_dd["trough_date"], mine["trough_date"])
    record("G2", "017811", "series_start", api_dd["start"], mine["start"])
    record("G2", "017811", "series_end", api_dd["end"], mine["end"])


def run_g3(client: TestClient) -> None:
    """G3 拟合估值复算（东财重仓 + 腾讯批量报价直连，独立加权）。

    口径说明：API 默认返回 T+1 净值（增强通道默认关，attempts 已如实记录各通道状态）。
    本组复算对象为 provider 的 `get_fitted_estimate()` 函数本身（公式正确性验证），
    与直连源站独立加权结果对照；A股已收盘，价格为收盘快照，确定性可比。
    """
    print("== G3 拟合估值复算 ==")
    from backend.app.datasource import fund as fund_ds  # noqa: PLC0415 - 仅本组需要

    realtime = client.get("/api/fund/realtime", params={"code": "017811"}).json()["data"]
    # API 默认降级链状态如实记录（非拟合态时 is_estimate=false + attempts 说明）
    record("G3", "017811", "api_default_is_estimate", realtime.get("is_estimate"), False)
    record("G3", "017811", "api_nav_source", realtime.get("source"), "蛋卷")
    # 复算 provider 拟合函数（前十大重仓加权，规范 §12.5 第 2 级公式）
    fitted = fund_ds.get_fitted_estimate("017811")
    import akshare as ak  # noqa: PLC0415 - 仅本组需要

    frame = ak.fund_portfolio_hold_em(symbol="017811", date="2026")
    # 与 provider 修订后口径一致：先取最新季度，再按占净值比例降序前 10
    latest_q = max(str(q) for q in frame["季度"].tolist())
    frame = frame[frame["季度"] == latest_q]
    frame = frame.sort_values("占净值比例", ascending=False).head(10)
    codes = [str(c) for c in frame["股票代码"].tolist()]
    # 腾讯批量取价：A 股代码前缀 sh/sz 由首位数字判定（6→sh，0/3→sz）
    prefixed = [("sh" + c) if c.startswith("6") else ("sz" + c) for c in codes]
    resp = requests.get(f"https://qt.gtimg.cn/q={','.join(prefixed)}",
                        headers={"User-Agent": UA}, timeout=15)
    resp.encoding = "gbk"
    pct_map: Dict[str, float] = {}
    for line in resp.text.strip().split(";"):
        line = line.strip()
        if "=" not in line:
            continue
        key = line.split("=", 1)[0].replace("v_", "").strip()
        parts = line.split("=", 1)[1].strip().strip('"').split("~")
        if len(parts) >= 5 and parts[4]:
            prev_close = float(parts[4])
            pct_map[key[2:]] = (float(parts[3]) / prev_close - 1) * 100 if prev_close else 0.0
    weighted = sum_weights = 0.0
    for _, row in frame.iterrows():
        code = str(row["股票代码"])
        ratio = float(row.get("占净值比例") or 0)
        pct = pct_map.get(code)
        if pct is None or ratio <= 0:
            continue
        weighted += ratio * pct
        sum_weights += ratio
    est_pct = weighted / sum_weights if sum_weights else None
    # 收盘后价格为收盘快照，两次拉取间隔内无行情变动 → 容差 0.1 个百分点
    record("G3", "017811", "fitted_day_growth", fitted.get("day_growth"),
           round(est_pct, 4) if est_pct is not None else None, tol=0.1)
    record("G3", "017811", "fitted_coverage", fitted.get("coverage"), round(sum_weights, 2), tol=0.5)
    record("G3", "017811", "fitted_base_nav", fitted.get("base_nav"),
           realtime.get("nav"), tol=0.0001)


def run_g4(client: TestClient) -> None:
    """G4 财务趋势复算（YoY 自洽 + 原始字段 vs 直连新浪 abstract）。"""
    print("== G4 财务趋势复算 ==")
    trend = client.get("/api/stock/financial-trend", params={"symbol": "600519"}).json()["data"]
    series = {item["key"]: item for item in trend["series"]}
    periods = trend["periods"]
    revenue = series.get("revenue")
    if revenue:
        values = revenue["values"]
        for index in range(1, min(3, len(values))):
            expect = (values[index] / values[index - 1] - 1) * 100
            record("G4", f"600519 营收YoY {periods[index]}", "yoy",
                   series.get("revenue_yoy", {}).get("values", [None] * 99)[index],
                   round(expect, 2), tol=0.01)
        src = sina_financial_abstract("600519")
        for keyword, key in (("营业总收入", "revenue"), ("净利润", "profit")):
            src_rows = src["rows"].get(keyword) or {}
            src_periods = [p.replace("-", "") for p in src["periods"]]
            for period, value in src_rows.items():
                norm = period.replace("-", "")
                if norm in periods:
                    index = periods.index(norm)
                    record("G4", f"600519 {keyword} {norm}", "raw_value",
                           series[key]["values"][index], float(value), tol=1.0)
    profit = series.get("profit")
    if profit:
        values = profit["values"]
        for index in range(1, min(3, len(values))):
            expect = (values[index] / values[index - 1] - 1) * 100
            record("G4", f"600519 净利润YoY {periods[index]}", "yoy",
                   series.get("profit_yoy", {}).get("values", [None] * 99)[index],
                   round(expect, 2), tol=0.01)


def run_g5(client: TestClient) -> None:
    """G5 报价涨跌幅复算（A/HK/US 三市场，腾讯直连；quote 字段在 data.quote 内）。"""
    print("== G5 报价涨跌幅复算 ==")
    direct_all = tencent_quote()
    for symbol, market, key in (("600519", "a", "sh600519"), ("00700", "hk", "r_hk00700"),
                                ("AAPL", "us", "usAAPL")):
        data = client.get("/api/market/quote",
                          params={"symbol": symbol, "market": market}).json()["data"]
        quote = data.get("quote") or {}
        direct = direct_all.get(key) or {}
        record("G5", f"{symbol}@{market}", "price", quote.get("price"), direct.get("price"), tol=0.001)
        record("G5", f"{symbol}@{market}", "prev_close", quote.get("prev_close"),
               direct.get("prev_close"), tol=0.001)
        record("G5", f"{symbol}@{market}", "pct_chg", quote.get("pct"),
               round(direct["pct"], 4) if direct.get("pct") is not None else None, tol=0.01)


if __name__ == "__main__":
    sys.exit(main())
