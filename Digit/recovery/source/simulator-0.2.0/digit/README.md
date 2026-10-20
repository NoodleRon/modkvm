# Digit

An original MIT-licensed, keyboard-first controller prototype with an
observation-bound proposal and approval loop. Runtime dependencies are Python's
standard library only.

**Current checkpoint: a working synthetic loop.** A fake frame source, fake
model, and in-memory input transport demonstrate observation → proposal →
operator approval → input receipt → new observation. There is no real Bluetooth,
KVM, USB, camera, screenshot capture, model-provider call, or autonomous agent.
No target software or native macOS application is installed by this package.

The integration direction remains a software-only Bluetooth input adapter,
starting with tested Mac/Windows pairings. No “any Bluetooth to any Bluetooth”
guarantee is made. Bluetooth keyboard input alone does not provide screenshots;
a real bidirectional loop needs a separately supported observation route.

## Try the complete synthetic loop

Python 3.10+; verified here on Python 3.12.14. No installation or network needed:

```sh
python3 -m digit.loop_demo
python3 -m unittest discover -s tests -v
```

The demo observes an empty synthetic screen, uses a deterministic fake model to
propose typing `A`, explicitly simulates operator arming and approval, executes
one fake input batch, and observes `A` in a new synthetic frame. It reports
`input_acknowledged` and then `outcome: unverified`, because delivery and a fresh
observation do not automatically prove task success. It does not type into your
computer. Do not use its simulated-operator calls as automatic approval for real
hardware.

## Interactive keyboard-only simulator

```sh
python3 -m digit validate examples/hello.json
python3 -m digit simulate examples/hello.json
```

This older CLI exercises the controller alone, without the observation loop.
It shows the exact plan, asks you to type `ARM`, then asks for
`APPROVE <full fingerprint>`. Ctrl-C attempts release and cleanup. It emits only
in-memory events. `validate` performs no arming or execution.

Optional local installation: `python3 -m pip install .`; packaging tools may
need separate installation. The commands above do not require installation.

## What the composed loop checks

- Immutable frame/context passed to the model; bounded JSON proposals only
- Exact source/session/frame, target/epoch, dimensions, timestamp, payload hash,
  and calibration-revision binding
- Freshness and connection checks after model output, before approval, and again
  before dispatch
- Invalidated queued actions after stale data, source/target/resolution changes,
  recalibration, uncertainty, or disconnect is observed at a gate or `poll()`
- Exact, one-time observation-bound approval and short keyboard batches
- Input receipts that require a new post-action observation before more proposals
- No guessed success and no automatic retry after uncertain execution

The source/transport must honestly report identity and status. Revalidation is
not atomic with changes on an external target. There is no background watcher.
The default 500 ms frame-age budget is a prototype limit, not a verified human
review workflow. See [the loop contract](docs/observed-loop.md).

## Keyboard action vocabulary

`text`, `keychord`, and `release_all` only. Text is printable US-ASCII. Control
keys are explicit named chords. At most eight actions and 1,000 ms estimated
input are allowed per batch. Chords hold for 5–100 ms, default 30, with at most
six non-modifier keys. Mouse actions are rejected.

```json
{"actions": [{"type": "text", "value": "Hello, Digit!"}, {"type": "release_all"}]}
```

Key names: `A`–`Z`, `0`–`9`, `F1`–`F12`, `ENTER`, `ESCAPE`, `BACKSPACE`, `TAB`,
`SPACE`, `DELETE`, `HOME`, `END`, `PAGEUP`, `PAGEDOWN`, `RIGHT`, `LEFT`, `DOWN`,
`UP`. Modifiers: `CTRL`, `SHIFT`, `ALT`, `META`.

A keyboard-only schema is not a semantic safety filter: typing text or shortcuts
in a terminal, payment form, or sensitive application can have consequences.
Operator approval of target context and actions remains necessary. Screen text
and model proposals cannot grant permission.

## Backend and safety boundaries

The model must not receive Python execution access, approval functions, device
credentials, or an unrestricted input client. Application/adapters are trusted;
this Python API is not a sandbox against malicious in-process code.

The simulator's watchdog advances only with its clock and `tick()`; it is not
an independent firmware watchdog. Real transport timeouts and independent
release/stop behavior must be demonstrated by the selected adapter/device. A
synchronous controller lock cannot preempt an adapter that blocks indefinitely.

A future Bluetooth adapter would translate approved keyboard actions into HID,
use explicit pairing, and expose verified target identity/status. BLE HID and
Classic HID need separate compatibility tests, initially Mac-to-Windows and
Windows-to-Mac. Neither direction has been tested here. Review exact licenses
before reusing any upstream implementation; no AGPL code is incorporated.

## Source map

- `digit/loop.py`: composition contracts and observation/approval coordinator
- `digit/loop_fakes.py`, `digit/loop_demo.py`: deterministic in-memory demonstration
- `digit/controller.py`: accepted v0.1.1 approval and stop lifecycle
- `digit/actions.py`, `protocol.py`, `simulator.py`: keyboard schema and fake input
- `digit_observation/`: accepted metadata, calibration, geometry, and guard package
- `tests/`: controller, observation, and composition tests
- [Observed-loop contract](docs/observed-loop.md)
- [Verification status](docs/test-status.md)
- [Safety boundaries](SECURITY.md)
- [Protocol](docs/protocol.md)
- [Hardware acceptance matrix, all unrun](docs/hardware-tests.md)
- [Observation package reference](docs/observation/README.md)
- [Changelog](CHANGELOG.md) and [provenance](THIRD_PARTY_NOTICES.md)

This composition preserves the accepted controller implementation and observation
modules (package-version metadata advances); the separately accepted source trees and prior archives remain
unchanged. Original code is MIT-licensed under [LICENSE](LICENSE). No public
repository or package publication has occurred.
