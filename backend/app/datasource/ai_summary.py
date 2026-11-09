# -*- coding: utf-8 -*-
"""财报/公告中文摘要流水线（第十轮 P4 · R14 · N3 契约）。

设计基准 §8 R14：EDGAR 原文 / 港股繁体 / A 股正文 → LLM 结构化中文摘要
（关键数据表 + 要点 + 风险 三段式）；未配置 Key 时 NOT_CONFIGURED，
界面禁用+引导（功能存在性不降级，J1）。

口径与约束
----------
- **正文零新通道**：复用 `financial_report.detail_cached` 三市场已实测正文链
  （A 股东财 np-cnotice / 港 HKEX ann_type=H 繁体 / 美 EDGAR primaryDocument）。
- **数字忠实**：prompt 硬约束「所有数字必须来自原文、禁止外推」；C14 人核
  7 份（3 美 + 2 港 + 2 A）校验关键数据表与原文一致。
- **幂等落库**：`data/summaries/{key}.json`（key=市场+代码+标题+发布时间哈希）；
  命中直接回 `cached=True` 不重烧 token；`force=True` 才重算覆盖。
- **繁简/语言**：港股繁体原文→中文摘要天然简转；美股英文→中文；A 股本身
  中文，摘要=结构化压缩。
- 超长正文截 `MAX_SOURCE_CHARS` 字（与详情节选同源，人核口径一致）；
  摘要失败=错误态（UPSTREAM），不得以原文冒充摘要（J1）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from backend.app import config
from backend.app.core import netfix
from backend.app.core.errors import NonRetryableError
from backend.app.datasource import financial_report
from backend.app.datasource import utils

logger = logging.getLogger(__name__)

SUMMARY_DIR = config.DATA_DIR / "summaries"
MAX_SOURCE_CHARS = 12000  # 进 prompt 的正文上限（详情展示节选更长，此处控 token 成本）
LLM_TIMEOUT = 120
MARKETS = ("a", "hk", "us")

_PROMPT = """你是财经公告分析师。请阅读公告原文，输出严格 JSON（不要任何其他文字、不要代码围栏），结构：
{"key_table": [{"metric": "指标名", "value": "数值带单位", "yoy": "同比（原文没有则留空字符串）"}],
 "points": ["要点1", "要点2", ...],
 "risks": ["风险或不确定事项1", ...]}

