#!/usr/bin/env python3
"""PBSEE (Portabotty see): tiny multi-camera viewer for the Portabotty server (separate from pbkey.py on purpose).

UVC webcams -> ffmpeg (MJPEG passthrough, no decode) -> optional light processing -> HTTP (live MJPEG, snapshots, JSON).
Cameras only run while somebody is watching, then stop after IDLE_STOP seconds (saves CPU and USB bandwidth).

  python3 pbsee.py doctor [--fix]       check (and fix) packages, driver, group, devices, a test capture
  python3 pbsee.py list                 cameras with stable paths, USB ids, formats
  python3 pbsee.py probe                config skeleton for the cameras found (paste into pbsee.json)
  python3 pbsee.py run                  server + inline status lines (live / idle / stopped)  [default for `pbs`]
  python3 pbsee.py screen               full-screen status view with the latest picture where the terminal supports it
  python3 pbsee.py serve [--listen 0.0.0.0] [--port 8808] [--token T] [--test-source]
  python3 pbsee.py snap NAME [-o out.jpg] [--raw]
  python3 pbsee.py install            the pbs / pbc commands, run once (uses sudo)
  python3 pbsee.py install-service    optional: always-on systemd service instead of the pbs terminal

Needs only: ffmpeg, python3-pil, python3-numpy (v4l-utils recommended). `doctor --fix` installs them.
"""
import os, sys, io, re, json, glob, time, math, select, shutil, getpass, hmac, secrets, argparse, threading, subprocess
import urllib.parse, urllib.request, urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
SYSFS = os.environ.get("PBSEE_SYSFS", "/sys")
DEVFS = os.environ.get("PBSEE_DEVFS", "/dev")
CONFIG = os.environ.get("PBSEE_CONFIG", os.path.join(HERE, "pbsee.json"))
IDLE_STOP = float(os.environ.get("PBSEE_IDLE", "10"))     # seconds without viewers before a camera stops
CHANGE_THRESHOLD = 2.0                                        # mean abs pixel diff (0-255) that counts as "screen changed"
OPT_KEYS = ("rot", "tilt", "crop", "warp", "contrast", "sharp", "gamma", "gray", "w", "q")


# ---------------------------------------------------------------- small helpers
def rd(path, default=""):
    try:
        with open(path) as f: return f.read().strip()
    except Exception:
        return default

def run(cmd, timeout=8):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout + r.stderr
    except Exception:
        return ""

def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "cam"


# ---------------------------------------------------------------- discovery (sysfs, stdlib only)
def discover():
    """Capture-capable video devices. Skips metadata nodes (UVC exposes two nodes per camera)."""
    stable = {}
    for d in ("by-path", "by-id"):                        # by-id wins when present
        for p in glob.glob(f"{DEVFS}/v4l/{d}/*-index0"):
            stable[os.path.realpath(p)] = p
    have_ctl = bool(shutil.which("v4l2-ctl"))
    nodes = sorted(glob.glob(f"{SYSFS}/class/video4linux/video*"),
                   key=lambda p: int(re.sub(r"\D", "", os.path.basename(p)) or 0))
    cams = []
    for node in nodes:
        base = os.path.basename(node)
        if rd(node + "/index", "0") != "0":
            continue
        dev = f"{DEVFS}/{base}"
        if have_ctl:
            caps = run(["v4l2-ctl", "-d", dev, "--info"]).split("Device Caps")[-1]
            if "Video Capture" not in caps:
                continue
        iface = os.path.realpath(node + "/device")
        usb = os.path.dirname(iface)
        if not os.path.exists(os.path.join(usb, "idVendor")):
            usb = None
        cams.append({
            "dev": dev, "label": rd(node + "/name", base),
            "product": rd(usb + "/product") if usb else "",
            "vid": rd(usb + "/idVendor") if usb else "", "pid": rd(usb + "/idProduct") if usb else "",
            "serial": rd(usb + "/serial") if usb else "", "bus": rd(usb + "/busnum") if usb else "",
            "stable": stable.get(os.path.realpath(dev), dev)})
    return cams

def usb_video_devices():
    """USB devices exposing a video-class interface (0x0e), even if no /dev/video exists."""
    out = []
    for d in glob.glob(f"{SYSFS}/bus/usb/devices/*"):
        if not os.path.exists(d + "/idVendor"):
            continue
        if any(rd(i + "/bInterfaceClass") == "0e" for i in glob.glob(d + "/*:*")):
            out.append({"product": rd(d + "/product", "?"), "vid": rd(d + "/idVendor"),
                        "pid": rd(d + "/idProduct"), "bus": rd(d + "/busnum")})
    return out

def formats(dev):
    out = run(["v4l2-ctl", "-d", dev, "--list-formats-ext"])
    fmts, cur, size = {}, None, None
    for line in out.splitlines():
        m = re.search(r"\[\d+\]: '(\w+)'", line)
        if m: cur = m.group(1); fmts[cur] = {}; continue
        m = re.search(r"Size: Discrete (\d+)x(\d+)", line)
        if m and cur: size = (int(m.group(1)), int(m.group(2))); fmts[cur][size] = []; continue
        m = re.search(r"\(([\d.]+) fps\)", line)
        if m and cur and size: fmts[cur][size].append(float(m.group(1)))
    return fmts

