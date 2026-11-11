/* 演示数据（仅当 /api 不可达时降级使用，例如 file:// 直接打开）。
   规范 §7.3.1(5)：集中于此，界面统一显示「演示数据·非真实行情」角标。
   结构与真实端点一致；__demo 标记供渲染层识别，禁止冒充真实行情。 */

window.DEMO_DATA = {
  __demo: true,
  index: {
    items: [
      { symbol: "000001", name: "上证指数", price: 3663.72, pct: 0.08, change: 2.93 },
      { symbol: "399001", name: "深证成指", price: 11842.16, pct: 0.62, change: 73.02 },
      { symbol: "399006", name: "创业板指", price: 2488.35, pct: 1.10, change: 27.08 },
      { symbol: "000688", name: "科创50", price: 1086.44, pct: 1.43, change: 15.31 }
    ],
    as_of: "演示数据·非真实行情"
  },
  indexKline: {
    items: Array.from({ length: 60 }, function (_, i) {
      const base = 3400 + i * 4.2 + Math.sin(i / 4) * 38;
      return {
        date: "2026-08-" + String((i % 28) + 1).padStart(2, "0"),
        open: base, close: base + Math.cos(i / 3) * 12,
        high: base + 18, low: base - 18, volume: 3.5e8 + i * 2e6
      };
    })
  },
  sentiment: {
    temperature: 54.9, level: "中性", positive: 7, negative: 0, neutral: 53, total: 60,
    breadth: { up: 4035, down: 1101, flat: 159, available: true }
  },
  industry: {
    items: [
      { name: "影视院线", code: "881274", pct: 7.05, amount: 196.02, net_inflow: 2.42, lead_stock: "华智数媒" },
      { name: "游戏", code: "881275", pct: 4.81, amount: 123.09, net_inflow: 6.71, lead_stock: "恺英网络" },
      { name: "软件开发", code: "881276", pct: 3.62, amount: 210.44, net_inflow: 9.15, lead_stock: "润和软件" },
      { name: "半导体", code: "881277", pct: 2.94, amount: 402.10, net_inflow: 18.30, lead_stock: "寒武纪" },
      { name: "通信设备", code: "881278", pct: 2.31, amount: 178.55, net_inflow: 5.02, lead_stock: "中际旭创" },
      { name: "电力设备", code: "881279", pct: 1.88, amount: 155.20, net_inflow: 3.11, lead_stock: "宁德时代" },
      { name: "证券", code: "881280", pct: 1.42, amount: 132.80, net_inflow: 1.90, lead_stock: "东方财富" },
      { name: "白酒", code: "881281", pct: 0.96, amount: 98.30, net_inflow: -1.20, lead_stock: "贵州茅台" },
      { name: "银行", code: "881282", pct: 0.35, amount: 121.40, net_inflow: 0.80, lead_stock: "招商银行" },
      { name: "港口航运", code: "881283", pct: -1.03, amount: 89.75, net_inflow: -11.65, lead_stock: "中远海控" }
    ],
    count: 10
  },
  summary: {
    gainers: [{ name: "影视院线", pct: 7.05 }, { name: "游戏", pct: 4.81 }, { name: "软件开发", pct: 3.62 }],
    losers: [{ name: "油气开采", pct: -1.61 }, { name: "港口航运", pct: -1.03 }, { name: "煤炭", pct: -0.88 }],
    concentration: 45.11, total_amount: 15167.45, board_count: 90
  },
  crowding: { level_counts: { 高位: 1, 偏高: 13, 正常: 76 }, scanned: 90, total: 90, items: [] },
  alerts: { events: [], unread: 0 },
  macro: {
    quadrant: {
      stage: "滞胀",
      conclusion: "增长放缓、通胀上行，当前位于「滞胀」阶段。增长放缓而通胀上行，历史上对应盈利与估值同时承压。",
      disclaimer: "以上为宏观数据的位置判断与历史规律描述，不构成投资建议，也不预测未来。"
    }
  }
};
