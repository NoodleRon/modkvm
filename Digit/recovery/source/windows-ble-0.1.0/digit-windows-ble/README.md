# Digit Windows BLE controller prototype

An original, MIT-licensed Windows BLE peripheral experiment for Digit. It uses
public Windows `GattServiceProvider` APIs to expose a keyboard-shaped HID service.
It is separate from the accepted Digit core; it does not change or integrate that
core. It is a future transport-adapter candidate, not a working cross-platform
product or certified/complete HID over GATT Profile (HOGP) implementation.

**Verified here:** the real portable C++ encoder/queue implementation compiles and
passes tests on Linux. **Not verified:** C# / WinRT compilation, packaging, Windows
Bluetooth publication, pairing, security negotiation, receiver input, or any
Mac/Windows combination. No Bluetooth device, model provider, public repository,
or external controller was connected. Nothing was published.

## Scope

- Windows is the controller/peripheral; a receiver uses its built-in Bluetooth HID
  host support. There is no receiver agent and no proprietary service.
- HID service 0x1812, Battery service 0x180F, Report Map, HID Information,
  encrypted input Report, Report Reference 0x2908 and HID Control Point
- Windows-generated CCCD; no duplicate CCCD, DIS, GAP, GATT or SCP publication
- Keyboard-only Report protocol; one eight-byte input report, ID 1 in its Report
  Reference. GATT payloads omit the Report ID. No boot keyboard, output LED report,
  mouse, consumer controls, text API, commands, capture, autonomous loop or network
  bridge in this checkpoint
- Explicit Publish, Select subscriber, Arm, Send fixed test, Stop
- Fixed test is the five US-layout letters `digit`, with a neutral report after
  each letter. No Enter, shortcut or application launch is sent
- One selected subscribed peer, exactly one subscriber required to arm/send,
  eight-byte MTU/notification-size gate, targeted notifications only
- A 16-report FIFO, whole-batch admission, one in-flight report, unique completion
  tokens, neutral baseline, backpressure fault, discard-on-stop/disconnect,
  ordered best-effort neutral release, no automatic rearm or uncertain retry

## Run the verified portable tests

With an existing C++17 compiler and Python 3:

```sh
./scripts/test-portable.sh
```

The same `Core/digit_queue.cpp` is compiled to a Windows DLL and called by the
adapter, not a Python rewrite or simulator substitute. The Python checks parse
its actual HID descriptor and check source-level safety guardrails. They do not
compile or execute C#/WinRT. LeakSanitizer is disabled because this cloud runtime
uses ptrace; AddressSanitizer and UndefinedBehaviorSanitizer remain enabled.

## Windows build and package

See [Windows build instructions](docs/WINDOWS_BUILD.md). A Windows x64 development
machine with Visual Studio C++ tools, Windows SDK and .NET 8 is required. No SDK was
downloaded for the cloud validation. The app intentionally requires package
identity and the Bluetooth capability. There is no silent unpackaged fallback.

## Controlled hardware test

1. Use only computers you own or are authorized to control. Keep a physical
   keyboard/mouse and receiver-side disconnect control available
2. Build/package and pass all static/native checks on Windows first
3. Open an empty plain-text editor on the receiver, use US keyboard layout, and
   publish using the button. Pair manually through the receiver's OS Bluetooth UI
4. Refresh peers, verify the receiver identity on both devices, explicitly select
   its exact subscribed ID, and check the confirmation box
5. Arm to send a neutral baseline, then click Send fixed test once. Observe whether
   exactly `digit` appeared. A transport completion is not evidence of input
6. Stop. Confirm no keys remain held. Repeat the disconnect, ambiguity, overflow,
   timeout and suspend checks in [the hardware matrix](docs/HARDWARE_MATRIX.md)

Stop also unpublishes. Re-publication requires an app restart. A timeout or
uncertain transport result locks out further input for that process. If Windows
never settles its notification operation, the app remains disarmed and waits;
physically disconnect at the receiver before force-quitting. No software can
promise delivery of a release after link loss, process death or power loss.

## Important HOGP gap

Microsoft reserves Device Information Service (0x180A) for Windows. This app does
not fabricate DIS or PnP ID (0x2A50), spoof another product's identifiers, or bypass
that restriction. Receiver-side enumeration must establish what DIS/PnP ID,
GAP Appearance and mandatory profile data Windows actually exposes. Missing or
incompatible system-provided data can block native HID recognition even when the
GATT service advertises successfully. Record that result; do not call the
prototype fully HOGP-compliant or enable production use without qualification.

See [sources](docs/SOURCES.md), [validation](docs/VALIDATION.md) and
[third-party notices](THIRD_PARTY_NOTICES.md).
