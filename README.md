# OpenBB 中文投研工作台

1:1 复刻 OpenBB Workspace 的中文本地投研桌面软件（A股 / 港股 / 美股 / 场外基金）。
技术栈：Python 3.13 + FastAPI + 原生 HTML/CSS/JS + ECharts（本地，无 CDN）+ pywebview + akshare。
当前版本：**v1.0.1-20261021**（功能已交付，未升版）；后端 **65 个 `/api` 路径 / 71 个操作**，前端 **13 个路由视图 + 首页**，回归用例 **173 例**。

## 一、环境要求

| 项 | 要求 |
|---|---|
| 操作系统 | Windows 10/11 64 位 |
| Python | 3.13（本项目实测 3.13.14，见 `.venv`） |
| 端口 | `127.0.0.1:6900`（固定，不静默更换） |
| 浏览器 | Edge / Chrome（安装版使用 WebView2） |

## 二、安装与启动

```powershell
cd A_运行资产\openbb-cn
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 开发模式（终端日志 + 浏览器访问）
.\start.bat dev

# 桌面模式（pywebview 窗口）
.\start.bat desktop

# 只做自检（不弹窗口，检查图标/依赖/端口）
.\.venv\Scripts\python.exe desktop.py --check
```

访问 `http://127.0.0.1:6900`。安装包形态见 `B_交付归档\04_安装包\`。

## 三、功能范围（已交付）

| 域 | 视图 | 说明 |
|---|---|---|
| 行情 | `#/home`、`#/charting`、`#/stock` | 指数快照、K 线/分时、五档、报价、公司概况、股东、财务摘要与趋势、一致预期 |
| 板块 | `#/sectors`、`#/sector-val` | 热力图、行业/概念排行、资金流、板块 K 线、成分股、估值分位 |
| 新闻情绪 | `#/news` | 快讯/全球/个股新闻、板块命中、确定性情绪规则（±0.1 三档）、AI 复核（需自配 Key） |
| 基金 | `#/fund` | 档案、净值/回撤、阶段业绩、重仓、分红、盘中估值 |
| 持仓 | `#/portfolio` | 本地台账（成本盈亏）、四市场折算、汇率 |
| 自选股 | `#/watchlist` | W-01 概览 + W-02 行情表，四市场混合，本地持久化（`data/watchlist.json`） |
| 龙虎榜·北向 | `#/lhb` | L-01 北向柱状图、L-02 北向 Top10、L-03 龙虎榜明细 |
| 仪表板 | `#/dashboard` | 35 个组件元数据（`/api/widgets`）、拖拽布局、组件配方渲染 |
| 系统 | `#/connectors`、`#/settings`、`#/yjb` | 数据源能力探测、AI 配置（DPAPI 密文）、养基宝合规绑定 |

## 四、目录结构

```
openbb-cn/
├── backend/app/
│   ├── main.py            # FastAPI 入口、统一异常、静态托管、端口预检
│   ├── config.py          # 全局配置唯一来源（路径/端口/TTL/数据源/配色/AI）
│   ├── core/              # cache（两级缓存）、resilient（降级/熔断）、netfix、capabilities（通道探测）、secure_store（DPAPI）
│   ├── routers/           # 14 个业务路由（system/market/sector/stock/news/sentiment/fund/portfolio/connectors/ai/yjb/widgets/watchlist/moneyflow）
│   └── datasource/        # 14 个模块（13 个 provider + utils；腾讯/新浪/东财/申万/同花顺/蛋卷/雪球/养基宝/自选/资金面等）
├── frontend/              # 四区骨架（64/236/52/400）+ 13 路由视图 + 本地 ECharts
├── sdk/openbb_cn_sdk/     # 只读 Python 客户端（仅标准库，6 个方法）
├── tests/                 # 173 例（单元/契约/真实网络/隔离夹具）
├── tools/                 # 12 个可复用工具（一次性脚本已归档至 B_交付归档）
├── data/                  # 运行时数据（密钥/自选/持仓，均不入 Git）
└── start.bat              # dev/desktop/check 三模式
```

## 五、关键约定

- **统一信封**：所有接口返回 `{data, error, error_code}`；错误全中文，错误码只进日志。
- **数据三性**：`source / caliber / as_of / market_status / is_realtime`，双徽章常驻区块右上角。
- **红涨绿跌**：`#FF4B4B` / `#00B061`，前后端同源（`config.py` 唯一权威）。
- **48 列栅格**：`.g-3/.g-4/.g-5/.g-6/.g-7/.g-8/.g-12` → 12/16/20/24/28/32/48。
- **四态**：加载骨架 / 空态（含中文引导+刷新）/ 错误态（中文原因+重试）/ default，均落 `data-state`。
- **i18n**：文案统一 `t("key")`，字典 900+ 键；禁止渲染英文键名与 `undefined`。
- **自选与持仓分离**：自选=关注清单（`watchlist.json`），持仓=成本盈亏台账（`portfolio.json`）。

## 六、数据源与已知限制

| 项 | 现状 |
|---|---|
| A股报价/K线/分时 | 腾讯为主，新浪/东财备源 |
| 板块行情/资金流 | 同花顺为主（V8），新浪/申万备源；冻结形态标「已接入（V8 降级）」 |
| 快讯 | 财联社（签名接口）为主，东财/新浪全球备源 |
| 基金 | 蛋卷 + 雪球 + 东财分红页 |
| 港美股 | 腾讯（首候选）；东财 push2 本机被源站重置，仅生产候选 |
| 北向每日净买入 | **交易所 2024-08-16 起停止披露**：L-01 展示最后 20 个披露日、L-02 空态说明，不补零不造数 |
| 美股分时 | 免费通道不可用，接口返回 `available=false` + 中文说明 |

## 七、测试与验收

```powershell
.\.venv\Scripts\python.exe -m pytest tests\ -q          # 173 例
.\.venv\Scripts\python.exe tools\evidence_four_domains.py   # 四域交互取证（另见 tools/README.md）
```

- 全量 173 例：单元（数学/规则/契约）+ 真实网络（三性/对账）+ 前端静态一致性 + 新功能；
- `tests/conftest.py` 逐用例隔离进程级状态与缓存目录，保证「单用例/单文件/全量」三种方式均可重复；
- 验收材料归档于 `B_交付归档\03_测试报告\<日期>\`，接口说明见 `B_交付归档\02_接口说明书\`。

## 八、配置与密钥

- AI Key：`data/ai_config.json` 的 `api_key_enc`（Windows DPAPI 密文，`ENC1:` 前缀），读取时自动解密；DPAPI 不可用时拒绝保存。
- 养基宝 token：`data/yjb_token.json`（DPAPI 密文，`YJB1` 魔数）。
- 密钥类文件均已 gitignore；日志不出现密钥字符。

## 九、文档索引

| 文档 | 位置 |
|---|---|
| 项目状态与里程碑 | `项目状态文档.md`（项目根） |
| 接口说明书（9 份） | `B_交付归档\02_接口说明书\` |
| 测试与验收报告 | `B_交付归档\03_测试报告\<日期>\` |
| 目录例外登记 | `B_交付归档\08_项目文档\目录例外登记表.md` |
| 工作规范 v7.2 | `工作规范.md` |
