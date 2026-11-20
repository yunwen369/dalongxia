"""AI 域路由：/api/ai 下 3 个接口（批次 5.5）。

对应规范 v7.2 §5.6 / §12（AI 配置与密钥管理）与 D2 决策：
- GET  /config  读取配置（密钥只返回掩码，绝不回显原文）；
- POST /config  保存配置（写入 data/ai_config.json，已 gitignore，权限最小化）；
- POST /test    测试连通性（R3-13：真实最小请求探测，120s 超时，失败分因给中文文案）；
- POST /chat    对话（180s 超时；未配置时返回明确引导，不静默降级、不返回假回答）。

安全要点：
1. 密钥只在内存出现；落盘前必须用 Windows DPAPI 加密（``api_key_enc`` 字段），
   DPAPI 不可用时拒绝保存，绝不写明文；日志只记「已加密存储」，不出现任何密钥字符；
2. 保存时校验 base_url 以 http(s):// 开头，防止误填；
3. 对话失败区分「未配置 / 鉴权失败 / 超时 / 上游异常」，文案均含下一步动作。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Tuple

import requests
from fastapi import APIRouter, Body

from backend.app import config
from backend.app.core import atomic, netfix
from backend.app.core.errors import ErrorCode, api_error, bad_request, ok
from backend.app.datasource import utils

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["AI"])

# 与 /api/news/financial-detail 同一白名单（doc_url 安全约束，防 SSRF）。
_DOC_URL_ALLOWED_PREFIXES: Tuple[str, ...] = (
    "https://np-anotice-stock.eastmoney.com/",
    "https://data.eastmoney.com/notices/detail/",
    "https://static.cninfo.com.cn/",
    "https://www1.hkexnews.hk/",
    "https://www.sec.gov/Archives/",
)


def _now() -> str:
    """当前时间字符串（北京时间口径）。

    Returns:
        str: `%Y-%m-%d %H:%M:%S` 标准格式。

    Notes:
        R3-22：委托 datasource.utils.now_text，格式串唯一来源。
    """
    return utils.now_text()


@router.get("/config")
def get_config() -> Dict[str, Any]:
    """读取 AI 配置（密钥掩码）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    public = config.get_public_ai_config()
    return ok({
        **public,
        "source": "环境变量 / .env / data/ai_config.json（优先级从高到低）",
        "caliber": "密钥仅返回掩码；修改后立即生效，无需重启",
        "as_of": _now(),
        "market_status": "配置快照",
        "is_realtime": False,
    })


