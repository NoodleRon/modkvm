# Portabotty architecture

Portabotty is one bundled application — a single file, `portabotty.py` — with four components, started
as one of four modes (`portabotty.py <mode>`) and installed as five short commands by `--install`.

> The terminal UI (the `pb` menu and keys below) is bespoke and intentionally designed, not incidental.
> Don't change its behavior without a specific reason — ask first if a change isn't obviously required.

| component | mode word(s) | commands | runs as |
|---|---|---|---|
| **Key** | `kb` / `key` | `pb` / `pbk` / `pbkb` — the terminal + local API, and brings up the Gateway+MCP baseline and Cam too | root |
| **Cam** | `see` / `cam` / `c` | `pbs` / `pbc` — camera capture, and also brings up the Gateway+MCP baseline if Key isn't already running it | your user (`video` group) |
| **Gateway** | `web` | a baseline started by whichever of Key/Cam comes up first; rarely run standalone | — |
| **MCP** | `mcp` | stdio, via `claude mcp add`; the Gateway's `/mcp` reaches the same tool logic in-process | — |

## One app. One process, except the one place that needs two.

```
┌─ portabotty.py — one bundled app ────────────────────────────────────────┐
│                                                                          │
│  ┌─ Key (mode kb) ────────────────────────────────────────────────────┐  │
│  │ root, one OS process                                               │  │
│  │ raw-tty terminal — *is* the BT keyboard+mouse                      │  │
│  │ local API :8809                                                    │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                               same process                               │
│                                    ▼                                     │
│  ┌─ Gateway + MCP (mode web / mcp) ───────────────────────────────────┐  │
│  │ HTTP :8800: browser console, /mcp, proxies to :8809 / :8808        │  │
│  │ started by ensure_gateway() - whichever of Key/Cam comes up        │  │
│  │ first; the other finds it already listening and skips              │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│              separate process, started via ensure_gateway()              │
│                                    ▲                                     │
│  ┌─ Cam (mode see/cam/c) ─────────────────────────────────────────────┐  │
│  │ your user, SEPARATE OS process (video group, not root)             │  │
│  │ HTTP :8808, ffmpeg -> MJPEG per camera                             │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

Key, Gateway, and MCP are one process with no boundary between them — the Gateway's `/mcp` handler calls
the exact same tool-logic functions that mode `mcp` calls over stdio; no subprocess, no RPC. Cam is kept
as a **separate OS process** — still fully part of the one app, just not the one process — because a
camera/ffmpeg crash or hang must not take the keyboard down with it, and camera capture only needs the
`video` group, not root; folding it into Key's process would mean camera code running as root for no
benefit. That's the one place "separate" buys something real.

The Gateway isn't owned by Key specifically: `ensure_gateway(app=None)` is the shared function both
`main_kb()` and Cam's `start_server()` call. Whichever of Key or Cam starts first brings the Gateway up;
the other finds it already listening (`port_open()` distinguishes "already running, fine" from a genuine
bind failure) and skips starting a second one. Called from Key, `app` is also used to wire the camera
on/off switch and relay hosting through the Gateway; called from Cam with no `App` instance, it just
brings the Gateway itself up. So the web console + MCP work whether you run `pb`, or only ever run
`pbs`/`pbc` and never `pb` at all.

## Key (default: `pb` / `pbk` / `pbkb`) — `main_kb()`

The entry point that owns the process: sets up D-Bus, builds the `App`, starts the local API, the
Gateway (via `ensure_gateway()`), and (via the terminal) Cam.

**Bluetooth setup**, done once at startup:
- `reap_stale()` — kills any other running Key instance of this file (a stale one holds the BlueZ HID
  profile and blocks re-registering it). Checks `/proc/<pid>/cmdline` for `portabotty.py` + mode word
  `kb`, so it never touches a running Cam/MCP/Gateway process.
- `set_prop(bus, name, val)` — sets an adapter property (`Powered`, `Alias`, `Discoverable`, ...) via
  `org.freedesktop.DBus.Properties.Set`, passing `signature="ssv"` explicitly (the value isn't wrapped in
  a `dbus.Variant` — that class isn't present in every `python3-dbus` build; the signature kwarg gets the
  same marshalling without needing it).
- `Agent` (`dbus.service.Object`) — BlueZ pairing agent (`RequestPinCode` → `"0000"`,
  `RequestConfirmation` auto-accepts and calls `trust()`, `RequestPasskey`, etc.). Registered as the
  system's default agent.
- `Profile` (`dbus.service.Object`) — registers the HID service (`SDP_RECORD`, built from `KBD_DESC` +
  `MOUSE_DESC`, the raw HID report descriptors) under `HID_UUID` so paired phones see a keyboard+mouse.
- `trust(bus, path)` / `bonds(bus)` — mark a device trusted; list bonded (paired) devices from BlueZ's
  object tree.

**`HID`** — the actual Bluetooth transport, independent of D-Bus. Binds two L2CAP sockets (`PSM_CTRL`,
`PSM_INTR`), accepts inbound connections or dials out (`connect()`), and tracks one
`{"ctrl": sock, "intr": sock}` pair per bonded address in `self.conns`. `press(addr, mod, key)` and
`mouse(addr, buttons, dx, dy, wheel)` write raw HID report bytes to the `intr` socket. This class has no
idea what a menu or a terminal is — it only ever sees `(addr, bytes)`.

**`App`** — everything else: current active device, solo/keyboard/mouse-on-off state (persisted via
`load_state()`/`save_state()` to `.pbkey_state.json`), owns one `HID` instance.
- `console()` — the main loop when stdin is a tty. Puts the tty in raw mode (`tty_raw()`), reads bytes
  (`read()`), and on each chunk calls `handle()`.
- `handle(fd, data)` — splits input at the menu hotkey (`MENU_KEY`, Ctrl+]), runs the rest through
  `parse_input()` to get a list of events (`("key", mod, hidcode)`, `("mouse", buttons, x, y, pressed)`,
  `("mode", ...)`, `("menu",)`, `("skip",)`), then for each event calls `self.hid.press()` (keys) or
  accumulates/flushes mouse deltas into `send_mouse()`. A `("menu",)` event hands control to `menu()`.
- `menu()` / `getkey()` — reads one key after Ctrl+] and dispatches it: `1-9` → `use()`, `p` → `pair()`,
  `c` → `toggle_see()`, `h` → `calibrate()`, `n` → `rename_server()`, etc. — one method per menu key.
- `send_mouse()` / `goto()` / `home()` — relative-mouse HID reports, with `goto()` additionally tracking
  an *estimated* absolute position (`self.vpos`) so the web console's "map" mode and `calibrate()` can
  reason about where the pointer probably is.
- `calibrate()` / `fit_scale()` / `straighten()` — camera-assisted: homes the pointer, moves it to a
  known spot, calls `cal_snap()` to grab a frame from the linked camera, finds the pointer in the frame
  via `locate_pointer()` (pixel-diff blob detection), and adjusts `self.gx`/`self.gy` (the per-device
  mouse scale) until the pointer lands where it was aimed. Needs the camera's screen corners already
  marked (`opts["warp"]` set) — see the Cam section below. The web console also auto-triggers the
  corner-marking flow (`maybeAutoCal()`) the first time an uncalibrated camera is viewed.
- `PadTracker` / `PointerSource` / `open_pointer_devices()` / `classify()` — server-side trackpad/mouse
  support: `PointerSource` is a thread per evdev device, feeding raw events into a `PadTracker` (per
  device, turns absolute touchpad coordinates or relative mouse deltas into `emit(dx, dy, wheel, btn)`
  calls), wired to `App.pad_emit()` → `send_mouse()`.
- `start_see()` / `stop_see()` / `set_see(on)` / `toggle_see()` — Cam's lifecycle. `start_see()` calls
  `cam_autofix()` first (installs ffmpeg/v4l-utils/PIL/numpy, modprobe's `uvcvideo`, adds the user to
  `video` — all without an interactive sudo prompt, since Key is already root), then
  `subprocess.Popen([sys.executable, __file__, "see", "serve"], ...)`, run as `SUDO_USER` (not root) if
  Key itself is root. `check_see()` polls `see_state()` once a second and prints a line only when it
  changes (`live`/`idle`/`stopped`).
- `api(method, path, body)` — the logic behind the local HTTP API (below): `/status`, `/type`, `/key`,
  `/use`, `/click`, `/drag`, etc., each validating input and calling the same `HID`/`send_mouse`/`goto`
  methods the terminal uses. This is the single place both the terminal and every HTTP/MCP caller funnel
  through.

**Local HTTP API** — `make_api(app, token, host, port)` builds a `BaseHTTPRequestHandler` that checks a
bearer token and calls `app.api(method, path, body)` for every request. Listens on :8809 (`KB_PORT`),
token in `.pbkey_token` (`load_token()`).

**Startup sequence**: register D-Bus agent + HID profile → build `App` → start the local API
(`make_api`) → `ensure_gateway(app)` unless `--no-web` → run a `GLib.MainLoop()` (needed for the D-Bus
mainloop), with `App.console()` or `App.headless()` running in a background thread depending on whether
stdin is a tty.

## Gateway (mode `web`) — `main_web()` / started by `ensure_gateway()`

**`WebHandler`** (`BaseHTTPRequestHandler`) serves everything on :8800 (`DB_PORT`):
- `/` → the `CONSOLE` HTML/JS blob (the browser UI: device chips, live image, on-screen keyboard/
  trackpad, the Link sheet for wiring devices to cameras) — a static string constant, no templating.
- `/hub` → `hub_status()`: polls whether Key's API (:8809) and Cam's API (:8808) are up, camera
  live/idle, returns it as JSON for the console's poll loop.
- `/kb/<path>` → `_proxy()` forwards to `KB_URL` (:8809) with the kb token attached.
- `/cams`, `/stream/<name>`, `/snap/<name>`, `/calibrate/<name>`, etc. → `_proxy()` forwards to `SB_URL`
  (:8808) with the cam token attached. `_proxy()` streams multipart responses through unbuffered (for
  the MJPEG live view).
- `/mcp` → `_mcp()`: parses the JSON-RPC body and calls `handle()` (the MCP tool logic) directly — no
  subprocess, no extra HTTP hop, just a function call in the same process.
- `/links` → reads/writes the device↔camera links stored in `portabotty.json` (`load_cfg()`/`save_cfg()`).
- `/see`, `/viewonly` → toggle the camera (via the `SEE` dict: wired to `app.set_see` when Key started
  the Gateway; returns 501 "managed by PBKEY" if Cam started it alone, since there's no `App` to ask) and
  the console's view-only flag.

**`ensure_gateway(app=None)`** — the shared starter described above. **`relay_agent(url, agent_token,
port, local_token)`** — only runs if a relay is configured (via Key's `App.start_hosting()`). A
background thread that long-polls a PBRELAY server for queued browser/MCP requests and replays each one
against `http://127.0.0.1:{port}` (this same Gateway), streaming the response back to the relay.

