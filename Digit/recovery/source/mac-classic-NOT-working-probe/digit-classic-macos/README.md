# Digit Classic Bluetooth macOS: capability gate + protocol core

Original MIT-licensed source for the **first feasibility gate** of a macOS
Classic Bluetooth keyboard controller, initially targeting a Windows host.
It is not yet a working Bluetooth keyboard or a complete HID-profile product.

## What exists

- Portable C11 keyboard descriptor and HIDP encoder/parser: Report ID 1,
  modifiers, reserved byte and up to six distinct keyboard usages
- Report and Bluetooth Boot protocol modes with the same keyboard wire layout
- Bounded GET/SET_PROTOCOL, GET_REPORT and LED SET_REPORT/interrupt output handling
- An explicit selected-peer/encrypted-link/two-channel/human-arm safety gate
- A 16-report queue, one in-flight report, stale completion rejection,
  fail-closed backpressure, neutral release, and no reconnect/replay
- Original Objective-C public-API capability probe: transient SDP service class
  0x1124, control PSM 0x0011, interrupt PSM 0x0013, strict assigned-PSM readback,
  incoming L2CAP notifications, and record removal
- An Objective-C protocol describing the future native adapter boundary

**Only the portable core was compiled and tested. The native files are uncompiled
scaffold source. There is no production transport implementation, pairing UI,
working controller app, or demonstrated Windows/macOS receiver compatibility.**

## Test the portable code

Requirements: C11 compiler, make, Python 3. No external Python packages.

```
make test
make sanitize
```

In an executor that prevents LeakSanitizer's process inspection, use:

```
ASAN_OPTIONS=detect_leaks=0 make sanitize
```

This still exercises AddressSanitizer and UndefinedBehaviorSanitizer but does
not test memory leaks. The C core performs no dynamic allocation.

## Native feasibility probe (not run)

On a separately authorized macOS test machine with the Xcode command-line SDK:

```
make native-probe
./build/digit-classic-probe --help
./build/digit-classic-probe --publish-transient-sdp
```

The explicit publication option advertises an **incomplete** HID-shaped record
for up to five seconds. It does not publish a full HID descriptor/profile, pair,
change discoverability or class-of-device, or send any input. It observes channel-open notifications without taking ownership, attaching a
delegate, or closing channels that might belong to the OS. Do not pair a receiver
against this probe.

The probe reads PSMs from the returned published record, never the request
object. Apple may substitute another PSM if the requested one is already used.
If either value differs, is missing, has an unexpected shape/type, or listener
registration fails, the probe removes its record and fails. There is no PSM
workaround, Bluetooth daemon modification or private API fallback.

A pass means only that this public API returned the requested attributes and
registered notifications. It does not establish exclusive endpoint ownership,
actual channel routing, correct encryption, discoverability, or interoperability.
Removal failure is separately reported. The record is non-persistent and Apple
documents removal when the publishing process exits.

## Safety and limitations

The native probe cannot emit a keyboard report: it has no input write path.
The portable core has no Bluetooth access. Its link/security facts must eventually
come from a separately implemented and validated native adapter, not network or
user-supplied Boolean claims. That adapter must obtain an explicitly selected
peer, verify live bonding/encryption and both channels, and require a new local
human arm for each session. Stop/disconnect cannot undo input already delivered.
A neutral release is best effort and cannot be promised after a broken link.

See [test status](docs/test-status.md), [native gates](docs/native-gates.md), and
[protocol/adapter contract](docs/protocol-contract.md) for exact boundaries.
The existing Digit composition package was not modified or wired to this source.

## Sources and provenance

Written from public Apple API documentation and Bluetooth/USB wire specifications;
no third-party implementation was imported. Protocol constants and HID descriptor
encodings express interoperability formats. No KeyPad or AGPL source was copied.

- [Apple SDP publication](https://developer.apple.com/documentation/iobluetooth/iobluetoothsdpservicerecord/publishedservicerecord(with:))
- [Apple SDP data-element representation](https://developer.apple.com/documentation/iobluetooth/iobluetoothsdpdataelement)
- [Apple incoming L2CAP notifications](https://developer.apple.com/documentation/iobluetooth/iobluetoothl2capchannel/register(forchannelopennotifications:selector:withpsm:direction:))
- [Apple device encryption state](https://developer.apple.com/documentation/iobluetooth/iobluetoothdevice/getencryptionmode())
- [Bluetooth HID specification](https://www.bluetooth.com/specifications/specs/human-interface-device-profile1-1-2ed2/)
- [Bluetooth SIG HID Lite wire-format explanation](https://www.bluetooth.com/wp-content/uploads/2023/08/HID-Lite-WP-V10.pdf)
- [USB HID 1.11 specification](https://www.usb.org/sites/default/files/hid1_11.pdf)

No Bluetooth qualification, profile conformance or trademark certification is claimed.
