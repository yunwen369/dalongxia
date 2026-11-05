# -*- coding: utf-8 -*-
"""
check_spec_consistency.py — 工作规范内部一致性自检（19.5 升版门禁 · 项目内唯一实现）
PEP8；仅 stdlib；Python 3.11+
用法: python check_spec_consistency.py <规范md路径>
检查项:
  1. 提取全部章节标题编号，构建"已定义章节集合"
  2. 提取全文交叉引用（X.Y / 第X章 / 附录X），核对悬空引用
  3. 旧编号残留扫描（第十六章模板残留 16.x、v7.x 写死、_rN、四套发布序列特征串）
  4. 关键术语单点性（发布步骤序列定义处数量、"缺一不可"残留、门禁定义重复）
输出: 控制台报告 + 退出码(0 通过 / 1 有发现)
"""
from __future__ import annotations
import re
import sys
from collections import defaultdict
from pathlib import Path

CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
          "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12, "十三": 13,
          "十四": 14, "十五": 15, "十六": 16, "十七": 17, "十八": 18,
          "十九": 19, "二十": 20}


def load(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def check(lines: list[str]) -> dict[str, list[str]]:
    findings: dict[str, list[str]] = defaultdict(list)
    # --- 1. 已定义章节/小节集合 ---
    defined: set[str] = set()
    ch_re = re.compile(r"^##\s*第([一二三四五六七八九十]+)章")
    sec_re = re.compile(r"^###\s*(\d+)\.(\d+)")
    cur_ch = None
    for ln in lines:
        m = ch_re.match(ln)
        if m:
            cur_ch = CN_NUM.get(m.group(1))
            if cur_ch:
                defined.add(str(cur_ch))
                defined.add(f"{cur_ch}.")
            continue
        m = sec_re.match(ln)
        if m:
            defined.add(f"{m.group(1)}.{m.group(2)}")
            defined.add(f"{m.group(1)}.")
    # 附录 A-N
    app_re = re.compile(r"^#\s*附录\s*([A-N])")
    for ln in lines:
        m = app_re.match(ln)
        if m:
            defined.add("附录" + m.group(1))

    # --- 2. 交叉引用悬空 ---
    ref_re = re.compile(r"(?<![\d.～\-])(\d{1,2})\.(\d{1,2})(?![\d%])")
    for i, ln in enumerate(lines, 1):
        # 跳过版本形态 v7.6 / SHA / 日期 / 端口 / 文件大小上下文
        for m in ref_re.finditer(ln):
            a, b = m.group(1), m.group(2)
            key = f"{a}.{b}"
            if key in defined:
                continue
            # 允许 4.1-1 等条目引用: 只要 a.b 定义存在即可; 不存在则记录
            ctx = ln.strip()[:90]
            # 排除明显非章节引用: 前后为 v/V 或 数字.数字.数字 版本号
            s = m.start()
            if s > 0 and ln[s - 1] in "vV.":
                continue
            findings["悬空交叉引用"].append(f"L{i} [{key}] {ctx}")

    # --- 3. 旧编号/残留扫描 ---
    pat_specs = {
        "残留_第十六章模板旧引用(16.1-16.8)": re.compile(r"16\.[1-8](?![\d])"),
        "残留_v7.1写死": re.compile(r"v7\.1(?![.\d])"),
        "残留__rN批次命名": re.compile(r"[_\-]?r\d+\b"),
        "残留_2456行198075字节旧基线": re.compile(r"198,?075|2456\s*行"),
        "残留_118/118断言写死": re.compile(r"118\s*/\s*118|118 个断言"),
        "残留_约57端点写死": re.compile(r"约\s*57|57\s*个端点"),
        "残留_基线40断言": re.compile(r"≥\s*40\s*(?:条|个)?\s*断言"),
        "残留_9个router域(未标目标态)": re.compile(r"9\s*个\s*router"),
        "残留_16个模块": re.compile(r"16\s*个模块"),
        "残留_缺一不可": re.compile(r"缺一不可"),
        "残留_DIFF-RQ": re.compile(r"diff\s+-rq", re.I),
    }
    # 19.5 判定口径：定位说明（^> **【vX.Y 定位说明】段落）、修订记录表（^| vX.Y |）、
    # 附则历史沿革条款、附录 O 豁免/收编表中的旧名示例——均属合法历史记录，不计残留。
    hist_ranges = []
    for i, ln in enumerate(lines, 1):
        if re.match(r"^> \*\*【v\d", ln) or re.match(r"^\| v\d", ln):
            hist_ranges.append(i)
    def in_hist(i: int) -> bool:
        # 命中行本身或其上一行是历史条目起始（表格行/引用段）即视为历史语境
        for j in (i, i - 1, i - 2):
            if j - 1 >= 0 and j <= len(lines):
                t = lines[j - 1]
                if re.match(r"^> \*\*【v\d", t) or re.match(r"^\| v\d", t):
                    return True
        return False
    for i, ln in enumerate(lines, 1):
        for name, rx in pat_specs.items():
            if rx.search(ln):
                if name.startswith("残留_v") and (
                    in_hist(i)
                    or re.search(r"[（(]v\d+\.\d+\s*(新增|起|按|新登记|说明|相对|裁决)", ln)
                    or re.match(r"^> \*\*v\d+\.\d+ 说明", ln)):
                    continue  # 历史版本号：定位说明/修订记录/条款溯源标注，合法沿革引用
                if name == "残留__rN批次命名" and ("豁免" in "".join(lines[max(0, i - 40):i]) or "对照" in ln):
                    continue  # 附录 O 示例
                findings[name].append(f"L{i} {ln.strip()[:100]}")

    # --- 4. 单点性: 发布序列是否只有一处"步骤清单" ---
    # 特征: "生成安装包" 且 "快捷方式" 且 "正式环境" 的编号步骤列表
    seq_heads = [i for i, ln in enumerate(lines, 1)
                 if re.search(r"发布(动作)?序列|发布顺序|发布步骤", ln)]
    findings["发布序列出现位置(人工核对单点性)"] = [
        f"L{i} {lines[i-1].strip()[:90]}" for i in seq_heads]

    # --- 5. 章号顺序完整性 ---
    chs = [CN_NUM[m.group(1)] for ln in lines
           if (m := ch_re.match(ln)) and m.group(1) in CN_NUM]
    if chs != list(range(1, max(chs) + 1)):
        findings["章号顺序异常"].append(f"实际章序: {chs}")

    # --- 6. 15/16章引用第二十章模板? ---
    tmpl = [i for i, ln in enumerate(lines, 1) if re.search(r"第十六章", ln)]
    findings["提及'第十六章'(应已改指第十八章或设计基准)"] = [
        f"L{i} {lines[i-1].strip()[:90]}" for i in tmpl]
    st = [i for i, ln in enumerate(lines, 1) if re.search(r"[^0-9]20\.8|16\.8", ln)]
    findings["提及 16.8/20.8 状态文档模板"] = [f"L{i} {lines[i-1].strip()[:90]}" for i in st]

    return findings


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    p = Path(sys.argv[1])
    lines = load(p)
    print(f"文件: {p}  共 {len(lines)} 行")
    findings = check(lines)
    total = 0
    for k in sorted(findings):
        v = findings[k]
        if not v:
            continue
        # 信息类(人工核对) 不计入问题数
        info = k.startswith("发布序列出现位置") or k.startswith("提及")
        tag = "INFO" if info else "FIND"
        print(f"\n== [{tag}] {k}: {len(v)} 处 ==")
        for x in v[:40]:
            print("  " + x)
        if len(v) > 40:
            print(f"  ...另 {len(v)-40} 处")
        if not info:
            total += len(v)
    print(f"\n自动判定问题总数(不含INFO): {total}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
