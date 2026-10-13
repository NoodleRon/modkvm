# Portabotty
Version 0.0.00001

Your Linux server as a Bluetooth keyboard + mouse for phones, tablets and computers, plus a webcam view of their screens. Terminal, web page, or agent (MCP).

| part | commands | job |
|---|---|---|
| **PBKEY** | `pb` · `pbk` · `pbkb` | the terminal: keyboard + mouse over Bluetooth, camera status, web link. Runs as root |
| **PBSEE** | `pbs` · `pbc` | webcam server: live / idle / stopped, calibration. Optional: PBKEY starts it with `c` |

One inline terminal (`pb`). The web console and MCP are served by it; either part works without the other.

## Dependencies
apt packages only; the code uses Python's standard library.

| package | for |
|---|---|
| `bluez` | Bluetooth daemon, `btmgmt` (PBKEY) |
| `python3-dbus`, `python3-gi` | BlueZ control (PBKEY) |
| `python3-evdev` | server trackpad/mouse capture (PBKEY, optional) |
| `ffmpeg` | camera capture (PBSEE) |
| `v4l-utils` | camera controls (PBSEE) |
| `python3-pil`, `python3-numpy` | image processing (PBSEE) |
| `sudo` | PBKEY runs as root |

Also: Bluetooth adapter (USB 4.0+), `bluetoothd` started with `-P input`, `uvcvideo` module and your user in `video` for webcams (`python3 pbsee.py doctor --fix`), Python 3.8+. Clients: any modern browser; `claude` CLI for MCP.

```
./install-deps.sh
# = sudo apt install bluez python3-dbus python3-gi python3-evdev ffmpeg v4l-utils python3-pil python3-numpy
```

## Install
```
git clone https://github.com/noodleron/portabotty.git ~/portabotty && cd ~/portabotty
./install-deps.sh
python3 pbsee.py doctor --fix      # re-login after
sudo python3 pbkey.py --install   # pb pbk pbkb
python3 pbsee.py install           # pbs pbc
pb
```
Update: `cd ~/portabotty && git pull`.

## PBKEY
Typing goes straight to the active device. A line prints when something changes (device, `● live` / `◐ idle` / `○ stopped` for the camera). The web link is printed at start and on `w`. `Ctrl+]` then:

| key | does |
|---|---|
| `1`-`9` | switch device (server dials it) |
| `p` | pair mode, 120 s |
| `s` | solo: switching disconnects the previous device |
| `m` / `t` | terminal mouse / server trackpad |
| `a` | mouse: **map** (window = device screen) / **relative** |
| `h` | re-home pointer to top-left |
| `+` `-` | map size ±10 % |
| `c` | start / stop the camera server (PBSEE) |
| `w` | print the web link |
| `d` / `f` / `q` | drop / forget (y) / quit |

