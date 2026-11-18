/* ===================== 悬浮弹窗 / 检视器 ===================== */
function svgKline(it){
  const detail=(window.MC_ITEM||{})[STATE.market+':'+it.code];
  const kd=detail&&detail.kline;
  if(!kd){
    if(window.MC&&MC.supportedKline(STATE.market))MC.loadItem(STATE.market,it.code);
    return '<div style="height:118px;display:flex;align-items:center;justify-content:center;font-size:11px;color:var(--faint)">'+((window.MC&&MC.supportedKline(STATE.market))?'日 K 线加载中…':'该市场日 K 线尚未接入，本轮按空态展示')+'</div>';
  }
  const vmax=Math.max.apply(null,kd.volume.map(x=>x||0).concat([1]));
  const arr=kd.dates.map((d,i)=>({o:kd.open[i],c:kd.close[i],h:kd.high[i],l:kd.low[i],v:(kd.volume[i]||0)/vmax*100}));
  const n=arr.length;const W=306,H=88,VH=22,pad=2;const hi=Math.max(...arr.map(x=>x.h)),lo=Math.min(...arr.map(x=>x.l));
  const y=v=>pad+(hi-v)/(hi-lo||1)*(H-2*pad);const cw=W/n;let s='<svg width="100%" height="118" viewBox="0 0 '+W+' '+(H+VH+8)+'">';
  arr.forEach((d,i)=>{const x=i*cw+cw/2,up=d.c>=d.o,col=up?'var(--up)':'var(--down)';
    s+='<line x1="'+x+'" y1="'+y(d.h)+'" x2="'+x+'" y2="'+y(d.l)+'" stroke="'+col+'" stroke-width="1"/>';
    s+='<rect x="'+(x-cw*0.32)+'" y="'+y(Math.max(d.o,d.c))+'" width="'+(cw*0.64)+'" height="'+Math.max(1,Math.abs(y(d.o)-y(d.c)))+'" fill="'+col+'"/>';
    s+='<rect x="'+(x-cw*0.32)+'" y="'+(H+4+(1-d.v/100)*VH)+'" width="'+(cw*0.64)+'" height="'+(d.v/100*VH)+'" fill="'+col+'" opacity=".7"/>';});
  s+='<line x1="0" y1="'+(H+2)+'" x2="'+W+'" y2="'+(H+2)+'" stroke="var(--border)" stroke-width="1"/></svg>';return s;
}
function sparkline(arr,up){
  /* 迷你走势需逐票近 20 日收盘，本轮未接入 → 明确显示 --，不画随机折线 */
  if(!arr||!arr.length)return '<span style="width:50px;text-align:center;color:var(--faint);font-size:10px">--</span>';
  const a=arr;const mx=Math.max(...a),mn=Math.min(...a);
  const pts=a.map((v,i)=>(i/(a.length-1)*50).toFixed(1)+','+(15-(v-mn)/(mx-mn||1)*13).toFixed(1)).join(' ');
  return '<svg width="50" height="16" viewBox="0 0 50 16"><polyline points="'+pts+'" fill="none" stroke="'+(up?'var(--up)':'var(--down)')+'" stroke-width="1.4"/></svg>';
}
const RANK_TABS=[['changePct','涨幅'],['changePctAsc','跌幅'],['amplitude','振幅'],['turnover','成交额'],['name','名称'],['mcapTotal','总市值'],['mcapFloat','流通市值']];
function rankList(it){
  const peers=DEMO_DATA[STATE.market].filter(x=>x.l1===it.l1);const sb=STATE.rankTab;
  let arr=peers.slice();
  if(sb==='changePctAsc')arr.sort((a,b)=>a.pct-b.pct);
  else if(sb==='name')arr.sort((a,b)=>a.name.localeCompare(b.name));
  else arr.sort((a,b)=>(b[sb]||0)-(a[sb]||0));
  return arr.slice(0,12);
}
function renderPopover(it,x,y){
  const p=$('B-popover'),list=rankList(it),s=summary(list);
  let h='<div class="mc-pop-h"><b>'+esc(it.name)+'</b><span class="num">'+esc(it.code)+'</span><span class="pct num" style="color:'+pctColor(it.pct)+'">'+fmtPct(it.pct)+'</span></div>';
  h+='<div class="mc-pop-sub"><span>最新价 <b class="num" style="color:var(--text)">'+(it.price==null?'--':it.price.toFixed(STATE.market==='hk'?3:2))+'</b></span><span>'+metricLabel()+' <b class="num" style="color:var(--text)">'+fmtCap(areaVal(it))+'</b></span><span>涨跌额 <b class="num" style="color:'+pctColor(it.changeAmt)+'">'+(it.changeAmt==null?'--':((it.changeAmt>0?'+':'')+it.changeAmt.toFixed(2)))+'</b></span><span>振幅 '+fmtPct(it.amplitude)+'</span></div>';
  const kd=(window.MC_ITEM||{})[STATE.market+':'+it.code];
  const ktag=kd&&kd.kline?('近 '+kd.kline.dates.length+' 交易日 · 东方财富'):'未接入';
  h+='<div class="mc-kline"><div class="kt"><span>'+esc(it.name)+' · 日K线</span><span>'+ktag+'</span></div>'+svgKline(it)+'</div>';
  h+='<div class="mc-pop-sub" style="margin:7px 0 3px">'+esc(it.l1)+(it.l2?' - '+esc(it.l2):'')+'　涨 '+s.up+' 跌 '+s.down+' '+fmtPct(s.avgPct)+'</div>';
  h+='<div class="mc-rank-tabs">'+RANK_TABS.map(t=>'<button data-rank="'+t[0]+'" class="'+(STATE.rankTab===t[0]?'on':'')+'">'+t[1]+'</button>').join('')+'</div>';
  h+='<div class="mc-rank">'+list.map(r=>'<div class="r'+(r.code===it.code?' on':'')+'" data-rank-code="'+esc(r.code)+'"><span class="nm">'+esc(r.name)+'</span>'+sparkline(r.mini,r.pct>=0)+'<span class="v num">'+(r.price==null?'--':r.price.toFixed(2))+'</span><span class="pc num" style="color:'+pctColor(r.pct)+'">'+fmtPct(r.pct)+'</span></div>').join('')+'</div>';
  h+='<div class="mc-pop-foot">G 固定 · M 停靠 · S / Shift+S 切换排序 · ↑/↓ 或 J/K 切换标的 · Esc 关闭</div>';
  p.innerHTML=h;p.classList.add('on');
  const canvas=$('B-canvas').getBoundingClientRect();
  let lx=x+16,ly=y+12;
  if(lx+336>canvas.width)lx=x-336;if(ly+410>canvas.height)ly=Math.max(4,canvas.height-414);
  p.style.left=Math.max(4,lx)+'px';p.style.top=Math.max(4,ly)+'px';
  STATE.popCode=it.code;
}
function renderInspector(it){
  const p=$('C-inspector'),list=rankList(it);
  let h='<div class="mc-insp-h"><b>'+esc(it.name)+' <span style="color:var(--faint);font-size:11px;font-weight:400">'+esc(it.code)+'</span></b><button class="x" data-close="inspector">×</button></div><div class="mc-insp-p">';
  h+='<div class="mc-insp-big num" style="color:'+pctColor(it.pct)+'">'+(it.price==null?'--':it.price.toFixed(STATE.market==='hk'?3:2))+'</div>';
  h+='<div class="num" style="color:'+pctColor(it.pct)+';font-size:15px;font-weight:600;margin-top:2px">'+fmtPct(it.pct)+'　<span style="font-size:12px">'+(it.changeAmt==null?'--':((it.changeAmt>0?'+':'')+it.changeAmt.toFixed(2)+' 元'))+'</span></div>';
  h+='<div class="mc-insp-metrics">'+[['总市值',fmtCap(it.mcapTotal)],['流通市值',fmtCap(it.mcapFloat)],['成交额',fmtCap(it.turnover)],['振幅',fmtPct(it.amplitude)],['换手率',it.turnoverRate==null?'--':it.turnoverRate.toFixed(2)+'%'],['规模/口径',fmtCap(areaVal(it))]].map(x=>'<div>'+x[0]+'<b class="num">'+x[1]+'</b></div>').join('')+'</div>';
  h+='<div style="font-size:11px;color:var(--dim)">所属分组：'+esc(it.l1)+' / '+esc(it.l2)+'　同分组排名 '+((list.findIndex(x=>x.code===it.code)+1)||'--')+'/'+list.length+'</div>';
  h+='<div class="mc-kline" style="margin-top:10px"><div class="kt"><span>近期日 K 线 · MA5/MA10</span><span>东方财富日线</span></div><div id="C-echarts" style="height:160px"></div></div>';
  h+='<div class="mc-insp-btns"><button id="C-watchlist">＋ 加入自选</button><button class="primary" id="C-jump">打开行情页</button></div>';
  h+='<div style="font-size:10px;color:var(--faint);margin-top:8px">M 停靠 / 取消 · G 固定 · 双击方块由工作台回调跳转</div></div>';
  p.innerHTML=h;initInspectorChart(it);
}
function openInspector(it,mode){
  STATE.selected=it.code;STATE.inspector=mode||'docked';
  $('market-cloud-root').className='mc-root'+(STATE.settings.sidebarCollapsed?' collapsed':'')+(mode==='docked'?' docked':'');
  const p=$('C-inspector');p.classList.toggle('float',mode==='float');
  renderInspector(it);renderTreemap();
}

