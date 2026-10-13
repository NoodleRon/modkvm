# Portobotty

Version 0.0.00001

Your Linux server as a Bluetooth keyboard + mouse for phones, tablets and computers, plus a webcam view of their screens. Use it from a terminal, a web page, or an agent (MCP).

| part | commands | job |
|---|---|---|
| **Portobotty** (hub) | `pb` | opens the two parts, shows which are running, serves the web console and MCP |
| **PBKEY** (key) | `pbk` · `pbkb` | keyboard + mouse over Bluetooth. Its own terminal. Runs as root (sudo) |
| **PBSEE** (see) | `pbs` · `pbc` | webcam: live / idle / stopped, optional picture, calibration page. Its own terminal |

`pb k`, `pb s`, `pb c` (also `pb key|kb|see|cam|sb`) open just one. Either part works without the other.

## Dependencies
All system packages (apt). Nothing from pip or npm: the Python code uses only the standard library.

| package | needed for |
|---|---|
| `bluez` | Bluetooth daemon and `btmgmt` (PBKEY) |
| `python3-dbus`, `python3-gi` | BlueZ control (PBKEY) |
| `python3-evdev` | server trackpad/mouse capture (PBKEY, optional) |
| `ffmpeg` | camera capture (PBSEE) |
| `v4l-utils` | camera controls (PBSEE, recommended) |
| `python3-pil`, `python3-numpy` | image processing (PBSEE) |
| `tmux` | `pb` panes over SSH (optional with a desktop terminal) |
| `sudo` | PBKEY runs as root |

Also: Linux with a Bluetooth adapter (USB Bluetooth 4.0+ is fine), `bluetoothd` started with `-P input` (PBKEY listens on the HID ports itself), the `uvcvideo` kernel module and your user in the `video` group for webcams (`python3 pbsee.py doctor --fix` handles both), Python 3.8+. On the machines you use it from: any modern browser (web console); the `claude` CLI only for MCP.

One command: `./install-deps.sh`

```
sudo apt install bluez python3-dbus python3-gi python3-evdev ffmpeg v4l-utils python3-pil python3-numpy tmux
```

## Install (once)
```
git clone https://github.com/noodleron/portobotty.git ~/portobotty && cd ~/portobotty   # private repo: needs a GitHub login or SSH key on the server
./install-deps.sh                            # see Dependencies
python3 pbsee.py doctor --fix                # camera driver + video group (re-login after)
sudo python3 portobotty.py --install         # creates pb, pbk, pbkb, pbs, pbc (works in any shell, console or SSH)
pb
```
Update later: `cd ~/portobotty && git pull`.

