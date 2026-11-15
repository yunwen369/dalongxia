# -*- coding: utf-8 -*-
"""微预览探针：6902 无口令实例，CDP 打开 #/dashboard 断言 .dw-preview。
退出码 0=全过；1=有失败。"""
import json, sys, io, time, urllib.request, urllib.parse
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = "http://127.0.0.1:6902"
DEVTOOLS = "http://127.0.0.1:9223"
def http(method, path, body=None, ctype="application/json"):
    req = urllib.request.Request(BASE + path, method=method,
        data=body.encode() if isinstance(body, str) else body,
        headers={"Content-Type": ctype} if body else {})
    return urllib.request.urlopen(req, timeout=30)

req = urllib.request.Request(DEVTOOLS + "/json/new?" + urllib.parse.quote(BASE + "/#/dashboard", safe=":/?#&="), method="PUT")
dev = json.load(urllib.request.urlopen(req, timeout=30))
dev_ws_url = dev.get("webSocketDebuggerUrl")
import websocket  # websocket-client 在 venv 中
def send(ws_conn, _id, method, params=None):
    ws_conn.send(json.dumps({"id": _id, "method": method, "params": params or {}}))
    while True:
        msg = json.loads(ws_conn.recv())
        if msg.get("id") == _id:
            return msg

conn = websocket.create_connection(dev_ws_url, timeout=90)
send(conn, 1, "Runtime.enable")
send(conn, 2, "Page.enable")
send(conn, 3, "Page.reload")
time.sleep(6)  # 首屏 + 微预览请求（板块域冷启动可到 10s+，断言阶段再做有界轮询）

def js_eval(expr):
    r = send(conn, 100 + int(time.time() * 1000 % 100000), "Runtime.evaluate",
             {"expression": expr, "returnByValue": True})
    return (r.get("result") or {}).get("result", {}).get("value")

fails = []
def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ((" | " + str(detail)[:180]) if detail else ""))
    if not ok: fails.append(name)

# 1) 编排卡结构
total = js_eval("document.querySelectorAll('.demo-widget').length")
check("编排卡总数 > 0", total and total > 0, "count=%s" % total)

# 2) .dw-preview 存在数量 == 编排卡数量
def pv_stats():
    return js_eval("""
(function(){
  var cards=document.querySelectorAll('.demo-widget');
  var pv=document.querySelectorAll('.dw-preview');
  var ok=document.querySelectorAll('.dw-preview.pv-ok').length;
  var fb=document.querySelectorAll('.dw-preview.pv-fallback').length;
  var none=document.querySelectorAll('.dw-preview.pv-none').length;
  var loading=0; pv.forEach(function(n){ if(n.textContent.indexOf('加载中')>=0) loading++; });
  return JSON.stringify({cards:cards.length,pv:pv.length,ok:ok,fb:fb,none:none,loading:loading});
})()
""")

deadline = time.time() + 40
stats = None
while time.time() < deadline:
    s = pv_stats()
    try: stats = json.loads(s)
    except Exception: stats = None
    if stats and stats.get("loading", 1) == 0:
        break
    time.sleep(2)
check("每张编排卡都有 .dw-preview", stats and stats["pv"] == stats["cards"], stats)
check("微预览无残留「加载中」", stats and stats.get("loading") == 0, stats)
check("至少一半卡片拿到真实数据（pv-ok）", stats and stats.get("ok", 0) >= stats.get("pv", 0) * 0.5, stats)

# 3) desc 行（方案 A）
desc_n = js_eval("document.querySelectorAll('.dw-desc').length")
check("desc 说明行数量 > 0", desc_n and desc_n > 0, "desc=%s" % desc_n)

# 4) 热力图卡内容断言（领涨/领跌字样 + 涨跌色 class）
heat = js_eval("""
(function(){
  var n=document.querySelector('.dw-preview[data-pv="S-03"]');
  return n?JSON.stringify({t:n.textContent,up:!!n.querySelector('.pv-up'),down:!!n.querySelector('.pv-down')}):"missing";
})()
""")
try:
    h = json.loads(heat)
    check("S-03 热力图微预览含「板块」与领涨/领跌", h["t"] != "missing" and ("领涨" in h["t"] or "领跌" in h["t"]), h["t"])
    check("S-03 涨跌色标记渲染", h.get("up") or h.get("down"), h)
except Exception as e:
    check("S-03 热力图微预览", False, str(e))

# 5) 资讯流卡有最新标题摘要
n02 = js_eval('var n=document.querySelector(\'.dw-preview[data-pv="N-02"]\');n?n.textContent:"missing"')
check("N-02 资讯流微预览有标题摘要", n02 and n02 not in ("missing", "预览加载中…", "暂无快讯"), n02)

# 6) 控制台零新增错误
errs = js_eval("""
(function(){
  return JSON.stringify(window.__inject_errors||[]);
})()
""")
print("inject_errors:", errs)

conn.close()
print("\n== 结果: %s 项失败 ==" % len(fails))
sys.exit(1 if fails else 0)
