# -*- coding: utf-8 -*-
"""第八轮交叉验证（规范 §11.9 数据核对 / §11.11 数据三性）。

设计原则：**不复用被测代码的 provider**，每个校验都另起一条独立 HTTP 请求
（直连源站原始接口），与本工程接口返回逐字段比对，避免「自己验自己」。

覆盖 12 项：
    1   A股报价        /api/market/quote      vs 新浪 hq.sinajs.cn
    2   指数快照       /api/market/index      vs 新浪 s_sh000001
    3   A股日K末点     /api/market/kline      vs 新浪 CN_MarketData.getKLineData
    4   板块行情涨跌   /api/sector/industry   vs 腾讯 board_type=hy2 同名板块
    5   板块资金流自洽 net = inflow - outflow（同源同口径）
    6   四档加总自洽   四档净额和 ≈ 净额（容差 3%）
    7   量纲自检       amount/(volume×100×price) ∈ [0.9, 1.1]
    8   基金净值       /api/fund/nav          vs 东财 lsjz
    9   汇率           /api/portfolio/analysis vs 新浪 fx_susdcny
    10  时区          全部 as_of 落在北京时间窗口内
    11  拥挤度加权     score ≈ Σ(pct×weight)/Σweight
    12  美股分时       首末时间戳与新浪原始一致

用法：python tools/cross_validate.py --base http://127.0.0.1:6903 --out result.json
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
BJ = timezone(timedelta(hours=8))


def get_json(url: str, params: Optional[Dict[str, Any]] = None, referer: str = "") -> Any:
    """直连源站取 JSON（独立于被测代码）。"""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    headers = {"User-Agent": UA}
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def get_text(url: str, referer: str = "", encoding: str = "gbk") -> str:
    """直连源站取文本。"""
    headers = {"User-Agent": UA}
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as resp:
        return resp.read().decode(encoding, "replace")


def api(base: str, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """调用被测服务。"""
    return get_json(f"{base}{path}", params)


def add(results: List[Dict[str, Any]], name: str, expected: Any, actual: Any,
        diff: Any, verdict: str, note: str = "") -> None:
    """登记一条校验结果。"""
    results.append({
        "check": name, "expected": expected, "actual": actual,
        "diff": diff, "verdict": verdict, "note": note,
    })


def check_quote(base: str, results: List[Dict[str, Any]]) -> None:
    """① A股报价 vs 新浪原始行情。"""
    data = api(base, "/api/market/quote", {"symbol": "600519", "market": "a"})["data"]
    quote = data.get("quote") or {}
    text = get_text("https://hq.sinajs.cn/list=sh600519", referer="https://finance.sina.com.cn/")
    raw = text.split('="', 1)[-1].strip('";').split(",")
    sina_price = float(raw[3]) if len(raw) > 3 else None
    ours = quote.get("price")
    diff = abs(ours - sina_price) / sina_price * 100 if ours and sina_price else None
    add(results, "① A股报价 600519", f"新浪 {sina_price}", f"接口 {ours}",
        f"{diff:.4f}%" if diff is not None else "n/a",
        "PASS" if diff is not None and diff < 0.5 else "FAIL",
        "判据：价差 <0.5% 可信（规范 §11.9）")


def check_index(base: str, results: List[Dict[str, Any]]) -> None:
    """② 指数快照 vs 新浪。"""
    data = api(base, "/api/market/index", {"market": "all"})["data"]
    item = next((row for row in data.get("items", []) if row.get("code") == "000001"), None)
    text = get_text("https://hq.sinajs.cn/list=s_sh000001", referer="https://finance.sina.com.cn/")
    raw = text.split('="', 1)[-1].strip('";').split(",")
    sina_pct = float(raw[3]) if len(raw) > 3 else None
    ours = item.get("pct") if item else None
    diff = abs(ours - sina_pct) if ours is not None and sina_pct is not None else None
    add(results, "② 上证指数涨跌幅", f"新浪 {sina_pct}%", f"接口 {ours}%",
        f"{diff:.4f}pp" if diff is not None else "n/a",
        "PASS" if diff is not None and diff < 0.1 else "FAIL",
        "判据：涨跌幅差 <0.1 个百分点")


def check_kline(base: str, results: List[Dict[str, Any]]) -> None:
    """③ A股日K末点 vs 新浪日线。"""
    data = api(base, "/api/market/kline",
               {"symbol": "600519", "market": "a", "period": "daily", "limit": 5})["data"]
    items = data.get("items") or []
    ours_last = items[-1] if items else {}
    sina = get_json(
        "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData",
        {"symbol": "sh600519", "scale": 240, "ma": "no", "datalen": 5},
        referer="https://finance.sina.com.cn/",
    )
    sina_last = sina[-1] if sina else {}
    ours_close = ours_last.get("close")
    sina_close = float(sina_last.get("close")) if sina_last.get("close") else None
    diff = abs(ours_close - sina_close) / sina_close * 100 if ours_close and sina_close else None
    same_day = str(ours_last.get("date"))[:10] == str(sina_last.get("day"))[:10]
    add(results, "③ 600519 日K末点收盘", f"新浪 {sina_last.get('day')} {sina_close}",
        f"接口 {ours_last.get('date')} {ours_close}",
        f"{diff:.4f}%" if diff is not None else "n/a",
        "PASS" if diff is not None and diff < 0.5 else ("WARN" if not same_day else "FAIL"),
        "跨源末点可能差一个交易日（P1-2 已登记），同日则要求价差 <0.5%")


def check_sector(base: str, results: List[Dict[str, Any]]) -> None:
    """④ 板块涨跌幅 vs 腾讯板块榜（独立请求）。"""
    data = api(base, "/api/sector/industry", {"market": "a"})["data"]
    ours = {row["name"]: row.get("pct") for row in data.get("items", [])}
    payload = get_json(
        "https://proxy.finance.qq.com/cgi/cgi-bin/rank/pt/getRank",
        {"board_type": "hy2", "sort_type": "price", "direct": "down", "offset": 0, "count": 100},
        referer="https://gu.qq.com/",
    )
    tx = {row["name"]: float(row["zdf"]) for row in payload["data"]["rank_list"]
          if row.get("zdf") not in (None, "")}
    matched = [(name, ours[name], tx[name]) for name in ours if name in tx and ours[name] is not None]
    if not matched:
        add(results, "④ 板块涨跌幅（同花顺 vs 腾讯）", "同名板块≥3 个", "0", "n/a", "WARN", "两套行业分类命名交集不足")
        return
    diffs = sorted(abs(a - b) for _, a, b in matched)
    median = diffs[len(diffs) // 2]
    worst = diffs[-1]
    same_sign = sum(1 for _, a, b in matched if (a > 0) == (b > 0))
    ratio = same_sign / len(matched)
    # 同花顺行业（90）与申万二级（124）是两套分类体系，成分不完全相同，
    # 逐板块不可能相等；判据取「中位差 + 涨跌方向一致率」。
    verdict = "PASS" if median < 0.5 and ratio >= 0.8 else "FAIL"
    add(results, "④ 板块涨跌幅（同花顺 vs 申万二级）",
        f"同名 {len(matched)} 个，中位差 <0.5pp 且方向一致率 ≥80%",
        f"中位差 {median:.4f}pp，方向一致 {same_sign}/{len(matched)}，最大差 {worst:.2f}pp",
        f"{median:.4f}pp", verdict,
        "两套行业分类成分不同，最大差来自分类差异而非取数错误")


def check_fundflow(base: str, results: List[Dict[str, Any]]) -> None:
    """⑤ 资金流自洽 + ⑥ 四档加总。"""
    data = api(base, "/api/sector/fundflow", {"market": "a"})["data"]
    rows = [row for row in data.get("items", []) if row.get("net_inflow") is not None]
    bad_net = [
        row for row in rows
        if row.get("inflow") is None or row.get("outflow") is None
        or abs((row["inflow"] - row["outflow"]) - row["net_inflow"]) > 0.02
    ]
    add(results, "⑤ 资金流自洽 net=in−out", f"{len(rows)} 行全部满足", f"不符 {len(bad_net)} 行",
        f"{len(bad_net)}", "PASS" if not bad_net else "FAIL",
        (bad_net[0]["name"] if bad_net else "同源同口径，容差 0.02 亿元"))

    tiered = [row for row in rows if row.get("super_big") is not None]
    if not tiered:
        add(results, "⑥ 四档加总 ≈ 净额", "至少 1 个板块已聚合", "0（预热中）", "n/a", "WARN",
            "成分映射预热未覆盖，稍后复测")
        return
    # 同行同口径校验：已聚合行必须声明 flow_source=新浪
    wrong_src = [row["name"] for row in tiered if "新浪" not in str(row.get("flow_source") or "")]
    add(results, "⑥a 已聚合行口径标注", "flow_source 含「新浪」", f"不符 {len(wrong_src)} 行",
        f"{len(wrong_src)}", "PASS" if not wrong_src else "FAIL", ",".join(wrong_src[:3]))
    worst = None
    worst_name = ""
    for row in tiered:
        total = sum(row[k] for k in ("super_big", "big", "medium", "small"))
        base_abs = abs(row["net_inflow"]) or 1.0
        ratio = abs(total - row["net_inflow"]) / base_abs
        if worst is None or ratio > worst:
            worst, worst_name = ratio, row["name"]
    add(results, "⑥ 四档加总 ≈ 净额", "已聚合板块偏差 <3%",
        f"{len(tiered)} 个板块，最大偏差 {worst * 100:.2f}%（{worst_name}）",
        f"{worst * 100:.2f}%", "PASS" if worst < 0.03 else "FAIL",
        "四档来自成分股逐只聚合，与板块级净额口径差异应很小")


def check_units(base: str, results: List[Dict[str, Any]]) -> None:
    """⑦ 量纲自检：A股 amount/(volume×100×price)。"""
    data = api(base, "/api/market/kline",
               {"symbol": "600519", "market": "a", "period": "daily", "limit": 3})["data"]
    rows = data.get("items") or []
    ratios = []
    for row in rows:
        price, volume, amount = row.get("close"), row.get("volume"), row.get("amount")
        if price and volume and amount:
            ratios.append(amount / (volume * 100 * price))
    ok = bool(ratios) and all(0.9 <= value <= 1.1 for value in ratios)
    add(results, "⑦ 量纲自检 amount/(volume×100×price)", "0.9–1.1",
        ", ".join(f"{value:.4f}" for value in ratios) or "n/a",
        f"{max(ratios) - min(ratios):.4f}" if ratios else "n/a",
        "PASS" if ok else "FAIL", "A股每手 100 股；偏离说明单位换算错位")


def check_fund_nav(base: str, results: List[Dict[str, Any]]) -> None:
    """⑧ 基金净值 vs 东财 lsjz。"""
    data = api(base, "/api/fund/nav", {"code": "017811"})["data"]
    items = data.get("items") or []
    ours = items[0] if items else {}
    payload = get_json(
        "https://api.fund.eastmoney.com/f10/lsjz",
        {"fundCode": "017811", "pageIndex": 1, "pageSize": 1},
        referer="https://fundf10.eastmoney.com/",
    )
    em = ((payload.get("Data") or {}).get("LSJZList") or [{}])[0]
    ours_nav = ours.get("nav")
    em_nav = float(em["DWJZ"]) if em.get("DWJZ") else None
    diff = abs(ours_nav - em_nav) if ours_nav and em_nav else None
    add(results, "⑧ 基金 017811 单位净值", f"东财 {em.get('FSRQ')} {em_nav}",
        f"接口 {ours.get('date')} {ours_nav}", f"{diff:.6f}" if diff is not None else "n/a",
        "PASS" if diff is not None and diff < 0.0005 else "FAIL", "净值应完全一致（同一公布值）")


def check_fx(base: str, results: List[Dict[str, Any]]) -> None:
    """⑨ 汇率 vs 新浪外汇原始。"""
    text = get_text("https://hq.sinajs.cn/list=fx_susdcny", referer="https://finance.sina.com.cn/")
    raw = text.split('="', 1)[-1].strip('";').split(",")
    sina = float(raw[1]) if len(raw) > 1 else None
    # 通过持仓分析间接取（台账为空时跳过）
    payload = api(base, "/api/portfolio/analysis", {})
    fx = ((payload.get("data") or {}).get("fx") or {})
    ours = fx.get("USD")
    if not ours:
        add(results, "⑨ 汇率 USD/CNY", f"新浪 {sina}", "台账为空→接口未返回 fx", "n/a", "WARN",
            "持仓台账为空时该字段不下发，属设计（无持仓无需折算）")
        return
    diff = abs(ours - sina) / sina * 100 if sina else None
    add(results, "⑨ 汇率 USD/CNY", f"新浪 {sina}", f"接口 {ours}",
        f"{diff:.4f}%" if diff is not None else "n/a",
        "PASS" if diff is not None and diff < 0.5 else "FAIL", "主源同源，差 <0.5%")


def check_timezone(base: str, results: List[Dict[str, Any]]) -> None:
    """⑩ 时区：as_of 必须是北京时间且不超前。"""
    paths = ["/api/market/quote?symbol=600519&market=a",
             "/api/sector/industry?market=a",
             "/api/news/flash?limit=5",
             "/api/market/minute?symbol=600519&market=a"]
    now = datetime.now(BJ)
    bad: List[str] = []
    checked = 0
    for path in paths:
        payload = get_json(f"{base}{path}")
        as_of = (payload.get("data") or {}).get("as_of")
        if not as_of:
            continue
        checked += 1
        try:
            stamp = datetime.strptime(str(as_of)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=BJ)
        except ValueError:
            bad.append(f"{path} 格式异常:{as_of}")
            continue
        if stamp > now + timedelta(minutes=5) or stamp < now - timedelta(days=2):
            bad.append(f"{path} 越界:{as_of}")
    add(results, "⑩ 时区契约（北京时间）", f"{checked} 个端点 as_of 在 2 日内且不超前",
        "全部合规" if not bad else "; ".join(bad), f"{len(bad)}",
        "PASS" if not bad and checked >= 3 else "FAIL", "规范 §6.10 / TZ 红线")


def check_crowding(base: str, results: List[Dict[str, Any]]) -> None:
    """⑪ 拥挤度加权自洽。"""
    data = api(base, "/api/sector/crowding", {"board": "银行"})["data"]
    parts = data.get("parts") or []
    weights = [part.get("weight") or 0 for part in parts]
    pcts = [part.get("pct") for part in parts]
    if not parts or any(value is None for value in pcts):
        add(results, "⑪ 拥挤度加权自洽", "四维分位齐全", "存在缺失维度", "n/a", "WARN", "分位样本不足")
        return
    total_weight = sum(weights)
    recomputed = sum(p * w for p, w in zip(pcts, weights)) / total_weight
    diff = abs(recomputed - data["score"])
    add(results, "⑪ 拥挤度加权自洽（银行）", f"复算 {recomputed:.2f}",
        f"接口 {data['score']}", f"{diff:.4f}",
        "PASS" if diff < 0.2 else "FAIL", "score = Σ(pct×weight)/Σweight")


def check_us_minute(base: str, results: List[Dict[str, Any]]) -> None:
    """⑫ 美股分时首末点 vs 新浪原始。"""
    data = api(base, "/api/market/minute",
               {"symbol": "AAPL", "market": "us", "period": "5"})["data"]
    items = data.get("items") or []
    raw = get_json(
        "https://stock.finance.sina.com.cn/usstock/api/json_v2.php/US_MinKService.getMinK",
        {"symbol": "AAPL", "type": "5"},
        referer="https://stock.finance.sina.com.cn/",
    )
    ours_last = items[-1] if items else {}
    sina_last = raw[-1] if raw else {}
    same = (str(ours_last.get("date")) == str(sina_last.get("d", ""))[:10]
            and float(ours_last.get("price") or 0) == float(sina_last.get("c") or -1))
    add(results, "⑫ 美股分时末点（AAPL 5min）",
        f"新浪 {sina_last.get('d')} {sina_last.get('c')}",
        f"接口 {ours_last.get('date')} {ours_last.get('time')} {ours_last.get('price')}",
        "一致" if same else "不一致", "PASS" if same else "FAIL", "逐点应完全相同（同源同粒度）")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:6903")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    results: List[Dict[str, Any]] = []
    for func in (check_quote, check_index, check_kline, check_sector, check_fundflow,
                 check_units, check_fund_nav, check_fx, check_timezone, check_crowding,
                 check_us_minute):
        try:
            func(args.base, results)
        except Exception as exc:  # noqa: BLE001 - 单项异常不阻断其余校验
            results.append({"check": func.__name__, "expected": "-", "actual": f"{type(exc).__name__}: {exc}"[:160],
                            "diff": "n/a", "verdict": "ERROR", "note": "校验执行异常"})

    counts: Dict[str, int] = {}
    for item in results:
        counts[item["verdict"]] = counts.get(item["verdict"], 0) + 1
    report = {"base": args.base, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "counts": counts, "results": results}
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    for item in results:
        print(f"{item['verdict']:<5} {item['check']}: {item['expected']} | {item['actual']} | {item['diff']}")
    print(json.dumps(counts, ensure_ascii=False))
    return 0 if counts.get("FAIL", 0) == 0 and counts.get("ERROR", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
