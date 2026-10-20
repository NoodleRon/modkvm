# Composition verification status

Verified in a Linux cloud workspace on 2026-10-06 with Python 3.12.14.

- Project suite: **115 tests passed** (46 controller, 43 observation, 26 composition)
- `python3 -m digit.loop_demo`: **passed**, exactly one synthetic input batch,
  empty frame 0 → `A` in frame 1, receipt `input_acknowledged`, outcome `unverified`
- Keyboard-only CLI validation and interactive simulation remain covered
- The composition has no runtime external dependencies

Composition tests include 22 source/input mutation subcases across approval and
dispatch gates, stale frames at both gates and after model latency, calibration
revision changes, tampered model bindings, malformed/oversized model output,
unknown actions, single-use object-identity approval, lost acknowledgments,
post-action replay/backdated capture/disconnect, model interruption, and cleanup.

The controller and observation modules were copied from their accepted
checkpoints into this isolated composition; the source trees were not modified.
Independent composition review accepted this synthetic-only checkpoint: all
115 project tests plus 24 additional adversarial review tests passed. The extra
review probes include exceptions/interruptions at each gate, cleanup failures,
cross-session tokens, post-capture lifecycle changes, and 50 successive observed
cycles. These are separate from the 115 tests shipped in `tests/`.

Additional checks passed:

- `python3 -m compileall -q digit digit_observation tests examples`
- Original synthetic observation-mapping example
- Offline wheel build with installed setuptools 84.0.0
- Installed-wheel synthetic-loop smoke test from outside the source tree
- Byte comparison of inherited controller implementation and observation modules
  against their accepted originals; only package-version metadata advanced

This acceptance covers the synthetic composition only, not production or
physical-device operation.

## Still not implemented or tested

- Real Bluetooth pairing, target keyboard input, USB, or KVM adapters
- Camera/HDMI capture, screenshot decoding, OCR, or visual recognition
- A real model provider or autonomous continuation loop
- Semantic task-success evaluation
- Native macOS/Windows applications or cross-platform compatibility
- Physical release deadlines, independent hardware watchdogs, or interruption
  of a blocking adapter from another thread/process

All tests exercise synthetic data and in-memory events. They do not prove that
an external computer can be controlled or observed.