/* ===================== 筛选抽屉 ===================== */
function renderDrawer(){
  const f=STATE.filters,groups=Array.from(new Set(DEMO_DATA[STATE.market].map(x=>x.l1)));
  let h='<div class="mc-fgroup"><label>一级板块 <span style="color:var(--faint);font-weight:400">（可多选，实时生效）</span></label><div class="mc-checks">';
  h+='<div class="mc-check"><input type="checkbox" data-group="__all__" '+(!f.groups.length?'checked':'')+'><span>全部板块</span><span>'+DEMO_DATA[STATE.market].length+'</span></div>';
  groups.forEach(g=>{const cnt=DEMO_DATA[STATE.market].filter(x=>x.l1===g).length;h+='<div class="mc-check"><input type="checkbox" data-group="'+esc(g)+'" '+(f.groups.indexOf(g)>=0?'checked':'')+'><span>'+esc(g)+'</span><span>'+cnt+'</span></div>';});
  h+='</div></div>';
  h+='<div class="mc-fgroup"><label>涨跌周期</label><div class="mc-seg" data-seg="period">'+[['day','当日'],['week','5 日'],['month','20 日'],['year','年初至今']].map(x=>'<button data-v="'+x[0]+'" class="'+(f.period===x[0]?'on':'')+'">'+x[1]+'</button>').join('')+'</div><div class="mc-fhint">周=近 5 日 · 月=近 20 日 · 年=年初至今，均由上游逐票字段直接下发</div></div>';
  h+='<div class="mc-fgroup"><label>涨跌方向</label><div class="mc-seg" data-seg="direction">'+[['all','全部'],['up','上涨'],['down','下跌']].map(x=>'<button data-v="'+x[0]+'" class="'+(f.direction===x[0]?'on':'')+'">'+x[1]+'</button>').join('')+'</div></div>';
  const lo=f.pctMin==null?-20:f.pctMin,hi=f.pctMax==null?20:f.pctMax;
  h+='<div class="mc-fgroup"><label>涨跌幅范围（%）</label><div class="mc-range"><div class="track"></div><div class="fill" id="D-fill"></div><input type="range" id="D-min" min="-20" max="20" step="0.5" value="'+lo+'"><input type="range" id="D-max" min="-20" max="20" step="0.5" value="'+hi+'"></div>';
  h+='<div class="mc-dual"><input id="D-min-num" type="number" min="-20" max="20" value="'+lo+'"><span>至</span><input id="D-max-num" type="number" min="-20" max="20" value="'+hi+'"></div>';
  h+='<div class="mc-quick">'+[['±3%',-3,3],['±5%',-5,5],['0~5%',0,5],['5~10%',5,10],['-5~0%',-5,0],['-10~-5%',-10,-5]].map(x=>'<button data-q="'+x[1]+','+x[2]+'">'+x[0]+'</button>').join('')+'</div></div>';
  h+='<div class="mc-fgroup"><label>面积指标</label><div class="mc-seg" data-seg="metric">'+MARKETS[STATE.market].metrics.map(x=>'<button data-v="'+x[0]+'" class="'+(STATE.areaMetric===x[0]?'on':'')+'">'+x[1]+'</button>').join('')+'</div></div>';
  h+='<div class="mc-fgroup"><label>缩略图</label><div class="mc-seg" data-seg="thumb">'+[['0','关'],['1','开']].map(x=>'<button data-v="'+x[0]+'" class="'+((STATE.settings.showThumbnail?1:0)==Number(x[0])?'on':'')+'">'+x[1]+'</button>').join('')+'</div></div>';
  h+='<div class="mc-fgroup"><label>名称 / 代码过滤</label><input id="D-keyword" class="mc-dual" style="width:100%;background:var(--bg-card);border:1px solid var(--border);border-radius:5px;padding:6px 8px" placeholder="输入名称或代码" value="'+esc(f.keyword)+'"></div>';
  h+='<button id="D-reset" style="width:100%;border:1px solid var(--border);border-radius:5px;padding:8px;font-size:12px;color:var(--dim)">重置全部筛选</button>';
  $('D-body').innerHTML=h;syncRange();
}
function syncRange(){
  const lo=Number($('D-min').value),hi=Number($('D-max').value);
  const fill=$('D-fill');if(fill){fill.style.left=((lo+20)/40*100)+'%';fill.style.width=((hi-lo)/40*100)+'%';}
}
function refreshAll(){renderSide();renderTreemap();}

