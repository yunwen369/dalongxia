"""M11 · 组合台账 xlsx 一次性导入（只读导入，系统台账为唯一源）。

口径（设计基准 §12.2 / 用户裁决 3）：
- 支持 `.xlsx` / `.xls`，第一行为表头；
- 自动列映射：代码 / 名称 / 数量 / 成本 / 市值 / 市场 / 类型；未识别列如实回报；
- 三项校验：① 代码必须能在行情接口查到（查不到标红不导入）；② 数量 / 成本为正；
  ③ 合计市值与 xlsx 原值差 > 1 元报警（仅提示，不阻断）；
- **只读导入**：导入后 `data/portfolio.json` 为唯一源，xlsx 不再双向同步；
- 导入日志记入 `data/audit/`（不落 xlsx 原文，只记行数与结果统计）。

分两段式：`parse()` 只读预览（不入库）→ `commit()` 覆盖写入（需二次确认）。
"""

from __future__ import annotations

import io
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

from backend.app.core import audit
from backend.app.datasource import portfolio, utils

logger = logging.getLogger(__name__)

MAX_ROWS = 200  # 单次导入上限（超过请拆分，避免逐条查价超时）

# 列名别名 → 内部字段（全部小写去空格后匹配）
COLUMN_ALIASES: Dict[str, Tuple[str, ...]] = {
    "symbol": ("代码", "股票代码", "基金代码", "证券代码", "code", "symbol", "标的代码"),
    "name": ("名称", "股票名称", "基金名称", "证券名称", "name", "标的名称"),
    "shares": ("数量", "份额", "持股数量", "持仓数量", "股数", "shares", "qty", "quantity"),
    "cost": ("成本", "单位成本", "成本价", "成本单价", "成本价格", "cost"),
    "value": ("市值", "金额", "持仓市值", "市值(元)", "市值（元）", "value", "amount"),
    "market": ("市场", "交易市场", "market"),
    "type": ("类型", "品种", "资产类型", "type", "asset"),
}

# 类型列取值 → 市场
TYPE_TO_MARKET = {
    "股票": "a", "a股": "a", "沪深": "a", "深市": "a", "沪市": "a",
    "基金": "fund", "场外基金": "fund", "公募基金": "fund", "etf": "fund", "lof": "fund",
    "港股": "hk", "香港": "hk", "美股": "us", "美国": "us",
}

# A 股代码前缀（6 位）：60/68 沪市，00/30 深市，4/8/92 北交所
_A_PREFIXES = ("60", "68", "00", "30", "43", "83", "87", "92")
# 场外基金常见前缀（6 位）：01x/02x/1xx/5xx 等，与 A 股冲突时靠「类型」列或名称判定
_FUND_PREFIXES = ("01", "02", "03", "04", "05", "07", "09", "10", "11", "12", "15", "16", "17", "18", "19", "20", "21", "22", "24", "25", "26", "27", "28", "29", "50", "51", "52", "53", "55", "56", "58")


def _norm(text: Any) -> str:
    """列名归一化：去空白、去全角括号、转小写。

    Args:
        text: 原始列名。

    Returns:
        str: 归一化后的列名。
    """
    return str(text or "").strip().replace(" ", "").replace("　", "").lower()


def _read_frame(data: bytes, filename: str):
    """把上传字节读成 DataFrame（xlsx 用 openpyxl，xls 用 xlrd）。

    Args:
        data: 文件字节。
        filename: 原始文件名（决定引擎）。

    Returns:
        pandas.DataFrame: 首个 sheet 的表格。

    Raises:
        ValueError: 文件格式不支持或解析失败。
    """
    import pandas as pd

    lower = filename.lower()
    if lower.endswith(".xls") and not lower.endswith(".xlsx"):
        engine = "xlrd"
    elif lower.endswith(".xlsx"):
        engine = "openpyxl"
    else:
        raise ValueError("仅支持 .xlsx 或 .xls 文件")
    try:
        frame = pd.read_excel(io.BytesIO(data), sheet_name=0, engine=engine, dtype=object)
    except Exception as exc:  # noqa: BLE001 - 统一转中文给前端
        raise ValueError(f"文件解析失败（{type(exc).__name__}）：请确认是有效的 Excel 文件") from exc
    if frame is None or frame.empty:
        raise ValueError("表格为空：第一行应为表头，第二行起为持仓数据")
    if len(frame) > MAX_ROWS:
        raise ValueError(f"单次最多导入 {MAX_ROWS} 行（当前 {len(frame)} 行），请拆分后再试")
    return frame