## Cam (mode `see` / `cam` / `c`; commands `pbs` / `pbc`) — `main_see()`

The one separate OS process in the app (why: see above). Runs as a normal user (the `video` group), not
root. `start_server()` (shared by `serve`/`run`/`screen`) also calls `ensure_gateway()` after binding
Cam's own HTTP server, so the Gateway comes up here too if Key hasn't already started it.

**`Registry`** — `discover()` finds `/dev/video*` nodes via sysfs (no root needed), `match_entry()`
matches each one against `pbsee.json` entries (by USB vid:pid, name, serial, or stable path), and
`refresh()` keeps a `name -> Cam` dict current, creating/retiring `Cam` instances as cameras
appear/disappear.

**`Cam`** — one instance per camera. `_run()`/`_capture_once()` spawn `ffmpeg` (`BASE` + `_input()`,
MJPEG passthrough when the camera supports it, falling back to `-c:v mjpeg` re-encode otherwise) only
while `viewers > 0` (tracked via `acquire()`/`release()`), and stop it after `IDLE_STOP` seconds idle.
`_publish()` stores the latest JPEG frame and notifies any threads waiting in `wait_frame()`. `_sample()`
does cheap frame-diffing (`CHANGE_THRESHOLD`) to report a "has the screen changed" score. `focus(lock)`
drives `v4l2-ctl` to freeze autofocus once it's settled.

