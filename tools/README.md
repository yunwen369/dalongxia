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
| `diag_frontend_data.py` | **前端数据加载诊断**（登录后逐视图采集 fetch 失败/JS 异常，区分接口 401/404/超时 vs 渲染失败；复用 ui_clickthrough 的 CDP harness。v3.6.1 修复 D-3：标题判据 `#page-title`→`#view-title`、空态 `.pg-empty`→`.v-empty`） | `python tools/diag_frontend_data.py --base <url> --token <口令> --out diag.json` |
| `human_walkthrough.py` | **人工模拟连续操作走查**（v2.0 / 2026-11-14 判据层重构：24 场景 A 启动·B 导航·C 侧栏折叠·D 三皮肤·E 视图内交互·F 断点·G 空态恢复·Z 无 JS 异常）。v3.6.1 修复 D-3：v1.0 判据绑定已退役 v6 壳层（`#page-title`/`#main-nav`/`.sidebar-item`/`window.App|Pages|State4`）→ 任何实例都 `RuntimeError: 页面未就绪`；现按现行 V6/W7 契约重写（见文件头映射表；B3 面包屑改为「末项=标题」、E2 改为「表行右键菜单 6 项」，因现行面包屑非导航控件、预览表行不跳转） | `python tools/human_walkthrough.py --base-url <url> --out <json> [--shots <dir>]` |
| `v6_skin_probe.py` | ⛔**已退役（2026-11-12）**：断言层对应 v6 旧壳层（`--menu-w`/`window.App.applyTheme`/NAV_TREE），默认拒跑（退出码 2），需 `--legacy`。**CDP 原语（`CDP`/`launch_edge`/`open_target`/`free_port`）仍被 `human_walkthrough`/`ui_clickthrough`/`shoot_states` 复用，不得改为导入期报错**；其残留 v6 选择器只存在于退役断言层，不参与任何判据 | `python tools/v6_skin_probe.py`（退役）/ `--legacy`（历史取证） |
| `comment_rate_check.py` | 注释率快查 | `python tools/comment_rate_check.py` |
| `docstring_audit.py` | 注释率审计（出 JSON/MD） | `python tools/docstring_audit.py --out-json <p> --out-md <p>` |
| `docstring_fill.py` | docstring 批量补齐（--dry-run/--apply/--js） | `python tools/docstring_fill.py --dry-run` |
| `envelope_baseline.py` | **端点信封契约基线**：改造前采集 → 改造后重采 → 结构/五字段比对（0 差异才放行） | `python tools/envelope_baseline.py capture --base <url> --out before.json`；`compare --before a.json --after b.json` |
| `eval_in_page.py` | 页面内 CDP 求值助手 | 被其它取证脚本引用 |
| `evidence_four_domains.py` | 四域增强交互取证 | `python tools/evidence_four_domains.py` |
| `evidence_symbol_store.py` | 符号共享 store 取证 | `python tools/evidence_symbol_store.py` |
| `hash_manifest.py` | 归档哈希清单 | `python tools/hash_manifest.py <目录>` |
| `make_icon.py` | 生成打包图标 | 封装前 |
| `scan_ui_leaks.py` | UI 泄漏扫描（v3.6.1 修复 D-3：标题判据 `#page-title`→`#view-title`） | `python tools/scan_ui_leaks.py --base-url <url> --out <json>` |
| `stress_v8.py` | 同花顺 V8 并发回归 | `python tools/stress_v8.py -u` |
| `yjb_scan.py` | 养基宝扫码助手 | `python tools/yjb_scan.py new\|poll\|status` |
| `ui_clickthrough.py` | 通用 CDP harness（CDP/launch_chrome/page_ws/set_viewport/click_at/eval_js）+ 全页真实点击（含就绪闸门，白屏不再记 0 错误） | `python tools/ui_clickthrough.py --base-url http://127.0.0.1:6903` |
| `probe_endpoints.py` | 端点全量探针（116 条，分类 DATA/EMPTY_OK/DEGRADED/SKIPPED） | `python tools/probe_endpoints.py --base <url> --out <json>` |
| `cross_validate.py` | 双源交叉验证 12 项（报价/指数/K线/板块/资金流/净值/汇率/时区/拥挤度/美股分时；v3.6.1 修复 D-4：拥挤度单查读超时 30s→180s，对齐端点冷启动口径） | `python tools/cross_validate.py --base <url> --out <json>` |
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
| `_cdp_v350_features.py` | **v3.5.0 新功能 CDP 界面专项探针**（REQ-20261105-02..05 批次：经理画像 F1 / 持仓走势卡 F2 / 订阅流 F3 / 利率罗盘 F4 / 定时研判卡 F5 / 净值三档 F6 / 表格分页 F7 / 列配置 F8 / 美股四态徽章 F9+R12 / 拥挤度分项口径列 F12 / 搜索禁用态 R18，11 项真实浏览器点击；口令实例自动 /auth。F5/F7/F12 异步慢接口轮询等待（v3.6.1 修复 D-4：F12 等待上限 20s→60s，对齐「首次约 10–40 秒」端点口径）；F8 前清 `v7-cols:sectors` 记忆防 profile 复用污染） | `python tools/_cdp_v350_features.py --base http://127.0.0.1:6903 --out <json>` |
| `_cdp_r14_summary.py` | **R14 三市场财报中文摘要 CDP 专项探针**（5 判据：资讯工具市场下拉渲染 / 市场切换状态持久 / fin 视图注入卡 / ann 卡共用状态 / 执行链可达；口令实例自动 /auth。2026-11-13 回归批次加固：auth 后加登录墙清除闸门——整页 reload + 轮询 `view-body` 就绪，防 cookie 生效晚于首次 goto 造成 R14_1/2 假阴） | `python tools/_cdp_r14_summary.py --base http://127.0.0.1:6903 --out <json>` |
| `_diag_topnav_sweep.py` | **顶栏可点性宽度扫描**（8 断点 × 3 皮肤：8 个分组按钮是否被遮挡 / 文档是否横向溢出 / 右状态区裁切项；D-8 回归判据。注意 `elementFromPoint` 只作布局参考，**可点性只认真实点击**） | `python tools/_diag_topnav_sweep.py --base <url> --out <json>` |
| `_diag_topnav_click.py` | **顶栏点击诊断**（逐分组真实点击 + 命中元素/坐标/hash/高亮取证；`--pre-nav` 复现 B1 前置状态） | `python tools/_diag_topnav_click.py --base <url> --out <json> [--pre-nav]` |
| `_probe_first_paint.py` | **首屏里程碑耗时**（导航→标题→指数卡→迷你走势→无「加载中」各里程碑秒数；D-2 回归判据） | `python tools/_probe_first_paint.py --base <url> --out <json>` |
| `_probe_home_spark.py` | **首页指数走势卡数据实测**（4 张卡的 SVG/「走势无数据」+ 图表分析页 canvas + 控制台错误 + 截图；D-1 回归判据） | `python tools/_probe_home_spark.py --base <url> --out <json> [--settle N] [--shots <dir>]` |
| `_probe_f12_crowding.py` | **拥挤度单查长等待补验**（等待上限 120s，断言「口径说明」列 + 「仅参考」；澄清 `_cdp_v350_features` F12 的 20s 假阴） | `python tools/_probe_f12_crowding.py --base <url> --out <json> [--wait N]` |
| `_perf_latency.py` | **首屏/慢端点冷热耗时**（14 项首屏 + 5 项慢点，逐项 elapsed/HTTP/字节；冷启动实测用） | `python tools/_perf_latency.py --base <url> --out <json> --label cold` |
| `_cmp_env_dirs.py` | **历史运行环境 vs 现行 清单对比**（各环境 `_internal/frontend` 文件/js/视图 三层差异，判定功能缺失） | `python tools/_cmp_env_dirs.py <out.json>` |
| `_run_ui_battery.sh` | **UI 探针串行编排**（nav → clickthrough → panels → mobile → v350 → r14 → walkthrough；自动清 safe-delete 守卫变量，避免限流/假失败） | `bash tools/_run_ui_battery.sh <base_url> <tag> <evidence_dir> [shots_root]` |
| `_diag_r14_news_inject.py` | **登录墙/注入链一次性诊断**（v3.6.0 回归批次定位 R14_1 假阴根因所用：分步 dump 登录墙状态 / view-body / `.v7-news` / 下拉 / W7 注册 / 控制台错误；口令 json 转义注入。可复用于任何「SPA 渲染到哪一层」的分层诊断） | `python tools/_diag_r14_news_inject.py`（BASE/TOKEN 内置读 env） |
| `_probe_v370_features.py` | **v3.7.0 看盘体验补齐专项探针（13 项）**：F1 指数卡走势 / F2 云图 / F3 行业资金流 / F4 完整K线 / F5 中文表头 / F6 全表排序 / F7 快讯原文 / F10 拥挤度·风控「计数+名单」（`data-total` 必须等于该档计数）/ F11 板块研判三级选择器（断言不含 `kind 仅支持`，防参数口径降级）/ F12 仪表板管理 CRUD+右键（新建→复读→删除→复读）/ F13 顶栏搜索含板块 / F14 组件自由拉伸（拖把手→高度变化→`h` 落库）/ F9 控制台 0 错。**F12/F14 会真实改 `/api/dashboards`，探针开头备份整表、结尾自动回滚** | `python tools/_probe_v370_features.py --base <url> --out <json>` |
| `_diag_v37_inject.py` | **「渲染后注入」类卡片诊断**（分步 dump `currentId/hash`、`#view-body` 卡片标题、`__inject_errors`、手动调用注入器是否成功；`--seq` 用 hash 逐步切换复现 SPA 导航）。用于定位「切路由偶发不出现」——根因是 `renderGeneric` 的 `await` 后 `innerHTML` 覆盖，v3.7.0 已改为 `view:rendered` 事件驱动 | `python tools/_diag_v37_inject.py --base <url> --view market --seq "market,today" [--seqwait 8]` |
| `_diag_fund_hang.py` | **接口「挂起」打栈定位**（faulthandler `dump_traceback_later` 定时打印全部线程栈，定位到底卡在哪个函数）。2026-11-15 用它定位到全量 pytest 偶发挂起：`fund_combo.evaluate → fund.get_style_history → stock.get_profile → tencent.get_quote → http_get_text` 停在 `ssl_wrap_socket`（TLS 握手），根因是「季度期数 × 前 10 大重仓股」串行查行业，链路抖动时被重试放大到 20 分钟以上 → 已加 `STYLE_INDUSTRY_BUDGET` 墙钟预算 | `python tools/_diag_fund_hang.py --after 60 [--node <pytest节点>] [--repeat]` |
| `_probe_ths_rate.py` | **同花顺行情数据域（d.10jqka.com.cn）限流容忍度实测**：按指定间隔/并发打一轮 K 线文件，统计状态码分布，用于判定 `ths_http._POLITE_DELAY` 能否下调。2026-11-15 实测间隔 0 / 8 并发三轮共 440 次请求全部 200（73–81 QPS）→ 该域间隔由 0.1s 降至 0.01s（板块排行冷启动 19.5s→6.1s 的关键一环）。判据：出现任何 401/403 即判不安全 | `python tools/_probe_ths_rate.py [--gap 0] [--workers 8] [--boards 90] [--files both] [--join]` |
| `_prof_sector_endpoints.py` | **板块端点冷启动耗时剖析**（cProfile 热点 + 分阶段计时 + 末尾打印缓存命中状态，用于区分「算得慢」与「每次都重算」）。**注意：Python 3.13 的 cProfile 会一并剖析工作线程，绝对墙钟被放大约 2.5 倍，横向比热点可以、报耗时必须用直接计时** | `python tools/_prof_sector_endpoints.py --target fundflow/rankings/both --top N` |
| `_verify_sector_equiv.py` | **v3.7.1/v3.7.2 板块端点提速的取值等价性对拍**：A 段对比「四档串行取数 vs 并发预取」逐字段是否一致（含 None 不补 0 的降级行为）；B 段验证 `limit=N` 返回 == 全量前 N 条；**C/D 段对比「改造前串行实现（`_ref_correlation`/`_ref_turnover` 独立复刻）vs 当前并发实现」的相关性/换手率**；E 段验证换手率分母换口径只缩放水平值、不动分位。返回码 0=全部等价 | `python tools/_verify_sector_equiv.py [--skip-rankings] [--skip-crowding] [--board 半导体]` |
| `_prof_crowding.py` | **单板块拥挤度（`/api/sector/crowding`）耗时拆解**：直接计时（不用 cProfile）分步测 `_board_float_cap` / `_constituent_correlation` / `_constituent_turnover`，并打印各 part 权重与 pct。`--raw` 拆内部、`--fill` 先同步补齐市值缓存再测 | `python tools/_prof_crowding.py --board 半导体 [--raw] [--fill]` |
| `_fill_board_caps.py` | **批量补齐 `data/board_cap.json`（板块成分流通市值合计落盘缓存）**。⚠️ **限速**：实测连续补 **52 个板块（约 7 分钟、400+ 次 q 域请求）触发同花顺 401 反爬**（15s 自愈），故默认 `--limit 30` + 板块间 `--sleep 1`；补更多请分多次跑，**不要 `--limit 0` 一把梭** | `python tools/_fill_board_caps.py [--limit 30] [--sleep 1] [--check]` |
| `ui_clickthrough.py` | **19 视图全遍历 clickthrough + 截图**（控制台错误捕获 / 视图标题 / 交互点击；2026-11-13 修复：就绪判据 `page-title` → `view-title`——V6 整站替换（v3.1.0）后旧元素退役导致探针无法就绪） | `python tools/ui_clickthrough.py --base-url http://127.0.0.1:6905 --out <json> --shots <dir>` |
