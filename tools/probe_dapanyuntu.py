"""抓取 dapanyuntu.com 的界面结构与功能（只读取证）。

用途：与当前工作台内的云图实现做功能对照，输出 DOM 文本、控件清单与截图。
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ui_clickthrough import CDP, eval_js, launch_chrome, page_ws  # noqa: E402

URL = "https://www.dapanyuntu.com/"
OUT = Path("_tmp_api")


def main() -> int:
    port = 9345
    profile = os.path.join(os.environ.get("TEMP", "."), f"dpy_{int(time.time())}")
    proc = launch_chrome(port, profile, 1600, 1000)
    cdp = None
    try:
        for _ in range(60):
            try:
                cdp = CDP(page_ws(port))
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.4)
        if cdp is None:
            print("无法连接调试端口")
            return 2

        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": 1600, "height": 1000, "deviceScaleFactor": 1, "mobile": False,
        })
        cdp.call("Page.navigate", {"url": URL})
        time.sleep(9)

        title = eval_js(cdp, "document.title")
        url = eval_js(cdp, "location.href")
        print("title:", title)
        print("url:", url)

        # 控件清单
        controls = eval_js(
            cdp,
            "JSON.stringify(Array.from(document.querySelectorAll('button,a,[role=button],select,input')).map(function(e){"
            "return {tag:e.tagName, text:(e.innerText||e.value||e.placeholder||'').trim().slice(0,30),"
            " cls:(e.className&&e.className.toString?e.className.toString():'').slice(0,60),"
            " title:(e.title||'').slice(0,30)};"
            "}).filter(function(x){return x.text||x.title;}))",
        )
        (OUT / "dpy_controls.json").write_text(
            json.dumps(controls, ensure_ascii=False, indent=1), encoding="utf-8"
        )

        # 页面可见文本（分块）
        texts = eval_js(
            cdp,
            "JSON.stringify(Array.from(document.querySelectorAll('body *')).filter(function(e){"
            "var r=e.getBoundingClientRect();"
            "return r.width>0&&r.height>0&&e.children.length===0&&(e.innerText||'').trim().length>0;"
            "}).map(function(e){return {t:(e.innerText||'').trim().slice(0,40),"
            " c:(e.className&&e.className.toString?e.className.toString():'').slice(0,50),"
            " x:Math.round(e.getBoundingClientRect().x),y:Math.round(e.getBoundingClientRect().y)};}))",
        )
        (OUT / "dpy_texts.json").write_text(
            json.dumps(texts, ensure_ascii=False, indent=1), encoding="utf-8"
        )

        # 画布/图表容器
        canvas = eval_js(cdp, "document.querySelectorAll('canvas').length")
        svg = eval_js(cdp, "document.querySelectorAll('svg').length")
        print("canvas:", canvas, "svg:", svg)

        # 顶层布局骨架
        layout = eval_js(
            cdp,
            "JSON.stringify(Array.from(document.body.children).map(function(e){var r=e.getBoundingClientRect();"
            "return {tag:e.tagName,id:e.id,cls:(e.className&&e.className.toString?e.className.toString():'').slice(0,80),"
            "w:Math.round(r.width),h:Math.round(r.height)};}))",
        )
        print("layout:", layout)

        shot = cdp.call("Page.captureScreenshot", {"format": "png"})
        data = shot.get("result", {}).get("data")
        if data:
            OUT.mkdir(exist_ok=True)
            (OUT / "dpy_full.png").write_bytes(base64.b64decode(data))
            print("saved _tmp_api/dpy_full.png")

        html = eval_js(cdp, "document.documentElement.outerHTML.length")
        print("html length:", html)
        return 0
    finally:
        if cdp is not None:
            try:
                cdp.ws.close()
            except Exception:  # noqa: BLE001
                pass
        proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