def choose_mode(fmts, want, fps):
    """Prefer MJPG (camera-compressed, free to pass through); else any format. Returns (kind, 'WxH', fps)."""
    for f in ["MJPG"] + [x for x in fmts if x != "MJPG"]:
        sizes = fmts.get(f) or {}
        if not sizes:
            continue
        ok = [s for s in sizes if s[0] <= want[0] and s[1] <= want[1]] or list(sizes)
        s = max(ok, key=lambda z: z[0] * z[1])
        fr = sizes[s]
        return ("mjpeg" if f == "MJPG" else "raw"), f"{s[0]}x{s[1]}", (min(fr, key=lambda x: abs(x - fps)) if fr else fps)
    return "mjpeg", f"{want[0]}x{want[1]}", fps


# ---------------------------------------------------------------- config
def load_config():
    cfg = {"port": 8808, "listen": "127.0.0.1", "cameras": []}
    try:
        with open(CONFIG) as f: cfg.update(json.load(f))
    except Exception:
        pass
    return cfg

def save_config(cfg):
    tmp = CONFIG + ".tmp"
    with open(tmp, "w") as f: json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG)

def match_entry(cfg, info, claimed):
    for e in cfg.get("cameras", []):
        if id(e) in claimed: continue
        m = e.get("match", "")
        kind, _, val = m.partition(":")
        if ((kind == "usb" and f"{info['vid']}:{info['pid']}".lower() == val.lower() and info["vid"]) or
                (kind == "name" and val.lower() in (info["product"] + " " + info["label"]).lower()) or
                (kind == "serial" and val and val == info["serial"]) or
                (kind == "path" and val in (info["stable"], info["dev"]))):
            return e
    return None


# ---------------------------------------------------------------- frames + processing (Pillow/numpy, imported lazily)
def split_frames(buf):
    """JPEG frames from a byte stream. Returns (frames, remainder)."""
    frames = []
    while True:
        s = buf.find(b"\xff\xd8")
        if s < 0:
            return frames, buf[-1:]
        e = buf.find(b"\xff\xd9", s + 2)
        if e < 0:
            return frames, buf[s:]
        frames.append(buf[s:e + 2]); buf = buf[e + 2:]

def has_huffman(jpeg):
    return jpeg.find(b"\xff\xc4") != -1        # raw MJPEG from some webcams omits the tables browsers need

def reencode(jpeg, q=85):
    from PIL import Image
    im = Image.open(io.BytesIO(jpeg)); im.load()
    out = io.BytesIO(); im.save(out, "JPEG", quality=q)
    return out.getvalue()

def persp_coeffs(src_pts, w, h):
    """Coefficients for PIL's PERSPECTIVE transform: output (x,y) in a w*h rect -> source quad (TL,TR,BR,BL)."""
    import numpy as np
    A, B = [], []
    for (x, y), (X, Y) in zip([(0, 0), (w, 0), (w, h), (0, h)], src_pts):
        A.append([x, y, 1, 0, 0, 0, -X * x, -X * y]); B.append(X)
        A.append([0, 0, 0, x, y, 1, -Y * x, -Y * y]); B.append(Y)
    return [float(v) for v in np.linalg.solve(np.array(A, float), np.array(B, float))]

def _nums(v, n):
    if isinstance(v, str):
        v = [float(x) for x in v.split(",") if x != ""]
    return list(v) if v is not None and len(v) == n else None

def parse_opts(q, base):
    o = dict(base or {})
    if q.get("raw") and q["raw"][-1] not in ("0", ""):
        o = {}
    def one(k): return q[k][-1] if k in q and q[k] else None
    for k, cast in (("rot", int), ("tilt", float), ("w", int), ("q", int), ("contrast", float), ("sharp", float),
                    ("gamma", float), ("gray", int), ("fps", float)):
        v = one(k)
        if v not in (None, ""):
            try: o[k] = cast(v)
            except ValueError: pass
    if "tilt" in o: o["tilt"] = max(-45.0, min(45.0, o["tilt"]))
    for k, n in (("crop", 4), ("warp", 8)):
        v = _nums(one(k), n) if one(k) else None
        if v: o[k] = v
    return o

def needs_processing(o):
    return bool(o.get("rot") or o.get("tilt") or o.get("crop") or o.get("warp") or o.get("contrast") or o.get("sharp")
                or o.get("gray") or o.get("w") or (o.get("gamma") not in (None, 0, 1, 1.0)))

