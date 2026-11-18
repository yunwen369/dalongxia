/* ===================== 颜色映射（连续插值，量程由配置下发） ===================== */
function hex2rgb(h){h=h.replace('#','');if(h.length===3)h=h.split('').map(c=>c+c).join('');return [parseInt(h.slice(0,2),16),parseInt(h.slice(2,4),16),parseInt(h.slice(4,6),16)];}
function rgb2css(a){return 'rgb('+a.map(x=>Math.round(x)).join(',')+')';}
let HALT_CACHE='#3b4048';
/* 端点色与中性色对齐 dapanyuntu：红 #f03a3f / 绿 #12b146 / 0% #3d4250 */
function heatColor(p){
  if(p==null)return HALT_CACHE;
  const st=STATE.settings,th=THEMES[st.colorTheme]||THEMES.classic;
  const light=st.theme==='light';
  const neutral=hex2rgb(light?'#e7e9ed':(st.palette==='prd'?'#3d4250':'#3c4250'));
  let up=hex2rgb(st.palette==='prd'?'#f03a3f':'#FF4B4B'),down=hex2rgb(st.palette==='prd'?'#12b146':'#00B061');
  if(st.colorTheme!=='classic'){up=hex2rgb(th[1]);down=hex2rgb(th[0]);}
  const isRed=st.polarity==='redUp'?p>0:p<0;
  const end=isRed?up:down;
  const max=MARKETS[STATE.market].legend;
  const t=Math.min(Math.abs(p)/max,1);
  /* 指数 0.55：中低涨幅也能看出色差，避免整屏都是"接近中性"的灰块 */
  let a=Math.pow(t,0.5)*0.82+0.06;
  if(st.colorTheme==='soft')a*=0.72;if(st.colorTheme==='lowSat')a*=0.58;if(st.colorTheme==='highContrast')a=Math.min(1,a*1.25);
  if(light)a=Math.min(a,0.62);
  if(Math.abs(p)<=0.05)a=0.05;
  return rgb2css(neutral.map((c,i)=>c+(end[i]-c)*a));
}

/* ===================== 过滤 / 统计 ===================== */
function viewFilter(it,v){
  v=v||STATE.view;
  /* 除自选外，所有视图口径（含沪深300/中证A500 成分名录）由后端按 view 参数过滤，
     前端不再重复判断，避免两套口径打架。 */
  if(v==='watchlist')return STATE.watchlist.indexOf(STATE.market+':'+it.code)>=0;
  return true;
}
function visibleItems(){
  const f=STATE.filters,d=STATE.drill||{l1:null,l2:null};
  return DEMO_DATA[STATE.market].filter(it=>{
    if(!viewFilter(it))return false;
    if(d.l1&&it.l1!==d.l1)return false;
    if(d.l2&&it.l2!==d.l2)return false;
    if(f.groups.length&&f.groups.indexOf(it.l1)<0)return false;
    if(f.keyword&&(it.name.indexOf(f.keyword)<0&&it.code.indexOf(f.keyword)<0))return false;
    if(f.direction==='up'&&!(it.pct>0.05))return false;
    if(f.direction==='down'&&!(it.pct< -0.05))return false;
    if(f.pctMin!=null&&it.pct<f.pctMin)return false;
    if(f.pctMax!=null&&it.pct>f.pctMax)return false;
    if(STATE.market==='etf'&&f.hideBondCurrency&&(it.l1==='债券'||it.l1==='货币'))return false;
    return true;
  });
}
/* 后端字段名（marketCapTotal）与前端扁平键（mcapTotal）不同，必须显式映射；
   若直接 it[STATE.areaMetric] 会恒为 undefined → 布局退化成「按只数」，与市值口径不符 */
const AREA_KEY={marketCapTotal:'mcapTotal',marketCapFloat:'mcapFloat',turnover:'turnover',scale:'scale'};
function areaVal(it){return it[AREA_KEY[STATE.areaMetric]||STATE.areaMetric];}
function areaOf(it){const v=areaVal(it);return v==null||v<=0?0.35:v;}
function summary(items){
  let up=0,flat=0,down=0,sum=0,n=0,turn=0;
  items.forEach(it=>{if(it.pct==null)return;n++;sum+=it.pct;turn+=it.turnover||0;
    if(it.pct>0.05)up++;else if(it.pct< -0.05)down++;else flat++;});
  return {up,flat,down,avgPct:n?P(sum/n):null,turnover:P(turn),count:items.length};
}

