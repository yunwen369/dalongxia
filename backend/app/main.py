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
import time
import webbrowser
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import List

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
    alerts,
    connectors,
    dashboards,
    fund,
    macro,
    market,
    moneyflow,
    news,
    portfolio,
    screener,
    sector,
    sentiment,
    stock,
    system,
    valuation,
    watchlist,
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
    """生成端口占用的中文错误提示（含下一步操作）。

    Args:
        port: 端口号。
        pids: 进程号列表。

    Returns:
        str: 中文错误提示（含占用端口与 PID 列表）。
    """
    pid_text = "、".join(pids) if pids else "未知"
    hint = f"taskkill /PID {pids[0]} /F" if pids else f"netstat -ano | findstr :{port}"
    return (
        f"端口 {port} 已被占用（PID：{pid_text}），服务未启动。\n"
        f"请先结束占用进程后重试：{hint}\n"
        "本项目不会自动更换端口，以免前端、SDK 与安装包引用不一致。"
    )


async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """把 HTTP 异常转换为统一 {data,error} 结构，避免英文堆栈外泄。

    Args:
        request: HTTP 请求对象。
        exc: 捕获到的异常。

    Returns:
        JSONResponse: FastAPI 响应对象（统一错误体，中文文案且不泄露堆栈）。
    """
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
    """把参数校验异常转换为中文提示。

    Args:
        request: HTTP 请求对象。
        exc: 捕获到的异常。

    Returns:
        JSONResponse: FastAPI 响应对象（统一错误体，中文文案且不泄露堆栈）。
    """
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
    """兜底异常处理：记录完整堆栈到日志，前端只看到中文提示。

    Args:
        request: HTTP 请求对象。
        exc: 捕获到的异常。

    Returns:
        JSONResponse: FastAPI 响应对象（统一错误体，中文文案且不泄露堆栈）。
    """
    logger.exception("未捕获异常：%s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=200,
        content=api_error(ErrorCode.INTERNAL_ERROR),
    )


def _portfolio_snapshot_loop() -> None:
    """持仓快照调度（R13）：每 30 分钟检查「工作日 + 15:05 后 + 当日缺档」，满足才写。

    独立线程运行，异常只记日志，绝不影响主服务与请求链路。
    """
    from backend.app.datasource import portfolio_trend

    while True:
        try:
            result = portfolio_trend.ensure_today()
            if result.get("written"):
                logger.info("持仓快照已写入：%s", result.get("date"))
        except Exception as exc:  # noqa: BLE001 - 快照失败不影响主服务
            logger.info("持仓快照调度异常：%s", type(exc).__name__)
        time.sleep(1800)


