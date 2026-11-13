# OpenBB 中文投研工作台

受 [OpenBB Workspace](https://openbb.co) 启发的**中文本地投研工作台**（A股 / 港股 / 美股 / 场外基金）。
纯 Python 后端 + 原生 HTML/CSS/JS 前端，ECharts 本地化（无 CDN），数据来自公开行情接口（akshare / 腾讯 / 新浪 / 东财 / 同花顺 / 蛋卷 / 雪球等）。

> 本项目与 OpenBB / OpenBB Workspace 无任何隶属或合作关系，仅受其「组件化投研终端」理念启发独立实现。

当前版本 **v3.6.0**：后端 116 个 `/api` 端点，前端 15 个路由视图，回归测试 579 例。

## 功能速览

| 域 | 能力 |
|---|---|
| 行情 | 指数快照、K 线/分时、五档、报价、公司概况、股东、财务摘要与趋势、一致预期 |
| 板块 | 热力图、行业/概念排行、资金流、板块 K 线、成分股、估值分位 |
| 新闻情绪 | 快讯/全球/个股新闻、板块命中、确定性情绪规则、AI 复核（需自配 Key） |
| 基金 | 档案、净值/回撤、阶段业绩、重仓、分红、盘中估值 |
| 持仓 | 本地台账（成本盈亏）、四市场折算、汇率 |
| 自选股 | 四市场混合关注清单，本地持久化 |
| 龙虎榜·北向 | 北向成交总额趋势、北向 Top10、龙虎榜明细 |
| 财报 AI 摘要 | A股/港股/美股三市场财报中文摘要（需自配 OpenAI 兼容 Key） |
| 仪表板 | 组件元数据（`/api/widgets`）、拖拽布局、组件配方渲染 |
| 系统 | 数据源能力探测、AI 配置、养基宝合规绑定 |

## Docker 快速开始（推荐）

镜像随 GitHub tag 自动构建发布到 GHCR（linux/amd64 + linux/arm64）：

```bash
mkdir -p data logs && echo "OPENBB_ACCESS_PASSWORD=你的访问口令" > .env
docker compose up -d
# 浏览器访问 http://localhost:6900
```

或不用 compose：

```bash
docker run -d --name openbb-cn -p 6900:6900 \
  -e OPENBB_ACCESS_PASSWORD=你的访问口令 \
  -v $(pwd)/data:/app/data -v $(pwd)/logs:/app/logs \
  --restart unless-stopped \
  ghcr.io/yunwen369/openbb-cn:latest
```

镜像标签：`latest`（最新发布版）/ `3.6`（大版本） / `3.6.0`（精确版本）。
服务器部署完整手册（含升级流程、AI 能力注入、反代与 HTTPS）见 [docs/docker-deploy.md](docs/docker-deploy.md)。

### 环境变量

| 变量 | 必填 | 说明 |
|---|---|---|
| `OPENBB_ACCESS_PASSWORD` | 建议 | 访问口令（≥4 字符启用登录墙；空值则无鉴权，仅限内网使用） |
| `OPENAI_BASE_URL` | 可选 | OpenAI 兼容接口地址（财报摘要 / 新闻情绪 AI 复核 / AI 助手） |
| `OPENAI_API_KEY` | 可选 | 对应 API Key。**Linux 容器无 Windows DPAPI，AI 配置必须走环境变量** |

数据持久化：挂卷 `./data:/app/data` 与 `./logs:/app/logs`，升级镜像不丢自选股、持仓台账与日志。

## 从源码运行（开发）

要求 Python 3.10+（实测 3.13）：

```bash
python -m pip install -r requirements-docker.txt
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 6900
# 访问 http://127.0.0.1:6900
```

Windows 桌面壳（pywebview 窗口）为可选增强：`pip install -r requirements.txt` 后运行 `python desktop.py`。

## 关键设计约定

- **统一信封**：所有接口返回 `{data, error, error_code}`；错误全中文，错误码只进日志。
- **数据三性**：`source / caliber / as_of / market_status / is_realtime`，双徽章常驻区块右上角。
- **红涨绿跌**：`#FF4B4B` / `#00B061`（A 股惯例），前后端同源（`backend/app/config.py` 唯一权威）。
- **空/错分离**：数据为空 = 成功 + 空列表 + 中文说明；通道异常才返回错误码。不补零、不造数。
- **四态**：加载骨架 / 空态（含中文引导）/ 错误态（中文原因 + 重试）/ 正常态。
- **i18n**：文案统一 `t("key")`，字典 900+ 键；禁止渲染英文键名与 `undefined`。

## 数据源与已知限制

| 项 | 现状 |
|---|---|
| A股报价/K线/分时 | 腾讯为主，新浪/东财备源 |
| 板块行情/资金流 | 同花顺为主，新浪/申万备源（限流与封禁自动退避切换） |
| 快讯 | 财联社为主，东财/新浪全球备源 |
| 基金 | 蛋卷 + 雪球 + 东财分红页 |
| 港美股 | 腾讯为首候选，东财备源 |
| 北向每日净买入 | 交易所 2024-08-16 起停止披露：展示成交总额口径，不补零不造数 |
| 美股分时 | 免费通道不可用，接口返回 `available=false` + 中文说明 |

## 测试

```bash
python -m pytest tests/ -q        # 579 例（单元/契约/前端静态一致性/隔离夹具）
```

## 密钥与安全

- Windows 桌面形态：AI Key 与养基宝 token 经 **DPAPI 加密**存储（`data/ai_config.json`、`data/yjb_token.json`，均已 gitignore）。
- Linux/容器形态：DPAPI 不可用时**拒绝写明文**（安全红线），AI 能力统一走环境变量注入。
- 日志不输出密钥字符；访问口令仅存 bcrypt 哈希。

## 免责声明

- 本项目仅为个人投研工具，**不构成任何投资建议**；数据来自第三方公开接口，可能延迟、缺失或有误，使用前请自行核实。
- 使用本项目产生的任何直接或间接损失，作者与贡献者不承担责任。
- 请遵守所在地区法律法规及数据源服务条款，合理控制请求频率。

## License

[MIT](LICENSE) © OpenBB-CN Contributors