/* ===================== 渲染入口（canvas 单层绘制） ===================== */
function buildGroups(items){
  const map=new Map();
  items.forEach(it=>{
    if(!map.has(it.l1))map.set(it.l1,new Map());
    const m=map.get(it.l1);
    if(!m.has(it.l2))m.set(it.l2,[]);
    m.get(it.l2).push(it);
  });
  return Array.from(map).map(([l1,subs])=>{
    const subArr=Array.from(subs).map(([l2,arr])=>({name:l2,items:arr,stat:summary(arr)}));
    const all=[];subArr.forEach(s=>{for(let i=0;i<s.items.length;i++)all.push(s.items[i]);});
    return {name:l1,subs:subArr,stat:summary(all)};
  });
}
let LAST_BOXES=[],LAST_W=1,LAST_H=1;

function renderTreemap(){
  const cv=$('B-inner');if(!cv||!window.MCV)return;
  MCV.mount(cv);MCV.resize();
  const dim=MCV.size(),items=visibleItems();
  if(!items.length){MCV.setData([]);MCV.paint();LAST_BOXES=[];renderThumb([]);return;}
  for(let i=0;i<items.length;i++)items[i]._area=Math.max(areaOf(items[i]),0.0001);
  const groups=buildGroups(items);
  const boxes=MCV.buildLayout(groups,dim.w,dim.h);
  MCV.setData(boxes);MCV.paint();
  LAST_BOXES=boxes;LAST_W=dim.w;LAST_H=dim.h;
  renderThumb(boxes);
}

/* ===================== 缩放 / 平移 / 下钻 ===================== */
function applyZoom(){
  if(window.MCV){MCV.resize();MCV.paint();}
  const z=$('B-zoom-val');if(z)z.textContent=Math.round(STATE.zoom*100)+'%';
  if(LAST_BOXES.length)renderThumb(LAST_BOXES);
}
function setZoom(k,cx,cy){
  const old=STATE.zoom;k=Math.max(0.6,Math.min(6,k));
  const wrap=$('B-wrap').getBoundingClientRect();cx=cx==null?wrap.width/2:cx;cy=cy==null?wrap.height/2:cy;
  STATE.tx=cx-(cx-STATE.tx)*(k/old);STATE.ty=cy-(cy-STATE.ty)*(k/old);STATE.zoom=k;applyZoom();
}
function resetView(){STATE.zoom=1;STATE.tx=0;STATE.ty=0;applyZoom();}

/* 下钻：单击一级/二级标题进入该分组，面包屑逐级返回（站点交互） */
function drillTo(l1,l2){
  STATE.drill={l1:l1||null,l2:l2||null};
  STATE.popCode=null;
  const p=$('B-popover');if(p)p.classList.remove('on');
  if(window.MCV)MCV.setHover(null);
  resetView();refreshAll();renderCrumb();
}
function clearDrill(level){
  if(!STATE.drill.l1)return;
  if(level==='l1')STATE.drill={l1:STATE.drill.l1,l2:null};
  else STATE.drill={l1:null,l2:null};
  resetView();refreshAll();renderCrumb();
}
function renderCrumb(){
  const c=$('B-crumb');if(!c)return;
  const d=STATE.drill||{};
  if(!d.l1){c.classList.remove('on');c.innerHTML='';return;}
  let h='<button data-crumb="all">全部</button><i>›</i>';
  h+='<button data-crumb="l1" class="'+(d.l2?'':'cur')+'">'+esc(d.l1)+'</button>';
  if(d.l2)h+='<i>›</i><button data-crumb="l2" class="cur">'+esc(d.l2)+'</button>';
  const n=visibleItems().length;
  h+='<em>'+n+' 只</em>';
  c.innerHTML=h;c.classList.add('on');
}

/* ===================== 左栏 / 图例 / 统计 ===================== */
/* 视图均值：仅在「A股全图」下有意义（此时本地持有全市场样本）；
   切到成分视图后数据已被后端裁到成分内，再算其他视图会失真，故只显示当前值 */
