---
name: modkvm
description: Operate a phone, tablet or computer over Bluetooth (keyboard, pointer) and look at its screen through a webcam, using the modkvm MCP tools (phone_status, phone_use, phone_type, phone_key, phone_tap, phone_scroll, phone_drag, phone_home_pointer, phone_look).
version: 1.0.0
metadata:
  hermes:
    tags: [bluetooth, devices, automation]
    category: devices
---

# modkvm

## When to Use
The user wants something done on a phone, tablet or computer that modkvm controls: open an app, type text, tap, scroll, check what is on the screen.

## Procedure
1. `phone_status`: see devices and which is active. `phone_use` to switch (number, name or address).
2. `phone_look` first: you need to see the screen before you act.
3. Act with the smallest step: `phone_type` (text), `phone_key` (combos such as `cmd+space`, `cmd+h`, `esc`), `phone_tap` (x, y as 0-1 fractions of the screen), `phone_scroll`, `phone_drag`.
4. `phone_look` with `settle` (1 s) after every action, then decide the next step from what the screen shows.
5. If taps land in the wrong place: `phone_home_pointer`, then retry.

## Pitfalls
- Text entry is ASCII only. Keys are sent as a US layout.
- iPhone needs AssistiveTouch on for a pointer; taps do nothing without it.
- The screen can show passwords or private messages. Never type a secret you were not given and do not read private content aloud.
- Taps use a calibrated view of the screen. If `phone_look` shows the screen skewed or cropped, ask the user to calibrate the view in the web console (Link sheet, view row).

## Verification
After the last action call `phone_look` and confirm the screen shows the expected result before reporting success.
