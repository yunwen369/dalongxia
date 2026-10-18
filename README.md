# OpenBB 中文投研工作台

1:1 复刻 OpenBB Workspace 的中文本地投研桌面软件（A股 / 美股 / 港股 / 场外基金）。
技术栈：Python 3.13 + FastAPI + 原生 HTML/CSS/JS + ECharts（本地）+ pywebview + akshare。
当前阶段：**阶段 1 · 环境与工程初始化**（只提供健康检查与前端占位页，不含业务接口）。

## 一、环境要求

| 项 | 要求 |
|---|---|
| 操作系统 | Windows 10/11 64 位 |
| Python | 3.13（本项目实测 3.13.14） |
| 端口 | `127.0.0.1:6900`（固定，不静默更换） |
| 浏览器 | Edge / Chrome（安装版使用 WebView2） |

## 二、首次安装

在 `openbb-cn` 目录执行：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

依赖已用 `==` 精确锁定，完整快照见 `requirements-lock.txt`。

## 三、启动

双击 `start.bat`，或在命令行执行：

```powershell
.venv\Scripts\python.exe backend\app\main.py
```

启动成功后：

- 前端首页：<http://127.0.0.1:6900/>
- 健康检查：<http://127.0.0.1:6900/api/health>
- 接口文档：<http://127.0.0.1:6900/docs>

端口被占用时，入口会打印中文提示与占用进程 PID，并以退出码 2 结束，**不会自动换端口**。

## 四、目录说明

```
openbb-cn/
├── backend/app/
│   ├── main.py          # FastAPI 入口、异常处理、静态托管、端口预检
│   ├── config.py        # 全局配置唯一来源（路径/端口/TTL/数据源/配色/AI 配置）
│   ├── core/            # cache.py（两级缓存）、resilient.py（降级）、netfix.py（证书/代理）
│   ├── routers/         # 路由层，本阶段仅 system.py（健康检查/清缓存）
│   └── datasource/      # provider 层，本阶段仅包结构，不写业务取数
├── frontend/            # 四区骨架：64px 顶栏 / 236px 侧栏 / 52px 工具栏 / 400px Copilot
├── tests/               # pytest 冒烟测试
├── data/                # 运行时数据（密钥文件已 gitignore，见 data/README.md）
└── start.bat            # 一键启动（内部调用 backend/app/main.py）
```

## 五、配置与密钥

1. 复制 `.env.example` 为 `.env`，填写 `OPENAI_BASE_URL`、`OPENAI_API_KEY`、`OPENAI_MODEL`；
2. 优先级：进程环境变量 > `.env` > `data/ai_config.json`；
3. 密钥**不入库、不回显、不写日志**；界面只展示掩码；
4. `.env`、`data/ai_config.json`、`data/yjb_token.json` 均已被 `.gitignore` 排除。

## 六、测试

```powershell
.venv\Scripts\python.exe -m pytest
```

冒烟用例覆盖：`/api/health` 契约与数据非空、根路径返回首页、未知 API 返回统一 `{data,error}`。

## 七、本阶段边界

- 已实现：单端口同源、健康检查、统一异常契约、两级缓存、降级调度、网络自修复、密钥脱敏、四区占位页。
- 未实现：任何行情/财报/基金/新闻业务接口，pywebview 桌面窗口（阶段 6），PyInstaller 打包（发布阶段）。
- 前端不使用任何 mock 行情数据；占位区只显示 `—` 与“待接入”文案。
