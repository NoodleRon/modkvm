# Portabotty
Version 0.0.00001

Your Linux server as a Bluetooth keyboard + mouse for phones, tablets and computers, plus a webcam view of their screens. Terminal, web page, or agent (MCP).

| part | commands | job |
|---|---|---|
| **PBKEY** | `pb` · `pbk` · `pbkb` | the terminal: keyboard + mouse over Bluetooth, camera status, web link. Runs as root |
| **PBSEE** | `pbs` · `pbc` | webcam server: live / idle / off, calibration. Optional: PBKEY starts it with `c` |

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
| `qrencode` | QR code for the web link (optional) |
| `sudo` | PBKEY runs as root |

Also: Bluetooth adapter (USB 4.0+), `bluetoothd` started with `-P input`, `uvcvideo` module and your user in `video` for webcams (`python3 pbsee.py doctor --fix`), Python 3.8+. Clients: any modern browser; `claude` CLI for MCP.

```
./install-deps.sh
# = sudo apt install bluez python3-dbus python3-gi python3-evdev ffmpeg v4l-utils python3-pil python3-numpy qrencode
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
Typing goes straight to the active device. The top line stays pinned: `Portabotty 0.0.00001: Ctrl+] for menu, then press [key]`, with the clickable `Web: <link>` and `API: <url> | Token file: <path>` pinned under it (the Web row also says `hosted locally | [u] get paid hosting (coming soon)`, or `hosted ● <relay> | [u] change` when a relay is set). The status lines show each key next to its indicator (`name [n]`, `cam [c] ● live`, `keyboard [k] ON`, `solo [s] ON`, `mouse [m] map [a]`, `trackpad [t]`). The screen is never cleared. The status block prints once at start; `Ctrl+]` then a key only logs what changed (`Solo ON`, `Mouse off`, ...), and `Ctrl+]` then Enter prints the status block again. `Ctrl+]` then:

| key | does |
|---|---|
| `1`-`9` | switch device (server dials it) |
| `p` | pair mode, 120 s |
| `s` | solo: switching disconnects the previous device |
| `k` | keyboard on / off (off: keys stay in this terminal, the mouse keeps working) |
| `m` / `t` | terminal mouse / server trackpad |
| `a` | mouse: **map** (window = device screen) / **relative** |
| `h` | calibrate: homes the pointer, sends it to a known spot, looks where it really landed (camera linked to the device and its view marked in the web console) and fixes the map scale for that device; saved. It also straightens a crooked view (sets the view's tilt from the pointer's path). No camera: just re-homes |
| `+` `-` | map size ±10 % by hand (not listed on screen) |
| `c` | camera (PBSEE) on / off; **on by default** (`◐ on, idle` = running, nobody watching), the last choice is remembered |
| `n` | rename the emulated device (the Bluetooth name phones see): type, Enter saves, Esc cancels |
| `d` / `f` | drop / forget (y) |
| `u` | hosting through a relay: URL + agent token, empty URL = back to local (`● host` connected, `○ host` unreachable) |
| `r` / `q` | restart (restarts the Bluetooth service, re-runs Key, reconnects the active device) / quit |
| Enter | print the status block again |

- **Mouse** is on by default. **Over SSH it is the pointer of the computer you SSH from** (terminal mouse; on when the terminal reports it, or is xterm-family; it disables text selection there). **At the server itself** a trackpad or USB mouse is captured automatically (the server's cursor freezes while captured): relative, lift-and-continue; tap = click, two-finger tap = right click, two-finger drag = scroll; `Ctrl+]` `t` toggles it. `--pad` forces the server's pad over SSH.
- The target needs a pointer: iPhone/iPad show a round translucent dot once AssistiveTouch is on. If the pad does nothing: `sudo python3 pbkey.py --pads` lists input devices and counts live motion events (needs `python3-evdev`).
- **Map** estimates position (HID is relative, targets accelerate); edges self-correct. Calibrate once per device with `Ctrl+]` `h` (needs the camera: AssistiveTouch on, the phone in view, its screen marked under Calibrate in the web console). Without a camera, `h` re-homes and `+` / `-` tune the size by eye.
- Flags: `-v` (show start-up log lines), `--qr` (print a QR code for the web link), `--no-pad`, `--pad`, `--pads`, `--no-term-mouse`, `--no-mouse` (keyboard-only; re-pair), `--no-api`, `--api-port`, `--api-listen`, `--no-web`, `--web-port`, `--web-listen`, `--web-token`.
- **Targets:** iPhone, iPad, Mac, Windows, Linux, Android. iPhone needs AssistiveTouch (Settings > Accessibility > Touch) for a pointer. Cmd = GUI/Win key. Keys are sent as a US layout. iOS caches the HID descriptor: after `--no-mouse`, forget the bond on both sides and re-pair.

## PBSEE
`pbs` runs the camera server and prints `● live` / `◐ idle` / `○ stopped` on change (cameras run only while watched, stop after 10 s). `pbs screen` is a full-screen view with a latest picture where the terminal supports inline images (~1/s, 480 px). `pbs list | snap NAME -o shot.jpg | doctor | probe | install-service`.

**Calibrate first** (web console, Link sheet, view row): Calibrate → tap the 4 screen corners (TL, TR, BR, BL) on the live image. Web taps map onto this view.

Cameras are data: `pbsee.json`, one entry per camera (`match`: `usb:VID:PID` / `name:` / `serial:` / `path:`; `size`, `fps`, `controls`, `opts`); `pbs probe` writes it. Localhost-only by default; beyond that a token is required.

## Web console
Served by PBKEY on port 8800, on the local network by default. The link carries a short token (6 lowercase characters, easy to type on a phone; generated once, kept in `portabotty.json`, typing it in capitals works too). Wrong guesses are rate limited (8 per minute per address). `--web-listen 127.0.0.1` keeps it local; `--no-web` turns it off. The link is pinned under the title (clickable in terminals that support links), or says why it's off. `pbk --qr` also prints a QR code to scan with a phone. Not reachable from another computer: `sudo ufw allow 8800/tcp` (Key prints this when ufw blocks it) and use the printed link including `?t=` (without the token: 401).

Live image, device chips (tap to switch; a chip shows its linked view), key / see pills (green up, amber idle, red stopped). Works with either part down.
- **◎ direct** (default on desktop): the image maps to the device screen: tap = click there, drag = swipe, long-press / two-finger tap = right click, wheel / two-finger drag = scroll. **▭ pad** (default on touch screens): the image is a relative trackpad. The button switches; the choice is remembered per browser.
- **⌨** opens the device keyboard; on desktop just type. Keys, Ctrl/Alt/Cmd combos, F-keys, arrows forwarded.
- **⋯** dock, collapsible, three tabs: **Tray** (esc tab ⌫ ⏎ arrows, sticky ⌃ ⌥ ⌘ ⇧, ⌂ = Cmd+H, Cmd+Space, Cmd+Tab; default on phones), **Keys** (full on-screen board, sticky modifiers, keys flash as they're sent), **Pad** (trackpad surface with a touch dot, tap = click, two fingers = scroll, Left / Middle / Right).
- **Link** sheet: devices on the left, views (cameras) on the right. Drag a dot to the other side (or tap a device, then a view) to link them; × on the line unlinks. Choosing a device shows its linked view, and MCP `phone_look` uses it. Row chevrons expand the settings: device (solo, server trackpad, reconnect / disconnect, home pointer), view (rotate, tilt ±20° for a crooked camera, gray, contrast, sharpness, width, calibrate / clear). The switch above Views turns the camera server on or off; "This server" at the top renames the server. Links are stored in `portabotty.json`.

## Remote access (PBRELAY)
Reach the console from anywhere without opening a port on your network. Run `relay.py` on any small public host (stdlib only; put TLS in front, e.g. Caddy or an AWS load balancer), and your server dials out to it.
```
python3 relay.py --port 8080                                  # prints the access + agent tokens
pb --relay https://relay.example.com --relay-token AGENT_TOKEN   # saved; later runs just `pb`
                                                                  # or `Ctrl+]` `u`: type the relay URL and agent token, no restart; empty URL = off
