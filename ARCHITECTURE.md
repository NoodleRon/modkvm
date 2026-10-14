# Portabotty architecture

## The actual design: the `pb` terminal

Start with what was deliberately designed, not what's biggest in the diff. Portabotty began as a single
script (`pbkey.py`) whose whole job was: *be a Bluetooth keyboard+mouse, controlled from one terminal
session*. That terminal — the `pb` command, its status block, and the `Ctrl+]` menu — is the part that
was iterated on as a piece of UX, not just grown to pass a test. Everything else (the web console, the
camera, MCP) was built *around* that terminal afterward, as ways to reach the same device state from
somewhere other than the terminal in front of it.

So read the menu as the spec:

```
Portabotty 0.0.00001: Ctrl+] for menu, then press [key]
Web: <link>  |  hosted locally | [u] get paid hosting (coming soon)
API: http://127.0.0.1:8809 | Token file: .pbkey_token
  [1-9]  Device                   Address            Status
* 1      Ismail's iPhone          AA:BB:CC:DD:EE:FF   ● connected

  cam [c] ● live | calibrate [h]
  keyboard [k] on | name [n] BTE Keyboard | solo [s] on
  mouse [m] map [a] | trackpad [t] on
  pair [p] | drop [d] | forget [f]
  restart [r] | quit [q]
```

Nothing is pinned or redrawn — it's a normal scrolling terminal program. `Ctrl+]` + a key does the thing
*and* prints only what changed (`Mouse off`, `Solo on`); `Ctrl+]` + Enter reprints the whole block. That
economy — one keystroke to open a menu that never gets in the way of the typing it's layered on top of —
is the thing to preserve if you're changing this code. The HID/D-Bus plumbing underneath is substantial,
but it exists to make that terminal's promise true (every keystroke goes live to the phone), not the
other way around.

Everything below this point — one file, four modes, three local ports — is the "grown around it" layer:
functional, but built more opportunistically than the terminal was.

## One file, four modes

As of this merge, `pbkey.py` + `portabotty.py` + `pbsee.py` + `pbmcp.py` are one file: `portabotty.py`.
They used to find each other at runtime (`pbkey.py` doing `import portabotty as web` as an *optional*
sibling file, `portabotty.py` doing `import pbmcp as mcp` per MCP request) — four files that only really
made sense installed together, discovering each other by filename. That implicit coupling is now an
explicit first argument:

```
portabotty.py            kb mode (default) — the terminal above. Runs as root.
portabotty.py see ARGS   see mode  — camera server (doctor/list/probe/serve/run/screen/snap/install...)
portabotty.py mcp        mcp mode  — stdio MCP server
portabotty.py web ARGS   web mode  — the web console + MCP-over-HTTP gateway, standalone
```

`--install` (any mode) writes five launcher commands in `/usr/local/bin`, all pointed at this one file:

| command | mode | runs as |
|---|---|---|
| `pb` / `pbk` / `pbkb` | `kb` | root (re-execs via `sudo`) |
| `pbs` / `pbc` | `see` | your normal user (`video` group) |

```
┌──────────────────────────────────────────────────────────────────────┐
│  main_kb()   — the terminal above + BlueZ/D-Bus HID profile            │
│  ─────────────────────────────────────────────────────────────────    │
│  • raw-tty terminal UI = the actual Bluetooth keyboard+mouse device    │
│  • local HTTP API on :8809 (token in .pbkey_token)                     │
│  • calls start_gateway() directly, in a thread → :8800 (web+MCP)       │
│  • spawns `python3 portabotty.py see serve` as a SEPARATE PROCESS      │
└───────────────┬──────────────────────────────┬─────────────────────────┘
                │ same process, same call        │ subprocess.Popen
                ▼                                 ▼
┌───────────────────────────────┐   ┌─────────────────────────────────┐
│ main_web() / WebHandler         │   │ main_see() / SeeHandler          │
│ ─────────────────────────────  │   │ ───────────────────────────────  │
│ • HTTP on :8800: browser console│  │ • HTTP on :8808                  │
│ • /mcp: calls handle() directly │   │ • ffmpeg → MJPEG per camera      │
│ • proxies kb/* → :8809,          │   │ • idles a camera when unwatched │
│   sb/* → :8808                  │   └───────────────────────────────┘
└───────────────┬─────────────────┘
                │ same process
                ▼
┌───────────────────────────────┐
│ handle() / tool_call()  — MCP   │   phone_status/_use/_type/_key/_tap/
│ tool logic, used two ways:      │   _scroll/_drag/_home_pointer/_look,
│  1. main_mcp(): stdio loop      │   all implemented as plain HTTP calls
│  2. called in-process by        │   to :8809 and :8808 — even when MCP
│     WebHandler's /mcp            │   is reached via stdio, same file,
└───────────────────────────────┘   same process.
```

Why `see` mode stays a separate **process** rather than folding in completely: a camera/ffmpeg crash or
hang must not take down the keyboard, and it needs to run as your normal user (`video` group) rather
than root. That isolation was a deliberate property of the old `pbsee.py` split and the merge keeps it —
only the *file* boundary disappeared, not the *process* boundary. `kb`, `mcp`, and `web` modes, by
contrast, had no such reason to be separate processes; they were separate *files* only because that's
how the project grew, one capability at a time, each new piece finding the others by importing a
sibling script. Collapsing those three into one file's call graph removes a layer of indirection without
giving up anything that mattered.

`relay.py` (PBRELAY) stays a fully separate script on purpose: it's meant to run on a *different machine
entirely* (a small public VM, reached when your server has no public IP), stdlib-only, with zero
dependency on the rest of the code. There's no reason for that remote box to carry Bluetooth/dbus/camera
code it will never execute.

## Runtime picture (ports)

| Port | Served by | Purpose |
|---|---|---|
| 8809 | `kb` mode | local API: device status, type/key/click/scroll, camera on/off |
| 8808 | `see` mode | local API: camera list, MJPEG stream, snapshot, calibration |
| 8800 | `web` mode (started in-process by `kb`) | public-facing: browser console + `/mcp`, proxies to 8809/8808 |

A phone/tablet/computer itself never talks HTTP — it only sees the Bluetooth HID device `kb` mode
exposes via BlueZ. The HTTP ports are for *you* (browser, MCP agent, or the relay) to drive that device.

## Everything else in the repo

| File/dir | What it is |
|---|---|
| `README.md` | User-facing docs: install, commands, web console, MCP tools |
| `VERSION` | Plain string, read at startup to print the version |
| `install-deps.sh` | One-line `apt install` for the system packages (bluez, ffmpeg, etc.) |
| `skills/portabotty/SKILL.md` | Agent-facing instructions for Claude/Hermes on *how* to use the MCP tools (when to call `phone_look`, pitfalls like ASCII-only text entry) — not code, just a prompt |
| `.gitignore` | Excludes the local secrets/state files written next to the script (`.pbkey_token`, `pbsee.json`, `portabotty.json`, `.pbkey_state.json`) |

28 unused pip wheels (`mcp-2.3.0`, `pydantic`, `starlette`, `uvicorn`, `cryptography`, etc.) were
previously committed at the repo root — a `pip download` artifact nothing in the code referenced. Removed.