def _map_columns(columns: List[Any]) -> Tuple[Dict[str, str], List[str]]:
    """表头 → 字段映射；返回（已识别映射, 未识别列名）。

    Args:
        columns: DataFrame 的列名列表。

    Returns:
        Tuple[Dict[str, str], List[str]]: ({字段: 原列名}, [未识别列名])。
    """
    mapping: Dict[str, str] = {}
    used: set[str] = set()
    for field, aliases in COLUMN_ALIASES.items():
        for column in columns:
            key = _norm(column)
            if key in aliases and field not in mapping:
                mapping[field] = column
                used.add(column)
                break
    unmapped = [str(c) for c in columns if c not in used]
    return mapping, unmapped


def _infer_market(symbol: str, type_text: str) -> Tuple[str, Optional[str]]:
    """推断市场：优先「类型」列，其次代码形态。

    Args:
        symbol: 代码。
        type_text: 类型列原文（可为空）。

    Returns:
        Tuple[str, Optional[str]]: (市场, 提示信息)。
    """
    text = _norm(type_text)
    if text:
        for key, market in TYPE_TO_MARKET.items():
            if key in text:
                return market, None
    code = str(symbol or "").strip().upper()
    if not code:
        return "a", "代码为空"
    if code.isdigit() and len(code) == 5:
        return "hk", None
    if code.isdigit() and len(code) == 6:
        if code.startswith(_A_PREFIXES):
            return "a", None
        if code.startswith(_FUND_PREFIXES):
            # 基金代码与 A 股前缀重叠区（如 000xxx 已是 A 股），此处只处理非 A 股前缀
            return "fund", None
        return "a", "代码前缀无法确定市场，按 A 股处理"
    if any(ch.isalpha() for ch in code):
        return "us", None
    return "a", "无法判断市场，按 A 股处理"


