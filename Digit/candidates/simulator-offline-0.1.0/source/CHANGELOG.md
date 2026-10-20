# Changelog

## 0.2.0 — 2026-10-06

- Compose the accepted controller and observation modules in a new isolated tree
- Add immutable synthetic frames, model contexts, observation-bound proposals,
  operator approvals, input receipts, and fresh post-action observations
- Revalidate frame/target/calibration/connection before approval and dispatch
- Add a deterministic fake-model/source/transport demonstration and 26 composition
  tests, bringing the project suite to 115 tests
- Keep task outcome explicitly unverified and avoid any real device/provider I/O

## 0.1.1 — 2026-10-06

- Revoke previous approval before validating a replacement preview or checking
  a new confirmation. Rejected previews/confirmations release/disarm and require
  a fresh session decision
- Release/disarm when the currently owned approval expires
- Enter fault state before stop I/O, so an interrupted release cannot leave the
  controller locally armed
- Always attempt transport cleanup during close, even if stop is interrupted
- Preserve KeyboardInterrupt/SystemExit rather than hiding them; retain the
  original interruption if transport cleanup also fails
- Clarify that an invalid/stale execution token cannot revoke a different
  current approval, and that local closed state is not physical release proof
- Expand the project suite to 46 tests, including interruption subcases

No real Bluetooth, KVM, USB, model provider, or capture backend was added.

## 0.1.0 — 2026-10-06

Initial backend-neutral simulator, approval-gated keyboard core, CLI, protocol,
documentation, and 38 tests. The original source archive is preserved separately.
