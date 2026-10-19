"""AI 域路由：/api/ai 下 3 个接口（批次 5.5）。

对应规范 v7.2 §5.6 / §12（AI 配置与密钥管理）与 D2 决策：
- GET  /config  读取配置（密钥只返回掩码，绝不回显原文）；
- POST /config  保存配置（写入 data/ai_config.json，已 gitignore，权限最小化）；
- POST /chat    对话（90s 超时；未配置时返回明确引导，不静默降级、不返回假回答）。

安全要点：
1. 密钥只在内存与落盘文件出现，不写日志、不进响应；
2. 保存时校验 base_url 以 http(s):// 开头，防止误填；
3. 对话失败区分「未配置 / 鉴权失败 / 超时 / 上游异常」，文案均含下一步动作。
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List

import requests
from fastapi import APIRouter, Body

from backend.app import config
from backend.app.core.errors import ErrorCode, api_error, ok

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["AI"])


def _now() -> str:
    """当前时间字符串（北京时间口径）。"""
    return time.strftime("%Y-%m-%d %H:%M:%S")


@router.get("/config")
def get_config() -> Dict[str, Any]:
    """读取 AI 配置（密钥掩码）。"""
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
        {base_url, api_key?, model?}；api_key 留空表示保留原值。
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

    new_key = api_key or str(existing.get("api_key") or "")
    if not new_key:
        return api_error(ErrorCode.INVALID_PARAM, "首次保存必须填写 api_key。")

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload_to_save = {
        "base_url": base_url,
        "api_key": new_key,
        "model": model or existing.get("model") or "",
        "updated_at": _now(),
    }
    temp_path = config.AI_CONFIG_FILE.with_suffix(".json.tmp")
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(payload_to_save, handle, ensure_ascii=False, indent=2)
    os.replace(temp_path, config.AI_CONFIG_FILE)

    logger.info("AI 配置已更新（model=%s，key=%s）", payload_to_save["model"],
                config.mask_secret(new_key) if hasattr(config, "mask_secret") else "已设置")
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


@router.post("/chat")
def chat(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """对话（OpenAI 兼容 /chat/completions，90s 超时）。

    Body:
        {message} 或 {messages:[{role,content}, ...]}
    """
    raw_messages = payload.get("messages")
    messages: List[Dict[str, str]] = []
    if isinstance(raw_messages, list) and raw_messages:
        for item in raw_messages[-10:]:  # 只保留最近 10 轮，避免上下文无限增长
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "user")
            content = str(item.get("content") or "").strip()
            if content and role in ("user", "assistant", "system"):
                messages.append({"role": role, "content": content})
    else:
        message = str(payload.get("message") or "").strip()
        if message:
            messages.append({"role": "user", "content": message})

    if not messages:
        return api_error(ErrorCode.INVALID_PARAM, "请提供 message 或 messages，不能为空。")

    ai = config.load_ai_config()
    if not ai.api_key or not ai.base_url:
        return api_error(
            ErrorCode.NOT_CONFIGURED,
            "AI 助手尚未配置：请到「设置 → AI 设置」填写 base_url、api_key、model 后再试。",
            data={"guidance": "打开设置页填写三项配置后即可对话", "configured": False},
        )

    url = ai.base_url.rstrip("/") + "/chat/completions"
    started = time.time()
    try:
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {ai.api_key}", "Content-Type": "application/json"},
            json={"model": ai.model or "gpt-4o-mini", "messages": messages, "temperature": 0.3},
            timeout=90,
        )
    except requests.exceptions.Timeout:
        return api_error(ErrorCode.UPSTREAM_TIMEOUT,
                         "AI 响应超时（90 秒），请稍后重试或缩短问题。", data={"configured": True})
    except Exception as exc:  # noqa: BLE001 - 网络类异常统一中文
        logger.warning("AI 对话连接失败：%s", type(exc).__name__)
        return api_error(ErrorCode.UPSTREAM_ERROR,
                         "无法连接 AI 接口，请检查网络与 base_url 是否正确。", data={"configured": True})

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
        "source": "用户配置 AI",
        "caliber": "AI 回复仅供研究参考，不作为数据源；数据仍以行情/财务接口为准",
        "as_of": _now(),
        "market_status": "对话",
        "is_realtime": False,
    })
