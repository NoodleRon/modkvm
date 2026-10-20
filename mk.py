#!/usr/bin/env python3
"""modkvm: turn this Linux server into a Bluetooth keyboard + mouse for your devices, with a webcam view of
their screens. Primary command: mk; camera module: mk cam; MCP: mk mcp.
Legacy aliases below remain compatible during migration:

  pb / pbk / pbkb   kb mode (default): the terminal (and trackpad) as a BT keyboard + mouse. Runs as root.
                    Everything you type is transmitted live to the active phone (nothing buffered/echoed).
                    Ctrl+] opens the menu: 1-9 switch | p pair | s solo | m mouse (terminal) | t trackpad (server) |
                    a map/relative | h re-home pointer | + - map size | c camera | n name | k keyboard on/off |
                    u hosting | d drop | f forget | r restart | q quit
                    Also starts the local HTTP API (127.0.0.1:8809) and, unless --no-web, the web console + MCP
                    (http://localhost:8800, see mode 'web' below). The camera (mode 'see') starts automatically too
                    (remembered; toggle with the 'c' menu key). See (webcam) shows as an indicator line;
                    w prints the web link.
  pb --pads         list input devices + live event test, then exit (no Bluetooth/web/camera started)
  pbs / pbc         see mode: `python3 mk.py see ARGS` (ARGS = doctor/list/probe/serve/run/screen/snap/
                    install/install-service). Multi-camera viewer; separate process from kb mode on purpose (a
                    camera crash/hang can't take down the keyboard). Runs as your normal user (the `video` group).
  (claude mcp add)  mcp mode: `python3 mk.py mcp` -- stdio MCP server (phone_status/_use/_type/_key/_tap/
                    _scroll/_drag/_home_pointer/_look). The hub already serves the same tools over HTTP at /mcp;
                    use this mode only for clients that need stdio.
  (rarely needed)   web mode: `python3 mk.py web [--listen ADDR] [--port N] [--token T]` -- the web
                    console + MCP-over-HTTP gateway standalone, against an already-running kb/see mode elsewhere.
                    Normally kb mode starts this itself, in-process.

kb mode flags: --no-mouse (keyboard-only descriptor; re-pair after changing) | --no-pad | --no-term-mouse
       --no-api | --no-web | --api-port N | --api-listen ADDR | --web-port N | --web-listen ADDR | --web-token T
       --relay URL --relay-token T | --pad (use the server's own trackpad/mouse even over SSH) | --pads
--install (any mode): creates mk plus pb, pbk, pbkb, pbs, pbc compatibility aliases. See mode details: `mk.py see -h`.
install-service: `mk install-service` installs both systemd units (Key + Cam); `mk kb install-service` or
       `mk see install-service` installs just one. Key's unit runs `mk kb` headless (no TTY needed).
Details: README.md / ARCHITECTURE.md
"""
import os, re, sys, io, glob, json, math, time, socket, struct, select, termios, atexit, getpass
import subprocess, threading, signal, logging, fcntl, hmac, secrets, stat, shutil, base64, argparse
import urllib.request, urllib.parse, urllib.error, hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
try:
    import dbus, dbus.service, dbus.mainloop.glib
    from gi.repository import GLib
except ImportError:
    # Key (kb mode) needs python3-dbus + python3-gi (Linux). Cam/MCP/web modes and --help don't;
    # stub just enough for the class definitions so those still import and run anywhere.
    class _StubObject:
        def __init__(self, *a, **k): raise ImportError("kb mode needs python3-dbus + python3-gi (Linux)")
    def _stub_decorator(*a, **k): return lambda f: f
    def _stub_fail(*a, **k): raise ImportError("kb mode needs python3-dbus + python3-gi (Linux)")
    dbus = SimpleNamespace(
        service=SimpleNamespace(Object=_StubObject, method=_stub_decorator),
        mainloop=SimpleNamespace(glib=SimpleNamespace(DBusGMainLoop=_stub_fail, threads_init=_stub_fail)),
        Boolean=bool, String=str, UInt32=int,
        exceptions=SimpleNamespace(DBusException=Exception),
        SystemBus=_stub_fail, Interface=_stub_fail)
    GLib = SimpleNamespace(MainLoop=_stub_fail, timeout_add=_stub_fail)

CLIENT_DISCONNECTS = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, socket.timeout)


class QuietHTTPHandler(BaseHTTPRequestHandler):
    """A browser canceling a request is routine; application errors still reach the server log."""
    def handle(self):
        try: super().handle()
        except CLIENT_DISCONNECTS: pass

    def _client_gone(self):
        try:
            ready, _, _ = select.select([self.connection], [], [], 0)
            return bool(ready) and not self.connection.recv(1, socket.MSG_PEEK)
        except (OSError, ValueError): return True

HERE = os.path.dirname(os.path.abspath(__file__))

logging.basicConfig(level=logging.INFO if ("-v" in sys.argv or "--verbose" in sys.argv) else logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

def _version():
    try: return open(os.path.join(HERE, "VERSION")).read().strip()
    except OSError: return "0.0.00001"
VERSION = _version()
def _build():
    try:
        with open(__file__, "rb") as f: return hashlib.sha256(f.read()).hexdigest()[:12]
    except OSError: return "unknown"
BUILD = _build()             # source loaded by this process, unchanged if the file is edited later
SEE_DOT = {"live": "● live", "idle": "◐ on, idle", "stopped": "○ off"}
HID_UUID = "00001124-0000-1000-8000-00805f9b34fb"
PSM_CTRL, PSM_INTR = 0x11, 0x13
ADAPTER = "/org/bluez/hci0"
STATE_FILE = os.path.join(HERE, ".mkkey_state.json")
STATE_FILE_LEGACY = os.path.join(HERE, ".pbkey_state.json")   # read-only fallback; saves always use STATE_FILE
MENU_KEY = 0x1D            # Ctrl+]  (change here if you want another menu hotkey)

# ---- tuning knobs
CELL_W, CELL_H = 9, 18     # mouse counts per terminal cell moved (terminals without pixel reporting)
PAD_GAIN = 1200            # mouse counts for a full trackpad-width swipe
PAD_SCROLL = 24            # wheel clicks for a full trackpad-height two-finger swipe
PAD_NATURAL = True         # two-finger scroll direction (flip if it feels backwards)
TAP_SECONDS = 0.25         # a touch shorter than this, with little movement, is a tap-click
MAP_W, MAP_H = 1500, 2800  # mouse counts that span the phone screen (iOS pointer speed varies: tune with Ctrl+] + / -)
PAD_RESCAN = 3.0           # seconds between looks for a newly plugged trackpad/mouse
KB_PORT, SB_PORT, DB_PORT = 8809, 8808, 8800     # kb API, see API, web gateway

MOUSE = "--no-mouse" not in sys.argv
TOKEN_FILE = os.path.join(HERE, ".mkkey_token")
TOKEN_FILE_LEGACY = os.path.join(HERE, ".pbkey_token")        # read-only fallback; new tokens are written to TOKEN_FILE


def argval(flag, default):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv): return sys.argv[i + 1]
    return default

# Keyboard: report ID 1 (8 modifier bits, 1 reserved, 5 LEDs + pad, 6 keys)
KBD_DESC = bytes([
    0x05,0x01,0x09,0x06,0xA1,0x01,0x85,0x01,
    0x75,0x01,0x95,0x08,0x05,0x07,0x19,0xE0,0x29,0xE7,0x15,0x00,0x25,0x01,0x81,0x02,
    0x95,0x01,0x75,0x08,0x81,0x03,
    0x95,0x05,0x75,0x01,0x05,0x08,0x19,0x01,0x29,0x05,0x91,0x02,
    0x95,0x01,0x75,0x03,0x91,0x03,
    0x95,0x06,0x75,0x08,0x15,0x00,0x26,0xFF,0x00,0x05,0x07,0x19,0x00,0x29,0xFF,0x81,0x00,
    0xC0])
# Mouse: report ID 2 (3 buttons + pad, then int8 X, Y, wheel; all relative)
MOUSE_DESC = bytes([
    0x05,0x01,0x09,0x02,0xA1,0x01,0x85,0x02,
    0x09,0x01,0xA1,0x00,
    0x05,0x09,0x19,0x01,0x29,0x03,0x15,0x00,0x25,0x01,0x95,0x03,0x75,0x01,0x81,0x02,
    0x95,0x01,0x75,0x05,0x81,0x03,
    0x05,0x01,0x09,0x30,0x09,0x31,0x09,0x38,0x15,0x81,0x25,0x7F,0x75,0x08,0x95,0x03,0x81,0x06,
    0xC0,0xC0])
REPORT_DESC = KBD_DESC + (MOUSE_DESC if MOUSE else b"")
DEV_SUBCLASS = 0xC0 if MOUSE else 0x40      # 0x40 keyboard, 0xC0 keyboard+pointing

SDP_RECORD = f"""<?xml version="1.0" encoding="UTF-8" ?>
<record>
 <attribute id="0x0001"><sequence><uuid value="0x1124"/></sequence></attribute>
 <attribute id="0x0004"><sequence><sequence><uuid value="0x0100"/><uint16 value="0x0011"/></sequence><sequence><uuid value="0x0011"/></sequence></sequence></attribute>
 <attribute id="0x0005"><sequence><uuid value="0x1002"/></sequence></attribute>
 <attribute id="0x0006"><sequence><uint16 value="0x656e"/><uint16 value="0x006a"/><uint16 value="0x0100"/></sequence></attribute>
 <attribute id="0x0009"><sequence><sequence><uuid value="0x1124"/><uint16 value="0x0100"/></sequence></sequence></attribute>
 <attribute id="0x000d"><sequence><sequence><sequence><uuid value="0x0100"/><uint16 value="0x0013"/></sequence><sequence><uuid value="0x0011"/></sequence></sequence></sequence></attribute>
 <attribute id="0x0100"><text value="modkvm Keyboard"/></attribute>
 <attribute id="0x0101"><text value="Keyboard"/></attribute>
 <attribute id="0x0102"><text value="modkvm"/></attribute>
 <attribute id="0x0200"><uint16 value="0x0100"/></attribute>
 <attribute id="0x0201"><uint16 value="0x0111"/></attribute>
 <attribute id="0x0202"><uint8 value="0x{DEV_SUBCLASS:02X}"/></attribute>
 <attribute id="0x0203"><uint8 value="0x00"/></attribute>
 <attribute id="0x0204"><boolean value="true"/></attribute>
 <attribute id="0x0205"><boolean value="true"/></attribute>
 <attribute id="0x0206"><sequence><sequence><uint8 value="0x22"/><text encoding="hex" value="{REPORT_DESC.hex()}"/></sequence></sequence></attribute>
 <attribute id="0x0207"><sequence><sequence><uint16 value="0x0409"/><uint16 value="0x0100"/></sequence></sequence></attribute>
 <attribute id="0x020b"><uint16 value="0x0100"/></attribute>
 <attribute id="0x020c"><uint16 value="0x0c80"/></attribute>
 <attribute id="0x020d"><boolean value="true"/></attribute>
 <attribute id="0x020e"><boolean value="false"/></attribute>
 <attribute id="0x020f"><uint16 value="0x0640"/></attribute>
 <attribute id="0x0210"><uint16 value="0x0320"/></attribute>
</record>"""

# ---------------------------------------------------------------- keyboard tables (US layout)
ASCII = {" ": (0, 0x2C)}
for _i, _c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    ASCII[_c] = (0, 0x04 + _i); ASCII[_c.upper()] = (0x02, 0x04 + _i)
for _i, (_a, _b) in enumerate(zip("1234567890", "!@#$%^&*()")):
    ASCII[_a] = (0, 0x1E + _i); ASCII[_b] = (0x02, 0x1E + _i)
for _code, (_lo, _hi) in {0x2D: ("-", "_"), 0x2E: ("=", "+"), 0x2F: ("[", "{"), 0x30: ("]", "}"),
                          0x31: ("\\", "|"), 0x33: (";", ":"), 0x34: ("'", '"'), 0x35: ("`", "~"),
                          0x36: (",", "<"), 0x37: (".", ">"), 0x38: ("/", "?")}.items():
    ASCII[_lo] = (0, _code); ASCII[_hi] = (0x02, _code)

K_ENTER, K_ESC, K_BKSP, K_TAB = 0x28, 0x29, 0x2A, 0x2B
CSI_KEYS = {b"A": 0x52, b"B": 0x51, b"C": 0x4F, b"D": 0x50, b"H": 0x4A, b"F": 0x4D}   # up down right left home end
TILDE_KEYS = {b"1": 0x4A, b"2": 0x49, b"3": 0x4C, b"4": 0x4D, b"5": 0x4B, b"6": 0x4E, b"7": 0x4A, b"8": 0x4D}

KEY_NAMES = {"enter": 0x28, "return": 0x28, "esc": 0x29, "escape": 0x29, "backspace": 0x2A, "tab": 0x2B,
             "space": 0x2C, "delete": 0x4C, "home": 0x4A, "end": 0x4D, "pageup": 0x4B, "pagedown": 0x4E,
             "up": 0x52, "down": 0x51, "left": 0x50, "right": 0x4F}
for _n in range(1, 13): KEY_NAMES[f"f{_n}"] = 0x3A + _n - 1
MOD_NAMES = {"ctrl": 1, "control": 1, "shift": 2, "alt": 4, "opt": 4, "option": 4,
             "cmd": 8, "command": 8, "gui": 8, "super": 8, "meta": 8, "win": 8}


def parse_combo(text):
    """'cmd+shift+4' / 'enter' / 'ctrl+c' -> (modifier_bits, hid_code). Raises ValueError."""
    mod, code = 0, None
    for part in [p.strip().lower() for p in str(text).split("+") if p.strip()]:
        if part in MOD_NAMES and not (code is None and part == str(text).strip().lower() and False):
            mod |= MOD_NAMES[part]
        elif part in KEY_NAMES:
            code = KEY_NAMES[part]
        elif len(part) == 1 and part in ASCII:
            m, c = ASCII[part]; mod |= m; code = c
        else:
            raise ValueError(f"unknown key '{part}'")
    if code is None: raise ValueError("no key in combo (modifiers alone can't be sent)")
    return mod, code


def parse_input(data):
    """Terminal bytes -> ([('key', mod, hid) | ('mouse', cb, x, y, pressed) | ('mode', n, status)
    | ('menu',) | ('skip',)], leftover_bytes_of_an_incomplete_escape_sequence)."""
    ev, i, n = [], 0, len(data)
    while i < n:
        start = i
        b = data[i]; i += 1
        if b == MENU_KEY:
            ev.append(("menu",))
        elif b == 0x1B:
            if i < n and data[i] in (0x5B, 0x4F):            # CSI / SS3 sequences
                intro = data[i]; i += 1
                params = b""
                while i < n and not (0x40 <= data[i] <= 0x7E):
                    params += bytes([data[i]]); i += 1
                if i >= n:
                    return ev, data[start:]                  # incomplete: wait for the rest
                final = bytes([data[i]]); i += 1
                if intro == 0x5B and params.startswith(b"<") and final in (b"M", b"m"):
                    try:
                        cb, x, y = (int(v) for v in params[1:].split(b";"))
                        ev.append(("mouse", cb, x, y, final == b"M"))
                    except ValueError:
                        pass
                elif intro == 0x5B and final == b"y" and params.startswith(b"?") and params.endswith(b"$"):
                    try:
                        mode, status = (int(v) for v in params[1:-1].split(b";"))
                        ev.append(("mode", mode, status))
                    except ValueError:
                        pass
                else:
                    code = TILDE_KEYS.get(params.split(b";")[0]) if final == b"~" else CSI_KEYS.get(final)
                    if code:
                        ev.append(("key", 0, code))
            elif i < n:                                      # Alt+char
                ch = chr(data[i]); i += 1
                if ch in ASCII:
                    m, k = ASCII[ch]; ev.append(("key", m | 0x04, k))
            else:
                ev.append(("key", 0, K_ESC))
        elif b in (0x0D, 0x0A):
            ev.append(("key", 0, K_ENTER))
        elif b in (0x7F, 0x08):
            ev.append(("key", 0, K_BKSP))
        elif b == 0x09:
            ev.append(("key", 0, K_TAB))
        elif 0x01 <= b <= 0x1A:                              # Ctrl+letter
            ev.append(("key", 0x01, 0x04 + b - 1))
        elif 0x20 <= b < 0x7F:
            m, k = ASCII[chr(b)]; ev.append(("key", m, k))
        elif b >= 0xC0:                                      # UTF-8 lead byte: can't type it
            i += 3 if b >= 0xF0 else 2 if b >= 0xE0 else 1
            ev.append(("skip",))
    return ev, b""


# ---------------------------------------------------------------- server trackpad / mouse (evdev)
EV_SYN, EV_KEY, EV_REL, EV_ABS = 0, 1, 2, 3
REL_X, REL_Y, REL_WHEEL = 0, 1, 8
ABS_X, ABS_Y = 0, 1
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE, BTN_TOUCH = 0x110, 0x111, 0x112, 0x14A
BTN_TOOL_FINGER, BTN_TOOL_DOUBLETAP, BTN_TOOL_TRIPLETAP = 0x145, 0x14D, 0x14E


class PadTracker:
    """Pure logic: raw evdev events -> emit(dx, dy, wheel, buttons). Handles mice and touchpads."""
    def __init__(self, kind, xr, yr, emit):
        self.kind, self.emit = kind, emit
        self.xr, self.yr = max(int(xr), 1), max(int(yr), 1)
        self.btn = 0
        self.btn_for = {}                       # physical button -> HID bit it pressed
        self.rx = self.ry = self.wheel = 0      # relative-device accumulators
        self.x = self.y = self.px = self.py = None
        self.tools = {BTN_TOOL_FINGER: 0, BTN_TOOL_DOUBLETAP: 0, BTN_TOOL_TRIPLETAP: 0}
        self.fingers = self.maxf = 0
        self.touching = self.clicked = False
        self.t0 = 0.0
        self.moved = 0
        self.rem_x = self.rem_y = self.rem_w = 0.0

    def feed(self, etype, code, value, now=None):
        now = time.monotonic() if now is None else now
        if etype == EV_KEY:
            if code in (BTN_LEFT, BTN_RIGHT, BTN_MIDDLE):
                if value:
                    bit = {BTN_LEFT: 2 if self.fingers >= 2 else 1, BTN_RIGHT: 2, BTN_MIDDLE: 4}[code]
                    self.btn_for[code] = bit
                    self.btn |= bit
                    self.clicked = True
                else:
                    self.btn &= ~self.btn_for.pop(code, 0)
                self.emit(0, 0, 0, self.btn)
            elif code in self.tools:
                self.tools[code] = 1 if value else 0
                n = 3 if self.tools[BTN_TOOL_TRIPLETAP] else 2 if self.tools[BTN_TOOL_DOUBLETAP] else \
                    1 if self.tools[BTN_TOOL_FINGER] else 0
                if n != self.fingers:
                    self.fingers = n
                    self.px = self.py = None            # avoid a jump when the finger count changes
                    self.maxf = max(self.maxf, n)
            elif code == BTN_TOUCH:
                if value:
                    self.touching, self.t0, self.moved, self.clicked = True, now, 0, False
                    self.maxf = max(self.fingers, 1)
                    self.px = self.py = None
                else:
                    was = self.touching
                    self.touching = False
                    self.px = self.py = None
                    if (was and not self.clicked and now - self.t0 < TAP_SECONDS
                            and self.moved < 0.025 * self.xr):
                        bit = 2 if self.maxf >= 2 else 1
                        self.emit(0, 0, 0, self.btn | bit)
                        self.emit(0, 0, 0, self.btn)
        elif etype == EV_ABS:
            if code == ABS_X: self.x = value
            elif code == ABS_Y: self.y = value
        elif etype == EV_REL:
            if code == REL_X: self.rx += value
            elif code == REL_Y: self.ry += value
            elif code == REL_WHEEL: self.wheel += value
        elif etype == EV_SYN and code == 0:
            self._frame()

    def _frame(self):
        if self.kind == "rel":
            if self.rx or self.ry or self.wheel:
                self.emit(self.rx, self.ry, self.wheel, self.btn)
            self.rx = self.ry = self.wheel = 0
            return
        if not self.touching or self.x is None or self.y is None:
            return
        if self.px is not None:
            dx, dy = self.x - self.px, self.y - self.py
            self.moved += abs(dx) + abs(dy)
            if self.fingers >= 2:
                self.rem_w += dy / self.yr * PAD_SCROLL * (1 if PAD_NATURAL else -1)
                w = int(self.rem_w); self.rem_w -= w
                if w: self.emit(0, 0, w, self.btn)
            else:
                self.rem_x += dx / self.xr * PAD_GAIN       # same scale on both axes (isotropic)
                self.rem_y += dy / self.xr * PAD_GAIN
                ix, iy = int(self.rem_x), int(self.rem_y)
                self.rem_x -= ix; self.rem_y -= iy
                if ix or iy: self.emit(ix, iy, 0, self.btn)
        self.px, self.py = self.x, self.y


