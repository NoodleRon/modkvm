# Digit observation contract (proposal)

An independent, original MIT-licensed Python library for validating observation
metadata and mapping camera-image points onto one calibrated target screen.
Runtime dependencies: **Python standard library only**, Python 3.10+.

This is a tested contract and geometry prototype, **not a camera/KVM adapter,
vision/OCR system, model integration, Bluetooth transport, or autonomous agent**.
No device access, network access, image decoding, input execution or files are
performed by the library. It does not import or modify the Digit controller core.

## What is implemented

- Frozen frame IDs, monotonic timestamps and explicit clock domains
- Source/session/target/layout-epoch binding; source and target pixel dimensions
- Ready, uncertain and disconnected states; explicit uncertainty reasons
- Four-corner, perspective-aware calibration and forward/inverse homographies
- Freshness and calibration-age checks, replay protection, lifecycle invalidation
- Coordinate results tied to a frame, binding and internal calibration revision
- `verify_mapping()` for rechecking a result immediately before downstream use
- Metadata-only adapter and optional borrowed-pixel provider protocols

The guard retains **one metadata record and one metadata-only calibration**. It
contains no raw-image storage, logging or persistence. A future pixel provider is
responsible for bounded in-memory leases; Python cannot guarantee secure erasure
or prevent callers copying a borrowed buffer. No real pixel provider is included.

## Run the synthetic example and tests

From this directory, without installation or network access:

```sh
python -m unittest discover -s tests -v
python -m examples.synthetic_mapping
```

The example transforms a synthetic point and prints metadata only. It neither
captures a screen nor moves a pointer.

## Coordinate and lifecycle rules

1. Pixel centers use x in `[0, width−1]` and y in `[0, height−1]`; y points down.
   Dimensions must be between 2 and 1,000,000 pixels per axis.
2. Supply visible screen corners in target order: top-left, top-right,
   bottom-right, bottom-left. They must form a non-mirrored, clockwise, strictly
   convex quadrilateral in the source image. Letterbox bars are not screen area.
3. `FrameId(source_id, session_id, sequence)` is immutable and unique. Sequences
   strictly increase, including events subsequently rejected by the guard.
   Session IDs must be newly generated on capture restart/reconnect.
4. `target_epoch` must change whenever display identity, layout, scale, crop,
   rotation, camera position or camera-to-screen registration changes. A source
   that cannot establish the current binding must emit `UNCERTAIN` or stop.
5. Feed a frame to `accept_frame()` before attaching a calibration anchored to
   that exact frame. The calibration's declared error is caller-supplied in
   target pixels; the library does **not** estimate or verify it.
6. Disconnect, binding changes, either resolution change, clock-domain changes
   and monotonic-clock regression permanently close that guard. Construct a new
   guard with a new capture session and newly established binding/calibration.
   Uncertainty or stale captures clear the frame and calibration; recovery needs
   a strictly newer ID plus a fresh calibration.
7. Every mapping and verification rechecks age using a fresh timestamp from the
   same local monotonic clock. Defaults: frame age ≤500 ms, calibration age ≤30 s,
   declared calibration error ≤2 target pixels. These are prototype limits, not
   evidence that a scene or target stayed unchanged for that period.
8. Physical out-of-frame/screen points are rejected rather than clamped. Screen
   edge admission allows only 64 machine-epsilon (~1.4e−14) normalized distance
   for floating-point roundoff. Output clamping is limited to numerical noise
   after geometric admission. This is not a localization-error allowance.
9. Calibration revisions increase within a guard, even if an external caller
   reuses a calibration ID. A new frame or recalibration invalidates old mapping
   results for `verify_mapping()`.

## Limits that downstream code must preserve

- Metadata is an adapter assertion, not cryptographic proof of target identity,
  actual capture time, camera pose, visual content, or confidence. A malicious or
  buggy adapter can lie. There is no OCR, detection or target recognition here.
- The projective model assumes a planar screen and negligible lens distortion;
  distortion correction, occlusion, motion blur, glare and rolling shutter need
  separate evidence. Four corner fits cannot measure real-world accuracy.
- Logical desktop coordinates, target pixels and HID absolute/relative units are
  different spaces. This library only produces target pixel-center coordinates.
- `verify_mapping()` is observation validation, not authorization. The future
  controller must separately check approval, capabilities, action limits and
  stop state. The target can change between validation and action.
- The guard is single-owner and not thread-safe. Serialize ingestion, calibration,
  mapping and verification. Do not retain a mapping as a reusable permission.
- Real camera, KVM, Mac/Windows behavior and Python versions other than 3.12 have
  not been exercised. No hardware or operating-system compatibility is claimed.

See [adapter and controller design](docs/integration.md) and
[test status](docs/test-status.md).