- **Mouse** is on by default. **Over SSH it is the pointer of the computer you SSH from** (terminal mouse; on when the terminal reports it, or is xterm-family; it disables text selection there). **At the server itself** a trackpad or USB mouse is captured automatically (the server's cursor freezes while captured): relative, lift-and-continue; tap = click, two-finger tap = right click, two-finger drag = scroll; `Ctrl+]` `t` toggles it. `--pad` forces the server's pad over SSH.
- The target needs a pointer: iPhone/iPad show a round translucent dot once AssistiveTouch is on. If the pad does nothing: `sudo python3 pbkey.py --pads` lists input devices and counts live motion events (needs `python3-evdev`).
- **Map** estimates position (HID is relative, targets accelerate); edges self-correct. Calibrate once: `Ctrl+]` `h`, move to the window's bottom-right; pointer short of the corner → `+`, past → `-`.
- Flags: `--no-pad`, `--pad`, `--pads`, `--no-term-mouse`, `--no-mouse` (keyboard-only; re-pair), `--no-api`, `--api-port`, `--api-listen`, `--no-web`, `--web-port`, `--web-listen`, `--web-token`.
- **Targets:** iPhone, iPad, Mac, Windows, Linux, Android. iPhone needs AssistiveTouch (Settings > Accessibility > Touch) for a pointer. Cmd = GUI/Win key. Keys are sent as a US layout. iOS caches the HID descriptor: after `--no-mouse`, forget the bond on both sides and re-pair.

## PBSEE
`pbs` runs the camera server and prints `● live` / `◐ idle` / `○ stopped` on change (cameras run only while watched, stop after 10 s). `pbs screen` is a full-screen view with a latest picture where the terminal supports inline images (~1/s, 480 px). `pbs list | snap NAME -o shot.jpg | doctor | probe | install-service`.

**Calibrate first** (web console ⚙, `/view`): Calibrate screen → click the 4 corners (TL, TR, BR, BL) → Save view. Web taps map onto this view.

Cameras are data: `pbsee.json`, one entry per camera (`match`: `usb:VID:PID` / `name:` / `serial:` / `path:`; `size`, `fps`, `controls`, `opts`); `pbs probe` writes it. Localhost-only by default; beyond that a token is required.

## Web console
Served by PBKEY on port 8800, on the local network by default; the link carries a token (generated once, kept in `portabotty.json`). `--web-listen 127.0.0.1` keeps it local. `--no-web` turns it off. Start-up shows `web: <link>`, or why it's off. Not reachable from another computer: `sudo ufw allow 8800/tcp` (Key prints this when ufw blocks it) and use the printed link including `?t=` (without the token: 401).

Live image, device chips (tap to switch), key / see dots (green up, amber idle, red stopped). Works with either part down.
- **◎ direct:** tap = click there, drag = swipe, long-press / two-finger tap = right click, wheel / two-finger drag = scroll.
- **▭ pad:** image area is a relative trackpad.
- **⌨** opens the device keyboard; on desktop just type. Keys, Ctrl/Alt/Cmd combos, F-keys, arrows forwarded.
- **⋯** tray: esc tab ⌫ ⏎ arrows, sticky ⌃ ⌥ ⌘ ⇧, ⌂ (Cmd+H), Cmd+Space, Cmd+Tab.
- **⚙** calibration page.

## Remote access (PBRELAY)
Reach the console from anywhere without opening a port on your network. Run `relay.py` on any small public host (stdlib only; put TLS in front, e.g. Caddy or an AWS load balancer), and your server dials out to it.
```
python3 relay.py --port 8080                                  # prints the access + agent tokens
pb --relay https://relay.example.com --relay-token AGENT_TOKEN   # saved; later runs just `pb`
```
Browser and MCP: `https://relay.example.com/?t=ACCESS_TOKEN` (same pages, stream and `/mcp`). Shows "home server offline" when the server isn't connected.

## MCP
```
claude mcp add --transport http portabotty http://SERVER:8800/mcp?t=<token>
claude mcp add portabotty -- python3 ~/portabotty/pbmcp.py             # stdio alternative
```
Tools: `phone_status`, `phone_use`, `phone_type`, `phone_key`, `phone_tap` (x,y fractions), `phone_scroll`, `phone_drag`, `phone_home_pointer`, `phone_look` (snapshot + change score; `settle` waits first).
PBKEY API (`127.0.0.1:8809`, token in `.pbkey_token`): `GET /status`; `POST /use /drop /solo /mouse /type /key /goto /click /scroll /drag /move /home`.
The API types into your devices and the camera shows their screens: keep localhost + token.

## Files
Next to the scripts: `.pbkey_state.json`, `.pbkey_token`, `pbsee.json`, `portabotty.json` (web token, relay).

## Recovery
- "UUID already registered": `sudo pkill -f "[p]bkey\.py"; sudo systemctl restart bluetooth`. Never Ctrl+Z.
- Garbled terminal: `reset`. Camera: `pbs doctor`.
