# Adapter and future controller design

## Proposed camera adapter

Implement `ObservationAdapter` and optionally `PixelProvider` outside this
package. A platform SDK, permissions and hardware tests are separate work.

- Open only an explicitly selected local camera, through an authorized OS API
- Choose a source ID and fresh random session ID; use a monotonic local clock
  ID unique to that clock's lifetime. Frame sequence starts at zero
- Deliver the actual capture timestamp, not the time a consumer dequeued it.
  Capture pipelines with unknown buffering age must report uncertainty. If a
  device clock differs, establish a bounded conversion before marking ready
- Require an explicit target/display binding and target pixel geometry. Never
  infer that the image belongs to the paired keyboard target merely because a
  Bluetooth connection exists
- Establish ordered screen corners from explicit calibration. Mark uncertainty
  on occlusion, camera movement, focus/crop changes or suspected pose drift;
  change the target epoch and re-establish calibration when geometry changes
- On capture failure or permission loss, synchronously close the guard or
  feed a new `DISCONNECTED` observation. A freeze of unchanged image content is
  not itself proof of capture failure; stale capture timestamps are
- Keep at most the bounded number of in-memory buffers the consumer is using.
  `borrow_pixels(frame_id)` must pin exactly that immutable frame until the lease
  ends, never substitute a newer frame. Return read-only views; document stride,
  pixel format, orientation and decoding in the concrete adapter. This prototype
  does not specify or implement image decoding
- Default to no recording, upload, telemetry, debug screenshots or raw retention.
  Close/release buffers promptly. Do not claim Python can securely erase pixels

A camera SDK's “connected” status cannot establish that a target is visible or
that a current image is actionable. The future recognition layer must report
uncertainty separately; no recognition behavior is implied by this protocol.

## Proposed KVM screenshot adapter

Use a separately authorized local capture device or vendor screenshot API.
Network credentials, discovery, service terms and remote access are outside this
package. The adapter should bind each frame to the selected KVM port/display
and the target epoch, not merely the KVM appliance.

- Timestamp the actual screenshot acquisition. A cached HTTP response must not
  become a new observation by receiving a new timestamp. Reject unknown age
- For direct full-screen capture, use source corners `(0,0)`, `(w−1,0)`,
  `(w−1,h−1)`, `(0,h−1)`. Use a calibrated crop for letterboxing or overlays
- Carry the target pixel resolution separately from the captured resolution;
  account explicitly for rotation/scaling/cropping. Display mode, port, source
  or capture-size changes invalidate the session and calibration
- Mark `DISCONNECTED` when the device reports no signal or acquisition fails;
  never keep emitting a last-good screenshot as fresh
- Borrow pixels by exact frame ID using the same bounded lease contract. No raw
  screenshot/video recording is enabled by this library

A test adapter may synthesize these events without connecting to anything. A
real adapter needs hardware acceptance tests before being represented as usable.

## Controller integration boundary

This package does not depend on or mutate `digit-hid`. A future coordinator can:

1. Read a current `FrameMetadata` from one trusted adapter
2. Admit it through a guard bound to the approved target/session/layout
3. Borrow pixels by that exact ID for an authorized vision provider, if any;
   retain the ID on every returned observation and coordinate proposal
4. Establish manual/verified calibration for the same frame and guard
5. Call `map_image_point(frame_id, source_point, now=...)`
6. Present any proposed action through Digit's separate approval mechanism
7. Immediately before handing a proposal to the controller, call
   `verify_mapping(mapped_point, now=...)` and separately verify target binding,
   approval expiry, transport readiness, capabilities and stop state
8. Capture and check a fresh post-action observation before any next proposal

Do not send input from an adapter, calibration routine, exception handler or
retry. A rejected observation should stop dependent work, not trigger a guess.
This package does not perform any of the action/approval/model steps above.

A decision based on a frame may become obsolete while a user reviews it. Reject
that old proposal; do not silently relabel it with the latest frame. Re-observe
and reconsider the action, with renewed approval when required. Even immediate
revalidation is not atomic with target changes or external input execution.

## Required hardware/recognition acceptance tests (not run)

- Target binding against the actual target, including port switching and reconnect
- True timestamp/clock conversion, buffering latency and dropped/frozen frames
- Actual camera intrinsics/distortion, calibration error over the entire screen,
  pose drift, occlusion, glare, rolling shutter and variable lighting
- KVM scaling, letterboxing, no-signal overlays, mode changes and cached responses
- Retina/DPI/logical coordinates versus target pixels and input transport units
- Permission denial/revocation, cancellation, shutdown and pixel lease cleanup
- Zero default recording/telemetry; verified explicit opt-in for any retention

Synthetic transform tests are not evidence for any of these physical claims.
