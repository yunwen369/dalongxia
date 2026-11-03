"""网络环境自修复模块。

必须在导入 akshare / curl_cffi 之前调用 apply_netfix()（规范 v7.0 §6.5）。
解决三类 Windows 本地环境问题：
1. 项目路径含中文时，curl / OpenSSL 读取 certifi 证书失败；
2. 本机残留 HTTP_PROXY/HTTPS_PROXY 导致请求被劫持或超时；
3. 网络抖动时缺少重试与退避。

安全说明：本模块只处理证书与代理，不读取、不记录任何密钥。
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Iterator, Optional

import certifi
import requests

from backend.app import config

logger = logging.getLogger(__name__)

_APPLIED = False  # 幂等标记，避免重复复制证书与覆盖环境变量
_CERT_PATH: Optional[Path] = None  # 实际生效的 ASCII 证书路径


def _iter_candidate_dirs() -> Iterator[Path]:
    """按优先级产出可用的证书目录候选（全部要求纯 ASCII 路径）。

    Returns:
        Iterator[Path]: 计算结果。
    """
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        yield Path(local_app_data) / "OpenBB-CN" / "certs"
    # ProgramData 通常是纯 ASCII，作为第二候选
    yield Path("C:/ProgramData/OpenBB-CN/certs")
    # 系统临时目录作为最后兜底
    yield Path(tempfile.gettempdir()) / "OpenBB-CN" / "certs"


def _is_writable_dir(directory: Path) -> bool:
    """检测目录是否可创建且可写，避免选到只读路径。

    Args:
        directory: 目录路径。

    Returns:
        bool: 判定结果。
    """
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write_test"
        probe.write_text("ok", encoding="ascii")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _select_cert_dir() -> Optional[Path]:
    """选择第一个纯 ASCII 且可写的证书目录。

    Returns:
        Optional[Path]: 可用目录；全部不可用时返回 None。
    """
    for directory in _iter_candidate_dirs():
        # 中文用户名会出现在 LOCALAPPDATA 中，必须跳过非 ASCII 路径
        if not str(directory).isascii():
            logger.warning("跳过非 ASCII 证书目录：%s", directory)
            continue
        if _is_writable_dir(directory):
            return directory
    return None


def apply_netfix() -> Optional[Path]:
    """执行网络环境自修复（幂等）。

    动作：
    1. 把 certifi 的 cacert.pem 复制到纯 ASCII 目录；
    2. 注入 CURL_CA_BUNDLE / SSL_CERT_FILE / REQUESTS_CA_BUNDLE；
    3. 记录日志（仅路径，不含任何密钥）。

    Returns:
        Optional[Path]: 生效的证书路径；失败时返回 None，调用方可继续运行。
    """
    global _APPLIED, _CERT_PATH
    if _APPLIED:
        return _CERT_PATH

    _APPLIED = True
    source = Path(certifi.where())
    target_dir = _select_cert_dir()
    if target_dir is None:
        logger.error("网络自修复失败：未找到可写的 ASCII 证书目录，将沿用系统证书")
        return None

    target = target_dir / "cacert.pem"
    try:
        # 源证书缺失或大小变化时重新复制，保证与 certifi 版本同步
        if not target.is_file() or target.stat().st_size != source.stat().st_size:
            shutil.copyfile(source, target)
        _CERT_PATH = target
    except OSError as exc:
        logger.error("网络自修复失败：复制证书出错（%s）", type(exc).__name__)
        return None

    # 同时覆盖 curl、OpenSSL 与 requests 三条证书查找链
    os.environ["CURL_CA_BUNDLE"] = str(target)
    os.environ["SSL_CERT_FILE"] = str(target)
    os.environ["REQUESTS_CA_BUNDLE"] = str(target)
    logger.info("网络自修复完成，证书路径：%s", target)
    return target


def _make_base_session() -> "requests.Session":
    """创建基础会话（trust_env=False、浏览器 UA），不挂载重试适配器。

    内部函数：供 make_session / make_post_session 复用。

    Returns:
        requests.Session: 已关闭环境代理继承并设置浏览器 UA 的会话。
    """
    import requests

    session = requests.Session()
    session.trust_env = False  # 关键：绕过本机残留代理与 .netrc
    session.headers.update({"User-Agent": config.BROWSER_USER_AGENT})
    return session


def make_session() -> "requests.Session":
    """创建绕过系统代理、带重试的 requests 会话（GET/HEAD 可重试）。

    Returns:
        requests.Session: 已设置 trust_env=False、浏览器 UA 与重试适配器的会话。

    Notes:
        trust_env=False 会忽略 HTTP_PROXY/HTTPS_PROXY/NO_PROXY 等环境变量，
        等价于「代理异常时自动回退直连」，符合规范 §6.5。
        仅 GET/HEAD 纳入重试（幂等方法），POST 不自动重试——避免非幂等操作重复执行。
    """
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    session = _make_base_session()

    retry = Retry(
        total=config.MAX_RETRY,
        connect=config.MAX_RETRY,
        read=config.MAX_RETRY,
        backoff_factor=1.0,  # 退避间隔 1s、2s，与规范一致
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),  # POST 不重试：非幂等
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def make_post_session() -> "requests.Session":
    """创建 POST 专用会话：统一 trust_env=False/UA/超时，**不自动重试**（POST 非幂等）。

    Returns:
        requests.Session: 仅带基础配置（无重试适配器）的会话。

    Notes:
        1. POST 不自动重试：避免非幂等接口（下单、写入、AI 对话等）重复执行。
        2. 仍共享 trust_env=False、浏览器 UA、统一超时等网络环境配置。
        3. 调用方需自行处理超时/异常重试逻辑（如 AI 接口在路由层已有显式 try/except）。
    """
    session = _make_base_session()
    # 故意不挂载 Retry 适配器：POST 不重试（SC-P2-2 / 规范 §6.5）
    return session


def http_request(method: str, url: str, *, timeout: Optional[int] = None,
                 **kwargs) -> "requests.Response":
    """使用统一会话发起一次 HTTP 请求。

    Args:
        method: HTTP 方法，如 GET / POST。
        url: 完整 URL。
        timeout: 超时秒数，缺省使用 config.REQUEST_TIMEOUT。
        **kwargs: 透传给 requests 的参数。

    Returns:
        requests.Response: 响应对象。
    """
    # GET/HEAD 用带重试会话，POST 用不重试会话（非幂等安全）
    session = make_session() if method.upper() in ("GET", "HEAD") else make_post_session()
    try:
        return session.request(
            method,
            url,
            timeout=timeout or config.REQUEST_TIMEOUT,
            **kwargs,
        )
    finally:
        session.close()