```
Browser and MCP: `https://relay.example.com/?t=ACCESS_TOKEN` (same pages, stream and `/mcp`). Shows "home server offline" when the server isn't connected.

## MCP
Works with any MCP client: Streamable HTTP (`/mcp` on the web console, token as `?t=` or `Authorization: Bearer`) or stdio (`pbmcp.py`).
```
claude mcp add --transport http portabotty http://SERVER:8800/mcp?t=<token>
claude mcp add portabotty -- python3 ~/portabotty/pbmcp.py             # stdio alternative
```
**Hermes Agent** (`~/.hermes/config.yaml`; `/reload-mcp` or restart, then `hermes mcp test portabotty`):
```yaml
mcp_servers:
  portabotty:
    url: "http://SERVER:8800/mcp"
    headers:
      Authorization: "Bearer <token>"
    timeout: 120
# or run it on the server itself, no token needed:
#  portabotty:
#    command: "python3"
#    args: ["/home/you/portabotty/pbmcp.py"]
```
Tools appear as `mcp_portabotty_phone_*`; `phone_look` returns an image Hermes passes to vision models (text-only models get the file path). Optional skill that teaches the look-act-look loop: `cp -r skills/portabotty ~/.hermes/skills/` (also a standard `SKILL.md` for other agents).

Tools: `phone_status`, `phone_use`, `phone_type`, `phone_key`, `phone_tap` (x,y fractions), `phone_scroll`, `phone_drag`, `phone_home_pointer`, `phone_look` (snapshot + change score; `settle` waits first; uses the view linked to the active device).
PBKEY API (`127.0.0.1:8809`, token in `.pbkey_token`): `GET /status`; `POST /use /drop /solo /mouse /rename /type /key /goto /click /scroll /drag /move /home`.
The API types into your devices and the camera shows their screens: keep localhost + token.

## Files
Next to the scripts: `.pbkey_state.json`, `.pbkey_token`, `pbsee.json`, `portabotty.json` (web token, relay).

## Recovery
- "UUID already registered": another Key still holds the Bluetooth HID profile. Start-up now stops it (SIGKILL if it hangs), restarts Bluetooth and retries once by itself; by hand: `sudo pkill -9 -f "[p]bkey\.py"; sudo systemctl restart bluetooth`. Never Ctrl+Z.
- Garbled terminal: `reset`. Camera: `pbs doctor`.
