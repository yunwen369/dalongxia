# Docker 部署手册（服务器一步式）

面向生产/家用服务器部署。镜像随 GitHub tag 自动构建发布到 `ghcr.io`（linux/amd64 + linux/arm64），服务器上无需 Python 环境，只需 Docker。

## 一、前置条件

| 项 | 要求 |
|---|---|
| 服务器 | 任意 Linux x86_64 / arm64（2C2G 起步足够），或 Windows/macOS 上的 Docker Desktop |
| Docker | 20.10+（compose v2 内置） |
| 端口 | 宿主 6900（可改，见下文） |
| 磁盘 | 镜像约 500MB，data/logs 随使用增长（通常 <1GB） |

```bash
# 服务器没装 Docker 时（Debian/Ubuntu）：
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker
```

## 二、一步式部署

```bash
# 1. 建部署目录
mkdir -p /opt/openbb-cn && cd /opt/openbb-cn

# 2. 下载 compose 文件（或手工复制仓库根的 docker-compose.yml）
curl -o docker-compose.yml https://raw.githubusercontent.com/yunwen369/openbb-cn/main/docker-compose.yml

# 3. 配置环境变量（口令必设；AI 可选）
cat > .env <<'EOF'
OPENBB_ACCESS_PASSWORD=换成你的强口令
# OPENAI_BASE_URL=https://api.openai.com/v1
# OPENAI_API_KEY=sk-xxx
EOF
chmod 600 .env

# 4. 建数据卷目录并启动
mkdir -p data logs
docker compose up -d

# 5. 验证
docker compose ps          # STATUS 应为 healthy
curl -s http://127.0.0.1:6900/api/health   # {"data":...,"error":null,...}
```

浏览器访问 `http://服务器IP:6900`，输入口令登录。

> 镜像路径 `ghcr.io/yunwen369/openbb-cn`（GitHub 用户名在镜像路径中全小写）。

## 三、配置详解

### 端口 / 访问

- 改宿主端口：compose 里 `"6900:6900"` 左侧改，如 `"8080:6900"`。
- 只想内网访问：`ports` 改 `"127.0.0.1:6900:6900"`，再由 Nginx/Caddy 反代出网。
- HTTPS 建议 Caddy 两行配置自动签证书：

```
你的域名.example.com {
    reverse_proxy 127.0.0.1:6900
}
```

### 环境变量

| 变量 | 必填 | 说明 |
|---|---|---|
| `OPENBB_ACCESS_PASSWORD` | 强烈建议 | ≥4 字符启用登录墙；空值=无鉴权（仅限完全内网） |
| `OPENAI_BASE_URL` | 可选 | OpenAI 兼容接口（财报 AI 摘要 / 新闻情绪复核 / AI 助手） |
| `OPENAI_API_KEY` | 可选 | 对应 Key；与 BASE_URL 须同时设置 |

### 数据持久化

| 宿主路径 | 容器路径 | 内容 |
|---|---|---|
| `./data` | `/app/data` | 缓存、自选股、持仓台账、AI 配置缓存 |
| `./logs` | `/app/logs` | 日志（按天滚动） |

升级/重建容器不丢数据；备份这两个目录即可完成整机备份。

## 四、AI 能力注入（容器特有）

Linux 容器内无 Windows DPAPI，界面「AI 设置」保存会被拒绝（设计行为，不写明文）。
因此容器形态下 AI 能力**必须用环境变量注入**（`.env` 或 compose `environment`）：

```bash
# 修改 .env 后重启生效
docker compose up -d --force-recreate
```

验证：界面打开任意财报详情，出现中文摘要卡即注入成功；未注入时卡片显示「未配置」。

## 五、升级流程

```bash
cd /opt/openbb-cn
docker compose pull        # 拉取新镜像
docker compose up -d       # 滚动重建容器（data/logs 卷保留）
docker image prune -f      # 清理旧镜像（可选）
```

锁定版本：把 compose 中镜像标签从 `latest` 改为 `3.6.0` 这样的精确版本再 pull。

## 六、运维速查

```bash
docker compose logs -f --tail 100     # 跟踪日志
docker compose restart                # 重启
docker compose down                   # 停止并移除容器（数据卷不动）
docker stats openbb-cn                # 资源占用
docker inspect --format='{{.State.Health.Status}}' openbb-cn   # 健康状态
```

常见问题：

| 现象 | 处置 |
|---|---|
| 首次打开某板块很慢 | 数据源冷启动拉取全量，属正常；预热后走本地缓存 |
| `401`/跳登录 | 口令未配或输错；检查 `.env` 后 `docker compose up -d --force-recreate` |
| 数据长时间空 | 查 `docker compose logs` 中数据源通道错误；本项目「空/错分离」，空=源无数据，错=通道异常 |
| 容器反复重启 | `docker compose logs` 看异常栈；多数为端口占用或卷权限 |

## 七、镜像说明（体积策略）

镜像约 480–580MB（akshare/pandas 科学栈是大头，为功能完整性不可再裁）：

1. **多阶段构建**：依赖装在 builder 独立 venv，运行时只拷最终态，pip 缓存/编译工具链不进镜像；
2. **python:3.13-slim** 基础（akshare/pandas/lxml/curl_cffi 均有 manylinux wheel，无需 gcc，故不用 alpine）；
3. `--no-compile --no-cache-dir` 省 .pyc 与缓存；
4. 依赖清单 `requirements-docker.txt` 裁剪 Windows 桌面壳（pywebview/pythonnet/pystray 等）与 pytest；
5. 非 root 用户运行、内置 python 健康检查（不装 curl）。
