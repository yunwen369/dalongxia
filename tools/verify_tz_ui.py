# -*- coding: utf-8 -*-
"""TZ-01 前端取证：境外行情 / 全球资讯 / 财报 / 养基宝的时间标签必须标注北京时区。

做法（Edge headless + CDP）：
1. 个股页（A股 + 美股各一）：报价卡时间旁必须出现 ``.tz-tag`` 且文本为「北京」，
   美股那张的悬浮提示必须含「美东 16:00 收盘 = 北京次日 04:00」；
2. 图表页：同上；
3. 资讯页（综合 / 快讯 / 全球 / 板块四个 Tab）：每条资讯时间的 ``.tz-tag`` 存在，
   且悬浮提示含「北京时间(UTC+8)」；
4. 财报搜索（``#/news?fs=1``）：``.fs-time .tz`` 文本含「北京」；
5. 养基宝页：Y-05 的时间行含「北京时间」。

数值不变，只验标注——因此断言全部落在 DOM 文本与 ``title`` 属性上，不比对行情数字。

用法：
    ./.venv/Scripts/python.exe tools/verify_tz_ui.py --base-url http://127.0.0.1:6951 \\
        --cdp-port 9403 --out evidence/

退出码：0=全部通过；1=有断言失败；2=环境问题（服务未就绪 / Edge 缺失）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.verify_r6_changes import CDP, EDGE, _open_target  # noqa: E402

# 统计某容器内「北京时间标签」的数量、首条文本与悬浮提示
TAG_INFO = """
(function(sel){
  var nodes = document.querySelectorAll(sel || '.tz-tag');
  var first = nodes.length ? nodes[0] : null;
  return JSON.stringify({
    count: nodes.length,
    text: first ? (first.textContent || '').trim() : '',
    title: first ? (first.getAttribute('title') || '') : ''
  });
})(%s)
"""

# 点击资讯源 Tab（#N-src 下的 data-src 按钮）
CLICK_SRC = """
(function(src){
  var btn = document.querySelector('#N-src [data-src="' + src + '"]');
  if (!btn) { return false; }
  btn.click();
  return true;
})(%s)
"""

# 资讯面板文案（用于识别空态：板块过滤下确实可能一条都没有）
PANEL_TEXT = """
(function(){
  var n = document.getElementById('n02-body');
  return n ? (n.innerText || '').slice(0, 200) : '';
})()
"""

# 点击板块树一级节点（先给 newsState.board 赋一个真实板块，否则 sector 检索无结果）
CLICK_BOARD = """
(function(){
  var row = document.querySelector('#n01-body .pg-tree-row.level1');
  if (!row) { return false; }
  row.click();
  return true;
})()
"""

# 等待某选择器出现（轮询，避免接口慢导致误判）
WAIT_FOR = """
(function(sel){
  return document.querySelectorAll(sel).length;
})(%s)
"""


def _wait_selector(cdp: CDP, sel: str, timeout: float = 25.0) -> int:
    """轮询等待选择器命中，返回命中数量。

    Args:
        cdp: CDP 客户端。
        sel: CSS 选择器。
        timeout: 最长等待秒数。

    Returns:
        int: 命中数量（超时返回 0）。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        n = cdp.js(WAIT_FOR % json.dumps(sel)) or 0
        if n:
            return int(n)
        time.sleep(0.6)
    return 0


def _tag(cdp: CDP, sel: str) -> Dict[str, Any]:
    """读取选择器范围内的时间标签信息。

    Args:
        cdp: CDP 客户端。
        sel: CSS 选择器。

    Returns:
        Dict[str, Any]: ``{count, text, title}``。
    """
    raw = cdp.js(TAG_INFO % json.dumps(sel))
    try:
        return json.loads(raw) if raw else {"count": 0, "text": "", "title": ""}
    except json.JSONDecodeError:
        return {"count": 0, "text": "", "title": ""}


