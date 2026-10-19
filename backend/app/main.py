"""FastAPI 应用入口。

职责（规范 v7.0 §5.4 / §6.1 / §6.2 / §6.3）：
1. 创建 FastAPI 应用，挂载 /api/* 路由；
2. 用 StaticFiles 把 frontend 挂到根路径，实现 6900 单端口同源托管；
3. 统一异常处理，保证 API 永不裸抛 500、永不把英文堆栈吐给前端；
4. 源码模式启动：端口预检 -> uvicorn 起服务 -> 延迟打开默认浏览器；
5. 在导入 akshare / curl_cffi 之前执行网络自修复 netfix。

本阶段只提供 /api/health 与 /api/cache/clear，不包含任何业务接口。
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import threading
import webbrowser
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List

# 以脚本方式运行时，把 openbb-cn 根目录加入 sys.path，保证 backend 包可导入
PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backend.app import config  # noqa: E402 - 必须先设置 sys.path

config.ensure_runtime_dirs()  # 先建 data/cache/logs 等运行目录

from backend.app.core import netfix  # noqa: E402

# 关键顺序：证书与代理修复必须早于 akshare / curl_cffi 的任何导入
netfix.apply_netfix()

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from starlette.exceptions import HTTPException as StarletteHTTPException  # noqa: E402

from backend.app.core.errors import ErrorCode, api_error  # noqa: E402
from backend.app.routers import (  # noqa: E402
    ai,
    connectors,
    fund,
    market,
    news,
    portfolio,
    sector,
    sentiment,
    stock,
    system,
    widgets,
    yjb,
)

logger = logging.getLogger("openbb_cn")


def _setup_logging() -> None:
    """初始化日志：控制台 + logs/app.log 按天滚动（规范 §6.10）。"""
    root_logger = logging.getLogger()
    if root_logger.handlers:  # 幂等，避免重复添加 handler
        return
    root_logger.setLevel(logging.INFO)
    formatter = logging.Formatter(config.LOG_FORMAT, datefmt=config.LOG_DATE_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root_logger.addHandler(console)

    try:
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            config.LOG_DIR / "app.log",
            when="midnight",
            backupCount=14,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    except OSError as exc:
        # 日志目录不可写时只退化为控制台，不阻断服务启动
        root_logger.warning("日志文件不可写，仅输出到控制台：%s", type(exc).__name__)


_setup_logging()


def _port_in_use(host: str, port: int) -> bool:
    """检测端口是否已有服务监听。

    Args:
        host: 监听地址。
        port: 端口号。

    Returns:
        bool: 已被占用返回 True。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def _find_port_owner(port: int) -> List[str]:
    """通过 netstat 查找监听指定端口的进程 PID（仅 Windows）。

    Args:
        port: 端口号。

    Returns:
        List[str]: PID 列表；无法获取时返回空列表。
    """
    pids: set[str] = set()
    try:
        completed = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        # netstat 在中文 Windows 下输出 GBK，忽略无法解码的字符
        text = completed.stdout.decode("gbk", errors="ignore")
    except (OSError, subprocess.SubprocessError):
        return []

    for line in text.splitlines():
        if f":{port}" not in line or "LISTENING" not in line.upper():
            continue
        parts = line.split()
        if parts and parts[-1].isdigit():
            pids.add(parts[-1])
    return sorted(pids)


def _port_error_message(port: int, pids: List[str]) -> str:
    """生成端口占用的中文错误提示（含下一步操作）。"""
    pid_text = "、".join(pids) if pids else "未知"
    hint = f"taskkill /PID {pids[0]} /F" if pids else f"netstat -ano | findstr :{port}"
    return (
        f"端口 {port} 已被占用（PID：{pid_text}），服务未启动。\n"
        f"请先结束占用进程后重试：{hint}\n"
        "本项目不会自动更换端口，以免前端、SDK 与安装包引用不一致。"
    )


