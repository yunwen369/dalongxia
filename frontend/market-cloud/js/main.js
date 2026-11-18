/* ===================== 事件绑定 ===================== */
function switchMarket(k){
  if(!MARKETS[k])return;
  STATE.market=k;STATE.view='all';STATE.areaMetric=MARKETS[k].metric;
  STATE.filters={groups:[],period:'day',direction:'all',pctMin:null,pctMax:null,keyword:''};
  STATE.drill={l1:null,l2:null};
  STATE.selected=null;STATE.inspector='hidden';
  $('market-cloud-root').className='mc-root'+(STATE.settings.sidebarCollapsed?' collapsed':'');
  $('C-inspector').className='mc-inspector';
  resetView();refreshAll();renderDrawer();renderCrumb();
  if(window.MC)MC.ensure(k,false,STATE.filters.period,'all');
}
function switchView(v){
  STATE.view=v;STATE.drill={l1:null,l2:null};
  resetView();
  /* 自选在本地按 localStorage 过滤，其余视图口径由后端下发 */
  var reqView=v==='watchlist'?'all':v;
  if(window.MC)MC.ensure(STATE.market,false,STATE.filters.period,reqView);else refreshAll();
  renderSide();renderCrumb();
}
function setSetting(k,v){STATE.settings[k]=v;save();applySettings();applyI18n();refreshAll();renderSettings();renderDrawer();}
function applySettings(){
  document.body.dataset.theme=STATE.settings.theme;document.body.dataset.palette=STATE.settings.palette;
  document.documentElement.style.setProperty('--accent',ACCENTS[STATE.settings.accent]||ACCENTS.purple);
  HALT_CACHE=getComputedStyle(document.body).getPropertyValue('--halt').trim()||'#3b4048';
  $('B-thumb').classList.toggle('on',!!STATE.settings.showThumbnail);
}
function closeLayers(){$('D-drawer').classList.remove('on');$('E-bg').classList.remove('on');$('F-help').classList.remove('on');$('G-bg').classList.remove('on');$('B-popover').classList.remove('on');}
/* 筛选面板：定位在左侧「筛选」按钮右侧，而不是全高抽屉（对齐站点） */
function openFilter(){
  const d=$('D-drawer'),btn=$('A-filter-entry');
  if(!d||!btn)return;
  const r=btn.getBoundingClientRect();
  const w=Math.min(340,window.innerWidth-176);
  d.style.width=w+'px';
  d.style.left=Math.max(8,Math.min(r.right+8,window.innerWidth-w-12))+'px';
  d.style.top=Math.max(8,Math.min(r.top-40,window.innerHeight-500))+'px';
  d.classList.add('on');
  renderDrawer();
  if(window.MCV)MCV.paint();
}
function cycleSort(back){const keys=RANK_TABS.map(x=>x[0]);let i=keys.indexOf(STATE.rankTab);i=(i+(back?-1:1)+keys.length)%keys.length;STATE.rankTab=keys[i];
  if(STATE.popCode){const it=findItem(STATE.popCode);if(it)renderPopover(it,parseInt($('B-popover').style.left)||20,parseInt($('B-popover').style.top)||20);}renderInspectorIfOpen();}
function renderInspectorIfOpen(){if(STATE.selected&&STATE.inspector!=='hidden'){const it=findItem(STATE.selected);if(it)renderInspector(it);}}

