"""场外基金 provider：搜索 / 实时 / 净值 / 持仓 / 档案 / 业绩 / 回撤 / 分红 / 持仓分布。

对应规范 v7.2 §8.6 与基线03 §3.9、§4.11。所有通道均以本机实测为准（2026-10-20）：

- 蛋卷 `djapi/fund/{code}`：档案（fd_name/fd_full_name/found_date/manager_name/totshare/risk_level），
  并内嵌 fd_derived（最新净值、区间涨幅、同类排名）；
- 蛋卷 `djapi/fund/derived/{code}`：阶段业绩 nav_grl1w/1m/3m/6m/1y/2y/3y、同类排名 srank_*、
  定投 aip_grl* / itg_aip_grl*、分年度 annual_performance_list；
- 蛋卷 `djapi/fund/nav/history/{code}?page=&size=`：净值历史，实测 size≤500，total_items 887；
- 蛋卷 `djapi/fund/nav/grtd/{code}`：**需登录**（实测 result_code=300001「请重新登录」），按规范禁用；
- 东财 `api.fund.eastmoney.com/f10/lsjz`：公布净值，实测 pageSize 硬上限 20、TotalCount 887，需翻页；
- 东财 `fundf10.eastmoney.com/fhsp_{code}.html`：分红表 `table.w782.comm.cfxq`
  （年份/权益登记日/除息日/每10份分红/分红发放日），无记录时返回「暂无分红信息!」；
- akshare `fund_name_em` 搜索、`fund_portfolio_hold_em` 重仓（今年→去年→前年）、
  `fund_individual_basic_info_xq` 雪球档案补充。

实时估值口径（D1）：养基宝未接入（阶段 7）、蛋卷 nav_grtd 需登录已禁用，
当前实际可用为「蛋卷 derived 的最新公布净值 + 日涨幅」→「东财 lsjz 最新净值」，
因此界面口径一律标注「T+1 公布净值」，**不得标注盘中实时**。

本模块只做取数与口径清洗，不缓存、不拼装响应。
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from backend.app import config
from backend.app.core import netfix
from backend.app.core.errors import NonRetryableError, NotFoundError
from backend.app.datasource import utils

netfix.apply_netfix()
import akshare as ak  # noqa: E402

logger = logging.getLogger(__name__)

DANJUAN_BASE = "https://danjuanfunds.com"
DANJUAN_REFERER = "https://danjuanfunds.com/"
# 东财基金接口统一从 config 读（P2-08：不在 provider 内硬编码 URL）
EM_LSJZ_URL = config.EASTMONEY_FUND_LSJZ_URL
EM_FHSP_URL = config.EASTMONEY_FUND_FHSP_URL
EM_FHSP_REFERER = config.EASTMONEY_FUND_FHSP_REFERER
# 东财批量估值表进程内缓存（全市场约 798 行，逐只基金复用同一份快照）
_EM_ESTIMATION: Dict[str, Any] = {"at": 0.0, "rows": {}}


def _num(value: Any) -> Optional[float]:
    """安全转 float（字符串含 % 或空值返回 None）。

    Args:
        value: 待处理值（类型由调用方约定）。

    Returns:
        float | None: 数值；无法解析时返回 None（不补 0）。
    """
    if value is None:
        return None
    text = str(value).strip().replace("%", "")
    if not text or text in ("--", "---"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _round(value: Optional[float], digits: int = 2) -> Optional[float]:
    """保留小数位，None 原样返回。

    Args:
        value: 待处理数值。
        digits: 保留小数位数。

    Returns:
        float | None: 数值；无法解析时返回 None（不补 0）。
    """
    return utils.round_or_none(value, digits)


def _danjuan(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """调用蛋卷接口，返回 data 段；result_code 非 0 视为失败（含需登录场景）。

    Args:
        path: 接口路径 / 文件路径。
        params: 请求参数（查询串键值对）。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。

    Raises:
        NonRetryableError: 确定性失效（不重试）时抛出，交由降级链切换下一源。
    """
    payload = utils.http_get_json(
        DANJUAN_BASE + path,
        params=params or {},
        referer=DANJUAN_REFERER,
        timeout=config.DATASOURCE_TIMEOUT,
    )
    code = payload.get("result_code")
    if code not in (0, "0", None):
        raise NonRetryableError(f"蛋卷返回 {code}：{payload.get('message') or '未知原因'}")
    return payload.get("data") or {}


def get_search(keyword: str, limit: int = 20) -> List[Dict[str, Any]]:
    """基金搜索（天天基金全量名录本地过滤，支持代码/简称/拼音）。

    Args:
        keyword: 搜索关键词。
        limit: 返回条数上限（超出上限按上限截断）。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。

    Raises:
        ValueError: 关键词不可为空。
    """
    text = (keyword or "").strip()
    if not text:
        raise ValueError("关键词不可为空")
    frame = ak.fund_name_em()
    rows = frame.to_dict("records")
    lowered = text.lower()
    hits: List[Dict[str, Any]] = []
    for row in rows:
        code = str(row.get("基金代码") or "")
        name = str(row.get("基金简称") or "")
        pinyin = str(row.get("拼音缩写") or "").lower()
        if lowered in code or lowered in name.lower() or (pinyin and lowered in pinyin):
            hits.append(
                {
                    "code": code,
                    "name": name,
                    "type": row.get("基金类型"),
                    "pinyin": row.get("拼音缩写"),
                }
            )
        if len(hits) >= max(1, limit):
            break
    return hits


def get_detail(code: str) -> Dict[str, Any]:
    """基金档案（蛋卷主源）。

    Args:
        code: 证券或基金代码。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。

    Raises:
        ValueError: 参数不合法或上游数据不满足口径时抛出。
    """
    data = _danjuan(f"/djapi/fund/{code}")
    if not data.get("fd_code"):
        raise ValueError(f"蛋卷未返回档案：{code}")
    derived = data.get("fd_derived") or {}
    return {
        "code": data.get("fd_code"),
        "name": data.get("fd_name"),
        "full_name": data.get("fd_full_name"),
        "found_date": data.get("found_date"),
        "manager": data.get("manager_name"),
        "keeper": data.get("keeper_name"),
        "trustee": data.get("trup_name"),
        "risk_level": data.get("risk_level"),
        "scale": data.get("totshare"),
        "type": data.get("fd_type"),
        "subscribe_status": data.get("subscribe_status"),
        "withdraw_status": data.get("withdraw_status"),
        "end_date": derived.get("end_date"),
        "unit_nav": _num(derived.get("unit_nav")),
        "day_growth": _num(derived.get("nav_grtd")),
        "nav_grl1y": _num(derived.get("nav_grl1y")),
        "nav_grl3y": _num(derived.get("nav_grl3y")),
    }


def get_derived(code: str) -> Dict[str, Any]:
    """阶段业绩与排名（蛋卷 derived）。

    Args:
        code: 证券或基金代码。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。

    Raises:
        ValueError: 参数不合法或上游数据不满足口径时抛出。
    """
    data = _danjuan(f"/djapi/fund/derived/{code}")
    if not data:
        raise ValueError(f"蛋卷未返回业绩：{code}")

    periods = [
        ("近1周", "nav_grl1w", "srank_l1w"),
        ("近1月", "nav_grl1m", "srank_l1m"),
        ("近3月", "nav_grl3m", "srank_l3m"),
        ("近6月", "nav_grl6m", "srank_l6m"),
        ("近1年", "nav_grl1y", "srank_l1y"),
        ("近2年", "nav_grl2y", "srank_l2y"),
        ("近3年", "nav_grl3y", "srank_l3y"),
        ("成立以来", "nav_grbase", "srank_base"),
    ]
    rows = []
    for label, growth_key, rank_key in periods:
        value = _num(data.get(growth_key))
        if value is None and not data.get(rank_key):
            continue
        rows.append({"period": label, "growth": _round(value), "rank": data.get(rank_key) or None})

    annual = []
    for item in data.get("annual_performance_list") or []:
        annual.append(
            {
                "period": item.get("period"),
                "nav": _num(item.get("nav")),
                "rank": item.get("rank"),
            }
        )
    return {
        "code": code,
        "end_date": data.get("end_date"),
        "unit_nav": _num(data.get("unit_nav")),
        "unit_acc_nav": _num(data.get("unit_acc_nav")),
        "day_growth": _num(data.get("nav_grtd")),
        "periods": rows,
        "annual": annual,
        "aip": {
            "1y": _num(data.get("aip_grl1y")),
            "2y": _num(data.get("aip_grl2y")),
            "3y": _num(data.get("aip_grl3y")),
        },
        "aip_itg": {
            "1y": _num(data.get("itg_aip_grl1y")),
            "2y": _num(data.get("itg_aip_grl2y")),
            "3y": _num(data.get("itg_aip_grl3y")),
        },
    }


def get_nav(code: str, page: int = 1, size: int = 30) -> Dict[str, Any]:
    """净值历史：蛋卷（size≤500）→ 东财 lsjz（pageSize 20，翻页换算）。

    Args:
        code: 证券或基金代码。
        page: 页码（从 1 开始）。
        size: 条数 / 字节数。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。

    Raises:
        ValueError: 蛋卷净值返回空。
    """
    safe_size = max(1, min(size, 500))
    try:
        data = _danjuan(f"/djapi/fund/nav/history/{code}", {"page": page, "size": safe_size})
        items = [
            {
                "date": item.get("date"),
                "nav": _num(item.get("nav")),
                "growth": _num(item.get("percentage")),
            }
            for item in data.get("items") or []
        ]
        if not items:
            raise ValueError("蛋卷净值返回空")
        return {
            "items": items,
            "page": data.get("current_page") or page,
            "size": data.get("size") or safe_size,
            "total": data.get("total_items"),
            "total_pages": data.get("total_pages"),
            "source": "蛋卷",
            "caliber": "蛋卷/单位净值历史（含日增长率）",
        }
    except Exception as exc:  # noqa: BLE001 - 降级东财
        logger.info("蛋卷净值降级东财：%s（%s）", code, type(exc).__name__)

    # 东财 lsjz 单页硬上限 20：按 20 条/页换算页码与条数
    em_size = 20
    start_index = (page - 1) * size
    em_page = start_index // em_size + 1
    payload = utils.http_get_json(
        EM_LSJZ_URL,
        params={"fundCode": code, "pageIndex": em_page, "pageSize": em_size},
        referer=f"https://fundf10.eastmoney.com/jjjz_{code}.html",
        timeout=config.DATASOURCE_TIMEOUT,
    )
    rows = (payload.get("Data") or {}).get("LSJZList") or []
    items = [
        {
            "date": row.get("FSRQ"),
            "nav": _num(row.get("DWJZ")),
            "growth": _num(row.get("JZZZL")),
        }
        for row in rows
    ]
    if not items:
        # 东财明确返回「0 条」= 该基金代码不存在（不是通道故障）→ 快速失败为 NOT_FOUND，
        # 与「通道异常」区分开（空/错分离，规范 §6.3 + 原型 §7.1）。
        total_count = utils.to_int(payload.get("TotalCount"))
        if total_count == 0 or payload.get("ErrCode") in (0, None):
            raise NotFoundError(f"未找到基金代码 {code} 的净值记录")
        raise ValueError(f"净值通道均无数据：{code}")
    total = utils.to_int(payload.get("TotalCount"))
    return {
        "items": items[:size],
        "page": page,
        "size": size,
        "total": total,
        "total_pages": (total + size - 1) // size if total else None,
        "source": "东财",
        "caliber": "东财/公布净值（pageSize 硬上限 20，已按页换算）",
    }


def _all_nav_items(code: str) -> Dict[str, Any]:
    """拉取基金全历史净值序列（蛋卷分页翻到底，东财降级只取首页）。

    R6-16 / BUG-R6-13：最大回撤必须基于**全历史**，原实现固定 `size=500`，
    对成立满 2 年的基金只能覆盖最近 500 个交易日，会漏掉更早的更深谷底
    （2026-10-28 实测 017811：500 条窗口 -46.69% vs 全历史 -47.52%，
    漏掉 2024-02-05 的谷底）。

    安全约束：
      1. 最多翻 12 页（蛋卷 size=500 时 894 条仅需 2 页，12 页足够覆盖 6000 个交易日）；
      2. 任一页失败即停止，返回已拿到的部分（不因后段失败丢掉前段）；
      3. 东财降级路径分页成本高（pageSize 硬上限 20），只取首页并如实标注来源。

    Args:
        code: 基金代码。

    Returns:
        dict: {"items": [...], "source": str, "pages": int, "total": int|None}。

    Raises:
        ValueError: 首屏无数据。
    """
    first = get_nav(code, page=1, size=500)
    items: List[Dict[str, Any]] = list(first.get("items") or [])
    source = str(first.get("source") or "")
    if not items:
        raise ValueError(f"净值通道均无数据：{code}")
    if source != "蛋卷":
        return {"items": items, "source": source, "pages": 1, "total": first.get("total")}
    try:
        total_pages = int(first.get("total_pages") or 1)
    except (TypeError, ValueError):
        total_pages = 1
    try:
        total_items = int(first.get("total") or len(items))
    except (TypeError, ValueError):
        total_items = len(items)
    pages = 1
    page = 2
    while page <= min(total_pages, 12) and len(items) < total_items:
        try:
            chunk = get_nav(code, page=page, size=500)
        except Exception as exc:  # noqa: BLE001 - 后段翻页失败保留已得数据
            logger.info("净值翻页中止（第 %s 页，%s）：%s", page, type(exc).__name__, code)
            break
        rows = chunk.get("items") or []
        if not rows:
            break
        items.extend(rows)
        pages = page
        page += 1
    return {"items": items, "source": source, "pages": pages, "total": total_items}


def _t1_latest(code: str) -> Dict[str, Any]:
    """T+1 基准净值（蛋卷 derived → 东财 lsjz），供拟合估算取基准，不进入估值链。

    Args:
        code: 证券或基金代码。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。

    Raises:
        ValueError: 参数不合法或上游数据不满足口径时抛出。
    """
    try:
        derived = get_derived(code)
        if derived.get("unit_nav") is not None:
            return {
                "nav": derived.get("unit_nav"),
                "nav_date": derived.get("end_date"),
                "day_growth": derived.get("day_growth"),
                "source": "蛋卷",
            }
    except Exception:  # noqa: BLE001 - 继续东财兜底
        pass
    payload = utils.http_get_json(
        EM_LSJZ_URL,
        params={"fundCode": code, "pageIndex": 1, "pageSize": 1},
        referer=f"https://fundf10.eastmoney.com/jjjz_{code}.html",
        timeout=config.DATASOURCE_TIMEOUT,
    )
    rows = (payload.get("Data") or {}).get("LSJZList") or []
    if not rows:
        raise ValueError(f"无 T+1 基准净值：{code}")
    return {
        "nav": _num(rows[0].get("DWJZ")),
        "nav_date": rows[0].get("FSRQ"),
        "day_growth": _num(rows[0].get("JZZZL")),
        "source": "东财",
    }


def _batch_pct(codes: list) -> Dict[str, float]:
    """批量取 A 股涨跌幅（腾讯 q= 多代码，字段 32 为涨跌幅%）。

    单次请求替代 N 次串行请求，显著降低耗时与限频风险；解析失败返回空字典。

    Args:
        codes: 代码列表。

    Returns:
        dict: 指标名 → 数值。
    """
    if not codes:
        return {}
    symbols = []
    for code in codes:
        if code.startswith(("6", "9")):
            symbols.append("sh" + code)
        elif code.startswith(("0", "2", "3")):
            symbols.append("sz" + code)
        else:
            symbols.append("bj" + code)
    text = utils.http_get_text(
        # P2-08：腾讯报价基址/Referer 收口到 config，禁止 provider 内硬编码
        config.TENCENT_QUOTE_BASE + "/q=" + ",".join(symbols),
        referer=config.TENCENT_REFERER,
        encoding="gbk",
        timeout=config.DATASOURCE_TIMEOUT,
    )
    result: Dict[str, float] = {}
    for line in text.splitlines():
        if '="' not in line:
            continue
        parts = line.split('="', 1)[1].strip('";').split("~")
        if len(parts) < 33:
            continue
        code = str(parts[2])
        pct = utils.to_float(parts[32])
        if pct is not None:
            result[code] = pct
    return result


def get_fitted_estimate(code: str) -> Dict[str, Any]:
    """重仓股加权拟合盘中估算（规范 §12.5 第 2 级）。

    公式：est_pct = Σ(占净值比_i × 个股涨跌幅_i) / Σ(占净值比_i)
    - 只取前 10 大重仓（东财季报口径），权重按「占净值比例」；
    - 个股涨跌幅取腾讯实时快照（收盘后为最后成交快照）；
    - 覆盖度 = Σ权重（%），覆盖度越低越不可靠，需在界面标注。

    Returns:
        dict: {pct, nav, nav_date, source, caliber, coverage, is_estimate}

    Args:
        code: 证券或基金代码。

    Raises:
        ValueError: 参数不合法或上游数据不满足口径时抛出。
    """
    holding = get_holding(code)
    # 注意：这里必须取「T+1 基准净值」，不能调用 get_realtime——后者在增强通道开启时会
    # 再次进入本函数，形成递归（实测导致 180 秒级耗时）。
    t1 = _t1_latest(code)
    # 批量取价：腾讯 qt.gtimg.cn 支持逗号分隔多只，避免 10 次串行请求（实测串行易超时）
    codes = [str(item.get("code") or "") for item in (holding.get("items") or [])[:10]]
    pct_map = _batch_pct([item for item in codes if item])
    weighted = 0.0
    weight_sum = 0.0
    used = 0
    for item in holding.get("items") or []:
        ratio = _num(item.get("ratio")) or 0.0
        stock_code = str(item.get("code") or "")
        if not stock_code or ratio <= 0:
            continue
        pct = pct_map.get(stock_code)
        if pct is None:
            continue
        weighted += ratio * pct
        weight_sum += ratio
        used += 1
    if weight_sum <= 0 or used == 0:
        raise ValueError(f"拟合估算不可用（无有效重仓行情）：{code}")
    est_pct = weighted / weight_sum
    base_nav = _num(t1.get("nav"))
    est_nav = round(base_nav * (1 + est_pct / 100), 4) if base_nav else None
    return {
        "code": code,
        "nav": est_nav,
        "nav_date": t1.get("nav_date"),
        "day_growth": round(est_pct, 4),
        "source": "蛋卷拟合",
        "caliber": "拟合估算：前十大重仓按占净值比加权（非官方净值，仅供参考）",
        "coverage": round(weight_sum, 2),
        "used_holdings": used,
        "is_estimate": True,
        "base_nav": base_nav,
        "quarter": holding.get("quarter"),
    }


def _em_estimation_rows() -> Dict[str, Dict[str, Any]]:
    """东财批量盘中估值表（``fund_value_estimation_em``）→ 以基金代码为键的索引。

    该接口一次返回全市场约 798 行（实测 2.9s），因此按 60 秒进程内缓存，
    避免逐只基金重复拉全表。列名带日期前缀（如 ``2026-10-23-估算数据-估算值``），
    故用后缀匹配解析，不写死具体日期。

    Returns:
        Dict[str, Dict[str, Any]]: 基金代码 → 归一化估值记录。

    Raises:
        Exception: 上游不可用时原样抛出，由调用方降级到下一通道。
    """
    now = time.time()
    cached_at = float(_EM_ESTIMATION.get("at") or 0)
    if _EM_ESTIMATION.get("rows") and now - cached_at < 60:
        return _EM_ESTIMATION["rows"]

    frame = ak.fund_value_estimation_em(symbol="全部")
    columns = [str(col) for col in frame.columns]
    # 列名解析：日期前缀 + 字段后缀；缺失关键列时直接判不可用（不猜列序）
    def pick(suffix: str) -> Optional[str]:
        """按后缀匹配列名。

        Args:
            suffix: 列名后缀（如 `-估算数据-估算值`）。

        Returns:
            Optional[str]: 命中的列名；未命中返回 None。
        """
        for column in columns:
            if column.endswith(suffix):
                return column
        return None

    estimate_column = pick("-估算数据-估算值")
    growth_column = pick("-估算数据-估算增长率")
    if not estimate_column or not growth_column:
        raise ValueError("东财估值表缺少估算值/估算增长率列")
    estimate_date = estimate_column.split("-估算数据-")[0]
    deviation_column = pick("-估算偏差")
    published_column = pick("-公布数据-单位净值")

    rows: Dict[str, Dict[str, Any]] = {}
    for record in frame.to_dict("records"):
        code = str(record.get("基金代码") or "").strip()
        if not code:
            continue
        rows[code] = {
            "name": str(record.get("基金名称") or "").strip(),
            "estimate": _num(record.get(estimate_column)),
            "growth": _num(record.get(growth_column)),
            "deviation": _num(record.get(deviation_column)) if deviation_column else None,
            "published_nav": _num(record.get(published_column)) if published_column else None,
            "estimate_date": estimate_date,
        }
    _EM_ESTIMATION.update({"at": now, "rows": rows})
    return rows


def get_em_estimation(code: str) -> Dict[str, Any]:
    """东财批量估值的单只基金盘中估算（免费拟合估算源，D1 合规口径）。

    仅在「估算数据日期 == 今天」时视为**当日盘中估算**；否则该表只是上一交易日的
    历史估算，不能冒充盘中估值（红线：不得用估算值冒充净值），直接判不可用并
    让降级链继续到 T+1 净值。

    Args:
        code: 6 位基金代码。

    Returns:
        dict: `{code, nav, nav_date, day_growth, source, caliber, is_estimate, deviation, base_nav}`。

    Raises:
        ValueError: 该基金不在估值表内，或估算数据不是当日（含收盘/非交易日）。
    """
    record = _em_estimation_rows().get(str(code).strip())
    if not record:
        raise ValueError("东财估值表未收录该基金")
    estimate = record.get("estimate")
    growth = record.get("growth")
    if estimate is None or growth is None:
        raise ValueError("东财估值表缺少估算值/增长率")
    estimate_date = str(record.get("estimate_date") or "")
    if estimate_date != utils.now_date_text():
        raise ValueError(f"东财估值数据非当日（{estimate_date or '未知'}），不作为盘中估值")
    return {
        "code": code,
        "nav": estimate,
        "nav_date": estimate_date,
        "day_growth": growth,
        "source": "东财估值",
        "caliber": "盘中拟合估算（东财批量估值表，非官方净值，仅供参考）",
        "is_estimate": True,
        "deviation": record.get("deviation"),
        "base_nav": record.get("published_nav"),
    }


def get_realtime(code: str) -> Dict[str, Any]:
    """基金最新净值与日涨幅（默认 T+1 口径；增强通道开启时优先盘中估算）。

    降级链（D1 合规口径，逐级记录 attempts）：
    ① 养基宝 increase-rate（需绑定+签名盐）→ ①-b 养基宝 gz-data
    → ② 东财批量估值 fund_value_estimation_em（免费，仅当日估算可用）
    → ③ 蛋卷拟合（前十大重仓加权）→ ④ 蛋卷 derived（T+1 公布净值）→ ⑤ 东财 lsjz。
    盘中估算链仅在「已确认告知书 + 开启增强通道」时执行；关闭即直落 T+1。

    Returns:
        dict: {code, nav, nav_date, day_growth, source, caliber, is_estimate, attempts}

    Args:
        code: 证券或基金代码。

    Raises:
        ValueError: 参数不合法或上游数据不满足口径时抛出。
        NotFoundError: 全部净值通道均无有效净值（代码不存在或已清盘），P1-02。
    """
    attempts: List[Dict[str, Any]] = []

    # ① 养基宝：阶段 7 才接入，按 D1 明确跳过（不阻断后续通道）
    if config.DATA_SOURCES.get("yangjibao", {}).get("enabled"):
        attempts.append({"channel": "养基宝", "ok": False, "reason": "config.DATA_SOURCES 未登记（运行时由用户开关直连）"})
    else:
        attempts.append({"channel": "养基宝", "ok": False, "reason": "config.DATA_SOURCES 未登记（运行时由用户开关直连）"})

    # ② 蛋卷 nav_grtd：需登录，规范明确禁用，仅登记不重试
    attempts.append({"channel": "蛋卷 nav_grtd", "ok": False, "reason": "需登录（300001），依规范禁用"})

    # ② 盘中估值链（养基宝需「告知书+增强开关」；东财免费源由「盘中估值」开关单独控制）
    try:
        from backend.app.core import user_prefs
        from backend.app.datasource import yangjibao

        state = yangjibao.status()
        yjb_on = bool(state.get("enabled") and (state.get("consent") or {}).get("accepted"))
        estimate_on = user_prefs.get_flag("fund_estimation")
        if yjb_on or estimate_on:
            if yjb_on:
                try:
                    # 真实接口按 6 位代码匹配；先查内部 fund_id 反而会匹配不到
                    estimate = yangjibao.fetch_increase_rate(code)
                    attempts.append({"channel": "养基宝 increase-rate", "ok": True})
                    return {
                        "code": code,
                        "nav": estimate.get("nav"),
                        "nav_date": str(estimate.get("as_of") or utils.now_date_text())[:10],
                        "day_growth": estimate["pct"],
                        "source": "养基宝",
                        "caliber": "非官方·拟合估算·仅供参考·不构成投资建议",
                        "is_estimate": True,
                        "attempts": attempts,
                    }
                except Exception as exc:  # noqa: BLE001 - 逐级降级
                    attempts.append({"channel": "养基宝 increase-rate", "ok": False,
                                     "reason": type(exc).__name__})
                # ①-b 养基宝 gz-data（increase-rate 失败后的同源备选）
                try:
                    gz_estimate = yangjibao.fetch_gz_data(code)
                    attempts.append({"channel": "养基宝 gz-data", "ok": True})
                    return {
                        "code": code,
                        "nav": gz_estimate.get("nav"),
                        "nav_date": str(gz_estimate.get("as_of") or utils.now_date_text())[:10],
                        "day_growth": gz_estimate["pct"],
                        "source": "养基宝",
                        "caliber": "非官方·拟合估算·仅供参考·不构成投资建议",
                        "is_estimate": True,
                        "attempts": attempts,
                    }
                except Exception as exc:  # noqa: BLE001 - 继续降级
                    attempts.append({"channel": "养基宝 gz-data", "ok": False,
                                     "reason": type(exc).__name__})
            if estimate_on:
                # ② 东财批量估值（免费源，仅当日估算可用；非当日如实判不可用）
                try:
                    em_estimate = get_em_estimation(code)
                    attempts.append({"channel": "东财估值", "ok": True})
                    em_estimate["attempts"] = attempts
                    return em_estimate
                except Exception as exc:  # noqa: BLE001
                    attempts.append({"channel": "东财估值", "ok": False,
                                     "reason": str(exc)[:60] or type(exc).__name__})
                try:
                    fitted = get_fitted_estimate(code)
                    attempts.append({"channel": "蛋卷拟合", "ok": True})
                    fitted["attempts"] = attempts
                    return fitted
                except Exception as exc:  # noqa: BLE001
                    attempts.append({"channel": "蛋卷拟合", "ok": False, "reason": type(exc).__name__})
        else:
            attempts.append({"channel": "盘中估值链", "ok": False,
                             "reason": "未开启增强通道或未确认合规告知书"})
    except Exception as exc:  # noqa: BLE001 - 状态读取失败不阻断 T+1
        attempts.append({"channel": "盘中估值链", "ok": False, "reason": type(exc).__name__})

    # ③ 蛋卷 derived
    try:
        derived = get_derived(code)
        nav = derived.get("unit_nav")
        # 空值冒充成功（P1-02）：不存在的代码（如 999999）蛋卷返回 unit_nav=0.0
        # 且无净值日期。原实现只判 `is not None`，于是 0.0 被当成"查到净值"，
        # 前端渲染成「净值 0.0 的真基金」。此处要求净值 > 0 且日期存在，
        # 否则视为「查到但净值缺失」，继续降级；全部通道都拿不到 → NOT_FOUND。
        if nav is not None and nav > 0 and derived.get("end_date"):
            attempts.append({"channel": "蛋卷 derived", "ok": True})
            return {
                "code": code,
                "nav": nav,
                "nav_date": derived.get("end_date"),
                "day_growth": derived.get("day_growth"),
                "source": "蛋卷",
                "caliber": "T+1 公布净值（非盘中估值）",
                "is_estimate": False,
                "attempts": attempts,
            }
        attempts.append({"channel": "蛋卷 derived", "ok": False,
                         "reason": "净值为 0 或净值日期缺失"})
    except Exception as exc:  # noqa: BLE001 - 继续降级
        attempts.append({"channel": "蛋卷 derived", "ok": False, "reason": type(exc).__name__})

    # ④ 东财 lsjz
    payload = utils.http_get_json(
        EM_LSJZ_URL,
        params={"fundCode": code, "pageIndex": 1, "pageSize": 1},
        referer=f"https://fundf10.eastmoney.com/jjjz_{code}.html",
        timeout=config.DATASOURCE_TIMEOUT,
    )
    rows = (payload.get("Data") or {}).get("LSJZList") or []
    nav = _num((rows[0] or {}).get("DWJZ")) if rows else None
    if not rows or nav is None or nav <= 0:
        # 三通道全无有效净值 → 该代码不存在（或已清盘），返回 NOT_FOUND 而非空数据成功
        attempts.append({"channel": "东财 lsjz", "ok": False, "reason": "无有效净值"})
        raise NotFoundError(f"未找到基金代码 {code} 的有效净值")
    row = rows[0]
    attempts.append({"channel": "东财 lsjz", "ok": True})
    return {
        "code": code,
        "nav": _num(row.get("DWJZ")),
        "nav_date": row.get("FSRQ"),
        "day_growth": _num(row.get("JZZZL")),
        "source": "东财",
        "caliber": "T+1 公布净值（非盘中估值）",
        "is_estimate": False,
        "attempts": attempts,
    }


def get_holding(code: str) -> Dict[str, Any]:
    """重仓股（akshare fund_portfolio_hold_em，今年→去年→前年回退）。

    口径（2026-10-20 实测修订）：东财按年返回时会**拼接多个季度**的行
    （如 2026 年返回「1季度」+「2季度」两段，旧季度在前）。必须先取
    **最新季度**、再按「占净值比例」降序取前 10，否则拿到的是旧季报
    前 10（拟合估值权重偏差，属数据口径错误）。

    Args:
        code: 证券或基金代码。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。

    Raises:
        ValueError: 参数不合法或上游数据不满足口径时抛出。
    """
    year_now = utils.today_bj().year  # TZ-06：跨年时按北京时间判定年份
    tried: List[str] = []
    for year in (year_now, year_now - 1, year_now - 2):
        try:
            frame = ak.fund_portfolio_hold_em(symbol=code, date=str(year))
        except Exception as exc:  # noqa: BLE001 - 单年失败继续回退
            tried.append(f"{year}:{type(exc).__name__}")
            continue
        rows = frame.to_dict("records")
        if not rows:
            tried.append(f"{year}:空")
            continue
        # 1) 定位最新季度（季度串格式「YYYY年N季度股票投资明细」，字典序即时序）
        latest_quarter = max((row.get("季度") or "" for row in rows), default="")
        quarter_rows = [row for row in rows if (row.get("季度") or "") == latest_quarter]
        # 2) 按占净值比例降序，真·前 10 大重仓
        quarter_rows.sort(key=lambda row: _num(row.get("占净值比例")) or 0.0, reverse=True)
        items: List[Dict[str, Any]] = []
        for row in quarter_rows[:10]:
            items.append(
                {
                    "code": str(row.get("股票代码") or ""),
                    "name": row.get("股票名称"),
                    "ratio": _num(row.get("占净值比例")),
                    "shares": _num(row.get("持股数")),
                    # 持仓市值单位为万元（东财季报口径）
                    "value": _num(row.get("持仓市值")),
                    "quarter": row.get("季度"),
                }
            )
        return {
            "code": code,
            "year": year,
            "items": items,
            "quarter": latest_quarter,
            "source": "东财季报",
            "caliber": "东财基金季报重仓（最新季度前10，市值单位=万元，比例=占净值比）",
        }
    raise ValueError(f"重仓数据不可用：{code}（{','.join(tried)}）")


def get_info(code: str) -> Dict[str, Any]:
    """基金档案聚合：蛋卷主源 + 雪球补字段（失败不影响主档）。

    Args:
        code: 证券或基金代码。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    detail = get_detail(code)
    extra: Dict[str, Any] = {}

    def _fetch_xq() -> Dict[str, Any]:
        """抓一次雪球档案并转成 item→value 字典。

        Returns:
            dict: 字段字典；失败返回空字典。
        """
        try:
            frame = ak.fund_individual_basic_info_xq(symbol=code)
            return {str(row.get("item")): row.get("value") for row in frame.to_dict("records")}
        except Exception as exc:  # noqa: BLE001 - 雪球为补充源
            logger.info("雪球档案降级：%s（%s）", code, type(exc).__name__)
            return {}

    extra = _fetch_xq()
    # 雪球偶发限流会返回缺字段/空表（实测全量回归时出现「基金公司」为 None）。
    # 补齐关键字段前重试一次，退避 0.8s，避免把第三方抖动透传给用户。
    if not extra.get("基金公司") or not extra.get("最新规模"):
        time.sleep(0.8)
        retry = _fetch_xq()
        if retry.get("基金公司") or retry.get("最新规模"):
            extra = retry
    detail["xq"] = {
        "成立时间": extra.get("成立时间"),
        # 键名与前端 i18n 的 fund.kv.scale（"基金规模"）保持一致（R2-04：
        # 原为"最新规模"，前后端键名不一致导致规模行永远显示"无数据"）
        "基金规模": extra.get("最新规模"),
        "基金公司": extra.get("基金公司"),
        "基金经理": extra.get("基金经理"),
        "托管银行": extra.get("托管银行"),
    }
    detail["source"] = "蛋卷/雪球"
    detail["caliber"] = "蛋卷档案为主，雪球补齐成立时间/规模/公司"
    return detail


