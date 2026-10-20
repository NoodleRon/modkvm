# Native release gates

All hardware steps require a separate user-authorized run. Nothing in this
source task authorizes operating a user's Mac, pairing a receiver, or input.

1. **SDK compile:** compile the Objective-C probe on the target macOS SDK; verify
   signatures, ownership/nullability, delegates, deployment target, required
   Bluetooth consent and sandbox constraints. Source API review is insufficient.
2. **PSM feasibility:** publish the transient gate record, read actual assigned
   control/interrupt PSMs, and require 0x0011/0x0013. Remove it on mismatch. If the
   OS owns the fixed HID endpoints, report the public-API path blocked; do not
   disable system services or patch a daemon. Notifications are not proof of
   exclusive endpoint ownership.
3. **Complete profile:** construct and verify the complete mandatory HID SDP
   record including actual binary report-descriptor encoding, attribute widths,
   language metadata, version declarations and supported capability flags.
   Add the required Device ID record with legitimate identifiers; do not invent
   vendor IDs or claim borrowed identity. The gate record intentionally omits
   these. Do not pair against it.
4. **Security and selection:** explicit human-selected peer; prove the active
   ACL is the selected bonded peer and encrypted using public IOBluetooth APIs.
   Determine valid authentication/security behavior and downgrade notification
   handling. A name or stored paired flag is insufficient. Both channel objects
   must bind to that peer. Reject other connections and duplicate channels.
5. **Transport implementation:** implement `DigitClassicTransport`, public
   channel delegate callbacks, serial run-loop confinement, one async write,
   buffer lifetime, monotonic deadlines, MTU bounds and security checks before
   each write. No retries of ambiguous writes. Cancel/drop queue on failures.
   Remove publication and listeners on exit/Stop; do not claim input delivered
   merely because the local stack completed a write.
6. **Virtual cable and bond lifecycle:** specify pairing and reconnect policy,
   host/device initiated connect sequences, suspend semantics and VC_UNPLUG.
   Current C unplug handling only closes local logical state and latches a flag;
   it does not remove a bond or implement compliant virtual-cable lifecycle.
   Any OS bond deletion needs its own supported UI/API and authorization.
7. **Human arm and neutral release:** input off by default, fresh per-session
   arm, visible Stop, neutral keyboard state on safe shutdown, and no automatic
   rearm/replay after disconnect, sleep, encryption loss, peer change or crash.
   Test held modifiers/key-up loss and transport failure. No guarantee of neutral
   delivery after the radio connection has failed.
8. **User-present interoperability:** Windows host first, then macOS host;
   record OS versions, Mac chip/radio, exact source revision and packet captures.
   Test protocol changes, report requests/LED writes, fragmented/oversized SDUs,
   sleep, radio-off, reconnect, stale callbacks and all stop paths. Other host
   platforms are untested and outside this checkpoint.

No gate is marked complete merely because its source stub or test exists.
