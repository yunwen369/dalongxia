# -*- coding: utf-8 -*-
"""R14_1 news 下拉缺失根因诊断（一次性诊断工具，不改应用代码）。

步骤：launch Edge → / → POST /auth（json 转义 token）→ 整页 reload →
goto #/news → 分步等待，逐层 dump：登录墙/view-body/.v7-news/下拉/W7 注册/控制台错误。
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ui_clickthrough import CDP, eval_js, launch_chrome, page_ws  # noqa: E402

BASE = "http://127.0.0.1:6903"
TOKEN = os.environ.get("OPENBB_ACCESS_PASSWORD", "")

CONSOLE_HOOK = """
(function(){
  window.__diagErrs = [];
  ['error','warn'].forEach(function(k){
    var orig = console[k];
    console[k] = function(){ try{ window.__diagErrs.push(k+': '+[].slice.call(arguments).join(' ').slice(0,200)); }catch(e){} orig.apply(console, arguments); };
  });
  window.addEventListener('error', function(e){ try{ window.__diagErrs.push('onerror: '+e.message+' @'+e.filename+':'+e.lineno); }catch(e2){} });
})();
"""


def dump(cdp, tag):
    r = eval_js(cdp, """(function(){
      var body = document.body;
      return JSON.stringify({
        bodyLen: body ? body.innerText.length : -1,
        bodyHead: body ? body.innerText.slice(0, 80).replace(/\\n/g, '|') : '',
        viewBody: !!document.getElementById('view-body'),
        newsCard: !!document.querySelector('.v7-news'),
        newsSel: !!document.getElementById('v7-news-market'),
        viewTitle: (document.getElementById('view-title')||{}).textContent||'',
        hasW7: typeof window.W7 !== 'undefined',
        stNewsInput: (window.W7 && W7.st && W7.st.newsInput) ? JSON.stringify(W7.st.newsInput) : null,
        dataKeys: (window.W7 && W7.data) ? Object.keys(W7.data).slice(0,20).join(',') : null,
        errs: (window.__diagErrs||[]).slice(0,6)
      });
    })()""") or {}
    print(f"--- {tag} ---")
    for k, v in r.items():
        print(f"  {k} = {v}")
    return r


def main():
    profile = tempfile.mkdtemp(prefix="edge_r14diag_")
    proc = launch_chrome(9358, profile, 1366, 768)
    try:
        time.sleep(4)
        cdp = CDP(page_ws(9358))
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": CONSOLE_HOOK})

        cdp.call("Page.navigate", {"url": BASE + "/"})
        time.sleep(3.5)
        dump(cdp, "首次进入 /（未鉴权）")

        token_js = json.dumps(TOKEN)  # json 转义 = JS 字符串字面量
        authed = eval_js(cdp, (
            "(function(){return fetch('/auth',{method:'POST',headers:{'Content-Type':"
            "'application/x-www-form-urlencoded'},body:'token='+encodeURIComponent(" + token_js + ")})"
            ".then(function(r){return 'auth:'+r.status;})"
            ".catch(function(e){return 'auth-err:'+String(e);});})()"
        ))
        print("auth 结果 =", authed)
        time.sleep(1.0)

        cdp.call("Page.navigate", {"url": BASE + "/#/news"})
        for i in range(5):
            time.sleep(3)
            r = dump(cdp, f"reload→#/news t+{(i+1)*3}s")
            if r.get("newsSel"):
                print(">>> 下拉已出现，提前结束")
                break
        print("=== 最终 __diagErrs ===")
        print(eval_js(cdp, "JSON.stringify(window.__diagErrs||[])"))
    finally:
        proc.terminate()


if __name__ == "__main__":
    main()
