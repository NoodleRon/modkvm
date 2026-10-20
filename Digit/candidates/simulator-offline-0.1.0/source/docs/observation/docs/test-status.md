# Test status

Verified in the cloud workspace on 2026-10-06 with Python 3.12.14.

## Passed

- 43 standard-library `unittest` tests from the source checkout
- The same 43 tests against an offline-built wheel installed in a separate
  temporary directory, with imports verified outside the source checkout
- Source compilation with `compileall`
- Synthetic example: source (500,500) maps to target (959.5,539.5)
- Offline, dependency-free wheel build; no package downloads

Coverage includes immutable/numeric contracts, frame/source/session binding,
source and target resolution changes, clocks and exact age boundaries, stale
and future frames, replay and rejected-ID reuse, disconnect and uncertainty,
permanent lifecycle invalidation even on duplicate IDs, calibration expiry,
calibration revisions, re-verification, modified result rejection, rectangular
and perspective mapping, inverse mapping, perimeter roundtrips, out-of-bounds
points, singular/concave/bow-tie/tiny-area calibration and projective horizons.

An independent read-only review additionally ran deterministic randomized
math checks: 5,000 interior roundtrips across 1,000 perspective quadrilaterals,
28,000 edge projections, 8,000 perimeter roundtrips and 4,000 outward overshoots
of 0.001 source pixels. The interior maximum error was approximately 5.9e-10
pixels. Edge and perimeter checks passed after numerical-boundary corrections;
all intentional overshoots were rejected. These are synthetic checks only.

## Not run / not implemented

Real camera or KVM capture, OS permissions, physical target binding, actual
image recognition/OCR, device clock calibration, model providers, network APIs,
Mac or Windows execution, Bluetooth/USB input, actions, and atomic observation
plus input. No raw images or videos were used or retained by these tests.

Python 3.10 and 3.11 are declared compatible but were not available/tested here.
No claim of hardware readiness, real-world calibration accuracy, or end-to-end
autonomous operation follows from this test result.