def parse(data: bytes, filename: str) -> Dict[str, Any]:
    """解析并校验 xlsx（只读预览，不写库）。

    Args:
        data: 文件字节。
        filename: 原始文件名。

    Returns:
        Dict[str, Any]: {rows, errors, warnings, mapped, unmapped, summary, caliber}。
            rows 每项含 symbol/name/market/shares/cost/value_xlsx/price/value/ok/errors/warnings。
    """
    frame = _read_frame(data, filename)
    mapping, unmapped = _map_columns(list(frame.columns))

    if "symbol" not in mapping or "shares" not in mapping or "cost" not in mapping:
        missing = [name for name, key in (("代码", "symbol"), ("数量", "shares"), ("成本", "cost")) if key not in mapping]
        raise ValueError(f"缺少必需列：{'、'.join(missing)}（已识别列：{'、'.join(str(c) for c in frame.columns)}）")

    rows: List[Dict[str, Any]] = []
    errors: List[str] = []
    warnings: List[str] = []
    total_value_xlsx = 0.0
    total_value_computed = 0.0

    for index, record in frame.iterrows():
        line_no = int(index) + 2  # 表头占第 1 行
        symbol = str(record.get(mapping["symbol"]) or "").strip()
        if not symbol or symbol.lower() == "nan":
            errors.append(f"第 {line_no} 行：代码为空，已跳过")
            continue
        name = str(record.get(mapping["name"]) or "").strip() if "name" in mapping else ""
        if name.lower() == "nan":
            name = ""
        type_text = str(record.get(mapping.get("type") or "") or "") if "type" in mapping else ""
        market, market_hint = _infer_market(symbol, type_text)
        if "market" in mapping:
            explicit = _norm(record.get(mapping["market"]))
            if explicit in ("a", "a股", "沪深"):
                market = "a"
            elif explicit in ("hk", "港股", "香港"):
                market = "hk"
            elif explicit in ("us", "美股", "美国"):
                market = "us"
            elif explicit in ("fund", "基金", "场外基金"):
                market = "fund"

        row_errors: List[str] = []
        row_warnings: List[str] = []
        try:
            shares = float(record.get(mapping["shares"]))
        except (TypeError, ValueError):
            shares = 0.0
            row_errors.append("数量不是数值")
        try:
            cost = float(record.get(mapping["cost"]))
        except (TypeError, ValueError):
            cost = 0.0
            row_errors.append("成本不是数值")
        if shares <= 0:
            row_errors.append("数量必须大于 0")
        if cost < 0:
            row_errors.append("成本不能为负")
        if market_hint:
            row_warnings.append(market_hint)

        price: Optional[float] = None
        value_computed: Optional[float] = None
        if not row_errors:
            try:
                from backend.app.datasource import tencent

                quote = tencent.get_quote(symbol, market)
                price = float(quote.get("price") or 0.0) or None
            except Exception as exc:  # noqa: BLE001 - 查不到即不导入
                row_errors.append(f"行情接口查不到该代码（{type(exc).__name__}）")
            if price is not None:
                value_computed = round(shares * price, 2)

        value_xlsx: Optional[float] = None
        if "value" in mapping:
            try:
                raw_value = record.get(mapping["value"])
                if raw_value is not None and str(raw_value).strip() not in ("", "nan"):
                    value_xlsx = float(raw_value)
            except (TypeError, ValueError):
                row_warnings.append("市值列不是数值，已忽略该行市值比对")

        if value_xlsx is not None:
            total_value_xlsx += value_xlsx
        if value_computed is not None:
            total_value_computed += value_computed

        rows.append({
            "line": line_no,
            "symbol": symbol,
            "name": name or symbol,
            "market": market,
            "shares": round(shares, 4),
            "cost": round(cost, 4),
            "value_xlsx": value_xlsx,
            "price": round(price, 4) if price is not None else None,
            "value": value_computed,
            "ok": not row_errors,
            "errors": row_errors,
            "warnings": row_warnings,
        })
        for message in row_errors:
            errors.append(f"第 {line_no} 行（{symbol}）：{message}")

    # 合计市值比对（仅当 xlsx 有市值列且两边都有值）
    if total_value_xlsx and total_value_computed:
        diff = abs(total_value_xlsx - total_value_computed)
        if diff > 1.0:
            warnings.append(
                f"合计市值与表格原值差 {diff:,.2f} 元（表格 {total_value_xlsx:,.2f} / 按现价 {total_value_computed:,.2f}），"
                "请确认数量或市值为最新"
            )

    ok_rows = [row for row in rows if row["ok"]]
    return {
        "rows": rows,
        "errors": errors,
        "warnings": warnings,
        "mapped": {field: str(column) for field, column in mapping.items()},
        "unmapped": unmapped,
        "summary": {
            "total": len(rows),
            "importable": len(ok_rows),
            "rejected": len(rows) - len(ok_rows),
            "total_value_xlsx": round(total_value_xlsx, 2) if total_value_xlsx else None,
            "total_value_computed": round(total_value_computed, 2) if total_value_computed else None,
        },
        "caliber": (
            "只读导入：确认后覆盖写入系统台账（data/portfolio.json），xlsx 不再双向同步；"
            "代码必须能在行情接口查到才导入；数量/成本为正；合计市值差 >1 元仅提示不阻断"
        ),
        "as_of": utils.now_text(utils.bj_now()),
    }


def commit(rows: List[Dict[str, Any]], *, confirm: bool = False) -> Dict[str, Any]:
    """把预览通过的行写入台账（覆盖当前 items）。

    Args:
        rows: `parse()` 返回的 rows（只取 ok=true 的行）。
        confirm: 必须显式 True（二次确认），否则拒绝执行。

    Returns:
        Dict[str, Any]: {imported, skipped, total, updated_at}。

    Raises:
        ValueError: 未确认或无可导入行。
    """
    if not confirm:
        raise ValueError("导入需二次确认：请显式提交 confirm=true（覆盖当前台账）")
    accepted = [row for row in rows if row.get("ok")]
    if not accepted:
        raise ValueError("没有可导入的行：请先修正标红的行")

    items = []
    for row in accepted:
        items.append({
            "id": uuid.uuid4().hex[:12],
            "market": row["market"],
            "symbol": row["symbol"],
            "name": row.get("name") or row["symbol"],
            "shares": round(float(row["shares"]), 4),
            "cost": round(float(row["cost"]), 4),
            "note": "xlsx 导入",
            "created_at": utils.now_text(utils.bj_now()),
        })
    portfolio.save({"items": items})
    audit.record(
        "portfolio.import-xlsx",
        "ok",
        {"imported": len(items), "skipped": len(rows) - len(items), "total_rows": len(rows)},
    )
    logger.info("xlsx 导入完成：写入 %d 条，跳过 %d 条", len(items), len(rows) - len(items))
    return {
        "imported": len(items),
        "skipped": len(rows) - len(items),
        "total": len(rows),
        "updated_at": utils.now_text(utils.bj_now()),
    }
