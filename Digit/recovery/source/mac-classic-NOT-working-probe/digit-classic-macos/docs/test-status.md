# Verification status, 2026-10-06

## Executed in Linux cloud executor

- GCC 14.2.0, C11, `-Wall -Wextra -Werror -Wpedantic -O2`: compiled successfully
- `make test`: PASS; C assertion suite plus 3 Python unittest cases
- C suite: 42,736 assertions, including an exhaustive 256-header × 33-length
  bounded-parser corpus (8,448 cases); this is not formal verification or a
  coverage-guided fuzzer
- Independent Python descriptor parser: single keyboard Application collection,
  Report ID 1, 64 input bits + ID, 8 output bits + ID
- All 256 modifier-byte encoder vectors and 1,000 deterministic randomized
  valid six-key-or-fewer report vectors: PASS
- AddressSanitizer + UndefinedBehaviorSanitizer: PASS with
  `ASAN_OPTIONS=detect_leaks=0`
- Initial sanitizer run with default leak detection: BLOCKED by the executor's
  LeakSanitizer process-inspection/ptrace restriction, not reported as a pass
- `make native-probe` on Linux: intentionally rejected by the platform guard;
  this is not a native compilation attempt and does not validate native code

Covered behaviors include no output before gates/arm, PSM mismatch latch,
wrong-peer rejection, key bounds/duplicates, queue overflow, serialized writes,
Stop during an in-flight write, stale completion after reconnect, write failure,
security downgrade, report/protocol control requests, little-endian report size,
LED outputs, suspend/resume, virtual-unplug local state, control requests before
interrupt-channel establishment, idempotent repeated arm preserving key-up,
repeated protocol changes preserving pending release, and malformed input bounds.

## Not executed / not established

- Any Objective-C or Swift compilation, linking, Apple SDK type validation,
  code signing, entitlement or privacy-permission validation
- Native SDP publication, actual PSM allocation, listener registration/removal,
  channel routing, hardware encryption or Bluetooth packet capture
- Pairing, discovery, input transmission, host receipt, text entry, Windows host
  compatibility, macOS host compatibility, or end-to-end Digit integration
- Mandatory complete SDP profile, Device ID, virtual-cable/bond lifecycle,
  reconnect interoperability, HID qualification, or full profile compliance

The native transport is a protocol declaration only. The capability probe has
no keyboard-write code. Do not mistake portable tests for a working Mac product.