## What `pb` does
- Inside tmux: splits panes (hub, pbk, pbs). Plain SSH terminal: starts tmux session `portobotty` (reattach with `pb` or `tmux attach -t portobotty`). Desktop session: opens two terminal windows (gnome-terminal, kitty, wezterm, alacritty, konsole, xfce4-terminal, xterm). Neither available: background processes with logs in `.portobotty/` (sudo can't prompt there, so start `pbk` yourself).
- Hub keys: `k` start key, `s` start see (only if stopped), `q` quit the hub (parts keep running).
- Web console: `http://localhost:8800/`. Over SSH: `ssh -L 8800:localhost:8800 server`. To open it to your LAN: `pb --listen 0.0.0.0` (a token is generated and printed in the URL).

## PBKEY (terminal)
Everything you type goes to the active device, nothing is buffered or echoed. `Ctrl+]` opens the menu, then one key:

| key | does |
|---|---|
| `1`-`9` | switch device (the server dials it; you touch nothing). Like a 3-device keyboard |
| `p` | pair mode, visible 120 s |
| `s` | solo: switching disconnects the previous device (its on-screen keyboard returns) |
| `m` / `t` | terminal mouse / server trackpad on or off |
| `a` | terminal mouse: **map** (window = device screen) or **relative** |
| `h` | re-home the pointer to the top-left corner |
| `+` `-` | map size ±10 % (calibration) |
| `d` / `f` / `q` | drop / forget (asks y) / quit |

Mouse is on by default when available. Trackpad/USB mouse: captured automatically (re-scanned every 3 s), relative like a normal trackpad: lift and continue, tap = click, two-finger tap = right click, two-finger drag = scroll. While captured the server console ignores it (`Ctrl+]` `t` releases). Terminal mouse: auto-on in terminals that support it (iTerm2, kitty, WezTerm, Windows Terminal, Alacritty, Ghostty, GNOME Terminal, most xterm-likes; not the bare Linux console). Text selection in that terminal is off meanwhile (`Ctrl+]` `m`).

**Map is approximate.** The HID mouse is relative and the target applies pointer acceleration, so the position is estimated and edges overshoot to self-correct. Calibrate once: `Ctrl+]` `h`, move to the window's bottom-right; pointer short of the target's corner → `+`, past it → `-`. Saved. A terminal only reports the pointer while it is over that window, so the map is live exactly then.

Flags: `--no-pad`, `--no-term-mouse`, `--no-mouse` (keyboard-only; re-pair after), `--no-api`, `--api-port`, `--api-listen`.

### Targets
Any device that pairs a Bluetooth keyboard + mouse: iPhone, iPad, Mac, Windows, Linux, Android. Tested here only against fakes; designed around iPhone. iPhone needs AssistiveTouch (Settings > Accessibility > Touch) to show a pointer; iPad and desktops show it natively. Cmd is sent as the GUI key (Win key on Windows). Layout: key *positions* are sent as a US keyboard; a target set to another layout interprets them like any physical keyboard would. iOS caches the HID descriptor: after changing it (`--no-mouse`), forget the bond on both sides and re-pair.

## PBSEE (terminal)
`pbs` runs the camera server plus a quiet status screen: `● live` (frames flowing), `◐ idle` (ready; cameras only run while watched, then stop after 10 s), `○ stopped`. In kitty, Ghostty, WezTerm and iTerm2 it also draws a small latest picture (about 1 per second, 480 px, low load); `p` toggles it, `q` quits. Other terminals show the indicator only. `pbs list | snap NAME -o shot.jpg | doctor | probe`; `pbs install-service` makes an always-on systemd service instead.

**Calibrate first** (`http://localhost:8800/view`, via the hub, or `:8808/` directly): Calibrate screen → click the screen's 4 corners (TL, TR, BR, BL) → Save view. The web console maps taps on the image to the target screen, so this view must be the target screen, cropped and straightened.

Another camera = edit data, not code: `pbsee.json` has one entry per camera (`match`: `usb:VID:PID`, `name:`, `serial:`, `path:`; `size`, `fps`, `controls`, `opts`); `pbs probe` writes the skeleton. The camera sees the screen (passwords included): localhost-only by default; beyond that a token is required. Internal non-UVC cameras may need extra drivers (`doctor` says so).

## Web console
One page: live image (if see is up), device chips (tap to switch), and two dots: key / see (green = up, amber = see idle, red = stopped). Works with either part down.
- **◎ direct:** tap the image = click at that spot, drag = swipe, long-press = right click, mouse wheel / two-finger drag = scroll, two-finger tap = right click.
- **▭ pad:** the image area is a trackpad (relative; tap = click, two-finger tap = right click).
- **⌨** opens the device keyboard on phones/tablets; on desktop just type. Hardware keys, combos (Ctrl/Alt/Cmd + key), F-keys and arrows are forwarded; the browser keeps F5/F12/Ctrl+R and shortcuts it reserves (Cmd/Ctrl+W, Cmd+Tab).
- **⋯** key tray: esc, tab, backspace, enter, arrows, sticky ⌃ ⌥ ⌘ ⇧, ⌂ (Cmd+H, iOS Home), Cmd+Space, Cmd+Tab.
- **⚙** calibration/view page. The browser never sees the PBKEY token.

## Agents (MCP)
The hub serves MCP over HTTP at `/mcp` (same tools as the stdio adapter):
```
claude mcp add --transport http portobotty http://localhost:8800/mcp
# exposed beyond localhost: append ?t=<token>
# stdio instead:  claude mcp add portobotty -- python3 ~/portobotty/pbmcp.py
```
Tools: `phone_status`, `phone_use`, `phone_type`, `phone_key` (e.g. `cmd+h`, `cmd+space`), `phone_tap` (x,y = fractions of the screen), `phone_scroll`, `phone_drag`, `phone_home_pointer`, `phone_look` (webcam snapshot + change score; `settle` waits first). Loop: act → `phone_look settle=1` → decide. Underneath is PBKEY's HTTP API (`127.0.0.1:8809`, token in `.pbkey_token`, always required): `GET /status`, `POST /use /drop /solo /mouse /type /key /goto /click /scroll /drag /move /home`.

Safety: the API can type into your devices and the camera shows their screens. Defaults are localhost + token; keep it that way.

## Files
`.pbkey_state.json` (active device, solo, map), `.pbkey_token`, `pbsee.json` (cameras, token), `portobotty.json` (hub token/port), all next to the scripts.

## Recovery
- "UUID already registered": `sudo pkill -f "[p]bkey\.py"; sudo systemctl restart bluetooth` (`pbk` does the pkill itself). Never Ctrl+Z.
- Garbled terminal after a crash: `reset`. Stuck trackpad capture: quit pbk.
- Camera trouble: `pbs doctor`.

## Not verified on real hardware
Acceptance of the keyboard+mouse descriptor by each OS, map accuracy/gain, tap thresholds, the laptop's evdev names, real UVC cameras, GUI-terminal launching. Tested here with fakes: parser, trackpad logic, console flow in a pty, every API endpoint, MCP (stdio and HTTP), the web console in a headless browser (desktop + phone), tmux launching, the `pbs` status screen and picture escape sequences, camera discovery/processing/serving.
