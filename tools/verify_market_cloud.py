"""大盘云图后端接入的浏览器取证（CDP / Edge headless）。

用途：验证「云图已从演示数据切到 /api/market-cloud 真实行情」这一批改动，
断言覆盖「数据来自后端」「无演示角标」「市场切换」「弹窗真实 K 线」
「双击回调工作台」五类，失败即红，不放宽。

运行：
    python tools/verify_market_cloud.py --base http://127.0.0.1:6971
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ui_clickthrough import CDP, click_at, eval_js, launch_chrome, page_ws  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    """记录一条断言。"""
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + ("  | " + detail if detail else ""))


def capture(cdp: CDP, path: str) -> None:
    """截图落盘（用于人工复核）。"""
    if not path:
        return
    import base64

    shot = cdp.call("Page.captureScreenshot", {"format": "png"})
    data = shot.get("result", {}).get("data")
    if not data:
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(base64.b64decode(data))
    print("截图：" + path)


def set_vp(cdp: CDP, width: int, height: int) -> None:
    """只设置视口，不清除覆盖（清除会让后续布局回落到默认尺寸）。"""
    cdp.call("Emulation.setDeviceMetricsOverride", {
        "width": width, "height": height, "deviceScaleFactor": 1, "mobile": False,
    })


def wait_until(cdp: CDP, expr: str, timeout: float = 25.0, interval: float = 0.4) -> bool:
    """轮询等待 JS 表达式为真。"""
    end = time.time() + timeout
    while time.time() < end:
        if eval_js(cdp, expr):
            return True
        time.sleep(interval)
    return False


FRAME = "document.getElementById('cloud-frame').contentWindow"


def frame_js(expr: str) -> str:
    """构造在 iframe 内执行的表达式（w=contentWindow, d=contentDocument）。"""
    return (
        "(function(){var f=document.getElementById('cloud-frame');"
        "if(!f||!f.contentWindow)return null;var w=f.contentWindow,d=f.contentDocument;"
        "return (" + expr + ");})()"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:6971")
    ap.add_argument("--port", type=int, default=9333)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    profile = os.path.join(os.environ.get("TEMP", "."), f"mc_verify_{int(time.time())}")
    proc = launch_chrome(args.port, profile, 1440, 900)
    cdp = None
    try:
        for _ in range(40):
            try:
                cdp = CDP(page_ws(args.port))
                break
            except Exception:  # noqa: BLE001 - 启动期轮询
                time.sleep(0.3)
        if cdp is None:
            print("FAIL 无法连接 Edge 调试端口")
            return 2

        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Log.enable")
        set_vp(cdp, 1440, 900)
        cdp.call("Page.navigate", {"url": args.base + "/index.html"})
        time.sleep(2.5)
        eval_js(cdp, "1")  # 触发一次求值，确保上下文就绪

        # W-01 左侧一级目录出现「大盘云图」（分组默认折叠，先展开再判定）
        eval_js(cdp, "(function(){var g=document.querySelector('.side-group[data-group=\"cloud\"]');if(g)g.click();return true;})()")
        time.sleep(0.5)
        groups = eval_js(
            cdp,
            "JSON.stringify(Array.from(document.querySelectorAll('[data-view]')).map(function(e){return e.dataset.view;}))",
        )
        ids = groups if isinstance(groups, list) else (json.loads(groups) if isinstance(groups, str) else [])
        has_entry = "cloud" in ids
        check("W-01 工作台左侧出现「大盘云图」一级目录", has_entry, str(ids)[:160])

        # W-02 进入云图页
        clicked = eval_js(cdp, "(function(){var b=document.querySelector('[data-view=\"cloud\"]');if(!b)return false;b.click();return true;})()")
        check("W-02 可跳转到云图视图", bool(clicked))
        time.sleep(1.2)

        # W-03 iframe 载入
        ok_frame = wait_until(cdp, frame_js("!!(d&&d.getElementById('B-inner'))"), 20)
        check("W-03 云图 iframe 载入完成", ok_frame)

        # 注入控制台采集（须在业务脚本报错之前，故在导航后立刻补注入）
        from ui_clickthrough import CONSOLE_CAPTURE

        cdp.call("Runtime.evaluate", {
            "expression": "(function(){var f=document.getElementById('cloud-frame');"
                          "if(f&&f.contentWindow)f.contentWindow.eval(" + json.dumps(CONSOLE_CAPTURE) + ");})()",
        })

        # W-04 元信息来自后端
        meta_ok = wait_until(cdp, frame_js("!!(w.MC_META&&w.MC_META.ok)"), 40)
        meta = eval_js(cdp, frame_js("JSON.stringify(w.MC_META||{})"))
        check("W-04 概览接口返回成功（MC_META.ok）", meta_ok, str(meta)[:200])

        # W-05 来源为东财 push2
        src = eval_js(cdp, frame_js("(w.MC_META||{}).source"))
        check("W-05 数据来源为 eastmoney_clist", src == "eastmoney_clist", str(src))

        # W-06 全市场样本量
        total = eval_js(cdp, frame_js("(w.MC_META||{}).total"))
        check("W-06 全市场样本 > 5000", isinstance(total, int) and total > 5000, str(total))

        # W-07 canvas 树图叶子数量（v3.0 改 canvas，无 .mc-leaf DOM）
        leaves = eval_js(cdp, frame_js("(w.MCV?w.MCV.all():[]).filter(function(b){return b.kind==='leaf';}).length"))
        check("W-07 图内个股方块 > 3000 个", isinstance(leaves, int) and leaves > 3000, str(leaves))

        # W-08 无「演示数据」角标
        demo_badge = eval_js(cdp, frame_js("d.querySelectorAll('.mc-demo').length"))
        check("W-08 演示数据角标已移除", demo_badge == 0, str(demo_badge))

        # W-09 一级分组数 > 20（申万 31 类 + 未分类）
        groups_n = eval_js(cdp, frame_js("(w.MCV?w.MCV.all():[]).filter(function(b){return b.kind==='g1';}).length"))
        check("W-09 一级分组渲染 > 20 个", isinstance(groups_n, int) and groups_n > 20, str(groups_n))

        # W-10 统计卡涨跌家数非零
        up = eval_js(cdp, frame_js("(function(){var e=d.querySelectorAll('#A-cards .mc-card .v');return e.length?parseInt(e[0].textContent,10):null;})()"))
        check("W-10 上涨家数 > 0", isinstance(up, int) and up > 0, str(up))

        # W-11 数据时间与时段来自后端
        as_of = eval_js(cdp, frame_js("(w.MC_META||{}).asOf"))
        sess = eval_js(cdp, frame_js("(w.MC_META||{}).sessionText"))
        check("W-11 展示后端 asOf 与会话状态", bool(as_of) and bool(sess), f"{as_of} / {sess}")

        # W-12 切换到港股能拉到真实数据
        eval_js(cdp, frame_js("(function(){var b=d.querySelector('[data-market=\"hk\"]');if(b)b.click();return !!b;})()"))
        hk_ok = wait_until(cdp, frame_js("(w.MC_META||{}).market==='hk' && (w.MC_META||{}).ok"), 40)
        hk_total = eval_js(cdp, frame_js("(w.MC_META||{}).total"))
        check("W-12 港股市场切换后拉到真实快照", hk_ok and isinstance(hk_total, int) and hk_total > 2000, str(hk_total))

        # W-13 切回 A 股并使用缓存（无需重新拉取即可渲染）
        eval_js(cdp, frame_js("(function(){var b=d.querySelector('[data-market=\"stock\"]');if(b)b.click();return !!b;})()"))
        back = wait_until(cdp, frame_js("(function(){var n=(w.MCV?w.MCV.all():[]).filter(function(b){return b.kind==='leaf';}).length;return n>3000;})()"), 25)
        check("W-13 切回 A 股后仍渲染 > 3000 方块", back)
        if args.out:
            capture(cdp, args.out)

        # 叶子中心在宿主页面中的坐标（用于 CDP 真实鼠标事件穿透 iframe）
        pt_expr = (
            "(function(){var all=(w.MCV?w.MCV.all():[]);"
            "var ls=all.filter(function(b){return b.kind==='leaf';}).sort(function(a,b){return b.w*b.h-a.w*a.h;});"
            "if(!ls.length)return null;var L=ls[0];var cv=d.getElementById('B-inner');"
            "var r=cv.getBoundingClientRect();var f=document.getElementById('cloud-frame').getBoundingClientRect();"
            "var zm=w.eval('STATE.zoom'),tx=w.eval('STATE.tx'),ty=w.eval('STATE.ty');"
            "if(typeof zm!=='number')zm=1;if(typeof tx!=='number')tx=0;if(typeof ty!=='number')ty=0;"
            "return JSON.stringify({x:Math.round(f.left+r.left+(L.x+L.w/2)*zm+tx),"
            "y:Math.round(f.top+r.top+(L.y+L.h/2)*zm+ty),code:L.item.code});})()"
        )
        raw_pt = eval_js(cdp, frame_js(pt_expr))
        if isinstance(raw_pt, dict):
            pt = raw_pt
        elif isinstance(raw_pt, str) and raw_pt.startswith("{"):
            pt = json.loads(raw_pt)
        else:
            pt = None
        if pt:
            cdp.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": pt["x"], "y": pt["y"], "button": "none"})
            time.sleep(1.2)
            rows = eval_js(cdp, frame_js("d.querySelectorAll('#B-popover .mc-rank .r').length"))
            check("W-16 悬浮弹窗同分组排行 ≥ 5 行", isinstance(rows, int) and rows >= 5, str(rows))

            # W-14 单击方块打开检视器并加载真实 K 线
            click_at(cdp, pt["x"], pt["y"])
            got = wait_until(
                cdp,
                frame_js("(function(){var c=d.getElementById('C-echarts');return !!(c&&c.querySelector('canvas'));})()"),
                40,
            )
            check("W-14 检视器渲染真实日 K（canvas）", got, str(pt.get("code")))
            metric_rows = eval_js(cdp, frame_js("d.querySelectorAll('#C-inspector .mc-insp-metrics > div').length"))
            check("W-15 检视器指标行 ≥ 6 项", isinstance(metric_rows, int) and metric_rows >= 6, str(metric_rows))
        else:
            check("W-14 检视器渲染真实日 K（canvas）", False, "未找到可点击方块")
            check("W-15 检视器指标行 ≥ 6 项", False, "未找到可点击方块")
            check("W-16 悬浮弹窗同分组排行 ≥ 5 行", False, "未找到可点击方块")

        # W-17 双击回调工作台行情页
        if pt:
            cdp.call("Input.dispatchMouseEvent", {"type": "mousePressed", "x": pt["x"], "y": pt["y"], "button": "left", "clickCount": 2})
            cdp.call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": pt["x"], "y": pt["y"], "button": "left", "clickCount": 2})
        time.sleep(1.2)
        host_hash = eval_js(cdp, "location.hash")
        check("W-17 双击由工作台打开行情页", isinstance(host_hash, str) and host_hash.startswith("#/stock"), f"{pt and pt.get('code')} -> {host_hash}")

        # W-18 无 JS 严重错误
        errs = eval_js(cdp, frame_js("JSON.stringify(w.__cb_errors||[])"))
        fatal = []
        if isinstance(errs, str) and errs not in ("[]", "", "null"):
            try:
                fatal = [e for e in json.loads(errs) if e.get("kind") in ("onerror",)]
            except ValueError:
                fatal = []
        check("W-18 云图内无未捕获脚本错误", not fatal, str(fatal)[:200])

        # 尾帧留证（双击跳转后的工作台行情页，与云图主视图分开存）
        if args.out:
            capture(cdp, str(Path(args.out).with_name(Path(args.out).stem + "_after_jump.png")))

        passed = sum(1 for _, ok, _ in RESULTS if ok)
        print(f"\n合计 {passed}/{len(RESULTS)} PASS")
        return 0 if passed == len(RESULTS) else 1
    finally:
        if cdp is not None:
            try:
                cdp.ws.close()
            except Exception:  # noqa: BLE001
                pass
        proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
