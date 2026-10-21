/* =============================================================
 * OpenBB 中文投研工作台 · 国际化与本地化（阶段 6）
 * 职责：
 *   1) I18N：key-value 语言包（默认 zh-CN），覆盖菜单/页面标题/区块标题/
 *      Toast/弹窗/错误/表头/导出文件名；通过 data-i18n 属性批量套用；
 *   2) L10N：数字千分位、A股 2–3 位小数、港股/美股价位、CNY/HKD/USD 符号、
 *      百分比、日期时间（Asia/Shanghai）、CSV 文件名（含 BOM）。
 * 约束：不定义第二套配色/涨跌语义；颜色仍取 theme.css 的 --up/--down。
 * ============================================================= */

(function () {
  "use strict";

  var dict = {
    // ---------- 应用与导航 ----------
    "app.name": "OpenBB 中文投研工作台",
    "app.tagline": "本地投研 · 真实数据",
    "nav.home": "首页",
    "nav.dashboard": "仪表板",
    "nav.charting": "图表",
    "nav.news": "新闻",
    "nav.sectors": "板块",
    "nav.stock": "个股",
    "nav.sectorVal": "估值",
    "nav.fund": "场外基金",
    "nav.portfolio": "持仓",
    "nav.yjb": "养基宝",
    "nav.connectors": "连接器",
    "nav.settings": "设置",

    // ---------- 页面标题 ----------
    "page.home": "首页 · 市场总览",
    "page.dashboard": "仪表板",
    "page.charting": "图表分析",
    "page.news": "新闻资讯",
    "page.sectors": "板块分析",
    "page.stock": "个股详情 · FinScope",
    "page.sector-val": "单板块估值",
    "page.fund": "场外基金",
    "page.portfolio": "持仓管理",
    "page.yjb": "养基宝绑定",
    "page.connectors": "数据连接器",
    "page.settings": "设置",

    // ---------- 通用动作 ----------
    "action.refresh": "刷新",
    "action.reload": "刷新本页",
    "action.retry": "重试",
    "action.view": "查看",
    "action.add": "添加",
    "action.delete": "删除",
    "action.save": "保存",
    "action.cancel": "取消",
    "action.confirm": "确定",
    "action.export": "导出 CSV",
    "action.close": "关闭",
    "action.search": "搜索",
    "action.send": "发送",
    "action.review": "AI 复核",

    // ---------- 状态与空值 ----------
    "state.loading": "加载中…",
    "state.empty": "暂无数据，点击刷新",
    "state.errorTitle": "加载失败",
    "state.errorHint": "请检查网络后点击重试",
    "value.none": "无数据",

    // ---------- 表头（通用） ----------
    "th.code": "代码",
    "th.name": "名称",
    "th.price": "最新价",
    "th.pct": "涨跌幅%",
    "th.amount": "成交额",
    "th.weight": "权重%",
    "th.rank": "排名",
    "th.period": "区间",
    "th.growth": "涨幅%",
    "th.time": "时间",
    "th.source": "来源",
    "th.market": "市场",
    "th.shares": "份额/股数",
    "th.cost": "成本",
    "th.value": "市值",
    "th.pnl": "盈亏",
    "th.pnlPct": "盈亏%",
    "th.action": "操作",
    "th.indicator": "指标",
    "th.item": "项目",
    "th.board": "板块名",
    "th.leadStock": "领涨股",
    "th.leadPct": "领涨幅%",
    "th.netInflow": "主力净流入(亿元)",
    "th.inflow": "流入(亿元)",
    "th.outflow": "流出(亿元)",
    "th.superBig": "超大单(亿元)",
    "th.big": "大单(亿元)",
    "th.capability": "能力",

    // ---------- 仪表板 ----------
    "dashboard.title": "仪表板",
    "dashboard.new": "新建仪表板",
    "dashboard.rename": "重命名",
    "dashboard.remove": "删除仪表板",
    "dashboard.library": "组件库",
    "dashboard.templates": "模板库",
    "dashboard.save": "保存布局",
    "dashboard.saved": "布局已保存",
    "dashboard.empty": "仪表板还是空的，从「组件库」添加组件",
    "dashboard.emptyLibrary": "没有可添加的组件",
    "dashboard.confirmRemove": "确定删除当前仪表板吗？该操作不可撤销。",
    "dashboard.namePrompt": "请输入仪表板名称",
    "dashboard.defaultName": "我的仪表板",
    "dashboard.template.overview": "总览模板",
    "dashboard.template.news": "资讯模板",
    "dashboard.template.portfolio": "持仓模板",
    "dashboard.resize": "调整宽度",
    "dashboard.dragHint": "拖拽标题可排序，点击宽度按钮调整占列",
    "dashboard.removeWidget": "移除组件",

    // ---------- 命令面板 ----------
    "palette.placeholder": "输入视图名、组件名或「清缓存」",
    "palette.views": "切换视图",
    "palette.widgets": "添加组件",
    "palette.actions": "操作",
    "palette.clearCache": "清空缓存",
    "palette.noResult": "无匹配项",
    "palette.hint": "↑↓ 选择 · Enter 执行 · Esc 关闭",

    // ---------- 提示与错误 ----------
    "toast.themeDark": "已切换到深色主题",
    "toast.themeLight": "已切换到浅色主题",
    "toast.cacheCleared": "缓存已清空",
    "toast.cacheFailed": "清空缓存失败，请稍后重试",
    "toast.copied": "已复制",
    "error.network": "网络连接失败，请检查网络后重试。",
    "error.health": "健康检查失败：请确认服务已在 127.0.0.1:6900 启动，然后点击「刷新」重试。",
    "error.unknown": "请求未能完成，请稍后重试。",
    "error.aiNotConfigured": "尚未配置 AI：请到「设置 → AI 设置」填写 base_url、api_key、model。",
    "error.exportEmpty": "当前表格没有可导出的数据",

    // ---------- 图表页 #/charting（阶段 8 补齐） ----------
    "chart.market.a": "A股",
    "chart.market.hk": "港股",
    "chart.market.us": "美股",
    "chart.label.code": "代码",
    "chart.placeholder.symbol": "如 688012 / 00700 / AAPL",
    "chart.period.day": "日K",
    "chart.period.week": "周K",
    "chart.period.month": "月K",
    "chart.adjust.qfq": "前复权",
    "chart.adjust.hfq": "后复权",
    "chart.adjust.none": "不复权",
    "chart.action.query": "查询",
    "chart.draw.off": "画线：关",
    "chart.draw.on": "画线：开（点击主图添加水平线）",
    "chart.action.clearDraw": "清除画线",
    "chart.tab.kline": "K 线与指标",
    "chart.tab.minute": "分时走势",
    "chart.card.quote": "报价概览",
    "chart.card.quoteSub": "红涨绿跌 · 数据源与口径双徽章",
    "chart.card.chart": "价格走势",
    "chart.card.chartSub": "K 线 / 分时 · 指标前端计算",
    "chart.empty.input": "请输入标的代码后查询",
    "chart.empty.quote": "该标的暂无行情数据",
    "chart.empty.kline": "该标的暂无 K 线数据",
    "chart.empty.minute": "该标的暂无分时数据（美股免费通道不可用，见 D3）",
    "chart.meta.high": "最高",
    "chart.meta.low": "最低",
    "chart.meta.open": "今开",
    "chart.meta.prevClose": "昨收",
    "chart.meta.volume": "成交量",
    // 个股一致预期（F-027，分年度机构预测）
    "stock.est.title": "一致预期（分年度）",
    "stock.est.sub": "机构覆盖聚合 · 同花顺盈利预测",
    "stock.est.year": "年度",
    "stock.est.orgs": "机构数",
    "stock.est.epsAvg": "EPS均值(元)",
    "stock.est.epsRange": "EPS区间",
    "stock.est.profitAvg": "净利均值(亿)",
    "stock.est.industry": "行业平均EPS",
    "stock.est.empty": "该标的暂无机构预测覆盖",
    "stock.est.collecting": "暂无一致预期数据，点击刷新",
    "chart.meta.amount": "成交额",
    "chart.series.candle": "K线",
    "chart.series.volume": "成交量",
    "chart.series.price": "价格",
    "chart.series.avg": "均价",
    "chart.series.noon": "午休",
    "chart.draw.added": "已添加参考线：{price}",
    "chart.draw.opened": "画线已开启：点击主图任意位置添加水平参考线",
    "chart.draw.closed": "画线已关闭",
    "chart.draw.cleared": "已清除画线",

    // ---------- 设置页 #/settings（阶段 8 补齐） ----------
    "set.card.ai": "AI 设置",
    "set.card.aiSub": "OpenAI 兼容接口；密钥仅掩码回显，留空保存表示保留原值",
    "set.card.theme": "主题与缓存",
    "set.card.themeSub": "主题切换支持 Ctrl+M 快捷键",
    "set.card.about": "关于",
    "set.card.aboutSub": "数据以真实接口为准，非投资建议",
    "set.label.baseUrl": "接口地址",
    "set.label.apiKey": "API Key",
    "set.label.model": "模型",
    "set.placeholder.baseUrl": "https://api.deepseek.com/v1",
    "set.placeholder.apiKeyNew": "首次配置必填",
    "set.placeholder.apiKeySaved": "已保存密钥（留空表示不修改）",
    "set.placeholder.model": "如 deepseek-chat",
    "set.action.save": "保存",
    "set.action.saving": "保存中…",
    "set.ai.configured": "当前已配置：{model}（密钥 {key}）",
    "set.ai.modelEmpty": "模型未填写",
    "set.ai.keyMasked": "已掩码",
    "set.ai.notConfigured": "尚未配置：保存后 Copilot 与 AI 复核即可使用（密钥仅存本地 data/ai_config.json）",
    "set.toast.aiSaved": "AI 配置已保存，立即生效",
    "set.toast.aiFailed": "保存失败：{message}",
    "set.theme.current": "当前主题：{theme}",
    "set.theme.light": "浅色",
    "set.theme.dark": "深色",
    "set.theme.toLight": "切换到浅色",
    "set.theme.toDark": "切换到深色",
    "set.theme.toggled": "主题已切换（快捷键 Ctrl+M）",
    "set.cache.label": "数据缓存：内存 + 磁盘，缓存清理统一在「数据连接器」页",
    "set.cache.jump": "去连接器页清理",
    "set.about.software": "软件",
    "set.about.version": "版本",
    "set.about.build": "构建标识",
    "set.about.python": "Python",
    "set.about.datasources": "数据源",
    "set.about.datasourcesValue": "{count} 个（详见数据连接器页）",
    "set.about.serverTime": "服务时间",
    "set.about.default": "OpenBB 中文投研工作台",
    "set.about.disclaimer": "本工具为本地个人投研用途：数据来自公开接口的实时拉取，行情可能有延迟；基金盘中估值为拟合估算仅供参考；不构成任何投资建议。",

    // ---------- 导出 ----------
    "export.prefix": "OpenBB导出",
    "export.suffix": "csv"
  };

  var locale = "zh-CN";

  /** 取翻译文本，支持 {name} 占位符。 */
  function t(key, vars) {
    var text = dict[key];
    if (text === undefined) {
      // 缺 key 时返回 key 本身，便于 grep 排查，不抛错、不白屏
      return key;
    }
    if (vars) {
      Object.keys(vars).forEach(function (name) {
        text = text.replace(new RegExp("\\{" + name + "\\}", "g"), String(vars[name]));
      });
    }
    return text;
  }

  /** 批量套用 data-i18n / data-i18n-title / data-i18n-placeholder。 */
  function apply(root) {
    var scope = root || document;
    scope.querySelectorAll("[data-i18n]").forEach(function (node) {
      node.textContent = t(node.getAttribute("data-i18n"));
    });
    scope.querySelectorAll("[data-i18n-title]").forEach(function (node) {
      node.setAttribute("title", t(node.getAttribute("data-i18n-title")));
    });
    scope.querySelectorAll("[data-i18n-placeholder]").forEach(function (node) {
      node.setAttribute("placeholder", t(node.getAttribute("data-i18n-placeholder")));
    });
    document.documentElement.setAttribute("lang", locale);
  }

  // =============================================================
  // 本地化格式（L10N）
  // =============================================================
  var MARKET_DIGITS = { a: 2, hk: 3, us: 2, fund: 4, index: 2 };
  var CURRENCY_SYMBOL = { CNY: "¥", HKD: "HK$", USD: "$" };

  /** 数值格式化：空值返回「无数据」，千分位 + 指定位数。 */
  function num(value, digits) {
    if (value === null || value === undefined || value === "") {
      return t("value.none");
    }
    var n = Number(value);
    if (isNaN(n)) {
      return t("value.none");
    }
    var fixed = n.toFixed(digits === undefined ? 2 : digits);
    var parts = fixed.split(".");
    parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    return parts.join(".");
  }

  /** 价格：A股 2 位、港股 3 位、美股 2 位、基金净值 4 位。 */
  function price(value, market) {
    var digits = MARKET_DIGITS[market || "a"];
    return num(value, digits === undefined ? 2 : digits);
  }

  /** 百分比：带符号，默认 2 位。 */
  function pct(value, digits) {
    if (value === null || value === undefined) {
      return t("value.none");
    }
    var n = Number(value);
    if (isNaN(n)) {
      return t("value.none");
    }
    return (n > 0 ? "+" : "") + n.toFixed(digits === undefined ? 2 : digits) + "%";
  }

  /** 金额：带币种符号，千分位。 */
  function money(value, currency, digits) {
    if (value === null || value === undefined) {
      return t("value.none");
    }
    var symbol = CURRENCY_SYMBOL[currency || "CNY"] || "";
    return symbol + num(value, digits === undefined ? 2 : digits);
  }

  /** 日期时间：统一 Asia/Shanghai（YYYY-MM-DD HH:mm）。 */
  function datetime(value) {
    if (!value) {
      return t("value.none");
    }
    var stamp = new Date(String(value).replace(/-/g, "/"));
    if (isNaN(stamp.getTime())) {
      return String(value).slice(0, 16);
    }
    var formatter = new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hour12: false
    });
    return formatter.format(stamp).replace(/\//g, "-");
  }

  /** 日期：YYYY-MM-DD（Asia/Shanghai）。 */
  function date(value) {
    return datetime(value).slice(0, 10);
  }

  /** 导出文件名：OpenBB导出_前缀_YYYYMMDD-HHmm.csv（不含非法字符）。 */
  function csvFilename(prefix) {
    var now = new Date();
    var pad = function (n) { return n < 10 ? "0" + n : String(n); };
    var stamp = now.getFullYear() + pad(now.getMonth() + 1) + pad(now.getDate()) +
      "-" + pad(now.getHours()) + pad(now.getMinutes());
    var clean = String(prefix || "数据").replace(/[\\/:*?"<>|\s]+/g, "");
    return t("export.prefix") + "_" + clean + "_" + stamp + "." + t("export.suffix");
  }

  /** CSV 文本：加 UTF-8 BOM，Excel 打开不乱码。 */
  function csvText(headers, rows) {
    var escape = function (cell) {
      var text = cell === null || cell === undefined ? "" : String(cell);
      return '"' + text.replace(/"/g, '""') + '"';
    };
    var lines = [headers.map(escape).join(",")];
    rows.forEach(function (row) {
      lines.push(row.map(escape).join(","));
    });
    return "\ufeff" + lines.join("\r\n");
  }

  window.I18N = { t: t, apply: apply, locale: locale, dict: dict };
  window.L10N = {
    num: num, price: price, pct: pct, money: money,
    datetime: datetime, date: date, csvFilename: csvFilename, csvText: csvText
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { apply(document); });
  } else {
    apply(document);
  }
})();
