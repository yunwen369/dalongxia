#!/usr/bin/env python3
"""全页可见文本泄漏扫描：英文键名 / undefined / 错误码 / 阶段水印。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:  # 第十轮：verify_frontier_fixes 已并入 ui_clickthrough，保留旧导入以兼容历史环境
    from verify_frontier_fixes import CDP, launch_chrome, page_ws, set_viewport, wait_for_page  # noqa: E402
except ModuleNotFoundError:
    from ui_clickthrough import CDP, launch_chrome, page_ws, set_viewport, eval_js  # noqa: E402

    def wait_for_page(cdp, route: str, timeout: int = 45) -> bool:
        """等待页面就绪（原 verify_frontier_fixes.wait_for_page 的最小替代）。

        Args:
            cdp: CDP 客户端。
            route: 路由标识（仅日志用）。
            timeout: 超时秒数。

        Returns:
            bool: 就绪返回 True，超时 False。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if eval_js(cdp, "document.readyState") == "complete":
                    return True
            except Exception:  # noqa: BLE001 - 导航中取值失败属正常
                pass
            time.sleep(0.5)
        return False

ROUTES = [
    ("home", "#/home"),
    ("dashboard", "#/dashboard"),
    ("charting", "#/charting?symbol=688012&market=a"),
    ("news", "#/news"),
    ("sectors", "#/sectors"),
    ("stock", "#/stock?symbol=688012&market=a"),
    ("stock-empty", "#/stock?symbol=600519&market=a"),
    ("sector-val", "#/sector-val?board=%E5%8D%8A%E5%AF%BC%E4%BD%93&market=cn"),
    ("fund", "#/fund?code=017811"),
    ("portfolio", "#/portfolio"),
    ("yjb", "#/yjb"),
    ("connectors", "#/connectors"),
    ("settings", "#/settings"),
]

SCAN = r"""
(function(){
  var text=document.body.innerText||'';
  var keyRe=/\b(?:nav|page|stock|fund|val|sectors|news|dashboard|state|value|action|mk|ui|error|palette|export|health|sys|market|yjb|portfolio|sidebar|copilot|search|charting)\.[A-Za-z][A-Za-z0-9_.]*/g;
  // ② 裸英文指标键（R2-07 漏报根因）：接口对象键名被直接当标签时，界面上只有
  //    scope/total/positive 等单词，没有前缀可匹配。按「键名簇」判定：同页出现
  //    ≥2 个已知英文指标键才判泄漏，避免新闻标题里的单个英文词误报。
  var bareWords=['scope','total','positive','negative','neutral','level','temperature',
    'mean_score','net_inflow','inflow','outflow','lead_stock','lead_pct','pnl_pct','pnl',
    'pe_ttm','pb','turnover','prev_close','float_market_cap','total_market_cap',
    'cny_market_value','cny_cost','cny_pnl','market_value','market_label','super_big','big'];
  var bareHits=bareWords.filter(function(word){
    return new RegExp('(^|[^A-Za-z0-9_])'+word+'([^A-Za-z0-9_]|$)').test(text);
  });
  // ③ 通用 snake_case 键（mean_score / cny_xxx）：同样按簇判定，≥2 个才报
  var snakeHits=(text.match(/(^|[^A-Za-z0-9_])[a-z][a-z0-9]*(?:_[a-z0-9]+)+(?=[^A-Za-z0-9_]|$)/g)||[])
    .map(function(v){return v.replace(/^[^A-Za-z]+/,'');})
    .filter(function(v,i,arr){return arr.indexOf(v)===i;});
  var badRe=/undefined|NaN|\[object |ERR_[A-Z_]+|UPSTREAM_|INVALID_PARAM|HTTP_\d{3}|阶段\s*\d|后续接入|占位/g;
  return {
    keys:(text.match(keyRe)||[]).slice(0,10),
    bare:(bareHits.length>=2?bareHits:[]).slice(0,10),
    snake:(snakeHits.length>=2?snakeHits:[]).slice(0,10),
    bad:(text.match(badRe)||[]).slice(0,10),
    title:(document.getElementById('page-title')||{innerText:''}).innerText.trim(),
    len:text.length
  };
})()
"""


# 允许出现的 snake_case 文本：连接器能力矩阵会如实展示数据源函数名（D3 缺口说明），
# 属产品有意展示的技术标识，不算键名泄漏。新增此类展示需同步登记到本白名单。
SNAKE_ALLOW = {
    "stock_news_em", "stock_hk_news", "stock_us_news",
    "stock_lhb_em", "stock_hsgt_hist_em", "sw_index_third_cons",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:6912")
    parser.add_argument("--out", required=True)
    parser.add_argument("--port", type=int, default=9372)
    args = parser.parse_args()

    profile = tempfile.mkdtemp(prefix="chrome_scan_")
    proc = launch_chrome(args.port, profile, 1366, 768)
    rows = {}
    try:
        cdp = CDP(page_ws(args.port))
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Network.enable")
        cdp.call("Network.setCacheDisabled", {"cacheDisabled": True})
        set_viewport(cdp, 1366, 768, 1.0)
        for name, route in ROUTES:
            cdp.call("Page.navigate", {"url": args.base_url + "/" + route})
            wait_for_page(cdp, route, 45)
            time.sleep(4.5)
            rows[name] = eval_js(cdp, SCAN)
        pass  # CDP 无 close()，连接随进程结束释放
    finally:
        proc.kill()

    os.makedirs(args.out, exist_ok=True)
    for row in rows.values():
        if row and row.get("snake"):
            row["snake"] = [item for item in row["snake"] if item not in SNAKE_ALLOW]
    path = os.path.join(args.out, "leak_scan.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=2)
    bad = {k: v for k, v in rows.items()
           if (v or {}).get("keys") or (v or {}).get("bad")
           or (v or {}).get("bare") or (v or {}).get("snake")}
    print(json.dumps(bad, ensure_ascii=False, indent=2) if bad else "CLEAN：13 个页面均无键名/裸英文指标键/错误码/阶段水印泄漏")
    print(f"结果：{path}")


if __name__ == "__main__":
    main()