def _fundflow_warm_loop() -> None:
    """板块四档预热（R5）：启动后台逐板块构建成分映射并聚合四档。

    同花顺成分页有 1.1s 限流且 V8 签名需串行，故单线程顺序执行；
    实测 89 个行业约 2–3 分钟完成，完成后当日刷新只走新浪批量接口（约 3 秒）。
    延迟 15 秒启动，避开首屏请求高峰与启动探针。
    """
    import time as _time

    from backend.app.datasource import sector as sector_provider

    _time.sleep(15)
    try:
        # 每批 15 个板块、批间冷却 120 秒：单次连续请求过多会被同花顺判为异常流量
        # （实测 40 个板块后 NonRetryableError 连片出现，并连累同进程的业务请求）。
        done = 0
        for _ in range(6):
            done += sector_provider.warm_fundflow_tiers(max_boards=15)
            _time.sleep(120)
        logger.info("板块四档预热完成：累计 %d 个板块已聚合", done)
    except Exception as exc:  # noqa: BLE001 - 预热失败不影响主服务
        logger.info("板块四档预热异常：%s", type(exc).__name__)


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
    application.include_router(watchlist.router)
    application.include_router(moneyflow.router)
    # R3-02：仪表板整表读写（分组/增删改/复制/排序，落盘 data/dashboards.json）
    application.include_router(dashboards.router)
    # 第七轮：宏观指标与周期象限（M1）+ 估值分位与 ERP（M2）
    application.include_router(macro.router)
    application.include_router(valuation.router)
    # 第七轮波次 5：多因子筛选器（M9，快照+手动刷新）+ 提醒与预警（M6，仅应用内）
    application.include_router(screener.router)
    application.include_router(alerts.router)

    # 第十轮 P2 · R13：持仓快照调度（后台线程，与打包形态无关）
    @application.on_event("startup")
    def _start_portfolio_snapshot() -> None:
        """启动持仓快照调度线程（幂等：重复触发只会多一个守护线程）。"""
        threading.Thread(
            target=_portfolio_snapshot_loop, daemon=True, name="portfolio-snapshot"
        ).start()

    # 第十轮 P3：板块四档预热（**默认关闭**，需显式 OPENBB_FUNDFLOW_WARM=1）
    # 原因（2026-11-06 实测）：预热会与业务请求争抢同花顺同一限流通道（1.1s + V8 串行），
    # 全量跑一遍约 40 个板块触发 NonRetryableError，并让同时在跑的 fundflow / 排行请求
    # 拿到「行业数量 49」的残缺表 —— 属于用后台任务换前台可用性，不可接受。
    # 现改为按需补齐：每次 fundflow 请求内 `_ensure_cons_progress` 按 6 秒预算补 1–2 个板块，
    # 用户实际访问的板块优先补齐；需要一次性预热时再用环境变量开启。
    @application.on_event("startup")
    def _start_fundflow_warm() -> None:
        """按开关启动板块四档预热线程（默认不启动，避免抢源站限流）。"""
        import os as _os

        if _os.environ.get("OPENBB_FUNDFLOW_WARM", "").strip() != "1":
            return
        threading.Thread(
            target=_fundflow_warm_loop, daemon=True, name="fundflow-warm"
        ).start()

    # 单端口同源托管：/ 返回 index.html，其余静态资源按路径查找
    application.mount(
        "/",
        StaticFiles(directory=str(config.FRONTEND_DIR), html=True),
        name="frontend",
    )
    return application


app = create_app()


def _run_frozen_check() -> int:
    """冻结形态自检（``OpenBB-CN.exe --check``，不建窗口、不起服务）。

    窗口化构建下 stdout 为 None，故先重定向 stdio，并把结果同时写入
    ``logs/selfcheck.log``——便于安装测试自动化取证（不弹阻塞式对话框）。

    Returns:
        int: 0=可启动；2=WebView2 缺失；3=端口被非本应用占用。
    """
    from backend.app.core import desktop_shell

    desktop_shell.redirect_stdio(config.LOG_DIR)
    lines: List[str] = ["== OpenBB 中文投研工作台 · 安装版自检 =="]
    lines.append(f"版本：{config.APP_VERSION}（{config.BUILD_TAG}）")
    lines.append(f"程序目录：{config.PROJECT_DIR}")
    lines.append(f"资源目录：{config.RESOURCE_DIR}")
    lines.append(f"前端目录：{config.FRONTEND_DIR}（存在={config.FRONTEND_DIR.exists()}）")
    lines.append(f"数据目录：{config.DATA_DIR}（可写={os.access(config.DATA_DIR, os.W_OK)}）")
    ok, detail = desktop_shell.check_webview2()
    lines.append(f"WebView2：{detail}")
    code = 0
    if not ok:
        code = 2
    elif _port_in_use(config.HOST, config.PORT):
        if desktop_shell.wait_for_server(f"{config.BASE_URL}/api/health", timeout=3):
            lines.append(f"端口 {config.PORT}：本应用已在运行，双击图标会复用该实例")
        else:
            lines.append(f"端口 {config.PORT}：被其他进程占用，启动将提示冲突")
            code = 3
    else:
        lines.append(f"端口 {config.PORT}：空闲，启动将自动拉起本地服务")
    summary = "\n".join(lines)
    print(summary, flush=True)
    try:
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        (config.LOG_DIR / "selfcheck.log").write_text(summary, encoding="utf-8")
    except OSError:
        pass
    return code


