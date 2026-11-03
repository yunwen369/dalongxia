# tools/ 可复用工具（常驻）

> 一次性脚本（verify_/diag_/reconcile_/阶段验收类）已归档至
> `B_交付归档\03_测试报告\20261025_clean\tools_归档_一次性脚本\`（27 个，见其 README_归档说明.md）。
> 本目录只留**跨轮次可复用**工具，共 18 个（下表为常用入口，其余为打包/发版/验收脚本）：

| 脚本 | 用途 | 调用 |
|---|---|---|
| `check_i18n_keys.py` | i18n 键命中校验 | `python tools/check_i18n_keys.py` |
| `comment_rate_check.py` | 注释率快查 | `python tools/comment_rate_check.py` |
| `docstring_audit.py` | 注释率审计（出 JSON/MD） | `python tools/docstring_audit.py --out-json <p> --out-md <p>` |
| `docstring_fill.py` | docstring 批量补齐（--dry-run/--apply/--js） | `python tools/docstring_fill.py --dry-run` |
| `envelope_baseline.py` | **端点信封契约基线**：改造前采集 → 改造后重采 → 结构/五字段比对（0 差异才放行） | `python tools/envelope_baseline.py capture --base <url> --out before.json`；`compare --before a.json --after b.json` |
| `eval_in_page.py` | 页面内 CDP 求值助手 | 被其它取证脚本引用 |
| `evidence_four_domains.py` | 四域增强交互取证 | `python tools/evidence_four_domains.py` |
| `evidence_symbol_store.py` | 符号共享 store 取证 | `python tools/evidence_symbol_store.py` |
| `hash_manifest.py` | 归档哈希清单 | `python tools/hash_manifest.py <目录>` |
| `make_icon.py` | 生成打包图标 | 封装前 |
| `scan_ui_leaks.py` | UI 泄漏扫描 | `python tools/scan_ui_leaks.py` |
| `stress_v8.py` | 同花顺 V8 并发回归 | `python tools/stress_v8.py -u` |
| `yjb_scan.py` | 养基宝扫码助手 | `python tools/yjb_scan.py new\|poll\|status` |
| `verify_frontier_fixes.py` | 通用 CDP harness（CDP/launch_chrome/page_ws/set_viewport 等） | 被 scan_ui_leaks 等取证脚本引用 |