@router.post("/config")
def save_config(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """保存 AI 配置。

    Body:
        {base_url, api_key?, model?, reasoning_effort?, context_kb?, max_tokens?}；
        api_key 留空表示保留原值；后三项为可选扩展配置：
        reasoning_effort 接受中文标签（弱/中/高/强/极致）或上游取值
        （low/medium/high/xhigh/max）；context_kb 上下文输入 KB 字符预算 0–512
        （留空/0=不裁剪，按上游上下文默认）；max_tokens 输出预算 0–8192
        （留空/0=不传该参数，按上游输出默认）。

    Args:
        payload: 请求体（JSON 对象）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    base_url = str(payload.get("base_url") or "").strip()
    api_key = str(payload.get("api_key") or "").strip()
    model = str(payload.get("model") or "").strip()

    if not base_url:
        return api_error(ErrorCode.INVALID_PARAM, "base_url 不可为空，请填写 OpenAI 兼容接口地址。")
    if not base_url.startswith(("http://", "https://")):
        return api_error(ErrorCode.INVALID_PARAM, "base_url 必须以 http:// 或 https:// 开头。")

    existing: Dict[str, Any] = {}
    if config.AI_CONFIG_FILE.exists():
        try:
            with open(config.AI_CONFIG_FILE, "r", encoding="utf-8") as handle:
                existing = json.load(handle)
        except Exception:  # noqa: BLE001 - 损坏文件按空处理
            existing = {}

    # 既有密钥（P1-15）：优先解密密文，兼容历史明文（读取时已由 config 迁移）
    from backend.app.core import secure_store  # 延迟导入，避免启动期循环依赖

    existing_key = ""
    if existing.get("api_key_enc"):
        existing_key = secure_store.decrypt_text(str(existing["api_key_enc"])) or ""
    else:
        existing_key = str(existing.get("api_key") or "")

    new_key = api_key or existing_key
    if not new_key:
        return api_error(ErrorCode.INVALID_PARAM, "首次保存必须填写 api_key。")

    # 2026-11-18/19：思考强度/上下文/输出配置的解析与校验
    from backend.app.config import (
        _parse_int_clamped,
        _parse_reasoning_effort,
    )

    effort_raw = str(payload.get("reasoning_effort") or "").strip()
    reasoning_effort = _parse_reasoning_effort(effort_raw)
    if effort_raw and not reasoning_effort:
        return api_error(
            ErrorCode.INVALID_PARAM,
            "思考强度仅支持：弱 / 中 / 高 / 强 / 极致（或 low/medium/high/xhigh/max）。",
        )
    # 留空表示沿用已有值；已有值缺失时为空串（不传该参数）
    if not effort_raw:
        reasoning_effort = _parse_reasoning_effort(existing.get("reasoning_effort"))

    # 2026-11-19：上下文输入改为 KB 字符预算（0–512）、输出预算（0–8192）；
    # 字段随表单提交时：留空/0 = 不裁剪/不传，按上游上下文与输出默认处理；
    # 请求未携带该字段时保留原文件值
    if "context_kb" in payload:
        context_kb = _parse_int_clamped(payload.get("context_kb"), 0, 512, 0)
    else:
        context_kb = _parse_int_clamped(existing.get("context_kb"), 0, 512, 0)
    if "max_tokens" in payload:
        max_tokens = _parse_int_clamped(payload.get("max_tokens"), 0, 8192, 0)
    else:
        max_tokens = _parse_int_clamped(existing.get("max_tokens"), 0, 8192, 0)

    # 落盘前加密：DPAPI 不可用时拒绝写明文（规范 §5.6 / §12.3）
    try:
        encrypted_key = secure_store.encrypt_text(new_key)
    except OSError as exc:
        return api_error(
            ErrorCode.NOT_CONFIGURED,
            f"当前系统不支持安全存储（DPAPI 不可用），已拒绝明文保存：{exc}",
        )

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload_to_save = {
        "base_url": base_url,
        "api_key_enc": encrypted_key,
        "model": model or existing.get("model") or "",
        # 2026-11-18/19：扩展配置一并落盘，读取时应用；
        # 上下文输入为 KB 字符预算，输出 0=不传用上游默认
        "reasoning_effort": reasoning_effort,
        "context_kb": context_kb,
        "max_tokens": max_tokens,
        "updated_at": _now(),
    }
    # R3-09：统一原子写助手（含 fsync + 0o600 权限），替代手动 temp + os.replace + os.chmod
    atomic.atomic_write_json(config.AI_CONFIG_FILE, payload_to_save, ensure_ascii=False, indent=2, mode=0o600)

    # 日志只记录「已加密存储」，不出现任何密钥字符
    logger.info("AI 配置已更新（model=%s，key=DPAPI 密文已落盘）", payload_to_save["model"])
    public = config.get_public_ai_config()
    return ok({
        **public,
        "saved": True,
        "source": "data/ai_config.json",
        "caliber": "密钥不回显；如需更换请在 api_key 字段填新值",
        "as_of": _now(),
        "market_status": "配置快照",
        "is_realtime": False,
    })


@router.post("/test")
def test_connection(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """测试 AI 接口连通性（不发完整对话，只验证鉴权与模型可用）。

    Body:
        {base_url, api_key?, model?}；api_key 留空时用已保存的密钥。

    Args:
        payload: 请求体（JSON 对象）。

    Returns:
        dict: 统一信封；data 含 ok / latency_ms / detail。

    Notes:
        真实发送一次最小请求（max_tokens=512，兼容推理模型：token 会先消耗在
        reasoning 上，max_tokens=1/64 时正文可能为空——实测 reasoning_tokens=66
        曾超过 64 预算导致 content 恒空、测试结果不稳定，故留足思考+输出预算），
        超时 120 秒（推理模型首字延迟可达数十秒，与摘要链路 LLM_TIMEOUT=120 对齐）；
        失败时区分未配置 / 鉴权失败 / 连接超时 / 读超时 / 上游异常，文案均中文。
        响应绝不回显密钥，只在 detail 里给状态说明。
    """
    base_url = str(payload.get("base_url") or "").strip()
    api_key = str(payload.get("api_key") or "").strip()
    model = str(payload.get("model") or "").strip()

    saved = config.load_ai_config()
    if not base_url:
        base_url = saved.base_url
    if not api_key:
        api_key = saved.api_key
    if not model:
        model = saved.model

    if not base_url:
        return api_error(ErrorCode.INVALID_PARAM, "base_url 不可为空：请填写以 http(s):// 开头的接口地址。")
    if not base_url.startswith(("http://", "https://")):
        return api_error(ErrorCode.INVALID_PARAM, "base_url 必须以 http:// 或 https:// 开头。")
    if not api_key:
        return api_error(
            ErrorCode.NOT_CONFIGURED,
            "未检测到 api_key：请先填写密钥再测试（保存后密钥不回显，无需重复填写）。",
            data={"ok": False, "configured": False},
        )
    if not model:
        return api_error(ErrorCode.INVALID_PARAM, "model 不可为空：请填写模型名，如 deepseek-chat。")

    url = base_url.rstrip("/") + "/chat/completions"
    started = time.time()
    try:
        response = netfix.http_request(
            "POST",
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 512,
                "temperature": 0,
            },
            timeout=120,
        )
    except requests.exceptions.ConnectTimeout:
        # 连接层超时：通常远早于 120 秒即抛出，属网络/上游拒接，勿报「超时（120 秒）」误导
        return api_error(ErrorCode.UPSTREAM_TIMEOUT,
                         "连接 AI 接口超时：网络链路或上游服务响应慢，请稍后重试。",
                         data={"ok": False, "latency_ms": int((time.time() - started) * 1000)})
    except requests.exceptions.Timeout:
        return api_error(ErrorCode.UPSTREAM_TIMEOUT,
                         "测试超时（读响应超过 120 秒）：该模型响应较慢，请稍后重试或更换更轻量的模型。",
                         data={"ok": False, "latency_ms": int((time.time() - started) * 1000)})
    except requests.exceptions.RequestException as exc:
        # 真实网络类异常（连接拒绝/DNS 失败/TLS 等）：中文分因，保留 latency_ms
        logger.warning("AI 测试连接失败（网络类 %s）：%s", type(exc).__name__, exc)
        return api_error(ErrorCode.UPSTREAM_ERROR,
                         "无法连接 AI 接口：请检查网络连通性与 base_url 是否正确。",
                         data={"ok": False, "latency_ms": int((time.time() - started) * 1000)})
    except Exception as exc:  # noqa: BLE001 - 未预期内部异常：记录堆栈，报 INTERNAL_ERROR
        # 第五轮 P1：代码缺陷（如未导入的 netfix 触发 NameError）绝不能被伪装成"网络问题"，
        # 否则用户排查方向被误导、测试也会误判为绿。此处记录完整堆栈并返回内部错误码。
        logger.exception("AI 测试连接出现内部异常（非网络类）：%s", type(exc).__name__)
        return api_error(ErrorCode.INTERNAL_ERROR,
                         "服务内部异常，请稍后重试；如持续出现请查看 logs/app.log。",
                         data={"ok": False, "latency_ms": int((time.time() - started) * 1000)})

    latency_ms = int((time.time() - started) * 1000)
    if response.status_code in (401, 403):
        return api_error(ErrorCode.UNAUTHORIZED,
                         "鉴权失败（401/403）：api_key 无效或已过期，请更换后重试。",
                         data={"ok": False, "latency_ms": latency_ms})
    if response.status_code == 404:
        return api_error(ErrorCode.UPSTREAM_ERROR,
                         f"接口返回 404：base_url 路径或模型名「{model}」不正确。",
                         data={"ok": False, "latency_ms": latency_ms})
    if response.status_code >= 400:
        logger.warning("AI 测试返回 %s", response.status_code)
        return api_error(ErrorCode.UPSTREAM_ERROR,
                         f"接口返回异常状态（{response.status_code}）：请检查模型名与服务商可用性。",
                         data={"ok": False, "latency_ms": latency_ms})

    try:
        body = response.json()
        msg = body["choices"][0]["message"]
        content = msg.get("content")
        reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
    except Exception:  # noqa: BLE001 - 非 OpenAI 结构
        return api_error(ErrorCode.UPSTREAM_ERROR,
                         "接口可达但返回格式无法解析：请确认服务商兼容 OpenAI Chat Completions。",
                         data={"ok": False, "latency_ms": latency_ms})

    # 推理模型：max_tokens 可能全部消耗在思考（reasoning_content）上，正文为空属正常
    if not content and not reasoning:
        return api_error(ErrorCode.UPSTREAM_ERROR,
                         "接口返回空内容：模型可能暂不可用，请更换模型名重试。",
                         data={"ok": False, "latency_ms": latency_ms})

    logger.info("AI 测试连接成功（model=%s，latency=%dms）", model, latency_ms)
    return ok({
        "ok": True,
        "latency_ms": latency_ms,
        "model": model,
        "detail": f"连接成功，模型 {model} 可用",
        "source": base_url,
        "caliber": "真实最小请求（max_tokens=512，兼容推理模型思考消耗）探测，不产生计费对话记录之外的额外用量",
        "as_of": _now(),
        "market_status": "连通性测试",
        "is_realtime": True,
    })


@router.post("/chat")
def chat(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """对话（OpenAI 兼容 /chat/completions，180s 超时）。

    Body:
        {message} 或 {messages:[{role,content}, ...]}；
        可选 `page` + `page_params`（R16 页面上下文：Copilot 上报当前视图名
        与 hash 参数，后端据此注入页面真实数值到 system prompt）。

    Args:
        payload: 请求体（JSON 对象）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime/context_used。
    """
    ai = config.load_ai_config()
    raw_messages = payload.get("messages")
    messages: List[Dict[str, str]] = []
    if isinstance(raw_messages, list) and raw_messages:
        # 2026-11-19：上下文输入改为 KB 字符预算（context_kb）：
        # 从最新消息向前累积，超出预算即截断；0/留空=不裁剪（按上游上下文默认）
        _CHARS_PER_KB = 1024
        budget = (ai.context_kb or 0) * _CHARS_PER_KB
        picked: List[Dict[str, str]] = []
        total_chars = 0
        for item in reversed(raw_messages):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "user")
            content = str(item.get("content") or "").strip()
            if not content or role not in ("user", "assistant", "system"):
                continue
            cost = len(content) + 16  # 每条消息的 JSON 结构开销近似
            if picked and budget and total_chars + cost > budget:
                break
            picked.append({"role": role, "content": content})
            total_chars += cost
        messages = list(reversed(picked))
    else:
        message = str(payload.get("message") or "").strip()
        if message:
            messages.append({"role": "user", "content": message})

    if not messages:
        return api_error(ErrorCode.INVALID_PARAM, "请提供 message 或 messages，不能为空。")

    if not ai.api_key or not ai.base_url:
        return api_error(
            ErrorCode.NOT_CONFIGURED,
            "AI 助手尚未配置：请到「设置 → AI 设置」填写 base_url、api_key、model 后再试。",
            data={"guidance": "打开设置页填写三项配置后即可对话", "configured": False},
        )

    # R16 页面上下文：构造成功才注入（失败=None=与 R16 之前行为完全一致，零降级）；
    # 放在配置检查之后——未配置直接拒绝，不应白白拉一遍页面数据源。
    context_used = False
    try:
        from backend.app.datasource import page_context

        summary = page_context.build_summary(
            str(payload.get("page") or ""), payload.get("page_params") or {}
        )
    except Exception:  # noqa: BLE001 - 上下文注入绝不影响对话主链路
        summary = None
    if summary:
        messages = [{"role": "system", "content": summary}] + messages
        context_used = True

    url = ai.base_url.rstrip("/") + "/chat/completions"
    started = time.time()
    # 2026-11-18/19：应用思考强度（仅当配置了合法档位时传参）；
    # 输出配置 0/留空表示不传 max_tokens，按上游模型默认输出
    request_body: Dict[str, Any] = {
        "model": ai.model or "gpt-4o-mini",
        "messages": messages,
        "temperature": 0.3,
    }
    if ai.max_tokens > 0:
        request_body["max_tokens"] = ai.max_tokens
    if ai.reasoning_effort:
        request_body["reasoning_effort"] = ai.reasoning_effort
    try:
        response = netfix.http_request(
            "POST",
            url,
            headers={"Authorization": f"Bearer {ai.api_key}", "Content-Type": "application/json"},
            json=request_body,
            timeout=180,
        )
    except requests.exceptions.ConnectTimeout:
        # 连接层超时：通常远早于 180 秒即抛出，属网络/上游拒接，勿报「超时（180 秒）」误导
        return api_error(ErrorCode.UPSTREAM_TIMEOUT,
                         "连接 AI 接口超时：网络链路或上游服务响应慢，请稍后重试。", data={"configured": True})
    except requests.exceptions.Timeout:
        return api_error(ErrorCode.UPSTREAM_TIMEOUT,
                         "AI 响应超时（读响应超过 180 秒）：该模型响应较慢，请稍后重试或缩短问题。", data={"configured": True})
    except requests.exceptions.RequestException as exc:
        logger.warning("AI 对话连接失败（网络类 %s）：%s", type(exc).__name__, exc)
        return api_error(ErrorCode.UPSTREAM_ERROR,
                         "无法连接 AI 接口，请检查网络与 base_url 是否正确。", data={"configured": True})
    except Exception as exc:  # noqa: BLE001 - 未预期内部异常：记录堆栈，报 INTERNAL_ERROR
        # 第五轮 P1：同 /test，内部缺陷不得伪装成网络问题
        logger.exception("AI 对话出现内部异常（非网络类）：%s", type(exc).__name__)
        return api_error(ErrorCode.INTERNAL_ERROR,
                         "服务内部异常，请稍后重试；如持续出现请查看 logs/app.log。",
                         data={"configured": True})

    if response.status_code in (401, 403):
        return api_error(ErrorCode.UNAUTHORIZED, "AI 接口鉴权失败，请到设置页更新 api_key。",
                         data={"configured": True})
    if response.status_code >= 400:
        logger.warning("AI 对话返回 %s", response.status_code)
        return api_error(ErrorCode.UPSTREAM_ERROR,
                         f"AI 接口返回异常状态（{response.status_code}），请检查模型名是否正确。",
                         data={"configured": True})

    try:
        body = response.json()
        content = body["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001 - 非 OpenAI 结构
        return api_error(ErrorCode.UPSTREAM_ERROR,
                         "AI 返回格式无法解析，请确认接口兼容 OpenAI Chat Completions。",
                         data={"configured": True})

    return ok({
        "reply": content,
        "model": ai.model,
        "elapsed_ms": int((time.time() - started) * 1000),
        "messages": messages,
        "context_used": context_used,
        "source": "用户配置 AI",
        "caliber": "AI 回复仅供研究参考，不作为数据源；数据仍以行情/财务接口为准"
                   + ("；本轮已注入当前页面真实数值上下文（R16）" if context_used else ""),
        "as_of": _now(),
        "market_status": "对话",
        "is_realtime": False,
    })


@router.post("/translate")
def ai_translate(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """文本翻译（港美股英文财报/公告 → 中文，G-4 配套）。

    Args:
        payload: {text: 待翻译文本, target?: "zh"}。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。

    Notes:
        未配置 AI 时返回 NOT_CONFIGURED（不假装翻译）；原文超 4000 字符会截断并标注。
    """
    from backend.app.core.errors import ErrorCode as _ErrorCode
    from backend.app.datasource import translate as translate_provider

    text = str(payload.get("text") or "")
    target = str(payload.get("target") or "zh")
    if not text.strip():
        return api_error(_ErrorCode.INVALID_PARAM, "text 不可为空")
    if not translate_provider.is_configured():
        return api_error(
            _ErrorCode.NOT_CONFIGURED,
            "未配置 AI 模型，无法翻译。请到「设置 → AI 设置」填写 base_url / api_key / model 后重试。",
            data={"configured": False},
        )
    try:
        result = translate_provider.translate_text(text, target)
    except ValueError as exc:
        return api_error(_ErrorCode.INVALID_PARAM, str(exc))
    except Exception as exc:  # noqa: BLE001 - 上游异常转中文错误
        logger.info("翻译失败：%s", type(exc).__name__)
        return api_error(_ErrorCode.UPSTREAM_ERROR,
                         f"翻译服务调用失败（{type(exc).__name__}），请稍后重试。")
    return ok({
        **result,
        "source": "用户配置 AI（OpenAI 兼容）",
        "as_of": _now(),
        "market_status": "机翻快照",
        "is_realtime": False,
    })


@router.post("/summary")
def ai_summary(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """财报 / 公告中文摘要（R14 · N3 契约）。

    Args:
        payload: 请求体，``{market, code, title, pub_time?, doc_url?, force?}``；
            market 取 a/hk/us，title 与 code 至少一项，doc_url 需在白名单内。

    Body:
        {market, code, title, pub_time?, doc_url?, force?}
        market: a / hk / us；title 与 code 至少一项；doc_url 需在白名单内。

    Returns:
        dict: 统一信封；data 含 summary（key_table/points/risks 三段式）、
        source_text_chars、truncated、cached、model、caliber、as_of。
        正文不可得时返回成功信封 + status=no_content + summary=None（空/错分离）。

    Notes:
        未配置 AI → NOT_CONFIGURED（界面按钮禁用+引导，不静默降级）；
        doc_url 仅接受巨潮/东财/HKEX/SEC 已知域名前缀，防 SSRF；
        摘要失败=UPSTREAM_ERROR（不得以原文冒充摘要）。
    """
    from backend.app.core.errors import NonRetryableError
    from backend.app.datasource import ai_summary as summary_provider

    market = str(payload.get("market") or "a").strip().lower()
    if market not in ("a", "hk", "us"):
        return bad_request("market 仅支持 a / hk / us")
    code = str(payload.get("code") or "").strip()
    title = str(payload.get("title") or "").strip()
    if not code and not title:
        return bad_request("缺少公告标识：code 与 title 至少一项")
    pub_time = str(payload.get("pub_time") or "").strip()
    doc_url = str(payload.get("doc_url") or "").strip()
    force = bool(payload.get("force"))
    if doc_url and not any(doc_url.startswith(p) for p in _DOC_URL_ALLOWED_PREFIXES):
        return bad_request(
            "doc_url 来源不在支持的公告源（巨潮资讯/东方财富/HKEX/SEC）内，已拒绝处理。"
        )

    if not summary_provider.is_configured():
        return api_error(
            ErrorCode.NOT_CONFIGURED,
            "未配置 AI 模型，无法生成中文摘要。请到「设置 → AI 设置」填写 base_url / api_key / model 后重试。",
            data={"configured": False},
        )

    try:
        result = summary_provider.summarize(
            market=market, code=code, title=title,
            pub_time=pub_time, doc_url=doc_url, force=force,
        )
    except NonRetryableError as exc:
        msg = str(exc)
        if msg == "NOT_CONFIGURED":
            return api_error(
                ErrorCode.NOT_CONFIGURED,
                "未配置 AI 模型，无法生成中文摘要。请到「设置 → AI 设置」填写后重试。",
                data={"configured": False},
            )
        return api_error(
            ErrorCode.UPSTREAM_ERROR,
            msg,
            data={"configured": True},
        )
    except Exception as exc:  # noqa: BLE001 - 上游异常统一转中文错误态
        logger.warning("AI 摘要生成失败：%s", type(exc).__name__)
        return api_error(
            ErrorCode.UPSTREAM_ERROR,
            f"摘要服务调用失败（{type(exc).__name__}），请稍后重试。",
            data={"configured": True},
        )

    return ok({
        **result,
        "source": "用户配置 AI + 公告原文",
        "as_of": _now(),
        "market_status": "AI 摘要",
        "is_realtime": False,
    })