def _run_frozen() -> None:
    """冻结形态启动：重定向 stdio → 后台线程跑 uvicorn → 主线程开窗口。

    与源码模式的区别（§13.6-1）：窗口化构建没有控制台，必须先重定向
    stdout/stderr 到日志文件；服务与窗口在同一进程内（不另起解释器）。

    Raises:
        SystemExit: WebView2 缺失（2）或端口被非本应用占用（2/3）时退出。
    """
    import uvicorn

    from backend.app.core import desktop_shell

    desktop_shell.redirect_stdio(config.LOG_DIR)
    logger.info("冻结模式启动 %s %s（%s）", config.APP_NAME, config.APP_VERSION, config.BUILD_TAG)

    if _port_in_use(config.HOST, config.PORT):
        # 端口已被本应用占用（用户重复双击图标）：直接复用，不再起第二个服务
        if desktop_shell.wait_for_server(f"{config.BASE_URL}/api/health", timeout=3):
            if not desktop_shell.open_window(config.BASE_URL, config.APP_NAME,
                                             desktop_shell.resolve_icon(config.PROJECT_DIR)):
                raise SystemExit(2)
            return
        message = _port_error_message(config.PORT, _find_port_owner(config.PORT))
        logger.error("端口占用，启动中止：%s", message.replace("\n", " | "))
        desktop_shell.show_error(message)
        raise SystemExit(2)

    def _serve() -> None:
        """后台线程：单进程运行 uvicorn（log_config=None 避免读外部配置文件）。"""
        try:
            uvicorn.run(app, host=config.HOST, port=config.PORT,
                        log_level="warning", log_config=None)
        except Exception as exc:  # noqa: BLE001 - 线程内异常需落日志，否则窗口侧无声
            logger.exception("本地服务异常退出：%s", exc)

    threading.Thread(target=_serve, name="uvicorn", daemon=True).start()
    if not desktop_shell.wait_for_server(f"{config.BASE_URL}/api/health", timeout=60):
        desktop_shell.show_error(
            "本地服务启动超时（60 秒），窗口无法打开。\n\n"
            f"请查看日志：{config.LOG_DIR / 'app.log'}\n"
            "常见原因：端口 6900 被占用、防火墙拦截、安装目录无写权限。"
        )
        raise SystemExit(3)
    logger.info("本地服务就绪：%s", config.BASE_URL)
    if not desktop_shell.open_window(config.BASE_URL, config.APP_NAME,
                                     desktop_shell.resolve_icon(config.PROJECT_DIR)):
        raise SystemExit(2)
    logger.info("窗口已关闭，进程退出")


def _run_frozen_serve_only() -> None:
    """冻结形态「仅起服务」模式（``OpenBB-CN.exe --serve-only``，验收门禁专用）。

    与正常启动的唯一区别：**不创建窗口**，uvicorn 直接在主线程运行。
    用途：让发版门禁脚本在打包产物（而非源码 dev 服务）上实打实跑
    ``/api/fund/yjb/status|collect|holdings``，避免"交付了旧包/凭据没落位"
    这类问题再次发生。默认启动路径不受影响（不带该参数仍走窗口模式）。

    Raises:
        SystemExit: 端口被非本应用占用时以 2 退出。
    """
    import uvicorn

    from backend.app.core import desktop_shell

    desktop_shell.redirect_stdio(config.LOG_DIR)
    logger.info("冻结模式（仅服务）启动 %s %s（%s）端口 %s",
                config.APP_NAME, config.APP_VERSION, config.BUILD_TAG, config.PORT)
    if _port_in_use(config.HOST, config.PORT):
        message = _port_error_message(config.PORT, _find_port_owner(config.PORT))
        logger.error("端口占用，仅服务模式中止：%s", message.replace("\n", " | "))
        raise SystemExit(2)
    try:
        uvicorn.run(app, host=config.HOST, port=config.PORT,
                    log_level="warning", log_config=None)
    except KeyboardInterrupt:  # pragma: no cover - 门禁脚本用 terminate 结束进程
        logger.info("仅服务模式收到中断信号，退出")
    finally:
        logger.info("仅服务模式已退出")


