/* =====================================================================
   视图切换状态重置矩阵（PRD §4.4.0）与各市场默认口径（§2.2 / §4.4.7）
   机器可读配置，供正式 React 实现的 reducer 直接对齐。
   ===================================================================== */
(function () {
  window.MC_VIEW_TRANSITIONS = {
    switchMarket: {
      reset: ['view', 'filters', 'keyword', 'areaMetric', 'zoom', 'pan', 'popover', 'inspector', 'selected'],
      keep: ['language', 'theme', 'accent', 'polarity', 'colorTheme', 'jumpSite', 'sidebarCollapsed']
    },
    switchView: { reset: ['zoom', 'pan', 'selected'], keep: ['market', 'areaMetric', 'filters', 'theme'] },
    setAreaMetric: { reset: ['zoom', 'pan'], keep: ['market', 'view', 'filters', 'theme'] },
    setFilters: { reset: ['statistics', 'ranking'], keep: ['market', 'view', 'areaMetric', 'theme'] },
    setAppearance: { reset: ['colors'], keep: ['market', 'view', 'areaMetric', 'filters', 'zoom', 'pan', 'selected'] },
    resetView: { reset: ['zoom', 'pan'], keep: ['market', 'view', 'areaMetric', 'filters', 'selected'] }
  };
  window.MC_MARKET_DEFAULTS = {
    stock: { areaMetric: 'marketCapTotal', legend: 4, unit: '亿元' },
    etf: { areaMetric: 'scale', legend: 4, unit: '亿元' },
    convertible: { areaMetric: 'scale', legend: 4, unit: '亿元' },
    hk: { areaMetric: 'marketCapFloat', legend: 10, unit: '亿港元' },
    us: { areaMetric: 'marketCapFloat', legend: 3, unit: '亿美元' }
  };
})();