/* ===================== 设置中心 ===================== */
const SET_TABS=[['appearance','外观'],['watchlist','自选'],['hotkeys','快捷键'],['help','帮助'],['changelog','更新日志'],['about','关于我们']];
const HOTKEYS=[['screenshot','C','截图分享'],['resetView','R','重置视图'],['fullscreen','F','全屏'],['openSettings',',','设置'],['toggleSidebar','B','收起/展开侧栏'],['toggleTheme','D','浅色/深色'],['toggleFilter','L','筛选'],['dockInspector','M','检视器停靠'],['pinInspector','G','检视器固定']];
function renderSettings(){
  $('E-tabs').innerHTML=SET_TABS.map(x=>'<button data-tab="'+x[0]+'" class="'+(STATE.settings.tab===x[0]?'on':'')+'">'+window.t('tab.'+x[0])+'</button>').join('');
  const st=STATE.settings,tab=st.tab||'appearance';let h='';
  if(tab==='appearance'){
    const seg=(key,opts)=>'<div class="mc-seg" data-set="'+key+'">'+opts.map(x=>'<button data-v="'+x[0]+'" class="'+(st[key]===x[0]?'on':'')+'">'+x[1]+'</button>').join('')+'</div>';
    h+='<div class="mc-setrow"><div class="lb">语言<small>界面文案语言</small></div>'+seg('language',[['zh','中文'],['en','English']])+'</div>';
    h+='<div class="mc-setrow"><div class="lb">显示模式<small>明暗主题（快捷键 D）</small></div>'+seg('theme',[['dark','暗黑模式'],['light','明亮模式']])+'</div>';
    h+='<div class="mc-setrow"><div class="lb">色板来源<small>PRD §4.11（默认） / 规范 §7.4</small></div>'+seg('palette',[['prd','PRD 标准（默认）'],['workbench','工作台规范 §7.4']])+'</div>';
    h+='<div class="mc-setrow"><div class="lb">板块标题栏统计<small>涨 N 跌 M 平均涨跌</small></div>'+seg('showGroupStat',[[true,'开'],[false,'关']])+'</div>';
    h+='<div class="mc-setrow"><div class="lb">筛选面板打开方式</div>'+seg('filterOpenMode',[['click','点击（推荐）'],['hover','悬停']])+'</div>';
    h+='<div class="mc-setrow"><div class="lb">强调色<small>选中态 / 按钮</small></div><div class="mc-swatches">'+Object.keys(ACCENTS).map(k=>'<i data-accent="'+k+'" class="'+(st.accent===k?'on':'')+'" style="background:'+ACCENTS[k]+'"></i>').join('')+'</div></div>';
    h+='<div class="mc-setrow"><div class="lb">涨跌极性<small>中国市场默认红涨绿跌</small></div>'+seg('polarity',[['redUp','红涨绿跌 +2.40%'],['greenUp','绿涨红跌 -1.80%']])+'</div>';
    h+='<div class="mc-setrow"><div class="lb">行情跳转<small>双击个股时打开的站点</small></div>'+seg('jumpSite',[['xueqiu','雪球'],['eastmoney','东方财富'],['ths','同花顺']])+'</div>';
    h+='<div style="margin-top:14px;font-size:12px;font-weight:700">热力图配色</div><div class="mc-themes" style="margin-top:8px">'+Object.keys(THEMES).map(k=>{
      const c=THEMES[k];return '<button data-theme-card="'+k+'" class="'+(st.colorTheme===k?'on':'')+'"><div class="g" style="background:linear-gradient(90deg,'+c[0]+',#8a8f98,'+c[1]+')"></div>'+k+'</button>';}).join('')+'</div>';
    h+='<div style="margin-top:14px"><button id="E-export" style="border:1px solid var(--border);border-radius:5px;padding:7px 12px;font-size:12px">导出主题配置</button> <span style="font-size:10px;color:var(--faint)">所有偏好即时生效并写入 mc_settings_proto_v1</span></div>';
  }else if(tab==='watchlist'){
    h+='<p style="font-size:12px;color:var(--dim)">通过股票名称或代码添加，生成你的专属热力图。上限 80 只。</p>';
    h+='<div style="display:flex;gap:6px"><input id="E-wl-input" placeholder="输入名称或代码" style="flex:1;background:var(--bg-card);border:1px solid var(--border);border-radius:5px;padding:7px 9px"><button id="E-wl-add" style="background:var(--accent,#9B5181);color:#fff;border-radius:5px;padding:0 12px">添加</button></div>';
    h+='<button id="E-wl-shot" style="width:100%;margin-top:8px;border:1px dashed var(--border);border-radius:5px;padding:9px;font-size:12px;color:var(--dim)">从截图添加（原型演示）</button>';
    h+='<button id="E-wl-back" style="margin-top:8px;border:1px solid var(--border);border-radius:5px;padding:6px 10px;font-size:11px;color:var(--dim)">返回 A 股全图</button>';
    h+='<h4 style="margin:14px 0 6px;font-size:12px">已添加 '+STATE.watchlist.length+' <span style="font-weight:400;color:var(--faint)">'+STATE.watchlist.length+'/80</span></h4>';
    h+=STATE.watchlist.length?STATE.watchlist.map(k=>{const it=findItem(k.split(':')[1]);return '<div class="mc-wl"><span>'+esc(it?it.name:k)+'</span><span style="color:var(--faint)">'+esc(k)+'</span><button class="x" data-wl-del="'+esc(k)+'">×</button></div>';}).join(''):'<div style="font-size:12px;color:var(--faint);padding:14px 0">还没有自选股票</div>';
  }else if(tab==='hotkeys'){
    h+='<p style="font-size:12px;color:var(--dim)">点击一项后按下新按键即可绑定；Esc 取消录制且不可重映射。固定导航键（↑↓/J/K/S/Esc）不可改写。</p>';
    h+=HOTKEYS.map(x=>'<div class="mc-setrow"><div class="lb" style="width:auto;flex:1">'+window.t('hk.'+x[0])+'</div><button data-rebind="'+x[0]+'" style="border:1px solid var(--border);border-radius:4px;padding:3px 10px;font-size:12px">'+(STATE.hotkeyRecord===x[0]?'请按键…':x[1])+'</button></div>').join('');
    h+='<button id="E-hk-reset" style="margin-top:12px;border:1px solid var(--border);border-radius:5px;padding:7px 12px;font-size:12px">恢复默认</button>';
  }else if(tab==='help'){
    h+='<h4 style="margin:0 0 8px;font-size:13px">使用帮助</h4><p style="font-size:11px;color:var(--faint);margin:0 0 10px">通过面积、颜色和行业分组快速阅读市场。</p><ul style="padding:0;margin:0">';
    ['面积代表当前面积口径（如总市值）','颜色代表涨跌幅度','开启缩略图可查看已筛选的一、二级板块统计','单击一级板块标题可聚焦；侧栏可同时选择多个板块；双击个股打开行情页','滚轮缩放查看细节','缩放后按住鼠标可拖动画布','悬浮个股后，可用 ↑/↓ 或 J/K 滚动弹窗列表','弹窗列表中按 S / Shift+S 切换排序'].forEach((t,i)=>h+='<li>'+(i+1)+'. '+t+'</li>');
    h+='</ul><h4 style="margin:16px 0 8px;font-size:13px">键盘快捷键（只读）</h4>'+HOTKEYS.map(x=>'<div class="mc-wl">'+window.t('hk.'+x[0])+'<span style="margin-left:auto;color:var(--dim)">'+x[1]+'</span></div>').join('');
  }else if(tab==='changelog'){
    h+=[['2026-11-18','v2.2 内嵌版','接入工作台 #/cloud 路由 / 侧栏一级入口 / 恢复 7 方法 4 事件契约'],['2026-11-18','v2.0 严格版','PRD §4.11 暗色默认 / iframe 嵌入 / 本地 ECharts 日K / 双语核心文案'],['2026-11-15','v1.0 原型','按 PRD 第 4 章完成五市场云图界面骨架、筛选抽屉、设置中心与四态'],['2026-11-15','v0.9','补充连续色阶、量程钳制与停牌斜纹'],['2026-11-14','v0.8','补充检视器停靠/固定与悬浮弹窗排行']].map(x=>'<div style="margin-bottom:14px"><div style="font-size:11px;color:var(--faint)">'+x[0]+' · '+x[1]+'</div><div style="font-size:12px;margin-top:4px;color:var(--dim)">'+x[2]+'</div></div>').join('');
  }else{
    h+='<h4 style="margin:0 0 10px">大盘云图 · 工作台独立界面</h4><p style="font-size:12px;color:var(--dim);line-height:1.9">版本：工作台内嵌版 v2.2（2026-11-18）<br>数据来源：东方财富 push2 行情快照（/api/market-cloud/overview）<br>行情延迟：由 asOf / session / stale 三个字段标注，非盘中时段显示盘后/休市<br>免责声明：本界面仅供个人研究与投教演示，不构成投资建议，不提供交易下单。<br>反馈入口：工作台内「帮助与反馈」。</p>';
  }
  $('E-pane').innerHTML=h;
}
function findItem(code){return DEMO_DATA[STATE.market].find(x=>x.code===code);}