def _bg_prewarm() -> None:
    """后台预热（守护线程，失败不影响启动）。

    覆盖两项冷启动较慢的数据，使首次点击即命中缓存：
    1. **拥挤度排行**（第二轮检测 P1-1：冷启动 44.4s 超 15s 预期）——
       第八轮已改纯 HTTP，90 个行业约 20 秒；
    2. **板块四档资金流**（降级点 G-1）——需逐板块构建成分映射（同花顺 1.1s 限流），
       首次约数分钟；映射落盘后 7 天内不再重复翻页，之后每 10 分钟只刷新四档数值。

    预热顺序：先排行（快、用户最先看到），再四档（慢、有磁盘缓存）。
    """
    import time

    from backend.app.core import cache as cache_module
    from backend.app.datasource import sector as sector_provider

    time.sleep(6)  # 等服务就绪，避免与启动探测抢网络
    try:
        result = sector_provider.get_crowding_rankings(kind="industry", limit=50)
        cache_module.set("sector:crowding:rankings:industry:50", result, 3600)
        logger.info("拥挤度排行（industry）后台预热完成：扫描 %d/%d，缓存已写",
                    result.get("scanned"), result.get("total"))
    except Exception as exc:  # noqa: BLE001 - 预热失败不阻断启动
        logger.info("拥挤度排行后台预热失败（不影响启动）：%s", type(exc).__name__)
    # 3. 全市场快照（M13 候选池依赖）：缺失时自动建库，用户无需先去筛选器页点按钮
    #    （第二轮检测 P3-1：首次使用需手动建快照）
    try:
        from backend.app.datasource import screener as screener_provider

        if not screener_provider.snapshot_ready():
            logger.info("全市场快照缺失，后台自动构建（M13 候选池首次可用）")
            screener_provider.start_build_async(reason="startup")
    except Exception as exc:  # noqa: BLE001 - 预热失败不阻断启动
        logger.info("全市场快照自动构建失败（不影响启动）：%s", type(exc).__name__)
    # 4. 四档资金流：需 90 个板块的成分映射（同花顺 1.1s 限流），首次约 5–8 分钟；
    #    之后每 10 分钟只需刷新四档数值（新浪批量，约 3 秒）。循环补建，
    #    保证「首次未补完的板块」下一轮自动补齐（不依赖用户点击）。
    for round_index in range(12):
        try:
            done = sector_provider.warm_fundflow_tiers()
            logger.info("板块四档资金流预热第 %d 轮完成：%d 个板块", round_index + 1, done)
        except Exception as exc:  # noqa: BLE001 - 预热失败不阻断启动
            logger.info("板块四档资金流预热失败（不影响启动）：%s", type(exc).__name__)
        time.sleep(600)


def main() -> None:
    """启动入口：冻结形态走内嵌窗口；源码模式走端口预检 + uvicorn + 浏览器。

    Raises:
        SystemExit: 启动前置检查失败需要终止进程时抛出。
    """
    if config.IS_FROZEN:
        if "--check" in sys.argv:
            raise SystemExit(_run_frozen_check())
        if "--serve-only" in sys.argv:
            # 发版门禁专用：无窗口跑打包产物，供自动化实测接口
            raise SystemExit(_run_frozen_serve_only())
        _run_frozen()
        return

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

    # ── 后台预热拥挤度排行缓存（缓解 P1-1：首次 44s+ 冷启动）─────────
    # 排行首次计算需扫描 90 个行业（同花顺 V8 串行），超过规范 15s 单接口预期。
    # 故在服务就绪后由后台守护线程预热，用户首次请求直接命中缓存（<0.5s）。
    # 仅预热默认 industry 口径（最常用）；concept 首次请求时仍需一次完整扫描并缓存。
    threading.Thread(target=_bg_prewarm, daemon=True, name="prewarm").start()

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