硬性规则（违反即失败）：
1. key_table 的每一个数字必须原样来自公告原文，禁止计算、外推、引用外部知识或编造；原文没有的指标不要列；
2. points 提炼 3~6 条对投资者最重要的事实（业绩、分配、重大合同、股权变动等）；
3. risks 提炼 0~4 条风险/不确定事项（原文无则空数组，不得硬凑）；
4. 全部用简体中文输出（英文/繁体公告需转换语言，专有名词与科目保留通行译法）；
5. 若原文与标题明显不符或为占位空文，输出 {"error": "原文无有效内容"}。
"""


def is_configured() -> bool:
    """AI 摘要模型是否已配置（未配置界面按钮禁用+引导）。

    Returns:
        bool: base_url 与 model 均非空为 True。
    """
    try:
        ai = config.load_ai_config()
    except Exception:  # noqa: BLE001 - 读取失败按未配置
        return False
    return bool(ai.base_url and ai.model)


# P1 收敛：_now_text 统一委托 utils.now_text（北京时间，替代手写 datetime.now(UTC+8)）。
_now_text = utils.now_text



def _key(market: str, code: str, title: str, pub_time: str) -> str:
    """摘要幂等键。

    Args:
        market: a/hk/us。
        code: 证券代码。
        title: 公告标题。
        pub_time: 发布时间。

    Returns:
        str: 文件名安全的哈希键。
    """
    raw = f"{market}|{code}|{title}|{pub_time}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _path(key: str) -> Path:
    """摘要落盘路径。

    Args:
        key: 幂等键。

    Returns:
        Path: JSON 文件路径。
    """
    return SUMMARY_DIR / f"{key}.json"


def _parse_reply(text: str) -> Dict[str, Any]:
    """从模型回复提取 JSON（容忍围栏/前后杂讯）。

    Args:
        text: 模型原始回复。

    Returns:
        Dict[str, Any]: 含 key_table/points/risks 或 error。

    Raises:
        ValueError: 完全无法解析出 JSON。
    """
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(text or "").strip())
    try:
        obj = json.loads(cleaned)
    except (ValueError, TypeError):
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if not match:
            raise ValueError("模型未返回可解析 JSON")
        obj = json.loads(match.group(0))
    if not isinstance(obj, dict):
        raise ValueError("模型返回不是对象")
    return obj


def _normalize(parsed: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """字段级清洗成契约结构；模型判无有效内容返回 None。

    Args:
        parsed: _parse_reply 结果。

    Returns:
        Optional[Dict[str, Any]]: {key_table, points, risks}；error/不可用返回 None。
    """
    if parsed.get("error"):
        return None
    table = []
    for row in parsed.get("key_table") or []:
        if not isinstance(row, dict):
            continue
        metric = str(row.get("metric") or "").strip()
        value = str(row.get("value") or "").strip()
        if not metric or not value:
            continue
        table.append({"metric": metric, "value": value, "yoy": str(row.get("yoy") or "").strip()})
    points = [str(x).strip() for x in (parsed.get("points") or []) if str(x).strip()][:8]
    risks = [str(x).strip() for x in (parsed.get("risks") or []) if str(x).strip()][:6]
    if not table and not points:
        return None
    return {"key_table": table, "points": points, "risks": risks}


def _llm_summarize(content: str) -> Dict[str, Any]:
    """调用户配置的 OpenAI 兼容模型生成摘要（复用 translate 同款调用范式）。

    Args:
        content: 公告正文。

    Returns:
        Dict[str, Any]: {summary, model}。

    Raises:
        NonRetryableError: 未配置 / 调用失败 / 结构异常 / 无有效内容。
    """
    ai = config.load_ai_config()
    if not (ai.base_url and ai.model):
        raise NonRetryableError("NOT_CONFIGURED")
    body_text = content[:MAX_SOURCE_CHARS]
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
                    {"role": "user", "content": f"公告标题与正文如下：\n{body_text}"},
                ],
                "temperature": 0.1,
            }, ensure_ascii=False).encode("utf-8"),
            timeout=LLM_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.exceptions.RequestException as exc:
        logger.warning("摘要模型调用失败：%s", type(exc).__name__)
        raise NonRetryableError(f"摘要服务调用失败（{type(exc).__name__}），请检查 AI 设置") from exc
    finally:
        session.close()
    try:
        reply = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise NonRetryableError("摘要服务返回结构异常") from exc
    parsed = _parse_reply(reply)
    summary = _normalize(parsed)
    if summary is None:
        raise NonRetryableError("原文无有效内容或模型未产出摘要（不以原文冒充）")
    return {"summary": summary, "model": ai.model}


def summarize(
    *,
    market: str = "a",
    code: str = "",
    title: str = "",
    pub_time: str = "",
    doc_url: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    """财报/公告中文摘要主入口（幂等落库 + force 重算）。

    Args:
        market: a / hk / us。
        code: 证券代码。
        title: 公告标题。
        pub_time: 发布时间。
        doc_url: 原文链接（EDGAR/HKEX 正文抓取需要）。
        force: True=忽略已存摘要重算。

    Returns:
        Dict[str, Any]: {summary:{key_table,points,risks}, lang, source_text_chars,
        truncated, cached, model, source, caliber, as_of}。

    Raises:
        NonRetryableError: 未配置 Key（上层转 NOT_CONFIGURED）/ 正文不可得 /
            模型失败（错误态，不白屏不假数据不冒充）。
    """
    market = str(market or "a").strip().lower()
    if market not in MARKETS:
        raise NonRetryableError(f"摘要仅支持 A 股/港股/美股，收到：{market}")
    code = str(code or "").strip()
    title = str(title or "").strip()
    if not title and not code:
        raise NonRetryableError("摘要需要公告标题或证券代码")

    key = _key(market, code, title, pub_time)
    path = _path(key)
    if not force and path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            if stored.get("summary"):
                stored["cached"] = True
                return stored
        except (OSError, ValueError):
            pass  # 坏档视同未命中重算

    detail = financial_report.detail_cached(
        market=market, title=title, code=code,
        pub_time=str(pub_time or ""), doc_url=str(doc_url or ""),
    )
    content = str(detail.get("content") or "")
    if not content.strip():
        # 空/错分离：正文不可得=成功信封 + summary 空 + 中文口径（与详情弹窗同语义）
        return {
            "status": "no_content",
            "summary": None,
            "lang": "zh-CN",
            "source_text_chars": 0,
            "truncated": False,
            "cached": False,
            "model": None,
            "body_source": detail.get("source"),
            "market": market,
            "code": code,
            "title": title,
            "caliber": f"正文未能获取（{detail.get('caliber') or '源站无内容'}），未生成摘要（不造数）；可直接打开原文",
            "as_of": _now_text(),
        }

    result = _llm_summarize(content)
    payload = {
        "summary": result["summary"],
        "lang": "zh-CN",
        "source_text_chars": len(content),
        "truncated": len(content) > MAX_SOURCE_CHARS,
        "cached": False,
        "model": result["model"],
        "body_source": detail.get("source"),
        "market": market,
        "code": code,
        "title": title,
        "generated_at": _now_text(),
        "source": "用户配置 AI + 公告原文",
        "caliber": (
            "AI 结构化中文摘要（关键数据表/要点/风险三段式）；表内数字原样取自原文"
            "（temperature=0.1），要点为原文事实提炼；"
            "港股繁体/美股英文已转述为简体；摘要仅供研究参考，数据以原文为准"
        ),
        "as_of": _now_text(),
    }
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)  # 同目录原子替换，中断不产生半截档
    return payload
