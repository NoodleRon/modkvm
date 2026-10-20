# Architecture and next integration boundary

## Intended observation/action loop

```text
Target monitor / HDMI output
          |
          v
KVM capture OR external camera                 Target USB input
          |                                           ^
          v                                           |
Observation adapter -> controller -> approved batch -> input adapter
                            ^              ^
                            |              |
                      model proposal   operator review
                      (untrusted JSON) and explicit arm
```

The controller and model run away from the target: on a Mac, server, or cloud
machine. A supported KVM presents normal USB input and captures the target's
video. The target does not require an installed software agent. Some targets,
ports, secure desktops, BIOS modes, display formats, and USB policies will still
require compatibility testing. No claim of universal access or security bypass
is made.

The current composition adds a synthetic bidirectional loop using a fake model,
source, and input adapter. It does not provide a real capture/model/input loop.
See [the implemented fixture contract](observed-loop.md).

## Proposed loop, not implemented

1. Capture a new observation from the selected KVM/camera adapter.
2. Attach source identity, monotonically increasing observation ID, capture
   timestamp, image dimensions, and crop/calibration version. Reject stale,
   missing, or inconsistent observations.
3. Show the target identity and current view to the operator. Treat visible text
   as untrusted data, never as instructions that override operator policy.
4. Give the model only the necessary observation/task context and the closed
   action schema. No shell tool, direct KVM tool, credentials, or approval API.
5. Parse and validate its proposed short batch.
6. Obtain approval of that exact preview and verify the source is still current.
7. Execute once. A transport timeout means execution may have happened.
8. Acquire a fresh observation and compare the actual result with the intended
   result. Request a new approval for subsequent input. Stop on uncertainty.

Image transmission to a model provider is a separate data-sharing decision.
Screens can contain credentials, private messages, or other sensitive content.
The MVP makes no provider calls and saves no screen images.

## Use an off-the-shelf KVM first

Before adding a backend, select a device that supplies both captures and input
with supported APIs. Candidate existing adapter work to inspect includes:

- [jetkvm-mcp](https://github.com/shvartzj1/jetkvm-mcp)
- [TinyPilot MCP package](https://pypi.org/project/tinypilot-mcp/)
- [HIDAgent](https://github.com/CMUBigLab/hidagent)

These are evaluation links, not dependencies or endorsements. This checkpoint
contains none of their code. Pin exact versions/commits, confirm the API and
product/plan requirements, review their licenses and dependencies, and run the
hardware acceptance matrix before incorporation. Do not assume that a tool
having a `type` or `key` operation makes it safe to expose directly to a model.

Adapt the existing capture/input API behind the controller. Do not fork a
complete KVM stack solely to change branding. Keep upstream attribution and
licenses with any reused components. A license file alone does not establish
that every bundled dependency has the same license.

## Future production adapter requirements

The present `Transport` interface is a synchronous simulator-oriented contract.
A production KVM adapter may translate it locally to the KVM's supported API;
the custom framed protocol must not be imposed on an unmodified KVM.

An adapter must:

- Connect only to an explicitly selected and verified target
- Use the upstream authenticated API and TLS where supported; never bypass
  certificate/security warnings or publish an unauthenticated control endpoint
- Keep credentials outside model context and source control
- Advertise supported input capabilities and fail closed on unsupported actions
- Map key names and keyboard layout explicitly; initially require a known US
  layout and tested Caps Lock/IME behavior, or reject text that cannot be mapped
- Implement real bounded timeouts and classify unknown delivery without retry
- Guarantee release-after-press even if a later action fails
- Provide an independent stop/release path; the controller lock is not that path
- Demonstrate behavior after controller crash, network loss, stuck operations,
  KVM reboot, and target USB detach
- Preserve the semantic distinction between a delivered input and an observed
  application result
- Require fresh local/operator enable and a fresh session after faults/reconnect

The simulator's local-arm latch represents an operator-controlled device/session
enable. It does not establish that an off-the-shelf KVM has a physical arm
switch. For each selected product, decide and document the actual interlock.
Do not claim a missing hardware capability can be supplied by software alone.

## Camera alternative and mouse calibration (design only)

No camera is opened by this code. A future camera source would return immutable
image bytes plus capture ID, timestamp, dimensions, source ID, and calibration
version. A mock source should be based on explicit fixture images.

For an external camera, establish the monitor's visible quadrilateral, correct
perspective distortion, and exclude surrounding workspace content. Require
operator verification after camera/display movement or resolution changes.
For direct HDMI capture, still verify scaling, letterboxing, and target display
selection. With multiple monitors, pick one explicitly.

Mouse control remains disabled. Before adding it, calibrate capture coordinates
to the target's actual pointer coordinate space, define clamping, check
coordinate transform error at corners and center, and test absolute versus
relative device behavior. A screenshot width is not automatically the USB
absolute-pointer range.

## Only if custom hardware becomes necessary

A Pico-class board could expose standard HID to the target while receiving
approved input from a separate trusted control link. The board, USB topology,
electrical design, and controller-side link must first be selected and verified;
this repository supplies no pinout, firmware, flashing script, or build claim.

Required design properties include a local enable/interlock, visible armed
indicator, bounded buffers, queue invalidation on stop, short press windows,
and a timer/watchdog mechanism independent of the command-processing loop.
A held-key deadline must force a zero report or a safely tested USB detach/reset,
including when the main firmware task stalls. Reset alone is not a verified
release strategy until measured on the chosen target systems. Do not silently
refresh a watchdog from a hung executor. Use honest product identity; no cloned
stealth USB IDs or security bypass behavior.
