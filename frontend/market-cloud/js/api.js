/* =====================================================================
   工作台 iframe 嵌入通信（PRD §4.13 / §4.14）· 运行时内嵌于 OpenBB 工作台 market-cloud 页
   单向：父页面 postMessage 下发指令；本模块只上报事件，不反向篡改工作台。
   ===================================================================== */
(function () {
  var NS = 'market-cloud';

  function emit(name, payload) {
    try {
      window.parent.postMessage(
        { source: NS, type: 'event', name: name, payload: payload || {} }, '*');
    } catch (e) { /* 独立打开（非 iframe）时静默 */ }
  }

  /* v3.0 改用 canvas 渲染，没有 .mc-leaf DOM，事件由 main.js 的命中检测回调本函数 */
  window.MC_EMIT = emit;

  // 对外方法（父 → 子），指令名与 PRD §4.14 一一对应
  window.addEventListener('message', function (e) {
    var d = e.data || {};
    if (d.source !== 'workbench' || d.type !== 'command') return;
    var p = d.payload || {};
    switch (d.name) {
      case 'setMarket': if (MARKETS[p.market]) switchMarket(p.market); break;
      case 'setAreaMetric':
        if (p.metric) { STATE.areaMetric = p.metric; refreshAll(); renderDrawer(); }
        break;
      case 'setFilters':
        if (p.filters) {
          STATE.filters = Object.assign(STATE.filters, p.filters);
          refreshAll(); renderDrawer();
        }
        break;
      case 'focusGroup': if (p.name) drillTo(p.name, null); break;
      case 'resetView': resetView(); break;
      case 'exportImage': document.getElementById('B-share').click(); break;
      case 'setTheme':
        if (p.theme === 'light' || p.theme === 'dark') setSetting('theme', p.theme);
        break;
      default: emit('onCommandAck', { name: d.name, ok: false, reason: 'unknown command' }); return;
    }
    emit('onCommandAck', { name: d.name, ok: true });
  });

  emit('onReady', { version: '3.0', market: STATE.market, mode: 'embedded' });
})();
