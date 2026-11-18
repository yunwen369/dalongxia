/* ===================== 状态（模块私有，mc_ 前缀持久化） ===================== */
const MC_KEY='mc_settings_proto_v1';
const DEFAULT_SETTINGS={theme:'dark',palette:'prd',accent:'red',polarity:'redUp',colorTheme:'classic',
  showGroupStat:true,filterOpenMode:'click',language:'zh',jumpSite:'xueqiu',showThumbnail:false,refreshInterval:60};
let saved={};try{saved=JSON.parse(localStorage.getItem(MC_KEY)||'{}');}catch(e){saved={};}
const STATE={market:'stock',view:'all',areaMetric:'marketCapTotal',filters:{groups:[],period:'day',direction:'all',pctMin:null,pctMax:null,keyword:''},
  settings:Object.assign({},DEFAULT_SETTINGS,saved),zoom:1,tx:0,ty:0,focus:null,selected:null,inspector:'hidden',
  drill:{l1:null,l2:null},
  dataState:'default',sortBy:'changePct',popCode:null,rankTab:'changePct',hotkeyRecord:null,
  watchlist:(function(){try{return JSON.parse(localStorage.getItem('mc_watchlist')||'[]')}catch(e){return []}})()};
const $=id=>document.getElementById(id);
const save=()=>{try{localStorage.setItem(MC_KEY,JSON.stringify(STATE.settings));}catch(e){}};
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmtPct=p=>p==null?'--':(p>0?'+':'')+p.toFixed(2)+'%';
const pctColor=p=>p==null?'var(--dim)':(Math.abs(p)<=0.05?'var(--dim)':((STATE.settings.polarity==='redUp'?p>0:p<0)?'var(--up)':'var(--down)'));
const fmtCap=v=>v==null?'--':(v>=10000?(v/10000).toFixed(2)+'万亿':v.toFixed(2)+'亿');
const metricLabel=()=>{const m=MARKETS[STATE.market].metrics.find(x=>x[0]===STATE.areaMetric);return m?m[1]:'--';};

