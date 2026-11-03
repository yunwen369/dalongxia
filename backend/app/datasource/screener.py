# -*- coding: utf-8 -*-
"""多因子筛选器（第七轮 M9）：全市场快照 + 条件 AND 查询。

数据源与口径（2026-10-31 实测）：
- 估值/行情：东财 datacenter ``RPT_VALUEANALYSIS_DET`` 按交易日全量分页
  （pageSize=500 × 12 页 ≈ 5577 只，全量约 5.2s），字段含
  CLOSE_PRICE / CHANGE_RATE / TOTAL_MARKET_CAP / PE_TTM / PB_MRQ / PS_TTM；
  **不走 push2 域**（`stock_zh_a_spot_em` 实测代理重置不可用，L-20 同口径）。
- 财务：akshare ``stock_yjbb_em``（业绩报表：ROE/毛利率/营收与净利同比）、
  ``stock_zcfz_em``（资产负债表：资产负债率）、``stock_xjll_em``（现金流量表：
  经营现金流）按报告期全量取，单期约 6–17s。
- ROE 连续 3 年：取最近三个**年报**（YYYY1231）的 `净资产收益率`，全满足才算连续。

快照与刷新（用户 2026-10-31 裁决）：
- 存储落 ``data/screener_snapshot.json``（版本号 + 行数 + 构建耗时）；
- 每日 18:00 后首个请求惰性触发自动构建（当日未建过才建）；
- 「立即刷新快照」为后台线程异步任务：构建中查询读上一版快照，不阻塞；
- 快照未建库 → ``ready=false`` + 说明，不以 0 只命中冒充。

条件语义：
- 最多 8 个条件 AND 连接；因子 7 类（估值/成长/质量/现金流/股息/规模/动量）；
- 财务类因子在快照里缺值时该行不满足该条件（不冒充）；
- 金融业（银行/保险/券商）对「资产负债率」自动豁免并在行内标注 ``exempt``；
- 本模块**不排序、不给推荐语**：只按代码升序返回，排序交给前端。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.app import config
from backend.app.core import atomic
from backend.app.datasource import utils

logger = logging.getLogger(__name__)

# 落盘路径在模块级求值，conftest 需逐用例 patch（与 portfolio 同模式）
SNAPSHOT_FILE = config.DATA_DIR / "screener_snapshot.json"

_NETFIX_DONE = False  # akshare 导入前 netfix 只做一次


def _ensure_netfix() -> None:
    """确保 netfix 已应用（akshare 导入前必须修证书路径，与 stock.py 同款）。"""
    global _NETFIX_DONE
    if _NETFIX_DONE:
        return
    from backend.app.core import netfix

    netfix.apply_netfix()
    _NETFIX_DONE = True


# 模块级一次性导入 akshare（单线程、netfix 之后）。
# 禁止在函数/线程内并发 import akshare：首次导入会初始化 py_mini_racer(V8)，
# 多线程并发首次创建 MiniRacer 上下文 → PartitionAlloc 双初始化 → 进程级 FATAL 崩溃
# （2026-11-02 实测：_dividend_yield_batch 的 6 线程并发 import 触发）。
_ensure_netfix()
import akshare as ak  # noqa: E402


def _now_text() -> str:
    """返回当前北京时间文本（禁止裸 date.today，时区口径收口）。

    Returns:
        str: ``YYYY-MM-DD HH:MM:SS``。
    """
    return utils.now_text(utils.bj_now())


# =============================================================
# 一、快照存储
# =============================================================
def _read_snapshot() -> Dict[str, Any]:
    """读取本地快照；缺失或损坏返回空字典。

    Returns:
        Dict[str, Any]: 快照载荷。
    """
    import json

    try:
        return json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_snapshot(payload: Dict[str, Any]) -> None:
    """原子写快照（进程中断不损坏旧版）。

    Args:
        payload: 快照载荷。
    """
    atomic.atomic_write_json(SNAPSHOT_FILE, payload, ensure_ascii=False)


def snapshot_ready() -> bool:
    """判断本地全市场快照是否已建库（供服务启动预热判断是否自动构建）。

    Returns:
        bool: 快照文件存在且有行数据时为 True。
    """
    payload = _read_snapshot()
    return bool(payload.get("rows"))


def get_snapshot_meta() -> Dict[str, Any]:
    """返回快照元信息（不含行数据），供状态卡与测试。

    Returns:
        Dict[str, Any]: {ready, version, built_at, trade_date, row_count,
        building, progress, last_error}。
    """
    payload = _read_snapshot()
    building = is_build_running()
    row_count = len(payload.get("rows") or [])
    meta: Dict[str, Any] = {
        "ready": row_count > 0,
        "version": payload.get("version"),
        "built_at": payload.get("built_at"),
        "trade_date": payload.get("trade_date"),
        "row_count": row_count,
        "building": building,
        "last_error": _BUILD_STATE.get("last_error"),
    }
    if building:
        meta["progress"] = {
            "done": _BUILD_STATE.get("done", 0),
            "total": _BUILD_STATE.get("total", 0),
            "step": _BUILD_STATE.get("step", ""),
        }
    else:
        meta["progress"] = {
            "done": row_count,
            "total": row_count,
            "step": "idle" if row_count else "none",
        }
    return meta


# =============================================================
# 二、快照构建（异步 + 进度）
# =============================================================
_BUILD_LOCK = threading.Lock()
_BUILD_STATE: Dict[str, Any] = {
    "running": False, "done": 0, "total": 0, "step": "idle", "last_error": None,
}


def is_build_running() -> bool:
    """是否有构建任务在跑。

    Returns:
        bool: 判定结果。
    """
    with _BUILD_LOCK:
        return bool(_BUILD_STATE.get("running"))


def _em_pages(
    report_name: str,
    filter_text: str,
    *,
    sort: str = "SECURITY_CODE",
    page_size: int = 500,
    max_pages: int = 40,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> List[Dict[str, Any]]:
    """东财 datacenter 报表分页全量拉取（非 push2 域，L-20 同口径）。

    Args:
        report_name: 报表名。
        filter_text: filter 表达式。
        sort: 排序字段（稳定分页必须排序）。
        page_size: 每页条数（实测 500 可用）。
        max_pages: 页数上限（防御源站异常时的超长分页）。
        on_progress: 进度回调 (done, total, step)。

    Returns:
        List[Dict[str, Any]]: 全量数据行。

    Raises:
        RuntimeError: 首页拉取失败（视为源不可用）。
    """
    rows: List[Dict[str, Any]] = []
    page = 1
    while page <= max_pages:
        try:
            payload = utils.http_get_json(
                config.EASTMONEY_DATACENTER_URL,
                params={
                    "reportName": report_name,
                    "columns": "ALL",
                    "filter": filter_text,
                    "pageNumber": page,
                    "pageSize": page_size,
                    "sortColumns": sort,
                    "sortTypes": "1",
                    "source": "WEB",
                    "client": "WEB",
                },
                referer=config.EASTMONEY_REFERER,
                timeout=config.DATASOURCE_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001 - 首页失败视为源不可用
            if page == 1:
                raise RuntimeError(f"datacenter 首页失败：{type(exc).__name__}") from exc
            logger.warning("datacenter 第 %d 页失败，已取前 %d 行", page, len(rows))
            break
        result = payload.get("result") or {}
        data = result.get("data") or []
        rows.extend(data)
        total = int(result.get("count") or 0)
        pages = int(result.get("pages") or 0)
        if on_progress:
            on_progress(len(rows), total, f"{report_name} p{page}/{pages}")
        if page >= pages or not data:
            break
        page += 1
        time.sleep(0.25)  # 限速：对源站礼貌间隔（§6.5）
    return rows


def _latest_trade_date() -> Optional[str]:
    """从估值明细报表取最新交易日（不猜日历）。

    Returns:
        Optional[str]: 'YYYY-MM-DD'；源不可用返回 None。
    """
    try:
        payload = utils.http_get_json(
            config.EASTMONEY_DATACENTER_URL,
            params={
                "reportName": "RPT_VALUEANALYSIS_DET",
                "columns": "TRADE_DATE",
                "filter": "",
                "pageNumber": 1,
                "pageSize": 1,
                "sortColumns": "TRADE_DATE",
                "sortTypes": "-1",
                "source": "WEB",
                "client": "WEB",
                "isDetail": "true",
            },
            referer=config.EASTMONEY_REFERER,
            timeout=config.DATASOURCE_TIMEOUT,
        )
        data = (payload.get("result") or {}).get("data") or []
        if not data:
            return None
        return str(data[0].get("TRADE_DATE") or "")[:10] or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("取最新交易日失败：%s", type(exc).__name__)
        return None


def _num(value: Any) -> Optional[float]:
    """安全转 float（空串/NaN → None）。

    Args:
        value: 原始值。

    Returns:
        Optional[float]: 结果。
    """
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:  # NaN
        return None
    return result


def _last_completed_quarter(today: Any) -> str:
    """最近一个已披露完毕的报告期（YYYYMMDD）。

    当日恰为季末时不取当日（披露未齐全），取上一个季末。

    Args:
        today: date 对象（北京时间）。

    Returns:
        str: 报告期。
    """
    year = today.year
    quarters = [
        f"{year}1231", f"{year}0930", f"{year}0630", f"{year}0331",
        f"{year - 1}1231", f"{year - 1}0930", f"{year - 1}0630", f"{year - 1}0331",
    ]
    today_text = today.strftime("%Y%m%d")
    for report in quarters:
        if report < today_text:
            return report
    return f"{year - 1}1231"


def _annual_reports(today: Any, count: int = 3) -> List[str]:
    """最近 N 个**已披露**年报报告期（YYYY1231），新→旧。

    年报 Y1231 的披露截止为次年 4/30，故只有 today >= (Y+1)-05-01 的期才可拉取；
    否则 akshare 会抛 TypeError，该期整列缺值后按「任一年缺值视为不满足」
    会把全市场 ROE 判为不满足（2026-10-31 实测 5577 行 roe3y_min 全为 null）。

    Args:
        today: date 对象。
        count: 年报个数。

    Returns:
        List[str]: 报告期列表。
    """
    from datetime import date as _date

    def disclosed(year: int) -> bool:
        """判断某年度报告是否已到披露截止日。

        Args:
            year: 报告年度（如 2025 表示 2025 年报）。

        Returns:
            bool: 已过 Y+1 年 4/30 则视为已披露完毕。
        """
        # Y 年报在 Y+1 年 4/30 前披露完毕，5/1 起视为可用
        return today >= _date(year + 1, 5, 1)

    result: List[str] = []
    offset = 0
    while len(result) < count and offset <= count + 2:
        year = today.year - offset
        if disclosed(year):
            result.append(f"{year}1231")
        offset += 1
    return result


def _load_yjbb(reports: List[str]) -> Dict[str, Dict[str, Dict[str, Optional[float]]]]:
    """拉取业绩报表（stock_yjbb_em）若干期，按代码聚合。

    Args:
        reports: 报告期列表（YYYYMMDD）。

    Returns:
        Dict[str, Dict[str, Dict[str, Optional[float]]]]:
            {code: {report: {roe, gross, rev_yoy, np_yoy}}}。
    """
    _ensure_netfix()
    # akshare 已在模块顶层导入，此处直接使用模块级 `ak`

    result: Dict[str, Dict[str, Dict[str, Optional[float]]]] = {}
    for report in reports:
        try:
            frame = ak.stock_yjbb_em(date=report)
        except Exception as exc:  # noqa: BLE001 - 单期失败不拖垮整表
            logger.warning("业绩报表 %s 失败：%s", report, type(exc).__name__)
            continue
        if frame is None or frame.empty:
            continue
        for _, row in frame.iterrows():
            code = str(row.get("股票代码") or "").zfill(6)
            slot = result.setdefault(code, {})
            slot[report] = {
                "roe": _num(row.get("净资产收益率")),
                "gross": _num(row.get("销售毛利率")),
                "rev_yoy": _num(row.get("营业总收入-同比增长")),
                "np_yoy": _num(row.get("净利润-同比增长")),
            }
    return result


def _load_zcfz(reports: List[str]) -> Dict[str, Dict[str, Dict[str, Optional[float]]]]:
    """拉取资产负债表快照（stock_zcfz_em）若干期，按代码聚合。

    Args:
        reports: 报告期列表。

    Returns:
        Dict[str, Dict[str, Dict[str, Optional[float]]]]:
            {code: {report: {debt_ratio}}}。
    """
    _ensure_netfix()
    # akshare 已在模块顶层导入，此处直接使用模块级 `ak`

    result: Dict[str, Dict[str, Dict[str, Optional[float]]]] = {}
    for report in reports:
        try:
            frame = ak.stock_zcfz_em(date=report)
        except Exception as exc:  # noqa: BLE001
            logger.warning("资产负债表 %s 失败：%s", report, type(exc).__name__)
            continue
        if frame is None or frame.empty:
            continue
        for _, row in frame.iterrows():
            code = str(row.get("股票代码") or "").zfill(6)
            slot = result.setdefault(code, {})
            slot[report] = {"debt_ratio": _num(row.get("资产负债率"))}
    return result


def _load_xjll(reports: List[str]) -> Dict[str, Dict[str, Dict[str, Optional[float]]]]:
    """拉取现金流量表快照（stock_xjll_em）若干期，按代码聚合。

    Args:
        reports: 报告期列表。

    Returns:
        Dict[str, Dict[str, Dict[str, Optional[float]]]]:
            {code: {report: {ocf, ncf}}}。
    """
    _ensure_netfix()
    # akshare 已在模块顶层导入，此处直接使用模块级 `ak`

    result: Dict[str, Dict[str, Dict[str, Optional[float]]]] = {}
    for report in reports:
        try:
            frame = ak.stock_xjll_em(date=report)
        except Exception as exc:  # noqa: BLE001
            logger.warning("现金流量表 %s 失败：%s", report, type(exc).__name__)
            continue
        if frame is None or frame.empty:
            continue
        for _, row in frame.iterrows():
            code = str(row.get("股票代码") or "").zfill(6)
            slot = result.setdefault(code, {})
            slot[report] = {
                "ocf": _num(row.get("经营性现金流-现金流量净额")),
                "ncf": _num(row.get("净现金流-净现金流")),
            }
    return result


# 金融业豁免：资产负债率对银行/保险/券商不可比（M7 同一口径）
FINANCIAL_BOARD_MARKS = ("银行", "保险", "证券", "券商", "多元金融")

# 快照行字段（写进返回值，前端据此渲染表头）
SNAPSHOT_ROW_FIELDS = {
    "code": "SECURITY_CODE",
    "name": "SECURITY_NAME_ABBR",
    "close": "CLOSE_PRICE",
    "chg": "CHANGE_RATE",
    "mktcap": "TOTAL_MARKET_CAP",  # 元
    "pe": "PE_TTM",
    "pb": "PB_MRQ",
    "ps": "PS_TTM",
    "pcf": "PCF_OCF_TTM",  # 经营现金流市值比（TTM，倍）
}


def _build_rows(
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """构建快照行：估值全量 + 三张财务报表按代码聚合。

    Args:
        on_progress: 进度回调。

    Returns:
        Tuple[List[Dict[str, Any]], Dict[str, Any]]: (行列表, 统计信息)。

    Raises:
        RuntimeError: 无法确定最新交易日（估值明细源不可用）。
    """
    today = utils.bj_now().date()
    trade_date = _latest_trade_date()
    if not trade_date:
        raise RuntimeError("无法确定最新交易日（估值明细源不可用）")

    # 1) 估值/行情全量（唯一行情源）
    val_rows = _em_pages(
        "RPT_VALUEANALYSIS_DET",
        f"(TRADE_DATE='{trade_date}')",
        on_progress=on_progress,
    )
    if not val_rows:
        raise RuntimeError(f"估值明细 {trade_date} 无数据")

    # 2) 财务：最近季度 + 最近 3 个年报
    quarter = _last_completed_quarter(today)
    annuals = _annual_reports(today, 3)
    reports_needed = list({quarter, *annuals})

    if on_progress:
        on_progress(0, 0, "财务报表：业绩")
    yjbb = _load_yjbb(reports_needed)
    if on_progress:
        on_progress(0, 0, "财务报表：资产负债")
    zcfz = _load_zcfz(reports_needed)
    if on_progress:
        on_progress(0, 0, "财务报表：现金流")
    xjll = _load_xjll(reports_needed)

    rows: List[Dict[str, Any]] = []
    for item in val_rows:
        raw_code = str(item.get("SECURITY_CODE") or "").zfill(6)
        # 只留 A 股正股：沪深京 6 位数字代码（估值明细里 C 市场为京市）
        if not raw_code.isdigit():
            continue
        board = str(item.get("BOARD_NAME") or "")
        name = str(item.get("SECURITY_NAME_ABBR") or "")
        code = raw_code

        row: Dict[str, Any] = {
            "code": code,
            "name": name,
            "board": board,
            "close": _num(item.get("CLOSE_PRICE")),
            "chg": _num(item.get("CHANGE_RATE")),
            "mktcap": _num(item.get("TOTAL_MARKET_CAP")),
            "pe": _num(item.get("PE_TTM")),
            "pb": _num(item.get("PB_MRQ")),
            "ps": _num(item.get("PS_TTM")),
            "pcf": _num(item.get("PCF_OCF_TTM")),
        }

        # ROE 连续 3 年（年报口径）：三年都有值且全满足阈值才 True；
        # 任一年缺值 → None（不冒充「满足」，也不冒充「不满足」）
        roe_series = [
            (yjbb.get(code) or {}).get(year, {}).get("roe") for year in annuals
        ]
        gross_latest = None
        rev_yoy = None
        np_yoy = None
        fin = yjbb.get(code) or {}
        for report in (quarter, *annuals):
            slot = fin.get(report)
            if slot:
                if gross_latest is None:
                    gross_latest = slot.get("gross")
                if rev_yoy is None:
                    rev_yoy = slot.get("rev_yoy")
                if np_yoy is None:
                    np_yoy = slot.get("np_yoy")
                if gross_latest is not None and rev_yoy is not None and np_yoy is not None:
                    break

        debt_latest = None
        for report in (quarter, *annuals):
            slot = (zcfz.get(code) or {}).get(report)
            if slot and slot.get("debt_ratio") is not None:
                debt_latest = slot.get("debt_ratio")
                break

        ocf_latest = None
        for report in (quarter, *annuals):
            slot = (xjll.get(code) or {}).get(report)
            if slot and slot.get("ocf") is not None:
                ocf_latest = slot.get("ocf")
                break

        row["roe_years"] = roe_series  # [最近年报, 去年, 前年]，可能含 None
        row["gross"] = gross_latest
        row["rev_yoy"] = rev_yoy
        row["np_yoy"] = np_yoy
        row["debt_ratio"] = debt_latest
        row["ocf"] = ocf_latest
        # 财报覆盖率标记：四项财务字段全缺 → 该行财务区视为缺失（AC-X1 不冒充）
        fin_any = any(v is not None for v in (gross_latest, rev_yoy, np_yoy, debt_latest, ocf_latest))
        row["fin_missing"] = not fin_any
        rows.append(row)

    stats = {
        "trade_date": trade_date,
        "quarter": quarter,
        "annuals": annuals,
        "val_rows": len(val_rows),
    }
    return rows, stats


def _run_build(reason: str) -> Dict[str, Any]:
    """同步执行快照构建并落盘（调用方负责加 _BUILD_LOCK 或在工作线程内）。

    Args:
        reason: 触发原因（auto/manual），写入版本元信息。

    Returns:
        Dict[str, Any]: 构建结果 {version, row_count, elapsed, trade_date}。

    Raises:
        Exception: 构建失败时向上抛出（自动构建吞掉，手动构建返回错误）。
    """
    started = time.time()
    _BUILD_STATE.update({"running": True, "done": 0, "total": 0, "step": "启动", "last_error": None})
    try:
        rows, stats = _build_rows(
            on_progress=lambda done, total, step: _BUILD_STATE.update(
                {"done": done, "total": total, "step": step}
            ),
        )
        version = utils.bj_now().strftime("%Y%m%d%H%M%S")
        payload = {
            "version": version,
            "built_at": _now_text(),
            "reason": reason,
            "trade_date": stats["trade_date"],
            "quarter": stats["quarter"],
            "annuals": stats["annuals"],
            "elapsed": round(time.time() - started, 1),
            "rows": rows,
        }
        _write_snapshot(payload)
        _BUILD_STATE.update({"running": False, "step": "完成"})
        logger.info(
            "筛选器快照构建完成：%s %d 行，耗时 %.1fs（%s）",
            version, len(rows), time.time() - started, reason,
        )
        return {
            "version": version,
            "row_count": len(rows),
            "elapsed": round(time.time() - started, 1),
            "trade_date": stats["trade_date"],
        }
    except Exception as exc:  # noqa: BLE001
        _BUILD_STATE.update({"running": False, "last_error": f"{type(exc).__name__}"})
        logger.warning("筛选器快照构建失败：%s %s", type(exc).__name__, str(exc)[:120])
        raise


def start_build_async(reason: str = "manual") -> Dict[str, Any]:
    """启动后台构建线程（异步、立即返回）。

    构建中查询读上一版快照，不阻塞（用户裁决 4）。

    Args:
        reason: 触发原因（auto/manual）。

    Returns:
        Dict[str, Any]: {started, building}；已有任务在跑则 started=False。
    """
    with _BUILD_LOCK:
        if _BUILD_STATE.get("running"):
            return {"started": False, "building": True}
        _BUILD_STATE["running"] = True  # 先占位，防止并发双跑

    def _worker() -> None:
        """工作线程：执行构建并复位状态。"""
        try:
            _run_build(reason)
        except Exception as exc:  # noqa: BLE001 - 失败已记 last_error，线程不得向外抛
            logger.warning("筛选器快照构建失败（last_error 已记录）：%s", type(exc).__name__)
        finally:
            # 占位与 _run_build 内部复位会叠加，这里兜底确保退出态正确
            with _BUILD_LOCK:
                _BUILD_STATE["running"] = False

    threading.Thread(target=_worker, name="screener-build", daemon=True).start()
    return {"started": True, "building": True}


def maybe_auto_build() -> Dict[str, Any]:
    """每日 18:00 后惰性自动构建（当日未建过才触发）。

    Returns:
        Dict[str, Any]: {triggered, building, reason}。
    """
    now = utils.bj_now()
    payload = _read_snapshot()
    built_day = str(payload.get("built_at") or "")[:10]
    today_text = now.strftime("%Y-%m-%d")
    if payload.get("rows") and built_day == today_text:
        return {"triggered": False, "building": False, "reason": "当日已有快照"}
    if now.hour < 18:
        return {"triggered": False, "building": False, "reason": "未到 18:00 自动构建窗口"}
    result = start_build_async(reason="auto")
    return {"triggered": bool(result.get("started")), "building": True,
            "reason": "每日 18:00 惰性自动构建"}


# =============================================================
# 三、条件查询
# =============================================================
# 因子注册表：{key: (中文名, 单位, 类型)}；类型 finance 的缺值行不满足条件
FACTOR_META: Dict[str, Dict[str, str]] = {
    "pe": {"label": "PE-TTM", "unit": "倍", "kind": "quote"},
    "pb": {"label": "PB-MRQ", "unit": "倍", "kind": "quote"},
    "ps": {"label": "PS-TTM", "unit": "倍", "kind": "quote"},
    "pcf": {"label": "经营现金流市值比", "unit": "倍", "kind": "quote"},
    "chg": {"label": "涨跌幅", "unit": "%", "kind": "quote"},
    "mktcap": {"label": "总市值", "unit": "亿元", "kind": "quote", "scale": 1e8},
    "roe_min": {"label": "ROE（近3年年报均≥）", "unit": "%", "kind": "finance"},
    "gross": {"label": "销售毛利率", "unit": "%", "kind": "finance"},
    "rev_yoy": {"label": "营收同比", "unit": "%", "kind": "finance"},
    "np_yoy": {"label": "净利同比", "unit": "%", "kind": "finance"},
    "debt_ratio": {"label": "资产负债率", "unit": "%", "kind": "finance", "exempt_fin": True},
    "ocf": {"label": "经营现金流净额", "unit": "元", "kind": "finance"},
    # 第七轮 M13 补齐：股息率（派息比例/收盘价）。快照不含此字段，故不进 SNAPSHOT_ROW_FIELDS，
    # 由 run_candidates 对**已通过其余规则的候选**逐只按需拉取（并发 + 缓存），不扫全市场 5577 只。
    "dividend_yield": {"label": "股息率（近一年派息/收盘）", "unit": "%", "kind": "dividend"},
}
# 允许的因子键（防注入白名单）
FACTOR_KEYS = set(FACTOR_META) | {"roe_years_any"}
MAX_CONDITIONS = 8
OPERATORS = ("gte", "lte")


def _row_value(row: Dict[str, Any], key: str) -> Optional[float]:
    """取某行在因子上的取值（mktcap 元→亿元）。

    Args:
        row: 快照行。
        key: 因子键。

    Returns:
        Optional[float]: 取值；缺值返回 None。
    """
    if key == "roe_min":
        series = row.get("roe_years") or []
        if not series or any(v is None for v in series):
            return None
        return min(series)  # 连续 3 年的「最弱一年」
    value = row.get(key)
    if key == "mktcap" and value is not None:
        return value / 1e8
    return _num(value)


def run_screener(
    conditions: List[Dict[str, Any]],
    *,
    limit: int = 100,
) -> Dict[str, Any]:
    """执行条件筛选（只查本地快照，不触源站）。

    Args:
        conditions: [{factor, op, value}]，op ∈ {gte, lte}；最多 8 条。
        limit: 返回行数上限（默认 100，防御结果过大）。

    Returns:
        Dict[str, Any]: {ready, total_matched, returned, columns, items, caliber, snapshot}。

    Raises:
        ValueError: 条件不合法（因子未知 / op 未知 / 超量 / value 非数）。
        RuntimeError: 快照未建库。
    """
    if len(conditions) > MAX_CONDITIONS:
        raise ValueError(f"条件最多 {MAX_CONDITIONS} 个（收到 {len(conditions)} 个）")
    parsed: List[Tuple[str, str, float]] = []
    for cond in conditions:
        if not isinstance(cond, dict):
            raise ValueError("条件必须为 {factor, op, value} 对象")
        factor = str(cond.get("factor") or "").strip()
        op = str(cond.get("op") or "").strip()
        if factor not in FACTOR_KEYS:
            raise ValueError(f"未知因子：{factor}")
        if op not in OPERATORS:
            raise ValueError(f"运算符必须是 gte（≥）或 lte（≤）：{op}")
        value = _num(cond.get("value"))
        if value is None:
            raise ValueError(f"阈值必须为数字：{factor}")
        parsed.append((factor, op, value))

    payload = _read_snapshot()
    rows = payload.get("rows") or []
    if not rows:
        raise RuntimeError("快照未建库：请先点击「立即刷新快照」构建（首次约 1–2 分钟）")

    matched: List[Dict[str, Any]] = []
    exempt_count = 0
    for row in rows:
        keep = True
        exempted: List[str] = []
        for factor, op, value in parsed:
            meta = FACTOR_META.get(factor, {})
            # 金融业豁免：资产负债率对银行/保险/券商不可比 → 跳过该条件并标注
            if meta.get("exempt_fin") and any(
                mark in str(row.get("board") or "") for mark in FINANCIAL_BOARD_MARKS
            ):
                exempted.append(factor)
                continue
            actual = _row_value(row, factor)
            if actual is None:
                keep = False  # 缺值不满足（不冒充），与 AC-X1 同口径
                break
            if op == "gte" and not actual >= value:
                keep = False
                break
            if op == "lte" and not actual <= value:
                keep = False
                break
        if not keep:
            continue
        if exempted:
            exempt_count += 1
        matched.append({
            **{k: row.get(k) for k in ("code", "name", "board", "close", "chg",
                                       "pe", "pb", "ps", "pcf")},
            "mktcap": (row.get("mktcap") / 1e8) if row.get("mktcap") is not None else None,
            "gross": row.get("gross"),
            "rev_yoy": row.get("rev_yoy"),
            "np_yoy": row.get("np_yoy"),
            "debt_ratio": row.get("debt_ratio"),
            "roe3y_min": _row_value(row, "roe_min"),
            "ocf_yi": (row.get("ocf") / 1e8) if row.get("ocf") is not None else None,
            "exempt": exempted,
        })
        if len(matched) >= limit:
            break

    matched.sort(key=lambda item: item["code"])  # 稳定输出；排序交前端
    meta = get_snapshot_meta()
    return {
        "ready": True,
        "total_matched": len(matched),
        "returned": len(matched),
        "truncated": len(matched) >= limit and len(matched) == limit,
        "items": matched,
        "exempt_count": exempt_count,
        "snapshot": {
            "version": meta.get("version"),
            "built_at": meta.get("built_at"),
            "trade_date": meta.get("trade_date"),
            "row_count": meta.get("row_count"),
        },
        "caliber": (
            "条件 AND 连接；财务因子缺值的行不满足该条件（不以 0 冒充）；"
            "银行/保险/证券对「资产负债率」自动豁免并在行内标注；"
            "ROE 连续 3 年取最近三个年报的最弱一年，任一年缺值视为不满足。"
            "结果只按代码排序，不做任何推荐排序。"
        ),
        "as_of": _now_text(),
    }


# =============================================================
# 第七轮 M13：全市场候选池（复用 M9 快照 + 基金友好模板）
# =============================================================
# 模板条件（全部 AND）：ROE 三年最弱 ≥10 / 资产负债率 ≤60 / PE ≤40 / 毛利率 ≥30 / ROE 逐年不降
CANDIDATE_TEMPLATE = {
    "key": "fund_friendly",
    "label": "基金友好",
    "rules": [
        {"factor": "roe_min", "label": "ROE（近 3 年最弱）", "op": "gte", "value": 10, "note": "近 3 年年报最弱一年 ≥10%"},
        {"factor": "debt_ratio", "label": "资产负债率", "op": "lte", "value": 60, "note": "金融业自动豁免并标注"},
        {"factor": "pe", "label": "PE-TTM", "op": "lte", "value": 40, "note": "PE-TTM ≤40"},
        {"factor": "gross", "label": "销售毛利率", "op": "gte", "value": 30, "note": "毛利率 ≥30%"},
        {"factor": "roe_trend", "label": "ROE 趋势", "op": "gte", "value": 0, "note": "ROE 逐年不下滑（新 ≥ 旧）"},
        # 第七轮 M13 补齐：股息率作为模板规则之一（对已通过前四条的候选逐只按需拉取，
        # 不扫全市场；分红数据缺失的标的如实标 None 并排除，不冒充）
        {"factor": "dividend_yield", "label": "股息率", "op": "gte", "value": 1.0,
         "note": "近一年派息/收盘 ≥1.0%（分红数据无 SLA，缺失则不满足）"},
    ],
}


# 股息率逐只拉取的内存缓存（TTL 由调用方控制；进程内去重避免重复请求）
_DIVIDEND_CACHE: Dict[str, Tuple[float, Optional[float]]] = {}


# 全市场分红映射缓存：(时间戳, {code: 每10股派息元})
_DIVIDEND_MAP: Dict[str, Tuple[float, Dict[str, float]]] = {}
_DIVIDEND_MAP_TTL = 86400  # 分红方案按年披露，缓存 24 小时


def _load_dividend_map() -> Dict[str, float]:
    """一次性拉取全市场最近一个年度分红方案（东财 datacenter，纯 HTTP）。

    2026-11-02 第八轮：原实现逐只调用 ``stock_dividend_cninfo``（巨潮，经 V8 签名），
    冻结形态不可用且 5577 只逐个拉取不可行。改用东财 datacenter 的
    ``RPT_SHAREBONUS_DET``（分红送配，免鉴权、纯 HTTP），按最近两个年度报告期
    一次性分页取回，得到 {代码: 每 10 股派息(元)}；同一代码取最新报告期。

    Returns:
        Dict[str, float]: 代码 → 每 10 股派息（元）；通道不可用时返回空字典（不抛）。
    """
    cached = _DIVIDEND_MAP.get("data")
    if cached and time.time() - cached[0] < _DIVIDEND_MAP_TTL:
        return cached[1]
    mapping: Dict[str, float] = {}
    current_year = utils.bj_now().year
    for report_year in (current_year - 1, current_year - 2):
        page = 1
        while page <= 12:
            payload = _datacenter_page(
                "RPT_SHAREBONUS_DET",
                {
                    "sortColumns": "REPORT_DATE",
                    "sortTypes": "-1",
                    "pageSize": 500,
                    "pageNumber": page,
                    "filter": f"(REPORT_DATE='{report_year}-12-31')",
                },
            )
            rows = ((payload.get("result") or {}).get("data") or [])
            if not rows:
                break
            for row in rows:
                code = str(row.get("SECURITY_CODE") or "").strip()
                per_10 = utils.to_float(row.get("PRETAX_BONUS_RMB"))
                if not code or per_10 is None or per_10 <= 0:
                    continue
                # 最新报告期优先：先写入的年份更新，后面的年份不覆盖
                mapping.setdefault(code, per_10)
            if len(rows) < 500:
                break
            page += 1
    if mapping:
        _DIVIDEND_MAP["data"] = (time.time(), mapping)
        logger.info("全市场分红映射已更新：%d 只", len(mapping))
    return mapping


def _datacenter_page(report_name: str, extra: Dict[str, Any]) -> Dict[str, Any]:
    """请求东财 datacenter 报表一页（复用统一会话与异常转译）。

    Args:
        report_name: 报表名（如 ``RPT_SHAREBONUS_DET``）。
        extra: 额外查询参数（分页/排序/过滤）。

    Returns:
        Dict[str, Any]: 解析后的 JSON；失败返回空字典。
    """
    import json

    from backend.app.core import netfix

    params = {"reportName": report_name, "columns": "ALL", "source": "WEB", "client": "WEB"}
    params.update(extra)
    session = netfix.make_session()
    try:
        response = session.get(
            config.EASTMONEY_DATACENTER_URL,
            params=params,
            headers={
                "User-Agent": config.BROWSER_USER_AGENT,
                "Referer": config.EASTMONEY_REFERER,
            },
            timeout=config.DATASOURCE_TIMEOUT,
        )
        response.raise_for_status()
        return json.loads(response.text) or {}
    except Exception as exc:  # noqa: BLE001 - 分红数据缺失不影响筛选主流程
        logger.info("东财分红报表请求失败：%s", type(exc).__name__)
        return {}
    finally:
        session.close()


def _dividend_yield(code: str, close: Optional[float]) -> Optional[float]:
    """取某只 A 股的近一年股息率（每 10 股派息 / 10 / 收盘价 × 100）。

    数据源：东财 datacenter ``RPT_SHAREBONUS_DET``（分红送配，纯 HTTP 免鉴权），
    全市场映射一次性加载后按代码查表（缓存 24 小时）。缺值返回 None，不冒充。

    Args:
        code: 6 位 A 股代码。
        close: 快照收盘价（元）。

    Returns:
        Optional[float]: 股息率（%）；不可得时 None。
    """
    if not close or close <= 0:
        return None
    mapping = _load_dividend_map()
    per_10 = mapping.get(str(code))
    if per_10 is None:
        return None
    return round((per_10 / 10) / close * 100, 2)


def _dividend_yield_batch(
    items: List[Dict[str, Any]],
    *,
    max_workers: int = 6,
) -> Dict[str, Optional[float]]:
    """对一批候选逐只并发拉股息率，返回 {code: yield}。

    已经内存缓存的代码直接复用，避免重复请求。

    Args:
        items: 候选行（含 code / close）。
        max_workers: 并发数。

    Returns:
        Dict[str, Optional[float]]: code → 股息率（%）。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    now = time.time()
    result: Dict[str, Optional[float]] = {}
    todo: List[Dict[str, Any]] = []
    for item in items:
        code = str(item.get("code") or "")
        if not code:
            continue
        cached = _DIVIDEND_CACHE.get(code)
        if cached and cached[0] > now:
            result[code] = cached[1]
        else:
            todo.append(item)
    if not todo:
        return result

    def _work(item: Dict[str, Any]) -> Tuple[str, Optional[float]]:
        """单只候选的股息率计算（并发任务体，异常在调用方兜底）。

        Args:
            item: 候选行（含 code / close）。

        Returns:
            Tuple[str, Optional[float]]: (代码, 股息率%)。
        """
        code = str(item.get("code") or "")
        value = _dividend_yield(code, utils.to_float(item.get("close")))
        _DIVIDEND_CACHE[code] = (now + 1800, value)  # 30 分钟进程内缓存
        return code, value

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 6))) as pool:
        futures = {pool.submit(_work, item): item for item in todo}
        for future in as_completed(futures):
            try:
                code, value = future.result()
                result[code] = value
            except Exception as exc:  # noqa: BLE001 - 单只失败不阻断
                logger.info("股息率并发拉取异常：%s", type(exc).__name__)
    return result

