# Portobotty
Version 0.0.00001

Your Linux server as a Bluetooth keyboard + mouse for phones, tablets and computers, plus a webcam view of their screens. Terminal, web page, or agent (MCP).

| part | commands | job |
|---|---|---|
| **Portobotty** (hub) | `pb` | opens the two parts, shows what's running, serves the web console and MCP |
| **PBKEY** | `pbk` · `pbkb` | keyboard + mouse over Bluetooth (runs as root) |
| **PBSEE** | `pbs` · `pbc` | webcam: live / idle / stopped, latest picture, calibration |

`pb k`, `pb s`, `pb c` open just one. Either part works without the other.

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
| `tmux` | `pb` panes over SSH (optional with a desktop terminal) |
| `sudo` | PBKEY runs as root |

Also: Bluetooth adapter (USB 4.0+), `bluetoothd` started with `-P input`, `uvcvideo` module and your user in `video` for webcams (`python3 pbsee.py doctor --fix`), Python 3.8+. Clients: any modern browser; `claude` CLI for MCP.

```
./install-deps.sh
# = sudo apt install bluez python3-dbus python3-gi python3-evdev ffmpeg v4l-utils python3-pil python3-numpy tmux
```

## Install
```
git clone https://github.com/noodleron/portobotty.git ~/portobotty && cd ~/portobotty
./install-deps.sh
python3 pbsee.py doctor --fix      # re-login after
sudo python3 portobotty.py --install   # creates pb pbk pbkb pbs pbc
pb
```
Update: `cd ~/portobotty && git pull`.

## pb
- In tmux: panes for hub, pbk, pbs. Plain SSH: starts tmux session `portobotty` (`pb` reattaches). Desktop: two terminal windows. Neither: background processes, logs in `.portobotty/` (start `pbk` yourself; sudo can't prompt).
- Hub keys: `k` / `s` start a stopped part, `q` quit hub.
- Web console `http://localhost:8800/` (SSH: `ssh -L 8800:localhost:8800 server`). LAN: `pb --listen 0.0.0.0` (token auto-generated, in the printed URL).

## PBKEY
Typing goes straight to the active device. `Ctrl+]` then:

| key | does |
|---|---|
| `1`-`9` | switch device (server dials it) |
| `p` | pair mode, 120 s |
| `s` | solo: switching disconnects the previous device |
| `m` / `t` | terminal mouse / server trackpad |
| `a` | mouse: **map** (window = device screen) / **relative** |
| `h` | re-home pointer to top-left |
| `+` `-` | map size ±10 % |
| `d` / `f` / `q` | drop / forget (y) / quit |

- **Mouse** is on by default when available. Trackpad or USB mouse: captured automatically, relative, lift-and-continue; tap = click, two-finger tap = right click, two-finger drag = scroll; `Ctrl+]` `t` releases it. Terminal mouse: auto-on when the terminal reports the mouse (not the Linux console); disables text selection there.
- **Map** estimates position (HID is relative, targets accelerate); edges self-correct. Calibrate once: `Ctrl+]` `h`, move to the window's bottom-right; pointer short of the corner → `+`, past → `-`.
- Flags: `--no-pad`, `--no-term-mouse`, `--no-mouse` (keyboard-only; re-pair), `--no-api`, `--api-port`, `--api-listen`.
- **Targets:** iPhone, iPad, Mac, Windows, Linux, Android. iPhone needs AssistiveTouch (Settings > Accessibility > Touch) for a pointer. Cmd = GUI/Win key. Keys are sent as a US layout. iOS caches the HID descriptor: after `--no-mouse`, forget the bond on both sides and re-pair.

## PBSEE
`pbs` serves the camera and shows `● live` / `◐ idle` / `○ stopped` (cameras run only while watched, stop after 10 s). Works in any terminal; where it supports inline images (kitty graphics or iTerm2 protocol) it also draws a latest picture (~1/s, 480 px), `p` toggles; `q` quits. `pbs list | snap NAME -o shot.jpg | doctor | probe | install-service`.

**Calibrate first** (`http://localhost:8800/view`): Calibrate screen → click the 4 corners (TL, TR, BR, BL) → Save view. Web taps map onto this view.

Cameras are data: `pbsee.json`, one entry per camera (`match`: `usb:VID:PID` / `name:` / `serial:` / `path:`; `size`, `fps`, `controls`, `opts`); `pbs probe` writes it. Localhost-only by default; beyond that a token is required.

## Web console
Live image, device chips (tap to switch), key / see dots (green up, amber idle, red stopped). Works with either part down.
- **◎ direct:** tap = click there, drag = swipe, long-press / two-finger tap = right click, wheel / two-finger drag = scroll.
- **▭ pad:** image area is a relative trackpad.
- **⌨** opens the device keyboard; on desktop just type. Keys, Ctrl/Alt/Cmd combos, F-keys, arrows forwarded.
- **⋯** tray: esc tab ⌫ ⏎ arrows, sticky ⌃ ⌥ ⌘ ⇧, ⌂ (Cmd+H), Cmd+Space, Cmd+Tab.
- **⚙** calibration page.

## MCP
```
claude mcp add --transport http portobotty http://localhost:8800/mcp   # beyond localhost: ?t=<token>
claude mcp add portobotty -- python3 ~/portobotty/pbmcp.py             # stdio alternative
```
Tools: `phone_status`, `phone_use`, `phone_type`, `phone_key`, `phone_tap` (x,y fractions), `phone_scroll`, `phone_drag`, `phone_home_pointer`, `phone_look` (snapshot + change score; `settle` waits first).
PBKEY API (`127.0.0.1:8809`, token in `.pbkey_token`): `GET /status`; `POST /use /drop /solo /mouse /type /key /goto /click /scroll /drag /move /home`.
The API types into your devices and the camera shows their screens: keep localhost + token.

## Files
Next to the scripts: `.pbkey_state.json`, `.pbkey_token`, `pbsee.json`, `portobotty.json`.

## Recovery
- "UUID already registered": `sudo pkill -f "[p]bkey\.py"; sudo systemctl restart bluetooth`. Never Ctrl+Z.
- Garbled terminal: `reset`. Camera: `pbs doctor`.
