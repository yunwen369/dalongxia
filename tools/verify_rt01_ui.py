# -*- coding: utf-8 -*-
"""RT-01 前端取证：解绑 / 清除签名盐必须先弹二次确认，且未确认前不发任何写请求。

做法（Edge headless + CDP）：
1. 在页面注入 fetch 钩子，记录所有 POST 目标；
2. 点「解绑」→ 断言弹窗出现、文案含「不可逆」、**且此时没有任何 /unbind 请求**；
3. 点「取消」→ 断言弹窗消失、仍然没有 /unbind 请求；
4. 切设置页点「清除签名盐」→ 同样断言弹窗出现且没有 /secret POST。

⚠️ 脚本**故意不点「确认」**：点是会真的解绑用户凭据 / 清掉签名盐。二次确认的
「确认后是否执行」已由 `verify_rt01_guard.py`（带 confirm=true 才执行）与
`tests/test_yjb.py::test_destructive_posts_execute_with_confirm` 在服务端证明。

用法：
    ./.venv/Scripts/python.exe tools/verify_rt01_ui.py --base-url http://127.0.0.1:6941 \
        --cdp-port 9401 --out evidence/

退出码：0=全部通过；1=有断言失败；2=环境/未绑定。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.verify_r6_changes import CDP, EDGE, _open_target  # noqa: E402

HOOK = """
(function(){
  window.__yjbPosts = [];
  if (window.__yjbHooked) { return; }
  window.__yjbHooked = true;
  var original = window.fetch;
  window.fetch = function(url, options) {
    try {
      var method = (options && options.method ? options.method : 'GET').toUpperCase();
      if (method === 'POST') { window.__yjbPosts.push(String(url)); }
    } catch (err) { /* 记录失败不影响请求本身 */ }
    return original.apply(window, arguments);
  };
})();
"""

CLICK_BY_TEXT = """
(function(text){
  var nodes = document.querySelectorAll('button');
  for (var i = 0; i < nodes.length; i++) {
    if ((nodes[i].textContent || '').trim() === text) { nodes[i].click(); return true; }
  }
  return false;
})(%s)
"""

MODAL_STATE = """
JSON.stringify({
  visible: !!document.querySelector('.ui-modal'),
  text: (document.querySelector('.ui-modal') || {}).innerText || '',
  posts: window.__yjbPosts || []
})
"""

CANCEL = """
(function(){
  var buttons = document.querySelectorAll('.ui-modal-actions button');
  if (!buttons.length) { return false; }
  buttons[0].click();
  return true;
})()
"""


def _get_json(url: str) -> Dict[str, Any]:
    """GET 并解析 JSON（应用统一信封）。

    Args:
        url: 完整 URL。

    Returns:
        dict: 解析后的响应体。
    """
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 - 本机回环
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    """执行取证并输出 JSON。

    Returns:
        int: 进程退出码。
    """
    parser = argparse.ArgumentParser(description="RT-01 前端二次确认取证")
    parser.add_argument("--base-url", default="http://127.0.0.1:6941")
    parser.add_argument("--cdp-port", type=int, default=9401)
    parser.add_argument("--out", default="", help="截图与 JSON 输出目录")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    out_dir = Path(args.out) if args.out else ROOT / "evidence"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 必须先处于「已绑定」态，解绑按钮才会渲染
    status = _get_json(base + "/api/fund/yjb/status").get("data") or {}
    if not status.get("bound"):
        print("[FAIL] 当前未绑定，解绑按钮不存在，无法取证（请先落位凭据）")
        return 2

    edge = subprocess.Popen(
        [EDGE, "--headless=new", "--disable-gpu", f"--remote-debugging-port={args.cdp_port}",
         f"--user-data-dir={out_dir / '_profile_rt01'}", "--window-size=1366,900",
         "--remote-allow-origins=*", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    report: Dict[str, Any] = {"base": base, "checks": {}, "passed": False}
    try:
        cdp = CDP(_open_target(args.cdp_port))
        cdp.call("Runtime.enable")
        cdp.call("Page.enable")

        # ① 养基宝页：点解绑 → 弹窗出现且无 POST /unbind
        cdp.goto(base + "/#/yjb", wait=5.0)
        cdp.js(HOOK)
        clicked = bool(cdp.js(CLICK_BY_TEXT % json.dumps("解绑", ensure_ascii=False)))
        time.sleep(0.8)
        state = json.loads(cdp.js(MODAL_STATE))
        report["unbind_modal"] = {"clicked": clicked, "visible": state["visible"],
                                  "text": state["text"][:160], "posts": state["posts"]}
        unbind_posts = [url for url in state["posts"] if "/yjb/unbind" in url]
        report["checks"]["unbind_click_opens_modal"] = clicked and state["visible"]
        report["checks"]["unbind_modal_warns_irreversible"] = "不可逆" in state["text"]
        report["checks"]["unbind_no_request_before_confirm"] = not unbind_posts
        cdp.shot(out_dir / "rt01_unbind_modal.png")

        # ② 点取消 → 弹窗消失，仍无请求
        cancelled = bool(cdp.js(CANCEL))
        time.sleep(0.6)
        after_cancel = json.loads(cdp.js(MODAL_STATE))
        report["unbind_after_cancel"] = after_cancel
        report["checks"]["unbind_cancel_closes_modal"] = cancelled and not after_cancel["visible"]
        report["checks"]["unbind_cancel_sends_nothing"] = not [
            url for url in after_cancel["posts"] if "/yjb/unbind" in url
        ]

        # ③ 设置页：点清除签名盐 → 弹窗出现且无 POST /secret
        cdp.goto(base + "/#/settings", wait=4.5)
        cdp.js(HOOK)
        clear_clicked = bool(cdp.js(CLICK_BY_TEXT % json.dumps("清除签名盐", ensure_ascii=False)))
        time.sleep(0.8)
        settings_state = json.loads(cdp.js(MODAL_STATE))
        report["secret_modal"] = {"clicked": clear_clicked, "visible": settings_state["visible"],
                                  "text": settings_state["text"][:160],
                                  "posts": settings_state["posts"]}
        secret_posts = [url for url in settings_state["posts"] if "/yjb/secret" in url]
        report["checks"]["secret_click_opens_modal"] = clear_clicked and settings_state["visible"]
        report["checks"]["secret_no_request_before_confirm"] = not secret_posts
        cdp.shot(out_dir / "rt01_secret_modal.png")
        if settings_state["visible"]:
            cdp.js(CANCEL)
        cdp.close()
    finally:
        edge.terminate()
        try:
            edge.wait(timeout=12)
        except subprocess.TimeoutExpired:
            edge.kill()

    report["passed"] = all(report["checks"].values())
    for key, value in report["checks"].items():
        print(f"[{'OK  ' if value else 'FAIL'}] {key}")
    out_json = out_dir / "rt01_ui.json"
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"       报告：{out_json}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