**`process(jpeg, o)`** — the image pipeline applied per-request (not baked into the stream): tilt
(straighten a crooked camera) → perspective warp or crop (from the 4 tapped corners, `persp_coeffs()`) →
rotate → contrast/gamma/sharpen → resize, all via PIL. `Denoiser` keeps a running mean of the last *n*
frames, restarting whenever the picture changes enough, used for snapshots and the optional live-view
"denoise" slider.

**`SeeHandler`** serves :8808 (`SB_PORT`): `/cams` (`Registry.describe()`), `/status`
(`Registry.status()`), `/stream/<name>` (MJPEG, calls `process()` per frame), `/snap/<name>` (one
processed frame, averaged by default), `/calibrate/<name>` (saves the 4 tapped corners as `opts["warp"]`
via `Registry.save_opts()`), `/save/<name>` (rotate/tilt/contrast/... settings), `/focus/<name>`.

**`cam_autofix()`** — installs ffmpeg/v4l-utils/PIL/numpy, modprobe's `uvcvideo`, adds the user to the
`video` group, as needed. Called from Key's `start_see()` before Cam's first start each session (no
interactive sudo prompt - Key's already root); also what `doctor --fix` does interactively.

**CLI subcommands** (`main_see()`'s argparse): `doctor [--fix]` runs the same checks as `cam_autofix()`
plus a real per-camera capture test, printed verbosely; `list`/`probe` for discovery; `serve`/`run`/
`screen` are the HTTP server with no/line/full-screen status output; `snap`/`install`/`install-service`.

## MCP — `main_mcp()`, and `WebHandler._mcp()`'s in-process call

**`TOOLS`** declares the schema for each `phone_*` tool. **`tool_call(name, args)`** is the dispatch
table: most tools call `kb(path, data)` (a plain HTTP POST/GET to `KB_URL`, i.e. Key's :8809 API —
`phone_tap` → `kb("/click", args)`, `phone_type` → `kb("/type", args)`, etc.); `phone_look` instead calls
`pick_cam()` (resolve which camera: explicit arg, else the one linked to the active device via
`linked_cam()`, else the first camera reported) and fetches a snapshot from `SB_URL` (:8808). **`handle(m)`**
wraps `tool_call()` in the JSON-RPC envelope MCP expects (`initialize`, `tools/list`, `tools/call`).

## Ports

| Port | Served by | Purpose |
|---|---|---|
| 8809 (`KB_PORT`) | Key's `make_api()` | device status, type/key/click/scroll, camera on/off |
| 8808 (`SB_PORT`) | Cam's `SeeHandler` | camera list, MJPEG stream, snapshot, calibration |
| 8800 (`DB_PORT`) | Gateway's `WebHandler` | browser console + `/mcp`, proxies to 8809/8808 |

A phone/tablet/computer never talks HTTP — it only sees the Bluetooth HID device Key's `HID` class
exposes via BlueZ. The HTTP ports are for *you* (browser, MCP agent, or the relay) to drive that device.

## Everything else in the repo

| File/dir | What it is |
|---|---|
| `relay.py` | PBRELAY: a separate, stdlib-only script run on a *different* machine (a small public VM) so the Gateway is reachable without a public IP. Queues browser/MCP requests; the Gateway's `relay_agent()` thread dials out and drains the queue. No dependency on `portabotty.py`. |
| `README.md` | User-facing docs: install, commands, web console, MCP tools |
| `VERSION` | Plain string, read at startup to print the version |
| `install-deps.sh` | `apt install` for the system packages (bluez, ffmpeg, etc.) |
| `skills/portabotty/SKILL.md` | Agent-facing instructions for Claude/Hermes on *how* to use the MCP tools |
| `.gitignore` | Local secrets/state the script writes next to itself (`.pbkey_token`, `pbsee.json`, `portabotty.json`, `.pbkey_state.json`) |
