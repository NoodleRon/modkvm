# Safety and security boundaries

This is an early simulator checkpoint. Do not use it to control production or
safety-critical systems. It provides no real input backend and no independent
hardware safety mechanism.

## Trust boundaries

- Operator UI and controller code are trusted. A model may provide action JSON
  only. It must not call arming/approval functions or arbitrary Python/shell.
- Images and visible text are untrusted, including instructions shown on a
  target screen. They never grant permission to act.
- An input device can affect the target like a user keyboard. OS permissions,
  sensitive-data approval, transactions, and other consequential actions still
  require appropriate authorization; a batch fingerprint is not that policy.
- Keyboard-only schemas do not prevent typing shell commands. Approval and
  observed target context remain necessary.
- CRC32 is corruption detection only. Production network control must use a
  reviewed, authenticated upstream API and appropriate transport security.
- Simulated releases do not prove USB release behavior. The real backend needs
  an independent stop/timeout mechanism and hardware acceptance tests.

## Fail-closed behavior

Uncertain input is never automatically replayed. Approval is consumed before
I/O. Starting a replacement preview or confirmation attempt revokes any previous
approval token. Failed preview/confirmation, expiry of the current approval,
execution I/O errors, and stop invalidate permission and attempt release/disarm.
Presenting an unknown or stale execution token is rejected without I/O; that
does not revoke a different, currently valid approval. A malformed ARM phrase
does not start an arming attempt or implicitly stop an existing session.

Stop enters a fault state before release I/O and stays faulted if delivery or
acknowledgment is interrupted. Close always attempts transport cleanup, including
after KeyboardInterrupt/SystemExit, and then re-raises the interruption. If
cleanup also fails, the original interruption is preserved with the cleanup
failure chained. Local closed state prevents further controller input; it does
not prove that the physical connection closed or that remote keys were released.

Disconnecting the fake transport releases its simulated state; physical target
behavior is unverified. A fresh observation and operator decision are required
to resume after a fault.

The current synchronous controller cannot preempt a transport that ignores its
timeout. A background call blocked behind its lock is not an emergency stop.
This limitation must be addressed in the selected production adapter/device.

## Reporting

No public issue tracker or security contact is established by this checkpoint.
Before publication, maintainers should add a verified private reporting route.
Do not include passwords, API tokens, private screenshots, or target data in
public bug reports.

## Composed observation loop

The coordinator's model interface returns bounded JSON only. Its immutable
context contains the exact observation pin and previous input receipt, with no
operator token or transport reference. Proposed observations must match the
current pin byte-for-byte after canonical serialization. The operator digest
also binds the observation and expected-result description.

Freshness and target/calibration/connection checks run again at approval and
dispatch. A failed composition gate revokes the entire workflow and closes its
guard, even where the lower-level controller API has a less destructive error
rule. `poll()` is explicit; no background safety monitor is installed. A trusted
adapter must report disconnections and identity changes honestly and promptly.

Input acknowledgments produce no task-success assertion. A fresh post-action
frame is mandatory before another proposal. Re-observation is still evidence to
inspect; the result remains `unverified`. Real capture and model-provider data
sharing are not included or authorized by running the synthetic demonstration.