def classify(name, caps):
    """evdev capabilities -> ('rel', None) for mice, ('abs', (xrange, yrange)) for touchpads, else None."""
    low = name.lower()
    if "touchscreen" in low or "stylus" in low:
        return None
    keys = caps.get(EV_KEY, [])
    if REL_X in caps.get(EV_REL, []) and REL_Y in caps.get(EV_REL, []) and BTN_LEFT in keys:
        return ("rel", None)
    ab = {c: info for c, info in caps.get(EV_ABS, [])}
    if ABS_X in ab and ABS_Y in ab and BTN_TOUCH in keys and (BTN_LEFT in keys or BTN_TOOL_FINGER in keys):
        return ("abs", (ab[ABS_X].max - ab[ABS_X].min, ab[ABS_Y].max - ab[ABS_Y].min))
    return None


class PointerSource(threading.Thread):
    def __init__(self, dev, tracker):
        super().__init__(daemon=True)
        self.dev, self.tracker, self.halt = dev, tracker, threading.Event()

    def run(self):
        try: self.dev.grab()                     # the server console stops reacting while captured
        except Exception as e: say(f"(could not grab {self.dev.name}: {e})")
        try:
            while not self.halt.is_set():
                r, _, _ = select.select([self.dev.fd], [], [], 0.2)
                if not r: continue
                try: events = list(self.dev.read())
                except BlockingIOError: continue
                except OSError: break
                for e in events:
                    self.tracker.feed(e.type, e.code, e.value)
        finally:
            try: self.dev.ungrab()
            except Exception: pass
            try: self.dev.close()
            except Exception: pass


def open_pointer_devices(evdev, emit, skip=()):
    srcs = []
    for path in evdev.list_devices():
        if path in skip: continue
        try:
            dev = evdev.InputDevice(path)
            kind = classify(dev.name, dev.capabilities())
            if kind and kind[0] == "abs" and 1 in (getattr(dev, "input_props", lambda: [])() or []): kind = None   # INPUT_PROP_DIRECT = touchscreen
        except Exception:
            continue
        if not kind:
            try: dev.close()
            except Exception: pass
            continue
        k, rng = kind
        srcs.append(PointerSource(dev, PadTracker(k, *(rng or (1, 1)), emit)))
        say(f"  using {dev.name} ({'mouse' if k == 'rel' else 'touchpad'})")
    for s in srcs: s.start()
    return srcs


def is_ssh():
    """True when this session came in over SSH (also through sudo, which strips the variables)."""
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"): return True
    pid = os.getppid()
    for _ in range(6):
        try:
            if b"SSH_CONNECTION=" in open(f"/proc/{pid}/environ", "rb").read(): return True
            pid = int(open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return False
        if pid <= 1: return False
    return False


def firewall_hint(port):
    """ufw active and not allowing the port -> the command that fixes it, else ''."""
    try:
        out = subprocess.run(["ufw", "status"], capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return ""
    if "Status: active" not in out or re.search(rf"^{port}(/tcp)?\s+ALLOW", out, re.M): return ""
    return f"sudo ufw allow {port}/tcp"


def pad_diagnose(seconds=6):
    """sudo python3 mk.py --pads : every input device, how it is classified, and live event counts."""
    try:
        import evdev
    except ImportError:
        print("python3-evdev missing: sudo apt install python3-evdev"); return
    if os.geteuid() != 0: print("(not root: devices may be unreadable, run with sudo)")
    devs = []
    for path in evdev.list_devices():
        try:
            d = evdev.InputDevice(path); kind = classify(d.name, d.capabilities())
            direct = 1 in (getattr(d, "input_props", lambda: [])() or [])
            print(f"{path:<20}{d.name[:36]:<38}{('mouse' if kind and kind[0]=='rel' else 'touchpad' if kind and not direct else 'skip')}")
            devs.append((d, kind and not direct))
        except Exception as e:
            print(f"{path:<20}unreadable ({e})")
    print(f"\nmove / touch the pointer now ({seconds}s)...")
    counts = {d.path: 0 for d, _ in devs}
    end = time.time() + seconds
    while time.time() < end:
        r, _, _ = select.select([d.fd for d, _ in devs], [], [], 0.2)
        for d, _ in devs:
            if d.fd in r:
                try: counts[d.path] += sum(1 for e in d.read() if e.type in (EV_REL, EV_ABS))
                except OSError: pass
    for d, usable in devs:
        if counts[d.path]: print(f"{d.path:<20}{counts[d.path]:>6} motion events  {d.name}{'' if usable else '  (not used: ' + ('touchscreen' if 1 in (getattr(d, 'input_props', lambda: [])() or []) else 'no pointer buttons/axes') + ')'}")
    if not any(counts.values()): print("no motion seen on any device")


# ---------------------------------------------------------------- terminal helpers
_TTY = {"fd": None, "old": None}
_MOUSE_REPORTING = {"on": False}
# Terminal queries. A terminal that doesn't know them may print the last letter ("p"): save the cursor, ask, put it back, erase.
PROBE_1006 = "\x1b7\x1b[?1006$p\x1b8\x1b[K"
PROBE_1016 = "\x1b7\x1b[?1016$p\x1b8\x1b[K"
MOUSE_OFF_SEQ = "\x1b[?1016l\x1b[?1003l\x1b[?1006l"

def tty_raw(fd):
    old = termios.tcgetattr(fd)
    new = termios.tcgetattr(fd)
    new[0] &= ~(termios.BRKINT | termios.ICRNL | termios.INPCK | termios.ISTRIP | termios.IXON)
    new[2] |= termios.CS8
    new[3] &= ~(termios.ECHO | termios.ICANON | termios.IEXTEN | termios.ISIG)
    new[6][termios.VMIN] = 1
    new[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSADRAIN, new)
    _TTY["fd"], _TTY["old"] = fd, old

def tty_restore():
    if _MOUSE_REPORTING["on"]:
        raw_out(MOUSE_OFF_SEQ); _MOUSE_REPORTING["on"] = False
    if _TTY["old"] is not None:
        try: termios.tcsetattr(_TTY["fd"], termios.TCSADRAIN, _TTY["old"])
        except Exception: pass
        _TTY["old"] = None

def raw_out(s):
    try:
        sys.stdout.write(s); sys.stdout.flush()
    except Exception:
        pass

def say(msg="", end="\n"):
    print(msg, end=end, flush=True)

def hyperlink(url, text=None):
    """Clickable link (OSC 8); terminals without support just show the text."""
    return f"\x1b]8;;{url}\x1b\\{text or url}\x1b]8;;\x1b\\"


def hyperlink_text(link, tail=""):
    return f"{hyperlink(link)}{tail}" if link else tail


atexit.register(tty_restore)


def cam_autofix():
    """Quietly do what `see doctor --fix` would do, before Cam's first start. Called from Key (root
    already), so no interactive sudo prompt is needed - apt/modprobe/usermod just run directly. Cheap
    and idempotent once fixed: a camera subprocess spawned after a usermod picks up the new group
    immediately (fresh process, fresh group lookup), no logout/login needed. Returns what it changed,
    or [] if nothing was."""
    changed = []
    sudo = [] if os.geteuid() == 0 else ["sudo"]
    pkgs = [p for p, have in (("ffmpeg", shutil.which("ffmpeg")), ("v4l-utils", shutil.which("v4l2-ctl"))) if not have]
    for mod, pkg in (("PIL", "python3-pil"), ("numpy", "python3-numpy")):
        try: __import__(mod)
        except ImportError: pkgs.append(pkg)
    if pkgs:
        subprocess.run(sudo + ["apt-get", "install", "-y"] + pkgs, capture_output=True)
        changed.append(", ".join(pkgs))
    if usb_video_devices() and not os.path.isdir(f"{SYSFS}/module/uvcvideo"):
        subprocess.run(sudo + ["modprobe", "uvcvideo"], capture_output=True)
        changed.append("uvcvideo driver")
    user = os.environ.get("SUDO_USER") or getpass.getuser()
    if "video" not in run(["id", "-nG", user]).split():
        subprocess.run(sudo + ["usermod", "-aG", "video", user], capture_output=True)
        changed.append(f"added {user} to the video group")
    return changed


# ---------------------------------------------------------------- state / bluez helpers
def load_state():
    for p in (STATE_FILE, STATE_FILE_LEGACY):              # new name first, legacy fallback
        try:
            with open(p) as f: return json.load(f)
        except Exception: pass
    return {}

def save_state(**kw):
    st = load_state(); st.update(kw)
    try:
        with open(STATE_FILE, "w") as f: json.dump(st, f)
    except Exception as e:
        logging.warning(f"state save failed: {e}")

def dev_path(addr):
    return ADAPTER + "/dev_" + addr.replace(":", "_")

def trust(bus, path):
    try:
        dbus.Interface(bus.get_object("org.bluez", path), "org.freedesktop.DBus.Properties") \
            .Set("org.bluez.Device1", "Trusted", dbus.Boolean(True), signature="ssv")
    except Exception as e:
        logging.warning(f"trust failed: {e}")

def bonds(bus, timeout=None):
    out = []
    om = dbus.Interface(bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
    objects = om.GetManagedObjects(**({"timeout": timeout} if timeout is not None else {}))
    for path, ifs in objects.items():
        d = ifs.get("org.bluez.Device1")
        if d and d.get("Paired", False):
            addr = str(d.get("Address", "")).upper()
            out.append({"path": str(path), "addr": addr,
                        "name": str(d.get("Name", d.get("Alias", addr))),
                        "link": bool(d.get("Connected", False))})
    out.sort(key=lambda b: (b["name"].lower(), b["addr"]))
    return out

def set_prop(bus, name, val):
    dbus.Interface(bus.get_object("org.bluez", ADAPTER), "org.freedesktop.DBus.Properties") \
        .Set("org.bluez.Adapter1", name, val, signature="ssv")


class Agent(dbus.service.Object):
    def __init__(self, bus, path):
        super().__init__(bus, path); self.bus = bus

    @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
    def Release(self): pass

    @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
    def AuthorizeService(self, device, uuid):
        trust(self.bus, device)

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="")
    def RequestAuthorization(self, device):
        trust(self.bus, device)

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="s")
    def RequestPinCode(self, device): return "0000"

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="u")
    def RequestPasskey(self, device): return dbus.UInt32(123456)

    @dbus.service.method("org.bluez.Agent1", in_signature="ouq", out_signature="")
    def DisplayPasskey(self, device, passkey, entered):
        logging.info(f"DisplayPasskey {int(passkey):06d}")

    @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
    def DisplayPinCode(self, device, pin): logging.info(f"PIN {pin}")

    @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="")
    def RequestConfirmation(self, device, passkey):
        logging.info(f"Pairing confirm {int(passkey):06d} from {device} (auto-accepted)")
        trust(self.bus, device)

    @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
    def Cancel(self): logging.info("Agent: pairing cancelled")


class Profile(dbus.service.Object):
    @dbus.service.method("org.bluez.Profile1", in_signature="", out_signature="")
    def Release(self): pass

    @dbus.service.method("org.bluez.Profile1", in_signature="oha{sv}", out_signature="")
    def NewConnection(self, path, fd, props):
        try: os.close(fd.take())
        except Exception: pass

    @dbus.service.method("org.bluez.Profile1", in_signature="o", out_signature="")
    def RequestDisconnection(self, path): pass


class ReconnectPlan:
    """A finite retry window for one selected address; never sends HID input."""
    DELAYS = (1, 2, 4, 8, 16, 30, 30, 30, 30, 30, 30, 30)
    WINDOW = 300.0

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.target = None
        self.generation = 0
        self.attempts = 0
        self.deadline = self.next_at = None
        self.state, self.error = "idle", ""

    def cancel(self, state="idle"):
        self.generation += 1
        self.target = self.deadline = self.next_at = None
        self.state, self.error = state, ""

    def arm(self, target):
        # Repeated disconnect notifications must not reset an exhausted budget.
        if not target or self.target == target: return
        self.generation += 1
        self.target, self.attempts = target, 0
        self.deadline = self.clock() + self.WINDOW
        self.next_at = self.clock() + self.DELAYS[0]
        self.state, self.error = "waiting", ""

    def due(self):
        if self.target and self.deadline is not None and self.clock() >= self.deadline:
            self.state, self.next_at = "exhausted", None
        return self.state == "waiting" and self.next_at is not None and self.clock() >= self.next_at

    def begin(self):
        self.attempts += 1
        self.state, self.next_at = "connecting", None

    def failed(self, error):
        self.error = str(error or "connection failed")[:160]
        if self.attempts >= len(self.DELAYS) or self.clock() >= self.deadline:
            self.state, self.next_at = "exhausted", None
        else:
            self.state = "waiting"
            self.next_at = min(self.deadline, self.clock() + self.DELAYS[self.attempts])

    def snapshot(self):
        return {"target": self.target, "state": self.state, "attempts": self.attempts,
                "max_attempts": len(self.DELAYS), "window_seconds": self.WINDOW,
                "next_in": None if self.next_at is None else round(max(0, self.next_at - self.clock()), 1),
                "error": self.error}


class HID:
    """Tracks control+interrupt L2CAP channels per remote address. Accepts inbound, can dial out."""
    def __init__(self, on_change):
        self.conns = {}               # ADDR -> {"ctrl": sock, "intr": sock}
        self.lock = threading.RLock()
        self.on_change = on_change
        self.servers = []
        for psm, kind in ((PSM_CTRL, "ctrl"), (PSM_INTR, "intr")):
            s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("00:00:00:00:00:00", psm))
            s.listen(2)
            self.servers.append(s)
            threading.Thread(target=self._accept, args=(s, kind), daemon=True).start()

    def is_up(self, addr):
        with self.lock:
            return bool(addr) and "intr" in self.conns.get(addr.upper(), {})

    def _accept(self, srv, kind):
        while True:
            try: conn, addr = srv.accept()
            except OSError: return
            self._attach(addr[0].upper(), kind, conn)

    def _attach(self, addr, kind, conn):
        with self.lock:
            entry = self.conns.setdefault(addr, {})
            old = entry.get(kind)
            entry[kind] = conn
        if old:
            self._kill(old)
        threading.Thread(target=self._drain, args=(addr, kind, conn), daemon=True).start()
        if kind == "intr":
            self.on_change(addr, True)

    @staticmethod
    def _kill(s):
        try: s.shutdown(socket.SHUT_RDWR)
        except Exception: pass
        try: s.close()
        except Exception: pass

    def _drain(self, addr, kind, conn):
        while True:
            try: data = conn.recv(64)
            except OSError: break
            if not data: break
            if kind == "ctrl":
                t = data[0] >> 4
                try:
                    if t in (5, 7, 9): conn.send(b"\x00")
                    elif t == 6: conn.send(bytes([0xA0, 0x01]))
                    elif t in (4, 8): conn.send(b"\x03")
                except OSError: break
        with self.lock:
            current = self.conns.get(addr, {}).get(kind) is conn
        self._kill(conn)
        if current:
            self.drop(addr)

    def drop(self, addr):
        addr = addr.upper()
        with self.lock:
            entry = self.conns.pop(addr, None)
        if not entry: return False
        for s in entry.values(): self._kill(s)
        if "intr" in entry: self.on_change(addr, False)
        return True

    def connect(self, addr, timeout=10):
        """Dial out to a bonded host. Returns (ok, error)."""
        addr = addr.upper()
        if self.is_up(addr): return True, None
        socks, cur = {}, None
        try:
            for kind, psm in (("ctrl", PSM_CTRL), ("intr", PSM_INTR)):
                cur = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
                try: cur.setsockopt(274, 4, struct.pack("BB", 2, 0))   # BT_SECURITY = MEDIUM (encrypted)
                except OSError: pass
                cur.settimeout(timeout)
                cur.connect((addr, psm))
                cur.settimeout(None)
                socks[kind] = cur; cur = None
        except OSError as e:
            if cur: self._kill(cur)
            for s in socks.values(): self._kill(s)
            return False, str(e)
        self._attach(addr, "ctrl", socks["ctrl"])
        self._attach(addr, "intr", socks["intr"])
        return True, None

    def _send(self, addr, payload):
        with self.lock:
            s = self.conns.get(addr.upper(), {}).get("intr")
        if not s: return False
        try:
            s.send(payload)
            return True
        except OSError as e:
            say(f"Send failed: {e}"); return False

    def press(self, addr, mod, key):
        return (self._send(addr, bytes([0xA1, 0x01, mod, 0, key, 0, 0, 0, 0, 0])) and
                self._send(addr, bytes([0xA1, 0x01, 0, 0, 0, 0, 0, 0, 0, 0])))

    def mouse(self, addr, buttons, dx, dy, wheel):
        return self._send(addr, bytes([0xA1, 0x02, buttons & 0x07, dx & 0xFF, dy & 0xFF, wheel & 0xFF]))


