#!/usr/bin/env python3
"""PBKEY (Portabotty key): use this server's terminal (and trackpad) as a Bluetooth keyboard + mouse for your devices.

Everything you type is transmitted live to the active phone (nothing is buffered or echoed).
Ctrl+]  opens the menu: 1-9 switch | p pair | s solo | m mouse (terminal) | t trackpad (server) | a map/relative |
        h re-home pointer | + - map size | d drop | f forget | q quit
Mouse (terminal window + server trackpad) is ON by default when detected.
Also serves a local HTTP API (127.0.0.1:8809, token file next to this script) and, with portabotty.py alongside, the web
console + MCP (http://localhost:8800). See (webcam) shows as an indicator line; c starts/stops it, w prints the web link.
Flags: --no-mouse (keyboard-only descriptor; re-pair after changing) | --no-pad | --no-term-mouse | --no-api | --no-web
       --api-port N | --api-listen ADDR | --web-port N | --web-listen ADDR | --web-token T | --relay URL --relay-token T
       --pad (use the server's own trackpad/mouse even over SSH) | --pads (list input devices + live event test)
       --install (creates pb, pbk, pbkb: work in any shell)
"""
import os, re, sys, json, time, socket, struct, select, termios, atexit, subprocess, threading, signal, logging
import fcntl, hmac, secrets, stat, shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import portabotty as web                  # web console + MCP gateway (optional file next to this one)
except Exception:
    web = None
import dbus, dbus.service, dbus.mainloop.glib
from gi.repository import GLib

