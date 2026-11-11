# tools/ 可复用工具（常驻）

> 一次性脚本（verify_/diag_/reconcile_/阶段验收类）已归档至
> `B_交付归档\03_测试报告\20261025_clean\tools_归档_一次性脚本\`（27 个，见其 README_归档说明.md）。
> 本目录只留**跨轮次可复用**工具（按下表常用入口使用；打包/发版/验收脚本见 `build_and_gate.py`、
> `release_gate.py`、`probe_endpoints.py`、`cross_validate.py`、`ui_clickthrough.py`）。

| 脚本 | 用途 | 调用 |
|---|---|---|
| `check_i18n_keys.py` | i18n 键命中校验 | `python tools/check_i18n_keys.py` |
| `check_css_theme_vars.py` | CSS 主题变量完整性（两主题齐备/禁背景色 fallback，AR-25） | `python tools/check_css_theme_vars.py` |
| `check_comment_drift.py` | 注释漂移静态校验（断点数字/AR 编号/Logo 承诺，AR-26） | `python tools/check_comment_drift.py` |
| `check_nav_tree.py` | **导航单一配置完整性**（侧栏视图全覆盖 NAV_TREE / 面包屑落点合法 / 顶栏分组合法 / 标题齐备，2026-11-10 新增） | `python tools/check_nav_tree.py` |
| `nav_consistency_probe.py` | **导航三处一致动态探针**（逐侧栏视图读真实渲染的顶栏/侧栏/面包屑；审查报告 §九 #4） | `python tools/nav_consistency_probe.py --base <url> --out nav.json` |
| `review_recheck.py` | **审查报告 §九 九项复核动态判定**（三视口×双主题 101 项：移动端/F5/四处一致/键盘/对比度/侧栏层级/卡片内容；原 `_ad_hoc` 脚本 2026-11-10 转正） | `python tools/review_recheck.py --base <url> --out <证据目录>` |
| `diag_frontend_data.py` | **前端数据加载诊断**（登录后逐视图采集 fetch 失败/JS 异常，区分接口 401/404/超时 vs 渲染失败；复用 ui_clickthrough 的 CDP harness） | `python tools/diag_frontend_data.py --base <url> --token <口令> --out diag.json` |
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
| `ui_clickthrough.py` | 通用 CDP harness（CDP/launch_chrome/page_ws/set_viewport/click_at/eval_js）+ 全页真实点击（含就绪闸门，白屏不再记 0 错误） | `python tools/ui_clickthrough.py --base-url http://127.0.0.1:6903` |
| `probe_endpoints.py` | 端点全量探针（116 条，分类 DATA/EMPTY_OK/DEGRADED/SKIPPED） | `python tools/probe_endpoints.py --base <url> --out <json>` |
| `cross_validate.py` | 双源交叉验证 12 项（报价/指数/K线/板块/资金流/净值/汇率/时区/拥挤度/美股分时） | `python tools/cross_validate.py --base <url> --out <json>` |
| `release_gate.py` | 发版门禁（pyflakes + 全量 pytest + 端点扫描 + 界面资源四段） | `python tools/release_gate.py --artifact 正式环境 --port 6916` |
| `build_and_gate.py` | 打包 + 版本三元派生（`sync_nsi()`）+ 门禁串联 | `python tools/build_and_gate.py` |
| `probe_v6_landing.py` | V6 定稿工程落地取证：解析原型引用端点并与本地服务逐条 HTTP 探测 | `python tools/probe_v6_landing.py --base http://127.0.0.1:6903` |
| `v6_landing_ui_probe.py` | V6 定稿页真实浏览器自测（29 视图 / 三皮肤 / V6_LIVE / 添加弹窗，复用 ui_clickthrough 的 CDP 原语） | `python tools/v6_landing_ui_probe.py --base-url http://127.0.0.1:6903` |
