# Portable protocol and future adapter contract

## Wire subset

Keyboard input is `A1 01 MM 00 K1 K2 K3 K4 K5 K6`: HIDP DATA/Input, Report ID 1,
modifier byte, reserved zero, six usage slots. A neutral release has all fields
after the ID zero. Both Report mode and Bluetooth Boot mode retain the ID.
This is Bluetooth HIDP framing, not USB's bare eight-byte boot report.

The report descriptor permits usages 0 through 0x65 in the keyboard array;
the local encoder accepts only distinct nonzero keys 0x04 through 0x65 and up
to six keys. Modifiers occupy their own bits. It rejects larger rollover sets,
error usages, duplicate keys and unsupported usages. No text-to-layout mapping,
mouse, consumer controls, IME, Unicode typing or key-repeat scheduler is included.

Control requests are whole L2CAP SDUs, bounded to 16 bytes. Control setup
requests are accepted on the selected encrypted control channel before the
interrupt channel opens; arming/input still requires both channels. GET_PROTOCOL returns
`A0 00` or `A0 01`; SET_PROTOCOL accepts only the one-byte 0x70/0x71 request and
acknowledges with HANDSHAKE. A mode change drops queued work and disarms.
GET_REPORT supports input/output ID 1, optional little-endian BufferSize and
bounded truncation. SET_REPORT and interrupt DATA/Output accept only ID 1 plus
five LED bits; padding bits must be zero in this strict subset. Unsupported or
malformed requests produce a bounded handshake where applicable. No DATC or
large-report reassembly is implemented. No full conformance is claimed.

`digit_hid_control` return status distinguishes acceptance from rejection; the
adapter must still send any nonempty response packet, including error handshakes.
A NOT_READY result produces no reply. Interrupt outputs have no handshake.
GET_REPORT uses the last successfully completed authorized input state; this is
local-stack state, not proof that a receiver application observed it.

## Safety state and completion

The caller initializes once, verifies assigned PSMs, selects a peer, supplies
verified live link facts, and arms only after a local human action. Every call
is confined to one executor. Repeating arm on an already-armed safe session is
idempotent and preserves queued releases, last state and the in-flight write. The C struct is not a concurrent API, authentication
protocol or an untrusted-process boundary. Wrong-peer updates are rejected.

The bounded queue allows one in-flight packet. Tokens are monotonically
increasing across disconnects; obsolete completions are ignored. On failure,
backpressure or security loss, future key reports stop and queued work is erased.
A Stop after an in-flight press requests a neutral report after that completion;
there is no safe way to recall bytes already submitted to the OS. A transport
write failure disconnects the logical state without an ambiguous retry.

Suspend disarms and discards queued input without transmitting in suspend;
Exit Suspend never rearms. Release of host-held keys during host-initiated suspend
is not guaranteed by this subset and is a native interoperability gate.
Virtual unplug discards state and signals `unplug_requested`; the adapter must
perform an authorized, standards-correct lifecycle action before this feature
can be considered implemented. It must not silently re-pair or replay input.

Stop is latched. A fresh session requires closed channels and explicit peer
selection. Fatal fault/PSM mismatch requires a newly initialized core after
native teardown. Token wrap closes the core. Do not reuse/initialize a struct
while a previous adapter can still deliver callbacks into that instance.

## Existing Digit composition

`digit-composition/digit/transport.py` defines a simulator request/ACK transport;
HIDP does not supply equivalent application-level acknowledgement or screenshot
observation. No adapter is installed into that package here. A future integration
must preserve its action validation, consent/arm and observed-loop boundaries;
it must not turn a successful L2CAP enqueue into an asserted remote action ACK.