def get_performance(code: str) -> Dict[str, Any]:
    """阶段业绩（蛋卷 derived），附名称从档案接口取（derived 不含 fd_name）。

    Args:
        code: 证券或基金代码。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    derived = get_derived(code)
    name = None
    try:
        name = get_detail(code).get("name")
    except Exception:  # noqa: BLE001 - 名称缺失不阻断业绩
        name = None
    derived["name"] = name
    derived["source"] = "蛋卷"
    derived["caliber"] = "蛋卷 derived：阶段涨幅/同类排名/分年度（净值增长率口径）"
    return derived


def get_drawdown(code: str, start: str = "", end: str = "") -> Dict[str, Any]:
    """最大回撤（基于净值序列自算）。

    公式（注释即口径）：
    - 设净值序列为 NAV[0..n]，累计峰值 peak[i] = max(NAV[0..i])；
    - 单点回撤 dd[i] = NAV[i] / peak[i] - 1（负值）；
    - 最大回撤 maxDD = min(dd)，对应区间为 [peak 所在日, 谷底日]；
    - 当前回撤 = NAV[-1] / peak[-1] - 1。

    Args:
        start: 起始日期（YYYY-MM-DD）。
        end: 结束日期（YYYY-MM-DD）。
        code: 基金代码。
        start/end: 可选日期过滤（YYYY-MM-DD），留空取全部。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。

    Raises:
        ValueError: 参数不合法或上游数据不满足口径时抛出。
    """
    # R6-16：全历史序列（原 size=500 会漏掉更早的谷底，最大回撤被算小）
    payload = _all_nav_items(code)
    items = [item for item in payload["items"] if item.get("nav")]
    items.sort(key=lambda item: item.get("date") or "")
    if start:
        items = [item for item in items if (item.get("date") or "") >= start]
    if end:
        items = [item for item in items if (item.get("date") or "") <= end]
    if len(items) < 2:
        raise ValueError(f"净值样本不足，无法计算回撤：{code}")

    peak_value = items[0]["nav"]
    peak_date = items[0]["date"]
    max_dd = 0.0
    dd_peak_date = peak_date
    dd_trough_date = peak_date
    series: List[Dict[str, Any]] = []
    for item in items:
        nav = item["nav"]
        if nav > peak_value:
            peak_value = nav
            peak_date = item["date"]
        drawdown = nav / peak_value - 1 if peak_value else 0.0
        series.append({"date": item["date"], "drawdown": round(drawdown * 100, 2)})
        if drawdown < max_dd:
            max_dd = drawdown
            dd_peak_date = peak_date
            dd_trough_date = item["date"]

    current = items[-1]["nav"] / peak_value - 1 if peak_value else 0.0
    return {
        "code": code,
        "start": items[0]["date"],
        "end": items[-1]["date"],
        "max_drawdown": round(max_dd * 100, 2),
        "peak_date": dd_peak_date,
        "trough_date": dd_trough_date,
        "current_drawdown": round(current * 100, 2),
        "series": series[-250:],
        "source": payload.get("source"),
        # R6-16：口径写明「全历史」及实际样本区间，避免与"最近 500 日"混淆
        "caliber": (
            f"基于单位净值全历史自算（{items[0]['date']} 起，共 {len(items)} 条）："
            "dd=NAV/累计峰值-1，最大回撤取序列最小值；图表仅展示最近 250 个点"
        ),
        "sample_size": len(items),
        "sample_from": items[0]["date"],
    }


def get_position(code: str) -> Dict[str, Any]:
    """基金持仓分布（行业配置，东财 F10 ``/f10/HYPZ/`` 接口，纯 HTTP）。

    2026-11-02 第八轮：原实现依赖蛋卷 ``/djapi/fund/position``，该端点需登录
    （rc=300001）被禁用（降级点 G-9）。改用天天基金 F10「行业配置」接口：
    按报告期返回各行业市值与**占净值比例**，可完整替代「持仓分布」视图。

    Args:
        code: 6 位基金代码。

    Returns:
        Dict[str, Any]: {code, name, report_date, items: [{industry, market_value, pct}],
        source, caliber}

    Raises:
        ValueError: 接口无数据或结构异常。
    """
    payload = _em_f10("HYPZ", code, {"year": ""})
    data = payload.get("Data") or {}
    quarters = data.get("QuarterInfos") or []
    if not quarters:
        raise ValueError(f"东财行业配置无数据：{code}")
    latest = quarters[0]
    rows = latest.get("HYPZInfo") or []
    items: List[Dict[str, Any]] = []
    for row in rows:
        pct = utils.to_float(row.get("ZJZBL"))
        if pct is None:
            continue
        items.append(
            {
                "industry": row.get("HYMC"),
                "code": row.get("HYDM"),
                "market_value": utils.to_float(row.get("SZ")),
                "pct": round(pct, 2),
            }
        )
    items.sort(key=lambda item: item["pct"], reverse=True)
    if not items:
        raise ValueError(f"东财行业配置为空：{code}")
    return {
        "code": code,
        "name": data.get("ShortName"),
        "report_date": str(latest.get("JZRQ") or "")[:10],
        "items": items,
        "source": "东财 F10",
        "caliber": (
            f"天天基金 F10 行业配置（报告期 {str(latest.get('JZRQ') or '')[:10]}），"
            "行业按证监会行业分类，占比=该行业市值/基金净值；季报口径，非实时"
        ),
    }


def _em_f10(path: str, code: str, extra: Dict[str, Any]) -> Dict[str, Any]:
    """请求天天基金 F10 JSON 接口（需 Referer，纯 HTTP）。

    Args:
        path: 接口名（HYPZ=行业配置 / ZCPZ=资产配置 …）。
        code: 6 位基金代码。
        extra: 额外查询参数。

    Returns:
        Dict[str, Any]: 解析后的 JSON；失败返回空字典。

    Raises:
        ValueError: 响应不是合法 JSON。
    """
    params = {"fundCode": code}
    params.update(extra)
    text = utils.http_get_text(
        f"https://api.fund.eastmoney.com/f10/{path}/",
        params=params,
        referer="https://fundf10.eastmoney.com/",
        encoding="utf-8",
        timeout=config.DATASOURCE_TIMEOUT,
    )
    return json.loads(text)


def get_dividend(code: str) -> Dict[str, Any]:
    """分红记录（东财 fhsp HTML 解析）。

    Args:
        code: 证券或基金代码。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。

    Raises:
        ValueError: 参数不合法或上游数据不满足口径时抛出。
    """
    html = utils.http_get_text(
        EM_FHSP_URL.format(code=code),
        referer=EM_FHSP_REFERER,
        encoding="utf-8",
        timeout=config.DATASOURCE_TIMEOUT,
    )
    if "暂无分红信息" in html:
        return {
            "code": code,
            "items": [],
            "source": "东财",
            "caliber": "东财分红页（该基金暂无分红记录）",
        }
    match = re.search(r"class='w782 comm cfxq'.*?</table>", html, re.S)
    segment = match.group(0) if match else ""
    rows = re.findall(r"<tr>(.*?)</tr>", segment, re.S)
    items: List[Dict[str, Any]] = []
    for row in rows:
        cells = [
            re.sub(r"<[^>]+>", "", cell).replace("&nbsp;", "").strip()
            for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        ]
        if len(cells) < 5 or not re.match(r"\d{4}", cells[0] or ""):
            continue
        amount_match = re.search(r"([0-9.]+)\s*元", cells[3] or "")
        items.append(
            {
                "year": cells[0],
                "record_date": cells[1],
                "ex_date": cells[2],
                "per_10": _num(amount_match.group(1)) if amount_match else None,
                "per_10_text": cells[3],
                "pay_date": cells[4],
            }
        )
    if not items:
        raise ValueError(f"分红表解析为空：{code}")
    return {
        "code": code,
        "items": items,
        "source": "东财",
        "caliber": "东财分红页：每10份派现金（元/10份）",
    }