logging.basicConfig(level=logging.INFO if ("-v" in sys.argv or "--verbose" in sys.argv) else logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

def _version():
    try: return open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")).read().strip()
    except OSError: return "0.0.00001"
VERSION = _version()
SEE_DOT = {"live": "● live", "idle": "◐ on, idle", "stopped": "○ off"}
HID_UUID = "00001124-0000-1000-8000-00805f9b34fb"
PSM_CTRL, PSM_INTR = 0x11, 0x13
ADAPTER = "/org/bluez/hci0"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pbkey_state.json")
MENU_KEY = 0x1D            # Ctrl+]  (change here if you want another menu hotkey)

# ---- tuning knobs
CELL_W, CELL_H = 9, 18     # mouse counts per terminal cell moved (terminals without pixel reporting)
PAD_GAIN = 1200            # mouse counts for a full trackpad-width swipe
PAD_SCROLL = 24            # wheel clicks for a full trackpad-height two-finger swipe
PAD_NATURAL = True         # two-finger scroll direction (flip if it feels backwards)
TAP_SECONDS = 0.25         # a touch shorter than this, with little movement, is a tap-click
MAP_W, MAP_H = 1500, 2800  # mouse counts that span the phone screen (iOS pointer speed varies: tune with Ctrl+] + / -)
PAD_RESCAN = 3.0           # seconds between looks for a newly plugged trackpad/mouse
API_PORT = 8809

MOUSE = "--no-mouse" not in sys.argv
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pbkey_token")


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
 <attribute id="0x0100"><text value="Portabotty Keyboard"/></attribute>
 <attribute id="0x0101"><text value="Keyboard"/></attribute>
 <attribute id="0x0102"><text value="Portabotty"/></attribute>
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
    """sudo python3 pbkey.py --pads : every input device, how it is classified, and live event counts."""
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
    header_off()
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

_HDR = {"on": False, "redraw": False, "web": None, "api": None, "host": None, "n": 1}


def hyperlink(url, text=None):
    """Clickable link (OSC 8); terminals without support just show the text."""
    return f"\x1b]8;;{url}\x1b\\{text or url}\x1b]8;;\x1b\\"


def header_text(cols):
    text = f"Portabotty {VERSION}: Ctrl+] for menu, then press [key]"
    return text if len(text) <= cols else text[:cols - 1] + "…"


def header_lines(cols):
    """[(visible text, text to print)] for each pinned row: the title, then the web link."""
    t = header_text(cols)
    out = [(t, f"\x1b[7m{t.ljust(cols)}\x1b[0m")]
    web = _HDR["web"]() if _HDR["web"] else None
    if web:
        link, tail = web
        host = _HDR["host"]() if _HDR["host"] else None
        if host: tail += f" | upgrade [u] hosting {host}"
        room = cols - len("Web: ")
        if len(link) > room:
            link, tail = "", (link + tail)
        tail = tail if len(link) + len(tail) <= room else tail[:max(0, room - len(link) - 1)] + "…"
        vis = f"Web: {link}{tail}"
        out.append((vis, f"Web: {hyperlink(link) if link else ''}{tail}"))
    api = _HDR["api"]() if _HDR["api"] else None
    if api:
        api = api if len(api) <= cols else api[:cols - 1] + "…"
        out.append((api, api))
    return out


def header_draw(cols, rows):
    """Pinned top lines; everything else scrolls underneath them."""
    lines = header_lines(cols)
    _HDR["n"] = len(lines)
    raw_out("\x1b7" + "".join(f"\x1b[{i};1H\x1b[2K{ren}" for i, (vis, ren) in enumerate(lines, 1))
            + f"\x1b[{len(lines) + 1};{rows}r\x1b8")


def cursor_row(fd, timeout=0.3):
    """Ask the terminal where the cursor is (1-based row), or None."""
    try:
        old = termios.tcgetattr(fd)
        raw = termios.tcgetattr(fd)
        raw[3] &= ~(termios.ECHO | termios.ICANON)
        termios.tcsetattr(fd, termios.TCSADRAIN, raw)
    except Exception:
        return None
    try:
        raw_out("\x1b[6n")
        buf, end = b"", time.time() + timeout
        while time.time() < end and not buf.endswith(b"R"):
            if select.select([fd], [], [], max(0, end - time.time()))[0]:
                buf += os.read(fd, 32)
        m = re.search(rb"\x1b\[(\d+);\d+R", buf)
        return int(m.group(1)) if m else None
    except Exception:
        return None
    finally:
        try: termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception: pass


def header_on(cols, rows, row=None):
    """Pin the top lines without wiping the screen: push lines into scrollback, open rows 1..n."""
    _HDR["on"] = True
    n = len(header_lines(cols))
    raw_out(f"\x1b[{rows};1H" + "\n" * n + "\x1b[1;1H" + "\x1b[L" * n)
    header_draw(cols, rows)
    raw_out(f"\x1b[{max(n + 1, row or n + 1)};1H")


def header_off():
    if _HDR["on"]:
        _HDR["on"] = False
        raw_out("\x1b[r\x1b[999;1H\n")


atexit.register(tty_restore)


# ---------------------------------------------------------------- state / bluez helpers
def load_state():
    try:
        with open(STATE_FILE) as f: return json.load(f)
    except Exception: return {}

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
            .Set("org.bluez.Device1", "Trusted", dbus.Boolean(True))
    except Exception as e:
        logging.warning(f"trust failed: {e}")

def bonds(bus):
    out = []
    om = dbus.Interface(bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
    for path, ifs in om.GetManagedObjects().items():
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
        .Set("org.bluez.Adapter1", name, val)


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
        self.see_proc = None                   # PBSEE child started by Key (on by default, remembered)
        self.want_see = bool(st.get("see", True))
        self.server_name = st.get("name") or f"Portabotty {socket.gethostname()}"
        self.see_last = None
        self.last_see_check = 0.0
        self.last_scan = 0.0
        self.term_map = st.get("term_map", True)      # terminal window <-> phone screen (absolute-feel)
        self.map_gain = float(st.get("map_gain", 1.0))
        self.vpos = None                       # where we believe the phone's pointer is (counts); None = unknown
        self.restart = False
        self.api_info = ""; self.web_port = 0; self.web_tok = ""; self.relay = None
        self.kbd = bool(st.get("keyboard", True))
        self.api_lock = threading.RLock()
        self.hid = HID(self.on_change)

    # ---------- helpers ----------
    def name_of(self, addr):
        try:
            for b in bonds(self.bus):
                if b["addr"] == addr: return b["name"]
        except Exception: pass
        return addr

    def status(self, b):
        if self.hid.is_up(b["addr"]): return "● connected"
        if b["link"]: return "◐ linked (no HID)"
        return "○ offline"

    def show(self):
        bl = bonds(self.bus)
        if not bl:
            say("No bonds.")
            return bl
        say(f"\n  {'[1-9]':<6}{'Device':<26}{'Address':<19}Status")
        for i, b in enumerate(bl, 1):
            mark = "*" if b["addr"] == self.active else " "
            say(f"{mark} {i:<5}{b['name'][:24]:<26}{b['addr']:<19}{self.status(b)}")
        ptr = (f"  mouse [m] {(('map' if self.term_map else 'relative') + ' [a]') if self.mouse_term else 'off'}"
               f" | trackpad [t] {'ON' if self.pads else 'off'} | home [h] | size [+/-]") if MOUSE else "  no mouse"
        say(f"\n  name [n] {self.server_name} | cam [c] {SEE_DOT[self.see_now()]}"
            f" | keyboard [k] {'ON' if self.kbd else 'OFF'} | solo [s] {'ON' if self.solo else 'OFF'}")
        say(ptr)
        say("  pair [p] | drop [d] | forget [f] | restart [r] | quit [q]\n")
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
        self.active = addr
        save_state(active=addr)

    def bt_disconnect(self, addr):
        try:
            dbus.Interface(self.bus.get_object("org.bluez", dev_path(addr)), "org.bluez.Device1").Disconnect()
        except Exception: pass

    # ---------- events ----------
    def on_change(self, addr, up):
        self.vpos = None                       # pointer position on the phone is unknown after any (re)connect
        say(f"{'●' if up else '○'} {self.name_of(addr)} {'connected' if up else 'disconnected'}")
        if up and (not self.active or not self.hid.is_up(self.active)):
            self.set_active(addr)

    # ---------- actions ----------
    def use(self, b):
        self.set_active(b["addr"])
        if self.solo:
            for a in list(self.hid.conns):
                if a != b["addr"]:
                    self.hid.drop(a); self.bt_disconnect(a)
        if self.hid.is_up(b["addr"]):
            return
        say(f"Connecting to {b['name']}...")
        ok, err = self.hid.connect(b["addr"])
        if not ok:
            say(f"Failed: {err}")

    def drop(self, b):
        was = self.hid.drop(b["addr"]); self.bt_disconnect(b["addr"])
        say(f"Disconnected {b['name']} (bond kept)." if was else f"{b['name']} wasn't connected.")

    def forget(self, b, fd):
        say(f"Delete bond for {b['name']}? Press y to confirm: ", end="")
        k = self.getkey(fd); say()
        if k != "y":
            say("Cancelled."); return
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
        say("Keyboard ON" if self.kbd else "Keyboard OFF")

    def toggle_solo(self):
        self.solo = not self.solo; save_state(solo=self.solo)
        say("Solo ON" if self.solo else "Solo OFF")

    # ---------- mouse ----------
    def set_term_mouse(self, on, quiet=False):
        if on == self.mouse_term:
            return
        self.mouse_term = on
        self.mpos = None
        if on:
            self.mscale = (CELL_W, CELL_H)
            _MOUSE_REPORTING["on"] = True
            raw_out("\x1b[?1016$p\x1b[?1003h\x1b[?1006h")     # ask about pixel mode, enable any-motion + SGR
            if not quiet:
                say("Mouse ON")
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
            self.pad_note("Trackpad ON", True)
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
        raw_out("\x1b[?1006$p")
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
        self.send_mouse(int(-MAP_W * 1.3 * self.map_gain), int(-MAP_H * 1.3 * self.map_gain), 0, self.mbtn)
        self.vpos = [0.0, 0.0]
        return True

    def goto(self, fx, fy, buttons=None):
        """Move the phone pointer to a fraction (0..1, 0..1) of its screen. Edges overshoot to self-correct drift."""
        b = self.mbtn if buttons is None else buttons
        if self.vpos is None and not self.home(): return False
        W, H = MAP_W * self.map_gain, MAP_H * self.map_gain
        tx = -0.25 * W if fx <= 0.01 else 1.25 * W if fx >= 0.99 else fx * W
        ty = -0.25 * H if fy <= 0.01 else 1.25 * H if fy >= 0.99 else fy * H
        dx, dy = int(round(tx - self.vpos[0])), int(round(ty - self.vpos[1]))
        self.vpos = [min(max(tx, 0.0), W), min(max(ty, 0.0), H)]
        if dx or dy or b != self.mbtn:
            return self.send_mouse(dx, dy, 0, b)
        return True

    def term_fraction(self, x, y):
        cols, rows, xp, yp = self.term_size()
        top = 1 if _HDR["on"] else 0                      # the pinned header row is not part of the map
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

    def start_hosting(self, url, token):
        """Dial out to a relay (no inbound port): remembered in portabotty.json. Empty URL turns it off."""
        if self.relay: self.relay.stop(); self.relay = None
        cfg = web.load_cfg()
        if url and token and web and self.web_port:
            self.relay = web.relay_agent(url, token, self.web_port, self.web_tok)
            cfg["relay"], cfg["relay_token"] = url, token
        else:
            cfg.pop("relay", None); cfg.pop("relay_token", None)
        if web: web.save_cfg(cfg)
        return self.hosting_text()

    def hosting(self, fd):
        if not web or not self.web_port:
            say("Hosting needs the web console"); return
        cfg = web.load_cfg()
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
        return web.see_state() if web else "stopped"

    def check_see(self):
        """Inline indicator: prints a line only when PBSEE changes state (live / idle / stopped)."""
        st = self.see_now()
        if self.see_last is not None and st != self.see_last:
            say(f"cam {SEE_DOT[st]}")
        self.see_last = st

    def see_running(self):
        return bool(self.see_proc and self.see_proc.poll() is None)

    def start_see(self, quiet=False):
        if self.see_running() or (web and web.port_open(web.SB_URL)):
            return True                                                # already up (here or in another terminal)
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pbsee.py")
        if not os.path.exists(script):
            quiet or say("pbsee.py not found"); return False
        cmd = [sys.executable, script, "serve"]
        if os.geteuid() == 0 and os.environ.get("SUDO_USER"):
            cmd = ["sudo", "-u", os.environ["SUDO_USER"]] + cmd       # the camera needs the video group, not root
        self.see_proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, start_new_session=True)
        quiet or say("See starting")
        return True

    def stop_see(self, quiet=False):
        if self.see_running():
            self.see_proc.terminate()
            try: self.see_proc.wait(3)
            except Exception: pass
        self.see_proc = None
        quiet or say("See off")

    def set_see(self, on, quiet=True):
        """Remembered across runs (default on). Returns the new wanted state."""
        self.want_see = bool(on); save_state(see=self.want_see)
        if self.want_see: self.start_see(quiet)
        else:
            if web and web.port_open(web.SB_URL) and not self.see_running():
                quiet or say("See is running in another terminal")
            self.stop_see(quiet)
        return self.want_see

    def toggle_see(self):
        self.set_see(not (self.want_see and (self.see_running() or (web and web.port_open(web.SB_URL)))), quiet=False)

    def shutdown(self, bye="Disconnected from all devices. Bye."):
        self.stop_see(quiet=True)
        self.set_term_mouse(False, quiet=True)
        self.stop_pads()
        for addr in list(self.hid.conns):
            self.hid.drop(addr); self.bt_disconnect(addr)
        say(bye)
        self.stop.set()

    def ensure_target(self):
        if not self.active:
            self.hint("No target. Ctrl+] then a number picks one.")
            return False
        if self.hid.is_up(self.active):
            return True
        now = time.time()
        if now - self.last_try < 10:
            self.hint(f"{self.name_of(self.active)} is offline. Ctrl+] for the menu.")
            return False
        self.last_try = now
        say(f"Reconnecting to {self.name_of(self.active)}...")
        ok, err = self.hid.connect(self.active, timeout=5)
        if not ok:
            say(f"Not reachable ({err}).")
        return ok

    def auto_reconnect(self):
        if self.hid.is_up(self.active): return
        say(f"Auto-reconnecting to {self.name_of(self.active)}...")
        ok, err = self.hid.connect(self.active, timeout=8)
        if not ok:
            say(f"  not reachable ({err})")

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
                "terminal_mouse": self.mouse_term, "terminal_map": self.term_map, "trackpad": bool(self.pads),
                "pointer": None if self.vpos is None else
                [round(self.vpos[0] / (MAP_W * self.map_gain), 3), round(self.vpos[1] / (MAP_H * self.map_gain), 3)]}

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
                self.vpos = [min(max(self.vpos[0] + dx, 0.0), MAP_W * self.map_gain),
                             min(max(self.vpos[1] + dy, 0.0), MAP_H * self.map_gain)]
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
            say("Pointer re-homed." if self.home() else "No connected phone.")
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
        _HDR["web"] = self.web_header; _HDR["api"] = lambda: self.api_info; _HDR["host"] = self.hosting_text
        self.host_last = None
        header_on(cols, rows, cursor_row(fd))
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
                        say("Mouse ON")
                    if typed: self.handle(fd, typed)                  # keys typed while we were asking
                if self.pads_auto: self.scan_pads()
            while not self.stop.is_set():
                data = self.read(fd)
                if data is None: break
                if data: self.handle(fd, data)
                if _HDR["redraw"]:
                    _HDR["redraw"] = False; header_draw(*self.term_size()[:2])
                if self.pads_auto and time.time() - self.last_scan > PAD_RESCAN:
                    self.last_scan = time.time(); self.scan_pads()
                if time.time() - self.last_see_check > 2:
                    self.last_see_check = time.time(); self.check_see()
                    ht = self.hosting_text()
                    if ht != self.host_last: self.host_last = ht; _HDR["redraw"] = True
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
    try:
        return open(TOKEN_FILE).read().strip()
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
    class H(BaseHTTPRequestHandler):
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


