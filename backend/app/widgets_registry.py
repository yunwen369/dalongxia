"""组件注册表（widget_registry）：面板元数据的唯一来源，经 /api/widgets 下发。

对应规范 v7.2 §8.6 与《设计基准说明书_阶段5.1》§1.3 区块编号规则：
- 每个区块登记 id（S-01 / ST-01 / V-01）、页面、标题、网格跨列、绑定接口、可点击跳转；
- 前端按 id 渲染，后端按 id 下发，保证 MD 说明书、HTML 工程与接口三方编号一致；
- 本批只注册阶段 5.1 的 3 个页面（sectors 7 + stock 10 + sector-val 4 = 21 个区块）。
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
            desc="面积=abs(涨跌幅)，颜色与标签用原值，负值不丢面积"),
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
]


def all_widgets() -> List[Dict[str, Any]]:
    """返回全部组件元数据（按区块编号顺序）。"""
    return WIDGETS


def by_page(page: str) -> List[Dict[str, Any]]:
    """按页面筛选组件元数据。"""
    return [item for item in WIDGETS if item["page"] == page]