class App:
    def __init__(self, bus):
        self.bus = bus
        st = load_state()
        self.active = st.get("active")
        self.solo = st.get("solo", True)       # one phone at a time (frees the other's on-screen keyboard)
        self.stop = threading.Event()
        self.last_try = 0.0
        self.last_hint = 0.0
        self.pending = b""                     # incomplete escape sequence carried to the next read
        self.stash = b""                       # bytes typed right after Ctrl+] in the same read (menu input)
        self.mouse_term = False                # terminal mouse capture (works over SSH)
        self.mscale = (CELL_W, CELL_H)
        self.mpos = None
        self.mbtn = 0
        self.pads = []                         # server trackpad/mouse sources (evdev)
        self.ssh = is_ssh()
        self.pads_auto = MOUSE and "--no-pad" not in sys.argv and (not self.ssh or "--pad" in sys.argv)
        self.pad_state = None
        self.web_url = ""                      # set by main() when the web console is up (text shown)
        self.web_link = ""                     # the bare link inside it
        self.see_proc = None                   # Cam child started by Key (on by default, remembered)
        self.want_see = bool(st.get("see", True))
        self.server_name = st.get("name") or DEFAULT_NAME
        self.see_last = None
        self.last_see_check = 0.0
        self.last_scan = 0.0
        self.term_map = st.get("term_map", True)      # terminal window <-> phone screen (absolute-feel)
        self.map_gain = float(st.get("map_gain", 1.0))
        self.gx, self.gy = (st.get("map_cal", {}).get(st.get("active")) or [1.0, 1.0])[:2]   # camera calibration, per device (see calibrate)
        self.vpos = None                       # where we believe the phone's pointer is (counts); None = unknown
        self.restart = False
        self.api_info = ""; self.web_port = 0; self.web_tok = ""; self.relay = None
        self.kbd = bool(st.get("keyboard", True))
        self.api_lock = threading.RLock()
        self.target_lock = threading.RLock()
        self.reconnect_enabled = True
        self.reconnect_plan = ReconnectPlan()
        self.hid = HID(self.on_change)
        threading.Thread(target=self.reconnect_loop, name="modkvm-reconnect", daemon=True).start()

    # ---------- helpers ----------
    def name_of(self, addr):
        try:
            for b in bonds(self.bus, timeout=2):
                if b["addr"] == addr: return b["name"]
        except Exception: pass
        return addr

    def status(self, b):
        if self.hid.is_up(b["addr"]): return "● connected"
        if b["link"]: return "◐ linked (no HID)"
        return "○ offline"

    def top_lines(self):
        out = [f"modkvm {VERSION}: Ctrl+] for menu, then press [key]"]
        web = self.web_header()
        if web:
            link, tail = web
            host = self.hosting_label()
            out.append(f"Web: {hyperlink_text(link, tail)}" + (f" | {host}" if host else ""))
        if self.api_info: out.append(self.api_info)
        return out

    def show(self):
        bl = bonds(self.bus)
        for line in self.top_lines(): say(line)
        if not bl:
            say("No bonds.")
            return bl
        say(f"\n  {'[1-9]':<6}{'Device':<26}{'Address':<19}Status")
        for i, b in enumerate(bl, 1):
            mark = "*" if b["addr"] == self.active else " "
            say(f"{mark} {i:<5}{b['name'][:24]:<26}{b['addr']:<19}{self.status(b)}")
        onoff = lambda v: "on" if v else "off"
        say(f"\n  cam [c] {SEE_DOT[self.see_now()]}" + (" | calibrate [h]" if MOUSE else ""))
        say(f"\n  keyboard [k] {onoff(self.kbd)} | name [n] {self.server_name} | solo [s] {onoff(self.solo)}")
        if MOUSE:
            say(f"  mouse [m] {(('map' if self.term_map else 'relative') + ' [a]') if self.mouse_term else 'off'}"
                f" | trackpad [t] {onoff(self.pads)}")
        else:
            say("  no mouse")
        say("  pair [p] | drop [d] | forget [f]")
        say("\n  restart [r] | quit [q]\n")
        return bl

    def banner(self):
        if not self.active:
            say("No target.")
            return
        if self.hid.is_up(self.active):
            say(f"▶ {self.name_of(self.active)} ●")
        else:
            say(f"○ {self.name_of(self.active)}")

    def hint(self, msg):
        now = time.time()
        if now - self.last_hint > 3:
            self.last_hint = now; say(msg)

    def read_line(self, fd, prompt, initial=""):
        """Tiny line editor for the menu: type, Backspace, Enter saves, Esc cancels (None)."""
        buf = bytearray(initial.encode())
        def draw(): raw_out("\r\x1b[2K" + prompt + buf.decode("utf-8", "ignore"))
        draw()
        while not self.stop.is_set():
            if self.stash: d, self.stash = self.stash, b""
            else:
                d = self.read(fd)
                if d is None: return None
            d = re.sub(rb"\x1b\[<[\d;]*[Mm]|\x1b\[\?[\d;]*\$y", b"", d)       # mouse noise
            for i, ch in enumerate(d):
                if ch in (10, 13):
                    self.stash = d[i + 1:]; say(); return buf.decode("utf-8", "ignore").strip()
                if ch == 27:
                    say(); return None
                if ch in (8, 127):
                    while buf and (buf[-1] & 0xC0) == 0x80: buf.pop()      # a whole UTF-8 character
                    if buf: buf.pop()
                elif ch >= 32 and len(buf) < 60:
                    buf.append(ch)
            draw()
        return None

    def set_server_name(self, name):
        name = " ".join(str(name).split())[:60]
        if not name: raise ValueError("empty name")
        set_prop(self.bus, "Alias", dbus.String(name))
        self.server_name = name; save_state(name=name)
        return name

    def rename_server(self, fd):
        name = self.read_line(fd, "Name: ", self.server_name)
        if not name: say("Cancelled."); return
        try: say(f"Renamed: {self.set_server_name(name)}")
        except Exception as e: say(f"error: {e}")

    def pick(self, arg):
        bl = bonds(self.bus)
        try:
            n = int(arg)
            if n >= 1: return bl[n - 1]
        except (ValueError, IndexError):
            pass
        say("?")
        return None

    def set_active(self, addr):
        with self.target_lock:
            self.reconnect_plan.cancel("selected")
            self.reconnect_enabled = True
            self.active = addr
            save_state(active=addr)
            self.gx, self.gy = (load_state().get("map_cal", {}).get(addr) or [1.0, 1.0])[:2]
            self.vpos = None

    def bt_disconnect(self, addr):
        try:
            dbus.Interface(self.bus.get_object("org.bluez", dev_path(addr)), "org.bluez.Device1").Disconnect()
        except Exception: pass

    # ---------- events ----------
    def on_change(self, addr, up):
        with self.target_lock:
            self.vpos = None                   # reconnect does not establish pointer calibration
            if self.stop.is_set(): return
            if up:
                if not self.active: self.set_active(addr)
                if addr == self.active: self.reconnect_plan.cancel("connected")
            elif addr == self.active and self.reconnect_enabled and not self.stop.is_set():
                self.reconnect_plan.arm(addr)
        say(f"{'●' if up else '○'} {self.name_of(addr)} {'connected' if up else 'disconnected'}")

    # ---------- actions ----------
    def use(self, b):
        with self.target_lock:
            self.set_active(b["addr"])
            if self.solo:
                for a in list(self.hid.conns):
                    if a != b["addr"]:
                        self.hid.drop(a); self.bt_disconnect(a)
            if self.hid.is_up(b["addr"]): return
            say(f"Connecting to {b['name']}...")
            ok, err = self.hid.connect(b["addr"])
            if not ok:
                say(f"Failed: {err}")
                self.reconnect_plan.arm(b["addr"])

    def drop(self, b):
        with self.target_lock:
            if b["addr"] == self.active:
                self.reconnect_enabled = False
                self.reconnect_plan.cancel("disconnected manually")
            was = self.hid.drop(b["addr"]); self.bt_disconnect(b["addr"])
        say(f"Disconnected {b['name']} (bond kept)." if was else f"{b['name']} wasn't connected.")

    def forget(self, b, fd):
        say(f"Delete bond for {b['name']}? Press y to confirm: ", end="")
        k = self.getkey(fd); say()
        if k != "y":
            say("Cancelled."); return
        with self.target_lock:
            if self.active == b["addr"]:
                self.reconnect_enabled = False
                self.reconnect_plan.cancel("bond removed")
            self.hid.drop(b["addr"])
            try:
                dbus.Interface(self.bus.get_object("org.bluez", ADAPTER), "org.bluez.Adapter1").RemoveDevice(b["path"])
                say("Bond removed.")
            except Exception as e:
                say(f"error: {e}")
            if self.active == b["addr"]:
                self.active = None; save_state(active=None)

    def pair(self):
        set_prop(self.bus, "DiscoverableTimeout", dbus.UInt32(120))
        set_prop(self.bus, "Discoverable", dbus.Boolean(True))
        say("Visible 120s.")

    def toggle_keyboard(self):
        self.kbd = not self.kbd; save_state(keyboard=self.kbd)
        say("Keyboard on" if self.kbd else "Keyboard off")

    def toggle_solo(self):
        self.solo = not self.solo; save_state(solo=self.solo)
        say("Solo on" if self.solo else "Solo off")

    # ---------- mouse ----------
    def set_term_mouse(self, on, quiet=False):
        if on == self.mouse_term:
            return
        self.mouse_term = on
        self.mpos = None
        if on:
            self.mscale = (CELL_W, CELL_H)
            _MOUSE_REPORTING["on"] = True
            raw_out(PROBE_1016 + "\x1b[?1003h\x1b[?1006h")     # ask about pixel mode, enable any-motion + SGR
            if not quiet:
                say("Mouse on")
        else:
            raw_out(MOUSE_OFF_SEQ); _MOUSE_REPORTING["on"] = False
            if self.mbtn:
                self.mbtn = 0
                if self.active and self.hid.is_up(self.active): self.send_mouse(0, 0, 0, 0)
            if not quiet: say("Mouse off")

    def toggle_pads(self):
        if self.pads:
            self.pads_auto = False
            self.stop_pads(); say("Trackpad off"); return
        self.pads_auto = True
        if not self.scan_pads(loud=True):
            say("No trackpad found")

    def scan_pads(self, loud=False):
        """Open any trackpad/mouse not yet captured. Returns True if something is captured."""
        self.pads = [p for p in self.pads if p.is_alive()]
        try:
            import evdev
        except ImportError:
            self.pad_note("trackpad: needs python3-evdev", loud)
            return bool(self.pads)
        have = {p.dev.path for p in self.pads}
        try:
            new = open_pointer_devices(evdev, self.pad_emit, skip=have)
        except Exception as e:
            self.pad_note(f"trackpad: {e}", loud); return bool(self.pads)
        self.pads += new
        if new:
            self.pad_note("Trackpad on", True)
        elif not self.pads:
            self.pad_note("trackpad: none found", loud)
        return bool(self.pads)

    def pad_note(self, msg, loud):
        """Print a trackpad status line when it changes (or when asked for with the hotkey)."""
        if loud or msg != self.pad_state: say(msg)
        self.pad_state = msg

    def stop_pads(self):
        for p in self.pads: p.halt.set()
        for p in self.pads: p.join(1)
        self.pads = []

    def pad_emit(self, dx, dy, wheel, buttons):
        if self.active and self.hid.is_up(self.active):
            self.send_mouse(dx, dy, wheel, buttons)

    def term_has_mouse(self, fd):
        """Ask the terminal if it reports the mouse (SGR mode). No answer or 'unsupported' = leave it off.
        Returns (supported, bytes the user typed meanwhile)."""
        raw_out(PROBE_1006)
        end, buf = time.time() + 0.4, b""
        pat = rb"\x1b\[\?1006;(\d)\$y"
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                try: buf += os.read(fd, 4096)
                except OSError: break
                m = re.search(pat, buf)
                if m: return m.group(1) in (b"1", b"2", b"3"), re.sub(pat, b"", buf)    # 0 unknown, 4 permanently off
        # no answer at all (e.g. macOS Terminal): trust an xterm-family TERM; a terminal without mouse ignores the request
        return bool(re.match(r"(xterm|screen|tmux|rxvt|alacritty|kitty|foot|wezterm|ghostty|vte|konsole)", os.environ.get("TERM", ""))), buf

    def term_size(self):
        try:
            rows, cols, xp, yp = struct.unpack("HHHH", fcntl.ioctl(sys.stdin.fileno(), termios.TIOCGWINSZ, b"\0" * 8))
            return max(cols, 1), max(rows, 1), xp, yp
        except Exception:
            return 80, 24, 0, 0

    def home(self):
        """Slam the phone pointer into its top-left corner so we know where it is."""
        if not (self.active and self.hid.is_up(self.active)): return False
        self.send_mouse(int(-self.map_w() * 1.3), int(-self.map_h() * 1.3), 0, self.mbtn)
        self.vpos = [0.0, 0.0]
        return True

    def map_w(self): return MAP_W * self.map_gain * self.gx
    def map_h(self): return MAP_H * self.map_gain * self.gy

    def cal_snap(self):
        """One picture of the phone from the camera linked to the active device. Returns (jpeg bytes, '') or (None, why)."""
        cam = load_cfg().get("links", {}).get(self.active, "")
        if not cam: return None, "link a camera to this device in the web console"
        try:
            hdr = {"Authorization": "Bearer " + sb_token()} if sb_token() else {}
            cams = http_json(SB_URL + "/cams", hdr)
            cams = cams.get("cams", cams) if isinstance(cams, dict) else cams
            opts = next((c.get("opts", {}) for c in cams if isinstance(c, dict) and c.get("name") == cam), {})
            if not (opts.get("warp") or opts.get("crop")):
                return None, "mark the phone's screen in the web console (Calibrate)"
            req = urllib.request.Request(f"{SB_URL}/snap/{urllib.parse.quote(cam)}.jpg", headers=hdr)
            with urllib.request.urlopen(req, timeout=20) as r: return r.read(), ""
        except Exception as e:
            return None, f"camera unavailable ({e})"

    def cal_cam_opts(self):
        """(camera name, auth headers, its saved view options) for the camera linked to the active device."""
        cam = load_cfg().get("links", {}).get(self.active, "")
        hdr = {"Authorization": "Bearer " + sb_token()} if sb_token() else {}
        try:
            cams = http_json(SB_URL + "/cams", hdr)
            cams = cams.get("cams", cams) if isinstance(cams, dict) else cams
            return cam, hdr, next((c.get("opts", {}) for c in cams if isinstance(c, dict) and c.get("name") == cam), {})
        except Exception:
            return cam, hdr, {}

    def cal_set_tilt(self, deg):
        cam, hdr, _ = self.cal_cam_opts()
        req = urllib.request.Request(f"{SB_URL}/save/{urllib.parse.quote(cam)}", method="POST",
                                     data=json.dumps({"tilt": round(deg, 2)}).encode(),
                                     headers=dict(hdr, **{"Content-Type": "application/json"}))
        urllib.request.urlopen(req, timeout=10).read()

    def straighten(self, snap, tilt_get, tilt_set, settle, tries=2):
        """Slide the pointer along a horizontal line near the top. Any slope in the picture is the camera being crooked:
        turn the view by that much. Returns degrees applied (0 if already straight), or None if the pointer wasn't seen."""
        total = 0.0
        for _ in range(tries):
            self.vpos = None
            if not self.home(): return None
            time.sleep(settle); ref = snap()
            size = frame_size(ref) if ref else None
            pts = []
            for fx in (0.35, 0.85):                       # a line inside the screen, clear of the clipped edge
                self.goto(fx, 0.2); time.sleep(settle)
                now = snap(); hit = locate_pointer(ref, now) if now else None
                if not hit or not size: return None
                pts.append((hit[0] * size[0], hit[1] * size[1]))
            ang = math.degrees(math.atan2(pts[1][1] - pts[0][1], pts[1][0] - pts[0][0]))
            if abs(ang) < 0.6 or abs(ang) > 30: break
            tilt_set(max(-45.0, min(45.0, tilt_get() + ang))); total += ang
            time.sleep(settle)
        return total

    def fit_scale(self, snap, settle, rounds, aim):
        """Home, aim at (aim, aim), look where the pointer really landed, correct the scale; the last pass only checks.
        Returns the final miss (fraction of the screen), or None if the pointer wasn't seen."""
        err = None
        for i in range(rounds + 1):
            self.vpos = None
            if not self.home(): return None
            time.sleep(settle); ref = snap()
            self.goto(aim, aim); time.sleep(settle); now = snap()
            hit = locate_pointer(ref, now) if ref and now else None
            if not hit: return None
            ax, ay = hit
            err = max(abs(ax - aim), abs(ay - aim))
            if err < 0.03 or i == rounds: break
            self.gx *= min(3.0, max(0.33, aim / max(ax, 0.05)))
            self.gy *= min(3.0, max(0.33, aim / max(ay, 0.05)))
        return err

    def calibrate(self, snap=None, settle=0.6, rounds=4, aim=0.8, tilt_get=None, tilt_set=None):
        """Camera-assisted: fix the pointer scale, straighten a crooked view, fix the scale again. One line back."""
        if not (self.active and self.hid.is_up(self.active)): return "No connected phone."
        if snap is None:
            pic, why = self.cal_snap()
            if pic is None:
                self.home(); return f"Pointer re-homed. For auto-calibration: {why}."
            snap = lambda: self.cal_snap()[0]
            tilt_get = lambda: float(self.cal_cam_opts()[2].get("tilt") or 0)
            tilt_set = self.cal_set_tilt
        gx0, gy0 = self.gx, self.gy
        unseen = "Couldn't see the pointer (is AssistiveTouch on, is the view in frame?). Unchanged."
        err = self.fit_scale(snap, settle, rounds, aim)
        if err is None:
            self.gx, self.gy = gx0, gy0; return unseen
        turned = 0.0
        if tilt_get and tilt_set:
            try: turned = self.straighten(snap, tilt_get, tilt_set, settle)
            except Exception: turned = None
            if turned is None:
                self.gx, self.gy = gx0, gy0; return unseen
            if abs(turned) >= 0.6:
                err = self.fit_scale(snap, settle, rounds, aim)
                if err is None:
                    self.gx, self.gy = gx0, gy0; return unseen
        self.vpos = None; self.home()
        cal = dict(load_state().get("map_cal", {})); cal[self.active] = [round(self.gx, 4), round(self.gy, 4)]
        save_state(map_cal=cal)
        return (f"Calibrated x{self.gx:.2f} y{self.gy:.2f} (within {max(err, 0.01) * 100:.0f}%)"
                + (f", straightened {turned:+.1f}°" if abs(turned) >= 0.6 else ""))

    def goto(self, fx, fy, buttons=None):
        """Move the phone pointer to a fraction (0..1, 0..1) of its screen. Edges overshoot to self-correct drift."""
        b = self.mbtn if buttons is None else buttons
        if self.vpos is None and not self.home(): return False
        W, H = self.map_w(), self.map_h()
        tx = -0.25 * W if fx <= 0.01 else 1.25 * W if fx >= 0.99 else fx * W
        ty = -0.25 * H if fy <= 0.01 else 1.25 * H if fy >= 0.99 else fy * H
        dx, dy = int(round(tx - self.vpos[0])), int(round(ty - self.vpos[1]))
        self.vpos = [min(max(tx, 0.0), W), min(max(ty, 0.0), H)]
        if dx or dy or b != self.mbtn:
            return self.send_mouse(dx, dy, 0, b)
        return True

    def term_fraction(self, x, y):
        cols, rows, xp, yp = self.term_size()
        top = 0
        if self.mscale == (1, 1) and xp > 0 and yp > 0:
            rh = yp / max(rows, 1) * top
            return min(max(x / xp, 0.0), 1.0), min(max((y - rh) / max(yp - rh, 1), 0.0), 1.0)
        return (min(max((x - 1) / max(cols - 1, 1), 0.0), 1.0),
                min(max((y - 1 - top) / max(rows - 1 - top, 1), 0.0), 1.0))

    def send_mouse(self, dx, dy, wheel, buttons):
        a = self.active
        if not a: return False
        while True:
            sx, sy, sw = (max(-127, min(127, v)) for v in (dx, dy, wheel))
            if not self.hid.mouse(a, buttons, sx, sy, sw): return False
            dx -= sx; dy -= sy; wheel -= sw
            if not (dx or dy or wheel): return True

    # ---------- misc ----------
    def ask_target(self, verb, fd):
        say(f"{verb} #? ", end="")
        k = self.getkey(fd); say()
        if k in ("\r", "\n"):
            for b in bonds(self.bus):
                if b["addr"] == self.active: return b
            say("No active device"); return None
        if k and k.isdigit():
            return self.pick(k)
        say("Cancelled."); return None

    # ---------- see (camera) + web indicators ----------
    def hosting_text(self):
        if not self.relay or self.relay.stop_ev.is_set(): return "off"
        return ("● " if self.relay.ok else "○ ") + re.sub(r"^https?://", "", self.relay.url).rstrip("/")

    def hosting_label(self):
        ht = self.hosting_text()
        return "hosted locally | [u] get paid hosting (coming soon)" if ht == "off" else f"hosted {ht} | [u] change"

    def start_hosting(self, url, token):
        """Dial out to a relay (no inbound port): remembered in modkvm.json. Empty URL turns it off."""
        if self.relay: self.relay.stop(); self.relay = None
        cfg = load_cfg()
        if url and token and self.web_port:
            self.relay = relay_agent(url, token, self.web_port, self.web_tok)
            cfg["relay"], cfg["relay_token"] = url, token
        else:
            cfg.pop("relay", None); cfg.pop("relay_token", None)
        save_cfg(cfg)
        return self.hosting_text()

    def hosting(self, fd):
        if not self.web_port:
            say("Hosting needs the web console"); return
        cfg = load_cfg()
        url = self.read_line(fd, "Relay URL (empty = off): ", cfg.get("relay", ""))
        if url is None: say("Cancelled."); return
        if not url:
            self.start_hosting("", ""); say("Hosting off"); return
        tok = self.read_line(fd, "Agent token: ", cfg.get("relay_token", ""))
        if not tok: say("Cancelled."); return
        say(f"Hosting {self.start_hosting(url.rstrip('/'), tok)}")

    def web_header(self):
        """(link, trailing text) for the pinned web row, or None when there is no web console."""
        if not self.web_url: return None
        if self.web_link and self.web_url.startswith(self.web_link):
            return self.web_link, self.web_url[len(self.web_link):]
        return "", self.web_url

    def web_text(self):
        if self.web_link and self.web_url.startswith(self.web_link):
            return hyperlink(self.web_link) + self.web_url[len(self.web_link):]
        return self.web_url

    def show_web(self):
        if not self.web_url: say("web off"); return
        say(self.web_text())
        if self.web_link and shutil.which("qrencode"):
            try: say(subprocess.run(["qrencode", "-t", "ANSIUTF8", "-m", "1", self.web_link], capture_output=True, text=True, timeout=3).stdout.rstrip())
            except Exception: pass

    def see_now(self):
        return see_state()

    def check_see(self):
        """Inline indicator: prints a line only when Cam changes state (live / idle / stopped)."""
        st = self.see_now()
        if self.see_last is not None and st != self.see_last:
            say(f"cam {SEE_DOT[st]}")
        self.see_last = st

    def see_running(self):
        return bool(self.see_proc and self.see_proc.poll() is None)

    def start_see(self, quiet=False):
        if self.see_running() or port_open(SB_URL):
            return True                                                # already up (here or in another terminal)
        fixed = cam_autofix()
        if fixed: say("Cam: fixed " + ", ".join(fixed))
        cmd = [sys.executable, os.path.abspath(__file__), "see", "serve"]
        if os.geteuid() == 0 and os.environ.get("SUDO_USER"):
            cmd = ["sudo", "-u", os.environ["SUDO_USER"]] + cmd       # the camera needs the video group, not root
        self.see_proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, start_new_session=True)
        quiet or say("Cam starting")
        return True

    def stop_see(self, quiet=False):
        if self.see_running():
            self.see_proc.terminate()
            try: self.see_proc.wait(3)
            except Exception: pass
        self.see_proc = None
        quiet or say("Cam off")

    def set_see(self, on, quiet=True):
        """Remembered across runs (default on). Returns the new wanted state."""
        self.want_see = bool(on); save_state(see=self.want_see)
        if self.want_see: self.start_see(quiet)
        else:
            if port_open(SB_URL) and not self.see_running():
                quiet or say("Cam is running in another terminal")
            self.stop_see(quiet)
        return self.want_see

    def toggle_see(self):
        self.set_see(not (self.want_see and (self.see_running() or port_open(SB_URL))), quiet=False)

    def shutdown(self, bye="Disconnected from all devices. Bye."):
        with self.target_lock:
            self.reconnect_enabled = False
            self.reconnect_plan.cancel("stopped")
        self.stop_see(quiet=True)
        self.set_term_mouse(False, quiet=True)
        self.stop_pads()
        for addr in list(self.hid.conns):
            self.hid.drop(addr); self.bt_disconnect(addr)
        say(bye)
        self.stop.set()

    def ensure_target(self):
        with self.target_lock:
            return self._ensure_target()

    def _ensure_target(self):
        if self.stop.is_set() or not self.reconnect_enabled: return False
        if not self.active:
            self.hint("No target. Ctrl+] then a number picks one.")
            return False
        if self.hid.is_up(self.active):
            return True
        try: paired = {b["addr"] for b in bonds(self.bus, timeout=2)}
        except Exception:
            self.reconnect_plan.arm(self.active); return False
        if self.active not in paired: return False
        now = time.monotonic()
        if now - self.last_try < 10:
            self.hint(f"{self.name_of(self.active)} is offline. Ctrl+] for the menu.")
            return False
        self.last_try = now
        say(f"Reconnecting to {self.name_of(self.active)}...")
        ok, err = self.hid.connect(self.active, timeout=5)
        if not ok:
            say(f"Not reachable ({err}).")
            self.reconnect_plan.arm(self.active)
        return ok

    def auto_reconnect(self):
        with self.target_lock:
            if self.stop.is_set() or not self.reconnect_enabled or self.hid.is_up(self.active): return
            try: paired = {b["addr"] for b in bonds(self.bus, timeout=2)}
            except Exception:
                self.reconnect_plan.arm(self.active); return
            if self.active not in paired: return
            say(f"Auto-reconnecting to {self.name_of(self.active)}...")
            ok, err = self.hid.connect(self.active, timeout=8)
            if not ok:
                say(f"  not reachable ({err})")
                self.reconnect_plan.arm(self.active)

    def reconnect_loop(self):
        while not self.stop.wait(.25):
            try: self.reconnect_once()
            except Exception as e:
                logging.warning("reconnect check failed: %s", type(e).__name__)
                with self.target_lock:
                    if self.reconnect_plan.target: self.reconnect_plan.cancel("check failed; use device to retry")

    def reconnect_once(self):
        with self.target_lock:
            plan = self.reconnect_plan
            if self.stop.is_set() or not self.reconnect_enabled or plan.target != self.active:
                if plan.target: plan.cancel("paused")
                return
            if not plan.due(): return
            # Only the selected, still-paired address may be dialed. No discovery,
            # Pair(), Use(), target selection, pointer home or input is performed.
            try: paired = {b["addr"] for b in bonds(self.bus, timeout=2)}
            except Exception:
                plan.begin(); plan.failed("Bluetooth metadata unavailable"); return
            if self.active not in paired:
                plan.cancel("not paired"); return
            if not plan.due(): return           # metadata query may have crossed the deadline
            if self.hid.is_up(self.active):
                plan.cancel("connected"); return
            generation, target = plan.generation, plan.target
            timeout = min(2.0, max(.01, (plan.deadline - plan.clock()) / 2))
            plan.begin()
            try: ok, err = self.hid.connect(target, timeout=timeout)
            except Exception as e: ok, err = False, type(e).__name__
            # A successful connect invokes on_change; it may already cancel this plan.
            if self.stop.is_set(): plan.cancel("stopped"); return
            if generation != plan.generation: return
            if ok: plan.cancel("connected")
            else: plan.failed(err)

    # ---------- agent API ----------
    def find_bond(self, ref):
        bl = bonds(self.bus)
        if isinstance(ref, int) or str(ref).isdigit():
            n = int(ref)
            if 1 <= n <= len(bl): return bl[n - 1]
        for b in bl:
            if str(ref).upper() == b["addr"] or str(ref).lower() in b["name"].lower(): return b
        raise ApiError(f"no such device '{ref}' (see GET /status)", 404)

    def snapshot(self):
        devs = []
        for i, b in enumerate(bonds(self.bus), 1):
            devs.append({"n": i, "name": b["name"], "addr": b["addr"], "active": b["addr"] == self.active,
                         "connected": self.hid.is_up(b["addr"])})
        return {"active": self.active, "solo": self.solo, "name": self.server_name, "devices": devs, "mouse_enabled": MOUSE,
                "reconnect": dict(self.reconnect_plan.snapshot(), enabled=self.reconnect_enabled),
                "terminal_mouse": self.mouse_term, "terminal_map": self.term_map, "trackpad": bool(self.pads),
                "pointer": None if self.vpos is None else
                [round(self.vpos[0] / self.map_w(), 3), round(self.vpos[1] / self.map_h(), 3)]}

    def need_target(self):
        if not self.ensure_target():
            raise ApiError("no connected target phone (POST /use first)", 409)

    def need_mouse(self):
        if not MOUSE: raise ApiError("started with --no-mouse", 409)
        self.need_target()

    @staticmethod
    def frac(body, k):
        v = float(body[k])
        if not 0.0 <= v <= 1.0: raise ApiError(f"'{k}' must be 0..1 (fraction of the phone screen)")
        return v

    @staticmethod
    def button_bit(body):
        try: return {"left": 1, "right": 2, "middle": 4}[str(body.get("button", "left")).lower()]
        except KeyError: raise ApiError("button must be left, right or middle")

    def api(self, method, path, body):
        if method == "GET" and path in ("/", "/status"):
            return self.snapshot()
        if method != "POST": raise ApiError("not found", 404)
        if path == "/type":
            text = str(body.get("text", ""))
            self.need_target()
            keys = [e for e in parse_input("".join(c for c in text if c in "\n\t" or 32 <= ord(c) < 127).encode())[0] if e[0] == "key"]
            skipped = sum(1 for c in text if not (c in "\n\t" or 32 <= ord(c) < 127))
            if body.get("submit"): keys.append(("key", 0, K_ENTER))
            for _, m, k in keys:
                if not self.hid.press(self.active, m, k): raise ApiError("send failed (phone went away?)", 409)
                time.sleep(float(body.get("delay", 0.008)))
            return {"typed": len(keys), "skipped_non_ascii": skipped}
        if path == "/key":
            combos = body.get("keys") or [body["key"]]
            parsed = []
            for c in combos:
                try: parsed.append(parse_combo(c))
                except ValueError as e: raise ApiError(str(e))
            self.need_target()
            for m, k in parsed:
                if not self.hid.press(self.active, m, k): raise ApiError("send failed (phone went away?)", 409)
                time.sleep(0.04)
            return {"sent": combos}
        if path == "/use":
            b = self.find_bond(body["device"])
            self.use(b)
            if not self.hid.is_up(b["addr"]): raise ApiError(f"could not connect to {b['name']}", 409)
            return self.snapshot()
        if path == "/drop":
            self.drop(self.find_bond(body.get("device", self.active)))
            return self.snapshot()
        if path == "/rename":
            try: return {"server": self.set_server_name(body.get("server", ""))}
            except ValueError as e: raise ApiError(str(e))
        if path == "/solo":
            if bool(body["on"]) != self.solo: self.toggle_solo()
            return self.snapshot()
        if path == "/mouse":
            if "terminal" in body: self.set_term_mouse(bool(body["terminal"]), quiet=True)
            if "map" in body: self.term_map = bool(body["map"]); save_state(term_map=self.term_map)
            if "trackpad" in body:
                if bool(body["trackpad"]) != bool(self.pads): self.toggle_pads()
            return self.snapshot()
        # ---- pointer actions
        self.need_mouse()
        if path == "/home":
            self.home(); return {"pointer": [0, 0]}
        if path == "/move":
            dx, dy = int(body.get("dx", 0)), int(body.get("dy", 0))
            self.send_mouse(dx, dy, 0, self.mbtn)
            if self.vpos is not None:
                self.vpos = [min(max(self.vpos[0] + dx, 0.0), self.map_w()),
                             min(max(self.vpos[1] + dy, 0.0), self.map_h())]
            return {}
        if path == "/goto":
            self.goto(self.frac(body, "x"), self.frac(body, "y")); return {"pointer": [body["x"], body["y"]]}
        if path == "/click":
            bit = self.button_bit(body)
            if "x" in body and "y" in body: self.goto(self.frac(body, "x"), self.frac(body, "y"), 0)
            for _ in range(2 if body.get("double") else 1):
                self.send_mouse(0, 0, 0, bit); time.sleep(float(body.get("hold", 0.05)))
                self.send_mouse(0, 0, 0, 0); time.sleep(0.06)
            return {"clicked": body.get("button", "left"), "double": bool(body.get("double"))}
        if path == "/scroll":
            n = int(body.get("amount", 3)); d = str(body.get("dir", "down")).lower()
            if d not in ("up", "down"): raise ApiError("dir must be up or down")
            if "x" in body and "y" in body: self.goto(self.frac(body, "x"), self.frac(body, "y"), 0)
            self.send_mouse(0, 0, n if d == "up" else -n, 0)
            return {"scrolled": d, "amount": n}
        if path == "/drag":
            bit = self.button_bit(body)
            x1, y1, x2, y2 = (self.frac(body, k) for k in ("x1", "y1", "x2", "y2"))
            steps = max(2, int(body.get("steps", 12))); dur = float(body.get("duration", 0.4))
            self.goto(x1, y1, 0); time.sleep(0.05)
            self.send_mouse(0, 0, 0, bit); time.sleep(0.08)
            for i in range(1, steps + 1):
                self.goto(x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps, bit)
                time.sleep(dur / steps)
            self.send_mouse(0, 0, 0, 0)
            return {"dragged": [x1, y1, x2, y2]}
        raise ApiError("not found", 404)

    # ---------- input ----------
    def read(self, fd, timeout=0.2):
        r, _, _ = select.select([fd], [], [], timeout)
        if not r: return b""
        d = os.read(fd, 4096)
        return d if d else None

    def getkey(self, fd):
        while not self.stop.is_set():
            if self.stash:
                d, self.stash = self.stash, b""
            else:
                d = self.read(fd)
                if d is None: return None
            d = re.sub(rb"\x1b\[<[\d;]*[Mm]|\x1b\[\?[\d;]*\$y", b"", d)   # ignore mouse noise while a menu waits
            if d:
                self.stash = d[1:]
                return chr(d[0]) if d[0] < 0x80 else "?"
        return None

    def menu(self, fd):
        k = self.getkey(fd)
        if k is None: return
        if k in ("\r", "\n", " ", "?"):
            self.show(); return
        if k.isdigit() and k != "0":
            b = self.pick(k)
            if b: self.use(b)
        elif k == "p": self.pair()
        elif k == "s": self.toggle_solo()
        elif k == "m":
            if MOUSE: self.set_term_mouse(not self.mouse_term)
            else: say("keyboard-only")
        elif k == "t":
            if MOUSE: self.toggle_pads()
            else: say("keyboard-only")
        elif k == "a":
            self.term_map = not self.term_map; save_state(term_map=self.term_map); self.mpos = None
            say("Mouse: map" if self.term_map else "Mouse: relative")
        elif k == "h":
            say("Calibrating..."); say(self.calibrate())
        elif k in "+=-_":
            self.map_gain = min(3.0, max(0.3, self.map_gain * (1.1 if k in "+=" else 1 / 1.1)))
            save_state(map_gain=round(self.map_gain, 3)); self.vpos = None
            say(f"Map x{self.map_gain:.2f}")
        elif k == "c": self.toggle_see()
        elif k == "n": self.rename_server(fd)
        elif k == "k": self.toggle_keyboard()
        elif k == "u": self.hosting(fd)
        elif k == "d":
            b = self.ask_target("Drop", fd)
            if b: self.drop(b)
        elif k == "f":
            b = self.ask_target("Forget", fd)
            if b: self.forget(b, fd)
        elif k == "r":
            self.restart = True; self.shutdown(bye="Restarting (Bluetooth too)."); return
        elif k == "q":
            self.shutdown(); return
        self.banner()

    def handle(self, fd, data):
        # Split at the menu hotkey so a fast "Ctrl+] 2" (same read) still reaches the menu.
        head, sep, tail = (self.pending + data).partition(bytes([MENU_KEY]))
        events, self.pending = parse_input(head)
        if sep: self.pending = b""
        skipped = 0
        mdx = mdy = wheel = 0

        def flush():
            nonlocal mdx, mdy, wheel
            if mdx or mdy or wheel:
                self.send_mouse(mdx, mdy, wheel, self.mbtn)
            mdx = mdy = wheel = 0

        for ev in events:
            kind = ev[0]
            if kind == "skip":
                skipped += 1; continue
            if kind == "mode":                                   # terminal says it can report pixels
                if ev[1] == 1016 and ev[2] in (1, 2, 3) and self.mouse_term and self.term_size()[2] > 0:
                    self.mscale = (1, 1); raw_out("\x1b[?1016h")
                continue
            if kind != "mouse" and not self.kbd:                 # keyboard off: keys stay local, mouse still works
                continue
            if not self.ensure_target():
                if not sep: termios.tcflush(fd, termios.TCIFLUSH)
                self.pending = b""
                break
            if kind == "mouse":
                _, cb, x, y, pressed = ev
                if cb & 64:                                      # wheel: 64 = up, 65 = down
                    wheel += 1 if (cb & 3) == 0 else -1
                    continue
                bit = {0: 1, 1: 4, 2: 2}.get(cb & 3)             # left, middle, right -> HID bits
                newb = self.mbtn
                if bit and not (cb & 32):
                    newb = (self.mbtn | bit) if pressed else (self.mbtn & ~bit)
                if self.term_map:                                # window position == phone screen position
                    flush()
                    fx, fy = self.term_fraction(x, y)
                    self.goto(fx, fy, self.mbtn)
                    if newb != self.mbtn:
                        self.mbtn = newb
                        self.send_mouse(0, 0, 0, self.mbtn)
                    continue
                if self.mpos is not None:
                    mdx += (x - self.mpos[0]) * self.mscale[0]
                    mdy += (y - self.mpos[1]) * self.mscale[1]
                self.mpos = (x, y)
                if newb != self.mbtn:
                    flush()
                    self.mbtn = newb
                    self.send_mouse(0, 0, 0, self.mbtn)
                continue
            flush()                                              # a key: send pending motion first
            if not self.hid.press(self.active, ev[1], ev[2]):
                break
            time.sleep(0.004)
        flush()
        if skipped:
            say(f"(skipped {skipped} non-ASCII character(s))")
        if sep:                                                  # the menu is always reachable
            self.stash = tail
            self.menu(fd)
            self.stash = b""
            termios.tcflush(fd, termios.TCIFLUSH)

    def console(self):
        fd = sys.stdin.fileno()
        time.sleep(0.5)
        cols, rows = self.term_size()[:2]
        bl = self.show()
        if "--qr" in sys.argv: self.show_web()
        if bl and self.active not in [b["addr"] for b in bl]:
            self.set_active(bl[0]["addr"])
        tty_raw(fd)
        try:
            if self.active: self.auto_reconnect()
            termios.tcflush(fd, termios.TCIFLUSH)
            self.banner()
            if self.want_see: self.start_see(quiet=True)
            if MOUSE:
                if "--no-term-mouse" not in sys.argv:
                    has, typed = self.term_has_mouse(fd)
                    if has:
                        self.set_term_mouse(True, quiet=True)
                        say("Mouse on")
                    if typed: self.handle(fd, typed)                  # keys typed while we were asking
                if self.pads_auto: self.scan_pads()
            while not self.stop.is_set():
                data = self.read(fd)
                if data is None: break
                if data: self.handle(fd, data)
                if self.pads_auto and time.time() - self.last_scan > PAD_RESCAN:
                    self.last_scan = time.time(); self.scan_pads()
                if time.time() - self.last_see_check > 2:
                    self.last_see_check = time.time(); self.check_see()

        except Exception as e:
            say(f"console error: {e}")
        finally:
            self.set_term_mouse(False, quiet=True)
            self.stop_pads()
            tty_restore()
            self.stop.set()

    def headless(self):
        time.sleep(0.5)
        if self.want_see: self.start_see(quiet=True)
        if self.active: self.auto_reconnect()

