# syntax=docker/dockerfile:1
# =============================================================
# OpenBB 中文投研工作台 · 容器镜像
# 设计目标：功能零删减、镜像尽可能小、非 root 运行、内置健康检查
# 体积策略：
#   1. 多阶段构建——依赖装在 builder 阶段独立 venv，runtime 只拷最终产物，
#      pip 缓存 / 编译工具链不进最终镜像；
#   2. 基础镜像 python:3.13-slim（akshare/pandas/lxml/curl_cffi 均有
#      manylinux 预编译 wheel，无需 gcc，故不用 alpine）；
#   3. --no-compile 省 .pyc；--no-cache-dir 省 pip 缓存；
#   4. 依赖清单用 requirements-docker.txt：裁剪 Windows 桌面壳
#      （pythonnet/pywebview/pystray/pillow，Linux 不可用也不需要）与 pytest；
#   5. docs/tests/tools 不进镜像（GitHub 上看）。
# =============================================================

# ---------- 阶段 1：依赖构建 ----------
FROM python:3.13-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY requirements-docker.txt .

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --no-compile -r requirements-docker.txt

# ---------- 阶段 2：运行时 ----------
FROM python:3.13-slim

LABEL org.opencontainers.image.title="OpenBB 中文投研工作台" \
      org.opencontainers.image.description="受 OpenBB Workspace 启发的中文本地投研工作台（A股/港股/美股/基金）" \
      org.opencontainers.image.source="https://github.com/OWNER/openbb-cn" \
      org.opencontainers.image.licenses="MIT"

# 时区（时区契约：北京时间）；PYTHONUNBUFFERED 使探针/日志即时可见
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    OPENBB_PORT=6900

# tzdata 供 zoneinfo 使用；无 curl/wget——健康检查用 python 内置库，不装多余工具
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r app && useradd -r -g app -d /app app

# 依赖（builder 阶段产物，仅 venv 最终态）
COPY --from=builder /opt/venv /opt/venv

# 应用代码（.dockerignore 已排除 .git/data/logs/tests/tools 等全部无关物）
WORKDIR /app
COPY backend ./backend
COPY frontend ./frontend

# 运行时目录（compose 挂卷到这两个路径做持久化）+ 权限交给非 root 用户
RUN mkdir -p /app/data /app/logs \
    && chown -R app:app /app /opt/venv

USER app

EXPOSE 6900

# 内置健康检查：/api/health 返回 ok 即健康（带口令时 health 端点仍公开可探测）
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys;sys.exit(0 if b'ok' in urllib.request.urlopen('http://127.0.0.1:6900/api/health', timeout=8).read() else 1)"]

CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "6900", "--log-level", "warning"]
