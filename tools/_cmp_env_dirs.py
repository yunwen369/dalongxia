# -*- coding: utf-8 -*-
"""只读：历史正式环境备份 vs 当前正式环境 功能清单对比（不改任何文件）。

对比口径（三层）：
  L1 文件清单：各环境 _internal/frontend/ 下全部文件相对路径集合差异（增/删）
  L2 视图定义：views.js 中 V.<id> = { 的 id 集合差异
  L3 前端 JS 域文件：frontend/js 下 .js 文件清单差异
输出：JSON + 可读摘要
"""
import os, re, json, sys

ROOT = r"F:/workbuddy记录/OpenBB Terminal Pro/A_运行资产"
OUT = sys.argv[1] if len(sys.argv) > 1 else None

envs = []
for name in sorted(os.listdir(ROOT)):
    p = os.path.join(ROOT, name)
    if not os.path.isdir(p):
        continue
    if name == "正式环境" or name.startswith("正式环境_v"):
        ver = ""
        vf = os.path.join(p, "VERSION.txt")
        if os.path.exists(vf):
            ver = open(vf, encoding="utf-8", errors="replace").readline().strip()
        envs.append((name, p, ver))

def rel_files(base):
    out = set()
    if not os.path.isdir(base):
        return out
    for dp, dn, fn in os.walk(base):
        for f in fn:
            out.add(os.path.relpath(os.path.join(dp, f), base).replace("\\", "/"))
    return out

def view_ids(js_path):
    if not os.path.exists(js_path):
        return set()
    src = open(js_path, encoding="utf-8", errors="replace").read()
    return set(re.findall(r"\bV\.([a-z0-9_\-]+)\s*=\s*\{", src))

result = {"envs": [], "order": []}
snap = {}
for name, p, ver in envs:
    fe = os.path.join(p, "_internal", "frontend")
    files = rel_files(fe)
    js = os.path.join(fe, "js")
    jsfiles = {f[len("js/"):] for f in files if f.startswith("js/") and f.endswith(".js")}
    views = view_ids(os.path.join(js, "views.js"))
    snap[name] = {"ver": ver, "files": files, "jsfiles": jsfiles, "views": views}
    result["order"].append(name)
    result["envs"].append({"name": name, "version": ver,
                           "frontend_files": len(files), "js_files": len(jsfiles),
                           "views": len(views)})

print("== L0 环境清单 ==")
for e in result["envs"]:
    print(f"  {e['name'][:46]:48s} ver={e['version'][:22]:24s} frontend文件={e['frontend_files']:4d} js={e['js_files']:3d} 视图={e['views']:3d}")

cur = "正式环境"  # 当前正式环境（显式指定，避免中文排序把 v3.5.0 备份当现行）
print(f"\n== L1/L2/L3 对比：各历史环境 → 当前({cur}) ==")
diffs = {}
for name in result["order"][:-1]:
    a, b = snap[name], snap[cur]
    removed_files = sorted(a["files"] - b["files"])
    added_files = sorted(b["files"] - a["files"])
    removed_js = sorted(a["jsfiles"] - b["jsfiles"])
    removed_views = sorted(a["views"] - b["views"])
    added_views = sorted(b["views"] - a["views"])
    diffs[name] = {"ver": a["ver"], "removed_files": removed_files,
                   "added_files": added_files, "removed_js": removed_js,
                   "removed_views": removed_views, "added_views": added_views}
    flag = "缺失!" if (removed_files or removed_js or removed_views) else "无缺失"
    print(f"\n  {name[:46]} ({a['ver'][:18]})  →  {flag}")
    print(f"    文件删除 {len(removed_files):3d} | js删除 {len(removed_js):2d} | 视图删除 {len(removed_views):2d} | 文件新增 {len(added_files):3d} | 视图新增 {len(added_views):2d}")
    if removed_js:
        print(f"      js 删除明细: {removed_js[:12]}{' ...' if len(removed_js) > 12 else ''}")
    if removed_views:
        print(f"      视图删除明细: {removed_views}")
    if removed_files and len(removed_files) <= 15:
        print(f"      文件删除明细: {removed_files}")

result["diffs"] = {k: {kk: (sorted(vv) if isinstance(vv, set) else vv) for kk, vv in v.items()} for k, v in diffs.items()}
if OUT:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n证据已写: {OUT}")
