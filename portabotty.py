#!/usr/bin/env python3
"""Portabotty web gateway: the web console (live image, trackpad, keyboard) and MCP over HTTP, in front of PBKEY and PBSEE.
Normally started by PBKEY (pbk). Standalone, without PBKEY:  python3 portabotty.py [--listen ADDR] [--port N] [--token T]
Details: README.md
"""
import os, sys, base64, json, time, socket, secrets, hmac, argparse, threading
import urllib.parse, urllib.request, urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
KB_PORT, SB_PORT, DB_PORT = 8809, 8808, 8800
KB_URL = os.environ.get("PBKEY_URL", f"http://127.0.0.1:{KB_PORT}").rstrip("/")
SB_URL = os.environ.get("PBSEE_URL", f"http://127.0.0.1:{SB_PORT}").rstrip("/")
CONFIG = os.environ.get("PORTABOTTY_CONFIG", os.path.join(HERE, "portabotty.json"))
KB_PATHS = {"status", "use", "drop", "solo", "rename", "mouse", "type", "key", "goto", "click", "scroll", "drag", "move", "home"}
SB_GET = {"cams", "status", "stream", "snap"}
SB_POST = {"calibrate", "save"}
SEE = {}          # set by PBKEY: {"state": () -> bool, "set": (bool) -> bool}  (camera server on/off)


WHICH = {"k": "kb", "key": "kb", "kb": "kb", "pbk": "kb", "pbkb": "kb",
         "s": "sb", "see": "sb", "sb": "sb", "pbs": "sb", "c": "sb", "cam": "sb", "pbc": "sb"}


# ---------------------------------------------------------------- config / tokens
def load_cfg():
    try:
        with open(CONFIG) as f: return json.load(f)
    except (OSError, ValueError):
        return {}


def save_cfg(cfg):
    fd = os.open(CONFIG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f: json.dump(cfg, f, indent=1)
    uid = os.environ.get("SUDO_UID")                       # written under sudo: keep it readable by you
    if uid:
        try: os.chown(CONFIG, int(uid), int(os.environ.get("SUDO_GID", uid)))
        except OSError: pass


def kb_token():
    t = os.environ.get("PBKEY_TOKEN")
    if t: return t.strip()
    for p in (os.environ.get("PBKEY_TOKEN_FILE"), os.path.expanduser("~/.pbkey_token"), os.path.join(HERE, ".pbkey_token")):
        if p:
            try: return open(p).read().strip()
            except OSError: pass
    return ""


def sb_token():
    t = os.environ.get("PBSEE_TOKEN")
    if t: return t.strip()
    try:
        with open(os.environ.get("PBSEE_CONFIG", os.path.join(HERE, "pbsee.json"))) as f:
            return json.load(f).get("token", "")
    except (OSError, ValueError):
        return ""


def port_open(url):
    u = urllib.parse.urlparse(url)
    try:
        with socket.create_connection((u.hostname, u.port), timeout=0.4): return True
    except OSError:
        return False


# ---------------------------------------------------------------- status
def http_json(url, headers=None, timeout=3):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read())


def hub_status():
    kb_up, sb_up = port_open(KB_URL), port_open(SB_URL)
    status, cam = None, "none"
    if kb_up:
        try: status = http_json(KB_URL + "/status", {"Authorization": "Bearer " + kb_token()})
        except Exception: status = None
    if sb_up:
        try:
            cams = http_json(SB_URL + "/status", {"Authorization": "Bearer " + sb_token()}).get("cams", [])
            if any(c.get("fps", 0) > 0 and (c.get("frame_age") is None or c["frame_age"] < 3) for c in cams): cam = "live"
            elif any(c.get("present") for c in cams): cam = "idle"
        except Exception:
            pass
    return {"kb": kb_up, "sb": sb_up, "cam": cam, "status": status, "links": load_cfg().get("links", {}),
            "see_on": SEE.get("state", lambda: None)()}


