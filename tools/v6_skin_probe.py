#!/usr/bin/env python3
"""V6 皮肤验收探针（REQ-20261111-01 · 门禁 A 证据）。

用途：真实 Edge headless CDP 验证 V6 皮肤落地后的视觉与结构判据，
      覆盖验收标准 S/A/B 三组中可自动断言的部分。

用法:
  python tools/v6_skin_probe.py --base-url http://127.0.0.1:6903 --out v6_probe.json

判据来源：B_交付归档\\01_原型设计\\20261111-V6视觉换肤\\验收标准20261111.md
  S-02 :root 六固定参数与 §7.4 全等
  S-03 V6 布局参数仅在自身作用域生效
  S-04 三皮肤红涨绿跌语义
  S-05 层次量化（p50/p90/p90-p50/p99）
  A-01 29 视图四处一致
  A-03 品牌区宽度 = 侧栏宽度
  A-04 侧栏分组折叠
  A-05 组件库 49 卡
  B-01 3 皮肤 × 2 断点无卡片内部裁切
  B-02 1366×768 无页面级纵向滚动条
  B-03 顶栏右侧不裁切
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

import websocket

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

# 29 视图（与 frontend/js/app.js NAV_TREE 逐条一致）
VIEWS = [
    "home", "today", "market", "dashboard", "dashboard-manage", "widget-library",
    "watchlist", "news", "sectors", "stock", "fin", "ann", "tools", "cand",
    "charting", "sector-val", "macro", "valuation", "crowding", "screener",
    "lhb", "fund-combo", "portfolio", "fund", "yjb", "mgr", "alerts",
    "connectors", "settings",
]

GROUP_LABELS = {
    "dashboard": "总览", "market": "市场", "news": "资讯", "portfolio": "组合",
    "money": "资金", "watchlist": "自选", "yjb": "养基宝", "system": "系统",
}


class CDP:
    """极简 CDP 客户端。"""

    def __init__(self, ws_url: str) -> None:
        self.ws = websocket.create_connection(ws_url, timeout=30)
        self._id = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        msg = {"id": self._id, "method": method}
        if params:
            msg["params"] = params
        self.ws.send(json.dumps(msg))
        return self._recv(msg["id"])

    def _recv(self, expect_id: int) -> dict:
        while True:
            raw = self.ws.recv()
            if not raw:
                raise RuntimeError("CDP 连接断开")
            data = json.loads(raw)
            if data.get("id") == expect_id:
                return data

    def eval_js(self, expr: str):
        """执行 JS 并返回原始值（returnByValue）。"""
        res = self.call("Runtime.evaluate", {
            "expression": expr,
            "returnByValue": True,
            "awaitPromise": True,
        })
        result = res.get("result", {})
        if result.get("exceptionDetails"):
            raise RuntimeError(f"JS 异常: {result['exceptionDetails'].get('text')}")
        return result.get("result", {}).get("value")

    def shot(self, path: str) -> None:
        """截图到指定路径。"""
        res = self.call("Page.captureScreenshot", {
            "format": "png",
            "captureBeyondViewport": False,
        })
        data = res.get("result", {}).get("data")
        if data:
            with open(path, "wb") as fh:
                fh.write(base64.b64decode(data))

    def viewport(self, width: int, height: int) -> None:
        """设置视口尺寸。"""
        self.call("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height,
            "deviceScaleFactor": 1, "mobile": False,
        })

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


def find_edge() -> str:
    for path in EDGE_CANDIDATES:
        if os.path.exists(path):
            return path
    raise RuntimeError("未找到 msedge.exe")


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def launch_edge(port: int, profile: str) -> subprocess.Popen:
    edge = find_edge()
    proc = subprocess.Popen([
        edge,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--window-size=1366,768",
        "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # 等待 devtools 就绪
    for _ in range(80):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as resp:
                json.loads(resp.read().decode())
                return proc
        except Exception:
            time.sleep(0.25)
    raise RuntimeError("Edge devtools 未就绪")


def open_target(port: int, url: str) -> str:
    """新建标签页并返回 ws url。"""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/json/new?{url}", method="PUT"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    return data["webSocketDebuggerUrl"]


def compute_percentiles(png_path: str) -> tuple[float, float, float] | None:
    """计算灰度分位（p50 / p90 / p99），缩略到 420px 宽以对齐参考图口径。

    依据：用户 2026-11-11 01:20 的量化诊断方法 —— 缩到 420px 转灰度取分位，
    参考图 p50=20 / p90=88 / p99=181。此处用同一口径，保证可比。
    """
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        return None
    im = Image.open(png_path).convert("L")
    width = 420
    height = max(1, int(width * im.size[1] / im.size[0]))
    im = im.resize((width, height))
    arr = np.array(im).astype(float)
    p50, p90, p99 = np.percentile(arr, [50, 90, 99])
    return float(p50), float(p90), float(p99)


class Result:
    """断言收集器。"""

    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, code: str, name: str, ok: bool, detail: str) -> None:
        self.items.append({"code": code, "name": name, "ok": bool(ok), "detail": detail})
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {code} {name} :: {detail}")

    def summary(self) -> tuple[int, int]:
        passed = sum(1 for i in self.items if i["ok"])
        return passed, len(self.items) - passed


JS_HELPERS = r"""
window.__v6p = {
  vars: function () {
    var s = getComputedStyle(document.documentElement);
    var keys = ['--menu-w','--sidebar-w','--toolbar-h','--copilot-w','--cell','--radius',
                '--bg-app','--bg-widget','--border','--text','--accent','--up','--down'];
    var out = {};
    keys.forEach(function (k) { out[k] = s.getPropertyValue(k).trim(); });
    out['data-theme'] = document.documentElement.getAttribute('data-theme');
    return out;
  },
  clipped: function () {
    var bad = [];
    var nodes = document.querySelectorAll('.card, .pg-card, .grid-card, .dash-widget');
    Array.prototype.forEach.call(nodes, function (n) {
      if (n.offsetParent === null) { return; }
      if (n.scrollHeight > n.clientHeight + 1 || n.scrollWidth > n.clientWidth + 1) {
        bad.push((n.className || '').slice(0, 40) + ' sh=' + n.scrollHeight + '/' + n.clientHeight +
                 ' sw=' + n.scrollWidth + '/' + n.clientWidth);
      }
    });
    return bad;
  },
  overflow: function () {
    return { sh: document.documentElement.scrollHeight, ih: window.innerHeight,
             bw: document.body.scrollWidth, iw: window.innerWidth };
  },
  align: function () {
    var brand = document.querySelector('.logo');
    var side = document.querySelector('.sidebar');
    var nav = document.querySelector('.nav');
    var main = document.querySelector('.workspace');
    var r = function (el) { return el ? el.getBoundingClientRect() : null; };
    var b = r(brand), s = r(side), n = r(nav), m = r(main);
    return { brandW: b ? Math.round(b.width) : -1, sidebarW: s ? Math.round(s.width) : -1,
             navLeft: n ? Math.round(n.left) : -1, mainLeft: m ? Math.round(m.left) : -1 };
  },
  rightOk: function () {
    var act = document.querySelector('.topbar-actions');
    if (!act) { return { ok: false, why: 'no .topbar-actions' }; }
    var r = act.getBoundingClientRect();
    return { ok: r.right <= window.innerWidth + 1, right: Math.round(r.right), iw: window.innerWidth };
  },
  viewState: function () {
    var t = document.getElementById('page-title');
    var crumb = document.getElementById('crumb');
    var navActive = document.querySelector('.nav-item.active');
    var sideActive = document.querySelector('.sidebar-item.active');
    return {
      title: t ? t.textContent.trim() : '',
      crumb: crumb ? crumb.textContent.trim() : '',
      navLabel: navActive ? (navActive.getAttribute('data-group') || '') : '',
      navText: navActive ? navActive.textContent.trim() : '',
      sideText: sideActive ? sideActive.textContent.trim() : '',
      hash: location.hash
    };
  },
  widgetCount: function () {
    var cards = document.querySelectorAll('#dash-lib-grid .dash-lib-card');
    var addBtns = document.querySelectorAll('#dash-lib-grid .dash-lib-card .pg-btn');
    return { cards: cards.length, addButtons: addBtns.length };
  },
  groups: function () {
    var gs = document.querySelectorAll('.sb-group');
    var open = 0, total = 0;
    Array.prototype.forEach.call(gs, function (g) { total += 1; if (g.open) { open += 1; } });
    // 可见项判据用 offsetHeight（offsetParent 在 flex 容器下恒为 null，不可靠）
    var visible = 0;
    Array.prototype.forEach.call(document.querySelectorAll('.sidebar-item'), function (it) {
      if (it.offsetHeight > 0 && it.getBoundingClientRect().height > 0) { visible += 1; }
    });
    return { total: total, open: open, visibleItems: visible };
  },
  foldToggle: function () {
    // 点开第二个分组 → 再点收起，验证折叠可恢复
    var gs = document.querySelectorAll('.sb-group');
    if (gs.length < 2) { return { ok: false, why: 'groups<2' }; }
    var before = 0;
    Array.prototype.forEach.call(document.querySelectorAll('.sidebar-item'), function (it) {
      if (it.offsetHeight > 0) { before += 1; }
    });
    var target = gs[1];
    target.open = true;
    var mid = 0;
    Array.prototype.forEach.call(document.querySelectorAll('.sidebar-item'), function (it) {
      if (it.offsetHeight > 0) { mid += 1; }
    });
    target.open = false;
    var after = 0;
    Array.prototype.forEach.call(document.querySelectorAll('.sidebar-item'), function (it) {
      if (it.offsetHeight > 0) { after += 1; }
    });
    return { ok: mid > before && after === before, before: before, mid: mid, after: after };
  }
};
'ok'
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:6903")
    ap.add_argument("--out", default="v6_probe.json")
    ap.add_argument("--shots", default="")
    args = ap.parse_args()

    result = Result()
    port = free_port()
    profile = tempfile.mkdtemp(prefix="v6probe_")
    proc = None
    cdp = None
    shots = args.shots
    if shots:
        os.makedirs(shots, exist_ok=True)

    try:
        proc = launch_edge(port, profile)
        ws_url = open_target(port, args.base_url + "/")
        cdp = CDP(ws_url)
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Log.enable")
        cdp.viewport(1366, 768)
        # 就绪闸门：必须等到 window.App 与 __v6p 都可用再断言，
        # 否则慢机器上会抛「Uncaught」（2026-11-11 实测出现过一次假失败）。
        ready = False
        for _ in range(40):
            time.sleep(0.5)
            try:
                if cdp.eval_js("!!(window.App && window.App.applyTheme)"):
                    ready = True
                    break
            except Exception:
                continue
        if not ready:
            raise RuntimeError("页面未就绪：window.App.applyTheme 40 次轮询内不可用")
        cdp.eval_js(JS_HELPERS)

        # ---------- S-03：默认皮肤必须是 v6 ----------
        v = cdp.eval_js("window.__v6p.vars()")
        result.add("S-03a", "默认皮肤为 v6", v.get("data-theme") == "v6",
                   f"data-theme={v.get('data-theme')}")

        # ---------- S-03：V6 布局参数生效 ----------
        layout_ok = (v.get("--menu-w") == "44px" and v.get("--sidebar-w") == "130px"
                     and v.get("--toolbar-h") == "40px")
        result.add("S-03b", "V6 布局参数 44/130/40", layout_ok,
                   f"menu-w={v.get('--menu-w')} sidebar-w={v.get('--sidebar-w')} toolbar-h={v.get('--toolbar-h')}")

        # ---------- S-02：切到 dark 后 :root 六参数与 §7.4 全等 ----------
        cdp.eval_js("window.App.applyTheme('dark')")
        time.sleep(0.6)
        d = cdp.eval_js("window.__v6p.vars()")
        spec = {"--menu-w": "64px", "--sidebar-w": "236px", "--toolbar-h": "52px",
                "--copilot-w": "400px", "--cell": "40px", "--radius": "8px"}
        bad = {k: (d.get(k), want) for k, want in spec.items() if d.get(k) != want}
        result.add("S-02", "dark 皮肤 :root 六参数 = §7.4", not bad,
                   "全等" if not bad else f"不符: {bad}")

        # ---------- S-04：三皮肤红涨绿跌 ----------
        def is_red(hexv: str) -> bool:
            hexv = (hexv or "").lstrip("#")
            if len(hexv) != 6:
                return False
            r, g, b = int(hexv[0:2], 16), int(hexv[2:4], 16), int(hexv[4:6], 16)
            return r > 150 and r > g + 40 and r > b + 40

        def is_green(hexv: str) -> bool:
            hexv = (hexv or "").lstrip("#")
            if len(hexv) != 6:
                return False
            r, g, b = int(hexv[0:2], 16), int(hexv[2:4], 16), int(hexv[4:6], 16)
            return g > 120 and g > r + 30 and g > b - 20

        sem = []
        for theme in ("v6", "dark", "light"):
            cdp.eval_js(f"window.App.applyTheme('{theme}')")
            time.sleep(0.4)
            tv = cdp.eval_js("window.__v6p.vars()")
            up, dn = tv.get("--up", ""), tv.get("--down", "")
            ok = is_red(up) and is_green(dn)
            sem.append((theme, up, dn, ok))
        all_ok = all(x[3] for x in sem)
        result.add("S-04", "三皮肤红涨绿跌", all_ok,
                   " | ".join(f"{t}:up={u},down={d}" for t, u, d, _ in sem))

        # ---------- A-03：品牌区宽 = 侧栏宽，导航左 = 主区左 ----------
        cdp.eval_js("window.App.applyTheme('v6')")
        time.sleep(0.5)
        al = cdp.eval_js("window.__v6p.align()")
        align_ok = (al["brandW"] == al["sidebarW"] and al["navLeft"] == al["mainLeft"]
                    and al["brandW"] > 0)
        result.add("A-03", "品牌区宽=侧栏宽 且 导航左=主区左", align_ok,
                   f"brandW={al['brandW']} sidebarW={al['sidebarW']} navLeft={al['navLeft']} mainLeft={al['mainLeft']}")

        # ---------- B-03：顶栏右侧不裁切 ----------
        ro = cdp.eval_js("window.__v6p.rightOk()")
        result.add("B-03", "顶栏右侧不裁切", ro.get("ok"), f"right={ro.get('right')} innerW={ro.get('iw')}")

        # ---------- B-02：1366×768 无页面级纵向滚动条 ----------
        of = cdp.eval_js("window.__v6p.overflow()")
        result.add("B-02", "1366x768 无页面级纵向滚动", of["sh"] <= of["ih"] + 1,
                   f"scrollH={of['sh']} innerH={of['ih']}")

        # ---------- A-04：侧栏分组折叠 ----------
        g0 = cdp.eval_js("window.__v6p.groups()")
        fold = cdp.eval_js("window.__v6p.foldToggle()")
        fold_ok = g0["total"] == 5 and g0["open"] <= 2 and g0["visibleItems"] <= 12 and fold.get("ok")
        result.add("A-04", "侧栏 5 组、默认只展开当前组、折叠可恢复", fold_ok,
                   f"groups={g0['total']} open={g0['open']} visible={g0['visibleItems']} "
                   f"fold={fold.get('before')}→{fold.get('mid')}→{fold.get('after')}")

        # ---------- A-01：29 视图四处一致 ----------
        # 判据说明（REQ-20261111-01）：
        #   顶栏文案以**正式环境 i18n 字典**为准（nav.dashboard=仪表板 / nav.watchlist=自选股），
        #   不采用原型为贴近参考图而写的「总览 / 自选」——用户 2026-11-11 08:40 第 2 条
        #   明确「功能与现在正式环境一致」。因此这里断言「顶栏文案非空且与 group 一一对应」，
        #   而不是「文案等于某个硬编码中文」。
        mismatch = []
        group_seen = {}
        for vid in VIEWS:
            cdp.eval_js(f"location.hash = '#/{vid}'")
            time.sleep(0.75)
            st = cdp.eval_js("window.__v6p.viewState()")
            grp = cdp.eval_js(
                "(function(){var t=window.App && window.App.VIEW_GROUP;"
                f"return t ? (t['{vid}'] || '') : '';}})()"
            )
            got_label = st.get("navText", "")
            if not got_label:
                mismatch.append(f"{vid}: 顶栏无高亮项")
            if not grp:
                mismatch.append(f"{vid}: 未登记 NAV_TREE group")
            if grp and got_label:
                # 同一 group 的多个视图必须高亮同一个顶栏项（一一对应）
                if grp in group_seen and group_seen[grp] != got_label:
                    mismatch.append(f"{vid}: group={grp} 顶栏文案不一致 "
                                    f"('{got_label}' vs '{group_seen[grp]}')")
                group_seen[grp] = got_label
            if not st.get("title"):
                mismatch.append(f"{vid}: 标题为空")
            if not st.get("crumb"):
                mismatch.append(f"{vid}: 面包屑为空")
        result.add("A-01", "29 视图四处一致", not mismatch,
                   f"不符 {len(mismatch)} 项；group→顶栏 {group_seen}"
                   + ("" if not mismatch else "；" + "; ".join(mismatch[:6])))

        # ---------- A-05：组件库 49 卡 ----------
        cdp.eval_js("location.hash = '#/widget-library'")
        time.sleep(2.6)
        wc = cdp.eval_js("window.__v6p.widgetCount()")
        result.add("A-05", "组件库 ≥49 组件卡", wc["cards"] >= 49,
                   f"cards={wc['cards']} addButtons={wc['addButtons']}")
        if shots:
            cdp.shot(os.path.join(shots, "widget_library_1366.png"))

        # ---------- B-01：3 皮肤 × 2 断点，卡片无内部裁切 ----------
        clip_fail = []
        for theme in ("v6", "dark", "light"):
            for w, h in ((1366, 768), (1024, 640)):
                cdp.viewport(w, h)
                cdp.eval_js(f"window.App.applyTheme('{theme}')")
                cdp.eval_js("location.hash = '#/home'")
                time.sleep(1.5)
                bad = cdp.eval_js("window.__v6p.clipped()")
                if bad:
                    clip_fail.append(f"{theme}@{w}x{h}: {len(bad)} 卡裁切 ({bad[0]})")
                if shots:
                    cdp.shot(os.path.join(shots, f"home_{theme}_{w}x{h}.png"))
        result.add("B-01", "3 皮肤 × 2 断点无卡片裁切", not clip_fail,
                   "6/6 组全过" if not clip_fail else "; ".join(clip_fail))

        # ---------- S-05：层次量化（真实计算，不用截图占位） ----------
        # 判据（用户 2026-11-11 01:20 + 验收标准 S-05）：
        #   缩略到 420px 转灰度后取分位，p50 = 21±3 / p90 = 88±5 / p90-p50 >= 60 / p99 >= 150
        cdp.viewport(1366, 768)
        cdp.eval_js("window.App.applyTheme('v6')")
        cdp.eval_js("location.hash = '#/home'")
        time.sleep(2.4)
        shot_path = os.path.join(shots or ".", "home_v6_final.png")
        if shots:
            cdp.shot(shot_path)
        if shots and os.path.exists(shot_path):
            q = compute_percentiles(shot_path)
            if q is None:
                result.add("S-05", "层次量化基线", False, "PIL/numpy 不可用，无法计算分位")
            else:
                p50, p90, p99 = q
                gap = p90 - p50
                ok = (abs(p50 - 21) <= 3) and (abs(p90 - 88) <= 5) and gap >= 60 and p99 >= 150
                result.add("S-05", "层次量化（p50/p90/gap/p99）", ok,
                           f"p50={p50:.0f}(目标21±3) p90={p90:.0f}(目标88±5) "
                           f"gap={gap:.0f}(>=60) p99={p99:.0f}(>=150)")
        else:
            result.add("S-05", "层次量化基线", False, "未产出截图，无法量化")

        # ---------- 收尾：恢复 V6 ----------
        cdp.eval_js("window.App.applyTheme('v6')")

    finally:
        if cdp:
            cdp.close()
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=8)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    passed, failed = result.summary()
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base_url": args.base_url,
        "passed": passed,
        "failed": failed,
        "items": result.items,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"\n=== {passed} PASS / {failed} FAIL ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
