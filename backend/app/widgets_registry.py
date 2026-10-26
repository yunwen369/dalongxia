"""组件注册表（widget_registry）：面板元数据的唯一来源，经 /api/widgets 下发。

对应规范 v7.2 §8.6 与《设计基准说明书_阶段5.1》§1.3 区块编号规则：
- 每个区块登记 id（S-01 / ST-01 / V-01）、页面、标题、网格跨列、绑定接口、可点击跳转；
- 前端按 id 渲染，后端按 id 下发，保证 MD 说明书、HTML 工程与接口三方编号一致；
- 当前注册 9 个页面共 49 个区块：sectors 7 + stock 10 + sector-val 4 + news 5 + fund 6 + portfolio 4 + watchlist 2 + lhb 5 + yjb 6；
  dashboard/home/charting/yjb/connectors/settings 不走本表（仪表板由 RECIPES 渲染，其余为静态页）。
"""

from __future__ import annotations

from typing import Any, Dict, List

# 网格类名 → 48 列跨列数（设计基准 §2.2 冻结映射，P0-03 修复口径）
GRID_SPAN: Dict[str, int] = {
    ".g-3": 12,
    ".g-4": 16,
    ".g-5": 20,
    ".g-6": 24,
    ".g-7": 28,
    ".g-8": 32,
    ".g-12": 48,
}


def _widget(
    widget_id: str,
    page: str,
    title: str,
    grid: str,
    api: List[str],
    *,
    jump: str = "",
    desc: str = "",
) -> Dict[str, Any]:
    """构造单个组件元数据。

    Args:
        widget_id: 区块编号（S-xx / ST-xx / V-xx），必须与 HTML 区块 id 一致。
        page: 所属页面路由。
        title: 中文标题。
        grid: 网格类名（.g-7 等）。
        api: 绑定的接口路径列表。
        jump: 跳转目标路由（无跳转留空）。
        desc: 口径说明。

    Returns:
        dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。
    """
    return {
        "id": widget_id,
        "page": page,
        "title": title,
        "grid": grid,
        "span": GRID_SPAN.get(grid, 48),
        "api": api,
        "jump": jump,
        "desc": desc,
    }