# ---------------------------------------------------------------- gateway (web console + proxies)
CONSOLE = r"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover,user-scalable=no">
<title>Portabotty</title>
<style>
:root{--bg:#0b0c0e;--panel:#141519;--raise:#1c1e23;--line:#272a31;--fg:#eceef2;--dim:#8a8f9a;--acc:#5aa9ff;--acc2:rgba(90,169,255,.16);--ok:#3ecf74;--warn:#e6b84a;--bad:#ff6363;--r:12px}
@media (prefers-color-scheme:light){:root{--bg:#f2f3f6;--panel:#fff;--raise:#f4f5f8;--line:#dfe1e7;--fg:#14161b;--dim:#6a6f7b;--acc:#1a73e8;--acc2:rgba(26,115,232,.12)}}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--fg);font:15px/1.35 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,system-ui,sans-serif;display:flex;flex-direction:column;overscroll-behavior:none;-webkit-user-select:none;user-select:none}
button{font:inherit;color:var(--fg);background:var(--raise);border:1px solid var(--line);border-radius:10px;height:38px;min-width:38px;padding:0 12px;cursor:pointer;touch-action:manipulation;transition:background .12s,border-color .12s,color .12s;display:inline-flex;align-items:center;justify-content:center;gap:6px;flex:none}
button:hover{border-color:var(--dim)}button:active{background:var(--line)}button.on{border-color:var(--acc);color:var(--acc);background:var(--acc2)}
button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid var(--acc);outline-offset:1px}
svg.i{width:19px;height:19px;stroke:currentColor;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
#bar{display:flex;align-items:center;gap:8px;padding:9px max(12px,env(safe-area-inset-left)) 9px max(12px,env(safe-area-inset-left));background:color-mix(in srgb,var(--panel) 88%,transparent);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border-bottom:1px solid var(--line);position:relative;z-index:5}
.brand{font-weight:650;letter-spacing:.2px;margin-right:2px}
.pill{display:inline-flex;align-items:center;gap:6px;height:26px;padding:0 10px;border-radius:99px;background:var(--raise);border:1px solid var(--line);color:var(--dim);font-size:12.5px;flex:none}
.pill i,.dot{width:8px;height:8px;border-radius:50%;background:var(--bad);flex:none}.pill.up i,.dot.up{background:var(--ok)}.pill.idle i,.dot.idle{background:var(--warn)}.dot.off{background:var(--dim)}
#dev{display:flex;gap:6px;overflow-x:auto;flex:1;min-width:0;scrollbar-width:none;padding:1px}#dev::-webkit-scrollbar{display:none}
.chip{white-space:nowrap;height:36px;border-radius:99px;padding:0 14px 0 12px}.chip .dot{background:var(--dim)}.chip.up .dot{background:var(--ok)}.chip.act{border-color:var(--acc);background:var(--acc2)}
.chip small{color:var(--dim);font-size:11.5px;max-width:90px;overflow:hidden;text-overflow:ellipsis}
#stage{position:relative;flex:1;min-height:0;display:flex;align-items:center;justify-content:center;touch-action:none;overflow:hidden;padding:14px;background:radial-gradient(1200px 600px at 50% 0%,color-mix(in srgb,var(--acc) 7%,transparent),transparent 70%)}
#wrap{position:relative;line-height:0;max-width:100%;max-height:100%;border-radius:14px;overflow:hidden;box-shadow:0 10px 40px rgba(0,0,0,.35);border:1px solid var(--line)}
#cam{max-width:100%;max-height:calc(100vh - 100px - var(--tray,0px));display:block;-webkit-user-drag:none;pointer-events:none;background:#000}
#cam:not([src]){display:none}#wrap:has(#cam:not([src])){display:none}
#ptr{position:absolute;width:22px;height:22px;margin:-11px 0 0 -11px;border:2px solid var(--acc);border-radius:50%;pointer-events:none;opacity:.9;display:none;box-shadow:0 0 0 4px var(--acc2)}
#pad{position:absolute;inset:0;display:none;border:2px dashed var(--acc);border-radius:14px;pointer-events:none;opacity:.5}#stage.pad #pad{display:block}
#none{position:absolute;display:flex;flex-direction:column;align-items:center;gap:12px;color:var(--dim);text-align:center}#none:empty{display:none}
#calbar{position:absolute;left:50%;top:16px;transform:translateX(-50%);display:none;align-items:center;gap:10px;padding:8px 8px 8px 16px;background:var(--panel);border:1px solid var(--acc);border-radius:99px;z-index:4;box-shadow:0 6px 24px rgba(0,0,0,.4);line-height:1.2;white-space:nowrap}#calbar.on{display:flex}
.mk{position:absolute;width:16px;height:16px;margin:-8px 0 0 -8px;border-radius:50%;background:var(--acc);border:2px solid #fff;pointer-events:none;font:700 10px/12px system-ui;color:#fff;text-align:center}
#stage.cal #wrap{cursor:crosshair;outline:2px solid var(--acc)}
#dock{display:none;background:var(--panel);border-top:1px solid var(--line);padding:8px max(10px,env(safe-area-inset-left)) max(10px,env(safe-area-inset-bottom));flex:none}#dock.open{display:block}
#dtabs{display:flex;gap:6px;margin-bottom:8px;align-items:center}#dtabs .seg{flex:none}#dtabs span{flex:1}
#tray{display:flex;flex-wrap:wrap;gap:6px;max-width:760px;margin:0 auto}#tray button{flex:1 0 44px;height:42px}
#board{display:flex;flex-direction:column;gap:5px;max-width:760px;margin:0 auto}.kr{display:flex;gap:5px}
.kr button{flex:1 1 0;min-width:0;height:40px;padding:0;font-size:14px;border-radius:8px;transition:background .08s,transform .08s}.kr button.w15{flex:1.5}.kr button.w2{flex:2}.kr button.w5{flex:5}
.kr button.fl{background:var(--acc2);border-color:var(--acc);transform:scale(.94)}
#tpd{max-width:760px;margin:0 auto}#tpad{position:relative;height:170px;border:1px solid var(--line);border-radius:14px;background:var(--raise);touch-action:none;overflow:hidden}
#tpad:after{content:"";position:absolute;inset:0;background:radial-gradient(circle at 50% 50%,var(--acc2),transparent 60%);opacity:.5;pointer-events:none}
#tdot{position:absolute;width:26px;height:26px;margin:-13px 0 0 -13px;border-radius:50%;background:var(--acc);opacity:0;pointer-events:none;transition:opacity .15s;z-index:1}
#tbtn{display:flex;gap:6px;margin-top:6px}#tbtn button{flex:1;height:40px}
#kb{position:fixed;left:0;top:0;width:1px;height:1px;opacity:0;border:0;padding:0}
/* link sheet */
#sheet{position:fixed;inset:0;z-index:20;display:none}#sheet.open{display:block}
#scrim{position:absolute;inset:0;background:rgba(0,0,0,.5);animation:f .15s}
#panel{position:absolute;background:var(--panel);border:1px solid var(--line);display:flex;flex-direction:column;box-shadow:0 20px 60px rgba(0,0,0,.5);-webkit-user-select:none;user-select:none}
@media(min-width:720px){#panel{top:0;right:0;bottom:0;width:480px;border-width:0 0 0 1px;animation:sr .18s}}
@media(max-width:719px){#panel{left:0;right:0;bottom:0;max-height:88vh;border-radius:20px 20px 0 0;animation:su .2s}.brand{display:none}}
@keyframes f{from{opacity:0}}@keyframes sr{from{transform:translateX(30px);opacity:0}}@keyframes su{from{transform:translateY(40px);opacity:0}}
#ph{display:flex;align-items:center;justify-content:space-between;padding:14px 16px 8px;font-weight:650;font-size:16px}
#pb{overflow:auto;padding:4px 16px max(18px,env(safe-area-inset-bottom));-webkit-overflow-scrolling:touch}
#cols{position:relative;display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);column-gap:44px;align-items:start}
.col h4{margin:8px 2px 10px;font-size:11.5px;letter-spacing:.9px;text-transform:uppercase;color:var(--dim);font-weight:600;display:flex;align-items:center;justify-content:space-between;min-height:24px}
.row{position:relative;background:var(--raise);border:1px solid var(--line);border-radius:var(--r);margin-bottom:9px;transition:border-color .12s,box-shadow .12s}
.row.sel{border-color:var(--acc);box-shadow:0 0 0 3px var(--acc2)}.row.drop{border-color:var(--ok);box-shadow:0 0 0 3px rgba(62,207,116,.18)}
.main{display:flex;align-items:center;gap:9px;padding:0 10px;min-height:48px;cursor:pointer}
.nm{flex:1;min-width:0;overflow:hidden}.nm b{display:block;font-weight:550;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.nm small{display:block;color:var(--dim);font-size:11.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.chev{height:28px;min-width:28px;padding:0;border:0;background:none;color:var(--dim);transition:transform .15s}.row.ex .chev{transform:rotate(180deg)}
.port{position:absolute;top:16px;width:16px;height:16px;border-radius:50%;background:var(--panel);border:2px solid var(--dim);cursor:grab;touch-action:none;z-index:2;transition:border-color .12s,background .12s}
.port:hover,.row.sel .port{border-color:var(--acc);background:var(--acc)}.col.d .port{right:-9px}.col.v .port{left:-9px}.port.linked{border-color:var(--acc);background:var(--acc)}
.more{display:none;padding:2px 12px 12px;border-top:1px solid var(--line);margin-top:2px}.row.ex .more{display:block}
.set{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:6px 10px;padding:8px 0 0;font-size:13.5px;color:var(--dim)}.set>span{white-space:nowrap}
.set button{height:32px;font-size:13px;min-width:0}.btns{display:flex;gap:6px;flex-wrap:wrap;padding-top:10px}.btns button{height:32px;font-size:13px}
.seg{display:flex;border:1px solid var(--line);border-radius:9px;overflow:hidden}.seg button{border:0;border-radius:0;height:30px;padding:0 8px;background:none;font-size:12.5px}.seg button.on{background:var(--acc2)}
input[type=text],#sn{font:inherit;background:var(--raise);color:var(--fg);border:1px solid var(--line);border-radius:8px;height:32px;padding:0 10px;min-width:0;flex:1;-webkit-user-select:text;user-select:text}#srv{padding:0 0 12px;gap:8px;flex-wrap:nowrap;align-items:center}
input[type=range]{width:100%;max-width:130px;accent-color:var(--acc)}select{font:inherit;background:var(--raise);color:var(--fg);border:1px solid var(--line);border-radius:8px;height:30px}
.sw{position:relative;width:38px;height:22px;flex:none}.sw input{position:absolute;opacity:0;inset:0;margin:0;cursor:pointer}.sw s{position:absolute;inset:0;border-radius:99px;background:var(--line);transition:background .15s;pointer-events:none}
.sw s:after{content:"";position:absolute;left:3px;top:3px;width:16px;height:16px;border-radius:50%;background:#fff;transition:transform .15s}.sw input:checked+s{background:var(--acc)}.sw input:checked+s:after{transform:translateX(16px)}
#lines{position:absolute;left:0;top:0;width:100%;height:100%;pointer-events:none;overflow:visible;z-index:1}
#lines path{fill:none;stroke:var(--acc);stroke-width:2.2;stroke-linecap:round}#lines path.g{stroke-dasharray:5 5;opacity:.9}
#lines g{pointer-events:all;cursor:pointer}#lines circle{fill:var(--panel);stroke:var(--acc);stroke-width:1.6}#lines text{fill:var(--acc);font:600 11px system-ui;text-anchor:middle;dominant-baseline:central}
.empty{color:var(--dim);font-size:13px;padding:6px 2px}
</style>
<div id=bar><span class=brand id=brand>Portabotty</span><span class=pill id=kbi><i></i>key</span><span class=pill id=sbi><i></i>see</span><div id=dev></div>
<button id=mode aria-label=mode></button><button id=kbd aria-label=keyboard></button><button id=tr aria-label=keys></button><button id=lk aria-label=link></button></div>
<div id=stage><div id=none><span id=nt></span><button id=ns style=display:none>Start camera</button></div><div id=wrap><img id=cam alt=""><i id=ptr></i><div id=pad></div></div><div id=calbar><span id=ct></span><button id=cx>Cancel</button></div></div>
<div id=dock><div id=dtabs><div class=seg><button id=tabt>Tray</button><button id=tabk>Keys</button><button id=tabp>Pad</button></div><span></span></div>
<div id=tray></div><div id=board></div><div id=tpd style=display:none><div id=tpad><i id=tdot></i></div><div id=tbtn><button id=tl>Left</button><button id=tm>Middle</button><button id=trr>Right</button></div></div></div><input id=kb autocapitalize=off autocorrect=off autocomplete=off spellcheck=false aria-label=keyboard>
<div id=sheet><div id=scrim></div><div id=panel><div id=ph><span>Link</span><button id=pc aria-label=close>✕</button></div><div id=pb>
<div class=set id=srv><span>This server</span><input id=sn maxlength=60 aria-label=name><button id=sv>Save</button></div>
<div id=cols><svg id=lines></svg><div class="col d"><h4>Devices</h4><div id=ld></div></div><div class="col v"><h4><span>Views</span><label class=sw title=camera><input type=checkbox id=seesw><s></s></label></h4><div id=lv></div></div></div></div></div></div>
<script>
const T=new URLSearchParams(location.search).get('t')||'',$=id=>document.getElementById(id);
const U=p=>p+(T?(p.includes('?')?'&':'?')+'t='+encodeURIComponent(T):'');
const ls={get:k=>{try{return localStorage.getItem(k)}catch(e){return null}},set:(k,v)=>{try{localStorage.setItem(k,v)}catch(e){}}};
const IC={mode:'<svg class=i viewBox="0 0 24 24"><circle cx=12 cy=12 r=8 /><circle cx=12 cy=12 r=2.5 /></svg>',pad:'<svg class=i viewBox="0 0 24 24"><rect x=3 y=6 width=18 height=12 rx=3 /><path d="M9 14h6"/></svg>',
 kbd:'<svg class=i viewBox="0 0 24 24"><rect x=2.5 y=6 width=19 height=12 rx=2.5 /><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/></svg>',
 tr:'<svg class=i viewBox="0 0 24 24"><path d="M5 12h.01M12 12h.01M19 12h.01" stroke-width=3 /></svg>',
 lk:'<svg class=i viewBox="0 0 24 24"><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/></svg>',
 chev:'<svg class=i viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></svg>'};
$('kbd').innerHTML=IC.kbd;$('tr').innerHTML=IC.tr;$('lk').innerHTML=IC.lk;
// ---- api (serialised so keys keep their order)
let chain=Promise.resolve();
const raw=(p,b)=>fetch(U('/kb/'+p),{method:b===undefined?'GET':'POST',body:b===undefined?undefined:JSON.stringify(b)}).then(r=>r.json()).catch(()=>({ok:false}));
const send=(p,b)=>chain=chain.then(()=>raw(p,b));
const post=(p,b)=>fetch(U(p),{method:'POST',body:JSON.stringify(b||{})}).then(r=>r.json()).catch(()=>({}));
// ---- state
let hub={},devs=[],cams=[],links={},viewCam=ls.get('view')||'',lastActive=null,sel=null,expanded=new Set(),sig='',nonce=0,tick=0,camKey='',cal=null,shownKey='';
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const camOf=n=>cams.find(c=>c.name===n),devOf=a=>devs.find(d=>d.addr===a);
function chips(){
  const el=$('dev');el.textContent='';
  devs.forEach(d=>{const b=document.createElement('button');b.className='chip'+(d.connected?' up':'')+(d.active?' act':'');
    const c=links[d.addr];b.innerHTML='<i class=dot></i><span>'+esc(d.name)+'</span>'+(c?'<small>'+esc(c)+'</small>':'');
    b.onclick=()=>send('use',{device:d.n}).then(poll);el.append(b)});
}
function poll(){
  fetch(U('/hub')).then(r=>r.json()).then(h=>{
  hub=h;const s=h.status||{};devs=s.devices||[];links=h.links||{};if(s.name&&s.name!==$('brand').textContent){$('brand').textContent=s.name;document.title=s.name}
  $('kbi').className='pill'+(h.kb?' up':'');$('sbi').className='pill'+(h.sb?(h.cam==='live'?' up':' idle'):'');
  if(s.pointer&&!cal){$('ptr').style.cssText='display:block;left:'+s.pointer[0]*100+'%;top:'+s.pointer[1]*100+'%'}else $('ptr').style.display='none';
  if(s.active!==lastActive){lastActive=s.active;if(links[s.active]){viewCam=links[s.active];ls.set('view',viewCam)}}
  const done=()=>{chips();stream();placeholder();render()};
  if(h.sb&&(!cams.length||tick++%4===0)){fetch(U('/cams')).then(r=>r.json()).then(c=>{cams=c;if(!camOf(viewCam)||!camOf(viewCam).present){const p=cams.find(x=>x.present)||cams[0];viewCam=p?p.name:''}done()}).catch(done)}
  else{if(!h.sb)cams=[];done()}
 }).catch(()=>{$('kbi').className=$('sbi').className='pill'})}
function stream(force){
  if(cal)return;const c=camOf(viewCam),o=c&&c.opts||{};
  const key=hub.sb&&c&&c.present?viewCam+'|'+JSON.stringify(o)+'|'+nonce:'';
  if(key===camKey&&!force)return;camKey=key;
  if(key)$('cam').src=U('/stream/'+encodeURIComponent(viewCam)+'?_='+nonce);else $('cam').removeAttribute('src');
}
$('cam').onerror=()=>{camKey='';$('cam').removeAttribute('src')};
function placeholder(){
  let t='',b=false;
  if(!hub.kb&&!hub.sb)t='offline';
  else if(!hub.sb){t=hub.see_on===false?'camera off':'starting camera…';b=hub.see_on===false}
  else if(!cams.length)t='no camera found';
  else if(!camOf(viewCam)||!camOf(viewCam).present)t='camera not connected';
  $('nt').textContent=t;$('ns').style.display=b?'':'none';
}
$('ns').onclick=()=>post('/see',{on:true}).then(()=>setTimeout(poll,800));
setInterval(poll,1500);poll();
// ---- modes
let mode=(ls.get('mode')||(matchMedia('(pointer:coarse)').matches?'pad':'direct'))==='pad'?'pad':'direct';
function setMode(m){mode=m;ls.set('mode',m);$('stage').classList.toggle('pad',m==='pad');$('mode').innerHTML=m==='pad'?IC.pad:IC.mode;$('mode').classList.toggle('on',m==='pad')}
$('mode').onclick=()=>setMode(mode==='pad'?'direct':'pad');setMode(mode);
// ---- pointer
const frac=(x,y)=>{const r=$('cam').getBoundingClientRect();return[Math.min(1,Math.max(0,(x-r.left)/r.width)),Math.min(1,Math.max(0,(y-r.top)/r.height))]};
const P=new Map();let g=null,mvAcc=[0,0],mvT=0,scAcc=0;
function flushMove(force){const n=performance.now();if(!force&&n-mvT<25)return;if(mvAcc[0]||mvAcc[1]){const dx=Math.round(mvAcc[0]),dy=Math.round(mvAcc[1]);mvAcc[0]-=dx;mvAcc[1]-=dy;mvT=n;send('move',{dx,dy})}}
function scroll(px){scAcc+=px;const n=Math.trunc(scAcc/28);if(n){scAcc-=n*28;send('scroll',{dir:n>0?'down':'up',amount:Math.abs(n)})}}
$('stage').addEventListener('pointerdown',e=>{e.preventDefault();if(cal||e.target.closest('#calbar,#none button'))return;$('stage').setPointerCapture(e.pointerId);P.set(e.pointerId,{x:e.clientX,y:e.clientY});
  if(P.size===1)g={x0:e.clientX,y0:e.clientY,t0:performance.now(),moved:0,multi:false,type:e.pointerType,cy:e.clientY};
  else if(g){g.multi=true;g.cy=[...P.values()].reduce((a,p)=>a+p.y,0)/P.size}
  if(g&&g.type==='mouse'&&e.button===2)g.right=true;focusKb()});
$('stage').addEventListener('pointermove',e=>{const p=P.get(e.pointerId);if(!p||!g)return;const dx=e.clientX-p.x,dy=e.clientY-p.y;p.x=e.clientX;p.y=e.clientY;
  g.moved=Math.max(g.moved,Math.hypot(e.clientX-g.x0,e.clientY-g.y0));
  if(P.size>=2){const cy=[...P.values()].reduce((a,q)=>a+q.y,0)/P.size;scroll(g.cy-cy);g.cy=cy;g.scrolled=true;return}
  if(mode==='pad'&&!g.multi){mvAcc[0]+=dx*1.8;mvAcc[1]+=dy*1.8;flushMove()}});
function end(e){const p=P.get(e.pointerId);if(!p||!g)return;P.delete(e.pointerId);
  if(P.size>0)return;const dt=performance.now()-g.t0,tap=g.moved<10&&dt<600&&!g.scrolled;flushMove(true);
  const[x,y]=frac(e.clientX,e.clientY);
  if(g.multi){if(tap&&!g.scrolled)send('click',{button:'right'})}
  else if(mode==='pad'){if(tap)send('click',{button:g.right?'right':'left'});else if(g.moved<10&&dt>=600)send('click',{button:'right'})}
  else if(tap)send('click',{x,y,button:g.right?'right':'left'});
  else if(g.moved<10)send('click',{x,y,button:'right'});
  else{const[x1,y1]=frac(g.x0,g.y0);send('drag',{x1,y1,x2:x,y2:y,duration:Math.min(1,Math.max(.15,dt/1000)),steps:10})}
  g=null}
$('stage').addEventListener('pointerup',e=>{if(cal)return calPt(e);end(e)});$('stage').addEventListener('pointercancel',e=>{P.delete(e.pointerId);g=null});
$('stage').addEventListener('contextmenu',e=>e.preventDefault());
$('stage').addEventListener('wheel',e=>{e.preventDefault();scroll(e.deltaY)},{passive:false});
// ---- calibrate (inline)
function startCal(){const c=camOf(viewCam);if(!c)return;closeSheet();cal={name:viewCam,pts:[]};
  $('stage').classList.add('cal');$('calbar').classList.add('on');$('ct').textContent='Tap screen corners: top-left, top-right, bottom-right, bottom-left';
  $('cam').src=U('/stream/'+encodeURIComponent(viewCam)+'?raw=1&w=960&_='+Date.now())}
function endCal(){cal=null;$('stage').classList.remove('cal');$('calbar').classList.remove('on');document.querySelectorAll('.mk').forEach(m=>m.remove());camKey='';nonce++;poll()}
function calPt(e){const[x,y]=frac(e.clientX,e.clientY);cal.pts.push(x,y);const r=$('cam').getBoundingClientRect(),w=$('wrap').getBoundingClientRect();
  const m=document.createElement('i');m.className='mk';m.textContent=cal.pts.length/2;m.style.left=(e.clientX-w.left)+'px';m.style.top=(e.clientY-w.top)+'px';$('wrap').append(m);
  $('ct').textContent='corner '+cal.pts.length/2+' of 4';
  if(cal.pts.length===8){const n=cal.name,p=cal.pts;post('/calibrate/'+encodeURIComponent(n),{warp:p}).then(()=>{endCal()})}}
$('cx').onclick=endCal;
// ---- keyboard
const NAMES={Enter:'enter',Escape:'esc',Tab:'tab',Backspace:'backspace',Delete:'delete',ArrowUp:'up',ArrowDown:'down',ArrowLeft:'left',ArrowRight:'right',Home:'home',End:'end',PageUp:'pageup',PageDown:'pagedown',' ':'space'};
for(let i=1;i<=12;i++)NAMES['F'+i]='f'+i;
const sticky=new Set();let typed='',tt=0;
function clearSticky(){sticky.clear();document.querySelectorAll('[data-m]').forEach(b=>b.classList.remove('on'))}
function type(s){if(s.length===1)flash(s===' '?'space':s.toLowerCase());if(sticky.size){const m=[...sticky];clearSticky();s.split('').forEach(c=>send('key',{key:m.concat(c).join('+')}));return}
  typed+=s;clearTimeout(tt);tt=setTimeout(()=>{const t=typed;typed='';send('type',{text:t})},15)}
function key(name,extra){const m=[...(extra||[]),...sticky];clearSticky();flushTyped();flash(name);send('key',{key:m.concat(name).join('+')})}
function flushTyped(){if(typed){clearTimeout(tt);const t=typed;typed='';send('type',{text:t})}}
document.addEventListener('keydown',e=>{
  if($('sheet').classList.contains('open')){if(e.key==='Escape')closeSheet();return}
  if(e.isComposing||e.key==='Process'||e.key==='Dead'||e.key==='Unidentified')return;
  if(e.key==='F5'||e.key==='F12'||((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='r'))return;
  if(['Shift','Control','Alt','Meta','CapsLock'].includes(e.key))return;
  const mods=[e.ctrlKey&&'ctrl',e.altKey&&'alt',e.metaKey&&'cmd'].filter(Boolean);
  const named=NAMES[e.key];
  if(named){e.preventDefault();if(e.shiftKey)mods.push('shift');
    if(named==='space'&&!mods.length&&!sticky.size){type(' ');return}key(named,mods);return}
  if(e.key.length===1){
    if(mods.length){e.preventDefault();const c=e.key.toLowerCase();if(e.shiftKey&&/[a-z]/i.test(e.key))mods.push('shift');key(/[a-z]/i.test(c)?c:e.key,mods);return}
    if(e.target!==$('kb')){e.preventDefault();type(e.key)}}
});
$('kb').addEventListener('beforeinput',e=>{
  if(e.inputType==='insertText'||e.inputType==='insertReplacementText'){e.preventDefault();if(e.data)type(e.data)}
  else if(e.inputType==='deleteContentBackward'){e.preventDefault();key('backspace')}
  else if(e.inputType==='insertLineBreak'||e.inputType==='insertParagraph'){e.preventDefault();key('enter')}
});
$('kb').addEventListener('input',()=>{$('kb').value=''});
function focusKb(){if(matchMedia('(pointer:fine)').matches)$('kb').focus({preventScroll:true})}
$('kbd').onclick=()=>{$('kb').focus({preventScroll:true})};
if(matchMedia('(pointer:fine)').matches)focusKb();
// ---- dock: on-screen board + trackpad
const ROWS=[
 [['esc','esc'],'1','2','3','4','5','6','7','8','9','0','-','=',['⌫','backspace','w15']],
 [['tab','tab','w15'],'q','w','e','r','t','y','u','i','o','p','[',']','\\'],
 ['a','s','d','f','g','h','j','k','l',';',"'",['⏎','enter','w2']],
 [['⇧','shift','w2',1],'z','x','c','v','b','n','m',',','.','/',['⇧','shift','w2',1]],
 [['⌃','ctrl','w15',1],['⌥','alt','w15',1],['⌘','cmd','w15',1],['space','space','w5'],['←','left'],['↑','up'],['↓','down'],['→','right']],
 [['⌂','cmd+h','w2'],['⌘⇥','cmd+tab','w2'],['⌘␣','cmd+space','w2'],['⌘C','cmd+c','w2'],['⌘V','cmd+v','w2'],['⌘A','cmd+a','w2'],['⌘Z','cmd+z','w2']]];
const keyEls={};
function flash(name){(keyEls[name]||[]).forEach(b=>{b.classList.add('fl');setTimeout(()=>b.classList.remove('fl'),120)})}
ROWS.forEach(r=>{const d=document.createElement('div');d.className='kr';
  r.forEach(c=>{const[l,k,w,m]=typeof c==='string'?[c,c]:c;const b=document.createElement('button');b.textContent=l;if(w)b.className=w;
    (keyEls[k]=keyEls[k]||[]).push(b);
    if(m){b.dataset.m=k;b.onclick=()=>{sticky.has(k)?sticky.delete(k):sticky.add(k);document.querySelectorAll('[data-m="'+k+'"]').forEach(x=>x.classList.toggle('on',sticky.has(k)))}}
    else b.onclick=()=>{if(k==='space'&&!sticky.size){type(' ')}else if(k.length===1&&!sticky.size){type(k)}else if(k.length===1){key(k)}else{const parts=k.split('+');key(parts.pop(),parts)}flash(k)};
    d.append(b)});$('board').append(d)});
function dockSize(){document.documentElement.style.setProperty('--tray',$('dock').classList.contains('open')?$('dock').offsetHeight+'px':'0px')}
$('tr').onclick=()=>{const o=$('dock').classList.toggle('open');$('tr').classList.toggle('on',o);dockSize()};
const TRAY=[['⎋','esc'],['⇥','tab'],['⌫','backspace'],['⏎','enter'],['←','left'],['↑','up'],['↓','down'],['→','right'],
 ['⌃','ctrl',1],['⌥','alt',1],['⌘','cmd',1],['⇧','shift',1],['⌂','cmd+h'],['⌘␣','cmd+space'],['⌘⇥','cmd+tab']];
TRAY.forEach(([l,k,m])=>{const b=document.createElement('button');b.textContent=l;
  if(m){b.dataset.m=k;b.onclick=()=>{sticky.has(k)?sticky.delete(k):sticky.add(k);document.querySelectorAll('[data-m="'+k+'"]').forEach(x=>x.classList.toggle('on',sticky.has(k)))}}
  else b.onclick=()=>{const parts=k.split('+');key(parts.pop(),parts)};
  $('tray').append(b)});
function tab(n){ls.set('tab',n);$('tabt').classList.toggle('on',n==='tray');$('tabk').classList.toggle('on',n==='keys');$('tabp').classList.toggle('on',n==='pad');
  $('tray').style.display=n==='tray'?'':'none';$('board').style.display=n==='keys'?'':'none';$('tpd').style.display=n==='pad'?'':'none';dockSize()}
$('tabt').onclick=()=>tab('tray');$('tabk').onclick=()=>tab('keys');$('tabp').onclick=()=>tab('pad');
tab(ls.get('tab')||(matchMedia('(max-width:719px)').matches?'tray':'keys'));
addEventListener('resize',()=>{dockSize();draw()});
// trackpad surface
const TP=new Map();let tg=null;
const tpad=$('tpad');
tpad.addEventListener('pointerdown',e=>{e.preventDefault();tpad.setPointerCapture(e.pointerId);TP.set(e.pointerId,{x:e.clientX,y:e.clientY});
  if(TP.size===1)tg={t0:performance.now(),moved:0,multi:false,x0:e.clientX,y0:e.clientY,cy:e.clientY};else if(tg){tg.multi=true;tg.cy=[...TP.values()].reduce((a,p)=>a+p.y,0)/TP.size}
  const r=tpad.getBoundingClientRect();$('tdot').style.cssText='opacity:.85;left:'+(e.clientX-r.left)+'px;top:'+(e.clientY-r.top)+'px'});
tpad.addEventListener('pointermove',e=>{const p=TP.get(e.pointerId);if(!p||!tg)return;const dx=e.clientX-p.x,dy=e.clientY-p.y;p.x=e.clientX;p.y=e.clientY;
  tg.moved=Math.max(tg.moved,Math.hypot(e.clientX-tg.x0,e.clientY-tg.y0));
  const r=tpad.getBoundingClientRect();$('tdot').style.left=(e.clientX-r.left)+'px';$('tdot').style.top=(e.clientY-r.top)+'px';
  if(TP.size>=2){const cy=[...TP.values()].reduce((a,q)=>a+q.y,0)/TP.size;scroll(tg.cy-cy);tg.cy=cy;tg.scrolled=true;return}
  if(!tg.multi){mvAcc[0]+=dx*2;mvAcc[1]+=dy*2;flushMove()}});
function tend(e){if(!TP.delete(e.pointerId)||!tg)return;if(TP.size>0)return;flushMove(true);$('tdot').style.opacity=0;
  const tap=tg.moved<10&&performance.now()-tg.t0<500&&!tg.scrolled;if(tap)send('click',{button:tg.multi?'right':'left'});tg=null}
tpad.addEventListener('pointerup',tend);tpad.addEventListener('pointercancel',e=>{TP.delete(e.pointerId);tg=null});
$('tl').onclick=()=>send('click',{button:'left'});$('tm').onclick=()=>send('click',{button:'middle'});$('trr').onclick=()=>send('click',{button:'right'});
// ---- link sheet: devices <-> views, settings inline
function openSheet(){$('sn').value=(hub.status||{}).name||'';$('sheet').classList.add('open');$('lk').classList.add('on');sig='';render();setTimeout(draw,30)}
function closeSheet(){$('sheet').classList.remove('open');$('lk').classList.remove('on');sel=null}
$('lk').onclick=()=>$('sheet').classList.contains('open')?closeSheet():openSheet();$('pc').onclick=closeSheet;$('scrim').onclick=closeSheet;
function sw(on,fn){const l=document.createElement('label');l.className='sw';l.innerHTML='<input type=checkbox'+(on?' checked':'')+'><s></s>';l.firstChild.onchange=e=>fn(e.target.checked);return l}
function setRow(label,ctl){const d=document.createElement('div');d.className='set';const s=document.createElement('span');s.textContent=label;d.append(s,ctl);return d}
function seg(opts,cur,fn){const d=document.createElement('div');d.className='seg';opts.forEach(([v,l])=>{const b=document.createElement('button');b.textContent=l;if(String(v)===String(cur))b.className='on';b.onclick=()=>fn(v);d.append(b)});return d}
function range(min,max,step,v,fn){const r=document.createElement('input');r.type='range';r.min=min;r.max=max;r.step=step;r.value=v;r.onchange=()=>fn(+r.value);return r}
function btn(t,fn){const b=document.createElement('button');b.textContent=t;b.onclick=fn;return b}
function devRow(d){
  const r=document.createElement('div');r.className='row'+(expanded.has('d'+d.addr)?' ex':'')+(sel&&sel.k==='d'&&sel.id===d.addr?' sel':'');r.dataset.k='d';r.dataset.id=d.addr;
  const c=links[d.addr];
  r.innerHTML='<div class=main><i class="dot '+(d.connected?'up':'off')+'"></i><span class=nm><b>'+esc(d.name)+'</b><small>'+(c?esc(c):(d.active?'active':d.connected?'connected':'offline'))+'</small></span><button class=chev aria-label=more>'+IC.chev+'</button></div><span class="port'+(c?' linked':'')+'" data-k=d data-id="'+esc(d.addr)+'"></span>';
  const m=document.createElement('div');m.className='more';
  const st=hub.status||{};
  const ni=document.createElement('input');ni.type='text';ni.value=d.name;ni.maxLength=60;ni.setAttribute('aria-label','name');
  const nb=btn('Save',()=>send('rename',{device:d.n,name:ni.value.trim()}).then(()=>{sig='';poll()}));
  const nw=document.createElement('div');nw.style.cssText='display:flex;gap:6px;flex:1;min-width:140px';nw.append(ni,nb);m.append(setRow('Name',nw));
  m.append(setRow('Solo',sw(!!st.solo,v=>send('solo',{on:v}).then(poll))));
  if(st.mouse_enabled!==false)m.append(setRow('Server trackpad',sw(!!st.trackpad,v=>send('mouse',{trackpad:v}).then(poll))));
  const b=document.createElement('div');b.className='btns';
  b.append(btn(d.active&&d.connected?'Reconnect':'Use',()=>send('use',{device:d.n}).then(poll)),btn('Disconnect',()=>send('drop',{device:d.n}).then(poll)),btn('Home pointer',()=>send('home',{})));
  if(c)b.append(btn('Unlink',()=>setLink(d.addr,null)));
  m.append(b);r.append(m);return r}
function camRow(c){
  const linked=Object.keys(links).filter(a=>links[a]===c.name);
  const r=document.createElement('div');r.className='row'+(expanded.has('v'+c.name)?' ex':'')+(sel&&sel.k==='v'&&sel.id===c.name?' sel':'');r.dataset.k='v';r.dataset.id=c.name;
  const dn=linked.map(a=>(devOf(a)||{}).name||a).join(', ');
  r.innerHTML='<div class=main><i class="dot '+(c.present?(viewCam===c.name?'up':'idle'):'off')+'"></i><span class=nm><b>'+esc(c.label||c.name)+'</b><small>'+esc(dn||c.name)+'</small></span><button class=chev aria-label=more>'+IC.chev+'</button></div><span class="port'+(linked.length?' linked':'')+'" data-k=v data-id="'+esc(c.name)+'"></span>';
  const o=c.opts||{},m=document.createElement('div');m.className='more';
  const s=(patch)=>saveCam(c.name,patch);
  m.append(setRow('Rotate',seg([[0,'0'],[90,'90'],[180,'180'],[270,'270']],o.rot||0,v=>s({rot:v}))));
  m.append(setRow('Gray',sw(!!o.gray,v=>s({gray:v?1:0}))));
  m.append(setRow('Contrast',range(0,10,.5,o.contrast||0,v=>s({contrast:v}))));
  m.append(setRow('Sharpness',range(0,3,.5,o.sharp||0,v=>s({sharp:v}))));
  const w=document.createElement('select');[[0,'native'],[480,'480'],[720,'720'],[960,'960'],[1280,'1280']].forEach(([v,l])=>{const op=document.createElement('option');op.value=v;op.textContent=l;if((o.w||0)==v)op.selected=true;w.append(op)});w.onchange=()=>s({w:+w.value});
  m.append(setRow('Width',w));
  const b=document.createElement('div');b.className='btns';
  b.append(btn(o.warp?'Recalibrate':'Calibrate',()=>{viewCam=c.name;startCal()}));
  if(o.warp)b.append(btn('Clear',()=>post('/calibrate/'+encodeURIComponent(c.name),{clear:true}).then(()=>{nonce++;poll()})));
  linked.forEach(a=>b.append(btn('Unlink '+((devOf(a)||{}).name||a).slice(0,14),()=>setLink(a,null))));
  m.append(b);r.append(m);return r}
function saveCam(name,patch){const c=camOf(name);if(c)c.opts=Object.assign({},c.opts,patch);return post('/save/'+encodeURIComponent(name),patch).then(()=>{nonce++;poll()})}
function setLink(addr,cam){return post('/links',{addr,cam}).then(j=>{links=j.links||{};if(cam&&hub.status&&hub.status.active===addr){viewCam=cam;ls.set('view',cam)}sig='';poll()})}
function render(){
  if(!$('sheet').classList.contains('open'))return;
  const s=JSON.stringify([devs,cams.map(c=>[c.name,c.label,c.present,c.opts]),links,[...expanded],sel,viewCam,hub.see_on,(hub.status||{}).solo,(hub.status||{}).trackpad]);
  if(s===sig)return;
  if(document.activeElement&&document.activeElement.closest&&document.activeElement.closest('.more')&&document.activeElement.tagName==='SELECT')return;
  sig=s;
  const ld=$('ld'),lv=$('lv');ld.textContent='';lv.textContent='';
  devs.forEach(d=>ld.append(devRow(d)));cams.forEach(c=>lv.append(camRow(c)));
  if(!devs.length){ld.innerHTML='<div class=empty>no devices</div>'}if(!cams.length){lv.innerHTML='<div class=empty>no cameras</div>'}
  $('seesw').checked=hub.see_on!==false&&(hub.sb||hub.see_on===true);$('seesw').disabled=hub.see_on===undefined||hub.see_on===null;
  draw();
}
$('sv').onclick=()=>{const n=$('sn').value.trim();if(n)send('rename',{server:n}).then(()=>poll())};
$('seesw').onchange=e=>post('/see',{on:e.target.checked}).then(()=>setTimeout(poll,900));
// rows: tap selects / shows, chevron expands
$('cols').addEventListener('click',e=>{
  const row=e.target.closest('.row');if(!row||e.target.closest('.more')||e.target.closest('.port'))return;
  const k=row.dataset.k,id=row.dataset.id;
  if(e.target.closest('.chev')){const key=k+id;expanded.has(key)?expanded.delete(key):expanded.add(key);sig='';render();setTimeout(draw,0);return}
  if(sel&&sel.k!==k){const a=k==='d'?id:sel.id,c=k==='v'?id:sel.id;sel=null;setLink(a,c);return}
  sel=sel&&sel.k===k&&sel.id===id?null:{k,id};
  if(k==='v'){viewCam=id;ls.set('view',id);camKey='';stream()}
  sig='';render();
});
// drag from a port onto the other column
const NS='http://www.w3.org/2000/svg';
function ctr(el){const c=$('cols').getBoundingClientRect(),r=el.getBoundingClientRect();return[r.left+r.width/2-c.left,r.top+r.height/2-c.top]}
function curve(a,b){const dx=Math.max(24,Math.abs(b[0]-a[0])*.5);return'M'+a[0]+' '+a[1]+' C'+(a[0]+dx)+' '+a[1]+' '+(b[0]-dx)+' '+b[1]+' '+b[0]+' '+b[1]}
function draw(){
  const svg=$('lines');if(!svg||!$('sheet').classList.contains('open'))return;svg.textContent='';
  Object.keys(links).forEach(addr=>{
    const p=document.querySelector('.port[data-k=d][data-id="'+CSS.escape(addr)+'"]'),q=document.querySelector('.port[data-k=v][data-id="'+CSS.escape(links[addr])+'"]');if(!p||!q)return;
    const a=ctr(p),b=ctr(q),path=document.createElementNS(NS,'path');path.setAttribute('d',curve(a,b));svg.append(path);
    const g=document.createElementNS(NS,'g'),c=document.createElementNS(NS,'circle'),t=document.createElementNS(NS,'text');
    const mx=(a[0]+b[0])/2,my=(a[1]+b[1])/2;c.setAttribute('cx',mx);c.setAttribute('cy',my);c.setAttribute('r',9);t.setAttribute('x',mx);t.setAttribute('y',my);t.textContent='×';
    g.append(c,t);g.onclick=()=>setLink(addr,null);svg.append(g)});
}
new ResizeObserver(()=>draw()).observe($('cols'));
let drag=null;
$('cols').addEventListener('pointerdown',e=>{const p=e.target.closest('.port');if(!p)return;e.preventDefault();p.setPointerCapture(e.pointerId);
  const path=document.createElementNS(NS,'path');path.setAttribute('class','g');$('lines').append(path);drag={k:p.dataset.k,id:p.dataset.id,p,path,pid:e.pointerId}});
$('cols').addEventListener('pointermove',e=>{if(!drag||e.pointerId!==drag.pid)return;
  const c=$('cols').getBoundingClientRect(),a=ctr(drag.p),b=[e.clientX-c.left,e.clientY-c.top];
  drag.path.setAttribute('d',drag.k==='d'?curve(a,b):curve(b,a));
  document.querySelectorAll('.row.drop').forEach(r=>r.classList.remove('drop'));
  const t=document.elementsFromPoint(e.clientX,e.clientY).find(x=>x.classList&&x.classList.contains('row')&&x.dataset.k!==drag.k);if(t)t.classList.add('drop')});
function dragEnd(e){if(!drag||e.pointerId!==drag.pid)return;
  const t=document.elementsFromPoint(e.clientX,e.clientY).find(x=>x.classList&&x.classList.contains('row')&&x.dataset.k!==drag.k);
  document.querySelectorAll('.row.drop').forEach(r=>r.classList.remove('drop'));drag.path.remove();
  if(t){const a=drag.k==='d'?drag.id:t.dataset.id,c=drag.k==='v'?drag.id:t.dataset.id;setLink(a,c)}
  drag=null;draw()}
$('cols').addEventListener('pointerup',dragEnd);$('cols').addEventListener('pointercancel',e=>{if(drag){drag.path.remove();drag=null}});
</script>
"""


class Gateway:
    token = ""


FAILS = {}


def guessed(ip):
    FAILS.setdefault(ip, []).append(time.time())


def locked_out(ip, limit=8, window=60):
    now = time.time(); FAILS[ip] = [t for t in FAILS.get(ip, []) if now - t < window]
    return len(FAILS[ip]) >= limit


class Handler(BaseHTTPRequestHandler):
    server_version = "portabotty"
    def log_message(self, *a): pass

    def _send(self, code, body, ctype="application/json", extra=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in extra: self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200): self._send(code, json.dumps(obj).encode())

    def _auth(self, q):
        if not Gateway.token: return True
        given = (q.get("t") or [""])[-1] or self.headers.get("Authorization", "").replace("Bearer ", "")
        if Gateway.token == Gateway.token.lower(): given = given.lower()
        ok = hmac.compare_digest(given, Gateway.token)
        if given and not ok: guessed(self.client_address[0])
        return ok

    def _body(self):
        return self.rfile.read(min(int(self.headers.get("Content-Length", 0) or 0), 100_000)) or b"{}"

    def _proxy(self, method, base, path, query, token, body=None):
        q = [(k, v) for k, v in urllib.parse.parse_qsl(query) if k != "t"]        # our token is not theirs
        url = base + path + ("?" + urllib.parse.urlencode(q) if q else "")
        req = urllib.request.Request(url, data=body, method=method,
                                     headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        try:
            r = urllib.request.urlopen(req, timeout=30)
        except urllib.error.HTTPError as e:
            return self._send(e.code, e.read(), e.headers.get("Content-Type", "application/json"))
        except OSError:
            return self._json({"ok": False, "error": "stopped"}, 502)
        try:
            ctype = r.headers.get("Content-Type", "application/octet-stream")
            if ctype.startswith("multipart"):                                     # live stream: pass bytes through
                self.send_response(200); self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store"); self.end_headers()
                while True:
                    chunk = r.read1(65536)
                    if not chunk: break
                    self.wfile.write(chunk); self.wfile.flush()
            else:
                self._send(r.status, r.read(), ctype)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            r.close()                                                             # lets pbsee stop an unwatched camera

    def _mcp(self):
        """MCP over HTTP (JSON-RPC in a POST): the same tools as the stdio adapter, run in this process."""
        try:
            m = json.loads(self._body())
        except ValueError:
            return self._json({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}, 400)
        import pbmcp as mcp
        mcp.KB_URL, mcp.CV_URL, mcp.CV_TOKEN = KB_URL, SB_URL, sb_token()
        msgs = m if isinstance(m, list) else [m]
        out = [r for r in (mcp.handle(x) for x in msgs if isinstance(x, dict)) if r is not None]
        if not out: return self._send(202, b"", "application/json")
        self._json(out if isinstance(m, list) else out[0])

    def _route(self, method):
        u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query)
        parts = [p for p in u.path.split("/") if p]
        if locked_out(self.client_address[0]): return self._send(429, b"too many wrong tokens, wait a minute", "text/plain")
        if not self._auth(q): return self._send(401, b"token required (?t=...)", "text/plain")
        if method == "GET":
            if not parts: return self._send(200, CONSOLE.encode(), "text/html; charset=utf-8")
            if parts == ["mcp"]: return self._send(405, b"POST only", "text/plain", (("Allow", "POST"),))
            if parts == ["hub"]: return self._json(hub_status())
            if parts == ["links"]: return self._json({"links": load_cfg().get("links", {})})
            if parts[0] == "kb" and len(parts) == 2 and parts[1] in KB_PATHS:
                return self._proxy("GET", KB_URL, "/" + parts[1], u.query, kb_token())
            if parts[0] in SB_GET and len(parts) <= 2:
                return self._proxy("GET", SB_URL, u.path, u.query, sb_token())
        else:
            if parts == ["mcp"]: return self._mcp()
            if parts == ["links"]: return self._links()
            if parts == ["see"]:
                if "set" not in SEE: return self._json({"ok": False, "error": "camera server is managed by PBKEY"}, 501)
                try: on = bool(json.loads(self._body()).get("on"))
                except Exception: return self._json({"ok": False}, 400)
                return self._json({"ok": True, "on": SEE["set"](on)})
            if parts[0:1] == ["kb"] and len(parts) == 2 and parts[1] in KB_PATHS:
                return self._proxy("POST", KB_URL, "/" + parts[1], "", kb_token(), self._body())
            if parts[0:1] and parts[0] in SB_POST and len(parts) == 2:
                return self._proxy("POST", SB_URL, u.path, u.query, sb_token(), self._body())
        self._send(404, b"not found", "text/plain")

    def _links(self):
        try: b = json.loads(self._body()); addr = str(b["addr"])
        except Exception: return self._json({"ok": False}, 400)
        cfg = load_cfg(); links = cfg.setdefault("links", {})
        if b.get("cam"): links[addr] = str(b["cam"])
        else: links.pop(addr, None)
        save_cfg(cfg)
        self._json({"ok": True, "links": links})

    def do_DELETE(self):                                   # MCP clients end sessions this way; we keep none
        if urllib.parse.urlparse(self.path).path.rstrip("/") == "/mcp": return self._send(405, b"no sessions", "text/plain", (("Allow", "POST"),))
        self._send(404, b"not found", "text/plain")

    def do_GET(self): self._route("GET")
    def do_POST(self): self._route("POST")


def start_gateway(listen, port, token):
    Gateway.token = token
    srv = ThreadingHTTPServer((listen, port), Handler); srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ---------------------------------------------------------------- start / urls
def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("10.255.255.255", 1)); ip = s.getsockname()[0]; s.close()
        return ip
    except OSError:
        return "localhost"


TOKEN_CHARS = "abcdefghjkmnpqrstuvwxyz23456789"          # lowercase, no look-alikes: easy to type on a phone


def short_token(n=6):
    return "".join(secrets.choice(TOKEN_CHARS) for _ in range(n))


def web_token(listen, token=""):
    """Token for the web console: required (and generated once) when it listens beyond this machine.
    Short on purpose (typed on phones); wrong guesses are rate limited per address."""
    cfg = load_cfg()
    token = token or cfg.get("token", "")
    if token and token == cfg.get("token") and len(token) > 8:           # an older, longer generated token
        token = ""
    if listen not in ("127.0.0.1", "localhost", "::1") and not token:
        token = short_token(); cfg["token"] = token; save_cfg(cfg)
    return token


def web_url(listen, port, token):
    host = lan_ip() if listen == "0.0.0.0" else "localhost"
    return f"http://{host}:{port}/" + (f"?t={token}" if token else "")


def see_state():
    """'live' (frames flowing) / 'idle' (up, camera stopped until watched) / 'stopped' (PBSEE not running)."""
    if not port_open(SB_URL): return "stopped"
    try:
        cams = http_json(SB_URL + "/status", {"Authorization": "Bearer " + sb_token()}).get("cams", [])
    except Exception:
        return "idle"
    if any(c.get("fps", 0) > 0 and (c.get("frame_age") is None or c["frame_age"] < 3) for c in cams): return "live"
    return "idle"


def relay_agent(url, agent_token, port, local_token=""):
    """Dial out to a PBRELAY and serve its queued requests from the local gateway (no inbound port needed)."""
    import urllib.error
    url = url.rstrip("/")
    def call(path, data=None, timeout=40):
        req = urllib.request.Request(url + path, data=data, method="POST" if data is not None else "GET",
                                     headers={"Authorization": "Bearer " + agent_token})
        return urllib.request.urlopen(req, timeout=timeout)

    def serve(job):
        rid = job["id"]; body = base64.b64decode(job["body"] or "")
        q = job["query"]
        hdr = {"Authorization": "Bearer " + local_token} if local_token else {}
        if job["ctype"]: hdr["Content-Type"] = job["ctype"]
        try:
            up = urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}{job['path']}" + ("?" + q if q else ""),
                                        data=body if job["method"] == "POST" else None, method=job["method"], headers=hdr), timeout=30)
            status, ctype = up.status, up.headers.get("Content-Type", "application/octet-stream")
        except urllib.error.HTTPError as e:
            up, status, ctype = e, e.code, e.headers.get("Content-Type", "text/plain")
        except Exception:
            up, status, ctype = None, 502, "text/plain"
        try:
            call(f"/_a/{rid}/head", json.dumps({"status": status, "ctype": ctype}).encode())
            while up is not None:
                chunk = up.read1(65536) if hasattr(up, "read1") else up.read(65536)
                if not chunk: break
                call(f"/_a/{rid}/data", chunk)
            call(f"/_a/{rid}/end", b"")
        except Exception:
            pass
        finally:
            try: up and up.close()
            except Exception: pass

    def loop():
        while True:
            try:
                with call("/_a/next", timeout=40) as r:
                    if r.status == 200:
                        threading.Thread(target=serve, args=(json.loads(r.read()),), daemon=True).start()
            except Exception:
                time.sleep(3)
    threading.Thread(target=loop, daemon=True).start()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--listen"); ap.add_argument("--port", type=int); ap.add_argument("--token")
    ap.add_argument("--relay"); ap.add_argument("--relay-token")
    a = ap.parse_args()
    cfg = load_cfg()
    listen = a.listen or cfg.get("listen", "0.0.0.0")
    port = a.port or int(cfg.get("port", DB_PORT))
    token = web_token(listen, a.token or "")
    try:
        start_gateway(listen, port, token)
    except OSError as e:
        sys.exit(f"cannot listen on {listen}:{port} ({e}). Already running?")
    print(web_url(listen, port, token), flush=True)
    ru, rt = a.relay or cfg.get("relay", ""), a.relay_token or cfg.get("relay_token", "")
    if ru and rt:
        if (ru, rt) != (cfg.get("relay"), cfg.get("relay_token")): cfg["relay"], cfg["relay_token"] = ru, rt; save_cfg(cfg)
        relay_agent(ru, rt, port, token)
    try:
        while True: time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
