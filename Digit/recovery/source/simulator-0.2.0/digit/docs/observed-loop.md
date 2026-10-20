# Synthetic observed-action loop

The composition connects the accepted controller and observation guard through
`digit.loop.ObservationLoop`. It introduces no external runtime dependency and
no real capture, model, Bluetooth, or HID backend.

## Data flow

```text
FakeFrameSource -> immutable FrozenFrame + metadata
                            |
                     ObservationGuard
                 target / clock / calibration
                            |
                   FakeModel + ModelContext
                            |
                  bounded JSON action proposal
                            |
              observation-bound operator approval
                            |
                 Controller -> FakeTransport
                            |
                    ExecutionReceipt
                            |
              mandatory fresh post-action frame
                            |
              ObservedResult(outcome="unverified")
```

The demo's frame payload is synthetic JSON text derived from fake keyboard
events. It is not a camera image, screenshot, OCR result, or visual understanding.
The fake model performs one deterministic fixture task, `Type A`, and emits only
JSON. The demo explicitly simulates the operator's arm and approval calls. It
must not be copied into a real backend as automatic approval logic.

## Interfaces and authority

- `FrameSource.capture()` returns one `FrozenFrame`: frozen metadata, immutable
  bytes (1 MiB maximum), and a media-type label. The source is trusted to report
  true capture identity/time and to keep payloads stable for a frame ID
- `FrameSource.status()` reports the current exact frame, binding, geometry, and
  readiness. Real event loops must call `poll()` when status changes; the current
  implementation has no watcher, callback subscription, or background thread
- The same transport object used by `Controller` must expose `input_status()`
  returning `InputStatus(target_id, target_epoch, connected)`. The observation
  target/epoch must match this input target/epoch. Status from an unrelated
  monitor cannot be passed separately as a substitute
- `ProposalModel.propose(frame, context)` receives an immutable frame and context
  and returns at most 8,192 bytes of JSON. It does not receive a controller,
  operator callback, approval token, shell tool, or transport
- `ObservationLoop.arm()`, `approve()`, and `dispatch()` belong to the trusted
  operator/application layer. An external model must never call them directly

Trusted adapters are ordinary Python objects; this process is not a sandbox
against hostile code installed as an adapter. Model output is parsed as data,
never evaluated. Only the closed controller action vocabulary is accepted.

## Binding a proposal

The model must echo the exact `ObservationPin`: source/session/frame sequence,
source/target binding and epoch, capture timestamp and clock domain, source and
target dimensions, payload SHA-256, calibration ID, and calibration revision.
Canonical-byte comparison prevents boolean/integer aliasing in echoed metadata.
The expected-result description is bounded and is not treated as evidence.

The operator confirmation digest binds that entire pin, the core action-preview
digest, and the expected-result description. The operator must inspect the
actual target and actions as well as confirm the fingerprint; the fingerprint
itself is not semantic authorization. Tokens use object identity and are
single-use within this Python process; arbitrary in-process code is outside
the security boundary.

Freshness, current-frame identity, target binding, geometry, readiness,
calibration revision/age, and input connection are checked:

1. Before the model sees the observation
2. After model output returns
3. Immediately before human approval is accepted
4. Immediately before dispatch

A newer frame does not silently refresh an old proposal. A changed calibration,
resolution, source/session, target/epoch, disconnect, uncertainty, or expired
frame invalidates queued work when a gate or `poll()` encounters it. The
coordinator revokes permissions, closes its guard, and attempts release/disarm.
Restart with a new observation session, calibration, and operator arming after a
fault. This coordinator deliberately uses a stricter lifecycle than the
standalone guard's temporary-uncertainty recovery.

Defaults inherited from the observation prototype are a 500 ms frame age and
30-second calibration age. These are fixture policy limits, not a usable
human-review latency guarantee. A real asynchronous/model/operator interface
needs a deliberate freshness/review design; do not silently increase age limits
or relabel old frames just to let an action through.

## Receipt versus outcome

A successful dispatch returns `ExecutionReceipt(status="input_acknowledged")`
and moves the coordinator to `awaiting_observation`. Another proposal is blocked
until `observe_after()` receives a new admitted frame with a strictly newer
sequence and a capture timestamp no earlier than input completion. Captures are
still adapter assertions: timestamp metadata cannot prove honest acquisition.

`ObservedResult.outcome` remains `unverified`. It carries the new frame for
inspection; it does not infer that the application task succeeded. A subsequent
model proposal receives the fresh frame and previous input receipt. There is
no autonomous continue loop, automatic success adjudicator, or automatic retry.

A lost acknowledgment means input may have occurred, faults the session, and
never generates an automatic replay. A failed post-action capture also faults;
it does not erase the fact that input may already have been delivered.

## Important non-guarantees

- Status checks and input dispatch are not atomic with an external screen or
  user changing state. Hardware/OS adapter tests and independent release paths
  remain required
- The coordinator is single-owner and synchronous. Concurrent callers,
  re-entrant adapters, a blocked model/transport, or an unresponsive Python
  process are not handled by a background emergency-stop mechanism
- A source must change epoch/session on binding changes, including switching
  away and back. Comparing metadata cannot detect a source that lies or reuses
  identities incorrectly
- Core transport timeouts and independent physical watchdog behavior remain
  adapter requirements, not guarantees enforced by this composition
- No image is sent to a provider or saved. A future provider/image path requires
  its own authorization and bounded retention policy. Python cannot guarantee
  zeroization or prevent a caller from retaining immutable bytes
- Bluetooth keyboard input alone still cannot supply target screenshots

## Run

```sh
python3 -m digit.loop_demo
python3 -m unittest discover -s tests -v
```

The demo observes an empty synthetic screen, approves one `text: A` batch using
a simulated operator, receives an input acknowledgment, and obtains synthetic
frame 1 containing `A`. Its reported task outcome is still `unverified`.
