/* =====================================================================
   Canvas 树图渲染器（v3.0）
   职责：squarified 布局 → 单 canvas 绘制 → 坐标命中检测。
   设计要点：
   1. 5,900+ 个方块用 DOM 会触发上万次 layout，改 canvas 后缩放平移只重绘一帧；
   2. 文字分级阈值按「块宽 × 块高」双约束，小块只出涨跌幅，中块出名称+涨跌幅，
      大块补价格 —— 与 dapanyuntu.com 的信息密度一致；
   3. 一级/二级标题条单独成层，先画方块再画标题，保证层级不被遮挡；
   4. 命中检测按「叶子 → 二级标题 → 一级标题」倒序，保证小方块优先。
   ===================================================================== */
(function () {
  var canvas = null, ctx = null, dpr = 1;
  var W = 0, H = 0;
  var boxes = [];
  var hover = null;

  /* ---------- squarified 布局 ---------- */
  function worst(row, sum, short) {
    var mx = 0, mn = Infinity;
    row.forEach(function (r) { mx = Math.max(mx, r.v); mn = Math.min(mn, r.v); });
    return Math.max(short * short * mx / (sum * sum), sum * sum / (short * short * mn));
  }

  function squarify(nodes, rect) {
    var total = nodes.reduce(function (a, b) { return a + Math.max(b.value, 0.0001); }, 0);
    if (total <= 0 || rect.w <= 0 || rect.h <= 0) return [];
    var area = rect.w * rect.h;
    var list = nodes.map(function (n) {
      return { node: n, v: Math.max(n.value, 0.0001) / total * area };
    }).sort(function (a, b) { return b.v - a.v; });

    var r = { x: rect.x, y: rect.y, w: rect.w, h: rect.h }, out = [];
    while (list.length && r.w > 0.4 && r.h > 0.4) {
      var vertical = r.w >= r.h, short = Math.min(r.w, r.h);
      var row = [], s = 0, ratio = Infinity;
      while (list.length) {
        var cand = list[0], ns = s + cand.v, nr = worst(row.concat([cand]), ns, short);
        if (!row.length || nr <= ratio) { row.push(list.shift()); s = ns; ratio = nr; } else break;
      }
      if (vertical) {
        var cw = Math.min(s / r.h, r.w), y = r.y;
        row.forEach(function (it) {
          var hh = it.v / (cw || 1);
          out.push({ node: it.node, x: r.x, y: y, w: cw, h: hh });
          y += hh;
        });
        r.x += cw; r.w -= cw;
      } else {
        var rh = Math.min(s / r.w, r.h), x = r.x;
        row.forEach(function (it) {
          var ww = it.v / (rh || 1);
          out.push({ node: it.node, x: x, y: r.y, w: ww, h: rh });
          x += ww;
        });
        r.y += rh; r.h -= rh;
      }
    }
    return out;
  }

  /* ---------- 版式常量（与站点实测一致） ---------- */
  var GAP1 = 6, HEAD1 = 22, GAP2 = 3, HEAD2 = 15, LEAF_GAP = 0.8;

  function buildLayout(groups, width, height) {
    var out = [];
    var g1 = groups.map(function (g) {
      var v = 0;
      g.subs.forEach(function (s) { s.items.forEach(function (it) { v += it._area; }); });
      return { g: g, value: v };
    });
    squarify(g1, { x: 0, y: 0, w: width, h: height }).forEach(function (p) {
      var x = p.x + GAP1 / 2, y = p.y + GAP1 / 2;
      var w = Math.max(p.w - GAP1, 1), h = Math.max(p.h - GAP1, 1);
      /* 只有一个一级分组时（下钻 / 筛选到单板块），顶部标题带让位给方块，信息由面包屑承担 */
      var head1 = (g1.length <= 1) ? 0 : Math.min(HEAD1, Math.max(h * 0.35, 10));
      out.push({ kind: 'g1', name: p.node.g.name, stat: p.node.g.stat, x: x, y: y, w: w, h: h,
                 hdr: { x: x, y: y, w: w, h: head1 } });
      if (h <= head1 + 4) return;

      var subs = p.node.g.subs.map(function (s) {
        var sv = 0; s.items.forEach(function (it) { sv += it._area; });
        return { s: s, value: sv };
      });
      squarify(subs, { x: x, y: y + head1, w: w, h: h - head1 }).forEach(function (q) {
        var x2 = q.x + GAP2 / 2, y2 = q.y + GAP2 / 2;
        var w2 = Math.max(q.w - GAP2, 1), h2 = Math.max(q.h - GAP2, 1);
        /* 一级与二级同名（如银行 / 银行）时不重复画标题，把高度让给方块 */
        var sameName = (q.node.s.name === p.node.g.name);
        var head2 = sameName ? 0 : Math.min(HEAD2, Math.max(h2 * 0.3, 9));
        out.push({ kind: 'g2', l1: p.node.g.name, name: q.node.s.name, stat: q.node.s.stat,
                   x: x2, y: y2, w: w2, h: h2, hdr: { x: x2, y: y2, w: w2, h: head2 } });
        if (h2 <= head2 + 3) return;

        var leaves = q.node.s.items.map(function (it) { return { it: it, value: it._area }; });
        squarify(leaves, { x: x2, y: y2 + head2, w: w2, h: h2 - head2 }).forEach(function (r) {
          out.push({ kind: 'leaf', item: r.node.it, l1: p.node.g.name, l2: q.node.s.name,
                     x: r.x, y: r.y, w: r.w, h: r.h });
        });
      });
    });
    return out;
  }

  /* ---------- 绘制 ---------- */
  /* canvas 不认 CSS 变量，必须在每帧前把 --up/--down 解析成实色，否则 fillStyle 会被忽略 */
  var COLORS = { up: '#f03a3f', down: '#12b146', dim: '#9aa0aa', halt: '#3b4048' };
  function refreshColors() {
    var cs = getComputedStyle(document.body);
    COLORS.up = cs.getPropertyValue('--up').trim() || COLORS.up;
    COLORS.down = cs.getPropertyValue('--down').trim() || COLORS.down;
    COLORS.dim = cs.getPropertyValue('--dim').trim() || COLORS.dim;
    COLORS.halt = cs.getPropertyValue('--halt').trim() || COLORS.halt;
  }
  function pctC(p) {
    if (p == null || Math.abs(p) <= 0.05) return COLORS.dim;
    var redUp = (typeof STATE === 'undefined') || STATE.settings.polarity === 'redUp';
    var isUp = p > 0;
    return (redUp ? isUp : !isUp) ? COLORS.up : COLORS.down;
  }

  function isLight() { return document.body.dataset.theme === 'light'; }
  function ink(main) {
    return isLight()
      ? (main ? 'rgba(20,28,38,.92)' : 'rgba(20,28,38,.68)')
      : (main ? 'rgba(255,255,255,.96)' : 'rgba(255,255,255,.78)');
  }
  function band(alpha) {
    return isLight() ? 'rgba(255,255,255,' + alpha + ')' : 'rgba(8,10,14,' + alpha + ')';
  }

  function font(weight, size) {
    return weight + ' ' + size + 'px Arial,"PingFang SC","Microsoft YaHei",sans-serif';
  }

  function clipText(text, maxW) {
    if (maxW <= 4) return '';
    if (ctx.measureText(text).width <= maxW) return text;
    var t = text;
    while (t.length > 1 && ctx.measureText(t + '…').width > maxW) t = t.slice(0, -1);
    return t + '…';
  }

  function drawLeaf(b) {
    var it = b.item;
    var x = b.x, y = b.y, w = Math.max(b.w - LEAF_GAP, 0.6), h = Math.max(b.h - LEAF_GAP, 0.6);
    var halted = it.status === 'halted';
    ctx.fillStyle = halted ? COLORS.halt : (window.heatColor ? heatColor(it.pct) : '#3d4250');
    ctx.fillRect(x, y, w, h);
    if (halted && w > 6 && h > 6) {
      ctx.save();
      ctx.beginPath(); ctx.rect(x, y, w, h); ctx.clip();
      ctx.strokeStyle = 'rgba(0,0,0,.22)'; ctx.lineWidth = 1;
      for (var d = -h; d < w; d += 6) {
        ctx.beginPath(); ctx.moveTo(x + d, y + h); ctx.lineTo(x + d + h, y); ctx.stroke();
      }
      ctx.restore();
    }
    if (hover && hover.kind === 'leaf' && hover.item.code === it.code) {
      ctx.strokeStyle = isLight() ? 'rgba(20,28,38,.7)' : 'rgba(255,255,255,.94)';
      ctx.lineWidth = 1.6;
      ctx.strokeRect(x + 0.8, y + 0.8, Math.max(w - 1.6, 0.6), Math.max(h - 1.6, 0.6));
      return;
    }
    if (w < 30 || h < 14) return;

    /* fmtPct 在 app.js 里是 const，不会挂到 window 上，只能用裸标识符 + typeof 保护 */
    var name = it.name, pct = (typeof fmtPct === 'function') ? fmtPct(it.pct) : '';
    var price = (it.price == null) ? '--' : it.price.toFixed(STATE.market === 'hk' ? 3 : 2);
    var cx = x + w / 2;

    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    var tier = 0;
    if (w >= 120 && h >= 88) tier = 4;      /* 三行：名称 / 涨跌幅 / 价格 */
    else if (w >= 84 && h >= 50) tier = 3;   /* 两行：名称 / 涨跌幅 */
    else if (w >= 40 && h >= 26) tier = 2;   /* 小两行：名称 / 涨跌幅（11px/10px） */
    else tier = 1;                            /* 只出涨跌幅 */


    if (tier === 1) {
      ctx.fillStyle = ink(true);
      ctx.font = font('600', 10);
      ctx.fillText(clipText(pct, w - 4), cx, y + h / 2);
      return;
    }

    var fName = tier === 4 ? 18 : tier === 3 ? 14 : 11;
    var fPct = tier === 4 ? 16 : tier === 3 ? 13 : 10;
    var fPrice = tier === 4 ? 13 : 11;
    var lines = tier === 4 ? 3 : 2;
    var totalH = fName * 1.15 + fPct * 1.15 + (lines === 3 ? fPrice * 1.15 : 0);
    var top = y + (h - totalH) / 2 + fName * 0.58;

    var nm = clipText(name, w - 6);
    ctx.fillStyle = ink(true);
    if (!nm) {
      /* 名称放不下时退化成「只出涨跌幅」一行，避免出现半截名称或只剩价格的空块 */
      ctx.font = font('700', fPct);
      ctx.fillText(pct, cx, y + h / 2);
      return;
    }
    ctx.font = font('700', fName);
    ctx.fillText(nm, cx, top);
    ctx.font = font('600', fPct);
    ctx.fillText(pct, cx, top + fName * 1.05);
    if (lines === 3 && h >= 108) {
      ctx.fillStyle = ink(false);
      ctx.font = font('500', fPrice);
      ctx.fillText(price, cx, top + fName * 1.05 + fPct * 1.05);
    }
  }

  function drawG2(b) {
    if (b.w < 30 || b.hdr.h < 8) return;
    var h = b.hdr.h;
    ctx.fillStyle = band('.34');
    ctx.fillRect(b.x, b.y, b.w, h);
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'left';
    var ty = b.y + h / 2;

    var stat = b.stat || {};
    var pctTxt = (stat.avgPct == null) ? '' : fmtPct(stat.avgPct);
    var cntTxt = (stat.up == null) ? '' : ('涨' + stat.up + ' 跌' + stat.down);
    ctx.font = font('600', 9);
    var cntW = cntTxt ? ctx.measureText(cntTxt).width : 0;
    ctx.font = font('700', 10);
    var pctW = pctTxt ? ctx.measureText(pctTxt).width : 0;

    var pad = 4;
    var nameMax = b.w - pad * 2 - pctW - cntW - (pctW ? 5 : 0) - (cntW ? 6 : 0);
    if (nameMax < 8) {
      ctx.fillStyle = ink(true);
      ctx.font = font('600', 9);
      ctx.fillText(clipText(b.name, b.w - 6), b.x + pad, ty);
      return;
    }
    ctx.fillStyle = ink(true);
    ctx.font = font('600', 10);
    ctx.fillText(clipText(b.name, nameMax), b.x + pad, ty);

    var rx = b.x + b.w - pad;
    if (cntW) {
      ctx.fillStyle = isLight() ? 'rgba(60,70,84,.72)' : 'rgba(170,176,188,.85)';
      ctx.font = font('600', 9);
      ctx.textAlign = 'right';
      ctx.fillText(cntTxt, rx, ty);
      rx -= cntW + 6;
    }
    if (pctW) {
      ctx.fillStyle = pctC(stat.avgPct);
      ctx.font = font('700', 10);
      ctx.textAlign = 'right';
      ctx.fillText(pctTxt, rx, ty);
    }
  }

  function drawG1(b) {
    var h = b.hdr.h;
    ctx.fillStyle = band('.45');
    ctx.fillRect(b.x, b.y, b.w, h);
    var stat = b.stat || {};

    ctx.textBaseline = 'middle';
    ctx.textAlign = 'left';
    var ty = b.y + h / 2, pad = 5;

    var pctTxt = (stat.avgPct == null) ? '' : fmtPct(stat.avgPct);
    var cntTxt = (stat.up == null) ? '' : ('涨 ' + stat.up + ' 跌 ' + stat.down);
    ctx.font = font('600', 10);
    var cntW = cntTxt ? ctx.measureText(cntTxt).width : 0;
    ctx.font = font('700', 11);
    var pctW = pctTxt ? ctx.measureText(pctTxt).width : 0;

    var nameMax = b.w - pad * 2 - cntW - pctW - (cntW ? 8 : 0) - (pctW ? 8 : 0) - 4;
    ctx.fillStyle = ink(true);
    ctx.font = font('700', 13);
    if (nameMax > 16) {
      ctx.fillText(clipText(b.name, nameMax), b.x + pad, ty);
    } else {
      ctx.font = font('700', 10);
      ctx.fillText(clipText(b.name, b.w - 8), b.x + pad, ty);
    }

    var rx = b.x + b.w - pad;
    if (pctW && b.w > 76) {
      ctx.fillStyle = pctC(stat.avgPct);
      ctx.font = font('700', 11);
      ctx.textAlign = 'right';
      ctx.fillText(pctTxt, rx, ty);
      rx -= pctW + 8;
    }
    if (cntW && b.w > 130) {
      ctx.fillStyle = isLight() ? 'rgba(60,70,84,.66)' : 'rgba(166,172,184,.9)';
      ctx.font = font('600', 9);
      ctx.textAlign = 'right';
      ctx.fillText(cntTxt, rx, ty);
    }
  }

  function paint() {
    if (!ctx) return;
    refreshColors();
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = isLight() ? '#f5f6f8' : '#0b0d11';
    ctx.fillRect(0, 0, W, H);
    ctx.save();
    ctx.translate(STATE.tx, STATE.ty);
    ctx.scale(STATE.zoom, STATE.zoom);

    var i;
    for (i = 0; i < boxes.length; i++) if (boxes[i].kind === 'leaf') drawLeaf(boxes[i]);
    for (i = 0; i < boxes.length; i++) if (boxes[i].kind === 'g2') drawG2(boxes[i]);
    for (i = 0; i < boxes.length; i++) if (boxes[i].kind === 'g1') drawG1(boxes[i]);

    ctx.restore();
  }

  /* ---------- 尺寸 / 命中 ---------- */
  function resize() {
    if (!canvas) return;
    var host = canvas.parentElement;
    var w = Math.max(host.clientWidth, 1), h = Math.max(host.clientHeight, 1);
    dpr = window.devicePixelRatio || 1;
    if (W === w && H === h && canvas.width === Math.round(w * dpr)) return false;
    W = w; H = h;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    canvas.style.width = w + 'px';
    canvas.style.height = h + 'px';
    return true;
  }

  function setData(list) { boxes = list || []; }

  function hitTest(clientX, clientY) {
    if (!canvas) return null;
    var r = canvas.getBoundingClientRect();
    var x = (clientX - r.left - STATE.tx) / STATE.zoom;
    var y = (clientY - r.top - STATE.ty) / STATE.zoom;
    for (var i = boxes.length - 1; i >= 0; i--) {
      var b = boxes[i];
      if (x >= b.x && x <= b.x + b.w && y >= b.y && y <= b.y + b.h) {
        if (b.kind === 'leaf') return b;
        if (b.hdr && y <= b.hdr.y + b.hdr.h) return b;
        // 落在分组体内但没命中方块（间隙）→ 归为该分组
        return b;
      }
    }
    return null;
  }

  function mount(el) {
    canvas = el;
    ctx = canvas.getContext('2d');
    canvas.style.display = 'block';
    resize();
  }

  function size() { return { w: W, h: H }; }
  function all() { return boxes; }
  function setHover(b) { hover = b; }

  window.MCV = {
    mount: mount, resize: resize, setData: setData, buildLayout: buildLayout,
    paint: paint, hitTest: hitTest, squarify: squarify,
    size: size, all: all, setHover: setHover
  };
})();