/* ===================== 四态 / toast / 帮助 / 分享 ===================== */
function renderState(){
  const el=$('B-state'),t=STATE.dataState;el.className='mc-state'+(t==='default'?'':' on');
  document.querySelectorAll('.mc-states button').forEach(b=>b.classList.toggle('on',b.dataset.state===t));
  $('B-stale').style.display=t==='error'?'':'none';
  if(t==='loading'){let s='';for(let i=0;i<64;i++)s+='<i style="animation-delay:'+(i%8)*.12+'s"></i>';
    el.innerHTML='<div class="mc-skel">'+s+'</div><h3>海量数据加载中 <span class="num">68%</span></h3><p>方块面积代表规模，颜色代表涨跌幅；先选战场，再谈交易。</p>';}
  else if(t==='empty')el.innerHTML='<h3>无匹配标的</h3><p>当前筛选条件下没有可见标的，请调整板块、方向或涨跌幅区间。</p><button id="S-empty-reset">重置筛选</button>';
  else if(t==='error')el.innerHTML='<h3 style="color:var(--warn)">行情获取失败</h3><p>'+esc((window.MC_META&&window.MC_META.error)||'接口未返回数据')+'。本页不展示演示数据，避免与实盘混淆。</p><button id="S-error-retry">重试</button>';
}
function showToast(){
  try{if(localStorage.getItem('mc_notice_dismiss')==='1')return;}catch(e){}
  const t=$('H-toast');t.innerHTML='<button class="x" id="H-close">×</button><b>大盘云图工作台版上线</b><div style="color:var(--faint);font-size:10px;margin:3px 0">2026-11-18</div><p>五个市场视图与筛选、设置中心已可评审；行情来自东方财富 push2 实时快照。</p><label><input type="checkbox" id="H-forever"> 不再提示</label>';t.classList.add('on');
}
function renderHelp(){
  $('F-body').innerHTML='<h4 style="margin:0 0 8px;font-size:13px">鼠标操作</h4>'+
    [['滚轮','以光标为中心缩放画布'],['左键拖拽','缩放后平移画布'],['单击一级标题','聚焦分组，再次点击返回全局'],['单击叶子','打开检视器'],['双击叶子','回调工作台打开行情页'],['悬浮叶子','显示行情摘要弹窗']].map(x=>'<div class="mc-wl"><span style="width:96px">'+x[0]+'</span><span style="color:var(--dim)">'+x[1]+'</span></div>').join('')+
    '<h4 style="margin:16px 0 8px;font-size:13px">快捷键</h4>'+HOTKEYS.map(x=>'<div class="mc-wl">'+window.t('hk.'+x[0])+'<span style="margin-left:auto;color:var(--dim)">'+x[1]+'</span></div>').join('');
}
function renderShare(){
  $('G-body').innerHTML='<div style="background:var(--bg-card);border:1px solid var(--border);border-radius:6px;padding:12px;font-size:12px;line-height:1.9">'+
    '<div>市场：'+window.t('market.'+STATE.market)+'　面积口径：'+metricLabel()+'</div><div>视图：'+window.t('view.'+(VIEWS.find(v=>v[0]===STATE.view)||VIEWS[0])[0])+'　主题：'+(STATE.settings.theme==='dark'?'暗黑':'明亮')+'</div>'+
    '<div style="color:var(--faint);margin-top:6px">链接参数：market='+STATE.market+'&areaMetric='+STATE.areaMetric+'&view='+STATE.view+'</div></div>'+
    '<div style="display:flex;gap:8px;margin-top:14px"><button id="G-png" style="flex:1;background:var(--accent,#9B5181);color:#fff;border-radius:5px;padding:9px;font-size:12px">导出当前画布 PNG</button><button id="G-copy" style="flex:1;border:1px solid var(--border);border-radius:5px;padding:9px;font-size:12px">复制链接</button></div>'+
    '<p style="font-size:10px;color:var(--faint);margin-top:10px">原型演示：PNG 导出与分享链接为界面占位，正式实现由前端 canvas 截图 + 后端短链完成。</p>';
}
function toastMsg(msg){const t=$('H-toast');t.innerHTML='<button class="x" id="H-close">×</button><b>提示</b><p style="margin:8px 0 0">'+esc(msg)+'</p>';t.classList.add('on');}

