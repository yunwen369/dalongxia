/* =====================================================================
   真实行情接入层（PRD §7.1 /api/market-cloud/overview）
   职责：把后端 groups 树摊平成渲染层使用的扁平标的数组，写回 DEMO_DATA；
        同时把 asOf/session/stale/source 等元信息写到 window.MC_META。
   红线：请求失败时**清空**演示数据并进入错误态，不拿 mock 冒充实盘。
   ===================================================================== */
(function () {
  var API = '/api/market-cloud/overview';
  var loaded = {};       // "market:period" -> true：已成功取到一次快照
  var inflight = {};     // "market:period" -> Promise：同参数并发去重
  var metaByMarket = {}; // "market:period" -> 最近一次成功的元信息
  var seq = 0;           // 请求序号：只允许最新一次响应改写界面
  var currentKey = null;  // DEMO_DATA 当前承载的是哪一组 (market,period,view) 的数据

  function ck(market, period, view) { return market + ':' + (period || 'day') + ':' + (view || 'all'); }

  window.MC_META = {
    market: 'stock', period: 'day', asOf: null, session: null, sessionText: null,
    stale: false, source: null, sourceNote: null, notice: null,
    total: 0, legendMax: null, ok: false, error: null
  };

  function resetMeta(market) {
    window.MC_META = {
      market: market, period: 'day', asOf: null, session: null, sessionText: null,
      stale: false, source: null, sourceNote: null, notice: null,
      total: 0, legendMax: null, ok: false, error: null
    };
  }

  /* groups -> 扁平数组（字段名对齐渲染层既有的 it.pct / it.mcapTotal 等） */
  function flatten(data) {
    var out = [];
    (data.groups || []).forEach(function (g) {
      (g.children || []).forEach(function (c) {
        (c.items || []).forEach(function (it) {
          out.push({
            code: it.code,
            name: it.name,
            l1: g.level1,
            l2: c.level2,
            price: it.price,
            pct: it.changePct,
            changeAmt: it.changeAmt,
            amplitude: it.amplitude,
            mcapTotal: it.marketCapTotal,
            mcapFloat: it.marketCapFloat,
            scale: it.scale,
            turnover: it.turnover,
            turnoverRate: it.extra && it.extra.turnoverRate != null ? it.extra.turnoverRate : null,
            status: it.status === 'trading' ? 'trading' : it.status,
            mini: null,
            idx: null
          });
        });
      });
    });
    return out;
  }

  function replaceMarket(market, items) {
    var target = DEMO_DATA[market];
    if (!target) return;
    target.length = 0;
    for (var i = 0; i < items.length; i++) target.push(items[i]);
  }

  function applyMeta(data, period, view) {
    window.MC_META = {
      market: data.market,
      period: period || 'day',
      view: view || 'all',
      asOf: data.asOf,
      session: data.session,
      sessionText: data.sessionText,
      stale: !!data.stale,
      source: data.source,
      sourceNote: data.sourceNote || null,
      notice: data.notice || null,
      total: data.total || 0,
      ok: true,
      error: null
    };
    metaByMarket[ck(data.market, period, view)] = window.MC_META;
    if (data.legend && data.legend.max) window.MC_META.legendMax = data.legend.max;
    /* MARKETS 在 demoData.js 里是 const，不会挂到 window，必须裸引用 + typeof 保护 */
    if (typeof MARKETS !== 'undefined' && MARKETS[data.market]) {
      if (data.legend && data.legend.max) MARKETS[data.market].legend = data.legend.max;
      if (data.areaUnit) MARKETS[data.market].unit = data.areaUnit;
    }
  }

  function fetchMarket(market, force, period, view) {
    period = period || (STATE.filters && STATE.filters.period) || 'day';
    var key = ck(market, period, view);
    if (inflight[key]) return inflight[key];
    var url = API + '?market=' + encodeURIComponent(market) + '&period=' + encodeURIComponent(period);
    if (view && view !== 'all') url += '&view=' + encodeURIComponent(view);
    if (force) url += '&refresh=1';
    var my = ++seq;
    var p = fetch(url, { headers: { Accept: 'application/json' } })
      .then(function (r) { return r.json(); })
      .then(function (body) {
        if (body.error) throw new Error(body.error);
        var data = body.data || {};
        if (my !== seq) return data; // 已被更新的请求取代，丢弃过期响应
        replaceMarket(market, flatten(data));
        if (market === STATE.market) applyMeta(data, period, view);
        loaded[key] = true;
        currentKey = key;
        return data;
      })
      .then(function (data) {
        delete inflight[key];
        if (my === seq && market === STATE.market) {
          STATE.dataState = 'default';
          if (typeof renderState === 'function') renderState();
          refreshAll();
        }
        return data;
      })
      .catch(function (err) {
        delete inflight[key];
        if (my !== seq) throw err;
        replaceMarket(market, []);
        if (market === STATE.market) {
          resetMeta(market);
          window.MC_META.error = err && err.message ? err.message : '行情获取失败';
          STATE.dataState = 'error';
          if (typeof renderState === 'function') renderState();
          refreshAll();
        }
        throw err;
      });
    inflight[key] = p;
    return p;
  }

  /* 单标的详情（PRD §7.3）：供弹窗/检视器取真实日 K 与同分组排行 */
  var itemCache = window.MC_ITEM || (window.MC_ITEM = {});
  var itemInflight = {};

  function loadItem(market, code) {
    var key = market + ':' + code;
    if (itemCache[key]) return Promise.resolve(itemCache[key]);
    if (itemInflight[key]) return itemInflight[key];
    var p = fetch('/api/market-cloud/item?market=' + encodeURIComponent(market) + '&code=' + encodeURIComponent(code))
      .then(function (r) { return r.json(); })
      .then(function (body) {
        if (body.error) throw new Error(body.error);
        itemCache[key] = body.data;
        delete itemInflight[key];
        repaintFor(code);
        return body.data;
      })
      .catch(function () { delete itemInflight[key]; return null; });
    itemInflight[key] = p;
    return p;
  }

  /* 详情到达后就地重绘（只在用户仍停留在该标的时） */
  function repaintFor(code) {
    if (typeof findItem !== 'function') return;
    var it = findItem(code);
    if (!it) return;
    var pop = document.getElementById('B-popover');
    if (STATE.popCode === code && pop && pop.classList.contains('on')) {
      renderPopover(it, parseInt(pop.style.left, 10) || 20, parseInt(pop.style.top, 10) || 20);
    }
    if (STATE.selected === code && STATE.inspector !== 'hidden') renderInspector(it);
  }

  /* 对外：切换市场/周期时按需拉取；同参数已加载过则直接用缓存，force=true 强制回源 */
  window.MC = {
    ensure: function (market, force, period, view) {
      period = period || (STATE.filters && STATE.filters.period) || 'day';
      var key = ck(market, period, view);
      if (!force && loaded[key] && currentKey === key) {
        /* 命中已加载组合：恢复该组合的元信息，避免停留在上一周期的量程/asOf */
        if (metaByMarket[key]) {
          window.MC_META = metaByMarket[key];
          if (typeof MARKETS !== 'undefined' && MARKETS[market] && window.MC_META.legendMax) {
            MARKETS[market].legend = window.MC_META.legendMax;
          }
        }
        refreshAll();
        return Promise.resolve();
      }
      return fetchMarket(market, force, period, view).catch(function () { /* 错误态已渲染 */ });
    },
    reload: function () {
      var m = STATE.market;
      loaded[ck(m, STATE.filters.period, STATE.view === 'watchlist' ? 'all' : STATE.view)] = false;
      STATE.dataState = 'default';
      return fetchMarket(m, true, STATE.filters.period, STATE.view).catch(function () { /* 错误态已渲染 */ });
    },
    loadItem: loadItem,
    /* 仅 A 股接入日 K（其余市场需另接数据源，界面按空态展示） */
    supportedKline: function (market) { return market === 'stock'; },
    meta: function () { return window.MC_META; }
  };

  /* 启动：先清空 mock，避免首帧把演示数据当成实盘展示 */
  Object.keys(DEMO_DATA).forEach(function (m) { DEMO_DATA[m].length = 0; });
  resetMeta(STATE.market);
  fetchMarket(STATE.market, false).catch(function () { /* 错误态已渲染 */ });
})();