def install_launcher():
    me = os.path.abspath(__file__)
    script = ('#!/bin/sh\n# PBKEY launcher (created by pbkey.py --install). Works in any shell, local or SSH.\n'
              f'sudo pkill -f "[p]bkey\\.py" 2>/dev/null; sleep 1\nexec sudo python3 "{me}" "$@"\n')
    try:
        for name in ("pb", "pbk", "pbkb"):
            with open(f"/usr/local/bin/{name}", "w") as f: f.write(script)
            os.chmod(f"/usr/local/bin/{name}", 0o755)
    except PermissionError:
        sys.exit(f"Run:  sudo python3 {me} --install")
    print("Installed pb, pbk, pbkb (run from any shell, console or SSH).")


def main():
    if "--install" in sys.argv:
        return install_launcher()
    if "--pads" in sys.argv:
        return pad_diagnose()
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    dbus.mainloop.glib.threads_init()
    bus = dbus.SystemBus()
    name = load_state().get("name") or f"Portabotty {socket.gethostname()}"

    r = subprocess.run(["btmgmt", "class", "5", str(DEV_SUBCLASS)], capture_output=True, text=True)
    logging.info(f"btmgmt class: {(r.stdout or r.stderr).strip()[:120]}")

    have_bonds = bool(bonds(bus))
    for k, v in (("Powered", dbus.Boolean(True)), ("Alias", dbus.String(name)),
                 ("Pairable", dbus.Boolean(True)), ("PairableTimeout", dbus.UInt32(0)),
                 ("DiscoverableTimeout", dbus.UInt32(0 if not have_bonds else 120)),
                 ("Discoverable", dbus.Boolean(not have_bonds))):
        set_prop(bus, k, v)

    agent_manager = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"), "org.bluez.AgentManager1")
    agent = Agent(bus, "/pbkey/agent")
    agent_manager.RegisterAgent("/pbkey/agent", "NoInputNoOutput")
    agent_manager.RequestDefaultAgent("/pbkey/agent")

    pm = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"), "org.bluez.ProfileManager1")
    prof = Profile(bus, "/pbkey/hid")
    pm.RegisterProfile("/pbkey/hid", HID_UUID, {
        "Name": "Portabotty Keyboard", "Role": "server",
        "RequireAuthentication": dbus.Boolean(False), "RequireAuthorization": dbus.Boolean(False),
        "ServiceRecord": SDP_RECORD})

    app = App(bus)
    app.server_name = name
    api_srv = web_srv = None
    if "--no-api" not in sys.argv:
        host, port = argval("--api-listen", "127.0.0.1"), int(argval("--api-port", API_PORT))
        tok = load_token()
        try:
            api_srv = make_api(app, tok, host, port)
            app.api_info = f"API: http://{host}:{port} | Token file: {TOKEN_FILE}"
            logging.info(app.api_info)
        except OSError as e:
            logging.warning(f"API not started: {e}")
        if "--no-web" in sys.argv: pass
        elif not web: app.web_url = "off (portabotty.py not found next to pbkey.py)"
        elif not api_srv: app.web_url = "off (key API busy: another pbk running? sudo pkill -f '[p]bkey\\.py')"
        else:
            wl, wp = argval("--web-listen", "0.0.0.0"), int(argval("--web-port", web.DB_PORT))
            os.environ["PBKEY_TOKEN"] = tok; web.KB_URL = f"http://127.0.0.1:{port}"
            try:
                wtok = web.web_token(wl, argval("--web-token", ""))
                web_srv = web.start_gateway(wl, wp, wtok)
                app.web_link = app.web_url = web.web_url(wl, wp, wtok)
                web.SEE["state"] = lambda: app.want_see
                web.SEE["set"] = app.set_see
                fw = firewall_hint(wp) if wl == "0.0.0.0" else ""
                if fw: app.web_url += f"   (ufw blocks LAN: {fw})"
                cfg = web.load_cfg()
                ru, rt = argval("--relay", cfg.get("relay", "")), argval("--relay-token", cfg.get("relay_token", ""))
                app.web_port, app.web_tok = wp, wtok
                if ru and rt: app.start_hosting(ru.rstrip("/"), rt)
            except OSError as e:
                app.web_url = f"off ({e.strerror or e}: port {wp} busy? sudo pkill -f '[p]bkey\\.py')"
    loop = GLib.MainLoop()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *a: app.stop.set())
    signal.signal(signal.SIGWINCH, lambda *a: _HDR.__setitem__("redraw", True))
    GLib.timeout_add(200, lambda: (loop.quit(), False)[1] if app.stop.is_set() else True)
    target = app.console if sys.stdin.isatty() else app.headless
    threading.Thread(target=target, daemon=True).start()
    try:
        loop.run()
    finally:
        tty_restore()
        app.stop_pads()
        app.stop_see(quiet=True)
        if api_srv: api_srv.shutdown()
        if web_srv: web_srv.shutdown()
        for a in list(app.hid.conns): app.hid.drop(a)
        for s in app.hid.servers: s.close()
        try: pm.UnregisterProfile("/pbkey/hid")
        except Exception: pass
        try: agent_manager.UnregisterAgent("/pbkey/agent")
        except Exception: pass
        if app.restart:
            try: subprocess.run(["systemctl", "restart", "bluetooth"], timeout=30, capture_output=True)
            except Exception: pass
            time.sleep(2)
            os.execv(sys.executable, [sys.executable] + sys.argv)

if __name__ == "__main__":
    main()
