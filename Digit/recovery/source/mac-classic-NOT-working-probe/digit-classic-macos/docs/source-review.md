# Source-review checkpoint

Three findings were corrected and regression-tested where portable:

1. Control negotiation must work before the interrupt channel opens. A separate
   selected-peer/encrypted-control-channel gate now handles GET/SET requests;
   arm and keyboard input still require both channels.
2. Repeating arm must not erase a pending key-up. Arm is now idempotent for an
   already-armed safe session, retaining queued input, last state and in-flight
   completion identity.
3. Apple SDP `getArrayValue` accepts both sequences and alternatives. Native
   readback now explicitly requires type 6 at every required sequence level,
   validates integer type/width, and reads back the HID service class as well.
   This native fix has been source-reviewed only, not compiled or executed.

A further local review correction preserves a pending neutral report across
multiple protocol changes before it can be drained. The regression suite covers it.

Notifications alone do not prove ownership of a channel. The capability probe
therefore only observes channel-open notifications; it never attaches a delegate,
closes an observed channel, or writes a report. A real transport remains gated on
native routing, security, full-profile and host-compatibility validation.

This review does not establish full profile conformance or absence of defects.