function viewAvg(v){
  const all=DEMO_DATA[STATE.market];
  if(!all.length)return null;
  if(v==='watchlist'){
    const arr=all.filter(it=>STATE.watchlist.indexOf(STATE.market+':'+it.code)>=0);
    return arr.length?summary(arr).avgPct:null;
  }
  if(v==='all')return summary(all).avgPct;
  if(v==='hs300'||v==='a500')return null; /* 成分均值需成员名单，后端已按成分过滤，前端不再重算 */
  if(v==='sh')return summary(all.filter(it=>it.code[0]==='6')).avgPct;
  if(v==='sz')return summary(all.filter(it=>it.code[0]==='0'||it.code[0]==='3')).avgPct;
  if(v==='chinext')return summary(all.filter(it=>it.code[0]==='3')).avgPct;
  if(v==='star')return summary(all.filter(it=>it.code.indexOf('688')===0)).avgPct;
  if(v==='bj')return summary(all.filter(it=>it.code.indexOf('920')===0||it.code[0]==='8'||it.code[0]==='4')).avgPct;
  return null;
}
function renderSide(){
  $('A-markets').innerHTML=Object.keys(MARKETS).map(k=>'<button class="mc-mkt'+(STATE.market===k?' on':'')+'" data-market="'+k+'">'+window.t('market.'+k)+'</button>').join('');
  /* 视图列表：口径由后端下发，侧栏只做导航，不再本地重算涨跌，避免与 K 线口径不一致 */
  const canAvg=STATE.view==='all';
  $('A-views').innerHTML=MARKETS[STATE.market].view?VIEWS.map(v=>{
    const on=STATE.view===v[0];
    const avg=canAvg?viewAvg(v[0]):null;
    const num=(canAvg&&avg!=null)?'<em class="num" style="color:'+pctColor(avg)+'">'+fmtPct(avg)+'</em>':'<em class="num"></em>';
    return '<button class="mc-view'+(on?' on':'')+'" data-view="'+v[0]+'"><span>'+window.t('view.'+v[0])+'</span>'+num+'</button>';
  }).join(''):'';
  const mh=$('A-metric-hint');if(mh)mh.textContent=metricLabel();
  const n=[STATE.filters.groups.length,STATE.filters.direction!=='all',STATE.filters.pctMin!=null||STATE.filters.pctMax!=null,STATE.filters.keyword].filter(Boolean).length;
  const badge=$('A-filter-count');
  if(badge){badge.style.display=n?'':'none';badge.textContent=n;}
  const s=summary(visibleItems());
  $('A-cards').innerHTML='<div class="mc-card"><div class="v num" style="color:var(--up)">'+s.up+'</div><div class="l">'+window.t('sum.up')+'</div></div>'+
    '<div class="mc-card"><div class="v num" style="color:var(--dim)">'+s.flat+'</div><div class="l">'+window.t('sum.flat')+'</div></div>'+
    '<div class="mc-card"><div class="v num" style="color:var(--down)">'+s.down+'</div><div class="l">'+window.t('sum.down')+'</div></div>';
  $('A-stat').innerHTML='<div class="row"><span>平均涨跌</span><b class="num" style="color:'+pctColor(s.avgPct)+'">'+fmtPct(s.avgPct)+'</b></div>'+
    '<div class="row"><span>成交额</span><b class="num">'+fmtCap(s.turnover)+'</b></div>'+
    '<div class="row"><span>样本</span><b class="num">'+s.count+' 只</b></div>';

  const meta=window.MC_META||{};
  const d=STATE.drill||{};
  $('B-title').textContent=(d.l2||d.l1||window.t('view.'+(VIEWS.find(v=>v[0]===STATE.view)||VIEWS[0])[0]))+' · '+window.t('market.'+STATE.market);
  const periodTxt={day:'当日',week:'5 日（周）',month:'20 日（月）',year:'年初至今（年）'}[STATE.filters.period]||'当日';
  const lm=$('B-legend-meta');
  if(lm)lm.innerHTML='面积 '+metricLabel()+' · 颜色 '+periodTxt+'涨跌 · 量程 ±'+MARKETS[STATE.market].legend+'%';
  const rf=$('A-refresh');if(rf)rf.textContent=meta.asOf?meta.asOf.slice(11,19):'--:--:--';
  const src=$('A-source-note');
  if(src){
    const bits=[];
    bits.push(meta.ok?('东方财富 push2 · '+(meta.sessionText||'--')+'<br>数据时间 '+(meta.asOf||'--')):'行情未连接，等待重试');
    if(meta.total)bits.push('全市场样本 '+meta.total+' 只');
    if(meta.sourceNote)bits.push('<span style="color:var(--warn)">'+esc(meta.sourceNote)+'</span>');
    else if(meta.notice)bits.push('<span style="color:var(--warn)">'+esc(meta.notice)+'</span>');
    src.innerHTML=bits.join('<br>');
  }
  const down=STATE.settings.palette==='prd'?'#12b146':'#00B061';
  const up=STATE.settings.palette==='prd'?'#f03a3f':'#FF4B4B';
  const mid=getComputedStyle(document.body).getPropertyValue('--legend-mid').trim();
  const [l,r]=STATE.settings.polarity==='redUp'?[down,up]:[up,down];
  const sc=$('B-scale');if(sc)sc.style.background='linear-gradient(90deg,'+l+' 0%,'+mid+' 50%,'+r+' 100%)';
  const L=MARKETS[STATE.market].legend;
  const tk=$('B-ticks');
  if(tk)tk.innerHTML=[-L,-L/2,0,L/2,L].map(x=>'<span class="num">'+(x>0?'+':'')+(Math.round(x*10)/10)+'%</span>').join('');
  const bs=$('B-source');if(bs)bs.textContent='东方财富 push2';
  const sess=$('B-session');if(sess)sess.textContent=(meta.sessionText||'--');
  const staleEl=$('B-stale');if(staleEl)staleEl.style.display=meta.stale?'':'none';
}
function renderThumb(boxes){
  const box=$('B-thumb-mini');if(!box)return;
  box.querySelectorAll('i').forEach(e=>e.remove());
  const W=LAST_W||1,H=LAST_H||1;
  (boxes||[]).forEach(g=>{
    if(g.kind!=='g1')return;
    const i=document.createElement('i');
    i.style.left=(g.x/W*100)+'%';i.style.top=(g.y/H*100)+'%';
    i.style.width=(g.w/W*100)+'%';i.style.height=(g.h/H*100)+'%';
    box.appendChild(i);
  });
  const vp=$('B-thumb-vp'),wrap=$('B-wrap');if(!vp||!wrap)return;
  vp.style.left=Math.max(0,(-STATE.tx/STATE.zoom/wrap.clientWidth*100))+'%';
  vp.style.top=Math.max(0,(-STATE.ty/STATE.zoom/wrap.clientHeight*100))+'%';
  vp.style.width=Math.min(100,100/STATE.zoom)+'%';vp.style.height=Math.min(100,100/STATE.zoom)+'%';
}