document.addEventListener('click',e=>{
  const t=e.target;
  if(t.closest('[data-close]')){const id=t.closest('[data-close]').dataset.close;
    if(id==='inspector'){STATE.inspector='hidden';STATE.selected=null;$('market-cloud-root').className='mc-root'+(STATE.settings.sidebarCollapsed?' collapsed':'');$('C-inspector').className='mc-inspector';renderTreemap();}
    else $(id).classList.remove('on');return;}
  const m=t.closest('[data-market]');if(m){switchMarket(m.dataset.market);return;}
  const v=t.closest('[data-view]');if(v){switchView(v.dataset.view);return;}
  const st=t.closest('[data-state]');if(st){STATE.dataState=st.dataset.state;renderState();return;}
  const seg=t.closest('[data-seg] button');if(seg){
    const key=seg.parentElement.dataset.seg,val=seg.dataset.v;
    if(key==='metric'){STATE.areaMetric=val;renderDrawer();refreshAll();return;}
    if(key==='thumb'){setSetting('showThumbnail',val==='1');renderDrawer();return;}
    if(key==='direction'){STATE.filters.direction=val;renderDrawer();refreshAll();return;}
    if(key==='period'){
      if(val===STATE.filters.period){renderDrawer();return;}
      STATE.filters.period=val;STATE.drill={l1:null,l2:null};
      renderDrawer();resetView();renderCrumb();
      /* 周/月/年由后端 f109/f110/f25 直接下发，切周期必须回源取对应口径 */
      if(window.MC)MC.ensure(STATE.market,false,val,STATE.view==='watchlist'?'all':STATE.view);
      else refreshAll();
      return;
    }
    renderDrawer();refreshAll();return;}
  const setb=t.closest('[data-set] button');if(setb){const k=setb.parentElement.dataset.set;let val=setb.dataset.v;
    if(k==='showGroupStat')val=val==='true';setSetting(k,val);return;}
  const ac=t.closest('[data-accent]');if(ac){setSetting('accent',ac.dataset.accent);return;}
  const thc=t.closest('[data-theme-card]');if(thc){setSetting('colorTheme',thc.dataset.themeCard);return;}
  const tab=t.closest('[data-tab]');if(tab){STATE.settings.tab=tab.dataset.tab;save();renderSettings();return;}
  const rb=t.closest('[data-rank]');if(rb){STATE.rankTab=rb.dataset.rank;if(STATE.popCode){const it=findItem(STATE.popCode);if(it)renderPopover(it,parseInt($('B-popover').style.left)||20,parseInt($('B-popover').style.top)||20);}renderInspectorIfOpen();return;}
  const rc=t.closest('[data-rank-code]');if(rc){const it=findItem(rc.dataset.rankCode);if(it){STATE.popCode=it.code;renderPopover(it,parseInt($('B-popover').style.left)||20,parseInt($('B-popover').style.top)||20);openInspector(it,STATE.inspector==='hidden'?'docked':STATE.inspector);}return;}
  const q=t.closest('[data-q]');if(q){const p=q.dataset.q.split(',');STATE.filters.pctMin=Number(p[0]);STATE.filters.pctMax=Number(p[1]);renderDrawer();refreshAll();return;}
  const crumb=t.closest('[data-crumb]');if(crumb){clearDrill(crumb.dataset.crumb);return;}
  const leaf=t.closest('.mc-leaf');if(leaf){const it=findItem(leaf.dataset.code);if(it)openInspector(it,STATE.inspector==='hidden'?'docked':STATE.inspector);return;}
  const wl=t.closest('[data-wl-del]');if(wl){STATE.watchlist=STATE.watchlist.filter(x=>x!==wl.dataset.wlDel);localStorage.setItem('mc_watchlist',JSON.stringify(STATE.watchlist));renderSettings();refreshAll();return;}
  const rebind=t.closest('[data-rebind]');if(rebind){STATE.hotkeyRecord=rebind.dataset.rebind;renderSettings();return;}
  const bid=(t.closest('button')||t).id;
  switch(bid){
    case 'A-filter-entry':{const d=$('D-drawer');if(d.classList.contains('on'))d.classList.remove('on');else openFilter();break;}
    case 'A-settings':closeLayers();$('E-bg').classList.add('on');renderSettings();break;
    case 'A-collapse':STATE.settings.sidebarCollapsed=!STATE.settings.sidebarCollapsed;save();$('market-cloud-root').classList.toggle('collapsed',STATE.settings.sidebarCollapsed);$('A-collapse').innerHTML=(STATE.settings.sidebarCollapsed?'▶':'◀')+' <span class="txt">'+(STATE.settings.sidebarCollapsed?'展开侧栏':'收起侧栏')+'</span> <span class="mc-kbd txt" style="margin-left:auto">B</span>';break;
    case 'A-tools':toastMsg('投研工具：选股器 / 行业对比 / 资金流，按工作台左侧一级目录跳转。');break;
    case 'A-pwa':toastMsg('添加到桌面（PWA）为原型占位，正式实现由工作台安装入口承载。');break;
    case 'B-help':closeLayers();$('F-help').classList.add('on');renderHelp();break;
    case 'B-theme':setSetting('theme',STATE.settings.theme==='dark'?'light':'dark');break;
    case 'B-thumb-toggle':setSetting('showThumbnail',!STATE.settings.showThumbnail);break;
    case 'B-full':if(!document.fullscreenElement){document.documentElement.requestFullscreen&&document.documentElement.requestFullscreen();}else{document.exitFullscreen&&document.exitFullscreen();}break;
    case 'B-share':closeLayers();$('G-bg').classList.add('on');renderShare();break;
    case 'B-zoom-in':setZoom(STATE.zoom*1.2);break;
    case 'B-zoom-out':setZoom(STATE.zoom/1.2);break;
    case 'B-fit':case 'B-reset':resetView();break;
    case 'D-reset':STATE.filters={groups:[],period:'day',direction:'all',pctMin:null,pctMax:null,keyword:''};STATE.areaMetric=MARKETS[STATE.market].metric;renderDrawer();refreshAll();break;
    case 'S-empty-reset':STATE.dataState='default';renderState();$('D-drawer').classList.add('on');break;
    case 'S-error-retry':STATE.dataState='default';renderState();if(window.MC)MC.reload();break;
    case 'C-watchlist':{const it=findItem(STATE.selected);if(it){const k=STATE.market+':'+it.code;if(STATE.watchlist.indexOf(k)<0&&STATE.watchlist.length<80){STATE.watchlist.push(k);localStorage.setItem('mc_watchlist',JSON.stringify(STATE.watchlist));toastMsg('已加入自选：'+it.name);}else toastMsg('已在自选或已达 80 只上限');}break;}
    case 'C-jump':toastMsg('行情跳转回调 onItemDoubleClick → 工作台行情页（站点：'+({xueqiu:'雪球',eastmoney:'东方财富',ths:'同花顺'}[STATE.settings.jumpSite])+'）；原型不内嵌第三方页面。');break;
    case 'E-wl-add':{const q=$('E-wl-input').value.trim();const it=q?DEMO_DATA[STATE.market].find(x=>x.name.indexOf(q)>=0||x.code.indexOf(q)>=0):null;
      if(!it){toastMsg('未找到匹配的标的');break;}
      const k=STATE.market+':'+it.code;if(STATE.watchlist.indexOf(k)>=0){toastMsg('已在自选');break;}
      if(STATE.watchlist.length>=80){toastMsg('已达 80 只上限');break;}
      STATE.watchlist.push(k);localStorage.setItem('mc_watchlist',JSON.stringify(STATE.watchlist));renderSettings();refreshAll();break;}
    case 'E-wl-shot':toastMsg('从截图识别添加为原型占位，正式实现需 OCR 与后端校验。');break;
    case 'E-wl-back':STATE.market='stock';STATE.view='all';STATE.areaMetric=MARKETS.stock.metric;closeLayers();resetView();refreshAll();break;
    case 'E-hk-reset':STATE.hotkeyRecord=null;toastMsg('已恢复默认快捷键映射。');renderSettings();break;
    case 'E-export':toastMsg('主题配置已导出（原型演示）：'+JSON.stringify({accent:STATE.settings.accent,polarity:STATE.settings.polarity,colorTheme:STATE.settings.colorTheme}));break;
    case 'G-png':toastMsg('PNG 导出为原型占位；正式实现导出当前画布并生成分享短链。');break;
    case 'G-copy':toastMsg('已复制带参数链接（原型演示）。');break;
    case 'H-close':$('H-toast').classList.remove('on');if($('H-forever')&&$('H-forever').checked)localStorage.setItem('mc_notice_dismiss','1');break;
    case 'E-bg':case 'G-bg':if(t.id===e.target.id)closeLayers();break;
  }
});
document.addEventListener('change',e=>{
  const t=e.target;
  if(t.dataset.group){const g=t.dataset.group;if(g==='__all__')STATE.filters.groups=[];
    else{const i=STATE.filters.groups.indexOf(g);if(t.checked&&i<0)STATE.filters.groups.push(g);if(!t.checked&&i>=0)STATE.filters.groups.splice(i,1);}
    renderDrawer();refreshAll();return;}
  if(t.id==='D-min'||t.id==='D-max'){
    let lo=Number($('D-min').value),hi=Number($('D-max').value);if(lo>hi){if(t.id==='D-min')hi=lo;else lo=hi;}
    STATE.filters.pctMin=lo;STATE.filters.pctMax=hi;renderDrawer();refreshAll();return;}
  if(t.id==='D-min-num'||t.id==='D-max-num'){
    let lo=Number($('D-min-num').value),hi=Number($('D-max-num').value);
    lo=Math.max(-20,Math.min(20,lo));hi=Math.max(-20,Math.min(20,hi));if(lo>hi){toastMsg('下限不能大于上限，已回退上一次有效筛选');renderDrawer();return;}
    STATE.filters.pctMin=lo;STATE.filters.pctMax=hi;renderDrawer();refreshAll();return;}
});
document.addEventListener('input',e=>{if(e.target.id==='D-keyword'){STATE.filters.keyword=e.target.value.trim();refreshAll();}});
/* ===================== 画布交互（命中检测走 MCV） ===================== */
let hoverKey=null,moveRaf=0,lastMoveEv=null,dragMoved=false;

