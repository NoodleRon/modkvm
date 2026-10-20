# Bluetooth reconnect in modkvm

This change is implemented in canonical `~/Pontic/mk/mk.py`. It is not yet
installed in the running Ubuntu process. Cam, the web console, MCP schemas and
manual keyboard/mouse algorithms are preserved.

When the selected target's HID link disconnects, a background worker retries
only that same address after 1, 2, 4, 8, 16 and then 30 second delays. It stops
after 12 background attempts or five minutes. Each L2CAP channel has a maximum
two second connection timeout, reduced near the deadline; paired-device metadata
queries have a two second timeout. Metadata failures also consume the budget.
The retry worker verifies that the address remains paired before every dial.
It sends no key, text, click, scroll, pointer-home or calibration report.

An inbound reconnection from the selected device cancels pending retries.
Another device connecting while that target is offline cannot replace the
selected target. Explicit manual target selection remains available, and cancels
the previous target's pending work. Manual Disconnect/Forget and shutdown cancel
retries. A deliberate Disconnect stays disconnected until explicit Use. Manual
connection attempts and automatic retries share a lock, so they cannot race into
different target selections. An attempt already in progress finishes within its
timeouts before a manual selection proceeds.

The existing status response gains a `reconnect` object with target, state,
attempt count, maximum count, next delay, error and enabled flag. Existing fields
and MCP tool names remain unchanged. A startup connection failure also starts the
bounded background retry window. No pairing, power, sleep, network or credential
settings are changed.

This does not guarantee Bluetooth wake. A PC waking after the retry window has
expired may reconnect from its own Bluetooth stack. Otherwise choose **Use** on
the exact existing PC row in the web console once to start another window.
No restart or re-pair is needed for that manual repair. Confirm the active PC
identity before sending input; reconnect invalidates the estimated pointer
position and does not calibrate or move it automatically.

## Validation and deployment state

The new regressions run entirely against fake paired-device metadata, fake HID,
a fake monotonic clock and synthetic manual actions. Hardware/socket creation,
capture and subprocess startup are forbidden by the fixtures.

The initial health failure was local outbound sandbox denial (`EPERM`). An
approved diagnostic then verified TCP 22/8800, SSH host verification and existing
`pb-operator` authentication; the installed health-only helper returned success
with ports 8800/8808/8809 listening and input disabled. This does not verify
current Bluetooth status or a usable HDMI picture.

Ordinary `ismail@192.168.4.36` noninteractive authentication was rejected. Its
existing password must be entered locally by the user for a one-time deployment;
no password or web token belongs in chat, a script argument, a log or a saved
file. The restricted helper cannot install source and will not be widened.
The existing PBKEY process has not been restarted or replaced. A source update
must be rebased/checked against the actual Ubuntu file; the Mac and Ubuntu source
hashes differ. Keep runtime configs, tokens, bonds and Cam routing in place.
