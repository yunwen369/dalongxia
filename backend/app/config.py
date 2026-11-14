"""全局配置唯一来源。

本模块集中管理路径、服务端口、超时、缓存 TTL、数据源开关、配色常量与
AI 密钥读取逻辑，是全站唯一权威配置来源（规范 v7.0 §5.6 / §10.11）。

安全约束：
1. 本文件不得出现任何明文密钥、Token 或密码；
2. AI Key 的读取优先级为「进程环境变量 > 项目 .env > data/ai_config.json」；
3. 对外暴露配置时必须调用 get_public_ai_config()，密钥仅返回掩码；
4. 任何日志都不得打印 api_key 原文。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# =============================================================
# 一、应用身份与服务参数（规范 §5.4 / §5.6）
# =============================================================
APP_NAME = "大龙虾投研工作台"  # 应用中文名，写入健康检查与窗口标题
APP_VERSION = "3.7.0"  # 语义化版本号：3.7.0 = REQ-20261115-01 看盘体验补齐（大盘云图/指数走势/完整板块K线/中文表头/全表排序/三级板块选择器/行业资金流/快讯原文 + 拥挤度风控名单/板块研判/仪表板CRUD/搜索板块/组件自由拉伸），并修复「渲染后注入被 innerHTML 覆盖」的既存缺陷
BUILD_TAG = "v3.7.0-20261115"  # 构建标识：v{APP_VERSION}-{YYYYMMDD}，写入 /api/health 的 build 字段
# 版本号单一来源（规范 v7.4 §2.11 第 6 条第 2 款）：installer.nsi 的 APPVER/APPVER4/OutFile
# 与 VERSION.txt 一律由 tools/build_and_gate.py 从本文件派生，禁止手工改 NSI 或写死版本；
# tests/test_version_consistency.py 会在门禁中校验四处（config / NSI / VERSION / 状态文档）一致。

# 默认仅监听本机回环（127.0.0.1），不对外网暴露；
# 远程访问（Tailscale）时设 OPENBB_HOST=0.0.0.0 放开。
HOST = os.environ.get("OPENBB_HOST", "127.0.0.1")
# 端口固定 6900（复刻 OpenBB ODP 本地连接器约定）；仅"发版门禁"自动化允许用
# OPENBB_PORT 临时指定另一端口跑打包产物，避免与用户正在运行的实例抢 6900。
# 正常双击启动不受影响（无该环境变量时恒为 6900），非静默换端口。
PORT = int(os.environ.get("OPENBB_PORT") or 6900)
BASE_URL = f"http://{HOST}:{PORT}"  # 监听地址（含 0.0.0.0 形态），仅用于展示
# 本机访问基址：健康检查、默认浏览器打开一律走回环 —— 监听 0.0.0.0 时
# `http://0.0.0.0:6900` 不是合法访问目标（部分系统直接拒绝），必须与监听地址解耦。
LOCAL_URL = f"http://127.0.0.1:{PORT}"

# =============================================================
# 二、目录与文件路径（全部由本文件派生，业务代码禁止另行拼接）
# =============================================================
# 冻结形态判定（PyInstaller onedir）：影响资源定位与运行数据落盘位置（§13.6-7）。
IS_FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = Path(__file__).resolve().parent  # backend/app（源码模式）
if IS_FROZEN:
    # 冻结模式：EXE 同级目录可写（data/logs 放这里），只读资源在 _internal（sys._MEIPASS）
    PROJECT_DIR = Path(sys.executable).resolve().parent
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", str(PROJECT_DIR)))
else:
    PROJECT_DIR = APP_DIR.parents[1]  # openbb-cn 工程根目录
    RESOURCE_DIR = PROJECT_DIR
FRONTEND_DIR = RESOURCE_DIR / "frontend"  # 前端静态资源目录（冻结时在 _internal/frontend）
DATA_DIR = PROJECT_DIR / "data"  # 运行时数据目录（冻结时 = EXE_DIR/data）
CACHE_DIR = DATA_DIR / "cache"  # 两级缓存的磁盘目录
EXPORT_DIR = DATA_DIR / "exports"  # 导出文件预留目录
LOG_DIR = PROJECT_DIR / "logs"  # 日志目录，按天滚动（冻结时 = EXE_DIR/logs）
AI_CONFIG_FILE = DATA_DIR / "ai_config.json"  # AI 配置落盘文件（含密钥，禁止入库）
YJB_TOKEN_FILE = DATA_DIR / "yjb_token.json"  # 养基宝 Token 落盘文件（禁止入库）
# py_mini_racer(V8) 可用性开关（规范 §13.6-2）。
# 第八轮（2026-11-02）起，板块行情/K线/成分/资金流/拥挤度、财联社快讯、
# 同花顺全球快讯、分红数据已全部改走**纯 HTTP** 通道（ths_http / tencent_board /
# sina_flow / cls / 东财 datacenter），V8 签名不再位于任何业务主链路上，
# 因此该开关**不再用于关闭任何功能**，仅作为诊断信息保留（v8_guard 仍会拦截
# 遗留的 akshare 同花顺调用，避免冻结环境原生崩溃）。
V8_SOURCES_ENABLED = not IS_FROZEN
ENV_FILE = PROJECT_DIR / ".env"  # 本地环境变量文件（禁止入库）

# =============================================================
# 三、网络与韧性参数（规范 §6.5，取值固定）
# =============================================================
MAX_RETRY = 3  # 本次指令要求每源重试 3 次（规范 §6.5 为 2 次，已在评审记录登记）
REQUEST_TIMEOUT = 20  # 对外 HTTP 默认超时（秒）
DATASOURCE_TIMEOUT = 15  # 单数据源全链路超时（秒）
RETRY_BACKOFF = (1.0, 2.0, 4.0)  # 指数退避间隔（秒）
RATE_LIMIT_MIN_DELAY = 0.3  # 串行调用最小随机延时（秒）
RATE_LIMIT_MAX_DELAY = 1.0  # 串行调用最大随机延时（秒）
CIRCUIT_COOLDOWN = 300.0  # 传输层失败源的熔断冷却秒数（P1-04，5 分钟后自动重试）
EXISTS_PROBE_TIMEOUT = 5  # 存在性探测超时（秒，P1-03 快速失败，>5s 视为未知）
BROWSER_USER_AGENT = (  # 统一浏览器 UA，降低被源站风控概率
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# 数据源基址（允许环境变量覆盖，便于故障演练与联调；生产默认值不变）
TENCENT_QUOTE_BASE = os.environ.get("OPENBB_TENCENT_QUOTE_BASE", "https://qt.gtimg.cn")
# 实测（2026-11-02）：ifzq.gtimg.cn 对程序化请求返回 501（跳转校验页），
# web.ifzq.gtimg.cn 与 proxy.finance.qq.com/ifzqgtimg 正常，故基址改用前者。
TENCENT_KLINE_BASE = os.environ.get(
    "OPENBB_TENCENT_KLINE_BASE", "https://web.ifzq.gtimg.cn"
)
TENCENT_SEARCH_BASE = os.environ.get(
    "OPENBB_TENCENT_SEARCH_BASE", "https://smartbox.gtimg.cn"
)
SINA_QUOTE_BASE = os.environ.get("OPENBB_SINA_QUOTE_BASE", "https://hq.sinajs.cn")

# 其余直连接口统一收口在此（P2-08：禁止在 provider 内硬编码 URL）
# 东财 datacenter（F10 概况/股东，与 push2 行情通道分离，本机可用）
EASTMONEY_DATACENTER_URL = os.environ.get(
    "OPENBB_EASTMONEY_DATACENTER_URL",
    "https://datacenter-web.eastmoney.com/api/data/v1/get",
)
# 东财基金历史净值（pageSize 硬上限 20）
EASTMONEY_FUND_LSJZ_URL = os.environ.get(
    "OPENBB_EASTMONEY_FUND_LSJZ_URL",
    "https://api.fund.eastmoney.com/f10/lsjz",
)
# 东财基金分红页（HTML 表格，无记录时页面含「暂无分红信息!」）
EASTMONEY_FUND_FHSP_URL = os.environ.get(
    "OPENBB_EASTMONEY_FUND_FHSP_URL",
    "https://fundf10.eastmoney.com/fhsp_{code}.html",
)
EASTMONEY_FUND_FHSP_REFERER = os.environ.get(
    "OPENBB_EASTMONEY_FUND_FHSP_REFERER",
    "https://fundf10.eastmoney.com/",
)
# 新浪外汇（美元兑人民币 fx_susdcny、港币兑人民币 fx_shkdcny）
SINA_FX_URL = os.environ.get(
    "OPENBB_SINA_FX_URL",
    "https://hq.sinajs.cn/list=fx_susdcny,fx_shkdcny",
)

# ── 纯 HTTP 板块通道（2026-11-02 第八轮）──────────────────────────
# 背景：同花顺板块数据原经 akshare + py_mini_racer(V8) 签名，冻结形态被禁用后
#   板块行情/K线/资金流/拥挤度全部降级。实测同花顺「行情中心」与「行情数据」两个
#   纯 HTTP 域名无需 hexin-v 签名即可取数，故改由下列直连通道承接，彻底移除 V8 依赖。
# 同花顺行情中心（行业列表、行业成分、板块详情，GBK 编码）
THS_QUOTE_BASE = os.environ.get("OPENBB_THS_QUOTE_BASE", "http://q.10jqka.com.cn")
# 同花顺行情数据（板块日/周/月 K 线、板块实时快照，JSONP）
THS_LINE_BASE = os.environ.get("OPENBB_THS_LINE_BASE", "https://d.10jqka.com.cn")
THS_REFERER = "http://q.10jqka.com.cn/"
# 腾讯板块行情（hy2=申万二级行业 124 个、gn=概念 805 个，含主力净流入与领涨股）
TENCENT_BOARD_URL = os.environ.get(
    "OPENBB_TENCENT_BOARD_URL",
    "https://proxy.finance.qq.com/cgi/cgi-bin/rank/pt/getRank",
)
# 新浪板块资金流（行业/概念，净额/流入/流出，单位=元）
SINA_BOARD_FLOW_URL = os.environ.get(
    "OPENBB_SINA_BOARD_FLOW_URL",
    "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_bkzj_bk",
)
# 新浪个股四档资金流（支持 daima=a,b,c 批量；r0=超大单 r1=大单 r2=中单 r3=小单）
SINA_STOCK_FLOW_URL = os.environ.get(
    "OPENBB_SINA_STOCK_FLOW_URL",
    "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "MoneyFlow.ssi_ssfx_flzjtj",
)
# 新浪美股分时（type=1 分钟 / 5 五分钟；返回 d/o/h/l/c/v/a）
SINA_US_MINUTE_URL = os.environ.get(
    "OPENBB_SINA_US_MINUTE_URL",
    "https://stock.finance.sina.com.cn/usstock/api/json_v2.php/US_MinKService.getMinK",
)
# 财联社电报（md5(sha1(sorted_params)) 签名，纯 HTTP，无需 V8）
CLS_ROLL_URL = os.environ.get(
    "OPENBB_CLS_ROLL_URL",
    "https://www.cls.cn/v1/roll/get_roll_list",
)
# 天天基金 F10 行业配置页（HTML 表格，无记录时页面含「暂无数据」）
EASTMONEY_FUND_HYTZ_URL = os.environ.get(
    "OPENBB_EASTMONEY_FUND_HYTZ_URL",
    "https://fundf10.eastmoney.com/hytz_{code}.html",
)

# 站点 Referer（部分源站缺 Referer 会返回 403）
TENCENT_REFERER = "https://gu.qq.com/"
SINA_REFERER = "https://finance.sina.com.cn/"
EASTMONEY_REFERER = "https://data.eastmoney.com/"
CNINFO_REFERER = "http://www.cninfo.com.cn/"

# 数据源开关（环境变量仅用于故障演练；默认全部启用）
PROVIDER_SWITCHES: Dict[str, bool] = {
    "tencent": os.environ.get("OPENBB_DISABLE_TENCENT") != "1",
    "sina": os.environ.get("OPENBB_DISABLE_SINA") != "1",
    "eastmoney": os.environ.get("OPENBB_DISABLE_EASTMONEY") != "1",
    "cninfo": os.environ.get("OPENBB_DISABLE_CNINFO") != "1",
    # 东财 F10 datacenter 与 push2 行情通道分离，避免误伤概况接口
    "em_f10": os.environ.get("OPENBB_DISABLE_EM_F10") != "1",
}

# 指数基线：code 为北向统一代码，tencent 为腾讯请求代码
MARKET_INDEXES: List[Dict[str, str]] = [
    {"code": "000001", "name": "上证指数", "market": "a", "tencent": "sh000001"},
    {"code": "399001", "name": "深证成指", "market": "a", "tencent": "sz399001"},
    {"code": "399006", "name": "创业板指", "market": "a", "tencent": "sz399006"},
    {"code": "000300", "name": "沪深300", "market": "a", "tencent": "sh000300"},
    {"code": "HSI", "name": "恒生指数", "market": "hk", "tencent": "hkHSI"},
    {"code": "HSTECH", "name": "恒生科技指数", "market": "hk", "tencent": "hkHSTECH"},
    {"code": "DJI", "name": "道琼斯", "market": "us", "tencent": "usDJI"},
    {"code": "IXIC", "name": "纳斯达克", "market": "us", "tencent": "usIXIC"},
    {"code": "INX", "name": "标普500", "market": "us", "tencent": "usINX"},
]

# 指数代码别名：前端/旧接口可能只传 000001、SPX 等
INDEX_CODE_ALIASES: Dict[str, str] = {
    "000001": "000001",
    "399001": "399001",
    "399006": "399006",
    "000300": "000300",
    "SH000001": "000001",
    "SZ399001": "399001",
    "SZ399006": "399006",
    "SH000300": "000300",
    "HSI": "HSI",
    "HSTECH": "HSTECH",
    "DJI": "DJI",
    "IXIC": "IXIC",
    "INX": "INX",
    "SPX": "INX",
}

# 周期与复权枚举：内部统一值 → 腾讯/akshare 参数
PERIOD_MAP: Dict[str, str] = {
    "daily": "day",
    "weekly": "week",
    "monthly": "month",
    "日k": "day",
    "周k": "week",
    "月k": "month",
}
ADJUST_VALUES = ("", "qfq", "hfq")

# =============================================================
# 四、缓存 TTL 表（单位：秒，规范 §6.8）
# =============================================================
TTL: Dict[str, int] = {
    "realtime_quote": 15,  # 实时报价 / 大盘指数快照
    "index_quote": 15,  # 指数行情快照
    "kline": 60,  # 历史 K 线（盘中：保实时性）
    # v3.6.1（D-2 修复）：非盘中/休市时段，日 K 的最后一根在收盘后不再变化，
    # 仍按 60s 缓存会让每次打开软件都重走一遍上游（4 个指数迷你走势串行 ≈ 8–10s）。
    # 由 datasource.utils.kline_cache_ttl() 按交易时段分流取值（盘后/周末 900s）。
    "kline_after_hours": 900,  # 历史 K 线（非盘中/休市）
    "minute": 30,  # 分时数据
    "sector": 30,  # 板块行情
    "fund_flow": 60,  # 资金流向
    "news_flash": 60,  # 新闻滚动快讯
    "sentiment": 300,  # 本地利好利空分析结果
    "fund_nav": 300,  # 基金净值
    "fund_holding": 3600,  # 基金持仓（季报口径）
    "profile": 3600,  # 公司概况
    "estimates": 1800,  # 机构一致预期（机构覆盖聚合，日频更新）
    "financial": 3600,  # 财报 / 财务指标
    "search": 600,  # 搜索联想列表
    "yjb_status": 300,  # 养基宝绑定状态
    "fund_performance": 3600,  # 基金阶段业绩 / 分红
    # 板块成分股属低频数据（申万/新浪按季报与月度维护），
    # 原按 kline 的 60 秒缓存导致每分钟都要重走一次 15 秒级冷启动（P1-04）
    "cons": 3600,  # 板块成分股
    "watchlist": 15,  # 自选股行情快照（与单只报价同频）
    "northbound": 3600,  # 北向资金历史（日频）
    "north_top": 3600,  # 北向个股排行（日频）
    "lhb": 3600,  # 龙虎榜（盘后披露）
    # 第七轮（M1/M2）：宏观为月度公布值，估值分位为月频序列 → 都不需要短缓存
    "macro": 86400,  # 宏观指标（月度，24 小时）
    "valuation": 21600,  # 估值分位（月频，6 小时）
    # ---- B2 口径收编（2026-11-12）：进程内存缓存（core/memcache 命名实例）----
    # 此前散落在各 provider 的手写缓存 TTL（600s/12h/24h 各异、口径不可见），
    # 统一登记于此；core/cache.py 的 CACHE_TTL 展示表（S-03）另有概括行。
    "stock_industry": 43200,  # 个股→东财行业归属（跨估值/基金/持仓复用，12h）
    "probe": 600,  # 连接器/数据源通道探测结果（10 分钟，原 capabilities.PROBE_TTL）
    "tx_board_map": 600,  # 腾讯板块名→代码映射（10 分钟）
    "flow_tier": 600,  # 板块四档资金流聚合（10 分钟）
    "fund_fee_table": 86400,  # 基金费率批量表（24 小时）
    "breadth": 60,  # 市场宽度（涨跌家数，1 分钟）
    "page_probe": 86400,  # 页面上下文只读探测（24 小时，原 page_context._PROBE_TTL）
    "ths_board_list": 3600,  # 同花顺行业名清单（1 小时，原 fund_combo._THS_BOARD_TTL）
}

# =============================================================
# 五、市场枚举与数据源开关（健康检查据此报告真实接入状态）
# =============================================================
MARKETS: Dict[str, str] = {
    "a": "A股",
    "us": "美股",
    "hk": "港股",
    "fund": "场外基金",
}

# stage 取值：active=能力可用；degraded=降级可用；unavailable=通道异常；
# optin=待授权启用；missing=依赖缺失；disabled=已禁用
# kind 取值：library=pip 依赖；http=外部 HTTP 通道（轻探测）；v8=V8 依赖源；optin=需用户授权
# 注意：stage/active 仅作兼容字段，真实状态由 core.capabilities 探测后统一产出（P1-05）。
DATA_SOURCES: Dict[str, Dict[str, Any]] = {
    "akshare": {"label": "akshare（东财/新浪等）", "module": "akshare", "enabled": True, "kind": "library", "stage": "active", "active": True},
    "tencent": {"label": "腾讯行情", "module": "requests", "enabled": True, "kind": "library", "stage": "active", "active": True},
    "eastmoney": {"label": "东方财富", "module": "requests", "enabled": True, "kind": "library", "stage": "active", "active": True},
    "sina": {"label": "新浪财经", "module": "requests", "enabled": True, "kind": "library", "stage": "active", "active": True},
    "ths": {"label": "同花顺", "module": None, "enabled": True, "kind": "v8", "stage": "active"},
    "cls": {"label": "财联社", "module": None, "enabled": True, "kind": "http", "stage": "active"},
    "fund": {"label": "天天基金/蛋卷", "module": None, "enabled": True, "kind": "http", "stage": "active"},
    "yangjibao": {"label": "养基宝（非官方）", "module": None, "enabled": False, "kind": "optin", "stage": "optin"},
}

# =============================================================
# 六、全站配色常量（唯一权威来源，前端 app.css 主题变量必须一致，规范 §7.4 / §9.4；
# 2026-11-12 B1：原 theme.css 已随旧前端层归档退役，变量现集中在 app.css）
# =============================================================
COLOR_UP = "#FF4B4B"  # 红涨：涨、正收益
COLOR_DOWN = "#00B061"  # 绿跌：跌、负收益
COLOR_WARN = "#E8A33D"  # 警示色
COLOR_INFO = "#4B9EFF"  # 信息色

# =============================================================
# 七、日志格式（规范 §6.10）
# =============================================================
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


# =============================================================
# 八、AI 配置读取与脱敏（规范 §5.6 / §10.11）
# =============================================================
@dataclass(frozen=True)
class AIConfig:
    """AI 服务配置。

    Attributes:
        base_url: OpenAI 兼容接口基址。
        api_key: 接口密钥，repr=False 确保对象被打印时不会泄露。
        model: 模型名称。
        source: 配置来源，取值 env / dotenv / file / none。
    """

    base_url: str = ""
    api_key: str = field(default="", repr=False)
    model: str = ""
    source: str = "none"

    @property
    def configured(self) -> bool:
        """判断 AI 配置是否完整可用。

        Returns:
            bool: 判定结果。
        """
        return bool(self.base_url and self.api_key and self.model)


def _read_dotenv(path: Path) -> Dict[str, str]:
    """读取项目 .env 文件，返回键值字典。

    Args:
        path: .env 文件路径。

    Returns:
        Dict[str, str]: 解析后的键值对；文件不存在或解析失败时返回空字典。

    Notes:
        仅做最简解析（KEY=VALUE、忽略 # 注释、去除成对引号），
        不引入额外依赖；解析失败不抛出异常，避免阻断服务启动。
    """
    result: Dict[str, str] = {}
    if not path.is_file():
        return result
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key.startswith("export "):
                key = key[len("export "):].strip()
            value = value.strip().strip('"').strip("'")
            if key:
                result[key] = value
    except OSError as exc:
        # 只记录路径与异常类型，绝不记录文件内容（可能含密钥）
        logger.warning("读取 .env 失败：%s（%s）", path, type(exc).__name__)
    return result


def _decrypt_file_key(path: Path, payload: Dict[str, Any]) -> str:
    """从 ai_config.json 取回 API Key：优先 DPAPI 密文，兼容历史明文并就地迁移。

    Args:
        path: AI 配置文件路径。
        payload: 已解析的配置字典。

    Returns:
        str: 明文密钥；未配置或密文不可解（换账户/损坏）时返回空串。

    Notes:
        P1-15：密钥不得明文落盘。历史版本写入的 ``api_key`` 明文，在 DPAPI 可用时
        立即改写为 ``api_key_enc`` 密文（原子替换 + 600 权限），失败仅告警不静默。
    """
    from backend.app.core import secure_store  # 延迟导入，避免 config 早期加载的循环依赖

    encrypted = payload.get("api_key_enc")
    if secure_store.is_encrypted(encrypted):
        plain = secure_store.decrypt_text(str(encrypted))
        if plain is None:
            logger.warning("AI 密钥密文无法解密（可能非本机账户加密），按未配置处理")
            return ""
        return plain

    legacy = str(payload.get("api_key") or "")
    if not legacy:
        return ""
    if not secure_store.available():
        logger.warning("当前环境不支持 DPAPI，历史明文密钥无法迁移，请改用环境变量注入")
        return legacy
    try:
        migrated = {
            "base_url": payload.get("base_url", ""),
            "api_key_enc": secure_store.encrypt_text(legacy),
            "model": payload.get("model", ""),
            "updated_at": payload.get("updated_at", ""),
            # R3-22：格式串统一走 utils.now_text（延迟导入避免 core.atomic/config 循环依赖）
            "encrypted_at": __import__("backend.app.datasource.utils", fromlist=["now_text"]).now_text(),
        }
        # R3-09：统一原子写助手（fsync + 0o600），替代手写 temp + replace + chmod
        # 延迟导入避免与 core.atomic 的启动期循环依赖
        from backend.app.core import atomic

        atomic.atomic_write_json(path, migrated, ensure_ascii=False, indent=2, mode=0o600)
        logger.info("AI 密钥已由明文迁移为 DPAPI 密文（%s）", path.name)
    except OSError as exc:
        logger.warning("AI 密钥明文迁移失败（%s），本次仍按内存可用处理", type(exc).__name__)
    return legacy


def _read_ai_config_file(path: Path) -> Dict[str, Any]:
    """读取 data/ai_config.json，返回字典（密钥已解密）。

    Args:
        path: AI 配置文件路径。

    Returns:
        Dict[str, Any]: 配置字典；文件缺失或格式错误时返回空字典。
    """
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("读取 AI 配置失败：%s（%s）", path, type(exc).__name__)
        return {}
    if not isinstance(payload, dict):
        return {}
    key = _decrypt_file_key(path, payload)
    result = dict(payload)
    result.pop("api_key_enc", None)
    result.pop("api_key", None)
    if key:
        result["api_key"] = key
    return result


def _first_non_empty(*values: Optional[str]) -> str:
    """返回第一个非空字符串，用于多来源优先级取值。

    Returns:
        str: 第一个非空的值；全部为空时返回空串。
    """
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def load_ai_config() -> AIConfig:
    """按优先级加载 AI 配置。

    优先级：进程环境变量 > 项目 .env > data/ai_config.json。
    同时兼容 OPENAI_* 与规范示例中的 AI_* 两套变量名。

    Returns:
        AIConfig: 已脱敏保护的配置对象；未配置时各字段为空、source="none"。
    """
    dotenv = _read_dotenv(ENV_FILE)
    file_config = _read_ai_config_file(AI_CONFIG_FILE)

    base_url = _first_non_empty(
        os.environ.get("OPENAI_BASE_URL"),
        os.environ.get("AI_BASE_URL"),
        dotenv.get("OPENAI_BASE_URL"),
        dotenv.get("AI_BASE_URL"),
        file_config.get("base_url"),
    )
    api_key = _first_non_empty(
        os.environ.get("OPENAI_API_KEY"),
        os.environ.get("AI_API_KEY"),
        dotenv.get("OPENAI_API_KEY"),
        dotenv.get("AI_API_KEY"),
        file_config.get("api_key"),
    )
    model = _first_non_empty(
        os.environ.get("OPENAI_MODEL"),
        os.environ.get("AI_MODEL"),
        dotenv.get("OPENAI_MODEL"),
        dotenv.get("AI_MODEL"),
        file_config.get("model"),
    )

    # 判定来源仅用于界面提示，不涉及密钥内容
    if os.environ.get("OPENAI_API_KEY") or os.environ.get("AI_API_KEY"):
        source = "env"
    elif dotenv.get("OPENAI_API_KEY") or dotenv.get("AI_API_KEY"):
        source = "dotenv"
    elif file_config.get("api_key"):
        source = "file"
    else:
        source = "none"

    return AIConfig(base_url=base_url, api_key=api_key, model=model, source=source)


def mask_secret(secret: str) -> str:
    """对密钥做掩码处理，供界面与健康检查展示。

    Args:
        secret: 原始密钥。

    Returns:
        str: 未配置返回空串；已配置返回首 3 位 + 掩码 + 末 4 位。
    """
    if not secret:
        return ""
    if len(secret) <= 8:
        return "已配置（长度不足，已隐藏）"
    return f"{secret[:3]}****{secret[-4:]}"


def get_public_ai_config() -> Dict[str, Any]:
    """返回可安全暴露给前端的 AI 配置（密钥只给掩码）。

    Returns:
        Dict[str, Any]: 含 configured/base_url/model/api_key/source 的字典。
    """
    config = load_ai_config()
    return {
        "configured": config.configured,
        "base_url": config.base_url,
        "model": config.model,
        "api_key": mask_secret(config.api_key),
        "source": config.source,
    }


def ensure_runtime_dirs() -> None:
    """创建运行时必需目录（幂等，失败时记录日志但不抛出）。"""
    for directory in (DATA_DIR, CACHE_DIR, EXPORT_DIR, LOG_DIR):
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("创建目录失败：%s（%s）", directory, type(exc).__name__)
