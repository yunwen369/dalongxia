/* 界面文案（PRD §4.10.1）：核心条目中英双语；未覆盖条目按 PRD 明文回退中文 */
(function () {
  var ZH = {
    'brand.name': '大盘云图', 'brand.sub': 'OpenBB Terminal Pro',
    'nav.refresh': '最近刷新', 'filter.area': '面积',
    'foot.tools': '投研工具', 'foot.share': '分享',
    'nav.market': '市场', 'nav.views': 'A 股快捷视图', 'nav.source': '数据来源与口径',
    'filter.entry': '筛选', 'foot.pwa': '添加到桌面', 'foot.settings': '设置',
    'foot.collapse': '收起侧栏', 'top.state': '原型状态',
    'state.default': '默认', 'state.loading': '加载', 'state.empty': '空态', 'state.error': '错误',
    'settings.title': '设置', 'settings.sub': '调整热力图的显示、语言和看盘偏好',
    'drawer.filter': '筛选', 'help.title': '操作提示', 'share.title': '截图分享',
    'share.sub': '导出当前画布 PNG 并生成带参数的链接',
    'market.stock': '股票', 'market.etf': 'ETF', 'market.convertible': '可转债',
    'market.hk': '港股', 'market.us': '美股',
    'view.all': 'A股全图', 'view.sh': '上证A股', 'view.sz': '深证A股',
    'view.hs300': '沪深300', 'view.a500': '中证A500', 'view.chinext': '创业板',
    'view.star': '科创板', 'view.bj': '北交所', 'view.watchlist': '自选',
    'sum.up': '上涨', 'sum.flat': '平盘', 'sum.down': '下跌',
    'tab.appearance': '外观', 'tab.watchlist': '自选', 'tab.hotkeys': '快捷键',
    'tab.help': '帮助', 'tab.changelog': '更新日志', 'tab.about': '关于我们',
    'hk.screenshot': '截图分享', 'hk.resetView': '重置视图', 'hk.fullscreen': '全屏',
    'hk.openSettings': '设置', 'hk.toggleSidebar': '收起/展开侧栏',
    'hk.toggleTheme': '浅色/深色', 'hk.toggleFilter': '筛选',
    'hk.dockInspector': '检视器停靠', 'hk.pinInspector': '检视器固定'
  };
  var EN = {
    'brand.name': 'Market Cloud', 'brand.sub': 'OpenBB Terminal Pro',
    'nav.refresh': 'Updated', 'filter.area': 'Size',
    'foot.tools': 'Research', 'foot.share': 'Share',
    'nav.market': 'Markets', 'nav.views': 'A-share views', 'nav.source': 'Source & caliber',
    'filter.entry': 'Filter', 'foot.pwa': 'Add to desktop', 'foot.settings': 'Settings',
    'foot.collapse': 'Collapse sidebar', 'top.state': 'Prototype state',
    'state.default': 'Default', 'state.loading': 'Loading', 'state.empty': 'Empty', 'state.error': 'Error',
    'settings.title': 'Settings', 'settings.sub': 'Adjust display, language and preferences',
    'drawer.filter': 'Filter', 'help.title': 'Help', 'share.title': 'Share',
    'share.sub': 'Export canvas PNG and generate a share link',
    'market.stock': 'Stocks', 'market.etf': 'ETF', 'market.convertible': 'Convertibles',
    'market.hk': 'HK stocks', 'market.us': 'US stocks',
    'view.all': 'All A-shares', 'view.sh': 'SSE A-shares', 'view.sz': 'SZSE A-shares',
    'view.hs300': 'CSI 300', 'view.a500': 'CSI A500', 'view.chinext': 'ChiNext',
    'view.star': 'STAR Market', 'view.bj': 'BSE', 'view.watchlist': 'Watchlist',
    'sum.up': 'Up', 'sum.flat': 'Flat', 'sum.down': 'Down',
    'tab.appearance': 'Appearance', 'tab.watchlist': 'Watchlist', 'tab.hotkeys': 'Hotkeys',
    'tab.help': 'Help', 'tab.changelog': 'Changelog', 'tab.about': 'About',
    'hk.screenshot': 'Screenshot', 'hk.resetView': 'Reset view', 'hk.fullscreen': 'Fullscreen',
    'hk.openSettings': 'Settings', 'hk.toggleSidebar': 'Toggle sidebar',
    'hk.toggleTheme': 'Light / Dark', 'hk.toggleFilter': 'Filter',
    'hk.dockInspector': 'Dock inspector', 'hk.pinInspector': 'Pin inspector'
  };
  window.I18N = { zh: ZH, en: EN };
  window.t = function (k) {
    var d = window.I18N[STATE.settings.language] || ZH;
    return d[k] || ZH[k] || k;
  };
  window.applyI18n = function () {
    document.querySelectorAll('[data-i18n]').forEach(function (e) {
      var k = e.dataset.i18n;
      if (e.dataset.i18nAttr) e.setAttribute(e.dataset.i18nAttr, window.t(k));
      else e.textContent = window.t(k);
    });
  };
})();
