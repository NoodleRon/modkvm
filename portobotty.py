#!/usr/bin/env python3
"""Portobotty (pb): hub for PBKEY (keyboard + mouse to your devices) and PBSEE (webcam view), with one web console and MCP.

  pb                     open pbk and pbs in their own terminals (tmux / GUI terminals); hub + web console here
  pb k | pb s            open only one   (also: pb key, pb kb, pb c, pb cam, pb see, pb sb)
  pb --listen 0.0.0.0 [--token T]   web console beyond this machine (token required, auto-generated)
  pb --install           create pb, pbk (pbkb), pbs (pbc) commands (sudo)
Details: PORTOBOTTY.md
"""
import os, sys, json, time, shlex, socket, shutil, select, secrets, hmac, argparse, threading, subprocess
import urllib.parse, urllib.request, urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
KB_PORT, SB_PORT, DB_PORT = 8809, 8808, 8800
KB_URL = os.environ.get("PBKEY_URL", f"http://127.0.0.1:{KB_PORT}").rstrip("/")
SB_URL = os.environ.get("PBSEE_URL", f"http://127.0.0.1:{SB_PORT}").rstrip("/")
CONFIG = os.environ.get("PORTOBOTTY_CONFIG", os.path.join(HERE, "portobotty.json"))
KB_PATHS = {"status", "use", "drop", "solo", "mouse", "type", "key", "goto", "click", "scroll", "drag", "move", "home"}
SB_GET = {"cams", "status", "stream", "snap"}
SB_POST = {"calibrate", "save"}


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
    return {"kb": kb_up, "sb": sb_up, "cam": cam, "status": status}


