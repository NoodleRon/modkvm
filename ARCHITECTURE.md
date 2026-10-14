# Portabotty architecture

Portabotty turns a Linux server into a Bluetooth keyboard/mouse for other devices, with a webcam view of
their screens. Despite the file count, there are really only **3 independent programs** plus two thin
adapters. Nothing here uses a framework or a build step — every `.py` file is run directly with `python3`.

## The 3 programs

```
┌─────────────────────────────────────────────────────────────────────┐
│  pbkey.py  (PBKEY)            — runs as root, owns the terminal      │
│  ──────────────────────────────────────────────────────────────     │
│  • Terminal UI (raw tty) + BlueZ/D-Bus HID profile                   │
│    → this is the actual Bluetooth keyboard+mouse device              │
│  • Local HTTP API on :8809  (kb_token in .pbkey_token)               │
│  • Imports portabotty.py IN-PROCESS (not a subprocess) and starts    │
│    its web server on :8800, in a thread                              │
│  • Spawns pbsee.py as a SUBPROCESS when the camera is wanted         │
└───────────────────────┬───────────────────────────┬──────────────────┘
                         │ in-process import          │ subprocess.Popen
                         ▼                             ▼
┌────────────────────────────────────┐   ┌─────────────────────────────┐
│ portabotty.py  — the web gateway    │   │ pbsee.py  (PBSEE)           │
│ ────────────────────────────────── │   │ ─────────────────────────── │
│ • HTTP server on :8800              │   │ • own HTTP API on :8808     │
│ • Serves the browser web console    │   │ • ffmpeg → MJPEG per camera │
│ • /mcp endpoint: MCP-over-HTTP      │   │ • idles cameras when no one │
│ • Proxies kb/* → :8809, sb/* → :8808│   │   is watching                │
└───────────────────┬─────────────────┘   └─────────────────────────────┘
                     │ imports, in-process
                     ▼
┌──────────────────────────────────────┐
│ pbmcp.py  — MCP tool logic            │
│ ────────────────────────────────────  │
│ • phone_status / phone_use / phone_type / phone_key / phone_tap /    │
│   phone_scroll / phone_drag / phone_home_pointer / phone_look        │
│ • Talks to :8809 and :8808 over plain HTTP, stdlib only               │
│ • Used TWO ways:                                                      │
│     1. run standalone over stdio (for MCP clients that need stdio)   │
│     2. imported by portabotty.py's /mcp handler (the normal path,    │
│        when a client talks MCP-over-HTTP to the hub on :8800)        │
└────────────────────────────────────────────────────────────────────┘
```

### Why it looks like one thing but is 3 processes

- **pbkey.py** is the only thing that *must* run as root (BlueZ/D-Bus + raw HID sockets need it).
  It is the entry point (`pb` / `pbk` / `pbkb`).
- **portabotty.py** never needs root and never touches Bluetooth — it only speaks HTTP to pbkey
  and pbsee. It's imported in-process by pbkey for convenience, but can also be run completely
  standalone (`python3 portabotty.py`) against an already-running pbkey/pbsee.
- **pbsee.py** is split out on purpose (see its own docstring) so a camera crash/hang can't take
  down the keyboard, and so it can run under a different user/group (`video` group, no root).

Each of the three keeps its own token file and its own tiny HTTP server — that's why you see
`kb_token()` / `sb_token()` / `web_token()` duplicated across files rather than shared: it's
deliberate process isolation, not an oversight.

## The 2 standalone extras

- **pbmcp.py** — see above; it's not a 4th server, it's a module that happens to also have a
  `serve()` stdio entry point.
- **relay.py** (PBRELAY) — a *separate* program meant to run on a *different* machine (e.g. a small
  cloud VM), for reaching a Portabotty server that's behind NAT/no public IP. The home server dials
  out to it (`pbk --relay URL --relay-token ...`); browsers/MCP clients hit the relay's public URL
  instead of the home IP. Stdlib only, no dependency on the other files.

## Everything else in the repo

| File/dir | What it is |
|---|---|
| `README.md` | User-facing docs: install, commands, web console, MCP tools |
| `VERSION` | Plain string, read by `pbkey.py` at startup to print its version |
| `install-deps.sh` | One-line `apt install` for the system packages (bluez, ffmpeg, etc.) |
| `skills/portabotty/SKILL.md` | Agent-facing instructions for Claude/Hermes on *how* to use the MCP tools (when to call `phone_look`, pitfalls like ASCII-only text entry) — not code, just a prompt |
| `.gitignore` | Excludes the local secrets/state files each process writes next to itself (`.pbkey_token`, `pbsee.json`, `portabotty.json`, `.pbkey_state.json`) |

28 unused pip wheels (`mcp-2.3.0`, `pydantic`, `starlette`, `uvicorn`, `cryptography`, etc.) were
previously committed at the repo root — a `pip download` artifact nothing in the code referenced
(`pbmcp.py` is stdlib-only). Removed.

## Runtime picture (ports)

| Port | Owner | Purpose |
|---|---|---|
| 8809 | pbkey.py | local API: device status, type/key/click/scroll, camera on/off |
| 8808 | pbsee.py | local API: camera list, MJPEG stream, snapshot, calibration |
| 8800 | portabotty.py | public-facing: web console (browser UI) + `/mcp` (MCP-over-HTTP), proxies to 8809/8808 |

A phone/tablet/computer itself never talks HTTP — it only sees the Bluetooth HID device pbkey.py
exposes via BlueZ. The HTTP ports are for *you* (browser, MCP agent, or relay) to drive that device.