function cursorFor(hit){
  const c=$('B-inner');if(!c)return;
  c.style.cursor=!hit?'grab':(hit.kind==='leaf'?'pointer':'zoom-in');
}
function handleHover(e){
  if(!window.MCV)return;
  const hit=MCV.hitTest(e.clientX,e.clientY);
  const key=!hit?null:(hit.kind==='leaf'?'L:'+hit.item.code:hit.kind+':'+(hit.name||'')+(hit.l1||''));
  if(key===hoverKey)return;
  hoverKey=key;
  MCV.setHover(hit);
  MCV.paint();
  const pop=$('B-popover');
  if(hit&&hit.kind==='leaf'){
    const it=findItem(hit.item.code);
    if(it){const r=$('B-canvas').getBoundingClientRect();renderPopover(it,e.clientX-r.left,e.clientY-r.top);}
  }else if(pop){pop.classList.remove('on');STATE.popCode=null;}
  cursorFor(hit);
}
$('B-inner').addEventListener('mousemove',e=>{
  if(dragMoved)return;
  lastMoveEv=e;
  if(moveRaf)return;
  moveRaf=requestAnimationFrame(()=>{moveRaf=0;if(lastMoveEv)handleHover(lastMoveEv);});
});
$('B-inner').addEventListener('mouseleave',()=>{
  hoverKey=null;if(window.MCV){MCV.setHover(null);MCV.paint();}
  $('B-popover').classList.remove('on');STATE.popCode=null;
});
$('B-inner').addEventListener('click',e=>{
  if(dragMoved||!window.MCV)return;
  const hit=MCV.hitTest(e.clientX,e.clientY);
  if(!hit)return;
  if(hit.kind==='leaf'){
    const it=findItem(hit.item.code);
    if(!it)return;
    if(window.MC_EMIT)MC_EMIT('onItemClick',{market:STATE.market,code:it.code,name:it.name});
    openInspector(it,STATE.inspector==='hidden'?'docked':STATE.inspector);
    return;
  }
  /* 单击分组标题 = 下钻；再次单击面包屑「全部」返回 */
  if(hit.kind==='g1')drillTo(hit.name,null);
  else if(hit.kind==='g2')drillTo(hit.l1,hit.name);
});
$('B-inner').addEventListener('dblclick',e=>{
  if(!window.MCV)return;
  const hit=MCV.hitTest(e.clientX,e.clientY);
  if(hit&&hit.kind==='leaf'){
    if(window.MC_EMIT)MC_EMIT('onItemDoubleClick',{market:STATE.market,code:hit.item.code,symbol:hit.item.code,site:STATE.settings.jumpSite});
    toastMsg('onItemDoubleClick：'+hit.item.name+'（'+hit.item.code+'）→ 工作台行情页，站点 '+STATE.settings.jumpSite);
  }
});
$('B-inner').addEventListener('wheel',e=>{
  e.preventDefault();
  const r=$('B-inner').getBoundingClientRect();
  setZoom(STATE.zoom*(e.deltaY<0?1.12:0.89),e.clientX-r.left,e.clientY-r.top);
},{passive:false});
$('B-inner').addEventListener('mousedown',e=>{
  if(e.button!==0)return;
  dragMoved=false;
  const sx=e.clientX,sy=e.clientY,tx=STATE.tx,ty=STATE.ty;
  const mv=ev=>{
    const dx=ev.clientX-sx,dy=ev.clientY-sy;
    if(!dragMoved&&Math.abs(dx)+Math.abs(dy)>3){dragMoved=true;$('B-inner').style.cursor='grabbing';}
    if(!dragMoved)return;
    STATE.tx=tx+dx;STATE.ty=ty+dy;applyZoom();
  };
  const up=()=>{
    document.removeEventListener('mousemove',mv);document.removeEventListener('mouseup',up);
    setTimeout(()=>{dragMoved=false;cursorFor(window.MCV?MCV.hitTest(e.clientX,e.clientY):null);},0);
  };
  document.addEventListener('mousemove',mv);document.addEventListener('mouseup',up);
});
document.addEventListener('keydown',e=>{
  if(STATE.hotkeyRecord){if(e.key==='Escape'){STATE.hotkeyRecord=null;toastMsg('已取消录制');renderSettings();}else{toastMsg('原型演示：动作 '+STATE.hotkeyRecord+' 已录制按键 '+e.key+'（冲突检测与持久化见正式实现）');STATE.hotkeyRecord=null;renderSettings();}e.preventDefault();return;}
  const tag=(e.target.tagName||'').toLowerCase();if(tag==='input'||tag==='textarea'){if(e.key==='Escape')closeLayers();return;}
  const k=e.key.toLowerCase();
  if(k==='escape'){
    const pop=$('B-popover');
    if(pop&&pop.classList.contains('on')){pop.classList.remove('on');STATE.popCode=null;return;}
    const dr=$('D-drawer');
    if(dr&&dr.classList.contains('on')){dr.classList.remove('on');return;}
    if(STATE.drill&&(STATE.drill.l1||STATE.drill.l2)){clearDrill(STATE.drill.l2?'l1':'all');return;}
    closeLayers();return;
  }
  if(k==='l'){const d=$('D-drawer');if(d.classList.contains('on'))d.classList.remove('on');else openFilter();return;}
  if(k==='b'){$('A-collapse').click();return;}
  if(k==='d'){setSetting('theme',STATE.settings.theme==='dark'?'light':'dark');return;}
  if(k==='f'){$('B-full').click();return;}
  if(k==='r'){resetView();return;}
  if(k==='c'){$('B-share').click();return;}
  if(k===','){$('A-settings').click();return;}
  if(k==='m'){if(STATE.inspector==='docked'){STATE.inspector='float';$('market-cloud-root').className='mc-root'+(STATE.settings.sidebarCollapsed?' collapsed':'');$('C-inspector').classList.add('float');}else if(STATE.selected){openInspector(findItem(STATE.selected),'docked');}return;}
  if(k==='g'){toastMsg('G：固定检视器位置与内容（原型演示：状态已记录）。');return;}
  if(k==='s'){cycleSort(e.shiftKey);return;}
  if(k==='j'||k==='arrowdown'||k==='k'||k==='arrowup'){
    const rows=Array.from(document.querySelectorAll('.mc-rank .r'));if(!rows.length)return;e.preventDefault();
    let i=rows.findIndex(r=>r.classList.contains('on'));i=(i+(k==='j'||k==='arrowdown'?1:-1)+rows.length)%rows.length;
    rows.forEach(r=>r.classList.remove('on'));rows[i].classList.add('on');rows[i].scrollIntoView({block:'nearest'});return;}
});
let resizeT=0;
window.addEventListener('resize',()=>{clearTimeout(resizeT);resizeT=setTimeout(renderTreemap,120);});

/* ===================== 启动 ===================== */
function init(){
  applySettings();STATE.settings.tab='appearance';
  if(!STATE.drill)STATE.drill={l1:null,l2:null};
  renderSide();renderTreemap();renderDrawer();renderSettings();renderState();renderHelp();renderShare();renderCrumb();
  applyI18n();
}
window.addEventListener('load',()=>{applySettings();applyI18n();renderTreemap();renderCrumb();});
init();
