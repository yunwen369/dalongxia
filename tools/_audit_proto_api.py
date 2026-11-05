# -*- coding: utf-8 -*-
"""原型 v3.4 接口打通审计：逐区块实测真实端点可用性。
用法：python tools/_audit_proto_api.py <base>
"""
import json
import sys
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:6901/api"

CASES = [
    # (原型区块, 端点, 参数)
    ("KPI情绪", "/sentiment/market", {}),
    ("KPI涨跌家数", "/market/indices", {}),
    ("新闻AI流", "/news/ai-feed", {}),
    ("板块新闻", "/news/sector-search", {"sector": "半导体"}),
    ("全局新闻", "/news/global", {}),
    ("个股新闻", "/news/stock", {"code": "300750"}),
    ("行业名录", "/sector/industry", {"market": "a"}),
    ("行业汇总(云图)", "/sector/summary", {"market": "a"}),
    ("概念名录", "/sector/concept", {"market": "a"}),
    ("板块资金流", "/sector/fundflow", {"market": "a"}),
    ("板块K线一级", "/sector/kline", {"board": "半导体", "period": "daily"}),
    ("板块K线二级", "/sector/kline", {"board": "半导体材料", "period": "daily"}),
    ("板块成分", "/sector/cons", {"board": "半导体"}),
    ("板块拥挤度", "/sector/crowding", {"board": "半导体"}),
    ("拥挤排行", "/sector/crowding/rankings", {}),
    ("板块趋势", "/sector/trend", {"boards": "半导体,白酒", "days": 30}),
    ("个股面板", "/stock/realtime-panel", {"code": "300750"}),
    ("个股K线", "/market/kline", {"code": "300750", "period": "daily"}),
    ("个股估值", "/valuation/percentile", {"code": "300750"}),
    ("个股财务", "/stock/financial-summary", {"code": "300750"}),
    ("FCF背离", "/stock/fcf-health", {"code": "300750"}),
    ("同业对比", "/stock/peer-compare", {"code": "300750"}),
    ("个股资金流", "/moneyflow/stock", {"code": "300750"}),
    ("龙虎榜", "/moneyflow/lhb", {}),
    ("宏观", "/macro/overview", {}),
    ("市场情绪股", "/sentiment/stock", {"code": "300750"}),
    ("基金档案", "/fund/info", {"code": "021179"}),
    ("基金净值", "/fund/nav", {"code": "021179"}),
    ("基金业绩排名", "/fund/performance", {"code": "021179"}),
    ("基金持仓", "/fund/holding", {"code": "021179"}),
    ("基金回撤", "/fund/drawdown", {"code": "021179"}),
    ("基金行业配置", "/fund/position", {"code": "021179"}),
    ("基金实时", "/fund/realtime", {"code": "021179"}),
    ("基金分红", "/fund/dividend", {"code": "021179"}),
    ("组合", "/portfolio", {}),
    ("组合归因", "/portfolio/attribution", {}),
    ("组合风险", "/portfolio/risk", {}),
    ("筛选器", "/screener/run", {}),
    ("公告检索", "/news/financial-search", {"q": "宁德时代"}),
    ("自选", "/watchlist", {}),
    ("提醒", "/alerts", {}),
    ("AI配置", "/ai/config", {}),
    ("仪表盘", "/dashboards", {}),
    ("指数K线", "/market/index-kline", {"code": "sh000001", "period": "daily"}),
]


def hit(path, params):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "audit/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            body = r.read(4000).decode("utf-8", "replace")
        try:
            j = json.loads(body)
            ok = j.get("ok", j.get("success"))
            return r.status, ("OK" if ok is not False else "BIZ-FAIL"), body[:120]
        except Exception:
            return r.status, "NONJSON", body[:120]
    except Exception as e:
        return 0, "ERR", str(e)[:120]


rows = []
for name, path, params in CASES:
    st, flag, sample = hit(path, params)
    rows.append((name, path, st, flag, sample))
    print(f"{flag:9s} {st:3d} {path:34s} {name}")

ok_n = sum(1 for r in rows if r[3] == "OK")
print(f"\n== {ok_n}/{len(rows)} OK ==")
for r in rows:
    if r[3] != "OK":
        print(" FAIL:", r[0], r[1], r[4])