# ---------------------------------------------------------------- gateway (web console + proxies)
CONSOLE = r"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover,user-scalable=no">
<title>pbsee</title>
<style>
:root{--bg:#0e0f11;--bar:#17181b;--fg:#e6e7ea;--dim:#8b8e96;--acc:#4da3ff;--ok:#3ecf74;--bad:#ff5d5d;--line:#26282d}
@media (prefers-color-scheme:light){:root:not([data-t=dark]){--bg:#f3f4f6;--bar:#fff;--fg:#14151a;--dim:#6b6f78;--line:#dcdee3}}
*{box-sizing:border-box}html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--fg);font:15px system-ui,sans-serif;display:flex;flex-direction:column;overscroll-behavior:none;-webkit-user-select:none;user-select:none}
#bar{display:flex;align-items:center;gap:6px;padding:8px max(12px,env(safe-area-inset-left));background:var(--bar);border-bottom:1px solid var(--line)}
#dev{display:flex;gap:6px;overflow-x:auto;flex:1;min-width:0;scrollbar-width:none}
button{flex:none;font:inherit;color:var(--fg);background:none;border:1px solid var(--line);border-radius:10px;min-width:40px;height:40px;padding:0 12px;cursor:pointer;touch-action:manipulation}
button:active{background:var(--line)}button.on{border-color:var(--acc);color:var(--acc)}
.chip{flex:none;white-space:nowrap;display:flex;align-items:center;gap:8px}.chip i{width:8px;height:8px;border-radius:50%;background:var(--dim)}
.chip.up i{background:var(--ok)}.chip.act{border-color:var(--acc)}
a.b{display:flex;align-items:center;justify-content:center;min-width:40px;height:40px;border:1px solid var(--line);border-radius:10px;color:var(--fg);text-decoration:none}
#stage{position:relative;flex:1;min-height:0;display:flex;align-items:center;justify-content:center;touch-action:none;overflow:hidden}
#wrap{position:relative;line-height:0;max-width:100%;max-height:100%}
#cam{max-width:100%;max-height:calc(100vh - 66px - var(--tray,0px));display:block;-webkit-user-drag:none;pointer-events:none}
#ptr{position:absolute;width:22px;height:22px;margin:-11px 0 0 -11px;border:2px solid var(--acc);border-radius:50%;pointer-events:none;opacity:.85;display:none}
#pad{position:absolute;inset:0;display:none;border:2px dashed var(--line);pointer-events:none}
#stage.pad #pad{display:block}
#tray{display:none;flex-wrap:wrap;gap:6px;padding:8px max(12px,env(safe-area-inset-left)) max(8px,env(safe-area-inset-bottom));background:var(--bar);border-top:1px solid var(--line)}
#tray.open{display:flex}#tray button{flex:1 0 44px}
#kb{position:fixed;left:0;top:0;width:1px;height:1px;opacity:0;border:0;padding:0}
.ind{display:flex;align-items:center;gap:5px;color:var(--dim);font-size:13px}.ind i{width:8px;height:8px;border-radius:50%;background:var(--bad)}.ind.up i{background:var(--ok)}.ind.idle i{background:#e0b341}
#cam:not([src]){display:none}
</style>
<div id=bar><span class=ind id=kbi><i></i>key</span><span class=ind id=sbi><i></i>see</span><div id=dev></div>
<button id=mode aria-label=mode>◎</button><button id=kbd aria-label=keyboard>⌨</button><button id=tr aria-label=keys>⋯</button><a class=b href=view id=gear aria-label=view>⚙</a></div>
<div id=stage><div id=wrap><img id=cam alt=""><i id=ptr></i><div id=pad></div></div></div>
<div id=tray></div><input id=kb autocapitalize=off autocorrect=off autocomplete=off spellcheck=false aria-label=keyboard>
<script>
const T=new URLSearchParams(location.search).get('t')||'',$=id=>document.getElementById(id);
const U=p=>p+(T?(p.includes('?')?'&':'?')+'t='+encodeURIComponent(T):'');
$('gear').href=U('/view');
const ls={get:k=>{try{return localStorage.getItem(k)}catch(e){return null}},set:(k,v)=>{try{localStorage.setItem(k,v)}catch(e){}}};
// ---- api (serialised so keys keep their order)
let chain=Promise.resolve(),down=false;
const raw=(p,b)=>fetch(U('/kb/'+p),{method:b===undefined?'GET':'POST',body:b===undefined?undefined:JSON.stringify(b)}).then(r=>r.json()).catch(()=>({ok:false}));
const send=(p,b)=>chain=chain.then(()=>raw(p,b));
// ---- devices + camera
let devs=[];
function chips(s){
  devs=s.devices||[];const el=$('dev');el.textContent='';
  devs.forEach(d=>{const b=document.createElement('button');b.className='chip'+(d.connected?' up':'')+(d.active?' act':'');
    const i=document.createElement('i'),t=document.createElement('span');t.textContent=d.name;b.append(i,t);
    b.onclick=()=>send('use',{device:d.n}).then(poll);el.append(b)});
  if(s.pointer){const w=$('cam');$('ptr').style.cssText='display:block;left:'+s.pointer[0]*100+'%;top:'+s.pointer[1]*100+'%'}else $('ptr').style.display='none';
  tray_state=s;
}
let tray_state={};
let camSet=false;
function poll(){fetch(U('/hub')).then(r=>r.json()).then(h=>{
  $('kbi').className='ind'+(h.kb?' up':'');$('sbi').className='ind'+(h.sb?(h.cam==='live'?' up':' idle'):'');
  chips(h.status||{});
  if(h.sb&&!camSet)fetch(U('/cams')).then(r=>r.json()).then(c=>{const cam=(c.find(x=>x.present)||c[0]);if(cam){camSet=true;$('cam').src=U('/stream/'+encodeURIComponent(cam.name))}}).catch(()=>{});
  if(!h.sb&&camSet){camSet=false;$('cam').removeAttribute('src')}
 }).catch(()=>{$('kbi').className=$('sbi').className='ind'})}
$('cam').onerror=()=>{camSet=false;$('cam').removeAttribute('src')};
setInterval(poll,1500);poll();
// ---- modes
let mode=ls.get('mode')==='pad'?'pad':'direct';
function setMode(m){mode=m;ls.set('mode',m);$('stage').classList.toggle('pad',m==='pad');$('mode').textContent=m==='pad'?'▭':'◎';$('mode').classList.toggle('on',m==='pad')}
$('mode').onclick=()=>setMode(mode==='pad'?'direct':'pad');setMode(mode);
// ---- pointer
const frac=(x,y)=>{const r=$('cam').getBoundingClientRect();return[Math.min(1,Math.max(0,(x-r.left)/r.width)),Math.min(1,Math.max(0,(y-r.top)/r.height))]};
const P=new Map();let g=null,mvAcc=[0,0],mvT=0,scAcc=0;
function flushMove(force){const n=performance.now();if(!force&&n-mvT<25)return;if(mvAcc[0]||mvAcc[1]){const dx=Math.round(mvAcc[0]),dy=Math.round(mvAcc[1]);mvAcc[0]-=dx;mvAcc[1]-=dy;mvT=n;send('move',{dx,dy})}}
function scroll(px){scAcc+=px;const n=Math.trunc(scAcc/28);if(n){scAcc-=n*28;send('scroll',{dir:n>0?'down':'up',amount:Math.abs(n)})}}
$('stage').addEventListener('pointerdown',e=>{e.preventDefault();$('stage').setPointerCapture(e.pointerId);P.set(e.pointerId,{x:e.clientX,y:e.clientY});
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
$('stage').addEventListener('pointerup',end);$('stage').addEventListener('pointercancel',e=>{P.delete(e.pointerId);g=null});
$('stage').addEventListener('contextmenu',e=>e.preventDefault());
$('stage').addEventListener('wheel',e=>{e.preventDefault();scroll(e.deltaY)},{passive:false});
// ---- keyboard
const NAMES={Enter:'enter',Escape:'esc',Tab:'tab',Backspace:'backspace',Delete:'delete',ArrowUp:'up',ArrowDown:'down',ArrowLeft:'left',ArrowRight:'right',Home:'home',End:'end',PageUp:'pageup',PageDown:'pagedown',' ':'space'};
for(let i=1;i<=12;i++)NAMES['F'+i]='f'+i;
const sticky=new Set();let typed='',tt=0;
function clearSticky(){sticky.clear();document.querySelectorAll('#tray [data-m]').forEach(b=>b.classList.remove('on'))}
function type(s){if(sticky.size){const m=[...sticky];clearSticky();s.split('').forEach(c=>send('key',{key:m.concat(c).join('+')}));return}
  typed+=s;clearTimeout(tt);tt=setTimeout(()=>{const t=typed;typed='';send('type',{text:t})},15)}
function key(name,extra){const m=[...(extra||[]),...sticky];clearSticky();flushTyped();send('key',{key:m.concat(name).join('+')})}
function flushTyped(){if(typed){clearTimeout(tt);const t=typed;typed='';send('type',{text:t})}}
document.addEventListener('keydown',e=>{
  if(e.isComposing||e.key==='Process'||e.key==='Dead'||e.key==='Unidentified')return;
  if(e.key==='F5'||e.key==='F12'||((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='r'))return;
  if(['Shift','Control','Alt','Meta','CapsLock'].includes(e.key))return;
  const mods=[e.ctrlKey&&'ctrl',e.altKey&&'alt',e.metaKey&&'cmd'].filter(Boolean);
  const named=NAMES[e.key];
  if(named){e.preventDefault();if(e.shiftKey)mods.push('shift');
    if(named==='space'&&!mods.length&&!sticky.size){type(' ');return}key(named,mods);return}
  if(e.key.length===1){
    if(mods.length){e.preventDefault();const c=e.key.toLowerCase();if(e.shiftKey&&/[a-z]/i.test(e.key))mods.push('shift');key(/[a-z]/i.test(c)?c:e.key,mods);return}
    if(e.target!==$('kb')){e.preventDefault();type(e.key)}}   // printable text normally arrives through beforeinput on #kb
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
// ---- key tray
const TRAY=[['⎋','esc'],['⇥','tab'],['⌫','backspace'],['⏎','enter'],['←','left'],['↑','up'],['↓','down'],['→','right'],
 ['⌃','ctrl',1],['⌥','alt',1],['⌘','cmd',1],['⇧','shift',1],['⌂','cmd+h'],['⌘␣','cmd+space'],['⌘⇥','cmd+tab']];
TRAY.forEach(([l,k,m])=>{const b=document.createElement('button');b.textContent=l;
  if(m){b.dataset.m=k;b.onclick=()=>{sticky.has(k)?sticky.delete(k):sticky.add(k);b.classList.toggle('on',sticky.has(k))}}
  else b.onclick=()=>{const parts=k.split('+');key(parts.pop(),parts)};
  $('tray').append(b)});
$('tr').onclick=()=>{const o=$('tray').classList.toggle('open');$('tr').classList.toggle('on',o);
  document.documentElement.style.setProperty('--tray',o?$('tray').offsetHeight+'px':'0px')};
addEventListener('resize',()=>{if($('tray').classList.contains('open'))document.documentElement.style.setProperty('--tray',$('tray').offsetHeight+'px')});
</script>
"""


class Gateway:
    token = ""


class Handler(BaseHTTPRequestHandler):
    server_version = "portobotty"
    def log_message(self, *a): pass

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store"); self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200): self._send(code, json.dumps(obj).encode())

    def _auth(self, q):
        if not Gateway.token: return True
        given = (q.get("t") or [""])[-1] or self.headers.get("Authorization", "").replace("Bearer ", "")
        return hmac.compare_digest(given, Gateway.token)

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
        if not self._auth(q): return self._send(401, b"token required (?t=...)", "text/plain")
        if method == "GET":
            if not parts: return self._send(200, CONSOLE.encode(), "text/html; charset=utf-8")
            if parts == ["hub"]: return self._json(hub_status())
            if parts == ["view"]: return self._proxy("GET", SB_URL, "/", "", sb_token())
            if parts[0] == "kb" and len(parts) == 2 and parts[1] in KB_PATHS:
                return self._proxy("GET", KB_URL, "/" + parts[1], u.query, kb_token())
            if parts[0] in SB_GET and len(parts) <= 2:
                return self._proxy("GET", SB_URL, u.path, u.query, sb_token())
        else:
            if parts == ["mcp"]: return self._mcp()
            if parts[0:1] == ["kb"] and len(parts) == 2 and parts[1] in KB_PATHS:
                return self._proxy("POST", KB_URL, "/" + parts[1], "", kb_token(), self._body())
            if parts[0:1] and parts[0] in SB_POST and len(parts) == 2:
                return self._proxy("POST", SB_URL, u.path, u.query, sb_token(), self._body())
        self._send(404, b"not found", "text/plain")

    def do_GET(self): self._route("GET")
    def do_POST(self): self._route("POST")


def start_gateway(listen, port, token):
    Gateway.token = token
    srv = ThreadingHTTPServer((listen, port), Handler); srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ---------------------------------------------------------------- launching pbk / pbs in their own terminals
def which_cmd(name):
    """Command list for key / see: the installed launcher if there is one, else run the script directly."""
    if name == "kb":
        return ["pbk"] if shutil.which("pbk") else ["sudo", "python3", os.path.join(HERE, "pbkey.py")]
    return ["pbs"] if shutil.which("pbs") else [sys.executable, os.path.join(HERE, "pbsee.py"), "run"]


GUI_TERMS = [("gnome-terminal", ["--"]), ("kitty", []), ("wezterm", ["start", "--"]), ("alacritty", ["-e"]), ("konsole", ["-e"]),
             ("xfce4-terminal", ["-x"]), ("xterm", ["-e"]), ("x-terminal-emulator", ["-e"])]


def pick_mode():
    if os.environ.get("TMUX") and shutil.which("tmux"): return "tmux"
    if (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) and any(shutil.which(t) for t, _ in GUI_TERMS): return "gui"
    if shutil.which("tmux"): return "session"
    return "bg"


def launch(name, mode, last_pane=[None]):
    """Open `name` (kb = key, sb = see) in a new pane / window. Returns True if something was started."""
    cmd = which_cmd(name)
    line = shlex.join(cmd)
    if mode == "tmux":
        if last_pane[0]:
            alive = subprocess.run(["tmux", "list-panes", "-a", "-F", "#{pane_id}"], capture_output=True, text=True).stdout.split()
            if last_pane[0] not in alive: last_pane[0] = None      # that pane closed (its program exited)
        target = ["-t", last_pane[0]] if last_pane[0] else []
        flag = ["-v"] if last_pane[0] else ["-h"]
        r = subprocess.run(["tmux", "split-window", "-d", *flag, *target, "-P", "-F", "#{pane_id}", "-c", HERE, line],
                           capture_output=True, text=True)
        if r.returncode == 0: last_pane[0] = r.stdout.strip()
        return r.returncode == 0
    if mode == "gui":
        wrapped = f"{line}; echo; echo '[exited]'; read _"
        for term, pre in GUI_TERMS:
            if shutil.which(term):
                subprocess.Popen([term, *pre, "sh", "-c", wrapped], cwd=HERE, start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
    # background fallback: log file, no sudo prompt possible
    os.makedirs(os.path.join(HERE, ".portobotty"), exist_ok=True)
    log = open(os.path.join(HERE, ".portobotty", f"{name}.log"), "ab")
    if cmd[0] == "sudo": cmd = ["sudo", "-n"] + cmd[1:]
    subprocess.Popen(cmd, cwd=HERE, stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
    return True


# ---------------------------------------------------------------- hub screen
def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("10.255.255.255", 1)); ip = s.getsockname()[0]; s.close()
        return ip
    except OSError:
        return "localhost"


def render(h, url):
    kb = "●" if h["kb"] else "○"
    active = ""
    if h["status"]:
        act = next((d["name"] for d in h["status"].get("devices", []) if d.get("active")), None)
        active = f"  {act}" if act else ""
    sb = {"live": "●", "idle": "◐"}.get(h["cam"], "●" if h["sb"] else "○") if h["sb"] else "○"
    return ["PORTOBOTTY", "", f"{kb} key{active}", f"{sb} see", f"● web  {url}", "", "k key  s see  q quit"]


def cmd_hub(a):
    import termios, tty
    cfg = load_cfg()
    listen = a.listen or cfg.get("listen", "127.0.0.1")
    port = a.port or int(cfg.get("port", DB_PORT))
    token = a.token or cfg.get("token", "")
    if listen not in ("127.0.0.1", "localhost", "::1") and not token:
        token = secrets.token_urlsafe(9); cfg["token"] = token; save_cfg(cfg)
    try:
        srv = start_gateway(listen, port, token)
    except OSError as e:
        sys.exit(f"db: cannot listen on {listen}:{port} ({e}). Already running?")
    host = lan_ip() if listen == "0.0.0.0" else "localhost"
    url = f"http://{host}:{port}/" + (f"?t={token}" if token else "")
    mode = pick_mode()
    want = sorted({WHICH[w] for w in a.which}) or ["kb", "sb"]
    if not a.no_launch:
        for n in want:
            if not port_open(KB_URL if n == "kb" else SB_URL): launch(n, mode)
    tty_ok = sys.stdin.isatty()
    old = None
    if tty_ok: old = termios.tcgetattr(sys.stdin); tty.setcbreak(sys.stdin.fileno())
    sys.stdout.write("\x1b[?25l\x1b[2J")
    try:
        while True:
            h = hub_status()
            sys.stdout.write("\x1b[H" + "".join(l + "\x1b[K\n" for l in render(h, url)) + "\x1b[J"); sys.stdout.flush()
            if tty_ok:
                r, _, _ = select.select([sys.stdin], [], [], 1.0)
                if r:
                    k = sys.stdin.read(1)
                    if k in ("q", "\x03"): break
                    if k == "k" and not h["kb"]: launch("kb", mode)
                    if k == "s" and not h["sb"]: launch("sb", mode)
            else:
                time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\x1b[?25h\x1b[2J\x1b[H"); sys.stdout.flush()
        if old: termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)
        srv.shutdown(); srv.server_close()


def cmd_start(a):
    """`db` from a plain terminal: with no GUI and no tmux session yet, re-enter inside tmux so everything gets panes."""
    if a.hub or a.no_launch or not sys.stdin.isatty() or pick_mode() != "session":
        return cmd_hub(a)
    args = [sys.executable, os.path.abspath(__file__), "--hub"] + sys.argv[1:]
    os.execvp("tmux", ["tmux", "new-session", "-A", "-s", "portobotty", shlex.join(args)])


def install(a):
    me = os.path.abspath(__file__)
    sudo = [] if os.geteuid() == 0 else ["sudo"]
    script = f'#!/bin/sh\n# Portobotty launcher (created by portobotty.py --install)\nexec {sys.executable} "{me}" "$@"\n'
    subprocess.run(sudo + ["tee", "/usr/local/bin/pb"], input=script, text=True, stdout=subprocess.DEVNULL, check=True)
    subprocess.run(sudo + ["chmod", "755", "/usr/local/bin/pb"], check=True)
    print("Installed /usr/local/bin/pb")
    kb, sb = os.path.join(HERE, "pbkey.py"), os.path.join(HERE, "pbsee.py")
    if os.path.exists(kb): subprocess.run(sudo + ["python3", kb, "--install"])
    if os.path.exists(sb): subprocess.run([sys.executable, sb, "install"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("which", nargs="*", choices=sorted(WHICH), help="open only these")
    ap.add_argument("--listen"); ap.add_argument("--port", type=int); ap.add_argument("--token")
    ap.add_argument("--no-launch", action="store_true", help="hub and web console only")
    ap.add_argument("--hub", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--install", action="store_true")
    a = ap.parse_args()
    if a.install: return install(a)
    cmd_start(a)


if __name__ == "__main__":
    main()