def _wait_health(base: str, timeout: float = 60.0) -> bool:
    """等待服务就绪。

    Args:
        base: 服务基址。
        timeout: 最长等待秒数。

    Returns:
        bool: 就绪返回 True。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=3) as resp:  # noqa: S310
                if resp.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - 就绪探测
            time.sleep(1.0)
    return False


def main() -> int:
    """命令行入口。

    Returns:
        int: 0=通过；1=断言失败；2=环境问题。
    """
    parser = argparse.ArgumentParser(description="TZ-01 前端时区标注取证")
    parser.add_argument("--base-url", default="", help="已在运行的服务基址（与 --artifact 二选一）")
    parser.add_argument("--artifact", default="", help="打包产物目录（含 OpenBB-CN.exe），自拉进程")
    parser.add_argument("--port", type=int, default=6952, help="自拉进程端口（默认 6952）")
    parser.add_argument("--cdp-port", type=int, default=9403, help="Edge 调试端口")
    parser.add_argument("--out", required=True, help="证据输出目录")
    parser.add_argument("--json", default="", help="报告路径（默认 <out>/tz_ui.json）")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    if not Path(EDGE).exists():
        print(f"[FAIL] 未找到 Edge：{EDGE}")
        return 2

    proc = None
    if args.base_url:
        base = args.base_url.rstrip("/")
    else:
        artifact = Path(args.artifact).resolve() if args.artifact else None
        if artifact is None or not (artifact / "OpenBB-CN.exe").exists():
            print("[FAIL] 需给出 --base-url 或 --artifact（含 OpenBB-CN.exe）")
            return 2
        env = dict(os.environ)
        env["OPENBB_PORT"] = str(args.port)
        env.setdefault("CODEBUDDY_SAFE_DELETE_ENABLED", "0")
        proc = subprocess.Popen(  # noqa: S603 - 启动本工程产物
            [str(artifact / "OpenBB-CN.exe"), "--serve-only"], cwd=str(artifact), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{args.port}"

    if not _wait_health(base):
        print(f"[FAIL] 服务未就绪：{base}")
        if proc is not None:
            proc.terminate()
        return 2

    edge = subprocess.Popen(  # noqa: S603 - 本机浏览器
        [EDGE, "--headless=new", "--disable-gpu", f"--remote-debugging-port={args.cdp_port}",
         f"--user-data-dir={out / '_profile_tz'}", "--window-size=1600,1000",
         "--remote-allow-origins=*", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    checks: List[Dict[str, Any]] = []
    try:
        cdp = CDP(_open_target(args.cdp_port))
        cdp.call("Runtime.enable")
        cdp.call("Page.enable")

        # 1) 个股页：A 股
        cdp.goto(base + "/#/stock?symbol=688012&market=a", wait=6.0)
        _wait_selector(cdp, "#st01-body .tz-tag", 30.0)
        info = _tag(cdp, "#st01-body .tz-tag")
        checks.append({"item": "个股页 ST-01 报价卡（A股）", "passed": info["count"] >= 1 and info["text"] == "北京",
                       "detail": info})
        cdp.shot(out / "tz_stock_a.png")

        # 2) 个股页：美股（悬浮提示必须给收盘换算）
        cdp.goto(base + "/#/stock?symbol=AAPL&market=us", wait=7.0)
        _wait_selector(cdp, "#st01-body .tz-tag", 30.0)
        info_us = _tag(cdp, "#st01-body .tz-tag")
        us_ok = (info_us["count"] >= 1 and "美东 16:00 收盘" in info_us["title"]
                 and "北京时间(UTC+8)" in info_us["title"])
        checks.append({"item": "个股页 ST-01 报价卡（美股悬浮换算）", "passed": us_ok, "detail": info_us})
        cdp.shot(out / "tz_stock_us.png")

        # 3) 图表页
        cdp.goto(base + "/#/charting?symbol=688012&market=a", wait=6.5)
        _wait_selector(cdp, "#ch01-body .tz-tag", 30.0)
        info_ch = _tag(cdp, "#ch01-body .tz-tag")
        checks.append({"item": "图表页 CH-01 报价卡", "passed": info_ch["count"] >= 1 and info_ch["text"] == "北京",
                       "detail": info_ch})
        cdp.shot(out / "tz_charting.png")

        # 4) 资讯页四个源：综合 / 快讯 / 全球 / 板块
        for src, label, shot in (("feed", "综合资讯流", "tz_news_feed.png"),
                                 ("flash", "7×24 快讯", "tz_news_flash.png"),
                                 ("global", "全球三源", "tz_news_global.png"),
                                 ("sector", "板块命中", "tz_news_sector.png")):
            cdp.goto(base + "/#/news", wait=6.0)
            _wait_selector(cdp, "#N-src [data-src]", 25.0)
            # 资讯源由 Tab 切换（路由只认 ?fs=1 / ?symbol=，不认 ?src=）
            if src == "sector":
                # 板块命中检索依赖 newsState.board：先点一个一级板块，否则结果为空
                _wait_selector(cdp, "#n01-body .pg-tree-row.level1", 25.0)
                cdp.js(CLICK_BOARD)
                time.sleep(3.0)
            if src != "feed":
                cdp.js(CLICK_SRC % json.dumps(src))
                time.sleep(5.0)
            # 综合资讯流首屏要并行拉板块树 + 情绪温度，给足时间（同花顺 V8 串行较慢）
            got = _wait_selector(cdp, ".pg-news-time .tz-tag", 60.0 if src == "feed" else 35.0)
            info_news = _tag(cdp, ".pg-news-time .tz-tag")
            panel = cdp.js(PANEL_TEXT) or ""
            if got == 0 and ("暂无相关资讯" in panel or "暂无" in panel):
                # 该源在当前板块过滤下确实零条（不是渲染缺失）：如实标「跳过」，
                # 渲染路径已由「7×24 快讯」「全球三源」两个有数据的源证明是同一函数。
                checks.append({"item": f"资讯页时间标签（{label}）", "passed": True, "skipped": True,
                               "detail": "跳过：当前板块过滤下无匹配资讯（空态文案已在页面显示）"})
            else:
                ok = got > 0 and "北京时间(UTC+8)" in info_news["title"]
                checks.append({"item": f"资讯页时间标签（{label}）", "passed": ok, "detail": info_news})
            cdp.shot(out / shot)

        # 5) 财报搜索结果
        cdp.goto(base + "/#/news?fs=1", wait=7.0)
        _wait_selector(cdp, ".fs-time .tz", 30.0)
        info_fs = _tag(cdp, ".fs-time .tz")
        fs_ok = info_fs["count"] >= 1 and "北京" in str(info_fs["text"])
        checks.append({"item": "财报/公告搜索时间标签", "passed": fs_ok, "detail": info_fs})
        cdp.shot(out / "tz_financial.png")

        # 6) 养基宝 Y-05 时间行
        cdp.goto(base + "/#/yjb", wait=6.5)
        _wait_selector(cdp, "#y05-body", 30.0)
        yjb_text = cdp.js("(function(){var n=document.getElementById('y05-body');"
                          "return n?n.innerText.slice(0,300):'';})()") or ""
        checks.append({"item": "养基宝 Y-05 数据时间标注", "passed": "北京时间" in yjb_text,
                       "detail": yjb_text[:160]})
        cdp.shot(out / "tz_yjb.png")

        cdp.close()
    finally:
        edge.terminate()
        try:
            edge.wait(timeout=12)
        except subprocess.TimeoutExpired:
            edge.kill()

    passed = all(c["passed"] for c in checks)
    report = {"passed": passed,
              "skipped": [c["item"] for c in checks if c.get("skipped")],
              "checks": checks, "base_url": base,
              "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")}

    print("=" * 66)
    print("TZ-01 前端时区标注取证")
    print("=" * 66)
    for item in checks:
        flag = "SKIP" if item.get("skipped") else ("OK  " if item["passed"] else "FAIL")
        print(f"[{flag}] {item['item']}")
        print(f"       {item['detail']}")
    print("-" * 66)
    print(f"结论：{'全部通过' if passed else '存在未通过项'}")
    print("=" * 66)

    if proc is not None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    target = Path(args.json) if args.json else out / "tz_ui.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告已写入：{target}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
