# -*- coding: utf-8 -*-
"""文本翻译（港美股英文财报/公告 → 中文，G-4 配套）。

设计：
- 复用「设置 → AI 设置」保存的 OpenAI 兼容接口（base_url / api_key / model），
  不引入新的第三方翻译 SDK、不把密钥写进代码；
- 未配置模型时**如实返回 NOT_CONFIGURED**，不假装翻译过、不返回原文冒充译文；
- 长文本按字符上限截断并标注，避免单次请求打爆模型上下文。

调用链：`routers/ai.py::ai_translate` → :func:`translate_text`。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

import requests

from backend.app import config
from backend.app.core import netfix
from backend.app.core.errors import NonRetryableError

logger = logging.getLogger(__name__)

# 单次翻译最大字符数（超长截断，防止撑爆上下文；单位=字符）
MAX_CHARS = 4000

_PROMPT = (
    "你是金融翻译助手。把下面的英文财经文本翻译成简体中文，"
    "保持数字、公司名、代码与专业术语准确，不要添加解释，只输出译文本身。"
)


def is_configured() -> bool:
    """判断 AI 翻译通道是否已配置（base_url 与 model 均非空）。

    Returns:
        bool: 可用返回 True。
    """
    try:
        ai = config.load_ai_config()
    except Exception:  # noqa: BLE001 - 配置损坏视为未配置
        return False
    return bool(ai.base_url and ai.model)


def translate_text(text: str, target: str = "zh") -> Dict[str, Any]:
    """把文本翻译成中文（走用户配置的 OpenAI 兼容模型）。

    Args:
        text: 待翻译文本（英文财报/公告正文）。
        target: 目标语言，目前仅支持 ``zh``。

    Returns:
        Dict[str, Any]: {translated, truncated, chars, target, model, caliber}。

    Raises:
        ValueError: 入参为空或目标语言不支持。
        NonRetryableError: 未配置模型或调用确定性失败。
    """
    body = str(text or "").strip()
    if not body:
        raise ValueError("待翻译文本不可为空")
    if target not in ("zh", "zh-cn", "zh-CN"):
        raise ValueError("目前仅支持翻译为中文（zh）")
    ai = config.load_ai_config()
    if not (ai.base_url and ai.model):
        raise NonRetryableError(
            "未配置 AI 模型：请到「设置 → AI 设置」填写 base_url / api_key / model 后重试"
        )
    truncated = len(body) > MAX_CHARS
    payload_text = body[:MAX_CHARS]
    session = netfix.make_session()
    try:
        response = session.post(
            ai.base_url.rstrip("/") + "/chat/completions",
            headers={
                "Authorization": f"Bearer {ai.api_key or ''}",
                "Content-Type": "application/json",
            },
            data=json.dumps({
                "model": ai.model,
                "messages": [
                    {"role": "system", "content": _PROMPT},
                    {"role": "user", "content": payload_text},
                ],
                "temperature": 0.1,
            }, ensure_ascii=False).encode("utf-8"),
            timeout=120,
        )
        response.raise_for_status()
        body_json: Optional[Dict[str, Any]] = response.json()
    except requests.exceptions.RequestException as exc:
        logger.warning("翻译调用失败：%s", type(exc).__name__)
        raise NonRetryableError(
            f"翻译服务调用失败（{type(exc).__name__}），请检查 AI 设置中的 base_url/密钥"
        ) from exc
    finally:
        session.close()
    try:
        translated = (body_json or {})[ "choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise NonRetryableError("翻译服务返回结构异常，未取得译文") from exc
    return {
        "translated": translated,
        "truncated": truncated,
        "chars": len(body),
        "target": "zh",
        "model": ai.model,
        "caliber": (
            "AI 机翻（OpenAI 兼容接口，temperature=0.1）；"
            + ("原文超长已截断至 4000 字符，" if truncated else "")
            + "机翻仅供参考，重大数据请以原文为准"
        ),
    }