WIDGETS: List[Dict[str, Any]] = [
    # ---------- #/sectors（7 个） ----------
    _widget("S-01", "#/sectors", "市场切换（国内/港股/美股）", ".g-12", [],
            desc="可点切换，切换后本页全部区块真实重渲染"),
    _widget("S-02", "#/sectors", "行业/概念切换", ".g-12", [],
            desc="切换排行表口径，热力图不强制跟随"),
    _widget("S-03", "#/sectors", "板块热力图", ".g-7", ["/api/sector/heatmap"],
            jump="#/sector-val?board={板块名}&market={市场}",
            desc="按涨跌幅着色与定面积，负值板块同样可见"),
    _widget("S-04", "#/sectors", "行业板块排行", ".g-5", ["/api/sector/industry"],
            jump="#/sector-val?board={板块名}&market={市场}", desc="sticky 表头，整行可点"),
    _widget("S-05", "#/sectors", "概念板块排行", ".g-5", ["/api/sector/concept"],
            jump="#/sector-val?board={板块名}&market={市场}", desc="与 S-04 同位切换"),
    _widget("S-06", "#/sectors", "板块摘要", ".g-5", ["/api/sector/summary"],
            desc="领涨/领跌/净流入 Top3 + 成交额集中度"),
    _widget("S-07", "#/sectors", "板块资金流向", ".g-12", ["/api/sector/fundflow"],
            desc="主力净流入/流入/流出（亿元）；超大单/大单源不提供，显示无数据"),

    # ---------- #/stock（10 个） ----------
    _widget("ST-01", "#/stock", "报价大卡", ".g-12", ["/api/stock/realtime-panel"],
            desc="代码/名称/最新价/涨跌/开高低昨收/换手/市值/PE + 双徽章"),
    _widget("ST-02", "#/stock", "K 线图", ".g-7", ["/api/stock/realtime-panel"],
            desc="蜡烛+成交量副图，日K 默认"),
    _widget("ST-03", "#/stock", "分时图", ".g-7", ["/api/stock/realtime-panel"],
            desc="上下午两段，午休断裂不连线"),
    _widget("ST-04", "#/stock", "资金分布", ".g-7", ["/api/stock/realtime-panel"],
            desc="超大单/大单/中单/小单四档环形图（新浪）"),
    _widget("ST-05", "#/stock", "相关新闻", ".g-7", ["/api/stock/realtime-panel"],
            jump="#/news?symbol={代码}", desc="标题可点，新闻页 5.2 落地前给轻提示"),
    _widget("ST-06", "#/stock", "机构预测", ".g-5", ["/api/stock/realtime-panel"],
            desc="目标价区间/均值、评级分布、机构家数（同花顺）"),
    _widget("ST-07", "#/stock", "舆情面板", ".g-5", ["/api/stock/realtime-panel"],
            desc="本地词典利好/利空/中性标签"),
    _widget("ST-08", "#/stock", "公司概况", ".g-12", ["/api/stock/profile"],
            desc="基本信息/股本市值/主营，四组卡片"),
    _widget("ST-09", "#/stock", "财务页签区", ".g-12",
            ["/api/stock/financial-summary", "/api/stock/financial-trend",
             "/api/stock/financial-indicator", "/api/stock/financial-report"],
            desc="关键指标/趋势四序列/三大报表/指标明细"),
    _widget("ST-10", "#/stock", "十大股东", ".g-12", ["/api/stock/holders"],
            desc="股东名/持股比例/本期变动"),

    # ---------- #/sector-val（4 个） ----------
    _widget("V-01", "#/sector-val", "板块 K 线", ".g-7", ["/api/sector/kline"],
            desc="板块指数日线，十字光标"),
    _widget("V-02", "#/sector-val", "板块估值指标", ".g-7", ["/api/sector/kline"],
            desc="近一年价格分位/涨跌幅/成交额/领涨领跌"),
    _widget("V-03", "#/sector-val", "成分股表", ".g-7", ["/api/sector/cons"],
            jump="#/stock?symbol={代码}&market=a", desc="整行可点，未命中显示空态"),
    _widget("V-04", "#/sector-val", "板块精准资讯", ".g-5", [],
            desc="AI 初评 5.2 阶段落地，本批占位+参数预留"),

    # ---------- #/news（4 个，批次 5.2） ----------
    _widget("N-01", "#/news", "三级板块树", ".g-4", ["/api/news/board-tree"],
            desc="一级 6 / 二级 15 / 末级 28，末级可选中过滤资讯"),
    _widget("N-02", "#/news", "资讯流", ".g-8",
            ["/api/news/ai-feed", "/api/news/sector-search", "/api/news/stock"],
            desc="多源合并去重 + 板块命中 + 本地确定性初评；标题可点开原文"),
    _widget("N-03", "#/news", "情绪温度", ".g-4", ["/api/sentiment/board", "/api/sentiment/stock"],
            desc="温度=50+均分×500，裁剪 0–100；含驱动事件与证据样本"),
    _widget("N-04", "#/news", "AI 复核", ".g-8", ["/api/news/ai-review"],
            desc="用户点击后调用自备 OpenAI 兼容接口；未配置时给出设置引导"),
    _widget("N-05", "#/news", "财报/公告搜索入口", ".g-12", ["/api/news/financial-search"],
            jump="#/news?fs=1", desc="进入唯一实现页；A股巨潮→东财、港股HKEX、美股SEC EDGAR"),

    # ---------- #/fund（6 个，批次 5.3） ----------
    _widget("F-01", "#/fund", "基金概览", ".g-12", ["/api/fund/info", "/api/fund/realtime"],
            desc="名称/净值/日涨幅/经理/规模 + 双徽章（T+1 公布净值）"),
    _widget("F-02", "#/fund", "净值与回撤", ".g-7", ["/api/fund/nav", "/api/fund/drawdown"],
            desc="净值走势 + 回撤序列，回撤由净值自算"),
    _widget("F-03", "#/fund", "阶段业绩", ".g-7", ["/api/fund/performance"],
            desc="区间涨幅与同类排名（蛋卷 derived）"),
    _widget("F-04", "#/fund", "重仓股", ".g-12", ["/api/fund/holding"],
            jump="#/stock?symbol={代码}&market=a", desc="东财季报前十大重仓，行可点进个股"),
    _widget("F-05", "#/fund", "档案与分红", ".g-5", ["/api/fund/info", "/api/fund/dividend"],
            desc="蛋卷档案 + 东财分红（每10份派现）"),
    _widget("F-06", "#/fund", "持仓分布", ".g-5", ["/api/fund/position"],
            desc="蛋卷持仓分布需登录（rc=300001），依规范禁用并说明"),

    # ---------- #/portfolio（4 个，批次 5.3） ----------
    _widget("P-01", "#/portfolio", "持仓汇总", ".g-12", ["/api/portfolio/analysis"],
            desc="人民币市值/成本/盈亏/汇率；缺汇率时不给折算值"),
    _widget("P-02", "#/portfolio", "新增持仓表单", ".g-12", ["/api/portfolio"],
            desc="市场/代码/名称/份额/成本；写本机 data/portfolio.json"),
    _widget("P-03", "#/portfolio", "持仓明细", ".g-12", ["/api/portfolio", "/api/portfolio/analysis"],
            jump="#/stock?symbol={代码}&market={市场}", desc="本币与人民币双口径盈亏，支持删除"),
    _widget("P-04", "#/portfolio", "市场分布", ".g-5", ["/api/portfolio/analysis"],
            desc="按人民币市值占比的四市场分布"),

    # ---------- #/watchlist（2 个） ----------
    _widget("W-01", "#/watchlist", "自选股概览", ".g-12", ["/api/watchlist"],
            desc="自选清单条数/涨跌分布/四市场占比"),
    _widget("W-02", "#/watchlist", "自选股行情", ".g-12", ["/api/market/quote"],
            desc="代码/名称/现价/涨跌/涨幅/成交额/PE，单行失败隔离"),

    # ---------- #/lhb（5 个） ----------
    _widget("L-01", "#/lhb", "北向资金净流入（近20日）", ".g-7", ["/api/moneyflow/northbound"],
            desc="每日收盘净买额（亿元），交易所自 2024-08-16 起停止披露"),
    _widget("L-02", "#/lhb", "北向净买入 Top10", ".g-5", ["/api/moneyflow/north-top"],
            desc="2024-08-19 起改季度披露，当日无数据时空态+说明，不补零"),
    _widget("L-03", "#/lhb", "龙虎榜明细", ".g-12", ["/api/moneyflow/lhb"],
            desc="当日上榜个股净买额/买入额/卖出额（亿元），按上榜日取最新"),
    _widget("L-04", "#/lhb", "机构席位统计", ".g-7", ["/api/moneyflow/lhb-institution"],
            desc="东财 stock_lhb_jgmmtj_em：买方/卖方机构数、机构净买额与占比"),
    _widget("L-05", "#/lhb", "活跃营业部", ".g-5", ["/api/moneyflow/lhb-broker"],
            desc="东财 stock_lhb_hyyyb_em：营业部买卖金额与上榜个股数"),

    # ---------- #/yjb（6 个） ----------
    _widget("Y-01", "#/yjb", "养基宝授权告知书", ".g-12", ["/api/fund/yjb/consent"],
            desc="首次使用必须显式同意；拒绝则不发起任何上游请求"),
    _widget("Y-02", "#/yjb", "绑定状态", ".g-12", ["/api/fund/yjb/status"],
            desc="是否已绑定/签名盐是否就绪/通道是否可用（不显示授权凭据明文）"),
    _widget("Y-03", "#/yjb", "扫码绑定", ".g-12", ["/api/fund/yjb/qrcode", "/api/fund/yjb/poll"],
            desc="二维码 2 秒轮询、最长 240 秒；成功后以 DPAPI 加密落盘授权凭据"),
    _widget("Y-04", "#/yjb", "实时估值", ".g-12", ["/api/fund/yjb/realtime"],
            desc="持仓基金估算涨跌幅（非官方·拟合估算·仅供参考）"),
    _widget("Y-05", "#/yjb", "养基宝账户概览", ".g-12", ["/api/fund/yjb/collect"],
            desc="总资产/今日估算收益/累计持有收益 + 各账户明细（需绑定养基宝）"),
    _widget("Y-06", "#/yjb", "养基宝持仓与估值", ".g-12", ["/api/fund/yjb/holdings"],
            desc="持仓市值/持有收益/估算涨跌幅，支持按账户切换（非官方·拟合估算）"),
]


def all_widgets() -> List[Dict[str, Any]]:
    """返回全部组件元数据（按区块编号顺序）。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。
    """
    return WIDGETS


def by_page(page: str) -> List[Dict[str, Any]]:
    """按页面筛选组件元数据。

    Args:
        page: 页面标识（视图 id，如 home/sectors/news）。

    Returns:
        list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。
    """
    return [item for item in WIDGETS if item["page"] == page]
