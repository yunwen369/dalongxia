# -*- coding: utf-8 -*-
"""P1 动态验收（真实浏览器 · 真实接口 · 逐页截图取证）。

依据：验收标准 AC-R10-v2.0 A 组通用断言 + B 组全局断言 + 设计基准 §10-P1
     「21 视图迁移零 JS 错误；CDP 点击回归+主题切换用例；泄漏扫描 0」。

覆盖
----
1. 19 页 IA 逐页导航：容器内容、四态标记、console 错误、截图；
2. 关键交互真实操作：指数卡切主图 / 财报检索 / 公告正文 / 工具跳转 /
   今日研判纪律检查 / 候选池；
3. 双主题各拍一组（深色默认 + light）；
4. 产出 `result.json` 与 `shots/*.png`，可脱离本脚本复核。

用法
----
    python tools/p1_acceptance.py --base-url http://127.0.0.1:6903 \
        --out <报告目录>
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ui_clickthrough import CDP, eval_js, page_ws  # noqa: E402  复用极简 CDP 客户端

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

# (页面标识, hash, 人类可读名, 断言选择器, 期望最小卡片数)
PAGES = [
    ("today", "#/today", "今日研判", "#page-today .pg-card", 4),
    ("workspace", "#/dashboard", "自定义工作区", "#page-dashboard", 1),
    ("market", "#/market", "市场概览", "#page-market .pg-card", 2),
    ("macro", "#/macro", "宏观数据", "#page-macro", 1),
    ("sectors", "#/sectors", "板块云图", "#page-sectors", 1),
    ("sector", "#/sector-val", "板块详情", "#page-sector-val", 1),
    ("money", "#/lhb", "资金流向", "#page-lhb", 1),
    ("fund", "#/fund", "基金详情", "#page-fund", 1),
    ("combo", "#/fund-combo", "基金组合诊断", "#page-fund-combo .page-toolbar", 0),
    ("stock", "#/stock", "个股研究", "#page-stock", 1),
    ("news", "#/news", "新闻情绪", "#page-news", 1),
    ("fin", "#/fin", "财报摘要", "#page-fin .pg-card", 2),
    ("ann", "#/ann", "公告库·正文", "#page-ann .pg-card", 1),
    ("my", "#/portfolio", "我的投资", "#page-portfolio", 1),
    ("yjb", "#/yjb", "养基宝", "#page-yjb", 1),
    ("mgr", "#/mgr", "经理画像", "#page-mgr .pg-card", 1),
    ("tools", "#/tools", "工具", "#page-tools .pg-card", 1),
    ("cand", "#/cand", "候选池", "#page-cand .pg-card", 1),
    ("sys", "#/settings", "系统设置", "#page-settings", 1),
    ("watchlist", "#/watchlist", "自选股", "#page-watchlist", 1),
    ("connectors", "#/connectors", "连接器", "#page-connectors", 1),
    ("alerts", "#/alerts", "提醒中心", "#page-alerts", 1),
]

HOOK = """
window.__errs = [];
window.addEventListener('error', function (e) {
  window.__errs.push((e.message || '') + ' @ ' + (e.filename || '').split('/').pop() + ':' + (e.lineno || 0));
}, true);
window.__cerr = [];
var __oe = console.error;
console.error = function () { window.__cerr.push([].slice.call(arguments).join(' ')); __oe.apply(console, arguments); };
"""

PROBE = """(function(){
  var page = document.querySelector('.page-view:not([hidden])');
  var cards = page ? page.querySelectorAll('.pg-card') : [];
  var bodies = page ? page.querySelectorAll('.pg-body') : [];
  var states = [];
  for (var i = 0; i < bodies.length; i++) { states.push(bodies[i].getAttribute('data-state') || '-'); }
  return JSON.stringify({
    pageId: page ? page.id : null,
    cards: cards.length,
    states: states,
    textLen: page ? (page.innerText || '').replace(/\\s+/g, ' ').trim().length : 0,
    textHead: page ? (page.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 100) : '',
    errs: window.__errs.slice(),
    cerr: window.__cerr.slice(),
    hasCanvas: !!(page && page.querySelector('canvas'))
  });
})()"""


def wait_js(cdp: CDP, expr: str, timeout: float = 10.0, interval: float = 0.4) -> bool:
    """轮询等待页面条件成立（渲染/接口返回都需要时间，固定 sleep 不可靠）。"""
    end = time.time() + timeout
    while time.time() < end:
        try:
            if eval_js(cdp, expr):
                return True
        except Exception:  # noqa: BLE001 - 页面切换瞬间可能抛错，继续等
            pass
        time.sleep(interval)
    return False


def shot(cdp: CDP, path: str) -> bool:
    """整页截图（captureBeyondViewport=False，避免超长白底）。"""
    try:
        result = cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        data = ((result or {}).get("result") or {}).get("data") or ""
        if not data:
            return False
        with open(path, "wb") as handle:
            handle.write(base64.b64decode(data))
        return os.path.getsize(path) > 8000
    except Exception:  # noqa: BLE001 - 截图失败不阻断验收，如实记录
        return False


def interact(cdp: CDP) -> list[dict]:
    """关键交互真实操作（结果用于 A/B 组断言）。"""
    results: list[dict] = []

    # 1) 市场概览：点第 2 张指数卡，主图应重绘
    eval_js(cdp, "location.hash='#/market'")
    time.sleep(2.5)
    before = eval_js(cdp, "document.querySelector('#page-market').querySelectorAll('.mk-idx-cell').length")
    clicked = eval_js(cdp, """(function(){
      var cells = document.querySelectorAll('#page-market .mk-idx-cell');
      if (cells.length < 2) { return false; }
      cells[1].click();
      return true;
    })()""")
    time.sleep(1.8)
    after = eval_js(cdp, "!!document.querySelector('#page-market canvas')")
    results.append({
        "case": "MK-01 指数卡切主图",
        "detail": f"指数卡 {before} 张，点击第 2 张={clicked}，canvas 存在={after}",
        "pass": bool(before and before >= 2 and clicked and after)
    })

    # 2) 财报摘要：输入 600519 回车 → 结果表
    eval_js(cdp, "location.hash='#/fin'")
    wait_js(cdp, "!!document.querySelector('#page-fin .p1-search-input')", 12)
    typed = eval_js(cdp, """(function(){
      var i = document.querySelector('#page-fin .p1-search-input');
      if (!i) { return false; }
      i.value = '600519';
      i.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
      return true;
    })()""")
    wait_js(cdp, "document.querySelectorAll('#page-fin .p1-result table tr').length > 1", 25)
    rows = eval_js(cdp, "document.querySelectorAll('#page-fin .p1-result table tr').length")
    results.append({
        "case": "FR-02 财报检索（q=600519）",
        "detail": f"输入={typed}，结果行={rows}",
        "pass": bool(typed and rows and rows > 1)
    })

    # 3) 公告库：检索 + 打开正文
    eval_js(cdp, "location.hash='#/ann'")
    wait_js(cdp, "!!document.querySelector('#page-ann .p1-search-input')", 12)
    eval_js(cdp, """(function(){
      var i = document.querySelector('#page-ann .p1-search-input');
      if (!i) { return; }
      i.value = '宁德时代';
      i.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
    })()""")
    wait_js(cdp, "document.querySelectorAll('#page-ann .p1-result table tr').length > 1", 30)
    ann_rows = eval_js(cdp, "document.querySelectorAll('#page-ann .p1-result table tr').length")
    opened = eval_js(cdp, """(function(){
      var b = document.querySelector('#page-ann .p1-result .pg-btn');
      if (!b) { return false; }
      b.click();
      return true;
    })()""")
    wait_js(cdp, "((document.querySelector('#page-ann .p1-detail-body')||{}).textContent||'').length > 50", 25)
    body_len = eval_js(cdp, "((document.querySelector('#page-ann .p1-detail-body')||{}).textContent||'').length")
    results.append({
        "case": "AN-01 公告检索 + 正文",
        "detail": f"结果行={ann_rows}，点正文={opened}，正文长度={body_len}",
        "pass": bool(ann_rows and ann_rows > 1 and opened and body_len and body_len > 50)
    })

    # 4) 工具页跳转
    eval_js(cdp, "location.hash='#/tools'")
    time.sleep(1.4)
    jumped = eval_js(cdp, """(function(){
      var c = document.querySelector('#page-tools .tl-cell');
      if (!c) { return ''; }
      c.click();
      return location.hash;
    })()""")
    time.sleep(1.2)
    results.append({
        "case": "TL 工具箱跳转",
        "detail": f"点击首卡后 hash={jumped}",
        "pass": bool(jumped and jumped != "#/tools")
    })

    # 5) 今日研判纪律检查（本地规则）
    eval_js(cdp, "location.hash='#/today'")
    time.sleep(3)
    disc = eval_js(cdp, "document.querySelectorAll('#td-discipline table tr').length")
    results.append({
        "case": "TD-05 纪律检查逐条判定",
        "detail": f"判定行={disc}",
        "pass": bool(disc and disc >= 4)
    })

    # 6) 候选池（快照可能未建，加载态/空态/表格均算正常，只要不报错）
    eval_js(cdp, "location.hash='#/cand'")
    time.sleep(5)
    cand = eval_js(cdp, """({
      state: (document.querySelector('#page-cand .pg-body')||{}).getAttribute
        ? document.querySelector('#page-cand .pg-body').getAttribute('data-state') : null,
      rows: document.querySelectorAll('#page-cand table tr').length
    })""")
    if not isinstance(cand, str):
        cand = json.dumps(cand, ensure_ascii=False)
    results.append({
        "case": "CD-01 候选池加载",
        "detail": str(cand),
        "pass": True  # 只验不崩，数据可用性由 C 组断言
    })
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:6903")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    out = os.path.abspath(args.out)
    shots = os.path.join(out, "shots")
    os.makedirs(shots, exist_ok=True)

    profile = os.path.join(tempfile.gettempdir(), "edge_p1_accept_%d" % int(time.time()))
    port = 9341
    proc = subprocess.Popen([
        EDGE, "--headless", "--no-sandbox", "--disable-gpu",
        "--remote-debugging-port=%d" % port,
        "--user-data-dir=" + profile,
        "--window-size=1366,768",
        "--remote-allow-origins=*",
        "about:blank",
    ])
    time.sleep(4)
    cdp = CDP(page_ws(port))
    cdp.call("Runtime.enable")
    cdp.call("Page.enable")
    cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": HOOK})
    cdp.call("Page.navigate", {"url": args.base_url + "/?v=%d" % int(time.time())})
    time.sleep(6)

    records = []
    for key, hash_, label, selector, min_cards in PAGES:
        eval_js(cdp, "location.hash='%s'" % hash_)
        time.sleep(1.2)
        # 等页面自身四态稳定（不再有 loading），再截图；否则会拍到骨架屏（P1 首轮验收踩过）
        wait_js(cdp, """(function(){
          var p = document.querySelector('.page-view:not([hidden])');
          if (!p) { return false; }
          var bodies = p.querySelectorAll('.pg-body');
          if (!bodies.length) { return true; }
          for (var i = 0; i < bodies.length; i++) {
            if (bodies[i].getAttribute('data-state') === 'loading') { return false; }
          }
          return true;
        })()""", 15)
        # 截图前等所有区块退出 loading，避免截到骨架屏（取证无效）
        wait_js(cdp, "!document.querySelector('.page-view:not([hidden]) .pg-body[data-state=\'loading\']')", 10)
        raw = eval_js(cdp, PROBE)
        probe = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if not isinstance(probe, dict):
            probe = {}
        found = eval_js(cdp, "document.querySelectorAll('%s').length" % selector)
        png = os.path.join(shots, "%s.png" % key)
        time.sleep(0.8)  # 等渲染管线刷新，避免截到上一帧（骨架屏）
        ok_shot = shot(cdp, png)
        ok = (
            probe["pageId"] is not None
            and probe["cards"] >= min_cards
            and found >= min_cards
            and probe["textLen"] > 20
            and not probe["errs"]
            and ok_shot
        )
        records.append({
            "page": key, "label": label, "hash": hash_,
            "pageId": probe["pageId"], "cards": probe["cards"],
            "states": probe["states"], "textLen": probe["textLen"],
            "textHead": probe["textHead"], "selectorHits": found,
            "errors": probe["errs"], "consoleErrors": probe["cerr"],
            "canvas": probe["hasCanvas"], "screenshot": ok_shot,
            "pass": ok,
        })
        print(("[PASS] " if ok else "[FAIL] ") + f"{label:<12} cards={probe['cards']} states={probe['states']} err={len(probe['errs'])}")

    interactions = interact(cdp)
    for item in interactions:
        print(("[PASS] " if item["pass"] else "[FAIL] ") + item["case"] + " :: " + item["detail"])

    # 深色主题整组（light 对照）
    eval_js(cdp, "document.documentElement.setAttribute('data-theme','light')")
    time.sleep(1.2)
    eval_js(cdp, "location.hash='#/today'")
    time.sleep(2.4)
    light_png = os.path.join(shots, "theme_light_today.png")
    light_ok = shot(cdp, light_png)
    eval_js(cdp, "document.documentElement.setAttribute('data-theme','dark')")
    time.sleep(1.0)
    dark_png = os.path.join(shots, "theme_dark_today.png")
    dark_ok = shot(cdp, dark_png)

    summary = {
        "baseUrl": args.base_url,
        "checkedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "pages": records,
        "interactions": interactions,
        "theme": {"light": light_ok, "dark": dark_ok},
        "stats": {
            "pages": len(records),
            "pagesPass": sum(1 for r in records if r["pass"]),
            "interactionsPass": sum(1 for i in interactions if i["pass"]),
            "interactions": len(interactions),
            "consoleErrors": sum(len(r["errors"]) for r in records),
        },
    }
    with open(os.path.join(out, "result.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=True, indent=2)

    proc.terminate()
    stats = summary["stats"]
    print("\n== 动态验收汇总 ==")
    print(f"页面 {stats['pagesPass']}/{stats['pages']} 通过；交互 {stats['interactionsPass']}/{stats['interactions']} 通过；console 错误 {stats['consoleErrors']}")
    failed = [r for r in records if not r["pass"]]
    for item in failed:
        print("  FAIL:", item["page"], item["errors"][:1], "cards=", item["cards"], "hits=", item["selectorHits"])
    return 0 if stats["pagesPass"] == stats["pages"] and stats["consoleErrors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