CANDIDATE_MISSING = (
    "股息率（M13 补齐）：快照不含此字段，对已通过其余四条规则的候选逐只按需拉取"
    "「分红记录 cninfo」取近一年派息比例/收盘价计算，不扫全市场 5577 只；"
    "分红数据无 SLA，缺失时如实标 None 不冒充"
)


def run_candidates(
    *,
    limit: int = 200,
    market: str = "a",
) -> Dict[str, Any]:
    """全市场候选池（第七轮 M13，复用 M9 快照）。

    只按模板条件筛选，**不排序打分、不给推荐语**（排序与打分交给 M5 评估器）。

    Args:
        limit: 返回上限（1–500）。
        market: 市场（目前仅 a）。

    Returns:
        Dict[str, Any]: {ready, total_matched, items, template, missing, caliber, as_of}

    Raises:
        RuntimeError: 快照未建库。
    """
    if str(market or "a").lower() != "a":
        raise ValueError("候选池目前仅支持 A 股（快照为 A 股全市场）")
    limit = max(1, min(int(limit or 200), 500))

    payload = _read_snapshot()
    rows = payload.get("rows") or []
    if not rows:
        raise RuntimeError("快照未建库：请先到「筛选器」页点击「立即刷新快照」构建")

    matched: List[Dict[str, Any]] = []
    exempt_count = 0
    # M13：股息率快照不含，需逐只按需拉取 → 先按快照可得的规则筛出候选，再对候选补拉股息率
    on_demand_rules = [r for r in CANDIDATE_TEMPLATE["rules"] if r["factor"] == "dividend_yield"]
    snapshot_rules = [r for r in CANDIDATE_TEMPLATE["rules"] if r["factor"] != "dividend_yield"]

    for row in rows:
        keep = True
        exempted: List[str] = []
        for rule in snapshot_rules:
            factor = rule["factor"]
            value = float(rule["value"])
            if factor == "roe_trend":
                series = row.get("roe_years") or []
                if not series or any(v is None for v in series):
                    keep = False
                    break
                # 新→旧：逐年不降（最新年报 ≥ 前一年）
                if not all(series[i] >= series[i + 1] for i in range(len(series) - 1)):
                    keep = False
                    break
                continue
            meta = FACTOR_META.get(factor, {})
            if meta.get("exempt_fin") and any(
                mark in str(row.get("board") or "") for mark in FINANCIAL_BOARD_MARKS
            ):
                exempted.append(factor)
                continue
            actual = _row_value(row, factor)
            if actual is None:
                keep = False
                break
            if rule["op"] == "gte" and not actual >= value:
                keep = False
                break
            if rule["op"] == "lte" and not actual <= value:
                keep = False
                break
        if not keep:
            continue
        if exempted:
            exempt_count += 1
        series = row.get("roe_years") or []
        matched.append({
            "code": row.get("code"),
            "name": row.get("name"),
            "board": row.get("board"),
            "close": row.get("close"),
            "chg": row.get("chg"),
            "pe": row.get("pe"),
            "pb": row.get("pb"),
            "mktcap": (row.get("mktcap") / 1e8) if row.get("mktcap") is not None else None,
            "roe3y_min": _row_value(row, "roe_min"),
            "roe_years": series,
            "gross": row.get("gross"),
            "debt_ratio": row.get("debt_ratio"),
            "exempt": exempted,
            "dividend_yield": None,  # 第二轮补齐（M13）
        })
        if len(matched) >= limit * 2:  # 多取一些，防止股息率筛选后不够
            break

    # ── 第二轮：对候选逐只按需拉股息率（并发），并应用股息率规则 ──
    if on_demand_rules:
        yields = _dividend_yield_batch(matched)
        final: List[Dict[str, Any]] = []
        for item in matched:
            code = str(item.get("code") or "")
            value = yields.get(code)
            item["dividend_yield"] = value
            rule = on_demand_rules[0]
            if value is None:
                continue  # 分红数据缺失，如实排除（不冒充满足）
            if rule["op"] == "gte" and not value >= float(rule["value"]):
                continue
            final.append(item)
        matched = final

    matched.sort(key=lambda item: item["code"])
    matched = matched[:limit]
    meta = get_snapshot_meta()
    return {
        "ready": True,
        "total_matched": len(matched),
        "returned": len(matched),
        "truncated": len(matched) >= limit,
        "items": matched,
        "template": CANDIDATE_TEMPLATE,
        "exempt_count": exempt_count,
        "missing": [CANDIDATE_MISSING],
        "snapshot": {
            "version": meta.get("version"),
            "built_at": meta.get("built_at"),
            "trade_date": meta.get("trade_date"),
            "row_count": meta.get("row_count"),
        },
        "caliber": (
            "候选池= M9 快照按「基金友好」模板筛选（ROE 三年最弱 ≥10 ∧ 资产负债率 ≤60 ∧ PE ≤40 ∧ "
            "毛利率 ≥30 ∧ ROE 逐年不降）；金融业对资产负债率自动豁免并标注；"
            "只筛选不排序、不打分、不给推荐语；排序与打分请在「基金组合 → 候选评估」里做"
        ),
        "as_of": utils.now_text(utils.bj_now()),
    }