async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """把 HTTP 异常转换为统一 {data,error} 结构，避免英文堆栈外泄。"""
    if exc.status_code == 404:
        message = "接口或页面不存在，请检查地址；接口清单请访问 /docs。"
    elif exc.status_code == 405:
        message = "请求方法不被允许，请确认使用 GET 或 POST。"
    else:
        message = f"请求未能完成（HTTP {exc.status_code}），请稍后重试。"
    logger.warning("HTTP 异常：%s %s -> %s", request.method, request.url.path, exc.status_code)
    code = ErrorCode.NOT_FOUND if exc.status_code == 404 else ErrorCode.INVALID_PARAM
    return JSONResponse(status_code=200, content=api_error(code, message))


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """把参数校验异常转换为中文提示。"""
    fields: List[str] = []
    for item in exc.errors():
        location = item.get("loc", [])
        if location:
            fields.append(str(location[-1]))
    field_text = "、".join(dict.fromkeys(fields)) if fields else "未知字段"
    logger.warning("参数校验失败：%s %s -> %s", request.method, request.url.path, field_text)
    return JSONResponse(
        status_code=200,
        content=api_error(ErrorCode.INVALID_PARAM, f"请求参数不合法，请检查：{field_text}。"),
    )


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """兜底异常处理：记录完整堆栈到日志，前端只看到中文提示。"""
    logger.exception("未捕获异常：%s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=200,
        content=api_error(ErrorCode.INTERNAL_ERROR),
    )


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用。

    Returns:
        FastAPI: 已挂载路由、异常处理器与静态前端的应用实例。
    """
    application = FastAPI(
        title=config.APP_NAME,
        version=config.APP_VERSION,
        description="本地投研工作台 API（阶段 1：环境与工程初始化）",
        docs_url="/docs",
        redoc_url=None,
    )

    application.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    application.add_exception_handler(RequestValidationError, _validation_exception_handler)
    application.add_exception_handler(Exception, _unhandled_exception_handler)

    # API 必须先于根路径静态挂载注册，保证 /api/* 不被 StaticFiles 截获
    application.include_router(system.router)
    application.include_router(market.router)
    # 阶段 5.1：板块 8 接口 + 个股 7 接口 + 组件元数据下发
    application.include_router(sector.router)
    application.include_router(stock.router)
    application.include_router(widgets.router)
    # 阶段 5.2：新闻 7 接口 + 情绪 3 接口
    application.include_router(news.router)
    application.include_router(sentiment.router)
    # 阶段 5.3：基金 9 接口 + 持仓 5 接口
    application.include_router(fund.router)
    application.include_router(portfolio.router)
    # 阶段 5.4：连接器能力矩阵；阶段 5.5：AI 配置与对话
    application.include_router(connectors.router)
    application.include_router(ai.router)
    # 阶段 7：养基宝连接器（合规告知书 / 扫码绑定 / 估值链 / 一键关闭）
    application.include_router(yjb.router)

    # 单端口同源托管：/ 返回 index.html，其余静态资源按路径查找
    application.mount(
        "/",
        StaticFiles(directory=str(config.FRONTEND_DIR), html=True),
        name="frontend",
    )
    return application


app = create_app()


def main() -> None:
    """源码模式启动入口：端口预检 -> 启动 uvicorn -> 延迟打开浏览器。"""
    import uvicorn

    logger.info("启动 %s %s（%s）", config.APP_NAME, config.APP_VERSION, config.BUILD_TAG)

    # 中文启动横幅由 Python 输出，避免 .bat 文件的中文编码问题
    print("=" * 60, flush=True)
    print(f"  {config.APP_NAME} · 开发模式", flush=True)
    print(f"  服务地址: {config.BASE_URL}", flush=True)
    print(f"  接口文档: {config.BASE_URL}/docs", flush=True)
    print("  停止服务: Ctrl+C", flush=True)
    print("=" * 60, flush=True)

    if _port_in_use(config.HOST, config.PORT):
        pids = _find_port_owner(config.PORT)
        message = _port_error_message(config.PORT, pids)
        logger.error("端口占用，启动中止：%s", message.replace("\n", " | "))
        print(message, file=sys.stderr)
        raise SystemExit(2)

    # 开发模式自动打开浏览器；自动化测试可用 OPENBB_NO_BROWSER=1 关闭
    if os.environ.get("OPENBB_NO_BROWSER") != "1":
        threading.Timer(2.5, lambda: webbrowser.open(f"{config.BASE_URL}/")).start()

    # 传入 app 对象而非导入字符串，避免 uvicorn 二次导入本模块
    uvicorn.run(
        app,
        host=config.HOST,
        port=config.PORT,
        log_level="info",
        log_config=None,
        reload=False,
    )


if __name__ == "__main__":
    main()