# ---------------------------------------------------------------- local HTTP API (for agents / MCP)
def load_token():
    """One token, created on first run, readable only by the user who started sudo."""
    for p in (TOKEN_FILE, TOKEN_FILE_LEGACY):              # new name first, legacy fallback
        try:
            return open(p).read().strip()
        except OSError:
            pass
    tok = secrets.token_urlsafe(24)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f: f.write(tok)
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if uid:
        try: os.chown(TOKEN_FILE, int(uid), int(gid or uid))
        except OSError: pass
    return tok


class ApiError(Exception):
    def __init__(self, msg, code=400): super().__init__(msg); self.code = code


def make_api(app, token, host, port):
    class H(QuietHTTPHandler):
        def log_message(self, *a): pass

        def _out(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)

        def _auth(self):
            given = self.headers.get("Authorization", "").replace("Bearer ", "") or self.headers.get("X-Pb-Token", "")
            return hmac.compare_digest(given, token)

        def _run(self, method):
            if not self._auth(): return self._out(401, {"ok": False, "error": "token required (Authorization: Bearer <token>)"})
            path = self.path.split("?")[0].rstrip("/") or "/"
            try:
                body = {}
                if method == "POST":
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > 100_000: raise ApiError("body too large")
                    body = json.loads(self.rfile.read(n) or b"{}")
                    if not isinstance(body, dict): raise ApiError("JSON object expected")
                with app.api_lock:
                    res = app.api(method, path, body)
                self._out(200, dict(res, ok=True))
            except CLIENT_DISCONNECTS:
                return                                  # do not try to send a 500 to a departed client
            except ApiError as e:
                self._out(e.code, {"ok": False, "error": str(e)})
            except (ValueError, KeyError, TypeError) as e:
                self._out(400, {"ok": False, "error": f"bad request: {e}"})
            except Exception as e:
                self._out(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

        def do_GET(self): self._run("GET")
        def do_POST(self): self._run("POST")

    srv = ThreadingHTTPServer((host, port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def locate_pointer(ref, img, min_peak=9):
    """Where did the pointer appear between two pictures of the phone screen? (x, y) as fractions, or None.
    `ref` has it parked in the top-left corner, so of the two changed spots (gone / arrived) the one farther from
    that corner is the new position. A change in overall brightness is ignored."""
    try:
        from PIL import Image, ImageFilter
        import numpy as np, io
        a = Image.open(io.BytesIO(ref)).convert("L")
        size = (200, max(1, round(200 * a.height / a.width)))
        def prep(im):
            return np.asarray(im.convert("L").resize(size).filter(ImageFilter.GaussianBlur(2)), dtype=float)
        pa, pb = prep(a), prep(Image.open(io.BytesIO(img)))
        d = pb - pa
        d = np.abs(d - np.median(d))
        h, w = d.shape
        yy, xx = np.mgrid[0:h, 0:w]
        def blob(dd):
            peak = dd.max()
            if peak < min_peak: return None, 0.0, dd
            py, px = np.unravel_index(int(dd.argmax()), dd.shape)
            near = (xx - px) ** 2 + (yy - py) ** 2 < (0.12 * w) ** 2
            sel = near & (dd >= peak * 0.5)
            ys, xs = np.nonzero(sel)
            cx, cy = (xs.mean() + 0.5) / w, (ys.mean() + 0.5) / h
            return (float(cx), float(cy)), float(peak), np.where((xx - px) ** 2 + (yy - py) ** 2 < (0.16 * w) ** 2, 0, dd)
        b1, p1, rest = blob(d)
        if b1 is None: return None
        found = [b1]
        b2, p2, _ = blob(rest)
        if b2 is not None and p2 >= 0.35 * p1: found.append(b2)
        best = max(found, key=lambda t: t[0] ** 2 + t[1] ** 2)
        return best if best[0] ** 2 + best[1] ** 2 > 0.18 ** 2 else None
    except Exception:
        return None


def frame_size(jpeg):
    try:
        from PIL import Image
        import io
        return Image.open(io.BytesIO(jpeg)).size
    except Exception:
        return None


DEFAULT_NAME = "BTE Keyboard"


def server_name_for(saved, host=None):
    """The Bluetooth name phones see: whatever you set, else BTE Keyboard. A saved name is never changed."""
    return saved or DEFAULT_NAME


def reap_stale(wait=3.0):
    """Stop any other running kb-mode Key (a hung one keeps the Bluetooth HID profile registered).
    Never touches us, our parents, or a see/mcp/web-mode process of this same file.

    In headless (systemd) mode a terminal Key may already hold the Bluetooth profile:
    instead of killing it (which would just restart the service in a loop), wait for
    it to exit, then take over. This keeps the always-on service from fighting the
    user's terminal sessions."""
    if not sys.stdin.isatty():
        mine = set()
        pid = os.getpid()
        while pid > 1:
            mine.add(pid)
            try:
                with open(f"/proc/{pid}/stat") as f: pid = int(f.read().rsplit(")", 1)[1].split()[1])
            except (OSError, ValueError, IndexError): break
        while True:
            others = []
            try:
                out = subprocess.run(["pgrep", "-f", r"([m]k|[p]ortabotty)\.py"], capture_output=True, text=True).stdout.split()
                for p in out:
                    p = int(p)
                    if p in mine: continue
                    try:
                        with open(f"/proc/{p}/cmdline", "rb") as f: a = f.read().split(b"\0")
                    except OSError: continue
                    if len(a) > 1 and os.path.basename(a[0]).startswith(b"python") and os.path.basename(a[1]) in (b"mk.py", b"portabotty.py"):
                        mode = a[2] if len(a) > 2 else b""
                        if mode not in (b"see", b"cam", b"c", b"mcp", b"web", b"-h", b"--help", b"help", b"install-service"):
                            others.append(p)
            except (ValueError, OSError): pass
            if not others: break
            try: print(f"mk: waiting for active terminal Key to exit (mine={sorted(mine)}, others={others})...", flush=True)
            except Exception: pass
            time.sleep(2)
        return 0
    mine = set()
    pid = os.getpid()
    while pid > 1:                                   # us + sudo/shell parents
        mine.add(pid)
        try:
            with open(f"/proc/{pid}/stat") as f: pid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError): break
    def is_key(p):
        try:
            with open(f"/proc/{p}/cmdline", "rb") as f: argv = f.read().split(b"\0")
        except OSError: return False
        if not (len(argv) > 1 and os.path.basename(argv[0]).startswith(b"python")
                and os.path.basename(argv[1]) in (b"mk.py", b"portabotty.py")):
            return False
        script = os.fsdecode(argv[1])
        if not os.path.isabs(script):
            try: script = os.path.join(os.readlink(f"/proc/{p}/cwd"), script)
            except OSError: return False
        if os.path.realpath(script) != os.path.realpath(__file__):
            return False
        mode = argv[2] if len(argv) > 2 else b""
        return mode not in (b"see", b"cam", b"c", b"mcp", b"web", b"-h", b"--help", b"help", b"install-service")
    def others():
        r = subprocess.run(["pgrep", "-f", r"([m]k|[p]ortabotty)\.py"], capture_output=True, text=True)
        return [int(p) for p in r.stdout.split() if int(p) not in mine and is_key(int(p))]
    pids = others()
    for p in pids:
        try: os.kill(p, signal.SIGTERM)
        except OSError: pass
    end = time.time() + wait
    while pids and time.time() < end:
        time.sleep(0.2); pids = others()
    for p in pids:
        try: os.kill(p, signal.SIGKILL)
        except OSError: pass
    if pids: time.sleep(0.5)
    return len(pids)


def install_launcher():
    """mk is primary; pb/pbk/pbkb and pbs/pbc remain compatibility aliases."""
    me = os.path.realpath(__file__)
    sudo = [] if os.geteuid() == 0 else ["sudo"]
    primary = (f'#!/bin/sh\n# modkvm primary launcher.\n'
               f'case "${{1-}}" in\n'
               f'  cam|see|c|mcp|web|-h|--help|help) exec {sys.executable} "{me}" "$@" ;;\n'
               f'  *) exec sudo {sys.executable} "{me}" "$@" ;;\nesac\n')
    kb_script = (f'#!/bin/sh\n# modkvm keyboard compatibility alias.\n'
                 f'exec sudo {sys.executable} "{me}" kb "$@"\n')
    see_script = (f'#!/bin/sh\n# modkvm Cam compatibility alias.\n'
                  f'[ $# -eq 0 ] && set -- run\nexec {sys.executable} "{me}" see "$@"\n')
    for name, script in (("mk", primary), ("pb", kb_script), ("pbk", kb_script),
                         ("pbkb", kb_script), ("pbs", see_script), ("pbc", see_script)):
        subprocess.run(sudo + ["tee", f"/usr/local/bin/{name}"], input=script, text=True, stdout=subprocess.DEVNULL, check=True)
        subprocess.run(sudo + ["chmod", "755", f"/usr/local/bin/{name}"], check=True)
    print("Installed mk (primary), pb/pbk/pbkb (Key aliases), pbs/pbc (Cam aliases).")


def main_kb():
    if "--pads" in sys.argv:
        return pad_diagnose()
    if "install-service" in sys.argv:
        return cmd_key_service()
    if os.geteuid() != 0:
        print("mk: Key mode needs root for Bluetooth. Run: sudo python3 mk.py", flush=True)
        return
    print("mk: debug: entering main_kb", flush=True)
    if ensure_key_service():
        print("modkvm: always-on service installed and started (mkkey.service).", flush=True)
        print("modkvm: it keeps running after you leave. This terminal instance exits.", flush=True)
        return
    print("mk: debug: ensure_key_service done", flush=True)
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    dbus.mainloop.glib.threads_init()
    print("mk: debug: dbus mainloop set", flush=True)
    reap_stale()
    print("mk: debug: reap_stale done", flush=True)
    bus = dbus.SystemBus()
    print("mk: debug: system bus connected", flush=True)
    name = server_name_for(load_state().get("name"), socket.gethostname())

    r = subprocess.run(["btmgmt", "class", "5", str(DEV_SUBCLASS)], capture_output=True, text=True)
    logging.info(f"btmgmt class: {(r.stdout or r.stderr).strip()[:120]}")

    have_bonds = bool(bonds(bus))
    for k, v in (("Powered", dbus.Boolean(True)), ("Alias", dbus.String(name)),
                 ("Pairable", dbus.Boolean(True)), ("PairableTimeout", dbus.UInt32(0)),
                 ("DiscoverableTimeout", dbus.UInt32(0 if not have_bonds else 120)),
                 ("Discoverable", dbus.Boolean(not have_bonds))):
        set_prop(bus, k, v)

    agent_manager = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"), "org.bluez.AgentManager1")
    agent = Agent(bus, "/modkvm/agent")
    agent_manager.RegisterAgent("/modkvm/agent", "NoInputNoOutput")
    agent_manager.RequestDefaultAgent("/modkvm/agent")

    pm = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"), "org.bluez.ProfileManager1")
    prof = Profile(bus, "/modkvm/hid")
    try:
        pm.RegisterProfile("/modkvm/hid", HID_UUID, {
            "Name": "modkvm Keyboard", "Role": "server",
            "RequireAuthentication": dbus.Boolean(False), "RequireAuthorization": dbus.Boolean(False),
            "ServiceRecord": SDP_RECORD})
    except dbus.exceptions.DBusException as e:
        if "already registered" in str(e) and not os.environ.get("MODKVM_RETRIED"):
            # something still holds the HID profile: stop it, restart Bluetooth, start over once
            reap_stale()
            try: subprocess.run(["systemctl", "restart", "bluetooth"], timeout=30, capture_output=True)
            except Exception: pass
            time.sleep(2)
            os.environ["MODKVM_RETRIED"] = "1"
            os.execv(sys.executable, [sys.executable] + sys.argv)
        sys.exit(f"Bluetooth won't take the HID profile ({e}). Try: sudo systemctl restart bluetooth")
    os.environ.pop("MODKVM_RETRIED", None)

    app = App(bus)
    app.server_name = name
    api_srv = web_srv = None
    if "--no-api" not in sys.argv:
        host, port = argval("--api-listen", "127.0.0.1"), int(argval("--api-port", KB_PORT))
        tok = load_token()
        try:
            api_srv = make_api(app, tok, host, port)
            app.api_info = f"API: http://{host}:{port} | Token file: {TOKEN_FILE}"
            logging.info(app.api_info)
        except OSError as e:
            logging.warning(f"API not started: {e}")
        if "--no-web" in sys.argv: pass
        elif not api_srv: app.web_url = "off (key API busy: another mk running? sudo pkill -f '[m]k\\.py kb')"
        else:
            global KB_URL
            KB_URL = f"http://127.0.0.1:{port}"
            os.environ["MKKEY_TOKEN"] = tok
            os.environ["PBKEY_TOKEN"] = tok
            web_srv, app.web_url, err = ensure_gateway(app)
            if err: app.web_url = f"off ({err}: sudo pkill -f '[m]k\\.py kb')"
    loop = GLib.MainLoop()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *a: app.stop.set())
    GLib.timeout_add(200, lambda: (loop.quit(), False)[1] if app.stop.is_set() else True)
    target = app.console if sys.stdin.isatty() else app.headless
    threading.Thread(target=target, daemon=True).start()
    try:
        loop.run()
    finally:
        app.stop.set()
        tty_restore()
        app.stop_pads()
        app.stop_see(quiet=True)
        if api_srv: api_srv.shutdown()
        if web_srv: web_srv.shutdown()
        with app.target_lock:
            app.reconnect_plan.cancel("stopped")
            for a in list(app.hid.conns): app.hid.drop(a)
            for s in app.hid.servers: s.close()
        try: pm.UnregisterProfile("/modkvm/hid")
        except Exception: pass
        try: agent_manager.UnregisterAgent("/modkvm/agent")
        except Exception: pass
        if app.restart:
            try: subprocess.run(["systemctl", "restart", "bluetooth"], timeout=30, capture_output=True)
            except Exception: pass
            time.sleep(2)
            os.execv(sys.executable, [sys.executable] + sys.argv)


# ======================================================================
# Web gateway: console UI + MCP-over-HTTP, proxies to kb/see (Core: Gateway+MCP)
# ======================================================================

KB_URL = os.environ.get("MKKEY_URL", os.environ.get("PBKEY_URL", f"http://127.0.0.1:{KB_PORT}")).rstrip("/")
SB_URL = os.environ.get("MKCAM_URL", os.environ.get("PBSEE_URL", f"http://127.0.0.1:{SB_PORT}")).rstrip("/")
WEB_CONFIG = os.environ.get("PORTABOTTY_CONFIG", os.path.join(HERE, "modkvm.json"))
WEB_CONFIG_LEGACY = os.path.join(HERE, "portabotty.json")   # read-only fallback; saves always use WEB_CONFIG
KB_PATHS = {"status", "use", "drop", "solo", "rename", "mouse", "type", "key", "goto", "click", "scroll", "drag", "move", "home"}
INPUT_PATHS = {"type", "key", "goto", "click", "scroll", "drag", "move", "home"}      # blocked on the web console while view-only
SB_GET = {"cams", "status", "stream", "snap"}
SB_POST = {"calibrate", "save", "focus"}
REMOTE_MAX_FRAME = 512_000
REMOTE_TTL = 15.0             # no frames: revoke the in-memory publishing permission
REMOTE_LIMIT = 4             # bounded remote sources, including disconnected rows
SEE = {}          # set by main_kb: {"state": () -> bool, "set": (bool) -> bool}  (camera server on/off)


# ---------------------------------------------------------------- config / tokens
def load_cfg():
    for p in (WEB_CONFIG, WEB_CONFIG_LEGACY):              # new name first, legacy fallback
        try:
            with open(p) as f: return json.load(f)
        except (OSError, ValueError):
            pass
    return {}


def save_cfg(cfg):
    fd = os.open(WEB_CONFIG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f: json.dump(cfg, f, indent=1)
    uid = os.environ.get("SUDO_UID")                       # written under sudo: keep it readable by you
    if uid:
        try: os.chown(WEB_CONFIG, int(uid), int(os.environ.get("SUDO_GID", uid)))
        except OSError: pass


def kb_token():
    t = os.environ.get("MKKEY_TOKEN", os.environ.get("PBKEY_TOKEN"))
    if t: return t.strip()
    for p in (os.environ.get("MKKEY_TOKEN_FILE", os.environ.get("PBKEY_TOKEN_FILE")), os.path.expanduser("~/.mkkey_token"), os.path.expanduser("~/.pbkey_token"),
              os.path.join(HERE, ".mkkey_token"), os.path.join(HERE, ".pbkey_token")):
        if p:
            try: return open(p).read().strip()
            except OSError: pass
    return ""


def sb_token():
    t = os.environ.get("MKCAM_TOKEN", os.environ.get("PBSEE_TOKEN"))
    if t: return t.strip()
    for p in (SEE_CONFIG, SEE_CONFIG_LEGACY):              # new name first, legacy fallback
        try:
            with open(p) as f:
                return json.load(f).get("token", "")
        except (OSError, ValueError):
            pass
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
    status, cam, remote, remote_support, camera_build = None, "none", False, None, None
    if kb_up:
        try: status = http_json(KB_URL + "/status", {"Authorization": "Bearer " + kb_token()})
        except Exception: status = None
    if sb_up:
        try:
            report = http_json(SB_URL + "/status", {"Authorization": "Bearer " + sb_token()})
            cams, remote = report.get("cams", []), report.get("remote_cameras", False)
            remote_support, camera_build = "remote_cameras" in report, report.get("build")
            if any(c.get("fps", 0) > 0 and (c.get("frame_age") is None or c["frame_age"] < 3) for c in cams): cam = "live"
            elif any(c.get("present") for c in cams): cam = "idle"
        except Exception:
            pass
    return {"kb": kb_up, "sb": sb_up, "cam": cam, "status": status, "links": load_cfg().get("links", {}),
            "see_on": SEE.get("state", lambda: None)(), "viewonly": bool(load_cfg().get("viewonly")),
            "remote_cameras": remote, "remote_camera_support": remote_support,
            "version": VERSION, "build": BUILD, "camera_build": camera_build}


def remote_request_ok(handler):
    """Only a local tunnel/TLS proxy may publish; never trust forwarded-IP/TLS headers.
    A browser Origin must match this console, also when its loopback token is disabled.
    """
    if handler.client_address[0] not in ("127.0.0.1", "::1"): return False
    origin = handler.headers.get("Origin")
    if origin:
        u = urllib.parse.urlparse(origin)
        if u.scheme not in ("http", "https") or u.netloc.lower() != handler.headers.get("Host", "").lower(): return False
    return handler.headers.get("Sec-Fetch-Site", "") != "cross-site"


# ---------------------------------------------------------------- gateway (web console + proxies)
CONSOLE = r"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover,user-scalable=no">
<meta name=modkvm-build content="__PB_BUILD__">
<title>modkvm</title>
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
body.vo #mode,body.vo #kbd,body.vo #tr,body.vo #dock,body.vo #pad{display:none!important}body.vo #stage{cursor:default}
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
#sharing{padding:8px 14px;background:var(--acc2);display:flex;align-items:center;justify-content:space-between;gap:10px}#sharing[hidden]{display:none}
#remote{padding-bottom:12px}#remote small{width:100%}
#connectiondetails{width:100%;font-size:12px;color:var(--dim)}#connectiondetails summary{cursor:pointer}
</style>
<div id=bar><span class=brand id=brand>modkvm</span><span class=pill id=kbi><i></i>key</span><span class=pill id=sbi><i></i>see</span><div id=dev></div>
<button id=mode aria-label=mode></button><button id=kbd aria-label=keyboard></button><button id=tr aria-label=keys></button><button id=vo aria-label=view></button><button id=lk aria-label="Link devices and cameras" title="Link devices and share this computer's camera"></button></div>
<div id=sharing hidden role=status><span id=sharestate>Starting camera sharing…</span><button id=stopshare>Stop sharing</button></div>
<div id=stage><div id=none><span id=nt></span><button id=ns style=display:none>Start camera</button></div><div id=wrap><img id=cam alt=""><i id=ptr></i><div id=pad></div></div><div id=calbar><span id=ct></span><button id=cx>Cancel</button></div></div>
<div id=dock><div id=dtabs><div class=seg><button id=tabt>Tray</button><button id=tabk>Keys</button><button id=tabp>Pad</button></div><span></span></div>
<div id=tray></div><div id=board></div><div id=tpd style=display:none><div id=tpad><i id=tdot></i></div><div id=tbtn><button id=tl>Left</button><button id=tm>Middle</button><button id=trr>Right</button></div></div></div><input id=kb autocapitalize=off autocorrect=off autocomplete=off spellcheck=false aria-label=keyboard>
<div id=sheet><div id=scrim></div><div id=panel><div id=ph><span>Link</span><button id=pc aria-label=close>✕</button></div><div id=pb>
<div class=set id=srv><span>This server</span><input id=sn maxlength=60 aria-label=name><button id=sv>Save</button></div>
<div class=set id=remote><input type=text id=remotename maxlength=60 value="Remote camera" aria-label="Remote camera name"><button id=share>Share this computer’s camera</button><small id=sharestatus role=status>Checking camera service…</small><small id=sharehint>This browser shares its camera into Views. Keep this tab open.</small><details id=connectiondetails><summary>Connection details</summary><small id=buildinfo>web __PB_BUILD__</small></details></div>
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
IC.eye='<svg class=i viewBox="0 0 24 24"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx=12 cy=12 r=3 /></svg>';$('vo').innerHTML=IC.eye;$('kbd').innerHTML=IC.kbd;$('tr').innerHTML=IC.tr;$('lk').innerHTML=IC.lk+'<span>Link / Camera</span>';
// ---- api (serialised so keys keep their order)
let chain=Promise.resolve();
const raw=(p,b)=>fetch(U('/kb/'+p),{method:b===undefined?'GET':'POST',body:b===undefined?undefined:JSON.stringify(b)}).then(r=>r.json()).catch(()=>({ok:false}));
let vo=false;const INP=new Set(['type','key','goto','click','scroll','drag','move','home']);
const send=(p,b)=>vo&&INP.has(p)?Promise.resolve({ok:false}):chain=chain.then(()=>raw(p,b));
const post=(p,b)=>fetch(U(p),{method:'POST',body:JSON.stringify(b||{})}).then(r=>r.json()).catch(()=>({}));
// ---- voluntary remote camera; never started by polling, viewing, or a stored preference
let localShare=null;
function cameraAvailability(){
  const steps=[];
  if(!window.isSecureContext||!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia)steps.push('Open this page through HTTPS or your SSH localhost tunnel.');
  if(!hub.sb)steps.push('Start the camera service on the Linux host.');
  else if(hub.remote_camera_support===null)steps.push('Check the camera service connection on the Linux host.');
  else if(hub.remote_camera_support===false)steps.push('Restart the camera service on the Linux host.');
  else if(!hub.remote_cameras)steps.push('Enable remote_cameras on the Linux host, then restart its camera service.');
  return steps.join(' ');
}
function cameraSetupStatus(){
  $('sharestatus').textContent=cameraAvailability()||'Ready. Choose Share to allow this browser’s camera.';
  $('buildinfo').textContent='web '+(hub.build||'unavailable')+' · camera '+(hub.camera_build||'unavailable');
}
async function cameraRequest(path,body,session,ctype='application/json'){
  const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),8000);
  if(session)session.abort=abort;
  try{
    const r=await fetch(U(path),{method:'POST',headers:{'Content-Type':ctype,...(session&&session.token?{'X-PB-Camera':session.token}:{})},body,signal:abort.signal});
    const j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||'Camera sharing failed');return j;
  }finally{clearTimeout(timer);if(session&&session.abort===abort)session.abort=null}
}
function stopSharing(message='Camera sharing stopped'){
  const s=localShare;localShare=null;
  if(s){clearTimeout(s.timer);if(s.abort)s.abort.abort();if(s.stream)s.stream.getTracks().forEach(t=>t.stop());s.video.pause();s.video.srcObject=null;
    if(s.name)cameraRequest('/remote/stop/'+encodeURIComponent(s.name),'{}',{token:s.token}).catch(()=>{});}
  $('sharing').hidden=true;$('share').disabled=false;$('remotename').disabled=false;$('sharehint').textContent=message;
}
async function sendCameraFrame(s){
  if(localShare!==s)return;
  try{
    if(!s.video.videoWidth||!s.video.videoHeight)throw Error('Camera did not supply an image');
    const scale=Math.min(1,1280/s.video.videoWidth,720/s.video.videoHeight);
    s.canvas.width=Math.max(1,Math.round(s.video.videoWidth*scale));s.canvas.height=Math.max(1,Math.round(s.video.videoHeight*scale));
    s.canvas.getContext('2d').drawImage(s.video,0,0,s.canvas.width,s.canvas.height);
    const blob=await new Promise(resolve=>s.canvas.toBlob(resolve,'image/jpeg',.8));
    if(localShare!==s)return;if(!blob||blob.size>512000)throw Error('Camera frame is too large');
    await cameraRequest('/remote/frame/'+encodeURIComponent(s.name),blob,s,'image/jpeg');
    if(localShare!==s)return;$('sharestate').textContent='Sharing '+s.label+' with modkvm viewers';
    s.timer=setTimeout(()=>sendCameraFrame(s),200); // one upload at a time; at most 5 fps, no queued footage
  }catch(e){if(localShare===s)stopSharing(e.message+' — start sharing again when ready')}
}
$('share').onclick=async()=>{
  if(localShare)return;
  const unavailable=cameraAvailability();if(unavailable){$('sharestatus').textContent=unavailable;return}
  const label=$('remotename').value.trim();if(!label){$('sharehint').textContent='Enter a camera name first';return}
  const s={label,video:document.createElement('video'),canvas:document.createElement('canvas')};localShare=s;
  $('share').disabled=true;$('remotename').disabled=true;$('sharing').hidden=false;$('sharestate').textContent='Starting '+label+' — waiting for camera permission';
  try{
    const stream=await navigator.mediaDevices.getUserMedia({video:{width:{ideal:1280},height:{ideal:720},frameRate:{ideal:5,max:10}},audio:false});
    if(localShare!==s){stream.getTracks().forEach(t=>t.stop());return}s.stream=stream;
    stream.getVideoTracks().forEach(t=>t.addEventListener('ended',()=>{if(localShare===s)stopSharing('Camera permission ended; sharing stopped')}));
    s.video.muted=true;s.video.playsInline=true;s.video.srcObject=stream;await s.video.play();if(localShare!==s)return;
    const j=await cameraRequest('/remote/start',JSON.stringify({label}),s);
    if(localShare!==s){cameraRequest('/remote/stop/'+encodeURIComponent(j.name),'{}',{token:j.token}).catch(()=>{});return}
    s.name=j.name;s.token=j.token;viewCam=j.name;ls.set('view',viewCam);tick=0;poll();sendCameraFrame(s);
  }catch(e){if(localShare===s)stopSharing(e.message||'Camera permission declined')}
};
$('stopshare').onclick=()=>stopSharing();
$('stopshare').addEventListener('keydown',e=>e.stopPropagation());
addEventListener('pagehide',()=>stopSharing());
document.addEventListener('visibilitychange',()=>{if(document.hidden&&localShare)stopSharing('Sharing stopped because this page was hidden; start again when ready')});
// ---- state
let hub={},devs=[],cams=[],links={},viewCam=ls.get('view')||'',lastActive=null,sel=null,expanded=new Set(),sig='',nonce=0,tick=0,camKey='',cal=null,autoCalDone=new Set();
let pollPending=null,pollAgain=false,pollEpoch=0;
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const camOf=n=>cams.find(c=>c.name===n),devOf=a=>devs.find(d=>d.addr===a);
function chips(){
  const el=$('dev'),old=Array.from(el.children),kept=new Set();
  devs.forEach((d,i)=>{const b=old.find(n=>n.dataset.addr===d.addr)||document.createElement('button');b.dataset.addr=d.addr;kept.add(b);
    b.className='chip'+(d.connected?' up':'')+(d.active?' act':'');
    const c=links[d.addr],label='<i class=dot></i><span>'+esc(d.name)+'</span>'+(c?'<small>'+esc(c)+'</small>':'');if(b.innerHTML!==label)b.innerHTML=label;
    b.onclick=()=>{pollEpoch++;return send('use',{device:d.addr}).then(poll)};
    if(el.children[i]!==b)el.insertBefore(b,el.children[i]||null)});
  old.forEach(b=>{if(!kept.has(b))b.remove()});
}
function poll(){
  if(pollPending){pollAgain=true;return pollPending}
  const epoch=pollEpoch,abort=new AbortController(),timer=setTimeout(()=>abort.abort(),10000);
  pollPending=fetch(U('/hub'),{signal:abort.signal}).then(r=>r.json()).then(h=>{
  if(epoch!==pollEpoch)return;
  hub=h;cameraSetupStatus();vo=!!h.viewonly;document.body.classList.toggle('vo',vo);$('vo').classList.toggle('on',vo);dockSize();const s=h.status||{};devs=s.devices||[];links=h.links||{};if(s.name&&s.name!==$('brand').textContent){$('brand').textContent=s.name;document.title=s.name}
  $('kbi').className='pill'+(h.kb?' up':'');$('sbi').className='pill'+(h.sb?(h.cam==='live'?' up':' idle'):'');
  if(s.pointer&&!cal){$('ptr').style.cssText='display:block;left:'+s.pointer[0]*100+'%;top:'+s.pointer[1]*100+'%'}else $('ptr').style.display='none';
  if(s.active!==lastActive){lastActive=s.active;if(links[s.active]){viewCam=links[s.active];ls.set('view',viewCam)}}
  const done=()=>{if(epoch!==pollEpoch)return;chips();stream();placeholder();render();maybeAutoCal()};
  if(h.sb&&(!cams.length||tick++%4===0)){return fetch(U('/cams'),{signal:abort.signal}).then(r=>r.json()).then(c=>{if(epoch!==pollEpoch)return;cams=c;if(!camOf(viewCam)||(!camOf(viewCam).present&&camOf(viewCam).source!=='remote')){const p=cams.find(x=>x.present)||cams[0];viewCam=p?p.name:''}done()}).catch(done)}
  else{if(!h.sb)cams=[];done()}
 }).catch(()=>{if(epoch===pollEpoch)$('kbi').className=$('sbi').className='pill'}).finally(()=>{
   clearTimeout(timer);pollPending=null;if(pollAgain){pollAgain=false;poll()}
 });return pollPending}