/* =====================================================================
   检视器日 K 线（PRD §4.7 / §4.8 / F21）—— 使用本地 vendor ECharts 渲染
   K 线 + MA5/MA10 均线 + 成交量柱；数据为演示序列，非真实行情。
   ===================================================================== */
function initInspectorChart(it) {
  var box = document.getElementById('C-echarts');
  if (!box || typeof echarts === 'undefined') return;
  var old = echarts.getInstanceByDom(box);
  if (old) old.dispose();
  var detail = (window.MC_ITEM || {})[STATE.market + ':' + it.code];
  var kd = detail && detail.kline;
  if (!kd) {
    box.innerHTML = '<div style="height:160px;display:flex;align-items:center;justify-content:center;text-align:center;padding:0 12px;font-size:11px;color:var(--faint)">'
      + ((window.MC && MC.supportedKline(STATE.market)) ? 'K 线加载中…' : '该市场日 K 线尚未接入，本轮按空态展示')
      + '</div>';
    if (window.MC && MC.supportedKline(STATE.market)) MC.loadItem(STATE.market, it.code);
    return;
  }
  var chart = echarts.init(box, null, { renderer: 'canvas' });
  var dates = kd.dates || [], ohlc = [], vol = [], closes = kd.close || [];
  for (var i = 0; i < dates.length; i++) {
    ohlc.push([kd.open[i], kd.close[i], kd.low[i], kd.high[i]]);
    vol.push(kd.volume ? kd.volume[i] : null);
  }
  function ma(k) {
    var src = (kd.ma && kd.ma['ma' + k]) || null;
    return closes.map(function (_, j) { return (src && src[j] != null) ? src[j] : '-'; });
  }
  var up = getComputedStyle(document.body).getPropertyValue('--up').trim() || '#e0453a';
  var down = getComputedStyle(document.body).getPropertyValue('--down').trim() || '#2ea36b';
  chart.setOption({
    animation: false,
    grid: [{ left: 44, right: 8, top: 8, height: '58%' },
           { left: 44, right: 8, top: '74%', height: '18%' }],
    xAxis: [
      { type: 'category', data: dates, gridIndex: 0, axisLabel: { show: false }, axisLine: { lineStyle: { color: '#4a505c' } } },
      { type: 'category', data: dates, gridIndex: 1, axisLabel: { show: false }, axisLine: { lineStyle: { color: '#4a505c' } } }
    ],
    yAxis: [
      { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: 'rgba(127,127,127,.15)' } }, axisLabel: { fontSize: 9 } },
      { scale: true, gridIndex: 1, splitLine: { show: false }, axisLabel: { show: false } }
    ],
    series: [
      { name: '日K', type: 'candlestick', data: ohlc,
        itemStyle: { color: up, color0: down, borderColor: up, borderColor0: down } },
      { name: 'MA5', type: 'line', data: ma(5), smooth: true, showSymbol: false, lineStyle: { width: 1, color: '#E8A33D' } },
      { name: 'MA10', type: 'line', data: ma(10), smooth: true, showSymbol: false, lineStyle: { width: 1, color: '#4B9EFF' } },
      { name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: vol,
        itemStyle: { color: function (p) { return ohlc[p.dataIndex][1] >= ohlc[p.dataIndex][0] ? up : down; } } }
    ]
  });
}
