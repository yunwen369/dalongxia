# -*- coding: utf-8 -*-
"""编排卡点击跳转探针（v3.7.3 T3 专项）。
6902 无口令实例 + 9223 无头 Chrome。断言：
J1 每张编排卡都有 .dw-jump 锚点且 href 合法
J2 CDP 真实点击 S-03 卡本体 → hash 变 #/sectors 且 view-title=板块行情
J3 点击 .dw-jump 锚点（F-01）→ #/fund 正常加载
J4 移除按钮点击不跳转（仍在 #/dashboard）
J5 移动宽度（375px 视口）下点击同样触发
退出码 0=全过。"""
import json, sys, io, time, urllib.request, urllib.parse, base64, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import websocket

BASE = "http://127.0.0.1:6902"
DEVTOOLS = "http://127.0.0.1:9223"
OUT = "tools/_out/ui_test_v373/jump_result.json"

def http_json(method, url):
    req = urllib.request.Request(url, method=method)
    return json.load(urllib.request.urlopen(req, timeout=30))

req = urllib.request.Request(DEVTOOLS + "/json/new?" + urllib.parse.quote(BASE + "/#/dashboard", safe=":/?#&="), method="PUT")
dev = http_json("PUT", DEVTOOLS + "/json/new?" + urllib.parse.quote(BASE + "/#/dashboard", safe=":/?#&="))
conn = websocket.create_connection(dev["webSocketDebuggerUrl"], timeout=120)
_seq = [0]
def send(m, p=None):
    _seq[0] += 1
    conn.send(json.dumps({"id": _seq[0], "method": m, "params": p or {}}))
    while True:
        msg = json.loads(conn.recv())
        if msg.get("id") == _seq[0]:
            return msg
def ev(expr):
    r = send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    res = (r.get("result") or {}).get("result", {})
    if "exceptionDetails" in r.get("result", {}):
        return None
    return res.get("value")

send("Runtime.enable"); send("Page.enable")
time.sleep(7)

fails = []
def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ((" | " + str(detail)[:200]) if detail else ""))
    if not ok: fails.append(name)

def cdp_click(sel):
    """真实派发 click：不在视口内先 scrollIntoView，elementFromPoint 校验命中后 dispatchEvent。"""
    return ev("""
(function(){
  var el=document.querySelector('""" + sel + """');
  if(!el) return "missing";
  el.scrollIntoView({block:"center"});
  var r=el.getBoundingClientRect();
  var cx=r.left+r.width/2, cy=r.top+r.height/2;
  var hit=document.elementFromPoint(cx,cy);
  if(!hit) return "nopoint";
  var opts={bubbles:true,cancelable:true,view:window,clientX:cx,clientY:cy};
  hit.dispatchEvent(new MouseEvent('mousedown',opts));
  hit.dispatchEvent(new MouseEvent('mouseup',opts));
  hit.dispatchEvent(new MouseEvent('click',opts));
  return "clicked@"+Math.round(cx)+","+Math.round(cy);
})()
""")

# J1 锚点齐全
j1 = ev("""
(function(){
  var cards=document.querySelectorAll('.demo-widget');
  var ok=0, bad=[];
  cards.forEach(function(c){
    var a=c.querySelector('.dw-jump');
    var h=a?a.getAttribute('href'):'';
    if(a && h && h.indexOf('#/')===0 && h!=='#') ok++; else bad.push(c.getAttribute('data-id'));
  });
  return JSON.stringify({cards:cards.length,ok:ok,bad:bad});
})()
""")
j1 = json.loads(j1)
check("J1 每张编排卡都有合法跳转锚点", j1["ok"] == j1["cards"] and not j1["bad"], j1)

# J2 点 S-03 卡本体 → #/sectors
ev("location.hash='#/dashboard'")
time.sleep(2.5)
r = cdp_click('.demo-widget[data-id="S-03"] .dw-head')
time.sleep(1.6)
h = ev("location.hash"); t = ev("(document.getElementById('view-title')||{}).textContent")
ch = ev("(document.getElementById('view-body')||document.getElementById('page-sectors')||{children:[]}).children.length")
check("J2 点击 S-03 卡本体 → #/sectors", h == "#/sectors", "hash=%s title=%s children=%s" % (h, t, ch))
check("J2b 目标页正常加载（标题+容器）", t == "板块行情" and (ch or 0) > 0, "%s/%s" % (t, ch))

# J3 点 F-01 的锚点 → #/fund
ev("location.hash='#/dashboard'")
time.sleep(2.5)
r = cdp_click('.demo-widget[data-id="F-01"] .dw-jump')
time.sleep(1.8)
h = ev("location.hash"); t = ev("(document.getElementById('view-title')||{}).textContent")
ch = ev("(document.getElementById('view-body')||{children:[]}).children.length")
check("J3 点击 F-01 锚点 → #/fund", h == "#/fund", "hash=%s" % h)
check("J3b 目标页正常加载", t == "基金详情" and (ch or 0) > 0, "%s/%s" % (t, ch))

# J4 移除按钮不跳转（用栈内备份回滚保护）
ev("location.hash='#/dashboard'")
time.sleep(2.5)
snap = ev("JSON.stringify(JSON.parse(JSON.stringify((function(){var j=JSON.parse(JSON.stringify(window.__dw_backup||null));return j;})( ))||null))")  # no-op 读取
before = ev("(JSON.parse(localStorage.getItem('dw_probe_before')||'null'))||true")
r = cdp_click('.demo-widget[data-id="S-04"] .dw-remove')
time.sleep(1.2)
h = ev("location.hash")
check("J4 点击移除按钮不跳转", h == "#/dashboard", "hash=%s click=%s" % (h, r))
# 恢复被删的 S-04：直接 PUT 回原布局（从服务端读改写）
ev("""
(function(done){
  fetch('/api/dashboards').then(function(x){return x.json()}).then(function(j){
    var data=j.data; var list=data.dashboards;
    var t=list.filter(function(x){return x.id===data.activeId})[0]||list[0];
    if(!t.widgets.some(function(w){return w.id==='S-04'})){
      t.widgets.splice(2,0,{id:'S-04',span:28});
    }
    return fetch('/api/dashboards',{method:'PUT',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({dashboards:list,activeId:data.activeId,groups:data.groups||[]})});
  });
})()
""")
time.sleep(1.0)

# J5 移动宽度 375px 下点击触发
send("Emulation.setDeviceMetricsOverride", {"width": 375, "height": 812, "deviceScaleFactor": 2, "mobile": True})
time.sleep(1.5)
ev("location.hash='#/dashboard'")
time.sleep(3.5)  # 移动视口下重渲染 + 微预览请求更慢，多等一轮
r = cdp_click('.demo-widget[data-id="S-03"] .dw-head')
time.sleep(1.8)
h = ev("location.hash")
check("J5 移动宽 375px 点击同样触发跳转", h == "#/sectors", "hash=%s click=%s" % (h, r))
send("Emulation.clearDeviceMetricsOverride")

# 控制台零错
errs = ev("(window.__cb_errors||[]).length")
check("J6 控制台 0 错误", not errs, "errs=%s" % errs)

conn.close()
result = {"base": BASE, "fails": fails, "j1": j1}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=1)
print("\n== 结果: %d 项失败 ==" % len(fails))
sys.exit(1 if fails else 0)