def process(jpeg, o):
    """Pass-through unless the view needs work. Order: tilt (crop views) -> warp/crop -> tilt (warped views) -> rotate -> contrast/gamma/sharpen -> resize.
    tilt = degrees to straighten a crooked camera (counter-clockwise positive)."""
    if not needs_processing(o):
        return jpeg
    from PIL import Image, ImageOps, ImageEnhance
    T, R, TR = getattr(Image, "Transform", Image), getattr(Image, "Resampling", Image), getattr(Image, "Transpose", Image)
    im = Image.open(io.BytesIO(jpeg))
    W, H = im.size
    maxw = int(o.get("w") or 0)
    rot = int(o.get("rot") or 0) % 360
    if maxw and not o.get("warp"):                       # let the JPEG decoder shrink for us (cheap on slow CPUs)
        side = int(o.get("rot") or 0) % 180 != 0         # after a 90/270 turn the output width comes from the source height
        c = o.get("crop")
        need = int(maxw / max((c[3] if side else c[2]) if c else 1.0, 0.05))
        req = (need * W // H, need) if side else (need, need * H // W)
        im.draft("RGB", (max(1, req[0]), max(1, req[1])))
    im = im.convert("RGB")
    W, H = im.size
    tilt = float(o.get("tilt") or 0)
    if tilt and not o.get("warp"):                       # straighten first so a plain crop can use every pixel
        im = im.rotate(tilt, resample=R.BILINEAR)
    if o.get("warp"):
        wp = o["warp"]; pts = [(wp[i] * W, wp[i + 1] * H) for i in (0, 2, 4, 6)]
        d = lambda a, b: math.hypot(a[0] - b[0], a[1] - b[1])
        ow, oh = (d(pts[0], pts[1]) + d(pts[3], pts[2])) / 2, (d(pts[0], pts[3]) + d(pts[1], pts[2])) / 2
        fw = oh if rot % 180 else ow                     # `w` limits the final (rotated) width
        if maxw and fw > maxw: ow, oh = ow * maxw / fw, oh * maxw / fw
        ow, oh = max(8, int(ow)), max(8, int(oh))
        im = im.transform((ow, oh), T.PERSPECTIVE, persp_coeffs(pts, ow, oh), R.BILINEAR)
        if tilt: im = im.rotate(tilt, resample=R.BILINEAR)   # fixes a hastily marked quadrilateral
    elif o.get("crop"):
        x, y, w, h = o["crop"]
        im = im.crop((int(x * W), int(y * H), max(int((x + w) * W), int(x * W) + 1), max(int((y + h) * H), int(y * H) + 1)))
    if rot:
        im = im.transpose({90: TR.ROTATE_270, 180: TR.ROTATE_180, 270: TR.ROTATE_90}[rot])
    if o.get("contrast"):
        im = ImageOps.autocontrast(im, cutoff=float(o["contrast"]))
    g = o.get("gamma")
    if g and g != 1:
        im = im.point([int(255 * ((i / 255) ** (1 / g)) + 0.5) for i in range(256)] * 3)
    if o.get("sharp"):
        im = ImageEnhance.Sharpness(im).enhance(1 + float(o["sharp"]))
    if maxw and im.width > maxw:
        im = im.resize((maxw, max(1, im.height * maxw // im.width)), R.BILINEAR)
    if o.get("gray"):
        im = im.convert("L")
    out = io.BytesIO(); im.save(out, "JPEG", quality=int(o.get("q") or 80))
    return out.getvalue()


# ---------------------------------------------------------------- one camera = one on-demand ffmpeg
class Cam:
    def __init__(self, name, src, entry, info):
        self.name, self.src, self.entry, self.info = name, src, entry, info
        self.present = True
        self.cond = threading.Condition()
        self.latest, self.seq = None, 0
        self.viewers, self.last_use = 0, 0.0
        self.thread = self.proc = None
        self.error, self.fps = "", 0.0
        self.stamps = []
        self.dht_ok = None
        self.prev, self.change, self.last_change = None, 0.0, 0.0
        self.last_sample = 0.0
        self.force_raw = False
        self.running = False

    # -- config views
    def defaults(self): return dict(self.entry.get("opts") or {})
    def want_size(self):
        m = re.match(r"(\d+)x(\d+)", str(self.entry.get("size", "1280x720")))
        return (int(m.group(1)), int(m.group(2))) if m else (1280, 720)

    # -- lifecycle
    def acquire(self):
        with self.cond:
            self.viewers += 1; self.last_use = time.time()
        self._ensure()
    def release(self):
        with self.cond:
            self.viewers = max(0, self.viewers - 1); self.last_use = time.time()
    def touch(self):
        with self.cond: self.last_use = time.time()
        self._ensure()
    def _ensure(self):
        with self.cond:
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, daemon=True); self.thread.start()
    def shutdown(self):
        with self.cond: self.viewers = 0; self.last_use = 0
        p = self.proc
        if p:
            try: p.terminate()
            except Exception: pass

    def wait_frame(self, after, timeout=5.0):
        with self.cond:
            self.cond.wait_for(lambda: self.seq != after, timeout)
            return self.seq, self.latest if self.seq != after else None

    def _idle(self):
        with self.cond:
            return self.viewers <= 0 and time.time() - self.last_use > IDLE_STOP

    # -- capture
    BASE = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]

    def _input(self):
        """(input args, copy_ok): ffmpeg input side. copy_ok = camera already delivers MJPEG, so no re-encode."""
        fps = float(self.entry.get("fps", 10))
        if self.src.startswith("test:"):
            w, h = self.want_size()
            return ["-re", "-f", "lavfi", "-i", f"testsrc2=size={min(w, 640)}x{min(h, 480)}:rate={fps:g}"], False
        kind, size = "mjpeg", f"{self.want_size()[0]}x{self.want_size()[1]}"
        if shutil.which("v4l2-ctl"):
            fm = formats(self.src)
            if fm: kind, size, fps = choose_mode(fm, self.want_size(), fps)
            for k, v in (self.entry.get("controls") or {}).items():
                run(["v4l2-ctl", "-d", self.src, "--set-ctrl", f"{k}={v}"])
        if self.force_raw: kind = "raw"
        args = ["-f", "v4l2"] + (["-input_format", "mjpeg"] if kind == "mjpeg" else []) + \
               ["-video_size", size, "-framerate", f"{fps:g}", "-i", self.src]
        return args, kind == "mjpeg"

    def _cmd(self):
        args, copy_ok = self._input()
        out = ["-c", "copy"] if copy_ok else ["-c:v", "mjpeg", "-q:v", "5"]
        return self.BASE + args + out + ["-f", "mjpeg", "pipe:1"]

    def _run(self):
        while True:
            if self._idle():
                with self.cond:
                    if self.viewers <= 0 and time.time() - self.last_use > IDLE_STOP:
                        self.thread = None; self.running = False
                        return
            self._capture_once()
            if not self._idle():
                time.sleep(1.0)                              # back off before restarting a camera that dropped out

    def _capture_once(self):
        try:
            p = subprocess.Popen(self._cmd(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except FileNotFoundError:
            self.error = "ffmpeg not found: run `python3 pbsee.py doctor --fix`"; time.sleep(4); return
        self.proc, self.running, self.error = p, True, ""
        errs = []
        threading.Thread(target=lambda: errs.append(p.stderr.read(2000)), daemon=True).start()
        buf, got, t0 = b"", 0, time.time()
        try:
            while not self._idle():
                r, _, _ = select.select([p.stdout], [], [], 1.0)
                if not r:
                    if p.poll() is not None: break
                    continue
                chunk = os.read(p.stdout.fileno(), 65536)
                if not chunk: break
                frames, buf = split_frames(buf + chunk)
                if len(buf) > 8_000_000: buf = b""
                for fr in frames:
                    got += 1; self._publish(fr)
        finally:
            try: p.terminate(); p.wait(timeout=2)
            except Exception:
                try: p.kill()
                except Exception: pass
            self.proc = None
        if got == 0 and not self._idle():
            msg = (errs[0].decode(errors="replace").strip().splitlines() or ["no frames"])[-1] if errs else "no frames"
            self.error = msg
            if not self.src.startswith("test:") and time.time() - t0 < 6 and not self.force_raw:
                self.force_raw = True                      # camera may not do MJPEG: retry as raw -> mjpeg
            if not self.src.startswith("test:"): time.sleep(2)

    def _publish(self, fr):
        if self.dht_ok is None:
            self.dht_ok = has_huffman(fr)
        if not self.dht_ok:
            try: fr = reencode(fr)
            except Exception: pass
        now = time.time()
        self.stamps = [t for t in self.stamps if now - t < 3.0] + [now]
        self.fps = len(self.stamps) / 3.0 if len(self.stamps) > 1 else 0.0
        with self.cond:
            self.latest, self.seq = fr, self.seq + 1
            self.cond.notify_all()
        if now - self.last_sample >= 0.5:
            self.last_sample = now; self._sample(fr, now)

    def _sample(self, fr, now):
        try:
            from PIL import Image
            import numpy as np
            im = Image.open(io.BytesIO(fr)); im.draft("L", (160, 120))
            a = np.asarray(im.convert("L").resize((80, 60)), dtype=np.int16)
            if self.prev is not None:
                self.change = float(np.abs(a - self.prev).mean())
                if self.change > CHANGE_THRESHOLD: self.last_change = now
            self.prev = a
        except Exception:
            pass

    def status(self):
        now = time.time()
        return {"name": self.name, "present": self.present, "running": self.running, "viewers": self.viewers,
                "fps": round(self.fps, 1), "frame_age": round(now - self.stamps[-1], 2) if self.stamps else None,
                "change": round(self.change, 2),
                "seconds_since_change": round(now - self.last_change, 1) if self.last_change else None,
                "error": self.error}


class Registry:
    def __init__(self, cfg, test_source=False):
        self.cfg, self.cams, self.lock, self.test = cfg, {}, threading.RLock(), test_source
        self.t_refresh = 0.0

    def refresh(self, force=False):
        with self.lock:
            if not force and self.cams and time.time() - self.t_refresh < 5:
                return self.cams                           # discovery spawns v4l2-ctl: don't redo it every poll
            self.t_refresh = time.time()
            claimed, used = set(), set()
            for c in self.cams.values(): c.present = False
            infos = discover()
            if self.test:
                infos.append({"dev": "test:", "label": "Synthetic test source", "product": "test", "vid": "", "pid": "",
                              "serial": "", "bus": "", "stable": "test:"})
            for info in infos:
                entry = match_entry(self.cfg, info, claimed)
                if entry: claimed.add(id(entry))
                else: entry = {}
                name = entry.get("name") or slug(info["product"] or info["label"])
                base, i = name, 2
                while name in used: name = f"{base}-{i}"; i += 1
                used.add(name)
                cam = self.cams.get(name)
                if cam is None or cam.src != info["stable"]:
                    if cam: cam.shutdown()
                    cam = self.cams[name] = Cam(name, info["stable"], entry, info)
                cam.entry, cam.info, cam.present = entry, info, True
            return self.cams

    def get(self, name):
        with self.lock:
            if name not in self.cams: self.refresh(force=True)
            return self.cams.get(name)

    def describe(self):
        self.refresh()
        return [{"name": c.name, "label": c.info.get("label"), "src": c.src, "present": c.present,
                 "usb": f"{c.info.get('vid')}:{c.info.get('pid')}" if c.info.get("vid") else "",
                 "opts": c.defaults()} for c in self.cams.values()]

    def status(self):
        self.refresh()
        return {"cams": [c.status() for c in self.cams.values()]}

    def save_opts(self, cam, opts):
        with self.lock:
            if cam.entry not in self.cfg["cameras"]:
                info = cam.info
                cam.entry.update({"name": cam.name,
                                  "match": f"usb:{info['vid']}:{info['pid']}" if info.get("vid") and
                                  sum(1 for c in self.cams.values() if c.info.get("vid") == info["vid"] and c.info.get("pid") == info["pid"]) == 1
                                  else f"path:{info['stable']}"})
                self.cfg["cameras"].append(cam.entry)
            cam.entry["opts"] = {k: v for k, v in opts.items() if k in OPT_KEYS and v not in (None, "", 0, [])}
            save_config(self.cfg)

    def shutdown(self):
        for c in self.cams.values(): c.shutdown()


# ---------------------------------------------------------------- HTTP
PAGE = r"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>pbsee</title>
<style>body{font:14px system-ui;margin:0;background:#111;color:#ddd}#bar{padding:8px;display:flex;gap:10px;flex-wrap:wrap;align-items:center;background:#1c1c1c}
button,select,input{font:inherit}img{max-width:100%;display:block;margin:0 auto}#st{padding:6px 10px;color:#9bb}#v.cal{cursor:crosshair;outline:2px solid #fb0}</style>
<div id=bar>
 <select id=cam></select>
 rotate <select id=rot><option>0<option>90<option>180<option>270</select>
 contrast <input id=con type=range min=0 max=10 step=0.5 value=0 style=width:90px>
 sharp <input id=sh type=range min=0 max=3 step=0.5 value=0 style=width:90px>
 width <select id=w><option value=0>native<option>480<option>720<option>960<option>1280</select>
 <button id=cal>Calibrate screen</button> <button id=clr>Clear</button> <button id=sv>Save view</button>
</div><div id=st></div><img id=v>
<script>
const T=new URLSearchParams(location.search).get('t')||'',$=id=>document.getElementById(id);
const U=(p,o={})=>{const q=new URLSearchParams(o);if(T)q.set('t',T);const s=q.toString();return p+(s?'?'+s:'')};
let cams=[],cur='',pts=[],cal=false;
const vals=()=>({rot:$('rot').value,contrast:$('con').value,sharp:$('sh').value,w:$('w').value});
function load(){if(!cur)return;$('v').className=cal?'cal':'';$('v').src=U('/stream/'+cur,cal?{raw:1,w:960,_:Date.now()}:{...vals(),_:Date.now()})}
function pick(){const c=cams.find(x=>x.name==cur)||{},o=c.opts||{};$('rot').value=o.rot||0;$('con').value=o.contrast||0;$('sh').value=o.sharp||0;$('w').value=o.w||0;load()}
fetch(U('/cams')).then(r=>r.json()).then(j=>{cams=j;$('cam').innerHTML=j.map(c=>`<option value="${c.name}">${c.name} (${c.label})`).join('');cur=(j[0]||{}).name;pick()});
$('cam').onchange=e=>{cur=e.target.value;cal=false;pick()};
for(const id of ['rot','con','sh','w'])$(id).onchange=load;
$('cal').onclick=()=>{cal=true;pts=[];$('st').textContent='Click the phone screen corners: top-left, top-right, bottom-right, bottom-left (as you want it shown)';load()};
$('clr').onclick=()=>fetch(U('/calibrate/'+cur),{method:'POST',body:JSON.stringify({clear:true})}).then(()=>{cal=false;load()});
$('sv').onclick=()=>fetch(U('/save/'+cur),{method:'POST',body:JSON.stringify(vals())}).then(()=>$('st').textContent='saved');
$('v').onclick=e=>{if(!cal)return;const r=e.target.getBoundingClientRect();pts.push((e.clientX-r.left)/r.width,(e.clientY-r.top)/r.height);
 $('st').textContent='corner '+(pts.length/2)+'/4';
 if(pts.length==8)fetch(U('/calibrate/'+cur),{method:'POST',body:JSON.stringify({warp:pts})}).then(()=>{cal=false;pts=[];fetch(U('/cams')).then(r=>r.json()).then(j=>{cams=j;pick()})})};
setInterval(()=>fetch(U('/status')).then(r=>r.json()).then(s=>{const c=s.cams.find(x=>x.name==cur);if(!c||cal)return;
 $('st').textContent=(c.error?'error: '+c.error+' | ':'')+c.fps+' fps | change '+c.change+(c.seconds_since_change!=null?' | last change '+c.seconds_since_change+'s ago':'')}),1000);
</script>"""


class State:
    reg = None
    token = ""

class Handler(BaseHTTPRequestHandler):
    server_version = "pbsee"
    def log_message(self, *a): pass

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store"); self.end_headers()
        self.wfile.write(body)
    def _json(self, obj, code=200): self._send(code, json.dumps(obj).encode())

    def _auth(self, q):
        if not State.token: return True
        given = (q.get("t") or [""])[-1] or self.headers.get("Authorization", "").replace("Bearer ", "")
        return hmac.compare_digest(given, State.token)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query)
        parts = [p for p in u.path.split("/") if p]
        if not self._auth(q): return self._send(401, b"token required (?t=...)", "text/plain")
        reg = State.reg
        if not parts or parts == ["view"]: return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        if parts == ["cams"]: return self._json(reg.describe())
        if parts == ["status"]: return self._json(reg.status())
        if len(parts) == 2 and parts[0] in ("stream", "snap"):
            cam = reg.get(parts[1][:-4] if parts[1].endswith(".jpg") else parts[1])
            if not cam: return self._send(404, b"no such camera", "text/plain")
            o = parse_opts(q, cam.defaults())
            return self._snap(cam, o) if parts[0] == "snap" else self._stream(cam, o)
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query)
        parts = [p for p in u.path.split("/") if p]
        if not self._auth(q): return self._send(401, b"token required", "text/plain")
        if len(parts) != 2 or parts[0] not in ("calibrate", "save"):
            return self._send(404, b"not found", "text/plain")
        cam = State.reg.get(parts[1])
        if not cam: return self._send(404, b"no such camera", "text/plain")
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
        except Exception:
            return self._send(400, b"bad json", "text/plain")
        opts = cam.defaults()
        if parts[0] == "calibrate":
            if body.get("clear"): opts.pop("warp", None); opts.pop("crop", None)
            elif _nums(body.get("warp"), 8): opts["warp"] = _nums(body["warp"], 8); opts.pop("crop", None)
            else: return self._send(400, b"need warp[8] or clear", "text/plain")
        else:
            for k, cast in (("rot", int), ("tilt", float), ("w", int), ("q", int), ("contrast", float), ("sharp", float), ("gamma", float), ("gray", int)):
                if k in body:
                    try: opts[k] = cast(body[k])
                    except (TypeError, ValueError): pass
            if "tilt" in opts: opts["tilt"] = round(max(-45.0, min(45.0, float(opts["tilt"]))), 2)
            if _nums(body.get("crop"), 4): opts["crop"] = _nums(body["crop"], 4)
        State.reg.save_opts(cam, opts)
        self._json({"ok": True, "opts": cam.defaults()})

    def _snap(self, cam, o):
        cam.touch()
        seq, jpg = cam.wait_frame(cam.seq, 10)
        if jpg is None: return self._send(504, (cam.error or "no frame").encode(), "text/plain")
        try: self._send(200, process(jpg, o), "image/jpeg")
        except Exception as e: self._send(500, f"processing failed: {e}".encode(), "text/plain")

    def _stream(self, cam, o):
        cam.acquire()
        try:
            self.connection.settimeout(15)               # a stalled client must not keep a camera running forever
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store"); self.end_headers()
            seq, last, dt = 0, 0.0, (1.0 / o["fps"] if o.get("fps") else 0.0)
            while True:
                seq2, jpg = cam.wait_frame(seq, 5)
                if jpg is None: continue
                seq = seq2
                if dt and time.time() - last < dt: continue
                out = process(jpg, o)
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(out))
                self.wfile.write(out); self.wfile.write(b"\r\n"); self.wfile.flush()
                last = time.time()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            cam.release()


def start_server(a):
    cfg = load_config()
    listen, port = a.listen or cfg.get("listen", "127.0.0.1"), a.port or int(cfg.get("port", 8808))
    token = a.token or cfg.get("token", "")
    if listen not in ("127.0.0.1", "localhost", "::1") and not token:
        token = secrets.token_urlsafe(9); cfg["token"] = token; save_config(cfg)     # never expose a camera feed unauthenticated
    State.reg, State.token = Registry(cfg, a.test_source), token
    State.reg.refresh()
    srv = ThreadingHTTPServer((listen, port), Handler); srv.daemon_threads = True
    return srv, listen, port, token


def cmd_serve(a):
    srv, listen, port, token = start_server(a)
    found = ", ".join(c.name for c in State.reg.cams.values()) or "none yet (plug one in; `doctor` explains)"
    print(f"pbsee on http://{listen}:{port}/{'?t=' + token if token else ''}   cameras: {found}", flush=True)
    try: srv.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        State.reg.shutdown(); srv.server_close()


# ---------------------------------------------------------------- terminal status screen (`sb`)
def graphics_kind():
    env = os.environ
    if env.get("KITTY_WINDOW_ID") or env.get("TERM") == "xterm-kitty" or env.get("TERM_PROGRAM") == "ghostty": return "kitty"
    if env.get("TERM_PROGRAM") in ("iTerm.app", "WezTerm"): return "iterm"
    return None


def image_seq(kind, jpeg, cols=40):
    """Escape sequence that draws a JPEG at the cursor, `cols` cells wide, without moving the cursor."""
    import base64
    if kind == "iterm":
        return f"\x1b]1337;File=inline=1;width={cols};preserveAspectRatio=1;size={len(jpeg)}:{base64.b64encode(jpeg).decode()}\x07"
    from PIL import Image
    buf = io.BytesIO(); Image.open(io.BytesIO(jpeg)).convert("RGB").save(buf, "PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    chunks = [b64[i:i + 4096] for i in range(0, len(b64), 4096)] or [""]
    out = []
    for i, c in enumerate(chunks):
        more = 1 if i < len(chunks) - 1 else 0
        head = f"a=T,f=100,c={cols},C=1,q=2,m={more}" if i == 0 else f"q=2,m={more}"
        out.append(f"\x1b_G{head};{c}\x1b\\")
    return "".join(out)


def state_line(st):
    if st.get("error") or not st.get("present", True): return "○", "stopped"
    if st.get("fps", 0) > 0 and (st.get("frame_age") is None or st["frame_age"] < 3): return "●", "live"
    return "◐", "idle"


def cmd_run(a):
    """Server + inline status lines (a line is printed only when the state changes)."""
    srv, listen, port, token = start_server(a)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"http://{'localhost' if listen in ('127.0.0.1', '0.0.0.0') else listen}:{port}/" + (f"?t={token}" if token else ""), flush=True)
    last = None
    try:
        while True:
            State.reg.refresh()
            sts = [c.status() for c in State.reg.cams.values()]
            dot, word = state_line(sts[0]) if sts else ("○", "stopped")
            if word != last:
                print(f"{dot} see {word}" + (f"  {sts[0]['name']}" if sts else "  no camera"), flush=True)
                last = word
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        State.reg.shutdown(); srv.shutdown(); srv.server_close()


def cmd_screen(a):
    """Server + a full-screen status view: Live / idle / stopped per camera, and (optionally) the latest picture."""
    import termios, tty
    srv, listen, port, token = start_server(a)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    kind = None if a.no_pic else graphics_kind()
    show_pic = kind is not None
    url = f"http://{'localhost' if listen in ('127.0.0.1', '0.0.0.0') else listen}:{port}/" + (f"?t={token}" if token else "")
    tty_ok = sys.stdin.isatty()
    old = None
    if tty_ok:
        old = termios.tcgetattr(sys.stdin); tty.setcbreak(sys.stdin.fileno())
    sys.stdout.write("\x1b[?25l\x1b[2J"); sys.stdout.flush()
    try:
        while True:
            reg = State.reg; reg.refresh()
            lines = ["PBSEE", ""]
            sts = [c.status() for c in reg.cams.values()]
            if not sts: lines.append("○ no camera")
            for st in sts:
                dot, word = state_line(st)
                age = st.get("seconds_since_change")
                lines.append(f"{dot} {word:<8}{st['name']}" + (f"  {st['fps']:.0f} fps  change {st['change']}" + (f"  {age:.0f}s" if age is not None else "") if word == "live" else ""))
            lines += ["", url, "", ("p pic  " if kind else "") + "q quit"]
            sys.stdout.write("\x1b[H" + "".join(l + "\x1b[K\n" for l in lines) + "\x1b[J")
            if show_pic and sts and sts[0]["present"]:
                try:
                    q = "?w=480&q=50" + (f"&t={token}" if token else "")
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/snap/{urllib.parse.quote(sts[0]['name'])}.jpg{q}", timeout=8) as r:
                        sys.stdout.write(image_seq(kind, r.read()))
                except Exception:
                    pass
            sys.stdout.flush()
            r, _, _ = select.select([sys.stdin], [], [], 1.0) if tty_ok else (select.select([], [], [], 1.0)[0], 0, 0)
            if r:
                k = sys.stdin.read(1)
                if k in ("q", "\x03"): break
                if k == "p" and kind:
                    show_pic = not show_pic
                    sys.stdout.write("\x1b[2J"); sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\x1b[?25h\x1b[2J\x1b[H"); sys.stdout.flush()
        if old: termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)
        State.reg.shutdown(); srv.shutdown(); srv.server_close()


def cmd_snap(a):
    cfg = load_config(); reg = Registry(cfg, a.test_source); reg.refresh()
    cam = reg.get(a.name) if a.name else next(iter(reg.cams.values()), None)
    if not cam: sys.exit("no such camera (try: pbsee.py list)")
    o = parse_opts({"raw": ["1"]} if a.raw else {}, cam.defaults())
    cam.touch(); seq, jpg = cam.wait_frame(cam.seq, 12); cam.shutdown()
    if jpg is None: sys.exit("no frame: " + (cam.error or "timeout"))
    out = a.o or f"{cam.name}.jpg"
    with open(out, "wb") as f: f.write(process(jpg, o))
    print(out)


def cmd_list(a):
    cams = discover()
    if not cams: print("No capture devices found. Run: python3 pbsee.py doctor"); return
    for c in cams:
        print(f"{slug(c['product'] or c['label'])}\n  label  : {c['label']}\n  device : {c['dev']}\n  stable : {c['stable']}"
              f"\n  usb    : {c['vid']}:{c['pid']} (bus {c['bus']})  serial: {c['serial'] or '-'}")
        if shutil.which("v4l2-ctl"):
            for f, sizes in formats(c["dev"]).items():
                top = sorted(sizes, key=lambda s: -s[0] * s[1])[:3]
                print(f"  {f:5}  " + ", ".join(f"{w}x{h}@{max(sizes[(w, h)] or [0]):g}" for w, h in top))


def cmd_probe(a):
    skeleton, claimed = [], {}
    for c in discover():
        key = f"{c['vid']}:{c['pid']}"
        claimed[key] = claimed.get(key, 0) + 1
    for c in discover():
        key = f"{c['vid']}:{c['pid']}"
        match = f"usb:{key}" if c["vid"] and claimed[key] == 1 else f"path:{c['stable']}"
        skeleton.append({"name": slug(c["product"] or c["label"]), "match": match, "size": "1280x720", "fps": 10,
                         "controls": {}, "opts": {}})
    print(json.dumps({"port": 8808, "listen": "127.0.0.1", "cameras": skeleton}, indent=2))


# ---------------------------------------------------------------- doctor / service
def cmd_doctor(a):
    fix = a.fix
    user = os.environ.get("SUDO_USER") or getpass.getuser()
    sudo = [] if os.geteuid() == 0 else ["sudo"]
    bad = 0
    def line(good, msg, hint=""):
        nonlocal bad
        if not good: bad += 1
        print(("  [ ok ] " if good else "  [ !! ] ") + msg + (f"\n          -> {hint}" if hint and not good else ""))
    print("pbsee doctor\n")
    pkgs = [p for p, have in (("ffmpeg", shutil.which("ffmpeg")), ("v4l-utils", shutil.which("v4l2-ctl"))) if not have]
    for mod, pkg in (("PIL", "python3-pil"), ("numpy", "python3-numpy")):
        try: __import__(mod)
        except ImportError: pkgs.append(pkg)
    line(not pkgs, "packages: " + (", ".join(pkgs) + " missing" if pkgs else "ffmpeg, v4l-utils, Pillow, numpy present"),
         f"sudo apt install -y {' '.join(pkgs)}   (or: python3 pbsee.py doctor --fix)")
    if pkgs and fix:
        print("     installing...", flush=True); subprocess.run(sudo + ["apt-get", "install", "-y"] + pkgs)
    usb = usb_video_devices()
    for d in usb: print(f"         USB camera seen by kernel: {d['product']} ({d['vid']}:{d['pid']}, bus {d['bus']})")
    mod = os.path.isdir(f"{SYSFS}/module/uvcvideo")
    line(mod or not usb, "uvcvideo kernel driver " + ("loaded" if mod else "not loaded"), "sudo modprobe uvcvideo")
    if not mod and usb and fix: subprocess.run(sudo + ["modprobe", "uvcvideo"])
    cams = discover()
    line(bool(cams), f"{len(cams)} capture device(s)",
         "kernel sees a USB camera but made no /dev/video: check `dmesg | grep -i uvc`" if usb else
         "no camera visible: replug it. For the internal camera (not USB/UVC) send this report back")
    groups = run(["id", "-nG", user]).split()
    for c in cams:
        acc = os.access(c["dev"], os.R_OK | os.W_OK)
        line(acc, f"{c['dev']} readable by {os.environ.get('USER', user)}", f"sudo usermod -aG video {user}, then log out/in")
        if not acc and "video" not in groups and fix:
            subprocess.run(sudo + ["usermod", "-aG", "video", user]); print("     added to 'video' group: log out and in again")
        if shutil.which("ffmpeg") and acc:
            tmp = Cam("probe", c["stable"], {"fps": 10, "size": "1280x720"}, c)
            try:
                r = subprocess.run(Cam.BASE + tmp._input()[0] + ["-frames:v", "3", "-f", "null", "-"],
                                   capture_output=True, text=True, timeout=25)
                ok, err = r.returncode == 0, (r.stderr.strip().splitlines() or [""])[-1]
            except subprocess.TimeoutExpired:
                ok, err = False, "timed out (camera busy? another program may be using it)"
            line(ok, f"capture test ({slug(c['product'] or c['label'])})", err or "ffmpeg failed")
    bus = {}
    for c in cams: bus.setdefault(c["bus"] or "?", []).append(c)
    for b, lst in bus.items():
        if len(lst) > 1:
            print(f"  [ note ] {len(lst)} cameras share USB bus {b}: fine here (cameras only stream while viewed),"
                  " but two at high resolution at once may exceed bandwidth. Use size 640x480 in pbsee.json if so.")
    print(f"  [ info ] config: {CONFIG} ({'exists' if os.path.exists(CONFIG) else 'none yet; `probe` prints a skeleton'})")
    print("\n" + ("All good. Start it: python3 pbsee.py serve" if not bad else
                  f"{bad} issue(s). Re-run with --fix for the ones apt/modprobe/usermod can solve."))


def install_launcher():
    sudo = [] if os.geteuid() == 0 else ["sudo"]
    script = ('#!/bin/sh\n# PBSEE launcher (created by pbsee.py install). Alone it runs the server with inline status.\n'
              f'[ $# -eq 0 ] && set -- run\nexec {sys.executable} "{os.path.abspath(__file__)}" "$@"\n')
    for name in ("pbs", "pbc"):
        subprocess.run(sudo + ["tee", f"/usr/local/bin/{name}"], input=script, text=True, stdout=subprocess.DEVNULL, check=True)
        subprocess.run(sudo + ["chmod", "755", f"/usr/local/bin/{name}"], check=True)
    print("Installed pbs and pbc")


def cmd_install(a):
    install_launcher()


def cmd_service(a):
    user = os.environ.get("SUDO_USER") or getpass.getuser()
    unit = f"""[Unit]
Description=pbsee webcam viewer
After=network.target

[Service]
User={user}
SupplementaryGroups=video
ExecStart={sys.executable} {os.path.abspath(__file__)} serve
Restart=on-failure

[Install]
WantedBy=multi-user.target
"""
    sudo = [] if os.geteuid() == 0 else ["sudo"]
    subprocess.run(sudo + ["tee", "/etc/systemd/system/pbsee.service"], input=unit, text=True, stdout=subprocess.DEVNULL, check=True)
    subprocess.run(sudo + ["systemctl", "daemon-reload"], check=True)
    subprocess.run(sudo + ["systemctl", "enable", "--now", "pbsee"], check=True)
    print("pbsee service installed and started (config in", CONFIG + ")")
    install_launcher()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("doctor"); d.add_argument("--fix", action="store_true"); d.set_defaults(fn=cmd_doctor)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    sub.add_parser("probe").set_defaults(fn=cmd_probe)
    s = sub.add_parser("serve"); s.add_argument("--listen"); s.add_argument("--port", type=int); s.add_argument("--token")
    s.add_argument("--test-source", action="store_true", help="add a synthetic camera (no hardware needed)"); s.set_defaults(fn=cmd_serve)
    r = sub.add_parser("run"); r.add_argument("--listen"); r.add_argument("--port", type=int); r.add_argument("--token")
    r.add_argument("--test-source", action="store_true"); r.set_defaults(fn=cmd_run)
    r = sub.add_parser("screen"); r.add_argument("--listen"); r.add_argument("--port", type=int); r.add_argument("--token")
    r.add_argument("--no-pic", action="store_true"); r.add_argument("--test-source", action="store_true"); r.set_defaults(fn=cmd_screen)
    n = sub.add_parser("snap"); n.add_argument("name", nargs="?"); n.add_argument("-o"); n.add_argument("--raw", action="store_true")
    n.add_argument("--test-source", action="store_true"); n.set_defaults(fn=cmd_snap)
    sub.add_parser("install").set_defaults(fn=cmd_install)
    sub.add_parser("install-service").set_defaults(fn=cmd_service)
    a = ap.parse_args()
    a.fn(a)

if __name__ == "__main__":
    main()
