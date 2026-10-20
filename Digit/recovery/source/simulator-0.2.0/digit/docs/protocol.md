# Simulator protocol v1

This is a local adapter contract for tests, not a deployed KVM API or firmware.
It is intentionally small. A future KVM adapter can translate commands to an
existing upstream API without modifying the KVM.

## Framing

One JSON object followed by LF. Maximum 4,096 bytes including LF. The incremental
reader buffers no more than one bounded partial frame. Invalid input poisons the
reader until explicit reset. No automatic resynchronization or replay occurs.

Fields are exactly `v`, `session`, `seq`, `type`, `body`, and `crc32`.

- `v`: integer 1; JSON booleans and floats are not integers here
- `session`: 32 lowercase hexadecimal characters, randomly generated per arm
- `seq`: integer 1 through 2^31 - 1
- `type`: `arm`, `execute`, `release_all`, or response-only `ack`
- `body`: an object matching the selected operation
- `crc32`: eight lowercase hexadecimal digits

CRC32 covers canonical ASCII JSON of all fields except `crc32`: sorted keys,
compact separators, ASCII escaping, no NaN/Infinity. CRC32 detects accidental
corruption; it is not a signature, authorization check, or encrypted channel.
Never expose this protocol to an untrusted network.

Duplicate JSON keys, unknown envelope fields, extra physical newlines, malformed
JSON, unknown versions, oversized frames, and incorrect checksums are rejected.

## Requests

- `arm`, sequence 1, body `{}`. A fresh, unconsumed local operator-enable latch
  is required in the simulator. The latch lasts 10 seconds; an armed session
  expires after 60 seconds. Re-arming the current session ID is rejected.
- `execute`, exactly the next sequence, body `{"actions": [...]}`. All actions
  are validated before any input. Actions are the strict schema in the README.
- `release_all`, body `{}`. Always release and disarm, including while unarmed
  or when the session/sequence differs. Stop must survive a sequence gap. It can
  safely be repeated because it creates no input.

Every normal simulated key press is followed by a release. An explicit
`release_all` *action inside a batch* releases keys without disarming the batch;
the top-level `release_all` *message* also disarms. A completed batch ends in a
release and leaves the session armed until expiry/stop.

## Responses and delivery

An `ack` copies the request session and sequence. Its body has exactly `status`
(`ok` or `rejected`), `detail` (string), `state` (`armed` or `disarmed`), and
`action_count` (integer). Successful execution acknowledges the number of
completed actions. An error disarms; partial input may already have happened.

The simulator caches only the most recent successfully completed execution
request/ACK. An identical duplicate in the current armed session returns the
cached ACK without emitting new input. Reusing that sequence for different
content or sending any gap/out-of-order execution fails closed and disarms.
There is no persistent exactly-once guarantee across restart, reconnect, or a
new session. The controller never uses retries to recover an uncertain result.

Deduplication is defensive, not a license to replay. Lost ACK, corrupted ACK,
mismatched session/sequence, interrupted transport, or backend rejection marks
execution uncertain, consumes approval, and attempts emergency release/disarm.
The operator must inspect the target before creating a new plan. An ACK confirms
the backend's input processing, not the success of the target application task.

## Timing limitations

Short batches are capped at 1,000 ms estimated input time. Text is modeled as
10 ms press + 10 ms release per character. Key chords use the requested 5–100 ms
hold + 10 ms release. The fake device's hold watchdog is 200 ms. Actual target
latencies, USB polling, scheduler jitter, dropped reports, and network timeouts
are not modeled faithfully by these numbers.

The simulator clock must advance and `tick()` must run for watchdog events to
occur. Independent firmware/device release guarantees remain unimplemented.
The controller requests a 2-second transport timeout; each real adapter must
actually enforce that deadline. Do not treat an arbitrary synchronous Python
call as interruptible merely because it receives a timeout argument.
