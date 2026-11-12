# tools/ 可复用工具（常驻）

> 一次性脚本（verify_/diag_/reconcile_/阶段验收类）已归档至
> `B_交付归档\03_测试报告\20261025_clean\tools_归档_一次性脚本\`（27 个，见其 README_归档说明.md）。
> 本目录只留**跨轮次可复用**工具（按下表常用入口使用；打包/发版/验收脚本见 `build_and_gate.py`、
> `release_gate.py`、`probe_endpoints.py`、`cross_validate.py`、`ui_clickthrough.py`）。

| 脚本 | 用途 | 调用 |
|---|---|---|
| `check_i18n_keys.py` | ⛔**已退役（2026-11-13）**：默认拒跑（退出码 2），需 `--legacy` 才执行。原：前端 i18n 键覆盖/重复/冲突校验——判据对象 `frontend/js/i18n.js` 已随 B1 前端减脂归档（多语言为非目标，新层无 i18n 体系） | ~~`python tools/check_i18n_keys.py`~~（历史取证加 `--legacy`） |
| `check_css_theme_vars.py` | CSS 主题变量完整性（颜色类两主题齐备 / 非颜色类任意样式表已定义 / 禁 `--bg-*` 色值 fallback，AR-25；2026-11-12 起纳入 v7.css 与跨文件布局变量） | `python tools/check_css_theme_vars.py` |
| `check_comment_drift.py` | 注释漂移静态校验（断点数字/AR 编号/Logo 承诺，AR-26） | `python tools/check_comment_drift.py` |
| `check_nav_tree.py` | **导航单一配置完整性**（判据源 2026-11-12 改为 `views.js` 的 `V6_VIEWS`：视图 id 唯一 / title 齐备 / crumb 末项=title / group 命中顶栏 / sidebar 命中分组且无空组 / 仅 `-edit` 可为 null） | `python tools/check_nav_tree.py` |
| `nav_consistency_probe.py` | **导航三处一致动态探针**（逐侧栏视图读真实渲染的顶栏/侧栏/面包屑；审查报告 §九 #4） | `python tools/nav_consistency_probe.py --base <url> --out nav.json` |
| `review_recheck.py` | ⛔**已退役（2026-11-12）**：默认拒跑（退出码 2），需 `--legacy` 才执行。原：断言基于 v2.x 壳层选择器，47 项为选择器漂移；四处一致/交互/皮肤已由 nav_consistency + functional + landing + v34_panels 覆盖，移动端已由 `mobile_layout_probe.py`（14 项）覆盖。保留作历史取证 | **审查报告 §九 九项复核动态判定**（三视口×双主题 101 项：移动端/F5/四处一致/键盘/对比度/侧栏层级/卡片内容；原 `_ad_hoc` 脚本 2026-11-10 转正） | `python tools/review_recheck.py --base <url> --out <证据目录>` |
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
| `v34_panels_probe.py` | **v3.4.0 新增面板深度探针**（板块工具/组合归因风险/系统运维/提醒已读/连接器测速/条件选股/AI 测试与摘要 + 12 秒刷新横切，24 项；默认只读，`--write` 才执行写操作） | `python tools/v34_panels_probe.py --base-url http://127.0.0.1:6901 --out <json>` |
| `mobile_layout_probe.py` | **移动端布局门禁**（390/768/1024 三档无横向溢出 · ≤768 汉堡+抽屉+遮罩 · 抽屉四条关闭路径 · 顶栏搜索/提醒在视口内 · 390 首页单列 · 0 控制台错误，14 项；替代已退役 review_recheck 的 R1 组） | `python tools/mobile_layout_probe.py --base-url http://127.0.0.1:6901 --out <json>` |
| `lib_smoke.js` | **lib 基础层行为门禁**（RF-20261112-01 阶段1b 落地：config 键枚举 7 键 / store 原始串+JSON 双口径 / fmt 数值百分比金额日期 / dom.esc 与 v7_app esc 逐字符等价，17 断言；lib/* 任何修改先过此门禁再跑 UI 探针） | `node tools/lib_smoke.js` |
| `check_layer_deps.py` | **前端层级依赖门禁**（RF-20261112-01 阶段6：7 条不变式——lib 纯度/唯一入口/W7 命名空间/挂载顺序/Utils 幽灵/裸 fetch/裸 echarts.init，扫 frontend/js 23 文件；字符串感知剥注释防文案误报） | `python tools/check_layer_deps.py` |
| `check_dup_patterns.py` | **后端重复模式门禁**（RF-20261112-01 阶段6：P1 手写退避循环→retry_call/retry_until；P2 信封专属字段 error_code 字面量→core/errors.py 唯一实现；ast 剥 docstring 防文案误报；`# dup-ok: 原因` 行级备案豁免，errors.py/resilient.py 唯一实现层整体豁免） | `python tools/check_dup_patterns.py` |
| `css_tokenize_migration.py` | **CSS 全量硬编码色值 token 化迁移**（v3.4.2 批次：app.css/v7.css 全部 hex/rgba 字面量→`var(--c-*)` 常量 token（166 个，:root 定义），三主题块定义行与注释保护，RGB(A) 解析逐位断言保证值等价；一次性迁移工具，重跑须先 git 还原） | `python tools/css_tokenize_migration.py [--apply]` |
| `probe_equivalence_diff.py` | **改前/改后探针 JSON 深度对拍**（v3.4.2 批次：baseline/after 三份探针 JSON 递归 diff，时变键与内嵌时间戳归一；views[9].cards(news)/views[10].cards(fin) 为数据条数驱动时变字段，甄别后豁免） | `python tools/probe_equivalence_diff.py` |