function stream(force){
  if(cal)return;const c=camOf(viewCam),o=c&&c.opts||{};
  const key=hub.sb&&c&&c.present?viewCam+'|'+JSON.stringify(o)+'|'+nonce:'';
  if(key===camKey&&!force)return;camKey=key;
  const old=$('cam'),img=document.createElement('img');img.id='cam';img.alt='';
  img.onerror=()=>{if($('cam')!==img)return;camKey='';img.removeAttribute('src')};
  old.onerror=null;old.removeAttribute('src');old.replaceWith(img);
  if(key)img.src=U('/stream/'+encodeURIComponent(viewCam)+'?_='+nonce);
}
function placeholder(){
  let t='',b=false;
  if(!hub.kb&&!hub.sb)t='offline';
  else if(!hub.sb){t=hub.see_on===false?'camera off':'starting camera…';b=hub.see_on===false}
  else if(!cams.length)t='no camera found';
  else if(!camOf(viewCam)||!camOf(viewCam).present)t=camOf(viewCam)&&camOf(viewCam).source==='remote'?'Remote camera disconnected — start sharing on its computer':'camera not connected';
  $('nt').textContent=t;$('ns').style.display=b?'':'none';
}
$('ns').onclick=()=>post('/see',{on:true}).then(()=>setTimeout(poll,800));
setInterval(poll,1500);poll();
// ---- modes
let mode=(ls.get('mode')||(matchMedia('(pointer:coarse)').matches?'pad':'direct'))==='pad'?'pad':'direct';
function setMode(m){mode=m;ls.set('mode',m);$('stage').classList.toggle('pad',m==='pad');$('mode').innerHTML=m==='pad'?IC.pad:IC.mode;$('mode').classList.toggle('on',m==='pad')}
$('vo').onclick=()=>post('/viewonly',{on:!vo}).then(poll);
$('mode').onclick=()=>setMode(mode==='pad'?'direct':'pad');setMode(mode);
// ---- pointer
const frac=(x,y)=>{const r=$('cam').getBoundingClientRect();return[Math.min(1,Math.max(0,(x-r.left)/r.width)),Math.min(1,Math.max(0,(y-r.top)/r.height))]};
const P=new Map();let g=null,mvAcc=[0,0],mvT=0,scAcc=0;
function flushMove(force){const n=performance.now();if(!force&&n-mvT<25)return;if(mvAcc[0]||mvAcc[1]){const dx=Math.round(mvAcc[0]),dy=Math.round(mvAcc[1]);mvAcc[0]-=dx;mvAcc[1]-=dy;mvT=n;send('move',{dx,dy})}}
function scroll(px){scAcc+=px;const n=Math.trunc(scAcc/28);if(n){scAcc-=n*28;send('scroll',{dir:n>0?'down':'up',amount:Math.abs(n)})}}
$('stage').addEventListener('pointerdown',e=>{e.preventDefault();if(vo&&!cal)return;if(cal||e.target.closest('#calbar,#none button'))return;$('stage').setPointerCapture(e.pointerId);P.set(e.pointerId,{x:e.clientX,y:e.clientY});
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
function maybeAutoCal(){
  if(cal||!hub.sb||autoCalDone.has(viewCam))return;
  const c=camOf(viewCam);if(!c||!c.present)return;
  const o=c.opts||{};if(o.warp||o.crop)return;
  autoCalDone.add(viewCam);startCal();
}
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
  if(vo)return;
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
  r.innerHTML='<div class=main><i class="dot '+(c.present?(viewCam===c.name?'up':'idle'):'off')+'"></i><span class=nm><b>'+esc(c.label||c.name)+'</b><small>'+esc((c.source==='remote'?'Remote · '+(c.sharing?'sharing':'disconnected')+' · ':'')+(dn||c.name))+'</small></span><button class=chev aria-label=more>'+IC.chev+'</button></div><span class="port'+(linked.length?' linked':'')+'" data-k=v data-id="'+esc(c.name)+'"></span>';
  const o=c.opts||{},m=document.createElement('div');m.className='more';
  const s=(patch)=>saveCam(c.name,patch);
  m.append(setRow('Rotate',seg([[0,'0'],[90,'90'],[180,'180'],[270,'270']],o.rot||0,v=>s({rot:v}))));
  m.append(setRow('Tilt',range(-20,20,.5,o.tilt||0,v=>s({tilt:v}))));
  m.append(setRow('Gray',sw(!!o.gray,v=>s({gray:v?1:0}))));
  m.append(setRow('Contrast',range(0,10,.5,o.contrast||0,v=>s({contrast:v}))));
  m.append(setRow('Denoise',range(1,12,1,o.avg||1,v=>s({avg:v}))));
  m.append(setRow('Sharpness',range(0,3,.5,o.sharp||0,v=>s({sharp:v}))));
  const w=document.createElement('select');[[0,'native'],[480,'480'],[720,'720'],[960,'960'],[1280,'1280']].forEach(([v,l])=>{const op=document.createElement('option');op.value=v;op.textContent=l;if((o.w||0)==v)op.selected=true;w.append(op)});w.onchange=()=>s({w:+w.value});
  m.append(setRow('Width',w));
  const b=document.createElement('div');b.className='btns';
  if(c.source==='remote'){
    if(c.sharing)b.append(btn('Stop remote camera',()=>cameraRequest('/remote/stop/'+encodeURIComponent(c.name),'{}').then(()=>{tick=0;poll()}).catch(e=>{$('sharehint').textContent=e.message})));
  }else b.append(btn(c.focus_locked?'Unlock focus':'Lock focus',()=>post('/focus/'+encodeURIComponent(c.name),{lock:!c.focus_locked}).then(()=>{poll()})));
  b.append(btn(o.warp?'Recalibrate':'Calibrate',()=>{viewCam=c.name;startCal()}));
  if(o.warp)b.append(btn('Clear',()=>post('/calibrate/'+encodeURIComponent(c.name),{clear:true}).then(()=>{nonce++;poll()})));
  linked.forEach(a=>b.append(btn('Unlink '+((devOf(a)||{}).name||a).slice(0,14),()=>setLink(a,null))));
  m.append(b);r.append(m);return r}
function saveCam(name,patch){const c=camOf(name);if(c)c.opts=Object.assign({},c.opts,patch);return post('/save/'+encodeURIComponent(name),patch).then(()=>{nonce++;poll()})}
function setLink(addr,cam){return post('/links',{addr,cam}).then(j=>{links=j.links||{};if(cam&&hub.status&&hub.status.active===addr){viewCam=cam;ls.set('view',cam)};sig='';poll();setTimeout(()=>{sig='';render();setTimeout(draw,0);if(cam)$('sharehint').textContent='Linked '+cam+' to device (saved)'},50)})
function render(){
  if(!$('sheet').classList.contains('open'))return;
  setTimeout(draw,0);
  const s=JSON.stringify([devs,cams.map(c=>[c.name,c.label,c.present,c.opts,c.focus_locked,c.sharing]),links,[...expanded],sel,viewCam,hub.see_on,(hub.status||{}).solo,(hub.status||{}).trackpad]);
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


class WebHandler(QuietHTTPHandler):
    server_version = "modkvm"
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

    def _proxy(self, method, base, path, query, token, body=None, extra_headers=None):
        q = [(k, v) for k, v in urllib.parse.parse_qsl(query) if k != "t"]        # our token is not theirs
        url = base + path + ("?" + urllib.parse.urlencode(q) if q else "")
        req = urllib.request.Request(url, data=body, method=method,
                                     headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", **(extra_headers or {})})
        try:
            r = urllib.request.urlopen(req, timeout=30)
        except urllib.error.HTTPError as e:
            with e: return self._send(e.code, e.read(), e.headers.get("Content-Type", "application/json"))
        except OSError:
            return self._json({"ok": False, "error": "stopped"}, 502)
        stop_watch, watcher = threading.Event(), None
        try:
            ctype = r.headers.get("Content-Type", "application/octet-stream")
            if ctype.startswith("multipart"):                                     # live stream: pass bytes through
                # read1 can block while the source has no frames. A duplicated socket lets the
                # watcher interrupt that read on browser cancellation, without urllib internals.
                upstream = socket.socket(fileno=os.dup(r.fileno()))
                def watch_client():
                    try:
                        while not stop_watch.wait(.25):
                            if self._client_gone():
                                try: upstream.shutdown(socket.SHUT_RDWR)
                                except OSError: pass
                                return
                    finally: upstream.close()
                watcher = threading.Thread(target=watch_client, daemon=True); watcher.start()
                self.send_response(200); self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store"); self.end_headers()
                while True:
                    if self._client_gone(): break
                    chunk = r.read1(65536)
                    if not chunk: break
                    self.wfile.write(chunk); self.wfile.flush()
            else:
                self._send(r.status, r.read(), ctype)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            stop_watch.set()
            r.close()                                                             # lets Cam stop an unwatched camera
            if watcher: watcher.join(.5)

    def _mcp(self):
        """MCP over HTTP (JSON-RPC in a POST): the same tools as the stdio adapter, run in this process."""
        try:
            m = json.loads(self._body())
        except ValueError:
            return self._json({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}, 400)
        msgs = m if isinstance(m, list) else [m]
        out = [r for r in (handle(x) for x in msgs if isinstance(x, dict)) if r is not None]
        if not out: return self._send(202, b"", "application/json")
        self._json(out if isinstance(m, list) else out[0])

    def _route(self, method):
        u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query)
        parts = [p for p in u.path.split("/") if p]
        if locked_out(self.client_address[0]): return self._send(429, b"too many wrong tokens, wait a minute", "text/plain")
        if not self._auth(q): return self._send(401, b"token required (?t=...)", "text/plain")
        if method == "GET":
            if not parts: return self._send(200, CONSOLE.replace("__PB_BUILD__", BUILD).replace("__PB_VERSION__", VERSION).encode(), "text/html; charset=utf-8")
            if parts == ["version"]: return self._json({"version": VERSION, "build": BUILD, "features": {"remote_camera_sharing": 1}})
            if parts == ["mcp"]: return self._send(405, b"POST only", "text/plain", (("Allow", "POST"),))
            if parts == ["hub"]: return self._json(hub_status())
            if parts == ["links"]: return self._json({"links": load_cfg().get("links", {})})
            if parts[0] == "kb" and len(parts) == 2 and parts[1] in KB_PATHS:
                return self._proxy("GET", KB_URL, "/" + parts[1], u.query, kb_token())
            if parts[0] in SB_GET and len(parts) <= 2:
                return self._proxy("GET", SB_URL, u.path, u.query, sb_token())
        else:
            if parts[0:1] == ["remote"]: return self._remote(u, parts)
            if parts == ["mcp"]: return self._mcp()
            if parts == ["links"]: return self._links()
            if parts == ["see"]:
                if "set" not in SEE: return self._json({"ok": False, "error": "camera server is managed by Key"}, 501)
                try: on = bool(json.loads(self._body()).get("on"))
                except Exception: return self._json({"ok": False}, 400)
                return self._json({"ok": True, "on": SEE["set"](on)})
            if parts == ["viewonly"]:
                try: on = bool(json.loads(self._body()).get("on"))
                except Exception: return self._json({"ok": False}, 400)
                cfg = load_cfg(); cfg["viewonly"] = on; save_cfg(cfg)
                return self._json({"ok": True, "viewonly": on})
            if parts[0:1] == ["kb"] and len(parts) == 2 and parts[1] in KB_PATHS:
                if parts[1] in INPUT_PATHS and load_cfg().get("viewonly"):
                    return self._json({"ok": False, "error": "view only"}, 403)
                return self._proxy("POST", KB_URL, "/" + parts[1], "", kb_token(), self._body())
            if parts[0:1] and parts[0] in SB_POST and len(parts) == 2:
                return self._proxy("POST", SB_URL, u.path, u.query, sb_token(), self._body())
        self._send(404, b"not found", "text/plain")

    def _remote(self, u, parts):
        if not remote_request_ok(self):
            return self._json({"ok": False, "error": "use an SSH localhost tunnel or a local HTTPS proxy"}, 403)
        frame = len(parts) == 3 and parts[1] == "frame"
        if parts != ["remote", "start"] and not (len(parts) == 3 and parts[1] in ("frame", "stop")):
            return self._json({"ok": False, "error": "unknown camera action"}, 404)
        ctype = self.headers.get("Content-Type", "").split(";")[0]
        if ctype != ("image/jpeg" if frame else "application/json"):
            return self._json({"ok": False, "error": "unexpected content type"}, 415)
        try: n = int(self.headers.get("Content-Length", "0"))
        except ValueError: n = -1
        if not 0 < n <= (REMOTE_MAX_FRAME if frame else 1024):
            return self._json({"ok": False, "error": "invalid body size"}, 413)
        self.connection.settimeout(10)
        try: body = self.rfile.read(n)
        except OSError: return self._json({"ok": False, "error": "upload timed out"}, 408)
        if len(body) != n: return self._json({"ok": False, "error": "incomplete upload"}, 400)
        return self._proxy("POST", SB_URL, u.path, "", sb_token(), body,
                           {"Content-Type": ctype, "X-PB-Camera": self.headers.get("X-PB-Camera", "")})

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
    srv = ThreadingHTTPServer((listen, port), WebHandler); srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def ensure_gateway(app=None):
    """Start the web Gateway (+MCP) if nothing is listening on its port yet - callable from Key or Cam,
    whichever comes up first, so the Gateway is a baseline and not owned by either one specifically.
    `app` (Key's App instance) is only given when called from kb mode, to wire the camera on/off switch
    and relay hosting through it; called with app=None (from cam mode) it just gets the Gateway itself up.
    Returns (srv_or_None, url, error_or_None); srv is None both when --no-web is set and when the
    Gateway turned out to already be running elsewhere (not an error - that's the common case)."""
    if "--no-web" in sys.argv:
        return None, "", None
    wl, wp = argval("--web-listen", "0.0.0.0"), int(argval("--web-port", DB_PORT))
    wtok = web_token(wl, argval("--web-token", ""))
    url = web_url(wl, wp, wtok)
    srv, err = None, None
    try:
        srv = start_gateway(wl, wp, wtok)
    except OSError as e:
        if not port_open(f"http://127.0.0.1:{wp}"):
            err = str(e)      # a real bind failure, not just "already running"
    if app:
        app.web_link = app.web_url = url
        SEE["state"] = lambda: app.want_see
        SEE["set"] = app.set_see
        app.web_port, app.web_tok = wp, wtok
        if srv:
            cfg = load_cfg()
            ru, rt = argval("--relay", cfg.get("relay", "")), argval("--relay-token", cfg.get("relay_token", ""))
            if ru and rt: app.start_hosting(ru.rstrip("/"), rt)
    if srv and wl == "0.0.0.0":
        fw = firewall_hint(wp)
        if fw: url += f"   (ufw blocks LAN: {fw})"
    return srv, url, err


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
    """'live' (frames flowing) / 'idle' (up, camera stopped until watched) / 'stopped' (Cam not running)."""
    if not port_open(SB_URL): return "stopped"
    try:
        cams = http_json(SB_URL + "/status", {"Authorization": "Bearer " + sb_token()}).get("cams", [])
    except Exception:
        return "idle"
    if any(c.get("fps", 0) > 0 and (c.get("frame_age") is None or c["frame_age"] < 3) for c in cams): return "live"
    return "idle"


def relay_agent(url, agent_token, port, local_token=""):
    """Dial out to a Relay and serve its queued requests from the local gateway (no inbound port needed)."""
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
        if job.get("camera_token"): hdr["X-PB-Camera"] = job["camera_token"][:128]
        try:
            relay_url = urllib.parse.urlparse(url)
            if job["path"].startswith("/remote/") and not (relay_url.scheme == "https" or
                    (relay_url.scheme == "http" and relay_url.hostname in ("127.0.0.1", "localhost", "::1"))):
                up, status, ctype = io.BytesIO(b"remote cameras require an HTTPS relay"), 403, "text/plain"
            else:
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

    class Relay:
        def __init__(self): self.stop_ev = threading.Event(); self.ok = False; self.url = url
        def stop(self): self.stop_ev.set()
    handle = Relay()

    def loop():
        while not handle.stop_ev.is_set():
            try:
                with call("/_a/next", timeout=40) as r:
                    handle.ok = True
                    if r.status == 200 and not handle.stop_ev.is_set():
                        threading.Thread(target=serve, args=(json.loads(r.read()),), daemon=True).start()
            except Exception:
                handle.ok = False
                handle.stop_ev.wait(3)
        handle.ok = False
    threading.Thread(target=loop, daemon=True).start()
    return handle


WEB_DOC = """modkvm web gateway (mode 'web'): the web console (live image, trackpad, keyboard) and MCP over HTTP,
in front of kb and see mode. Normally started by kb mode in-process. Standalone, against kb/see running elsewhere:
  python3 mk.py web [--listen ADDR] [--port N] [--token T]
Details: README.md
"""


def main_web(args=None):
    ap = argparse.ArgumentParser(description=WEB_DOC, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--listen"); ap.add_argument("--port", type=int); ap.add_argument("--token")
    ap.add_argument("--relay"); ap.add_argument("--relay-token")
    a = ap.parse_args(args)
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


# ======================================================================
# Camera server: ffmpeg capture + HTTP (Cam, mode 'see')
# ======================================================================
SYSFS = os.environ.get("MKCAM_SYSFS", os.environ.get("PBSEE_SYSFS", "/sys"))
DEVFS = os.environ.get("MKCAM_DEVFS", os.environ.get("PBSEE_DEVFS", "/dev"))
SEE_CONFIG = os.environ.get("MKCAM_CONFIG", os.environ.get("PBSEE_CONFIG", os.path.join(HERE, "mkcam.json")))
SEE_CONFIG_LEGACY = os.path.join(HERE, "pbsee.json")      # read-only fallback; saves always use SEE_CONFIG
IDLE_STOP = float(os.environ.get("MKCAM_IDLE", os.environ.get("PBSEE_IDLE", "10")))     # seconds without viewers before a camera stops
FOCUS_WAIT = float(os.environ.get("MKCAM_FOCUS_WAIT", os.environ.get("PBSEE_FOCUS_WAIT", "1.2")))   # seconds autofocus gets to settle before it is frozen
CHANGE_THRESHOLD = 2.0                                        # mean abs pixel diff (0-255) that counts as "screen changed"
OPT_KEYS = ("rot", "tilt", "crop", "warp", "contrast", "sharp", "gamma", "gray", "w", "q", "avg")


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
    for p in (SEE_CONFIG, SEE_CONFIG_LEGACY):              # new name first, legacy fallback
        try:
            with open(p) as f: cfg.update(json.load(f)); break
        except Exception:
            pass
    return cfg

def save_config(cfg):
    tmp = SEE_CONFIG + ".tmp"
    with open(tmp, "w") as f: json.dump(cfg, f, indent=2)
    os.replace(tmp, SEE_CONFIG)

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
                    ("gamma", float), ("gray", int), ("fps", float), ("avg", int)):
        v = one(k)
        if v not in (None, ""):
            try: o[k] = cast(v)
            except ValueError: pass
    if "tilt" in o: o["tilt"] = max(-45.0, min(45.0, o["tilt"]))
    if "avg" in o: o["avg"] = max(1, min(16, o["avg"]))
    for k, n in (("crop", 4), ("warp", 8)):
        v = _nums(one(k), n) if one(k) else None
        if v: o[k] = v
    return o

def needs_processing(o):
    return bool(o.get("avg", 1) > 1 or o.get("rot") or o.get("tilt") or o.get("crop") or o.get("warp") or o.get("contrast") or o.get("sharp")
                or o.get("gray") or o.get("w") or (o.get("gamma") not in (None, 0, 1, 1.0)))

class Denoiser:
    """Temporal denoise for a still scene: running mean of the last n frames, restarted when the picture moves.
    Sensor noise falls with sqrt(n); text edges stay put because a changed frame resets the mean."""
    MOVE = 6.0                                                  # mean abs diff (0-255) on a 32px thumbnail that counts as motion

    def __init__(self, n):
        self.n, self.acc, self.k, self.thumb = max(1, int(n)), None, 0, None

    def add(self, jpeg):
        from PIL import Image, ImageChops, ImageStat
        im = Image.open(io.BytesIO(jpeg)).convert("RGB")
        t = im.convert("L").resize((32, 32))
        if self.acc is None or self.acc.size != im.size or ImageStat.Stat(ImageChops.difference(t, self.thumb)).mean[0] > self.MOVE:
            self.acc, self.k = im, 1
        else:
            self.k = min(self.k + 1, self.n)
            self.acc = Image.blend(self.acc, im, 1.0 / self.k)
        self.thumb = t
        return self.acc


def process(jpeg, o, im=None):
    """Pass-through unless the view needs work. Order: tilt (crop views) -> warp/crop -> tilt (warped views) -> rotate -> contrast/gamma/sharpen -> resize.
    tilt = degrees to straighten a crooked camera (counter-clockwise positive)."""
    if im is None and not needs_processing(o):
        return jpeg
    from PIL import Image, ImageOps, ImageEnhance
    T, R, TR = getattr(Image, "Transform", Image), getattr(Image, "Resampling", Image), getattr(Image, "Transpose", Image)
    if im is None: im = Image.open(io.BytesIO(jpeg))
    W, H = im.size
    maxw = int(o.get("w") or 0)
    rot = int(o.get("rot") or 0) % 360
    if maxw and not o.get("warp") and hasattr(im, "draft"):   # let the JPEG decoder shrink for us (cheap on slow CPUs)
        side = int(o.get("rot") or 0) % 180 != 0         # after a 90/270 turn the output width comes from the source height
        c = o.get("crop")
        need = int(maxw / max((c[3] if side else c[2]) if c else 1.0, 0.05))
        req = (need * W // H, need) if side else (need, need * H // W)
        im.draft("RGB", (max(1, req[0]), max(1, req[1])))
    im = im.convert("RGB")
    W, H = im.size
    tilt = float(o.get("tilt") or 0)
    if tilt and not o.get("warp"):                       # straighten first so a plain crop can use every pixel
        im = im.rotate(tilt, resample=R.BICUBIC)
    if o.get("warp"):
        wp = o["warp"]; pts = [(wp[i] * W, wp[i + 1] * H) for i in (0, 2, 4, 6)]
        d = lambda a, b: math.hypot(a[0] - b[0], a[1] - b[1])
        ow, oh = (d(pts[0], pts[1]) + d(pts[3], pts[2])) / 2, (d(pts[0], pts[3]) + d(pts[1], pts[2])) / 2
        fw = oh if rot % 180 else ow                     # `w` limits the final (rotated) width
        if maxw and fw > maxw: ow, oh = ow * maxw / fw, oh * maxw / fw
        ow, oh = max(8, int(ow)), max(8, int(oh))
        im = im.transform((ow, oh), T.PERSPECTIVE, persp_coeffs(pts, ow, oh), R.BICUBIC)
        if tilt: im = im.rotate(tilt, resample=R.BICUBIC)   # fixes a hastily marked quadrilateral
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
        im = im.resize((maxw, max(1, im.height * maxw // im.width)), R.LANCZOS)
    if o.get("gray"):
        im = im.convert("L")
    out = io.BytesIO(); im.save(out, "JPEG", quality=int(o.get("q") or 90), subsampling=0)
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
        m = re.match(r"(\d+)x(\d+)", str(self.entry.get("size", "1920x1080")))
        return (int(m.group(1)), int(m.group(2))) if m else (1920, 1080)

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

    def focus(self, lock):
        """Lock: let autofocus settle once, then freeze it where it landed (stops focus hunting that smears text).
        Unlock: back to continuous autofocus."""
        if self.src.startswith("test:") or not shutil.which("v4l2-ctl"): return {"ok": False, "error": "camera has no focus control"}
        have = run(["v4l2-ctl", "-d", self.src, "--list-ctrls"])
        auto = next((k for k in ("focus_automatic_continuous", "focus_auto") if k in have), None)
        if not auto or "focus_absolute" not in have: return {"ok": False, "error": "camera has no focus control"}
        ctl = dict(self.entry.get("controls") or {})
        for k in ("focus_automatic_continuous", "focus_auto", "focus_absolute"): ctl.pop(k, None)
        self.entry["controls"] = ctl
        self.acquire()
        try:
            time.sleep(FOCUS_WAIT)
            run(["v4l2-ctl", "-d", self.src, "--set-ctrl", f"{auto}=1"])
            val = None
            if lock:
                time.sleep(FOCUS_WAIT * 2)
                m = re.search(r"focus_absolute\D*(-?\d+)", run(["v4l2-ctl", "-d", self.src, "--get-ctrl", "focus_absolute"]))
                if not m: return {"ok": False, "error": "could not read focus"}
                val = int(m.group(1))
                run(["v4l2-ctl", "-d", self.src, "--set-ctrl", f"{auto}=0"])
                run(["v4l2-ctl", "-d", self.src, "--set-ctrl", f"focus_absolute={val}"])
                ctl[auto], ctl["focus_absolute"] = 0, val
        finally:
            self.release()
        State.reg.save_controls(self)
        return {"ok": True, "locked": bool(lock), "value": val}

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
            self.error = "ffmpeg not found: run `mk cam doctor --fix`"; time.sleep(4); return
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


class RemoteCam(Cam):
    """A voluntarily published browser camera; viewing can never start capture.
    Tokens and images live only in memory. Every new sharing session needs a click.
    """
    def __init__(self, name, entry):
        super().__init__(name, "remote:", entry, {"label": entry.get("label", name)})
        self.publisher, self.deadline, self.last_upload = "", 0.0, 0.0
        self.present = False
        self.error = "remote camera stopped"

    def _ensure(self): pass                              # no ffmpeg, devices or automatic capture
    def focus(self, lock): return {"ok": False, "error": "focus is managed on the sharing computer"}

    def start(self):
        with self.cond:
            self.shutdown()
            self.publisher = secrets.token_urlsafe(32)
            self.deadline = time.monotonic() + REMOTE_TTL
            self.error = "waiting for the sharing computer"
            return self.publisher

    def authorized(self, token):
        with self.cond:
            self.expire()
            return bool(self.publisher and re.fullmatch(r"[A-Za-z0-9_-]{43}", token)) and hmac.compare_digest(token, self.publisher)

    def expire(self):
        with self.cond:
            if self.publisher and time.monotonic() >= self.deadline:
                self.shutdown()
                self.error = "sharing computer disconnected; start sharing again there"

    def publish(self, token, jpeg):
        with self.cond:
            if not self.authorized(token): raise ApiError("sharing session expired", 403)
            now = time.monotonic()
            if self.last_upload and now - self.last_upload < 0.1: raise ApiError("maximum 10 frames per second", 429)
            self.last_upload, self.deadline = now, now + REMOTE_TTL
            self.present, self.running, self.error = True, True, ""
            self._publish(jpeg)

    def shutdown(self):
        with self.cond:
            self.publisher, self.deadline, self.last_upload = "", 0.0, 0.0
            self.present = self.running = False
            self.latest, self.fps, self.stamps, self.prev = None, 0.0, [], None
            self.change, self.last_change, self.last_sample, self.dht_ok = 0.0, 0.0, 0.0, None
            self.error = "remote camera stopped"
            self.seq += 1
            self.cond.notify_all()

    def wait_frame(self, after, timeout=5.0):
        with self.cond:
            self.cond.wait_for(lambda: self.seq != after or not self.publisher, timeout)
            self.expire()
            return self.seq, self.latest if self.seq != after and self.present else None


class Registry:
    def __init__(self, cfg, test_source=False, allow_remote_cameras=False):
        self.cfg, self.cams, self.lock, self.test = cfg, {}, threading.RLock(), test_source
        self.t_refresh = 0.0
        self.remote_enabled = cfg.get("remote_cameras") is True or allow_remote_cameras
        self.remote_stop = threading.Event()
        self.remote_thread = None
        # Only saved view metadata is restored, always disconnected and without a token.
        for entry in cfg.get("cameras", []):
            if entry.get("source") == "remote" and re.fullmatch(r"remote-[a-z0-9-]{1,48}", entry.get("name", "")):
                self.cams[entry["name"]] = RemoteCam(entry["name"], entry)

    def start_remote(self, label):
        with self.lock:
            if not self.remote_enabled: raise ApiError("remote cameras are disabled on this server", 403)
            if not isinstance(label, str) or not label.strip() or len(label) > 60:
                raise ApiError("camera name must be 1–60 characters", 400)
            label = label.strip()
            name = "remote-" + (slug(label)[:48] or "camera")
            self.refresh()
            cam = self.cams.get(name)
            if cam and not isinstance(cam, RemoteCam): raise ApiError("name belongs to a server camera", 409)
            if cam:
                cam.expire()
                if cam.publisher: raise ApiError("that camera is already sharing; choose another name", 409)
            else:
                if sum(isinstance(c, RemoteCam) for c in self.cams.values()) >= REMOTE_LIMIT:
                    raise ApiError("remote camera limit reached; reuse an existing name", 409)
                entry = {"name": name, "label": label, "source": "remote"}
                cam = self.cams[name] = RemoteCam(name, entry)
            token = cam.start()
            if self.remote_thread is None:
                self.remote_thread = threading.Thread(target=self._expire_remotes, daemon=True)
                self.remote_thread.start()
            return {"ok": True, "name": name, "token": token, "expires_in": REMOTE_TTL}

    def _expire_remotes(self):
        while not self.remote_stop.wait(1):
            with self.lock:
                for cam in self.cams.values():
                    if isinstance(cam, RemoteCam): cam.expire()

    def refresh(self, force=False):
        with self.lock:
            if not force and self.cams and time.time() - self.t_refresh < 5:
                return self.cams                           # discovery spawns v4l2-ctl: don't redo it every poll
            self.t_refresh = time.time()
            claimed, used = set(), {n for n, c in self.cams.items() if isinstance(c, RemoteCam)}
            for c in self.cams.values():
                if isinstance(c, RemoteCam): c.expire()
                else: c.present = False
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
        with self.lock:
            self.refresh()
            return [{"name": c.name, "label": c.info.get("label"), "src": c.src, "present": c.present,
                     "usb": f"{c.info.get('vid')}:{c.info.get('pid')}" if c.info.get("vid") else "",
                     "opts": c.defaults(), "source": "remote" if isinstance(c, RemoteCam) else "local",
                     "sharing": bool(c.publisher) if isinstance(c, RemoteCam) else False,
                     "focus_locked": "focus_absolute" in (c.entry.get("controls") or {})}
                    for c in self.cams.values()]

    def status(self):
        with self.lock:
            self.refresh()
            return {"cams": [c.status() for c in self.cams.values()], "remote_cameras": self.remote_enabled,
                    "version": VERSION, "build": BUILD}

    def save_controls(self, cam):
        with self.lock:
            if cam.entry not in self.cfg["cameras"]: self._adopt(cam)
            save_config(self.cfg)

    def _adopt(self, cam):
        if isinstance(cam, RemoteCam):
            self.cfg["cameras"].append(cam.entry)
            return
        info = cam.info
        cam.entry.update({"name": cam.name,
                          "match": f"usb:{info['vid']}:{info['pid']}" if info.get("vid") and
                          sum(1 for c in self.cams.values() if c.info.get("vid") == info["vid"] and c.info.get("pid") == info["pid"]) == 1
                          else f"path:{info['stable']}"})
        self.cfg["cameras"].append(cam.entry)

    def save_opts(self, cam, opts):
        with self.lock:
            if cam.entry not in self.cfg["cameras"]: self._adopt(cam)
            cam.entry["opts"] = {k: v for k, v in opts.items() if k in OPT_KEYS and v not in (None, "", 0, [])}
            save_config(self.cfg)

    def shutdown(self):
        self.remote_stop.set()
        with self.lock:
            self.remote_enabled = False
            for c in self.cams.values(): c.shutdown()


# ---------------------------------------------------------------- HTTP
PAGE = r"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>modkvm Cam</title>
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

class SeeHandler(QuietHTTPHandler):
    server_version = "mkcam"
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
            if parts[0] == "snap": return self._snap(cam, o, explicit="avg" in q or ("raw" in q and q["raw"][-1] not in ("0", "")))
            return self._stream(cam, o)
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query)
        parts = [p for p in u.path.split("/") if p]
        if not self._auth(q): return self._send(401, b"token required", "text/plain")
        if parts[0:1] == ["remote"]: return self._remote(parts)
        if len(parts) != 2 or parts[0] not in ("calibrate", "save", "focus"):
            return self._send(404, b"not found", "text/plain")
        cam = State.reg.get(parts[1])
        if not cam: return self._send(404, b"no such camera", "text/plain")
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
        except Exception:
            return self._send(400, b"bad json", "text/plain")
        if parts[0] == "focus":
            return self._json(cam.focus(bool(body.get("lock", True))))
        opts = cam.defaults()
        if parts[0] == "calibrate":
            if body.get("clear"): opts.pop("warp", None); opts.pop("crop", None)
            elif _nums(body.get("warp"), 8): opts["warp"] = _nums(body["warp"], 8); opts.pop("crop", None)
            else: return self._send(400, b"need warp[8] or clear", "text/plain")
        else:
            for k, cast in (("rot", int), ("tilt", float), ("w", int), ("q", int), ("contrast", float), ("sharp", float), ("gamma", float), ("gray", int), ("avg", int)):
                if k in body:
                    try: opts[k] = cast(body[k])
                    except (TypeError, ValueError): pass
            if "avg" in opts: opts["avg"] = max(1, min(16, opts["avg"]))
            if "tilt" in opts: opts["tilt"] = round(max(-45.0, min(45.0, float(opts["tilt"]))), 2)
            if _nums(body.get("crop"), 4): opts["crop"] = _nums(body["crop"], 4)
        State.reg.save_opts(cam, opts)
        self._json({"ok": True, "opts": cam.defaults()})

    def _remote(self, parts):
        if not remote_request_ok(self): return self._json({"ok": False, "error": "local gateway required"}, 403)
        if not State.reg.remote_enabled: return self._json({"ok": False, "error": "remote cameras are disabled on this server"}, 403)
        frame = len(parts) == 3 and parts[1] == "frame"
        if parts != ["remote", "start"] and not (len(parts) == 3 and parts[1] in ("frame", "stop")):
            return self._json({"ok": False, "error": "unknown camera action"}, 404)
        if self.headers.get("Content-Type", "").split(";")[0] != ("image/jpeg" if frame else "application/json"):
            return self._json({"ok": False, "error": "unexpected content type"}, 415)
        cam = None
        if len(parts) == 3:
            cam = State.reg.get(parts[2])
            if not isinstance(cam, RemoteCam): return self._json({"ok": False, "error": "no such remote camera"}, 404)
            # Controller Stop may omit the capability. Source cleanup is scoped to its session,
            # so a delayed request cannot stop a replacement sharing the same camera name.
            token = self.headers.get("X-PB-Camera", "")
            if (frame or token) and not cam.authorized(token):
                return self._json({"ok": False, "error": "sharing session expired"}, 403)
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if not 0 < n <= (REMOTE_MAX_FRAME if frame else 1024): raise ApiError("invalid body size", 413)
            self.connection.settimeout(10)
            body = self.rfile.read(n)
            if len(body) != n: raise ApiError("incomplete upload")
            if frame:
                from PIL import Image
                try:
                    with Image.open(io.BytesIO(body)) as im:
                        if im.format != "JPEG" or max(im.size) > 1920 or im.width * im.height > 1920 * 1080:
                            raise ValueError("expected JPEG at most 1920x1080 pixels")
                        im.load()                       # reject malformed/truncated images before publishing
                except Exception: raise ApiError("invalid JPEG or image too large")
                cam.publish(self.headers.get("X-PB-Camera", ""), body)
                return self._json({"ok": True})
            if parts == ["remote", "start"]:
                data = json.loads(body)
                if not isinstance(data, dict): raise ApiError("JSON object expected")
                return self._json(State.reg.start_remote(data.get("label")))
            with cam.cond:
                # Reading the body can outlast a stop/restart; check and stop atomically.
                if token and not cam.authorized(token): raise ApiError("sharing session expired", 403)
                cam.shutdown()
            return self._json({"ok": True})
        except ApiError as e: return self._json({"ok": False, "error": str(e)}, e.code)
        except (ValueError, OSError): return self._json({"ok": False, "error": "invalid or incomplete request"}, 400)

    def _snap(self, cam, o, explicit=False):
        if isinstance(cam, RemoteCam) and not cam.present:
            return self._send(503, cam.error.encode(), "text/plain")
        publisher = cam.publisher if isinstance(cam, RemoteCam) else None
        cam.touch()
        seq, jpg = cam.wait_frame(cam.seq, 10)
        if jpg is None: return self._send(504, (cam.error or "no frame").encode(), "text/plain")
        try:
            n = int(o.get("avg") or 1) if explicit else max(int(o.get("avg") or 1), 4)      # stills are averaged by default
            if n > 1:
                d = Denoiser(n); d.MOVE = 1e9; im = d.add(jpg)
                for _ in range(n - 1):
                    seq, nxt = cam.wait_frame(seq, 2)
                    if nxt is not None: im = d.add(nxt)
                if isinstance(cam, RemoteCam) and publisher != cam.publisher:
                    return self._send(503, b"remote camera stopped", "text/plain")
                return self._send(200, process(jpg, o, im), "image/jpeg")
            if isinstance(cam, RemoteCam) and publisher != cam.publisher:
                return self._send(503, b"remote camera stopped", "text/plain")
            self._send(200, process(jpg, o), "image/jpeg")
        except Exception as e: self._send(500, f"processing failed: {e}".encode(), "text/plain")

    def _stream(self, cam, o):
        if isinstance(cam, RemoteCam) and not cam.present:
            return self._send(503, cam.error.encode(), "text/plain")
        publisher = cam.publisher if isinstance(cam, RemoteCam) else None
        cam.acquire()
        try:
            self.connection.settimeout(15)               # a stalled client must not keep a camera running forever
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store"); self.end_headers()
            seq, last, dt = 0, 0.0, (1.0 / o["fps"] if o.get("fps") else 0.0)
            dn = Denoiser(o["avg"]) if int(o.get("avg") or 1) > 1 else None
            while True:
                if self._client_gone(): break
                seq2, jpg = cam.wait_frame(seq, 1)
                if self._client_gone(): break
                if isinstance(cam, RemoteCam) and (not cam.present or publisher != cam.publisher): break
                if jpg is None: continue
                seq = seq2
                if dt and time.time() - last < dt: continue
                out = process(jpg, o, dn.add(jpg)) if dn else process(jpg, o)
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
    State.reg, State.token = Registry(cfg, a.test_source, getattr(a, "allow_remote_cameras", False)), token
    State.reg.refresh()
    srv = ThreadingHTTPServer((listen, port), SeeHandler); srv.daemon_threads = True
    _, gw_url, _ = ensure_gateway()      # bring up the web Gateway+MCP baseline if Key hasn't already
    return srv, listen, port, token, gw_url


def cmd_serve(a):
    srv, listen, port, token, gw_url = start_server(a)
    found = ", ".join(c.name for c in State.reg.cams.values()) or "none yet (plug one in; `doctor` explains)"
    print(f"mk cam on http://{listen}:{port}/{'?t=' + token if token else ''}   cameras: {found}", flush=True)
    if gw_url: print(f"web: {gw_url}", flush=True)
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
    srv, listen, port, token, gw_url = start_server(a)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"http://{'localhost' if listen in ('127.0.0.1', '0.0.0.0') else listen}:{port}/" + (f"?t={token}" if token else ""), flush=True)
    if gw_url: print(f"web: {gw_url}", flush=True)
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
    srv, listen, port, token, gw_url = start_server(a)
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
            lines = ["modkvm Cam", ""]
            sts = [c.status() for c in reg.cams.values()]
            if not sts: lines.append("○ no camera")
            for st in sts:
                dot, word = state_line(st)
                age = st.get("seconds_since_change")
                lines.append(f"{dot} {word:<8}{st['name']}" + (f"  {st['fps']:.0f} fps  change {st['change']}" + (f"  {age:.0f}s" if age is not None else "") if word == "live" else ""))
            lines += ["", url] + ([f"web: {gw_url}"] if gw_url else []) + ["", ("p pic  " if kind else "") + "q quit"]
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
    if not cam: sys.exit("no such camera (try: mk cam list)")
    o = parse_opts({"raw": ["1"]} if a.raw else {}, cam.defaults())
    cam.touch(); seq, jpg = cam.wait_frame(cam.seq, 12); cam.shutdown()
    if jpg is None: sys.exit("no frame: " + (cam.error or "timeout"))
    out = a.o or f"{cam.name}.jpg"
    with open(out, "wb") as f: f.write(process(jpg, o))
    print(out)


def cmd_list(a):
    cams = discover()
    if not cams: print("No capture devices found. Run: mk cam doctor"); return
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
        skeleton.append({"name": slug(c["product"] or c["label"]), "match": match, "size": "1920x1080", "fps": 10,
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
    print("mk cam doctor\n")
    pkgs = [p for p, have in (("ffmpeg", shutil.which("ffmpeg")), ("v4l-utils", shutil.which("v4l2-ctl"))) if not have]
    for mod, pkg in (("PIL", "python3-pil"), ("numpy", "python3-numpy")):
        try: __import__(mod)
        except ImportError: pkgs.append(pkg)
    line(not pkgs, "packages: " + (", ".join(pkgs) + " missing" if pkgs else "ffmpeg, v4l-utils, Pillow, numpy present"),
         f"sudo apt install -y {' '.join(pkgs)}   (or: mk cam doctor --fix)")
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
            tmp = Cam("probe", c["stable"], {"fps": 10, "size": "1920x1080"}, c)
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
                  " but two at high resolution at once may exceed bandwidth. Use size 640x480 in mkcam.json if so.")
    print(f"  [ info ] config: {SEE_CONFIG} ({'exists' if os.path.exists(SEE_CONFIG) else 'none yet; `probe` prints a skeleton'})")
    print("\n" + ("All good. Start it: mk cam serve" if not bad else
                  f"{bad} issue(s). Re-run with --fix for the ones apt/modprobe/usermod can solve."))


def cmd_install(a):
    install_launcher()


def ensure_key_service(force=False):
    """Install + start the always-on mkkey.service on first root run (no separate command needed).
    Key runs as root and auto-starts Cam/Core, so one unit brings up the whole stack."""
    if os.geteuid() != 0:
        return False
    if not shutil.which("systemctl"):
        return False
    me = os.path.abspath(__file__)
    unit_path = "/etc/systemd/system/mkkey.service"
    user = os.environ.get("SUDO_USER") or getpass.getuser()
    unit = ("[Unit]\n"
            "Description=modkvm Key (Bluetooth keyboard)\n"
            "After=bluetooth.target\n\n"
            "[Service]\n"
            "User=root\n"
            "Environment=SUDO_USER=" + user + "\n"
            "WorkingDirectory=" + os.path.dirname(me) + "\n"
            "ExecStart=" + sys.executable + " " + me + " kb\n"
            "Restart=always\n"
            "RestartSec=3\n\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n")
    try:
        existing = ""
        try:
            with open(unit_path) as f: existing = f.read()
        except OSError: pass
        if (not force) and existing and ("ExecStart=" + sys.executable + " " + me + " kb") in existing:
            return False
        with open(unit_path, "w") as f: f.write(unit)
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "enable", "--now", "mkkey"], check=True)
        try: install_launcher()
        except Exception: pass
        return True
    except Exception as e:
        try: print(f"mk: always-on service not installed: {e}", flush=True)
        except Exception: pass
        return False


def cmd_key_service(a=None):
    """Key as an always-on systemd unit. No TTY needed: main_kb() picks App.headless() when stdin isn't a TTY."""
    ok = ensure_key_service(force=True)
    print("modkvm Key service installed and started (mkkey.service; runs `mk kb` headless)" if ok else "mkkey service install failed")


def cmd_service(a):
    user = os.environ.get("SUDO_USER") or getpass.getuser()
    unit = f"""[Unit]
Description=modkvm Cam (webcam viewer)
After=network.target

[Service]
User={user}
SupplementaryGroups=video
ExecStart={sys.executable} {os.path.abspath(__file__)} see serve
Restart=on-failure

[Install]
WantedBy=multi-user.target
"""
    sudo = [] if os.geteuid() == 0 else ["sudo"]
    subprocess.run(sudo + ["tee", "/etc/systemd/system/mkcam.service"], input=unit, text=True, stdout=subprocess.DEVNULL, check=True)
    subprocess.run(sudo + ["systemctl", "daemon-reload"], check=True)
    subprocess.run(sudo + ["systemctl", "enable", "--now", "mkcam"], check=True)
    print("modkvm Cam service installed and started (mkcam.service; config in", SEE_CONFIG + ")")
    if os.path.exists("/etc/systemd/system/pbsee.service"):
        print("note: legacy pbsee.service exists; disable it: sudo systemctl disable --now pbsee")
    install_launcher()


SEE_DOC = """Cam (mode 'see', alias 'cam'): tiny multi-camera viewer for the modkvm server (a separate process from kb mode
on purpose: a camera crash/hang can't take down the keyboard). Primary command `mk cam ...`; legacy aliases pbs/pbc
run the same mode.

UVC webcams -> ffmpeg (MJPEG passthrough, no decode) -> optional light processing -> HTTP (live MJPEG, snapshots, JSON).
Cameras only run while somebody is watching, then stop after IDLE_STOP seconds (saves CPU and USB bandwidth).

  mk cam doctor [--fix]       check (and fix) packages, driver, group, devices, a test capture
  mk cam list                 cameras with stable paths, USB ids, formats
  mk cam probe                config skeleton for the cameras found (paste into mkcam.json)
  mk cam run                  server + inline status lines (live / idle / stopped)  [default for legacy `pbs`]
  mk cam screen               full-screen status view with the latest picture where the terminal supports it
  mk cam serve [--listen 0.0.0.0] [--port 8808] [--token T] [--test-source]
  mk cam snap NAME [-o out.jpg] [--raw]
  mk cam install              (re)creates mk + pb/pbk/pbkb/pbs/pbc aliases; same as `mk --install`
  mk cam install-service      optional: always-on systemd service (mkcam.service) instead of the terminal

Needs only: ffmpeg, python3-pil, python3-numpy (v4l-utils recommended). `doctor --fix` installs them.
"""


def main_see(args=None):
    ap = argparse.ArgumentParser(description=SEE_DOC, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("doctor"); d.add_argument("--fix", action="store_true"); d.set_defaults(fn=cmd_doctor)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    sub.add_parser("probe").set_defaults(fn=cmd_probe)
    s = sub.add_parser("serve"); s.add_argument("--listen"); s.add_argument("--port", type=int); s.add_argument("--token")
    s.add_argument("--test-source", action="store_true", help="add a synthetic camera (no hardware needed)"); s.set_defaults(fn=cmd_serve)
    s.add_argument("--allow-remote-cameras", action="store_true", help="accept voluntarily shared browser cameras through the existing gateway")
    r = sub.add_parser("run"); r.add_argument("--listen"); r.add_argument("--port", type=int); r.add_argument("--token")
    r.add_argument("--test-source", action="store_true"); r.set_defaults(fn=cmd_run)
    r.add_argument("--allow-remote-cameras", action="store_true")
    r = sub.add_parser("screen"); r.add_argument("--listen"); r.add_argument("--port", type=int); r.add_argument("--token")
    r.add_argument("--no-pic", action="store_true"); r.add_argument("--test-source", action="store_true"); r.set_defaults(fn=cmd_screen)
    r.add_argument("--allow-remote-cameras", action="store_true")
    n = sub.add_parser("snap"); n.add_argument("name", nargs="?"); n.add_argument("-o"); n.add_argument("--raw", action="store_true")
    n.add_argument("--test-source", action="store_true"); n.set_defaults(fn=cmd_snap)
    sub.add_parser("install").set_defaults(fn=cmd_install)
    sub.add_parser("install-service").set_defaults(fn=cmd_service)
    a = ap.parse_args(args)
    a.fn(a)


# ======================================================================
# MCP tool logic: phone_* tools over the Key/Cam HTTP APIs
# ======================================================================
DEFAULT_CAM = os.environ.get("MKCAM_CAM", os.environ.get("PBSEE_CAM", ""))


def http(url, data=None, headers=None, timeout=30):
    req = urllib.request.Request(url, data=None if data is None else json.dumps(data).encode(),
                                 headers=headers or {}, method="GET" if data is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        body = e.read()
        try: msg = json.loads(body).get("error", body.decode(errors="replace"))
        except Exception: msg = body.decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {msg}")
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"cannot reach {url}: {getattr(e, 'reason', e)}")


def kb(path, data=None):
    raw, _ = http(KB_URL + path, data if data is not None else ({} if path != "/status" else None),
                  {"Authorization": "Bearer " + kb_token(), "Content-Type": "application/json"})
    return json.loads(raw)


def cv_headers():
    return {"Authorization": "Bearer " + sb_token()} if sb_token() else {}


def cv_json(path):
    raw, _ = http(SB_URL + path, None, cv_headers())
    return json.loads(raw)


def linked_cam():
    """Camera linked (web console) to the active phone, else ''."""
    try:
        return load_cfg().get("links", {}).get(kb("/status").get("active"), "")
    except Exception:
        return ""


def pick_cam(name):
    if name or DEFAULT_CAM: return name or DEFAULT_CAM
    c = linked_cam()
    if c: return c
    cams = cv_json("/cams")
    cams = cams.get("cams", cams) if isinstance(cams, dict) else cams
    names = [c if isinstance(c, str) else c.get("name") for c in cams]
    if not names: raise RuntimeError("Cam reports no cameras")
    return names[0]


FR = {"type": "number", "minimum": 0, "maximum": 1}
TOOLS = [
    ("phone_status", "List paired phones, which is active/connected, and mouse state.", {}, []),
    ("phone_use", "Switch to a phone (number from phone_status, name fragment, or address). Dials it; no touching the phone.",
     {"device": {"type": ["string", "integer"]}}, ["device"]),
    ("phone_type", "Type text on the phone's focused field. submit=true presses Enter after. ASCII only.",
     {"text": {"type": "string"}, "submit": {"type": "boolean"}}, ["text"]),
    ("phone_key", "Press a key or combo, e.g. 'enter', 'esc', 'cmd+h' (iOS Home), 'cmd+space' (Spotlight), 'cmd+tab', 'ctrl+c'. "
                  "Pass 'keys' for a sequence.", {"key": {"type": "string"}, "keys": {"type": "array", "items": {"type": "string"}}}, []),
    ("phone_tap", "Click at a position given as fractions of the phone screen (x,y in 0..1; 0,0 = top-left). "
                  "Needs AssistiveTouch on the phone. Pointer position is estimated, so verify with phone_look.",
     {"x": FR, "y": FR, "button": {"enum": ["left", "right", "middle"]}, "double": {"type": "boolean"}}, ["x", "y"]),
    ("phone_scroll", "Scroll at an optional position.",
     {"dir": {"enum": ["up", "down"]}, "amount": {"type": "integer"}, "x": FR, "y": FR}, ["dir"]),
    ("phone_drag", "Press at (x1,y1), drag to (x2,y2), release. Fractions of the screen. Use for swipes.",
     {"x1": FR, "y1": FR, "x2": FR, "y2": FR, "duration": {"type": "number"}}, ["x1", "y1", "x2", "y2"]),
    ("phone_home_pointer", "Re-home the pointer to the top-left corner (fixes drift after many moves).", {}, []),
    ("phone_look", "Take a photo of the phone screen from the webcam (Cam; uses the camera linked to the active phone in the web console). Optionally wait `settle` seconds first so the "
                   "screen has updated. Returns the image plus the change score. Stills are averaged over several frames to cut sensor noise; add 'avg=1' to disable.",
     {"settle": {"type": "number"}, "camera": {"type": "string"}, "query": {"type": "string",
      "description": "extra Cam options, e.g. 'w=900&contrast'"}}, []),
]


def tool_call(name, a):
    if name == "phone_status":
        return [text(json.dumps(kb("/status"), indent=1))]
    if name == "phone_use": return [text(json.dumps(kb("/use", {"device": a["device"]})))]
    if name == "phone_type": return [text(json.dumps(kb("/type", {"text": a["text"], "submit": bool(a.get("submit"))})))]
    if name == "phone_key":
        if not (a.get("key") or a.get("keys")): raise RuntimeError("give key or keys")
        return [text(json.dumps(kb("/key", {"keys": a["keys"]} if a.get("keys") else {"key": a["key"]})))]
    if name == "phone_tap": return [text(json.dumps(kb("/click", a)))]
    if name == "phone_scroll": return [text(json.dumps(kb("/scroll", a)))]
    if name == "phone_drag": return [text(json.dumps(kb("/drag", a)))]
    if name == "phone_home_pointer": return [text(json.dumps(kb("/home", {})))]
    if name == "phone_look":
        if a.get("settle"): time.sleep(min(float(a["settle"]), 10))
        cam = pick_cam(a.get("camera"))
        q = ("?" + a["query"].lstrip("?")) if a.get("query") else ""
        img, ctype = http(f"{SB_URL}/snap/{urllib.parse.quote(cam)}.jpg{q}", None, cv_headers(), timeout=30)
        info = ""
        try: info = json.dumps(cv_json("/status"))[:600]
        except Exception: pass
        return [{"type": "image", "data": base64.b64encode(img).decode(), "mimeType": ctype or "image/jpeg"},
                text(f"camera={cam} {info}")]
    raise RuntimeError(f"unknown tool {name}")


def text(t): return {"type": "text", "text": t}


def handle(m):
    """One JSON-RPC message -> reply dict (None for notifications). Used by stdio and by the hub's HTTP /mcp."""
    method, i, p = m.get("method"), m.get("id"), m.get("params") or {}
    if i is None: return None                                    # notifications need no reply
    def ok(result): return {"jsonrpc": "2.0", "id": i, "result": result}
    if method == "initialize":
        return ok({"protocolVersion": p.get("protocolVersion", "2024-11-05"), "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": "modkvm", "version": "1.0"},
                   "instructions": "Drive an iPhone/iPad/computer over Bluetooth (keyboard+pointer) and look at it through a webcam. "
                                   "Loop: act (phone_type/key/tap) -> phone_look with settle=1 -> decide. "
                                   "The screen may show passwords; never type secrets you were not given."})
    if method == "ping": return ok({})
    if method == "tools/list":
        return ok({"tools": [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": pr, "required": req}}
                             for n, d, pr, req in TOOLS]})
    if method == "tools/call":
        try: return ok({"content": tool_call(p.get("name"), p.get("arguments") or {})})
        except Exception as e: return ok({"content": [text(str(e))], "isError": True})
    return {"jsonrpc": "2.0", "id": i, "error": {"code": -32601, "message": f"method not found: {method}"}}


def main_mcp():
    for line in sys.stdin:
        line = line.strip()
        if not line: continue
        try: m = json.loads(line)
        except ValueError: continue
        r = handle(m)
        if r is not None:
            sys.stdout.write(json.dumps(r) + "\n"); sys.stdout.flush()


# ======================================================================
# dispatch
# ======================================================================
def main():
    argv = sys.argv[1:]
    if argv in (["-h"], ["--help"], ["help"]):
        print(__doc__); return
    if "--install" in argv:
        return install_launcher()
    if argv and argv[0] == "install-service":           # no mode word: both units
        cmd_key_service()
        return cmd_service(None)
    mode, rest = "kb", argv
    if argv and argv[0] in ("kb", "key"): mode, rest = "kb", argv[1:]
    elif argv and argv[0] in ("see", "cam", "c"): mode, rest = "see", argv[1:]
    elif argv and argv[0] == "mcp": mode, rest = "mcp", argv[1:]
    elif argv and argv[0] == "web": mode, rest = "web", argv[1:]
    sys.argv = [sys.argv[0]] + rest
    {"kb": main_kb, "see": main_see, "mcp": main_mcp, "web": main_web}[mode]()


if __name__ == "__main__":
    main()
